"""Level 2 must notice that a published file changed on disk since the verify.

The cached verify answers "did the copies agree at verify time". It says
nothing about the file NOW: a published output edited after the verify,
or an output declared after it, passed Level 2 until the next verify
(up to 24 hours), while the Level 3 envelope gate had already been fixed
to compare live hashes against the ones the verify recorded.

A changed or newly declared file is not proven divergent -- nobody
compared it -- so the honest statement is the one the stale cache
already makes: the evidence no longer answers the question, verify
again. The gate and the blockers consult the same predicate, so the
header cell fails with them.
"""

import hashlib
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
BA_OUTPUT = b"1,2\n"


def _fsSha(baContent):
    return hashlib.sha256(baContent).hexdigest()


def _fsRecentIso():
    return (datetime.now(timezone.utc) - timedelta(hours=1)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def _fnWrite(sRepo, sRelative, baContent):
    sPath = os.path.join(sRepo, sRelative)
    os.makedirs(os.path.dirname(sPath), exist_ok=True)
    with open(sPath, "wb") as fileHandle:
        fileHandle.write(baContent)


def _fdictWorkflow(sRepo, listOutputs=("out.csv",)):
    return {
        "sProjectRepoPath": sRepo,
        "dictRemotes": {
            "github": {
                "sOwner": "someone", "sRepo": "something",
                "sCommittedSha": S_SHA,
            },
            "zenodo": {"sDoi": "10.5281/zenodo.1"},
        },
        "listSteps": [{
            "sName": "Step", "sDirectory": "Step",
            "saOutputDataFiles": list(listOutputs), "saPlotFiles": [],
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


def _fnWriteVerifiedCaches(sRepo, dictRecordedHashes):
    """Both services verified Step/out.csv at the recorded hashes."""
    listCompared = sorted(dictRecordedHashes)
    dictIdentity = {
        "github": {"sCommittedShaVerified": S_SHA},
        "zenodo": {
            "sZenodoDoi": "10.5281/zenodo.1", "sEndpointVerified": "sandbox",
        },
    }
    dictAll = {
        sService: fdictBuildCachedVerify(
            sService=sService, listComparedPaths=listCompared,
            sLastVerified=_fsRecentIso(),
            dictComparedHashes=dict(dictRecordedHashes),
            **dictIdentity[sService],
        )
        for sService in ("github", "zenodo")
    }
    sDirectory = os.path.join(sRepo, ".vaibify")
    os.makedirs(sDirectory, exist_ok=True)
    with open(os.path.join(sDirectory, "syncStatus.json"), "w") as fileOut:
        json.dump(dictAll, fileOut)


def _flistCriteriaAndCell(dictWorkflow, sRepo):
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
    _fnWrite(sPath, "Step/out.csv", BA_OUTPUT)
    return sPath


def testAnUnchangedPublishedFileHoldsTheGatesAndTheCell(sRepo):
    """The baseline: the recorded hash IS the live hash."""
    _fnWriteVerifiedCaches(sRepo, {"Step/out.csv": _fsSha(BA_OUTPUT)})
    dictWorkflow = _fdictWorkflow(sRepo)
    assert fbWorkflowFullySyncedWithGithub(dictWorkflow, sRepo) is True
    assert fbWorkflowFullySyncedWithZenodo(dictWorkflow, sRepo) is True
    listCriteria, dictCell = _flistCriteriaAndCell(dictWorkflow, sRepo)
    assert listCriteria == []
    assert dictCell["sState"] == "attained"


@pytest.mark.falsification
def testAPublishedFileEditedAfterTheVerifyDeniesTheGatesAndTheCell(sRepo):
    """Kills: levelGates.fbWorkflowFullySyncedWithGithub: the
    `_fbLevel2UnchangedSinceVerify` conjunct removed (the blockers still
    name it, the gate no longer does)."""
    _fnWriteVerifiedCaches(sRepo, {"Step/out.csv": _fsSha(BA_OUTPUT)})
    _fnWrite(sRepo, "Step/out.csv", b"1,2,EDITED\n")
    dictWorkflow = _fdictWorkflow(sRepo)
    assert fbWorkflowFullySyncedWithGithub(dictWorkflow, sRepo) is False
    assert fbWorkflowFullySyncedWithZenodo(dictWorkflow, sRepo) is False
    listCriteria, dictCell = _flistCriteriaAndCell(dictWorkflow, sRepo)
    assert "github-verify-stale" in listCriteria
    assert "zenodo-verify-stale" in listCriteria
    assert dictCell["sState"] != "attained"


@pytest.mark.falsification
def testAnOutputDeclaredAfterTheVerifyDeniesTheGatesAndTheCell(sRepo):
    """Kills: levelGates._flistGithubLevel2Blockers: the unchanged-since-
    verify check removed from the blocker list (the gate still refuses,
    nothing names it)."""
    _fnWriteVerifiedCaches(sRepo, {"Step/out.csv": _fsSha(BA_OUTPUT)})
    _fnWrite(sRepo, "Step/second.csv", b"3,4\n")
    dictWorkflow = _fdictWorkflow(sRepo, ("out.csv", "second.csv"))
    assert fbWorkflowFullySyncedWithGithub(dictWorkflow, sRepo) is False
    listCriteria, dictCell = _flistCriteriaAndCell(dictWorkflow, sRepo)
    assert "github-verify-stale" in listCriteria
    assert dictCell["sState"] != "attained"
    # Named for what it is, not by the generic "gate refused" fallback.
    listHints = [
        d["sRemediationHint"]
        for d in flistLevel2Blockers(dictWorkflow, sRepo)
        if d["sCriterion"] == "github-verify-stale"
    ]
    assert any("after the last GitHub check" in sHint for sHint in listHints)


def testAnOutputNotYetProducedIsNotClaimedAboutEither(sRepo):
    """Declared but absent on disk: the verify could not compare it, and the
    gate must not read its absence as a change."""
    _fnWriteVerifiedCaches(sRepo, {"Step/out.csv": _fsSha(BA_OUTPUT)})
    dictWorkflow = _fdictWorkflow(sRepo, ("out.csv", "notYet.csv"))
    assert fbWorkflowFullySyncedWithGithub(dictWorkflow, sRepo) is True


def testACachePredatingRecordedHashesKeepsItsVerifiedRow(sRepo):
    """No ``dictComparedHashes`` means nothing to compare against; no
    project loses a verified row by upgrading (the Level 3 gate is the
    one that blocks on such a cache)."""
    _fnWriteVerifiedCaches(sRepo, {"Step/out.csv": _fsSha(BA_OUTPUT)})
    sPath = os.path.join(sRepo, ".vaibify", "syncStatus.json")
    with open(sPath) as fileIn:
        dictAll = json.load(fileIn)
    for dictEntry in dictAll.values():
        dictEntry.pop("dictComparedHashes")
    with open(sPath, "w") as fileOut:
        json.dump(dictAll, fileOut)
    _fnWrite(sRepo, "Step/out.csv", b"edited\n")
    assert fbWorkflowFullySyncedWithGithub(
        _fdictWorkflow(sRepo), sRepo) is True
