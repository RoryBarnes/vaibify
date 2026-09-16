"""The first paint of the Project block is the right one.

A researcher opened a project and watched it twice: five to ten seconds
to start, a Project block painted green with the "Do this next" arrow
on the Rebuild attestation row -- then five to ten seconds more, and
the real state arrived. Amber level cell, arrow moved to Artifacts.
They acted on the interim. Twice (researcher-reported, 2026-09-15).

The cause is structural rather than incidental, which is why a test
lives here. The Dependency-lock verdict costs a container exec; the
poll is forbidden to make one; so the verdict is measured by a
readiness GET the dashboard fires on open, recorded server-side, and
carried back by a LATER poll. Everything the block renders in between
-- the level strip, the ordering arrow, the row itself -- is derived
from an answer nobody has yet.

A provisional green is not a smaller error than a slow page. It is a
worse one, because it is the one that gets clicked. So the block waits
and says it is waiting, and the tempting alternative -- the remote
badges' pulse-while-asking -- is deliberately not borrowed: it trades
a wrong first paint for a two-stage one and still leaves an interim to
act on.

Asserted as an ORDER, never as a duration. The recording runs inside
the page: a MutationObserver notes each kind of Project-block paint,
and a fetch wrapper notes the moment the readiness answer arrives, into
one list. A wrong first paint puts a rows-paint before the answer, and
no clock is consulted to see it.
"""

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow


pytestmark = pytest.mark.browser


# Held INSIDE the page so the ordering is the page's own, not the
# harness's view of it. Two details are load-bearing. The readiness
# answer is DELAYED rather than blocked, because the block must paint
# eventually and a test that only proved it never paints would pass
# against a permanently blank one. And the observer is attached on
# DOMContentLoaded: an init script runs at document-start, where
# document.documentElement does not exist yet and observe() throws --
# silently, leaving the fetch half working and the recording half not.
_S_RECORD_PROJECT_BLOCK_PAINTS = """(function () {
    window.__listPaintOrder = [];
    const fnNote = function (sEvent) {
        const listOrder = window.__listPaintOrder;
        if (listOrder[listOrder.length - 1] !== sEvent) {
            listOrder.push(sEvent);
        }
    };
    const fnOriginalFetch = window.fetch;
    window.fetch = async function (resource, dictOptions) {
        const response = await fnOriginalFetch.call(
            this, resource, dictOptions);
        if (String(resource).indexOf("/level3/readiness") !== -1) {
            await new Promise(function (fnResolve) {
                setTimeout(fnResolve, 1500);
            });
            fnNote("readiness-answered");
        }
        return response;
    };
    document.addEventListener("DOMContentLoaded", function () {
        const observer = new MutationObserver(function () {
            const elBlock = document.getElementById("projectBlock");
            if (!elBlock || !elBlock.innerHTML) return;
            if (elBlock.querySelector(".project-block-waiting")) {
                fnNote("waiting");
            } else if (elBlock.querySelector(".requirement-group")) {
                fnNote("rows");
            }
        });
        observer.observe(document.documentElement, {
            childList: true, subtree: true,
        });
    });
})();"""


@pytest.mark.falsification
def test_the_project_block_waits_for_its_verdict_and_says_so(
    pageDashboard, serverHub,
):
    """ONE open, the whole sequence: the seeded project is leased.

    Kills: firing the open-time readiness check alongside the first
    paint instead of holding the block for it -- the shipped
    behaviour, and the one the researcher acted on. With the hold
    removed a rows-paint lands first, while the answer that decides
    the level strip and the arrow is still in flight.
    """
    pageDashboard.add_init_script(_S_RECORD_PROJECT_BLOCK_PAINTS)
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)

    # --- the wait announces itself. A silent hold is a hang, and a
    # researcher reloads a hang; the ruling is one wait WITH a
    # message. No level strip rides along, because a strip IS a
    # verdict and no verdict has been reached.
    elWaiting = pageDashboard.wait_for_selector(
        "#projectBlock .project-block-waiting", timeout=20000,
    )
    sNotice = elWaiting.text_content()
    assert "may take a moment" in sNotice, sNotice
    assert pageDashboard.query_selector(
        "#projectBlock .step-level-strip",
    ) is None, (
        "a level strip was painted over a project whose verdict has "
        "not been measured"
    )

    # --- and it ends when the answer lands, not before it.
    pageDashboard.wait_for_selector(
        "#projectBlock .requirement-group", timeout=30000,
    )
    assert pageDashboard.query_selector(
        "#projectBlock .project-block-waiting",
    ) is None, "the notice outlived the wait it describes"

    listOrder = pageDashboard.evaluate("() => window.__listPaintOrder")
    assert listOrder and "rows" in listOrder, listOrder
    assert "readiness-answered" in listOrder, (
        "the readiness answer never arrived, so this run proves "
        f"nothing about what was painted before it: {listOrder}"
    )
    assert listOrder.index("readiness-answered") < listOrder.index("rows"), (
        "the Project block painted requirement rows -- and with them a "
        "level strip and an ordering arrow -- before the answer those "
        "are derived from had arrived. That interim is the one the "
        f"researcher clicks: {listOrder}"
    )
