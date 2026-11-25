"""The remote-binding review always leaves a way to store a token.

Source: ``vaibify/gui/static/scriptRemoteBindings.js`` (the review's
buttons and its token action) and ``scriptSyncManager.js`` (the token
box).

The review once offered "Store your token" at most once per dialog, and
the token box reported a failed store in a toast that vanished, so a
researcher whose first store failed was left with a disabled Confirm, no
Store button and no word of why (researcher-reported, 2026-11-24). The
answers below are built by the server's own functions, so the page is
tested against the sentences and buttons the server sends.
"""

import json
import re

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow
from tests.browser.testRemoteBindingDialogIsTheServers import (  # noqa: F401
    _Hub, _fdictDescribed, _fdictEntry, _fnOpenTheDialogFor,
    _fsDialogText, _fnWaitFor, fnReleaseEveryHeldRoute,
)
from vaibify.gui.routes.bindingRoutes import _fdictEvidenceFailure
from vaibify.reproducibility import overleafAuth, zenodoClient


pytestmark = pytest.mark.browser

_RE_SETUP = re.compile(r".*/api/sync/[^/]+/setup$")
S_OVERLEAF_PROJECT = "64a1b2c3d4e5f6a7b8c9d0e1"


class _TokenBox:
    """The token box's store route, answered (or held) from the test."""

    def __init__(self, pageDashboard, listAnswers, bHold=False):
        self.listAnswers = list(listAnswers)
        self.listBodies, self.listHeld = [], []
        self.bHold = bHold
        pageDashboard.route(_RE_SETUP, self._fnStore)

    def _fnStore(self, routeIntercepted):
        self.listBodies.append(json.loads(routeIntercepted.request.post_data))
        if self.bHold:
            self.listHeld.append(routeIntercepted)
            return
        self.fnAnswer(routeIntercepted)

    def fnAnswer(self, routeIntercepted):
        dictAnswer = (
            self.listAnswers.pop(0) if len(self.listAnswers) > 1
            else self.listAnswers[0]
        )
        routeIntercepted.fulfill(
            status=200, content_type="application/json",
            body=json.dumps(dictAnswer),
        )


def _fdictZenodoTokenMissing():
    return _fdictEntry(
        "zenodo", "project", "zenodo",
        dictEvidence=_fdictEvidenceFailure("zenodo", "zenodo", "token-missing"),
    )


def _fdictRejectedStore():
    return {
        "bConnected": False,
        **zenodoClient.fdictDescribeZenodoFailure("token-rejected", "zenodo"),
    }


def _fnStoreThroughTheBox(pageDashboard, sToken):
    pageDashboard.wait_for_selector(
        "#modalConnectionSetup", state="visible", timeout=10000)
    pageDashboard.fill("#inputSetupToken", sToken)
    pageDashboard.click("#btnSetupSave")


@pytest.mark.falsification
def testAFailedStoreLeavesTheReviewAWayForwardAndSaysWhy(pageDashboard, serverHub):
    """A rejected store keeps its sentence, and Store is offered again.

    Kills: withholding the token action after a store has failed (the
    old one-prompt-per-dialog cap).
    """
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    _Hub(pageDashboard, [_fdictDescribed([_fdictZenodoTokenMissing()])])
    boxStore = _TokenBox(pageDashboard, [_fdictRejectedStore()])
    _fnOpenTheDialogFor(pageDashboard, ["zenodo", "project", "zenodo"])
    pageDashboard.wait_for_selector("#btnRemoteBindingStoreToken", timeout=10000)
    pageDashboard.click("#btnRemoteBindingStoreToken")
    _fnStoreThroughTheBox(pageDashboard, "a-token-zenodo-refuses")
    pageDashboard.wait_for_selector("#setupFailureMessage:not([hidden])", timeout=10000)
    sFailure = pageDashboard.locator("#setupFailureMessage").inner_text()
    assert "zenodo.org rejected the token" in sFailure
    assert pageDashboard.locator("#modalConnectionSetup").is_visible()
    pageDashboard.click("#btnSetupCancel")
    pageDashboard.wait_for_selector("#btnRemoteBindingStoreToken", timeout=10000)
    assert "Your token was not stored: zenodo.org rejected the token" in (
        _fsDialogText(pageDashboard))
    assert pageDashboard.locator("#btnRemoteBindingConfirm").is_disabled()
    assert len(boxStore.listBodies) == 1
    assert boxStore.listBodies[0]["bCredentialOnly"] is True


def testDoubleClickingSaveSendsOneStore(pageDashboard, serverHub):
    """Two clicks while the first store is being checked send one request."""
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    _Hub(pageDashboard, [_fdictDescribed([_fdictZenodoTokenMissing()])])
    boxStore = _TokenBox(pageDashboard, [_fdictRejectedStore()], bHold=True)
    _fnOpenTheDialogFor(pageDashboard, ["zenodo", "project", "zenodo"])
    pageDashboard.wait_for_selector("#btnRemoteBindingStoreToken", timeout=10000)
    pageDashboard.click("#btnRemoteBindingStoreToken")
    pageDashboard.wait_for_selector(
        "#modalConnectionSetup", state="visible", timeout=10000)
    pageDashboard.fill("#inputSetupToken", "a-token")
    pageDashboard.evaluate(
        "() => { var el = document.getElementById('btnSetupSave');"
        " el.click(); el.click(); el.click(); }")
    _fnWaitFor(pageDashboard, lambda: boxStore.listHeld, "the store request")
    pageDashboard.wait_for_timeout(300)
    assert len(boxStore.listBodies) == 1
    boxStore.fnAnswer(boxStore.listHeld.pop())
    pageDashboard.wait_for_selector("#setupFailureMessage:not([hidden])", timeout=10000)


def testReplacingTheOverleafTokenFixesTheProjectUnderReview(pageDashboard, serverHub):
    """Replace opens the box on the reviewed project, asking for a NEW token.

    The binding compares project.json's Overleaf project, so the box
    shows that ID read-only, never offers to reuse the token Overleaf
    just rejected, says the token is shared by every project, and stores
    without recording any setting.
    """
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    dictRejected = _fdictEntry(
        "overleaf", "", "",
        dictTarget={"sProjectId": S_OVERLEAF_PROJECT},
        sLabel="this workflow's Overleaf project",
        dictEvidence=_fdictEvidenceFailure(
            "overleaf", "", overleafAuth.fsClassifyOverleafGitFailure(
                "fatal: Authentication failed for 'https://git.overleaf.com/x/'"),
        ),
    )
    _Hub(pageDashboard, [_fdictDescribed([dictRejected])])
    boxStore = _TokenBox(pageDashboard, [{"bConnected": True, "sMessage": "Connected"}])
    _fnOpenTheDialogFor(pageDashboard, ["overleaf", "", ""])
    pageDashboard.wait_for_selector("#btnRemoteBindingStoreToken", timeout=10000)
    assert pageDashboard.locator(
        "#btnRemoteBindingStoreToken").inner_text() == "Replace your token"
    pageDashboard.click("#btnRemoteBindingStoreToken")
    pageDashboard.wait_for_selector(
        "#modalConnectionSetup", state="visible", timeout=10000)
    elProjectId = pageDashboard.locator("#inputSetupProjectId")
    assert elProjectId.input_value() == S_OVERLEAF_PROJECT
    assert elProjectId.evaluate("(el) => el.readOnly") is True
    assert pageDashboard.locator("#rowOverleafReuse").count() == 0
    assert pageDashboard.locator("#noteSetupOverleafShared").is_visible()
    pageDashboard.fill("#inputSetupToken", "a-new-overleaf-token")
    pageDashboard.click("#btnSetupSave")
    _fnWaitFor(pageDashboard, lambda: boxStore.listBodies, "the store request")
    dictBody = boxStore.listBodies[0]
    assert (dictBody["sService"], dictBody["sProjectId"]) == (
        "overleaf", S_OVERLEAF_PROJECT)
    assert dictBody["bCredentialOnly"] is True
    pageDashboard.wait_for_selector(
        "#modalConnectionSetup", state="hidden", timeout=10000)
