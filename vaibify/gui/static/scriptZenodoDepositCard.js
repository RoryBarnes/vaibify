/* Vaibify — Zenodo Status modal.

   Phase 3 of the Zenodo integration plan, repositioned into a
   View menu entry. Shows the workflow's latest Zenodo deposit
   (DOI, concept DOI, record link) on demand rather than always
   taking screen space. Getting to Level 2 is a final archival
   step, not something the user needs to monitor continuously.

   The modal also lists the DECLARED records — the set of Zenodo
   deposits the archive criteria consult. Zenodo's own GitHub
   integration archives a code release as a separate record with its
   own DOI, so a project legitimately holds a data deposit AND a
   software deposit; declaring the second record here is how the
   verify comes to consult it. Declaring cannot fake agreement:
   files still have to hash-match some declared record.

   Public surface:
   - VaibifyZenodoDepositCard.fnOpen(sContainerId)
       Fetch the deposit summary and open the modal. Opens with a
       "not yet published" message when no deposit exists.
   - VaibifyZenodoDepositCard.fnClose()
       Hide the modal.
   - VaibifyZenodoDepositCard.fnUpdateFromPushResult(dictResult)
       If the modal is currently open, refresh its contents from a
       /archive POST response without a second round-trip. No-op
       when the modal is closed (the post-push toast is enough).
*/

var VaibifyZenodoDepositCard = (function () {
    "use strict";

    var _sContainerId = "";

    function _fsEscape(sText) {
        return VaibifyUtilities.fnEscapeHtml(sText || "");
    }

    async function fnOpen(sContainerId) {
        if (!sContainerId) return;
        _sContainerId = sContainerId;
        var dictSummary;
        try {
            dictSummary = await VaibifyApi.fdictGet(
                "/api/zenodo/" + encodeURIComponent(sContainerId) +
                "/deposit"
            );
        } catch (error) {
            dictSummary = {};
        }
        _fnRender(dictSummary || {});
        _fnShowModal();
        _fnRefreshRecords();
    }

    function fnClose() {
        var elModal = document.getElementById("modalZenodoStatus");
        if (!elModal) return;
        elModal.style.display = "none";
    }

    function fnUpdateFromPushResult(dictResult) {
        var elModal = document.getElementById("modalZenodoStatus");
        if (!elModal || elModal.style.display === "none") return;
        if (!dictResult || !dictResult.sDoi) return;
        _fnRender({
            sDepositionId: String(dictResult.iDepositId || ""),
            sDoi: dictResult.sDoi,
            sConceptDoi: dictResult.sConceptDoi || "",
            sHtmlUrl: dictResult.sHtmlUrl || "",
        });
        _fnRefreshRecords();
    }

    function _fnShowModal() {
        var elModal = document.getElementById("modalZenodoStatus");
        if (elModal) elModal.style.display = "flex";
    }

    function _fnRender(dictSummary) {
        var elBody = document.getElementById("modalZenodoStatusBody");
        if (!elBody) return;
        var sCard = dictSummary.sDoi
            ? _fsBuildCardHtml(dictSummary)
            : _fsBuildEmptyHtml();
        elBody.innerHTML = sCard +
            '<div id="zdcRecordsSection"></div>';
        if (dictSummary.sDoi) {
            _fnBindCardButtons(elBody, dictSummary);
        }
    }

    function _fsBuildEmptyHtml() {
        return (
            '<p class="zdc-empty">' +
            'This project has not been published to Zenodo yet. ' +
            'Use <strong>Sync → Archive to Zenodo</strong> to ' +
            'publish the tracked files and mint a DOI.' +
            '</p>'
        );
    }

    function _fsBuildCardHtml(dictSummary) {
        var sUrl = _fbSafeZenodoUrl(dictSummary.sHtmlUrl)
            ? dictSummary.sHtmlUrl : "";
        var sLinkRow = sUrl
            ? '<div class="zdc-row"><a class="zdc-link" href="' +
              _fsEscape(sUrl) + '" target="_blank" rel="noopener">' +
              'Open on Zenodo ↗</a></div>'
            : "";
        var sConceptRow = dictSummary.sConceptDoi
            ? '<div class="zdc-row">' +
              '<span class="zdc-label">Concept DOI</span>' +
              '<code>' + _fsEscape(dictSummary.sConceptDoi) +
              '</code></div>'
            : "";
        return (
            '<div class="zdc-row">' +
            '<span class="zdc-label">DOI</span>' +
            '<code class="zdc-doi">' + _fsEscape(dictSummary.sDoi) +
            '</code>' +
            '<button type="button" class="zdc-copy" ' +
            'data-doi="' + _fsEscape(dictSummary.sDoi) +
            '">Copy</button>' +
            '</div>' +
            sConceptRow + sLinkRow
        );
    }

    /* --- Declared records --- */

    async function _fnRefreshRecords() {
        var dictPayload;
        try {
            dictPayload = await VaibifyApi.fdictGet(
                "/api/zenodo/" + encodeURIComponent(_sContainerId) +
                "/records"
            );
        } catch (error) {
            dictPayload = {listRecords: [], sPrimaryRecordId: ""};
        }
        _fnRenderRecords(dictPayload || {});
    }

    function _fnRenderRecords(dictPayload) {
        var elSection = document.getElementById("zdcRecordsSection");
        if (!elSection) return;
        var listRecords = dictPayload.listRecords || [];
        var sPrimary = dictPayload.sPrimaryRecordId || "";
        var sRows = "";
        for (var i = 0; i < listRecords.length; i++) {
            sRows += _fsBuildRecordRow(listRecords[i], sPrimary);
        }
        if (!sRows) {
            sRows = '<div class="zdc-records-empty">No Zenodo ' +
                'records declared yet.</div>';
        }
        elSection.innerHTML =
            '<div class="zdc-records-header">Records the archive ' +
            'checks consult</div>' + sRows +
            _fsBuildRecordAddForm();
        _fnBindRecordControls(elSection);
    }

    function _fsBuildRecordRow(dictRecord, sPrimary) {
        var sRecordId = dictRecord.sRecordId || "";
        var bPrimary = sPrimary !== "" && sRecordId === sPrimary;
        var sDoi = dictRecord.sDoi
            ? ' <code class="zdc-record-doi">' +
              _fsEscape(dictRecord.sDoi) + '</code>'
            : "";
        var sTail = bPrimary
            ? '<span class="zdc-record-primary">primary</span>'
            : '<button type="button" class="zdc-record-remove" ' +
              'data-record-id="' + _fsEscape(sRecordId) + '">' +
              'Remove</button>';
        return '<div class="zdc-record-row">' +
            '<code class="zdc-record-id">' + _fsEscape(sRecordId) +
            '</code>' + sDoi + sTail + '</div>';
    }

    function _fsBuildRecordAddForm() {
        return '<div class="zdc-record-add">' +
            '<input type="text" id="zdcRecordAddInput" ' +
            'placeholder="Record id or Zenodo DOI">' +
            '<button type="button" id="zdcRecordAddButton" ' +
            'class="btn">Declare record</button>' +
            '<div class="zdc-record-add-hint">Declare another ' +
            'Zenodo record to consult — for example the software ' +
            'deposit Zenodo’s GitHub integration made for a ' +
            'release.</div>' +
            '</div>';
    }

    function _fnBindRecordControls(elSection) {
        var elAdd = elSection.querySelector("#zdcRecordAddButton");
        if (elAdd) {
            elAdd.addEventListener("click", _fnDeclareRecord);
        }
        var listRemove = elSection.querySelectorAll(
            ".zdc-record-remove");
        for (var i = 0; i < listRemove.length; i++) {
            listRemove[i].addEventListener(
                "click", _fnRemoveRecordFromButton);
        }
    }

    async function _fnDeclareRecord() {
        var elInput = document.getElementById("zdcRecordAddInput");
        var sValue = ((elInput && elInput.value) || "").trim();
        if (!sValue) return;
        var dictBody = /^\d+$/.test(sValue)
            ? {sRecordId: sValue}
            : {sDoi: sValue};
        var dictPayload;
        try {
            dictPayload = await VaibifyApi.fdictPost(
                "/api/zenodo/" + encodeURIComponent(_sContainerId) +
                "/records", dictBody
            );
        } catch (error) {
            VaibifyApp.fnShowToast(
                "Could not declare the record: " +
                VaibifyUtilities.fsSanitizeErrorForUser(
                    error && error.message ? error.message : ""),
                "error");
            return;
        }
        _fnRenderRecords(dictPayload || {});
    }

    async function _fnRemoveRecordFromButton(eventClick) {
        var sRecordId =
            eventClick.currentTarget.getAttribute("data-record-id");
        if (!sRecordId) return;
        var dictPayload;
        try {
            dictPayload = await VaibifyApi.fnDelete(
                "/api/zenodo/" + encodeURIComponent(_sContainerId) +
                "/records/" + encodeURIComponent(sRecordId)
            );
        } catch (error) {
            VaibifyApp.fnShowToast(
                "Could not remove the record: " +
                VaibifyUtilities.fsSanitizeErrorForUser(
                    error && error.message ? error.message : ""),
                "error");
            return;
        }
        _fnRenderRecords(dictPayload || {});
    }

    function _fnBindCardButtons(elBody, dictSummary) {
        var elCopy = elBody.querySelector(".zdc-copy");
        if (!elCopy) return;
        elCopy.addEventListener("click", function () {
            _fnCopyToClipboard(dictSummary.sDoi || "");
            var sOriginal = elCopy.textContent;
            elCopy.textContent = "Copied";
            setTimeout(function () {
                elCopy.textContent = sOriginal;
            }, 1800);
        });
    }

    function _fbSafeZenodoUrl(sUrl) {
        if (!sUrl) return false;
        return sUrl.indexOf("https://zenodo.org/") === 0 ||
            sUrl.indexOf("https://sandbox.zenodo.org/") === 0;
    }

    function _fnCopyToClipboard(sText) {
        if (!sText) return;
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(sText);
            return;
        }
        var elTmp = document.createElement("textarea");
        elTmp.value = sText;
        document.body.appendChild(elTmp);
        elTmp.select();
        try { document.execCommand("copy"); } catch (e) { /* noop */ }
        document.body.removeChild(elTmp);
    }

    return {
        fnOpen: fnOpen,
        fnClose: fnClose,
        fnUpdateFromPushResult: fnUpdateFromPushResult,
    };
})();
