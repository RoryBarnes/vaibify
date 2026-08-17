"""The durable pre-execution pull marker (spec §4.5 condition 1).

Published before a step declaring ``listRemoteData`` runs, cleared
only after every declared file was examined and the record merge
committed. The marker is the crash guarantee: pulled bytes on disk
can never be silently undocumented, because either their records
committed (marker cleared) or the marker still says a pull was in
flight. It fails CLOSED — no durable marker, no execution — and it
lives at the state-document ROOT because a field inside the saving
project's own section is rebuilt from the in-memory workflow on
every ordinary save and silently vanishes.
"""

import asyncio
import json
import shlex
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vaibify.gui.pipelineRunner import _fiExecuteAndRecord
from vaibify.gui.stateManager import (
    S_REMOTE_MARKER_ROOT_KEY,
    fdictClearRemoteDataMarker,
    fdictInstallWorkflowSection,
    fdictPublishRemoteDataMarker,
    fdictReadRemoteDataMarker,
    flistUnresolvedRemoteDataStepIds,
    fnSaveStateToContainer,
    ftLoadStateWithStatus,
)

_S_CONTAINER = "cid"
_S_STATE_PATH = "/workspace/repo/.vaibify/state.json"
_S_WORKFLOW_KEY = ".vaibify/projects/pull.json"
_S_STEP_ID = "pull-archive"
_S_SHA_FRESH = "d" * 64


class _FakeStateDocker:
    """A working files-on-disk model: write, checkpoint, atomic rename."""

    def __init__(self, dictInitialDocument=None):
        self.dictFiles = {}
        if dictInitialDocument is not None:
            self.dictFiles[_S_STATE_PATH] = json.dumps(
                dictInitialDocument,
            ).encode("utf-8")

    def fbaFetchFile(self, _sContainerId, sPath):
        if sPath in self.dictFiles:
            return self.dictFiles[sPath]
        raise FileNotFoundError(sPath)

    def fnWriteFile(self, _sContainerId, sPath, baPayload):
        self.dictFiles[sPath] = baPayload

    def ftResultExecuteCommand(self, _sContainerId, sCommand):
        if sCommand.startswith("mv -f "):
            listParts = shlex.split(sCommand.split("||")[0])
            sTempPath, sTargetPath = listParts[2], listParts[3]
            if sTempPath in self.dictFiles:
                self.dictFiles[sTargetPath] = self.dictFiles.pop(
                    sTempPath,
                )
            return (0, "")
        return (0, "")


class _RenameDroppingDocker(_FakeStateDocker):
    """Reports the rename succeeded while installing nothing."""

    def ftResultExecuteCommand(self, _sContainerId, sCommand):
        if sCommand.startswith("mv -f "):
            return (0, "")
        return (0, "")


def _fdictLoadStateFile(dockerFake):
    dictDocument, _sStatus = ftLoadStateWithStatus(
        dockerFake, _S_CONTAINER, _S_STATE_PATH,
    )
    return dictDocument


def _fdictPublish(dockerFake, listPaths=("data/pull.fits",)):
    return fdictPublishRemoteDataMarker(
        dockerFake, _S_CONTAINER, _S_STATE_PATH, _S_WORKFLOW_KEY,
        _S_STEP_ID, list(listPaths),
    )


# ---------------------------------------------------------------------------
# Publish: durable, acknowledged, fail-closed
# ---------------------------------------------------------------------------


def testAPublishLandsDurablyWithItsExpectedRecords():
    dockerFake = _FakeStateDocker()
    dictOutcome = _fdictPublish(
        dockerFake, ["data/pull.fits", "data/aux.fits"],
    )
    assert dictOutcome["bPublished"] is True
    dictMarker = fdictReadRemoteDataMarker(
        _fdictLoadStateFile(dockerFake), _S_WORKFLOW_KEY, _S_STEP_ID,
    )
    assert dictMarker is not None
    assert dictMarker["listExpectedPaths"] == [
        "data/aux.fits", "data/pull.fits",
    ]
    assert flistUnresolvedRemoteDataStepIds(
        _fdictLoadStateFile(dockerFake), _S_WORKFLOW_KEY,
    ) == [_S_STEP_ID]


@pytest.mark.falsification
def testAPublishThatCannotBeReadBackRefuses():
    """Kills: the durable acknowledgment — a write is not a guarantee.

    The fake reports the atomic rename succeeded while installing
    nothing, which is what a full disk or a dying daemon looks like.
    Without the read-back, the step would run on a marker that never
    became durable, and a crash would leave its pull undocumented
    with the protocol claiming otherwise.
    """
    dictOutcome = _fdictPublish(_RenameDroppingDocker())
    assert dictOutcome["bPublished"] is False
    assert "read back" in dictOutcome["sDetail"]


def testAPublishWithNoDurableHomeRefuses():
    dictOutcome = fdictPublishRemoteDataMarker(
        _FakeStateDocker(), _S_CONTAINER, _S_STATE_PATH, "",
        _S_STEP_ID, ["data/pull.fits"],
    )
    assert dictOutcome["bPublished"] is False


@pytest.mark.falsification
def testAMarkerSurvivesTheOwningProjectsOwnSave():
    """Kills: the document carry-through the root placement relies on.

    An ordinary save of the marker's OWN workflow rebuilds that
    workflow's section from memory — a marker stored in the section
    (or a save that rebuilds the whole document, the v2 defect)
    silently erases the crash guarantee mid-run, exactly when a
    researcher saves while a pull step is executing.
    """
    dockerFake = _FakeStateDocker()
    assert _fdictPublish(dockerFake)["bPublished"] is True
    fnSaveStateToContainer(
        dockerFake, _S_CONTAINER, _S_STATE_PATH,
        {"dictStepState": {}}, sWorkflowKey=_S_WORKFLOW_KEY,
    )
    assert fdictReadRemoteDataMarker(
        _fdictLoadStateFile(dockerFake), _S_WORKFLOW_KEY, _S_STEP_ID,
    ) is not None, (
        "the owning project's ordinary save erased its own pull "
        "marker; the crash guarantee lasted only until the next save"
    )


def testAMarkerSurvivesASiblingProjectsSave():
    dockerFake = _FakeStateDocker()
    assert _fdictPublish(dockerFake)["bPublished"] is True
    fnSaveStateToContainer(
        dockerFake, _S_CONTAINER, _S_STATE_PATH,
        {"dictStepState": {}},
        sWorkflowKey=".vaibify/projects/sibling.json",
    )
    assert fdictReadRemoteDataMarker(
        _fdictLoadStateFile(dockerFake), _S_WORKFLOW_KEY, _S_STEP_ID,
    ) is not None


def testClearingAnAbsentMarkerIsIdempotent():
    dockerFake = _FakeStateDocker({"iStateSchemaVersion": 3})
    dictOutcome = fdictClearRemoteDataMarker(
        dockerFake, _S_CONTAINER, _S_STATE_PATH, _S_WORKFLOW_KEY,
        _S_STEP_ID,
    )
    assert dictOutcome["bCleared"] is True


def testMarkersDoNotLeakAcrossWorkflowNamespaces():
    dockerFake = _FakeStateDocker()
    assert _fdictPublish(dockerFake)["bPublished"] is True
    assert flistUnresolvedRemoteDataStepIds(
        _fdictLoadStateFile(dockerFake),
        ".vaibify/projects/other.json",
    ) == []


# ---------------------------------------------------------------------------
# The runner bracket: publish -> execute -> commit -> clear
# ---------------------------------------------------------------------------


def _ftRunMarkeredStep(
    iStepExitCode, dockerState, sShaOutput="", dictCommitAnswer=None,
):
    """Drive one remote-data step through the marker bracket.

    Returns ``(iReturned, listEvents, listCommandsRun)``. The state
    docker is REAL enough to hold the marker file; step execution and
    hashing are stubbed at their seams.
    """
    listEvents = []

    async def fnCallback(dictEvent):
        listEvents.append(dictEvent)

    listCommandsRun = []

    async def ftFakeRunStepCommands(*tArgs, **dictKeywords):
        listCommandsRun.append(tArgs)
        return (iStepExitCode, 1.0)

    def fdictCommitStub(_sStepId, _listRecords):
        return dictCommitAnswer or {
            "bCommitted": True, "sDetail": "",
            "iInstalled": 1, "listRefusals": [],
        }

    dictStep = {
        "sStepId": _S_STEP_ID,
        "sDirectory": "/ws/step",
        "listRemoteData": [{
            "sPath": "data/pull.fits",
            "sSourceUrl": "https://archive.example/query",
        }],
    }
    dockerState.dictShaAnswer = sShaOutput
    fnRealExecute = dockerState.ftResultExecuteCommand

    def ftRouteExecute(sContainerId, sCommand):
        if sCommand.startswith("sha256sum"):
            return (0, dockerState.dictShaAnswer)
        return fnRealExecute(sContainerId, sCommand)

    dockerState.ftResultExecuteCommand = ftRouteExecute
    with patch(
        "vaibify.gui.pipelineRunner.ftRunStepCommands",
        new=ftFakeRunStepCommands,
    ), patch(
        "vaibify.gui.pipelineRunner._fsetSnapshotDirectory",
        new=AsyncMock(return_value=set()),
    ), patch(
        "vaibify.gui.pipelineRunner._fnEmitDiscoveredOutputs",
        new=AsyncMock(),
    ), patch(
        "vaibify.gui.workflowManager.flistCleanStepScratchDirs",
        new=MagicMock(),
    ):
        iReturned = asyncio.run(_fiExecuteAndRecord(
            dockerState, _S_CONTAINER, dictStep,
            1, "/ws", {"sRepoRoot": "/workspace/repo"}, fnCallback,
            fdictCommitProvenance=fdictCommitStub,
            dictMarkerContext={
                "sStatePath": _S_STATE_PATH,
                "sWorkflowKey": _S_WORKFLOW_KEY,
            },
        ))
    return iReturned, listEvents, listCommandsRun


def _fbMarkerStillSet(dockerState):
    return fdictReadRemoteDataMarker(
        _fdictLoadStateFile(dockerState), _S_WORKFLOW_KEY, _S_STEP_ID,
    ) is not None


@pytest.mark.falsification
def testAFailedPublishMeansTheStepDoesNotRun():
    """Kills: fail-closed — no durable marker, no execution.

    Running anyway is the §4.5 crash window re-opened: the pull
    happens, the crash lands, and nothing on disk says remote data
    was ever in flight.
    """
    listEvents = []

    async def fnCallback(dictEvent):
        listEvents.append(dictEvent)

    dictStep = {
        "sStepId": _S_STEP_ID,
        "sDirectory": "/ws/step",
        "listRemoteData": [{"sPath": "data/pull.fits"}],
    }
    mockRunCommands = AsyncMock(return_value=(0, 1.0))
    with patch(
        "vaibify.gui.pipelineRunner.ftRunStepCommands",
        new=mockRunCommands,
    ), patch(
        "vaibify.gui.pipelineRunner._fsetSnapshotDirectory",
        new=AsyncMock(return_value=set()),
    ), patch(
        "vaibify.gui.pipelineRunner._fnEmitDiscoveredOutputs",
        new=AsyncMock(),
    ), patch(
        "vaibify.gui.workflowManager.flistCleanStepScratchDirs",
        new=MagicMock(),
    ):
        iReturned = asyncio.run(_fiExecuteAndRecord(
            _RenameDroppingDocker(), _S_CONTAINER, dictStep,
            1, "/ws", {"sRepoRoot": "/workspace/repo"}, fnCallback,
            dictMarkerContext={
                "sStatePath": _S_STATE_PATH,
                "sWorkflowKey": _S_WORKFLOW_KEY,
            },
        ))
    assert iReturned != 0
    assert mockRunCommands.await_count == 0, (
        "the step executed without a durable pull marker; the crash "
        "guarantee never existed for this run"
    )
    assert any(
        dictEvent.get("sType") == "stepMarkerRefused"
        for dictEvent in listEvents
    )


def testASuccessfulReconciledStepClearsTheMarker():
    dockerState = _FakeStateDocker()
    iReturned, listEvents, listCommandsRun = _ftRunMarkeredStep(
        0, dockerState,
        sShaOutput=f"{_S_SHA_FRESH}  /workspace/repo/data/pull.fits",
    )
    assert iReturned == 0
    assert listCommandsRun, "the step never executed"
    assert not _fbMarkerStillSet(dockerState), (
        "a fully reconciled step left its marker set; every success "
        "would gate the reproducibility level forever"
    )
    assert not any(
        dictEvent.get("sType") == "remoteDataMarkerRetained"
        for dictEvent in listEvents
    )


@pytest.mark.falsification
def testACommandFailureLeavesTheMarkerSet():
    """Kills: the exit-code conjunct of the clear condition.

    A failed step may have pulled files the examination did not
    describe; clearing anyway converts "possibly undocumented data"
    into a positive claim of full documentation.
    """
    dockerState = _FakeStateDocker()
    iReturned, listEvents, _listCommandsRun = _ftRunMarkeredStep(
        7, dockerState,
        sShaOutput=f"{_S_SHA_FRESH}  /workspace/repo/data/pull.fits",
    )
    assert iReturned == 7
    assert _fbMarkerStillSet(dockerState), (
        "a failed step's marker was cleared; the pulled data now "
        "claims to be fully documented"
    )
    assert any(
        dictEvent.get("sType") == "remoteDataMarkerRetained"
        for dictEvent in listEvents
    )


@pytest.mark.falsification
def testAnUnexaminedFileLeavesTheMarkerSet():
    """Kills: the every-file-examined conjunct of the clear condition.

    A declared file the hash never saw (missing, unreadable) is
    exactly the record the marker exists to flag.
    """
    dockerState = _FakeStateDocker()
    iReturned, listEvents, _listCommandsRun = _ftRunMarkeredStep(
        0, dockerState, sShaOutput="",
    )
    assert iReturned == 0
    assert _fbMarkerStillSet(dockerState), (
        "the marker cleared although a declared file was never "
        "examined"
    )
    listRetained = [
        dictEvent for dictEvent in listEvents
        if dictEvent.get("sType") == "remoteDataMarkerRetained"
    ]
    assert listRetained and "data/pull.fits" in (
        listRetained[0]["sReason"]
    )


def testARefusedRecordLeavesTheMarkerSet():
    dockerState = _FakeStateDocker()
    _iReturned, listEvents, _listCommandsRun = _ftRunMarkeredStep(
        0, dockerState,
        sShaOutput=f"{_S_SHA_FRESH}  /workspace/repo/data/pull.fits",
        dictCommitAnswer={
            "bCommitted": True, "sDetail": "", "iInstalled": 0,
            "listRefusals": [{
                "sPath": "data/pull.fits",
                "sReason": "the record's declaration changed",
            }],
        },
    )
    assert _fbMarkerStillSet(dockerState)
    assert any(
        "declaration changed" in dictEvent.get("sReason", "")
        for dictEvent in listEvents
        if dictEvent.get("sType") == "remoteDataMarkerRetained"
    )


def testAFailedCommitLeavesTheMarkerSet():
    dockerState = _FakeStateDocker()
    _iReturned, _listEvents, _listCommandsRun = _ftRunMarkeredStep(
        0, dockerState,
        sShaOutput=f"{_S_SHA_FRESH}  /workspace/repo/data/pull.fits",
        dictCommitAnswer={
            "bCommitted": False,
            "sDetail": "project.json could not be read",
            "iInstalled": 0, "listRefusals": [],
        },
    )
    assert _fbMarkerStillSet(dockerState)


def testDocumentInstallCarriesUnknownRootKeysThrough():
    """The property the root placement rests on, asserted directly."""
    dictDocument = {S_REMOTE_MARKER_ROOT_KEY: {
        _S_WORKFLOW_KEY: {_S_STEP_ID: {"sStepId": _S_STEP_ID}},
    }}
    dictInstalled = fdictInstallWorkflowSection(
        dictDocument, ".vaibify/projects/other.json",
        {"dictStepState": {}},
    )
    assert dictInstalled[S_REMOTE_MARKER_ROOT_KEY] == (
        dictDocument[S_REMOTE_MARKER_ROOT_KEY]
    )
