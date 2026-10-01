"""The setup wizard page signs itself in and then works.

The wizard's server refuses every ``/api`` call that does not carry a
per-browser credential. The page redeems the one-time capability in its
launch URL fragment for that credential, clears the fragment, and presents
the credential on every call. A page that failed to do so would load, show
"No templates available" and silently lose the researcher's Save.

Kills (confirmed -- the mutation was applied, this test run, and the
named assertion observed to fail):
  - the page not presenting its credential on the API calls -> the
    template grid stays empty and the save is refused.
"""

import os

import pytest

pytestmark = pytest.mark.browser


def _fpageOpen(browserEngine):
    contextBrowser = browserEngine.new_context()
    page = contextBrowser.new_page()
    page.listPageErrors = []
    page.on("pageerror", lambda error: page.listPageErrors.append(str(error)))
    return contextBrowser, page


@pytest.mark.falsification
def testALaunchedWizardLoadsTemplatesAndSavesThroughItsCredential(
    browserEngine, serverSetupWizard,
):
    """Kills: dropping the credential header from the wizard's fetches."""
    contextBrowser, page = _fpageOpen(browserEngine)
    try:
        page.goto(serverSetupWizard.fsBootstrapUrl(), wait_until="load")
        page.wait_for_selector(".template-card", timeout=15000)
        assert "bootstrap" not in page.url, (
            "the one-time capability must be cleared from the address bar"
        )
        page.fill("#projectName", "wizardSignedIn")
        page.fill("#containerUser", "researcher")
        page.click("#btnSaveConfig")
        page.wait_for_selector(".toast.success", timeout=15000)
        assert os.path.isfile(
            os.path.join(serverSetupWizard.sOutputDirectory, "vaibify.yml"))
        page.reload(wait_until="load")
        page.wait_for_selector(".template-card", timeout=15000)
        assert page.listPageErrors == []
    finally:
        contextBrowser.close()


def testAWizardTabWithoutACredentialIsDeniedNotHalfWorking(
    browserEngine, serverSetupWizard,
):
    contextBrowser, page = _fpageOpen(browserEngine)
    try:
        page.goto(serverSetupWizard.sBaseUrl + "/", wait_until="load")
        page.wait_for_selector("#templateGrid .muted-text", timeout=15000)
        assert page.locator(".template-card").count() == 0
        page.fill("#projectName", "wizardDenied")
        page.click("#btnSaveConfig")
        page.wait_for_selector(".toast.error", timeout=15000)
        sConfigPath = os.path.join(
            serverSetupWizard.sOutputDirectory, "vaibify.yml")
        if os.path.isfile(sConfigPath):
            with open(sConfigPath) as fileConfig:
                assert "wizardDenied" not in fileConfig.read()
    finally:
        contextBrowser.close()
