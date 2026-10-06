"""Shared steps for the browser journeys of the Files tab.

The journeys that drop files onto the panel, upload them and download
them again all begin the same way -- claim a project, enter it, open the
Files tab -- and all need to put a file in front of the page's drop
handler, which Playwright cannot do with an operating-system drag. They
share those steps here rather than carrying five private copies that
drift apart (the lane's own history, see ``fnOpenTheSeededHostWorkflow``).

What a synthetic drop proves, and what it cannot: a ``DragEvent`` carrying
a ``DataTransfer`` built in the page runs the page's real drop handler,
so everything from the handler onward is exercised -- the entry capture,
the walk, the verdict request, the streamed PUT, the summary. It does not
exercise the browser's own drag-and-drop machinery, and a synthetic
``DataTransfer`` cannot carry a directory entry, so a dropped FOLDER is
driven with entry-like objects in the same shape the browser produces.
No lane performs a true Finder drop.
"""

import os

import pytest

from tests.browser.conftest import (
    S_HOST_PROJECT_READY,
)
from tests.browser.fakeDockerAdapter import (
    S_CONTAINER_ID,
    S_CONTAINER_NAME,
    S_WORKSPACE_ROOT,
)

S_FILES_LIST_SELECTOR = "#listFiles"
S_ZONE_SELECTOR = "#fileUploadDropZone"
S_SUMMARY_SELECTOR = "#fileUploadSummary"
S_UPLOAD_STREAM_GLOB = "**/api/upload/*/stream*"

# JavaScript helpers handed to ``page.evaluate``. The entry factories
# return objects shaped like FileSystemEntry (isFile, isDirectory, name,
# file(), createReader().readEntries()), and a directory hands its
# children back in fragments of ``iBatchSize`` and then an empty batch,
# which is what Chromium does at a hundred.
S_FAKE_ENTRY_FACTORIES = """
const fentryFile = (sName, sContent) => ({
    isFile: true, isDirectory: false, name: sName,
    file: (fnOk) => fnOk(new File([sContent], sName)),
});
const fentryDirectory = (sName, listChildren, iBatchSize = 100) => ({
    isFile: false, isDirectory: true, name: sName,
    createReader: () => {
        let iNext = 0;
        return {
            readEntries: (fnOk) => {
                const listBatch = listChildren.slice(iNext, iNext + iBatchSize);
                iNext += iBatchSize;
                fnOk(listBatch);
            },
        };
    },
});
const fdtFromEntries = (listEntries) => ({
    types: ['Files'],
    files: [],
    items: listEntries.map((entry) => ({
        kind: 'file',
        webkitGetAsEntry: () => entry,
        getAsFile: () => null,
    })),
});
const fnDropOn = (sSelector, dt) => {
    const el = document.querySelector(sSelector);
    const event = new Event('drop', {bubbles: true, cancelable: true});
    Object.defineProperty(event, 'dataTransfer', {value: dt});
    el.dispatchEvent(event);
};
"""


@pytest.fixture
def fixtureDropClaimsBetweenJourneys(serverHub):
    """Give every claim back after each journey.

    The hub is module-scoped and the page is not, so a journey that
    claims a project would leave it owned by a lease nobody holds and the
    next journey's claim would be refused by a session that no longer
    exists.
    """
    yield
    from vaibify.config.containerLock import fnReleaseContainerLock
    dictContainerOwners = serverHub.app.state.dictContainerOwners
    for _sName, recordOwner in list(dictContainerOwners.items()):
        fileHandle = getattr(recordOwner, "fileHandleLock", None)
        if fileHandle is not None:
            try:
                fnReleaseContainerLock(fileHandle)
            except OSError:
                pass
    dictContainerOwners.clear()
    serverHub.app.state.dictSessionOwner.clear()


def fnOpenTheFilesTab(page):
    """Click the Files tab and wait for the panel to be the visible one."""
    page.click('.left-tab[data-panel="files"]')
    page.wait_for_selector(
        "#panelFiles.active", state="visible", timeout=20000)
    page.wait_for_function(
        """() => {
            const elVerdict = document.getElementById('fileUploadVerdict');
            return elVerdict && elVerdict.textContent.trim().length > 0
                && !elVerdict.textContent.startsWith('Checking');
        }""",
        timeout=20000,
    )


def fnEnterTheContainerFilesPanel(page, serverHub):
    """Claim the lane's container project and show its Files tab."""
    page.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    page.wait_for_selector(
        f'.container-tile[data-name="{S_CONTAINER_NAME}"]', timeout=15000)
    page.evaluate(
        "(sId) => VaibifyContainerManager.fnConnectToContainer(sId)",
        S_CONTAINER_ID)
    page.wait_for_selector("#workflowPicker", state="visible")
    page.click("#btnNoWorkflow")
    page.wait_for_selector("#mainLayout.active", timeout=20000)
    fnOpenTheFilesTab(page)


def fnEnterTheHostFilesPanel(page, serverHub):
    """Claim the lane's host project and show its Files tab."""
    page.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    page.wait_for_selector(
        f'.container-tile[data-name="{S_HOST_PROJECT_READY}"]'
        ":not(.container-tile--locked)",
        timeout=90000)
    page.click(
        f'.container-tile[data-name="{S_HOST_PROJECT_READY}"] '
        ".container-tile-main")
    page.wait_for_selector("#modalConfirm", timeout=10000)
    page.click("#btnConfirmOk")
    page.wait_for_selector("#btnNoWorkflow", timeout=20000)
    page.click("#btnNoWorkflow")
    page.wait_for_selector("#mainLayout.active", timeout=20000)
    fnOpenTheFilesTab(page)


def fsHostProjectDirectory(serverHub):
    """Return the on-disk directory of the lane's ready host project."""
    return os.path.join(serverHub.sHome, S_HOST_PROJECT_READY)


def fnBrowseTo(page, sPath):
    """Open a directory in the panel through the product's own loader."""
    page.evaluate(
        "async (sPath) => { await VaibifyFiles.fnLoadDirectory(sPath); }",
        sPath)
    page.wait_for_function(
        """() => {
            const elVerdict = document.getElementById('fileUploadVerdict');
            return elVerdict && elVerdict.textContent.trim().length > 0
                && !elVerdict.textContent.startsWith('Checking');
        }""",
        timeout=20000,
    )


def fnDropFilesOn(page, sSelector, listFiles):
    """Drop synthetic files (``[(name, text)]``) on an element."""
    page.evaluate(
        """({sSelector, listFiles}) => {
            const dt = new DataTransfer();
            listFiles.forEach(([sName, sContent]) =>
                dt.items.add(new File([sContent], sName)));
            const el = document.querySelector(sSelector);
            el.dispatchEvent(new DragEvent('drop', {
                bubbles: true, cancelable: true, dataTransfer: dt}));
        }""",
        {"sSelector": sSelector, "listFiles": listFiles})


def fnDropEntriesOn(page, sSelector, sEntryBuilder):
    """Drop entry-like objects built by ``sEntryBuilder`` on an element.

    ``sEntryBuilder`` is the body of a JavaScript function that may use
    ``fentryFile`` and ``fentryDirectory`` and returns the list of
    top-level entries.
    """
    page.evaluate(
        "({sSelector}) => {" + S_FAKE_ENTRY_FACTORIES
        + "const listEntries = (() => {" + sEntryBuilder + "})();"
        "fnDropOn(sSelector, fdtFromEntries(listEntries)); }",
        {"sSelector": sSelector})


def fnAwaitTheSummary(page, fTimeoutSeconds=30.0):
    """Wait for the batch's one summary and return its text."""
    page.wait_for_selector(
        S_SUMMARY_SELECTOR, state="visible",
        timeout=int(fTimeoutSeconds * 1000))
    return page.inner_text(S_SUMMARY_SELECTOR)


def flistRecordUploadRequests(page):
    """Record every upload PUT the page issues, as (path, query) pairs."""
    listRequests = []
    page.on("request", lambda request: (
        listRequests.append(request.url)
        if "/api/upload/" in request.url and "/stream" in request.url
        else None
    ))
    return listRequests


def fsContainerPath(*listParts):
    """Join parts onto the lane's workspace root."""
    return "/".join([S_WORKSPACE_ROOT.rstrip("/")] + list(listParts))
