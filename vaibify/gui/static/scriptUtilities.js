/* Vaibify — Shared utility functions */

var VaibifyUtilities = (function () {
    "use strict";

    var SET_FIGURE_EXTENSIONS = new Set([
        ".pdf", ".png", ".jpg", ".jpeg", ".svg",
    ]);

    var SET_BINARY_EXTENSIONS = new Set([
        ".npy", ".npz", ".pkl", ".pickle", ".h5", ".hdf5",
        ".fits", ".fit", ".fz", ".dat", ".bin", ".so",
        ".o", ".a", ".pyc", ".pyo", ".whl", ".egg",
        ".gz", ".tar", ".zip", ".bz2", ".xz",
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

    function fbIsBinaryFile(sPath) {
        var iDot = sPath.lastIndexOf(".");
        if (iDot === -1) return true;
        var sExtension = sPath.substring(iDot).toLowerCase();
        return SET_BINARY_EXTENSIONS.has(sExtension);
    }

    return {
        fnEscapeHtml: fnEscapeHtml,
        fbIsFigureFile: fbIsFigureFile,
        fbIsBinaryFile: fbIsBinaryFile,
        SET_FIGURE_EXTENSIONS: SET_FIGURE_EXTENSIONS,
        SET_BINARY_EXTENSIONS: SET_BINARY_EXTENSIONS,
    };
})();
