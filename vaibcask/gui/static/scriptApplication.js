/* Pipeleyen — Main application logic */

const PipeleyenApp = (function () {
    "use strict";

    let sContainerId = null;
    let dictScript = null;
    let sScriptPath = null;
    let iSelectedSceneIndex = -1;
    let setExpandedScenes = new Set();
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
    let dictSceneStatus = {};

    /* --- Initialization --- */

    function fnInitialize() {
        fnLoadContainers();
        fnBindToolbarEvents();
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

    async function fnConnectToContainer(sId) {
        try {
            var responseScripts = await fetch("/api/scripts/" + sId);
            var listScripts = await responseScripts.json();
            var sChosenPath = null;
            if (listScripts.length === 0) {
                fnShowToast("No script.json found in container", "error");
                return;
            } else if (listScripts.length === 1) {
                sChosenPath = listScripts[0];
            } else {
                sChosenPath = prompt(
                    "Multiple script.json files found:\n\n" +
                    listScripts.map(function (s, i) {
                        return (i + 1) + ") " + s;
                    }).join("\n") + "\n\nEnter the full path:",
                    listScripts[0]
                );
                if (!sChosenPath) return;
            }
            var response = await fetch(
                "/api/connect/" + sId +
                "?sScriptPath=" + encodeURIComponent(sChosenPath),
                { method: "POST" }
            );
            if (!response.ok) {
                var detail = await response.json();
                fnShowToast(detail.detail || "Connection failed", "error");
                return;
            }
            var data = await response.json();
            sContainerId = sId;
            dictScript = data.dictScript;
            sScriptPath = data.sScriptPath;
            fnShowMainLayout();
            fnRenderSceneList();
            PipeleyenTerminal.fnCreateTab();
        } catch (error) {
            fnShowToast("Connection failed: " + error.message, "error");
        }
    }

    function fnShowMainLayout() {
        document.getElementById("containerPicker").style.display = "none";
        document.getElementById("mainLayout").classList.add("active");
    }

    function fnDisconnect() {
        sContainerId = null;
        dictScript = null;
        sScriptPath = null;
        iSelectedSceneIndex = -1;
        setExpandedScenes.clear();
        dictSceneStatus = {};
        if (wsPipeline) {
            wsPipeline.close();
            wsPipeline = null;
        }
        PipeleyenTerminal.fnCloseAll();
        document.getElementById("mainLayout").classList.remove("active");
        document.getElementById("containerPicker").style.display = "flex";
        fnLoadContainers();
    }

    /* --- Template Resolution --- */

    function fdictBuildClientVariables() {
        if (!dictScript) return {};
        var sScriptDir = fsGetScriptDirectory();
        return {
            sPlotDirectory: dictScript.sPlotDirectory || "Plot",
            sRepoRoot: sScriptDir,
            iNumberOfCores: dictScript.iNumberOfCores || -1,
            sFigureType: (dictScript.sFigureType || "pdf").toLowerCase(),
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
                document.getElementById("panelScenes").classList.toggle(
                    "active", sPanel === "scenes"
                );
                document.getElementById("panelFiles").classList.toggle(
                    "active", sPanel === "files"
                );
                if (sPanel === "files") {
                    PipeleyenFiles.fnLoadDirectory(fsGetScriptDirectory());
                }
            });
        });
    }

    function fsGetScriptDirectory() {
        if (!sScriptPath) return "/workspace";
        var iLastSlash = sScriptPath.lastIndexOf("/");
        return iLastSlash > 0 ? sScriptPath.substring(0, iLastSlash) : "/workspace";
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
        if (!dictScript) return;
        var el = document.getElementById("globalSettingsPanel");
        el.innerHTML =
            '<div class="gs-row">' +
            '<span class="gs-label">Plot Dir</span>' +
            '<input class="gs-input" id="gsPlotDirectory" value="' +
            fnEscapeHtml(dictScript.sPlotDirectory || "Plot") + '">' +
            '</div>' +
            '<div class="gs-row">' +
            '<span class="gs-label">Figure Type</span>' +
            '<input class="gs-input" id="gsFigureType" value="' +
            fnEscapeHtml(dictScript.sFigureType || "pdf") + '">' +
            '</div>' +
            '<div class="gs-row">' +
            '<span class="gs-label">Cores</span>' +
            '<input class="gs-input" id="gsNumberOfCores" type="number" value="' +
            (dictScript.iNumberOfCores || -1) + '">' +
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
                dictScript.sPlotDirectory = result.sPlotDirectory;
                dictScript.sFigureType = result.sFigureType;
                dictScript.iNumberOfCores = result.iNumberOfCores;
                fnShowToast("Settings saved", "success");
                fnRenderSceneList();
            }
        } catch (error) {
            fnShowToast("Failed to save settings", "error");
        }
    }

    /* --- Scene List --- */

    function fnRenderSceneList() {
        var elList = document.getElementById("listScenes");
        if (!dictScript || !dictScript.listScenes) {
            elList.innerHTML = "";
            return;
        }
        var dictVars = fdictBuildClientVariables();
        var sHtml = "";
        dictScript.listScenes.forEach(function (scene, iIndex) {
            sHtml += fsRenderSceneItem(scene, iIndex, dictVars);
        });
        elList.innerHTML = sHtml;
        fnBindSceneEvents();
    }

    function fsRenderSceneItem(scene, iIndex, dictVars) {
        var sStatusClass = dictSceneStatus[iIndex] || "";
        var bEnabled = scene.bEnabled !== false;
        var bSelected = iIndex === iSelectedSceneIndex;
        var bExpanded = setExpandedScenes.has(iIndex);

        var sHtml =
            '<div class="scene-item' + (bSelected ? " selected" : "") +
            '" data-index="' + iIndex + '" draggable="true">' +
            '<input type="checkbox" class="scene-checkbox"' +
            (bEnabled ? " checked" : "") + ">" +
            '<span class="scene-number">' +
            String(iIndex + 1).padStart(2, "0") + "</span>" +
            '<span class="scene-name" title="' +
            fnEscapeHtml(scene.sName) + '">' +
            fnEscapeHtml(scene.sName) + "</span>" +
            '<span class="scene-status ' + sStatusClass + '"></span>' +
            '<span class="scene-actions">' +
            '<button class="btn-icon scene-edit" title="Edit">&#9998;</button>' +
            "</span></div>";

        sHtml += '<div class="scene-detail' +
            (bExpanded ? " expanded" : "") +
            '" data-index="' + iIndex + '">';

        /* Directory */
        var sResolvedDir = fsResolveTemplate(scene.sDirectory, dictVars);
        sHtml += '<div class="detail-label">Directory</div>';
        sHtml += '<div class="detail-field" data-view="field">' +
            fnEscapeHtml(sResolvedDir) + "</div>";
        sHtml += '<div class="detail-label">Plot Only: ' +
            (scene.bPlotOnly !== false ? "Yes" : "No") + "</div>";

        /* Setup Commands */
        sHtml += fsRenderSectionLabel(
            "Setup Commands", iIndex, "saSetupCommands"
        );
        if (scene.saSetupCommands) {
            scene.saSetupCommands.forEach(function (sCmd, iCmdIdx) {
                sHtml += fsRenderDetailItem(
                    sCmd, dictVars, "command", "saSetupCommands",
                    iIndex, iCmdIdx
                );
            });
        }

        /* Commands */
        sHtml += fsRenderSectionLabel("Commands", iIndex, "saCommands");
        if (scene.saCommands) {
            scene.saCommands.forEach(function (sCmd, iCmdIdx) {
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
        if (scene.saOutputFiles) {
            scene.saOutputFiles.forEach(function (sFile, iFileIdx) {
                sHtml += fsRenderDetailItem(
                    sFile, dictVars, "output", "saOutputFiles",
                    iIndex, iFileIdx, sResolvedDir
                );
            });
        }

        sHtml += "</div>";
        return sHtml;
    }

    function fsRenderSectionLabel(sLabel, iSceneIdx, sArrayKey) {
        return '<div class="detail-label">' +
            '<span>' + sLabel + '</span>' +
            '<button class="section-add" data-scene="' + iSceneIdx +
            '" data-array="' + sArrayKey +
            '" title="Add item">+</button>' +
            '</div>';
    }

    function fsRenderDetailItem(
        sRaw, dictVars, sType, sArrayKey, iSceneIdx, iItemIdx,
        sWorkdir
    ) {
        var sResolved = fsResolveTemplate(sRaw, dictVars);
        var sFileClass = "";
        if (sType === "output") {
            sFileClass = " " + fsFileTypeClass(sResolved);
        }

        var sHtml = '<div class="detail-item ' + sType +
            '" data-scene="' + iSceneIdx +
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

    /* --- Scene Event Binding --- */

    function fnBindSceneEvents() {
        var elList = document.getElementById("listScenes");

        elList.querySelectorAll(".scene-item").forEach(function (el) {
            var iIndex = parseInt(el.dataset.index);

            el.addEventListener("click", function (event) {
                if (event.target.classList.contains("scene-checkbox") ||
                    event.target.classList.contains("scene-edit")) {
                    return;
                }
                fnToggleSceneExpand(iIndex);
            });

            el.addEventListener("contextmenu", function (event) {
                event.preventDefault();
                fnShowContextMenu(event.pageX, event.pageY, iIndex);
            });

            el.querySelector(".scene-checkbox").addEventListener(
                "change", function (event) {
                    fnToggleSceneEnabled(iIndex, event.target.checked);
                }
            );

            var btnEdit = el.querySelector(".scene-edit");
            if (btnEdit) {
                btnEdit.addEventListener("click", function () {
                    PipeleyenSceneEditor.fnOpenEditModal(iIndex);
                });
            }

            /* Scene header drag: reorder scenes */
            el.addEventListener("dragstart", function (event) {
                event.dataTransfer.setData("text/plain", String(iIndex));
                event.dataTransfer.setData("pipeleyen/scene", String(iIndex));
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
                var sSceneData = event.dataTransfer.getData("text/plain");
                if (sSceneData !== "") {
                    var iFromIndex = parseInt(sSceneData);
                    if (iFromIndex !== iIndex) {
                        fnReorderScene(iFromIndex, iIndex);
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
                    parseInt(el.dataset.scene), el.dataset.array
                );
            });
        });

        /* Bind drop targets on detail sections */
        elList.querySelectorAll(".scene-detail").forEach(function (el) {
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
                    var iTargetScene = parseInt(el.dataset.index);
                    fnHandleDetailDrop(sDetailData, iTargetScene);
                }
            });
        });
    }

    function fnBindDetailItemEvents(el) {
        var iScene = parseInt(el.dataset.scene);
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

        /* Drag detail items — carry source info for cross-scene drops */
        el.addEventListener("dragstart", function (event) {
            event.stopPropagation();
            var dictDragData = {
                iScene: iScene,
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
                fnInlineEditItem(el, iScene, sArray, iIdx);
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
                fnDeleteDetailItem(iScene, sArray, iIdx);
            });
        }
    }

    /* --- Detail Item Actions --- */

    function fnInlineEditItem(el, iScene, sArray, iIdx) {
        var sRaw = dictScript.listScenes[iScene][sArray][iIdx];
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
                dictScript.listScenes[iScene][sArray][iIdx] = sNewValue;
                fnSaveSceneArray(iScene, sArray);
            }
            elInput.remove();
            elText.style.display = "";
            elActions.style.display = "";
            fnRenderSceneList();
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

    async function fnDeleteDetailItem(iScene, sArray, iIdx) {
        var sValue = dictScript.listScenes[iScene][sArray][iIdx];
        if (!confirm("Delete this item?\n\n" + sValue)) return;
        dictScript.listScenes[iScene][sArray].splice(iIdx, 1);
        fnPushUndo({
            sAction: "delete",
            iScene: iScene,
            sArray: sArray,
            iIdx: iIdx,
            sValue: sValue,
        });
        await fnSaveSceneArray(iScene, sArray);
        fnRenderSceneList();
    }

    async function fnHandleDetailDrop(sDetailData, iTargetScene) {
        var dictDrag = JSON.parse(sDetailData);
        var iSource = dictDrag.iScene;
        var sArray = dictDrag.sArray;
        var iIdx = dictDrag.iIdx;
        if (iSource === iTargetScene) return;

        if (!confirm(
            "WARNING: Moving a command may break dependencies " +
            "in later scenes.\n\nProceed?"
        )) {
            return;
        }

        var sValue = dictScript.listScenes[iSource][sArray].splice(
            iIdx, 1
        )[0];
        var sTargetArray = sArray;
        if (!dictScript.listScenes[iTargetScene][sTargetArray]) {
            dictScript.listScenes[iTargetScene][sTargetArray] = [];
        }
        dictScript.listScenes[iTargetScene][sTargetArray].unshift(sValue);
        fnPushUndo({
            sAction: "move",
            iScene: iSource,
            sArray: sArray,
            iIdx: iIdx,
            iTargetScene: iTargetScene,
            iTargetIdx: 0,
            sValue: sValue,
        });
        await fnSaveSceneArray(iSource, sArray);
        await fnSaveSceneArray(iTargetScene, sTargetArray);

        /* Expand target and highlight */
        setExpandedScenes.add(iTargetScene);
        fnRenderSceneList();
        fnHighlightItem(iTargetScene, sTargetArray, 0);
        fnShowToast(
            "Moved to " + dictScript.listScenes[iTargetScene].sName,
            "success"
        );

        alert(
            "Modifying pipeline. Ensure that all subsequent " +
            "scenes properly reference the new pipeline."
        );
    }

    function fnHighlightItem(iScene, sArray, iIdx) {
        var elItem = document.querySelector(
            '.detail-item[data-scene="' + iScene +
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

    function fnAddNewItem(iScene, sArrayKey) {
        var sPlaceholder = sArrayKey === "saOutputFiles" ?
            "File path..." : "Command...";
        fnShowInlineInput(iScene, sArrayKey, sPlaceholder);
    }

    function fnShowInlineInput(iScene, sArrayKey, sPlaceholder) {
        var elSection = document.querySelector(
            '.section-add[data-scene="' + iScene +
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
                fnCommitNewItem(iScene, sArrayKey, sValue);
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

    async function fnCommitNewItem(iScene, sArrayKey, sValue) {
        if (!dictScript.listScenes[iScene][sArrayKey]) {
            dictScript.listScenes[iScene][sArrayKey] = [];
        }
        dictScript.listScenes[iScene][sArrayKey].push(sValue);
        fnPushUndo({
            sAction: "add",
            iScene: iScene,
            sArray: sArrayKey,
            iIdx: dictScript.listScenes[iScene][sArrayKey].length - 1,
            sValue: sValue,
        });
        await fnSaveSceneArray(iScene, sArrayKey);
        fnRenderSceneList();
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
            dictScript.listScenes[dictAction.iScene][dictAction.sArray]
                .splice(dictAction.iIdx, 1);
            await fnSaveSceneArray(dictAction.iScene, dictAction.sArray);
        } else if (dictAction.sAction === "delete") {
            dictScript.listScenes[dictAction.iScene][dictAction.sArray]
                .splice(dictAction.iIdx, 0, dictAction.sValue);
            await fnSaveSceneArray(dictAction.iScene, dictAction.sArray);
        } else if (dictAction.sAction === "move") {
            var sValue = dictScript.listScenes[dictAction.iTargetScene][
                dictAction.sArray
            ].splice(dictAction.iTargetIdx, 1)[0];
            if (!dictScript.listScenes[dictAction.iScene][dictAction.sArray]) {
                dictScript.listScenes[dictAction.iScene][dictAction.sArray] = [];
            }
            dictScript.listScenes[dictAction.iScene][dictAction.sArray]
                .splice(dictAction.iIdx, 0, sValue);
            await fnSaveSceneArray(dictAction.iScene, dictAction.sArray);
            await fnSaveSceneArray(
                dictAction.iTargetScene, dictAction.sArray
            );
        }
        fnRenderSceneList();
        fnShowToast("Undone", "success");
    }

    async function fnSaveSceneArray(iScene, sArray) {
        var dictUpdate = {};
        dictUpdate[sArray] = dictScript.listScenes[iScene][sArray];
        try {
            await fetch(
                "/api/scenes/" + sContainerId + "/" + iScene,
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

    /* --- Scene Expand/Collapse --- */

    function fnToggleSceneExpand(iIndex) {
        if (setExpandedScenes.has(iIndex)) {
            setExpandedScenes.delete(iIndex);
        } else {
            setExpandedScenes.add(iIndex);
        }
        iSelectedSceneIndex = iIndex;
        fnRenderSceneList();
    }

    async function fnToggleSceneEnabled(iIndex, bEnabled) {
        try {
            await fetch(
                "/api/scenes/" + sContainerId + "/" + iIndex,
                {
                    method: "PUT",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ bEnabled: bEnabled }),
                }
            );
            dictScript.listScenes[iIndex].bEnabled = bEnabled;
        } catch (error) {
            fnShowToast("Failed to update scene", "error");
        }
    }

    async function fnReorderScene(iFromIndex, iToIndex) {
        try {
            var response = await fetch(
                "/api/scenes/" + sContainerId + "/reorder",
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
                dictScript.listScenes = result.listScenes;
                fnRenderSceneList();
                fnShowToast(
                    "Scene reordered (references renumbered)",
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
        if (dictEvent.sType === "scenePass") {
            dictSceneStatus[dictEvent.iSceneNumber - 1] = "pass";
            fnRenderSceneList();
        } else if (dictEvent.sType === "sceneFail") {
            dictSceneStatus[dictEvent.iSceneNumber - 1] = "fail";
            fnRenderSceneList();
        } else if (dictEvent.sType === "started") {
            fnShowToast("Pipeline started", "success");
        } else if (dictEvent.sType === "completed") {
            fnShowToast("Pipeline completed", "success");
        } else if (dictEvent.sType === "failed") {
            fnShowToast(
                "Pipeline failed (exit " + dictEvent.iExitCode + ")", "error"
            );
        }
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
        document.querySelectorAll(".scene-checkbox:checked")
            .forEach(function (el) {
                var iIndex = parseInt(
                    el.closest(".scene-item").dataset.index
                );
                listIndices.push(iIndex);
                dictSceneStatus[iIndex] = "running";
            });
        fnRenderSceneList();
        fnSendPipelineAction({
            sAction: "runSelected",
            listSceneIndices: listIndices,
        });
    }

    function fnRunAll() {
        dictScript.listScenes.forEach(function (_, iIndex) {
            dictSceneStatus[iIndex] = "running";
        });
        fnRenderSceneList();
        fnSendPipelineAction({ sAction: "runAll" });
    }

    function fnVerify() {
        fnSendPipelineAction({ sAction: "verify" });
    }

    async function fnValidateReferences() {
        if (!sContainerId) return;
        try {
            var response = await fetch(
                "/api/scenes/" + sContainerId + "/validate"
            );
            var result = await response.json();
            var listWarnings = result.listWarnings;
            if (listWarnings.length === 0) {
                fnShowToast(
                    "All cross-scene references are valid",
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

    var iContextSceneIndex = -1;

    function fnShowContextMenu(iX, iY, iIndex) {
        iContextSceneIndex = iIndex;
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
                    fnHandleContextAction(el.dataset.action, iContextSceneIndex);
                    fnHideContextMenu();
                });
            });
    }

    function fnHandleContextAction(sAction, iIndex) {
        if (sAction === "edit") {
            PipeleyenSceneEditor.fnOpenEditModal(iIndex);
        } else if (sAction === "runFrom") {
            fnSendPipelineAction({
                sAction: "runFrom",
                iStartScene: iIndex + 1,
            });
        } else if (sAction === "insertBefore") {
            PipeleyenSceneEditor.fnOpenInsertModal(iIndex);
        } else if (sAction === "insertAfter") {
            PipeleyenSceneEditor.fnOpenInsertModal(iIndex + 1);
        } else if (sAction === "delete") {
            fnDeleteScene(iIndex);
        }
    }

    async function fnDeleteScene(iIndex) {
        var sName = dictScript.listScenes[iIndex].sName;
        if (!confirm('Delete scene "' + sName + '"?')) return;
        try {
            var response = await fetch(
                "/api/scenes/" + sContainerId + "/" + iIndex,
                { method: "DELETE" }
            );
            if (response.ok) {
                var result = await response.json();
                dictScript.listScenes = result.listScenes;
                if (iSelectedSceneIndex === iIndex) iSelectedSceneIndex = -1;
                setExpandedScenes.delete(iIndex);
                fnRenderSceneList();
                fnShowToast(
                    "Scene deleted (references renumbered)",
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
        fnRenderSceneList: fnRenderSceneList,
        fnEscapeHtml: fnEscapeHtml,
        fsGetContainerId: function () { return sContainerId; },
        fdictGetScript: function () { return dictScript; },
        fsGetScriptPath: function () { return sScriptPath; },
        fiGetSelectedSceneIndex: function () { return iSelectedSceneIndex; },
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
