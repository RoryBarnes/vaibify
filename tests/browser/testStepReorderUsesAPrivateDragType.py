"""Only a drag that started on a step can reorder steps.

The drop handler read ``text/plain``, a type every text selection on the
page carries, so dropping a selected "3" from anywhere onto a step moved
a step. It also parsed whatever arrived without asking whether a step
with that index exists, and called ``event.target.closest`` on targets
that Firefox and WebKit report as Text nodes, which throws. The drag now
carries a private type, the drop validates the index it carries, and the
target is resolved from the element around a Text node.

A real drag is driven for the positive case; the negative cases build
the drop event directly, because a real drag cannot be made to carry
the wrong payload.
"""

import json

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow

pytestmark = pytest.mark.browser

S_REORDER_GLOB = "**/api/steps/*/reorder"

_S_DROP_WITH_PAYLOAD = """([sTargetSelector, dictPayload, bOnTextNode]) => {
    const elStep = document.querySelector(sTargetSelector);
    let nodeTarget = elStep;
    if (bOnTextNode) {
        const walker = document.createTreeWalker(
            elStep, NodeFilter.SHOW_TEXT,
            {acceptNode: (n) => n.textContent.trim()
                ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT});
        nodeTarget = walker.nextNode();
    }
    const dataTransfer = new DataTransfer();
    for (const [sType, sValue] of Object.entries(dictPayload)) {
        dataTransfer.setData(sType, sValue);
    }
    nodeTarget.dispatchEvent(new DragEvent('drop', {
        bubbles: true, cancelable: true, dataTransfer: dataTransfer}));
}"""


@pytest.fixture
def listReorderRequests(pageDashboard, serverHub):
    """Open the workflow and record every reorder request, refusing each."""
    listBodies = []

    def fnRecord(route):
        listBodies.append(json.loads(route.request.post_data or "{}"))
        route.fulfill(
            status=500, content_type="application/json",
            body='{"detail": "refused by the test"}',
        )

    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    pageDashboard.route(S_REORDER_GLOB, fnRecord)
    pageDashboard.wait_for_selector(".step-item", timeout=15000)
    return listBodies


@pytest.mark.falsification
def testDroppingPlainTextOnAStepDoesNotReorderIt(
    pageDashboard, listReorderRequests,
):
    """Kills: reading ``text/plain`` in the drop, which every text
    selection carries."""
    pageDashboard.evaluate(
        _S_DROP_WITH_PAYLOAD,
        ['.step-item[data-index="0"]', {"text/plain": "1"}, False],
    )
    pageDashboard.wait_for_timeout(500)
    assert listReorderRequests == []
    assert pageDashboard.listPageErrors == []


@pytest.mark.falsification
def testADragCarryingAnIndexOfNoStepDoesNotReorderAnything(
    pageDashboard, listReorderRequests,
):
    """Kills: forwarding whatever index the payload carries."""
    for sCarried in ("99", "-1", "1.5", "abc", ""):
        pageDashboard.evaluate(
            _S_DROP_WITH_PAYLOAD,
            ['.step-item[data-index="0"]',
             {"vaibify/step": sCarried}, False],
        )
    pageDashboard.wait_for_timeout(500)
    assert listReorderRequests == []
    assert pageDashboard.listPageErrors == []


@pytest.mark.falsification
def testADropOntoATextNodeStillResolvesItsStep(
    pageDashboard, listReorderRequests,
):
    """Kills: calling ``closest`` on the raw event target, which throws
    when the target is a Text node."""
    pageDashboard.evaluate(
        _S_DROP_WITH_PAYLOAD,
        ['.step-item[data-index="0"]', {"vaibify/step": "1"}, True],
    )
    pageDashboard.wait_for_timeout(500)
    assert listReorderRequests == [{"iFromIndex": 1, "iToIndex": 0}]
    assert pageDashboard.listPageErrors == []


@pytest.mark.falsification
def testARealDragOfAStepAsksForTheReorder(
    pageDashboard, listReorderRequests,
):
    """Kills: a drag that no longer carries the private type."""
    pageDashboard.drag_and_drop(
        '.step-item[data-index="1"]', '.step-item[data-index="0"]',
    )
    pageDashboard.wait_for_timeout(1000)
    assert listReorderRequests == [{"iFromIndex": 1, "iToIndex": 0}]
    assert pageDashboard.listPageErrors == []
