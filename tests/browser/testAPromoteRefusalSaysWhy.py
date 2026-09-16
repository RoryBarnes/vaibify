"""A refused publish is an error with a reason, never a quiet success.

The promote route returns refusals as 200s with ``bSuccess: false``
and the reason in ``sMessage``. The action's toast was a static
"Publishing on production Zenodo..." shown over BOTH answers, so a
researcher watched a refusal render as a success announcement whose
result never arrived, and asked whether anything was archiving at
all (2026-09-16). The parsed reason had been in the response the
whole time.
"""

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow


pytestmark = pytest.mark.browser


@pytest.mark.falsification
def test_the_promote_toast_reads_the_answer_it_reports(
    pageDashboard, serverHub,
):
    """ONE open, both outcomes through the real describer.

    Kills: restoring the static success toast -- a refusal then
    reads "Publishing on production Zenodo" and the researcher waits
    for a deposit nobody started.
    """
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)

    dictRefused = pageDashboard.evaluate(
        """() => VaibifyApp.fdictDescribePromoteOutcome({
            bSuccess: false, sErrorType: "unknown",
            sMessage: "no production token is configured",
        })"""
    )
    assert dictRefused["sType"] == "error", dictRefused
    assert "refused" in dictRefused["sMessage"], dictRefused
    assert "no production token is configured" in (
        dictRefused["sMessage"]
    ), "the refusal's own reason was dropped again"
    assert "Publishing on production Zenodo" not in (
        dictRefused["sMessage"]
    ), "a refusal still announces a publish"

    # --- Zenodo's overload answers name WHOSE problem it is. The
    # raw refusal is a gateway's whole HTML page inside a Python
    # traceback; a researcher shown that reasonably reads it as
    # their own failure (asked twice, live, 2026-09-16).
    dictOverloaded = pageDashboard.evaluate(
        """() => VaibifyApp.fdictDescribePromoteOutcome({
            bSuccess: false, sErrorType: "unknown",
            sMessage: "Traceback (most recent call last):\\n" +
                "  File \\"<string>\\", line 28, in <module>\\n" +
                "zenodoClient.ZenodoError: Zenodo API error (504): " +
                "<html><body><h1>504 Gateway Time-out</h1> The " +
                "server didn't respond in time. </body></html>",
        })"""
    )
    assert dictOverloaded["sType"] == "error"
    assert "Zenodo's servers are overloaded" in (
        dictOverloaded["sMessage"]
    ), dictOverloaded
    assert "status.zenodo.org" in dictOverloaded["sMessage"]
    assert "<html>" not in dictOverloaded["sMessage"], (
        "a gateway's HTML page reached the toast raw"
    )
    assert "Traceback" not in dictOverloaded["sMessage"], (
        "a Python traceback reached the toast raw"
    )

    dictPublished = pageDashboard.evaluate(
        """() => VaibifyApp.fdictDescribePromoteOutcome({
            bSuccess: true, sOutput: "Published: 4242",
        })"""
    )
    assert dictPublished["sType"] == "success", dictPublished
    assert "Verify now" in dictPublished["sMessage"], (
        "the success toast no longer names the researcher's next step"
    )
