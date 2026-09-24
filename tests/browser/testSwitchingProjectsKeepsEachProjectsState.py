"""Switching projects leaves each project's dashboard state its own.

A container can host several projects, and a researcher ran two of
them while switching the dashboard between them (2026-09-24). The page
kept listening to the project it had left: its pipeline socket stayed
open and delivered that project's run events -- step numbers that
index ANOTHER step list -- onto the new project's lights, and a poll
answer computed before the switch was applied to the project after it.
Nothing on the page said the container was busy with the other
project's run.

The answers are staged on the real poll through the page's own
handlers, and the switch is made through the real project switcher on a
host project holding two workflows.
"""

import json
import os

import pytest

from tests.browser.conftest import (
    S_HOST_PROJECT_READY,
    fdictHostWorkflowDocument,
    fnOpenTheSeededHostWorkflow,
)


pytestmark = pytest.mark.browser


_S_POLL_ONCE = """async () => {
    await VaibifyPolling.fnPollFileStatusOnce(VaibifyApp.fsGetContainerId());
    await new Promise(fnResolve => requestAnimationFrame(
        () => requestAnimationFrame(fnResolve)));
}"""

_S_READ_PULSING = """() => {
    const listPulsing = [];
    document.querySelectorAll('.step-item[data-index]').forEach(el => {
        const elLight = el.querySelector('.step-status');
        if (elLight && getComputedStyle(elLight).animationName !== 'none') {
            listPulsing.push(el.getAttribute('data-index'));
        }
    });
    return listPulsing.sort();
}"""

_S_CONDITIONAL_HEADERS = ("if-none-match", "if-modified-since")

_S_OTHER_WORKFLOW_PATH = "/elsewhere/otherProject/.vaibify/projects/other.json"


def _fnStagePollOverrides(pageDashboard, dictStage):
    """Route the status poll so ``dictStage["current"]`` overrides its keys."""
    def fnAnswer(routeIntercepted):
        dictHeaders = {
            sKey: sValue
            for sKey, sValue in routeIntercepted.request.headers.items()
            if sKey.lower() not in _S_CONDITIONAL_HEADERS
        }
        response = routeIntercepted.fetch(headers=dictHeaders)
        dictStatus = json.loads(response.text())
        dictStatus.update(dictStage["current"] or {})
        routeIntercepted.fulfill(
            status=200, content_type="application/json",
            body=json.dumps(dictStatus),
        )
    pageDashboard.route("**/api/pipeline/*/file-status*", fnAnswer)


def _fnReleaseStagedPoll(pageDashboard):
    """Drop the route so a poll in flight at teardown cannot error it."""
    pageDashboard.unroute_all(behavior="ignoreErrors")


def _fdictRunningFirstStep():
    return {
        "bRunning": True, "iActiveStep": 1, "dictStepResults": {},
        "bActiveStepOverBudget": False,
        "fActiveStepElapsedSeconds": 0.0, "fActiveStepBudgetSeconds": 0.0,
    }


@pytest.mark.falsification
def test_an_answer_about_another_project_moves_no_lights(
    pageDashboard, serverHub,
):
    """Kills: applying a poll answer without asking which project it describes.

    The control applies the SAME run state labelled as this project's
    and sees the first step pulse, so a quiet page is the guard working
    and not a staging that never reached the lights.
    """
    dictStage = {"current": None}
    _fnStagePollOverrides(pageDashboard, dictStage)
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    sOpenPath = pageDashboard.evaluate("() => VaibifyApp.fsGetWorkflowPath()")

    dictStage["current"] = {
        "sServedWorkflowPath": _S_OTHER_WORKFLOW_PATH,
        "dictRunState": _fdictRunningFirstStep(),
    }
    pageDashboard.evaluate(_S_POLL_ONCE)
    assert pageDashboard.evaluate(_S_READ_PULSING) == [], (
        "another project's run lit this project's step"
    )

    dictStage["current"] = {
        "sServedWorkflowPath": sOpenPath,
        "dictRunState": _fdictRunningFirstStep(),
    }
    pageDashboard.evaluate(_S_POLL_ONCE)
    assert pageDashboard.evaluate(_S_READ_PULSING) == ["0"]
    assert pageDashboard.listPageErrors == []
    _fnReleaseStagedPoll(pageDashboard)


@pytest.mark.falsification
def test_another_projects_run_is_announced(pageDashboard, serverHub):
    """Kills: dropping the other project's run from the page."""
    dictStage = {"current": None}
    _fnStagePollOverrides(pageDashboard, dictStage)
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)

    dictStage["current"] = {"dictOtherProjectRun": {
        "sWorkflowPath": _S_OTHER_WORKFLOW_PATH,
        "sWorkflowName": "Other Project",
        "sProjectRepoPath": "/elsewhere/otherProject",
    }}
    pageDashboard.evaluate(_S_POLL_ONCE)
    elBadge = pageDashboard.locator("#otherProjectRunBadge")
    assert elBadge.is_visible()
    assert "Other Project" in elBadge.inner_text()

    dictStage["current"] = {"dictOtherProjectRun": {}}
    pageDashboard.evaluate(_S_POLL_ONCE)
    assert not elBadge.is_visible(), (
        "the badge outlived the other project's run"
    )
    assert pageDashboard.listPageErrors == []
    _fnReleaseStagedPoll(pageDashboard)


S_SECOND_WORKFLOW_NAME = "secondLaneProject"
S_SECOND_STEP_NAME = "SecondProjectStep"


def _fsWriteSecondWorkflow(serverHub):
    """Add a second project document beside the seeded one."""
    dictDocument = fdictHostWorkflowDocument()
    dictDocument["sWorkflowName"] = S_SECOND_WORKFLOW_NAME
    dictDocument["listSteps"] = [dict(
        dictDocument["listSteps"][1],
        sName=S_SECOND_STEP_NAME, sStepId="second-project-step",
        sDirectory="SecondStage",
    )]
    sPath = os.path.join(
        serverHub.sHome, S_HOST_PROJECT_READY, ".vaibify", "projects",
        S_SECOND_WORKFLOW_NAME + ".json",
    )
    with open(sPath, "w") as fileWorkflow:
        json.dump(dictDocument, fileWorkflow)
    return sPath


@pytest.mark.falsification
def test_switching_projects_closes_the_left_projects_socket(
    pageDashboard, serverHub,
):
    """Kills: keeping the pipeline socket open across a project switch.

    That socket is what carried the left project's run events onto the
    opened project's step list. The socket is opened first and proven
    open, so a closed one afterwards is the switch's doing.
    """
    sSecondPath = _fsWriteSecondWorkflow(serverHub)
    try:
        fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
        pageDashboard.evaluate("""() => VaibifyWebSocket.fnConnect(
            VaibifyApp.fsGetContainerId(), VaibifyApp.fsGetSessionToken())""")
        pageDashboard.wait_for_function(
            "() => VaibifyWebSocket.fbIsOpen()", timeout=10000,
        )
        pageDashboard.click("#activeWorkflowName")
        pageDashboard.click(
            f'.workflow-dropdown-item[data-name="{S_SECOND_WORKFLOW_NAME}"]',
        )
        pageDashboard.wait_for_selector("#modalConfirm", timeout=10000)
        pageDashboard.click("#btnConfirmOk")
        pageDashboard.wait_for_selector(
            f"text={S_SECOND_STEP_NAME}", timeout=20000,
        )
        assert pageDashboard.evaluate(
            "() => VaibifyApp.fsGetWorkflowPath()",
        ).endswith(S_SECOND_WORKFLOW_NAME + ".json")
        assert pageDashboard.evaluate(
            "() => !VaibifyWebSocket.fbIsOpen()",
        ) is True, "the left project's pipeline socket is still open"
        assert pageDashboard.listPageErrors == []
    finally:
        os.remove(sSecondPath)
