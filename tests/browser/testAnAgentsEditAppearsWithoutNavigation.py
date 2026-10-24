"""A change the open tab did not make appears in it, unasked.

A step an in-container agent added reached the dashboard only after the
researcher left the project and came back (alpha tester, 2026-10-23).
The server half -- an agent's committed save advances the epoch and
queues a notice, the tab's own saves do neither -- is driven over real
HTTP with the agent's own token in
``tests/testAgentEditsReachTheOpenTab.py``. This lane has no container
project whose dashboard it can open, and a host project has no agent
lane by design (its agent is the researcher), so here the agent's poll
answer is staged on top of the REAL poll's answer, and what is
exercised is the page's own handling of it: the row, the notice, and
the "reloaded from disk" toast that must not appear.

Two journeys need no staging at all: an open step editor whose project
changed underneath it, and a sandbox promoted through "Convert to
Project" whose ``project.json`` is then edited on disk, which is how a
host project's agent changes it.
"""

import json
import os
import time

import httpx
import pytest

from tests.browser.conftest import (
    S_HOST_STEP_NAME,
    fnOpenTheSeededHostWorkflow,
)


pytestmark = pytest.mark.browser

S_FILE_STATUS_GLOB = "**/api/pipeline/*/file-status*"
S_AGENT_STEP = "Agent Added Step"
S_NOTICE = "An in-container agent changed this project: the step creation."

_S_POLL_ONCE = """async () => {
    await VaibifyPolling.fnPollFileStatusOnce(VaibifyApp.fsGetContainerId());
    await new Promise(fnResolve => requestAnimationFrame(
        () => requestAnimationFrame(fnResolve)));
}"""


def _fnStageTheAgentsPollAnswer(page, listRequestUrls):
    """Answer each poll as the hub does after an agent added a step.

    The real answer is fetched and only the agent's change is laid on
    top: the workflow with one step more, the epoch advanced by an
    agent, and the notice the hub queued -- repeated on every poll, as
    the hub repeats it until the tab acknowledges.
    """
    def fnAnswer(routeIntercepted):
        listRequestUrls.append(routeIntercepted.request.url)
        response = routeIntercepted.fetch()
        dictStatus = json.loads(response.text())
        dictWorkflow = json.loads(json.dumps(
            page.evaluate("() => VaibifyApp.fdictGetWorkflow()")))
        if not any(dictStep["sName"] == S_AGENT_STEP
                   for dictStep in dictWorkflow["listSteps"]):
            dictWorkflow["listSteps"].append(dict(
                dictWorkflow["listSteps"][0], sName=S_AGENT_STEP,
                sDirectory="AgentAddedStep", sStepId="agent-added-step",
            ))
        dictStatus.update({
            "bWorkflowReloaded": True,
            "dictWorkflow": dictWorkflow,
            "iWorkflowEpoch": dictStatus["iWorkflowEpoch"] + 1,
            "sWorkflowReloadOrigin": "agent",
            "listResearcherNotices": [{
                "iNoticeId": 7, "sLevel": "info", "sMessage": S_NOTICE,
            }],
            "iNewestNoticeId": 7,
        })
        routeIntercepted.fulfill(
            status=200, content_type="application/json",
            body=json.dumps(dictStatus),
        )
    page.route(S_FILE_STATUS_GLOB, fnAnswer)


def _flistToastTexts(page):
    return page.evaluate(
        "() => Array.from(document.querySelectorAll('.toast'))"
        ".map((el) => el.textContent)"
    )


@pytest.mark.falsification
def testAnAgentsChangeIsShownAndNamedOnce(pageDashboard, serverHub):
    """The row appears, the notice is shown once, and the tab acknowledges it.

    Kills: announcing an agent's change as "reloaded from disk".
    """
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    listRequestUrls = []
    _fnStageTheAgentsPollAnswer(pageDashboard, listRequestUrls)
    pageDashboard.evaluate(_S_POLL_ONCE)
    pageDashboard.wait_for_selector(
        f'.step-name:text-is("{S_AGENT_STEP}")', timeout=10000,
    )
    pageDashboard.evaluate(_S_POLL_ONCE)
    listToasts = _flistToastTexts(pageDashboard)
    assert sum(S_NOTICE in sToast for sToast in listToasts) == 1, listToasts
    assert not any("reloaded from disk" in sToast for sToast in listToasts)
    assert "iLastNoticeId=7" in listRequestUrls[-1]
    assert pageDashboard.listPageErrors == []


def _fdictServedWorkflow(serverHub, sContainerId):
    return serverHub.app.state.dictRouteContext["workflows"][sContainerId]


def _fsPageCredential(page):
    return page.evaluate(
        "() => window.sessionStorage.getItem('vaibifySessionCredential')"
    )


def testAnOpenEditorNeverSavesOverTheWrongStep(pageDashboard, serverHub):
    """A step is inserted above the one being edited; the save is refused.

    The editor submits the fingerprint it opened with, so its index --
    stale once a step lands above it -- can never write into whichever
    step now sits there.
    """
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    sContainerId = pageDashboard.evaluate(
        "() => VaibifyApp.fsGetContainerId()")
    sEditedName = "Second Stage"
    pageDashboard.evaluate(
        "(sName) => VaibifyStepEditor.fnOpenEditModal("
        "VaibifyApp.fdictGetWorkflow().listSteps.findIndex("
        "(dictStep) => dictStep.sName === sName))",
        sEditedName,
    )
    pageDashboard.wait_for_selector("#modalStepEditor.active", timeout=10000)
    dictCommandsBefore = {
        dictStep["sName"]: list(dictStep.get("saDataCommands", []))
        for dictStep in _fdictServedWorkflow(
            serverHub, sContainerId)["listSteps"]
    }
    response = httpx.post(
        f"{serverHub.sBaseUrl}/api/steps/{sContainerId}/insert/0",
        json={"sName": "Inserted Above", "sDirectory": ""},
        headers={
            "X-Session-Token": _fsPageCredential(pageDashboard),
            "X-Vaibify-Lease": pageDashboard.evaluate(
                "() => VaibifyApp.fsGetLeaseId ? "
                "VaibifyApp.fsGetLeaseId() : ''"),
        },
        timeout=30.0,
    )
    assert response.status_code == 200, response.text
    pageDashboard.fill("#inputSetupCommands", "python3 overwritten.py")
    pageDashboard.click("#btnModalSave")
    pageDashboard.wait_for_function(
        "() => Array.from(document.querySelectorAll('.toast'))"
        ".some((el) => el.textContent.includes("
        "'The project changed since you loaded it'))",
        timeout=15000,
    )
    for dictStep in _fdictServedWorkflow(
        serverHub, sContainerId,
    )["listSteps"]:
        assert dictStep.get("saDataCommands", []) == dictCommandsBefore.get(
            dictStep["sName"], [],
        ), dictStep["sName"]


def testAPromotedSandboxShowsAStepWrittenOnDisk(pageDashboard, serverHub):
    """Convert to Project, then a step written into project.json appears."""
    from tests.browser.testPromoteToHostProjectJourney import (
        _fnOpenPromoteWizardAndChooseHost,
        _fnSeedAPromotableSandbox,
        _fnWaitForPicker,
    )
    sSandbox = "agentEditSandbox"
    _fnSeedAPromotableSandbox(serverHub, sSandbox)
    _fnWaitForPicker(pageDashboard, serverHub)
    pageDashboard.wait_for_selector(
        f'.container-tile[data-name="{sSandbox}"]', timeout=10000,
    )
    _fnOpenPromoteWizardAndChooseHost(pageDashboard, sSandbox)
    pageDashboard.fill("#inputWizardProjectName", "Agent Edit Project")
    pageDashboard.click("#btnWizardNext")
    pageDashboard.wait_for_timeout(200)
    pageDashboard.click("#btnWizardNext")
    pageDashboard.wait_for_selector("#mainLayout.active", timeout=30000)
    sContainerId, sWorkflowPath = _ftWaitForTheOpenWorkflow(pageDashboard)
    with open(sWorkflowPath) as fileWorkflow:
        dictWorkflow = json.load(fileWorkflow)
    dictWorkflow.setdefault("listSteps", []).append({
        "sName": "Written On Disk", "sStepId": "written-on-disk",
        "sDirectory": "WrittenOnDisk", "bRunEnabled": True,
        "bPlotOnly": False, "saDataCommands": ["python3 make.py"],
        "saOutputDataFiles": [], "saPlotCommands": [], "saPlotFiles": [],
    })
    with open(sWorkflowPath, "w") as fileWorkflow:
        json.dump(dictWorkflow, fileWorkflow, indent=2)
    os.utime(os.path.dirname(sWorkflowPath))
    pageDashboard.wait_for_selector(
        '.step-name:text-is("Written On Disk")', timeout=60000,
    )
    assert sContainerId
    assert pageDashboard.listPageErrors == []


def _ftWaitForTheOpenWorkflow(page):
    """Return the open project's resource id and workflow path."""
    fDeadline = time.monotonic() + 30.0
    while time.monotonic() < fDeadline:
        tOpen = page.evaluate(
            "() => [VaibifyApp.fsGetContainerId(), "
            "VaibifyApp.fsGetWorkflowPath ? VaibifyApp.fsGetWorkflowPath()"
            " : '']")
        if tOpen[0] and tOpen[1]:
            return tOpen[0], tOpen[1]
        page.wait_for_timeout(200)
    raise AssertionError("the promoted project never opened a workflow")
