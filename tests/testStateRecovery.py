"""Tests for state.json crash recovery: .bak fallback + quarantine.

Motivated by a real incident: a system crash mid-write left
state.json truncated, the next load returned None, and the marker
bootstrap silently rebuilt verifications without the user's
``sUser="passed"`` attestations. The hardening this exercises:

1. Atomic write (``.tmp`` + ``mv``) prevents the torn file in the
   first place.
2. ``.bak`` checkpoint preserves the previous good state for fallback.
3. Quarantine renames a corrupt file so its contents survive for
   hand-recovery instead of being overwritten by bootstrap.
4. ``ftLoadStateWithStatus`` reports the recovery path so the
   workflow-load handler can surface a toast — silent recovery is
   how the original incident went unnoticed.
"""

import json
from unittest.mock import MagicMock

from vaibify.gui import stateManager


def _fnBuildRecoveryMock(dictByPath, listMissing=()):
    """Mock fbaFetchFile + ftResultExecuteCommand for recovery paths.

    ``dictByPath`` maps absolute container paths to bytes; missing
    entries raise FileNotFoundError. Shell commands (mv, cp, test)
    succeed by default; tests override behaviour by reading the
    recorded command list.
    """
    mockDocker = MagicMock()
    listCommands = []

    def _fFetch(_sContainerId, sPath):
        if sPath in listMissing:
            raise FileNotFoundError(sPath)
        if sPath in dictByPath:
            return dictByPath[sPath]
        raise FileNotFoundError(sPath)

    def _fExec(_sContainerId, sCommand):
        listCommands.append(sCommand)
        return (0, "")

    mockDocker.fbaFetchFile.side_effect = _fFetch
    mockDocker.ftResultExecuteCommand.side_effect = _fExec
    return mockDocker, listCommands


def test_load_returns_loaded_when_primary_parses_cleanly():
    """The clean-load path reports sStatus="loaded" with no recovery."""
    sPath = "/workspace/Project/.vaibify/state.json"
    dictGood = {
        "iStateSchemaVersion": 1,
        "dictStepState": {"A": {"dictVerification": {"sUser": "passed"}}},
    }
    mockDocker, _listCmds = _fnBuildRecoveryMock(
        {sPath: json.dumps(dictGood).encode("utf-8")},
    )
    dictResult, sStatus = stateManager.ftLoadStateWithStatus(
        mockDocker, "cid", sPath,
    )
    assert sStatus == "loaded"
    assert dictResult["dictStepState"]["A"]["dictVerification"]["sUser"] == "passed"


def test_load_falls_back_to_bak_when_primary_corrupt():
    """A truncated state.json is recovered from the .bak checkpoint."""
    sPath = "/workspace/Project/.vaibify/state.json"
    sBakPath = sPath + ".bak"
    dictBak = {
        "iStateSchemaVersion": 1,
        "dictStepState": {
            "A": {"dictVerification": {"sUser": "passed"}},
            "B": {"dictVerification": {"sUser": "passed"}},
        },
    }
    mockDocker, listCmds = _fnBuildRecoveryMock({
        sPath: b"{not json -- truncated mid-write",
        sBakPath: json.dumps(dictBak).encode("utf-8"),
    })
    dictResult, sStatus = stateManager.ftLoadStateWithStatus(
        mockDocker, "cid", sPath,
    )
    assert sStatus == "loaded-from-bak"
    assert dictResult["dictStepState"]["A"]["dictVerification"]["sUser"] == "passed"
    assert dictResult["dictStepState"]["B"]["dictVerification"]["sUser"] == "passed"
    assert any("mv " in sCmd and ".corrupted-" in sCmd for sCmd in listCmds), (
        "Corrupt primary must be quarantined before falling back."
    )


def test_load_falls_back_to_bak_when_primary_missing():
    """A missing primary recovers from .bak without quarantine."""
    sPath = "/workspace/Project/.vaibify/state.json"
    sBakPath = sPath + ".bak"
    dictBak = {
        "iStateSchemaVersion": 1,
        "dictStepState": {"A": {"dictVerification": {"sUser": "passed"}}},
    }
    mockDocker, listCmds = _fnBuildRecoveryMock(
        {sBakPath: json.dumps(dictBak).encode("utf-8")},
        listMissing=(sPath,),
    )
    dictResult, sStatus = stateManager.ftLoadStateWithStatus(
        mockDocker, "cid", sPath,
    )
    assert sStatus == "loaded-from-bak"
    assert dictResult is not None
    assert not any(".corrupted-" in sCmd for sCmd in listCmds), (
        "Missing (not corrupt) primary must not be quarantined."
    )


def test_load_returns_corrupted_when_both_files_unparseable():
    """Both files corrupt: both quarantined, status='corrupted', dict None."""
    sPath = "/workspace/Project/.vaibify/state.json"
    sBakPath = sPath + ".bak"
    mockDocker, listCmds = _fnBuildRecoveryMock({
        sPath: b"{not json",
        sBakPath: b"also not json",
    })
    dictResult, sStatus = stateManager.ftLoadStateWithStatus(
        mockDocker, "cid", sPath,
    )
    assert sStatus == "corrupted"
    assert dictResult is None
    listQuarantine = [sCmd for sCmd in listCmds if ".corrupted-" in sCmd]
    assert len(listQuarantine) == 2, (
        "Both corrupt files must be quarantined for hand-recovery."
    )


def test_load_returns_missing_when_neither_file_exists():
    """The fresh-checkout case: status='missing', no quarantine."""
    sPath = "/workspace/Project/.vaibify/state.json"
    sBakPath = sPath + ".bak"
    mockDocker, listCmds = _fnBuildRecoveryMock(
        {}, listMissing=(sPath, sBakPath),
    )
    dictResult, sStatus = stateManager.ftLoadStateWithStatus(
        mockDocker, "cid", sPath,
    )
    assert sStatus == "missing"
    assert dictResult is None
    assert not listCmds, (
        "Fresh checkout must not quarantine non-existent files."
    )


def test_save_writes_via_temp_then_renames():
    """Save sequence: write .tmp, copy old → .bak, mv .tmp → state.json."""
    mockDocker, listCmds = _fnBuildRecoveryMock({})
    listWrites = []
    mockDocker.fnWriteFile.side_effect = (
        lambda _cid, sPath, baBody: listWrites.append((sPath, baBody))
    )
    dictState = stateManager.fdictBuildEmptyState()
    stateManager.fnSaveStateToContainer(
        mockDocker, "cid", "/p/.vaibify/state.json", dictState,
    )
    assert listWrites
    assert listWrites[0][0] == "/p/.vaibify/state.json.tmp"
    assert any("cp -f" in sCmd and "/state.json'" in sCmd
               and "/state.json.bak" in sCmd for sCmd in listCmds)
    assert any("mv -f" in sCmd and "/state.json.tmp" in sCmd
               and "/state.json'" in sCmd for sCmd in listCmds)


def test_save_skips_checkpoint_copy_when_state_absent():
    """First save on a fresh checkout still succeeds without a prior file."""
    mockDocker, listCmds = _fnBuildRecoveryMock({})
    listWrites = []
    mockDocker.fnWriteFile.side_effect = (
        lambda _cid, sPath, baBody: listWrites.append((sPath, baBody))
    )
    stateManager.fnSaveStateToContainer(
        mockDocker, "cid", "/p/.vaibify/state.json",
        stateManager.fdictBuildEmptyState(),
    )
    sCheckpoint = next(sCmd for sCmd in listCmds if "cp -f" in sCmd)
    assert "if [ -f " in sCheckpoint, (
        "Checkpoint must guard the cp with an existence test so a "
        "fresh checkout does not error out."
    )


def test_load_does_not_quarantine_clean_primary():
    """A clean primary leaves no .corrupted-* sidecar behind."""
    sPath = "/workspace/Project/.vaibify/state.json"
    mockDocker, listCmds = _fnBuildRecoveryMock(
        {sPath: b'{"iStateSchemaVersion": 1, "dictStepState": {}}'},
    )
    stateManager.ftLoadStateWithStatus(mockDocker, "cid", sPath)
    assert not any(".corrupted-" in sCmd for sCmd in listCmds), (
        "A clean load must not touch the filesystem via mv."
    )


def test_workflow_load_attaches_recovery_notice_on_fallback():
    """End-to-end: a corrupt state.json surfaces a notice on connect."""
    from vaibify.gui import workflowManager
    sRepo = "/workspace/Project"
    sStatePath = sRepo + "/.vaibify/state.json"
    sBakPath = sStatePath + ".bak"
    sWorkflowPath = sRepo + "/.vaibify/workflows/w.json"
    dictDeclarative = {
        "iWorkflowSchemaVersion": 3,
        "sPlotDirectory": "Plot",
        "listSteps": [{
            "sName": "A", "sDirectory": "A",
            "saPlotCommands": [], "saPlotFiles": [],
        }],
    }
    dictBakState = {
        "iStateSchemaVersion": 1,
        "dictStepState": {
            "A": {"dictVerification": {"sUser": "passed"}},
        },
    }
    dictPaths = {
        sWorkflowPath: json.dumps(dictDeclarative).encode("utf-8"),
        sStatePath: b"{truncated",
        sBakPath: json.dumps(dictBakState).encode("utf-8"),
        sRepo + "/.vaibify/.gitignore": b"state.json\n",
    }
    mockDocker, _listCmds = _fnBuildRecoveryMock(dictPaths)
    dictWorkflow = workflowManager.fdictLoadWorkflowFromContainer(
        mockDocker, "cid", sWorkflowPath=sWorkflowPath,
    )
    dictNotice = dictWorkflow.get("dictStateLoadNotice")
    assert dictNotice is not None
    assert dictNotice["sLevel"] == "warning"
    assert "recovered" in dictNotice["sMessage"].lower()
    assert (
        dictWorkflow["listSteps"][0]["dictVerification"]["sUser"]
        == "passed"
    ), "Bak fallback must restore the user-acknowledged status."


def test_workflow_load_notice_stripped_on_save():
    """The transient notice must not leak into persisted workflow.json."""
    from vaibify.gui import workflowManager
    mockDocker = MagicMock()
    listWrites = []
    mockDocker.fnWriteFile.side_effect = (
        lambda _cid, sPath, baBody: listWrites.append((sPath, baBody))
    )
    mockDocker.ftResultExecuteCommand.return_value = (0, "")
    dictWorkflow = {
        "iWorkflowSchemaVersion": 3,
        "sPlotDirectory": "Plot",
        "sProjectRepoPath": "/workspace/Project",
        "dictStateLoadNotice": {
            "sLevel": "warning", "sMessage": "transient",
        },
        "listSteps": [{
            "sName": "A", "sDirectory": "A",
            "saPlotCommands": [], "saPlotFiles": [],
        }],
    }
    workflowManager.fnSaveWorkflowToContainer(
        mockDocker, "cid", dictWorkflow,
        sWorkflowPath="/workspace/Project/.vaibify/workflows/w.json",
    )
    for sPath, baPayload in listWrites:
        if sPath.endswith("/workflows/w.json"):
            dictPersisted = json.loads(baPayload.decode("utf-8"))
            assert "dictStateLoadNotice" not in dictPersisted
            return
    raise AssertionError("Save did not write workflow.json")
