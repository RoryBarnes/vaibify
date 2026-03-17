/* Vaibify — Shared utility functions */

var VaibifyUtilities = (function () {
    "use strict";

    var SET_FIGURE_EXTENSIONS = new Set([
        ".pdf", ".png", ".jpg", ".jpeg", ".svg",
    ]);

    function fnEscapeHtml(sText) {
        var el = document.createElement("span");
        el.textContent = sText;
        return el.innerHTML;
    }

    function fbIsFigureFile(sPath) {
        var iDot = sPath.lastIndexOf(".");
        if (iDot === -1) return false;
        return SET_FIGURE_EXTENSIONS.has(
            sPath.substring(iDot).toLowerCase()
        );
    }

    return {
        fnEscapeHtml: fnEscapeHtml,
        fbIsFigureFile: fbIsFigureFile,
        SET_FIGURE_EXTENSIONS: SET_FIGURE_EXTENSIONS,
    };
})();
