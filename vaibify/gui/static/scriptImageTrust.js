/* The image-trust modal: how may an image vaibify did not build run?

   A persistent container is not created from an image vaibify did not
   build until the researcher has chosen restricted, as its author built
   it, or inspect only, for that exact image digest. The server refuses
   the start and sends this module the question; the answer goes back to
   a route the in-container agent cannot reach.

   Every word the researcher reads about the three options, and about
   the credentials box, comes from the server's prompt
   (vaibify/config/imageTrust.py), the same text the command line
   prints. This module owns layout and behavior only, so a change to the
   wording is one edit, in one place. Nothing is preselected: Continue
   stays disabled until a radio is chosen. */

var VaibifyImageTrust = (function () {
    "use strict";

    var S_MODAL_ID = "modalImageTrust";
    var S_CHOICE_NAME = "imageTrustChoice";
    var S_INSPECT_CHOICE = "inspect";

    function _fsEscape(sText) {
        return VaibifyUtilities.fnEscapeHtml(sText);
    }

    function _fsMegabytes(iBytes) {
        var fMegabytes = Number(iBytes) / 1048576;
        return (isFinite(fMegabytes) ? fMegabytes.toFixed(1) : "0.0") +
            " MB";
    }

    function _fsProvenanceRow(sLabel, sValue) {
        return "<tr><th scope=\"row\">" + _fsEscape(sLabel) + "</th>" +
            "<td>" + _fsEscape(sValue) + "</td></tr>";
    }

    function _fsProvenanceMarkup(dictPrompt) {
        var sVerified = dictPrompt.bDigestMatchesRecordedDeposit
            ? "yes" : "no record to compare against";
        return "<table class=\"image-trust-provenance\">" +
            _fsProvenanceRow("Digest", dictPrompt.sImageDigest) +
            _fsProvenanceRow("Obtained from",
                dictPrompt.sObtainedFrom || dictPrompt.sVersionDoi ||
                dictPrompt.sPinnedImageReference || "unknown") +
            _fsProvenanceRow("Matches the deposit's recorded digest",
                sVerified) +
            _fsProvenanceRow("Size", _fsMegabytes(dictPrompt.iSizeBytes)) +
            _fsProvenanceRow("Declared USER",
                dictPrompt.sDeclaredUser || "none declared") +
            _fsProvenanceRow("Declared ENTRYPOINT",
                (dictPrompt.listDeclaredEntrypoint || []).join(" ") ||
                "none declared") +
            "</table>";
    }

    function _fsDetailsMarkup(listLines, sExtraMarkup) {
        var sItems = (listLines || []).map(function (sLine) {
            return "<li>" + _fsEscape(sLine) + "</li>";
        }).join("");
        return "<details class=\"image-trust-details\">" +
            "<summary>What this means</summary><ul>" + sItems + "</ul>" +
            (sExtraMarkup || "") + "</details>";
    }

    function _fsDocumentationLinkMarkup(sUrl) {
        if (!/^https:\/\//.test(sUrl || "")) return "";
        return "<p><a href=\"" + _fsEscape(sUrl) + "\" target=\"_blank\" " +
            "rel=\"noopener noreferrer\">What an image must do to run " +
            "restricted</a></p>";
    }

    function _fsOptionMarkup(dictOption, iIndex, sDocumentationUrl) {
        var sId = "imageTrustChoice" + iIndex;
        var sLink = dictOption.sChoice === "restricted"
            ? _fsDocumentationLinkMarkup(sDocumentationUrl) : "";
        return "<div class=\"image-trust-option\" data-choice=\"" +
            _fsEscape(dictOption.sChoice) + "\">" +
            "<label for=\"" + sId + "\">" +
            "<input type=\"radio\" id=\"" + sId + "\" name=\"" +
            S_CHOICE_NAME + "\" value=\"" +
            _fsEscape(dictOption.sChoice) + "\"> " +
            "<strong class=\"image-trust-label\">" +
            _fsEscape(dictOption.sLabel) + "</strong> " +
            "<span class=\"image-trust-summary\">" +
            _fsEscape(dictOption.sSummary) + "</span></label>" +
            _fsDetailsMarkup(dictOption.listDetailLines, sLink) + "</div>";
    }

    function _fsCredentialsMarkup(dictCredentials) {
        return "<div class=\"image-trust-credentials\">" +
            "<label for=\"imageTrustCredentials\">" +
            "<input type=\"checkbox\" id=\"imageTrustCredentials\"> " +
            "<span class=\"image-trust-credentials-label\">" +
            _fsEscape(dictCredentials.sLabel) + "</span></label>" +
            _fsDetailsMarkup(dictCredentials.listDetailLines) + "</div>";
    }

    function _fsModalMarkup(sName, dictPrompt, sNotice) {
        var sOptions = (dictPrompt.listOptions || []).map(function (
            dictOption, iIndex) {
            return _fsOptionMarkup(dictOption, iIndex,
                dictPrompt.sRestrictedDocumentationUrl);
        }).join("");
        return "<div class=\"modal image-trust-modal\" role=\"dialog\" " +
            "aria-modal=\"true\" aria-labelledby=\"imageTrustTitle\">" +
            "<h2 id=\"imageTrustTitle\">How should '" + _fsEscape(sName) +
            "' run this image?</h2>" +
            (sNotice ? "<p class=\"image-trust-notice\">" +
                _fsEscape(sNotice) + "</p>" : "") +
            _fsProvenanceMarkup(dictPrompt) + sOptions +
            _fsCredentialsMarkup(dictPrompt.dictCredentials || {}) +
            "<div class=\"modal-actions\">" +
            "<button class=\"btn\" id=\"btnImageTrustCancel\">Cancel" +
            "</button><button class=\"btn btn-primary\" " +
            "id=\"btnImageTrustContinue\" disabled>Continue</button>" +
            "</div></div>";
    }

    function _fsChosenValue() {
        var elChosen = document.querySelector(
            "#" + S_MODAL_ID + " input[name=" + S_CHOICE_NAME + "]:checked");
        return elChosen ? elChosen.value : "";
    }

    function _fnSyncControls() {
        var sChoice = _fsChosenValue();
        var elCredentials = document.getElementById("imageTrustCredentials");
        var bInspect = sChoice === S_INSPECT_CHOICE;
        if (bInspect) elCredentials.checked = false;
        elCredentials.disabled = bInspect;
        document.getElementById("btnImageTrustContinue").disabled =
            sChoice === "";
    }

    async function _fnSubmitAnswer(sName, dictPrompt, fnOnConfirmed) {
        var dictBody = {
            sImageDigest: dictPrompt.sImageDigest,
            sChoice: _fsChosenValue(),
            bWithCredentials:
                document.getElementById("imageTrustCredentials").checked,
        };
        try {
            var dictResult = await VaibifyApi.fdictPost(
                "/api/registry/" + encodeURIComponent(sName) +
                "/image-trust", dictBody);
            fnHide();
            if (fnOnConfirmed) fnOnConfirmed(dictResult.dictImageTrust);
        } catch (error) {
            VaibifyDiagnosis.fnReportFailureFromError(error);
        }
    }

    function fnHide() {
        var elModal = document.getElementById(S_MODAL_ID);
        if (elModal) elModal.remove();
    }

    /* Ask the researcher. ``dictOptions`` may carry ``sNotice`` (why the
       question is being asked now) and ``fnOnConfirmed`` (called with
       the stored record after the server accepts the answer). Cancel
       creates nothing and records nothing. */
    function fnPromptForImageTrust(sName, dictPrompt, dictOptions) {
        var dictSettings = dictOptions || {};
        fnHide();
        var elModal = document.createElement("div");
        elModal.id = S_MODAL_ID;
        elModal.className = "modal-overlay";
        elModal.style.display = "flex";
        elModal.innerHTML = _fsModalMarkup(
            sName, dictPrompt, dictSettings.sNotice || "");
        document.body.appendChild(elModal);
        elModal.addEventListener("change", _fnSyncControls);
        document.getElementById("btnImageTrustCancel").addEventListener(
            "click", fnHide);
        document.getElementById("btnImageTrustContinue").addEventListener(
            "click", function () {
                _fnSubmitAnswer(
                    sName, dictPrompt, dictSettings.fnOnConfirmed);
            });
    }

    /* The short badge text for a stored answer, or for none yet. The
       three values are the route's own tokens, not prose. */
    function fsBadgeText(dictAnswer) {
        if (!dictAnswer || !dictAnswer.sChoice) {
            return "image trust: not confirmed";
        }
        return "image trust: " + dictAnswer.sChoice +
            (dictAnswer.bWithCredentials ? " + credentials" : "");
    }

    return {
        fnPromptForImageTrust: fnPromptForImageTrust,
        fnHide: fnHide,
        fsBadgeText: fsBadgeText,
    };
})();
