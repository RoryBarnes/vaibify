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
    };

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
        bShowTimestamps: false,
        iContextStepIndex: -1,
    };

    var I_MAX_UNDO = 50;
    var I_HUB_POLL_INTERVAL_MS = 3000;
    var _dictHubPolling = {
        iContainerIntervalId: null,
        iWorkflowIntervalId: null,
        sWorkflowContainerId: null,
    };
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

    /* --- WebSocket and Polling Registration --- */

    function fnRegisterWebSocketHandlers() {
        VaibifyWebSocket.fnOnEvent("*",
            PipeleyenPipelineRunner.fnHandlePipelineEvent);
        VaibifyWebSocket.fnOnEvent("_wsClose", function (dictEvent) {
            fnClearRunningStatuses();
            fnRenderStepList();
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
        await fnFetchSessionToken();
        fnRegisterWebSocketHandlers();
        fnRegisterPollingHandlers();
        fnLoadUserName();
        fnLoadTimestampSetting();
        PipeleyenContainerManager.fnLoadContainers();
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
    }

    function _fnActivateWorkflow(sId, data, sWorkflowName) {
        _fnResetWorkflowState();
        VaibifyPolling.fnStopDiscoveryPolling();
        _dictSessionState.sContainerId = sId;
        _dictWorkflowState.dictWorkflow = data.dictWorkflow;
        _dictWorkflowState.sWorkflowPath = data.sWorkflowPath;
        _dictSessionState.dictDashboardMode = DICT_MODE_WORKFLOW;
        _fnSurfaceStateLoadNotice(data.dictWorkflow);
        if (data.dictFileStatus) {
            fnProcessFileStatusResponse(data.dictFileStatus);
        }
        var iStepCount = (_dictWorkflowState.dictWorkflow.listSteps || []).length;
        if (iStepCount > 500) {
            fnShowToast(
                "This workflow has " + iStepCount + " steps. " +
                "Large workflows may use significant memory. " +
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
        PipeleyenTerminal.fnEnsureTab();
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
        _fnClearFileCaches();
        _fnInvalidateAllRenderCaches();
        fnRenderStepList();
        fnPollAllStepFiles();
    }

    function _fnApplyOutOfBandWorkflowReload(dictWorkflowNew) {
        var iPriorSelected = _dictUiState.iSelectedStepIndex;
        var dictPriorExpanded = _fdictSnapshotExpansionSets();
        _dictWorkflowState.dictWorkflow = dictWorkflowNew;
        _fnClearFileCaches();
        _fnInvalidateAllRenderCaches();
        fnRenderStepList();
        var iStepCount = (dictWorkflowNew.listSteps || []).length;
        _fnRestoreUiSelection(iPriorSelected, iStepCount);
        _fnRestoreExpansionSets(dictPriorExpanded, iStepCount);
        fnRenderStepList();
        fnShowToast(
            "Workflow definition reloaded from disk", "info");
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
        if (dictPrior.setSteps.has(-1)) {
            // -1 is the expandable workflow row, valid in every
            // workflow regardless of step count.
            _dictUiState.setExpandedSteps.add(-1);
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
            await VaibifyApi.fdictPostRaw("/api/connect/" + sId);
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
                "New workflow available: " + dictWf.sName
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
        _fnStopContainerHubPolling();
        _dictHubPolling.iContainerIntervalId = setInterval(
            _fnPollContainerHubIfIdle, I_HUB_POLL_INTERVAL_MS,
        );
    }

    function _fnPollContainerHubIfIdle() {
        if (_fbContainerHubHasOpenMenu()) return;
        PipeleyenContainerManager.fnLoadContainers();
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
        if (_dictHubPolling.iContainerIntervalId !== null) {
            clearInterval(_dictHubPolling.iContainerIntervalId);
            _dictHubPolling.iContainerIntervalId = null;
        }
    }

    function _fnStartWorkflowHubPolling(sContainerId) {
        _fnStopWorkflowHubPolling();
        if (!sContainerId) return;
        _dictHubPolling.sWorkflowContainerId = sContainerId;
        _dictHubPolling.iWorkflowIntervalId = setInterval(
            function () {
                _fnRefreshWorkflowHubList(
                    _dictHubPolling.sWorkflowContainerId,
                );
            },
            I_HUB_POLL_INTERVAL_MS,
        );
    }

    function _fnStopWorkflowHubPolling() {
        if (_dictHubPolling.iWorkflowIntervalId !== null) {
            clearInterval(_dictHubPolling.iWorkflowIntervalId);
            _dictHubPolling.iWorkflowIntervalId = null;
        }
        _dictHubPolling.sWorkflowContainerId = null;
    }

    async function _fnRefreshWorkflowHubList(sContainerId) {
        try {
            var listWorkflows = await VaibifyApi.fdictGet(
                "/api/workflows/" + encodeURIComponent(sContainerId));
            VaibifyWorkflowManager.fnRenderWorkflowList(
                listWorkflows, sContainerId);
        } catch (error) {
            /* best-effort: leave the last-rendered list in place */
        }
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
        if (sRepoRoot.endsWith("/.vaibify/workflows")) {
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
            var listFiles = (step.saDataFiles || []).concat(
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

    function fsToleranceToExponent(fTolerance) {
        return Math.round(Math.log10(fTolerance));
    }

    async function fnSaveGlobalSettings() {
        var iExp = parseInt(
            document.getElementById("gsTolerance").value, 10);
        var elAutoArchive = document.getElementById("gsAutoArchive");
        var dictUpdates = {
            sPlotDirectory: document.getElementById("gsPlotDirectory").value,
            sFigureType: document.getElementById("gsFigureType").value,
            iNumberOfCores: parseInt(
                document.getElementById("gsNumberOfCores").value
            ),
            fTolerance: Math.pow(10, iExp),
            bAutoArchive: elAutoArchive
                ? elAutoArchive.checked : false,
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
            fnShowToast("Settings saved", "success");
            fnRenderStepList();
        } catch (error) {
            fnShowToast("Failed to save settings", "error");
        }
    }

    /* --- Step List --- */

    function fdictBuildRenderContext() {
        return {
            dictStepStatus: _dictWorkflowState.dictStepStatus,
            iSelectedStepIndex: _dictUiState.iSelectedStepIndex,
            setExpandedSteps: _dictUiState.setExpandedSteps,
            setExpandedDeps: _dictUiState.setExpandedDeps,
            setExpandedUnitTests: PipeleyenTestManager.fsetGetExpandedUnitTests(),
            setStepsWithData: PipeleyenTestManager.fsetGetStepsWithData(),
            setGeneratingInFlight: PipeleyenTestManager.fsetGetGeneratingInFlight(),
            dictPlotStandardExists: _dictWorkflowState.dictPlotStandardExists,
            dictScriptModified: _dictWorkflowState.dictScriptModified,
            dictStaleArtifacts: _dictWorkflowState.dictStaleArtifacts,
            dictOutputMtimes: _dictWorkflowState.dictOutputMtimes,
            dictMaxDataMtimeByStep:
                _dictWorkflowState.dictMaxDataMtimeByStep,
            dictMaxPlotMtimeByStep: _dictWorkflowState.dictPlotMtimes,
            dictMarkerMtimeByStep:
                _dictWorkflowState.dictMarkerMtimeByStep,
            dictTestCategoryMtimes:
                _dictWorkflowState.dictTestCategoryMtimes,
            dictDiscoveredOutputs: _dictWorkflowState.dictDiscoveredOutputs,
            dictWorkflow: _dictWorkflowState.dictWorkflow,
            sUserName: _dictSessionState.sUserName,
            fsComputeStepDotState: fsComputeStepDotState,
            fsComputeStepLabel: fsComputeStepLabel,
            fsBuildWarningBadge: fsBuildWarningBadge,
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
            fbBlockerBannerRendersPencil: fbBlockerBannerRendersPencil,
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
        // Compact key that changes when the step count, the
        // automated/interactive boundary positions, the presence of
        // an AI-declaration step (ghost-row trigger), or the
        // workflow-level header inputs shift. Used to detect
        // "structural change" so the renderer falls back to the full
        // innerHTML rebuild path instead of orphaning the header row
        // or ghost row that only the full path renders.
        var sKey = String(listSteps.length);
        for (var i = 0; i < listSteps.length; i++) {
            if (listSteps[i].sStepKind === "ai-declaration") {
                sKey += "D";
            } else {
                sKey += listSteps[i].bInteractive === true ? "I" : "A";
            }
        }
        return sKey + "|" + _fsWorkflowHeaderSignature();
    }

    function _fsWorkflowHeaderSignature() {
        // Every input the workflow-level header row reads. Including
        // the workflow-scope L2 blocker keeps the header tooltip in
        // sync with the dominant remediation hint.
        return JSON.stringify([
            _dictWorkflowState.dictWorkflowScopeLevels,
            _dictWorkflowState.dictWorkflowLevelHighWater,
            (_dictWorkflowState.dictBlockersByStepLevel2 || {})[-1] ||
                null,
            (_dictWorkflowState.dictStepLevelWarnings || {})["-1"] ||
                null,
            _dictWorkflowState.dictWorkflowEnvelopeDetail,
            _dictUiState.setExpandedSteps.has(-1),
        ]);
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
            dictContext.dictMaxPlotMtimeByStep[sIdx] || "",
            dictContext.dictTestCategoryMtimes[sIdx] || null,
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
        var sHtml = VaibifyStepRenderer.fsRenderWorkflowLevelHeader(
            dictContext);
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
        if (!_fbWorkflowHasAiDeclarationStep()) {
            sHtml += VaibifyStepRenderer
                .fsRenderGhostAiDeclarationRow();
        }
        elList.innerHTML = sHtml;
        _sLastBoundarySignature = sBoundary;
    }

    function _fbWorkflowHasAiDeclarationStep() {
        var listSteps = (_dictWorkflowState.dictWorkflow || {})
            .listSteps || [];
        for (var i = 0; i < listSteps.length; i++) {
            if (listSteps[i].sStepKind === "ai-declaration") {
                return true;
            }
        }
        return false;
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
            if (sVal === "running" || sVal === "queued") {
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

    function fsBuildWarningBadge(step, iIndex) {
        var sBlockerGlyph = fsBuildL1BlockerBannerGlyph(iIndex);
        var listWarnings = [];
        var dictV = step.dictVerification || {};
        var listMod = dictV.listModifiedFiles || [];
        if (listMod.length > 0) {
            var sNames = listMod.map(function (s) {
                return s.split("/").pop();
            }).join(", ");
            listWarnings.push("Modified: " + sNames);
        }
        if (fbAnyDepTimingStale(iIndex)) {
            listWarnings.push(
                "Upstream changed; rerun to re-verify");
        }
        if (listWarnings.length === 0) return sBlockerGlyph;
        var sTooltip = fnEscapeHtml(listWarnings.join("\n"));
        return sBlockerGlyph + '<span class="data-modified-badge" ' +
            'title="' + sTooltip + '">&#9888;</span>';
    }

    var _DICT_BLOCKER_CRITERION_GLYPHS = {
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
            sLabel: "Tests are not green; re-run to clear blocker",
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
            sLabel: "User attestation pending",
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
        "not-in-github-mirror": {
            sIcon: "⚠",
            sLabel: "Outputs differ from GitHub mirror — " +
                "push to clear blocker",
            sClass: "step-blocker-glyph-l2-mirror",
        },
        "not-in-zenodo-deposit": {
            sIcon: "⚠",
            sLabel: "Outputs differ from Zenodo deposit — " +
                "archive to clear blocker",
            sClass: "step-blocker-glyph-l2-zenodo",
        },
        "github-verify-stale": {
            sIcon: "⚠",
            sLabel: "GitHub sync check is stale — " +
                "re-verify to refresh status",
            sClass: "step-blocker-glyph-l2-github-stale",
        },
        "zenodo-verify-stale": {
            sIcon: "⚠",
            sLabel: "Zenodo sync check is stale — " +
                "re-verify to refresh status",
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
        "arxiv-not-submitted": {
            sIcon: "—",
            sLabel: "No arXiv ID recorded — submit manuscript and " +
                "record the arXiv ID",
            sClass: "step-blocker-glyph-l2-arxiv-submit",
        },
        "arxiv-mismatch": {
            sIcon: "⚠",
            sLabel: "arXiv tarball doesn't match Overleaf push at " +
                "recorded commit",
            sClass: "step-blocker-glyph-l2-arxiv-mismatch",
        },
        "arxiv-version-stale": {
            sIcon: "⚠",
            sLabel: "arXiv has a newer version — update sArxivVersion " +
                "or re-submit",
            sClass: "step-blocker-glyph-l2-arxiv-version",
        },
    };

    var _DICT_L3_BLOCKER_GLYPHS = {
        "missing-from-manifest": {
            sIcon: "⚠",
            sLabel: "Path missing from MANIFEST.sha256 — refresh manifest",
            sClass: "step-blocker-glyph-l3-manifest",
        },
        "script-not-pinned": {
            sIcon: "⚠",
            sLabel: "Script hash drifted from MANIFEST — re-run or " +
                "refresh manifest",
            sClass: "step-blocker-glyph-l3-pin",
        },
        "nondeterminism-undeclared": {
            sIcon: "⚠",
            sLabel: "Step has unseeded RNG; declare or seed it",
            sClass: "step-blocker-glyph-l3-determinism",
        },
        "binary-not-declared": {
            sIcon: "⚠",
            sLabel: "Step invokes a binary not in listDeclaredBinaries",
            sClass: "step-blocker-glyph-l3-binary-declared",
        },
        "binary-not-captured": {
            sIcon: "⚠",
            sLabel: "Declared binary missing from environment.json — " +
                "capture SHA + version",
            sClass: "step-blocker-glyph-l3-binary-captured",
        },
        "dockerfile-not-pinned": {
            sIcon: "⚠",
            sLabel: "Dockerfile FROM line not pinned with @sha256:",
            sClass: "step-blocker-glyph-l3-workflow-dockerfile",
        },
        "dependency-lock-missing": {
            sIcon: "⚠",
            sLabel: "requirements.lock missing or unhashed",
            sClass: "step-blocker-glyph-l3-workflow-lock",
        },
        "environment-snapshot-missing": {
            sIcon: "⚠",
            sLabel: "environment.json missing or unpinned",
            sClass: "step-blocker-glyph-l3-workflow-env",
        },
        "reproduce-script-missing": {
            sIcon: "⚠",
            sLabel: "reproduce.sh missing or unpinned in MANIFEST",
            sClass: "step-blocker-glyph-l3-workflow-reproduce",
        },
        "l3-attestation-stale": {
            sIcon: "⚠",
            sLabel: "L3 attestation stale — re-run verification",
            sClass: "step-blocker-glyph-l3-workflow-attestation",
        },
        "binaries-not-declared-or-waived": {
            sIcon: "⚠",
            sLabel: "Open 'Declare standalone binaries' and waive " +
                "or declare each binary",
            sClass: "step-blocker-glyph-l3-workflow-binaries",
        },
    };

    function fsBuildL1BlockerBannerGlyph(iIndex) {
        var dictEntry = _dictWorkflowState.dictBlockersByStep[iIndex];
        if (!dictEntry) return "";
        var dictMeta = _fdictBannerGlyphMeta(dictEntry);
        if (!dictMeta) return "";
        // Section G: prefer the backend's per-criterion remediation
        // hint (Stage 3 schema field) over the static glyph label so
        // the tooltip language stays in lock-step with the gate.
        var sTooltip = dictEntry.sRemediationHint || dictMeta.sLabel;
        return '<span class="step-blocker-glyph ' + dictMeta.sClass +
            '" title="' + fnEscapeHtml(sTooltip) + '">' +
            dictMeta.sIcon + '</span>';
    }

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

    function fbBlockerBannerRendersPencil(iStepIndex) {
        // True when the step's active L1 blocker banner glyph is the
        // pencil. The step card suppresses its standalone
        // script-modified pencil in that case so each row carries
        // exactly one pencil.
        var dictEntry = _dictWorkflowState.dictBlockersByStep[
            iStepIndex];
        if (!dictEntry) return false;
        var dictMeta = _fdictBannerGlyphMeta(dictEntry);
        return Boolean(dictMeta) && dictMeta.sIcon === "✎";
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
        "not-started": "not started",
        "none": "no requirements met",
        "partial": "partially met",
        "attained": "attained",
        "unknown": "sync state unknown — refresh remote status",
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
        // cell renders the hollow grey "unknown" — never a fake
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
        if (dictCell) {
            listParts.push(dictCell.iSatisfied + " of " +
                dictCell.iTotal + " requirements met");
        }
        return _flistAppendLevelTooltipContext(
            listParts, iStepIndex, iLevel, sState).join("\n");
    }

    function _flistAppendLevelTooltipContext(
        listParts, iStepIndex, iLevel, sState
    ) {
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
        // is currently working toward. 4 when all three are attained.
        for (var iLevel = 1; iLevel <= 3; iLevel++) {
            if (fsLevelCellState(iStepIndex, iLevel) !== "attained") {
                return iLevel;
            }
        }
        return 4;
    }

    function fdictRegressionWarning(iStepIndex) {
        // Consolidated regression/timing warning for the regression
        // column. Per-step entries arrive precomputed in
        // ``dictStepLevelWarnings`` and render verbatim; the workflow
        // row falls back to the cell-level regression flag when the
        // poll carries no "-1" entry.
        var dictEntry = (_dictWorkflowState.dictStepLevelWarnings ||
            {})[String(iStepIndex)];
        if (!dictEntry && iStepIndex < 0) {
            dictEntry = _fdictDeriveWorkflowScopeWarning();
        }
        if (!dictEntry || !dictEntry.sWarningSeverity) return null;
        return dictEntry;
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

    function fnToggleWorkflowRowExpand() {
        // The workflow header row expands like a step row; -1 keys
        // its expansion in the shared Set (mutated in place — the
        // render context holds the Set by reference).
        if (_dictUiState.setExpandedSteps.has(-1)) {
            _dictUiState.setExpandedSteps.delete(-1);
        } else {
            _dictUiState.setExpandedSteps.add(-1);
        }
        fnRenderStepList();
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
        var listFileKeys = ["saDataFiles", "saPlotFiles", "saOutputFiles"];
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
            "saTestCommands", "saDataFiles", "saPlotFiles",
            "saDependencies", "saSetupCommands", "saCommands",
            "saOutputFiles"];
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
        // every file family that fsRenderStepItem renders a git
        // badge for: data, plot, step scripts, and test standards.
        var step = _dictWorkflowState.dictWorkflow.listSteps[iStep];
        if (!step) return;
        var listFileKeys = ["saDataFiles", "saPlotFiles",
            "saStepScripts", "saTestStandards"];
        listFileKeys.forEach(function (sKey) {
            (step[sKey] || []).forEach(function (sFile) {
                if (sFile) _dictStepIndexByFilePath[sFile] = iStep;
            });
        });
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

    function _fsClassifyVerificationSignal(sState) {
        if (sState === "passed") return "passed";
        if (sState === "failed" || sState === "error") return "failed";
        return "untested";
    }

    function fnSetVerificationUserName(sName) {
        _dictSessionState.sUserName = sName || "User";
    }

    function fsComputeStepDotState(step, iIndex) {
        var dictVerify = fdictGetVerification(step);
        var bInteractive = step.bInteractive === true;
        var bPlotOnly = (step.saDataCommands || []).length === 0;
        var listModified = dictVerify.listModifiedFiles || [];
        var bDirty = listModified.length > 0 ||
            fbAnyUpstreamModified(iIndex) ||
            _dictWorkflowState.dictScriptModified[iIndex] === "modified";
        var bHasData = PipeleyenTestManager.fsetGetStepsWithData().has(iIndex) ||
            !!_dictWorkflowState.dictOutputMtimes[String(iIndex)];
        if (!bHasData) return "";

        var listSignals = [
            _fsClassifyVerificationSignal(dictVerify.sUser)];
        if (!bInteractive && !bPlotOnly) {
            listSignals.push(_fsClassifyVerificationSignal(
                fsEffectiveTestState(step)));
        }
        var sDeps = fsComputeDepsState(iIndex);
        if (sDeps !== "none") {
            listSignals.push(_fsClassifyVerificationSignal(sDeps));
        }

        var bL1Blocked = !!_dictWorkflowState
            .dictBlockersByStep[iIndex];
        return _fsDotStateFromSignals(listSignals, bDirty, bL1Blocked);
    }

    function _fsDotStateFromSignals(listSignals, bDirty, bL1Blocked) {
        var bAllPassed = listSignals.every(function (s) {
            return s === "passed";
        });
        var bAnyPassed = listSignals.some(function (s) {
            return s === "passed";
        });
        var bAnyFailed = listSignals.some(function (s) {
            return s === "failed";
        });
        if (bAllPassed && !bL1Blocked) return bDirty ? "partial" : "verified";
        if (bAllPassed && bL1Blocked) return "partial";
        if (bAnyPassed) return "partial";
        // Nothing failed and nothing passed: the signals are merely
        // untested/pending — work not yet done is orange, not red.
        if (!bAnyFailed) return "partial";
        return "fail";
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
        var dictData = dictStep.dictDataFileCategories || {};
        return dictData[sFilePath] || "archive";
    }

    function fnBindStepEvents() {
        if (_dictWorkflowState.bDelegatedEventsInitialized) return;
        _dictWorkflowState.bDelegatedEventsInitialized = true;
        var elList = document.getElementById("listSteps");
        PipeleyenEventBindings.fnSetupDelegatedEvents(elList);
    }

    async function fnTogglePlotOnly(iStep, bPlotOnly) {
        _dictWorkflowState.dictWorkflow.listSteps[iStep].bPlotOnly = bPlotOnly;
        try {
            await VaibifyApi.fdictPut(
                "/api/steps/" + _dictSessionState.sContainerId + "/" + iStep,
                {bPlotOnly: bPlotOnly}
            );
        } catch (error) {
            fnShowToast("Save failed", "error");
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
        try {
            await VaibifyApi.fdictPut(
                "/api/steps/" + _dictSessionState.sContainerId + "/" + iStep,
                dictUpdate);
        } catch (error) {
            fnShowToast("Save failed", "error");
        }
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
        try {
            await VaibifyApi.fdictPut(
                "/api/steps/" + _dictSessionState.sContainerId + "/" + iStep,
                {dictVerification: dictVerify}
            );
        } catch (error) {
            fnShowToast("Save failed", "error");
        }
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

    function fnMoveDetailToStep(dictDrag, iTargetStep) {
        var iSource = dictDrag.iStep;
        var sArray = dictDrag.sArray;
        var sValue = _dictWorkflowState.dictWorkflow.listSteps[iSource][sArray].splice(
            dictDrag.iIdx, 1
        )[0];
        if (!_dictWorkflowState.dictWorkflow.listSteps[iTargetStep][sArray]) {
            _dictWorkflowState.dictWorkflow.listSteps[iTargetStep][sArray] = [];
        }
        _dictWorkflowState.dictWorkflow.listSteps[iTargetStep][sArray].unshift(sValue);
        fnPushUndo({
            sAction: "move",
            iStep: iSource,
            sArray: sArray,
            iIdx: dictDrag.iIdx,
            iTargetStep: iTargetStep,
            iTargetIdx: 0,
            sValue: sValue,
        });
        return sArray;
    }

    function fnHandleDetailDrop(sDetailData, iTargetStep) {
        var dictDrag = JSON.parse(sDetailData);
        if (dictDrag.iStep === iTargetStep) return;
        fnShowConfirmModal(
            "Move Item",
            "Moving a command may break dependencies " +
            "in later steps.\n\nProceed?",
            function () {
                _fnExecuteDetailDrop(dictDrag, iTargetStep);
            }
        );
    }

    async function _fnExecuteDetailDrop(dictDrag, iTargetStep) {
        var sArray = fnMoveDetailToStep(dictDrag, iTargetStep);
        await fnSaveStepArray(dictDrag.iStep, sArray);
        await fnSaveStepArray(iTargetStep, sArray);
        _dictUiState.setExpandedSteps.add(iTargetStep);
        fnRenderStepListSync();
        fnHighlightItem(iTargetStep, sArray, 0);
        fnShowToast(
            "Moved to " + _dictWorkflowState.dictWorkflow.listSteps[iTargetStep].sName,
            "success"
        );
        fnShowToast(
            "Modifying pipeline. Ensure that all subsequent " +
            "steps properly reference the new pipeline.",
            "warning"
        );
    }

    function fnHighlightItem(iStep, sArray, iIdx) {
        var elItem = document.querySelector(
            '.detail-item[data-step="' + iStep +
            '"][data-array="' + sArray +
            '"][data-idx="' + iIdx + '"]'
        );
        if (elItem) {
            elItem.classList.add("highlight");
            setTimeout(function () {
                elItem.classList.remove("highlight");
            }, 2000);
        }
    }

    function fnAddNewItem(iStep, sArrayKey) {
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
        } else if (dictAction.sAction === "move") {
            var sValue = _dictWorkflowState.dictWorkflow.listSteps[dictAction.iTargetStep][
                dictAction.sArray
            ].splice(dictAction.iTargetIdx, 1)[0];
            if (!_dictWorkflowState.dictWorkflow.listSteps[dictAction.iStep][dictAction.sArray]) {
                _dictWorkflowState.dictWorkflow.listSteps[dictAction.iStep][dictAction.sArray] = [];
            }
            _dictWorkflowState.dictWorkflow.listSteps[dictAction.iStep][dictAction.sArray]
                .splice(dictAction.iIdx, 0, sValue);
            await fnSaveStepArray(dictAction.iStep, dictAction.sArray);
            await fnSaveStepArray(
                dictAction.iTargetStep, dictAction.sArray
            );
        }
        fnRenderStepList();
        fnShowToast("Undone", "success");
    }

    async function fnSaveStepArray(iStep, sArray, bScanDeps) {
        var dictUpdate = {};
        dictUpdate[sArray] = _dictWorkflowState.dictWorkflow.listSteps[iStep][sArray];
        try {
            await VaibifyApi.fdictPut(
                "/api/steps/" + _dictSessionState.sContainerId + "/" + iStep,
                dictUpdate);
        } catch (error) {
            fnShowToast("Save failed", "error");
        }
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
        try {
            await VaibifyApi.fdictPut(
                "/api/steps/" + _dictSessionState.sContainerId + "/" + iIndex,
                {bRunEnabled: bRunEnabled}
            );
            _dictWorkflowState.dictWorkflow.listSteps[iIndex].bRunEnabled = bRunEnabled;
        } catch (error) {
            fnShowToast("Failed to update step", "error");
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

    function _fnRenderDagWithZoom(sSvgText, dScale) {
        var elViewport = document.getElementById("viewportA");
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
            var elViewport = document.getElementById("viewportA");
            elViewport.innerHTML =
                '<pre class="pipeline-output">' +
                fnEscapeHtml(sContent) + '</pre>';
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
                "workflow.json error: " +
                dictStatus.sWorkflowReloadError +
                ". Showing last good state.",
                "warning");
        }
        if (dictStatus.bWorkflowReloaded && dictStatus.dictWorkflow) {
            _fnApplyOutOfBandWorkflowReload(dictStatus.dictWorkflow);
        }
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
        var elViewport = document.getElementById("viewportA");
        elViewport.innerHTML =
            '<span class="placeholder output-missing-message">' +
            'Output not available. Run the step to generate.</span>';
    }

    function fnShowBinaryNotViewable() {
        var elViewport = document.getElementById("viewportA");
        elViewport.innerHTML =
            '<span class="placeholder">' +
            'File cannot be viewed.</span>';
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
        fnToggleWorkflowRowExpand: fnToggleWorkflowRowExpand,
        fnTogglePlotOnly: fnTogglePlotOnly,
        fnShowContextMenu: fnShowContextMenu,
        fnHideContextMenu: fnHideContextMenu,
        fnHandleDetailDrop: fnHandleDetailDrop,
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
    var sName = PipeleyenContainerManager.fsGetSelectedContainerName();
    if (!sName) return;
    try {
        fetch(
            "/api/registry/" + encodeURIComponent(sName) + "/release",
            {method: "POST", keepalive: true},
        );
    } catch (error) {
        /* best-effort: the hub's shutdown hook will catch it later */
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
