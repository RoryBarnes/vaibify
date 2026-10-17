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
import hashlib
import posixpath
from datetime import datetime, timezone

from vaibify.gui.pipelineUtils import fbStepIsInteractive
from vaibify.reproducibility.assessmentLane import (
    S_INCONSISTENT_ASSESSMENT,
    fdictOpenAssessorLane,
    fdictRunAssessorAssess,
    flistCrossCheckAssessment,
)
from vaibify.reproducibility.environmentSnapshot import (
    fdictReadEnvironmentJson,
    fiRecordedSourceDateEpoch,
)
from vaibify.reproducibility.l3Attestation import fsCurrentManifestDigest
from vaibify.reproducibility.rerunDiagnostics import (
    S_FAILURE_KIND_PREFLIGHT,
    ftBuildRerunDiagnosticsCollector,
)
from vaibify.reproducibility.manifestPaths import (
    fdictWorkflowTemplateValues,
)
from vaibify.reproducibility.manifestWriter import (
    fdictHashManifestEntries,
    fiCountManifestEntries,
    flistParseManifestLines,
)
from vaibify.reproducibility.rerunPreparation import (
    RerunPreparationRefusedError,
    fdictClassifyRerunPaths,
    fdictClearShadowBeforeRerun,
    fsetDeclaredOutputRepoPaths,
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
    "S_DIVERGENCE_NO_OUTPUT_REGENERABLE",
    "S_DIVERGENCE_PIPELINE_FAILED",
    "S_FILE_CARRIED",
    "S_FILE_DIVERGED",
    "S_FILE_MATCHED",
    "S_FILE_MISSING",
    "S_ROLE_CARRIED",
    "S_ROLE_OUTPUT",
    "S_ROLE_PINNED_INPUT",
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
S_DIVERGENCE_NO_OUTPUT_REGENERABLE = (
    "no declared output of a step the rerun executes is pinned in "
    "MANIFEST.sha256, so the rerun has nothing it could have "
    "regenerated and compared"
)


# The per-file verdicts in ``listFileOutcomes``. One record per frozen
# manifest entry, in the manifest's order, and the AUTHORITY every
# count, path list and the reproduced manifest are derived from --
# nothing downstream may re-hash after the shadow is destroyed or
# re-read a manifest the rerun may have mutated.
S_FILE_MATCHED = "matched"
S_FILE_DIVERGED = "diverged"
S_FILE_MISSING = "missing"
S_FILE_CARRIED = "carried"

# The ROLE of a manifest entry in the comparison. A declared output of
# an executed step was deleted before the run, so its hash proves the
# run regenerated it; every other pinned file (scripts, input data,
# standards, the environment files) is only checked UNCHANGED.
S_ROLE_OUTPUT = "output"
S_ROLE_PINNED_INPUT = "pinned-input"
S_ROLE_CARRIED = "carried"


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
    dictWorkflowEvidence = fdictSnapshotWorkflowEvidence(
        filesRepo, sWorkflowPath,
    )
    iSourceDateEpoch = fiRecordedSourceDateEpoch(filesRepo)
    sRerunStartedIso = datetime.now(timezone.utc).isoformat()
    dictReplayed, sProfileRefusal = ftReadReplayedExecutionProfile(filesRepo)
    dictProductionProvenance = _fdictSnapshotProductionProvenance(filesRepo)

    def fdictStampRunFacts(dictOutcome):
        """Name the manifest graded, the moment the run began, and its profile."""
        dictOutcome.update(dictWorkflowEvidence)
        dictOutcome["sManifestDigest"] = dictExpectedManifest["sDigest"]
        dictOutcome["iSourceDateEpoch"] = iSourceDateEpoch
        dictOutcome["sRerunStartedIso"] = sRerunStartedIso
        dictOutcome["sProfileId"] = dictReplayed.get("sProfileId")
        dictOutcome["dictRequested"] = dictReplayed.get("dictRequested")
        return dictOutcome

    listBlocking = flistNameStepsThatBlockARerun(dictWorkflow)
    if listBlocking:
        return fdictStampRunFacts({
            "bPassed": False,
            "bRerunAttempted": False,
            "iOutputHashesMatched": 0,
            "iOutputHashesTotal": 0,
            "iPinnedInputsUnchanged": 0,
            "iPinnedInputsTotal": 0,
            "dictPreRerunClearing": {},
            "listCarriedPaths": [],
            "listFileOutcomes": [],
            "dictRerunFailure": {},
            "listDivergedHashes": listBlocking,
        })
    sRootRefusal = _fsRefuseAMismatchedRunRoot(
        dictWorkflow, sWorkflowPath, filesRepo,
    )
    if sRootRefusal:
        return fdictStampRunFacts(fdictUnrunOutcome(sRootRefusal))
    if sProfileRefusal:
        return fdictStampRunFacts(fdictUnrunOutcome(sProfileRefusal))
    dictClassification = fdictClassifyRerunPaths(dictWorkflow)
    # The assessor is selected and prepared BEFORE anything is deleted:
    # ``prepare`` sets the published figures aside and checks every
    # pinned input, and a policy this vaibify lacks refuses the rerun
    # before it spends any compute.
    dictLane = fdictOpenAssessorLane(filesRepo, dictWorkflow)
    if "sUnrunReason" in dictLane:
        return fdictStampRunFacts(fdictUnrunOutcome(dictLane["sUnrunReason"]))
    listCarriedPaths = flistCarriedOutputRepoPaths(dictWorkflow)
    fnCollect, dictDiagnostics = ftBuildRerunDiagnosticsCollector(
        dictWorkflow, fnStatusCallback,
    )
    dictRun = _fdictClearThenRunSteps(
        connectionDocker, sContainerId, dictWorkflow, sWorkflowPath,
        filesRepo, dictClassification, dictLane, fnCollect,
        iSourceDateEpoch, dictReplayed,
    )
    if "sUnrunReason" in dictRun:
        return fdictStampRunFacts(fdictUnrunOutcome(dictRun["sUnrunReason"]))
    # The run is over and the re-hash begins. Announced because the
    # two look identical from outside -- a card that says "running"
    # through a long comparison of a large manifest is telling the
    # researcher the wrong thing. A consumer that does not know this
    # event ignores it, as every consumer does for every other type.
    if fnStatusCallback is not None:
        fnStatusCallback({"sType": "comparingOutputs"})
    dictAssessment = fdictRunAssessorAssess(
        filesRepo, dictLane, dictRun["listLedger"],
        dictRun.get("dictRunContext"))
    dictOutcome = fdictVerifyRerunOutputs(
        filesRepo, dictRun["bRerunSucceeded"], dictExpectedManifest,
        listCarriedPaths, dictDiagnostics,
        setProducedPaths=dictClassification["setProduced"],
        dictPreRerunClearing=dictRun["dictPreRerunClearing"],
        dictAssessment=dictAssessment,
    )
    fnAttachProductionContexts(
        dictOutcome, dictProductionProvenance, dictRun.get("dictRunContext"))
    return fdictStampRunFacts(dictOutcome)


def _fdictClearThenRunSteps(
    connectionDocker, sContainerId, dictWorkflow, sWorkflowPath,
    filesRepo, dictClassification, dictLane, fnCollect, iSourceDateEpoch,
    dictExecutionProfile,
):
    """Delete what the rerun regenerates, then run every step with evidence.

    When ``prepare`` already found a published input changed, nothing is
    deleted and nothing runs: the verdict is "Published inputs changed"
    whatever the steps would do, and ``assess`` says so. Returns
    ``{bRerunSucceeded, dictPreRerunClearing, listLedger,
    dictRunContext}`` or ``{sUnrunReason}`` for a refused preparation.
    The run replays ``dictExecutionProfile`` through the runner's own
    Stage A, and reports the decision and the probe as its run context.
    """
    dictRerunEvidence = {
        "sEvidenceDirectory": dictLane["sEvidence"], "listLedger": [],
        "dictExecutionProfile": dictExecutionProfile,
    }
    if dictLane["iPrepareExit"] != 0:
        return {"bRerunSucceeded": True, "dictPreRerunClearing": {},
                "listLedger": dictRerunEvidence["listLedger"]}
    try:
        dictPreRerunClearing = fdictClearShadowBeforeRerun(
            dictWorkflow, filesRepo, dictClassification,
        )
    except RerunPreparationRefusedError as errorRefused:
        return {"sUnrunReason": str(errorRefused)}
    bRerunSucceeded = fbRunWorkflowInContainer(
        connectionDocker, sContainerId, dictWorkflow, sWorkflowPath,
        posixpath.dirname(sWorkflowPath), fnCollect,
        iSourceDateEpochOverride=iSourceDateEpoch,
        dictRerunEvidence=dictRerunEvidence,
    )
    return {"bRerunSucceeded": bRerunSucceeded,
            "dictPreRerunClearing": dictPreRerunClearing,
            "listLedger": dictRerunEvidence["listLedger"],
            "dictRunContext": dictRerunEvidence.get("dictRunContext")}


def _fdictSnapshotProductionProvenance(filesRepo):
    """Return the output records as published, before the rerun rewrites them.

    The shadow's own runner records its outputs as it reruns, so the
    production contexts must be read first or they would describe the
    rerun instead of the run that made the published bytes.
    """
    from vaibify.gui.outputProvenance import fdictReadOutputProvenance
    return fdictReadOutputProvenance(filesRepo)


def fnAttachProductionContexts(dictOutcome, dictProductionProvenance,
                               dictRerunContext):
    """Put each byte-differing output's production context beside the rerun's.

    A difference is then explained from what was recorded -- the
    computer and settings that made the published bytes, and the ones
    the rerun ran under -- never predicted. An output whose record does
    not name its published digest is "production context unknown".
    """
    from vaibify.gui.outputProvenance import fdictProductionContextFor
    dictOutcome["dictRerunContext"] = dictRerunContext
    for dictFile in dictOutcome.get("listFileOutcomes") or []:
        if dictFile.get("sRole") != "output" or (
                dictFile.get("sObserved") == dictFile.get("sExpected")):
            continue
        dictFile["dictProductionContext"] = fdictProductionContextFor(
            dictProductionProvenance, dictFile.get("sPath", ""),
            dictFile.get("sExpected", ""))


def ftReadReplayedExecutionProfile(filesRepo):
    """Return ``(dictProfile, sRefusal)``: the envelope's profile, to replay.

    The rerun replays what the envelope RECORDED, never the table's
    current answer (F9), so a later vaibify cannot change what a
    published profile asked for. An envelope written before profiles
    existed records none, which is today's behavior: no settings. A
    recorded profile naming a setting outside the allowlist, or a value
    outside the pattern, refuses the rerun and names it (attack surface
    item 2): it came from a cloned file.
    """
    from vaibify.reproducibility import executionProfiles
    dictRecorded = (fdictReadEnvironmentJson(filesRepo) or {}).get(
        "dictExecutionProfile")
    if dictRecorded is None:
        return executionProfiles.fdictProfileFromTable(
            executionProfiles.S_DEFAULT_EXECUTION_PROFILE), ""
    try:
        return executionProfiles.fdictRequireValidProfile(dictRecorded), ""
    except executionProfiles.ExecutionProfileError as error:
        return {}, str(error)


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

    A path an EXECUTED step also declares is an ambiguous overlap, and
    :func:`~vaibify.reproducibility.rerunPreparation.fdictClearShadowBeforeRerun`
    refuses the rerun before any step runs; it is never silently kept
    and then counted as regenerated.
    """
    listSteps = dictWorkflow.get("listSteps", []) or []
    dictTemplateValues = fdictWorkflowTemplateValues(dictWorkflow)
    setGiven = fsetDeclaredOutputRepoPaths(
        flistSelectStepsWhoseOutputsAreGiven(dictWorkflow),
        dictTemplateValues,
    )
    setExecuted = fsetDeclaredOutputRepoPaths(
        [
            dictStep for dictStep in listSteps
            if not fbStepIsInteractive(dictStep)
        ],
        dictTemplateValues,
    )
    return sorted(setGiven - setExecuted)


def fbRunWorkflowInContainer(
    connectionDocker, sContainerId, dictWorkflow, sWorkflowPath,
    sWorkdir, fnStatusCallback=None, iSourceDateEpochOverride=0,
    dictRerunEvidence=None,
):
    """Run every enabled step of one workflow; True iff the pipeline exits 0.

    Every executed step runs its data commands, whatever its
    ``bPlotOnly`` says: that flag is a convenience for ordinary
    dashboard runs, and a rerun that skipped a step's data commands
    would grade the outputs it left untouched. This matches
    ``reproduce.sh``.

    This answers "did the workflow run again", nothing more. Whether it
    *reproduced* is a separate question that only the post-rerun re-hash
    can answer. ``iSourceDateEpochOverride`` carries the envelope's
    recorded epoch so the rerun salts its figures the way the pinned
    artefacts were salted, instead of re-deriving from a HEAD the
    publishing commit has since moved. The runner is imported lazily so
    importing this module does not pull the GUI pipeline machinery into
    the CLI's import graph. ``dictRerunEvidence``
    (``{sEvidenceDirectory, listLedger}``) asks the runner to name every
    command, point each test command's JUnit report at the evidence
    directory, and record every exit status.
    """
    from vaibify.gui.pipelineRunner import S_RUN_MODE_RERUN, fiRunAllSteps
    iExitCode = asyncio.run(fiRunAllSteps(
        connectionDocker, sContainerId, dictWorkflow, sWorkflowPath,
        sWorkdir, fnStatusCallback or _fnDiscardStatusEvent,
        iSourceDateEpochOverride=iSourceDateEpochOverride,
        sRunMode=S_RUN_MODE_RERUN,
        dictRerunEvidence=dictRerunEvidence,
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


def fdictSnapshotWorkflowEvidence(filesRepo, sWorkflowPath):
    """Freeze the workflow file's repo-relative path and digest.

    Taken beside the expected manifest, from the tree the rerun is
    about to run in, so a reproduction record binds the workflow it
    actually ran. An unreadable file is recorded as an empty digest,
    which no live file can equal.
    """
    filesRepo = ffilesEnsureRepoFiles(filesRepo)
    sRelativePath = posixpath.relpath(
        sWorkflowPath, fsRepoRootOf(filesRepo),
    ) if sWorkflowPath else ""
    if not sRelativePath or sRelativePath.startswith(".."):
        return {"sWorkflowRelativePath": "", "sWorkflowDigest": ""}
    try:
        sDigest = hashlib.sha256(
            filesRepo.fbaReadBytes(sRelativePath)).hexdigest()
    except (OSError, ValueError):
        sDigest = ""
    return {"sWorkflowRelativePath": sRelativePath, "sWorkflowDigest": sDigest}


def fdictVerifyRerunOutputs(
    filesRepo, bRerunSucceeded, dictExpectedManifest=None,
    listCarriedPaths=(), dictRerunFailure=None,
    setProducedPaths=None, dictPreRerunClearing=None, dictAssessment=None,
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

    ``setProducedPaths`` names the declared outputs of the steps the
    rerun executed -- files deleted from the shadow BEFORE the run, so
    a hash that matches proves the run regenerated them. They are
    counted as ``iOutputHashesMatched`` of ``iOutputHashesTotal``; every
    other pinned file (scripts, input data, standards, the environment
    files) is only checked UNCHANGED and counted as
    ``iPinnedInputsUnchanged`` of ``iPinnedInputsTotal``. ``None`` means
    no role split was made and every compared entry counts as an
    output -- correct only for a read-only re-check of a quiescent tree.
    ``dictPreRerunClearing`` is the evidence of what was deleted first.

    Omitting ``dictExpectedManifest`` snapshots at call time, which is
    correct only when nothing has run since — a read-only re-check of a
    quiescent tree. The rerun lanes must not do that, and do not:
    :func:`fdictRerunAndVerifyWorkflow` is the only production caller
    and it always snapshots first.

    ``dictAssessment`` is the rendered assessor's answer for the rerun
    lane. With it, this function's own hashing becomes the HOST's
    cross-check of the assessor, and the verdict is the assessment's:
    see :func:`fdictApplyAssessment`.
    """
    filesRepo = ffilesEnsureRepoFiles(filesRepo)
    if dictExpectedManifest is None:
        dictExpectedManifest = fdictSnapshotExpectedManifest(filesRepo)
    dictCompared = _fdictCompareRerunHashes(
        filesRepo, bRerunSucceeded, dictExpectedManifest, listCarriedPaths,
        dictRerunFailure, setProducedPaths, dictPreRerunClearing,
    )
    if dictAssessment is None:
        return dictCompared
    return fdictApplyAssessment(
        dictCompared, dictAssessment, dictExpectedManifest,
        _fbManifestMovedDuringRerun(filesRepo, dictExpectedManifest),
        bRerunSucceeded, dictRerunFailure,
    )


def _fdictCompareRerunHashes(
    filesRepo, bRerunSucceeded, dictExpectedManifest, listCarriedPaths,
    dictRerunFailure, setProducedPaths, dictPreRerunClearing,
):
    """Hash every frozen manifest entry and grade it by bytes alone."""
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
    # EVERY frozen entry is hashed, carried ones included: the
    # reproduced manifest lists them like any other line, and the
    # outcome's status is what marks them as given rather than graded.
    dictObserved = _fdictHashEntriesOrNone(filesRepo, listEntries)
    if dictObserved is None:
        return _fdictNoComparisonOutcome(
            S_DIVERGENCE_MANIFEST_UNREADABLE, bRerunSucceeded, listCarried,
        )
    listFileOutcomes = _flistBuildFileOutcomes(
        listEntries, dictObserved, set(listCarried), setProducedPaths,
    )
    listOutputOutcomes = [
        dictFile for dictFile in listFileOutcomes
        if dictFile["sRole"] == S_ROLE_OUTPUT
    ]
    if not listOutputOutcomes:
        return _fdictNoComparisonOutcome(
            S_DIVERGENCE_NO_OUTPUT_REGENERABLE, bRerunSucceeded,
            listCarried,
        )
    listInputOutcomes = [
        dictFile for dictFile in listFileOutcomes
        if dictFile["sRole"] == S_ROLE_PINNED_INPUT
    ]
    bManifestMoved = _fbManifestMovedDuringRerun(
        filesRepo, dictExpectedManifest,
    )
    listMismatchedPaths = [
        dictFile["sPath"] for dictFile in listFileOutcomes
        if dictFile["sStatus"] in (S_FILE_DIVERGED, S_FILE_MISSING)
    ]
    return {
        "bPassed": (
            bool(bRerunSucceeded)
            and not listMismatchedPaths
            and not bManifestMoved
        ),
        "iOutputHashesMatched": _fiCountMatched(listOutputOutcomes),
        "iOutputHashesTotal": len(listOutputOutcomes),
        "iPinnedInputsUnchanged": _fiCountMatched(listInputOutcomes),
        "iPinnedInputsTotal": len(listInputOutcomes),
        "dictPreRerunClearing": dict(dictPreRerunClearing or {}),
        # The paths that MATCHED, named rather than counted. A ratio
        # is a claim about a set the reader cannot see; a reproduction
        # report is read by somebody deciding whether to trust a
        # result, and "which files" is the question they are asking.
        # Derived from the per-file outcomes, as is every other count
        # and list here, so none of them can disagree with another.
        "listMatchedPaths": sorted(
            dictFile["sPath"] for dictFile in listFileOutcomes
            if dictFile["sStatus"] == S_FILE_MATCHED
        ),
        "listCarriedPaths": listCarried,
        "listFileOutcomes": listFileOutcomes,
        "dictRerunFailure": dict(dictRerunFailure or {}),
        "listDivergedHashes": _flistOrderDivergences(
            listMismatchedPaths, bRerunSucceeded, bManifestMoved,
            dictRerunFailure,
        ),
    }


def _fiCountMatched(listOutcomes):
    """Return how many per-file outcomes matched their pinned hash."""
    return sum(
        1 for dictFile in listOutcomes
        if dictFile["sStatus"] == S_FILE_MATCHED
    )


def _flistBuildFileOutcomes(
    listEntries, dictObserved, setCarried, setProducedPaths=None,
):
    """Return one ``{sPath, sExpected, sObserved, sStatus, sRole}`` per entry.

    In manifest order. ``sRole`` says why the entry is graded: a
    declared output of an executed step (regenerated), a pinned input
    (checked unchanged), or carried (given, not graded).
    """
    listFileOutcomes = []
    for dictEntry in listEntries:
        sPath = dictEntry["sPath"]
        sObserved = dictObserved.get(sPath)
        if sPath in setCarried:
            sStatus, sRole = S_FILE_CARRIED, S_ROLE_CARRIED
        else:
            sStatus = _fsGradeAgainstExpected(
                sObserved, dictEntry["sExpected"])
            sRole = (
                S_ROLE_OUTPUT
                if setProducedPaths is None or sPath in setProducedPaths
                else S_ROLE_PINNED_INPUT
            )
        listFileOutcomes.append({
            "sPath": sPath,
            "sExpected": dictEntry["sExpected"],
            "sObserved": sObserved,
            "sStatus": sStatus,
            "sRole": sRole,
        })
    return listFileOutcomes


def _fsGradeAgainstExpected(sObserved, sExpected):
    """Return matched, diverged or missing; an absent file never matches."""
    if sObserved is None:
        return S_FILE_MISSING
    if sObserved == sExpected:
        return S_FILE_MATCHED
    return S_FILE_DIVERGED


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
        "iPinnedInputsUnchanged": 0,
        "iPinnedInputsTotal": 0,
        "dictPreRerunClearing": {},
        "listCarriedPaths": [],
        "listFileOutcomes": [],
        "dictRerunFailure": {},
        "listDivergedHashes": [sReason],
    }


def fdictApplyAssessment(
    dictCompared, dictAssessment, dictExpectedManifest, bManifestMoved,
    bRerunSucceeded, dictRerunFailure,
):
    """Return the lane's outcome, graded by the assessor and checked by the host.

    The assessor's verdict is adopted only after the host has found
    nothing to disagree with: every graded output's hash, every
    command's exit status, the pinned inputs it saw changed or missing,
    and a re-derivation of every derived field with vaibify's own policy
    module. Anything the rerun could not be graded on -- a refused
    preparation, an assessor that did not answer, a preflight refusal,
    an inconsistent report, the assessor's own "Could not run" -- is an
    UNRUN outcome, which no lane writes as an attestation.

    A rerun that ran and failed is a graded result: ``bPassed`` is the
    assessment's exit code being zero, and ``dictRerunFailure`` is kept.
    """
    if "sUnrunReason" in dictAssessment:
        return fdictUnrunOutcome(dictAssessment["sUnrunReason"])
    if not dictCompared.get("listFileOutcomes"):
        return fdictUnrunOutcome(dictCompared["listDivergedHashes"][-1])
    if (dictRerunFailure or {}).get("sKind") == S_FAILURE_KIND_PREFLIGHT:
        return fdictUnrunOutcome("; ".join(_flistDescribePreflight(
            dictRerunFailure.get("listErrors") or [])))
    dictObserved = {dictFile["sPath"]: dictFile["sObserved"]
                    for dictFile in dictCompared["listFileOutcomes"]}
    listProblems = flistCrossCheckAssessment(
        dictAssessment, dictExpectedManifest["listEntries"], dictObserved,
        bManifestMoved)
    if listProblems:
        return fdictUnrunOutcome(
            f"{S_INCONSISTENT_ASSESSMENT}: " + "; ".join(listProblems))
    dictReport = dictAssessment["dictReport"]
    if dictReport["iExitCode"] == 4:
        return fdictUnrunOutcome(
            dictReport["sVerdictRendered"].replace("Could not run: ", "", 1))
    return _fdictMergeAssessment(
        dictCompared, dictReport, dictAssessment, bRerunSucceeded,
        dictRerunFailure)


def _fdictMergeAssessment(
    dictCompared, dictReport, dictAssessment, bRerunSucceeded,
    dictRerunFailure,
):
    """Return the hash outcome carrying the assessment's verdict and report."""
    dictOutcome = dict(dictCompared)
    bPassed = dictReport["iExitCode"] == 0
    listLines = []
    if not bPassed:
        listLines = [dictReport["sVerdictRendered"]]
        if not bRerunSucceeded:
            listLines += ([S_DIVERGENCE_PIPELINE_FAILED]
                          + _flistExplainTheFailure(dictRerunFailure))
        listLines += [dictFile["sPath"]
                      for dictFile in dictCompared["listFileOutcomes"]
                      if dictFile["sStatus"] in (
                          S_FILE_DIVERGED, S_FILE_MISSING)]
    dictOutcome.update({
        "bPassed": bPassed,
        "bRerunAttempted": True,
        "iAssessmentExitCode": dictReport["iExitCode"],
        "sVerdict": dictReport["sVerdict"],
        "sVerdictRendered": dictReport["sVerdictRendered"],
        "sTallyRendered": dictReport["sTallyRendered"],
        "dictTally": dict(dictReport["dictTally"]),
        "sComparisonPolicy": dictReport["sComparisonPolicy"],
        "sComparisonPolicySource": dictReport["sComparisonPolicySource"],
        "dictAssessmentReport": dictReport,
        "listFileOutcomes": _flistAttachOutputOutcomes(
            dictCompared["listFileOutcomes"], dictReport),
        "listDivergedHashes": listLines,
    })
    return dictOutcome


def _flistAttachOutputOutcomes(listFileOutcomes, dictReport):
    """Add each file's graded outcome to the per-file list, in its order.

    A regenerated or carried output takes the assessment's outcome; a
    pinned input reads ``unchanged``, ``changed`` or ``missing``.
    """
    dictGraded = {dictOutput["sPath"]: dictOutput
                  for dictOutput in dictReport["listOutputs"]}
    dictInputWords = {S_FILE_MATCHED: "unchanged", S_FILE_DIVERGED: "changed",
                      S_FILE_MISSING: "missing"}
    listAttached = []
    for dictFile in listFileOutcomes:
        dictWithOutcome = dict(dictFile)
        dictOutput = dictGraded.get(dictFile["sPath"])
        if dictOutput is not None:
            dictWithOutcome["sOutcome"] = dictOutput["sOutcome"]
            dictWithOutcome["sOutcomeReason"] = dictOutput.get("sReason", "")
        else:
            dictWithOutcome["sOutcome"] = dictInputWords.get(
                dictFile["sStatus"], dictFile["sStatus"])
            dictWithOutcome["sOutcomeReason"] = ""
        listAttached.append(dictWithOutcome)
    return listAttached


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
        "iPinnedInputsUnchanged": 0,
        "iPinnedInputsTotal": 0,
        "dictPreRerunClearing": {},
        "listCarriedPaths": list(listCarriedPaths or []),
        "listFileOutcomes": [],
        "dictRerunFailure": {},
        "listDivergedHashes": _flistOrderDivergences(
            [sDivergence], bRerunSucceeded, False,
        ),
    }


def _fdictHashEntriesOrNone(filesRepo, listEntries):
    """Return the observed hashes, or ``None`` when hashing is impossible.

    A missing repo root, an IO error, or a malformed entry all mean the
    same thing to the caller: no comparison was possible. Distinguishing
    them here would only let the pass decision depend on which flavour
    of unusable the manifest was.
    """
    try:
        return fdictHashManifestEntries(filesRepo, listEntries)
    except (FileNotFoundError, OSError, ValueError):
        return None


def fiCountManifestEntriesOrZero(filesRepo):
    """Return the manifest entry count; an unusable manifest counts zero."""
    try:
        return fiCountManifestEntries(filesRepo)
    except (FileNotFoundError, OSError, ValueError):
        return 0
