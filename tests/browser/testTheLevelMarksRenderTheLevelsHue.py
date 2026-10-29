"""The filtered level marks and the terminal cursor render the level's hue.

The companion census, ``testTheLevelColorHasOneHome.py``, checks every
accent by its computed CSS. Two things computed CSS cannot see are
checked here by their pixels: what a ``filter`` does to an image (the
toolbar checkmarks, the verified badge and the attained-level favicon
are tinted that way from one pale-blue source), and what xterm paints
for its cursor, which is read from xterm's live options and sampled
from the screen. Each mark is screenshotted and the saturation-weighted
mean hue of its colored pixels compared with the level color's hue.

The toolbar wordmark is drawn through an alpha mask in the level color.
It used to be a filtered opaque PNG, whose tinted background drew a box
around the logo (a maroon one at Level 3), so every pixel of its box
must be a blend of the toolbar's own background and the level color.

Kills (confirmed): restoring the fixed purple filter on
``.vaibify-checkmark img`` -> the pixel check fails at Level 2 with the
checkmark's hue purple.
"""

import colorsys
import io
import math

import pytest
from PIL import Image

from tests.browser.testTheLevelColorHasOneHome import (
    DICT_LEVEL_COLORS,
    fnOpenAndFreezeTheDashboard,
    fnSetTheLevel,
    ftParseRgb,
)


pytestmark = pytest.mark.browser

F_HUE_TOLERANCE_DEGREES = 30.0
I_BLEND_TOLERANCE = 12
T_TERMINAL_BACKGROUND = (13, 13, 26)


def _ffHueDegrees(tRgb):
    fHue, _, _ = colorsys.rgb_to_hsv(*(i / 255.0 for i in tRgb))
    return fHue * 360.0


def _ffHueDistance(fFirst, fSecond):
    fDelta = abs(fFirst - fSecond) % 360.0
    return min(fDelta, 360.0 - fDelta)


def _ffMeanHueOfMark(baPng, tBackground):
    """Return the saturation-weighted circular mean hue of a mark.

    Pixels near the backdrop, and nearly grey ones, carry no hue the
    filter chose, so they are left out.
    """
    imageMark = Image.open(io.BytesIO(baPng)).convert("RGB")
    fSine, fCosine = 0.0, 0.0
    for tPixel in imageMark.getdata():
        if sum(abs(a - b) for a, b in zip(tPixel, tBackground)) < 40:
            continue
        fHue, fSaturation, fValue = colorsys.rgb_to_hsv(
            *(i / 255.0 for i in tPixel))
        if fSaturation < 0.2 or fValue < 0.25:
            continue
        fSine += fSaturation * math.sin(2 * math.pi * fHue)
        fCosine += fSaturation * math.cos(2 * math.pi * fHue)
    assert fSine or fCosine, "the mark rendered no colored pixel"
    return (math.degrees(math.atan2(fSine, fCosine)) + 360.0) % 360.0


def _fnAssertHueIsTheLevels(sWhat, fHue, iLevel):
    fExpected = _ffHueDegrees(DICT_LEVEL_COLORS[iLevel])
    assert _ffHueDistance(fHue, fExpected) <= F_HUE_TOLERANCE_DEGREES, (
        f"at Level {iLevel} the {sWhat} renders hue {fHue:.0f} deg, "
        f"not the level color's {fExpected:.0f} deg"
    )


def _ffDistanceFromBlend(tPixel, tBackground, tColor):
    """Return how far a pixel lies from the background-to-color segment."""
    listDirection = [c - b for b, c in zip(tBackground, tColor)]
    fLengthSquared = sum(d * d for d in listDirection)
    fAlong = sum((p - b) * d for p, b, d in
                 zip(tPixel, tBackground, listDirection)) / fLengthSquared
    fAlong = min(1.0, max(0.0, fAlong))
    return math.sqrt(sum(
        (p - (b + fAlong * d)) ** 2
        for p, b, d in zip(tPixel, tBackground, listDirection)))


def _fnAssertTheLogoHasNoBox(pageDashboard, iLevel, tToolbar):
    """The wordmark's box holds only the toolbar and the level color."""
    tLevel = DICT_LEVEL_COLORS[iLevel]
    baPng = pageDashboard.locator("#toolbar .toolbar-logo").screenshot()
    listPixels = list(Image.open(io.BytesIO(baPng)).convert("RGB").getdata())
    listStray = [t for t in listPixels
                 if _ffDistanceFromBlend(t, tToolbar, tLevel) >
                 I_BLEND_TOLERANCE]
    assert listStray == [], (
        f"at Level {iLevel} the toolbar logo paints "
        f"{len(listStray)} pixels that are neither the toolbar "
        f"rgb{tToolbar} nor the level color rgb{tLevel}, nor a blend: "
        f"{sorted(set(listStray))[:5]}"
    )
    iBackground = sum(1 for t in listPixels if max(
        abs(a - b) for a, b in zip(t, tToolbar)) <= 3)
    iGlyph = sum(1 for t in listPixels if max(
        abs(a - b) for a, b in zip(t, tLevel)) <= 3)
    assert iBackground > len(listPixels) * 0.3, (
        f"at Level {iLevel} the pixels just outside the wordmark are "
        "not the toolbar's own background"
    )
    assert iGlyph > len(listPixels) * 0.05, (
        f"at Level {iLevel} the wordmark is not painted in the level "
        f"color rgb{tLevel}"
    )


def _fnAssertTheFilteredMarks(pageDashboard, iLevel):
    tBackground = ftParseRgb(pageDashboard.evaluate(
        "() => getComputedStyle(document.getElementById("
        "'levelColorMarks')).backgroundColor"))
    for sWhat, sSelector in (("attained-level favicon", "#probeFavicon"),
                             ("verified badge", "#probeBadge")):
        baPng = pageDashboard.locator(sSelector).screenshot()
        _fnAssertHueIsTheLevels(
            sWhat, _ffMeanHueOfMark(baPng, tBackground), iLevel)
    tToolbar = ftParseRgb(pageDashboard.evaluate(
        "() => getComputedStyle(document.getElementById('toolbar'))"
        ".backgroundColor"))
    _fnAssertTheLogoHasNoBox(pageDashboard, iLevel, tToolbar)
    if iLevel == 0:
        return
    baPng = pageDashboard.locator(
        f'.vaibify-checkmark[data-proof-level="{iLevel}"] img').screenshot()
    _fnAssertHueIsTheLevels(
        "toolbar checkmark", _ffMeanHueOfMark(baPng, tToolbar), iLevel)


def _fnAssertTheTerminalCursor(pageDashboard, iLevel):
    sCursor = pageDashboard.evaluate(
        "() => window.__listTerminalsForTest.map("
        "(term) => term.options.theme.cursor)")
    sExpected = "#%02x%02x%02x" % DICT_LEVEL_COLORS[iLevel]
    assert sCursor and all(s.lower() == sExpected for s in sCursor), (
        f"at Level {iLevel} xterm's live cursor is {sCursor}, not "
        f"{sExpected}"
    )
    baPng = pageDashboard.locator(".xterm-cursor").first.screenshot()
    _fnAssertHueIsTheLevels(
        "terminal cursor", _ffMeanHueOfMark(baPng, T_TERMINAL_BACKGROUND), iLevel)



@pytest.mark.falsification
def testFilteredMarksAndTheCursorRenderTheLevelsHue(
    pageDashboard, serverHub,
):
    """Kills: restoring the fixed purple filter on .vaibify-checkmark img."""
    fnOpenAndFreezeTheDashboard(pageDashboard, serverHub, bWithShell=True)
    for iLevel in (0, 1, 2, 3):
        fnSetTheLevel(pageDashboard, iLevel)
        _fnAssertTheFilteredMarks(pageDashboard, iLevel)
        _fnAssertTheTerminalCursor(pageDashboard, iLevel)
    pageDashboard.clock.resume()
    assert pageDashboard.listPageErrors == []
