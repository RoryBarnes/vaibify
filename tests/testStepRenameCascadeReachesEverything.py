"""A step rename reaches every record that names the old directory.

Three things the cascade missed, each leaving a record that points at a
directory that no longer exists or a verification marker under the wrong
name: test declaration paths, per-file sync records, and a marker left
under the new name when a later stage failed and the move was undone.
"""

import json

import pytest

from vaibify.gui import stepRename
from tests.testStepRename import (
    S_WORKFLOW_PATH, _FakeDocker, _FakeRepoFiles, _fdictWorkflow,
)

S_MARKER_OLD = ".vaibify/test_markers/study/OldStep.json"
S_MARKER_NEW = ".vaibify/test_markers/study/NewStep.json"
DICT_MOVE_WORKS = {
    "test -e": (1, ""), "test -d": (0, ""), "git mv": (0, ""),
}


def _fdictRename(dictWorkflow, filesRepo=None):
    dictPlan = stepRename.fdictPlanStepRename(dictWorkflow, 0, "NewStep")
    return stepRename.fdictApplyStepRename(
        _FakeDocker(DICT_MOVE_WORKS), "cid", filesRepo or _FakeRepoFiles(),
        dictWorkflow, 0, dictPlan, S_WORKFLOW_PATH,
    )


@pytest.mark.falsification
def testTestDeclarationPathsFollowTheRenamedDirectory():
    """Kills: leaving dictTests paths naming the old directory."""
    dictWorkflow = _fdictWorkflow({"dictTests": {
        "dictQualitative": {
            "sFilePath": "OldStep/tests/testQualitative.py",
            "sStandardsPath": "OldStep/tests/standards.json",
        },
        "dictQuantitative": {"sFilePath": "tests/testQuantitative.py"},
        "dictIntegrity": "not a mapping",
    }})
    _fdictRename(dictWorkflow)
    dictTests = dictWorkflow["listSteps"][0]["dictTests"]
    assert dictTests["dictQualitative"]["sFilePath"] == (
        "NewStep/tests/testQualitative.py")
    assert dictTests["dictQualitative"]["sStandardsPath"] == (
        "NewStep/tests/standards.json")
    assert dictTests["dictQuantitative"]["sFilePath"] == (
        "tests/testQuantitative.py")


@pytest.mark.falsification
def testSyncRecordsFollowTheRenamedDirectory():
    """Kills: leaving per-file sync records keyed by the old path."""
    dictWorkflow = _fdictWorkflow(dictSyncStatus={
        "OldStep/figure.pdf": {"bOverleaf": True},
        "OldStepper/other.pdf": {"bZenodo": True},
        "local.csv": {"bGithub": True},
    })
    _fdictRename(dictWorkflow)
    assert dictWorkflow["dictSyncStatus"] == {
        "NewStep/figure.pdf": {"bOverleaf": True},
        "OldStepper/other.pdf": {"bZenodo": True},
        "local.csv": {"bGithub": True},
    }


@pytest.mark.falsification
def testAFailedLaterStageRestoresTheMarkerUnderTheOldName(monkeypatch):
    """Kills: undoing the directory move but leaving the marker renamed."""
    def fnFailManifest(filesRepo, dictPlan):
        raise OSError("manifest write failed")

    monkeypatch.setattr(stepRename, "_fbRewriteManifestPaths", fnFailManifest)
    sMarker = json.dumps({"sLabel": "A01", "sDirectory": "OldStep"})
    filesRepo = _FakeRepoFiles({S_MARKER_OLD: sMarker})
    dictWorkflow = _fdictWorkflow()
    with pytest.raises(OSError):
        _fdictRename(dictWorkflow, filesRepo)
    assert S_MARKER_NEW not in filesRepo.dictFiles
    assert json.loads(filesRepo.dictFiles[S_MARKER_OLD]) == {
        "sLabel": "A01", "sDirectory": "OldStep"}
    assert dictWorkflow["listSteps"][0]["sName"] == "OldStep"
