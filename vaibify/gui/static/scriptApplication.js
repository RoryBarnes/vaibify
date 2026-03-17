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
        fnLoadContainers();
        fnBindToolbarEvents();
        fnBindWorkflowPickerEvents();
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

    function fnRenderContainerList(listContainers) {
        var elList = document.getElementById("listContainers");
        if (listContainers.length === 0) {
            elList.innerHTML =
                '<p style="color: var(--text-muted); text-align: center;">' +
                "No running containers found</p>";
            return;
        }
        elList.innerHTML = listContainers
            .map(function (container) {
                return (
                    '<div class="container-card" data-id="' +
                    container.sContainerId + '">' +
                    '<span class="name">' +
                    fnEscapeHtml(container.sName) + "</span>" +
                    '<span class="image">' +
                    fnEscapeHtml(container.sImage) + "</span></div>"
                );
            })
            .join("");
        elList.querySelectorAll(".container-card").forEach(function (el) {
            el.addEventListener("click", function () {
                fnConnectToContainer(el.dataset.id);
            });
        });
    }

    var _sSelectedContainerId = null;
    var _sSelectedContainerName = null;

    async function fnConnectToContainer(sId) {
        try {
            var responseWorkflows = await fetch("/api/workflows/" + sId);
            var listWorkflows = await responseWorkflows.json();
            if (listWorkflows.length === 0) {
                fnShowToast("No workflows found in container", "error");
                return;
            }
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
        elList.innerHTML = listWorkflows.map(function (dictWf) {
            return (
                '<div class="container-card" data-path="' +
                fnEscapeHtml(dictWf.sPath) + '">' +
                '<span class="name">' +
                fnEscapeHtml(dictWf.sName) + '</span>' +
                '<span class="image">' +
                fnEscapeHtml(dictWf.sPath) + '</span></div>'
            );
        }).join("");
        elList.querySelectorAll(".container-card").forEach(function (el) {
            el.addEventListener("click", function () {
                var sPath = el.dataset.path;
                var sName = el.querySelector(".name").textContent;
                fnSelectWorkflow(sId, sPath, sName);
            });
        });
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
        document.getElementById("workflowPickerSubtitle").textContent =
            "Select Workflow \u2014 " + sContainerName;
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
                    PipeleyenFiles.fnLoadDirectory(fsGetWorkflowDirectory());
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
    }

    function fsRenderStepItem(step, iIndex, dictVars) {
        var sStatusClass = dictStepStatus[iIndex] || "";
        var bEnabled = step.bEnabled !== false;
        var bSelected = iIndex === iSelectedStepIndex;
        var bExpanded = setExpandedSteps.has(iIndex);

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
            '<span class="step-status ' + sStatusClass + '"></span>' +
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
        sHtml += '<div class="detail-label">Plot Only: ' +
            (step.bPlotOnly !== false ? "Yes" : "No") + "</div>";

        /* Setup Commands */
        sHtml += fsRenderSectionLabel(
            "Setup Commands", iIndex, "saSetupCommands"
        );
        if (step.saSetupCommands) {
            step.saSetupCommands.forEach(function (sCmd, iCmdIdx) {
                sHtml += fsRenderDetailItem(
                    sCmd, dictVars, "command", "saSetupCommands",
                    iIndex, iCmdIdx
                );
            });
        }

        /* Commands */
        sHtml += fsRenderSectionLabel("Commands", iIndex, "saCommands");
        if (step.saCommands) {
            step.saCommands.forEach(function (sCmd, iCmdIdx) {
                sHtml += fsRenderDetailItem(
                    sCmd, dictVars, "command", "saCommands",
                    iIndex, iCmdIdx
                );
            });
        }

        /* Output Files */
        sHtml += fsRenderSectionLabel(
            "Output Files", iIndex, "saOutputFiles"
        );
        if (step.saOutputFiles) {
            step.saOutputFiles.forEach(function (sFile, iFileIdx) {
                sHtml += fsRenderDetailItem(
                    sFile, dictVars, "output", "saOutputFiles",
                    iIndex, iFileIdx, sResolvedDir
                );
            });
        }

        sHtml += "</div>";
        return sHtml;
    }

    function fsRenderSectionLabel(sLabel, iStepIdx, sArrayKey) {
        return '<div class="detail-label">' +
            '<span>' + sLabel + '</span>' +
            '<button class="section-add" data-step="' + iStepIdx +
            '" data-array="' + sArrayKey +
            '" title="Add item">+</button>' +
            '</div>';
    }

    function fsRenderDetailItem(
        sRaw, dictVars, sType, sArrayKey, iStepIdx, iItemIdx,
        sWorkdir
    ) {
        var sResolved = fsResolveTemplate(sRaw, dictVars);
        var sFileClass = "";
        if (sType === "output") {
            sFileClass = " " + fsFileTypeClass(sResolved);
        }

        var sHtml = '<div class="detail-item ' + sType +
            '" data-step="' + iStepIdx +
            '" data-array="' + sArrayKey +
            '" data-idx="' + iItemIdx +
            '" data-resolved="' + fnEscapeHtml(sResolved) +
            '" data-workdir="' + fnEscapeHtml(sWorkdir || "") +
            '" draggable="true">';

        sHtml += '<div class="detail-text' + sFileClass + '">' +
            fnEscapeHtml(sResolved) +
            '</div>';

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

    var SET_FIGURE_EXTENSIONS = new Set([
        ".pdf", ".png", ".jpg", ".jpeg", ".svg",
    ]);

    function fbIsFigureFile(sPath) {
        var iDot = sPath.lastIndexOf(".");
        if (iDot === -1) return false;
        return SET_FIGURE_EXTENSIONS.has(
            sPath.substring(iDot).toLowerCase()
        );
    }

    /* --- Step Event Binding --- */

    function fnBindStepEvents() {
        var elList = document.getElementById("listSteps");

        elList.querySelectorAll(".step-item").forEach(function (el) {
            var iIndex = parseInt(el.dataset.index);

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

            /* Step header drag: reorder steps */
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
        });

        /* Bind detail item clicks and actions */
        elList.querySelectorAll(".detail-item").forEach(function (el) {
            fnBindDetailItemEvents(el);
        });

        /* Bind section add buttons */
        elList.querySelectorAll(".section-add").forEach(function (el) {
            el.addEventListener("click", function (event) {
                event.stopPropagation();
                fnAddNewItem(
                    parseInt(el.dataset.step), el.dataset.array
                );
            });
        });

        /* Bind drop targets on detail sections */
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
                        fnShowToast("File cannot be viewed", "error");
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

    async function fnHandleDetailDrop(sDetailData, iTargetStep) {
        var dictDrag = JSON.parse(sDetailData);
        var iSource = dictDrag.iStep;
        var sArray = dictDrag.sArray;
        var iIdx = dictDrag.iIdx;
        if (iSource === iTargetStep) return;

        if (!confirm(
            "WARNING: Moving a command may break dependencies " +
            "in later steps.\n\nProceed?"
        )) {
            return;
        }

        var sValue = dictWorkflow.listSteps[iSource][sArray].splice(
            iIdx, 1
        )[0];
        var sTargetArray = sArray;
        if (!dictWorkflow.listSteps[iTargetStep][sTargetArray]) {
            dictWorkflow.listSteps[iTargetStep][sTargetArray] = [];
        }
        dictWorkflow.listSteps[iTargetStep][sTargetArray].unshift(sValue);
        fnPushUndo({
            sAction: "move",
            iStep: iSource,
            sArray: sArray,
            iIdx: iIdx,
            iTargetStep: iTargetStep,
            iTargetIdx: 0,
            sValue: sValue,
        });
        await fnSaveStepArray(iSource, sArray);
        await fnSaveStepArray(iTargetStep, sTargetArray);

        /* Expand target and highlight */
        setExpandedSteps.add(iTargetStep);
        fnRenderStepList();
        fnHighlightItem(iTargetStep, sTargetArray, 0);
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
        var sPlaceholder = sArrayKey === "saOutputFiles" ?
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
                document.getElementById("mainLayout").style.gridTemplateColumns =
                    iWidth + "px 1fr";
            });
        }

        var elHandleV = document.getElementById("resizeHandleVertical");
        if (elHandleV) {
            var elViewerDual = document.getElementById("panelViewerDual");
            fnMakeDraggableVertical(elHandleV, function (iDeltaY) {
                var iHeight = elViewerDual.offsetHeight + iDeltaY;
                iHeight = Math.max(80, iHeight);
                elViewerDual.style.flex = "0 0 " + iHeight + "px";
            });
        }

        var elHandleViewer = document.getElementById("resizeHandleViewer");
        if (elHandleViewer) {
            var elViewerA = document.getElementById("viewerA");
            fnMakeDraggable(elHandleViewer, function (iDeltaX) {
                var iWidth = elViewerA.offsetWidth + iDeltaX;
                iWidth = Math.max(100, iWidth);
                elViewerA.style.flex = "0 0 " + iWidth + "px";
            });
        }
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
        document.getElementById("activeWorkflowName").addEventListener(
            "click", function () {
                if (_sSelectedContainerId) {
                    fnConnectToContainer(_sSelectedContainerId);
                }
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
            fnAppendPipelineOutput(
                "FAILED: " + dictEvent.sCommand +
                " (in " + dictEvent.sDirectory +
                ", exit " + dictEvent.iExitCode + ")"
            );
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
        } else if (dictEvent.sType === "failed") {
            fnShowToast(
                "Pipeline failed (exit " + dictEvent.iExitCode + ")", "error"
            );
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

    function fnShowToast(sMessage, sType) {
        var el = document.createElement("div");
        el.className = "toast " + (sType || "");
        el.textContent = sMessage;
        document.getElementById("toastContainer").appendChild(el);
        setTimeout(function () { el.remove(); }, 4000);
    }

    /* --- Utilities --- */

    function fnEscapeHtml(sText) {
        var el = document.createElement("span");
        el.textContent = sText;
        return el.innerHTML;
    }

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
