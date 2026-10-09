"""What recreating a container throws away, measured before anyone confirms.

Restart, Rebuild and the image switches all create a NEW container, so
whatever an agent left in the old container's writable layer -- its
scratch files in /tmp above all -- is gone. The confirmations used to say
only "Workspace files are preserved." Each now carries one sentence from
the server, with /tmp measured by a typed read (``du -sxk``) under the
same bounded deadline as the memory watch. A measurement that did not
happen is said to have not happened, and a timeout is never a size.
"""

import asyncio
import subprocess
import sys
import threading

import pytest

from vaibify.docker import dockerConnection, writableLayerLoss as loss
from vaibify.gui import writableLayerPreview


S_PRESERVED = "mounted volumes and host directories are preserved."


def testDuOutputParsesToBytes():
    assert loss.fiParseTmpBytes("1433604\t/tmp\n") == 1433604 * 1024
    assert loss.fiParseTmpBytes("du: cannot read x\n12\t/tmp\n") == 12 * 1024
    assert loss.fiParseTmpBytes("") is None
    assert loss.fiParseTmpBytes("garbage\n") is None


def testAMeasuredLossNamesTheSize():
    sSentence = loss.fsDescribeWritableLayerLoss(
        loss.S_STATE_MEASURED, 1433604 * 1024)
    assert sSentence == (
        "Files in the container's writable layer, including 1.4 GB in "
        "/tmp, are discarded; " + S_PRESERVED)


@pytest.mark.parametrize("sState,sReason,sClause", [
    (loss.S_STATE_TIMEOUT, "the container did not answer within 5 seconds",
     "the container did not answer within 5 seconds"),
    (loss.S_STATE_NOT_RUNNING, "", "the container is not running"),
    (loss.S_STATE_UNREADABLE, "Docker did not describe the container",
     "Docker did not describe the container"),
])
def testAnUnmeasuredLossSaysWhyAndNamesNoSize(sState, sReason, sClause):
    sSentence = loss.fsDescribeWritableLayerLoss(sState, None, sReason)
    assert sSentence == (
        "Files in the container's writable layer, including /tmp, are "
        f"discarded (the size of /tmp could not be measured: {sClause}); "
        + S_PRESERVED)


def testANeverAttemptedMeasurementClaimsNoSizeAndNoFailure():
    sSentence = loss.fsDescribeWritableLayerLoss(loss.S_STATE_NOT_MEASURED)
    assert sSentence == (
        "Files in the container's writable layer, including /tmp, are "
        "discarded; " + S_PRESERVED)


def testTheTypedReadProgramMeasuresADirectory(tmp_path):
    (tmp_path / "scratch.bin").write_bytes(b"\0" * 300000)
    sProgram = dockerConnection._DICT_TYPED_READ_PROGRAMS[
        dockerConnection.S_TYPED_READ_TMP_SIZE
    ].replace(
        dockerConnection._S_TYPED_READ_PATH_SLOT,
        dockerConnection._fsTypedReadPathLiteral(str(tmp_path)),
    )
    processResult = subprocess.run(
        [sys.executable, "-c", sProgram], capture_output=True, text=True)
    assert processResult.returncode == 0, processResult.stderr
    assert loss.fiParseTmpBytes(processResult.stdout) >= 290000


# ---------------------------------------------------------------------
# The preview: inspect, then a bounded read
# ---------------------------------------------------------------------

S_NAME = "preview-lane-project"
S_ID = "9a1e000000000000000000000000000000000000000000000000000000000ccc"


class _ConnectionDouble:
    def __init__(self, eventRelease=None, errorRaised=None):
        self.eventRelease = eventRelease
        self.errorRaised = errorRaised
        self.listReadIds = []

    def fiReadTmpBytes(self, sContainerId):
        self.listReadIds.append(sContainerId)
        if self.errorRaised is not None:
            raise self.errorRaised
        if self.eventRelease is not None:
            self.eventRelease.wait(timeout=10)
        return 2 * 2 ** 30


def _fdictPreview(monkeypatch, dictInspect, connectionDouble):
    monkeypatch.setattr(
        "vaibify.docker.containerManager.fjsonInspectContainer",
        lambda sName: dictInspect)
    return asyncio.run(writableLayerPreview.fdictPreviewWritableLayer(
        connectionDouble, S_NAME, {}))


def testARunningContainerIsMeasuredByItsId(monkeypatch):
    connectionDouble = _ConnectionDouble()
    dictPreview = _fdictPreview(
        monkeypatch, {"Id": S_ID, "State": {"Running": True}},
        connectionDouble)
    assert connectionDouble.listReadIds == [S_ID]
    assert dictPreview["sState"] == loss.S_STATE_MEASURED
    assert dictPreview["iTmpBytes"] == 2 * 2 ** 30
    assert "including 2 GB in /tmp" in dictPreview["sSentence"]


def testAStoppedContainerIsNotRunAgainst(monkeypatch):
    connectionDouble = _ConnectionDouble()
    dictPreview = _fdictPreview(
        monkeypatch, {"Id": S_ID, "State": {"Running": False}},
        connectionDouble)
    assert connectionDouble.listReadIds == []
    assert dictPreview["sState"] == loss.S_STATE_NOT_RUNNING
    assert dictPreview["iTmpBytes"] is None


def testADaemonThatDoesNotDescribeTheContainerIsUnreadable(monkeypatch):
    dictPreview = _fdictPreview(monkeypatch, {}, _ConnectionDouble())
    assert dictPreview["sState"] == loss.S_STATE_UNREADABLE
    assert dictPreview["iTmpBytes"] is None


def testAFailedReadIsUnreadable(monkeypatch):
    dictPreview = _fdictPreview(
        monkeypatch, {"Id": S_ID, "State": {"Running": True}},
        _ConnectionDouble(errorRaised=OSError("du failed")))
    assert dictPreview["sState"] == loss.S_STATE_UNREADABLE


@pytest.mark.falsification
def testAHungReadIsATimeoutNeverASize(monkeypatch):
    """Kills: reporting 0 bytes when the read missed its deadline."""
    from vaibify.gui import containerMemoryWatch
    monkeypatch.setattr(
        containerMemoryWatch, "F_MEMORY_READ_TIMEOUT_SECONDS", 0.2)
    eventRelease = threading.Event()
    try:
        dictPreview = _fdictPreview(
            monkeypatch, {"Id": S_ID, "State": {"Running": True}},
            _ConnectionDouble(eventRelease=eventRelease))
    finally:
        eventRelease.set()
    assert dictPreview["sState"] == loss.S_STATE_TIMEOUT
    assert dictPreview["iTmpBytes"] is None
    assert "could not be measured" in dictPreview["sSentence"]


def testTheRepairLaneSpeaksTheSameSentence(monkeypatch):
    from vaibify.docker import containerLifecycleRepair as repair
    listAnnounced = []
    monkeypatch.setattr(repair, "_fnRefuseWhileLiveWork", lambda *a: None)
    monkeypatch.setattr(
        repair, "fsResolveRunningImageIdentity", lambda s: "sha256:abc")
    monkeypatch.setattr(repair, "_fsPrepareJournalRecord", lambda *a: "op1")
    monkeypatch.setattr(
        repair, "_fdictMutateThroughTheGateway",
        lambda *a: {"sContainerId": "new"})
    monkeypatch.setattr(
        repair.operationJournal, "fnSettleOperation", lambda *a: None)
    monkeypatch.setattr(
        repair, "fsDescribeRestartConsequences", lambda s: "Restarting")
    repair.fdictRecreateUnderJournal(
        None, S_NAME, fnAnnounce=listAnnounced.append)
    assert loss.fsDescribeWritableLayerLoss(loss.S_STATE_NOT_MEASURED) in (
        listAnnounced[0])


def testTheRouteAnswersForARegisteredProjectOnly(tmp_path, monkeypatch):
    """Over real HTTP: a registered name is measured, any other is 404."""
    from tests.testContainerLifecycleGating import (
        fclientAuthenticated, fnRegisterProject,
    )
    import os
    from unittest.mock import patch
    from vaibify.config import containerLock, registryManager
    from vaibify.gui import pipelineServer
    from tests.testAgentLaneEnforcement import MockDockerConnection
    monkeypatch.setattr(
        containerLock, "_S_LOCK_DIRECTORY", str(tmp_path / "locks"))
    sRegistry = str(tmp_path / ".vaibify")
    monkeypatch.setattr(registryManager, "_S_REGISTRY_DIRECTORY", sRegistry)
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_PATH",
        os.path.join(sRegistry, "registry.json"))
    monkeypatch.setattr(
        registryManager, "_S_LOCK_PATH",
        os.path.join(sRegistry, "registry.lock"))
    listInspected = []
    monkeypatch.setattr(
        "vaibify.docker.containerManager.fjsonInspectContainer",
        lambda sName: listInspected.append(sName) or {
            "Id": S_ID, "State": {"Running": False}})
    with patch.object(
        pipelineServer, "_fconnectionCreateDocker", MockDockerConnection,
    ):
        appHub = pipelineServer.fappCreateHubApplication(iExpectedPort=0)
    with fclientAuthenticated(appHub) as client:
        fnRegisterProject(client, tmp_path, S_NAME)
        dictPreview = client.get(
            f"/api/containers/{S_NAME}/writable-layer-preview").json()
        responseGhost = client.get(
            "/api/containers/never-registered/writable-layer-preview")
    assert dictPreview["sState"] == loss.S_STATE_NOT_RUNNING
    assert dictPreview["sSentence"].endswith(S_PRESERVED)
    assert responseGhost.status_code == 404
    assert listInspected == [S_NAME]
