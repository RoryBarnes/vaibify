"""Live falsification of the council gateway's quarantine semantics (R4).

Each test drives the REAL daemon through the gateway, because the
claims — a failure after create leaves no unrecorded reservation and no
container, a daemon that cannot prove removal quarantines with the
admission budget still held, an unproven sandbox destruction refuses to
confirm evidence, and the egress proxy really wears the runner's
hardened posture — are exactly the kind a unit stub confirms
vacuously. Live-daemon convention matches
``testAgentCouncilRunnerLive``: skip with no daemon,
``VAIBIFY_REQUIRE_DOCKER_DAEMON`` turns the skip into a failure.

Container rules: throwaway only, uniquely named, force-removed in
teardown even on failure — including the deliberately quarantined ones,
which are cleaned up directly once their assertions are made so the
harness leaves nothing behind.
"""

import asyncio
import io
import os
import secrets
import tarfile

import pytest

from tests.testDockerConnectionLive import fnRequireDaemonReachable
from vaibify.gui import agentCouncilDockerGateway as moduleGateway
from vaibify.gui import agentCouncilEgress
from vaibify.gui import agentCouncilProviders
from vaibify.gui import agentCouncilRegistry as registry
from vaibify.gui import agentCouncilRunner

pytestmark = pytest.mark.docker_live

S_RUNNER_TEST_IMAGE = os.environ.get(
    "VAIBIFY_COUNCIL_TEST_IMAGE", "python:3.10-slim")
I_MEBIBYTE = 1024 * 1024


def _fdictSmallLimits():
    dictLimits = agentCouncilRunner.fdictBuildDefaultRunnerLimits()
    dictLimits.update({
        "iMemoryBytes": 256 * I_MEBIBYTE,
        "iWorkingTreeBytes": 64 * I_MEBIBYTE,
        "iScratchBytes": 16 * I_MEBIBYTE,
        "iPidsLimit": 64,
    })
    return dictLimits


DICT_SMALL_COST = {"iMemoryBytes": 256 * I_MEBIBYTE, "fCpuCount": 1.0}


def _fbaBuildTinySnapshot():
    bufferTar = io.BytesIO()
    with tarfile.open(fileobj=bufferTar, mode="w") as fileTar:
        baContent = b'{"name": "gateway-live"}'
        infoMember = tarfile.TarInfo(name="project.json")
        infoMember.size = len(baContent)
        fileTar.addfile(infoMember, io.BytesIO(baContent))
    return bufferTar.getvalue()


@pytest.fixture
def tGatewayLiveHarness():
    """Yield (dictGateway, listCreatedContainerIds) over a real daemon."""
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


def test_exception_after_create_settles_the_reservation_and_destroys(
        tGatewayLiveHarness, monkeypatch):
    """A copy-in failure leaks neither a reservation nor a container.

    The connection's prepare path is driven with the gateway copy-in
    monkeypatched to raise AFTER the runner exists — the exact window
    the pre-R4 adapter leaked in. The exception must propagate, the
    reservation must be settled (destroyed frees it on a healthy
    daemon), and the container must be POSITIVELY absent.
    """
    dictGateway, listCreatedContainerIds = tGatewayLiveHarness
    listRunnerContainerIds = []

    def fnCopyRaisesAfterRecordingTheTarget(dictGatewayInner, sHandle,
                                            baSnapshotTar, **dictKeywords):
        listRunnerContainerIds.append(
            dictGatewayInner["dictHandlesById"][sHandle]["sContainerId"])
        listCreatedContainerIds.append(listRunnerContainerIds[-1])
        raise RuntimeError("injected copy-in failure")

    monkeypatch.setattr(
        moduleGateway, "fnCopySnapshotIntoRunner",
        fnCopyRaisesAfterRecordingTheTarget)
    connection = agentCouncilProviders.ClaudeRunnerConnection(
        dictGateway, "campInjected", S_RUNNER_TEST_IMAGE,
        _fbaBuildTinySnapshot(), "sonnet",
        dictLimits=_fdictSmallLimits())
    dictRequest = {"sTurnId": "turn-1", "sCampaignId": "campInjected"}
    with pytest.raises(RuntimeError, match="injected copy-in failure"):
        asyncio.run(connection.fdictPrepareImmutableContext(dictRequest))

    assert len(listRunnerContainerIds) == 1
    # Never leaked unrecorded: no pending or live reservation survives;
    # a healthy daemon proves absence, so the settle freed it outright.
    assert dictGateway["dictRegistry"]["dictReservationsById"] == {}
    dictProbe = moduleGateway.fdictProbeRunnerAbsence(
        dictGateway["dockerCouncil"], listRunnerContainerIds[0])
    assert dictProbe["sAnswer"] == agentCouncilRunner.S_ABSENCE_ABSENT


def test_forced_indeterminate_teardown_quarantines_and_holds_budget(
        tGatewayLiveHarness, monkeypatch):
    """An unproven removal quarantines: visible, budget held, listed.

    ``remove_container`` is forced to raise, so the daemon never proves
    the container gone. The outcome must be quarantined, the
    reservation must stay visible with status quarantined, a follow-up
    reserve must still see the held budget, and the campaign's
    "runner may exist" listing must name it. The REAL container is then
    removed directly so the harness leaves nothing behind.
    """
    dictGateway, listCreatedContainerIds = tGatewayLiveHarness
    dictRegistry = dictGateway["dictRegistry"]
    dictRegistry["dictGlobalCeilings"][
        "iPerCampaignMaxConcurrentRunners"] = 1
    dictCreated = moduleGateway.fdictReserveAndCreateRunner(
        dictGateway, "campQuarantine", "claude", DICT_SMALL_COST,
        S_RUNNER_TEST_IMAGE, dictLimits=_fdictSmallLimits())
    assert dictCreated["bCreated"] is True, dictCreated
    sHandle = dictCreated["sHandle"]
    sReservationId = dictCreated["sReservationId"]
    sContainerId = dictGateway["dictHandlesById"][sHandle]["sContainerId"]
    listCreatedContainerIds.append(sContainerId)

    def fnRemoveRaises(*listArguments, **dictKeywords):
        raise RuntimeError("injected removal fault")

    with monkeypatch.context() as monkeypatchContext:
        monkeypatchContext.setattr(
            dictGateway["dockerCouncil"].api, "remove_container",
            fnRemoveRaises)
        dictOutcome = moduleGateway.fdictDestroyAndSettle(
            dictGateway, sHandle)

    assert dictOutcome["sOutcome"] == (
        agentCouncilRunner.S_OUTCOME_QUARANTINED), dictOutcome
    dictReservation = dictRegistry["dictReservationsById"][sReservationId]
    assert dictReservation["sStatus"] == registry.S_RESERVATION_QUARANTINED
    # Admission is still consumed: the per-campaign quota of one is
    # held by the quarantined reservation, so a follow-up is refused.
    dictRefused = registry.fdictReserveRunner(
        dictRegistry, "campQuarantine", "res-follow-up", "claude",
        DICT_SMALL_COST)
    assert dictRefused["bReserved"] is False
    assert "per-campaign" in dictRefused["sRefusalReason"]
    assert moduleGateway.flistDescribeQuarantinedReservations(
        dictGateway, "campQuarantine") == [{
            "sReservationId": sReservationId,
            "sCampaignId": "campQuarantine",
            "sProvider": "claude",
        }]
    # The registry still vetoes idle exit while the quarantine stands.
    assert registry.fbCouncilRegistryReportsLiveWork(dictRegistry) is True

    # Clean up the REAL container with the un-patched client, and prove
    # the harness leaves nothing behind.
    dictCleanup = moduleGateway.fdictDestroyRunnerAndProveAbsence(
        dictGateway["dockerCouncil"], sContainerId)
    assert dictCleanup["sOutcome"] == agentCouncilRunner.S_OUTCOME_DESTROYED


def test_baseline_executor_raises_when_destruction_is_unproven(
        tGatewayLiveHarness, monkeypatch):
    """An unproven sandbox destruction refuses to yield evidence.

    The command itself SUCCEEDS; only the teardown is forced
    indeterminate. The executor must raise rather than return an
    execution record, so the engine's ``_fnRecordBaselineClaim``
    reverts the claim and nothing becomes confirmed (the engine-side
    half is ``testARaisingBaselineExecutorRevertsTheConfirmedClaim``).
    """
    dictGateway, listCreatedContainerIds = tGatewayLiveHarness
    fdictExecuteBaselineEvidence = (
        agentCouncilProviders.ffnBuildBaselineEvidenceExecutor(
            dictGateway, "campBaselineQuarantine", S_RUNNER_TEST_IMAGE,
            "snapshot-hash-fixed", _fbaBuildTinySnapshot(),
            dictLimits=_fdictSmallLimits(), fWallClockSeconds=60.0))

    def fnRemoveRaises(*listArguments, **dictKeywords):
        raise RuntimeError("injected removal fault")

    with monkeypatch.context() as monkeypatchContext:
        monkeypatchContext.setattr(
            dictGateway["dockerCouncil"].api, "remove_container",
            fnRemoveRaises)
        with pytest.raises(moduleGateway.CouncilGatewayError,
                           match="unproven"):
            fdictExecuteBaselineEvidence({"sCommandText": "true"})

    # The quarantined reservation is recorded and holds budget.
    listQuarantined = moduleGateway.flistDescribeQuarantinedReservations(
        dictGateway, "campBaselineQuarantine")
    assert len(listQuarantined) == 1
    sReservationId = listQuarantined[0]["sReservationId"]
    dictReservation = dictGateway["dictRegistry"][
        "dictReservationsById"][sReservationId]
    sContainerId = dictReservation["sContainerId"]
    listCreatedContainerIds.append(sContainerId)

    # Clean up the REAL sandbox directly and prove absence.
    dictCleanup = moduleGateway.fdictDestroyRunnerAndProveAbsence(
        dictGateway["dockerCouncil"], sContainerId)
    assert dictCleanup["sOutcome"] == agentCouncilRunner.S_OUTCOME_DESTROYED


def test_hardened_proxy_wears_the_runner_posture_and_reports_ready(
        tGatewayLiveHarness):
    """The egress proxy is digest-pinned, non-root, cap-dropped, bounded.

    ``fsLaunchAllowlistProxy`` returning at all proves the ready line
    appeared under the unprivileged user (the launch blocks on it); the
    inspect then pins every element of the posture, and teardown proves
    absence of both resources.
    """
    dictGateway, _ = tGatewayLiveHarness
    sCampaignId = "livegateway-" + secrets.token_hex(4)
    moduleGateway.fsCreateCampaignInternalNetwork(dictGateway, sCampaignId)
    try:
        sProxyAddress = moduleGateway.fsLaunchAllowlistProxy(
            dictGateway, sCampaignId, ["provider-stand-in"],
            iaAllowedPorts=[443],
            dictHostnameAddressMap={"provider-stand-in": "203.0.113.7"})
        assert sProxyAddress
        sProxyName = agentCouncilEgress.fsComposeProxyContainerName(
            sCampaignId)
        dictInspect = dictGateway["dockerCouncil"].api.inspect_container(
            sProxyName)
        assert dictInspect["Config"]["User"] == "1000:1000"
        assert dictInspect["Config"]["Image"] == (
            agentCouncilEgress.S_PROXY_IMAGE)
        dictHostConfig = dictInspect["HostConfig"]
        assert dictHostConfig["CapDrop"] == ["ALL"]
        assert "no-new-privileges:true" in dictHostConfig["SecurityOpt"]
        assert dictHostConfig["Memory"] == (
            moduleGateway.I_PROXY_MEMORY_BYTES)
        assert dictHostConfig["MemorySwap"] == (
            moduleGateway.I_PROXY_MEMORY_BYTES)
        assert dictHostConfig["NanoCpus"] == 500_000_000
        assert dictHostConfig["PidsLimit"] == (
            moduleGateway.I_PROXY_PIDS_LIMIT)
        baLogs = dictGateway["dockerCouncil"].api.logs(
            sProxyName, stdout=True, stderr=True)
        assert agentCouncilEgress.S_PROXY_READY_LINE in baLogs.decode(
            "utf-8", errors="replace")
    finally:
        dictRemoval = moduleGateway.fdictRemoveCampaignEgressResources(
            dictGateway, sCampaignId)
    assert dictRemoval["bProxyAbsenceProven"] is True
    assert dictRemoval["bNetworkAbsenceProven"] is True
    assert dictRemoval["saIndeterminateResources"] == []
