"""Stage 0: every dashboard view renders without throwing.

The baseline the rest of the front-end suite is built on. It answers
one question for every panel, tab and modal the dashboard declares:
does revealing it produce a JavaScript error?

That is a low bar and it is deliberately low. It is also the bar that
was not being met by anything: ~30,000 lines of frontend with no
runner at all, where a single ReferenceError kills the module that
threw it and every module below it in load order. A branch once merged
fully green with the frontend entirely unexecuted.

**One test per view, not one test over all views.** The first version
looped inside a single test, so the first broken view aborted the loop
and hid every one after it -- you learned about one bad panel per run.
Parametrising means a run reports all of them at once and names each
in its own test id.

**The roster is derived, never hardcoded.** It is parsed from
`index.html` at collection time, so adding a panel puts it under test
automatically. `testLiveDomMatchesTheParsedRoster` then checks the
parsed set against what the browser actually has, so the two cannot
drift apart -- a view that only exists in the file, or only at
runtime, is itself a finding.

**What this does NOT prove.** Revealing a view is not the same as
driving the app into the state that populates it. A panel can render
empty, or with stale content, and pass here. Correct population is
what the per-action journeys must show; this only proves the view is
reachable without exploding. Do not read a green baseline as "the
dashboard works".
"""

import pathlib
import re

import pytest


pytestmark = pytest.mark.browser


_PATH_INDEX = (
    pathlib.Path(__file__).resolve().parent.parent.parent
    / "vaibify" / "gui" / "static" / "index.html"
)

_RE_VIEW_ID = re.compile(
    r'id="((?:panel|tab|modal)[A-Z][A-Za-z0-9_-]*)"'
)

# Minimum the dashboard is known to declare. Guards against the
# selectors silently stopping matching -- a naming-convention change
# or template rewrite would otherwise leave every test below passing
# vacuously over an empty roster.
_I_MINIMUM_VIEWS = 30


def _flistParseViewRoster():
    """Return every view id index.html declares, sorted and unique."""
    return sorted(set(_RE_VIEW_ID.findall(_PATH_INDEX.read_text())))


def _fsViewKind(sId):
    """Return 'modal', 'panel' or 'tab' from the id's prefix."""
    return re.match(r"[a-z]+", sId).group(0)


LIST_VIEW_IDS = _flistParseViewRoster()


_S_REVEAL_VIEW = """(sId) => {
    const el = document.getElementById(sId);
    if (!el) return {found: false};
    el.style.display = 'block';
    el.style.visibility = 'visible';
    el.removeAttribute('hidden');
    el.classList.remove('hidden');
    return {
        found: true,
        iChildren: el.querySelectorAll('*').length,
        iTextLength: (el.textContent || '').trim().length,
    };
}"""

_S_COLLECT_LIVE_VIEWS = """() => Array.from(
    document.querySelectorAll('[id]')
).map(el => el.id).filter(
    sId => /^(panel|tab|modal)[A-Z]/.test(sId)
).sort()"""


def testTheRosterIsNotEmpty():
    """A baseline over an empty set would be a green lie."""
    assert len(LIST_VIEW_IDS) >= _I_MINIMUM_VIEWS, (
        f"Only {len(LIST_VIEW_IDS)} views matched the panel/tab/modal "
        "naming convention in index.html. Either the dashboard shrank "
        "drastically or the convention changed and this baseline is "
        "now measuring almost nothing."
    )


def testLiveDomMatchesTheParsedRoster(pageDashboard, serverHub):
    """What the browser has must match what the file declares.

    The parametrised tests below are collected from index.html, so a
    view present at runtime but absent from the file would never be
    tested, and one in the file but missing at runtime would fail
    every reveal for a reason that has nothing to do with rendering.
    Either direction is worth knowing about explicitly.
    """
    pageDashboard.goto(serverHub.sBaseUrl, wait_until="networkidle")
    listLive = pageDashboard.evaluate(_S_COLLECT_LIVE_VIEWS)
    setOnlyInFile = set(LIST_VIEW_IDS) - set(listLive)
    setOnlyInDom = set(listLive) - set(LIST_VIEW_IDS)
    assert not setOnlyInFile, (
        f"Declared in index.html but absent from the live DOM: "
        f"{sorted(setOnlyInFile)}"
    )
    assert not setOnlyInDom, (
        "Present at runtime but not declared in index.html, so the "
        f"parametrised baseline never covers it: {sorted(setOnlyInDom)}"
    )


@pytest.mark.parametrize("sViewId", LIST_VIEW_IDS)
def testViewRevealsWithoutThrowing(pageDashboard, serverHub, sViewId):
    """Revealing this view must not raise or log an error."""
    pageDashboard.goto(serverHub.sBaseUrl, wait_until="networkidle")
    iErrorsBefore = len(pageDashboard.listConsoleErrors)
    iThrowsBefore = len(pageDashboard.listPageErrors)

    dictResult = pageDashboard.evaluate(_S_REVEAL_VIEW, sViewId)

    assert dictResult["found"], (
        f"{sViewId} is declared in index.html but not in the DOM."
    )
    listNewErrors = (
        pageDashboard.listConsoleErrors[iErrorsBefore:]
        + pageDashboard.listPageErrors[iThrowsBefore:]
    )
    assert not listNewErrors, (
        f"Revealing {sViewId} produced JavaScript errors: "
        + "; ".join(listNewErrors)
    )


@pytest.mark.parametrize(
    "sViewId",
    [s for s in LIST_VIEW_IDS if _fsViewKind(s) != "modal"],
)
def testPanelOrTabHasContentAtLoad(pageDashboard, serverHub, sViewId):
    """A panel present but empty is a rendering failure, not a state.

    Modals are excluded on purpose: an unopened modal legitimately
    holds no content until something populates it. A panel or tab is
    part of the page's own structure and should carry its markup from
    the start, so an empty one means its template never rendered.
    """
    pageDashboard.goto(serverHub.sBaseUrl, wait_until="networkidle")
    dictResult = pageDashboard.evaluate(_S_REVEAL_VIEW, sViewId)
    assert dictResult["found"], f"{sViewId} missing from the DOM."
    assert dictResult["iChildren"] > 0, (
        f"{sViewId} rendered with no child elements at all, which "
        "means its markup never arrived."
    )
