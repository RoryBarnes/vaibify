/* Vaibify — failure toasts that carry a diagnosis.

   Every failure the environment hub can show ends the same way: the
   researcher reads what happened and has nowhere to go next. This
   module gives each of them the same next step. A failure toast ends
   in "Click to run a diagnosis", and the click runs the host-scope
   checks of `vaibify doctor` through /api/system/doctor and renders
   the report -- the same findings, remedies and commands the terminal
   prints, so the browser never says something the terminal would not.

   Public surface:
   - VaibifyDiagnosis.fnReportFailure(sMessage)
       An error toast carrying the diagnosis click.
   - VaibifyDiagnosis.fnReportFailureFromError(error)
       The same, from a thrown API error (its message sanitized).
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

    function fnReportFailureFromError(error) {
        fnReportFailure(VaibifyUtilities.fsSanitizeErrorForUser(
            error && error.message));
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
        fnReportFailure: fnReportFailure,
        fnReportFailureFromError: fnReportFailureFromError,
        fnShowDoctorReport: fnShowDoctorReport,
    };
})();
