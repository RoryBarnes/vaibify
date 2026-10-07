"""A file or folder in the Files tab downloads from a visible control.

Right-click used to download silently, with no menu and no icon, and a
symlinked file downloaded as zero bytes. These journeys drive the real
menu and the real keyboard-reachable button in a real browser, in
container mode over the fail-closed fake adapter and in host mode over
real files. The oracle is the browser's own download event and the bytes
it saved.
"""

import io
import os
import tarfile

import pytest

from tests.browser.filesLane import (
    fixtureDropClaimsBetweenJourneys,  # noqa: F401  (fixture)
    fnBrowseTo,
    fnEnterTheContainerFilesPanel,
    fnEnterTheHostFilesPanel,
    fsHostProjectDirectory,
)

pytestmark = pytest.mark.browser

S_MENU_SELECTOR = "#fileRowMenu"


@pytest.fixture
def adapter(serverHub):
    adapterDocker = serverHub.adapterDocker
    adapterDocker.fnResetProjectFiles()
    adapterDocker.fnSeedDirectory("/workspace/data")
    yield adapterDocker
    adapterDocker.fnResetProjectFiles()


def _fsRowSelector(sPath):
    return f'#listFiles .file-item[data-path="{sPath}"]'


def _fnChooseFromRowMenu(page, sPath, sLabel):
    page.click(_fsRowSelector(sPath), button="right")
    page.wait_for_selector(S_MENU_SELECTOR, state="visible", timeout=10000)
    page.click(f'{S_MENU_SELECTOR} button.picklist-item:text-is("{sLabel}")')


def _fbaDownloadViaMenu(page, sPath, sLabel):
    with page.expect_download(timeout=20000) as contextDownload:
        _fnChooseFromRowMenu(page, sPath, sLabel)
    with open(contextDownload.value.path(), "rb") as fileIn:
        return contextDownload.value.suggested_filename, fileIn.read()


# ---------------------------------------------------------------------
# Container mode
# ---------------------------------------------------------------------


@pytest.mark.falsification
def testARowMenuOffersTheActionsByName(
    pageDashboard, serverHub, adapter, fixtureDropClaimsBetweenJourneys,
):
    """The menu names each action, for a file and for a folder.

    Kills: restoring the silent right-click download, which left a
    researcher no way to see that a download was possible.
    """
    adapter.fnSeedFile("/workspace/data/result.csv", b"a,b\n")
    adapter.fnSeedDirectory("/workspace/data/folder")
    fnEnterTheContainerFilesPanel(pageDashboard, serverHub)
    fnBrowseTo(pageDashboard, "/workspace/data")
    pageDashboard.click(
        _fsRowSelector("/workspace/data/result.csv"), button="right")
    pageDashboard.wait_for_selector(S_MENU_SELECTOR, state="visible")
    listFile = pageDashboard.locator(
        f"{S_MENU_SELECTOR} button.picklist-item").all_inner_texts()
    assert listFile == [
        "Download to this computer", "Open in viewer", "Copy path"]
    pageDashboard.keyboard.press("Escape")
    pageDashboard.click(
        _fsRowSelector("/workspace/data/folder"), button="right")
    pageDashboard.wait_for_selector(S_MENU_SELECTOR, state="visible")
    listFolder = pageDashboard.locator(
        f"{S_MENU_SELECTOR} button.picklist-item").all_inner_texts()
    assert listFolder == ["Download as .tar", "Copy path"]


@pytest.mark.falsification
def testAFileDownloadsAsItselfFromTheMenu(
    pageDashboard, serverHub, adapter, fixtureDropClaimsBetweenJourneys,
):
    """Kills: a menu entry that does not reach the download."""
    adapter.fnSeedFile("/workspace/data/result.csv", b"col,val\n1,2\n")
    fnEnterTheContainerFilesPanel(pageDashboard, serverHub)
    fnBrowseTo(pageDashboard, "/workspace/data")
    sName, baSaved = _fbaDownloadViaMenu(
        pageDashboard, "/workspace/data/result.csv",
        "Download to this computer")
    assert sName == "result.csv"
    assert baSaved == b"col,val\n1,2\n"


def testASymlinkedFileDownloadsItsTargetsBytesNotAnEmptyFile(
    pageDashboard, serverHub, adapter, fixtureDropClaimsBetweenJourneys,
):
    """The daemon's archive yielded zero bytes for a link.

    A regression test of the page and route plumbing over the fake
    adapter; the link-following itself is the product's, and is killed in
    host mode by ``testAHostProjectsSymlinkedFileAndFolderDownloadRealBytes``.
    """
    adapter.fnSeedFile("/workspace/data/real.dat", b"the real bytes")
    adapter.fnSeedSymlink("/workspace/data/latest.dat", "real.dat")
    fnEnterTheContainerFilesPanel(pageDashboard, serverHub)
    fnBrowseTo(pageDashboard, "/workspace/data")
    sName, baSaved = _fbaDownloadViaMenu(
        pageDashboard, "/workspace/data/latest.dat",
        "Download to this computer")
    assert sName == "latest.dat"
    assert baSaved == b"the real bytes"


@pytest.mark.falsification
def testAFolderDownloadsAsATarKeepingItsLinks(
    pageDashboard, serverHub, adapter, fixtureDropClaimsBetweenJourneys,
):
    """Kills: downloading a folder as a file (or not at all)."""
    adapter.fnSeedFile("/workspace/data/tree/one.txt", b"one")
    adapter.fnSeedFile("/workspace/data/tree/sub/two.txt", b"two")
    adapter.fnSeedSymlink("/workspace/data/tree/link", "one.txt")
    fnEnterTheContainerFilesPanel(pageDashboard, serverHub)
    fnBrowseTo(pageDashboard, "/workspace/data")
    sName, baSaved = _fbaDownloadViaMenu(
        pageDashboard, "/workspace/data/tree", "Download as .tar")
    assert sName == "tree.tar"
    with tarfile.open(fileobj=io.BytesIO(baSaved)) as tarIn:
        listNames = tarIn.getnames()
        assert tarIn.extractfile("tree/sub/two.txt").read() == b"two"
        assert tarIn.getmember("tree/link").issym()
    assert "tree/one.txt" in listNames


@pytest.mark.falsification
def testALinkLeadingOutOfTheProjectToastsWhyAndDownloadsNothing(
    pageDashboard, serverHub, adapter, fixtureDropClaimsBetweenJourneys,
):
    """An anchor download cannot show an HTTP error, so the page asks first.

    Kills: skipping the HEAD probe, after which the browser saves the
    error page as the file.
    """
    adapter.fnSeedSymlink("/workspace/data/leak", "/etc/hosts")
    fnEnterTheContainerFilesPanel(pageDashboard, serverHub)
    fnBrowseTo(pageDashboard, "/workspace/data")
    listDownloads = []
    pageDashboard.on("download", lambda download: listDownloads.append(1))
    _fnChooseFromRowMenu(
        pageDashboard, "/workspace/data/leak", "Download to this computer")
    pageDashboard.wait_for_selector(".toast.error", timeout=15000)
    sToast = pageDashboard.inner_text(".toast.error")
    assert "leak" in sToast
    pageDashboard.wait_for_timeout(1000)
    assert listDownloads == []


@pytest.mark.falsification
def testTheDownloadButtonIsReachableAndWorksFromTheKeyboard(
    pageDashboard, serverHub, adapter, fixtureDropClaimsBetweenJourneys,
):
    """The visible control is a real button, so Tab reaches it.

    Kills: making the icon a mouse-only affordance.
    """
    adapter.fnSeedFile("/workspace/data/key.txt", b"by keyboard")
    fnEnterTheContainerFilesPanel(pageDashboard, serverHub)
    fnBrowseTo(pageDashboard, "/workspace/data")
    sButton = (_fsRowSelector("/workspace/data/key.txt")
               + " button.file-download-button")
    pageDashboard.focus(sButton)
    assert pageDashboard.evaluate(
        "(s) => document.activeElement === document.querySelector(s)",
        sButton)
    with pageDashboard.expect_download(timeout=20000) as contextDownload:
        pageDashboard.keyboard.press("Enter")
    with open(contextDownload.value.path(), "rb") as fileIn:
        assert fileIn.read() == b"by keyboard"


# ---------------------------------------------------------------------
# Host mode, over real files and real links
# ---------------------------------------------------------------------


@pytest.mark.falsification
def testAHostProjectsSymlinkedFileAndFolderDownloadRealBytes(
    pageDashboard, serverHub, fixtureDropClaimsBetweenJourneys,
):
    """Real files, a real link, real archive bytes, the same menu.

    Kills: a host download that yields an empty file for a link or drops
    the folder's contents.
    """
    sProject = fsHostProjectDirectory(serverHub)
    sScratch = os.path.join(sProject, "downloadJourney")
    os.makedirs(os.path.join(sScratch, "tree", "sub"), exist_ok=True)
    with open(os.path.join(sScratch, "real.dat"), "wb") as fileOut:
        fileOut.write(b"host real bytes")
    with open(os.path.join(sScratch, "tree", "sub", "x.txt"), "wb") as fileOut:
        fileOut.write(b"x")
    for sLink, sTarget in (("latest.dat", "real.dat"),
                           ("tree/pointer", "sub/x.txt")):
        sLinkPath = os.path.join(sScratch, sLink)
        if os.path.lexists(sLinkPath):
            os.unlink(sLinkPath)
        os.symlink(sTarget, sLinkPath)
    fnEnterTheHostFilesPanel(pageDashboard, serverHub)
    fnBrowseTo(pageDashboard, sScratch)
    sName, baSaved = _fbaDownloadViaMenu(
        pageDashboard, os.path.join(sScratch, "latest.dat"),
        "Download to this computer")
    assert baSaved == b"host real bytes"
    sName, baTar = _fbaDownloadViaMenu(
        pageDashboard, os.path.join(sScratch, "tree"), "Download as .tar")
    assert sName == "tree.tar"
    with tarfile.open(fileobj=io.BytesIO(baTar)) as tarIn:
        assert tarIn.extractfile("tree/sub/x.txt").read() == b"x"
        assert tarIn.getmember("tree/pointer").issym()
