"""The test-generation toast for unparseable outputs says what happened.

From 2026-04 to 2026-10 this toast read "Standards came from the LLM
fallback -- review them carefully" although the backend never called an
LLM there: an unintrospectable step yields no quantitative standards and
an introspector error, nothing else. The toast is now built from the
payload, so a distinctive error string and a standard count served by a
faked generate-test route must reach the screen verbatim.
"""

import json

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow


pytestmark = pytest.mark.browser

S_INTROSPECTOR_ERROR = "fit.h5: no reader for HDF5 group layout q7"


@pytest.fixture(autouse=True)
def fixtureDropClaimsBetweenTests(serverHub):
    """Give every claim back after each test (the hub is module-scoped)."""
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


def _fdictGeneratedPayload():
    dictEmptyCategory = {"saCommands": [], "sFilePath": ""}
    return {
        "bGenerated": True,
        "dictIntegrity": dict(dictEmptyCategory),
        "dictQualitative": dict(dictEmptyCategory),
        "dictQuantitative": {
            "saCommands": [], "sFilePath": "",
            "sStochasticityClassification": "unintrospectable",
            "sIntrospectorError": S_INTROSPECTOR_ERROR,
            "iStandardCount": 0,
        },
    }


@pytest.mark.falsification
def testTheToastCarriesTheIntrospectorErrorAndNoLlmClaim(
        pageDashboard, serverHub):
    """Kills: restoring the fixed "Standards came from the LLM fallback" toast."""
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    pageDashboard.route(
        "**/api/steps/**/generate-test",
        lambda route: route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps(_fdictGeneratedPayload())))
    pageDashboard.evaluate(
        "() => { VaibifyApp.fdictGetWorkflow().listSteps[0]"
        ".saTestCommands = []; VaibifyTestManager.fnGenerateTests(0); }")
    sSelector = "#toastContainer .toast.warning"
    pageDashboard.wait_for_function(
        "(s) => Array.from(document.querySelectorAll(s)).some("
        "e => e.textContent.includes('introspector'))",
        arg=sSelector, timeout=5000)
    sToast = next(
        sText for sText in pageDashboard.locator(sSelector).all_text_contents()
        if "introspector" in sText)
    assert "no quantitative standards were derived" in sToast, sToast
    assert S_INTROSPECTOR_ERROR in sToast, sToast
    assert "LLM" not in sToast, sToast
