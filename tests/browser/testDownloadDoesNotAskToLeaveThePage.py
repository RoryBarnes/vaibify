"""Downloading a file must not look like leaving the dashboard.

"Download to this computer" navigated the page at the file
(``window.location.assign``), and the dashboard blocks unload. Firefox
fires ``beforeunload`` when a navigation STARTS -- before the response
can say ``Content-Disposition: attachment`` -- so the researcher who
asked for their file was asked whether they wanted to leave the page,
and got no file (reported on Firefox, 2026-09-22).

No server header can fix that: the prompt precedes the header. A
browser that cancels the navigation once it sees the attachment simply
hides the defect, which is why this was invisible in Chromium and why
the test below asserts the DOWNLOAD, not the absence of a dialog --
an assertion about a dialog would pass in the browser that never shows
one.

The fix is to stop navigating: an anchor click downloads same-origin
without unloading anything, so the unload guard stays whole.
"""

import pathlib

import pytest

from tests.browser.conftest import (
    S_HOST_STEP_NAME,
    fnOpenTheSeededHostWorkflow,
)

# A file the seed actually WRITES. The step's declared output is
# produced by running it, so downloading that would 404 and the test
# would fail for a reason that has nothing to do with navigation.
S_SEEDED_FILE_NAME = "makeNumbers.py"


pytestmark = pytest.mark.browser


def _fnOpenFilesPanelOnTheSeededProject(pageDashboard, serverHub):
    """Open the seeded host workflow and show its Files sub-panel."""
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    pageDashboard.click('.left-tab[data-panel="files"]')
    pageDashboard.wait_for_selector(
        "#panelFiles.active", state="visible", timeout=20000,
    )


@pytest.mark.falsification
def testDownloadingAFileStartsADownloadRatherThanANavigation(
    pageDashboard, serverHub,
):
    """The researcher gets the file, not a question about leaving.

    The oracle is a real download event from the browser. Navigating
    instead produces no download at all -- in Firefox because the
    unload prompt intercepts it, and in every browser because a
    navigation is not a download event -- so this fails on the old
    code wherever it runs.

    Kills: restoring ``window.location.assign`` for the download,
    which re-enters the dashboard's unload guard and asks a
    researcher who wanted a file whether they meant to leave.
    """
    _fnOpenFilesPanelOnTheSeededProject(pageDashboard, serverHub)
    sUrlBefore = pageDashboard.url
    # The oracle is the unload EVENT, not the dialog. A navigation
    # fires beforeunload in every engine; only whether a PROMPT is
    # then shown differs between them, which is why this defect was
    # visible in Firefox and invisible in Chromium. Asserting on the
    # event holds wherever the lane runs.
    pageDashboard.evaluate(
        """() => {
            window.__bUnloadFired = false;
            window.addEventListener('beforeunload', () => {
                window.__bUnloadFired = true;
            });
        }"""
    )
    with pageDashboard.expect_download(timeout=20000) as contextDownload:
        pageDashboard.evaluate(
            """(sPath) => VaibifyFilePull.fnDownloadToThisComputer(sPath)""",
            f"{S_HOST_STEP_NAME}/{S_SEEDED_FILE_NAME}",
        )
    assert contextDownload.value.suggested_filename == S_SEEDED_FILE_NAME, (
        contextDownload.value.suggested_filename
    )
    sDownloaded = pathlib.Path(
        contextDownload.value.path()).read_text(encoding="utf-8")
    assert sDownloaded.strip(), (
        "a download event arrived but no bytes landed"
    )
    assert pageDashboard.url == sUrlBefore, (
        "the dashboard navigated to serve a download; that is what "
        "raises the unload prompt in the first place"
    )
    assert pageDashboard.evaluate("() => window.__bUnloadFired") is False, (
        "serving the download fired beforeunload, so the dashboard's "
        "unload guard runs and a browser that honours it asks the "
        "researcher whether they meant to leave"
    )
    assert pageDashboard.listPageErrors == [], pageDashboard.listPageErrors


@pytest.mark.falsification
def testTheUnloadGuardIsStillArmedAfterADownload(
    pageDashboard, serverHub,
):
    """The fix must not be "remove the unload guard".

    Dropping the ``beforeunload`` listener would also stop the prompt,
    and would silently give up the protection that exists so a
    researcher does not close a dashboard mid-run. The download route
    goes around the guard; it does not disarm it.

    Kills: removing or neutering the beforeunload registration to
    silence the prompt.
    """
    _fnOpenFilesPanelOnTheSeededProject(pageDashboard, serverHub)
    with pageDashboard.expect_download(timeout=20000):
        pageDashboard.evaluate(
            """(sPath) => VaibifyFilePull.fnDownloadToThisComputer(sPath)""",
            f"{S_HOST_STEP_NAME}/{S_SEEDED_FILE_NAME}",
        )
    bGuardArmed = pageDashboard.evaluate(
        """() => {
            const event = new Event('beforeunload', {cancelable: true});
            const bPrevented = !window.dispatchEvent(event);
            return bPrevented;
        }"""
    )
    assert bGuardArmed, (
        "the beforeunload guard is no longer armed; the download was "
        "made quiet by giving up the protection, not by going around it"
    )
