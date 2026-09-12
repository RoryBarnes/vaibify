"""Admin > Environment Info reaches the screen, and says "unknown" honestly.

Two things the Python suite cannot see. The menu item is wired by id
through `scriptEventBindings.js`, so a typo in the id or a missing
`<script>` tag produces a menu entry that silently does nothing --
green everywhere else. And the modal's job is to distinguish a fact
from an absence: an image built before vaibify recorded the epoch must
read as UNKNOWN, never as a blank cell that looks like a value nobody
filled in.

The backend is stubbed here on purpose. What this file proves is that
a real click renders real markup; the route's own honesty (the epoch
comes from the image, not the host recipe) is
tests/testEnvironmentInfoRoute.py, which drives the real helper.
"""

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow


pytestmark = pytest.mark.browser


@pytest.fixture(autouse=True)
def fixtureDropClaimsBetweenTests(serverHub):
    """Give every claim back after each test.

    The hub is module-scoped and the page is not, so a test that
    claims the project and stops leaves it owned by a lease nobody
    holds. The next test's claim is refused and the symptom is a
    locked tile intercepting the click -- which reads as a UI bug in
    the feature under test rather than as leaked state. (Met exactly
    that way while writing this file.)
    """
    yield
    from vaibify.config.containerLock import fnReleaseContainerLock
    dictContainerOwners = serverHub.app.state.dictContainerOwners
    for _sName, recordOwner in list(dictContainerOwners.items()):
        fileHandle = getattr(recordOwner, "fileHandleLock", None)
        if fileHandle is not None:
            try:
                fnReleaseContainerLock(fileHandle)
            except OSError:
                pass
    dictContainerOwners.clear()
    serverHub.app.state.dictSessionOwner.clear()


_S_STUB_A_KNOWN_ENVIRONMENT = """() => {
    VaibifyApi.fdictGet = () => Promise.resolve({
        sContainerId: 'cid-probe',
        sImageDigest: 'sha256:' + 'ab'.repeat(32),
        sImageId: 'sha256:' + 'cd'.repeat(32),
        sArchitecture: 'arm64',
        sRecipeFingerprint: 'ef'.repeat(32),
        sToolchainEpoch: '20260909',
        sVaibifyVersion: '0.9.9',
        dictSystemTools: {python: '3.12.3', gcc: '13.3.0'},
    });
}"""

_S_STUB_AN_UNLABELLED_IMAGE = """() => {
    VaibifyApi.fdictGet = () => Promise.resolve({
        sContainerId: 'cid-probe',
        sImageDigest: 'sha256:' + 'ab'.repeat(32),
        sImageId: '',
        sArchitecture: '',
        sRecipeFingerprint: '',
        sToolchainEpoch: '',
        sVaibifyVersion: '0.9.9',
        dictSystemTools: {},
    });
}"""


def _fnOpenTheModal(pageDashboard):
    """Drive the real menu, not the module, so the wiring is exercised.

    The dropdown opens on a CLICK of its trigger, which adds `.active`
    -- it is not a CSS :hover menu, and a test that hovered found the
    item present in the DOM and permanently invisible. Clicking the
    trigger is also what a researcher does.
    """
    pageDashboard.click(
        ".toolbar-menu:has(#btnAdminEnvironmentInfo) "
        ".toolbar-menu-trigger",
    )
    pageDashboard.wait_for_selector(
        "#btnAdminEnvironmentInfo", state="visible", timeout=5000,
    )
    pageDashboard.click("#btnAdminEnvironmentInfo")
    pageDashboard.wait_for_selector("#modalInfo", timeout=5000)


def test_the_admin_menu_item_opens_the_environment_modal(
    pageDashboard, serverHub,
):
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    pageDashboard.evaluate(_S_STUB_A_KNOWN_ENVIRONMENT)
    _fnOpenTheModal(pageDashboard)

    sBody = pageDashboard.inner_text("#modalInfo")
    assert "Environment Info" in sBody
    # The epoch is stored compact so it can ride in a Docker label, and
    # shown as a date because that is what a researcher reads.
    assert "2026-09-09" in sBody, sBody
    assert "20260909" not in sBody, "the raw label form leaked to the UI"
    assert "arm64" in sBody
    assert "3.12.3" in sBody


def test_an_unlabelled_image_reads_unknown_not_blank(
    pageDashboard, serverHub,
):
    """A blank cell reads as a value nobody filled in. An image that
    predates epoch labelling has to say so."""
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    pageDashboard.evaluate(_S_STUB_AN_UNLABELLED_IMAGE)
    _fnOpenTheModal(pageDashboard)

    assert pageDashboard.locator(
        "#modalInfo .env-info-unknown",
    ).count() >= 3
    sBody = pageDashboard.inner_text("#modalInfo")
    assert "unknown" in sBody
    assert "built before vaibify recorded the epoch" in sBody
