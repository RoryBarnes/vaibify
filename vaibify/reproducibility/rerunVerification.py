"""Re-run a workflow in its container and derive the L3 hash verdict.

An L3 attestation claims two independent things: that the workflow ran
again, and that the artefacts it produced are byte-identical to the ones
``MANIFEST.sha256`` pins. Only the first is observable from a pipeline
exit code. A step that exits zero and writes one different byte is
exactly the case an exit-code check cannot see, and it is the case an
attestation must never certify — so the re-hash has to happen *after*
the rerun, against the artefacts the rerun just produced.

Three things must line up for that comparison to mean anything, and each
of them has been wrong here at least once:

* **The same filesystem.** The rerun executes inside a container whose
  ``/workspace`` is a Docker-managed named volume, not a bind mount. A
  verification rooted on a host clone re-hashes a tree the rerun never
  touched, finds every entry clean, and certifies a reproduction nobody
  observed. :func:`fdictRerunAndVerifyWorkflow` takes one ``filesRepo``
  and uses it for both halves so the roots cannot drift apart.
* **The same workflow.** A container may host several project repos.
  Rediscovering "the first workflow" at rerun time attests one workflow
  while running another, which reads as a complete, correct claim. The
  workflow and its container path are therefore explicit parameters,
  never rediscovered here.
* **An immutable expected side.** ``MANIFEST.sha256`` is the expected
  half of the comparison. Read after the run, a step that changed an
  output and re-pinned the manifest leaves a self-consistent tree with
  nothing to notice. The expected hashes are snapshotted before
  execution, and a manifest that moved during the run is itself a
  divergence.

Both lanes that can write an attestation — the dashboard's
``/level3/verify`` route and the ``vaibify reproduce --rerun`` CLI —
enter through :func:`fdictRerunAndVerifyWorkflow`. It exists as one
shared function because the two lanes previously derived the same four
fields separately and drifted: the CLI recorded ``N of N matched`` from
the exit code alone and never opened a file. The counts, the diverged
paths, and the pass decision now have one derivation, so a lane cannot
quietly disagree with the other about what "reproduced" means.
"""

import asyncio

from vaibify.gui.pipelineUtils import fbStepIsInteractive
from vaibify.reproducibility.environmentSnapshot import (
    fiRecordedSourceDateEpoch,
)
from vaibify.reproducibility.l3Attestation import fsCurrentManifestDigest
from vaibify.reproducibility.manifestWriter import (
    fiCountManifestEntries,
    flistParseManifestLines,
    flistVerifyManifestEntries,
)
from vaibify.reproducibility.repoFiles import (
    ffilesEnsureRepoFiles,
    fsRepoRootOf,
)


__all__ = [
    "S_DIVERGENCE_MANIFEST_EMPTY",
    "S_DIVERGENCE_MANIFEST_MUTATED",
    "S_DIVERGENCE_MANIFEST_UNREADABLE",
    "S_DIVERGENCE_PIPELINE_FAILED",
    "fbRunWorkflowInContainer",
    "fdictRerunAndVerifyWorkflow",
    "fdictSnapshotExpectedManifest",
    "fdictUnrunOutcome",
    "fdictVerifyRerunOutputs",
    "fiCountManifestEntriesOrZero",
    "flistNameUnexecutableSteps",
]


# Non-path entries that may appear in ``listDivergedHashes``. They name a
# failure of the comparison itself rather than a file whose hash moved,
# and are always listed before any diverged path so the first line of a
# failed attestation states the dominant cause.
S_DIVERGENCE_PIPELINE_FAILED = "pipeline rerun exited non-zero"
S_DIVERGENCE_MANIFEST_UNREADABLE = "MANIFEST.sha256 missing or unreadable"
S_DIVERGENCE_MANIFEST_MUTATED = "MANIFEST.sha256 changed during the rerun"
S_DIVERGENCE_MANIFEST_EMPTY = "MANIFEST.sha256 pins no files"


def fdictRerunAndVerifyWorkflow(
    connectionDocker, sContainerId, dictWorkflow, sWorkflowPath,
    filesRepo, fnStatusCallback=None,
):
    """Re-run one workflow in its container and hash what that run wrote.

    The single entry point for L3 tier 5, used by both the CLI and the
    dashboard. Every input is explicit: the caller names the container,
    the workflow, its container-side path, and the repo-file adapter to
    verify through. Nothing is rediscovered and nothing is resolved from
    a working directory, because both of those substitutions have
    already produced attestations that described the wrong thing.

    ``filesRepo`` must be rooted on the filesystem the rerun writes to —
    in production a
    :class:`~vaibify.reproducibility.repoFiles.ContainerRepoFiles` at the
    workflow's ``sProjectRepoPath``. Returns the four-field outcome
    :func:`~vaibify.reproducibility.l3Attestation.fdictBuildAttestation`
    consumes, plus ``sManifestDigest``: the digest of the manifest this
    comparison was actually made against. An attestation that labelled
    itself with some other envelope's digest — the host clone's, say —
    would be naming a thing it did not check.

    A workflow containing steps the unattended runner would silently
    skip — interactive steps, or steps disabled in the dashboard — is
    refused before any step executes (``bRerunAttempted`` False, one
    divergence line per unexecutable step). A skipped step leaves its
    pinned outputs untouched, so every hash would trivially match and
    the attestation would certify a rerun that ran nothing.
    """
    filesRepo = ffilesEnsureRepoFiles(filesRepo)
    dictExpectedManifest = fdictSnapshotExpectedManifest(filesRepo)
    listUnexecutable = flistNameUnexecutableSteps(dictWorkflow)
    if listUnexecutable:
        dictOutcome = {
            "bPassed": False,
            "bRerunAttempted": False,
            "iOutputHashesMatched": 0,
            "iOutputHashesTotal": 0,
            "listDivergedHashes": listUnexecutable,
        }
        dictOutcome["sManifestDigest"] = dictExpectedManifest["sDigest"]
        return dictOutcome
    bRerunSucceeded = fbRunWorkflowInContainer(
        connectionDocker, sContainerId, dictWorkflow, sWorkflowPath,
        fsRepoRootOf(filesRepo), fnStatusCallback,
        iSourceDateEpochOverride=fiRecordedSourceDateEpoch(filesRepo),
    )
    dictOutcome = fdictVerifyRerunOutputs(
        filesRepo, bRerunSucceeded, dictExpectedManifest,
    )
    dictOutcome["sManifestDigest"] = dictExpectedManifest["sDigest"]
    return dictOutcome


def flistNameUnexecutableSteps(dictWorkflow):
    """Name every step an unattended rerun cannot or would not execute.

    A tier 5 rerun runs with no researcher present, so an interactive
    step can never execute — the runner's unattended path returns
    success without running anything, which is precisely the outcome an
    attestation must never absorb silently. A step disabled in the
    dashboard is skipped the same way. In both cases the step's pinned
    outputs sit untouched on disk, every hash trivially matches, and a
    "byte-identical rerun" would be certified with nothing rerun. The
    honest verdict is a refusal that names each step, before any compute
    is spent: the researcher either enables the step, removes it, or
    accepts that this workflow cannot attest at tier 5.
    """
    listSteps = dictWorkflow.get("listSteps", [])
    if not listSteps:
        return ["workflow contains no steps to execute"]
    listReasons = []
    for dictStep in listSteps:
        sStepName = dictStep.get("sName", "")
        if fbStepIsInteractive(dictStep):
            listReasons.append(
                f"step '{sStepName}' is interactive and cannot execute "
                f"unattended"
            )
        elif not dictStep.get("bRunEnabled", True):
            listReasons.append(
                f"step '{sStepName}' is disabled and would not execute"
            )
    return listReasons


def fbRunWorkflowInContainer(
    connectionDocker, sContainerId, dictWorkflow, sWorkflowPath,
    sWorkdir, fnStatusCallback=None, iSourceDateEpochOverride=0,
):
    """Run every enabled step of one workflow; True iff the pipeline exits 0.

    This answers "did the workflow run again", nothing more. Whether it
    *reproduced* is a separate question that only the post-rerun re-hash
    can answer. ``iSourceDateEpochOverride`` carries the envelope's
    recorded epoch so the rerun salts its figures the way the pinned
    artefacts were salted, instead of re-deriving from a HEAD the
    publishing commit has since moved. The runner is imported lazily so
    importing this module does not pull the GUI pipeline machinery into
    the CLI's import graph.
    """
    from vaibify.gui.pipelineRunner import fiRunAllSteps
    iExitCode = asyncio.run(fiRunAllSteps(
        connectionDocker, sContainerId, dictWorkflow, sWorkflowPath,
        sWorkdir, fnStatusCallback or _fnDiscardStatusEvent,
        iSourceDateEpochOverride=iSourceDateEpochOverride,
    ))
    return iExitCode == 0


async def _fnDiscardStatusEvent(dictEvent):
    """Drop a pipeline status event for callers that report separately."""
    del dictEvent


def fdictSnapshotExpectedManifest(filesRepo):
    """Freeze the expected hashes and the manifest's own digest.

    Call this *before* the rerun. The returned entries are the expected
    side of the later comparison, and ``sDigest`` is what proves the
    manifest itself did not move while the workflow ran. An unreadable
    manifest is recorded as such rather than as an empty expectation, so
    the comparison fails closed instead of matching zero of zero.
    """
    filesRepo = ffilesEnsureRepoFiles(filesRepo)
    try:
        listEntries = flistParseManifestLines(filesRepo)
    except (FileNotFoundError, OSError, ValueError):
        return {"bReadable": False, "listEntries": [], "sDigest": ""}
    return {
        "bReadable": True,
        "listEntries": listEntries,
        "sDigest": fsCurrentManifestDigest(filesRepo),
    }


def fdictVerifyRerunOutputs(
    filesRepo, bRerunSucceeded, dictExpectedManifest=None,
):
    """Re-hash the pinned artefacts and return the attestation's hash fields.

    Call this only *after* the rerun has finished, so the hashes come
    from the artefacts the rerun produced, and pass the
    ``dictExpectedManifest`` that :func:`fdictSnapshotExpectedManifest`
    took *before* it. Returns ``bPassed``, ``iOutputHashesMatched``,
    ``iOutputHashesTotal`` and ``listDivergedHashes`` — the exact shape
    :func:`~vaibify.reproducibility.l3Attestation.fdictBuildAttestation`
    consumes. ``bPassed`` requires a zero-exit rerun, a clean re-hash,
    *and* an unmoved manifest; any one alone is not a reproduction. An
    unreadable manifest fails closed with zero entries counted, because
    a comparison that could not be performed must never be recorded as
    one that passed.

    Omitting ``dictExpectedManifest`` snapshots at call time, which is
    correct only when nothing has run since — a read-only re-check of a
    quiescent tree. The rerun lanes must not do that, and do not:
    :func:`fdictRerunAndVerifyWorkflow` is the only production caller
    and it always snapshots first.
    """
    filesRepo = ffilesEnsureRepoFiles(filesRepo)
    if dictExpectedManifest is None:
        dictExpectedManifest = fdictSnapshotExpectedManifest(filesRepo)
    if not dictExpectedManifest["bReadable"]:
        return _fdictNoComparisonOutcome(
            S_DIVERGENCE_MANIFEST_UNREADABLE, bRerunSucceeded,
        )
    listEntries = dictExpectedManifest["listEntries"]
    if not listEntries:
        return _fdictNoComparisonOutcome(
            S_DIVERGENCE_MANIFEST_EMPTY, bRerunSucceeded,
        )
    listMismatches = _flistVerifyEntriesOrNone(filesRepo, listEntries)
    if listMismatches is None:
        return _fdictNoComparisonOutcome(
            S_DIVERGENCE_MANIFEST_UNREADABLE, bRerunSucceeded,
        )
    bManifestMoved = _fbManifestMovedDuringRerun(
        filesRepo, dictExpectedManifest,
    )
    return {
        "bPassed": (
            bool(bRerunSucceeded)
            and not listMismatches
            and not bManifestMoved
        ),
        "iOutputHashesMatched": max(
            len(listEntries) - len(listMismatches), 0,
        ),
        "iOutputHashesTotal": len(listEntries),
        "listDivergedHashes": _flistOrderDivergences(
            [dictMismatch["sPath"] for dictMismatch in listMismatches],
            bRerunSucceeded, bManifestMoved,
        ),
    }


def _flistOrderDivergences(listPaths, bRerunSucceeded, bManifestMoved):
    """Prepend the comparison-level failures so the dominant cause reads first."""
    listDiverged = list(listPaths)
    if bManifestMoved:
        listDiverged = [S_DIVERGENCE_MANIFEST_MUTATED] + listDiverged
    if not bRerunSucceeded:
        listDiverged = [S_DIVERGENCE_PIPELINE_FAILED] + listDiverged
    return listDiverged


def _fbManifestMovedDuringRerun(filesRepo, dictExpectedManifest):
    """Return True when MANIFEST.sha256 differs from the pre-rerun snapshot.

    A step that re-pins the manifest over its own changed output has
    rewritten the expected side of the comparison mid-flight. The
    artefacts then match the new manifest perfectly, which is exactly
    why this has to be judged on the manifest's own bytes rather than on
    the entries agreeing.
    """
    sExpectedDigest = dictExpectedManifest.get("sDigest") or ""
    if not sExpectedDigest:
        return False
    return fsCurrentManifestDigest(filesRepo) != sExpectedDigest


def fdictUnrunOutcome(sReason):
    """Return the fail-closed outcome for a rerun that never started.

    A tier 5 that could not reach a container, or could not tell which
    workflow to run, has performed no comparison. It reports zero of
    zero and names why, never a pass. ``bRerunAttempted`` is False so
    reporters can say "the rerun never started" instead of describing
    the exit status of a run that did not happen.
    """
    return {
        "bPassed": False,
        "bRerunAttempted": False,
        "iOutputHashesMatched": 0,
        "iOutputHashesTotal": 0,
        "listDivergedHashes": [sReason],
    }


def _fdictNoComparisonOutcome(sDivergence, bRerunSucceeded):
    """Return the fail-closed outcome when no comparison was possible.

    An unreadable manifest and a manifest that pins no files fail the
    same way: a comparison against nothing must never be recorded as
    one that passed — zero of zero is a vacuous match, not a
    reproduction.
    """
    return {
        "bPassed": False,
        "iOutputHashesMatched": 0,
        "iOutputHashesTotal": 0,
        "listDivergedHashes": _flistOrderDivergences(
            [sDivergence], bRerunSucceeded, False,
        ),
    }


def _flistVerifyEntriesOrNone(filesRepo, listEntries):
    """Return the mismatch list, or ``None`` when hashing is impossible.

    A missing repo root, an IO error, or a malformed entry all mean the
    same thing to the caller: no comparison was possible. Distinguishing
    them here would only let the pass decision depend on which flavour
    of unusable the manifest was.
    """
    try:
        return flistVerifyManifestEntries(filesRepo, listEntries)
    except (FileNotFoundError, OSError, ValueError):
        return None


def fiCountManifestEntriesOrZero(filesRepo):
    """Return the manifest entry count; an unusable manifest counts zero."""
    try:
        return fiCountManifestEntries(filesRepo)
    except (FileNotFoundError, OSError, ValueError):
        return 0
