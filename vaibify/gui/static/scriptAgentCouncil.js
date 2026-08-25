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
        /* Poll health. The panel renders backend truth, so when the
           poll itself is the thing that is broken the panel must say
           so rather than keep displaying its last good answer. */
        iConsecutivePollFailures: 0,
        sLastPollError: "",
        sLastEventPollError: "",
        sLastRenderError: "",
        iLastPollSucceededAt: 0,
        /* Per-directory snapshot feasibility, keyed by directory
           basename, filled on demand when the convene form opens. The
           empty string keys the project's own resolved repository — the
           common single-directory case, whose answer the capabilities
           poll already carried. Mutated in place, never reassigned
           (the IIFE state trap). */
        dictFeasibilityByDirectory: {},
        setExcludedPaths: new Set(),
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
        _fnResetSnapshotScope();
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
        elButton.classList.toggle("council-blocked", dictState.bBlocked);
    }

    function _fdictToolbarState() {
        if (!_dictState.sContainerId) {
            /* No container id means fnActivate has not run: it fires
               from _fnActivateWorkflow, so the toolbar can be visible
               with nothing behind it. Explainable rather than disabled
               — a disabled button swallows its own click and says
               nothing, which was the reported defect.

               Note this is NOT "you must open a workflow": a Blank
               Project convenes against its tracked directory, and the
               backend resolves that without one. This branch is only
               the window before the panel has a container at all. */
            return _fdictUnavailableButExplainable(
                "Open this project to convene a council.");
        }
        var dictCapabilities = _dictState.dictCapabilities;
        if (!dictCapabilities) {
            return _fdictDisabled("Checking council availability…");
        }
        /* Unavailable is CLICKABLE, not disabled. A disabled button
           swallows its own click, so fnHandleToolbarClick below -- which
           exists precisely to explain the refusal -- could never run, and
           the researcher got a button that did nothing at all. The
           hover title still carries the reason for anyone who finds it;
           the click is for everyone who does not. Kept disabled only
           for the two states where there is nothing to say yet: no
           project open, and capabilities still loading. */
        if (!dictCapabilities.bAvailable) {
            return _fdictUnavailableButExplainable(
                _fsUnavailableExplanation(dictCapabilities));
        }
        if (_fiSupportedParticipantCount(dictCapabilities) < 1) {
            return _fdictUnavailableButExplainable(
                "No provider with a reviewed council adapter is " +
                "available on this project — a council needs at least " +
                "two supported participants, drawn from two distinct " +
                "models of an available provider.");
        }
        return _fdictEnabledState();
    }

    function _fdictDisabled(sTitle) {
        return {
            bDisabled: true, sTitle: sTitle,
            bAttention: false, bRunning: false, bBlocked: false,
        };
    }

    function _fdictUnavailableButExplainable(sTitle) {
        /* Dimmed so it still reads as "not usable right now", but live
           so the click can say WHY and what to do about it. */
        return {
            bDisabled: false, sTitle: sTitle,
            bAttention: false, bRunning: false, bBlocked: true,
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
        /* The one refusal that is a SHUT GATE rather than a wrong
           project, so it is the one that earns instructions. The
           backend's reason says which way it is shut (no record, a key
           missing, an image that does not match); this adds where to
           act, which the backend deliberately does not hardcode. */
        /* The repository is too big to snapshot. Its own branch because
           the fix is to the PROJECT, not to this machine — pointing the
           researcher at the credential ceremony here would send them to
           do something entirely unrelated. The backend's reason already
           names the counts and the bounds, so this only adds the
           practical way out. */
        /* The project has not said which directory it is about. The
           backend's reason already names the candidates and the action,
           so this branch exists to keep it OUT of the size branch
           below: one asks you to shrink a repository, the other to name
           one, and offering the wrong advice is worse than none. */
        if (dictCapabilities.sUnavailableIn === "no-dominant-directory") {
            return dictCapabilities.sReason;
        }
        if (dictCapabilities.sUnavailableIn === "snapshot-too-large") {
            return (dictCapabilities.sReason || "") +
                " Convene from a smaller repository, or move the bulk " +
                "output out of this one — a council reasons about your " +
                "code and inputs, not your generated results.";
        }
        if (dictCapabilities.sUnavailableIn === "credential-evidence") {
            return (dictCapabilities.sReason || "") +
                " To open it: run the live credential check on a paid " +
                "account, then record the result at " +
                "~/.vaibify/agentCouncils/credentialEvidence.json. The " +
                "record must name this project's own image by its " +
                "sha256 id — a tag is refused.";
        }
        return dictCapabilities.sReason
            || "Convening a council is unavailable on this project.";
    }

    function _fiSupportedParticipantCount(dictCapabilities) {
        /* Providers that are actually AVAILABLE (remediation R7): one
           available provider supports a full council, because the
           two-distinct-models quorum draws on its model list — the
           backend validator is the authority on that rule. */
        return (dictCapabilities.listProviders || []).filter(
            function (dictProvider) {
                return dictProvider.bAvailable !== false;
            }).length;
    }

    function fnHandleToolbarClick() {
        /* Every early return below MUST say something. A click that
           returns in silence is the defect this handler exists to
           prevent, and it has now been reported twice. */
        if (!_dictState.sContainerId) {
            VaibifyApp.fnShowToast(_fdictToolbarState().sTitle, "warning");
            return;
        }
        var dictCapabilities = _dictState.dictCapabilities;
        if (!dictCapabilities || !dictCapabilities.bAvailable) {
            /* "warning", not "info": an info toast self-destructs after
               four seconds, which is not long enough to read a refusal
               and act on it. A warning stays until dismissed. */
            VaibifyApp.fnShowToast(
                _fsUnavailableExplanation(dictCapabilities || {}),
                "warning");
            return;
        }
        /* Available but unusable: the toolbar makes this case clickable
           too, so the handler has to answer it. Falling through here
           would open the convene form with nobody to convene. */
        if (_fiSupportedParticipantCount(dictCapabilities) < 1) {
            VaibifyApp.fnShowToast(
                _fdictToolbarState().sTitle, "warning");
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
        _fnResetSnapshotScope();
        elBody.innerHTML = _fsPlanningFormMarkup();
        _fnBindPlanningForm();
        _fnWeighCandidateDirectories();
    }

    function _fnResetSnapshotScope() {
        /* Cleared in place, never reassigned: the Set is held by the
           render context (the IIFE state trap in AGENTS.md). The
           project's own resolved repository is seeded from the
           capabilities poll, which already weighed it — a second
           request for the same answer would only be slower. */
        Object.keys(_dictState.dictFeasibilityByDirectory).forEach(
            function (sKey) {
                delete _dictState.dictFeasibilityByDirectory[sKey];
            });
        _dictState.setExcludedPaths.clear();
        var dictOwn = (_dictState.dictCapabilities || {})
            .dictSnapshotFeasibility;
        if (dictOwn) {
            _dictState.dictFeasibilityByDirectory[""] = dictOwn;
            _fnExcludeEveryOversizedFile(dictOwn);
        }
    }

    function _fnExcludeEveryOversizedFile(dictFeasibility) {
        /* Ticked BY DEFAULT. The researcher opened the form to ask a
           question, not to adjudicate file sizes; the default that
           lets them proceed is the one that excludes, and the list
           stays visible so the choice is never silent. */
        (dictFeasibility.listOversizedFiles || []).forEach(
            function (dictFile) {
                _dictState.setExcludedPaths.add(dictFile.sPath);
            });
    }

    async function _fnWeighCandidateDirectories() {
        /* One request per candidate, in parallel, only when the form is
           open. The capabilities poll deliberately does NOT do this: a
           toolkit container tracks many repositories and weighing all
           of them every few seconds would spend a metadata walk each to
           answer a question nobody asked. */
        var listCandidates = (_dictState.dictCapabilities || {})
            .listCandidateDirectories || [];
        if (listCandidates.length < 2) {
            _fnRenderSnapshotScope();
            return;
        }
        await Promise.all(listCandidates.map(async function (sName) {
            try {
                _dictState.dictFeasibilityByDirectory[sName] =
                    await VaibifyApi.fdictGet(
                        _fsRoute("/snapshot-feasibility?sProjectDirectory=" +
                            encodeURIComponent(sName)));
            } catch (error) {
                /* An unweighable directory is UNKNOWN, never "fine".
                   The capture enforces the bounds regardless, so the
                   honest render is a question mark rather than a tick
                   the researcher would read as a promise. */
                _dictState.dictFeasibilityByDirectory[sName] = null;
            }
        }));
        _fnRenderDirectoryOptions();
        _fnAdoptChosenDirectoryScope();
    }

    function _fnAdoptChosenDirectoryScope() {
        /* The exclusions belong to the CHOSEN directory, so switching
           directories discards them: a path ticked for one repository
           means nothing in another, and carrying it over would send an
           exclusion the capture silently ignores. */
        _dictState.setExcludedPaths.clear();
        var dictFeasibility = _fdictChosenFeasibility();
        if (dictFeasibility) _fnExcludeEveryOversizedFile(dictFeasibility);
        _fnRenderSnapshotScope();
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

    function _fsDirectoryChoiceMarkup() {
        /* Only when the project tracks SEVERAL directories and no
           workflow pins one. A toolkit container tracks many by
           design, so this asks rather than demanding the researcher
           untrack the rest. Absent entirely in the common case, so the
           form does not grow a control that answers nothing. */
        var listCandidates = (_dictState.dictCapabilities || {})
            .listCandidateDirectories || [];
        if (listCandidates.length < 2) return "";
        return "<label class=\"council-field\">Directory this council " +
            "is about" +
            "<select id=\"councilDirectory\">" +
            _fsDirectoryOptionsMarkup(listCandidates) +
            "</select></label>";
    }

    function _fsDirectoryOptionsMarkup(listCandidates) {
        /* Each option carries its own verdict, because the choice IS
           the moment the researcher can act on it: a directory whose
           snapshot would refuse outright is marked, and one that only
           needs oversized files excluded is marked differently. An
           unweighed or unweighable directory says so rather than
           looking clean. */
        return listCandidates.map(function (sName) {
            var sSafe = VaibifyUtilities.fnEscapeHtml(sName);
            return "<option value=\"" + sSafe + "\">" + sSafe +
                _fsFeasibilityGlyph(sName) + "</option>";
        }).join("");
    }

    function _fsFeasibilityGlyph(sName) {
        if (!(sName in _dictState.dictFeasibilityByDirectory)) return " …";
        var dictFeasibility = _dictState.dictFeasibilityByDirectory[sName];
        if (!dictFeasibility) return " (could not be weighed)";
        if (dictFeasibility.bFits) return "";
        if (dictFeasibility.bResolvableByExcludingFiles) {
            return " ⚠ (" +
                (dictFeasibility.listOversizedFiles || []).length +
                " oversized file(s))";
        }
        return " ⛔ (too large to snapshot)";
    }

    function _fnRenderDirectoryOptions() {
        var elSelect = document.getElementById("councilDirectory");
        if (!elSelect) return;
        var sChosen = elSelect.value;
        var listCandidates = (_dictState.dictCapabilities || {})
            .listCandidateDirectories || [];
        elSelect.innerHTML = _fsDirectoryOptionsMarkup(listCandidates);
        elSelect.value = sChosen;
    }

    function _fdictChosenFeasibility() {
        var elSelect = document.getElementById("councilDirectory");
        var sChosen = elSelect ? elSelect.value : "";
        return _dictState.dictFeasibilityByDirectory[sChosen] || null;
    }

    function _fnRenderSnapshotScope() {
        /* The offending files, named, each with the tick that leaves it
           out. Rendered into the form rather than raised as an error,
           because it is a decision the researcher is making, not a
           failure they are being told about. */
        var elScope = document.getElementById("councilSnapshotScope");
        if (!elScope) return;
        var dictFeasibility = _fdictChosenFeasibility();
        var listOversized = dictFeasibility
            ? (dictFeasibility.listOversizedFiles || []) : [];
        if (!listOversized.length) {
            elScope.innerHTML = "";
            return;
        }
        elScope.innerHTML =
            "<fieldset class=\"council-snapshot-scope\">" +
            "<legend>⚠ Files too large for a snapshot</legend>" +
            "<p class=\"council-hint\">A council ships a copy of the " +
            "repository to every participant, and no single file in it " +
            "may exceed " +
            _fsMegabytes(dictFeasibility.iMaxSnapshotMemberBytes) +
            " on this machine. Leave these out to convene; the " +
            "participants are told by name which files they were not " +
            "shown.</p>" +
            listOversized.map(_fsOversizedFileRow).join("") +
            (dictFeasibility.bOversizedListTruncated
                ? "<p class=\"council-hint\">Only the largest " +
                  listOversized.length + " are listed; there may be " +
                  "more.</p>"
                : "") +
            "</fieldset>";
        _fnBindOversizedCheckboxes();
    }

    function _fsOversizedFileRow(dictFile, iIndex) {
        var sSafePath = VaibifyUtilities.fnEscapeHtml(dictFile.sPath);
        return "<label class=\"council-oversized-file\">" +
            "<input type=\"checkbox\" data-oversized-index=\"" + iIndex +
            "\" data-oversized-path=\"" + sSafePath + "\"" +
            (_dictState.setExcludedPaths.has(dictFile.sPath)
                ? " checked" : "") +
            "> Leave out <code>" + sSafePath + "</code> (" +
            _fsMegabytes(dictFile.iSizeBytes) + ")</label>";
    }

    function _fsMegabytes(iBytes) {
        return Math.floor((iBytes || 0) / (1024 * 1024)) + " MB";
    }

    function _fnBindOversizedCheckboxes() {
        var listBoxes = document.querySelectorAll("[data-oversized-path]");
        Array.prototype.forEach.call(listBoxes, function (elBox) {
            elBox.addEventListener("change", function () {
                var sPath = elBox.getAttribute("data-oversized-path");
                if (elBox.checked) {
                    _dictState.setExcludedPaths.add(sPath);
                } else {
                    _dictState.setExcludedPaths.delete(sPath);
                }
            });
        });
    }

    function _fsPlanningFormMarkup() {
        return "<h2>Plan a change</h2>" +
            _fsDirectoryChoiceMarkup() +
            "<div id=\"councilSnapshotScope\"></div>" +
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
            "<div id=\"councilConveneStatus\" " +
            "class=\"council-convening\" role=\"status\" " +
            "aria-live=\"polite\"></div>" +
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
        _fnBindElement("btnCouncilCancel", _fnHideModalConfirmingLoss);
        var elDirectory = document.getElementById("councilDirectory");
        if (elDirectory) {
            elDirectory.addEventListener(
                "change", _fnAdoptChosenDirectoryScope);
        }
        _fnRenderSnapshotScope();
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
            "<span class=\"council-participant-title\">Agent " +
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
        /* From the capabilities endpoint's discovery payload, never a
           hardcoded table in this source (section 8.2). The payload
           says where the list came from, and the picker SHOWS that:
           an un-verified alias set is labelled as one, so a researcher
           picking an entry knows nothing enumerated it. Free text
           remains only when a provider offers no list at all. */
        var dictDiscovery = _fdictProviderDiscovery(
            dictParticipant.sProvider);
        var listModels = dictDiscovery.listModelIds || [];
        if (listModels.length) {
            var sOptions = listModels.map(function (sModel) {
                return "<option value=\"" + _fsEscape(sModel) + "\"" +
                    (sModel === dictParticipant.sRequestedModel
                        ? " selected" : "") + ">" +
                    _fsEscape(sModel) + "</option>";
            }).join("");
            return "<select class=\"council-model\" data-index=\"" +
                iIndex + "\"><option value=\"\">Choose a model…" +
                "</option>" + sOptions + "</select>" +
                _fsDiscoveryProvenance(dictDiscovery);
        }
        return "<input type=\"text\" class=\"council-model\" " +
            "data-index=\"" + iIndex + "\" placeholder=\"Model id\" " +
            "value=\"" + _fsEscape(dictParticipant.sRequestedModel) + "\">";
    }

    function _fdictProviderDiscovery(sProvider) {
        /* The nested shape the backend actually sends. This read used
           to be dictProvider.listModels — a key no payload ever
           carried — so the picker silently fell through to free text
           while the discovery result rode over the wire unread. */
        var dictCapabilities = _dictState.dictCapabilities || {};
        var listProviders = dictCapabilities.listProviders || [];
        var dictMatch = listProviders.filter(function (dictProvider) {
            return dictProvider.sProvider === sProvider;
        })[0];
        return (dictMatch && dictMatch.dictModelDiscovery) || {};
    }

    function _fsDiscoveryProvenance(dictDiscovery) {
        /* Where the list came from, in the researcher's words. A
           labelled un-verified alias set is honest; an unlabelled one
           would read as a discovered list, which is the claim the
           design amendment exists to avoid making. */
        if (dictDiscovery.bVerified) {
            return "<span class=\"council-model-source\">live from the " +
                "provider API</span>";
        }
        return "<span class=\"council-model-source council-unverified\">" +
            "un-verified aliases — the subscription backend cannot " +
            "enumerate models without spending a paid turn</span>";
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
                    ">Agent " + (iIndex + 1) + " (" +
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
            dictSettings: _fdictReadSettingsForm(),
            sProjectDirectory: _fsReadValue("councilDirectory"),
            listExcludedPaths: Array.from(_dictState.setExcludedPaths),
        };
        /* Convening is a SINGLE request that does a great deal before
           it answers: resolve the image, check the credential gate and
           the login, capture the repository snapshot, build one runner
           per participant, copy the snapshot into each, provision
           egress, and spawn the drive task. On a real project that is
           5-10 seconds with two participants and longer with more —
           and until now the form simply sat there, which reads as a
           click that missed (live report, 2026-08-24). */
        var fnFinishBusy = _ffnEnterConveningState(dictBody);
        try {
            var dictResult = await VaibifyApi.fdictPost(
                _fsRoute("/start"), dictBody);
            fnFinishBusy();
            _fnAdoptCampaign(dictResult.sCampaignId, dictResult.dictCampaign);
            _fnHideModal();
            _fnShowWorkspace();
        } catch (error) {
            fnFinishBusy();
            if (elError) {
                elError.textContent = "Could not convene: " +
                    (error.message || String(error));
            }
        }
    }

    function _ffnEnterConveningState(dictBody) {
        /* Returns the undo. A busy state that cannot be left is worse
           than none: a refused convene would strand the form disabled
           with the researcher's question inside it.

           The status text does NOT narrate server-side stages. The
           convene is one HTTP request and the browser is told nothing
           until it answers, so a "Capturing snapshot…  Building
           runners…" sequence driven by a timer would be inventing
           progress it cannot see. What it shows instead is true: the
           work being waited on, the participant count that scales it,
           and a running clock proving the page is alive. */
        var elConvene = document.getElementById("btnCouncilConvene");
        var elStatus = document.getElementById("councilConveneStatus");
        var iParticipants = (dictBody.listParticipants || []).length;
        var iStartedAt = Date.now();
        if (elConvene) {
            elConvene.disabled = true;
            elConvene.textContent = "Convening…";
        }
        function _fnTick() {
            if (!elStatus) return;
            var iSeconds = Math.round((Date.now() - iStartedAt) / 1000);
            elStatus.textContent =
                "Convening a council of " + iParticipants +
                ": copying the repository snapshot into " + iParticipants +
                " disposable container" + (iParticipants === 1 ? "" : "s") +
                " and starting the first turn — " + iSeconds + "s";
        }
        _fnTick();
        var iTimer = window.setInterval(_fnTick, 1000);
        return function _fnLeaveConveningState() {
            window.clearInterval(iTimer);
            if (elStatus) elStatus.textContent = "";
            if (elConvene) {
                elConvene.disabled = false;
                elConvene.textContent = "Convene council";
            }
        };
    }

    function _fdictReadSettingsForm() {
        /* The convene request SENDS the settings the form renders
           (remediation R6) — a form whose values never left the
           browser was configuration theatre. The backend's bounded
           validator stays the authority; this only collects. */
        var elAnonymity = document.getElementById("councilPeerAnonymity");
        var iMinimumRounds = parseInt(
            _fsReadValue("councilMinimumRounds") || "1", 10);
        return {
            bPeerAnonymity: elAnonymity
                ? !!elAnonymity.checked
                : DICT_DEFAULT_SETTINGS.bPeerAnonymity,
            sEffortPerParticipant: _fsReadValue("councilEffort")
                || DICT_DEFAULT_SETTINGS.sEffortPerParticipant,
            sExecutionPermission: _fsReadValue("councilExecution")
                || DICT_DEFAULT_SETTINGS.sExecutionPermission,
            iMinimumRounds: iMinimumRounds > 0 ? iMinimumRounds : 1,
        };
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
            /* Reschedule rather than return. This used to fall out of
               the loop entirely, so ONE tick arriving while the panel
               was momentarily not visible stopped polling for the rest
               of the session — and a stopped poll is indistinguishable
               from a council doing nothing. _fnStopPolling is the only
               thing that should end the loop. */
            _fnScheduleNextPoll(_fiPollInterval());
            return;
        }
        _dictState.bPollInFlight = true;
        try {
            /* INDEPENDENTLY. These were sequential awaits in one try,
               so a failing events poll meant the campaign refresh never
               ran at all — the panel kept rendering convene-time state
               for an entire deliberation while requests fired every
               few seconds, all of them events, none of them the
               campaign (live evidence, 2026-08-24).

               The campaign record is the panel's ground truth; the
               event ring is a console nicety. The nicety must never be
               able to starve the truth, so its failure is recorded and
               stepped over rather than aborting the tick. */
            try {
                await _fnPollEventsOnce();
            } catch (errorEvents) {
                _dictState.sLastEventPollError =
                    errorEvents.message || String(errorEvents);
            }
            await _fnLoadCampaignQuietly();
            _dictState.iConsecutivePollFailures = 0;
            _dictState.sLastPollError = "";
            _dictState.iLastPollSucceededAt = Date.now();
        } catch (error) {
            /* A transient poll error is not ground truth and the next
               tick retries — but it must not be INVISIBLE. This was
               `void error`, so a poll failing every three seconds
               looked exactly like a council making no progress, and a
               researcher watched a frozen panel with no way to tell
               which (live report, 2026-08-24). */
            _dictState.iConsecutivePollFailures += 1;
            _dictState.sLastPollError = error.message || String(error);
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
                _dictState.iHighestSequenceSeen) +
            _fsDirectoryQuery("&"));
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
            _fsRoute("/" + _dictState.sActiveCampaignId) +
            _fsDirectoryQuery("?"));
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
        /* The SIGNATURE is inside the guard too, not just the render.
           It walks the same campaign payload, so a shape that breaks
           one breaks the other — and a throw here escapes the events
           poll, which (before the tick's two refreshes were separated)
           also killed the campaign refresh. One bad payload froze the
           entire panel with nothing on the console. */
        try {
            if (_fsWorkspaceSignature() !== _dictState.sRenderSignature) {
                _fnRenderWorkspace();
            }
        } catch (errorRender) {
            _dictState.sLastRenderError =
                errorRender.message || String(errorRender);
            _fnShowRenderFault();
        }
    }

    function _fsTurnProgressSignature(dictCampaign) {
        /* One token per turn: round, phase, and settled status. Counts
           alone would miss a turn moving from in-flight to failed, and
           the whole payload would redraw the panel on every poll. */
        return (dictCampaign.listRounds || []).map(function (dictRound) {
            var dictByPhase = dictRound.dictTurnsByPhase || {};
            return Object.keys(dictByPhase).sort().map(function (sPhase) {
                return sPhase + ":" + dictByPhase[sPhase].map(
                    function (dictTurn) {
                        return (dictTurn.sParticipantId || "").slice(-4) +
                            "=" + (dictTurn.sStatus || "");
                    }).join(",");
            }).join(";");
        }).join("/");
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
            /* TURN progress. Without it a settled turn changes
               listRounds and nothing the signature reads, so the panel
               does not redraw and a participant that finished minutes
               ago still shows as deliberating — the live report this
               whole block exists to answer (2026-08-24). */
            _fsTurnProgressSignature(dictCampaign),
            /* So the stale banner can APPEAR. Without it the only
               thing that changes when polling breaks is the failure
               count, and the panel would keep showing its last good
               answer forever — which is the state being warned about. */
            _dictState.iConsecutivePollFailures > 0 ? "stale" : "fresh",
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

    /* The researcher's chosen panel height, in pixels. Persisted so it
       survives a reload: a height re-chosen on every visit is barely
       better than one that cannot be chosen at all. */
    var S_HEIGHT_STORAGE_KEY = "vaibifyCouncilWorkspaceHeight";
    var I_MIN_WORKSPACE_HEIGHT = 120;

    function _fnShowWorkspace() {
        var elWorkspace = document.getElementById("agentCouncilWorkspace");
        if (!elWorkspace) return;
        elWorkspace.classList.remove("council-collapsed");
        elWorkspace.style.display = "";
        _fnApplyStoredWorkspaceHeight(elWorkspace);
        document.body.classList.add("council-workspace-open");
        _fnBindWorkspaceResize(elWorkspace);
        _fnRenderWorkspace();
        _fnStartPolling();
    }

    function _fnHideWorkspace() {
        var elWorkspace = document.getElementById("agentCouncilWorkspace");
        if (elWorkspace) elWorkspace.style.display = "none";
        /* The layout's reserved space goes with it. Leaving the class on
           would strand a band of padding under a panel that is gone. */
        document.body.classList.remove("council-workspace-open");
        _fnStopPolling();
    }

    function _fnApplyStoredWorkspaceHeight(elWorkspace) {
        var iStored = parseInt(
            window.localStorage.getItem(S_HEIGHT_STORAGE_KEY) || "", 10);
        if (!isNaN(iStored) && iStored >= I_MIN_WORKSPACE_HEIGHT) {
            _fnSetWorkspaceHeight(elWorkspace, iStored);
        } else {
            /* No stored choice: publish the CSS default as a concrete
               pixel value so the layout reserves the SAME space the
               panel occupies. A percentage here and a percentage there
               drift as the window resizes. */
            _fnPublishWorkspaceHeight(elWorkspace.offsetHeight);
        }
    }

    function _fnSetWorkspaceHeight(elWorkspace, iHeightPixels) {
        var iMaximum = Math.round(window.innerHeight * 0.85);
        var iClamped = Math.max(
            I_MIN_WORKSPACE_HEIGHT, Math.min(iHeightPixels, iMaximum));
        elWorkspace.style.height = iClamped + "px";
        _fnPublishWorkspaceHeight(iClamped);
        return iClamped;
    }

    function _fnPublishWorkspaceHeight(iHeightPixels) {
        document.documentElement.style.setProperty(
            "--council-workspace-height", iHeightPixels + "px");
    }

    function _fnBindWorkspaceResize(elWorkspace) {
        var elHandle = document.getElementById("councilResizeHandle");
        /* Bound once. _fnShowWorkspace runs on every workspace entry,
           and a listener added per call would apply the drag delta once
           per past visit. */
        if (!elHandle || elHandle.dataset.bBound === "true") return;
        elHandle.dataset.bBound = "true";
        var iStartY = 0;
        var iStartHeight = 0;

        function _fnOnPointerMove(eventMove) {
            /* Dragging the TOP edge upward grows the panel, so the
               delta is inverted against the pointer's Y. */
            _fnSetWorkspaceHeight(
                elWorkspace, iStartHeight + (iStartY - eventMove.clientY));
        }

        function _fnOnPointerUp() {
            document.removeEventListener("pointermove", _fnOnPointerMove);
            document.removeEventListener("pointerup", _fnOnPointerUp);
            document.body.style.userSelect = "";
            window.localStorage.setItem(
                S_HEIGHT_STORAGE_KEY, String(elWorkspace.offsetHeight));
        }

        elHandle.addEventListener("pointerdown", function (eventDown) {
            eventDown.preventDefault();
            iStartY = eventDown.clientY;
            iStartHeight = elWorkspace.offsetHeight;
            /* Without this a drag selects the page text it passes over,
               which makes the resize feel broken even when it works. */
            document.body.style.userSelect = "none";
            document.addEventListener("pointermove", _fnOnPointerMove);
            document.addEventListener("pointerup", _fnOnPointerUp);
        });
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
                    "Agent " + (iIndex + 1) + "</button>";
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
            _fsPollHealth() +
            "<p>" + _fsEscape(_fsStateSentence(dictCampaign.sState)) +
            "</p>" +
            _fsQuarantineWarning(dictCampaign) +
            _fsBaselineWarning(dictCampaign) +
            _fsVerdictBanner(dictCampaign) +
            _fsParticipantStates(dictCampaign) +
            _fsRoundProgress(dictCampaign) +
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

    var DICT_PHASE_LABELS = {
        independentProposals: "independent proposals",
        crossReview: "adversarial cross-review",
        synthesis: "synthesis",
        veto: "veto",
    };

    function _fnShowRenderFault() {
        /* Written straight to the DOM, deliberately NOT through the
           renderer that just failed. */
        var elBody = document.getElementById("agentCouncilWorkspaceBody");
        if (!elBody) return;
        elBody.innerHTML = "<div class=\"council-stale\">⚠ This panel " +
            "could not draw the council's current state (" +
            _fsEscape(_dictState.sLastRenderError) + "). The council " +
            "itself is unaffected — this is a dashboard fault. Reload " +
            "the page; if it recurs, the message above is the bug " +
            "report.</div>";
    }

    /* What each campaign state MEANS, in the researcher's terms. The
       raw state name is the protocol's vocabulary, not theirs:
       "Phase: needsHuman" told a researcher nothing about the fact
       that five questions were waiting for them (2026-08-24). The raw
       name is kept in the title attribute for anyone debugging. */
    var DICT_STATE_SENTENCES = {
        draft: "Not yet convened.",
        planning: "The agents are deliberating.",
        needsHuman: "The council needs your opinion — see the questions "
            + "below.",
        planReady: "A plan is ready for your review.",
        planAccepted: "You accepted this plan.",
        awaitingImplementation: "Accepted, awaiting implementation.",
        failed: "This council stopped without producing a plan.",
        interrupted: "This council was interrupted before it finished.",
        archived: "Archived.",
    };

    function _fsStateSentence(sState) {
        return DICT_STATE_SENTENCES[sState] || ("Phase: " + sState);
    }

    function _fsPollHealth() {
        /* Silence about a broken poll is the same defect as an
           optimistic status: both leave the researcher reading a panel
           that no longer reflects the backend. */
        if (!_dictState.iConsecutivePollFailures) {
            if (!_dictState.iLastPollSucceededAt) return "";
            var iAgo = Math.round(
                (Date.now() - _dictState.iLastPollSucceededAt) / 1000);
            return "<p class=\"council-hint\">Updated " +
                (iAgo < 5 ? "just now" : iAgo + "s ago") + ".</p>";
        }
        return "<div class=\"council-stale\">⚠ This panel is NOT " +
            "updating. " + _dictState.iConsecutivePollFailures +
            " consecutive refresh attempts failed" +
            (_dictState.sLastPollError
                ? " (" + _fsEscape(_dictState.sLastPollError) + ")" : "") +
            ". What you see below is the last answer the server gave, " +
            "not its current state.</div>";
    }

    function _fsRoundProgress(dictCampaign) {
        /* Per-TURN truth, which the panel showed nowhere. The
           participant chips above report the RUNNER lifecycle and the
           campaign state, so a participant that had finished a 20,000
           token proposal still read "deliberating" and a researcher
           watching saw no sign anything had happened for minutes
           (live report, 2026-08-24).

           Rendered from listRounds — already in the payload, and
           previously referenced nowhere in this file. */
        var listRounds = dictCampaign.listRounds || [];
        if (!listRounds.length) {
            return "<p class=\"council-hint\">No turn has settled yet. " +
                "The first proposals are being written; each is a full " +
                "model turn, so this takes minutes rather than " +
                "seconds.</p>";
        }
        var dictModelById = {};
        (dictCampaign.listParticipants || []).forEach(
            function (dictParticipant) {
                dictModelById[dictParticipant.sParticipantId] =
                    dictParticipant.sRequestedModel ||
                    dictParticipant.sProvider;
            });
        return "<div class=\"council-rounds\">" +
            listRounds.map(function (dictRound) {
                return _fsOneRound(dictRound, dictModelById);
            }).join("") + "</div>";
    }

    function _fsOneRound(dictRound, dictModelById) {
        var dictByPhase = dictRound.dictTurnsByPhase || {};
        var sBody = Object.keys(dictByPhase).filter(
            function (sPhase) { return dictByPhase[sPhase].length; }
        ).map(function (sPhase) {
            return "<li>" +
                _fsEscape(DICT_PHASE_LABELS[sPhase] || sPhase) + ": " +
                dictByPhase[sPhase].map(function (dictTurn) {
                    return _fsOneTurn(dictTurn, dictModelById);
                }).join(" ") + "</li>";
        }).join("");
        return "<div class=\"council-round\">" +
            "<strong>Round " + _fsEscape(String(dictRound.iRoundNumber)) +
            "</strong><ul>" + (sBody ||
                "<li class=\"council-hint\">turns in flight</li>") +
            "</ul></div>";
    }

    function _fsOneTurn(dictTurn, dictModelById) {
        var sModel = dictModelById[dictTurn.sParticipantId] || "participant";
        var dictUsage = (dictTurn.dictModelIdentity || {}).dictUsage || {};
        if (dictTurn.sStatus === "completed") {
            /* The token count is the honest proof a turn did real work.
               A "complete" with no output is the zero-token failure
               this council has already produced once. */
            var iOut = dictUsage.output_tokens;
            return "<span class=\"council-turn council-turn-done\">" +
                _fsEscape(sModel) + " ✓" +
                (typeof iOut === "number"
                    ? " (" + iOut.toLocaleString() + " tokens out)" : "") +
                "</span>";
        }
        if (dictTurn.sStatus === "failed") {
            return "<span class=\"council-turn council-turn-failed\" " +
                "title=\"" + _fsEscape(dictTurn.sFailureReason || "") +
                "\">" + _fsEscape(sModel) + " ✗ failed</span>";
        }
        return "<span class=\"council-turn\">" + _fsEscape(sModel) +
            " …</span>";
    }

    function _fsParticipantStates(dictCampaign) {
        var sRows = (dictCampaign.listParticipants || []).map(
            function (dictParticipant, iIndex) {
                var bChair = dictParticipant.sParticipantId ===
                    dictCampaign.sChairbotParticipantId;
                return "<li>Agent " + (iIndex + 1) + " — " +
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
        /* A terminal campaign STATE proves nothing about runners on its
           own (remediation R4/R10): "verified stopped" is reserved for
           a backend-reported lifecycle after the absence probe, and
           the quarantine banner covers the may-still-exist case. This
           inferred value therefore claims only "stopped". */
        if (SET_LIVE_STATES[dictCampaign.sState]) return "deliberating";
        if (SET_TERMINAL_STATES[dictCampaign.sState]) return "stopped";
        return "waiting";
    }

    function _fsLifecycleLabel(sLifecycle) {
        var dictLabels = {
            preparingSandbox: "preparing sandbox",
            deliberating: "deliberating",
            cleaningUp: "cleaning up",
            verifiedStopped: "verified stopped",
            stopped: "stopped",
            waiting: "waiting",
        };
        return dictLabels[sLifecycle] || sLifecycle;
    }

    function _fsQuarantineWarning(dictCampaign) {
        /* The R4 "runner may exist" surface: a quarantined reservation
           is a runner the daemon could not prove gone. It holds its
           admission budget and this banner until reconciliation proves
           absence — never silently absorbed into a clean stop. */
        var listQuarantined = dictCampaign.listQuarantinedRunners || [];
        if (!listQuarantined.length) return "";
        return "<p class=\"council-verdict council-verdict-" +
            "blockedForWantOfEvidence\">⚠ " + listQuarantined.length +
            " council runner(s) may still exist: the daemon could not " +
            "prove destruction, so the reservation is quarantined and " +
            "keeps holding its budget. Run <code>vaibify reconcile</code>" +
            " to prove absence.</p>";
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
                _fsEscape(dictEvent.sEventKind || "") + "</span> " +
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
        /* The protocol has no mid-deliberation message channel
           (remediation R6): the engine accepts researcher text ONLY at
           a human gate, so this surface says what a researcher can
           actually do while the council deliberates — read, or stop.
           A message box here would post a respond the backend rightly
           refuses 409, which is a control that only ever fails. */
        void dictCampaign;
        return "<div class=\"council-composer\">" +
            "<p class=\"council-hint\">The council is deliberating. It " +
            "will pause here when it needs your decision; until then " +
            "you can watch the consoles or stop after the current " +
            "turn.</p>" +
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
        /* The ENGINE'S gate shape (remediation R6): a list of
           questions, each carrying who raised it. The quorum-shortfall
           gate shares this renderer — its single server-raised
           question rides the same list. */
        var listQuestions = dictGate.listQuestions || [];
        var sQuestionRows = listQuestions.map(function (dictQuestion) {
            return "<li>" + _fsEscape(dictQuestion.sQuestionText || "") +
                " <span class=\"council-question-author\">(raised by " +
                _fsEscape(dictQuestion.sRaisedByParticipantId || "") +
                ")</span></li>";
        }).join("");
        return "<div class=\"council-needs-human\">" +
            "<h4>The council needs your opinion</h4>" +
            (sQuestionRows
                ? "<ul class=\"council-questions\">" + sQuestionRows +
                    "</ul>"
                : "<p>A material choice could not be settled from " +
                    "evidence.</p>") +
            "<textarea id=\"councilAnswer\" rows=\"3\"></textarea>" +
            "<button type=\"button\" id=\"btnCouncilAnswer\" " +
            "class=\"btn btn-primary\">Record decision</button>" +
            "</div>";
    }

    function _fsExhaustedRoundCard(dictCampaign, dictGate) {
        /* Exactly the three section 5.1 exits as distinct controls, and
           NO plain respond field that would silently relaunch the spent
           round budget (section 6.5). Each control posts the ENGINE'S
           own exit transition; the resolve/override exit requires a
           decision on EVERY unresolved objection before it submits. */
        var listObjections = dictGate.listUnresolvedObjections || [];
        var sObjectionRows = listObjections.map(function (dictObjection) {
            return "<div class=\"council-objection-row\" " +
                "data-objection-id=\"" +
                _fsEscape(dictObjection.sObjectionId || "") + "\">" +
                "<p>" + _fsEscape(dictObjection.sObjectionText || "") +
                "</p>" +
                "<select class=\"council-objection-action\">" +
                "<option value=\"\">Decide…</option>" +
                "<option value=\"resolve\">Resolve (the plan will be " +
                "amended)</option>" +
                "<option value=\"override\">Override (recorded as YOUR " +
                "decision)</option>" +
                "</select>" +
                "<input type=\"text\" class=\"council-objection-text\" " +
                "placeholder=\"How it is resolved, or why overridden\">" +
                "</div>";
        }).join("");
        return "<div class=\"council-needs-human council-exhausted\">" +
            "<h4>Rounds exhausted with objections outstanding</h4>" +
            sObjectionRows +
            "<div class=\"council-exits\">" +
            "<label>Rounds to grant <input type=\"number\" " +
            "id=\"councilGrantRounds\" min=\"1\" value=\"1\"></label>" +
            "<button type=\"button\" id=\"btnCouncilGrantRound\" " +
            "class=\"btn\">Grant a bounded resolution round</button>" +
            "<button type=\"button\" id=\"btnCouncilResolveOverride\" " +
            "class=\"btn\">Resolve or override, then a final veto</button>" +
            "<input type=\"text\" id=\"councilRejectReason\" " +
            "placeholder=\"Why the candidate is rejected (optional)\">" +
            "<button type=\"button\" id=\"btnCouncilReject\" " +
            "class=\"btn danger\">Reject and archive</button>" +
            "</div></div>";
    }

    /* ------------------------------------------------------------------ */
    /* Plan tab and accepted-plan actions (section 6.6)                   */
    /* ------------------------------------------------------------------ */

    function _fsCandidatePlanBody(dictPlan) {
        /* The engine's real candidate shape (remediation R6): the
           synthesis result lives at dictCandidatePlan.dictResult, and
           the objection provenance lists ride beside it. Never a
           top-level plan-text field a fabricated record carried — no
           record no engine ever wrote. */
        var dictResult = dictPlan.dictResult || {};
        var sParts = "<p class=\"council-plan-summary\">" +
            _fsEscape(dictResult.sSummary || "") + "</p>";
        var listItems = dictResult.listPlanItems || [];
        if (listItems.length) {
            sParts += "<h5>Plan</h5><ol>" + listItems.map(function (sItem) {
                return "<li>" + _fsEscape(String(sItem)) + "</li>";
            }).join("") + "</ol>";
        }
        /* The design section 7.1 sections the schema now asks
           participants to produce. Rendered only when populated, so a
           plan accepted under charter 1.0.0 — whose participants were
           never asked — shows what it actually has rather than empty
           headings claiming the council considered them. */
        [["listRejectedAlternatives", "Rejected alternatives, and why"],
         ["listVerificationRequirements",
          "Required verification, automated and manual"],
         ["listStopConditions",
          "Stop conditions — halt and return to the council"],
         ["listOpenQuestions", "Open questions"]]
            .forEach(function (tSection) {
                var listEntries = dictResult[tSection[0]] || [];
                if (!listEntries.length) return;
                sParts += "<h5>" + _fsEscape(tSection[1]) + "</h5><ul>" +
                    listEntries.map(function (sEntry) {
                        return "<li>" + _fsEscape(String(sEntry)) + "</li>";
                    }).join("") + "</ul>";
            });
        sParts += _fsObjectionProvenance(dictPlan);
        return sParts;
    }

    function _fsObjectionProvenance(dictPlan) {
        var sParts = "";
        [["listCouncilClearedObjections", "Objections cleared in review"],
         ["listResearcherResolvedObjections",
          "Objections resolved by the researcher"],
         ["listResearcherOverriddenObjections",
          "Objections OVERRIDDEN by the researcher"]]
            .forEach(function (tProvenance) {
                var listObjections = dictPlan[tProvenance[0]] || [];
                if (!listObjections.length) return;
                sParts += "<h5>" + tProvenance[1] + "</h5><ul>" +
                    listObjections.map(function (dictObjection) {
                        return "<li>" + _fsEscape(
                            dictObjection.sObjectionText || "") + "</li>";
                    }).join("") + "</ul>";
            });
        return sParts;
    }

    function _fsComposePlanBriefText(dictPlan) {
        /* The copy/download text, composed from the same candidate the
           backend's plan.md composer reads — display-side only; the
           backend stays the authority for the accepted artifact. */
        var dictResult = dictPlan.dictResult || {};
        var listLines = ["# Council plan", "", dictResult.sSummary || ""];
        (dictResult.listPlanItems || []).forEach(function (sItem, iIndex) {
            listLines.push((iIndex + 1) + ". " + String(sItem));
        });
        [["listRejectedAlternatives", "rejected alternative"],
         ["listVerificationRequirements", "verification required"],
         ["listStopConditions", "stop condition"],
         ["listOpenQuestions", "open question"]]
            .forEach(function (tSection) {
                (dictResult[tSection[0]] || []).forEach(function (sEntry) {
                    listLines.push(
                        "- " + tSection[1] + ": " + String(sEntry));
                });
            });
        return listLines.join("\n");
    }

    function _fsPlanTab(dictCampaign) {
        var dictPlan = dictCampaign.dictCandidatePlan;
        if (!dictPlan) {
            return "<p class=\"council-hint\">No candidate plan yet.</p>";
        }
        return "<div class=\"council-plan\">" +
            _fsVerdictBanner(dictCampaign) +
            "<div class=\"council-plan-text\">" +
            _fsCandidatePlanBody(dictPlan) + "</div>" +
            _fsPlanActions(dictCampaign) + "</div>";
    }

    function _fsPlanActions(dictCampaign) {
        /* No "request another pass" control (remediation R6): the
           engine offers exactly acceptance and rejection at planReady;
           a control posting a transition the protocol does not have
           would either fail or fabricate one. */
        if (SET_TERMINAL_STATES[dictCampaign.sState]) {
            return "<p class=\"council-plan-accepted\">This plan was " +
                "accepted. Give the saved plan and its implementation " +
                "brief to a fresh implementation agent — the council " +
                "does not implement it.</p>";
        }
        return "<div class=\"council-plan-actions\">" +
            "<button type=\"button\" id=\"btnCouncilAcceptPlan\" " +
            "class=\"btn btn-primary\">Accept and save plan</button>" +
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
        _fnBindElement("btnCouncilStop", _fnStopCouncil);
        _fnBindElement("btnCouncilAnswer", _fnAnswerQuestion);
        _fnBindElement("btnCouncilGrantRound", _fnGrantResolutionRound);
        _fnBindElement("btnCouncilResolveOverride", _fnResolveObjections);
        _fnBindElement("btnCouncilReject", _fnRejectCandidate);
        _fnBindPlanActions(dictCampaign);
    }

    function _fnBindPlanActions(dictCampaign) {
        _fnBindElement("btnCouncilAcceptPlan", _fnAcceptPlan);
        _fnBindElement("btnCouncilCopyBrief", function () {
            _fnCopyBrief(dictCampaign);
        });
        _fnBindElement("btnCouncilDownloadPlan", function () {
            _fnDownloadPlan(dictCampaign);
        });
        _fnBindElement("btnCouncilRejectPlan", _fnRejectCandidate);
    }

    /* ------------------------------------------------------------------ */
    /* Human actions — each refetches backend truth, never optimistic     */
    /* ------------------------------------------------------------------ */

    async function _fnAnswerQuestion() {
        var sAnswer = _fsReadValue("councilAnswer");
        if (!sAnswer) return;
        await _fnPostAction("/" + _dictState.sActiveCampaignId + "/respond",
            {sResponseText: sAnswer});
    }

    /* Each exhausted-round exit posts the ENGINE'S own transition
       (remediation R6) — its dedicated route, never a respond message
       the backend would have to parse back into an intent. */

    async function _fnGrantResolutionRound() {
        var iRounds = parseInt(
            _fsReadValue("councilGrantRounds") || "1", 10);
        await _fnPostAction(
            "/" + _dictState.sActiveCampaignId + "/grant-resolution-round",
            {iGrantedRounds: iRounds > 0 ? iRounds : 1});
    }

    async function _fnResolveObjections() {
        var dictDispositions = {};
        var bIncomplete = false;
        document.querySelectorAll(".council-objection-row").forEach(
            function (elRow) {
                var sObjectionId = elRow.getAttribute("data-objection-id");
                var elAction = elRow.querySelector(
                    ".council-objection-action");
                var elText = elRow.querySelector(
                    ".council-objection-text");
                if (!elAction || !elAction.value) {
                    bIncomplete = true;
                    return;
                }
                dictDispositions[sObjectionId] = {
                    sAction: elAction.value,
                    sText: elText ? elText.value : "",
                };
            });
        if (bIncomplete) {
            VaibifyApp.fnShowToast(
                "Every objection needs a resolve or override decision " +
                "before the final veto.", "error");
            return;
        }
        await _fnPostAction(
            "/" + _dictState.sActiveCampaignId + "/resolve-objections",
            {dictDispositionByObjectionId: dictDispositions});
    }

    async function _fnRejectCandidate() {
        await _fnPostAction(
            "/" + _dictState.sActiveCampaignId + "/reject-candidate",
            {sReasonText: _fsReadValue("councilRejectReason") || ""});
    }

    async function _fnStopCouncil() {
        await _fnPostAction(
            "/" + _dictState.sActiveCampaignId + "/request-stop", undefined);
    }

    async function _fnAcceptPlan() {
        /* No body (remediation R3): the backend accepts the council's
           own server-held candidate through the engine's planReady
           gate; the review gate is the researcher READING it here. */
        try {
            var dictResult = await VaibifyApi.fdictPostRaw(
                _fsRoute("/" + _dictState.sActiveCampaignId
                    + "/accept-plan"));
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
        var sBrief = _fsComposePlanBriefText(dictPlan);
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
        var sText = _fsComposePlanBriefText(dictPlan);
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

    function _fsDirectoryQuery(sJoiner) {
        /* The directory this campaign is about, echoed back on every
           read. A toolkit container tracks several repositories, so
           with no workflow open the server cannot resolve which one a
           bare request means — it answered 409 on every poll, which
           froze a live panel for an entire deliberation (2026-08-24).

           Taken from the CAMPAIGN's own recorded repo path, not from a
           remembered form value: the campaign is the authority on which
           repository it belongs to, and the server re-validates the
           basename against the tracked set exactly as it does at
           convene. */
        var sRepoPath = ((_dictState.dictCampaign || {})
            .dictProjectIdentity || {}).sProjectRepoPath || "";
        if (!sRepoPath) return "";
        var sBasename = sRepoPath.split("/").filter(Boolean).pop() || "";
        if (!sBasename) return "";
        return sJoiner + "sProjectDirectory=" +
            encodeURIComponent(sBasename);
    }

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

    function _fbConveneFormHasWork() {
        /* Only the QUESTION counts as work worth protecting. The
           participant rows are pre-populated and the settings have
           defaults, so treating those as "work" would prompt on a
           modal the researcher merely opened and thought better of --
           a confirm that fires when there is nothing to lose is the
           kind users learn to dismiss without reading. */
        var elQuestion = document.getElementById("councilQuestion");
        return !!(elQuestion && elQuestion.value.trim());
    }

    function _fnHideModalConfirmingLoss() {
        /* Composing the question is the expensive part of convening --
           it is the researcher's actual thinking — and before this a
           stray click on Cancel or the X discarded it silently.

           Through VaibifyApp.fnShowConfirmModal, not window.confirm:
           the dashboard already has one confirmation idiom and a
           second would look like a different application. */
        if (!_fbConveneFormHasWork()) {
            _fnHideModal();
            return;
        }
        VaibifyApp.fnShowConfirmModal(
            "Discard this council?",
            "The question you have written will be lost. Your "
            + "participants and settings will reset to their defaults.",
            _fnHideModal);
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
        _fnBindElement(
            "btnAgentCouncilModalClose", _fnHideModalConfirmingLoss);
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
        /* Exported so the panel can be opened without first convening a
           real council — the browser lane drives the resize against the
           REAL panel, and a test-only alias would be a second name for
           a behaviour the module already owns. */
        fnShowWorkspace: _fnShowWorkspace,
        /* Renders a supplied campaign through the REAL render path,
           signature check included. The browser lane cannot drive a
           live multi-model deliberation, and the defect being guarded
           is precisely that the renderer is not re-entered when only
           turn state changes. */
        fnSetPollHealthForTest: function (iFailures, sError) {
            _dictState.iConsecutivePollFailures = iFailures;
            _dictState.sLastPollError = sError;
            _dictState.iLastPollSucceededAt = Date.now();
        },
        fnSetCampaignForTest: function (dictCampaign) {
            _dictState.dictCampaign = dictCampaign;
            _dictState.sActiveCampaignId = dictCampaign.sCampaignId || "";
            _fnRenderIfChanged();
        },
        /* Runs ONE real poll tick against the real routes. The seam
           supplies no answer of its own, so a test using it exercises
           the actual fetch, the actual catch, and the actual render. */
        fnPollOnceForTest: function () {
            return _fnPollTick();
        },
    };
})();
