/* Vaibify — Zenodo tokens moved out of the container: the researcher decides.
   A token that lived in the container keyring is staged on the host as
   PENDING and can be spent by nothing until the researcher confirms it
   here (ruling D6: the container keyring is agent-writable, so the
   token may not be theirs). This banner renders the server's answer
   (GET /api/zenodo-tokens/{id}) -- slot states, the host-side
   validation outcome and what Zenodo reports about the token -- and
   sends the decision back. It derives nothing about the slots itself,
   and it never shows a token value, because the server never sends one. */

var VaibifyZenodoTokens = (function () {
    "use strict";

    var _S_BANNER_ID = "zenodoTokenBanner";
    var _S_DISMISS_ID = "btnDismissZenodoTokens";
    var _sContainerId = "";

    function _fsUrl(sContainerId, sSuffix) {
        return "/api/zenodo-tokens/" + encodeURIComponent(sContainerId) +
            (sSuffix || "");
    }

    async function fnRefresh(sContainerId) {
        /* Asked on project open and after a reconnect, like the remote
           badges. A failure leaves the banner as it was and says so in
           the console; it never paints an answer nobody gave. */
        if (!sContainerId) return;
        _sContainerId = sContainerId;
        try {
            fnRender(await VaibifyApi.fdictGet(_fsUrl(sContainerId)));
        } catch (error) {
            console.warn("[zenodo-tokens] could not read the migration state:",
                error && error.message);
        }
    }

    function fnRender(dictDescribed) {
        var elBanner = document.getElementById(_S_BANNER_ID);
        if (!elBanner) return;
        elBanner.textContent = "";
        var listSlots = (dictDescribed && dictDescribed.listSlots) || [];
        var listAwaiting = listSlots.filter(function (dictSlot) {
            return dictSlot.bAwaitingDecision;
        });
        if (!dictDescribed || !dictDescribed.bApplicable ||
                (!listAwaiting.length && !dictDescribed.bRotationAdvised)) {
            elBanner.style.display = "none";
            return;
        }
        elBanner.appendChild(_felementHeader(listAwaiting.length));
        listAwaiting.forEach(function (dictSlot) {
            elBanner.appendChild(_felementSlotRow(dictSlot));
        });
        if (dictDescribed.bRotationAdvised) {
            elBanner.appendChild(_felementRotationAdvice(
                dictDescribed.dictTokenPages || {}));
        }
        elBanner.style.display = "block";
    }

    function _felementHeader(iAwaiting) {
        var elHeader = document.createElement("div");
        elHeader.className = "build-warnings-banner-header";
        var elHeading = document.createElement("span");
        elHeading.textContent = iAwaiting
            ? "Zenodo tokens moved out of this container await your decision"
            : "Zenodo tokens were moved out of this container";
        var elDismiss = document.createElement("button");
        elDismiss.type = "button";
        elDismiss.className = "build-warnings-banner-dismiss";
        elDismiss.id = _S_DISMISS_ID;
        elDismiss.setAttribute("aria-label",
            "Hide this notice until the container is next opened");
        elDismiss.textContent = "×";
        elDismiss.addEventListener("click", function () {
            var elBanner = document.getElementById(_S_BANNER_ID);
            elBanner.style.display = "none";
            elBanner.textContent = "";
        });
        elHeader.appendChild(elHeading);
        elHeader.appendChild(elDismiss);
        return elHeader;
    }

    function _fsDescribeSlot(dictSlot) {
        var sInstance = dictSlot.sProposedInstance || dictSlot.sInstance;
        var sWhere = sInstance ? " for " + sInstance : " (instance unknown)";
        if (dictSlot.sState === "conflict") {
            return "The container held a " + (dictSlot.sInstance || "Zenodo") +
                " token that differs from the one stored on this computer" +
                (dictSlot.sReason ? ": " + dictSlot.sReason : "") + ".";
        }
        var dictValidation = dictSlot.dictValidation || {};
        var sOutcome = dictValidation.sOutcome || "";
        var sVerdict = sOutcome === "accepted"
            ? "it was accepted by " + (sInstance || "Zenodo")
            : sOutcome === "rejected"
                ? "it was rejected by Zenodo"
                : "vaibify could not reach Zenodo to check it, and will try again";
        return "A token" + sWhere + " was moved out of the container" +
            " (slot " + dictSlot.sSlot + "); " + sVerdict + ".";
    }

    function _felementSlotRow(dictSlot) {
        var elRow = document.createElement("div");
        elRow.className = "zenodo-token-row";
        elRow.dataset.slot = dictSlot.sSlot;
        var elText = document.createElement("div");
        elText.textContent = _fsDescribeSlot(dictSlot);
        elRow.appendChild(elText);
        var listTitles = dictSlot.listDepositTitles || [];
        if (listTitles.length) {
            var elTitles = document.createElement("div");
            elTitles.className = "zenodo-token-titles";
            elTitles.textContent = "Zenodo reports these deposits for it: " +
                listTitles.join("; ");
            elRow.appendChild(elTitles);
        }
        elRow.appendChild(_felementDecisionButtons(dictSlot));
        return elRow;
    }

    function _felementDecisionButtons(dictSlot) {
        var elButtons = document.createElement("div");
        elButtons.className = "zenodo-token-actions";
        var listChoices = dictSlot.sState === "conflict"
            ? [["Keep the token on this computer", "keep-host"],
               ["Review the container's token instead", "stage-pending"]]
            : [["Confirm: this is my token", "confirm"],
               ["Decline: discard it", "decline"]];
        listChoices.forEach(function (tChoice) {
            var elButton = document.createElement("button");
            elButton.type = "button";
            elButton.className = "btn-small";
            elButton.dataset.decision = tChoice[1];
            elButton.textContent = tChoice[0];
            elButton.addEventListener("click", function () {
                _fnDecide(dictSlot, tChoice[1]);
            });
            elButtons.appendChild(elButton);
        });
        return elButtons;
    }

    function _fsInstanceForConfirm(dictSlot) {
        if (dictSlot.sInstance || dictSlot.sProposedInstance) return "";
        var sAnswer = window.prompt(
            "This token's instance is not known. Which is it: sandbox or " +
            "production?", "sandbox");
        return (sAnswer || "").trim().toLowerCase();
    }

    async function _fnDecide(dictSlot, sDecision) {
        var dictBody = {sSlot: dictSlot.sSlot, sDecision: sDecision};
        if (sDecision === "confirm") {
            dictBody.sInstance = _fsInstanceForConfirm(dictSlot);
        }
        try {
            fnRender(await VaibifyApi.fdictPost(
                _fsUrl(_sContainerId, "/decide"), dictBody));
        } catch (error) {
            VaibifyApp.fnShowToast(
                (error && error.message) || "The decision was not recorded",
                "error");
        }
    }

    function _felementRotationAdvice(dictTokenPages) {
        /* Moving a token does not revoke copies an agent may already
           hold; vaibify cannot revoke a token itself. */
        var elAdvice = document.createElement("div");
        elAdvice.className = "zenodo-token-rotation";
        elAdvice.appendChild(document.createTextNode(
            "A token that lived in the container may have been read " +
            "there. Regenerate your Zenodo tokens (and your Overleaf " +
            "token, which was also handed to the container) at: "));
        Object.keys(dictTokenPages).sort().forEach(function (sInstance, iIndex) {
            if (iIndex) elAdvice.appendChild(document.createTextNode(", "));
            var elLink = document.createElement("a");
            elLink.href = dictTokenPages[sInstance];
            elLink.target = "_blank";
            elLink.rel = "noopener";
            elLink.textContent = sInstance;
            elAdvice.appendChild(elLink);
        });
        elAdvice.appendChild(document.createTextNode("."));
        return elAdvice;
    }

    return {
        fnRefresh: fnRefresh,
        fnRender: fnRender,
    };
})();
