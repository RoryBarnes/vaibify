"""The setup wizard page tells the truth about the file it is editing.

Three things it got wrong, each invisible to a server-only test:

* a ``vaibify.yml`` the server could not load rendered as an empty form,
  and the next Save replaced it;
* a refused Save toasted "Save failed: [object Object]" because the
  server's validation errors arrive as an object;
* a Save over an unreadable file silently discarded it.

Kills (the mutation applied, the test run, the named assertion observed
to fail):
  - the page ignoring a non-OK answer to the load request -> no notice
    appears and the form reads as a fresh project;
  - the page printing the error object itself -> "[object Object]";
  - the page never asking before replacing an unreadable file.
"""

import os

import pytest

pytestmark = pytest.mark.browser


def _fsConfigPath(serverSetupWizard):
    return os.path.join(serverSetupWizard.sOutputDirectory, "vaibify.yml")


@pytest.fixture
def pageWizard(browserEngine, serverSetupWizard):
    sPath = _fsConfigPath(serverSetupWizard)
    if os.path.exists(sPath):
        os.remove(sPath)
    contextBrowser = browserEngine.new_context()
    page = contextBrowser.new_page()
    page.listPageErrors = []
    page.on("pageerror", lambda error: page.listPageErrors.append(str(error)))
    yield page
    contextBrowser.close()
    if os.path.exists(sPath):
        os.remove(sPath)


def _fnOpen(page, serverSetupWizard):
    page.goto(serverSetupWizard.fsBootstrapUrl(), wait_until="load")
    page.wait_for_selector(".template-card", timeout=15000)


@pytest.mark.falsification
def testAConfigThatCannotBeLoadedIsReportedNotShownAsAnEmptyForm(
    pageWizard, serverSetupWizard,
):
    """Kills: ignoring a refused load, so a broken file reads as no file."""
    with open(_fsConfigPath(serverSetupWizard), "w") as fileConfig:
        fileConfig.write("projectName: broken\npackageManager: nonsense\n")
    _fnOpen(pageWizard, serverSetupWizard)
    pageWizard.wait_for_selector(".toast.error", timeout=15000)
    sNotice = pageWizard.locator(".toast.error").first.inner_text()
    assert "could not be loaded" in sNotice
    assert "NOT your configuration" in sNotice
    assert pageWizard.input_value("#projectName") == ""
    assert pageWizard.listPageErrors == []


@pytest.mark.falsification
def testARefusedSaveShowsTheServersWordsNotAnObject(
    pageWizard, serverSetupWizard,
):
    """Kills: toasting the error object, which read "[object Object]"."""
    _fnOpen(pageWizard, serverSetupWizard)
    pageWizard.fill("#projectName", "refusedSave")
    pageWizard.fill("#containerUser", "Bad User!")
    pageWizard.click("#btnSaveConfig")
    pageWizard.wait_for_selector(".toast.error", timeout=15000)
    sToast = pageWizard.locator(".toast.error").first.inner_text()
    assert "[object Object]" not in sToast
    assert "not a usable Unix user name" in sToast
    assert not os.path.exists(_fsConfigPath(serverSetupWizard))


@pytest.mark.falsification
def testSavingOverAnUnreadableFileAsksFirstAndHonorsNo(
    pageWizard, serverSetupWizard,
):
    """Kills: replacing a file the server could not read without asking."""
    sUnreadable = "projectName: [unclosed\n"
    with open(_fsConfigPath(serverSetupWizard), "w") as fileConfig:
        fileConfig.write(sUnreadable)
    _fnOpen(pageWizard, serverSetupWizard)
    listDialogs = []

    def fnDecline(dialog):
        listDialogs.append(dialog.message)
        dialog.dismiss()

    pageWizard.on("dialog", fnDecline)
    pageWizard.fill("#projectName", "replacement")
    pageWizard.fill("#containerUser", "researcher")
    pageWizard.click("#btnSaveConfig")
    pageWizard.wait_for_timeout(1500)
    assert len(listDialogs) == 1 and "Replace it?" in listDialogs[0]
    with open(_fsConfigPath(serverSetupWizard)) as fileConfig:
        assert fileConfig.read() == sUnreadable


def testSavingOverAnUnreadableFileReplacesItOnceConfirmed(
    pageWizard, serverSetupWizard,
):
    with open(_fsConfigPath(serverSetupWizard), "w") as fileConfig:
        fileConfig.write("projectName: [unclosed\n")
    _fnOpen(pageWizard, serverSetupWizard)
    pageWizard.on("dialog", lambda dialog: dialog.accept())
    pageWizard.fill("#projectName", "replacement")
    pageWizard.fill("#containerUser", "researcher")
    pageWizard.click("#btnSaveConfig")
    pageWizard.wait_for_selector(".toast.success", timeout=15000)
    with open(_fsConfigPath(serverSetupWizard)) as fileConfig:
        assert "projectName: replacement" in fileConfig.read()
