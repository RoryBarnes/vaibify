"""The project.json / sidecar split: the definition can match its archive.

The Zenodo archive uploads ``project.json`` and then records what it
published. Until 2026-08-27 that record was written INTO
``project.json`` — deposit id, DOIs, per-file digests — so the local
file always diverged from the copy it had just archived, and
re-archiving minted a new deposit id that changed it again: a
treadmill by construction. These tests pin the structural fix: every
push/archive-produced field rides ``syncBookkeeping`` into the
uncompared ``.vaibify/syncStatus.json`` sidecar, and the serialized
definition is byte-stable across a publish.
"""

import json

import pytest
from unittest.mock import MagicMock, patch

from vaibify.gui import workflowManager
from vaibify.gui.routes.syncRoutes import _fnPersistZenodoPublishRecord
from vaibify.reproducibility import scheduledReverify, syncBookkeeping
from vaibify.reproducibility.repoFiles import HostRepoFiles


_S_WORKFLOW_KEY = ".vaibify/projects/project.json"
_S_WORKFLOW_PATH = "/workspace/repo/.vaibify/projects/project.json"


def _fdictMergedWorkflow():
    """Return a merged-shape workflow with declared remote bindings."""
    return {
        "sPlotDirectory": "plots",
        "sProjectRepoPath": "/workspace/repo",
        "sOverleafProjectId": "abc123",
        "dictRemotes": {
            "overleaf": {"sProjectId": "abc123"},
            "zenodo": {"listRecords": [{"sRecordId": "111"}]},
        },
        "listSteps": [{
            "sStepId": "make-figure",
            "sName": "Make Figure",
            "sDirectory": "MakeFigure",
            "saPlotCommands": ["python plot.py"],
            "saPlotFiles": ["figure.pdf"],
        }],
    }


def _fnSimulateOnePublish(dictWorkflow):
    """Apply exactly the writes a successful Zenodo archive performs."""
    _fnPersistZenodoPublishRecord(
        dictWorkflow,
        {
            "iDepositId": 424242,
            "sDoi": "10.5281/zenodo.424242",
            "sConceptDoi": "10.5281/zenodo.424241",
            "sHtmlUrl": "https://zenodo.example/records/424242",
        },
        "production",
    )
    workflowManager.fnUpdateSyncStatus(
        dictWorkflow, ["MakeFigure/figure.pdf"], "Zenodo",
    )
    workflowManager.fnUpdateZenodoDigests(
        dictWorkflow, {"MakeFigure/figure.pdf": "ab12cd"},
        sZenodoService="production",
    )


@pytest.mark.falsification
def test_a_publish_does_not_change_the_serialized_definition():
    """The treadmill is gone: publish, and project.json bytes hold still.

    Kills: removing any produced key from the extraction registries
    (``T_BOOKKEEPING_TOP_KEYS`` losing ``dictSyncStatus``, or the
    publish-record advance landing in the declarative dict), which
    puts a publish-written field back into the serialized definition
    and re-creates the divergence this migration removed.
    """
    dictWorkflow = _fdictMergedWorkflow()
    sJsonBefore, _dictState, _dictBefore = (
        workflowManager._ftSplitAndSerializeWorkflow(dictWorkflow)
    )
    _fnSimulateOnePublish(dictWorkflow)
    sJsonAfter, _dictState, dictBookkeeping = (
        workflowManager._ftSplitAndSerializeWorkflow(dictWorkflow)
    )
    assert sJsonAfter == sJsonBefore
    assert dictBookkeeping["sZenodoDepositionId"] == "424242"
    assert dictBookkeeping["sZenodoLatestDoi"] == "10.5281/zenodo.424242"
    dictZenodo = dictBookkeeping["dictRemoteBookkeeping"]["zenodo"]
    assert dictZenodo["sRecordId"] == "424242"
    assert dictBookkeeping["dictSyncStatus"][
        "MakeFigure/figure.pdf"]["sZenodoLastPushedDigest"] == "ab12cd"


@pytest.mark.falsification
def test_publish_bookkeeping_does_not_move_the_attestation_fingerprint():
    """A publish records history; it must not redefine the definition.

    Before the split, archiving to Zenodo rewrote ``dictSyncStatus``
    inside the declarative dict, moved the semantic fingerprint, and
    superseded every verification the archive had not touched.

    Kills: dropping the bookkeeping extraction from
    ``fsComputeSemanticWorkflowFingerprint``.
    """
    dictWorkflow = _fdictMergedWorkflow()
    sBefore = workflowManager.fsComputeSemanticWorkflowFingerprint(
        dictWorkflow,
    )
    _fnSimulateOnePublish(dictWorkflow)
    assert workflowManager.fsComputeSemanticWorkflowFingerprint(
        dictWorkflow,
    ) == sBefore


def test_declared_bindings_stay_in_the_definition():
    """Researcher declarations survive extraction; produced fields go."""
    dictDeclarative = _fdictMergedWorkflow()
    _fnSimulateOnePublish(dictDeclarative)
    dictBookkeeping = syncBookkeeping.fdictExtractSyncBookkeeping(
        dictDeclarative,
    )
    assert dictDeclarative["sOverleafProjectId"] == "abc123"
    assert dictDeclarative["dictRemotes"]["overleaf"] == {
        "sProjectId": "abc123",
    }
    assert dictDeclarative["dictRemotes"]["zenodo"] == {
        "listRecords": [{"sRecordId": "111"}],
    }
    for sKey in syncBookkeeping.T_BOOKKEEPING_TOP_KEYS:
        assert sKey not in dictDeclarative
    assert dictBookkeeping["dictRemoteBookkeeping"]["zenodo"][
        "sService"] == "production"


@pytest.mark.falsification
def test_sidecar_values_win_over_legacy_fielded_keys():
    """Restoring an old definition must not roll back the publish record.

    A legacy fielded project.json (or one restored from git history)
    still carries a stale deposit id; the sidecar holds what was
    actually published last. The merge must prefer the sidecar.

    Kills: disabling ``fnMergeSyncBookkeepingIntoWorkflow``, which
    leaves the workflow reading the stale fielded value.
    """
    dictWorkflow = {
        "sZenodoDepositionId": "1",
        "dictSyncStatus": {"old.csv": {"bZenodo": True}},
        "listSteps": [],
    }
    syncBookkeeping.fnMergeSyncBookkeepingIntoWorkflow(
        dictWorkflow,
        {
            "sZenodoDepositionId": "424242",
            "dictSyncStatus": {"new.csv": {"bZenodo": True}},
            "dictRemoteBookkeeping": {
                "zenodo": {"sRecordId": "424242"},
            },
        },
    )
    assert dictWorkflow["sZenodoDepositionId"] == "424242"
    assert "new.csv" in dictWorkflow["dictSyncStatus"]
    assert dictWorkflow["dictRemotes"]["zenodo"]["sRecordId"] == "424242"


def test_round_trip_through_the_real_host_adapter(tmp_path):
    """Write and read the section through ``HostRepoFiles``, not a fake.

    The sidecar file also carries the per-service verify caches; a
    bookkeeping write must carry them through unchanged, and vice
    versa (the two writers share one lock and one document).
    """
    filesRepo = HostRepoFiles(str(tmp_path))
    dictServiceStatus = scheduledReverify._fdictEmptyServiceStatus(
        "github",
    )
    scheduledReverify.fnWriteSyncStatus(filesRepo, dictServiceStatus)
    dictBookkeeping = {"sZenodoDepositionId": "424242"}
    syncBookkeeping.fnWriteSyncBookkeeping(
        filesRepo, _S_WORKFLOW_KEY, dictBookkeeping,
    )
    assert syncBookkeeping.fdictReadSyncBookkeeping(
        filesRepo, _S_WORKFLOW_KEY,
    ) == dictBookkeeping
    assert scheduledReverify.fdictReadCachedSyncStatus(
        filesRepo, "github",
    )["sService"] == "github"
    scheduledReverify.fnWriteSyncStatus(
        filesRepo, dict(dictServiceStatus, iTotalFiles=3),
    )
    assert syncBookkeeping.fdictReadSyncBookkeeping(
        filesRepo, _S_WORKFLOW_KEY,
    ) == dictBookkeeping


def test_an_unchanged_section_is_not_rewritten(tmp_path):
    """A save with unchanged bookkeeping must not churn the file.

    The save path calls the writer on EVERY workflow save; rewriting
    identical bytes would spend a container exec per save and move the
    mtime the poll snapshot watches.
    """
    filesRepo = HostRepoFiles(str(tmp_path))
    dictBookkeeping = {"sZenodoDepositionId": "424242"}
    syncBookkeeping.fnWriteSyncBookkeeping(
        filesRepo, _S_WORKFLOW_KEY, dictBookkeeping,
    )
    listWriteCalls = []
    fnRealWrite = filesRepo.fnWriteJsonAtomic
    filesRepo.fnWriteJsonAtomic = (
        lambda sRelPath, dictPayload: listWriteCalls.append(sRelPath)
        or fnRealWrite(sRelPath, dictPayload)
    )
    syncBookkeeping.fnWriteSyncBookkeeping(
        filesRepo, _S_WORKFLOW_KEY, dict(dictBookkeeping),
    )
    syncBookkeeping.fnWriteSyncBookkeeping(filesRepo, "", {"s": "x"})
    assert listWriteCalls == []


def test_the_sidecar_write_lands_before_the_definition():
    """Ordering is the safety property: sidecar first, project.json after.

    If the sidecar write fails, the save must abort with the previous
    project.json still on disk; stripping the file first and then
    failing the sidecar write would lose the bookkeeping outright.
    """
    listEventOrder = []
    mockDocker = MagicMock()
    mockDocker.fnWriteFile.side_effect = (
        lambda *tArgs: listEventOrder.append("definition")
    )
    with patch(
        "vaibify.gui.workflowManager._fnWriteSidecarBookkeeping",
        side_effect=lambda *tArgs: listEventOrder.append("sidecar"),
    ), patch(
        "vaibify.gui.stateManager.fnSaveStateToContainer",
    ), patch(
        "vaibify.gui.stateManager.fnEnsureVaibifyGitignore",
    ):
        workflowManager.fnSaveWorkflowToContainer(
            mockDocker, "cid", _fdictMergedWorkflow(),
            sWorkflowPath=_S_WORKFLOW_PATH,
        )
    assert listEventOrder[:2] == ["sidecar", "definition"]


def test_load_grafts_the_sidecar_section(tmp_path):
    """A clean definition plus a populated sidecar loads merged.

    The sidecar carries a value no default can produce (the deposit
    id), so its arrival in the loaded dict proves the wire is intact
    end to end — the threaded-parameter trap this repo has hit before.
    """
    filesRepo = HostRepoFiles(str(tmp_path))
    syncBookkeeping.fnWriteSyncBookkeeping(
        filesRepo, _S_WORKFLOW_KEY,
        {
            "sZenodoDepositionId": "424242",
            "dictSyncStatus": {
                "MakeFigure/figure.pdf": {"bZenodo": True},
            },
            "dictRemoteBookkeeping": {
                "zenodo": {"sRecordId": "424242"},
            },
        },
    )
    dictDefinition = _fdictMergedWorkflow()
    del dictDefinition["sProjectRepoPath"]
    mockDocker = MagicMock()
    mockDocker.fbaFetchFile.return_value = json.dumps(
        dictDefinition,
    ).encode("utf-8")
    with patch(
        "vaibify.gui.workflowManager._ffilesContainerRepo",
        return_value=filesRepo,
    ), patch(
        "vaibify.gui.workflowManager._fnLoadAndMergeState",
    ), patch(
        "vaibify.gui.workflowManager._fnDeriveProofLevel",
    ):
        dictLoaded = workflowManager.fdictLoadWorkflowFromContainer(
            mockDocker, "cid", sWorkflowPath=_S_WORKFLOW_PATH,
        )
    assert dictLoaded["sZenodoDepositionId"] == "424242"
    assert dictLoaded["dictSyncStatus"][
        "MakeFigure/figure.pdf"]["bZenodo"] is True
    assert dictLoaded["dictRemotes"]["zenodo"]["sRecordId"] == "424242"
    assert dictLoaded["dictRemotes"]["zenodo"]["listRecords"] == [
        {"sRecordId": "111"},
    ]


def test_a_legacy_fielded_file_loads_intact_before_its_first_save(
    tmp_path,
):
    """Pre-migration projects keep their fielded bookkeeping on load.

    A project.json written by an older hub still carries the keys and
    has no sidecar section yet; the merge must fall back to the file's
    values, or the first post-upgrade load would show every badge
    unsynced and forget the deposit until a save happened to run.
    """
    dictFielded = _fdictMergedWorkflow()
    del dictFielded["sProjectRepoPath"]
    dictFielded["sZenodoDepositionId"] = "593191"
    dictFielded["dictSyncStatus"] = {
        "MakeFigure/figure.pdf": {"bZenodo": True},
    }
    mockDocker = MagicMock()
    mockDocker.fbaFetchFile.return_value = json.dumps(
        dictFielded,
    ).encode("utf-8")
    with patch(
        "vaibify.gui.workflowManager._ffilesContainerRepo",
        return_value=HostRepoFiles(str(tmp_path)),
    ), patch(
        "vaibify.gui.workflowManager._fnLoadAndMergeState",
    ), patch(
        "vaibify.gui.workflowManager._fnDeriveProofLevel",
    ):
        dictLoaded = workflowManager.fdictLoadWorkflowFromContainer(
            mockDocker, "cid", sWorkflowPath=_S_WORKFLOW_PATH,
        )
    assert dictLoaded["sZenodoDepositionId"] == "593191"
    assert dictLoaded["dictSyncStatus"][
        "MakeFigure/figure.pdf"]["bZenodo"] is True
