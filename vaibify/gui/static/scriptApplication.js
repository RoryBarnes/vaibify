/* Vaibify — Main application logic */

const PipeleyenApp = (function () {
    "use strict";

    function fbIsTerminalFocused() {
        var elActive = document.activeElement;
        if (!elActive) return false;
        return !!elActive.closest("#terminalStrip, .xterm");
    }

    var sSessionToken = "";
    let sContainerId = null;
    let dictWorkflow = null;
    let sWorkflowPath = null;
    let iSelectedStepIndex = -1;
    let setExpandedSteps = new Set();
    var listUndoStack = [];
    var I_MAX_UNDO = 50;
    var SET_BINARY_EXTENSIONS = new Set([
        ".npy", ".npz", ".pkl", ".pickle", ".hdf5", ".h5",
        ".bin", ".dat", ".o", ".so", ".a", ".pyc", ".gz",
        ".zip", ".tar", ".bz2", ".xz",
    ]);
    let wsPipeline = null;
    let dictStepStatus = {};
    var _dictDashboardMode = null;

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
            var response = await fetch("/api/session-token");
            var data = await response.json();
            sSessionToken = data.sToken || "";
            fnInstallAuthenticatedFetch(sSessionToken);
        } catch (e) {
            sSessionToken = "";
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

    /* --- Initialization --- */

    async function fnInitialize() {
        await fnFetchSessionToken();
        fnLoadUserName();
        fnLoadContainers();
        fnBindToolbarEvents();
        fnBindWorkflowPickerEvents();
        fnBindContainerLandingEvents();
        fnBindAddContainerModal();
        fnBindErrorModal();
        fnBindApiConfirmModal();
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
            var response = await fetch("/api/user");
            var dictUser = await response.json();
            fnSetVerificationUserName(dictUser.sUserName);
        } catch (error) {
            fnSetVerificationUserName("User");
        }
    }

    /* --- Container Landing --- */

    async function fnLoadContainers() {
        var elList = document.getElementById("listContainers");
        try {
            var response = await fetch("/api/registry");
            var dictResult = await response.json();
            fnRenderContainerList(dictResult.listContainers || []);
            fnRenderUnrecognizedList(dictResult.listUnrecognized || []);
        } catch (error) {
            elList.innerHTML =
                '<p style="color: var(--color-red);">' +
                "Cannot load containers</p>";
        }
    }

    function fnRenderContainerList(listContainers) {
        var elList = document.getElementById("listContainers");
        if (listContainers.length === 0) {
            elList.innerHTML =
                '<p class="muted-text" style="text-align: center;">' +
                "No containers registered. Click + to add one.</p>";
            return;
        }
        elList.innerHTML = listContainers.map(function (dictContainer) {
            return fsRenderContainerTile(dictContainer);
        }).join("");
        fnBindContainerTiles(elList);
    }

    function fnRenderUnrecognizedList(listUnrecognized) {
        var elSection = document.getElementById("unrecognizedSection");
        var elList = document.getElementById("listUnrecognized");
        if (listUnrecognized.length === 0) {
            elSection.style.display = "none";
            return;
        }
        elSection.style.display = "";
        elList.innerHTML = listUnrecognized.map(function (c) {
            return (
                '<div class="container-card unrecognized" data-id="' +
                fnEscapeHtml(c.sContainerId) + '">' +
                '<span class="name">' +
                fnEscapeHtml(c.sName) + "</span>" +
                '<span class="image">' +
                fnEscapeHtml(c.sImage) + "</span></div>"
            );
        }).join("");
        elList.querySelectorAll(".container-card").forEach(function (el) {
            el.addEventListener("click", function () {
                fnConnectToContainer(el.dataset.id);
            });
        });
    }

    function fsRenderContainerTile(dictContainer) {
        var sStatusClass = _fsStatusDotClass(dictContainer.sStatus);
        var sId = dictContainer.sContainerId || "";
        return (
            '<div class="container-tile" data-name="' +
            fnEscapeHtml(dictContainer.sName) +
            '" data-container-id="' + fnEscapeHtml(sId) + '">' +
            '<div class="container-tile-main">' +
            '<span class="status-dot ' + sStatusClass + '"></span>' +
            '<span class="container-tile-name">' +
            fnEscapeHtml(dictContainer.sName) + "</span>" +
            "</div>" +
            '<button class="btn-icon container-tile-gear" ' +
            'title="Actions">&#9881;</button>' +
            '<div class="container-tile-menu" style="display:none;">' +
            '<div class="container-menu-item" data-action="start">' +
            "Start</div>" +
            '<div class="container-menu-item" data-action="stop">' +
            "Stop</div>" +
            '<div class="container-menu-item" data-action="rebuild">' +
            "Rebuild</div>" +
            '<div class="container-menu-separator"></div>' +
            '<div class="container-menu-item danger" ' +
            'data-action="remove">Remove from list</div>' +
            "</div></div>"
        );
    }

    function _fsStatusDotClass(sStatus) {
        if (sStatus === "running") return "status-running";
        if (sStatus === "stopped") return "status-stopped";
        return "status-not-built";
    }

    function fnBindContainerTiles(elParent) {
        elParent.querySelectorAll(".container-tile").forEach(function (el) {
            var sName = el.dataset.name;
            el.querySelector(".container-tile-main").addEventListener(
                "click", function () {
                    fnHandleContainerClick(sName);
                }
            );
            _fnBindGearMenu(el, sName);
        });
    }

    function _fnBindGearMenu(elTile, sName) {
        var elGear = elTile.querySelector(".container-tile-gear");
        var elMenu = elTile.querySelector(".container-tile-menu");
        elGear.addEventListener("click", function (event) {
            event.stopPropagation();
            _fnToggleGearMenu(elMenu);
        });
        elMenu.querySelectorAll(".container-menu-item").forEach(
            function (elItem) {
                elItem.addEventListener("click", function (event) {
                    event.stopPropagation();
                    elMenu.style.display = "none";
                    fnHandleContainerAction(sName, elItem.dataset.action);
                });
            }
        );
    }

    function _fnToggleGearMenu(elMenu) {
        document.querySelectorAll(".container-tile-menu").forEach(
            function (el) { el.style.display = "none"; }
        );
        var bVisible = elMenu.style.display !== "none";
        elMenu.style.display = bVisible ? "none" : "";
    }

    async function fnHandleContainerClick(sName) {
        var elTile = document.querySelector(
            '.container-tile[data-name="' + sName + '"]'
        );
        var elDot = elTile ? elTile.querySelector(".status-dot") : null;
        var bRunning = elDot && elDot.classList.contains("status-running");
        var bNotBuilt = elDot &&
            elDot.classList.contains("status-not-built");
        if (bNotBuilt) {
            await fnBuildContainer(sName);
            return;
        }
        if (!bRunning) {
            await fnStartContainer(sName);
        }
        var sStoredId = elTile ? elTile.dataset.containerId : "";
        if (sStoredId) {
            fnConnectToContainer(sStoredId);
        } else {
            fnConnectToContainerByName(sName);
        }
    }

    async function fnHandleContainerAction(sName, sAction) {
        if (sAction === "start") await fnStartContainer(sName);
        else if (sAction === "stop") await fnStopContainer(sName);
        else if (sAction === "rebuild") await fnRebuildContainer(sName);
        else if (sAction === "remove") await fnRemoveContainer(sName);
    }

    async function fnBuildContainer(sName) {
        var elOverlay = document.getElementById("modalBuildProgress");
        elOverlay.style.display = "flex";
        try {
            var response = await fetch(
                "/api/containers/" + encodeURIComponent(sName) + "/build",
                { method: "POST" }
            );
            if (!response.ok) {
                var detail = await response.json();
                fnShowToast(detail.detail || "Build failed", "error");
                return;
            }
            fnShowToast("Build complete", "success");
            await fnStartContainer(sName);
        } catch (error) {
            fnShowToast(fsSanitizeErrorForUser(error.message), "error");
        } finally {
            elOverlay.style.display = "none";
            fnLoadContainers();
        }
    }

    async function fnStartContainer(sName) {
        try {
            var response = await fetch(
                "/api/containers/" + encodeURIComponent(sName) + "/start",
                { method: "POST" }
            );
            if (!response.ok) {
                var detail = await response.json();
                fnShowToast(detail.detail || "Start failed", "error");
                return;
            }
            fnShowToast("Container started", "success");
        } catch (error) {
            fnShowToast(fsSanitizeErrorForUser(error.message), "error");
        }
        fnLoadContainers();
    }

    async function fnStopContainer(sName) {
        try {
            var response = await fetch(
                "/api/containers/" + encodeURIComponent(sName) + "/stop",
                { method: "POST" }
            );
            if (!response.ok) {
                var detail = await response.json();
                fnShowToast(detail.detail || "Stop failed", "error");
                return;
            }
            fnShowToast("Container stopped", "success");
        } catch (error) {
            fnShowToast(fsSanitizeErrorForUser(error.message), "error");
        }
        fnLoadContainers();
    }

    async function fnRebuildContainer(sName) {
        fnShowConfirmModal(
            "Rebuild Container",
            "Rebuild will stop and rebuild the container. Continue?",
            async function () {
                await fnStopContainer(sName);
                await fnBuildContainer(sName);
            }
        );
    }

    async function fnRemoveContainer(sName) {
        fnShowConfirmModal(
            "Remove Container",
            "Remove '" + sName + "' from the container list?",
            async function () {
                try {
                    var response = await fetch(
                        "/api/registry/" + encodeURIComponent(sName),
                        { method: "DELETE" }
                    );
                    if (!response.ok) {
                        var detail = await response.json();
                        fnShowToast(detail.detail || "Remove failed", "error");
                        return;
                    }
                    fnShowToast("Container removed", "success");
                } catch (error) {
                    fnShowToast(fsSanitizeErrorForUser(error.message), "error");
                }
                fnLoadContainers();
            }
        );
    }

    /* --- Directory Browser --- */

    var _sBrowserCurrentPath = "";
    var _listBrowserHistory = [];
    var _iBrowserHistoryIndex = -1;
    var _bBrowserNavigating = false;

    async function fnOpenDirectoryBrowser() {
        document.getElementById("modalAddContainer").style.display = "flex";
        _listBrowserHistory = [];
        _iBrowserHistoryIndex = -1;
        await fnBrowseDirectory("");
    }

    function fnBrowserNavigateBack() {
        if (_iBrowserHistoryIndex <= 0) return;
        _bBrowserNavigating = true;
        _iBrowserHistoryIndex--;
        fnBrowseDirectory(_listBrowserHistory[_iBrowserHistoryIndex]);
    }

    function fnBrowserNavigateForward() {
        if (_iBrowserHistoryIndex >= _listBrowserHistory.length - 1) return;
        _bBrowserNavigating = true;
        _iBrowserHistoryIndex++;
        fnBrowseDirectory(_listBrowserHistory[_iBrowserHistoryIndex]);
    }

    function _fnUpdateBrowserNavButtons() {
        var elBack = document.getElementById("btnBrowserBack");
        var elForward = document.getElementById("btnBrowserForward");
        elBack.disabled = _iBrowserHistoryIndex <= 0;
        elForward.disabled =
            _iBrowserHistoryIndex >= _listBrowserHistory.length - 1;
    }

    async function fnBrowseDirectory(sPath) {
        var elEntries = document.getElementById("directoryEntries");
        elEntries.innerHTML =
            '<p class="muted-text" style="text-align:center;">Loading...</p>';
        try {
            var sUrl = "/api/host-directories";
            if (sPath) sUrl += "?sPath=" + encodeURIComponent(sPath);
            var response = await fetch(sUrl);
            if (!response.ok) {
                var detail = await response.json();
                fnShowToast(detail.detail || "Browse failed", "error");
                return;
            }
            var dictResult = await response.json();
            _sBrowserCurrentPath = dictResult.sCurrentPath;
            if (!_bBrowserNavigating) {
                _listBrowserHistory = _listBrowserHistory.slice(
                    0, _iBrowserHistoryIndex + 1
                );
                _listBrowserHistory.push(dictResult.sCurrentPath);
                _iBrowserHistoryIndex = _listBrowserHistory.length - 1;
            }
            _bBrowserNavigating = false;
            _fnUpdateBrowserNavButtons();
            fnRenderBreadcrumb(dictResult.sCurrentPath);
            fnRenderDirectoryEntries(dictResult.listEntries);
            _fnUpdateSelectButton(
                dictResult.sCurrentPath, dictResult.bHasConfig
            );
        } catch (error) {
            elEntries.innerHTML =
                '<p style="color:var(--color-red);">Error loading</p>';
        }
    }

    function fnRenderBreadcrumb(sPath) {
        var elBar = document.getElementById("directoryBreadcrumb");
        var listSegments = sPath.split("/").filter(function (s) {
            return s.length > 0;
        });
        var sHtml = "";
        var sBuiltPath = "";
        for (var i = 0; i < listSegments.length; i++) {
            sBuiltPath += "/" + listSegments[i];
            var sNavTarget = (i === 0) ? "/" : sBuiltPath.substring(
                0, sBuiltPath.lastIndexOf("/"));
            sHtml +=
                '<span class="breadcrumb-sep" data-path="' +
                fnEscapeHtml(sNavTarget) + '">/</span>' +
                '<span class="breadcrumb-segment" data-path="' +
                fnEscapeHtml(sBuiltPath) + '">' +
                fnEscapeHtml(listSegments[i]) + "</span>";
        }
        if (listSegments.length === 0) {
            sHtml = '<span class="breadcrumb-segment" data-path="/">/</span>';
        }
        elBar.innerHTML = sHtml;
        elBar.querySelectorAll(
            ".breadcrumb-segment, .breadcrumb-sep"
        ).forEach(function (el) {
            if (el.dataset.path) {
                el.addEventListener("click", function () {
                    fnBrowseDirectory(el.dataset.path);
                });
            }
        });
    }

    function fnRenderDirectoryEntries(listEntries) {
        var elContainer = document.getElementById("directoryEntries");
        if (listEntries.length === 0) {
            elContainer.innerHTML =
                '<p class="muted-text" style="text-align:center;">' +
                "No subdirectories</p>";
            return;
        }
        elContainer.innerHTML = listEntries.map(function (entry) {
            var sConfigClass = entry.bHasConfig ? " has-config" : "";
            return (
                '<div class="directory-entry' + sConfigClass +
                '" data-path="' + fnEscapeHtml(entry.sPath) + '">' +
                '<span class="directory-entry-icon">&#128193;</span>' +
                '<span class="directory-entry-name">' +
                fnEscapeHtml(entry.sName) + "</span>" +
                (entry.bHasConfig
                    ? '<img src="/static/favicon.png" class="config-indicator" alt="vaibify">'
                    : "") +
                "</div>"
            );
        }).join("");
        _fnBindDirectoryEntryClicks(elContainer);
    }

    function _fnBindDirectoryEntryClicks(elContainer) {
        elContainer.querySelectorAll(".directory-entry").forEach(
            function (el) {
                el.addEventListener("click", function () {
                    fnBrowseDirectory(el.dataset.path);
                });
            }
        );
    }

    function _fnUpdateSelectButton(sPath, bHasConfig) {
        var elPath = document.getElementById("directoryCurrentPath");
        var elLabel = document.getElementById("configFoundLabel");
        var elButton = document.getElementById("btnAddContainerConfirm");
        elPath.textContent = sPath;
        elLabel.style.display = bHasConfig ? "" : "none";
        elButton.disabled = !bHasConfig;
    }

    async function fnSelectDirectory() {
        if (!_sBrowserCurrentPath) return;
        try {
            var response = await fetch("/api/registry", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    sDirectory: _sBrowserCurrentPath,
                }),
            });
            if (!response.ok) {
                var detail = await response.json();
                fnShowToast(detail.detail || "Add failed", "error");
                return;
            }
            fnShowToast("Container added", "success");
            document.getElementById("modalAddContainer").style.display = "none";
        } catch (error) {
            fnShowToast(fsSanitizeErrorForUser(error.message), "error");
        }
        fnLoadContainers();
    }

    async function fnConnectToContainerByName(sName) {
        try {
            var response = await fetch("/api/registry");
            var dictResult = await response.json();
            var listAll = dictResult.listContainers || [];
            var dictMatch = listAll.find(function (c) {
                return c.sName === sName && c.sContainerId;
            });
            if (!dictMatch) {
                fnShowToast("Container not found for " + sName, "error");
                return;
            }
            fnConnectToContainer(dictMatch.sContainerId);
        } catch (error) {
            fnShowToast(fsSanitizeErrorForUser(error.message), "error");
        }
    }

    var _sSelectedContainerId = null;
    var _sSelectedContainerName = null;

    async function fnConnectToContainer(sId) {
        try {
            var responseWorkflows = await fetch("/api/workflows/" + sId);
            var listWorkflows = await responseWorkflows.json();
            _sSelectedContainerId = sId;
            _sSelectedContainerName = _fsContainerNameById(sId);
            fnShowWorkflowPicker(_sSelectedContainerName);
            fnRenderWorkflowList(listWorkflows, sId);
        } catch (error) {
            fnShowToast(fsSanitizeErrorForUser(error.message), "error");
        }
    }

    function _fsContainerNameById(sId) {
        var el = document.querySelector(
            '.container-tile[data-container-id="' + sId + '"]' +
            ' .container-tile-name'
        );
        return el ? el.textContent : sId.substring(0, 12);
    }

    function fnRenderWorkflowList(listWorkflows, sId) {
        var elList = document.getElementById("listWorkflows");
        var sCardsHtml = "";
        if (listWorkflows.length === 0) {
            sCardsHtml =
                '<p style="color: var(--text-muted); text-align: center;">' +
                'No workflows found. Create one to get started.</p>';
        } else {
            sCardsHtml = listWorkflows.map(function (dictWf) {
                var sRepo = dictWf.sRepoName || "";
                return (
                    '<div class="container-card" data-path="' +
                    fnEscapeHtml(dictWf.sPath) + '">' +
                    '<span class="name">' +
                    fnEscapeHtml(dictWf.sName) + '</span>' +
                    '<span class="image">' +
                    fnEscapeHtml(sRepo) + '</span></div>'
                );
            }).join("");
        }
        elList.innerHTML = sCardsHtml;
        elList.querySelectorAll(".container-card").forEach(function (el) {
            el.addEventListener("click", function () {
                var sPath = el.dataset.path;
                var sName = el.querySelector(".name").textContent;
                fnSelectWorkflow(sId, sPath, sName);
            });
        });
    }

    function fnCreateNewWorkflow() {
        if (!_sSelectedContainerId) return;
        fnShowInputModal(
            "Workflow display name",
            "My Workflow",
            function (sName) {
                var sDefault = sName.toLowerCase().replace(/[^a-z0-9]+/g, "-");
                fnShowInputModal(
                    "Filename (no spaces, .json added automatically)",
                    sDefault,
                    function (sFileName) {
                        _fnSubmitNewWorkflow(sName, sFileName);
                    }
                );
            }
        );
    }

    async function _fnSubmitNewWorkflow(sName, sFileName) {
        try {
            var response = await fetch(
                "/api/workflows/" + _sSelectedContainerId + "/create",
                {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        sWorkflowName: sName,
                        sFileName: sFileName,
                    }),
                }
            );
            if (!response.ok) {
                var detail = await response.json();
                fnShowToast(
                    detail.detail || "Create failed", "error"
                );
                return;
            }
            var dictResult = await response.json();
            fnShowToast("Workflow created", "success");
            fnSelectWorkflow(
                _sSelectedContainerId, dictResult.sPath, dictResult.sName
            );
        } catch (error) {
            fnShowToast(fsSanitizeErrorForUser(error.message), "error");
        }
    }

    async function fnSelectWorkflow(sId, sWorkflowPathArg, sWorkflowName) {
        try {
            var response = await fetch(
                "/api/connect/" + sId +
                "?sWorkflowPath=" + encodeURIComponent(sWorkflowPathArg),
                { method: "POST" }
            );
            if (!response.ok) {
                var detail = await response.json();
                fnShowToast(fsSanitizeErrorForUser(
                    detail.detail || "Connection failed"), "error");
                return;
            }
            var data = await response.json();
            sContainerId = sId;
            dictWorkflow = data.dictWorkflow;
            sWorkflowPath = data.sWorkflowPath;
            _dictDashboardMode = DICT_MODE_WORKFLOW;
            dictStepStatus = {};
            dictFileExistenceCache = {};
            dictFileModTimes = {};
            setStepsWithData.clear();
            bFileCheckInProgress = false;
            bDelegatedEventsInitialized = false;
            iPreviousOutputCount = 0;
            fnStopPipelinePolling();
            fnStopFileChangePolling();
            var iStepCount = (dictWorkflow.listSteps || []).length;
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
            document.title = (_sSelectedContainerName || "Vaibify") +
                (sWorkflowName ? ": " + sWorkflowName : "");
            fnShowMainLayout();
            fnLoadSyncStatus();
            fnRenderStepList();
            fnCheckVaibifiedOnLoad();
            fnPollAllStepFiles();
            fnStartFileChangePolling();
            PipeleyenTerminal.fnCreateTab();
            fnRecoverPipelineState(sId);
        } catch (error) {
            fnShowToast(fsSanitizeErrorForUser(error.message), "error");
        }
    }

    async function fnEnterNoWorkflow(sId) {
        try {
            var response = await fetch(
                "/api/connect/" + sId,
                { method: "POST" }
            );
            if (!response.ok) {
                var detail = await response.json();
                fnShowToast(fsSanitizeErrorForUser(
                    detail.detail || "Connection failed"), "error");
                return;
            }
            sContainerId = sId;
            dictWorkflow = null;
            sWorkflowPath = null;
            _dictDashboardMode = DICT_MODE_NO_WORKFLOW;
            dictStepStatus = {};
            var elWorkflowName = document.getElementById(
                "activeWorkflowName"
            );
            elWorkflowName.textContent = "No Workflow";
            document.title = _sSelectedContainerName || "Vaibify";
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
        if (!_dictDashboardMode) return;
        var listLeftTabs = _dictDashboardMode.listLeftTabs;
        var listAllTabs = document.querySelectorAll(".left-tab");
        listAllTabs.forEach(function (elTab) {
            var bVisible = listLeftTabs.includes(elTab.dataset.panel);
            elTab.style.display = bVisible ? "" : "none";
        });
        fnReorderLeftTabs(listLeftTabs);
        fnReorderLeftPanels(listLeftTabs);
        var elDefaultTab = document.querySelector(
            '.left-tab[data-panel="' +
            _dictDashboardMode.sDefaultLeftTab + '"]'
        );
        if (elDefaultTab) elDefaultTab.click();
        fnApplyToolbarVisibility(_dictDashboardMode);
    }

    function fnShowContainerLanding() {
        document.getElementById("containerLanding").style.display = "flex";
        document.getElementById("workflowPicker").style.display = "none";
        document.getElementById("mainLayout").classList.remove("active");
        _dictDashboardMode = null;
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

    function fnDisconnect() {
        sContainerId = null;
        dictWorkflow = null;
        sWorkflowPath = null;
        iSelectedStepIndex = -1;
        setExpandedSteps.clear();
        setExpandedDeps.clear();
        setExpandedUnitTests.clear();
        dictStepStatus = {};
        _bVaibifiedShown = false;
        var elBanner = document.getElementById("vaibifiedBanner");
        if (elBanner) {
            elBanner.style.display = "none";
            elBanner.classList.remove("revealed");
        }
        if (wsPipeline) {
            wsPipeline.close();
            wsPipeline = null;
        }
        PipeleyenTerminal.fnCloseAll();
        fnShowContainerLanding();
        fnLoadContainers();
    }

    /* --- Template Resolution --- */

    function fdictBuildClientVariables() {
        if (!dictWorkflow) return {};
        var sWorkflowDir = fsGetWorkflowDirectory();
        var sRepoRoot = sWorkflowDir;
        if (sRepoRoot.endsWith("/.vaibify/workflows")) {
            sRepoRoot = sRepoRoot.replace(
                "/.vaibify/workflows", "");
        } else if (sRepoRoot.endsWith("/.vaibify")) {
            sRepoRoot = sRepoRoot.replace("/.vaibify", "");
        }
        var sPlotDir = dictWorkflow.sPlotDirectory || "Plot";
        if (sPlotDir.charAt(0) !== "/") {
            sPlotDir = sRepoRoot + "/" + sPlotDir;
        }
        var dictVars = {
            sPlotDirectory: sPlotDir,
            sRepoRoot: sRepoRoot,
            iNumberOfCores: dictWorkflow.iNumberOfCores || -1,
            sFigureType: (dictWorkflow.sFigureType || "pdf").toLowerCase(),
        };
        dictWorkflow.listSteps.forEach(function (step, iIdx) {
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

    function fsResolveTemplate(sTemplate, dictVariables) {
        return sTemplate.replace(/\{([^}]+)\}/g, function (sMatch, sToken) {
            if (dictVariables.hasOwnProperty(sToken)) {
                return String(dictVariables[sToken]);
            }
            return sMatch;
        });
    }

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
                var bWorkflowMode = _dictDashboardMode &&
                    _dictDashboardMode.sMode === "workflow";
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
        if (!sWorkflowPath) return "/workspace";
        var iLastSlash = sWorkflowPath.lastIndexOf("/");
        return iLastSlash > 0 ? sWorkflowPath.substring(0, iLastSlash) : "/workspace";
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

    function fnRenderGlobalSettings() {
        if (!dictWorkflow) return;
        var el = document.getElementById("globalSettingsPanel");
        el.innerHTML =
            '<div class="gs-row">' +
            '<span class="gs-label">Plot Dir</span>' +
            '<input class="gs-input" id="gsPlotDirectory" value="' +
            fnEscapeHtml(dictWorkflow.sPlotDirectory || "Plot") + '">' +
            '</div>' +
            '<div class="gs-row">' +
            '<span class="gs-label">Figure Type</span>' +
            '<input class="gs-input" id="gsFigureType" value="' +
            fnEscapeHtml(dictWorkflow.sFigureType || "pdf") + '">' +
            '</div>' +
            '<div class="gs-row">' +
            '<span class="gs-label">Cores</span>' +
            '<input class="gs-input" id="gsNumberOfCores" type="number" value="' +
            (dictWorkflow.iNumberOfCores || -1) + '">' +
            '</div>' +
            '<div class="gs-row">' +
            '<span class="gs-label">Tolerance</span>' +
            '<input class="gs-input" id="gsTolerance" type="range"' +
            ' min="-16" max="0" step="1" value="' +
            fsToleranceToExponent(
                dictWorkflow.fTolerance || 1e-6) +
            '" title="10^' +
            fsToleranceToExponent(
                dictWorkflow.fTolerance || 1e-6) +
            ' = ' + (dictWorkflow.fTolerance || 1e-6) + '">' +
            '</div>' +
            '<div class="gs-row">' +
            '<span class="gs-label">Poll Interval</span>' +
            '<input class="gs-input" id="gsPollInterval" type="range"' +
            ' min="1" max="60" value="' +
            (iPollIntervalMs / 1000) + '" title="' +
            (iPollIntervalMs / 1000) + ' seconds">' +
            '</div>';
        el.querySelectorAll(".gs-input").forEach(function (inp) {
            inp.addEventListener("change", fnSaveGlobalSettings);
        });
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
                dictWorkflow.fTolerance = fVal;
            });
        }
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
            var response = await fetch(
                "/api/settings/" + sContainerId,
                {
                    method: "PUT",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(dictUpdates),
                }
            );
            if (response.ok) {
                var result = await response.json();
                dictWorkflow.sPlotDirectory = result.sPlotDirectory;
                dictWorkflow.sFigureType = result.sFigureType;
                dictWorkflow.iNumberOfCores = result.iNumberOfCores;
                if (result.fTolerance !== undefined) {
                    dictWorkflow.fTolerance = result.fTolerance;
                }
                fnShowToast("Settings saved", "success");
                fnRenderStepList();
            }
        } catch (error) {
            fnShowToast("Failed to save settings", "error");
        }
    }

    /* --- Step List --- */

    function fnRenderStepList() {
        var elList = document.getElementById("listSteps");
        if (!dictWorkflow || !dictWorkflow.listSteps) {
            elList.innerHTML = "";
            return;
        }
        var dictVars = fdictBuildClientVariables();
        var sHtml = "";
        dictWorkflow.listSteps.forEach(function (step, iIndex) {
            sHtml += fsRenderStepItem(step, iIndex, dictVars);
        });
        elList.innerHTML = sHtml;
        fnBindStepEvents();
        fnScheduleFileExistenceCheck();
    }

    var dictFileExistenceCache = {};
    var iFileCheckTimer = null;
    var bFileCheckInProgress = false;
    var iInflightRequests = 0;

    function fnScheduleFileExistenceCheck() {
        if (iFileCheckTimer) return;
        iFileCheckTimer = setTimeout(function () {
            iFileCheckTimer = null;
            bFileCheckInProgress = false;
            iInflightRequests = 0;
            var iOutputCount = document.querySelectorAll(
                ".detail-item.output").length;
            console.log("[vaibify] File check: " +
                iOutputCount + " output elements found");
            fnCheckOutputFileExistence();
            fnCheckDataFileExistence();
            if (iInflightRequests === 0) {
                bFileCheckInProgress = false;
            } else {
                setTimeout(function () {
                    bFileCheckInProgress = false;
                }, 10000);
            }
        }, 200);
    }

    function fnFileCheckComplete() {
        iInflightRequests--;
        if (iInflightRequests <= 0) {
            bFileCheckInProgress = false;
        }
    }

    function fnClearRunningStatuses() {
        dictStepStatus = {};
    }

    function fnInvalidateStepFileCache(iStep) {
        var sPrefix = iStep + ":";
        Object.keys(dictFileExistenceCache).forEach(function (sKey) {
            if (sKey.indexOf(sPrefix) === 0) {
                delete dictFileExistenceCache[sKey];
            }
        });
        setStepsWithData.delete(iStep);
    }

    function fnPollAllStepFiles() {
        if (!sContainerId || !dictWorkflow) return;
        dictWorkflow.listSteps.forEach(function (step, iStep) {
            fnCheckStepDataFiles(step, iStep);
        });
    }

    function fnCheckDataFileExistence() {
        if (!sContainerId || !dictWorkflow) return;
        dictWorkflow.listSteps.forEach(function (step, iStep) {
            if (!setExpandedSteps.has(iStep)) return;
            fnCheckStepDataFiles(step, iStep);
        });
    }

    function fnCheckStepDataFiles(step, iStep) {
        if (setStepsWithData.has(iStep)) return;
        var listNecessary = flistNecessaryDataFiles(step, iStep);
        if (listNecessary.length === 0) return;
        var iPresent = 0;
        var iTotal = listNecessary.length;
        listNecessary.forEach(function (sFile) {
            var sDir = step.sDirectory || "";
            var sCacheKey = iStep + ":" + sFile;
            if (dictFileExistenceCache[sCacheKey]) {
                iPresent++;
                if (iPresent >= iTotal) {
                    setStepsWithData.add(iStep);
                    fnUpdateGenerateButton(iStep);
                }
                return;
            }
            var sUrl = "/api/figure/" + sContainerId +
                "/" + sFile + "?sWorkdir=" +
                encodeURIComponent(sDir);
            iInflightRequests++;
            fetch(sUrl, { method: "HEAD" }).then(
                function (r) {
                    if (r.ok) {
                        dictFileExistenceCache[sCacheKey] = true;
                        iPresent++;
                        if (iPresent >= iTotal) {
                            setStepsWithData.add(iStep);
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

    function fnCheckOutputFileExistence() {
        if (!sContainerId) return;
        var dictDataCounts = {};
        var dictDataPresent = {};
        document.querySelectorAll(
            '.detail-item.output'
        ).forEach(function (el) {
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
                fsGetFileCategory(iStep, sRaw, sArray) ===
                "archive";
            if (bNecessaryData) {
                dictDataCounts[iStep] =
                    (dictDataCounts[iStep] || 0) + 1;
            }
            if (dictFileExistenceCache[sCacheKey] === true) {
                fnUpdateFileStatus(el, true);
                fnTrackDataPresence(
                    iStep, bNecessaryData,
                    dictDataCounts, dictDataPresent
                );
                return;
            }
            if (dictFileExistenceCache[sCacheKey] === false) {
                fnUpdateFileStatus(el, false);
                return;
            }
            var sUrl = "/api/figure/" + sContainerId + "/" +
                sResolved;
            if (sWorkdir) {
                sUrl += "?sWorkdir=" + encodeURIComponent(sWorkdir);
            }
            iInflightRequests++;
            fetch(sUrl, { method: "HEAD" }).then(function (r) {
                if (r.ok) {
                    dictFileExistenceCache[sCacheKey] = true;
                    fnUpdateFileStatus(el, true);
                    fnTrackDataPresence(
                        iStep, bNecessaryData,
                        dictDataCounts, dictDataPresent
                    );
                } else {
                    console.warn("[vaibify] HEAD " +
                        r.status + " " + sUrl);
                    dictFileExistenceCache[sCacheKey] = false;
                    fnUpdateFileStatus(el, false);
                }
                fnFileCheckComplete();
            }).catch(function (err) {
                console.warn("[vaibify] HEAD error: " +
                    err.message + " " + sUrl);
                dictFileExistenceCache[sCacheKey] = false;
                fnUpdateFileStatus(el, false);
                fnFileCheckComplete();
            });
        });
    }

    function fnTrackDataPresence(
        iStep, bNecessaryData, dictCounts, dictPresent
    ) {
        if (!bNecessaryData) return;
        dictPresent[iStep] = (dictPresent[iStep] || 0) + 1;
        if (dictPresent[iStep] >= (dictCounts[iStep] || 0)) {
            setStepsWithData.add(iStep);
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
        var dictStep = dictWorkflow.listSteps[iStep];
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
        return dictFileExistenceCache[sCacheKey] === false;
    }

    function fbIsBinaryFile(sRaw) {
        var iDot = sRaw.lastIndexOf(".");
        if (iDot === -1) return true;
        var sExt = sRaw.substring(iDot).toLowerCase();
        return SET_BINARY_EXTENSIONS.has(sExt);
    }

    function fsInitialFileStatusClass(iStep, sArrayKey, sRaw) {
        if (fbIsBinaryFile(sRaw)) return "file-binary";
        return "file-pending";
    }

    function fsComputeStepLabel(iIndex) {
        var listSteps = dictWorkflow.listSteps;
        var bInteractive = listSteps[iIndex].bInteractive === true;
        var sPrefix = bInteractive ? "I" : "A";
        var iCount = 0;
        for (var i = 0; i <= iIndex; i++) {
            var bSameType = listSteps[i].bInteractive === bInteractive;
            if (bSameType) iCount++;
        }
        return sPrefix + String(iCount).padStart(2, "0");
    }

    function fsRenderStepItem(step, iIndex, dictVars) {
        var bInteractive = step.bInteractive === true;
        var sRunStatus = dictStepStatus[iIndex] || "";
        var sStatusClass = "";
        if (sRunStatus === "running" || sRunStatus === "queued") {
            sStatusClass = sRunStatus;
        } else if (sRunStatus === "fail") {
            sStatusClass = "fail";
        } else {
            sStatusClass = fsComputeStepDotState(step, iIndex);
        }
        var bEnabled = step.bEnabled !== false;
        var bSelected = iIndex === iSelectedStepIndex;
        var bExpanded = setExpandedSteps.has(iIndex);

        var sVerifiedBadge = "";
        if (sStatusClass === "verified") {
            sVerifiedBadge = '<img src="/static/favicon.png" ' +
                'class="vaib-verified-badge" alt="verified">';
        }

        var sStepNumber = fsComputeStepLabel(iIndex);

        var sHtml = '<div class="step-wrapper">' +
            '<div class="step-item' + (bSelected ? " selected" : "") +
            (bInteractive ? " interactive" : "") +
            '" data-index="' + iIndex + '" draggable="true">' +
            '<input type="checkbox" class="step-checkbox"' +
            (bEnabled ? " checked" : "") + ">" +
            '<span class="step-number">' +
            sStepNumber + "</span>" +
            '<span class="step-name" title="' +
            fnEscapeHtml(step.sName) + '">' +
            fnEscapeHtml(step.sName) + "</span>" +
            (dictScriptModified[iIndex] === "modified" ?
                '<span class="script-modified-badge" ' +
                'title="Scripts modified since last run">' +
                '&#9998;</span>' : '') +
            (function () {
                var listMod = (step.dictVerification || {})
                    .listModifiedFiles || [];
                if (listMod.length === 0) return '';
                var sNames = listMod.map(function (s) {
                    return s.split("/").pop();
                }).join(", ");
                return '<span class="data-modified-badge" ' +
                    'title="Modified: ' +
                    fnEscapeHtml(sNames) + '">&#9888;</span>';
            })() +
            (sStatusClass === "verified" ? "" :
                '<span class="step-status ' + sStatusClass +
                '"></span>') +
            sVerifiedBadge +
            '<span class="step-actions">' +
            '<button class="btn-icon step-edit" title="Edit">&#9998;</button>' +
            "</span></div>";

        if (!bExpanded) {
            return sHtml + '</div>';
        }

        sHtml += '<div class="step-detail expanded' +
            '" data-index="' + iIndex + '">';

        /* Directory */
        var sResolvedDir = fsResolveTemplate(step.sDirectory, dictVars);
        sHtml += '<div class="detail-label">Directory</div>';
        sHtml += '<div class="detail-field" data-view="field">' +
            fnEscapeHtml(sResolvedDir) + "</div>";
        if (!bInteractive) {
            sHtml += '<div class="detail-label plot-only-row">' +
                '<label class="plot-only-toggle">' +
                '<input type="checkbox" class="plot-only-checkbox"' +
                ' data-step="' + iIndex + '"' +
                (step.bPlotOnly !== false ? " checked" : "") + '>' +
                ' Plot only (skip data analysis)</label></div>';
        }

        if (bInteractive) {
            var bHasPlots = (step.saPlotCommands || []).length > 0;
            sHtml += '<div class="interactive-run-section">' +
                '<button class="btn btn-interactive-run" ' +
                'data-index="' + iIndex + '">' +
                '&#9654; Run in Terminal</button>';
            if (bHasPlots) {
                sHtml += ' <button class="btn btn-interactive-plots" ' +
                    'data-index="' + iIndex + '">' +
                    '&#9654; Run Plots</button>';
            }
            sHtml += '<div class="detail-note">This step requires ' +
                'human judgment. It will run in the terminal ' +
                'with X11 display forwarding.</div></div>';
        }

        /* Data Analysis Commands */
        sHtml += fsRenderSectionLabel(
            "Data Analysis Commands", iIndex, "saDataCommands"
        );
        if (step.saDataCommands) {
            step.saDataCommands.forEach(function (sCmd, iCmdIdx) {
                sHtml += fsRenderDetailItem(
                    sCmd, dictVars, "command", "saDataCommands",
                    iIndex, iCmdIdx
                );
            });
        }

        /* Data Analysis Timing */
        if ((step.saDataCommands || []).length > 0) {
            sHtml += fsRenderRunStats(step);
        }

        /* Data Files */
        sHtml += fsRenderSectionLabel(
            "Data Files", iIndex, "saDataFiles"
        );
        if (step.saDataFiles) {
            step.saDataFiles.forEach(function (sFile, iFileIdx) {
                sHtml += fsRenderDetailItem(
                    sFile, dictVars, "output", "saDataFiles",
                    iIndex, iFileIdx, sResolvedDir
                );
            });
        }

        /* Plot Commands */
        sHtml += fsRenderSectionLabel(
            "Plot Commands", iIndex, "saPlotCommands"
        );
        if (step.saPlotCommands) {
            step.saPlotCommands.forEach(function (sCmd, iCmdIdx) {
                sHtml += fsRenderDetailItem(
                    sCmd, dictVars, "command", "saPlotCommands",
                    iIndex, iCmdIdx
                );
            });
        }

        /* Plot Files */
        sHtml += fsRenderSectionLabel(
            "Plot Files", iIndex, "saPlotFiles"
        );
        if (step.saPlotFiles) {
            step.saPlotFiles.forEach(function (sFile, iFileIdx) {
                sHtml += fsRenderDetailItem(
                    sFile, dictVars, "output", "saPlotFiles",
                    iIndex, iFileIdx, sResolvedDir
                );
            });
        }

        /* Plot Timing */
        if ((step.saPlotFiles || []).length > 0) {
            var dictPlotStats = step.dictRunStats || {};
            sHtml += '<div class="run-stats">' +
                '<span class="run-stat">Plots created: ' +
                (dictPlotStats.sLastRun || "—") +
                '</span></div>';
        }

        /* Verification */
        sHtml += fsRenderVerificationBlock(step, iIndex);

        /* Discovered outputs */
        sHtml += fsRenderDiscoveredOutputs(iIndex);

        sHtml += "</div>";
        sHtml += "</div>";
        return sHtml;
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

    function fsTestCategoryLabel(sCategory) {
        var dictLabels = {
            qualitative: "Qualitative Tests",
            quantitative: "Quantitative Tests",
            integrity: "Integrity Tests",
        };
        return dictLabels[sCategory] || sCategory;
    }

    function fsVerificationStateLabel(sState) {
        var dictLabels = {
            passed: "Passed", failed: "Failed",
            untested: "Untested", error: "Error",
        };
        return dictLabels[sState] || "Untested";
    }

    function fsVerificationStateIcon(sState) {
        var dictIcons = {
            passed: "\u2713", failed: "\u2717",
            untested: "\u2014", error: "\u2717",
        };
        return dictIcons[sState] || "\u2014";
    }

    var setExpandedDeps = new Set();
    var setExpandedUnitTests = new Set();
    var setExpandedQualitative = new Set();
    var setExpandedQuantitative = new Set();
    var setExpandedIntegrity = new Set();
    var setStepsWithData = new Set();
    var setGeneratedTestsPending = new Set();
    var setGeneratingInFlight = new Set();

    function fsetGetExpandedCategory(sCategory) {
        var dictSets = {
            qualitative: setExpandedQualitative,
            quantitative: setExpandedQuantitative,
            integrity: setExpandedIntegrity,
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
        if (!dictWorkflow || !dictWorkflow.listSteps) return [];
        var step = dictWorkflow.listSteps[iStep];
        var setDeps = {};
        var listArrays = ["saDataCommands", "saPlotCommands",
            "saTestCommands", "saDataFiles", "saPlotFiles"];
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
        if (!dictWorkflow || !dictWorkflow.listSteps[iStep]) {
            return false;
        }
        if (dictVisited[iStep]) return dictVisited[iStep] === "pass";
        dictVisited[iStep] = "checking";
        var step = dictWorkflow.listSteps[iStep];
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
                dictWorkflow.listSteps[listDeps[i]]);
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

    function fsRenderVerificationBlock(step, iIndex) {
        var bInteractive = step.bInteractive === true;
        var bPlotOnly = (step.saDataCommands || []).length === 0;
        var dictVerify = fdictGetVerification(step);
        var sHtml = '<div class="detail-label">Verification</div>';
        sHtml += '<div class="verification-block" data-step="' +
            iIndex + '">';
        var listModified = dictVerify.listModifiedFiles || [];
        if (listModified.length > 0) {
            var listNames = listModified.map(function (sPath) {
                return sPath.split("/").pop();
            });
            sHtml += '<div class="output-modified-warning">' +
                '\u26A0 Modified: ' +
                fnEscapeHtml(listNames.join(", ")) + '</div>';
        }
        if (fbAnyUpstreamModified(iIndex)) {
            sHtml += '<div class="output-modified-warning">' +
                '\u26A0 Upstream step outputs changed</div>';
        }
        if (!bInteractive && !bPlotOnly) {
            var sUnitState = fsEffectiveTestState(step);
            sHtml += fsRenderVerificationRow(
                "Unit Tests", sUnitState, "unitTest", iIndex
            );
            if (setGeneratingInFlight.has(iIndex)) {
                sHtml += '<div class="unit-tests-expanded">' +
                    '<button class="btn-generate-test" disabled>' +
                    '<span class="spinner"></span> ' +
                    'Building Tests\u2026</button></div>';
            } else if (setExpandedUnitTests.has(iIndex)) {
                sHtml += fsRenderUnitTestsExpanded(step, iIndex);
            }
        }
        var sDepsState = fsComputeDepsState(iIndex);
        sHtml += fsRenderVerificationRow(
            "Dependencies", sDepsState, "deps", iIndex
        );
        if (setExpandedDeps.has(iIndex)) {
            sHtml += fsRenderDepsExpanded(iIndex);
        }
        sHtml += fsRenderVerificationRow(
            sUserName, dictVerify.sUser, "user", iIndex
        );
        sHtml += '</div>';
        return sHtml;
    }

    function fsRenderUnitTestsExpanded(step, iIndex) {
        var sHtml = '<div class="unit-tests-expanded">';
        var listCategories = [
            "qualitative", "quantitative", "integrity"];
        var bAnyTests = false;
        for (var i = 0; i < listCategories.length; i++) {
            var sCategory = listCategories[i];
            var sCatState = fsGetCategoryState(step, sCategory);
            var sLabel = fsTestCategoryLabel(sCategory);
            sHtml += fsRenderSubTestRow(
                sLabel, sCatState, sCategory, iIndex);
            var setExp = fsetGetExpandedCategory(sCategory);
            if (setExp.has(iIndex)) {
                sHtml += fsRenderSubTestExpanded(
                    step, iIndex, sCategory);
            }
            var dictTests = fdictGetTests(step);
            var sCatKey = "dict" +
                sCategory.charAt(0).toUpperCase() +
                sCategory.slice(1);
            if (((dictTests[sCatKey] || {}).saCommands ||
                []).length > 0) {
                bAnyTests = true;
            }
        }
        if (bAnyTests) {
            sHtml += '<button class="btn btn-run-all-tests" ' +
                'data-step="' + iIndex +
                '">Run All Tests</button>';
        }
        sHtml += fsRenderGenerateButton(step, iIndex);
        sHtml += '</div>';
        return sHtml;
    }

    function fsRenderSubTestRow(
        sLabel, sState, sCategory, iIndex
    ) {
        var setExp = fsetGetExpandedCategory(sCategory);
        var bExpanded = setExp.has(iIndex);
        var sTriangle = '<span class="expand-triangle">' +
            (bExpanded ? "\u25BE" : "\u25B8") + '</span> ';
        return '<div class="sub-test-row expandable" data-step="' +
            iIndex + '" data-approver="' + sCategory + '">' +
            '<span class="verification-label">' +
            sTriangle + fnEscapeHtml(sLabel) + '</span>' +
            '<span class="verification-badge state-' +
            sState + '">' +
            fsVerificationStateIcon(sState) + ' ' +
            fsVerificationStateLabel(sState) +
            '</span></div>';
    }

    function fsRenderSubTestExpanded(step, iIndex, sCategory) {
        var dictTests = fdictGetTests(step);
        var sCatKey = "dict" +
            sCategory.charAt(0).toUpperCase() +
            sCategory.slice(1);
        var dictCat = dictTests[sCatKey] || {};
        var sStandardsPath = dictCat.sStandardsPath || "";
        var sHtml = '<div class="sub-test-expanded">';
        if (sStandardsPath) {
            sHtml += '<span class="test-standards-link" ' +
                'data-step="' + iIndex +
                '" data-category="' + sCategory +
                '" data-path="' +
                fnEscapeHtml(sStandardsPath) +
                '">Standards</span>';
        }
        if ((dictCat.saCommands || []).length > 0) {
            sHtml += '<button class="btn btn-run-category" ' +
                'data-step="' + iIndex +
                '" data-category="' + sCategory +
                '">Run</button>';
        }
        sHtml += '</div>';
        return sHtml;
    }

    function fsRenderDepsExpanded(iIndex) {
        var listDeps = flistGetStepDependencies(iIndex);
        var sHtml = '<div class="deps-expanded">';
        var dictVisited = {};
        for (var i = 0; i < listDeps.length; i++) {
            var iDep = listDeps[i];
            if (iDep === iIndex) continue;
            var depStep = dictWorkflow.listSteps[iDep];
            if (!depStep) continue;
            var bPassing = fbStepFullyPassing(iDep, dictVisited);
            var sState = bPassing ? "passed" : "failed";
            var sNum = fsComputeStepLabel(iDep);
            sHtml += '<div class="dep-item">' +
                '<span class="dep-label">' + sNum + ' ' +
                fnEscapeHtml(depStep.sName) + '</span>' +
                '<span class="verification-badge state-' +
                sState + '">' +
                fsVerificationStateIcon(sState) + ' ' +
                fsVerificationStateLabel(sState) +
                '</span></div>';
        }
        sHtml += '<button class="btn btn-small btn-add-deps" ' +
            'data-step="' + iIndex + '" ' +
            'style="margin-top:6px">Add Dependencies</button>';
        sHtml += '</div>';
        return sHtml;
    }

    function fsRenderVerificationRow(
        sLabel, sState, sApprover, iIndex
    ) {
        var sClickClass = sApprover === "user" ? " clickable" :
            " expandable";
        var sTriangle = "";
        if (sApprover === "unitTest") {
            var bExpanded = setExpandedUnitTests.has(iIndex);
            sTriangle = '<span class="expand-triangle">' +
                (bExpanded ? "\u25BE" : "\u25B8") + '</span> ';
        }
        if (sApprover === "deps") {
            var bDepsExpanded = setExpandedDeps.has(iIndex);
            sTriangle = '<span class="expand-triangle">' +
                (bDepsExpanded ? "\u25BE" : "\u25B8") + '</span> ';
        }
        return '<div class="verification-row' + sClickClass +
            '" data-step="' + iIndex +
            '" data-approver="' + sApprover + '">' +
            '<span class="verification-label">' +
            sTriangle + fnEscapeHtml(sLabel) + '</span>' +
            '<span class="verification-badge state-' + sState + '">' +
            fsVerificationStateIcon(sState) + ' ' +
            fsVerificationStateLabel(sState) + '</span></div>';
    }

    function fsRenderUnitTestExpanded(step, iIndex) {
        var sHtml = '<div class="unit-test-expanded">';
        var bHasTests = (step.saTestCommands || []).length > 0;
        sHtml += fsRenderTestSection(
            "Test Commands", step.saTestCommands, iIndex, "command"
        );
        if (bHasTests) {
            sHtml += '<button class="btn btn-run-tests" ' +
                'data-step="' + iIndex + '">Run Tests</button>';
        }
        var sLogPath = (fdictGetVerification(step)).sTestLogPath;
        if (sLogPath) {
            sHtml += '<div class="test-last-run" data-log="' +
                fnEscapeHtml(sLogPath) + '">Last Run: view log</div>';
        }
        sHtml += fsRenderGenerateButton(step, iIndex);
        sHtml += '</div>';
        return sHtml;
    }

    function fsRenderGenerateButton(step, iIndex) {
        if ((step.saDataCommands || []).length === 0) return "";
        if (setGeneratingInFlight.has(iIndex)) {
            return '<button class="btn-generate-test" data-step="' +
                iIndex + '" id="btnGenTest' + iIndex +
                '" disabled>' +
                '<span class="spinner"></span> Building Tests' +
                '</button>';
        }
        var bDisabled = !setStepsWithData.has(iIndex);
        var sLabel = bDisabled ? "No Data for Tests" : "Generate Tests";
        return '<button class="btn-generate-test" data-step="' +
            iIndex + '"' +
            (bDisabled ? " disabled" : "") +
            ' id="btnGenTest' + iIndex + '">' +
            sLabel + '</button>';
    }

    function fsRenderTestSection(sLabel, listItems, iIndex, sType) {
        var sHtml = '<div class="test-section-label">' + sLabel +
            ' <button class="section-add test-add" data-step="' +
            iIndex + '" data-test-type="' + sType +
            '" title="Add">+</button></div>';
        if (!listItems || listItems.length === 0) return sHtml;
        for (var i = 0; i < listItems.length; i++) {
            var sCls = sType === "file" ?
                "test-file-item" : "test-command-item";
            sHtml += '<div class="' + sCls + '" data-step="' +
                iIndex + '" data-idx="' + i + '">' +
                '<span class="test-item-text">' +
                fnEscapeHtml(listItems[i]) + '</span>' +
                '<span class="test-item-actions">' +
                '<button class="btn-icon test-edit-cmd" ' +
                'data-step="' + iIndex + '" data-idx="' + i +
                '" title="Edit test file">&#9998;</button>' +
                '<button class="btn-icon test-delete-cmd" ' +
                'data-step="' + iIndex + '" data-idx="' + i +
                '" title="Delete test">&times;</button>' +
                '</span></div>';
        }
        return sHtml;
    }

    var sUserName = "User";

    function fnSetVerificationUserName(sName) {
        sUserName = sName || "User";
    }

    function fsComputeStepDotState(step, iIndex) {
        var dictVerify = fdictGetVerification(step);
        var sUser = dictVerify.sUser;
        var bInteractive = step.bInteractive === true;
        var bPlotOnly = (step.saDataCommands || []).length === 0;
        var listModified = dictVerify.listModifiedFiles || [];
        var bDirty = listModified.length > 0 ||
            fbAnyUpstreamModified(iIndex) ||
            dictScriptModified[iIndex] === "modified";
        var bHasData = setStepsWithData.has(iIndex) ||
            !!(step.dictRunStats || {}).sLastRun;

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

    function fsRenderFileSyncBadges(sResolved) {
        var dictSync = dictCachedSyncStatus[sResolved] || {};
        var sGithubTitle = dictSync.bGithub ?
            "Commit: " + (dictSync.sGithubCommit || "unknown") :
            "Not synced";
        return '<span class="sync-badges">' +
            fsRenderOneBadge("overleaf", dictSync.bOverleaf) +
            '<span class="sync-badge sync-badge-github ' +
            (dictSync.bGithub ? "sync-active" : "sync-inactive") +
            '" title="' + sGithubTitle + '"></span>' +
            fsRenderOneBadge("zenodo", dictSync.bZenodo) +
            '</span>';
    }

    function fsRenderRunStats(step) {
        var dictStats = step.dictRunStats || {};
        var sLastRun = dictStats.sLastRun || "";
        var sWallClock = dictStats.fWallClock !== undefined ?
            fsFormatDuration(dictStats.fWallClock) : "";
        var sCpuTime = dictStats.fCpuTime !== undefined ?
            fsFormatDuration(dictStats.fCpuTime) : "";
        return '<div class="run-stats">' +
            '<span class="run-stat">Last run: ' +
            (sLastRun || "—") + '</span>' +
            '<span class="run-stat">Wall-clock: ' +
            (sWallClock || "—") + '</span>' +
            '<span class="run-stat">CPU time: ' +
            (sCpuTime || "—") + '</span></div>';
    }

    function fsFormatDuration(fSeconds) {
        if (fSeconds < 60) return fSeconds.toFixed(1) + "s";
        var iMinutes = Math.floor(fSeconds / 60);
        var fRemainder = (fSeconds % 60).toFixed(0);
        if (iMinutes < 60) return iMinutes + "m " + fRemainder + "s";
        var iHours = Math.floor(iMinutes / 60);
        iMinutes = iMinutes % 60;
        return iHours + "h " + iMinutes + "m";
    }

    function fsRenderSectionLabel(sLabel, iStepIdx, sArrayKey) {
        return '<div class="detail-label">' +
            '<span>' + sLabel + '</span>' +
            '<button class="section-add" data-step="' + iStepIdx +
            '" data-array="' + sArrayKey +
            '" title="Add item">+</button>' +
            '</div>';
    }

    function fbIsInvalidOutputPath(sRaw, sResolved, sWorkdir) {
        if (!sResolved || sResolved.length === 0) return true;
        if (sRaw.includes("{")) return false;
        if (sResolved.startsWith("/")) return false;
        if (sWorkdir) return false;
        return true;
    }

    function fsRenderDetailItem(
        sRaw, dictVars, sType, sArrayKey, iStepIdx, iItemIdx,
        sWorkdir
    ) {
        var sResolved = fsResolveTemplate(sRaw, dictVars);
        if (sType === "output" && sWorkdir &&
            !sResolved.startsWith("/")) {
            sResolved = fsJoinPath(sWorkdir, sResolved);
        }
        var sFileClass = "";
        var bInvalid = false;
        if (sType === "output") {
            if (fbIsInvalidOutputPath(sRaw, sResolved, sWorkdir)) {
                sFileClass = " file-invalid";
                bInvalid = true;
            }
        }

        var sHtml = '<div class="detail-item ' + sType +
            '" data-step="' + iStepIdx +
            '" data-array="' + sArrayKey +
            '" data-idx="' + iItemIdx +
            '" data-raw="' + fnEscapeHtml(sRaw) +
            '" data-resolved="' + fnEscapeHtml(sResolved) +
            '" data-workdir="' + fnEscapeHtml(sWorkdir || "") +
            '" draggable="true">';

        if (sType === "output" && !bInvalid) {
            sFileClass = " " + fsInitialFileStatusClass(
                iStepIdx, sArrayKey, sRaw
            );
        }
        if ((sArrayKey === "saPlotFiles" ||
            sArrayKey === "saDataFiles") && !bInvalid) {
            var sCategory = fsGetFileCategory(
                iStepIdx, sRaw, sArrayKey
            );
            var bArchive = sCategory === "archive";
            var sFileLabel = sArrayKey === "saPlotFiles" ?
                "plot" : "data file";
            sHtml += '<span class="archive-star ' +
                (bArchive ? "active" : "inactive") +
                '" data-step="' + iStepIdx +
                '" data-file="' + fnEscapeHtml(sRaw) +
                '" data-array="' + sArrayKey +
                '" title="' +
                (bArchive ?
                    "Archive " + sFileLabel :
                    "Supporting " + sFileLabel) +
                '">' + (bArchive ? "\u2605" : "\u2606") +
                '</span>';
        }
        var sDisplayPath = fsShortenPath(sResolved, sWorkdir);
        if (bInvalid) {
            sHtml += '<div class="detail-text file-invalid' +
                '" title="Output path is not absolute">' +
                '<em>' + fnEscapeHtml(sResolved) + '</em></div>';
        } else {
            sHtml += '<div class="detail-text' + sFileClass +
                '" title="' + fnEscapeHtml(sResolved) + '">' +
                fnEscapeHtml(sDisplayPath) + '</div>';
        }

        if (sType === "output") {
            sHtml += fsRenderFileSyncBadges(sResolved);
        }
        sHtml += '<div class="detail-actions">' +
            '<button class="action-edit" title="Edit">&#9998;</button>' +
            '<button class="action-copy" title="Copy">&#9112;</button>' +
            '<button class="action-delete" title="Delete">&#10005;</button>' +
            '</div>';

        sHtml += '</div>';
        return sHtml;
    }

    function fsGetFileCategory(iStep, sFilePath, sArrayKey) {
        var dictStep = dictWorkflow.listSteps[iStep];
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
        var dictStep = dictWorkflow.listSteps[iStep];
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

    var bDelegatedEventsInitialized = false;

    function fnBindStepEvents() {
        if (bDelegatedEventsInitialized) return;
        bDelegatedEventsInitialized = true;
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

    function fnHandleDelegatedClick(event) {
        var elTarget = event.target;
        var elDetailItem = elTarget.closest(".detail-item");
        var elStepItem = elTarget.closest(".step-item");

        if (elTarget.closest(".action-edit")) {
            event.stopPropagation();
            if (elDetailItem) {
                fnInlineEditItem(
                    elDetailItem,
                    parseInt(elDetailItem.dataset.step),
                    elDetailItem.dataset.array,
                    parseInt(elDetailItem.dataset.idx)
                );
            }
            return;
        }
        if (elTarget.closest(".action-copy")) {
            event.stopPropagation();
            if (elDetailItem) {
                navigator.clipboard.writeText(
                    elDetailItem.dataset.resolved
                ).then(function () {
                    fnShowToast("Copied to clipboard", "success");
                });
            }
            return;
        }
        if (elTarget.closest(".action-delete")) {
            event.stopPropagation();
            if (elDetailItem) {
                fnDeleteDetailItem(
                    parseInt(elDetailItem.dataset.step),
                    elDetailItem.dataset.array,
                    parseInt(elDetailItem.dataset.idx)
                );
            }
            return;
        }
        if (elTarget.closest(".btn-discovered")) {
            event.stopPropagation();
            var elDiscBtn = elTarget.closest(".btn-discovered");
            var elDiscItem = elDiscBtn.closest(".discovered-item");
            fnAddDiscoveredOutput(
                parseInt(elDiscItem.dataset.step),
                elDiscItem.dataset.file,
                elDiscBtn.dataset.target
            );
            return;
        }
        if (elTarget.closest(".archive-star")) {
            event.stopPropagation();
            var elStar = elTarget.closest(".archive-star");
            fnToggleArchiveCategory(
                parseInt(elStar.dataset.step),
                elStar.dataset.file,
                elStar.dataset.array || "saPlotFiles"
            );
            return;
        }
        if (elTarget.closest(".test-add")) {
            event.stopPropagation();
            var elTestAdd2 = elTarget.closest(".test-add");
            fnAddTestItem(
                parseInt(elTestAdd2.dataset.step),
                elTestAdd2.dataset.testType
            );
            return;
        }
        if (elTarget.closest(".section-add")) {
            event.stopPropagation();
            var elAdd = elTarget.closest(".section-add");
            fnAddNewItem(
                parseInt(elAdd.dataset.step), elAdd.dataset.array
            );
            return;
        }
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
        var elVerifClickable = elTarget.closest(
            ".verification-row.clickable"
        );
        if (elVerifClickable) {
            fnCycleUserVerification(
                parseInt(elVerifClickable.dataset.step)
            );
            return;
        }
        var elSubTestRow = elTarget.closest(".sub-test-row");
        if (elSubTestRow) {
            var sSubApprover = elSubTestRow.dataset.approver;
            var iSubStep = parseInt(elSubTestRow.dataset.step);
            var setSubExp = fsetGetExpandedCategory(sSubApprover);
            if (setSubExp.has(iSubStep)) {
                setSubExp.delete(iSubStep);
            } else {
                setSubExp.add(iSubStep);
            }
            fnRenderStepList();
            return;
        }
        var elVerifExpandable = elTarget.closest(
            ".verification-row.expandable"
        );
        if (elVerifExpandable) {
            var sApprover = elVerifExpandable.dataset.approver;
            var iStep = parseInt(elVerifExpandable.dataset.step);
            if (sApprover === "unitTest") {
                fnToggleUnitTestExpand(iStep);
                return;
            }
        }
        var elVerifDeps = elTarget.closest(
            '.verification-row[data-approver="deps"]'
        );
        if (elVerifDeps) {
            fnToggleDepsExpand(
                parseInt(elVerifDeps.dataset.step)
            );
            return;
        }
        if (elTarget.closest(".test-file-item")) {
            PipeleyenFigureViewer.fnDisplayFileFromContainer(
                elTarget.closest(".test-file-item")
                    .textContent.trim()
            );
            return;
        }
        if (elTarget.closest(".test-last-run")) {
            var elLog = elTarget.closest(".test-last-run");
            PipeleyenFigureViewer.fnDisplayFileFromContainer(
                elLog.dataset.log
            );
            return;
        }
        if (elTarget.closest(".btn-generate-test")) {
            var elBtn = elTarget.closest(".btn-generate-test");
            fnGenerateTests(parseInt(elBtn.dataset.step));
            return;
        }
        if (elTarget.closest(".step-edit")) {
            PipeleyenStepEditor.fnOpenEditModal(
                parseInt(elStepItem.dataset.index)
            );
            return;
        }
        if (elTarget.closest(".btn-interactive-run")) {
            fnRunInteractiveStep(
                parseInt(elTarget.closest(
                    ".btn-interactive-run").dataset.index)
            );
            return;
        }
        if (elTarget.closest(".archive-star")) {
            var elStar = elTarget.closest(".archive-star");
            fnToggleArchiveCategory(
                parseInt(elStar.dataset.step),
                elStar.dataset.file,
                elStar.dataset.array || "saPlotFiles"
            );
            return;
        }
        if (elTarget.closest(".btn-interactive-plots")) {
            fnRunInteractivePlots(
                parseInt(elTarget.closest(
                    ".btn-interactive-plots").dataset.index)
            );
            return;
        }
        if (elTarget.closest(".btn-run-tests")) {
            fnRunStepTests(
                parseInt(elTarget.closest(
                    ".btn-run-tests").dataset.step));
            return;
        }
        if (elTarget.closest(".btn-run-all-tests")) {
            fnRunStepTests(
                parseInt(elTarget.closest(
                    ".btn-run-all-tests").dataset.step));
            return;
        }
        if (elTarget.closest(".btn-run-category")) {
            var elCatBtn = elTarget.closest(".btn-run-category");
            fnRunCategoryTests(
                parseInt(elCatBtn.dataset.step),
                elCatBtn.dataset.category);
            return;
        }
        if (elTarget.closest(".btn-add-deps")) {
            var elAddDeps = elTarget.closest(".btn-add-deps");
            fnScanDependencies(parseInt(elAddDeps.dataset.step));
            return;
        }
        if (elTarget.closest(".test-category-file")) {
            var elView = elTarget.closest(".test-category-file");
            fnViewCategoryTestFile(
                parseInt(elView.dataset.step),
                elView.dataset.category);
            return;
        }
        if (elTarget.closest(".test-standards-link")) {
            var elStandards = elTarget.closest(".test-standards-link");
            fnViewStandardsFile(
                parseInt(elStandards.dataset.step),
                elStandards.dataset.category);
            return;
        }
        if (elTarget.closest(".test-edit-cmd")) {
            var elEditCmd = elTarget.closest(".test-edit-cmd");
            fnEditTestFile(
                parseInt(elEditCmd.dataset.step),
                parseInt(elEditCmd.dataset.idx));
            return;
        }
        if (elTarget.closest(".test-delete-cmd")) {
            var elDelCmd = elTarget.closest(".test-delete-cmd");
            fnDeleteTestCommand(
                parseInt(elDelCmd.dataset.step),
                parseInt(elDelCmd.dataset.idx));
            return;
        }
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
        dictWorkflow.listSteps[iStep].bPlotOnly = bPlotOnly;
        try {
            await fetch(
                "/api/steps/" + sContainerId + "/" + iStep,
                {
                    method: "PUT",
                    headers: {"Content-Type": "application/json"},
                    body: JSON.stringify({bPlotOnly: bPlotOnly}),
                }
            );
        } catch (error) {
            fnShowToast("Save failed", "error");
        }
    }

    function fnToggleDepsExpand(iStep) {
        if (setExpandedDeps.has(iStep)) {
            setExpandedDeps.delete(iStep);
        } else {
            setExpandedDeps.add(iStep);
        }
        fnRenderStepList();
    }

    function fnToggleUnitTestExpand(iStep) {
        if (setExpandedUnitTests.has(iStep)) {
            setExpandedUnitTests.delete(iStep);
        } else {
            setExpandedUnitTests.add(iStep);
        }
        fnRenderStepList();
    }

    async function fnGenerateTests(iStep) {
        var step = dictWorkflow.listSteps[iStep];
        if (setGeneratingInFlight.has(iStep)) return;
        if (step && (step.saTestCommands || []).length > 0) {
            var bConfirmed = await new Promise(function (resolve) {
                fnShowConfirmModal(
                    "Overwrite Tests",
                    "Tests already exist for this step. " +
                    "Generate new tests will overwrite them.",
                    function () { resolve(true); },
                    function () { resolve(false); }
                );
            });
            if (!bConfirmed) return;
        }
        setGeneratingInFlight.add(iStep);
        fnRenderStepList();
        try {
            var response = await fetch(
                "/api/steps/" + sContainerId + "/" + iStep +
                "/generate-test",
                { method: "POST",
                  headers: {"Content-Type": "application/json"},
                  body: JSON.stringify({}) }
            );
            var dictResult = await response.json();
            setGeneratingInFlight.delete(iStep);
            if (dictResult.bNeedsFallback) {
                fnRenderStepList();
                fnShowConfirmModal(
                    "Claude Code Not Found",
                    "Test generation requires Claude Code, " +
                    "which is not installed in this container. " +
                    "You can use the Anthropic API instead " +
                    "(requires an API key, may incur charges).",
                    function () { fnShowApiKeyDialog(iStep); }
                );
                return;
            }
            if (!response.ok) {
                fnRenderStepList();
                fnShowToast("Test generation failed", "error");
                fnShowErrorModal(
                    "Test generation failed (HTTP " +
                    response.status + "):\n\n" +
                    (dictResult.detail || "Unknown error")
                );
                return;
            }
            if (!dictResult.bGenerated) {
                fnRenderStepList();
                fnShowToast("No tests generated", "error");
                fnShowErrorModal(
                    "Test generation failed:\n\n" +
                    (dictResult.sMessage || "No tests generated")
                );
                return;
            }
            fnHandleGeneratedTest(iStep, dictResult);
        } catch (error) {
            console.error("[vaibify] Generate tests error:", error);
            setGeneratingInFlight.delete(iStep);
            fnRenderStepList();
            fnShowErrorModal(
                "Test generation failed:\n\n" +
                (error.message || String(error))
            );
        }
    }

    var dictDiscoveredOutputs = {};

    function fnHandleDiscoveredOutputs(dictEvent) {
        var iStep = dictEvent.iStepNumber - 1;
        dictDiscoveredOutputs[iStep] = dictEvent.listDiscovered;
        fnRenderStepList();
        fnShowToast(
            "Step " + dictEvent.iStepNumber +
            ": " + dictEvent.listDiscovered.length +
            " new output(s) discovered", "success"
        );
    }

    function fsRenderDiscoveredOutputs(iStep) {
        var listDiscovered = dictDiscoveredOutputs[iStep];
        if (!listDiscovered || listDiscovered.length === 0) return "";
        var sHtml = '<div class="detail-label discovered-label">' +
            'Discovered Outputs</div>';
        for (var i = 0; i < listDiscovered.length; i++) {
            var sFile = listDiscovered[i].sFilePath;
            sHtml += '<div class="discovered-item" data-step="' +
                iStep + '" data-file="' +
                fnEscapeHtml(sFile) + '">' +
                '<span class="discovered-file">[+] ' +
                fnEscapeHtml(sFile) + '</span>' +
                '<button class="btn-discovered" ' +
                'data-target="saDataFiles">Add as data</button>' +
                '<button class="btn-discovered" ' +
                'data-target="saPlotFiles">Add as plot</button>' +
                '</div>';
        }
        return sHtml;
    }

    async function fnAddDiscoveredOutput(
        iStep, sFile, sTargetArray
    ) {
        var dictStep = dictWorkflow.listSteps[iStep];
        if (!dictStep[sTargetArray]) dictStep[sTargetArray] = [];
        dictStep[sTargetArray].push(sFile);
        var dictUpdate = {};
        dictUpdate[sTargetArray] = dictStep[sTargetArray];
        await fnSaveStepUpdate(iStep, dictUpdate);
        var listDisc = dictDiscoveredOutputs[iStep] || [];
        dictDiscoveredOutputs[iStep] = listDisc.filter(
            function (d) { return d.sFilePath !== sFile; }
        );
        fnRenderStepList();
    }

    function fnResetGenerateButton(iStep) {
        var elBtn = document.getElementById("btnGenTest" + iStep);
        if (elBtn) {
            elBtn.disabled = false;
            elBtn.innerHTML = "Generate Tests";
        }
    }

    function fnHandleGeneratedTest(iStep, dictResult) {
        var dictStep = dictWorkflow.listSteps[iStep];
        if (!dictStep.dictTests) {
            dictStep.dictTests = fdictGetTests(dictStep);
        }
        if (!dictStep.dictVerification) {
            dictStep.dictVerification = {
                sUnitTest: "untested", sUser: "untested",
            };
        }
        var listCategories = [
            "qualitative", "quantitative", "integrity"];
        var listAllCommands = [];
        var listErrors = [];
        for (var i = 0; i < listCategories.length; i++) {
            var sCategory = listCategories[i];
            var sCatKey = "dict" +
                sCategory.charAt(0).toUpperCase() +
                sCategory.slice(1);
            var dictCatResult = dictResult[sCatKey];
            if (!dictCatResult) continue;
            if (dictCatResult.sError) {
                listErrors.push(
                    fsTestCategoryLabel(sCategory));
                var sVerifKey = "s" +
                    sCategory.charAt(0).toUpperCase() +
                    sCategory.slice(1);
                dictStep.dictVerification[sVerifKey] = "error";
                continue;
            }
            dictStep.dictTests[sCatKey] = {
                saCommands: dictCatResult.saCommands || [],
                sFilePath: dictCatResult.sFilePath || "",
            };
            if (dictCatResult.sStandardsPath) {
                dictStep.dictTests[sCatKey].sStandardsPath =
                    dictCatResult.sStandardsPath;
            }
            listAllCommands = listAllCommands.concat(
                dictCatResult.saCommands || []);
        }
        dictStep.saTestCommands = listAllCommands;
        fnSaveStepUpdate(iStep, {
            dictTests: dictStep.dictTests,
            dictVerification: dictStep.dictVerification,
            saTestCommands: listAllCommands,
        });
        if (!setExpandedUnitTests.has(iStep)) {
            setExpandedUnitTests.add(iStep);
        }
        fnRenderStepList();
        var iSuccessCount = listCategories.length - listErrors.length;
        var sStepLabel = fsComputeStepLabel(iStep);
        if (listErrors.length > 0) {
            fnShowErrorModal(
                listErrors.join(", ") +
                " failed to generate.\n" +
                "Fix manually or ask a coding agent to fix.");
        }
        fnShowToast(
            sStepLabel + ": " + iSuccessCount +
            " of 3 test categories generated. Running\u2026",
            iSuccessCount === 3 ? "success" : "error");
        if (listAllCommands.length > 0) {
            fnRunStepTests(iStep);
        }
    }

    function fnShowApiKeyDialog(iStep) {
        var elModal = document.getElementById("modalApiConfirm");
        elModal.style.display = "flex";
        elModal.dataset.step = iStep;
    }

    function fnFinalizeGeneratedTest(iStep) {
        setGeneratedTestsPending.delete(iStep);
        var dictStep = dictWorkflow.listSteps[iStep];
        if (!dictStep.dictVerification) {
            dictStep.dictVerification = {
                sUnitTest: "untested", sUser: "untested",
            };
        }
        dictStep.dictVerification.sQualitative = "untested";
        dictStep.dictVerification.sQuantitative = "untested";
        dictStep.dictVerification.sIntegrity = "untested";
        dictStep.dictVerification.sUnitTest = "untested";
        fnSaveStepUpdate(iStep, {
            dictTests: dictStep.dictTests,
            dictVerification: dictStep.dictVerification,
        });
        fnRenderStepList();
    }

    async function fnCancelGeneratedTest(iStep) {
        setGeneratedTestsPending.delete(iStep);
        try {
            await fetch(
                "/api/steps/" + sContainerId + "/" + iStep +
                "/generated-test",
                { method: "DELETE" }
            );
        } catch (error) {
            fnShowToast("Delete failed", "error");
        }
        var dictStep = dictWorkflow.listSteps[iStep];
        dictStep.saTestCommands = [];
        dictStep.saTestFiles = [];
        dictStep.dictTests = {
            dictQualitative: {saCommands: [], sFilePath: ""},
            dictQuantitative: {
                saCommands: [], sFilePath: "", sStandardsPath: "",
            },
            dictIntegrity: {saCommands: [], sFilePath: ""},
            listUserTests: [],
        };
        fnRenderStepList();
    }

    async function fnRunCategoryTests(iStepIndex, sCategory) {
        try {
            var response = await fetch(
                "/api/steps/" + sContainerId + "/" + iStepIndex +
                "/run-test-category",
                { method: "POST",
                  headers: {"Content-Type": "application/json"},
                  body: JSON.stringify({sCategory: sCategory}) }
            );
            var dictResult = await response.json();
            if (!response.ok) {
                fnShowErrorModal("Test run failed: " +
                    (dictResult.detail || "Unknown error"));
                return;
            }
            fnUpdateCategoryTestState(
                iStepIndex, sCategory, dictResult);
        } catch (error) {
            fnShowErrorModal("Test run failed: " + error.message);
        }
    }

    function fnUpdateCategoryTestState(
        iStepIndex, sCategory, dictResult
    ) {
        var dictStep = dictWorkflow.listSteps[iStepIndex];
        if (!dictStep.dictVerification) {
            dictStep.dictVerification = {
                sUnitTest: "untested", sUser: "untested",
            };
        }
        var sKey = "s" + sCategory.charAt(0).toUpperCase() +
            sCategory.slice(1);
        dictStep.dictVerification[sKey] =
            dictResult.bPassed ? "passed" : "failed";
        fnComputeAggregateTestState(iStepIndex);
        fnSaveStepUpdate(iStepIndex, {
            dictVerification: dictStep.dictVerification,
        });
        fnRenderStepList();
        var sLabel = fsTestCategoryLabel(sCategory);
        fnShowToast(sLabel + ": " +
            (dictResult.bPassed ? "Passed" : "Failed"),
            dictResult.bPassed ? "success" : "error");
    }

    function fnComputeAggregateTestState(iStepIndex) {
        var dictStep = dictWorkflow.listSteps[iStepIndex];
        var dictVerify = fdictGetVerification(dictStep);
        var dictTests = fdictGetTests(dictStep);
        var listCategories = [
            "qualitative", "quantitative", "integrity"];
        var bAllPassed = true;
        var bAnyFailed = false;
        for (var i = 0; i < listCategories.length; i++) {
            var sCategory = listCategories[i];
            var sCatKey = "dict" +
                sCategory.charAt(0).toUpperCase() +
                sCategory.slice(1);
            var dictCat = dictTests[sCatKey] || {};
            if ((dictCat.saCommands || []).length === 0) continue;
            var sKey = "s" +
                sCategory.charAt(0).toUpperCase() +
                sCategory.slice(1);
            var sCatState = dictVerify[sKey] || "untested";
            if (sCatState !== "passed") bAllPassed = false;
            if (sCatState === "failed" || sCatState === "error") {
                bAnyFailed = true;
            }
        }
        if (bAnyFailed) {
            dictVerify.sUnitTest = "failed";
        } else if (bAllPassed) {
            dictVerify.sUnitTest = "passed";
        } else {
            dictVerify.sUnitTest = "untested";
        }
    }

    function fnViewCategoryTestFile(iStepIndex, sCategory) {
        var dictStep = dictWorkflow.listSteps[iStepIndex];
        var dictTests = fdictGetTests(dictStep);
        var sCatKey = "dict" + sCategory.charAt(0).toUpperCase() +
            sCategory.slice(1);
        var dictCat = dictTests[sCatKey] || {};
        var sFilePath = dictCat.sFilePath || "";
        if (!sFilePath) {
            fnShowToast("No test file for this category", "error");
            return;
        }
        var sDir = dictStep.sDirectory || "";
        PipeleyenFigureViewer.fnDisplayInNextViewer(sFilePath, sDir);
    }

    function fnViewStandardsFile(iStepIndex, sCategory) {
        var dictStep = dictWorkflow.listSteps[iStepIndex];
        var dictTests = fdictGetTests(dictStep);
        var sCatKey = "dict" + sCategory.charAt(0).toUpperCase() +
            sCategory.slice(1);
        var dictCat = dictTests[sCatKey] || {};
        var sStandardsPath = dictCat.sStandardsPath || "";
        if (!sStandardsPath) {
            fnShowToast("No standards file for this category", "error");
            return;
        }
        var sDir = dictStep.sDirectory || "";
        PipeleyenFigureViewer.fnDisplayInNextViewer(
            sStandardsPath, sDir);
    }

    function fnAddTestItem(iStep, sType) {
        if (sType === "user") {
            fnShowInputModal(
                "Test name",
                "e.g. Check convergence tolerance",
                function (sValue) {
                    _fnSaveUserTest(iStep, sValue);
                }
            );
            return;
        }
        var sLabel = sType === "file" ?
            "Test file path" : "Test command";
        var sPlaceholder = sType === "file" ?
            "e.g. test_step01.py" : "e.g. pytest test_step01.py";
        fnShowInputModal(sLabel, sPlaceholder, function (sValue) {
            _fnSaveTestItem(iStep, sType, sValue);
        });
    }

    async function _fnSaveTestItem(iStep, sType, sValue) {
        var dictStep = dictWorkflow.listSteps[iStep];
        var sKey = sType === "file" ?
            "saTestFiles" : "saTestCommands";
        if (!dictStep[sKey]) dictStep[sKey] = [];
        dictStep[sKey].push(sValue.trim());
        var dictUpdate = {};
        dictUpdate[sKey] = dictStep[sKey];
        await fnSaveStepUpdate(iStep, dictUpdate);
        fnRenderStepList();
    }

    async function _fnSaveUserTest(iStep, sName) {
        var dictStep = dictWorkflow.listSteps[iStep];
        if (!dictStep.dictTests) {
            dictStep.dictTests = fdictGetTests(dictStep);
        }
        if (!dictStep.dictTests.listUserTests) {
            dictStep.dictTests.listUserTests = [];
        }
        dictStep.dictTests.listUserTests.push({
            sName: sName.trim(),
            sCommand: "",
            sFilePath: "",
        });
        await fnSaveStepUpdate(iStep, {
            dictTests: dictStep.dictTests,
        });
        fnRenderStepList();
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
            await fetch(
                "/api/steps/" + sContainerId + "/" + iStep,
                {
                    method: "PUT",
                    headers: {"Content-Type": "application/json"},
                    body: JSON.stringify(dictUpdate),
                }
            );
        } catch (error) {
            fnShowToast("Save failed", "error");
        }
    }

    async function fnCycleUserVerification(iStep) {
        var dictStep = dictWorkflow.listSteps[iStep];
        var dictVerify = fdictGetVerification(dictStep);
        var listStates = [
            "untested", "passed", "failed", "error"
        ];
        var iCurrent = listStates.indexOf(dictVerify.sUser);
        var iNext = (iCurrent + 1) % listStates.length;
        dictVerify.sUser = listStates[iNext];
        dictStep.dictVerification = dictVerify;
        try {
            await fetch(
                "/api/steps/" + sContainerId + "/" + iStep,
                {
                    method: "PUT",
                    headers: {"Content-Type": "application/json"},
                    body: JSON.stringify({
                        dictVerification: dictVerify,
                    }),
                }
            );
        } catch (error) {
            fnShowToast("Save failed", "error");
        }
        fnRenderStepList();
        fnCheckVaibified();
    }

    var _bVaibifiedShown = false;

    function fbIsWorkflowFullyVerified() {
        if (!dictWorkflow || !dictWorkflow.listSteps) return false;
        var listSteps = dictWorkflow.listSteps;
        if (listSteps.length === 0) return false;
        for (var i = 0; i < listSteps.length; i++) {
            var step = listSteps[i];
            if (step.bEnabled === false) continue;
            var dictVerify = fdictGetVerification(step);
            if (dictVerify.sUser !== "passed") return false;
        }
        return true;
    }

    function fnCheckVaibified() {
        if (!fbIsWorkflowFullyVerified()) return;
        var elBanner = document.getElementById("vaibifiedBanner");
        if (!elBanner) return;
        if (_bVaibifiedShown) return;
        _bVaibifiedShown = true;
        elBanner.style.display = "inline";
        fnSpawnSparkles(elBanner);
        fnPlayWhooshSound();
        setTimeout(function () {
            elBanner.classList.add("revealed");
        }, 1500);
    }

    function fnCheckVaibifiedOnLoad() {
        if (!fbIsWorkflowFullyVerified()) return;
        var elBanner = document.getElementById("vaibifiedBanner");
        if (!elBanner) return;
        _bVaibifiedShown = true;
        elBanner.style.display = "inline";
        elBanner.style.animation = "none";
        elBanner.classList.add("revealed");
    }

    function fnSpawnSparkles(elAnchor) {
        var elContainer = document.createElement("div");
        elContainer.className = "sparkle-container";
        elAnchor.parentElement.appendChild(elContainer);
        var listColors = [
            "#ffffff", "var(--color-pale-blue)", "#ffd700"
        ];
        var iCount = 18;
        for (var i = 0; i < iCount; i++) {
            var elSparkle = document.createElement("span");
            elSparkle.className = "sparkle-particle";
            var iSize = 3 + Math.random() * 2;
            elSparkle.style.width = iSize + "px";
            elSparkle.style.height = iSize + "px";
            elSparkle.style.background =
                listColors[i % listColors.length];
            elSparkle.style.top =
                (Math.random() * 100) + "%";
            elSparkle.style.animationDelay =
                (Math.random() * 0.8) + "s";
            elSparkle.style.animationDuration =
                (0.8 + Math.random() * 0.6) + "s";
            elContainer.appendChild(elSparkle);
        }
        setTimeout(function () {
            elContainer.remove();
        }, 2500);
    }

    function fnPlayWhooshSound() {
        try {
            var audioContext = new (window.AudioContext ||
                window.webkitAudioContext)();
            var fDuration = 0.5;
            var oscillator = audioContext.createOscillator();
            var gainNode = audioContext.createGain();
            oscillator.type = "sine";
            oscillator.frequency.setValueAtTime(
                800, audioContext.currentTime);
            oscillator.frequency.exponentialRampToValueAtTime(
                200, audioContext.currentTime + fDuration);
            gainNode.gain.setValueAtTime(
                0, audioContext.currentTime);
            gainNode.gain.linearRampToValueAtTime(
                0.08, audioContext.currentTime + 0.05);
            gainNode.gain.linearRampToValueAtTime(
                0.06, audioContext.currentTime + fDuration * 0.7);
            gainNode.gain.linearRampToValueAtTime(
                0, audioContext.currentTime + fDuration);
            var iNoiseLength = audioContext.sampleRate * fDuration;
            var noiseBuffer = audioContext.createBuffer(
                1, iNoiseLength, audioContext.sampleRate);
            var daNoiseData = noiseBuffer.getChannelData(0);
            for (var i = 0; i < iNoiseLength; i++) {
                daNoiseData[i] = (Math.random() * 2 - 1) * 0.02;
            }
            var noiseSource = audioContext.createBufferSource();
            noiseSource.buffer = noiseBuffer;
            var noiseGain = audioContext.createGain();
            noiseGain.gain.setValueAtTime(
                0, audioContext.currentTime);
            noiseGain.gain.linearRampToValueAtTime(
                0.3, audioContext.currentTime + 0.05);
            noiseGain.gain.linearRampToValueAtTime(
                0, audioContext.currentTime + fDuration);
            oscillator.connect(gainNode);
            gainNode.connect(audioContext.destination);
            noiseSource.connect(noiseGain);
            noiseGain.connect(audioContext.destination);
            oscillator.start(audioContext.currentTime);
            oscillator.stop(audioContext.currentTime + fDuration);
            noiseSource.start(audioContext.currentTime);
            noiseSource.stop(audioContext.currentTime + fDuration);
        } catch (error) {
            /* Web Audio API not available */
        }
    }

    /* --- Detail Item Actions --- */

    function fnInlineEditItem(el, iStep, sArray, iIdx) {
        var sRaw = dictWorkflow.listSteps[iStep][sArray][iIdx];
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
                dictWorkflow.listSteps[iStep][sArray][iIdx] = sNewValue;
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
        var sValue = dictWorkflow.listSteps[iStep][sArray][iIdx];
        fnShowConfirmModal("Delete Item", sValue, function () {
            _fnExecuteDeleteItem(iStep, sArray, iIdx, sValue);
        });
    }

    async function _fnExecuteDeleteItem(iStep, sArray, iIdx, sValue) {
        dictWorkflow.listSteps[iStep][sArray].splice(iIdx, 1);
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
        var sValue = dictWorkflow.listSteps[iSource][sArray].splice(
            dictDrag.iIdx, 1
        )[0];
        if (!dictWorkflow.listSteps[iTargetStep][sArray]) {
            dictWorkflow.listSteps[iTargetStep][sArray] = [];
        }
        dictWorkflow.listSteps[iTargetStep][sArray].unshift(sValue);
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
        setExpandedSteps.add(iTargetStep);
        fnRenderStepList();
        fnHighlightItem(iTargetStep, sArray, 0);
        fnShowToast(
            "Moved to " + dictWorkflow.listSteps[iTargetStep].sName,
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
        if (!dictWorkflow.listSteps[iStep][sArrayKey]) {
            dictWorkflow.listSteps[iStep][sArrayKey] = [];
        }
        dictWorkflow.listSteps[iStep][sArrayKey].push(sValue);
        fnPushUndo({
            sAction: "add",
            iStep: iStep,
            sArray: sArrayKey,
            iIdx: dictWorkflow.listSteps[iStep][sArrayKey].length - 1,
            sValue: sValue,
        });
        await fnSaveStepArray(iStep, sArrayKey, true);
        fnRenderStepList();
        fnShowToast("Item added", "success");
    }

    /* --- Undo Stack --- */

    function fnPushUndo(dictAction) {
        listUndoStack.push(dictAction);
        if (listUndoStack.length > I_MAX_UNDO) {
            listUndoStack.shift();
        }
    }

    async function fnUndo() {
        if (listUndoStack.length === 0) {
            fnShowToast("Nothing to undo", "error");
            return;
        }
        var dictAction = listUndoStack.pop();
        if (dictAction.sAction === "add") {
            dictWorkflow.listSteps[dictAction.iStep][dictAction.sArray]
                .splice(dictAction.iIdx, 1);
            await fnSaveStepArray(dictAction.iStep, dictAction.sArray);
        } else if (dictAction.sAction === "delete") {
            dictWorkflow.listSteps[dictAction.iStep][dictAction.sArray]
                .splice(dictAction.iIdx, 0, dictAction.sValue);
            await fnSaveStepArray(dictAction.iStep, dictAction.sArray);
        } else if (dictAction.sAction === "move") {
            var sValue = dictWorkflow.listSteps[dictAction.iTargetStep][
                dictAction.sArray
            ].splice(dictAction.iTargetIdx, 1)[0];
            if (!dictWorkflow.listSteps[dictAction.iStep][dictAction.sArray]) {
                dictWorkflow.listSteps[dictAction.iStep][dictAction.sArray] = [];
            }
            dictWorkflow.listSteps[dictAction.iStep][dictAction.sArray]
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
        dictUpdate[sArray] = dictWorkflow.listSteps[iStep][sArray];
        try {
            await fetch(
                "/api/steps/" + sContainerId + "/" + iStep,
                {
                    method: "PUT",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(dictUpdate),
                }
            );
        } catch (error) {
            fnShowToast("Save failed", "error");
        }
        if (sArray === "saDataCommands" && bScanDeps) {
            fnScanDependencies(iStep);
        }
    }

    async function fnScanDependencies(iStep) {
        var dictStep = dictWorkflow.listSteps[iStep];
        var saCommands = dictStep.saDataCommands || [];
        if (saCommands.length === 0) {
            return;
        }
        try {
            var responseHttp = await fetch(
                "/api/steps/" + sContainerId + "/" + iStep +
                "/scan-dependencies",
                {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        saDataCommands: saCommands,
                    }),
                }
            );
            var dictResult = await responseHttp.json();
            fnShowDependencyModal(iStep, dictResult);
        } catch (error) {
            fnShowToast("Dependency scan failed", "error");
        }
    }

    function fsRenderDetectedSection(listSuggestions) {
        if (listSuggestions.length === 0) return "";
        var sHtml = '<div class="dependency-section-title">' +
            'Detected Dependencies</div>';
        for (var i = 0; i < listSuggestions.length; i++) {
            var dictSugg = listSuggestions[i];
            sHtml += '<div class="dependency-suggestion">' +
                '<input type="checkbox" checked ' +
                'data-source="detected" data-dep-index="' + i +
                '" class="dependency-checkbox">' +
                '<span class="dependency-step-badge">' +
                fnEscapeHtml(dictSugg.sSourceStepName) +
                '</span> ' +
                '<span>' + fnEscapeHtml(dictSugg.sFileName) +
                '</span> ' +
                '<span style="color:var(--text-secondary)">' +
                fnEscapeHtml(dictSugg.sLoadFunction) +
                ' (line ' + dictSugg.iLineNumber + ')' +
                '</span></div>';
        }
        return sHtml;
    }

    function fsRenderPossibleSection(listUnmatched) {
        if (listUnmatched.length === 0) return "";
        var sHtml = '<div class="dependency-section-title">' +
            'Possible Dependencies</div>';
        for (var j = 0; j < listUnmatched.length; j++) {
            var dictFile = listUnmatched[j];
            sHtml += '<div class="dependency-unmatched">' +
                '<input type="checkbox" data-source="possible" ' +
                'data-unmatched-idx="' + j +
                '" class="dependency-checkbox">' +
                '<span>' + fnEscapeHtml(dictFile.sFileName) +
                '</span> ' +
                '<span style="color:var(--text-secondary)">' +
                fnEscapeHtml(dictFile.sLoadFunction) +
                ' (line ' + dictFile.iLineNumber + ')' +
                '</span></div>';
        }
        return sHtml;
    }

    function fsRenderManualSection() {
        return '<div class="dependency-section-title">' +
            'Manual Dependencies</div>' +
            '<div id="listManualDeps"></div>' +
            '<div class="dependency-browser-row">' +
            '<button class="btn btn-small" id="btnBrowseDep">' +
            'Browse</button>' +
            '</div>' +
            '<div id="depFileBrowser" class="dep-file-browser" ' +
            'style="display:none"></div>';
    }

    function fsRenderSelectionStep(dictResult) {
        var sHtml = '<h2>Dependency Detection' +
            '<span class="dep-step-indicator">Step 1 of 2</span></h2>';
        var listSuggestions = dictResult.listSuggestions || [];
        var listUnmatched = dictResult.listUnmatchedFiles || [];
        sHtml += fsRenderDetectedSection(listSuggestions);
        sHtml += fsRenderPossibleSection(listUnmatched);
        sHtml += fsRenderManualSection();
        sHtml += '<div class="modal-actions">' +
            '<button class="btn" id="btnDepSkip">Skip</button>' +
            '<button class="btn btn-primary" ' +
            'id="btnDepNext">Next</button></div>';
        return sHtml;
    }

    function flistCollectCheckedDeps(elModal, dictResult) {
        var listChecked = [];
        var listBoxes = elModal.querySelectorAll(
            ".dependency-checkbox:checked");
        for (var k = 0; k < listBoxes.length; k++) {
            var elBox = listBoxes[k];
            var sSource = elBox.getAttribute("data-source");
            if (sSource === "detected") {
                var iIdx = parseInt(
                    elBox.getAttribute("data-dep-index"), 10);
                listChecked.push(dictResult.listSuggestions[iIdx]);
            } else if (sSource === "possible") {
                var iUIdx = parseInt(
                    elBox.getAttribute("data-unmatched-idx"), 10);
                var dictFile = dictResult.listUnmatchedFiles[iUIdx];
                var sResolved = fsResolvePathToTemplate(
                    dictFile.sFileName);
                listChecked.push({
                    sFileName: dictFile.sFileName,
                    sSourceStepName: "Manual",
                    sTemplateVariable: sResolved,
                });
            } else if (sSource === "manual") {
                var sPath = elBox.getAttribute("data-file-path");
                var sTemplate = fsResolvePathToTemplate(sPath);
                listChecked.push({
                    sFileName: sPath.split("/").pop(),
                    sSourceStepName: "Manual",
                    sTemplateVariable: sTemplate,
                });
            }
        }
        return listChecked;
    }

    function fsSourceLabel(sSource) {
        if (sSource === "detected") return "Detected";
        if (sSource === "possible") return "Possible";
        return "Manual";
    }

    function fsRenderConfirmStep(listChecked) {
        var sHtml = '<h2>Confirm Dependencies' +
            '<span class="dep-step-indicator">Step 2 of 2</span></h2>';
        if (listChecked.length === 0) {
            sHtml += '<p style="color:var(--text-secondary)">' +
                'No dependencies selected.</p>';
        }
        for (var i = 0; i < listChecked.length; i++) {
            var dictDep = listChecked[i];
            sHtml += '<div class="dependency-suggestion">' +
                '<input type="checkbox" checked ' +
                'data-confirm-index="' + i +
                '" class="dependency-checkbox">' +
                '<span class="dependency-step-badge">' +
                fnEscapeHtml(dictDep.sSourceStepName) +
                '</span> ' +
                '<span>' +
                fnEscapeHtml(dictDep.sFileName) + '</span> ' +
                '<span style="color:var(--text-secondary)">' +
                fnEscapeHtml(fsSourceLabel(
                    dictDep._sSource || "detected")) +
                '</span></div>';
        }
        sHtml += '<div class="modal-actions">' +
            '<button class="btn" id="btnDepBack">Go Back</button>' +
            '<button class="btn btn-primary" ' +
            'id="btnDepConfirm">Confirm</button></div>';
        return sHtml;
    }

    function fbDepAlreadyListed(sFilePath, dictResult, elModal) {
        var sBasename = sFilePath.split("/").pop();
        var listSugg = dictResult.listSuggestions || [];
        for (var i = 0; i < listSugg.length; i++) {
            if (listSugg[i].sFileName === sBasename) return true;
        }
        var listUnm = dictResult.listUnmatchedFiles || [];
        for (var j = 0; j < listUnm.length; j++) {
            if (listUnm[j].sFileName === sBasename) return true;
        }
        var listManual = elModal.querySelectorAll(
            '[data-source="manual"]');
        for (var m = 0; m < listManual.length; m++) {
            if (listManual[m].getAttribute("data-file-path") ===
                sFilePath) return true;
        }
        return false;
    }

    function fnAddManualDepRow(elList, sFilePath) {
        var sBasename = sFilePath.split("/").pop();
        var elRow = document.createElement("div");
        elRow.className = "dependency-suggestion";
        elRow.innerHTML =
            '<input type="checkbox" checked data-source="manual" ' +
            'data-file-path="' + fnEscapeHtml(sFilePath) +
            '" class="dependency-checkbox">' +
            '<span>' + fnEscapeHtml(sBasename) + '</span> ' +
            '<span style="color:var(--text-secondary)">' +
            fnEscapeHtml(sFilePath) + '</span>' +
            '<button class="btn btn-small btn-remove-manual" ' +
            'style="margin-left:auto;padding:2px 8px;' +
            'font-size:12px">Remove</button>';
        elList.appendChild(elRow);
    }

    function fsRenderBrowserEntries(listEntries) {
        var sHtml = '';
        for (var i = 0; i < listEntries.length; i++) {
            var dictEntry = listEntries[i];
            var sIcon = dictEntry.bIsDirectory ? "\uD83D\uDCC1" :
                "\uD83D\uDCC4";
            var sClass = dictEntry.bIsDirectory ?
                "dep-browser-dir" : "dep-browser-file";
            sHtml += '<div class="' + sClass +
                '" data-path="' + fnEscapeHtml(dictEntry.sPath) +
                '" data-is-dir="' + dictEntry.bIsDirectory + '">' +
                sIcon + ' ' +
                fnEscapeHtml(dictEntry.sName) + '</div>';
        }
        return sHtml;
    }

    function fsRenderBreadcrumb(sCurrentPath) {
        var listParts = sCurrentPath.split("/").filter(
            function (s) { return s; });
        var sHtml = '<span class="dep-breadcrumb-part" ' +
            'data-path="/">/</span>';
        var sBuilt = "";
        for (var i = 0; i < listParts.length; i++) {
            sBuilt += "/" + listParts[i];
            sHtml += '<span class="dep-breadcrumb-sep">/</span>' +
                '<span class="dep-breadcrumb-part" data-path="' +
                fnEscapeHtml(sBuilt) + '">' +
                fnEscapeHtml(listParts[i]) + '</span>';
        }
        return '<div class="dep-breadcrumb">' + sHtml + '</div>';
    }

    async function fnLoadBrowserDirectory(elBrowser, sPath) {
        elBrowser.style.display = "block";
        elBrowser.innerHTML = '<div class="dep-browser-loading">' +
            'Loading...</div>';
        try {
            var sUrl = "/api/files/" + sContainerId + sPath;
            var resp = await fetch(sUrl);
            var listEntries = await resp.json();
            elBrowser.innerHTML = fsRenderBreadcrumb(sPath) +
                '<div class="dep-browser-list">' +
                fsRenderBrowserEntries(listEntries) + '</div>';
            elBrowser.setAttribute("data-current-path", sPath);
        } catch (error) {
            elBrowser.innerHTML = '<div class="dep-browser-loading">' +
                'Failed to load directory</div>';
        }
    }

    function fnShowDependencyModal(iStep, dictResult) {
        var elExisting = document.getElementById("modalDependency");
        if (elExisting) elExisting.remove();
        var elModal = document.createElement("div");
        elModal.id = "modalDependency";
        elModal.className = "modal-overlay";
        elModal.style.display = "flex";
        fnRenderDepModalStep1(elModal, dictResult);
        document.body.appendChild(elModal);
        fnAttachDepModalEvents(elModal, iStep, dictResult);
    }

    function fnRenderDepModalStep1(elModal, dictResult) {
        elModal.innerHTML = '<div class="modal">' +
            fsRenderSelectionStep(dictResult) + '</div>';
    }

    function fnAttachDepModalEvents(elModal, iStep, dictResult) {
        elModal.addEventListener("click", function (event) {
            var elTarget = event.target;
            if (elTarget.id === "btnDepSkip") {
                elModal.remove();
                return;
            }
            if (elTarget.id === "btnDepNext") {
                fnHandleDepNext(elModal, dictResult);
                return;
            }
            if (elTarget.id === "btnDepBack") {
                fnRenderDepModalStep1(elModal, dictResult);
                return;
            }
            if (elTarget.id === "btnDepConfirm") {
                fnHandleDepConfirm(elModal, iStep);
                return;
            }
            if (elTarget.id === "btnBrowseDep") {
                fnHandleDepBrowse(elModal);
                return;
            }
            if (elTarget.classList.contains("btn-remove-manual")) {
                elTarget.closest(".dependency-suggestion").remove();
                return;
            }
            fnHandleBrowserClick(elTarget, elModal, dictResult);
        });
    }

    function fnHandleDepBrowse(elModal) {
        var elBrowser = elModal.querySelector("#depFileBrowser");
        if (!elBrowser) return;
        if (elBrowser.style.display === "block") {
            elBrowser.style.display = "none";
            return;
        }
        fnLoadBrowserDirectory(elBrowser, "/workspace");
    }

    function fnHandleBrowserClick(elTarget, elModal, dictResult) {
        var elEntry = elTarget.closest(
            ".dep-browser-dir, .dep-browser-file");
        if (elEntry) {
            var sPath = elEntry.getAttribute("data-path");
            var bIsDir = elEntry.getAttribute("data-is-dir") ===
                "true";
            var elBrowser = elModal.querySelector("#depFileBrowser");
            if (bIsDir) {
                fnLoadBrowserDirectory(elBrowser, sPath);
            } else {
                fnHandleFileSelection(elModal, sPath, dictResult);
            }
            return;
        }
        var elCrumb = elTarget.closest(".dep-breadcrumb-part");
        if (elCrumb) {
            var sCrumbPath = elCrumb.getAttribute("data-path");
            var elBr = elModal.querySelector("#depFileBrowser");
            fnLoadBrowserDirectory(elBr, sCrumbPath);
        }
    }

    function fnHandleFileSelection(elModal, sPath, dictResult) {
        if (fbDepAlreadyListed(sPath, dictResult, elModal)) {
            fnShowToast("Already listed as a dependency", "info");
            return;
        }
        var elList = elModal.querySelector("#listManualDeps");
        if (elList) fnAddManualDepRow(elList, sPath);
    }

    function fnHandleDepNext(elModal, dictResult) {
        var listChecked = flistCollectCheckedDeps(
            elModal, dictResult);
        for (var i = 0; i < listChecked.length; i++) {
            if (!listChecked[i]._sSource) {
                listChecked[i]._sSource = "detected";
            }
        }
        var elInner = elModal.querySelector(".modal");
        elInner.innerHTML = fsRenderConfirmStep(listChecked);
        elModal._listPendingDeps = listChecked;
    }

    function fnHandleDepConfirm(elModal, iStep) {
        var listPending = elModal._listPendingDeps || [];
        var listFinal = [];
        var listBoxes = elModal.querySelectorAll(
            ".dependency-checkbox:checked");
        for (var i = 0; i < listBoxes.length; i++) {
            var iIdx = parseInt(
                listBoxes[i].getAttribute("data-confirm-index"), 10);
            if (!isNaN(iIdx) && listPending[iIdx]) {
                listFinal.push(listPending[iIdx]);
            }
        }
        elModal.remove();
        if (listFinal.length > 0) {
            fnApplyDependencies(iStep, listFinal);
        }
    }

    function fsResolvePathToTemplate(sPath) {
        var sBasename = sPath.split("/").pop();
        var sStem = sBasename.replace(/\.[^.]+$/, "");
        if (!sStem) return sPath;
        var listSteps = dictWorkflow.listSteps || [];
        for (var i = 0; i < listSteps.length; i++) {
            var saFiles = (listSteps[i].saDataFiles || []).concat(
                listSteps[i].saPlotFiles || []
            );
            for (var j = 0; j < saFiles.length; j++) {
                var sUpBase = saFiles[j].split("/").pop();
                var sUpStem = sUpBase.replace(/\.[^.]+$/, "");
                if (sUpStem === sStem) {
                    var iStepNumber = i + 1;
                    var sStepLabel = "Step" +
                        String(iStepNumber).padStart(2, "0");
                    return "{" + sStepLabel + "." + sStem + "}";
                }
            }
        }
        return sPath;
    }

    function flistFilterNewTokens(listTokens, saCommands) {
        var sJoined = saCommands.join(" ");
        var listNew = [];
        for (var i = 0; i < listTokens.length; i++) {
            var sToken = listTokens[i];
            if (!/^\{Step\d+\./.test(sToken)) continue;
            if (sJoined.indexOf(sToken) === -1) {
                listNew.push(sToken);
            }
        }
        return listNew;
    }

    async function fnApplyDependencies(iStep, listChecked) {
        var dictStep = dictWorkflow.listSteps[iStep];
        var saCommands = dictStep.saDataCommands || [];

        var listDepTokens = [];
        for (var i = 0; i < listChecked.length; i++) {
            listDepTokens.push(listChecked[i].sTemplateVariable);
        }
        var listNew = flistFilterNewTokens(listDepTokens, saCommands);
        if (listNew.length === 0 || saCommands.length === 0) {
            fnShowToast("No new dependencies to add", "info");
            return;
        }
        var sDepComment = "  # " + listNew.join(" ");
        var iTarget = saCommands.length - 1;
        saCommands[iTarget] = saCommands[iTarget].replace(
            /\s*#\s*\{Step\d+\..*$/, ""
        ) + sDepComment;
        dictStep.saDataCommands = saCommands;
        await fnSaveDependencyCommands(iStep, saCommands);
        fnShowToast(listNew.length + " dependencies added", "success");
    }

    async function fnSaveDependencyCommands(iStep, saCommands) {
        var dictUpdate = { saDataCommands: saCommands };
        try {
            await fetch(
                "/api/steps/" + sContainerId + "/" + iStep,
                {
                    method: "PUT",
                    headers: {
                        "Content-Type": "application/json",
                    },
                    body: JSON.stringify(dictUpdate),
                }
            );
            fnRenderStepList();
        } catch (error) {
            fnShowToast("Failed to save dependencies", "error");
        }
    }

    /* --- Step Expand/Collapse --- */

    function fnToggleStepExpand(iIndex) {
        if (setExpandedSteps.has(iIndex)) {
            setExpandedSteps.delete(iIndex);
        } else {
            setExpandedSteps.add(iIndex);
        }
        iSelectedStepIndex = iIndex;
        fnRenderStepList();
    }

    async function fnToggleStepEnabled(iIndex, bEnabled) {
        try {
            await fetch(
                "/api/steps/" + sContainerId + "/" + iIndex,
                {
                    method: "PUT",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ bEnabled: bEnabled }),
                }
            );
            dictWorkflow.listSteps[iIndex].bEnabled = bEnabled;
        } catch (error) {
            fnShowToast("Failed to update step", "error");
        }
    }

    async function fnReorderStep(iFromIndex, iToIndex) {
        try {
            var response = await fetch(
                "/api/steps/" + sContainerId + "/reorder",
                {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        iFromIndex: iFromIndex,
                        iToIndex: iToIndex,
                    }),
                }
            );
            if (response.ok) {
                var result = await response.json();
                dictWorkflow.listSteps = result.listSteps;
                fnRenderStepList();
                fnShowToast(
                    "Step reordered (references renumbered)",
                    "success"
                );
            }
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
            btnValidateReferences: fnValidateReferences,
            btnOverleafPush: function () { fnOpenPushModal("overleaf"); },
            btnGithubPush: function () { fnOpenPushModal("github"); },
            btnZenodoArchive: function () { fnOpenPushModal("zenodo"); },
            btnShowDag: fnShowDag,
            btnVsCode: fnOpenVsCode,
            btnMonitor: function () {},
            btnResetLayout: fnResetLayout,
            btnAdminContainers: fnDisconnect,
            btnAdminWorkflows: function () {
                if (sContainerId) fnConnectToContainer(sContainerId);
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
        iPollIntervalMs = iSeconds * 1000;
        var elSlider = document.getElementById("gsPollInterval");
        if (elSlider) elSlider.title = iSeconds + " seconds";
        fnStartFileChangePolling();
    }

    /* --- Sync Push Modal --- */

    async function fnShowDag() {
        if (!sContainerId) return;
        fnShowToast("Generating dependency graph...", "success");
        try {
            var response = await fetch(
                "/api/workflow/" + sContainerId + "/dag"
            );
            if (!response.ok) throw new Error("DAG failed");
            var sSvgText = await response.text();
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
        }
        elViewport.appendChild(elContainer);
    }

    var dictCachedSyncStatus = {};
    var sPushService = "";

    async function fnLoadSyncStatus() {
        if (!sContainerId) return;
        try {
            var response = await fetch(
                "/api/sync/" + sContainerId + "/status"
            );
            dictCachedSyncStatus = await response.json();
        } catch (error) {
            dictCachedSyncStatus = {};
        }
    }

    async function fnOpenPushModal(sService) {
        if (!sContainerId) return;
        var response = await fetch(
            "/api/sync/" + sContainerId + "/check/" + sService
        );
        var dictResult = await response.json();
        if (!dictResult.bConnected) {
            fnShowConnectionSetup(sService);
            return;
        }
        sPushService = sService;
        fnPopulatePushModal(sService);
    }

    async function fnPopulatePushModal(sService) {
        var response = await fetch(
            "/api/sync/" + sContainerId + "/files"
        );
        var listFiles = await response.json();
        var dictNames = {
            overleaf: "Overleaf", zenodo: "Zenodo",
            github: "GitHub",
        };
        document.getElementById("modalPushTitle").textContent =
            "Push to " + dictNames[sService];
        fnRenderPushFileList(listFiles);
        document.getElementById("modalPush").style.display = "flex";
    }

    function fnRenderPushFileList(listFiles) {
        var elList = document.getElementById("modalPushFileList");
        var bOverleaf = sPushService === "overleaf";
        elList.innerHTML = listFiles.map(function (dictFile) {
            var bSupporting = bOverleaf &&
                dictFile.sCategory === "supporting";
            return '<div class="push-file-row' +
                (bSupporting ? " push-file-supporting" : "") +
                '">' +
                '<input type="checkbox" class="push-file-checkbox" ' +
                'data-path="' + fnEscapeHtml(dictFile.sPath) +
                '"' + (bSupporting ? "" : " checked") +
                (bSupporting ? " disabled" : "") + '>' +
                '<span class="push-file-name">' +
                fnEscapeHtml(dictFile.sPath) +
                (bSupporting ? " (supporting)" : "") +
                '</span>' +
                '<span class="push-file-sync">' +
                fsRenderSyncBadges(dictFile.dictSync) +
                '</span></div>';
        }).join("");
    }

    function fsRenderSyncBadges(dictSync) {
        return fsRenderOneBadge("overleaf", dictSync.bOverleaf) +
            fsRenderOneBadge("github", dictSync.bGithub) +
            fsRenderOneBadge("zenodo", dictSync.bZenodo);
    }

    function fsRenderOneBadge(sService, bActive) {
        var sClass = bActive ? "sync-active" : "sync-inactive";
        return '<span class="sync-badge sync-badge-' + sService +
            ' ' + sClass + '" title="' +
            (bActive ? "Synced" : "Not synced") + '"></span>';
    }

    async function fnHandlePushConfirm() {
        var listPaths = [];
        document.querySelectorAll(
            ".push-file-checkbox:checked"
        ).forEach(function (el) {
            listPaths.push(el.dataset.path);
        });
        if (listPaths.length === 0) {
            fnShowToast("No files selected", "error");
            return;
        }
        document.getElementById("modalPush").style.display = "none";
        fnShowToast("Pushing " + listPaths.length + " files...",
            "success");
        var sEndpoint = _fsServiceEndpoint(sPushService);
        var sAction = _fsServiceAction(sPushService);
        try {
            var response = await fetch(
                sEndpoint + sContainerId + "/" + sAction,
                {
                    method: "POST",
                    headers: {"Content-Type": "application/json"},
                    body: JSON.stringify({
                        listFilePaths: listPaths,
                    }),
                }
            );
            var dictResult = await response.json();
            if (!response.ok || !dictResult.bSuccess) {
                fnShowSyncError(dictResult, sPushService);
                return;
            }
            fnShowToast("Push complete!", "success");
            await fnLoadSyncStatus();
            fnRenderStepList();
        } catch (error) {
            fnShowToast(fsSanitizeErrorForUser(error.message), "error");
        }
    }

    var dictSyncErrorMessages = {
        auth: "Authentication failed. Check your credentials " +
            "in Sync > Setup.",
        rateLimit: "Rate limited. Try again in a few minutes.",
        notFound: "Resource not found. Check your project ID " +
            "or DOI.",
        network: "Network error. Check your container's " +
            "internet connection.",
    };

    function fnShowSyncError(dictResult, sService) {
        var sErrorType = dictResult.sErrorType || "unknown";
        var sMessage = dictSyncErrorMessages[sErrorType] ||
            dictResult.sMessage || "Unknown error";
        var sTitle = (sService || "Sync") + " failed: " +
            sErrorType;
        fnShowErrorModal(sTitle + "\n\n" + sMessage);
    }

    function _fsServiceEndpoint(sService) {
        if (sService === "overleaf") return "/api/overleaf/";
        if (sService === "zenodo") return "/api/zenodo/";
        return "/api/github/";
    }

    function _fsServiceAction(sService) {
        if (sService === "zenodo") return "archive";
        return "push";
    }

    function fnBindPushModalEvents() {
        document.getElementById("btnPushCancel").addEventListener(
            "click", function () {
                document.getElementById("modalPush")
                    .style.display = "none";
            }
        );
        document.getElementById("btnPushConfirm").addEventListener(
            "click", fnHandlePushConfirm
        );
        document.getElementById("btnPushSelectAll").addEventListener(
            "click", function () {
                document.querySelectorAll(".push-file-checkbox")
                    .forEach(function (el) { el.checked = true; });
            }
        );
        fnBindConnectionSetupEvents();
    }

    function fnShowConnectionSetup(sService) {
        var elModal = document.getElementById("modalConnectionSetup");
        elModal.dataset.service = sService;
        var elProjectId = document.getElementById("groupSetupProjectId");
        var elToken = document.getElementById("groupSetupToken");
        elProjectId.style.display = "none";
        elToken.style.display = "none";
        if (sService === "overleaf") {
            elProjectId.style.display = "";
            elToken.style.display = "";
            var elLabel = document.getElementById("labelSetupToken");
            var elHelp = document.getElementById("helpSetupToken");
            elLabel.textContent = "Overleaf Password ";
            if (elHelp) {
                elHelp.setAttribute("title",
                    "Enter your Overleaf account password. " +
                    "Overleaf uses this as the git password " +
                    "for its git bridge. Go to Account > " +
                    "Password to set or reset it.");
                elLabel.appendChild(elHelp);
            }
            document.getElementById("modalConnectionTitle")
                .textContent = "Connect to Overleaf";
        } else if (sService === "zenodo") {
            elToken.style.display = "";
            var elLabel = document.getElementById("labelSetupToken");
            var elHelp = document.getElementById("helpSetupToken");
            elLabel.textContent = "Zenodo API Token ";
            if (elHelp) elLabel.appendChild(elHelp);
            document.getElementById("modalConnectionTitle")
                .textContent = "Connect to Zenodo";
        } else {
            fnShowToast(
                "GitHub uses gh auth. Run 'gh auth login' " +
                "on your host machine.", "error"
            );
            return;
        }
        elModal.style.display = "flex";
    }

    function fnBindConnectionSetupEvents() {
        document.getElementById("btnSetupCancel").addEventListener(
            "click", function () {
                document.getElementById("modalConnectionSetup")
                    .style.display = "none";
            }
        );
        document.getElementById("btnSetupSave").addEventListener(
            "click", fnHandleSetupSave
        );
        document.addEventListener("click", function (event) {
            var elHelp = event.target.closest(".help-icon");
            if (!elHelp) return;
            var sText = elHelp.getAttribute("title");
            if (!sText) return;
            event.preventDefault();
            event.stopPropagation();
            fnShowHelpPopup(sText);
        });
    }

    function fnShowHelpPopup(sText) {
        var elExisting = document.getElementById("popupHelp");
        if (elExisting) elExisting.remove();
        var elPopup = document.createElement("div");
        elPopup.id = "popupHelp";
        elPopup.className = "help-popup";
        elPopup.innerHTML =
            '<div class="help-popup-content">' +
            '<span class="help-popup-close">&times;</span>' +
            '<p>' + fnEscapeHtml(sText) + '</p></div>';
        document.body.appendChild(elPopup);
        elPopup.querySelector(".help-popup-close").addEventListener(
            "click", function () { elPopup.remove(); }
        );
    }

    async function fnHandleSetupSave() {
        var elModal = document.getElementById("modalConnectionSetup");
        var sService = elModal.dataset.service;
        var dictBody = { sService: sService };
        var sProjectId = document.getElementById(
            "inputSetupProjectId").value.trim();
        var sToken = document.getElementById(
            "inputSetupToken").value.trim();
        if (sProjectId) dictBody.sProjectId = sProjectId;
        if (sToken) dictBody.sToken = sToken;
        try {
            var response = await fetch(
                "/api/sync/" + sContainerId + "/setup",
                {
                    method: "POST",
                    headers: {"Content-Type": "application/json"},
                    body: JSON.stringify(dictBody),
                }
            );
            var dictResult = await response.json();
            elModal.style.display = "none";
            if (dictResult.bConnected) {
                fnShowToast("Connected!", "success");
                fnOpenPushModal(sService);
            } else {
                fnShowToast(
                    dictResult.sMessage || "Connection failed",
                    "error"
                );
            }
        } catch (error) {
            fnShowToast(fsSanitizeErrorForUser(error.message), "error");
        }
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
                if (_sSelectedContainerId) {
                    fnEnterNoWorkflow(_sSelectedContainerId);
                }
            }
        );
        document.getElementById("btnNewWorkflow").addEventListener(
            "click", fnCreateNewWorkflow
        );
        document.getElementById("btnRefreshWorkflows").addEventListener(
            "click", function () {
                if (_sSelectedContainerId) {
                    fnConnectToContainer(_sSelectedContainerId);
                }
            }
        );
        document.getElementById("activeWorkflowName").addEventListener(
            "click", function (event) {
                event.stopPropagation();
                fnToggleWorkflowDropdown();
            }
        );
        document.addEventListener("click", function () {
            fnHideWorkflowDropdown();
        });
    }

    async function fnToggleWorkflowDropdown() {
        var elDropdown = document.getElementById("workflowDropdown");
        if (elDropdown.classList.contains("active")) {
            elDropdown.classList.remove("active");
            return;
        }
        if (!sContainerId) return;
        try {
            var response = await fetch("/api/workflows/" + sContainerId);
            var listWorkflows = await response.json();
            fnRenderWorkflowDropdown(listWorkflows);
            elDropdown.classList.add("active");
        } catch (error) {
            fnShowToast("Could not load workflows", "error");
        }
    }

    function fnHideWorkflowDropdown() {
        document.getElementById("workflowDropdown")
            .classList.remove("active");
    }

    function fnRenderWorkflowDropdown(listWorkflows) {
        var elDropdown = document.getElementById("workflowDropdown");
        var bInNoWorkflow = !sWorkflowPath && !dictWorkflow;
        var sHtml = '<div class="workflow-dropdown-item' +
            (bInNoWorkflow ? " current" : "") +
            '" data-action="noWorkflow">' +
            '<span class="wf-name">No Workflow</span></div>';
        sHtml += listWorkflows.map(function (dictWf) {
            var bCurrent = dictWf.sPath === sWorkflowPath;
            return (
                '<div class="workflow-dropdown-item' +
                (bCurrent ? " current" : "") +
                '" data-path="' + fnEscapeHtml(dictWf.sPath) +
                '" data-name="' + fnEscapeHtml(dictWf.sName) + '">' +
                '<span class="wf-name">' +
                fnEscapeHtml(dictWf.sName) + '</span>' +
                '<span class="wf-path">' +
                fnEscapeHtml(dictWf.sPath) + '</span></div>'
            );
        }).join("");
        elDropdown.innerHTML = sHtml;
        fnBindWorkflowDropdownItems(elDropdown);
    }

    function fnBindWorkflowDropdownItems(elDropdown) {
        elDropdown.querySelectorAll(".workflow-dropdown-item")
            .forEach(function (el) {
                el.addEventListener("click", function (event) {
                    event.stopPropagation();
                    fnHideWorkflowDropdown();
                    if (el.dataset.action === "noWorkflow") {
                        if (!dictWorkflow && !sWorkflowPath) return;
                        fnEnterNoWorkflow(sContainerId);
                        return;
                    }
                    var sPath = el.dataset.path;
                    var sName = el.dataset.name;
                    if (sPath === sWorkflowPath) return;
                    fnConfirmWorkflowSwitch(sPath, sName);
                });
            });
    }

    function fnConfirmWorkflowSwitch(sNewPath, sNewName) {
        fnShowConfirmModal(
            "Switch Workflow",
            "Switch to \"" + sNewName + "\"?\n\n" +
            "Current workflow state will be saved.",
            async function () {
                await fnSaveCurrentWorkflow();
                fnSelectWorkflow(sContainerId, sNewPath, sNewName);
            }
        );
    }

    async function fnSaveCurrentWorkflow() {
        if (!sContainerId || !dictWorkflow || !sWorkflowPath) return;
        try {
            await fetch(
                "/api/connect/" + sContainerId +
                "?sWorkflowPath=" + encodeURIComponent(sWorkflowPath),
                { method: "POST" }
            );
        } catch (error) {
            fnShowToast("Could not save workflow", "error");
        }
    }

    function fnBindContainerLandingEvents() {
        document.getElementById("btnRefreshContainers").addEventListener(
            "click", function () {
                fnLoadContainers();
            }
        );
        document.getElementById("btnAddContainer").addEventListener(
            "click", fnOpenAddChoice
        );
        document.getElementById("btnShowUnrecognized").addEventListener(
            "click", function () {
                var elList = document.getElementById("listUnrecognized");
                var bVisible = elList.style.display !== "none";
                elList.style.display = bVisible ? "none" : "";
                this.textContent = bVisible
                    ? "Show unrecognized containers"
                    : "Hide unrecognized containers";
            }
        );
        document.addEventListener("click", function () {
            document.querySelectorAll(".container-tile-menu").forEach(
                function (el) { el.style.display = "none"; }
            );
        });
        document.getElementById("btnBrowserBack").addEventListener(
            "click", fnBrowserNavigateBack
        );
        document.getElementById("btnBrowserForward").addEventListener(
            "click", fnBrowserNavigateForward
        );
    }

    function fnBindAddContainerModal() {
        document.getElementById("btnAddContainerCancel").addEventListener(
            "click", function () {
                document.getElementById("modalAddContainer")
                    .style.display = "none";
            }
        );
        document.getElementById("btnAddContainerConfirm").addEventListener(
            "click", fnSelectDirectory
        );
        fnBindAddChoiceModal();
        fnBindCreateWizardModal();
    }

    /* --- Add Choice Modal --- */

    function fnOpenAddChoice() {
        document.getElementById("modalAddChoice").style.display = "flex";
    }

    function fnBindAddChoiceModal() {
        document.getElementById("btnAddChoiceCancel").addEventListener(
            "click", function () {
                document.getElementById("modalAddChoice")
                    .style.display = "none";
            }
        );
        document.getElementById("btnChoiceAddExisting").addEventListener(
            "click", function () {
                document.getElementById("modalAddChoice")
                    .style.display = "none";
                fnOpenDirectoryBrowser();
            }
        );
        document.getElementById("btnChoiceCreateNew").addEventListener(
            "click", function () {
                document.getElementById("modalAddChoice")
                    .style.display = "none";
                fnOpenCreateWizard();
            }
        );
    }

    /* --- Creation Wizard --- */

    var _iWizardStep = 0;
    var _dictWizardData = {};
    var _LIST_WIZARD_TITLES = [
        "Project Directory",
        "Template",
        "Project Name",
        "Python Version",
        "Repositories",
        "Summary",
    ];

    function fnOpenCreateWizard() {
        _iWizardStep = 0;
        _dictWizardData = {
            sDirectory: "",
            sTemplateName: "",
            sProjectName: "",
            sPythonVersion: "3.12",
            listRepositories: [],
        };
        document.getElementById("modalCreateWizard")
            .style.display = "flex";
        fnRenderWizardStep(_iWizardStep);
    }

    function fnBindCreateWizardModal() {
        document.getElementById("btnWizardCancel").addEventListener(
            "click", _fnCloseWizard
        );
        document.getElementById("btnWizardBack").addEventListener(
            "click", _fnWizardStepBack
        );
        document.getElementById("btnWizardNext").addEventListener(
            "click", _fnWizardStepNext
        );
    }

    function _fnCloseWizard() {
        document.getElementById("modalCreateWizard")
            .style.display = "none";
    }

    function _fnWizardStepBack() {
        if (_iWizardStep <= 0) return;
        _fnSaveCurrentStepData();
        _iWizardStep--;
        fnRenderWizardStep(_iWizardStep);
    }

    function _fnWizardStepNext() {
        _fnSaveCurrentStepData();
        if (!_fbValidateWizardStep(_iWizardStep)) return;
        if (_iWizardStep >= 5) {
            fnSubmitCreateProject();
            return;
        }
        _iWizardStep++;
        fnRenderWizardStep(_iWizardStep);
    }

    function fnRenderWizardStep(iStep) {
        _fnUpdateWizardProgress(iStep);
        _fnUpdateWizardButtons(iStep);
        document.getElementById("wizardStepTitle").textContent =
            _LIST_WIZARD_TITLES[iStep];
        var elContent = document.getElementById("wizardStepContent");
        var listRenderers = [
            _fnRenderStepDirectory,
            _fnRenderStepTemplate,
            _fnRenderStepProjectName,
            _fnRenderStepPythonVersion,
            _fnRenderStepRepositories,
            _fnRenderStepSummary,
        ];
        listRenderers[iStep](elContent);
    }

    function _fnUpdateWizardProgress(iStep) {
        var listDots = document.querySelectorAll(
            ".wizard-progress-step"
        );
        listDots.forEach(function (el, i) {
            el.classList.toggle("active", i <= iStep);
        });
    }

    function _fnUpdateWizardButtons(iStep) {
        document.getElementById("btnWizardBack").disabled =
            iStep === 0;
        document.getElementById("btnWizardNext").textContent =
            iStep === 5 ? "Create" : "Next";
    }

    function _fnRenderStepDirectory(elContent) {
        elContent.innerHTML =
            '<div class="form-group">' +
            '<label>Project Directory</label>' +
            '<input type="text" id="inputWizardDirectory" ' +
            'placeholder="/Users/you/projects/my-project">' +
            '</div>';
        var elInput = document.getElementById("inputWizardDirectory");
        elInput.value = _dictWizardData.sDirectory;
    }

    function _fnRenderStepTemplate(elContent) {
        elContent.innerHTML =
            '<p class="muted-text" style="text-align:center;">' +
            'Loading templates...</p>';
        _fnFetchAndRenderTemplates(elContent);
    }

    async function _fnFetchAndRenderTemplates(elContent) {
        try {
            var response = await fetch("/api/setup/templates");
            if (!response.ok) throw new Error("Fetch failed");
            var dictResult = await response.json();
            _fnBuildTemplateCards(elContent, dictResult.listTemplates);
        } catch (error) {
            elContent.innerHTML =
                '<p class="muted-text">Could not load templates.</p>';
        }
    }

    function _fnBuildTemplateCards(elContent, listTemplates) {
        if (!listTemplates || listTemplates.length === 0) {
            elContent.innerHTML =
                '<p class="muted-text">No templates available.</p>';
            return;
        }
        elContent.innerHTML = '<div class="add-choice-cards">' +
            listTemplates.map(function (sName) {
                var sActive = sName === _dictWizardData.sTemplateName
                    ? " style=\"border-color:var(--color-pale-blue);\""
                    : "";
                return '<div class="add-choice-card" ' +
                    'data-template="' + fnEscapeHtml(sName) + '"' +
                    sActive + '>' +
                    '<div class="add-choice-title">' +
                    fnEscapeHtml(sName) + '</div></div>';
            }).join("") + '</div>';
        _fnBindTemplateCardClicks(elContent);
    }

    function _fnBindTemplateCardClicks(elContent) {
        elContent.querySelectorAll(".add-choice-card").forEach(
            function (el) {
                el.addEventListener("click", function () {
                    _dictWizardData.sTemplateName =
                        el.dataset.template;
                    _fnHighlightSelectedCard(elContent, el);
                });
            }
        );
    }

    function _fnHighlightSelectedCard(elContent, elSelected) {
        elContent.querySelectorAll(".add-choice-card").forEach(
            function (el) {
                el.style.borderColor =
                    el === elSelected
                        ? "var(--color-pale-blue)" : "";
            }
        );
    }

    function _fnRenderStepProjectName(elContent) {
        var sDefault = _fsProjectNameFromDirectory();
        if (!_dictWizardData.sProjectName) {
            _dictWizardData.sProjectName = sDefault;
        }
        elContent.innerHTML =
            '<div class="form-group">' +
            '<label>Project Name</label>' +
            '<input type="text" id="inputWizardProjectName" ' +
            'placeholder="my-project">' +
            '</div>';
        document.getElementById("inputWizardProjectName").value =
            _dictWizardData.sProjectName;
    }

    function _fsProjectNameFromDirectory() {
        var sDir = _dictWizardData.sDirectory || "";
        var sTrimmed = sDir.replace(/\/+$/, "");
        var iLastSlash = sTrimmed.lastIndexOf("/");
        return iLastSlash >= 0
            ? sTrimmed.substring(iLastSlash + 1) : sTrimmed;
    }

    function _fnRenderStepPythonVersion(elContent) {
        var listVersions = [
            "3.9", "3.10", "3.11", "3.12", "3.13", "3.14",
        ];
        elContent.innerHTML =
            '<div class="form-group">' +
            '<label>Python Version</label>' +
            '<select id="selectWizardPython">' +
            listVersions.map(function (sVersion) {
                var sSelected =
                    sVersion === _dictWizardData.sPythonVersion
                        ? " selected" : "";
                return '<option value="' + sVersion + '"' +
                    sSelected + '>' + sVersion + '</option>';
            }).join("") +
            '</select></div>';
    }

    function _fnRenderStepRepositories(elContent) {
        elContent.innerHTML =
            '<div class="form-group">' +
            '<label>Repositories (one per line, optional)</label>' +
            '<textarea id="inputWizardRepos" rows="5" ' +
            'placeholder="https://github.com/org/repo.git">' +
            '</textarea></div>';
        var sRepos = _dictWizardData.listRepositories.join("\n");
        document.getElementById("inputWizardRepos").value = sRepos;
    }

    function _fnRenderStepSummary(elContent) {
        elContent.innerHTML =
            '<div style="font-size:13px;color:var(--text-secondary);">' +
            '<p><strong>Directory:</strong> ' +
            fnEscapeHtml(_dictWizardData.sDirectory) + '</p>' +
            '<p><strong>Template:</strong> ' +
            fnEscapeHtml(_dictWizardData.sTemplateName) + '</p>' +
            '<p><strong>Project Name:</strong> ' +
            fnEscapeHtml(_dictWizardData.sProjectName) + '</p>' +
            '<p><strong>Python:</strong> ' +
            fnEscapeHtml(_dictWizardData.sPythonVersion) + '</p>' +
            '<p><strong>Repositories:</strong> ' +
            (_dictWizardData.listRepositories.length > 0
                ? fnEscapeHtml(
                    _dictWizardData.listRepositories.join(", "))
                : '<em>None</em>') +
            '</p></div>';
    }

    function _fnSaveCurrentStepData() {
        var elDir = document.getElementById("inputWizardDirectory");
        if (elDir) _dictWizardData.sDirectory = elDir.value.trim();
        var elName = document.getElementById(
            "inputWizardProjectName"
        );
        if (elName) {
            _dictWizardData.sProjectName = elName.value.trim();
        }
        var elPython = document.getElementById("selectWizardPython");
        if (elPython) {
            _dictWizardData.sPythonVersion = elPython.value;
        }
        var elRepos = document.getElementById("inputWizardRepos");
        if (elRepos) {
            _dictWizardData.listRepositories = elRepos.value
                .split("\n")
                .map(function (s) { return s.trim(); })
                .filter(function (s) { return s.length > 0; });
        }
    }

    function _fbValidateWizardStep(iStep) {
        if (iStep === 0 && !_dictWizardData.sDirectory) {
            fnShowToast("Directory path is required.", "warning");
            return false;
        }
        if (iStep === 1 && !_dictWizardData.sTemplateName) {
            fnShowToast("Please select a template.", "warning");
            return false;
        }
        if (iStep === 2 && !_dictWizardData.sProjectName) {
            fnShowToast("Project name is required.", "warning");
            return false;
        }
        return true;
    }

    async function fnSubmitCreateProject() {
        var elButton = document.getElementById("btnWizardNext");
        elButton.disabled = true;
        elButton.textContent = "Creating...";
        try {
            var response = await fetch("/api/projects/create", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(_dictWizardData),
            });
            if (!response.ok) {
                var dictError = await response.json();
                fnShowToast(
                    dictError.detail || "Creation failed", "error"
                );
                return;
            }
            _fnCloseWizard();
            fnShowToast("Project created successfully.");
            fnLoadContainers();
        } catch (error) {
            fnShowToast("Network error creating project.", "error");
        } finally {
            elButton.disabled = false;
            elButton.textContent = "Create";
        }
    }

    async function fnLoadLogs() {
        if (!sContainerId) return;
        var elList = document.getElementById("listLogs");
        try {
            var response = await fetch("/api/logs/" + sContainerId);
            var listLogs = await response.json();
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
        if (!sContainerId) return;
        try {
            var response = await fetch(
                "/api/logs/" + sContainerId + "/" +
                encodeURIComponent(sFilename)
            );
            var sContent = await response.text();
            var elViewport = document.getElementById("viewportA");
            elViewport.innerHTML =
                '<pre class="pipeline-output">' +
                fnEscapeHtml(sContent) + '</pre>';
        } catch (error) {
            fnShowToast(fsSanitizeErrorForUser(error.message), "error");
        }
    }

    function fnConnectPipelineWebSocket() {
        if (wsPipeline && (
            wsPipeline.readyState === WebSocket.OPEN ||
            wsPipeline.readyState === WebSocket.CONNECTING
        )) {
            return wsPipeline;
        }
        var sProtocol =
            window.location.protocol === "https:" ? "wss:" : "ws:";
        var sUrl = sProtocol + "//" + window.location.host +
            "/ws/pipeline/" + sContainerId +
            "?sToken=" + encodeURIComponent(sSessionToken);
        wsPipeline = new WebSocket(sUrl);
        wsPipeline.onmessage = function (event) {
            fnHandlePipelineEvent(JSON.parse(event.data));
        };
        wsPipeline.onclose = function () {
            wsPipeline = null;
            fnClearRunningStatuses();
            fnRenderStepList();
        };
        wsPipeline.onerror = function () {
            wsPipeline = null;
        };
        return wsPipeline;
    }

    function fnHandlePipelineEvent(dictEvent) {
        if (dictEvent.sType === "output") {
            fnAppendPipelineOutput(dictEvent.sLine);
        } else if (dictEvent.sType === "commandFailed") {
            var sMessage =
                "FAILED: " + dictEvent.sCommand +
                "\n  Directory: " + dictEvent.sDirectory +
                "\n  Exit code: " + dictEvent.iExitCode;
            fnAppendPipelineOutput(sMessage);
            fnShowErrorModal(sMessage);
        } else if (dictEvent.sType === "preflightFailed") {
            var sErrors = dictEvent.listErrors.join("\n");
            fnShowErrorModal(
                "Pre-flight validation failed:\n\n" + sErrors
            );
        } else if (dictEvent.sType === "testResult") {
            fnHandleTestResult(dictEvent);
        } else if (dictEvent.sType === "stepStarted") {
            dictStepStatus[dictEvent.iStepNumber - 1] = "running";
            fnRenderStepList();
        } else if (dictEvent.sType === "stepStats") {
            var iStepIdx = dictEvent.iStepNumber - 1;
            if (dictWorkflow && dictWorkflow.listSteps[iStepIdx]) {
                dictWorkflow.listSteps[iStepIdx].dictRunStats =
                    dictEvent.dictRunStats;
                fnRenderStepList();
            }
        } else if (dictEvent.sType === "stepSkipped") {
            dictStepStatus[dictEvent.iStepNumber - 1] = "skipped";
            fnAppendPipelineOutput(
                "Step " + dictEvent.iStepNumber +
                ": SKIPPED (inputs unchanged)");
            fnRenderStepList();
        } else if (dictEvent.sType === "discoveredOutputs") {
            fnHandleDiscoveredOutputs(dictEvent);
        } else if (dictEvent.sType === "stepPass") {
            var iPassIdx = dictEvent.iStepNumber - 1;
            dictStepStatus[iPassIdx] = "pass";
            fnClearOutputModified(iPassIdx);
            fnInvalidateStepFileCache(iPassIdx);
            fnRenderStepList();
        } else if (dictEvent.sType === "stepFail") {
            dictStepStatus[dictEvent.iStepNumber - 1] = "fail";
            fnInvalidateStepFileCache(dictEvent.iStepNumber - 1);
            fnRenderStepList();
        } else if (dictEvent.sType === "started") {
            fnStopPipelinePolling();
            fnStopFileChangePolling();
            fnInitPipelineOutput();
            fnShowToast("Pipeline started", "success");
        } else if (dictEvent.sType === "completed") {
            fnClearRunningStatuses();
            fnStartFileChangePolling();
            fnShowToast("Pipeline completed", "success");
            fnRenderStepList();
            if (dictEvent.sLogPath) {
                fnDisplayLogInViewer(dictEvent.sLogPath);
            }
        } else if (dictEvent.sType === "failed") {
            fnClearRunningStatuses();
            fnStartFileChangePolling();
            fnShowToast(
                "Pipeline failed (exit " + dictEvent.iExitCode + ")", "error"
            );
            fnRenderStepList();
            if (dictEvent.sLogPath) {
                fnDisplayLogInViewer(dictEvent.sLogPath);
            }
        } else if (dictEvent.sType === "interactivePause") {
            fnShowInteractivePauseDialog(dictEvent);
        } else if (dictEvent.sType === "interactiveTerminalStart") {
            fnRunInteractiveInTerminal(dictEvent);
        }
    }

    function fnShowInteractivePauseDialog(dictEvent) {
        var sLabel = fsComputeStepLabel(dictEvent.iStepIndex);
        _fnShowTwoActionModal(
            "Interactive Step Reached",
            "Step " + sLabel + " '" + dictEvent.sStepName +
            "' requires your input.\n\n" +
            "Run it in the terminal?",
            "Run", function () {
                _fnSendPipelineMessage("interactiveResume");
            },
            "Skip", function () {
                _fnSendPipelineMessage("interactiveSkip");
            }
        );
    }

    function _fnShowTwoActionModal(
        sTitle, sMessage, sConfirmLabel, fnOnConfirm,
        sCancelLabel, fnOnCancel,
    ) {
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
            '<button class="btn" id="btnConfirmCancel">' +
            fnEscapeHtml(sCancelLabel) + '</button>' +
            '<button class="btn btn-primary" ' +
            'id="btnConfirmOk">' +
            fnEscapeHtml(sConfirmLabel) + '</button>' +
            '</div></div>';
        document.body.appendChild(elModal);
        document.getElementById("btnConfirmCancel").addEventListener(
            "click", function () {
                elModal.remove();
                fnOnCancel();
            }
        );
        document.getElementById("btnConfirmOk").addEventListener(
            "click", function () {
                elModal.remove();
                fnOnConfirm();
            }
        );
    }

    function _fnSendPipelineMessage(sAction) {
        if (wsPipeline) {
            wsPipeline.send(JSON.stringify({sAction: sAction}));
        }
    }

    function fnRunInteractiveInTerminal(dictEvent) {
        var dictStep = dictEvent.dictStep || {};
        var sDirectory = dictStep.sDirectory || "";
        var listCommands = (dictStep.saDataCommands || []).concat(
            dictStep.saPlotCommands || []
        );
        if (listCommands.length === 0) {
            _fnSendInteractiveComplete(0);
            return;
        }
        var sUuid = _fsGenerateUuid();
        var sSentinel = "__VAIBIFY_DONE_" + sUuid + "__";
        var sFullCommand = _fsBuildInteractiveCommand(
            sDirectory, listCommands, sSentinel,
        );
        PipeleyenTerminal.fnSendCommandInFreshTab(sFullCommand);
        _fnMonitorTerminalForSentinel(sSentinel);
    }

    function _fsBuildInteractiveCommand(
        sDirectory, listCommands, sSentinel,
    ) {
        var sCd = sDirectory
            ? "cd '" + sDirectory.replace(/'/g, "'\\''") + "' && "
            : "";
        var sJoined = listCommands.join(" && ");
        return sCd + sJoined +
            "; echo " + sSentinel + "=$?";
    }

    function _fsGenerateUuid() {
        return "xxxx-xxxx".replace(/x/g, function () {
            return Math.floor(Math.random() * 16).toString(16);
        });
    }

    function _fnMonitorTerminalForSentinel(sSentinel) {
        var iCheckInterval = setInterval(function () {
            var sText = _fsReadAllTerminalText();
            var iMatch = sText.indexOf(sSentinel + "=");
            if (iMatch < 0) return;
            clearInterval(iCheckInterval);
            var sAfter = sText.substring(
                iMatch + sSentinel.length + 1
            );
            var iExitCode = parseInt(sAfter.trim(), 10) || 0;
            _fnSendInteractiveComplete(iExitCode);
        }, 1000);
    }

    function _fsReadAllTerminalText() {
        var sText = "";
        var listPanes = document.querySelectorAll(
            ".terminal-pane-container .xterm"
        );
        listPanes.forEach(function (elTerminal) {
            try {
                var elRows = elTerminal.querySelectorAll(
                    ".xterm-rows > div"
                );
                elRows.forEach(function (el) {
                    sText += el.textContent + "\n";
                });
            } catch (e) { /* skip unreadable pane */ }
        });
        return sText;
    }

    function _fnSendInteractiveComplete(iExitCode) {
        if (wsPipeline) {
            wsPipeline.send(JSON.stringify({
                sAction: "interactiveComplete",
                iExitCode: iExitCode,
            }));
        }
    }

    function fnClearOutputModified(iStep) {
        var dictStep = dictWorkflow.listSteps[iStep];
        if (dictStep && dictStep.dictVerification) {
            delete dictStep.dictVerification.bOutputModified;
            delete dictStep.dictVerification.listModifiedFiles;
        }
    }

    function fnHandleTestResult(dictEvent) {
        var iStep = dictEvent.iStepNumber - 1;
        var dictStep = dictWorkflow.listSteps[iStep];
        if (!dictStep.dictVerification) {
            dictStep.dictVerification = {
                sUnitTest: "untested", sUser: "untested",
            };
        }
        dictStep.dictVerification.sUnitTest = dictEvent.sResult;
        if (dictEvent.sResult === "passed") {
            fnClearOutputModified(iStep);
        }
        fnRenderStepList();
        fnCheckVaibified();
        var sLabel = dictEvent.sResult === "passed" ?
            "Tests passed" : "Tests FAILED";
        fnShowToast("Step " + (iStep + 1) + ": " + sLabel,
            dictEvent.sResult === "passed" ? "success" : "error");
    }

    var iPipelinePollTimer = null;
    var iPreviousOutputCount = 0;
    var iFileChangePollTimer = null;
    var iPollIntervalMs = 5000;
    var dictFileModTimes = {};
    var dictScriptModified = {};

    async function fnRecoverPipelineState(sId) {
        try {
            var response = await fetch(
                "/api/pipeline/" + sId + "/state"
            );
            var dictState = await response.json();
            if (!dictState || !dictState.bRunning) {
                if (dictState && dictState.sLogPath &&
                    dictState.iExitCode >= 0) {
                    fnApplyCompletedState(dictState);
                }
                fnStartFileChangePolling();
                return;
            }
            fnApplyRunningState(dictState, true);
            fnStartPipelinePolling(sId);
        } catch (error) {
            fnStartFileChangePolling();
        }
    }

    function fnStartPipelinePolling(sId) {
        fnStopPipelinePolling();
        iPipelinePollTimer = setInterval(function () {
            fnPollPipelineState(sId);
        }, 10000);
    }

    function fnStopPipelinePolling() {
        if (iPipelinePollTimer) {
            clearInterval(iPipelinePollTimer);
            iPipelinePollTimer = null;
        }
    }

    async function fnPollPipelineState(sId) {
        try {
            var response = await fetch(
                "/api/pipeline/" + sId + "/state"
            );
            var dictState = await response.json();
            if (!dictState) return;
            if (!dictState.bRunning) {
                fnStopPipelinePolling();
                fnApplyCompletedState(dictState);
                if (dictState.sLogPath) {
                    fnDisplayLogInViewer(dictState.sLogPath);
                }
                fnShowToast(
                    dictState.iExitCode === 0 ?
                        "Pipeline completed" :
                        "Pipeline failed (exit " +
                        dictState.iExitCode + ")",
                    dictState.iExitCode === 0 ? "success" : "error"
                );
                fnStartFileChangePolling();
                return;
            }
            fnApplyRunningState(dictState, false);
        } catch (error) {
            /* poll failed, try again next interval */
        }
    }

    function fnApplyRunningState(dictState, bInitial) {
        if (bInitial) {
            fnInitPipelineOutput();
            fnShowToast(
                "Reconnected to running pipeline", "success"
            );
            iPreviousOutputCount = 0;
        }
        var dictResults = dictState.dictStepResults || {};
        for (var sKey in dictResults) {
            var iStep = parseInt(sKey) - 1;
            var sStatus = dictResults[sKey].sStatus;
            if (sStatus === "passed") {
                dictStepStatus[iStep] = "";
            } else if (sStatus === "failed") {
                dictStepStatus[iStep] = "fail";
            } else if (sStatus === "skipped") {
                dictStepStatus[iStep] = "";
            }
        }
        if (dictState.iActiveStep > 0) {
            dictStepStatus[dictState.iActiveStep - 1] = "running";
        }
        var iStepCount = dictState.iStepCount || 0;
        for (var i = 0; i < iStepCount; i++) {
            var sIdx = String(i + 1);
            if (!dictResults[sIdx] &&
                i !== dictState.iActiveStep - 1) {
                if (!dictResults[sIdx]) {
                    dictStepStatus[i] = "queued";
                }
            }
        }
        var listOutput = dictState.listRecentOutput || [];
        var elOutput = document.getElementById("panelOutput");
        if (elOutput && listOutput.length > iPreviousOutputCount) {
            var listNew = listOutput.slice(iPreviousOutputCount);
            listNew.forEach(function (sLine) {
                var elLine = document.createElement("div");
                elLine.textContent = sLine;
                if (sLine.indexOf("FAILED") >= 0) {
                    elLine.style.color = "var(--color-red)";
                } else if (sLine.startsWith("$")) {
                    elLine.style.color =
                        "var(--color-blue, #3498db)";
                }
                elOutput.appendChild(elLine);
            });
            elOutput.scrollTop = elOutput.scrollHeight;
            iPreviousOutputCount = listOutput.length;
        }
        fnRenderStepList();
    }

    function fnApplyCompletedState(dictState) {
        fnClearRunningStatuses();
        var dictResults = dictState.dictStepResults || {};
        for (var sKey in dictResults) {
            var iStep = parseInt(sKey) - 1;
            var sStatus = dictResults[sKey].sStatus;
            if (sStatus === "failed") {
                dictStepStatus[iStep] = "fail";
            }
        }
        fnRenderStepList();
    }

    function fnStartFileChangePolling() {
        fnStopFileChangePolling();
        iFileChangePollTimer = setInterval(function () {
            fnPollFileChanges();
        }, iPollIntervalMs);
    }

    function fnStopFileChangePolling() {
        if (iFileChangePollTimer) {
            clearInterval(iFileChangePollTimer);
            iFileChangePollTimer = null;
        }
    }

    async function fnPollFileChanges() {
        if (!sContainerId || !dictWorkflow) return;
        try {
            var response = await fetch(
                "/api/pipeline/" + sContainerId + "/file-status"
            );
            var dictStatus = await response.json();
            var bChanged = false;
            var dictNewMods = dictStatus.dictModTimes || {};
            for (var sPath in dictNewMods) {
                if (dictFileModTimes[sPath] !== dictNewMods[sPath]) {
                    bChanged = true;
                    break;
                }
            }
            if (bChanged) {
                dictFileModTimes = dictNewMods;
                dictFileExistenceCache = {};
                fnScheduleFileExistenceCheck();
                fnRenderStepList();
            }
            var dictInv = dictStatus.dictInvalidatedSteps;
            if (dictInv && Object.keys(dictInv).length > 0) {
                fnApplyInvalidatedSteps(dictInv);
            }
            if (dictStatus.dictScriptStatus) {
                var dictPrev = JSON.stringify(dictScriptModified);
                dictScriptModified = dictStatus.dictScriptStatus;
                if (JSON.stringify(dictScriptModified) !== dictPrev) {
                    fnRenderStepList();
                }
            }
        } catch (error) {
            /* poll failed, try again next interval */
        }
    }

    function fnApplyInvalidatedSteps(dictStepVerifications) {
        var bAnyChanged = false;
        for (var sIndex in dictStepVerifications) {
            var iStep = parseInt(sIndex, 10);
            var dictStep = dictWorkflow.listSteps[iStep];
            if (!dictStep) continue;
            dictStep.dictVerification =
                dictStepVerifications[sIndex];
            bAnyChanged = true;
        }
        if (bAnyChanged) fnRenderStepList();
    }

    function fnDisplayLogInViewer(sLogPath) {
        PipeleyenFigureViewer.fnDisplayFileFromContainer(sLogPath);
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

    function fnBindApiConfirmModal() {
        document.getElementById("btnApiCancel").addEventListener(
            "click", function () {
                document.getElementById("modalApiConfirm")
                    .style.display = "none";
            }
        );
        document.getElementById("btnApiConfirm").addEventListener(
            "click", function () {
                var elModal = document.getElementById("modalApiConfirm");
                var iStep = parseInt(elModal.dataset.step);
                var sApiKey = document.getElementById(
                    "inputApiKey"
                ).value.trim();
                if (!sApiKey) {
                    fnShowToast("API key is required", "error");
                    return;
                }
                elModal.style.display = "none";
                fnGenerateTestsWithApi(iStep, sApiKey);
            }
        );
    }

    async function fnGenerateTestsWithApi(iStep, sApiKey) {
        fnShowToast("Generating tests via API...", "success");
        try {
            var response = await fetch(
                "/api/steps/" + sContainerId + "/" + iStep +
                "/generate-test",
                { method: "POST",
                  headers: {"Content-Type": "application/json"},
                  body: JSON.stringify({
                      bUseApi: true, sApiKey: sApiKey,
                  }) }
            );
            var dictResult = await response.json();
            if (!response.ok) {
                fnShowToast(
                    dictResult.detail || "Generation failed", "error"
                );
                return;
            }
            fnHandleGeneratedTest(iStep, dictResult);
        } catch (error) {
            fnShowToast(fsSanitizeErrorForUser(error.message), "error");
        }
    }

    function fnInitPipelineOutput() {
        var elViewport = document.getElementById("viewportA");
        elViewport.innerHTML =
            '<pre id="pipelineOutput" class="pipeline-output"></pre>';
        elViewport.scrollTop = 0;
    }

    var MAX_PIPELINE_OUTPUT_LINES = 5000;

    function fnAppendPipelineOutput(sLine) {
        var elOutput = document.getElementById("pipelineOutput");
        if (!elOutput) {
            fnInitPipelineOutput();
            elOutput = document.getElementById("pipelineOutput");
        }
        var elLine = document.createElement("span");
        elLine.textContent = sLine + "\n";
        if (sLine.startsWith("FAILED:")) {
            elLine.style.color = "var(--color-red, #e74c3c)";
        } else if (sLine.startsWith("$")) {
            elLine.style.color = "var(--color-blue, #3498db)";
        }
        elOutput.appendChild(elLine);
        while (elOutput.childNodes.length > MAX_PIPELINE_OUTPUT_LINES) {
            elOutput.removeChild(elOutput.firstChild);
        }
        elOutput.scrollTop = elOutput.scrollHeight;
    }

    function fnSendPipelineAction(dictAction) {
        var ws = fnConnectPipelineWebSocket();
        if (ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify(dictAction));
        } else {
            ws.addEventListener("open", function () {
                ws.send(JSON.stringify(dictAction));
            }, { once: true });
        }
    }

    function fnEditTestFile(iStepIndex, iCmdIdx) {
        var step = dictWorkflow.listSteps[iStepIndex];
        if (!step) return;
        var sCmd = (step.saTestCommands || [])[iCmdIdx];
        if (!sCmd) return;
        var sFilePath = sCmd
            .replace(/^python\s+-m\s+pytest\s+/, "")
            .replace(/^pytest\s+/, "")
            .replace(/\s+(-v|--verbose)(\s|$).*$/, "")
            .trim();
        var sDir = step.sDirectory || "";
        if (sFilePath.charAt(0) !== "/" && sDir) {
            sFilePath = sDir + "/" + sFilePath;
        }
        PipeleyenFigureViewer.fnDisplayInNextViewer(sFilePath, sDir);
    }

    function fnDeleteTestCommand(iStepIndex, iCmdIdx) {
        fnShowConfirmModal(
            "Delete Test",
            "Delete this test command and its test file? " +
            "This cannot be undone.",
            async function () {
                var step = dictWorkflow.listSteps[iStepIndex];
                if (!step) return;
                var listCmds = step.saTestCommands || [];
                if (iCmdIdx >= listCmds.length) return;
                listCmds.splice(iCmdIdx, 1);
                step.dictVerification = step.dictVerification || {};
                step.dictVerification.sUnitTest = "untested";
                await fnSaveStepUpdate(iStepIndex, {
                    saTestCommands: listCmds,
                    dictVerification: step.dictVerification,
                });
                fnRenderStepList();
            }
        );
    }

    function fnRunSingleStep(iIndex) {
        var step = dictWorkflow.listSteps[iIndex];
        if (!step) return;
        if (step.bInteractive) {
            fnRunInteractiveStep(iIndex);
            return;
        }
        dictStepStatus[iIndex] = "queued";
        fnRenderStepList();
        fnSendPipelineAction({
            sAction: "runSelected",
            listStepIndices: [iIndex],
        });
    }

    async function fnRunStepTests(iStepIndex) {
        if (!sContainerId) return;
        var step = dictWorkflow.listSteps[iStepIndex];
        if (!step || !step.saTestCommands ||
            step.saTestCommands.length === 0) return;
        fnShowToast("Running tests for Step " +
            (iStepIndex + 1) + "...", "success");
        try {
            var response = await fetch(
                "/api/steps/" + sContainerId + "/" +
                iStepIndex + "/run-tests",
                { method: "POST" }
            );
            var dictResult = await response.json();
            step.dictVerification = step.dictVerification || {};
            fnApplyCategoryResults(
                step, dictResult.dictCategoryResults);
            step.dictVerification.sUnitTest =
                dictResult.bPassed ? "passed" : "failed";
            if (dictResult.bPassed) {
                fnClearOutputModified(iStepIndex);
            }
            fnSaveStepUpdate(iStepIndex, {
                dictVerification: step.dictVerification,
            });
            fnRenderStepList();
            fnCheckVaibified();
            var sOutput = fsCollectTestOutput(dictResult);
            fnShowToast(
                dictResult.bPassed ?
                    "Tests passed" : "Tests FAILED",
                dictResult.bPassed ? "success" : "error"
            );
            PipeleyenFigureViewer.fnDisplayTestOutput(
                sOutput, dictResult.bPassed);
        } catch (error) {
            fnShowToast(
                fsSanitizeErrorForUser(error.message), "error");
        }
    }

    function fnApplyCategoryResults(step, dictCategoryResults) {
        if (!dictCategoryResults) return;
        var dictMap = {
            dictIntegrity: "sIntegrity",
            dictQualitative: "sQualitative",
            dictQuantitative: "sQuantitative",
        };
        for (var sKey in dictMap) {
            var dictCat = dictCategoryResults[sKey];
            if (dictCat) {
                step.dictVerification[dictMap[sKey]] =
                    dictCat.bPassed ? "passed" : "failed";
            }
        }
    }

    function fsCollectTestOutput(dictResult) {
        var listParts = [];
        var dictCats = dictResult.dictCategoryResults || {};
        for (var sKey in dictCats) {
            var sOutput = (dictCats[sKey].sOutput || "").trim();
            if (sOutput) listParts.push(sOutput);
        }
        if (listParts.length > 0) return listParts.join("\n\n");
        return dictResult.sOutput || "(no output)";
    }

    function fnRunInteractiveStep(iIndex) {
        var step = dictWorkflow.listSteps[iIndex];
        if (!step) return;
        var dictVars = fdictBuildClientVariables();
        var listCmds = step.saDataCommands || [];
        if (listCmds.length === 0) return;
        var sDir = fsResolveTemplate(step.sDirectory, dictVars);
        var sFullCmd = "cd " + sDir + " && " +
            listCmds.map(function (c) {
                return fsResolveTemplate(c, dictVars);
            }).join(" && ");
        PipeleyenTerminal.fnSendCommandInFreshTab(sFullCmd);
        var elStrip = document.getElementById("terminalStrip");
        if (elStrip) elStrip.scrollIntoView({ behavior: "smooth" });
    }

    function fnRunInteractivePlots(iIndex) {
        var step = dictWorkflow.listSteps[iIndex];
        if (!step) return;
        var dictVars = fdictBuildClientVariables();
        var listCmds = step.saPlotCommands || [];
        if (listCmds.length === 0) return;
        var sDir = fsResolveTemplate(step.sDirectory, dictVars);
        var sFullCmd = "cd " + sDir + " && " +
            listCmds.map(function (c) {
                return fsResolveTemplate(c, dictVars);
            }).join(" && ");
        PipeleyenTerminal.fnSendCommandInFreshTab(sFullCmd);
        var elStrip = document.getElementById("terminalStrip");
        if (elStrip) elStrip.scrollIntoView({ behavior: "smooth" });
    }

    function fnRunSelected() {
        var listIndices = [];
        document.querySelectorAll(".step-checkbox:checked")
            .forEach(function (el) {
                var iIndex = parseInt(
                    el.closest(".step-item").dataset.index
                );
                listIndices.push(iIndex);
                dictStepStatus[iIndex] = "queued";
            });
        fnRenderStepList();
        fnSendPipelineAction({
            sAction: "runSelected",
            listStepIndices: listIndices,
        });
    }

    function fsInteractiveWarning() {
        if (!dictWorkflow || !dictWorkflow.listSteps) return "";
        var iLeading = fiCountLeadingInteractive();
        if (iLeading > 0) {
            return "\n\nThe first " + iLeading +
                " step(s) are interactive. The pipeline will " +
                "pause at each one for your input.";
        }
        var bHasMiddle = dictWorkflow.listSteps.some(
            function (step) { return step.bInteractive; }
        );
        if (bHasMiddle) {
            return "\n\nThe pipeline contains interactive steps " +
                "and will pause when it reaches them.";
        }
        return "";
    }

    function fiCountLeadingInteractive() {
        if (!dictWorkflow || !dictWorkflow.listSteps) return 0;
        var iCount = 0;
        for (var i = 0; i < dictWorkflow.listSteps.length; i++) {
            if (!dictWorkflow.listSteps[i].bInteractive) break;
            iCount++;
        }
        return iCount;
    }

    async function fsGetSleepWarning() {
        var fTotalSeconds = fsEstimateRunTimeSeconds();
        if (fTotalSeconds < 3600) return "";
        try {
            var response = await fetch("/api/runtime");
            var dictRuntime = await response.json();
            return "\n\n" + (dictRuntime.sSleepWarning || "");
        } catch (e) {
            return "";
        }
    }

    function fsEstimateRunTimeSeconds() {
        if (!dictWorkflow || !dictWorkflow.listSteps) return 0;
        var fTotal = 0;
        dictWorkflow.listSteps.forEach(function (step) {
            if (step.bEnabled === false) return;
            var dictStats = step.dictRunStats || {};
            if (dictStats.fWallClock) fTotal += dictStats.fWallClock;
        });
        return fTotal;
    }

    async function fnRunAll() {
        var sEstimate = fsEstimateRunTime();
        var sInteractiveWarn = fsInteractiveWarning();
        var sSleepWarn = await fsGetSleepWarning();
        var sMessage = "Run all enabled steps?";
        if (sInteractiveWarn) {
            sMessage += sInteractiveWarn;
        }
        if (sEstimate) {
            sMessage += "\n\n" + sEstimate;
        }
        sMessage += sSleepWarn;
        fnShowConfirmModal("Run All", sMessage, async function () {
            var listEnablePromises = [];
            dictWorkflow.listSteps.forEach(function (step, iIndex) {
                if (step.bEnabled === false) {
                    listEnablePromises.push(
                        fnToggleStepEnabled(iIndex, true)
                    );
                }
                dictStepStatus[iIndex] = "queued";
            });
            if (listEnablePromises.length > 0) {
                await Promise.all(listEnablePromises);
            }
            fnRenderStepList();
            fnSendPipelineAction({ sAction: "runAll" });
        });
    }

    async function fnForceRunAll() {
        var sSleepWarn = await fsGetSleepWarning();
        fnShowConfirmModal(
            "Force Run All",
            "This will clear input hashes and re-run every " +
            "automatic step from scratch. Interactive step " +
            "outputs are preserved.\n\n" +
            "All verification states will be reset to untested.",
            function () {
                var sEstimate = fsEstimateRunTime();
                var sTimeMsg = sEstimate ?
                    "\n\n" + sEstimate : "";
                fnShowConfirmModal(
                    "Confirm Clean Rebuild",
                    "Are you sure? This cannot be undone." +
                    sTimeMsg + sSleepWarn,
                    async function () {
                        await _fnExecuteForceRunAll();
                    }
                );
            }
        );
    }

    function fnKillPipeline() {
        fnShowConfirmModal(
            "Stop All Tasks",
            "This will kill all running pipeline processes " +
            "in the container.\n\n" +
            "Any in-progress computations will be lost.",
            async function () {
                try {
                    var response = await fetch(
                        "/api/pipeline/" + sContainerId + "/kill",
                        { method: "POST" }
                    );
                    var dictResult = await response.json();
                    if (dictResult.bSuccess) {
                        dictStepStatus = {};
                        fnRenderStepList();
                        fnShowToast(
                            "Killed " + dictResult.iProcessesKilled +
                            " process(es)", "success");
                    } else {
                        fnShowToast("Kill failed", "error");
                    }
                } catch (error) {
                    fnShowToast(fsSanitizeErrorForUser(error.message), "error");
                }
            }
        );
    }

    async function _fnExecuteForceRunAll() {
        fnShowToast("Stopping running tasks...", "success");
        try {
            await fetch(
                "/api/pipeline/" + sContainerId + "/kill",
                { method: "POST" }
            );
        } catch (error) { /* continue even if kill fails */ }
        fnShowToast("Cleaning outputs...", "success");
        try {
            await fetch(
                "/api/pipeline/" + sContainerId + "/clean",
                { method: "POST" }
            );
        } catch (error) {
            fnShowToast(fsSanitizeErrorForUser(error.message), "error");
            return;
        }
        var listEnablePromises = [];
        dictWorkflow.listSteps.forEach(function (step, iIndex) {
            if (step.bEnabled === false) {
                listEnablePromises.push(
                    fnToggleStepEnabled(iIndex, true)
                );
            }
            dictStepStatus[iIndex] = "queued";
        });
        if (listEnablePromises.length > 0) {
            await Promise.all(listEnablePromises);
        }
        dictFileExistenceCache = {};
        fnRenderStepList();
        fnSendPipelineAction({ sAction: "forceRunAll" });
    }

    function fsEstimateRunTime() {
        if (!dictWorkflow || !dictWorkflow.listSteps) return "";
        var fTotalSeconds = 0;
        var iStepsWithTime = 0;
        var iEnabledSteps = 0;
        dictWorkflow.listSteps.forEach(function (step) {
            if (step.bEnabled === false) return;
            iEnabledSteps++;
            var dictStats = step.dictRunStats || {};
            if (dictStats.fWallClock) {
                fTotalSeconds += dictStats.fWallClock;
                iStepsWithTime++;
            }
        });
        if (iStepsWithTime === 0) return "";
        var sTime = fsFormatDurationLong(fTotalSeconds);
        if (iStepsWithTime < iEnabledSteps) {
            return "This workflow will require at least " + sTime +
                " (based on " + iStepsWithTime + " of " +
                iEnabledSteps + " steps).";
        }
        return "This workflow will require at least " + sTime + ".";
    }

    function fsFormatDurationLong(fSeconds) {
        var iDays = Math.floor(fSeconds / 86400);
        var iHours = Math.floor((fSeconds % 86400) / 3600);
        var iMinutes = Math.floor((fSeconds % 3600) / 60);
        var listParts = [];
        if (iDays > 0) listParts.push(iDays + " day" +
            (iDays > 1 ? "s" : ""));
        if (iHours > 0) listParts.push(iHours + " hour" +
            (iHours > 1 ? "s" : ""));
        if (iMinutes > 0 || listParts.length === 0) {
            listParts.push(iMinutes + " minute" +
                (iMinutes !== 1 ? "s" : ""));
        }
        return listParts.join(", ");
    }

    function fnVerify() {
        fnSendPipelineAction({ sAction: "verify" });
    }

    async function fnValidateReferences() {
        if (!sContainerId) return;
        try {
            var response = await fetch(
                "/api/steps/" + sContainerId + "/validate"
            );
            var result = await response.json();
            var listWarnings = result.listWarnings;
            if (listWarnings.length === 0) {
                fnShowToast(
                    "All cross-step references are valid",
                    "success"
                );
            } else {
                listWarnings.forEach(function (sWarning) {
                    fnShowToast(sWarning, "error");
                });
            }
        } catch (error) {
            fnShowToast(fsSanitizeErrorForUser(error.message), "error");
        }
    }

    function fnOpenVsCode() {
        var sHexId = sContainerId.replace(/-/g, "");
        var sUri =
            "vscode://ms-vscode-remote.remote-containers/attach?containerId=" +
            sHexId;
        window.open(sUri, "_blank");
        fnShowToast("Opening VS Code...", "success");
    }

    /* --- Context Menu --- */

    var iContextStepIndex = -1;

    function fnShowContextMenu(iX, iY, iIndex) {
        iContextStepIndex = iIndex;
        var el = document.getElementById("contextMenu");
        el.style.left = iX + "px";
        el.style.top = iY + "px";
        el.classList.add("active");
    }

    var sContextFilePath = "";
    var sContextFileWorkdir = "";
    var iContextFileStepIndex = -1;

    function fnShowFileContextMenu(
        iX, iY, sFilePath, sWorkdir, iStepIndex
    ) {
        fnHideContextMenu();
        sContextFilePath = sFilePath;
        sContextFileWorkdir = sWorkdir;
        iContextFileStepIndex = iStepIndex;
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
                        el.dataset.action, iContextStepIndex);
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
        if (sAction === "copyPath") {
            navigator.clipboard.writeText(sContextFilePath)
                .then(function () {
                    fnShowToast("Copied to clipboard", "success");
                });
            return;
        }
        if (sAction === "addToGit") {
            fnShowToast("Adding to Git...", "success");
            try {
                var response = await fetch(
                    "/api/github/" + sContainerId + "/add-file",
                    {
                        method: "POST",
                        headers: {
                            "Content-Type": "application/json",
                        },
                        body: JSON.stringify({
                            sFilePath: sContextFilePath,
                        }),
                    }
                );
                var dictResult = await response.json();
                if (dictResult.bSuccess) {
                    fnShowToast("Added to Git", "success");
                    await fnLoadSyncStatus();
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
                var zenodoResponse = await fetch(
                    "/api/zenodo/" + sContainerId + "/archive",
                    {
                        method: "POST",
                        headers: {
                            "Content-Type": "application/json",
                        },
                        body: JSON.stringify({
                            listFilePaths: [sContextFilePath],
                        }),
                    }
                );
                var dictZenodoResult = await zenodoResponse.json();
                if (dictZenodoResult.bSuccess) {
                    fnShowToast("Archived to Zenodo", "success");
                    await fnLoadSyncStatus();
                    fnRenderStepList();
                } else {
                    fnShowSyncError(dictZenodoResult, "Zenodo");
                }
            } catch (error) {
                fnShowToast(fsSanitizeErrorForUser(error.message), "error");
            }
        }
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
        var sName = dictWorkflow.listSteps[iIndex].sName;
        fnShowConfirmModal(
            "Delete Step",
            'Delete step "' + sName + '"?',
            function () { _fnExecuteDeleteStep(iIndex); }
        );
    }

    async function _fnExecuteDeleteStep(iIndex) {
        try {
            var response = await fetch(
                "/api/steps/" + sContainerId + "/" + iIndex,
                { method: "DELETE" }
            );
            if (response.ok) {
                var result = await response.json();
                dictWorkflow.listSteps = result.listSteps;
                if (iSelectedStepIndex === iIndex) iSelectedStepIndex = -1;
                setExpandedSteps.delete(iIndex);
                fnRenderStepList();
                fnShowToast(
                    "Step deleted (references renumbered)",
                    "success"
                );
            }
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

    function fsSanitizeErrorForUser(sRawError) {
        if (!sRawError) return "An error occurred.";
        console.error("[vaibify]", sRawError);
        if (sRawError.indexOf("no space left on device") >= 0) {
            return "Docker disk is full. Run 'docker image " +
                "prune -f' to free space.";
        }
        if (sRawError.indexOf("No such container") >= 0) {
            return "Container not found. It may have stopped.";
        }
        if (sRawError.indexOf("connection refused") >= 0 ||
            sRawError.indexOf("Cannot connect") >= 0) {
            return "Cannot connect to Docker. Is it running?";
        }
        if (sRawError.indexOf("permission denied") >= 0) {
            return "Permission denied. Check Docker access.";
        }
        if (sRawError.length > 200) {
            return sRawError.substring(0, 200) + "...";
        }
        return sRawError;
    }

    function fnShowToast(sMessage, sType) {
        var el = document.createElement("div");
        el.className = "toast " + (sType || "");
        if (sType === "error") {
            el.innerHTML = fnEscapeHtml(sMessage) +
                '<button class="toast-close">&times;</button>';
            el.querySelector(".toast-close").addEventListener(
                "click", function () { el.remove(); }
            );
        } else {
            el.textContent = sMessage;
            var iTimeout = sType === "warning" ? 8000 : 4000;
            setTimeout(function () { el.remove(); }, iTimeout);
        }
        document.getElementById("toastContainer").appendChild(el);
    }

    var fnEscapeHtml = VaibifyUtilities.fnEscapeHtml;

    function fsGetDashboardMode() {
        return _dictDashboardMode ? _dictDashboardMode.sMode : null;
    }

    /* --- Public API --- */

    return {
        fnInitialize: fnInitialize,
        fnShowToast: fnShowToast,
        fnRenderStepList: fnRenderStepList,
        fnEscapeHtml: fnEscapeHtml,
        fsGetContainerId: function () { return sContainerId; },
        fsGetSessionToken: function () { return sSessionToken; },
        fdictGetWorkflow: function () { return dictWorkflow; },
        fsGetWorkflowPath: function () { return sWorkflowPath; },
        fiGetSelectedStepIndex: function () { return iSelectedStepIndex; },
        fdictBuildClientVariables: fdictBuildClientVariables,
        fsResolveTemplate: fsResolveTemplate,
        fnShowConfirmModal: fnShowConfirmModal,
        fnClearOutputModified: fnClearOutputModified,
        fnFinalizeGeneratedTest: fnFinalizeGeneratedTest,
        fnCancelGeneratedTest: fnCancelGeneratedTest,
        fbIsTestPending: function (iStep) {
            return setGeneratedTestsPending.has(iStep);
        },
        fsGetDashboardMode: fsGetDashboardMode,
    };
})();

/* --- File Browser --- */

var PipeleyenFiles = (function () {
    "use strict";

    var sCurrentPath = "/workspace";

    async function fnLoadDirectory(sPath) {
        sCurrentPath = sPath || "/workspace";
        var sContainerId = PipeleyenApp.fsGetContainerId();
        if (!sContainerId) return;

        fnRenderBreadcrumb(sCurrentPath);

        try {
            var response = await fetch(
                "/api/files/" + sContainerId + sCurrentPath
            );
            var listEntries = await response.json();
            fnRenderFileList(listEntries);
        } catch (error) {
            document.getElementById("listFiles").innerHTML =
                '<p style="padding:14px;color:var(--text-muted)">Error loading directory</p>';
        }
    }

    function fnRenderBreadcrumb(sPath) {
        var elBreadcrumb = document.getElementById("fileBreadcrumb");
        var listParts = sPath.split("/").filter(Boolean);
        var sHtml = "";
        var sBuiltPath = "";
        listParts.forEach(function (sPart, iIndex) {
            sBuiltPath += "/" + sPart;
            var sPathCopy = sBuiltPath;
            if (iIndex > 0) sHtml += " / ";
            sHtml += '<span class="crumb" data-path="' +
                sPathCopy + '">' + sPart + "</span>";
        });
        elBreadcrumb.innerHTML = sHtml;
        elBreadcrumb.querySelectorAll(".crumb").forEach(function (el) {
            el.addEventListener("click", function () {
                fnLoadDirectory(el.dataset.path);
            });
        });
    }

    function fnRenderFileList(listEntries) {
        var elList = document.getElementById("listFiles");
        if (listEntries.length === 0) {
            elList.innerHTML =
                '<p style="padding:14px;color:var(--text-muted)">Empty directory</p>';
            return;
        }
        listEntries.sort(function (a, b) {
            if (a.bIsDirectory !== b.bIsDirectory) {
                return a.bIsDirectory ? -1 : 1;
            }
            return a.sName.localeCompare(b.sName);
        });

        elList.innerHTML = listEntries.map(function (entry) {
            var sIconClass = entry.bIsDirectory ? "dir" : "";
            var sIcon = entry.bIsDirectory ? "&#128193;" : "&#128196;";
            var sLower = entry.sName.toLowerCase();
            if (sLower.endsWith(".pdf") || sLower.endsWith(".png") ||
                sLower.endsWith(".jpg") || sLower.endsWith(".svg")) {
                sIconClass = "figure";
            }
            return (
                '<div class="file-item" data-path="' + entry.sPath +
                '" data-is-dir="' + entry.bIsDirectory +
                '" draggable="true">' +
                '<span class="file-icon ' + sIconClass + '">' +
                sIcon + "</span>" +
                '<span class="file-name">' + entry.sName + "</span>" +
                "</div>"
            );
        }).join("");

        elList.querySelectorAll(".file-item").forEach(function (el) {
            el.addEventListener("click", function () {
                if (el.dataset.isDir === "true") {
                    fnLoadDirectory(el.dataset.path);
                } else {
                    PipeleyenFigureViewer.fnDisplayInNextViewer(
                        el.dataset.path
                    );
                }
            });
            el.addEventListener("dragstart", function (event) {
                event.dataTransfer.setData(
                    "vaibify/filepath", el.dataset.path
                );
            });
        });
    }

    return {
        fnLoadDirectory: fnLoadDirectory,
    };
})();

document.addEventListener("DOMContentLoaded", PipeleyenApp.fnInitialize);
