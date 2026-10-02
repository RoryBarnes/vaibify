"""Finding nothing to reconcile is a success on both lanes.

The crash-time lane printed "nothing to reconcile" and exited 0; the
live-hub lane printed the same sentence and exited 1, so a script that
ran ``vaibify reconcile`` could not tell a project with nothing to prove
from a refusal.
"""

import pytest

from vaibify.cli import commandReconcile
from vaibify.config import containerLock, operationJournal

S_PROJECT = "demo"


@pytest.fixture(autouse=True)
def fixtureIsolateJournalAndLockDirs(tmp_path, monkeypatch):
    monkeypatch.setattr(
        operationJournal, "_S_JOURNAL_DIRECTORY", str(tmp_path / "journal"))
    monkeypatch.setattr(
        containerLock, "_S_LOCK_DIRECTORY", str(tmp_path / "locks"))
    monkeypatch.setattr(
        commandReconcile, "_fconnectionCreateDockerQuietly", lambda: None)


@pytest.mark.falsification
def testNothingToReconcileExitsZeroOnTheLiveHubLane(monkeypatch, capsys):
    """Kills: reporting the hub lane's empty journal as a failure."""
    monkeypatch.setattr(
        commandReconcile, "fdictReadLockHolder",
        lambda sName: {"iPid": 4242, "iPort": 8123})

    def fdictRefuseToSend(iPort, dictRequest):
        raise AssertionError("nothing to reconcile must not reach the hub")

    monkeypatch.setattr(
        "vaibify.gui.hostControlChannel.fdictSendHostControlRequest",
        fdictRefuseToSend)
    iExitCode = commandReconcile.fiRunReconcileCommand(S_PROJECT, True)
    assert "nothing to reconcile" in capsys.readouterr().out
    assert iExitCode == 0


def testNothingToReconcileExitsZeroOnTheCrashTimeLane(capsys):
    iExitCode = commandReconcile.fiRunReconcileCommand(S_PROJECT, True)
    assert "nothing to reconcile" in capsys.readouterr().out
    assert iExitCode == 0
