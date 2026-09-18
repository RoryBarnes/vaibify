"""A poll must not destroy the card the researcher is about to click.

The workflow hub poll refreshes the picker every three seconds, and
the render replaced the list wholesale with innerHTML -- destroying
every card and rebinding the handlers. A click that resolved a card an
instant before a tick landed on a node no longer in the document and
did nothing, so the researcher clicked a project and the picker sat
there.

It is rare per click and certain in aggregate. Measured on CI across
the three engine lanes: roughly one click in three hundred was
swallowed on Firefox and WebKit, and never on Chromium, which
dispatches faster than the rebuild window -- which is why the
single-engine lane could not see it. It surfaced as two timeouts per
Firefox lane, on DIFFERENT tests every run, because any test that
opens a project rolls the same dice.

This asserts the CARD SURVIVES rather than that a click lands, because
a test that clicked and checked the result would reproduce the defect
only at that same one-in-three-hundred rate. Identity is checkable
every run: a marker written onto the live element is gone if and only
if the element was replaced.

The wait is ``wait_for_timeout`` and NOT ``time.sleep``, which is not
a style preference. Playwright's sync API dispatches events only while
the Python thread is inside a Playwright call, so a bare ``time.sleep``
leaves request events undelivered and the tick count below reads zero.
That is a control which fails in the direction of passing: it reports
that the poll never ran, which is exactly what this test would see if
the defect were fixed, and it cost one wrong diagnosis before it was
understood.
"""

import pytest

from tests.browser.conftest import (
    S_HOST_PROJECT_READY,
    S_HOST_WORKFLOW_NAME,
)


pytestmark = pytest.mark.browser

# The hub poll runs on a 3s interval, so this spans at least two ticks.
I_SPAN_SEVERAL_POLL_TICKS_MILLISECONDS = 8000

S_MARK_THE_LIVE_CARD = """() => {
    const el = document.querySelector('#listWorkflows .container-card');
    if (!el) { return false; }
    el.dataset.markedBeforeThePoll = 'yes';
    return true;
}"""

S_READ_THE_MARK_BACK = """() => {
    const el = document.querySelector('#listWorkflows .container-card');
    return Boolean(el && el.dataset.markedBeforeThePoll === 'yes');
}"""


@pytest.mark.falsification
def test_the_picker_keeps_its_cards_across_a_poll(
    pageDashboard, serverHub,
):
    """Mark a live card, let the poll tick, and look for it again.

    Kills: dropping the identical-repaint guard from
    `fnRenderWorkflowList`, so the hub poll rebuilds the list with
    innerHTML and the marked card is replaced by an equal-looking one.
    """
    listWorkflowRequests = []
    pageDashboard.on("request", lambda request: (
        listWorkflowRequests.append(request.url)
        if "/api/workflows/" in request.url else None
    ))

    pageDashboard.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    pageDashboard.wait_for_selector(
        f'.container-tile[data-name="{S_HOST_PROJECT_READY}"]',
        timeout=15000,
    )
    pageDashboard.click(
        f'.container-tile[data-name="{S_HOST_PROJECT_READY}"] '
        '.container-tile-main',
    )
    pageDashboard.wait_for_selector("#modalConfirm", timeout=10000)
    pageDashboard.click("#btnConfirmOk")
    pageDashboard.wait_for_selector(
        f"text={S_HOST_WORKFLOW_NAME}", timeout=20000,
    )

    assert pageDashboard.evaluate(S_MARK_THE_LIVE_CARD), (
        "the picker rendered no workflow card, so there was nothing to "
        "mark and nothing below is measurable"
    )
    iRequestsBefore = len(listWorkflowRequests)
    pageDashboard.wait_for_timeout(
        I_SPAN_SEVERAL_POLL_TICKS_MILLISECONDS,
    )

    # The control: without a tick nothing could have rebuilt the list,
    # so the survival assertion below would pass against the defect.
    iTicks = len(listWorkflowRequests) - iRequestsBefore
    assert iTicks >= 1, (
        "the workflow hub poll never asked for the list during the "
        "wait, so this test proves nothing about what a tick does to "
        f"the cards; ticks={iTicks}"
    )

    assert pageDashboard.evaluate(S_READ_THE_MARK_BACK), (
        "the workflow card the researcher was about to click was "
        "replaced by the hub poll. A click resolved against the old "
        "element lands on a node that has left the document and does "
        "nothing, which is the picker appearing to ignore a click"
    )
