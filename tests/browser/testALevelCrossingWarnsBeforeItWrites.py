"""A Level 3 declaration warns BEFORE it rewrites the file Level 2 compares.

``declare-binary`` and ``declare-determinism`` advance Level 3 by
writing ``project.json`` -- which Level 2 compares against the GitHub
mirror and the Zenodo archive -- so the project drops below Level 2
until the change is pushed AND a new Zenodo version carries it. A Zenodo
version is immutable, which is what earns this a modal where a step
rename gets none.

Both were seeded in ``DICT_ACCEPTED_CROSSINGS`` as ``unwarned``, with
the budget that counts them. This is the change that empties it.

The Python guard for the same property parses the JavaScript SOURCE for
a ``bConfirm`` flag, and presence in source is not the guarantee: the
dispatcher could stop honouring it, the modal could open after the
request, or the form body could be read from an element the re-render
has already replaced. So the assertion here is on the ORDER -- no
request leaves the page before the researcher has seen the dialog --
which is the only form of the claim a researcher would notice being
false.
"""

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow
from tests.browser.testDestructiveActionsLookDestructive import (
    _fnExpandEverything,
)


pytestmark = pytest.mark.browser

S_CONFIRM_MODAL = "#modalConfirm"


def _flistInterceptDeclarations(pageDashboard):
    """Record every declaration POST without letting one happen."""
    listPosts = []
    for sPath in ("determinism/declare", "binaries/declare"):
        pageDashboard.route(
            f"**/api/workflow/**/{sPath}",
            lambda route: (
                listPosts.append(route.request.url),
                route.fulfill(
                    status=200, content_type="application/json",
                    body="{}",
                ),
            ),
        )
    return listPosts


def _fnFillTheFormFor(pageDashboard, sAction):
    """Fill the form the named declaration reads, through its controls."""
    if sAction == "declare-determinism":
        pageDashboard.locator(".determinism-answer").first.check()
        return
    # The package form is collapsed to an "Add package…" button until
    # the researcher asks for it, so the fields do not exist yet.
    if pageDashboard.locator(".binary-form-path").count() == 0:
        pageDashboard.locator(".wf-toggle-binary-form").first.click()
        pageDashboard.wait_for_selector(
            ".binary-form-path", timeout=10000,
        )
    pageDashboard.locator(".binary-form-path").first.fill("/usr/bin/env")
    pageDashboard.locator(".binary-form-purpose").first.fill("a purpose")
    pageDashboard.locator(".binary-form-version").first.fill("1.0")


@pytest.mark.falsification
def test_neither_declaration_writes_before_the_researcher_agrees(
    pageDashboard, serverHub,
):
    """ONE open, both actions: the seeded project is leased.

    Kills: dropping ``dictConfirm`` from either entry in
    ``_DICT_PROJECT_ACTIONS``, which sends the POST on the click and
    puts the project below Level 2 with no warning at all.
    """
    fnOpenTheSeededHostWorkflow(
        pageDashboard, serverHub, bAwaitProjectBlock=True,
    )
    _fnExpandEverything(pageDashboard)
    listPosts = _flistInterceptDeclarations(pageDashboard)

    for sAction in ("declare-determinism", "declare-binary"):
        # Each form is filled IMMEDIATELY before its own click, and
        # through the real controls. A form reader that finds nothing
        # returns null and the action ends SILENTLY, so a test that
        # skipped this -- or that filled both up front and let a
        # re-render clear one -- would be asserting the absence of a
        # POST that was never going to happen.
        _fnFillTheFormFor(pageDashboard, sAction)
        pageDashboard.locator(
            f'[data-wf-action="{sAction}"]',
        ).first.click()
        pageDashboard.wait_for_selector(
            S_CONFIRM_MODAL, state="visible", timeout=5000,
        )
        sText = pageDashboard.inner_text(S_CONFIRM_MODAL).lower()
        assert listPosts == [], (
            f"{sAction} wrote project.json before the researcher saw "
            f"the dialog: {listPosts}"
        )
        # The message names the COST, not merely the act. A dialog
        # that says "are you sure?" is a speed bump; this one has to
        # say what the researcher is spending, because the Zenodo half
        # cannot be corrected in place.
        assert "level 2" in sText, sText
        assert "zenodo" in sText, sText
        assert "immutable" in sText, sText
        pageDashboard.click("#btnConfirmCancel")
        pageDashboard.wait_for_selector(
            S_CONFIRM_MODAL, state="hidden", timeout=5000,
        )
        assert listPosts == [], (
            f"{sAction} wrote project.json after the researcher "
            f"declined: {listPosts}"
        )

    assert pageDashboard.listPageErrors == []
