"""A stopped container's out-of-memory evidence is read before it is removed.

Every start removes a stopped container of the same name before creating
a new one, and removal destroys the only record of a kill that took the
container's main process down with it: the daemon's ``State.OOMKilled``.
Both removal paths -- the hub's start reservation and the CLI's
``vaibify start`` -- now read that evidence first, and neither lets a
failed read stop the removal.

The fakes model a removed container honestly: after ``docker rm`` an
inspect answers nothing, so reading the evidence AFTER the removal finds
none, which is exactly the defect the ordering prevents.

Names and ids are kept distinct: the old container's id, the new
container's id and the project name are three different strings.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from vaibify.docker import containerManager
from vaibify.gui import containerMemoryWatch, startReservation
from vaibify.gui.startReservation import StartTaskRecord


S_NAME = "evidence-lane-project"
S_OLD_ID = "e41f78246fc9" + "0" * 52
S_FINISHED = "2026-10-08T22:16:13.000000001Z"


class _FakeDaemon:
    """A stopped container that the daemon forgets once it is removed."""

    def __init__(self, bOomKilled=True, bInspectFails=False):
        self.bRemoved = False
        self.bOomKilled = bOomKilled
        self.bInspectFails = bInspectFails
        self.listCalls = []

    def fdictStatus(self, sName):
        self.listCalls.append(("status", sName))
        return {"bExists": True, "bRunning": False, "sStatus": "exited"}

    def fjsonInspect(self, sName):
        self.listCalls.append(("inspect", sName))
        if self.bInspectFails:
            raise OSError("daemon did not answer")
        if self.bRemoved:
            return {}
        return {"Id": S_OLD_ID, "State": {
            "OOMKilled": self.bOomKilled, "ExitCode": 137,
            "FinishedAt": S_FINISHED}}

    def fnRemove(self, sName):
        self.listCalls.append(("remove", sName))
        self.bRemoved = True


@pytest.fixture
def daemonFake(monkeypatch):
    daemon = _FakeDaemon()
    monkeypatch.setattr(
        containerManager, "fdictGetContainerStatus", daemon.fdictStatus)
    monkeypatch.setattr(
        containerManager, "fjsonInspectContainer", daemon.fjsonInspect)
    monkeypatch.setattr(containerManager, "fnRemoveStopped", daemon.fnRemove)
    return daemon


def _frecordTask():
    return StartTaskRecord(
        sStartTaskId="taskEvidence", sJournalOperationId="operationEvidence")


@pytest.mark.falsification
def testTheHubReadsTheEvidenceBeforeItRemovesTheContainer(daemonFake):
    """Kills: removing the stopped container before inspecting it."""
    recordTask = _frecordTask()
    startReservation._fnClearStoppedIncarnation(S_NAME, recordTask)
    assert daemonFake.listCalls == [
        ("status", S_NAME), ("inspect", S_NAME), ("remove", S_NAME)]
    dictEvidence = recordTask.dictExitedOomEvidence
    assert dictEvidence["bOomKilled"] is True
    assert dictEvidence["sContainerId"] == S_OLD_ID
    assert dictEvidence["sFinishedIso"].startswith("2026-10-08T22:16:13")


def testAFailedInspectStillRemovesTheContainer(daemonFake):
    daemonFake.bInspectFails = True
    recordTask = _frecordTask()
    startReservation._fnClearStoppedIncarnation(S_NAME, recordTask)
    assert ("remove", S_NAME) in daemonFake.listCalls
    assert recordTask.dictExitedOomEvidence["bAnswered"] is False
    assert recordTask.dictExitedOomEvidence["bOomKilled"] is False


def testTheSettledStartRecordsTheIncidentUnderTheOldId():
    """The incident names the container that was killed, not its successor."""
    appState = SimpleNamespace(
        dictContainerMemory=containerMemoryWatch.fdictCreateMemoryStore())
    containerMemoryWatch.fnRecordExitedEvidenceForApp(appState, S_NAME, {
        "bAnswered": True, "bOomKilled": True, "sContainerId": S_OLD_ID,
        "iExitCode": 137, "sFinishedIso": "2026-10-08T22:16:13+00:00",
    })
    listIncidents = appState.dictContainerMemory[S_NAME]["listIncidents"]
    assert [d["sKind"] for d in listIncidents] == [
        containerMemoryWatch.S_INCIDENT_EXITED_OOM_KILLED]
    assert listIncidents[0]["sContainerId"] == S_OLD_ID


def testEvidenceOfAnOrdinaryExitRecordsNothing():
    appState = SimpleNamespace(
        dictContainerMemory=containerMemoryWatch.fdictCreateMemoryStore())
    containerMemoryWatch.fnRecordExitedEvidenceForApp(appState, S_NAME, {
        "bAnswered": True, "bOomKilled": False, "sContainerId": S_OLD_ID,
    })
    assert appState.dictContainerMemory == {}


def _fConfig():
    return SimpleNamespace(sProjectName=S_NAME)


@pytest.mark.falsification
def testTheCliReadsTheEvidenceBeforeItRemovesTheContainer(daemonFake, capsys):
    """Kills: the CLI removing the stopped container before inspecting it."""
    from vaibify.cli.commandStart import fnClearStoppedContainerBeforeLaunch
    fnClearStoppedContainerBeforeLaunch(_fConfig())
    assert daemonFake.listCalls == [
        ("status", S_NAME), ("inspect", S_NAME), ("remove", S_NAME)]
    sPrinted = capsys.readouterr().out
    assert "was killed for lack of memory" in sPrinted
    assert S_OLD_ID[:12] in sPrinted


def testTheCliSaysNothingAboutMemoryForAnOrdinaryExit(daemonFake, capsys):
    from vaibify.cli.commandStart import fnClearStoppedContainerBeforeLaunch
    daemonFake.bOomKilled = False
    fnClearStoppedContainerBeforeLaunch(_fConfig())
    assert "memory" not in capsys.readouterr().out


def testTheCliStillRemovesWhenTheInspectFails(daemonFake):
    from vaibify.cli.commandStart import fnClearStoppedContainerBeforeLaunch
    daemonFake.bInspectFails = True
    fnClearStoppedContainerBeforeLaunch(_fConfig())
    assert ("remove", S_NAME) in daemonFake.listCalls


def testTheStartRouteRecordsTheEvidenceItsWorkerFound(tmp_path, monkeypatch):
    """Drive a real hub start whose worker found an OOM-killed predecessor.

    The worker runs on a thread with no app state, so the evidence rides
    the start task record and is recorded when the start settles.
    """
    import os
    from vaibify.config import containerLock, registryManager
    from tests import testStartReservation as harness
    monkeypatch.setattr(
        containerLock, "_S_LOCK_DIRECTORY", str(tmp_path / "locks"))
    sRegistryDirectory = str(tmp_path / ".vaibify")
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_DIRECTORY", sRegistryDirectory)
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_PATH",
        os.path.join(sRegistryDirectory, "registry.json"))

    def fsExecute(sName, reservation, configProject):
        reservation.recordStartTask.dictExitedOomEvidence = {
            "bAnswered": True, "bOomKilled": True, "sContainerId": S_OLD_ID,
            "iExitCode": 137, "sFinishedIso": "2026-10-08T22:16:13+00:00",
        }
        return harness.S_STARTED_CONTAINER_ID

    executor = SimpleNamespace(fsExecute=fsExecute)
    harness.fnInstallExecutor(monkeypatch, executor)
    with patch.object(
        harness.pipelineServer, "_fconnectionCreateDocker",
        harness.MockDockerProjectNotRunning,
    ):
        appHub = harness.pipelineServer.fappCreateHubApplication(
            iExpectedPort=0)
    sCredential = harness.fsBootstrapCredential(appHub)
    with harness.fclientLive(appHub, sCredential) as client:
        harness.fnRegisterProject(client, tmp_path)
        dictStart = client.post(
            f"/api/containers/{harness.S_PROJECT_NAME}/start").json()
        harness.fnWaitForSettledResult(
            client, appHub, dictStart["sReservationId"])
    dictRecord = appHub.state.dictContainerMemory[harness.S_PROJECT_NAME]
    assert [d["sContainerId"] for d in dictRecord["listIncidents"]] == [
        S_OLD_ID]
