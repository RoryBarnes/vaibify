"""Provenance commits during the run, as record units (spec §4.5).

Three guarantees, each with the failure it prevents:

- The refresh-and-commit runs on EVERY exit of a step declaring
  remote data. The old hook fired only on exit 0, so a step whose
  download succeeded and whose later command failed recorded nothing
  — and a successful-step test passes under both designs, which is
  why the failing-step case is asserted by name here.
- The record is the conflict unit. Digest and timestamp install
  together into a record whose declared fields still match the
  declaration the run pulled under; a record edited or removed
  mid-run is refused, never guessed at — installing the run's hash
  under a researcher's new ``sSourceUrl`` would manufacture an
  internally-consistent false record with no symptom.
- The commit targets the CURRENT document through the session save
  seam, so a researcher's unrelated mid-run edit survives and the
  run's own write moves the freshness baseline with the file.
"""

import asyncio
import copy
import hashlib
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vaibify.gui.pipelineRunner import _fiExecuteAndRecord
from vaibify.gui.provenanceCommitter import (
    fdictCommitRemoteDataRecords,
)

_S_SHA_FRESH = "c" * 64
_S_STAMP_FRESH = "2026-08-15T01:00:00Z"
_S_CONTAINER = "cid"
_S_PROJECT_PATH = "/workspace/repo/project.json"


def _fdictDiskWorkflow(sSourceUrl="https://archive.example/query"):
    return {
        "sPlotDirectory": "plots",
        "listSteps": [{
            "sStepId": "pull-archive",
            "sName": "Pull Archive",
            "sDirectory": "PullArchive",
            "saPlotCommands": [],
            "saPlotFiles": [],
            "listRemoteData": [{
                "sPath": "data/pull.fits",
                "sSourceUrl": sSourceUrl,
            }],
        }],
    }


def _fdictRunRecord():
    return {
        "sPath": "data/pull.fits",
        "sSourceUrl": "https://archive.example/query",
        "sSha256": _S_SHA_FRESH,
        "sDigestBecameCurrentUtc": _S_STAMP_FRESH,
    }


class _FakeContextDocker:
    """Serves project.json bytes; nothing else is reachable."""

    def __init__(self, baProjectBytes):
        self.baProjectBytes = baProjectBytes

    def fbaFetchFile(self, _sContainerId, sPath):
        if sPath == _S_PROJECT_PATH:
            return self.baProjectBytes
        raise FileNotFoundError(sPath)


def _ftBuildContext(dictDiskWorkflow):
    """Return ``(dictCtx, listSaves)`` with cache current on disk.

    The cache is a distinct object from the disk fixture on purpose
    (name != id discipline): the committer must merge into the cache
    OBJECT the session holds, and a test sharing one dict for both
    could not tell.
    """
    baDiskBytes = json.dumps(dictDiskWorkflow).encode("utf-8")
    dictCache = copy.deepcopy(dictDiskWorkflow)
    dictCache["_sSourceFingerprint"] = hashlib.sha256(
        baDiskBytes,
    ).hexdigest()
    listSaves = []

    def fnRecordSave(sContainerId, dictWorkflow):
        listSaves.append((sContainerId, dictWorkflow))

    dictCtx = {
        "docker": _FakeContextDocker(baDiskBytes),
        "paths": {_S_CONTAINER: _S_PROJECT_PATH},
        "workflows": {_S_CONTAINER: dictCache},
        "save": fnRecordSave,
    }
    return dictCtx, listSaves


# ---------------------------------------------------------------------------
# The named regression: pull succeeds, a later command in the step fails
# ---------------------------------------------------------------------------


@pytest.mark.falsification
def testAFailingStepStillRecordsItsPull():
    """Kills: refreshing and committing provenance on a non-zero exit.

    The pulled file is on disk whether or not a later command in the
    same step failed; provenance describes the files. Guarding the
    hook on exit 0 is the "no pull boundary" hole — asserted here
    with the failing step, because a successful-step test passes
    under both the correct design and the broken one.
    """
    listCommitCalls = []

    def fdictCommitStub(sStepId, listRecords):
        listCommitCalls.append((sStepId, listRecords))
        return {
            "bCommitted": True, "sDetail": "",
            "iInstalled": 1, "listRefusals": [],
        }

    listEvents = []

    async def fnCallback(dictEvent):
        listEvents.append(dictEvent)

    dictStep = {
        "sStepId": "pull-archive",
        "sDirectory": "/ws/step",
        "listRemoteData": [{
            "sPath": "data/pull.fits",
            "sSourceUrl": "https://archive.example/query",
        }],
    }
    mockDocker = MagicMock()
    mockDocker.ftResultExecuteCommand.return_value = (
        0, f"{_S_SHA_FRESH}  /workspace/repo/data/pull.fits",
    )
    with patch(
        "vaibify.gui.pipelineRunner.ftRunStepCommands",
        new=AsyncMock(return_value=(1, 1.0)),
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
            mockDocker, _S_CONTAINER, dictStep,
            1, "/ws", {"sRepoRoot": "/workspace/repo"}, fnCallback,
            fdictCommitProvenance=fdictCommitStub,
        ))
    assert iReturned == 1
    assert dictStep["listRemoteData"][0]["sSha256"] == _S_SHA_FRESH, (
        "the failing step's pull was not hashed; the no-pull-boundary "
        "hole is back"
    )
    assert listCommitCalls and listCommitCalls[0][0] == "pull-archive", (
        "the failing step's records were never committed"
    )


# ---------------------------------------------------------------------------
# Record-unit merge against the current document
# ---------------------------------------------------------------------------


def testAFreshDigestCommitsThroughTheSessionSaveSeam():
    dictCtx, listSaves = _ftBuildContext(_fdictDiskWorkflow())
    dictOutcome = fdictCommitRemoteDataRecords(
        dictCtx, _S_CONTAINER, "pull-archive", [_fdictRunRecord()],
    )
    assert dictOutcome["bCommitted"] is True
    assert dictOutcome["iInstalled"] == 1
    assert dictOutcome["listRefusals"] == []
    assert len(listSaves) == 1, (
        "the commit must persist through the session save seam, which "
        "moves the self-write baseline with the file"
    )
    sContainerSaved, dictSaved = listSaves[0]
    assert sContainerSaved == _S_CONTAINER
    assert dictSaved is dictCtx["workflows"][_S_CONTAINER], (
        "the commit must save the live cache object, not a copy the "
        "session never sees"
    )


@pytest.mark.falsification
def testDigestAndTimestampInstallTogether():
    """Kills: installing the record as one assertion, never leafwise.

    A digest without its timestamp (or the reverse) is half an
    assertion; merging leafwise is how a false record gets
    manufactured piecemeal.
    """
    dictCtx, _listSaves = _ftBuildContext(_fdictDiskWorkflow())
    fdictCommitRemoteDataRecords(
        dictCtx, _S_CONTAINER, "pull-archive", [_fdictRunRecord()],
    )
    dictRecord = dictCtx["workflows"][_S_CONTAINER]["listSteps"][0][
        "listRemoteData"
    ][0]
    assert dictRecord.get("sSha256") == _S_SHA_FRESH
    assert dictRecord.get("sDigestBecameCurrentUtc") == _S_STAMP_FRESH, (
        "the digest installed without its timestamp; the record is "
        "the conflict unit and must move as one assertion"
    )


@pytest.mark.falsification
def testAMidRunDeclarationEditRefusesTheRecord():
    """Kills: refusing a record whose declaration moved mid-run.

    The run's digest was pulled under the dispatch-time declaration.
    Installing it under the researcher's new ``sSourceUrl`` would
    manufacture an internally-consistent false record — the exact
    §4.5 failure. Refused per record, never auto-retried.
    """
    dictCtx, listSaves = _ftBuildContext(
        _fdictDiskWorkflow(sSourceUrl="https://elsewhere.example/new"),
    )
    dictOutcome = fdictCommitRemoteDataRecords(
        dictCtx, _S_CONTAINER, "pull-archive", [_fdictRunRecord()],
    )
    assert dictOutcome["iInstalled"] == 0
    assert len(dictOutcome["listRefusals"]) == 1
    assert "declaration changed" in (
        dictOutcome["listRefusals"][0]["sReason"]
    )
    dictRecord = dictCtx["workflows"][_S_CONTAINER]["listSteps"][0][
        "listRemoteData"
    ][0]
    assert "sSha256" not in dictRecord, (
        "the pulled digest was installed under a declaration the run "
        "never pulled under — a manufactured record"
    )
    assert listSaves == [], "a refused-only merge must not save"


def testARemovedRecordIsRefusedNotResurrected():
    dictDisk = _fdictDiskWorkflow()
    dictDisk["listSteps"][0]["listRemoteData"] = []
    dictCtx, listSaves = _ftBuildContext(dictDisk)
    dictOutcome = fdictCommitRemoteDataRecords(
        dictCtx, _S_CONTAINER, "pull-archive", [_fdictRunRecord()],
    )
    assert dictOutcome["iInstalled"] == 0
    assert len(dictOutcome["listRefusals"]) == 1
    assert "removed" in dictOutcome["listRefusals"][0]["sReason"]
    assert dictCtx["workflows"][_S_CONTAINER]["listSteps"][0][
        "listRemoteData"
    ] == [], "the refused record was resurrected into the document"
    assert listSaves == []


def testARemovedStepRefusesEverything():
    dictDisk = _fdictDiskWorkflow()
    dictDisk["listSteps"] = []
    dictCtx, listSaves = _ftBuildContext(dictDisk)
    dictOutcome = fdictCommitRemoteDataRecords(
        dictCtx, _S_CONTAINER, "pull-archive", [_fdictRunRecord()],
    )
    assert dictOutcome["bCommitted"] is False
    assert "no longer in the project" in dictOutcome["sDetail"]
    assert listSaves == []


def testAnUnrelatedMidRunEditSurvivesTheCommit():
    """The commit merges into the CURRENT document, never a snapshot.

    Models a mid-run out-of-band edit: the disk moved past the cache,
    the reload accepts it (stubbed at the reload seam — the detector
    has its own tests), and the digests land in the accepted document
    with the researcher's edit intact.
    """
    dictEditedDisk = _fdictDiskWorkflow()
    dictEditedDisk["listSteps"][0]["sNotes"] = "edited mid-run"
    baEditedBytes = json.dumps(dictEditedDisk).encode("utf-8")
    sEditedFingerprint = hashlib.sha256(baEditedBytes).hexdigest()

    dictCtx, listSaves = _ftBuildContext(_fdictDiskWorkflow())
    dictCtx["docker"] = _FakeContextDocker(baEditedBytes)
    dictStaleCache = dictCtx["workflows"][_S_CONTAINER]

    def fdictAcceptReload(
        dictCtxSeen, sContainerId, _sPath, _dictModTimes,
        sPolledFingerprint="",
    ):
        dictAccepted = copy.deepcopy(dictEditedDisk)
        dictAccepted["_sSourceFingerprint"] = sPolledFingerprint
        dictCtxSeen["workflows"][sContainerId] = dictAccepted
        return {"bReplaced": True, "dictWorkflow": dictAccepted}

    with patch(
        "vaibify.gui.workflowReloadDetector.fdictMaybeReloadWorkflow",
        new=fdictAcceptReload,
    ):
        dictOutcome = fdictCommitRemoteDataRecords(
            dictCtx, _S_CONTAINER, "pull-archive", [_fdictRunRecord()],
        )
    assert dictOutcome["bCommitted"] is True
    assert dictOutcome["iInstalled"] == 1
    assert len(listSaves) == 1
    dictSaved = listSaves[0][1]
    assert dictSaved is not dictStaleCache, (
        "the commit merged into the stale pre-edit cache; the mid-run "
        "edit would be overwritten on save"
    )
    assert dictSaved["listSteps"][0]["sNotes"] == "edited mid-run"
    assert dictSaved["listSteps"][0]["listRemoteData"][0][
        "sSha256"
    ] == _S_SHA_FRESH
    assert dictSaved["_sSourceFingerprint"] == sEditedFingerprint


def testAnUnreadableDocumentRefusesInsteadOfRaising():
    dictCtx, listSaves = _ftBuildContext(_fdictDiskWorkflow())
    dictCtx["paths"][_S_CONTAINER] = "/workspace/repo/missing.json"
    dictOutcome = fdictCommitRemoteDataRecords(
        dictCtx, _S_CONTAINER, "pull-archive", [_fdictRunRecord()],
    )
    assert dictOutcome["bCommitted"] is False
    assert "could not be read" in dictOutcome["sDetail"]
    assert listSaves == []


def testASaveFailureRefusesInsteadOfRaising():
    dictCtx, _listSaves = _ftBuildContext(_fdictDiskWorkflow())

    def fnFailingSave(_sContainerId, _dictWorkflow):
        raise OSError("disk full")

    dictCtx["save"] = fnFailingSave
    dictOutcome = fdictCommitRemoteDataRecords(
        dictCtx, _S_CONTAINER, "pull-archive", [_fdictRunRecord()],
    )
    assert dictOutcome["bCommitted"] is False
    assert "disk full" in dictOutcome["sDetail"]
