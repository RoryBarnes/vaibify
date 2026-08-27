"""A badge in the Project block opens the same menu as one in a step.

The Project block lists the same files with the same octocat badges as
a Step Viewer, and for as long as those rows existed the badges there
were inert: the global ``.remote-badge`` handler reads ``data-resolved``
off the enclosing ``.detail-item`` and returns early when it is absent,
and the Project block's rows carried only ``data-path``. So a
researcher looking at an orange octocat beside
``.vaibify/projects/<name>.json`` -- a file the Level 2 gate is
blocking on -- had no way to act on it, while the identical badge one
panel over offered a push.

The click is DRIVEN, not the attribute asserted. ``data-resolved``
being present proves nothing: the handler could still bail on the
remote key, the picklist could fail to build for a path with no step
context, or the menu could open empty. Only opening it shows the
researcher gets something they can choose.

Kills (confirmed, not assumed): removing ``data-resolved`` from
_fsRenderFileRowWithBadges fails the attribute assertion, which is the
FIRST one it reaches -- not the menu-visibility assertion further down.
Both are kept: the attribute one localises the break to the renderer,
and the click one is the only thing that shows the researcher ends up
with choices rather than an empty box.
"""

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow


pytestmark = pytest.mark.browser




@pytest.mark.falsification
def test_clicking_a_project_block_badge_opens_its_picklist(
    pageDashboard, serverHub,
):
    """A Project-block badge opens the same menu as one in a step.

    Kills: drop the data-resolved attribute from
    _fsRenderFileRowWithBadges, after which the global .remote-badge
    handler bails on the enclosing row and the menu never opens.
    """
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub, bAwaitProjectBlock=True)

    # Open the Level 2 published-copies section and its GitHub row so
    # the file rows render.
    pageDashboard.click(
        '.requirement-group-header[data-group="publishedCopies"]',
    )
    pageDashboard.wait_for_selector(
        ".requirement-row-header", timeout=10000,
    )
    iRows = pageDashboard.locator(".requirement-row-header").count()
    for iIndex in range(iRows):
        pageDashboard.locator(
            ".requirement-row-header",
        ).nth(iIndex).click()

    elBadge = pageDashboard.locator(
        '.requirement-group .detail-item.tracked-file '
        '.remote-badge[data-remote="sGithub"]',
    ).first
    elBadge.wait_for(state="visible", timeout=10000)

    # The row must carry what the handler reads, and the path must be
    # the repo-relative form the push sends to `git add`.
    sResolved = pageDashboard.evaluate(
        """() => {
            const el = document.querySelector(
                '.requirement-group .detail-item.tracked-file');
            return el ? (el.dataset.resolved || '') : '(no row)';
        }"""
    )
    assert sResolved and not sResolved.startswith("/"), (
        "the Project block file row carries no usable repo-relative "
        f"data-resolved, so the badge handler bails: {sResolved!r}"
    )

    elBadge.click()

    elMenu = pageDashboard.locator("#remotePicklistMenu")
    elMenu.wait_for(state="visible", timeout=5000)
    iItems = pageDashboard.locator(
        "#remotePicklistMenu .picklist-item",
    ).count()
    assert iItems > 0, (
        "the badge opened an EMPTY menu — the researcher can see the "
        "problem and still cannot act on it"
    )

    assert pageDashboard.listPageErrors == []
