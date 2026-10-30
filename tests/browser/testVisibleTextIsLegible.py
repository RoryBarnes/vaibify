"""Every visible piece of text on the rendered page is legible.

The palette test (``tests/testPaletteContrast.py``) proves the color
tokens can be read on the surfaces; this sweep proves the PAGE can,
which is a different claim. It walks every element that holds its own
text, composites the text color and every translucent layer behind it
down to the first opaque surface, and asks the WCAG 2.1 contrast ratio
(Success Criterion 1.4.3): 4.5:1 for text, 3:1 for large text (24px,
or 18.66px bold). It is driven over the three screens the lane can
render -- the container hub, the setup wizard, and a workflow with the
Zenodo token banner rendered through the module that owns it, because
that banner's links were the ones nobody could read (2026-10-30).

What it cannot see, it reports rather than passes: text over a gradient
or an image is listed as unmeasured, never counted legible. Text that
is dim on purpose is allowed by selector, with its reason, in
``DICT_ALLOW_LIST``; the terminal's content is the program's own ANSI
palette and is exempt. A disabled control is exempt by the criterion's
own terms.

The independent oracle is the same hand formula the palette test pins
on white-on-black (21:1) and the browser's default link blue on this
theme (1.7:1); the sweep's arithmetic is checked against it on the
rendered banner link.
"""

import pytest

from tests.browser.conftest import (
    fnLoadThePickerUntilItsButtonsListen,
    fnOpenTheSeededHostWorkflow,
)
from tests.browser.testTheSetupWizardSignsItselfIn import _fpageOpen


pytestmark = pytest.mark.browser

F_TEXT_MINIMUM_CONTRAST = 4.5
F_LARGE_TEXT_MINIMUM_CONTRAST = 3.0

# Selectors whose text is allowed below the minimum, each with its
# reason. The sweep exempts the element and everything inside it.
DICT_ALLOW_LIST = {
    ".xterm": "terminal content is the program's own ANSI palette",
}

# The banner as the server describes a migrated token (the shape of
# GET /api/zenodo-tokens/{id}); no token value is ever in it.
DICT_TOKEN_BANNER = {
    "bApplicable": True,
    "listSlots": [{
        "sSlot": "zenodo_token_sandbox", "sState": "removed",
        "sInstance": "sandbox", "sProposedInstance": "sandbox",
        "bAwaitingDecision": True, "sReason": "", "sConflictChoice": "",
        "dictValidation": {"sOutcome": "accepted"},
        "listDepositTitles": ["An example deposit"],
    }],
    "bAwaitingDecision": True,
    "bRotationAdvised": True,
    "dictTokenPages": {
        "sandbox": "https://sandbox.zenodo.org/account/settings/applications/",
        "production": "https://zenodo.org/account/settings/applications/",
    },
}

S_RENDER_TOKEN_BANNER = "(dictDescribed) => VaibifyZenodoTokens.fnRender(dictDescribed)"

S_SWEEP = """([listExemptSelectors, fTextMinimum, fLargeMinimum]) => {
    function flistRgba(sValue) {
        const match = /rgba?\\(([^)]+)\\)/.exec(sValue || '');
        if (!match) return null;
        const listParts = match[1].split(/[ ,\\/]+/).filter(Boolean)
            .map(parseFloat);
        return [listParts[0], listParts[1], listParts[2],
                listParts.length > 3 ? listParts[3] : 1];
    }
    function flistComposite(listTop, listUnder) {
        const fAlpha = listTop[3];
        return [0, 1, 2].map((i) =>
            listTop[i] * fAlpha + listUnder[i] * (1 - fAlpha)).concat([1]);
    }
    function ffEffectiveOpacity(el) {
        let fOpacity = 1;
        for (let node = el; node; node = node.parentElement) {
            fOpacity *= parseFloat(getComputedStyle(node).opacity);
        }
        return fOpacity;
    }
    function fdictBackgroundBehind(el) {
        /* Every painted layer from the element to the root, folded from
           the root down. A gradient or an image is a surface no
           arithmetic can read, and is reported as such. */
        const listLayers = [];
        let bUnreadable = false;
        for (let node = el; node; node = node.parentElement) {
            const style = getComputedStyle(node);
            if (style.backgroundImage !== 'none') bUnreadable = true;
            const listRgba = flistRgba(style.backgroundColor);
            if (listRgba && listRgba[3] > 0) listLayers.push(listRgba);
        }
        let listBackground = [255, 255, 255, 1];
        for (let i = listLayers.length - 1; i >= 0; i--) {
            listBackground = flistComposite(listLayers[i], listBackground);
        }
        return {listRgb: listBackground, bUnreadable: bUnreadable};
    }
    function ffLuminance(listRgb) {
        const [r, g, b] = listRgb.slice(0, 3).map((c) => {
            c /= 255;
            return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
        });
        return 0.2126 * r + 0.7152 * g + 0.0722 * b;
    }
    function ffContrast(listA, listB) {
        const fA = ffLuminance(listA), fB = ffLuminance(listB);
        return (Math.max(fA, fB) + 0.05) / (Math.min(fA, fB) + 0.05);
    }
    function fsOwnText(el) {
        return Array.from(el.childNodes).filter((n) => n.nodeType === 3)
            .map((n) => n.textContent.trim()).filter(Boolean).join(' ');
    }
    function fbVisible(el) {
        /* A text node at font-size 0 paints nothing: a glyphless control
           keeps its label for assistive technology, not for the eye. */
        if (el.getClientRects().length === 0) return false;
        const style = getComputedStyle(el);
        return style.visibility !== 'hidden' && parseFloat(style.fontSize) > 0 &&
            ffEffectiveOpacity(el) > 0;
    }
    function fsDescribe(el) {
        const sClass = typeof el.className === 'string' ? el.className.trim() : '';
        return el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') +
            (sClass ? '.' + sClass.split(/\\s+/).join('.') : '');
    }
    function fbLargeText(style) {
        const fSize = parseFloat(style.fontSize);
        const iWeight = parseInt(style.fontWeight, 10) ||
            (style.fontWeight === 'bold' ? 700 : 400);
        return fSize >= 24 || (fSize >= 18.66 && iWeight >= 700);
    }
    const listOffenders = [], listUnmeasured = [], listMeasured = [];
    document.querySelectorAll('body *').forEach((el) => {
        const sText = fsOwnText(el);
        if (!sText || !fbVisible(el)) return;
        if (el.matches(':disabled') ||
            el.closest('[aria-disabled="true"], [aria-hidden="true"]')) return;
        if (listExemptSelectors.some((s) => el.closest(s))) return;
        const style = getComputedStyle(el);
        const sElement = fsDescribe(el);
        const dictBehind = fdictBackgroundBehind(el);
        let listText = flistRgba(style.color);
        if (dictBehind.bUnreadable || !listText) {
            listUnmeasured.push([sElement, sText.slice(0, 40)]);
            return;
        }
        listText[3] *= ffEffectiveOpacity(el);
        listText = flistComposite(listText, dictBehind.listRgb);
        const fRatio = ffContrast(listText, dictBehind.listRgb);
        const fMinimum = fbLargeText(style) ? fLargeMinimum : fTextMinimum;
        const dictReading = {
            sElement: sElement, sText: sText.slice(0, 40),
            listText: listText.slice(0, 3).map(Math.round),
            listBackground: dictBehind.listRgb.slice(0, 3).map(Math.round),
            fRatio: Math.round(fRatio * 100) / 100, fMinimum: fMinimum,
        };
        listMeasured.push(dictReading);
        if (fRatio < fMinimum) listOffenders.push(dictReading);
    });
    return {listOffenders: listOffenders, listUnmeasured: listUnmeasured,
            listMeasured: listMeasured};
}"""


def _fdictSweep(page):
    return page.evaluate(S_SWEEP, [
        list(DICT_ALLOW_LIST), F_TEXT_MINIMUM_CONTRAST,
        F_LARGE_TEXT_MINIMUM_CONTRAST,
    ])


def _fsDescribeReading(dictReading):
    return (
        f"{dictReading['sElement']} {dictReading['sText']!r}: "
        f"rgb{tuple(dictReading['listText'])} on "
        f"rgb{tuple(dictReading['listBackground'])} = "
        f"{dictReading['fRatio']}:1, needs {dictReading['fMinimum']}:1"
    )


def _fnAssertLegible(dictSweep, sScreen, iAtLeastMeasured):
    assert len(dictSweep["listMeasured"]) >= iAtLeastMeasured, (
        f"the sweep of {sScreen} measured only "
        f"{len(dictSweep['listMeasured'])} text elements; it did not see "
        f"the screen"
    )
    assert dictSweep["listOffenders"] == [], (
        f"text on {sScreen} is below the WCAG minimum:\n  "
        + "\n  ".join(_fsDescribeReading(d) for d in dictSweep["listOffenders"])
        + "\nunmeasured (over a gradient or image): "
        + f"{dictSweep['listUnmeasured']}"
    )


def testTheContainerHubReadsLegibly(pageDashboard, serverHub):
    fnLoadThePickerUntilItsButtonsListen(pageDashboard, serverHub)
    _fnAssertLegible(_fdictSweep(pageDashboard), "the container hub", 10)
    assert pageDashboard.listPageErrors == []


def testTheSetupWizardReadsLegibly(browserEngine, serverSetupWizard):
    contextBrowser, page = _fpageOpen(browserEngine)
    try:
        page.goto(serverSetupWizard.fsBootstrapUrl(), wait_until="load")
        page.wait_for_selector(".template-card", timeout=15000)
        _fnAssertLegible(_fdictSweep(page), "the setup wizard", 10)
        assert page.listPageErrors == []
    finally:
        contextBrowser.close()


@pytest.mark.falsification
def testTheWorkflowAndItsTokenBannerReadLegibly(pageDashboard, serverHub):
    """Kills: deleting the base ``a:where([href])`` rule from styleMain.css.

    Without it the banner's links take the browser's default dark blue,
    which the sweep reads at about 1.7:1 on this theme.
    """
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub, bAwaitProjectBlock=True)
    pageDashboard.evaluate(S_RENDER_TOKEN_BANNER, DICT_TOKEN_BANNER)
    pageDashboard.wait_for_selector("#zenodoTokenBanner a[href]", timeout=5000)
    dictSweep = _fdictSweep(pageDashboard)
    listBannerLinks = [
        dictReading for dictReading in dictSweep["listMeasured"]
        if dictReading["sElement"] == "a"
        and dictReading["sText"] in ("sandbox", "production")
    ]
    assert len(listBannerLinks) == 2, (
        f"the sweep did not measure both banner links: {listBannerLinks}"
    )
    _fnAssertLegible(dictSweep, "the workflow with its token banner", 40)
    assert pageDashboard.listPageErrors == []
