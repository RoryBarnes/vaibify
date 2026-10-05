"""Environment guarantees a pipeline run makes, and whether they held.

Every step command is prefixed with exports that make its output a
function of the source rather than of the wall clock: the project-repo
HEAD commit epoch becomes ``SOURCE_DATE_EPOCH`` (fixing PDF/EPS/PS
``CreationDate`` and the SVG ``<dc:date>``) and matplotlib's
``svg.hashsalt`` (fixing SVG element ids, which otherwise come from a
fresh ``uuid4`` per process).

The guarantee is best-effort by contract — a repository with no
reachable HEAD still runs — so the second responsibility here is
recording when it did **not** hold. A reproduction that later fails on
differing bytes must be explainable, and an unrecorded skip makes it a
mystery. Unknown is never graded as clean.

Split out of ``pipelineRunner`` because it changes for reproducibility
reasons on a different cadence than step execution does; the runner
re-exports every name so existing imports and patch targets still
resolve.
"""

import asyncio
import logging
import os
import secrets

from .pipelineUtils import fsShellQuote

__all__ = [
    "S_ENV_PREFIX_KEY",
    "S_ENV_OVERLAY_KEY",
    "S_DETERMINISM_APPLIED_KEY",
    "S_DETERMINISM_EPOCH_SOURCE_KEY",
    "S_MATPLOTLIB_CONFIG_ROOT",
    "S_MATPLOTLIB_DIRECTORY_PREFIX",
    "fsBuildMatplotlibSaltShell",
    "fsBuildMatplotlibStaleSweepShell",
]

S_ENV_PREFIX_KEY = "__sEnvPrefix"
S_ENV_OVERLAY_KEY = "__dictEnvOverlay"
S_DETERMINISM_APPLIED_KEY = "__bDeterminismApplied"
S_DETERMINISM_EPOCH_SOURCE_KEY = "__sDeterminismEpochSource"

# matplotlib reads ``matplotlibrc`` from ``MPLCONFIGDIR`` only after a
# working-directory ``matplotlibrc`` and after ``MATPLOTLIBRC``
# (measured against matplotlib 3.5.0), so seeding the salt here supplies
# a default the researcher can still override, rather than clobbering
# one they set deliberately.
#
# The directory is PRIVATE TO ONE RUN: ``<root>/<prefix><token>``. It used
# to be one fixed path holding the project's HEAD epoch, and concurrent
# runs are allowed now, so a run could import matplotlib and read the
# salt another run had just written (measured: run A wrote 111, run B
# wrote 222, and A then read 222), which grades a reproducible SVG as not
# reproducible. A lock around the write would not help: the loser reads
# the winner's value. Only the SALT reaches the output bytes; the
# directory's path does not (``tests/testMatplotlibSaltIsolationLive.py``
# renders the same figure from two directories and compares the bytes).
S_MATPLOTLIB_CONFIG_ROOT = "/tmp"
S_MATPLOTLIB_DIRECTORY_PREFIX = "vaibifyMatplotlib."
I_STALE_MATPLOTLIB_DIRECTORY_MINUTES = 720


async def _fiQueryHeadCommitEpoch(
    connectionDocker, sContainerId, sProjectRepoPath,
):
    """Return HEAD commit epoch as int, or 0 if unavailable."""
    if not sProjectRepoPath:
        return 0
    sCommand = (
        f"git -C {fsShellQuote(sProjectRepoPath)} "
        f"log -1 --format=%ct HEAD 2>/dev/null"
    )
    iExitCode, sOutput = await asyncio.to_thread(
        connectionDocker.ftResultExecuteCommand,
        sContainerId, sCommand,
    )
    if iExitCode != 0:
        return 0
    try:
        return int(sOutput.strip())
    except ValueError:
        return 0


def fsBuildMatplotlibSaltShell(sEpochShellWord, sDirectoryShellWord):
    """Return shell that pins matplotlib's ``svg.hashsalt`` to an epoch.

    There is no environment variable for that rcParam, so the salt is
    written into a ``matplotlibrc`` inside ``MPLCONFIGDIR``. Failing to
    write the file reports on stderr and does not break the ``&&``
    chain: a step must still run when its determinism cannot be
    guaranteed.

    ``sEpochShellWord`` and ``sDirectoryShellWord`` are shell WORDS, not
    values: the epoch is interpolated inside a DOUBLE-quoted word, so a
    caller may pass a literal integer (the live runner, which knows the
    epoch) or an expansion such as ``$SOURCE_DATE_EPOCH``
    (``reproduce.sh``, which reads the epoch out of the envelope on the
    reproducing host); the directory is a quoted literal (the runner,
    one per run) or a quoted expansion such as ``"/tmp/x.$$"``
    (``reproduce.sh``, its own temporary directory). Both lanes pin the
    same rcParam to the same value through this one builder rather than
    each spelling the file out. Whatever the directory is, it must not be
    one another run can write to: see ``S_MATPLOTLIB_CONFIG_ROOT``.

    The file is written under a private name and renamed into place, so a
    process starting while a sibling command of the same run rewrites it
    can never read it half-written.

    Returned as a bare statement with no trailing separator: the runner
    chains it into a command prefix, the reproduction emits it as a line
    of its own.
    """
    return (
        f"export MPLCONFIGDIR={sDirectoryShellWord} && "
        f"{{ mkdir -p -m 700 {sDirectoryShellWord} && "
        f"printf '%s\\n' \"svg.hashsalt: {sEpochShellWord}\" "
        f"> {sDirectoryShellWord}/matplotlibrc.$$ && "
        f"mv -f {sDirectoryShellWord}/matplotlibrc.$$ "
        f"{sDirectoryShellWord}/matplotlibrc || "
        f"echo 'vaibify: matplotlib svg.hashsalt not pinned' >&2; }}"
    )


def fsBuildMatplotlibStaleSweepShell(sRoot=S_MATPLOTLIB_CONFIG_ROOT):
    """Return shell that removes per-run salt directories nobody uses.

    A run's directory outlives its commands (each command is a separate
    exec, and the font cache inside it is reused by the next one), and a
    killed run cannot clean up after itself, so the next command's prefix
    removes directories of this family that have not been touched for
    half a day. A live run rewrites its ``matplotlibrc`` with every
    command, which keeps its directory's modification time current; one
    that is swept mid-run is rebuilt by the next command's prefix. The
    pattern is the family's own name at the top level of ``sRoot`` and
    nothing else.
    """
    return (
        f"find {fsShellQuote(sRoot)} -maxdepth 1 -type d "
        f"-name {fsShellQuote(S_MATPLOTLIB_DIRECTORY_PREFIX + '*')} "
        f"-mmin +{I_STALE_MATPLOTLIB_DIRECTORY_MINUTES} "
        "-exec rm -rf {} + 2>/dev/null || true"
    )


def _fsMintMatplotlibRunToken():
    """Return a fresh token naming one run's salt directory."""
    return secrets.token_hex(8)


async def _fsBuildDeterminismEnvPrefix(
    connectionDocker, sContainerId, sProjectRepoPath,
    iSourceDateEpochOverride=0,
):
    """Return shell prefix that pins the run's time and figure salts.

    One derivation — the HEAD commit epoch — feeds both consumers, so
    identical source produces byte-stable figures across reruns and
    across machines.

    ``iSourceDateEpochOverride`` replaces the HEAD derivation when
    positive (see :func:`_fiResolveRunEpoch`).

    Returns empty string if the epoch cannot be determined; callers
    must not block step execution on the result, but they MUST record
    the skip — see :func:`_fnInjectDeterminismEnvPrefix`.
    """
    iEpoch = await _fiResolveRunEpoch(
        connectionDocker, sContainerId, sProjectRepoPath,
        iSourceDateEpochOverride,
    )
    if iEpoch <= 0:
        return ""
    sDirectory = fsShellQuote(
        f"{S_MATPLOTLIB_CONFIG_ROOT}/{S_MATPLOTLIB_DIRECTORY_PREFIX}"
        f"{_fsMintMatplotlibRunToken()}"
    )
    return (
        f"export SOURCE_DATE_EPOCH={iEpoch} && "
        + fsBuildMatplotlibStaleSweepShell(S_MATPLOTLIB_CONFIG_ROOT) + " && "
        + fsBuildMatplotlibSaltShell(str(iEpoch), sDirectory)
        + " && "
    )


async def _fiResolveRunEpoch(
    connectionDocker, sContainerId, sProjectRepoPath,
    iSourceDateEpochOverride,
):
    """Return the run's epoch; see :func:`_ftResolveRunEpochAndSource`."""
    iEpoch, _sSource = await _ftResolveRunEpochAndSource(
        connectionDocker, sContainerId, sProjectRepoPath,
        iSourceDateEpochOverride,
    )
    return iEpoch


async def _ftResolveRunEpochAndSource(
    connectionDocker, sContainerId, sProjectRepoPath,
    iSourceDateEpochOverride,
):
    """Return ``(iEpoch, sSource)``: the one place a run's epoch is decided.

    Every lane reaches it through ``_fnInjectDeterminismEnvPrefix``, so
    Run All, Run Step, Run From, a plot-only run and the test runner
    cannot disagree. In order: a positive explicit override (the tier 5
    rerun, which passes the envelope's epoch because the commit that
    published the manifest moved HEAD); else, in a container whose
    manifest another identity committed, the epoch the author recorded
    in the envelope, because a reader's run dated from HEAD can never
    reproduce a vector figure the author's run dated otherwise; else
    HEAD's commit epoch. ``sSource`` says which, and why HEAD when it
    could have been the record.
    """
    if iSourceDateEpochOverride > 0:
        return iSourceDateEpochOverride, "the explicit override"
    iRecordedEpoch, sWhyNotRecorded = await asyncio.to_thread(
        _ftDecideReplayOfRecordedEpoch,
        connectionDocker, sContainerId, sProjectRepoPath,
    )
    if iRecordedEpoch > 0:
        return iRecordedEpoch, "replaying the author's recorded epoch"
    iHeadEpoch = await _fiQueryHeadCommitEpoch(
        connectionDocker, sContainerId, sProjectRepoPath,
    )
    return iHeadEpoch, f"dating from HEAD ({sWhyNotRecorded})"


def _ffilesOpenProjectRepoFiles(connectionDocker, sContainerId, sProjectRepoPath):
    """Return the repo-files adapter that reads the project where it lives."""
    from vaibify.reproducibility.repoFiles import ContainerRepoFiles
    return ContainerRepoFiles(connectionDocker, sContainerId, sProjectRepoPath)


def _ftDecideReplayOfRecordedEpoch(
    connectionDocker, sContainerId, sProjectRepoPath,
):
    """Return ``(iRecordedEpoch, sWhyNot)``; a positive epoch means replay it.

    Replay needs all three: a container project, an envelope that
    records an epoch, and a manifest another identity committed. An
    ownership git cannot settle is not foreign: the run says so and
    dates from HEAD, which is what it always did.
    """
    from vaibify.config.mutationAdmission import fnReRaiseControlPlaneRefusal
    from vaibify.config.registryManager import fbIsHostProject
    from vaibify.reproducibility import gitEvidence
    from vaibify.reproducibility.environmentSnapshot import (
        fiRecordedSourceDateEpoch,
    )
    if fbIsHostProject(sContainerId):
        return 0, "this project runs on this machine"
    if not sProjectRepoPath:
        return 0, "no project repository"
    try:
        filesRepo = _ffilesOpenProjectRepoFiles(
            connectionDocker, sContainerId, sProjectRepoPath,
        )
        iRecordedEpoch = fiRecordedSourceDateEpoch(filesRepo)
        if iRecordedEpoch <= 0:
            return 0, "the environment records no epoch"
        sOwnership = gitEvidence.fsManifestOwnershipForRepoFiles(filesRepo)
    except Exception as error:  # noqa: BLE001 -- an unreadable record is not a replay
        fnReRaiseControlPlaneRefusal(error)
        return 0, f"the record could not be read: {type(error).__name__}"
    if sOwnership == gitEvidence.S_MANIFEST_OWNERSHIP_FOREIGN:
        return iRecordedEpoch, ""
    if sOwnership == gitEvidence.S_MANIFEST_OWNERSHIP_OWN:
        return 0, "the manifest is this project's own"
    return 0, "whose manifest this is could not be determined"


async def _fdictBuildHostDeterminismOverlay(
    connectionDocker, sContainerId, sProjectRepoPath,
    iSourceDateEpochOverride=0,
):
    """Return the determinism variables as an environment overlay dict.

    The host-exec primitive can pass real environment entries, so the
    host lane carries its guarantees as DATA — shell text is a
    container-lane necessity, not a host one, and vaibify-authored
    text prepended to a researcher's command is exactly the thing to
    minimize on their own machine. Empty dict when the epoch cannot
    be determined (same best-effort contract as the shell prefix).
    """
    iEpoch = await _fiResolveRunEpoch(
        connectionDocker, sContainerId, sProjectRepoPath,
        iSourceDateEpochOverride,
    )
    if iEpoch <= 0:
        return {}
    dictOverlay = {"SOURCE_DATE_EPOCH": str(iEpoch)}
    sConfigDirectory = await _fsWriteHostMatplotlibSalt(
        connectionDocker, sContainerId, iEpoch,
    )
    if sConfigDirectory:
        dictOverlay["MPLCONFIGDIR"] = sConfigDirectory
    return dictOverlay


async def _fsWriteHostMatplotlibSalt(connectionDocker, sContainerId, iEpoch):
    """Write a matplotlibrc pinning ``svg.hashsalt``; return its directory.

    The host twin of :func:`fsBuildMatplotlibSaltShell`: the rcParam
    has no environment variable, so the salt needs a file, and on the
    host that file belongs in the project's guarded scratch subtree —
    never a world-shared ``/tmp`` directory on the researcher's own
    machine. Written through the connection's gated write primitive so
    the path guard vets it like every other vaibify write.

    Best-effort on real I/O trouble (the step must still run when its
    determinism cannot be guaranteed; the skip is recorded), but a
    control-plane refusal propagates — a refusal is not an I/O error.
    """
    from . import projectRoots
    sConfigDirectory = projectRoots.fsResolveScratchDirectory(
        sContainerId, "matplotlib-determinism", "/tmp",
    )
    sConfigPath = os.path.join(sConfigDirectory, "matplotlibrc")
    try:
        await asyncio.to_thread(
            connectionDocker.fnWriteFile, sContainerId, sConfigPath,
            f"svg.hashsalt: {iEpoch}\n".encode("utf-8"),
        )
    except OSError as errorWrite:
        logging.getLogger("vaibify").warning(
            "matplotlib svg.hashsalt not pinned for '%s': %s",
            sContainerId, errorWrite,
        )
        return ""
    return sConfigDirectory


async def _fnInjectDeterminismEnvPrefix(
    connectionDocker, sContainerId, dictWorkflow, dictVariables,
    iSourceDateEpochOverride=0,
):
    """Compute the determinism guarantees once and stash them.

    Container lane: a shell-text prefix under ``S_ENV_PREFIX_KEY``,
    exactly as always. Host lane: a real environment overlay under
    ``S_ENV_OVERLAY_KEY`` (inherited env + overlay at the primitive),
    and an empty prefix — the two lanes' path/text handling stays
    deliberately un-unified (the withdrawn ``director`` lesson).

    Bundles a ``VAIBIFY_ACTIVE_WORKFLOW_SLUG`` entry either way so the
    marker conftest namespaces writes under the active workflow when
    commands flow through ``_ftRunCommandList`` (e.g. runAllTests).

    Also stashes whether the determinism guarantee was actually built.
    The slug travels unconditionally, so a non-empty prefix or overlay
    is NOT evidence the epoch was exported — callers must read the
    boolean, never sniff the carrier.
    """
    from .fileStatusManager import fsWorkflowSlugFromPath
    from .workflowManager import fsWorkflowLoadedFromPath
    from vaibify.config.registryManager import fbIsHostProject
    sProjectRepoPath = dictWorkflow.get("sProjectRepoPath", "")
    sWorkflowSlug = fsWorkflowSlugFromPath(
        fsWorkflowLoadedFromPath(dictWorkflow),
    )
    iSourceDateEpochOverride, sEpochSource = await _ftResolveRunEpochAndSource(
        connectionDocker, sContainerId, sProjectRepoPath,
        iSourceDateEpochOverride,
    )
    dictVariables[S_DETERMINISM_EPOCH_SOURCE_KEY] = (
        f"Run date pinned to epoch {iSourceDateEpochOverride}: {sEpochSource}"
        if iSourceDateEpochOverride > 0 else ""
    )
    if fbIsHostProject(sContainerId):
        dictOverlay = await _fdictBuildHostDeterminismOverlay(
            connectionDocker, sContainerId, sProjectRepoPath,
            iSourceDateEpochOverride=iSourceDateEpochOverride,
        )
        dictVariables[S_DETERMINISM_APPLIED_KEY] = (
            "SOURCE_DATE_EPOCH" in dictOverlay
        )
        if sWorkflowSlug:
            dictOverlay["VAIBIFY_ACTIVE_WORKFLOW_SLUG"] = sWorkflowSlug
        dictVariables[S_ENV_OVERLAY_KEY] = dictOverlay
        dictVariables[S_ENV_PREFIX_KEY] = ""
        return
    sEnvPrefix = await _fsBuildDeterminismEnvPrefix(
        connectionDocker, sContainerId, sProjectRepoPath,
        iSourceDateEpochOverride=iSourceDateEpochOverride,
    )
    dictVariables[S_DETERMINISM_APPLIED_KEY] = bool(sEnvPrefix)
    if sWorkflowSlug:
        sEnvPrefix += (
            "export VAIBIFY_ACTIVE_WORKFLOW_SLUG="
            + fsShellQuote(sWorkflowSlug) + " && "
        )
    dictVariables[S_ENV_PREFIX_KEY] = sEnvPrefix


async def _fnAnnounceDeterminismEpochSource(fnLogging, dictVariables):
    """Write which epoch dated this run, and why, into the run log."""
    sSource = dictVariables.get(S_DETERMINISM_EPOCH_SOURCE_KEY)
    if sSource:
        await fnLogging({"sType": "output", "sLine": sSource})


async def _fnAnnounceDegradedDeterminism(fnLogging, dictVariables):
    """Write the skipped-determinism notice into the run log.

    The run proceeds either way, but a reproduction that later fails
    on differing output bytes has to be explainable. The per-step
    ``bDeterminismEnvApplied`` flag is the durable record; this line
    puts the same fact in front of the researcher while the run is
    happening, and into the log file kept beside it.
    """
    if dictVariables.get(S_DETERMINISM_APPLIED_KEY):
        return
    await fnLogging({
        "sType": "output",
        "sLine": (
            "WARNING: SOURCE_DATE_EPOCH could not be derived from the "
            "project repository HEAD; this run's figures and archives "
            "are not byte-reproducible."
        ),
    })
