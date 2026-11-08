/* Vaibify — How a project opens: Default, Latest, or Choose chats */

var VaibifyEntryChoice = (function () {
    "use strict";

    /* Clicking a project asks how its terminals and agents start:

       - Default: the project's first-entry view, with no chat resumed;
       - Latest: the environment's panes and tabs exactly as they were
         left, each agent chat resumed, and this project's own view;
       - Choose chats: past conversations of this project, each placed
         in a pane, sharing a pane as tabs.

       The question is skipped on exactly one path: Admin > Projects in
       this page load keeps the environment's terminals live, so they
       stay as they are and only the project's own view is restored.

       Every chat offered was found in the agent's own records, which
       anything running as the researcher can write -- an in-container
       agent included -- so each is shown as discovered data (provider,
       date, first prompt), never as verified, and a chat that would
       resume with permission checks bypassed needs an answer before
       Continue. A restore never claims a status: steps, lights and
       level cells come from the poll as they always do. */

    var I_PANE_COUNT = 5;
    var S_DIALOG_ID = "modalEntryChoice";
    var S_DISCOVERED_NOTICE = "These chats were found in the agents' " +
        "own records on this machine. vaibify has not verified who " +
        "wrote them: check the provider, date and first prompt before " +
        "resuming one.";
    var DICT_PROVIDER_LABELS = {
        claude: "Claude Code", codex: "Codex", gemini: "Gemini"};
    var _dictDialog = null;

    function fsEscape(valueText) {
        return VaibifyUtilities.fnEscapeHtml(
            valueText === undefined || valueText === null ? "" : String(valueText));
    }

    function _fnSetEntryState(sState) {
        if (sState) {
            document.body.dataset.entryState = sState;
        } else {
            delete document.body.dataset.entryState;
        }
    }

    /* --- Entry --- */

    async function fnEnterProject(sContainerId, dictCard) {
        if (VaibifyTerminal.fbHasOpenPanes()) {
            /* Ruling 1: the environment's terminals are live. */
            await _fnEnter(sContainerId, dictCard, {sChoice: "kept"});
            return;
        }
        _fnSetEntryState("reading");
        var dictEnvironment;
        try {
            dictEnvironment = await VaibifyDashboardMemory.fdictReadRecord(
                "environment", {sContainerId: sContainerId});
        } catch (error) {
            dictEnvironment = {iRevision: 0, dictState: {},
                               sReadError: VaibifyDiagnosis.fsExplainError(error)};
        }
        _fnOpenDialog(sContainerId, dictCard, dictEnvironment || {});
    }

    /* --- The saved chats of the environment record --- */

    function _flistSavedTabs(dictEnvironment) {
        var listTabs = [];
        var dictTerminals = (dictEnvironment.dictState || {}).dictTerminals || {};
        (dictTerminals.listPanes || []).forEach(function (dictPane, iPane) {
            (dictPane.listTabs || []).forEach(function (dictTab, iTab) {
                if (dictTab && dictTab.dictChat) {
                    listTabs.push({iPane: iPane, iTab: iTab,
                                   sKey: iPane + ":" + iTab,
                                   dictChat: dictTab.dictChat});
                }
            });
        });
        return listTabs;
    }

    function _fbHasSavedLayout(dictEnvironment) {
        var dictTerminals = (dictEnvironment.dictState || {}).dictTerminals;
        return dictEnvironment.iRevision > 0 && !dictEnvironment.bUnreadable &&
            Boolean(dictTerminals && (dictTerminals.listPanes || []).length);
    }

    /* --- The dialog --- */

    function _fnOpenDialog(sContainerId, dictCard, dictEnvironment) {
        _fnCloseDialog();
        var bLatest = _fbHasSavedLayout(dictEnvironment);
        _dictDialog = {
            sContainerId: sContainerId,
            dictCard: dictCard,
            dictEnvironment: dictEnvironment,
            listSavedTabs: _flistSavedTabs(dictEnvironment),
            sChoice: bLatest ? "latest" : "default",
            bLatestAvailable: bLatest,
            dictCatalog: null,
            sCatalogError: "",
            dictAnswers: {},
            dictPlacements: {},
            dictInferredPicks: {},
        };
        var elModal = document.createElement("div");
        elModal.id = S_DIALOG_ID;
        elModal.className = "modal-overlay";
        elModal.style.display = "flex";
        elModal.innerHTML = '<div class="modal entry-choice-modal">' +
            '<h2>Open ' + fsEscape(dictCard.sName) + '</h2>' +
            '<div class="entry-choice-body"></div>' +
            '<div class="modal-actions">' +
            '<button class="btn" id="btnEntryCancel">Cancel</button>' +
            '<button class="btn btn-primary" id="btnEntryContinue">' +
            'Continue</button></div></div>';
        document.body.appendChild(elModal);
        elModal.querySelector("#btnEntryCancel").addEventListener(
            "click", function () {
                _fnCloseDialog();
                _fnSetEntryState("");
            });
        elModal.querySelector("#btnEntryContinue").addEventListener(
            "click", _fnContinue);
        elModal.addEventListener("change", _fnHandleDialogChange);
        _fnSetEntryState("asking");
        _fnRenderDialog();
        if (_fbChoiceNeedsCatalog()) _fnLoadCatalog();
    }

    function _fbChoiceNeedsCatalog() {
        /* Listing chats runs a program where the transcripts live, so
           it is asked only when the answer is needed: Choose chats
           always, Latest only when its layout held a chat. */
        if (_dictDialog.dictCatalog || _dictDialog.sCatalogError) return false;
        if (_dictDialog.sChoice === "choose") return true;
        return _dictDialog.sChoice === "latest" &&
            _dictDialog.listSavedTabs.length > 0;
    }

    function _fnCloseDialog() {
        var elModal = document.getElementById(S_DIALOG_ID);
        if (elModal) elModal.remove();
    }

    function _fsOption(sValue, sLabel, sDetail, bDisabled) {
        return '<label class="entry-choice-option' +
            (bDisabled ? ' entry-choice-option--disabled' : '') + '">' +
            '<input type="radio" name="entryChoice" value="' + sValue + '"' +
            (_dictDialog.sChoice === sValue ? " checked" : "") +
            (bDisabled ? " disabled" : "") + '> <strong>' + sLabel +
            '</strong><span class="entry-choice-detail">' + sDetail +
            '</span></label>';
    }

    function _fsLatestDetail() {
        if (_dictDialog.dictEnvironment.sReadError) {
            return "The saved layout could not be read: " +
                fsEscape(_dictDialog.dictEnvironment.sReadError);
        }
        if (_dictDialog.dictEnvironment.bUnreadable) {
            return "The saved layout of this environment could not be " +
                "read, so there is nothing to restore.";
        }
        if (!_dictDialog.bLatestAvailable) {
            return "Nothing has been saved for this environment yet.";
        }
        return "The panes and tabs as you left them, each agent chat " +
            "resumed, and this project's own view.";
    }

    function _fnRenderDialog() {
        var elBody = document.querySelector("#" + S_DIALOG_ID + " .entry-choice-body");
        if (!elBody) return;
        elBody.innerHTML =
            _fsOption("default", "Default", "This project's first view, " +
                      "with no agent chat resumed.", false) +
            _fsOption("latest", "Latest", _fsLatestDetail(),
                      !_dictDialog.bLatestAvailable) +
            _fsOption("choose", "Choose chats", "Pick past conversations " +
                      "of this project and place each in a pane.", false) +
            '<div class="entry-choice-chats">' + _fsChatsSection() + '</div>';
        _fnUpdateContinue();
    }

    function _fsChatsSection() {
        if (_dictDialog.sChoice === "default") return "";
        if (_dictDialog.sChoice === "latest" &&
                !_dictDialog.listSavedTabs.length) {
            return _fsLatestRows();
        }
        if (_dictDialog.sCatalogError) {
            return '<p class="entry-choice-warning">The past chats could not ' +
                'be listed: ' + fsEscape(_dictDialog.sCatalogError) + '</p>';
        }
        if (!_dictDialog.dictCatalog) {
            return '<p class="entry-choice-detail">Looking for past chats…</p>';
        }
        var sRows = _dictDialog.sChoice === "latest" ?
            _fsLatestRows() : _fsChooseRows();
        return '<p class="entry-choice-notice">' + S_DISCOVERED_NOTICE +
            '</p>' + sRows;
    }

    /* --- One chat's description and its permission gate --- */

    function _fsDescribeChat(dictChat, dictFound) {
        var iEpoch = (dictFound || {}).iModifiedEpoch || 0;
        var sWhen = iEpoch ? new Date(iEpoch * 1000).toLocaleString() : "";
        var sPrompt = (dictFound || {}).sFirstPrompt || "";
        return '<span class="entry-choice-provider">' +
            fsEscape(DICT_PROVIDER_LABELS[dictChat.sProvider] ||
                     dictChat.sProvider) + '</span>' +
            (sWhen ? ' <span class="entry-choice-when">last active ' +
             fsEscape(sWhen) + '</span>' : '') +
            (sPrompt ? '<span class="entry-choice-prompt">“' +
             fsEscape(sPrompt) + '”</span>' : '') +
            '<span class="entry-choice-where">' + fsEscape(dictChat.sCwd) +
            '</span>';
    }

    function _fsGate(sKey, dictPlan) {
        var listNotes = [];
        if (dictPlan && dictPlan.listDroppedArguments &&
                dictPlan.listDroppedArguments.length) {
            listNotes.push('<span class="entry-choice-detail">Not replayed: ' +
                fsEscape(dictPlan.listDroppedArguments.join(" ")) + '</span>');
        }
        if (!dictPlan || !dictPlan.dictPermission.bBypass) {
            return listNotes.join("");
        }
        var sAnswer = _dictDialog.dictAnswers[sKey] || "";
        var sSources = dictPlan.dictPermission.listBypassSources.join(", ");
        return listNotes.join("") +
            '<div class="entry-choice-gate">This chat ran with permission ' +
            'checks bypassed (from its ' + fsEscape(sSources) + '). ' +
            '<label><input type="radio" name="gate:' + fsEscape(sKey) +
            '" value="bypass"' + (sAnswer === "bypass" ? " checked" : "") +
            '> Resume it that way; I understand the agent will act ' +
            'without asking</label><label><input type="radio" name="gate:' +
            fsEscape(sKey) + '" value="standard"' +
            (sAnswer === "standard" ? " checked" : "") + '> Resume with ' +
            'standard permissions instead</label></div>';
    }

    /* --- Latest --- */

    function _fdictResolved(dictChat) {
        var dictResolved = (_dictDialog.dictCatalog || {}).dictResolved || {};
        return dictResolved[dictChat.sProvider + ":" + dictChat.sSessionId];
    }

    function _flistCandidatesFor(dictChat) {
        /* An unidentified chat may be any conversation of its provider
           launched in its directory since it started. */
        return ((_dictDialog.dictCatalog || {}).listCatalog || []).filter(
            function (dictEntry) {
                return dictEntry.sProvider === dictChat.sProvider &&
                    dictEntry.sCwd === dictChat.sCwd &&
                    dictEntry.iModifiedEpoch >= (dictChat.iStartedEpoch || 0) &&
                    dictEntry.dictPlan;
            });
    }

    function _fsInferredRow(dictTab) {
        var sPick = _dictDialog.dictInferredPicks[dictTab.sKey] || "";
        var sOptions = '<option value="">Open it as a plain shell</option>';
        _flistCandidatesFor(dictTab.dictChat).forEach(function (dictEntry) {
            var sValue = dictEntry.sProvider + ":" + dictEntry.sSessionId;
            sOptions += '<option value="' + fsEscape(sValue) + '"' +
                (sPick === sValue ? " selected" : "") + '>' +
                fsEscape(new Date(dictEntry.iModifiedEpoch * 1000)
                    .toLocaleString() + " — " + dictEntry.sFirstPrompt) +
                '</option>';
        });
        var dictPicked = _fdictCatalogEntry(sPick);
        return '<span class="entry-choice-warning">Chat not identified: ' +
            'vaibify cannot tell which conversation this was.</span> ' +
            '<label>Choose <select name="inferred:' + fsEscape(dictTab.sKey) +
            '">' + sOptions + '</select></label>' +
            (dictPicked ? _fsGate(dictTab.sKey, dictPicked.dictPlan) : '');
    }

    function _fsLatestRow(dictTab) {
        var sWhere = '<span class="entry-choice-place">Pane ' +
            (dictTab.iPane + 1) + ', tab ' + (dictTab.iTab + 1) + '</span> ';
        var dictChat = dictTab.dictChat;
        if (dictChat.sIdentityClass === "inferred") {
            return '<li>' + sWhere + _fsDescribeChat(dictChat, null) +
                _fsInferredRow(dictTab) + '</li>';
        }
        var dictResolved = _fdictResolved(dictChat);
        if (!dictResolved || !dictResolved.bTranscriptFound ||
                !dictResolved.dictPlan) {
            return '<li>' + sWhere + _fsDescribeChat(dictChat, null) +
                '<span class="entry-choice-warning">No record of this ' +
                'conversation was found; the tab opens as a plain shell.' +
                '</span></li>';
        }
        var sCaveat = dictChat.sIdentityClass === "launched" ?
            '<span class="entry-choice-detail">Started as this chat; a ' +
            'switch inside ' + fsEscape(DICT_PROVIDER_LABELS[dictChat.sProvider]) +
            ' cannot be detected.</span>' : '';
        return '<li>' + sWhere + _fsDescribeChat(dictChat, dictResolved) +
            sCaveat + _fsGate(dictTab.sKey, dictResolved.dictPlan) + '</li>';
    }

    function _fsLatestRows() {
        if (!_dictDialog.listSavedTabs.length) {
            return '<p class="entry-choice-detail">No agent chat was running ' +
                'when this environment was left; its panes open as plain ' +
                'shells.</p>';
        }
        return '<ul class="entry-choice-list">' +
            _dictDialog.listSavedTabs.map(_fsLatestRow).join("") + '</ul>';
    }

    /* --- Choose chats --- */

    function _fdictCatalogEntry(sKey) {
        if (!sKey) return null;
        var listCatalog = (_dictDialog.dictCatalog || {}).listCatalog || [];
        for (var i = 0; i < listCatalog.length; i++) {
            if (listCatalog[i].sProvider + ":" + listCatalog[i].sSessionId === sKey) {
                return listCatalog[i];
            }
        }
        return null;
    }

    function _fsPaneSelect(sKey) {
        var iPane = _dictDialog.dictPlacements[sKey];
        var sOptions = '<option value="">Pane…</option>';
        for (var i = 1; i <= I_PANE_COUNT; i++) {
            sOptions += '<option value="' + i + '"' +
                (iPane === i ? " selected" : "") + '>Pane ' + i + '</option>';
        }
        return '<select name="pane:' + fsEscape(sKey) + '">' + sOptions +
            '</select>';
    }

    function _fsChooseRows() {
        var listCatalog = (_dictDialog.dictCatalog || {}).listCatalog || [];
        var listUsable = listCatalog.filter(function (d) { return d.dictPlan; });
        if (!listUsable.length) {
            return '<p class="entry-choice-detail">No past chat of this ' +
                'project was found.</p>';
        }
        return '<ul class="entry-choice-list">' + listUsable.map(
            function (dictEntry) {
                var sKey = dictEntry.sProvider + ":" + dictEntry.sSessionId;
                var bChosen = _dictDialog.dictPlacements[sKey] !== undefined;
                return '<li><label><input type="checkbox" name="choose:' +
                    fsEscape(sKey) + '"' + (bChosen ? " checked" : "") + '> ' +
                    _fsDescribeChat(dictEntry, dictEntry) + '</label>' +
                    (bChosen ? _fsPaneSelect(sKey) +
                     _fsGate(sKey, dictEntry.dictPlan) : '') + '</li>';
            }).join("") + '</ul>';
    }

    /* --- Answers and the Continue gate --- */

    function _fnHandleDialogChange(event) {
        var elInput = event.target;
        var sName = elInput.name || "";
        if (sName === "entryChoice") {
            _dictDialog.sChoice = elInput.value;
            if (_fbChoiceNeedsCatalog()) _fnLoadCatalog();
        } else if (sName.indexOf("gate:") === 0) {
            _dictDialog.dictAnswers[sName.slice(5)] = elInput.value;
        } else if (sName.indexOf("choose:") === 0) {
            var sKey = sName.slice(7);
            if (elInput.checked) {
                _dictDialog.dictPlacements[sKey] = null;
            } else {
                delete _dictDialog.dictPlacements[sKey];
            }
        } else if (sName.indexOf("pane:") === 0) {
            _dictDialog.dictPlacements[sName.slice(5)] =
                elInput.value ? parseInt(elInput.value, 10) : null;
        } else if (sName.indexOf("inferred:") === 0) {
            _dictDialog.dictInferredPicks[sName.slice(9)] = elInput.value;
        }
        _fnRenderDialog();
    }

    function _fbGateAnswered(sKey, dictPlan) {
        return !dictPlan || !dictPlan.dictPermission.bBypass ||
            Boolean(_dictDialog.dictAnswers[sKey]);
    }

    function _fsWhyContinueIsDisabled() {
        if (_dictDialog.sChoice === "default") return "";
        var bCatalogNeeded = _dictDialog.sChoice === "choose" ||
            _dictDialog.listSavedTabs.length > 0;
        if (bCatalogNeeded && !_dictDialog.dictCatalog &&
                !_dictDialog.sCatalogError) {
            return "Still looking for past chats.";
        }
        var iUnanswered = 0;
        var iUnplaced = 0;
        if (_dictDialog.sChoice === "latest") {
            _dictDialog.listSavedTabs.forEach(function (dictTab) {
                var dictPlan = _fdictPlanForSavedTab(dictTab);
                if (!_fbGateAnswered(dictTab.sKey, dictPlan)) iUnanswered += 1;
            });
        } else {
            Object.keys(_dictDialog.dictPlacements).forEach(function (sKey) {
                if (!_dictDialog.dictPlacements[sKey]) iUnplaced += 1;
                var dictEntry = _fdictCatalogEntry(sKey);
                if (!_fbGateAnswered(sKey, dictEntry && dictEntry.dictPlan)) {
                    iUnanswered += 1;
                }
            });
        }
        var listReasons = [];
        if (iUnplaced) {
            listReasons.push(iUnplaced + (iUnplaced === 1 ? " chosen chat is" :
                " chosen chats are") + " not placed in a pane.");
        }
        if (iUnanswered) {
            listReasons.push(iUnanswered + (iUnanswered === 1 ? " chat needs" :
                " chats need") + " an answer about bypassed permissions.");
        }
        return listReasons.join(" ");
    }

    function _fnUpdateContinue() {
        var elContinue = document.getElementById("btnEntryContinue");
        if (!elContinue) return;
        var sWhy = _fsWhyContinueIsDisabled();
        elContinue.disabled = Boolean(sWhy);
        elContinue.title = sWhy;
    }

    async function _fnLoadCatalog() {
        var dictDialog = _dictDialog;
        var listRequested = dictDialog.listSavedTabs.map(function (dictTab) {
            return dictTab.dictChat;
        }).filter(function (dictChat) { return dictChat.sSessionId; });
        try {
            dictDialog.dictCatalog = await VaibifyApi.fdictPost(
                "/api/dashboard-state/" +
                encodeURIComponent(dictDialog.sContainerId) + "/chat-catalog",
                {sProjectRepoPath: dictDialog.dictCard.sRepoPath || "",
                 listRequested: listRequested});
        } catch (error) {
            dictDialog.sCatalogError = VaibifyDiagnosis.fsExplainError(error);
        }
        if (_dictDialog === dictDialog) _fnRenderDialog();
    }

    /* --- Composing what to type into each tab --- */

    function _fdictPlanForSavedTab(dictTab) {
        var dictChat = dictTab.dictChat;
        if (dictChat.sIdentityClass === "inferred") {
            var dictPicked = _fdictCatalogEntry(
                _dictDialog.dictInferredPicks[dictTab.sKey]);
            return dictPicked ? dictPicked.dictPlan : null;
        }
        var dictResolved = _fdictResolved(dictChat);
        return dictResolved && dictResolved.bTranscriptFound ?
            dictResolved.dictPlan : null;
    }

    function _fsCommandFor(sKey, dictPlan) {
        if (!dictPlan) return "";
        return _dictDialog.dictAnswers[sKey] === "standard" ?
            dictPlan.sStandardCommand : dictPlan.sResumeCommand;
    }

    function _fdictLatestPlan() {
        var dictCommands = {};
        _dictDialog.listSavedTabs.forEach(function (dictTab) {
            dictCommands[dictTab.sKey] = _fsCommandFor(
                dictTab.sKey, _fdictPlanForSavedTab(dictTab));
        });
        var dictState = _dictDialog.dictEnvironment.dictState || {};
        return {
            sChoice: "latest",
            dictLayout: dictState.dictTerminals,
            dictCouncil: dictState.dictAgentCouncil,
            fsCommandForTab: function (iPane, iTab) {
                return dictCommands[iPane + ":" + iTab] || "";
            },
        };
    }

    function _fdictChoosePlan() {
        var dictTabsByPane = {};
        Object.keys(_dictDialog.dictPlacements).forEach(function (sKey) {
            var iPane = _dictDialog.dictPlacements[sKey];
            dictTabsByPane[iPane] = dictTabsByPane[iPane] || [];
            dictTabsByPane[iPane].push(_fsCommandFor(
                sKey, _fdictCatalogEntry(sKey).dictPlan));
        });
        var listPaneNumbers = Object.keys(dictTabsByPane).map(Number)
            .sort(function (iFirst, iSecond) { return iFirst - iSecond; });
        return {
            sChoice: "choose",
            dictLayout: {listPanes: listPaneNumbers.map(function (iPane) {
                return {listTabs: dictTabsByPane[iPane].map(function () {
                    return {};
                }), iActiveTabIndex: 0};
            })},
            fsCommandForTab: function (iPaneIndex, iTab) {
                return dictTabsByPane[listPaneNumbers[iPaneIndex]][iTab] || "";
            },
        };
    }

    function _fnContinue() {
        if (!_dictDialog || _fsWhyContinueIsDisabled()) return;
        var dictDialog = _dictDialog;
        var dictPlan = {sChoice: "default"};
        if (dictDialog.sChoice === "latest") dictPlan = _fdictLatestPlan();
        if (dictDialog.sChoice === "choose") dictPlan = _fdictChoosePlan();
        _fnCloseDialog();
        _dictDialog = null;
        _fnEnter(dictDialog.sContainerId, dictDialog.dictCard, dictPlan);
    }

    /* --- Opening and restoring --- */

    async function _fnEnter(sContainerId, dictCard, dictPlan) {
        _fnSetEntryState("entering");
        VaibifyDashboardMemory.fnSuspendSaving();
        try {
            var bOpened = await VaibifyWorkflowManager.fnSelectWorkflow(
                sContainerId, dictCard.sPath, dictCard.sName,
                dictCard.iSizeBytes || 0);
            if (bOpened) await _fnApplyPlan(dictPlan);
        } catch (error) {
            _fnFallBackToDefault(error);
        } finally {
            VaibifyDashboardMemory.fnResumeSaving();
            _fnSetEntryState("");
        }
    }

    async function _fnApplyPlan(dictPlan) {
        var dictProjectRecord = await VaibifyDashboardMemory.fdictReadRecord(
            "project");
        var bRestoreView = dictPlan.sChoice === "latest" ||
            dictPlan.sChoice === "kept";
        if (bRestoreView && dictProjectRecord) {
            if (dictProjectRecord.bUnreadable) {
                VaibifyApp.fnShowToast("The saved view of this project " +
                    "could not be read, so it opened in its first view.",
                    "warning");
            } else {
                VaibifyApp.fnRestoreProjectView(dictProjectRecord.dictState);
            }
        }
        if (dictPlan.sChoice === "latest" || dictPlan.sChoice === "choose") {
            VaibifyTerminal.fnRestoreLayout(
                dictPlan.dictLayout, dictPlan.fsCommandForTab);
        }
        if (dictPlan.sChoice === "latest" && dictPlan.dictCouncil &&
                typeof VaibifyAgentCouncil !== "undefined") {
            await VaibifyAgentCouncil.fnRestoreModalState(dictPlan.dictCouncil);
        }
    }

    function _fnFallBackToDefault(error) {
        /* A restore that failed part-way leaves nothing half-applied:
           the project's first view and one fresh terminal tab, with the
           reason on screen. */
        try {
            VaibifyTerminal.fnCloseAll();
            VaibifyTerminal.fnEnsureTab();
            VaibifyApp.fnRestoreProjectView({});
        } finally {
            VaibifyApp.fnShowToast("The saved layout could not be " +
                "restored, so the project opened in its first view: " +
                VaibifyDiagnosis.fsExplainError(error), "warning");
        }
    }

    return {
        fnEnterProject: fnEnterProject,
    };
})();
