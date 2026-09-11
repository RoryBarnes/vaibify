"""The terminal's selection highlight must be visible, by measurement.

A researcher could not tell whether text was selected, which made the
copy shortcut look broken when it was working: with nothing selected
the pane has nothing to copy, and the highlight was the only evidence
either way. Measured, it sat at 1.66:1 against the pane background --
well under the 3:1 that WCAG 2.2 SC 1.4.11 (Non-text Contrast) asks of
a user-interface state, and under the 4.5:1 of SC 1.4.3 for the text
sitting on it.

It read as acceptable on two machines and faint on a third. That is a
property of displays, not of the code: the composited colour is pixel
arithmetic and is the same everywhere, so a value that fails here
fails on every screen and merely looks worse on some.

The ratios are RECOMPUTED from the theme rather than asserted as
remembered numbers, so the guard survives a change of palette and
fails only when a change actually crosses a threshold. Both directions
matter and pull against each other: a highlight bright enough to see
is a background the selected text must still be readable against, and
while that text inherited the pane's own colour no alpha satisfied
both at once. Naming `selectionForeground` is what makes them
independent.

Kills: the selection alpha returned to 0.3 -> the highlight assertion
fails at 1.66:1.
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


S_THEME_SOURCE = "vaibify/gui/static/scriptTerminal.js"

# WCAG 2.2: 1.4.11 for a UI state, 1.4.3 for text on it.
F_MINIMUM_STATE_CONTRAST = 3.0
F_MINIMUM_TEXT_CONTRAST = 4.5


def _ftReadThemeColour(sSource, sKey):
    """Return (r, g, b, alpha) for one theme entry, hex or rgba."""
    matchEntry = re.search(
        rf'{sKey}:\s*"([^"]+)"', sSource,
    )
    assert matchEntry, f"the terminal theme names no {sKey}"
    sValue = matchEntry.group(1).strip()
    matchRgba = re.match(
        r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([\d.]+)\s*)?\)",
        sValue,
    )
    if matchRgba:
        fAlpha = (float(matchRgba.group(4))
                  if matchRgba.group(4) is not None else 1.0)
        return (int(matchRgba.group(1)), int(matchRgba.group(2)),
                int(matchRgba.group(3)), fAlpha)
    sHex = sValue.lstrip("#")
    assert len(sHex) == 6, f"{sKey} is neither hex nor rgba: {sValue}"
    return (int(sHex[0:2], 16), int(sHex[2:4], 16), int(sHex[4:6], 16), 1.0)


def _fFlattenChannel(iChannel):
    """Return one sRGB channel linearised, per WCAG's definition."""
    fValue = iChannel / 255.0
    if fValue <= 0.04045:
        return fValue / 12.92
    return ((fValue + 0.055) / 1.055) ** 2.4


def _fRelativeLuminance(tColour):
    fRed, fGreen, fBlue = (_fFlattenChannel(i) for i in tColour[:3])
    return 0.2126 * fRed + 0.7152 * fGreen + 0.0722 * fBlue


def _fContrastRatio(tOne, tOther):
    fOne = _fRelativeLuminance(tOne)
    fOther = _fRelativeLuminance(tOther)
    fLighter, fDarker = max(fOne, fOther), min(fOne, fOther)
    return (fLighter + 0.05) / (fDarker + 0.05)


def _tCompositeOver(tOver, tUnder):
    """Return tOver blended onto opaque tUnder by tOver's alpha.

    A translucent highlight is never seen on its own -- what a
    researcher looks at is the blend -- so the blend is what must be
    measured.
    """
    fAlpha = tOver[3]
    return tuple(
        round(fAlpha * fOver + (1.0 - fAlpha) * fUnder)
        for fOver, fUnder in zip(tOver[:3], tUnder[:3])
    )


@pytest.fixture(name="dictThemeColours")
def fdictThemeColours():
    sSource = (REPO_ROOT / S_THEME_SOURCE).read_text(encoding="utf-8")
    return {
        sKey: _ftReadThemeColour(sSource, sKey)
        for sKey in ("background", "foreground",
                     "selectionBackground", "selectionForeground")
    }


@pytest.mark.falsification
def testTheSelectionHighlightIsVisibleAgainstThePane(dictThemeColours):
    """The highlight must be distinguishable from an unselected pane.

    Kills: the selection alpha returned to 0.3 -> this assertion fails
    at 1.66:1.
    """
    tBackground = dictThemeColours["background"]
    tSelection = _tCompositeOver(
        dictThemeColours["selectionBackground"], tBackground,
    )
    fRatio = _fContrastRatio(tSelection, tBackground)
    assert fRatio >= F_MINIMUM_STATE_CONTRAST, (
        f"the selection highlight sits at {fRatio:.2f}:1 against the "
        f"pane background, under WCAG 1.4.11's "
        f"{F_MINIMUM_STATE_CONTRAST}:1 for a user-interface state. A "
        "researcher cannot tell whether anything is selected, which "
        "makes a working copy shortcut look broken"
    )


def testSelectedTextStaysReadableOnTheHighlight(dictThemeColours):
    """Visibility must not be bought with unreadable text.

    The counterweight to the assertion above: they pull against each
    other, and while the selected text inherited the pane's own colour
    no alpha satisfied both at once.
    """
    tSelection = _tCompositeOver(
        dictThemeColours["selectionBackground"],
        dictThemeColours["background"],
    )
    fRatio = _fContrastRatio(
        dictThemeColours["selectionForeground"], tSelection,
    )
    assert fRatio >= F_MINIMUM_TEXT_CONTRAST, (
        f"selected text sits at {fRatio:.2f}:1 on its own highlight, "
        f"under WCAG 1.4.3's {F_MINIMUM_TEXT_CONTRAST}:1. Raising the "
        "highlight's visibility must not be paid for by making the "
        "text it covers unreadable"
    )


def testTheSelectionNamesItsOwnForeground(dictThemeColours):
    """An explicit foreground is what decouples the two ratios.

    Left to inherit, the selected text's colour moves with the pane's
    and the two assertions above become one constraint that no single
    alpha satisfies. This is the line that keeps them independent.
    """
    assert dictThemeColours["selectionForeground"][3] == 1.0, (
        "selectionForeground must be an opaque colour: a translucent "
        "one blends with the highlight beneath it, so the ratio "
        "measured above is not the one a researcher sees"
    )
