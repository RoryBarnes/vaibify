"""Unit tests for ``vaibify.gui.truthDerivation``.

These tests pin the canonical truth-derivation contract: given a
marker and a hash observation, the four-axis dict must reflect
*observation*, not *assertion*. Any future L2/L3 truth predicate
that lands in ``truthDerivation`` belongs in this file too.
"""

import pytest

from vaibify.gui.truthDerivation import (
    fdictComputeTestAxes,
    fsAggregateUnitTestFromAxes,
    fsResolveCategoryAxisFromCounts,
    fsResolveUnitTestFromExitCode,
)


T_AVAILABLE_CATEGORIES = ("integrity", "qualitative", "quantitative")


@pytest.fixture
def dictMarkerAllPassed():
    """A marker recording a clean run for three integrity tests."""
    return {
        "sRunAtUtc": "2026-01-01T00:00:00Z",
        "iExitStatus": 0,
        "dictOutputHashes": {
            "step1/out.json": "sha-A",
            "step1/data.csv": "sha-B",
        },
        "dictCategories": {
            "integrity": {"iPassed": 3, "iFailed": 0},
            "qualitative": {"iPassed": 1, "iFailed": 0},
            "quantitative": {"iPassed": 2, "iFailed": 0},
        },
    }


def testMatchingHashesProducePassedFromMarker(dictMarkerAllPassed):
    """All-passing marker + matching hashes → every axis ``passed-from-marker``."""
    dictOnDisk = {"step1/out.json": "sha-A", "step1/data.csv": "sha-B"}
    dictAxes = fdictComputeTestAxes(
        dictMarkerAllPassed, dictOnDisk, T_AVAILABLE_CATEGORIES,
    )
    assert dictAxes["sUnitTest"] == "passed-from-marker"
    assert dictAxes["sIntegrity"] == "passed-from-marker"
    assert dictAxes["sQualitative"] == "passed-from-marker"
    assert dictAxes["sQuantitative"] == "passed-from-marker"
    assert dictAxes["listModifiedFiles"] == []
    assert dictAxes["sLastTestRun"] == "2026-01-01T00:00:00Z"


def testNonZeroExitStatusDemotesAxes(dictMarkerAllPassed):
    """A non-zero ``iExitStatus`` short-circuits every axis to ``failed``."""
    dictMarker = dict(dictMarkerAllPassed)
    dictMarker["iExitStatus"] = 1
    dictOnDisk = {"step1/out.json": "sha-A", "step1/data.csv": "sha-B"}
    dictAxes = fdictComputeTestAxes(
        dictMarker, dictOnDisk, T_AVAILABLE_CATEGORIES,
    )
    assert dictAxes["sUnitTest"] == "failed"
    assert dictAxes["sIntegrity"] == "failed"
    assert dictAxes["sQualitative"] == "failed"
    assert dictAxes["sQuantitative"] == "failed"


def testMismatchedHashesReportOutputsChanged(dictMarkerAllPassed):
    """A hash mismatch flips axes to ``outputs-changed`` and lists the path."""
    dictOnDisk = {"step1/out.json": "sha-DRIFTED", "step1/data.csv": "sha-B"}
    dictAxes = fdictComputeTestAxes(
        dictMarkerAllPassed, dictOnDisk, T_AVAILABLE_CATEGORIES,
    )
    assert dictAxes["sUnitTest"] == "outputs-changed"
    assert dictAxes["sIntegrity"] == "outputs-changed"
    assert "step1/out.json" in dictAxes["listModifiedFiles"]


def testMissingOutputsReportOutputsMissing(dictMarkerAllPassed):
    """A missing on-disk output flips every axis to ``outputs-missing``."""
    dictOnDisk = {"step1/out.json": "sha-A"}
    dictAxes = fdictComputeTestAxes(
        dictMarkerAllPassed, dictOnDisk, T_AVAILABLE_CATEGORIES,
    )
    assert dictAxes["sUnitTest"] == "outputs-missing"
    assert dictAxes["sIntegrity"] == "outputs-missing"
    assert dictAxes["sQualitative"] == "outputs-missing"
    assert dictAxes["sQuantitative"] == "outputs-missing"


def testEmptyMarkerCollapsesToUntested():
    """An empty/missing marker leaves every axis ``untested``."""
    dictAxes = fdictComputeTestAxes({}, {}, T_AVAILABLE_CATEGORIES)
    assert dictAxes["sUnitTest"] == "untested"
    assert dictAxes["sIntegrity"] == "untested"
    assert dictAxes["sQualitative"] == "untested"
    assert dictAxes["sQuantitative"] == "untested"
    assert dictAxes["listModifiedFiles"] == []


def testMarkerWithoutHashesSignalsUntested():
    """A marker missing ``dictOutputHashes`` cannot certify anything."""
    dictMarker = {"iExitStatus": 0, "dictCategories": {}}
    dictAxes = fdictComputeTestAxes(
        dictMarker, {}, T_AVAILABLE_CATEGORIES,
    )
    assert dictAxes["sUnitTest"] == "untested"
    assert dictAxes["sIntegrity"] == "untested"


def testCategoryWithFailureDemotesOnlyThatAxis(dictMarkerAllPassed):
    """A failed-count > 0 in one category demotes only that axis."""
    dictMarker = dict(dictMarkerAllPassed)
    dictMarker["dictCategories"] = {
        "integrity": {"iPassed": 0, "iFailed": 2},
        "qualitative": {"iPassed": 1, "iFailed": 0},
        "quantitative": {"iPassed": 1, "iFailed": 0},
    }
    dictOnDisk = {"step1/out.json": "sha-A", "step1/data.csv": "sha-B"}
    dictAxes = fdictComputeTestAxes(
        dictMarker, dictOnDisk, T_AVAILABLE_CATEGORIES,
    )
    assert dictAxes["sIntegrity"] == "failed"
    assert dictAxes["sQualitative"] == "passed-from-marker"
    assert dictAxes["sQuantitative"] == "passed-from-marker"


def testResolveCategoryAxisFromCountsReturnsFailedOnFailure():
    """Any positive ``iFailed`` short-circuits to ``failed``."""
    assert fsResolveCategoryAxisFromCounts(
        {"iPassed": 5, "iFailed": 1}) == "failed"


def testResolveCategoryAxisFromCountsReturnsPassedOnAllPass():
    """At least one pass with zero failures yields ``passed``."""
    assert fsResolveCategoryAxisFromCounts(
        {"iPassed": 3, "iFailed": 0}) == "passed"


def testResolveCategoryAxisFromCountsReturnsEmptyOnZeros():
    """Neither pass nor fail leaves the axis untouched (empty string)."""
    assert fsResolveCategoryAxisFromCounts(
        {"iPassed": 0, "iFailed": 0}) == ""


def testAggregateUnitTestFromEmptyAxesIsUnnecessary():
    """No categories demanded means no badge to compute."""
    assert fsAggregateUnitTestFromAxes([]) == "unnecessary"


def testAggregateUnitTestFromFailedAxesIsFailed():
    """One failed axis short-circuits the aggregate."""
    assert fsAggregateUnitTestFromAxes(
        ["passed", "failed", "passed"]) == "failed"


def testAggregateUnitTestFromAllPassedIsPassed():
    """All-passed (fresh runs) folds to ``passed``."""
    assert fsAggregateUnitTestFromAxes(
        ["passed", "passed"]) == "passed"


def testAggregateUnitTestFromMixedStatesIsUntested():
    """Mixed passed-from-marker/untested signals incomplete coverage."""
    assert fsAggregateUnitTestFromAxes(
        ["passed", "untested"]) == "untested"


def testResolveUnitTestFromExitCodeMapsZeroToPassed():
    """A clean exit is the canonical fresh-run pass signal."""
    assert fsResolveUnitTestFromExitCode(0) == "passed"


def testResolveUnitTestFromExitCodeMapsNonZeroToFailed():
    """Any non-zero exit yields ``failed`` regardless of magnitude."""
    assert fsResolveUnitTestFromExitCode(1) == "failed"
    assert fsResolveUnitTestFromExitCode(137) == "failed"
