"""End-to-end coverage for hash-staleness wiring inside the poll loop.

Covers the failure mode that motivated Phase 3.4: a step's outputs are
silently restored from a ``.bak`` file via ``shutil.copy2`` (which
preserves mtime). The dashboard's mtime-based delta detection cannot
see that change, but the marker's recorded content hashes can.

These tests exercise :func:`fileStatusManager._flistDetectAndInvalidate`
with a marker + on-disk file pair whose mtimes match but whose contents
diverge. Both ``sUnitTest = "passed"`` and ``sUnitTest =
"passed-from-marker"`` initial states must invalidate to ``untested``.
"""

import os

import pytest

from vaibify.gui import mtimeCache
from vaibify.gui.fileStatusManager import _flistDetectAndInvalidate


def _fsWrite(sRoot, sRelPath, sContent):
    """Write content at sRoot/sRelPath, creating directories as needed."""
    sAbsPath = os.path.join(sRoot, *sRelPath.split("/"))
    os.makedirs(os.path.dirname(sAbsPath), exist_ok=True)
    with open(sAbsPath, "w") as handle:
        handle.write(sContent)
    return sAbsPath


def _fnTouchMtime(sAbsPath, fMtime):
    """Set both atime and mtime on a path to ``fMtime``."""
    os.utime(sAbsPath, (fMtime, fMtime))


def _fdictBuildOneStepWorkflow(sProjectRepoPath, sUnitTestState):
    """Return a one-step workflow rooted at sProjectRepoPath."""
    return {
        "sPath": "/workspace/repo/.vaibify/workflows/main.json",
        "sProjectRepoPath": sProjectRepoPath,
        "listSteps": [{
            "sLabel": "A01",
            "sDirectory": "step1",
            "saDataFiles": ["out.json"],
            "saPlotFiles": [],
            "dictVerification": {
                "sUnitTest": sUnitTestState,
                "sIntegrity": sUnitTestState,
                "sQualitative": sUnitTestState,
                "sQuantitative": sUnitTestState,
                "sUser": "untested",
            },
        }],
    }


def _fdictBuildMarker(dictHashes, sStepDirectory="step1", sLabel="A01"):
    """Return a marker dict shaped like the conftest plugin writes."""
    return {
        "sDirectory": sStepDirectory,
        "sLabel": sLabel,
        "iExitStatus": 0,
        "dictOutputHashes": dictHashes,
        "dictCategories": {
            "integrity": {"iPassed": 1, "iFailed": 0},
            "qualitative": {"iPassed": 1, "iFailed": 0},
            "quantitative": {"iPassed": 1, "iFailed": 0},
        },
    }


class _FakeDocker:
    """Minimal docker shim for pipelineState.fdictReadState calls."""

    def ftResultExecuteCommand(self, sContainerId, sCmd):
        return (1, "")

    def fbaFetchFile(self, sContainerId, sPath):
        raise FileNotFoundError(sPath)


def _fdictBuildCtx(dictPrevModTimes=None):
    """Build a dictCtx shape that satisfies the poll path's contract."""
    return {
        "docker": _FakeDocker(),
        "save": _fnNoopSave,
        "dictPreviousModTimes": dictPrevModTimes or {},
    }


def _fnNoopSave(sContainerId, dictWorkflow):
    """No-op save callable so the test exercises the in-memory state."""
    return


def _fnSeedShutilCopyScenario(tmp_path, sUnitTestState):
    """Build the workflow, marker, on-disk file, and ctx for the test."""
    fSharedMtime = 1_700_000_000.0
    sBaselineSha = _fsSeedDriftFilesAtSharedMtime(tmp_path, fSharedMtime)
    dictWorkflow = _fdictBuildOneStepWorkflow(str(tmp_path), sUnitTestState)
    dictMarker = _fdictBuildMarker({"step1/out.json": sBaselineSha})
    sAbsLive = os.path.join(str(tmp_path), "step1", "out.json")
    dictNewModTimes = {sAbsLive: str(int(fSharedMtime))}
    dictCtx = _fdictBuildCtx({"cid": dict(dictNewModTimes)})
    return dictWorkflow, dictMarker, dictNewModTimes, dictCtx


def _fsSeedDriftFilesAtSharedMtime(tmp_path, fSharedMtime):
    """Write a baseline + live file with divergent content but shared mtime."""
    sBaselinePath = _fsWrite(
        str(tmp_path), "baseline/out.json", "baseline-content",
    )
    sBaselineSha = mtimeCache.fsBlobShaForFile(
        str(tmp_path), "baseline/out.json", {},
    )
    sLivePath = _fsWrite(str(tmp_path), "step1/out.json", "drifted-content")
    _fnTouchMtime(sLivePath, fSharedMtime)
    _fnTouchMtime(sBaselinePath, fSharedMtime)
    return sBaselineSha


def _fnAssertStepInvalidated(dictWorkflow):
    """Assert step1's four axes flipped to ``untested`` after the poll."""
    dictVerify = dictWorkflow["listSteps"][0]["dictVerification"]
    assert dictVerify["sUnitTest"] == "untested"
    assert dictVerify["sIntegrity"] == "untested"
    assert dictVerify["sQualitative"] == "untested"
    assert dictVerify["sQuantitative"] == "untested"
    assert "step1/out.json" in dictVerify.get("listModifiedFiles", [])


@pytest.mark.parametrize(
    "sUnitTestState", ["passed", "passed-from-marker"],
)
def test_shutil_copy_drift_invalidates_step(tmp_path, sUnitTestState):
    dictWorkflow, dictMarker, dictNewModTimes, dictCtx = (
        _fnSeedShutilCopyScenario(tmp_path, sUnitTestState)
    )
    dictCache = {}
    _flistDetectAndInvalidate(
        dictCtx, "cid", dictWorkflow, dictNewModTimes,
        dictVars={"sRepoRoot": str(tmp_path)},
        dictMarkersByStep={0: dictMarker},
        dictCache=dictCache,
    )
    _fnAssertStepInvalidated(dictWorkflow)


def test_matching_content_does_not_invalidate(tmp_path):
    """A live file whose hash matches the marker stays passed."""
    sContent = "in-sync-content"
    sLivePath = _fsWrite(str(tmp_path), "step1/out.json", sContent)
    _fnTouchMtime(sLivePath, 1_700_000_000.0)
    sBaselineSha = mtimeCache.fsBlobShaForFile(
        str(tmp_path), "step1/out.json", {},
    )
    dictWorkflow = _fdictBuildOneStepWorkflow(
        str(tmp_path), "passed-from-marker",
    )
    dictMarker = _fdictBuildMarker({"step1/out.json": sBaselineSha})
    sAbsLive = os.path.join(str(tmp_path), "step1", "out.json")
    dictNewModTimes = {sAbsLive: "1700000000"}
    dictCtx = _fdictBuildCtx({"cid": dict(dictNewModTimes)})
    _flistDetectAndInvalidate(
        dictCtx, "cid", dictWorkflow, dictNewModTimes,
        dictVars={"sRepoRoot": str(tmp_path)},
        dictMarkersByStep={0: dictMarker},
        dictCache={},
    )
    dictVerify = dictWorkflow["listSteps"][0]["dictVerification"]
    assert dictVerify["sUnitTest"] == "passed-from-marker"


def test_marker_with_mismatched_label_is_ignored(tmp_path):
    """A marker whose sLabel diverges from the live step is treated as absent."""
    sContent = "anything"
    sLivePath = _fsWrite(str(tmp_path), "step1/out.json", sContent)
    _fnTouchMtime(sLivePath, 1_700_000_000.0)
    dictWorkflow = _fdictBuildOneStepWorkflow(
        str(tmp_path), "passed-from-marker",
    )
    dictMarker = _fdictBuildMarker(
        {"step1/out.json": "0" * 40}, sLabel="A99",
    )
    sAbsLive = os.path.join(str(tmp_path), "step1", "out.json")
    dictNewModTimes = {sAbsLive: "1700000000"}
    dictCtx = _fdictBuildCtx({"cid": dict(dictNewModTimes)})
    _flistDetectAndInvalidate(
        dictCtx, "cid", dictWorkflow, dictNewModTimes,
        dictVars={"sRepoRoot": str(tmp_path)},
        dictMarkersByStep={0: dictMarker},
        dictCache={},
    )
    dictVerify = dictWorkflow["listSteps"][0]["dictVerification"]
    assert dictVerify["sUnitTest"] == "passed-from-marker"


def test_marker_without_hashes_is_no_op(tmp_path):
    """A marker carrying no dictOutputHashes leaves the step untouched."""
    sContent = "anything"
    sLivePath = _fsWrite(str(tmp_path), "step1/out.json", sContent)
    _fnTouchMtime(sLivePath, 1_700_000_000.0)
    dictWorkflow = _fdictBuildOneStepWorkflow(str(tmp_path), "passed")
    dictMarker = _fdictBuildMarker({})
    sAbsLive = os.path.join(str(tmp_path), "step1", "out.json")
    dictNewModTimes = {sAbsLive: "1700000000"}
    dictCtx = _fdictBuildCtx({"cid": dict(dictNewModTimes)})
    _flistDetectAndInvalidate(
        dictCtx, "cid", dictWorkflow, dictNewModTimes,
        dictVars={"sRepoRoot": str(tmp_path)},
        dictMarkersByStep={0: dictMarker},
        dictCache={},
    )
    dictVerify = dictWorkflow["listSteps"][0]["dictVerification"]
    assert dictVerify["sUnitTest"] == "passed"


def test_guard_fix_passed_from_marker_invalidates_on_mtime_change(tmp_path):
    """The guard-fix half of PR 1: marker-bootstrapped steps must demote."""
    sLivePath = _fsWrite(str(tmp_path), "step1/out.json", "v1")
    _fnTouchMtime(sLivePath, 1_700_000_000.0)
    dictWorkflow = _fdictBuildOneStepWorkflow(
        str(tmp_path), "passed-from-marker",
    )
    sAbsLive = os.path.join(str(tmp_path), "step1", "out.json")
    dictOldModTimes = {sAbsLive: "1690000000"}
    dictNewModTimes = {sAbsLive: "1700000000"}
    dictCtx = _fdictBuildCtx({"cid": dict(dictOldModTimes)})
    _flistDetectAndInvalidate(
        dictCtx, "cid", dictWorkflow, dictNewModTimes,
        dictVars={"sRepoRoot": str(tmp_path)},
        dictMarkersByStep={},
        dictCache={},
    )
    dictVerify = dictWorkflow["listSteps"][0]["dictVerification"]
    assert dictVerify["sUnitTest"] == "untested"
