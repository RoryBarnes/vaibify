"""Unit tests for the Stage 4 L2 Overleaf + arXiv blocker wiring.

Stage 4 extends ``flistLevel2Blockers`` with one per-step criterion
(``figure-not-frozen``) projecting the Overleaf push manifest onto each
step's declared plot paths, and two workflow-scope criteria
(``arxiv-mismatch`` / ``arxiv-version-stale``) covering
arXiv-submission state. The boolean L2 gate gains the
``fbWorkflowFullySyncedWithArxiv`` conjunct, which is opt-in: it is
suppressed entirely when the workflow records no arXiv submission —
posting to arXiv happens outside vaibify on its own timeline, so an
untracked manuscript must not block publication of the code and data.

These tests pin the contract: a workflow with Overleaf but no arXiv ID
emits no arXiv blocker and passes the gate; matching Overleaf push and
arXiv hashes clear ``arxiv-mismatch``; a recorded arXiv ID with no
Overleaf push fails honestly; an unfrozen plot fires
``figure-not-frozen`` on its owning step; a workflow without an
Overleaf binding stays L2 without any arXiv-related blockers; and a
stale recorded version fires ``arxiv-version-stale``.
"""

import hashlib
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


_BA_FIGURE_CONTENT = b"%PDF-1.4 canonical figure bytes\n"
S_FIGURE_SHA = hashlib.sha256(_BA_FIGURE_CONTENT).hexdigest()


def _fnWriteFigureFile(sProjectRepo, sRelPath="A/plot.pdf"):
    """Create a real figure file; live hashing needs actual bytes.

    The gate's expected side is the figure's current local content
    (the L3 manifest plays no role at L2).
    """
    sAbsolute = os.path.join(sProjectRepo, sRelPath)
    os.makedirs(os.path.dirname(sAbsolute), exist_ok=True)
    with open(sAbsolute, "wb") as fileHandle:
        fileHandle.write(_BA_FIGURE_CONTENT)


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
        "saOutputDataFiles": [sName + "/data.csv"],
        "saPlotFiles": [sName + "/plot.pdf"],
        "bNoInputData": True,
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
# Unrecorded arXiv submission is neutral (opt-in claim)
# ----------------------------------------------------------------------


def testOverleafBindingWithoutArxivIdEmitsNoBlockerAndPassesGate(tmp_path):
    """An Overleaf binding alone must not drag in the arXiv criteria.

    Recording an arXiv ID is an opt-in claim; a bound manuscript whose
    e-print has not been posted (or is deliberately untracked) emits
    no arXiv blocker and leaves the L2 gate closeable.
    """
    sProjectRepo = str(tmp_path)
    _fnWriteAllGreenSyncCache(sProjectRepo)
    dictWorkflow = _fdictWorkflowWithOverleaf(dictArxiv={})
    listBlockers = flistLevel2Blockers(dictWorkflow, sProjectRepo)
    listMatch = [
        d for d in listBlockers
        if d["sCriterion"].startswith("arxiv-")
    ]
    assert listMatch == []
    assert fbWorkflowFullySyncedWithArxiv(
        dictWorkflow, sProjectRepo,
    ) is True
    assert _fbComputeLevel2(dictWorkflow, sProjectRepo) is True


def testArxivIdWithoutOverleafPushFailsGateHonestly(tmp_path):
    """A recorded arXiv ID with nothing pushed to Overleaf must fail.

    The claim of correspondence cannot be demonstrated without a
    pushed-figure list, so the gate stays open and ``arxiv-mismatch``
    fires — a recorded connection is never vacuously satisfied.
    """
    sProjectRepo = str(tmp_path)
    _fnWriteAllGreenSyncCache(sProjectRepo)
    dictWorkflow = _fdictWorkflowWithOverleaf(
        sCommit="",  # no Overleaf push recorded
        dictArxiv={"sArxivId": "2401.00001", "sArxivVersion": "v1"},
    )
    with patch(
        "vaibify.reproducibility.arxivClient.fsResolveLatestVersion",
        return_value="v1",
    ):
        listBlockers = flistLevel2Blockers(dictWorkflow, sProjectRepo)
        bSynced = fbWorkflowFullySyncedWithArxiv(
            dictWorkflow, sProjectRepo,
        )
    listMismatch = [
        d for d in listBlockers
        if d["sCriterion"] == "arxiv-mismatch"
    ]
    assert len(listMismatch) == 1
    assert listMismatch[0]["sScope"] == "workflow"
    assert bSynced is False


# ----------------------------------------------------------------------
# arxiv-mismatch / clean (workflow-scope)
# ----------------------------------------------------------------------


def testArxivTarballHashesMatchOverleafPushClearsBlocker(tmp_path):
    """Tarball hashes equal to the manifest's clear ``arxiv-mismatch``."""
    sProjectRepo = str(tmp_path)
    _fnWriteAllGreenSyncCache(sProjectRepo)
    _fnWriteFigureFile(sProjectRepo)
    overleafSync.fnRecordOverleafPushManifest(
        sProjectRepo, "commitabc", ["A/plot.pdf"],
    )
    dictWorkflow = _fdictWorkflowWithOverleaf(
        sCommit="commitabc",
        dictArxiv={"sArxivId": "2401.00001", "sArxivVersion": "v1"},
    )
    with patch(
        "vaibify.reproducibility.arxivClient.fdictFetchRemoteHashes",
        return_value={"A/plot.pdf": S_FIGURE_SHA},
    ), patch(
        "vaibify.reproducibility.arxivClient.fsResolveLatestVersion",
        return_value="v1",
    ):
        listBlockers = flistLevel2Blockers(dictWorkflow, sProjectRepo)
        bSynced = fbWorkflowFullySyncedWithArxiv(
            dictWorkflow, sProjectRepo,
        )
    listMismatch = [
        d for d in listBlockers
        if d["sCriterion"] == "arxiv-mismatch"
    ]
    assert listMismatch == []
    assert bSynced is True


def testArxivTarballWithDifferentContentFiresMismatch(tmp_path):
    """A same-named figure with drifted content must NOT close the gate.

    Presence is not correspondence: the e-print carries ``A/plot.pdf``
    but its hash differs from the manifest's pin, so ``arxiv-mismatch``
    fires and the gate stays open.
    """
    sProjectRepo = str(tmp_path)
    _fnWriteAllGreenSyncCache(sProjectRepo)
    _fnWriteFigureFile(sProjectRepo)
    overleafSync.fnRecordOverleafPushManifest(
        sProjectRepo, "commitabc", ["A/plot.pdf"],
    )
    dictWorkflow = _fdictWorkflowWithOverleaf(
        sCommit="commitabc",
        dictArxiv={"sArxivId": "2401.00001", "sArxivVersion": "v1"},
    )
    with patch(
        "vaibify.reproducibility.arxivClient.fdictFetchRemoteHashes",
        return_value={"A/plot.pdf": "e" * 64},
    ), patch(
        "vaibify.reproducibility.arxivClient.fsResolveLatestVersion",
        return_value="v1",
    ):
        listBlockers = flistLevel2Blockers(dictWorkflow, sProjectRepo)
        bSynced = fbWorkflowFullySyncedWithArxiv(
            dictWorkflow, sProjectRepo,
        )
    listMismatch = [
        d for d in listBlockers
        if d["sCriterion"] == "arxiv-mismatch"
    ]
    assert len(listMismatch) == 1
    assert bSynced is False


def testArxivGateFailsWhenPushedFigureMissingLocally(tmp_path):
    """A pushed figure absent from the working tree cannot demonstrate sync.

    Live hashing has no expected value for a file that does not exist,
    so the gate fails conservatively — even when the tarball reports a
    hash for that name — instead of trusting name-level presence.
    """
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
        return_value={"A/plot.pdf": S_FIGURE_SHA},
    ), patch(
        "vaibify.reproducibility.arxivClient.fsResolveLatestVersion",
        return_value="v1",
    ):
        bSynced = fbWorkflowFullySyncedWithArxiv(
            dictWorkflow, sProjectRepo,
        )
    assert bSynced is False


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
            "saOutputDataFiles": [], "saPlotFiles": ["Plot/A12/foo.pdf"],
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
    """A data-only workflow surfaces no manuscript criteria at all.

    ``figure-not-frozen`` is suppressed without an Overleaf binding
    and the arXiv criteria are suppressed without a recorded arXiv
    submission, so the boolean L2 gate closes on GitHub + Zenodo +
    AI-declaration alone.
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
        "arxiv-mismatch", "arxiv-version-stale", "figure-not-frozen",
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
    """Recorded ``v1`` against arXiv's ``v2`` -> ``arxiv-version-stale``.

    The hash comparison is satisfied (manifest and tarball agree) so
    the version criterion is isolated: only ``arxiv-version-stale``
    fires.
    """
    sProjectRepo = str(tmp_path)
    _fnWriteAllGreenSyncCache(sProjectRepo)
    _fnWriteFigureFile(sProjectRepo)
    overleafSync.fnRecordOverleafPushManifest(
        sProjectRepo, "commitabc", ["A/plot.pdf"],
    )
    dictWorkflow = _fdictWorkflowWithOverleaf(
        sCommit="commitabc",
        dictArxiv={"sArxivId": "2401.00001", "sArxivVersion": "v1"},
    )
    with patch(
        "vaibify.reproducibility.arxivClient.fdictFetchRemoteHashes",
        return_value={"A/plot.pdf": S_FIGURE_SHA},
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
    listMismatch = [
        d for d in listBlockers
        if d["sCriterion"] == "arxiv-mismatch"
    ]
    assert listMismatch == []
