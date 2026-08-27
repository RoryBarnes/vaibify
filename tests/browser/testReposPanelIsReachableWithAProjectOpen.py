"""The Repos tab is reachable in the mode that points at it.

``DICT_MODE_WORKFLOW`` listed ``["steps", "proof", "files", "logs"]``
and ``DICT_MODE_NO_WORKFLOW`` listed ``["files", "repos", "logs"]``,
so the Repos panel was visible only while NO project was open --
which is the one situation in which nobody is being told to go there.

Four researcher-facing pointers render only WITH a project open and
all four name that panel: the PROOF tab's Level 2 "GitHub mirror" and
"Zenodo deposit" rows, whose fix link is labelled "Open the Repos
panel", and the Project block's two published-copies hints ("Push and
re-verify from the Repos panel"). A researcher following any of them
found no such tab.

The fix link half-worked, which is why this survived: it calls
``.click()`` on the tab element, and a programmatic click fires the
handler even on a ``display: none`` element, so the panel opened with
no tab in the strip to return to or to leave from. That is the shape
of bug a test asserting only "the fix link opens the panel" would
have passed -- so this asserts the TAB is visible, which is what a
researcher actually needs in order to find it unaided.

Kills (confirmed, not assumed): removing "repos" from
``DICT_MODE_WORKFLOW.listLeftTabs`` -> the first assertion fails
reporting the visible tab set without it.
"""

import pytest

from tests.browser.conftest import (
    S_HOST_PROJECT_READY,
    S_HOST_STEP_NAME,
    S_HOST_WORKFLOW_NAME,
)


pytestmark = pytest.mark.browser

S_VISIBLE_TABS_SCRIPT = (
    "() => Array.from(document.querySelectorAll('.left-tab'))"
    ".filter(el => getComputedStyle(el).display !== 'none')"
    ".map(el => el.dataset.panel)"
)


def test_the_repos_tab_is_visible_once_a_project_is_open(
    pageDashboard, serverHub,
):
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
    pageDashboard.click(f"text={S_HOST_WORKFLOW_NAME}")
    pageDashboard.wait_for_selector(
        f"text={S_HOST_STEP_NAME}", timeout=20000,
    )

    listVisible = pageDashboard.evaluate(S_VISIBLE_TABS_SCRIPT)
    assert "repos" in listVisible, (
        "with a project open the dashboard tells the researcher to "
        "'Open the Repos panel' from the PROOF tab and the Project "
        f"block, but the visible tabs are {listVisible}"
    )

    # Reachable by CLICKING it, not only by the deep link. A hidden
    # tab already responded to a programmatic click, so activating
    # the panel proves nothing on its own.
    pageDashboard.click('.left-tab[data-panel="repos"]')
    assert pageDashboard.evaluate(
        "() => document.getElementById('panelRepos')"
        ".classList.contains('active')",
    ) is True, "clicking the Repos tab did not activate its panel"

    # The other tabs survive: this widened the set, it did not
    # replace it.
    for sTab in ("steps", "proof", "files", "logs"):
        assert sTab in listVisible, (
            f"the {sTab} tab disappeared: {listVisible}"
        )

    assert pageDashboard.listPageErrors == []
