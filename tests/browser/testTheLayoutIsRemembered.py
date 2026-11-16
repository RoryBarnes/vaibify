"""The dashboard remembers how it was arranged, saves in order, and asks honestly.

Source: ``scriptDashboardMemory.js`` (saving), ``scriptEntryChoice.js``
(the entry dialog and restore) and ``routes/dashboardStateRoutes.py``.

Every journey enters through the real hub screens and the real project
card, against the real hub. The expectations come from the plan's
rules (plan sections 4, 7 and 2), not from the code under test:

- a reload starts its saves from the STORED revision;
- a save that lost the race to another writer is discarded, never
  retried, the other writer's record survives, the page says so, and
  its next change saves;
- a restore saves nothing until it has finished;
- a step inserted above the selection does not move the selection;
- Continue waits until every chosen chat has a pane and every bypassed
  chat an answer;
- Admin > Projects keeps the live terminals: the next project opens
  without asking and with its own saved view.
"""

import json
import os

import pytest

from tests.browser.conftest import (
    S_HOST_PROJECT_READY,
    S_HOST_STEP_NAME,
    S_HOST_WORKFLOW_NAME,
    fnAnswerTheEntryDialog,
    fnOpenTheSeededHostWorkflow,
)

pytestmark = pytest.mark.browser

S_UUID_BYPASSED = "1c37be47-a36d-45b8-9688-6f971b50c106"
S_UUID_STANDARD = "96969a67-6545-426c-b128-aeacfda91223"
S_PROJECT_ROUTE = "**/api/dashboard-state/*/project"
S_CONFLICT_NOTICE = "Layout saved from another window"


def _fbIsProjectSave(response):
    return (response.request.method == "PUT"
            and response.url.endswith("/project"))


def _fdictProjectRecord(page):
    """Read this project's record exactly as the page would."""
    return page.evaluate("""async () => {
        const dictKey = VaibifyApp.fdictGetProjectRecordKey();
        return VaibifyApi.fdictGet('/api/dashboard-state/' +
            encodeURIComponent(dictKey.sContainerId) + '/project' +
            '?sProjectRepoPath=' + encodeURIComponent(dictKey.sProjectRepoPath) +
            '&sWorkflowPath=' + encodeURIComponent(dictKey.sWorkflowPath));
    }""")


def _fiWriteProjectRecordAsAnotherWindow(page, dictState):
    """Store a record from outside this page's saver; return its revision."""
    return page.evaluate("""async (dictState) => {
        const dictKey = VaibifyApp.fdictGetProjectRecordKey();
        const sBase = '/api/dashboard-state/' +
            encodeURIComponent(dictKey.sContainerId) + '/project';
        const dictStored = await VaibifyApi.fdictGet(sBase +
            '?sProjectRepoPath=' + encodeURIComponent(dictKey.sProjectRepoPath) +
            '&sWorkflowPath=' + encodeURIComponent(dictKey.sWorkflowPath));
        const dictAnswer = await VaibifyApi.fdictPut(sBase, {
            sProjectRepoPath: dictKey.sProjectRepoPath,
            sWorkflowPath: dictKey.sWorkflowPath,
            iBaseRevision: dictStored.iRevision, dictState: dictState});
        return dictAnswer.iRevision;
    }""", dictState)


def _fnReloadAndOpen(page, sEntryChoice):
    """Reload the page -- a real unload -- and open the project again."""
    page.reload(wait_until="load")
    # WebKit reports the replaced page's in-flight polls, which the
    # reload itself cancels, as "access control checks" page errors;
    # they belong to the discarded document, not the reloaded one.
    page.listPageErrors.clear()
    sTile = f'.container-tile[data-name="{S_HOST_PROJECT_READY}"]'
    page.wait_for_selector(sTile + ":not(.container-tile--locked)",
                           timeout=90000)
    page.click(sTile + " .container-tile-main")
    page.wait_for_function(
        """() => {
            const elPicker = document.getElementById('workflowPicker');
            const elModal = document.getElementById('modalConfirm');
            return (elPicker && elPicker.style.display === 'flex') ||
                (elModal && getComputedStyle(elModal).display !== 'none');
        }""", timeout=20000)
    if page.is_visible("#modalConfirm"):
        page.click("#btnConfirmOk")
    page.wait_for_selector(f"text={S_HOST_WORKFLOW_NAME}", timeout=20000)
    page.click(f"text={S_HOST_WORKFLOW_NAME}")
    fnAnswerTheEntryDialog(page, sEntryChoice)
    page.wait_for_selector(f"text={S_HOST_STEP_NAME}", timeout=20000)
    page.wait_for_function(
        "() => !document.body.dataset.entryState", timeout=20000)


def _fnClickLeftTab(page, sPanel):
    page.click(f'.left-tab[data-panel="{sPanel}"]')


def _fsActiveLeftTab(page):
    return page.evaluate(
        "() => document.querySelector('.left-tab.active').dataset.panel")


def test_a_reload_starts_from_the_stored_revision(pageDashboard, serverHub):
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    with pageDashboard.expect_response(_fbIsProjectSave, timeout=15000) as info:
        _fnClickLeftTab(pageDashboard, "files")
    assert info.value.status == 200, info.value.text()
    iFirst = info.value.json()["iRevision"]
    _fnReloadAndOpen(pageDashboard, "latest")
    assert _fsActiveLeftTab(pageDashboard) == "files", (
        "Latest restores the project's own view")
    with pageDashboard.expect_response(_fbIsProjectSave, timeout=15000) as info:
        _fnClickLeftTab(pageDashboard, "steps")
    assert info.value.status == 200, (
        "a reloaded page saves from the stored revision, never from zero: "
        + info.value.text())
    assert info.value.json()["iRevision"] > iFirst
    assert pageDashboard.listPageErrors == []


@pytest.mark.falsification
def test_a_save_that_lost_the_race_is_discarded_and_the_next_change_saves(
    pageDashboard, serverHub,
):
    """Kills: retrying a refused snapshot, which wrote an OLDER layout
    over the change another window had saved after it."""
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    with pageDashboard.expect_response(_fbIsProjectSave, timeout=15000):
        _fnClickLeftTab(pageDashboard, "files")
    listHeld = []

    def fnHoldTheFirstSave(routeIntercepted):
        if routeIntercepted.request.method == "PUT" and not listHeld:
            listHeld.append(routeIntercepted)
            return
        routeIntercepted.continue_()

    pageDashboard.route(S_PROJECT_ROUTE, fnHoldTheFirstSave)
    _fnClickLeftTab(pageDashboard, "steps")
    for _ in range(100):
        if listHeld:
            break
        pageDashboard.wait_for_timeout(100)
    assert listHeld, "the page never sent its save"
    iOther = _fiWriteProjectRecordAsAnotherWindow(
        pageDashboard, {"sLeftTab": "repos"})
    with pageDashboard.expect_response(_fbIsProjectSave, timeout=15000) as info:
        listHeld[0].continue_()
    assert info.value.status == 409
    pageDashboard.wait_for_selector(f"text={S_CONFLICT_NOTICE}", timeout=10000)
    dictStored = _fdictProjectRecord(pageDashboard)
    assert dictStored["iRevision"] == iOther
    assert dictStored["dictState"] == {"sLeftTab": "repos"}, (
        "the other window's record survives; the stale snapshot is not "
        "retried")
    pageDashboard.unroute(S_PROJECT_ROUTE)
    with pageDashboard.expect_response(_fbIsProjectSave, timeout=15000) as info:
        _fnClickLeftTab(pageDashboard, "files")
    assert info.value.status == 200, info.value.text()
    assert info.value.json()["iRevision"] == iOther + 1


def test_a_restore_saves_nothing_until_it_has_finished(
    pageDashboard, serverHub,
):
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    with pageDashboard.expect_response(_fbIsProjectSave, timeout=15000):
        _fnClickLeftTab(pageDashboard, "files")
    pageDashboard.add_init_script("""(() => {
        window.listLayoutSaves = [];
        const fetchOriginal = window.fetch;
        window.fetch = function (sUrl, dictOptions) {
            if (dictOptions && dictOptions.method === 'PUT' &&
                    String(sUrl).indexOf('/api/dashboard-state/') >= 0) {
                window.listLayoutSaves.push({
                    sUrl: String(sUrl),
                    sEntryState: document.body.dataset.entryState || '',
                    dictBody: JSON.parse(dictOptions.body)});
            }
            return fetchOriginal.apply(this, arguments);
        };
    })();""")
    _fnReloadAndOpen(pageDashboard, "latest")
    pageDashboard.wait_for_timeout(2500)
    listSaves = pageDashboard.evaluate("() => window.listLayoutSaves")
    assert [d for d in listSaves if d["sEntryState"] == "entering"] == []
    listProjectSaves = [d for d in listSaves if d["sUrl"].endswith("/project")]
    for dictSave in listProjectSaves:
        assert dictSave["dictBody"]["dictState"]["sLeftTab"] == "files", (
            "a save after the restore records the restored view, whole")


def test_a_step_inserted_above_the_selection_keeps_it_selected(
    pageDashboard, serverHub,
):
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    pageDashboard.click("text=Second Stage")
    pageDashboard.wait_for_function(
        "() => VaibifyApp.fiGetSelectedStepIndex() === 1", timeout=10000)
    dictView = pageDashboard.evaluate(
        "() => VaibifyApp.fdictCaptureProjectView()")
    assert dictView["sSelectedStepId"] == "second-stage"
    iRestored = pageDashboard.evaluate("""(dictView) => {
        VaibifyApp.fdictGetWorkflow().listSteps.unshift(
            {sName: 'Inserted Above', sStepId: 'inserted-above'});
        VaibifyApp.fnRestoreProjectView(dictView);
        return VaibifyApp.fiGetSelectedStepIndex();
    }""", dictView)
    assert iRestored == 2, "the selection follows the step, not its index"


def _fnPlantTranscripts(sHome, sRepo):
    """One Claude chat that ran bypassed, one that did not, in this project."""
    sDirectory = os.path.join(sHome, ".claude", "projects", "-browser-lane")
    os.makedirs(sDirectory, exist_ok=True)
    for sSessionId, sMode, sPrompt in (
        (S_UUID_BYPASSED, "bypassPermissions", "Refit the curves"),
        (S_UUID_STANDARD, "default", "Plot the residuals"),
    ):
        with open(os.path.join(sDirectory, sSessionId + ".jsonl"), "w") as fileOut:
            for dictLine in (
                {"type": "user", "cwd": sRepo, "permissionMode": sMode,
                 "message": {"content": sPrompt}},
                {"type": "assistant", "permissionMode": sMode},
            ):
                fileOut.write(json.dumps(dictLine) + "\n")


def _fsHostProjectDirectory():
    from vaibify.config.registryManager import fdictGetProject
    return os.path.realpath(fdictGetProject(S_HOST_PROJECT_READY)["sDirectory"])


def test_continue_waits_for_every_chosen_chat_to_be_placed_and_answered(
    pageDashboard, serverHub, tmp_path, monkeypatch,
):
    sAgentHome = str(tmp_path / "agentHome")
    _fnPlantTranscripts(sAgentHome, _fsHostProjectDirectory())
    monkeypatch.setenv("HOME", sAgentHome)
    pageDashboard.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    sTile = f'.container-tile[data-name="{S_HOST_PROJECT_READY}"]'
    pageDashboard.wait_for_selector(
        sTile + ":not(.container-tile--locked)", timeout=90000)
    pageDashboard.click(sTile + " .container-tile-main")
    pageDashboard.wait_for_selector("#modalConfirm", timeout=10000)
    pageDashboard.click("#btnConfirmOk")
    pageDashboard.wait_for_selector(f"text={S_HOST_WORKFLOW_NAME}", timeout=20000)
    pageDashboard.click(f"text={S_HOST_WORKFLOW_NAME}")
    pageDashboard.wait_for_selector("#modalEntryChoice", timeout=20000)
    pageDashboard.check('input[name="entryChoice"][value="choose"]')
    sChoose = f'input[name="choose:claude:{S_UUID_BYPASSED}"]'
    pageDashboard.wait_for_selector(sChoose, timeout=30000)
    assert pageDashboard.is_visible("text=has not verified who wrote them")
    assert pageDashboard.is_visible("text=Refit the curves")
    pageDashboard.check(sChoose)
    elContinue = pageDashboard.locator("#btnEntryContinue")
    assert elContinue.is_disabled()
    assert "not placed in a pane" in elContinue.get_attribute("title")
    pageDashboard.select_option(f'select[name="pane:claude:{S_UUID_BYPASSED}"]', "2")
    assert elContinue.is_disabled()
    assert "bypassed permissions" in elContinue.get_attribute("title")
    pageDashboard.check(
        f'input[name="gate:claude:{S_UUID_BYPASSED}"][value="standard"]')
    assert elContinue.is_enabled()
    pageDashboard.check(f'input[name="choose:claude:{S_UUID_STANDARD}"]')
    assert elContinue.is_disabled(), "the second chosen chat has no pane yet"
    pageDashboard.select_option(f'select[name="pane:claude:{S_UUID_STANDARD}"]', "2")
    assert elContinue.is_enabled(), "a chat that ran standard needs no answer"
    pageDashboard.click("#btnEntryCancel")
    assert pageDashboard.listPageErrors == []


@pytest.mark.falsification
def test_admin_projects_keeps_the_terminals_and_restores_the_projects_view(
    pageDashboard, serverHub,
):
    """Kills: asking again while the environment's terminals are live."""
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    pageDashboard.click("#terminalStrip .terminal-pane-container")
    pageDashboard.wait_for_function(
        "() => VaibifyTerminal.fdictCaptureLayout().listPanes[0]"
        ".listTabs[0].bShellStarted", timeout=20000)
    sTerminalId = pageDashboard.evaluate(
        "() => VaibifyTerminal.flistTabsAt()[0].sTerminalId")
    _fiWriteProjectRecordAsAnotherWindow(pageDashboard, {"sLeftTab": "files"})
    pageDashboard.evaluate("() => VaibifyApp.fnLeaveToProjectHub()")
    pageDashboard.wait_for_selector(f"text={S_HOST_WORKFLOW_NAME}", timeout=20000)
    pageDashboard.click(f"text={S_HOST_WORKFLOW_NAME}")
    assert pageDashboard.evaluate(
        "() => document.body.dataset.entryState") == "entering", (
        "with the terminals live, the click goes straight in")
    pageDashboard.wait_for_selector(f"text={S_HOST_STEP_NAME}", timeout=20000)
    pageDashboard.wait_for_function(
        "() => !document.body.dataset.entryState", timeout=20000)
    assert pageDashboard.locator("#modalEntryChoice").count() == 0
    assert pageDashboard.evaluate(
        "() => VaibifyTerminal.flistTabsAt().map(d => d.sTerminalId)") == [
        sTerminalId], "the same terminal, not a restored one"
    assert pageDashboard.evaluate(
        "() => VaibifyTerminal.fdictCaptureLayout().listPanes[0]"
        ".listTabs[0].bShellStarted") is True
    assert _fsActiveLeftTab(pageDashboard) == "files", (
        "the project opens with its own saved view")
