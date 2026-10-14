/* Vaibify — the window a containerization is watched and decided from.

   The hub owns a pending containerization (the download of the author's
   environment, the staging of the image, the switch). This module is a
   browser's window onto that record: it polls GET /api/conversions/<host>
   while the hub is working, renders the bytes, the rate, the time left
   and the attempt state the hub computes, and shows the three decisions
   a researcher can take -- Try again, Finish, Stay in host mode -- when
   the hub is waiting for one. Nothing here is state the hub does not
   hold: a reopened tab re-attaches by reading the same record, and a
   failure stays on screen until dismissed, never as a toast.

   Public surface:
   - VaibifyAcquisitionWindow.fnWatch(sHostName)
       Open the window for a host project the hub is containerizing (or
       waiting on) and follow it to its end.
   - VaibifyAcquisitionWindow.fnClose()
*/

var VaibifyAcquisitionWindow = (function () {
    "use strict";

    var _I_POLL_MILLISECONDS = 1500;
    var _S_TITLE_DOWNLOADING = "Downloading the author’s environment";
    var _S_TITLE_PREPARING = "Preparing the author’s environment";
    var _S_TITLE_WAITING = "The download stopped";
    var _S_TITLE_READY = "The author’s environment is ready";
    var _S_TITLE_INTERRUPTED = "Containerizing was interrupted";

    var _sWatchedHost = null;
    var _sLastNewName = "";
    var _iPollTimer = null;
    var _bLastLive = false;

    function _fsConversionUrl(sHostName, sSuffix) {
        return "/api/conversions/" + encodeURIComponent(sHostName) +
            (sSuffix || "");
    }

    function _fsMegabytes(iBytes) {
        return Math.round((iBytes || 0) / 1e6) + " MB";
    }

    function _fsDuration(fSeconds) {
        if (!fSeconds || !isFinite(fSeconds)) return "";
        if (fSeconds < 90) return Math.max(1, Math.round(fSeconds)) + " s";
        if (fSeconds < 5400) return Math.round(fSeconds / 60) + " min";
        return (fSeconds / 3600).toFixed(1) + " h";
    }

    function _felModal() {
        return document.getElementById("modalAcquisition");
    }

    function _fnRender(sTitle, elBody, listButtons) {
        var elModal = _felModal();
        if (!elModal) return;
        elModal.querySelector(".acquisition-title").textContent = sTitle;
        var elHost = elModal.querySelector(".acquisition-body");
        elHost.textContent = "";
        elHost.appendChild(elBody);
        var elButtons = elModal.querySelector(".acquisition-buttons");
        elButtons.textContent = "";
        listButtons.forEach(function (dictButton) {
            var elButton = document.createElement("button");
            elButton.type = "button";
            elButton.className = dictButton.bPrimary ? "button-primary" : "btn";
            elButton.textContent = dictButton.sLabel;
            elButton.dataset.acquisitionAction = dictButton.sAction;
            elButton.addEventListener("click", dictButton.fnOnClick);
            elButtons.appendChild(elButton);
        });
        elModal.style.display = "flex";
    }

    function _felParagraph(sText, sClass) {
        var elParagraph = document.createElement("p");
        if (sClass) elParagraph.className = sClass;
        elParagraph.textContent = sText;
        return elParagraph;
    }

    function _felDetails(sTechnicalDetail) {
        var elDetails = document.createElement("details");
        elDetails.className = "acquisition-details";
        var elSummary = document.createElement("summary");
        elSummary.textContent = "Technical details";
        elDetails.appendChild(elSummary);
        var elPre = document.createElement("pre");
        elPre.textContent = sTechnicalDetail || "";
        elDetails.appendChild(elPre);
        return elDetails;
    }

    /* ---- the progress view: what the hub computes, nothing else ---- */

    function _fsProgressSentence(dictDownload) {
        var iBytes = dictDownload.iBytes || 0;
        var iTotal = dictDownload.iTotalBytes || 0;
        var sLine = _fsMegabytes(iBytes) + (iTotal ? " of " + _fsMegabytes(iTotal) : "");
        if (dictDownload.fBytesPerSecond > 0) {
            sLine += ", " + (dictDownload.fBytesPerSecond / 1e6).toFixed(1) + " MB/s";
        }
        if (dictDownload.fSecondsRemaining) {
            sLine += ", about " + _fsDuration(dictDownload.fSecondsRemaining) + " left";
        }
        return sLine;
    }

    function _fsSilenceSentence(dictDownload) {
        var fSilent = dictDownload.fSecondsSinceLastByte;
        if (!fSilent || fSilent < 10) return "";
        return "No data from Zenodo for " + _fsDuration(fSilent) + ".";
    }

    function _fnRenderProgress(dictView) {
        var dictDownload = dictView.dictDownload || {};
        var bDownloading = (dictDownload.iTotalBytes || 0) > 0 &&
            (dictDownload.iBytes || 0) < dictDownload.iTotalBytes;
        var elBody = document.createElement("div");
        if (bDownloading) {
            elBody.appendChild(_felParagraph(
                _fsProgressSentence(dictDownload), "acquisition-progress"));
            var sSilence = _fsSilenceSentence(dictDownload);
            if (sSilence) elBody.appendChild(_felParagraph(sSilence, "acquisition-note"));
            if (dictDownload.sAttemptState) {
                elBody.appendChild(_felParagraph(
                    dictDownload.sAttemptState, "acquisition-note"));
            }
            if (dictDownload.bSlow) {
                elBody.appendChild(_felParagraph(
                    "The download has run below 100 kB/s for two minutes. " +
                    "It continues.", "acquisition-note"));
            }
        } else {
            elBody.appendChild(_felParagraph(
                "The download finished; the image is being checked, " +
                "loaded and prepared.", "acquisition-progress"));
        }
        var elSpinner = document.createElement("div");
        elSpinner.className = "build-spinner";
        elBody.appendChild(elSpinner);
        var saTail = dictView.saTailLines || [];
        if (saTail.length) {
            var elTail = document.createElement("pre");
            elTail.className = "acquisition-tail";
            elTail.textContent = saTail.join("\n");
            elBody.appendChild(elTail);
            elTail.scrollTop = elTail.scrollHeight;
        }
        var listButtons = [{sLabel: "Cancel", sAction: "cancel", fnOnClick: function () {
            _fnWarnThenCancel(dictView);
        }}];
        if (bDownloading && dictDownload.bSlow) {
            listButtons.push(_fdictCheckConnectionButton(dictView));
        }
        listButtons.push({sLabel: "Hide", sAction: "hide", fnOnClick: fnClose});
        _fnRender(
            bDownloading ? _S_TITLE_DOWNLOADING : _S_TITLE_PREPARING, elBody,
            listButtons
        );
    }

    /* ---- Check connection: the hub's probes, reported as observed ---- */

    function _fsAcquisitionIdOf(dictView) {
        var dictDownload = dictView.dictDownload || {};
        var dictFailure = dictView.dictFailure || {};
        return dictDownload.sAcquisitionId || dictFailure.sAcquisitionId || "";
    }

    function _fdictCheckConnectionButton(dictView) {
        return {sLabel: "Check connection", sAction: "check-connection",
            fnOnClick: function () { _fnCheckConnection(dictView); }};
    }

    function _felCheckReport(dictView) {
        /* One paragraph the report lands in, created on the first check
           and reused after, so a second click replaces the sentences
           rather than stacking them. Survives the next poll's re-render
           only while the hub's state does, which is what a reader
           watching a live download expects. */
        var elModal = _felModal();
        var elReport = elModal.querySelector(".acquisition-check");
        if (!elReport) {
            elReport = document.createElement("p");
            elReport.className = "acquisition-check";
            elModal.querySelector(".acquisition-body").appendChild(elReport);
        }
        return elReport;
    }

    async function _fnCheckConnection(dictView) {
        var sAcquisitionId = _fsAcquisitionIdOf(dictView);
        var elReport = _felCheckReport(dictView);
        if (!sAcquisitionId) {
            elReport.textContent = "The download has not started yet, so " +
                "there is no connection to check.";
            return;
        }
        elReport.textContent = "Checking the connection to the archive " +
            "(this takes up to 45 s)…";
        var dictCheck;
        try {
            dictCheck = await VaibifyApi.fdictGet(
                "/api/connection-check/" + encodeURIComponent(sAcquisitionId));
        } catch (error) {
            elReport.textContent = "The check could not run: " +
                VaibifyDiagnosis.fsExplainError(error);
            return;
        }
        elReport.textContent = (dictCheck.listObservations || [])
            .concat(dictCheck.listExplanations || []).join(" ");
        elReport.dataset.connectionChecked = "true";
    }

    function _fnWarnThenCancel(dictView) {
        /* Cancel is a give-up: the warning names the megabytes it costs,
           and the researcher chooses between keeping and deleting. */
        var dictDownload = dictView.dictDownload || {};
        VaibifyModals.fnShowConfirmModal(
            "Cancel the download?",
            "Cancelling stops the download and deletes the " +
            _fsMegabytes(dictDownload.iBytes || 0) + " downloaded so far. " +
            "'" + dictView.sHostName + "' stays a host project.",
            function () { _fnPost(dictView.sHostName, "/cancel", "cancelled"); },
            {sConfirmLabel: "Cancel and delete", sCancelLabel: "Keep downloading"}
        );
    }

    /* ---- the decision views ---- */

    function _flistDecisionButtons(dictView, dictFailure) {
        var listButtons = [];
        if (!dictFailure || dictFailure.bRetryable !== false) {
            listButtons.push({sLabel: "Try again", sAction: "retry", bPrimary: true,
                fnOnClick: function () { _fnPost(dictView.sHostName, "/retry", "retried"); }});
        }
        if (dictFailure && dictFailure.bDiagnosable) {
            listButtons.push({sLabel: "Run a diagnosis", sAction: "diagnose",
                fnOnClick: function () {
                    VaibifyDiagnosis.fnShowDoctorReport(dictFailure.sSentence);
                }});
        }
        if (_fsAcquisitionIdOf(dictView)) {
            listButtons.push(_fdictCheckConnectionButton(dictView));
        }
        listButtons.push({sLabel: "Stay in host mode", sAction: "give-up",
            fnOnClick: function () { _fnWarnThenGiveUp(dictView); }});
        listButtons.push({sLabel: "Close", sAction: "close", fnOnClick: fnClose});
        return listButtons;
    }

    function _fnWarnThenGiveUp(dictView) {
        var dictDownload = dictView.dictDownload || {};
        var iBytes = dictDownload.iBytes || 0;
        VaibifyModals.fnShowConfirmModal(
            "Stay in host mode?",
            "'" + dictView.sHostName + "' stays a host project, exactly as " +
            "it is." + (iBytes
                ? " The " + _fsMegabytes(iBytes) + " downloaded so far are deleted."
                : ""),
            function () { _fnPost(dictView.sHostName, "/cancel", "cancelled"); },
            {sConfirmLabel: iBytes ? "Stay and delete" : "Stay in host mode",
             sCancelLabel: "Back"}
        );
    }

    function _fnRenderWaiting(dictView) {
        var dictFailure = dictView.dictFailure || {};
        var elBody = document.createElement("div");
        elBody.appendChild(_felParagraph(
            dictFailure.sSentence || "The download stopped.", "acquisition-sentence"));
        if (dictFailure.sHeld) {
            elBody.appendChild(_felParagraph(dictFailure.sHeld, "acquisition-held"));
        }
        if (dictFailure.sAlsoChecked) {
            elBody.appendChild(_felParagraph(dictFailure.sAlsoChecked, "acquisition-note"));
        }
        elBody.appendChild(_felDetails(dictFailure.sTechnicalDetail));
        _fnRender(_S_TITLE_WAITING, elBody, _flistDecisionButtons(dictView, dictFailure));
    }

    function _fnRenderInterrupted(dictView) {
        var dictDownload = dictView.dictDownload || {};
        var elBody = document.createElement("div");
        var sWhere = dictDownload.iTotalBytes
            ? " at " + _fsMegabytes(dictDownload.iBytes) + " of " +
              _fsMegabytes(dictDownload.iTotalBytes)
            : "";
        elBody.appendChild(_felParagraph(
            "Containerizing '" + dictView.sHostName + "' was interrupted" +
            sWhere + ": vaibify stopped before it finished. The bytes " +
            "downloaded are kept.", "acquisition-sentence"));
        _fnRender(_S_TITLE_INTERRUPTED, elBody, [
            {sLabel: "Resume", sAction: "retry", bPrimary: true,
             fnOnClick: function () { _fnPost(dictView.sHostName, "/retry", "retried"); }},
            {sLabel: "Discard", sAction: "give-up",
             fnOnClick: function () { _fnWarnThenGiveUp(dictView); }},
            {sLabel: "Close", sAction: "close", fnOnClick: fnClose},
        ]);
    }

    function _fnRenderReady(dictView) {
        var elBody = document.createElement("div");
        elBody.appendChild(_felParagraph(
            "The author’s environment is on this machine. Close the " +
            "project '" + dictView.sHostName + "' in every browser tab, " +
            "then finish containerizing it as '" + dictView.sNewName + "'.",
            "acquisition-sentence"));
        var dictFailure = dictView.dictFailure;
        if (dictFailure && dictFailure.sSentence) {
            elBody.appendChild(_felParagraph(dictFailure.sSentence, "acquisition-note"));
        }
        _fnRender(_S_TITLE_READY, elBody, [
            {sLabel: "Finish", sAction: "commit", bPrimary: true,
             fnOnClick: function () { _fnCommit(dictView); }},
            {sLabel: "Stay in host mode", sAction: "give-up",
             fnOnClick: function () { _fnWarnThenGiveUp(dictView); }},
            {sLabel: "Close", sAction: "close", fnOnClick: fnClose},
        ]);
    }

    /* ---- the decisions, posted to the hub ---- */

    async function _fnPost(sHostName, sSuffix, sOutcome) {
        try {
            await VaibifyApi.fdictPost(_fsConversionUrl(sHostName, sSuffix), {});
        } catch (error) {
            VaibifyDiagnosis.fnReportFailureFromError(error);
            return;
        }
        if (sOutcome === "cancelled") {
            fnClose();
            VaibifyApp.fnShowToast(
                "'" + sHostName + "' stays a host project.", "info");
            VaibifyContainerManager.fnLoadContainers();
            return;
        }
        fnWatch(sHostName);
    }

    async function _fnCommit(dictView) {
        var dictAnswer;
        try {
            dictAnswer = await VaibifyApi.fdictPost(
                _fsConversionUrl(dictView.sHostName, "/commit"), {});
        } catch (error) {
            /* A busy refusal is the hub saying the project is still
               open somewhere: the sentence names where. */
            var elModal = _felModal();
            if (elModal && error.dictDetail && error.dictDetail.sMessage) {
                var elNote = elModal.querySelector(".acquisition-body")
                    .appendChild(_felParagraph(
                        error.dictDetail.sMessage, "acquisition-note"));
                elNote.dataset.commitRefusal = "true";
                return;
            }
            VaibifyDiagnosis.fnReportFailureFromError(error);
            return;
        }
        if (dictAnswer && dictAnswer.bCommitted) {
            await _fnAnnounceConverted(dictView.sHostName, dictView.sNewName);
            return;
        }
        fnWatch(dictView.sHostName);
    }

    async function _fnAnnounceConverted(sHostName, sNewName) {
        fnClose();
        VaibifyApp.fnShowToast(
            "Converted '" + sHostName + "' to '" + sNewName + "'. " +
            "Starting the container.", "success");
        VaibifyContainerManager.fnLoadContainers();
        await VaibifyContainerManager.fnStartContainer(sNewName);
    }

    /* ---- the poll ---- */

    function _fnStopPolling() {
        if (_iPollTimer !== null) {
            clearInterval(_iPollTimer);
            _iPollTimer = null;
        }
    }

    async function _fnPollOnce() {
        var sHostName = _sWatchedHost;
        if (!sHostName) return;
        var dictView;
        try {
            dictView = await VaibifyApi.fdictGet(_fsConversionUrl(sHostName));
        } catch (error) {
            if (error && error.iStatus === 404) {
                /* The record is gone: the hub finished the switch. */
                _fnStopPolling();
                var sNewName = _sLastNewName || sHostName;
                _sWatchedHost = null;
                await _fnAnnounceConverted(sHostName, sNewName);
            }
            return;
        }
        if (_sWatchedHost !== sHostName) return;
        _sLastNewName = dictView.sNewName;
        _fnRenderState(dictView);
    }

    function _fnRenderState(dictView) {
        _bLastLive = dictView.bLive === true;
        if (dictView.bLive) {
            _fnRenderProgress(dictView);
            return;
        }
        _fnStopPolling();
        if (dictView.sState === "waiting") {
            _fnRenderWaiting(dictView);
        } else if (dictView.sState === "ready") {
            _fnRenderReady(dictView);
        } else {
            _fnRenderInterrupted(dictView);
        }
    }

    function fnWatch(sHostName) {
        _sWatchedHost = sHostName;
        _fnStopPolling();
        _fnPollOnce();
        _iPollTimer = setInterval(_fnPollOnce, _I_POLL_MILLISECONDS);
    }

    function fnClose() {
        _fnStopPolling();
        _sWatchedHost = null;
        var elModal = _felModal();
        if (elModal) elModal.style.display = "none";
    }

    function fbIsWatching() {
        return _sWatchedHost !== null && _bLastLive;
    }

    return {
        fnWatch: fnWatch,
        fnClose: fnClose,
        fbIsWatching: fbIsWatching,
    };
})();
