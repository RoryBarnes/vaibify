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

    var _DICT_LEGEND_SECTIONS = {
        1: {
            sTitle: "Level 1 — Self-Consistent",
            sGlyph: "⚠",
            sColorClass: "aics-level-1-tint",
            listCriteria: [
                "upstream-modified — rerun the step",
                "script-stale — rerun after editing the script",
                "axis-not-green — fix failing tests",
                "attestation-stale — outputs changed since you verified",
                "user-not-approved — never attested",
            ],
        },
        2: {
            sTitle: "Level 2 — Published",
            sGlyph: "⚠",
            sColorClass: "aics-level-2-tint",
            listCriteria: [
                "not-in-github-mirror — push to mirror",
                "not-in-zenodo-deposit — archive to Zenodo",
                "figure-not-frozen — push figure to Overleaf",
                "arxiv-not-submitted — submit to arXiv",
                "missing-ai-declaration-step — add AI declaration step",
            ],
        },
        3: {
            sTitle: "Level 3 — Reproducible",
            sGlyph: "✗",
            sColorClass: "aics-level-3-tint",
            listCriteria: [
                "missing-from-manifest — refresh MANIFEST.sha256",
                "script-not-pinned — rerun or refresh manifest",
                "nondeterminism-undeclared — seed RNGs",
                "binary-not-declared — declare external binaries",
                "binary-not-captured — capture SHA + version",
                "dockerfile-not-pinned — pin FROM @sha256",
                "dependency-lock-missing — generate requirements.lock",
                "environment-snapshot-missing — capture environment.json",
                "reproduce-script-missing — generate reproduce.sh",
            ],
        },
    };

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
            _fsRenderFooter();
    }

    function _fdictBlockerCounts() {
        if (PipeleyenApp && PipeleyenApp.fdictBlockerCountsByLevel) {
            return PipeleyenApp.fdictBlockerCountsByLevel();
        }
        return {iLevel1: 0, iLevel2: 0, iLevel3: 0};
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
        return '<div class="aics-legend-section ' +
            dictSection.sColorClass + '">' +
            '<div class="aics-legend-section-title">' +
            '<span class="aics-legend-glyph">' +
            fnEscapeHtml(dictSection.sGlyph) + '</span>' +
            fnEscapeHtml(dictSection.sTitle) +
            '<span class="aics-legend-count">(' + iCount +
            ' active)</span></div>' +
            _fsRenderCriteriaList(dictSection.listCriteria) +
            '</div>';
    }

    function _fsRenderCriteriaList(listCriteria) {
        var sHtml = '<ul class="aics-legend-criteria">';
        for (var i = 0; i < listCriteria.length; i++) {
            sHtml += '<li>' + fnEscapeHtml(listCriteria[i]) + '</li>';
        }
        sHtml += '</ul>';
        return sHtml;
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
