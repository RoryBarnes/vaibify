"""Tests for vaibify.cli.commandSessions."""

import os
import signal

from click.testing import CliRunner


def _flistFakeSlots():
    """Return two fake live session slots on distinct ports."""
    return [
        {"iPid": 4001, "sRole": "hub", "iPort": 8050,
         "sStartedIso": "2026-06-25T09:00:00", "bAlive": True},
        {"iPid": 4002, "sRole": "viewer", "iPort": 8060,
         "sStartedIso": "2026-06-25T10:00:00", "bAlive": True},
    ]


def _flistFakeHolders():
    """Return fake lock holders matching the slots by port."""
    return [
        {"sProjectName": "alpha", "iPid": 4001, "iPort": 8050,
         "sStartedIso": "2026-06-25T09:00:00"},
        {"sProjectName": "beta", "iPid": 4002, "iPort": 8060,
         "sStartedIso": "2026-06-25T10:00:00"},
    ]


def _fnPatchEnumerators(monkeypatch, listSlots, listHolders):
    """Point both enumerators at fixed fakes inside commandSessions."""
    import vaibify.cli.commandSessions as commandSessionsModule
    monkeypatch.setattr(
        commandSessionsModule, "flistReadAllSlots", lambda: listSlots,
    )
    monkeypatch.setattr(
        commandSessionsModule, "flistReadAllLockHolders",
        lambda: listHolders,
    )


def test_sessions_list_joins_containers_by_port(monkeypatch):
    from vaibify.cli.commandSessions import sessions
    _fnPatchEnumerators(monkeypatch, _flistFakeSlots(), _flistFakeHolders())
    resultRun = CliRunner().invoke(sessions, [])
    assert resultRun.exit_code == 0
    assert "pid=4001" in resultRun.output
    assert "containers=[alpha]" in resultRun.output
    assert "pid=4002" in resultRun.output
    assert "containers=[beta]" in resultRun.output


def test_sessions_list_reports_none_when_empty(monkeypatch):
    from vaibify.cli.commandSessions import sessions
    _fnPatchEnumerators(monkeypatch, [], [])
    resultRun = CliRunner().invoke(sessions, [])
    assert resultRun.exit_code == 0
    assert "No live Vaibify sessions." in resultRun.output


def test_sessions_stop_sends_sigterm_to_known_pid(monkeypatch):
    from vaibify.cli.commandSessions import sessions
    _fnPatchEnumerators(monkeypatch, _flistFakeSlots(), _flistFakeHolders())
    listKilled = []
    monkeypatch.setattr(
        os, "kill", lambda iPid, iSignal: listKilled.append((iPid, iSignal)),
    )
    resultRun = CliRunner().invoke(sessions, ["stop", "4001"])
    assert resultRun.exit_code == 0
    assert listKilled == [(4001, signal.SIGTERM)]


def test_sessions_stop_refuses_non_session_pid(monkeypatch):
    from vaibify.cli.commandSessions import sessions
    _fnPatchEnumerators(monkeypatch, _flistFakeSlots(), _flistFakeHolders())
    listKilled = []
    monkeypatch.setattr(
        os, "kill", lambda iPid, iSignal: listKilled.append((iPid, iSignal)),
    )
    resultRun = CliRunner().invoke(sessions, ["stop", "999999"])
    assert resultRun.exit_code != 0
    assert listKilled == []
    assert "not a Vaibify session" in resultRun.output


def test_sessions_stop_requires_pid_or_all(monkeypatch):
    from vaibify.cli.commandSessions import sessions
    _fnPatchEnumerators(monkeypatch, _flistFakeSlots(), _flistFakeHolders())
    resultRun = CliRunner().invoke(sessions, ["stop"])
    assert resultRun.exit_code != 0
    assert "provide a PID or use --all" in resultRun.output


def test_sessions_stop_all_excludes_self(monkeypatch):
    from vaibify.cli.commandSessions import sessions
    listSlots = _flistFakeSlots() + [
        {"iPid": os.getpid(), "sRole": "hub", "iPort": 8070,
         "sStartedIso": "2026-06-25T11:00:00", "bAlive": True},
    ]
    _fnPatchEnumerators(monkeypatch, listSlots, _flistFakeHolders())
    listKilled = []
    monkeypatch.setattr(
        os, "kill", lambda iPid, iSignal: listKilled.append((iPid, iSignal)),
    )
    resultRun = CliRunner().invoke(sessions, ["stop", "--all"])
    assert resultRun.exit_code == 0
    setKilledPids = {iPid for iPid, _ in listKilled}
    assert setKilledPids == {4001, 4002}
    assert os.getpid() not in setKilledPids


def test_sessions_stop_all_reports_when_no_others(monkeypatch):
    from vaibify.cli.commandSessions import sessions
    listSlots = [
        {"iPid": os.getpid(), "sRole": "hub", "iPort": 8050,
         "sStartedIso": "2026-06-25T09:00:00", "bAlive": True},
    ]
    _fnPatchEnumerators(monkeypatch, listSlots, [])
    listKilled = []
    monkeypatch.setattr(
        os, "kill", lambda iPid, iSignal: listKilled.append((iPid, iSignal)),
    )
    resultRun = CliRunner().invoke(sessions, ["stop", "--all"])
    assert resultRun.exit_code == 0
    assert listKilled == []
    assert "No other Vaibify sessions to stop." in resultRun.output
