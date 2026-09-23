"""A run this browser did not start moves the step lights as it goes.

A researcher watched an in-container agent run a workflow step by step,
2026-09-23. Each step's light began pulsing orange when its step
started -- and kept pulsing after the step finished, so the lights
accumulated: two, then three, then every step the run had passed
through, all pulsing at once. When the run ended the pulsing stopped,
and no step turned blue. The dashboard learns about such a run only
from its continuous status poll, which carried nothing but the active
step's number: nothing released the step before it, and nothing said
how any step had ended.

The run is staged here by rewriting the run state on the real status
poll's answer, so the page's own poll handler, render and stylesheet
are what is exercised. The poll is fired explicitly after each stage
rather than waited for, because the claim is about what one answer
does to the lights, not about the polling interval.
"""

import json

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow


pytestmark = pytest.mark.browser


# The step list repaints on the next animation frame, not inside the
# poll handler, so the lights are read only after two frames have
# passed; reading sooner reports the previous answer's lights.
_S_POLL_ONCE = """async () => {
    await VaibifyPolling.fnPollFileStatusOnce(VaibifyApp.fsGetContainerId());
    await new Promise(fnResolve => requestAnimationFrame(
        () => requestAnimationFrame(fnResolve)));
}"""

# Every step light's class list, by step index, plus which of them are
# actually animating: a class with no stylesheet rule behind it would
# satisfy a class-only assertion while nothing on screen pulsed.
_S_READ_LIGHTS = """() => {
    const dictLights = {};
    document.querySelectorAll('.step-item[data-index]').forEach(el => {
        const elLight = el.querySelector('.step-status');
        if (!elLight) return;
        dictLights[el.getAttribute('data-index')] = {
            sClass: elLight.className,
            bPulsing: getComputedStyle(elLight).animationName !== 'none',
        };
    });
    return dictLights;
}"""

_S_CONDITIONAL_HEADERS = ("if-none-match", "if-modified-since")


def _fnStageRunStateOnThePoll(pageDashboard, dictStage):
    """Route the status poll so it answers with ``dictStage["current"]``."""
    def fnAnswer(routeIntercepted):
        dictHeaders = {
            sKey: sValue
            for sKey, sValue in routeIntercepted.request.headers.items()
            if sKey.lower() not in _S_CONDITIONAL_HEADERS
        }
        response = routeIntercepted.fetch(headers=dictHeaders)
        dictStatus = json.loads(response.text())
        if dictStage["current"] is not None:
            dictStatus["dictRunState"] = dictStage["current"]
        routeIntercepted.fulfill(
            status=200, content_type="application/json",
            body=json.dumps(dictStatus),
        )
    pageDashboard.route("**/api/pipeline/*/file-status*", fnAnswer)


def _fdictRunState(bRunning, iActiveStep, dictStepResults):
    """Return a run state as the status poll carries it."""
    return {
        "bRunning": bRunning,
        "iActiveStep": iActiveStep,
        "dictStepResults": dictStepResults,
        "bActiveStepOverBudget": False,
        "fActiveStepElapsedSeconds": 0.0,
        "fActiveStepBudgetSeconds": 0.0,
    }


def _fdictLightsAfter(pageDashboard, dictStage, dictRunState):
    """Stage one poll answer, apply it, and return the lights it left."""
    dictStage["current"] = dictRunState
    pageDashboard.evaluate(_S_POLL_ONCE)
    return pageDashboard.evaluate(_S_READ_LIGHTS)


def _flistPulsing(dictLights):
    """Return the step indices whose light is animating."""
    return sorted(
        sIndex for sIndex, dictLight in dictLights.items()
        if dictLight["bPulsing"]
    )


@pytest.mark.falsification
def test_an_agents_run_releases_each_step_and_says_how_it_ended(
    pageDashboard, serverHub,
):
    """Kills: leaving the step the run moved past marked running.

    Run on the seeded workflow's two runnable steps: step 1 runs, then
    step 2 runs after step 1 passed, then the run ends with step 2
    failed. At every moment exactly the active step pulses, and each
    finished step wears its own verdict -- a failed step is not blue,
    which is what separates reading the verdicts from painting every
    finished step as passed.
    """
    dictStage = {"current": None}
    _fnStageRunStateOnThePoll(pageDashboard, dictStage)
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)

    dictLights = _fdictLightsAfter(
        pageDashboard, dictStage, _fdictRunState(True, 1, {}),
    )
    assert _flistPulsing(dictLights) == ["0"], dictLights

    # The result for step 1 has not landed yet (results are written on
    # a debounce, the next step's start is not). The light must still
    # be released: only the active step is running.
    dictLights = _fdictLightsAfter(
        pageDashboard, dictStage, _fdictRunState(True, 2, {}),
    )
    assert _flistPulsing(dictLights) == ["1"], (
        "a step the run had moved past was still pulsing: "
        f"{dictLights}"
    )

    dictLights = _fdictLightsAfter(
        pageDashboard, dictStage, _fdictRunState(True, 2, {
            "1": {"sStatus": "passed", "iExitCode": 0},
        }),
    )
    assert _flistPulsing(dictLights) == ["1"], dictLights
    assert "pass" in dictLights["0"]["sClass"].split(), (
        "the step the agent's run passed does not say so while the "
        f"run continues: {dictLights['0']}"
    )

    dictLights = _fdictLightsAfter(
        pageDashboard, dictStage, _fdictRunState(False, 2, {
            "1": {"sStatus": "passed", "iExitCode": 0},
            "2": {"sStatus": "failed", "iExitCode": 1},
        }),
    )
    assert _flistPulsing(dictLights) == [], (
        f"a light kept pulsing after the run ended: {dictLights}"
    )
    assert "pass" in dictLights["0"]["sClass"].split(), dictLights
    assert "fail" in dictLights["1"]["sClass"].split(), (
        "the finished run did not say its last step failed: "
        f"{dictLights['1']}"
    )
    assert pageDashboard.listPageErrors == []
