"""Stop All Tasks, in a real browser, in both modes.

The backend refuses to signal a run it cannot identify, and reports
those refusals. Neither fact reaches a researcher unless the dashboard
says so, and a green Python suite says nothing about whether it does.

Two things are driven here, both through the real modules:

* **The confirmation copy.** It has always said the processes will be
  killed "in the container". On a host project there is no container
  and the signals land on the researcher's own machine — the mode
  being invisible at exactly the moment it decides what a click does.
* **The refusal report.** The server's ``listCancellationRefusals``
  is the only thing separating "your machine is quiet" from "vaibify
  declined to touch a run it could not identify". Dropped from the
  toast, the second reads as the first.

The kill POST is intercepted rather than served, because what is under
test is the dashboard's handling of an answer, not the route that
produces it — that half is driven end to end, against real processes,
in ``tests/testHostCancel.py``.
"""

import json

import pytest


pytestmark = pytest.mark.browser

S_KILL_ROUTE_GLOB = "**/api/pipeline/*/kill"


def _fnOpenTheKillConfirmation(page, serverHub, sProjectMode):
    """Load the dashboard, declare the mode, open the confirmation."""
    page.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    page.wait_for_selector(".container-tile", timeout=10000)
    page.evaluate(
        "(sMode) => VaibifyApp.fnApplyProjectMode(sMode)", sProjectMode,
    )
    page.evaluate("() => VaibifyPipelineRunner.fnKillPipeline()")
    page.wait_for_selector("#modalConfirm", timeout=5000)


def testTheHostConfirmationNeverSaysTheProcessesAreInAContainer(
    pageDashboard, serverHub,
):
    """A host project is told where its processes actually are.

    Kills: dropping the mode branch from the confirmation body, which
    tells a researcher about to stop work on their own machine that
    the kill is contained.
    """
    _fnOpenTheKillConfirmation(pageDashboard, serverHub, "host")
    sBody = pageDashboard.text_content("#modalConfirm p")
    assert "in the container" not in sBody, (
        f"a host project was told its processes are contained: {sBody}"
    )
    assert "on this machine" in sBody, sBody
    assert "detached into its own session" in sBody, (
        "the confirmation claims more than host mode can prove: "
        f"{sBody}"
    )
    pageDashboard.click("#btnConfirmCancel")
    assert pageDashboard.listPageErrors == []


def testTheContainerConfirmationIsUnchanged(pageDashboard, serverHub):
    """The other direction: a containerized project keeps its copy.

    Kills: making the host wording unconditional, which would tell
    every containerized researcher that vaibify cannot contain their
    run — the false alarm that trains people to ignore the real one.
    """
    _fnOpenTheKillConfirmation(pageDashboard, serverHub, "container")
    sBody = pageDashboard.text_content("#modalConfirm p")
    assert "in the container" in sBody, sBody
    assert "on this machine" not in sBody, sBody
    pageDashboard.click("#btnConfirmCancel")
    assert pageDashboard.listPageErrors == []


def _fnAnswerTheKillWith(page, dictBody):
    """Intercept the kill POST and answer it with a canned payload."""
    page.route(
        S_KILL_ROUTE_GLOB,
        lambda routeIntercepted: routeIntercepted.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(dictBody),
        ),
    )


def testARefusedCancellationIsShownAndNotRoundedDownToZero(
    pageDashboard, serverHub,
):
    """The researcher hears about the run vaibify would not touch.

    Kills: reporting only ``iProcessesKilled``, under which a Cancel
    that declined to signal anything is indistinguishable from a
    Cancel that found nothing to signal.
    """
    _fnAnswerTheKillWith(pageDashboard, {
        "bSuccess": True,
        "iProcessesKilled": 0,
        "bTaskCancelled": False,
        "listCancellationRefusals": [{
            "sOperationLabel": "pipeline-step:A01",
            "sReason": "run 'vaibify reconcile' to settle this record",
        }],
    })
    _fnOpenTheKillConfirmation(pageDashboard, serverHub, "host")
    pageDashboard.click("#btnConfirmOk")
    pageDashboard.wait_for_selector("text=vaibify reconcile", timeout=5000)
    assert pageDashboard.listPageErrors == []


def testACleanCancellationRaisesNoAlarm(pageDashboard, serverHub):
    """The other direction: nothing refused, nothing to warn about.

    Kills: showing the reconcile warning unconditionally, which would
    send every researcher to a reconciliation they do not need.
    """
    _fnAnswerTheKillWith(pageDashboard, {
        "bSuccess": True,
        "iProcessesKilled": 2,
        "bTaskCancelled": True,
        "listCancellationRefusals": [],
    })
    _fnOpenTheKillConfirmation(pageDashboard, serverHub, "host")
    pageDashboard.click("#btnConfirmOk")
    pageDashboard.wait_for_selector("text=Killed 2 process(es)", timeout=5000)
    assert "reconcile" not in pageDashboard.content(), (
        "a clean cancellation sent the researcher to reconciliation"
    )
    assert pageDashboard.listPageErrors == []
