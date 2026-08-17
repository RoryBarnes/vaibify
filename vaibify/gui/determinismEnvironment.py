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

from .pipelineUtils import fsShellQuote

__all__ = [
    "S_ENV_PREFIX_KEY",
    "S_ENV_OVERLAY_KEY",
    "S_DETERMINISM_APPLIED_KEY",
    "S_MATPLOTLIB_CONFIG_DIR",
]

S_ENV_PREFIX_KEY = "__sEnvPrefix"
S_ENV_OVERLAY_KEY = "__dictEnvOverlay"
S_DETERMINISM_APPLIED_KEY = "__bDeterminismApplied"

# matplotlib reads ``matplotlibrc`` from ``MPLCONFIGDIR`` only after a
# working-directory ``matplotlibrc`` and after ``MATPLOTLIBRC``
# (measured against matplotlib 3.5.0), so seeding the salt here supplies
# a default the researcher can still override, rather than clobbering
# one they set deliberately.
S_MATPLOTLIB_CONFIG_DIR = "/tmp/vaibifyMatplotlib"


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


def _fsBuildMatplotlibSaltPrefix(iEpoch):
    """Return shell that pins matplotlib's ``svg.hashsalt`` to iEpoch.

    There is no environment variable for that rcParam, so the salt is
    written into a ``matplotlibrc`` inside ``MPLCONFIGDIR``. Failing to
    write the file reports on stderr and does not break the ``&&``
    chain: a step must still run when its determinism cannot be
    guaranteed.
    """
    sDirectory = fsShellQuote(S_MATPLOTLIB_CONFIG_DIR)
    sFile = fsShellQuote(S_MATPLOTLIB_CONFIG_DIR + "/matplotlibrc")
    return (
        f"export MPLCONFIGDIR={sDirectory} && "
        f"{{ mkdir -p {sDirectory} && "
        f"printf '%s\\n' 'svg.hashsalt: {iEpoch}' > {sFile} || "
        f"echo 'vaibify: matplotlib svg.hashsalt not pinned' >&2; }} && "
    )


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
    return (
        f"export SOURCE_DATE_EPOCH={iEpoch} && "
        + _fsBuildMatplotlibSaltPrefix(iEpoch)
    )


async def _fiResolveRunEpoch(
    connectionDocker, sContainerId, sProjectRepoPath,
    iSourceDateEpochOverride,
):
    """Return the run's epoch: the recorded override, else HEAD's.

    Shared by both lanes so the override contract cannot drift between
    them. The tier 5 rerun lane passes the epoch recorded in the
    envelope, because the commit that published the manifest moved
    HEAD: re-deriving would salt the rerun's figures differently from
    the pinned ones and every timestamped artefact would diverge.
    """
    if iSourceDateEpochOverride > 0:
        return iSourceDateEpochOverride
    return await _fiQueryHeadCommitEpoch(
        connectionDocker, sContainerId, sProjectRepoPath,
    )


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

    The host twin of :func:`_fsBuildMatplotlibSaltPrefix`: the rcParam
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
    from vaibify.config.registryManager import fbIsHostProject
    sProjectRepoPath = dictWorkflow.get("sProjectRepoPath", "")
    sWorkflowSlug = fsWorkflowSlugFromPath(
        dictWorkflow.get("sPath", ""),
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
