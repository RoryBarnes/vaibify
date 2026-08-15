"""Remote-data records have identity: unique ``sPath`` per step.

``sPath`` is how the digest refresh and the record-unit merge attach
a hash to a record. Before slice 4 the validation checked only the
path BOUNDARY, so two records could share a path and the last
occurrence silently claimed the other's digest — the same wrong-owner
failure the step-id conflict guard closes one level up. Identity is
validated before anything merges (spec §4.5).
"""

import pytest

from vaibify.gui.pipelineUtils import fsDescribeRemoteDataPathConflict
from vaibify.gui.workflowManager import (
    fnSaveWorkflowToContainer,
    fsDescribeValidationFailure,
)


def _fdictWorkflowWithRemotePaths(listPaths):
    return {
        "sPlotDirectory": "plots",
        "listSteps": [{
            "sStepId": "pull-archive",
            "sName": "Pull Archive",
            "sDirectory": "PullArchive",
            "saPlotCommands": [],
            "saPlotFiles": [],
            "listRemoteData": [
                {"sPath": sPath, "sSourceUrl": "https://archive.example"}
                for sPath in listPaths
            ],
        }],
    }


class _RefusingDocker:
    """Any touch is a test failure: the save must refuse first."""

    def __getattr__(self, sName):
        raise AssertionError(
            f"the save touched the container ({sName}) despite a "
            "record-identity conflict; identity must be validated "
            "before either file is written"
        )


def testUniqueRecordPathsPassValidation():
    dictWorkflow = _fdictWorkflowWithRemotePaths(
        ["data/first.fits", "data/second.fits"],
    )
    assert fsDescribeRemoteDataPathConflict(dictWorkflow) == ""
    assert fsDescribeValidationFailure(dictWorkflow) == ""


@pytest.mark.falsification
def testDuplicateRecordPathsAreNamedAtValidation():
    """Kills: refusing two records that share one path.

    Without identity, ``_fbApplyRemoteDataHashes`` writes the same
    digest into both records and the record-unit merge cannot tell
    which assertion the researcher meant — a manufactured record with
    no symptom.
    """
    dictWorkflow = _fdictWorkflowWithRemotePaths(
        ["data/pull.fits", "data/pull.fits"],
    )
    sConflict = fsDescribeRemoteDataPathConflict(dictWorkflow)
    assert "data/pull.fits" in sConflict
    assert "Step01" in sConflict
    assert fsDescribeValidationFailure(dictWorkflow) == sConflict


def testNormalizedVariantsOfOnePathCollide():
    """``data/a.csv`` and ``./data/a.csv`` are the same file on disk."""
    dictWorkflow = _fdictWorkflowWithRemotePaths(
        ["data/pull.fits", "./data/pull.fits"],
    )
    assert fsDescribeRemoteDataPathConflict(dictWorkflow) != ""


def testTwoStepsMayDeclareTheSamePath():
    """Identity is per step; a sibling step's record is not a collision."""
    dictWorkflow = _fdictWorkflowWithRemotePaths(["data/pull.fits"])
    dictSecond = dict(dictWorkflow["listSteps"][0])
    dictSecond.update({
        "sStepId": "pull-again", "sName": "Pull Again",
        "sDirectory": "PullAgain",
    })
    dictWorkflow["listSteps"].append(dictSecond)
    assert fsDescribeRemoteDataPathConflict(dictWorkflow) == ""


@pytest.mark.falsification
def testTheSaveRefusesADuplicateBeforeTouchingTheContainer():
    """Kills: the save-path enforcement, independently of validation.

    A route that skips ``fsDescribeValidationFailure`` still cannot
    persist a workflow whose records lack identity: the save raises
    before either file is written, and the container is never
    touched.
    """
    dictWorkflow = _fdictWorkflowWithRemotePaths(
        ["data/pull.fits", "data/pull.fits"],
    )
    with pytest.raises(ValueError, match="data/pull.fits"):
        fnSaveWorkflowToContainer(
            _RefusingDocker(), "cid", dictWorkflow,
            sWorkflowPath="/workspace/repo/project.json",
        )
