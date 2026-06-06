"""Unit tests for the Stage 4 L2 Overleaf + arXiv blocker wiring.

Stage 4 extends ``flistLevel2Blockers`` with one per-step criterion
(``figure-not-frozen``) projecting the Overleaf push manifest onto each
step's declared plot paths, and three workflow-scope criteria
(``arxiv-not-submitted`` / ``arxiv-mismatch`` / ``arxiv-version-stale``)
covering arXiv-submission state. The boolean L2 gate gains the
``fbWorkflowFullySyncedWithArxiv`` conjunct, which is suppressed
entirely when the workflow has no Overleaf binding — data-only
workflows reach L2 without a manuscript.

These tests pin the contract: a workflow with Overleaf but no arXiv ID
fires ``arxiv-not-submitted``; matching Overleaf push and arXiv hashes
clear ``arxiv-mismatch``; an unfrozen plot fires ``figure-not-frozen``
on its owning step; a workflow without an Overleaf binding stays L2
without any arXiv-related blockers; and a stale recorded version fires
``arxiv-version-stale``.
"""

import json
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from vaibify.reproducibility.aiDeclarationStep import (
    S_AI_DECLARATION_STEP_KIND,
)
from vaibify.reproducibility import overleafSync
from vaibify.reproducibility.levelGates import (
    _fbComputeLevel2,
    fbWorkflowFullySyncedWithArxiv,
    flistLevel2Blockers,
)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


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


def _fdictFreshGithubCache():
    """Return a fresh, fully-matching github cache entry."""
    return {
        "sService": "github",
        "sLastVerified": _fsBuildIsoTimestamp(fHoursAgo=1.0),
        "iTotalFiles": 1, "iMatching": 1, "listDiverged": [],
        "sCommittedShaVerified": "abc123",
    }


def _fdictFreshZenodoCache():
    """Return a fresh, fully-matching zenodo cache entry."""
    return {
        "sService": "zenodo",
        "sLastVerified": _fsBuildIsoTimestamp(fHoursAgo=1.0),
        "iTotalFiles": 1, "iMatching": 1, "listDiverged": [],
        "sZenodoDoi": "10.1000/example",
        "sEndpointVerified": "sandbox",
    }


def _fnWriteAllGreenSyncCache(sProjectRepo):
    """Write fresh + full-match cache entries for github and zenodo."""
    _fnWriteSyncStatusFile(sProjectRepo, {
        "github": _fdictFreshGithubCache(),
        "zenodo": _fdictFreshZenodoCache(),
    })


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


def _fdictWorkflowWithOverleaf(sCommit="commitabc", dictArxiv=None):
    """Return a workflow with an Overleaf binding and optional arXiv config."""
    dictRemotes = {
        "github": {
            "sOwner": "u", "sRepo": "r", "sBranch": "main",
            "sCommittedSha": "abc123",
        },
        "zenodo": {
            "sRecordId": "1", "sService": "sandbox",
            "sDoi": "10.1000/example",
        },
        "overleaf": {
            "sProjectId": "ol1234",
            "sLastPushCommit": sCommit,
        },
    }
    if dictArxiv is not None:
        dictRemotes["arxiv"] = dictArxiv
    return {
        "listSteps": [
            _fdictGreenStep(sName="A"),
            _fdictAiDeclarationStep(),
        ],
        "dictRemotes": dictRemotes,
    }


# ----------------------------------------------------------------------
# arxiv-not-submitted (workflow-scope)
# ----------------------------------------------------------------------


def testWorkflowWithOverleafBindingButNoArxivIdEmitsBlocker(tmp_path):
    """Overleaf binding + missing arXiv ID -> ``arxiv-not-submitted``."""
    sProjectRepo = str(tmp_path)
    _fnWriteAllGreenSyncCache(sProjectRepo)
    dictWorkflow = _fdictWorkflowWithOverleaf(dictArxiv={})
    listBlockers = flistLevel2Blockers(dictWorkflow, sProjectRepo)
    listMatch = [
        d for d in listBlockers
        if d["sCriterion"] == "arxiv-not-submitted"
    ]
    assert len(listMatch) == 1
    dictEntry = listMatch[0]
    assert dictEntry["iLevel"] == 2
    assert dictEntry["sScope"] == "workflow"
    assert dictEntry["iStepIndex"] == -1
    assert dictEntry["sStepLabel"] == "(workflow)"
    assert dictEntry["sRemediationHint"]


# ----------------------------------------------------------------------
# arxiv-mismatch / clean (workflow-scope)
# ----------------------------------------------------------------------


def testArxivTarballHashesMatchOverleafPushClearsBlocker(tmp_path):
    """When fetched arXiv hashes cover every pushed path, no mismatch."""
    sProjectRepo = str(tmp_path)
    _fnWriteAllGreenSyncCache(sProjectRepo)
    overleafSync.fnRecordOverleafPushManifest(
        sProjectRepo, "commitabc", ["A/plot.pdf"],
    )
    dictWorkflow = _fdictWorkflowWithOverleaf(
        sCommit="commitabc",
        dictArxiv={"sArxivId": "2401.00001", "sArxivVersion": "v1"},
    )
    with patch(
        "vaibify.reproducibility.arxivClient.fdictFetchRemoteHashes",
        return_value={"A/plot.pdf": "deadbeef"},
    ), patch(
        "vaibify.reproducibility.arxivClient.fsResolveLatestVersion",
        return_value="v1",
    ):
        listBlockers = flistLevel2Blockers(dictWorkflow, sProjectRepo)
    listMismatch = [
        d for d in listBlockers
        if d["sCriterion"] == "arxiv-mismatch"
    ]
    assert listMismatch == []


# ----------------------------------------------------------------------
# figure-not-frozen (per-step)
# ----------------------------------------------------------------------


def testFigureNotFrozenFiresForStepWithUnpushedPlot(tmp_path):
    """Plot declared on a step but absent from push manifest -> per-step."""
    sProjectRepo = str(tmp_path)
    _fnWriteAllGreenSyncCache(sProjectRepo)
    overleafSync.fnRecordOverleafPushManifest(
        sProjectRepo, "commitabc", ["A/plot.pdf"],  # only A's plot pushed
    )
    dictWorkflow = _fdictWorkflowWithOverleaf(sCommit="commitabc")
    dictAiDecl = _fdictAiDeclarationStep()
    dictAiDecl["saPlotFiles"] = []  # ai-declaration declares no plots
    dictWorkflow["listSteps"] = [
        _fdictGreenStep(sName="A"),
        {
            "sName": "B", "sDirectory": "Plot/A12",
            "saDataFiles": [], "saPlotFiles": ["Plot/A12/foo.pdf"],
            "dictVerification": {
                "sUser": "passed", "sUnitTest": "passed",
                "sIntegrity": "passed", "sQualitative": "passed",
                "sQuantitative": "passed",
            },
        },
        dictAiDecl,
    ]
    listBlockers = flistLevel2Blockers(dictWorkflow, sProjectRepo)
    listFrozen = [
        d for d in listBlockers
        if d["sCriterion"] == "figure-not-frozen"
    ]
    assert len(listFrozen) == 1
    dictEntry = listFrozen[0]
    assert dictEntry["iLevel"] == 2
    assert dictEntry["sScope"] == "step"
    assert dictEntry["iStepIndex"] == 1
    assert dictEntry["listOffendingFiles"] == ["Plot/A12/foo.pdf"]
    assert dictEntry["sRemediationHint"]


# ----------------------------------------------------------------------
# Data-only workflows pass L2 without arXiv
# ----------------------------------------------------------------------


def testDataOnlyWorkflowReachesL2WithoutArxiv(tmp_path):
    """Workflow with no Overleaf binding suppresses arXiv/figure criteria.

    The four arXiv-and-Overleaf criteria are all suppressed when no
    Overleaf binding is configured, so the boolean L2 gate closes on
    GitHub + Zenodo + AI-declaration alone.
    """
    sProjectRepo = str(tmp_path)
    _fnWriteAllGreenSyncCache(sProjectRepo)
    dictWorkflow = {
        "listSteps": [
            _fdictGreenStep(sName="A"),
            _fdictAiDeclarationStep(),
        ],
        "dictRemotes": {
            "github": {
                "sOwner": "u", "sRepo": "r", "sBranch": "main",
                "sCommittedSha": "abc123",
            },
            "zenodo": {
                "sRecordId": "1", "sService": "sandbox",
                "sDoi": "10.1000/example",
            },
        },
    }
    listBlockers = flistLevel2Blockers(dictWorkflow, sProjectRepo)
    setArxivOrFigure = {
        "arxiv-not-submitted", "arxiv-mismatch",
        "arxiv-version-stale", "figure-not-frozen",
    }
    listMatch = [
        d for d in listBlockers
        if d["sCriterion"] in setArxivOrFigure
    ]
    assert listMatch == []
    assert fbWorkflowFullySyncedWithArxiv(
        dictWorkflow, sProjectRepo,
    ) is True
    assert _fbComputeLevel2(dictWorkflow, sProjectRepo) is True


# ----------------------------------------------------------------------
# arxiv-version-stale (workflow-scope)
# ----------------------------------------------------------------------


def testArxivVersionStaleFiresWhenNewerVersionAvailable(tmp_path):
    """Recorded ``v1`` against arXiv's ``v2`` -> ``arxiv-version-stale``."""
    sProjectRepo = str(tmp_path)
    _fnWriteAllGreenSyncCache(sProjectRepo)
    overleafSync.fnRecordOverleafPushManifest(
        sProjectRepo, "commitabc", ["A/plot.pdf"],
    )
    dictWorkflow = _fdictWorkflowWithOverleaf(
        sCommit="commitabc",
        dictArxiv={"sArxivId": "2401.00001", "sArxivVersion": "v1"},
    )
    with patch(
        "vaibify.reproducibility.arxivClient.fdictFetchRemoteHashes",
        return_value={"A/plot.pdf": "deadbeef"},
    ), patch(
        "vaibify.reproducibility.arxivClient.fsResolveLatestVersion",
        return_value="v2",
    ):
        listBlockers = flistLevel2Blockers(dictWorkflow, sProjectRepo)
    listStale = [
        d for d in listBlockers
        if d["sCriterion"] == "arxiv-version-stale"
    ]
    assert len(listStale) == 1
    dictEntry = listStale[0]
    assert dictEntry["iLevel"] == 2
    assert dictEntry["sScope"] == "workflow"
    assert dictEntry["iStepIndex"] == -1
    assert dictEntry["sRemediationHint"]
