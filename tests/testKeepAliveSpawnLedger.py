"""The keep-alive spawn ledger, and the one host probe for caffeinate.

A pid file names the keep-alive a registry currently holds; nothing
could say whether a ``caffeinate`` no registry held was one vaibify
launched or the researcher's own. The ledger records every spawn by
pid with its start instant, pruned of dead pids on every append and
read, and the kill rule for a later removal demands a readable clock
that matches it AND a command name of caffeinate. The ``pgrep`` the
sleep hint used is retired onto the one ``ps`` enumerator, so the hint
and the scanner cannot disagree.
"""

import datetime
import json
import os
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

from vaibify.config import keepAliveManager, processLiveness
from vaibify.config.processLiveness import fsNowClaimIso


@pytest.fixture(autouse=True)
def fnNeverSignal(monkeypatch):
    monkeypatch.setattr(keepAliveManager, "_fnKillIfRunning", lambda iPid, sIso: None)
    monkeypatch.setattr(keepAliveManager, "fbPlatformSupportsKeepAlive", lambda: True)


def _fiProvablyDeadPid():
    processChild = subprocess.Popen([sys.executable, "-c", "pass"])
    processChild.wait()
    return processChild.pid


def _fdictLedgerOnDisk():
    with open(keepAliveManager._fsSpawnLedgerPath()) as fileHandle:
        return json.load(fileHandle)


def test_a_spawn_is_ledgered_with_its_name_and_start_instant(monkeypatch):
    monkeypatch.setattr(keepAliveManager, "_fiSpawnCaffeinate", lambda: os.getpid())
    keepAliveManager.fnStartKeepAlive("projectOne")
    dictLedger = keepAliveManager.fdictReadSpawnLedger()
    assert dictLedger[os.getpid()]["sName"] == "projectOne"
    assert dictLedger[os.getpid()]["sStartedIso"]
    assert "spawnLedger.json" not in keepAliveManager.flistKeepAliveNames()
    assert "projectOne" in keepAliveManager.flistKeepAliveNames()


def test_dead_entries_are_pruned_on_read_and_dropped_on_the_next_append(monkeypatch):
    iDead = _fiProvablyDeadPid()
    keepAliveManager._fnRecordSpawnInLedger("gone", iDead, fsNowClaimIso())
    monkeypatch.setattr(keepAliveManager, "_fiSpawnCaffeinate", lambda: os.getpid())
    assert iDead not in keepAliveManager.fdictReadSpawnLedger()
    assert str(iDead) in _fdictLedgerOnDisk(), "a read never writes the ledger"
    keepAliveManager.fnStartKeepAlive("live")
    assert str(iDead) not in _fdictLedgerOnDisk()
    assert str(os.getpid()) in _fdictLedgerOnDisk()


def test_the_cap_keeps_the_newest_entries():
    dictLedger = {str(iPid): {"sName": "n", "sStartedIso": f"2026-01-01T00:00:{iPid % 60:02d}+00:00"}
                  for iPid in range(1, keepAliveManager._I_SPAWN_LEDGER_CAP + 50)}
    dictCapped = keepAliveManager._fdictCapLedger(dictLedger)
    assert len(dictCapped) == keepAliveManager._I_SPAWN_LEDGER_CAP


def test_concurrent_appends_lose_nothing(monkeypatch):
    monkeypatch.setattr(keepAliveManager, "fdictEnumerateStartClocks", lambda: None)
    monkeypatch.setattr(keepAliveManager, "fbIsProcessAlive", lambda iPid: True)
    listThreads = [
        threading.Thread(target=keepAliveManager._fnRecordSpawnInLedger,
                         args=(f"name{iIndex}", 100000 + iIndex, fsNowClaimIso()))
        for iIndex in range(20)]
    for threadAppend in listThreads:
        threadAppend.start()
    for threadAppend in listThreads:
        threadAppend.join()
    assert sorted(keepAliveManager.fdictReadSpawnLedger()) == list(range(100000, 100020))


S_PS_LISTING = (
    "    1     0     0       10:00 /sbin/launchd\n"
    "  4242     1  1234    01:00 caffeinate -s\n"
    "  4243  4242  1234       30 /usr/bin/caffeinate -w 99\n"
    "  4244     1  5678    02:00 caffeinate -s\n"
    "  4245     1  1234    05:00 /usr/bin/python3 -m vaibify --port 8050 with spaces\n"
    "  4246     1  1234    00:10 caffeinated-thing\n"
    "garbage line\n"
)


def test_the_enumerator_parses_the_five_column_listing(monkeypatch):
    monkeypatch.setattr(processLiveness.os, "getuid", lambda: 1234)
    monkeypatch.setattr(
        processLiveness.subprocess, "run",
        lambda *tArgs, **dictKeywords: SimpleNamespace(returncode=0, stdout=S_PS_LISTING))
    listRows = processLiveness.flistEnumerateProcessesNamed("caffeinate")
    assert [dictRow["iPid"] for dictRow in listRows] == [4242, 4243]
    assert listRows[0]["sCommand"] == "caffeinate -s"
    assert listRows[0]["iParentPid"] == 1
    # The start clock is now derived from the listing's own etime, in one
    # spawn: a one-minute elapsed time reads back as ~one minute ago.
    fAgeSeconds = (datetime.datetime.now(datetime.timezone.utc)
                   - listRows[0]["datetimeStart"]).total_seconds()
    assert 55 <= fAgeSeconds <= 65, fAgeSeconds
    assert processLiveness.flistEnumerateProcessesNamed(
        "python3")[0]["sCommand"].endswith("with spaces")


def test_the_enumerator_answers_none_when_ps_cannot_run(monkeypatch):
    monkeypatch.setattr(processLiveness.subprocess, "run",
                        lambda *tArgs, **dictKeywords: (_ for _ in ()).throw(FileNotFoundError()))
    assert processLiveness.flistEnumerateProcessesNamed("caffeinate") is None


def test_the_start_clocks_come_from_one_spawn(monkeypatch):
    listCalls = []

    def fnFakeRun(tArgv, **dictKeywords):
        listCalls.append(tArgv)
        return SimpleNamespace(returncode=0, stdout="  4242    01:00\n  4243    bad\n")
    monkeypatch.setattr(processLiveness.subprocess, "run", fnFakeRun)
    dictClocks = processLiveness.fdictEnumerateStartClocks()
    assert len(listCalls) == 1, "one ps spawn answers every pid"
    assert dictClocks[4242] is not None and dictClocks[4243] is None


def test_the_sleep_hint_reads_the_one_enumerator(monkeypatch):
    from vaibify.gui import dockerStatus
    monkeypatch.setattr(processLiveness, "flistEnumerateProcessesNamed",
                        lambda sName: [{"iPid": 1}] if sName == "caffeinate" else [])
    assert dockerStatus._fbCaffeinateRunning() is True
    monkeypatch.setattr(processLiveness, "flistEnumerateProcessesNamed", lambda sName: None)
    assert dockerStatus._fbCaffeinateRunning() is False, "unknown is never 'running'"


def _fnArrangeLiveCaffeinate(monkeypatch, iPid, sStartedIso):
    """Make the host list iPid as a caffeinate started at the given instant."""
    import datetime as _dt
    datetimeStart = _dt.datetime.fromisoformat(sStartedIso)
    monkeypatch.setattr(
        keepAliveManager, "fdictEnumerateKeepAlivesByPid",
        lambda: {iPid: {"iPid": iPid, "iParentPid": 1,
                        "sCommand": "caffeinate -s", "datetimeStart": datetimeStart}})


def test_the_kill_rule_demands_the_ledger_and_a_consistent_live_clock(monkeypatch):
    iPid = os.getpid()
    sStartedIso = fsNowClaimIso()
    keepAliveManager._fnRecordSpawnInLedger("ours", iPid, sStartedIso)
    _fnArrangeLiveCaffeinate(monkeypatch, iPid, sStartedIso)
    assert keepAliveManager.fbCaffeinateIsProvablyOurs(iPid) is True
    assert keepAliveManager.fbCaffeinateIsProvablyOurs(iPid + 1) is False, "not ledgered"
    # Host no longer lists it as a caffeinate: not provably ours.
    monkeypatch.setattr(keepAliveManager, "fdictEnumerateKeepAlivesByPid", lambda: {})
    assert keepAliveManager.fbCaffeinateIsProvablyOurs(iPid) is False, "not listed"
    # Listed but with an unreadable clock: not enough to kill.
    monkeypatch.setattr(
        keepAliveManager, "fdictEnumerateKeepAlivesByPid",
        lambda: {iPid: {"iPid": iPid, "datetimeStart": None}})
    assert keepAliveManager.fbCaffeinateIsProvablyOurs(iPid) is False, "unreadable clock"


def test_the_stop_refuses_what_it_cannot_prove_and_signals_what_it_can(monkeypatch):
    listKilled = []
    monkeypatch.setattr(keepAliveManager, "_fnKillIfRunning",
                        lambda iPid, sIso: listKilled.append(iPid))
    monkeypatch.setattr(keepAliveManager, "_fbAwaitProcessExit", lambda iPid: True)
    with pytest.raises(ValueError, match="left alone"):
        keepAliveManager.fbStopProvablyOursKeepAlive(os.getpid())
    sStartedIso = fsNowClaimIso()
    keepAliveManager._fnRecordSpawnInLedger("ours", os.getpid(), sStartedIso)
    _fnArrangeLiveCaffeinate(monkeypatch, os.getpid(), sStartedIso)
    assert keepAliveManager.fbStopProvablyOursKeepAlive(os.getpid()) is True
    assert listKilled == [os.getpid()]


def test_the_stop_reports_a_signalled_process_that_is_still_running(monkeypatch):
    sStartedIso = fsNowClaimIso()
    keepAliveManager._fnRecordSpawnInLedger("ours", os.getpid(), sStartedIso)
    _fnArrangeLiveCaffeinate(monkeypatch, os.getpid(), sStartedIso)
    monkeypatch.setattr(keepAliveManager, "_fnKillIfRunning", lambda iPid, sIso: None)
    monkeypatch.setattr(keepAliveManager, "_fbAwaitProcessExit", lambda iPid: False)
    assert keepAliveManager.fbStopProvablyOursKeepAlive(os.getpid()) is False


def test_the_confirmed_stop_refuses_a_recycled_pid(monkeypatch):
    monkeypatch.setattr(keepAliveManager, "fbIsProcessAliveSince",
                        lambda iPid, sIso: False)
    with pytest.raises(ValueError, match="already gone"):
        keepAliveManager.fbStopKeepAliveProcess(os.getpid(), fsNowClaimIso())
