"""Tests for the legacy-key -> dictRemotes load-time migration.

Real workflows predate the ``dictRemotes`` schema and carry their
remote bindings in legacy top-level keys (``sOverleafProjectId``,
``sZenodoDepositionId``/``sZenodoDoi``/``sZenodoService``,
``sGithubBaseUrl``). The Level 2 gates and the verify routes read
only ``dictWorkflow["dictRemotes"]``, so without migration those
workflows silently lose their arXiv/Overleaf L2 conjunct and verify
actions 409. ``fnMigrateLegacyRemotes`` bridges the two shapes on
every load and save without ever overwriting explicit entries,
deleting legacy keys, or inventing verify-produced fields.
"""

import copy
import json
import os
from unittest.mock import MagicMock, patch

import pytest

from vaibify.gui.workflowManager import (
    fdictLoadWorkflowFromContainer,
    fnMigrateLegacyRemotes,
    fnSaveWorkflowToContainer,
)
from vaibify.reproducibility import scheduledReverify

DICT_MIRROR_MODULE_BY_SERVICE = {
    "github": "githubMirror",
    "overleaf": "overleafMirror",
    "zenodo": "zenodoClient",
}


def _fdictMinimalWorkflow(**dictExtraFields):
    """Return a minimal valid workflow dict plus any legacy keys."""
    dictWorkflow = {
        "sPlotDirectory": "Plot",
        "listSteps": [{
            "sName": "S1", "sDirectory": "d",
            "saPlotCommands": ["echo"], "saPlotFiles": ["f.pdf"],
        }],
    }
    dictWorkflow.update(dictExtraFields)
    return dictWorkflow


def _fdictLoadThroughMockContainer(dictWorkflow):
    """Round a workflow dict through the mocked container load path."""
    mockDocker = MagicMock()
    mockDocker.fbaFetchFile.return_value = (
        json.dumps(dictWorkflow).encode("utf-8")
    )
    return fdictLoadWorkflowFromContainer(
        mockDocker, "cid", sWorkflowPath="/w.json",
    )


def test_legacy_overleaf_key_gains_remote_entry_on_load():
    dictLoaded = _fdictLoadThroughMockContainer(
        _fdictMinimalWorkflow(sOverleafProjectId="abc123def456"),
    )
    assert dictLoaded["dictRemotes"]["overleaf"] == {
        "sProjectId": "abc123def456",
    }
    assert dictLoaded["sOverleafProjectId"] == "abc123def456"


def test_legacy_zenodo_keys_gain_remote_entry_on_load():
    dictLoaded = _fdictLoadThroughMockContainer(
        _fdictMinimalWorkflow(
            sZenodoDepositionId="98765",
            sZenodoDoi="10.5281/zenodo.98765",
            sZenodoService="sandbox",
        ),
    )
    assert dictLoaded["dictRemotes"]["zenodo"] == {
        "sRecordId": "98765",
        "sDoi": "10.5281/zenodo.98765",
        "sService": "sandbox",
    }
    assert dictLoaded["sZenodoService"] == "sandbox"


def test_zenodo_record_id_derived_from_doi_when_deposit_absent():
    dictWorkflow = _fdictMinimalWorkflow(
        sZenodoLatestDoi="10.5281/zenodo.31415",
    )
    fnMigrateLegacyRemotes(dictWorkflow)
    dictZenodo = dictWorkflow["dictRemotes"]["zenodo"]
    assert dictZenodo["sRecordId"] == "31415"
    assert dictZenodo["sDoi"] == "10.5281/zenodo.31415"
    assert "sService" not in dictZenodo


def test_zenodo_record_id_not_invented_from_foreign_doi():
    dictWorkflow = _fdictMinimalWorkflow(
        sZenodoDoi="10.1000/other.suffix",
    )
    fnMigrateLegacyRemotes(dictWorkflow)
    dictZenodo = dictWorkflow["dictRemotes"]["zenodo"]
    assert dictZenodo == {"sDoi": "10.1000/other.suffix"}


def test_zenodo_record_id_not_invented_from_near_miss_doi():
    dictWorkflow = _fdictMinimalWorkflow(
        sZenodoDoi="10.9999/notzenodo.123",
    )
    fnMigrateLegacyRemotes(dictWorkflow)
    dictZenodo = dictWorkflow["dictRemotes"]["zenodo"]
    assert dictZenodo == {"sDoi": "10.9999/notzenodo.123"}


def test_legacy_github_url_gains_owner_repo_binding_on_load():
    dictLoaded = _fdictLoadThroughMockContainer(
        _fdictMinimalWorkflow(
            sGithubBaseUrl="https://github.com/AnOwner/a-repository",
        ),
    )
    dictGithub = dictLoaded["dictRemotes"]["github"]
    assert dictGithub == {"sOwner": "AnOwner", "sRepo": "a-repository"}
    assert "sCommittedSha" not in dictGithub


def test_unparseable_github_url_invents_no_entry():
    dictWorkflow = _fdictMinimalWorkflow(sGithubBaseUrl="not a url")
    fnMigrateLegacyRemotes(dictWorkflow)
    assert "github" not in dictWorkflow.get("dictRemotes", {})


def test_existing_explicit_remote_entry_is_never_overwritten():
    dictWorkflow = _fdictMinimalWorkflow(
        sOverleafProjectId="legacyProject",
        dictRemotes={"overleaf": {
            "sProjectId": "explicitProject",
            "sLastPushCommit": "deadbeef",
        }},
    )
    fnMigrateLegacyRemotes(dictWorkflow)
    assert dictWorkflow["dictRemotes"]["overleaf"] == {
        "sProjectId": "explicitProject",
        "sLastPushCommit": "deadbeef",
    }


def test_absent_legacy_keys_invent_no_remotes():
    dictWorkflow = _fdictMinimalWorkflow()
    fnMigrateLegacyRemotes(dictWorkflow)
    assert "dictRemotes" not in dictWorkflow


def test_migration_is_idempotent_across_double_application():
    dictWorkflow = _fdictMinimalWorkflow(
        sOverleafProjectId="abc123",
        sZenodoDepositionId="42",
        sGithubBaseUrl="git@github.com:owner/repo.git",
    )
    fnMigrateLegacyRemotes(dictWorkflow)
    dictAfterFirst = copy.deepcopy(dictWorkflow)
    fnMigrateLegacyRemotes(dictWorkflow)
    assert dictWorkflow == dictAfterFirst


def test_migration_preserves_legacy_keys_for_old_readers():
    dictWorkflow = _fdictMinimalWorkflow(
        sOverleafProjectId="abc123",
        sZenodoService="sandbox",
        sZenodoDepositionId="42",
        sGithubBaseUrl="https://github.com/owner/repo",
    )
    fnMigrateLegacyRemotes(dictWorkflow)
    assert dictWorkflow["sOverleafProjectId"] == "abc123"
    assert dictWorkflow["sZenodoService"] == "sandbox"
    assert dictWorkflow["sZenodoDepositionId"] == "42"
    assert dictWorkflow["sGithubBaseUrl"] == (
        "https://github.com/owner/repo"
    )


def _fsBuildRepoWithManifest(tmp_path):
    """Return a temp project repo carrying a one-entry MANIFEST.sha256."""
    sRepo = str(tmp_path / "project")
    os.makedirs(os.path.join(sRepo, "step01"), exist_ok=True)
    sManifestPath = os.path.join(sRepo, "MANIFEST.sha256")
    with open(sManifestPath, "w", encoding="utf-8") as fileManifest:
        fileManifest.write(
            "# SHA-256 manifest of workflow outputs\n"
            f"{'a' * 64}  step01/data.csv\n"
        )
    return sRepo


def _fdictLegacyKeyedWorkflow():
    """Return a workflow whose remotes exist only as legacy keys."""
    return _fdictMinimalWorkflow(
        sOverleafProjectId="project1234",
        sZenodoDepositionId="98765",
        sZenodoService="sandbox",
        sGithubBaseUrl="https://github.com/owner/repo",
    )


@pytest.mark.parametrize(
    "sService", sorted(DICT_MIRROR_MODULE_BY_SERVICE),
)
def test_unmigrated_legacy_workflow_hits_verify_409_guard(
    tmp_path, sService,
):
    sRepo = _fsBuildRepoWithManifest(tmp_path)
    with pytest.raises(scheduledReverify.ReverifyConfigError):
        scheduledReverify.fdictVerifyRemoteService(
            sRepo, _fdictLegacyKeyedWorkflow(), sService,
        )


@pytest.mark.parametrize(
    "sService", sorted(DICT_MIRROR_MODULE_BY_SERVICE),
)
def test_migrated_legacy_workflow_unblocks_verify_409_guard(
    tmp_path, sService,
):
    sRepo = _fsBuildRepoWithManifest(tmp_path)
    dictWorkflow = _fdictLegacyKeyedWorkflow()
    fnMigrateLegacyRemotes(dictWorkflow)
    sMirrorModule = DICT_MIRROR_MODULE_BY_SERVICE[sService]
    with patch(
        f"vaibify.reproducibility.{sMirrorModule}.fdictFetchRemoteHashes",
        return_value={"step01/data.csv": "a" * 64},
    ):
        dictStatus = scheduledReverify.fdictVerifyRemoteService(
            sRepo, dictWorkflow, sService,
        )
    assert dictStatus["iTotalFiles"] == 1
    assert dictStatus["iMatching"] == 1
    assert dictStatus["listDiverged"] == []


def test_migrated_remotes_round_trip_through_save():
    mockDocker = MagicMock()
    dictWorkflow = _fdictMinimalWorkflow(
        sOverleafProjectId="abc123",
        sZenodoDepositionId="42",
    )
    fnSaveWorkflowToContainer(
        mockDocker, "cid", dictWorkflow, sWorkflowPath="/w.json",
    )
    (_, _, baPayload), _ = mockDocker.fnWriteFile.call_args
    dictWritten = json.loads(baPayload.decode("utf-8"))
    assert dictWritten["dictRemotes"]["overleaf"] == {
        "sProjectId": "abc123",
    }
    assert dictWritten["dictRemotes"]["zenodo"]["sRecordId"] == "42"
    assert dictWritten["sOverleafProjectId"] == "abc123"
