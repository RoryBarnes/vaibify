/* Vaibify — Zenodo Status modal.

   Phase 3 of the Zenodo integration plan, repositioned into a
   View menu entry. Shows the workflow's latest Zenodo deposit
   (DOI, concept DOI, record link) on demand rather than always
   taking screen space. Getting to Level 2 is a final archival
   step, not something the user needs to monitor continuously.

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

    function _fsEscape(sText) {
        return VaibifyUtilities.fnEscapeHtml(sText || "");
    }

    async function fnOpen(sContainerId) {
        if (!sContainerId) return;
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
    }

    function _fnShowModal() {
        var elModal = document.getElementById("modalZenodoStatus");
        if (elModal) elModal.style.display = "flex";
    }

    function _fnRender(dictSummary) {
        var elBody = document.getElementById("modalZenodoStatusBody");
        if (!elBody) return;
        if (!dictSummary.sDoi) {
            elBody.innerHTML = _fsBuildEmptyHtml();
            return;
        }
        elBody.innerHTML = _fsBuildCardHtml(dictSummary);
        _fnBindCardButtons(elBody, dictSummary);
    }

    function _fsBuildEmptyHtml() {
        return (
            '<p class="zdc-empty">' +
            'This workflow has not been published to Zenodo yet. ' +
            'Use <strong>Sync \u2192 Archive to Zenodo</strong> to ' +
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
              'Open on Zenodo \u2197</a></div>'
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
