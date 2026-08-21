"""Lane 3, controller leg: HTTP start → real runners → planReady.

The R1 definition-of-done proof: a campaign started over REAL HTTP is
driven by the REAL controller through the REAL capture (live daemon,
coherence observations and all), builds REAL disposable runners through
the gateway, runs a deterministic fake provider CLI inside them, and
reaches planReady with no hand-patched state — then leaves no council
container behind. The stale-baseline producer is exercised against the
same live repository: fresh right after capture, stale after a commit.

The provider inside the runner is a scripted fake (lane 3 proves
containment and integration, never a paid turn — see
design/agentCouncilVerificationLanes.md); the credential gate is
patched enabled because lane 4's evidence record is the maintainer's.
Container name != id throughout, by construction: real Docker ids.
"""

import os
import secrets
import time

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch

from tests.testDockerConnectionLive import fnRequireDaemonReachable
from vaibify.config import registryManager
from vaibify.gui import (
    agentCouncilController,
    agentCouncilProviders,
    agentCouncilStore,
    browserSession,
    containerOwnership,
    pipelineServer,
)
from tests.sessionTokenTestHelper import fsBootstrapCredential
from tests.testAgentCouncilProvidersLive import (
    LIST_FAKE_CLI_PROGRAM,
    S_FAKE_PROVIDER_SCRIPT,
    _fdictSmallLimits,
)


pytestmark = pytest.mark.docker_live

S_THROWAWAY_IMAGE = "alpine:3.20"
S_RUNNER_IMAGE = "python:3.10-slim"
S_REPO_ROOT = "/home/researcher/sampleRepo"


@pytest.fixture(autouse=True)
def fixtureIsolateRegistry(tmp_path, monkeypatch):
    sRegistryDirectory = str(tmp_path / ".vaibify-registry")
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_DIRECTORY", sRegistryDirectory)
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_PATH",
        os.path.join(sRegistryDirectory, "registry.json"))


@pytest.fixture
def tLiveProjectFixture():
    """A real container holding a git repo that CARRIES the fake CLI."""
    fnRequireDaemonReachable()
    import docker
    clientDocker = docker.from_env()
    sName = f"vaibifyCouncilCtl{secrets.token_hex(4)}"
    container = clientDocker.containers.run(
        S_THROWAWAY_IMAGE, ["sleep", "600"], name=sName, detach=True)
    try:
        iExitCode, _ = container.exec_run(
            ["/bin/sh", "-c", "apk add --no-cache git bash python3"])
        if iExitCode != 0:
            pytest.skip("cannot install git+bash+python3 (no network?)")
        iExitCode, baOutput = container.exec_run(
            ["/bin/sh", "-c", "adduser -D researcher"])
        assert iExitCode == 0, baOutput
        sBuildScript = (
            "set -e\n"
            f"mkdir -p {S_REPO_ROOT}\n"
            f"cd {S_REPO_ROOT}\n"
            "git init -q\n"
            "git config user.email fixture@example.invalid\n"
            "git config user.name Fixture\n"
            "printf 'alpha payload\\n' > dataFile.txt\n"
            "cat > fakeProvider.py <<'PYEOF'\n"
            + S_FAKE_PROVIDER_SCRIPT +
            "\nPYEOF\n"
            "git add -A\n"
            "git commit -q -m 'fixture state'\n")
        iExitCode, baOutput = container.exec_run(
            ["/bin/bash", "-c", sBuildScript], user="researcher")
        assert iExitCode == 0, baOutput.decode()
        # The launch-time login-presence probe reads the workspace
        # root's persisted login, so the live lane must model a project
        # the researcher HAS logged in to — the precondition for an
        # enabled runner backend. Written as root because /workspace is
        # root-owned in this throwaway image. The token is a fixture
        # string; nothing here ever authenticates to a provider.
        iExitCode, baOutput = container.exec_run(
            ["/bin/sh", "-c",
             "mkdir -p /workspace/.claude && printf '%s' "
             "'{\"claudeAiOauth\":{\"accessToken\":\"fixture-token\"}}' "
             "> /workspace/.claude/.credentials.json"])
        assert iExitCode == 0, baOutput.decode()
        yield (sName, container.id, container)
    finally:
        try:
            container.remove(force=True)
        except Exception:
            pass


def test_http_start_reaches_plan_ready_over_real_runners(
        tLiveProjectFixture, tmp_path, monkeypatch):
    sName, sContainerId, containerReal = tLiveProjectFixture
    assert sName != sContainerId

    def _fconnectionRealRunnerWithFakeCli(dictRuntime, dictParticipant):
        return agentCouncilProviders.ClaudeRunnerConnection(
            agentCouncilController._fdictEnsureRuntimeGateway(dictRuntime),
            dictRuntime["sCampaignId"], S_RUNNER_IMAGE,
            dictRuntime["baSnapshotTar"],
            dictParticipant["sRequestedModel"],
            saCliProgram=LIST_FAKE_CLI_PROGRAM,
            dictLimits=_fdictSmallLimits(), fWallClockSeconds=60.0)

    monkeypatch.setattr(
        agentCouncilController, "fconnectionBuildParticipantConnection",
        _fconnectionRealRunnerWithFakeCli)
    from vaibify.gui import agentCouncilCredentialGate
    monkeypatch.setattr(
        agentCouncilCredentialGate, "fdictEvaluateCredentialEnablement",
        lambda sProvider, sImageIdentity=None: {
            "bEnabled": True, "sReason": "", "dictRecord": {}})

    app = pipelineServer.fappCreateApplication(
        sWorkspaceRoot="/workspace", sTerminalUserArg="testuser")
    app.state.dictCouncilCampaignStore = (
        agentCouncilStore.fdictCreateCampaignStore(
            sDurableStoreRoot=str(tmp_path / "councils")))
    app.state.dictRouteContext["workflows"][sContainerId] = {
        "sProjectRepoPath": S_REPO_ROOT}
    sCredential = fsBootstrapCredential(app)
    sBrowserSessionId = browserSession.fsSessionIdForCredential(
        app.state.dictBrowserSessions, sCredential)
    sLease = containerOwnership.fsMintLease()
    app.state.dictContainerOwners[sName] = containerOwnership.OwnerRecord(
        sLeaseId=sLease, fileHandleLock=None, sAgentToken="tok",
        sContainerId=sContainerId, sBrowserSessionId=sBrowserSessionId)

    with TestClient(app, headers={
        "X-Session-Token": sCredential, "X-Vaibify-Lease": sLease,
    }) as client:
        response = client.post(
            f"/api/agent-councils/{sContainerId}/start",
            json={
                "sQuestion": "How should the change be implemented?",
                "listParticipants": [
                    {"sProvider": "claude", "sRequestedModel": "sonnet"},
                    {"sProvider": "claude", "sRequestedModel": "haiku"},
                ],
                "dictSettings": {"iMaximumRounds": 1},
            })
        assert response.status_code == 200, response.text
        sCampaignId = response.json()["sCampaignId"]

        fDeadline = time.monotonic() + 300.0
        sState = ""
        while time.monotonic() < fDeadline:
            dictRecord = agentCouncilStore.fjsonGetCampaignRecord(
                app.state.dictCouncilCampaignStore, sCampaignId)
            sState = dictRecord["sState"]
            if sState in ("planReady", "failed", "interrupted"):
                break
            time.sleep(1.0)
        assert sState == "planReady", (
            f"campaign settled at {sState!r}: "
            f"{dictRecord['listStateTransitions']}")
        assert dictRecord["dictCandidatePlan"]["dictResult"]["sVerdict"] \
            == "accept"
        # The identity triple carries the REAL sealed snapshot hash.
        assert dictRecord["dictProjectIdentity"]["sSnapshotIdentity"]

        # The stale-baseline producer against the LIVE repository:
        # fresh now, stale after the repo moves.
        sBase = f"/api/agent-councils/{sContainerId}/{sCampaignId}"
        dictFresh = client.get(sBase).json()["dictCampaign"]
        assert dictFresh["bPlanningBaselineStale"] is False, (
            dictFresh["sPlanningBaselineSummary"])
        iExitCode, baOutput = containerReal.exec_run(
            ["/bin/bash", "-c",
             f"cd {S_REPO_ROOT} && printf 'moved\\n' > dataFile.txt && "
             "git commit -qam 'move the baseline'"],
            user="researcher")
        assert iExitCode == 0, baOutput
        dictStale = client.get(sBase).json()["dictCampaign"]
        assert dictStale["bPlanningBaselineStale"] is True
        assert "commit moved" in dictStale["sPlanningBaselineSummary"]

    # No council container survives the campaign (every runner was
    # destroyed with proven absence by the connection's own path).
    import docker
    listSurvivors = docker.from_env().containers.list(
        all=True, filters={"label": "vaibify-council"})
    assert listSurvivors == [], [
        containerLeft.name for containerLeft in listSurvivors]
