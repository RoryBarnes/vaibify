"""Tests for the workflow / state-file split and marker-based bootstrap."""

import json
from unittest.mock import MagicMock

from vaibify.gui import stateManager


# ----------------------------------------------------------------------
# Path helpers
# ----------------------------------------------------------------------


def test_fsStatePathFromRepo_returns_canonical_path():
    sPath = stateManager.fsStatePathFromRepo("/workspace/Project")
    assert sPath == "/workspace/Project/.vaibify/state.json"


def test_fsStatePathFromRepo_returns_empty_for_empty_repo():
    assert stateManager.fsStatePathFromRepo("") == ""


def test_fsGitignorePathFromRepo_returns_canonical_path():
    sPath = stateManager.fsGitignorePathFromRepo("/workspace/Project")
    assert sPath == "/workspace/Project/.vaibify/.gitignore"


# ----------------------------------------------------------------------
# Split / merge round-trip
# ----------------------------------------------------------------------


def _fdictBuildMergedWorkflow():
    """Workflow shaped like what fdictLoadWorkflowFromContainer returns."""
    return {
        "sPlotDirectory": "Plot",
        "iWorkflowSchemaVersion": 3,
        "bArchiveTrackingMigrated": True,
        "sProjectRepoPath": "/workspace/Project",
        "listSteps": [
            {
                "sName": "First", "sDirectory": "First",
                "saPlotCommands": ["python plot.py"],
                "saPlotFiles": ["fig.pdf"],
                "sLabel": "A01",
                "dictVerification": {
                    "sUnitTest": "passed", "sUser": "passed",
                },
                "dictRunStats": {"fLastRunSeconds": 12.5},
            },
            {
                "sName": "Second", "sDirectory": "Second",
                "saPlotCommands": ["python plot.py"],
                "saPlotFiles": ["fig2.pdf"],
                "sLabel": "A02",
            },
        ],
    }


def test_ftSplitMergedDict_extracts_step_state_keyed_by_sDirectory():
    dictMerged = _fdictBuildMergedWorkflow()
    _, dictState = stateManager.ftSplitMergedDict(dictMerged)
    assert "First" in dictState["dictStepState"]
    assert (
        dictState["dictStepState"]["First"]["dictVerification"]
        ["sUnitTest"] == "passed"
    )
    assert (
        dictState["dictStepState"]["First"]["dictRunStats"]
        ["fLastRunSeconds"] == 12.5
    )


def test_ftSplitMergedDict_omits_step_with_no_state():
    dictMerged = _fdictBuildMergedWorkflow()
    _, dictState = stateManager.ftSplitMergedDict(dictMerged)
    assert "Second" not in dictState["dictStepState"]


def test_ftSplitMergedDict_strips_state_from_declarative_steps():
    dictMerged = _fdictBuildMergedWorkflow()
    dictDeclarative, _ = stateManager.ftSplitMergedDict(dictMerged)
    for dictStep in dictDeclarative["listSteps"]:
        assert "dictVerification" not in dictStep
        assert "dictRunStats" not in dictStep
        assert "sLabel" not in dictStep


def test_ftSplitMergedDict_moves_top_level_state_flag():
    dictMerged = _fdictBuildMergedWorkflow()
    dictDeclarative, dictState = stateManager.ftSplitMergedDict(
        dictMerged,
    )
    assert "bArchiveTrackingMigrated" not in dictDeclarative
    assert dictState["bArchiveTrackingMigrated"] is True


def test_ftSplitMergedDict_drops_transient_sProjectRepoPath():
    dictMerged = _fdictBuildMergedWorkflow()
    dictDeclarative, _ = stateManager.ftSplitMergedDict(dictMerged)
    assert "sProjectRepoPath" not in dictDeclarative


def test_ftSplitMergedDict_does_not_mutate_input():
    dictMerged = _fdictBuildMergedWorkflow()
    sBefore = json.dumps(dictMerged, sort_keys=True)
    stateManager.ftSplitMergedDict(dictMerged)
    assert json.dumps(dictMerged, sort_keys=True) == sBefore


def test_split_then_merge_round_trip_preserves_per_step_state():
    dictMerged = _fdictBuildMergedWorkflow()
    dictDeclarative, dictState = stateManager.ftSplitMergedDict(
        dictMerged,
    )
    stateManager.fnMergeStateIntoWorkflow(dictDeclarative, dictState)
    assert (
        dictDeclarative["listSteps"][0]["dictVerification"]["sUnitTest"]
        == "passed"
    )
    assert (
        dictDeclarative["bArchiveTrackingMigrated"] is True
    )


def test_fnMergeStateIntoWorkflow_no_op_on_none():
    dictWorkflow = {"listSteps": [{"sDirectory": "A"}]}
    stateManager.fnMergeStateIntoWorkflow(dictWorkflow, None)
    assert dictWorkflow == {"listSteps": [{"sDirectory": "A"}]}


# ----------------------------------------------------------------------
# State-file load / save / gitignore
# ----------------------------------------------------------------------


def test_fdictLoadStateFromContainer_returns_none_when_missing():
    mockDocker = MagicMock()
    mockDocker.fbaFetchFile.side_effect = FileNotFoundError("nope")
    dictResult = stateManager.fdictLoadStateFromContainer(
        mockDocker, "cid", "/some/state.json",
    )
    assert dictResult is None


def test_fdictLoadStateFromContainer_returns_none_on_corrupt_json():
    mockDocker = MagicMock()
    mockDocker.fbaFetchFile.return_value = b"{not json"
    dictResult = stateManager.fdictLoadStateFromContainer(
        mockDocker, "cid", "/some/state.json",
    )
    assert dictResult is None


def test_fdictLoadStateFromContainer_parses_valid_state():
    dictPersisted = {
        "iStateSchemaVersion": 1,
        "dictStepState": {"X": {"dictVerification": {"sUser": "passed"}}},
    }
    mockDocker = MagicMock()
    mockDocker.fbaFetchFile.return_value = json.dumps(
        dictPersisted,
    ).encode("utf-8")
    dictResult = stateManager.fdictLoadStateFromContainer(
        mockDocker, "cid", "/state.json",
    )
    assert dictResult["dictStepState"]["X"]["dictVerification"]["sUser"] == "passed"


def test_fnSaveStateToContainer_stamps_sLastUpdated_and_writes():
    mockDocker = MagicMock()
    dictState = stateManager.fdictBuildEmptyState()
    stateManager.fnSaveStateToContainer(
        mockDocker, "cid", "/state.json", dictState,
    )
    sContainerId, sPath, baPayload = mockDocker.fnWriteFile.call_args[0]
    assert sContainerId == "cid"
    assert sPath == "/state.json"
    dictPersisted = json.loads(baPayload.decode("utf-8"))
    assert dictPersisted["iStateSchemaVersion"] == 1
    assert dictPersisted["sLastUpdated"]


def test_fnEnsureVaibifyGitignore_writes_when_missing():
    mockDocker = MagicMock()
    mockDocker.fbaFetchFile.side_effect = FileNotFoundError("nope")
    stateManager.fnEnsureVaibifyGitignore(
        mockDocker, "cid", "/workspace/Project",
    )
    sContainerId, sPath, baPayload = mockDocker.fnWriteFile.call_args[0]
    assert sPath == "/workspace/Project/.vaibify/.gitignore"
    assert b"state.json" in baPayload


def test_fnEnsureVaibifyGitignore_skips_when_present():
    mockDocker = MagicMock()
    mockDocker.fbaFetchFile.return_value = b"existing content\n"
    stateManager.fnEnsureVaibifyGitignore(
        mockDocker, "cid", "/workspace/Project",
    )
    mockDocker.fnWriteFile.assert_not_called()


# ----------------------------------------------------------------------
# Marker-based bootstrap
# ----------------------------------------------------------------------


def _fnBuildBootstrapMock(
    dictMarkers, dictDiskHashes,
):
    """Build a MagicMock for the bootstrap test surface.

    ``dictMarkers`` maps repo-relative step name to marker dict (or
    None to simulate a missing marker). ``dictDiskHashes`` maps
    repo-relative output path to the on-disk SHA returned by the
    container-side hashing helper.
    """
    mockDocker = MagicMock()

    def _fFetchFile(sContainerId, sPath):
        for sName, dictMarker in dictMarkers.items():
            sExpected = (
                "/workspace/Project/.vaibify/test_markers/"
                + sName + ".json"
            )
            if sPath == sExpected:
                if dictMarker is None:
                    raise FileNotFoundError(sPath)
                return json.dumps(dictMarker).encode("utf-8")
        raise FileNotFoundError(sPath)

    mockDocker.fbaFetchFile.side_effect = _fFetchFile

    def _fExecuteCommand(sContainerId, sCommand, **_kwargs):
        if "git hash-object" in sCommand or "python3" in sCommand:
            return (0, json.dumps(dictDiskHashes) + "\n")
        return (0, "")

    mockDocker.ftResultExecuteCommand.side_effect = _fExecuteCommand
    return mockDocker


def _fdictBuildWorkflowWith(listSteps):
    return {
        "sPlotDirectory": "Plot",
        "listSteps": [
            {
                "sName": s, "sDirectory": s,
                "saPlotCommands": [], "saPlotFiles": [],
            }
            for s in listSteps
        ],
    }


def _fdictBuildMarker(dictHashes, dictCategories=None):
    return {
        "iExitStatus": 0,
        "sRunAtUtc": "2026-04-29T22:00:04Z",
        "dictCategories": dictCategories or {
            "quantitative": {"iPassed": 5, "iFailed": 0},
        },
        "dictOutputHashes": dictHashes,
    }


def test_bootstrap_passed_from_marker_when_hashes_match():
    dictHashes = {"A/out.npz": "a" * 40}
    mockDocker = _fnBuildBootstrapMock(
        {"A": _fdictBuildMarker(dictHashes)}, dictHashes,
    )
    dictWorkflow = _fdictBuildWorkflowWith(["A"])
    dictState = stateManager.fdictBootstrapStateFromMarkers(
        mockDocker, "cid", dictWorkflow, "/workspace/Project",
    )
    dictVerify = dictState["dictStepState"]["A"]["dictVerification"]
    assert dictVerify["sQuantitative"] == "passed-from-marker"
    assert dictVerify["sUser"] == ""
    assert dictVerify["listModifiedFiles"] == []


def test_bootstrap_outputs_changed_when_hash_differs():
    dictMarkerHashes = {"A/out.npz": "a" * 40}
    dictDiskHashes = {"A/out.npz": "b" * 40}
    mockDocker = _fnBuildBootstrapMock(
        {"A": _fdictBuildMarker(dictMarkerHashes)}, dictDiskHashes,
    )
    dictWorkflow = _fdictBuildWorkflowWith(["A"])
    dictState = stateManager.fdictBootstrapStateFromMarkers(
        mockDocker, "cid", dictWorkflow, "/workspace/Project",
    )
    dictVerify = dictState["dictStepState"]["A"]["dictVerification"]
    assert dictVerify["sQuantitative"] == "outputs-changed"
    assert dictVerify["listModifiedFiles"] == ["A/out.npz"]


def test_bootstrap_outputs_missing_when_files_absent():
    dictMarkerHashes = {"A/out.npz": "a" * 40, "A/extra.npz": "b" * 40}
    mockDocker = _fnBuildBootstrapMock(
        {"A": _fdictBuildMarker(dictMarkerHashes)},
        {},  # nothing on disk
    )
    dictWorkflow = _fdictBuildWorkflowWith(["A"])
    dictState = stateManager.fdictBootstrapStateFromMarkers(
        mockDocker, "cid", dictWorkflow, "/workspace/Project",
    )
    dictVerify = dictState["dictStepState"]["A"]["dictVerification"]
    assert dictVerify["sQuantitative"] == "outputs-missing"


def test_bootstrap_skips_steps_without_markers():
    mockDocker = _fnBuildBootstrapMock({"A": None}, {})
    dictWorkflow = _fdictBuildWorkflowWith(["A"])
    dictState = stateManager.fdictBootstrapStateFromMarkers(
        mockDocker, "cid", dictWorkflow, "/workspace/Project",
    )
    assert "A" not in dictState["dictStepState"]


def test_bootstrap_user_attestation_resets_per_machine():
    """Even when a marker reports passing, sUser must start empty."""
    dictHashes = {"A/out.npz": "a" * 40}
    mockDocker = _fnBuildBootstrapMock(
        {"A": _fdictBuildMarker(dictHashes)}, dictHashes,
    )
    dictWorkflow = _fdictBuildWorkflowWith(["A"])
    dictState = stateManager.fdictBootstrapStateFromMarkers(
        mockDocker, "cid", dictWorkflow, "/workspace/Project",
    )
    dictVerify = dictState["dictStepState"]["A"]["dictVerification"]
    assert dictVerify["sUser"] == ""


def test_bootstrap_returns_empty_state_for_empty_repo_path():
    mockDocker = MagicMock()
    dictWorkflow = _fdictBuildWorkflowWith(["A"])
    dictState = stateManager.fdictBootstrapStateFromMarkers(
        mockDocker, "cid", dictWorkflow, "",
    )
    assert dictState["dictStepState"] == {}
    mockDocker.fbaFetchFile.assert_not_called()


def test_bootstrap_records_marker_run_timestamp():
    dictHashes = {"A/out.npz": "a" * 40}
    mockDocker = _fnBuildBootstrapMock(
        {"A": _fdictBuildMarker(dictHashes)}, dictHashes,
    )
    dictWorkflow = _fdictBuildWorkflowWith(["A"])
    dictState = stateManager.fdictBootstrapStateFromMarkers(
        mockDocker, "cid", dictWorkflow, "/workspace/Project",
    )
    dictVerify = dictState["dictStepState"]["A"]["dictVerification"]
    assert dictVerify["sLastTestRun"] == "2026-04-29T22:00:04Z"


def test_bootstrap_failed_marker_does_not_pass_from_marker():
    """A non-zero iExitStatus must not produce passed-from-marker."""
    dictHashes = {"A/out.npz": "a" * 40}
    dictMarker = _fdictBuildMarker(dictHashes)
    dictMarker["iExitStatus"] = 1
    mockDocker = _fnBuildBootstrapMock({"A": dictMarker}, dictHashes)
    dictWorkflow = _fdictBuildWorkflowWith(["A"])
    dictState = stateManager.fdictBootstrapStateFromMarkers(
        mockDocker, "cid", dictWorkflow, "/workspace/Project",
    )
    dictVerify = dictState["dictStepState"]["A"]["dictVerification"]
    assert dictVerify["sQuantitative"] == "failed"
    assert dictVerify["sUnitTest"] == "failed"


def test_bootstrap_category_with_failures_marked_failed():
    """A category with iFailed > 0 is failed even when hashes match."""
    dictHashes = {"A/out.npz": "a" * 40}
    dictMarker = _fdictBuildMarker(
        dictHashes,
        dictCategories={
            "quantitative": {"iPassed": 3, "iFailed": 2},
        },
    )
    mockDocker = _fnBuildBootstrapMock({"A": dictMarker}, dictHashes)
    dictWorkflow = _fdictBuildWorkflowWith(["A"])
    dictState = stateManager.fdictBootstrapStateFromMarkers(
        mockDocker, "cid", dictWorkflow, "/workspace/Project",
    )
    dictVerify = dictState["dictStepState"]["A"]["dictVerification"]
    assert dictVerify["sQuantitative"] == "failed"


# ----------------------------------------------------------------------
# End-to-end load / save round-trip via workflowManager
# ----------------------------------------------------------------------


def _fnBuildLoadSaveMock(dictWorkflowOnDisk, dictStateOnDisk=None):
    """Build a docker mock backing a workflow.json + state.json pair.

    ``dictStateOnDisk=None`` simulates a fresh checkout (state.json
    absent). Tracks every fnWriteFile call so tests can inspect what
    landed at each path.
    """
    mockDocker = MagicMock()
    listWrites = []

    def _fFetch(sContainerId, sPath):
        if sPath.endswith(".vaibify/workflows/w.json"):
            return json.dumps(dictWorkflowOnDisk).encode("utf-8")
        if sPath.endswith(".vaibify/state.json"):
            if dictStateOnDisk is None:
                raise FileNotFoundError(sPath)
            return json.dumps(dictStateOnDisk).encode("utf-8")
        if sPath.endswith(".vaibify/.gitignore"):
            return b"state.json\n"
        if sPath.endswith(".json"):
            raise FileNotFoundError(sPath)
        raise FileNotFoundError(sPath)

    mockDocker.fbaFetchFile.side_effect = _fFetch

    def _fWrite(sContainerId, sPath, baPayload):
        listWrites.append((sPath, baPayload))

    mockDocker.fnWriteFile.side_effect = _fWrite
    mockDocker.ftResultExecuteCommand.return_value = (0, "")
    return mockDocker, listWrites


def test_load_merges_state_file_into_workflow_dict():
    """When both files exist, the loaded dict carries stateful fields."""
    from vaibify.gui.workflowManager import (
        fdictLoadWorkflowFromContainer,
    )
    dictDeclarative = {
        "iWorkflowSchemaVersion": 3,
        "sPlotDirectory": "Plot",
        "listSteps": [{
            "sName": "A", "sDirectory": "A",
            "saPlotCommands": [], "saPlotFiles": [],
        }],
    }
    dictPersistedState = {
        "iStateSchemaVersion": 1,
        "bArchiveTrackingMigrated": True,
        "dictStepState": {
            "A": {
                "dictVerification": {"sUser": "passed"},
                "dictRunStats": {"fLastRunSeconds": 7.5},
            },
        },
    }
    mockDocker, _ = _fnBuildLoadSaveMock(
        dictDeclarative, dictPersistedState,
    )
    dictResult = fdictLoadWorkflowFromContainer(
        mockDocker, "cid",
        sWorkflowPath="/workspace/Project/.vaibify/workflows/w.json",
    )
    assert dictResult["bArchiveTrackingMigrated"] is True
    assert (
        dictResult["listSteps"][0]["dictVerification"]["sUser"]
        == "passed"
    )
    assert (
        dictResult["listSteps"][0]["dictRunStats"]["fLastRunSeconds"]
        == 7.5
    )


def test_load_persists_bootstrap_when_state_file_absent():
    """Bootstrap result is written so subsequent loads skip the work."""
    from vaibify.gui.workflowManager import (
        fdictLoadWorkflowFromContainer,
    )
    dictDeclarative = {
        "iWorkflowSchemaVersion": 3,
        "sPlotDirectory": "Plot",
        "listSteps": [{
            "sName": "A", "sDirectory": "A",
            "saPlotCommands": [], "saPlotFiles": [],
        }],
    }
    mockDocker, listWrites = _fnBuildLoadSaveMock(dictDeclarative)
    fdictLoadWorkflowFromContainer(
        mockDocker, "cid",
        sWorkflowPath="/workspace/Project/.vaibify/workflows/w.json",
    )
    listStateWrites = [
        (sPath, baPayload) for sPath, baPayload in listWrites
        if sPath.endswith(".vaibify/state.json")
    ]
    assert listStateWrites, (
        "Bootstrap must persist state.json so the next load is cheap"
    )


def test_save_split_workflow_json_carries_no_stateful_fields():
    """Round-trip the merged dict; persisted workflow.json is declarative."""
    from vaibify.gui.workflowManager import fnSaveWorkflowToContainer
    mockDocker, listWrites = _fnBuildLoadSaveMock(
        {"sPlotDirectory": "Plot", "listSteps": []},
    )
    dictMerged = {
        "iWorkflowSchemaVersion": 3,
        "sPlotDirectory": "Plot",
        "bArchiveTrackingMigrated": True,
        "sProjectRepoPath": "/workspace/Project",
        "listSteps": [{
            "sName": "A", "sDirectory": "A",
            "saPlotCommands": [], "saPlotFiles": [],
            "dictVerification": {"sUser": "passed"},
            "dictRunStats": {"fLastRunSeconds": 1.0},
        }],
    }
    fnSaveWorkflowToContainer(
        mockDocker, "cid", dictMerged,
        sWorkflowPath="/workspace/Project/.vaibify/workflows/w.json",
    )
    dictWorkflowWritten = None
    dictStateWritten = None
    for sPath, baPayload in listWrites:
        if sPath.endswith("/workflows/w.json"):
            dictWorkflowWritten = json.loads(baPayload.decode("utf-8"))
        elif sPath.endswith(".vaibify/state.json"):
            dictStateWritten = json.loads(baPayload.decode("utf-8"))
    assert dictWorkflowWritten is not None
    assert dictStateWritten is not None
    assert "bArchiveTrackingMigrated" not in dictWorkflowWritten
    assert "sProjectRepoPath" not in dictWorkflowWritten
    for dictStep in dictWorkflowWritten["listSteps"]:
        assert "dictVerification" not in dictStep
        assert "dictRunStats" not in dictStep
        assert "sLabel" not in dictStep
    assert dictStateWritten["bArchiveTrackingMigrated"] is True
    assert (
        dictStateWritten["dictStepState"]["A"]
        ["dictVerification"]["sUser"] == "passed"
    )


def test_save_is_idempotent_for_workflow_json_payload():
    """Two saves in a row produce the same workflow.json bytes."""
    from vaibify.gui.workflowManager import fnSaveWorkflowToContainer
    mockDocker, listWrites = _fnBuildLoadSaveMock(
        {"sPlotDirectory": "Plot", "listSteps": []},
    )
    dictMerged = {
        "sPlotDirectory": "Plot",
        "listSteps": [{
            "sName": "A", "sDirectory": "A",
            "saPlotCommands": [], "saPlotFiles": [],
            "dictVerification": {"sUser": "passed"},
        }],
    }
    fnSaveWorkflowToContainer(
        mockDocker, "cid", dictMerged,
        sWorkflowPath="/workspace/Project/.vaibify/workflows/w.json",
    )
    fnSaveWorkflowToContainer(
        mockDocker, "cid", dictMerged,
        sWorkflowPath="/workspace/Project/.vaibify/workflows/w.json",
    )
    listWorkflowWrites = [
        baPayload for sPath, baPayload in listWrites
        if sPath.endswith("/workflows/w.json")
    ]
    assert len(listWorkflowWrites) == 2
    assert listWorkflowWrites[0] == listWorkflowWrites[1]
