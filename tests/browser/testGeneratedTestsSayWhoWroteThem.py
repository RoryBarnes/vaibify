"""A model-written test tier says so, and a failed tier says why.

The generator records who wrote each category (``sGenerator``). A tier
a language model wrote after vaibify could not read the outputs must
say so on the step's test rows, and survive the page's own save of the
step; a tier that failed must show its own reason, which for a fallback
that could not run names both failures.
"""

import json

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow


pytestmark = pytest.mark.browser

S_BOTH_FAILURES = (
    "No quantitative standards could be derived (blob.bin: unsupported "
    "binary format). No language-model fallback is available: this "
    "container has no Claude CLI, and no stored API key was requested.")


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


def _fnServeGeneration(pageDashboard, dictQuantitative):
    dictEmpty = {"saCommands": [], "sFilePath": "",
                 "sGenerator": "deterministic"}
    dictPayload = {"bGenerated": True, "dictIntegrity": dict(dictEmpty),
                   "dictQualitative": dict(dictEmpty),
                   "dictQuantitative": dictQuantitative}
    # The hub answers with the step as it saved it, and the page adopts
    # that copy rather than rebuilding the categories itself.
    dictPayload["dictStep"] = {
        "dictTests": {
            "dictIntegrity": dict(dictEmpty),
            "dictQualitative": dict(dictEmpty),
            "dictQuantitative": dictQuantitative,
        },
        "dictVerification": {"sUnitTest": "untested", "sUser": "untested"},
        "saTestCommands": [],
    }
    pageDashboard.route(
        "**/api/steps/**/generate-test",
        lambda route: route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps(dictPayload)))


def _fnGenerateForFirstStep(pageDashboard):
    pageDashboard.evaluate(
        "() => { VaibifyApp.fdictGetWorkflow().listSteps[0]"
        ".saTestCommands = []; VaibifyTestManager.fnGenerateTests(0); }")


@pytest.mark.falsification
def testAModelWrittenTierSaysSoOnItsRow(pageDashboard, serverHub):
    """Kills: dropping sGenerator from the page's copy of the step."""
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    pageDashboard.click('.step-item[data-index="0"]')
    _fnServeGeneration(pageDashboard, {
        "saCommands": [], "sFilePath": "",
        "sStochasticityClassification": "deterministic",
        "sGenerator": "llm-fallback"})
    _fnGenerateForFirstStep(pageDashboard)
    sNoteSelector = ('.step-detail[data-index="0"] .sub-test-note:has-text('
                     '"Written by a language model")')
    pageDashboard.wait_for_selector(sNoteSelector, timeout=5000)
    assert pageDashboard.locator(sNoteSelector).count() == 1
    assert "could not read this step's outputs" in (
        pageDashboard.text_content(sNoteSelector))
    assert pageDashboard.evaluate(
        "() => VaibifyApp.fdictGetWorkflow().listSteps[0].dictTests"
        ".dictQuantitative.sGenerator") == "llm-fallback"


def testAFailedTierShowsItsOwnReason(pageDashboard, serverHub):
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    _fnServeGeneration(pageDashboard, {
        "saCommands": [], "sFilePath": "", "sError": S_BOTH_FAILURES})
    _fnGenerateForFirstStep(pageDashboard)
    pageDashboard.wait_for_selector(
        "#modalError", state="visible", timeout=5000)
    sModal = pageDashboard.inner_text("#modalError")
    assert "blob.bin: unsupported binary format" in sModal, sModal
    assert "No language-model fallback is available" in sModal, sModal
