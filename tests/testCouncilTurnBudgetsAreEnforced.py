"""Every per-turn budget in the record must reach the thing that enforces it.

``iMaximumOutputBytesPerTurn`` sat in DICT_DEFAULT_SETTINGS and in every
stored campaign, and governed nothing: the controller never passed
``iOutputByteCap`` to the connection, so all turns ran under the runner
module's own 1 MiB default. The record said 256 KiB. The enforcement
said 1 MiB. Neither number was reachable by the researcher, and both
participants of the first real implementation council were killed at a
ceiling no setting could move (2026-08-30).

The wall-clock budget beside it carries a comment naming exactly this
failure — "without this the setting is a number in a record that
governs nothing" — so the defect was one argument away from its own
warning. This file asserts the property for BOTH budgets, because a
test for one and not the other is how the second one got missed.
"""

import pytest

from vaibify.gui import agentCouncilCampaign
from vaibify.gui import agentCouncilController
from vaibify.gui import agentCouncilRunner


class _RecordingConnectionFactory:
    """Captures the kwargs the controller builds a connection with."""

    def __init__(self):
        self.dictSeen = {}

    def __call__(self, *args, **kwargs):
        self.dictSeen = dict(kwargs)
        return object()


def _fdictRuntimeWithSettings(dictSettings):
    return {
        "dictCampaign": {"dictSettings": dictSettings},
        "ftStageRunnerCredential": lambda *a, **k: ("", 0),
        "dictRunnerAccess": {"dictEgress": {}},
    }


def _fdictBuildAndCapture(monkeypatch, dictSettings):
    from vaibify.gui import agentCouncilProviders
    factoryRecording = _RecordingConnectionFactory()
    # Patched on the PROVIDERS module, which is what the controller
    # imports inside the function — patching an attribute the
    # controller does not carry would silently exercise nothing.
    monkeypatch.setattr(
        agentCouncilProviders, "ClaudeRunnerConnection", factoryRecording)
    monkeypatch.setattr(
        agentCouncilController, "_fdictProvisionRunnerAccessOnce",
        lambda dictRuntime: {"dictEgress": {}})
    monkeypatch.setattr(
        agentCouncilController, "_fdictEnsureRuntimeGateway",
        lambda dictRuntime: object())
    dictRuntime = _fdictRuntimeWithSettings(dictSettings)
    dictRuntime.update({"sCampaignId": "campaign-x",
                        "sImageReference": "sha256:abc",
                        "baSnapshotTar": b""})
    agentCouncilController.fconnectionBuildParticipantConnection(
        dictRuntime,
        {"sParticipantId": "p-1", "sProvider": "claude",
         "sRequestedModel": "opus"},
    )
    assert factoryRecording.dictSeen, (
        "the connection factory was never called, so this test proves "
        "nothing about what the controller passes")
    return factoryRecording.dictSeen


def test_the_output_cap_in_the_record_is_the_one_that_is_enforced(
        monkeypatch):
    """Kills: omitting iOutputByteCap, which is how this shipped."""
    dictSeen = _fdictBuildAndCapture(
        monkeypatch, {"iMaximumOutputBytesPerTurn": 8 * 1024 * 1024})
    assert dictSeen.get("iOutputByteCap") == 8 * 1024 * 1024, (
        "the campaign's output cap never reached the connection, so "
        "every turn runs under the module default no matter what the "
        "record or the researcher says")


def test_the_wall_clock_in_the_record_is_the_one_that_is_enforced(
        monkeypatch):
    """The budget that WAS threaded, asserted so it stays that way."""
    dictSeen = _fdictBuildAndCapture(
        monkeypatch, {"iTurnWallClockSeconds": 1234})
    assert dictSeen.get("fWallClockSeconds") == pytest.approx(1234.0)


def test_a_legacy_records_dead_value_can_only_raise_the_budget(
        monkeypatch):
    """Wiring a dead setting must not make existing councils worse.

    Campaigns convened before the setting was connected carry 262144.
    Enforcing that literally would cap them BELOW the 1 MiB they have
    been running under, so the floor is the old effective ceiling.
    """
    dictSeen = _fdictBuildAndCapture(
        monkeypatch, {"iMaximumOutputBytesPerTurn": 262144})
    assert dictSeen.get("iOutputByteCap") == (
        agentCouncilRunner.I_DEFAULT_TURN_OUTPUT_CAP_BYTES), (
        "a stored campaign's budget was lowered by connecting the "
        "setting, which is a regression dressed as a fix")


@pytest.mark.parametrize("jsonStored,iExpected", [
    (None, agentCouncilCampaign.DICT_DEFAULT_SETTINGS[
        "iMaximumOutputBytesPerTurn"]),
    ("not a number", agentCouncilCampaign.DICT_DEFAULT_SETTINGS[
        "iMaximumOutputBytesPerTurn"]),
    (1, agentCouncilCampaign.I_MINIMUM_TURN_OUTPUT_CAP_BYTES),
    (1 << 40, agentCouncilCampaign.I_MAXIMUM_TURN_OUTPUT_CAP_BYTES),
])
def test_the_clamp_never_raises_on_the_launch_path(jsonStored, iExpected):
    """A malformed number in an old record must not stop a council."""
    assert agentCouncilCampaign.fiClampTurnOutputCapBytes(
        jsonStored) == iExpected


def test_the_default_carries_a_real_patch():
    """Ten megabytes, by ruling, because an implementation turn emits a
    diff for every file it touches on top of its narration."""
    assert agentCouncilCampaign.DICT_DEFAULT_SETTINGS[
        "iMaximumOutputBytesPerTurn"] == 10 * 1024 * 1024


def test_a_retry_may_raise_this_campaigns_budget(monkeypatch):
    """Retry must be able to change the condition that caused the kill.

    Kills: accepting the field and ignoring it, which leaves retry
    spending paid work to reproduce a known failure.
    """
    from vaibify.gui.routes import councilRoutes
    dictCampaign = {"dictSettings": {
        "iMaximumOutputBytesPerTurn": 2 * 1024 * 1024}}
    listCheckpointed = []
    monkeypatch.setattr(
        councilRoutes.agentCouncilStore, "fnCheckpointStoredCampaign",
        lambda dictStore, sId, dictRecord: listCheckpointed.append(sId))

    councilRoutes._fnRaiseOutputBudgetForRetry(
        {}, "campaign-x", dictCampaign, 8 * 1024 * 1024)
    assert dictCampaign["dictSettings"][
        "iMaximumOutputBytesPerTurn"] == 8 * 1024 * 1024
    assert listCheckpointed == ["campaign-x"], (
        "the raised budget was not persisted, so the relaunch reads the "
        "old number back off the record")


def test_a_retry_never_lowers_the_budget(monkeypatch):
    """Kills: letting retry shrink a budget mid-campaign.

    A later turn would then fail for a reason the researcher never
    chose, in a council they were trying to rescue.
    """
    from vaibify.gui.routes import councilRoutes
    dictCampaign = {"dictSettings": {
        "iMaximumOutputBytesPerTurn": 16 * 1024 * 1024}}
    listCheckpointed = []
    monkeypatch.setattr(
        councilRoutes.agentCouncilStore, "fnCheckpointStoredCampaign",
        lambda dictStore, sId, dictRecord: listCheckpointed.append(sId))

    councilRoutes._fnRaiseOutputBudgetForRetry(
        {}, "campaign-x", dictCampaign, 2 * 1024 * 1024)
    assert dictCampaign["dictSettings"][
        "iMaximumOutputBytesPerTurn"] == 16 * 1024 * 1024
    assert listCheckpointed == []


def test_a_retry_that_names_no_budget_leaves_the_record_alone(monkeypatch):
    """Zero means "unchanged", not "reset to the default"."""
    from vaibify.gui.routes import councilRoutes
    dictCampaign = {"dictSettings": {
        "iMaximumOutputBytesPerTurn": 3 * 1024 * 1024}}
    listCheckpointed = []
    monkeypatch.setattr(
        councilRoutes.agentCouncilStore, "fnCheckpointStoredCampaign",
        lambda dictStore, sId, dictRecord: listCheckpointed.append(sId))

    councilRoutes._fnRaiseOutputBudgetForRetry({}, "c", dictCampaign, 0)
    assert dictCampaign["dictSettings"][
        "iMaximumOutputBytesPerTurn"] == 3 * 1024 * 1024
    assert listCheckpointed == []


def test_the_stall_window_in_the_record_is_the_one_that_is_enforced(
        monkeypatch):
    """The third budget, threaded like the other two.

    Kills: adding a stall setting and leaving it unwired, which is the
    exact failure iMaximumOutputBytesPerTurn shipped with.
    """
    dictSeen = _fdictBuildAndCapture(
        monkeypatch, {"iTurnStallSeconds": 900})
    assert dictSeen.get("fStallSeconds") == pytest.approx(900.0)


def _fdictDriveOneTurnCapturing(monkeypatch, iExpiresAtEpochMs,
                                fWallClockSeconds):
    """Run prepare+start against fakes; return the gateway's kwargs.

    Drives the REAL connection, because the clamp lives between the
    staging step and the bounded-turn call and a unit test of the clamp
    function alone cannot show that anything calls it.
    """
    import asyncio
    from vaibify.config import secretManager
    from vaibify.gui import agentCouncilDockerGateway
    from vaibify.gui import agentCouncilProviders

    dictCaptured = {}

    monkeypatch.setattr(
        agentCouncilProviders, "fbaBuildCredentialTarball",
        lambda sPath: b"tar")
    monkeypatch.setattr(
        secretManager, "fnCleanupSecretFiles", lambda listPaths: None)
    monkeypatch.setattr(
        agentCouncilDockerGateway, "fdictReserveAndCreateRunner",
        lambda *tArguments, **dictKeywords: {
            "bCreated": True, "sHandle": "handle-1",
            "sReservationId": "reservation-1"})
    monkeypatch.setattr(
        agentCouncilDockerGateway, "fnCopySnapshotIntoRunner",
        lambda *tArguments, **dictKeywords: None)
    monkeypatch.setattr(
        agentCouncilProviders, "fnDeliverCredentialIntoRunner",
        lambda dictGateway, sHandle, baTar: None)

    def _fdictRecordBoundedTurn(dictGateway, sHandle, listCommand,
                                iOutputByteCap=None, fWallClock=None,
                                sWorkingDirectory=None, baStdinPayload=None,
                                fStallSeconds=None):
        dictCaptured["fWallClockSeconds"] = fWallClock
        return {"sOutput": "", "iExitCode": 0}

    monkeypatch.setattr(
        agentCouncilDockerGateway, "fdictExecuteBoundedTurn",
        _fdictRecordBoundedTurn)

    connection = agentCouncilProviders.ClaudeRunnerConnection(
        {"bFakeGateway": True}, "campaign-clamp", "sha256:" + "00" * 32,
        b"tar", "opus", dictEgress=None,
        fWallClockSeconds=fWallClockSeconds,
        ftStageRunnerCredential=lambda: ("/tmp/staged.json",
                                         iExpiresAtEpochMs))

    async def _fnDrive():
        await connection.fdictPrepareImmutableContext({})
        await connection.fnStartTurn(
            {"sInstructionChannel": "charter", "listQuotedMaterial": []})

    asyncio.run(_fnDrive())
    assert "fWallClockSeconds" in dictCaptured, (
        "the bounded-turn primitive was never called, so this test "
        "proves nothing about the budget that reaches it")
    return dictCaptured


def test_a_short_login_shortens_the_turn_that_actually_runs(monkeypatch):
    """Kills: computing the clamp and passing the unclamped budget on.

    The clamp is only worth anything if the number the bounded-turn
    primitive receives is the clamped one. A connection that clamps
    into a local and then hands ``self.fWallClockSeconds`` to the
    gateway passes every unit test of the clamp function and changes
    nothing about any turn.
    """
    import time
    dictCaptured = _fdictDriveOneTurnCapturing(
        monkeypatch, int((time.time() + 300) * 1000), 14400.0)
    assert 260 <= dictCaptured["fWallClockSeconds"] <= 305, (
        "the turn ran under the campaign's budget, not the login's "
        "remaining life: "
        f"{dictCaptured['fWallClockSeconds']}")


def test_a_healthy_login_leaves_the_researchers_budget_alone(monkeypatch):
    """The falsification pair.

    Kills: clamping every turn to something short regardless of the
    login, which would satisfy the test above and quietly cap every
    council on the machine.
    """
    import time
    dictCaptured = _fdictDriveOneTurnCapturing(
        monkeypatch, int((time.time() + 28800) * 1000), 14400.0)
    assert dictCaptured["fWallClockSeconds"] == pytest.approx(14400.0)


def test_a_login_with_no_expiry_leaves_the_budget_untouched(monkeypatch):
    """A document that does not say must not be read as "expired now"."""
    dictCaptured = _fdictDriveOneTurnCapturing(monkeypatch, 0, 14400.0)
    assert dictCaptured["fWallClockSeconds"] == pytest.approx(14400.0)


def test_a_silent_stream_is_stopped_at_the_stall_window():
    """Silence for the window ends the turn and flags it as a stall.

    Drives the REAL pump against a socket that accepts the connection
    and then says nothing — which is what a dead provider connection or
    a wedged CLI looks like from this side.
    """
    import socket as socketModule
    import time
    from vaibify.gui import agentCouncilRunner

    socketLeft, socketRight = socketModule.socketpair()
    socketLeft.settimeout(0.05)
    try:
        fStarted = time.monotonic()
        dictPumped = agentCouncilRunner.fdictPumpBoundedExecStream(
            socketLeft, 1024 * 1024,
            time.monotonic() + 30.0, fStallSeconds=0.3)
        fElapsed = time.monotonic() - fStarted
    finally:
        socketLeft.close()
        socketRight.close()

    assert dictPumped["bStalled"] is True
    assert dictPumped["bDeadlineExceeded"] is False, (
        "the total budget fired instead of the stall window, so the "
        "stall detector is not what stopped this turn")
    assert fElapsed < 5.0, (
        f"the stall took {fElapsed:.1f}s to fire against a 0.3s window")


def test_a_stream_that_keeps_talking_is_not_called_stalled():
    """Kills: a window that fires on a working turn.

    The falsification pair for the test above — a detector that always
    reports a stall would pass that one and be worthless.
    """
    import socket as socketModule
    import threading
    import time
    from vaibify.gui import agentCouncilRunner

    socketLeft, socketRight = socketModule.socketpair()
    socketLeft.settimeout(0.05)
    bStop = threading.Event()

    def _fnKeepTalking():
        while not bStop.is_set():
            try:
                socketRight.send(b"\x01\x00\x00\x00\x00\x00\x00\x04noop")
            except OSError:
                return
            time.sleep(0.05)

    threadTalker = threading.Thread(target=_fnKeepTalking, daemon=True)
    threadTalker.start()
    try:
        dictPumped = agentCouncilRunner.fdictPumpBoundedExecStream(
            socketLeft, 1024 * 1024,
            time.monotonic() + 1.0, fStallSeconds=0.5)
    finally:
        bStop.set()
        socketLeft.close()
        socketRight.close()

    assert dictPumped["bStalled"] is False, (
        "a stream producing output every 50ms was called stalled "
        "against a 500ms window")
    assert dictPumped["bDeadlineExceeded"] is True
