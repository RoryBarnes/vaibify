/* Vaibify — reproducibility-ladder legend panel.

   Section G of the AICS-ladder UX plan: a dismissible, fixed-position
   panel listing the L1/L2/L3 glyph + color matrix with live blocker
   counts. Opens from the dashboard-header ``?`` button beside the AICS
   chip. State lives in this IIFE; data is pulled from
   ``PipeleyenApp.fdictBlockerCountsByLevel``. */

var VaibifyLegendPanel = (function () {
    "use strict";

    var fnEscapeHtml = VaibifyUtilities.fnEscapeHtml;

    var _S_PANEL_ID = "aicsLegendPanel";
    var _S_BUTTON_ID = "aicsLegendButton";
    var _bOpen = false;
    var _bOutsideClickBound = false;

    // Criterion rows are generated live from
    // ``PipeleyenApp.fdictBlockerGlyphCatalog()`` — the same dicts the
    // step renderer draws from — so the legend cannot drift from the
    // glyphs actually rendered. Only the section chrome is static.
    var _DICT_LEGEND_SECTIONS = {
        1: {
            sTitle: "Level 1 — Self-Consistent",
            sColorClass: "aics-level-1-tint",
            sCatalogKey: "iLevel1",
        },
        2: {
            sTitle: "Level 2 — Published",
            sColorClass: "aics-level-2-tint",
            sCatalogKey: "iLevel2",
        },
        3: {
            sTitle: "Level 3 — Reproducible",
            sColorClass: "aics-level-3-tint",
            sCatalogKey: "iLevel3",
        },
    };

    // Marks that are not per-criterion blocker glyphs but appear on
    // step cards, file lists, and dependency edges. Static by design:
    // each entry names the CSS class that styles the live mark.
    // Entries with ``sSampleHtml`` render that static markup verbatim
    // so the legend sample matches the live cell exactly.
    var _LIST_OTHER_MARKS = [
        {
            sIcon: "⚠", sClass: "l1-blocker-file-glyph",
            sLabel: "Offending file or dependency edge — " +
                "blocking verification; re-run the step",
        },
        {
            sIcon: "✓", sClass: "aics-legend-check-sample",
            sLabel: "Test axis passed (fresh run or restored " +
                "from a committed test marker)",
        },
        {
            sSampleHtml: '<span class="step-level-cell ' +
                'level-cell-not-started">' +
                '<span class="level-cell-circle"></span></span>',
            sLabel: "Level cell, grey circle — not started: the " +
                "step has no activity at this level yet",
        },
        {
            sSampleHtml: '<span class="step-level-cell ' +
                'level-cell-none">' +
                '<span class="level-cell-circle"></span></span>',
            sLabel: "Level cell, red circle — no requirements met",
        },
        {
            sSampleHtml: '<span class="step-level-cell ' +
                'level-cell-partial">' +
                '<span class="level-cell-circle"></span></span>',
            sLabel: "Level cell, orange circle — partially met",
        },
        {
            sSampleHtml: '<span class="step-level-cell ' +
                'level-cell-attained">' +
                '<img src="/static/favicon.png" ' +
                'class="level-cell-favicon" alt="attained"></span>',
            sLabel: "Level cell, vaibify badge — attained: every " +
                "requirement at this level is met",
        },
        {
            sSampleHtml: '<span class="step-level-cell ' +
                'level-cell-unknown">' +
                '<span class="level-cell-circle"></span></span>',
            sLabel: "Level cell, hollow grey circle — unknown: " +
                "GitHub/Zenodo have not been checked recently; " +
                "refresh remote status to find out",
        },
        {
            sSampleHtml: '<span class="step-level-cell ' +
                'level-cell-not-applicable">' +
                '<span class="level-cell-dash">&#8212;</span></span>',
            sLabel: "Level cell, dash — not applicable: this step " +
                "has no requirements at this level",
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
            sIcon: "●", sClass: "aics-legend-orange-light-sample",
            sLabel: "Orange status light = work not yet done " +
                "(never-run tests / pending attestation)",
        },
        {
            sIcon: "file", sClass: "aics-legend-red-missing-sample",
            sLabel: "Red upright file name — declared file missing",
        },
        {
            sIcon: "file", sClass: "aics-legend-red-stale-sample",
            sLabel: "Red dotted-underlined file name — file changed " +
                "since its last test run",
        },
        {
            sIcon: "file", sClass: "aics-legend-red-unattested-sample",
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
        var elClose = elPanel.querySelector(".aics-legend-close");
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
        var dictCounts = _fdictBlockerCounts();
        return _fsRenderHeader() +
            _fsRenderSection(1, dictCounts.iLevel1) +
            _fsRenderSection(2, dictCounts.iLevel2) +
            _fsRenderSection(3, dictCounts.iLevel3) +
            _fsRenderOtherMarksSection() +
            _fsRenderFooter();
    }

    function _fdictBlockerCounts() {
        if (PipeleyenApp && PipeleyenApp.fdictBlockerCountsByLevel) {
            return PipeleyenApp.fdictBlockerCountsByLevel();
        }
        return {iLevel1: 0, iLevel2: 0, iLevel3: 0};
    }

    function _fdictGlyphCatalog() {
        if (PipeleyenApp && PipeleyenApp.fdictBlockerGlyphCatalog) {
            return PipeleyenApp.fdictBlockerGlyphCatalog();
        }
        return {};
    }

    function _fsRenderHeader() {
        return '<div class="aics-legend-header">' +
            '<span>Reproducibility ladder legend</span>' +
            '<button class="aics-legend-close" ' +
            'title="Close">&times;</button>' +
            '</div>';
    }

    function _fsRenderSection(iLevel, iCount) {
        var dictSection = _DICT_LEGEND_SECTIONS[iLevel];
        if (!dictSection) return "";
        var dictGlyphs =
            _fdictGlyphCatalog()[dictSection.sCatalogKey] || {};
        var sSubStateRows = iLevel === 1
            ? _fsRenderAxisSubStateRows() : "";
        return '<div class="aics-legend-section ' +
            dictSection.sColorClass + '">' +
            '<div class="aics-legend-section-title">' +
            fnEscapeHtml(dictSection.sTitle) +
            '<span class="aics-legend-count">(' + iCount +
            ' active)</span></div>' +
            _fsRenderCriteriaRows(dictGlyphs) +
            sSubStateRows +
            '</div>';
    }

    function _fsRenderAxisSubStateRows() {
        // The axis-not-green causes, drawn from the same
        // ``dictAxisSubStates`` catalog the banner glyph dispatches
        // through. The null ``untested`` entry renders no row — the
        // orange status light carries that state.
        var dictSubStates =
            _fdictGlyphCatalog().dictAxisSubStates || {};
        var sHtml = '<div class="aics-legend-subsection-title">' +
            'axis-not-green causes</div>' +
            '<ul class="aics-legend-criteria">';
        Object.keys(dictSubStates).forEach(function (sSubState) {
            var dictMeta = dictSubStates[sSubState];
            if (!dictMeta) return;
            sHtml += '<li><span class="aics-legend-glyph ' +
                fnEscapeHtml(dictMeta.sClass) + '">' +
                fnEscapeHtml(dictMeta.sIcon) + '</span> ' +
                fnEscapeHtml(sSubState) + ' — ' +
                fnEscapeHtml(dictMeta.sLabel) + '</li>';
        });
        return sHtml + '</ul>';
    }

    function _fsRenderCriteriaRows(dictGlyphs) {
        var sHtml = '<ul class="aics-legend-criteria">';
        Object.keys(dictGlyphs).forEach(function (sCriterion) {
            var dictMeta = dictGlyphs[sCriterion];
            sHtml += '<li><span class="aics-legend-glyph ' +
                fnEscapeHtml(dictMeta.sClass) + '">' +
                fnEscapeHtml(dictMeta.sIcon) + '</span> ' +
                fnEscapeHtml(sCriterion) + ' — ' +
                fnEscapeHtml(dictMeta.sLabel) + '</li>';
        });
        sHtml += '</ul>';
        return sHtml;
    }

    function _fsRenderOtherMarksSection() {
        var sHtml = '<div class="aics-legend-section">' +
            '<div class="aics-legend-section-title">' +
            'Other marks</div>' +
            '<ul class="aics-legend-criteria">';
        for (var i = 0; i < _LIST_OTHER_MARKS.length; i++) {
            var dictMark = _LIST_OTHER_MARKS[i];
            sHtml += '<li>' + _fsRenderMarkSample(dictMark) + ' ' +
                fnEscapeHtml(dictMark.sLabel) + '</li>';
        }
        return sHtml + '</ul></div>';
    }

    function _fsRenderMarkSample(dictMark) {
        // ``sSampleHtml`` entries are static, trusted markup defined
        // above — never user data — so they render verbatim.
        if (dictMark.sSampleHtml) return dictMark.sSampleHtml;
        return '<span class="aics-legend-glyph ' +
            fnEscapeHtml(dictMark.sClass) + '">' +
            fnEscapeHtml(dictMark.sIcon) + '</span>';
    }

    function _fsRenderFooter() {
        return '<div class="aics-legend-footer">' +
            'Re-run step to clear most blockers.</div>';
    }

    document.addEventListener("DOMContentLoaded", fnInitialize);

    return {
        fnInitialize: fnInitialize,
        fnOpen: fnOpen,
        fnClose: fnClose,
    };
})();
