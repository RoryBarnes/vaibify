"""Both hubs say Quit, and their help says which vaibify is running.

The hubs' buttons and the Quit dialog's confirm button read "Quit"; the
dialog's heading still names what quits. Each hub's "?" window opens
with an About vaibify section whose values are the real hub's answer
from GET /api/system/about.

WHAT THIS DOES NOT COVER: what the route answers (tests/testAboutVaibify.py)
and what a Quit does (tests/browser/testQuitVaibify.py).
"""

import pytest

from tests.browser.conftest import fnLoadThePickerUntilItsButtonsListen
from tests.browser.testLostClaimIsRecoverable import (  # noqa: F401
    _fnReachTheWorkflowPicker,
    fixtureDropClaimsBetweenJourneys,
)


pytestmark = pytest.mark.browser

S_READ_ABOUT = "async () => VaibifyApi.fdictGet('/api/system/about')"


def _fnAssertTheHelpShowsTheServersVersion(pageDashboard, sHelpButton):
    dictAbout = pageDashboard.evaluate(S_READ_ABOUT)
    pageDashboard.click(sHelpButton)
    pageDashboard.wait_for_selector(
        "#modalInfo #hubHelpAbout dl", state="visible", timeout=10000)
    sAbout = pageDashboard.inner_text("#modalInfo #hubHelpAbout")
    assert "About vaibify" in sAbout
    assert dictAbout["sVersion"] and dictAbout["sVersion"] in sAbout
    assert dictAbout["sSource"] in sAbout
    assert dictAbout["sPythonVersion"] in sAbout
    pageDashboard.click("#btnInfoClose")


def _fnAssertTheQuitDialogSaysQuit(pageDashboard, sQuitButton):
    assert pageDashboard.inner_text(sQuitButton) == "Quit"
    pageDashboard.click(sQuitButton)
    pageDashboard.wait_for_selector("#modalQuit", state="visible", timeout=10000)
    assert pageDashboard.inner_text("#modalQuit h2") == "Quit vaibify"
    assert pageDashboard.inner_text("#btnQuitConfirm") == "Quit"
    pageDashboard.click("#btnQuitCancel")


def testTheEnvironmentsHubSaysQuitAndWhichVaibifyIsRunning(
    pageDashboard, serverHub,
):
    fnLoadThePickerUntilItsButtonsListen(pageDashboard, serverHub)
    _fnAssertTheQuitDialogSaysQuit(pageDashboard, "#btnQuitVaibifyEnvironments")
    _fnAssertTheHelpShowsTheServersVersion(pageDashboard, "#btnHubHelp")
    assert pageDashboard.listPageErrors == []


def testTheProjectHubSaysQuitAndWhichVaibifyIsRunning(pageDashboard, serverHub):
    _fnReachTheWorkflowPicker(pageDashboard, serverHub)
    _fnAssertTheQuitDialogSaysQuit(pageDashboard, "#btnQuitVaibifyProjects")
    _fnAssertTheHelpShowsTheServersVersion(pageDashboard, "#btnProjectHubHelp")
    assert pageDashboard.listPageErrors == []
