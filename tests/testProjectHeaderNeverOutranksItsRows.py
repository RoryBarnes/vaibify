"""The Project header cell must not claim more than its rows do.

Reported by the researcher on 2026-08-30: both Published-copies rows —
GitHub and Zenodo — were orange, and the Project row's Level 2 cell
showed a CHECK above them.

The cause was a projection that narrowed its input set. A remote's
published-copy check fails in two ways, and the workflow-scope L2 cell
counted only one of them: ``_T_WORKFLOW_LEVEL2_BASE_CRITERIA`` listed
``github-verify-stale`` / ``zenodo-verify-stale`` and not
``not-in-github-mirror`` / ``not-in-zenodo-deposit``. So a FRESH verify
that found real divergence emitted its blockers at workflow scope,
``_ftCountWorkflowCriteria`` intersected them away, and the cell
counted 4 of 4.

Two things about this defect are worth carrying forward. The scalar
gate ``_fbComputeLevel2`` was right the whole time — so the header chip
said Level 1 while the cell above the rows showed a check, which is the
more insidious failure: the display disagreed with itself and only the
display was wrong. And the two halves are mutually exclusive by
construction (``_flistGithubLevel2Blockers`` returns the stale blocker
and nothing else when the cache is stale), so counting both can never
double-charge one service — which is why the fix is to widen the set
rather than to special-case it.
"""

import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from tests.syncStatusFixtures import fdictBuildCachedVerify
from vaibify.reproducibility.levelGates import (
    _fbComputeLevel2,
    fdictComputeWorkflowScopeLevelStates,
    flistLevel2Blockers,
)


_LIST_COMPARED = [
    ".vaibify/projects/project.json", "Step/out.csv", "MANIFEST.sha256",
]


def _fsBuildIsoTimestamp(fHoursAgo=0.0):
    """Return an ISO-8601 UTC timestamp ``fHoursAgo`` before now."""
    dtNow = datetime.now(timezone.utc) - timedelta(hours=fHoursAgo)
    return dtNow.strftime("%Y-%m-%dT%H:%M:%SZ")


def _fdictBuildWorkflow(sProjectRepo):
    """A workflow whose AI answers are complete, so only sync can fail."""
    return {
        "sProjectRepoPath": sProjectRepo,
        "dictRemotes": {
            "github": {"sOwner": "someone", "sRepo": "something"},
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


def _fnWriteBothServices(sProjectRepo, listDivergedPaths, fHoursAgo=0.0):
    """Write a syncStatus cache for GitHub and Zenodo alike."""
    sIso = _fsBuildIsoTimestamp(fHoursAgo)
    dictAll = {}
    for sService in ("github", "zenodo"):
        dictAll[sService] = fdictBuildCachedVerify(
            sService=sService,
            listComparedPaths=_LIST_COMPARED,
            listDivergedPaths=listDivergedPaths,
            sLastVerified=sIso,
        )
    sDir = os.path.join(sProjectRepo, ".vaibify")
    os.makedirs(sDir, exist_ok=True)
    with open(
        os.path.join(sDir, "syncStatus.json"), "w", encoding="utf-8",
    ) as fileHandle:
        json.dump(dictAll, fileHandle)


def _fdictHeaderLevel2Cell(sProjectRepo, dictWorkflow):
    """Return the Project header's Level 2 cell for this workflow."""
    listBlockers = flistLevel2Blockers(dictWorkflow, sProjectRepo)
    return fdictComputeWorkflowScopeLevelStates(
        dictWorkflow, listBlockers, [],
    )["s2"]


@pytest.fixture
def fixtureProjectRepo(tmp_path):
    sRepo = str(tmp_path / "project")
    os.makedirs(sRepo, exist_ok=True)
    return sRepo


@pytest.mark.falsification
def testAProvenDivergenceDeniesTheProjectHeaderItsCheck(
    fixtureProjectRepo,
):
    """A fresh verify that found divergence must deny the cell.

    The exact reported shape: the verify RAN, it compared the files,
    and it found one that differs. Nothing here is stale or unknown —
    which is what made the check so wrong, because the evidence for
    "not published" was as fresh as evidence gets.

    Kills: removing "not-in-github-mirror" / "not-in-zenodo-deposit"
    from _T_WORKFLOW_LEVEL2_BASE_CRITERIA, which restores the
    intersection that dropped them and paints the cell attained.
    """
    _fnWriteBothServices(
        fixtureProjectRepo, [".vaibify/projects/project.json"],
    )
    dictWorkflow = _fdictBuildWorkflow(fixtureProjectRepo)
    dictCell = _fdictHeaderLevel2Cell(fixtureProjectRepo, dictWorkflow)

    assert dictCell["sState"] != "attained", (
        "the Project header claims Level 2 while a fresh verify says "
        "published files differ from BOTH remotes; the rows beside it "
        f"correctly show orange: {dictCell}"
    )
    # And the scalar gate agreed all along, which is the asymmetry
    # that made this a display bug rather than a gate bug.
    assert _fbComputeLevel2(dictWorkflow, fixtureProjectRepo) is False


def testAMatchingProjectStillEarnsTheCheck(fixtureProjectRepo):
    """The widened criteria set must not deny a clean project.

    The falsification above would also pass for a cell that never
    attains, which would be a worse bug in the opposite direction.
    """
    _fnWriteBothServices(fixtureProjectRepo, [])
    dictWorkflow = _fdictBuildWorkflow(fixtureProjectRepo)
    dictCell = _fdictHeaderLevel2Cell(fixtureProjectRepo, dictWorkflow)

    assert dictCell["sState"] == "attained", (
        "a project whose files match both remotes, with every AI "
        f"answer given, is denied its Level 2 cell: {dictCell}"
    )
    assert dictCell["iSatisfied"] == dictCell["iTotal"]


def testStalenessAndDivergenceNeverChargeTheSameServiceTwice(
    fixtureProjectRepo,
):
    """The two halves are exclusive, so the count must stay honest.

    ``_flistGithubLevel2Blockers`` returns the stale blocker and
    nothing else when the cache is stale. If that ever changed, a
    single unhappy service would cost two criteria and the cell would
    understate — the mirror image of the reported bug.
    """
    _fnWriteBothServices(
        fixtureProjectRepo, [".vaibify/projects/project.json"],
        fHoursAgo=48.0,
    )
    dictWorkflow = _fdictBuildWorkflow(fixtureProjectRepo)
    listBlockers = flistLevel2Blockers(dictWorkflow, fixtureProjectRepo)
    listCriteria = [d["sCriterion"] for d in listBlockers]

    assert "github-verify-stale" in listCriteria
    assert "not-in-github-mirror" not in listCriteria, (
        "a stale cache emitted BOTH halves for one service, so the "
        f"header cell charges it twice: {listCriteria}"
    )
    dictCell = _fdictHeaderLevel2Cell(fixtureProjectRepo, dictWorkflow)
    assert dictCell["iTotal"] - dictCell["iSatisfied"] == 2, (
        "two unhappy services cost other than two criteria: "
        f"{dictCell}"
    )
