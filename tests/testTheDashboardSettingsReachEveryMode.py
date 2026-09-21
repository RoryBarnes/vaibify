"""Two ways the dashboard hid something a researcher needed.

Both were reported on the same evening (2026-09-21), both in Blank
Project mode, and both are the same shape: a control rendered into a
place that mode does not show, or rendered with styling that mode's
background makes unreadable.

1. The project banner wrote an ``<a>`` into the toolbar. No stylesheet
   rule ever named its class, so it took the browser's default link
   colour — dark blue on a dark toolbar — and the one line naming how
   many projects were available could not be read.
2. The two host-global timeouts lived in the project settings gear,
   which sits in the Steps panel. Blank Project mode does not render
   that panel, so the researcher whose blank-project session had just
   timed out was the one researcher who could not reach the control
   that prevents it.

These are source-level guards. The browser lane asserts the rendered
result; what is pinned here is the structural property each fix rests
on, because that is what a later edit would quietly undo.
"""

import pathlib
import re

import pytest


S_STATIC = pathlib.Path("vaibify/gui/static")


def _fsReadStatic(sName):
    """Return one static file's text."""
    return (S_STATIC / sName).read_text(encoding="utf-8")


def _fsExtractFunctionBody(sSource, sFunctionName):
    """Return the text of one JavaScript function, brace-matched."""
    iStart = sSource.index("function " + sFunctionName)
    iBrace = sSource.index("{", iStart)
    iDepth = 0
    for iIndex in range(iBrace, len(sSource)):
        if sSource[iIndex] == "{":
            iDepth += 1
        elif sSource[iIndex] == "}":
            iDepth -= 1
            if iDepth == 0:
                return sSource[iStart:iIndex + 1]
    raise AssertionError(f"unbalanced braces in {sFunctionName}")


@pytest.mark.falsification
def testTheProjectBannerWritesTextRatherThanMarkup():
    """The banner may not introduce an element with styling of its own.

    The independent oracle is CSS's own cascade: an element the
    stylesheet does not name takes the user agent's defaults, and the
    user agent's default for a link is a dark blue chosen for white
    paper. The toolbar is dark. So any element written into the
    toolbar identity span must either inherit the span's colour or
    carry a rule — and text inherits by construction, where markup is
    a standing invitation to forget.

    Kills: restoring the ``<a class="toolkit-banner-switch">`` the
    banner used to write into ``#activeWorkflowName``.
    """
    sBody = _fsExtractFunctionBody(
        _fsReadStatic("scriptApplication.js"), "_fnRenderToolkitBanner",
    )
    assert "innerHTML" not in sBody, (
        "the project banner must set textContent; markup here inherits "
        "no toolbar styling and is how the switcher became illegible"
    )
    assert "<" not in sBody.split("/*", 1)[0] + sBody.rsplit("*/", 1)[-1], (
        "no element may be constructed in the project banner"
    )


def testEveryClassTheProjectBannerAreaUsesIsStyled():
    """A class with no rule anywhere is styling that does not exist."""
    sStyles = "".join(
        pathFile.read_text(encoding="utf-8")
        for pathFile in S_STATIC.glob("*.css")
    )
    for sClass in ("toolbar-workflow", "host-settings-button",
                   "host-settings-panel", "configuration-drift-banner"):
        assert "." + sClass in sStyles, (
            f"{sClass} is used but no stylesheet defines it, so whatever "
            "it renders takes browser defaults"
        )


@pytest.mark.falsification
def testTheHostTimeoutsAreReachableWithoutAnOpenProject():
    """The session cap must not live behind a panel Blank Project hides.

    The independent oracle is the mode table itself: Blank Project mode
    lists the left-hand tabs it renders, and ``steps`` is not among
    them. So a control rendered into the Steps panel is, by that
    table's own definition, unreachable there — and the session cap is
    a HOST setting that applies identically in both modes.

    Kills: moving the timeout rows back into the project settings
    panel (``fsGlobalSettingsHtml``), whose renderer also returns
    early when no project is open.
    """
    sScript = _fsReadStatic("scriptApplication.js")
    sIndex = _fsReadStatic("index.html")

    sProjectPanel = _fsExtractFunctionBody(sScript, "fsGlobalSettingsHtml")
    assert "fsTimeoutSettingsRowsHtml" not in sProjectPanel, (
        "the host timeouts must not be rendered into the project "
        "settings panel; that panel is hidden in Blank Project mode"
    )

    sHostPanel = _fsExtractFunctionBody(sScript, "fnRenderHostSettings")
    assert "fsTimeoutSettingsRowsHtml" in sHostPanel
    assert 'getElementById("hostSettingsPanel")' in sHostPanel

    # The gear is in the toolbar, which every dashboard mode renders,
    # and the mode table never hides it.
    assert 'id="btnHostSettings"' in sIndex
    iToolbar = sIndex.index('<div id="toolbar">')
    iPanelLeft = sIndex.index('<div id="panelLeft">')
    iGear = sIndex.index('id="btnHostSettings"')
    assert iToolbar < iGear < iPanelLeft, (
        "the host-settings gear must live in the toolbar, not in a "
        "left-hand panel that a dashboard mode can hide"
    )


def testNoDashboardModeHidesTheHostSettingsGear():
    """The mode table may hide panels and menus, never the host gear."""
    sScript = _fsReadStatic("scriptApplication.js")
    sVisibility = _fsExtractFunctionBody(sScript, "fnApplyToolbarVisibility")
    assert "btnHostSettings" not in sVisibility, (
        "hiding the host gear per mode would recreate the bug: the "
        "session cap applies in every mode"
    )


def testTheHostPanelIsBoundOnceAtStartup():
    """A binding that depended on the mode is how this became unreachable."""
    sBindings = _fsReadStatic("scriptEventBindings.js")
    assert "fnBindHostSettingsToggle" in sBindings
    sScript = _fsReadStatic("scriptApplication.js")
    assert re.search(
        r"fnBindHostSettingsToggle\(\);", sScript,
    ), "the toggle must be bound from fnInitialize"
