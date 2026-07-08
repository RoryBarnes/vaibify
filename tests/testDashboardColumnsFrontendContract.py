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


def test_run_light_sits_beside_the_checkbox_not_in_the_strip():
    """The run light is execution-only and belongs to the LEFT
    execution cluster (intent checkbox + fact light); the right strip
    carries only verification (warnings + L1-L3). 2026-07-02 ruling —
    the pre-ladder light that folded in verification signals read as
    a shadow L1."""
    sSource = _fsReadStaticFile("scriptStepRenderer.js")
    sStrip = _fsExtractFunctionBlock(sSource, "_fsBuildStepLevelStrip")
    assert "step-status-cell" not in sStrip, (
        "the verification strip must not carry the run light"
    )
    sItem = _fsExtractFunctionBlock(sSource, "fsRenderStepItem")
    iCheckbox = sItem.find("step-checkbox")
    iLight = sItem.find("_fsBuildStepStatusCell")
    iNumber = sItem.find("step-number")
    assert -1 < iCheckbox < iLight < iNumber, (
        "the run light must render between the checkbox and the "
        "step label"
    )


def test_column_header_row_labels_both_clusters():
    """The one-time header row labels the left execution cluster
    ("Run") and the right verification strip; every header carries a
    plain-English hover title."""
    sSource = _fsReadStaticFile("scriptStepRenderer.js")
    sHeader = _fsExtractFunctionBlock(
        sSource, "_fsRenderLevelColumnHeaderRow",
    )
    assert "run-column-header" in sHeader, (
        "the execution cluster needs its own labeled header"
    )
    assert "Run</span>" in sHeader, (
        "the execution-cluster header must read 'Run'"
    )
    assert "&#9888;" in sHeader, (
        "warning column header must be visible (⚠), not an empty span"
    )


def test_run_light_titles_are_execution_only():
    """The run light speaks only about execution — every run state
    has a hover phrase, and the verification vocabulary (partial /
    verified) must never reappear in it."""
    sSource = _fsReadStaticFile("scriptStepRenderer.js")
    iStart = sSource.find("_DICT_STEP_STATUS_TITLES")
    assert iStart != -1, "run-title dict missing"
    sBlock = sSource[iStart:sSource.find("};", iStart)]
    for sState in (
        '""', '"pass"', '"fail"', '"queued"', '"running"',
        '"skipped"',
    ):
        assert sState in sBlock, (
            "run-title dict missing phrase for " + sState
        )
    for sRetired in ('"partial"', '"verified"'):
        assert sRetired not in sBlock, (
            "run light must not carry verification state " + sRetired
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
    """The block header must read "Workflow-wide", not "Workflow" — the
    bare word reads as a summary of the step rows, which it is not."""
    sSource = _fsReadStaticFile("scriptWorkflowRequirements.js")
    sHeader = _fsExtractFunctionBlock(
        sSource, "fsRenderWorkflowWideBlock",
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


def test_verification_dot_machinery_is_retired():
    """The old dot computation folded attestation, tests, and
    dependencies into one light — a shadow L1. It must stay deleted;
    the run light reads dictStepStatus alone."""
    sApplication = _fsReadStaticFile("scriptApplication.js")
    assert "fsComputeStepDotState" not in sApplication
    sRenderer = _fsReadStaticFile("scriptStepRenderer.js")
    assert "fsComputeStepDotState" not in sRenderer


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


def test_workflow_wide_block_rebuilds_unconditionally():
    """The 2026-07-02 skip-repaint bug (a toggle whose expansion set
    was not in the render signature repainted nothing) is retired
    structurally: the Workflow-wide block lives in its own container
    and is rebuilt on every render, so its group/row expansion Sets
    can never cause a skipped repaint. Guard that the rebuild stays
    unconditional and outside the memoized step-hash path."""
    sSource = _fsReadStaticFile("scriptApplication.js")
    sRebuild = _fsExtractFunctionBlock(
        sSource, "_fnRenderWorkflowWideBlock",
    )
    assert "workflowWideBlock" in sRebuild, (
        "the block must render into its own #workflowWideBlock "
        "container"
    )
    assert "fsRenderWorkflowWideBlock" in sRebuild, (
        "the block must rebuild from the module renderer each pass"
    )
    # It must not be gated behind the boundary signature (which is the
    # step-list full-vs-incremental decision).
    assert "_fsBoundarySignature" not in sRebuild


def test_every_column_header_carries_a_tooltip():
    """The per-step column headers (run, warnings, L1) must explain
    themselves on hover. L2/L3 are workflow-wide, not per-step, so they
    are no longer headed on the step rows."""
    sSource = _fsReadStaticFile("scriptStepRenderer.js")
    sHeader = _fsExtractFunctionBlock(
        sSource, "_fsRenderLevelColumnHeaderRow",
    )
    for sNeedle in (
        "Run controls", "Warnings", "Level 1 Self-Consistent",
    ):
        assert sNeedle in sHeader, (
            "column header missing its tooltip: " + sNeedle
        )
    assert "Level 2 Published" not in sHeader, (
        "L2 is workflow-wide and must not be a per-step column"
    )
    assert "Level 3 Reproducible" not in sHeader, (
        "L3 is workflow-wide and must not be a per-step column"
    )


def test_workflow_wide_groups_and_rows_are_expandable():
    """The Workflow-wide block groups requirements into the four
    envelope categories plus Attestation; each section and each
    requirement row is independently expandable with a status light."""
    sSource = _fsReadStaticFile("scriptWorkflowRequirements.js")
    sBlock = _fsExtractFunctionBlock(
        sSource, "fsRenderWorkflowWideBlock",
    )
    for sKey in ('"software"', '"artifacts"', '"determinism"',
                 '"publishedCopies"', '"attestation"'):
        assert sKey in sBlock, "missing requirement section " + sKey
    assert "data-group=" in sSource
    assert "data-req=" in sSource
    assert "requirement-group-header" in sSource
    assert "requirement-row-header" in sSource
    sBindings = _fsReadStaticFile("scriptEventBindings.js")
    assert ".requirement-group-header" in sBindings, (
        "group headers must be click-bound to the toggle"
    )
    assert ".requirement-row-header" in sBindings, (
        "requirement rows must be click-bound to the toggle"
    )


def test_envelope_passing_items_use_the_vaibify_favicon():
    """Passing requirement items render the vaibify favicon — the same
    'attained' glyph the step level cells use — not a bare check
    character. A never-verified remote stays hollow, never a pass."""
    sSource = _fsReadStaticFile("scriptWorkflowRequirements.js")
    sMark = _fsExtractFunctionBlock(sSource, "_fsBuildEnvelopeMark")
    assert "favicon.png" in sMark and "level-cell-favicon" in sMark
    assert "envelope-light-unknown" in sMark, (
        "the unknown/never-verified state must render hollow"
    )
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


def test_envelope_mark_columns_have_lettered_headers():
    """The Software (V/H) and Artifacts (F/R) mark columns carry
    one-letter headers with instructive tooltips, rendered inside the
    Workflow-wide requirement-row detail bodies."""
    sSource = _fsReadStaticFile("scriptWorkflowRequirements.js")
    assert "_fsRenderEnvelopeMarkHeader" in sSource
    sSoftware = _fsExtractFunctionBlock(
        sSource, "_flistSoftwareRows",
    )
    assert '"V"' in sSoftware and '"H"' in sSoftware
    sArtifact = _fsExtractFunctionBlock(
        sSource, "_fsRenderArtifactDetail",
    )
    assert '"F"' in sArtifact and '"R"' in sArtifact


def test_envelope_summary_marks_show_partial_as_orange():
    """A group with some but not all requirements met summarizes
    orange — never a false check, never an all-red."""
    sSource = _fsReadStaticFile("scriptWorkflowRequirements.js")
    sCounts = _fsExtractFunctionBlock(
        sSource, "_fsSummaryStateFromCounts",
    )
    assert '"orange"' in sCounts and '"red"' in sCounts, (
        "summary marks must distinguish none-met from partially-met"
    )


def test_published_copies_section_names_services_not_syncs():
    """"Syncs" read as software syncing; the Published copies section
    names each published copy by service (GitHub / Zenodo / arXiv)."""
    sSource = _fsReadStaticFile("scriptWorkflowRequirements.js")
    iStart = sSource.find("_DICT_GROUP_TITLES")
    sBlock = sSource[iStart:sSource.find("};", iStart)]
    assert "Published copies" in sBlock
    assert "Syncs" not in sBlock
    assert "GitHub mirror" in sSource and "Zenodo deposit" in sSource


def test_declaration_step_offers_commit_to_repo():
    """The declaration file is canonical; the step body must offer a
    way to commit it — scoped to THAT file only (the full canonical
    checklist confused the researcher), and styled pale blue as the
    routine action (researcher ruling 2026-07-02: commit = pale
    blue, remove = orange danger)."""
    sRenderer = _fsReadStaticFile("scriptStepRenderer.js")
    assert "btn-ai-declaration-commit" in sRenderer
    sBindings = _fsReadStaticFile("scriptEventBindings.js")
    assert '".btn-ai-declaration-commit"' in sBindings
    assert "fsCommitSinglePath" in sBindings, (
        "the declaration button must use the scoped single-path "
        "commit, not the full pre-push checklist"
    )
    sCheck = _fsReadStaticFile("scriptManifestCheck.js")
    assert "listOnlyPaths" in sCheck, (
        "the scoped commit must send listOnlyPaths so the server "
        "narrows the commit"
    )
    sCss = _fsReadStaticFile("styleMain.css")
    iStart = sCss.find(".btn-ai-declaration-commit")
    assert iStart != -1
    assert "--color-pale-blue" in sCss[iStart:iStart + 200], (
        "the routine commit action is pale blue"
    )


def test_declaration_step_offers_both_commit_and_remove():
    """Commit and remove coexist (researcher ruling 2026-07-02): an
    updated declaration needs recommitting even while tracked, so
    commit is always offered, and removal appears once git tracks
    the file. Removal is the dangerous action (orange) and goes
    through the declaration-only endpoint so it can never untrack
    other files."""
    sRenderer = _fsReadStaticFile("scriptStepRenderer.js")
    sButtons = _fsExtractFunctionBlock(
        sRenderer, "_fsBuildDeclarationGitButtons",
    )
    assert "btn-ai-declaration-commit" in sButtons
    assert "btn-ai-declaration-untrack" in sButtons
    sGate = _fsExtractFunctionBlock(
        sRenderer, "_fbDeclarationFileIsTracked",
    )
    for sTrackedState in ('"synced"', '"dirty"', '"drifted"'):
        assert sTrackedState in sGate, (
            "every tracked git state must offer removal"
        )
    sBindings = _fsReadStaticFile("scriptEventBindings.js")
    assert '".btn-ai-declaration-untrack"' in sBindings
    assert "fsRemoveSinglePath" in sBindings
    sCheck = _fsReadStaticFile("scriptManifestCheck.js")
    assert "untrack-ai-declaration" in sCheck, (
        "removal must call the declaration-scoped endpoint, not a "
        "general git route"
    )
    sCss = _fsReadStaticFile("styleMain.css")
    iStart = sCss.find(".btn-ai-declaration-untrack")
    assert iStart != -1
    assert "--color-orange" in sCss[iStart:iStart + 200], (
        "the destructive remove action is orange"
    )


def test_repos_panel_push_toasts_are_honest():
    """FALSIFICATION TARGET (live bug 2026-07-02): the push routes
    return HTTP 200 with bSuccess false on git failures (an
    unconditional ``git commit`` hit "nothing to commit" and the
    push never ran), and the panel toasted "Pushed to remote."
    anyway — a success the dashboard-is-ground-truth rule forbids.
    Both push handlers must check bSuccess before claiming success."""
    sPanel = _fsReadStaticFile("scriptReposPanel.js")
    sGate = _fsExtractFunctionBlock(sPanel, "_fbPushSucceeded")
    assert "bSuccess" in sGate
    assert "error" in sGate, "a failed push must toast an error"
    sOutcome = _fsExtractFunctionBlock(sPanel, "_fnToastPushOutcome")
    assert "sPostPushVerifyWarning" in sOutcome, (
        "a push whose follow-up status check failed must show the "
        "backend's warning, or 'pushed' and 'L2 unknown' contradict"
    )
    for sHandler in ("_fnPostPushStaged", "_fnPostPushFiles"):
        sBlock = _fsExtractFunctionBlock(sPanel, sHandler)
        assert "_fbPushSucceeded" in sBlock, (
            sHandler + " must check the push result before toasting"
        )
        assert "_fnToastPushOutcome" in sBlock
    sSyncManager = _fsReadStaticFile("scriptSyncManager.js")
    assert "sPostPushVerifyWarning" in sSyncManager, (
        "the sync-buttons push path must surface the post-push "
        "verify warning too — it hits the same backend field"
    )


def test_dropped_websocket_actions_are_reported_not_swallowed():
    """FALSIFICATION TARGET (live incident 2026-07-03): a Run clicked
    against a dying socket painted the queued light, parked the
    action in the pending queue, and evaporated when the socket
    closed — the researcher saw "queued" then an unexplained
    disconnect, and the run never reached the server. Three links:
    the socket layer must report that pending actions were dropped,
    the app must tell the researcher their request was NOT
    submitted, and the unreachable toast must carry the close detail
    (the code distinguishes a duplicate-session rejection from a
    network drop from a restart)."""
    sSocket = _fsReadStaticFile("scriptWebSocket.js")
    sEmit = _fsExtractFunctionBlock(
        sSocket, "_fnEmitCloseEventAndDropPending",
    )
    assert "bActionsDropped" in sEmit
    assert "iCode" in sEmit
    sApplication = _fsReadStaticFile("scriptApplication.js")
    sHandlers = _fsExtractFunctionBlock(
        sApplication, "fnRegisterWebSocketHandlers",
    )
    assert "bActionsDropped" in sHandlers, (
        "the close handler must inspect the dropped-actions flag"
    )
    assert "NOT submitted" in sHandlers, (
        "the researcher must be told their request never reached "
        "the server"
    )
    sMonitor = _fsReadStaticFile("scriptConnectionMonitor.js")
    sToast = _fsExtractFunctionBlock(sMonitor, "_fsBuildToastMessage")
    assert "sMessage" in sToast, (
        "the unreachable toast must surface the close detail"
    )


def test_viewer_lease_replaces_a_stale_stored_lease():
    """FALSIFICATION TARGET (live incident 2026-07-03): single-
    container mode mints a server-side lease and hands it to the
    browser in the connect response. The browser MUST replace any
    lease left in sessionStorage by a previous hub — an old lease
    survives a reload (sessionStorage persists), so keeping it makes
    every WebSocket present a foreign lease and fail closed as 1006
    after a hub restart, which no amount of restarting fixes. The
    recorder must only skip when the stored lease already equals the
    served one (a no-op), never merely because SOME lease is held."""
    sApplication = _fsReadStaticFile("scriptApplication.js")
    sBody = _fsExtractFunctionBlock(
        sApplication, "_fnRecordViewerLeaseFromConnect",
    )
    assert "=== dictConnect.sLeaseId" in sBody, (
        "the served lease must replace a differing stored lease; the "
        "skip may only fire when the stored lease already matches"
    )
    assert "if (fsGetLeaseId()) return;" not in sBody, (
        "the unconditional 'already have a lease' guard stranded a "
        "stale lease across hub restarts (the 1006 lockout)"
    )


def test_output_existence_lookup_joins_the_workdir_exactly_once():
    """FALSIFICATION TARGET (live bug 2026-07-03): the renderer joins
    a relative output path with the step workdir when it builds
    data-resolved, and the existence planner then composed the
    workdir in AGAIN — the server was asked about
    'XuvEvolution/XuvEvolution/…', answered 'missing', and every
    existing data file in a repo-relative step directory rendered
    red with no explanation. The join happens exactly once, in the
    renderer; the planner must trust data-resolved verbatim."""
    sRenderer = _fsReadStaticFile("scriptStepRenderer.js")
    sItem = _fsExtractFunctionBlock(sRenderer, "fsRenderDetailItem")
    assert "fsJoinPath(sWorkdir, sResolved)" in sItem, (
        "the renderer owns the one workdir join for output paths"
    )
    sOperations = _fsReadStaticFile("scriptFileOperations.js")
    sPlanItem = _fsExtractFunctionBlock(
        sOperations, "_fdictOutputItemForPlan",
    )
    assert "sLookupPath: sResolved" in sPlanItem, (
        "the planner must send data-resolved verbatim"
    )
    assert "_fsComposeAbsoluteOrRelative" not in sPlanItem, (
        "re-composing data-resolved with the workdir double-joins "
        "relative step directories"
    )


def test_declaration_badge_state_reaches_the_incremental_renderer():
    """FALSIFICATION TARGET (live bug 2026-07-02): the declaration
    buttons gate on the file's git badge, so the incremental renderer
    must (a) map the declaration file to its step in the badge-driven
    partial-render reverse index and (b) carry the badge state in the
    step render hash. Missing either leaves the stale commit-only
    card on screen forever — the researcher hard-refreshed and still
    saw one button because the card rendered before badges loaded and
    was never invalidated."""
    sApplication = _fsReadStaticFile("scriptApplication.js")
    sReverseMap = _fsExtractFunctionBlock(
        sApplication, "_fnIndexStepFilesIntoReverseMap",
    )
    assert "sDeclarationFile" in sReverseMap, (
        "a declaration badge change must invalidate its step's card"
    )
    sHash = _fsExtractFunctionBlock(
        sApplication, "_fsComputeStepRenderHash",
    )
    assert "_fsDeclarationBadgeSlice" in sHash, (
        "the render hash must move when the declaration badge does"
    )
    sSlice = _fsExtractFunctionBlock(
        sApplication, "_fsDeclarationBadgeSlice",
    )
    assert "fdictGetBadgesForFile" in sSlice
    assert "sGithub" in sSlice
    sActivate = _fsExtractFunctionBlock(
        sApplication, "_fnActivateWorkflow",
    )
    assert "VaibifyGitBadges.fnRefresh" in sActivate, (
        "badges must be seeded on workflow activation, or every "
        "badge consumer gates on an empty map until a sync action"
    )


def test_run_light_success_renders_the_vaibify_check():
    """A successful last run renders the vaibify check (the favicon,
    the same mark as an attained level cell — a text glyph reads as
    foreign), and the never-run state is the hollow circle — the
    researcher-approved vocabulary."""
    sRenderer = _fsReadStaticFile("scriptStepRenderer.js")
    sCell = _fsExtractFunctionBlock(
        sRenderer, "_fsBuildStepStatusCell",
    )
    assert "step-status-check" in sCell
    assert "favicon.png" in sCell, (
        "the success mark must be the vaibify check image, not a "
        "text glyph"
    )
    sCss = _fsReadStaticFile("styleMain.css")
    iStart = sCss.find(".step-item .step-status {")
    assert "border: 1.5px solid var(--text-muted)" in (
        sCss[iStart:iStart + 300]
    ), "the never-run light must be the hollow grey circle"


def test_publication_rows_point_at_the_repos_panel():
    """Repository status has ONE home (the Repos panel); the
    Publication requirement rows send the researcher there to push and
    re-verify rather than duplicating repo actions in the block."""
    sSource = _fsReadStaticFile("scriptWorkflowRequirements.js")
    assert "Repos panel" in sSource, (
        "Publication how-to guidance must direct to the Repos panel"
    )
    sBindings = _fsReadStaticFile("scriptEventBindings.js")
    assert 'data-panel="repos"' in sBindings, (
        "the Repos tab jump must stay wired"
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


# -----------------------------------------------------------------------
# The poll delivers workflow-level promotions to the theme
# -----------------------------------------------------------------------


def test_poll_updates_workflow_level_integer_for_the_theme():
    """The file-status poll must copy ``iAICSLevel`` onto the client's
    workflow dict — the theme (``fiClientAICSLevel``) reads exactly
    that integer. Before this contract, the poll updated every level
    CELL but never the workflow-level integer, so a promotion earned
    mid-session showed every step's L1 check while the theme stayed
    at level 0 until a full reload."""
    sSource = _fsReadStaticFile("scriptApplication.js")
    sApply = _fsExtractFunctionBlock(
        sSource, "_fnApplyLevelStatesFromPoll")
    assert "dictWorkflow.iAICSLevel =" in sApply.replace(
        "\n", "").replace("    ", " ").replace("  ", " "), (
        "_fnApplyLevelStatesFromPoll must assign the poll's "
        "iAICSLevel onto the workflow dict the theme reads"
    )
    sSnapshot = _fsExtractFunctionBlock(
        sSource, "_fsBlockerAndLevelSnapshot")
    assert "iAICSLevel" in sSnapshot, (
        "the blocker/level snapshot must include the workflow-level "
        "integer so a promotion alone triggers the re-render that "
        "calls fnUpdateHighlightState (the theme flip)"
    )


def test_client_level_gate_exempts_declaration_steps():
    """``fbStepIsAtLeastLevel1`` must return True for ai-declaration
    steps BEFORE reading verification/data signals. Declaration steps
    are L1-not-applicable (the server emits no L1 blockers for them
    and their sign-off is an L2 criterion), but they have no output
    data and no "passed" user badge — so without the exemption the
    client-side conjunction demoted the whole workflow to level 0 and
    the theme never left the base color, even at server level 1."""
    sSource = _fsReadStaticFile("scriptApplication.js")
    sGate = _fsExtractFunctionBlock(sSource, "fbStepIsAtLeastLevel1")
    iExemption = sGate.find('sStepKind === "ai-declaration"')
    iFirstSignal = sGate.find("fdictGetVerification")
    assert iExemption != -1, (
        "fbStepIsAtLeastLevel1 must exempt ai-declaration steps or "
        "they demote the client-side workflow level forever"
    )
    assert iFirstSignal == -1 or iExemption < iFirstSignal, (
        "the declaration exemption must precede the data/verification "
        "signals a declaration step can never satisfy"
    )
