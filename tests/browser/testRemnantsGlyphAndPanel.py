"""The hub's leftover-processes glyph and its panel, driven in a browser.

The glyph is hidden while the hub's scan holds nothing and appears,
with the server's count and title, once it does; the panel renders the
server's sentences verbatim; Remove selected posts exactly the chosen
ids and renders the outcome sentence the server answers. The scan is
seeded on the hub's own state, so the registry poll, the read route and
the removal route all run for real against the lane's fail-closed
adapter; the removal is answered honestly by the real route, which
finds no such session in the adapter's process table.
"""

import json

import pytest

pytestmark = pytest.mark.browser

S_EVIDENCE = (
    "Interactive session 40 (bash) in container 'browser-lane-project', "
    "started 2026-01-01T00:00:00+00:00, 2 process(es), 12 MB, user 1000. "
    "vaibify did not open it: it may be your own docker exec, or an editor's.")
S_REMEDY = "Select it and choose Remove selected to end every process in the session, with proof."


def _fnSeedScan(serverHub, listItems):
    dictScan = serverHub.app.state.dictRemnantScan
    dictScan["listItems"] = listItems
    dictScan["sScannedIso"] = "2026-01-01T00:00:00+00:00"
    dictScan["sScanError"] = ""
    serverHub.app.state.dictReaperHealth = {
        "ephemeralSecretFiles": {"sReaperName": "ephemeralSecretFiles",
                                 "sLastRunIso": "2026-01-01T00:00:00+00:00",
                                 "sOutcome": "ran", "iRemoved": 3,
                                 "sReason": "", "sRemedy": ""}}


def _fdictSessionItem(serverHub):
    from tests.browser.fakeDockerAdapter import S_CONTAINER_ID, S_CONTAINER_NAME
    return {"sItemId": "s1", "sCategory": "untrackedSession", "sTier": "possibly",
            "sContainerName": S_CONTAINER_NAME, "sEvidence": S_EVIDENCE,
            "sRemedy": S_REMEDY, "sAction": "terminate", "bConfirmRequired": False,
            "dictIdentity": {"sContainerId": S_CONTAINER_ID, "iLeaderPid": 40,
                             "iStartTicks": 500}}


@pytest.fixture(autouse=True)
def fnClearScanAfterwards(serverHub):
    yield
    _fnSeedScan(serverHub, [])


def testTheGlyphIsHiddenAtZeroAndShownWithItems(pageDashboard, serverHub):
    _fnSeedScan(serverHub, [])
    pageDashboard.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    pageDashboard.wait_for_selector("#listContainers .container-tile", timeout=15000)
    assert not pageDashboard.is_visible("#btnRemnants")
    _fnSeedScan(serverHub, [_fdictSessionItem(serverHub)])
    pageDashboard.wait_for_selector("#btnRemnants", state="visible", timeout=15000)
    elGlyph = pageDashboard.query_selector("#btnRemnants")
    assert elGlyph.inner_text().strip() == "⚠ 1"
    assert elGlyph.get_attribute("title") == "1 leftover processes or files; click to review."
    assert pageDashboard.listPageErrors == []
    assert pageDashboard.listConsoleErrors == []


def testThePanelRendersTheServersSentencesVerbatim(pageDashboard, serverHub):
    _fnSeedScan(serverHub, [_fdictSessionItem(serverHub)])
    pageDashboard.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    pageDashboard.wait_for_selector("#btnRemnants", state="visible", timeout=15000)
    pageDashboard.click("#btnRemnants")
    elModal = pageDashboard.wait_for_selector("#modalInfo", timeout=10000)
    sText = elModal.inner_text()
    assert S_EVIDENCE in sText
    assert S_REMEDY in sText
    assert "Possibly orphaned" in sText
    assert "ephemeralSecretFiles: ran, removed 3" in sText
    assert pageDashboard.listPageErrors == []


def testRemoveSelectedPostsTheChosenIdsAndShowsTheOutcome(pageDashboard, serverHub):
    _fnSeedScan(serverHub, [_fdictSessionItem(serverHub)])
    listBodies = []
    pageDashboard.on("request", lambda request: listBodies.append(json.loads(request.post_data))
                     if request.url.endswith("/api/system/remnants/remove") else None)
    pageDashboard.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    pageDashboard.wait_for_selector("#btnRemnants", state="visible", timeout=15000)
    pageDashboard.click("#btnRemnants")
    pageDashboard.wait_for_selector("#modalInfo .remnants-select", timeout=10000)
    pageDashboard.click("#modalInfo .remnants-select")
    pageDashboard.click("#modalInfo .remnants-remove-selected")
    pageDashboard.wait_for_function(
        "() => (document.querySelector('#modalInfo .remnants-outcomes') || {}).textContent"
        ".includes('already gone')", timeout=15000)
    assert listBodies == [{"listItemIds": ["s1"]}]
    assert pageDashboard.listPageErrors == []
    assert pageDashboard.listConsoleErrors == []
