"""A terminal closed in the task that opened it raises no page error.

xterm 5.5's viewport queues a bare ``setTimeout`` scroll-area sync when
a terminal opens, and that sync reads the renderer ``dispose()`` drops.
A pane opened and closed before the timer fired threw an uncaught
TypeError into the researcher's console. The browser lane met it as a
reload that restored the stored layout (WebKit, 2026-11-12): opening the
project made a pane, and the restore's ``fnCloseAll`` closed it in the
same task whenever the stored record answered fast enough. The test
calls those same two functions in one task, so the race is certain
rather than a matter of timing.

Kills: disposing the terminal at once, so the sync open() queued finds
its renderer gone.
"""

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow


pytestmark = [pytest.mark.browser, pytest.mark.falsification]


def test_a_terminal_closed_as_it_opens_raises_no_error(
    pageDashboard, serverHub,
):
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    pageDashboard.wait_for_selector(".xterm", timeout=20000)
    assert pageDashboard.listPageErrors == []
    pageDashboard.evaluate("""() => {
        VaibifyTerminal.fnCreatePane();
        VaibifyTerminal.fnCloseAll();
    }""")
    assert pageDashboard.query_selector(".xterm") is None, (
        "a closed terminal stays on the page until it is disposed")
    pageDashboard.evaluate(
        "() => new Promise(fnResolve => setTimeout(fnResolve, 250))")
    assert pageDashboard.listPageErrors == []
