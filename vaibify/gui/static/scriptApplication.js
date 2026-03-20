/* Pipeleyen — Main application logic */

const PipeleyenApp = (function () {
    "use strict";

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
    var SET_TEXT_EXTENSIONS = new Set([
        ".json", ".txt", ".csv", ".tsv", ".log", ".yaml", ".yml",
        ".xml", ".ini", ".cfg", ".conf", ".md", ".rst", ".in",
        ".out", ".py", ".c", ".h", ".js", ".css", ".html",
    ]);
    let wsPipeline = null;
    let dictStepStatus = {};

    /* --- Initialization --- */

    function fnInitialize() {
        fnLoadUserName();
        fnLoadContainers();
        fnBindToolbarEvents();
        fnBindWorkflowPickerEvents();
        fnBindUnconfiguredToggle();
        fnBindRefreshButton();
        fnBindErrorModal();
        fnBindApiConfirmModal();
        fnBindContextMenuEvents();
        fnBindLeftPanelTabs();
        fnBindResizeHandles();
        fnBindGlobalSettingsToggle();
        document.addEventListener("click", function () {
            fnHideContextMenu();
        });
        document.addEventListener("keydown", function (event) {
            if ((event.ctrlKey || event.metaKey) && event.key === "z") {
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

    /* --- Container Picker --- */

    async function fnLoadContainers() {
        try {
            var response = await fetch("/api/containers");
            var listContainers = await response.json();
            fnRenderContainerList(listContainers);
        } catch (error) {
            document.getElementById("listContainers").innerHTML =
                '<p style="color: var(--color-red);">Cannot connect to Docker</p>';
        }
    }

    function fsRenderContainerCard(container, sExtraClass) {
        return (
            '<div class="container-card' +
            (sExtraClass ? " " + sExtraClass : "") +
            '" data-id="' + container.sContainerId + '">' +
            '<span class="name">' +
            fnEscapeHtml(container.sName) + "</span>" +
            '<span class="image">' +
            fnEscapeHtml(container.sImage) + "</span></div>"
        );
    }

    function fnBindContainerCards(elParent) {
        elParent.querySelectorAll(".container-card").forEach(function (el) {
            el.addEventListener("click", function () {
                fnConnectToContainer(el.dataset.id);
            });
        });
    }

    function fnRenderContainerList(listContainers) {
        var elList = document.getElementById("listContainers");
        var elUnconfiguredSection = document.getElementById(
            "unconfiguredSection"
        );
        var elUnconfiguredList = document.getElementById("listUnconfigured");
        var elLabel = document.getElementById("labelConfigured");

        var listConfigured = listContainers.filter(function (c) {
            return c.bConfigured;
        });
        var listUnconfigured = listContainers.filter(function (c) {
            return !c.bConfigured;
        });

        if (listContainers.length === 0) {
            elLabel.style.display = "none";
            elList.innerHTML =
                '<p class="muted-text" style="text-align: center;">' +
                "No running containers found</p>";
            elUnconfiguredSection.style.display = "none";
            return;
        }

        elLabel.style.display = "";
        if (listConfigured.length === 0) {
            elList.innerHTML =
                '<p class="muted-text" style="text-align: center;">' +
                "No configured containers found</p>";
        } else {
            elList.innerHTML = listConfigured.map(function (c) {
                return fsRenderContainerCard(c, "");
            }).join("");
            fnBindContainerCards(elList);
        }

        if (listUnconfigured.length > 0) {
            elUnconfiguredSection.style.display = "";
            elUnconfiguredList.innerHTML = listUnconfigured.map(
                function (c) {
                    return fsRenderContainerCard(c, "unconfigured");
                }
            ).join("");
            fnBindContainerCards(elUnconfiguredList);
        } else {
            elUnconfiguredSection.style.display = "none";
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
            if (listWorkflows.length === 1) {
                fnSelectWorkflow(sId, listWorkflows[0].sPath,
                    listWorkflows[0].sName);
            } else {
                fnShowWorkflowPicker(_sSelectedContainerName);
                fnRenderWorkflowList(listWorkflows, sId);
            }
        } catch (error) {
            fnShowToast("Connection failed: " + error.message, "error");
        }
    }

    function _fsContainerNameById(sId) {
        var el = document.querySelector(
            '.container-card[data-id="' + sId + '"] .name'
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
                return (
                    '<div class="container-card" data-path="' +
                    fnEscapeHtml(dictWf.sPath) + '">' +
                    '<span class="name">' +
                    fnEscapeHtml(dictWf.sName) + '</span>' +
                    '<span class="image">' +
                    fnEscapeHtml(dictWf.sPath) + '</span></div>'
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

    async function fnCreateNewWorkflow() {
        if (!_sSelectedContainerId) return;
        var sName = prompt("Workflow display name:", "My Workflow");
        if (!sName) return;
        var sFileName = prompt(
            "Filename (no spaces, .json added automatically):",
            sName.toLowerCase().replace(/[^a-z0-9]+/g, "-")
        );
        if (!sFileName) return;
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
            fnShowToast("Create failed: " + error.message, "error");
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
                fnShowToast(detail.detail || "Connection failed", "error");
                return;
            }
            var data = await response.json();
            sContainerId = sId;
            dictWorkflow = data.dictWorkflow;
            sWorkflowPath = data.sWorkflowPath;
            dictStepStatus = {};
            dictFileExistenceCache = {};
            setStepsWithData.clear();
            bFileCheckInProgress = false;
            bDelegatedEventsInitialized = false;
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
            fnShowMainLayout();
            fnLoadSyncStatus();
            fnRenderStepList();
            PipeleyenTerminal.fnCreateTab();
        } catch (error) {
            fnShowToast("Connection failed: " + error.message, "error");
        }
    }

    function fnShowContainerPicker() {
        document.getElementById("containerPicker").style.display = "flex";
        document.getElementById("workflowPicker").style.display = "none";
        document.getElementById("mainLayout").classList.remove("active");
    }

    function fnShowWorkflowPicker(sContainerName) {
        document.getElementById("containerPicker").style.display = "none";
        document.getElementById("workflowPicker").style.display = "flex";
        document.getElementById("mainLayout").classList.remove("active");
    }

    function fnShowMainLayout() {
        document.getElementById("containerPicker").style.display = "none";
        document.getElementById("workflowPicker").style.display = "none";
        document.getElementById("mainLayout").classList.add("active");
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
        if (wsPipeline) {
            wsPipeline.close();
            wsPipeline = null;
        }
        PipeleyenTerminal.fnCloseAll();
        fnShowContainerPicker();
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
        return {
            sPlotDirectory: dictWorkflow.sPlotDirectory || "Plot",
            sRepoRoot: sRepoRoot,
            iNumberOfCores: dictWorkflow.iNumberOfCores || -1,
            sFigureType: (dictWorkflow.sFigureType || "pdf").toLowerCase(),
        };
    }

    function fsResolveTemplate(sTemplate, dictVariables) {
        return sTemplate.replace(/\{([^}]+)\}/g, function (sMatch, sToken) {
            if (dictVariables.hasOwnProperty(sToken)) {
                return String(dictVariables[sToken]);
            }
            return sMatch;
        });
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
                document.getElementById("panelSteps").classList.toggle(
                    "active", sPanel === "steps"
                );
                document.getElementById("panelFiles").classList.toggle(
                    "active", sPanel === "files"
                );
                document.getElementById("panelLogs").classList.toggle(
                    "active", sPanel === "logs"
                );
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
            '</div>';
        el.querySelectorAll(".gs-input").forEach(function (inp) {
            inp.addEventListener("change", fnSaveGlobalSettings);
        });
    }

    async function fnSaveGlobalSettings() {
        var dictUpdates = {
            sPlotDirectory: document.getElementById("gsPlotDirectory").value,
            sFigureType: document.getElementById("gsFigureType").value,
            iNumberOfCores: parseInt(
                document.getElementById("gsNumberOfCores").value
            ),
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
            if (bFileCheckInProgress) return;
            bFileCheckInProgress = true;
            iInflightRequests = 0;
            fnCheckOutputFileExistence();
            fnCheckDataFileExistence();
            if (iInflightRequests === 0) {
                bFileCheckInProgress = false;
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
        Object.keys(dictStepStatus).forEach(function (sKey) {
            var sStatus = dictStepStatus[sKey];
            if (sStatus === "queued" || sStatus === "running") {
                delete dictStepStatus[sKey];
            }
        });
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

    function fnCheckDataFileExistence() {
        if (!sContainerId || !dictWorkflow) return;
        dictWorkflow.listSteps.forEach(function (step, iStep) {
            if (!setExpandedSteps.has(iStep)) return;
            if (setStepsWithData.has(iStep)) return;
            var listData = step.saDataFiles || [];
            if (listData.length === 0) return;
            var iPresent = 0;
            var iTotal = listData.length;
            listData.forEach(function (sFile) {
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
                var sUrl = "/api/figure/" + sContainerId + "/" +
                    sFile + "?sWorkdir=" +
                    encodeURIComponent(sDir);
                iInflightRequests++;
                fetch(sUrl, { method: "HEAD" }).then(function (r) {
                    if (r.ok) {
                        dictFileExistenceCache[sCacheKey] = true;
                        iPresent++;
                        if (iPresent >= iTotal) {
                            setStepsWithData.add(iStep);
                            fnUpdateGenerateButton(iStep);
                        }
                    }
                    fnFileCheckComplete();
                }).catch(function () { fnFileCheckComplete(); });
            });
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
            if (sArray === "saDataFiles") {
                dictDataCounts[iStep] =
                    (dictDataCounts[iStep] || 0) + 1;
            }
            if (dictFileExistenceCache[sCacheKey] === true) {
                fnMarkOutputPresent(el);
                fnTrackDataPresence(
                    iStep, sArray, dictDataCounts, dictDataPresent
                );
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
                    fnMarkOutputPresent(el);
                    fnTrackDataPresence(
                        iStep, sArray, dictDataCounts,
                        dictDataPresent
                    );
                } else {
                    fnMarkOutputMissing(el);
                }
                fnFileCheckComplete();
            }).catch(function () {
                dictFileExistenceCache[sCacheKey] = false;
                fnMarkOutputMissing(el);
                fnFileCheckComplete();
            });
        });
    }

    function fnTrackDataPresence(
        iStep, sArray, dictCounts, dictPresent
    ) {
        if (sArray !== "saDataFiles") return;
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

    function fnMarkOutputPresent(el) {
        var elText = el.querySelector(".detail-text");
        if (!elText) return;
        var sResolved = el.dataset.resolved;
        elText.classList.remove("file-missing");
        elText.classList.add(fsFileTypeClass(sResolved));
    }

    function fnMarkOutputMissing(el) {
        var elText = el.querySelector(".detail-text");
        if (elText) {
            elText.classList.remove(
                "file-figure", "file-text", "file-binary"
            );
            elText.classList.add("file-missing");
        }
    }

    function fsRenderStepItem(step, iIndex, dictVars) {
        var sRunStatus = dictStepStatus[iIndex] || "";
        var sStatusClass = "";
        if (sRunStatus === "running" || sRunStatus === "queued") {
            sStatusClass = sRunStatus;
        } else if (sRunStatus === "fail") {
            sStatusClass = "fail";
        } else if (sRunStatus === "pass") {
            sStatusClass = "pass";
        } else {
            sStatusClass = fsComputeStepDotState(step, iIndex);
        }
        var bEnabled = step.bEnabled !== false;
        var bSelected = iIndex === iSelectedStepIndex;
        var bExpanded = setExpandedSteps.has(iIndex);

        var sStatusContent = "";
        if (sStatusClass === "verified") {
            sStatusContent = "&#10003;";
        }

        var sHtml =
            '<div class="step-item' + (bSelected ? " selected" : "") +
            '" data-index="' + iIndex + '" draggable="true">' +
            '<input type="checkbox" class="step-checkbox"' +
            (bEnabled ? " checked" : "") + ">" +
            '<span class="step-number">' +
            String(iIndex + 1).padStart(2, "0") + "</span>" +
            '<span class="step-name" title="' +
            fnEscapeHtml(step.sName) + '">' +
            fnEscapeHtml(step.sName) + "</span>" +
            '<span class="step-status ' + sStatusClass + '">' +
            sStatusContent + '</span>' +
            '<span class="step-actions">' +
            '<button class="btn-icon step-edit" title="Edit">&#9998;</button>' +
            "</span></div>";

        if (!bExpanded) {
            return sHtml;
        }

        sHtml += '<div class="step-detail expanded' +
            '" data-index="' + iIndex + '">';

        /* Directory */
        var sResolvedDir = fsResolveTemplate(step.sDirectory, dictVars);
        sHtml += '<div class="detail-label">Directory</div>';
        sHtml += '<div class="detail-field" data-view="field">' +
            fnEscapeHtml(sResolvedDir) + "</div>";
        sHtml += '<div class="detail-label plot-only-row">' +
            '<label class="plot-only-toggle">' +
            '<input type="checkbox" class="plot-only-checkbox"' +
            ' data-step="' + iIndex + '"' +
            (step.bPlotOnly !== false ? " checked" : "") + '>' +
            ' Plot only (skip data analysis)</label></div>';

        /* Run Stats */
        sHtml += fsRenderRunStats(step);

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

        /* Verification */
        sHtml += fsRenderVerificationBlock(step, iIndex);

        /* Discovered outputs */
        sHtml += fsRenderDiscoveredOutputs(iIndex);

        sHtml += "</div>";
        return sHtml;
    }

    function fdictGetVerification(step) {
        return step.dictVerification || {
            sUnitTest: "untested", sUser: "untested",
        };
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
    var setStepsWithData = new Set();
    var setGeneratedTestsPending = new Set();

    function fsEffectiveTestState(step) {
        var dictVerify = fdictGetVerification(step);
        var sState = dictVerify.sUnitTest;
        if (sState === "untested" &&
            (step.saDataCommands || []).length > 0 &&
            (step.saTestCommands || []).length === 0) {
            return "error";
        }
        return sState;
    }

    function flistGetStepDependencies(iStep) {
        if (!dictWorkflow || !dictWorkflow.listSteps) return [];
        var step = dictWorkflow.listSteps[iStep];
        var setDeps = {};
        var listArrays = ["saDataCommands", "saPlotCommands",
            "saTestCommands", "saDataFiles", "saPlotFiles"];
        var rRef = /\{Step(\d+)\.\w+\}/g;
        listArrays.forEach(function (sKey) {
            (step[sKey] || []).forEach(function (sVal) {
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
        var sTestState = fsEffectiveTestState(step);
        var dictVerify = fdictGetVerification(step);
        if (sTestState !== "passed" || dictVerify.sUser !== "passed") {
            dictVisited[iStep] = "fail";
            return false;
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
        var sTestState = fsEffectiveTestState(step);
        var dictVerify = fdictGetVerification(step);
        var sHtml = '<div class="detail-label">Verification</div>';
        sHtml += '<div class="verification-block" data-step="' +
            iIndex + '">';
        sHtml += fsRenderVerificationRow(
            "Unit Tests", sTestState, "unitTest", iIndex
        );
        if (setExpandedUnitTests.has(iIndex)) {
            sHtml += fsRenderUnitTestExpanded(step, iIndex);
        }
        sHtml += fsRenderVerificationRow(
            sUserName, dictVerify.sUser, "user", iIndex
        );
        var sDepsState = fsComputeDepsState(iIndex);
        if (sDepsState !== "none") {
            sHtml += fsRenderVerificationRow(
                "Dependencies", sDepsState, "deps", iIndex
            );
            if (setExpandedDeps.has(iIndex)) {
                sHtml += fsRenderDepsExpanded(iIndex);
            }
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
            var depStep = dictWorkflow.listSteps[iDep];
            if (!depStep) continue;
            var bPassing = fbStepFullyPassing(iDep, dictVisited);
            var sState = bPassing ? "passed" : "failed";
            var sNum = String(iDep + 1).padStart(2, "0");
            sHtml += '<div class="dep-item">' +
                '<span class="dep-label">' + sNum + ' ' +
                fnEscapeHtml(depStep.sName) + '</span>' +
                '<span class="verification-badge state-' +
                sState + '">' +
                fsVerificationStateIcon(sState) + ' ' +
                fsVerificationStateLabel(sState) +
                '</span></div>';
        }
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
        sHtml += fsRenderTestSection(
            "Test Commands", step.saTestCommands, iIndex, "command"
        );
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
        if ((step.saTestCommands || []).length > 0) return "";
        var bDisabled = !setStepsWithData.has(iIndex);
        return '<button class="btn-generate-test" data-step="' +
            iIndex + '"' +
            (bDisabled ? " disabled" : "") +
            ' id="btnGenTest' + iIndex + '">' +
            'Generate Tests</button>';
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
                fnEscapeHtml(listItems[i]) + '</div>';
        }
        return sHtml;
    }

    var sUserName = "User";

    function fnSetVerificationUserName(sName) {
        sUserName = sName || "User";
    }

    function fsComputeStepDotState(step, iIndex) {
        var dictVerify = fdictGetVerification(step);
        var sUnit = fsEffectiveTestState(step);
        var sUser = dictVerify.sUser;
        if (sUnit === "failed" || sUnit === "error" ||
            sUser === "failed" || sUser === "error") {
            return "fail";
        }
        var sDeps = fsComputeDepsState(iIndex);
        if (sDeps === "failed") {
            return "fail";
        }
        if (sUnit === "passed" && sUser === "passed" &&
            sDeps !== "failed") {
            return "verified";
        }
        if ((sUnit === "passed" && sUser === "untested") ||
            (sUnit === "untested" && sUser === "passed")) {
            return "partial";
        }
        return "";
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
        if (!dictStats.sLastRun) return "";
        var sHtml = '<div class="run-stats">';
        sHtml += '<span class="run-stat">Last run: ' +
            fnEscapeHtml(dictStats.sLastRun) + '</span>';
        if (dictStats.fWallClock !== undefined) {
            sHtml += '<span class="run-stat">Wall-clock: ' +
                fsFormatDuration(dictStats.fWallClock) + '</span>';
        }
        sHtml += '</div>';
        return sHtml;
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

    function fbIsInvalidOutputPath(sRaw, sResolved) {
        if (!sResolved || sResolved.length === 0) return true;
        if (sRaw.includes("{")) return false;
        return !sResolved.startsWith("/");
    }

    function fsRenderDetailItem(
        sRaw, dictVars, sType, sArrayKey, iStepIdx, iItemIdx,
        sWorkdir
    ) {
        var sResolved = fsResolveTemplate(sRaw, dictVars);
        var sFileClass = "";
        var bInvalid = false;
        if (sType === "output") {
            if (fbIsInvalidOutputPath(sRaw, sResolved)) {
                sFileClass = " file-invalid";
                bInvalid = true;
            }
        }

        var sHtml = '<div class="detail-item ' + sType +
            '" data-step="' + iStepIdx +
            '" data-array="' + sArrayKey +
            '" data-idx="' + iItemIdx +
            '" data-resolved="' + fnEscapeHtml(sResolved) +
            '" data-workdir="' + fnEscapeHtml(sWorkdir || "") +
            '" draggable="true">';

        if (sType === "output" && !bInvalid) {
            sFileClass = " " + fsFileTypeClass(sResolved);
        }
        if (sArrayKey === "saPlotFiles" && !bInvalid) {
            var sCategory = fsGetPlotCategory(iStepIdx, sRaw);
            var bArchive = sCategory === "archive";
            sHtml += '<span class="archive-star ' +
                (bArchive ? "active" : "inactive") +
                '" data-step="' + iStepIdx +
                '" data-file="' + fnEscapeHtml(sRaw) +
                '" title="' +
                (bArchive ? "Archive plot" : "Supporting plot") +
                '">' + (bArchive ? "\u2605" : "\u2606") +
                '</span>';
        }
        if (bInvalid) {
            sHtml += '<div class="detail-text file-invalid' +
                '" title="Output path is not absolute">' +
                '<em>' + fnEscapeHtml(sResolved) + '</em></div>';
        } else {
            sHtml += '<div class="detail-text' + sFileClass + '">' +
                fnEscapeHtml(sResolved) + '</div>';
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

    function fsGetPlotCategory(iStep, sFilePath) {
        var dictStep = dictWorkflow.listSteps[iStep];
        var dictCategories = dictStep.dictPlotFileCategories || {};
        return dictCategories[sFilePath] || "archive";
    }

    async function fnToggleArchiveCategory(iStep, sFilePath) {
        var dictStep = dictWorkflow.listSteps[iStep];
        if (!dictStep.dictPlotFileCategories) {
            dictStep.dictPlotFileCategories = {};
        }
        var sCurrentCategory = fsGetPlotCategory(iStep, sFilePath);
        var sNewCategory = sCurrentCategory === "archive" ?
            "supporting" : "archive";
        dictStep.dictPlotFileCategories[sFilePath] = sNewCategory;
        await fnSaveStepUpdate(iStep, {
            dictPlotFileCategories: dictStep.dictPlotFileCategories,
        });
        fnRenderStepList();
    }

    function fsFileTypeClass(sPath) {
        var iDot = sPath.lastIndexOf(".");
        if (iDot === -1) return "file-binary";
        var sExt = sPath.substring(iDot).toLowerCase();
        if (SET_FIGURE_EXTENSIONS.has(sExt)) return "file-figure";
        if (SET_TEXT_EXTENSIONS.has(sExt)) return "file-text";
        if (SET_BINARY_EXTENSIONS.has(sExt)) return "file-binary";
        return "file-text";
    }

    var SET_FIGURE_EXTENSIONS = VaibifyUtilities.SET_FIGURE_EXTENSIONS;

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
                elStar.dataset.file
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
            } else if (elText.classList.contains("file-missing")) {
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
        var elVerifUnitTest = elTarget.closest(
            '.verification-row[data-approver="unitTest"]'
        );
        if (elVerifUnitTest) {
            fnToggleUnitTestExpand(
                parseInt(elVerifUnitTest.dataset.step)
            );
            return;
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
                "pipeleyen/detail", JSON.stringify(dictDragData)
            );
            event.dataTransfer.setData(
                "pipeleyen/filepath", elDetail.dataset.resolved
            );
            event.dataTransfer.setData(
                "pipeleyen/workdir", elDetail.dataset.workdir || ""
            );
            return;
        }
        var elStep = event.target.closest(".step-item");
        if (elStep) {
            var iIdx = parseInt(elStep.dataset.index);
            event.dataTransfer.setData("text/plain", String(iIdx));
            event.dataTransfer.setData(
                "pipeleyen/step", String(iIdx)
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
            "pipeleyen/detail"
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
        var elBtn = document.getElementById("btnGenTest" + iStep);
        if (elBtn) {
            if (elBtn.disabled) return;
            elBtn.disabled = true;
            elBtn.innerHTML =
                '<span class="spinner"></span> Building Tests';
        }
        try {
            var response = await fetch(
                "/api/steps/" + sContainerId + "/" + iStep +
                "/generate-test",
                { method: "POST",
                  headers: {"Content-Type": "application/json"},
                  body: JSON.stringify({}) }
            );
            var dictResult = await response.json();
            if (dictResult.bNeedsFallback) {
                fnResetGenerateButton(iStep);
                fnShowApiKeyDialog(iStep);
                return;
            }
            if (!response.ok) {
                fnResetGenerateButton(iStep);
                var sDetail = dictResult.detail ||
                    "Unknown error";
                fnShowErrorModal(
                    "Test generation failed:\n\n" + sDetail
                );
                return;
            }
            if (!dictResult.bGenerated) {
                fnResetGenerateButton(iStep);
                fnShowErrorModal(
                    "Test generation failed:\n\n" +
                    (dictResult.sMessage || "No tests generated")
                );
                return;
            }
            fnHandleGeneratedTest(iStep, dictResult);
        } catch (error) {
            fnResetGenerateButton(iStep);
            fnShowErrorModal(
                "Test generation failed:\n\n" + error.message
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
        dictStep.saTestCommands = ["pytest " +
            dictResult.sFilePath.split("/").pop()];
        dictStep.saTestFiles = [dictResult.sFilePath];
        setGeneratedTestsPending.add(iStep);
        fnRenderStepList();
        PipeleyenFigureViewer.fnDisplayGeneratedTest(
            dictResult.sFilePath, dictResult.sContent, iStep
        );
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
        dictStep.dictVerification.sUnitTest = "passed";
        fnSaveStepUpdate(iStep, {
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
        fnRenderStepList();
    }

    function fnAddTestItem(iStep, sType) {
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
                fnSaveStepArray(iStep, sArray);
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
        alert(
            "Modifying pipeline. Ensure that all subsequent " +
            "steps properly reference the new pipeline."
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
        await fnSaveStepArray(iStep, sArrayKey);
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

    async function fnSaveStepArray(iStep, sArray) {
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
            btnVerify: fnVerify,
            btnValidateReferences: fnValidateReferences,
            btnOverleafPush: function () { fnOpenPushModal("overleaf"); },
            btnGithubPush: function () { fnOpenPushModal("github"); },
            btnZenodoArchive: function () { fnOpenPushModal("zenodo"); },
            btnShowDag: fnShowDag,
            btnVsCode: fnOpenVsCode,
            btnMonitor: function () {},
            btnResetLayout: fnResetLayout,
            btnDisconnect: fnDisconnect,
            btnNewWorkflowToolbar: fnCreateNewWorkflow,
        };
        for (var sId in dictActions) {
            var el = document.getElementById(sId);
            if (el) {
                el.addEventListener("click", dictActions[sId]);
            }
        }
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
            PipeleyenFigureViewer.fnDisplayInNextViewer(
                ".vaibify/dag.svg", ""
            );
        } catch (error) {
            fnShowToast("DAG: " + error.message, "error");
        }
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
            fnShowToast("Push failed: " + error.message, "error");
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
            fnShowToast("Setup failed: " + error.message, "error");
        }
    }

    function fnBindWorkflowPickerEvents() {
        document.getElementById("btnWorkflowBack").addEventListener(
            "click", function () {
                fnShowContainerPicker();
                fnLoadContainers();
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
        if (listWorkflows.length === 0) {
            elDropdown.innerHTML =
                '<div class="workflow-dropdown-item">' +
                '<span class="wf-name muted-text">' +
                'No other workflows</span></div>';
            return;
        }
        elDropdown.innerHTML = listWorkflows.map(function (dictWf) {
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
        fnBindWorkflowDropdownItems(elDropdown);
    }

    function fnBindWorkflowDropdownItems(elDropdown) {
        elDropdown.querySelectorAll(".workflow-dropdown-item")
            .forEach(function (el) {
                el.addEventListener("click", function (event) {
                    event.stopPropagation();
                    fnHideWorkflowDropdown();
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

    function fnBindRefreshButton() {
        document.getElementById("btnRefreshContainers").addEventListener(
            "click", function () {
                fnLoadContainers();
            }
        );
    }

    function fnBindUnconfiguredToggle() {
        document.getElementById("btnShowUnconfigured").addEventListener(
            "click", function () {
                var elList = document.getElementById("listUnconfigured");
                var bVisible = elList.style.display !== "none";
                elList.style.display = bVisible ? "none" : "";
                this.textContent = bVisible
                    ? "See unconfigured containers"
                    : "Hide unconfigured containers";
            }
        );
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
            fnShowToast("Could not load log: " + error.message, "error");
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
            "/ws/pipeline/" + sContainerId;
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
        } else if (dictEvent.sType === "stepSkipped") {
            dictStepStatus[dictEvent.iStepNumber - 1] = "skipped";
            fnAppendPipelineOutput(
                "Step " + dictEvent.iStepNumber +
                ": SKIPPED (inputs unchanged)");
            fnRenderStepList();
        } else if (dictEvent.sType === "discoveredOutputs") {
            fnHandleDiscoveredOutputs(dictEvent);
        } else if (dictEvent.sType === "stepPass") {
            dictStepStatus[dictEvent.iStepNumber - 1] = "pass";
            fnInvalidateStepFileCache(dictEvent.iStepNumber - 1);
            fnRenderStepList();
        } else if (dictEvent.sType === "stepFail") {
            dictStepStatus[dictEvent.iStepNumber - 1] = "fail";
            fnInvalidateStepFileCache(dictEvent.iStepNumber - 1);
            fnRenderStepList();
        } else if (dictEvent.sType === "started") {
            fnInitPipelineOutput();
            fnShowToast("Pipeline started", "success");
        } else if (dictEvent.sType === "completed") {
            fnClearRunningStatuses();
            fnShowToast("Pipeline completed", "success");
            fnRenderStepList();
            if (dictEvent.sLogPath) {
                fnDisplayLogInViewer(dictEvent.sLogPath);
            }
        } else if (dictEvent.sType === "failed") {
            fnClearRunningStatuses();
            fnShowToast(
                "Pipeline failed (exit " + dictEvent.iExitCode + ")", "error"
            );
            fnRenderStepList();
            if (dictEvent.sLogPath) {
                fnDisplayLogInViewer(dictEvent.sLogPath);
            }
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
        fnRenderStepList();
        var sLabel = dictEvent.sResult === "passed" ?
            "Tests passed" : "Tests FAILED";
        fnShowToast("Step " + (iStep + 1) + ": " + sLabel,
            dictEvent.sResult === "passed" ? "success" : "error");
    }

    function fnDisplayLogInViewer(sLogPath) {
        PipeleyenFigureViewer.fnDisplayFileFromContainer(sLogPath);
    }

    function fnShowErrorModal(sMessage) {
        var elModal = document.getElementById("modalError");
        var elContent = document.getElementById("modalErrorContent");
        elContent.textContent = sMessage;
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
            fnShowToast("Generation failed: " + error.message, "error");
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

    function fnRunAll() {
        var sEstimate = fsEstimateRunTime();
        var sMessage = "Run all enabled steps?";
        if (sEstimate) {
            sMessage += "\n\n" + sEstimate;
        }
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

    function fnForceRunAll() {
        fnShowConfirmModal(
            "Force Run All",
            "This will DELETE all existing data and plot " +
            "outputs, clear input hashes, and re-run every " +
            "step from scratch.\n\n" +
            "All verification states will be reset to untested.",
            function () {
                var sEstimate = fsEstimateRunTime();
                var sTimeMsg = sEstimate ?
                    "\n\n" + sEstimate : "";
                fnShowConfirmModal(
                    "Confirm Clean Rebuild",
                    "Are you sure? This cannot be undone." +
                    sTimeMsg,
                    async function () {
                        await _fnExecuteForceRunAll();
                    }
                );
            }
        );
    }

    async function _fnExecuteForceRunAll() {
        fnShowToast("Cleaning outputs...", "success");
        try {
            await fetch(
                "/api/pipeline/" + sContainerId + "/clean",
                { method: "POST" }
            );
        } catch (error) {
            fnShowToast("Clean failed: " + error.message, "error");
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
            fnShowToast("Validation failed: " + error.message, "error");
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
                fnShowToast(error.message, "error");
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
                fnShowToast(error.message, "error");
            }
        }
    }

    function fnHandleContextAction(sAction, iIndex) {
        if (sAction === "edit") {
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
        fnEscapeHtml: fnEscapeHtml,
        fsGetContainerId: function () { return sContainerId; },
        fdictGetWorkflow: function () { return dictWorkflow; },
        fsGetWorkflowPath: function () { return sWorkflowPath; },
        fiGetSelectedStepIndex: function () { return iSelectedStepIndex; },
        fdictBuildClientVariables: fdictBuildClientVariables,
        fsResolveTemplate: fsResolveTemplate,
        fnFinalizeGeneratedTest: fnFinalizeGeneratedTest,
        fnCancelGeneratedTest: fnCancelGeneratedTest,
        fbIsTestPending: function (iStep) {
            return setGeneratedTestsPending.has(iStep);
        },
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
            var sApiPath = sCurrentPath;
            if (sApiPath.startsWith("/")) {
                sApiPath = sApiPath.substring(1);
            }
            var response = await fetch(
                "/api/files/" + sContainerId + "/" + sApiPath
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
                    "pipeleyen/filepath", el.dataset.path
                );
            });
        });
    }

    return {
        fnLoadDirectory: fnLoadDirectory,
    };
})();

document.addEventListener("DOMContentLoaded", PipeleyenApp.fnInitialize);
