"""A broad ``except`` must let a control-plane refusal through.

A refusal (``ControlPlaneRefusalError``: a mutation without a carrier
admission, a commit the guard declined) says "run ``vaibify reconcile``".
It is not an I/O failure, and a handler written to answer
conservatively when something cannot be read gives exactly that answer
to a refusal -- a workflow that quietly loses a reproducibility level,
or a route that answers "Write failed" and drops the instruction.

Narrowing the handlers does not work (the type already keeps a refusal
out of ``except OSError``); only a genuinely broad handler -- ``except
Exception``, ``except BaseException``, a bare ``except`` -- can swallow
one. So the rule is stated for exactly those, in every module that
imports ``mutationAdmission`` or ``commitCarrier`` (the modules whose
code can reach a container primitive). A broad handler passes when

* an EARLIER handler of the same ``try`` names a refusal class and
  re-raises, or
* its own body names a refusal class or calls
  ``fnReRaiseControlPlaneRefusal``, or
* it ends by re-raising what it caught.

RATCHET. Handlers that do none of these are SEEDED below as an exact
list, so a new one cannot enter and a fixed one must leave the seed in
the same commit. The seed was triaged on 2026-10-01. Each seeded handler
falls in one of these groups, none of which swallows a refusal silently
on a path whose answer depends on it:

* the try body reaches no container exec or write (host-only probes,
  background-loop bookkeeping, JSON decoding, process management);
* a read (``get_archive``) that never consults the admission gate;
* it logs or carries the exception's own text to the caller, so the
  refusal and its ``vaibify reconcile`` instruction stay visible
  (attribution and flag appends, best-effort cache writes, the sync
  verify helpers, the status-carrying workers);
* it answers in the fail-safe direction (busy, unproven, absent);
* it resolves the outcome from repository state by design (the push
  probes).

The one handler found to turn a refusal into an unrelated answer was the
project-search route, which answered "Search failed" through a sanitizer
that dropped the instruction; it was fixed and left the seed.
"""

import ast
import pathlib

import pytest

PATH_REPOSITORY = pathlib.Path(__file__).resolve().parent.parent
PATH_PACKAGE = PATH_REPOSITORY / "vaibify"

SET_GATE_MODULE_NAMES = {"mutationAdmission", "commitCarrier"}
SET_REFUSAL_NAMES = {
    "ControlPlaneRefusalError", "MutationNotAdmittedError",
    "CommitRefusedError",
}
S_REFUSAL_HELPER = "fnReRaiseControlPlaneRefusal"
SET_BROAD_EXCEPTION_NAMES = {"Exception", "BaseException"}


def _fsNameOfNode(nodeExpression):
    if isinstance(nodeExpression, ast.Name):
        return nodeExpression.id
    if isinstance(nodeExpression, ast.Attribute):
        return nodeExpression.attr
    return ""


def _fbModuleImportsAGateModule(treeModule):
    for node in ast.walk(treeModule):
        if isinstance(node, ast.ImportFrom):
            setParts = set((node.module or "").split("."))
            if setParts & SET_GATE_MODULE_NAMES:
                return True
            if any(alias.name in SET_GATE_MODULE_NAMES
                   for alias in node.names):
                return True
        elif isinstance(node, ast.Import):
            if any(
                set(alias.name.split(".")) & SET_GATE_MODULE_NAMES
                for alias in node.names
            ):
                return True
    return False


def _fbHandlerIsBroad(nodeHandler):
    if nodeHandler.type is None:
        return True
    listTypes = (
        nodeHandler.type.elts if isinstance(nodeHandler.type, ast.Tuple)
        else [nodeHandler.type]
    )
    return any(
        _fsNameOfNode(nodeType) in SET_BROAD_EXCEPTION_NAMES
        for nodeType in listTypes
    )


def _fbNodesNameARefusal(listNodes):
    for nodeRoot in listNodes:
        for node in ast.walk(nodeRoot):
            sName = _fsNameOfNode(node)
            if sName in SET_REFUSAL_NAMES or sName == S_REFUSAL_HELPER:
                return True
    return False


def _fbHandlerEndsByReRaising(nodeHandler):
    nodeLast = nodeHandler.body[-1]
    if not isinstance(nodeLast, ast.Raise):
        return False
    return nodeLast.exc is None or (
        isinstance(nodeLast.exc, ast.Name)
        and nodeLast.exc.id == nodeHandler.name
    )


def _fbHandlerPassesRefusalsThrough(nodeTry, iHandlerIndex):
    nodeHandler = nodeTry.handlers[iHandlerIndex]
    for nodeEarlier in nodeTry.handlers[:iHandlerIndex]:
        if nodeEarlier.type is not None and _fbNodesNameARefusal(
            [nodeEarlier.type],
        ) and _fbHandlerEndsByReRaising(nodeEarlier):
            return True
    return (
        _fbNodesNameARefusal(nodeHandler.body)
        or _fbHandlerEndsByReRaising(nodeHandler)
    )


class _FunctionHandlerCollector(ast.NodeVisitor):
    """Collect ``path::qualified function::ordinal`` for offending handlers."""

    def __init__(self, sRelativePath):
        self.sRelativePath = sRelativePath
        self.listScope = []
        self.dictOrdinalByScope = {}
        self.listIdentities = []

    def _fnVisitScope(self, node):
        self.listScope.append(node.name)
        self.generic_visit(node)
        self.listScope.pop()

    visit_ClassDef = _fnVisitScope
    visit_FunctionDef = _fnVisitScope
    visit_AsyncFunctionDef = _fnVisitScope

    def visit_Try(self, node):
        for iIndex, nodeHandler in enumerate(node.handlers):
            if _fbHandlerIsBroad(nodeHandler) and (
                not _fbHandlerPassesRefusalsThrough(node, iIndex)
            ):
                self._fnRecord(nodeHandler)
        self.generic_visit(node)

    def _fnRecord(self, nodeHandler):
        sScope = ".".join(self.listScope) or "<module>"
        iOrdinal = self.dictOrdinalByScope.get(sScope, 0) + 1
        self.dictOrdinalByScope[sScope] = iOrdinal
        self.listIdentities.append(
            f"{self.sRelativePath}::{sScope}::{iOrdinal}"
        )


def flistFindOffendingHandlers(sSource, sRelativePath):
    """Return the identities of broad handlers that can swallow a refusal."""
    treeModule = ast.parse(sSource)
    if not _fbModuleImportsAGateModule(treeModule):
        return []
    collector = _FunctionHandlerCollector(sRelativePath)
    collector.visit(treeModule)
    return collector.listIdentities


def _flistScanPackage():
    listIdentities = []
    for pathModule in sorted(PATH_PACKAGE.rglob("*.py")):
        listIdentities.extend(flistFindOffendingHandlers(
            pathModule.read_text(encoding="utf-8", errors="replace"),
            str(pathModule.relative_to(PATH_REPOSITORY)),
        ))
    return sorted(listIdentities)


# The exact seed: may only shrink. Regenerate nothing by hand; fix the
# handler (call fnReRaiseControlPlaneRefusal first) and delete its line.
LIST_SEEDED_OFFENDERS = [
    "vaibify/docker/dockerConnection.py::DockerConnection.fbaFetchDirectoryArchive::1",
    "vaibify/docker/dockerConnection.py::DockerConnection.fdictProbeProcessGroupMembers::1",
    "vaibify/docker/dockerConnection.py::DockerConnection.fiterStreamFile::1",
    "vaibify/docker/dockerConnection.py::DockerConnection.fnSignalProcessGroupMembers::1",
    "vaibify/docker/dockerConnection.py::DockerConnection.fsImageState::1",
    "vaibify/docker/dockerConnection.py::_fiterChunksFromTarStream::1",
    "vaibify/docker/dockerConnection.py::_fnMountTcpAdapter::1",
    "vaibify/docker/dockerConnection.py::_fnMountUnixAdapter::1",
    "vaibify/docker/dockerConnection.py::fdictReadDaemonCapacityFromClient::1",
    "vaibify/gui/appFactory.py::_fdockerCreateCouncilClientOrNone::1",
    "vaibify/gui/appFactory.py::_fnCouncilChatReaperLoop::1",
    "vaibify/gui/appFactory.py::_fnRegisterHubShutdownStopKeepAlive.fnStopAllKeepAlive::1",
    "vaibify/gui/appFactory.py::_fnStagedCopySweepLoop::1",
    "vaibify/gui/appFactory.py::_fnSweepOrphanedCredentialTests::1",
    "vaibify/gui/pipelineServer.py::_fbaFetchFallback::1",
    "vaibify/gui/pipelineServer.py::_fdictComputeConnectFileStatus::1",
    "vaibify/gui/pipelineServer.py::_fdictStaleWorkflowRefusal::1",
    "vaibify/gui/pipelineServer.py::_fnCheckSupervisedIntervalAtConnect::1",
    "vaibify/gui/pipelineServer.py::_fnPushAgentSession::1",
    "vaibify/gui/pipelineServer.py::_fnRecordDispatchAttribution::1",
    "vaibify/gui/pipelineServer.py::_fnRefreshConftestsAndMigrateMarkers::1",
    "vaibify/gui/pipelineServer.py::_fnRegisterStaticFiles.fdictBootstrapSession::1",
    "vaibify/gui/pipelineServer.py::_fnRegisterStaticFiles.fresponseRedeemTransferCapability::1",
    "vaibify/gui/pipelineServer.py::_fnSafeDispatch::1",
    "vaibify/gui/pipelineServer.py::_fnSafeDispatch::2",
    "vaibify/gui/pipelineServer.py::_fnScanDependenciesBackground::1",
    "vaibify/gui/pipelineServer.py::_fsResolveContainerUser::1",
    "vaibify/gui/pipelineServer.py::fbaFetchFigureWithFallback::1",
    "vaibify/gui/pipelineServer.py::fdictHandleConnect::1",
    "vaibify/gui/pipelineServer.py::ffBuildResilientWsCallback.fnCallback::1",
    "vaibify/gui/pipelineServer.py::fnCaptureLiveImageIdentityAtConnect::1",
    "vaibify/gui/pipelineServer.py::fnTerminalReadLoop::1",
    "vaibify/gui/pipelineServer.py::fsContainerNameForId::1",
    "vaibify/gui/registryRoutes.py::_fbNameHasRunningPipeline::1",
    "vaibify/gui/registryRoutes.py::_fbReadForceFlag::1",
    "vaibify/gui/registryRoutes.py::_fnRegisterStopContainer.fdictStopContainer::1",
    "vaibify/gui/registryRoutes.py::_fsResolveContainerId::1",
    "vaibify/gui/registryRoutes.py::_ftDiscoverAllContainers::1",
    "vaibify/gui/routeContext.py::ffilesSnapshotForWorkflow::1",
    "vaibify/gui/routeContext.py::fnRecordAttributionEvent::1",
    "vaibify/gui/routeContext.py::fsHashContainerFileOrEmpty::1",
    "vaibify/gui/routeContext.py::fsRefreshVerifyCacheAfterPush::1",
    "vaibify/gui/routes/councilRoutes.py::_fdictComputeBaselineStaleness::1",
    "vaibify/gui/routes/environmentArchiveRoutes.py::_fnRunDepositWorker::1",
    "vaibify/gui/routes/environmentArchiveRoutes.py::_fnRunPromotionWorker::1",
    "vaibify/gui/routes/fileRoutes.py::_fnRegisterFilePull.fdictHandlePullFile::1",
    "vaibify/gui/routes/fileRoutes.py::_fnRegisterFileUpload.fdictUploadFile::1",
    "vaibify/gui/routes/pipelineRoutes.py::_fdictFetchTestMarkers::1",
    "vaibify/gui/routes/pipelineRoutes.py::_fdictHydrateShaCacheFromContainer::1",
    "vaibify/gui/routes/pipelineRoutes.py::_fnMaintainAiProvenanceStamp::1",
    "vaibify/gui/routes/pipelineRoutes.py::_fnPersistShaCacheToContainer::1",
    "vaibify/gui/routes/pipelineRoutes.py::_fnRunSupervisionWatchdog::1",
    "vaibify/gui/routes/remoteRefreshRoutes.py::_fdictVerifyWithoutWriting::1",
    "vaibify/gui/routes/remoteRefreshRoutes.py::_fnRunRefreshWorker::1",
    "vaibify/gui/routes/replayRoutes.py::_flistGatherSessionSecrets::1",
    "vaibify/gui/routes/replayRoutes.py::_fsFetchContextOrNone::1",
    "vaibify/gui/routes/reproducibilityRoutes.py::_fdictCaptureProvenanceOrNone::1",
    "vaibify/gui/routes/reproducibilityRoutes.py::_flistLoadPackagesFromConfig::1",
    "vaibify/gui/routes/settingsRoutes.py::_fnRegisterLogRoutes.fresponseGetLogContent::1",
    "vaibify/gui/routes/syncRoutes.py::_fbRestoreContainerSnapshot::1",
    "vaibify/gui/routes/syncRoutes.py::_fdictRemoteStateAfterPush::1",
    "vaibify/gui/routes/syncRoutes.py::_fdictResolveInterruptedPush::1",
    "vaibify/gui/routes/syncRoutes.py::_fdictRunArxivVerifyAfterConfig::1",
    "vaibify/gui/routes/syncRoutes.py::_fdictRunGithubAddFileBlocking::1",
    "vaibify/gui/routes/syncRoutes.py::_fdictRunGithubPushBlocking::1",
    "vaibify/gui/routes/syncRoutes.py::_fdictStoreCredentialSafely::1",
    "vaibify/gui/routes/syncRoutes.py::_fdictVerifyRemoteUnderTheDrain.fdictVerifyTheRemote::1",
    "vaibify/gui/routes/syncRoutes.py::_fnCleanupCredential::1",
    "vaibify/gui/routes/syncRoutes.py::_fnCleanupOverleafHostCredential::1",
    "vaibify/gui/routes/syncRoutes.py::_fnDropContainerSnapshot::1",
    "vaibify/gui/routes/syncRoutes.py::_fnRollBackFailedCredential::1",
    "vaibify/gui/routes/syncRoutes.py::_fsClearArxivSyncCache::1",
    "vaibify/gui/routes/syncRoutes.py::_fsFetchCommitHashAfterPush::1",
    "vaibify/gui/routes/syncRoutes.py::_fsFetchPreviousHostCredential::1",
    "vaibify/gui/routes/syncRoutes.py::_ftSnapshotContainerCredential::1",
    "vaibify/gui/routes/testRoutes.py::_fsRequireConfiguredProviderKey::1",
    "vaibify/gui/serverLifespan.py::_fbAnyHeldContainerBusy::1",
    "vaibify/gui/serverLifespan.py::_fbOwnedNamePipelineRunning::1",
    "vaibify/gui/serverLifespan.py::_fnDisposableReclaimLoop::1",
    "vaibify/gui/serverLifespan.py::_fnIdleShutdownWatchdogLoop::1",
    "vaibify/gui/serverLifespan.py::_fnPeriodicContainerSweepLoop::1",
    "vaibify/gui/serverLifespan.py::_fnRunOneContainerSweep::1",
    "vaibify/gui/serverLifespan.py::_fnRunShutdownHookSafely::1",
    "vaibify/gui/serverLifespan.py::_fnRunStartupHookSafely::1",
    "vaibify/gui/serverLifespan.py::_fnSessionLifecycleEvaluatorLoop::1",
    "vaibify/gui/serverLifespan.py::_fnSweepSleepPreventionForApp::1",
    "vaibify/gui/serverLifespan.py::fnCancelBackgroundTask::1",
    "vaibify/gui/sessionLifecycle.py::_fnCloseDetachedConnections::1",
    "vaibify/gui/startReservation.py::_fbAwaitStartTaskSettlement::1",
    "vaibify/gui/startReservation.py::_fnRunStartTask::1",
    "vaibify/gui/startReservation.py::_fsImageBuildState::1",
    "vaibify/gui/startReservation.py::_fsRefusalForAlreadyRunningContainer::1",
    "vaibify/gui/terminalContainment.py::_fdictProbeGroupQuietly::1",
    "vaibify/gui/terminalContainment.py::_fdictResolveUndiscoveredRecord::1",
    "vaibify/gui/terminalContainment.py::_fdictSignalAndAwaitEmpty::1",
    "vaibify/gui/terminalContainment.py::fiDiscoverTerminalProcessGroup::1",
    "vaibify/reproducibility/environmentSnapshot.py::_fsReadHexLabel::1",
    "vaibify/reproducibility/environmentSnapshot.py::fdictCaptureBuiltImageIdentity::1",
    "vaibify/reproducibility/environmentSnapshot.py::fsReadImageArchitecture::1",
    "vaibify/reproducibility/environmentSnapshot.py::fsReadImageToolchainEpoch::1",
    "vaibify/reproducibility/imageDeposit.py::_fnDiscardDraft::1",
    "vaibify/reproducibility/imageDeposit.py::fiReadImageSizeBytes::1",
    "vaibify/reproducibility/scheduledReverify.py::_fnReverifyLoop::1",
    "vaibify/reproducibility/scheduledReverify.py::fdictAttemptOneVerify::1",
    "vaibify/reproducibility/scheduledReverify.py::fnScheduleReverify.fnStopReverifyTask::1",
    "vaibify/reproducibility/shadowRerun.py::_fdictCreateShadowOrExplainTheMissingImage::1",
    "vaibify/reproducibility/shadowRerun.py::_fdictTearDownShadow::1",
    "vaibify/reproducibility/shadowRerun.py::_fnSweepShadowsLeftByACrash::1",
]


@pytest.mark.falsification
def testNoNewBroadHandlerCanSwallowAControlPlaneRefusal():
    """The scan finds exactly the seeded handlers, in either direction.

    Kills: an ``fnReRaiseControlPlaneRefusal`` call dropped from a
    guarded handler, or a new unguarded broad handler in a module that
    reaches a container primitive.
    """
    listFound = _flistScanPackage()
    setNew = sorted(set(listFound) - set(LIST_SEEDED_OFFENDERS))
    setFixed = sorted(set(LIST_SEEDED_OFFENDERS) - set(listFound))
    assert not setNew, (
        "broad handlers that can swallow a ControlPlaneRefusalError "
        "(call fnReRaiseControlPlaneRefusal(error) first in each): "
        f"{setNew}"
    )
    assert not setFixed, (
        "these handlers now pass refusals through; remove them from "
        f"LIST_SEEDED_OFFENDERS so the ratchet falls: {setFixed}"
    )
    assert len(set(LIST_SEEDED_OFFENDERS)) == len(LIST_SEEDED_OFFENDERS)
    assert LIST_SEEDED_OFFENDERS == sorted(LIST_SEEDED_OFFENDERS)


# ── The scanner can fail: synthetic sources, independent of the seed ──

S_GATE_IMPORT = "from vaibify.config import mutationAdmission\n"


def _flistFindInSynthetic(sBody, sImport=S_GATE_IMPORT):
    return flistFindOffendingHandlers(sImport + sBody, "synthetic.py")


@pytest.mark.falsification
def testAnUnguardedBroadHandlerIsFlagged():
    """Kills: treating every broad handler as acceptable."""
    listFound = _flistFindInSynthetic(
        "def fnRead():\n    try:\n        pass\n"
        "    except Exception:\n        return None\n"
    )
    assert listFound == ["synthetic.py::fnRead::1"]


def testABareExceptAndABaseExceptionAreBroad():
    listFound = _flistFindInSynthetic(
        "def fnRead():\n    try:\n        pass\n    except:\n        pass\n"
        "    try:\n        pass\n    except BaseException:\n        pass\n"
    )
    assert listFound == ["synthetic.py::fnRead::1", "synthetic.py::fnRead::2"]


def testANarrowHandlerIsNotBroad():
    assert _flistFindInSynthetic(
        "def fnRead():\n    try:\n        pass\n"
        "    except (OSError, ValueError):\n        return None\n"
    ) == []


@pytest.mark.falsification
def testTheReRaiseHelperCallPassesAHandler():
    """Kills: not recognising the helper call as a pass."""
    assert _flistFindInSynthetic(
        "def fnRead():\n    try:\n        pass\n"
        "    except Exception as error:\n"
        "        mutationAdmission.fnReRaiseControlPlaneRefusal(error)\n"
        "        return None\n"
    ) == []


def testAnEarlierHandlerThatReRaisesARefusalPassesTheBroadOne():
    assert _flistFindInSynthetic(
        "def fnRead():\n    try:\n        pass\n"
        "    except mutationAdmission.ControlPlaneRefusalError:\n"
        "        raise\n"
        "    except Exception:\n        return None\n"
    ) == []


@pytest.mark.falsification
def testAnEarlierHandlerThatNamesARefusalButSwallowsItDoesNotPass():
    """Naming a refusal is not enough: the earlier handler must re-raise.

    Kills: accepting any earlier handler that merely names a refusal.
    """
    assert _flistFindInSynthetic(
        "def fnRead():\n    try:\n        pass\n"
        "    except mutationAdmission.ControlPlaneRefusalError:\n"
        "        return None\n"
        "    except Exception:\n        return None\n"
    ) == ["synthetic.py::fnRead::1"]


def testAHandlerThatEndsByReRaisingPasses():
    assert _flistFindInSynthetic(
        "def fnRead():\n    try:\n        pass\n"
        "    except Exception:\n        fnCleanUp()\n        raise\n"
    ) == []


def testAModuleOutsideTheGateIsNotScanned():
    assert flistFindOffendingHandlers(
        "def fnRead():\n    try:\n        pass\n"
        "    except Exception:\n        return None\n", "synthetic.py",
    ) == []


def testNestedFunctionsAndClassesGetDistinctIdentities():
    listFound = _flistFindInSynthetic(
        "class Reader:\n    def fnRead(self):\n        try:\n"
        "            pass\n        except Exception:\n            pass\n"
    )
    assert listFound == ["synthetic.py::Reader.fnRead::1"]


# ── The one handler the triage found turning a refusal into another answer ──

S_REFUSAL_TEXT = (
    "The container has an unfinished operation. Run `vaibify reconcile` "
    "to settle it before changing anything else."
)


@pytest.mark.falsification
def testAProjectSearchRefusedByTheCarrierAnswersWithTheRefusalsOwnText(
    monkeypatch, tmp_path,
):
    """The refusal's instruction must reach the researcher, not "Search failed".

    Driven over the served app with a connected client and the Docker
    double that calls the real admission gates. The container name and
    id differ, so a route that keyed on the wrong one fails here.

    Kills: removing ``fnReRaiseControlPlaneRefusal`` from the project
    search handler, which wrapped the refusal in "Search failed" and
    sent it through a sanitizer that dropped the `vaibify reconcile`
    instruction.
    """
    from tests.testCarrierMigratedRoutes import (
        DockerDoubleThatCallsTheRealGates, _tConnectGatedClient,
    )
    from tests.testDraftRoutes import S_CONTAINER_ID
    from vaibify.config import mutationAdmission, registryManager
    from vaibify.gui import workflowManager

    sRegistryDirectory = str(tmp_path / "registryHome")
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_DIRECTORY", sRegistryDirectory,
    )
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_PATH",
        sRegistryDirectory + "/registry.json",
    )
    monkeypatch.setattr(
        registryManager, "_S_LOCK_PATH",
        sRegistryDirectory + "/registry.lock",
    )

    def fnRefuseTheSearch(*listArguments, **dictKeywords):
        raise mutationAdmission.MutationNotAdmittedError(S_REFUSAL_TEXT)

    client, _connectionDocker = _tConnectGatedClient(
        DockerDoubleThatCallsTheRealGates(),
    )
    monkeypatch.setattr(
        workflowManager, "flistFindWorkflowsInContainer", fnRefuseTheSearch,
    )
    responseHttp = client.get(f"/api/workflows/{S_CONTAINER_ID}")
    assert responseHttp.status_code == 500
    assert S_REFUSAL_TEXT in responseHttp.text
    assert "Search failed" not in responseHttp.text
