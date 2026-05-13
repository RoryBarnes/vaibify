/* Vaibify — arXiv configuration modal.

   Reachable from two entry points:
     - Sync → Configure arXiv… (toolbar pulldown)
     - Click the A badge on any plot row (picklist "Configure arXiv…"
       or "Edit arXiv ID…" depending on whether one is already set)

   POSTs /api/sync/{id}/arxiv/configure with one of:
     {sArxivId: "<id>", dictPathMap?: {…}}   — set or update
     {bRemove: true}                          — stop tracking

   On success the backend auto-runs verify, so the A badge updates
   from grey → orange → green on the next badge refresh without a
   second user action.

   Exposes:
     - VaibifyArxivConfig.fnOpen()      open the modal
     - VaibifyArxivConfig.fnClose()     hide the modal
     - VaibifyArxivConfig.fnSave()      submit the form
     - VaibifyArxivConfig.fnRemove()    stop tracking
     - VaibifyArxivConfig.fnAddPathMapRow()
*/

var VaibifyArxivConfig = (function () {
    "use strict";

    var _RE_ARXIV_ID =
        /^(?:\d{4}\.\d{4,5}(?:v\d+)?|[a-z-]+\/\d{7}(?:v\d+)?)$/;

    function _felGet(sId) {
        return document.getElementById(sId);
    }

    function _fdictCurrentArxivConfig() {
        var dictWorkflow = PipeleyenApp.fdictGetWorkflow() || {};
        var dictRemotes = dictWorkflow.dictRemotes || {};
        return dictRemotes.arxiv || {};
    }

    function _fnPopulateForm(dictConfig) {
        _felGet("inputArxivId").value = dictConfig.sArxivId || "";
        var elEditor = _felGet("arxivPathMapEditor");
        elEditor.innerHTML = "";
        var dictPathMap = dictConfig.dictPathMap || {};
        Object.keys(dictPathMap).forEach(function (sLocal) {
            _fnAppendPathMapRow(sLocal, dictPathMap[sLocal]);
        });
    }

    function _fnAppendPathMapRow(sLocal, sTarball) {
        var elEditor = _felGet("arxivPathMapEditor");
        var elRow = document.createElement("div");
        elRow.className = "arxiv-pathmap-row";
        elRow.innerHTML =
            '<input type="text" class="arxiv-pathmap-local" ' +
            'placeholder="local rel path">' +
            '<span class="arxiv-pathmap-arrow">→</span>' +
            '<input type="text" class="arxiv-pathmap-tarball" ' +
            'placeholder="tarball path">' +
            '<button type="button" class="btn btn-small ' +
            'arxiv-pathmap-remove">×</button>';
        elRow.querySelector(".arxiv-pathmap-local").value = sLocal || "";
        elRow.querySelector(".arxiv-pathmap-tarball").value =
            sTarball || "";
        elRow.querySelector(".arxiv-pathmap-remove").addEventListener(
            "click", function () { elRow.remove(); }
        );
        elEditor.appendChild(elRow);
    }

    function fnAddPathMapRow() {
        _fnAppendPathMapRow("", "");
    }

    function _fdictReadPathMapFromForm() {
        var elEditor = _felGet("arxivPathMapEditor");
        var listRows = elEditor.querySelectorAll(".arxiv-pathmap-row");
        var dictResult = {};
        for (var i = 0; i < listRows.length; i++) {
            var sLocal = listRows[i]
                .querySelector(".arxiv-pathmap-local").value.trim();
            var sTarball = listRows[i]
                .querySelector(".arxiv-pathmap-tarball").value.trim();
            if (sLocal === "" && sTarball === "") continue;
            dictResult[sLocal] = sTarball;
        }
        return dictResult;
    }

    function _fnShowError(sMessage) {
        var elError = _felGet("arxivConfigError");
        elError.textContent = sMessage;
        elError.hidden = false;
    }

    function _fnClearError() {
        var elError = _felGet("arxivConfigError");
        elError.textContent = "";
        elError.hidden = true;
    }

    function _fnUpdateRemoveButtonVisibility(dictConfig) {
        _felGet("btnArxivConfigRemove").hidden = !dictConfig.sArxivId;
    }

    function fnOpen() {
        var dictConfig = _fdictCurrentArxivConfig();
        _fnPopulateForm(dictConfig);
        _fnUpdateRemoveButtonVisibility(dictConfig);
        _fnClearError();
        _felGet("modalArxivConfig").style.display = "flex";
    }

    function fnClose() {
        _felGet("modalArxivConfig").style.display = "none";
    }

    function _fbValidateBeforeSubmit(sArxivId) {
        if (!sArxivId) {
            _fnShowError("Enter an arXiv ID.");
            return false;
        }
        if (!_RE_ARXIV_ID.test(sArxivId)) {
            _fnShowError(
                "ID must look like '2401.12345' (with optional 'v2' " +
                "suffix) or 'astro-ph/0601001'."
            );
            return false;
        }
        return true;
    }

    async function fnSave() {
        _fnClearError();
        var sArxivId = _felGet("inputArxivId").value.trim();
        if (!_fbValidateBeforeSubmit(sArxivId)) return;
        var dictBody = {
            sArxivId: sArxivId,
            dictPathMap: _fdictReadPathMapFromForm(),
        };
        await _fnPostConfigureAndHandle(dictBody, "Saved arXiv config");
    }

    async function fnRemove() {
        _fnClearError();
        await _fnPostConfigureAndHandle(
            {bRemove: true}, "Stopped tracking arXiv",
        );
    }

    async function _fnPostConfigureAndHandle(dictBody, sSuccessMessage) {
        var sContainerId = PipeleyenApp.fsGetContainerId();
        if (!sContainerId) return;
        try {
            var dictResult = await VaibifyApi.fdictPost(
                "/api/sync/" + encodeURIComponent(sContainerId) +
                    "/arxiv/configure",
                dictBody,
            );
            _fnApplyConfigureResultToWorkflow(dictResult, dictBody);
            await VaibifyGitBadges.fnRefresh(sContainerId);
            _fnShowSuccessOrVerifyWarning(dictResult, sSuccessMessage);
            fnClose();
        } catch (error) {
            _fnShowError(_fsExtractErrorDetail(error));
        }
    }

    function _fnApplyConfigureResultToWorkflow(dictResult, dictBody) {
        var dictWorkflow = PipeleyenApp.fdictGetWorkflow();
        if (!dictWorkflow) return;
        var dictRemotes = dictWorkflow.dictRemotes || {};
        if (dictBody.bRemove) {
            delete dictRemotes.arxiv;
        } else {
            dictRemotes.arxiv = dictResult.dictArxivConfig || {};
        }
        dictWorkflow.dictRemotes = dictRemotes;
    }

    function _fnShowSuccessOrVerifyWarning(dictResult, sSuccessMessage) {
        if (dictResult && dictResult.sVerifyError) {
            PipeleyenApp.fnShowToast(
                "Saved, but verify failed: " + dictResult.sVerifyError,
                "warning",
            );
            return;
        }
        PipeleyenApp.fnShowToast(sSuccessMessage, "success");
    }

    function _fsExtractErrorDetail(error) {
        if (!error) return "Configure failed.";
        if (typeof error === "string") return error;
        return error.message || "Configure failed.";
    }

    return {
        fnOpen: fnOpen,
        fnClose: fnClose,
        fnSave: fnSave,
        fnRemove: fnRemove,
        fnAddPathMapRow: fnAddPathMapRow,
    };
})();
