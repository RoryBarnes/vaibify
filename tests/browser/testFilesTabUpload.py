"""The Files tab uploads what is dropped on it, whatever its size or shape.

Journeys through the real page, a real uvicorn hub and the real routes.
Container mode runs over the fail-closed fake adapter (which models a
workspace and refuses the way the real writer does); host mode runs over
real files in a real directory.

What a synthetic drop proves, and what it cannot, is stated in
``tests/browser/filesLane.py``: it runs the page's real drop handler and
everything after it, but no lane performs a true Finder drop and none
drives a real container through the browser.
"""

import hashlib
import os

import pytest

from tests.browser.filesLane import (
    S_SUMMARY_SELECTOR,
    S_UPLOAD_STREAM_GLOB,
    S_ZONE_SELECTOR,
    fixtureDropClaimsBetweenJourneys,  # noqa: F401  (fixture)
    fnAwaitTheSummary,
    fnBrowseTo,
    fnDropEntriesOn,
    fnDropFilesOn,
    fnEnterTheContainerFilesPanel,
    fnEnterTheHostFilesPanel,
    fsHostProjectDirectory,
)

pytestmark = pytest.mark.browser

S_CONFIRM_KEEP_SELECTOR = "#btnConfirmCancel"
S_CONFIRM_REPLACE_SELECTOR = "#btnConfirmOk"


@pytest.fixture
def adapter(serverHub):
    adapterDocker = serverHub.adapterDocker
    adapterDocker.fnResetProjectFiles()
    adapterDocker.fnSeedDirectory("/workspace/data")
    adapterDocker.fnSeedDirectory("/workspace/data/sub")
    yield adapterDocker
    adapterDocker.fnResetProjectFiles()


def _fnWaitForRow(page, sName):
    page.wait_for_selector(
        f'#listFiles .file-item .file-name:text-is("{sName}")', timeout=15000)


# ---------------------------------------------------------------------
# Where a drop lands
# ---------------------------------------------------------------------


@pytest.mark.falsification
def testAFileDroppedOnTheZoneLandsInTheFolderThatIsOpen(
    pageDashboard, serverHub, adapter, fixtureDropClaimsBetweenJourneys,
):
    """The drop lands where the panel is looking, not in some repository.

    Kills: sending the file to the repository path instead of the
    listed directory, which refuses every drop at the workspace.
    """
    fnEnterTheContainerFilesPanel(pageDashboard, serverHub)
    fnBrowseTo(pageDashboard, "/workspace/data")
    fnDropFilesOn(pageDashboard, S_ZONE_SELECTOR,
                  [("obs.csv", "a,b\n1,2\n")])
    sSummary = fnAwaitTheSummary(pageDashboard)
    assert "Uploaded 1 file" in sSummary and "/workspace/data" in sSummary
    assert adapter.dictProjectFiles["/workspace/data/obs.csv"] == (
        b"a,b\n1,2\n")
    _fnWaitForRow(pageDashboard, "obs.csv")


@pytest.mark.falsification
def testAFileDroppedOnAFolderRowLandsInThatFolder(
    pageDashboard, serverHub, adapter, fixtureDropClaimsBetweenJourneys,
):
    """Kills: ignoring the row under the pointer (always using the view)."""
    fnEnterTheContainerFilesPanel(pageDashboard, serverHub)
    fnBrowseTo(pageDashboard, "/workspace/data")
    fnDropFilesOn(
        pageDashboard,
        '#listFiles .file-item[data-path="/workspace/data/sub"]',
        [("inner.txt", "inner")])
    fnAwaitTheSummary(pageDashboard)
    assert adapter.dictProjectFiles["/workspace/data/sub/inner.txt"] == (
        b"inner")
    assert "/workspace/data/inner.txt" not in adapter.dictProjectFiles


@pytest.mark.falsification
def testAFolderThatTakesNoUploadsSaysWhyAndWritesNothing(
    pageDashboard, serverHub, adapter, fixtureDropClaimsBetweenJourneys,
):
    """The panel renders the server's verdict, and the drop honours it.

    Kills: rendering a verdict of the page's own (a refused folder left
    looking like a target) or sending the bytes anyway.
    """
    adapter.fnSeedDirectory("/workspace/repo/.git")
    fnEnterTheContainerFilesPanel(pageDashboard, serverHub)
    fnBrowseTo(pageDashboard, "/workspace/repo/.git")
    sVerdict = pageDashboard.inner_text("#fileUploadVerdict")
    assert ".git" in sVerdict
    assert pageDashboard.get_attribute(
        S_ZONE_SELECTOR, "aria-disabled") == "true"
    fnDropFilesOn(pageDashboard, S_ZONE_SELECTOR, [("hook", "x")])
    sSummary = fnAwaitTheSummary(pageDashboard)
    assert "Nothing was uploaded" in sSummary and ".git" in sSummary
    assert not [sPath for sPath in adapter.dictProjectFiles
                if sPath.startswith("/workspace/repo/.git/")]


# ---------------------------------------------------------------------
# Folders
# ---------------------------------------------------------------------


@pytest.mark.falsification
def testADroppedFolderIsRecreatedWithItsEmptyFoldersAndWithoutItsGit(
    pageDashboard, serverHub, adapter, fixtureDropClaimsBetweenJourneys,
):
    """Nested files, an empty folder, and repository internals counted.

    Kills: flattening the folder, dropping the empty folder, or sending
    the ``.git`` subtree.
    """
    fnEnterTheContainerFilesPanel(pageDashboard, serverHub)
    fnBrowseTo(pageDashboard, "/workspace/data")
    fnDropEntriesOn(pageDashboard, S_ZONE_SELECTOR, """
        return [fentryDirectory('dropped', [
            fentryFile('top.txt', 't'),
            fentryDirectory('nested', [fentryFile('deep.txt', 'd')]),
            fentryDirectory('empty', []),
            fentryDirectory('.git', [fentryFile('config', 'secret')]),
        ])];
    """)
    sSummary = fnAwaitTheSummary(pageDashboard)
    assert adapter.dictProjectFiles["/workspace/data/dropped/top.txt"] == b"t"
    assert adapter.dictProjectFiles[
        "/workspace/data/dropped/nested/deep.txt"] == b"d"
    assert adapter._fbIsModelledDirectory("/workspace/data/dropped/empty")
    assert not [sPath for sPath in adapter.dictProjectFiles if "/.git/" in sPath]
    assert ".git" in sSummary and "repository internals" in sSummary


def testAFolderOfTwoHundredFiftyFilesArrivesWhole(
    pageDashboard, serverHub, adapter, fixtureDropClaimsBetweenJourneys,
):
    """The reader hands entries back in batches of a hundred, then none."""
    fnEnterTheContainerFilesPanel(pageDashboard, serverHub)
    fnBrowseTo(pageDashboard, "/workspace/data")
    fnDropEntriesOn(pageDashboard, S_ZONE_SELECTOR, """
        const listChildren = Array.from({length: 250},
            (_, iIndex) => fentryFile('f' + iIndex + '.txt', 'v' + iIndex));
        return [fentryDirectory('bulk', listChildren, 100)];
    """)
    fnAwaitTheSummary(pageDashboard, fTimeoutSeconds=180)
    listLanded = [sPath for sPath in adapter.dictProjectFiles
                  if sPath.startswith("/workspace/data/bulk/")]
    assert len(listLanded) == 250
    assert adapter.dictProjectFiles["/workspace/data/bulk/f249.txt"] == b"v249"


# ---------------------------------------------------------------------
# A batch keeps the destination it was dropped on
# ---------------------------------------------------------------------


@pytest.mark.falsification
def testOpeningAnotherFolderMidBatchDoesNotRedirectTheFilesStillWaiting(
    pageDashboard, serverHub, adapter, fixtureDropClaimsBetweenJourneys,
):
    """The researcher keeps working while an upload runs.

    The first request is held, the panel is moved to another folder, and
    the request is released: every file must still land where it was
    dropped.

    Kills: reading the destination from the live view at send time
    instead of from the frozen batch.
    """
    adapter.fnSeedFile("/workspace/elsewhere/kept.txt", b"kept")
    fnEnterTheContainerFilesPanel(pageDashboard, serverHub)
    fnBrowseTo(pageDashboard, "/workspace/data")
    listHeld = []

    def fnHoldTheFirst(route):
        if not listHeld:
            listHeld.append(route)
        else:
            route.continue_()

    pageDashboard.route(S_UPLOAD_STREAM_GLOB, fnHoldTheFirst)
    fnDropFilesOn(pageDashboard, S_ZONE_SELECTOR,
                  [("one.txt", "1"), ("two.txt", "2"), ("three.txt", "3")])
    for _ in range(100):
        if listHeld:
            break
        pageDashboard.wait_for_timeout(100)
    assert listHeld, "the first upload request never left the page"
    fnBrowseTo(pageDashboard, "/workspace/elsewhere")
    listHeld[0].continue_()
    fnAwaitTheSummary(pageDashboard)
    for sName, baContent in (("one.txt", b"1"), ("two.txt", b"2"),
                             ("three.txt", b"3")):
        assert adapter.dictProjectFiles[f"/workspace/data/{sName}"] == baContent
        assert f"/workspace/elsewhere/{sName}" not in adapter.dictProjectFiles


# ---------------------------------------------------------------------
# Replacing, and what the researcher is told
# ---------------------------------------------------------------------


@pytest.mark.falsification
def testAFileThatAppearedAfterTheListingIsNotReplacedAndSaysSo(
    pageDashboard, serverHub, adapter, fixtureDropClaimsBetweenJourneys,
):
    """The listing is a hint; the server's 409 is the authority.

    Kills: sending ``bReplaceAllowed`` true for a name nobody confirmed.
    """
    fnEnterTheContainerFilesPanel(pageDashboard, serverHub)
    fnBrowseTo(pageDashboard, "/workspace/data")
    adapter.fnSeedFile("/workspace/data/notes.txt", b"old")
    fnDropFilesOn(pageDashboard, S_ZONE_SELECTOR, [("notes.txt", "new")])
    sSummary = fnAwaitTheSummary(pageDashboard)
    assert "already exists" in sSummary and "Not uploaded" in sSummary
    assert adapter.dictProjectFiles["/workspace/data/notes.txt"] == b"old"


def testAListedFileIsReplacedOnlyWhenTheResearcherConfirms(
    pageDashboard, serverHub, adapter, fixtureDropClaimsBetweenJourneys,
):
    adapter.fnSeedFile("/workspace/data/notes.txt", b"old")
    fnEnterTheContainerFilesPanel(pageDashboard, serverHub)
    fnBrowseTo(pageDashboard, "/workspace/data")
    _fnWaitForRow(pageDashboard, "notes.txt")
    fnDropFilesOn(pageDashboard, S_ZONE_SELECTOR, [("notes.txt", "new")])
    pageDashboard.click(S_CONFIRM_KEEP_SELECTOR)
    sSummary = fnAwaitTheSummary(pageDashboard)
    assert "Kept as they were" in sSummary
    assert adapter.dictProjectFiles["/workspace/data/notes.txt"] == b"old"
    fnDropFilesOn(pageDashboard, S_ZONE_SELECTOR, [("notes.txt", "new")])
    pageDashboard.click(S_CONFIRM_REPLACE_SELECTOR)
    fnAwaitTheSummary(pageDashboard)
    assert adapter.dictProjectFiles["/workspace/data/notes.txt"] == b"new"


@pytest.mark.falsification
def testABatchThatCannotFitIsRefusedUpfrontNamingTheDisk(
    pageDashboard, serverHub, adapter, fixtureDropClaimsBetweenJourneys,
):
    """The server's sentence reaches the researcher, and nothing is sent.

    Kills: swallowing the refusal behind a generic failure message.
    """
    adapter.iFreeBytes = 1 << 20
    fnEnterTheContainerFilesPanel(pageDashboard, serverHub)
    fnBrowseTo(pageDashboard, "/workspace/data")
    pageDashboard.evaluate(
        """() => {
            const dt = new DataTransfer();
            dt.items.add(new File(['x'.repeat(5000000)], 'huge.bin'));
            document.querySelector('#fileUploadDropZone').dispatchEvent(
                new DragEvent('drop', {bubbles: true, cancelable: true,
                                       dataTransfer: dt}));
        }""")
    sSummary = fnAwaitTheSummary(pageDashboard)
    assert "container's disk" in sSummary
    assert "/workspace/data/huge.bin" not in adapter.dictProjectFiles


# ---------------------------------------------------------------------
# Host mode, over real files
# ---------------------------------------------------------------------


@pytest.mark.falsification
def testAHostProjectReceivesAFileAndAFolderOnDisk(
    pageDashboard, serverHub, fixtureDropClaimsBetweenJourneys,
):
    """Real bytes, real directories, the same page.

    Kills: a host-mode upload that lands nowhere or loses the folder.
    """
    sProject = fsHostProjectDirectory(serverHub)
    fnEnterTheHostFilesPanel(pageDashboard, serverHub)
    fnBrowseTo(pageDashboard, sProject)
    fnDropFilesOn(pageDashboard, S_ZONE_SELECTOR,
                  [("hostFile.csv", "h,i\n1,2\n")])
    fnAwaitTheSummary(pageDashboard)
    with open(os.path.join(sProject, "hostFile.csv"), "rb") as fileIn:
        assert fileIn.read() == b"h,i\n1,2\n"
    fnDropEntriesOn(pageDashboard, S_ZONE_SELECTOR, """
        return [fentryDirectory('hostDropped', [
            fentryFile('a.txt', 'A'),
            fentryDirectory('inner', [fentryFile('b.txt', 'B')]),
            fentryDirectory('hollow', []),
        ])];
    """)
    fnAwaitTheSummary(pageDashboard)
    sDropped = os.path.join(sProject, "hostDropped")
    assert hashlib.sha256(open(
        os.path.join(sDropped, "inner", "b.txt"), "rb").read()).hexdigest() == (
        hashlib.sha256(b"B").hexdigest())
    assert os.path.isdir(os.path.join(sDropped, "hollow"))
