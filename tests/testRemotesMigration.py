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
    """A migrated workflow's remotes verify without a config 409.

    The Overleaf verify additionally requires a recorded push (its
    comparison set is the pushed-figure list, hashed at the remote
    paths the push flattened them to), so the overleaf case arranges
    that record — migration alone clears only the *config* guard and
    never invents push provenance.
    """
    import hashlib
    from vaibify.reproducibility import overleafSync
    sRepo = _fsBuildRepoWithManifest(tmp_path)
    baPlotContent = b"%PDF-1.4 migrated figure\n"
    sPlotSha = hashlib.sha256(baPlotContent).hexdigest()
    os.makedirs(os.path.join(sRepo, "d"), exist_ok=True)
    with open(os.path.join(sRepo, "d", "f.pdf"), "wb") as fileHandle:
        fileHandle.write(baPlotContent)
    dictWorkflow = _fdictLegacyKeyedWorkflow()
    fnMigrateLegacyRemotes(dictWorkflow)
    dictRemoteHashes = {"d/f.pdf": sPlotSha}
    if sService == "zenodo":
        # A Zenodo deposit is flat: the verify requests DEPOSIT keys
        # (basenames) and maps them back onto the compared paths.
        dictRemoteHashes = {"f.pdf": sPlotSha}
    if sService == "overleaf":
        overleafSync.fnRecordOverleafPushManifest(
            sRepo, "commit1", ["d/f.pdf"], "figures",
        )
        dictWorkflow["dictRemotes"]["overleaf"][
            "sLastPushCommit"] = "commit1"
        dictRemoteHashes = {"figures/f.pdf": sPlotSha}
    sMirrorModule = DICT_MIRROR_MODULE_BY_SERVICE[sService]
    with patch(
        f"vaibify.reproducibility.{sMirrorModule}.fdictFetchRemoteHashes",
        return_value=dictRemoteHashes,
    ):
        dictStatus = scheduledReverify.fdictVerifyRemoteService(
            sRepo, dictWorkflow, sService,
        )
    # The point of this test is that migration clears the CONFIG
    # guard: the verify RAN instead of raising ReverifyConfigError.
    # It asserted an exact count and a wholly empty divergence list
    # until 2026-08-26, when the comparison set gained the
    # reproducibility envelope. This fixture's repo carries a
    # MANIFEST.sha256, and the stubbed remote returns hashes for one
    # figure only, so that file now diverges correctly -- an artifact
    # of the stub, not of migration. Asserting the FIGURE's outcome
    # keeps a real check that cannot be satisfied vacuously, without
    # coupling an unrelated test to the envelope definition.
    setDiverged = {
        dictEntry["sPath"] for dictEntry in dictStatus["listDiverged"]
    }
    assert "d/f.pdf" not in setDiverged, dictStatus["listDiverged"]
    assert dictStatus["iTotalFiles"] >= 1
    assert dictStatus["iMatching"] >= 1


def test_migrated_remotes_round_trip_through_save():
    """Declared bindings persist; produced identity leaves the file.

    Until the sidecar migration (2026-08-27) the saved project.json
    carried the derived ``dictRemotes.zenodo`` identity. That identity
    is advanced by every publish, so persisting it in the definition
    is what made the archived copy diverge; it now rides the
    bookkeeping split instead, and the declared Overleaf binding is
    what remains in the file.
    """
    mockDocker = MagicMock()
    dictWorkflow = _fdictMinimalWorkflow(
        sOverleafProjectId="abc123",
        sZenodoDepositionId="42",
    )
    with patch(
        "vaibify.gui.workflowManager._fnWriteSidecarBookkeeping",
    ) as mockSidecar, patch(
        "vaibify.gui.stateManager.fnSaveStateToContainer",
    ), patch(
        "vaibify.gui.stateManager.fnEnsureVaibifyGitignore",
    ):
        fnSaveWorkflowToContainer(
            mockDocker, "cid", dictWorkflow,
            sWorkflowPath="/workspace/repo/.vaibify/projects/w.json",
        )
    baPayload = next(
        tCall.args[2] for tCall in mockDocker.fnWriteFile.call_args_list
        if tCall.args[1].endswith("projects/w.json")
    )
    dictWritten = json.loads(baPayload.decode("utf-8"))
    assert dictWritten["dictRemotes"]["overleaf"] == {
        "sProjectId": "abc123",
    }
    assert "zenodo" not in dictWritten["dictRemotes"]
    assert "sZenodoDepositionId" not in dictWritten
    assert dictWritten["sOverleafProjectId"] == "abc123"
    dictBookkeeping = mockSidecar.call_args[0][4]
    assert dictBookkeeping["sZenodoDepositionId"] == "42"
    assert dictBookkeeping[
        "dictRemoteBookkeeping"]["zenodo"]["sRecordId"] == "42"
