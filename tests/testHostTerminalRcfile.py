"""A host terminal gets the same rcfile a container terminal does.

Source: ``vaibify/host/hostConnection.py`` (``_S_TERMINAL_LAUNCH_STUB``)
and ``vaibify/config/terminalRcfile.py``.

Every test drives a REAL shell through the REAL ``HostConnection``
launch -- ``/bin/bash`` is bash 3.2 on macOS -- and reads what the
shell itself prints, never a stub keyed like the code under test.
"""

import os
import time

import pytest

from vaibify.config import containerLock, operationJournal
from vaibify.gui import terminalContainment
from vaibify.gui.terminalSession import HostTerminalSession
from vaibify.host import hostScratch
from vaibify.host.hostConnection import HostConnection

S_PROJECT_NAME = "host-rcfile-proj"


@pytest.fixture(autouse=True)
def fixtureIsolateJournalAndScratch(tmp_path, monkeypatch):
    monkeypatch.setattr(
        operationJournal, "_S_JOURNAL_DIRECTORY", str(tmp_path / "journal"))
    monkeypatch.setattr(
        containerLock, "_S_LOCK_DIRECTORY", str(tmp_path / "locks"))
    monkeypatch.setattr(
        hostScratch, "_S_HOST_DIAGNOSTICS_ROOT",
        str(tmp_path / "host-diagnostics"))


class _AppStateStub:
    """The one attribute the terminal registry reaches for."""


def _fsessionStart(tmp_path, dictOverlay=None):
    sProjectRoot = str(tmp_path / "project")
    os.makedirs(sProjectRoot, exist_ok=True)
    connection = HostConnection(
        fnResolveProjectRoot=lambda sResourceId: sProjectRoot)
    session = HostTerminalSession(
        connection, S_PROJECT_NAME,
        {"appState": _AppStateStub(), "iOwnerGeneration": 1},
        dictEnvironmentOverlay=dictOverlay)
    session.fnStart()
    return session


def _fsAwaitOutput(session, sNeedle, fTimeoutSeconds=30.0):
    baCollected = b""
    fDeadline = time.monotonic() + fTimeoutSeconds
    while time.monotonic() < fDeadline:
        baChunk = session.fbaReadOutput()
        if baChunk:
            baCollected += baChunk
            if sNeedle.encode() in baCollected:
                return baCollected.decode("utf-8", "replace")
        else:
            time.sleep(0.05)
    return None


def _fnClose(session):
    terminalContainment.fdictDrainSessionRecord(session)
    session.fnClose()


@pytest.mark.falsification
def testAHostShellHasTheWrappersAndTheResetHook(tmp_path):
    """The host shell starts with the rcfile, under this machine's bash.

    Kills: launching the host shell with a plain ``bash -i`` again.
    """
    session = _fsessionStart(tmp_path)
    try:
        session.fnSendInput(
            b"echo KINDS-$((6*7))-$(type -t claude codex gemini | tr '\\n' '-')"
            b"HOOK-[${PROMPT_COMMAND}]-$((6*7))\n")
        sOutput = _fsAwaitOutput(session, "]-42")
        assert sOutput is not None
        assert "KINDS-42-function-function-function-HOOK-" in sOutput
        assert "fnVaibifyResetInputModes]-42" in sOutput
    finally:
        _fnClose(session)


def testTheHostShellFallsBackWhenTheRcfileCannotBeWritten(
    tmp_path, monkeypatch,
):
    sUnwritable = tmp_path / "unwritable"
    sUnwritable.mkdir()
    sUnwritable.chmod(0o500)
    monkeypatch.setenv("TMPDIR", str(sUnwritable))
    session = _fsessionStart(tmp_path)
    try:
        session.fnSendInput(b"echo SHELL-$((6*7))-[$(type -t claude)]\n")
        sOutput = _fsAwaitOutput(session, "SHELL-42-[")
        assert sOutput is not None, "a failed rcfile must still give a shell"
        assert "SHELL-42-[function]" not in sOutput
    finally:
        _fnClose(session)
        sUnwritable.chmod(0o700)


def testAHostShellIsToldWhereToRecordItsChats(tmp_path):
    dictOverlay = {"VAIBIFY_TERMINAL_LIFECYCLE": str(
        tmp_path / "terminal.jsonl")}
    session = _fsessionStart(tmp_path, dictOverlay)
    try:
        session.fnSendInput(
            b"echo RECORD-$((6*7))-[$VAIBIFY_TERMINAL_LIFECYCLE]\n")
        assert _fsAwaitOutput(
            session, "RECORD-42-[" + dictOverlay["VAIBIFY_TERMINAL_LIFECYCLE"])
    finally:
        _fnClose(session)


def testTheLaunchStubIsIsolatedFromTheProjectDirectory():
    """The stub runs in the project directory, so it must not import from it.

    A shape test, not a falsification: on an interpreter that preloads
    ``signal`` no planted module can run, so the kill cannot be shown
    there; with the system Python a project ``signal.py`` did run.
    """
    import inspect
    from vaibify.host import hostConnection
    listImports = [sLine for sLine in
                   hostConnection._S_TERMINAL_LAUNCH_STUB.splitlines()
                   if sLine.lstrip().startswith(("import ", "from "))]
    assert listImports == ["import os,signal,sys"]
    sSource = inspect.getsource(
        hostConnection.HostConnection.fdictLaunchTerminalShellSuspended)
    assert '[sys.executable, "-I", "-c", _S_TERMINAL_LAUNCH_STUB,' in sSource
