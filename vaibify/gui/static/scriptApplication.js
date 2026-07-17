/* Vaibify — Main application logic */

const PipeleyenApp = (function () {
    "use strict";

    function fbIsTerminalFocused() {
        var elActive = document.activeElement;
        if (!elActive) return false;
        return !!elActive.closest("#terminalStrip, .xterm");
    }

    var _dictSessionState = {
        sSessionToken: "",
        sContainerId: null,
        sUserName: "User",
        dictDashboardMode: null,
        sLeaseId: "",
        sLeaseContainerName: null,
    };

    var _S_LEASE_STORAGE_KEY = "vaibifyContainerLease";

    function _fdictDefaultWorkflowState() {
        return {
            dictWorkflow: null,
            sWorkflowPath: null,
            dictStepStatus: {},
            dictScriptModified: {},
            dictStaleArtifacts: {},
            dictDiscoveredOutputs: {},
            dictUserVerifiedAt: {},
            dictFileExistenceCache: {},
            dictFileModTimes: {},
            dictOutputMtimes: {},
            dictPlotMtimes: {},
            dictMaxDataMtimeByStep: {},
            dictMaxInputMtimeByStep: {},
            dictMarkerMtimeByStep: {},
            dictTestSourceMtimeByStep: {},
            dictTestCategoryMtimes: {},
            dictPlotStandardExists: {},
            dictBlockersByStep: {},
            dictBlockersByStepLevel2: {},
            dictBlockersByStepLevel3: {},
            dictStepLevels: {},
            dictStepLevelHighWater: {},
            dictStepLevelWarnings: {},
            dictWorkflowScopeLevels: null,
            dictWorkflowLevelHighWater: {},
            dictWorkflowEnvelopeDetail: null,
            iL1BlockerCount: 0,
            iL2BlockerCount: 0,
            iL3BlockerCount: 0,
            iCachedAicsLevel: null,
            iWorkflowEpoch: -1,
            sWorkflowFingerprint: "",
            iFileCheckTimer: null,
            bFileCheckInProgress: false,
            iInflightRequests: 0,
            abortControllerFileCheck: null,
            bDelegatedEventsInitialized: false,
            iLastRenderedAICSLevel: 0,
            listUndoStack: [],
            dictContainerSettings: null,
            bClaudeRestartNeeded: false,
        };
    }

    var _dictWorkflowState = _fdictDefaultWorkflowState();

    var _dictUiState = {
        iSelectedStepIndex: -1,
        setExpandedSteps: new Set(),
        setExpandedDeps: new Set(),
        setExpandedQualitative: new Set(),
        setExpandedQuantitative: new Set(),
        setExpandedIntegrity: new Set(),
        setExpandedRequirementGroups: new Set(),
        setExpandedRequirementRows: new Set(),
        bStepsCollapsed: false,
        bProjectBlockCollapsed: false,
        bBinaryAddFormOpen: false,
        bShowTimestamps: false,
        iContextStepIndex: -1,
    };

    var I_MAX_UNDO = 50;
    var fbIsBinaryFile = VaibifyUtilities.fbIsBinaryFile;

    var DICT_MODE_WORKFLOW = {
        sMode: "workflow",
        listLeftTabs: ["steps", "aics", "files", "logs"],
        sDefaultLeftTab: "steps",
        bShowRunMenu: true,
        bShowDagButton: true,
    };

    var DICT_MODE_NO_WORKFLOW = {
        sMode: "noWorkflow",
        listLeftTabs: ["files", "repos", "logs"],
        sDefaultLeftTab: "files",
        bShowRunMenu: false,
        bShowDagButton: false,
    };

    async function fnFetchSessionToken() {
        try {
            var data = await VaibifyApi.fdictGet("/api/session-token");
            _dictSessionState.sSessionToken = data.sToken || "";
            fnInstallAuthenticatedFetch(_dictSessionState.sSessionToken);
        } catch (e) {
            _dictSessionState.sSessionToken = "";
        }
    }

    function fnInstallAuthenticatedFetch(sToken) {
        var originalFetch = window.fetch;
        window.fetch = function (sUrl, dictOptions) {
            dictOptions = dictOptions || {};
            dictOptions.headers = dictOptions.headers || {};
            if (typeof dictOptions.headers.set === "function") {
                dictOptions.headers.set("X-Session-Token", sToken);
            } else {
                dictOptions.headers["X-Session-Token"] = sToken;
            }
            return originalFetch.call(window, sUrl, dictOptions);
        };
    }

    /* --- Per-tab claim lease --- */

    function _fnPersistLease() {
        try {
            if (!_dictSessionState.sLeaseId) {
                window.sessionStorage.removeItem(_S_LEASE_STORAGE_KEY);
                return;
            }
            window.sessionStorage.setItem(
                _S_LEASE_STORAGE_KEY,
                JSON.stringify({
                    sName: _dictSessionState.sLeaseContainerName,
                    sLeaseId: _dictSessionState.sLeaseId,
                }),
            );
        } catch (error) {
            /* sessionStorage unavailable; lease lives in memory only */
        }
    }

    function _fnRestoreLeaseFromStorage() {
        try {
            var sStored = window.sessionStorage.getItem(
                _S_LEASE_STORAGE_KEY);
            if (!sStored) return;
            var dictStored = JSON.parse(sStored);
            _dictSessionState.sLeaseContainerName =
                dictStored.sName || null;
            _dictSessionState.sLeaseId = dictStored.sLeaseId || "";
        } catch (error) {
            /* corrupt or unavailable storage; start with no lease */
        }
    }

    function fnRecordClaimedLease(sName, sLeaseId) {
        _dictSessionState.sLeaseContainerName = sName;
        _dictSessionState.sLeaseId = sLeaseId || "";
        _fnPersistLease();
    }

    function fnForgetLease() {
        _dictSessionState.sLeaseContainerName = null;
        _dictSessionState.sLeaseId = "";
        _fnPersistLease();
    }

    function fsGetLeaseId() {
        return _dictSessionState.sLeaseId || "";
    }

    function fsGetLeaseForContainer(sName) {
        if (_dictSessionState.sLeaseContainerName === sName) {
            return _dictSessionState.sLeaseId || "";
        }
        return "";
    }

    function _fnRecordViewerLeaseFromConnect(sId, dictConnect) {
        /* Viewer mode mints its lease server-side and returns it on the
           connect response (the viewer has no claim route). The served
           lease is AUTHORITATIVE: it must replace any lease left in
           sessionStorage by a previous hub process, or every
           WebSocket presents a foreign lease and fails closed as 1006
           after a hub restart (live incident 2026-07-03 — a reload
           preserves sessionStorage, so the stale lease survived every
           restart). Hub-mode connect responses carry no lease
           (sLeaseId ""), so the first guard leaves the claim-recorded
           lease untouched; the second skips a redundant re-record when
           the stored lease already matches. */
        if (!dictConnect || !dictConnect.sLeaseId) return;
        if (fsGetLeaseId() === dictConnect.sLeaseId) return;
        var sName = PipeleyenContainerManager
            .fsGetSelectedContainerName() || sId;
        fnRecordClaimedLease(sName, dictConnect.sLeaseId);
    }

    /* --- WebSocket and Polling Registration --- */

    function fnRegisterWebSocketHandlers() {
        VaibifyWebSocket.fnOnEvent("*",
            PipeleyenPipelineRunner.fnHandlePipelineEvent);
        VaibifyWebSocket.fnOnEvent("_wsClose", function (dictEvent) {
            fnClearRunningStatuses();
            fnRenderStepList();
            if (dictEvent.bActionsDropped) {
                // The socket died holding unsent actions: whatever
                // the researcher just clicked (typically a step run
                // that already painted its queued light) never
                // reached the server. Saying so beats letting the
                // queued light silently evaporate (live incident,
                // 2026-07-03).
                fnShowToast(
                    "Connection lost before your last request " +
                    "reached the server — it was NOT submitted. " +
                    "A step run that showed as queued never " +
                    "started. Reconnect and retry.",
                    "error");
            }
            _fnReportConnectionLossToMonitor(dictEvent);
        });
        VaibifyWebSocket.fnOnEvent("_wsError", function (dictEvent) {
            _fnReportConnectionLossToMonitor(dictEvent);
        });
    }

    function _fnReportConnectionLossToMonitor(dictEvent) {
        if (typeof VaibifyConnectionMonitor === "undefined") return;
        VaibifyConnectionMonitor.fnReportWsLoss(dictEvent);
    }

    function fnRegisterPollingHandlers() {
        VaibifyPolling.fnSetPipelineStateHandler(
            PipeleyenPipelineRunner.fnHandlePipelinePollResult);
        VaibifyPolling.fnSetFileStatusHandler(
            fnProcessFileStatusResponse);
        VaibifyPolling.fnSetWorkflowDiscoveryHandler(
            fnProcessWorkflowDiscovery);
    }

    /* --- Initialization --- */

    async function fnInitialize() {
        _fnRestoreLeaseFromStorage();
        await fnFetchSessionToken();
        fnRegisterWebSocketHandlers();
        fnRegisterPollingHandlers();
        fnLoadUserName();
        fnLoadTimestampSetting();
        fnShowContainerLanding();
        PipeleyenEventBindings.fnBindToolbarEvents();
        PipeleyenEventBindings.fnBindWorkflowPickerEvents();
        PipeleyenContainerManager.fnBindContainerLandingEvents();
        PipeleyenContainerManager.fnBindAddContainerModal();
        PipeleyenEventBindings.fnBindErrorModal();
        PipeleyenTestManager.fnBindApiConfirmModal();
        PipeleyenEventBindings.fnBindContextMenuEvents();
        PipeleyenEventBindings.fnBindLeftPanelTabs();
        PipeleyenEventBindings.fnBindResizeHandles();
        PipeleyenEventBindings.fnBindGlobalSettingsToggle();
        PipeleyenEventBindings.fnBindRefreshRemoteStatus();
        document.addEventListener("click", function () {
            fnHideContextMenu();
        });
        /*
         * TERMINAL SAFETY: When the terminal pane is focused, ALL
         * keystrokes must pass through to the container PTY
         * unmodified. Before adding any new global keybinding,
         * check fbIsTerminalFocused() and skip if true.
         */
        document.addEventListener("keydown", function (event) {
            if ((event.ctrlKey || event.metaKey) && event.key === "z") {
                if (fbIsTerminalFocused()) return;
                event.preventDefault();
                fnUndo();
            }
        });
    }

    async function fnLoadUserName() {
        try {
            var dictUser = await VaibifyApi.fdictGet("/api/user");
            fnSetVerificationUserName(dictUser.sUserName);
        } catch (error) {
            fnSetVerificationUserName("User");
        }
    }

    function _fnResetWorkflowState() {
        var dictDefaults = _fdictDefaultWorkflowState();
        for (var sKey in dictDefaults) {
            _dictWorkflowState[sKey] = dictDefaults[sKey];
        }
        _fnResetUiState();
        _fnInvalidateAllRenderCaches();
        PipeleyenTestManager.fnResetState();
        PipeleyenPipelineRunner.fnResetState();
        VaibifyOverleafMirror.fnResetState();
        VaibifySyncManager.fnResetState();
        VaibifyPolling.fnStopPipelinePolling();
        VaibifyPolling.fnStopFilePolling();
        VaibifyPolling.fnStopDiscoveryPolling();
        PipeleyenReposPanel.fnTeardown();
        VaibifyAicsTab.fnSetContainerId(null);
    }

    function _fnResetUiState() {
        _dictUiState.iSelectedStepIndex = -1;
        _dictUiState.setExpandedSteps.clear();
        _dictUiState.setExpandedDeps.clear();
        _dictUiState.setExpandedQualitative.clear();
        _dictUiState.setExpandedQuantitative.clear();
        _dictUiState.setExpandedIntegrity.clear();
        _dictUiState.setExpandedRequirementGroups.clear();
        _dictUiState.setExpandedRequirementRows.clear();
        _dictUiState.bBinaryAddFormOpen = false;
    }

    function _fnActivateWorkflow(sId, data, sWorkflowName) {
        _fnResetWorkflowState();
        VaibifyPolling.fnStopDiscoveryPolling();
        _fnRecordViewerLeaseFromConnect(sId, data);
        _dictSessionState.sContainerId = sId;
        _dictWorkflowState.dictWorkflow = data.dictWorkflow;
        _dictWorkflowState.sWorkflowPath = data.sWorkflowPath;
        _dictWorkflowState.iWorkflowEpoch =
            typeof data.iWorkflowEpoch === "number" ?
                data.iWorkflowEpoch : -1;
        _dictWorkflowState.sWorkflowFingerprint =
            data.sWorkflowFingerprint || "";
        _fnLoadStepsCollapsed();
        _dictSessionState.dictDashboardMode = DICT_MODE_WORKFLOW;
        _fnSurfaceStateLoadNotice(data.dictWorkflow);
        if (data.dictFileStatus) {
            fnProcessFileStatusResponse(data.dictFileStatus);
        }
        var iStepCount = (_dictWorkflowState.dictWorkflow.listSteps || []).length;
        if (iStepCount > 500) {
            fnShowToast(
                "This project has " + iStepCount + " steps. " +
                "Large projects may use significant memory. " +
                "Avoid expanding many steps simultaneously.",
                "error"
            );
        }
        document.getElementById("activeContainerName").textContent =
            PipeleyenContainerManager.fsGetSelectedContainerName() || "";
        document.getElementById("activeWorkflowName").textContent =
            sWorkflowName || "";
        document.title = (PipeleyenContainerManager.fsGetSelectedContainerName() || "Vaibify") +
            (sWorkflowName ? ": " + sWorkflowName : "");
        fnShowMainLayout();
        fnRenderStepList();
        fnUpdateHighlightState();
        fnPollAllStepFiles();
        fnStartFileChangePolling();
        try {
            PipeleyenTerminal.fnEnsureTab();
        } catch (errorTerminal) {
            // A terminal failure must never abort the rest of
            // activation (AICS tab, repos panel, badges, pipeline
            // recovery below) — that shipped as a raw "Terminal is
            // not defined" toast plus a half-initialized dashboard.
            fnShowToast(
                "Terminal setup failed: " +
                fsSanitizeErrorForUser(errorTerminal.message),
                "error");
        }
        // The AICS and Repos tabs are container-scoped: without
        // these two calls they sit in their "connect first" empty
        // states for the entire workflow session, which is the mode
        // researchers are actually in.
        VaibifyAicsTab.fnSetContainerId(sId);
        PipeleyenReposPanel.fnInit(sId);
        // Badges otherwise stay empty until a sync action bumps the
        // epoch mid-session: the per-file remote icons render grey
        // and the declaration commit/remove buttons gate wrong on
        // every fresh load. One fetch here seeds them.
        if (typeof VaibifyGitBadges !== "undefined") {
            VaibifyGitBadges.fnRefresh(sId);
        }
        PipeleyenPipelineRunner.fnRecoverPipelineState(sId);
        fnLoadContainerSettings();
    }

    function _fnSurfaceStateLoadNotice(dictWorkflow) {
        if (!dictWorkflow) return;
        var dictNotice = dictWorkflow.dictStateLoadNotice;
        if (!dictNotice || !dictNotice.sMessage) return;
        var sLevel = dictNotice.sLevel || "warning";
        fnShowToast(dictNotice.sMessage, sLevel);
        delete dictWorkflow.dictStateLoadNotice;
    }

    function fnRefreshWorkflowData(dictData) {
        _dictWorkflowState.dictWorkflow = dictData.dictWorkflow;
        _dictWorkflowState.sWorkflowPath = dictData.sWorkflowPath;
        if (typeof dictData.iWorkflowEpoch === "number") {
            _dictWorkflowState.iWorkflowEpoch =
                dictData.iWorkflowEpoch;
        }
        // Adopt the fresh compare-and-swap baseline immediately so the
        // very next edit is validated against current state, not the
        // stale fingerprint that provoked the reload.
        if (typeof dictData.sWorkflowFingerprint === "string") {
            _dictWorkflowState.sWorkflowFingerprint =
                dictData.sWorkflowFingerprint;
        }
        _fnClearFileCaches();
        _fnInvalidateAllRenderCaches();
        fnRenderStepList();
        fnPollAllStepFiles();
    }

    function _fnApplyOutOfBandWorkflowReload(
        dictWorkflowNew, iWorkflowEpoch
    ) {
        var iPriorSelected = _dictUiState.iSelectedStepIndex;
        var dictPriorExpanded = _fdictSnapshotExpansionSets();
        _dictWorkflowState.dictWorkflow = dictWorkflowNew;
        if (typeof iWorkflowEpoch === "number") {
            _dictWorkflowState.iWorkflowEpoch = iWorkflowEpoch;
        }
        _fnClearFileCaches();
        _fnInvalidateAllRenderCaches();
        fnRenderStepList();
        var iStepCount = (dictWorkflowNew.listSteps || []).length;
        _fnRestoreUiSelection(iPriorSelected, iStepCount);
        _fnRestoreExpansionSets(dictPriorExpanded, iStepCount);
        fnRenderStepList();
        fnShowToast(
            "Project definition reloaded from disk", "info");
    }

    function _fdictSnapshotExpansionSets() {
        return {
            setSteps: new Set(_dictUiState.setExpandedSteps),
            setDeps: new Set(_dictUiState.setExpandedDeps),
            setQualitative: new Set(
                _dictUiState.setExpandedQualitative),
            setQuantitative: new Set(
                _dictUiState.setExpandedQuantitative),
            setIntegrity: new Set(_dictUiState.setExpandedIntegrity),
        };
    }

    function _fnRestoreUiSelection(iPriorSelected, iStepCount) {
        if (iPriorSelected >= 0 && iPriorSelected < iStepCount) {
            _dictUiState.iSelectedStepIndex = iPriorSelected;
        } else {
            _dictUiState.iSelectedStepIndex = -1;
        }
    }

    function _fnRestoreExpansionSets(dictPrior, iStepCount) {
        var listPairs = [
            [_dictUiState.setExpandedSteps, dictPrior.setSteps],
            [_dictUiState.setExpandedDeps, dictPrior.setDeps],
            [_dictUiState.setExpandedQualitative,
             dictPrior.setQualitative],
            [_dictUiState.setExpandedQuantitative,
             dictPrior.setQuantitative],
            [_dictUiState.setExpandedIntegrity,
             dictPrior.setIntegrity],
        ];
        for (var iPair = 0; iPair < listPairs.length; iPair++) {
            _fnCopyIndicesWithinRange(
                listPairs[iPair][0], listPairs[iPair][1], iStepCount);
        }
    }

    function _fnCopyIndicesWithinRange(setLive, setPrior, iStepCount) {
        setLive.clear();
        setPrior.forEach(function (iIndex) {
            if (iIndex >= 0 && iIndex < iStepCount) {
                setLive.add(iIndex);
            }
        });
    }

    function _fnClearFileCaches() {
        _dictWorkflowState.dictFileExistenceCache = {};
        _dictWorkflowState.dictFileModTimes = {};
        _dictWorkflowState.dictOutputMtimes = {};
        _dictWorkflowState.dictPlotMtimes = {};
        _dictWorkflowState.dictMaxDataMtimeByStep = {};
        _dictWorkflowState.dictMaxInputMtimeByStep = {};
        _dictWorkflowState.dictMarkerMtimeByStep = {};
        _dictWorkflowState.dictTestSourceMtimeByStep = {};
        _dictWorkflowState.dictTestCategoryMtimes = {};
        _dictWorkflowState.dictPlotStandardExists = {};
        _fnClearBlockerAndLevelState();
    }

    function _fnClearBlockerAndLevelState() {
        _dictWorkflowState.dictBlockersByStep = {};
        _dictWorkflowState.dictBlockersByStepLevel2 = {};
        _dictWorkflowState.dictBlockersByStepLevel3 = {};
        _dictWorkflowState.dictStepLevels = {};
        _dictWorkflowState.dictStepLevelHighWater = {};
        _dictWorkflowState.dictStepLevelWarnings = {};
        _dictWorkflowState.dictWorkflowScopeLevels = null;
        _dictWorkflowState.dictWorkflowLevelHighWater = {};
        _dictWorkflowState.dictWorkflowEnvelopeDetail = null;
        _dictWorkflowState.iL1BlockerCount = 0;
        _dictWorkflowState.iL2BlockerCount = 0;
        _dictWorkflowState.iL3BlockerCount = 0;
        _dictWorkflowState.iCachedAicsLevel = null;
    }

    async function fnEnterNoWorkflow(sId) {
        try {
            var dictConnect = await VaibifyApi.fdictPostRaw(
                "/api/connect/" + sId);
            _fnRecordViewerLeaseFromConnect(sId, dictConnect);
            _fnResetWorkflowState();
            _dictSessionState.sContainerId = sId;
            _dictSessionState.dictDashboardMode = DICT_MODE_NO_WORKFLOW;
            document.getElementById("activeContainerName").textContent =
                PipeleyenContainerManager.fsGetSelectedContainerName() || "";
            _fnRenderToolkitBanner(0);
            document.title = PipeleyenContainerManager.fsGetSelectedContainerName() || "Vaibify";
            fnShowMainLayout();
            PipeleyenTerminal.fnEnsureTab();
            await PipeleyenReposPanel.fnInit(sId);
            VaibifyAicsTab.fnSetContainerId(sId);
            VaibifyPolling.fnStartDiscoveryPolling(sId);
        } catch (error) {
            fnShowToast(
                fsSanitizeErrorForUser(error.message), "error"
            );
        }
    }

    function _fnRenderToolkitBanner(iAvailable) {
        var elName = document.getElementById("activeWorkflowName");
        if (!elName) return;
        if (iAvailable > 0) {
            elName.innerHTML =
                '<a href="#" id="toolkitBannerSwitch" '
                + 'class="toolkit-banner-switch">'
                + 'None &mdash; ' + iAvailable
                + ' available <span aria-hidden="true">&#9662;</span>'
                + '</a>';
            var elLink = document.getElementById(
                "toolkitBannerSwitch");
            if (elLink) {
                elLink.addEventListener("click", function (event) {
                    event.preventDefault();
                    VaibifyWorkflowManager
                        .fnToggleWorkflowDropdown();
                });
            }
        } else {
            elName.textContent = "None";
        }
    }

    function fnProcessWorkflowDiscovery(dictResponse) {
        if (!dictResponse) return;
        var listAvailable = dictResponse.listAvailableWorkflows || [];
        if (_dictSessionState.dictDashboardMode === DICT_MODE_NO_WORKFLOW) {
            _fnRenderToolkitBanner(listAvailable.length);
        }
        if (!dictResponse.bWorkflowsChanged) return;
        var listNew = dictResponse.listNewWorkflowPaths || [];
        if (listNew.length === 0) return;
        _fnToastNewWorkflowsAppeared(listNew, listAvailable);
    }

    function _fnToastNewWorkflowsAppeared(listNewPaths, listAvailable) {
        var dictByPath = {};
        listAvailable.forEach(function (dictWf) {
            dictByPath[dictWf.sPath] = dictWf;
        });
        listNewPaths.forEach(function (sPath) {
            var dictWf = dictByPath[sPath];
            if (!dictWf) return;
            fnShowToast(
                "New project available: " + dictWf.sName
                + ". Click to load.",
                "info",
                function () {
                    VaibifyWorkflowManager.fnSelectWorkflow(
                        _dictSessionState.sContainerId,
                        dictWf.sPath, dictWf.sName,
                        dictWf.iSizeBytes || 0);
                }
            );
        });
    }

    function fnReorderLeftTabs(listVisibleTabs) {
        var elTabBar = document.getElementById("leftPanelTabs");
        if (!elTabBar) return;
        listVisibleTabs.forEach(function (sPanel) {
            var elTab = elTabBar.querySelector(
                '.left-tab[data-panel="' + sPanel + '"]'
            );
            if (elTab) elTabBar.appendChild(elTab);
        });
    }

    function fnReorderLeftPanels(listVisibleTabs) {
        var elResizeHandle = document.querySelector(
            "#panelLeft > .resize-handle-horizontal"
        );
        if (!elResizeHandle) return;
        var elPanelLeft = elResizeHandle.parentElement;
        listVisibleTabs.forEach(function (sPanel) {
            var sId = "panel" + sPanel.charAt(0).toUpperCase()
                + sPanel.slice(1);
            var elPanel = document.getElementById(sId);
            if (elPanel) {
                elPanelLeft.insertBefore(elPanel, elResizeHandle);
            }
        });
    }

    function fnApplyToolbarVisibility(dictMode) {
        var elRunMenu = document.getElementById("toolbarMenuRun");
        if (elRunMenu) elRunMenu.style.display =
            dictMode.bShowRunMenu ? "" : "none";
        var elDagButton = document.getElementById("btnShowDag");
        if (elDagButton) elDagButton.style.display =
            dictMode.bShowDagButton ? "" : "none";
    }

    function fnApplyDashboardMode() {
        if (!_dictSessionState.dictDashboardMode) return;
        var listLeftTabs = _dictSessionState.dictDashboardMode.listLeftTabs;
        var listAllTabs = document.querySelectorAll(".left-tab");
        listAllTabs.forEach(function (elTab) {
            var bVisible = listLeftTabs.includes(elTab.dataset.panel);
            elTab.style.display = bVisible ? "" : "none";
        });
        fnReorderLeftTabs(listLeftTabs);
        fnReorderLeftPanels(listLeftTabs);
        var elDefaultTab = document.querySelector(
            '.left-tab[data-panel="' +
            _dictSessionState.dictDashboardMode.sDefaultLeftTab + '"]'
        );
        if (elDefaultTab) elDefaultTab.click();
        fnApplyToolbarVisibility(_dictSessionState.dictDashboardMode);
    }

    function fnShowContainerLanding() {
        var sActiveName = PipeleyenContainerManager
            .fsGetSelectedContainerName();
        if (sActiveName) {
            PipeleyenContainerManager.fnReleaseClaim(sActiveName);
        }
        document.getElementById("containerLanding").style.display = "flex";
        document.getElementById("workflowPicker").style.display = "none";
        document.getElementById("mainLayout").classList.remove("active");
        _dictSessionState.dictDashboardMode = null;
        document.getElementById("activeContainerName").textContent = "";
        document.getElementById("activeWorkflowName").textContent = "";
        document.title = "Vaibify";
        _fnStopWorkflowHubPolling();
        _fnStartContainerHubPolling();
    }

    function fnShowWorkflowPicker(sContainerName) {
        document.getElementById("containerLanding").style.display = "none";
        document.getElementById("workflowPicker").style.display = "flex";
        document.getElementById("mainLayout").classList.remove("active");
        document.title = sContainerName || "Vaibify";
        _fnStopContainerHubPolling();
        var sContainerId = PipeleyenContainerManager
            .fsGetSelectedContainerId();
        _fnStartWorkflowHubPolling(sContainerId);
    }

    function fnShowMainLayout() {
        document.getElementById("containerLanding").style.display = "none";
        document.getElementById("workflowPicker").style.display = "none";
        document.getElementById("mainLayout").classList.add("active");
        fnApplyDashboardMode();
        _fnStopContainerHubPolling();
        _fnStopWorkflowHubPolling();
    }

    function _fnStartContainerHubPolling() {
        VaibifyPolling.fnSetContainerHubHandler(
            _fnPollContainerHubIfIdle);
        VaibifyPolling.fnStartContainerHubPolling();
    }

    async function _fnPollContainerHubIfIdle() {
        if (_fbContainerHubHasOpenMenu()) return;
        await PipeleyenContainerManager.fnRefreshContainerHub();
    }

    function _fbContainerHubHasOpenMenu() {
        var listMenus = document.querySelectorAll(
            ".container-tile-menu");
        for (var i = 0; i < listMenus.length; i++) {
            if (listMenus[i].style.display !== "none") return true;
        }
        return false;
    }

    function _fnStopContainerHubPolling() {
        VaibifyPolling.fnStopContainerHubPolling();
    }

    function _fnStartWorkflowHubPolling(sContainerId) {
        if (!sContainerId) {
            _fnStopWorkflowHubPolling();
            return;
        }
        VaibifyPolling.fnSetWorkflowHubHandler(function () {
            return _fnRefreshWorkflowHubList(sContainerId);
        });
        VaibifyPolling.fnStartWorkflowHubPolling();
    }

    function _fnStopWorkflowHubPolling() {
        VaibifyPolling.fnStopWorkflowHubPolling();
    }

    async function _fnRefreshWorkflowHubList(sContainerId) {
        var listWorkflows = await VaibifyApi.fdictGet(
            "/api/workflows/" + encodeURIComponent(sContainerId));
        VaibifyWorkflowManager.fnRenderWorkflowList(
            listWorkflows, sContainerId);
    }

    function fnStopAllHubPolling() {
        _fnStopContainerHubPolling();
        _fnStopWorkflowHubPolling();
    }

    function fnResumeHubPollingForCurrentView() {
        var elLanding = document.getElementById("containerLanding");
        var elPicker = document.getElementById("workflowPicker");
        if (elLanding && elLanding.style.display === "flex") {
            _fnStartContainerHubPolling();
            return;
        }
        if (elPicker && elPicker.style.display === "flex") {
            var sContainerId = PipeleyenContainerManager
                .fsGetSelectedContainerId();
            _fnStartWorkflowHubPolling(sContainerId);
        }
    }

    function _fnCancelAllTimers() {
        VaibifyWebSocket.fnDisconnect();
        VaibifyPolling.fnStopPipelinePolling();
        VaibifyPolling.fnStopFilePolling();
        VaibifyPolling.fnStopDiscoveryPolling();
        PipeleyenReposPanel.fnTeardown();
        if (_dictWorkflowState.iFileCheckTimer) {
            clearTimeout(_dictWorkflowState.iFileCheckTimer);
            _dictWorkflowState.iFileCheckTimer = null;
        }
        if (_dictWorkflowState.abortControllerFileCheck) {
            _dictWorkflowState.abortControllerFileCheck.abort();
            _dictWorkflowState.abortControllerFileCheck = null;
        }
        PipeleyenPipelineRunner.fnCancelSentinelMonitor();
    }

    function fnDisconnect() {
        _dictSessionState.sContainerId = null;
        _dictWorkflowState.dictWorkflow = null;
        _dictWorkflowState.sWorkflowPath = null;
        _dictUiState.iSelectedStepIndex = -1;
        _dictUiState.setExpandedSteps.clear();
        _dictUiState.setExpandedDeps.clear();
        PipeleyenTestManager.fnResetState();
        _dictWorkflowState.dictPlotStandardExists = {};
        _dictWorkflowState.dictStepStatus = {};
        document.body.classList.remove(
            "aics-level-1", "aics-level-2", "aics-level-3",
        );
        _fnCancelAllTimers();
        PipeleyenFigureViewer.fnReleaseResources();
        PipeleyenTerminal.fnCloseAll();
        fnShowContainerLanding();
        PipeleyenContainerManager.fnLoadContainers();
    }

    /* --- Template Resolution --- */

    function fdictBuildClientVariables() {
        if (!_dictWorkflowState.dictWorkflow) return {};
        var sWorkflowDir = fsGetWorkflowDirectory();
        var sRepoRoot = sWorkflowDir;
        if (sRepoRoot.endsWith("/.vaibify/projects")) {
            sRepoRoot = sRepoRoot.replace(
                "/.vaibify/projects", "");
        } else if (sRepoRoot.endsWith("/.vaibify/workflows")) {
            sRepoRoot = sRepoRoot.replace(
                "/.vaibify/workflows", "");
        } else if (sRepoRoot.endsWith("/.vaibify")) {
            sRepoRoot = sRepoRoot.replace("/.vaibify", "");
        }
        var sPlotDir = _dictWorkflowState.dictWorkflow.sPlotDirectory || "Plot";
        if (sPlotDir.charAt(0) !== "/") {
            sPlotDir = sRepoRoot + "/" + sPlotDir;
        }
        var dictVars = {
            sPlotDirectory: sPlotDir,
            sRepoRoot: sRepoRoot,
            iNumberOfCores: _dictWorkflowState.dictWorkflow.iNumberOfCores || -1,
            sFigureType: (_dictWorkflowState.dictWorkflow.sFigureType || "pdf").toLowerCase(),
        };
        _dictWorkflowState.dictWorkflow.listSteps.forEach(function (step, iIdx) {
            var sStepDir = step.sDirectory || "";
            var iNum = iIdx + 1;
            var sPrefix = "Step" + String(iNum).padStart(2, "0");
            var listFiles = (step.saOutputDataFiles || []).concat(
                step.saPlotFiles || []);
            listFiles.forEach(function (sFile) {
                var sResolved = sFile.replace(
                    /\{([^}]+)\}/g, function (m, t) {
                        return dictVars[t] || m;
                    });
                if (sResolved.charAt(0) !== "/") {
                    sResolved = sStepDir + "/" + sResolved;
                }
                var sBase = sResolved.split("/").pop();
                var sStem = sBase.replace(/\.[^.]+$/, "");
                dictVars[sPrefix + "." + sStem] = sResolved;
            });
        });
        return dictVars;
    }

    var fsResolveTemplate = VaibifyUtilities.fsResolveTemplate;

    function fsJoinPath(sDirectory, sFilename) {
        if (!sDirectory || sDirectory === ".") {
            return sFilename;
        }
        if (sDirectory.endsWith("/")) {
            return sDirectory + sFilename;
        }
        return sDirectory + "/" + sFilename;
    }

    function fsShortenPath(sResolved, sWorkdir) {
        if (!sWorkdir || !sResolved) return sResolved;
        var sPrefix = sWorkdir.endsWith("/") ?
            sWorkdir : sWorkdir + "/";
        if (sResolved.startsWith(sPrefix)) {
            return sResolved.substring(sPrefix.length);
        }
        return sResolved;
    }

    function fsGetWorkflowDirectory() {
        if (!_dictWorkflowState.sWorkflowPath) return "/workspace";
        var iLastSlash = _dictWorkflowState.sWorkflowPath.lastIndexOf("/");
        return iLastSlash > 0 ? _dictWorkflowState.sWorkflowPath.substring(0, iLastSlash) : "/workspace";
    }

    /* --- Global Settings --- */

    function fsSettingsRowHtml(sLabel, sInputHtml) {
        return '<div class="gs-row">' +
            '<span class="gs-label">' + sLabel + '</span>' +
            sInputHtml + '</div>';
    }

    function fsGlobalSettingsHtml() {
        var iToleranceExp = fsToleranceToExponent(
            _dictWorkflowState.dictWorkflow.fTolerance || 1e-6);
        return fsSettingsRowHtml("Plot Dir",
            '<input class="gs-input" id="gsPlotDirectory" value="' +
            fnEscapeHtml(_dictWorkflowState.dictWorkflow.sPlotDirectory || "Plot") + '">') +
            fsSettingsRowHtml("Figure Type",
            '<input class="gs-input" id="gsFigureType" value="' +
            fnEscapeHtml(_dictWorkflowState.dictWorkflow.sFigureType || "pdf") + '">') +
            fsSettingsRowHtml("Cores",
            '<input class="gs-input" id="gsNumberOfCores" type="number" value="' +
            (_dictWorkflowState.dictWorkflow.iNumberOfCores || -1) + '">') +
            fsSettingsRowHtml("Tolerance",
            '<input class="gs-input" id="gsTolerance" type="range"' +
            ' min="-16" max="0" step="1" value="' + iToleranceExp +
            '" title="10^' + iToleranceExp +
            ' = ' + (_dictWorkflowState.dictWorkflow.fTolerance || 1e-6) + '">') +
            fsSettingsRowHtml("Poll Interval",
            '<input class="gs-input" id="gsPollInterval" type="range"' +
            ' min="1" max="60" value="' +
            (VaibifyPolling.fiGetPollIntervalMs() / 1000) +
            '" title="' +
            (VaibifyPolling.fiGetPollIntervalMs() / 1000) +
            ' seconds">') +
            fsSettingsRowHtml("Show timestamps",
            '<input type="checkbox" id="gsShowTimestamps"' +
            (_dictUiState.bShowTimestamps ? " checked" : "") + '>') +
            fsSettingsRowHtml("Terminal lines",
            '<input id="gsTerminalScrollback" class="gs-input-local"' +
            ' type="number" min="100"' +
            ' value="' + PipeleyenTerminal.fiGetScrollback() + '"' +
            (PipeleyenTerminal.fbScrollbackIsUnlimited()
                ? " disabled" : "") +
            ' title="Lines of terminal scrollback to retain (min 100)">' +
            ' <label class="gs-inline-check" title="Retain up to' +
            ' 1,000,000 lines — effectively unlimited; protects' +
            ' browser memory"><input type="checkbox"' +
            ' id="gsTerminalScrollbackUnlimited"' +
            (PipeleyenTerminal.fbScrollbackIsUnlimited()
                ? " checked" : "") + '> &#8734;</label>') +
            fsSettingsRowHtml("Auto Archive",
            '<input type="checkbox" id="gsAutoArchive"' +
            (_dictWorkflowState.dictWorkflow.bAutoArchive
                ? " checked" : "") +
            ' title="Push verified files to Overleaf/Zenodo automatically">') +
            fsSettingsRowHtml("Runtime limit (s)",
            '<input class="gs-input" id="gsDefaultWallClockBudget"' +
            ' type="number" min="0" step="1" value="' +
            (_dictWorkflowState.dictWorkflow
                .fDefaultWallClockBudgetSeconds || 0) + '"' +
            ' title="Default expected runtime in seconds applied to' +
            ' every step without its own value. A step that runs longer' +
            ' turns its run light red as a possibly-hung warning; the' +
            ' run is never stopped. 0 = no limit.">') +
            fsClaudeSettingsHtml();
    }

    function fsClaudeSettingsHtml() {
        var dictSettings = _dictWorkflowState.dictContainerSettings;
        if (!dictSettings || !dictSettings.bClaudeInstalled) {
            return "";
        }
        var sChecked = dictSettings.bClaudeAutoUpdate ? " checked" : "";
        var sNotice = _dictWorkflowState.bClaudeRestartNeeded
            ? '<div class="gs-notice">Restart the container to '
              + 'apply the new Claude auto-update setting.</div>'
            : "";
        return '<div class="gs-section-heading">Container</div>' +
            fsSettingsRowHtml("Claude auto-update",
                '<input type="checkbox" id="gsClaudeAutoUpdate"'
                + sChecked + '>') +
            sNotice;
    }

    function fnBindSettingsSliders() {
        var elPollSlider = document.getElementById("gsPollInterval");
        if (elPollSlider) {
            elPollSlider.addEventListener("input", function () {
                fnSetPollInterval(parseInt(elPollSlider.value, 10));
            });
        }
        var elToleranceSlider = document.getElementById("gsTolerance");
        if (elToleranceSlider) {
            elToleranceSlider.addEventListener("input", function () {
                var iExp = parseInt(elToleranceSlider.value, 10);
                var fVal = Math.pow(10, iExp);
                elToleranceSlider.title =
                    "10^" + iExp + " = " + fVal;
                _dictWorkflowState.dictWorkflow.fTolerance = fVal;
            });
        }
        var elTimestampCheckbox = document.getElementById(
            "gsShowTimestamps");
        if (elTimestampCheckbox) {
            elTimestampCheckbox.addEventListener(
                "change", function () {
                    fnToggleShowTimestamps(
                        elTimestampCheckbox.checked);
                });
        }
        var elAutoArchive = document.getElementById("gsAutoArchive");
        if (elAutoArchive) {
            elAutoArchive.addEventListener(
                "change", fnSaveGlobalSettings);
        }
        var elClaudeAuto = document.getElementById(
            "gsClaudeAutoUpdate");
        if (elClaudeAuto) {
            elClaudeAuto.addEventListener(
                "change", function () {
                    fnSaveClaudeAutoUpdate(elClaudeAuto.checked);
                });
        }
        fnBindTerminalScrollbackControls();
    }

    function fnApplyTerminalScrollbackSetting(elNum, elUnlimited) {
        var bUnlimited = elUnlimited.checked;
        elNum.disabled = bUnlimited;
        PipeleyenTerminal.fnSetScrollback(
            parseInt(elNum.value, 10), bUnlimited);
        elNum.value = PipeleyenTerminal.fiGetScrollback();
    }

    function fnBindTerminalScrollbackControls() {
        var elNum = document.getElementById("gsTerminalScrollback");
        var elUnlimited = document.getElementById(
            "gsTerminalScrollbackUnlimited");
        if (!elNum || !elUnlimited) return;
        elNum.addEventListener("change", function () {
            fnApplyTerminalScrollbackSetting(elNum, elUnlimited);
        });
        elUnlimited.addEventListener("change", function () {
            fnApplyTerminalScrollbackSetting(elNum, elUnlimited);
        });
    }

    async function fnLoadContainerSettings() {
        var sId = _dictSessionState.sContainerId;
        if (!sId) return;
        try {
            var dictResult = await VaibifyApi.fdictGet(
                "/api/containers/"
                + encodeURIComponent(sId) + "/settings");
            _dictWorkflowState.dictContainerSettings = dictResult;
            fnRenderGlobalSettings();
        } catch (error) {
            _dictWorkflowState.dictContainerSettings = null;
        }
    }

    function fnApplyClaudeSaveResult(bValue, dictResult) {
        if (_dictWorkflowState.dictContainerSettings) {
            _dictWorkflowState.dictContainerSettings
                .bClaudeAutoUpdate = bValue;
        }
        _dictWorkflowState.bClaudeRestartNeeded =
            Boolean(dictResult && dictResult.bRestartRequired);
        fnRenderGlobalSettings();
        fnShowToast("Claude setting saved", "success");
    }

    async function fnSaveClaudeAutoUpdate(bValue) {
        var sId = _dictSessionState.sContainerId;
        if (!sId) return;
        try {
            var dictResult = await VaibifyApi.fdictPost(
                "/api/containers/"
                + encodeURIComponent(sId) + "/settings",
                { bClaudeAutoUpdate: bValue });
            fnApplyClaudeSaveResult(bValue, dictResult);
        } catch (error) {
            fnShowToast(
                "Failed to save Claude setting", "error");
        }
    }

    function fnRenderGlobalSettings() {
        if (!_dictWorkflowState.dictWorkflow) return;
        var el = document.getElementById("globalSettingsPanel");
        el.innerHTML = fsGlobalSettingsHtml();
        el.querySelectorAll(".gs-input").forEach(function (inp) {
            inp.addEventListener("change", fnSaveGlobalSettings);
        });
        fnBindSettingsSliders();
    }

    function fnToggleShowTimestamps(bEnabled) {
        _dictUiState.bShowTimestamps = bEnabled;
        try {
            localStorage.setItem(
                "vaibifyShowTimestamps",
                bEnabled ? "true" : "false");
        } catch (e) { /* localStorage may be unavailable */ }
        fnApplyTimestampVisibility();
        fnRenderStepList();
    }

    function fnApplyTimestampVisibility() {
        var elList = document.getElementById("listSteps");
        if (!elList) return;
        if (_dictUiState.bShowTimestamps) {
            elList.classList.remove("hide-timestamps");
        } else {
            elList.classList.add("hide-timestamps");
        }
    }

    function fnLoadTimestampSetting() {
        try {
            var sStored = localStorage.getItem(
                "vaibifyShowTimestamps");
            _dictUiState.bShowTimestamps = sStored === "true";
        } catch (e) { /* localStorage may be unavailable */ }
    }

    function _fsStepsCollapsedKey() {
        return "vaibify.stepsCollapsed." +
            (_dictWorkflowState.sWorkflowPath || "");
    }

    function _fsStepsAutoCollapsedKey() {
        return "vaibify.stepsAutoCollapsed." +
            (_dictWorkflowState.sWorkflowPath || "");
    }

    function _fnPersistStepsCollapsed(bCollapsed) {
        try {
            localStorage.setItem(
                _fsStepsCollapsedKey(), bCollapsed ? "1" : "0");
        } catch (e) { /* localStorage may be unavailable */ }
    }

    function _fnLoadStepsCollapsed() {
        // Default expanded on a fresh workflow (no stored choice).
        try {
            _dictUiState.bStepsCollapsed =
                localStorage.getItem(_fsStepsCollapsedKey()) === "1";
        } catch (e) {
            _dictUiState.bStepsCollapsed = false;
        }
    }

    function _fnMaybeAutoCollapseStepsOnFirstL1(iAICSLevel) {
        // Collapse the Steps block the first time this workflow
        // reaches L1; a one-shot guard means the user's manual choice
        // wins thereafter. Uses the authoritative server level. When
        // localStorage is unavailable the one-shot cannot be tracked,
        // so auto-collapse is skipped rather than fired every poll.
        if (typeof iAICSLevel !== "number" || iAICSLevel < 1) return;
        var bAlready;
        try {
            bAlready =
                localStorage.getItem(_fsStepsAutoCollapsedKey()) === "1";
        } catch (e) {
            return;
        }
        if (bAlready) return;
        try {
            localStorage.setItem(_fsStepsAutoCollapsedKey(), "1");
            localStorage.setItem(_fsStepsCollapsedKey(), "1");
        } catch (e) {
            return;
        }
        _dictUiState.bStepsCollapsed = true;
    }

    function fsToleranceToExponent(fTolerance) {
        return Math.round(Math.log10(fTolerance));
    }

    async function fnSaveGlobalSettings() {
        var iExp = parseInt(
            document.getElementById("gsTolerance").value, 10);
        var elAutoArchive = document.getElementById("gsAutoArchive");
        var elBudget = document.getElementById(
            "gsDefaultWallClockBudget");
        var fBudget = elBudget ? parseFloat(elBudget.value) : 0;
        var dictUpdates = {
            sPlotDirectory: document.getElementById("gsPlotDirectory").value,
            sFigureType: document.getElementById("gsFigureType").value,
            iNumberOfCores: parseInt(
                document.getElementById("gsNumberOfCores").value
            ),
            fTolerance: Math.pow(10, iExp),
            bAutoArchive: elAutoArchive
                ? elAutoArchive.checked : false,
            fDefaultWallClockBudgetSeconds:
                (isNaN(fBudget) || fBudget < 0) ? 0 : fBudget,
        };
        try {
            var result = await VaibifyApi.fdictPut(
                "/api/settings/" + _dictSessionState.sContainerId, dictUpdates);
            _dictWorkflowState.dictWorkflow.sPlotDirectory = result.sPlotDirectory;
            _dictWorkflowState.dictWorkflow.sFigureType = result.sFigureType;
            _dictWorkflowState.dictWorkflow.iNumberOfCores = result.iNumberOfCores;
            if (result.fTolerance !== undefined) {
                _dictWorkflowState.dictWorkflow.fTolerance = result.fTolerance;
            }
            if (result.bAutoArchive !== undefined) {
                _dictWorkflowState.dictWorkflow.bAutoArchive =
                    result.bAutoArchive;
            }
            if (result.fDefaultWallClockBudgetSeconds !== undefined) {
                _dictWorkflowState.dictWorkflow
                    .fDefaultWallClockBudgetSeconds =
                    result.fDefaultWallClockBudgetSeconds;
            }
            fnShowToast("Settings saved", "success");
            fnRenderStepList();
        } catch (error) {
            fnShowToast("Failed to save settings", "error");
        }
    }

    /* --- Step List --- */

    function _fsFindAiDeclarationFile() {
        // The declaration file of the workflow's ai-declaration step,
        // for the Project-block Publication "AI Declaration" row.
        var listSteps = (_dictWorkflowState.dictWorkflow || {})
            .listSteps || [];
        for (var i = 0; i < listSteps.length; i++) {
            if (listSteps[i].sStepKind === "ai-declaration") {
                return listSteps[i].sDeclarationFile || "";
            }
        }
        return "";
    }

    function fdictBuildRenderContext() {
        return {
            dictStepStatus: _dictWorkflowState.dictStepStatus,
            iSelectedStepIndex: _dictUiState.iSelectedStepIndex,
            setExpandedSteps: _dictUiState.setExpandedSteps,
            setExpandedDeps: _dictUiState.setExpandedDeps,
            setExpandedRequirementGroups:
                _dictUiState.setExpandedRequirementGroups,
            setExpandedRequirementRows:
                _dictUiState.setExpandedRequirementRows,
            bProjectBlockCollapsed: _dictUiState.bProjectBlockCollapsed,
            bBinaryAddFormOpen: _dictUiState.bBinaryAddFormOpen,
            sProjectRepoPath: (_dictWorkflowState.dictWorkflow || {})
                .sProjectRepoPath || "",
            sAiDeclarationFile: _fsFindAiDeclarationFile(),
            setExpandedUnitTests: PipeleyenTestManager.fsetGetExpandedUnitTests(),
            fdictGetFalsificationState:
                PipeleyenTestManager.fdictGetFalsificationState,
            setStepsWithData: PipeleyenTestManager.fsetGetStepsWithData(),
            setGeneratingInFlight: PipeleyenTestManager.fsetGetGeneratingInFlight(),
            dictPlotStandardExists: _dictWorkflowState.dictPlotStandardExists,
            dictScriptModified: _dictWorkflowState.dictScriptModified,
            dictStaleArtifacts: _dictWorkflowState.dictStaleArtifacts,
            dictOutputMtimes: _dictWorkflowState.dictOutputMtimes,
            dictMaxDataMtimeByStep:
                _dictWorkflowState.dictMaxDataMtimeByStep,
            dictMaxInputMtimeByStep:
                _dictWorkflowState.dictMaxInputMtimeByStep,
            dictMaxPlotMtimeByStep: _dictWorkflowState.dictPlotMtimes,
            dictMarkerMtimeByStep:
                _dictWorkflowState.dictMarkerMtimeByStep,
            dictTestCategoryMtimes:
                _dictWorkflowState.dictTestCategoryMtimes,
            dictDiscoveredOutputs: _dictWorkflowState.dictDiscoveredOutputs,
            dictWorkflow: _dictWorkflowState.dictWorkflow,
            sUserName: _dictSessionState.sUserName,
            fsComputeStepLabel: fsComputeStepLabel,
            fsResolveTemplate: fsResolveTemplate,
            fsJoinPath: fsJoinPath,
            fsShortenPath: fsShortenPath,
            fsInitialFileStatusClass: fsInitialFileStatusClass,
            fsGetFileCategory: fsGetFileCategory,
            fdictGetVerification: fdictGetVerification,
            fdictGetTests: fdictGetTests,
            fsEffectiveTestState: fsEffectiveTestState,
            fsComputeDepsState: fsComputeDepsState,
            fsGetCategoryState: fsGetCategoryState,
            fsTestCategoryLabel: fsTestCategoryLabel,
            fsVerificationStateLabel: fsVerificationStateLabel,
            fsVerificationStateIcon: fsVerificationStateIcon,
            flistGetStepDependencies: flistGetStepDependencies,
            fbStepFullyPassing: fbStepFullyPassing,
            fbAnyUpstreamModified: fbAnyUpstreamModified,
            ftComputeDepAxisStates: ftComputeDepAxisStates,
            fsetGetExpandedCategory: fsetGetExpandedCategory,
            fdictBuildClientVariables: fdictBuildClientVariables,
            dictBlockersByStep: _dictWorkflowState.dictBlockersByStep,
            dictBlockersByStepLevel2:
                _dictWorkflowState.dictBlockersByStepLevel2,
            dictBlockersByStepLevel3:
                _dictWorkflowState.dictBlockersByStepLevel3,
            dictStepLevels: _dictWorkflowState.dictStepLevels,
            dictStepLevelHighWater:
                _dictWorkflowState.dictStepLevelHighWater,
            dictStepLevelWarnings:
                _dictWorkflowState.dictStepLevelWarnings,
            dictWorkflowEnvelopeDetail:
                _dictWorkflowState.dictWorkflowEnvelopeDetail,
            fbFileIsL1Offending: fbFileIsL1Offending,
            fbUpstreamStepIsL1Offending: fbUpstreamStepIsL1Offending,
            fsBuildL1FailureGlyph: fsBuildL1FailureGlyph,
            fsBuildFileMarkGlyph: fsBuildFileMarkGlyph,
            fsBlockerHintForStep: fsBlockerHintForStep,
            fsBlockerHintForFile: fsBlockerHintForFile,
            fsLevelCellState: fsLevelCellState,
            fsLevelCellTooltip: fsLevelCellTooltip,
            fdictRegressionWarning: fdictRegressionWarning,
        };
    }

    var _bRenderScheduled = false;

    // Per-step render memoization. _dictRenderedStepHashes maps
    // iIndex -> sHash, where sHash captures every input
    // fsRenderStepItem reads. On each render the loop skips steps
    // whose hash is unchanged and replaces only the changed cards in
    // place, instead of blowing away all 5K-20K DOM nodes for 100
    // steps every poll tick. Structural changes (step count delta,
    // interactive-boundary shift) trigger a full innerHTML rebuild.
    var _dictRenderedStepHashes = {};
    var _sLastBoundarySignature = null;

    // Change 6: dependency-graph memo. flistGetStepDependencies is
    // O(N) per step (directory-overlap probe against every prior
    // step), so the naive call site is O(N^2) per render. The memo
    // is cleared by _fnInvalidateAllRenderCaches on workflow swap.
    var _dictStepDepsByIndex = {};
    // Reverse index used by the badge-driven partial-render entry:
    // sFilePath -> iStep, so a change set of affected files can be
    // mapped to step indices in O(F) instead of O(N x F).
    var _dictStepIndexByFilePath = {};

    // Change 7: step-label memo. The legacy fallback in
    // fsComputeStepLabel walks 0..iIndex to count interactive vs
    // automated steps, which is O(N) per call. Build the entire
    // index in one forward pass on workflow load.
    var _dictStepLabelByIndex = {};

    function _fnInvalidateAllRenderCaches() {
        _dictRenderedStepHashes = {};
        _sLastBoundarySignature = null;
        _dictStepDepsByIndex = {};
        _dictStepIndexByFilePath = {};
        _dictStepLabelByIndex = {};
    }

    function _fnInvalidateRenderCache(iIndex) {
        delete _dictRenderedStepHashes[iIndex];
    }

    function fnRenderStepList() {
        if (_bRenderScheduled) return;
        _bRenderScheduled = true;
        requestAnimationFrame(function () {
            _bRenderScheduled = false;
            _fnRenderStepListImmediate();
        });
    }

    function fnRenderStepListSync() {
        _bRenderScheduled = false;
        _fnRenderStepListImmediate();
    }

    function _fsBoundarySignature(listSteps) {
        // Compact key that changes when the step count or the
        // automated/interactive/declaration boundary pattern shifts.
        // Used to detect "structural change" so the renderer falls
        // back to the full innerHTML rebuild (which re-emits the
        // column header + step-type banners) instead of only swapping
        // individual step wrappers.
        //
        // The Project block is NOT part of this signature: it
        // lives in its own container and is rebuilt on every render,
        // so its expansion Sets can never cause a skip-repaint. A
        // future maintainer who memoizes that block MUST fold
        // setExpandedRequirementGroups / setExpandedRequirementRows
        // into a signature of its own.
        var sKey = String(listSteps.length);
        for (var i = 0; i < listSteps.length; i++) {
            if (listSteps[i].sStepKind === "ai-declaration") {
                sKey += "D";
            } else {
                sKey += listSteps[i].bInteractive === true ? "I" : "A";
            }
        }
        return sKey;
    }

    function _fsExpansionSliceForStep(iIndex, dictContext) {
        // Captures every UI-expansion bit that fsRenderStepItem
        // reads for this step. Each toggle handler mutates one of
        // these Sets and calls fnRenderStepList — if the bit is not
        // in the hash, the incremental path silently skips the
        // re-render and the user's click appears to do nothing.
        return (
            (iIndex === dictContext.iSelectedStepIndex ? "1" : "0")
            + (dictContext.setExpandedSteps.has(iIndex) ? "1" : "0")
            + (dictContext.setExpandedDeps.has(iIndex) ? "1" : "0")
            + (dictContext.setExpandedUnitTests.has(iIndex) ? "1" : "0")
            + (dictContext.setGeneratingInFlight.has(iIndex) ? "1" : "0")
            + (dictContext.setStepsWithData.has(iIndex) ? "1" : "0")
            + (dictContext.fsetGetExpandedCategory("qualitative").has(iIndex) ? "1" : "0")
            + (dictContext.fsetGetExpandedCategory("quantitative").has(iIndex) ? "1" : "0")
            + (dictContext.fsetGetExpandedCategory("integrity").has(iIndex) ? "1" : "0")
        );
    }

    function _fsContextSliceForStep(iIndex, dictContext) {
        // Per-step slice of the context dicts that vary independently
        // of the step object itself. Anything fsRenderStepItem reads
        // off dictContext keyed by iIndex must be represented here or
        // the incremental renderer will leave a stale card on screen.
        var sIdx = String(iIndex);
        return JSON.stringify([
            dictContext.dictScriptModified[iIndex] || "",
            dictContext.dictStaleArtifacts[iIndex] || null,
            dictContext.dictDiscoveredOutputs[iIndex] || null,
            dictContext.dictOutputMtimes[sIdx] || "",
            dictContext.dictMarkerMtimeByStep[sIdx] || "",
            dictContext.dictMaxDataMtimeByStep[sIdx] || "",
            (dictContext.dictMaxInputMtimeByStep || {})[sIdx] || "",
            dictContext.dictMaxPlotMtimeByStep[sIdx] || "",
            dictContext.dictTestCategoryMtimes[sIdx] || null,
            dictContext.fdictGetFalsificationState ?
                dictContext.fdictGetFalsificationState(iIndex) : null,
        ].concat(_flistBlockerAndLevelSlice(iIndex, dictContext)));
    }

    function _flistBlockerAndLevelSlice(iIndex, dictContext) {
        // Blocker entries and level-cell inputs the card renders.
        // Without these, a poll that flips a blocker or a level cell
        // leaves a stale card on screen under the incremental path.
        var sIdx = String(iIndex);
        return [
            (dictContext.dictBlockersByStep || {})[iIndex] || null,
            (dictContext.dictBlockersByStepLevel2 || {})[iIndex] ||
                null,
            (dictContext.dictBlockersByStepLevel3 || {})[iIndex] ||
                null,
            (dictContext.dictStepLevels || {})[sIdx] || null,
            (dictContext.dictStepLevelHighWater || {})[sIdx] || null,
            (dictContext.dictStepLevelWarnings || {})[sIdx] || null,
        ];
    }

    function _fsDeclarationBadgeSlice(step) {
        // The declaration commit/remove buttons gate on the file's
        // git badge at render time; the hash must move when the
        // badge does, or the incremental path leaves the stale
        // commit-only card on screen (the same signature-omission
        // class as _fsExpansionSliceForStep documents).
        if (!step || step.sStepKind !== "ai-declaration") return "";
        var sFile = (step.sDeclarationFile || "").trim();
        if (!sFile || typeof VaibifyGitBadges === "undefined") {
            return "";
        }
        var dictBadges = VaibifyGitBadges.fdictGetBadgesForFile(
            sFile, "");
        return (dictBadges && dictBadges.sGithub) || "";
    }

    function _fsComputeStepRenderHash(step, iIndex, dictContext, dictVars) {
        // The hash captures every render-affecting input
        // fsRenderStepItem reads: the step object itself plus the
        // per-step slices of dictContext and the resolved template
        // variables (so a global-setting save invalidates every
        // card's hash).
        return JSON.stringify(step)
            + "\x01" + (dictContext.dictStepStatus[iIndex] || "")
            + "\x01" + _fsExpansionSliceForStep(iIndex, dictContext)
            + "\x01" + _fsContextSliceForStep(iIndex, dictContext)
            + "\x01" + _fsDeclarationBadgeSlice(step)
            + "\x01" + JSON.stringify(dictVars || {});
    }

    function _fnRenderStepListImmediate() {
        if (typeof VaibifySyncManager !== "undefined"
            && typeof VaibifySyncManager.fnDismissAllPicklists
                === "function") {
            VaibifySyncManager.fnDismissAllPicklists();
        }
        var elList = document.getElementById("listSteps");
        if (!_dictWorkflowState.dictWorkflow || !_dictWorkflowState.dictWorkflow.listSteps) {
            elList.innerHTML = "";
            _fnInvalidateAllRenderCaches();
            return;
        }
        var listSteps = _dictWorkflowState.dictWorkflow.listSteps;
        var sBoundary = _fsBoundarySignature(listSteps);
        var dictVars = fdictBuildClientVariables();
        var dictContext = fdictBuildRenderContext();
        if (sBoundary !== _sLastBoundarySignature) {
            _fnRenderStepListFull(
                elList, listSteps, dictVars, dictContext, sBoundary);
        } else {
            _fnRenderStepListIncremental(
                elList, listSteps, dictVars, dictContext);
        }
        _fnRenderProjectBlock(dictContext);
        _fnApplyStepsCollapsedClass();
        fnApplyTimestampVisibility();
        fnBindStepEvents();
        fnUpdateHighlightState();
        VaibifyStepRenderer.fnFillAiDeclarationPreviews();
        PipeleyenFileOps.fnScheduleFileExistenceCheck(
            _dictWorkflowState);
    }

    function _fnRenderStepListFull(
        elList, listSteps, dictVars, dictContext, sBoundary
    ) {
        var sHtml = VaibifyStepRenderer.fsRenderStepColumnHeader();
        var bPrior = null;
        _dictRenderedStepHashes = {};
        listSteps.forEach(function (step, iIndex) {
            var bInteractive = step.bInteractive === true;
            if (bInteractive !== bPrior) {
                sHtml += fsRenderStepTypeBanner(bInteractive);
                bPrior = bInteractive;
            }
            sHtml += VaibifyStepRenderer.fsRenderStepItem(
                step, iIndex, dictVars, dictContext);
            _dictRenderedStepHashes[iIndex] = _fsComputeStepRenderHash(
                step, iIndex, dictContext, dictVars);
        });
        elList.innerHTML = sHtml;
        _sLastBoundarySignature = sBoundary;
    }

    var _sLastProjectBlockHtml = null;

    function _fnRenderProjectBlock(dictContext) {
        // Rebuilt from data on every render — the block lives in its
        // own container, never in the incremental step-hash path, so
        // the requirement group/row expansion Sets need no render
        // signature (expansion state is IN the rendered output). The
        // DOM write is skipped when the output is byte-identical so a
        // steady-state poll never wipes in-progress form input (the
        // determinism declare form) or a text selection.
        var elBlock = document.getElementById("projectBlock");
        if (!elBlock) return;
        if (!_dictWorkflowState.dictWorkflow) {
            elBlock.innerHTML = "";
            _sLastProjectBlockHtml = null;
            return;
        }
        var sHtml =
            VaibifyWorkflowRequirements.fsRenderProjectBlock(
                dictContext);
        if (sHtml === _sLastProjectBlockHtml) return;
        elBlock.innerHTML = sHtml;
        _sLastProjectBlockHtml = sHtml;
    }

    function _fnApplyStepsCollapsedClass() {
        var elBlock = document.getElementById("stepsBlock");
        if (!elBlock) return;
        elBlock.classList.toggle(
            "collapsed", _dictUiState.bStepsCollapsed === true);
        // The Steps header is static HTML; keep its aggregate status
        // light current here so the banner conveys the total step
        // state even while collapsed.
        var elStatus = elBlock.querySelector(".steps-block-status");
        if (elStatus) {
            elStatus.innerHTML = _fsRenderStepsAggregateLight();
        }
    }

    var _DICT_STEPS_AGGREGATE_TOOLTIP = {
        "attained": "Every step is self-consistent (Level 1)",
        "partial": "Some steps have unmet Level 1 requirements",
        "none": "Every started step is failing Level 1",
        "unknown": "Step status is not yet known",
        "not-started": "No step has started yet",
        "unassessed": "Step outputs exist but none have been " +
            "assessed yet",
        "not-applicable": "No steps with Level 1 requirements",
    };

    function _fsAggregateStepsL1State() {
        // The total Level-1 state across every step, summarized by
        // the shared banner rule (see
        // VaibifyUtilities.fsSummarizeLevelStates): red only when
        // every started step is failing; any progress in the mix
        // reads orange.
        var listSteps = (_dictWorkflowState.dictWorkflow || {})
            .listSteps || [];
        if (listSteps.length === 0) return "not-started";
        var listStates = listSteps.map(function (dictStep, iIndex) {
            return fsLevelCellState(iIndex, 1);
        });
        return VaibifyUtilities.fsSummarizeLevelStates(listStates);
    }

    function _fsRenderStepsAggregateLight() {
        // A full L1|L2|L3 strip (with a leading warning-column
        // spacer) so the collapsed Steps banner lines up with the
        // Project banner strip. L1 carries the aggregate step
        // state; L2/L3 are dashes — those levels are project-wide,
        // not per-step.
        var sState = _fsAggregateStepsL1State();
        var sLevelCell = VaibifyUtilities.fsBuildLevelCell(
            sState,
            _DICT_STEPS_AGGREGATE_TOOLTIP[sState] || "Step status",
            "all steps passing");
        var sDashCell = '<span class="step-level-cell ' +
            'level-cell-not-applicable" title="No step-level ' +
            'requirements at this level — see the Project block">' +
            '<span class="level-cell-dash">&#8212;</span></span>';
        return '<span class="step-level-strip">' +
            '<span class="step-regression-cell"></span>' +
            sLevelCell + sDashCell + sDashCell + '</span>';
    }

    function _fnRenderStepListIncremental(
        elList, listSteps, dictVars, dictContext
    ) {
        listSteps.forEach(function (step, iIndex) {
            var sHash = _fsComputeStepRenderHash(
                step, iIndex, dictContext, dictVars);
            if (_dictRenderedStepHashes[iIndex] === sHash) return;
            var sHtml = VaibifyStepRenderer.fsRenderStepItem(
                step, iIndex, dictVars, dictContext);
            var elExisting = elList.querySelector(
                ".step-wrapper[data-step-index=\"" + iIndex + "\"]");
            if (!elExisting) return;
            var elTemp = document.createElement("div");
            elTemp.innerHTML = sHtml;
            var elNew = elTemp.firstElementChild;
            if (!elNew) return;
            elExisting.replaceWith(elNew);
            _dictRenderedStepHashes[iIndex] = sHash;
        });
    }

    function fnRenderStepListPartial(listAffectedFiles) {
        // Maps changed files to step indices via the reverse map.
        // When no file matches (badge keys are workspace-relative;
        // raw step file values may not be), invalidate everything so
        // the next render rebuilds rather than leaving stale badges.
        if (!listAffectedFiles || !listAffectedFiles.length) {
            return fnRenderStepList();
        }
        var iMatched = 0;
        listAffectedFiles.forEach(function (sFile) {
            var iStep = _dictStepIndexByFilePath[sFile];
            if (iStep === undefined) return;
            _fnInvalidateRenderCache(iStep);
            iMatched++;
        });
        if (iMatched === 0) _dictRenderedStepHashes = {};
        fnRenderStepList();
    }

    function fsRenderStepTypeBanner(bInteractive) {
        var sLabel = bInteractive ?
            "Interactive Steps" : "Automatic Steps";
        return '<div class="step-type-banner">' + sLabel + '</div>';
    }

    function fnClearRunningStatuses() {
        for (var sKey in _dictWorkflowState.dictStepStatus) {
            var sVal = _dictWorkflowState.dictStepStatus[sKey];
            if (sVal === "running" || sVal === "queued"
                || sVal === "overBudget") {
                delete _dictWorkflowState.dictStepStatus[sKey];
            }
        }
    }

    var _bReflectedDispatchRun = false;

    function _fnReflectDispatchedRunState(dictRunState) {
        // The continuously-polled /status payload surfaces any
        // dispatched run's active step — including an in-container
        // agent's run-step/runSelected — so the running marker lights
        // even for runs this browser did not initiate. Only the active
        // step is touched; the pipeline-run poll owns the fuller
        // queued/completed vocabulary for browser-initiated runs. The
        // marker is cleared on the running->idle transition so a
        // finished out-of-band run never sticks as "running".
        if (!dictRunState) return;
        if (dictRunState.bRunning && dictRunState.iActiveStep > 0) {
            // An active step that has outrun its declared wall-clock
            // budget still runs, but is flagged distinctly so a hung
            // step is no longer indistinguishable from a legitimately
            // long one. The backend computes this live each poll.
            _dictWorkflowState.dictStepStatus[
                dictRunState.iActiveStep - 1] =
                dictRunState.bActiveStepOverBudget
                    ? "overBudget" : "running";
            _bReflectedDispatchRun = true;
            fnRenderStepList();
        } else if (_bReflectedDispatchRun) {
            fnClearRunningStatuses();
            _bReflectedDispatchRun = false;
            fnRenderStepList();
        }
    }

    function fnResetQueuedSteps(listStepIndices) {
        /* A refused dispatch resets only the lights it optimistically
         * set to "queued"; a live run's "running" lights stay. With no
         * indices (runAll/runFrom refusals), clear every queued light. */
        if (listStepIndices && listStepIndices.length > 0) {
            listStepIndices.forEach(function (iIndex) {
                if (_dictWorkflowState.dictStepStatus[iIndex] === "queued") {
                    delete _dictWorkflowState.dictStepStatus[iIndex];
                }
            });
            return;
        }
        for (var sKey in _dictWorkflowState.dictStepStatus) {
            if (_dictWorkflowState.dictStepStatus[sKey] === "queued") {
                delete _dictWorkflowState.dictStepStatus[sKey];
            }
        }
    }

    function fnPruneStaleStatuses() {
        var iStepCount = (_dictWorkflowState.dictWorkflow && _dictWorkflowState.dictWorkflow.listSteps)
            ? _dictWorkflowState.dictWorkflow.listSteps.length : 0;
        for (var sKey in _dictWorkflowState.dictStepStatus) {
            if (parseInt(sKey, 10) >= iStepCount) {
                delete _dictWorkflowState.dictStepStatus[sKey];
            }
        }
    }

    function fnInvalidateStepFileCache(iStep) {
        var sPrefix = iStep + ":";
        Object.keys(_dictWorkflowState.dictFileExistenceCache).forEach(function (sKey) {
            if (sKey.indexOf(sPrefix) === 0) {
                delete _dictWorkflowState.dictFileExistenceCache[sKey];
            }
        });
        PipeleyenTestManager.fsetGetStepsWithData().delete(iStep);
    }

    function fnPollAllStepFiles() {
        if (!_dictSessionState.sContainerId ||
            !_dictWorkflowState.dictWorkflow) return;
        // Skip the per-step existence check for any step whose output
        // mtime is already populated by the polling response — its
        // files demonstrably exist on disk, so re-issuing the check
        // would waste ~1000 file probes per poll on a 100-step
        // workflow. New / never-run steps still get the check.
        var dictMtimes = _dictWorkflowState.dictOutputMtimes || {};
        _dictWorkflowState.dictWorkflow.listSteps.forEach(
            function (step, iStep) {
                if (dictMtimes[String(iStep)]) return;
                PipeleyenFileOps.fnCheckStepDataFiles(
                    step, iStep, _dictWorkflowState);
            });
    }

    function fbStepRequiresUnitTests(dictStep) {
        if (dictStep.bInteractive) return false;
        if ((dictStep.saDataCommands || []).length === 0) return false;
        return true;
    }

    function fbStepIsAtLeastLevel1(dictStep, iStep) {
        if (dictStep && dictStep.sStepKind === "ai-declaration") {
            /* Declaration steps are L1-not-applicable by the
             * 2026-07-02 ruling (their sign-off is an L2 criterion;
             * the L1 cell renders a dash, and the server emits no L1
             * blockers for them). Without this mirror of the server
             * rule the step has no data and no "passed" badge, so it
             * demoted the client-side level to 0 forever — every step
             * showed its check while the theme never left level 0. */
            return true;
        }
        /* Input contract must be declared — files listed or the
         * explicit "no input data" flag — mirroring the server's L1
         * rule. Without this the client chip and file colours showed
         * an undeclared step as L1 while the server cell showed it
         * blocked. */
        var bInputDeclared =
            (dictStep.saInputDataFiles || []).length > 0 ||
            dictStep.bNoInputData === true;
        if (!bInputDeclared) return false;
        var dictVerify = fdictGetVerification(dictStep);
        var listModified = dictVerify.listModifiedFiles || [];
        if (listModified.length > 0) return false;
        if (fbAnyUpstreamModified(iStep)) return false;
        if (_dictWorkflowState.dictScriptModified[iStep] === "modified") {
            return false;
        }
        var bHasData =
            PipeleyenTestManager.fsetGetStepsWithData().has(iStep) ||
            !!_dictWorkflowState.dictOutputMtimes[String(iStep)];
        if (!bHasData) return false;
        var sUser = dictVerify.sUser;
        var sDeps = fsComputeDepsState(iStep);
        var bDepsFullyOk = sDeps === "none" || sDeps === "passed";
        if (sUser !== "passed" || !bDepsFullyOk) return false;
        if (fbStepRequiresUnitTests(dictStep)) {
            return fsEffectiveTestState(dictStep) === "passed";
        }
        return true;
    }

    function fbIsFileMissing(elText) {
        return PipeleyenFileOps.fbIsFileMissing(
            elText, _dictWorkflowState.dictFileExistenceCache);
    }

    var fsInitialFileStatusClass =
        PipeleyenFileOps.fsInitialFileStatusClass;

    function fsComputeStepLabel(iIndex) {
        var listSteps = _dictWorkflowState.dictWorkflow.listSteps;
        var step = listSteps[iIndex];
        if (step && typeof step.sLabel === "string" && step.sLabel) {
            return step.sLabel;
        }
        // TODO(2026-07-01): drop this fallback once every response
        // path carries step.sLabel. Kept as a transition shim.
        if (_dictStepLabelByIndex[iIndex] !== undefined) {
            return _dictStepLabelByIndex[iIndex];
        }
        _fnPopulateStepLabelMemo(listSteps);
        return _dictStepLabelByIndex[iIndex];
    }

    function _fnPopulateStepLabelMemo(listSteps) {
        // Walk the list once, counting interactive vs automated steps
        // up to each index. Replaces the original O(N) per-step walk
        // with an O(N) one-time pass; subsequent lookups are O(1).
        var iAuto = 0;
        var iInter = 0;
        for (var i = 0; i < listSteps.length; i++) {
            var bInteractive = listSteps[i].bInteractive === true;
            if (bInteractive) {
                iInter++;
                _dictStepLabelByIndex[i] =
                    "I" + String(iInter).padStart(2, "0");
            } else {
                iAuto++;
                _dictStepLabelByIndex[i] =
                    "A" + String(iAuto).padStart(2, "0");
            }
        }
    }

    function _flistStepWarningReasons(iStepIndex) {
        // Every reason the ⚠ column should report for one step, one
        // plain-English line each. The backend level warning comes
        // first (it is still gated server-side to the lowest
        // non-attained level); the step staleness signals follow.
        // Signals already voiced by the step's dominant L1 blocker
        // hint are skipped so the tooltip never repeats itself.
        var listReasons = [];
        var dictBackend = (_dictWorkflowState.dictStepLevelWarnings ||
            {})[String(iStepIndex)] || null;
        if (dictBackend && dictBackend.iWarningLevel !== null &&
                dictBackend.sWarningHint &&
                dictBackend.sWarningHint.indexOf(
                    "timestamp order") === -1) {
            // The backend's generic timestamp-order hint is dropped:
            // the specific staleness lines below say the same thing
            // with the actual cause named.
            listReasons.push(dictBackend.sWarningHint);
        }
        var dictBlocker =
            _dictWorkflowState.dictBlockersByStep[iStepIndex];
        var sBlockerCriterion = dictBlocker
            ? dictBlocker.sCriterion : "";
        if (dictBlocker) {
            var dictMeta = _fdictBannerGlyphMeta(dictBlocker);
            if (dictMeta) {
                listReasons.push(
                    dictBlocker.sRemediationHint || dictMeta.sLabel);
            }
        }
        var dictStep = ((_dictWorkflowState.dictWorkflow || {})
            .listSteps || [])[iStepIndex] || {};
        var dictVerify = dictStep.dictVerification || {};
        if (_dictWorkflowState.dictScriptModified[iStepIndex] ===
                "modified" && sBlockerCriterion !== "script-stale") {
            listReasons.push("You edited this step's script after " +
                "its outputs were made — re-run the step");
        }
        var listModified = dictVerify.listModifiedFiles || [];
        if (listModified.length > 0) {
            listReasons.push("Output files changed after you " +
                "verified: " + listModified.map(function (sPath) {
                    return sPath.split("/").pop();
                }).join(", ") + " — re-run or re-verify");
        }
        if (fbAnyDepTimingStale(iStepIndex) &&
                sBlockerCriterion !== "upstream-modified") {
            listReasons.push("An earlier step's outputs changed — " +
                "re-run to stay consistent");
        }
        if (dictVerify.bUnseededRandomnessWarning === true) {
            listReasons.push("Unseeded randomness detected — add a " +
                "seed so the run is reproducible");
        }
        return listReasons;
    }

    function _fbStepWarningIsRed(iStepIndex) {
        // Red is reserved for genuine failures: the backend's
        // failed-tests warning, or an L1 blocker whose glyph meta is
        // the red axis warning (failed / outputs-missing).
        var dictBackend = (_dictWorkflowState.dictStepLevelWarnings ||
            {})[String(iStepIndex)] || null;
        if (dictBackend && dictBackend.sWarningSeverity === "red") {
            return true;
        }
        var dictBlocker =
            _dictWorkflowState.dictBlockersByStep[iStepIndex];
        if (!dictBlocker) return false;
        var dictMeta = _fdictBannerGlyphMeta(dictBlocker);
        return Boolean(dictMeta) &&
            dictMeta.sClass === "step-blocker-glyph-axis";
    }

    var _DICT_BLOCKER_CRITERION_GLYPHS = {
        "input-data-undeclared": {
            /* Orange "pending action" family (same glyph as awaiting
             * sign-off): undeclared input is an incomplete
             * declaration a single click resolves, not a breakage —
             * red stays reserved for failed/missing tests. */
            sIcon: "—",
            sLabel: "Input data undeclared — list the step's raw " +
                "inputs or check 'No input data needed'",
            sClass: "step-blocker-glyph-user",
        },
        "upstream-modified": {
            sIcon: "✎",
            sLabel: "Upstream changed; re-run to clear blocker",
            sClass: "step-blocker-glyph-upstream",
        },
        "script-stale": {
            sIcon: "✎",
            sLabel: "Script edited after output — re-run to clear blocker",
            sClass: "step-blocker-glyph-script-stale",
        },
        "axis-not-green": {
            sIcon: "⚠",
            sLabel: "Some tests are not verified — re-run to clear " +
                "the blocker",
            sClass: "step-blocker-glyph-axis",
        },
        "attestation-stale": {
            sIcon: "✎",
            sLabel: "Outputs changed since you verified — " +
                "re-verify or re-run",
            sClass: "step-blocker-glyph-attestation-stale",
        },
        "user-not-approved": {
            sIcon: "—",
            sLabel: "Awaiting your sign-off — review the step's " +
                "results and approve it",
            sClass: "step-blocker-glyph-user",
        },
    };

    // Severity refinement for ``axis-not-green``: the blocker's
    // ``sSubState`` (mirroring levelGates._fsAxisNotGreenSubState)
    // selects the banner glyph. ``untested`` is deliberately null —
    // no banner glyph; the orange status light already carries
    // "work not yet done". Keys must equal the Python sub-state
    // literals (pinned by tests/testStepRendererBlockerGlyphs.py).
    var _DICT_AXIS_SUBSTATE_GLYPHS = {
        "failed": {
            sIcon: "⚠",
            sLabel: "Unit tests failed — fix the step and re-run",
            sClass: "step-blocker-glyph-axis",
        },
        "outputs-missing": {
            sIcon: "⚠",
            sLabel: "Declared outputs missing — run the step",
            sClass: "step-blocker-glyph-axis",
        },
        "outputs-changed": {
            sIcon: "✎",
            sLabel: "Outputs changed since tests last passed — re-run",
            sClass: "step-blocker-glyph-outputs-changed",
        },
        "untested": null,
    };

    var _DICT_L2_BLOCKER_GLYPHS = {
        "ai-declaration-unattested": {
            sIcon: "—",
            sLabel: "AI declaration not yet attested — open the " +
                "step and verify it",
            sClass: "step-blocker-glyph-l2-declaration",
        },
        "not-in-github-mirror": {
            sIcon: "⚠",
            sLabel: "Outputs differ from GitHub mirror — commit " +
                "and push from the Repos panel",
            sClass: "step-blocker-glyph-l2-mirror",
        },
        "not-in-zenodo-deposit": {
            sIcon: "⚠",
            sLabel: "Outputs differ from Zenodo deposit — publish " +
                "a new deposit from the Repos panel",
            sClass: "step-blocker-glyph-l2-zenodo",
        },
        "github-verify-stale": {
            sIcon: "⚠",
            sLabel: "GitHub sync check is stale — re-verify from " +
                "the Repos panel to refresh status",
            sClass: "step-blocker-glyph-l2-github-stale",
        },
        "zenodo-verify-stale": {
            sIcon: "⚠",
            sLabel: "Zenodo sync check is stale — re-verify from " +
                "the Repos panel to refresh status",
            sClass: "step-blocker-glyph-l2-zenodo-stale",
        },
        "missing-ai-declaration-step": {
            sIcon: "—",
            sLabel: "Add an AI declaration step to " +
                "record agent involvement",
            sClass: "step-blocker-glyph-l2-ai-declaration",
        },
        "figure-not-frozen": {
            sIcon: "⚠",
            sLabel: "Plot not pushed to Overleaf at recorded commit — " +
                "push manuscript figures",
            sClass: "step-blocker-glyph-l2-figure",
        },
        "arxiv-mismatch": {
            sIcon: "⚠",
            sLabel: "arXiv submission doesn't match the Overleaf " +
                "push — re-submit to arXiv or update the recorded " +
                "submission under Published copies",
            sClass: "step-blocker-glyph-l2-arxiv-mismatch",
        },
        "arxiv-version-stale": {
            sIcon: "⚠",
            sLabel: "arXiv has a newer version than recorded — " +
                "update the recorded version under Published copies",
            sClass: "step-blocker-glyph-l2-arxiv-version",
        },
    };

    var _DICT_L3_BLOCKER_GLYPHS = {
        "missing-from-manifest": {
            sIcon: "⚠",
            sLabel: "File missing from MANIFEST.sha256 — regenerate " +
                "the envelope from the Artifacts section of the " +
                "Project block",
            sClass: "step-blocker-glyph-l3-manifest",
        },
        "script-not-pinned": {
            sIcon: "⚠",
            sLabel: "Script changed since MANIFEST.sha256 was " +
                "written — re-run the step or regenerate the " +
                "envelope (Artifacts section)",
            sClass: "step-blocker-glyph-l3-pin",
        },
        "nondeterminism-undeclared": {
            sIcon: "⚠",
            sLabel: "Step uses randomness without a recorded seed — " +
                "seed it, or declare it in the Determinism section " +
                "of the Project block",
            sClass: "step-blocker-glyph-l3-determinism",
        },
        "binary-not-declared": {
            sIcon: "⚠",
            sLabel: "Step runs a program vaibify has no record of — " +
                "declare it in the Software section of the Project " +
                "block",
            sClass: "step-blocker-glyph-l3-binary-declared",
        },
        "binary-not-captured": {
            sIcon: "⚠",
            sLabel: "Declared program's version and hash not yet " +
                "recorded — use Capture in the Software section",
            sClass: "step-blocker-glyph-l3-binary-captured",
        },
        "binary-drifted": {
            sIcon: "⚠",
            sLabel: "The program on disk no longer matches the hash " +
                "recorded for reproducibility — it was rebuilt or " +
                "replaced after the outputs were produced. Re-run " +
                "with the current program and re-capture, or restore " +
                "the published binary.",
            sClass: "step-blocker-glyph-l3-binary-drifted",
        },
        "dockerfile-not-pinned": {
            sIcon: "⚠",
            sLabel: "Dockerfile base image not pinned to an exact " +
                "digest — pin the FROM line with @sha256:… or ask " +
                "the in-container agent to pin it",
            sClass: "step-blocker-glyph-l3-workflow-dockerfile",
        },
        "dependency-lock-missing": {
            sIcon: "⚠",
            sLabel: "requirements.lock missing or lacks hashes — " +
                "regenerate the envelope from the Artifacts section",
            sClass: "step-blocker-glyph-l3-workflow-lock",
        },
        "environment-snapshot-missing": {
            sIcon: "⚠",
            sLabel: "Environment snapshot missing or lacks the " +
                "container image digest — regenerate the envelope " +
                "from the Artifacts section",
            sClass: "step-blocker-glyph-l3-workflow-env",
        },
        "reproduce-script-missing": {
            sIcon: "⚠",
            sLabel: "reproduce.sh missing or not pinned in the " +
                "manifest — generate it from the Artifacts section " +
                "of the Project block",
            sClass: "step-blocker-glyph-l3-workflow-reproduce",
        },
        "l3-attestation-stale": {
            sIcon: "⚠",
            sLabel: "Files changed since the last successful " +
                "rebuild — re-run rebuild verification on the AICS " +
                "tab",
            sClass: "step-blocker-glyph-l3-workflow-attestation",
        },
        "binaries-not-declared-or-waived": {
            sIcon: "⚠",
            sLabel: "Programs found in step commands are neither " +
                "declared nor waived — resolve each one in the " +
                "Software section of the Project block",
            sClass: "step-blocker-glyph-l3-workflow-binaries",
        },
    };

    function _fdictBannerGlyphMeta(dictEntry) {
        // ``axis-not-green`` dispatches through the sub-state dict
        // when the backend supplies ``sSubState``; the static entry
        // is the fallback for older payloads that lack the field.
        if (dictEntry.sCriterion === "axis-not-green" &&
            _DICT_AXIS_SUBSTATE_GLYPHS.hasOwnProperty(
                dictEntry.sSubState)) {
            return _DICT_AXIS_SUBSTATE_GLYPHS[dictEntry.sSubState];
        }
        return _DICT_BLOCKER_CRITERION_GLYPHS[dictEntry.sCriterion];
    }

    var S_L1_FAILURE_GLYPH = "⚠";

    function fbFileIsL1Offending(iStepIndex, sRawPath) {
        var dictEntry = _dictWorkflowState.dictBlockersByStep[iStepIndex];
        if (!dictEntry) return false;
        var listOffending = dictEntry.listOffendingFiles || [];
        for (var i = 0; i < listOffending.length; i++) {
            if (listOffending[i] === sRawPath) return true;
        }
        return false;
    }

    function fbUpstreamStepIsL1Offending(iStepIndex, iUpstreamIndex) {
        var dictEntry = _dictWorkflowState.dictBlockersByStep[iStepIndex];
        if (!dictEntry) return false;
        var listOffending = dictEntry.listOffendingUpstreamSteps || [];
        return listOffending.indexOf(iUpstreamIndex) !== -1;
    }

    function fsBuildL1FailureGlyph(sTooltip) {
        return '<span class="l1-blocker-file-glyph" title="' +
            fnEscapeHtml(sTooltip || "Blocking L1 verification") +
            '">' + S_L1_FAILURE_GLYPH + '</span>';
    }

    // Per-file severity marks driven by the blocker's optional
    // ``dictOffendingFileMarks`` field ({sRawPath: "stale" | "failed"
    // | "missing"}). ``stale`` is recoverable by a re-run, so it
    // renders the orange pencil; ``failed`` / ``missing`` render the
    // red warning glyph (no X marks in the vaibify glyph language).
    var _DICT_FILE_MARK_GLYPHS = {
        "stale": {sIcon: "✎", sClass: "file-mark-stale"},
        "failed": {sIcon: "⚠", sClass: "l1-blocker-file-glyph"},
        "missing": {sIcon: "⚠", sClass: "l1-blocker-file-glyph"},
    };

    function _fsFileMarkForPath(iStepIndex, sRawPath) {
        var dictEntry = _dictWorkflowState.dictBlockersByStep[
            iStepIndex];
        if (!dictEntry) return "";
        return (dictEntry.dictOffendingFileMarks || {})[sRawPath] ||
            "";
    }

    function fsBuildFileMarkGlyph(iStepIndex, sRawPath, sTooltip) {
        var dictMeta = _DICT_FILE_MARK_GLYPHS[
            _fsFileMarkForPath(iStepIndex, sRawPath)];
        if (!dictMeta) return fsBuildL1FailureGlyph(sTooltip);
        return '<span class="' + dictMeta.sClass + '" title="' +
            fnEscapeHtml(sTooltip || "Blocking L1 verification") +
            '">' + dictMeta.sIcon + '</span>';
    }

    function _flistBlockerLevels() {
        return [
            _dictWorkflowState.dictBlockersByStep,
            _dictWorkflowState.dictBlockersByStepLevel2,
            _dictWorkflowState.dictBlockersByStepLevel3,
        ];
    }

    function _fiBlockerLevelWalkLimit(iStepIndex) {
        // Per-step gating: a step only surfaces work for its next
        // target rung. A step dirty at L1 shows only its L1 work;
        // L2 requirements appear once L1 is attained for that step.
        return Math.min(
            fiStepNextTargetLevel(iStepIndex),
            _flistBlockerLevels().length);
    }

    function fsBlockerHintForStep(iStepIndex) {
        // Section G: surface the dominant blocker's per-criterion
        // remediation hint for the step. Walks L1 upward, but only
        // to the step's next target rung, so the file-glyph tooltip
        // language matches the banner glyph's.
        var listLevels = _flistBlockerLevels();
        var iLimit = _fiBlockerLevelWalkLimit(iStepIndex);
        for (var i = 0; i < iLimit; i++) {
            var dictEntry = (listLevels[i] || {})[iStepIndex];
            if (dictEntry && dictEntry.sRemediationHint) {
                return dictEntry.sRemediationHint;
            }
        }
        return "";
    }

    function fsBlockerHintForFile(iStepIndex, sRawPath) {
        // Hook up file-list red glyphs to the per-file hint
        // (``dictOffendingFileHints``, when the backend supplies it)
        // of the first blocker whose ``listOffendingFiles`` contains
        // the file, then to that blocker's per-criterion hint.
        // Returns "" when the file is in no ``listOffendingFiles`` so
        // non-offending files never inherit the step-level hint.
        var listLevels = _flistBlockerLevels();
        var iLimit = _fiBlockerLevelWalkLimit(iStepIndex);
        for (var i = 0; i < iLimit; i++) {
            var dictEntry = (listLevels[i] || {})[iStepIndex];
            if (!dictEntry) continue;
            var listOffending = dictEntry.listOffendingFiles || [];
            if (listOffending.indexOf(sRawPath) === -1) continue;
            var dictFileHints = dictEntry.dictOffendingFileHints || {};
            if (dictFileHints[sRawPath]) return dictFileHints[sRawPath];
            if (dictEntry.sRemediationHint) {
                return dictEntry.sRemediationHint;
            }
        }
        return "";
    }

    /* --- Level cells (Scope F) ---
       Each step card and the workflow header row render a regression
       cell plus an always-visible L1|L2|L3 strip. Every cell comes
       from the poll's independent per-level projection
       (``dictStepLevels`` / ``dictWorkflowScopeLevels``), whose CELL
       dicts carry {sState, iSatisfied, iTotal, bRegression}.
       First-attainment dates come from the high-water marks.
       ``iStepIndex`` of -1 selects the workflow scope. */

    var _DICT_LEVEL_CELL_LABELS = {
        1: "L1 Self-Consistent",
        2: "L2 Published",
        3: "L3 Reproducible",
    };

    var _DICT_LEVEL_CELL_STATE_PHRASES = {
        "not-started": "not started — no outputs on disk and no " +
            "activity at this level yet",
        "unassessed": "unassessed — outputs exist on disk, but " +
            "no tests, checks, or sign-off have been recorded yet",
        "none": "no requirements met",
        "partial": "partially met",
        "attained": "attained",
        "unknown": "unknown — GitHub/Zenodo have not been checked " +
            "recently; refresh remote status to find out",
        "not-applicable":
            "not applicable — this step has no requirements at " +
            "this level",
    };

    function _fdictLevelStatesForScope(iStepIndex) {
        if (iStepIndex < 0) {
            return _dictWorkflowState.dictWorkflowScopeLevels || {};
        }
        return (_dictWorkflowState.dictStepLevels || {})[
            String(iStepIndex)] || {};
    }

    function _fdictLevelHighWaterForScope(iStepIndex) {
        if (iStepIndex < 0) {
            return _dictWorkflowState.dictWorkflowLevelHighWater ||
                {};
        }
        return (_dictWorkflowState.dictStepLevelHighWater || {})[
            String(iStepIndex)] || {};
    }

    function fdictLevelCellForScope(iStepIndex, iLevel) {
        var dictCell = _fdictLevelStatesForScope(iStepIndex)[
            "s" + iLevel];
        if (dictCell && typeof dictCell === "object") {
            return dictCell;
        }
        return null;
    }

    function fsLevelCellState(iStepIndex, iLevel) {
        // Rendered verbatim from the backend projection. An absent
        // cell renders the "?" "unknown" mark — never a fake
        // attained or not-started claim.
        var dictCell = fdictLevelCellForScope(iStepIndex, iLevel);
        return (dictCell && dictCell.sState) || "unknown";
    }

    function fsLevelCellTooltip(iStepIndex, iLevel) {
        var dictCell = fdictLevelCellForScope(iStepIndex, iLevel);
        var sState = fsLevelCellState(iStepIndex, iLevel);
        var listParts = [
            _DICT_LEVEL_CELL_LABELS[iLevel] + " — " +
                (_DICT_LEVEL_CELL_STATE_PHRASES[sState] || sState),
        ];
        if (iStepIndex < 0) {
            // The Project row covers project-scope requirements
            // only; it is NOT a roll-up of the step rows. The
            // all-steps aggregate renders as the header checkmarks
            // and the AICS tab.
            listParts.push(
                "These requirements apply to the project as a " +
                "whole, not to any single step. Each step row " +
                "tracks its own. The overall level is shown by " +
                "the checkmarks next to the project name and in " +
                "the AICS tab.");
        }
        if (dictCell && sState !== "not-applicable") {
            listParts.push(dictCell.iSatisfied + " of " +
                dictCell.iTotal + " requirements met");
        }
        return _flistAppendLevelTooltipContext(
            listParts, iStepIndex, iLevel, sState).join("\n");
    }

    function _flistAppendLevelTooltipContext(
        listParts, iStepIndex, iLevel, sState
    ) {
        if (sState === "not-applicable") {
            // Nothing to attain, regress from, or remediate; a stray
            // high-water stamp from the vacuous-attainment era must
            // not resurface here.
            return listParts;
        }
        var sFirstAttained = _fdictLevelHighWaterForScope(
            iStepIndex)[String(iLevel)] || "";
        if (sFirstAttained) {
            listParts.push("First attained " + sFirstAttained);
        }
        var sHint = _fsLevelBlockerHint(iStepIndex, iLevel);
        if (sHint && sState !== "attained") {
            listParts.push(sHint);
        }
        return listParts;
    }

    function _fsLevelBlockerHint(iStepIndex, iLevel) {
        // Dominant blocker hint for one rung of one scope. Workflow
        // scope (-1) hits the workflow-scope L2 entries that
        // _fdictBlockersByStepIndex keys under -1; L1 and L3 have no
        // workflow-scope entries in these dicts, so the hint is "".
        var dictEntry = (_flistBlockerLevels()[iLevel - 1] || {})[
            iStepIndex];
        return (dictEntry && dictEntry.sRemediationHint) || "";
    }

    function fiStepNextTargetLevel(iStepIndex) {
        // First rung whose cell is not attained — the rung the step
        // is currently working toward. A not-applicable rung has no
        // work to offer, so it never becomes the target. 4 when
        // every rung is attained or not applicable.
        for (var iLevel = 1; iLevel <= 3; iLevel++) {
            var sState = fsLevelCellState(iStepIndex, iLevel);
            if (sState !== "attained" && sState !== "not-applicable") {
                return iLevel;
            }
        }
        return 4;
    }

    function fdictRegressionWarning(iStepIndex) {
        // The single ⚠-column entry for one row — every warning a
        // step carries, consolidated. For steps it composes the
        // backend level warning (still gated server-side to the
        // lowest non-attained level) with each staleness signal, one
        // plain-English line per reason; red is reserved for genuine
        // failures. The workflow row keeps its backend entry, with
        // the cell-level regression flag as fallback when the poll
        // carries no "-1" entry.
        if (iStepIndex < 0) {
            var dictScope = (_dictWorkflowState.dictStepLevelWarnings
                || {})[String(iStepIndex)] ||
                _fdictDeriveWorkflowScopeWarning();
            if (!dictScope || !dictScope.sWarningSeverity) return null;
            return dictScope;
        }
        var listReasons = _flistStepWarningReasons(iStepIndex);
        if (listReasons.length === 0) return null;
        return {
            sWarningSeverity: _fbStepWarningIsRed(iStepIndex)
                ? "red" : "orange",
            sWarningHint: listReasons.join("\n"),
        };
    }

    function _fdictDeriveWorkflowScopeWarning() {
        // Mirrors the backend rule at workflow scope: warn only when
        // the lowest non-attained level was attained before
        // (bRegression). Severity is orange — red is reserved for
        // failed L1 test axes, which do not exist at workflow scope.
        var iLevel = fiStepNextTargetLevel(-1);
        if (iLevel > 3) return null;
        var dictCell = fdictLevelCellForScope(-1, iLevel);
        if (!dictCell || dictCell.bRegression !== true) return null;
        return {
            iWarningLevel: iLevel,
            sWarningSeverity: "orange",
            sWarningHint: _DICT_LEVEL_CELL_LABELS[iLevel] +
                " was attained before and has regressed",
        };
    }

    function _fnToggleInSet(setTarget, sKey) {
        // Mutate in place — the render context holds these Sets by
        // reference (the shared-Sets rule); never reassign.
        if (setTarget.has(sKey)) {
            setTarget.delete(sKey);
        } else {
            setTarget.add(sKey);
        }
        fnRenderStepList();
    }

    function fnToggleStepsBlockExpand() {
        _dictUiState.bStepsCollapsed = !_dictUiState.bStepsCollapsed;
        _fnPersistStepsCollapsed(_dictUiState.bStepsCollapsed);
        fnRenderStepList();
    }

    function fnToggleProjectBlockExpand() {
        _dictUiState.bProjectBlockCollapsed =
            !_dictUiState.bProjectBlockCollapsed;
        fnRenderStepList();
    }

    function fnToggleBinaryAddForm() {
        _dictUiState.bBinaryAddFormOpen =
            !_dictUiState.bBinaryAddFormOpen;
        fnRenderStepList();
    }

    function fnToggleRequirementGroup(sGroupKey) {
        _fnToggleInSet(
            _dictUiState.setExpandedRequirementGroups, sGroupKey);
    }

    function fnToggleRequirementRow(sReqKey) {
        _fnToggleInSet(
            _dictUiState.setExpandedRequirementRows, sReqKey);
    }

    var _DICT_PROJECT_ACTIONS = {
        "capture-binary": {
            sPath: "/binaries/capture",
            fdictBody: function (sArg) {
                return {sBinaryPath: sArg};
            },
            fdictAfterResponse: function (dictResult, sArg) {
                var dictCaptured =
                    (dictResult || {}).dictCaptured || {};
                if (dictCaptured.sSha256) {
                    return {sMessage: "Captured " + sArg +
                        " (version " +
                        (dictCaptured.sVersion || "unknown") + ").",
                        sType: "info"};
                }
                return {sMessage: "No file found at " + sArg +
                    ". Fix the declared path with the Add / update " +
                    "package form at the bottom of the Software " +
                    "section.", sType: "error"};
            },
        },
        "declare-binary": {
            sPath: "/binaries/declare",
            fdictBodyFromElement: _fdictReadBinaryForm,
            sToast: "Package declared. Now capture its version and " +
                "hash from its row.",
        },
        "remove-binary": {
            sPath: "/binaries/declare",
            fdictBody: _fdictReadBinaryRemoval,
            dictConfirm: {
                sTitle: "Remove package",
                sMessage: "Remove this package from the declared " +
                    "software list? Its captured version and hash " +
                    "stay in the environment snapshot until the " +
                    "next regeneration.",
            },
            sToast: "Package removed from the declaration.",
        },
        "verify-dependency-lock": {
            sPath: "/dependencies/verify",
            fdictAfterResponse: function (dictResult) {
                var listProblems =
                    (dictResult || {}).listProblems || [];
                if (listProblems.length === 0) {
                    return {sMessage: "requirements.lock is clean: " +
                        "every dependency pinned by exact version " +
                        "with hashes.", sType: "info"};
                }
                return {sMessage: listProblems.length +
                    " problem(s) in requirements.lock (first: " +
                    listProblems[0] + "). Regenerate the envelope " +
                    "to rebuild it.", sType: "warning"};
            },
        },
        "regenerate-envelope": {
            sPath: "/level3/envelope",
            bOfferCommitAfterGenerate: true,
            fdictAfterResponse: function (dictResult) {
                var dictGaps =
                    (dictResult || {}).dictL3ReadinessGaps || {};
                var listStillFailing = [
                    ["bManifestComplete", "manifest"],
                    ["bDependencyLockHashed", "dependency lock"],
                    ["bEnvironmentDigestPinned", "environment"],
                ].filter(function (t) {
                    return dictGaps[t[0]] === false;
                }).map(function (t) { return t[1]; });
                if (listStillFailing.length === 0) {
                    return {sMessage: "Envelope regenerated — " +
                        "manifest, dependency lock, and environment " +
                        "snapshot are all current.", sType: "info"};
                }
                return {sMessage: "Envelope regenerated, but still " +
                    "failing: " + listStillFailing.join(", ") +
                    ". Check the hub log for the tier error.",
                    sType: "warning"};
            },
        },
        "verify-manifest": {
            sPath: "/manifest/verify",
            fdictAfterResponse: function (dictResult) {
                var iTotal = (dictResult || {}).iTotal || 0;
                var listBad = (dictResult || {}).listMismatches || [];
                if (listBad.length === 0) {
                    return {sMessage: "All " + iTotal + " manifest " +
                        "files match their pinned hashes.",
                        sType: "info"};
                }
                return {sMessage: listBad.length + " of " + iTotal +
                    " files differ from the manifest (first: " +
                    (listBad[0].sPath || listBad[0]) + "). Re-run " +
                    "the project or regenerate the envelope.",
                    sType: "warning"};
            },
        },
        "generate-reproduce-script": {
            sPath: "/level3/reproduce-script",
            bOfferCommitAfterGenerate: true,
            fdictAfterResponse: function (dictResult) {
                if ((dictResult || {}).bManifestRefreshed === true) {
                    return {sMessage: "reproduce.sh written and " +
                        "pinned in the manifest — the check will " +
                        "pass on the next status poll.",
                        sType: "info"};
                }
                return {sMessage: "reproduce.sh was written, but " +
                    "re-pinning the manifest failed — click " +
                    "'Regenerate now' on the Manifest row, then " +
                    "check the hub log.", sType: "warning"};
            },
        },
        "verify-l3": {
            sPath: "/level3/verify",
            sToast: "Level 3 verification started. The rebuild runs " +
                "in the container; the result appears in the " +
                "Attestation row when it completes.",
        },
        "declare-determinism": {
            sPath: "/determinism/declare",
            fdictBodyFromElement: _fdictReadDeterminismForm,
            sToast: "Reproducibility rules declared.",
        },
        "delete-determinism": {
            sPath: "/determinism",
            sMethod: "DELETE",
            dictConfirm: {
                sTitle: "Delete reproducibility rules",
                sMessage: "Remove the declared repeatability rules " +
                    "from project.json? Level 3 requires a " +
                    "declaration, so you will need to declare again.",
            },
            sToast: "Reproducibility rules deleted.",
        },
    };

    function _fdictReadBinaryForm(elButton) {
        // Merge the form entry into the existing declarations —
        // an entry with the same path replaces the old one, so the
        // form both adds missed packages and fixes stale paths.
        var elForm = elButton.closest(".binary-add-form");
        if (!elForm) return null;
        var sPath = (elForm.querySelector(".binary-form-path")
            .value || "").trim();
        var sPurpose = (elForm.querySelector(".binary-form-purpose")
            .value || "").trim();
        var sVersion = (elForm.querySelector(".binary-form-version")
            .value || "").trim();
        if (!sPath || !sPurpose || !sVersion) {
            fnShowToast(
                "All three fields are needed: path, purpose, and " +
                "expected version.", "warning");
            return null;
        }
        var listExisting = ((_dictWorkflowState.dictWorkflow || {})
            .listDeclaredBinaries || []).filter(function (d) {
                return d && d.sBinaryPath !== sPath;
            });
        listExisting.push({
            sBinaryPath: sPath,
            sPurpose: sPurpose,
            sExpectedVersion: sVersion,
        });
        return {
            bNoStandaloneBinaries: false,
            listDeclaredBinaries: listExisting,
        };
    }

    function _fdictReadDeterminismForm(elButton) {
        // Gather the declare-determinism form inputs from the same
        // requirement-row detail the button lives in. A blank thread
        // box sends an explicit null so a previously pinned count is
        // REMOVED — the endpoint merges keys, so omitting it would
        // silently keep the old pin forever.
        var elDetail = elButton.closest(".requirement-row-detail");
        if (!elDetail) return null;
        var elBlas = elDetail.querySelector(".determinism-accept-blas");
        var elThreads = elDetail.querySelector(
            ".determinism-omp-threads");
        var dictBody = {
            bAcceptBlasVariance: Boolean(elBlas && elBlas.checked),
            dOmpNumThreads: null,
        };
        if (elThreads && elThreads.value !== "") {
            var iThreads = parseInt(elThreads.value, 10);
            if (!isNaN(iThreads) && iThreads > 0) {
                dictBody.dOmpNumThreads = iThreads;
            }
        }
        return dictBody;
    }

    function _fdictReadBinaryRemoval(sBinaryPath) {
        // Re-declare the list minus the removed entry.
        var listRemaining = ((_dictWorkflowState.dictWorkflow || {})
            .listDeclaredBinaries || []).filter(function (d) {
                return d && d.sBinaryPath !== sBinaryPath;
            });
        return {
            bNoStandaloneBinaries: listRemaining.length === 0,
            listDeclaredBinaries: listRemaining,
        };
    }

    async function fnRunProjectAction(sAction, sArg, elButton) {
        // Runs a project action in place from the expanded
        // blocks (capture/declare binaries, regenerate the envelope,
        // verify the manifest, generate reproduce.sh, declare/delete
        // determinism, verify Level 3), then refreshes so the status
        // lights update. Destructive actions confirm first; actions
        // with a response-aware formatter report what actually
        // happened rather than a fixed message.
        var dictAction = _DICT_PROJECT_ACTIONS[sAction];
        var sContainerId = _dictSessionState.sContainerId;
        if (!dictAction || !sContainerId) return;
        if (dictAction.dictConfirm) {
            var dictNoConfirm = Object.assign({}, dictAction);
            delete dictNoConfirm.dictConfirm;
            fnShowConfirmModal(
                dictAction.dictConfirm.sTitle,
                dictAction.dictConfirm.sMessage,
                function () {
                    _fnExecuteProjectAction(
                        dictNoConfirm, sContainerId, sArg, elButton);
                });
            return;
        }
        await _fnExecuteProjectAction(
            dictAction, sContainerId, sArg, elButton);
    }

    async function _fnExecuteProjectAction(
        dictAction, sContainerId, sArg, elButton
    ) {
        var oBody = {};
        if (dictAction.fdictBodyFromElement) {
            oBody = dictAction.fdictBodyFromElement(elButton);
            if (!oBody) return;
        } else if (dictAction.fdictBody) {
            oBody = dictAction.fdictBody(sArg);
        }
        var sUrl = "/api/workflow/" + sContainerId + dictAction.sPath;
        try {
            var dictResult;
            if (dictAction.sMethod === "DELETE") {
                dictResult = await VaibifyApi.fnDelete(sUrl);
            } else {
                dictResult = await VaibifyApi.fdictPost(sUrl, oBody);
            }
            if (dictAction.fdictAfterResponse) {
                var dictOutcome = dictAction.fdictAfterResponse(
                    dictResult, sArg);
                fnShowToast(dictOutcome.sMessage, dictOutcome.sType);
            } else {
                fnShowToast(dictAction.sToast, "info");
            }
            if (dictAction.bOfferCommitAfterGenerate) {
                // The generated envelope/reproduce.sh files are not
                // auto-committed; offer to commit them now rather than
                // leaving them silently untracked (blocking L2).
                await VaibifyManifestCheck.fbOfferCommitAfterGenerate(
                    sContainerId);
            }
        } catch (error) {
            fnShowToast(
                "Action failed: " +
                ((error && error.message) ? error.message : error),
                "error");
        }
        // Fire an immediate file-status poll so the block's status
        // lights reflect the action right away instead of waiting for
        // the next scheduled tick. (fnRefreshWorkflowData requires a
        // connect payload — calling it bare threw and silently
        // skipped this refresh.)
        VaibifyPolling.fnStartFilePolling(sContainerId);
    }

    function fnSetCachedAicsLevel(iLevel) {
        _dictWorkflowState.iCachedAicsLevel =
            typeof iLevel === "number" ? iLevel : null;
    }

    function fdictBlockerCountsByLevel() {
        // Section G legend panel: live counts of active blockers per
        // ladder rung. The panel renders one count per level so the
        // researcher can sanity-check the header progression.
        return {
            iLevel1: _dictWorkflowState.iL1BlockerCount || 0,
            iLevel2: _dictWorkflowState.iL2BlockerCount || 0,
            iLevel3: _dictWorkflowState.iL3BlockerCount || 0,
        };
    }

    function fdictBlockerGlyphCatalog() {
        // Section G legend panel: the authoritative glyph dicts per
        // ladder rung. The legend generates its criterion rows from
        // these so the panel cannot drift from the rendered glyphs.
        return {
            iLevel1: _DICT_BLOCKER_CRITERION_GLYPHS,
            iLevel2: _DICT_L2_BLOCKER_GLYPHS,
            iLevel3: _DICT_L3_BLOCKER_GLYPHS,
            dictAxisSubStates: _DICT_AXIS_SUBSTATE_GLYPHS,
        };
    }

    function fbAnyDepTimingStale(iStep) {
        var listDeps = flistGetStepDependencies(iStep);
        for (var i = 0; i < listDeps.length; i++) {
            var iDep = listDeps[i];
            if (iDep === iStep) continue;
            var tStates = ftComputeDepAxisStates(iStep, iDep);
            if (tStates.sTiming === "failed") return true;
        }
        return false;
    }

    function fdictGetVerification(step) {
        return step.dictVerification || {
            sUnitTest: "untested", sUser: "untested",
        };
    }

    function fdictGetTests(step) {
        if (step.dictTests) return step.dictTests;
        var listOldCommands = step.saTestCommands || [];
        return {
            dictQualitative: {saCommands: [], sFilePath: ""},
            dictQuantitative: {
                saCommands: [], sFilePath: "", sStandardsPath: "",
            },
            dictIntegrity: {
                saCommands: listOldCommands.slice(), sFilePath: "",
            },
            listUserTests: [],
        };
    }

    function fsGetCategoryState(step, sCategory) {
        var dictVerify = fdictGetVerification(step);
        var sKey = "s" + sCategory.charAt(0).toUpperCase() +
            sCategory.slice(1);
        return dictVerify[sKey] || "untested";
    }

    var fsTestCategoryLabel = VaibifyUtilities.fsTestCategoryLabel;

    function fsVerificationStateLabel(sState) {
        var dictLabels = {
            passed: "Passed", failed: "Failed",
            untested: "Untested", error: "Error",
            stale: "Stale",
            "passed-from-marker": "Passed",
            "outputs-changed": "Stale",
            "outputs-missing": "Missing",
        };
        return dictLabels[sState] || "Untested";
    }

    function fsVerificationStateIcon(sState) {
        var dictIcons = {
            passed: "\u2713", failed: "\u2717",
            untested: "\u2014", error: "\u2717",
            stale: "\u26A0",
            "passed-from-marker": "\u2713",
            "outputs-changed": "\u26A0",
            "outputs-missing": "\u2717",
        };
        return dictIcons[sState] || "\u2014";
    }

    function fsetGetExpandedCategory(sCategory) {
        var dictSets = {
            qualitative: _dictUiState.setExpandedQualitative,
            quantitative: _dictUiState.setExpandedQuantitative,
            integrity: _dictUiState.setExpandedIntegrity,
        };
        return dictSets[sCategory] || new Set();
    }

    function fsEffectiveTestState(step) {
        var dictVerify = fdictGetVerification(step);
        var dictTests = fdictGetTests(step);
        var listCategories = [
            "qualitative", "quantitative", "integrity"];
        var bAnyCommands = false;
        var bAllPassed = true;
        var bAnyFailed = false;
        for (var i = 0; i < listCategories.length; i++) {
            var sCategory = listCategories[i];
            var sCatKey = "dict" +
                sCategory.charAt(0).toUpperCase() +
                sCategory.slice(1);
            var dictCat = dictTests[sCatKey] || {};
            if ((dictCat.saCommands || []).length === 0) continue;
            bAnyCommands = true;
            var sKey = "s" +
                sCategory.charAt(0).toUpperCase() +
                sCategory.slice(1);
            var sState = dictVerify[sKey] || "untested";
            if (sState === "failed" || sState === "error") {
                bAnyFailed = true;
            }
            if (sState !== "passed") bAllPassed = false;
        }
        if (!bAnyCommands) return "untested";
        if (bAnyFailed) return "failed";
        if (bAllPassed) return "passed";
        return "untested";
    }

    function fbDirectoryOverlap(dictEarlierStep, sCurrentDirectory) {
        if (!dictEarlierStep.sDirectory) return false;
        var sPrefix = sCurrentDirectory + "/";
        var listFileKeys = ["saOutputDataFiles", "saPlotFiles"];
        for (var iKey = 0; iKey < listFileKeys.length; iKey++) {
            var listFiles = dictEarlierStep[listFileKeys[iKey]] || [];
            for (var iFile = 0; iFile < listFiles.length; iFile++) {
                if (listFiles[iFile].indexOf("{") !== -1) continue;
                var sJoined = dictEarlierStep.sDirectory + "/" + listFiles[iFile];
                if (sJoined.indexOf(sPrefix) === 0) return true;
            }
        }
        return false;
    }

    function flistGetStepDependencies(iStep) {
        if (!_dictWorkflowState.dictWorkflow || !_dictWorkflowState.dictWorkflow.listSteps) return [];
        if (_dictStepDepsByIndex[iStep] !== undefined) {
            return _dictStepDepsByIndex[iStep];
        }
        var listDeps = _flistComputeStepDependencies(iStep);
        _dictStepDepsByIndex[iStep] = listDeps;
        _fnIndexStepFilesIntoReverseMap(iStep);
        return listDeps;
    }

    function _flistComputeStepDependencies(iStep) {
        var listSteps = _dictWorkflowState.dictWorkflow.listSteps;
        var step = listSteps[iStep];
        var setDeps = {};
        var listArrays = ["saDataCommands", "saPlotCommands",
            "saTestCommands", "saOutputDataFiles", "saPlotFiles",
            "saDependencies", "saSetupCommands", "saCommands"];
        listArrays.forEach(function (sKey) {
            (step[sKey] || []).forEach(function (sVal) {
                var rRef = /\{Step(\d+)\.\w+\}/g;
                var match;
                while ((match = rRef.exec(sVal)) !== null) {
                    var iDep = parseInt(match[1]) - 1;
                    if (iDep !== iStep) setDeps[iDep] = true;
                }
            });
        });
        if (step.sDirectory) {
            for (var j = 0; j < iStep; j++) {
                if (fbDirectoryOverlap(listSteps[j], step.sDirectory))
                    setDeps[j] = true;
            }
        }
        (step.saSourceCodeDeps || []).forEach(function (iDepNumber) {
            var iDep = iDepNumber - 1;
            if (iDep >= 0 && iDep !== iStep) setDeps[iDep] = true;
        });
        return Object.keys(setDeps).map(Number).sort(
            function (a, b) { return a - b; }
        );
    }

    function _fnIndexStepFilesIntoReverseMap(iStep) {
        // Populate _dictStepIndexByFilePath so the badge-driven
        // partial render can map "this file's badge changed" to
        // "this step's card needs re-rendering" in O(1). Must cover
        // every file family that fsRenderStepItem reads badge state
        // for: data, plot, step scripts, test standards, and the AI
        // declaration file (its commit/remove buttons gate on the
        // git badge).
        var step = _dictWorkflowState.dictWorkflow.listSteps[iStep];
        if (!step) return;
        var listFileKeys = ["saOutputDataFiles", "saPlotFiles",
            "saStepScripts", "saTestStandards"];
        listFileKeys.forEach(function (sKey) {
            (step[sKey] || []).forEach(function (sFile) {
                if (sFile) _dictStepIndexByFilePath[sFile] = iStep;
            });
        });
        var sDeclarationFile = (step.sDeclarationFile || "").trim();
        if (sDeclarationFile) {
            _dictStepIndexByFilePath[sDeclarationFile] = iStep;
        }
    }

    function fbStepFullyPassing(iStep, dictVisited) {
        if (!_dictWorkflowState.dictWorkflow || !_dictWorkflowState.dictWorkflow.listSteps[iStep]) {
            return false;
        }
        if (dictVisited[iStep]) return dictVisited[iStep] === "pass";
        dictVisited[iStep] = "checking";
        var step = _dictWorkflowState.dictWorkflow.listSteps[iStep];
        var dictVerify = fdictGetVerification(step);
        var bInteractive = step.bInteractive === true;
        var bPlotOnly = (step.saDataCommands || []).length === 0;
        if (bInteractive) {
            if (dictVerify.sUser !== "passed") {
                dictVisited[iStep] = "fail";
                return false;
            }
        } else if (bPlotOnly) {
            if (dictVerify.sUser !== "passed") {
                dictVisited[iStep] = "fail";
                return false;
            }
        } else {
            var sTestState = fsEffectiveTestState(step);
            if (sTestState !== "passed" ||
                dictVerify.sUser !== "passed") {
                dictVisited[iStep] = "fail";
                return false;
            }
        }
        var listDeps = flistGetStepDependencies(iStep);
        for (var i = 0; i < listDeps.length; i++) {
            if (!fbStepFullyPassing(listDeps[i], dictVisited)) {
                dictVisited[iStep] = "fail";
                return false;
            }
        }
        dictVisited[iStep] = "pass";
        return true;
    }

    function fbAnyUpstreamModified(iStep) {
        var listDeps = flistGetStepDependencies(iStep);
        for (var i = 0; i < listDeps.length; i++) {
            var dictV = fdictGetVerification(
                _dictWorkflowState.dictWorkflow.listSteps[listDeps[i]]);
            var listMod = dictV.listModifiedFiles || [];
            if (listMod.length > 0) return true;
        }
        return false;
    }

    function fsComputeDepsState(iStep) {
        var listDeps = flistGetStepDependencies(iStep);
        if (listDeps.length === 0) return "none";
        var bAnyFailed = false;
        var bAnyUnknown = false;
        for (var i = 0; i < listDeps.length; i++) {
            var iDep = listDeps[i];
            if (iDep === iStep) continue;
            var tStates = ftComputeDepAxisStates(iStep, iDep);
            if (tStates.sStepStatus === "failed" ||
                    tStates.sTiming === "failed") {
                bAnyFailed = true;
            } else if (tStates.sTiming === "unknown") {
                bAnyUnknown = true;
            }
        }
        if (bAnyFailed) return "failed";
        if (bAnyUnknown) return "untested";
        return "passed";
    }

    function ftComputeDepAxisStates(iStep, iDep) {
        var dictVisited = {};
        var bStepStatus = fbStepFullyPassing(iDep, dictVisited);
        var dictMax = _dictWorkflowState.dictOutputMtimes || {};
        var iDepMtime = parseInt(dictMax[String(iDep)] || "0", 10);
        var iMyOutputMtime = parseInt(
            dictMax[String(iStep)] || "0", 10);
        var sTiming = _fsComputeDepLineageTiming(
            iStep, iDep, iDepMtime, iMyOutputMtime,
        );
        var dictTestSrc =
            _dictWorkflowState.dictTestSourceMtimeByStep || {};
        var sTestSrc = dictTestSrc[String(iDep)];
        var iDepTestSrcMtime = sTestSrc !== undefined
            ? parseInt(sTestSrc, 10) : null;
        return {
            sStepStatus: bStepStatus ? "passed" : "failed",
            sTiming: sTiming,
            iDepMtime: iDepMtime,
            iMyOutputMtime: iMyOutputMtime,
            iDepTestSrcMtime: iDepTestSrcMtime,
        };
    }

    function _fsComputeDepLineageTiming(
        iStep, iDep, iDepMtime, iMyOutputMtime,
    ) {
        /* The unit-test source mtime is the contract: when the
           upstream's correctness criteria were last written. If the
           contract was in force at the moment downstream was built
           (and is still currently met, captured by sStepStatus
           passing), the lineage is intact — even if the upstream
           data was rerun and produced bit-identical bytes with a
           fresher mtime. Falls back to upstream-output-mtime
           comparison for steps without a test contract (interactive
           and plot-only steps). Key-presence (not value > 0) marks
           "contract exists" so a legitimate mtime=0 (e.g. files
           placed by a sync that did not preserve mtimes) still
           satisfies the gate. */
        if (!iMyOutputMtime) return "unknown";
        var dictTestSrc =
            _dictWorkflowState.dictTestSourceMtimeByStep || {};
        var sTestSrc = dictTestSrc[String(iDep)];
        if (sTestSrc !== undefined) {
            var iDepTestSrcMtime = parseInt(sTestSrc, 10);
            return iDepTestSrcMtime <= iMyOutputMtime
                ? "passed" : "failed";
        }
        if (!iDepMtime) return "unknown";
        return iDepMtime <= iMyOutputMtime ? "passed" : "failed";
    }

    function fnSetVerificationUserName(sName) {
        _dictSessionState.sUserName = sName || "User";
    }

    function fiParseUtcTimestamp(sTimestamp) {
        if (!sTimestamp) return 0;
        var sClean = sTimestamp.replace(" UTC", "").trim();
        var dtParsed = new Date(sClean + "Z");
        if (isNaN(dtParsed.getTime())) return 0;
        return Math.floor(dtParsed.getTime() / 1000);
    }

    var fsFormatUtcTimestamp = VaibifyUtilities.fsFormatUtcTimestamp;

    function fsGetFileCategory(iStep, sFilePath, sArrayKey) {
        var dictStep = _dictWorkflowState.dictWorkflow.listSteps[iStep];
        if (sArrayKey === "saPlotFiles") {
            var dictPlot = dictStep.dictPlotFileCategories || {};
            return dictPlot[sFilePath] || "archive";
        }
        var dictData = dictStep.dictOutputDataFileCategories || {};
        return dictData[sFilePath] || "archive";
    }

    function fnBindStepEvents() {
        if (_dictWorkflowState.bDelegatedEventsInitialized) return;
        _dictWorkflowState.bDelegatedEventsInitialized = true;
        var elList = document.getElementById("listSteps");
        PipeleyenEventBindings.fnSetupDelegatedEvents(elList);
    }

    async function fnPutStepEdit(iStep, dictUpdate) {
        // Single choke-point for every step edit. Attaches the
        // compare-and-swap fingerprint so a concurrent writer (the
        // in-container agent) is never silently overwritten, and keeps
        // the tracked fingerprint fresh for the next edit. On a 409 the
        // local optimistic edit is stale, so we re-sync from the server
        // rather than trust it. Returns the response dict on success,
        // null on any failure (the caller shows nothing extra).
        var dictBody = Object.assign({}, dictUpdate, {
            sBaseFingerprint:
                _dictWorkflowState.sWorkflowFingerprint || null,
        });
        try {
            var dictResult = await VaibifyApi.fdictPut(
                "/api/steps/" + _dictSessionState.sContainerId + "/" + iStep,
                dictBody);
            if (dictResult && dictResult.sWorkflowFingerprint) {
                _dictWorkflowState.sWorkflowFingerprint =
                    dictResult.sWorkflowFingerprint;
            }
            return dictResult;
        } catch (error) {
            if (error && error.iStatus === 409) {
                fnShowToast(
                    "The project changed since you loaded it — "
                    + "reloaded to stay in sync so your edit didn't "
                    + "overwrite it. Re-apply it if you still want it.",
                    "warning");
                // Reload the CURRENT workflow in place (refreshes the
                // fingerprint baseline and re-renders the dashboard).
                // The old path called fnConnectToContainer, which shows
                // the workflow picker — a 409 on a routine step edit
                // must never eject the researcher from their dashboard.
                VaibifyWorkflowManager.fnRefreshWorkflow();
            } else {
                fnShowToast("Save failed", "error");
            }
            return null;
        }
    }

    async function fnSetStepBudget(iStep, fBudget) {
        // The wall-clock budget in seconds (0 = inherit the workflow
        // default). Mirror it into local state before the PUT so the
        // input stays sticky across re-renders, exactly as the
        // plot-only toggle does.
        _dictWorkflowState.dictWorkflow.listSteps[iStep]
            .fWallClockBudgetSeconds = fBudget;
        await fnPutStepEdit(iStep, {fWallClockBudgetSeconds: fBudget});
    }

    async function fnTogglePlotOnly(iStep, bPlotOnly) {
        _dictWorkflowState.dictWorkflow.listSteps[iStep].bPlotOnly = bPlotOnly;
        await fnPutStepEdit(iStep, {bPlotOnly: bPlotOnly});
    }

    async function fnToggleNoInputData(iStep, bNoInputData) {
        _dictWorkflowState.dictWorkflow.listSteps[iStep].bNoInputData =
            bNoInputData;
        await fnPutStepEdit(iStep, {bNoInputData: bNoInputData});
        fnRenderStepList();
    }

    async function fnBulkDeclareNoInputData() {
        var sContainerId = _dictSessionState.sContainerId;
        if (!sContainerId) return;
        try {
            var dictResult = await VaibifyApi.fdictPost(
                "/api/steps/" + sContainerId
                + "/declare-no-input-data", {});
            var listDeclared =
                dictResult.listDeclaredStepIndices || [];
            listDeclared.forEach(function (iStep) {
                var step =
                    _dictWorkflowState.dictWorkflow.listSteps[iStep];
                if (step) step.bNoInputData = true;
            });
            fnShowToast(
                listDeclared.length === 0
                    ? "Every step already declares its input data"
                    : listDeclared.length
                        + " step(s) declared as needing no input data",
                "success");
            fnRenderStepList();
            VaibifyPolling.fnStartFilePolling(sContainerId);
        } catch (error) {
            fnShowToast("Declaration failed", "error");
        }
    }

    function fnToggleDepsExpand(iStep) {
        if (_dictUiState.setExpandedDeps.has(iStep)) {
            _dictUiState.setExpandedDeps.delete(iStep);
        } else {
            _dictUiState.setExpandedDeps.add(iStep);
        }
        fnRenderStepList();
    }

    function fnToggleUnitTestExpand(iStep) {
        var setExpanded = PipeleyenTestManager.fsetGetExpandedUnitTests();
        if (setExpanded.has(iStep)) {
            setExpanded.delete(iStep);
        } else {
            setExpanded.add(iStep);
        }
        fnRenderStepList();
    }

    function fnHandleDiscoveredOutputs(dictEvent) {
        var iStep = dictEvent.iStepNumber - 1;
        var iTotal = (typeof dictEvent.iTotalDiscovered === "number") ?
            dictEvent.iTotalDiscovered : dictEvent.listDiscovered.length;
        _dictWorkflowState.dictDiscoveredOutputs[iStep] = {
            listDiscovered: dictEvent.listDiscovered,
            iTotalDiscovered: iTotal,
        };
        fnRenderStepList();
        fnShowToast(
            "Step " + dictEvent.iStepNumber +
            ": " + iTotal +
            " new output(s) discovered", "success"
        );
    }

    async function fnAddDiscoveredOutput(
        iStep, sFile, sTargetArray
    ) {
        var dictStep = _dictWorkflowState.dictWorkflow.listSteps[iStep];
        if (!dictStep[sTargetArray]) dictStep[sTargetArray] = [];
        dictStep[sTargetArray].push(sFile);
        var dictUpdate = {};
        dictUpdate[sTargetArray] = dictStep[sTargetArray];
        await fnSaveStepUpdate(iStep, dictUpdate);
        var dictDisc = _dictWorkflowState.dictDiscoveredOutputs[iStep] ||
            {listDiscovered: [], iTotalDiscovered: 0};
        var listFiltered = (dictDisc.listDiscovered || []).filter(
            function (d) { return d.sFilePath !== sFile; }
        );
        _dictWorkflowState.dictDiscoveredOutputs[iStep] = {
            listDiscovered: listFiltered,
            iTotalDiscovered: dictDisc.iTotalDiscovered || 0,
        };
        fnRenderStepList();
    }

    var fnShowConfirmModal = PipeleyenModals.fnShowConfirmModal;
    var fnShowInputModal = PipeleyenModals.fnShowInputModal;

    async function fnSaveStepUpdate(iStep, dictUpdate) {
        await fnPutStepEdit(iStep, dictUpdate);
    }

    async function fnCycleUserVerification(iStep) {
        var dictStep = _dictWorkflowState.dictWorkflow.listSteps[iStep];
        var dictVerify = fdictGetVerification(dictStep);
        var listStates = [
            "untested", "passed", "failed", "error"
        ];
        var iCurrent = listStates.indexOf(
            dictVerify.sUser || "untested"
        );
        var iNext = (iCurrent + 1) % listStates.length;
        dictVerify.sUser = listStates[iNext];
        dictVerify.sLastUserUpdate = fsFormatUtcTimestamp();
        if (listStates[iNext] === "passed") {
            delete dictVerify.listModifiedFiles;
            delete dictVerify.bOutputModified;
        }
        dictStep.dictVerification = dictVerify;
        _dictWorkflowState.dictUserVerifiedAt[iStep] = Date.now();
        await fnPutStepEdit(iStep, {dictVerification: dictVerify});
        fnRenderStepList();
        fnUpdateHighlightState();
    }

    function fiClientAICSLevel() {
        /* Authoritative source is the server-derived ``iAICSLevel`` on
           the workflow dict, refreshed on every file-status poll. The
           per-step client gate (``fbStepIsAtLeastLevel1``) reads
           live render state (modified-files, deps badges, in-flight
           script changes) that the backend cannot see between polls,
           so we conjoin it with the server's level: any client-visible
           regression demotes the displayed level immediately, but the
           client never invents a level above what the server granted. */
        var dictWorkflow = _dictWorkflowState.dictWorkflow;
        if (!dictWorkflow || !dictWorkflow.listSteps) return 0;
        var iServerLevel = dictWorkflow.iAICSLevel || 0;
        if (iServerLevel === 0) return 0;
        var listSteps = dictWorkflow.listSteps;
        if (listSteps.length === 0) return 0;
        for (var i = 0; i < listSteps.length; i++) {
            if (!fbStepIsAtLeastLevel1(listSteps[i], i)) return 0;
        }
        return iServerLevel;
    }

    function fnUpdateHighlightState() {
        var iLevel = fiClientAICSLevel();
        var listLevelClasses = [
            "aics-level-1", "aics-level-2", "aics-level-3",
        ];
        document.body.classList.remove.apply(
            document.body.classList, listLevelClasses,
        );
        if (iLevel >= 1) {
            document.body.classList.add("aics-level-" + iLevel);
            PipeleyenTerminal.fnUpdateCursorColor("#b39ddb");
        } else {
            PipeleyenTerminal.fnUpdateCursorColor("#13aed5");
        }
        fnTriggerLevelTransitionAnimation(
            iLevel, _dictWorkflowState.iLastRenderedAICSLevel,
        );
        fnRecolorVisibleDagEdges();
        _dictWorkflowState.iLastRenderedAICSLevel = iLevel;
        _fnRefreshAttestationBanner(iLevel);
    }

    function _fnRefreshAttestationBanner(iLevel) {
        /* Show #aicsAttestationBanner when an L3 attestation exists
           but its recorded manifest digest no longer matches the live
           manifest. Loud failure: clicking opens the AICS tab so the
           researcher can re-verify. The poll is light (single GET)
           and only fires when the workflow is at least L2 so we never
           query an envelope-free repo. */
        if (iLevel < 2) {
            _fnHideAttestationBanner();
            return;
        }
        var sId = PipeleyenContainerManager.fsGetSelectedContainerId();
        if (!sId) {
            _fnHideAttestationBanner();
            return;
        }
        VaibifyApi.fdictGet(
            "/api/workflow/" + sId + "/level3/attestation"
        ).then(function (dictResp) {
            _fnRenderAttestationBannerFromResponse(dictResp);
        }).catch(function () {
            _fnHideAttestationBanner();
        });
    }

    function _fnRenderAttestationBannerFromResponse(dictResp) {
        var elBanner = document.getElementById(
            "aicsAttestationBanner"
        );
        if (!elBanner) return;
        var dictCurrent = dictResp && dictResp.dictCurrentAttestation;
        var sLive = (dictResp && dictResp.sLiveManifestDigest) || "";
        if (!dictCurrent) {
            _fnHideAttestationBanner();
            return;
        }
        var sRecorded = dictCurrent.sManifestDigestAtAttestation ||
            "";
        if (!sRecorded || !sLive || sRecorded === sLive) {
            _fnHideAttestationBanner();
            return;
        }
        elBanner.innerHTML = 'L3 attestation expired because the ' +
            'manifest changed. Click to open the AICS tab and ' +
            're-run reproduction verification.';
        elBanner.hidden = false;
        elBanner.onclick = function () {
            var elTab = document.querySelector(
                '.left-tab[data-panel="aics"]'
            );
            if (elTab) elTab.click();
        };
    }

    function _fnHideAttestationBanner() {
        var elBanner = document.getElementById(
            "aicsAttestationBanner"
        );
        if (!elBanner) return;
        elBanner.hidden = true;
        elBanner.innerHTML = "";
        elBanner.onclick = null;
    }

    function fnRecolorVisibleDagEdges() {
        document.querySelectorAll(".dag-container svg").forEach(
            function (elSvg) { fnRecolorDagEdges(elSvg); }
        );
    }

    function fnTriggerLevelTransitionAnimation(iNewLevel, iOldLevel) {
        /* Fires on any upward promotion across the ladder. The same
           DOM overlay is reused for every rung; the body's
           `--aics-bloom-color` CSS variable swaps the gradient color
           between purple (L1), green (L2), and pink (L3) without
           forking the element. */
        if (iNewLevel <= iOldLevel) return;
        if (iNewLevel === 1 || iNewLevel === 2 || iNewLevel === 3) {
            fnAnimateBloomOverlay();
            fnAnimatePanelBorderCascade();
        }
    }

    function fnAnimateBloomOverlay() {
        var elOverlay = document.createElement("div");
        elOverlay.className = "vaibify-bloom-overlay";
        document.body.appendChild(elOverlay);
        requestAnimationFrame(function () {
            elOverlay.classList.add("expanding");
        });
        setTimeout(function () {
            elOverlay.classList.add("fading");
        }, 2700);
        setTimeout(function () {
            if (elOverlay.parentNode) {
                elOverlay.parentNode.removeChild(elOverlay);
            }
        }, 3500);
    }

    function fnAnimatePanelBorderCascade() {
        var listSelectors = [
            "#panelLeft", "#viewerA", "#viewerB",
            "#terminalStrip"
        ];
        listSelectors.forEach(function (sSelector, iIndex) {
            var elPanel = document.querySelector(sSelector);
            if (!elPanel) return;
            setTimeout(function () {
                elPanel.classList.add("vaibify-glow-cascade");
                elPanel.addEventListener("animationend", function () {
                    elPanel.classList.remove("vaibify-glow-cascade");
                }, { once: true });
            }, iIndex * 200);
        });
    }

    function fnDeleteDetailItem(iStep, sArray, iIdx) {
        var sValue = _dictWorkflowState.dictWorkflow.listSteps[iStep][sArray][iIdx];
        var dictVars = fdictBuildClientVariables();
        var sDisplay = fsResolveTemplate(sValue, dictVars);
        fnShowConfirmModal("Delete Item", sDisplay, function () {
            _fnExecuteDeleteItem(iStep, sArray, iIdx, sValue);
        });
    }

    async function _fnExecuteDeleteItem(iStep, sArray, iIdx, sValue) {
        _dictWorkflowState.dictWorkflow.listSteps[iStep][sArray].splice(iIdx, 1);
        fnPushUndo({
            sAction: "delete",
            iStep: iStep,
            sArray: sArray,
            iIdx: iIdx,
            sValue: sValue,
        });
        await fnSaveStepArray(iStep, sArray);
        fnRenderStepList();
    }

    function fnAddNewItem(iStep, sArrayKey) {
        if (sArrayKey === "saInputDataFiles") {
            PipeleyenModals.fnShowFilePickerModal(
                "Add Input Data",
                "Pick the raw data file this step reads, or type "
                    + "its repo-relative path.",
                function (sPath) {
                    fnCommitNewItem(iStep, sArrayKey, sPath);
                });
            return;
        }
        var sPlaceholder = sArrayKey === "saPlotFiles" ?
            "File path..." : "Command...";
        PipeleyenModals.fnShowInlineInput(
            iStep, sArrayKey, sPlaceholder);
    }

    async function fnCommitNewItem(iStep, sArrayKey, sValue) {
        if (!_dictWorkflowState.dictWorkflow.listSteps[iStep][sArrayKey]) {
            _dictWorkflowState.dictWorkflow.listSteps[iStep][sArrayKey] = [];
        }
        _dictWorkflowState.dictWorkflow.listSteps[iStep][sArrayKey].push(sValue);
        fnPushUndo({
            sAction: "add",
            iStep: iStep,
            sArray: sArrayKey,
            iIdx: _dictWorkflowState.dictWorkflow.listSteps[iStep][sArrayKey].length - 1,
            sValue: sValue,
        });
        await fnSaveStepArray(iStep, sArrayKey, true);
        fnRenderStepList();
        fnShowToast("Item added", "success");
    }

    function fnPushUndo(dictAction) {
        _dictWorkflowState.listUndoStack.push(dictAction);
        if (_dictWorkflowState.listUndoStack.length > I_MAX_UNDO) {
            _dictWorkflowState.listUndoStack.shift();
        }
    }

    async function fnUndo() {
        if (_dictWorkflowState.listUndoStack.length === 0) {
            fnShowToast("Nothing to undo", "error");
            return;
        }
        var dictAction = _dictWorkflowState.listUndoStack.pop();
        if (dictAction.sAction === "add") {
            _dictWorkflowState.dictWorkflow.listSteps[dictAction.iStep][dictAction.sArray]
                .splice(dictAction.iIdx, 1);
            await fnSaveStepArray(dictAction.iStep, dictAction.sArray);
        } else if (dictAction.sAction === "delete") {
            _dictWorkflowState.dictWorkflow.listSteps[dictAction.iStep][dictAction.sArray]
                .splice(dictAction.iIdx, 0, dictAction.sValue);
            await fnSaveStepArray(dictAction.iStep, dictAction.sArray);
        }
        fnRenderStepList();
        fnShowToast("Undone", "success");
    }

    async function fnSaveStepArray(iStep, sArray, bScanDeps) {
        var dictUpdate = {};
        dictUpdate[sArray] = _dictWorkflowState.dictWorkflow.listSteps[iStep][sArray];
        await fnPutStepEdit(iStep, dictUpdate);
        if (sArray === "saDataCommands" && bScanDeps) {
            PipeleyenDependencyScanner.fnScanDependencies(iStep);
        }
    }

    /* --- Step Expand/Collapse --- */

    function fnToggleStepExpand(iIndex) {
        if (_dictUiState.setExpandedSteps.has(iIndex)) {
            _dictUiState.setExpandedSteps.delete(iIndex);
        } else {
            _dictUiState.setExpandedSteps.add(iIndex);
            PipeleyenPlotStandards.fnLoadPlotStandardStatus(
                iIndex);
        }
        _dictUiState.iSelectedStepIndex = iIndex;
        fnRenderStepList();
    }

    async function fnToggleStepEnabled(iIndex, bRunEnabled) {
        var dictResult = await fnPutStepEdit(
            iIndex, {bRunEnabled: bRunEnabled});
        if (dictResult) {
            _dictWorkflowState.dictWorkflow
                .listSteps[iIndex].bRunEnabled = bRunEnabled;
        }
    }

    async function fnReorderStep(iFromIndex, iToIndex) {
        try {
            var result = await VaibifyApi.fdictPost(
                "/api/steps/" + _dictSessionState.sContainerId + "/reorder",
                {iFromIndex: iFromIndex, iToIndex: iToIndex});
            _dictWorkflowState.dictWorkflow.listSteps = result.listSteps;
            fnRenderStepList();
            fnShowToast(
                "Step reordered (references renumbered)",
                "success");
        } catch (error) {
            fnShowToast("Reorder failed", "error");
        }
    }

    function fnResetLayout() {
        document.getElementById("mainLayout")
            .style.gridTemplateColumns = "280px 1fr";
        document.getElementById("panelViewerDual")
            .style.flex = "1";
        document.getElementById("viewerA")
            .style.flex = "1";
    }

    function fnSetPollInterval(iSeconds) {
        VaibifyPolling.fnSetPollInterval(iSeconds);
        var elSlider = document.getElementById("gsPollInterval");
        if (elSlider) elSlider.title = iSeconds + " seconds";
        fnStartFileChangePolling();
    }

    async function fnShowDag() {
        if (!_dictSessionState.sContainerId) return;
        fnShowToast("Generating dependency graph...", "success");
        try {
            var sSvgText = await VaibifyApi.fsGetText(
                "/api/workflow/" + _dictSessionState.sContainerId + "/dag");
            _fnRenderDagInViewer(sSvgText);
        } catch (error) {
            fnShowToast(fsSanitizeErrorForUser(error.message), "error");
        }
    }

    function _fnRenderDagInViewer(sSvgText) {
        var dScale = 1.0;
        _fnRenderDagWithZoom(sSvgText, dScale);
    }

    async function _fnExportDag() {
        var sFormat = "svg";
        if (_dictWorkflowState.dictWorkflow) {
            sFormat = (
                _dictWorkflowState.dictWorkflow.sFigureType || "pdf"
            ).toLowerCase();
        }
        var sUrl = "/api/workflow/" +
            _dictSessionState.sContainerId +
            "/dag/export?sFormat=" + encodeURIComponent(sFormat);
        try {
            var response = await fetch(sUrl);
            if (!response.ok) {
                throw new Error("Export failed (" +
                    response.status + ")");
            }
            var blob = await response.blob();
            var sBlobUrl = URL.createObjectURL(blob);
            var elLink = document.createElement("a");
            elLink.href = sBlobUrl;
            elLink.download = "dag." + sFormat;
            document.body.appendChild(elLink);
            elLink.click();
            document.body.removeChild(elLink);
            URL.revokeObjectURL(sBlobUrl);
        } catch (error) {
            fnShowToast(
                fsSanitizeErrorForUser(error.message), "error"
            );
        }
    }

    var _elDagViewport = null;

    function _fnRenderDagWithZoom(sSvgText, dScale) {
        if (_elDagViewport &&
                document.body.contains(_elDagViewport)) {
            _fnPaintDagInViewport(_elDagViewport, sSvgText, dScale);
            return;
        }
        PipeleyenFigureViewer.fnClaimNextViewerForReplacement(
            "pipeline DAG", function (sViewerLetter) {
                _elDagViewport = document.getElementById(
                    "viewport" + sViewerLetter);
                _fnPaintDagInViewport(
                    _elDagViewport, sSvgText, dScale);
            });
    }

    function _fnPaintDagInViewport(elViewport, sSvgText, dScale) {
        elViewport.innerHTML = "";
        elViewport.style.flexDirection = "column";
        elViewport.style.alignItems = "stretch";
        if (dScale === "fit") {
            dScale = 1.0;
        }
        var elToolbar = PipeleyenFigureViewer.fnCreateZoomToolbar(
            dScale, function (dNewScale) {
                _fnRenderDagWithZoom(sSvgText, dNewScale);
            }
        );
        var elExportButton = document.createElement("button");
        elExportButton.className = "btn btn-sm";
        elExportButton.textContent = "Export";
        elExportButton.title = "Export DAG in settings figure format";
        elExportButton.addEventListener("click", _fnExportDag);
        elToolbar.appendChild(elExportButton);
        elViewport.appendChild(elToolbar);
        var elContainer = document.createElement("div");
        elContainer.className = "dag-container";
        elContainer.style.overflow = "auto";
        elContainer.style.flex = "1";
        elContainer.style.display = "flex";
        elContainer.style.justifyContent = "center";
        elContainer.style.padding = "16px";
        elContainer.innerHTML = sSvgText;
        var elSvg = elContainer.querySelector("svg");
        if (elSvg) {
            elSvg.style.transform = "scale(" + dScale + ")";
            elSvg.style.transformOrigin = "top center";
            fnRecolorDagEdges(elSvg);
        }
        elViewport.appendChild(elContainer);
    }

    function fnRecolorDagEdges(elSvg) {
        var sColor = fsGetHighlightColor();
        elSvg.querySelectorAll(".edge path, .edge polygon")
            .forEach(function (el) {
                el.setAttribute("stroke", sColor);
                if (el.tagName === "polygon") {
                    el.setAttribute("fill", sColor);
                }
            });
    }

    function fsGetHighlightColor() {
        return getComputedStyle(document.body)
            .getPropertyValue("--highlight-color").trim();
    }

    async function fnLoadLogs() {
        if (!_dictSessionState.sContainerId) return;
        var elList = document.getElementById("listLogs");
        try {
            var listLogs = await VaibifyApi.fdictGet(
                "/api/logs/" + _dictSessionState.sContainerId);
            if (listLogs.length === 0) {
                elList.innerHTML =
                    '<p class="muted-text">No log files yet.</p>';
                return;
            }
            elList.innerHTML = listLogs.map(function (sFilename) {
                return (
                    '<div class="file-entry" data-log="' +
                    fnEscapeHtml(sFilename) + '">' +
                    fnEscapeHtml(sFilename) + '</div>'
                );
            }).join("");
            elList.querySelectorAll(".file-entry").forEach(function (el) {
                el.addEventListener("click", function () {
                    fnViewLogFile(el.dataset.log);
                });
            });
        } catch (error) {
            elList.innerHTML =
                '<p class="muted-text">Could not load logs.</p>';
        }
    }

    async function fnViewLogFile(sFilename) {
        if (!_dictSessionState.sContainerId) return;
        try {
            var sContent = await VaibifyApi.fsGetText(
                "/api/logs/" + _dictSessionState.sContainerId + "/" +
                encodeURIComponent(sFilename));
            PipeleyenFigureViewer.fnClaimNextViewerForReplacement(
                sFilename, function (sViewerLetter) {
                    var elViewport = document.getElementById(
                        "viewport" + sViewerLetter);
                    elViewport.innerHTML =
                        '<pre class="pipeline-output">' +
                        fnEscapeHtml(sContent) + '</pre>';
                });
        } catch (error) {
            fnShowToast(fsSanitizeErrorForUser(error.message), "error");
        }
    }

    function fnClearOutputModified(iStep) {
        var dictStep = _dictWorkflowState.dictWorkflow.listSteps[iStep];
        if (dictStep && dictStep.dictVerification) {
            delete dictStep.dictVerification.bOutputModified;
            delete dictStep.dictVerification.listModifiedFiles;
        }
    }

    function fnStartFileChangePolling() {
        if (!_dictSessionState.sContainerId) return;
        VaibifyPolling.fnStartFilePolling(_dictSessionState.sContainerId);
    }

    function fnProcessFileStatusResponse(dictStatus) {
        if (dictStatus.sWorkflowReloadError) {
            fnShowToast(
                "project.json error: " +
                dictStatus.sWorkflowReloadError +
                ". Showing last good state.",
                "warning");
        }
        if (dictStatus.bWorkflowReloaded && dictStatus.dictWorkflow) {
            _fnApplyOutOfBandWorkflowReload(
                dictStatus.dictWorkflow, dictStatus.iWorkflowEpoch);
        } else if (!dictStatus.bWorkflowReloaded &&
            typeof dictStatus.iWorkflowEpoch === "number") {
            _dictWorkflowState.iWorkflowEpoch =
                dictStatus.iWorkflowEpoch;
        }
        if (typeof dictStatus.sWorkflowFingerprint === "string") {
            _dictWorkflowState.sWorkflowFingerprint =
                dictStatus.sWorkflowFingerprint;
        }
        _fnReflectDispatchedRunState(dictStatus.dictRunState);
        PipeleyenFileOps.fnDetectOutputFileChanges(
            dictStatus.dictModTimes || {}, _dictWorkflowState);
        if (dictStatus.dictMaxMtimeByStep) {
            _dictWorkflowState.dictOutputMtimes =
                dictStatus.dictMaxMtimeByStep;
        }
        if (dictStatus.dictMaxPlotMtimeByStep) {
            _dictWorkflowState.dictPlotMtimes =
                dictStatus.dictMaxPlotMtimeByStep;
        }
        if (dictStatus.dictMaxDataMtimeByStep) {
            _dictWorkflowState.dictMaxDataMtimeByStep =
                dictStatus.dictMaxDataMtimeByStep;
        }
        if (dictStatus.dictMaxInputMtimeByStep) {
            _dictWorkflowState.dictMaxInputMtimeByStep =
                dictStatus.dictMaxInputMtimeByStep;
        }
        if (dictStatus.dictMarkerMtimeByStep) {
            _dictWorkflowState.dictMarkerMtimeByStep =
                dictStatus.dictMarkerMtimeByStep;
        }
        if (dictStatus.dictTestSourceMtimeByStep) {
            _dictWorkflowState.dictTestSourceMtimeByStep =
                dictStatus.dictTestSourceMtimeByStep;
        }
        if (dictStatus.dictTestCategoryMtimes) {
            _dictWorkflowState.dictTestCategoryMtimes =
                dictStatus.dictTestCategoryMtimes;
        }
        _fnApplyBlockerAndLevelState(dictStatus);
        fnResetStaleUserVerifications();
        var dictInv = dictStatus.dictInvalidatedSteps;
        if (dictInv && Object.keys(dictInv).length > 0) {
            fnApplyInvalidatedSteps(dictInv);
        }
        fnUpdateDepsTimestamps();
        PipeleyenFileOps.fnUpdateScriptStatus(
            dictStatus.dictScriptStatus, _dictWorkflowState);
        if (dictStatus.dictTestMarkers) {
            PipeleyenTestManager.fnApplyTestMarkers(
                dictStatus.dictTestMarkers);
        }
        if (dictStatus.dictTestFileChanges) {
            PipeleyenTestManager.fnNotifyTestFileChanges(
                dictStatus.dictTestFileChanges);
        }
    }

    function _fnApplyBlockersFromPoll(dictStatus) {
        var dictByStep = _fdictBlockersByStepIndex(
            dictStatus.listBlockers);
        _dictWorkflowState.dictBlockersByStep = dictByStep;
        _dictWorkflowState.iL1BlockerCount =
            (typeof dictStatus.iL1BlockerCount === "number")
                ? dictStatus.iL1BlockerCount
                : Object.keys(dictByStep).length;
        var dictByStepL2 = _fdictBlockersByStepIndex(
            dictStatus.listLevel2Blockers);
        _dictWorkflowState.dictBlockersByStepLevel2 = dictByStepL2;
        _dictWorkflowState.iL2BlockerCount =
            (typeof dictStatus.iL2BlockerCount === "number")
                ? dictStatus.iL2BlockerCount
                : Object.keys(dictByStepL2).length;
        _fnApplyL3BlockersFromPoll(dictStatus);
    }

    function _fnApplyBlockerAndLevelState(dictStatus) {
        // A poll can change blockers or level cells without touching
        // any other rendered input; re-render when (and only when)
        // they actually moved so the dashboard never shows a stale
        // ladder. The incremental renderer skips unchanged cards.
        var sPriorState = _fsBlockerAndLevelSnapshot();
        _fnApplyBlockersFromPoll(dictStatus);
        _fnApplyLevelStatesFromPoll(dictStatus);
        if (_fsBlockerAndLevelSnapshot() !== sPriorState) {
            fnRenderStepList();
        }
    }

    function _fsBlockerAndLevelSnapshot() {
        return JSON.stringify([
            _dictWorkflowState.dictWorkflow
                ? _dictWorkflowState.dictWorkflow.iAICSLevel
                : null,
            _dictWorkflowState.dictBlockersByStep,
            _dictWorkflowState.dictBlockersByStepLevel2,
            _dictWorkflowState.dictBlockersByStepLevel3,
            _dictWorkflowState.dictStepLevels,
            _dictWorkflowState.dictStepLevelHighWater,
            _dictWorkflowState.dictStepLevelWarnings,
            _dictWorkflowState.dictWorkflowScopeLevels,
            _dictWorkflowState.dictWorkflowLevelHighWater,
            _dictWorkflowState.dictWorkflowEnvelopeDetail,
        ]);
    }

    function _fnApplyLevelStatesFromPoll(dictStatus) {
        // Level-cell wire keys (Scope B/P backend projection). Each
        // key is optional so older payloads degrade to the previous
        // state rather than blanking the cells.
        if (typeof dictStatus.iAICSLevel === "number" &&
            _dictWorkflowState.dictWorkflow) {
            /* The theme (fiClientAICSLevel) reads this integer off
             * the workflow dict. Without this copy the level cells
             * update live but the workflow-level promotion only
             * arrives on a full reload — every step showed its L1
             * check while the theme stayed at level 0. */
            _dictWorkflowState.dictWorkflow.iAICSLevel =
                dictStatus.iAICSLevel;
            _fnMaybeAutoCollapseStepsOnFirstL1(dictStatus.iAICSLevel);
        }
        if (dictStatus.dictStepLevels) {
            _dictWorkflowState.dictStepLevels =
                dictStatus.dictStepLevels;
        }
        if (dictStatus.dictStepLevelHighWater) {
            _dictWorkflowState.dictStepLevelHighWater =
                dictStatus.dictStepLevelHighWater;
        }
        if (dictStatus.dictWorkflowScopeLevels) {
            _dictWorkflowState.dictWorkflowScopeLevels =
                dictStatus.dictWorkflowScopeLevels;
        }
        if (dictStatus.dictWorkflowLevelHighWater) {
            _dictWorkflowState.dictWorkflowLevelHighWater =
                dictStatus.dictWorkflowLevelHighWater;
        }
        _fnApplyWarningAndEnvelopeFromPoll(dictStatus);
    }

    function _fnApplyWarningAndEnvelopeFromPoll(dictStatus) {
        // Consolidated regression warnings and the workflow envelope
        // detail (software, artifacts, determinism, remote syncs).
        // Both keys are optional so older payloads degrade to the
        // previous state rather than blanking the cells.
        if (dictStatus.dictStepLevelWarnings) {
            _dictWorkflowState.dictStepLevelWarnings =
                dictStatus.dictStepLevelWarnings;
        }
        if (dictStatus.dictWorkflowEnvelopeDetail) {
            _dictWorkflowState.dictWorkflowEnvelopeDetail =
                dictStatus.dictWorkflowEnvelopeDetail;
        }
    }

    function _fdictBlockersByStepIndex(listBlockers) {
        var dictByStep = {};
        var listSafe = listBlockers || [];
        for (var i = 0; i < listSafe.length; i++) {
            var dictEntry = listSafe[i];
            if (dictEntry && typeof dictEntry.iStepIndex === "number") {
                dictByStep[dictEntry.iStepIndex] = dictEntry;
            }
        }
        return dictByStep;
    }

    function _fnApplyL3BlockersFromPoll(dictStatus) {
        var dictByStepL3 = {};
        var listL3 = dictStatus.listLevel3Blockers || [];
        for (var i = 0; i < listL3.length; i++) {
            var dictEntry = listL3[i];
            if (!dictEntry || typeof dictEntry.iStepIndex !== "number") {
                continue;
            }
            if (dictEntry.iStepIndex < 0) continue;
            dictByStepL3[dictEntry.iStepIndex] = dictEntry;
        }
        _dictWorkflowState.dictBlockersByStepLevel3 = dictByStepL3;
        _dictWorkflowState.iL3BlockerCount =
            (typeof dictStatus.iL3BlockerCount === "number")
                ? dictStatus.iL3BlockerCount
                : Object.keys(dictByStepL3).length;
    }

    function fnResetStaleUserVerifications() {
        if (!_dictWorkflowState.dictWorkflow || !_dictWorkflowState.dictWorkflow.listSteps) return;
        var bChanged = false;
        var iNow = Date.now();
        for (var i = 0; i < _dictWorkflowState.dictWorkflow.listSteps.length; i++) {
            if (_dictWorkflowState.dictUserVerifiedAt[i] &&
                (iNow - _dictWorkflowState.dictUserVerifiedAt[i]) < 15000) {
                continue;
            }
            var dictStep = _dictWorkflowState.dictWorkflow.listSteps[i];
            var dictVerify = (dictStep.dictVerification || {});
            if (dictVerify.sUser !== "passed") continue;
            if (_fbOutputNewerThanVerification(i, dictVerify)) {
                dictVerify.sUser = "untested";
                delete dictVerify.sLastUserUpdate;
                dictStep.dictVerification = dictVerify;
                bChanged = true;
            }
        }
        if (bChanged) fnRenderStepList();
    }

    function _fbOutputNewerThanVerification(iStep, dictVerify) {
        var sMaxMtime = _dictWorkflowState.dictPlotMtimes[String(iStep)];
        if (!sMaxMtime) return false;
        var iOutputEpoch = parseInt(sMaxMtime, 10);
        var iUserEpoch = fiParseUtcTimestamp(
            dictVerify.sLastUserUpdate);
        var bResult = iUserEpoch > 0 && iOutputEpoch > iUserEpoch;
        if (bResult) {
            console.log(
                "[STALE] step " + iStep + ": plotEpoch=" +
                iOutputEpoch + " userEpoch=" + iUserEpoch +
                " sLastUserUpdate=" + dictVerify.sLastUserUpdate
            );
        }
        return bResult;
    }

    function fnUpdateDepsTimestamps() {
        if (!_dictWorkflowState.dictWorkflow ||
                !_dictWorkflowState.dictWorkflow.listSteps) return;
        var sNow = fsFormatUtcTimestamp();
        for (var i = 0; i < _dictWorkflowState.dictWorkflow.listSteps.length; i++) {
            var listDeps = flistGetStepDependencies(i);
            if (listDeps.length === 0) continue;
            var dictStep = _dictWorkflowState.dictWorkflow.listSteps[i];
            var dictVerify = dictStep.dictVerification || {};
            dictVerify.sLastDepsCheck = sNow;
            dictStep.dictVerification = dictVerify;
        }
    }

    function fnApplyInvalidatedSteps(dictStepVerifications) {
        var bAnyChanged = false;
        var iNow = Date.now();
        var iGraceMs = 15000;
        for (var sIndex in dictStepVerifications) {
            var iStep = parseInt(sIndex, 10);
            if (_fbWithinGracePeriod(iStep, iNow, iGraceMs)) {
                continue;
            }
            var dictStep = _dictWorkflowState.dictWorkflow.listSteps[iStep];
            if (!dictStep) continue;
            var sOldUser = (dictStep.dictVerification || {}).sUser;
            var sNewUser = (dictStepVerifications[sIndex] || {}).sUser;
            if (sOldUser !== sNewUser) {
                console.log(
                    "[INVALIDATE] step " + iStep +
                    ": sUser " + sOldUser + " -> " + sNewUser
                );
            }
            dictStep.dictVerification =
                dictStepVerifications[sIndex];
            bAnyChanged = true;
        }
        if (bAnyChanged) fnRenderStepList();
    }

    function _fbWithinGracePeriod(iStep, iNow, iGraceMs) {
        var iAckedAt = PipeleyenPipelineRunner.fiGetAcknowledgedAt(iStep);
        if (iAckedAt && (iNow - iAckedAt) < iGraceMs) {
            return true;
        }
        var iVerifiedAt = _dictWorkflowState.dictUserVerifiedAt[iStep];
        if (iVerifiedAt && (iNow - iVerifiedAt) < iGraceMs) {
            return true;
        }
        return false;
    }

    var fnShowErrorModal = PipeleyenModals.fnShowErrorModal;

    async function fnAddAiDeclarationStep() {
        var sContainerId = _dictSessionState.sContainerId;
        if (!sContainerId || !_dictWorkflowState.dictWorkflow) return;
        try {
            var dictResult = await VaibifyApi.fdictPost(
                "/api/workflow/" + encodeURIComponent(sContainerId) +
                "/ai-declaration/add-step", {});
            _dictWorkflowState.dictWorkflow.listSteps.push(
                dictResult.dictStep);
            fnRenderStepList();
            fnShowToast("AI declaration step added", "success");
        } catch (error) {
            fnShowToast(
                fsSanitizeErrorForUser(error.message), "error");
        }
    }

    function fnOpenVsCode() {
        var sHexId = _dictSessionState.sContainerId.replace(/-/g, "");
        var sUri =
            "vscode://ms-vscode-remote.remote-containers/attach?containerId=" +
            sHexId;
        window.open(sUri, "_blank");
        fnShowToast("Opening VS Code...", "success");
    }

    function fnShowContextMenu(iX, iY, iIndex) {
        _dictUiState.iContextStepIndex = iIndex;
        var el = document.getElementById("contextMenu");
        el.style.left = iX + "px";
        el.style.top = iY + "px";
        el.classList.add("active");
    }

    function fnHideContextMenu() {
        document.getElementById("contextMenu")
            .classList.remove("active");
    }

    function fnHandleContextAction(sAction, iIndex) {
        if (sAction === "runStep") {
            PipeleyenPipelineRunner.fnRunSingleStep(iIndex);
        } else if (sAction === "edit") {
            PipeleyenStepEditor.fnOpenEditModal(iIndex);
        } else if (sAction === "runFrom") {
            PipeleyenPipelineRunner.fnSendPipelineAction({
                sAction: "runFrom",
                iStartStep: iIndex + 1,
            });
        } else if (sAction === "insertBefore") {
            PipeleyenStepEditor.fnOpenInsertModal(iIndex);
        } else if (sAction === "insertAfter") {
            PipeleyenStepEditor.fnOpenInsertModal(iIndex + 1);
        } else if (sAction === "delete") {
            fnDeleteStep(iIndex);
        }
    }

    function fnDeleteStep(iIndex) {
        var sName = _dictWorkflowState.dictWorkflow.listSteps[iIndex].sName;
        fnShowConfirmModal(
            "Delete Step",
            'Delete step "' + sName + '"?',
            function () { _fnExecuteDeleteStep(iIndex); }
        );
    }

    async function _fnExecuteDeleteStep(iIndex) {
        try {
            var result = await VaibifyApi.fnDelete(
                "/api/steps/" + _dictSessionState.sContainerId + "/" + iIndex);
            _dictWorkflowState.dictWorkflow.listSteps = result.listSteps;
            if (_dictUiState.iSelectedStepIndex === iIndex) _dictUiState.iSelectedStepIndex = -1;
            _dictUiState.setExpandedSteps.delete(iIndex);
            fnPruneStaleStatuses();
            fnRenderStepList();
            fnShowToast(
                "Step deleted (references renumbered)",
                "success");
        } catch (error) {
            fnShowToast("Delete failed", "error");
        }
    }

    function fnShowOutputNotAvailable() {
        PipeleyenFigureViewer.fnShowPlaceholderInNextViewer(
            '<span class="placeholder output-missing-message">' +
            'Output not available. Run the step to generate.</span>',
            "missing output");
    }

    function fnShowBinaryNotViewable() {
        PipeleyenFigureViewer.fnShowPlaceholderInNextViewer(
            '<span class="placeholder">' +
            'File cannot be viewed.</span>',
            "binary file");
    }

    var fsSanitizeErrorForUser = VaibifyUtilities.fsSanitizeErrorForUser;

    function fnShowToast(sMessage, sType, fnOnClick) {
        var el = document.createElement("div");
        el.className = "toast " + (sType || "");
        if (fnOnClick) el.classList.add("toast-clickable");
        el.innerHTML = fnEscapeHtml(sMessage) +
            '<button class="toast-close">&times;</button>';
        el.querySelector(".toast-close").addEventListener(
            "click", function (event) {
                event.stopPropagation();
                el.remove();
            }
        );
        if (fnOnClick) {
            el.addEventListener("click", function () {
                fnOnClick();
                el.remove();
            });
        }
        if (sType !== "error" && sType !== "warning") {
            setTimeout(function () { el.remove(); }, 4000);
        }
        document.getElementById("toastContainer").appendChild(el);
    }

    var fnEscapeHtml = VaibifyUtilities.fnEscapeHtml;

    /* --- Public API --- */

    return {
        fnInitialize: fnInitialize,
        fnShowToast: fnShowToast,
        fnRenderStepList: fnRenderStepList,
        fnRenderStepListPartial: fnRenderStepListPartial,
        _fnInvalidateAllRenderCaches: _fnInvalidateAllRenderCaches,
        fsGetContainerId: function () {
            return _dictSessionState.sContainerId;
        },
        fsGetSessionToken: function () {
            return _dictSessionState.sSessionToken;
        },
        fsGetLeaseId: fsGetLeaseId,
        fsGetLeaseForContainer: fsGetLeaseForContainer,
        fnRecordClaimedLease: fnRecordClaimedLease,
        fnForgetLease: fnForgetLease,
        fdictGetWorkflow: function () {
            return _dictWorkflowState.dictWorkflow;
        },
        fsGetWorkflowPath: function () {
            return _dictWorkflowState.sWorkflowPath;
        },
        fiGetSelectedStepIndex: function () {
            return _dictUiState.iSelectedStepIndex;
        },
        fdictBuildClientVariables: fdictBuildClientVariables,
        fnShowConfirmModal: fnShowConfirmModal,
        fnShowInputModal: fnShowInputModal,
        fnClearOutputModified: fnClearOutputModified,
        fnActivateWorkflow: _fnActivateWorkflow,
        fnRefreshWorkflowData: fnRefreshWorkflowData,
        fiGetWorkflowEpoch: function () {
            return _dictWorkflowState.iWorkflowEpoch;
        },
        fnEnterNoWorkflow: fnEnterNoWorkflow,
        fnSaveStepUpdate: fnSaveStepUpdate,
        fnShowWorkflowPicker: fnShowWorkflowPicker,
        fnSetPlotStandardExists: function (sKey, bValue) {
            _dictWorkflowState.dictPlotStandardExists[sKey] =
                bValue;
        },
        fbGetPlotStandardExists: function (sKey) {
            return _dictWorkflowState
                .dictPlotStandardExists[sKey];
        },
        fnShowErrorModal: fnShowErrorModal,
        fnUpdateHighlightState: fnUpdateHighlightState,
        fsComputeStepLabel: fsComputeStepLabel,
        fdictGetVerification: fdictGetVerification,
        fdictGetTests: fdictGetTests,
        fnInvalidateStepFileCache: fnInvalidateStepFileCache,
        fnSetStepStatus: function (iIndex, sStatus) {
            _dictWorkflowState.dictStepStatus[iIndex] = sStatus;
        },
        fnClearRunningStatuses: fnClearRunningStatuses,
        fnResetQueuedSteps: fnResetQueuedSteps,
        fnClearAllStepStatuses: function () {
            _dictWorkflowState.dictStepStatus = {};
        },
        fnStartFileChangePolling: fnStartFileChangePolling,
        fnToggleStepEnabled: fnToggleStepEnabled,
        fnClearFileExistenceCache: function () {
            _dictWorkflowState.dictFileExistenceCache = {};
        },
        fiGetL1BlockerCount: function () {
            return _dictWorkflowState.iL1BlockerCount || 0;
        },
        fiGetL2BlockerCount: function () {
            return _dictWorkflowState.iL2BlockerCount || 0;
        },
        fiGetL3BlockerCount: function () {
            return _dictWorkflowState.iL3BlockerCount || 0;
        },
        fiGetCachedAicsLevel: function () {
            var iCached = _dictWorkflowState.iCachedAicsLevel;
            return typeof iCached === "number" ? iCached : null;
        },
        fnSetCachedAicsLevel: fnSetCachedAicsLevel,
        fdictBlockerCountsByLevel: fdictBlockerCountsByLevel,
        fdictBlockerGlyphCatalog: fdictBlockerGlyphCatalog,
        fsBlockerHintForStep: fsBlockerHintForStep,
        fsBlockerHintForFile: fsBlockerHintForFile,
        fnAddAiDeclarationStep: fnAddAiDeclarationStep,
        fnHandleDiscoveredOutputs: fnHandleDiscoveredOutputs,

        /* New public methods for extracted modules */
        fnDeleteDetailItem: fnDeleteDetailItem,
        fnAddDiscoveredOutput: fnAddDiscoveredOutput,
        fnAddNewItem: fnAddNewItem,
        fnCycleUserVerification: fnCycleUserVerification,
        fsetGetExpandedCategory: fsetGetExpandedCategory,
        fnToggleUnitTestExpand: fnToggleUnitTestExpand,
        fnToggleDepsExpand: fnToggleDepsExpand,
        fnToggleStepExpand: fnToggleStepExpand,
        fnToggleStepsBlockExpand: fnToggleStepsBlockExpand,
        fnToggleProjectBlockExpand:
            fnToggleProjectBlockExpand,
        fnToggleBinaryAddForm: fnToggleBinaryAddForm,
        fnToggleRequirementGroup: fnToggleRequirementGroup,
        fnToggleRequirementRow: fnToggleRequirementRow,
        fnRunProjectAction: fnRunProjectAction,
        fnTogglePlotOnly: fnTogglePlotOnly,
        fnToggleNoInputData: fnToggleNoInputData,
        fnBulkDeclareNoInputData: fnBulkDeclareNoInputData,
        fnSetStepBudget: fnSetStepBudget,
        fnShowContextMenu: fnShowContextMenu,
        fnHideContextMenu: fnHideContextMenu,
        fnReorderStep: fnReorderStep,
        fnHandleContextAction: fnHandleContextAction,
        fiGetContextStepIndex: function () {
            return _dictUiState.iContextStepIndex;
        },
        fnDisconnect: fnDisconnect,
        fnShowContainerLanding: fnShowContainerLanding,
        fnStopAllHubPolling: fnStopAllHubPolling,
        fnResumeHubPollingForCurrentView:
            fnResumeHubPollingForCurrentView,
        fbIsWorkflowMode: function () {
            return _dictSessionState.dictDashboardMode &&
                _dictSessionState.dictDashboardMode.sMode ===
                "workflow";
        },
        fnLoadLogs: fnLoadLogs,
        fnShowDag: fnShowDag,
        fnOpenVsCode: fnOpenVsCode,
        fnResetLayout: fnResetLayout,
        fnReconnectToCurrentContainer: function () {
            if (_dictSessionState.sContainerId) {
                PipeleyenContainerManager.fnConnectToContainer(
                    _dictSessionState.sContainerId);
            }
        },
        fnRenderGlobalSettings: fnRenderGlobalSettings,
        fnCommitNewItem: fnCommitNewItem,
        fnShowOutputNotAvailable: fnShowOutputNotAvailable,
        fnShowBinaryNotViewable: fnShowBinaryNotViewable,
        fbIsFileMissing: fbIsFileMissing,
        fsGetFileCategory: fsGetFileCategory,
        fbStepIsAtLeastLevel1: fbStepIsAtLeastLevel1,
        fsetGetExpandedSteps: function () {
            return _dictUiState.setExpandedSteps;
        },
        fnSaveStepArray: fnSaveStepArray,
        flistGetStepDependencies: flistGetStepDependencies,
    };
})();

document.addEventListener("DOMContentLoaded", PipeleyenApp.fnInitialize);

function fnBlockUnload(event) {
    event.preventDefault();
    event.returnValue = "";
}

function fnReleaseActiveContainerOnUnload() {
    if (typeof PipeleyenContainerManager === "undefined") return;
    if (typeof PipeleyenApp === "undefined") return;
    var sName = PipeleyenContainerManager.fsGetSelectedContainerName();
    var sLeaseId = PipeleyenApp.fsGetLeaseId();
    if (!sName || !sLeaseId) return;
    try {
        navigator.sendBeacon(
            "/api/registry/" + encodeURIComponent(sName) +
            "/release?sLeaseId=" + encodeURIComponent(sLeaseId),
        );
    } catch (error) {
        /* best-effort: the grace reaper frees the owner if this misses */
    }
}

window.addEventListener("beforeunload", fnBlockUnload);
window.addEventListener("pagehide", function (event) {
    PipeleyenApp.fnStopAllHubPolling();
    if (event.persisted) return;
    fnReleaseActiveContainerOnUnload();
});

document.addEventListener("visibilitychange", function () {
    if (document.hidden) {
        PipeleyenApp.fnStopAllHubPolling();
    } else {
        PipeleyenApp.fnResumeHubPollingForCurrentView();
    }
});

window.addEventListener("keydown", function (event) {
    var bCloseShortcut = (event.metaKey || event.ctrlKey) &&
        event.key === "w";
    if (!bCloseShortcut) return;
    event.preventDefault();
    PipeleyenApp.fnShowConfirmModal(
        "Close Vaibify",
        "Are you sure you want to close this window? " +
            "Unsaved changes may be lost.",
        function () {
            window.removeEventListener("beforeunload",
                fnBlockUnload);
            window.close();
        }
    );
});
