"""stateManager edges, driven through a REAL HostConnection on tmp_path.

The state document is written, checkpointed and renamed by real shell
commands on real files. Where a failure must be forced (a rename or a
checkpoint copy that the shell refuses), a thin wrapper answers that
one command and delegates everything else to the real connection.
"""

import json
import logging
import os

import pytest

from vaibify.gui import workflowManager
from vaibify.config import containerLock, operationJournal
from vaibify.gui import stateManager
from vaibify.gui.fileStatusManager import (
    fsMarkerNameFromStepDirectory,
    fsWorkflowSlugFromPath,
)
from vaibify.host import hostScratch
from vaibify.host.hostConnection import HostConnection


S_RESOURCE_ID = "stateProjectAlpha"
S_WORKFLOW_KEY = ".vaibify/projects/alpha.json"


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
def tRepoAndConnection(tmp_path):
    """Return (sRepoPath, sStatePath, HostConnection) over a temp repo."""
    sRepoPath = os.path.realpath(str(tmp_path)) + "/repository"
    os.makedirs(os.path.join(sRepoPath, ".vaibify"))
    connectionHost = HostConnection(
        fnResolveProjectRoot=lambda sResourceId: sRepoPath,
    )
    return sRepoPath, stateManager.fsStatePathFromRepo(sRepoPath), connectionHost


class ConnectionFailingCommands:
    """Delegate to a real connection, failing commands with a given prefix."""

    def __init__(self, connectionReal, sFailingPrefix, exceptionRaised=None):
        self._connectionReal = connectionReal
        self._sFailingPrefix = sFailingPrefix
        self._exceptionRaised = exceptionRaised
        self.listRefused = []

    def __getattr__(self, sName):
        return getattr(self._connectionReal, sName)

    def ftResultExecuteCommand(self, sContainerId, sCommand):
        if sCommand.startswith(self._sFailingPrefix):
            self.listRefused.append(sCommand)
            if self._exceptionRaised:
                raise self._exceptionRaised
            return (1, "refused by test")
        return self._connectionReal.ftResultExecuteCommand(
            sContainerId, sCommand,
        )


def testPathAndKeyHelpersRefuseMissingOrForeignInputs():
    """Empty inputs and a workflow outside its repo attribute to nothing."""
    assert stateManager.fsGitignorePathFromRepo("") == ""
    assert stateManager.fsWorkflowKeyFromPath("", "/repository") == ""
    assert stateManager.fsWorkflowKeyFromPath(
        "/elsewhere/project.json", "/repository",
    ) == ""
    assert stateManager.fsWorkflowKeyFromPath(
        "/repository/.vaibify/projects/alpha.json", "/repository/",
    ) == S_WORKFLOW_KEY


def testSectionLookupToleratesMalformedSectionMap():
    """A non-dict section map or section reads as absent, not as a crash."""
    assert stateManager.fdictSectionForWorkflow(
        {stateManager.S_WORKFLOW_STATE_KEY: ["unexpected"]}, S_WORKFLOW_KEY,
    ) is None
    assert stateManager.fdictSectionForWorkflow(
        {stateManager.S_WORKFLOW_STATE_KEY: {S_WORKFLOW_KEY: "text"}},
        S_WORKFLOW_KEY,
    ) is None


def testSingleSlotQuarantineIsCarriedInAsFirstRecord():
    """The replaced single-slot shape survives beside the new record."""
    dictOldSlot = {"dictStepState": {"stepAlpha": {}}}
    dictDocument = {stateManager.S_QUARANTINE_LIST_KEY: dictOldSlot}
    stateManager.fnAppendQuarantineRecord(
        dictDocument, {"dictStepState": {"stepBeta": {}}},
    )
    listRecords = dictDocument[stateManager.S_QUARANTINE_LIST_KEY]
    assert listRecords[0] is dictOldSlot
    assert listRecords[1]["dictStepState"] == {"stepBeta": {}}
    assert "sQuarantinedUtc" in listRecords[1]


def testMigrationPassesNonDocumentsThrough():
    """A non-dict loaded value is returned unchanged, not migrated."""
    assert stateManager.fdictMigrateStateDocument(None, ["key"]) is None
    assert stateManager.fdictMigrateStateDocument([1, 2], None) == [1, 2]


def testLoadAndSaveWithoutAStatePathTouchNothing(tRepoAndConnection):
    """No repo means an empty loaded state and a save that writes nothing."""
    sRepoPath, sStatePath, connectionHost = tRepoAndConnection
    dictState, sStatus = stateManager.ftLoadStateWithStatus(
        connectionHost, S_RESOURCE_ID, "",
    )
    assert sStatus == "loaded"
    assert dictState["dictStepState"] == {}
    stateManager.fnSaveStateToContainer(
        connectionHost, S_RESOURCE_ID, "", {"dictStepState": {}},
    )
    assert not os.path.exists(sStatePath)


def testCorruptStateWhoseQuarantineRenameFailsStillBootstraps(
    tRepoAndConnection, caplog,
):
    """A refused quarantine rename is logged; the load still reports corrupt."""
    _, sStatePath, connectionHost = tRepoAndConnection
    with open(sStatePath, "w") as fileHandle:
        fileHandle.write("{ not json")
    connectionFailing = ConnectionFailingCommands(connectionHost, "mv ")
    with caplog.at_level(logging.WARNING):
        dictState, sStatus = stateManager.ftLoadStateWithStatus(
            connectionFailing, S_RESOURCE_ID, sStatePath,
        )
    assert sStatus == "corrupted"
    assert connectionFailing.listRefused
    assert any("exited 1" in recordLog.getMessage() for recordLog in caplog.records)
    with open(sStatePath) as fileHandle:
        assert fileHandle.read() == "{ not json"


@pytest.mark.parametrize(
    "exceptionRaised,sLogFragment",
    [(None, "checkpoint copy exited 1"), (OSError("shell gone"), "shell gone")],
)
def testFailedCheckpointIsLoggedAndTheSaveStillLands(
    tRepoAndConnection, caplog, exceptionRaised, sLogFragment,
):
    """The checkpoint is best-effort; the primary write proceeds."""
    _, sStatePath, connectionHost = tRepoAndConnection
    with open(sStatePath, "w") as fileHandle:
        json.dump({"iStateSchemaVersion": 3}, fileHandle)
    connectionFailing = ConnectionFailingCommands(
        connectionHost, "if [ -f ", exceptionRaised,
    )
    dictSection = stateManager.fdictBuildEmptyState()
    dictSection["dictStepState"] = {"stepAlpha": {"dictRunStats": {"i": 1}}}
    with caplog.at_level(logging.WARNING):
        stateManager.fnSaveStateToContainer(
            connectionFailing, S_RESOURCE_ID, sStatePath, dictSection,
            sWorkflowKey=S_WORKFLOW_KEY,
        )
    assert any(sLogFragment in recordLog.getMessage() for recordLog in caplog.records)
    with open(sStatePath) as fileHandle:
        dictSaved = json.load(fileHandle)
    assert dictSaved[stateManager.S_WORKFLOW_STATE_KEY][S_WORKFLOW_KEY][
        "dictStepState"
    ] == {"stepAlpha": {"dictRunStats": {"i": 1}}}
    assert not os.path.exists(sStatePath + ".bak")


@pytest.mark.parametrize(
    "sStatePath,sWorkflowKey,sExpectedFragment",
    [
        ("", S_WORKFLOW_KEY, "no project repo"),
        ("/repository/.vaibify/state.json", "", "cannot attribute run results"),
    ],
)
def testRunResultMergeRefusesWithoutAHomeOrAKey(
    sStatePath, sWorkflowKey, sExpectedFragment,
):
    """A merge with no durable home or attribution is refused by name."""
    dictOutcome = stateManager.fdictMergeRunResultsIntoState(
        None, S_RESOURCE_ID, sStatePath, sWorkflowKey, {"stepAlpha": {}}, {},
    )
    assert dictOutcome["bPersisted"] is False
    assert sExpectedFragment in dictOutcome["sDetail"]


def testStateAtRepositoryRootNeedsNoDirectoryCreation(tmp_path):
    """A bare file name has no directory to create, and the save lands."""
    sRoot = os.path.realpath(str(tmp_path))
    connectionHost = HostConnection(fnResolveProjectRoot=lambda s: sRoot)
    listCommands = []
    connectionRecording = ConnectionFailingCommands(connectionHost, "\0never")
    fnRealExecute = connectionRecording.ftResultExecuteCommand

    def ftRecordThenExecute(sContainerId, sCommand):
        listCommands.append(sCommand)
        return fnRealExecute(sContainerId, sCommand)

    connectionRecording.ftResultExecuteCommand = ftRecordThenExecute
    stateManager._fnEnsureStateDirectoryExists(
        connectionRecording, S_RESOURCE_ID, "state.json",
    )
    assert listCommands == []


def testMarkerPublishFailureIsReportedNotRaised(tRepoAndConnection):
    """An unreadable state file refuses the pull with the error named."""
    _, sStatePath, connectionHost = tRepoAndConnection
    connectionFailing = ConnectionFailingCommands(
        connectionHost, "mv -f ", OSError("rename impossible"),
    )
    dictOutcome = stateManager.fdictPublishRemoteDataMarker(
        connectionFailing, S_RESOURCE_ID, sStatePath, S_WORKFLOW_KEY,
        "stepAlpha", ["data/file.csv"],
    )
    assert dictOutcome["bPublished"] is False
    assert "pull marker publish failed: rename impossible" in (
        dictOutcome["sDetail"]
    )


def testMarkerClearRefusesIncompleteHomeAndReportsFailures(
    tRepoAndConnection,
):
    """An incomplete home is refused; a failed persist leaves the marker."""
    _, sStatePath, connectionHost = tRepoAndConnection
    assert stateManager.fdictClearRemoteDataMarker(
        connectionHost, S_RESOURCE_ID, sStatePath, S_WORKFLOW_KEY, "",
    ) == {"bCleared": False, "sDetail": "marker home incomplete"}
    assert stateManager.fdictPublishRemoteDataMarker(
        connectionHost, S_RESOURCE_ID, sStatePath, S_WORKFLOW_KEY,
        "stepAlpha", ["data/file.csv"],
    )["bPublished"] is True
    connectionFailing = ConnectionFailingCommands(
        connectionHost, "mv -f ", OSError("rename impossible"),
    )
    dictOutcome = stateManager.fdictClearRemoteDataMarker(
        connectionFailing, S_RESOURCE_ID, sStatePath, S_WORKFLOW_KEY,
        "stepAlpha",
    )
    assert dictOutcome["bCleared"] is False
    assert "rename impossible" in dictOutcome["sDetail"]
    dictDocument, _ = stateManager.ftLoadStateWithStatus(
        connectionHost, S_RESOURCE_ID, sStatePath,
    )
    assert stateManager.fdictReadRemoteDataMarker(
        dictDocument, S_WORKFLOW_KEY, "stepAlpha",
    ) is not None


def testRatchetSkipsNonDictStepsAndStampsAttainedOnly():
    """A malformed step entry is skipped; only 'attained' stamps."""
    dictWorkflow = {"listSteps": ["notAStep", {"sName": "stepBeta"}]}
    bChanged = stateManager.fbRatchetLevelHighWater(
        dictWorkflow,
        {1: {"s1": {"sState": "attained"}, "s2": "partial"}},
        {"s3": "attained"},
    )
    assert bChanged is True
    assert list(dictWorkflow["listSteps"][1]["dictLevelHighWater"]) == ["1"]
    assert list(dictWorkflow["dictWorkflowLevelHighWater"]) == ["3"]


def testGitignoreIsRefreshedOnlyWhileAutoManaged(tRepoAndConnection):
    """A stale auto-managed file is rewritten; an adopted one is left alone."""
    sRepoPath, _, connectionHost = tRepoAndConnection
    stateManager.fnEnsureVaibifyGitignore(connectionHost, S_RESOURCE_ID, "")
    sPath = stateManager.fsGitignorePathFromRepo(sRepoPath)
    assert not os.path.exists(sPath)
    with open(sPath, "w") as fileHandle:
        fileHandle.write("# Auto-managed by vaibify.\nstale.txt\n")
    stateManager.fnEnsureVaibifyGitignore(connectionHost, S_RESOURCE_ID, sRepoPath)
    with open(sPath) as fileHandle:
        assert fileHandle.read() == stateManager.S_VAIBIFY_GITIGNORE_BODY
    with open(sPath, "w") as fileHandle:
        fileHandle.write("myOwnRules\n")
    stateManager.fnEnsureVaibifyGitignore(connectionHost, S_RESOURCE_ID, sRepoPath)
    with open(sPath) as fileHandle:
        assert fileHandle.read() == "myOwnRules\n"


def fnWriteMarker(sRepoPath, sWorkflowPath, sDirectory, sContent):
    """Write one test-marker file where the bootstrap will look for it."""
    sMarkerDirectory = os.path.join(
        sRepoPath, ".vaibify", "test_markers",
        fsWorkflowSlugFromPath(sWorkflowPath),
    )
    os.makedirs(sMarkerDirectory, exist_ok=True)
    with open(os.path.join(
        sMarkerDirectory, fsMarkerNameFromStepDirectory(sDirectory),
    ), "w") as fileHandle:
        fileHandle.write(sContent)


def testBootstrapSkipsUnusableStepsAndMarkers(tRepoAndConnection):
    """No directory, a malformed marker, and a missing marker yield no state."""
    sRepoPath, _, connectionHost = tRepoAndConnection
    sWorkflowPath = sRepoPath + "/.vaibify/projects/alpha.json"
    fnWriteMarker(sRepoPath, sWorkflowPath, "stepBroken", "{ not json")
    dictWorkflow = {
        workflowManager.S_LOADED_FROM_KEY: sWorkflowPath,
        "listSteps": [
            {"sStepId": "idNoDirectory", "sDirectory": ""},
            {"sStepId": "idBroken", "sDirectory": "stepBroken"},
            {"sStepId": "idAbsent", "sDirectory": "stepAbsent"},
        ],
    }
    dictState = stateManager.fdictBootstrapStateFromMarkers(
        connectionHost, S_RESOURCE_ID, dictWorkflow, sRepoPath,
    )
    assert dictState["dictStepState"] == {}


def testBootstrapHashesSharedOutputsOnceAndKeysByStepId(
    tRepoAndConnection, monkeypatch,
):
    """Two markers naming one output hash it once; state is keyed by id."""
    sRepoPath, _, connectionHost = tRepoAndConnection
    sWorkflowPath = sRepoPath + "/.vaibify/projects/alpha.json"
    sMarker = json.dumps({"dictOutputHashes": {"shared/out.csv": "0" * 40}})
    fnWriteMarker(sRepoPath, sWorkflowPath, "stepAlpha", sMarker)
    fnWriteMarker(sRepoPath, sWorkflowPath, "stepBeta", sMarker)
    listHashed = []
    fnRealHash = stateManager._fdictHashOnDiskOutputs

    def fdictRecordHashes(connection, sContainerId, listPaths, sRepo):
        listHashed.append(list(listPaths))
        return fnRealHash(connection, sContainerId, listPaths, sRepo)

    monkeypatch.setattr(
        stateManager, "_fdictHashOnDiskOutputs", fdictRecordHashes,
    )
    dictWorkflow = {
        workflowManager.S_LOADED_FROM_KEY: sWorkflowPath,
        "listSteps": [
            {"sStepId": "idAlpha", "sDirectory": "stepAlpha"},
            {"sStepId": "idBeta", "sDirectory": "stepBeta"},
        ],
    }
    dictState = stateManager.fdictBootstrapStateFromMarkers(
        connectionHost, S_RESOURCE_ID, dictWorkflow, sRepoPath,
    )
    assert listHashed == [["shared/out.csv"]]
    assert sorted(dictState["dictStepState"]) == ["idAlpha", "idBeta"]
