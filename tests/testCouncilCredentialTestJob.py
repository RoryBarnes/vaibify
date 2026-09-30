"""The in-app credential test runs its seven checks and records one outcome.

Contracts A2 and A4 of the credential-consent plan, offline: a fake
runner connection stands in for the gateway's runner and the provider
CLI, but the admission, the store, the job record, the locks and the
restart sweep are all the real ones. Each check has a test that makes
exactly that check fail, so removing the check is caught.

No test here proves anything about a real subscription token. That is
the paid-account run, and only the researcher can perform it.
"""

import json
import os
import subprocess
import sys
import threading
import time

import pytest

from vaibify.gui import (
    agentCouncilCredentialGate,
    agentCouncilCredentialStore,
    agentCouncilCredentialTest,
    agentCouncilCredentialTestRecords,
    agentCouncilCredentialTestRecovery,
    agentCouncilProviders,
    agentCouncilStagedCopies,
)

S_IMAGE = "sha256:" + "7a" * 32
S_CONTAINER_ID = "c0ffee123456"
S_RESOURCE = "projectAlpha"
S_ACCESS_TOKEN = "sk-ant-oat01-SYNTHETIC-JOB-TOKEN"


@pytest.fixture
def sEvidencePath(tmp_path, monkeypatch):
    sPath = str(tmp_path / "agentCouncils" / "credentialEvidence.json")
    monkeypatch.setattr(
        agentCouncilCredentialGate, "fsResolveCredentialEvidencePath",
        lambda: sPath)
    return sPath


@pytest.fixture
def pathStagingRoot(tmp_path, monkeypatch):
    from vaibify.config import secretManager
    pathRoot = tmp_path / "staging"
    pathRoot.mkdir(mode=0o700)
    monkeypatch.setattr(
        secretManager, "_fsGetTempDirectory", lambda: str(pathRoot))
    return pathRoot


@pytest.fixture(autouse=True)
def fixtureNoLiveProjectLocks(monkeypatch):
    """No project is held by a live peer unless a test says so."""
    from vaibify.config import containerLock
    monkeypatch.setattr(containerLock, "fdictReadLockHolder",
                        lambda sName: {})


class FakeLoginDocker:
    """The project container: serves a login, never under the store lock."""

    def __init__(self):
        self.sAccessToken = S_ACCESS_TOKEN
        self.iExpiresAt = int((time.time() + 3600) * 1000)

    def fbaFetchCredentialFile(self, sContainerId, sFilePath):
        assert not agentCouncilCredentialStore.fbIsStoreLockHeld()
        return json.dumps({"claudeAiOauth": {
            "accessToken": self.sAccessToken, "scopes": ["user:inference"],
            "expiresAt": self.iExpiresAt}}).encode("utf-8")


class FakeCouncilDocker:
    """The daemon, as far as the label inspect is concerned."""

    def __init__(self):
        self.dictLabelsByContainer = {}
        outerSelf = self

        class _Api:
            def inspect_container(self, sContainerId):
                return {"Config": {"Labels":
                        outerSelf.dictLabelsByContainer[sContainerId]}}

            def remove_container(self, sContainerId, force=True, v=True):
                outerSelf.dictLabelsByContainer.pop(sContainerId, None)

        self.api = _Api()


class ScriptedTurn:
    """What one fake turn does: its result and its side effects."""

    def __init__(self, dictResult=None, sCompletion="terminal",
                 bLeaveStagedFile=False, fnDuringTurn=None,
                 sRunnerLabel=None):
        self.dictResult = dictResult if dictResult is not None else {
            "sRawResultText": "OK"}
        self.sCompletion = sCompletion
        self.bLeaveStagedFile = bLeaveStagedFile
        self.fnDuringTurn = fnDuringTurn
        self.sRunnerLabel = sRunnerLabel


def _ffnBuildFakeConnectionFactory(listScript, dockerCouncil):
    """Return a connection factory replaying ``listScript`` turn by turn."""
    listModels = []

    def fconnectionBuild(sProvider, dictGateway, sCampaignId, sImage,
                         baSnapshot, sModel, **dictArguments):
        listModels.append(sModel)
        scriptedTurn = listScript.pop(0)
        return FakeRunnerConnection(dictGateway, sCampaignId, scriptedTurn,
                                    dictArguments["ftStageRunnerCredential"],
                                    dockerCouncil)

    fconnectionBuild.listModels = listModels
    return fconnectionBuild


class FakeRunnerConnection:
    """Mirrors the real connection's four-call turn shape, offline."""

    def __init__(self, dictGateway, sCampaignId, scriptedTurn, ftStage,
                 dockerCouncil):
        self.dictGateway = dictGateway
        self.sCampaignId = sCampaignId
        self.scriptedTurn = scriptedTurn
        self.ftStage = ftStage
        self.dockerCouncil = dockerCouncil
        self.sHandle = ""

    async def fdictPrepareImmutableContext(self, dictRequest):
        sContainerId = "runner-" + os.urandom(4).hex()
        self.sHandle = os.urandom(8).hex()
        self.dockerCouncil.dictLabelsByContainer[sContainerId] = {
            "vaibify-council": self.scriptedTurn.sRunnerLabel
            or f"council-{self.sCampaignId}-abcdef012345"}
        self.dictGateway["dictHandlesById"][self.sHandle] = {
            "sCampaignId": self.sCampaignId, "sContainerId": sContainerId}
        try:
            sStaged, _ = self.ftStage()
        except BaseException:
            self.dictGateway["dictHandlesById"].pop(self.sHandle, None)
            raise
        if not self.scriptedTurn.bLeaveStagedFile:
            os.remove(sStaged)

    async def fnStartTurn(self, dictRequest):
        if self.scriptedTurn.fnDuringTurn is not None:
            self.scriptedTurn.fnDuringTurn()

    async def fdictCollectStructuredResult(self):
        return dict(self.scriptedTurn.dictResult)

    async def fsReportCompletion(self):
        self.dictGateway["dictHandlesById"].pop(self.sHandle, None)
        return self.scriptedTurn.sCompletion


def _dictWallClockKill():
    return {"sEmptyResultReason":
            agentCouncilProviders.S_EMPTY_BECAUSE_WALL_CLOCK}


def _flistPassingScript():
    return [ScriptedTurn(),
            ScriptedTurn({"sEmptyResultReason": "cliReportedErrorResult"}),
            ScriptedTurn(_dictWallClockKill())]


def _fdictBuildRuntime(listScript, dockerLogin=None):
    dockerCouncil = FakeCouncilDocker()
    return {
        "connectionDocker": dockerLogin or FakeLoginDocker(),
        "sContainerId": S_CONTAINER_ID,
        "dictGateway": {"dockerCouncil": dockerCouncil, "dictRegistry": {},
                        "dictHandlesById": {}},
        "eventCancel": threading.Event(),
        "fconnectionBuild": _ffnBuildFakeConnectionFactory(
            listScript, dockerCouncil),
        "fsReadCliVersion": lambda dictJob, dictRuntime: "fake-cli 1.2.3",
        "fdictProvisionEgress": lambda dictJob, dictRuntime: {},
        "dictEgress": None, "bEgressProvisioned": False,
    }


def _fdictRunJob(listScript, dockerLogin=None, sProvider="claude"):
    """Start a job through the real entry point and wait for it."""
    dictJobs = {}
    dictRuntime = _fdictBuildRuntime(listScript, dockerLogin)
    dictStarted = agentCouncilCredentialTest.fdictStartCredentialTest(
        dictJobs, sProvider, S_IMAGE, S_RESOURCE, "haiku", dictRuntime)
    dictJobs[dictStarted["sJobId"]]["threadJob"].join(timeout=30)
    return agentCouncilCredentialTestRecords.fdictReadJobRecord(
        dictStarted["sJobId"])


def _fdictEnablement():
    return agentCouncilCredentialGate.fdictEvaluateCredentialEnablement(
        "claude", S_IMAGE)


# ----- the whole test, passing --------------------------------------------------


def test_a_passing_test_authorizes_and_records_every_check(
        sEvidencePath, pathStagingRoot):
    dictJob = _fdictRunJob(_flistPassingScript())
    assert dictJob["sStatus"] == "passed", dictJob["sDetail"]
    assert all(dictCheck["sStatus"] == "passed"
               for dictCheck in dictJob["listChecks"])
    dictAnswer = _fdictEnablement()
    assert dictAnswer["bEnabled"] is True
    assert dictAnswer["dictRecord"]["sVerificationMethod"] == "inAppTest"
    assert dictAnswer["dictRecord"]["sCliVersion"] == "fake-cli 1.2.3"
    assert set(dictAnswer["dictRecord"]["listPassedChecks"]) == (
        agentCouncilCredentialTestRecords.SET_CHECK_IDS)
    assert list(pathStagingRoot.iterdir()) == []


def test_the_three_turns_use_the_model_an_invalid_id_and_the_model_again(
        sEvidencePath, pathStagingRoot):
    listScript = _flistPassingScript()
    dictRuntime = _fdictBuildRuntime(listScript)
    dictJobs = {}
    dictStarted = agentCouncilCredentialTest.fdictStartCredentialTest(
        dictJobs, "claude", S_IMAGE, S_RESOURCE, "haiku", dictRuntime)
    dictJobs[dictStarted["sJobId"]]["threadJob"].join(timeout=30)
    assert dictRuntime["fconnectionBuild"].listModels == [
        "haiku", agentCouncilCredentialTest.S_INVALID_MODEL_ID, "haiku"]


# ----- each check, made to fail ------------------------------------------------------


@pytest.mark.falsification
def test_check_one_fails_without_a_login(sEvidencePath, pathStagingRoot):
    """Check 1: no copyable login fails the test at its first check.

    Kills: a missing login read as an empty digest instead of failing
    the check.
    """
    class NoLoginDocker(FakeLoginDocker):
        def fbaFetchCredentialFile(self, sContainerId, sFilePath):
            raise FileNotFoundError(sFilePath)

    dictJob = _fdictRunJob(_flistPassingScript(), dockerLogin=NoLoginDocker())
    assert (dictJob["sStatus"], dictJob["sFailedCheck"]) == (
        "failed", "loginPresent")
    assert _fdictEnablement()["bEnabled"] is False


@pytest.mark.falsification
def test_check_two_fails_when_the_turn_is_refused(
        sEvidencePath, pathStagingRoot):
    """Check 2: a turn the provider refused fails and names the class.

    Kills: check 2 accepting a turn that returned no answer.
    """
    listScript = _flistPassingScript()
    listScript[0] = ScriptedTurn({"sEmptyResultReason": "authenticationFailure"})
    dictJob = _fdictRunJob(listScript)
    assert (dictJob["sStatus"], dictJob["sFailedCheck"]) == (
        "failed", "trivialTurn")
    assert "authenticationFailure" in dictJob["sDetail"]
    dictAnswer = _fdictEnablement()
    assert dictAnswer["sState"] == "lastTestFailed"
    assert "trivialTurn" in dictAnswer["sReason"]


@pytest.mark.falsification
def test_a_timeout_is_a_durable_incomplete_and_stays_disabled(
        sEvidencePath, pathStagingRoot):
    """A turn killed at its timeout is a durable incomplete, disabled.

    Kills: a timeout recorded as a failure of the login instead of an
    unfinished test.
    """
    listScript = _flistPassingScript()
    listScript[0] = ScriptedTurn(_dictWallClockKill())
    dictJob = _fdictRunJob(listScript)
    assert (dictJob["sStatus"], dictJob["sFailedCheck"]) == (
        "incomplete", "trivialTurn")
    assert _fdictEnablement()["sState"] == "lastTestIncomplete"
    dictDocument = json.load(open(sEvidencePath))
    assert dictDocument["listOutcomes"][-1]["sOutcome"] == "incomplete"


@pytest.mark.falsification
def test_check_four_fails_when_the_login_is_rotated(
        sEvidencePath, pathStagingRoot):
    """Check 4: a login changed during the test fails it.

    Kills: the rotation comparison removed.
    """
    dockerLogin = FakeLoginDocker()

    def _fnRotate():
        dockerLogin.sAccessToken = "sk-ant-oat01-ROTATED"

    listScript = _flistPassingScript()
    listScript[0] = ScriptedTurn(fnDuringTurn=_fnRotate)
    dictJob = _fdictRunJob(listScript, dockerLogin=dockerLogin)
    assert (dictJob["sStatus"], dictJob["sFailedCheck"]) == (
        "failed", "tokenNotRotated")


@pytest.mark.falsification
def test_check_five_fails_when_a_staged_copy_survives(
        sEvidencePath, pathStagingRoot):
    """Check 5: a staged copy left behind fails the test.

    Kills: the staged-copy check removed.
    """
    listScript = _flistPassingScript()
    listScript[0] = ScriptedTurn(bLeaveStagedFile=True)
    dictJob = _fdictRunJob(listScript)
    assert (dictJob["sStatus"], dictJob["sFailedCheck"]) == (
        "failed", "stagingCleaned")
    assert list(pathStagingRoot.iterdir()) == [], (
        "the job's own release did not delete the surviving copy")


@pytest.mark.falsification
def test_check_five_fails_when_the_runner_is_not_proven_destroyed(
        sEvidencePath, pathStagingRoot):
    """Check 5: an unproven runner destruction fails the test.

    Kills: a quarantined runner accepted as destroyed.
    """
    listScript = _flistPassingScript()
    listScript[0] = ScriptedTurn(sCompletion="indeterminate")
    dictJob = _fdictRunJob(listScript)
    assert (dictJob["sStatus"], dictJob["sFailedCheck"]) == (
        "failed", "stagingCleaned")


@pytest.mark.falsification
def test_check_six_fails_when_an_invalid_model_answers(
        sEvidencePath, pathStagingRoot):
    """Check 6: an invalid model id must end as a classified failure.

    Kills: the failure-path check accepting an answer from an invalid
    model.
    """
    listScript = _flistPassingScript()
    listScript[1] = ScriptedTurn()
    dictJob = _fdictRunJob(listScript)
    assert (dictJob["sStatus"], dictJob["sFailedCheck"]) == (
        "failed", "failurePath")


@pytest.mark.falsification
def test_check_seven_is_incomplete_when_the_turn_was_not_killed(
        sEvidencePath, pathStagingRoot):
    """Check 7: a kill that never happened is incomplete, not a pass.

    Kills: an unexercised kill path reported as a failure of the login.
    """
    listScript = _flistPassingScript()
    listScript[2] = ScriptedTurn()
    dictJob = _fdictRunJob(listScript)
    assert (dictJob["sStatus"], dictJob["sFailedCheck"]) == (
        "incomplete", "runnerKilledMidTurn")
    assert _fdictEnablement()["bEnabled"] is False


def test_check_seven_fails_when_the_turn_ends_some_other_way(
        sEvidencePath, pathStagingRoot):
    listScript = _flistPassingScript()
    listScript[2] = ScriptedTurn({"sEmptyResultReason": "nonZeroExit"})
    dictJob = _fdictRunJob(listScript)
    assert (dictJob["sStatus"], dictJob["sFailedCheck"]) == (
        "failed", "runnerKilledMidTurn")


# ----- cancel, withdrawal, duplicates ---------------------------------------------------


@pytest.mark.falsification
def test_a_cancel_ends_the_job_incomplete_and_leaves_nothing(
        sEvidencePath, pathStagingRoot):
    """Cancel kills the runner, deletes staging, records incomplete.

    Kills: the job ignoring a cancellation and running on to a pass.
    """
    dictJobs = {}
    dictHolder = {}
    listScript = _flistPassingScript()
    listScript[0] = ScriptedTurn(fnDuringTurn=lambda: (
        agentCouncilCredentialTest.fbRequestCredentialTestCancel(
            dictJobs, dictHolder["sJobId"])))
    dictRuntime = _fdictBuildRuntime(listScript)
    dictStarted = agentCouncilCredentialTest.fdictStartCredentialTest(
        dictJobs, "claude", S_IMAGE, S_RESOURCE, "haiku", dictRuntime)
    dictHolder["sJobId"] = dictStarted["sJobId"]
    dictJobs[dictStarted["sJobId"]]["threadJob"].join(timeout=30)
    dictJob = agentCouncilCredentialTestRecords.fdictReadJobRecord(
        dictStarted["sJobId"])
    assert dictJob["sStatus"] == "incomplete"
    assert "cancelled" in dictJob["sDetail"]
    assert list(pathStagingRoot.iterdir()) == []
    assert dictRuntime["dictGateway"]["dockerCouncil"].dictLabelsByContainer \
        == {}, "the cancel did not remove the live runner"


def test_a_withdrawal_mid_test_refuses_the_next_turn_and_ends_incomplete(
        sEvidencePath, pathStagingRoot):
    listScript = _flistPassingScript()
    listScript[0] = ScriptedTurn(fnDuringTurn=lambda: (
        agentCouncilCredentialStore.fdictWithdrawConsent(
            sEvidencePath, "claude", S_IMAGE)))
    dictJob = _fdictRunJob(listScript)
    assert dictJob["sStatus"] == "incomplete"
    assert dictJob["sFailedCheck"] == "failurePath"
    assert "not admitted" in dictJob["sDetail"]
    assert _fdictEnablement()["sState"] == "withdrawn"
    dictDocument = json.load(open(sEvidencePath))
    assert dictDocument["listOutcomes"][-1]["bStale"] is True


@pytest.mark.falsification
def test_two_requests_for_one_key_share_one_job(
        sEvidencePath, pathStagingRoot):
    """A second request for a running key gets the running job's id.

    Kills: the duplicate guard removed, so a second job starts for a
    running key.
    """
    eventRelease = threading.Event()
    listScript = _flistPassingScript()
    listScript[0] = ScriptedTurn(fnDuringTurn=lambda: eventRelease.wait(20))
    dictJobs = {}
    dictFirst = agentCouncilCredentialTest.fdictStartCredentialTest(
        dictJobs, "claude", S_IMAGE, S_RESOURCE, "haiku",
        _fdictBuildRuntime(listScript))
    try:
        dictSecond = agentCouncilCredentialTest.fdictStartCredentialTest(
            dictJobs, "claude", S_IMAGE, S_RESOURCE, "haiku",
            _fdictBuildRuntime(_flistPassingScript()))
    finally:
        eventRelease.set()
    dictJobs[dictFirst["sJobId"]]["threadJob"].join(timeout=30)
    assert dictSecond == {"sJobId": dictFirst["sJobId"],
                          "bAlreadyRunning": True}
    assert len(dictJobs) == 1


def test_a_job_record_never_holds_token_material(
        sEvidencePath, pathStagingRoot, caplog):
    import logging
    caplog.set_level(logging.DEBUG)
    dictJob = _fdictRunJob(_flistPassingScript())
    sRecordText = open(agentCouncilCredentialTestRecords.fsComposeJobRecordPath(
        dictJob["sJobId"])).read()
    for sText in (sRecordText, open(sEvidencePath).read(), caplog.text,
                  json.dumps(dictJob)):
        assert S_ACCESS_TOKEN not in sText
        assert "accessToken" not in sText


# ----- restart cleanup (A4) ----------------------------------------------------------------


def _fdictWriteOrphanJob(sEvidencePath, pathStagingRoot):
    """A job a dead hub left running, with a staged copy it never deleted."""
    agentCouncilCredentialStore.fdictRecordConsent(
        sEvidencePath, "claude", S_IMAGE)
    sJobId = agentCouncilCredentialTest.fsComposeTestJobId()
    agentCouncilCredentialStore.fdictBeginCredentialTest(
        sEvidencePath, "claude", S_IMAGE, sJobId)
    dictJob = agentCouncilCredentialTestRecords.fdictCreateJobRecord(
        sJobId, "claude", S_IMAGE, S_RESOURCE, S_CONTAINER_ID, "haiku")
    pathStaged = pathStagingRoot / "vc_secret_claudeCouncilAccessToken_x.tmp"
    pathStaged.write_text("leftover")
    fRecent = time.time() - 10
    os.utime(pathStaged, (fRecent, fRecent))
    dictJob["sCurrentCheck"] = "trivialTurn"
    agentCouncilCredentialTestRecords.fnWriteJobRecord(dictJob)
    return dictJob, pathStaged


@pytest.mark.falsification
def test_restart_sweep_records_a_dead_hubs_job_incomplete(
        sEvidencePath, pathStagingRoot):
    """A dead hub's job is recorded incomplete and its copy deleted.

    Kills: a dead hub's job left running, so its provider stays
    suspended forever.
    """
    dictJob, pathStaged = _fdictWriteOrphanJob(sEvidencePath, pathStagingRoot)
    dictReport = agentCouncilCredentialTestRecovery.fdictSweepOrphanedCredentialTests()
    assert dictReport["listSwept"] == [dictJob["sJobId"]]
    assert not pathStaged.exists()
    dictAfter = agentCouncilCredentialTestRecords.fdictReadJobRecord(
        dictJob["sJobId"])
    assert dictAfter["sStatus"] == "incomplete"
    assert _fdictEnablement()["sState"] == "lastTestIncomplete"


@pytest.mark.falsification
def test_restart_sweep_spares_a_job_whose_project_a_live_peer_holds(
        sEvidencePath, pathStagingRoot, monkeypatch):
    """A job whose project a live peer holds is never swept.

    Kills: the sweep settling a job whose project a live peer hub holds.
    """
    from vaibify.config import containerLock
    dictJob, pathStaged = _fdictWriteOrphanJob(sEvidencePath, pathStagingRoot)
    monkeypatch.setattr(
        containerLock, "fdictReadLockHolder",
        lambda sName: {"iPid": 1} if sName == S_RESOURCE else {})
    dictReport = agentCouncilCredentialTestRecovery.fdictSweepOrphanedCredentialTests()
    assert dictReport["listSpared"] == [dictJob["sJobId"]]
    assert agentCouncilCredentialTestRecords.fdictReadJobRecord(
        dictJob["sJobId"])["sStatus"] == "running"
    assert _fdictEnablement()["sState"] == "testInFlight"


_S_CHILD_HOLDS_TEST_LOCK = r"""
import sys, time
from vaibify.gui import agentCouncilCredentialStore
fileLock = agentCouncilCredentialStore.ffileTryAcquireTestLock(
    sys.argv[1], "claude", sys.argv[2])
print("held" if fileLock else "busy", flush=True)
time.sleep(60)
"""


@pytest.mark.falsification
def test_restart_sweep_spares_a_live_job_and_sweeps_it_once_its_hub_dies(
        sEvidencePath, pathStagingRoot):
    """The job lock is the liveness proof: held means a hub still runs it.

    Kills: the sweep ignoring a held test lock, destroying a live hub's
    test.
    """
    dictJob, pathStaged = _fdictWriteOrphanJob(sEvidencePath, pathStagingRoot)
    processHub = subprocess.Popen(
        [sys.executable, "-c", _S_CHILD_HOLDS_TEST_LOCK,
         os.path.dirname(sEvidencePath), S_IMAGE],
        stdout=subprocess.PIPE, text=True,
        env={**os.environ, "PYTHONPATH": os.getcwd()})
    try:
        assert processHub.stdout.readline().strip() == "held"
        dictReport = (
            agentCouncilCredentialTestRecovery.fdictSweepOrphanedCredentialTests())
        assert dictReport["listSpared"] == [dictJob["sJobId"]]
        assert _fdictEnablement()["sState"] == "testInFlight"
    finally:
        processHub.kill()
        processHub.wait(timeout=10)
    dictReport = agentCouncilCredentialTestRecovery.fdictSweepOrphanedCredentialTests()
    assert dictReport["listSwept"] == [dictJob["sJobId"]]
    assert not pathStaged.exists()


@pytest.mark.falsification
def test_an_unheld_copy_is_swept_at_any_age_and_a_held_one_never(
        pathStagingRoot):
    """Ownership is proven by a lock, not guessed from an age.

    Kills: the sweep deleting a copy without first taking its lock,
    which would remove the copy a live peer hub is delivering.
    """
    fRecent = time.time() - 10
    listPaths = [pathStagingRoot / f"vc_secret_{sName}.tmp" for sName in (
        "claudeCouncilAccessToken_orphan", "codexCouncilAccessToken_held",
        "githubToken_other")]
    for pathFile in listPaths:
        pathFile.write_text("x")
        os.utime(pathFile, (fRecent, fRecent))
    agentCouncilStagedCopies.fnHoldStagedCopy(str(listPaths[1]))
    assert agentCouncilStagedCopies.fiSweepOrphanedStagedCopies() == 1
    assert not listPaths[0].exists()
    assert listPaths[1].exists() and listPaths[2].exists()
    listPaths[1].unlink()
    assert agentCouncilStagedCopies.fiReleaseVanishedHolds() == 1


def test_a_copy_younger_than_the_lock_grace_is_left_alone(pathStagingRoot):
    pathFresh = pathStagingRoot / "vc_secret_claudeCouncilAccessToken_new.tmp"
    pathFresh.write_text("x")
    assert agentCouncilStagedCopies.fiSweepOrphanedStagedCopies() == 0
    assert pathFresh.exists()


def test_a_copy_held_by_another_live_process_is_spared(pathStagingRoot):
    pathHeld = pathStagingRoot / "vc_secret_claudeCouncilAccessToken_peer.tmp"
    pathHeld.write_text("x")
    fOld = time.time() - 10
    os.utime(pathHeld, (fOld, fOld))
    processPeer = subprocess.Popen(
        [sys.executable, "-c",
         "import fcntl,sys,time; f=open(sys.argv[1],'rb'); "
         "fcntl.flock(f, fcntl.LOCK_EX); print('held', flush=True); "
         "time.sleep(60)", str(pathHeld)],
        stdout=subprocess.PIPE, text=True)
    try:
        assert processPeer.stdout.readline().strip() == "held"
        assert agentCouncilStagedCopies.fiSweepOrphanedStagedCopies() == 0
        assert pathHeld.exists()
    finally:
        processPeer.kill()
        processPeer.wait(timeout=10)
    assert agentCouncilStagedCopies.fiSweepOrphanedStagedCopies() == 1


@pytest.mark.falsification
def test_unproven_cleanup_is_never_a_pass(sEvidencePath, pathStagingRoot,
                                          monkeypatch):
    """Review finding 2026-09-30: a pass needs its clean-up PROVEN.

    Kills: an unproven egress removal left as detail text on a passed
    outcome, which enabled the provider over resources nobody proved
    gone.
    """
    from vaibify.gui import agentCouncilDockerGateway
    monkeypatch.setattr(
        agentCouncilDockerGateway, "fdictRemoveCampaignEgressResources",
        lambda dictGateway, sScope: {"saIndeterminateResources": ["proxy-x"]})
    listScript = _flistPassingScript()
    dictRuntime = _fdictBuildRuntime(listScript)
    dictRuntime["fdictProvisionEgress"] = lambda dictJob, dictRuntime: (
        dictRuntime.update({"bEgressProvisioned": True}) or {})
    dictJobs = {}
    dictStarted = agentCouncilCredentialTest.fdictStartCredentialTest(
        dictJobs, "claude", S_IMAGE, S_RESOURCE, "haiku", dictRuntime)
    dictJobs[dictStarted["sJobId"]]["threadJob"].join(timeout=30)
    dictJob = agentCouncilCredentialTestRecords.fdictReadJobRecord(
        dictStarted["sJobId"])
    assert (dictJob["sStatus"], dictJob["sFailedCheck"]) == (
        "incomplete", "stagingCleaned")
    assert dictJob["listUnsettledResources"] == ["proxy-x"]
    assert _fdictEnablement()["bEnabled"] is False


def test_unsettled_leftovers_are_retried_until_proven(
        sEvidencePath, pathStagingRoot, monkeypatch):
    from vaibify.gui import agentCouncilDockerGateway
    dictAnswers = {"saIndeterminateResources": ["proxy-x"]}
    monkeypatch.setattr(
        agentCouncilDockerGateway, "fdictRemoveCampaignEgressResources",
        lambda dictGateway, sScope: dict(dictAnswers))
    monkeypatch.setattr(agentCouncilDockerGateway,
                        "flistDiscoverLabeledRunners", lambda docker: [])
    dictRuntime = _fdictBuildRuntime(_flistPassingScript())
    dictRuntime["fdictProvisionEgress"] = lambda dictJob, dictRuntime: (
        dictRuntime.update({"bEgressProvisioned": True}) or {})
    dictJobs = {}
    dictStarted = agentCouncilCredentialTest.fdictStartCredentialTest(
        dictJobs, "claude", S_IMAGE, S_RESOURCE, "haiku", dictRuntime)
    dictJobs[dictStarted["sJobId"]]["threadJob"].join(timeout=30)
    dockerFake = object()
    assert agentCouncilCredentialTestRecovery.fdictSweepOrphanedCredentialTests(
        dockerFake)["listReconciled"] == []
    dictAnswers["saIndeterminateResources"] = []
    assert agentCouncilCredentialTestRecovery.fdictSweepOrphanedCredentialTests(
        dockerFake)["listReconciled"] == [dictStarted["sJobId"]]
    assert agentCouncilCredentialTestRecords.fdictReadJobRecord(
        dictStarted["sJobId"])["listUnsettledResources"] == []


def test_no_record_names_a_credential_path(sEvidencePath, pathStagingRoot):
    dictJob = _fdictRunJob(_flistPassingScript())
    sRecordText = json.dumps(dictJob)
    assert "vc_secret_" not in sRecordText
    assert str(pathStagingRoot) not in sRecordText


def test_a_job_id_that_is_not_hex_never_names_a_file():
    for sHostile in ("../evil", "", "a" * 31, "A" * 32, "a" * 32 + "/x"):
        with pytest.raises(ValueError):
            agentCouncilCredentialTestRecords.fsComposeJobRecordPath(sHostile)
