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

from .pipelineUtils import fsShellQuote

__all__ = [
    "S_ENV_PREFIX_KEY",
    "S_DETERMINISM_APPLIED_KEY",
    "S_MATPLOTLIB_CONFIG_DIR",
]

S_ENV_PREFIX_KEY = "__sEnvPrefix"
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
):
    """Return shell prefix that pins the run's time and figure salts.

    One derivation — the HEAD commit epoch — feeds both consumers, so
    identical source produces byte-stable figures across reruns and
    across machines.

    Returns empty string if the epoch cannot be determined; callers
    must not block step execution on the result, but they MUST record
    the skip — see :func:`_fnInjectDeterminismEnvPrefix`.
    """
    iEpoch = await _fiQueryHeadCommitEpoch(
        connectionDocker, sContainerId, sProjectRepoPath,
    )
    if iEpoch <= 0:
        return ""
    return (
        f"export SOURCE_DATE_EPOCH={iEpoch} && "
        + _fsBuildMatplotlibSaltPrefix(iEpoch)
    )


async def _fnInjectDeterminismEnvPrefix(
    connectionDocker, sContainerId, dictWorkflow, dictVariables,
):
    """Compute the env prefix once and stash it in dictVariables.

    Bundles the determinism prefix with a
    ``VAIBIFY_ACTIVE_WORKFLOW_SLUG`` export so the marker conftest
    namespaces writes under the active workflow when commands flow
    through ``_ftRunCommandList`` (e.g. the runAllTests path).

    Also stashes whether the determinism prefix was actually built.
    The slug export is appended unconditionally, so a non-empty
    ``S_ENV_PREFIX_KEY`` is NOT evidence the epoch was exported —
    callers must read the boolean, never sniff the string.
    """
    from .fileStatusManager import fsWorkflowSlugFromPath
    sProjectRepoPath = dictWorkflow.get("sProjectRepoPath", "")
    sEnvPrefix = await _fsBuildDeterminismEnvPrefix(
        connectionDocker, sContainerId, sProjectRepoPath,
    )
    dictVariables[S_DETERMINISM_APPLIED_KEY] = bool(sEnvPrefix)
    sWorkflowSlug = fsWorkflowSlugFromPath(
        dictWorkflow.get("sPath", ""),
    )
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
