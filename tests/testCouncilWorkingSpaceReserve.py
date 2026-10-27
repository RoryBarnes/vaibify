"""A full-sandbox council leaves its members room to write.

Members of a full-sandbox council edit and run their own copies, and
before this reserve the largest admissible snapshot equalled the
runner's whole working tree: a snapshot at the limit filled ``/council``
and the first file a member wrote failed for want of space. The ruling
(D5, 2026-10-26) keeps half the working tree free in that kind; a
read-only council writes nothing and keeps today's ceiling.

The expected values are computed here from the ruling and the measured
tmpfs page size, not read back from the module under test.
"""

import pytest

from vaibify.gui import agentCouncilCapacity

I_GIBIBYTE = 1024 ** 3
I_PAGE = 4096


class _DaemonOfSize:
    def __init__(self, iMemoryBytes):
        self.iMemoryBytes = iMemoryBytes

    def fdictReadDaemonCapacity(self):
        return {"iMemoryBytes": self.iMemoryBytes}


@pytest.mark.falsification
def testAFullSandboxCeilingKeepsHalfTheWorkingTreeFree():
    """Kills: a full-sandbox ceiling equal to the whole working tree.

    Checked on an unmeasurable daemon (the floors) and a large one.
    """
    for iDaemonMemoryBytes in (0, 64 * I_GIBIBYTE):
        dictFull = agentCouncilCapacity.fdictResolveCouncilCapacity(
            _DaemonOfSize(iDaemonMemoryBytes))
        iWorkingTree = dictFull["iRunnerWorkingTreeBytes"]
        iExpected = (iWorkingTree - iWorkingTree // 2
                     - dictFull["iMaxSnapshotFileCount"] * I_PAGE)
        assert dictFull["iMaxSnapshotTotalBytes"] == iExpected
        assert dictFull["bReservesWorkingSpace"] is True
        assert dictFull["iMaxSnapshotMemberBytes"] <= iExpected


def testAReadOnlyCeilingIsTheWholeWorkingTree():
    dictReadOnly = agentCouncilCapacity.fdictResolveCouncilCapacity(
        _DaemonOfSize(64 * I_GIBIBYTE), bReserveWorkingSpace=False)
    assert dictReadOnly["iMaxSnapshotTotalBytes"] == (
        dictReadOnly["iRunnerWorkingTreeBytes"])
    assert dictReadOnly["bReservesWorkingSpace"] is False


def testTheRoundingBoundIsOnePagePerFile():
    assert agentCouncilCapacity.fiEstimateTmpfsRoundingBytes(0) == 0
    assert agentCouncilCapacity.fiEstimateTmpfsRoundingBytes(3) == 3 * I_PAGE


def testTheWorkingSpaceFiguresSubtractTheSnapshotAndItsRounding():
    dictSpace = agentCouncilCapacity.fdictDescribeWorkingSpace(
        iWorkingTreeBytes=512 * 1024 * 1024, iScratchBytes=64 * 1024 * 1024,
        iSnapshotBytes=100 * 1024 * 1024, iFileCount=1000)
    assert dictSpace["iFreeAfterCopyBytes"] == (
        412 * 1024 * 1024 - 1000 * I_PAGE)
    assert dictSpace["iScratchBytes"] == 64 * 1024 * 1024


@pytest.mark.falsification
def testAWholeCopyBaselineIsOfferedOnlyWhenHalfTheReserveSurvivesIt():
    """Kills: offering a whole-copy git baseline whatever its size.

    Working tree 512 MiB, so the reserve is 256 MiB and half of it 128.
    A 50 MiB snapshot of one file leaves 462 free, and a second copy for
    git leaves 412: offered. A 180 MiB snapshot leaves 332 free, and a
    second copy leaves 152 minus rounding: still offered. At 200 MiB,
    312 free and a second copy leaves 112: refused.
    """
    iMebibyte = 1024 * 1024

    def fbFits(iSnapshotMebibytes):
        return agentCouncilCapacity.fdictDescribeWorkingSpace(
            512 * iMebibyte, 64 * iMebibyte, iSnapshotMebibytes * iMebibyte,
            1)["bFullBaselineFits"]

    assert fbFits(50) is True
    assert fbFits(180) is True
    assert fbFits(200) is False


@pytest.mark.falsification
def testTheSnapshotCaptureIsBoundedByItsCouncilKind(monkeypatch):
    """Kills: the convene route resolving one ceiling for every kind.

    The capture builder resolves its bounds once, when it is built, so
    building it is enough to see which ceiling it asked for.
    """
    from vaibify.gui.routes import councilRoutes
    listRequested = []

    def fdictRecordTheKind(connectionDocker=None, bReserveWorkingSpace=True):
        listRequested.append(bReserveWorkingSpace)
        return {}

    monkeypatch.setattr(
        agentCouncilCapacity, "fdictResolveCouncilCapacity",
        fdictRecordTheKind)
    for bReserveWorkingSpace in (True, False):
        councilRoutes._ffnBuildSnapshotCapture(
            {"docker": None}, None, "containerId", "/workspace/project",
            "campaignId", "containerName",
            bReserveWorkingSpace=bReserveWorkingSpace)
    assert listRequested == [True, False]


class _WeighingConnection(_DaemonOfSize):
    def __init__(self, iTotalBytes):
        super().__init__(0)
        self.iTotalBytes = iTotalBytes

    def fdictWeighRepository(self, sContainerId, sProjectRepoPath):
        return {"iFileCount": 10, "iTotalBytes": self.iTotalBytes,
                "bTruncated": False, "listLargestFiles": []}


def testThePreflightSaysWhenOnlyAReadOnlyCouncilFits():
    """Judged against the larger ceiling, so the button stays offered,
    and the default kind's own verdict rides beside it."""
    from vaibify.gui import agentCouncilContext
    dictFloor = agentCouncilCapacity.fdictFloorCouncilCapacity()
    iBetween = (agentCouncilCapacity.fdictReserveWorkingSpace(dictFloor)[
        "iMaxSnapshotTotalBytes"] + dictFloor["iMaxSnapshotTotalBytes"]) // 2
    dictFeasibility = agentCouncilContext.fdictAssessSnapshotFeasibility(
        _WeighingConnection(iBetween), "containerId", "/workspace/project")
    assert dictFeasibility["bFits"] is True
    assert dictFeasibility["bFitsFullSandbox"] is False
    assert "read-only council" in dictFeasibility["sFullSandboxNote"]


def testASizeRefusalTheReserveCausedNamesTheWaysOut():
    import tarfile
    from vaibify.gui import agentCouncilContext
    dictBounds = agentCouncilCapacity.fdictReserveWorkingSpace(
        agentCouncilCapacity.fdictFloorCouncilCapacity())
    infoMember = tarfile.TarInfo(name="large.dat")
    infoMember.size = dictBounds["iMaxSnapshotTotalBytes"] + 1
    dictCapture = {"dictBounds": dict(
        dictBounds, iMaxSnapshotMemberBytes=infoMember.size),
        "iIncludedMemberCount": 0, "iTotalContentBytes": 0}
    with pytest.raises(agentCouncilContext.SnapshotRefusedError) as errorInfo:
        agentCouncilContext._fnEnforceCaptureLimits(
            dictCapture, infoMember, "large.dat")
    assert "files git tracks" in str(errorInfo.value)
    assert "read-only council" in str(errorInfo.value)
