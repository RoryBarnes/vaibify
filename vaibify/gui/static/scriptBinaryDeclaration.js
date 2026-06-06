/* Vaibify — standalone-binary declaration modal.

   Lets the researcher answer the L3 binary question in exactly one
   coherent state:
     - Waiver: bNoStandaloneBinaries = true, listDeclaredBinaries = []
     - Declaration: bNoStandaloneBinaries = false,
                    listDeclaredBinaries non-empty with
                    {sBinaryPath, sPurpose, sExpectedVersion} entries.

   POSTs to /api/workflow/{id}/binaries/declare to persist.
   POSTs to /api/workflow/{id}/binaries/capture to hash + version
   one binary, appending the result to .vaibify/environment.json.

   A directory-tree picker for binary paths is deferred to Stage 6;
   for now the path is entered as text in each row.

   Exposes:
     - VaibifyBinaryDeclaration.fnOpen()
     - VaibifyBinaryDeclaration.fnClose()
     - VaibifyBinaryDeclaration.fnSave()
     - VaibifyBinaryDeclaration.fnAddRow()
     - VaibifyBinaryDeclaration.fnCaptureRow(iIndex)
*/

var VaibifyBinaryDeclaration = (function () {
    "use strict";

    var _S_MODAL_ID = "modalBinaryDeclaration";
    var _S_WAIVER_ID = "binaryDeclarationWaiver";
    var _S_LIST_ID = "binaryDeclarationList";
    var _S_ERROR_ID = "binaryDeclarationError";

    function _felGet(sId) {
        return document.getElementById(sId);
    }

    function _fdictCurrentBinaryState() {
        var dictWorkflow = PipeleyenApp.fdictGetWorkflow() || {};
        return {
            bNoStandaloneBinaries: !!dictWorkflow.bNoStandaloneBinaries,
            listDeclaredBinaries:
                dictWorkflow.listDeclaredBinaries || [],
        };
    }

    function _fnRenderRows(listEntries) {
        var elList = _felGet(_S_LIST_ID);
        if (!elList) return;
        elList.innerHTML = "";
        for (var i = 0; i < listEntries.length; i++) {
            _fnAppendRow(listEntries[i]);
        }
    }

    function _fnAppendRow(dictEntry) {
        var elList = _felGet(_S_LIST_ID);
        if (!elList) return;
        var elRow = document.createElement("div");
        elRow.className = "binary-declaration-row";
        elRow.innerHTML = _fsBuildRowHtml(dictEntry || {});
        _fnAttachRowRemove(elRow);
        _fnAttachRowCapture(elRow);
        elList.appendChild(elRow);
    }

    function _fsBuildRowHtml(dictEntry) {
        var sPath = _fsEscape(dictEntry.sBinaryPath || "");
        var sPurpose = _fsEscape(dictEntry.sPurpose || "");
        var sVersion = _fsEscape(dictEntry.sExpectedVersion || "");
        return (
            '<input type="text" class="binary-declaration-path" ' +
            'placeholder="/usr/local/bin/binary" value="' + sPath + '">' +
            '<input type="text" class="binary-declaration-purpose" ' +
            'placeholder="purpose" value="' + sPurpose + '">' +
            '<input type="text" class="binary-declaration-version" ' +
            'placeholder="expected version" value="' + sVersion + '">' +
            '<button type="button" class="btn btn-small ' +
            'binary-declaration-capture">Capture</button>' +
            '<button type="button" class="btn btn-small ' +
            'binary-declaration-remove">×</button>'
        );
    }

    function _fsEscape(sText) {
        return String(sText)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;");
    }

    function _fnAttachRowRemove(elRow) {
        var elBtn = elRow.querySelector(".binary-declaration-remove");
        if (!elBtn) return;
        elBtn.addEventListener("click", function () {
            elRow.remove();
        });
    }

    function _fnAttachRowCapture(elRow) {
        var elBtn = elRow.querySelector(".binary-declaration-capture");
        if (!elBtn) return;
        elBtn.addEventListener("click", function () {
            _fnCaptureSingleRow(elRow);
        });
    }

    async function _fnCaptureSingleRow(elRow) {
        var elPath = elRow.querySelector(".binary-declaration-path");
        var sPath = (elPath && elPath.value || "").trim();
        if (!sPath) {
            _fnShowError("Enter a binary path before capturing.");
            return;
        }
        var sContainerId = PipeleyenApp.fsGetContainerId();
        if (!sContainerId) return;
        await _fnPostCapture(sContainerId, sPath);
    }

    async function _fnPostCapture(sContainerId, sPath) {
        try {
            await VaibifyApi.fdictPost(
                "/api/workflow/" +
                    encodeURIComponent(sContainerId) +
                    "/binaries/capture",
                {sBinaryPath: sPath},
            );
            _fnClearError();
        } catch (error) {
            _fnShowError(_fsExtractErrorDetail(error));
        }
    }

    function _fnShowError(sMessage) {
        var elError = _felGet(_S_ERROR_ID);
        if (!elError) return;
        elError.textContent = sMessage;
        elError.hidden = false;
    }

    function _fnClearError() {
        var elError = _felGet(_S_ERROR_ID);
        if (!elError) return;
        elError.textContent = "";
        elError.hidden = true;
    }

    function _fsExtractErrorDetail(error) {
        if (!error) return "Request failed.";
        if (error.dictBody && error.dictBody.detail) {
            return String(error.dictBody.detail);
        }
        return error.message || "Request failed.";
    }

    function _flistReadRowsFromForm() {
        var elList = _felGet(_S_LIST_ID);
        if (!elList) return [];
        var listRows = elList.querySelectorAll(".binary-declaration-row");
        var listResult = [];
        for (var i = 0; i < listRows.length; i++) {
            var dictRow = _fdictReadOneRow(listRows[i]);
            if (dictRow !== null) listResult.push(dictRow);
        }
        return listResult;
    }

    function _fdictReadOneRow(elRow) {
        var sPath = (elRow.querySelector(".binary-declaration-path")
            .value || "").trim();
        var sPurpose = (elRow.querySelector(".binary-declaration-purpose")
            .value || "").trim();
        var sVersion = (elRow.querySelector(".binary-declaration-version")
            .value || "").trim();
        if (!sPath && !sPurpose && !sVersion) return null;
        return {
            sBinaryPath: sPath,
            sPurpose: sPurpose,
            sExpectedVersion: sVersion,
        };
    }

    function _fnSetWaiverCheckbox(bChecked) {
        var elBox = _felGet(_S_WAIVER_ID);
        if (elBox) elBox.checked = bChecked;
    }

    function _fbReadWaiverCheckbox() {
        var elBox = _felGet(_S_WAIVER_ID);
        return elBox ? !!elBox.checked : false;
    }

    function fnOpen() {
        _fnClearError();
        var dictState = _fdictCurrentBinaryState();
        _fnSetWaiverCheckbox(dictState.bNoStandaloneBinaries);
        _fnRenderRows(dictState.listDeclaredBinaries);
        var elModal = _felGet(_S_MODAL_ID);
        if (elModal) elModal.style.display = "flex";
    }

    function fnClose() {
        var elModal = _felGet(_S_MODAL_ID);
        if (elModal) elModal.style.display = "none";
    }

    function fnAddRow() {
        _fnAppendRow({});
    }

    async function fnCaptureRow(iIndex) {
        var elList = _felGet(_S_LIST_ID);
        if (!elList) return;
        var listRows = elList.querySelectorAll(".binary-declaration-row");
        if (iIndex < 0 || iIndex >= listRows.length) return;
        await _fnCaptureSingleRow(listRows[iIndex]);
    }

    async function fnSave() {
        _fnClearError();
        var dictBody = _fdictBuildSaveBody();
        var sError = _fsValidateBody(dictBody);
        if (sError) {
            _fnShowError(sError);
            return;
        }
        var sContainerId = PipeleyenApp.fsGetContainerId();
        if (!sContainerId) return;
        await _fnPostSaveAndApply(sContainerId, dictBody);
    }

    function _fdictBuildSaveBody() {
        var bWaiver = _fbReadWaiverCheckbox();
        var listEntries = _flistReadRowsFromForm();
        return {
            bNoStandaloneBinaries: bWaiver,
            listDeclaredBinaries: bWaiver ? [] : listEntries,
        };
    }

    function _fsValidateBody(dictBody) {
        if (dictBody.bNoStandaloneBinaries) {
            return "";
        }
        var listEntries = dictBody.listDeclaredBinaries;
        if (!listEntries.length) {
            return "Add at least one binary or check the waiver.";
        }
        for (var i = 0; i < listEntries.length; i++) {
            var dictRow = listEntries[i];
            if (!dictRow.sBinaryPath || !dictRow.sPurpose ||
                !dictRow.sExpectedVersion) {
                return "Each row needs path, purpose, and version.";
            }
        }
        return "";
    }

    async function _fnPostSaveAndApply(sContainerId, dictBody) {
        try {
            var dictResult = await VaibifyApi.fdictPost(
                "/api/workflow/" +
                    encodeURIComponent(sContainerId) +
                    "/binaries/declare",
                dictBody,
            );
            _fnApplyResultToWorkflow(dictResult);
            fnClose();
        } catch (error) {
            _fnShowError(_fsExtractErrorDetail(error));
        }
    }

    function _fnApplyResultToWorkflow(dictResult) {
        var dictWorkflow = PipeleyenApp.fdictGetWorkflow();
        if (!dictWorkflow) return;
        dictWorkflow.bNoStandaloneBinaries =
            !!dictResult.bNoStandaloneBinaries;
        dictWorkflow.listDeclaredBinaries =
            dictResult.listDeclaredBinaries || [];
    }

    return {
        fnOpen: fnOpen,
        fnClose: fnClose,
        fnSave: fnSave,
        fnAddRow: fnAddRow,
        fnCaptureRow: fnCaptureRow,
    };
}());
