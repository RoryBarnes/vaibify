/* The Agent Council's size modal: "too large to copy in full".

   A council gives every participant its own copy of the project, held
   in memory on this computer. When the whole directory cannot fit, the
   capabilities read weighs the files git tracks and, when THOSE fit,
   this modal offers them instead — saying why, how big each option is,
   and exactly what would be missing. Everything shown comes from the
   server: the numbers are the capabilities answer, and the missing
   files are paged from the one observation the server recorded, never
   re-derived here. Choosing the option records it as the project's
   visible default; start re-validates it against the current tree. */

var VaibifyCouncilSnapshotScope = (function () {
    "use strict";

    var I_PAGE_SIZE = 200;

    var DICT_REASON_LABELS = {
        untracked: "untracked",
        ignored: "ignored",
        deletedInWorktree: "tracked but deleted in the working tree",
        notCheckedOut: "not checked out (skip-worktree)",
        policyExcluded: "excluded by vaibify policy",
    };

    var _dictState = {
        sContainerId: "",
        dictOffer: null,
        fnOnContinue: null,
    };

    function _fsEscape(sText) {
        return VaibifyUtilities.fnEscapeHtml(sText);
    }

    function fsFormatBytes(iBytes) {
        var fValue = Number(iBytes) || 0;
        var listUnits = ["bytes", "KB", "MB", "GB", "TB"];
        var iUnit = 0;
        while (fValue >= 1000 && iUnit < listUnits.length - 1) {
            fValue /= 1000;
            iUnit += 1;
        }
        return iUnit === 0 ? Math.round(fValue) + " bytes"
            : fValue.toFixed(1) + " " + listUnits[iUnit];
    }

    function _fsCount(iCount) {
        return Number(iCount || 0).toLocaleString("en-US");
    }

    function _fnShowModal() {
        var elModal = document.getElementById("councilSnapshotScopeModal");
        if (elModal) elModal.style.display = "flex";
    }

    function fnHideModal() {
        var elModal = document.getElementById("councilSnapshotScopeModal");
        if (elModal) elModal.style.display = "none";
    }

    function _fnSetBody(sMarkup) {
        var elBody = document.getElementById("councilSnapshotScopeModalBody");
        if (elBody) elBody.innerHTML = sMarkup;
    }

    function fnOpenSizeModal(sContainerId, dictCapabilities, fnOnContinue) {
        _dictState.sContainerId = sContainerId;
        _dictState.dictOffer = dictCapabilities.dictTrackedScopeOffer || {};
        _dictState.fnOnContinue = fnOnContinue || null;
        _fnSetBody(_fsModalMarkup(
            dictCapabilities.dictSnapshotFeasibility || {},
            _dictState.dictOffer));
        _fnShowModal();
        _fnBindModal();
    }

    function _fsWhyMarkup(dictFeasibility) {
        return "<h3>Why</h3><p>Each participant works on its own copy " +
            "of the project, held in memory on this computer. This " +
            "project has " + (dictFeasibility.bTruncated ? "more than " : "") +
            _fsCount(dictFeasibility.iFileCount) + " files (" +
            fsFormatBytes(dictFeasibility.iTotalBytes) + "); a council " +
            "copy can hold at most " +
            _fsCount(dictFeasibility.iMaxSnapshotFileCount) + " files and " +
            fsFormatBytes(dictFeasibility.iMaxSnapshotTotalBytes) + ".</p>";
    }

    function _fsAtLeast(dictSummary) {
        return dictSummary.bComplete ? "" : "at least ";
    }

    function _fsGroupsMarkup(dictSummary) {
        var listGroups = dictSummary.listGroups || [];
        if (!listGroups.length) return "<p>Nothing would be missing.</p>";
        return listGroups.map(function (dictGroup) {
            return "<details class=\"council-omission-group\" " +
                "data-directory=\"" + _fsEscape(dictGroup.sDirectory) +
                "\" data-reason=\"" + _fsEscape(dictGroup.sReason) + "\">" +
                "<summary>" + _fsEscape(dictGroup.sDirectory) + " — " +
                _fsEscape(DICT_REASON_LABELS[dictGroup.sReason] ||
                          dictGroup.sReason) + ": " +
                _fsCount(dictGroup.iCount) + " files, " +
                fsFormatBytes(dictGroup.iBytes) + "</summary>" +
                "<ul class=\"council-omission-paths\"></ul></details>";
        }).join("");
    }

    function _fsOptionMarkup(dictOffer) {
        var dictSummary = dictOffer.dictOmissionSummary || {};
        return "<h3>The option</h3><p><strong>Copy only the files git " +
            "tracks</strong> — " + _fsCount(dictOffer.iTrackedFileCount) +
            " files (" + fsFormatBytes(dictOffer.iTrackedBytes) + "). " +
            _fsCount(dictOffer.iUncommittedEditCount) + " tracked files " +
            "have uncommitted edits; the copy includes those edits as " +
            "they are now.</p>" +
            "<details class=\"council-missing-files\"><summary>Files that " +
            "will be missing: " + _fsAtLeast(dictSummary) +
            _fsCount(dictSummary.iOmittedCount) + " (" +
            fsFormatBytes(dictSummary.iOmittedBytes) + ")</summary>" +
            _fsGroupsMarkup(dictSummary) + "</details>" +
            "<p>Participants will be told what was left out — the counts " +
            "and folders, never the file names.</p>";
    }

    function _fsNoOptionMarkup(dictOffer) {
        var sLargest = (dictOffer.listLargestTrackedFiles || []).map(
            function (dictFile) {
                return "<li><code>" + _fsEscape(dictFile.sPath) +
                    "</code> — " + fsFormatBytes(dictFile.iSizeBytes) +
                    "</li>";
            }).join("");
        return "<h3>No smaller copy fits either</h3><p>" +
            (dictOffer.bOffered === false
                ? _fsEscape(dictOffer.sReason)
                : "Even the files git tracks are " +
                  _fsCount(dictOffer.iTrackedFileCount) + " files (" +
                  fsFormatBytes(dictOffer.iTrackedBytes) + "), over the " +
                  "limits.") + "</p>" +
            (sLargest ? "<p>The largest tracked files:</p><ul>" + sLargest +
                "</ul>" : "");
    }

    function _fsModalMarkup(dictFeasibility, dictOffer) {
        var bOffer = dictOffer.bOffered && dictOffer.bFits;
        return "<h2>This project is too large to copy in full for a " +
            "council</h2>" + _fsWhyMarkup(dictFeasibility) +
            (bOffer ? _fsOptionMarkup(dictOffer)
                : _fsNoOptionMarkup(dictOffer)) +
            "<div class=\"modal-actions\"><button type=\"button\" " +
            "id=\"btnCouncilScopeCancel\">" + (bOffer ? "Cancel" : "Close") +
            "</button>" + (bOffer
                ? "<button type=\"button\" id=\"btnCouncilScopeContinue\" " +
                  "class=\"primary\">Continue with the git-tracked " +
                  "files</button>" : "") + "</div>";
    }

    function _fnBindModal() {
        var elCancel = document.getElementById("btnCouncilScopeCancel");
        if (elCancel) elCancel.addEventListener("click", fnHideModal);
        var elContinue = document.getElementById("btnCouncilScopeContinue");
        if (elContinue) elContinue.addEventListener("click", _fnChooseTracked);
        document.querySelectorAll(".council-omission-group").forEach(
            function (elGroup) {
                elGroup.addEventListener("toggle", function () {
                    if (elGroup.open && !elGroup.dataset.iLoaded) {
                        _fnLoadPage(elGroup, 0);
                    }
                });
            });
    }

    async function _fnLoadPage(elGroup, iOffset) {
        elGroup.dataset.iLoaded = "1";
        var elList = elGroup.querySelector(".council-omission-paths");
        var elMore = elGroup.querySelector(".council-omission-more");
        if (elMore) elMore.remove();
        try {
            var dictPage = await VaibifyApi.fdictGet(
                "/api/council-snapshots/" +
                encodeURIComponent(_dictState.sContainerId) + "/omissions/" +
                encodeURIComponent(_dictState.dictOffer.sObservationId) +
                "?sDirectory=" + encodeURIComponent(elGroup.dataset.directory) +
                "&sReason=" + encodeURIComponent(elGroup.dataset.reason) +
                "&iOffset=" + iOffset + "&iLimit=" + I_PAGE_SIZE);
            _fnAppendPage(elGroup, elList, dictPage);
        } catch (error) {
            elList.insertAdjacentHTML("beforeend",
                "<li class=\"council-consent-warning\">" +
                _fsEscape(error.message || String(error)) + "</li>");
        }
    }

    function _fnAppendPage(elGroup, elList, dictPage) {
        elList.insertAdjacentHTML("beforeend", dictPage.listPaths.map(
            function (dictPath) {
                return "<li><code>" + _fsEscape(dictPath.sPath) +
                    "</code> " + fsFormatBytes(dictPath.iSizeBytes) + "</li>";
            }).join(""));
        var iShown = dictPage.iOffset + dictPage.listPaths.length;
        if (iShown < dictPage.iTotal) {
            elGroup.insertAdjacentHTML("beforeend",
                "<button type=\"button\" class=\"council-omission-more\">" +
                "Show more (" + _fsCount(dictPage.iTotal - iShown) +
                " left)</button>");
            elGroup.querySelector(".council-omission-more")
                .addEventListener("click", function () {
                    _fnLoadPage(elGroup, iShown);
                });
        }
    }

    async function _fnChooseTracked() {
        var elContinue = document.getElementById("btnCouncilScopeContinue");
        if (elContinue) elContinue.disabled = true;
        try {
            await VaibifyApi.fdictPost(
                "/api/council-snapshots/" +
                encodeURIComponent(_dictState.sContainerId) + "/scope",
                {sScope: "gitTracked"});
        } catch (error) {
            if (elContinue) elContinue.disabled = false;
            VaibifyDiagnosis.fnReportFailureFromError(error);
            return;
        }
        var fnOnContinue = _dictState.fnOnContinue;
        fnHideModal();
        if (fnOnContinue) fnOnContinue();
    }

    function fnInitialize() {
        var elClose = document.getElementById(
            "btnCouncilSnapshotScopeModalClose");
        if (elClose) elClose.addEventListener("click", fnHideModal);
    }

    document.addEventListener("DOMContentLoaded", fnInitialize);

    return {
        fnOpenSizeModal: fnOpenSizeModal,
        fnHideModal: fnHideModal,
        fsFormatBytes: fsFormatBytes,
    };
})();
