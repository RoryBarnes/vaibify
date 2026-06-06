"""Unit tests for ``flistLevel2Blockers``.

Stage 3 of the AICS-ladder plan introduces a per-step blocker surface
for the Publication gate, parallel to ``flistLevel1Blockers``. The L2
blockers cover only the GitHub mirror and Zenodo deposit endpoints in
this stage (arXiv + Overleaf land in Stage 4) plus the
workflow-scope ``missing-ai-declaration-step``. The boolean L2 gate
``_fbComputeLevel2`` is unchanged; this surface adds visibility, not
new gate semantics.

The tests pin three structural rules: per-step rows are only emitted
when the sync cache is fresh (stale cache suppresses per-step rows and
falls back to a single workflow-scope ``*-verify-stale`` entry); L1's
blockers gain the unified schema fields without losing semantics; and
workflow-scope entries carry ``iStepIndex=-1`` and
``sStepLabel="(workflow)"``.
"""

import json
import os
from datetime import datetime, timedelta, timezone

from vaibify.reproducibility.aiDeclarationStep import (
    S_AI_DECLARATION_STEP_KIND,
)
from vaibify.reproducibility.levelGates import (
    F_MAX_STALE_HOURS,
    flistLevel1Blockers,
    flistLevel2Blockers,
)


# ------------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------------


def _fsBuildIsoTimestamp(fHoursAgo=0.0):
    """Return an ISO-8601 UTC timestamp ``fHoursAgo`` before now."""
    dtNow = datetime.now(timezone.utc) - timedelta(hours=fHoursAgo)
    return dtNow.strftime("%Y-%m-%dT%H:%M:%SZ")


def _fnWriteSyncStatusFile(sProjectRepo, dictPerService):
    """Write a per-service syncStatus.json under ``.vaibify/``."""
    sDir = os.path.join(sProjectRepo, ".vaibify")
    os.makedirs(sDir, exist_ok=True)
    sPath = os.path.join(sDir, "syncStatus.json")
    with open(sPath, "w", encoding="utf-8") as fileHandle:
        json.dump(dictPerService, fileHandle)


def _fdictGreenStep(sName="A"):
    """Return a step dict that satisfies every L1 criterion."""
    return {
        "sName": sName, "sDirectory": sName,
        "saDataFiles": [sName + "/data.csv"],
        "saPlotFiles": [sName + "/plot.pdf"],
        "dictVerification": {
            "sUser": "passed",
            "sUnitTest": "passed",
            "sIntegrity": "passed",
            "sQualitative": "passed",
            "sQuantitative": "passed",
        },
    }


def _fdictAiDeclarationStep():
    """Return a minimal ai-declaration step dict."""
    dictStep = _fdictGreenStep(sName="Decl")
    dictStep["sStepKind"] = S_AI_DECLARATION_STEP_KIND
    return dictStep


def _fdictFreshGithubCache(listDiverged=None):
    """Return a syncStatus dict whose github entry verifies cleanly now."""
    return {
        "github": {
            "sService": "github",
            "sLastVerified": _fsBuildIsoTimestamp(fHoursAgo=1.0),
            "iTotalFiles": 5,
            "iMatching": 5 - len(listDiverged or []),
            "listDiverged": listDiverged or [],
            "sCommittedShaVerified": "abc123",
        },
    }


def _fdictFreshZenodoCache(listDiverged=None):
    """Return a syncStatus dict whose zenodo entry verifies cleanly now."""
    return {
        "zenodo": {
            "sService": "zenodo",
            "sLastVerified": _fsBuildIsoTimestamp(fHoursAgo=1.0),
            "iTotalFiles": 5,
            "iMatching": 5 - len(listDiverged or []),
            "listDiverged": listDiverged or [],
            "sZenodoDoi": "10.1000/example",
            "sEndpointVerified": "sandbox",
        },
    }


# ------------------------------------------------------------------------
# Per-step zenodo / github divergence
# ------------------------------------------------------------------------


def testGithubMirrorDivergenceFiresPerStepBlocker(tmp_path):
    sProjectRepo = str(tmp_path)
    _fnWriteSyncStatusFile(sProjectRepo, _fdictFreshGithubCache(
        listDiverged=[{
            "sPath": "B/data.csv",
            "sExpected": "deadbeef",
            "sActual": "feedface",
        }],
    ))
    dictWorkflow = {
        "listSteps": [
            _fdictGreenStep(sName="A"),
            _fdictGreenStep(sName="B"),
            _fdictAiDeclarationStep(),
        ],
    }
    listBlockers = flistLevel2Blockers(dictWorkflow, sProjectRepo)
    listGithub = [
        dictEntry for dictEntry in listBlockers
        if dictEntry["sCriterion"] == "not-in-github-mirror"
    ]
    assert len(listGithub) == 1
    dictEntry = listGithub[0]
    assert dictEntry["iLevel"] == 2
    assert dictEntry["sScope"] == "step"
    assert dictEntry["iStepIndex"] == 1
    assert dictEntry["listOffendingFiles"] == ["B/data.csv"]
    assert dictEntry["sRemediationHint"]


def testZenodoDepositDivergenceFiresPerStepBlocker(tmp_path):
    sProjectRepo = str(tmp_path)
    _fnWriteSyncStatusFile(sProjectRepo, _fdictFreshZenodoCache(
        listDiverged=[{
            "sPath": "B/data.csv",
            "sExpected": "deadbeef",
            "sActual": "feedface",
        }],
    ))
    dictWorkflow = {
        "listSteps": [
            _fdictGreenStep(sName="A"),
            _fdictGreenStep(sName="B"),
            _fdictAiDeclarationStep(),
        ],
    }
    listBlockers = flistLevel2Blockers(dictWorkflow, sProjectRepo)
    listZenodo = [
        dictEntry for dictEntry in listBlockers
        if dictEntry["sCriterion"] == "not-in-zenodo-deposit"
    ]
    assert len(listZenodo) == 1
    dictEntry = listZenodo[0]
    assert dictEntry["iLevel"] == 2
    assert dictEntry["sScope"] == "step"
    assert dictEntry["iStepIndex"] == 1
    assert dictEntry["listOffendingFiles"] == ["B/data.csv"]


# ------------------------------------------------------------------------
# Stale-cache workflow-scope suppression
# ------------------------------------------------------------------------


def testStaleSyncCacheFiresWorkflowScopeOnly(tmp_path):
    sProjectRepo = str(tmp_path)
    _fnWriteSyncStatusFile(sProjectRepo, {
        "github": {
            "sService": "github",
            "sLastVerified": _fsBuildIsoTimestamp(
                fHoursAgo=F_MAX_STALE_HOURS + 1.0,
            ),
            "iTotalFiles": 5, "iMatching": 4,
            "listDiverged": [{
                "sPath": "B/data.csv",
                "sExpected": "x", "sActual": "y",
            }],
            "sCommittedShaVerified": "abc123",
        },
        "zenodo": _fdictFreshZenodoCache()["zenodo"],
    })
    dictWorkflow = {
        "listSteps": [
            _fdictGreenStep(sName="A"),
            _fdictGreenStep(sName="B"),
            _fdictAiDeclarationStep(),
        ],
    }
    listBlockers = flistLevel2Blockers(dictWorkflow, sProjectRepo)
    listGithub = [
        dictEntry for dictEntry in listBlockers
        if dictEntry["sCriterion"].startswith("github")
        or dictEntry["sCriterion"] == "not-in-github-mirror"
    ]
    assert len(listGithub) == 1
    dictEntry = listGithub[0]
    assert dictEntry["sCriterion"] == "github-verify-stale"
    assert dictEntry["sScope"] == "workflow"
    assert dictEntry["iStepIndex"] == -1
    assert dictEntry["sStepLabel"] == "(workflow)"


def testStaleZenodoCacheFiresWorkflowScopeOnly(tmp_path):
    sProjectRepo = str(tmp_path)
    _fnWriteSyncStatusFile(sProjectRepo, {
        "github": _fdictFreshGithubCache()["github"],
        "zenodo": {
            "sService": "zenodo",
            "sLastVerified": _fsBuildIsoTimestamp(
                fHoursAgo=F_MAX_STALE_HOURS + 1.0,
            ),
            "iTotalFiles": 5, "iMatching": 4,
            "listDiverged": [{
                "sPath": "B/data.csv",
                "sExpected": "x", "sActual": "y",
            }],
            "sZenodoDoi": "10.1000/example",
            "sEndpointVerified": "sandbox",
        },
    })
    dictWorkflow = {
        "listSteps": [
            _fdictGreenStep(sName="A"),
            _fdictGreenStep(sName="B"),
            _fdictAiDeclarationStep(),
        ],
    }
    listBlockers = flistLevel2Blockers(dictWorkflow, sProjectRepo)
    listZenodo = [
        dictEntry for dictEntry in listBlockers
        if dictEntry["sCriterion"] in
        ("zenodo-verify-stale", "not-in-zenodo-deposit")
    ]
    assert len(listZenodo) == 1
    assert listZenodo[0]["sCriterion"] == "zenodo-verify-stale"
    assert listZenodo[0]["sScope"] == "workflow"
    assert listZenodo[0]["iStepIndex"] == -1


# ------------------------------------------------------------------------
# Missing AI declaration (workflow-scope)
# ------------------------------------------------------------------------


def testMissingAiDeclarationStepFiresWorkflowScope(tmp_path):
    sProjectRepo = str(tmp_path)
    _fnWriteSyncStatusFile(sProjectRepo, {
        "github": _fdictFreshGithubCache()["github"],
        "zenodo": _fdictFreshZenodoCache()["zenodo"],
    })
    dictWorkflow = {
        "listSteps": [_fdictGreenStep(sName="A")],
    }
    listBlockers = flistLevel2Blockers(dictWorkflow, sProjectRepo)
    listDecl = [
        dictEntry for dictEntry in listBlockers
        if dictEntry["sCriterion"] == "missing-ai-declaration-step"
    ]
    assert len(listDecl) == 1
    dictEntry = listDecl[0]
    assert dictEntry["iLevel"] == 2
    assert dictEntry["sScope"] == "workflow"
    assert dictEntry["iStepIndex"] == -1
    assert dictEntry["sStepLabel"] == "(workflow)"
    assert dictEntry["sRemediationHint"]


def testAiDeclarationPresentSuppressesWorkflowScope(tmp_path):
    sProjectRepo = str(tmp_path)
    _fnWriteSyncStatusFile(sProjectRepo, {
        "github": _fdictFreshGithubCache()["github"],
        "zenodo": _fdictFreshZenodoCache()["zenodo"],
    })
    dictWorkflow = {
        "listSteps": [
            _fdictGreenStep(sName="A"),
            _fdictAiDeclarationStep(),
        ],
    }
    listBlockers = flistLevel2Blockers(dictWorkflow, sProjectRepo)
    listDecl = [
        dictEntry for dictEntry in listBlockers
        if dictEntry["sCriterion"] == "missing-ai-declaration-step"
    ]
    assert listDecl == []


# ------------------------------------------------------------------------
# Composition rules
# ------------------------------------------------------------------------


def testMissingProjectRepoReturnsEmpty():
    dictWorkflow = {"listSteps": [_fdictGreenStep(sName="A")]}
    assert flistLevel2Blockers(dictWorkflow, "") == []


def testL1BoundariesPreserved(tmp_path):
    """L2 surfacing is independent of L1 outcome.

    ``flistLevel2Blockers`` runs unconditionally — it does not short-
    circuit on L1 failure. This decouples *what is wrong at L2* from
    *whether L1 is closed yet*, so the dashboard can render the user's
    full ladder picture in one poll instead of waiting for L1 to clear
    before showing the L2 readiness landscape. The boolean L2 gate
    (``_fbComputeLevel2``) still gates the level integer.
    """
    sProjectRepo = str(tmp_path)
    _fnWriteSyncStatusFile(sProjectRepo, {
        "github": _fdictFreshGithubCache(
            listDiverged=[{
                "sPath": "B/data.csv",
                "sExpected": "x", "sActual": "y",
            }],
        )["github"],
        "zenodo": _fdictFreshZenodoCache()["zenodo"],
    })
    dictStepA = _fdictGreenStep(sName="A")
    dictStepA["dictVerification"]["sUser"] = "untested"  # L1 blocker
    dictStepB = _fdictGreenStep(sName="B")
    dictWorkflow = {
        "listSteps": [
            dictStepA, dictStepB, _fdictAiDeclarationStep(),
        ],
    }
    listBlockers = flistLevel2Blockers(dictWorkflow, sProjectRepo)
    listMirror = [
        dictEntry for dictEntry in listBlockers
        if dictEntry["sCriterion"] == "not-in-github-mirror"
    ]
    assert len(listMirror) == 1
    assert listMirror[0]["iStepIndex"] == 1
    for dictEntry in listBlockers:
        assert dictEntry["iLevel"] == 2
        assert dictEntry["sScope"] in ("step", "workflow")


# ------------------------------------------------------------------------
# Schema unification on the L1 surface
# ------------------------------------------------------------------------


def testSchemaUnificationAppliedToL1():
    """Every L1 blocker entry carries ``sScope`` and ``sRemediationHint``.

    Stage 3 unifies the schema across L1/L2 (and the future L3) so the
    frontend can iterate one shape regardless of level. This test fails
    if any L1 builder forgets to stamp the new fields.
    """
    dictStepUserUntested = {
        "sName": "A", "sDirectory": "A",
        "saDataFiles": ["A/data.csv"], "saPlotFiles": [],
        "dictVerification": {"sUser": "untested"},
    }
    dictStepAxisFail = {
        "sName": "B", "sDirectory": "B",
        "saDataFiles": ["B/data.csv"], "saPlotFiles": [],
        "dictVerification": {
            "sUser": "passed",
            "sUnitTest": "failed",
            "sIntegrity": "passed",
            "sQualitative": "passed",
            "sQuantitative": "passed",
        },
    }
    dictStepAttestStale = {
        "sName": "C", "sDirectory": "C",
        "saDataFiles": ["C/data.csv"], "saPlotFiles": [],
        "dictVerification": {
            "sUser": "stale",
            "sLastUserUpdate": "2026-01-01T00:00:00Z",
            "sUnitTest": "passed",
            "sIntegrity": "passed",
            "sQualitative": "passed",
            "sQuantitative": "passed",
        },
    }
    listBlockers = flistLevel1Blockers(
        {"listSteps": [
            dictStepUserUntested, dictStepAxisFail, dictStepAttestStale,
        ]},
        {}, "/repo",
    )
    assert len(listBlockers) == 3
    for dictEntry in listBlockers:
        assert dictEntry["iLevel"] == 1
        assert dictEntry["sScope"] == "step"
        assert isinstance(dictEntry["sRemediationHint"], str)
        assert dictEntry["sRemediationHint"]
