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
        iMaximumOutputBytesPerTurn: 10 * 1024 * 1024,
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
        /* True while an action POST is in flight. Held in MODULE
           state and derived into the DOM by the render path, never by
           a helper holding a captured element: this module re-renders
           constantly, so a captured element is a stale element. The
           flag suppresses a second submission of paid work and keeps
           every action button disabled across mid-request re-renders;
           the finally clears it, so a refused action's button is
           restored. */
        bActionPending: false,
        listDraftParticipants: [],
        iChairbotIndex: 0,
        /* Poll health. The panel renders backend truth, so when the
           poll itself is the thing that is broken the panel must say
           so rather than keep displaying its last good answer. */
        iConsecutivePollFailures: 0,
        sLastPollError: "",
        sLastEventPollError: "",
        sLastRenderError: "",
        sLastListError: "",
        sLastAcceptedPlanPath: "",
        sLastChatError: "",
        iLastPollSucceededAt: 0,
        /* Per-directory snapshot feasibility, keyed by directory
           basename, filled on demand when the convene form opens. The
           empty string keys the project's own resolved repository — the
           common single-directory case, whose answer the capabilities
           poll already carried. Mutated in place, never reassigned
           (the IIFE state trap). */
        dictFeasibilityByDirectory: {},
        setExcludedPaths: new Set(),
        /* The ask-the-chairbot conversation, exactly as the backend
           reports it. Never composed here: an answer appears on screen
           because the server recorded it, so a message that failed to
           reach the runner cannot look like one that did. */
        dictChat: null,
        /* Set ONLY while the convene form is being opened to implement
           an accepted plan: the source campaign id the server loads
           the sealed plan from, plus what the form pre-fills. Cleared
           on teardown and on campaign switch, so a later ordinary
           convene can never inherit it. */
        dictImplementationSeed: null,
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
        _dictState.dictChat = null;
        _dictState.listDraftParticipants = [];
        _dictState.iChairbotIndex = 0;
        _dictState.dictImplementationSeed = null;
        _fnResetDraftFields();
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
        /* Focus the active council ONLY while it is live or waiting on
           the researcher (design 6.1: "focuses an active council
           instead of starting a duplicate"). A settled or failed
           campaign the researcher already closed must not capture the
           button forever: that trapped a researcher in a loop where
           every click reopened the same dead campaign and the chooser
           was unreachable (2026-08-27). */
        if (_dictState.sActiveCampaignId && (_fbActiveCampaignIsLive()
                || _fbActiveCampaignNeedsHuman()
                || _fbActiveCampaignPlanReady())) {
            _fnShowWorkspace();
            return;
        }
        _fnOpenCreationChooser();
    }

    /* ------------------------------------------------------------------ */
    /* Creation chooser (section 6.2) and planning form (section 6.3)     */
    /* ------------------------------------------------------------------ */

    async function _fnOpenCreationChooser() {
        /* Backing out to the chooser abandons an implementation seed:
           the next "Plan a change" must be an ordinary planning
           council, not one silently still carrying a plan. */
        _dictState.dictImplementationSeed = null;
        /* The chooser appears IMMEDIATELY and fills in. It used to
           await the campaign listing before rendering anything, which
           on a project tracking nine directories meant ~10 seconds of
           dead air after the click — indistinguishable from a button
           that did nothing (2026-08-27). "Plan a change" is usable
           from the first frame; only the listing arrives late. */
        _fnRenderChooser(true);
        _fnShowModal();
        await _fnRefreshSummaries();
        /* Re-render only if the chooser is still what the modal shows:
           the researcher may have opened the planning form while the
           listing loaded, and clobbering it would discard their
           question. */
        if (document.getElementById("btnCouncilPlanChange")) {
            _fnRenderChooser(false);
        }
    }

    function _fnRenderChooser(bListingStillLoading) {
        /* FOUR tasks, and nothing else. This screen used to print the
           whole council history underneath two buttons — which is what
           "Continue a council" is FOR, so the list both duplicated the
           button and buried it (2026-08-30). Each task now opens its
           own view, and the counts are the promise each one makes. */
        var elBody = document.getElementById("agentCouncilModalBody");
        if (!elBody) return;
        var iResumable = _flistResumableSummaries().length;
        var iImplementable = _flistImplementableSummaries().length;
        var iPast = _flistPastSummaries().length;
        elBody.innerHTML =
            "<h2>Agent Council</h2>" +
            "<div class=\"council-chooser\">" +
            _fsChoiceButton("btnCouncilPlanChange", "Plan a change",
                "Convene a council to produce an implementation plan.",
                true, "", bListingStillLoading) +
            _fsChoiceButton("btnCouncilImplementPlanChoice",
                "Implement a plan",
                "Convene a council that turns an accepted plan into a " +
                "reviewed patch.",
                iImplementable > 0, iImplementable, bListingStillLoading) +
            _fsChoiceButton("btnCouncilOpenExisting", "Continue a council",
                "Pick up a council that stopped part-way.",
                iResumable > 0, iResumable, bListingStillLoading) +
            _fsChoiceButton("btnCouncilViewPast", "View past councils",
                "Read what finished councils decided, and where their " +
                "plans were saved.",
                iPast > 0, iPast, bListingStillLoading) +
            "</div>" +
            (bListingStillLoading
                ? "<p class=\"council-open-when\">Loading past " +
                  "councils…</p>"
                : _fsListRefusalNotice());
        _fnBindChooser();
    }

    function _fsChoiceButton(sId, sLabel, sExplain, bEnabled, sCount,
                             bListingStillLoading) {
        /* Only the LIST-DEPENDENT tasks wait for the listing. Gating
           all four on it disabled "Plan a change" too, which needs no
           listing whatsoever — a researcher who wanted a fresh council
           had to wait for a history they were not going to read. */
        var bDisabled = (sCount !== "" && bListingStillLoading)
            || !bEnabled;
        return "<button type=\"button\" id=\"" + sId + "\" class=\"btn " +
            (sId === "btnCouncilPlanChange" ? "btn-primary " : "") +
            "council-choice\"" + (bDisabled ? " disabled" : "") + ">" +
            "<span class=\"council-choice-label\">" + _fsEscape(sLabel) +
            (sCount === "" ? "" : " (" +
                (bListingStillLoading ? "…" : sCount) + ")") +
            "</span><span class=\"council-choice-explain\">" +
            _fsEscape(sExplain) + "</span></button>";
    }

    function _flistSortedSummaries() {
        return _dictState.listSummaries.slice().sort(
            function (dictLeft, dictRight) {
                return (dictRight.fLastActivityEpoch || 0) -
                    (dictLeft.fLastActivityEpoch || 0);
            });
    }

    function _flistResumableSummaries() {
        return _flistSortedSummaries().filter(_fbSummaryIsResumable);
    }

    /* The states the SERVER will seed an implementation council from.
       Named here so this list offers exactly what the route accepts: a
       filter on "a plan file exists" admitted a campaign the server
       then refused, and the researcher met the refusal at the convene
       button after choosing participants and writing a direction
       (2026-08-30). A menu that offers what the next screen rejects is
       worse than a shorter menu. */
    var SET_IMPLEMENTABLE_STATES = {
        planAccepted: true, awaitingImplementation: true,
    };

    function _flistImplementableSummaries() {
        return _flistSortedSummaries().filter(function (dictSummary) {
            return Boolean(dictSummary.sAcceptedPlanPath)
                && Boolean(SET_IMPLEMENTABLE_STATES[dictSummary.sState])
                && dictSummary.sCampaignKind !== "implementation";
        });
    }

    function _flistPastSummaries() {
        return _flistSortedSummaries().filter(function (dictSummary) {
            return !_fbSummaryIsResumable(dictSummary);
        });
    }

    /* ---- the three task views ---------------------------------------- */

    function _fnRenderTaskView(sHeading, sExplain, sRows) {
        var elBody = document.getElementById("agentCouncilModalBody");
        if (!elBody) return;
        elBody.innerHTML =
            "<h2>" + _fsEscape(sHeading) + "</h2>" +
            "<p class=\"council-hint\">" + _fsEscape(sExplain) + "</p>" +
            (sRows || "<p class=\"council-hint\">Nothing here yet.</p>") +
            "<div class=\"council-view-actions\">" +
            "<button type=\"button\" id=\"btnCouncilBackToTasks\" " +
            "class=\"btn\">Back</button></div>";
        var elBack = document.getElementById("btnCouncilBackToTasks");
        if (elBack) {
            elBack.addEventListener("click", function () {
                _fnRenderChooser(false);
            });
        }
        _fnBindSummaryRows();
    }

    function _fnShowContinueView() {
        _fnRenderTaskView(
            "Continue a council",
            "These councils stopped part-way and can be picked up.",
            _fsSummaryRows(_flistResumableSummaries()));
    }

    function _fbSummaryFailed(dictSummary) {
        /* A council that STOPPED without finishing, as against one that
           reached its end. Keyed on the record's own state rather than
           on the absence of a plan: an accepted council whose plan file
           was later removed is still not a failure. */
        return dictSummary.sState === "failed"
            || dictSummary.sState === "interrupted";
    }

    function _fnShowPastView() {
        var listPast = _flistPastSummaries();
        var listFailed = listPast.filter(_fbSummaryFailed);
        var listDone = listPast.filter(function (dictSummary) {
            return !_fbSummaryFailed(dictSummary);
        });
        _fnRenderTaskView(
            "Past councils",
            "Councils that have finished. Opening one is read-only.",
            _fsPastSection("Completed", listDone) +
            _fsPastSection("Failed", listFailed) +
            _fsPastDeleteActions(listPast.length, listFailed.length));
        _fnBindPastDeleteActions();
    }

    function _fsPastSection(sHeading, listSummaries) {
        if (!listSummaries.length) return "";
        return "<h5 class=\"council-summary-directory\">" +
            _fsEscape(sHeading) + " (" + listSummaries.length + ")</h5>" +
            "<ul class=\"council-summaries\">" +
            listSummaries.map(_fsPastRow).join("") + "</ul>";
    }

    function _fsPastRow(dictSummary) {
        /* The delete control is a SIBLING of the open button, never
           inside it: nesting one button in another is invalid, and a
           stray click would open the very council it was deleting. */
        return "<li class=\"council-past-row\">" +
            _fsOneSummaryRow(dictSummary).replace(/^<li>|<\/li>$/g, "") +
            "<button type=\"button\" class=\"btn danger btn-small " +
            "council-delete-one\" data-delete-campaign=\"" +
            _fsEscape(dictSummary.sCampaignId) + "\" " +
            "data-delete-name=\"" +
            _fsEscape(dictSummary.sCampaignName ||
                dictSummary.sCampaignId) + "\">Delete</button></li>";
    }

    function _fsPastDeleteActions(iTotal, iFailed) {
        if (!iTotal) return "";
        return "<div class=\"council-plan-exports\">" +
            (iFailed
                ? "<button type=\"button\" id=\"btnCouncilDeleteFailed\" " +
                  "class=\"btn danger btn-small\">Delete all failed (" +
                  iFailed + ")</button>"
                : "") +
            "<button type=\"button\" id=\"btnCouncilDeleteAllPast\" " +
            "class=\"btn danger btn-small\">Delete all past councils (" +
            iTotal + ")</button></div>";
    }

    function _fnBindPastDeleteActions() {
        document.querySelectorAll("[data-delete-campaign]").forEach(
            function (elButton) {
                elButton.addEventListener("click", function (eventClick) {
                    eventClick.stopPropagation();
                    _fnConfirmDelete(
                        "Delete this council?",
                        "\u201c" +
                        elButton.getAttribute("data-delete-name") +
                        "\u201d and its snapshot and plan are removed "
                        + "from this machine. This cannot be undone.",
                        [elButton.getAttribute("data-delete-campaign")]);
                });
            });
        _fnBindChoice("btnCouncilDeleteFailed", function () {
            var listIds = _flistPastSummaries().filter(_fbSummaryFailed)
                .map(function (dictSummary) {
                    return dictSummary.sCampaignId;
                });
            _fnConfirmDelete(
                "Delete " + listIds.length + " failed council(s)?",
                "Every council that stopped without finishing is removed "
                + "from this machine, with its snapshot and any plan. "
                + "Completed councils are kept. This cannot be undone.",
                listIds);
        });
        _fnBindChoice("btnCouncilDeleteAllPast", function () {
            var listIds = _flistPastSummaries().map(
                function (dictSummary) { return dictSummary.sCampaignId; });
            _fnConfirmDelete(
                "Delete all " + listIds.length + " past councils?",
                "Every finished council is removed from this machine, "
                + "with its snapshot and any accepted plan. Councils "
                + "that can still be continued are NOT touched. This "
                + "cannot be undone.",
                listIds);
        });
    }

    function _fnConfirmDelete(sTitle, sBody, listCampaignIds) {
        /* Through the dashboard's own confirmation idiom, like every
           other irreversible act here. Deleting a council discards its
           snapshot and its accepted plan, so it is never a quiet
           button. */
        if (!listCampaignIds.length) return;
        VaibifyApp.fnShowConfirmModal(sTitle, sBody, function () {
            _fnDeleteCampaigns(listCampaignIds);
        });
    }

    async function _fnDeleteCampaigns(listCampaignIds) {
        var listFailedIds = [];
        for (var iIndex = 0; iIndex < listCampaignIds.length; iIndex += 1) {
            var sCampaignId = listCampaignIds[iIndex];
            try {
                await VaibifyApi.fnDelete(
                    _fsRoute("/" + sCampaignId) +
                    _fsDirectoryQuery("?", sCampaignId));
            } catch (error) {
                /* Named, never swallowed: a delete that failed while
                   its neighbours succeeded must not look like a clean
                   sweep. */
                listFailedIds.push(sCampaignId);
            }
        }
        if (listFailedIds.length) {
            VaibifyApp.fnShowToast(
                listFailedIds.length + " council(s) could not be "
                + "deleted; they are still listed.", "error");
        }
        /* The active campaign may have just been deleted underneath the
           panel. */
        if (listCampaignIds.indexOf(_dictState.sActiveCampaignId) >= 0) {
            _dictState.sActiveCampaignId = "";
            _dictState.dictCampaign = null;
            _fnRenderToolbarButton();
        }
        await _fnRefreshSummaries();
        _fnShowPastView();
    }

    function _fnShowImplementView() {
        /* Rows here do NOT open the campaign — they seed a NEW
           implementation council from the plan that campaign accepted.
           Reaching this used to require being inside the accepted
           campaign, which acceptance itself made unreachable: accepting
           moves a council to a terminal state, and a terminal council
           is not offered under "continue" (2026-08-30). */
        var elBody = document.getElementById("agentCouncilModalBody");
        if (!elBody) return;
        var listPlans = _flistImplementableSummaries();
        _fnRenderTaskView(
            "Implement a plan",
            "Convenes a second council that turns an accepted plan "
                + "into a reviewed patch. It never applies the patch.",
            listPlans.map(_fsImplementableRow).join(""));
        listPlans.forEach(function (dictSummary) {
            var elRow = document.querySelector(
                "[data-implement-campaign=\"" +
                dictSummary.sCampaignId + "\"]");
            if (!elRow) return;
            elRow.addEventListener("click", function () {
                _fnSeedImplementationFromSummary(dictSummary);
            });
        });
    }

    function _fsImplementableRow(dictSummary) {
        return "<li><button type=\"button\" class=\"council-open-row\" " +
            "data-implement-campaign=\"" +
            _fsEscape(dictSummary.sCampaignId) + "\">" +
            "<span class=\"council-open-name\">" +
            _fsEscape(dictSummary.sCampaignName ||
                dictSummary.sCampaignId) + "</span>" +
            "<span class=\"council-open-where\">plan saved at " +
            _fsEscape(dictSummary.sAcceptedPlanPath) + "</span>" +
            "<span class=\"council-open-when\">" +
            _fsEscape(_fsDescribeActivityTime(
                dictSummary.fLastActivityEpoch)) +
            "</span></button></li>";
    }

    function _fnSeedImplementationFromSummary(dictSummary) {
        _dictState.dictImplementationSeed = {
            sSourceCampaignId: dictSummary.sCampaignId,
            sSourceCampaignName: dictSummary.sCampaignName || "",
            listParticipants: [],
        };
        _fnOpenPlanningForm();
    }

    function _fsListRefusalNotice() {
        /* "(0)" is what an unreported refusal looks like, and it is
           indistinguishable from a project that never convened. */
        if (!_dictState.sLastListError) return "";
        return "<p class=\"council-chat-failure\">This project's " +
            "councils could not be listed (" +
            _fsEscape(_dictState.sLastListError) +
            "). The count beside the button is not a count of what " +
            "exists.</p>";
    }

    function _fsSummariesList() {
        /* Ordered by the record's own clock, identified by the
           researcher's own name, and split by whether the council can
           actually be continued. None of those three existed: a list of
           identical questions in sorted-by-uuid order, with no way to
           tell a live gate from a dead campaign, sent a researcher into
           a nine-hour-old failure (2026-08-25). */
        if (!_dictState.listSummaries.length) return "";
        var listSorted = _dictState.listSummaries.slice().sort(
            function (dictLeft, dictRight) {
                return (dictRight.fLastActivityEpoch || 0) -
                    (dictLeft.fLastActivityEpoch || 0);
            });
        return _fsSummaryRows(listSorted);
    }

    function _fbSummaryIsResumable(dictSummary) {
        return Boolean((dictSummary.dictStoppingPoint || {}).bResumable);
    }

    function _fsSummarySection(sHeading, listSummaries) {
        if (!listSummaries.length) return "";
        return "<h5 class=\"council-summary-directory\">" +
            _fsEscape(sHeading) + "</h5><ul class=\"council-summaries\">" +
            listSummaries.map(_fsOneSummaryRow).join("") + "</ul>";
    }

    function _fsSummaryRows(listSummaries) {
        if (!listSummaries.length) return "";
        return "<ul class=\"council-summaries\">" +
            listSummaries.map(_fsOneSummaryRow).join("") + "</ul>";
    }

    function _fsOneSummaryRow(dictSummary) {
        var dictStopping = dictSummary.dictStoppingPoint || {};
        return "<li><button type=\"button\" class=\"council-open-row\" " +
            "data-campaign=\"" + _fsEscape(dictSummary.sCampaignId) +
            "\"><span class=\"council-open-name\">" +
            _fsEscape(dictSummary.sCampaignName ||
                dictSummary.sCampaignId) + "</span>" +
            (dictSummary.sAcceptedPlanPath
                ? "<span class=\"council-open-where\">plan saved at " +
                  _fsEscape(dictSummary.sAcceptedPlanPath) + "</span>"
                : "") +
            /* "pause requested", never "paused": the listing has no
               liveness, so it cannot know whether the phase that was
               running has finished. It reports the durable flag in the
               flag's own words and lets the opened panel — which does
               have the answer — say which. */
            "<span class=\"council-open-state\">" +
            _fsEscape(dictSummary.bPauseRequested
                ? "pause requested"
                : _fsStateLabel(dictSummary.sState)) + "</span> " +
            "<span class=\"council-open-where\">" +
            _fsEscape(_fsDescribeStoppingPoint(dictStopping)) + "</span> " +
            "<span class=\"council-open-when\">" +
            _fsEscape(_fsDescribeActivityTime(
                dictSummary.fLastActivityEpoch)) + "</span></button></li>";
    }

    /* The protocol's own vocabulary, translated. A researcher read
       rows saying "needsHuman" and "awaitingImplementation" — internal
       state names, run together, in a list built for them (2026-08-30).
       Unknown states fall through to the camel-case splitter rather
       than to the raw token, so a state added later reads as words the
       day it appears instead of the day someone updates this table. */
    var DICT_STATE_LABELS = {
        draft: "not yet convened",
        planning: "deliberating",
        needsHuman: "needs your input",
        planReady: "plan ready for review",
        planAccepted: "completed — plan accepted",
        awaitingImplementation: "awaiting implementation",
        failed: "failed",
        interrupted: "interrupted",
        archived: "archived",
    };

    function _fsStateLabel(sState) {
        return DICT_STATE_LABELS[sState] || _fsSplitCamelCase(sState);
    }

    function _fsSplitCamelCase(sToken) {
        /* "independentProposals" -> "independent proposals". The
           missing spaces the researcher saw were phase names reaching
           the screen exactly as the protocol spells them. */
        return String(sToken || "")
            .replace(/([a-z0-9])([A-Z])/g, "$1 $2").toLowerCase();
    }

    function _fsPhaseLabel(sPhase) {
        return DICT_PHASE_LABELS[sPhase] || _fsSplitCamelCase(sPhase);
    }

    function _fsDescribeStoppingPoint(dictStopping) {
        if (!dictStopping.bResumable) {
            return dictStopping.sBlockedReason || "cannot be continued";
        }
        if (dictStopping.sAction === "retry") {
            return "retry " + (dictStopping.sAttemptPhase
                ? _fsPhaseLabel(dictStopping.sAttemptPhase)
                : "the failed phase") + ", round " +
                (dictStopping.iRoundNumber || 1);
        }
        if (dictStopping.sNextPhase) {
            return "next: " + _fsPhaseLabel(dictStopping.sNextPhase) +
                ", round " +
                (dictStopping.iRoundNumber || 1);
        }
        return "round " + (dictStopping.iRoundNumber || 1) + " complete";
    }

    function _fsDescribeActivityTime(fEpochSeconds) {
        /* Absence says so rather than rendering 1970. */
        if (!fEpochSeconds) return "no recorded activity";
        return new Date(fEpochSeconds * 1000).toLocaleString();
    }

    function _fsDirectoryForListedCampaign(sCampaignId) {
        /* From the SUMMARY the server sent, never a map the panel kept
           for itself: the record is the authority on which repository a
           campaign belongs to. */
        var listMatched = _dictState.listSummaries.filter(
            function (dictSummary) {
                return dictSummary.sCampaignId === sCampaignId;
            });
        return ((listMatched[0] || {}).sProjectRepoPath || "")
            .split("/").filter(Boolean).pop() || "";
    }

    function _fnBindChooser() {
        _fnBindChoice("btnCouncilPlanChange", function () {
            /* A fresh planning council, never carrying a seed left by
               an implementation form the researcher backed out of. */
            _dictState.dictImplementationSeed = null;
            _fnOpenPlanningForm();
        });
        /* Each task now OPENS A VIEW instead of guessing. "Continue"
           used to jump straight into the most recently active resumable
           council, which is why a researcher who had just accepted one
           council landed in a different one entirely (2026-08-30):
           accepting makes a campaign terminal, so it is no longer
           resumable, so the jump silently chose its neighbour. */
        _fnBindChoice("btnCouncilOpenExisting", _fnShowContinueView);
        _fnBindChoice("btnCouncilImplementPlanChoice", _fnShowImplementView);
        _fnBindChoice("btnCouncilViewPast", _fnShowPastView);
        _fnBindSummaryRows();
    }

    function _fnBindChoice(sId, fnHandler) {
        var elButton = document.getElementById(sId);
        if (elButton && !elButton.disabled) {
            elButton.addEventListener("click", fnHandler);
        }
    }

    function _fnBindSummaryRows() {
        document.querySelectorAll("[data-campaign]").forEach(
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

    function _fsComposeImplementationQuestion(dictSeed) {
        /* A DEFAULT the researcher edits, never a hidden instruction:
           what the form shows is exactly what the council receives. */
        return "Implement the accepted plan from \"" +
            (dictSeed.sSourceCampaignName || dictSeed.sSourceCampaignId) +
            "\" as a patch against this repository.";
    }

    function _fnSeedDraftParticipants() {
        var dictSeed = _dictState.dictImplementationSeed;
        if (dictSeed && dictSeed.listParticipants.length >= 2) {
            /* The planning council's own roster: the models that wrote
               the plan are the ones that know what it meant. Copied,
               then editable like any other convene. */
            _dictState.listDraftParticipants =
                dictSeed.listParticipants.map(function (dictParticipant) {
                    return {sProvider: dictParticipant.sProvider,
                            sRequestedModel:
                                dictParticipant.sRequestedModel,
                            sRole: ""};
                });
            _dictState.iChairbotIndex = 0;
            return;
        }
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
        var dictSeed = _dictState.dictImplementationSeed;
        return (dictSeed
            ? "<h2>Implement a plan</h2>" +
              "<p class=\"council-hint\">This council implements the " +
              "accepted plan from <strong>" +
              _fsEscape(dictSeed.sSourceCampaignName ||
                        dictSeed.sSourceCampaignId) +
              "</strong>. The plan is loaded from its sealed artifact " +
              "on the server — you never paste it. The council returns " +
              "a reviewed patch for you to apply by hand; it never " +
              "writes your project.</p>"
            : "<h2>Plan a change</h2>") +
            _fsDirectoryChoiceMarkup() +
            "<div id=\"councilSnapshotScope\"></div>" +
            "<label class=\"council-field\">" +
            (dictSeed ? "Direction for the implementers" : "Question") +
            "<textarea id=\"councilQuestion\" rows=\"4\" " +
            "placeholder=\"" + (dictSeed
                ? "Anything the implementers should know beyond the plan"
                : "What change should the council plan?") + "\">" +
            _fsEscape(dictSeed ? _fsComposeImplementationQuestion(dictSeed)
                      : "") +
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
            "<button type=\"button\" id=\"btnCouncilBackFromForm\" " +
            "class=\"btn\">Back</button>" +
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
            /* The other per-turn budget, exposed for the same reason
               the time budget is: the right value is a property of the
               work being convened, and an implementation council that
               emits a diff for every file it touches is not the same
               shape of work as a single-shot opinion. It was a dead
               number in the record until 2026-08-30. */
            "<label>Output budget per turn (MB) " +
            "<input type=\"number\" id=\"councilOutputBudgetMb\" " +
            "min=\"1\" max=\"64\" value=\"" +
            _fiPreferredOutputBudgetMegabytes() + "\"></label>" +
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
        /* BACK is not Cancel. Cancel closes the panel; Back returns to
           the task menu, which is the move a researcher who opened the
           wrong task actually wants — having to reopen the whole panel
           from the toolbar was reported as annoying (2026-08-30). It
           confirms the same way, because the draft is lost either way. */
        _fnBindElement("btnCouncilBackFromForm", function () {
            _fnReturnToTasksConfirmingLoss();
        });
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
        var sIncomplete = _fsDescribeParticipantsMissingAModel();
        if (sIncomplete) {
            if (elError) elError.textContent = sIncomplete;
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
        var dictSeed = _dictState.dictImplementationSeed;
        if (dictSeed) {
            /* The kind and the SOURCE ID only — never the plan text.
               The server loads the sealed artifact from the source
               campaign it names, so a client cannot substitute a plan
               the council never accepted. */
            dictBody.sCampaignKind = "implementation";
            dictBody.sSourceCampaignId = dictSeed.sSourceCampaignId;
        }
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
            _dictState.dictImplementationSeed = null;
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

    function _fiPreferredTurnWallClockSeconds() {
        var iStored = parseInt(
            window.localStorage.getItem(S_WALL_CLOCK_STORAGE_KEY) || "", 10);
        return (!isNaN(iStored) && iStored >= 60) ? iStored : 3600;
    }

    var S_OUTPUT_BUDGET_STORAGE_KEY = "vaibifyCouncilOutputBudgetBytes";
    var I_BYTES_PER_MEGABYTE = 1024 * 1024;

    function _fiPreferredOutputBudgetMegabytes() {
        var iStored = parseInt(
            window.localStorage.getItem(S_OUTPUT_BUDGET_STORAGE_KEY)
            || "", 10);
        var iBytes = (!isNaN(iStored) && iStored >= I_BYTES_PER_MEGABYTE)
            ? iStored
            : DICT_DEFAULT_SETTINGS.iMaximumOutputBytesPerTurn;
        return Math.round(iBytes / I_BYTES_PER_MEGABYTE);
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
            /* The value the researcher agreed to after a time-budget
               kill. Sending the default when they had raised it would
               make the modal a lie. */
            iTurnWallClockSeconds: _fiPreferredTurnWallClockSeconds(),
            iMaximumOutputBytesPerTurn: _fiReadOutputBudgetBytes(),
        };
    }

    function _fiReadOutputBudgetBytes() {
        /* The FORM's value, remembered for next time. The backend
           clamps to its own bounds and stays the authority. */
        var iMegabytes = parseInt(
            _fsReadValue("councilOutputBudgetMb") || "", 10);
        var iBytes = (!isNaN(iMegabytes) && iMegabytes > 0)
            ? iMegabytes * I_BYTES_PER_MEGABYTE
            : DICT_DEFAULT_SETTINGS.iMaximumOutputBytesPerTurn;
        window.localStorage.setItem(
            S_OUTPUT_BUDGET_STORAGE_KEY, String(iBytes));
        return iBytes;
    }

    function _fsDescribeParticipantsMissingAModel() {
        /* The model lists open UNCHOSEN on purpose (researcher ruling
           2026-08-28): pre-selecting one would tell every researcher
           which model vaibify thinks is best, and this product ranks
           no models — the same reason the chairbot default is
           "first configured", not "strongest".

           The cost of that choice is that a form can be submitted
           incomplete, so the refusal has to be the clear thing: the
           server's own answer is a field-path validation error, and
           before this it reached the researcher as "Request failed
           (422)". Named per agent, in the vocabulary the workspace
           already uses for them. */
        var listMissing = [];
        _dictState.listDraftParticipants.forEach(
            function (dictDraft, iIndex) {
                if (!dictDraft.sRequestedModel) {
                    listMissing.push("Agent " + (iIndex + 1));
                }
            });
        if (!listMissing.length) return "";
        if (listMissing.length === 1) {
            return "Choose a model for " + listMissing[0] +
                " — every participant needs one before the council "
                + "can convene.";
        }
        return "Choose a model for " + listMissing.slice(0, -1).join(", ") +
            " and " + listMissing[listMissing.length - 1] +
            " — every participant needs one before the council can "
            + "convene.";
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
        /* Say something while the record loads: hiding the modal first
           left a blank screen until the fetch returned, which read as
           an unresponsive click (2026-08-27). And show the workspace
           only when the campaign actually ADOPTED — on a load failure
           the researcher goes back to the chooser with the error
           toast, never into a workspace showing the wrong campaign. */
        var elBody = document.getElementById("agentCouncilModalBody");
        if (elBody) {
            elBody.innerHTML = "<h2>Agent Council</h2>" +
                "<p class=\"council-open-when\">Opening council…</p>";
        }
        await _fnLoadCampaign(sCampaignId);
        if (_dictState.sActiveCampaignId === sCampaignId) {
            _fnHideModal();
            _fnShowWorkspace();
            return;
        }
        _fnOpenCreationChooser();
    }

    async function _fnLoadCampaign(sCampaignId) {
        try {
            var dictResult = await VaibifyApi.fdictGet(
                _fsRoute("/" + sCampaignId)
                + _fsDirectoryQuery("?", sCampaignId));
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
            /* The directory rides EVERY request on a multi-directory
               project — exactly as _fbPostAction's comment rules — and
               this refresh was the one reader that forgot it: the
               retry itself succeeded and the refresh then toasted
               "tracks several directories" over a campaign whose
               repository was pinned at convene (2026-08-27). */
            var dictResult = await VaibifyApi.fdictGet(
                _fsRoute("/" + _dictState.sActiveCampaignId)
                + _fsDirectoryQuery("?", _dictState.sActiveCampaignId));
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
        /* The conversation belongs to the campaign, so switching
           campaigns must not show the previous one's transcript — and
           neither must its half-written drafts. */
        _dictState.dictChat = null;
        _fnResetDraftFields();
        /* AFTER the id is set, so the key names the campaign being
           adopted. This is the call that hands a returning researcher
           their half-written answers back. */
        _fnAdoptPersistedDraft();
        _fnRenderToolbarButton();
    }

    async function _fnRefreshSummaries() {
        /* A BLANK PROJECT has no workflow to open, so on a project
           tracking several directories the server cannot resolve which
           repository a bare listing means, and rightly refuses. That
           refusal used to be swallowed into an empty list, so the
           chooser read "(0)" for a project holding a live council
           waiting at its gate (2026-08-25).

           The bare call is tried FIRST, so an ordinary project still
           costs one request; only a refusal fans out across the
           candidate directories the capabilities poll already named. */
        _dictState.sLastListError = "";
        try {
            var dictResult = await VaibifyApi.fdictGet(_fsRoute(""));
            _dictState.listSummaries = dictResult.listCampaigns || [];
            return;
        } catch (error) {
            _dictState.sLastListError = error.message || String(error);
        }
        await _fnListAcrossCandidateDirectories();
    }

    async function _fnListAcrossCandidateDirectories() {
        var listCandidates = (_dictState.dictCapabilities || {})
            .listCandidateDirectories || [];
        if (!listCandidates.length) {
            _dictState.listSummaries = [];
            return;
        }
        /* In parallel, like the planning form's directory weigher: nine
           tracked directories queried one after another held the
           chooser's listing hostage for ~10 seconds (2026-08-27). */
        var listMerged = [];
        await Promise.all(listCandidates.map(async function (sCandidate) {
            try {
                var dictOne = await VaibifyApi.fdictGet(
                    _fsRoute("") + "?sProjectDirectory=" +
                    encodeURIComponent(sCandidate));
                (dictOne.listCampaigns || []).forEach(
                    function (dictSummary) { listMerged.push(dictSummary); });
            } catch (errorOne) {
                /* One unreadable directory must not hide the others. */
                void errorOne;
            }
        }));
        if (listMerged.length) _dictState.sLastListError = "";
        _dictState.listSummaries = listMerged;
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
            /* Only while its tab is open. A conversation nobody is
               reading is bounded by the hub's reaper, not by a poll,
               so polling it in the background would spend requests to
               learn nothing. */
            if (_dictState.sActiveTab === "chat") {
                await _fnLoadChatQuietly();
                _fnRenderIfChanged();
            }
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
            /* The pause changes which composer the footer renders and
               nothing else the signature reads, so without it the
               researcher clicks Pause and the panel keeps saying the
               council is deliberating until some unrelated fact moves.
               The same omission would hide the retirement that clears
               a pause the walk outran. */
            dictCampaign.bPauseRequested ? 1 : 0,
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
            /* The conversation's SETTLED facts only. The idle
               countdown is deliberately absent: it changes on every
               tick, and putting it here would re-render the panel
               every few seconds and wipe a half-typed question. */
            _fsChatSignature(),
        ].join("|");
    }

    function _fsChatSignature() {
        var dictChat = _dictState.dictChat;
        if (!dictChat || !dictChat.bOpen) {
            return "chat:closed:" +
                (_dictState.sLastChatError ? "stale" : "fresh");
        }
        return ["chat", _dictState.sLastChatError ? "stale" : "fresh",
                dictChat.sState,
                (dictChat.listMessages || []).length,
                dictChat.sFailureReason ? 1 : 0,
                dictChat.sResolvedModel || ""].join(":");
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
    var S_WALL_CLOCK_STORAGE_KEY = "vaibifyCouncilTurnWallClockSeconds";
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

    /* Turn ids already surfaced as a time-budget kill, so the modal
       fires ONCE per turn rather than on every poll tick. Mutated in
       place — never reassigned (the IIFE state trap). */
    var _setWallClockNoticesShown = new Set();

    function _fnNoticeWallClockKills(dictCampaign) {
        /* A turn destroyed at its time budget is the one failure a
           researcher can fix themselves, and the fix is a number they
           have to know exists. Left in the failure text alone it reads
           as one more red line among several (2026-08-24). */
        (dictCampaign.listRounds || []).forEach(function (dictRound) {
            var dictByPhase = dictRound.dictTurnsByPhase || {};
            Object.keys(dictByPhase).forEach(function (sPhase) {
                dictByPhase[sPhase].forEach(function (dictTurn) {
                    if (_setWallClockNoticesShown.has(dictTurn.sTurnId)) {
                        return;
                    }
                    if (_fbIsWallClockKill(dictTurn)) {
                        _setWallClockNoticesShown.add(dictTurn.sTurnId);
                        _fnOfferALongerTurnBudget(dictCampaign, dictTurn);
                    } else if (_fbIsOutputCapKill(dictTurn)) {
                        _setWallClockNoticesShown.add(dictTurn.sTurnId);
                        _fnOfferABiggerOutputBudget(dictCampaign);
                    }
                });
            });
        });
    }

    function _fbIsWallClockKill(dictTurn) {
        return dictTurn.sStatus === "failed"
            && (dictTurn.sRejectedPayload || "").indexOf(
                "killedAtTurnWallClockBudget") !== -1;
    }

    function _fbIsOutputCapKill(dictTurn) {
        return dictTurn.sStatus === "failed"
            && (dictTurn.sRejectedPayload || "").indexOf(
                "killedAtTurnOutputCap") !== -1;
    }

    function _fnOfferABiggerOutputBudget(dictCampaign) {
        /* Unlike the time budget, this offer RETRIES THIS campaign
           rather than only remembering a number for the next one. A
           turn killed at the output cap will be killed again at the
           same cap, so an unchanged retry spends paid work to
           reproduce a known outcome — the offer has to change the
           condition to be worth making.

           The suggestion is a multiple of the CURRENT budget, not a
           measurement: the recorded byte count is truncated TO the cap
           before it is stored, so a killed turn can only ever say "it
           needed more than this", never how much more. */
        var iCurrent = ((dictCampaign.dictSettings || {})
            .iMaximumOutputBytesPerTurn)
            || DICT_DEFAULT_SETTINGS.iMaximumOutputBytesPerTurn;
        var iSuggested = Math.min(iCurrent * 2, 64 * I_BYTES_PER_MEGABYTE);
        var iCurrentMb = Math.round(iCurrent / I_BYTES_PER_MEGABYTE);
        var iSuggestedMb = Math.round(iSuggested / I_BYTES_PER_MEGABYTE);
        if (iSuggested <= iCurrent) {
            VaibifyApp.fnShowToast(
                "An agent hit the largest output budget vaibify allows "
                + "(" + iCurrentMb + " MB). Narrow the question rather "
                + "than raising it further.", "error");
            return;
        }
        VaibifyApp.fnShowConfirmModal(
            "An agent produced more than its turn could carry",
            "One agent was stopped at this council's per-turn output "
            + "budget of " + iCurrentMb + " MB, and its work was lost. "
            + "Agents that narrate heavily, invoke many tools, or emit "
            + "a patch for every file they touch reach this before they "
            + "reach the time limit.\n\nRetry the failed phase with "
            + iSuggestedMb + " MB? The budget change applies to this "
            + "council, so the retry runs under different conditions "
            + "rather than repeating the same ones.",
            function () {
                _fnRetryWithOutputBudget(iSuggested, iSuggestedMb);
            },
            {sConfirmLabel: "Retry with " + iSuggestedMb + " MB",
             sCancelLabel: "Leave it"});
    }

    async function _fnRetryWithOutputBudget(iBytes, iMegabytes) {
        window.localStorage.setItem(
            S_OUTPUT_BUDGET_STORAGE_KEY, String(iBytes));
        var bSent = await _fbPostAction(
            "/" + _dictState.sActiveCampaignId + "/retry",
            {iMaximumOutputBytesPerTurn: iBytes});
        if (bSent) {
            VaibifyApp.fnShowToast(
                "Retrying with " + iMegabytes + " MB per turn.", "info");
        }
    }

    function _fnOfferALongerTurnBudget(dictCampaign, dictTurn) {
        var iCurrent = ((dictCampaign.dictSettings || {})
            .iTurnWallClockSeconds) || 3600;
        var iSuggested = Math.min(iCurrent * 2, 43200);
        VaibifyApp.fnShowConfirmModal(
            "An agent ran out of time",
            "One agent was still working when this council's " +
                "per-turn time budget of " + Math.round(iCurrent / 60) +
                " minutes ran out, so its container was stopped and its " +
                "work was lost. Agents that explore the repository with " +
                "many tool calls need longer than ones that answer in a " +
                "single shot.\n\nThe budget cannot be changed for a " +
                "turn already lost. Raise it to " +
            Math.round(iSuggested / 60) + " minutes for the NEXT " +
            "council you convene?",
            function () {
                window.localStorage.setItem(
                    S_WALL_CLOCK_STORAGE_KEY, String(iSuggested));
                VaibifyApp.fnShowToast(
                    "The next council will allow " +
                    Math.round(iSuggested / 60) +
                    " minutes per turn.", "info");
            },
            {sConfirmLabel: "Use " + Math.round(iSuggested / 60) +
                " minutes next time",
             sCancelLabel: "Leave it"});
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
        _fnNoticeWallClockKills(dictCampaign);
        _fnHarvestDraftInputs(elBody);
        elBody.innerHTML = _fsTabBar(dictCampaign) +
            "<div class=\"council-tab-content\">" +
            _fsActiveTabContent(dictCampaign) + "</div>";
        _fnRestoreDraftInputs(elBody);
        _fnBindWorkspace(dictCampaign);
        if (_dictState.bActionPending) {
            /* Derived HERE, in the render path, from module state: a
               poll tick re-rendering mid-request replaces every
               button element, so only a derivation that runs on each
               render can keep them disabled — a helper holding the
               clicked element would be holding a dead node. */
            elBody.querySelectorAll("button").forEach(
                function (elButton) { elButton.disabled = true; });
        }
        _dictState.sRenderSignature = _fsWorkspaceSignature();
    }

    /* Everything the researcher has typed but not submitted, held
       ACROSS renders and tab flips. A flip to the Council tab removed
       the chat textarea from the DOM entirely, so a per-render
       harvest was not enough: the draft must survive while its field
       is absent and refill it when the tab returns (2026-08-27).
       Cleared on campaign switch and on successful submission —
       merge-on-harvest handles the rest (a field the researcher
       emptied deliberately drops its draft). */
    var _dictDraftFields = {dictById: {}, listDecisionAnswers: []};

    function _fnResetDraftFields() {
        /* MEMORY only. The persisted copy is keyed per campaign, so it
           cannot leak between councils, and a reset that also wiped
           storage would delete the draft on the very reload that needs
           it -- this runs while adopting a campaign, before its stored
           draft has been read back. Storage is discarded where a draft
           is genuinely finished: on successful submission. */
        _dictDraftFields.dictById = {};
        _dictDraftFields.listDecisionAnswers = [];
    }

    /* In-memory drafts survive a render and a tab flip; they do NOT
       survive the page going away, and the page goes away for a reason
       the researcher did not choose. A 12-hour session cap fires
       regardless of whether anyone is typing — one expired at 05:28
       under a researcher part-way through answering a gate, and the
       words were gone (2026-08-29). localStorage is the only store
       that outlives the document.

       Keyed per campaign, so answers meant for one council can never
       surface in another. Persisted alongside the in-memory copy
       rather than replacing it: the in-memory path is hot (it runs on
       every render) and must not depend on storage being writable —
       private-mode and quota failures are swallowed for the same
       reason, since losing draft persistence must never break the
       panel that displays the draft. */
    var _S_DRAFT_STORAGE_PREFIX = "vaibifyCouncilDraft:";

    function _fsDraftStorageKey() {
        return _dictState.sActiveCampaignId
            ? _S_DRAFT_STORAGE_PREFIX + _dictState.sActiveCampaignId : "";
    }

    function _fnPersistDraftFields() {
        var sKey = _fsDraftStorageKey();
        if (!sKey) return;
        try {
            window.localStorage.setItem(sKey, JSON.stringify({
                dictById: _dictDraftFields.dictById,
                listDecisionAnswers: _dictDraftFields.listDecisionAnswers,
            }));
        } catch (error) {
            void error;
        }
    }

    function _fnAdoptPersistedDraft() {
        /* Called when a campaign is loaded, which is the render a
           returning researcher gets after a reload. Adopts only into
           EMPTY slots, so a live in-memory draft always wins over a
           stored one. */
        var sKey = _fsDraftStorageKey();
        if (!sKey) return;
        var jsonStored = null;
        try {
            jsonStored = JSON.parse(
                window.localStorage.getItem(sKey) || "null");
        } catch (error) {
            void error;
        }
        if (!jsonStored) return;
        Object.keys(jsonStored.dictById || {}).forEach(function (sId) {
            if (!_dictDraftFields.dictById[sId]) {
                _dictDraftFields.dictById[sId] = jsonStored.dictById[sId];
            }
        });
        (jsonStored.listDecisionAnswers || []).forEach(
            function (sAnswer, iIndex) {
                if (!_dictDraftFields.listDecisionAnswers[iIndex]) {
                    _dictDraftFields.listDecisionAnswers[iIndex] = sAnswer;
                }
            });
    }

    function _fnForgetPersistedDraft() {
        var sKey = _fsDraftStorageKey();
        if (!sKey) return;
        try {
            window.localStorage.removeItem(sKey);
        } catch (error) {
            void error;
        }
    }

    function _fnHarvestDraftInputs(elBody) {
        elBody.querySelectorAll("textarea, input[type=text]").forEach(
            function (elField) {
                if (!elField.id) return;
                if (elField.value) {
                    _dictDraftFields.dictById[elField.id] = elField.value;
                } else {
                    delete _dictDraftFields.dictById[elField.id];
                }
            });
        /* The per-decision answer boxes have no ids, so they are keyed
           by position — stable because the decision list renders in
           record order. Present boxes overwrite their positions;
           absent ones (another tab is showing) keep their drafts. */
        elBody.querySelectorAll(".council-decision-answer").forEach(
            function (elField, iIndex) {
                _dictDraftFields.listDecisionAnswers[iIndex] =
                    elField.value;
            });
        _fnPersistDraftFields();
    }

    function _fnRestoreDraftInputs(elBody) {
        Object.keys(_dictDraftFields.dictById).forEach(function (sId) {
            var elField = document.getElementById(sId);
            if (elField && !elField.value) {
                elField.value = _dictDraftFields.dictById[sId];
            }
        });
        elBody.querySelectorAll(".council-decision-answer").forEach(
            function (elField, iIndex) {
                if (!elField.value
                        && _dictDraftFields.listDecisionAnswers[iIndex]) {
                    elField.value =
                        _dictDraftFields.listDecisionAnswers[iIndex];
                }
            });
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
        sTabs += "<button type=\"button\" class=\"council-tab\" " +
            "data-tab=\"chat\">Ask the chairbot</button>";
        return "<div class=\"council-tabs\">" + sTabs + "</div>";
    }

    function _fbConsoleHasLostEarlierEvents() {
        /* The ring evicts from the FRONT, so anything lost is always
           earlier than everything retained — which is why the marker
           below belongs at the top of a log rather than between two
           rows. */
        var bGap = _dictState.iLowestRetainedSeen > 1
            && _dictState.iLowestRetainedSeen >
                (_fiEarliestSeenSequence() + 1);
        return Boolean(_dictState.bEvictionSeen || bGap);
    }

    function _fsRetentionBoundaryRow() {
        /* Rendered INSIDE the console, never above the tab bar. As a
           banner it appeared on the Council, Plan and chat tabs too —
           none of which show a single event — so a notice about missing
           console lines was displayed everywhere except the console.

           It states the RING's boundary, not a gap between adjacent
           visible rows: each agent tab filters the global stream down
           to one participant, so consecutive rows there routinely skip
           sequence numbers that were never lost at all. */
        if (!_fbConsoleHasLostEarlierEvents()) return "";
        return "<li class=\"council-event council-event-evicted\">" +
            "earlier events no longer retained" +
            (_dictState.iLowestRetainedSeen > 1
                ? " (before #" + _dictState.iLowestRetainedSeen + ")" : "") +
            " — the structured phase artifacts remain</li>";
    }

    function _fiEarliestSeenSequence() {
        if (!_dictState.listEvents.length) return 0;
        return _dictState.listEvents[0].iSequence;
    }

    function _fsActiveTabContent(dictCampaign) {
        var sTab = _dictState.sActiveTab;
        if (sTab === "plan") return _fsPlanTab(dictCampaign);
        if (sTab === "chat") return _fsChatTab();
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
        deliberationSummary: "deliberation summary",
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
                return _fsOneRound(dictRound, dictModelById,
                                   dictCampaign.dictPhaseInFlight);
            }).join("") + "</div>";
    }

    function _fsOneRound(dictRound, dictModelById, dictInFlight) {
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
        sBody += _fsPhaseInFlightRow(dictRound, dictInFlight, dictModelById);
        return "<div class=\"council-round\">" +
            "<strong>Round " + _fsEscape(String(dictRound.iRoundNumber)) +
            "</strong><ul>" + (sBody ||
                "<li class=\"council-hint\">turns in flight</li>") +
            "</ul></div>";
    }

    function _fsPhaseInFlightRow(dictRound, dictInFlight, dictModelById) {
        /* The running phase gets its own row, because the settled rows
           above cannot express it: a phase appears there only once it
           has finished, so cross-review is invisible for its whole
           duration and the round looks abandoned at the proposals. */
        if (!dictInFlight
                || dictInFlight.iRoundNumber !== dictRound.iRoundNumber) {
            return "";
        }
        var sWho = (dictInFlight.listRunningParticipantIds || []).map(
            function (sParticipantId) {
                return dictModelById[sParticipantId] || "participant";
            }).join(", ");
        return "<li class=\"council-phase-live\">" +
            _fsEscape(DICT_PHASE_LABELS[dictInFlight.sPhase]
                || dictInFlight.sPhase) + ": " +
            "<span class=\"council-turn council-turn-live\">" +
            (sWho ? _fsEscape(sWho) + " working…" : "starting…") +
            "</span></li>";
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
                "title=\"" + _fsEscape(_fsReadableFailureReason(
                    dictTurn.sFailureReason)) +
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
                "failed: " + _fsEscape(_fsReadableFailureReason(
                    dictParticipant.sFailureReason)) +
                "</span>";
        }
        /* An agent whose own turn has settled must not read
           "deliberating" just because the campaign is still live — the
           campaign-wide fallback said exactly that, so a finished agent
           was indistinguishable from one still working while a slower
           peer ran. This reports the TURN, which is what settled; it
           deliberately does not claim the runner stopped, because
           nothing here has proven that and "verified stopped" is
           reserved for the backend's absence probe. */
        var sActivity = _fsParticipantActivity(dictParticipant, dictCampaign);
        if (!dictParticipant.sRunnerLifecycle
                && SET_LIVE_STATES[dictCampaign.sState]
                && sActivity) {
            return "<span class=\"council-chip council-chip-activity\">" +
                _fsEscape(sActivity) + "</span>";
        }
        var sLifecycle = dictParticipant.sRunnerLifecycle
            || _fsLifecycleFromCampaign(dictCampaign);
        return "<span class=\"council-chip council-chip-" +
            _fsEscape(sLifecycle) + "\">" +
            _fsEscape(_fsLifecycleLabel(sLifecycle)) + "</span>";
    }

    /* What an agent is DOING, per phase. "Deliberating" was true of the
       campaign and useless about the agent: every chip read it at once,
       so a finished proposer looked identical to a pen-holder mid-
       synthesis. These name the phase's actual work. */
    var DICT_PHASE_ACTIVITY = {
        independentProposals: "proposing",
        crossReview: "reviewing peers",
        synthesis: "synthesizing",
        veto: "voting",
        /* Named rather than left to the "working" fallback: this turn
           is the researcher's LAST paid turn on a spent council, and a
           chip reading "working" would not say that the council is
           closing rather than continuing. */
        deliberationSummary: "writing the deliberation summary"
    };

    function _fsParticipantActivity(dictParticipant, dictCampaign) {
        /* Reads the backend's record of what is running, NEVER the
           settled turn records. A turn record exists only once its turn
           has SETTLED, so a view built from them is structurally blind
           to the phase in progress: during cross-review the newest
           records are the two finished proposals, and every agent reads
           "completed" while both are working. That is the reading a
           researcher correctly called a hang.

           Which agent is running is the backend's word too. Synthesis
           runs one author picked by a fallback chain, so a display that
           assumed the configured chairbot would name the wrong agent
           precisely when a substitution had happened. */
        var dictInFlight = dictCampaign.dictPhaseInFlight;
        if (!dictInFlight) return "";
        var listRunning = dictInFlight.listRunningParticipantIds || [];
        if (listRunning.indexOf(dictParticipant.sParticipantId) >= 0) {
            return DICT_PHASE_ACTIVITY[dictInFlight.sPhase] || "working";
        }
        /* Both single-author phases share the wait: the deliberation
           summary runs one pen through the same fallback chain, so the
           same "waiting on the chairbot, or on whoever took the pen"
           answer is the true one. */
        if (dictInFlight.sPhase === "synthesis"
                || dictInFlight.sPhase === "deliberationSummary") {
            return _fsWaitingOnTheSynthesisAuthor(dictCampaign, listRunning);
        }
        return DICT_PHASE_WAITING[dictInFlight.sPhase]
            || "waiting on the others";
    }

    /* An agent not running the phase in flight is waiting on whoever
       is. Naming WHO makes the wait legible: "waiting on the chairbot"
       during synthesis says the council is progressing without this
       agent, where a bare "waiting" reads as a stall. */
    var DICT_PHASE_WAITING = {
        independentProposals: "waiting on the others",
        crossReview: "waiting on the others",
        veto: "waiting on the other voters"
    };

    function _fsWaitingOnTheSynthesisAuthor(dictCampaign, listRunning) {
        /* Synthesis usually IS the chairbot, so that is what the wait
           says. But the author is picked by a fallback chain, and when
           the chairbot fails another agent takes the pen — so naming
           the chairbot unconditionally would be false exactly in the
           case a researcher most needs to notice. Name the substitute
           instead; the backend already recorded who is running. */
        var sRunningId = listRunning[0];
        if (sRunningId && sRunningId !== dictCampaign.sChairbotParticipantId) {
            return "waiting on " + _fsAgentLabelForId(
                dictCampaign, sRunningId);
        }
        return "waiting on the chairbot";
    }

    function _fsLifecycleFromCampaign(dictCampaign) {
        /* A terminal campaign STATE proves nothing about runners on its
           own (remediation R4/R10): "verified stopped" is reserved for
           a backend-reported lifecycle after the absence probe, and
           the quarantine banner covers the may-still-exist case. This
           inferred value therefore claims only "stopped". */
        if (SET_LIVE_STATES[dictCampaign.sState]) return "deliberating";
        if (SET_TERMINAL_STATES[dictCampaign.sState]) return "stopped";
        /* A council that has produced its plan is FINISHED, and every
           agent in it is finished with it. The fallback said "waiting"
           — the word for an agent whose peer is still working — so a
           completed council reported two idle agents beside a banner
           announcing a plan was ready, and a researcher read the pair
           as a stall (2026-08-29). "Finished" claims nothing about the
           runner process, which is what sRunnerLifecycle reports and
           what the absence probe alone can call stopped. */
        if (dictCampaign.sState === "planReady") return "finished";
        return "waiting";
    }

    function _fsLifecycleLabel(sLifecycle) {
        var dictLabels = {
            preparingSandbox: "preparing sandbox",
            deliberating: "deliberating",
            cleaningUp: "cleaning up",
            verifiedStopped: "verified stopped",
            stopped: "stopped",
            finished: "finished",
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

    /* Decision kinds that carry no prose of their own. Machine records
       stay machine-shaped in the durable file; the panel translates. */
    var DICT_DECISION_KIND_PROSE = {
        phaseRetried: "a failed phase was retired and re-run",
        stopRequestClearedOnRetry:
            "a standing stop request was cleared so the retry could run",
    };

    function _fsResearcherDecisions(dictCampaign) {
        /* A researcherResponse's sText is a pre-rendered "ASKED: …
           ANSWERED: …" blob, and every exchange of a long council was
           printed as one unbroken <li> — reported live as "a giant mass
           of unformatted text" (2026-08-29). The STRUCTURE was there
           all along in listResearcherResponses, so each exchange
           becomes a collapsed box a researcher opens to re-read one
           question and the answer they gave it. Other decision kinds
           (phaseRetried and friends) have no Q&A shape and keep the
           one-line prose rendering. */
        var sExchanges = _fsAnsweredExchanges(dictCampaign);
        var sOther = _fsNonResponseDecisions(dictCampaign);
        if (!sExchanges && !sOther) return "";
        return "<h4>Your decisions</h4>" + sExchanges + sOther;
    }

    function _fsAnsweredExchanges(dictCampaign) {
        var listResponses = dictCampaign.listResearcherResponses || [];
        return listResponses.map(function (dictResponse, iIndex) {
            var listQuestions = dictResponse.listAnsweredQuestions || [];
            if (!listQuestions.length) return "";
            var sRows = listQuestions.map(function (dictQuestion) {
                return _fsOneExchangeRow(
                    dictCampaign, dictResponse, dictQuestion);
            }).join("");
            return "<details class=\"council-exchange\">" +
                "<summary>Questions to you (" + listQuestions.length +
                ") — answered" +
                (listResponses.length > 1
                    ? ", exchange " + (iIndex + 1) : "") +
                "</summary><ol class=\"council-exchange-rows\">" +
                sRows + "</ol></details>";
        }).join("");
    }

    function _fsOneExchangeRow(dictCampaign, dictResponse, dictQuestion) {
        var sAnswer = _fsAnswerForQuestion(
            dictResponse, dictQuestion.sQuestionId || "");
        return "<li>" +
            "<p class=\"council-exchange-question\">" +
            _fsEscape(_fsHideInternalIdentifiers(
                dictCampaign, dictQuestion.sQuestionText)) +
            (dictQuestion.sRaisedByParticipantId
                ? " <span class=\"council-question-author\">(" +
                    _fsEscape(_fsAgentLabelForId(
                        dictCampaign,
                        dictQuestion.sRaisedByParticipantId)) + ")</span>"
                : "") + "</p>" +
            (sAnswer
                ? "<p class=\"council-exchange-answer\">" +
                    _fsEscape(sAnswer) + "</p>"
                : "<p class=\"council-exchange-answer council-hint\">" +
                    "No answer was recorded against this question.</p>") +
            "</li>";
    }

    function _fsAnswerForQuestion(dictResponse, sQuestionId) {
        /* One answer may cover several questions, so the mapping is
           by membership, never by position. */
        var listAnswers = dictResponse.listDecisionAnswers || [];
        for (var iIndex = 0; iIndex < listAnswers.length; iIndex += 1) {
            var listIds = listAnswers[iIndex].listQuestionIds || [];
            if (listIds.indexOf(sQuestionId) >= 0) {
                return listAnswers[iIndex].sAnswerText || "";
            }
        }
        return "";
    }

    function _fsNonResponseDecisions(dictCampaign) {
        var listDecisions = (
            dictCampaign.listResearcherDecisions || []).filter(
            function (dictDecision) {
                return dictDecision.sDecisionKind !== "researcherResponse";
            });
        if (!listDecisions.length) return "";
        var sRows = listDecisions.map(function (dictDecision) {
            /* Prose first, in preference order; NEVER raw JSON — a
               researcher read three serialized phaseRetried records
               under "Your decisions" (2026-08-28). Every record shape
               lands on a sentence, worst case naming its kind. */
            var sProse = dictDecision.sText || dictDecision.sDecision
                || dictDecision.sResponseText
                || DICT_DECISION_KIND_PROSE[dictDecision.sDecisionKind]
                || ("a decision of kind '"
                    + (dictDecision.sDecisionKind || "unknown")
                    + "' was recorded");
            return "<li>" + _fsEscape(sProse) + "</li>";
        }).join("");
        return "<ul class=\"council-decisions\">" + sRows + "</ul>";
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
            _fsEventLog(sParticipantId) + "</div>";
    }

    function _fsEventLog(sParticipantId) {
        /* FILTERED, and rendered for its CONTENT. Every agent tab used
           to show the same global stream as bare event names —
           "#4 providerEvent", "#5 providerEvent" — because the log read
           sEventKind and sDetail while the payload sat unread in
           sParticipantId and dictProviderEvent (2026-08-25). A row that
           names its envelope and hides its content is not a console. */
        var listVisible = _dictState.listEvents.filter(
            function (dictEvent) {
                if (!sParticipantId) return true;
                /* Council-level events (round opened, phase started)
                   carry no participant and belong to every agent's
                   timeline; anything attributed elsewhere does not. */
                if (dictEvent.sParticipantId
                        && dictEvent.sParticipantId !== sParticipantId) {
                    return false;
                }
                return _fbConsoleShowsEvent(dictEvent);
            });
        var sBoundaryRow = _fsRetentionBoundaryRow();
        if (!listVisible.length && !sBoundaryRow) {
            return "<p class=\"council-hint\">No events yet.</p>";
        }
        return "<ul class=\"council-event-log\">" + sBoundaryRow +
            listVisible.map(_fsOneEventRow).join("") + "</ul>";
    }

    function _fsOneEventRow(dictEvent) {
        var sBody = _fsDescribeEvent(dictEvent);
        return "<li class=\"council-event\"><span " +
            "class=\"council-seq\">#" + dictEvent.iSequence + "</span> " +
            "<span class=\"council-event-kind\">" +
            _fsEscape(_fsEventKindLabel(dictEvent)) + "</span> " +
            sBody + "</li>";
    }

    var DICT_EVENT_KIND_LABELS = {
        campaignStarted: "council convened",
        roundOpened: "round opened",
        phaseStarted: "phase started",
        stateTransition: "state changed",
        chairbotChatResting: "chairbot resting (conversation kept)",
        providerEvent: "",
    };

    function _fsEventKindLabel(dictEvent) {
        var sKind = dictEvent.sEventKind || "";
        var sLabel = DICT_EVENT_KIND_LABELS[sKind];
        return sLabel === undefined ? sKind : sLabel;
    }

    function _fsDescribeEvent(dictEvent) {
        var dictProvider = dictEvent.dictProviderEvent;
        if (!dictProvider) {
            return _fsEscape(dictEvent.sDetail || dictEvent.sReason || "");
        }
        return _fsDescribeProviderEvent(dictProvider);
    }

    /* Transport telemetry, not the agent's work. Fourteen consecutive
       "system" rows and a raw "rate_limit_event" told a researcher
       nothing and crowded out the lines that did — enough information
       to wonder what was happening, not enough to understand it
       (2026-08-25). They stay in the durable record; they are not
       shown in a console whose header promises messages, files
       inspected, and scripts run with their exit codes. */
    var SET_CONSOLE_SUPPRESSED_TYPES = {
        system: true, rate_limit_event: true,
    };

    function _fbConsoleShowsEvent(dictEvent) {
        var dictProvider = dictEvent.dictProviderEvent;
        if (!dictProvider) return true;
        if (SET_CONSOLE_SUPPRESSED_TYPES[dictProvider.type]) return false;
        /* An event whose rendering is empty is a bare type name — the
           noise this exists to remove. */
        return Boolean(_fsExtractMessageText(dictProvider)
            || dictProvider.type === "result");
    }

    function _fsDescribeProviderEvent(dictProvider) {
        /* The CLI's stream-json shapes, rendered as what happened. Not
           the model's private reasoning — the assistant TEXT it emits
           and the tools it invokes, which is what the console header
           has always promised and never showed. */
        var sType = dictProvider.type || "";
        if (sType === "assistant" || sType === "user") {
            var sText = _fsExtractMessageText(dictProvider);
            return sText
                ? "<span class=\"council-event-text\">" +
                    _fsEscape(sText.slice(0, 300)) +
                    (sText.length > 300 ? "…" : "") + "</span>"
                : _fsEscape(sType);
        }
        if (sType === "result") {
            return "<span class=\"council-event-text\">turn finished</span>";
        }
        return _fsEscape(sType || "event");
    }

    function _fsSummariseToolInput(jsonInput) {
        /* The one field that says what happened, per tool shape. Whole
           inputs are not rendered: a file write carries the entire new
           contents, which is the model's output, not a console line. */
        if (!jsonInput || typeof jsonInput !== "object") return "";
        var sSummary = jsonInput.command || jsonInput.file_path
            || jsonInput.path || jsonInput.pattern || jsonInput.query || "";
        sSummary = String(sSummary);
        return sSummary.length > 120
            ? sSummary.slice(0, 120) + "…" : sSummary;
    }

    function _fsExtractMessageText(dictProvider) {
        var jsonMessage = dictProvider.message || {};
        var jsonContent = jsonMessage.content;
        if (typeof jsonContent === "string") return jsonContent;
        if (!Array.isArray(jsonContent)) return "";
        return jsonContent.map(function (jsonBlock) {
            if (!jsonBlock || typeof jsonBlock !== "object") return "";
            if (jsonBlock.type === "text") return jsonBlock.text || "";
            /* A tool call is the most informative thing in the stream:
               it is what the agent DID to the repository. */
            if (jsonBlock.type === "tool_use") {
                /* WHAT it ran, not merely that it ran something. The
                   header promises files inspected and scripts run; a
                   bare tool name delivers neither. */
                return "→ " + (jsonBlock.name || "tool") + " "
                    + _fsSummariseToolInput(jsonBlock.input);
            }
            if (jsonBlock.type === "tool_result") {
                return "← " + (jsonBlock.is_error ? "error" : "ok");
            }
            return "";
        }).filter(Boolean).join("  ");
    }

    /* ------------------------------------------------------------------ */
    /* Human response surface (section 6.5)                               */
    /* ------------------------------------------------------------------ */

    function _fsHumanSurface(dictCampaign) {
        if (dictCampaign.sState === "needsHuman") {
            return _fsNeedsHumanCard(dictCampaign);
        }
        /* Routed by the record's own recovery ACTION, not by a state
           set: interrupted was in neither set, so an interrupted
           retryable campaign rendered no surface at all — and a
           failed one rendered a retry button beside a card telling
           the researcher to convene a fresh council. */
        var sRecoveryAction = (
            (dictCampaign.dictStoppingPoint || {}).sAction) || "";
        if (sRecoveryAction === "retry") {
            return _fsRetrySurface(dictCampaign) +
                _fsHeldQuestionsCard(dictCampaign);
        }
        if (SET_TERMINAL_STATES[dictCampaign.sState]
                || dictCampaign.sState === "interrupted") {
            return _fsBlockedRecoveryNote(dictCampaign) +
                _fsHeldQuestionsCard(dictCampaign);
        }
        return _fsComposer(dictCampaign);
    }

    function _fsBlockedRecoveryNote(dictCampaign) {
        /* A failed or interrupted council with no offered action says
           WHY, in the record's own words, instead of a silent dead
           panel. */
        if (dictCampaign.sState !== "failed"
                && dictCampaign.sState !== "interrupted") {
            return "";
        }
        var sReason = (dictCampaign.dictStoppingPoint || {})
            .sBlockedReason || "";
        if (!sReason) return "";
        return "<div class=\"council-composer\">" +
            "<p class=\"council-hint\">This council cannot be " +
            "continued: " + _fsEscape(sReason) + "</p></div>";
    }

    function _fsRetrySurface(dictCampaign) {
        /* Rendered only when the durable record's own action says
           retry: the terminating attempt is retired into the record
           and its phase re-run. The whitelist already refused
           failures that repeat identically, so a rendered button is
           one the route will admit. */
        var dictStopping = dictCampaign.dictStoppingPoint || {};
        if (dictStopping.sAction !== "retry") return "";
        return "<div class=\"council-composer\">" +
            "<p class=\"council-hint\">This council stopped at a " +
            "failure a re-run can survive (" +
            _fsEscape(_fsDescribeStoppingPoint(dictStopping)) +
            "). Retrying retires the failed attempt into the record " +
            "and re-runs its phase — paid provider work.</p>" +
            "<button type=\"button\" id=\"btnCouncilRetry\" " +
            "class=\"btn btn-primary\">" +
            (dictCampaign.bStopRequested
                ? "Retry the failed phase (clears the requested stop)"
                : "Retry the failed phase") +
            "</button>" +
            "</div>";
    }

    function _fsHeldQuestionsCard(dictCampaign) {
        /* Questions raised before synthesis wait for the plan they are
           about. If a later phase settles indeterminately the campaign
           is interrupted BEFORE any gate can open, and without this the
           questions sit on the round where nobody can read them —
           real deliberation, silently unreachable. What the card SAYS
           about them depends on the record's own action: beside a
           Retry button, "convene a fresh council" and "cannot be
           resumed" would contradict the control one line up. */
        var listHeld = dictCampaign.listHeldQuestions || [];
        if (!listHeld.length) return "";
        var bRetryOffered = ((dictCampaign.dictStoppingPoint || {})
            .sAction === "retry");
        var sDisposition = bRetryOffered
            ? "Retrying the failed phase re-runs the deliberation " +
              "that raised them, so the re-run asks its own questions " +
              "— these are kept here as the record of what this " +
              "attempt wanted to know."
            : "They were waiting for the plan they are about, which " +
              "this council never reached. This campaign cannot be " +
              "resumed, but the questions are not lost — they are " +
              "the answers a fresh council would not have to ask " +
              "for again.";
        var sRows = listHeld.map(function (dictQuestion) {
            return "<li>" + _fsEscape(dictQuestion.sQuestionText || "") +
                " <span class=\"council-question-author\">(" +
                _fsEscape(_fsAgentLabelForId(
                    dictCampaign, dictQuestion.sRaisedByParticipantId || "")) +
                ")</span></li>";
        }).join("");
        return "<div class=\"council-needs-human council-held\">" +
            "<h4>" + listHeld.length + " question" +
            (listHeld.length === 1 ? " was" : "s were") + " raised before " +
            "this council stopped</h4>" +
            "<p>" + _fsEscape(sDisposition) + "</p>" +
            "<ol class=\"council-questions\">" + sRows + "</ol>" +
            "</div>";
    }

    function _fsComposer(dictCampaign) {
        /* The protocol has no mid-deliberation message channel
           (remediation R6): the engine accepts researcher text ONLY at
           a human gate, so this surface says what a researcher can
           actually do while the council deliberates — read, or stop.
           A message box here would post a respond the backend rightly
           refuses 409, which is a control that only ever fails. */
        if (dictCampaign.bDeliberationLive === false) {
            return _fsResumeSurface(dictCampaign);
        }
        /* planReady reached this branch and rendered "the council is
           deliberating… you can stop after the current turn" UNDER a
           banner saying a plan was ready — two contradictory claims on
           one screen, with a Stop button offering to halt work that had
           already finished (2026-08-29). A finished council says so and
           points at the tab holding the outcome. */
        if (dictCampaign.sState === "planReady") {
            return _fsFinishedComposer(dictCampaign);
        }
        if (dictCampaign.bPauseRequested) {
            return _fsPausedSurface(dictCampaign);
        }
        return "<div class=\"council-composer\">" +
            "<p class=\"council-hint\">The council is deliberating. It " +
            "will stop here when it needs your decision; until then " +
            "you can watch the consoles, pause it, or stop it.</p>" +
            "<button type=\"button\" id=\"btnCouncilPause\" " +
            "class=\"btn btn-primary\">Pause after this phase</button>" +
            "<button type=\"button\" id=\"btnCouncilStop\" " +
            "class=\"btn btn-danger\">Stop council</button>" +
            /* The two controls sit side by side, so the difference has
               to be on the screen and not only in the labels: one of
               them ends the campaign for good. */
            "<p class=\"council-hint\">Pausing lets " +
            "the phase now running finish, then stands the council " +
            "down where you can resume it. Stopping ends this council " +
            "for good — its record is kept, but it can never " +
            "deliberate again.</p>" +
            "</div>";
    }

    function _fsPausedSurface(dictCampaign) {
        /* bPauseRequested with the hub still holding the campaign.
           Which of the two things it means — the phase is finishing, or
           the council has stood down — is answered by the BACKEND's
           in-flight phase record, which the engine clears when a phase
           settles. Never inferred from staleness, and never from the
           absence of recent events. */
        var dictInFlight = dictCampaign.dictPhaseInFlight;
        if (dictInFlight) {
            return "<div class=\"council-composer\">" +
                "<p class=\"council-hint\">Pausing. The " +
                _fsEscape(dictInFlight.sPhase || "current") +
                " phase is still running and will finish first — " +
                "cutting a turn off mid-answer would leave a failure " +
                "nobody could attribute to anything. Nothing new " +
                "starts after it, and you can close this tab.</p>" +
                "<button type=\"button\" id=\"btnCouncilStop\" " +
                "class=\"btn btn-danger\">Stop council instead</button>" +
                "</div>";
        }
        return "<div class=\"council-composer\">" +
            "<p class=\"council-hint\">Paused. No agent is working and " +
            "nothing new will start (" +
            _fsEscape(_fsDescribeStoppingPoint(
                dictCampaign.dictStoppingPoint || {})) +
            "). Resuming relaunches paid provider work from that step, " +
            "against the same sealed snapshot; stopping instead ends " +
            "this council for good.</p>" +
            "<button type=\"button\" id=\"btnCouncilResume\" " +
            "class=\"btn btn-primary\">Resume deliberation</button>" +
            "<button type=\"button\" id=\"btnCouncilStop\" " +
            "class=\"btn btn-danger\">Stop council</button>" +
            "</div>";
    }

    function _fsFinishedComposer(dictCampaign) {
        /* Deliberately NOT "reached consensus": the plan carries its own
           result classification, and the Plan tab's banner says in as
           many words that agreement is not evidence. Announcing
           consensus here would contradict it one tab away. */
        void dictCampaign;
        return "<div class=\"council-composer council-composer-done\">" +
            "<p class=\"council-hint\">This council has finished " +
            "deliberating and produced a plan. Open the Plan tab to " +
            "read it and decide what happens next.</p>" +
            "<button type=\"button\" id=\"btnCouncilOpenPlanTab\" " +
            "class=\"btn btn-primary\">Open the Plan tab</button>" +
            "</div>";
    }

    function _fnSelectTab(sTab) {
        _dictState.sActiveTab = sTab;
        _fnRenderWorkspace();
    }

    function _fsResumeSurface(dictCampaign) {
        /* A planning campaign with NO live deliberation: the hub
           restarted under it. Rendered only from backend truth
           (bDeliberationLive and the durable stopping point) — this
           panel never guesses liveness from staleness. The button
           offers exactly what the record supports; the route
           re-derives the same answer and adds the dynamic refusals
           (reservations, image drift, archive validation) a listing
           cannot promise. */
        var dictStopping = dictCampaign.dictStoppingPoint || {};
        if (dictStopping.sAction !== "resume") {
            return "<div class=\"council-composer\">" +
                "<p class=\"council-hint\">This council is not " +
                "running, and cannot be continued: " +
                _fsEscape(dictStopping.sBlockedReason ||
                    "no coherent stopping point was recorded") +
                "</p></div>";
        }
        var bClearsStop = Boolean(dictCampaign.bStopRequested);
        /* Why it is not running is not a detail. A researcher who
           paused it and came back must not be told the hub crashed,
           and a researcher whose hub DID crash must not be told they
           paused it. The record's own flag decides. */
        var sWhy = dictCampaign.bPauseRequested
            ? "This council is paused — you stood it down, and it " +
              "stopped at a settled boundary ("
            : "This council is not running — the hub restarted since " +
              "its last settled step (";
        return "<div class=\"council-composer\">" +
            "<p class=\"council-hint\">" + sWhy +
            _fsEscape(_fsDescribeStoppingPoint(dictStopping)) +
            "). Resuming relaunches paid provider work from that " +
            "step, against the same sealed snapshot.</p>" +
            "<button type=\"button\" id=\"btnCouncilResume\" " +
            "class=\"btn btn-primary\">" +
            (bClearsStop ? "Resume (clears the requested stop)"
                : "Resume deliberation") +
            "</button>" +
            "</div>";
    }

    function _fsNeedsHumanCard(dictCampaign) {
        var dictGate = dictCampaign.dictPendingHumanGate || {};
        var sGateCard = (dictGate.sGateKind === "exhaustedRounds")
            ? _fsExhaustedRoundCard(dictCampaign, dictGate)
            : _fsBlockingQuestionCard(dictCampaign, dictGate);
        /* BESIDE the gate, never inside it. Charter clause 6 told
           participants that a finding worth attention but not a
           decision belongs in their evidence, and a live gate then
           carried four items whose text literally opened "Emphasis,
           not a decision: ..." — raised as questions because there was
           nowhere else to put one (2026-08-29). There is a field now,
           and its contents must land somewhere the researcher reads
           without being asked to answer it. Appended after the gate so
           nothing here can be mistaken for part of the form above. */
        return sGateCard + _fsNotedFindingsPanel(dictCampaign);
    }

    function _fsNotedFindingsPanel(dictCampaign) {
        /* Deliberately a different SHAPE from the gate: an <aside>
           rather than a card, bulleted rather than numbered, and no
           answer control anywhere in it. A researcher scanning the
           screen has to be able to tell "this wants my decision" from
           "this wants my attention" without reading either. */
        var listNoted = dictCampaign.listGateNotes || [];
        if (!listNoted.length) return "";
        var sRows = listNoted.map(function (dictNote) {
            return "<li>" + _fsEscape(_fsHideInternalIdentifiers(
                dictCampaign, dictNote.sNoteText)) +
                " <span class=\"council-question-author\">(" +
                _fsEscape(_fsAgentLabelForId(
                    dictCampaign, dictNote.sRaisedByParticipantId || "")) +
                ")</span></li>";
        }).join("");
        return "<aside class=\"council-notes\">" +
            "<h4>Noted, not asked (" + listNoted.length + ")</h4>" +
            "<p class=\"council-notes-why\">These are findings the " +
            "council recorded for your attention. None of them is a " +
            "decision, none needs an answer, and nothing above is " +
            "waiting on them.</p>" +
            "<ul class=\"council-notes-list\">" + sRows + "</ul>" +
            "</aside>";
    }

    function _fsDeliberationSummarySection(dictCampaign) {
        /* A council that ran out of rounds without converging writes
           one last chairbot turn. It is a DELIBERATION SUMMARY and the
           word "plan" must not appear over it: no plan was agreed, and
           a heading that said otherwise would state a consensus that
           does not exist. */
        var dictSummary = dictCampaign.dictDeliberationSummary || {};
        var dictResult = dictSummary.dictResult || {};
        if (!dictResult.sSummary) {
            return "<p class=\"council-summary-absent\">No deliberation " +
                "summary was written — the closing turn returned none. " +
                "The objections below and the agent consoles are the " +
                "record of the argument.</p>";
        }
        var sParts = "<div class=\"council-deliberation-summary\">" +
            "<h5>Deliberation summary — not a plan</h5>" +
            "<p class=\"council-summary-caveat\">This council did not " +
            "converge, so nothing below was agreed. It is the " +
            "chairbot's account of what was argued.</p>" +
            "<p>" + _fsEscape(_fsHideInternalIdentifiers(
                dictCampaign, dictResult.sSummary)) + "</p>";
        [["listPositionsProposed", "Positions proposed"],
         ["listPointsOfDisagreement", "Where the council divided"],
         ["listEvidenceBehindEachPosition",
          "What each side had to stand on"]]
            .forEach(function (tSection) {
                var listEntries = dictResult[tSection[0]] || [];
                if (!listEntries.length) return;
                sParts += "<h6>" + _fsEscape(tSection[1]) + "</h6><ul>" +
                    listEntries.map(function (sEntry) {
                        return "<li>" + _fsEscape(_fsHideInternalIdentifiers(
                            dictCampaign, String(sEntry))) + "</li>";
                    }).join("") + "</ul>";
            });
        return sParts + "</div>";
    }

    function _fsAgentLabelForId(dictCampaign, sParticipantId) {
        /* "Agent 2", not "participant-248eecee27e1". The engine's id is
           the right thing to key on and the wrong thing to show: the
           tabs above already read "Agent 1"/"Agent 2", so a raw id in
           the question list is unmatchable to the tab it came from.
           Falls back to "the server" for the quorum-shortfall gate,
           whose single question is raised by "server", and to the id
           itself if a participant ever cannot be resolved — an
           unrecognizable label is better than a confidently wrong one. */
        var listParticipants = dictCampaign.listParticipants || [];
        for (var iIndex = 0; iIndex < listParticipants.length; iIndex += 1) {
            if (listParticipants[iIndex].sParticipantId === sParticipantId) {
                return "Agent " + (iIndex + 1);
            }
        }
        return sParticipantId === "server" ? "the server" : sParticipantId;
    }

    /* The server computes the tier; this only names it. A count here
       would be a second authority on who raised what, and the two would
       disagree the first time a participant failed. */
    var LIST_DECISION_TIERS = [
        {sTier: "raisedByAll",
         sHeading: "Every agent raised this",
         sWhy: "Answer these first — each unblocks work for the whole " +
             "council."},
        {sTier: "raisedBySeveral",
         sHeading: "Several agents raised this",
         sWhy: "More than one agent, but not all of them."},
        {sTier: "raisedByOne",
         sHeading: "One agent raised this",
         sWhy: "A single agent's concern; the others did not raise it."},
        {sTier: "raisedDuringSynthesis",
         sHeading: "Raised while writing the plan",
         sWhy: "The chairbot's own questions. No other agent has seen " +
             "these, so they carry no plan item yet."}
    ];

    /* The server's own identifiers, as they appear in text a MODEL
       wrote. The chairbot is handed question ids so it can say which
       decision a plan item waits on (charter clause for synthesis), and
       it dutifully repeats them into prose the researcher then reads:
       "question-6ad0ced24d7b (participant-854f6ca400d1) — Euler:
       deprecate this release…". The id is the right thing to key on and
       the wrong thing to show, which is already why the author suffix
       reads "Agent 1". Reported live 2026-08-25. */
    var _RE_QUESTION_IDENTIFIER = /question-[0-9a-f]{6,}/g;
    var _RE_PARTICIPANT_IDENTIFIER = /participant-[0-9a-f]{6,}/g;

    function _fsHideInternalIdentifiers(dictCampaign, sText) {
        /* A participant id is REPLACED rather than dropped: the model
           sometimes says "as participant-x noted", and deleting the id
           would leave a sentence missing its subject. A question id
           carries nothing a reader can use — the decision it belongs to
           is the block it is rendered inside — so it goes. */
        var sCleaned = String(sText || "").replace(
            _RE_PARTICIPANT_IDENTIFIER, function (sIdentifier) {
                return _fsAgentLabelForId(dictCampaign, sIdentifier);
            });
        sCleaned = sCleaned.replace(_RE_QUESTION_IDENTIFIER, "");
        /* The debris an excised id leaves: an empty bracket, a doubled
           space, a leading dash where the id used to be \u2014 and, since
           the charter tells the chairbot to write "waits on
           question-x and question-y", a parenthetical holding only
           its connectives: "(waits on and )" rendered verbatim in a
           live gate (2026-08-28). */
        /* The chairbot numbers its own decisions, and vaibify groups and
           numbers them independently, so a gate rendered "Decision 4"
           as a heading over a body opening "1. DECISION 2" — three
           authorities disagreeing about one item (reported live
           2026-08-29). Vaibify's grouping is the authority a researcher
           acts on, so the model's leading self-label is noise and goes.
           Anchored at the start on purpose: a mid-sentence "as DECISION
           2 established" is a reference, not a label, and carries
           meaning the reader needs. */
        sCleaned = sCleaned.replace(
            /^\s*(?:\d+[.)]\s*)?DECISION\s+\d+\s*/i, "");
        return sCleaned
            .replace(/\((?:\s|,|;|and|or|waits on)*\)/g, "")
            .replace(/\s{2,}/g, " ")
            .replace(/\s+([:;,.])/g, "$1")
            .replace(/^[\s\u2014\u2013-]+/, "")
            .trim();
    }

    function _fsDecisionBlock(dictCampaign, dictDecision, iNumber) {
        var sQuestions = (dictDecision.listQuestions || []).map(
            function (dictQuestion) {
                return "<li data-question-id=\"" +
                    _fsEscape(dictQuestion.sQuestionId || "") + "\">" +
                    _fsEscape(_fsHideInternalIdentifiers(
                        dictCampaign, dictQuestion.sQuestionText)) +
                    " <span class=\"council-question-author\">(" +
                    _fsEscape(_fsAgentLabelForId(
                        dictCampaign,
                        dictQuestion.sRaisedByParticipantId || "")) +
                    ")</span></li>";
            }).join("");
        var sContext = (dictDecision.listPlanItemTexts || []).map(
            function (sText) {
                return _fsDecisionContext(dictCampaign, sText);
            }).join("");
        return "<div class=\"council-decision\" data-decision-id=\"" +
            _fsEscape(dictDecision.sDecisionId || "") + "\">" +
            "<h5>Decision " + iNumber + "</h5>" +
            sContext +
            "<ul class=\"council-questions\">" + sQuestions + "</ul>" +
            "<textarea class=\"council-decision-answer\" rows=\"2\" " +
            "placeholder=\"Your answer to this\"></textarea>" +
            "</div>";
    }

    /* The plan item is the CONTEXT the question was unreadable without:
       "(Phase 2)" meant nothing when the plan was not shown beside it.
       It used to be cut at 240 characters with an ellipsis and NO way
       to read the rest — the researcher was asked to decide against a
       sentence that stopped mid-clause (reported live 2026-08-25). Now
       the whole text is in the DOM and a native <details> holds it, so
       reading it costs one click and no round trip; a short item is
       rendered plainly, because a disclosure widget around two lines is
       noise. */
    var I_CONTEXT_SUMMARY_CHARACTERS = 180;

    function _fsDecisionContext(dictCampaign, sRawText) {
        var sText = _fsHideInternalIdentifiers(dictCampaign, sRawText);
        if (sText.length <= I_CONTEXT_SUMMARY_CHARACTERS) {
            return "<p class=\"council-decision-context\">" +
                _fsEscape(sText) + "</p>";
        }
        return "<details class=\"council-decision-context\">" +
            "<summary>" +
            _fsEscape(sText.substring(0, I_CONTEXT_SUMMARY_CHARACTERS)) +
            "… <span class=\"council-context-more\">show all</span>" +
            "</summary><p>" + _fsEscape(sText) + "</p></details>";
    }

    function _fsDecisionGate(dictCampaign, listDecisions) {
        var sBody = "";
        var iNumber = 0;
        LIST_DECISION_TIERS.forEach(function (dictTier) {
            var listInTier = listDecisions.filter(function (dictDecision) {
                return dictDecision.sTier === dictTier.sTier;
            });
            if (!listInTier.length) return;
            sBody += "<h4 class=\"council-tier\">" +
                _fsEscape(dictTier.sHeading) + " (" + listInTier.length +
                ")</h4><p class=\"council-tier-why\">" +
                _fsEscape(dictTier.sWhy) + "</p>";
            listInTier.forEach(function (dictDecision) {
                iNumber += 1;
                sBody += _fsDecisionBlock(dictCampaign, dictDecision, iNumber);
            });
        });
        return sBody;
    }

    function _fsUnanswerableGateCard(dictCampaign, dictGate, dictStopping) {
        var sQuestionRows = (dictGate.listQuestions || []).map(
            function (dictQuestion) {
                return "<li>" + _fsEscape(_fsHideInternalIdentifiers(
                    dictCampaign, dictQuestion.sQuestionText)) +
                    " <span class=\"council-question-author\">(raised " +
                    "by " + _fsEscape(_fsAgentLabelForId(
                        dictCampaign,
                        dictQuestion.sRaisedByParticipantId || "")) +
                    ")</span></li>";
            }).join("");
        return "<div class=\"council-needs-human\">" +
            "<h4>This council's questions can be read, not answered</h4>" +
            "<p class=\"council-chat-failure\">" +
            _fsEscape(dictStopping.sBlockedReason) + "</p>" +
            (sQuestionRows
                ? "<ol class=\"council-questions\">" + sQuestionRows +
                    "</ol>"
                : "") +
            "<p class=\"council-hint\">Close this council and choose " +
            "Plan a change to convene a fresh one — its questions are " +
            "above for reference.</p>" +
            "</div>";
    }

    function _fsBlockingQuestionCard(dictCampaign, dictGate) {
        /* The ENGINE'S gate shape (remediation R6): a list of
           questions, each carrying who raised it. The quorum-shortfall
           gate shares this renderer — its single server-raised
           question rides the same list. */
        /* An unanswerable gate says so FIRST. The store can rule a
           campaign unusable (a lost provenance sidecar) while its
           record still holds a needsHuman gate; rendering live answer
           boxes then invited a researcher to type thirteen answers
           into a form whose submission was always going to be refused
           (2026-08-27). The questions stay readable — they are the
           record — but nothing pretends they can be answered. */
        var dictStopping = dictCampaign.dictStoppingPoint || {};
        if (dictStopping.bResumable === false
                && dictStopping.sBlockedReason) {
            return _fsUnanswerableGateCard(dictCampaign, dictGate,
                dictStopping);
        }
        var listQuestions = dictGate.listQuestions || [];
        var listDecisions = dictCampaign.listGateDecisions || [];
        var sQuestionRows = listQuestions.map(function (dictQuestion) {
            return "<li>" + _fsEscape(_fsHideInternalIdentifiers(
                dictCampaign, dictQuestion.sQuestionText)) +
                " <span class=\"council-question-author\">(raised by " +
                _fsEscape(_fsAgentLabelForId(
                    dictCampaign,
                    dictQuestion.sRaisedByParticipantId || "")) +
                ")</span></li>";
        }).join("");
        return "<div class=\"council-needs-human\">" +
            "<h4>The council needs your opinion</h4>" +
            /* Say so when the questions arrive WITHOUT the plan they
               are about. bPlanAvailable is false when the pen-holder's
               turn produced nothing: the questions are still shown —
               a failed chairbot must not swallow them — but the
               researcher is answering against proposals rather than
               against a folded plan, and only this sentence says so.
               Absent on gates written by an earlier hub, which is not
               the same as false, so the notice needs an explicit
               false. */
            (dictGate.bPlanAvailable === false
                ? "<p class=\"council-plan-missing\">The chairbot " +
                    "produced no plan this round, so these questions " +
                    "are not yet placed in one. They are shown as each " +
                    "agent raised them.</p>"
                : "") +
            /* Grouped into DECISIONS when the server could compute
               them: two agents asking one thing is answered once, and
               the most-shared come first. The flat numbered list stays
               as the fallback for a gate the grouping cannot place —
               the quorum-shortfall gate, whose single server-raised
               question has no plan item, and any record from a hub that
               predates the grouping. */
            (listDecisions.length
                ? _fsDecisionGate(dictCampaign, listDecisions)
                : (sQuestionRows
                    ? "<ol class=\"council-questions\">" + sQuestionRows +
                        "</ol>"
                    : "<p>A material choice could not be settled from " +
                        "evidence.</p>")) +
            (listDecisions.length
                ? ""
                : "<textarea id=\"councilAnswer\" rows=\"3\"></textarea>") +
            "<p id=\"councilGateNotice\" class=\"council-gate-notice\" " +
            "style=\"display:none\"></p>" +
            "<button type=\"button\" id=\"btnCouncilAnswer\" " +
            "class=\"btn btn-primary\">Record decision</button>" +
            "</div>";
    }

    function _fsExhaustedRoundCard(dictCampaign, dictGate) {
        /* The exits are distinct controls posting the ENGINE'S own
           transitions, and there is NO plain respond field that would
           silently relaunch the spent round budget (section 6.5); the
           resolve/override exit requires a decision on EVERY
           unresolved objection before it submits.

           TWO of them are ways FORWARD (researcher direction
           2026-08-29): implement the standing candidate as it is,
           recorded as an explicit override, or grant more rounds.
           Rejecting is not a third way forward — it is abandoning the
           council — so it sits below, under its own heading, rather
           than beside them where it read as a peer option. */
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
            "<h4>Rounds exhausted without consensus</h4>" +
            _fsDeliberationSummarySection(dictCampaign) +
            (sObjectionRows
                ? "<h5>Objections still outstanding</h5>" + sObjectionRows
                : "") +
            "<div class=\"council-exits\">" +
            "<p class=\"council-exits-why\">Two ways forward.</p>" +
            "<label>Rounds to grant <input type=\"number\" " +
            "id=\"councilGrantRounds\" min=\"1\" value=\"1\"></label>" +
            "<button type=\"button\" id=\"btnCouncilGrantRound\" " +
            "class=\"btn\">Grant a bounded resolution round</button>" +
            "<button type=\"button\" id=\"btnCouncilResolveOverride\" " +
            "class=\"btn\">Implement as-is: resolve or override, then a " +
            "final veto</button>" +
            "</div>" +
            "<div class=\"council-abandon\">" +
            "<h5>Or abandon this council</h5>" +
            "<input type=\"text\" id=\"councilRejectReason\" " +
            "placeholder=\"Why the candidate is rejected (optional)\">" +
            "<button type=\"button\" id=\"btnCouncilReject\" " +
            "class=\"btn danger\">Reject and archive</button>" +
            "</div></div>";
    }

    /* ------------------------------------------------------------------ */
    /* Plan tab and accepted-plan actions (section 6.6)                   */
    /* ------------------------------------------------------------------ */

    /* A recorded failure reason opens with its MACHINE class —
       "emptyTurn: ...", "invalidStructuredResultAfterRepair: ..." —
       which the record needs and a researcher does not. The screen
       showed "failed: emptyTurn: the provider refused this turn..."
       (reported live 2026-08-28); the class stays in the record and,
       where one is worth naming, inside the prose itself. Only a
       leading single camelCase token is stripped, so a reason that
       opens with a sentence keeps every word. */
    var _RE_LEADING_MACHINE_CLASS = /^[a-z][A-Za-z0-9]*:\s+/;

    function _fsReadableFailureReason(sRecordedReason) {
        return String(sRecordedReason || "")
            .replace(_RE_LEADING_MACHINE_CLASS, "");
    }

    function _fsPatchSection(dictResult) {
        /* An implementation council's deliverable. Rendered as the
           diff it is — escaped, never interpreted — with the files it
           touches and every declared departure from the plan, because
           a deviation the researcher does not read is a deviation
           nobody agreed to. */
        if (!dictResult.sPatchUnifiedDiff) return "";
        var sSection = "<h5>Patch</h5>" +
            "<pre class=\"council-patch\">" +
            _fsEscape(dictResult.sPatchUnifiedDiff) + "</pre>";
        var listFiles = dictResult.listFilesTouched || [];
        if (listFiles.length) {
            sSection += "<p class=\"council-hint\">Files touched: " +
                _fsEscape(listFiles.join(", ")) + "</p>";
        }
        var listDeviations = dictResult.listDeviationsFromPlan || [];
        if (listDeviations.length) {
            sSection += "<h5>Departures from the accepted plan</h5><ul>" +
                listDeviations.map(function (sEntry) {
                    return "<li>" + _fsEscape(String(sEntry)) + "</li>";
                }).join("") + "</ul>";
        }
        return sSection;
    }

    function _fsCandidatePlanBody(dictPlan) {
        /* The engine's real candidate shape (remediation R6): the
           synthesis result lives at dictCandidatePlan.dictResult, and
           the objection provenance lists ride beside it. Never a
           top-level plan-text field a fabricated record carried — no
           record no engine ever wrote. */
        var dictResult = dictPlan.dictResult || {};
        var sParts = "<p class=\"council-plan-summary\">" +
            _fsEscape(dictResult.sSummary || "") + "</p>";
        sParts += _fsPatchSection(dictResult);
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
         /* Charter clause 6's other half: a noted finding goes into
            the plan document as well as beside the gate, so it
            outlives the gate that showed it. */
         ["listNotedFindings",
          "Noted findings — recorded for attention, not decided"],
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

    async function _fsFetchPlanMarkdown() {
        /* The SERVER's bytes, always: the backend composer is the one
           authority on what the plan document says, and it watermarks
           an unaccepted draft in the document's own text. A second
           display-side composer lived here once and was a divergence
           waiting to happen. */
        var sUrl = _fsRoute(
            "/" + encodeURIComponent(_dictState.sActiveCampaignId)
            + "/plan.md") + _fsDirectoryQuery("?");
        try {
            return await VaibifyApi.fsGetText(sUrl);
        } catch (error) {
            VaibifyApp.fnShowToast(
                "The plan could not be fetched: " +
                (error.message || String(error)), "error");
            return "";
        }
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
            return _fsAcceptedPlanActions(dictCampaign);
        }
        /* Four peer-looking buttons, of which exactly ONE changed
           anything: Accept is a protocol transition, Reject is another,
           and Copy and Download export byte-identical markdown to two
           destinations. A researcher asked what the difference between
           the first three was, which is the question a flat row of
           equals invites (2026-08-29). The decisions lead and say what
           they lead to; the exports are demoted and labelled as copies. */
        return "<div class=\"council-plan-decide\">" +
            "<p class=\"council-hint\">Accepting ends this council and " +
            "unlocks <em>Implement plan with new council</em>, which " +
            "convenes a second council to turn the plan into a reviewed " +
            "patch. Rejecting ends it with no plan.</p>" +
            "<div class=\"council-plan-actions\">" +
            "<button type=\"button\" id=\"btnCouncilAcceptPlan\" " +
            "class=\"btn btn-primary\">Accept and save plan</button>" +
            "<button type=\"button\" id=\"btnCouncilRejectPlan\" " +
            "class=\"btn danger\">Reject</button>" +
            "</div>" + _fsPlanExportRow() + "</div>";
    }

    function _fsPlanExportRow() {
        /* Both send the SAME markdown; only the destination differs, so
           the destination is what the labels name. */
        return "<div class=\"council-plan-exports\">" +
            "<span class=\"council-hint\">Take a copy (changes " +
            "nothing):</span> " +
            "<button type=\"button\" id=\"btnCouncilCopyBrief\" " +
            "class=\"btn btn-small\">Copy to clipboard</button>" +
            "<button type=\"button\" id=\"btnCouncilDownloadPlan\" " +
            "class=\"btn btn-small\">Save as file</button>" +
            "</div>";
    }

    function _fsAcceptedPlanActions(dictCampaign) {
        /* An IMPLEMENTATION council's own accepted patch is the end of
           the line — it implements nothing further, and offering to
           would invite a council of councils nobody asked for. */
        if (dictCampaign.sCampaignKind === "implementation") {
            return "<p class=\"council-plan-accepted\">This patch was " +
                "accepted and saved. Apply it yourself with " +
                "<code>git apply</code> — vaibify never writes it into " +
                "your project.</p>";
        }
        if (dictCampaign.sState !== "planAccepted") {
            return "<p class=\"council-plan-accepted\">This council is " +
                "finished.</p>";
        }
        return "<p class=\"council-plan-accepted\">You accepted this " +
            "plan. A new council can implement it — it produces a " +
            "reviewed patch you apply by hand, never an applied " +
            "change.</p>" +
            _fsAcceptedPlanLocation() +
            "<div class=\"council-plan-actions\">" +
            "<button type=\"button\" id=\"btnCouncilImplementPlan\" " +
            "class=\"btn btn-primary\">Implement plan with new " +
            "council</button>" +
            "</div>" + _fsPlanExportRow();
    }

    function _fsAcceptedPlanLocation() {
        /* Where the file IS, on screen, for as long as the panel is
           open. The toast that used to be the only answer cleared in
           five seconds. */
        var sPath = _dictState.sLastAcceptedPlanPath ||
            _fsAcceptedPlanPathForActiveCampaign();
        if (!sPath) return "";
        return "<p class=\"council-plan-location\">Saved at " +
            "<code>" + _fsEscape(sPath) + "</code></p>";
    }

    function _fsAcceptedPlanPathForActiveCampaign() {
        /* From the LISTING, so a reload that cleared module state still
           knows where this campaign's plan landed. */
        var listMatched = _dictState.listSummaries.filter(
            function (dictSummary) {
                return dictSummary.sCampaignId ===
                    _dictState.sActiveCampaignId;
            });
        return (listMatched[0] || {}).sAcceptedPlanPath || "";
    }

    function _fnOpenImplementationForm() {
        /* The convene form, pre-seeded — NOT a one-click launch. The
           disclosure, the participants, the settings and the cost are
           the same consent surface every council passes through; an
           implementation council spends the same paid provider work,
           so it earns the same screen. */
        var dictCampaign = _dictState.dictCampaign || {};
        _dictState.dictImplementationSeed = {
            sSourceCampaignId: _dictState.sActiveCampaignId,
            sSourceCampaignName: dictCampaign.sCampaignName || "",
            listParticipants: (dictCampaign.listParticipants || []).map(
                function (dictParticipant) {
                    return {sProvider: dictParticipant.sProvider,
                            sRequestedModel:
                                dictParticipant.sRequestedModel,
                            sRole: ""};
                }),
        };
        _fnHideWorkspace();
        _fnOpenPlanningForm();
        _fnShowModal();
    }

    /* ------------------------------------------------------------------ */
    /* Ask the chairbot — a conversation, never a protocol turn           */
    /* ------------------------------------------------------------------ */

    function _fsChatTab() {
        var dictChat = _dictState.dictChat;
        if (!dictChat || !dictChat.bOpen) {
            return "<div class=\"council-chat\">" + _fsChatStaleNotice() +
                _fsChatClosedTab() + "</div>";
        }
        return "<div class=\"council-chat\">" +
            _fsChatStaleNotice() +
            _fsChatStatusLine(dictChat) +
            _fsChatTranscript(dictChat) +
            _fsChatComposer(dictChat) +
            "</div>";
    }

    function _fsChatStaleNotice() {
        if (!_dictState.sLastChatError) return "";
        return "<p class=\"council-chat-failure\">This conversation " +
            "could not be refreshed (" +
            _fsEscape(_dictState.sLastChatError) +
            "), so what is shown below may be out of date.</p>";
    }

    function _fsChatClosedTab() {
        /* The cost is stated BEFORE the button, not after the click.
           Opening builds a real container and every message spends the
           project's own provider subscription, so a researcher who has
           not been told that has not agreed to it. */
        return "" +
            "<p>Ask this council's chairbot about its work — the plan it " +
            "wrote, an objection it recorded, a question it held for " +
            "you.</p>" +
            "<p class=\"council-hint\">Opening a conversation builds a " +
            "disposable runner from the sealed snapshot this council " +
            "reviewed, and every message spends this project's provider " +
            "subscription. The chairbot answers only: " +
            "it cannot accept a plan, clear an objection or start a " +
            "round. An idle conversation lets its runner rest — your " +
            "login never sits in an idle container — and your next " +
            "question wakes it; the conversation itself stays.</p>" +
            "<button type=\"button\" id=\"btnCouncilChatOpen\" " +
            "class=\"btn\">Open conversation</button>";
    }

    function _fsChatStatusLine(dictChat) {
        var sModel = dictChat.sResolvedModel
            ? "chairbot running " + _fsEscape(dictChat.sResolvedModel)
            : "chairbot model not yet recorded";
        var sBusy = dictChat.sState === "answering"
            ? "<span class=\"council-chip council-chip-deliberating\">" +
              "answering…</span> "
            : "";
        var sWindow = dictChat.sState === "resting"
            ? "resting — your next question wakes the chairbot"
            : "the runner rests after " + Math.round(
                dictChat.iIdleSecondsRemaining / 60) +
                " min idle (the conversation stays)";
        return "<p class=\"council-chat-status\">" + sBusy + sModel +
            " · " + dictChat.iMessagesRemaining + " messages left · " +
            sWindow + "</p>" + _fsChatFailure(dictChat);
    }

    function _fsChatFailure(dictChat) {
        if (!dictChat.sFailureReason) return "";
        return "<p class=\"council-chat-failure\">" +
            _fsEscape(dictChat.sFailureReason) + "</p>";
    }

    function _fsChatTranscript(dictChat) {
        var listMessages = dictChat.listMessages || [];
        if (!listMessages.length) {
            return "<p class=\"council-hint\">Nothing asked yet.</p>";
        }
        return "<div class=\"council-chat-transcript\">" +
            listMessages.map(function (dictMessage) {
                return "<div class=\"council-chat-message " +
                    "council-chat-" + _fsEscape(dictMessage.sAuthor) +
                    "\"><span class=\"council-chat-author\">" +
                    _fsEscape(dictMessage.sAuthor) + "</span>" +
                    "<div class=\"council-chat-text\">" +
                    _fsEscape(dictMessage.sText) + "</div></div>";
            }).join("") + "</div>";
    }

    function _fsChatComposer(dictChat) {
        /* "resting" composes like "ready": the ask is what wakes the
           runner, so a disabled box would make resting a dead end. */
        var bBlocked = (dictChat.sState !== "ready"
            && dictChat.sState !== "resting")
            || dictChat.iMessagesRemaining <= 0;
        return "<div class=\"council-composer\">" +
            "<textarea id=\"councilChatQuestion\" rows=\"3\" " +
            (bBlocked ? "disabled " : "") +
            "placeholder=\"Ask the chairbot about this council's " +
            "work\"></textarea>" +
            "<div class=\"council-plan-actions\">" +
            "<button type=\"button\" id=\"btnCouncilChatAsk\" " +
            "class=\"btn\"" + (bBlocked ? " disabled" : "") + ">Ask</button>" +
            "<button type=\"button\" id=\"btnCouncilChatClose\" " +
            "class=\"btn\">Close conversation</button>" +
            "</div></div>";
    }

    async function _fnOpenChat() {
        await _fbPostChatAction("/chat/open");
    }

    async function _fnAskChairbot() {
        var sQuestion = _fsReadValue("councilChatQuestion");
        if (!sQuestion) return;
        var bAccepted = await _fbPostChatAction(
            "/chat/ask", {sQuestionText: sQuestion});
        if (bAccepted) {
            /* The question was delivered; keeping it in the box (or in
               the draft store, which would refill the box on the next
               render) would resubmit-bait every later click. */
            delete _dictDraftFields.dictById.councilChatQuestion;
            _fnPersistDraftFields();
            var elField = document.getElementById("councilChatQuestion");
            if (elField) elField.value = "";
        }
    }

    async function _fnCloseChat() {
        await _fbPostChatAction("/chat/close");
    }

    async function _fbPostChatAction(sSuffix, dictBody) {
        /* Never optimistic: the transcript on screen is refetched from
           the server after the action, so a message the backend
           refused never appears as one it accepted. Same one-action
           discipline as _fbPostAction: paid work is never
           double-submitted by a double-click. */
        if (_dictState.bActionPending) return false;
        _dictState.bActionPending = true;
        _fnRenderWorkspace();
        var bAccepted = false;
        try {
            var sPath = _fsRoute(
                "/" + _dictState.sActiveCampaignId + sSuffix)
                + _fsDirectoryQuery("?");
            if (dictBody === undefined) {
                await VaibifyApi.fdictPostRaw(sPath);
            } else {
                await VaibifyApi.fdictPost(sPath, dictBody);
            }
            bAccepted = true;
        } catch (error) {
            VaibifyApp.fnShowToast(
                "Chairbot: " + (error.message || String(error)), "error");
        } finally {
            _dictState.bActionPending = false;
        }
        await _fnLoadChatQuietly();
        _fnRenderWorkspace();
        _fnStartPolling();
        return bAccepted;
    }

    async function _fnLoadChatQuietly() {
        try {
            _dictState.dictChat = await VaibifyApi.fdictGet(
                _fsRoute("/" + _dictState.sActiveCampaignId + "/chat") +
                _fsDirectoryQuery("?"));
            _dictState.sLastChatError = "";
        } catch (error) {
            /* Recorded and RENDERED, not swallowed: a conversation
               whose poll is failing must not keep displaying its last
               good answer as though it were current. */
            _dictState.sLastChatError = error.message || String(error);
        }
    }

    /* ------------------------------------------------------------------ */
    /* Workspace event binding                                            */
    /* ------------------------------------------------------------------ */

    function _fnBindWorkspace(dictCampaign) {
        document.querySelectorAll(".council-tab").forEach(function (elTab) {
            elTab.addEventListener("click", function () {
                _dictState.sActiveTab = elTab.getAttribute("data-tab");
                _fnRenderWorkspace();
                /* The conversation is not polled while its tab is
                   hidden, so opening the tab has to fetch it once —
                   otherwise the first thing a returning researcher
                   sees is a stale transcript that only corrects itself
                   on the next tick. */
                if (_dictState.sActiveTab === "chat") {
                    _fnLoadChatQuietly().then(_fnRenderWorkspace);
                }
            });
        });
        _fnBindElement("btnCouncilStop", _fnStopCouncil);
        _fnBindElement("btnCouncilPause", _fnPauseCouncil);
        _fnBindElement("btnCouncilOpenPlanTab", function () {
            _fnSelectTab("plan");
        });
        _fnBindElement("btnCouncilResume", _fnResumeCouncil);
        _fnBindElement("btnCouncilRetry", _fnRetryCouncil);
        _fnBindElement("btnCouncilAnswer", _fnAnswerQuestion);
        _fnBindElement("btnCouncilGrantRound", _fnGrantResolutionRound);
        _fnBindElement("btnCouncilResolveOverride", _fnResolveObjections);
        _fnBindElement("btnCouncilReject", _fnRejectCandidate);
        _fnBindElement("btnCouncilImplementPlan",
                       _fnOpenImplementationForm);
        _fnBindElement("btnCouncilChatOpen", _fnOpenChat);
        _fnBindElement("btnCouncilChatAsk", _fnAskChairbot);
        _fnBindElement("btnCouncilChatClose", _fnCloseChat);
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
        var listBlocks = Array.prototype.slice.call(
            document.querySelectorAll(".council-decision"));
        if (!listBlocks.length) {
            var sAnswer = _fsReadValue("councilAnswer");
            if (!sAnswer) return;
            var bSent = await _fbPostAction(
                "/" + _dictState.sActiveCampaignId + "/respond",
                {sResponseText: sAnswer});
            if (bSent) {
                delete _dictDraftFields.dictById.councilAnswer;
                _fnPersistDraftFields();
            }
            return;
        }
        var listDecisionAnswers = [];
        var listUnanswered = [];
        listBlocks.forEach(function (elementBlock, iIndex) {
            var elementAnswer = elementBlock.querySelector(
                ".council-decision-answer");
            var sText = (elementAnswer && elementAnswer.value || "").trim();
            if (!sText) {
                listUnanswered.push(iIndex + 1);
                return;
            }
            listDecisionAnswers.push({
                sDecisionId: elementBlock.getAttribute("data-decision-id"),
                listQuestionIds: Array.prototype.slice.call(
                    elementBlock.querySelectorAll("[data-question-id]")
                ).map(function (elementQuestion) {
                    return elementQuestion.getAttribute("data-question-id");
                }),
                sAnswerText: sText
            });
        });
        /* Refuse a partial submission rather than sending it. An
           unanswered decision cannot be distinguished downstream from
           one the researcher deliberately declined, so sending eight of
           sixteen silently drops the other eight — and the gate closes,
           so there is no second chance at them. */
        if (listUnanswered.length) {
            _fnShowGateNotice(
                "Answer every decision before sending — " +
                (listUnanswered.length === 1
                    ? "decision " + listUnanswered[0] + " is empty."
                    : listUnanswered.length + " are still empty (" +
                        listUnanswered.join(", ") + ").") +
                " The gate closes on send, so an empty one is a question " +
                "the council never gets an answer to.");
            return;
        }
        var bRecorded = await _fbPostAction(
            "/" + _dictState.sActiveCampaignId + "/respond",
            {sResponseText: "(composed from per-decision answers)",
             listDecisionAnswers: listDecisionAnswers});
        if (bRecorded) {
            /* The gate closed on these answers; kept drafts would
               refill the NEXT gate's boxes with answers to questions
               it never asked. */
            _dictDraftFields.listDecisionAnswers = [];
            _fnForgetPersistedDraft();
        }
    }

    function _fnShowGateNotice(sMessage) {
        var elementNotice = document.getElementById("councilGateNotice");
        if (!elementNotice) return;
        elementNotice.textContent = sMessage;
        elementNotice.style.display = "block";
    }

    /* Each exhausted-round exit posts the ENGINE'S own transition
       (remediation R6) — its dedicated route, never a respond message
       the backend would have to parse back into an intent. */

    async function _fnGrantResolutionRound() {
        var iRounds = parseInt(
            _fsReadValue("councilGrantRounds") || "1", 10);
        await _fbPostAction(
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
        await _fbPostAction(
            "/" + _dictState.sActiveCampaignId + "/resolve-objections",
            {dictDispositionByObjectionId: dictDispositions});
    }

    async function _fnRejectCandidate() {
        await _fbPostAction(
            "/" + _dictState.sActiveCampaignId + "/reject-candidate",
            {sReasonText: _fsReadValue("councilRejectReason") || ""});
    }

    async function _fnStopCouncil() {
        await _fbPostAction(
            "/" + _dictState.sActiveCampaignId + "/request-stop", undefined);
    }

    async function _fnPauseCouncil() {
        /* Its own route, not a flag on the stop: the two have opposite
           consequences, and a shared endpoint is how a mis-set boolean
           archives a council somebody meant to keep. */
        await _fbPostAction(
            "/" + _dictState.sActiveCampaignId + "/pause", undefined);
    }

    async function _fnRetryCouncil() {
        var bClearsStop = Boolean(
            (_dictState.dictCampaign || {}).bStopRequested);
        await _fbPostAction(
            "/" + _dictState.sActiveCampaignId + "/retry",
            {bClearStopRequest: bClearsStop});
    }

    async function _fnResumeCouncil() {
        /* The stop-clear choice is surfaced ON the button label
           (continuation plan 4.2.5): a record carrying bStopRequested
           resumes only with the clear made explicit, and the backend
           records that clear as a researcher decision. */
        var bClearsStop = Boolean(
            (_dictState.dictCampaign || {}).bStopRequested);
        await _fbPostAction(
            "/" + _dictState.sActiveCampaignId + "/resume",
            {bClearStopRequest: bClearsStop});
    }

    async function _fnAcceptPlan() {
        /* No body (remediation R3): the backend accepts the council's
           own server-held candidate through the engine's planReady
           gate; the review gate is the researcher READING it here.

           The directory rides it like every other campaign action —
           this was the last call site that forgot, and on a container
           tracking nine directories "Accept and save plan" answered
           "a council needs to be told which one it is about"
           (2026-08-29). The server now reads the directory off the
           campaign record, so this query is agreement, not the fix. */
        try {
            var dictResult = await VaibifyApi.fdictPostRaw(
                _fsRoute("/" + _dictState.sActiveCampaignId
                    + "/accept-plan")
                + _fsDirectoryQuery("?"));
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
        /* The path is REMEMBERED, not just announced. A toast clears in
           five seconds and a researcher who blinked had no way to find
           where their accepted plan lived (2026-08-30). The panel now
           renders it, and the listing carries sAcceptedPlanPath so
           "View past councils" can show it for every accepted plan
           long after this toast is gone. */
        _dictState.sLastAcceptedPlanPath = dictResult.sLocalPlanPath || "";
        VaibifyApp.fnShowToast(
            "Plan saved" + sWhere + ": " +
            (dictResult.sLocalPlanPath || ""), "success");
    }

    async function _fbPostAction(sPath, dictBody) {
        /* The directory rides EVERY action, not just the reads. A
           project tracking several directories with no workflow open
           resolves to no repository, and the server rightly refuses to
           guess — so an action that omitted it was refused while the
           panel beside it polled happily.

           One action at a time: a double-click on a council control is
           a second submission of PAID provider work, so a click while
           one is pending is dropped, and the pending flag rides module
           state so every re-render keeps the buttons disabled until
           the request settles either way. */
        if (_dictState.bActionPending) return;
        _dictState.bActionPending = true;
        _fnRenderWorkspace();
        var sUrl = _fsRoute(sPath) + _fsDirectoryQuery("?");
        try {
            if (dictBody === undefined) {
                await VaibifyApi.fdictPostRaw(sUrl);
            } else {
                await VaibifyApi.fdictPost(sUrl, dictBody);
            }
        } catch (error) {
            VaibifyApp.fnShowToast(
                "Action failed: " + (error.message || String(error)),
                "error");
            return false;
        } finally {
            _dictState.bActionPending = false;
            _fnRenderWorkspace();
        }
        await _fnReloadActiveCampaign();
        _fnStartPolling();
        return true;
    }

    async function _fnCopyBrief(dictCampaign) {
        void dictCampaign;
        var sBrief = await _fsFetchPlanMarkdown();
        if (navigator.clipboard && sBrief) {
            navigator.clipboard.writeText(sBrief);
            VaibifyApp.fnShowToast("Implementation brief copied.", "info");
        }
    }

    async function _fnDownloadPlan(dictCampaign) {
        /* Downloads land on the computer the browser runs on, which in a
           remote session is NOT the execution host — say so (section
           21). */
        void dictCampaign;
        var sText = await _fsFetchPlanMarkdown();
        if (!sText) return;
        var elLink = document.createElement("a");
        elLink.href = "data:text/markdown;charset=utf-8," +
            encodeURIComponent(sText);
        elLink.download = "council-plan.md";
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

    function _fsDirectoryQuery(sJoiner, sCampaignIdForLookup) {
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
        if (!sRepoPath) {
            /* Nothing loaded yet — the first fetch of a campaign just
               picked out of the list. The SUMMARY carries the repo the
               record itself records, keyed on the id being fetched: the
               active id is not set until this call returns. */
            var sListed = _fsDirectoryForListedCampaign(
                sCampaignIdForLookup || _dictState.sActiveCampaignId);
            return sListed
                ? sJoiner + "sProjectDirectory=" + encodeURIComponent(sListed)
                : "";
        }
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

    function _fnReturnToTasksConfirmingLoss() {
        /* Same protection as Cancel, different destination: the draft
           is lost either way, so the question is asked either way. */
        var fnGoBack = function () {
            _dictState.dictImplementationSeed = null;
            _fnRenderChooser(false);
        };
        if (!_fbConveneFormHasWork()) {
            fnGoBack();
            return;
        }
        VaibifyApp.fnShowConfirmModal(
            "Go back and discard this council?",
            "The question you have written will be lost. Your "
            + "participants and settings will reset to their defaults.",
            fnGoBack);
    }

    function _fnShowWorkspaceModalClose() {
        /* Closing the workspace is NAVIGATION back to the chooser, not
           a dead end: hiding it and leaving the campaign active meant
           the next toolbar click reopened the same workspace, with no
           path back to "Plan a change" (2026-08-27). A campaign that
           is neither live nor waiting on the researcher is also
           released as the active one, so a dead campaign cannot
           recapture the button. */
        _fnHideWorkspace();
        if (!_fbActiveCampaignIsLive() && !_fbActiveCampaignNeedsHuman()
                && !_fbActiveCampaignPlanReady()) {
            _dictState.sActiveCampaignId = "";
            _dictState.dictCampaign = null;
            _fnRenderToolbarButton();
        }
        _fnOpenCreationChooser();
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
        fnSetEventsForTest: function (listEvents) {
            _dictState.listEvents.length = 0;
            listEvents.forEach(function (dictEvent) {
                _dictState.listEvents.push(dictEvent);
            });
        },
        /* Draft-persistence seams. The storage path runs inside the
           render, so a browser test needs to drive its three steps
           separately to prove each one rather than one composite. */
        fnHarvestDraftsForTest: function () {
            _fnHarvestDraftInputs(
                document.getElementById("agentCouncilWorkspaceBody"));
        },
        fnResetDraftsForTest: _fnResetDraftFields,
        /* Drives the REAL adoption path, not the persistence helper it
           calls: a seam onto _fnAdoptPersistedDraft would prove the
           function works while the call site could be deleted with the
           test still passing (observed 2026-08-29). */
        fnAdoptCampaignForTest: function (sCampaignId) {
            _fnAdoptCampaign(sCampaignId, _dictState.dictCampaign);
        },
        fdictDraftFieldsForTest: function () { return _dictDraftFields; },
        fsActiveCampaignIdForTest: function () {
            return _dictState.sActiveCampaignId;
        },
        /* Chooser seams: the views are rendered from the LISTING,
           so a test needs to supply one and re-render, and needs to
           read back the seed an implement row plants. */
        fnSetSummariesForTest: function (listSummaries) {
            _dictState.listSummaries = listSummaries;
        },
        fnRenderChooserForTest: function (bListingStillLoading) {
            _fnRenderChooser(Boolean(bListingStillLoading));
        },
        fdictImplementationSeedForTest: function () {
            return _dictState.dictImplementationSeed;
        },
        fnSelectTabForTest: function (sTab) {
            _dictState.sActiveTab = sTab;
            _fnRenderWorkspace();
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
