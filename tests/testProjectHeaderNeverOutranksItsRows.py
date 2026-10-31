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

Since 2026-10-30 each remote has a SECOND requirement beside its copy
check: that a reader can retrieve it (``github-repository-not-public``,
``zenodo-archive-not-public``). Being public and matching are separate
requirements, as in the paper's Level 2 table, so a private repository
with a diverged file is charged twice -- once for each failed
requirement, not twice for one. Staleness still excludes every other
criterion for its service: a record that cannot answer emits the stale
blocker alone, and a withheld Zenodo comparison emits only the
not-public one.
"""

import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from tests.syncStatusFixtures import fdictBuildCachedVerify
from vaibify.reproducibility.levelGates import (
    _fbComputeLevel2,
    _fdictL3WorkflowChecks,
    _T_WORKFLOW_LEVEL2_ARXIV_CRITERIA,
    _T_WORKFLOW_LEVEL2_BASE_CRITERIA,
    _T_WORKFLOW_LEVEL2_OVERLEAF_CRITERIA,
    _T_WORKFLOW_LEVEL3_CRITERIA,
    S_BASENAME_COLLISION_CRITERION,
    fnClearLevelBlockerCache,
    fbAtLeastLevel3,
    fdictComputeWorkflowScopeLevelStates,
    flistLevel2Blockers,
    flistLevel3Blockers,
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
    dictIdentity = {
        "github": {},
        # A recorded DOI and the endpoint the project is configured
        # for: without them the Zenodo gate refuses, and a "clean"
        # cache that the gate refuses is not clean.
        "zenodo": {
            "sZenodoDoi": "10.5281/zenodo.1", "sEndpointVerified": "sandbox",
        },
    }
    for sService in ("github", "zenodo"):
        dictAll[sService] = fdictBuildCachedVerify(
            sService=sService,
            listComparedPaths=_LIST_COMPARED,
            listDivergedPaths=listDivergedPaths,
            sLastVerified=sIso,
            **dictIdentity[sService],
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


def _fsetWorkflowScopeCriteria(dictWorkflow, sProjectRepo, dictAll):
    """Write one cache document and return the workflow-scope criteria it raises."""
    sDir = os.path.join(sProjectRepo, ".vaibify")
    os.makedirs(sDir, exist_ok=True)
    with open(os.path.join(sDir, "syncStatus.json"), "w") as fileHandle:
        json.dump(dictAll, fileHandle)
    fnClearLevelBlockerCache()
    return {
        dictEntry["sCriterion"]
        for dictEntry in flistLevel2Blockers(dictWorkflow, sProjectRepo)
        if dictEntry["iStepIndex"] < 0
    }


def _fdictCacheFor(sService, **dictOverrides):
    """A fresh record of one service, with the overrides a scenario names."""
    dictFields = {"sService": sService, "listComparedPaths": _LIST_COMPARED,
                  "sLastVerified": _fsBuildIsoTimestamp()}
    if sService == "zenodo":
        dictFields.update(sZenodoDoi="10.5281/zenodo.1",
                          sEndpointVerified="sandbox")
    dictFields.update(dictOverrides)
    return fdictBuildCachedVerify(**dictFields)


@pytest.mark.falsification
def testEveryLevel2CriterionTheGatesEmitIsCountedByTheHeader(
    fixtureProjectRepo,
):
    """The Level 2 twin of the Level 3 test below.

    Level 2 has no checks dict to enumerate, so the gates are DRIVEN
    into every workflow-scope refusal a remote can raise -- stale,
    diverged, and not public, for both services -- and each criterion
    they emit must be one the header counts. A criterion counted only
    while it applies (arXiv, Overleaf, a basename collision) counts
    here too; the AI Declaration is a step and is homed on its row.

    Kills: dropping the reader-access pair from
    _T_WORKFLOW_LEVEL2_BASE_CRITERIA, which paints the header over a
    private repository's row.
    """
    dictWorkflow = _fdictBuildWorkflow(fixtureProjectRepo)
    dictWorkflow["dictAiProvenance"] = {}
    setEmitted = set()
    sUnowned = ".vaibify/projects/project.json"
    setEmitted |= _fsetWorkflowScopeCriteria(dictWorkflow, fixtureProjectRepo, {
        "github": _fdictCacheFor(
            "github", sPublicAccess="not-public", listDivergedPaths=[sUnowned]),
        "zenodo": _fdictCacheFor(
            "zenodo", sPublicAccess="not-public", bComparisonWithheld=True),
    })
    setEmitted |= _fsetWorkflowScopeCriteria(dictWorkflow, fixtureProjectRepo, {
        "github": _fdictCacheFor("github", sLastVerified=_fsBuildIsoTimestamp(48)),
        "zenodo": _fdictCacheFor("zenodo", listDivergedPaths=[sUnowned]),
    })
    setEmitted |= _fsetWorkflowScopeCriteria(dictWorkflow, fixtureProjectRepo, {
        "zenodo": _fdictCacheFor("zenodo", sLastVerified=_fsBuildIsoTimestamp(48)),
    })
    assert {"github-repository-not-public", "zenodo-archive-not-public",
            "github-verify-stale", "zenodo-verify-stale",
            "not-in-github-mirror", "not-in-zenodo-deposit"} <= setEmitted
    setCounted = set(
        _T_WORKFLOW_LEVEL2_BASE_CRITERIA + _T_WORKFLOW_LEVEL2_ARXIV_CRITERIA
        + _T_WORKFLOW_LEVEL2_OVERLEAF_CRITERIA
        + (S_BASENAME_COLLISION_CRITERION, "missing-ai-declaration-step"))
    assert setEmitted - setCounted == set(), (
        "these criteria block the rows and are dropped from the header "
        f"count: {sorted(setEmitted - setCounted)}"
    )


def testAPrivateRepositoryIsChargedOnceForEachFailedRequirement(
    fixtureProjectRepo,
):
    """Not public AND diverged: two requirements failed, two charged."""
    _fnWriteBothServices(fixtureProjectRepo, [])
    sDir = os.path.join(fixtureProjectRepo, ".vaibify", "syncStatus.json")
    with open(sDir) as fileHandle:
        dictAll = json.load(fileHandle)
    dictAll["github"] = _fdictCacheFor(
        "github", sPublicAccess="not-public",
        listDivergedPaths=[".vaibify/projects/project.json"])
    with open(sDir, "w") as fileHandle:
        json.dump(dictAll, fileHandle)
    fnClearLevelBlockerCache()
    dictWorkflow = _fdictBuildWorkflow(fixtureProjectRepo)
    dictCell = _fdictHeaderLevel2Cell(fixtureProjectRepo, dictWorkflow)
    assert dictCell["iTotal"] - dictCell["iSatisfied"] == 2, dictCell


@pytest.mark.falsification
def testARecordThatNeverAskedAboutReadersDeniesTheHeaderItsCheck(
    fixtureProjectRepo,
):
    """Fresh, matching, about this remote, and silent about readers.

    The scalar gate refuses it -- absent is never public -- and the row
    reads "?". But the copy matched and the record named its remote, so
    no copy blocker fired, and the access side charged only "not
    public": the header counted 9 of 9 above a row it was refusing
    (found in review, 2026-10-31). An unknown verdict is now charged to
    the service's stale criterion when nothing else has charged it.

    Kills: _flistPublicAccessLevel2Blockers no longer charging an
    unknown verdict.
    """
    _fnWriteBothServices(fixtureProjectRepo, [])
    sPath = os.path.join(fixtureProjectRepo, ".vaibify", "syncStatus.json")
    with open(sPath) as fileHandle:
        dictAll = json.load(fileHandle)
    dictAll["github"] = _fdictCacheFor("github", sPublicAccess=None)
    with open(sPath, "w") as fileHandle:
        json.dump(dictAll, fileHandle)
    fnClearLevelBlockerCache()
    dictWorkflow = _fdictBuildWorkflow(fixtureProjectRepo)
    assert _fbComputeLevel2(dictWorkflow, fixtureProjectRepo) is False
    dictCell = _fdictHeaderLevel2Cell(fixtureProjectRepo, dictWorkflow)
    assert dictCell["sState"] != "attained", (
        "the header claims Level 2 while the gate refuses an unknown "
        f"reader verdict: {dictCell}")
    assert dictCell["iTotal"] - dictCell["iSatisfied"] == 1, dictCell
    listStale = [
        dictEntry for dictEntry in flistLevel2Blockers(
            dictWorkflow, fixtureProjectRepo)
        if dictEntry["sCriterion"] == "github-verify-stale"
    ]
    assert len(listStale) == 1
    assert "before vaibify asked what a reader is served" in (
        listStale[0]["sRemediationHint"])


def testAStaleRecordIsChargedOnceThoughBothQuestionsAreUnknown(
    fixtureProjectRepo,
):
    """The copy side already charged the stale criterion; access adds nothing."""
    _fnWriteBothServices(fixtureProjectRepo, [], fHoursAgo=48.0)
    dictWorkflow = _fdictBuildWorkflow(fixtureProjectRepo)
    listCriteria = [
        dictEntry["sCriterion"] for dictEntry in flistLevel2Blockers(
            dictWorkflow, fixtureProjectRepo)
    ]
    assert listCriteria.count("github-verify-stale") == 1, listCriteria
    assert listCriteria.count("zenodo-verify-stale") == 1, listCriteria


# ── The same asymmetry one level up ──


def testEveryLevel3CriterionTheGatesEmitIsCountedByTheHeader(
    fixtureProjectRepo,
):
    """A criterion the checks dict emits and the tuple omits is invisible.

    The header intersects the live blocker list against a fixed
    tuple, so an omission does not under-count — it silently drops the
    criterion and the cell paints a check above the row that blocks.
    This is the generalization of the reported bug: it asserts the two
    sets agree rather than naming one criterion at a time.
    """
    setEmitted = set(_fdictL3WorkflowChecks({}, fixtureProjectRepo))
    setCounted = set(_T_WORKFLOW_LEVEL3_CRITERIA)
    assert setEmitted - setCounted == set(), (
        "these criteria block the rows and are dropped from the "
        f"header count: {sorted(setEmitted - setCounted)}"
    )


def testASandboxArchiveBlocksTheScalarGateAndTheRowsTogether(
    fixtureProjectRepo,
):
    """The header must not outrank its rows over the permanence criterion.

    ``fbAtLeastLevel3`` used to enumerate its conjuncts by hand, so a
    criterion registered only in the dict and the tuple left the
    scalar gate reporting attainment while the row blocked — the
    display disagreeing with itself, which is the shape of the 2026-
    08-30 defect this module exists for. It did happen, to
    ``fbAttestationIsPubliclyArchived``. The gate now takes every
    workflow criterion FROM ``_fdictL3WorkflowChecks``, so a new one
    enters it by construction and the hand-maintained list is gone;
    this test stays as the behavioural proof of that for one criterion.
    """
    os.makedirs(os.path.join(fixtureProjectRepo, ".vaibify"), exist_ok=True)
    with open(
        os.path.join(fixtureProjectRepo, ".vaibify", "environment.json"),
        "w",
    ) as fileOut:
        json.dump({"dictContainer": {
            "sImageDigest": "registry.example/p@sha256:" + "a" * 64,
            "sArchitecture": "arm64",
            "dictImageArchive": {"sZenodoService": "sandbox"},
        }}, fileOut)
    dictWorkflow = _fdictBuildWorkflow(fixtureProjectRepo)
    listCriteria = [
        dictEntry["sCriterion"]
        for dictEntry in flistLevel3Blockers(
            dictWorkflow, fixtureProjectRepo, False,
        )
    ]
    assert "an-archive-is-a-sandbox-deposit" in listCriteria
    assert fbAtLeastLevel3(
        dictWorkflow, fixtureProjectRepo, False,
    ) is False
