"""A project too large to copy in full is offered the files git tracks.

Contracts B3, C (snapshot states) and D (order at convene), in a real
browser against the real capabilities, omission-page and scope routes.
The lane's fake Docker adapter answers the two tracked-scope reads with
a small tracked set beside 30 GB of ignored output — the shape of the
research repository that motivated the scope — and everything the modal
shows is computed by the real server from those answers.
"""

import copy
import os
import tempfile

import pytest

from .testBrowserJourneys import _fnReleaseBrowserLaneOwnership
from .testCouncilPlanningJourney import (  # noqa: F401 — fixture wiring
    _fdictClaimAndActivate,
    _fnIsolateCouncilStore,
)

pytestmark = pytest.mark.browser

I_OUTPUT_FILES = 450


@pytest.fixture(autouse=True)
def fnModelALargeRepository(serverHub, monkeypatch):
    """30 GB of ignored output; three tracked files; restored afterwards."""
    from vaibify.gui import agentCouncilCredentialGate
    sDirectory = tempfile.mkdtemp(prefix="councilScopeLane")
    monkeypatch.setattr(
        agentCouncilCredentialGate, "fsResolveCredentialEvidencePath",
        lambda: os.path.join(sDirectory, "credentialEvidence.json"))
    adapterDocker = serverHub.adapterDocker
    dictWeightBefore = copy.deepcopy(adapterDocker.dictRepositoryWeight)
    adapterDocker.dictRepositoryWeight.update({
        "iFileCount": 345000, "iTotalBytes": 30 * 10 ** 9,
        "bTruncated": True})
    adapterDocker.dictTrackedIdentities = {
        "bSuccess": True, "sReason": "", "sHeadSha": "h", "iChangedCount": 2,
        "sPorcelainDigest": "p", "dictEntries": {
            sPath: {"sMode": "100644", "listStages": [0],
                    "bSkipWorktree": False, "sType": "file",
                    "sIdentity": "ab" * 20, "iSizeBytes": 4096}
            for sPath in ("project.json", "code/step.py", "README.md")}}
    adapterDocker.dictUntrackedInventory = {
        "bSuccess": True, "sReason": "", "bComplete": True,
        "listEntries": [[f"output/run{iIndex:04d}.bin", "ignored", 10 ** 6]
                        for iIndex in range(I_OUTPUT_FILES)]
        + [["scratch.txt", "untracked", 12]]}
    yield adapterDocker
    adapterDocker.dictRepositoryWeight.clear()
    adapterDocker.dictRepositoryWeight.update(dictWeightBefore)
    adapterDocker.dictTrackedIdentities = None
    adapterDocker.dictUntrackedInventory = None
    _fnReleaseBrowserLaneOwnership(serverHub.app.state)


def _fnOpenTheGate(monkeypatch):
    from vaibify.gui import agentCouncilCredentialGate
    monkeypatch.setattr(
        agentCouncilCredentialGate, "fdictEvaluateCredentialEnablement",
        lambda sProvider, sImageIdentity=None: {
            "bEnabled": True, "sReason": "", "dictRecord": {},
            "sState": "authorized"})


@pytest.mark.falsification
def testATooLargeProjectOpensTheSizeModalWithTheNumbers(
        pageDashboard, serverHub, monkeypatch):
    """Readiness ``needsSnapshotChoice``: the click opens the size modal.

    Kills: the toolbar sending a size choice to the consent modal.
    """
    _fnOpenTheGate(monkeypatch)
    _fdictClaimAndActivate(pageDashboard, serverHub)
    pageDashboard.click("#btnAgentCouncil")
    pageDashboard.wait_for_selector("#btnCouncilScopeContinue", timeout=8000)
    sModal = pageDashboard.inner_text("#councilSnapshotScopeModalBody")
    assert "This project is too large to copy in full for a council" in (
        sModal)
    assert "Copy only the files git tracks" in sModal
    assert "3 files (12.3 KB)" in sModal
    assert "2 tracked files have uncommitted edits" in sModal
    assert "never the file names" in sModal
    assert not pageDashboard.is_visible("#councilConsentModal")
    assert pageDashboard.listPageErrors == []
    assert pageDashboard.listConsoleErrors == []


def testTheMissingFilesPageFromTheServer(pageDashboard, serverHub,
                                         monkeypatch):
    """B3 paging: a group expands to real pages, then 'Show more'."""
    _fnOpenTheGate(monkeypatch)
    _fdictClaimAndActivate(pageDashboard, serverHub)
    pageDashboard.click("#btnAgentCouncil")
    pageDashboard.wait_for_selector("#btnCouncilScopeContinue", timeout=8000)
    pageDashboard.click(".council-missing-files > summary")
    pageDashboard.click(
        ".council-omission-group[data-directory='output'] > summary")
    pageDashboard.wait_for_selector(
        ".council-omission-group[data-directory='output'] li", timeout=8000)
    sSelector = ".council-omission-group[data-directory='output'] li"
    assert pageDashboard.locator(sSelector).count() == 200
    pageDashboard.click(".council-omission-more")
    pageDashboard.wait_for_function(
        f"document.querySelectorAll(\"{sSelector}\").length === 400",
        timeout=8000)
    assert "output/run0000.bin" in pageDashboard.inner_text(
        ".council-omission-group[data-directory='output']")


def testContinuingRemembersTheScopeAndReachesTheCouncil(
        pageDashboard, serverHub, monkeypatch):
    """After the choice, the button is ready and the form shows the scope."""
    _fnOpenTheGate(monkeypatch)
    _fdictClaimAndActivate(pageDashboard, serverHub)
    pageDashboard.click("#btnAgentCouncil")
    pageDashboard.wait_for_selector("#btnCouncilScopeContinue", timeout=8000)
    pageDashboard.click("#btnCouncilScopeContinue")
    pageDashboard.wait_for_selector("#btnCouncilPlanChange", timeout=8000)
    pageDashboard.click("#btnCouncilPlanChange")
    pageDashboard.wait_for_selector("#councilSnapshotScopeChoice",
                                    timeout=8000)
    assert pageDashboard.eval_on_selector(
        "#councilSnapshotScopeChoice", "el => el.value") == "gitTracked"
    assert "remembered for this project" in pageDashboard.inner_text(
        "#councilSnapshotScope")


@pytest.mark.falsification
def testNeedsBothAsksAboutSizeBeforeConsent(pageDashboard, serverHub):
    """Order at convene (D): the free question first, the paid one second.

    Kills: the readiness step opening the consent modal first when both
    are needed.
    """
    _fdictClaimAndActivate(pageDashboard, serverHub)
    pageDashboard.click("#btnAgentCouncil")
    pageDashboard.wait_for_selector("#btnCouncilScopeContinue", timeout=8000)
    assert not pageDashboard.is_visible("#councilConsentModal"), (
        "the paid consent opened before the free size question")
    pageDashboard.click("#btnCouncilScopeContinue")
    pageDashboard.wait_for_selector("#btnCouncilConsentRun", timeout=8000)
    assert "First council with this project's image" in (
        pageDashboard.inner_text("#councilConsentModalBody"))


def testWhenEvenTheTrackedFilesDoNotFitTheButtonSaysWhy(
        pageDashboard, serverHub, monkeypatch, fnModelALargeRepository):
    _fnOpenTheGate(monkeypatch)
    for dictEntry in fnModelALargeRepository.dictTrackedIdentities[
            "dictEntries"].values():
        dictEntry["iSizeBytes"] = 10 ** 11
    _fdictClaimAndActivate(pageDashboard, serverHub)
    assert pageDashboard.evaluate(
        "() => document.getElementById('btnAgentCouncil')"
        ".classList.contains('council-blocked')")
    pageDashboard.click("#btnAgentCouncil")
    pageDashboard.wait_for_selector(".toast", timeout=8000)
    assert "does not help" in pageDashboard.inner_text(".toast")


def testAnIncompleteInventorySaysAtLeast(pageDashboard, serverHub,
                                         monkeypatch, fnModelALargeRepository):
    _fnOpenTheGate(monkeypatch)
    fnModelALargeRepository.dictUntrackedInventory["bComplete"] = False
    _fdictClaimAndActivate(pageDashboard, serverHub)
    pageDashboard.click("#btnAgentCouncil")
    pageDashboard.wait_for_selector("#btnCouncilScopeContinue", timeout=8000)
    assert "Files that will be missing: at least" in pageDashboard.inner_text(
        "#councilSnapshotScopeModalBody")
