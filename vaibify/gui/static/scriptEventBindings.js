/* Vaibify — DOM event binding and delegated click handlers */

var PipeleyenEventBindings = (function () {
    "use strict";

    /* --- Delegated Click Handlers --- */

    function _fnHandleActionDownload(event, elMatch) {
        event.stopPropagation();
        var elDetailItem = event.target.closest(".detail-item");
        if (elDetailItem) {
            PipeleyenFilePull.fnPromptPullToHost(
                elDetailItem.dataset.resolved);
        }
    }

    function _fnHandleActionEdit(event, elMatch) {
        event.stopPropagation();
        var elDetailItem = event.target.closest(".detail-item");
        if (elDetailItem) {
            PipeleyenFileOps.fnInlineEditItem(
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
            PipeleyenFileOps.fnCopyToClipboard(
                elDetailItem.dataset.resolved);
        }
    }

    function _fnHandleActionDelete(event, elMatch) {
        event.stopPropagation();
        var elDetailItem = event.target.closest(".detail-item");
        if (elDetailItem) {
            PipeleyenApp.fnDeleteDetailItem(
                parseInt(elDetailItem.dataset.step),
                elDetailItem.dataset.array,
                parseInt(elDetailItem.dataset.idx)
            );
        }
    }

    function _fnHandleDiscoveredButton(event, elMatch) {
        event.stopPropagation();
        var elDiscItem = elMatch.closest(".discovered-item");
        PipeleyenApp.fnAddDiscoveredOutput(
            parseInt(elDiscItem.dataset.step),
            elDiscItem.dataset.file,
            elMatch.dataset.target
        );
    }

    function _fnHandleArchiveStar(event, elMatch) {
        event.stopPropagation();
        PipeleyenApp.fnToggleArchiveCategory(
            parseInt(elMatch.dataset.step),
            elMatch.dataset.file,
            elMatch.dataset.array || "saPlotFiles"
        );
    }

    function _fnHandleTestAdd(event, elMatch) {
        event.stopPropagation();
        PipeleyenTestManager.fnAddTestItem(
            parseInt(elMatch.dataset.step),
            elMatch.dataset.testType
        );
    }

    function _fnHandleSectionAdd(event, elMatch) {
        event.stopPropagation();
        PipeleyenApp.fnAddNewItem(
            parseInt(elMatch.dataset.step),
            elMatch.dataset.array
        );
    }

    function _fnHandleVerificationClickable(event, elMatch) {
        PipeleyenApp.fnCycleUserVerification(
            parseInt(elMatch.dataset.step)
        );
    }

    function _fnHandleSubTestRow(event, elMatch) {
        var sSubApprover = elMatch.dataset.approver;
        var iSubStep = parseInt(elMatch.dataset.step);
        var setSubExp = PipeleyenApp.fsetGetExpandedCategory(
            sSubApprover);
        if (setSubExp.has(iSubStep)) {
            setSubExp.delete(iSubStep);
        } else {
            setSubExp.add(iSubStep);
        }
        PipeleyenApp.fnRenderStepList();
    }

    function _fnHandleVerificationExpandable(event, elMatch) {
        var sApprover = elMatch.dataset.approver;
        var iStep = parseInt(elMatch.dataset.step);
        if (sApprover === "unitTest") {
            PipeleyenApp.fnToggleUnitTestExpand(iStep);
        } else if (sApprover === "deps") {
            PipeleyenApp.fnToggleDepsExpand(iStep);
        }
    }

    function _fnHandleVerificationDeps(event, elMatch) {
        PipeleyenApp.fnToggleDepsExpand(
            parseInt(elMatch.dataset.step));
    }

    function _fnHandleMakeStandard(event, elMatch) {
        PipeleyenPlotStandards.fnStandardizeAllPlots(
            parseInt(elMatch.dataset.step));
    }

    function _fnHandleCompareStandard(event, elMatch) {
        PipeleyenPlotStandards.fnCompareStepPlots(
            parseInt(elMatch.dataset.step));
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
        PipeleyenTestManager.fnGenerateTests(
            parseInt(elMatch.dataset.step));
    }

    function _fnHandleStepEdit(event, elMatch) {
        var elStepItem = event.target.closest(".step-item");
        PipeleyenStepEditor.fnOpenEditModal(
            parseInt(elStepItem.dataset.index)
        );
    }

    function _fnHandleInteractiveRun(event, elMatch) {
        PipeleyenPipelineRunner.fnRunInteractiveStep(
            parseInt(elMatch.dataset.index));
    }

    function _fnHandleInteractivePlots(event, elMatch) {
        PipeleyenPipelineRunner.fnRunInteractivePlots(
            parseInt(elMatch.dataset.index));
    }

    function _fnHandleRunTests(event, elMatch) {
        PipeleyenTestManager.fnRunStepTests(
            parseInt(elMatch.dataset.step));
    }

    function _fnHandleRunCategory(event, elMatch) {
        PipeleyenTestManager.fnRunCategoryTests(
            parseInt(elMatch.dataset.step),
            elMatch.dataset.category);
    }

    function _fnHandleRunData(event, elMatch) {
        PipeleyenPipelineRunner.fnRunInteractiveStep(
            parseInt(elMatch.dataset.step));
    }

    function _fnHandleRunPlots(event, elMatch) {
        PipeleyenPipelineRunner.fnRunInteractivePlots(
            parseInt(elMatch.dataset.step));
    }

    function _fnHandleAddDeps(event, elMatch) {
        PipeleyenDependencyScanner.fnScanDependencies(
            parseInt(elMatch.dataset.step));
    }

    function _fnHandleShowDeps(event, elMatch) {
        PipeleyenApp.fnShowDag();
    }

    function _fnHandleRunStep(event, elMatch) {
        PipeleyenPipelineRunner.fnRunStepCombined(
            parseInt(elMatch.dataset.step));
    }

    function _fnHandleTestCategoryFile(event, elMatch) {
        PipeleyenTestManager.fnViewCategoryTestFile(
            parseInt(elMatch.dataset.step),
            elMatch.dataset.category);
    }

    function _fnHandleTestStandardsLink(event, elMatch) {
        PipeleyenTestManager.fnViewStandardsFile(
            parseInt(elMatch.dataset.step),
            elMatch.dataset.category);
    }

    function _fnHandleTestLogLink(event, elMatch) {
        var iLogStep = parseInt(elMatch.dataset.step, 10);
        var sCatKey = elMatch.dataset.category;
        var dictWorkflow = PipeleyenApp.fdictGetWorkflow();
        var dictLogStep = dictWorkflow.listSteps[iLogStep];
        var dictLogTests = PipeleyenApp.fdictGetTests(dictLogStep);
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
        PipeleyenTestManager.fnEditTestFile(
            parseInt(elMatch.dataset.step),
            parseInt(elMatch.dataset.idx));
    }

    function _fnHandleTestDeleteCmd(event, elMatch) {
        PipeleyenTestManager.fnDeleteTestCommand(
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
        ".verification-row.clickable":
            _fnHandleVerificationClickable,
        ".sub-test-row": _fnHandleSubTestRow,
        ".verification-row.expandable":
            _fnHandleVerificationExpandable,
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

    /* --- Delegated Event Dispatch --- */

    function fnHandleDelegatedClick(event) {
        var elTarget = event.target;

        for (var sSelector in _DICT_CLICK_HANDLERS) {
            var elMatch = elTarget.closest(sSelector);
            if (elMatch) {
                _DICT_CLICK_HANDLERS[sSelector](event, elMatch);
                return;
            }
        }

        var elDetailItem = elTarget.closest(".detail-item");
        if (elTarget.closest(".detail-text") && elDetailItem &&
            elDetailItem.classList.contains("output")) {
            var elText = elTarget.closest(".detail-text");
            if (elText.classList.contains("file-binary")) {
                PipeleyenApp.fnShowBinaryNotViewable();
            } else if (PipeleyenApp.fbIsFileMissing(elText)) {
                PipeleyenApp.fnShowOutputNotAvailable();
            } else {
                PipeleyenFigureViewer.fnDisplayInNextViewer(
                    elDetailItem.dataset.resolved,
                    elDetailItem.dataset.workdir || ""
                );
            }
            return;
        }

        var elStepItem = elTarget.closest(".step-item");
        if (elStepItem &&
            !elTarget.classList.contains("step-checkbox")) {
            PipeleyenApp.fnToggleStepExpand(
                parseInt(elStepItem.dataset.index));
        }
    }

    function fnHandleDelegatedChange(event) {
        var elTarget = event.target;
        if (elTarget.classList.contains("step-checkbox")) {
            var elStep = elTarget.closest(".step-item");
            PipeleyenApp.fnToggleStepEnabled(
                parseInt(elStep.dataset.index), elTarget.checked
            );
        }
        if (elTarget.classList.contains("plot-only-checkbox")) {
            PipeleyenApp.fnTogglePlotOnly(
                parseInt(elTarget.dataset.step), elTarget.checked
            );
        }
    }

    function fnHandleDelegatedContextMenu(event) {
        var elFile = event.target.closest(".detail-item.output");
        if (elFile) {
            event.preventDefault();
            event.stopPropagation();
            PipeleyenApp.fnShowFileContextMenu(
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
            PipeleyenApp.fnShowContextMenu(
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
                "vaibify/detail",
                JSON.stringify(dictDragData)
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
            event.dataTransfer.setData(
                "text/plain", String(iIdx));
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
            PipeleyenApp.fnHandleDetailDrop(sDetailData, iTarget);
            return;
        }
        if (elStep) {
            event.preventDefault();
            var sStepData = event.dataTransfer.getData(
                "text/plain");
            if (sStepData !== "") {
                var iFrom = parseInt(sStepData);
                var iTo = parseInt(elStep.dataset.index);
                if (iFrom !== iTo) {
                    PipeleyenApp.fnReorderStep(iFrom, iTo);
                }
            }
        }
    }

    /* --- Step Delegated Event Setup --- */

    function fnSetupDelegatedEvents(elList) {
        elList.addEventListener("click",
            fnHandleDelegatedClick);
        elList.addEventListener("change",
            fnHandleDelegatedChange);
        elList.addEventListener("contextmenu",
            fnHandleDelegatedContextMenu);
        elList.addEventListener("dragstart",
            fnHandleDelegatedDragStart);
        elList.addEventListener("dragend",
            fnHandleDelegatedDragEnd);
        elList.addEventListener("dragover",
            fnHandleDelegatedDragOver);
        elList.addEventListener("dragleave",
            fnHandleDelegatedDragLeave);
        elList.addEventListener("drop",
            fnHandleDelegatedDrop);
    }

    /* --- Toolbar Events --- */

    function fnBindToolbarEvents() {
        _fnBindToolbarMenus();
        _fnBindMenuItemActions();
        VaibifySyncManager.fnBindPushModalEvents();
        var elLogo = document.querySelector(".toolbar-logo");
        if (elLogo) {
            elLogo.style.cursor = "pointer";
            elLogo.addEventListener("click", function () {
                PipeleyenApp.fnDisconnect();
            });
        }
    }

    function _fnBindToolbarMenus() {
        _fnBindMenuItemCloseOnClick();
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

    function _fnBindMenuItemCloseOnClick() {
        document.querySelectorAll(".toolbar-menu-item")
            .forEach(function (el) {
                el.addEventListener("click",
                    fnCloseAllToolbarMenus);
            });
    }

    function _fnBindMenuItemActions() {
        var dictActions = {
            btnRunSelected: function () {
                PipeleyenPipelineRunner.fnRunSelected();
            },
            btnRunAll: function () {
                PipeleyenPipelineRunner.fnRunAll();
            },
            btnForceRunAll: function () {
                PipeleyenPipelineRunner.fnForceRunAll();
            },
            btnKillPipeline: function () {
                PipeleyenPipelineRunner.fnKillPipeline();
            },
            btnVerify: function () {
                PipeleyenPipelineRunner.fnVerify();
            },
            btnRunAllTests: function () {
                PipeleyenPipelineRunner.fnRunAllTests();
            },
            btnVerifyDependencies: function () {
                PipeleyenPipelineRunner.fnVerifyDependencies();
            },
            btnStandardizeAllPlots: function () {
                PipeleyenPlotStandards
                    .fnStandardizeAllWorkflowPlots();
            },
            btnOverleafPush: function () {
                VaibifySyncManager.fnOpenPushModal("overleaf");
            },
            btnGithubPush: function () {
                VaibifySyncManager.fnOpenPushModal("github");
            },
            btnZenodoArchive: function () {
                VaibifySyncManager.fnOpenPushModal("zenodo");
            },
            btnShowDag: function () {
                PipeleyenApp.fnShowDag();
            },
            btnVsCode: function () {
                PipeleyenApp.fnOpenVsCode();
            },
            btnMonitor: function () {},
            btnResetLayout: function () {
                PipeleyenApp.fnResetLayout();
            },
            btnAdminContainers: function () {
                PipeleyenModals.fnShowConfirmModal(
                    "Leave Dashboard",
                    "This will disconnect from the container " +
                    "and end any running sessions. Continue?",
                    PipeleyenApp.fnDisconnect);
            },
            btnAdminWorkflows: function () {
                PipeleyenModals.fnShowConfirmModal(
                    "Switch Workflow",
                    "This will leave the current dashboard " +
                    "and end any running sessions. Continue?",
                    PipeleyenApp.fnReconnectToCurrentContainer);
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

    /* --- Workflow Picker Events --- */

    function fnBindWorkflowPickerEvents() {
        document.getElementById("btnWorkflowBack").addEventListener(
            "click", function () {
                PipeleyenApp.fnShowContainerLanding();
                PipeleyenContainerManager.fnLoadContainers();
            }
        );
        document.getElementById("btnNoWorkflow").addEventListener(
            "click", function () {
                var sId = PipeleyenContainerManager
                    .fsGetSelectedContainerId();
                if (sId) PipeleyenApp.fnEnterNoWorkflow(sId);
            }
        );
        document.getElementById("btnNewWorkflow").addEventListener(
            "click", function () {
                PipeleyenContainerManager.fnCreateNewWorkflow();
            }
        );
        document.getElementById("btnRefreshWorkflows")
            .addEventListener("click", function () {
                var sId = PipeleyenContainerManager
                    .fsGetSelectedContainerId();
                if (sId) {
                    PipeleyenContainerManager
                        .fnConnectToContainer(sId);
                }
            }
        );
        document.getElementById("activeWorkflowName")
            .addEventListener("click", function (event) {
                event.stopPropagation();
                VaibifyWorkflowManager.fnToggleWorkflowDropdown();
            }
        );
        document.addEventListener("click", function () {
            VaibifyWorkflowManager.fnHideWorkflowDropdown();
        });
    }

    /* --- Context Menu Events --- */

    function fnBindContextMenuEvents() {
        document.getElementById("contextMenu")
            .querySelectorAll(".context-menu-item")
            .forEach(function (el) {
                el.addEventListener("click", function (event) {
                    event.stopPropagation();
                    PipeleyenApp.fnHandleContextAction(
                        el.dataset.action,
                        PipeleyenApp.fiGetContextStepIndex());
                    PipeleyenApp.fnHideContextMenu();
                });
            });
        document.getElementById("fileContextMenu")
            .querySelectorAll(".context-menu-item")
            .forEach(function (el) {
                el.addEventListener("click", function (event) {
                    event.stopPropagation();
                    PipeleyenApp.fnHandleFileContextAction(
                        el.dataset.action);
                    PipeleyenApp.fnHideContextMenu();
                });
            });
    }

    /* --- Left Panel Tabs --- */

    function fnBindLeftPanelTabs() {
        document.querySelectorAll(".left-tab").forEach(
            function (el) {
                el.addEventListener("click", function () {
                    document.querySelectorAll(".left-tab").forEach(
                        function (t) {
                            t.classList.remove("active");
                        });
                    el.classList.add("active");
                    var sPanel = el.dataset.panel;
                    var bWorkflowMode = PipeleyenApp
                        .fbIsWorkflowMode();
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
                    var elPanelRepos = document.getElementById(
                        "panelRepos");
                    if (elPanelRepos) {
                        elPanelRepos.classList.toggle(
                            "active", sPanel === "repos");
                    }
                    if (sPanel === "files") {
                        PipeleyenFiles.fnLoadDirectory(
                            "/workspace");
                    } else if (sPanel === "logs") {
                        PipeleyenApp.fnLoadLogs();
                    } else if (sPanel === "repos") {
                        PipeleyenReposPanel.fnRender();
                    }
                });
            });
    }

    /* --- Resize Handles --- */

    function fnBindResizeHandles() {
        var elLeft = document.getElementById("panelLeft");
        var elHandleH = elLeft.querySelector(
            ".resize-handle-horizontal");
        if (elHandleH) {
            _fnMakeDraggable(elHandleH, function (iDeltaX) {
                var iWidth = elLeft.offsetWidth + iDeltaX;
                iWidth = Math.max(180, Math.min(iWidth, 600));
                document.getElementById("mainLayout")
                    .style.gridTemplateColumns =
                    iWidth + "px 1fr";
            });
        }

        var elHandleV = document.getElementById(
            "resizeHandleVertical");
        if (elHandleV) {
            var elViewerDual = document.getElementById(
                "panelViewerDual");
            var elRight = document.getElementById("panelRight");
            _fnMakeDraggableVertical(
                elHandleV, function (iDeltaY) {
                    var iHeight = elViewerDual.offsetHeight +
                        iDeltaY;
                    var iMaxHeight = elRight.offsetHeight - 120;
                    iHeight = Math.max(80,
                        Math.min(iHeight, iMaxHeight));
                    elViewerDual.style.flex =
                        "0 0 " + iHeight + "px";
                });
        }

        var elHandleViewer = document.getElementById(
            "resizeHandleViewer");
        if (elHandleViewer) {
            var elViewerA = document.getElementById("viewerA");
            var elDual = document.getElementById(
                "panelViewerDual");
            _fnMakeDraggable(
                elHandleViewer, function (iDeltaX) {
                    var iWidth = elViewerA.offsetWidth + iDeltaX;
                    var iMaxWidth = elDual.offsetWidth - 120;
                    iWidth = Math.max(100,
                        Math.min(iWidth, iMaxWidth));
                    elViewerA.style.flex = "0 0 " + iWidth + "px";
                });
        }
    }

    function _fnMakeDraggable(elHandle, fnOnMove) {
        elHandle.addEventListener("mousedown", function (event) {
            var iStartX = event.clientX;
            event.preventDefault();
            function fnMouseMove(e) {
                var iDelta = e.clientX - iStartX;
                iStartX = e.clientX;
                fnOnMove(iDelta);
            }
            function fnMouseUp() {
                document.removeEventListener(
                    "mousemove", fnMouseMove);
                document.removeEventListener(
                    "mouseup", fnMouseUp);
            }
            document.addEventListener("mousemove", fnMouseMove);
            document.addEventListener("mouseup", fnMouseUp);
        });
    }

    function _fnMakeDraggableVertical(elHandle, fnOnMove) {
        elHandle.addEventListener("mousedown", function (event) {
            var iStartY = event.clientY;
            event.preventDefault();
            function fnMouseMove(e) {
                var iDelta = e.clientY - iStartY;
                iStartY = e.clientY;
                fnOnMove(iDelta);
            }
            function fnMouseUp() {
                document.removeEventListener(
                    "mousemove", fnMouseMove);
                document.removeEventListener(
                    "mouseup", fnMouseUp);
                PipeleyenTerminal.fnFitActiveTerminal();
            }
            document.addEventListener("mousemove", fnMouseMove);
            document.addEventListener("mouseup", fnMouseUp);
        });
    }

    /* --- Global Settings Toggle --- */

    function fnBindGlobalSettingsToggle() {
        document.getElementById("btnGlobalSettings").addEventListener(
            "click", function () {
                var el = document.getElementById(
                    "globalSettingsPanel");
                var bExpanded = el.classList.toggle("expanded");
                if (bExpanded) {
                    PipeleyenApp.fnRenderGlobalSettings();
                }
            }
        );
    }

    /* --- Refresh Workflow --- */

    function fnBindRefreshWorkflow() {
        document.getElementById("btnRefreshWorkflow")
            .addEventListener("click", function () {
                VaibifyWorkflowManager.fnRefreshWorkflow();
            });
    }

    /* --- Error Modal --- */

    function fnBindErrorModal() {
        document.getElementById("btnModalErrorClose")
            .addEventListener("click", function () {
                document.getElementById("modalError")
                    .style.display = "none";
            }
        );
    }

    return {
        fnSetupDelegatedEvents: fnSetupDelegatedEvents,
        fnBindToolbarEvents: fnBindToolbarEvents,
        fnBindWorkflowPickerEvents: fnBindWorkflowPickerEvents,
        fnBindContextMenuEvents: fnBindContextMenuEvents,
        fnBindLeftPanelTabs: fnBindLeftPanelTabs,
        fnBindResizeHandles: fnBindResizeHandles,
        fnBindGlobalSettingsToggle: fnBindGlobalSettingsToggle,
        fnBindRefreshWorkflow: fnBindRefreshWorkflow,
        fnBindErrorModal: fnBindErrorModal,
    };
})();
