"""The credential test's fault paths: cleanup, refusals, and seams.

The seven-check happy and failing paths are pinned elsewhere with a
fake runner connection. These tests drive what those leave unexercised:
an admission that faults AFTER staging must delete what it staged; a
job whose resources cannot be proven removed must name each one; a
publication the store refuses must not be reported as the outcome; a
cancel must kill every live runner; and the three production seams
(the CLI-version sandbox, the per-job egress, the start lock) must hold
their contracts. The Docker gateway is the only boundary patched; the
store, the job records, the locks and the admitter are the real ones.
Job ids, container ids and container names are kept distinct.
"""

import threading

import pytest

from vaibify.gui import (
    agentCouncilCredentialGate,
    agentCouncilCredentialStore,
    agentCouncilCredentialTest,
    agentCouncilCredentialTestRecords,
    agentCouncilDockerGateway,
    agentCouncilProviders,
    agentCouncilStagedCopies,
)

S_IMAGE = "sha256:" + "5e" * 32
S_RESOURCE = "projectAlpha"
S_CONTAINER_ID = "d00dfeed0123"
S_ACCESS_TOKEN = "sk-ant-oat01-SYNTHETIC-COVERAGE-TOKEN"


@pytest.fixture
def sEvidencePath(tmp_path, monkeypatch):
    """Point the credential document at a temp directory."""
    sPath = str(tmp_path / "agentCouncils" / "credentialEvidence.json")
    monkeypatch.setattr(
        agentCouncilCredentialGate, "fsResolveCredentialEvidencePath",
        lambda: sPath)
    return sPath


@pytest.fixture
def pathStagingRoot(tmp_path, monkeypatch):
    """Point credential staging at a private temp directory."""
    from vaibify.config import secretManager
    pathRoot = tmp_path / "staging"
    pathRoot.mkdir(mode=0o700)
    monkeypatch.setattr(
        secretManager, "_fsGetTempDirectory", lambda: str(pathRoot))
    return pathRoot


def fdictBeginRecordedJob(sEvidencePath, sProvider="claude"):
    """Consent, mint a job record, and make it the key's test in flight."""
    agentCouncilCredentialStore.fdictRecordConsent(
        sEvidencePath, sProvider, S_IMAGE)
    sJobId = agentCouncilCredentialTest.fsComposeTestJobId()
    dictJob = agentCouncilCredentialTestRecords.fdictCreateJobRecord(
        sJobId, sProvider, S_IMAGE, S_RESOURCE, S_CONTAINER_ID, "haiku")
    dictMarker = agentCouncilCredentialStore.fdictBeginCredentialTest(
        sEvidencePath, sProvider, S_IMAGE, sJobId)
    dictJob["iConsentGeneration"] = dictMarker["iConsentGeneration"]
    agentCouncilCredentialTestRecords.fnWriteJobRecord(dictJob)
    return dictJob


def fsLabelForJob(sJobId):
    """Return the council label a runner reserved for this job wears."""
    return (f"council-{agentCouncilCredentialTest.fsComposeTestCampaignId(sJobId)}"
            "-0123456789ab")


def fdictReadDocument(sEvidencePath):
    return agentCouncilCredentialStore.fdictReadCredentialDocument(
        sEvidencePath)["dictDocument"]


class NoLoginDocker:
    """A project container with no login: check 1 fails immediately."""

    def fbaFetchCredentialFile(self, sContainerId, sFilePath):
        raise FileNotFoundError(sFilePath)


# ----- the admitter cleans up what it staged -------------------------------


def testAnAdmissionThatFaultsAfterStagingDeletesTheStagedCopy(
        sEvidencePath, pathStagingRoot, monkeypatch):
    dictJob = fdictBeginRecordedJob(sEvidencePath)
    listHeld = []

    def fnFailToHoldTheCopy(sStagedPath):
        listHeld.append(sStagedPath)
        raise OSError("the staging lock could not be taken")

    monkeypatch.setattr(
        agentCouncilStagedCopies, "fnHoldStagedCopy", fnFailToHoldTheCopy)
    with pytest.raises(OSError) as excInfo:
        agentCouncilCredentialTest.ftAdmitCredentialTestCredential(
            dictJob["sJobId"], "claude", S_IMAGE,
            fsLabelForJob(dictJob["sJobId"]),
            {"sAccessToken": S_ACCESS_TOKEN,
             "listScopes": ["user:inference"],
             "iExpiresAtEpochMilliseconds": 1})
    assert "staging lock" in str(excInfo.value)
    assert len(listHeld) == 1, "the copy must have been staged first"
    assert list(pathStagingRoot.iterdir()) == []
    assert fdictReadDocument(sEvidencePath)["listAdmissions"] == []


# ----- the stager refuses an ambiguous runner --------------------------------


@pytest.mark.parametrize("iHandleCount", [0, 2])
def testTheStagerRefusesUnlessExactlyOneRunnerServesTheJob(
        sEvidencePath, pathStagingRoot, iHandleCount):
    dictJob = fdictBeginRecordedJob(sEvidencePath)
    sCampaignId = agentCouncilCredentialTest.fsComposeTestCampaignId(
        dictJob["sJobId"])
    dictHandles = {
        f"handle-{iIndex}": {"sCampaignId": sCampaignId,
                             "sContainerId": f"runnerid-{iIndex}"}
        for iIndex in range(iHandleCount)}
    dictHandles["handle-other"] = {"sCampaignId": "campaign-unrelated",
                                   "sContainerId": "runnerid-other"}
    dictRuntime = {"dictGateway": {"dockerCouncil": object(),
                                   "dictHandlesById": dictHandles},
                   "connectionDocker": NoLoginDocker(),
                   "sContainerId": S_CONTAINER_ID}
    ftStage = agentCouncilCredentialTest._ffnBuildTestStager(
        dictJob, dictRuntime)
    with pytest.raises(
            agentCouncilCredentialStore.CredentialAdmissionRefusedError) as \
            excInfo:
        ftStage()
    assert f"found {iHandleCount}" in str(excInfo.value)
    assert list(pathStagingRoot.iterdir()) == []


# ----- turn classification ---------------------------------------------------


def testAnInvalidModelTurnKilledAtItsDeadlineIsIncompleteNotFailed():
    """A failing turn that never ended proves nothing about the CLI."""
    with pytest.raises(
            agentCouncilCredentialTest.CredentialTestIncompleteError) as \
            excInfo:
        agentCouncilCredentialTest._fnClassifyFailurePathTurn({"dictResult": {
            "sEmptyResultReason":
                agentCouncilProviders.S_EMPTY_BECAUSE_WALL_CLOCK}})
    assert excInfo.value.sCheckId == "failurePath"
    assert "did not end within the timeout" in excInfo.value.sDetail


# ----- the CLI-version sandbox ----------------------------------------------


class SandboxGatewayRecorder:
    """Records the sandbox reserve, exec and destroy calls it answers."""

    def __init__(self, bCreated=True, dictExecuted=None,
                 errorOnExecute=None):
        self.bCreated = bCreated
        self.dictExecuted = dictExecuted or {}
        self.errorOnExecute = errorOnExecute
        self.listReserved = []
        self.listExecuted = []
        self.listDestroyed = []

    def fnInstall(self, monkeypatch):
        monkeypatch.setattr(
            agentCouncilDockerGateway, "fdictReserveAndCreateRunner",
            self.fdictReserve)
        monkeypatch.setattr(
            agentCouncilDockerGateway, "fdictExecuteBoundedTurn",
            self.fdictExecute)
        monkeypatch.setattr(
            agentCouncilDockerGateway, "fdictDestroyAndSettle",
            lambda dictGateway, sHandle: (
                self.listDestroyed.append(sHandle) or {
                    "sOutcome": "destroyed"}))

    def fdictReserve(self, dictGateway, sCampaignId, sProvider, dictCost,
                     sImageReference, bSandbox=False, **dictKeywords):
        self.listReserved.append(
            (sCampaignId, sProvider, sImageReference, bSandbox))
        return {"bCreated": self.bCreated, "sHandle": "handle-sandbox",
                "sRefusalReason": "" if self.bCreated else "ceiling"}

    def fdictExecute(self, dictGateway, sHandle, listCommand,
                     fWallClockSeconds=None, **dictKeywords):
        self.listExecuted.append((sHandle, list(listCommand)))
        if self.errorOnExecute is not None:
            raise self.errorOnExecute
        return self.dictExecuted


def fdictBuildVersionJob(sProvider="claude"):
    return {"sJobId": "a1" * 16, "sProvider": sProvider,
            "sImageIdentity": S_IMAGE}


def testTheCliVersionIsTheFirstOutputLineOfASandboxRun(monkeypatch):
    recorderGateway = SandboxGatewayRecorder(dictExecuted={
        "iExitCode": 0, "sOutput": "\n2.1.7 (Claude Code)\nextra line\n"})
    recorderGateway.fnInstall(monkeypatch)
    sVersion = agentCouncilCredentialTest.fsReadCliVersionFromImage(
        fdictBuildVersionJob(), {"dictGateway": {"bDouble": True}})
    assert sVersion == "2.1.7 (Claude Code)"
    assert recorderGateway.listReserved == [(
        "credentialTest-" + "a1" * 16,
        agentCouncilProviders.S_BASELINE_SANDBOX_PROVIDER, S_IMAGE, True)]
    sHandle, listCommand = recorderGateway.listExecuted[0]
    assert sHandle == "handle-sandbox"
    assert listCommand == (
        list(agentCouncilProviders.LIST_CLAUDE_CLI_PROGRAM) + ["--version"])
    assert recorderGateway.listDestroyed == ["handle-sandbox"]


def testAVersionLineIsTruncatedToTwoHundredCharacters(monkeypatch):
    SandboxGatewayRecorder(dictExecuted={
        "iExitCode": 0, "sOutput": "v" * 500}).fnInstall(monkeypatch)
    sVersion = agentCouncilCredentialTest.fsReadCliVersionFromImage(
        fdictBuildVersionJob(), {"dictGateway": {}})
    assert sVersion == "v" * 200


@pytest.mark.parametrize("dictExecuted", [
    {"iExitCode": 1, "sOutput": "error: unknown option\n"},
    {"iExitCode": 0, "sOutput": "   \n"},
    {"iExitCode": 0},
])
def testAnUnansweringSandboxRecordsUnreadableAndIsStillDestroyed(
        monkeypatch, dictExecuted):
    recorderGateway = SandboxGatewayRecorder(dictExecuted=dictExecuted)
    recorderGateway.fnInstall(monkeypatch)
    assert agentCouncilCredentialTest.fsReadCliVersionFromImage(
        fdictBuildVersionJob(), {"dictGateway": {}}) == "unreadable"
    assert recorderGateway.listDestroyed == ["handle-sandbox"]


def testARefusedSandboxRecordsUnreadableAndRunsNothing(monkeypatch):
    recorderGateway = SandboxGatewayRecorder(bCreated=False)
    recorderGateway.fnInstall(monkeypatch)
    assert agentCouncilCredentialTest.fsReadCliVersionFromImage(
        fdictBuildVersionJob(), {"dictGateway": {}}) == "unreadable"
    assert recorderGateway.listExecuted == []
    assert recorderGateway.listDestroyed == []


def testASandboxWhoseExecRaisesIsDestroyedBeforeTheErrorPropagates(
        monkeypatch):
    recorderGateway = SandboxGatewayRecorder(
        errorOnExecute=RuntimeError("daemon went away"))
    recorderGateway.fnInstall(monkeypatch)
    with pytest.raises(RuntimeError):
        agentCouncilCredentialTest.fsReadCliVersionFromImage(
            fdictBuildVersionJob(), {"dictGateway": {}})
    assert recorderGateway.listDestroyed == ["handle-sandbox"]


@pytest.mark.parametrize("sProvider,sModuleName,sConstantName", [
    ("codex", "agentCouncilCodexProvider", "LIST_CODEX_CLI_PROGRAM"),
    ("gemini", "agentCouncilAntigravityProvider",
     "LIST_ANTIGRAVITY_CLI_PROGRAM"),
])
def testEachProviderAsksItsOwnCliForTheVersion(
        monkeypatch, sProvider, sModuleName, sConstantName):
    import importlib
    moduleProvider = importlib.import_module(f"vaibify.gui.{sModuleName}")
    recorderGateway = SandboxGatewayRecorder(
        dictExecuted={"iExitCode": 0, "sOutput": "9.9.9\n"})
    recorderGateway.fnInstall(monkeypatch)
    agentCouncilCredentialTest.fsReadCliVersionFromImage(
        fdictBuildVersionJob(sProvider), {"dictGateway": {}})
    assert recorderGateway.listExecuted[0][1] == (
        list(getattr(moduleProvider, sConstantName)) + ["--version"])


# ----- the per-job egress ----------------------------------------------------


def testProvisioningMarksEgressBeforeCreatingSoAFaultIsStillTornDown(
        monkeypatch):
    def fsFailToCreateNetwork(dictGateway, sScope):
        raise RuntimeError("network create refused")

    monkeypatch.setattr(
        agentCouncilDockerGateway, "fsCreateCampaignInternalNetwork",
        fsFailToCreateNetwork)
    dictRuntime = {"dictGateway": {}, "bEgressProvisioned": False}
    with pytest.raises(RuntimeError):
        agentCouncilCredentialTest.fdictProvisionTestEgress(
            fdictBuildVersionJob(), dictRuntime)
    assert dictRuntime["bEgressProvisioned"] is True


def testProvisioningScopesTheEgressToTheJobAndItsProvider(monkeypatch):
    listCalls = []
    monkeypatch.setattr(
        agentCouncilDockerGateway, "fsCreateCampaignInternalNetwork",
        lambda dictGateway, sScope: (
            listCalls.append(("network", sScope)) or "netAlpha"))
    monkeypatch.setattr(
        agentCouncilDockerGateway, "fsLaunchAllowlistProxy",
        lambda dictGateway, sScope, saHosts: (
            listCalls.append(("proxy", sScope, list(saHosts)))
            or "172.30.9.2"))
    dictRuntime = {"dictGateway": {}, "bEgressProvisioned": False}
    dictEgress = agentCouncilCredentialTest.fdictProvisionTestEgress(
        fdictBuildVersionJob(), dictRuntime)
    sScope = "credentialTest-" + "a1" * 16
    assert dictEgress == {"sNetworkName": "netAlpha",
                          "sProxyInternalAddress": "172.30.9.2",
                          "iProxyPort": 8888}
    assert listCalls[0] == ("network", sScope)
    assert listCalls[1][:2] == ("proxy", sScope)
    assert listCalls[1][2], "the proxy must allow the provider's hosts"


# ----- the start lock --------------------------------------------------------


def testAStartThatFaultsReleasesTheKeysTestLock(sEvidencePath):
    dictJobs = {}
    with pytest.raises(KeyError):
        agentCouncilCredentialTest.fdictStartCredentialTest(
            dictJobs, "claude", S_IMAGE, S_RESOURCE, "haiku", {})
    assert dictJobs == {}
    fileLock = agentCouncilCredentialStore.ffileTryAcquireTestLock(
        agentCouncilCredentialGate.fsResolveCredentialStoreDirectory(),
        "claude", S_IMAGE)
    assert fileLock is not None, "the failed start kept the key locked"
    fileLock.close()


# ----- unproven cleanup is named, never passed ------------------------------


def testEveryUnprovenRunnerAndTheEgressAreNamedInTheOutcome(
        sEvidencePath, pathStagingRoot, monkeypatch):
    dictJob = fdictBeginRecordedJob(sEvidencePath)
    listDestroyAttempts = []

    def fdictDestroyAndSettle(dictGateway, sHandle):
        listDestroyAttempts.append(sHandle)
        if sHandle == "handle-raising":
            raise RuntimeError("daemon unreachable")
        return {"sOutcome": "quarantined"}

    def fdictFailToRemoveEgress(dictGateway, sScope):
        raise RuntimeError("daemon unreachable")

    monkeypatch.setattr(agentCouncilDockerGateway, "fdictDestroyAndSettle",
                        fdictDestroyAndSettle)
    monkeypatch.setattr(
        agentCouncilDockerGateway, "fdictRemoveCampaignEgressResources",
        fdictFailToRemoveEgress)
    dictRuntime = {
        "connectionDocker": NoLoginDocker(), "sContainerId": S_CONTAINER_ID,
        "eventCancel": threading.Event(), "bEgressProvisioned": True,
        "dictGateway": {"dockerCouncil": object(), "dictHandlesById": {
            "handle-raising": {"sContainerName": "vaibifyCouncilRunnerOne",
                               "sContainerId": "runnerid-one"},
            "handle-quarantined": {"sContainerName": "vaibifyCouncilRunnerTwo",
                                   "sContainerId": "runnerid-two"}}},
    }
    agentCouncilCredentialTest.fnRunCredentialTestJob(dictJob, dictRuntime)
    dictRecord = agentCouncilCredentialTestRecords.fdictReadJobRecord(
        dictJob["sJobId"])
    assert (dictRecord["sStatus"], dictRecord["sFailedCheck"]) == (
        "failed", "loginPresent")
    assert sorted(listDestroyAttempts) == [
        "handle-quarantined", "handle-raising"]
    assert sorted(dictRecord["listUnsettledResources"]) == [
        "runner vaibifyCouncilRunnerOne", "runner vaibifyCouncilRunnerTwo",
        "the test's network and proxy"]
    assert "could not be proven removed" in dictRecord["sDetail"]
    assert "vaibifyCouncilRunnerOne" in dictRecord["sDetail"]
    assert fdictReadDocument(sEvidencePath)["dictInFlight"] == {}


def testARefusedPublicationIsRecordedIncompleteWithTheStoresReason(
        sEvidencePath):
    dictJob = fdictBeginRecordedJob(sEvidencePath)
    agentCouncilCredentialTest.fnPublishJobOutcome(
        dictJob, agentCouncilCredentialStore.S_OUTCOME_PASSED, "", "")
    dictSecond = dict(dictJob)
    agentCouncilCredentialTest.fnPublishJobOutcome(
        dictSecond, agentCouncilCredentialStore.S_OUTCOME_PASSED, "", "")
    assert dictSecond["sStatus"] == (
        agentCouncilCredentialStore.S_OUTCOME_INCOMPLETE)
    assert "no longer the test in flight" in dictSecond["sDetail"]
    dictRecord = agentCouncilCredentialTestRecords.fdictReadJobRecord(
        dictJob["sJobId"])
    assert dictRecord["sStatus"] == "incomplete"
    listOutcomes = fdictReadDocument(sEvidencePath)["listOutcomes"]
    assert len(listOutcomes) == 1, "an outcome must never be appended twice"


# ----- cancel ----------------------------------------------------------------


def testCancelKillsEveryLiveRunnerOfARunningJob(monkeypatch):
    listKilled = []
    monkeypatch.setattr(
        agentCouncilDockerGateway, "fdictDestroyRunnerAndProveAbsence",
        lambda dockerCouncil, sContainerId: listKilled.append(
            (dockerCouncil, sContainerId)))
    eventRelease = threading.Event()
    threadJob = threading.Thread(target=eventRelease.wait, daemon=True)
    threadJob.start()
    dockerCouncil = object()
    dictRuntime = {"eventCancel": threading.Event(), "dictGateway": {
        "dockerCouncil": dockerCouncil, "dictHandlesById": {
            "handle-one": {"sContainerId": "runnerid-one"},
            "handle-two": {"sContainerId": "runnerid-two"}}}}
    dictJobs = {"jobAlpha": {"dictRuntime": dictRuntime,
                             "threadJob": threadJob}}
    try:
        assert agentCouncilCredentialTest.fbRequestCredentialTestCancel(
            dictJobs, "jobAlpha") is True
    finally:
        eventRelease.set()
        threadJob.join(timeout=5)
    assert dictRuntime["eventCancel"].is_set()
    assert sorted(listKilled, key=lambda tKill: tKill[1]) == [
        (dockerCouncil, "runnerid-one"), (dockerCouncil, "runnerid-two")]


def testCancelOfAnUnknownOrFinishedJobDoesNothing(monkeypatch):
    listKilled = []
    monkeypatch.setattr(
        agentCouncilDockerGateway, "fdictDestroyRunnerAndProveAbsence",
        lambda *arguments: listKilled.append(arguments))
    threadFinished = threading.Thread(target=lambda: None)
    threadFinished.start()
    threadFinished.join()
    dictRuntime = {"eventCancel": threading.Event(), "dictGateway": {
        "dockerCouncil": object(),
        "dictHandlesById": {"handle-one": {"sContainerId": "runnerid-one"}}}}
    dictJobs = {"jobAlpha": {"dictRuntime": dictRuntime,
                             "threadJob": threadFinished}}
    assert agentCouncilCredentialTest.fbRequestCredentialTestCancel(
        dictJobs, "jobBeta") is False
    assert agentCouncilCredentialTest.fbRequestCredentialTestCancel(
        dictJobs, "jobAlpha") is False
    assert not dictRuntime["eventCancel"].is_set()
    assert listKilled == []
