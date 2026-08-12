"""Two writers of one state file must not destroy each other's install.

Covers both files vaibify keeps this way — ``state.json`` through
``stateManager`` and ``pipeline_state.json`` through ``pipelineState``.
They are separate modules running one protocol, which is why they had
one defect, and why their proofs live together.

Driven against the REAL ``HostConnection`` — real files, a real
``mv``, a real journal in a temp directory — because the defect these
tests exist for was invisible to every mock: a double that records the
paths it was handed cannot notice that two writers were handed the
same one.

The interleaving is forced rather than raced. A save is
write-temp / checkpoint / rename, and the window that matters is
between the write and the rename, so the second saver is run from
inside the first one's checkpoint step. That is the same ordering the
hub produces on its own — a step edit saves under the drain while the
file poll saves from the event loop and the run saves from its own
thread — without depending on a scheduler to reproduce it.

``stateManager`` is mode-agnostic, so the protocol proven here is the
one the container leg runs too; only the connection under it differs.
"""

import json
import os
from unittest.mock import patch

import pytest

from vaibify.config import containerLock, operationJournal
from vaibify.gui import pipelineState, stateManager
from vaibify.host import hostScratch
from vaibify.host.hostConnection import HostConnection

S_RESOURCE_ID = "concurrent-state-project"


@pytest.fixture(autouse=True)
def fixtureIsolateJournalAndScratch(tmp_path, monkeypatch):
    """Redirect the journal, locks, and scratch roots to tmp_path."""
    monkeypatch.setattr(
        operationJournal, "_S_JOURNAL_DIRECTORY", str(tmp_path / "journal"),
    )
    monkeypatch.setattr(
        containerLock, "_S_LOCK_DIRECTORY", str(tmp_path / "locks"),
    )
    monkeypatch.setattr(
        hostScratch, "_S_HOST_DIAGNOSTICS_ROOT",
        str(tmp_path / "host-diagnostics"),
    )


@pytest.fixture()
def tProjectAndConnection(tmp_path):
    """Return (sStatePath, HostConnection) over a temp project repo."""
    sProjectRoot = str(tmp_path / "project")
    os.makedirs(os.path.join(sProjectRoot, ".vaibify"))
    connection = HostConnection(
        fnResolveProjectRoot=lambda sResourceId: sProjectRoot,
    )
    return os.path.join(sProjectRoot, ".vaibify", "state.json"), connection


def _fnSaveWithASecondSaverInTheWindow(
    connection, sStatePath, dictOuter, dictInner,
):
    """Save ``dictOuter``, running a whole save of ``dictInner`` inside it.

    The inner save lands between the outer save's temp write and its
    rename — the exact overlap the hub produces, made deterministic.
    ``patch.object`` rather than monkeypatch: the fixture instance is
    shared, and undoing it would revert the journal redirect with it.
    """
    fnRealCheckpoint = stateManager._fnCheckpointPriorState
    listInnerSaves = []

    def fnCheckpointThenLetTheOtherSaverFinish(*aArgs, **dictKwargs):
        if not listInnerSaves:
            listInnerSaves.append(1)
            stateManager.fnSaveStateToContainer(
                connection, S_RESOURCE_ID, sStatePath, dictInner,
            )
        return fnRealCheckpoint(*aArgs, **dictKwargs)

    with patch.object(
        stateManager, "_fnCheckpointPriorState",
        fnCheckpointThenLetTheOtherSaverFinish,
    ):
        stateManager.fnSaveStateToContainer(
            connection, S_RESOURCE_ID, sStatePath, dictOuter,
        )
    assert listInnerSaves, "the second saver never ran; the test proves nothing"


@pytest.mark.falsification
def testAnOverlappingSaveDoesNotBreakTheOtherOnesInstall(
    tProjectAndConnection,
):
    """The failure that reddened the browser lane on Linux CI.

    Both savers used to derive one temp name from the state path, so
    the first to rename consumed the file the second was about to
    rename. The second's ``mv`` then failed with "No such file or
    directory", the OSError travelled up through the carrier, and
    because a half-finished write poisons its journal record the whole
    project was quarantined: every later request answered 500 until
    someone reconciled it. A step edit during a run is all it took.

    Kills: deriving the temp name from the state path alone.
    """
    sStatePath, connection = tProjectAndConnection
    _fnSaveWithASecondSaverInTheWindow(
        connection, sStatePath,
        {"sWho": "step-edit"}, {"sWho": "file-poll"},
    )


@pytest.mark.falsification
def testTheOverlappingSaveLeavesAWholeReadableStateFile(
    tProjectAndConnection,
):
    """Not raising is not enough — one of the two writes must be intact.

    Temp-then-rename exists to keep a reader from ever seeing a torn
    file. Uniquifying the temp name must not cost that: the surviving
    state.json has to be exactly one saver's document, not a blend.

    Kills: computing the unique suffix once instead of per call — the
    plausible "why build a uuid on every save" edit, which is no
    uniqueness at all between two savers in one hub.
    """
    sStatePath, connection = tProjectAndConnection
    _fnSaveWithASecondSaverInTheWindow(
        connection, sStatePath,
        {"sWho": "step-edit"}, {"sWho": "file-poll"},
    )
    dictLoaded, sStatus = stateManager.ftLoadStateWithStatus(
        connection, S_RESOURCE_ID, sStatePath,
    )
    assert sStatus == "loaded", sStatus
    assert dictLoaded["sWho"] in ("step-edit", "file-poll")


def testNeitherSaverLeavesItsTemporaryFileBehind(
    tProjectAndConnection,
):
    """A per-writer temp name is never reclaimed, so it must be consumed.

    The old fixed name cleaned up after itself by accident — the next
    save overwrote it. Nothing overwrites these, so an abandoned one
    would sit in the researcher's ``.vaibify`` directory forever, and
    one per overlapping save.
    """
    sStatePath, connection = tProjectAndConnection
    _fnSaveWithASecondSaverInTheWindow(
        connection, sStatePath,
        {"sWho": "step-edit"}, {"sWho": "file-poll"},
    )
    sStateDirectory = os.path.dirname(sStatePath)
    listLeftovers = [
        sName for sName in os.listdir(sStateDirectory)
        if sName.endswith(".tmp")
    ]
    assert listLeftovers == [], listLeftovers


@pytest.mark.falsification
def testAFailedInstallDiscardsItsOwnTemporaryFile(
    tProjectAndConnection,
):
    """The one path that orphans a temp file cleans it up before raising.

    A rename can still fail for a real reason — a full disk, a
    directory replaced underneath it. The save must report THAT, and
    not leave its temp file behind on the way out.

    Kills: dropping the discard, which the fixed temp name used to make
    unnecessary because the next save simply overwrote the leftover.
    """
    sStatePath, connection = tProjectAndConnection
    listTempPaths = []
    fnRealInstall = stateManager._fnAtomicInstallTempFile

    def fnRecordThenFailTheInstall(
        connectionDocker, sContainerId, sTempPath, sTargetPath,
    ):
        listTempPaths.append(sTempPath)
        return fnRealInstall(
            connectionDocker, sContainerId, sTempPath,
            os.path.join(sTargetPath, "not-a-directory", "state.json"),
        )

    with patch.object(
        stateManager, "_fnAtomicInstallTempFile",
        fnRecordThenFailTheInstall,
    ):
        with pytest.raises(OSError):
            stateManager.fnSaveStateToContainer(
                connection, S_RESOURCE_ID, sStatePath, {"sWho": "doomed"},
            )
    assert listTempPaths
    assert not os.path.exists(listTempPaths[0]), listTempPaths[0]


# ── pipeline_state.json: the same protocol, in the other module ──────


class _ConnectionRunningASecondWriteAtTheRename:
    """Delegate everything, but run a second write inside the window.

    The pipeline-state writer has no seam between its temp write and
    its rename, so the overlap is forced from the connection: the
    first ``mv`` it is asked to perform triggers a whole second write,
    which lands its own file and renames it away before the original
    rename is allowed through.
    """

    def __init__(self, connectionReal, dictSecondState):
        self._connectionReal = connectionReal
        self._dictSecondState = dictSecondState
        self.listSecondWrites = []

    def fnWriteFile(self, *aArgs, **dictKwargs):
        return self._connectionReal.fnWriteFile(*aArgs, **dictKwargs)

    def ftResultExecuteCommand(self, sContainerId, sCommand, *aArgs):
        if sCommand.startswith("mv ") and not self.listSecondWrites:
            self.listSecondWrites.append(sCommand)
            pipelineState.fnWriteState(
                self._connectionReal, sContainerId, self._dictSecondState,
            )
        return self._connectionReal.ftResultExecuteCommand(
            sContainerId, sCommand, *aArgs,
        )


@pytest.mark.falsification
def testAnOverlappingPipelineStateWriteIsNotSilentlyDropped(
    tProjectAndConnection,
):
    """The run's writer and the reconciler both write this file.

    The document on disk must be the one whose rename ran LAST — here
    the run writer's, which reports the run finished. With one temp
    name per target its rename found nothing, and because this writer
    discarded the exit code the update vanished without a word: a
    pipeline the dashboard shows running forever, blocking the next
    run.

    Asserting merely that SOME whole document landed would have been
    satisfied by the bug, since the other writer's file was sitting
    there — the first version of this test was, and the mutant walked
    through it.

    Kills: deriving the temp name from the state path alone.
    """
    sStatePath, connection = tProjectAndConnection
    sPipelineStatePath = os.path.join(
        os.path.dirname(sStatePath), "pipeline_state.json",
    )
    connectionOverlapping = _ConnectionRunningASecondWriteAtTheRename(
        connection, {"bRunning": True, "sWho": "reconciler"},
    )
    with patch.object(
        pipelineState, "fsStatePathFor",
        lambda sResourceId: sPipelineStatePath,
    ):
        pipelineState.fnWriteState(
            connectionOverlapping, S_RESOURCE_ID,
            {"bRunning": False, "sWho": "run-writer"},
        )
    assert connectionOverlapping.listSecondWrites, (
        "the second writer never ran; the test proves nothing"
    )
    with open(sPipelineStatePath) as fileState:
        dictLanded = json.load(fileState)
    assert dictLanded["sWho"] == "run-writer", dictLanded
    assert dictLanded["bRunning"] is False


def testClearingThePipelineStateSweepsAPerWriterTemporaryFile(
    tProjectAndConnection,
):
    """The clear's wildcard is expanded by a REAL shell, or not at all.

    A double that pops the literal tokens of an ``rm`` would answer
    the same whether the pattern globs or is passed through as text,
    so this one runs the command the product composes against a real
    file on disk.
    """
    sStatePath, connection = tProjectAndConnection
    sPipelineStatePath = os.path.join(
        os.path.dirname(sStatePath), "pipeline_state.json",
    )
    sAbandonedTempPath = sPipelineStatePath + ".deadbeef.tmp"
    for sPath in (sPipelineStatePath, sAbandonedTempPath):
        with open(sPath, "w") as fileState:
            fileState.write("{}")
    with patch.object(
        pipelineState, "fsStatePathFor",
        lambda sResourceId: sPipelineStatePath,
    ):
        pipelineState.fnClearState(connection, S_RESOURCE_ID)
    assert not os.path.exists(sPipelineStatePath)
    assert not os.path.exists(sAbandonedTempPath)
