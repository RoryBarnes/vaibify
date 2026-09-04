"""The exit-code explanation reaches the screen, not just the event.

A researcher driving the shipped example on Ubuntu saw "Exit code:
127" and had no way to act on it. The server now stamps a cause onto
the ``commandFailed`` event — but a green Python suite proves only
that the STRING was computed. What the researcher gets is whatever the
frontend does with it, and that is a different question.

Driven by dispatching the real event through the real handler in a
real browser. The toast is asserted because it is the surface a
researcher actually reads: the run output scrolls, and on a failure at
the first step there is little else on screen.
"""

import pytest


pytestmark = pytest.mark.browser


def _fnLoadTheDashboardScripts(page, serverHub):
    """Load the hub page only, claiming no project.

    The handler under test needs no project: it is a pure function of
    the event it is handed. Claiming one would hold the container
    record through a 30-second reconnect grace and refuse every second
    test in this module — measured, and the reason several browser
    modules here open nothing.
    """
    page.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    page.wait_for_function(
        "() => typeof VaibifyPipelineRunner !== 'undefined'",
        timeout=15000,
    )


_S_DISPATCH_FAILURE = """(dictEvent) => {
    VaibifyPipelineRunner.fnHandlePipelineEvent(dictEvent);
    const elToast = document.querySelector('.toast');
    return elToast ? elToast.textContent : '';
}"""


def _fsToastForFailure(page, sExplanation):
    """Dispatch a commandFailed event and return the toast text."""
    return page.evaluate(_S_DISPATCH_FAILURE, {
        "sType": "commandFailed",
        "sCommand": "python analysis.py",
        "sDirectory": "/repo/StepOne",
        "iExitCode": 127,
        "sExitExplanation": sExplanation,
    })


def testTheToastCarriesTheCauseNotJustTheNumber(
    pageDashboard, serverHub,
):
    """Kills rendering the bare exit code when a cause is available.

    "Command failed (exit 127)" is what the researcher already had and
    could not act on. The toast must carry the sentence that names the
    missing program.
    """
    _fnLoadTheDashboardScripts(pageDashboard, serverHub)
    sExplanation = (
        "Exit 127 means the shell could not find python on this "
        "machine. Check it is installed and on PATH."
    )
    sToast = _fsToastForFailure(pageDashboard, sExplanation)
    assert "could not find python" in sToast, sToast
    assert pageDashboard.listPageErrors == []


def testAnUnexplainedFailureKeepsTheOldMessage(
    pageDashboard, serverHub,
):
    """An ordinary failure must still say something.

    The explanation is empty for exit codes carrying no standard
    meaning. Kills rendering the empty string: the researcher would
    get a blank toast where they used to get the exit code, which is
    strictly worse than the message this change set out to improve.
    """
    _fnLoadTheDashboardScripts(pageDashboard, serverHub)
    sToast = _fsToastForFailure(pageDashboard, "")
    assert "exit 127" in sToast, sToast
    assert pageDashboard.listPageErrors == []
