"""Stage 0: every dashboard view renders without throwing.

The baseline the rest of the front-end suite is built on. It answers
one question for every panel, tab and modal the dashboard declares:
does revealing it produce a JavaScript error?

That is a low bar and it is deliberately low. It is also the bar that
was not being met by anything: ~30,000 lines of frontend with no
runner at all, where a single ReferenceError kills the module that
threw it and every module below it in load order. A branch once merged
fully green with the frontend entirely unexecuted.

**The denominator is derived, never hardcoded.** The views come from
the live DOM, so adding a panel to `index.html` automatically puts it
under test. A hardcoded list would rot the moment someone added the
42nd view, and rot silently, which is the failure mode this whole
effort exists to remove.

**What this does NOT prove.** Revealing a view is not the same as
driving the app into the state that populates it. A panel can render
empty, or with stale content, and pass here. Correct population is
what the per-action journeys must show; this only proves the view is
reachable without exploding. Do not read a green baseline as "the
dashboard works".
"""

import pytest


pytestmark = pytest.mark.browser


_S_COLLECT_VIEWS = """() => {
    const out = {panels: [], tabs: [], modals: []};
    document.querySelectorAll('[id]').forEach(el => {
        const sId = el.id;
        if (/^modal[A-Z]/.test(sId)) out.modals.push(sId);
        else if (/^panel[A-Z]/.test(sId)) out.panels.push(sId);
        else if (/^tab[A-Z]/.test(sId)) out.tabs.push(sId);
    });
    return out;
}"""

# Reveal one element and report what it contains. Kept as one
# round-trip per view so a throw is attributed to the view that caused
# it rather than to a batch.
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


@pytest.fixture
def dictViews(pageDashboard, serverHub):
    """Load the dashboard once and enumerate its declared views."""
    pageDashboard.goto(serverHub.sBaseUrl, wait_until="networkidle")
    return pageDashboard.evaluate(_S_COLLECT_VIEWS)


def testTheDashboardDeclaresViewsToTest(dictViews):
    """A baseline over an empty set would be a green lie.

    If the selectors ever stop matching -- a naming convention
    change, a template rewrite -- every other test in this file
    passes vacuously. This is the guard against that.
    """
    iTotal = sum(len(listIds) for listIds in dictViews.values())
    assert iTotal >= 30, (
        f"Only {iTotal} views matched the panel/tab/modal naming "
        f"convention: {dictViews}. Either the dashboard shrank "
        "drastically or the convention changed and this baseline is "
        "now measuring almost nothing."
    )
    assert dictViews["modals"], "no modals found"
    assert dictViews["panels"] or dictViews["tabs"], "no panels found"


def testEveryDeclaredViewRevealsWithoutThrowing(
    pageDashboard, dictViews,
):
    """Revealing any view must not raise or log an error.

    One round trip per view, so the failure message names the view
    that broke rather than the batch it was in.
    """
    listOffenders = []
    for sKind, listIds in sorted(dictViews.items()):
        for sId in listIds:
            iErrorsBefore = len(pageDashboard.listConsoleErrors)
            iThrowsBefore = len(pageDashboard.listPageErrors)
            dictResult = pageDashboard.evaluate(_S_REVEAL_VIEW, sId)
            if not dictResult["found"]:
                listOffenders.append(f"{sKind}/{sId}: vanished from DOM")
                continue
            listNewErrors = (
                pageDashboard.listConsoleErrors[iErrorsBefore:]
                + pageDashboard.listPageErrors[iThrowsBefore:]
            )
            if listNewErrors:
                listOffenders.append(
                    f"{sKind}/{sId}: {'; '.join(listNewErrors)}"
                )
    assert not listOffenders, (
        "Revealing these views produced JavaScript errors:\n  "
        + "\n  ".join(listOffenders)
    )


def testEveryPanelAndTabHasContentAtLoad(pageDashboard, dictViews):
    """A panel present but empty is a rendering failure, not a state.

    Modals are excluded on purpose: an unopened modal legitimately
    holds no content until something populates it. A panel or tab is
    part of the page's own structure and should carry its markup from
    the start, so an empty one means its template did not render.
    """
    listEmpty = []
    for sKind in ("panels", "tabs"):
        for sId in dictViews[sKind]:
            dictResult = pageDashboard.evaluate(_S_REVEAL_VIEW, sId)
            if dictResult["found"] and dictResult["iChildren"] == 0:
                listEmpty.append(f"{sKind}/{sId}")
    assert not listEmpty, (
        "These panels/tabs rendered with no child elements at all, "
        "which means their markup never arrived: " + ", ".join(listEmpty)
    )
