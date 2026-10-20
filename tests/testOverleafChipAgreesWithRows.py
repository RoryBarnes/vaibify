"""The Level 2 chip and the Overleaf rows fail on the same set.

The Level 2 gate never read Overleaf, while a BOUND Overleaf project
shows two things beneath the chip: a ``figure-not-frozen`` blocker on
each step whose figure was not pushed, and an Overleaf manuscript row
coloured by the last Overleaf verify. So the chip could read Level 2
above a red step cell and an orange row. Approved ruling F5
(2026-10-19): the gate counts Overleaf only when an Overleaf project is
bound; unbound stays "not applicable" and never blocks; the rows are
not made quieter.
"""

import pytest

from vaibify.reproducibility import overleafSync
from vaibify.reproducibility.levelGates import (
    _fbComputeLevel2,
    flistLevel2Blockers,
)

from tests.testLevelGateL2Arxiv import (
    _fdictFreshGithubCache,
    _fdictFreshZenodoCache,
    _fdictWorkflowWithOverleaf,
    _fnWriteSyncStatusFile,
    _fsBuildIsoTimestamp,
    _I_SCOPE_VERSION,
)

S_COMMIT = "commitabc"
LIST_EVERY_PLOT = ["A/plot.pdf", "Decl/declaration.pdf"]


def _fdictOverleafCache(listDiverged=(), fHoursAgo=1.0):
    """An Overleaf verify in the shape the verify writes it."""
    return {
        "sService": "overleaf",
        "sLastVerified": _fsBuildIsoTimestamp(fHoursAgo=fHoursAgo),
        "iTotalFiles": len(LIST_EVERY_PLOT),
        "iMatching": len(LIST_EVERY_PLOT) - len(listDiverged),
        "listDiverged": [{"sPath": sPath} for sPath in listDiverged],
        "listComparedPaths": list(LIST_EVERY_PLOT),
        "iScopeVersion": _I_SCOPE_VERSION,
    }


def _fnWriteCaches(sProjectRepo, dictOverleaf=None):
    dictPerService = {"github": _fdictFreshGithubCache(),
                      "zenodo": _fdictFreshZenodoCache()}
    if dictOverleaf is not None:
        dictPerService["overleaf"] = dictOverleaf
    _fnWriteSyncStatusFile(sProjectRepo, dictPerService)


def _flistCriteria(dictWorkflow, sProjectRepo):
    return [dictEntry["sCriterion"] for dictEntry in
            flistLevel2Blockers(dictWorkflow, sProjectRepo)]


def testABoundProjectWithEveryFigurePushedAndVerifiedReachesLevelTwo(
        tmp_path):
    sProjectRepo = str(tmp_path)
    _fnWriteCaches(sProjectRepo, _fdictOverleafCache())
    overleafSync.fnRecordOverleafPushManifest(
        sProjectRepo, S_COMMIT, LIST_EVERY_PLOT)
    dictWorkflow = _fdictWorkflowWithOverleaf(sCommit=S_COMMIT)
    assert _flistCriteria(dictWorkflow, sProjectRepo) == []
    assert _fbComputeLevel2(dictWorkflow, sProjectRepo) is True


@pytest.mark.falsification
def testAnUnpushedFigureOfABoundProjectHoldsTheChipBelowLevelTwo(tmp_path):
    """Kills: dropping the bound-Overleaf conjunct from the gate."""
    sProjectRepo = str(tmp_path)
    _fnWriteCaches(sProjectRepo, _fdictOverleafCache())
    overleafSync.fnRecordOverleafPushManifest(
        sProjectRepo, S_COMMIT, ["A/plot.pdf"])
    dictWorkflow = _fdictWorkflowWithOverleaf(sCommit=S_COMMIT)
    assert "figure-not-frozen" in _flistCriteria(dictWorkflow, sProjectRepo)
    assert _fbComputeLevel2(dictWorkflow, sProjectRepo) is False


@pytest.mark.parametrize("dictOverleaf", [
    None,
    _fdictOverleafCache(listDiverged=["A/plot.pdf"]),
    _fdictOverleafCache(fHoursAgo=10000.0),
], ids=["never-verified", "diverged", "stale"])
def testABoundProjectWhoseOverleafRowIsNotGreenIsNotLevelTwo(
        tmp_path, dictOverleaf):
    sProjectRepo = str(tmp_path)
    _fnWriteCaches(sProjectRepo, dictOverleaf)
    overleafSync.fnRecordOverleafPushManifest(
        sProjectRepo, S_COMMIT, LIST_EVERY_PLOT)
    dictWorkflow = _fdictWorkflowWithOverleaf(sCommit=S_COMMIT)
    assert _fbComputeLevel2(dictWorkflow, sProjectRepo) is False
    assert _flistCriteria(dictWorkflow, sProjectRepo), (
        "the gate refused, so a blocker must say why")


def testAnUnboundProjectIsNeverBlockedByOverleaf(tmp_path):
    sProjectRepo = str(tmp_path)
    _fnWriteCaches(sProjectRepo)
    dictWorkflow = _fdictWorkflowWithOverleaf(sCommit=S_COMMIT)
    del dictWorkflow["dictRemotes"]["overleaf"]
    assert _flistCriteria(dictWorkflow, sProjectRepo) == []
    assert _fbComputeLevel2(dictWorkflow, sProjectRepo) is True
