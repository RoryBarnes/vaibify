"""Unit tests for workflowReloadDetector.

Covers the behaviours that matter for the dashboard's ground-truth
contract:

- self-write fingerprints silence subsequent polls
- divergent content fingerprints trigger a reload — including when
  the mtime is unchanged (the same-second swallow regression)
- malformed JSON / missing files surface as ``sError`` without crashing
- the project-repo path is re-derived via the in-container git probe
  on reload (mirroring connect-time semantics)
- every cache replacement bumps the per-container workflow epoch
"""

import json
from unittest.mock import patch

import pytest

from vaibify.gui import workflowReloadDetector


_S_CONTAINER_ID = "test-container"
_S_WORKFLOW_PATH = "/workspace/proj/.vaibify/workflows/demo.json"
_S_REPO_PATH = "/workspace/proj"
_S_FINGERPRINT_A = "a" * 64
_S_FINGERPRINT_B = "b" * 64


class _FakeDocker:
    """Minimal docker-connection fake for the reload detector.

    Only the existence probe reaches docker now — content comparison
    happens on fingerprints collected by the polling batch and passed
    in, and baselines are recorded from host-computed hashes.
    """

    def __init__(self):
        self.setExistingPaths = set()
        self.listCommands = []

    def ftResultExecuteCommand(self, sContainerId, sCommand):
        self.listCommands.append(sCommand)
        if sCommand.startswith("test -e ") and "exists:" in sCommand:
            for sPath in self.setExistingPaths:
                if sPath in sCommand:
                    return (0, "exists:1")
            return (0, "exists:0")
        return (0, "")


def _fdictMakeContext(connectionDocker):
    return {
        "docker": connectionDocker,
        "workflows": {},
        "lastSelfWriteFingerprints": {},
    }


def _fdictMakeWorkflow(sName="demo", listSteps=None):
    return {
        "sWorkflowName": sName,
        "sPath": _S_WORKFLOW_PATH,
        "sProjectRepoPath": _S_REPO_PATH,
        "_sSourceFingerprint": _S_FINGERPRINT_B,
        "listSteps": listSteps or [],
    }


def _fdictPolled(sMtime="1700000000"):
    return {_S_WORKFLOW_PATH: sMtime}


# ---------- fnRecordSelfWriteFingerprint ----------


def test_record_self_write_fingerprint_stores_value():
    dictCtx = _fdictMakeContext(_FakeDocker())
    workflowReloadDetector.fnRecordSelfWriteFingerprint(
        dictCtx, _S_CONTAINER_ID, _S_FINGERPRINT_A,
    )
    assert (
        dictCtx["lastSelfWriteFingerprints"][_S_CONTAINER_ID]
        == _S_FINGERPRINT_A
    )


def test_record_self_write_fingerprint_keeps_baseline_on_empty():
    """An empty fingerprint must not poison a known-good baseline.

    Absence of evidence (a failed hash or a loader that produced no
    fingerprint) is not evidence of change; overwriting the baseline
    with "" would make every following poll's real fingerprint compare
    unequal, firing a spurious reload each cycle (the reload-toast
    loop the mtime design had under docker-exec contention).
    """
    dictCtx = _fdictMakeContext(_FakeDocker())
    dictCtx["lastSelfWriteFingerprints"][_S_CONTAINER_ID] = (
        _S_FINGERPRINT_A
    )
    workflowReloadDetector.fnRecordSelfWriteFingerprint(
        dictCtx, _S_CONTAINER_ID, "",
    )
    assert (
        dictCtx["lastSelfWriteFingerprints"][_S_CONTAINER_ID]
        == _S_FINGERPRINT_A
    )


def test_record_self_write_fingerprint_initializes_map_when_missing():
    dictCtx = {"docker": _FakeDocker(), "workflows": {}}
    workflowReloadDetector.fnRecordSelfWriteFingerprint(
        dictCtx, _S_CONTAINER_ID, _S_FINGERPRINT_A,
    )
    assert (
        dictCtx["lastSelfWriteFingerprints"][_S_CONTAINER_ID]
        == _S_FINGERPRINT_A
    )


def test_reload_seeds_silently_when_baseline_absent():
    """A missing baseline is not evidence of an out-of-band edit.

    When no baseline was recorded, the next poll with a real
    fingerprint must seed the baseline and report no reload — not
    fire a spurious reload that clears the client's file caches
    (the grey-badge blink) and toasts "reloaded from disk".
    """
    dictCtx = _fdictMakeContext(_FakeDocker())
    dictReload = workflowReloadDetector.fdictMaybeReloadWorkflow(
        dictCtx, _S_CONTAINER_ID, _S_WORKFLOW_PATH,
        _fdictPolled(), _S_FINGERPRINT_A,
    )
    assert dictReload == {
        "bReplaced": False, "dictWorkflow": None, "sError": None,
    }
    assert (
        dictCtx["lastSelfWriteFingerprints"][_S_CONTAINER_ID]
        == _S_FINGERPRINT_A
    )


# ---------- fdictMaybeReloadWorkflow ----------


def test_no_reload_when_polled_fingerprint_matches_self_write():
    dictCtx = _fdictMakeContext(_FakeDocker())
    dictCtx["lastSelfWriteFingerprints"][_S_CONTAINER_ID] = (
        _S_FINGERPRINT_A
    )
    dictReload = workflowReloadDetector.fdictMaybeReloadWorkflow(
        dictCtx, _S_CONTAINER_ID, _S_WORKFLOW_PATH,
        _fdictPolled(), _S_FINGERPRINT_A,
    )
    assert dictReload == {
        "bReplaced": False, "dictWorkflow": None, "sError": None,
    }


def test_no_reload_when_fingerprint_flakes_but_stat_saw_file():
    """Empty fingerprint + stat success is a hash-collection hiccup."""
    dictCtx = _fdictMakeContext(_FakeDocker())
    dictCtx["lastSelfWriteFingerprints"][_S_CONTAINER_ID] = (
        _S_FINGERPRINT_A
    )
    dictReload = workflowReloadDetector.fdictMaybeReloadWorkflow(
        dictCtx, _S_CONTAINER_ID, _S_WORKFLOW_PATH,
        _fdictPolled(), "",
    )
    assert dictReload == {
        "bReplaced": False, "dictWorkflow": None, "sError": None,
    }


def test_reload_when_polled_fingerprint_diverges():
    dictCtx = _fdictMakeContext(_FakeDocker())
    dictCtx["lastSelfWriteFingerprints"][_S_CONTAINER_ID] = (
        _S_FINGERPRINT_A
    )
    dictNewWorkflow = _fdictMakeWorkflow(
        sName="updated", listSteps=[{"sDirectory": "stepA"}],
    )
    with patch(
        "vaibify.gui.workflowReloadDetector.workflowManager"
        ".fdictLoadWorkflowFromContainer",
        return_value=dictNewWorkflow,
    ), patch(
        "vaibify.gui.containerGit.fsDetectProjectRepoInContainer",
        return_value=_S_REPO_PATH,
    ):
        dictReload = workflowReloadDetector.fdictMaybeReloadWorkflow(
            dictCtx, _S_CONTAINER_ID, _S_WORKFLOW_PATH,
            _fdictPolled(), _S_FINGERPRINT_B,
        )
    assert dictReload["bReplaced"] is True
    assert dictReload["sError"] is None
    assert dictReload["dictWorkflow"]["sWorkflowName"] == "updated"
    assert (
        dictCtx["workflows"][_S_CONTAINER_ID]
        is dictReload["dictWorkflow"]
    )
    assert (
        dictCtx["lastSelfWriteFingerprints"][_S_CONTAINER_ID]
        == _S_FINGERPRINT_B
    )


def test_reload_fires_on_same_second_edit():
    """Content change with an identical mtime must still reload.

    Regression: whole-second mtime comparison swallowed any agent
    edit landing in the same second as a backend save of
    workflow.json (the poll's own invalidation save made this a
    recurring window). Content fingerprints are second-independent —
    the polled dictModTimes here is byte-identical to the baseline
    era and only the fingerprint moved.
    """
    dictCtx = _fdictMakeContext(_FakeDocker())
    dictCtx["lastSelfWriteFingerprints"][_S_CONTAINER_ID] = (
        _S_FINGERPRINT_A
    )
    dictNewWorkflow = _fdictMakeWorkflow(sName="same-second")
    with patch(
        "vaibify.gui.workflowReloadDetector.workflowManager"
        ".fdictLoadWorkflowFromContainer",
        return_value=dictNewWorkflow,
    ), patch(
        "vaibify.gui.containerGit.fsDetectProjectRepoInContainer",
        return_value=_S_REPO_PATH,
    ):
        dictReload = workflowReloadDetector.fdictMaybeReloadWorkflow(
            dictCtx, _S_CONTAINER_ID, _S_WORKFLOW_PATH,
            _fdictPolled("1700000000"), _S_FINGERPRINT_B,
        )
    assert dictReload["bReplaced"] is True
    assert (
        dictReload["dictWorkflow"]["sWorkflowName"] == "same-second"
    )


def test_reload_bumps_workflow_epoch():
    """Every cache replacement bumps the per-container epoch."""
    dictCtx = _fdictMakeContext(_FakeDocker())
    dictCtx["lastSelfWriteFingerprints"][_S_CONTAINER_ID] = (
        _S_FINGERPRINT_A
    )
    assert workflowReloadDetector.fiGetWorkflowEpoch(
        dictCtx, _S_CONTAINER_ID,
    ) == 0
    with patch(
        "vaibify.gui.workflowReloadDetector.workflowManager"
        ".fdictLoadWorkflowFromContainer",
        return_value=_fdictMakeWorkflow(),
    ), patch(
        "vaibify.gui.containerGit.fsDetectProjectRepoInContainer",
        return_value=_S_REPO_PATH,
    ):
        workflowReloadDetector.fdictMaybeReloadWorkflow(
            dictCtx, _S_CONTAINER_ID, _S_WORKFLOW_PATH,
            _fdictPolled(), _S_FINGERPRINT_B,
        )
    assert workflowReloadDetector.fiGetWorkflowEpoch(
        dictCtx, _S_CONTAINER_ID,
    ) == 1


def test_failed_reload_does_not_bump_epoch():
    """A rejected re-load leaves clients on the last good revision."""
    dictCtx = _fdictMakeContext(_FakeDocker())
    dictCtx["lastSelfWriteFingerprints"][_S_CONTAINER_ID] = (
        _S_FINGERPRINT_A
    )
    with patch(
        "vaibify.gui.workflowReloadDetector.workflowManager"
        ".fdictLoadWorkflowFromContainer",
        side_effect=ValueError("Invalid workflow.json: bad shape"),
    ):
        workflowReloadDetector.fdictMaybeReloadWorkflow(
            dictCtx, _S_CONTAINER_ID, _S_WORKFLOW_PATH,
            _fdictPolled(), _S_FINGERPRINT_B,
        )
    assert workflowReloadDetector.fiGetWorkflowEpoch(
        dictCtx, _S_CONTAINER_ID,
    ) == 0


def test_reload_handles_malformed_json():
    dictCtx = _fdictMakeContext(_FakeDocker())
    dictPriorWorkflow = _fdictMakeWorkflow(sName="prior")
    dictCtx["workflows"][_S_CONTAINER_ID] = dictPriorWorkflow
    dictCtx["lastSelfWriteFingerprints"][_S_CONTAINER_ID] = (
        _S_FINGERPRINT_A
    )
    with patch(
        "vaibify.gui.workflowReloadDetector.workflowManager"
        ".fdictLoadWorkflowFromContainer",
        side_effect=ValueError("Invalid workflow.json: bad shape"),
    ):
        dictReload = workflowReloadDetector.fdictMaybeReloadWorkflow(
            dictCtx, _S_CONTAINER_ID, _S_WORKFLOW_PATH,
            _fdictPolled(), _S_FINGERPRINT_B,
        )
    assert dictReload["bReplaced"] is False
    assert "Invalid workflow.json" in dictReload["sError"]
    assert dictCtx["workflows"][_S_CONTAINER_ID] is dictPriorWorkflow
    assert (
        dictCtx["lastSelfWriteFingerprints"][_S_CONTAINER_ID]
        == _S_FINGERPRINT_A
    )


def test_reload_silent_when_polled_batch_empty_but_file_exists():
    """Empty dictModTimes + existence probe confirms file → hiccup, no toast."""
    fakeDocker = _FakeDocker()
    fakeDocker.setExistingPaths.add(_S_WORKFLOW_PATH)
    dictCtx = _fdictMakeContext(fakeDocker)
    dictReload = workflowReloadDetector.fdictMaybeReloadWorkflow(
        dictCtx, _S_CONTAINER_ID, _S_WORKFLOW_PATH,
        {}, "",
    )
    assert dictReload == {
        "bReplaced": False, "dictWorkflow": None, "sError": None,
    }


def test_reload_reports_missing_when_other_paths_returned():
    """Other paths in dictModTimes but workflow absent → genuine missing event."""
    dictCtx = _fdictMakeContext(_FakeDocker())
    dictReload = workflowReloadDetector.fdictMaybeReloadWorkflow(
        dictCtx, _S_CONTAINER_ID, _S_WORKFLOW_PATH,
        {"/workspace/other/file.txt": "1700000000"}, "",
    )
    assert dictReload["bReplaced"] is False
    assert "missing" in dictReload["sError"].lower()


def test_reload_reports_missing_when_empty_batch_and_probe_confirms_gone():
    """Empty dictModTimes + probe says file gone → real deletion, do toast."""
    dictCtx = _fdictMakeContext(_FakeDocker())
    dictReload = workflowReloadDetector.fdictMaybeReloadWorkflow(
        dictCtx, _S_CONTAINER_ID, _S_WORKFLOW_PATH,
        {}, "",
    )
    assert dictReload["bReplaced"] is False
    assert "missing" in dictReload["sError"].lower()


def test_reload_handles_empty_workflow_path():
    dictCtx = _fdictMakeContext(_FakeDocker())
    dictReload = workflowReloadDetector.fdictMaybeReloadWorkflow(
        dictCtx, _S_CONTAINER_ID, "",
        _fdictPolled(), _S_FINGERPRINT_B,
    )
    assert dictReload == {
        "bReplaced": False, "dictWorkflow": None, "sError": None,
    }


def test_reload_re_derives_project_repo_path_via_container_git():
    dictCtx = _fdictMakeContext(_FakeDocker())
    dictCtx["lastSelfWriteFingerprints"][_S_CONTAINER_ID] = (
        _S_FINGERPRINT_A
    )
    dictNewWorkflow = _fdictMakeWorkflow()
    dictNewWorkflow["sProjectRepoPath"] = "/will-be-overridden"
    with patch(
        "vaibify.gui.workflowReloadDetector.workflowManager"
        ".fdictLoadWorkflowFromContainer",
        return_value=dictNewWorkflow,
    ), patch(
        "vaibify.gui.containerGit.fsDetectProjectRepoInContainer",
        return_value="/workspace/probed-repo",
    ) as mockProbe:
        workflowReloadDetector.fdictMaybeReloadWorkflow(
            dictCtx, _S_CONTAINER_ID, _S_WORKFLOW_PATH,
            _fdictPolled(), _S_FINGERPRINT_B,
        )
    mockProbe.assert_called_once()
    assert (
        dictCtx["workflows"][_S_CONTAINER_ID]["sProjectRepoPath"]
        == "/workspace/probed-repo"
    )


def test_reload_does_not_re_trigger_on_next_poll():
    """The loaded content's fingerprint becomes the new baseline."""
    dictCtx = _fdictMakeContext(_FakeDocker())
    dictCtx["lastSelfWriteFingerprints"][_S_CONTAINER_ID] = (
        _S_FINGERPRINT_A
    )
    dictNewWorkflow = _fdictMakeWorkflow(sName="updated")
    with patch(
        "vaibify.gui.workflowReloadDetector.workflowManager"
        ".fdictLoadWorkflowFromContainer",
        return_value=dictNewWorkflow,
    ), patch(
        "vaibify.gui.containerGit.fsDetectProjectRepoInContainer",
        return_value=_S_REPO_PATH,
    ):
        dictFirst = workflowReloadDetector.fdictMaybeReloadWorkflow(
            dictCtx, _S_CONTAINER_ID, _S_WORKFLOW_PATH,
            _fdictPolled(), _S_FINGERPRINT_B,
        )
        dictSecond = workflowReloadDetector.fdictMaybeReloadWorkflow(
            dictCtx, _S_CONTAINER_ID, _S_WORKFLOW_PATH,
            _fdictPolled(), _S_FINGERPRINT_B,
        )
    assert dictFirst["bReplaced"] is True
    assert dictSecond["bReplaced"] is False


# ---------- epoch helpers ----------


def test_epoch_defaults_to_zero_and_increments():
    dictCtx = {"docker": _FakeDocker(), "workflows": {}}
    assert workflowReloadDetector.fiGetWorkflowEpoch(
        dictCtx, _S_CONTAINER_ID,
    ) == 0
    workflowReloadDetector.fnBumpWorkflowEpoch(
        dictCtx, _S_CONTAINER_ID,
    )
    workflowReloadDetector.fnBumpWorkflowEpoch(
        dictCtx, _S_CONTAINER_ID,
    )
    assert workflowReloadDetector.fiGetWorkflowEpoch(
        dictCtx, _S_CONTAINER_ID,
    ) == 2


def test_epoch_is_per_container():
    dictCtx = {"docker": _FakeDocker(), "workflows": {}}
    workflowReloadDetector.fnBumpWorkflowEpoch(dictCtx, "cid-one")
    assert workflowReloadDetector.fiGetWorkflowEpoch(
        dictCtx, "cid-one",
    ) == 1
    assert workflowReloadDetector.fiGetWorkflowEpoch(
        dictCtx, "cid-two",
    ) == 0


# ---------- fdictDetectNewlyAvailableWorkflows ----------


_S_DEMO_PATH = "/workspace/proj/.vaibify/workflows/demo.json"
_S_OTHER_PATH = "/workspace/proj/.vaibify/workflows/other.json"


def _fdictMakeListing(sPath, sName):
    return {
        "sPath": sPath,
        "sName": sName,
        "sRepoName": "proj",
        "sProjectRepoPath": _S_REPO_PATH,
    }


def test_detect_seeds_cache_silently_on_first_poll():
    """First poll seeds the cache and reports no change."""
    dictCtx = _fdictMakeContext(_FakeDocker())
    with patch(
        "vaibify.gui.workflowReloadDetector.workflowManager"
        ".flistFindWorkflowsInContainer",
        return_value=[_fdictMakeListing(_S_DEMO_PATH, "demo")],
    ):
        dictResult = (
            workflowReloadDetector
            .fdictDetectNewlyAvailableWorkflows(
                dictCtx, _S_CONTAINER_ID,
            )
        )
    assert dictResult["bChangedSinceLastPoll"] is False
    assert dictResult["listNewWorkflowPaths"] == []
    assert len(dictResult["listWorkflows"]) == 1
    assert (
        dictCtx["lastDiscoveredWorkflows"][_S_CONTAINER_ID]
        == {_S_DEMO_PATH}
    )


def test_detect_no_change_when_list_unchanged():
    """Subsequent identical poll surfaces no change."""
    dictCtx = _fdictMakeContext(_FakeDocker())
    with patch(
        "vaibify.gui.workflowReloadDetector.workflowManager"
        ".flistFindWorkflowsInContainer",
        return_value=[_fdictMakeListing(_S_DEMO_PATH, "demo")],
    ):
        workflowReloadDetector.fdictDetectNewlyAvailableWorkflows(
            dictCtx, _S_CONTAINER_ID,
        )
        dictResult = (
            workflowReloadDetector
            .fdictDetectNewlyAvailableWorkflows(
                dictCtx, _S_CONTAINER_ID,
            )
        )
    assert dictResult["bChangedSinceLastPoll"] is False
    assert dictResult["listNewWorkflowPaths"] == []


def test_detect_flags_new_workflow_appearance():
    """An added workflow is reported in listNewWorkflowPaths."""
    dictCtx = _fdictMakeContext(_FakeDocker())
    listFirst = [_fdictMakeListing(_S_DEMO_PATH, "demo")]
    listSecond = [
        _fdictMakeListing(_S_DEMO_PATH, "demo"),
        _fdictMakeListing(_S_OTHER_PATH, "other"),
    ]
    with patch(
        "vaibify.gui.workflowReloadDetector.workflowManager"
        ".flistFindWorkflowsInContainer",
        side_effect=[listFirst, listSecond],
    ):
        workflowReloadDetector.fdictDetectNewlyAvailableWorkflows(
            dictCtx, _S_CONTAINER_ID,
        )
        dictResult = (
            workflowReloadDetector
            .fdictDetectNewlyAvailableWorkflows(
                dictCtx, _S_CONTAINER_ID,
            )
        )
    assert dictResult["bChangedSinceLastPoll"] is True
    assert dictResult["listNewWorkflowPaths"] == [_S_OTHER_PATH]


def test_detect_flags_workflow_disappearance():
    """A removed workflow flips bChanged but does not list newcomers."""
    dictCtx = _fdictMakeContext(_FakeDocker())
    listFirst = [_fdictMakeListing(_S_DEMO_PATH, "demo")]
    listSecond = []
    with patch(
        "vaibify.gui.workflowReloadDetector.workflowManager"
        ".flistFindWorkflowsInContainer",
        side_effect=[listFirst, listSecond],
    ):
        workflowReloadDetector.fdictDetectNewlyAvailableWorkflows(
            dictCtx, _S_CONTAINER_ID,
        )
        dictResult = (
            workflowReloadDetector
            .fdictDetectNewlyAvailableWorkflows(
                dictCtx, _S_CONTAINER_ID,
            )
        )
    assert dictResult["bChangedSinceLastPoll"] is True
    assert dictResult["listNewWorkflowPaths"] == []
    assert dictResult["listWorkflows"] == []


def test_detect_initializes_map_when_missing():
    """Helper creates lastDiscoveredWorkflows when absent on dictCtx."""
    dictCtx = {"docker": _FakeDocker(), "workflows": {}}
    with patch(
        "vaibify.gui.workflowReloadDetector.workflowManager"
        ".flistFindWorkflowsInContainer",
        return_value=[],
    ):
        workflowReloadDetector.fdictDetectNewlyAvailableWorkflows(
            dictCtx, _S_CONTAINER_ID,
        )
    assert "lastDiscoveredWorkflows" in dictCtx
    assert (
        dictCtx["lastDiscoveredWorkflows"][_S_CONTAINER_ID]
        == set()
    )
