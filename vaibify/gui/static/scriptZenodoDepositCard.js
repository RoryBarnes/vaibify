/* Vaibify — persistent Zenodo deposit card.

   Phase 3 of the Zenodo integration plan. A small always-visible
   block that tells the user which Zenodo deposit their workflow
   is currently linked to, with the DOI, concept DOI, and a link
   to the record. Hidden when the workflow has never been published.

   Public surface:
   - VaibifyZenodoDepositCard.fnRefresh(sContainerId)
       Fetch the deposit summary from the server and render.
   - VaibifyZenodoDepositCard.fnUpdateFromPushResult(dictResult)
       Update in place from the POST /archive response so the card
       refreshes without a second server round-trip after a push.
   - VaibifyZenodoDepositCard.fnClear()
       Hide the card (e.g. on workflow switch).
*/

var VaibifyZenodoDepositCard = (function () {
    "use strict";

    function _fsEscape(sText) {
        return VaibifyUtilities.fnEscapeHtml(sText || "");
    }

    async function fnRefresh(sContainerId) {
        if (!sContainerId) { fnClear(); return; }
        try {
            var dictSummary = await VaibifyApi.fdictGet(
                "/api/zenodo/" + encodeURIComponent(sContainerId) +
                "/deposit"
            );
            _fnRender(dictSummary || {});
        } catch (error) {
            fnClear();
        }
    }

    function fnUpdateFromPushResult(dictResult) {
        if (!dictResult || !dictResult.sDoi) return;
        _fnRender({
            sDepositionId: String(dictResult.iDepositId || ""),
            sDoi: dictResult.sDoi,
            sConceptDoi: dictResult.sConceptDoi || "",
            sHtmlUrl: dictResult.sHtmlUrl || "",
        });
    }

    function fnClear() {
        var elCard = document.getElementById("zenodoDepositCard");
        if (!elCard) return;
        elCard.style.display = "none";
        elCard.innerHTML = "";
    }

    function _fnRender(dictSummary) {
        var elCard = document.getElementById("zenodoDepositCard");
        if (!elCard) return;
        if (!dictSummary.sDoi) { fnClear(); return; }
        elCard.innerHTML = _fsBuildCardHtml(dictSummary);
        elCard.style.display = "";
        _fnBindCardButtons(elCard, dictSummary);
    }

    function _fsBuildCardHtml(dictSummary) {
        var sUrl = _fbSafeZenodoUrl(dictSummary.sHtmlUrl)
            ? dictSummary.sHtmlUrl : "";
        var sLinkRow = sUrl
            ? '<a class="zdc-link" href="' + _fsEscape(sUrl) +
              '" target="_blank" rel="noopener">Open on Zenodo \u2197</a>'
            : "";
        var sConceptRow = dictSummary.sConceptDoi
            ? '<div class="zdc-row zdc-concept">' +
              '<span class="zdc-label">Concept DOI</span>' +
              '<code>' + _fsEscape(dictSummary.sConceptDoi) +
              '</code></div>'
            : "";
        return (
            '<div class="zdc-head">' +
            '<span class="zdc-badge">Zenodo</span>' +
            '<span class="zdc-title">Published deposit</span>' +
            '</div>' +
            '<div class="zdc-row">' +
            '<span class="zdc-label">DOI</span>' +
            '<code class="zdc-doi">' + _fsEscape(dictSummary.sDoi) +
            '</code>' +
            '<button type="button" class="zdc-copy" ' +
            'data-doi="' + _fsEscape(dictSummary.sDoi) +
            '">Copy</button>' +
            '</div>' +
            sConceptRow +
            (sLinkRow ? '<div class="zdc-row">' + sLinkRow +
                        '</div>' : "")
        );
    }

    function _fnBindCardButtons(elCard, dictSummary) {
        var elCopy = elCard.querySelector(".zdc-copy");
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
        fnRefresh: fnRefresh,
        fnUpdateFromPushResult: fnUpdateFromPushResult,
        fnClear: fnClear,
    };
})();
