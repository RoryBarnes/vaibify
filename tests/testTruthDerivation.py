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


def testAggregateUnitTestFromAllMarkerAxesIsPassedFromMarker():
    """All-green marker-restored axes fold green and keep the honest
    marker provenance in the aggregate."""
    assert fsAggregateUnitTestFromAxes(
        ["passed-from-marker", "passed-from-marker"]
    ) == "passed-from-marker"


def testAggregateUnitTestFromMixedFreshAndMarkerIsPassedFromMarker():
    """A mix of fresh passes and marker-restored passes is still green;
    any marker provenance makes the aggregate ``passed-from-marker``."""
    assert fsAggregateUnitTestFromAxes(
        ["passed", "passed-from-marker", "passed"]
    ) == "passed-from-marker"


def testAggregateUnitTestFailedBeatsMarkerGreen():
    """A failure short-circuits even when sibling axes are
    marker-green."""
    assert fsAggregateUnitTestFromAxes(
        ["passed-from-marker", "failed"]
    ) == "failed"


def testAggregateUnitTestMarkerWithUntestedIsUntested():
    """Marker-green plus a never-run category is not green overall."""
    assert fsAggregateUnitTestFromAxes(
        ["passed-from-marker", "untested"]
    ) == "untested"


def testAggregateUnitTestUnnecessaryCountsAsGreen():
    """``unnecessary`` axes never drag a green fold down to untested."""
    assert fsAggregateUnitTestFromAxes(
        ["passed", "unnecessary"]
    ) == "passed"


def testAggregateUnitTestOutputsChangedIsUntested():
    """Hash-drift axis values are not green and fold to untested."""
    assert fsAggregateUnitTestFromAxes(
        ["passed", "outputs-changed"]
    ) == "untested"


def _fdictStepWithCategoryStates(dictStates):
    """Build a step with commands for every category in ``dictStates``."""
    dictTests = {}
    dictVerification = {"sUnitTest": "untested"}
    dictKeyByCategory = {
        "sIntegrity": "dictIntegrity",
        "sQualitative": "dictQualitative",
        "sQuantitative": "dictQuantitative",
    }
    for sVerifKey, sState in dictStates.items():
        dictTests[dictKeyByCategory[sVerifKey]] = {
            "saCommands": ["python -m pytest tests -v"],
        }
        dictVerification[sVerifKey] = sState
    return {
        "dictTests": dictTests,
        "dictVerification": dictVerification,
    }


def testRefreshAggregateTestStatesHealsStuckUntestedAggregate():
    """A persisted ``untested`` aggregate over all-marker-green axes
    self-corrects to ``passed-from-marker`` (the live-workflow bug)."""
    from vaibify.gui.testStatusManager import fbRefreshAggregateTestStates
    dictStep = _fdictStepWithCategoryStates({
        "sIntegrity": "passed-from-marker",
        "sQualitative": "passed-from-marker",
        "sQuantitative": "passed-from-marker",
    })
    dictWorkflow = {"listSteps": [dictStep]}
    assert fbRefreshAggregateTestStates(dictWorkflow) is True
    assert dictStep["dictVerification"]["sUnitTest"] == (
        "passed-from-marker"
    )


def testRefreshAggregateTestStatesReportsNoChangeWhenCorrect():
    """A correct aggregate is left untouched and reports no change."""
    from vaibify.gui.testStatusManager import fbRefreshAggregateTestStates
    dictStep = _fdictStepWithCategoryStates({
        "sIntegrity": "passed",
        "sQualitative": "passed",
        "sQuantitative": "passed",
    })
    dictStep["dictVerification"]["sUnitTest"] = "passed"
    dictWorkflow = {"listSteps": [dictStep]}
    assert fbRefreshAggregateTestStates(dictWorkflow) is False
    assert dictStep["dictVerification"]["sUnitTest"] == "passed"


def testRefreshAggregateTestStatesSkipsStepsWithoutEligibleCategories():
    """Steps with no category commands keep their aggregate untouched
    (a legacy unit-test ``passed`` must not be clobbered)."""
    from vaibify.gui.testStatusManager import fbRefreshAggregateTestStates
    dictStep = {
        "dictTests": {},
        "dictVerification": {"sUnitTest": "passed"},
    }
    dictWorkflow = {"listSteps": [dictStep, None]}
    assert fbRefreshAggregateTestStates(dictWorkflow) is False
    assert dictStep["dictVerification"]["sUnitTest"] == "passed"


def testMissingOutputOutranksDriftedOutput(dictMarkerAllPassed):
    """A marker simultaneously missing one output and drifting another
    reports the more severe ``outputs-missing`` on every axis, never the
    milder ``outputs-changed``. ``step1/out.json`` is absent on disk
    (missing) while ``step1/data.csv`` is present but drifted."""
    dictOnDisk = {"step1/data.csv": "sha-DRIFTED"}
    dictAxes = fdictComputeTestAxes(
        dictMarkerAllPassed, dictOnDisk, T_AVAILABLE_CATEGORIES,
    )
    assert dictAxes["sIntegrity"] == "outputs-missing"
    assert dictAxes["sUnitTest"] == "outputs-missing"
    assert dictAxes["sQualitative"] == "outputs-missing"
    assert dictAxes["sQuantitative"] == "outputs-missing"


def testMarkerWithoutExitStatusDefaultsToCleanPass(dictMarkerAllPassed):
    """A hash-clean marker that never stamped ``iExitStatus`` (older
    schema) must default to a clean exit and certify
    ``passed-from-marker`` — not a fabricated failure."""
    dictMarker = dict(dictMarkerAllPassed)
    dictMarker.pop("iExitStatus", None)
    assert "iExitStatus" not in dictMarker
    dictOnDisk = {"step1/out.json": "sha-A", "step1/data.csv": "sha-B"}
    dictAxes = fdictComputeTestAxes(
        dictMarker, dictOnDisk, T_AVAILABLE_CATEGORIES,
    )
    assert dictAxes["sIntegrity"] == "passed-from-marker"
    assert dictAxes["sQualitative"] == "passed-from-marker"
    assert dictAxes["sQuantitative"] == "passed-from-marker"
    assert dictAxes["sUnitTest"] == "passed-from-marker"


def testAggregateAllUnnecessaryAxesStaysUnnecessary():
    """Axes that all resolved to ``unnecessary`` must aggregate to
    ``unnecessary`` — never a fabricated ``passed`` for a step where no
    test actually ran."""
    assert fsAggregateUnitTestFromAxes(["unnecessary"]) == "unnecessary"
    assert fsAggregateUnitTestFromAxes(
        ["unnecessary", "unnecessary"]) == "unnecessary"


def testChangedOutputsAreReportedInStableSortedOrder():
    """When two or more outputs drift, ``listModifiedFiles`` is stable
    sorted order, independent of marker-dict insertion order, so the
    persisted state.json is reproducible."""
    dictMarker = dict(dictMarkerAllPassedFixtureUnused())
    dictMarker["dictOutputHashes"] = {
        "step1/zeta.csv": "sha-Z",
        "step1/alpha.csv": "sha-A",
        "step1/middle.csv": "sha-M",
    }
    dictMarker["dictCategories"] = {
        "integrity": {"iPassed": 1, "iFailed": 0},
    }
    dictOnDisk = {
        "step1/zeta.csv": "sha-Z-DRIFTED",
        "step1/alpha.csv": "sha-A-DRIFTED",
        "step1/middle.csv": "sha-M",
    }
    dictAxes = fdictComputeTestAxes(
        dictMarker, dictOnDisk, T_AVAILABLE_CATEGORIES,
    )
    listExpected = ["step1/alpha.csv", "step1/zeta.csv"]
    assert dictAxes["listModifiedFiles"] == listExpected
    assert dictAxes["listModifiedFiles"] == sorted(
        ["step1/zeta.csv", "step1/alpha.csv"])


def dictMarkerAllPassedFixtureUnused():
    """A plain marker dict for tests that do not need the fixture."""
    return {
        "sRunAtUtc": "2026-01-01T00:00:00Z",
        "iExitStatus": 0,
        "dictOutputHashes": {},
        "dictCategories": {},
    }
