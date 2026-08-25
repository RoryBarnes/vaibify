"""Every level of the Project block says it can be opened.

All three nesting levels — the Project banner, each requirement group,
each requirement row — have always been click-to-expand, and none of
them showed it. The consequence was not cosmetic: a requirement whose
light is red renders as "you are blocked and there is nothing to do",
when the detail body behind that banner is precisely where the fix
lives. A researcher hit this on the personal-instruction-layer row and
had to ask an agent what to do, because the UI offered nothing to
click.

Step rows have carried this triangle all along, so the fix is to stop
the Project block being the exception.

The direction is asserted, not just the presence: a collapsed row
showing the OPEN glyph would be worse than no glyph, since it tells
the researcher the thing is already open and its emptiness is the
answer.

Kills (confirmed): returning "" from ``_fsExpandTriangle`` -> the row
assertion fails with the header text carrying no marker.
"""

import pytest

from tests.browser.conftest import (
    S_HOST_PROJECT_READY,
    S_HOST_STEP_NAME,
    S_HOST_WORKFLOW_NAME,
)


pytestmark = pytest.mark.browser

S_CLOSED_TRIANGLE = "▸"
S_OPEN_TRIANGLE = "▾"


def _fnOpenTheHostWorkflow(pageDashboard, serverHub):
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
    pageDashboard.wait_for_selector(
        ".project-block-header", timeout=20000,
    )


def _fsTitleText(pageDashboard, sSelector):
    return pageDashboard.locator(sSelector).first.text_content()


def test_the_project_block_shows_and_tracks_its_expand_state(
    pageDashboard, serverHub,
):
    """One session, all three levels, because one session is all there is.

    These were two tests until the second could not run: the first
    still held the project and the tile answered "In use in another
    browser session." That is the one-session-per-container model
    working as designed, so the tests merged rather than the model
    being worked around.
    """
    _fnOpenTheHostWorkflow(pageDashboard, serverHub)

    sProject = _fsTitleText(pageDashboard, ".project-block-title")
    assert S_OPEN_TRIANGLE in sProject or S_CLOSED_TRIANGLE in sProject, (
        "the Project banner is click-to-expand and shows no marker "
        f"saying so: {sProject!r}"
    )

    sGroup = _fsTitleText(pageDashboard, ".requirement-group-title")
    assert S_OPEN_TRIANGLE in sGroup or S_CLOSED_TRIANGLE in sGroup, (
        f"a requirement group shows no expand marker: {sGroup!r}"
    )

    # Open a group so its rows render, then check a row.
    pageDashboard.locator(".requirement-group-header").first.click()
    pageDashboard.wait_for_selector(
        ".requirement-row-title", timeout=10000,
    )
    sRow = _fsTitleText(pageDashboard, ".requirement-row-title")
    assert S_CLOSED_TRIANGLE in sRow, (
        "a collapsed requirement row must show the CLOSED glyph -- "
        "this is the row that sent a researcher to ask an agent what "
        f"to do, because nothing said it could be opened: {sRow!r}"
    )

    # ...and the marker must track state, not decorate. A marker stuck
    # on "open" would tell the researcher the body is already showing
    # and that its emptiness is the answer.
    pageDashboard.locator(".requirement-row-header").first.click()
    pageDashboard.wait_for_timeout(400)
    sAfter = _fsTitleText(pageDashboard, ".requirement-row-title")
    assert S_OPEN_TRIANGLE in sAfter, (
        "the marker did not flip when the row opened, so it is "
        f"decoration rather than state: {sAfter!r}"
    )
    assert pageDashboard.listPageErrors == []
