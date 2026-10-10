"""The remnant scanner classifies with evidence and never overstates it.

Every classifier is driven with the container NAME distinct from its
id, a journal and registry on disk in the redirected state, and
daemon doubles shaped like the connection the hub passes. What is
pinned: a session vaibify opened is never listed; a session nobody
recorded is listed as "possibly", never proven; the discovery window
and an attribution mismatch report unknown and list nothing; a
keep-alive is proven only through the ledger; a container held by
another hub is never actionable; and a stopped container is listed only
by label or by a test-lane name, never by a Docker-generated one.
"""

import asyncio
import os
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from vaibify.config import containerLock, keepAliveManager, operationJournal
from vaibify.config.processLiveness import fsNowClaimIso
from vaibify.gui import (
    cliShellContainment, remnantReapers, remnantScanner, terminalContainment,
)

S_NAME = "scannedProject"
S_ID = "5ca11ed" * 9 + "5ca1"
I_BOOT = 1_700_000_000


def _fsTable(listRows, iProbePid=900):
    """Render rows as the typed read prints them: pid ppid pgid sid tty state start rss uid comm."""
    listLines = [f"@@ clock {I_BOOT} 100 4096 {iProbePid} {iProbePid - 1}"]
    for tRow in listRows:
        listLines.append(" ".join(str(objField) for objField in tRow))
    return "\n".join(listLines) + "\n"


# pid ppid pgid sid tty state start rss uid comm
T_INIT = (1, 0, 1, 1, 0, "S", 10, 300, 0, "sleep")
T_SHELL_A = (40, 0, 40, 40, 34816, "S", 500, 1000, 1000, "bash")
T_CHILD_A = (41, 40, 41, 40, 34816, "S", 600, 50000, 1000, "claude")
T_SHELL_B = (70, 0, 70, 70, 34817, "S", 700, 1000, 1000, "sh")
T_ZOMBIE = (99, 40, 99, 40, 34816, "Z", 650, 0, 1000, "sh")


def _fnStubDrainAndLock(monkeypatch):
    monkeypatch.setattr(terminalContainment, "fbContainerDrainInProgress", lambda sName: False)
    monkeypatch.setattr(containerLock, "fbContainerLockIsHeld", lambda sName: False)


def _fnJournalTerminal(iGroup, sExecId="exec-dash"):
    sOperationId = operationJournal.fsPrepareOperation(
        S_NAME, terminalContainment.S_TERMINAL_OPERATION_KIND, S_ID)
    operationJournal.fnPromoteOperationToInFlight(S_NAME, sOperationId, {
        "sDockerExecId": sExecId, "sDockerContainerId": S_ID,
        **({"iHolderProcessGroup": iGroup} if iGroup else {})})
    return sOperationId


def _flistClassify(sTable, listTtyExecIds, dictHostConfig=None, appState=None):
    return remnantScanner.flistClassifyContainerProcesses(
        appState or SimpleNamespace(dictContainerOwners={}), S_NAME, S_ID,
        remnantScanner.fdictParseProcessTable(sTable),
        {"Init": True} if dictHostConfig is None else dictHostConfig, listTtyExecIds)


def test_the_process_table_parses_rows_and_its_clock():
    dictTable = remnantScanner.fdictParseProcessTable(_fsTable([T_INIT, T_SHELL_A]))
    assert dictTable["dictClock"] == {"iBootEpoch": I_BOOT, "iTicksPerSecond": 100,
                                      "iPageBytes": 4096, "iProbePid": 900,
                                      "iProbeParentPid": 899}
    assert [dictRow["iPid"] for dictRow in dictTable["listRows"]] == [1, 40]
    assert dictTable["listRows"][1]["sCommand"] == "bash"
    with pytest.raises(ValueError):
        remnantScanner.fdictParseProcessTable("no header\n")


def test_a_session_nobody_recorded_is_listed_as_possibly_with_its_evidence(monkeypatch):
    _fnStubDrainAndLock(monkeypatch)
    [dictItem] = _flistClassify(_fsTable([T_INIT, T_SHELL_A, T_CHILD_A, T_ZOMBIE]), ["exec-x"])
    assert dictItem["sCategory"] == remnantScanner.S_CATEGORY_UNTRACKED_SESSION
    assert dictItem["sTier"] == remnantScanner.S_TIER_POSSIBLY
    assert dictItem["sAction"] == remnantScanner.S_ACTION_TERMINATE
    assert dictItem["dictIdentity"] == {"sContainerId": S_ID, "iLeaderPid": 40, "iStartTicks": 500}
    assert "2 process(es)" in dictItem["sEvidence"], "the zombie is not a process"
    assert "user 1000" in dictItem["sEvidence"]
    assert datetime.fromtimestamp(I_BOOT + 5, tz=timezone.utc).isoformat(
        timespec="seconds") in dictItem["sEvidence"]
    assert dictItem["bConfirmRequired"] is True, "it runs an agent CLI"
    assert "claude" in dictItem["sEvidence"]


def test_a_session_the_dashboard_opened_is_never_listed(monkeypatch):
    _fnStubDrainAndLock(monkeypatch)
    _fnJournalTerminal(40, "exec-dash")
    assert _flistClassify(_fsTable([T_INIT, T_SHELL_A, T_CHILD_A]), ["exec-dash"]) == []


def test_a_session_a_cli_shell_opened_is_never_listed(monkeypatch):
    _fnStubDrainAndLock(monkeypatch)
    monkeypatch.setattr(cliShellContainment, "flistCliShellRecords", lambda: [
        {"sContainerId": S_ID, "iSessionId": 70, "sContainerName": S_NAME,
         "iCliPid": os.getpid(), "sCliStartedIso": fsNowClaimIso(), "sSessionStartClock": "1"}])
    listItems = _flistClassify(_fsTable([T_INIT, T_SHELL_A, T_SHELL_B]), ["exec-x", "exec-y"])
    assert [dictItem["dictIdentity"]["iLeaderPid"] for dictItem in listItems] == [40]


def test_the_discovery_window_lists_nothing(monkeypatch):
    """A record without a group is a terminal mid-start; its session would read as untracked."""
    _fnStubDrainAndLock(monkeypatch)
    _fnJournalTerminal(0)
    assert _flistClassify(_fsTable([T_INIT, T_SHELL_A]), ["exec-dash"]) == []


def test_a_drain_in_progress_lists_nothing(monkeypatch):
    _fnStubDrainAndLock(monkeypatch)
    monkeypatch.setattr(terminalContainment, "fbContainerDrainInProgress", lambda sName: True)
    assert _flistClassify(_fsTable([T_INIT, T_SHELL_A]), ["exec-x"]) == []


def test_an_attribution_mismatch_reports_unknown_and_lists_no_session(monkeypatch):
    _fnStubDrainAndLock(monkeypatch)
    [dictItem] = _flistClassify(_fsTable([T_INIT, T_SHELL_A]), ["exec-x", "exec-y"])
    assert dictItem["sTier"] == remnantScanner.S_TIER_UNKNOWN
    assert dictItem["sAction"] == remnantScanner.S_ACTION_NONE
    assert "2 interactive exec(s)" in dictItem["sEvidence"] and "1 session leader" in dictItem["sEvidence"]


def test_a_container_held_by_another_hub_is_shown_without_an_action(monkeypatch):
    _fnStubDrainAndLock(monkeypatch)
    monkeypatch.setattr(containerLock, "fbContainerLockIsHeld", lambda sName: True)
    [dictItem] = _flistClassify(_fsTable([T_INIT, T_SHELL_A]), ["exec-x"])
    assert dictItem["sAction"] == remnantScanner.S_ACTION_NONE
    assert "another vaibify window" in dictItem["sEvidence"]
    appState = SimpleNamespace(dictContainerOwners={S_NAME: object()})
    [dictOwn] = _flistClassify(_fsTable([T_INIT, T_SHELL_A]), ["exec-x"], appState=appState)
    assert dictOwn["sAction"] == remnantScanner.S_ACTION_TERMINATE, "our own hold is not a peer's"


def test_a_container_without_init_is_a_proven_fact_with_its_zombie_count(monkeypatch):
    _fnStubDrainAndLock(monkeypatch)
    listItems = _flistClassify(_fsTable([T_INIT, T_ZOMBIE]), [], dictHostConfig={"Init": False})
    [dictItem] = [d for d in listItems if d["sCategory"] == remnantScanner.S_CATEGORY_CONTAINER_WITHOUT_INIT]
    assert dictItem["sTier"] == remnantScanner.S_TIER_PROVEN
    assert dictItem["sAction"] == remnantScanner.S_ACTION_NONE
    assert "1 zombie" in dictItem["sEvidence"]
    assert "Restart Container" in dictItem["sRemedy"]
    assert _flistClassify(_fsTable([T_INIT]), [], dictHostConfig={"Init": True}) == []


def test_a_quarantined_record_is_proven_and_points_at_reconcile(monkeypatch):
    _fnStubDrainAndLock(monkeypatch)
    sOperationId = _fnJournalTerminal(40)
    operationJournal.fnMarkOperationNeedsReconciliation(S_NAME, sOperationId, sNote="x")
    [dictItem] = _flistClassify(_fsTable([T_INIT]), [])
    assert dictItem["sCategory"] == remnantScanner.S_CATEGORY_QUARANTINED_RECORD
    assert dictItem["sTier"] == remnantScanner.S_TIER_PROVEN
    assert f"vaibify reconcile {S_NAME}" in dictItem["sRemedy"]


def test_an_unread_container_reports_unknown(monkeypatch):
    _fnStubDrainAndLock(monkeypatch)
    [dictItem] = remnantScanner.flistClassifyContainerProcesses(
        SimpleNamespace(dictContainerOwners={}), S_NAME, S_ID, None, {"Init": True}, [])
    assert dictItem["sTier"] == remnantScanner.S_TIER_UNKNOWN
    assert "could not be read" in dictItem["sEvidence"]


@pytest.fixture
def fnHostKeepAlives(monkeypatch):
    monkeypatch.setattr(keepAliveManager, "fbPlatformSupportsKeepAlive", lambda: True)
    monkeypatch.setattr(keepAliveManager, "_fnKillIfRunning", lambda iPid, sIso: None)

    def fnArrange(listProcesses, dictLedger, bOurs=True):
        monkeypatch.setattr(remnantScanner, "flistEnumerateProcessesNamed",
                            lambda sName: listProcesses)
        monkeypatch.setattr(keepAliveManager, "fdictReadSpawnLedger", lambda: dictLedger)
        monkeypatch.setattr(keepAliveManager, "fbCaffeinateIsProvablyOurs",
                            lambda iPid, dictLedger=None, dictRunningByPid=None: bOurs)
    return fnArrange


def _fdictProcess(iPid, sCommand="caffeinate -s", iParentPid=1):
    return {"iPid": iPid, "iParentPid": iParentPid, "sCommand": sCommand,
            "datetimeStart": datetime(2026, 1, 1, tzinfo=timezone.utc)}


def test_a_ledgered_keep_alive_no_registry_holds_is_proven(fnHostKeepAlives):
    fnHostKeepAlives([_fdictProcess(4242)], {4242: {"sName": "old", "sStartedIso": "2026-01-01T00:00:00+00:00"}})
    [dictItem] = remnantScanner.flistClassifyHostKeepAlives(SimpleNamespace(dictContainerOwners={}), {})
    assert dictItem["sTier"] == remnantScanner.S_TIER_PROVEN
    assert dictItem["sAction"] == remnantScanner.S_ACTION_KILL
    assert dictItem["dictIdentity"] == {"iPid": 4242, "sStartedIso": "2026-01-01T00:00:00+00:00"}


def test_an_unledgered_caffeinate_s_is_possibly_and_needs_confirmation(fnHostKeepAlives):
    fnHostKeepAlives([_fdictProcess(4243), _fdictProcess(4244, sCommand="caffeinate -w 12"),
                      _fdictProcess(4245, iParentPid=77)], {}, bOurs=False)
    [dictItem] = remnantScanner.flistClassifyHostKeepAlives(SimpleNamespace(dictContainerOwners={}), {})
    assert dictItem["dictIdentity"]["iPid"] == 4243
    assert dictItem["sTier"] == remnantScanner.S_TIER_POSSIBLY
    assert dictItem["bConfirmRequired"] is True


def test_a_registered_keep_alive_is_not_listed(fnHostKeepAlives, monkeypatch):
    monkeypatch.setattr(keepAliveManager, "_fiSpawnCaffeinate", lambda: 4242)
    keepAliveManager.fnStartKeepAlive("heldProject")
    fnHostKeepAlives([_fdictProcess(4242)], {})
    appState = SimpleNamespace(dictContainerOwners={"heldProject": object()})
    assert remnantScanner.flistClassifyHostKeepAlives(appState, {"heldProject": "id"}) == []


def test_a_session_lane_no_hub_owns_is_listed_after_one_interval(fnHostKeepAlives, monkeypatch):
    monkeypatch.setattr(keepAliveManager, "_fiSpawnCaffeinate", lambda: 4242)
    keepAliveManager.fnStartKeepAlive("orphanLane")
    fnHostKeepAlives([], {})
    assert remnantScanner.flistClassifyHostKeepAlives(
        SimpleNamespace(dictContainerOwners={}), {"orphanLane": "id"}) == [], "too young"
    monkeypatch.setattr(remnantReapers, "F_REAPER_PASS_INTERVAL_SECONDS", -1.0)
    [dictItem] = remnantScanner.flistClassifyHostKeepAlives(
        SimpleNamespace(dictContainerOwners={}), {"orphanLane": "id"})
    assert dictItem["sCategory"] == remnantScanner.S_CATEGORY_UNOWNED_SESSION_LANE
    assert dictItem["sAction"] == remnantScanner.S_ACTION_STOP
    assert remnantScanner.flistClassifyHostKeepAlives(
        SimpleNamespace(dictContainerOwners={}), {}) == [], "a stopped container's lane is the reaper's"


def test_an_unlistable_host_reports_unknown(fnHostKeepAlives, monkeypatch):
    fnHostKeepAlives(None, {})
    [dictItem] = remnantScanner.flistClassifyHostKeepAlives(SimpleNamespace(dictContainerOwners={}), {})
    assert dictItem["sTier"] == remnantScanner.S_TIER_UNKNOWN


def test_stopped_containers_are_listed_by_label_or_test_name_never_by_generated_name():
    listAll = [
        {"sContainerId": "id-labeled", "sName": "fresh-image", "sStatus": "exited",
         "dictLabels": {remnantScanner.S_LIVE_LANE_LABEL: "1"}},
        {"sContainerId": "id-prefixed", "sName": "vaibifyTermContain1234", "sStatus": "exited",
         "dictLabels": {}},
        {"sContainerId": "id-generated", "sName": "eloquent_hopper", "sStatus": "exited",
         "dictLabels": {}},
        {"sContainerId": "id-registered", "sName": "myProject", "sStatus": "exited",
         "dictLabels": {}},
        {"sContainerId": "id-running", "sName": "vaibifyDisposable9", "sStatus": "running",
         "dictLabels": {}},
    ]
    listItems = remnantScanner.flistClassifyStoppedContainers(listAll, {"myProject"})
    assert [(d["sContainerName"], d["sTier"]) for d in listItems] == [
        ("fresh-image", remnantScanner.S_TIER_PROVEN),
        ("vaibifyTermContain1234", remnantScanner.S_TIER_POSSIBLY)]
    assert all(d["sAction"] == remnantScanner.S_ACTION_REMOVE for d in listItems)


def test_the_live_lane_label_is_the_one_the_suite_stamps():
    from tests.liveContainerLabels import S_LIVE_LANE_LABEL
    assert remnantScanner.S_LIVE_LANE_LABEL == S_LIVE_LANE_LABEL


def test_every_agent_overlay_resolves_to_a_command_with_a_dockerfile():
    from vaibify.docker.imageBuilder import DICT_AGENT_COMMANDS, T_AGENT_OVERLAY_NAMES
    from vaibify.resources import fpathContainerImageRoot
    for sAgent in T_AGENT_OVERLAY_NAMES:
        assert DICT_AGENT_COMMANDS.get(sAgent), f"{sAgent} resolves to no command"
        assert (fpathContainerImageRoot() / f"Dockerfile.{sAgent}").is_file()


def test_the_poll_summary_reads_state_alone():
    appState = SimpleNamespace(dictRemnantScan={
        "sScannedIso": "2026-01-01T00:00:00+00:00", "bScanning": False, "sScanError": "",
        "listItems": [{"sTier": "proven"}, {"sTier": "possibly"}]},
        dictReaperHealth={"a": {"sOutcome": "ran"}})
    dictSummary = remnantScanner.fdictSummarizeForPoll(appState)
    assert dictSummary == {"iCount": 2, "iProvenCount": 1, "sScannedIso": "2026-01-01T00:00:00+00:00",
                           "sGlyphTitle": "2 leftover processes or files; click to review.",
                           "bReaperFailed": False, "bScanError": False, "bScanning": False}
    appState.dictReaperHealth["b"] = {"sOutcome": "failed"}
    assert remnantScanner.fdictSummarizeForPoll(appState)["sGlyphTitle"] == (
        "A cleanup could not run; click to review.")


def test_a_failed_scan_with_no_items_still_shows_the_glyph():
    appState = SimpleNamespace(dictRemnantScan={
        "sScannedIso": "2026-01-01T00:00:00+00:00", "bScanning": False,
        "sScanError": "The scan failed: boom.", "listItems": []},
        dictReaperHealth={})
    dictSummary = remnantScanner.fdictSummarizeForPoll(appState)
    assert dictSummary["iCount"] == 0
    assert dictSummary["bScanError"] is True, "a failed scan must not vanish at zero"
    assert "scan for leftover processes and files failed" in dictSummary["sGlyphTitle"]


def test_a_failing_scan_records_a_sentence_and_never_raises():
    app = SimpleNamespace(state=SimpleNamespace())

    class _Exploding:
        def flistGetRunningContainers(self):
            raise RuntimeError("daemon exploded")

        def flistListAllContainers(self):
            raise RuntimeError("daemon exploded")

    asyncio.run(remnantScanner.fnRunRemnantScan(app, {"docker": _Exploding()}))
    dictScan = app.state.dictRemnantScan
    assert "daemon exploded" in dictScan["sScanError"] and "rescan" in dictScan["sScanError"]
    assert dictScan["bScanning"] is False and dictScan["sScannedIso"]


def test_the_pass_ends_with_the_scan():
    from fastapi import FastAPI
    from vaibify.gui import serverLifespan
    app = FastAPI(lifespan=serverLifespan._fcontextLifespanShared)
    app.state.listLifespanStartup = []
    app.state.listLifespanShutdown = []
    listCalls = []

    async def fnScan(app, dictCtx):
        listCalls.append("scanned")

    remnantReapers.fnRegisterPostPassScan(app, fnScan)
    asyncio.run(remnantReapers.fnRunReaperPass(app, {}))
    assert listCalls == ["scanned"]
