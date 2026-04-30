"""Tests for marker staleness rules and stale-marker reset behavior.

The marker reconciliation in ``pipelineRoutes`` writes test results
into ``dictVerification`` on every poll. To stay in sync with the
data files the marker tested against, three staleness rules apply:

1. Markers without ``sRunAtUtc`` (legacy pre-2026-04 conftest format)
   are stale — they predate the workspace-as-git-repo migration and
   cannot be trusted.
2. Markers older than any test file are stale (existing behaviour).
3. Markers older than the most recent output file are stale — the
   tested data has moved.

When a marker is stale, the corresponding category is reset to
``"untested"`` rather than left at a misleading prior value.
"""

from vaibify.gui.routes.pipelineRoutes import (
    _fbMarkerStale,
    _fdictBuildTestMarkerStatus,
    _fnApplyExternalTestResults,
)


# -----------------------------------------------------------------------
# _fbMarkerStale: legacy and data-mtime checks
# -----------------------------------------------------------------------


def test_fbMarkerStale_legacy_marker_is_stale():
    """A marker without sRunAtUtc must be flagged as stale."""
    dictMarker = {"fTimestamp": 1_700_000_000.0}
    assert _fbMarkerStale(dictMarker, {}) is True


def test_fbMarkerStale_modern_marker_with_no_files_is_fresh():
    dictMarker = {
        "fTimestamp": 1_700_000_000.0,
        "sRunAtUtc": "2026-04-23T00:00:00Z",
    }
    assert _fbMarkerStale(dictMarker, {}) is False


def test_fbMarkerStale_data_mtime_newer_is_stale():
    """A modern marker is stale when output files are newer than it."""
    dictMarker = {
        "fTimestamp": 1_000.0,
        "sRunAtUtc": "2026-04-23T00:00:00Z",
    }
    bStale = _fbMarkerStale(
        dictMarker, {}, fMaxOutputMtime=2_000.0,
    )
    assert bStale is True


def test_fbMarkerStale_data_mtime_equal_is_fresh():
    """Equality counts as fresh (not strictly newer)."""
    dictMarker = {
        "fTimestamp": 2_000.0,
        "sRunAtUtc": "2026-04-23T00:00:00Z",
    }
    bStale = _fbMarkerStale(
        dictMarker, {}, fMaxOutputMtime=2_000.0,
    )
    assert bStale is False


def test_fbMarkerStale_data_mtime_older_is_fresh():
    dictMarker = {
        "fTimestamp": 5_000.0,
        "sRunAtUtc": "2026-04-23T00:00:00Z",
    }
    bStale = _fbMarkerStale(
        dictMarker, {}, fMaxOutputMtime=1_000.0,
    )
    assert bStale is False


def test_fbMarkerStale_test_file_newer_still_wins():
    """The pre-existing test-file check is preserved."""
    dictMarker = {
        "fTimestamp": 1_000.0,
        "sRunAtUtc": "2026-04-23T00:00:00Z",
    }
    dictTestFiles = {"dictMtimes": {"test_a.py": 5_000.0}}
    assert _fbMarkerStale(dictMarker, dictTestFiles) is True


def test_fbMarkerStale_default_fMaxOutputMtime_does_not_flag():
    """Calls without the new arg behave as before for fresh markers."""
    dictMarker = {
        "fTimestamp": 1_000.0,
        "sRunAtUtc": "2026-04-23T00:00:00Z",
    }
    assert _fbMarkerStale(dictMarker, {}) is False


# -----------------------------------------------------------------------
# _fdictBuildTestMarkerStatus: per-step output-mtime threading
# -----------------------------------------------------------------------


def test_fdictBuildTestMarkerStatus_marks_data_superseded_as_stale():
    dictWorkflow = {"listSteps": [{"sDirectory": "step1"}]}
    dictTestInfo = {
        "markers": {
            "step1.json": {
                "fTimestamp": 1_000.0,
                "sRunAtUtc": "2026-04-23T00:00:00Z",
                "dictCategories": {
                    "quantitative": {"iPassed": 5, "iFailed": 0},
                },
            },
        },
        "testFiles": {},
    }
    dictResult = _fdictBuildTestMarkerStatus(
        dictWorkflow, dictTestInfo,
        dictMaxOutputMtimeByStep={"0": "9999"},
    )
    assert dictResult["0"]["bStale"] is True


def test_fdictBuildTestMarkerStatus_keeps_marker_fresh_when_data_older():
    dictWorkflow = {"listSteps": [{"sDirectory": "step1"}]}
    dictTestInfo = {
        "markers": {
            "step1.json": {
                "fTimestamp": 9_000.0,
                "sRunAtUtc": "2026-04-23T00:00:00Z",
                "dictCategories": {
                    "quantitative": {"iPassed": 5, "iFailed": 0},
                },
            },
        },
        "testFiles": {},
    }
    dictResult = _fdictBuildTestMarkerStatus(
        dictWorkflow, dictTestInfo,
        dictMaxOutputMtimeByStep={"0": "1000"},
    )
    assert dictResult["0"]["bStale"] is False


def test_fdictBuildTestMarkerStatus_legacy_marker_stale_regardless_of_mtime():
    """A marker without sRunAtUtc is stale even if the data is older."""
    dictWorkflow = {"listSteps": [{"sDirectory": "step1"}]}
    dictTestInfo = {
        "markers": {
            "step1.json": {
                "fTimestamp": 9_000.0,
                "dictCategories": {
                    "quantitative": {"iPassed": 5, "iFailed": 0},
                },
            },
        },
        "testFiles": {},
    }
    dictResult = _fdictBuildTestMarkerStatus(
        dictWorkflow, dictTestInfo,
        dictMaxOutputMtimeByStep={"0": "1000"},
    )
    assert dictResult["0"]["bStale"] is True


# -----------------------------------------------------------------------
# _fnApplyExternalTestResults: stale-marker reset behaviour
# -----------------------------------------------------------------------


def test_fnApplyExternalTestResults_stale_marker_resets_corrupted_passed():
    """The A09 case: stale marker had previously written 'passed';
    next poll resets the corrupted value to 'untested'."""
    dictWorkflow = {
        "listSteps": [
            {
                "sDirectory": "BayesianPosteriors",
                "dictVerification": {"sQuantitative": "passed"},
            },
        ],
    }
    dictTestMarkers = {
        "0": {
            "bStale": True,
            "dictMarker": {
                "dictCategories": {
                    "quantitative": {"iPassed": 79, "iFailed": 0},
                },
            },
        },
    }
    bChanged = _fnApplyExternalTestResults(
        dictWorkflow, dictTestMarkers,
    )
    assert bChanged is True
    dictVerify = dictWorkflow["listSteps"][0]["dictVerification"]
    assert dictVerify["sQuantitative"] == "untested"


def test_fnApplyExternalTestResults_stale_marker_resets_corrupted_failed():
    """The A11 case: data was regenerated, marker is stale, the
    previously-applied 'failed' value gets reset to 'untested'."""
    dictWorkflow = {
        "listSteps": [
            {
                "sDirectory": "CumulativeXuvAndCosmicShoreline",
                "dictVerification": {"sQuantitative": "failed"},
            },
        ],
    }
    dictTestMarkers = {
        "0": {
            "bStale": True,
            "dictMarker": {
                "dictCategories": {
                    "quantitative": {"iPassed": 0, "iFailed": 10},
                },
            },
        },
    }
    bChanged = _fnApplyExternalTestResults(
        dictWorkflow, dictTestMarkers,
    )
    assert bChanged is True
    dictVerify = dictWorkflow["listSteps"][0]["dictVerification"]
    assert dictVerify["sQuantitative"] == "untested"


def test_fnApplyExternalTestResults_stale_marker_skips_already_untested():
    """An already-untested category isn't re-written, so bChanged stays
    False unless something actually moved."""
    dictWorkflow = {
        "listSteps": [
            {
                "sDirectory": "step1",
                "dictVerification": {"sQuantitative": "untested"},
            },
        ],
    }
    dictTestMarkers = {
        "0": {
            "bStale": True,
            "dictMarker": {
                "dictCategories": {
                    "quantitative": {"iPassed": 5, "iFailed": 0},
                },
            },
        },
    }
    bChanged = _fnApplyExternalTestResults(
        dictWorkflow, dictTestMarkers,
    )
    assert bChanged is False


def test_fnApplyExternalTestResults_stale_marker_only_touches_named_categories():
    """A stale marker that only mentions quantitative does not reset
    sIntegrity or sQualitative."""
    dictWorkflow = {
        "listSteps": [
            {
                "sDirectory": "step1",
                "dictVerification": {
                    "sIntegrity": "passed",
                    "sQualitative": "passed",
                    "sQuantitative": "passed",
                },
            },
        ],
    }
    dictTestMarkers = {
        "0": {
            "bStale": True,
            "dictMarker": {
                "dictCategories": {
                    "quantitative": {"iPassed": 5, "iFailed": 0},
                },
            },
        },
    }
    _fnApplyExternalTestResults(dictWorkflow, dictTestMarkers)
    dictVerify = dictWorkflow["listSteps"][0]["dictVerification"]
    assert dictVerify["sIntegrity"] == "passed"
    assert dictVerify["sQualitative"] == "passed"
    assert dictVerify["sQuantitative"] == "untested"


def test_fnApplyExternalTestResults_fresh_marker_writes_passed():
    """Sanity: a fresh marker still applies its result normally."""
    dictWorkflow = {
        "listSteps": [
            {"sDirectory": "step1", "dictVerification": {}},
        ],
    }
    dictTestMarkers = {
        "0": {
            "bStale": False,
            "dictMarker": {
                "dictCategories": {
                    "quantitative": {"iPassed": 5, "iFailed": 0},
                },
            },
        },
    }
    bChanged = _fnApplyExternalTestResults(
        dictWorkflow, dictTestMarkers,
    )
    assert bChanged is True
    dictVerify = dictWorkflow["listSteps"][0]["dictVerification"]
    assert dictVerify["sQuantitative"] == "passed"


def test_fnApplyExternalTestResults_returns_false_when_nothing_changed():
    """Idempotency: applying the same fresh marker twice doesn't
    keep flipping bChanged."""
    dictWorkflow = {
        "listSteps": [
            {
                "sDirectory": "step1",
                "dictVerification": {"sQuantitative": "passed"},
            },
        ],
    }
    dictTestMarkers = {
        "0": {
            "bStale": False,
            "dictMarker": {
                "dictCategories": {
                    "quantitative": {"iPassed": 5, "iFailed": 0},
                },
            },
        },
    }
    bChanged = _fnApplyExternalTestResults(
        dictWorkflow, dictTestMarkers,
    )
    assert bChanged is False
