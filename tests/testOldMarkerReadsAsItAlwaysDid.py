"""A marker written before runs existed reads exactly as it always did.

Published archives carry markers in the old single-session shape, and
an archive cannot be rewritten. The expected answers below were
produced by the derivation as it stood BEFORE the marker became a
per-test record (``truthDerivation.fdictComputeTestAxes`` at the commit
that introduced the racy-clean rule), run on a marker shaped like a
real project's, and are pinned here as literals: an independent oracle,
not the new code agreeing with itself.
"""

import pytest  # noqa: F401

from tests.markerStateSupport import fdictStatesFromCounts
from vaibify.gui import stateManager
from vaibify.gui import testMarkerContract as contract

S_FITS = "AiPowerOverTime/aiPowerFits.json"
S_FIGURE = "AiPowerOverTime/Plot/fig.pdf"
S_FIT_HASH, S_FIGURE_HASH = "a" * 40, "b" * 40


def _fdictOldMarker(**dictOverrides):
    dictMarker = {
        "sDirectory": "AiPowerOverTime", "sLabel": "A01", "iExitStatus": 0,
        "fTimestamp": 1790000000.0, "sRunAtUtc": "2026-10-04T12:00:00Z",
        "iCollected": 18,
        "dictCategories": {
            "integrity": {"iPassed": 6, "iFailed": 0},
            "qualitative": {"iPassed": 5, "iFailed": 0},
            "quantitative": {"iPassed": 7, "iFailed": 0}},
        "dictOutputHashes": {S_FITS: S_FIT_HASH, S_FIGURE: S_FIGURE_HASH},
        "dictInputHashes": {"data/raw.csv": "c" * 40},
    }
    dictMarker.update(dictOverrides)
    return dictMarker


def _fdictAllSame(sValue, listModifiedFiles=()):
    return {
        "listModifiedFiles": list(listModifiedFiles),
        "sIntegrity": sValue, "sQualitative": sValue,
        "sQuantitative": sValue, "sUnitTest": sValue,
        "sLastTestRun": "2026-10-04T12:00:00Z", "sUser": "",
    }


@pytest.mark.falsification
def test_the_bootstrap_of_an_old_marker_matches_the_old_derivation():
    """Kills: skipping the normalizer, which would read an old marker as
    having no runs and so derive nothing from it."""
    for dictOnDisk, dictOverrides, dictExpected in (
        ({S_FITS: S_FIT_HASH, S_FIGURE: S_FIGURE_HASH}, {},
         _fdictAllSame("passed-from-marker")),
        ({S_FITS: "d" * 40, S_FIGURE: S_FIGURE_HASH}, {},
         _fdictAllSame("outputs-changed", [S_FITS])),
        ({S_FIGURE: S_FIGURE_HASH}, {}, _fdictAllSame("outputs-missing")),
        ({S_FITS: S_FIT_HASH, S_FIGURE: S_FIGURE_HASH},
         {"iExitStatus": 1}, _fdictAllSame("failed")),
    ):
        dictAxes = stateManager._fdictVerificationFromMarker(
            _fdictOldMarker(**dictOverrides), dictOnDisk)
        assert dictAxes == dictExpected, dictOverrides


def test_an_old_marker_with_a_failing_category_demotes_the_aggregate():
    """The one deliberate difference from the old derivation.

    The old aggregate ignored per-category failures (it passed the whole
    category dict where the counts belonged) and read passed-from-marker
    over a failing category. The new one agrees with the category.
    """
    dictMarker = _fdictOldMarker(dictCategories={
        "integrity": {"iPassed": 0, "iFailed": 2},
        "qualitative": {"iPassed": 5, "iFailed": 0},
        "quantitative": {"iPassed": 7, "iFailed": 0}})
    dictAxes = stateManager._fdictVerificationFromMarker(
        dictMarker, {S_FITS: S_FIT_HASH, S_FIGURE: S_FIGURE_HASH})
    assert dictAxes["sIntegrity"] == "failed"
    assert dictAxes["sQualitative"] == "passed-from-marker"
    assert dictAxes["sUnitTest"] == "failed"


def test_an_old_marker_through_the_poll_applier_reads_its_counts():
    """Counts speak for a category exactly as ``fsResolveCategoryAxis...``
    did: any failure fails it, any pass with none failing passes it."""
    dictStates = fdictStatesFromCounts({
        "integrity": {"iPassed": 6, "iFailed": 0},
        "qualitative": {"iPassed": 1, "iFailed": 3},
        "quantitative": {"iPassed": 0, "iFailed": 0}})
    assert dictStates["integrity"]["sState"] == "passed"
    assert dictStates["qualitative"]["sState"] == "failed"
    assert dictStates["quantitative"]["bHasMarkerInfo"] is False


def test_normalizing_an_old_marker_loses_no_field_a_reader_used():
    dictOld = _fdictOldMarker()
    dictNormalized = contract.fdictNormalizeMarker(dictOld)
    dictRun = dictNormalized["dictRuns"]["legacy"]
    assert dictRun["fTimestamp"] == dictOld["fTimestamp"]
    assert dictRun["sRunAtUtc"] == dictOld["sRunAtUtc"]
    assert dictRun["iExitStatus"] == dictOld["iExitStatus"]
    assert dictRun["dictOutputHashes"] == dictOld["dictOutputHashes"]
    assert dictRun["dictInputHashes"] == dictOld["dictInputHashes"]
    assert dictNormalized["sDirectory"] == "AiPowerOverTime"
    assert dictNormalized["sLabel"] == "A01"
