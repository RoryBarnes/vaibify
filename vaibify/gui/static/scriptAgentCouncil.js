/* Vaibify — Agent Council planning UI (design/agentCouncil.md section 6).
 *
 * A read-only, poll-driven surface over the container-only council
 * backend (vaibify/gui/routes/councilRoutes.py). Every URL here is a
 * same-origin RELATIVE path so a tunnelled remote session forwards it on
 * the one port that reaches the execution host — the invariant
 * tests/testNoUnforwardedUrlsInRemoteSession.py governs this file the
 * moment it exists. No absolute address, no hardcoded loopback port, no
 * window.open with a custom scheme is handed to the browser.
 *
 * The frontend NEVER transitions campaign state optimistically: it
 * renders the backend registry's truth, refetched after every human
 * action and on every poll tick. It never claims a participant's private
 * chain-of-thought, and it never opens a shell — the workspace is not
 * attached to the terminal route (section 6.4).
 */

var VaibifyAgentCouncil = (function () {
    "use strict";

    /* Poll cadences (section 11): a live campaign streams turn events, a
       waiting campaign only needs reload recovery, and a hidden
       workspace polls nothing at all. */
    var I_POLL_LIVE_MILLISECONDS = 3000;
    var I_POLL_IDLE_MILLISECONDS = 12000;

    /* Campaign states whose turns are executing, so the console is worth
       polling quickly. Mirrors agentCouncilCampaign.py's vocabulary; the
       set is small and the frontend renders whatever state the server
       reports even when it is outside this set. */
    var SET_LIVE_STATES = {
        "draft": true, "planning": true,
    };
    var SET_TERMINAL_STATES = {
        "planAccepted": true, "awaitingImplementation": true,
        "failed": true, "archived": true,
    };

    /* The default council settings (section 6.3.2), each shown with its
       safe default so a researcher can launch without touching one.
       These are settings, not a model table — no model id is hardcoded
       anywhere in this module (section 6.3.1 / 8.2). */
    var DICT_DEFAULT_SETTINGS = {
        bPeerAnonymity: true,
        sEffortPerParticipant: "standard",
        sExecutionPermission: "fullSandbox",
        iMinimumRounds: 1,
    };

    /* Shared, mutated in place — never reassigned (the IIFE state trap in
       vaibify/gui/static/AGENTS.md). */
    var _setKnownEventSequences = new Set();

    var _dictState = {
        sContainerId: "",
        dictCapabilities: null,
        listSummaries: [],
        sActiveCampaignId: "",
        dictCampaign: null,
        listEvents: [],
        iHighestSequenceSeen: 0,
        iLowestRetainedSeen: 0,
        bEvictionSeen: false,
        sActiveTab: "council",
        sRenderSignature: "",
        iPollTimer: null,
        bPollInFlight: false,
        listDraftParticipants: [],
        iChairbotIndex: 0,
    };

    /* ------------------------------------------------------------------ */
    /* Lifecycle                                                          */
    /* ------------------------------------------------------------------ */

    function fnActivate(sContainerId) {
        _dictState.sContainerId = sContainerId || "";
        fnRefreshCapabilities();
    }

    function fnTeardown() {
        _fnStopPolling();
        _dictState.sContainerId = "";
        _dictState.dictCapabilities = null;
        _dictState.listSummaries = [];
        _dictState.sActiveCampaignId = "";
        _dictState.dictCampaign = null;
        _dictState.listEvents = [];
        _dictState.iHighestSequenceSeen = 0;
        _dictState.iLowestRetainedSeen = 0;
        _dictState.bEvictionSeen = false;
        _dictState.sActiveTab = "council";
        _dictState.sRenderSignature = "";
        _dictState.listDraftParticipants = [];
        _dictState.iChairbotIndex = 0;
        _setKnownEventSequences.clear();
        _fnHideWorkspace();
        _fnHideModal();
        _fnRenderToolbarButton();
    }

    async function fnRefreshCapabilities() {
        var elButton = document.getElementById("btnAgentCouncil");
        if (!elButton || !_dictState.sContainerId) {
            _fnRenderToolbarButton();
            return;
        }
        try {
            _dictState.dictCapabilities = await VaibifyApi.fdictGet(
                _fsRoute("/capabilities"));
        } catch (error) {
            _dictState.dictCapabilities = {
                bAvailable: false, sUnavailableIn: "",
                sReason: "Council availability could not be read: " +
                    (error.message || String(error)),
                listProviders: [],
            };
        }
        _fnRenderToolbarButton();
    }

    /* ------------------------------------------------------------------ */
    /* Toolbar button (section 6.1)                                       */
    /* ------------------------------------------------------------------ */

    function _fnRenderToolbarButton() {
        var elButton = document.getElementById("btnAgentCouncil");
        if (!elButton) return;
        var dictState = _fdictToolbarState();
        elButton.disabled = dictState.bDisabled;
        elButton.title = dictState.sTitle;
        elButton.classList.toggle(
            "council-attention", dictState.bAttention);
        elButton.classList.toggle("council-running", dictState.bRunning);
    }

    function _fdictToolbarState() {
        if (!_dictState.sContainerId) {
            return _fdictDisabled("Open a project to convene a council.");
        }
        var dictCapabilities = _dictState.dictCapabilities;
        if (!dictCapabilities) {
            return _fdictDisabled("Checking council availability…");
        }
        if (!dictCapabilities.bAvailable) {
            return _fdictDisabled(_fsUnavailableExplanation(dictCapabilities));
        }
        if (_fiSupportedParticipantCount(dictCapabilities) < 2) {
            return _fdictDisabled(
                "A council needs at least two supported participants; " +
                "only " + _fiSupportedParticipantCount(dictCapabilities) +
                " is available on this project.");
        }
        return _fdictEnabledState();
    }

    function _fdictDisabled(sTitle) {
        return {
            bDisabled: true, sTitle: sTitle,
            bAttention: false, bRunning: false,
        };
    }

    function _fdictEnabledState() {
        var bRunning = _fbActiveCampaignIsLive();
        var bAttention = _fbActiveCampaignNeedsHuman()
            || _fbActiveCampaignPlanReady();
        return {
            bDisabled: false,
            sTitle: bAttention
                ? "The council needs your attention."
                : "Convene an Agent Council to plan a change.",
            bAttention: bAttention,
            bRunning: bRunning,
        };
    }

    function _fsUnavailableExplanation(dictCapabilities) {
        /* The on-ramp wording is fixed by section 21: a host project is
           told to CONVERT. The neighbouring host action that merely
           renames a sandbox leaves it in host mode, and the route would
           refuse a second time, so that word is never offered as the way
           forward. The marker, not the prose, is what this branch keys
           on. */
        if (dictCapabilities.sUnavailableIn === "host-mode") {
            return "This project runs directly on this machine and has " +
                "no container to build a runner from. " +
                "Convert this project to a container to convene a council.";
        }
        return dictCapabilities.sReason
            || "Convening a council is unavailable on this project.";
    }

    function _fiSupportedParticipantCount(dictCapabilities) {
        return (dictCapabilities.listProviders || []).length;
    }

    function fnHandleToolbarClick() {
        if (!_dictState.sContainerId) return;
        var dictCapabilities = _dictState.dictCapabilities;
        if (!dictCapabilities || !dictCapabilities.bAvailable) {
            VaibifyApp.fnShowToast(
                _fsUnavailableExplanation(dictCapabilities || {}), "info");
            return;
        }
        if (_dictState.sActiveCampaignId) {
            _fnShowWorkspace();
            return;
        }
        _fnOpenCreationChooser();
    }

    /* ------------------------------------------------------------------ */
    /* Creation chooser (section 6.2) and planning form (section 6.3)     */
    /* ------------------------------------------------------------------ */

    async function _fnOpenCreationChooser() {
        await _fnRefreshSummaries();
        var elBody = document.getElementById("agentCouncilModalBody");
        if (!elBody) return;
        elBody.innerHTML =
            "<h2>Agent Council</h2>" +
            "<div class=\"council-chooser\">" +
            "<button type=\"button\" id=\"btnCouncilPlanChange\" " +
            "class=\"btn btn-primary council-choice\">Plan a change</button>" +
            "<button type=\"button\" id=\"btnCouncilOpenExisting\" " +
            "class=\"btn council-choice\"" +
            (_dictState.listSummaries.length ? "" : " disabled") +
            ">Open an existing campaign (" +
            _dictState.listSummaries.length + ")</button>" +
            "</div>" +
            _fsSummariesList();
        _fnShowModal();
        _fnBindChooser();
    }

    function _fsSummariesList() {
        if (!_dictState.listSummaries.length) return "";
        var sRows = _dictState.listSummaries.map(function (dictSummary) {
            return "<li><button type=\"button\" class=\"council-open-row\" " +
                "data-campaign=\"" + _fsEscape(dictSummary.sCampaignId) +
                "\"><span class=\"council-open-state\">" +
                _fsEscape(dictSummary.sState) + "</span> " +
                _fsEscape(dictSummary.sQuestion) + "</button></li>";
        }).join("");
        return "<ul class=\"council-summaries\">" + sRows + "</ul>";
    }

    function _fnBindChooser() {
        var elPlan = document.getElementById("btnCouncilPlanChange");
        if (elPlan) {
            elPlan.addEventListener("click", _fnOpenPlanningForm);
        }
        var elOpen = document.getElementById("btnCouncilOpenExisting");
        if (elOpen && _dictState.listSummaries.length) {
            elOpen.addEventListener("click", function () {
                _fnFocusCampaign(_dictState.listSummaries[0].sCampaignId);
            });
        }
        document.querySelectorAll(".council-open-row").forEach(
            function (elRow) {
                elRow.addEventListener("click", function () {
                    _fnFocusCampaign(elRow.getAttribute("data-campaign"));
                });
            });
    }

    function _fnOpenPlanningForm() {
        _fnSeedDraftParticipants();
        var elBody = document.getElementById("agentCouncilModalBody");
        if (!elBody) return;
        elBody.innerHTML = _fsPlanningFormMarkup();
        _fnBindPlanningForm();
    }

    function _fnSeedDraftParticipants() {
        var listProviders = _flistSupportedProviders();
        var sFirst = listProviders.length ? listProviders[0] : "";
        var sSecond = listProviders.length > 1 ? listProviders[1] : sFirst;
        _dictState.listDraftParticipants = [
            {sProvider: sFirst, sRequestedModel: "", sRole: ""},
            {sProvider: sSecond, sRequestedModel: "", sRole: ""},
        ];
        _dictState.iChairbotIndex = 0;
    }

    function _flistSupportedProviders() {
        var dictCapabilities = _dictState.dictCapabilities || {};
        return (dictCapabilities.listProviders || []).map(
            function (dictProvider) { return dictProvider.sProvider; });
    }

    function _fsPlanningFormMarkup() {
        return "<h2>Plan a change</h2>" +
            "<label class=\"council-field\">Question" +
            "<textarea id=\"councilQuestion\" rows=\"4\" " +
            "placeholder=\"What change should the council plan?\">" +
            "</textarea></label>" +
            "<div id=\"councilParticipants\"></div>" +
            "<button type=\"button\" id=\"btnCouncilAddParticipant\" " +
            "class=\"btn btn-small\">Add participant</button>" +
            _fsChairbotSelectorMarkup() +
            _fsSettingsMarkup() +
            _fsProtocolExplanation() +
            _fsDisclosureMarkup() +
            "<div class=\"council-form-actions\">" +
            "<button type=\"button\" id=\"btnCouncilConvene\" " +
            "class=\"btn btn-primary\">Convene council</button>" +
            "<button type=\"button\" id=\"btnCouncilCancel\" " +
            "class=\"btn\">Cancel</button></div>" +
            "<div id=\"councilFormError\" class=\"council-error\"></div>";
    }

    function _fsChairbotSelectorMarkup() {
        /* Default: the first configured participant — a structural
           default, not a capability judgment (section 6.3.1). Changeable
           in one click. Options are re-rendered when participants
           change. */
        return "<label class=\"council-field\">Chairbot (holds the pen)" +
            "<select id=\"councilChairbot\"></select></label>" +
            "<p class=\"council-hint\">The chairbot synthesizes each " +
            "round's candidate. It never votes on its own plan; every " +
            "other participant vetoes it.</p>";
    }

    function _fsSettingsMarkup() {
        var dict = DICT_DEFAULT_SETTINGS;
        return "<fieldset class=\"council-settings\"><legend>Council " +
            "settings</legend>" +
            "<label><input type=\"checkbox\" id=\"councilPeerAnonymity\"" +
            (dict.bPeerAnonymity ? " checked" : "") +
            "> Peer anonymity in review (default on)</label>" +
            "<label>Effort per participant " +
            "<input type=\"text\" id=\"councilEffort\" value=\"" +
            _fsEscape(dict.sEffortPerParticipant) + "\"></label>" +
            "<label>Execution permission " +
            "<select id=\"councilExecution\">" +
            "<option value=\"fullSandbox\" selected>Full sandbox " +
            "(default)</option>" +
            "<option value=\"readOnly\">Read-only council</option>" +
            "</select></label>" +
            "<label>Minimum rounds " +
            "<input type=\"number\" id=\"councilMinimumRounds\" min=\"1\" " +
            "value=\"" + dict.iMinimumRounds + "\"></label>" +
            "</fieldset>";
    }

    function _fsProtocolExplanation() {
        return "<p class=\"council-hint\">Standard protocol: each " +
            "participant proposes independently, then adversarially " +
            "cross-reviews the others, the chairbot synthesizes one " +
            "candidate plan, and every other participant vetoes it. The " +
            "plan is ready only when every required veto accepts. " +
            "Cross-review cost grows with the participant count.</p>";
    }

    function _fsDisclosureMarkup() {
        /* The section 2.7 residual-risk statement (drafted in
           design/agentCouncilPhase0Findings.md), plus the execution
           boundary, which providers receive content, and billing. Under
           a remote session it names WHOSE machine the reused login lives
           on, because "your subscription" is a claim about the execution
           host's account, not the laptop's. */
        return "<div class=\"council-disclosure\">" +
            "<h3>Before you convene</h3>" +
            "<p><strong>Execution boundary.</strong> Each participant " +
            "runs against a copy of your project in a disposable runner " +
            "or sandbox. Every write it makes is discarded when the " +
            "runner is destroyed; nothing it does touches your project " +
            "container or its files.</p>" +
            "<p><strong>Credential exposure (section 2.7).</strong> This " +
            "council reuses the provider subscription already logged in " +
            "for this project, copying the narrowest token that " +
            "authenticates into one throwaway container. A " +
            "prompt-injected model could read its own copied token or " +
            "push data out through the one network path it is allowed " +
            "(its provider's API). The copy is destroyed with the " +
            "container, but destroying the copy does not revoke the " +
            "credential — revoke at the provider if a run is " +
            "compromised.</p>" +
            _fsExecutionHostDisclosure() +
            "<p><strong>Provider content and billing.</strong> Every " +
            "provider you configure a participant for receives your " +
            "project's content. The runner backend bills your existing " +
            "subscription; the API fallback bills per token against a " +
            "configured API key.</p>" +
            "<p><strong>Records.</strong> The campaign — proposals, " +
            "critiques, candidate plans, decisions — is saved to " +
            "this hub's local application data, outside your repository " +
            "and credential-redacted, until you accept a plan.</p>" +
            "</div>";
    }

    function _fsExecutionHostDisclosure() {
        /* Section 21: name the execution host where a reader could
           otherwise assume the reused login is theirs. Ask the server's
           topology rather than assuming hub == laptop. */
        if (!VaibifyApp.fbIsRemoteSession()) return "";
        var sHost = VaibifyApp.fsGetExecutionHostname() || "another machine";
        return "<p class=\"council-remote-note\"><strong>Remote " +
            "session.</strong> The subscription being reused belongs to " +
            "the account configured on <em>" + _fsEscape(sHost) +
            "</em>, the machine running this hub — not the computer " +
            "you are sitting at.</p>";
    }

    function _fnBindPlanningForm() {
        _fnRenderParticipantCards();
        _fnBindElement("btnCouncilAddParticipant", _fnAddParticipant);
        _fnBindElement("btnCouncilConvene", _fnConveneCouncil);
        _fnBindElement("btnCouncilCancel", _fnHideModal);
    }

    function _fnRenderParticipantCards() {
        var elHost = document.getElementById("councilParticipants");
        if (!elHost) return;
        elHost.innerHTML = _dictState.listDraftParticipants.map(
            _fsParticipantCard).join("");
        _fnRenderChairbotOptions();
        _fnBindParticipantCards();
    }

    function _fsParticipantCard(dictParticipant, iIndex) {
        return "<div class=\"council-participant-card\" data-index=\"" +
            iIndex + "\">" +
            "<span class=\"council-participant-title\">Participant " +
            (iIndex + 1) + "</span>" +
            _fsProviderSelect(dictParticipant, iIndex) +
            _fsModelField(dictParticipant, iIndex) +
            "<input type=\"text\" class=\"council-role\" " +
            "data-index=\"" + iIndex + "\" placeholder=\"Optional role\" " +
            "value=\"" + _fsEscape(dictParticipant.sRole) + "\">" +
            _fsProviderAvailability(dictParticipant.sProvider) +
            (_dictState.listDraftParticipants.length > 2
                ? "<button type=\"button\" class=\"council-remove\" " +
                  "data-index=\"" + iIndex + "\">Remove</button>"
                : "") +
            "</div>";
    }

    function _fsProviderSelect(dictParticipant, iIndex) {
        var sOptions = _flistSupportedProviders().map(function (sProvider) {
            var bSelected = sProvider === dictParticipant.sProvider;
            return "<option value=\"" + _fsEscape(sProvider) + "\"" +
                (bSelected ? " selected" : "") + ">" +
                _fsEscape(sProvider) + "</option>";
        }).join("");
        return "<select class=\"council-provider\" data-index=\"" +
            iIndex + "\">" + sOptions + "</select>";
    }

    function _fsModelField(dictParticipant, iIndex) {
        /* Live from the capabilities endpoint, never a hardcoded table
           (section 8.2): if the provider entry carries a discovered
           model list, present it; otherwise a free-text id field so no
           stale alias table lives in this source. */
        var listModels = _flistProviderModels(dictParticipant.sProvider);
        if (listModels.length) {
            var sOptions = listModels.map(function (sModel) {
                return "<option value=\"" + _fsEscape(sModel) + "\"" +
                    (sModel === dictParticipant.sRequestedModel
                        ? " selected" : "") + ">" +
                    _fsEscape(sModel) + "</option>";
            }).join("");
            return "<select class=\"council-model\" data-index=\"" +
                iIndex + "\"><option value=\"\">Choose a model…" +
                "</option>" + sOptions + "</select>";
        }
        return "<input type=\"text\" class=\"council-model\" " +
            "data-index=\"" + iIndex + "\" placeholder=\"Model id\" " +
            "value=\"" + _fsEscape(dictParticipant.sRequestedModel) + "\">";
    }

    function _flistProviderModels(sProvider) {
        var dictCapabilities = _dictState.dictCapabilities || {};
        var listProviders = dictCapabilities.listProviders || [];
        var dictMatch = listProviders.filter(function (dictProvider) {
            return dictProvider.sProvider === sProvider;
        })[0];
        return (dictMatch && dictMatch.listModels) || [];
    }

    function _fsProviderAvailability(sProvider) {
        var listProviders = (_dictState.dictCapabilities || {})
            .listProviders || [];
        var dictMatch = listProviders.filter(function (dictProvider) {
            return dictProvider.sProvider === sProvider;
        })[0];
        if (!dictMatch) {
            return "<span class=\"council-unavailable\">No reviewed " +
                "adapter — unavailable</span>";
        }
        return "<span class=\"council-available\">Backend: " +
            _fsEscape(dictMatch.sBackend || "runner") + "</span>";
    }

    function _fnRenderChairbotOptions() {
        var elSelect = document.getElementById("councilChairbot");
        if (!elSelect) return;
        elSelect.innerHTML = _dictState.listDraftParticipants.map(
            function (dictParticipant, iIndex) {
                return "<option value=\"" + iIndex + "\"" +
                    (iIndex === _dictState.iChairbotIndex ? " selected" : "") +
                    ">Participant " + (iIndex + 1) + " (" +
                    _fsEscape(dictParticipant.sProvider || "?") + ")</option>";
            }).join("");
        elSelect.addEventListener("change", function () {
            _dictState.iChairbotIndex = parseInt(elSelect.value, 10) || 0;
        });
    }

    function _fnBindParticipantCards() {
        document.querySelectorAll(".council-provider").forEach(
            function (elSelect) {
                elSelect.addEventListener("change", function () {
                    _fnUpdateDraft(elSelect, "sProvider");
                    _fnRenderParticipantCards();
                });
            });
        _fnBindDraftInputs(".council-model", "sRequestedModel");
        _fnBindDraftInputs(".council-role", "sRole");
        document.querySelectorAll(".council-remove").forEach(
            function (elButton) {
                elButton.addEventListener("click", function () {
                    _fnRemoveParticipant(
                        parseInt(elButton.getAttribute("data-index"), 10));
                });
            });
    }

    function _fnBindDraftInputs(sSelector, sField) {
        document.querySelectorAll(sSelector).forEach(function (elInput) {
            elInput.addEventListener("input", function () {
                _fnUpdateDraft(elInput, sField);
            });
        });
    }

    function _fnUpdateDraft(elInput, sField) {
        var iIndex = parseInt(elInput.getAttribute("data-index"), 10);
        if (_dictState.listDraftParticipants[iIndex]) {
            _dictState.listDraftParticipants[iIndex][sField] = elInput.value;
        }
    }

    function _fnAddParticipant() {
        var listProviders = _flistSupportedProviders();
        _dictState.listDraftParticipants.push({
            sProvider: listProviders.length ? listProviders[0] : "",
            sRequestedModel: "", sRole: "",
        });
        _fnRenderParticipantCards();
    }

    function _fnRemoveParticipant(iIndex) {
        if (_dictState.listDraftParticipants.length <= 2) return;
        _dictState.listDraftParticipants.splice(iIndex, 1);
        if (_dictState.iChairbotIndex >=
                _dictState.listDraftParticipants.length) {
            _dictState.iChairbotIndex = 0;
        }
        _fnRenderParticipantCards();
    }

    /* ------------------------------------------------------------------ */
    /* Convene / focus / open                                             */
    /* ------------------------------------------------------------------ */

    async function _fnConveneCouncil() {
        var sQuestion = _fsReadValue("councilQuestion");
        var elError = document.getElementById("councilFormError");
        if (!sQuestion) {
            if (elError) elError.textContent = "A question is required.";
            return;
        }
        var dictBody = {
            sQuestion: sQuestion,
            listParticipants: _flistBuildParticipantPayload(),
            iChairbotIndex: _dictState.iChairbotIndex,
        };
        try {
            var dictResult = await VaibifyApi.fdictPost(
                _fsRoute("/start"), dictBody);
            _fnAdoptCampaign(dictResult.sCampaignId, dictResult.dictCampaign);
            _fnHideModal();
            _fnShowWorkspace();
        } catch (error) {
            if (elError) {
                elError.textContent = "Could not convene: " +
                    (error.message || String(error));
            }
        }
    }

    function _flistBuildParticipantPayload() {
        return _dictState.listDraftParticipants.map(function (dictDraft) {
            return {
                sProvider: dictDraft.sProvider,
                sRequestedModel: dictDraft.sRequestedModel,
                sRole: dictDraft.sRole || "",
            };
        });
    }

    async function _fnFocusCampaign(sCampaignId) {
        _fnHideModal();
        await _fnLoadCampaign(sCampaignId);
        _fnShowWorkspace();
    }

    async function _fnLoadCampaign(sCampaignId) {
        try {
            var dictResult = await VaibifyApi.fdictGet(
                _fsRoute("/" + sCampaignId));
            _fnAdoptCampaign(sCampaignId, dictResult.dictCampaign);
        } catch (error) {
            VaibifyApp.fnShowToast(
                "Could not load campaign: " +
                (error.message || String(error)), "error");
        }
    }

    async function _fnReloadActiveCampaign() {
        /* Refetch the active campaign IN PLACE after a human action —
           updating the record without re-adopting it. Adoption resets
           the active tab and clears the event log, which is right when
           SWITCHING campaigns and wrong after accepting a plan or
           answering a question in the one already open. */
        try {
            var dictResult = await VaibifyApi.fdictGet(
                _fsRoute("/" + _dictState.sActiveCampaignId));
            _dictState.dictCampaign = dictResult.dictCampaign || null;
        } catch (error) {
            VaibifyApp.fnShowToast(
                "Could not refresh council: " +
                (error.message || String(error)), "error");
            return;
        }
        _fnRenderToolbarButton();
        _fnRenderWorkspace();
    }

    function _fnAdoptCampaign(sCampaignId, dictCampaign) {
        _dictState.sActiveCampaignId = sCampaignId;
        _dictState.dictCampaign = dictCampaign || null;
        _dictState.listEvents = [];
        _dictState.iHighestSequenceSeen = 0;
        _dictState.iLowestRetainedSeen = 0;
        _dictState.bEvictionSeen = false;
        _setKnownEventSequences.clear();
        _dictState.sActiveTab = "council";
        _dictState.sRenderSignature = "";
        _fnRenderToolbarButton();
    }

    async function _fnRefreshSummaries() {
        try {
            var dictResult = await VaibifyApi.fdictGet(_fsRoute(""));
            _dictState.listSummaries = (
                dictResult.listCampaigns || []).slice().reverse();
        } catch (error) {
            _dictState.listSummaries = [];
        }
    }

    /* ------------------------------------------------------------------ */
    /* Event polling (section 11)                                         */
    /* ------------------------------------------------------------------ */

    function _fnStartPolling() {
        _fnStopPolling();
        _fnScheduleNextPoll(0);
    }

    function _fnStopPolling() {
        if (_dictState.iPollTimer) {
            clearTimeout(_dictState.iPollTimer);
            _dictState.iPollTimer = null;
        }
    }

    function _fnScheduleNextPoll(iDelay) {
        _dictState.iPollTimer = setTimeout(_fnPollTick, iDelay);
    }

    async function _fnPollTick() {
        if (_dictState.bPollInFlight) {
            _fnScheduleNextPoll(_fiPollInterval());
            return;
        }
        if (!_fbWorkspaceVisible() || !_dictState.sActiveCampaignId) {
            return;
        }
        _dictState.bPollInFlight = true;
        try {
            await _fnPollEventsOnce();
            await _fnLoadCampaignQuietly();
        } catch (error) {
            /* A transient poll error is not ground truth; the next tick
               retries. Never mask it as success. */
            void error;
        } finally {
            _dictState.bPollInFlight = false;
        }
        if (_fbWorkspaceVisible()) {
            _fnScheduleNextPoll(_fiPollInterval());
        }
    }

    async function _fnPollEventsOnce() {
        var dictResult = await VaibifyApi.fdictGet(
            _fsRoute("/" + _dictState.sActiveCampaignId + "/events?iAfter=" +
                _dictState.iHighestSequenceSeen));
        _fnIngestEvents(dictResult);
    }

    function _fnIngestEvents(dictResult) {
        _dictState.iLowestRetainedSeen = dictResult.iLowestRetainedSequence || 0;
        if (dictResult.bEvictionHasOccurred) _dictState.bEvictionSeen = true;
        (dictResult.listEvents || []).forEach(function (dictEvent) {
            if (_setKnownEventSequences.has(dictEvent.iSequence)) return;
            _setKnownEventSequences.add(dictEvent.iSequence);
            _dictState.listEvents.push(dictEvent);
            if (dictEvent.iSequence > _dictState.iHighestSequenceSeen) {
                _dictState.iHighestSequenceSeen = dictEvent.iSequence;
            }
        });
        _fnRenderIfChanged();
    }

    async function _fnLoadCampaignQuietly() {
        var dictResult = await VaibifyApi.fdictGet(
            _fsRoute("/" + _dictState.sActiveCampaignId));
        _dictState.dictCampaign = dictResult.dictCampaign || null;
        _fnRenderToolbarButton();
        _fnRenderIfChanged();
    }

    function _fnRenderIfChanged() {
        /* A poll tick re-renders only when the backend truth it just
           read differs from what is on screen. Re-rendering on every
           idle tick would wipe an in-progress answer or composer field,
           so a quiet poll must leave the DOM — and its form state —
           untouched. Interactive paths call _fnRenderWorkspace directly
           and always render. */
        if (_fsWorkspaceSignature() !== _dictState.sRenderSignature) {
            _fnRenderWorkspace();
        }
    }

    function _fsWorkspaceSignature() {
        var dictCampaign = _dictState.dictCampaign || {};
        var dictGate = dictCampaign.dictPendingHumanGate || {};
        return [
            dictCampaign.sState || "",
            _dictState.iHighestSequenceSeen,
            _dictState.sActiveTab,
            _dictState.bEvictionSeen ? 1 : 0,
            dictGate.sGateKind || "",
            dictCampaign.dictCandidatePlan ? 1 : 0,
            dictCampaign.bPlanningBaselineStale ? 1 : 0,
        ].join("|");
    }

    function _fiPollInterval() {
        return _fbActiveCampaignIsLive()
            ? I_POLL_LIVE_MILLISECONDS
            : I_POLL_IDLE_MILLISECONDS;
    }

    /* ------------------------------------------------------------------ */
    /* Workspace (section 6.4)                                            */
    /* ------------------------------------------------------------------ */

    function _fnShowWorkspace() {
        var elWorkspace = document.getElementById("agentCouncilWorkspace");
        if (!elWorkspace) return;
        elWorkspace.classList.remove("council-collapsed");
        elWorkspace.style.display = "";
        _fnRenderWorkspace();
        _fnStartPolling();
    }

    function _fnHideWorkspace() {
        var elWorkspace = document.getElementById("agentCouncilWorkspace");
        if (elWorkspace) elWorkspace.style.display = "none";
        _fnStopPolling();
    }

    function _fbWorkspaceVisible() {
        var elWorkspace = document.getElementById("agentCouncilWorkspace");
        return Boolean(elWorkspace && elWorkspace.style.display !== "none");
    }

    function _fnRenderWorkspace() {
        var elBody = document.getElementById("agentCouncilWorkspaceBody");
        if (!elBody) return;
        var dictCampaign = _dictState.dictCampaign;
        if (!dictCampaign) {
            elBody.innerHTML = "<p class=\"council-hint\">No council " +
                "selected.</p>";
            return;
        }
        elBody.innerHTML = _fsTabBar(dictCampaign) + _fsEvictionNotice() +
            "<div class=\"council-tab-content\">" +
            _fsActiveTabContent(dictCampaign) + "</div>";
        _fnBindWorkspace(dictCampaign);
        _dictState.sRenderSignature = _fsWorkspaceSignature();
    }

    function _fsTabBar(dictCampaign) {
        var sTabs = "<button type=\"button\" class=\"council-tab\" " +
            "data-tab=\"council\">Council</button>";
        (dictCampaign.listParticipants || []).forEach(
            function (dictParticipant, iIndex) {
                sTabs += "<button type=\"button\" class=\"council-tab\" " +
                    "data-tab=\"participant:" +
                    _fsEscape(dictParticipant.sParticipantId) + "\">" +
                    "Participant " + (iIndex + 1) + "</button>";
            });
        sTabs += "<button type=\"button\" class=\"council-tab\" " +
            "data-tab=\"plan\">Plan</button>";
        return "<div class=\"council-tabs\">" + sTabs + "</div>";
    }

    function _fsEvictionNotice() {
        var bGap = _dictState.iLowestRetainedSeen > 1
            && _dictState.iLowestRetainedSeen >
                (_fiEarliestSeenSequence() + 1);
        if (!_dictState.bEvictionSeen && !bGap) return "";
        return "<p class=\"council-eviction\">Earlier console output is " +
            "no longer retained (events " +
            (_dictState.iLowestRetainedSeen > 1
                ? "before #" + _dictState.iLowestRetainedSeen + " " : "") +
            "evicted). The structured phase artifacts remain.</p>";
    }

    function _fiEarliestSeenSequence() {
        if (!_dictState.listEvents.length) return 0;
        return _dictState.listEvents[0].iSequence;
    }

    function _fsActiveTabContent(dictCampaign) {
        var sTab = _dictState.sActiveTab;
        if (sTab === "plan") return _fsPlanTab(dictCampaign);
        if (sTab.indexOf("participant:") === 0) {
            return _fsParticipantTab(
                dictCampaign, sTab.substring("participant:".length));
        }
        return _fsCouncilTab(dictCampaign);
    }

    function _fsCouncilTab(dictCampaign) {
        return "<div class=\"council-summary\">" +
            "<h3>" + _fsEscape(dictCampaign.sQuestion) + "</h3>" +
            "<p>Phase: <strong>" + _fsEscape(dictCampaign.sState) +
            "</strong></p>" +
            _fsBaselineWarning(dictCampaign) +
            _fsVerdictBanner(dictCampaign) +
            _fsParticipantStates(dictCampaign) +
            _fsResearcherDecisions(dictCampaign) +
            "</div>" +
            _fsHumanSurface(dictCampaign);
    }

    function _fsBaselineWarning(dictCampaign) {
        /* The council reasoned against a snapshot of the project taken
           when it started. If the backend reports the project has since
           moved, say so — a plan built on a stale baseline is not a plan
           for the current tree. Rendered only from backend truth. */
        if (!dictCampaign.bPlanningBaselineStale) return "";
        return "<p class=\"council-verdict council-verdict-" +
            "blockedForWantOfEvidence\">The project changed since this " +
            "council started. Its plan was built against an earlier " +
            "baseline (" +
            _fsEscape(dictCampaign.sPlanningBaselineSummary || "") +
            "); request another pass to plan against the current tree." +
            "</p>";
    }

    function _fsVerdictBanner(dictCampaign) {
        /* Section 2.1: distinguish confirmed / asserted / blocked.
           Consensus is not proof — the banner never says "verified"
           without the backend's own verdict. */
        var dictPlan = dictCampaign.dictCandidatePlan;
        if (!dictPlan) {
            return "<p class=\"council-verdict council-verdict-pending\">" +
                "No candidate plan yet. One agent's confidence and " +
                "several agents' agreement are not evidence.</p>";
        }
        var sVerdict = dictPlan.sResultClassification || "asserted";
        return "<p class=\"council-verdict council-verdict-" +
            _fsEscape(sVerdict) + "\">Current result: " +
            _fsEscape(sVerdict) + "</p>";
    }

    function _fsParticipantStates(dictCampaign) {
        var sRows = (dictCampaign.listParticipants || []).map(
            function (dictParticipant, iIndex) {
                var bChair = dictParticipant.sParticipantId ===
                    dictCampaign.sChairbotParticipantId;
                return "<li>Participant " + (iIndex + 1) + " — " +
                    _fsEscape(dictParticipant.sProvider) + " / " +
                    _fsEscape(dictParticipant.sRequestedModel || "?") +
                    (bChair ? " <span class=\"council-chair\">chairbot" +
                        "</span>" : "") + " " +
                    _fsParticipantStatusChip(dictParticipant, dictCampaign) +
                    "</li>";
            }).join("");
        return "<ul class=\"council-participant-states\">" + sRows + "</ul>";
    }

    function _fsParticipantStatusChip(dictParticipant, dictCampaign) {
        /* Walks the runner lifecycle from BACKEND truth only. "Verified
           stopped" is shown solely when the backend reports it (after the
           absence probe); a quarantined runner is a persistent warning
           badge that only reconciliation clears (section 6.4). Nothing
           here is optimistic. */
        if (dictParticipant.bQuarantined) {
            return "<span class=\"council-chip council-chip-quarantined\">" +
                "⚠ quarantined runner — reconcile</span>";
        }
        if (dictParticipant.bFailed) {
            return "<span class=\"council-chip council-chip-failed\">" +
                "failed: " + _fsEscape(dictParticipant.sFailureReason || "") +
                "</span>";
        }
        var sLifecycle = dictParticipant.sRunnerLifecycle
            || _fsLifecycleFromCampaign(dictCampaign);
        return "<span class=\"council-chip council-chip-" +
            _fsEscape(sLifecycle) + "\">" +
            _fsEscape(_fsLifecycleLabel(sLifecycle)) + "</span>";
    }

    function _fsLifecycleFromCampaign(dictCampaign) {
        if (SET_LIVE_STATES[dictCampaign.sState]) return "deliberating";
        if (SET_TERMINAL_STATES[dictCampaign.sState]) return "verifiedStopped";
        return "waiting";
    }

    function _fsLifecycleLabel(sLifecycle) {
        var dictLabels = {
            preparingSandbox: "preparing sandbox",
            deliberating: "deliberating",
            cleaningUp: "cleaning up",
            verifiedStopped: "verified stopped",
            waiting: "waiting",
        };
        return dictLabels[sLifecycle] || sLifecycle;
    }

    function _fsResearcherDecisions(dictCampaign) {
        var listDecisions = dictCampaign.listResearcherDecisions || [];
        if (!listDecisions.length) return "";
        var sRows = listDecisions.map(function (dictDecision) {
            return "<li>" + _fsEscape(
                dictDecision.sDecision || JSON.stringify(dictDecision)) +
                "</li>";
        }).join("");
        return "<h4>Your decisions</h4><ul class=\"council-decisions\">" +
            sRows + "</ul>";
    }

    /* ------------------------------------------------------------------ */
    /* Participant console (read-only, section 6.4)                       */
    /* ------------------------------------------------------------------ */

    function _fsParticipantTab(dictCampaign, sParticipantId) {
        var listParticipants = dictCampaign.listParticipants || [];
        var dictParticipant = listParticipants.filter(function (dict) {
            return dict.sParticipantId === sParticipantId;
        })[0] || {};
        return "<div class=\"council-console\">" +
            "<p class=\"council-console-note\">Read-only console. These " +
            "are normalized provider events — messages, files " +
            "inspected, scripts run and their exit codes, usage and " +
            "errors. This is not the model's private reasoning.</p>" +
            _fsParticipantStatusChip(dictParticipant, dictCampaign) +
            _fsEventLog() + "</div>";
    }

    function _fsEventLog() {
        if (!_dictState.listEvents.length) {
            return "<p class=\"council-hint\">No events yet.</p>";
        }
        var sRows = _dictState.listEvents.map(function (dictEvent) {
            return "<li class=\"council-event\"><span " +
                "class=\"council-seq\">#" + dictEvent.iSequence + "</span> " +
                "<span class=\"council-event-kind\">" +
                _fsEscape(dictEvent.sKind) + "</span> " +
                _fsEscape(dictEvent.sDetail || "") +
                (dictEvent.sTurnId ? " <span class=\"council-turn\">(" +
                    _fsEscape(dictEvent.sTurnId) + ")</span>" : "") +
                "</li>";
        }).join("");
        return "<ul class=\"council-event-log\">" + sRows + "</ul>";
    }

    /* ------------------------------------------------------------------ */
    /* Human response surface (section 6.5)                               */
    /* ------------------------------------------------------------------ */

    function _fsHumanSurface(dictCampaign) {
        if (dictCampaign.sState === "needsHuman") {
            return _fsNeedsHumanCard(dictCampaign);
        }
        if (SET_TERMINAL_STATES[dictCampaign.sState]) return "";
        return _fsComposer(dictCampaign);
    }

    function _fsComposer(dictCampaign) {
        var sRecipients = (dictCampaign.listParticipants || []).map(
            function (dictParticipant, iIndex) {
                return "<option value=\"" +
                    _fsEscape(dictParticipant.sParticipantId) + "\">" +
                    "Participant " + (iIndex + 1) + "</option>";
            }).join("");
        return "<div class=\"council-composer\">" +
            "<h4>Message the council</h4>" +
            "<p class=\"council-hint\">Your message is queued for the " +
            "next protocol boundary, recorded in the campaign for every " +
            "participant to see, and never injected into a running turn. " +
            "Choosing one recipient directs who is asked to respond — " +
            "it is not a private side-channel.</p>" +
            "<select id=\"councilRecipient\"><option value=\"\">Whole " +
            "council</option>" + sRecipients + "</select>" +
            "<textarea id=\"councilMessage\" rows=\"3\"></textarea>" +
            "<button type=\"button\" id=\"btnCouncilSend\" " +
            "class=\"btn btn-primary\">Send</button>" +
            "<button type=\"button\" id=\"btnCouncilStop\" " +
            "class=\"btn\">Stop council</button>" +
            "</div>";
    }

    function _fsNeedsHumanCard(dictCampaign) {
        var dictGate = dictCampaign.dictPendingHumanGate || {};
        if (dictGate.sGateKind === "exhaustedRounds") {
            return _fsExhaustedRoundCard(dictCampaign, dictGate);
        }
        return _fsBlockingQuestionCard(dictCampaign, dictGate);
    }

    function _fsBlockingQuestionCard(dictCampaign, dictGate) {
        return "<div class=\"council-needs-human\">" +
            "<h4>The council needs your decision</h4>" +
            "<p>" + _fsEscape(dictGate.sDecisionRequired ||
                "A material choice could not be settled from evidence.") +
            "</p>" +
            _fsGateDetail("Why evidence does not decide it",
                dictGate.sWhyEvidenceInsufficient) +
            _fsGateList("Alternatives and consequences",
                dictGate.listAlternatives) +
            _fsGateList("Participant positions", dictGate.listPositions) +
            "<textarea id=\"councilAnswer\" rows=\"3\"></textarea>" +
            "<button type=\"button\" id=\"btnCouncilAnswer\" " +
            "class=\"btn btn-primary\">Record decision</button>" +
            "</div>";
    }

    function _fsExhaustedRoundCard(dictCampaign, dictGate) {
        /* Exactly the three section 5.1 exits as distinct controls, and
           NO plain respond field that would silently relaunch the spent
           round budget (section 6.5). */
        return "<div class=\"council-needs-human council-exhausted\">" +
            "<h4>Rounds exhausted with objections outstanding</h4>" +
            _fsGateList("Unresolved objections",
                dictGate.listUnresolvedObjections) +
            "<div class=\"council-exits\">" +
            "<button type=\"button\" id=\"btnCouncilGrantRound\" " +
            "class=\"btn\">Grant a bounded resolution round</button>" +
            "<button type=\"button\" id=\"btnCouncilResolveOverride\" " +
            "class=\"btn\">Resolve or override, then a final veto</button>" +
            "<button type=\"button\" id=\"btnCouncilReject\" " +
            "class=\"btn danger\">Reject and archive</button>" +
            "</div></div>";
    }

    function _fsGateDetail(sTitle, sBody) {
        if (!sBody) return "";
        return "<p><strong>" + _fsEscape(sTitle) + ":</strong> " +
            _fsEscape(sBody) + "</p>";
    }

    function _fsGateList(sTitle, listItems) {
        if (!listItems || !listItems.length) return "";
        var sRows = listItems.map(function (jsonItem) {
            return "<li>" + _fsEscape(
                typeof jsonItem === "string"
                    ? jsonItem : JSON.stringify(jsonItem)) + "</li>";
        }).join("");
        return "<p><strong>" + _fsEscape(sTitle) + "</strong></p><ul>" +
            sRows + "</ul>";
    }

    /* ------------------------------------------------------------------ */
    /* Plan tab and accepted-plan actions (section 6.6)                   */
    /* ------------------------------------------------------------------ */

    function _fsPlanTab(dictCampaign) {
        var dictPlan = dictCampaign.dictCandidatePlan;
        if (!dictPlan) {
            return "<p class=\"council-hint\">No candidate plan yet.</p>";
        }
        var sPlanText = dictPlan.sPlanText || dictPlan.sText || "";
        return "<div class=\"council-plan\">" +
            _fsVerdictBanner(dictCampaign) +
            "<pre id=\"councilPlanText\" class=\"council-plan-text\">" +
            _fsEscape(sPlanText) + "</pre>" +
            _fsPlanActions(dictCampaign, sPlanText) + "</div>";
    }

    function _fsPlanActions(dictCampaign, sPlanText) {
        if (SET_TERMINAL_STATES[dictCampaign.sState]) {
            return "<p class=\"council-plan-accepted\">This plan was " +
                "accepted. Give the saved plan and its implementation " +
                "brief to a fresh implementation agent — the council " +
                "does not implement it.</p>";
        }
        return "<div class=\"council-plan-actions\">" +
            "<button type=\"button\" id=\"btnCouncilAcceptPlan\" " +
            "class=\"btn btn-primary\">Accept and save plan</button>" +
            "<button type=\"button\" id=\"btnCouncilAnotherPass\" " +
            "class=\"btn\">Request another pass</button>" +
            "<button type=\"button\" id=\"btnCouncilCopyBrief\" " +
            "class=\"btn\">Copy implementation brief</button>" +
            "<button type=\"button\" id=\"btnCouncilDownloadPlan\" " +
            "class=\"btn\">Download</button>" +
            "<button type=\"button\" id=\"btnCouncilRejectPlan\" " +
            "class=\"btn danger\">Reject</button>" +
            "</div>";
    }

    /* ------------------------------------------------------------------ */
    /* Workspace event binding                                            */
    /* ------------------------------------------------------------------ */

    function _fnBindWorkspace(dictCampaign) {
        document.querySelectorAll(".council-tab").forEach(function (elTab) {
            elTab.addEventListener("click", function () {
                _dictState.sActiveTab = elTab.getAttribute("data-tab");
                _fnRenderWorkspace();
            });
        });
        _fnBindElement("btnCouncilSend", _fnSendMessage);
        _fnBindElement("btnCouncilStop", _fnStopCouncil);
        _fnBindElement("btnCouncilAnswer", _fnAnswerQuestion);
        _fnBindElement("btnCouncilGrantRound", function () {
            _fnRespondExit("grantBoundedResolutionRound");
        });
        _fnBindElement("btnCouncilResolveOverride", function () {
            _fnRespondExit("resolveOrOverrideThenFinalVeto");
        });
        _fnBindElement("btnCouncilReject", function () {
            _fnRespondExit("rejectOrArchiveCandidate");
        });
        _fnBindPlanActions(dictCampaign);
    }

    function _fnBindPlanActions(dictCampaign) {
        _fnBindElement("btnCouncilAcceptPlan", _fnAcceptPlan);
        _fnBindElement("btnCouncilAnotherPass", function () {
            _fnSendMessageText("Please make another pass on the plan.");
        });
        _fnBindElement("btnCouncilCopyBrief", function () {
            _fnCopyBrief(dictCampaign);
        });
        _fnBindElement("btnCouncilDownloadPlan", function () {
            _fnDownloadPlan(dictCampaign);
        });
        _fnBindElement("btnCouncilRejectPlan", _fnStopCouncil);
    }

    /* ------------------------------------------------------------------ */
    /* Human actions — each refetches backend truth, never optimistic     */
    /* ------------------------------------------------------------------ */

    async function _fnSendMessage() {
        var sMessage = _fsReadValue("councilMessage");
        if (!sMessage) return;
        var sRecipient = _fsReadValue("councilRecipient");
        var sPrefixed = sRecipient
            ? "[to participant " + sRecipient + "] " + sMessage
            : sMessage;
        await _fnSendMessageText(sPrefixed);
    }

    async function _fnSendMessageText(sMessage) {
        await _fnPostAction("/" + _dictState.sActiveCampaignId + "/respond",
            {sResponseText: sMessage});
    }

    async function _fnAnswerQuestion() {
        var sAnswer = _fsReadValue("councilAnswer");
        if (!sAnswer) return;
        await _fnPostAction("/" + _dictState.sActiveCampaignId + "/respond",
            {sResponseText: sAnswer});
    }

    async function _fnRespondExit(sExit) {
        /* Each exhausted-round exit maps to one defined transition; the
           backend owns the transition, the frontend only names it. */
        await _fnPostAction("/" + _dictState.sActiveCampaignId + "/respond",
            {sResponseText: "[exit] " + sExit});
    }

    async function _fnStopCouncil() {
        await _fnPostAction(
            "/" + _dictState.sActiveCampaignId + "/request-stop", undefined);
    }

    async function _fnAcceptPlan() {
        var sPlanText = _fsReadValue("councilPlanText");
        if (!sPlanText) {
            var dictPlan = (_dictState.dictCampaign || {}).dictCandidatePlan
                || {};
            sPlanText = dictPlan.sPlanText || dictPlan.sText || "";
        }
        try {
            var dictResult = await VaibifyApi.fdictPost(
                _fsRoute("/" + _dictState.sActiveCampaignId + "/accept-plan"),
                {sPlanText: sPlanText});
            _fnReportPlanSaved(dictResult);
            await _fnReloadActiveCampaign();
        } catch (error) {
            VaibifyApp.fnShowToast(
                "Accept failed: " + (error.message || String(error)),
                "error");
        }
    }

    function _fnReportPlanSaved(dictResult) {
        /* Name the execution host where the plan landed (section 21): in
           a remote session "saved on this machine" would be a lie. */
        var sWhere = (VaibifyApp.fbIsRemoteSession()
            && !VaibifyApp.fbExecutionHostIsTheEnvironment())
            ? " on " + (VaibifyApp.fsGetExecutionHostname() || "the hub")
            : "";
        VaibifyApp.fnShowToast(
            "Plan saved" + sWhere + ": " +
            (dictResult.sLocalPlanPath || ""), "success");
    }

    async function _fnPostAction(sPath, dictBody) {
        try {
            if (dictBody === undefined) {
                await VaibifyApi.fdictPostRaw(_fsRoute(sPath));
            } else {
                await VaibifyApi.fdictPost(_fsRoute(sPath), dictBody);
            }
        } catch (error) {
            VaibifyApp.fnShowToast(
                "Action failed: " + (error.message || String(error)),
                "error");
            return;
        }
        await _fnReloadActiveCampaign();
        _fnStartPolling();
    }

    function _fnCopyBrief(dictCampaign) {
        var dictPlan = dictCampaign.dictCandidatePlan || {};
        var sBrief = dictPlan.sImplementationBrief
            || dictPlan.sPlanText || dictPlan.sText || "";
        if (navigator.clipboard && sBrief) {
            navigator.clipboard.writeText(sBrief);
            VaibifyApp.fnShowToast("Implementation brief copied.", "info");
        }
    }

    function _fnDownloadPlan(dictCampaign) {
        /* Downloads land on the computer the browser runs on, which in a
           remote session is NOT the execution host — say so (section
           21). */
        var dictPlan = dictCampaign.dictCandidatePlan || {};
        var sText = dictPlan.sPlanText || dictPlan.sText || "";
        var elLink = document.createElement("a");
        elLink.href = "data:text/plain;charset=utf-8," +
            encodeURIComponent(sText);
        elLink.download = "council-plan.txt";
        elLink.click();
        if (VaibifyApp.fbIsRemoteSession()) {
            VaibifyApp.fnShowToast(
                "Downloaded to the computer you are sitting at, not " +
                (VaibifyApp.fsGetExecutionHostname() || "the hub") + ".",
                "info");
        }
    }

    /* ------------------------------------------------------------------ */
    /* Small helpers                                                      */
    /* ------------------------------------------------------------------ */

    function _fsRoute(sSuffix) {
        return "/api/agent-councils/" +
            encodeURIComponent(_dictState.sContainerId) + sSuffix;
    }

    function _fbActiveCampaignIsLive() {
        var dictCampaign = _dictState.dictCampaign;
        return Boolean(dictCampaign && SET_LIVE_STATES[dictCampaign.sState]);
    }

    function _fbActiveCampaignNeedsHuman() {
        var dictCampaign = _dictState.dictCampaign;
        return Boolean(dictCampaign && dictCampaign.sState === "needsHuman");
    }

    function _fbActiveCampaignPlanReady() {
        var dictCampaign = _dictState.dictCampaign;
        return Boolean(dictCampaign && dictCampaign.sState === "planReady");
    }

    function _fnShowModal() {
        var elModal = document.getElementById("agentCouncilModal");
        if (elModal) elModal.style.display = "flex";
    }

    function _fnHideModal() {
        var elModal = document.getElementById("agentCouncilModal");
        if (elModal) elModal.style.display = "none";
    }

    function _fnShowWorkspaceModalClose() {
        _fnHideWorkspace();
    }

    function _fnBindElement(sId, fnHandler) {
        var elElement = document.getElementById(sId);
        if (elElement) elElement.addEventListener("click", fnHandler);
    }

    function _fsReadValue(sId) {
        var elElement = document.getElementById(sId);
        return elElement ? (elElement.value || "").trim() : "";
    }

    function _fsEscape(sText) {
        var elDiv = document.createElement("div");
        elDiv.textContent = sText === undefined || sText === null
            ? "" : String(sText);
        return elDiv.innerHTML;
    }

    function fnInitialize() {
        _fnBindElement("btnAgentCouncil", fnHandleToolbarClick);
        _fnBindElement("btnAgentCouncilModalClose", _fnHideModal);
        _fnBindElement(
            "btnAgentCouncilWorkspaceClose", _fnShowWorkspaceModalClose);
        _fnRenderToolbarButton();
    }

    document.addEventListener("DOMContentLoaded", fnInitialize);

    return {
        fnInitialize: fnInitialize,
        fnActivate: fnActivate,
        fnTeardown: fnTeardown,
        fnHandleToolbarClick: fnHandleToolbarClick,
        fnRefreshCapabilities: fnRefreshCapabilities,
    };
})();
