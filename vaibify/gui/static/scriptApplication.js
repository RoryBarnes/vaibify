/* Vaibify — Main application logic */

const PipeleyenApp = (function () {
    "use strict";

    function fnCopyToClipboard(sText) {
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(sText).then(function () {
                fnShowToast("Copied to clipboard", "success");
            }).catch(function () {
                fnCopyToClipboardFallback(sText);
            });
        } else {
            fnCopyToClipboardFallback(sText);
        }
    }

    function fnCopyToClipboardFallback(sText) {
        var elTextarea = document.createElement("textarea");
        elTextarea.value = sText;
        elTextarea.style.position = "fixed";
        elTextarea.style.opacity = "0";
        document.body.appendChild(elTextarea);
        elTextarea.select();
        try {
            document.execCommand("copy");
            fnShowToast("Copied to clipboard", "success");
        } catch (e) {
            fnShowToast("Copy failed", "error");
        }
        document.body.removeChild(elTextarea);
    }

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
            dictDiscoveredOutputs: {},
            dictUserVerifiedAt: {},
            dictFileExistenceCache: {},
            dictFileModTimes: {},
            dictOutputMtimes: {},
            dictPlotMtimes: {},
            dictPlotStandardExists: {},
            iFileCheckTimer: null,
            bFileCheckInProgress: false,
            iInflightRequests: 0,
            abortControllerFileCheck: null,
            bDelegatedEventsInitialized: false,
            bWasVaibified: false,
            listUndoStack: [],
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
        listLeftTabs: ["files", "logs"],
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
        fnLoadContainers();
        fnBindToolbarEvents();
        fnBindWorkflowPickerEvents();
        fnBindContainerLandingEvents();
        fnBindAddContainerModal();
        fnBindErrorModal();
        PipeleyenTestManager.fnBindApiConfirmModal();
        fnBindContextMenuEvents();
        fnBindLeftPanelTabs();
        fnBindResizeHandles();
        fnBindGlobalSettingsToggle();
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

    /* Container management is now in scriptContainerManager.js */

    function fnLoadContainers() {
        PipeleyenContainerManager.fnLoadContainers();
    }

    /* Directory browser is now in scriptDirectoryBrowser.js */

    function fnOpenDirectoryBrowser() {
        PipeleyenDirectoryBrowser.fnOpenDirectoryBrowser();
    }

    function fnSelectDirectory() {
        PipeleyenDirectoryBrowser.fnSelectDirectory();
    }

    function fnConnectToContainer(sId) {
        PipeleyenContainerManager.fnConnectToContainer(sId);
    }

    function _fnResetWorkflowState() {
        var dictDefaults = _fdictDefaultWorkflowState();
        for (var sKey in dictDefaults) {
            _dictWorkflowState[sKey] = dictDefaults[sKey];
        }
        _fnResetUiState();
        PipeleyenTestManager.fnResetState();
        PipeleyenPipelineRunner.fnResetState();
        fnStopPipelinePolling();
        fnStopFileChangePolling();
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
        var iStepCount = (_dictWorkflowState.dictWorkflow.listSteps || []).length;
        if (iStepCount > 500) {
            fnShowToast(
                "This workflow has " + iStepCount + " steps. " +
                "Large workflows may use significant memory. " +
                "Avoid expanding many steps simultaneously.",
                "error"
            );
        }
        var elWorkflowName = document.getElementById("activeWorkflowName");
        elWorkflowName.textContent = sWorkflowName || "";
        document.title = (PipeleyenContainerManager.fsGetSelectedContainerName() || "Vaibify") +
            (sWorkflowName ? ": " + sWorkflowName : "");
        fnShowMainLayout();
        fnRenderStepList();
        fnUpdateHighlightState();
        fnPollAllStepFiles();
        fnStartFileChangePolling();
        PipeleyenTerminal.fnCreateTab();
        PipeleyenPipelineRunner.fnRecoverPipelineState(sId);
    }

    function fnSelectWorkflow(sId, sWorkflowPathArg, sWorkflowName) {
        VaibifyWorkflowManager.fnSelectWorkflow(
            sId, sWorkflowPathArg, sWorkflowName);
    }

    async function fnEnterNoWorkflow(sId) {
        try {
            await VaibifyApi.fdictPostRaw("/api/connect/" + sId);
            _dictSessionState.sContainerId = sId;
            _dictWorkflowState.dictWorkflow = null;
            _dictWorkflowState.sWorkflowPath = null;
            _dictSessionState.dictDashboardMode = DICT_MODE_NO_WORKFLOW;
            _dictWorkflowState.dictStepStatus = {};
            var elWorkflowName = document.getElementById(
                "activeWorkflowName"
            );
            elWorkflowName.textContent = "No Workflow";
            document.title = PipeleyenContainerManager.fsGetSelectedContainerName() || "Vaibify";
            fnShowMainLayout();
            PipeleyenTerminal.fnCreateTab();
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
        document.getElementById("containerLanding").style.display = "flex";
        document.getElementById("workflowPicker").style.display = "none";
        document.getElementById("mainLayout").classList.remove("active");
        _dictSessionState.dictDashboardMode = null;
        document.title = "Vaibify";
    }

    function fnShowWorkflowPicker(sContainerName) {
        document.getElementById("containerLanding").style.display = "none";
        document.getElementById("workflowPicker").style.display = "flex";
        document.getElementById("mainLayout").classList.remove("active");
        document.title = sContainerName || "Vaibify";
    }

    function fnShowMainLayout() {
        document.getElementById("containerLanding").style.display = "none";
        document.getElementById("workflowPicker").style.display = "none";
        document.getElementById("mainLayout").classList.add("active");
        fnApplyDashboardMode();
    }

    function _fnCancelAllTimers() {
        VaibifyWebSocket.fnDisconnect();
        fnStopPipelinePolling();
        fnStopFileChangePolling();
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
        fnLoadContainers();
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

    /* --- Left Panel Tabs --- */

    function fnBindLeftPanelTabs() {
        document.querySelectorAll(".left-tab").forEach(function (el) {
            el.addEventListener("click", function () {
                document.querySelectorAll(".left-tab").forEach(function (t) {
                    t.classList.remove("active");
                });
                el.classList.add("active");
                var sPanel = el.dataset.panel;
                var bWorkflowMode = _dictSessionState.dictDashboardMode &&
                    _dictSessionState.dictDashboardMode.sMode === "workflow";
                if (bWorkflowMode) {
                    document.getElementById("panelSteps")
                        .classList.add("active");
                    document.getElementById("panelFiles")
                        .classList.toggle("active",
                            sPanel === "files");
                    document.getElementById("panelLogs")
                        .classList.toggle("active",
                            sPanel === "logs");
                } else {
                    document.getElementById("panelSteps")
                        .classList.toggle("active",
                            sPanel === "steps");
                    document.getElementById("panelFiles")
                        .classList.toggle("active",
                            sPanel === "files");
                    document.getElementById("panelLogs")
                        .classList.toggle("active",
                            sPanel === "logs");
                }
                if (sPanel === "files") {
                    PipeleyenFiles.fnLoadDirectory("/workspace");
                } else if (sPanel === "logs") {
                    fnLoadLogs();
                }
            });
        });
    }

    function fsGetWorkflowDirectory() {
        if (!_dictWorkflowState.sWorkflowPath) return "/workspace";
        var iLastSlash = _dictWorkflowState.sWorkflowPath.lastIndexOf("/");
        return iLastSlash > 0 ? _dictWorkflowState.sWorkflowPath.substring(0, iLastSlash) : "/workspace";
    }

    /* --- Global Settings --- */

    function fnBindGlobalSettingsToggle() {
        document.getElementById("btnGlobalSettings").addEventListener(
            "click", function () {
                var el = document.getElementById("globalSettingsPanel");
                var bExpanded = el.classList.toggle("expanded");
                if (bExpanded) fnRenderGlobalSettings();
            }
        );
    }

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
            (_dictUiState.bShowTimestamps ? " checked" : "") + '>');
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
        var dictUpdates = {
            sPlotDirectory: document.getElementById("gsPlotDirectory").value,
            sFigureType: document.getElementById("gsFigureType").value,
            iNumberOfCores: parseInt(
                document.getElementById("gsNumberOfCores").value
            ),
            fTolerance: Math.pow(10, iExp),
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
            dictOutputMtimes: _dictWorkflowState.dictOutputMtimes,
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
            fsDepLabelColorClass: fsDepLabelColorClass,
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
        fnScheduleFileExistenceCheck();
    }

    function fsRenderStepTypeBanner(bInteractive) {
        var sLabel = bInteractive ?
            "Interactive Steps" : "Automatic Steps";
        return '<div class="step-type-banner">' + sLabel + '</div>';
    }

    var I_MAX_FILE_CACHE_ENTRIES = 500;

    function fnSetFileExistenceCache(sKey, bValue) {
        if (Object.keys(_dictWorkflowState.dictFileExistenceCache).length >=
            I_MAX_FILE_CACHE_ENTRIES) {
            _dictWorkflowState.dictFileExistenceCache = {};
        }
        _dictWorkflowState.dictFileExistenceCache[sKey] = bValue;
    }

    function fnScheduleFileExistenceCheck() {
        if (_dictWorkflowState.iFileCheckTimer) return;
        _dictWorkflowState.iFileCheckTimer = setTimeout(function () {
            _dictWorkflowState.iFileCheckTimer = null;
            _dictWorkflowState.bFileCheckInProgress = false;
            _dictWorkflowState.iInflightRequests = 0;
            var iOutputCount = document.querySelectorAll(
                ".detail-item.output").length;
            fnCheckOutputFileExistence();
            fnCheckDataFileExistence();
            if (_dictWorkflowState.iInflightRequests === 0) {
                _dictWorkflowState.bFileCheckInProgress = false;
            } else {
                setTimeout(function () {
                    _dictWorkflowState.bFileCheckInProgress = false;
                }, 10000);
            }
        }, 200);
    }

    function fnFileCheckComplete() {
        _dictWorkflowState.iInflightRequests--;
        if (_dictWorkflowState.iInflightRequests <= 0) {
            _dictWorkflowState.bFileCheckInProgress = false;
        }
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
        if (!_dictSessionState.sContainerId || !_dictWorkflowState.dictWorkflow) return;
        _dictWorkflowState.dictWorkflow.listSteps.forEach(function (step, iStep) {
            fnCheckStepDataFiles(step, iStep);
        });
    }

    function fnCheckDataFileExistence() {
        if (!_dictSessionState.sContainerId || !_dictWorkflowState.dictWorkflow) return;
        _dictWorkflowState.dictWorkflow.listSteps.forEach(function (step, iStep) {
            if (!_dictUiState.setExpandedSteps.has(iStep)) return;
            fnCheckStepDataFiles(step, iStep);
        });
    }

    function fnCheckStepDataFiles(step, iStep) {
        if (PipeleyenTestManager.fsetGetStepsWithData().has(iStep)) return;
        var listNecessary = flistNecessaryDataFiles(step, iStep);
        if (listNecessary.length === 0) return;
        var iPresent = 0;
        var iTotal = listNecessary.length;
        listNecessary.forEach(function (sFile) {
            var sDir = step.sDirectory || "";
            var sCacheKey = iStep + ":" + sFile;
            if (_dictWorkflowState.dictFileExistenceCache[sCacheKey]) {
                iPresent++;
                if (iPresent >= iTotal) {
                    PipeleyenTestManager.fsetGetStepsWithData().add(iStep);
                    fnUpdateGenerateButton(iStep);
                }
                return;
            }
            var sUrl = "/api/figure/" + _dictSessionState.sContainerId +
                "/" + sFile + "?sWorkdir=" +
                encodeURIComponent(sDir);
            _dictWorkflowState.iInflightRequests++;
            VaibifyApi.fbHead(sUrl).then(
                function (bExists) {
                    if (bExists) {
                        fnSetFileExistenceCache(sCacheKey, true);
                        iPresent++;
                        if (iPresent >= iTotal) {
                            PipeleyenTestManager.fsetGetStepsWithData().add(iStep);
                            fnUpdateGenerateButton(iStep);
                        }
                    }
                    fnFileCheckComplete();
                }
            ).catch(function () { fnFileCheckComplete(); });
        });
    }

    function flistNecessaryDataFiles(step, iStep) {
        var listData = step.saDataFiles || [];
        return listData.filter(function (sFile) {
            return fsGetFileCategory(
                iStep, sFile, "saDataFiles"
            ) === "archive";
        });
    }

    function _fnCheckSingleOutputFile(
        el, dictDataCounts, dictDataPresent, signalFileCheck
    ) {
        var elText = el.querySelector(".detail-text");
        if (!elText || elText.classList.contains("file-invalid")) {
            return;
        }
        var iStep = parseInt(el.dataset.step);
        var sArray = el.dataset.array;
        var sResolved = el.dataset.resolved;
        var sWorkdir = el.dataset.workdir || "";
        var sCacheKey = iStep + ":" + sResolved + ":" + sWorkdir;
        var sRaw = el.dataset.raw || "";
        var bNecessaryData = sArray === "saDataFiles" &&
            fsGetFileCategory(iStep, sRaw, sArray) === "archive";
        if (bNecessaryData) {
            dictDataCounts[iStep] =
                (dictDataCounts[iStep] || 0) + 1;
        }
        if (_dictWorkflowState.dictFileExistenceCache[sCacheKey] === true) {
            fnUpdateFileStatus(el, true);
            fnTrackDataPresence(
                iStep, bNecessaryData,
                dictDataCounts, dictDataPresent
            );
            return;
        }
        if (_dictWorkflowState.dictFileExistenceCache[sCacheKey] === false) {
            fnUpdateFileStatus(el, false);
            return;
        }
        var sUrl = "/api/figure/" + _dictSessionState.sContainerId + "/" + sResolved;
        if (sWorkdir) {
            sUrl += "?sWorkdir=" + encodeURIComponent(sWorkdir);
        }
        _dictWorkflowState.iInflightRequests++;
        VaibifyApi.fbHead(sUrl, {signal: signalFileCheck})
            .then(function (bExists) {
                if (bExists) {
                    fnSetFileExistenceCache(sCacheKey, true);
                    fnUpdateFileStatus(el, true);
                    fnTrackDataPresence(
                        iStep, bNecessaryData,
                        dictDataCounts, dictDataPresent
                    );
                } else {
                    fnSetFileExistenceCache(sCacheKey, false);
                    fnUpdateFileStatus(el, false);
                }
                fnFileCheckComplete();
            }).catch(function (err) {
                if (err.name === "AbortError") return;
                fnSetFileExistenceCache(sCacheKey, false);
                fnUpdateFileStatus(el, false);
                fnFileCheckComplete();
            });
    }

    function fnCheckOutputFileExistence() {
        if (!_dictSessionState.sContainerId) return;
        if (_dictWorkflowState.abortControllerFileCheck) {
            _dictWorkflowState.abortControllerFileCheck.abort();
        }
        _dictWorkflowState.abortControllerFileCheck = new AbortController();
        var signalFileCheck = _dictWorkflowState.abortControllerFileCheck.signal;
        var dictDataCounts = {};
        var dictDataPresent = {};
        document.querySelectorAll(
            '.detail-item.output'
        ).forEach(function (el) {
            _fnCheckSingleOutputFile(
                el, dictDataCounts, dictDataPresent, signalFileCheck
            );
        });
    }

    function fnTrackDataPresence(
        iStep, bNecessaryData, dictCounts, dictPresent
    ) {
        if (!bNecessaryData) return;
        dictPresent[iStep] = (dictPresent[iStep] || 0) + 1;
        if (dictPresent[iStep] >= (dictCounts[iStep] || 0)) {
            PipeleyenTestManager.fsetGetStepsWithData().add(iStep);
            fnUpdateGenerateButton(iStep);
        }
    }

    function fnUpdateGenerateButton(iStep) {
        var elBtn = document.querySelector(
            '.btn-generate-test[data-step="' + iStep + '"]'
        );
        if (elBtn) {
            elBtn.disabled = false;
        }
    }

    var LIST_FILE_STATUS_CLASSES = [
        "file-necessary-red", "file-necessary-orange",
        "file-necessary-valid", "file-supplementary-valid",
        "file-supplementary-missing", "file-binary",
        "file-pending",
    ];

    function fnRemoveAllFileStatusClasses(elText) {
        LIST_FILE_STATUS_CLASSES.forEach(function (sCls) {
            elText.classList.remove(sCls);
        });
    }

    function fnUpdateFileStatus(el, bExists) {
        var elText = el.querySelector(".detail-text");
        if (!elText) return;
        var iStep = parseInt(el.dataset.step);
        var sArrayKey = el.dataset.array;
        var sRaw = el.dataset.raw || "";
        var sResolved = el.dataset.resolved || "";
        fnRemoveAllFileStatusClasses(elText);
        var sClass = fsComputeFileStatusClass(
            iStep, sArrayKey, sRaw, sResolved, bExists
        );
        elText.classList.add(sClass);
    }

    function fsComputeFileStatusClass(
        iStep, sArrayKey, sRaw, sResolved, bExists
    ) {
        if (fbIsBinaryFile(sRaw)) return "file-binary";
        var sCategory = fsGetFileCategory(
            iStep, sRaw, sArrayKey
        );
        if (sCategory === "supporting") {
            return bExists ?
                "file-supplementary-valid" :
                "file-supplementary-missing";
        }
        return fsNecessaryFileClass(iStep, sResolved, bExists);
    }

    function fbFileInModifiedList(sResolved, listModified) {
        if (!sResolved || listModified.length === 0) return false;
        for (var i = 0; i < listModified.length; i++) {
            if (listModified[i] === sResolved) return true;
            if (listModified[i].endsWith("/" + sResolved)) {
                return true;
            }
        }
        return false;
    }

    function fsNecessaryFileClass(iStep, sResolved, bExists) {
        if (!bExists) return "file-necessary-red";
        var dictStep = _dictWorkflowState.dictWorkflow.listSteps[iStep];
        var dictVerify = fdictGetVerification(dictStep);
        var listModified = dictVerify.listModifiedFiles || [];
        if (fbFileInModifiedList(sResolved, listModified)) {
            return "file-necessary-red";
        }
        if (fbAllVerificationComplete(dictStep, iStep)) {
            return "file-necessary-valid";
        }
        return "file-necessary-orange";
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
        var sUser = dictVerify.sUser;
        var sDeps = fsComputeDepsState(iStep);
        if (sUser !== "passed" || sDeps === "failed") return false;
        if (fbStepRequiresUnitTests(dictStep)) {
            return fsEffectiveTestState(dictStep) === "passed";
        }
        return true;
    }

    function fbIsFileMissing(elText) {
        if (elText.classList.contains("file-supplementary-missing")) {
            return true;
        }
        if (!elText.classList.contains("file-necessary-red")) {
            return false;
        }
        var elItem = elText.closest(".detail-item");
        if (!elItem) return true;
        var sResolved = elItem.dataset.resolved || "";
        var sCacheKey = elItem.dataset.step + ":" +
            sResolved + ":" + (elItem.dataset.workdir || "");
        return _dictWorkflowState.dictFileExistenceCache[sCacheKey] === false;
    }

    function fsInitialFileStatusClass(iStep, sArrayKey, sRaw) {
        if (fbIsBinaryFile(sRaw)) return "file-binary";
        return "file-pending";
    }

    function fsComputeStepLabel(iIndex) {
        var listSteps = _dictWorkflowState.dictWorkflow.listSteps;
        var bInteractive = listSteps[iIndex].bInteractive === true;
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
        var listMod = (step.dictVerification || {})
            .listModifiedFiles || [];
        if (listMod.length > 0) {
            var sNames = listMod.map(function (s) {
                return s.split("/").pop();
            }).join(", ");
            listWarnings.push("Modified: " + sNames);
        }
        fnAppendTestWarning(step, iIndex, listWarnings);
        fnAppendDepsWarning(iIndex, listWarnings);
        if (listWarnings.length === 0) return "";
        var sTooltip = fnEscapeHtml(listWarnings.join("\n"));
        return '<span class="data-modified-badge" ' +
            'title="' + sTooltip + '">&#9888;</span>';
    }

    function fnAppendTestWarning(step, iIndex, listWarnings) {
        var bInteractive = step.bInteractive === true;
        var bPlotOnly = (step.saDataCommands || []).length === 0;
        if (bInteractive || bPlotOnly) return;
        var sUnit = fsEffectiveTestState(step);
        if (sUnit === "failed") {
            listWarnings.push("Unit tests failing");
        }
    }

    function fnAppendDepsWarning(iIndex, listWarnings) {
        var sDeps = fsComputeDepsState(iIndex);
        if (sDeps === "failed") {
            listWarnings.push("Dependencies failing");
        }
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

    function flistGetStepDependencies(iStep) {
        if (!_dictWorkflowState.dictWorkflow || !_dictWorkflowState.dictWorkflow.listSteps) return [];
        var step = _dictWorkflowState.dictWorkflow.listSteps[iStep];
        var setDeps = {};
        var listArrays = ["saDataCommands", "saPlotCommands",
            "saTestCommands", "saDataFiles", "saPlotFiles",
            "saDependencies"];
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
        var dictVisited = {};
        for (var i = 0; i < listDeps.length; i++) {
            if (!fbStepFullyPassing(listDeps[i], dictVisited)) {
                return "failed";
            }
        }
        return "passed";
    }

    function fsDepLabelColorClass(iDep, bPassing) {
        if (document.body.classList.contains("all-verified")) {
            return "";
        }
        if (bPassing) return "dep-status-blue";
        var depStep = _dictWorkflowState.dictWorkflow.listSteps[iDep];
        if (!depStep) return "dep-status-red";
        var sDepState = fsDepStepOverallState(depStep, iDep);
        if (sDepState === "partial") return "dep-status-orange";
        return "dep-status-red";
    }

    function fsDepStepOverallState(depStep, iDep) {
        var dictVerify = fdictGetVerification(depStep);
        var bUserPassed = dictVerify.sUser === "passed";
        var bUnitPassed = fsEffectiveTestState(depStep) === "passed";
        var sDeps = fsComputeDepsState(iDep);
        var bDepsPassed = sDeps === "none" || sDeps === "passed";
        var bAnyPassed = bUserPassed || bUnitPassed || bDepsPassed;
        var bAllPassed = bUserPassed && bUnitPassed && bDepsPassed;
        if (bAllPassed) return "passed";
        if (bAnyPassed) return "partial";
        return "failed";
    }

    function fnSetVerificationUserName(sName) {
        _dictSessionState.sUserName = sName || "User";
    }

    function fsComputeStepDotState(step, iIndex) {
        var dictVerify = fdictGetVerification(step);
        var sUser = dictVerify.sUser;
        var bInteractive = step.bInteractive === true;
        var bPlotOnly = (step.saDataCommands || []).length === 0;
        var listModified = dictVerify.listModifiedFiles || [];
        var bDirty = listModified.length > 0 ||
            fbAnyUpstreamModified(iIndex) ||
            _dictWorkflowState.dictScriptModified[iIndex] === "modified";
        var bHasData = PipeleyenTestManager.fsetGetStepsWithData().has(iIndex) ||
            !!(step.dictRunStats || {}).sLastRun ||
            !!_dictWorkflowState.dictOutputMtimes[String(iIndex)];

        if (!bHasData) return "";

        var bHasUnitTests = !bInteractive && !bPlotOnly;
        var sUnit = bHasUnitTests ?
            fsEffectiveTestState(step) : null;
        var sDeps = fsComputeDepsState(iIndex);
        var bHasDeps = sDeps !== "none";

        var bUserPassed = sUser === "passed";
        var bUnitPassed = !bHasUnitTests || sUnit === "passed";
        var bDepsPassed = !bHasDeps || sDeps === "passed";

        if (bUserPassed && bUnitPassed && bDepsPassed) {
            return bDirty ? "partial" : "verified";
        }
        if (bUserPassed || (bHasUnitTests && sUnit === "passed") ||
            (bHasDeps && sDeps === "passed")) {
            return "partial";
        }
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

    function fsFirstPlotBasename(iStepIndex) {
        return PipeleyenPlotStandards.fsFirstPlotBasename(iStepIndex);
    }

    function fbStepHasAnyStandard(iStepIndex) {
        return PipeleyenPlotStandards.fbStepHasAnyStandard(iStepIndex);
    }

    function fsGetFileCategory(iStep, sFilePath, sArrayKey) {
        var dictStep = _dictWorkflowState.dictWorkflow.listSteps[iStep];
        if (sArrayKey === "saPlotFiles") {
            var dictPlot = dictStep.dictPlotFileCategories || {};
            return dictPlot[sFilePath] || "archive";
        }
        var dictData = dictStep.dictDataFileCategories || {};
        return dictData[sFilePath] || "archive";
    }

    async function fnToggleArchiveCategory(
        iStep, sFilePath, sArrayKey
    ) {
        var dictStep = _dictWorkflowState.dictWorkflow.listSteps[iStep];
        var sDictKey = sArrayKey === "saDataFiles" ?
            "dictDataFileCategories" : "dictPlotFileCategories";
        if (!dictStep[sDictKey]) {
            dictStep[sDictKey] = {};
        }
        var sCurrentCategory = fsGetFileCategory(
            iStep, sFilePath, sArrayKey
        );
        var sNewCategory = sCurrentCategory === "archive" ?
            "supporting" : "archive";
        dictStep[sDictKey][sFilePath] = sNewCategory;
        var dictUpdate = {};
        dictUpdate[sDictKey] = dictStep[sDictKey];
        await fnSaveStepUpdate(iStep, dictUpdate);
        fnRenderStepList();
    }

    /* --- Step Event Binding (delegated) --- */

    function fnBindStepEvents() {
        if (_dictWorkflowState.bDelegatedEventsInitialized) return;
        _dictWorkflowState.bDelegatedEventsInitialized = true;
        var elList = document.getElementById("listSteps");
        fnSetupDelegatedEvents(elList);
    }

    function fnSetupDelegatedEvents(elList) {
        elList.addEventListener("click", fnHandleDelegatedClick);
        elList.addEventListener("change", fnHandleDelegatedChange);
        elList.addEventListener("contextmenu",
            fnHandleDelegatedContextMenu);
        elList.addEventListener("dragstart",
            fnHandleDelegatedDragStart);
        elList.addEventListener("dragend", fnHandleDelegatedDragEnd);
        elList.addEventListener("dragover",
            fnHandleDelegatedDragOver);
        elList.addEventListener("dragleave",
            fnHandleDelegatedDragLeave);
        elList.addEventListener("drop", fnHandleDelegatedDrop);
    }

    function _fnHandleActionDownload(event, elMatch) {
        event.stopPropagation();
        var elDetailItem = event.target.closest(".detail-item");
        if (elDetailItem) {
            fnPromptPullToHost(elDetailItem.dataset.resolved);
        }
    }

    function _fnHandleActionEdit(event, elMatch) {
        event.stopPropagation();
        var elDetailItem = event.target.closest(".detail-item");
        if (elDetailItem) {
            fnInlineEditItem(
                elDetailItem,
                parseInt(elDetailItem.dataset.step),
                elDetailItem.dataset.array,
                parseInt(elDetailItem.dataset.idx)
            );
        }
    }

    function _fnHandleActionCopy(event, elMatch) {
        event.stopPropagation();
        var elDetailItem = event.target.closest(".detail-item");
        if (elDetailItem) {
            fnCopyToClipboard(elDetailItem.dataset.resolved);
        }
    }

    function _fnHandleActionDelete(event, elMatch) {
        event.stopPropagation();
        var elDetailItem = event.target.closest(".detail-item");
        if (elDetailItem) {
            fnDeleteDetailItem(
                parseInt(elDetailItem.dataset.step),
                elDetailItem.dataset.array,
                parseInt(elDetailItem.dataset.idx)
            );
        }
    }

    function _fnHandleDiscoveredButton(event, elMatch) {
        event.stopPropagation();
        var elDiscItem = elMatch.closest(".discovered-item");
        fnAddDiscoveredOutput(
            parseInt(elDiscItem.dataset.step),
            elDiscItem.dataset.file,
            elMatch.dataset.target
        );
    }

    function _fnHandleArchiveStar(event, elMatch) {
        event.stopPropagation();
        fnToggleArchiveCategory(
            parseInt(elMatch.dataset.step),
            elMatch.dataset.file,
            elMatch.dataset.array || "saPlotFiles"
        );
    }

    function _fnHandleTestAdd(event, elMatch) {
        event.stopPropagation();
        fnAddTestItem(
            parseInt(elMatch.dataset.step),
            elMatch.dataset.testType
        );
    }

    function _fnHandleSectionAdd(event, elMatch) {
        event.stopPropagation();
        fnAddNewItem(
            parseInt(elMatch.dataset.step),
            elMatch.dataset.array
        );
    }

    function _fnHandleVerificationClickable(event, elMatch) {
        fnCycleUserVerification(
            parseInt(elMatch.dataset.step)
        );
    }

    function _fnHandleSubTestRow(event, elMatch) {
        var sSubApprover = elMatch.dataset.approver;
        var iSubStep = parseInt(elMatch.dataset.step);
        var setSubExp = fsetGetExpandedCategory(sSubApprover);
        if (setSubExp.has(iSubStep)) {
            setSubExp.delete(iSubStep);
        } else {
            setSubExp.add(iSubStep);
        }
        fnRenderStepList();
    }

    function _fnHandleVerificationExpandable(event, elMatch) {
        var sApprover = elMatch.dataset.approver;
        var iStep = parseInt(elMatch.dataset.step);
        if (sApprover === "unitTest") {
            fnToggleUnitTestExpand(iStep);
        }
    }

    function _fnHandleVerificationDeps(event, elMatch) {
        fnToggleDepsExpand(parseInt(elMatch.dataset.step));
    }

    function _fnHandleMakeStandard(event, elMatch) {
        fnStandardizeAllPlots(parseInt(elMatch.dataset.step));
    }

    function _fnHandleCompareStandard(event, elMatch) {
        fnCompareStepPlots(parseInt(elMatch.dataset.step));
    }

    function _fnHandleTestFileItem(event, elMatch) {
        PipeleyenFigureViewer.fnDisplayFileFromContainer(
            elMatch.textContent.trim()
        );
    }

    function _fnHandleTestLastRun(event, elMatch) {
        PipeleyenFigureViewer.fnDisplayFileFromContainer(
            elMatch.dataset.log
        );
    }

    function _fnHandleGenerateTest(event, elMatch) {
        fnGenerateTests(parseInt(elMatch.dataset.step));
    }

    function _fnHandleStepEdit(event, elMatch) {
        var elStepItem = event.target.closest(".step-item");
        PipeleyenStepEditor.fnOpenEditModal(
            parseInt(elStepItem.dataset.index)
        );
    }

    function _fnHandleInteractiveRun(event, elMatch) {
        fnRunInteractiveStep(parseInt(elMatch.dataset.index));
    }

    function _fnHandleInteractivePlots(event, elMatch) {
        fnRunInteractivePlots(parseInt(elMatch.dataset.index));
    }

    function _fnHandleRunTests(event, elMatch) {
        fnRunStepTests(parseInt(elMatch.dataset.step));
    }

    function _fnHandleRunCategory(event, elMatch) {
        fnRunCategoryTests(
            parseInt(elMatch.dataset.step),
            elMatch.dataset.category);
    }

    function _fnHandleRunData(event, elMatch) {
        fnRunInteractiveStep(parseInt(elMatch.dataset.step));
    }

    function _fnHandleRunPlots(event, elMatch) {
        fnRunInteractivePlots(parseInt(elMatch.dataset.step));
    }

    function _fnHandleAddDeps(event, elMatch) {
        fnScanDependencies(parseInt(elMatch.dataset.step));
    }

    function _fnHandleShowDeps(event, elMatch) {
        fnShowDag();
    }

    function _fnHandleRunStep(event, elMatch) {
        fnRunStepCombined(parseInt(elMatch.dataset.step));
    }

    function _fnHandleTestCategoryFile(event, elMatch) {
        fnViewCategoryTestFile(
            parseInt(elMatch.dataset.step),
            elMatch.dataset.category);
    }

    function _fnHandleTestStandardsLink(event, elMatch) {
        fnViewStandardsFile(
            parseInt(elMatch.dataset.step),
            elMatch.dataset.category);
    }

    function _fnHandleTestLogLink(event, elMatch) {
        var iLogStep = parseInt(elMatch.dataset.step, 10);
        var sCatKey = elMatch.dataset.category;
        var dictLogStep = _dictWorkflowState.dictWorkflow.listSteps[iLogStep];
        var dictLogTests = fdictGetTests(dictLogStep);
        var sLogCatKey = "dict" + sCatKey.charAt(0)
            .toUpperCase() + sCatKey.slice(1);
        var sOutput = (dictLogTests[sLogCatKey] || {})
            .sLastOutput || "No test output available.";
        var sVerifyKey = "s" + sCatKey.charAt(0)
            .toUpperCase() + sCatKey.slice(1);
        var bLogPassed = (dictLogStep.dictVerification || {})[
            sVerifyKey] === "passed";
        PipeleyenFigureViewer.fnDisplayTestOutput(
            sOutput, bLogPassed);
    }

    function _fnHandleTestEditCmd(event, elMatch) {
        fnEditTestFile(
            parseInt(elMatch.dataset.step),
            parseInt(elMatch.dataset.idx));
    }

    function _fnHandleTestDeleteCmd(event, elMatch) {
        fnDeleteTestCommand(
            parseInt(elMatch.dataset.step),
            parseInt(elMatch.dataset.idx));
    }

    var _DICT_CLICK_HANDLERS = {
        ".action-download": _fnHandleActionDownload,
        ".action-edit": _fnHandleActionEdit,
        ".action-copy": _fnHandleActionCopy,
        ".action-delete": _fnHandleActionDelete,
        ".btn-discovered": _fnHandleDiscoveredButton,
        ".archive-star": _fnHandleArchiveStar,
        ".test-add": _fnHandleTestAdd,
        ".section-add": _fnHandleSectionAdd,
        ".verification-row.clickable": _fnHandleVerificationClickable,
        ".sub-test-row": _fnHandleSubTestRow,
        ".verification-row.expandable": _fnHandleVerificationExpandable,
        '.verification-row[data-approver="deps"]':
            _fnHandleVerificationDeps,
        ".btn-make-standard": _fnHandleMakeStandard,
        ".btn-compare-standard": _fnHandleCompareStandard,
        ".test-file-item": _fnHandleTestFileItem,
        ".test-last-run": _fnHandleTestLastRun,
        ".btn-generate-test": _fnHandleGenerateTest,
        ".step-edit": _fnHandleStepEdit,
        ".btn-interactive-run": _fnHandleInteractiveRun,
        ".btn-interactive-plots": _fnHandleInteractivePlots,
        ".btn-run-tests": _fnHandleRunTests,
        ".btn-run-all-tests": _fnHandleRunTests,
        ".btn-run-category": _fnHandleRunCategory,
        ".btn-run-data": _fnHandleRunData,
        ".btn-run-plots": _fnHandleRunPlots,
        ".btn-add-deps": _fnHandleAddDeps,
        ".btn-show-deps": _fnHandleShowDeps,
        ".btn-run-step": _fnHandleRunStep,
        ".test-category-file": _fnHandleTestCategoryFile,
        ".test-standards-link": _fnHandleTestStandardsLink,
        ".test-log-link": _fnHandleTestLogLink,
        ".test-edit-cmd": _fnHandleTestEditCmd,
        ".test-delete-cmd": _fnHandleTestDeleteCmd,
    };

    function fnHandleDelegatedClick(event) {
        var elTarget = event.target;

        for (var sSelector in _DICT_CLICK_HANDLERS) {
            var elMatch = elTarget.closest(sSelector);
            if (elMatch) {
                _DICT_CLICK_HANDLERS[sSelector](event, elMatch);
                return;
            }
        }

        /* Special case: detail-text on output items */
        var elDetailItem = elTarget.closest(".detail-item");
        if (elTarget.closest(".detail-text") && elDetailItem &&
            elDetailItem.classList.contains("output")) {
            var elText = elTarget.closest(".detail-text");
            if (elText.classList.contains("file-binary")) {
                fnShowBinaryNotViewable();
            } else if (fbIsFileMissing(elText)) {
                fnShowOutputNotAvailable();
            } else {
                PipeleyenFigureViewer.fnDisplayInNextViewer(
                    elDetailItem.dataset.resolved,
                    elDetailItem.dataset.workdir || ""
                );
            }
            return;
        }

        /* Default: toggle step expansion */
        var elStepItem = elTarget.closest(".step-item");
        if (elStepItem &&
            !elTarget.classList.contains("step-checkbox")) {
            fnToggleStepExpand(parseInt(elStepItem.dataset.index));
        }
    }

    function fnHandleDelegatedChange(event) {
        var elTarget = event.target;
        if (elTarget.classList.contains("step-checkbox")) {
            var elStep = elTarget.closest(".step-item");
            fnToggleStepEnabled(
                parseInt(elStep.dataset.index), elTarget.checked
            );
        }
        if (elTarget.classList.contains("plot-only-checkbox")) {
            fnTogglePlotOnly(
                parseInt(elTarget.dataset.step), elTarget.checked
            );
        }
    }

    function fnHandleDelegatedContextMenu(event) {
        var elFile = event.target.closest(".detail-item.output");
        if (elFile) {
            event.preventDefault();
            event.stopPropagation();
            fnShowFileContextMenu(
                event.pageX, event.pageY,
                elFile.dataset.resolved,
                elFile.dataset.workdir || "",
                parseInt(elFile.dataset.step)
            );
            return;
        }
        var elStep = event.target.closest(".step-item");
        if (elStep) {
            event.preventDefault();
            fnShowContextMenu(
                event.pageX, event.pageY,
                parseInt(elStep.dataset.index)
            );
        }
    }

    function fnHandleDelegatedDragStart(event) {
        var elDetail = event.target.closest(".detail-item");
        if (elDetail) {
            event.stopPropagation();
            var dictDragData = {
                iStep: parseInt(elDetail.dataset.step),
                sArray: elDetail.dataset.array,
                iIdx: parseInt(elDetail.dataset.idx),
            };
            event.dataTransfer.setData(
                "vaibify/detail", JSON.stringify(dictDragData)
            );
            event.dataTransfer.setData(
                "vaibify/filepath", elDetail.dataset.resolved
            );
            event.dataTransfer.setData(
                "vaibify/workdir", elDetail.dataset.workdir || ""
            );
            return;
        }
        var elStep = event.target.closest(".step-item");
        if (elStep) {
            var iIdx = parseInt(elStep.dataset.index);
            event.dataTransfer.setData("text/plain", String(iIdx));
            event.dataTransfer.setData(
                "vaibify/step", String(iIdx)
            );
            elStep.classList.add("dragging");
        }
    }

    function fnHandleDelegatedDragEnd(event) {
        var elStep = event.target.closest(".step-item");
        if (elStep) elStep.classList.remove("dragging");
    }

    function fnHandleDelegatedDragOver(event) {
        var elStep = event.target.closest(".step-item");
        var elDetail = event.target.closest(".step-detail");
        if (elStep || elDetail) {
            event.preventDefault();
            if (elStep) elStep.classList.add("drop-target");
        }
    }

    function fnHandleDelegatedDragLeave(event) {
        var elStep = event.target.closest(".step-item");
        if (elStep) elStep.classList.remove("drop-target");
    }

    function fnHandleDelegatedDrop(event) {
        var elStep = event.target.closest(".step-item");
        var elDetail = event.target.closest(".step-detail");
        if (elStep) elStep.classList.remove("drop-target");

        var sDetailData = event.dataTransfer.getData(
            "vaibify/detail"
        );
        if (sDetailData) {
            event.preventDefault();
            event.stopPropagation();
            var iTarget = parseInt(
                (elDetail || elStep).dataset.index
            );
            fnHandleDetailDrop(sDetailData, iTarget);
            return;
        }
        if (elStep) {
            event.preventDefault();
            var sStepData = event.dataTransfer.getData("text/plain");
            if (sStepData !== "") {
                var iFrom = parseInt(sStepData);
                var iTo = parseInt(elStep.dataset.index);
                if (iFrom !== iTo) fnReorderStep(iFrom, iTo);
            }
        }
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


    /* Test generation, running, and state now in scriptTestManager.js */

    function fnGenerateTests(iStep) {
        PipeleyenTestManager.fnGenerateTests(iStep);
    }

    function fnHandleDiscoveredOutputs(dictEvent) {
        var iStep = dictEvent.iStepNumber - 1;
        _dictWorkflowState.dictDiscoveredOutputs[iStep] = dictEvent.listDiscovered;
        fnRenderStepList();
        fnShowToast(
            "Step " + dictEvent.iStepNumber +
            ": " + dictEvent.listDiscovered.length +
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
        var listDisc = _dictWorkflowState.dictDiscoveredOutputs[iStep] || [];
        _dictWorkflowState.dictDiscoveredOutputs[iStep] = listDisc.filter(
            function (d) { return d.sFilePath !== sFile; }
        );
        fnRenderStepList();
    }

    function fnRunCategoryTests(iStepIndex, sCategory) {
        PipeleyenTestManager.fnRunCategoryTests(iStepIndex, sCategory);
    }

    function fnViewCategoryTestFile(iStepIndex, sCategory) {
        PipeleyenTestManager.fnViewCategoryTestFile(iStepIndex, sCategory);
    }

    function fnViewStandardsFile(iStepIndex, sCategory) {
        PipeleyenTestManager.fnViewStandardsFile(iStepIndex, sCategory);
    }

    function fnAddTestItem(iStep, sType) {
        PipeleyenTestManager.fnAddTestItem(iStep, sType);
    }

    function fnShowConfirmModal(sTitle, sMessage, fnOnConfirm) {
        var elExisting = document.getElementById("modalConfirm");
        if (elExisting) elExisting.remove();
        var elModal = document.createElement("div");
        elModal.id = "modalConfirm";
        elModal.className = "modal-overlay";
        elModal.style.display = "flex";
        elModal.innerHTML =
            '<div class="modal">' +
            '<h2>' + fnEscapeHtml(sTitle) + '</h2>' +
            '<p style="white-space:pre-wrap;margin-bottom:16px">' +
            fnEscapeHtml(sMessage) + '</p>' +
            '<div class="modal-actions">' +
            '<button class="btn" id="btnConfirmCancel">Cancel</button>' +
            '<button class="btn btn-primary" ' +
            'id="btnConfirmOk">Confirm</button>' +
            '</div></div>';
        document.body.appendChild(elModal);
        document.getElementById("btnConfirmCancel").addEventListener(
            "click", function () { elModal.remove(); }
        );
        document.getElementById("btnConfirmOk").addEventListener(
            "click", function () {
                elModal.remove();
                fnOnConfirm();
            }
        );
    }

    function fnShowInputModal(sLabel, sPlaceholder, fnCallback) {
        var elExisting = document.getElementById("modalInput");
        if (elExisting) elExisting.remove();
        var elModal = document.createElement("div");
        elModal.id = "modalInput";
        elModal.className = "modal-overlay";
        elModal.style.display = "flex";
        elModal.innerHTML =
            '<div class="modal">' +
            '<h2>' + fnEscapeHtml(sLabel) + '</h2>' +
            '<input type="text" class="input-modal-field" ' +
            'placeholder="' + fnEscapeHtml(sPlaceholder) + '">' +
            '<div class="modal-actions">' +
            '<button class="btn" id="btnInputCancel">Cancel</button>' +
            '<button class="btn btn-primary" ' +
            'id="btnInputConfirm">Add</button>' +
            '</div></div>';
        document.body.appendChild(elModal);
        var elInput = elModal.querySelector(".input-modal-field");
        elInput.focus();
        elInput.addEventListener("keydown", function (e) {
            if (e.key === "Enter") fnConfirmInput();
            if (e.key === "Escape") elModal.remove();
        });
        document.getElementById("btnInputCancel").addEventListener(
            "click", function () { elModal.remove(); }
        );
        document.getElementById("btnInputConfirm").addEventListener(
            "click", fnConfirmInput
        );
        function fnConfirmInput() {
            var sValue = elInput.value.trim();
            elModal.remove();
            if (sValue) fnCallback(sValue);
        }
    }

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
        for (var i = 0; i < listSteps.length; i++) {
            var step = listSteps[i];
            if (step.bEnabled === false) continue;
            if (!fbAllVerificationComplete(step, i)) return false;
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

    /* --- Detail Item Actions --- */

    function fnInlineEditItem(el, iStep, sArray, iIdx) {
        var sRaw = _dictWorkflowState.dictWorkflow.listSteps[iStep][sArray][iIdx];
        var elText = el.querySelector(".detail-text");
        var elActions = el.querySelector(".detail-actions");
        elActions.style.display = "none";

        var elInput = document.createElement("input");
        elInput.type = "text";
        elInput.className = "detail-edit-input";
        elInput.value = sRaw;
        elText.style.display = "none";
        el.insertBefore(elInput, elActions);
        elInput.focus();
        elInput.select();

        var bFinished = false;
        function fnFinishEdit() {
            if (bFinished) return;
            bFinished = true;
            var sNewValue = elInput.value.trim();
            if (sNewValue && sNewValue !== sRaw) {
                _dictWorkflowState.dictWorkflow.listSteps[iStep][sArray][iIdx] = sNewValue;
                fnSaveStepArray(iStep, sArray, true);
            }
            elInput.removeEventListener("blur", fnFinishEdit);
            elInput.remove();
            elText.style.display = "";
            elActions.style.display = "";
            fnRenderStepList();
        }

        elInput.addEventListener("keydown", function (event) {
            if (event.key === "Enter") fnFinishEdit();
            if (event.key === "Escape") {
                bFinished = true;
                elInput.removeEventListener("blur", fnFinishEdit);
                elInput.remove();
                elText.style.display = "";
                elActions.style.display = "";
            }
        });
        elInput.addEventListener("blur", fnFinishEdit);
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
        fnShowInlineInput(iStep, sArrayKey, sPlaceholder);
    }

    function fnShowInlineInput(iStep, sArrayKey, sPlaceholder) {
        var elSection = document.querySelector(
            '.section-add[data-step="' + iStep +
            '"][data-array="' + sArrayKey + '"]'
        );
        if (!elSection) return;
        var elLabel = elSection.parentElement;
        var elExisting = elLabel.nextElementSibling;
        if (elExisting && elExisting.classList.contains("inline-add-row")) {
            return;
        }

        var elRow = document.createElement("div");
        elRow.className = "inline-add-row";
        elRow.innerHTML =
            '<input class="detail-edit-input" type="text" placeholder="' +
            sPlaceholder + '">' +
            '<button class="inline-add-confirm" title="Add">&#10003;</button>' +
            '<button class="inline-add-cancel" title="Cancel">&#10005;</button>';
        elLabel.parentElement.insertBefore(elRow, elLabel.nextSibling);

        var elInput = elRow.querySelector("input");
        elInput.focus();

        function fnConfirm() {
            var sValue = elInput.value.trim();
            if (sValue) {
                fnCommitNewItem(iStep, sArrayKey, sValue);
            }
            elRow.remove();
        }
        function fnCancel() {
            elRow.remove();
        }

        elRow.querySelector(".inline-add-confirm").addEventListener(
            "click", fnConfirm
        );
        elRow.querySelector(".inline-add-cancel").addEventListener(
            "click", fnCancel
        );
        elInput.addEventListener("keydown", function (event) {
            if (event.key === "Enter") fnConfirm();
            if (event.key === "Escape") fnCancel();
        });
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

    /* --- Undo Stack --- */

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
            fnScanDependencies(iStep);
        }
    }

    /* Dependency scanner is now in scriptDependencyScanner.js */

    function fnScanDependencies(iStep) {
        PipeleyenDependencyScanner.fnScanDependencies(iStep);
    }

    function fnShowDependencyModal(iStep, dictResult) {
        PipeleyenDependencyScanner.fnShowDependencyModal(
            iStep, dictResult);
    }

    /* --- Step Expand/Collapse --- */

    function fnToggleStepExpand(iIndex) {
        if (_dictUiState.setExpandedSteps.has(iIndex)) {
            _dictUiState.setExpandedSteps.delete(iIndex);
        } else {
            _dictUiState.setExpandedSteps.add(iIndex);
            fnLoadPlotStandardStatus(iIndex);
        }
        _dictUiState.iSelectedStepIndex = iIndex;
        fnRenderStepList();
    }

    function fnLoadPlotStandardStatus(iStepIndex) {
        PipeleyenPlotStandards.fnLoadPlotStandardStatus(iStepIndex);
    }

    async function fnToggleStepEnabled(iIndex, bEnabled) {
        try {
            await VaibifyApi.fdictPut(
                "/api/steps/" + _dictSessionState.sContainerId + "/" + iIndex,
                {bEnabled: bEnabled}
            );
            _dictWorkflowState.dictWorkflow.listSteps[iIndex].bEnabled = bEnabled;
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

    /* --- Resize Handles --- */

    function fnBindResizeHandles() {
        var elLeft = document.getElementById("panelLeft");
        var elHandleH = elLeft.querySelector(".resize-handle-horizontal");
        if (elHandleH) {
            fnMakeDraggable(elHandleH, function (iDeltaX) {
                var iWidth = elLeft.offsetWidth + iDeltaX;
                iWidth = Math.max(180, Math.min(iWidth, 600));
                document.getElementById("mainLayout")
                    .style.gridTemplateColumns = iWidth + "px 1fr";
            });
        }

        var elHandleV = document.getElementById("resizeHandleVertical");
        if (elHandleV) {
            var elViewerDual = document.getElementById("panelViewerDual");
            var elRight = document.getElementById("panelRight");
            fnMakeDraggableVertical(elHandleV, function (iDeltaY) {
                var iHeight = elViewerDual.offsetHeight + iDeltaY;
                var iMaxHeight = elRight.offsetHeight - 120;
                iHeight = Math.max(80, Math.min(iHeight, iMaxHeight));
                elViewerDual.style.flex = "0 0 " + iHeight + "px";
            });
        }

        var elHandleViewer = document.getElementById("resizeHandleViewer");
        if (elHandleViewer) {
            var elViewerA = document.getElementById("viewerA");
            var elDual = document.getElementById("panelViewerDual");
            fnMakeDraggable(elHandleViewer, function (iDeltaX) {
                var iWidth = elViewerA.offsetWidth + iDeltaX;
                var iMaxWidth = elDual.offsetWidth - 120;
                iWidth = Math.max(100, Math.min(iWidth, iMaxWidth));
                elViewerA.style.flex = "0 0 " + iWidth + "px";
            });
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

    function fnMakeDraggable(elHandle, fnOnMove) {
        elHandle.addEventListener("mousedown", function (event) {
            var iStartX = event.clientX;
            event.preventDefault();
            function fnMouseMove(e) {
                var iDelta = e.clientX - iStartX;
                iStartX = e.clientX;
                fnOnMove(iDelta);
            }
            function fnMouseUp() {
                document.removeEventListener("mousemove", fnMouseMove);
                document.removeEventListener("mouseup", fnMouseUp);
            }
            document.addEventListener("mousemove", fnMouseMove);
            document.addEventListener("mouseup", fnMouseUp);
        });
    }

    function fnMakeDraggableVertical(elHandle, fnOnMove) {
        elHandle.addEventListener("mousedown", function (event) {
            var iStartY = event.clientY;
            event.preventDefault();
            function fnMouseMove(e) {
                var iDelta = e.clientY - iStartY;
                iStartY = e.clientY;
                fnOnMove(iDelta);
            }
            function fnMouseUp() {
                document.removeEventListener("mousemove", fnMouseMove);
                document.removeEventListener("mouseup", fnMouseUp);
                PipeleyenTerminal.fnFitActiveTerminal();
            }
            document.addEventListener("mousemove", fnMouseMove);
            document.addEventListener("mouseup", fnMouseUp);
        });
    }

    /* --- Toolbar Events --- */

    function fnBindToolbarEvents() {
        fnBindToolbarMenus();
        fnBindMenuItemActions();
        fnBindPushModalEvents();
        var elLogo = document.querySelector(".toolbar-logo");
        if (elLogo) {
            elLogo.style.cursor = "pointer";
            elLogo.addEventListener("click", fnDisconnect);
        }
    }

    function fnBindToolbarMenus() {
        fnBindMenuItemCloseOnClick();
        document.querySelectorAll(".toolbar-menu-trigger")
            .forEach(function (el) {
                el.addEventListener("click", function (event) {
                    event.stopPropagation();
                    var elDropdown = el.parentElement.querySelector(
                        ".toolbar-menu-dropdown"
                    );
                    fnCloseAllToolbarMenus();
                    elDropdown.classList.toggle("active");
                });
            });
        document.addEventListener("click", fnCloseAllToolbarMenus);
    }

    function fnCloseAllToolbarMenus() {
        document.querySelectorAll(".toolbar-menu-dropdown")
            .forEach(function (el) {
                el.classList.remove("active");
            });
    }

    function fnBindMenuItemCloseOnClick() {
        document.querySelectorAll(".toolbar-menu-item")
            .forEach(function (el) {
                el.addEventListener("click", fnCloseAllToolbarMenus);
            });
    }

    function fnBindMenuItemActions() {
        var dictActions = {
            btnRunSelected: fnRunSelected,
            btnRunAll: fnRunAll,
            btnForceRunAll: fnForceRunAll,
            btnKillPipeline: fnKillPipeline,
            btnVerify: fnVerify,
            btnRunAllTests: fnRunAllTests,
            btnValidateReferences: fnValidateReferences,
            btnStandardizeAllPlots: fnStandardizeAllWorkflowPlots,
            btnOverleafPush: function () { fnOpenPushModal("overleaf"); },
            btnGithubPush: function () { fnOpenPushModal("github"); },
            btnZenodoArchive: function () { fnOpenPushModal("zenodo"); },
            btnShowDag: fnShowDag,
            btnVsCode: fnOpenVsCode,
            btnMonitor: function () {},
            btnResetLayout: fnResetLayout,
            btnAdminContainers: fnDisconnect,
            btnAdminWorkflows: function () {
                if (_dictSessionState.sContainerId) fnConnectToContainer(_dictSessionState.sContainerId);
            },
            btnAdminQuit: function () { window.close(); },
        };
        for (var sId in dictActions) {
            var el = document.getElementById(sId);
            if (el) {
                el.addEventListener("click", dictActions[sId]);
            }
        }
    }

    function fnSetPollInterval(iSeconds) {
        VaibifyPolling.fnSetPollInterval(iSeconds);
        var elSlider = document.getElementById("gsPollInterval");
        if (elSlider) elSlider.title = iSeconds + " seconds";
        fnStartFileChangePolling();
    }

    /* --- Sync Push Modal --- */

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

    function fnOpenPushModal(sService) {
        VaibifySyncManager.fnOpenPushModal(sService);
    }

    function fnShowSyncError(dictResult, sService) {
        VaibifySyncManager.fnShowSyncError(dictResult, sService);
    }

    function fnBindPushModalEvents() {
        VaibifySyncManager.fnBindPushModalEvents();
    }

    function fnBindWorkflowPickerEvents() {
        document.getElementById("btnWorkflowBack").addEventListener(
            "click", function () {
                fnShowContainerLanding();
                fnLoadContainers();
            }
        );
        document.getElementById("btnNoWorkflow").addEventListener(
            "click", function () {
                if (PipeleyenContainerManager.fsGetSelectedContainerId()) {
                    fnEnterNoWorkflow(PipeleyenContainerManager.fsGetSelectedContainerId());
                }
            }
        );
        document.getElementById("btnNewWorkflow").addEventListener(
            "click", function () {
                PipeleyenContainerManager.fnCreateNewWorkflow();
            }
        );
        document.getElementById("btnRefreshWorkflows").addEventListener(
            "click", function () {
                if (PipeleyenContainerManager.fsGetSelectedContainerId()) {
                    fnConnectToContainer(PipeleyenContainerManager.fsGetSelectedContainerId());
                }
            }
        );
        document.getElementById("activeWorkflowName").addEventListener(
            "click", function (event) {
                event.stopPropagation();
                VaibifyWorkflowManager.fnToggleWorkflowDropdown();
            }
        );
        document.addEventListener("click", function () {
            VaibifyWorkflowManager.fnHideWorkflowDropdown();
        });
    }

    function fnBindContainerLandingEvents() {
        PipeleyenContainerManager.fnBindContainerLandingEvents();
    }

    function fnBindAddContainerModal() {
        PipeleyenContainerManager.fnBindAddContainerModal();
    }

    /* --- Creation Wizard (delegated to VaibifyWorkflowManager) --- */

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

    /* Pipeline WebSocket, interactive, and sentinel monitoring
       now in scriptPipelineRunner.js */

    /* Test result handling now in scriptTestManager.js */

    function fnClearOutputModified(iStep) {
        var dictStep = _dictWorkflowState.dictWorkflow.listSteps[iStep];
        if (dictStep && dictStep.dictVerification) {
            delete dictStep.dictVerification.bOutputModified;
            delete dictStep.dictVerification.listModifiedFiles;
        }
    }

    /* Pipeline state recovery now in scriptPipelineRunner.js */

    function fnStartPipelinePolling(sId) {
        VaibifyPolling.fnStartPipelinePolling(sId);
    }

    function fnStopPipelinePolling() {
        VaibifyPolling.fnStopPipelinePolling();
    }

    function fnStartFileChangePolling() {
        if (!_dictSessionState.sContainerId) return;
        VaibifyPolling.fnStartFilePolling(_dictSessionState.sContainerId);
    }

    function fnStopFileChangePolling() {
        VaibifyPolling.fnStopFilePolling();
    }

    function fnProcessFileStatusResponse(dictStatus) {
        fnDetectOutputFileChanges(dictStatus.dictModTimes || {});
        if (dictStatus.dictMaxMtimeByStep) {
            _dictWorkflowState.dictOutputMtimes = dictStatus.dictMaxMtimeByStep;
        }
        if (dictStatus.dictMaxPlotMtimeByStep) {
            _dictWorkflowState.dictPlotMtimes = dictStatus.dictMaxPlotMtimeByStep;
        }
        fnResetStaleUserVerifications();
        var dictInv = dictStatus.dictInvalidatedSteps;
        if (dictInv && Object.keys(dictInv).length > 0) {
            fnApplyInvalidatedSteps(dictInv);
        }
        fnUpdateDepsTimestamps();
        fnUpdateScriptStatus(dictStatus.dictScriptStatus);
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
        if (!_dictWorkflowState.dictWorkflow || !_dictWorkflowState.dictWorkflow.listSteps) return;
        for (var i = 0; i < _dictWorkflowState.dictWorkflow.listSteps.length; i++) {
            var listDeps = flistGetStepDependencies(i);
            if (listDeps.length === 0) continue;
            var sDepsState = fsComputeDepsState(i);
            var dictStep = _dictWorkflowState.dictWorkflow.listSteps[i];
            var dictVerify = dictStep.dictVerification || {};
            var sOld = dictVerify.sLastDepsCheck || "";
            if (sDepsState === "passed" && !sOld) {
                dictVerify.sLastDepsCheck = fsFormatUtcTimestamp();
                dictStep.dictVerification = dictVerify;
            } else if (sDepsState !== "passed" && sOld) {
                dictVerify.sLastDepsCheck = "";
                dictStep.dictVerification = dictVerify;
            }
        }
    }

    function fnDetectOutputFileChanges(dictNewMods) {
        for (var sPath in dictNewMods) {
            if (_dictWorkflowState.dictFileModTimes[sPath] !== dictNewMods[sPath]) {
                _dictWorkflowState.dictFileModTimes = dictNewMods;
                _dictWorkflowState.dictFileExistenceCache = {};
                fnScheduleFileExistenceCheck();
                fnRenderStepList();
                return;
            }
        }
    }

    function fnUpdateScriptStatus(dictNewScriptStatus) {
        if (!dictNewScriptStatus) return;
        var dictPrev = JSON.stringify(_dictWorkflowState.dictScriptModified);
        _dictWorkflowState.dictScriptModified = dictNewScriptStatus;
        if (JSON.stringify(_dictWorkflowState.dictScriptModified) !== dictPrev) {
            fnRenderStepList();
        }
    }


    /* Test markers and file change notifications
       now in scriptTestManager.js */

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

    function fnDisplayLogInViewer(sLogPath) {
        PipeleyenPipelineRunner.fnDisplayLogInViewer(sLogPath);
    }

    function fnShowErrorModal(sMessage) {
        var elModal = document.getElementById("modalError");
        var elContent = document.getElementById("modalErrorContent");
        elContent.textContent = fsSanitizeErrorForUser(sMessage);
        elModal.style.display = "flex";
    }

    function fnBindErrorModal() {
        document.getElementById("btnModalErrorClose").addEventListener(
            "click", function () {
                document.getElementById("modalError").style.display = "none";
            }
        );
    }

    /* API confirm modal and test generation via API
       now in scriptTestManager.js */

    /* Pipeline output, execution, and actions
       now in scriptPipelineRunner.js */
    /* Test file editing now in scriptTestManager.js */

    function fnEditTestFile(iStepIndex, iCmdIdx) {
        PipeleyenTestManager.fnEditTestFile(iStepIndex, iCmdIdx);
    }

    function fnDeleteTestCommand(iStepIndex, iCmdIdx) {
        PipeleyenTestManager.fnDeleteTestCommand(iStepIndex, iCmdIdx);
    }

    function fnSendPipelineAction(dictAction) {
        PipeleyenPipelineRunner.fnSendPipelineAction(dictAction);
    }

    function fnRunSingleStep(iIndex) {
        PipeleyenPipelineRunner.fnRunSingleStep(iIndex);
    }

    function fnRunStepTests(iStepIndex) {
        PipeleyenTestManager.fnRunStepTests(iStepIndex);
    }

    /* Plot standardization is now in scriptPlotStandards.js */

    function fnStandardizeAllPlots(iStepIndex) {
        PipeleyenPlotStandards.fnStandardizeAllPlots(iStepIndex);
    }

    function fnCompareStepPlots(iStepIndex) {
        PipeleyenPlotStandards.fnCompareStepPlots(iStepIndex);
    }

    function fnStandardizeAllWorkflowPlots() {
        PipeleyenPlotStandards.fnStandardizeAllWorkflowPlots();
    }

    function fnRunInteractiveStep(iIndex) {
        PipeleyenPipelineRunner.fnRunInteractiveStep(iIndex);
    }

    function fnRunInteractivePlots(iIndex) {
        PipeleyenPipelineRunner.fnRunInteractivePlots(iIndex);
    }

    function fnRunStepCombined(iIndex) {
        PipeleyenPipelineRunner.fnRunStepCombined(iIndex);
    }

    function fnRunSelected() {
        PipeleyenPipelineRunner.fnRunSelected();
    }

    function fnRunAll() {
        PipeleyenPipelineRunner.fnRunAll();
    }

    function fnForceRunAll() {
        PipeleyenPipelineRunner.fnForceRunAll();
    }

    function fnKillPipeline() {
        PipeleyenPipelineRunner.fnKillPipeline();
    }

    function fnVerify() {
        PipeleyenPipelineRunner.fnVerify();
    }

    function fnRunAllTests() {
        PipeleyenPipelineRunner.fnRunAllTests();
    }

    function fnValidateReferences() {
        PipeleyenPipelineRunner.fnValidateReferences();
    }

    function fnOpenVsCode() {
        var sHexId = _dictSessionState.sContainerId.replace(/-/g, "");
        var sUri =
            "vscode://ms-vscode-remote.remote-containers/attach?containerId=" +
            sHexId;
        window.open(sUri, "_blank");
        fnShowToast("Opening VS Code...", "success");
    }

    /* --- Context Menu --- */

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

    function fnBindContextMenuEvents() {
        document.getElementById("contextMenu")
            .querySelectorAll(".context-menu-item")
            .forEach(function (el) {
                el.addEventListener("click", function (event) {
                    event.stopPropagation();
                    fnHandleContextAction(
                        el.dataset.action, _dictUiState.iContextStepIndex);
                    fnHideContextMenu();
                });
            });
        document.getElementById("fileContextMenu")
            .querySelectorAll(".context-menu-item")
            .forEach(function (el) {
                el.addEventListener("click", function (event) {
                    event.stopPropagation();
                    fnHandleFileContextAction(el.dataset.action);
                    fnHideContextMenu();
                });
            });
    }

    async function fnHandleFileContextAction(sAction) {
        if (sAction === "pullToHost") {
            fnPromptPullToHost(_dictUiState.sContextFilePath);
            return;
        }
        if (sAction === "copyPath") {
            fnCopyToClipboard(_dictUiState.sContextFilePath);
            return;
        }
        if (sAction === "addToGit") {
            fnShowToast("Adding to Git...", "success");
            try {
                var dictResult = await VaibifyApi.fdictPost(
                    "/api/github/" + _dictSessionState.sContainerId + "/add-file",
                    {sFilePath: _dictUiState.sContextFilePath}
                );
                if (dictResult.bSuccess) {
                    fnShowToast("Added to Git", "success");
                    fnRenderStepList();
                } else {
                    fnShowSyncError(dictResult, "GitHub");
                }
            } catch (error) {
                fnShowToast(fsSanitizeErrorForUser(error.message), "error");
            }
            return;
        }
        if (sAction === "archiveToZenodo") {
            fnShowToast("Archiving to Zenodo...", "success");
            try {
                var dictZenodoResult = await VaibifyApi.fdictPost(
                    "/api/zenodo/" + _dictSessionState.sContainerId + "/archive",
                    {listFilePaths: [_dictUiState.sContextFilePath]}
                );
                if (dictZenodoResult.bSuccess) {
                    fnShowToast("Archived to Zenodo", "success");
                    fnRenderStepList();
                } else {
                    fnShowSyncError(dictZenodoResult, "Zenodo");
                }
            } catch (error) {
                fnShowToast(fsSanitizeErrorForUser(error.message), "error");
            }
        }
    }

    function fnPromptPullToHost(sContainerPath) {
        PipeleyenFilePull.fnPromptPullToHost(sContainerPath);
    }

    function fnHandleContextAction(sAction, iIndex) {
        if (sAction === "runStep") {
            fnRunSingleStep(iIndex);
        } else if (sAction === "edit") {
            PipeleyenStepEditor.fnOpenEditModal(iIndex);
        } else if (sAction === "runFrom") {
            fnSendPipelineAction({
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

    /* --- Toast Notifications --- */

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
        fsGetContainerId: function () { return _dictSessionState.sContainerId; },
        fsGetSessionToken: function () { return _dictSessionState.sSessionToken; },
        fdictGetWorkflow: function () { return _dictWorkflowState.dictWorkflow; },
        fsGetWorkflowPath: function () { return _dictWorkflowState.sWorkflowPath; },
        fiGetSelectedStepIndex: function () { return _dictUiState.iSelectedStepIndex; },
        fdictBuildClientVariables: fdictBuildClientVariables,
        fnShowConfirmModal: fnShowConfirmModal,
        fnShowInputModal: fnShowInputModal,
        fnClearOutputModified: fnClearOutputModified,
        fnActivateWorkflow: _fnActivateWorkflow,
        fnEnterNoWorkflow: fnEnterNoWorkflow,
        fnSaveStepUpdate: fnSaveStepUpdate,
        fnShowWorkflowPicker: fnShowWorkflowPicker,
        fnSetPlotStandardExists: function (sKey, bValue) {
            _dictWorkflowState.dictPlotStandardExists[sKey] = bValue;
        },
        fbGetPlotStandardExists: function (sKey) {
            return _dictWorkflowState.dictPlotStandardExists[sKey];
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
    };
})();

/* PipeleyenFiles is now in scriptFiles.js */

document.addEventListener("DOMContentLoaded", PipeleyenApp.fnInitialize);

function fnBlockUnload(event) {
    event.preventDefault();
    event.returnValue = "";
}

window.addEventListener("beforeunload", fnBlockUnload);

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
