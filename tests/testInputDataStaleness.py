"""Input-data staleness: declared raw inputs invalidate steps on change.

Falsification-minded coverage for the input lane of the staleness
pipeline: full-resolved-path matching (no basename shortcut), shared
inputs invalidating every declaring step and only those, sticky
``unnecessary`` categories, marker ``dictInputHashes`` drift with
matching mtimes, fresh-clone protection, and the pencil-stale
``inputFile`` rows.
"""

import os

import pytest

from vaibify.gui import mtimeCache
from vaibify.gui.fileStatusManager import (
    _fbStepIsPencilStale,
    _fdictBuildStepStatusEntry,
    _fdictComputeMaxInputMtimeByStep,
    _flistDetectAndInvalidate,
    _fnInvalidateStepFiles,
    fdictCollectInputPathsByStep,
)


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


def _fdictBuildStep(sDirectory, saInputDataFiles, sLabel="A01"):
    """Return a step with passing tests declaring the given inputs."""
    return {
        "sLabel": sLabel,
        "sDirectory": sDirectory,
        "saOutputDataFiles": [],
        "saPlotFiles": [],
        "saInputDataFiles": list(saInputDataFiles),
        "dictVerification": {
            "sUnitTest": "passed",
            "sIntegrity": "passed",
            "sQualitative": "passed",
            "sQuantitative": "passed",
            "sUser": "untested",
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
        "save": lambda sContainerId, dictWorkflow: None,
        "dictPreviousModTimes": dictPrevModTimes or {},
    }


# ---------------------------------------------------------------
# Path collection
# ---------------------------------------------------------------


def test_collect_input_paths_resolve_against_repo_root():
    dictWorkflow = {
        "sProjectRepoPath": "/workspace/repo",
        "listSteps": [
            _fdictBuildStep("posteriors", ["data/observations.csv"]),
        ],
    }
    dictByStep = fdictCollectInputPathsByStep(
        dictWorkflow, {"sRepoRoot": "/workspace/repo"},
    )
    assert dictByStep == {0: ["/workspace/repo/data/observations.csv"]}


def test_collect_input_paths_never_join_the_step_directory():
    """An input joined onto sDirectory would stat the wrong file."""
    dictWorkflow = {
        "listSteps": [
            _fdictBuildStep("posteriors", ["data/observations.csv"]),
        ],
    }
    dictByStep = fdictCollectInputPathsByStep(
        dictWorkflow, {"sRepoRoot": "/workspace/repo"},
    )
    for sPath in dictByStep[0]:
        assert "posteriors" not in sPath


def test_collect_input_paths_skip_unresolved_templates_and_no_root():
    dictWorkflow = {
        "listSteps": [
            _fdictBuildStep("s1", ["raw_{sTargetName}.csv", "raw.csv"]),
        ],
    }
    assert fdictCollectInputPathsByStep(dictWorkflow, {}) == {0: []}
    dictByStep = fdictCollectInputPathsByStep(
        dictWorkflow, {"sRepoRoot": "/workspace/repo"},
    )
    assert dictByStep == {0: ["/workspace/repo/raw.csv"]}


def test_compute_max_input_mtime_by_step():
    dictWorkflow = {
        "listSteps": [
            _fdictBuildStep("s1", ["data/a.csv", "data/b.csv"]),
            _fdictBuildStep("s2", [], sLabel="A02"),
        ],
    }
    dictModTimes = {
        "/workspace/repo/data/a.csv": "100",
        "/workspace/repo/data/b.csv": "250",
    }
    dictResult = _fdictComputeMaxInputMtimeByStep(
        dictWorkflow, dictModTimes, {"sRepoRoot": "/workspace/repo"},
    )
    assert dictResult == {"0": "250"}


# ---------------------------------------------------------------
# Invalidation via the mtime lane
# ---------------------------------------------------------------


def _fdictOneStepWorkflow(sRepoRoot, listInputs):
    return {
        "sProjectRepoPath": sRepoRoot,
        "listSteps": [_fdictBuildStep("step1", listInputs)],
    }


def test_input_mtime_change_invalidates_declaring_step(tmp_path):
    sRepoRoot = str(tmp_path)
    _fsWrite(sRepoRoot, "data/raw.csv", "v1")
    sAbsInput = os.path.join(sRepoRoot, "data", "raw.csv")
    dictWorkflow = _fdictOneStepWorkflow(sRepoRoot, ["data/raw.csv"])
    dictCtx = _fdictBuildCtx({"cid": {sAbsInput: "100"}})
    _flistDetectAndInvalidate(
        dictCtx, "cid", dictWorkflow, {sAbsInput: "200"},
        dictVars={"sRepoRoot": sRepoRoot},
    )
    dictVerify = dictWorkflow["listSteps"][0]["dictVerification"]
    assert dictVerify["sUnitTest"] == "untested"
    assert "data/raw.csv" in dictVerify["listModifiedFiles"]


def test_shared_input_invalidates_all_declaring_steps_and_only_those(
    tmp_path,
):
    sRepoRoot = str(tmp_path)
    sAbsShared = os.path.join(sRepoRoot, "data", "shared.csv")
    dictWorkflow = {
        "sProjectRepoPath": sRepoRoot,
        "listSteps": [
            _fdictBuildStep("s1", ["data/shared.csv"]),
            _fdictBuildStep("s2", ["data/shared.csv"], sLabel="A02"),
            _fdictBuildStep("s3", [], sLabel="A03"),
        ],
    }
    dictCtx = _fdictBuildCtx({"cid": {sAbsShared: "100"}})
    _flistDetectAndInvalidate(
        dictCtx, "cid", dictWorkflow, {sAbsShared: "200"},
        dictVars={"sRepoRoot": sRepoRoot},
    )
    listStates = [
        dictStep["dictVerification"]["sUnitTest"]
        for dictStep in dictWorkflow["listSteps"]
    ]
    assert listStates == ["untested", "untested", "passed"]


def test_basename_collision_in_other_directory_does_not_invalidate():
    """Only the declared full path counts — kills a basename matcher."""
    dictStep = _fdictBuildStep("step1", ["data/raw.csv"])
    _fnInvalidateStepFiles(
        dictStep, ["/workspace/repo/other/raw.csv"],
        sRepoRoot="/workspace/repo",
    )
    assert dictStep["dictVerification"]["sUnitTest"] == "passed"


def test_exact_input_path_change_invalidates():
    dictStep = _fdictBuildStep("step1", ["data/raw.csv"])
    _fnInvalidateStepFiles(
        dictStep, ["/workspace/repo/data/raw.csv"],
        sRepoRoot="/workspace/repo",
    )
    assert dictStep["dictVerification"]["sUnitTest"] == "untested"


def test_unnecessary_categories_stay_sticky_on_input_change():
    dictStep = _fdictBuildStep("step1", ["data/raw.csv"])
    dictStep["dictVerification"]["sIntegrity"] = "unnecessary"
    _fnInvalidateStepFiles(
        dictStep, ["/workspace/repo/data/raw.csv"],
        sRepoRoot="/workspace/repo",
    )
    dictVerify = dictStep["dictVerification"]
    assert dictVerify["sUnitTest"] == "untested"
    assert dictVerify["sIntegrity"] == "unnecessary"
    assert dictVerify["sQualitative"] == "untested"


# ---------------------------------------------------------------
# Invalidation via the marker-hash lane
# ---------------------------------------------------------------


def _fdictBuildInputMarker(dictInputHashes):
    """Marker with input hashes only — no output hashes at all."""
    return {
        "sDirectory": "step1",
        "sLabel": "A01",
        "iExitStatus": 0,
        "dictOutputHashes": {},
        "dictInputHashes": dictInputHashes,
    }


def test_input_content_drift_with_same_mtime_invalidates(tmp_path):
    """Content changed, mtime restored — only the hash lane can see it.

    The marker carries input hashes ONLY, so this also kills a gate
    that skips markers lacking output hashes.
    """
    sRepoRoot = str(tmp_path)
    fSharedMtime = 1_700_000_000.0
    _fsWrite(sRepoRoot, "baseline/raw.csv", "original")
    sBaselineSha = mtimeCache.fsBlobShaForFile(
        sRepoRoot, "baseline/raw.csv", {},
    )
    sAbsLive = _fsWrite(sRepoRoot, "data/raw.csv", "tampered")
    _fnTouchMtime(sAbsLive, fSharedMtime)
    dictWorkflow = _fdictOneStepWorkflow(sRepoRoot, ["data/raw.csv"])
    dictNewModTimes = {sAbsLive: str(int(fSharedMtime))}
    dictCtx = _fdictBuildCtx({"cid": dict(dictNewModTimes)})
    _flistDetectAndInvalidate(
        dictCtx, "cid", dictWorkflow, dictNewModTimes,
        dictVars={"sRepoRoot": sRepoRoot},
        dictMarkersByStep={
            0: _fdictBuildInputMarker({"data/raw.csv": sBaselineSha}),
        },
        dictCache={},
    )
    dictVerify = dictWorkflow["listSteps"][0]["dictVerification"]
    assert dictVerify["sUnitTest"] == "untested"
    assert "data/raw.csv" in dictVerify["listModifiedFiles"]


def test_fresh_clone_matching_input_content_stays_passed(tmp_path):
    """First poll after a clone: new mtimes, identical content, no drift."""
    sRepoRoot = str(tmp_path)
    sAbsLive = _fsWrite(sRepoRoot, "data/raw.csv", "original")
    sBaselineSha = mtimeCache.fsBlobShaForFile(
        sRepoRoot, "data/raw.csv", {},
    )
    dictWorkflow = _fdictOneStepWorkflow(sRepoRoot, ["data/raw.csv"])
    dictCtx = _fdictBuildCtx()
    _flistDetectAndInvalidate(
        dictCtx, "cid", dictWorkflow,
        {sAbsLive: str(int(os.path.getmtime(sAbsLive)))},
        dictVars={"sRepoRoot": sRepoRoot},
        dictMarkersByStep={
            0: _fdictBuildInputMarker({"data/raw.csv": sBaselineSha}),
        },
        dictCache={},
    )
    dictVerify = dictWorkflow["listSteps"][0]["dictVerification"]
    assert dictVerify["sUnitTest"] == "passed"
    assert dictVerify.get("listModifiedFiles", []) == []


def test_missing_declared_input_counts_as_hash_drift(tmp_path):
    """An input the marker hashed but that vanished from disk is stale."""
    sRepoRoot = str(tmp_path)
    dictWorkflow = _fdictOneStepWorkflow(sRepoRoot, ["data/raw.csv"])
    dictCtx = _fdictBuildCtx()
    _flistDetectAndInvalidate(
        dictCtx, "cid", dictWorkflow, {},
        dictVars={"sRepoRoot": sRepoRoot},
        dictMarkersByStep={
            0: _fdictBuildInputMarker({"data/raw.csv": "0" * 40}),
        },
        dictCache={},
    )
    dictVerify = dictWorkflow["listSteps"][0]["dictVerification"]
    assert dictVerify["sUnitTest"] == "untested"


# ---------------------------------------------------------------
# Pencil-stale rows
# ---------------------------------------------------------------


def test_pencil_stale_reports_input_file_newer_than_marker():
    dictStep = _fdictBuildStep("step1", ["data/raw.csv"])
    bStale, listStale = _fbStepIsPencilStale(
        dictStep, {}, [],
        {"/workspace/repo/data/raw.csv": "500"},
        iMarkerMtime=100,
        listStepInputPaths=["/workspace/repo/data/raw.csv"],
    )
    assert bStale is True
    assert {
        "sValidator": "test",
        "sCategory": "inputFile",
        "sPath": "/workspace/repo/data/raw.csv",
    } in listStale


def test_pencil_not_stale_when_input_older_than_marker():
    dictStep = _fdictBuildStep("step1", ["data/raw.csv"])
    bStale, listStale = _fbStepIsPencilStale(
        dictStep, {}, [],
        {"/workspace/repo/data/raw.csv": "50"},
        iMarkerMtime=100,
        listStepInputPaths=["/workspace/repo/data/raw.csv"],
    )
    assert bStale is False
    assert listStale == []


def test_step_status_entry_flags_modified_on_stale_input():
    dictStep = _fdictBuildStep("step1", ["data/raw.csv"])
    dictEntry = _fdictBuildStepStatusEntry(
        dictStep, {}, [],
        {"/workspace/repo/data/raw.csv": "500"},
        {"sRepoRoot": "/workspace/repo"},
        iMarkerMtime=100,
    )
    assert dictEntry["sStatus"] == "modified"
    listCategories = [
        dictArtifact["sCategory"]
        for dictArtifact in dictEntry["listStaleArtifacts"]
    ]
    assert "inputFile" in listCategories
