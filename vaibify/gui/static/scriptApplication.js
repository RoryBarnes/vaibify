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
            iFileCheckTimer: null,
            bFileCheckInProgress: false,
            iInflightRequests: 0,
            abortControllerFileCheck: null,
            bDelegatedEventsInitialized: false,
            bWasVaibified: false,
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
        sContextFilePath: "",
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
        listLeftTabs: ["steps", "files", "logs"],
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
            if (dictEvent.bActionsDropped) {
                fnShowToast(
                    "Pipeline connection closed (code "
                    + dictEvent.iCode +
                    "). Reselect the workflow.", "error");
            }
        });
        VaibifyWebSocket.fnOnEvent("_wsError", function (dictEvent) {
            if (dictEvent.bActionsDropped) {
                fnShowToast(
                    "Pipeline connection error. "
                    + "Reselect the workflow.", "error");
            }
        });
    }

    function fnRegisterPollingHandlers() {
        VaibifyPolling.fnSetPipelineStateHandler(
            PipeleyenPipelineRunner.fnHandlePipelinePollResult);
        VaibifyPolling.fnSetFileStatusHandler(
            fnProcessFileStatusResponse);
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
        PipeleyenEventBindings.fnBindRefreshWorkflow();
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
        PipeleyenTestManager.fnResetState();
        PipeleyenPipelineRunner.fnResetState();
        VaibifyOverleafMirror.fnResetState();
        VaibifyPolling.fnStopPipelinePolling();
        VaibifyPolling.fnStopFilePolling();
        PipeleyenReposPanel.fnTeardown();
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
        fnRenderStepList();
        fnPollAllStepFiles();
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
    }

    async function fnEnterNoWorkflow(sId) {
        try {
            await VaibifyApi.fdictPostRaw("/api/connect/" + sId);
            _dictSessionState.sContainerId = sId;
            _dictWorkflowState.dictWorkflow = null;
            _dictWorkflowState.sWorkflowPath = null;
            _dictSessionState.dictDashboardMode = DICT_MODE_NO_WORKFLOW;
            _dictWorkflowState.dictStepStatus = {};
            document.getElementById("activeContainerName").textContent =
                PipeleyenContainerManager.fsGetSelectedContainerName() || "";
            document.getElementById("activeWorkflowName").textContent =
                "None";
            document.title = PipeleyenContainerManager.fsGetSelectedContainerName() || "Vaibify";
            fnShowMainLayout();
            PipeleyenTerminal.fnEnsureTab();
            await PipeleyenReposPanel.fnInit(sId);
        } catch (error) {
            fnShowToast(
                fsSanitizeErrorForUser(error.message), "error"
            );
        }
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
        document.body.classList.remove("all-verified");
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
        };
    }

    var _bRenderScheduled = false;

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

    function _fnRenderStepListImmediate() {
        var elList = document.getElementById("listSteps");
        if (!_dictWorkflowState.dictWorkflow || !_dictWorkflowState.dictWorkflow.listSteps) {
            elList.innerHTML = "";
            return;
        }
        var dictVars = fdictBuildClientVariables();
        var dictContext = fdictBuildRenderContext();
        var sHtml = "";
        var bPreviousInteractive = null;
        _dictWorkflowState.dictWorkflow.listSteps.forEach(function (step, iIndex) {
            var bInteractive = step.bInteractive === true;
            if (bInteractive !== bPreviousInteractive) {
                sHtml += fsRenderStepTypeBanner(bInteractive);
                bPreviousInteractive = bInteractive;
            }
            sHtml += VaibifyStepRenderer.fsRenderStepItem(
                step, iIndex, dictVars, dictContext);
        });
        elList.innerHTML = sHtml;
        fnApplyTimestampVisibility();
        fnBindStepEvents();
        fnUpdateHighlightState();
        PipeleyenFileOps.fnScheduleFileExistenceCheck(
            _dictWorkflowState);
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
        _dictWorkflowState.dictWorkflow.listSteps.forEach(
            function (step, iStep) {
                PipeleyenFileOps.fnCheckStepDataFiles(
                    step, iStep, _dictWorkflowState);
            });
    }

    function fbStepRequiresUnitTests(dictStep) {
        if (dictStep.bInteractive) return false;
        if ((dictStep.saDataCommands || []).length === 0) return false;
        return true;
    }

    function fbAllVerificationComplete(dictStep, iStep) {
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
        var bInteractive = step.bInteractive === true;
        var sPrefix = bInteractive ? "I" : "A";
        var iCount = 0;
        for (var i = 0; i <= iIndex; i++) {
            var bSameType = listSteps[i].bInteractive === bInteractive;
            if (bSameType) iCount++;
        }
        return sPrefix + String(iCount).padStart(2, "0");
    }

    function fsBuildWarningBadge(step, iIndex) {
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
        if (listWarnings.length === 0) return "";
        var sTooltip = fnEscapeHtml(listWarnings.join("\n"));
        return '<span class="data-modified-badge" ' +
            'title="' + sTooltip + '">&#9888;</span>';
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
        };
        return dictLabels[sState] || "Untested";
    }

    function fsVerificationStateIcon(sState) {
        var dictIcons = {
            passed: "\u2713", failed: "\u2717",
            untested: "\u2014", error: "\u2717",
            stale: "\u26A0",
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

        var bAllPassed = listSignals.every(function (s) {
            return s === "passed";
        });
        var bAnyPassed = listSignals.some(function (s) {
            return s === "passed";
        });

        if (bAllPassed) return bDirty ? "partial" : "verified";
        if (bAnyPassed) return "partial";
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
        var iCurrent = listStates.indexOf(dictVerify.sUser);
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

    function fbIsWorkflowFullyVerified() {
        if (!_dictWorkflowState.dictWorkflow || !_dictWorkflowState.dictWorkflow.listSteps) return false;
        var listSteps = _dictWorkflowState.dictWorkflow.listSteps;
        if (listSteps.length === 0) return false;
        /* Every step counts toward verification — bRunEnabled is run
           scope (whether to include in the next run), not verification
           scope. Disabling a step from the run list does not promote
           the workflow toward Vaibified. */
        for (var i = 0; i < listSteps.length; i++) {
            if (!fbAllVerificationComplete(listSteps[i], i)) return false;
        }
        return true;
    }

    /* fnCheckVaibified inlined to fnUpdateHighlightState */

    function fnUpdateHighlightState() {
        var bVerified = fbIsWorkflowFullyVerified();
        if (bVerified) {
            document.body.classList.add("all-verified");
            PipeleyenTerminal.fnUpdateCursorColor("#b39ddb");
            fnTriggerBloomIfNeeded(bVerified);
        } else {
            document.body.classList.remove("all-verified");
            PipeleyenTerminal.fnUpdateCursorColor("#13aed5");
        }
        fnRecolorVisibleDagEdges();
        _dictWorkflowState.bWasVaibified = bVerified;
    }

    function fnRecolorVisibleDagEdges() {
        document.querySelectorAll(".dag-container svg").forEach(
            function (elSvg) { fnRecolorDagEdges(elSvg); }
        );
    }

    function fnTriggerBloomIfNeeded(bVerified) {
        if (bVerified && !_dictWorkflowState.bWasVaibified) {
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

    function fnShowFileContextMenu(
        iX, iY, sFilePath, sWorkdir, iStepIndex
    ) {
        fnHideContextMenu();
        _dictUiState.sContextFilePath = sFilePath;
        var el = document.getElementById("fileContextMenu");
        el.style.left = iX + "px";
        el.style.top = iY + "px";
        el.classList.add("active");
    }

    function fnHideContextMenu() {
        document.getElementById("contextMenu")
            .classList.remove("active");
        document.getElementById("fileContextMenu")
            .classList.remove("active");
    }

    async function fnHandleFileContextAction(sAction) {
        if (sAction === "pullToHost") {
            PipeleyenFilePull.fnPromptPullToHost(
                _dictUiState.sContextFilePath);
            return;
        }
        if (sAction === "copyPath") {
            PipeleyenFileOps.fnCopyToClipboard(
                _dictUiState.sContextFilePath);
            return;
        }
        if (sAction === "addToGit") {
            fnShowToast("Adding to Git...", "success");
            try {
                var dictResult = await VaibifyApi.fdictPost(
                    "/api/github/" +
                    _dictSessionState.sContainerId + "/add-file",
                    {sFilePath: _dictUiState.sContextFilePath}
                );
                if (dictResult.bSuccess) {
                    fnShowToast("Added to Git", "success");
                    fnRenderStepList();
                } else {
                    VaibifySyncManager.fnShowSyncError(
                        dictResult, "GitHub");
                }
            } catch (error) {
                fnShowToast(
                    fsSanitizeErrorForUser(error.message),
                    "error");
            }
            return;
        }
        if (sAction === "archiveToZenodo") {
            fnShowToast("Archiving to Zenodo...", "success");
            try {
                var dictZenodoResult = await VaibifyApi.fdictPost(
                    "/api/zenodo/" +
                    _dictSessionState.sContainerId + "/archive",
                    {listFilePaths: [
                        _dictUiState.sContextFilePath]}
                );
                if (dictZenodoResult.bSuccess) {
                    fnShowToast(
                        "Archived to Zenodo", "success");
                    fnRenderStepList();
                } else {
                    VaibifySyncManager.fnShowSyncError(
                        dictZenodoResult, "Zenodo");
                }
            } catch (error) {
                fnShowToast(
                    fsSanitizeErrorForUser(error.message),
                    "error");
            }
        }
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

    function fnShowToast(sMessage, sType) {
        var el = document.createElement("div");
        el.className = "toast " + (sType || "");
        el.innerHTML = fnEscapeHtml(sMessage) +
            '<button class="toast-close">&times;</button>';
        el.querySelector(".toast-close").addEventListener(
            "click", function () { el.remove(); }
        );
        if (sType !== "error") {
            var iTimeout = sType === "warning" ? 8000 : 4000;
            setTimeout(function () { el.remove(); }, iTimeout);
        }
        document.getElementById("toastContainer").appendChild(el);
    }

    var fnEscapeHtml = VaibifyUtilities.fnEscapeHtml;

    /* --- Public API --- */

    return {
        fnInitialize: fnInitialize,
        fnShowToast: fnShowToast,
        fnRenderStepList: fnRenderStepList,
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
        fnTogglePlotOnly: fnTogglePlotOnly,
        fnShowContextMenu: fnShowContextMenu,
        fnShowFileContextMenu: fnShowFileContextMenu,
        fnHideContextMenu: fnHideContextMenu,
        fnHandleDetailDrop: fnHandleDetailDrop,
        fnReorderStep: fnReorderStep,
        fnHandleContextAction: fnHandleContextAction,
        fnHandleFileContextAction: fnHandleFileContextAction,
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
        fbAllVerificationComplete: fbAllVerificationComplete,
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
