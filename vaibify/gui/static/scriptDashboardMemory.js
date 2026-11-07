/* Vaibify — Saving how the dashboard was arranged, in order */

var VaibifyDashboardMemory = (function () {
    "use strict";

    /* Two records, each with its own saver: the ENVIRONMENT record
       (terminal panes, the chat each tab held, the Agent Council
       modal) and one PROJECT record per project file (selection,
       expansions, splitters, viewers, the Files directory). The records
       live on the host (routes/dashboardStateRoutes.py) and hold
       layout only; nothing in them is a status.

       The ordering guarantees, per saver:
       - single flight: at most one save in the air; later changes
         coalesce into one pending save, so this page never reorders
         its own saves;
       - the key is captured when a save is scheduled, never when it is
         sent, so a delayed save cannot land on the next project;
       - every save names the revision it was based on. A refusal
         (another window saved first) discards this snapshot for good,
         adopts the stored revision, and says so; the next human change
         saves normally, because it happened after this page saw the
         newer record -- a causal order, not a clock;
       - while a restore runs, nothing is saved; afterwards one save
         records what was restored. */

    var I_SAVE_DELAY_MILLISECONDS = 1000;
    var I_FLUSH_BOUND_MILLISECONDS = 3000;
    var S_CONFLICT_NOTICE = "Layout saved from another window; this " +
        "window's last change was not saved.";
    var S_RECORD_CONFLICT = "DASHBOARD-RECORD-CONFLICT";

    /* sTerminalId -> the chat the hub last reported for that tab. Used
       when the page is closing and there is no time to ask again. */
    var _dictChatByTerminalId = {};

    function _fsRoot(sContainerId) {
        return "/api/dashboard-state/" + encodeURIComponent(sContainerId);
    }

    /* --- The two scopes --- */

    var _dictEnvironmentScope = {
        sName: "environment",
        fdictCurrentKey: function () {
            var sContainerId = VaibifyApp.fsGetContainerId();
            return sContainerId ? {sContainerId: sContainerId} : null;
        },
        fsUrl: function (dictKey) {
            return _fsRoot(dictKey.sContainerId) + "/environment";
        },
        fdictBody: function (dictKey, iBase, dictState) {
            return {iBaseRevision: iBase, dictState: dictState};
        },
        fsReadUrl: function (dictKey) {
            return _fsRoot(dictKey.sContainerId) + "/environment";
        },
        fpromiseCapture: async function (dictKey, bAskForChats) {
            var dictLayout = VaibifyTerminal.fdictCaptureLayout();
            if (bAskForChats) {
                await _fnRefreshChats(dictKey.sContainerId, dictLayout);
            }
            return _fdictEnvironmentState(dictLayout);
        },
        fdictCaptureNow: function () {
            return _fdictEnvironmentState(VaibifyTerminal.fdictCaptureLayout());
        },
    };

    var _dictProjectScope = {
        sName: "project",
        fdictCurrentKey: function () {
            return VaibifyApp.fdictGetProjectRecordKey();
        },
        fsUrl: function (dictKey) {
            return _fsRoot(dictKey.sContainerId) + "/project";
        },
        fdictBody: function (dictKey, iBase, dictState) {
            return {sProjectRepoPath: dictKey.sProjectRepoPath,
                    sWorkflowPath: dictKey.sWorkflowPath,
                    iBaseRevision: iBase, dictState: dictState};
        },
        fsReadUrl: function (dictKey) {
            return _fsRoot(dictKey.sContainerId) + "/project" +
                "?sProjectRepoPath=" +
                encodeURIComponent(dictKey.sProjectRepoPath) +
                "&sWorkflowPath=" + encodeURIComponent(dictKey.sWorkflowPath);
        },
        fpromiseCapture: async function () {
            return VaibifyApp.fdictCaptureProjectView();
        },
        fdictCaptureNow: function () {
            return VaibifyApp.fdictCaptureProjectView();
        },
    };

    function _fdictEnvironmentState(dictLayout) {
        (dictLayout.listPanes || []).forEach(function (dictPane) {
            dictPane.listTabs.forEach(function (dictTab) {
                dictTab.dictChat =
                    _dictChatByTerminalId[dictTab.sTerminalId] || null;
            });
        });
        var dictState = {dictTerminals: dictLayout};
        if (typeof VaibifyAgentCouncil !== "undefined") {
            dictState.dictAgentCouncil =
                VaibifyAgentCouncil.fdictCaptureModalState();
        }
        return dictState;
    }

    async function _fnRefreshChats(sContainerId, dictLayout) {
        var listIds = [];
        (dictLayout.listPanes || []).forEach(function (dictPane) {
            dictPane.listTabs.forEach(function (dictTab) {
                if (dictTab.bShellStarted) listIds.push(dictTab.sTerminalId);
            });
        });
        if (!listIds.length) return;
        try {
            var dictAnswer = await VaibifyApi.fdictGet(
                _fsRoot(sContainerId) + "/terminal-chats?sTerminalIds=" +
                encodeURIComponent(listIds.join(",")));
            listIds.forEach(function (sTerminalId) {
                _dictChatByTerminalId[sTerminalId] =
                    (dictAnswer.dictChats || {})[sTerminalId] || null;
            });
        } catch (error) {
            /* The last answer stands; a layout without fresh chat
               identities is still a layout. */
        }
    }

    /* --- One saver per scope --- */

    function _fdictCreateSaver(dictScope) {
        return {
            dictScope: dictScope,
            dictRevisionByKey: {},
            dictStoredJsonByKey: {},
            iTimer: null,
            sScheduledKey: "",
            dictQueued: null,
            promiseInFlight: null,
            listIdleWaiters: [],
            iSuspensions: 0,
            bFailureShown: false,
        };
    }

    var _dictSavers = {
        environment: _fdictCreateSaver(_dictEnvironmentScope),
        project: _fdictCreateSaver(_dictProjectScope),
    };

    function _fsKeyString(dictKey) {
        return dictKey ? JSON.stringify(dictKey) : "";
    }

    function _fnSchedule(dictSaver, bAskForChats) {
        if (dictSaver.iSuspensions > 0) return;
        var dictKey = dictSaver.dictScope.fdictCurrentKey();
        if (!dictKey) return;
        if (dictSaver.iTimer) clearTimeout(dictSaver.iTimer);
        dictSaver.sScheduledKey = _fsKeyString(dictKey);
        dictSaver.iTimer = setTimeout(function () {
            dictSaver.iTimer = null;
            _fpromiseCaptureAndEnqueue(dictSaver, dictKey, bAskForChats);
        }, I_SAVE_DELAY_MILLISECONDS);
    }

    async function _fpromiseCaptureAndEnqueue(dictSaver, dictKey, bAskForChats) {
        var sKey = _fsKeyString(dictKey);
        if (sKey !== _fsKeyString(dictSaver.dictScope.fdictCurrentKey())) {
            return;
        }
        var dictState = await dictSaver.dictScope.fpromiseCapture(
            dictKey, bAskForChats);
        _fnEnqueue(dictSaver, dictKey, dictState);
    }

    function _fnEnqueue(dictSaver, dictKey, dictState) {
        var sKey = _fsKeyString(dictKey);
        var sJson = JSON.stringify(dictState);
        if (dictSaver.dictStoredJsonByKey[sKey] === sJson &&
                !dictSaver.promiseInFlight) {
            return;
        }
        dictSaver.dictQueued = {dictKey: dictKey, dictState: dictState,
                                sKey: sKey, sJson: sJson};
        _fnPump(dictSaver);
    }

    function _fnPump(dictSaver) {
        if (dictSaver.promiseInFlight) return;
        var dictItem = dictSaver.dictQueued;
        if (!dictItem) {
            var listWaiters = dictSaver.listIdleWaiters.splice(0);
            listWaiters.forEach(function (fnResolve) { fnResolve(); });
            return;
        }
        dictSaver.dictQueued = null;
        dictSaver.promiseInFlight = _fpromiseSend(dictSaver, dictItem)
            .finally(function () {
                dictSaver.promiseInFlight = null;
                _fnPump(dictSaver);
            });
    }

    async function _fpromiseSend(dictSaver, dictItem) {
        if (dictSaver.dictStoredJsonByKey[dictItem.sKey] === dictItem.sJson) {
            return;
        }
        var dictScope = dictSaver.dictScope;
        if (dictSaver.dictRevisionByKey[dictItem.sKey] === undefined) {
            await _fpromiseReadInto(dictSaver, dictItem.dictKey);
        }
        try {
            var dictAnswer = await VaibifyApi.fdictPut(
                dictScope.fsUrl(dictItem.dictKey),
                dictScope.fdictBody(
                    dictItem.dictKey,
                    dictSaver.dictRevisionByKey[dictItem.sKey] || 0,
                    dictItem.dictState));
            dictSaver.dictRevisionByKey[dictItem.sKey] = dictAnswer.iRevision;
            dictSaver.dictStoredJsonByKey[dictItem.sKey] = dictItem.sJson;
            dictSaver.bFailureShown = false;
        } catch (error) {
            _fnHandleRefusedSave(dictSaver, dictItem, error);
        }
    }

    function _fnHandleRefusedSave(dictSaver, dictItem, error) {
        var dictDetail = (error && error.dictDetail) || {};
        if (error && error.iStatus === 409 &&
                dictDetail.sError === S_RECORD_CONFLICT) {
            /* Discarded, never retried: this snapshot may be OLDER than
               the change the other window saved. */
            dictSaver.dictRevisionByKey[dictItem.sKey] = dictDetail.iRevision;
            delete dictSaver.dictStoredJsonByKey[dictItem.sKey];
            VaibifyApp.fnShowToast(S_CONFLICT_NOTICE, "info");
            return;
        }
        if (dictSaver.bFailureShown) return;
        dictSaver.bFailureShown = true;
        VaibifyApp.fnShowToast(
            "The " + dictSaver.dictScope.sName + " layout was not saved: " +
            VaibifyDiagnosis.fsExplainError(error), "warning");
    }

    async function _fpromiseReadInto(dictSaver, dictKey) {
        var sKey = _fsKeyString(dictKey);
        var dictRecord = await VaibifyApi.fdictGet(
            dictSaver.dictScope.fsReadUrl(dictKey));
        dictSaver.dictRevisionByKey[sKey] = dictRecord.iRevision || 0;
        dictSaver.dictStoredJsonByKey[sKey] =
            JSON.stringify(dictRecord.dictState || {});
        return dictRecord;
    }

    function _fpromiseIdle(dictSaver) {
        if (!dictSaver.promiseInFlight && !dictSaver.dictQueued) {
            return Promise.resolve();
        }
        return new Promise(function (fnResolve) {
            dictSaver.listIdleWaiters.push(fnResolve);
        });
    }

    /* --- Reading a record on entry --- */

    async function fdictReadRecord(sScope) {
        /* The stored revision becomes this page's base, so a reload
           never restarts at zero. Waits for this page's own saves to
           land first, so it never reads behind itself. */
        var dictSaver = _dictSavers[sScope];
        var dictKey = dictSaver.dictScope.fdictCurrentKey();
        if (!dictKey) return null;
        await _fpromiseIdle(dictSaver);
        return _fpromiseReadInto(dictSaver, dictKey);
    }

    /* --- Restore brackets --- */

    function fnSuspendSaving() {
        Object.keys(_dictSavers).forEach(function (sScope) {
            var dictSaver = _dictSavers[sScope];
            dictSaver.iSuspensions += 1;
            if (dictSaver.iTimer) clearTimeout(dictSaver.iTimer);
            dictSaver.iTimer = null;
        });
    }

    function fnResumeSaving() {
        /* One save records what the restore produced. */
        Object.keys(_dictSavers).forEach(function (sScope) {
            var dictSaver = _dictSavers[sScope];
            dictSaver.iSuspensions = Math.max(0, dictSaver.iSuspensions - 1);
            _fnSchedule(dictSaver, false);
        });
    }

    /* --- Leaving --- */

    function _fpromiseWithin(promiseWork, iMilliseconds) {
        return Promise.race([
            promiseWork.then(function () { return true; }),
            new Promise(function (fnResolve) {
                setTimeout(function () { fnResolve(false); }, iMilliseconds);
            }),
        ]);
    }

    async function _fpromiseFlushOne(dictSaver) {
        if (dictSaver.iTimer) clearTimeout(dictSaver.iTimer);
        dictSaver.iTimer = null;
        var dictKey = dictSaver.dictScope.fdictCurrentKey();
        if (dictKey && dictSaver.iSuspensions === 0) {
            var dictState = await dictSaver.dictScope.fpromiseCapture(
                dictKey, true);
            _fnEnqueue(dictSaver, dictKey, dictState);
        }
        await _fpromiseIdle(dictSaver);
    }

    async function fbFlushBeforeLeaving() {
        /* Called before the project closes, the claim is released or
           the hub quits -- while the shells still exist, so the chat
           each tab holds can still be read. Bounded: when the bound
           expires the page leaves anyway and says so. */
        var promiseAll = Promise.all(Object.keys(_dictSavers).map(
            function (sScope) { return _fpromiseFlushOne(_dictSavers[sScope]); }));
        var bSaved = await _fpromiseWithin(promiseAll, I_FLUSH_BOUND_MILLISECONDS);
        if (!bSaved) {
            VaibifyApp.fnShowToast(
                "The layout may not have been saved before leaving.",
                "warning");
        }
        return bSaved;
    }

    function fnSendOnPageHide() {
        /* Best effort, by construction: the page is going, so the
           current layout is sent once with keepalive and nobody reads
           the answer. Chat identities are the last ones the hub gave. */
        Object.keys(_dictSavers).forEach(function (sScope) {
            var dictSaver = _dictSavers[sScope];
            var dictKey = dictSaver.dictScope.fdictCurrentKey();
            if (!dictKey || dictSaver.iSuspensions > 0) return;
            var sKey = _fsKeyString(dictKey);
            var dictState = dictSaver.dictScope.fdictCaptureNow();
            if (dictSaver.dictStoredJsonByKey[sKey] === JSON.stringify(dictState)) {
                return;
            }
            VaibifyApi.fnPutKeepalive(
                dictSaver.dictScope.fsUrl(dictKey),
                dictSaver.dictScope.fdictBody(
                    dictKey, dictSaver.dictRevisionByKey[sKey] || 0, dictState));
        });
    }

    /* --- What schedules a save ---

       A human gesture: a click, the end of a drag, or Enter (which is
       how a chat is started or ended in a terminal). The capture runs a
       second later and is sent only when it differs from what is
       stored, so a gesture that changed nothing costs nothing. */

    function _fnScheduleAll(bAskForChats) {
        _fnSchedule(_dictSavers.environment, bAskForChats);
        _fnSchedule(_dictSavers.project, false);
    }

    function fnBindGestureTriggers() {
        document.addEventListener("click", function () {
            _fnScheduleAll(false);
        }, true);
        document.addEventListener("mouseup", function () {
            _fnScheduleAll(false);
        }, true);
        document.addEventListener("keydown", function (event) {
            if (event.key === "Enter") _fnScheduleAll(true);
        }, true);
    }

    function fdictChatForTerminal(sTerminalId) {
        return _dictChatByTerminalId[sTerminalId] || null;
    }

    return {
        fdictReadRecord: fdictReadRecord,
        fnSuspendSaving: fnSuspendSaving,
        fnResumeSaving: fnResumeSaving,
        fbFlushBeforeLeaving: fbFlushBeforeLeaving,
        fnSendOnPageHide: fnSendOnPageHide,
        fnBindGestureTriggers: fnBindGestureTriggers,
        fdictChatForTerminal: fdictChatForTerminal,
    };
})();
