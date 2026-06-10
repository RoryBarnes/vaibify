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
            sIcon: "", sClass: "step-level-dot state-green",
            sLabel: "Level dots — L1 manifest membership, L2 mirror " +
                "state, L3 envelope (green / yellow / red / grey)",
        },
        {
            sIcon: "✎", sClass: "script-modified-badge",
            sLabel: "Script edited since the last run",
        },
        {
            sIcon: "⚠", sClass: "script-unseeded-badge",
            sLabel: "Unseeded randomness detected — add a seed",
        },
        {
            sIcon: "⚠", sClass: "data-modified-badge",
            sLabel: "Output files modified since the last run",
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
        return '<div class="aics-legend-section ' +
            dictSection.sColorClass + '">' +
            '<div class="aics-legend-section-title">' +
            fnEscapeHtml(dictSection.sTitle) +
            '<span class="aics-legend-count">(' + iCount +
            ' active)</span></div>' +
            _fsRenderCriteriaRows(dictGlyphs) +
            '</div>';
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
            sHtml += '<li><span class="aics-legend-glyph ' +
                fnEscapeHtml(dictMark.sClass) + '">' +
                fnEscapeHtml(dictMark.sIcon) + '</span> ' +
                fnEscapeHtml(dictMark.sLabel) + '</li>';
        }
        return sHtml + '</ul></div>';
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
