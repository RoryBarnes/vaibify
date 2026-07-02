"""Frontend contract checks for the dashboard status columns.

Three researcher-reported usability defects drove this layout
(2026-07-02):

1. The step-status light floated loose in each row, so the left-most
   "column" of indicators had no header and was unidentifiable (the
   researcher guessed it meant dependencies). The light now renders
   inside the right-pinned level strip as a labeled column with a
   plain-English hover title.
2. The "Workflow" header row read as a summary of the step rows. It
   is workflow-scope requirements only, so it is now labeled
   "Workflow-wide" and its tooltip says so without jargon.
3. The AICS and Repos tabs were only handed the container id on the
   no-workflow connect path (``fnEnterNoWorkflow``); opening an
   existing workflow (``_fnActivateWorkflow``) — the path every
   researcher actually takes — left both tabs in their "connect
   first" empty states forever.

JavaScript is not executed by the repository test suite; these are
string-presence + structural assertions in the established
frontend-contract pattern.
"""

import os

_sStaticDir = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "vaibify", "gui", "static",
)


def _fsReadStaticFile(sName):
    sPath = os.path.join(_sStaticDir, sName)
    with open(sPath, "r", encoding="utf-8") as fileHandle:
        return fileHandle.read()


def _fsExtractFunctionBlock(sSource, sFunctionName):
    iStart = sSource.find("function " + sFunctionName)
    assert iStart != -1, sFunctionName + " missing from source"
    iNext = sSource.find("\n    function ", iStart + 1)
    return sSource[iStart:iNext if iNext != -1 else len(sSource)]


# -----------------------------------------------------------------------
# Status light is a labeled column inside the level strip
# -----------------------------------------------------------------------


def test_status_light_renders_inside_the_level_strip():
    """The step-status light must render through the strip builder so
    it aligns under the labeled column header, not float loose in the
    row where it cannot be identified."""
    sSource = _fsReadStaticFile("scriptStepRenderer.js")
    sStrip = _fsExtractFunctionBlock(sSource, "_fsBuildStepLevelStrip")
    assert "step-status-cell" in sStrip, (
        "the level strip must lead with the step-status column cell"
    )
    sItem = _fsExtractFunctionBlock(sSource, "fsRenderStepItem")
    assert "_fsBuildStepStatusCell" in sItem, (
        "step rows must build the status light via the shared cell "
        "builder so it lands in the labeled column"
    )


def test_column_header_row_labels_status_and_warning_columns():
    """The one-time header row must label all five columns; the ● and
    ⚠ headers carry plain-English hover titles."""
    sSource = _fsReadStaticFile("scriptStepRenderer.js")
    sHeader = _fsExtractFunctionBlock(
        sSource, "_fsRenderLevelColumnHeaderRow",
    )
    assert "step-status-cell" in sHeader, (
        "header row must include a cell over the status-light column"
    )
    assert "Step status" in sHeader, (
        "status column header must explain itself in plain English"
    )
    assert "&#9888;" in sHeader, (
        "warning column header must be visible (⚠), not an empty span"
    )


def test_status_light_titles_cover_every_dot_state():
    """Every status class the renderer can emit must have a
    plain-English hover phrase."""
    sSource = _fsReadStaticFile("scriptStepRenderer.js")
    iStart = sSource.find("_DICT_STEP_STATUS_TITLES")
    assert iStart != -1, "status-title dict missing"
    sBlock = sSource[iStart:sSource.find("};", iStart)]
    for sState in (
        '""', '"pass"', '"fail"', '"queued"', '"running"',
        '"skipped"', '"partial"', '"verified"',
    ):
        assert sState in sBlock, (
            "status-title dict missing phrase for " + sState
        )


# -----------------------------------------------------------------------
# No hover-edit affordance on step rows
# -----------------------------------------------------------------------


def test_step_rows_carry_no_hover_edit_button():
    """Hand-editing steps is deliberately de-emphasized: the hover
    pencil button is retired. The right-click context menu remains
    the one manual entry point."""
    for sName in ("scriptStepRenderer.js", "scriptEventBindings.js"):
        sSource = _fsReadStaticFile(sName)
        assert "step-edit" not in sSource, (
            sName + " reintroduces the retired hover-edit button"
        )
        assert "step-actions" not in sSource, (
            sName + " reintroduces the retired hover-actions span"
        )
    sApplication = _fsReadStaticFile("scriptApplication.js")
    assert 'sAction === "edit"' in sApplication, (
        "the context-menu edit path must survive as the one manual "
        "entry point to the step editor"
    )


# -----------------------------------------------------------------------
# Workflow-wide row labeling
# -----------------------------------------------------------------------


def test_workflow_row_is_labeled_workflow_wide():
    """The header row must read "Workflow-wide", not "Workflow" — the
    bare word reads as a summary of the step rows, which it is not."""
    sSource = _fsReadStaticFile("scriptStepRenderer.js")
    sHeader = _fsExtractFunctionBlock(
        sSource, "fsRenderWorkflowLevelHeader",
    )
    assert "Workflow-wide" in sHeader
    assert ">Workflow<" not in sHeader, (
        "bare 'Workflow' label reads as an aggregate of the steps"
    )


def test_workflow_scope_tooltip_is_plain_english():
    """The Workflow-wide cell tooltip must explain the scoping without
    internal jargon (no "AICS chip", no "wire", no "scope")."""
    sSource = _fsReadStaticFile("scriptApplication.js")
    assert "These requirements apply to the workflow" in sSource, (
        "workflow-scope tooltip must state the scoping plainly"
    )
    assert "AICS chip" not in sSource, (
        "user-visible text must not reference the 'AICS chip' — "
        "researchers do not know what a chip is"
    )


# -----------------------------------------------------------------------
# Consolidated ⚠ column (2026-07-02): one glyph per step, every reason
# in its tooltip, no inline glyphs beside the step name
# -----------------------------------------------------------------------


def test_step_rows_render_no_inline_warning_glyphs():
    """All step warnings live in the ⚠ column; the collapsed row must
    not render the retired inline badges (pencil, unseeded ⚠,
    modified-files ⚠, blocker banner glyph)."""
    sSource = _fsReadStaticFile("scriptStepRenderer.js")
    sItem = _fsExtractFunctionBlock(sSource, "fsRenderStepItem")
    for sRetired in (
        "script-modified-badge", "script-unseeded-badge",
        "fsBuildWarningBadge", "step-blocker-glyph",
    ):
        assert sRetired not in sItem, (
            "fsRenderStepItem reintroduces the retired inline glyph "
            + sRetired
        )


def test_warning_cell_composes_every_reason():
    """``fdictRegressionWarning`` must compose the backend level
    warning with the step staleness signals — one plain-English line
    each — instead of passing the backend entry through alone."""
    sSource = _fsReadStaticFile("scriptApplication.js")
    sWarning = _fsExtractFunctionBlock(
        sSource, "fdictRegressionWarning",
    )
    assert "_flistStepWarningReasons" in sWarning
    sReasons = _fsExtractFunctionBlock(
        sSource, "_flistStepWarningReasons",
    )
    for sSignal in (
        "dictStepLevelWarnings", "dictBlockersByStep",
        "dictScriptModified", "listModifiedFiles",
        "fbAnyDepTimingStale", "bUnseededRandomnessWarning",
    ):
        assert sSignal in sReasons, (
            "consolidated warning reasons must include " + sSignal
        )


def test_warning_tooltips_never_call_results_green():
    """Tooltip language says "verified", never "green" — the
    dashboard does not use that color for success."""
    sSource = _fsReadStaticFile("scriptApplication.js")
    for sDictName in (
        "_DICT_BLOCKER_CRITERION_GLYPHS", "_DICT_AXIS_SUBSTATE_GLYPHS",
    ):
        iStart = sSource.find(sDictName + " = {")
        assert iStart != -1
        sBlock = sSource[iStart:sSource.find("};", iStart)]
        # The wire criterion KEY "axis-not-green" is a backend
        # literal and must stay; only the researcher-facing labels
        # are held to the wording rule.
        sLabels = sBlock.replace('"axis-not-green"', "")
        assert "green" not in sLabels, (
            sDictName + " tooltip labels must not call results "
            "'green'"
        )
    sTitles = _fsReadStaticFile("scriptStepRenderer.js")
    iStart = sTitles.find("_DICT_STEP_STATUS_TITLES")
    sBlock = sTitles[iStart:sTitles.find("};", iStart)]
    assert "green" not in sBlock


def test_attested_step_without_outputs_gets_a_status_light():
    """FALSIFICATION TARGET: an attestation-only step (AI
    Declaration) has no output files, but a researcher sign-off is
    activity — the dot must not fall through to the grey "no results
    yet" state."""
    sSource = _fsReadStaticFile("scriptApplication.js")
    sDot = _fsExtractFunctionBlock(sSource, "fsComputeStepDotState")
    assert "bAttested" in sDot and "!bHasData && !bAttested" in sDot, (
        "fsComputeStepDotState must treat an attestation as "
        "activity for output-less steps"
    )


# -----------------------------------------------------------------------
# AICS tab: level wording and the header progression links
# -----------------------------------------------------------------------


def test_aics_tab_prefers_level_wording_in_visible_text():
    """User-visible AICS strings say "Level N", not the "L?"
    shorthand."""
    sSource = _fsReadStaticFile("scriptAicsTab.js")
    for sJargon in (
        "L3 Attestation", "verifiers green", "Verify L3 ",
        '"L3 verification',
    ):
        assert sJargon not in sSource, (
            "AICS tab shows the retired shorthand: " + sJargon
        )
    assert "Level 3 Attestation" in sSource


def test_aics_level1_segment_navigates_to_the_step_list():
    """The "Self-Consistent (N steps blocking)" segment used to
    scroll to the card it sits in — a dead click. It must switch to
    the Steps tab, where the blocked steps live."""
    sSource = _fsReadStaticFile("scriptAicsTab.js")
    sScroll = _fsExtractFunctionBlock(sSource, "_fnScrollToReadiness")
    assert '"L1"' in sScroll and 'data-panel="steps"' in sScroll, (
        "the Level 1 progression segment must navigate to the step "
        "list instead of scrolling to its own card"
    )


# -----------------------------------------------------------------------
# Repos tab attention badge
# -----------------------------------------------------------------------


def test_repos_tab_shows_attention_badge():
    """Repository status must be visible without opening the panel:
    the Repos tab carries a count of undecided repositories."""
    sSource = _fsReadStaticFile("scriptReposPanel.js")
    assert "repo-attention-badge" in sSource
    assert "_fnUpdateRepoTabBadge" in sSource
    sCss = _fsReadStaticFile("styleMain.css")
    assert ".repo-attention-badge" in sCss


# -----------------------------------------------------------------------
# Workflow-wide envelope (2026-07-02 redesign): expandable sections,
# theme-tinted checks for passing items, one home for repo status
# -----------------------------------------------------------------------


def test_envelope_sections_are_expandable():
    """The four envelope sections (Software / Artifacts /
    Determinism / Syncs) render as independently expandable headers
    with a summary mark, not a flat list."""
    sSource = _fsReadStaticFile("scriptStepRenderer.js")
    sDetail = _fsExtractFunctionBlock(
        sSource, "fsRenderWorkflowEnvelopeDetail",
    )
    for sKey in ('"software"', '"artifacts"', '"determinism"',
                 '"syncs"'):
        assert sKey in sDetail, "missing envelope section " + sKey
    assert "envelope-section-header" in sSource
    assert "data-envelope-section" in sSource
    sBindings = _fsReadStaticFile("scriptEventBindings.js")
    assert ".envelope-section-header" in sBindings, (
        "section headers must be click-bound to the toggle"
    )


def test_envelope_passing_items_use_the_vaibify_check_not_green():
    """Passing envelope items render the theme-tinted check (its
    color climbs the ladder with --highlight-color); green circles
    are retired."""
    sSource = _fsReadStaticFile("scriptStepRenderer.js")
    sMark = _fsExtractFunctionBlock(sSource, "_fsBuildEnvelopeMark")
    assert "envelope-check" in sMark
    assert "envelope-light-green" not in sSource, (
        "the green envelope circle is retired"
    )
    sCss = _fsReadStaticFile("styleMain.css")
    assert ".envelope-light-green" not in sCss
    iStart = sCss.find(".envelope-check")
    assert iStart != -1
    assert "--highlight-color" in sCss[iStart:iStart + 200], (
        "the envelope check must take the theme color so it climbs "
        "the ladder"
    )


def test_envelope_software_section_points_at_the_repos_panel():
    """Repository status has ONE home (the Repos panel); the
    Software section links there instead of duplicating it."""
    sSource = _fsReadStaticFile("scriptStepRenderer.js")
    sBody = _fsExtractFunctionBlock(
        sSource, "_fsRenderEnvelopeSoftwareBody",
    )
    assert "envelope-open-repos" in sBody
    sBindings = _fsReadStaticFile("scriptEventBindings.js")
    assert 'data-panel="repos"' in sBindings, (
        "the jump link must activate the Repos tab"
    )


# -----------------------------------------------------------------------
# Repos panel: single status indicator, working gear menu
# -----------------------------------------------------------------------


def test_repo_rows_have_one_status_indicator_with_tooltips():
    """The dot is the status; no redundant "clean" caption. Dirty and
    missing repos explain what to do in the dot tooltip."""
    sSource = _fsReadStaticFile("scriptReposPanel.js")
    sRow = _fsExtractFunctionBlock(sSource, "_fsRenderRepoRow")
    assert "_fsStatusLabel" not in sRow, (
        "the textual clean/dirty caption is retired — the dot plus "
        "tooltip carries the status"
    )
    sTooltip = _fsExtractFunctionBlock(sSource, "_fsStatusTooltip")
    assert "Push" in sTooltip and "re-clone" in sTooltip, (
        "dirty/missing tooltips must say what to do next"
    )


def test_repo_gear_menu_survives_its_opening_click():
    """The gear menu reuses .container-tile-menu styling, and the
    landing page's document-level click handler hides every such menu
    on any click — including the click that opens it. The gear
    handler must stop propagation."""
    sSource = _fsReadStaticFile("scriptReposPanel.js")
    iStart = sSource.find('.closest(".repo-gear-btn")')
    assert iStart != -1
    sBlock = sSource[iStart:iStart + 400]
    assert "stopPropagation" in sBlock, (
        "gear click must stopPropagation or the landing page's "
        "hide-all-menus handler closes the menu as it opens"
    )


# -----------------------------------------------------------------------
# Container-scoped tabs are wired on the workflow-activation path
# -----------------------------------------------------------------------


def test_activate_workflow_wires_aics_and_repos_tabs():
    """FALSIFICATION TARGET: ``_fnActivateWorkflow`` must hand the
    container id to the AICS tab and initialize the Repos panel.
    Before 2026-07-02 only ``fnEnterNoWorkflow`` did, so both tabs
    showed their "connect first" empty states during every actual
    workflow session."""
    sSource = _fsReadStaticFile("scriptApplication.js")
    sActivate = _fsExtractFunctionBlock(sSource, "_fnActivateWorkflow")
    assert "VaibifyAicsTab.fnSetContainerId(sId)" in sActivate, (
        "workflow activation must wire the AICS tab or it renders "
        "'Connect to a workflow to see AICS status' while connected"
    )
    assert "PipeleyenReposPanel.fnInit(sId)" in sActivate, (
        "workflow activation must initialize the Repos panel or the "
        "Repos tab stays empty for the whole session"
    )
