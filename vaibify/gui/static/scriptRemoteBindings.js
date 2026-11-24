/* Vaibify — which remote a workflow's tokens may act on: the researcher reviews and confirms.
   A token-bearing action spends the researcher's Overleaf or Zenodo token
   only on a target confirmed in the host-side binding record, never on
   what project.json says, because the in-container agent can edit
   project.json. This module renders the server's answer
   (GET /api/bindings/{id}): one notice per entry that applies and is not
   confirmed or disagrees, and a review dialog that sets side by side what
   project.json names, what is confirmed and what the host could see with
   the researcher's own token, then posts, unchanged, the review body the
   server offered. It derives no verdict of its own. An unreadable record's
   notice offers Move it aside, which asks the server to rename the record
   beside itself and then reads the notices again.

   A confirmation is tied to the view it was reviewed in. The context
   captured when a refusal opens the dialog is compared with the live one
   before the evidence renders, before the confirm posts, after a
   credential prompt resolves, after the confirm resolves and before the
   refused action is retried; on any difference the dialog closes and
   nothing is run again. The server's BINDING-REVIEW-STALE is the second
   control. */

var VaibifyRemoteBindings = (function () {
    "use strict";

    var _S_BANNER_ID = "remoteBindingBanner";
    var _S_MODAL_ID = "modalRemoteBinding";
    var _S_BODY_ID = "remoteBindingBody";
    var _S_ACTIONS_ID = "remoteBindingActions";
    var _S_RECORD_INVALID = "BINDING-RECORD-INVALID";
    var _S_RECORD_VALID = "BINDING-RECORD-VALID";
    var _LIST_DIALOG_CODES = ["BINDING-REQUIRED", "BINDING-MISMATCH"];
    var _S_CONFIRMED = "confirmed";
    var _S_CANCELLED = "cancelled";
    var _S_VIEW_CHANGED = "view-changed";
    var _S_VIEW_CHANGED_MESSAGE = "The project view changed while you " +
        "were confirming, so the action was not run again. Run it again " +
        "in this project if you meant to.";
    var _S_IF_NOT_YOURS = "If you did not make this change, an agent " +
        "edited project.json.";
    // The binding names Zenodo by service; the token slots and the
    // credential prompt name the permanent instance "production".
    var _DICT_TOKEN_SLOT_FOR_SERVICE = {zenodo: "production", sandbox: "sandbox"};
    // The server's evidence says which actions a failure offers
    // (vaibify.config.credentialFailures); these are their labels.
    var _S_ACTION_STORE = "store";
    var _S_ACTION_REPLACE = "replace";
    var _S_ACTION_RETRY = "retry";
    var _DICT_TOKEN_ACTION_LABELS = {
        store: "Store your token", replace: "Replace your token",
    };
    var _RE_OVERLEAF_PROJECT_ID = /^[A-Za-z0-9]+$/;
    var _S_NOTHING_TO_REVIEW = "Nothing needs your review now. An action " +
        "that would spend your Overleaf or Zenodo token on a target nobody " +
        "has confirmed on this computer asks you first.";
    var _sContainerId = "";
    var _dictDialog = null;

    function _fsUrl(sContainerId, sSuffix) {
        return "/api/bindings/" + encodeURIComponent(sContainerId) +
            (sSuffix || "");
    }

    function fdictCaptureContext() {
        return {
            sContainerId: VaibifyApp.fsGetContainerId(),
            sWorkflowKey: "",
            iViewGeneration: VaibifyApp.fiGetViewGeneration(),
        };
    }

    function _fbContextIsCurrent(dictContext) {
        return VaibifyApp.fsGetContainerId() === dictContext.sContainerId &&
            VaibifyApp.fiGetViewGeneration() === dictContext.iViewGeneration;
    }

    /* ---------------- Notices ---------------- */

    async function fnRefresh(sContainerId) {
        /* Asked on project open and after a reconnect, without evidence:
           a notice needs no network call beyond this one. A failure
           leaves the notices as they were and says so in the console. */
        if (!sContainerId) return;
        _sContainerId = sContainerId;
        var dictContext = fdictCaptureContext();
        try {
            var dictDescribed = await VaibifyApi.fdictGet(
                _fsUrl(sContainerId, "?bFetchEvidence=false"));
            if (_fbContextIsCurrent(dictContext)) fnRenderNotices(dictDescribed);
        } catch (error) {
            console.warn("[remote-bindings] could not read the bindings:",
                error && error.message);
        }
    }

    function _flistNotices(dictDescribed) {
        var dictState = (dictDescribed && dictDescribed.dictRecordState) || {};
        if (dictState.bValid === false) {
            return [{sNotice: dictState.sMessage, dictEntry: null}];
        }
        return ((dictDescribed && dictDescribed.listEntries) || []).filter(
            function (dictEntry) {
                return dictEntry.bApplies && dictEntry.sNotice;
            }).map(function (dictEntry) {
                return {sNotice: dictEntry.sNotice, dictEntry: dictEntry};
            });
    }

    function _flistNoticeContent(listNotices) {
        /* What the banner shows, as the server said it: the key a
           dismissal holds, so a different set of notices shows again. */
        return listNotices.map(function (dictNotice) {
            var dictEntry = dictNotice.dictEntry;
            return [dictNotice.sNotice, dictEntry ? [
                dictEntry.sService, dictEntry.sRecordKind,
                dictEntry.sInstance, dictEntry.sState] : null];
        });
    }

    function fnRenderNotices(dictDescribed, bAskedFor) {
        /* Drawn by itself only when there is a notice, and then until
           the researcher hides it: the x holds while the notices and
           the view are the ones it hid. Asked for through the chip, it
           is drawn whatever the answer, and says so when there is
           nothing to review. Hiding it authorizes nothing: every
           token-spending action is refused by the server until its
           target is confirmed. */
        var elBanner = document.getElementById(_S_BANNER_ID);
        if (!elBanner) return;
        elBanner.textContent = "";
        var listNotices = _flistNotices(dictDescribed);
        var sContentKey = VaibifyNoticeBanners.fsBuildContentKey(
            _flistNoticeContent(listNotices));
        if (bAskedFor) {
            VaibifyNoticeBanners.fnForgetDismissal(_S_BANNER_ID);
        } else if (!listNotices.length ||
                VaibifyNoticeBanners.fbIsDismissed(_S_BANNER_ID, sContentKey)) {
            elBanner.style.display = "none";
            return;
        }
        elBanner.appendChild(_felementNoticeHeader(elBanner, sContentKey));
        if (!listNotices.length) {
            var elNothing = document.createElement("div");
            elNothing.className = "remote-binding-row remote-binding-nothing";
            elNothing.textContent = _S_NOTHING_TO_REVIEW;
            elBanner.appendChild(elNothing);
        }
        listNotices.forEach(function (dictNotice) {
            elBanner.appendChild(_felementNoticeRow(dictNotice));
        });
        elBanner.style.display = "block";
    }

    function _felementNoticeHeader(elBanner, sContentKey) {
        var elHeader = document.createElement("div");
        elHeader.className = "build-warnings-banner-header";
        var elHeading = document.createElement("span");
        elHeading.textContent = "Remote bindings to review before a token is spent";
        var elDismiss = VaibifyNoticeBanners.felementBuildDismissButton(
            _S_BANNER_ID, sContentKey, function () {
                elBanner.style.display = "none";
                elBanner.textContent = "";
            });
        elDismiss.id = "btnDismissRemoteBindings";
        elHeader.appendChild(elHeading);
        elHeader.appendChild(
            VaibifyNoticeBanners.felementBuildHelpButton(_S_BANNER_ID));
        elHeader.appendChild(elDismiss);
        return elHeader;
    }

    async function fnShowNoticesOnDemand() {
        /* Asked of the server now: the notices rendered at open may
           describe a project.json that has changed since. */
        var sContainerId = _sContainerId;
        if (!sContainerId) return;
        var dictContext = fdictCaptureContext();
        var dictDescribed;
        try {
            dictDescribed = await VaibifyApi.fdictGet(
                _fsUrl(sContainerId, "?bFetchEvidence=false"));
        } catch (error) {
            VaibifyDiagnosis.fnReportFailureFromError(error);
            return;
        }
        if (_fbContextIsCurrent(dictContext)) fnRenderNotices(dictDescribed, true);
    }

    function _felementNoticeRow(dictNotice) {
        var elRow = document.createElement("div");
        elRow.className = "remote-binding-row";
        var elText = document.createElement("div");
        elText.textContent = dictNotice.sNotice;
        elRow.appendChild(elText);
        var dictEntry = dictNotice.dictEntry;
        if (!dictEntry) {
            elRow.dataset.recordInvalid = "1";
            elRow.appendChild(_felementSetAsideButton());
            return elRow;
        }
        elRow.dataset.service = dictEntry.sService;
        elRow.dataset.recordKind = dictEntry.sRecordKind;
        elRow.dataset.instance = dictEntry.sInstance;
        var elButton = _felementButton("Review and confirm…", "btn-small");
        elButton.addEventListener("click", function () {
            fpromiseReviewAndConfirm(dictEntry.sService, dictEntry.sRecordKind,
                dictEntry.sInstance, fdictCaptureContext())
                .then(function (sOutcome) {
                    if (sOutcome === _S_CONFIRMED) fnRefresh(_sContainerId);
                });
        });
        elRow.appendChild(elButton);
        return elRow;
    }

    function _felementSetAsideButton() {
        var elButton = _felementButton("Move it aside", "btn-small");
        elButton.title = "Rename the unreadable record beside itself " +
            "(nothing is deleted), then confirm this workflow's remotes again.";
        elButton.addEventListener("click", function () {
            _fnSetAsideUnreadableRecord(elButton);
        });
        return elButton;
    }

    async function _fnSetAsideUnreadableRecord(elButton) {
        /* The server decides whether the record is unreadable now; the
           notices are then read again, so the banner shows its answer. */
        var sContainerId = _sContainerId;
        elButton.disabled = true;
        try {
            await VaibifyApi.fdictPost(_fsUrl(sContainerId, "/set-aside"), {});
        } catch (error) {
            elButton.disabled = false;
            var dictDetail = (error && error.dictDetail) || {};
            if (dictDetail.sError !== _S_RECORD_VALID) {
                VaibifyDiagnosis.fnReportFailureFromError(error);
                return;
            }
            VaibifyApp.fnShowToast(dictDetail.sMessage, "warning");
        }
        fnRefresh(sContainerId);
    }

    function _felementButton(sLabel, sClass) {
        var elButton = document.createElement("button");
        elButton.type = "button";
        elButton.className = "btn " + (sClass || "");
        elButton.textContent = sLabel;
        return elButton;
    }

    /* ---------------- The review dialog ---------------- */

    function fpromiseReviewAndConfirm(sService, sRecordKind, sInstance, dictContext) {
        /* Resolves "confirmed", "cancelled" or "view-changed". One
           dialog at a time: a second request settles the first as
           cancelled. */
        _fnSettleDialog(_dictDialog, _S_CANCELLED);
        return new Promise(function (fnResolve) {
            _dictDialog = {
                dictContext: dictContext,
                listAddress: [sService, sRecordKind || "", sInstance || ""],
                dictEntry: null, bCredentialInFlight: false,
                sLastStoreFailure: "", bPosting: false,
                fnResolve: fnResolve,
            };
            _fnShowModal(true);
            _fnFetchForDialog(_dictDialog);
        });
    }

    function _fsAddressQuery(listAddress) {
        return "?sService=" + encodeURIComponent(listAddress[0]) +
            "&sRecordKind=" + encodeURIComponent(listAddress[1]) +
            "&sInstance=" + encodeURIComponent(listAddress[2]);
    }

    async function _fnFetchForDialog(dictDialog) {
        _fnRenderStatus(dictDialog, "Asking what the host can see with your token…");
        var dictDescribed;
        try {
            dictDescribed = await VaibifyApi.fdictGet(_fsUrl(
                dictDialog.dictContext.sContainerId,
                _fsAddressQuery(dictDialog.listAddress)));
        } catch (error) {
            if (_fbDialogIsStale(dictDialog)) return;
            _fnRenderUnreadable(dictDialog, error);
            return;
        }
        if (_fbDialogIsStale(dictDialog)) return;
        if (!_fbAdoptWorkflowKey(dictDialog, dictDescribed.sWorkflowKey)) return;
        _fnRenderDialog(dictDialog, dictDescribed);
    }

    function _fbAdoptWorkflowKey(dictDialog, sWorkflowKey) {
        /* The first answer names the workflow under review; a later
           answer about another workflow is a changed view. */
        var dictContext = dictDialog.dictContext;
        if (!dictContext.sWorkflowKey) dictContext.sWorkflowKey = sWorkflowKey || "";
        if (dictContext.sWorkflowKey === (sWorkflowKey || "")) return true;
        _fnCloseForChangedView(dictDialog);
        return false;
    }

    function _fbDialogIsStale(dictDialog) {
        if (dictDialog !== _dictDialog) return true;
        if (_fbContextIsCurrent(dictDialog.dictContext)) return false;
        _fnCloseForChangedView(dictDialog);
        return true;
    }

    function _fnCloseForChangedView(dictDialog) {
        _fnSettleDialog(dictDialog, _S_VIEW_CHANGED);
        VaibifyApp.fnShowToast(_S_VIEW_CHANGED_MESSAGE, "warning");
    }

    function _fnSettleDialog(dictDialog, sOutcome) {
        if (!dictDialog || dictDialog !== _dictDialog) return;
        _dictDialog = null;
        _fnShowModal(false);
        dictDialog.fnResolve(sOutcome);
    }

    function _fnShowModal(bShow) {
        var elModal = document.getElementById(_S_MODAL_ID);
        if (elModal) elModal.style.display = bShow ? "flex" : "none";
    }

    function _felementClearedChild(sId) {
        var elTarget = document.getElementById(sId);
        elTarget.textContent = "";
        return elTarget;
    }

    function _fnRenderStatus(dictDialog, sStatus) {
        var elBody = _felementClearedChild(_S_BODY_ID);
        _fnAppendParagraph(elBody, sStatus, "remote-binding-status");
        _fnRenderButtons(dictDialog, {});
    }

    function _fnAppendParagraph(elParent, sText, sClass) {
        var elParagraph = document.createElement("p");
        if (sClass) elParagraph.className = sClass;
        elParagraph.textContent = sText;
        elParent.appendChild(elParagraph);
        return elParagraph;
    }

    function _fnRenderUnreadable(dictDialog, error) {
        var elBody = _felementClearedChild(_S_BODY_ID);
        _fnAppendParagraph(elBody, "Couldn't read this binding: " +
            ((error && error.message) || "no answer"),
            "remote-binding-evidence remote-binding-evidence--unchecked");
        _fnRenderButtons(dictDialog, {bRetry: true});
    }

    function _fdictFindEntry(dictDescribed, listAddress) {
        return ((dictDescribed && dictDescribed.listEntries) || []).filter(
            function (dictEntry) {
                return dictEntry.sService === listAddress[0] &&
                    dictEntry.sRecordKind === listAddress[1] &&
                    dictEntry.sInstance === listAddress[2];
            })[0] || null;
    }

    function _fnRenderDialog(dictDialog, dictDescribed) {
        var elBody = _felementClearedChild(_S_BODY_ID);
        var dictState = dictDescribed.dictRecordState || {};
        var dictEntry = _fdictFindEntry(dictDescribed, dictDialog.listAddress);
        if (dictState.bValid === false || !dictEntry) {
            _fnAppendParagraph(elBody, dictState.sMessage ||
                "This binding is not one vaibify knows.", "remote-binding-record-invalid");
            _fnRenderButtons(dictDialog, {});
            return;
        }
        dictDialog.dictEntry = dictEntry;
        if (dictEntry.bHasTarget === false) {
            /* Nothing names a record (the environment archive, or a
               project record nobody has created yet), so a comparison of
               records would read as broken; the server says what
               confirming allows. */
            _fnAppendParagraph(elBody, dictEntry.sAuthorizes,
                "remote-binding-authorization");
        } else {
            _fnAppendParagraph(elBody, _fsAuthorizationSentence(dictEntry),
                "remote-binding-authorization");
            elBody.appendChild(_felementComparison(dictEntry));
        }
        if (dictEntry.sState === "differs") {
            _fnAppendParagraph(elBody, _S_IF_NOT_YOURS, "remote-binding-warning");
        }
        var dictEvidence = dictEntry.dictEvidence || {};
        elBody.appendChild(_felementEvidence(dictEntry, dictEvidence));
        if (dictDialog.sLastStoreFailure && !dictEvidence.bSucceeded) {
            _fnAppendParagraph(elBody, "Your token was not stored: " +
                dictDialog.sLastStoreFailure, "remote-binding-refusal");
        }
        var listActions = dictEvidence.listActions || [];
        _fnRenderButtons(dictDialog, {
            bConfirm: Boolean(dictEvidence.bSucceeded),
            bRetry: listActions.indexOf(_S_ACTION_RETRY) >= 0,
            sTokenAction: _fsTokenActionOf(listActions),
        });
    }

    function _fsTokenActionOf(listActions) {
        /* Store when none is stored, Replace when one is; the server's
           list decides whether either is offered at all. */
        if (listActions.indexOf(_S_ACTION_STORE) >= 0) return _S_ACTION_STORE;
        if (listActions.indexOf(_S_ACTION_REPLACE) >= 0) return _S_ACTION_REPLACE;
        return "";
    }

    function _fsAuthorizationSentence(dictEntry) {
        var dictTarget = dictEntry.dictReview.dictTarget;
        if (dictEntry.sService === "overleaf") {
            return "Allow this workflow's Overleaf pushes and pulls to use this project.";
        }
        var sSite = VaibifyWorkflowRequirements.fsDescribeZenodoService(dictEntry.sInstance);
        if (!dictTarget.sConceptDoi && !dictTarget.sDepositionId) {
            return "Allow a NEW " + (dictEntry.sInstance === "zenodo" ? "permanent " : "") +
                "record on " + VaibifyWorkflowRequirements.fsZenodoSitePhrase(
                    dictEntry.sInstance) + " for this workflow's project deposit.";
        }
        var sTitle = (dictEntry.dictEvidence || {}).sTitle;
        return "Allow this workflow's project deposits on " + sSite +
            " to use the record " + (sTitle ? "“" + sTitle + "” " : "") +
            "(" + (dictTarget.sConceptDoi || "deposition " + dictTarget.sDepositionId) + ").";
    }

    function _fsDescribeTarget(dictTarget) {
        if (!dictTarget) return "nothing";
        var listParts = ["sProjectId", "sConceptDoi", "sDepositionId"].filter(
            function (sField) { return dictTarget[sField]; }).map(
            function (sField) { return dictTarget[sField]; });
        return listParts.join(", ") || "no record yet";
    }

    function _felementComparison(dictEntry) {
        var elList = document.createElement("dl");
        elList.className = "remote-binding-comparison";
        [["project.json names", dictEntry.dictProjectTarget
            ? _fsDescribeTarget(dictEntry.dictProjectTarget)
            : "nothing for " + dictEntry.sLabel],
         ["You confirmed", dictEntry.dictConfirmed
            ? _fsDescribeTarget(dictEntry.dictConfirmed.dictTarget) : "nothing yet"],
         ["Confirm records", _fsDescribeTarget(dictEntry.dictReview.dictTarget)],
        ].forEach(function (listPair) {
            var elTerm = document.createElement("dt");
            elTerm.textContent = listPair[0];
            var elValue = document.createElement("dd");
            elValue.textContent = listPair[1];
            elList.appendChild(elTerm);
            elList.appendChild(elValue);
        });
        return elList;
    }

    function _felementEvidence(dictEntry, dictEvidence) {
        var elEvidence = document.createElement("div");
        elEvidence.className = "remote-binding-evidence";
        if (!dictEvidence.bSucceeded) {
            /* A failed check is never red: nothing about the remote
               was learned. The server's sentence says what failed and
               what to do, so it is shown as written. */
            elEvidence.classList.add("remote-binding-evidence--unchecked");
            _fnAppendParagraph(elEvidence, dictEvidence.sMessage || "no answer");
            return elEvidence;
        }
        _fnAppendParagraph(elEvidence, _fsDescribeEvidence(dictEntry, dictEvidence));
        if (dictEntry.sService === "overleaf") {
            elEvidence.appendChild(_felementOverleafLink(dictEntry.dictReview.dictTarget));
        }
        return elEvidence;
    }

    function _fsDescribeEvidence(dictEntry, dictEvidence) {
        if (dictEvidence.sState === "readable") {
            return "Your Overleaf token can read this project (" +
                dictEvidence.iFileCount + " files, latest commit " +
                String(dictEvidence.sHeadSha || "").slice(0, 7) + ").";
        }
        if (dictEvidence.sState === "token-accepted") {
            return "Zenodo accepts your token for " +
                VaibifyWorkflowRequirements.fsZenodoSitePhrase(dictEntry.sInstance) + ".";
        }
        var sSeen = "Zenodo reports the record “" + (dictEvidence.sTitle || "") +
            "”, owner " + (dictEvidence.sOwner || "unknown") + ".";
        return dictEvidence.sState === "published-record"
            ? sSeen + " A public record shows its title and owner; it does not prove the record is yours."
            : sSeen;
    }

    function _felementOverleafLink(dictTarget) {
        var elLink = document.createElement("a");
        var sProjectId = String(dictTarget.sProjectId || "");
        if (_RE_OVERLEAF_PROJECT_ID.test(sProjectId)) {
            elLink.href = "https://www.overleaf.com/project/" + sProjectId;
        }
        elLink.target = "_blank";
        elLink.rel = "noopener";
        elLink.textContent = "Open the project on Overleaf";
        return elLink;
    }

    function _fnRenderButtons(dictDialog, dictOffer) {
        var elActions = _felementClearedChild(_S_ACTIONS_ID);
        var listButtons = [["Cancel", "btnRemoteBindingCancel", "", function () {
            _fnSettleDialog(dictDialog, _S_CANCELLED);
        }]];
        if (dictOffer.sTokenAction) {
            listButtons.push([
                _DICT_TOKEN_ACTION_LABELS[dictOffer.sTokenAction],
                "btnRemoteBindingStoreToken", "",
                function () { _fnStoreToken(dictDialog); }]);
        }
        if (dictOffer.bRetry) {
            listButtons.push(["Retry", "btnRemoteBindingRetry", "",
                function () { _fnFetchForDialog(dictDialog); }]);
        }
        listButtons.push(["Confirm", "btnRemoteBindingConfirm", "btn-primary",
            function (event) { _fnConfirm(dictDialog, event.currentTarget); }]);
        listButtons.forEach(function (listButton) {
            var elButton = _felementButton(listButton[0], listButton[2]);
            elButton.id = listButton[1];
            elButton.addEventListener("click", listButton[3]);
            elActions.appendChild(elButton);
        });
        document.getElementById("btnRemoteBindingConfirm").disabled = !dictOffer.bConfirm;
    }

    async function _fnStoreToken(dictDialog) {
        /* Offered every time the evidence calls for it: a store that
           failed, or a box that was cancelled, leaves the researcher a
           way forward. Only one token box is in flight at a time, and
           the evidence is asked again once it closes, whatever the
           answer; a failed store's sentence is kept for the redraw. */
        if (dictDialog.bCredentialInFlight) return;
        dictDialog.bCredentialInFlight = true;
        _fnShowModal(false);
        var dictOutcome;
        try {
            dictOutcome = await VaibifySyncManager.fpromiseStoreCredentialForReview(
                dictDialog.listAddress[0], _fdictTokenTargetOf(dictDialog));
        } finally {
            dictDialog.bCredentialInFlight = false;
        }
        if (_fbDialogIsStale(dictDialog)) return;
        dictDialog.sLastStoreFailure = (dictOutcome && !dictOutcome.bConnected)
            ? (dictOutcome.sMessage || "") : "";
        _fnShowModal(true);
        _fnFetchForDialog(dictDialog);
    }

    function _fdictTokenTargetOf(dictDialog) {
        /* What the token box needs to store a token for THIS entry: the
           Zenodo instance, or the Overleaf project under review. */
        var dictTarget = ((dictDialog.dictEntry || {}).dictReview || {}).dictTarget || {};
        return {
            sInstance: _DICT_TOKEN_SLOT_FOR_SERVICE[dictDialog.listAddress[2]] || "sandbox",
            sProjectId: String(dictTarget.sProjectId || ""),
        };
    }

    async function _fnConfirm(dictDialog, elButton) {
        if (dictDialog.bPosting || !dictDialog.dictEntry) return;
        if (_fbDialogIsStale(dictDialog)) return;
        dictDialog.bPosting = true;
        elButton.disabled = true;
        try {
            await VaibifyApi.fdictPost(
                _fsUrl(dictDialog.dictContext.sContainerId, "/confirm"),
                dictDialog.dictEntry.dictReview);
        } catch (error) {
            dictDialog.bPosting = false;
            if (_fbDialogIsStale(dictDialog)) return;
            _fnRenderRefusedConfirm(dictDialog, error);
            return;
        }
        if (_fbDialogIsStale(dictDialog)) return;
        _fnSettleDialog(dictDialog, _S_CONFIRMED);
    }

    function _fnRenderRefusedConfirm(dictDialog, error) {
        /* The server's own sentence, then the way back: a stale review
           is reviewed again rather than confirmed blind. */
        var elBody = document.getElementById(_S_BODY_ID);
        _fnAppendParagraph(elBody, "Nothing was confirmed. " +
            ((error && error.message) || ""), "remote-binding-refusal");
        _fnRenderButtons(dictDialog, {bRetry: true});
    }

    /* ---------------- A refused action ---------------- */

    function fbRefusalNeedsABinding(error) {
        var dictDetail = (error && error.dictDetail) || {};
        return Boolean(error && error.iStatus === 409 && (
            _LIST_DIALOG_CODES.indexOf(dictDetail.sError) >= 0 ||
            dictDetail.sError === _S_RECORD_INVALID));
    }

    async function fnResolveRefusalThenRetry(error, fnReportRefusal, fpromiseRetry) {
        /* An invalid record is reported and never retried; otherwise
           the dialog opens, and only a confirmation made in the same
           view retries the action, once. */
        var dictDetail = error.dictDetail || {};
        if (dictDetail.sError === _S_RECORD_INVALID) {
            fnReportRefusal(dictDetail.sMessage || error.message);
            fnRefresh(VaibifyApp.fsGetContainerId());
            return;
        }
        var dictContext = fdictCaptureContext();
        var sOutcome = await fpromiseReviewAndConfirm(dictDetail.sService,
            dictDetail.sRecordKind, dictDetail.sInstance, dictContext);
        if (sOutcome === _S_CANCELLED) {
            fnReportRefusal(dictDetail.sMessage || error.message);
            return;
        }
        if (sOutcome !== _S_CONFIRMED) return;
        if (!_fbContextIsCurrent(dictContext)) {
            VaibifyApp.fnShowToast(_S_VIEW_CHANGED_MESSAGE, "warning");
            return;
        }
        fnRefresh(dictContext.sContainerId);
        await fpromiseRetry();
    }

    return {
        fnRefresh: fnRefresh,
        fnRenderNotices: fnRenderNotices,
        fnShowNoticesOnDemand: fnShowNoticesOnDemand,
        fdictCaptureContext: fdictCaptureContext,
        fpromiseReviewAndConfirm: fpromiseReviewAndConfirm,
        fbRefusalNeedsABinding: fbRefusalNeedsABinding,
        fnResolveRefusalThenRetry: fnResolveRefusalThenRetry,
    };
})();
