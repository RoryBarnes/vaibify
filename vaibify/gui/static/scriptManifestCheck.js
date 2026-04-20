/* Vaibify — pre-push manifest check dialog.

   Phase 5 of the workspace-as-git-repo plan. Before any push to a
   service this module surfaces canonical files that aren't cleanly
   committed. The dialog is non-blocking: the user can commit the
   listed files, skip the commit, or cancel the push entirely.

   Public surface:
   - VaibifyManifestCheck.fbRunBeforePush(sContainerId)
       Returns a promise resolving to true when the push should
       proceed and false when the user cancels. No-ops (resolves
       true) when the workspace isn't a git repo.
*/

var VaibifyManifestCheck = (function () {
    "use strict";

    var _DICT_STATE_LABELS = {
        modified: "modified",
        untracked: "new — not yet in git",
        dirty: "modified",
        "staged-only": "staged (not committed)",
    };

    function _fsEscape(sText) {
        return VaibifyUtilities.fnEscapeHtml(sText || "");
    }

    function _fsRenderFileList(listNeedsCommit) {
        var sHtml = '<ul class="manifest-check-list">';
        listNeedsCommit.forEach(function (dictEntry) {
            var sLabel = _DICT_STATE_LABELS[dictEntry.sState] ||
                dictEntry.sState;
            sHtml += '<li><code>' + _fsEscape(dictEntry.sPath) +
                '</code> <span class="manifest-state-tag">' +
                _fsEscape(sLabel) + '</span></li>';
        });
        sHtml += '</ul>';
        return sHtml;
    }

    function _fsRenderDialogHtml(dictReport) {
        var sHtml = '<div class="manifest-check-dialog">' +
            '<h2>Uncommitted canonical files</h2>' +
            '<p>These files are part of the workflow but not cleanly ' +
            'committed. Push anyway, or commit them first?</p>' +
            _fsRenderFileList(dictReport.listNeedsCommit) +
            '<div class="manifest-check-buttons">' +
            '<button class="btn" data-action="cancel">Cancel</button>' +
            '<button class="btn" data-action="push-anyway">' +
            'Push without committing</button>' +
            '<button class="btn btn-primary" data-action="commit">' +
            'Commit canonical &amp; continue</button>' +
            '</div></div>';
        return sHtml;
    }

    function _fnAttachOverlay(elDialog) {
        var elBackdrop = document.createElement("div");
        elBackdrop.className = "manifest-check-backdrop";
        elBackdrop.appendChild(elDialog);
        document.body.appendChild(elBackdrop);
        return elBackdrop;
    }

    function _fnRemoveOverlay(elBackdrop) {
        if (elBackdrop && elBackdrop.parentNode) {
            elBackdrop.parentNode.removeChild(elBackdrop);
        }
    }

    async function _fbCommitCanonical(sContainerId) {
        try {
            var dictResult = await VaibifyApi.fdictPost(
                "/api/git/" +
                    encodeURIComponent(sContainerId) +
                    "/commit-canonical",
                { sCommitMessage: "" }
            );
            return !!(dictResult && dictResult.bSuccess);
        } catch (error) {
            return false;
        }
    }

    function _fnPromptUser(dictReport, sContainerId) {
        return new Promise(function (fnResolve) {
            var elDialog = document.createElement("div");
            elDialog.innerHTML = _fsRenderDialogHtml(dictReport);
            var elBackdrop = _fnAttachOverlay(elDialog);
            elDialog.addEventListener("click", async function (event) {
                var sAction = (event.target.getAttribute &&
                    event.target.getAttribute("data-action")) || "";
                if (!sAction) return;
                if (sAction === "cancel") {
                    _fnRemoveOverlay(elBackdrop);
                    fnResolve(false);
                    return;
                }
                if (sAction === "push-anyway") {
                    _fnRemoveOverlay(elBackdrop);
                    fnResolve(true);
                    return;
                }
                if (sAction === "commit") {
                    event.target.disabled = true;
                    var bCommitted = await _fbCommitCanonical(
                        sContainerId
                    );
                    _fnRemoveOverlay(elBackdrop);
                    fnResolve(bCommitted);
                    return;
                }
            });
        });
    }

    async function fbRunBeforePush(sContainerId) {
        if (!sContainerId) return true;
        var dictReport;
        try {
            dictReport = await VaibifyApi.fdictGet(
                "/api/git/" + encodeURIComponent(sContainerId) +
                    "/manifest-check"
            );
        } catch (error) {
            return true;
        }
        if (!dictReport || !dictReport.bIsRepo) return true;
        var listNeedsCommit = dictReport.listNeedsCommit || [];
        if (listNeedsCommit.length === 0) return true;
        return _fnPromptUser(dictReport, sContainerId);
    }

    return {
        fbRunBeforePush: fbRunBeforePush,
    };
})();
