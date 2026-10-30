"""Every text-grade color token is legible on every resting surface.

A banner's links fell to the browser's default link color, a dark blue
drawn for white pages, and read at about 1.2:1 against this theme's
background (2026-10-30). Two contracts close that class of defect at
the palette, where it can be checked without a browser:

- every token the lists below declare TEXT-GRADE keeps the WCAG 2.1
  minimum contrast for body text, 4.5:1 (Success Criterion 1.4.3),
  against every opaque surface token, in each stylesheet that carries
  a palette;
- a link with no rule of its own takes a text-grade color, so no link
  anywhere can fall through to the browser default.

What this cannot see, it does not claim: which text sits on which
surface, a color written outside the token block, a translucent layer
between the two. The browser lane's sweep,
``tests/browser/testVisibleTextIsLegible.py``, measures the rendered
page for those. A token pair below the minimum is recorded in
``DICT_KNOWN_SHORTFALLS`` with its reason; that record may only
shrink, and a pair that comes to pass must leave it.

Relative luminance and the contrast ratio are the W3C definitions,
https://www.w3.org/TR/WCAG21/#dfn-contrast-ratio; the independent
oracle for the expected values is that formula applied by hand to the
hex values in the stylesheet, not the code under test.
"""

import re
from pathlib import Path

import pytest

PATH_STATIC = Path(__file__).resolve().parents[1] / "vaibify" / "gui" / "static"

F_TEXT_MINIMUM_CONTRAST = 4.5

# The tokens each stylesheet uses as the color OF TEXT. A token absent
# here is a fill, a border or a mark (styleMain's own comment grades
# --color-red as fill-grade, below the text minimum by design), and is
# not claimed legible as prose.
DICT_TEXT_GRADE_TOKENS = {
    "styleMain.css": (
        "text-primary", "text-secondary", "text-muted", "color-label",
        "color-red-text", "highlight-color", "color-blue", "color-orange",
        "color-purple", "proof-level-1-color", "proof-level-2-color",
        "proof-level-3-color",
    ),
    "styleSetupWizard.css": (
        "text-primary", "text-secondary", "text-muted", "color-label",
        "color-pale-blue", "color-orange", "color-purple",
    ),
}

# (stylesheet, text token, surface token) -> why it is still short. The
# hover surface is lighter than every resting surface, and a muted
# token brighter than --text-secondary would invert the text hierarchy
# it exists to express; the hovered row is also transient.
DICT_KNOWN_SHORTFALLS = {
    ("styleMain.css", "text-muted", "bg-hover"):
        "muted text on a hovered row reads at 4.06:1 (2026-10-30)",
    ("styleSetupWizard.css", "text-muted", "bg-hover"):
        "muted text on a hovered row reads at 4.06:1 (2026-10-30)",
}

RE_TOKEN = re.compile(r"--([\w-]+)\s*:\s*([^;]+);")
RE_VAR = re.compile(r"var\(--([\w-]+)\)")
RE_HEX = re.compile(r"#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})\b")
RE_BASE_LINK_RULE = re.compile(r"a:where\(\[href\]\)\s*\{([^}]*)\}")


def _fsReadStylesheet(sName):
    return (PATH_STATIC / sName).read_text(encoding="utf-8")


def _fdictReadRootTokens(sStylesheet):
    """Return the custom properties of the first ``:root`` block, by name."""
    iStart = sStylesheet.index(":root")
    sBlock = sStylesheet[iStart:sStylesheet.index("}", iStart)]
    return {sName: sValue.strip() for sName, sValue in RE_TOKEN.findall(sBlock)}


def _ftResolveOpaqueRgb(dictTokens, sValue):
    """Return the (r, g, b) a value names, or None when it is not one opaque color."""
    matchVariable = RE_VAR.fullmatch(sValue.strip())
    if matchVariable:
        return _ftResolveOpaqueRgb(dictTokens, dictTokens.get(matchVariable.group(1), ""))
    matchHex = RE_HEX.fullmatch(sValue.strip())
    if not matchHex:
        return None
    sHex = matchHex.group(1)
    if len(sHex) == 3:
        sHex = "".join(sCharacter * 2 for sCharacter in sHex)
    return tuple(int(sHex[iOffset:iOffset + 2], 16) for iOffset in (0, 2, 4))


def _ffChannelLuminance(iChannel):
    fChannel = iChannel / 255
    if fChannel <= 0.03928:
        return fChannel / 12.92
    return ((fChannel + 0.055) / 1.055) ** 2.4


def ffRelativeLuminance(tRgb):
    """Return the WCAG relative luminance of an sRGB color."""
    iRed, iGreen, iBlue = tRgb
    return (0.2126 * _ffChannelLuminance(iRed) + 0.7152 * _ffChannelLuminance(iGreen)
            + 0.0722 * _ffChannelLuminance(iBlue))


def ffContrastRatio(tForeground, tBackground):
    """Return the WCAG contrast ratio of two opaque colors, at least 1."""
    fLighter = max(ffRelativeLuminance(tForeground), ffRelativeLuminance(tBackground))
    fDarker = min(ffRelativeLuminance(tForeground), ffRelativeLuminance(tBackground))
    return (fLighter + 0.05) / (fDarker + 0.05)


def _fdictOpaqueSurfaces(dictTokens):
    """Return every ``--bg-*`` token that names one opaque color."""
    dictSurfaces = {}
    for sName, sValue in dictTokens.items():
        tRgb = _ftResolveOpaqueRgb(dictTokens, sValue) if sName.startswith("bg-") else None
        if tRgb:
            dictSurfaces[sName] = tRgb
    return dictSurfaces


def _fdictMeasurePairs(sName):
    """Return {(text token, surface token): ratio} for one stylesheet."""
    dictTokens = _fdictReadRootTokens(_fsReadStylesheet(sName))
    dictSurfaces = _fdictOpaqueSurfaces(dictTokens)
    assert dictSurfaces, f"{sName} declares no opaque --bg-* token"
    dictRatios = {}
    for sToken in DICT_TEXT_GRADE_TOKENS[sName]:
        tText = _ftResolveOpaqueRgb(dictTokens, dictTokens.get(sToken, ""))
        assert tText, f"{sName}: --{sToken} is not an opaque color token"
        for sSurface, tSurface in dictSurfaces.items():
            dictRatios[(sToken, sSurface)] = ffContrastRatio(tText, tSurface)
    return dictRatios


def testTheOracleAgreesWithTheHandComputedReferencePairs():
    """White on black is 21:1 and a color against itself is 1:1, by definition."""
    assert round(ffContrastRatio((255, 255, 255), (0, 0, 0)), 2) == 21.0
    assert ffContrastRatio((30, 30, 46), (30, 30, 46)) == 1.0
    # The browser's default link blue on --bg-primary: the defect itself.
    assert round(ffContrastRatio((0, 0, 238), (30, 30, 46)), 2) == 1.75


@pytest.mark.falsification
def testEveryTextGradeTokenIsLegibleOnEveryOpaqueSurface():
    """Kills: putting --text-muted back to #8080a0, 3.6:1 on a modal."""
    listShort = [
        f"{sName}: --{sToken} on --{sSurface} = {fRatio:.2f}:1"
        for sName in sorted(DICT_TEXT_GRADE_TOKENS)
        for (sToken, sSurface), fRatio in sorted(_fdictMeasurePairs(sName).items())
        if fRatio < F_TEXT_MINIMUM_CONTRAST
        and (sName, sToken, sSurface) not in DICT_KNOWN_SHORTFALLS
    ]
    assert listShort == [], (
        f"text-grade tokens below the WCAG text minimum of "
        f"{F_TEXT_MINIMUM_CONTRAST}:1:\n  " + "\n  ".join(listShort)
    )


def testEveryRecordedShortfallIsStillShort():
    """A pair that came to pass leaves the record; the record only shrinks."""
    listStale = [
        f"{sName}: --{sToken} on --{sSurface} now reads "
        f"{_fdictMeasurePairs(sName)[(sToken, sSurface)]:.2f}:1"
        for sName, sToken, sSurface in sorted(DICT_KNOWN_SHORTFALLS)
        if _fdictMeasurePairs(sName)[(sToken, sSurface)] >= F_TEXT_MINIMUM_CONTRAST
    ]
    assert listStale == [], (
        "these shortfalls are recorded but no longer short; remove them:\n  "
        + "\n  ".join(listStale)
    )


@pytest.mark.falsification
def testALinkWithNoRuleOfItsOwnTakesATextGradeColor():
    """Kills: deleting the base ``a:where([href])`` rule from styleMain.css."""
    sStylesheet = _fsReadStylesheet("styleMain.css")
    matchRule = RE_BASE_LINK_RULE.search(sStylesheet)
    assert matchRule, (
        "styleMain.css has no a:where([href]) rule, so a link with no rule "
        "of its own falls to the browser's default dark blue"
    )
    matchColor = re.search(r"color\s*:\s*([^;]+);", matchRule.group(1))
    assert matchColor, "the base link rule sets no color"
    dictTokens = _fdictReadRootTokens(sStylesheet)
    tLink = _ftResolveOpaqueRgb(dictTokens, matchColor.group(1))
    assert tLink, f"the base link color {matchColor.group(1)!r} is not an opaque token"
    listShort = [
        f"--{sSurface} = {ffContrastRatio(tLink, tSurface):.2f}:1"
        for sSurface, tSurface in sorted(_fdictOpaqueSurfaces(dictTokens).items())
        if ffContrastRatio(tLink, tSurface) < F_TEXT_MINIMUM_CONTRAST
    ]
    assert listShort == [], f"the base link color is short on: {listShort}"
