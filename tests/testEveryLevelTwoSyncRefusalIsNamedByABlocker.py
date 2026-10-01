"""Whatever the Level 2 sync gates refuse for, the blockers must name.

Four refusal states had no blocker, so the Project header's Level 2 cell
read ``attained`` while the scalar gate sat at Level 1 and no row said
why: the committed SHA moved past the verified one, the Zenodo DOI was
unrecorded, the verify ran against the other Zenodo instance than the
project is configured for, and a cache whose counts contradict its own
divergence list. The blocker projection only ever emitted entries for
paths the cache listed as diverged, which none of the four does.

Each case starts from a boringly valid cache for both services (every
file matching, identity fields consistent) and breaks exactly one thing,
so the refusal can only be the one under test. Then the three must
agree: the gate refuses, a blocker exists, and the header cell is not
attained.
"""

import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from tests.syncStatusFixtures import fdictBuildCachedVerify
from vaibify.reproducibility.levelGates import (
    fbWorkflowFullySyncedWithGithub,
    fbWorkflowFullySyncedWithZenodo,
    fdictComputeWorkflowScopeLevelStates,
    flistLevel2Blockers,
)

S_SHA = "a" * 40
LIST_COMPARED = [".vaibify/projects/project.json", "Step/out.csv"]


def _fsRecentIso():
    return (datetime.now(timezone.utc) - timedelta(hours=1)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def _fdictWorkflow(sRepo, sCommittedSha=S_SHA, sZenodoService=None):
    dictWorkflow = {
        "sProjectRepoPath": sRepo,
        "dictRemotes": {
            "github": {
                "sOwner": "someone", "sRepo": "something",
                "sCommittedSha": sCommittedSha,
            },
            "zenodo": {"sDoi": "10.5281/zenodo.1"},
        },
        "listSteps": [{
            "sName": "Step", "sDirectory": "Step",
            "saOutputDataFiles": ["out.csv"], "saPlotFiles": [],
        }],
        "dictAiProvenance": {
            "listDeclaredModels": [{
                "sVendor": "vendor", "sModelId": "model",
                "sUseStartDate": "2026-01-01",
                "sUseEndDate": "2026-01-02", "bOpenWeights": False,
            }],
            "dictPersonalLayer": {"sStatus": "none"},
        },
    }
    if sZenodoService:
        dictWorkflow["sZenodoService"] = sZenodoService
    return dictWorkflow


def _fnWriteCaches(sRepo, dictGithubIdentity=None, dictZenodoIdentity=None,
                   iGithubMatchingOverride=None):
    dictGithubFields = {"sCommittedShaVerified": S_SHA}
    dictGithubFields.update(dictGithubIdentity or {})
    dictZenodoFields = {
        "sZenodoDoi": "10.5281/zenodo.1", "sEndpointVerified": "sandbox",
    }
    dictZenodoFields.update(dictZenodoIdentity or {})
    dictAll = {
        "github": fdictBuildCachedVerify(
            sService="github", listComparedPaths=LIST_COMPARED,
            sLastVerified=_fsRecentIso(),
            iMatchingOverride=iGithubMatchingOverride,
            **dictGithubFields),
        "zenodo": fdictBuildCachedVerify(
            sService="zenodo", listComparedPaths=LIST_COMPARED,
            sLastVerified=_fsRecentIso(), **dictZenodoFields),
    }
    sDirectory = os.path.join(sRepo, ".vaibify")
    os.makedirs(sDirectory, exist_ok=True)
    with open(os.path.join(sDirectory, "syncStatus.json"), "w") as fileOut:
        json.dump(dictAll, fileOut)


def _fdictHeaderCell(dictWorkflow, sRepo):
    """Return (sync criteria, header cell) for the workflow.

    ``missing-ai-declaration-step`` is left out of the criteria: the
    fixture has no AI Declaration step, and that criterion is homed on
    its ghost row, never counted at workflow scope.
    """
    listBlockers = flistLevel2Blockers(dictWorkflow, sRepo)
    listCriteria = [
        d["sCriterion"] for d in listBlockers
        if d["sCriterion"] != "missing-ai-declaration-step"
    ]
    return listCriteria, fdictComputeWorkflowScopeLevelStates(
        dictWorkflow, listBlockers, [])["s2"]


@pytest.fixture
def sRepo(tmp_path):
    sPath = str(tmp_path / "project")
    os.makedirs(sPath)
    return sPath


def testAValidCacheForBothServicesHoldsTheGatesAndTheCell(sRepo):
    """The baseline each refusal below is a one-field departure from."""
    _fnWriteCaches(sRepo)
    dictWorkflow = _fdictWorkflow(sRepo)
    assert fbWorkflowFullySyncedWithGithub(dictWorkflow, sRepo) is True
    assert fbWorkflowFullySyncedWithZenodo(dictWorkflow, sRepo) is True
    listCriteria, dictCell = _fdictHeaderCell(dictWorkflow, sRepo)
    assert listCriteria == []
    assert dictCell["sState"] == "attained"


def _fnAssertGateBlockerAndCellRefuseTogether(
    dictWorkflow, sRepo, fbGate, sService,
):
    assert fbGate(dictWorkflow, sRepo) is False
    listCriteria, dictCell = _fdictHeaderCell(dictWorkflow, sRepo)
    assert listCriteria, f"the {sService} gate refused and named nothing"
    assert dictCell["sState"] != "attained", dictCell
    return listCriteria


@pytest.mark.falsification
def testACommitPushedSinceTheVerifyIsNamed(sRepo):
    """Kills: levelGates._flistGithubLevel2Blockers: the unexplained-
    refusal fallback `return [_fdictGithubVerifyStaleBlocker()]` replaced
    by `return listBlockers`."""
    _fnWriteCaches(sRepo, dictGithubIdentity={
        "sCommittedShaVerified": "b" * 40})
    listCriteria = _fnAssertGateBlockerAndCellRefuseTogether(
        _fdictWorkflow(sRepo), sRepo,
        fbWorkflowFullySyncedWithGithub, "github")
    assert "github-verify-stale" in listCriteria


@pytest.mark.falsification
def testAnUnrecordedZenodoDoiIsNamed(sRepo):
    """Kills: levelGates._flistZenodoLevel2Blockers: the unexplained-
    refusal fallback replaced by `return listBlockers`."""
    _fnWriteCaches(sRepo, dictZenodoIdentity={"sZenodoDoi": None})
    listCriteria = _fnAssertGateBlockerAndCellRefuseTogether(
        _fdictWorkflow(sRepo), sRepo,
        fbWorkflowFullySyncedWithZenodo, "zenodo")
    assert "not-in-zenodo-deposit" in listCriteria


def testAZenodoVerifyAgainstTheOtherInstanceIsNamed(sRepo):
    _fnWriteCaches(sRepo, dictZenodoIdentity={"sEndpointVerified": "sandbox"})
    listCriteria = _fnAssertGateBlockerAndCellRefuseTogether(
        _fdictWorkflow(sRepo, sZenodoService="zenodo"), sRepo,
        fbWorkflowFullySyncedWithZenodo, "zenodo")
    assert "zenodo-verify-stale" in listCriteria


def testACacheWhoseCountsContradictItsOwnListIsNamed(sRepo):
    _fnWriteCaches(sRepo, iGithubMatchingOverride=1)
    listCriteria = _fnAssertGateBlockerAndCellRefuseTogether(
        _fdictWorkflow(sRepo), sRepo,
        fbWorkflowFullySyncedWithGithub, "github")
    assert "github-verify-stale" in listCriteria
