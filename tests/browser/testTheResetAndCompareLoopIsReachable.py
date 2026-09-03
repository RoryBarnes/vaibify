"""Reset and compare, from the Run menu, in a real browser, in host mode.

A researcher who opens somebody else's published project has exactly
one question: do the steps still produce these bytes? Answering it is a
two-move loop -- clear the outputs, run, compare against
``MANIFEST.sha256`` -- and until this existed neither move had a
top-level home. Clean was reachable only bundled inside "Force Run All",
which reruns everything in the same click and so never shows the state
in between; the manifest comparison existed only in the PROOF tab's
manifest artifact row, behind two expansions.

Driven in HOST mode deliberately. Host projects are capped below Level
3 (``levelGates.flistLevel3Blockers`` answers ``host-mode`` and
nothing else), so it is the mode where a reader is most likely to be
checking somebody else's work without a container -- and the mode where
a gate added "because this is a Level 3 concern" would silently remove
the answer. Both items must be reachable with no Docker at all.

ONE test, opening the project once, because ``serverHub`` is
module-scoped and a browser context that claims a project holds the
record through a 30-second reconnect grace
(``containerOwnership._F_GRACE_SECONDS``) -- so a second test's fresh
context is a foreign session and is correctly refused. That was
measured here rather than assumed: a scratch module whose every test
did nothing but open the project failed on the second one, with none
of this feature involved. Splitting these assertions across two tests
would therefore be testing the grace window, not the menu. It also
happens to be the truer journey: a researcher opens the project once
and does both.

The POSTs are intercepted rather than served. What is under test is
that each gesture reaches the right endpoint; the endpoints themselves
are driven in the Python suite (``TestFlistBuildCleanCommands`` for
what a clean deletes, and the manifest verify's own route tests).
"""

import json

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow


pytestmark = pytest.mark.browser

S_CLEAN_ROUTE_GLOB = "**/api/pipeline/*/clean"
S_MANIFEST_ROUTE_GLOB = "**/api/workflow/*/manifest/verify"


def _fnOpenTheRunMenu(page):
    """Open the Run menu the way a researcher does."""
    page.click("#toolbarMenuRun .toolbar-menu-trigger")
    page.wait_for_selector(
        "#btnCleanOutputs", state="visible", timeout=5000,
    )


def _flistRecordRequestsTo(page, sGlob, dictAnswer):
    """Answer a route with a canned payload, recording each call."""
    listCalls = []

    def fnHandle(routeIntercepted):
        listCalls.append(routeIntercepted.request.url)
        routeIntercepted.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(dictAnswer),
        )

    page.route(sGlob, fnHandle)
    return listCalls


def testTheRunMenuOffersCleanThenTheManifestComparison(
    pageDashboard, serverHub,
):
    """The whole reset-and-compare loop, with no container anywhere.

    Kills four things, each of which has a way of coming back:

    * Removing the standalone Clean puts the reader back to Force Run
      All, whose clean is invisible because the rerun overwrites what
      it cleared.
    * Dropping the confirmation turns a menu item one slot below "Run
      All Steps" into an unannounced delete of every output file.
    * Gating the manifest comparison on Level 3 or on container mode.
      A manifest is a list of hashes over files on disk; recomputing
      them needs no container, and the host-mode reader checking a
      published project is the one who needs the answer most.
    * Re-wording the verdict in the menu handler. It is asserted here
      because it comes from the single ``verify-manifest`` action
      formatter -- a second, hand-written copy would satisfy a "did it
      POST" assertion and drift from the PROOF tab's wording by the
      next release.
    """
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    listCleanCalls = _flistRecordRequestsTo(
        pageDashboard, S_CLEAN_ROUTE_GLOB, {"bSuccess": True},
    )
    listVerifyCalls = _flistRecordRequestsTo(
        pageDashboard, S_MANIFEST_ROUTE_GLOB,
        {"iTotal": 7, "iMatching": 7, "listMismatches": []},
    )

    _fnOpenTheRunMenu(pageDashboard)
    pageDashboard.click("#btnCleanOutputs")
    pageDashboard.wait_for_selector("#modalConfirm", timeout=5000)
    assert listCleanCalls == [], (
        "the clean was POSTed before the researcher confirmed it"
    )
    sBody = pageDashboard.text_content("#modalConfirm p")
    assert "figures included" in sBody, (
        "the confirmation must say figures are deleted too -- the "
        f"builder used to skip every one of them: {sBody}"
    )

    pageDashboard.click("#btnConfirmOk")
    pageDashboard.wait_for_selector(
        "text=every step is back to never-run", timeout=10000,
    )
    assert len(listCleanCalls) == 1, listCleanCalls

    _fnOpenTheRunMenu(pageDashboard)
    pageDashboard.click("#btnVerifyManifest")
    pageDashboard.wait_for_selector(
        "text=All 7 manifest files match", timeout=10000,
    )
    assert len(listVerifyCalls) == 1, listVerifyCalls
    assert pageDashboard.listPageErrors == []
