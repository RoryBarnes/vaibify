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

    function fsSanitizeErrorForUser(sRawError) {
        if (!sRawError) return "An error occurred.";
        if (sRawError.indexOf("no space left on device") >= 0) {
            return "Docker disk is full. Run 'docker image " +
                "prune -f' to free space.";
        }
        if (sRawError.indexOf("No such container") >= 0) {
            return "Container not found. It may have stopped.";
        }
        if (sRawError.indexOf("connection refused") >= 0 ||
            sRawError.indexOf("Cannot connect") >= 0) {
            return "Cannot connect to Docker. Is it running?";
        }
        if (sRawError.indexOf("permission denied") >= 0) {
            return "Permission denied. Check Docker access.";
        }
        if (sRawError.length > 200) {
            return sRawError.substring(0, 200) + "...";
        }
        return sRawError;
    }

    function fsFormatUtcTimestamp() {
        var d = new Date();
        var sPad = function (i) { return String(i).padStart(2, "0"); };
        return d.getUTCFullYear() + "-" +
            sPad(d.getUTCMonth() + 1) + "-" +
            sPad(d.getUTCDate()) + " " +
            sPad(d.getUTCHours()) + ":" +
            sPad(d.getUTCMinutes()) + ":" +
            sPad(d.getUTCSeconds()) + " UTC";
    }

    function fsResolveTemplate(sTemplate, dictVariables) {
        return sTemplate.replace(/\{([^}]+)\}/g, function (sMatch, sToken) {
            if (dictVariables.hasOwnProperty(sToken)) {
                return String(dictVariables[sToken]);
            }
            return sMatch;
        });
    }

    function fsTestCategoryLabel(sCategory) {
        var dictLabels = {
            qualitative: "Qualitative Tests",
            quantitative: "Quantitative Tests",
            integrity: "Integrity Tests",
        };
        return dictLabels[sCategory] || sCategory;
    }

    async function fnSpawnNewSession() {
        try {
            var dictResponse = await VaibifyApi.fdictPost(
                "/api/session/spawn", {});
            var windowChild = window.open(dictResponse.sUrl, "_blank");
            if (windowChild) windowChild.focus();
        } catch (error) {
            PipeleyenApp.fnShowToast(
                "Could not open new vaibify window: " +
                fsSanitizeErrorForUser(error.message),
                "error");
        }
    }

    return {
        fnEscapeHtml: fnEscapeHtml,
        fbIsFigureFile: fbIsFigureFile,
        fbIsBinaryFile: fbIsBinaryFile,
        fsSanitizeErrorForUser: fsSanitizeErrorForUser,
        fsFormatUtcTimestamp: fsFormatUtcTimestamp,
        fsResolveTemplate: fsResolveTemplate,
        fsTestCategoryLabel: fsTestCategoryLabel,
        fnSpawnNewSession: fnSpawnNewSession,
        SET_FIGURE_EXTENSIONS: SET_FIGURE_EXTENSIONS,
        SET_BINARY_EXTENSIONS: SET_BINARY_EXTENSIONS,
    };
})();
