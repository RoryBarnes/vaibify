"""Every council token is staged through one of exactly two admitters.

Contracts A6 and A7 of the credential-consent plan. A campaign or chat
turn stages its token only through
``councilRouteGuards.ftAdmitCouncilTurnCredential``, which re-evaluates
consent under the store lock AFTER the slow login fetch, so a
withdrawal that lands mid-fetch is seen. A credential test stages only
through ``agentCouncilCredentialTest.ftAdmitCredentialTestCredential``,
which admits exactly one job's own runners while that job is the key's
test in flight and its consent generation is unchanged.

The keys are made distinct on purpose: the job id is not the provider,
the container id is not its name, and a campaign runner's label is a
real campaign label, never a job's.
"""

import ast
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from vaibify.gui import (
    agentCouncilCredentialGate,
    agentCouncilCredentialStore,
    agentCouncilCredentialTest,
    councilRouteGuards,
)

S_IMAGE = "sha256:" + "d4" * 32
S_OTHER_IMAGE = "sha256:" + "e5" * 32
S_CONTAINER_ID = "f00dcafe0123"
S_ACCESS_TOKEN = "sk-ant-oat01-SYNTHETIC-TOKEN-NOT-REAL"
S_JOB_ID = "a0a0a0a0b1b1b1b1c2c2c2c2d3d3d3d3"
S_TEST_LABEL = f"council-credentialTest-{S_JOB_ID}-0123456789ab"
S_CAMPAIGN_LABEL = "council-4f3e2d1c0b9a-0123456789ab"


@pytest.fixture
def sEvidencePath(tmp_path, monkeypatch):
    sPath = str(tmp_path / "agentCouncils" / "credentialEvidence.json")
    monkeypatch.setattr(
        agentCouncilCredentialGate, "fsResolveCredentialEvidencePath",
        lambda: sPath)
    return sPath


@pytest.fixture
def pathStagingRoot(tmp_path, monkeypatch):
    """Keep staged credential files out of the real home directory."""
    from vaibify.config import secretManager
    pathRoot = tmp_path / "staging"
    pathRoot.mkdir(mode=0o700)
    monkeypatch.setattr(
        secretManager, "_fsGetTempDirectory", lambda: str(pathRoot))
    return pathRoot


class FakeLoginDocker:
    """Serves a Claude login and refuses to be called under the lock."""

    def __init__(self, fnDuringFetch=None):
        self.fnDuringFetch = fnDuringFetch
        self.iFetchCount = 0

    def fbaFetchCredentialFile(self, sContainerId, sFilePath):
        if agentCouncilCredentialStore.fbIsStoreLockHeld():
            raise AssertionError("the login was fetched under the store lock")
        self.iFetchCount += 1
        if self.fnDuringFetch is not None:
            self.fnDuringFetch()
        return json.dumps({"claudeAiOauth": {
            "accessToken": S_ACCESS_TOKEN,
            "refreshToken": "never-copied",
            "scopes": ["user:inference"],
            "expiresAt": int((time.time() + 3600) * 1000),
        }}).encode("utf-8")


def _fnAuthorize(sEvidencePath, sProvider="claude", sImage=S_IMAGE):
    agentCouncilCredentialStore.fdictRecordConsent(
        sEvidencePath, sProvider, sImage)
    agentCouncilCredentialStore.fdictBeginCredentialTest(
        sEvidencePath, sProvider, sImage, "authorizingJob")
    agentCouncilCredentialStore.fdictPublishCredentialTestOutcome(
        sEvidencePath, sProvider, sImage, "authorizingJob", "passed")


def _fnStageThroughCampaignLane(dockerFake, sImage=S_IMAGE):
    fnStager = councilRouteGuards.ffnBuildCredentialStager(
        {"docker": dockerFake}, S_CONTAINER_ID, "claude", sImage)
    return fnStager()


# ----- A6: the per-turn admission --------------------------------------------


def test_an_authorized_turn_stages_the_token_and_records_the_admission(
        sEvidencePath, pathStagingRoot):
    _fnAuthorize(sEvidencePath)
    sStagedPath, iExpiresAt = _fnStageThroughCampaignLane(FakeLoginDocker())
    assert os.path.dirname(sStagedPath) == str(pathStagingRoot)
    assert os.stat(sStagedPath).st_mode & 0o777 == 0o600
    assert S_ACCESS_TOKEN in Path(sStagedPath).read_text()
    assert "never-copied" not in Path(sStagedPath).read_text()
    assert iExpiresAt > 0
    dictDocument = agentCouncilCredentialStore.fdictReadCredentialDocument(
        sEvidencePath)["dictDocument"]
    assert [dictAdmission["sAdmissionKind"]
            for dictAdmission in dictDocument["listAdmissions"]] == [
        "councilTurn"]
    assert S_ACCESS_TOKEN not in Path(sEvidencePath).read_text()


@pytest.mark.falsification
def test_withdrawal_between_two_turns_refuses_the_second(
        sEvidencePath, pathStagingRoot):
    """Turn 1 is admitted; after a withdrawal, turn 2 stages nothing.

    Kills: the per-turn admission staging without re-evaluating consent
    under the store lock.
    """
    _fnAuthorize(sEvidencePath)
    sFirst, _ = _fnStageThroughCampaignLane(FakeLoginDocker())
    os.remove(sFirst)
    agentCouncilCredentialStore.fdictWithdrawConsent(
        sEvidencePath, "claude", S_IMAGE)
    with pytest.raises(
            agentCouncilCredentialStore.CredentialAdmissionRefusedError
    ) as errorRefused:
        _fnStageThroughCampaignLane(FakeLoginDocker())
    assert "withdrew" in str(errorRefused.value)
    assert list(pathStagingRoot.iterdir()) == []


@pytest.mark.falsification
def test_a_withdrawal_during_the_fetch_is_seen_at_the_recheck(
        sEvidencePath, pathStagingRoot):
    """A withdrawal landing mid-fetch is seen at the locked recheck.

    Kills: the per-turn admission staging without re-evaluating consent
    under the store lock, so a withdrawal during the login fetch is
    missed.
    """
    _fnAuthorize(sEvidencePath)
    dockerFake = FakeLoginDocker(
        fnDuringFetch=lambda: agentCouncilCredentialStore.fdictWithdrawConsent(
            sEvidencePath, "claude", S_IMAGE))
    with pytest.raises(
            agentCouncilCredentialStore.CredentialAdmissionRefusedError):
        _fnStageThroughCampaignLane(dockerFake)
    assert dockerFake.iFetchCount == 1
    assert list(pathStagingRoot.iterdir()) == []


@pytest.mark.falsification
def test_the_turn_is_evaluated_for_the_campaigns_pinned_image(
        sEvidencePath, pathStagingRoot):
    """Authorization for another image does not admit this campaign.

    Kills: the stager evaluating a different image than the one the
    campaign's runners launch from.
    """
    _fnAuthorize(sEvidencePath, sImage=S_OTHER_IMAGE)
    with pytest.raises(
            agentCouncilCredentialStore.CredentialAdmissionRefusedError):
        _fnStageThroughCampaignLane(FakeLoginDocker(), sImage=S_IMAGE)
    assert list(pathStagingRoot.iterdir()) == []


def test_a_test_in_flight_refuses_campaign_turns(
        sEvidencePath, pathStagingRoot):
    _fnAuthorize(sEvidencePath)
    agentCouncilCredentialStore.fdictBeginCredentialTest(
        sEvidencePath, "claude", S_IMAGE, S_JOB_ID)
    with pytest.raises(
            agentCouncilCredentialStore.CredentialAdmissionRefusedError):
        _fnStageThroughCampaignLane(FakeLoginDocker())


_S_CHILD_WITHDRAWS = r"""
import sys
from vaibify.gui import agentCouncilCredentialStore
agentCouncilCredentialStore.fdictWithdrawConsent(sys.argv[1], "claude",
                                                 sys.argv[2])
"""


def test_a_withdrawal_by_another_hub_process_refuses_the_next_turn(
        sEvidencePath, pathStagingRoot):
    _fnAuthorize(sEvidencePath)
    sFirst, _ = _fnStageThroughCampaignLane(FakeLoginDocker())
    os.remove(sFirst)
    subprocess.run(
        [sys.executable, "-c", _S_CHILD_WITHDRAWS, sEvidencePath, S_IMAGE],
        check=True, env={**os.environ, "PYTHONPATH": os.getcwd()})
    with pytest.raises(
            agentCouncilCredentialStore.CredentialAdmissionRefusedError):
        _fnStageThroughCampaignLane(FakeLoginDocker())
    assert list(pathStagingRoot.iterdir()) == []


@pytest.mark.falsification
def test_a_refused_admission_is_filed_as_needing_the_researcher():
    """The engine files the refusal under its own class, not turnRaised.

    Kills: the engine filing every raised turn as turnRaised, hiding a
    withdrawn consent as a transport fault.
    """
    import asyncio
    from vaibify.gui import agentCouncil

    class RefusingConnection:
        async def fdictPrepareImmutableContext(self, dictRequest):
            raise agentCouncilCredentialStore.CredentialAdmissionRefusedError(
                "claude: consent withdrawn")

    engine = agentCouncil.CouncilEngine.__new__(agentCouncil.CouncilEngine)
    engine.dictConnections = {"p1": RefusingConnection()}
    dictAttempt = asyncio.run(engine._fdictDriveConnection(
        {"sParticipantId": "p1"}, {"sTurnId": "t1"}))
    assert dictAttempt["sFailureClass"] == "credentialAdmissionRefused"
    assert "consent withdrawn" in dictAttempt["sFailureReason"]


def test_the_refusal_class_is_on_the_retry_whitelist():
    from vaibify.gui import agentCouncilCampaign
    assert (agentCouncilCredentialStore.S_FAILURE_CLASS_ADMISSION_REFUSED
            in agentCouncilCampaign.SET_RETRYABLE_TURN_FAILURE_REASONS)


def test_a_chat_opened_after_withdrawal_delivers_nothing(
        sEvidencePath, pathStagingRoot, monkeypatch):
    """A chat stages once, at open; after a withdrawal the next open fails.

    An already-open conversation holds the copy it was admitted with
    until its session ceiling (ruling 1: admitted work finishes); what
    must fail is every later admission.
    """
    from vaibify.gui import agentCouncilChat, agentCouncilProviders
    listDelivered = []
    monkeypatch.setattr(
        agentCouncilProviders, "fnDeliverCredentialIntoRunner",
        lambda *args: listDelivered.append(args))
    monkeypatch.setattr(agentCouncilChat, "_fjsonReadCampaignNow",
                        lambda dictSession: {})
    monkeypatch.setattr(agentCouncilChat.agentCouncilCharter,
                        "fsComposeChatInstruction", lambda *args: "x")
    _fnAuthorize(sEvidencePath)
    dictSession = {
        "ftStageRunnerCredential": councilRouteGuards.ffnBuildCredentialStager(
            {"docker": FakeLoginDocker()}, S_CONTAINER_ID, "claude", S_IMAGE),
        "sProvider": "claude", "dictParticipant": {},
        "dictGateway": {}, "sHandle": "h"}
    agentCouncilChat._fnDeliverChatCredential(dictSession)
    assert len(listDelivered) == 1
    agentCouncilCredentialStore.fdictWithdrawConsent(
        sEvidencePath, "claude", S_IMAGE)
    with pytest.raises(
            agentCouncilCredentialStore.CredentialAdmissionRefusedError):
        agentCouncilChat._fnDeliverChatCredential(dictSession)
    assert len(listDelivered) == 1
    assert list(pathStagingRoot.iterdir()) == []


# ----- A7: the credential test's own admitter ---------------------------------


def _fnBeginJob(sEvidencePath, sJobId=S_JOB_ID, sProvider="claude"):
    agentCouncilCredentialStore.fdictRecordConsent(
        sEvidencePath, sProvider, S_IMAGE)
    agentCouncilCredentialStore.fdictBeginCredentialTest(
        sEvidencePath, sProvider, S_IMAGE, sJobId)


def _ftAdmitTest(sJobId=S_JOB_ID, sLabel=S_TEST_LABEL, sProvider="claude",
                 sImage=S_IMAGE):
    return agentCouncilCredentialTest.ftAdmitCredentialTestCredential(
        sJobId, sProvider, sImage, sLabel,
        {"sAccessToken": S_ACCESS_TOKEN, "listScopes": ["user:inference"],
         "iExpiresAtEpochMilliseconds": 1})


def test_the_in_flight_job_admits_its_own_runner(
        sEvidencePath, pathStagingRoot):
    _fnBeginJob(sEvidencePath)
    sStagedPath, _ = _ftAdmitTest()
    assert S_ACCESS_TOKEN in Path(sStagedPath).read_text()
    dictDocument = agentCouncilCredentialStore.fdictReadCredentialDocument(
        sEvidencePath)["dictDocument"]
    assert dictDocument["listAdmissions"][-1]["sAdmissionKind"] == (
        "credentialTest")
    assert dictDocument["listAdmissions"][-1]["sJobId"] == S_JOB_ID


def _fnAssertRefused(pathStagingRoot, **dictArguments):
    with pytest.raises(
            agentCouncilCredentialStore.CredentialAdmissionRefusedError):
        _ftAdmitTest(**dictArguments)
    assert list(pathStagingRoot.iterdir()) == []


@pytest.mark.falsification
def test_a_campaign_runner_is_refused_even_with_a_valid_job_id(
        sEvidencePath, pathStagingRoot):
    """A campaign runner never borrows a test's narrow authority.

    Kills: the test admitter not checking the runner's council label, so
    a campaign runner could be staged with a test's authority.
    """
    _fnBeginJob(sEvidencePath)
    _fnAssertRefused(pathStagingRoot, sLabel=S_CAMPAIGN_LABEL)


def test_another_jobs_runner_is_refused(sEvidencePath, pathStagingRoot):
    _fnBeginJob(sEvidencePath)
    _fnAssertRefused(
        pathStagingRoot,
        sLabel="council-credentialTest-ffffffffffffffff-0123456789ab")


def test_an_unknown_job_id_is_refused(sEvidencePath, pathStagingRoot):
    _fnBeginJob(sEvidencePath)
    _fnAssertRefused(pathStagingRoot, sJobId="0" * 32,
                     sLabel="council-credentialTest-" + "0" * 32 + "-ab")


@pytest.mark.falsification
def test_a_stale_job_replaced_by_a_newer_one_is_refused(
        sEvidencePath, pathStagingRoot):
    """A job no longer in flight for its key cannot stage a token.

    Kills: the test admitter not comparing the job id with the key's in-
    flight job.
    """
    _fnBeginJob(sEvidencePath)
    agentCouncilCredentialStore.fdictBeginCredentialTest(
        sEvidencePath, "claude", S_IMAGE, "b" * 32)
    _fnAssertRefused(pathStagingRoot)


def test_a_cancelled_job_is_refused(sEvidencePath, pathStagingRoot):
    _fnBeginJob(sEvidencePath)
    agentCouncilCredentialStore.fdictPublishCredentialTestOutcome(
        sEvidencePath, "claude", S_IMAGE, S_JOB_ID, "incomplete")
    _fnAssertRefused(pathStagingRoot)


def test_another_keys_job_is_refused(sEvidencePath, pathStagingRoot):
    _fnBeginJob(sEvidencePath, sProvider="codex")
    _fnAssertRefused(pathStagingRoot)
    _fnAssertRefused(pathStagingRoot, sImage=S_OTHER_IMAGE)


def test_a_withdrawal_mid_test_refuses_the_next_test_turn(
        sEvidencePath, pathStagingRoot):
    _fnBeginJob(sEvidencePath)
    sStagedPath, _ = _ftAdmitTest()
    os.remove(sStagedPath)
    agentCouncilCredentialStore.fdictWithdrawConsent(
        sEvidencePath, "claude", S_IMAGE)
    _fnAssertRefused(pathStagingRoot)


@pytest.mark.falsification
def test_a_consent_generation_change_refuses_the_test(
        sEvidencePath, pathStagingRoot):
    """Withdraw then consent again: active, but not the job's generation.

    Kills: the test admitter not comparing the consent generation with
    the job's.
    """
    _fnBeginJob(sEvidencePath)
    dictDocument = agentCouncilCredentialStore.fdictReadCredentialDocument(
        sEvidencePath)["dictDocument"]
    sKey = agentCouncilCredentialStore.fsComposeCredentialKey(
        "claude", S_IMAGE)

    def _fnBumpGeneration(dictMutable):
        dictMutable["dictConsents"][sKey]["iConsentGeneration"] += 2

    agentCouncilCredentialStore.fdictMutateCredentialDocument(
        sEvidencePath, _fnBumpGeneration)
    assert dictDocument["dictConsents"][sKey]["sState"] == "active"
    _fnAssertRefused(pathStagingRoot)


@pytest.mark.falsification
def test_a_withdrawn_consent_at_the_jobs_generation_is_refused(
        sEvidencePath, pathStagingRoot):
    """Consent state is checked on its own, not only via the generation.

    Kills: the test admitter not checking that consent is still active.
    """
    _fnBeginJob(sEvidencePath)
    sKey = agentCouncilCredentialStore.fsComposeCredentialKey(
        "claude", S_IMAGE)

    def _fnWithdrawInPlace(dictMutable):
        dictMutable["dictConsents"][sKey]["sState"] = "withdrawn"

    agentCouncilCredentialStore.fdictMutateCredentialDocument(
        sEvidencePath, _fnWithdrawInPlace)
    _fnAssertRefused(pathStagingRoot)


@pytest.mark.falsification
def test_a_marker_naming_another_image_is_refused(
        sEvidencePath, pathStagingRoot):
    """A marker whose recorded image differs is refused.

    Kills: the test admitter not comparing the job's recorded provider
    and image.
    """
    _fnBeginJob(sEvidencePath)
    sKey = agentCouncilCredentialStore.fsComposeCredentialKey(
        "claude", S_IMAGE)

    def _fnCorruptMarker(dictMutable):
        dictMutable["dictInFlight"][sKey]["sImageIdentity"] = S_OTHER_IMAGE

    agentCouncilCredentialStore.fdictMutateCredentialDocument(
        sEvidencePath, _fnCorruptMarker)
    _fnAssertRefused(pathStagingRoot)


def test_the_label_predicate_is_exact():
    assert agentCouncilCredentialTest.fbRunnerLabelBelongsToJob(
        S_TEST_LABEL, S_JOB_ID)
    assert not agentCouncilCredentialTest.fbRunnerLabelBelongsToJob(
        S_CAMPAIGN_LABEL, S_JOB_ID)
    assert not agentCouncilCredentialTest.fbRunnerLabelBelongsToJob(
        S_TEST_LABEL, "")
    assert not agentCouncilCredentialTest.fbRunnerLabelBelongsToJob(
        "", S_JOB_ID)
    assert not agentCouncilCredentialTest.fbRunnerLabelBelongsToJob(
        f"council-credentialTest-{S_JOB_ID}extra-01", S_JOB_ID)


# ----- structural: exactly two admitters ---------------------------------------


def _flistCallSitesOf(sCalleeName):
    """Return ``module:outermost function`` for every call of a name."""
    pathPackage = Path(__file__).resolve().parents[1] / "vaibify"
    listSites = []
    for pathSource in sorted(pathPackage.rglob("*.py")):
        nodeModule = ast.parse(pathSource.read_text(encoding="utf-8"))
        for nodeTop in nodeModule.body:
            for nodeInner in ast.walk(nodeTop):
                if isinstance(nodeInner, ast.Call) and (
                        getattr(nodeInner.func, "attr", None) == sCalleeName
                        or getattr(nodeInner.func, "id", None)
                        == sCalleeName):
                    listSites.append(
                        f"{pathSource.stem}:"
                        f"{getattr(nodeTop, 'name', '<module>')}")
    return sorted(listSites)


def test_exactly_two_functions_stage_a_council_token():
    assert _flistCallSitesOf("fsStageProviderCredential") == [
        "agentCouncilCredentialTest:ftAdmitCredentialTestCredential",
        "councilRouteGuards:ftAdmitCouncilTurnCredential",
    ]


def test_no_module_reaches_a_provider_stager_around_the_registry():
    for sProviderStager in ("fsStageRunnerCredentialFile",
                            "fsStageCodexRunnerCredentialFile",
                            "fsStageAntigravityRunnerCredentialFile"):
        for sSite in _flistCallSitesOf(sProviderStager):
            assert sSite.startswith("agentCouncilProviderRegistry:"), sSite


def test_campaign_and_chat_code_is_never_handed_the_test_admitter():
    assert _flistCallSitesOf("ftAdmitCredentialTestCredential") == [
        "agentCouncilCredentialTest:_ffnBuildTestStager"]
    assert _flistCallSitesOf("ftAdmitCouncilTurnCredential") == [
        "councilRouteGuards:ffnBuildCredentialStager"]
