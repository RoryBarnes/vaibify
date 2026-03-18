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
            var elWorkflowName = document.getElementById("activeWorkflowName");
            elWorkflowName.textContent = sWorkflowName || "";
            fnShowMainLayout();
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
        return {
            sPlotDirectory: dictWorkflow.sPlotDirectory || "Plot",
            sRepoRoot: sWorkflowDir,
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
        fnCheckOutputFileExistence();
    }

    function fnCheckOutputFileExistence() {
        if (!sContainerId) return;
        setStepsWithData.clear();
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
            var sUrl = "/api/figure/" + sContainerId + "/" +
                sResolved;
            if (sWorkdir) {
                sUrl += "?sWorkdir=" + encodeURIComponent(sWorkdir);
            }
            if (sArray === "saDataFiles") {
                dictDataCounts[iStep] =
                    (dictDataCounts[iStep] || 0) + 1;
            }
            fetch(sUrl, { method: "HEAD" }).then(function (r) {
                if (r.ok) {
                    fnMarkOutputPresent(el);
                    fnTrackDataPresence(
                        iStep, sArray, dictDataCounts,
                        dictDataPresent
                    );
                } else {
                    fnMarkOutputMissing(el);
                }
            }).catch(function () {
                fnMarkOutputMissing(el);
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
        if (sRunStatus === "running") {
            sStatusClass = "running";
        } else {
            sStatusClass = fsComputeStepDotState(step);
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

        sHtml += '<div class="step-detail' +
            (bExpanded ? " expanded" : "") +
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
        sHtml += '</div>';
        return sHtml;
    }

    function fsRenderVerificationRow(
        sLabel, sState, sApprover, iIndex
    ) {
        var sClickClass = sApprover === "user" ? " clickable" :
            " expandable";
        return '<div class="verification-row' + sClickClass +
            '" data-step="' + iIndex +
            '" data-approver="' + sApprover + '">' +
            '<span class="verification-label">' +
            fnEscapeHtml(sLabel) + '</span>' +
            '<span class="verification-badge state-' + sState + '">' +
            fsVerificationStateIcon(sState) + ' ' +
            fsVerificationStateLabel(sState) + '</span></div>';
    }

    function fsRenderUnitTestExpanded(step, iIndex) {
        var sHtml = '<div class="unit-test-expanded">';
        sHtml += fsRenderTestSection(
            "Test Commands", step.saTestCommands, iIndex, "command"
        );
        sHtml += fsRenderTestSection(
            "Test Files", step.saTestFiles, iIndex, "file"
        );
        var sLogPath = (fdictGetVerification(step)).sTestLogPath;
        if (sLogPath) {
            sHtml += '<div class="test-last-run" data-log="' +
                fnEscapeHtml(sLogPath) + '">Last Run: view log</div>';
        }
        if ((step.saDataCommands || []).length > 0 &&
            (step.saTestCommands || []).length === 0) {
            var bDisabled = !setStepsWithData.has(iIndex);
            sHtml += '<button class="btn-generate-test" data-step="' +
                iIndex + '"' +
                (bDisabled ? " disabled" : "") +
                '>Generate Tests</button>';
        }
        sHtml += '</div>';
        return sHtml;
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

    function fsComputeStepDotState(step) {
        var dictVerify = fdictGetVerification(step);
        var sUnit = fsEffectiveTestState(step);
        var sUser = dictVerify.sUser;
        if (sUnit === "failed" || sUnit === "error" ||
            sUser === "failed" || sUser === "error") {
            return "fail";
        }
        if (sUnit === "passed" && sUser === "passed") {
            return "verified";
        }
        if ((sUnit === "passed" && sUser === "untested") ||
            (sUnit === "untested" && sUser === "passed")) {
            return "partial";
        }
        return "";
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

        if (bInvalid) {
            sHtml += '<div class="detail-text' + sFileClass +
                '" title="Output path is not absolute">' +
                '<em>' + fnEscapeHtml(sResolved) + '</em></div>';
        } else {
            sHtml += '<div class="detail-text' + sFileClass + '">' +
                fnEscapeHtml(sResolved) + '</div>';
        }

        sHtml += '<div class="detail-actions">' +
            '<button class="action-edit" title="Edit">&#9998;</button>' +
            '<button class="action-copy" title="Copy">&#9112;</button>' +
            '<button class="action-delete" title="Delete">&#10005;</button>' +
            '</div>';

        sHtml += '</div>';
        return sHtml;
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
    var fbIsFigureFile = VaibifyUtilities.fbIsFigureFile;

    /* --- Step Event Binding --- */

    function fnBindStepClickEvents(el, iIndex) {
        el.addEventListener("click", function (event) {
            if (event.target.classList.contains("step-checkbox") ||
                event.target.classList.contains("step-edit")) {
                return;
            }
            fnToggleStepExpand(iIndex);
        });
        el.addEventListener("contextmenu", function (event) {
            event.preventDefault();
            fnShowContextMenu(event.pageX, event.pageY, iIndex);
        });
        el.querySelector(".step-checkbox").addEventListener(
            "change", function (event) {
                fnToggleStepEnabled(iIndex, event.target.checked);
            }
        );
        var btnEdit = el.querySelector(".step-edit");
        if (btnEdit) {
            btnEdit.addEventListener("click", function () {
                PipeleyenStepEditor.fnOpenEditModal(iIndex);
            });
        }
    }

    function fnBindStepDragEvents(el, iIndex) {
        el.addEventListener("dragstart", function (event) {
            event.dataTransfer.setData("text/plain", String(iIndex));
            event.dataTransfer.setData("pipeleyen/step", String(iIndex));
            el.classList.add("dragging");
        });
        el.addEventListener("dragend", function () {
            el.classList.remove("dragging");
        });
        el.addEventListener("dragover", function (event) {
            event.preventDefault();
            el.classList.add("drop-target");
        });
        el.addEventListener("dragleave", function () {
            el.classList.remove("drop-target");
        });
        el.addEventListener("drop", function (event) {
            event.preventDefault();
            el.classList.remove("drop-target");
            var sDetailData = event.dataTransfer.getData(
                "pipeleyen/detail"
            );
            if (sDetailData) {
                fnHandleDetailDrop(sDetailData, iIndex);
                return;
            }
            var sStepData = event.dataTransfer.getData("text/plain");
            if (sStepData !== "") {
                var iFromIndex = parseInt(sStepData);
                if (iFromIndex !== iIndex) {
                    fnReorderStep(iFromIndex, iIndex);
                }
            }
        });
    }

    function fnBindDetailSectionEvents(elList) {
        elList.querySelectorAll(".detail-item").forEach(function (el) {
            fnBindDetailItemEvents(el);
        });
        elList.querySelectorAll(".section-add").forEach(function (el) {
            el.addEventListener("click", function (event) {
                event.stopPropagation();
                fnAddNewItem(
                    parseInt(el.dataset.step), el.dataset.array
                );
            });
        });
        elList.querySelectorAll(".step-detail").forEach(function (el) {
            el.addEventListener("dragover", function (event) {
                event.preventDefault();
            });
            el.addEventListener("drop", function (event) {
                var sDetailData = event.dataTransfer.getData(
                    "pipeleyen/detail"
                );
                if (sDetailData) {
                    event.preventDefault();
                    event.stopPropagation();
                    var iTargetStep = parseInt(el.dataset.index);
                    fnHandleDetailDrop(sDetailData, iTargetStep);
                }
            });
        });
    }

    function fnBindStepEvents() {
        var elList = document.getElementById("listSteps");
        elList.querySelectorAll(".step-item").forEach(function (el) {
            var iIndex = parseInt(el.dataset.index);
            fnBindStepClickEvents(el, iIndex);
            fnBindStepDragEvents(el, iIndex);
        });
        fnBindDetailSectionEvents(elList);
        fnBindPlotOnlyCheckboxes(elList);
        fnBindVerificationEvents(elList);
    }

    function fnBindPlotOnlyCheckboxes(elList) {
        elList.querySelectorAll(".plot-only-checkbox")
            .forEach(function (el) {
                el.addEventListener("change", function () {
                    var iStep = parseInt(el.dataset.step);
                    fnTogglePlotOnly(iStep, el.checked);
                });
            });
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

    function fnBindVerificationEvents(elList) {
        elList.querySelectorAll(".verification-row.clickable")
            .forEach(function (el) {
                el.addEventListener("click", function () {
                    var iStep = parseInt(el.dataset.step);
                    fnCycleUserVerification(iStep);
                });
            });
        elList.querySelectorAll(
            '.verification-row[data-approver="unitTest"]'
        ).forEach(function (el) {
            el.addEventListener("click", function () {
                var iStep = parseInt(el.dataset.step);
                fnToggleUnitTestExpand(iStep);
            });
        });
        fnBindUnitTestDetailEvents(elList);
    }

    function fnToggleUnitTestExpand(iStep) {
        if (setExpandedUnitTests.has(iStep)) {
            setExpandedUnitTests.delete(iStep);
        } else {
            setExpandedUnitTests.add(iStep);
        }
        fnRenderStepList();
    }

    function fnBindUnitTestDetailEvents(elList) {
        elList.querySelectorAll(".test-file-item").forEach(
            function (el) {
                el.addEventListener("click", function () {
                    PipeleyenFigureViewer.fnDisplayFileFromContainer(
                        el.textContent.trim()
                    );
                });
            }
        );
        elList.querySelectorAll(".test-last-run").forEach(
            function (el) {
                el.addEventListener("click", function () {
                    PipeleyenFigureViewer.fnDisplayFileFromContainer(
                        el.dataset.log
                    );
                });
            }
        );
        elList.querySelectorAll(".test-add").forEach(function (el) {
            el.addEventListener("click", function (event) {
                event.stopPropagation();
                var iStep = parseInt(el.dataset.step);
                var sType = el.dataset.testType;
                fnAddTestItem(iStep, sType);
            });
        });
        elList.querySelectorAll(".btn-generate-test").forEach(
            function (el) {
                el.addEventListener("click", function (event) {
                    event.stopPropagation();
                    fnGenerateTests(parseInt(el.dataset.step));
                });
            }
        );
    }

    async function fnGenerateTests(iStep) {
        fnShowToast("Generating tests...", "success");
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
                fnShowApiKeyDialog(iStep);
                return;
            }
            if (!dictResult.bGenerated) {
                fnShowToast("Generation failed", "error");
                return;
            }
            fnHandleGeneratedTest(iStep, dictResult);
        } catch (error) {
            fnShowToast("Generation failed: " + error.message, "error");
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

    async function fnAddTestItem(iStep, sType) {
        var sPrompt = sType === "file" ?
            "Test file path:" : "Test command:";
        var sValue = prompt(sPrompt);
        if (!sValue || !sValue.trim()) return;
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

    function fnBindDetailItemEvents(el) {
        var iStep = parseInt(el.dataset.step);
        var sArray = el.dataset.array;
        var iIdx = parseInt(el.dataset.idx);
        var sResolved = el.dataset.resolved;
        var sWorkdir = el.dataset.workdir || "";

        /* Click on text to view */
        var elText = el.querySelector(".detail-text");
        if (elText) {
            elText.addEventListener("click", function () {
                if (el.classList.contains("output")) {
                    if (elText.classList.contains("file-binary")) {
                        fnShowBinaryNotViewable();
                    } else if (elText.classList.contains("file-missing")) {
                        fnShowOutputNotAvailable();
                    } else {
                        PipeleyenFigureViewer.fnDisplayInNextViewer(
                            sResolved, sWorkdir
                        );
                    }
                }
            });
        }

        /* Drag detail items — carry source info for cross-step drops */
        el.addEventListener("dragstart", function (event) {
            event.stopPropagation();
            var dictDragData = {
                iStep: iStep,
                sArray: sArray,
                iIdx: iIdx,
            };
            event.dataTransfer.setData(
                "pipeleyen/detail", JSON.stringify(dictDragData)
            );
            event.dataTransfer.setData("pipeleyen/filepath", sResolved);
            event.dataTransfer.setData("pipeleyen/workdir", sWorkdir);
        });

        /* Action: Edit */
        var btnEdit = el.querySelector(".action-edit");
        if (btnEdit) {
            btnEdit.addEventListener("click", function (event) {
                event.stopPropagation();
                fnInlineEditItem(el, iStep, sArray, iIdx);
            });
        }

        /* Action: Copy */
        var btnCopy = el.querySelector(".action-copy");
        if (btnCopy) {
            btnCopy.addEventListener("click", function (event) {
                event.stopPropagation();
                navigator.clipboard.writeText(sResolved).then(function () {
                    fnShowToast("Copied to clipboard", "success");
                });
            });
        }

        /* Action: Delete */
        var btnDelete = el.querySelector(".action-delete");
        if (btnDelete) {
            btnDelete.addEventListener("click", function (event) {
                event.stopPropagation();
                fnDeleteDetailItem(iStep, sArray, iIdx);
            });
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

        function fnFinishEdit() {
            var sNewValue = elInput.value.trim();
            if (sNewValue && sNewValue !== sRaw) {
                dictWorkflow.listSteps[iStep][sArray][iIdx] = sNewValue;
                fnSaveStepArray(iStep, sArray);
            }
            elInput.remove();
            elText.style.display = "";
            elActions.style.display = "";
            fnRenderStepList();
        }

        elInput.addEventListener("keydown", function (event) {
            if (event.key === "Enter") fnFinishEdit();
            if (event.key === "Escape") {
                elInput.remove();
                elText.style.display = "";
                elActions.style.display = "";
            }
        });
        elInput.addEventListener("blur", fnFinishEdit);
    }

    async function fnDeleteDetailItem(iStep, sArray, iIdx) {
        var sValue = dictWorkflow.listSteps[iStep][sArray][iIdx];
        if (!confirm("Delete this item?\n\n" + sValue)) return;
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

    async function fnHandleDetailDrop(sDetailData, iTargetStep) {
        var dictDrag = JSON.parse(sDetailData);
        if (dictDrag.iStep === iTargetStep) return;
        if (!confirm(
            "WARNING: Moving a command may break dependencies " +
            "in later steps.\n\nProceed?"
        )) {
            return;
        }
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
        document.getElementById("btnRunSelected").addEventListener(
            "click", fnRunSelected
        );
        document.getElementById("btnRunAll").addEventListener(
            "click", fnRunAll
        );
        document.getElementById("btnVerify").addEventListener(
            "click", fnVerify
        );
        document.getElementById("btnValidateReferences").addEventListener(
            "click", fnValidateReferences
        );
        document.getElementById("btnVsCode").addEventListener(
            "click", fnOpenVsCode
        );
        document.getElementById("btnResetLayout").addEventListener(
            "click", fnResetLayout
        );
        document.getElementById("btnDisconnect").addEventListener(
            "click", fnDisconnect
        );
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

    async function fnConfirmWorkflowSwitch(sNewPath, sNewName) {
        if (!confirm(
            "Switch to workflow \"" + sNewName + "\"?\n\n" +
            "Current workflow state will be saved."
        )) {
            return;
        }
        await fnSaveCurrentWorkflow();
        fnSelectWorkflow(sContainerId, sNewPath, sNewName);
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
        if (wsPipeline && wsPipeline.readyState === WebSocket.OPEN) {
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
        } else if (dictEvent.sType === "stepPass") {
            dictStepStatus[dictEvent.iStepNumber - 1] = "pass";
            fnRenderStepList();
        } else if (dictEvent.sType === "stepFail") {
            dictStepStatus[dictEvent.iStepNumber - 1] = "fail";
            fnRenderStepList();
        } else if (dictEvent.sType === "started") {
            fnInitPipelineOutput();
            fnShowToast("Pipeline started", "success");
        } else if (dictEvent.sType === "completed") {
            fnShowToast("Pipeline completed", "success");
            if (dictEvent.sLogPath) {
                fnDisplayLogInViewer(dictEvent.sLogPath);
            }
        } else if (dictEvent.sType === "failed") {
            fnShowToast(
                "Pipeline failed (exit " + dictEvent.iExitCode + ")", "error"
            );
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
                dictStepStatus[iIndex] = "running";
            });
        fnRenderStepList();
        fnSendPipelineAction({
            sAction: "runSelected",
            listStepIndices: listIndices,
        });
    }

    function fnRunAll() {
        dictWorkflow.listSteps.forEach(function (_, iIndex) {
            dictStepStatus[iIndex] = "running";
        });
        fnRenderStepList();
        fnSendPipelineAction({ sAction: "runAll" });
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

    function fnHideContextMenu() {
        document.getElementById("contextMenu").classList.remove("active");
    }

    function fnBindContextMenuEvents() {
        document.querySelectorAll(".context-menu-item")
            .forEach(function (el) {
                el.addEventListener("click", function (event) {
                    event.stopPropagation();
                    fnHandleContextAction(el.dataset.action, iContextStepIndex);
                    fnHideContextMenu();
                });
            });
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

    async function fnDeleteStep(iIndex) {
        var sName = dictWorkflow.listSteps[iIndex].sName;
        if (!confirm('Delete step "' + sName + '"?')) return;
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
            var response = await fetch(
                "/api/files/" + sContainerId + "/" +
                encodeURIComponent(sCurrentPath)
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
