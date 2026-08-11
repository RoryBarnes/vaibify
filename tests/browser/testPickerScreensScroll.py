"""An environment below the fold can still be reached.

Reported the moment a researcher had enough environments registered to
fill the window: "they extend beyond the bottom of the page, but I
don't have scrolling ability". The cause is that ``html, body`` are
``overflow: hidden`` — correct for the dashboard, which is a
fixed-viewport layout whose panels scroll themselves, and wrong for the
two picker screens, which are ordinary vertical lists that grow with
the number of environments and the number of Projects in one.

There is a second, quieter half. Both screens centred their content
with ``justify-content: center``, and a centred flex column whose
content overflows is clipped at the TOP: the rows that go missing are
the ones a scrollbar could never bring back, because they sit at a
negative offset. So simply adding ``overflow-y: auto`` to a centred
column would have made the bottom reachable and left the top lost —
which is why the test below checks BOTH ends.

Overflow is produced here by shrinking the window rather than by
registering many environments. That keeps the module-scoped registry
the rest of the lane depends on untouched, and it is the more general
condition: the screens must scroll whenever the content exceeds the
viewport, whether that is twenty environments or a short laptop.
"""

import pytest

from tests.browser.conftest import (
    S_HOST_PROJECT_READY,
    S_HOST_WORKFLOW_NAME,
)
from tests.browser.fakeDockerAdapter import S_CONTAINER_NAME


pytestmark = pytest.mark.browser

# Short enough that the logo, the tagline and three tiles cannot fit.
_DICT_SHORT_WINDOW = {"width": 1000, "height": 320}


def _fnAssertScrollsToBothEnds(page, sScreenSelector, sFirst, sLast):
    """Assert the screen scrolls and neither end is stranded."""
    assert page.evaluate(
        f"""() => {{
            const el = document.querySelector('{sScreenSelector}');
            return el.scrollHeight > el.clientHeight;
        }}"""
    ), (
        f"{sScreenSelector} does not overflow at "
        f"{_DICT_SHORT_WINDOW}, so this test proves nothing"
    )
    for sSelector in (sLast, sFirst):
        page.locator(sSelector).scroll_into_view_if_needed()
        dictBox = page.locator(sSelector).bounding_box()
        assert dictBox is not None, f"{sSelector} has no box"
        assert dictBox["y"] >= 0, (
            f"{sSelector} sits above the scroll origin at y="
            f"{dictBox['y']}: it is clipped, not scrolled, and no "
            "scrollbar can reach it"
        )
        assert dictBox["y"] + dictBox["height"] <= (
            _DICT_SHORT_WINDOW["height"]
        ), (
            f"{sSelector} could not be brought on screen: bottom at "
            f"{dictBox['y'] + dictBox['height']}"
        )


@pytest.mark.falsification
def testTheEnvironmentListScrollsToBothEnds(pageDashboard, serverHub):
    """The screen a researcher lands on, with more tiles than fit.

    Kills: leaving the landing screen unscrollable, which hides every
    environment past the fold with no way to reach it.
    """
    pageDashboard.set_viewport_size(_DICT_SHORT_WINDOW)
    pageDashboard.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    pageDashboard.wait_for_selector(
        f'.container-tile[data-name="{S_CONTAINER_NAME}"]', timeout=15000,
    )
    _fnAssertScrollsToBothEnds(
        pageDashboard, "#containerLanding",
        "#containerLanding .vaibify-logo-large",
        f'.container-tile[data-name="{S_HOST_PROJECT_READY}"]',
    )


@pytest.mark.falsification
def testTheProjectListScrollsToBothEnds(pageDashboard, serverHub):
    """The same for the second screen, which the report also named.

    A container can hold many Projects, so this list outgrows a window
    for its own reasons and needed the same fix.

    Kills: fixing only the landing screen.
    """
    pageDashboard.set_viewport_size(_DICT_SHORT_WINDOW)
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
        f'text={S_HOST_WORKFLOW_NAME}', timeout=20000,
    )
    _fnAssertScrollsToBothEnds(
        pageDashboard, "#workflowPicker",
        "#workflowPicker .vaibify-logo-large", "#btnWorkflowBack",
    )


@pytest.fixture(autouse=True)
def fixtureDropClaimsBetweenJourneys(serverHub):
    """Give every claim back; the hub outlives the page."""
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
