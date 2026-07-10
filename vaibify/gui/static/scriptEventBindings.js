/* Vaibify — DOM event binding and delegated click handlers */

var PipeleyenEventBindings = (function () {
    "use strict";

    /* --- Delegated Click Handlers --- */

    function _fnHandleDiscoveredButton(event, elMatch) {
        event.stopPropagation();
        var elDiscItem = elMatch.closest(".discovered-item");
        PipeleyenApp.fnAddDiscoveredOutput(
            parseInt(elDiscItem.dataset.step),
            elDiscItem.dataset.file,
            elMatch.dataset.target
        );
    }

    function _fnHandleRemoteBadge(event, elMatch) {
        event.stopPropagation();
        var sRemoteKey = elMatch.dataset.remote || "";
        var elItem = elMatch.closest(".detail-item");
        if (!elItem) return;
        var sResolved = elItem.dataset.resolved || "";
        var sWorkdir = elItem.dataset.workdir || "";
        if (!sResolved || !sRemoteKey) return;
        VaibifySyncManager.fnOpenRemotePicklistForBadge(
            elMatch, sRemoteKey, sResolved, sWorkdir,
        );
    }

    function _fnHandleRowOverflow(event, elMatch) {
        event.stopPropagation();
        var elItem = elMatch.closest(".detail-item");
        if (!elItem) return;
        VaibifySyncManager.fnOpenRowOverflowMenu(elMatch, elItem);
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

    function _fnHandleAiDeclarationOpen(event, elMatch) {
        event.stopPropagation();
        var sFilePath = elMatch.dataset.file || "";
        if (!sFilePath) return;
        var sRepoRoot = PipeleyenApp.fdictBuildClientVariables()
            .sRepoRoot || "";
        PipeleyenFigureViewer.fnDisplayFileInViewer(
            "A", sFilePath, sRepoRoot);
    }

    function _fnHandleAddAiDeclarationStep(event, elMatch) {
        event.stopPropagation();
        PipeleyenApp.fnAddAiDeclarationStep();
    }

    var _DICT_DECLARATION_COMMIT_TOASTS = {
        "clean": ["Declaration file is already committed — push to " +
            "GitHub to publish it.", "info"],
        "committed": ["Declaration file committed — push to GitHub " +
            "to publish it.", "success"],
        "failed": ["Could not commit the declaration file — check " +
            "the Repos panel.", "error"],
    };

    async function _fnHandleAiDeclarationCommit(event, elMatch) {
        // Scoped to the declaration file only: the dialog shows and
        // commits just that file. Committing is not publishing —
        // pushing happens from the Repos panel or the sync buttons.
        var sContainerId = PipeleyenApp.fsGetContainerId();
        var sFilePath = elMatch.dataset.file || "";
        if (!sContainerId || !sFilePath) return;
        var sOutcome = await VaibifyManifestCheck.fsCommitSinglePath(
            sContainerId, sFilePath);
        var listToast = _DICT_DECLARATION_COMMIT_TOASTS[sOutcome];
        if (listToast) {
            PipeleyenApp.fnShowToast(listToast[0], listToast[1]);
        }
        _fnRefreshDeclarationGitState(sContainerId, sOutcome,
            ["committed", "clean", "failed"]);
    }

    var _DICT_DECLARATION_REMOVE_TOASTS = {
        "removed": ["Declaration file removed from the repo — the " +
            "file stays on disk.", "success"],
        "failed": ["Could not remove the declaration file — check " +
            "the Repos panel.", "error"],
    };

    async function _fnHandleAiDeclarationUntrack(event, elMatch) {
        // Inverse of the commit action: confirms, then commits the
        // removal of the declaration file from git tracking. The
        // file itself stays on disk.
        var sContainerId = PipeleyenApp.fsGetContainerId();
        var sFilePath = elMatch.dataset.file || "";
        if (!sContainerId || !sFilePath) return;
        var sOutcome = await VaibifyManifestCheck.fsRemoveSinglePath(
            sContainerId, sFilePath);
        var listToast = _DICT_DECLARATION_REMOVE_TOASTS[sOutcome];
        if (listToast) {
            PipeleyenApp.fnShowToast(listToast[0], listToast[1]);
        }
        _fnRefreshDeclarationGitState(sContainerId, sOutcome,
            ["removed", "failed"]);
    }

    function _fnRefreshDeclarationGitState(
        sContainerId, sOutcome, listRefreshOutcomes
    ) {
        // A fresh badge pull flips the Commit/Remove button on the
        // next step-list repaint without waiting for the poll tick.
        // "failed" refreshes too: a partial failure (e.g. rm done,
        // commit refused) changes git state, and a stale button is
        // worse after an error than after a success.
        if (listRefreshOutcomes.indexOf(sOutcome) === -1) return;
        if (typeof VaibifyGitBadges === "undefined") return;
        VaibifyGitBadges.fnRefresh(sContainerId);
    }

    function _fnHandleOpenReposPanel(event, elMatch) {
        event.preventDefault();
        var elTab = document.querySelector(
            '.left-tab[data-panel="repos"]');
        if (elTab) elTab.click();
    }

    function _fnHandleStepsBlockToggle(event, elMatch) {
        PipeleyenApp.fnToggleStepsBlockExpand();
    }

    function _fnHandleWorkflowWideToggle(event, elMatch) {
        PipeleyenApp.fnToggleWorkflowWideBlockExpand();
    }

    function _fnHandleRequirementGroupToggle(event, elMatch) {
        PipeleyenApp.fnToggleRequirementGroup(elMatch.dataset.group);
    }

    function _fnHandleRequirementRowToggle(event, elMatch) {
        PipeleyenApp.fnToggleRequirementRow(elMatch.dataset.req);
    }

    function _fnHandleWorkflowWideAction(event, elMatch) {
        event.preventDefault();
        event.stopPropagation();
        PipeleyenApp.fnRunWorkflowWideAction(
            elMatch.dataset.wfAction, elMatch.dataset.wfArg || "",
            elMatch);
    }

    function _fnHandleOpenArxivConfig(event, elMatch) {
        event.preventDefault();
        event.stopPropagation();
        VaibifyArxivConfig.fnOpen();
    }

    function _fnHandleToggleBinaryForm(event, elMatch) {
        event.preventDefault();
        event.stopPropagation();
        PipeleyenApp.fnToggleBinaryAddForm();
    }

    function _fnHandleWorkflowWideFileLink(event, elMatch) {
        event.preventDefault();
        event.stopPropagation();
        PipeleyenFigureViewer.fnDisplayFileFromContainer(
            elMatch.dataset.path);
    }

    var _DICT_CLICK_HANDLERS = {
        ".btn-discovered": _fnHandleDiscoveredButton,
        ".remote-badge": _fnHandleRemoteBadge,
        ".row-overflow-btn": _fnHandleRowOverflow,
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
        ".btn-ai-declaration-open": _fnHandleAiDeclarationOpen,
        ".btn-add-ai-declaration-step": _fnHandleAddAiDeclarationStep,
        ".wf-action-btn": _fnHandleWorkflowWideAction,
        ".wf-open-arxiv-config": _fnHandleOpenArxivConfig,
        ".wf-toggle-binary-form": _fnHandleToggleBinaryForm,
        ".wf-file-link": _fnHandleWorkflowWideFileLink,
        ".steps-block-header": _fnHandleStepsBlockToggle,
        ".workflow-wide-header": _fnHandleWorkflowWideToggle,
        ".requirement-group-header": _fnHandleRequirementGroupToggle,
        ".requirement-row-header": _fnHandleRequirementRowToggle,
        ".btn-ai-declaration-commit": _fnHandleAiDeclarationCommit,
        ".btn-ai-declaration-untrack": _fnHandleAiDeclarationUntrack,
        ".envelope-open-repos": _fnHandleOpenReposPanel,
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
            (elDetailItem.classList.contains("output") ||
                elDetailItem.classList.contains("tracked-file"))) {
            VaibifySyncManager.fnViewDetailRow(elDetailItem);
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
        if (event.target.closest(".remote-badge")) {
            event.preventDefault();
            event.stopPropagation();
            return;
        }
        /* Drags starting inside the expanded detail area are text
           selections or badge images, never step-reorder intents —
           detail tiles themselves are no longer draggable. */
        if (event.target.closest(".step-detail")) return;
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
        if (elStep) {
            event.preventDefault();
            elStep.classList.add("drop-target");
        }
    }

    function fnHandleDelegatedDragLeave(event) {
        var elStep = event.target.closest(".step-item");
        if (elStep) elStep.classList.remove("drop-target");
    }

    function fnHandleDelegatedDrop(event) {
        var elStep = event.target.closest(".step-item");
        if (elStep) elStep.classList.remove("drop-target");

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
        // Click delegation attaches to the whole Steps panel, not just
        // #listSteps, because the Steps-block header and the
        // Workflow-wide block are siblings of #listSteps — their header
        // clicks bubble to #panelSteps, never through #listSteps. The
        // step-specific change/drag/contextmenu handlers stay on
        // #listSteps (they only ever act on step elements).
        var elClickRoot = elList.closest("#panelSteps") || elList;
        elClickRoot.addEventListener("click",
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
            btnConfigureArxiv: function () {
                VaibifyArxivConfig.fnOpen();
            },
            btnArxivConfigCancel: function () {
                VaibifyArxivConfig.fnClose();
            },
            btnArxivConfigSave: function () {
                VaibifyArxivConfig.fnSave();
            },
            btnArxivConfigRemove: function () {
                VaibifyArxivConfig.fnRemove();
            },
            btnArxivPathMapAdd: function () {
                VaibifyArxivConfig.fnAddPathMapRow();
            },
            btnGitIdentityCancel: function () {
                VaibifySyncManager.fnCloseGitIdentityModal();
            },
            btnGitIdentitySave: function () {
                VaibifySyncManager.fnSaveGitIdentity();
            },
            btnVerifyReproducibility: function () {
                _fnOpenVerifyReproducibilityModal();
            },
            btnVerifyReproducibilityClose: function () {
                _fnCloseVerifyReproducibilityModal();
            },
            btnVerifyReproducibilityManifest: function () {
                VaibifySyncManager.fdictVerifyManifest(
                    PipeleyenApp.fsGetContainerId());
            },
            btnShowDag: function () {
                PipeleyenApp.fnShowDag();
            },
            btnVsCode: function () {
                PipeleyenApp.fnOpenVsCode();
            },
            btnMonitor: function () {},
            btnZenodoStatus: function () {
                if (typeof VaibifyZenodoDepositCard ===
                    "undefined") return;
                VaibifyZenodoDepositCard.fnOpen(
                    PipeleyenApp.fsGetContainerId());
            },
            btnZenodoStatusClose: function () {
                if (typeof VaibifyZenodoDepositCard ===
                    "undefined") return;
                VaibifyZenodoDepositCard.fnClose();
            },
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
            btnAdminNewWindow: function () {
                VaibifyUtilities.fnSpawnNewSession();
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

    /* --- Verify Reproducibility Modal --- */

    var _elVerifyModalPriorFocus = null;
    var _fnVerifyModalKeyHandler = null;

    function _fnOpenVerifyReproducibilityModal() {
        var elModal = document.getElementById(
            "modalVerifyReproducibility");
        if (!elModal) return;
        var sContainerId = PipeleyenApp.fsGetContainerId();
        if (!sContainerId) return;
        _elVerifyModalPriorFocus = document.activeElement;
        elModal.style.display = "flex";
        var elBanner = document.getElementById(
            "verifyReproducibilityBanner");
        var elPanel = document.getElementById(
            "verifyReproducibilityPanel");
        VaibifySyncManager.fnRenderRemoteConsistencyBanner(
            sContainerId, elBanner);
        VaibifySyncManager.fnRenderRemoteSyncPanel(
            sContainerId, elPanel);
        _fnAttachVerifyModalKeyHandler(elModal);
        _fnFocusFirstInteractive(elModal);
    }

    function _fnAttachVerifyModalKeyHandler(elModal) {
        _fnVerifyModalKeyHandler = function (event) {
            if (event.key === "Escape") {
                event.stopPropagation();
                _fnCloseVerifyReproducibilityModal();
                return;
            }
            if (event.key === "Tab") {
                _fnTrapFocusInsideModal(event, elModal);
            }
        };
        document.addEventListener(
            "keydown", _fnVerifyModalKeyHandler);
    }

    function _flistFocusableInside(elModal) {
        var sSelector =
            'button:not([disabled]), [href], input:not([disabled]), ' +
            'select:not([disabled]), textarea:not([disabled]), ' +
            '[tabindex]:not([tabindex="-1"])';
        return Array.prototype.slice.call(
            elModal.querySelectorAll(sSelector));
    }

    function _fnFocusFirstInteractive(elModal) {
        var listFocusable = _flistFocusableInside(elModal);
        if (listFocusable.length > 0) listFocusable[0].focus();
    }

    function _fnTrapFocusInsideModal(event, elModal) {
        var listFocusable = _flistFocusableInside(elModal);
        if (listFocusable.length === 0) return;
        var elFirst = listFocusable[0];
        var elLast = listFocusable[listFocusable.length - 1];
        if (event.shiftKey && document.activeElement === elFirst) {
            event.preventDefault();
            elLast.focus();
        } else if (!event.shiftKey &&
                   document.activeElement === elLast) {
            event.preventDefault();
            elFirst.focus();
        }
    }

    function _fnCloseVerifyReproducibilityModal() {
        var elModal = document.getElementById(
            "modalVerifyReproducibility");
        if (!elModal) return;
        elModal.style.display = "none";
        if (_fnVerifyModalKeyHandler) {
            document.removeEventListener(
                "keydown", _fnVerifyModalKeyHandler);
            _fnVerifyModalKeyHandler = null;
        }
        if (typeof VaibifySyncManager !== "undefined" &&
            VaibifySyncManager.fnInvalidateVerifyCache) {
            VaibifySyncManager.fnInvalidateVerifyCache();
        }
        if (_elVerifyModalPriorFocus &&
            typeof _elVerifyModalPriorFocus.focus === "function") {
            _elVerifyModalPriorFocus.focus();
        }
        _elVerifyModalPriorFocus = null;
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
        var elWorkflowNewWindow = document.getElementById(
            "btnNewVaibifyWindowWorkflows");
        if (elWorkflowNewWindow) {
            elWorkflowNewWindow.addEventListener(
                "click", VaibifyUtilities.fnSpawnNewSession);
        }
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
        document.addEventListener("click", function () {
            VaibifySyncManager.fnDismissAllPicklists();
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
                            .classList.toggle("active",
                                sPanel !== "aics");
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
                    var elPanelAics = document.getElementById(
                        "panelAics");
                    if (elPanelAics) {
                        elPanelAics.classList.toggle(
                            "active", sPanel === "aics");
                    }
                    if (sPanel === "files") {
                        PipeleyenFiles.fnLoadDirectory(
                            "/workspace");
                    } else if (sPanel === "logs") {
                        PipeleyenApp.fnLoadLogs();
                    } else if (sPanel === "repos") {
                        PipeleyenReposPanel.fnRender();
                    } else if (sPanel === "aics") {
                        VaibifyAicsTab.fnRender();
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
            var elTerminalStrip = document.getElementById(
                "terminalStrip");
            var elRight = document.getElementById("panelRight");
            _fnMakeDraggableVertical(
                elHandleV, function (iDeltaY) {
                    var iHeight = elViewerDual.offsetHeight +
                        iDeltaY;
                    var iMaxHeight = elRight.offsetHeight - 120;
                    iHeight = Math.max(80,
                        Math.min(iHeight, iMaxHeight));
                    var iAvailable = elViewerDual.offsetHeight +
                        elTerminalStrip.offsetHeight;
                    var fGrow = iHeight / (iAvailable - iHeight);
                    elViewerDual.style.flex = fGrow + " 1 0";
                });
        }

        var elHandleViewer = document.getElementById(
            "resizeHandleViewer");
        if (elHandleViewer) {
            var elViewerA = document.getElementById("viewerA");
            var elViewerB = document.getElementById("viewerB");
            var elDual = document.getElementById(
                "panelViewerDual");
            _fnMakeDraggable(
                elHandleViewer, function (iDeltaX) {
                    var iWidth = elViewerA.offsetWidth + iDeltaX;
                    var iMaxWidth = elDual.offsetWidth - 120;
                    iWidth = Math.max(100,
                        Math.min(iWidth, iMaxWidth));
                    var iAvailable = elViewerA.offsetWidth +
                        elViewerB.offsetWidth;
                    var fGrow = iWidth / (iAvailable - iWidth);
                    elViewerA.style.flex = fGrow + " 1 0";
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

    /* --- Refresh Remote Status --- */

    function fnBindRefreshRemoteStatus() {
        document.getElementById("btnRefreshWorkflow")
            .addEventListener("click", _fnRefreshRemoteStatus);
    }

    async function _fnRefreshRemoteStatus() {
        var sContainerId = PipeleyenApp.fsGetContainerId();
        if (!sContainerId) return;
        try {
            await VaibifyApi.fdictPost(
                "/api/git/" + encodeURIComponent(sContainerId) +
                "/refresh-remotes", {bForce: true});
            if (typeof VaibifyGitBadges !== "undefined") {
                await VaibifyGitBadges.fnRefresh(sContainerId);
            }
            PipeleyenApp.fnRenderStepList();
            PipeleyenApp.fnShowToast(
                "Remote status refreshed", "success");
        } catch (error) {
            PipeleyenApp.fnShowToast(
                VaibifyUtilities.fsSanitizeErrorForUser(
                    error.message), "error");
        }
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
        fnBindRefreshRemoteStatus: fnBindRefreshRemoteStatus,
        fnBindErrorModal: fnBindErrorModal,
    };
})();
