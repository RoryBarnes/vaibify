"""Test management route handlers."""

__all__ = ["fnRegisterAll"]

import asyncio
import os
import posixpath

from fastapi import HTTPException, Request

from ..actionCatalog import ffnAgentAction
from ...docker.confinedWrite import ContainerWriteRefusedError
from ..fileStatusManager import (
    fbMaybeAutoArchive,
    fsWorkflowSlugFromPath,
)
from vaibify.reproducibility.levelGates import fiComputeProofLevelUpToOne
from vaibify.config.mutationAdmission import fnReRaiseControlPlaneRefusal
from ..routeContext import (
    fdictCarryARefusalBackInsteadOfRaising,
    fdictRequireLaneTupleForCommit,
    ffilesForWorkflow,
    fdictCommitWorkflowSave,
    fgenericRunWorkerUnderTheDrain,
    fnRejectAgentTokenLane,
)
from ..routeScope import (
    S_CARRIER_MODE_A_SYNCHRONOUS,
    S_CARRIER_MODE_B_LOCK_HELD,
    ffnDeclareCarrierMode,
)
from ..determinismEnvironment import fsBuildTestExecutionPrefix
from ..pipelineRunner import fsShellQuote
from ..workflowManager import (
    fbDeriveUnnecessaryVerification,
    fdictResolveTestCommandGroups,
    fsResolveStepWorkdir,
)
from .. import projectRoots
from .. import workflowManager
from .. import pipelineServer as _pipelineServer
from ..pipelineServer import (
    SaveAndRunTestRequest,
    TestGenerateCategoryRequest,
    TestGenerateRequest,
    WORKSPACE_ROOT,
    fdictRequireWorkflow,
    fdictConfinedWriteKeywords,
    fnRejectWriteDenylistedPath,
    fsValidatePathWithinRoot,
    _fsSanitizeServerError,
)
from ..testGenerator import T_GENERATED_TEST_CATEGORIES
from .. import researcherNotices
from .. import standardsFiles
from ..pipelineUtils import MissingStepOutputsError, fsLabelFromStepIndex
from ..serverMiddleware import fbRequestRidesAgentLane
from ..testStatusManager import (
    _LIST_TEST_CATEGORIES,
    _fdictBuildTestResponse,
    _flistResolveTestCommands,
    _fnRecordTestResult,
    _fnRegisterTestCommand,
    _fnRemoveTestDirectory,
    _fnUpdateAggregateTestState,
    _fsBuildPytestCommand,
)


def _fnRequireStepIndexBeforeGenerating(dictWorkflow, iStepIndex):
    """Refuse an out-of-range step index BEFORE any carrier opens.

    Read out of the generator's failure modes, not guessed from its
    shape. ``fdictGenerateAllTests`` touches the container in both
    branches, and the only failure preceding that touch is
    ``_ftExtractStepInfo``'s ``IndexError``: LLM errors are caught per
    category and returned as ``sError``, context reads answer ``""``,
    and "no model configured" already answered ``bNeedsFallback``. A
    bad index arrives from the URL, so inside the carrier it would
    QUARANTINE an untouched container over a typo.
    """
    listSteps = dictWorkflow.get("listSteps", [])
    if iStepIndex < 0 or iStepIndex >= len(listSteps):
        raise HTTPException(
            404, f"Step index {iStepIndex} out of range",
        )


def _fsRequireConfiguredProviderKey():
    """Resolve the stored Anthropic API key or refuse with the fix.

    The request body no longer carries a raw key (agent-council design
    9.5): the route resolves the host keyring slot through
    ``secretManager`` at request time, BEFORE any carrier opens, so a
    missing key refuses an untouched container instead of quarantining
    one from inside the worker. The refusal names the exact host
    command that configures the key.
    """
    from vaibify.config.secretManager import fsRetrieveSecret
    from ..providerApiTransport import (
        S_PROVIDER_ANTHROPIC,
        fsProviderKeySlotName,
    )
    try:
        return fsRetrieveSecret(
            fsProviderKeySlotName(S_PROVIDER_ANTHROPIC), "keyring",
        )
    except Exception:
        raise HTTPException(
            409,
            "No Anthropic API key is configured on this host. "
            "Store one with: vaibify secret set-provider-key "
            "--provider anthropic",
        )


async def _fdictRunTestGeneration(
    dictCtx, sContainerId, iStepIndex,
    dictWorkflow, fdictGenerate, request, requestHttp, sApiKey,
):
    """Invoke the test generator under the drain; return its result dict.

    Mode (b): the generator makes a directory, writes a conftest
    marker, runs an introspection program over every declared output
    and writes three test files -- around an LLM round-trip on the
    non-deterministic branch. A hand-over landing part-way leaves a
    container holding half a test suite belonging to somebody else.

    The pre-flight above having taken the one refusal decided before a
    write, every failure reaching the ``except`` is AT or after one, so
    its 500 propagates and marks the record for reconciliation, which
    is right because nobody knows what landed. A refusal is re-raised
    as itself first: "not admitted" reported as "Generation failed" is
    the shape that silently downgraded a reproducibility badge once.
    """
    dictVars = dictCtx["variables"](sContainerId)
    sUser = dictCtx["containerUsers"].get(
        sContainerId, dictCtx.get("sTerminalUser")
    )

    def fdictGenerateOrRefuse():
        try:
            return fdictGenerate(
                dictCtx["docker"], sContainerId, iStepIndex,
                dictWorkflow, dictVars,
                request.bUseApi, sApiKey,
                sUser=sUser,
                bDeterministic=request.bDeterministic,
                bForceOverwrite=request.bForceOverwrite,
            )
        except MissingStepOutputsError as error:
            # Decided before anything is written, so it is carried
            # back as a refusal rather than poisoning the record.
            _fnRaiseMissingOutputsRefusal(
                dictCtx, sContainerId, dictWorkflow, iStepIndex,
                error.listMissingFiles, requestHttp,
            )

    def fdictGenerateTheTests(supervisor=None):
        del supervisor
        try:
            return fdictCarryARefusalBackInsteadOfRaising(
                fdictGenerateOrRefuse,
            )
        except Exception as error:
            fnReRaiseControlPlaneRefusal(error)
            raise HTTPException(
                500, f"Generation failed: "
                f"{_fsSanitizeServerError(str(error))}")

    return await fgenericRunWorkerUnderTheDrain(
        sContainerId, fdictGenerateTheTests, "generate-tests", requestHttp,
    )


def _fbNeedsClaudeFallback(dictCtx, sContainerId, request):
    """Return True if we need an LLM fallback and Claude is unavailable."""
    if request.bDeterministic or request.bUseApi:
        return False
    from ..testGenerator import fbContainerHasClaude
    return not fbContainerHasClaude(
        dictCtx["docker"], sContainerId)


def _fnApplyGeneratedTests(
    dictCtx, sContainerId, dictWorkflow, iStepIndex,
    dictResult, requestHttp,
):
    """Store generated test categories in the step and save.

    The generator builds ``sFilePath``/``sStandardsPath`` from the
    container-absolute step directory (Docker writes need absolute
    paths); the workflow document stores repo-relative paths, so the
    categories are normalized before saving.
    """
    from ..workflowManager import flistBuildTestCommands
    from ..workflowMigrations import fnMigrateAbsoluteTestPaths
    dictStep = dictWorkflow["listSteps"][iStepIndex]
    dictTests = dictStep.setdefault("dictTests", {})
    dictVerification = dictStep.setdefault("dictVerification", {})
    for sCategory, sVerifKey in _LIST_TEST_CATEGORIES:
        if sCategory in dictResult:
            dictTests[sCategory] = dictResult[sCategory]
            _fnSetAxisForGeneratedCategory(
                dictVerification, sVerifKey, dictResult[sCategory],
            )
    fnMigrateAbsoluteTestPaths(
        dictWorkflow, dictWorkflow.get("sProjectRepoPath", ""),
    )
    dictStep["saTestCommands"] = flistBuildTestCommands(dictStep)
    fdictCommitWorkflowSave(
        dictCtx, sContainerId, dictWorkflow, requestHttp,
        "Recording the generated tests",
    )


def _fnSetAxisForGeneratedCategory(dictVerification, sVerifKey, dictCategory):
    """Say what a freshly generated category can claim, before any run.

    N/A is "unnecessary" (grey, nothing to check); a category the
    generator could not derive is "error", never the green N/A an empty
    command list would otherwise derive to; a category that was N/A and
    now has checks starts again at "untested".
    """
    if dictCategory.get("sNotApplicableReason"):
        dictVerification[sVerifKey] = "unnecessary"
    elif dictCategory.get("sError"):
        dictVerification[sVerifKey] = "error"
    elif dictVerification.get(sVerifKey) == "unnecessary":
        dictVerification[sVerifKey] = "untested"


S_REFUSAL_OUTPUTS_MISSING = "outputs-missing"
I_MISSING_FILES_NAMED = 5


def _fsNameTheMissingFiles(listMissing):
    """Return the first few missing names, and how many more there are."""
    listNamed = [sFile[:120] for sFile in listMissing[:I_MISSING_FILES_NAMED]]
    sNames = ", ".join(listNamed)
    iMore = len(listMissing) - len(listNamed)
    return sNames + (f", and {iMore} more" if iMore > 0 else "")


def _fnRaiseMissingOutputsRefusal(
    dictCtx, sContainerId, dictWorkflow, iStepIndex, listMissing,
    requestHttp,
):
    """Raise the 409 for generating tests before the step's outputs exist.

    The researcher sees the refusal in the dashboard even when an agent
    asked: the agent's request queues an error notice naming the step
    and the files. Both texts are composed here; the file names are the
    workflow's own declarations, shown through the escaping toast.
    """
    sLabel = fsLabelFromStepIndex(dictWorkflow, iStepIndex)
    sFiles = _fsNameTheMissingFiles(listMissing)
    if fbRequestRidesAgentLane(requestHttp):
        researcherNotices.fnQueueResearcherNotice(
            dictCtx, sContainerId, researcherNotices.S_NOTICE_LEVEL_ERROR,
            f"An in-container agent asked for tests for step {sLabel}, "
            f"but its declared outputs do not exist yet: {sFiles}. "
            "Nothing was written.",
        )
    raise HTTPException(409, {
        "sRefusal": S_REFUSAL_OUTPUTS_MISSING,
        "listMissingFiles": list(listMissing),
        "sMessage": (
            f"Tests were not generated for step {sLabel}: its declared "
            f"outputs do not exist yet ({sFiles}). Run the step first, "
            "then generate its tests."
        ),
    })


async def _fnRefuseGenerationWithoutOutputs(
    dictCtx, sContainerId, dictWorkflow, iStepIndex, requestHttp,
):
    """Refuse before any carrier opens when a declared output is missing.

    A typed read, so no admission is needed and no journal record is
    opened: nothing is written, the tests directory included, and no
    model is asked.
    """
    from ..testGenerator import flistMissingStepOutputs
    listMissing = await asyncio.to_thread(
        flistMissingStepOutputs, dictCtx["docker"], sContainerId,
        dictWorkflow, iStepIndex,
    )
    if listMissing:
        _fnRaiseMissingOutputsRefusal(
            dictCtx, sContainerId, dictWorkflow, iStepIndex, listMissing,
            requestHttp,
        )


def _fnRemoveFilesOfInapplicableCategories(
    dictCtx, sContainerId, dictResult, requestHttp,
):
    """Remove the unmodified files of categories that became N/A.

    The generator proved each file unmodified before naming it; the
    removal runs under the same mode-(a) helper admission as the
    tests-directory removal. ``listFilesToRemove`` never reaches the
    workflow document.
    """
    listPaths = []
    for sCategory, _sVerifKey in _LIST_TEST_CATEGORIES:
        dictCategory = dictResult.get(sCategory)
        if isinstance(dictCategory, dict):
            listPaths.extend(dictCategory.pop("listFilesToRemove", []))
    if not listPaths:
        return
    from .. import commitCarrier
    dictLaneTuple = fdictRequireLaneTupleForCommit(
        requestHttp, sContainerId, "Removing tests that no longer apply",
    )
    sCommand = "rm -f -- " + " ".join(
        fsShellQuote(sPath) for sPath in listPaths
    )
    commitCarrier.fdictCommitSynchronousMutation(
        requestHttp.app.state, dictLaneTuple["sContainerName"],
        sContainerId, dictLaneTuple, "helper", "remove-inapplicable-tests",
        lambda: dictCtx["docker"].ftResultExecuteCommand(
            sContainerId, sCommand,
        ),
        {
            "iHolderPid": os.getpid(),
            "iHolderProcessGroup": os.getpgrp(),
        },
    )


def _fdictBuildGenerateResponse(dictResult, dictWorkflow, iStepIndex):
    """Build the HTTP response dict for test generation.

    Carries the step as the hub saved it, so the dashboard renders the
    hub's verdict on each category -- N/A, error or untested -- rather
    than deriving and saving its own over it.
    """
    from ..pipelineUtils import fdictStepWithLabel
    return {
        "dictStep": fdictStepWithLabel(dictWorkflow, iStepIndex),
        "sWorkflowFingerprint": (
            workflowManager.fsComputeWorkflowFingerprint(dictWorkflow)
        ),
        "sExactSourceFingerprint": dictWorkflow.get(
            "_sSourceFingerprint", "",
        ),
        "bGenerated": True,
        "dictIntegrity": dictResult.get("dictIntegrity", {}),
        "dictQualitative": dictResult.get(
            "dictQualitative", {}),
        "dictQuantitative": dictResult.get(
            "dictQuantitative", {}),
    }


def _fsAbsoluteStepWorkdir(dictStep, sRepoRoot):
    """Return the container-absolute step workdir for a `cd` command.

    Step ``sDirectory`` values are repo-relative; ``cd``-ing into them
    from docker exec's default WORKDIR (`/workspace`) misses the
    project repo prefix. Joining with ``sProjectRepoPath`` makes the
    cd land inside the workflow's repo. Idempotent on already-absolute
    values; returns ``"/workspace"`` for the legacy empty-dir case.
    """
    sDir = dictStep.get("sDirectory", "")
    if not sDir:
        return "/workspace"
    return fsResolveStepWorkdir(sDir, {"sRepoRoot": sRepoRoot})


def _fsPrefixWithWorkflowEnv(sCommand, sWorkflowSlug):
    """Prepend ``VAIBIFY_ACTIVE_WORKFLOW_SLUG=<slug>`` to a shell command.

    The marker conftest reads this env var to namespace its writes
    under the right workflow's subdirectory. Returns the command
    unchanged when the slug is empty (no active workflow context).
    """
    if not sWorkflowSlug:
        return sCommand
    return (
        "export VAIBIFY_ACTIVE_WORKFLOW_SLUG="
        + fsShellQuote(sWorkflowSlug) + " && " + sCommand
    )


# The category names the dashboard may tell the marker plugin it is
# running, by every spelling the routes hold (a request's "integrity",
# a step's "dictIntegrity"). Looked up, never composed: the value that
# reaches the shell is always one of these three words, whatever the
# caller's string was.
_DICT_SIGNALLED_CATEGORY_BY_NAME = {
    "integrity": "integrity", "dictIntegrity": "integrity",
    "qualitative": "qualitative", "dictQualitative": "qualitative",
    "quantitative": "quantitative", "dictQuantitative": "quantitative",
}


def _fsPrefixWithTestCategoryEnv(sCommand, sCategoryName):
    """Prepend ``VAIBIFY_TEST_CATEGORY=<category>`` to a category's command.

    The marker plugin treats it as authoritative for a session whose
    collection fails before any test names a file: the failure is
    charged to that category instead of to nobody. A name outside the
    fixed set (the legacy group, or anything else) exports nothing.
    """
    sCategory = _DICT_SIGNALLED_CATEGORY_BY_NAME.get(sCategoryName, "")
    if not sCategory:
        return sCommand
    return "export VAIBIFY_TEST_CATEGORY=" + sCategory + " && " + sCommand


def _fdictRunAllTestCategories(
    dictCtx, sContainerId, dictStep, sRepoRoot="", sWorkflowSlug="",
):
    """Run every resolved test group and return {group: result}."""
    sDir = _fsAbsoluteStepWorkdir(dictStep, sRepoRoot)
    dictVerification = dictStep.setdefault(
        "dictVerification", {})
    dictTests = dictStep.get("dictTests", {})
    dictVerificationKeys = dict(_LIST_TEST_CATEGORIES)
    from .. import truthDerivation
    dictCategoryResults = {}
    dictGroups = fdictResolveTestCommandGroups(dictStep)
    for sCategory, listCommands in dictGroups.items():
        sInvalid = fsInvalidStandardsReason(
            dictCtx["docker"], sContainerId, sDir, sCategory,
        )
        dictResult = _fdictStandardsErrorResult(sInvalid) if sInvalid else (
            _fdictRunOneTestCategory(
                dictCtx, sContainerId, sDir, listCommands, sWorkflowSlug,
                sCategory,
            )
        )
        iExitCode = 0 if dictResult["bPassed"] else 1
        if sCategory in dictVerificationKeys:
            dictVerification[dictVerificationKeys[sCategory]] = (
                "error" if sInvalid else
                truthDerivation.fsResolveUnitTestFromExitCode(iExitCode))
        sCatOutput = dictResult.get("sOutput", "")
        if sCatOutput and sCategory in dictTests:
            dictTests[sCategory]["sLastOutput"] = sCatOutput
        dictCategoryResults[sCategory] = dictResult
    dictWorkflowLive = dictCtx["workflows"].get(sContainerId)
    if dictCategoryResults and dictWorkflowLive is not None:
        workflowManager.fnStampFieldProducer(
            dictWorkflowLive, dictStep, "dictVerification",
        )
    return dictCategoryResults


def fsInvalidStandardsReason(
    connectionDocker, sContainerId, sStepDirectory, sCategory,
):
    """Return why a category's standards file is not valid JSON, or "".

    Read before the category runs, because text reaches a standards
    file verbatim through the agent's shell as well as through the
    hub's own write routes: a malformed one would otherwise surface as
    a test error naming no file. An absent file is the test's own
    business, and says nothing here.
    """
    dictPathByCategory = dict(zip(
        ("dictIntegrity", "dictQualitative", "dictQuantitative"),
        standardsFiles.flistStepStandardsPaths(sStepDirectory),
    ))
    sPath = dictPathByCategory.get(sCategory, "")
    if not sPath:
        return ""
    try:
        baContent = connectionDocker.fbaFetchFile(sContainerId, sPath)
    except (FileNotFoundError, OSError):
        return ""
    if not isinstance(baContent, bytes):
        return ""
    return standardsFiles.fsDescribeInvalidStandardsText(
        baContent.decode("utf-8", errors="replace"), sPath,
    )


def _fdictStandardsErrorResult(sReason):
    """Return the run result for a category refused by its standards file."""
    return {
        "bPassed": False, "sOutput": sReason, "sStdout": "",
        "sStderr": sReason, "iExitCode": 1,
    }


def _fdictRunOneTestCategory(
    dictCtx, sContainerId, sDirectory, listCommands, sWorkflowSlug="",
    sCategory="",
):
    """Execute one group's commands and return its result dict.

    Synchronous because it runs inside a mode-(b) carrier worker, which
    the carrier calls in a thread; the ``asyncio.to_thread`` this used
    to wrap the exec in is what the carrier now owns.
    """
    sCatCmd = " && ".join(
        [f"cd {fsShellQuote(sDirectory)}"] + list(listCommands))
    sCatCmd = _fsPrefixWithTestCategoryEnv(sCatCmd, sCategory)
    sCatCmd = fsBuildTestExecutionPrefix(
        dictCtx["docker"], sContainerId,
        dictCtx["workflows"].get(sContainerId),
    ) + _fsPrefixWithWorkflowEnv(sCatCmd, sWorkflowSlug)
    tExecResult = dictCtx["docker"].ftRunInContainerStreamed(
        sContainerId, sCatCmd,
    )
    return {
        "bPassed": tExecResult.iExitCode == 0,
        "sOutput": tExecResult.sStdout + tExecResult.sStderr,
        "sStdout": tExecResult.sStdout,
        "sStderr": tExecResult.sStderr,
        "iExitCode": tExecResult.iExitCode,
    }


async def _ftProbeLevelThenRunUnderTheDrain(
    dictCtx, sContainerId, dictWorkflow, requestHttp, sTarget,
    fnRunTheTests,
):
    """Return ``(iLevelBefore, result)`` from ONE lock-held worker.

    The PROOF level probe runs INSIDE the worker, beside the run it
    brackets, rather than on the request coroutine before it — and that
    placement is the point of this helper rather than tidiness. The
    probe asks only whether the project is at Level 1
    (``fiComputeProofLevelUpToOne``), because the archive it feeds acts
    only on a crossing of 1; it brackets the run, so the before and
    after readings must see the same world. It was once the full
    ladder, which reached the general exec primitive as soon as a
    workflow was L2 (``fbWorkflowFullySyncedWithArxiv`` hashes every
    Overleaf-pushed figure through ``filesRepo.fdictHashFiles``) -- a
    probe left outside the carrier was then admitted on every fixture
    below L2 and REFUSED in the field, for exactly the researchers
    furthest along the ladder. Keep any level read here inside.

    Running the test commands under the drain is the ordinary mode-(b)
    reason: they are project-authored, can run for minutes, and cross a
    worker-thread boundary, so a hand-over arriving mid-run would
    otherwise see an idle container and commit while the former owner's
    pytest kept writing into the workspace.

    The test-support files are refreshed UNCACHED first, as a pipeline
    run does: the marker plugin is git-ignored, so a ``git clean``
    removes it, and a run without it writes no marker -- the poll then
    keeps showing the previous run's verdict, a pass over a failure. A
    refresh that cannot land refuses the run, carried back as a value
    so the container is not quarantined over a file it could not write.
    """
    from .. import commitCarrier
    from ..pipelineRunner import _flistRefreshConftestsBeforeRun
    dictLaneTuple = fdictRequireLaneTupleForCommit(
        requestHttp, sContainerId, sTarget,
    )

    def ftProbeThenRun(supervisor=None):
        del supervisor
        iLevelBefore = fiComputeProofLevelUpToOne(
            dictWorkflow,
            ffilesForWorkflow(dictCtx, sContainerId, dictWorkflow),
        )

        def ftRunUnlessUnrecordable():
            _fnRefuseAnExecutionProfileNotKnownHere(dictWorkflow)
            _fnRefuseATestRunNoMarkerWouldRecord(
                _flistRefreshConftestsBeforeRun(
                    dictCtx["docker"], sContainerId, dictWorkflow,
                ),
            )
            return (iLevelBefore, fnRunTheTests())
        return fdictCarryARefusalBackInsteadOfRaising(ftRunUnlessUnrecordable)

    dictOutcome = await commitCarrier.fdictRunLockHeldMutation(
        requestHttp.app.state, dictLaneTuple["sContainerName"],
        sContainerId, dictLaneTuple, "helper", sTarget, ftProbeThenRun,
    )
    dictCarried = dictOutcome["result"]
    if dictCarried["errorRefused"] is not None:
        raise dictCarried["errorRefused"]
    return dictCarried["objResult"]


def _fnRefuseAnExecutionProfileNotKnownHere(dictWorkflow):
    """Raise 409 when the workflow asks for a profile this vaibify lacks.

    Running the tests under some other settings would answer a question
    the researcher did not ask, so nothing runs and the reason says why.
    """
    from vaibify.reproducibility import executionProfiles
    try:
        executionProfiles.fdictWorkflowExecutionProfile(dictWorkflow)
    except executionProfiles.ExecutionProfileError as error:
        raise HTTPException(409, str(error)) from error


def _fnRefuseATestRunNoMarkerWouldRecord(listRefreshFailures):
    """Raise 409 when the test-support files could not be made current.

    Their marker is how the dashboard learns a run's result, so a run
    without them would answer in this response and leave the PREVIOUS
    run's verdict on every later poll. Refusing before pytest starts
    is the honest answer: nothing ran, and the reason names the files.
    """
    if not listRefreshFailures:
        return
    from ..pipelineTestRunner import fsDescribeUnrecordableTestRun
    raise HTTPException(
        409, fsDescribeUnrecordableTestRun(listRefreshFailures),
    )


async def _fnAutoArchiveUnderTheDrain(
    dictCtx, sContainerId, dictWorkflow, iStepIndex, iLevelBefore,
    requestHttp,
):
    """Run the post-run auto-archive under its own lock-held carrier.

    A second carrier invocation rather than a continuation of the run's
    worker, because the workflow save that records the result commits
    between them through mode (a), and folding the archive into the run
    would put a remote push inside the same drain as the pytest that
    justified it.

    Mode (b) rather than mode (c): ``fbMaybeAutoArchive`` re-reads the
    PROOF level (the same general exec as the pre-run probe) and then
    writes the L3 envelope and pushes to Overleaf and Zenodo, so it
    mutates and can run long — but it is awaited in the handler today,
    and making it durable would change what the researcher sees when
    the response arrives.
    """
    from .. import commitCarrier
    dictLaneTuple = fdictRequireLaneTupleForCommit(
        requestHttp, sContainerId, "Archiving the step's verified files",
    )

    def fbArchive(supervisor=None):
        del supervisor
        return fbMaybeAutoArchive(
            dictCtx["docker"], sContainerId, dictWorkflow,
            iStepIndex, iLevelBefore,
        )

    await commitCarrier.fdictRunLockHeldMutation(
        requestHttp.app.state, dictLaneTuple["sContainerName"],
        sContainerId, dictLaneTuple, "helper", "auto-archive", fbArchive,
    )


def _fdictCommitTestDirectoryRemoval(
    dictCtx, sContainerId, dictWorkflow, dictStep, requestHttp,
):
    """Delete the step's ``tests`` directory through carrier mode (a).

    Mode (a) rather than (b), matching the draft DELETE this most
    resembles: the effect is ONE ``rm -rf`` and it already ran on the
    request coroutine, so a mode-(b) supervisor would move it into a
    thread — a concurrency change this migration has no reason to make.
    What changes is only that the removal now carries an admission,
    where before it reached the exec primitive directly.

    The kind is ``helper`` rather than ``file-write``: a file-write
    record is probed by comparing the target's sha256 against an
    expected and a prior hash, and a removed DIRECTORY has neither, so
    recording it as one would hand the probe a question it cannot
    answer. The helper probe asks instead whether this hub process is
    still alive, which is the honest question about an ``rm`` issued
    from it.
    """
    from .. import commitCarrier
    dictLaneTuple = fdictRequireLaneTupleForCommit(
        requestHttp, sContainerId, "Deleting the step's generated tests",
    )
    return commitCarrier.fdictCommitSynchronousMutation(
        requestHttp.app.state, dictLaneTuple["sContainerName"],
        sContainerId, dictLaneTuple, "helper", "delete-generated-tests",
        lambda: _fnRemoveTestDirectory(
            dictCtx["docker"], sContainerId, dictStep,
            sProjectRepoPath=dictWorkflow.get("sProjectRepoPath", ""),
        ),
        {
            "iHolderPid": os.getpid(),
            "iHolderProcessGroup": os.getpgrp(),
        },
    )


class _DeterministicGenerateRequest:
    """The generator's request, with the LLM lane structurally absent.

    Not a Pydantic model and not parsed from the body: it is
    CONSTRUCTED by the handler with the flags fixed. A model that
    accepted ``bUseApi`` and merely defaulted it False would let an
    agent set it, and the whole point of the agent-safe generator is
    that it cannot reach the LLM branch. Forcing at construction means
    the refusal is a property of the type rather than of a validator
    somebody can later relax.

    ``bForceOverwrite`` is fixed False for the same reason: an agent
    must not silently replace a test file the researcher edited. The
    generator answers ``bNeedsOverwriteConfirm`` instead, and the agent
    has to bring that back to them.
    """

    bUseApi = False
    sApiKey = None
    bDeterministic = True
    bForceOverwrite = False


def _fsetValidateGenerateCategories(sCategory):
    """Return the category set to generate, or raise 400.

    An absent category means all three -- the same default the
    researcher's button has always had. A named one must be spelled
    the way every other lane spells it.
    """
    if not sCategory:
        return set(T_GENERATED_TEST_CATEGORIES)
    if sCategory not in T_GENERATED_TEST_CATEGORIES:
        raise HTTPException(
            400,
            f"Unknown test category: {sCategory}. Expected one of "
            + ", ".join(T_GENERATED_TEST_CATEGORIES)
            + ", or omit sCategory for all three.",
        )
    return {sCategory}


def _fnRegisterDeterministicGenerate(app, dictCtx):
    """Register POST /api/steps/{id}/{step}/generate-test-deterministic.

    The agent-safe generator. ``generate-tests`` stays user-only
    because it is TWO things behind one path -- deterministic
    introspection, and an LLM round-trip selected when neither
    ``bDeterministic`` nor ``bUseApi`` is set -- and the researcher
    must review AI-authored test content. That marking left an
    inversion: an agent hand-writing assertions through
    ``save-and-run-test`` was permitted, while vaibify DERIVING the
    same assertions mechanically from the researcher's own files was
    not. The unreviewed path was open and the mechanical one closed.

    This route is the mechanical one alone. It grants no capability the
    agent lacked -- it could already write a test file and declare it
    -- but it makes the grounded version reachable, so an agent asked
    for qualitative tests can pin the columns and keys that are
    actually in the data instead of the ones it believed were.
    """

    @ffnAgentAction("generate-tests-deterministic")
    @app.post(
        "/api/steps/{sContainerId}/{iStepIndex}"
        "/generate-test-deterministic"
    )
    @ffnDeclareCarrierMode(
        S_CARRIER_MODE_B_LOCK_HELD, S_CARRIER_MODE_A_SYNCHRONOUS,
    )
    async def fdictHandleGenerateDeterministic(
        sContainerId: str, iStepIndex: int,
        request: TestGenerateCategoryRequest, requestHttp: Request,
    ):
        dictCtx["require"](sContainerId)
        from ..testGenerator import fdictGenerateAllTestsDeterministic
        setCategories = _fsetValidateGenerateCategories(request.sCategory)
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId
        )
        _fnRequireStepIndexBeforeGenerating(dictWorkflow, iStepIndex)
        await _fnRefuseGenerationWithoutOutputs(
            dictCtx, sContainerId, dictWorkflow, iStepIndex, requestHttp,
        )

        def fdictGenerateTheCategories(
            connectionDocker, sCid, iStep, dictFlow, dictVars,
            bUseApi, sApiKey, sUser=None, bDeterministic=True,
            bForceOverwrite=False,
        ):
            del bUseApi, sApiKey, sUser, bDeterministic, bForceOverwrite
            return fdictGenerateAllTestsDeterministic(
                connectionDocker, sCid, iStep, dictFlow, dictVars,
                bForceOverwrite=False, setCategories=setCategories,
            )

        dictResult = await _fdictRunTestGeneration(
            dictCtx, sContainerId, iStepIndex, dictWorkflow,
            fdictGenerateTheCategories, _DeterministicGenerateRequest(),
            requestHttp, None,
        )
        if dictResult.get("bNeedsOverwriteConfirm"):
            return {
                "bNeedsOverwriteConfirm": True,
                "listModifiedFiles": dictResult.get(
                    "listModifiedFiles", []
                ),
            }
        _fnRemoveFilesOfInapplicableCategories(
            dictCtx, sContainerId, dictResult, requestHttp,
        )
        _fnApplyGeneratedTests(
            dictCtx, sContainerId, dictWorkflow,
            iStepIndex, dictResult, requestHttp,
        )
        return _fdictBuildGenerateResponse(
            dictResult, dictWorkflow, iStepIndex,
        )


def _fnRegisterTestGenerate(app, dictCtx):
    """Register test generation and deletion routes."""

    @ffnAgentAction("generate-tests")
    @app.post(
        "/api/steps/{sContainerId}/{iStepIndex}/generate-test"
    )
    @ffnDeclareCarrierMode(
        S_CARRIER_MODE_B_LOCK_HELD, S_CARRIER_MODE_A_SYNCHRONOUS,
    )
    async def fdictHandleGenerateTest(
        sContainerId: str, iStepIndex: int,
        request: TestGenerateRequest, requestHttp: Request,
    ):
        dictCtx["require"](sContainerId)
        from ..testGenerator import fdictGenerateAllTests
        if _fbNeedsClaudeFallback(
            dictCtx, sContainerId, request
        ):
            return {"bNeedsFallback": True}
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId
        )
        _fnRequireStepIndexBeforeGenerating(dictWorkflow, iStepIndex)
        await _fnRefuseGenerationWithoutOutputs(
            dictCtx, sContainerId, dictWorkflow, iStepIndex, requestHttp,
        )
        sResolvedApiKey = (
            _fsRequireConfiguredProviderKey() if request.bUseApi else None
        )
        dictResult = await _fdictRunTestGeneration(
            dictCtx, sContainerId, iStepIndex,
            dictWorkflow, fdictGenerateAllTests, request, requestHttp,
            sResolvedApiKey,
        )
        if dictResult.get("bNeedsOverwriteConfirm"):
            return {
                "bNeedsOverwriteConfirm": True,
                "listModifiedFiles": dictResult.get(
                    "listModifiedFiles", []
                ),
            }
        _fnRemoveFilesOfInapplicableCategories(
            dictCtx, sContainerId, dictResult, requestHttp,
        )
        _fnApplyGeneratedTests(
            dictCtx, sContainerId, dictWorkflow,
            iStepIndex, dictResult, requestHttp,
        )
        return _fdictBuildGenerateResponse(
            dictResult, dictWorkflow, iStepIndex,
        )

    @app.get("/api/provider-key/{sProvider}")
    async def fdictReportProviderKeyStatus(
        sProvider: str, requestHttp: Request,
    ):
        """Report whether the host keyring holds this provider's API key.

        Browser-only capability route (agent-council design 9.5): it
        answers ``bConfigured`` and the exact host command, never the
        key value, and the in-container agent lane is refused because
        the answer is host state.
        """
        fnRejectAgentTokenLane(requestHttp)
        from vaibify.config.secretManager import fbSecretExists
        from ..providerApiTransport import (
            SET_SUPPORTED_PROVIDERS,
            fsProviderKeySlotName,
        )
        if sProvider not in SET_SUPPORTED_PROVIDERS:
            raise HTTPException(404, f"Unknown provider: {sProvider}")
        return {
            "bConfigured": fbSecretExists(
                fsProviderKeySlotName(sProvider), "keyring",
            ),
            "sConfigureCommand": (
                "vaibify secret set-provider-key --provider " + sProvider
            ),
        }

    @ffnAgentAction("delete-generated-tests")
    @app.delete(
        "/api/steps/{sContainerId}/{iStepIndex}/generated-test"
    )
    @ffnDeclareCarrierMode(S_CARRIER_MODE_A_SYNCHRONOUS)
    async def fdictDeleteGeneratedTest(
        sContainerId: str, iStepIndex: int, requestHttp: Request,
    ):
        dictCtx["require"](sContainerId)
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId
        )
        dictStep = dictWorkflow["listSteps"][iStepIndex]
        _fdictCommitTestDirectoryRemoval(
            dictCtx, sContainerId, dictWorkflow, dictStep, requestHttp,
        )
        dictStep["dictTests"] = {
            "dictQualitative": {
                "saCommands": [], "sFilePath": "",
            },
            "dictQuantitative": {
                "saCommands": [],
                "sFilePath": "",
                "sStandardsPath": "",
            },
            "dictIntegrity": {
                "saCommands": [], "sFilePath": "",
            },
            "listUserTests": [],
        }
        dictStep["saTestCommands"] = []
        dictVerification = dictStep.setdefault(
            "dictVerification", {})
        dictVerification["sUnitTest"] = "untested"
        dictVerification["sQualitative"] = "untested"
        dictVerification["sQuantitative"] = "untested"
        dictVerification["sIntegrity"] = "untested"
        fbDeriveUnnecessaryVerification(dictWorkflow)
        fdictCommitWorkflowSave(
            dictCtx, sContainerId, dictWorkflow, requestHttp,
            "Recording the deleted tests",
        )
        return {"bSuccess": True}


def _fsResolveTestFilePath(sFilePath, sProjectRepoPath, sProjectRoot):
    """Return the validated container-absolute path for a test-file edit.

    ``save-and-run-test`` is agent-safe and takes a caller-supplied
    path, so it needs the same two guards the generic write route has
    always had: containment in the project repo, and the write denylist
    that keeps ``.git/hooks/`` (code execution on the next commit) and
    ``.vaibify/`` (the metadata-integrity contract) out of reach. The
    blast radius is the container's ``/workspace`` volume rather than
    the host, which is why this was easy to miss.

    Repo-relative inputs are joined onto the project repo, matching the
    paths ``testGenerator`` writes; the project's own root is the
    fallback for a workflow that has no detected repo — the container
    volume for a container project, the registered directory for a
    host one.
    """
    sRoot = sProjectRepoPath or sProjectRoot
    sCandidate = (
        sFilePath if sFilePath.startswith("/")
        else posixpath.join(sRoot, sFilePath)
    )
    sNormalized = fsValidatePathWithinRoot(sCandidate, sRoot)
    fnRejectWriteDenylistedPath(sNormalized, sRoot)
    return sNormalized


def _fnPersistTestEdit(
    connectionDocker, sContainerId, sFilePath, sContent, sAuthorizedRoot,
):
    """Write the edited test file back to the container filesystem.

    The write itself enforces the root and the denylist, so a symlink
    swapped in after the lexical check cannot redirect it.
    """
    try:
        connectionDocker.fnWriteFile(
            sContainerId, sFilePath, sContent.encode("utf-8"),
            **fdictConfinedWriteKeywords(sAuthorizedRoot),
        )
    except ContainerWriteRefusedError as error:
        raise HTTPException(403, str(error))


def _ftRunSaveAndRunTest(
    connectionDocker, sContainerId, dictStep, dictWorkflow, sFilePath,
):
    """Write the edited test file, then run it; return the exec result.

    The write and the run are one step deliberately, and both happen
    inside the caller's lock-held worker: a hand-over landing between
    them would leave the FORMER owner's pytest reporting on a file the
    SUCCESSOR now owns, and the reported result would be attributed to
    the wrong content.
    """
    sTestCmd = _fsBuildPytestCommand(
        _fsAbsoluteStepWorkdir(
            dictStep, dictWorkflow.get("sProjectRepoPath", ""),
        ),
        sFilePath,
    )
    sTestCmd = fsBuildTestExecutionPrefix(
        connectionDocker, sContainerId, dictWorkflow,
    ) + _fsPrefixWithWorkflowEnv(
        sTestCmd,
        fsWorkflowSlugFromPath(
            workflowManager.fsWorkflowLoadedFromPath(dictWorkflow)),
    )
    return connectionDocker.ftRunInContainerStreamed(
        sContainerId, sTestCmd,
    )


def _fdictBuildSaveRunResponse(bPassed, tExecResult):
    """Return the JSON response body for save-and-run-test."""
    return {
        "bPassed": bPassed,
        "sOutput": tExecResult.sStdout + tExecResult.sStderr,
        "sStdout": tExecResult.sStdout,
        "sStderr": tExecResult.sStderr,
        "iExitCode": tExecResult.iExitCode,
    }


# Category name (request body) -> (dictTests key, dictVerification key).
_DICT_TEST_CATEGORY_KEYS = {
    "integrity": ("dictIntegrity", "sIntegrity"),
    "qualitative": ("dictQualitative", "sQualitative"),
    "quantitative": ("dictQuantitative", "sQuantitative"),
}


def _ftResolveCategoryKeys(sCategory):
    """Return ``(sDictKey, sVerifKey)`` or raise HTTP 400 for an unknown name."""
    if sCategory not in _DICT_TEST_CATEGORY_KEYS:
        raise HTTPException(400, f"Unknown category: {sCategory}")
    return _DICT_TEST_CATEGORY_KEYS[sCategory]


def _ftRequireCategoryCommands(dictStep, sDictKey, sCategory):
    """Return ``(listCmds, dictCat)`` or raise HTTP 400 when no commands exist."""
    dictCat = dictStep.get("dictTests", {}).get(sDictKey, {})
    listCmds = dictCat.get("saCommands", [])
    if not listCmds:
        raise HTTPException(
            400, f"No commands for category: {sCategory}",
        )
    return listCmds, dictCat


def _ftResolveCategoryContext(
    dictCtx, sContainerId, iStepIndex, sCategory,
):
    """Resolve workflow, step, category and commands for a request.

    Returns ``(dictWorkflow, dictStep, dictCat, listCmds, sVerifKey)``.
    All HTTP errors raise here, on the request coroutine, so the handler
    body stays linear AND so no expected 4xx can be raised from inside a
    carrier worker — a worker that raises settles through the failure
    path, which marks the container as needing reconciliation, and an
    unknown category name is not a reason to take a researcher's
    container out of service.

    The pre-run PROOF level is deliberately NOT probed here any more: it
    reaches a general exec on an L2 workflow, so it belongs inside the
    carrier worker with the run it brackets.
    """
    sDictKey, sVerifKey = _ftResolveCategoryKeys(sCategory)
    dictWorkflow = fdictRequireWorkflow(
        dictCtx["workflows"], sContainerId,
    )
    dictStep = dictWorkflow["listSteps"][iStepIndex]
    listCmds, dictCat = _ftRequireCategoryCommands(
        dictStep, sDictKey, sCategory,
    )
    return (dictWorkflow, dictStep, dictCat, listCmds, sVerifKey)


def _fsBuildCategoryCommand(dictStep, dictWorkflow, listCmds, sCategory=""):
    """Build the cd + && joined category command with its env prefixes."""
    sDir = _fsAbsoluteStepWorkdir(
        dictStep, dictWorkflow.get("sProjectRepoPath", ""),
    )
    sFullCmd = " && ".join([f"cd {fsShellQuote(sDir)}"] + listCmds)
    return _fsPrefixWithWorkflowEnv(
        _fsPrefixWithTestCategoryEnv(sFullCmd, sCategory),
        fsWorkflowSlugFromPath(
            workflowManager.fsWorkflowLoadedFromPath(dictWorkflow)),
    )


def _ftRunCategoryCommands(
    connectionDocker, sContainerId, dictStep, dictWorkflow, listCmds,
    sCategory="",
):
    """Run the category commands; return (tExecResult, bPassed, sOutput).

    Synchronous: it runs inside a mode-(b) carrier worker, which the
    carrier already calls in a thread.
    """
    sFullCmd = fsBuildTestExecutionPrefix(
        connectionDocker, sContainerId, dictWorkflow,
    ) + _fsBuildCategoryCommand(dictStep, dictWorkflow, listCmds, sCategory)
    tExecResult = connectionDocker.ftRunInContainerStreamed(
        sContainerId, sFullCmd,
    )
    bPassed = tExecResult.iExitCode == 0
    sOutput = tExecResult.sStdout + tExecResult.sStderr
    return tExecResult, bPassed, sOutput


def _fnRecordCategoryOutcome(
    dictStep, dictCat, sVerifKey, bPassed, sOutput,
):
    """Update dictVerification + aggregate state after a category run."""
    from .. import truthDerivation
    dictVerification = dictStep.setdefault("dictVerification", {})
    iExitCode = 0 if bPassed else 1
    dictVerification[sVerifKey] = (
        truthDerivation.fsResolveUnitTestFromExitCode(iExitCode))
    dictVerification.pop("listModifiedFiles", None)
    dictVerification.pop("bUpstreamModified", None)
    if sOutput:
        dictCat["sLastOutput"] = sOutput
    _fnUpdateAggregateTestState(dictStep)


def _fdictBuildRunCategoryResponse(bPassed, tExecResult):
    """Return the JSON response body for run-test-category."""
    return {
        "bPassed": bPassed,
        "sOutput": tExecResult.sStdout + tExecResult.sStderr,
        "sStdout": tExecResult.sStdout,
        "sStderr": tExecResult.sStderr,
        "iExitCode": tExecResult.iExitCode,
    }


def _fnRegisterTestSaveAndRun(app, dictCtx):
    """Register POST /api/steps/{id}/{step}/save-and-run-test."""

    @ffnAgentAction("save-and-run-test")
    @app.post(
        "/api/steps/{sContainerId}/{iStepIndex}"
        "/save-and-run-test"
    )
    @ffnDeclareCarrierMode(
        S_CARRIER_MODE_B_LOCK_HELD, S_CARRIER_MODE_A_SYNCHRONOUS,
    )
    async def fdictSaveAndRunTest(
        sContainerId: str, iStepIndex: int,
        request: SaveAndRunTestRequest,
        requestHttp: Request,
    ):
        dictCtx["require"](sContainerId)
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        dictStep = dictWorkflow["listSteps"][iStepIndex]
        sProjectRepoPath = dictWorkflow.get("sProjectRepoPath", "")
        sProjectRoot = projectRoots.fsResolveProjectRoot(
            sContainerId, WORKSPACE_ROOT,
        )
        sFilePath = _fsResolveTestFilePath(
            request.sFilePath, sProjectRepoPath, sProjectRoot,
        )
        standardsFiles.fnRefuseInvalidStandardsWrite(
            sFilePath, request.sContent,
        )

        def ftWriteThenRunTheTest():
            _fnPersistTestEdit(
                dictCtx["docker"], sContainerId,
                sFilePath, request.sContent,
                sProjectRepoPath or sProjectRoot,
            )
            return _ftRunSaveAndRunTest(
                dictCtx["docker"], sContainerId, dictStep,
                dictWorkflow, sFilePath,
            )

        iLevelBefore, tExecResult = await _ftProbeLevelThenRunUnderTheDrain(
            dictCtx, sContainerId, dictWorkflow, requestHttp,
            "save-and-run-test", ftWriteThenRunTheTest,
        )
        bPassed = tExecResult.iExitCode == 0
        _fnRecordTestResult(dictStep, bPassed, dictWorkflow, iStepIndex)
        _fnRegisterTestCommand(
            dictStep, bPassed, sFilePath, sProjectRepoPath or sProjectRoot,
        )
        fdictCommitWorkflowSave(
            dictCtx, sContainerId, dictWorkflow, requestHttp,
            "Recording the test result",
        )
        await _fnAutoArchiveUnderTheDrain(
            dictCtx, sContainerId, dictWorkflow, iStepIndex,
            iLevelBefore, requestHttp,
        )
        return _fdictBuildSaveRunResponse(bPassed, tExecResult)


def _fnRegisterTestRun(app, dictCtx):
    """Register POST /api/steps/{id}/{step}/run-tests."""

    @ffnAgentAction("run-unit-tests")
    @app.post(
        "/api/steps/{sContainerId}/{iStepIndex}/run-tests"
    )
    @ffnDeclareCarrierMode(
        S_CARRIER_MODE_B_LOCK_HELD, S_CARRIER_MODE_A_SYNCHRONOUS,
    )
    async def fdictHandleRunTests(
        sContainerId: str, iStepIndex: int,
        requestHttp: Request,
    ):
        from ..workflowManager import flistBuildTestCommands
        dictCtx["require"](sContainerId)
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        dictStep = dictWorkflow["listSteps"][iStepIndex]
        listCmds = _flistResolveTestCommands(dictStep)
        if not listCmds:
            raise HTTPException(400, "No test commands")

        def fdictRunEveryCategory():
            return _fdictRunAllTestCategories(
                dictCtx, sContainerId, dictStep,
                sRepoRoot=dictWorkflow.get("sProjectRepoPath", ""),
                sWorkflowSlug=fsWorkflowSlugFromPath(
                    workflowManager.fsWorkflowLoadedFromPath(
                        dictWorkflow)),
            )

        (iLevelBefore, dictCategoryResults) = (
            await _ftProbeLevelThenRunUnderTheDrain(
                dictCtx, sContainerId, dictWorkflow, requestHttp,
                "run-tests", fdictRunEveryCategory,
            )
        )
        if not dictCategoryResults:
            raise HTTPException(
                500,
                "Test run executed no commands; refusing to record a "
                "result. The step resolved test commands but no test "
                "group ran, which is a bug — report it rather than "
                "trusting this step's verification state.",
            )
        bAllPassed = all(
            d["bPassed"] for d in dictCategoryResults.values()
        )
        _fnRecordTestResult(
            dictStep, bAllPassed, dictWorkflow, iStepIndex)
        fdictCommitWorkflowSave(
            dictCtx, sContainerId, dictWorkflow, requestHttp,
            "Recording the test results",
        )
        await _fnAutoArchiveUnderTheDrain(
            dictCtx, sContainerId, dictWorkflow, iStepIndex,
            iLevelBefore, requestHttp,
        )
        return _fdictBuildTestResponse(
            bAllPassed, dictCategoryResults)

    @ffnAgentAction("run-test-category")
    @app.post(
        "/api/steps/{sContainerId}/{iStepIndex}"
        "/run-test-category"
    )
    @ffnDeclareCarrierMode(
        S_CARRIER_MODE_B_LOCK_HELD, S_CARRIER_MODE_A_SYNCHRONOUS,
    )
    async def fdictRunTestCategory(
        sContainerId: str, iStepIndex: int,
        request: Request,
    ):
        dictCtx["require"](sContainerId)
        sCategory = (await request.json()).get("sCategory", "")
        (dictWorkflow, dictStep, dictCat, listCmds,
         sVerifKey) = _ftResolveCategoryContext(
            dictCtx, sContainerId, iStepIndex, sCategory,
        )

        sStepDirectory = _fsAbsoluteStepWorkdir(
            dictStep, dictWorkflow.get("sProjectRepoPath", ""),
        )

        def ftRunTheCategory():
            sInvalid = fsInvalidStandardsReason(
                dictCtx["docker"], sContainerId, sStepDirectory,
                _DICT_TEST_CATEGORY_KEYS[sCategory][0],
            )
            if sInvalid:
                return None, False, sInvalid
            return _ftRunCategoryCommands(
                dictCtx["docker"], sContainerId, dictStep,
                dictWorkflow, listCmds, sCategory,
            )

        iLevelBefore, tRun = await _ftProbeLevelThenRunUnderTheDrain(
            dictCtx, sContainerId, dictWorkflow, request,
            "run-test-category", ftRunTheCategory,
        )
        tExecResult, bPassed, sOutput = tRun
        if tExecResult is None:
            dictStep.setdefault("dictVerification", {})[sVerifKey] = "error"
            dictCat["sLastOutput"] = sOutput
            _fnUpdateAggregateTestState(dictStep)
        else:
            _fnRecordCategoryOutcome(
                dictStep, dictCat, sVerifKey, bPassed, sOutput,
            )
        workflowManager.fnStampFieldProducer(
            dictWorkflow, dictStep, "dictVerification",
        )
        fdictCommitWorkflowSave(
            dictCtx, sContainerId, dictWorkflow, request,
            "Recording the category test result",
        )
        await _fnAutoArchiveUnderTheDrain(
            dictCtx, sContainerId, dictWorkflow, iStepIndex,
            iLevelBefore, request,
        )
        if tExecResult is None:
            return _fdictStandardsErrorResult(sOutput)
        return _fdictBuildRunCategoryResponse(bPassed, tExecResult)


def fnRegisterAll(app, dictCtx):
    """Register all test management routes."""
    _fnRegisterDeterministicGenerate(app, dictCtx)
    _fnRegisterTestGenerate(app, dictCtx)
    _fnRegisterTestSaveAndRun(app, dictCtx)
    _fnRegisterTestRun(app, dictCtx)
