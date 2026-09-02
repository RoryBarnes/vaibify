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
import posixpath

from vaibify.gui.pipelineUtils import fbStepIsInteractive
from vaibify.reproducibility.environmentSnapshot import (
    fiRecordedSourceDateEpoch,
)
from vaibify.reproducibility.l3Attestation import fsCurrentManifestDigest
from vaibify.reproducibility.rerunDiagnostics import (
    S_FAILURE_KIND_PREFLIGHT,
    ftBuildRerunDiagnosticsCollector,
)
from vaibify.reproducibility.manifestPaths import (
    fdictWorkflowTemplateValues,
    flistStepDeclarationRepoPaths,
    flistStepOutputRepoPaths,
)
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
    "S_DIVERGENCE_EVERY_ENTRY_GIVEN",
    "S_DIVERGENCE_LOCK_UNSATISFIED",
    "S_DIVERGENCE_ROOT_MISMATCH",
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
    "flistCarriedOutputRepoPaths",
    "flistNameStepsThatBlockARerun",
    "flistSelectStepsWhoseOutputsAreGiven",
]


# Non-path entries that may appear in ``listDivergedHashes``. They name a
# failure of the comparison itself rather than a file whose hash moved,
# and are always listed before any diverged path so the first line of a
# failed attestation states the dominant cause.
# Plain language on purpose. This string is read by a researcher on
# their dashboard, not by an engineer reading a log: "exited
# non-zero" names a POSIX convention and says nothing about what
# went wrong, and it was the whole of what one researcher was told
# about a failed reproduction (2026-09-01). The cause follows on
# its own line, from the diagnostics collector.
S_DIVERGENCE_PIPELINE_FAILED = "the reproduction run failed"
S_DIVERGENCE_MANIFEST_UNREADABLE = "MANIFEST.sha256 missing or unreadable"
S_DIVERGENCE_MANIFEST_MUTATED = "MANIFEST.sha256 changed during the rerun"
S_DIVERGENCE_MANIFEST_EMPTY = "MANIFEST.sha256 pins no files"
S_DIVERGENCE_ROOT_MISMATCH = (
    "the rerun would execute in a different directory from the one "
    "the comparison reads"
)
S_DIVERGENCE_EVERY_ENTRY_GIVEN = (
    "every manifest entry is an output of a step the rerun does not "
    "execute"
)
S_DIVERGENCE_LOCK_UNSATISFIED = (
    "the pinned image does not satisfy requirements.lock"
)


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

    A workflow containing DISABLED steps is refused before any step
    executes (``bRerunAttempted`` False, one divergence line per such
    step): a disabled step leaves its pinned outputs untouched, so
    every hash would trivially match and the attestation would certify
    a rerun that ran nothing. INTERACTIVE steps do not refuse. Their
    outputs are human-made data the steps below them consume, so the
    rerun carries them verbatim and drops them from the comparison,
    reporting them in ``listCarriedPaths`` — see
    :func:`flistSelectStepsWhoseOutputsAreGiven`.
    """
    filesRepo = ffilesEnsureRepoFiles(filesRepo)
    dictExpectedManifest = fdictSnapshotExpectedManifest(filesRepo)
    listBlocking = flistNameStepsThatBlockARerun(dictWorkflow)
    if listBlocking:
        dictOutcome = {
            "bPassed": False,
            "bRerunAttempted": False,
            "iOutputHashesMatched": 0,
            "iOutputHashesTotal": 0,
            "listCarriedPaths": [],
            "dictRerunFailure": {},
            "listDivergedHashes": listBlocking,
        }
        dictOutcome["sManifestDigest"] = dictExpectedManifest["sDigest"]
        return dictOutcome
    sRootRefusal = _fsRefuseAMismatchedRunRoot(
        dictWorkflow, sWorkflowPath, filesRepo,
    )
    if sRootRefusal:
        dictOutcome = fdictUnrunOutcome(sRootRefusal)
        dictOutcome["sManifestDigest"] = dictExpectedManifest["sDigest"]
        return dictOutcome
    listCarriedPaths = flistCarriedOutputRepoPaths(dictWorkflow)
    fnCollect, dictDiagnostics = ftBuildRerunDiagnosticsCollector(
        dictWorkflow, fnStatusCallback,
    )
    bRerunSucceeded = fbRunWorkflowInContainer(
        connectionDocker, sContainerId, dictWorkflow, sWorkflowPath,
        posixpath.dirname(sWorkflowPath), fnCollect,
        iSourceDateEpochOverride=fiRecordedSourceDateEpoch(filesRepo),
    )
    dictOutcome = fdictVerifyRerunOutputs(
        filesRepo, bRerunSucceeded, dictExpectedManifest, listCarriedPaths,
        dictDiagnostics,
    )
    dictOutcome["sManifestDigest"] = dictExpectedManifest["sDigest"]
    return dictOutcome


def fsResolveRunnerRepoRoot(dictWorkflow, sWorkflowPath):
    """Return the repo root the pipeline runner will resolve steps against.

    Asked of the runner's OWN derivation rather than recomposed here,
    because a second derivation is how the two came to disagree in the
    first place. The runner takes a workflow DIRECTORY and peels a
    level before cutting at ``.vaibify``; handing it a repository root
    instead yields that root's PARENT, silently.
    """
    from vaibify.gui.workflowManager import fdictBuildGlobalVariables

    return fdictBuildGlobalVariables(
        dictWorkflow, posixpath.dirname(sWorkflowPath),
    ).get("sRepoRoot", "")


def _fsRefuseAMismatchedRunRoot(dictWorkflow, sWorkflowPath, filesRepo):
    """Return a refusal when the run root and the comparison root differ.

    This is a FALSE PASS guard, not tidiness. The rerun executes steps
    under the runner's resolved root and the comparison re-hashes under
    ``filesRepo``. If those are different directories, the steps write
    somewhere the comparison never looks -- so every pinned artefact is
    found exactly as the archive left it, every hash matches, and a
    workflow that reproduced nothing is attested as byte-identical.

    It is not hypothetical. The shadow lane passed the repository root
    where the runner wanted the workflow directory, so steps resolved
    against ``/shadow`` while the comparison read
    ``/shadow/<repo>`` (researcher-reported, 2026-09-01). That run
    happened to fail preflight because ``/shadow/<step>`` did not
    exist; had the layout put anything runnable there, the attestation
    would have PASSED.
    """
    sComparisonRoot = posixpath.normpath(fsRepoRootOf(filesRepo) or "")
    sRunnerRoot = posixpath.normpath(
        fsResolveRunnerRepoRoot(dictWorkflow, sWorkflowPath) or "",
    )
    if not sRunnerRoot or sRunnerRoot == sComparisonRoot:
        return ""
    return (
        f"{S_DIVERGENCE_ROOT_MISMATCH}: it would run in "
        f"{sRunnerRoot!r} while the comparison reads "
        f"{sComparisonRoot!r}"
    )


def flistNameStepsThatBlockARerun(dictWorkflow):
    """Name every step that would make an unattended rerun meaningless.

    A step DISABLED in the dashboard is skipped by the unattended
    runner, so its pinned outputs sit untouched, every hash trivially
    matches, and a "byte-identical rerun" would be certified with
    nothing rerun. Interactive steps are skipped by that same runner
    and are NOT blocking: the researcher declared them human-driven,
    and their outputs are carried as given (see
    :func:`flistSelectStepsWhoseOutputsAreGiven`). The difference is
    that being disabled is a switch, not a declared property of the
    workflow — carrying a disabled step's outputs would let anyone
    silence a step and still attest around it.

    The degenerate workflow blocks too: with no steps at all, 0-of-0
    execution exits zero and proves nothing. Every reason here is
    returned before any compute is spent.
    """
    listSteps = dictWorkflow.get("listSteps", []) or []
    if not listSteps:
        return ["workflow contains no steps to execute"]
    return [
        f"step '{dictStep.get('sName', '')}' is disabled and would not "
        f"execute"
        for dictStep in listSteps
        if not fbStepIsInteractive(dictStep)
        and not dictStep.get("bRunEnabled", True)
    ]


def flistSelectStepsWhoseOutputsAreGiven(dictWorkflow):
    """Return every step the rerun carries instead of executing.

    An interactive step needs a researcher and a rerun has none, so its
    outputs are not a computation the rerun could repeat: they are data
    a human produced, which the steps below it consume as input. The
    shadow's repository copy already carries those files verbatim — it
    copies everything git can enumerate, tracked or not — so the
    executable steps run against exactly the bytes the original run
    used. That is what makes a workflow with a human step in the middle
    reproducible for every step that is not one.

    What must never follow is counting those files as reproduced. They
    were given, not re-derived, so they leave the comparison
    (:func:`flistCarriedOutputRepoPaths`) and the attestation names
    them. The AI Declaration needs no case of its own here: it is an
    interactive step, and this is the rule that covers it.
    """
    return [
        dictStep
        for dictStep in dictWorkflow.get("listSteps", []) or []
        if fbStepIsInteractive(dictStep)
    ]


def flistCarriedOutputRepoPaths(dictWorkflow):
    """Return the repo-relative paths the comparison must not count.

    Resolved through the SAME helpers the manifest writer uses. An
    exclusion set resolved differently from the inclusion set excludes
    nothing and reports nothing: the paths simply fail to match, and
    every given file is silently graded as reproduced — which is the
    false pass this whole lane exists to prevent.

    A path an EXECUTED step also declares stays in the comparison. The
    rerun genuinely re-derives it, and where the two claims disagree
    the one backed by execution is the stronger.
    """
    listSteps = dictWorkflow.get("listSteps", []) or []
    dictTemplateValues = fdictWorkflowTemplateValues(dictWorkflow)
    setGiven = _fsetDeclaredOutputPaths(
        flistSelectStepsWhoseOutputsAreGiven(dictWorkflow),
        dictTemplateValues,
    )
    setExecuted = _fsetDeclaredOutputPaths(
        [
            dictStep for dictStep in listSteps
            if not fbStepIsInteractive(dictStep)
        ],
        dictTemplateValues,
    )
    return sorted(setGiven - setExecuted)


def _fsetDeclaredOutputPaths(listSteps, dictTemplateValues):
    """Return every declared output path of these steps, repo-relative.

    An ai-declaration step's declaration file joins its outputs: the
    manifest pins it as a publication artefact, and a human wrote it,
    so it is given for exactly the reason its step is.
    """
    setPaths = set()
    for dictStep in listSteps:
        setPaths.update(
            flistStepOutputRepoPaths(dictStep, dictTemplateValues),
        )
        setPaths.update(flistStepDeclarationRepoPaths(dictStep))
    return {sPath for sPath in setPaths if sPath}


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
    listCarriedPaths=(), dictRerunFailure=None,
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
    listCompared, listCarried = _ftPartitionManifestEntries(
        listEntries, listCarriedPaths,
    )
    if not listCompared:
        return _fdictNoComparisonOutcome(
            S_DIVERGENCE_EVERY_ENTRY_GIVEN, bRerunSucceeded, listCarried,
        )
    listMismatches = _flistVerifyEntriesOrNone(filesRepo, listCompared)
    if listMismatches is None:
        return _fdictNoComparisonOutcome(
            S_DIVERGENCE_MANIFEST_UNREADABLE, bRerunSucceeded, listCarried,
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
            len(listCompared) - len(listMismatches), 0,
        ),
        "iOutputHashesTotal": len(listCompared),
        "listCarriedPaths": listCarried,
        "dictRerunFailure": dict(dictRerunFailure or {}),
        "listDivergedHashes": _flistOrderDivergences(
            [dictMismatch["sPath"] for dictMismatch in listMismatches],
            bRerunSucceeded, bManifestMoved, dictRerunFailure,
        ),
    }


def _ftPartitionManifestEntries(listEntries, listCarriedPaths):
    """Split manifest entries into (compared, carried-path names).

    The carried half is intersected with the entries actually pinned
    rather than reported as declared: a given step may name an output
    the manifest does not pin, and calling that "excluded from the
    comparison" would describe an exclusion that never happened.
    """
    setCarried = set(listCarriedPaths or ())
    listCompared = [
        dictEntry for dictEntry in listEntries
        if dictEntry["sPath"] not in setCarried
    ]
    listCarried = sorted(
        dictEntry["sPath"] for dictEntry in listEntries
        if dictEntry["sPath"] in setCarried
    )
    return listCompared, listCarried


def _flistOrderDivergences(
    listPaths, bRerunSucceeded, bManifestMoved, dictRerunFailure=None,
):
    """Prepend the comparison-level failures so the dominant cause reads first.

    A failed pipeline keeps its generic first line -- callers match on
    ``S_DIVERGENCE_PIPELINE_FAILED`` to decide what to print -- and
    gains a second naming the step, because the generic line alone told
    a researcher nothing they could act on and the shadow that held the
    evidence was already destroyed.
    """
    listDiverged = list(listPaths)
    if bManifestMoved:
        listDiverged = [S_DIVERGENCE_MANIFEST_MUTATED] + listDiverged
    if not bRerunSucceeded:
        listDiverged = (
            [S_DIVERGENCE_PIPELINE_FAILED]
            + _flistExplainTheFailure(dictRerunFailure)
            + listDiverged
        )
    return listDiverged


def _flistExplainTheFailure(dictRerunFailure):
    """Return the researcher-facing lines naming what actually failed.

    Empty when nothing was collected, which is honest: a caller that
    passed no diagnostics did not fail to find the cause, it never
    looked.

    The two kinds are worded so they cannot be confused. A step that
    ran and failed is a problem inside that step; a run refused by
    preflight never started at all, and telling a researcher their
    workflow "failed" when it was never attempted sends them looking
    at the wrong thing entirely.
    """
    if not dictRerunFailure:
        return []
    if dictRerunFailure.get("sKind") == S_FAILURE_KIND_PREFLIGHT:
        return _flistDescribePreflight(
            dictRerunFailure.get("listErrors") or [],
        )
    sLabel = dictRerunFailure.get("sStepLabel") or "?"
    sName = dictRerunFailure.get("sStepName") or ""
    iExit = dictRerunFailure.get("iExitCode") or 0
    return [
        f"step {sLabel} '{sName}' stopped with error code {iExit}"
    ]


def _flistDescribePreflight(listErrors):
    """Say the cause once, then list each DISTINCT problem beneath it.

    Repeating "the run was stopped before any step could start" in
    front of every error turned two broken directories into twelve
    near-identical lines, which reads as a wall rather than a list of
    things to fix. Duplicates are dropped in order: preflight can
    report the same fault once per command in a step, and a researcher
    fixes it once.
    """
    listUnique, setSeen = [], set()
    for sError in listErrors:
        if sError in setSeen:
            continue
        setSeen.add(sError)
        listUnique.append(sError)
    return ["the run was stopped before any step could start"] + listUnique


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
        "listCarriedPaths": [],
        "dictRerunFailure": {},
        "listDivergedHashes": [sReason],
    }


def _fdictNoComparisonOutcome(
    sDivergence, bRerunSucceeded, listCarriedPaths=(),
):
    """Return the fail-closed outcome when no comparison was possible.

    An unreadable manifest, a manifest that pins no files, and a
    manifest whose every entry is a given step's output all fail the
    same way: a comparison against nothing must never be recorded as
    one that passed — zero of zero is a vacuous match, not a
    reproduction. The last of those is the one carrying introduced, and
    it is why carrying cannot be a blanket exemption: a workflow whose
    outputs are ALL human-made has nothing for a rerun to reproduce.
    """
    return {
        "bPassed": False,
        "iOutputHashesMatched": 0,
        "iOutputHashesTotal": 0,
        "listCarriedPaths": list(listCarriedPaths or []),
        "dictRerunFailure": {},
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
