"""Markers must not downgrade an "unnecessary" verification category.

A marker reporting pass/fail results for a category the workflow
declares as empty-commands ("unnecessary") is anomalous — the test
commands were removed but the runner still produced a result.
The fix is to leave the unnecessary state intact and log the
discrepancy so the operator can investigate, rather than silently
downgrading the derived state and re-locking the all-green gate.
"""

import logging

from vaibify.gui.routes.pipelineRoutes import (
    _fnApplyExternalTestResults,
    _fnApplyMarkerCategory,
    _fnClearStaleMarkerCategories,
)


def test_marker_pass_does_not_downgrade_unnecessary(caplog):
    """Sticky-pass branch leaves the state and logs a warning."""
    dictVerify = {"sIntegrity": "unnecessary"}
    dictCategories = {"integrity": {"iPassed": 1, "iFailed": 0}}
    with caplog.at_level(logging.WARNING, logger="vaibify"):
        bChanged = _fnApplyMarkerCategory(
            dictVerify, dictCategories, "integrity", "sIntegrity",
        )
    assert bChanged is False
    assert dictVerify["sIntegrity"] == "unnecessary"
    assert any(
        r.levelno == logging.WARNING
        and "unnecessary" in r.getMessage()
        and "sIntegrity" in r.getMessage()
        for r in caplog.records
    ), (
        "Sticky-unnecessary path must emit a WARNING so the marker/"
        "workflow discrepancy is observable in logs."
    )


def test_marker_fail_does_not_downgrade_unnecessary(caplog):
    """Sticky-fail branch leaves the state and logs a warning."""
    dictVerify = {"sIntegrity": "unnecessary"}
    dictCategories = {"integrity": {"iPassed": 0, "iFailed": 3}}
    with caplog.at_level(logging.WARNING, logger="vaibify"):
        bChanged = _fnApplyMarkerCategory(
            dictVerify, dictCategories, "integrity", "sIntegrity",
        )
    assert bChanged is False
    assert dictVerify["sIntegrity"] == "unnecessary"
    assert any(
        r.levelno == logging.WARNING
        and "unnecessary" in r.getMessage()
        for r in caplog.records
    ), "Sticky-unnecessary failure path must also emit a WARNING."


def test_stale_marker_skips_unnecessary_category():
    """Stale-marker reset must skip unnecessary categories."""
    dictVerify = {
        "sIntegrity": "unnecessary",
        "sQualitative": "passed",
    }
    dictCategories = {
        "integrity": {"iPassed": 1, "iFailed": 0},
        "qualitative": {"iPassed": 1, "iFailed": 0},
    }
    bChanged = _fnClearStaleMarkerCategories(
        dictVerify, dictCategories,
    )
    assert bChanged is True
    assert dictVerify["sIntegrity"] == "unnecessary"
    assert dictVerify["sQualitative"] == "untested"


def test_apply_external_results_leaves_unnecessary_step_alone():
    """Top-level marker apply path leaves an unnecessary step untouched."""
    dictWorkflow = {"listSteps": [{
        "dictVerification": {
            "sIntegrity": "unnecessary",
            "sQualitative": "unnecessary",
            "sQuantitative": "unnecessary",
            "sUnitTest": "unnecessary",
            "sUser": "passed",
        },
    }]}
    dictTestMarkers = {
        "0": {
            "bStale": False,
            "dictMarker": {"dictCategories": {
                "integrity": {"iPassed": 1, "iFailed": 0},
            }},
        },
    }
    _fnApplyExternalTestResults(dictWorkflow, dictTestMarkers)
    dictV = dictWorkflow["listSteps"][0]["dictVerification"]
    assert dictV["sIntegrity"] == "unnecessary"
