"""A warning raised before a run reaches the screen.

The runner emits ``preflightWarning`` events for low disk space and for
a test-support file it could not refresh, and lets the run go ahead.
The frontend had no branch for the event, so every such warning was
dropped -- including the one that says the dashboard's test badges may
be showing an earlier run's verdict (found while fixing that case).

Driven by dispatching the real event through the real handler.
"""

import pytest


pytestmark = pytest.mark.browser


@pytest.mark.falsification
def test_a_pre_run_warning_is_written_to_the_run_output_and_announced(
    pageDashboard, serverHub,
):
    """Kills: dropping the preflightWarning branch from the handler."""
    pageDashboard.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    pageDashboard.wait_for_function(
        "() => typeof VaibifyPipelineRunner !== 'undefined'",
        timeout=15000,
    )
    sWarning = "Could not update 1 test-support file(s)"
    dictShown = pageDashboard.evaluate("""(sMessage) => {
        VaibifyPipelineRunner.fnHandlePipelineEvent(
            {sType: "preflightWarning", sMessage: sMessage});
        const elOutput = document.getElementById("pipelineOutput");
        const elToast = document.querySelector(".toast");
        return {
            sOutput: elOutput ? elOutput.textContent : "",
            sToast: elToast ? elToast.textContent : "",
        };
    }""", sWarning)
    assert "WARNING: " + sWarning in dictShown["sOutput"], dictShown
    assert "See the run log" in dictShown["sToast"], dictShown
    assert pageDashboard.listPageErrors == []
