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
        // Escapes quotes as well as angle brackets so the result is
        // safe inside double- or single-quoted HTML attributes
        // (title tooltips carry workflow-derived strings).
        return String(sText == null ? "" : sText)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#39;");
    }

    /* --- Level-cell vocabulary (single owner) ---
       Every attained favicon and every L1/L2/L3 level cell in the
       GUI is built here, so the step rows, the Project block, the
       AICS tab, and the legend samples cannot drift apart. The alt
       text is per-context accessibility language ("attained",
       "met", "passing", …) supplied by the caller. */

    function fsBuildAttainedFavicon(sAltText, sTooltip) {
        var sTitleAttribute = sTooltip
            ? ' title="' + fnEscapeHtml(sTooltip) + '"'
            : "";
        return '<img src="/static/favicon.png" ' +
            'class="level-cell-favicon"' + sTitleAttribute +
            ' alt="' + fnEscapeHtml(sAltText) + '">';
    }

    function fsBuildLevelCell(sState, sTooltip, sAltText) {
        // Cell visuals: favicon = attained, muted dash = not
        // applicable, question mark = unknown (assessed once, answer
        // stale), a circle (tinted by the level-cell-<sState> class)
        // for every other state — hollow for not-started, filling in
        // as reality does.
        var sInner;
        if (sState === "attained") {
            sInner = fsBuildAttainedFavicon(sAltText || "attained");
        } else if (sState === "not-applicable") {
            sInner = '<span class="level-cell-dash">&#8212;</span>';
        } else if (sState === "unknown") {
            sInner = '<span class="level-cell-question">?</span>';
        } else {
            sInner = '<span class="level-cell-circle"></span>';
        }
        // sState is a server enum today, but this is the single
        // owner of the cell markup — escape it so a future non-enum
        // state can never become an attribute breakout.
        return '<span class="step-level-cell level-cell-' +
            fnEscapeHtml(sState) + '" title="' +
            fnEscapeHtml(sTooltip) + '">' + sInner + '</span>';
    }

    function fbIsFigureFile(sPath) {
        var iDot = sPath.lastIndexOf(".");
        if (iDot === -1) return false;
        return SET_FIGURE_EXTENSIONS.has(
            sPath.substring(iDot).toLowerCase()
        );
    }

    // Extensionless files vaibify itself requires and therefore
    // vouches for as plain text — only the Dockerfile (part of the
    // reproducibility envelope). Vaibify is a general tool, so it
    // makes no claims about other projects' extensionless names;
    // those stay conservatively unviewable (they are usually
    // executables).
    var SET_EXTENSIONLESS_TEXT_FILES = new Set([
        "dockerfile",
    ]);

    function fbIsBinaryFile(sPath) {
        var iDot = sPath.lastIndexOf(".");
        if (iDot === -1 || iDot < sPath.lastIndexOf("/")) {
            // No extension: known text files (Dockerfile, Makefile,
            // …) are viewable; anything else extensionless is
            // conservatively treated as an executable.
            var sBase = sPath.split("/").pop().toLowerCase();
            return !SET_EXTENSIONLESS_TEXT_FILES.has(sBase);
        }
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

    function fsFormatEpochUtc(iEpochSeconds) {
        if (iEpochSeconds === undefined || iEpochSeconds === null) {
            return "";
        }
        var iEpoch = parseInt(iEpochSeconds, 10);
        if (isNaN(iEpoch)) return "";
        var d = new Date(iEpoch * 1000);
        var sPad = function (i) { return String(i).padStart(2, "0"); };
        return d.getUTCFullYear() + "-" +
            sPad(d.getUTCMonth() + 1) + "-" +
            sPad(d.getUTCDate()) + " " +
            sPad(d.getUTCHours()) + ":" +
            sPad(d.getUTCMinutes()) + " UTC";
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

    function fsSummarizeLevelStates(listStates) {
        // The one aggregation rule for a collection of level cells
        // (step rows under the Steps banner, requirement rows under
        // a Project-block group header), so the two banners cannot
        // drift apart. All applicable cells attained → attained;
        // every ASSESSED cell none (with zero attained/partial in
        // the mix) → none; any progress at all (an attained or a
        // partial among assessed cells) → partial. Counting only
        // attained as credit once made a group of all-partial rows
        // summarize red, which read as "nothing works" when every
        // row was partially met. not-started, unassessed, and
        // unknown cells are not assessments, so they neither push
        // the summary to none nor count as progress — but they
        // still block attained, per the honesty rule. With nothing
        // assessed: all cells not-started → not-started; greys with
        // material somewhere (an unassessed in the mix) →
        // unassessed; otherwise → unknown.
        var iAttained = 0, iPartial = 0, iNone = 0;
        var iNotStarted = 0, iUnassessed = 0, iApplicable = 0;
        for (var i = 0; i < listStates.length; i++) {
            var sState = listStates[i];
            if (sState === "not-applicable") continue;
            iApplicable++;
            if (sState === "attained") iAttained++;
            else if (sState === "partial") iPartial++;
            else if (sState === "none") iNone++;
            else if (sState === "not-started") iNotStarted++;
            else if (sState === "unassessed") iUnassessed++;
        }
        var iAssessed = iAttained + iPartial + iNone;
        if (iApplicable === 0) return "not-applicable";
        if (iAssessed === 0) {
            if (iNotStarted === iApplicable) return "not-started";
            if (iNotStarted + iUnassessed === iApplicable) {
                return "unassessed";
            }
            return "unknown";
        }
        if (iAttained === iApplicable) return "attained";
        if (iNone === iAssessed) return "none";
        return "partial";
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
        fsBuildAttainedFavicon: fsBuildAttainedFavicon,
        fsBuildLevelCell: fsBuildLevelCell,
        fsSummarizeLevelStates: fsSummarizeLevelStates,
        fbIsFigureFile: fbIsFigureFile,
        fbIsBinaryFile: fbIsBinaryFile,
        fsSanitizeErrorForUser: fsSanitizeErrorForUser,
        fsFormatUtcTimestamp: fsFormatUtcTimestamp,
        fsFormatEpochUtc: fsFormatEpochUtc,
        fsResolveTemplate: fsResolveTemplate,
        fsTestCategoryLabel: fsTestCategoryLabel,
        fnSpawnNewSession: fnSpawnNewSession,
        SET_FIGURE_EXTENSIONS: SET_FIGURE_EXTENSIONS,
        SET_BINARY_EXTENSIONS: SET_BINARY_EXTENSIONS,
    };
})();
