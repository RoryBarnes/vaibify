/* Vaibify — failure toasts that carry a diagnosis.

   Every failure the environment hub can show ends the same way: the
   researcher reads what happened and has nowhere to go next. This
   module gives each of them the same next step. A failure toast ends
   in "Click to run a diagnosis", and the click runs the host-scope
   checks of `vaibify doctor` through /api/system/doctor and renders
   the report -- the same findings, remedies and commands the terminal
   prints, so the browser never says something the terminal would not.

   Public surface:
   - VaibifyDiagnosis.fsExplainError(error)
       The sentence to show for a thrown API error: the server's own
       structured sentence when it wrote one, else the raw message
       through the sanitizer.
   - VaibifyDiagnosis.fnReportFailure(sMessage)
       An error toast carrying the diagnosis click.
   - VaibifyDiagnosis.fnReportFailureFromError(error)
       The same, from a thrown API error.
   - VaibifyDiagnosis.fnRenderFailureInline(elTarget, sPrefix, error)
       The same next step inside a card or panel rather than a toast.
   - VaibifyDiagnosis.fnShowDoctorReport()
       Runs the checks and opens the report modal.
*/

var VaibifyDiagnosis = (function () {
    "use strict";

    var _S_CLICK_SUFFIX = " Click to run a diagnosis.";
    var _LIST_LEVEL_ORDER = ["fail", "warn", "not-checked", "info", "ok"];
    var _DICT_LEVEL_HEADING = {
        "fail": "Failing",
        "warn": "Worth acting on",
        "not-checked": "Could not be assessed",
        "info": "Facts",
        "ok": "Passing",
    };

    function fnReportFailure(sMessage) {
        VaibifyApp.fnShowToast(
            (sMessage || "An error occurred.") + _S_CLICK_SUFFIX,
            "error", fnShowDoctorReport);
    }

    function fsExplainError(error) {
        /* A sentence the SERVER wrote (a refusal's dictDetail.sMessage,
           written for the researcher) passes untouched. The sanitizer
           exists for raw daemon and transport text, and it matches on
           phrases -- so a server sentence quoting a git clone's
           "connection refused" used to be replaced by "Cannot connect
           to Docker", a remedy for a different failure. */
        var dictDetail = (error && error.dictDetail) || {};
        if (dictDetail.sMessage) return dictDetail.sMessage;
        return VaibifyUtilities.fsSanitizeErrorForUser(
            error && error.message);
    }

    function fnReportFailureFromError(error) {
        fnReportFailure(fsExplainError(error));
    }

    function fnRenderFailureInline(elTarget, sPrefix, error) {
        /* A failure shown inside a card keeps the same next step as a
           toast. The text is set as text, never markup: the sentence
           may quote whatever the server was told. */
        if (!elTarget) return;
        elTarget.textContent = "";
        elTarget.appendChild(document.createTextNode(
            (sPrefix || "") + fsExplainError(error) + " "));
        var elButton = document.createElement("button");
        elButton.type = "button";
        elButton.className = "diagnosis-link";
        elButton.textContent = "Run a diagnosis";
        elButton.addEventListener("click", fnShowDoctorReport);
        elTarget.appendChild(elButton);
    }

    function _fsRenderFinding(dictFinding) {
        var fnEscape = VaibifyUtilities.fnEscapeHtml;
        var sHtml =
            '<div class="diagnosis-finding diagnosis-finding--' +
            fnEscape(dictFinding.sLevel) + '">' +
            '<span class="diagnosis-level">[' + fnEscape(dictFinding.sLevel) +
            ']</span> <strong>' + fnEscape(dictFinding.sName) + '</strong>: ' +
            fnEscape(dictFinding.sMessage);
        if (dictFinding.sRemediation) {
            sHtml += '<div class="diagnosis-remedy">' +
                fnEscape(dictFinding.sRemediation) + '</div>';
        }
        if (dictFinding.sCommand) {
            sHtml += '<pre class="diagnosis-command">$ ' +
                fnEscape(dictFinding.sCommand) + '</pre>';
        }
        return sHtml + '</div>';
    }

    function _fsRenderReport(listFindings) {
        var sHtml = "";
        _LIST_LEVEL_ORDER.forEach(function (sLevel) {
            var listOfLevel = listFindings.filter(function (dictFinding) {
                return dictFinding.sLevel === sLevel;
            });
            if (!listOfLevel.length) return;
            sHtml += '<h3 class="diagnosis-heading">' +
                _DICT_LEVEL_HEADING[sLevel] + '</h3>' +
                listOfLevel.map(_fsRenderFinding).join("");
        });
        if (!sHtml) {
            sHtml = "<p>No checks ran. The hub answered, but reported " +
                "nothing.</p>";
        }
        return sHtml + '<p class="diagnosis-footer">This is the report ' +
            '<code>vaibify doctor</code> prints on this machine; run it ' +
            'in a terminal with <code>--explain CHECK</code> to see how ' +
            'one check decides.</p>';
    }

    async function fnShowDoctorReport() {
        VaibifyModals.fnShowInfoModal(
            "Diagnosis",
            "<p>Running the checks of <code>vaibify doctor</code> on " +
            "this machine\u2026</p>");
        var sBodyHtml;
        try {
            var dictReport = await VaibifyApi.fdictGet("/api/system/doctor");
            sBodyHtml = _fsRenderReport(dictReport.listFindings || []);
        } catch (error) {
            sBodyHtml = "<p>The diagnosis could not run: " +
                VaibifyUtilities.fnEscapeHtml(
                    VaibifyUtilities.fsSanitizeErrorForUser(
                        error && error.message)) +
                "</p><p>Run <code>vaibify doctor</code> in a terminal " +
                "on this machine instead.</p>";
        }
        var elBody = document.querySelector("#modalInfo .modal-info-body");
        if (elBody) elBody.innerHTML = sBodyHtml;
    }

    return {
        fsExplainError: fsExplainError,
        fnReportFailure: fnReportFailure,
        fnReportFailureFromError: fnReportFailureFromError,
        fnRenderFailureInline: fnRenderFailureInline,
        fnShowDoctorReport: fnShowDoctorReport,
    };
})();
