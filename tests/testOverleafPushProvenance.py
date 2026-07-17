"""Tests for the Overleaf push provenance recording (syncRoutes).

The Stage-4 figure-freeze machinery (the push manifest and
``dictRemotes.overleaf.sLastPushCommit``) originally shipped with
green unit tests but no production writer — every consumer (the L2
figure-freeze blockers, the arXiv gate, the Overleaf/arXiv verify
scope) read an empty push list in a live workflow. These tests pin
the wiring itself: a successful push records the local→remote path
map keyed by the project repo's HEAD and stamps ``sLastPushCommit``
before the workflow save.
"""

import asyncio
from unittest.mock import MagicMock, patch

from vaibify.gui.routes.syncRoutes import _fnFinalizeOverleafPush
from vaibify.reproducibility import overleafSync


def _fdictBuildWorkflow(sRepo):
    """Return a minimal workflow with an Overleaf binding."""
    return {
        "sProjectRepoPath": sRepo,
        "sOverleafProjectId": "ol1234",
        "dictRemotes": {"overleaf": {"sProjectId": "ol1234"}},
        "dictSyncStatus": {},
        "listSteps": [],
    }


def _fnRunFinalize(dictCtx, dictWorkflow, listFilePaths, sHeadSha):
    """Drive _fnFinalizeOverleafPush with the container hops mocked."""
    with patch(
        "vaibify.gui.containerGit.fsGitHeadShaInContainer",
        return_value=sHeadSha,
    ), patch(
        "vaibify.gui.routes.syncRoutes._fnPersistPostPushDigests",
    ):
        asyncio.run(_fnFinalizeOverleafPush(
            dictCtx, "cid1", dictWorkflow, "ol1234",
            listFilePaths, "figures",
        ))


def test_finalize_push_records_manifest_and_commit(tmp_path):
    """A successful push writes the push manifest and stamps the commit."""
    sRepo = str(tmp_path)
    dictWorkflow = _fdictBuildWorkflow(sRepo)
    mockSave = MagicMock()
    dictCtx = {"docker": MagicMock(), "save": mockSave}
    _fnRunFinalize(
        dictCtx, dictWorkflow,
        [sRepo + "/Plot/A12/foo.pdf"], "headsha123",
    )
    assert (
        dictWorkflow["dictRemotes"]["overleaf"]["sLastPushCommit"]
        == "headsha123"
    )
    assert overleafSync.fdictOverleafRemotePathsAt(
        sRepo, "headsha123",
    ) == {"Plot/A12/foo.pdf": "figures/foo.pdf"}
    mockSave.assert_called_once()


def test_finalize_push_survives_unresolvable_head(tmp_path):
    """No HEAD (exec contention) skips provenance but still finalizes.

    The push itself succeeded; the honest degradation is figures
    reading not-frozen, never a failed push response.
    """
    sRepo = str(tmp_path)
    dictWorkflow = _fdictBuildWorkflow(sRepo)
    mockSave = MagicMock()
    dictCtx = {"docker": MagicMock(), "save": mockSave}
    _fnRunFinalize(
        dictCtx, dictWorkflow, [sRepo + "/Plot/foo.pdf"], "",
    )
    assert (
        "sLastPushCommit"
        not in dictWorkflow["dictRemotes"]["overleaf"]
    )
    assert overleafSync.fdictOverleafRemotePathsAt(sRepo, "") == {}
    mockSave.assert_called_once()


def test_finalize_push_backfills_binding_when_remotes_entry_absent(
    tmp_path,
):
    """Stamping must not erase the Overleaf binding.

    ``fnMigrateLegacyRemotes`` never overwrites an existing
    ``dictRemotes.overleaf`` entry, so an entry created here with only
    ``sLastPushCommit`` would permanently mask the legacy
    ``sOverleafProjectId`` — the binding must be carried over.
    """
    sRepo = str(tmp_path)
    dictWorkflow = _fdictBuildWorkflow(sRepo)
    dictWorkflow["dictRemotes"] = {}
    dictCtx = {"docker": MagicMock(), "save": MagicMock()}
    _fnRunFinalize(
        dictCtx, dictWorkflow, [sRepo + "/Plot/foo.pdf"], "headsha123",
    )
    dictOverleaf = dictWorkflow["dictRemotes"]["overleaf"]
    assert dictOverleaf["sProjectId"] == "ol1234"
    assert dictOverleaf["sLastPushCommit"] == "headsha123"
