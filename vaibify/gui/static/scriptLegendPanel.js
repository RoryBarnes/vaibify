/* Vaibify — Help panel: the top-level entry point for new users.

   A dismissible, fixed-position panel opened from the dashboard-header
   ``?`` button. It links to the full online documentation, explains
   how to start the AI coding assistant inside the container (and why
   the sandbox makes that safe), and carries the symbol legend — what
   each mark means, nothing more. The requirements themselves and how
   to meet them live on the PROOF tab. Criterion rows come from
   ``VaibifyApp.fdictBlockerGlyphCatalog`` so the legend cannot
   drift from the glyphs actually rendered. */

var VaibifyLegendPanel = (function () {
    "use strict";

    var fnEscapeHtml = VaibifyUtilities.fnEscapeHtml;

    var _S_PANEL_ID = "proofLegendPanel";
    var _S_BUTTON_ID = "proofLegendButton";
    var _S_DOCUMENTATION_URL = "https://RoryBarnes.github.io/vaibify";
    // A container hosts whichever agents its build features enabled;
    // each command carries that agent's own skip-permissions flag.
    var _LIST_AGENT_START_COMMANDS = [
        {
            sAgentName: "Claude Code",
            sCommand: "claude --dangerously-skip-permissions",
        },
        {
            sAgentName: "Codex",
            sCommand: "codex --dangerously-bypass-approvals-and-sandbox",
        },
        {
            sAgentName: "Gemini",
            sCommand: "gemini --yolo",
        },
    ];
    var _bOpen = false;
    var _bOutsideClickBound = false;

    // Marks that appear on step rows: the execution cluster, the
    // consolidated warning column, and the per-file marks in the
    // expanded detail. Static by design: each entry names the CSS
    // class that styles the live mark, so the sample is colored
    // exactly as the dashboard colors it. Entries with ``sSampleHtml``
    // render that static markup verbatim so the legend sample matches
    // the live cell exactly.
    var _LIST_STEP_MARKS = [
        {
            sSampleHtml: '<input type="checkbox" ' +
                'class="proof-legend-checkbox-sample" disabled>',
            sLabel: "Run checkbox — include this step in the " +
                "next run",
        },
        {
            sIcon: "●", sClass: "proof-legend-orange-light-sample",
            sLabel: "Run light (beside each step's checkbox) — " +
                "execution only: hollow grey = not run this " +
                "session, filled grey = queued, blinking orange = " +
                "running, red = last run failed, blinking red = " +
                "may be hung, quiet pale-blue dot = last run " +
                "succeeded; verification lives in the L1|L2|L3 " +
                "cells",
        },
        {
            sIcon: "⚠", sClass: "step-regression-cell " +
                "regression-warning-red",
            sLabel: "Warning column, red — a test failed; hover " +
                "the glyph for every reason and its remedy",
        },
        {
            sIcon: "⚠", sClass: "step-regression-cell " +
                "regression-warning-orange",
            sLabel: "Warning column, orange — something changed " +
                "since verification (script, outputs, an earlier " +
                "step) or a level regressed; hover for the reasons",
        },
        {
            sIcon: "⚠", sClass: "l1-blocker-file-glyph",
            sLabel: "Offending file or dependency edge — " +
                "blocking verification; re-run the step",
        },
        {
            sIcon: "✎", sClass: "file-mark-stale",
            sLabel: "File changed since its last verified run — " +
                "re-run the step to refresh it",
        },
        {
            sSampleHtml: VaibifyUtilities.fsBuildAttainedFavicon(
                "passing"),
            sLabel: "Test(s) passing (fresh run or restored from a " +
                "committed test marker)",
        },
    ];

    // Marks on the Project-block sections (Repository, Software,
    // Artifacts, Determinism, Published copies, Attestation) and
    // their requirement rows.
    var _LIST_WORKFLOW_MARKS = [
        {
            sSampleHtml: VaibifyUtilities.fsBuildAttainedFavicon(
                "met"),
            sLabel: "Vaibify badge on a section or requirement " +
                "row — the requirement is met",
        },
        {
            sIcon: "⚠", sClass: "envelope-warn",
            sLabel: "Red warning on a requirement row — the " +
                "requirement is failing or the artifact is missing",
        },
        {
            sIcon: "⚠", sClass: "envelope-warn-orange",
            sLabel: "Orange warning on a requirement row — the " +
                "last check is stale; refresh or re-run to update it",
        },
        {
            sSampleHtml: '<span class="envelope-light ' +
                'envelope-light-unknown ' +
                'proof-legend-inline-sample"></span>',
            sLabel: "Hollow grey circle — never checked: refresh " +
                "remote status to find out",
        },
    ];

    // The L1|L2|L3 level-cell vocabulary, shared by step rows, the
    // Steps and Project banners, and the requirement rows. Samples
    // are generated by the shared builder, so they are identical to
    // the live cells by construction, not by hand-maintained promise.
    var _LIST_LEVEL_CELL_MARKS = [
        {
            sSampleHtml: VaibifyUtilities.fsBuildLevelCell(
                "not-started", "legend sample"),
            sLabel: "Hollow grey circle — not started: no outputs " +
                "on disk and no activity at this level yet",
        },
        {
            sSampleHtml: VaibifyUtilities.fsBuildLevelCell(
                "unassessed", "legend sample"),
            sLabel: "Grey filled circle — unassessed: outputs " +
                "exist on disk, but no tests, checks, or sign-off " +
                "have been recorded yet",
        },
        {
            sSampleHtml: VaibifyUtilities.fsBuildLevelCell(
                "none", "legend sample"),
            sLabel: "Red circle — no requirements met",
        },
        {
            sSampleHtml: VaibifyUtilities.fsBuildLevelCell(
                "partial", "legend sample"),
            sLabel: "Orange circle — partially met",
        },
        {
            sSampleHtml: VaibifyUtilities.fsBuildLevelCell(
                "attained", "legend sample", "attained"),
            sLabel: "Vaibify badge — attained: every requirement " +
                "at this level is met",
        },
        {
            sSampleHtml: VaibifyUtilities.fsBuildLevelCell(
                "unknown", "legend sample"),
            sLabel: "Question mark — unknown: GitHub/Zenodo have " +
                "not been checked recently; refresh remote status " +
                "to find out",
        },
        {
            sSampleHtml: VaibifyUtilities.fsBuildLevelCell(
                "not-applicable", "legend sample"),
            sLabel: "Dash — not applicable: no requirements at " +
                "this level",
        },
    ];

    // Per-file remote badges (GitHub, Overleaf, Zenodo, arXiv icons
    // beside file names) and the red file-name text styles. The badge
    // samples reuse the live ``badge-<state>`` classes so each dot is
    // tinted exactly as the dashboard tints the icon.
    var _LIST_FILE_REMOTE_MARKS = [
        {
            sIcon: "●", sClass: "remote-badge badge-synced",
            sLabel: "Badge tinted pale blue — in sync with the " +
                "remote",
        },
        {
            sIcon: "●", sClass: "remote-badge badge-drifted",
            sLabel: "Badge tinted amber — local file differs from " +
                "the last push",
        },
        {
            sIcon: "●", sClass: "remote-badge badge-dirty",
            sLabel: "Badge tinted red — uncommitted local changes",
        },
        {
            sIcon: "●", sClass: "remote-badge badge-untracked",
            sLabel: "Badge tinted blue — not tracked by git",
        },
        {
            sIcon: "●", sClass: "remote-badge badge-ignored",
            sLabel: "Badge muted — git-ignored",
        },
        {
            sIcon: "●", sClass: "remote-badge badge-none",
            sLabel: "Badge faded grey — not synced to this remote",
        },
        {
            sIcon: "file", sClass: "proof-legend-red-missing-sample",
            sLabel: "Red upright file name — declared file missing",
        },
        {
            sIcon: "file", sClass: "proof-legend-red-stale-sample",
            sLabel: "Red dotted-underlined file name — file changed " +
                "since its last test run",
        },
        {
            sIcon: "file", sClass: "proof-legend-red-unattested-sample",
            sLabel: "Red italic file name — present but never " +
                "verified by you",
        },
    ];

    function fnInitialize() {
        var elButton = document.getElementById(_S_BUTTON_ID);
        if (elButton) {
            elButton.addEventListener("click", _fnTogglePanel);
        }
    }

    function _fnTogglePanel() {
        if (_bOpen) {
            fnClose();
        } else {
            fnOpen();
        }
    }

    function fnOpen() {
        var elPanel = document.getElementById(_S_PANEL_ID);
        if (!elPanel) return;
        elPanel.innerHTML = _fsRenderPanelInner();
        elPanel.classList.add("is-open");
        elPanel.setAttribute("aria-hidden", "false");
        _bOpen = true;
        _fnBindCloseButton(elPanel);
        if (!_bOutsideClickBound) {
            document.addEventListener("click", _fnOutsideClick, true);
            _bOutsideClickBound = true;
        }
    }

    function fnClose() {
        var elPanel = document.getElementById(_S_PANEL_ID);
        if (!elPanel) return;
        elPanel.classList.remove("is-open");
        elPanel.setAttribute("aria-hidden", "true");
        _bOpen = false;
        if (_bOutsideClickBound) {
            document.removeEventListener(
                "click", _fnOutsideClick, true);
            _bOutsideClickBound = false;
        }
    }

    function _fnBindCloseButton(elPanel) {
        var elClose = elPanel.querySelector(".proof-legend-close");
        if (elClose) {
            elClose.addEventListener("click", fnClose);
        }
    }

    function _fnOutsideClick(event) {
        var elPanel = document.getElementById(_S_PANEL_ID);
        var elButton = document.getElementById(_S_BUTTON_ID);
        if (!elPanel) return;
        if (elPanel.contains(event.target)) return;
        if (elButton && elButton.contains(event.target)) return;
        fnClose();
    }

    function _fsRenderPanelInner() {
        // Symbols only in the legend: live blocker counts were
        // dropped so the panel cannot become a second, staler status
        // page — status lives on the banner strips and the PROOF tab.
        return _fsRenderHeader() +
            _fsRenderDocumentationSection() +
            _fsRenderUsingAiSection() +
            '<div class="proof-help-heading">Legend</div>' +
            _fsRenderStepsDivision() +
            _fsRenderProjectDivision() +
            _fsRenderLevelLightsDivision() +
            _fsRenderFilesAndRemotesDivision() +
            _fsRenderFooter();
    }

    function _fdictGlyphCatalog() {
        if (VaibifyApp && VaibifyApp.fdictBlockerGlyphCatalog) {
            return VaibifyApp.fdictBlockerGlyphCatalog();
        }
        return {};
    }

    function _fsRenderHeader() {
        return '<div class="proof-legend-header">' +
            '<span>Help</span>' +
            '<button class="proof-legend-close" ' +
            'title="Close">&times;</button>' +
            '</div>';
    }

    function _fsRenderDocumentationSection() {
        return '<div class="proof-help-section proof-help-docs">' +
            '<a href="' + _S_DOCUMENTATION_URL + '" ' +
            'target="_blank" rel="noopener">' +
            'Read the full vaibify documentation</a>' +
            '</div>';
    }

    function _fsRenderUsingAiSection() {
        return '<details class="proof-help-details">' +
            '<summary>Using AI</summary>' +
            '<p>A container can host Claude Code, Codex, or Gemini, ' +
            'depending on the features it was built with. To start ' +
            'an assistant, open a terminal in the container and ' +
            'run its command:</p>' +
            _fsRenderAgentStartCommands() +
            '<p>Each command includes that agent&rsquo;s ' +
            'skip-permissions option so the assistant works without ' +
            'stopping to ask permission for every command.</p>' +
            '<p>The options’ names sound alarming, but inside ' +
            'a vaibify container it is the intended mode. The ' +
            'container is an isolated sandbox: the assistant runs ' +
            'as an unprivileged user with no sudo, and it can only ' +
            'touch files on the container’s workspace volume ' +
            '— never your host machine. Every file it edits ' +
            'is tracked in git, hash-pinned in the project ' +
            'manifest, and ultimately checked by a full rebuild of ' +
            'the analysis — that is what PROOF Level 3 ' +
            '(Reproducible) certifies.</p>' +
            '<p>Your protection therefore comes from verifying ' +
            'results, not from approving each command: the ' +
            'dashboard shows exactly what changed, which steps ' +
            'went stale, and whether the outputs still pass their ' +
            'tests. Per-command permission prompts add friction ' +
            'without adding safety in this environment.</p>' +
            '</details>';
    }

    function _fsRenderAgentStartCommands() {
        var sHtml = "";
        for (var i = 0; i < _LIST_AGENT_START_COMMANDS.length; i++) {
            var dictAgent = _LIST_AGENT_START_COMMANDS[i];
            sHtml += '<p><strong>' +
                fnEscapeHtml(dictAgent.sAgentName) + '</strong></p>' +
                '<code class="proof-help-command">' +
                fnEscapeHtml(dictAgent.sCommand) + '</code>';
        }
        return sHtml;
    }

    function _fsRenderStepsDivision() {
        var dictCatalog = _fdictGlyphCatalog();
        return '<div class="proof-legend-section">' +
            '<div class="proof-legend-section-title">Steps</div>' +
            _fsRenderMarkRows(_LIST_STEP_MARKS) +
            '<div class="proof-legend-subsection-title">' +
            'Why a step shows a warning</div>' +
            _fsRenderCriteriaRows(dictCatalog.iLevel1 || {}) +
            _fsRenderAxisSubStateRows() +
            '</div>';
    }

    function _fsRenderProjectDivision() {
        var dictCatalog = _fdictGlyphCatalog();
        return '<div class="proof-legend-section">' +
            '<div class="proof-legend-section-title">' +
            'Project</div>' +
            _fsRenderMarkRows(_LIST_WORKFLOW_MARKS) +
            '<div class="proof-legend-subsection-title">' +
            'Publication warnings (Level 2)</div>' +
            _fsRenderCriteriaRows(dictCatalog.iLevel2 || {}) +
            '<div class="proof-legend-subsection-title">' +
            'Reproducibility warnings (Level 3)</div>' +
            _fsRenderCriteriaRows(dictCatalog.iLevel3 || {}) +
            '</div>';
    }

    function _fsRenderLevelLightsDivision() {
        return '<div class="proof-legend-section">' +
            '<div class="proof-legend-section-title">' +
            'Level status lights</div>' +
            '<div class="proof-legend-division-note">The ' +
            'L1&thinsp;|&thinsp;L2&thinsp;|&thinsp;L3 cells on ' +
            'step rows, both banners, and requirement rows:</div>' +
            _fsRenderMarkRows(_LIST_LEVEL_CELL_MARKS) +
            '</div>';
    }

    function _fsRenderFilesAndRemotesDivision() {
        return '<div class="proof-legend-section">' +
            '<div class="proof-legend-section-title">' +
            'Files and remotes</div>' +
            '<div class="proof-legend-division-note">GitHub, ' +
            'Overleaf, Zenodo, and arXiv badges beside file ' +
            'names, and the file-name text styles:</div>' +
            _fsRenderMarkRows(_LIST_FILE_REMOTE_MARKS) +
            '</div>';
    }

    function _fsRenderAxisSubStateRows() {
        // Why a test shows a warning: rows drawn from the same
        // ``dictAxisSubStates`` catalog the warning column dispatches
        // through. The null ``untested`` entry renders no row — the
        // orange level cell carries that state.
        var dictSubStates =
            _fdictGlyphCatalog().dictAxisSubStates || {};
        var sHtml = '<div class="proof-legend-subsection-title">' +
            'Why a test shows a warning</div>' +
            '<ul class="proof-legend-criteria">';
        Object.keys(dictSubStates).forEach(function (sSubState) {
            var dictMeta = dictSubStates[sSubState];
            if (!dictMeta) return;
            sHtml += '<li><span class="proof-legend-glyph ' +
                fnEscapeHtml(dictMeta.sClass) + '">' +
                fnEscapeHtml(dictMeta.sIcon) + '</span> ' +
                fnEscapeHtml(dictMeta.sLabel) + '</li>';
        });
        return sHtml + '</ul>';
    }

    function _fsRenderCriteriaRows(dictGlyphs) {
        // Rows show the catalog's plain-English label only: the dict
        // keys are wire literals (e.g. hyphenated criterion names)
        // and must never surface as user-facing text.
        var sHtml = '<ul class="proof-legend-criteria">';
        Object.keys(dictGlyphs).forEach(function (sCriterion) {
            var dictMeta = dictGlyphs[sCriterion];
            sHtml += '<li><span class="proof-legend-glyph ' +
                fnEscapeHtml(dictMeta.sClass) + '">' +
                fnEscapeHtml(dictMeta.sIcon) + '</span> ' +
                fnEscapeHtml(dictMeta.sLabel) + '</li>';
        });
        sHtml += '</ul>';
        return sHtml;
    }

    function _fsRenderMarkRows(listMarks) {
        var sHtml = '<ul class="proof-legend-criteria">';
        for (var i = 0; i < listMarks.length; i++) {
            var dictMark = listMarks[i];
            sHtml += '<li>' + _fsRenderMarkSample(dictMark) + ' ' +
                fnEscapeHtml(dictMark.sLabel) + '</li>';
        }
        return sHtml + '</ul>';
    }

    function _fsRenderMarkSample(dictMark) {
        // ``sSampleHtml`` entries are static, trusted markup defined
        // above — never user data — so they render verbatim.
        if (dictMark.sSampleHtml) return dictMark.sSampleHtml;
        return '<span class="proof-legend-glyph ' +
            fnEscapeHtml(dictMark.sClass) + '">' +
            fnEscapeHtml(dictMark.sIcon) + '</span>';
    }

    function _fsRenderFooter() {
        return '<div class="proof-legend-footer">' +
            'Getting started: pick a container, open a project, ' +
            'then run and verify each step in the Steps block to ' +
            'reach Level 1; climb further through the ' +
            'Project rows. The requirements themselves ' +
            '— and how to meet each one — live on the ' +
            'PROOF tab.</div>';
    }

    document.addEventListener("DOMContentLoaded", fnInitialize);

    return {
        fnInitialize: fnInitialize,
        fnOpen: fnOpen,
        fnClose: fnClose,
    };
})();
