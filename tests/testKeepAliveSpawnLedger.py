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
    monkeypatch.setattr(keepAliveManager, "fbIsProcessAliveSince",
                        lambda iPid, sIso, dictCache=None: True)
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
    "    1     0     0 /sbin/launchd\n"
    "  4242     1  1234 caffeinate -s\n"
    "  4243  4242  1234 /usr/bin/caffeinate -w 99\n"
    "  4244     1  5678 caffeinate -s\n"
    "  4245     1  1234 /usr/bin/python3 -m vaibify --port 8050 with spaces\n"
    "  4246     1  1234 caffeinated-thing\n"
    "garbage line\n"
)


def test_the_enumerator_parses_the_four_column_listing(monkeypatch):
    monkeypatch.setattr(processLiveness.os, "getuid", lambda: 1234)
    monkeypatch.setattr(
        processLiveness.subprocess, "run",
        lambda *tArgs, **dictKeywords: SimpleNamespace(returncode=0, stdout=S_PS_LISTING))
    datetimeFixed = datetime.datetime(2026, 1, 2, tzinfo=datetime.timezone.utc)
    monkeypatch.setattr(processLiveness, "fdatetimeReadProcessStartClock",
                        lambda iPid: datetimeFixed if iPid == 4242 else None)
    listRows = processLiveness.flistEnumerateProcessesNamed("caffeinate")
    assert [dictRow["iPid"] for dictRow in listRows] == [4242, 4243]
    assert listRows[0] == {"iPid": 4242, "iParentPid": 1, "sCommand": "caffeinate -s",
                           "datetimeStart": datetimeFixed}
    assert listRows[1]["datetimeStart"] is None
    assert processLiveness.flistEnumerateProcessesNamed("python3")[0]["sCommand"].endswith("with spaces")


def test_the_enumerator_answers_none_when_ps_cannot_run(monkeypatch):
    monkeypatch.setattr(processLiveness.subprocess, "run",
                        lambda *tArgs, **dictKeywords: (_ for _ in ()).throw(FileNotFoundError()))
    assert processLiveness.flistEnumerateProcessesNamed("caffeinate") is None


def test_the_sleep_hint_reads_the_one_enumerator(monkeypatch):
    from vaibify.gui import dockerStatus
    monkeypatch.setattr(processLiveness, "flistEnumerateProcessesNamed",
                        lambda sName: [{"iPid": 1}] if sName == "caffeinate" else [])
    assert dockerStatus._fbCaffeinateRunning() is True
    monkeypatch.setattr(processLiveness, "flistEnumerateProcessesNamed", lambda sName: None)
    assert dockerStatus._fbCaffeinateRunning() is False, "unknown is never 'running'"


def test_the_kill_rule_demands_the_ledger_a_clock_and_the_command_name(monkeypatch):
    iPid = os.getpid()
    keepAliveManager._fnRecordSpawnInLedger("ours", iPid, fsNowClaimIso())
    monkeypatch.setattr(keepAliveManager, "fsReadProcessCommandName",
                        lambda iQuery: "caffeinate")
    assert keepAliveManager.fbCaffeinateIsProvablyOurs(iPid) is True
    assert keepAliveManager.fbCaffeinateIsProvablyOurs(iPid + 1) is False, "not ledgered"
    monkeypatch.setattr(keepAliveManager, "fsReadProcessCommandName", lambda iQuery: "python3")
    assert keepAliveManager.fbCaffeinateIsProvablyOurs(iPid) is False, "wrong command"
    monkeypatch.setattr(keepAliveManager, "fsReadProcessCommandName", lambda iQuery: "caffeinate")
    monkeypatch.setattr(keepAliveManager, "fdatetimeReadProcessStartClock", lambda iQuery: None)
    assert keepAliveManager.fbCaffeinateIsProvablyOurs(iPid) is False, "unreadable clock"


def test_the_stop_refuses_what_it_cannot_prove_and_signals_what_it_can(monkeypatch):
    listKilled = []
    monkeypatch.setattr(keepAliveManager, "_fnKillIfRunning",
                        lambda iPid, sIso: listKilled.append(iPid))
    with pytest.raises(ValueError, match="left alone"):
        keepAliveManager.fnStopProvablyOursKeepAlive(os.getpid())
    keepAliveManager._fnRecordSpawnInLedger("ours", os.getpid(), fsNowClaimIso())
    monkeypatch.setattr(keepAliveManager, "fsReadProcessCommandName", lambda iQuery: "caffeinate")
    keepAliveManager.fnStopProvablyOursKeepAlive(os.getpid())
    assert listKilled == [os.getpid()]
