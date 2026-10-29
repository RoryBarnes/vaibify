/* Vaibify — Quit vaibify (D6-D8)
 *
 * One Quit for every entry point: the "Quit vaibify" button on the
 * Environments hub and on the Project hub, and Admin -> Quit on the
 * dashboard. The server decides whether a Quit may proceed
 * (GET /api/session/quit-preflight, POST /api/session/quit); this
 * module only shows its answer, asks the researcher, and then reports
 * what really happened. It never claims vaibify has quit while the
 * server still answers.
 */

var VaibifyHubQuit = (function () {
    "use strict";

    var I_SHUTDOWN_POLL_MILLISECONDS = 500;
    var I_STILL_ANSWERING_MILLISECONDS = 15000;
    var S_QUIT_URL = "/api/session/quit";

    async function fnQuitVaibify() {
        var dictPreflight;
        try {
            dictPreflight = await VaibifyApi.fdictGet(
                "/api/session/quit-preflight");
        } catch (error) {
            VaibifyDiagnosis.fnReportFailureFromError(error);
            return;
        }
        _fnShowQuitModal(dictPreflight);
    }

    function _fsBuildList(sHeading, listLines) {
        if (!listLines.length) return "";
        return '<p class="quit-modal-heading">' +
            VaibifyUtilities.fnEscapeHtml(sHeading) + '</p><ul>' +
            listLines.map(function (sLine) {
                return '<li>' + VaibifyUtilities.fnEscapeHtml(sLine) +
                    '</li>';
            }).join("") + '</ul>';
    }

    function _fsBuildQuitBody(dictPreflight) {
        var listEnvironments = dictPreflight.listRunningEnvironments || [];
        var listWindows = (dictPreflight.listChildWindows || []).map(
            function (dictWindow) {
                return "The vaibify window on port " + dictWindow.iPort;
            });
        var listReasons = (dictPreflight.listReasons || []).map(
            function (dictReason) {
                return dictReason.sWindow + ": " + dictReason.sReason;
            });
        var sBody = "";
        if (listReasons.length) {
            sBody += _fsBuildList(
                "vaibify cannot quit while this work is in progress:",
                listReasons);
        }
        sBody += _fsBuildList(
            "These environments keep running after vaibify quits:",
            listEnvironments);
        if (dictPreflight.bEnvironmentsUnknown) {
            sBody += '<p>Docker did not answer, so vaibify cannot say ' +
                'which open environments are running.</p>';
        }
        sBody += _fsBuildList("These windows will close:", listWindows);
        if (dictPreflight.sTerminalWarning) {
            sBody += '<p class="quit-modal-warning">' +
                VaibifyUtilities.fnEscapeHtml(
                    dictPreflight.sTerminalWarning) + '</p>';
        }
        return sBody;
    }

    function _fsBuildStopCheckbox(dictPreflight) {
        var iRunning = (dictPreflight.listRunningEnvironments || []).length;
        if (!iRunning) return "";
        var sNoun = iRunning === 1 ? "environment" : "environments";
        return '<label class="confirm-checkbox-row">' +
            '<input type="checkbox" id="checkboxQuitStopEnvironments"> ' +
            "Also stop " + iRunning + " running " + sNoun + '</label>';
    }

    function _fnShowQuitModal(dictPreflight) {
        var elExisting = document.getElementById("modalQuit");
        if (elExisting) elExisting.remove();
        var bCanQuit = Boolean(dictPreflight.bCanQuit);
        var elModal = document.createElement("div");
        elModal.id = "modalQuit";
        elModal.className = "modal-overlay";
        elModal.style.display = "flex";
        elModal.innerHTML =
            '<div class="modal"><h2>Quit vaibify</h2>' +
            _fsBuildQuitBody(dictPreflight) +
            (bCanQuit ? _fsBuildStopCheckbox(dictPreflight) : "") +
            '<div class="modal-actions">' +
            '<button class="btn" id="btnQuitCancel">Cancel</button>' +
            '<button class="btn btn-primary" id="btnQuitConfirm"' +
            (bCanQuit ? "" : " disabled title=\"Wait for the work " +
                "listed above to finish, then try again.\"") +
            '>Quit vaibify</button></div></div>';
        document.body.appendChild(elModal);
        elModal.querySelector("#btnQuitCancel").addEventListener(
            "click", function () { elModal.remove(); });
        elModal.querySelector("#btnQuitConfirm").addEventListener(
            "click", function () {
                var elCheckbox = elModal.querySelector(
                    "#checkboxQuitStopEnvironments");
                var bStop = Boolean(elCheckbox && elCheckbox.checked);
                elModal.remove();
                _fnSendQuit(bStop);
            });
    }

    function _fsDescribeRefusal(error) {
        var dictDetail = (error && error.dictDetail) || {};
        var sMessage = dictDetail.sMessage ||
            "vaibify did not quit.";
        var listStopped = dictDetail.listStoppedEnvironments || [];
        if (listStopped.length) {
            sMessage += " Already stopped, and still stopped: " +
                listStopped.join(", ") + ".";
        }
        if (dictDetail.sLeaseNote) sMessage += " " + dictDetail.sLeaseNote;
        return sMessage + " Try Quit again once it has finished.";
    }

    async function _fnSendQuit(bStopEnvironments) {
        VaibifyConnectionMonitor.fnExpectShutdown();
        try {
            await VaibifyApi.fdictPost(
                S_QUIT_URL, {bStopEnvironments: bStopEnvironments});
        } catch (error) {
            VaibifyConnectionMonitor.fnCancelExpectedShutdown();
            if (error && error.iStatus === 409) {
                VaibifyApp.fnShowToast(_fsDescribeRefusal(error), "warning");
                return;
            }
            VaibifyDiagnosis.fnReportFailureFromError(error);
            return;
        }
        VaibifyConnectionMonitor.fnStopForShutdown();
        _fnFollowTheShutdown();
    }

    function _felementShowShutdownStatus(sText) {
        var elStatus = document.getElementById("quitShutdownStatus");
        if (!elStatus) {
            elStatus = document.createElement("div");
            elStatus.id = "quitShutdownStatus";
            elStatus.className = "quit-shutdown-status";
            elStatus.setAttribute("role", "status");
            document.body.appendChild(elStatus);
        }
        elStatus.textContent = sText;
        return elStatus;
    }

    function _fnFollowTheShutdown() {
        var elStatus = _felementShowShutdownStatus("Shutting down…");
        var iStarted = Date.now();
        async function fnPollOnce() {
            try {
                await VaibifyApi.fdictGet("/api/session/lifetime");
            } catch (error) {
                if (error && error.sKind === "network") {
                    elStatus.textContent =
                        "vaibify has quit. You can close this tab.";
                    return;
                }
            }
            if (Date.now() - iStarted >= I_STILL_ANSWERING_MILLISECONDS) {
                elStatus.textContent =
                    "vaibify is still answering after 15 seconds. Run " +
                    "`vaibify sessions stop` in a terminal to stop it.";
            }
            window.setTimeout(fnPollOnce, I_SHUTDOWN_POLL_MILLISECONDS);
        }
        window.setTimeout(fnPollOnce, I_SHUTDOWN_POLL_MILLISECONDS);
    }

    return {
        fnQuitVaibify: fnQuitVaibify,
    };
})();
