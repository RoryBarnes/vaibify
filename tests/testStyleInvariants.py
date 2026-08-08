"""Style-contract invariants: the Hungarian-notation rules as tests.

The style guide's naming contract was prose until 2026-08-05; these
tests make it an enforced architectural invariant in the pattern of
testArchitecturalInvariants.py and the mutation-inventory drift check.
The scanner lives in tools/generateStyleInventory.py and is imported
in-process (never a subprocess), so the ordinary pytest lanes enforce
the contract as the rot-resistant backbone, and the dedicated
`.github/workflows/styleContract.yml` lane gives every merge a named,
requirable `style-contract` status on top.

Ratchet mechanics, and why there are two of them:

* The FROZEN SEED below is the original debt census as (debt class,
  identity) pairs, written once when the suite landed and never edited
  afterward except by explicit reviewed decision. Every current
  inventory row must be a member, so a NEW violation cannot enter.
* The seed alone cannot ratchet history -- it permanently admits every
  original pair, so a fixed function that later regressed would be
  seed-legal. The EXACT BUDGETS close that hole: each I_*_BUDGET
  records the current debt count per class and the test fails when the
  scan differs in EITHER direction. Above means a seeded violation was
  reintroduced; below means a fix was made without recording the gain.
  Every burn-down commit lowers the matching constant.

The prefix vocabulary here is an INDEPENDENT COPY of the one in the
tool; growing either tier takes both edits plus a ruling.
"""

import ast
import importlib.util
import json
import pathlib

import pytest

__all__ = [
    "testFunctionNamesCarryReturnTypePrefix",
    "testFnPrefixedFunctionsReturnNoValue",
    "testYieldingFunctionsCarryIterOrContext",
    "testLiteralReturnsMatchThePrefix",
    "testReturnAnnotationsMatchThePrefix",
    "testPrefixedNamesAgreeWithAnnotations",
    "testVariableBindingsCarryCastPrefixes",
    "testPrefixVocabularyIsClosed",
    "testCurrentInventoryIsWithinTheFrozenSeed",
    "testInterfaceExemptionsNameForeignProtocols",
    "testDebtCountsEqualTheRecordedBudgets",
    "testInventoryFileMatchesRegeneration",
    "testReviewTrackedMisnamingsStillExist",
    "testScannerCatchesFnReturningValue",
    "testScannerCatchesFnYielding",
    "testScannerCatchesLiteralReturnMismatch",
    "testScannerCatchesReturnAnnotationMismatch",
    "testScannerCatchesVariableAnnotationMismatch",
    "testScannerCatchesUnprefixedBinding",
    "testScannerCatchesBarePrefixlessFunction",
    "testScannerCatchesUnregisteredPrefix",
    "testScannerCatchesMisprefixedContextManager",
    "testScannerAcceptsCorrectContextManagerAnnotation",
    "testScannerRejectsContextManagerAnnotationOnTheGenerator",
    "testScannerFailsClosedOnUnparseableAnnotation",
    "testScannerDistinguishesSameNamedMethods",
    "testScannerParsesLongestPrefix",
]

PATH_REPOSITORY = pathlib.Path(__file__).resolve().parent.parent
PATH_TOOL = PATH_REPOSITORY / "tools" / "generateStyleInventory.py"
PATH_INVENTORY = PATH_REPOSITORY / "tests" / "styleInventory.json"


def _fmoduleLoadTool():
    """Load tools/generateStyleInventory.py without requiring sys.path."""
    spec = importlib.util.spec_from_file_location(
        "generateStyleInventory", PATH_TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tool = _fmoduleLoadTool()


# ---------------------------------------------------------------------------
# Independent copy of the two-tier vocabulary (testPrefixVocabularyIsClosed).
# Growing a tier is a ruling: edit BOTH this copy and the tool's.
# ---------------------------------------------------------------------------

DICT_TIER_ONE_AGREEMENT_COPY = {
    "n": set(),
    "fn": {"Callable"},
    "generic": set(),
    "b": {"bool"},
    "i": {"int"},
    "f": {"float"},
    "d": {"float"},
    "s": {"str"},
    "t": {"tuple", "Tuple"},
    "list": {"list", "List"},
    "dict": {"dict", "Dict"},
    "json": {"dict", "Dict", "list", "List", "str", "int", "float", "bool"},
    "ba": {"bytes", "bytearray"},
    "iter": {"Iterator", "Generator", "AsyncIterator", "AsyncGenerator",
             "Iterable"},
    "context": {"Iterator", "Generator", "AsyncIterator", "AsyncGenerator"},
    "da": {"list", "List", "ndarray"},
    "ia": {"list", "List", "ndarray"},
    "fa": {"list", "List", "ndarray"},
    "sa": {"list", "List"},
    "ta": {"list", "List"},
}

DICT_ARRAY_ELEMENT_CAST_COPY = {
    "da": {"float"},
    "ia": {"int"},
    "fa": {"float"},
    "sa": {"str"},
    "ta": {"tuple", "Tuple"},
}

DICT_TIER_TWO_REGISTRY_COPY = {
    "set": {"set", "Set", "frozenset"},
    "preflight": {"PreflightResult"},
    "config": {"ProjectConfig"},
    "path": {"Path"},
    "files": {"HostRepoFiles", "ContainerRepoFiles", "SnapshotRepoFiles"},
    "record": {"StartResultRecord", "StartTaskRecord", "OwnerRecord",
               "ConnectionRecord", "DurableTaskRecord",
               "TerminalExecutionRecord", "PoisonRecord",
               "BrowserSessionRecord"},
    "lock": {"Lock", "_RepoLockHolder"},
    "response": {"Response", "JSONResponse", "StreamingResponse",
                 "HTMLResponse", "PlainTextResponse", "FileResponse",
                 "RedirectResponse", "ValidateResponse"},
    "process": {"CompletedProcess", "Popen"},
    "parser": {"ArgumentParser"},
    "namespace": {"Namespace"},
    "thread": {"Thread"},
    "task": {"Task"},
    "request": {"Request"},  # suffix family: any *Request model agrees
    "httpresponse": {"HTTPResponse"},
    "error": {"Exception", "RuntimeError", "ValueError", "OSError"},
    "websocket": {"WebSocket"},
    "logger": {"Logger"},
    "listdict": {"list", "List"},
    "match": {"Match"},
    "reservation": {"StartReservation"},
    "supervisor": {"MutationSupervisor"},
    "session": {"TerminalSession"},
    "app": {"FastAPI"},
    "datetime": {"datetime"},
    "file": {"IO", "TextIO", "BinaryIO", "TextIOWrapper"},
    "token": {"Token"},
    "connection": {"DockerConnection"},
    "admission": {"MutationAdmission"},
    "module": {"ModuleType"},
    "container": {"Container"},
    "buffer": {"BytesIO"},
    "info": {"TarInfo"},
    "socket": {"SocketIO", "socket"},
    "deque": {"deque"},
    "identity": {"OwnershipIdentity"},
    "command": {"Command"},
    "docker": {"DockerClient"},
    "features": {"FeaturesConfig"},
    "repro": {"ReproducibilityConfig"},
    "overleaf": {"OverleafConfig"},
}


# ---------------------------------------------------------------------------
# Exact budgets: the current debt count per class. Fail above OR below.
# Lower the matching constant in the same commit as every burn-down.
# ---------------------------------------------------------------------------

I_LEGACY_NAME_BUDGET = 0
I_LEGACY_FN_RETURN_BUDGET = 0
I_LEGACY_YIELD_BUDGET = 0
I_LEGACY_LITERAL_RETURN_BUDGET = 0
I_LEGACY_RETURN_ANNOTATION_BUDGET = 0
I_LEGACY_ANNOTATION_MISMATCH_BUDGET = 0
I_LEGACY_VARIABLE_BUDGET = 388

DICT_BUDGETS = {
    "legacy-name": I_LEGACY_NAME_BUDGET,
    "legacy-fn-return": I_LEGACY_FN_RETURN_BUDGET,
    "legacy-yield": I_LEGACY_YIELD_BUDGET,
    "legacy-literal-return": I_LEGACY_LITERAL_RETURN_BUDGET,
    "legacy-return-annotation": I_LEGACY_RETURN_ANNOTATION_BUDGET,
    "legacy-annotation-mismatch": I_LEGACY_ANNOTATION_MISMATCH_BUDGET,
    "legacy-variable": I_LEGACY_VARIABLE_BUDGET,
}


# ---------------------------------------------------------------------------
# The frozen seed: the original census as "debtClass<TAB>identity" lines,
# written once at landing (2026-08-05). NEVER edit except by explicit
# reviewed decision; burn-down lowers budgets and regenerates the
# inventory but leaves this text alone.
# ---------------------------------------------------------------------------

S_FROZEN_SEED_TEXT = """\
cli-verb	vaibify/cli/actionCommands.py::do
cli-verb	vaibify/cli/commandBuild.py::build
cli-verb	vaibify/cli/commandCat.py::cat
cli-verb	vaibify/cli/commandConfig.py::config
cli-verb	vaibify/cli/commandConfig.py::configEdit
cli-verb	vaibify/cli/commandConfig.py::configExport
cli-verb	vaibify/cli/commandConfig.py::configImport
cli-verb	vaibify/cli/commandDestroy.py::destroy
cli-verb	vaibify/cli/commandDoctor.py::doctor
cli-verb	vaibify/cli/commandGenerateStandards.py::generate_standards
cli-verb	vaibify/cli/commandInit.py::init
cli-verb	vaibify/cli/commandLs.py::ls
cli-verb	vaibify/cli/commandOpen.py::open_container
cli-verb	vaibify/cli/commandPublish.py::publish
cli-verb	vaibify/cli/commandPublish.py::publishArchive
cli-verb	vaibify/cli/commandPublish.py::publishWorkflow
cli-verb	vaibify/cli/commandReconcile.py::reconcile
cli-verb	vaibify/cli/commandRegister.py::register
cli-verb	vaibify/cli/commandReproduce.py::reproduce
cli-verb	vaibify/cli/commandRevoke.py::revoke
cli-verb	vaibify/cli/commandRun.py::run
cli-verb	vaibify/cli/commandSessions.py::sessions
cli-verb	vaibify/cli/commandSessions.py::stop
cli-verb	vaibify/cli/commandStart.py::start
cli-verb	vaibify/cli/commandStatus.py::status
cli-verb	vaibify/cli/commandVerifyStep.py::verify_step
cli-verb	vaibify/cli/commandWorkflow.py::workflow
cli-verb	vaibify/cli/main.py::connect
cli-verb	vaibify/cli/main.py::gui
cli-verb	vaibify/cli/main.py::pull
cli-verb	vaibify/cli/main.py::push
cli-verb	vaibify/cli/main.py::setup
cli-verb	vaibify/cli/main.py::stop
cli-verb	vaibify/cli/main.py::verify
interface-method	vaibify/cli/main.py::_DefaultContainerIdFilter.filter
interface-method	vaibify/docker/dockerConnection.py::_BytesGeneratorPipe.read
interface-method	vaibify/gui/hostIncidents.py::HostIncidentHandler.emit
interface-method	vaibify/gui/routeContext.py::RouteContext.get
interface-method	vaibify/gui/routeContext.py::RouteContext.pop
interface-method	vaibify/gui/routeContext.py::RouteContext.setdefault
interface-method	vaibify/gui/routeScope.py::ContainerAwareRoute.get_route_handler
interface-method	vaibify/gui/serverMiddleware.py::ActivityTrackingMiddleware.dispatch
interface-method	vaibify/gui/serverMiddleware.py::SecurityHeadersMiddleware.dispatch
interface-method	vaibify/gui/serverMiddleware.py::SessionTokenMiddleware.dispatch
interface-method	vaibify/reproducibility/githubMirror.py::_AuthStrippingRedirectHandler.redirect_request
legacy-annotation-mismatch	vaibify/gui/commitCarrier.py::DurableTaskRecord.admission
legacy-annotation-mismatch	vaibify/gui/containerOwnership.py::ConnectionRecord.connection
legacy-annotation-mismatch	vaibify/gui/containerOwnership.py::OwnerRecord.fileHandleLock
legacy-annotation-mismatch	vaibify/gui/startReservation.py::StartReservation.identityOwnership
legacy-annotation-mismatch	vaibify/gui/terminalContainment.py::TerminalExecutionRecord.connectionDocker
legacy-annotation-mismatch	vaibify/gui/terminalContainment.py::TerminalExecutionRecord.dictRegistry
legacy-fn-return	vaibify/cli/actionCommands.py::fnCoerceFieldValue
legacy-fn-return	vaibify/cli/commandStart.py::_fnAcquireGuiSessionSlotOrExit
legacy-fn-return	vaibify/cli/commandStart.py::_fnAcquireProjectLockOrExit
legacy-fn-return	vaibify/cli/main.py::_fnAcquireHubSessionSlotOrExit
legacy-fn-return	vaibify/config/containerLock.py::fnAcquireContainerLock
legacy-fn-return	vaibify/config/registryManager.py::_fnOpenRegistryLock
legacy-fn-return	vaibify/config/secretManager.py::_fnLoadKeyringModule
legacy-fn-return	vaibify/config/sessionRegistry.py::fnAcquireSessionSlot
legacy-fn-return	vaibify/containerImage/vaibifyDo.py::_fnCoerceScalar
legacy-fn-return	vaibify/containerImage/vaibifyDo.py::_fnHandleHttpError
legacy-fn-return	vaibify/containerImage/vaibifyDo.py::_fnRecvExact
legacy-fn-return	vaibify/containerImage/vaibifyDo.py::_fnStreamWsEvents
legacy-fn-return	vaibify/containerImage/vaibifyDo.py::fnParseArguments
legacy-fn-return	vaibify/containerImage/vaibifyDo.py::fnRunWebsocket
legacy-fn-return	vaibify/containerImage/vaibifyDo.py::fnSendHttp
legacy-fn-return	vaibify/gui/actionCatalog.py::fnAgentAction
legacy-fn-return	vaibify/gui/actionCatalog.py::fnAgentAction._fnDecorator
legacy-fn-return	vaibify/gui/attributionLog.py::fnAppendFlag
legacy-fn-return	vaibify/gui/browserSession.py::fnRevokeSessionById
legacy-fn-return	vaibify/gui/buildRoutes.py::_fnRegisterBuildContainer.fnBuildContainer
legacy-fn-return	vaibify/gui/buildRoutes.py::_fnRegisterBuildProgress.fnGetBuildProgress
legacy-fn-return	vaibify/gui/commitCarrier.py::_fnBuildSupervisorEviction
legacy-fn-return	vaibify/gui/commitCarrier.py::_fnCallWorkerSynchronously
legacy-fn-return	vaibify/gui/conftestManager.py::fnWriteConftestMarkersBatch
legacy-fn-return	vaibify/gui/containerOwnership.py::_fnRecordNewOwner
legacy-fn-return	vaibify/gui/containerOwnership.py::fnReleaseOwnership
legacy-fn-return	vaibify/gui/diskSpace.py::fnCheckWorkspaceFreeBytes
legacy-fn-return	vaibify/gui/fileStatusManager.py::_fnArchiveZenodoForAutoArchive
legacy-fn-return	vaibify/gui/fileStatusManager.py::_fnPushOverleafForAutoArchive
legacy-fn-return	vaibify/gui/fileStatusManager.py::fnCollectMarkerPathsByStep
legacy-fn-return	vaibify/gui/fileStatusManager.py::fnCollectScriptPathsByStep
legacy-fn-return	vaibify/gui/fileStatusManager.py::fnMaybeAutoArchive
legacy-fn-return	vaibify/gui/fileStatusManager.py::fnSweepAllContainerCaches
legacy-fn-return	vaibify/gui/pipelineLogger.py::_fnEnsureLogsDirectory
legacy-fn-return	vaibify/gui/pipelineRunner.py::_fnRunOneStep
legacy-fn-return	vaibify/gui/pipelineRunner.py::_fnStartHeartbeatThread
legacy-fn-return	vaibify/gui/pipelineRunner.py::fnRunAllSteps
legacy-fn-return	vaibify/gui/pipelineRunner.py::fnRunFromStep
legacy-fn-return	vaibify/gui/pipelineRunner.py::fnRunSelectedSteps
legacy-fn-return	vaibify/gui/pipelineRunner.py::fnVerifyOnly
legacy-fn-return	vaibify/gui/pipelineServer.py::_fnRegisterLastResortExceptionHandler.fnHandleUnexpectedRouteException
legacy-fn-return	vaibify/gui/pipelineServer.py::_fnRegisterStaticFiles.fnBootstrapSession
legacy-fn-return	vaibify/gui/pipelineServer.py::_fnRegisterStaticFiles.fnRedeemTransferCapability
legacy-fn-return	vaibify/gui/pipelineServer.py::_fnRegisterStaticFiles.fnServeIndex
legacy-fn-return	vaibify/gui/pipelineServer.py::_ftBuildHelpers.fnFiles
legacy-fn-return	vaibify/gui/pipelineServer.py::_ftBuildHelpers.fnVariables
legacy-fn-return	vaibify/gui/pipelineServer.py::_ftBuildHelpers.fnWorkflowDir
legacy-fn-return	vaibify/gui/pipelineServer.py::fnPipelineMessageLoop.fnStartDispatchTask
legacy-fn-return	vaibify/gui/pipelineServer.py::fnValidatePathWithinRoot
legacy-fn-return	vaibify/gui/pipelineTestRunner.py::fnRunAllTests
legacy-fn-return	vaibify/gui/registryRoutes.py::_fnCreateHostFolder
legacy-fn-return	vaibify/gui/registryRoutes.py::_fnRegisterAddProject.fnAddProject
legacy-fn-return	vaibify/gui/registryRoutes.py::_fnRegisterContainerSettings.fnGetContainerSettings
legacy-fn-return	vaibify/gui/registryRoutes.py::_fnRegisterContainerSettings.fnSetContainerSettings
legacy-fn-return	vaibify/gui/registryRoutes.py::_fnRegisterCreateHostDirectory.fnCreateHostDirectory
legacy-fn-return	vaibify/gui/registryRoutes.py::_fnRegisterCreateProject.fnCreateProject
legacy-fn-return	vaibify/gui/registryRoutes.py::_fnRegisterGetRegistry.fnGetRegistry
legacy-fn-return	vaibify/gui/registryRoutes.py::_fnRegisterGetTemplateConfig.fnGetTemplateConfig
legacy-fn-return	vaibify/gui/registryRoutes.py::_fnRegisterGetTemplates.fnGetTemplates
legacy-fn-return	vaibify/gui/registryRoutes.py::_fnRegisterHostDirectories.fnGetHostDirectories
legacy-fn-return	vaibify/gui/registryRoutes.py::_fnRegisterRemoveProject.fnRemoveProject
legacy-fn-return	vaibify/gui/registryRoutes.py::_fnRegisterStartContainer.fnCancelStartContainer
legacy-fn-return	vaibify/gui/registryRoutes.py::_fnRegisterStartContainer.fnGetStartStatus
legacy-fn-return	vaibify/gui/registryRoutes.py::_fnRegisterStartContainer.fnStartContainer
legacy-fn-return	vaibify/gui/registryRoutes.py::_fnRegisterStopContainer.fnStopContainer
legacy-fn-return	vaibify/gui/routeScope.py::ContainerAwareRoute.get_route_handler.fnAuthorizedHandler
legacy-fn-return	vaibify/gui/routeScope.py::fnContainerOwner
legacy-fn-return	vaibify/gui/routeScope.py::fnRouteScope
legacy-fn-return	vaibify/gui/routeScope.py::fnRouteScope._fnDecorator
legacy-fn-return	vaibify/gui/routes/draftRoutes.py::_fnRegisterDraftDelete.fnDeleteDraft
legacy-fn-return	vaibify/gui/routes/draftRoutes.py::_fnRegisterDraftList.fnListDrafts
legacy-fn-return	vaibify/gui/routes/draftRoutes.py::_fnRegisterDraftRead.fnReadDraft
legacy-fn-return	vaibify/gui/routes/draftRoutes.py::_fnRegisterDraftWrite.fnWriteDraft
legacy-fn-return	vaibify/gui/routes/falsificationRoutes.py::_fnRegisterRun.fnRunFalsification
legacy-fn-return	vaibify/gui/routes/falsificationRoutes.py::_fnRegisterView.fnFalsificationGet
legacy-fn-return	vaibify/gui/routes/figureRoutes.py::_fnRegisterFigure.fnCheckFigure
legacy-fn-return	vaibify/gui/routes/figureRoutes.py::_fnRegisterFigure.fnServeFigure
legacy-fn-return	vaibify/gui/routes/fileRoutes.py::_fnProbeFirstChunk
legacy-fn-return	vaibify/gui/routes/fileRoutes.py::_fnRegisterFileDownload.fnDownloadFile
legacy-fn-return	vaibify/gui/routes/fileRoutes.py::_fnRegisterFileExistenceBatch.fnCheckFilesExist
legacy-fn-return	vaibify/gui/routes/fileRoutes.py::_fnRegisterFilePull.fnPullFile
legacy-fn-return	vaibify/gui/routes/fileRoutes.py::_fnRegisterFileUpload.fnUploadFile
legacy-fn-return	vaibify/gui/routes/fileRoutes.py::_fnRegisterFileWrite.fnWriteFile
legacy-fn-return	vaibify/gui/routes/fileRoutes.py::_fnRegisterFiles.fnListDirectory
legacy-fn-return	vaibify/gui/routes/gitRoutes.py::_fnRegisterCommitCanonical.fnCommitCanonical
legacy-fn-return	vaibify/gui/routes/gitRoutes.py::_fnRegisterFetchProjectRepo.fnFetchProjectRepo
legacy-fn-return	vaibify/gui/routes/gitRoutes.py::_fnRegisterGitBadges.fnGitBadges
legacy-fn-return	vaibify/gui/routes/gitRoutes.py::_fnRegisterGitStatus.fnGitStatus
legacy-fn-return	vaibify/gui/routes/gitRoutes.py::_fnRegisterManifestCheck.fnManifestCheck
legacy-fn-return	vaibify/gui/routes/gitRoutes.py::_fnRegisterPullProjectRepo.fnPullProjectRepo
legacy-fn-return	vaibify/gui/routes/gitRoutes.py::_fnRegisterReconcileRemoteState.fnReconcileRemoteState
legacy-fn-return	vaibify/gui/routes/gitRoutes.py::_fnRegisterRefreshRemotes.fnRefreshRemotes
legacy-fn-return	vaibify/gui/routes/gitRoutes.py::_fnRegisterUntrackAiDeclaration.fnUntrackAiDeclaration
legacy-fn-return	vaibify/gui/routes/levelRoutes.py::_fnRegisterAddStep.fnAddAiDeclarationStep
legacy-fn-return	vaibify/gui/routes/levelRoutes.py::_fnRegisterGenerateTemplate.fnGenerateTemplate
legacy-fn-return	vaibify/gui/routes/levelRoutes.py::_fnRegisterLevel2Readiness.fnLevel2Readiness
legacy-fn-return	vaibify/gui/routes/pipelineRoutes.py::_fbApplyRandomnessLint.fnReadFile
legacy-fn-return	vaibify/gui/routes/pipelineRoutes.py::_fnApplyAllMarkerCategories
legacy-fn-return	vaibify/gui/routes/pipelineRoutes.py::_fnApplyExternalTestResults
legacy-fn-return	vaibify/gui/routes/pipelineRoutes.py::_fnApplyMarkerCategory
legacy-fn-return	vaibify/gui/routes/pipelineRoutes.py::_fnClearStaleMarkerCategories
legacy-fn-return	vaibify/gui/routes/pipelineRoutes.py::_fnRegisterAcknowledgeStep.fnAcknowledgeStep
legacy-fn-return	vaibify/gui/routes/pipelineRoutes.py::_fnRegisterFileStatus.fnGetFileStatus
legacy-fn-return	vaibify/gui/routes/pipelineRoutes.py::_fnRegisterHostLogTail.fnGetHostLogTail
legacy-fn-return	vaibify/gui/routes/pipelineRoutes.py::_fnRegisterManifestText.fnGetManifestText
legacy-fn-return	vaibify/gui/routes/pipelineRoutes.py::_fnRegisterPipelineClean.fnCleanOutputs
legacy-fn-return	vaibify/gui/routes/pipelineRoutes.py::_fnRegisterPipelineKill.fnKillRunningTasks
legacy-fn-return	vaibify/gui/routes/pipelineRoutes.py::_fnRegisterPipelineState.fnGetPipelineState
legacy-fn-return	vaibify/gui/routes/pipelineRoutes.py::_fnRegisterWorkflowDiscovery.fnGetWorkflowDiscovery
legacy-fn-return	vaibify/gui/routes/pipelineRoutes.py::_fnUpdateShaCache
legacy-fn-return	vaibify/gui/routes/plotRoutes.py::_fnRegisterStandardizePlots.fnCheckPlotStandards
legacy-fn-return	vaibify/gui/routes/plotRoutes.py::_fnRegisterStandardizePlots.fnComparePlot
legacy-fn-return	vaibify/gui/routes/plotRoutes.py::_fnRegisterStandardizePlots.fnStandardizePlots
legacy-fn-return	vaibify/gui/routes/replayRoutes.py::_fnRegisterContextImport.fnImportProjectContext
legacy-fn-return	vaibify/gui/routes/replayRoutes.py::_fnRegisterContextTemplate.fnGenerateContextTemplate
legacy-fn-return	vaibify/gui/routes/replayRoutes.py::_fnRegisterDeclareAiModel.fnDeclareAiModel
legacy-fn-return	vaibify/gui/routes/replayRoutes.py::_fnRegisterDeclarePersonalLayer.fnDeclarePersonalLayer
legacy-fn-return	vaibify/gui/routes/replayRoutes.py::_fnRegisterHashPersonalLayerFile.fnHashPersonalLayerFile
legacy-fn-return	vaibify/gui/routes/replayRoutes.py::_fnRegisterPromptRecordApprove.fnApproveFirstCapture
legacy-fn-return	vaibify/gui/routes/replayRoutes.py::_fnRegisterPromptRecordCapture.fnCapturePromptRecord
legacy-fn-return	vaibify/gui/routes/replayRoutes.py::_fnRegisterPromptRecordConfigure.fnConfigurePromptRecord
legacy-fn-return	vaibify/gui/routes/replayRoutes.py::_fnRegisterPromptRecordStatus.fnPromptRecordStatus
legacy-fn-return	vaibify/gui/routes/replayRoutes.py::_fnRegisterReadProjectContext.fnReadProjectContext
legacy-fn-return	vaibify/gui/routes/replayRoutes.py::_fnRegisterRemoveAiModel.fnRemoveAiModel
legacy-fn-return	vaibify/gui/routes/replayRoutes.py::_fnRegisterSupervisionConfigure.fnConfigureSupervision
legacy-fn-return	vaibify/gui/routes/replayRoutes.py::_fnRegisterUpdateProjectContext.fnUpdateProjectContext
legacy-fn-return	vaibify/gui/routes/repoRoutes.py::_fnDoInitProjectRepo
legacy-fn-return	vaibify/gui/routes/repoRoutes.py::_fnDoTrackRepo
legacy-fn-return	vaibify/gui/routes/repoRoutes.py::_fnRegisterDirtyFiles.fnDirtyFiles
legacy-fn-return	vaibify/gui/routes/repoRoutes.py::_fnRegisterIgnore.fnIgnoreRepo
legacy-fn-return	vaibify/gui/routes/repoRoutes.py::_fnRegisterInit.fnInitProjectRepo
legacy-fn-return	vaibify/gui/routes/repoRoutes.py::_fnRegisterPushFiles.fnPushFiles
legacy-fn-return	vaibify/gui/routes/repoRoutes.py::_fnRegisterPushStaged.fnPushStaged
legacy-fn-return	vaibify/gui/routes/repoRoutes.py::_fnRegisterStatus.fnRepoStatus
legacy-fn-return	vaibify/gui/routes/repoRoutes.py::_fnRegisterTrack.fnTrackRepo
legacy-fn-return	vaibify/gui/routes/repoRoutes.py::_fnRegisterUntrack.fnUntrackRepo
legacy-fn-return	vaibify/gui/routes/reproducibilityRoutes.py::_fnRegisterAttestation.fnL3AttestationGet
legacy-fn-return	vaibify/gui/routes/reproducibilityRoutes.py::_fnRegisterCaptureBinary.fnCaptureBinary
legacy-fn-return	vaibify/gui/routes/reproducibilityRoutes.py::_fnRegisterDeclareBinaries.fnDeclareBinaries
legacy-fn-return	vaibify/gui/routes/reproducibilityRoutes.py::_fnRegisterDeclareDeterminism.fnDeclareDeterminism
legacy-fn-return	vaibify/gui/routes/reproducibilityRoutes.py::_fnRegisterDeleteDeterminism.fnDeleteDeterminism
legacy-fn-return	vaibify/gui/routes/reproducibilityRoutes.py::_fnRegisterGenerateScript.fnL3GenerateReproduceScript
legacy-fn-return	vaibify/gui/routes/reproducibilityRoutes.py::_fnRegisterReadiness.fnL3Readiness
legacy-fn-return	vaibify/gui/routes/reproducibilityRoutes.py::_fnRegisterRegenerateEnvelope.fnRegenerateEnvelope
legacy-fn-return	vaibify/gui/routes/reproducibilityRoutes.py::_fnRegisterVerify.fnL3Verify
legacy-fn-return	vaibify/gui/routes/reproducibilityRoutes.py::_fnRegisterVerifyDependencyLock.fnVerifyDependencyLock
legacy-fn-return	vaibify/gui/routes/scriptRoutes.py::_fnRegisterScriptRoutes.fnGetScripts
legacy-fn-return	vaibify/gui/routes/scriptRoutes.py::_fnRegisterScriptRoutes.fnScanDependencies
legacy-fn-return	vaibify/gui/routes/scriptRoutes.py::_fnRegisterScriptRoutes.fnScanScripts
legacy-fn-return	vaibify/gui/routes/sessionRoutes.py::_fnAwaitChildReady
legacy-fn-return	vaibify/gui/routes/sessionRoutes.py::_fnLaunchDetachedHub
legacy-fn-return	vaibify/gui/routes/settingsRoutes.py::_fnRegisterLogRoutes.fnGetLogContent
legacy-fn-return	vaibify/gui/routes/settingsRoutes.py::_fnRegisterLogRoutes.fnListLogs
legacy-fn-return	vaibify/gui/routes/settingsRoutes.py::_fnRegisterSettingsGet.fnGetSettings
legacy-fn-return	vaibify/gui/routes/settingsRoutes.py::_fnRegisterSettingsPut.fnUpdateSettings
legacy-fn-return	vaibify/gui/routes/stepRoutes.py::_fnRegisterAlignDirectories.fnAlignStepDirectories
legacy-fn-return	vaibify/gui/routes/stepRoutes.py::_fnRegisterDeclareNoInputData.fnDeclareNoInputData
legacy-fn-return	vaibify/gui/routes/stepRoutes.py::_fnRegisterInputDataAdd.fnAddInputDataFile
legacy-fn-return	vaibify/gui/routes/stepRoutes.py::_fnRegisterStepCreate.fnCreateStep
legacy-fn-return	vaibify/gui/routes/stepRoutes.py::_fnRegisterStepDelete.fnDeleteStep
legacy-fn-return	vaibify/gui/routes/stepRoutes.py::_fnRegisterStepGet.fnGetStep
legacy-fn-return	vaibify/gui/routes/stepRoutes.py::_fnRegisterStepInsert.fnInsertStep
legacy-fn-return	vaibify/gui/routes/stepRoutes.py::_fnRegisterStepRename.fnRenameStep
legacy-fn-return	vaibify/gui/routes/stepRoutes.py::_fnRegisterStepReorder.fnReorderSteps
legacy-fn-return	vaibify/gui/routes/stepRoutes.py::_fnRegisterStepUpdate.fnUpdateStep
legacy-fn-return	vaibify/gui/routes/stepRoutes.py::_fnRegisterStepsList.fnGetSteps
legacy-fn-return	vaibify/gui/routes/stepRoutes.py::_fnRegisterStepsList.fnResolveCommands
legacy-fn-return	vaibify/gui/routes/stepRoutes.py::_fnRegisterStepsList.fnResolveStepLabel
legacy-fn-return	vaibify/gui/routes/stepRoutes.py::_fnRegisterStepsList.fnValidateReferences
legacy-fn-return	vaibify/gui/routes/syncRoutes.py::_fnRegisterArxivConfigure.fnConfigureArxiv
legacy-fn-return	vaibify/gui/routes/syncRoutes.py::_fnRegisterDag.fnGetDag
legacy-fn-return	vaibify/gui/routes/syncRoutes.py::_fnRegisterDagExport.fnExportDag
legacy-fn-return	vaibify/gui/routes/syncRoutes.py::_fnRegisterDatasetDownload.fnDownloadDataset
legacy-fn-return	vaibify/gui/routes/syncRoutes.py::_fnRegisterGithubAddFile.fnGithubAddFile
legacy-fn-return	vaibify/gui/routes/syncRoutes.py::_fnRegisterGithubIdentity.fnGithubIdentity
legacy-fn-return	vaibify/gui/routes/syncRoutes.py::_fnRegisterGithubPush.fnGithubPush
legacy-fn-return	vaibify/gui/routes/syncRoutes.py::_fnRegisterOverleafDiff.fnOverleafDiff
legacy-fn-return	vaibify/gui/routes/syncRoutes.py::_fnRegisterOverleafMirrorDelete.fnDeleteMirror
legacy-fn-return	vaibify/gui/routes/syncRoutes.py::_fnRegisterOverleafMirrorRefresh.fnRefreshMirror
legacy-fn-return	vaibify/gui/routes/syncRoutes.py::_fnRegisterOverleafMirrorTree.fnGetMirrorTree
legacy-fn-return	vaibify/gui/routes/syncRoutes.py::_fnRegisterOverleafPush.fnOverleafPush
legacy-fn-return	vaibify/gui/routes/syncRoutes.py::_fnRegisterPullManuscript.fnPullManuscript
legacy-fn-return	vaibify/gui/routes/syncRoutes.py::_fnRegisterRemoteVerify.fnVerifyRemote
legacy-fn-return	vaibify/gui/routes/syncRoutes.py::_fnRegisterRemoteVerifyStatus.fnGetRemoteVerifyStatus
legacy-fn-return	vaibify/gui/routes/syncRoutes.py::_fnRegisterReverifySchedule.fnGetReverifySchedule
legacy-fn-return	vaibify/gui/routes/syncRoutes.py::_fnRegisterSyncRoutes.fnCheckConnection
legacy-fn-return	vaibify/gui/routes/syncRoutes.py::_fnRegisterSyncRoutes.fnGetSyncFiles
legacy-fn-return	vaibify/gui/routes/syncRoutes.py::_fnRegisterSyncRoutes.fnGetSyncStatus
legacy-fn-return	vaibify/gui/routes/syncRoutes.py::_fnRegisterSyncRoutes.fnHasCredential
legacy-fn-return	vaibify/gui/routes/syncRoutes.py::_fnRegisterSyncRoutes.fnSetTracking
legacy-fn-return	vaibify/gui/routes/syncRoutes.py::_fnRegisterSyncRoutes.fnSetupConnection
legacy-fn-return	vaibify/gui/routes/syncRoutes.py::_fnRegisterZenodoArchive.fnZenodoArchive
legacy-fn-return	vaibify/gui/routes/syncRoutes.py::_fnRegisterZenodoDeposit.fnGetZenodoDeposit
legacy-fn-return	vaibify/gui/routes/syncRoutes.py::_fnRegisterZenodoMetadata.fnGetZenodoMetadata
legacy-fn-return	vaibify/gui/routes/syncRoutes.py::_fnRegisterZenodoMetadata.fnSetZenodoMetadata
legacy-fn-return	vaibify/gui/routes/syncRoutes.py::_ftRunOverleafPushCall.fnPushWorker
legacy-fn-return	vaibify/gui/routes/systemRoutes.py::_fnRegisterContainerIsolation.fnContainerIsolation
legacy-fn-return	vaibify/gui/routes/systemRoutes.py::_fnRegisterContainerReady.fnContainerReady
legacy-fn-return	vaibify/gui/routes/systemRoutes.py::_fnRegisterDockerStatus.fnGetDockerStatus
legacy-fn-return	vaibify/gui/routes/systemRoutes.py::_fnRegisterDockerStatus.fnPostDockerStatusRetry
legacy-fn-return	vaibify/gui/routes/systemRoutes.py::_fnRegisterMonitor.fnGetMonitorStats
legacy-fn-return	vaibify/gui/routes/systemRoutes.py::_fnRegisterRuntimeInfo.fnGetRuntimeInfo
legacy-fn-return	vaibify/gui/routes/systemRoutes.py::_fnRegisterUserInfo.fnGetUser
legacy-fn-return	vaibify/gui/routes/testRoutes.py::_fnRegisterTestGenerate.fnDeleteGeneratedTest
legacy-fn-return	vaibify/gui/routes/testRoutes.py::_fnRegisterTestGenerate.fnGenerateTest
legacy-fn-return	vaibify/gui/routes/testRoutes.py::_fnRegisterTestRun.fnRunTestCategory
legacy-fn-return	vaibify/gui/routes/testRoutes.py::_fnRegisterTestRun.fnRunTests
legacy-fn-return	vaibify/gui/routes/testRoutes.py::_fnRegisterTestSaveAndRun.fnSaveAndRunTest
legacy-fn-return	vaibify/gui/routes/workflowRoutes.py::_fnRegisterConnect.fnConnect
legacy-fn-return	vaibify/gui/routes/workflowRoutes.py::_fnRegisterWorkflowCreate.fnCreateWorkflow
legacy-fn-return	vaibify/gui/routes/workflowRoutes.py::_fnRegisterWorkflowCreationRequest.fnRequestProjectCreation
legacy-fn-return	vaibify/gui/routes/workflowRoutes.py::_fnRegisterWorkflowSearch.fnFindWorkflows
legacy-fn-return	vaibify/gui/setupServer.py::_fnRegisterIndexRoute.fnServeSetupIndex
legacy-fn-return	vaibify/gui/setupServer.py::_fnRegisterReadRoutes.fnListTemplates
legacy-fn-return	vaibify/gui/setupServer.py::_fnRegisterReadRoutes.fnValidate
legacy-fn-return	vaibify/gui/setupServer.py::_fnRegisterSessionTokenRoute.fnGetSessionToken
legacy-fn-return	vaibify/gui/setupServer.py::_fnRegisterWriteRoutes.fnBuild
legacy-fn-return	vaibify/gui/setupServer.py::_fnRegisterWriteRoutes.fnSave
legacy-fn-return	vaibify/gui/stepRename.py::_fnMoveStepDirectory
legacy-fn-return	vaibify/gui/terminalContainment.py::fnDrainSessionRecord
legacy-fn-return	vaibify/gui/workflowManager.py::_fnRmRfDirectory
legacy-fn-return	vaibify/gui/workflowManager.py::fnCleanStepScratchDirs
legacy-fn-return	vaibify/gui/workflowManager.py::fnDeleteStep.fnRemap
legacy-fn-return	vaibify/gui/workflowManager.py::fnInsertStep.fnRemap
legacy-fn-return	vaibify/gui/workflowManager.py::fnReorderStep.fnRemap
legacy-fn-return	vaibify/gui/workflowManager.py::fsRemapStepReferences.fnReplace
legacy-fn-return	vaibify/gui/workflowManager.py::fsResolveVariables.fnReplace
legacy-fn-return	vaibify/gui/workflowMigrations.py::fnApplyMigrations
legacy-fn-return	vaibify/gui/workflowMigrations.py::fnMigrateArchiveToTracking
legacy-fn-return	vaibify/gui/workflowMigrations.py::fnRewritePositionalToSymbolic.fnReplace
legacy-fn-return	vaibify/install/setupServer.py::_fnRegisterBuildRoute.fnBuildContainer
legacy-fn-return	vaibify/install/setupServer.py::_fnRegisterConfigRoutes.fnGetDefaults
legacy-fn-return	vaibify/install/setupServer.py::_fnRegisterConfigRoutes.fnGetExistingConfig
legacy-fn-return	vaibify/install/setupServer.py::_fnRegisterConfigRoutes.fnSaveConfig
legacy-fn-return	vaibify/install/setupServer.py::_fnRegisterConfigRoutes.fnValidateConfig
legacy-fn-return	vaibify/install/setupServer.py::_fnRegisterStaticFiles.fnServeIndex
legacy-fn-return	vaibify/install/setupServer.py::_fnRegisterTemplateRoutes.fnGetTemplateConfig
legacy-fn-return	vaibify/install/setupServer.py::_fnRegisterTemplateRoutes.fnGetTemplates
legacy-fn-return	vaibify/reproducibility/aiDeclarationStep.py::fnWriteDeclarationTemplate
legacy-fn-return	vaibify/reproducibility/l3Attestation.py::fnInvalidateAttestation
legacy-fn-return	vaibify/reproducibility/overleafMirror.py::_fnCountMirrorFiles
legacy-fn-return	vaibify/reproducibility/overleafMirror.py::_fnRunGit
legacy-fn-return	vaibify/reproducibility/overleafSync.py::_fnBuildParser
legacy-fn-return	vaibify/reproducibility/overleafSync.py::_fnRunSubprocess
legacy-fn-return	vaibify/reproducibility/repoFiles.py::ContainerRepoFiles.fnWithLock
legacy-fn-return	vaibify/reproducibility/repoFiles.py::HostRepoFiles.fnWithLock
legacy-fn-return	vaibify/reproducibility/repoFiles.py::_fnAcquireHostLock
legacy-fn-return	vaibify/reproducibility/reproduceScriptGenerator.py::fnGenerateReproduceScript
legacy-fn-return	vaibify/reproducibility/scheduledReverify.py::fnRunReverifyOnce
legacy-literal-return	vaibify/cli/commandBuild.py::_fpreflightArch
legacy-literal-return	vaibify/cli/commandBuild.py::_fpreflightDisk
legacy-literal-return	vaibify/cli/commandBuild.py::_fpreflightMemory
legacy-literal-return	vaibify/cli/commandReproduce.py::_fiRunPipInstall
legacy-literal-return	vaibify/gui/dockerStatus.py::fsDetectDockerRuntime
legacy-literal-return	vaibify/gui/fileStatusManager.py::_fbStepIsPencilStale
legacy-literal-return	vaibify/gui/fileStatusManager.py::_flistDetectAndInvalidate
legacy-literal-return	vaibify/gui/fileStatusManager.py::_flistSplitOutputPaths
legacy-literal-return	vaibify/gui/pipelineRunner.py::_fiRunSetupIfNeeded
legacy-literal-return	vaibify/gui/pipelineRunner.py::_flistAppendAndMaybeDrainBatch
legacy-literal-return	vaibify/gui/pipelineRunner.py::fiRunStepCommands
legacy-literal-return	vaibify/gui/routes/draftRoutes.py::_fsRequireProjectRepoAndWorkflowPath
legacy-literal-return	vaibify/gui/routes/draftRoutes.py::_fsResolveDraftFile
legacy-literal-return	vaibify/gui/routes/syncRoutes.py::_fbRunOverleafValidation
legacy-literal-return	vaibify/gui/routes/testRoutes.py::_fdictResolveCategoryContext
legacy-literal-return	vaibify/gui/staleOutputDetector.py::_flistOffendingForPair
legacy-literal-return	vaibify/gui/syncDispatcher.py::_fbValidateOverleafOnHost
legacy-literal-return	vaibify/gui/testGenerator.py::fsBuildStepContext
legacy-name	vaibify/cli/commandBuild.py::_fImportBuildOrExit
legacy-name	vaibify/cli/commandBuild.py::_fdiDockerDfBytes
legacy-name	vaibify/cli/commandStart.py::_flistpreflightBindMountFormats
legacy-name	vaibify/cli/commandStart.py::_flistpreflightBindMounts
legacy-name	vaibify/cli/commandStart.py::_flistpreflightColimaSharedRoots
legacy-name	vaibify/cli/commandStart.py::_flistpreflightPorts
legacy-name	vaibify/cli/hubSession.py::_fobjParseResponseBody
legacy-name	vaibify/cli/hubSession.py::_fobjRequireOkResponse
legacy-name	vaibify/config/processLiveness.py::_fdtNormalizeToNaiveLocal
legacy-name	vaibify/config/processLiveness.py::fdtParseClaimIso
legacy-name	vaibify/config/processLiveness.py::fdtReadProcessStartClock
legacy-name	vaibify/config/processLiveness.py::fdtReadProcessStartClockCached
legacy-name	vaibify/docker/dockerConnection.py::DockerConnection.ftupleRunRootShellProbe
legacy-name	vaibify/docker/dockerConnection.py::_BytesGeneratorPipe._baDrainAll
legacy-name	vaibify/docker/imageBuilder.py::_ferrorBuildFailed
legacy-name	vaibify/gui/attributionLog.py::fdtParseTimestampAsUtc
legacy-name	vaibify/gui/browserSession.py::_tCreateSessionRecordLocked
legacy-name	vaibify/gui/browserSession.py::_tMintSessionForCapability
legacy-name	vaibify/gui/commitCarrier.py::_fRunEffectAdmitted
legacy-name	vaibify/gui/commitCarrier.py::ftupleOpenEstablishingAdmission
legacy-name	vaibify/gui/commitCarrier.py::ftupleOpenRequestAdmission
legacy-name	vaibify/gui/containerOwnership.py::_ftdictClaimUnowned
legacy-name	vaibify/gui/containerOwnership.py::ftdictClaim
legacy-name	vaibify/gui/dataLoaders.py::_fApplyAggregate
legacy-name	vaibify/gui/dataLoaders.py::_fExtractArrayValue
legacy-name	vaibify/gui/dataLoaders.py::_fExtractDataframeValue
legacy-name	vaibify/gui/dataLoaders.py::_fExtractHdf5Value
legacy-name	vaibify/gui/dataLoaders.py::_fExtractTabularValue
legacy-name	vaibify/gui/dataLoaders.py::_fLoadBamValue
legacy-name	vaibify/gui/dataLoaders.py::_fLoadBedValue
legacy-name	vaibify/gui/dataLoaders.py::_fLoadCefValue
legacy-name	vaibify/gui/dataLoaders.py::_fLoadCgnsValue
legacy-name	vaibify/gui/dataLoaders.py::_fLoadCsvAggregate
legacy-name	vaibify/gui/dataLoaders.py::_fLoadCsvByRowIndex
legacy-name	vaibify/gui/dataLoaders.py::_fLoadCsvNegativeRow
legacy-name	vaibify/gui/dataLoaders.py::_fLoadCsvValue
legacy-name	vaibify/gui/dataLoaders.py::_fLoadExcelValue
legacy-name	vaibify/gui/dataLoaders.py::_fLoadFastaValue
legacy-name	vaibify/gui/dataLoaders.py::_fLoadFastqValue
legacy-name	vaibify/gui/dataLoaders.py::_fLoadFitsValue
legacy-name	vaibify/gui/dataLoaders.py::_fLoadFixedwidthValue
legacy-name	vaibify/gui/dataLoaders.py::_fLoadFortranValue
legacy-name	vaibify/gui/dataLoaders.py::_fLoadGffValue
legacy-name	vaibify/gui/dataLoaders.py::_fLoadHdf5Value
legacy-name	vaibify/gui/dataLoaders.py::_fLoadImageValue
legacy-name	vaibify/gui/dataLoaders.py::_fLoadIpacValue
legacy-name	vaibify/gui/dataLoaders.py::_fLoadJsonValue
legacy-name	vaibify/gui/dataLoaders.py::_fLoadJsonlValue
legacy-name	vaibify/gui/dataLoaders.py::_fLoadKeyvalueValue
legacy-name	vaibify/gui/dataLoaders.py::_fLoadMatlabValue
legacy-name	vaibify/gui/dataLoaders.py::_fLoadMultitableValue
legacy-name	vaibify/gui/dataLoaders.py::_fLoadNpzValue
legacy-name	vaibify/gui/dataLoaders.py::_fLoadNumpyValue
legacy-name	vaibify/gui/dataLoaders.py::_fLoadParquetValue
legacy-name	vaibify/gui/dataLoaders.py::_fLoadPcapValue
legacy-name	vaibify/gui/dataLoaders.py::_fLoadRdataValue
legacy-name	vaibify/gui/dataLoaders.py::_fLoadSafetensorsValue
legacy-name	vaibify/gui/dataLoaders.py::_fLoadSamValue
legacy-name	vaibify/gui/dataLoaders.py::_fLoadSasValue
legacy-name	vaibify/gui/dataLoaders.py::_fLoadSpssValue
legacy-name	vaibify/gui/dataLoaders.py::_fLoadStataValue
legacy-name	vaibify/gui/dataLoaders.py::_fLoadSyslogValue
legacy-name	vaibify/gui/dataLoaders.py::_fLoadTabularWithComments
legacy-name	vaibify/gui/dataLoaders.py::_fLoadTfrecordValue
legacy-name	vaibify/gui/dataLoaders.py::_fLoadValue
legacy-name	vaibify/gui/dataLoaders.py::_fLoadVcfValue
legacy-name	vaibify/gui/dataLoaders.py::_fLoadVotableValue
legacy-name	vaibify/gui/dataLoaders.py::_fLoadVtkValue
legacy-name	vaibify/gui/dataLoaders.py::_fLoadWhitespaceValue
legacy-name	vaibify/gui/dataLoaders.py::_fNavigateJsonValue
legacy-name	vaibify/gui/dataLoaders.py::_freaderOpenCsv
legacy-name	vaibify/gui/dataLoaders.py::fLoadValue
legacy-name	vaibify/gui/hostControlChannel.py::_fbyteReadResponseLine
legacy-name	vaibify/gui/hostControlChannel.py::_ftupleParseDarwinPeerCredentials
legacy-name	vaibify/gui/hostControlChannel.py::_ftupleParseLinuxPeerCredentials
legacy-name	vaibify/gui/hostControlChannel.py::fituplePeerUidGid
legacy-name	vaibify/gui/pipelineRunner.py::_actxWebSocketHeartbeat
legacy-name	vaibify/gui/pipelineRunner.py::_fParseCpuTime
legacy-name	vaibify/gui/pipelineRunner.py::_faFlushBatchFromLoop
legacy-name	vaibify/gui/pipelineRunner.py::_faTimerFlush
legacy-name	vaibify/gui/pipelineRunner.py::_ftBuildBatchingEmitter.faDrainPending
legacy-name	vaibify/gui/pipelineServer.py::_ftupleBuildHelpers
legacy-name	vaibify/gui/registryRoutes.py::_ftupleDiscoverAllContainers
legacy-name	vaibify/gui/registryRoutes.py::_ftupleSplitContainers
legacy-name	vaibify/gui/routeContext.py::RouteContext.files
legacy-name	vaibify/gui/routeContext.py::RouteContext.require
legacy-name	vaibify/gui/routeContext.py::RouteContext.save
legacy-name	vaibify/gui/routeContext.py::RouteContext.variables
legacy-name	vaibify/gui/routeContext.py::RouteContext.workflowDir
legacy-name	vaibify/gui/routes/fileRoutes.py::_ttIterStreamOrRaiseHttp
legacy-name	vaibify/gui/routes/gitRoutes.py::_tCollectGitBadgeInputs
legacy-name	vaibify/gui/routes/testRoutes.py::_fresultRunSaveAndRunTest
legacy-name	vaibify/gui/serverLifespan.py::_alifespanShared
legacy-name	vaibify/gui/serverLifespan.py::_fIdleTimeoutSeconds
legacy-name	vaibify/gui/sessionLifecycle.py::_tCommitTransfer
legacy-name	vaibify/gui/sessionLifecycle.py::_tOutcomeForBusyContainer
legacy-name	vaibify/gui/sessionLifecycle.py::_tOutcomeForCapabilityState
legacy-name	vaibify/gui/sessionLifecycle.py::_tRefusalAtCommitPoint
legacy-name	vaibify/gui/sessionLifecycle.py::_tRefusalBeforePremint
legacy-name	vaibify/gui/sessionLifecycle.py::_tReserveForStartUnderLocks
legacy-name	vaibify/gui/sessionLifecycle.py::_tTransferUnderDrain
legacy-name	vaibify/gui/sessionLifecycle.py::ftdictClaimWithCardinality
legacy-name	vaibify/gui/staleOutputDetector.py::_fLookupMtime
legacy-name	vaibify/gui/staleOutputDetector.py::_fMaxMtime
legacy-name	vaibify/gui/staleOutputDetector.py::_fParseMtime
legacy-name	vaibify/gui/startReservation.py::_tDeliverFailedResult
legacy-name	vaibify/gui/startReservation.py::_tDeliverPendingResult
legacy-name	vaibify/gui/startReservation.py::_tDeliverSucceededResult
legacy-name	vaibify/gui/startReservation.py::_tMarkCancelRequested
legacy-name	vaibify/gui/startReservation.py::_tRecoverLeaseWithoutAResult
legacy-name	vaibify/gui/stateManager.py::_ftupleTryLoadStateFile
legacy-name	vaibify/gui/testGenerator.py::_ftolForStochasticKind
legacy-name	vaibify/gui/testGenerator.py::_ftolMeanFromCv
legacy-name	vaibify/gui/testGenerator.py::_ftolPercentileFromN
legacy-name	vaibify/gui/testGenerator.py::_ftolStdFromN
legacy-name	vaibify/gui/transcriptSanitizer.py::_fFractionalShannonEntropy
legacy-name	vaibify/gui/webSocketAuthorization.py::ffbBuildPerFrameCredentialCheck
legacy-name	vaibify/gui/workflowManager.py::_fdepCacheGet
legacy-name	vaibify/reproducibility/aiProvenanceStamp.py::_sHashProjectContext
legacy-name	vaibify/reproducibility/githubMirror.py::_fobjectBuildRequest
legacy-name	vaibify/reproducibility/githubMirror.py::_fobjectOpenRequest
legacy-name	vaibify/reproducibility/overleafMirror.py::_fresultSyntheticGitFailure
legacy-name	vaibify/templates/workflow/PlotHistogram/plotHistogram.py::fliaCountPerBin
legacy-name	vaibify/testing/standards.py::_daLoadCsv
legacy-name	vaibify/testing/standards.py::_daLoadNpy
legacy-name	vaibify/testing/standards.py::_daLoadWhitespace
legacy-name	vaibify/testing/standards.py::_dictLoadJson
legacy-name	vaibify/testing/standards.py::_dictLoadKeyValueText
legacy-name	vaibify/testing/standards.py::_dictLoadNpz
legacy-name	vaibify/testing/standards.py::_listColumnStats
legacy-name	vaibify/testing/standards.py::_listGlobalAggregates
legacy-name	vaibify/testing/standards.py::_listPerColumnFirstLast
legacy-name	vaibify/testing/standards.py::_listStandardsFromArray
legacy-name	vaibify/testing/standards.py::_listStandardsFromFile
legacy-name	vaibify/testing/standards.py::_listStandardsFromJson
legacy-name	vaibify/testing/standards.py::_listStandardsFromJsonList
legacy-name	vaibify/testing/standards.py::_listStandardsFromNpz
legacy-name	vaibify/testing/standards.py::_listStandardsFromNpz1d
legacy-name	vaibify/testing/standards.py::_listStandardsFromNpz2d
legacy-name	vaibify/testing/standards.py::_listStandardsFromNpzScalar
legacy-name	vaibify/testing/standards.py::_listStandardsFromScalarJson
legacy-name	vaibify/testing/standards.py::_listStandardsFromTextOrKv
legacy-name	vaibify/testing/standards.py::fLoadValue
legacy-yield	vaibify/docker/dockerConnection.py::DockerConnection.fnIterStreamFile
legacy-yield	vaibify/gui/workflowMigrations.py::_fnTemporaryProjectRepoPath
legacy-yield	vaibify/reproducibility/levelGates.py::fnLevelComputationContext
security-pinned	vaibify/docker/dockerConnection.py::DockerConnection._texecRunTypedRead
security-pinned	vaibify/docker/dockerConnection.py::DockerConnection.texecRunInContainerStreamed
security-pinned	vaibify/docker/dockerConnection.py::DockerConnection.texecRunInContainerStreamedWithChunks
"""


def _fsetParseFrozenSeed():
    setPairs = set()
    for sLine in S_FROZEN_SEED_TEXT.splitlines():
        if not sLine.strip():
            continue
        sDebtClass, sIdentity = sLine.split("\t", 1)
        setPairs.add((sIdentity, sDebtClass))
    return frozenset(setPairs)


SET_FROZEN_SEED_PAIRS = _fsetParseFrozenSeed()


# The variable-binding invariant's FOUNDING census (2026-08-06): the
# doctrine extended to every binding site, and these are the bindings
# that predate it. Same rules as the function seed: never edited except
# by explicit reviewed decision; burn-down lowers the budget and
# regenerates the inventory but leaves this text alone.
S_FROZEN_VARIABLE_SEED_TEXT = """\
legacy-variable	vaibify/docker/dockerConnection.py::DockerConnection._ftRunTypedRead::objPaths
legacy-variable	vaibify/docker/dockerConnection.py::_fsTypedReadPathLiteral::objPaths
legacy-variable	vaibify/gui/resourceMonitor.py::_ftReadFilesystemUsage.executorPool
legacy-variable	vaibify/gui/resourceMonitor.py::_ftReadFilesystemUsage.future
legacy-variable	vaibify/gui/routeScope.py::_fbServeOnAmbientAdmission::route
legacy-variable	vaibify/gui/routes/pipelineRoutes.py::_fdictVerifyManifestBlocking::manifestWriter
legacy-variable	vaibify/gui/routes/pipelineRoutes.py::_fdictVerifyManifestUnderTheDrain::manifestWriter
legacy-variable	vaibify/gui/routes/syncRoutes.py::_fdictPullManuscriptBlocking::syncDispatcher
legacy-variable	vaibify/gui/routes/syncRoutes.py::_fdictPullManuscriptUnderTheDrain::syncDispatcher
legacy-variable	vaibify/gui/routes/syncRoutes.py::_fnRegisterDag.fresponseHandleGetDag.result
legacy-variable	vaibify/gui/routes/syncRoutes.py::_fnRegisterDagExport.fresponseHandleExportDag.result
legacy-variable	vaibify/gui/routes/syncRoutes.py::_fnRegisterOverleafMirrorRefresh.fdictHandleRefreshMirror.result
legacy-variable	vaibify/cli/actionCommands.py::fnDoCommand::ctx
legacy-variable	vaibify/cli/actionCommands.py::fnRegisterGeneratedActions::groupParent
legacy-variable	vaibify/cli/actionCommands.py::fsAppendQueryString.objValue
legacy-variable	vaibify/cli/actionCommands.py::ftSplitQueryFromBodyFields.objValue
legacy-variable	vaibify/cli/commandBuild.py::_fnEnforceBuildPreflight.r
legacy-variable	vaibify/cli/commandBuild.py::_fnPrintWarningsIfAny.r
legacy-variable	vaibify/cli/commandBuild.py::flistRunBuildPreflight.r
legacy-variable	vaibify/cli/commandBuild.py::flistRunBuildPreflight.resultColimaVersion
legacy-variable	vaibify/cli/commandBuild.py::fnPruneDanglingImages.resultPrune
legacy-variable	vaibify/cli/commandConfig.py::fnConfigExportCommand::sfilepath
legacy-variable	vaibify/cli/commandConfig.py::fnConfigImportCommand::sfilepath
legacy-variable	vaibify/cli/commandDestroy.py::fnRemoveVolume.volume
legacy-variable	vaibify/cli/commandDoctor.py::_flistFilterQuiet.r
legacy-variable	vaibify/cli/commandDoctor.py::_flistOptionalSharedChecks.r
legacy-variable	vaibify/cli/commandDoctor.py::_ftCountLevels.r
legacy-variable	vaibify/cli/commandDoctor.py::flistRunDoctorChecks.r
legacy-variable	vaibify/cli/commandDoctor.py::fnDoctorCommand.r
legacy-variable	vaibify/cli/commandRegister.py::fnRegisterCommand::sdirectory
legacy-variable	vaibify/cli/commandRevoke.py::_fdictRevokeForService::sservice
legacy-variable	vaibify/cli/commandRevoke.py::fnPrintRevocationReport::sservice
legacy-variable	vaibify/cli/commandRevoke.py::fnRevokeCommand::sservice
legacy-variable	vaibify/cli/commandSessions.py::fnListSessionsCommand::ctx
legacy-variable	vaibify/cli/commandSessions.py::fnStopSessionCommand::ipid
legacy-variable	vaibify/cli/commandStart.py::_flistPreflightBindMountFormats.resultPath
legacy-variable	vaibify/cli/commandStart.py::_fnEnforcePreflightOrExit.r
legacy-variable	vaibify/cli/commandStart.py::_fnPrintWarningsIfAny.r
legacy-variable	vaibify/cli/commandStart.py::flistRunStartPreflight.resultColimaVersion
legacy-variable	vaibify/cli/commandStatus.py::flistDescribeContainers.dcContainer
legacy-variable	vaibify/cli/commandStatus.py::fsDescribeImage.image
legacy-variable	vaibify/cli/hubSession.py::fsExplainClaimConflict::objDetail
legacy-variable	vaibify/cli/main.py::_fbHasFileHandlerAttached.handlerExisting
legacy-variable	vaibify/cli/main.py::_fbHasIncidentHandlerAttached.handlerExisting
legacy-variable	vaibify/cli/main.py::_fnAttachHostIncidentHandler.handlerIncident
legacy-variable	vaibify/cli/main.py::_fnConfigureErrorLogging.rotatingHandler
legacy-variable	vaibify/cli/main.py::fnConnectCommand::project
legacy-variable	vaibify/cli/main.py::fnPullCommand::destination
legacy-variable	vaibify/cli/main.py::fnPullCommand::project
legacy-variable	vaibify/cli/main.py::fnPullCommand::source
legacy-variable	vaibify/cli/main.py::fnPushCommand::destination
legacy-variable	vaibify/cli/main.py::fnPushCommand::project
legacy-variable	vaibify/cli/main.py::fnPushCommand::source
legacy-variable	vaibify/cli/main.py::main::ctx
legacy-variable	vaibify/cli/portAllocator.py::fbIsPortFree.sock
legacy-variable	vaibify/cli/preflightResult.py::_fnPrintDetailBlock::resultPreflight
legacy-variable	vaibify/cli/preflightResult.py::fnPrintPreflightReport.resultPreflight
legacy-variable	vaibify/config/bindMountValidator.py::_fbEntryIsDirectory::entryChild
legacy-variable	vaibify/config/bindMountValidator.py::_fbEntryIsSymlink::entryChild
legacy-variable	vaibify/config/bindMountValidator.py::_fnAssertChildIsNotSocket::entryChild
legacy-variable	vaibify/config/bindMountValidator.py::_fnRejectContainedSocket.entryChild
legacy-variable	vaibify/config/containerLock.py::_fbHandleMatchesPath.statHandle
legacy-variable	vaibify/config/containerLock.py::_fbHandleMatchesPath.statPath
legacy-variable	vaibify/config/keepAliveManager.py::_fdictParsePidContent.objParsed
legacy-variable	vaibify/config/mutationAdmission.py::MutationAdmission.__init__::objectMintKey
legacy-variable	vaibify/config/mutationAdmission.py::_contextActiveAdmissions
legacy-variable	vaibify/config/mutationAdmission.py::_contextAuditedRead
legacy-variable	vaibify/config/mutationAdmission.py::_contextEnforcedLane
legacy-variable	vaibify/config/mutationAdmission.py::fnAssertOperationAdmittedByIdentity.valueExpected
legacy-variable	vaibify/config/operationJournal.py::_fdictValidateJournalBytes::byteContent
legacy-variable	vaibify/config/operationJournal.py::_fnWriteJournalBytesAtomically::byteContent
legacy-variable	vaibify/config/operationJournal.py::_fsComputeHostFileSha256.byteChunk
legacy-variable	vaibify/config/operationJournal.py::_fsComputeHostFileSha256.hashDigest
legacy-variable	vaibify/config/operationJournal.py::_fsValidateRecordFieldTypes.valueField
legacy-variable	vaibify/config/operationJournal.py::fdictReadJournalOutcome.byteContent
legacy-variable	vaibify/config/operationJournal.py::fnAmendInFlightHolderIdentity.valueIdentity
legacy-variable	vaibify/config/processLiveness.py::_fdatetimeNormalizeToNaiveLocal::dtValue
legacy-variable	vaibify/config/processLiveness.py::fbIsProcessAliveSince.dtClaim
legacy-variable	vaibify/config/processLiveness.py::fbIsProcessAliveSince.dtStart
legacy-variable	vaibify/config/processLiveness.py::fbIsProcessAliveSince.dtTolerance
legacy-variable	vaibify/config/processLiveness.py::fdatetimeParseClaimIso.dtClaim
legacy-variable	vaibify/config/projectConfig.py::ProjectConfig.reproducibility
legacy-variable	vaibify/config/projectConfig.py::_fbValidateFeatures.value
legacy-variable	vaibify/config/projectConfig.py::_fbValidateListFields.value
legacy-variable	vaibify/config/projectConfig.py::_fdictMergeWithDefaults.value
legacy-variable	vaibify/config/projectConfig.py::_fnMergeReproducibility.value
legacy-variable	vaibify/config/projectConfig.py::k
legacy-variable	vaibify/config/projectConfig.py::v
legacy-variable	vaibify/config/reconciliation.py::_fdictRecordForProbe.valueField
legacy-variable	vaibify/containerImage/vaibifyDo.py::_fbaRecvExact.dataBuffer
legacy-variable	vaibify/containerImage/vaibifyDo.py::_fbaRecvExact.dataChunk
legacy-variable	vaibify/containerImage/vaibifyDo.py::_fiHandleHttpError.dataBody
legacy-variable	vaibify/containerImage/vaibifyDo.py::_fiHandleHttpError::errHttp
legacy-variable	vaibify/containerImage/vaibifyDo.py::_fiStreamWsEvents.dataFrame
legacy-variable	vaibify/containerImage/vaibifyDo.py::_fnPrintHttpBody.objParsed
legacy-variable	vaibify/containerImage/vaibifyDo.py::_fnPrintHttpBody::dataBody
legacy-variable	vaibify/containerImage/vaibifyDo.py::_fnSendWsFrame.dataHeader
legacy-variable	vaibify/containerImage/vaibifyDo.py::_fnSendWsFrame.dataMask
legacy-variable	vaibify/containerImage/vaibifyDo.py::_fnSendWsFrame.dataMasked
legacy-variable	vaibify/containerImage/vaibifyDo.py::_fnSendWsFrame::dataPayload
legacy-variable	vaibify/containerImage/vaibifyDo.py::fiResolveLabelToIndex.resp
legacy-variable	vaibify/containerImage/vaibifyDo.py::fiSendHttpRequest.dataBody
legacy-variable	vaibify/containerImage/vaibifyDo.py::fiSendHttpRequest.errHttp
legacy-variable	vaibify/containerImage/vaibifyDo.py::fiSendHttpRequest.resp
legacy-variable	vaibify/containerImage/vaibifyDo.py::fnSendWsPong::dataPayload
legacy-variable	vaibify/containerImage/vaibifyDo.py::fnWebsocketHandshake.dataChunk
legacy-variable	vaibify/containerImage/vaibifyDo.py::fnWebsocketHandshake.dataResponse
legacy-variable	vaibify/containerImage/vaibifyDo.py::ftRecvWsFrame.dataHeader
legacy-variable	vaibify/containerImage/vaibifyDo.py::ftRecvWsFrame.dataPayload
legacy-variable	vaibify/docker/dockerConnection.py::DockerConnection._fbufferBuildTar.tar
legacy-variable	vaibify/docker/dockerConnection.py::DockerConnection._ftStreamExecLines.generator
legacy-variable	vaibify/docker/dockerConnection.py::_fiterChunksFromTarStream.tar
legacy-variable	vaibify/docker/dockerConnection.py::_fnMountTcpAdapter::classAdapter
legacy-variable	vaibify/docker/dockerConnection.py::_fnMountUnixAdapter.adapterExisting
legacy-variable	vaibify/docker/dockerConnection.py::_fnTuneDockerSessionPool::clientDocker
legacy-variable	vaibify/docker/imageBuilder.py::_fnRunDockerBuildCapturing.procBuild
legacy-variable	vaibify/docker/imageBuilder.py::_fsStreamAndCaptureStderr::procBuild
legacy-variable	vaibify/gui/attributionLog.py::_flistTimestampedEvents.dtEvent
legacy-variable	vaibify/gui/attributionLog.py::fdatetimeParseTimestampAsUtc.dtParsed
legacy-variable	vaibify/gui/commitCarrier.py::MutationSupervisor.eventCancelRequested
legacy-variable	vaibify/gui/commitCarrier.py::_fdictRunAndSettleWorker.resultWorker
legacy-variable	vaibify/gui/commitCarrier.py::_fgenericCallWorkerSynchronously.resultWorker
legacy-variable	vaibify/gui/commitCarrier.py::fdictCommitSynchronousMutation.resultEffect
legacy-variable	vaibify/gui/conftestManager.py::_fnRememberRefreshKey::orderedCache
legacy-variable	vaibify/gui/containerOwnership.py::OwnerRecord.poison
legacy-variable	vaibify/gui/dataLoaders.py::_ffExtractDataframeValue::dfData
legacy-variable	vaibify/gui/dataLoaders.py::_ffExtractHdf5Value::datasetHdf5
legacy-variable	vaibify/gui/dataLoaders.py::_ffExtractTabularValue.r
legacy-variable	vaibify/gui/dataLoaders.py::_ffLoadBamValue.read
legacy-variable	vaibify/gui/dataLoaders.py::_ffLoadBamValue.samfile
legacy-variable	vaibify/gui/dataLoaders.py::_ffLoadCsvAggregate.reader
legacy-variable	vaibify/gui/dataLoaders.py::_ffLoadCsvByRowIndex.reader
legacy-variable	vaibify/gui/dataLoaders.py::_ffLoadCsvNegativeRow.reader
legacy-variable	vaibify/gui/dataLoaders.py::_ffLoadExcelValue.c
legacy-variable	vaibify/gui/dataLoaders.py::_ffLoadExcelValue.r
legacy-variable	vaibify/gui/dataLoaders.py::_ffLoadExcelValue.sheet
legacy-variable	vaibify/gui/dataLoaders.py::_ffLoadExcelValue.workbook
legacy-variable	vaibify/gui/dataLoaders.py::_ffLoadFastqValue.c
legacy-variable	vaibify/gui/dataLoaders.py::_ffLoadFitsValue.hdu
legacy-variable	vaibify/gui/dataLoaders.py::_ffLoadFitsValue.hduList
legacy-variable	vaibify/gui/dataLoaders.py::_ffLoadFortranValue.fortranFile
legacy-variable	vaibify/gui/dataLoaders.py::_ffLoadHdf5Value.datasetHdf5
legacy-variable	vaibify/gui/dataLoaders.py::_ffLoadIpacValue.table
legacy-variable	vaibify/gui/dataLoaders.py::_ffLoadJsonlValue.r
legacy-variable	vaibify/gui/dataLoaders.py::_ffLoadMatlabValue.k
legacy-variable	vaibify/gui/dataLoaders.py::_ffLoadMultitableValue.r
legacy-variable	vaibify/gui/dataLoaders.py::_ffLoadNpzValue.archiveNpz
legacy-variable	vaibify/gui/dataLoaders.py::_ffLoadParquetValue.table
legacy-variable	vaibify/gui/dataLoaders.py::_ffLoadPcapValue.p
legacy-variable	vaibify/gui/dataLoaders.py::_ffLoadRdataValue.dfData
legacy-variable	vaibify/gui/dataLoaders.py::_ffLoadSasValue.dfData
legacy-variable	vaibify/gui/dataLoaders.py::_ffLoadSpssValue.dfData
legacy-variable	vaibify/gui/dataLoaders.py::_ffLoadStataValue.dfData
legacy-variable	vaibify/gui/dataLoaders.py::_ffLoadTfrecordValue.r
legacy-variable	vaibify/gui/dataLoaders.py::_ffLoadVotableValue.table
legacy-variable	vaibify/gui/dataLoaders.py::_ffLoadVotableValue.votable
legacy-variable	vaibify/gui/dataLoaders.py::_ffLoadVtkValue.mesh
legacy-variable	vaibify/gui/dataLoaders.py::_ffLoadWhitespaceValue.r
legacy-variable	vaibify/gui/dataLoaders.py::_ffNavigateJsonValue.value
legacy-variable	vaibify/gui/dataLoaders.py::_fnParseAccessIndexField.x
legacy-variable	vaibify/gui/dataLoaders.py::_ftOpenCsvReader.reader
legacy-variable	vaibify/gui/dependencyScanner.py::_flistMatchPatterns.resultMatch
legacy-variable	vaibify/gui/dockerStatus.py::fdictDetectDockerRuntime.resultContext
legacy-variable	vaibify/gui/fileStatusManager.py::_fiParseUtcTimestamp.dtParsed
legacy-variable	vaibify/gui/fileStatusManager.py::_fiParseUtcTimestamp.dtUtc
legacy-variable	vaibify/gui/gitStatus.py::_fbIsGitRepo.result
legacy-variable	vaibify/gui/gitStatus.py::_fsHeadSha.result
legacy-variable	vaibify/gui/gitStatus.py::fdictGitStatusForWorkspace.result
legacy-variable	vaibify/gui/hashStaleness.py::_fiCoerceMtime::mtimeValue
legacy-variable	vaibify/gui/hostControlChannel.py::_fbPeerIsThisUser::writer
legacy-variable	vaibify/gui/hostControlChannel.py::_fbaReadResponseLine.byteChunk
legacy-variable	vaibify/gui/hostControlChannel.py::_fdictAnswerOneRequest.byteRequest
legacy-variable	vaibify/gui/hostControlChannel.py::_fdictAnswerOneRequest::reader
legacy-variable	vaibify/gui/hostControlChannel.py::_fdictHandleMintTransfer.valueExpectedGeneration
legacy-variable	vaibify/gui/hostControlChannel.py::_fnAssertBindTargetSafe.statResult
legacy-variable	vaibify/gui/hostControlChannel.py::_fnServeHostControlConnection::reader
legacy-variable	vaibify/gui/hostControlChannel.py::_fnServeHostControlConnection::writer
legacy-variable	vaibify/gui/hostControlChannel.py::_ftParseDarwinPeerCredentials::byteCredentials
legacy-variable	vaibify/gui/hostControlChannel.py::_ftParseLinuxPeerCredentials::byteCredentials
legacy-variable	vaibify/gui/hostControlChannel.py::fdictSendHostControlRequest.byteResponse
legacy-variable	vaibify/gui/hostControlChannel.py::fnRegisterHostControlChannel.fnStartHostControlServer.serverControl
legacy-variable	vaibify/gui/hostControlChannel.py::fnRegisterHostControlChannel.fnStopHostControlServer.serverControl
legacy-variable	vaibify/gui/llmInvoker.py::fsGenerateViaApi.client
legacy-variable	vaibify/gui/llmInvoker.py::fsGenerateViaApi.message
legacy-variable	vaibify/gui/mtimeCache.py::fdictLoadCache.handle
legacy-variable	vaibify/gui/mtimeCache.py::fnSaveCache.handle
legacy-variable	vaibify/gui/pathContract.py::fdictAbsKeysToRepoRelative.value
legacy-variable	vaibify/gui/personalLayerManager.py::fdictComputeHashCommitment.hasher
legacy-variable	vaibify/gui/pipelineLogger.py::_ffBuildFlushingCallback::stateWriter
legacy-variable	vaibify/gui/pipelineLogger.py::_fnDispatchEventToWriter::stateWriter
legacy-variable	vaibify/gui/pipelineLogger.py::_fnFinalizeRun::stateWriter
legacy-variable	vaibify/gui/pipelineLogger.py::_fnUpdatePipelineState::stateWriter
legacy-variable	vaibify/gui/pipelineRunner.py::_ffBuildStreamingChunkEmitter::loopMain
legacy-variable	vaibify/gui/pipelineRunner.py::_fiRunStepsAndLog.eventStopHeartbeat
legacy-variable	vaibify/gui/pipelineRunner.py::_fiRunStepsAndLog.stateWriter
legacy-variable	vaibify/gui/pipelineRunner.py::_fnCancelTimerFlush.handleTimer
legacy-variable	vaibify/gui/pipelineRunner.py::_fnFlushBatchFromWorker.future
legacy-variable	vaibify/gui/pipelineRunner.py::_fnFlushBatchFromWorker::loopMain
legacy-variable	vaibify/gui/pipelineRunner.py::_fnRunHeartbeatLoop::eventStop
legacy-variable	vaibify/gui/pipelineRunner.py::_fnScheduleTimerFlush::loopMain
legacy-variable	vaibify/gui/pipelineRunner.py::_ftBuildBatchingEmitter::loopMain
legacy-variable	vaibify/gui/pipelineRunner.py::_ftInitializeRunState.stateWriter
legacy-variable	vaibify/gui/pipelineRunner.py::_ftRunSingleCommand.loopMain
legacy-variable	vaibify/gui/pipelineRunner.py::_fthreadStartHeartbeat::eventStop
legacy-variable	vaibify/gui/pipelineServer.py::__getattr__.value
legacy-variable	vaibify/gui/pipelineServer.py::_fnLaunchDependencyScan.loop
legacy-variable	vaibify/gui/pipelineServer.py::fdictFilterNonNone.k
legacy-variable	vaibify/gui/pipelineServer.py::fdictFilterNonNone.v
legacy-variable	vaibify/gui/pipelineServer.py::fnTerminalInputLoop.message
legacy-variable	vaibify/gui/pipelineState.py::StateWriter._fnArmStepResultDebounce.timerNew
legacy-variable	vaibify/gui/pipelineState.py::StateWriter._fnDrainCoalesced.item
legacy-variable	vaibify/gui/pipelineState.py::StateWriter._fnRunWriter.item
legacy-variable	vaibify/gui/pipelineState.py::_ffCoerceStateBudget::value
legacy-variable	vaibify/gui/pipelineState.py::fbHeartbeatIsStale.dtBeat
legacy-variable	vaibify/gui/pipelineState.py::fdictActiveStepBudgetStatus.dtStarted
legacy-variable	vaibify/gui/pipelineState.py::fsBuildHeartbeatStaleReason.dtBeat
legacy-variable	vaibify/gui/pipelineUtils.py::fbStepIsInteractive.valueFlag
legacy-variable	vaibify/gui/randomnessLint.py::_fbFileContainsRegex.regex
legacy-variable	vaibify/gui/registryRoutes.py::_fdictBuildHostEntry::entry
legacy-variable	vaibify/gui/registryRoutes.py::_fdictBuildHostFileEntry::entry
legacy-variable	vaibify/gui/registryRoutes.py::_flistSortDirectoryEntries.e
legacy-variable	vaibify/gui/registryRoutes.py::_fnRequireLimitWithinRange::numberMaximum
legacy-variable	vaibify/gui/registryRoutes.py::_fnRequireLimitWithinRange::numberMinimum
legacy-variable	vaibify/gui/registryRoutes.py::_fnRequireLimitWithinRange::numberValue
legacy-variable	vaibify/gui/registryRoutes.py::_fnUpdateYamlNumberField::numberValue
legacy-variable	vaibify/gui/registryRoutes.py::flistQueryHostDirectory.entry
legacy-variable	vaibify/gui/routeContext.py::RouteContext.__setitem__::value
legacy-variable	vaibify/gui/routeContext.py::RouteContext.get::default
legacy-variable	vaibify/gui/routeContext.py::RouteContext.setdefault::default
legacy-variable	vaibify/gui/routeScope.py::_fbIsApplicableMutatingRoute::route
legacy-variable	vaibify/gui/routeScope.py::fnValidateRouteScopesOrRaise.route
legacy-variable	vaibify/gui/routes/falsificationRoutes.py::_fdictParseSummaryOutput::resultSummary
legacy-variable	vaibify/gui/routes/falsificationRoutes.py::_fdictSummarizeMutationSession.resultSummary
legacy-variable	vaibify/gui/routes/figureRoutes.py::_fnRegisterFigure.fresponseCheckFigure.p
legacy-variable	vaibify/gui/routes/pipelineRoutes.py::_fiCoercePollMtime::mtimeValue
legacy-variable	vaibify/gui/routes/pipelineRoutes.py::_ftRunManifestVerify::manifestWriter
legacy-variable	vaibify/gui/routes/reproducibilityRoutes.py::_fdictKickOffVerification.coroutineWorker
legacy-variable	vaibify/gui/routes/reproducibilityRoutes.py::_fnRequireScalarType.typeOption
legacy-variable	vaibify/gui/routes/sessionRoutes.py::_fbIsPortAcceptingConnections.sock
legacy-variable	vaibify/gui/routes/sessionRoutes.py::_fnPruneDeadChildren.child
legacy-variable	vaibify/gui/routes/sessionRoutes.py::_fnRegisterSpawn.fdictSpawnSession.child
legacy-variable	vaibify/gui/routes/sessionRoutes.py::_fnRegisterSpawnedChildShutdown.fnTerminateSpawnedChildren.child
legacy-variable	vaibify/gui/routes/settingsRoutes.py::_fnCommitSettingsUpdate.value
legacy-variable	vaibify/gui/routes/settingsRoutes.py::_fnRegisterLogRoutes.flistLogs.e
legacy-variable	vaibify/gui/routes/syncRoutes.py::_fbRestoreContainerSnapshot::syncDispatcher
legacy-variable	vaibify/gui/routes/syncRoutes.py::_fdictHandleOverleafPushRequest::syncDispatcher
legacy-variable	vaibify/gui/routes/syncRoutes.py::_fdictHandlePullManuscript::syncDispatcher
legacy-variable	vaibify/gui/routes/syncRoutes.py::_fdictResolveZenodoMetadataForArchive.c
legacy-variable	vaibify/gui/routes/syncRoutes.py::_fdictRunOverleafPushFlow::syncDispatcher
legacy-variable	vaibify/gui/routes/syncRoutes.py::_fdictStoreCredentialSafely::syncDispatcher
legacy-variable	vaibify/gui/routes/syncRoutes.py::_flistManuscriptMirrorPaths._resultDetail
legacy-variable	vaibify/gui/routes/syncRoutes.py::_flistManuscriptMirrorPaths::syncDispatcher
legacy-variable	vaibify/gui/routes/syncRoutes.py::_fnCleanupCredential::syncDispatcher
legacy-variable	vaibify/gui/routes/syncRoutes.py::_fnDispatchStore::syncDispatcher
legacy-variable	vaibify/gui/routes/syncRoutes.py::_fnDropContainerSnapshot::syncDispatcher
legacy-variable	vaibify/gui/routes/syncRoutes.py::_fnEvictExpiredPushResults._result
legacy-variable	vaibify/gui/routes/syncRoutes.py::_fnRegisterDag.fresponseGetDag.result
legacy-variable	vaibify/gui/routes/syncRoutes.py::_fnRegisterDagExport.fresponseExportDag.result
legacy-variable	vaibify/gui/routes/syncRoutes.py::_fnRegisterOverleafMirrorRefresh.fdictRefreshMirror.result
legacy-variable	vaibify/gui/routes/syncRoutes.py::_fnRollBackFailedCredential::syncDispatcher
legacy-variable	vaibify/gui/routes/syncRoutes.py::_ftPerformZenodoArchive::syncDispatcher
legacy-variable	vaibify/gui/routes/syncRoutes.py::_ftRunOverleafPushCall::syncDispatcher
legacy-variable	vaibify/gui/routes/syncRoutes.py::_ftRunOverleafValidation::syncDispatcher
legacy-variable	vaibify/gui/routes/syncRoutes.py::_ftRunServiceValidation::syncDispatcher
legacy-variable	vaibify/gui/routes/syncRoutes.py::_ftSnapshotContainerCredential::syncDispatcher
legacy-variable	vaibify/gui/routes/systemRoutes.py::_fdictProbeContainerReadiness.exception
legacy-variable	vaibify/gui/routes/systemRoutes.py::_fdictProbeWithTimeout.executorPool
legacy-variable	vaibify/gui/routes/systemRoutes.py::_fdictProbeWithTimeout.future
legacy-variable	vaibify/gui/serverLifespan.py::_fnInvokeMaybeAsync.objectResult
legacy-variable	vaibify/gui/serverLifespan.py::_fnRegisterDefaultThreadPoolExecutor.fnInstallExecutor.executorIo
legacy-variable	vaibify/gui/serverLifespan.py::_fnRegisterDefaultThreadPoolExecutor.fnShutdownExecutor.executorIo
legacy-variable	vaibify/gui/serverMiddleware.py::ActivityTrackingMiddleware.dispatch::call_next
legacy-variable	vaibify/gui/serverMiddleware.py::SecurityHeadersMiddleware.dispatch::call_next
legacy-variable	vaibify/gui/serverMiddleware.py::SessionTokenMiddleware.dispatch::call_next
legacy-variable	vaibify/gui/serverMiddleware.py::_fresponseServeAdmittedAgentRequest::call_next
legacy-variable	vaibify/gui/serverMiddleware.py::_fsRouteTemplateForRequest.route
legacy-variable	vaibify/gui/sessionLifecycle.py::fnScheduleConnectionFencing.loopRunning
legacy-variable	vaibify/gui/staleOutputDetector.py::_ffParseMtime::value
legacy-variable	vaibify/gui/stateContract.py::_flistExcludedPathsFromWorkflow.p
legacy-variable	vaibify/gui/syncDispatcher.py::_fdictBuildApiMetadata.k
legacy-variable	vaibify/gui/syncDispatcher.py::_flistFilterByExtension::frozensetExtensions
legacy-variable	vaibify/gui/syncDispatcher.py::fdictComputeContainerDigests.p
legacy-variable	vaibify/gui/testGenerator.py::_fdictBuildIntegrityStandards.r
legacy-variable	vaibify/gui/testGenerator.py::_fdictBuildQualitativeStandards.r
legacy-variable	vaibify/gui/testGenerator.py::_fnWarnIfAllUnloadable.r
legacy-variable	vaibify/gui/testGenerator.py::_fsClassifyStochasticity.r
legacy-variable	vaibify/gui/transcriptSanitizer.py::_fsRedactLinePatterns.secretFound
legacy-variable	vaibify/gui/workflowManager.py::_ffCoerceWallClockBudget::value
legacy-variable	vaibify/gui/workflowManager.py::_fnDepCacheSet::value
legacy-variable	vaibify/gui/workflowManager.py::_fnValidateZenodoMetadata.c
legacy-variable	vaibify/gui/workflowManager.py::fnUpdateStep.value
legacy-variable	vaibify/gui/workflowManager.py::fsRemapStepReferences.fsReplaceMatch::resultMatch
legacy-variable	vaibify/gui/workflowManager.py::fsResolveVariables.fsReplaceMatch::resultMatch
legacy-variable	vaibify/gui/workflowMigrations.py::fnRewritePositionalToSymbolic.fsReplaceMatch::resultMatch
legacy-variable	vaibify/reproducibility/_hashing.py::_fnFeedHasher::hasher
legacy-variable	vaibify/reproducibility/_hashing.py::fsHashChunkIteratorSha256.hasher
legacy-variable	vaibify/reproducibility/_hashing.py::fsHashFileObjectSha256.hasher
legacy-variable	vaibify/reproducibility/_hashing.py::fsHashFileSha256.hasher
legacy-variable	vaibify/reproducibility/aiProvenanceStamp.py::_fbCapturedAtPlausible.dtCaptured
legacy-variable	vaibify/reproducibility/arxivClient.py::_fnExtractTarballSafely.memberTar
legacy-variable	vaibify/reproducibility/arxivClient.py::_fnExtractTarballSafely.tarballHandle
legacy-variable	vaibify/reproducibility/arxivClient.py::_fnRejectUnsafeTarMemberKinds::memberTar
legacy-variable	vaibify/reproducibility/arxivClient.py::_fnValidateTarMember::memberTar
legacy-variable	vaibify/reproducibility/arxivClient.py::_fsParseLatestVersion.elementEntry
legacy-variable	vaibify/reproducibility/arxivClient.py::_fsParseLatestVersion.elementId
legacy-variable	vaibify/reproducibility/arxivClient.py::_fsParseLatestVersion.elementRoot
legacy-variable	vaibify/reproducibility/credentialRedactor.py::_fsScrubUrlQueryParts.result
legacy-variable	vaibify/reproducibility/dataArchiver.py::_fnCleanupFailedDraft::clientZenodo
legacy-variable	vaibify/reproducibility/dataArchiver.py::_fnUploadAllFiles::clientZenodo
legacy-variable	vaibify/reproducibility/dataArchiver.py::fnUploadToZenodo.clientZenodo
legacy-variable	vaibify/reproducibility/determinismGate.py::_fbAttributeIsClock.nodeValue
legacy-variable	vaibify/reproducibility/determinismGate.py::_fbAttributeIsClock::nodeAttr
legacy-variable	vaibify/reproducibility/determinismGate.py::_fbCallIsOsUrandom.nodeFn
legacy-variable	vaibify/reproducibility/determinismGate.py::_fbCallIsOsUrandom.nodeValue
legacy-variable	vaibify/reproducibility/determinismGate.py::_fbCallIsOsUrandom::nodeCall
legacy-variable	vaibify/reproducibility/determinismGate.py::_fbCallIsSeedFunction.nodeFn
legacy-variable	vaibify/reproducibility/determinismGate.py::_fbCallIsSeedFunction::nodeCall
legacy-variable	vaibify/reproducibility/determinismGate.py::_fbExprUsesClock.nodeChild
legacy-variable	vaibify/reproducibility/determinismGate.py::_fbExprUsesClock::nodeExpr
legacy-variable	vaibify/reproducibility/determinismGate.py::_flistFindClockSeeds.node
legacy-variable	vaibify/reproducibility/determinismGate.py::_flistFindClockSeeds.nodeArg
legacy-variable	vaibify/reproducibility/determinismGate.py::_flistFindClockSeeds.treeAst
legacy-variable	vaibify/reproducibility/githubAuth.py::_ftFetchLoginFresh.resp
legacy-variable	vaibify/reproducibility/githubAuth.py::ftParseOwnerRepoFromRemoteUrl.pattern
legacy-variable	vaibify/reproducibility/githubMirror.py::_AuthStrippingRedirectHandler.redirect_request.objectNew
legacy-variable	vaibify/reproducibility/githubMirror.py::_AuthStrippingRedirectHandler.redirect_request::code
legacy-variable	vaibify/reproducibility/githubMirror.py::_AuthStrippingRedirectHandler.redirect_request::fp
legacy-variable	vaibify/reproducibility/githubMirror.py::_AuthStrippingRedirectHandler.redirect_request::headers
legacy-variable	vaibify/reproducibility/githubMirror.py::_AuthStrippingRedirectHandler.redirect_request::msg
legacy-variable	vaibify/reproducibility/githubMirror.py::_AuthStrippingRedirectHandler.redirect_request::newurl
legacy-variable	vaibify/reproducibility/githubMirror.py::_AuthStrippingRedirectHandler.redirect_request::req
legacy-variable	vaibify/reproducibility/githubMirror.py::_fhttpresponseOpenRequest::objectRequest
legacy-variable	vaibify/reproducibility/githubMirror.py::_frequestBuildGithub.objectRequest
legacy-variable	vaibify/reproducibility/githubMirror.py::_fsHashOneRemote.objectRequest
legacy-variable	vaibify/reproducibility/githubMirror.py::_fsHashOneRemote.objectResponse
legacy-variable	vaibify/reproducibility/githubMirror.py::_fsHashResponseStream::objectResponse
legacy-variable	vaibify/reproducibility/githubWorkflow.py::_fsRenderTemplate.environment
legacy-variable	vaibify/reproducibility/githubWorkflow.py::_fsRenderTemplate.templateObject
legacy-variable	vaibify/reproducibility/levelGates.py::_fbCachedSyncStatusFresh.dtVerified
legacy-variable	vaibify/reproducibility/levelGates.py::_fbCommandsInvokeBinary.regexBinary
legacy-variable	vaibify/reproducibility/levelGates.py::_fsModTimesFingerprint.value
legacy-variable	vaibify/reproducibility/levelGates.py::fbWorkflowDeclaresBinaries.e
legacy-variable	vaibify/reproducibility/overleafMirror.py::_fnClonePartial.result
legacy-variable	vaibify/reproducibility/overleafMirror.py::_fnFetchOrigin.result
legacy-variable	vaibify/reproducibility/overleafMirror.py::_fnFullClone.result
legacy-variable	vaibify/reproducibility/overleafMirror.py::_fnResetToOriginHead.result
legacy-variable	vaibify/reproducibility/overleafMirror.py::_fsStrippedStderr::result
legacy-variable	vaibify/reproducibility/overleafMirror.py::flistListMirrorTree.result
legacy-variable	vaibify/reproducibility/overleafMirror.py::fsComputeBlobSha.handleFile
legacy-variable	vaibify/reproducibility/overleafMirror.py::fsComputeBlobSha.hasher
legacy-variable	vaibify/reproducibility/overleafMirror.py::fsReadMirrorHeadSha.result
legacy-variable	vaibify/reproducibility/overleafSync.py::_fnAddLsRemoteParser.sub
legacy-variable	vaibify/reproducibility/overleafSync.py::_fnAddLsRemoteParser::subparsers
legacy-variable	vaibify/reproducibility/overleafSync.py::_fnAddPullParser.sub
legacy-variable	vaibify/reproducibility/overleafSync.py::_fnAddPullParser::subparsers
legacy-variable	vaibify/reproducibility/overleafSync.py::_fnAddPushAnnotatedParser.sub
legacy-variable	vaibify/reproducibility/overleafSync.py::_fnAddPushAnnotatedParser::subparsers
legacy-variable	vaibify/reproducibility/overleafSync.py::_fnAddPushParser.sub
legacy-variable	vaibify/reproducibility/overleafSync.py::_fnAddPushParser::subparsers
legacy-variable	vaibify/reproducibility/overleafSync.py::_fparserBuildCommandLine.subparsers
legacy-variable	vaibify/reproducibility/overleafSync.py::fdictOverleafRemotePathsAt.entryRecorded
legacy-variable	vaibify/reproducibility/provenanceTracker.py::_fsHashFileContents.hasher
legacy-variable	vaibify/reproducibility/repoFiles.py::_RepoLockHolder.__exit__::classExc
legacy-variable	vaibify/reproducibility/repoFiles.py::_RepoLockHolder.__exit__::traceback
legacy-variable	vaibify/reproducibility/repoFiles.py::_RepoLockHolder.__exit__::valueExc
legacy-variable	vaibify/reproducibility/repoFiles.py::_fsHashHostFileOrNone.hasher
legacy-variable	vaibify/reproducibility/scheduledReverify.py::_ffMeasureSecondsSinceIso.dtStamp
legacy-variable	vaibify/reproducibility/scheduledReverify.py::_fnBumpSyncEpochForVerifiedContainers.entryWorkflow
legacy-variable	vaibify/reproducibility/scheduledReverify.py::_ftResolveWorkflowEntry::entryWorkflow
legacy-variable	vaibify/reproducibility/scheduledReverify.py::fdictRunReverifyOnce.entryWorkflow
legacy-variable	vaibify/reproducibility/zenodoClient.py::_fdictBuildUploadHeaders::clientZenodo
legacy-variable	vaibify/reproducibility/zenodoClient.py::_fdictGetRecordSafely::clientZenodo
legacy-variable	vaibify/reproducibility/zenodoClient.py::_fdictHashSelectedFiles::clientZenodo
legacy-variable	vaibify/reproducibility/zenodoClient.py::_fiterReadChunks::barProgress
legacy-variable	vaibify/reproducibility/zenodoClient.py::_fnReraiseRecordError.clsError
legacy-variable	vaibify/reproducibility/zenodoClient.py::_fnReraiseRecordError::excOriginal
legacy-variable	vaibify/reproducibility/zenodoClient.py::_fnStreamDownload::clientZenodo
legacy-variable	vaibify/reproducibility/zenodoClient.py::_fnStreamUpload.barProgress
legacy-variable	vaibify/reproducibility/zenodoClient.py::_fnStreamUpload::clientZenodo
legacy-variable	vaibify/reproducibility/zenodoClient.py::_fnWriteStreamToFile.barProgress
legacy-variable	vaibify/reproducibility/zenodoClient.py::_fsHashRemoteFile::clientZenodo
legacy-variable	vaibify/reproducibility/zenodoClient.py::fdictFetchRemoteHashes.clientResolved
legacy-variable	vaibify/reproducibility/zenodoClient.py::fdictFetchRemoteHashes::clientZenodo
legacy-variable	vaibify/templates/workflow/GenerateSamples/generateSamples.py::fdaDrawSamples.generatorRandom
legacy-variable	vaibify/templates/workflow/GenerateSamples/generateSamples.py::fnParseArgumentsAndRun.arguments
legacy-variable	vaibify/templates/workflow/PlotHistogram/plotHistogram.py::fnParseArgumentsAndRun.arguments
legacy-variable	vaibify/testing/standards.py::_fdictLoadNpz.archiveNpz
legacy-variable	vaibify/testing/standards.py::_flistStandardsFromJson.value
legacy-variable	vaibify/testing/standards.py::_flistStandardsFromJsonList.v
legacy-variable	vaibify/testing/standards.py::_flistStandardsFromScalarJson::value
legacy-variable	vaibify/testing/stochasticDetector.py::ftDetectStochastic.reConsumption
legacy-variable	vaibify/testing/stochasticDetector.py::ftDetectStochastic.reSeed
"""


def _fsetParseVariableSeed():
    setPairs = set()
    for sLine in S_FROZEN_VARIABLE_SEED_TEXT.splitlines():
        if not sLine.strip():
            continue
        sDebtClass, sIdentity = sLine.split("\t", 1)
        setPairs.add((sIdentity, sDebtClass))
    return frozenset(setPairs)


SET_FROZEN_SEED_PAIRS = SET_FROZEN_SEED_PAIRS | _fsetParseVariableSeed()


@pytest.fixture(scope="module")
def listScannedRows():
    listRows, _ = tool.flistScanPackage()
    return listRows


def _flistNewPairsOfClass(listScannedRows, sDebtClass):
    return sorted(
        sIdentity for sIdentity, sRowClass, _ in listScannedRows
        if sRowClass == sDebtClass
        and (sIdentity, sRowClass) not in SET_FROZEN_SEED_PAIRS
    )


def _fnAssertNoNewDebt(listScannedRows, sDebtClass, sContractStatement):
    listNew = _flistNewPairsOfClass(listScannedRows, sDebtClass)
    assert not listNew, (
        f"{sContractStatement}\nNew {sDebtClass} violations (not in the "
        f"frozen seed):\n  " + "\n  ".join(listNew)
    )


def testFunctionNamesCarryReturnTypePrefix(listScannedRows):
    _fnAssertNoNewDebt(
        listScannedRows, "legacy-name",
        "Function names must be f + a prefix from the closed two-tier "
        "vocabulary + a capitalized description (AGENTS.md style guide).")


def testFnPrefixedFunctionsReturnNoValue(listScannedRows):
    _fnAssertNoNewDebt(
        listScannedRows, "legacy-fn-return",
        "fn-prefixed functions return nothing; a value-returning "
        "function declares its return type in its prefix.")


def testYieldingFunctionsCarryIterOrContext(listScannedRows):
    _fnAssertNoNewDebt(
        listScannedRows, "legacy-yield",
        "A generator function is prefixed iter; a "
        "@contextmanager-decorated one is prefixed context.")


def testLiteralReturnsMatchThePrefix(listScannedRows):
    _fnAssertNoNewDebt(
        listScannedRows, "legacy-literal-return",
        "A literal return must have the type the function's prefix "
        "declares.")


def testReturnAnnotationsMatchThePrefix(listScannedRows):
    _fnAssertNoNewDebt(
        listScannedRows, "legacy-return-annotation",
        "A return annotation must agree with the function's prefix "
        "(fn is None; context annotates its undecorated generator).")


def testPrefixedNamesAgreeWithAnnotations(listScannedRows):
    _fnAssertNoNewDebt(
        listScannedRows, "legacy-annotation-mismatch",
        "An annotated variable whose name claims a cast must be "
        "annotated with that cast; object and Any never agree.")


def testVariableBindingsCarryCastPrefixes(listScannedRows):
    _fnAssertNoNewDebt(
        listScannedRows, "legacy-variable",
        "Every binding name carries a variable cast prefix (doctrine "
        "2026-08-06); a name outside the vocabulary spells nothing.")


def testScannerCatchesUnprefixedBinding():
    assert _fbSyntheticCaught(
        "def fnWork():\n    banana = 1\n", "legacy-variable", "banana")


def testPrefixVocabularyIsClosed():
    assert tool.DICT_TIER_ONE_AGREEMENT == DICT_TIER_ONE_AGREEMENT_COPY, (
        "Tier-1 vocabulary drifted between the tool and this copy; "
        "growing a tier requires editing both plus a ruling.")
    assert tool.DICT_ARRAY_ELEMENT_CAST == DICT_ARRAY_ELEMENT_CAST_COPY
    assert tool.DICT_TIER_TWO_REGISTRY == DICT_TIER_TWO_REGISTRY_COPY, (
        "Tier-2 registry drifted between the tool and this copy; "
        "growing a tier requires editing both plus a ruling.")


# The ONLY names allowed to escape the naming contract are names a
# FOREIGN contract owns: the caller looks up the literal string, so
# conforming is impossible in principle (like renaming a FITS keyword).
# This closed table is the whitelist's own whitelist -- an
# interface-method exemption whose method name is not here fails, so a
# lazy future entry cannot hide behind the category.
DICT_FOREIGN_PROTOCOL_NAMES = {
    "dispatch": "starlette BaseHTTPMiddleware override",
    "read": "file-like protocol consumed by tarfile",
    "emit": "logging.Handler override",
    "filter": "logging.Filter override",
    "redirect_request": "urllib HTTPRedirectHandler override",
    "get_route_handler": "fastapi APIRoute override",
    "get": "dict protocol (RouteContext mapping compatibility)",
    "setdefault": "dict protocol (RouteContext mapping compatibility)",
    "pop": "dict protocol (RouteContext mapping compatibility)",
}


def testInterfaceExemptionsNameForeignProtocols():
    dictInventory = json.loads(PATH_INVENTORY.read_text())
    listUnknown = []
    setUsedNames = set()
    for dictRow in dictInventory["listRows"]:
        if dictRow["sDebtClass"] != "interface-method":
            continue
        sMethodName = dictRow["sIdentity"].split("::")[1].split(".")[-1]
        setUsedNames.add(sMethodName)
        if sMethodName not in DICT_FOREIGN_PROTOCOL_NAMES:
            listUnknown.append(dictRow["sIdentity"])
    assert not listUnknown, (
        "interface-method exemptions whose names no known foreign "
        "protocol owns -- a lazy exemption cannot hide behind the "
        "category:\n  " + "\n  ".join(listUnknown)
    )
    setStale = set(DICT_FOREIGN_PROTOCOL_NAMES) - setUsedNames
    assert not setStale, (
        f"protocol table entries no exemption uses (prune them): "
        f"{sorted(setStale)}"
    )


def testCurrentInventoryIsWithinTheFrozenSeed():
    dictInventory = json.loads(PATH_INVENTORY.read_text())
    listOutside = sorted(
        f"{dictRow['sDebtClass']}: {dictRow['sIdentity']}"
        for dictRow in dictInventory["listRows"]
        if (dictRow["sIdentity"], dictRow["sDebtClass"])
        not in SET_FROZEN_SEED_PAIRS
    )
    assert not listOutside, (
        "Inventory rows outside the frozen seed -- new debt or a "
        "reclassification without the reviewed two-file edit:\n  "
        + "\n  ".join(listOutside)
    )


def testDebtCountsEqualTheRecordedBudgets(listScannedRows):
    dictScanned = {sClass: 0 for sClass in DICT_BUDGETS}
    for _, sRowClass, _ in listScannedRows:
        if sRowClass in dictScanned:
            dictScanned[sRowClass] += 1
    assert dictScanned == DICT_BUDGETS, (
        "Debt counts differ from the recorded budgets. Above a budget "
        "means a seeded violation was reintroduced; below means a fix "
        "landed without lowering the constant in the same commit. "
        f"scanned={dictScanned} budgets={DICT_BUDGETS}")
    dictInventory = json.loads(PATH_INVENTORY.read_text())
    assert dictInventory["dictBudgets"] == DICT_BUDGETS


def testInventoryFileMatchesRegeneration():
    dictCommitted = json.loads(PATH_INVENTORY.read_text())
    assert dictCommitted == tool.fdictGenerateInventory(), (
        "tests/styleInventory.json does not match a fresh regeneration; "
        "run python tools/generateStyleInventory.py --write and review "
        "the diff.")


def testReviewTrackedMisnamingsStillExist():
    for sIdentity, _ in tool.LIST_REVIEW_TRACKED_MISNAMINGS:
        sPath, sQualified = sIdentity.split("::", 1)
        sFunctionName = sQualified.split(".")[-1]
        treeModule = ast.parse((PATH_REPOSITORY / sPath).read_text())
        listNames = [node.name for node in ast.walk(treeModule)
                     if isinstance(node, (ast.FunctionDef,
                                          ast.AsyncFunctionDef))]
        assert sFunctionName in listNames, (
            f"{sIdentity} is review-tracked as a misnaming but no longer "
            "exists; delete its LIST_REVIEW_TRACKED_MISNAMINGS entry in "
            "the tool -- the record self-prunes.")


# ---------------------------------------------------------------------------
# Adversarial self-tests: known-bad synthetic sources each scanner check
# must catch. These falsify the scanner, not the codebase.
# ---------------------------------------------------------------------------


def _flistScanSyntheticSource(sSource):
    scanner = tool.StyleViolationScanner("synthetic.py")
    scanner.visit(ast.parse(sSource))
    return scanner.listViolations


def _fbSyntheticCaught(sSource, sDebtClass, sNameFragment):
    return any(
        sRowClass == sDebtClass and sNameFragment in sIdentity
        for sIdentity, sRowClass, _ in _flistScanSyntheticSource(sSource)
    )


@pytest.mark.falsification
def testScannerCatchesFnReturningValue():
    """A value-returning fn* must be caught.

    Kills: disabling the fn-return record in the scanner
    (if listValueReturns -> if False).
    """
    assert _fbSyntheticCaught(
        "def fnLeaky():\n    return 1\n", "legacy-fn-return", "fnLeaky")


def testScannerCatchesFnYielding():
    assert _fbSyntheticCaught(
        "def fnYields():\n    yield 1\n", "legacy-yield", "fnYields")


@pytest.mark.falsification
def testScannerCatchesLiteralReturnMismatch():
    """A literal return contradicting the prefix must be caught.

    Kills: making fbLiteralAgreesWithPrefix unconditionally True.
    """
    assert _fbSyntheticCaught(
        "def fbFlag():\n    return []\n", "legacy-literal-return", "fbFlag")


@pytest.mark.falsification
def testScannerCatchesReturnAnnotationMismatch():
    """A return annotation contradicting the prefix must be caught.

    Kills: short-circuiting the return-annotation agreement branch
    (if False and not all(...)).
    """
    assert _fbSyntheticCaught(
        "def fsBuild() -> dict:\n    return fdictCompute()\n",
        "legacy-return-annotation", "fsBuild")


def testScannerCatchesVariableAnnotationMismatch():
    assert _fbSyntheticCaught(
        "dictThing: int = 0\n", "legacy-annotation-mismatch", "dictThing")


@pytest.mark.falsification
def testScannerCatchesBarePrefixlessFunction():
    """A bare f+Capital name with no type letters must be caught.

    Kills: replacing the legacy-name record with pass in the scanner.
    """
    assert _fbSyntheticCaught(
        "def fReadValue():\n    pass\n", "legacy-name", "fReadValue")


def testScannerCatchesUnregisteredPrefix():
    assert _fbSyntheticCaught(
        "def fzzUnregistered():\n    pass\n", "legacy-name",
        "fzzUnregistered")


@pytest.mark.falsification
def testScannerCatchesMisprefixedContextManager():
    """A contextmanager-decorated function not named context must be caught.

    Kills: disabling the decorated branch of the yield rules
    (if bContextDecorated... -> if False).
    """
    sSource = ("from contextlib import contextmanager\n"
               "@contextmanager\n"
               "def fiterWrong():\n    yield\n")
    assert _fbSyntheticCaught(sSource, "legacy-yield", "fiterWrong")


def testScannerAcceptsCorrectContextManagerAnnotation():
    sSource = ("from contextlib import contextmanager\n"
               "from typing import Iterator\n"
               "@contextmanager\n"
               "def fcontextGood() -> Iterator[str]:\n    yield ''\n")
    assert _flistScanSyntheticSource(sSource) == []


def testScannerRejectsContextManagerAnnotationOnTheGenerator():
    sSource = ("from contextlib import contextmanager\n"
               "from typing import ContextManager\n"
               "@contextmanager\n"
               "def fcontextBad() -> ContextManager[str]:\n    yield ''\n")
    assert _fbSyntheticCaught(sSource, "legacy-return-annotation",
                              "fcontextBad")


def testScannerFailsClosedOnUnparseableAnnotation():
    assert _fbSyntheticCaught(
        'sBroken: "nonsense[" = ""\n', "legacy-annotation-mismatch",
        "sBroken")


def testScannerDistinguishesSameNamedMethods():
    sSource = ("class Alpha:\n"
               "    def dispatch(self):\n        pass\n"
               "class Beta:\n"
               "    def dispatch(self):\n        pass\n")
    listIdentities = sorted(
        sIdentity for sIdentity, _, _ in _flistScanSyntheticSource(sSource))
    assert listIdentities == ["synthetic.py::Alpha.dispatch",
                              "synthetic.py::Beta.dispatch"]


def testScannerParsesLongestPrefix():
    assert tool.fsParseFunctionPrefix("fsetDivergedPaths") == "set"
    assert tool.fsParseFunctionPrefix("flistBuildThings") == "list"
    assert tool.fsParseFunctionPrefix("fsSlugFromStepName") == "s"
    assert tool.fsParseFunctionPrefix("fReadValue") is None
