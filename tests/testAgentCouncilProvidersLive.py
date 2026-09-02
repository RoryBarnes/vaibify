"""Live falsification of the Claude runner-backend adapter (design section 16).

Phase 2 exit criterion: a headless fake campaign runs through the runner
backend and the real context boundary — a runner turn parsed from real
stream-json, a full engine campaign reaching planReady over real
disposable runners, the shutdown drain destroying a live runner, restart
after runner-orphaning, and the ownership-stamped credential landing
1000-owned inside a runner. The detached-descendant, resource-limit and
proxy-escape falsifications already live in
``testAgentCouncilRunnerLive.py`` / ``testAgentCouncilEgressLive.py`` and
are not duplicated here.

The provider is a deterministic, test-owned executable that runs INSIDE
the runner and emits scripted stream-json, so the adapter is driven
end-to-end without a paid provider call. Live-daemon convention matches
``testAgentCouncilRunnerLive``: skip with no daemon,
``VAIBIFY_REQUIRE_DOCKER_DAEMON`` turns the skip into a failure.
"""

import asyncio
import io
import os
import secrets
import tarfile

import pytest

from tests.testDockerConnectionLive import fnRequireDaemonReachable
from vaibify.gui import agentCouncilDockerGateway as moduleGateway
from vaibify.gui import agentCouncilRegistry as registry
from vaibify.gui import agentCouncilRunner
from vaibify.gui.agentCouncilProviders import (
    ClaudeRunnerConnection,
    ffnBuildBaselineEvidenceExecutor,
    fbaBuildCredentialTarball,
    fnDeliverCredentialIntoRunner,
    fsStageRunnerCredentialFile,
    S_RUNNER_CLAUDE_CONFIG_DIRECTORY,
)

pytestmark = pytest.mark.docker_live

S_RUNNER_TEST_IMAGE = os.environ.get(
    "VAIBIFY_COUNCIL_TEST_IMAGE", "python:3.10-slim")
I_MEBIBYTE = 1024 * 1024

# The deterministic fake provider: reads --model and stdin, emits the
# stream-json shape the adapter parses (init model, assistant, a result
# whose `result` is the §8.5 structured turn schema). It records the
# stdin byte count so a test can prove untrusted material was delivered
# through stdin, not argv.
S_FAKE_PROVIDER_SCRIPT = r'''
import sys, json
saArgv = sys.argv[1:]
sModel = saArgv[saArgv.index("--model") + 1] if "--model" in saArgv else ""
baStdin = sys.stdin.buffer.read()
iStdinBytes = len(baStdin)
print(json.dumps({"type": "system", "subtype": "init",
                  "model": sModel, "session_id": "fake"}), flush=True)
print(json.dumps({"type": "assistant",
                  "message": {"model": sModel,
                              "content": [{"type": "text",
                                           "text": "deliberating"}]}}),
      flush=True)
dictResult = {
    "sSummary": "fake council turn stdin_bytes=%d model=%s" % (
        iStdinBytes, sModel),
    "sVerdict": "accept",
    "listAssumptions": [], "listEvidence": [], "listMathematicalClaims": [],
    "listArchitectureClaims": [], "listSecurityRisks": [],
    "listCounterexamplesAttempted": [], "listPlanItems": [],
    "listRejectedAlternatives": [], "listVerificationRequirements": [],
    "listStopConditions": [], "listNotedFindings": [],
    "listOpenQuestions": [], "listBlockingObjections": [],
}
print(json.dumps({"type": "result", "subtype": "success", "is_error": False,
                  "result": json.dumps(dictResult),
                  "modelUsage": {sModel: {"inputTokens": iStdinBytes,
                                          "outputTokens": 10}},
                  "usage": {"input_tokens": iStdinBytes, "output_tokens": 10},
                  "num_turns": 1}), flush=True)
'''

S_FAKE_PROVIDER_CONTAINER_PATH = (
    agentCouncilRunner.S_RUNNER_SNAPSHOT_ROOT + "/fakeProvider.py")
LIST_FAKE_CLI_PROGRAM = ["python3", S_FAKE_PROVIDER_CONTAINER_PATH]


def _fdictSmallLimits(**dictOverrides):
    dictLimits = agentCouncilRunner.fdictBuildDefaultRunnerLimits()
    dictLimits.update({
        "iMemoryBytes": 256 * I_MEBIBYTE,
        "iWorkingTreeBytes": 64 * I_MEBIBYTE,
        "iScratchBytes": 16 * I_MEBIBYTE,
        "iPidsLimit": 64,
    })
    dictLimits.update(dictOverrides)
    return dictLimits


def _fbaBuildFakeProviderSnapshot():
    """Build a snapshot tarball carrying the fake provider at the repo root.

    Members are repo-relative (no root component), matching the real
    context snapshot's shape, so extraction into /council places the
    fake at the path the adapter's CLI program points at.
    """
    bufferTar = io.BytesIO()
    with tarfile.open(fileobj=bufferTar, mode="w") as fileTar:
        baScript = S_FAKE_PROVIDER_SCRIPT.encode("utf-8")
        infoScript = tarfile.TarInfo(name="fakeProvider.py")
        infoScript.size = len(baScript)
        infoScript.mode = 0o644
        fileTar.addfile(infoScript, io.BytesIO(baScript))
        baProject = b'{"name": "council-fake"}'
        infoProject = tarfile.TarInfo(name="project.json")
        infoProject.size = len(baProject)
        fileTar.addfile(infoProject, io.BytesIO(baProject))
    return bufferTar.getvalue()


@pytest.fixture
def tCouncilLiveHarness():
    """Yield (dictGateway, listCreatedContainerIds), gateway-first (R4)."""
    fnRequireDaemonReachable()
    dockerCouncil = moduleGateway.fdockerCreateCouncilClient()
    dictGateway = moduleGateway.fdictCreateCouncilDockerGateway(
        dockerCouncil, registry.fdictCreateCouncilRegistry())
    listCreatedContainerIds = []
    try:
        yield (dictGateway, listCreatedContainerIds)
    finally:
        for sContainerId in listCreatedContainerIds:
            try:
                dockerCouncil.api.remove_container(
                    sContainerId, force=True, v=True)
            except Exception:
                pass


def _fsContainerIdForHandle(dictGateway, sHandle):
    """Read the container id a live handle currently names."""
    return dictGateway["dictHandlesById"][sHandle]["sContainerId"]


def _fdictBuildTurnRequest(sMarker):
    return {
        "sTurnId": "turn-" + secrets.token_hex(4),
        "sCampaignId": "camp" + secrets.token_hex(4),
        "sParticipantId": "participant-1",
        "sPhase": "independentProposals",
        "iRoundNumber": 1,
        "sInstructionChannel": "COUNCIL CHARTER (test)\n\nPHASE: proposal.",
        "listQuotedMaterial": [{
            "sSourceKind": "researcherQuestion", "sAuthorIdentity": "researcher",
            "sLabel": "quoted untrusted material", "sContent": sMarker}],
        "bRepairRequest": False,
        "listSchemaProblems": [],
    }


def test_adapter_turn_parses_real_stream_json_and_proves_destruction(
        tCouncilLiveHarness):
    """One turn through the real seam: create, run the CLI, parse, destroy.

    The fake runs inside the runner, reads stdin (proving untrusted
    material was delivered off-argv) and emits stream-json; the adapter
    extracts the structured result and resolved model, then proves the
    runner gone.
    """
    dictGateway, listCreatedContainerIds = tCouncilLiveHarness
    baSnapshot = _fbaBuildFakeProviderSnapshot()
    sMarker = "UNTRUSTED_MARKER_" + secrets.token_hex(4)
    dictRequest = _fdictBuildTurnRequest(sMarker)
    connection = ClaudeRunnerConnection(
        dictGateway, dictRequest["sCampaignId"], S_RUNNER_TEST_IMAGE,
        baSnapshot, "sonnet",
        saCliProgram=LIST_FAKE_CLI_PROGRAM, dictLimits=_fdictSmallLimits(),
        fWallClockSeconds=60.0)
    listRunnerContainerIds = []

    async def _fdictRunOneTurn():
        await connection.fdictPrepareImmutableContext(dictRequest)
        listRunnerContainerIds.append(
            _fsContainerIdForHandle(dictGateway, connection._sHandle))
        listCreatedContainerIds.append(listRunnerContainerIds[-1])
        await connection.fnStartTurn(dictRequest)
        listEvents = [dictEvent async for dictEvent
                      in connection.fiterStreamNormalizedEvents()]
        dictResult = await connection.fdictCollectStructuredResult()
        sCompletion = await connection.fsReportCompletion()
        return listEvents, dictResult, sCompletion

    listEvents, dictResult, sCompletion = asyncio.run(_fdictRunOneTurn())

    assert any(dictEvent.get("type") == "system" for dictEvent in listEvents)
    assert dictResult["sVerdict"] == "accept"
    # The fake echoed the stdin byte count; a positive count proves the
    # untrusted material reached the CLI through stdin, not argv.
    assert "stdin_bytes=" in dictResult["sSummary"]
    iStdinBytes = int(dictResult["sSummary"].split(
        "stdin_bytes=")[1].split()[0])
    assert iStdinBytes > 0
    assert connection.dictModelIdentity["sResolvedModel"] == "sonnet"
    assert sCompletion == "terminal"

    dictProbe = moduleGateway.fdictProbeRunnerAbsence(
        dictGateway["dockerCouncil"], listRunnerContainerIds[0])
    assert dictProbe["sAnswer"] == agentCouncilRunner.S_ABSENCE_ABSENT
    # The settled reservation freed its budget: nothing live remains.
    assert dictGateway["dictRegistry"]["dictReservationsById"] == {}


def test_fake_campaign_reaches_plan_ready_over_real_runners(
        tCouncilLiveHarness):
    """A two-model campaign runs to planReady over real disposable runners."""
    from vaibify.gui.agentCouncil import CouncilEngine, S_STATE_PLAN_READY
    from vaibify.gui.agentCouncilCampaign import (
        fdictCreateCampaign, fdictCreateParticipant)
    from vaibify.gui.agentCouncilStore import (
        CouncilEvidenceLedger, InMemoryCampaignCheckpoint)

    dictGateway, listCreatedContainerIds = tCouncilLiveHarness
    baSnapshot = _fbaBuildFakeProviderSnapshot()
    listParticipants = [
        fdictCreateParticipant("claude", "sonnet"),
        fdictCreateParticipant("claude", "haiku"),
    ]
    dictCampaign = fdictCreateCampaign(
        "How should the change be implemented?", listParticipants,
        dictSettings={"iMaximumRounds": 1, "iMaximumConcurrentTurns": 2})

    def _fnTrackConnection(dictParticipant):
        connection = ClaudeRunnerConnection(
            dictGateway, dictCampaign["sCampaignId"], S_RUNNER_TEST_IMAGE,
            baSnapshot, dictParticipant["sRequestedModel"],
            saCliProgram=LIST_FAKE_CLI_PROGRAM,
            dictLimits=_fdictSmallLimits(), fWallClockSeconds=60.0)
        return _TrackingConnection(
            connection, dictGateway, listCreatedContainerIds)

    dictConnections = {
        dictParticipant["sParticipantId"]: _fnTrackConnection(dictParticipant)
        for dictParticipant in listParticipants}
    ledger = CouncilEvidenceLedger(65536, 262144)
    checkpoint = InMemoryCampaignCheckpoint()
    engine = CouncilEngine(
        dictCampaign, dictConnections, lambda dictEvent: None,
        ledger.fdictRecordEvidence, checkpoint.fnCheckpointCampaign,
        lambda dictRequest: {"sSnapshotHash": "h", "iExitCode": 0,
                             "sExecutionImageIdentity": "i",
                             "sOutputDigest": "d"})
    dictFinal = asyncio.run(engine.fdictRunUntilBlocked())
    assert dictFinal["sState"] == S_STATE_PLAN_READY, dictFinal["sState"]


class _TrackingConnection:
    """Wrap a real connection to register each runner id for teardown."""

    def __init__(self, connection, dictGateway, listCreatedContainerIds):
        self._connection = connection
        self._dictGateway = dictGateway
        self._listCreatedContainerIds = listCreatedContainerIds

    async def fdictPrepareImmutableContext(self, dictTurnRequest):
        dictContext = await self._connection.fdictPrepareImmutableContext(
            dictTurnRequest)
        self._listCreatedContainerIds.append(
            _fsContainerIdForHandle(
                self._dictGateway, self._connection._sHandle))
        return dictContext

    async def fnStartTurn(self, dictTurnRequest):
        await self._connection.fnStartTurn(dictTurnRequest)

    def fiterStreamNormalizedEvents(self):
        return self._connection.fiterStreamNormalizedEvents()

    async def fdictCollectStructuredResult(self):
        return await self._connection.fdictCollectStructuredResult()

    async def fsReportCompletion(self):
        return await self._connection.fsReportCompletion()


def test_baseline_evidence_executor_runs_server_command_in_a_sandbox(
        tCouncilLiveHarness):
    """The mandatory baseline executor runs a server command in a sandbox."""
    dictGateway, listCreatedContainerIds = tCouncilLiveHarness
    baSnapshot = _fbaBuildFakeProviderSnapshot()
    fdictExecuteBaselineEvidence = ffnBuildBaselineEvidenceExecutor(
        dictGateway, "campBaseline", S_RUNNER_TEST_IMAGE,
        "snapshot-hash-fixed", baSnapshot, dictLimits=_fdictSmallLimits(),
        fWallClockSeconds=60.0)
    # The command reads a snapshot file, proving the sandbox was seeded
    # from the immutable snapshot, not a runner's mutated copy.
    dictExecuted = fdictExecuteBaselineEvidence(
        {"sCommandText": "cat project.json"})
    assert dictExecuted["sSnapshotHash"] == "snapshot-hash-fixed"
    assert dictExecuted["iExitCode"] == 0
    assert dictExecuted["sExecutionImageIdentity"] == S_RUNNER_TEST_IMAGE
    assert dictExecuted["sOutputDigest"]


def test_shutdown_drain_destroys_a_live_registered_runner(
        tCouncilLiveHarness):
    """The drain destroys a live runner and settles its reservation."""
    dictGateway, listCreatedContainerIds = tCouncilLiveHarness
    dictRegistry = dictGateway["dictRegistry"]
    dictCreated = moduleGateway.fdictReserveAndCreateRunner(
        dictGateway, "campaignDrain", "claude",
        {"iMemoryBytes": 256 * I_MEBIBYTE, "fCpuCount": 1.0},
        S_RUNNER_TEST_IMAGE, dictLimits=_fdictSmallLimits())
    assert dictCreated["bCreated"] is True, dictCreated
    sReservationId = dictCreated["sReservationId"]
    sContainerId = _fsContainerIdForHandle(
        dictGateway, dictCreated["sHandle"])
    listCreatedContainerIds.append(sContainerId)

    dictReport = registry.fdictDrainCouncilRegistry(
        dictRegistry, dictGateway["dockerCouncil"])

    assert dictReport["listDestroyed"] == [sReservationId]
    assert dictRegistry["bAdmittingNewTurns"] is False
    dictProbe = moduleGateway.fdictProbeRunnerAbsence(
        dictGateway["dockerCouncil"], sContainerId)
    assert dictProbe["sAnswer"] == agentCouncilRunner.S_ABSENCE_ABSENT


def test_restart_reconciles_an_orphaned_labeled_runner(tCouncilLiveHarness):
    """A restarted hub discovers a labeled survivor and settles it."""
    dictGateway, listCreatedContainerIds = tCouncilLiveHarness
    dictCreated = moduleGateway.fdictReserveAndCreateRunner(
        dictGateway, "campaignOrphan", "claude",
        {"iMemoryBytes": 256 * I_MEBIBYTE, "fCpuCount": 1.0},
        S_RUNNER_TEST_IMAGE, dictLimits=_fdictSmallLimits())
    assert dictCreated["bCreated"] is True, dictCreated
    sReservationId = dictCreated["sReservationId"]
    sContainerId = _fsContainerIdForHandle(
        dictGateway, dictCreated["sHandle"])
    listCreatedContainerIds.append(sContainerId)

    # A restarted hub: fresh client, fresh registry, discover by label.
    dockerRestarted = moduleGateway.fdockerCreateCouncilClient()
    dictRegistryRestarted = registry.fdictCreateCouncilRegistry()
    dictReport = registry.fdictReconcileLabeledRunnersOnRestart(
        dictRegistryRestarted, dockerRestarted)

    assert sReservationId in dictReport["listDestroyed"]
    dictProbe = moduleGateway.fdictProbeRunnerAbsence(
        dockerRestarted, sContainerId)
    assert dictProbe["sAnswer"] == agentCouncilRunner.S_ABSENCE_ABSENT


def test_credential_lands_owned_by_the_unprivileged_user_in_the_runner(
        tCouncilLiveHarness):
    """A staged credential is delivered 1000-owned under CLAUDE_CONFIG_DIR.

    This proves the DELIVERY structure (ownership, path, env) with a
    FAKE credential, and that is ALL it proves — the distinction is
    load-bearing. A fake token is delivered identically whether or not
    the document would authenticate, so this test passed for weeks
    against a staged document the CLI rejects outright.

    The two empirics it cannot reach were run by hand on 2026-08-22
    against a real Max account, and both now have answers:

    - a copied ACCESS token DOES authenticate the CLI headless, but
      only when the document also carries ``scopes``; with the token
      alone the CLI answers "Not logged in · Please run /login" and
      never reaches the API. ``fsStageRunnerCredentialFile`` was fixed
      to include it.
    - non-interference HOLDS: five copies and three real inferences
      left the project login byte-identical (access token, refresh
      token and expiry all unchanged), so Claude passes the section 9.7
      runner gate rather than falling back to the API backend.

    Neither can live in CI — they cost money and need a real login —
    so they stay a maintainer ceremony, recorded in the evidence file.
    """
    dictGateway, listCreatedContainerIds = tCouncilLiveHarness
    sHostCredentialPath = fsStageRunnerCredentialFile(
        "FAKE-ACCESS-TOKEN-XYZ", ["user:inference"])
    try:
        dictCreated = moduleGateway.fdictReserveAndCreateRunner(
            dictGateway, "campCredential", "claude",
            {"iMemoryBytes": 256 * I_MEBIBYTE, "fCpuCount": 1.0},
            S_RUNNER_TEST_IMAGE, dictLimits=_fdictSmallLimits(),
            dictEnvironment={"CLAUDE_CONFIG_DIR":
                             S_RUNNER_CLAUDE_CONFIG_DIRECTORY})
        assert dictCreated["bCreated"] is True, dictCreated
        sHandle = dictCreated["sHandle"]
        listCreatedContainerIds.append(
            _fsContainerIdForHandle(dictGateway, sHandle))
        fnDeliverCredentialIntoRunner(
            dictGateway, sHandle,
            fbaBuildCredentialTarball(sHostCredentialPath))
        dictProbe = moduleGateway.fdictExecuteBoundedTurn(
            dictGateway, sHandle,
            ["/bin/sh", "-c",
             "python3 -c \"import os,json;"
             "p='%s/.credentials.json';"
             "st=os.stat(p);"
             "print('OWNER', st.st_uid, st.st_gid);"
             "print('TOKEN', json.load(open(p))"
             "['claudeAiOauth']['accessToken'])\""
             % S_RUNNER_CLAUDE_CONFIG_DIRECTORY],
            fWallClockSeconds=30.0)
    finally:
        from vaibify.config import secretManager
        secretManager.fnCleanupSecretFiles([sHostCredentialPath])

    assert "OWNER 1000 1000" in dictProbe["sOutput"], dictProbe["sOutput"]
    assert "TOKEN FAKE-ACCESS-TOKEN-XYZ" in dictProbe["sOutput"]
    moduleGateway.fdictDestroyAndSettle(dictGateway, sHandle)


def testTheCredentialCeilingHoldsAtTheExactBoundaryLive():
    """A real container, at the ceiling and one byte over it.

    The bound is enforced by the declared program INSIDE the container,
    so only a real read proves it: source-inspecting for ``.read(N)``
    shows the intent, not the behaviour. Exactly
    ``I_MAX_CREDENTIAL_FILE_BYTES`` must come back whole; one byte more
    must refuse — and refuse as ``RunnerCredentialError`` through the
    extraction, so the launch probe answers 409 rather than 500.
    """
    fnRequireDaemonReachable()
    import docker as moduleDocker
    from vaibify.docker import dockerConnection as moduleTransport
    from vaibify.gui import agentCouncilProviders as moduleProviders

    iCeiling = moduleTransport.I_MAX_CREDENTIAL_FILE_BYTES
    clientDocker = moduleDocker.from_env()
    sName = f"vaibifyCouncilCap{secrets.token_hex(4)}"
    container = clientDocker.containers.run(
        S_RUNNER_TEST_IMAGE, ["sleep", "120"], name=sName, detach=True)
    try:
        # A login document padded to EXACTLY the ceiling, and a second
        # one byte longer. The padding rides a JSON field so the
        # at-ceiling file is still a parseable login.
        sBuild = (
            "import json\n"
            f"iCeiling = {iCeiling}\n"
            "for sPath, iTarget in ((\"/at.json\", iCeiling),"
            " (\"/over.json\", iCeiling + 1)):\n"
            "    dictLogin = {'claudeAiOauth': {'accessToken': 'tok',"
            " 'sPad': ''}}\n"
            "    iPad = iTarget - len(json.dumps(dictLogin))\n"
            "    dictLogin['claudeAiOauth']['sPad'] = 'x' * iPad\n"
            "    baOut = json.dumps(dictLogin).encode()\n"
            "    assert len(baOut) == iTarget, (len(baOut), iTarget)\n"
            "    open(sPath, 'wb').write(baOut)\n")
        iExitCode, baOutput = container.exec_run(
            ["python3", "-c", sBuild])
        assert iExitCode == 0, baOutput.decode()
        # The transport execs as the unprivileged container user this
        # bare image lacks; create it and make the fixtures readable,
        # so the read under test runs exactly as production runs it.
        iExitCode, baOutput = container.exec_run(
            ["/bin/sh", "-c",
             "useradd -m researcher && chmod 0644 /at.json /over.json"])
        assert iExitCode == 0, baOutput.decode()

        connectionDocker = moduleTransport.DockerConnection()
        baAtCeiling = connectionDocker.fbaFetchCredentialFile(
            container.id, "/at.json")
        assert len(baAtCeiling) == iCeiling, (
            "a login exactly at the ceiling must come back whole")
        assert moduleProviders.fdictExtractRunnerCredential(
            connectionDocker, container.id, "/at.json",
        )["sAccessToken"] == "tok"

        with pytest.raises(ValueError):
            connectionDocker.fbaFetchCredentialFile(
                container.id, "/over.json")
        with pytest.raises(moduleProviders.RunnerCredentialError):
            moduleProviders.fdictExtractRunnerCredential(
                connectionDocker, container.id, "/over.json")
        assert moduleProviders.fbRunnerCredentialIsPresent(
            connectionDocker, container.id, "/over.json") is False
    finally:
        try:
            container.remove(force=True)
        except Exception:
            pass
