/* The Agent Council's credential consent modal, test progress, and the
   Credential tests panel (plan contracts A2 and A5).

   A council copies the researcher's provider login into a disposable
   runner, so the first council with a project's image on a computer
   asks for consent and then lets vaibify run a short credential test.
   Everything this module shows is the SERVER's verdict: the checklist
   is the providers the panel route says have a login, progress is the
   job record, and the panel's states are the store's. Nothing here
   decides whether a provider is authorized; start re-checks all of it.

   The consent click is the researcher's act. What the backend can
   actually guarantee about it is narrower — a request from an
   authenticated browser session holding the container's lease — and
   nothing in this module claims more. */

var VaibifyCouncilConsent = (function () {
    "use strict";

    var I_POLL_INTERVAL_MILLISECONDS = 2000;

    /* Shown here AND in the convene disclosure. One constant, so the
       two can never say different things about the same risk. */
    var S_RESIDUAL_RISK_PARAGRAPH =
        "While a council runs, each participant holds a copy of your " +
        "access token. A participant manipulated by something it reads " +
        "could read that copy. The token expires and cannot renew " +
        "itself, but until it expires it works. This test shows the " +
        "sharing works as designed; it does not make that risk zero.";

    var LIST_CHECK_EXPLANATIONS = [
        "vaibify confirms this project has a login it can copy.",
        "It starts one disposable runner from this project's image, " +
            "gives it a copy of the access token only (never the " +
            "refresh token), and asks for a one-word reply. This is a " +
            "real, paid request.",
        "It confirms the project's own login is still present and " +
            "unchanged. It does not spend a request to prove the login " +
            "still works.",
        "It confirms the token was not rotated by the test.",
        "It confirms the copied token is gone from this computer and " +
            "that the runner was destroyed.",
        "It runs a turn with a model that does not exist, which may " +
            "or may not reach the provider, and confirms vaibify " +
            "reports it as a failure. The same clean-up checks run " +
            "again.",
        "It starts a longer turn and kills its runner part-way " +
            "through, which is one more paid request, and confirms the " +
            "clean-up holds when a runner dies mid-turn.",
    ];

    var _dictState = {
        sContainerId: "",
        dictPanel: null,
        listJobIds: [],
        dictJobs: {},
        iPollTimer: 0,
        fnOnFinished: null,
    };

    /* ------------------------------------------------------------------ */
    /* Modal plumbing                                                      */
    /* ------------------------------------------------------------------ */

    function _fnShowModal() {
        var elModal = document.getElementById("councilConsentModal");
        if (elModal) elModal.style.display = "flex";
    }

    function fnHideModal() {
        var elModal = document.getElementById("councilConsentModal");
        if (elModal) elModal.style.display = "none";
        _fnStopPolling();
    }

    function _fnSetBody(sMarkup) {
        var elBody = document.getElementById("councilConsentModalBody");
        if (elBody) elBody.innerHTML = sMarkup;
    }

    function _fsRoute(sSuffix) {
        return "/api/council-credentials/" +
            encodeURIComponent(_dictState.sContainerId) + sSuffix;
    }

    function _fsEscape(sText) {
        var elDiv = document.createElement("div");
        elDiv.textContent = sText === undefined || sText === null
            ? "" : String(sText);
        return elDiv.innerHTML;
    }

    function _fnBind(sId, fnHandler) {
        var elElement = document.getElementById(sId);
        if (elElement) elElement.addEventListener("click", fnHandler);
    }

    async function _fdictLoadPanel() {
        _dictState.dictPanel = await VaibifyApi.fdictGet(_fsRoute("/panel"));
        return _dictState.dictPanel;
    }

    /* ------------------------------------------------------------------ */
    /* Consent modal                                                       */
    /* ------------------------------------------------------------------ */

    async function fnOpenConsent(sContainerId, fnOnFinished) {
        _dictState.sContainerId = sContainerId;
        _dictState.fnOnFinished = fnOnFinished || null;
        _fnSetBody("<p>Reading this project's logins…</p>");
        _fnShowModal();
        try {
            await _fdictLoadPanel();
        } catch (error) {
            fnHideModal();
            VaibifyDiagnosis.fnReportFailureFromError(error);
            return;
        }
        _fnRenderConsent();
    }

    function _flistProvidersWithLogin() {
        return (_dictState.dictPanel.listProviders || []).filter(
            function (dictProvider) { return !dictProvider.sLoginProblem; });
    }

    function _fsProviderChoiceMarkup(dictProvider, iIndex) {
        var sId = "councilConsentProvider" + iIndex;
        var sModel = dictProvider.sDefaultTestModel
            ? "" :
            "<input type=\"text\" class=\"council-consent-model\" " +
            "id=\"councilConsentModel" + iIndex + "\" " +
            "placeholder=\"model id to test with\" " +
            "aria-label=\"Model id for " + _fsEscape(dictProvider.sProvider) +
            "\">";
        return "<li><label><input type=\"checkbox\" id=\"" + sId + "\" " +
            "data-provider=\"" + _fsEscape(dictProvider.sProvider) + "\"" +
            (iIndex === 0 ? " checked" : "") + "> " +
            _fsEscape(dictProvider.sProvider) + "</label>" + sModel +
            (dictProvider.bAuthorized
                ? " <span class=\"council-consent-note\">already " +
                  "verified; running it again suspends that until the " +
                  "new test passes</span>" : "") +
            "</li>";
    }

    function _fsSharingMarkup() {
        var listProjects = _dictState.dictPanel.listProjectsSharingImage
            || [];
        return "<p>This applies to every project on this computer that " +
            "uses this image" + (listProjects.length
                ? " — running now: " + listProjects.map(_fsEscape)
                    .join(", ")
                : "") + ".</p>";
    }

    function _fsDamageMarkup() {
        var bDamaged = (_dictState.dictPanel.listProviders || []).some(
            function (dictProvider) {
                return dictProvider.sState === "damaged";
            });
        if (!bDamaged) return "";
        return "<p class=\"council-consent-warning\">The credential " +
            "record on this computer cannot be read. It will be set " +
            "aside (renamed, not deleted) when you continue, and every " +
            "image on this computer will need its test again.</p>";
    }

    function _fsRisksMarkup() {
        return "<details class=\"council-consent-details\">" +
            "<summary>What happens, and the risks</summary><ol>" +
            LIST_CHECK_EXPLANATIONS.map(function (sLine) {
                return "<li>" + _fsEscape(sLine) + "</li>";
            }).join("") + "</ol>" +
            "<p>Each provider you tick costs about two paid requests, " +
            "billed to the login already in this project. Nothing is " +
            "retried automatically.</p>" +
            "<p class=\"council-residual-risk\">" +
            _fsEscape(S_RESIDUAL_RISK_PARAGRAPH) + "</p></details>";
    }

    function _fnRenderConsent() {
        var listProviders = _flistProvidersWithLogin();
        if (!listProviders.length) {
            _fnSetBody("<h2>First council with this project's image on " +
                "this computer</h2><p>No provider is logged in inside " +
                "this project's container, so there is nothing to " +
                "test. Log in from the project's terminal first " +
                "(claude, codex login, or agy).</p>" +
                "<div class=\"modal-actions\"><button type=\"button\" " +
                "id=\"btnCouncilConsentCancel\">Close</button></div>");
            _fnBind("btnCouncilConsentCancel", fnHideModal);
            return;
        }
        _fnSetBody(
            "<h2>First council with this project's image on this " +
            "computer</h2>" +
            "<p>A council gives each participant a copy of this " +
            "project's provider login. Before the first council, " +
            "vaibify runs a short test to confirm that sharing works as " +
            "designed. Choose the providers to allow:</p>" +
            "<ul class=\"council-consent-providers\">" +
            listProviders.map(_fsProviderChoiceMarkup).join("") + "</ul>" +
            _fsSharingMarkup() + _fsDamageMarkup() + _fsRisksMarkup() +
            "<div class=\"modal-actions\">" +
            "<button type=\"button\" id=\"btnCouncilConsentCancel\">" +
            "Cancel</button>" +
            "<button type=\"button\" id=\"btnCouncilConsentRun\" " +
            "class=\"primary\">Run the test and continue</button></div>");
        _fnBind("btnCouncilConsentCancel", fnHideModal);
        _fnBind("btnCouncilConsentRun", _fnSubmitConsent);
    }

    function _flistChosenProviders() {
        var listChosen = [];
        _flistProvidersWithLogin().forEach(function (dictProvider, iIndex) {
            var elBox = document.getElementById(
                "councilConsentProvider" + iIndex);
            if (!elBox || !elBox.checked) return;
            var elModel = document.getElementById(
                "councilConsentModel" + iIndex);
            listChosen.push({
                sProvider: dictProvider.sProvider,
                sRequestedModel: elModel ? elModel.value.trim() : "",
            });
        });
        return listChosen;
    }

    async function _fnSubmitConsent() {
        var listChosen = _flistChosenProviders();
        if (!listChosen.length) {
            VaibifyApp.fnShowToast(
                "Tick at least one provider to test.", "warning");
            return;
        }
        var dictMissingModel = listChosen.find(function (dictChoice) {
            return !dictChoice.sRequestedModel && !_fsDefaultModel(
                dictChoice.sProvider);
        });
        if (dictMissingModel) {
            VaibifyApp.fnShowToast(
                "Name the model id to test " + dictMissingModel.sProvider +
                " with; vaibify cannot list its models.", "warning");
            return;
        }
        await _fnStartTests(listChosen);
    }

    function _fsDefaultModel(sProvider) {
        var dictProvider = (_dictState.dictPanel.listProviders || []).find(
            function (dictEntry) { return dictEntry.sProvider === sProvider; });
        return dictProvider ? dictProvider.sDefaultTestModel : "";
    }

    async function _fnStartTests(listChosen) {
        var elRun = document.getElementById("btnCouncilConsentRun");
        if (elRun) elRun.disabled = true;
        try {
            var dictAnswer = await VaibifyApi.fdictPost(
                _fsRoute("/credential-test"), {listProviders: listChosen});
            _fnBeginProgress(dictAnswer.listJobs.map(function (dictJob) {
                return dictJob.sJobId;
            }));
        } catch (error) {
            if (elRun) elRun.disabled = false;
            VaibifyDiagnosis.fnReportFailureFromError(error);
        }
    }

    /* ------------------------------------------------------------------ */
    /* Progress                                                            */
    /* ------------------------------------------------------------------ */

    function _fnBeginProgress(listJobIds) {
        _dictState.listJobIds = listJobIds.slice();
        _dictState.dictJobs = {};
        _fnSetBody("<h2>Testing this project's login</h2>" +
            "<p>Starting the credential test…</p>");
        _fnShowModal();
        _fnPollJobs();
    }

    function _fnStopPolling() {
        if (_dictState.iPollTimer) {
            window.clearTimeout(_dictState.iPollTimer);
            _dictState.iPollTimer = 0;
        }
    }

    async function _fnPollJobs() {
        _fnStopPolling();
        try {
            for (var iIndex = 0; iIndex < _dictState.listJobIds.length;
                    iIndex++) {
                var sJobId = _dictState.listJobIds[iIndex];
                _dictState.dictJobs[sJobId] = await VaibifyApi.fdictGet(
                    _fsRoute("/credential-test/" +
                             encodeURIComponent(sJobId)));
            }
        } catch (error) {
            _fnSetBody("<h2>Testing this project's login</h2>" +
                "<p class=\"council-consent-warning\">The test's " +
                "progress could not be read: " +
                _fsEscape(error.message || String(error)) + ". The test " +
                "itself keeps running; its result will appear in the " +
                "Credential tests panel.</p>" + _fsCloseButton());
            _fnBind("btnCouncilConsentDone", fnHideModal);
            return;
        }
        _fnRenderProgress();
        if (_fbAnyJobRunning()) {
            _dictState.iPollTimer = window.setTimeout(
                _fnPollJobs, I_POLL_INTERVAL_MILLISECONDS);
        }
    }

    function _fbAnyJobRunning() {
        return _dictState.listJobIds.some(function (sJobId) {
            var dictJob = _dictState.dictJobs[sJobId];
            return !dictJob || dictJob.sStatus === "running";
        });
    }

    function _fbAnyJobPassed() {
        return _dictState.listJobIds.some(function (sJobId) {
            var dictJob = _dictState.dictJobs[sJobId];
            return dictJob && dictJob.sStatus === "passed";
        });
    }

    var DICT_CHECK_GLYPHS = {
        pending: "·", running: "…", passed: "✓", failed: "✗",
        incomplete: "–",
    };

    function _fsJobMarkup(dictJob) {
        var sChecks = (dictJob.listChecks || []).map(function (dictCheck) {
            return "<li class=\"council-check council-check-" +
                _fsEscape(dictCheck.sStatus) + "\" data-check=\"" +
                _fsEscape(dictCheck.sCheckId) + "\">" +
                (DICT_CHECK_GLYPHS[dictCheck.sStatus] || "?") + " " +
                _fsEscape(dictCheck.sLabel) +
                (dictCheck.sDetail
                    ? " — " + _fsEscape(dictCheck.sDetail) : "") + "</li>";
        }).join("");
        var sCancel = dictJob.sStatus === "running"
            ? "<button type=\"button\" class=\"council-consent-cancel-job\" " +
              "data-job=\"" + _fsEscape(dictJob.sJobId) + "\">Cancel " +
              "test</button>" : "";
        return "<section class=\"council-consent-job\" data-status=\"" +
            _fsEscape(dictJob.sStatus) + "\"><h3>" +
            _fsEscape(dictJob.sProvider) + ": " +
            _fsEscape(_fsDescribeStatus(dictJob)) + "</h3><ul>" + sChecks +
            "</ul>" + sCancel + "</section>";
    }

    function _fsDescribeStatus(dictJob) {
        if (dictJob.sStatus === "running") return "running";
        if (dictJob.sStatus === "passed") return "passed";
        if (dictJob.sStatus === "failed") {
            return "failed at '" + dictJob.sFailedCheck + "'";
        }
        return "did not finish — re-run it from the Credential tests " +
            "panel";
    }

    function _fsCloseButton() {
        return "<div class=\"modal-actions\"><button type=\"button\" " +
            "id=\"btnCouncilConsentDone\">Close</button></div>";
    }

    function _fnRenderProgress() {
        var sJobs = _dictState.listJobIds.map(function (sJobId) {
            var dictJob = _dictState.dictJobs[sJobId];
            return dictJob ? _fsJobMarkup(dictJob) : "";
        }).join("");
        var sActions;
        if (_fbAnyJobRunning()) {
            sActions = "<p>Each paid turn has a hard timeout; nothing " +
                "is retried.</p>";
        } else if (_fbAnyJobPassed()) {
            sActions = "<div class=\"modal-actions\"><button " +
                "type=\"button\" id=\"btnCouncilConsentContinue\" " +
                "class=\"primary\">Continue to the council</button></div>";
        } else {
            sActions = "<p class=\"council-consent-warning\">No provider " +
                "passed, so a council cannot start yet.</p>" +
                _fsCloseButton();
        }
        _fnSetBody("<h2>Testing this project's login</h2>" + sJobs +
            sActions);
        document.querySelectorAll(".council-consent-cancel-job").forEach(
            function (elButton) {
                elButton.addEventListener("click", function () {
                    _fnCancelJob(elButton.getAttribute("data-job"));
                });
            });
        _fnBind("btnCouncilConsentDone", fnHideModal);
        _fnBind("btnCouncilConsentContinue", _fnContinueAfterTest);
    }

    async function _fnCancelJob(sJobId) {
        try {
            await VaibifyApi.fdictPost(_fsRoute("/credential-test/" +
                encodeURIComponent(sJobId) + "/cancel"), {});
        } catch (error) {
            VaibifyDiagnosis.fnReportFailureFromError(error);
        }
        _fnPollJobs();
    }

    function _fnContinueAfterTest() {
        var fnOnFinished = _dictState.fnOnFinished;
        fnHideModal();
        if (fnOnFinished) fnOnFinished();
    }

    /* ------------------------------------------------------------------ */
    /* Credential tests panel                                              */
    /* ------------------------------------------------------------------ */

    async function fnOpenPanel(sContainerId) {
        _dictState.sContainerId = sContainerId;
        _dictState.fnOnFinished = null;
        _fnSetBody("<p>Reading credential tests…</p>");
        _fnShowModal();
        try {
            await _fdictLoadPanel();
        } catch (error) {
            fnHideModal();
            VaibifyDiagnosis.fnReportFailureFromError(error);
            return;
        }
        _fnRenderPanel();
    }

    function _fsOutcomeMarkup(dictOutcome) {
        if (!dictOutcome || !dictOutcome.sOutcome) return "never tested";
        var sWhen = dictOutcome.sFinishedIso
            ? " on " + _fsEscape(String(dictOutcome.sFinishedIso)
                .slice(0, 10)) : "";
        var sMethod = dictOutcome.sVerificationMethod === "manual"
            ? " (recorded by hand)" : "";
        if (dictOutcome.sOutcome === "passed") {
            return "passed" + sWhen + sMethod;
        }
        if (dictOutcome.sOutcome === "failed") {
            return "failed at '" + _fsEscape(dictOutcome.sFailedCheck) +
                "'" + sWhen;
        }
        return "did not finish" + sWhen + "; re-run it";
    }

    function _fsPanelRowMarkup(dictProvider) {
        var sConsent = dictProvider.sConsentState === "active"
            ? (dictProvider.bConsentImplied
                ? "active (from a record made by hand)" : "active")
            : dictProvider.sConsentState === "withdrawn"
                ? "withdrawn" : "not given";
        var sRunning = dictProvider.sRunningJobId
            ? " A test is running now." : "";
        var bCanRun = !dictProvider.sLoginProblem
            && !dictProvider.sRunningJobId;
        return "<tr data-provider=\"" + _fsEscape(dictProvider.sProvider) +
            "\" data-state=\"" + _fsEscape(dictProvider.sState) + "\">" +
            "<td>" + _fsEscape(dictProvider.sProvider) + "</td>" +
            "<td>" + _fsEscape(sConsent) + "</td>" +
            "<td>" + _fsOutcomeMarkup(dictProvider.dictLatestOutcome) +
            _fsEscape(sRunning) + "</td>" +
            "<td>" + (dictProvider.sLoginProblem
                ? _fsEscape(dictProvider.sLoginProblem) : "") +
            (bCanRun ? "<button type=\"button\" class=\"council-panel-rerun\"" +
                " data-provider=\"" + _fsEscape(dictProvider.sProvider) +
                "\">Re-run test</button>" : "") +
            (dictProvider.sConsentState === "active"
                ? "<button type=\"button\" class=\"council-panel-withdraw\"" +
                  " data-provider=\"" + _fsEscape(dictProvider.sProvider) +
                  "\">Withdraw</button>" : "") + "</td></tr>";
    }

    function _fnRenderPanel() {
        var dictPanel = _dictState.dictPanel;
        _fnSetBody("<h2>Credential tests</h2>" +
            "<p>For this project's image on this computer.</p>" +
            "<table class=\"council-credential-panel\"><thead><tr>" +
            "<th>Provider</th><th>Consent</th><th>Latest test</th>" +
            "<th></th></tr></thead><tbody>" +
            (dictPanel.listProviders || []).map(_fsPanelRowMarkup).join("") +
            "</tbody></table>" + _fsSharingMarkup() +
            "<p class=\"council-consent-note\">Re-running a test suspends " +
            "the current pass until the new test passes. A test that " +
            "does not finish leaves the provider off until one does.</p>" +
            "<p class=\"council-residual-risk\">" +
            _fsEscape(S_RESIDUAL_RISK_PARAGRAPH) + "</p>" + _fsCloseButton());
        _fnBind("btnCouncilConsentDone", fnHideModal);
        _fnBindPanelButtons();
    }

    function _fnBindPanelButtons() {
        document.querySelectorAll(".council-panel-rerun").forEach(
            function (elButton) {
                elButton.addEventListener("click", function () {
                    _fnConfirmRerun(elButton.getAttribute("data-provider"));
                });
            });
        document.querySelectorAll(".council-panel-withdraw").forEach(
            function (elButton) {
                elButton.addEventListener("click", function () {
                    _fnConfirmWithdraw(
                        elButton.getAttribute("data-provider"));
                });
            });
    }

    function _fnConfirmRerun(sProvider) {
        var dictProvider = (_dictState.dictPanel.listProviders || []).find(
            function (dictEntry) { return dictEntry.sProvider === sProvider; })
            || {};
        var sModel = dictProvider.sDefaultTestModel ? ""
            : (dictProvider.sLastTestedModel || "");
        if (!dictProvider.sDefaultTestModel && !sModel) {
            /* No model is known for this provider, and a guessed one
               would spend a paid request on the wrong model: the
               consent modal asks for it instead. */
            fnOpenConsent(_dictState.sContainerId, null);
            return;
        }
        VaibifyApp.fnShowConfirmModal(
            "Re-run the " + sProvider + " credential test?",
            "Starting the test suspends " + sProvider + " for every " +
            "project using this image until the new test passes. It " +
            "makes about two paid requests.",
            function () {
                _fnStartTests([{sProvider: sProvider,
                                sRequestedModel: sModel}]);
            });
    }

    function _fnConfirmWithdraw(sProvider) {
        VaibifyApp.fnShowConfirmModal(
            "Withdraw consent for " + sProvider + "?",
            "Councils already running finish the turns they have " +
            "started; no later turn will be given a copy of this " +
            "login, for any project using this image, until you " +
            "consent and a new test passes.",
            function () { _fnWithdraw(sProvider); });
    }

    async function _fnWithdraw(sProvider) {
        try {
            await VaibifyApi.fnDelete(_fsRoute("/credential-consent/" +
                encodeURIComponent(sProvider)));
            await _fdictLoadPanel();
            _fnRenderPanel();
        } catch (error) {
            VaibifyDiagnosis.fnReportFailureFromError(error);
        }
        if (typeof VaibifyAgentCouncil !== "undefined") {
            VaibifyAgentCouncil.fnRefreshCapabilities();
        }
    }

    function fnInitialize() {
        _fnBind("btnCouncilConsentModalClose", fnHideModal);
    }

    document.addEventListener("DOMContentLoaded", fnInitialize);

    return {
        S_RESIDUAL_RISK_PARAGRAPH: S_RESIDUAL_RISK_PARAGRAPH,
        fnOpenConsent: fnOpenConsent,
        fnOpenPanel: fnOpenPanel,
        fnHideModal: fnHideModal,
    };
})();
