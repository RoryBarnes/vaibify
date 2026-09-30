"""Live: the credential test runs its checks over REAL disposable runners.

Plan contracts A2 and A4 on a real daemon. The runners are real — the
same gateway, reservation, copy-in and destroy-with-proof a council
uses — and the only stand-in is the provider CLI: a deterministic
script shipped in the runner's snapshot, which answers a trivial turn,
reports an error for an invalid model, and hangs when asked for the
interrupted turn so the gateway's own wall-clock kill ends it. The
project login is served host-side, because the project container is
not what this lane tests.

The second test is the one the plan singles out: a hub KILLED mid-test.
The job runs in a child process that is sent SIGKILL during its slow
turn; this process then runs the restart sweep and must find the key
``incomplete``, the runner gone, and no staged copy left behind.

No test here proves anything about a real subscription token.
"""

import json
import os
import signal
import subprocess
import sys
import time

import pytest

from tests.testDockerConnectionLive import fnRequireDaemonReachable

pytestmark = pytest.mark.docker_live

from tests.councilCredentialLiveHelpers import (
    S_RESOURCE_NAME,
    S_RUNNER_TEST_IMAGE,
    fdictBuildLiveRuntime,
)


def _fsResolveImageIdentity():
    import docker
    clientDocker = docker.from_env()
    try:
        imageRunner = clientDocker.images.get(S_RUNNER_TEST_IMAGE)
    except docker.errors.ImageNotFound:
        imageRunner = clientDocker.images.pull(S_RUNNER_TEST_IMAGE)
    return imageRunner.id


def _flistRunnersOfJob(sJobId):
    from vaibify.gui import agentCouncilCredentialTest, agentCouncilDockerGateway
    return [dictSurvivor for dictSurvivor
            in agentCouncilDockerGateway.flistDiscoverLabeledRunners(
                agentCouncilDockerGateway.fdockerCreateCouncilClient())
            if agentCouncilCredentialTest.fbRunnerLabelBelongsToJob(
                dictSurvivor["sReservationId"], sJobId)]


@pytest.fixture
def sLiveHome(tmp_path, monkeypatch):
    fnRequireDaemonReachable()
    from vaibify.config import secretManager
    from vaibify.gui import agentCouncilCredentialGate
    sEvidencePath = str(tmp_path / "agentCouncils" / "credentialEvidence.json")
    monkeypatch.setattr(agentCouncilCredentialGate,
                        "fsResolveCredentialEvidencePath",
                        lambda: sEvidencePath)
    (tmp_path / "staging").mkdir(mode=0o700)
    monkeypatch.setattr(secretManager, "_fsGetTempDirectory",
                        lambda: str(tmp_path / "staging"))
    return tmp_path


def test_live_credential_test_passes_every_check_over_real_runners(sLiveHome):
    from vaibify.gui import (
        agentCouncilCredentialGate, agentCouncilCredentialTest,
        agentCouncilCredentialTestRecords)
    sImageIdentity = _fsResolveImageIdentity()
    dictJobs = {}
    dictStarted = agentCouncilCredentialTest.fdictStartCredentialTest(
        dictJobs, "claude", sImageIdentity, S_RESOURCE_NAME, "haiku",
        fdictBuildLiveRuntime(sImageIdentity))
    dictJobs[dictStarted["sJobId"]]["threadJob"].join(timeout=300)
    dictJob = agentCouncilCredentialTestRecords.fdictReadJobRecord(
        dictStarted["sJobId"])
    assert dictJob["sStatus"] == "passed", dictJob
    assert agentCouncilCredentialGate.fdictEvaluateCredentialEnablement(
        "claude", sImageIdentity)["bEnabled"] is True
    assert _flistRunnersOfJob(dictStarted["sJobId"]) == []
    assert list((sLiveHome / "staging").iterdir()) == []




def test_live_a_hub_killed_mid_test_is_swept_at_restart(sLiveHome):
    """A second hub spares a live test; after SIGKILL it settles it."""
    from vaibify.gui import (
        agentCouncilCredentialGate, agentCouncilCredentialTestRecords,
        agentCouncilCredentialTestRecovery, agentCouncilDockerGateway)
    sImageIdentity = _fsResolveImageIdentity()
    sRepository = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    processHub = subprocess.Popen(
        [sys.executable, "-m", "tests.councilCredentialLiveHelpers",
         agentCouncilCredentialGate.fsResolveCredentialEvidencePath(),
         str(sLiveHome / "staging"), sImageIdentity],
        stdout=subprocess.PIPE, text=True, cwd=sRepository,
        env={**os.environ, "HOME": str(sLiveHome),
             "PYTHONPATH": sRepository})
    try:
        sJobId = json.loads(processHub.stdout.readline())["sJobId"]
        fDeadline = time.monotonic() + 120
        while not _flistRunnersOfJob(sJobId) and time.monotonic() < fDeadline:
            time.sleep(1)
        assert _flistRunnersOfJob(sJobId), "the job never started a runner"
        dictWhileAlive = (
            agentCouncilCredentialTestRecovery.fdictSweepOrphanedCredentialTests(
                agentCouncilDockerGateway.fdockerCreateCouncilClient()))
        assert sJobId in dictWhileAlive["listSpared"], (
            "a second hub swept a LIVE hub's credential test")
        assert _flistRunnersOfJob(sJobId), (
            "the live hub's runner was destroyed by the other hub's sweep")
    finally:
        processHub.send_signal(signal.SIGKILL)
        processHub.wait(timeout=30)
    dictReport = agentCouncilCredentialTestRecovery.fdictSweepOrphanedCredentialTests(
        agentCouncilDockerGateway.fdockerCreateCouncilClient())
    assert sJobId in dictReport["listSwept"]
    assert _flistRunnersOfJob(sJobId) == []
    assert list((sLiveHome / "staging").iterdir()) == []
    assert agentCouncilCredentialTestRecords.fdictReadJobRecord(sJobId)[
        "sStatus"] == "incomplete"
    assert agentCouncilCredentialGate.fdictEvaluateCredentialEnablement(
        "claude", sImageIdentity)["sState"] == "lastTestIncomplete"
