/* Vaibify — Pipeline execution and state recovery (extracted from scriptApplication.js) */

var PipeleyenPipelineRunner = (function () {
    "use strict";

    var iPreviousOutputCount = 0;
    var _iActiveSentinelMonitor = null;
    var dictAcknowledgedAt = {};
    var MAX_PIPELINE_OUTPUT_LINES = 1000;

    /* --- WebSocket --- */

    function fnConnectPipelineWebSocket() {
        var sContainerId = PipeleyenApp.fsGetContainerId();
        var sSessionToken = PipeleyenApp.fsGetSessionToken();
        return VaibifyWebSocket.fnConnect(
            sContainerId, sSessionToken);
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
            PipeleyenApp.fnShowToast(
                "Command failed (exit "
                + dictEvent.iExitCode + ")", "error");
        } else if (dictEvent.sType === "preflightFailed") {
            var sErrors = dictEvent.listErrors.join("\n");
            PipeleyenApp.fnShowErrorModal(
                "Pre-flight validation failed:\n\n" + sErrors
            );
        } else if (dictEvent.sType === "testResult") {
            PipeleyenTestManager.fnHandleTestResult(dictEvent);
        } else if (dictEvent.sType === "stepStarted") {
            PipeleyenApp.fnSetStepStatus(
                dictEvent.iStepNumber - 1, "running");
            PipeleyenApp.fnRenderStepList();
        } else if (dictEvent.sType === "stepStats") {
            var iStepIdx = dictEvent.iStepNumber - 1;
            var dictWorkflow = PipeleyenApp.fdictGetWorkflow();
            if (dictWorkflow && dictWorkflow.listSteps[iStepIdx]) {
                dictWorkflow.listSteps[iStepIdx].dictRunStats =
                    dictEvent.dictRunStats;
                PipeleyenApp.fnRenderStepList();
            }
        } else if (dictEvent.sType === "stepSkipped") {
            PipeleyenApp.fnSetStepStatus(
                dictEvent.iStepNumber - 1, "skipped");
            fnAppendPipelineOutput(
                "Step " + dictEvent.iStepNumber +
                ": SKIPPED (inputs unchanged)");
            PipeleyenApp.fnRenderStepList();
        } else if (dictEvent.sType === "discoveredOutputs") {
            PipeleyenApp.fnHandleDiscoveredOutputs(dictEvent);
        } else if (dictEvent.sType === "stepPass") {
            var iPassIdx = dictEvent.iStepNumber - 1;
            PipeleyenApp.fnSetStepStatus(iPassIdx, "pass");
            PipeleyenApp.fnClearOutputModified(iPassIdx);
            fnResetUserVerification(iPassIdx);
            fnAcknowledgeStepCompletion(iPassIdx);
            PipeleyenApp.fnInvalidateStepFileCache(iPassIdx);
            PipeleyenApp.fnRenderStepList();
        } else if (dictEvent.sType === "stepFail") {
            var iFailIdx = dictEvent.iStepNumber - 1;
            PipeleyenApp.fnSetStepStatus(iFailIdx, "fail");
            fnResetUserVerification(iFailIdx);
            PipeleyenApp.fnInvalidateStepFileCache(iFailIdx);
            PipeleyenApp.fnRenderStepList();
        } else if (dictEvent.sType === "started") {
            PipeleyenApp.fnStopPipelinePolling();
            PipeleyenApp.fnStopFileChangePolling();
            fnInitPipelineOutput();
            PipeleyenApp.fnShowToast(
                "Pipeline started", "success");
        } else if (dictEvent.sType === "completed") {
            PipeleyenApp.fnClearRunningStatuses();
            PipeleyenApp.fnStartFileChangePolling();
            PipeleyenApp.fnShowToast(
                "Pipeline completed", "success");
            PipeleyenApp.fnRenderStepList();
            if (dictEvent.sLogPath) {
                fnDisplayLogInViewer(dictEvent.sLogPath);
            }
        } else if (dictEvent.sType === "failed") {
            PipeleyenApp.fnClearRunningStatuses();
            PipeleyenApp.fnStartFileChangePolling();
            PipeleyenApp.fnShowToast(
                "Pipeline failed (exit " + dictEvent.iExitCode + ")",
                "error"
            );
            PipeleyenApp.fnRenderStepList();
            if (dictEvent.sLogPath) {
                fnDisplayLogInViewer(dictEvent.sLogPath);
            }
        } else if (dictEvent.sType === "interactivePause") {
            fnShowInteractivePauseDialog(dictEvent);
        } else if (dictEvent.sType === "interactiveTerminalStart") {
            fnRunInteractiveInTerminal(dictEvent);
        }
    }

    function fnSendPipelineAction(dictAction) {
        fnConnectPipelineWebSocket();
        VaibifyWebSocket.fnSend(dictAction);
    }

    /* --- Interactive --- */

    function fnShowInteractivePauseDialog(dictEvent) {
        var sLabel = PipeleyenApp.fsComputeStepLabel(
            dictEvent.iStepIndex);
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
        sCancelLabel, fnOnCancel
    ) {
        var elExisting = document.getElementById("modalConfirm");
        if (elExisting) elExisting.remove();
        var elModal = document.createElement("div");
        elModal.id = "modalConfirm";
        elModal.className = "modal-overlay";
        elModal.style.display = "flex";
        elModal.innerHTML =
            '<div class="modal">' +
            '<h2>' + PipeleyenApp.fnEscapeHtml(sTitle) + '</h2>' +
            '<p style="white-space:pre-wrap;margin-bottom:16px">' +
            PipeleyenApp.fnEscapeHtml(sMessage) + '</p>' +
            '<div class="modal-actions">' +
            '<button class="btn" id="btnConfirmCancel">' +
            PipeleyenApp.fnEscapeHtml(sCancelLabel) + '</button>' +
            '<button class="btn btn-primary" ' +
            'id="btnConfirmOk">' +
            PipeleyenApp.fnEscapeHtml(sConfirmLabel) + '</button>' +
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
        VaibifyWebSocket.fnSendDirect({sAction: sAction});
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
            sDirectory, listCommands, sSentinel
        );
        PipeleyenTerminal.fnSendCommandInFreshTab(sFullCommand);
        _fnMonitorTerminalForSentinel(sSentinel);
    }

    function _fsBuildInteractiveCommand(
        sDirectory, listCommands, sSentinel
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
        if (_iActiveSentinelMonitor) {
            clearInterval(_iActiveSentinelMonitor);
        }
        var I_MAX_SENTINEL_CHECKS = 86400;
        var iCheckCount = 0;
        _iActiveSentinelMonitor = setInterval(function () {
            iCheckCount++;
            if (iCheckCount >= I_MAX_SENTINEL_CHECKS) {
                clearInterval(_iActiveSentinelMonitor);
                _iActiveSentinelMonitor = null;
                PipeleyenApp.fnShowToast(
                    "Interactive step timed out after 24 hours",
                    "error");
                _fnSendInteractiveComplete(1);
                return;
            }
            var sText = _fsReadAllTerminalText();
            var oPattern = new RegExp(
                sSentinel.replace(/[-]/g, "\\-") + "=(\\d+)"
            );
            var oMatch = sText.match(oPattern);
            if (!oMatch) return;
            clearInterval(_iActiveSentinelMonitor);
            _iActiveSentinelMonitor = null;
            var iExitCode = parseInt(oMatch[1], 10);
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
        VaibifyWebSocket.fnSendDirect({
            sAction: "interactiveComplete",
            iExitCode: iExitCode,
        });
    }

    /* --- State --- */

    function fnResetUserVerification(iStepIndex) {
        var dictWorkflow = PipeleyenApp.fdictGetWorkflow();
        var dictStep = dictWorkflow.listSteps[iStepIndex];
        if (!dictStep) return;
        var dictVerify = PipeleyenApp.fdictGetVerification(dictStep);
        if (dictVerify.sUser === "untested") return;
        dictVerify.sUser = "untested";
        dictStep.dictVerification = dictVerify;
        PipeleyenApp.fnSaveStepUpdate(iStepIndex, {
            dictVerification: dictStep.dictVerification,
        });
    }

    function fnAcknowledgeStepCompletion(iStepIndex) {
        var sContainerId = PipeleyenApp.fsGetContainerId();
        if (!sContainerId) return;
        dictAcknowledgedAt[iStepIndex] = Date.now();
        VaibifyApi.fdictPostRaw(
            "/api/pipeline/" + sContainerId +
            "/acknowledge-step/" + iStepIndex
        ).then(function () {
            PipeleyenApp.fnClearOutputModified(iStepIndex);
        }).catch(function () { /* best effort */ });
    }

    function fnClearOutputModified(iStep) {
        PipeleyenApp.fnClearOutputModified(iStep);
    }

    async function fnRecoverPipelineState(sId) {
        try {
            var dictState = await VaibifyApi.fdictGet(
                "/api/pipeline/" + sId + "/state");
            if (!dictState || !dictState.bRunning) {
                if (dictState && dictState.sLogPath &&
                    dictState.iExitCode >= 0) {
                    fnApplyCompletedState(dictState);
                }
                PipeleyenApp.fnStartFileChangePolling();
                return;
            }
            fnApplyRunningState(dictState, true);
            PipeleyenApp.fnStartPipelinePolling(sId);
        } catch (error) {
            PipeleyenApp.fnStartFileChangePolling();
        }
    }

    function fnHandlePipelinePollResult(dictState) {
        if (!dictState) return;
        if (!dictState.bRunning) {
            PipeleyenApp.fnStopPipelinePolling();
            fnApplyCompletedState(dictState);
            if (dictState.sLogPath) {
                fnDisplayLogInViewer(dictState.sLogPath);
            }
            PipeleyenApp.fnShowToast(
                dictState.iExitCode === 0 ?
                    "Pipeline completed" :
                    "Pipeline failed (exit " +
                    dictState.iExitCode + ")",
                dictState.iExitCode === 0 ? "success" : "error"
            );
            PipeleyenApp.fnStartFileChangePolling();
            return;
        }
        fnApplyRunningState(dictState, false);
    }

    function fnApplyRunningState(dictState, bInitial) {
        if (bInitial) {
            fnInitPipelineOutput();
            PipeleyenApp.fnShowToast(
                "Reconnected to running pipeline", "success"
            );
            iPreviousOutputCount = 0;
        }
        var dictResults = dictState.dictStepResults || {};
        for (var sKey in dictResults) {
            var iStep = parseInt(sKey) - 1;
            var sStatus = dictResults[sKey].sStatus;
            if (sStatus === "passed") {
                PipeleyenApp.fnSetStepStatus(iStep, "pass");
            } else if (sStatus === "failed") {
                PipeleyenApp.fnSetStepStatus(iStep, "fail");
            } else if (sStatus === "skipped") {
                PipeleyenApp.fnSetStepStatus(iStep, "");
            }
        }
        if (dictState.iActiveStep > 0) {
            PipeleyenApp.fnSetStepStatus(
                dictState.iActiveStep - 1, "running");
        }
        var iStepCount = dictState.iStepCount || 0;
        for (var i = 0; i < iStepCount; i++) {
            var sIdx = String(i + 1);
            if (!dictResults[sIdx] &&
                i !== dictState.iActiveStep - 1) {
                if (!dictResults[sIdx]) {
                    PipeleyenApp.fnSetStepStatus(i, "queued");
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
        PipeleyenApp.fnRenderStepList();
    }

    function fnApplyCompletedState(dictState) {
        PipeleyenApp.fnClearRunningStatuses();
        var dictResults = dictState.dictStepResults || {};
        for (var sKey in dictResults) {
            var iStep = parseInt(sKey) - 1;
            var sStatus = dictResults[sKey].sStatus;
            if (sStatus === "passed") {
                PipeleyenApp.fnSetStepStatus(iStep, "pass");
            } else if (sStatus === "failed") {
                PipeleyenApp.fnSetStepStatus(iStep, "fail");
            }
        }
        PipeleyenApp.fnRenderStepList();
    }

    /* --- Output --- */

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
        var iExcessCount =
            elOutput.childNodes.length - MAX_PIPELINE_OUTPUT_LINES;
        while (iExcessCount > 0) {
            elOutput.removeChild(elOutput.firstChild);
            iExcessCount--;
        }
        elOutput.scrollTop = elOutput.scrollHeight;
    }

    /* --- Execution --- */

    function fnRunSingleStep(iIndex) {
        var dictWorkflow = PipeleyenApp.fdictGetWorkflow();
        var step = dictWorkflow.listSteps[iIndex];
        if (!step) return;
        if (step.bInteractive) {
            fnRunInteractiveStep(iIndex);
            return;
        }
        PipeleyenApp.fnSetStepStatus(iIndex, "queued");
        PipeleyenApp.fnRenderStepList();
        fnSendPipelineAction({
            sAction: "runSelected",
            listStepIndices: [iIndex],
        });
    }

    function fnRunInteractiveStep(iIndex) {
        var dictWorkflow = PipeleyenApp.fdictGetWorkflow();
        var step = dictWorkflow.listSteps[iIndex];
        if (!step) return;
        var dictVars = PipeleyenApp.fdictBuildClientVariables();
        var listCmds = (step.saDataCommands || []).map(function (c) {
            return PipeleyenApp.fsResolveTemplate(c, dictVars);
        });
        if (listCmds.length === 0) return;
        var sDir = PipeleyenApp.fsResolveTemplate(
            step.sDirectory, dictVars);
        var sUuid = _fsGenerateUuid();
        var sSentinel = "__VAIBIFY_DONE_" + sUuid + "__";
        var sFullCmd = _fsBuildInteractiveCommand(
            sDir, listCmds, sSentinel
        );
        PipeleyenTerminal.fnSendCommandInFreshTab(sFullCmd);
        _fnMonitorStepCompletion(sSentinel, iIndex);
        var elStrip = document.getElementById("terminalStrip");
        if (elStrip) elStrip.scrollIntoView({ behavior: "smooth" });
    }

    function fnRunInteractivePlots(iIndex) {
        var dictWorkflow = PipeleyenApp.fdictGetWorkflow();
        var step = dictWorkflow.listSteps[iIndex];
        if (!step) return;
        var dictVars = PipeleyenApp.fdictBuildClientVariables();
        var listCmds = (step.saPlotCommands || []).map(function (c) {
            return PipeleyenApp.fsResolveTemplate(c, dictVars);
        });
        if (listCmds.length === 0) return;
        var sDir = PipeleyenApp.fsResolveTemplate(
            step.sDirectory, dictVars);
        var sUuid = _fsGenerateUuid();
        var sSentinel = "__VAIBIFY_DONE_" + sUuid + "__";
        var sFullCmd = _fsBuildInteractiveCommand(
            sDir, listCmds, sSentinel
        );
        PipeleyenTerminal.fnSendCommandInFreshTab(sFullCmd);
        _fnMonitorStepCompletion(sSentinel, iIndex);
        var elStrip = document.getElementById("terminalStrip");
        if (elStrip) elStrip.scrollIntoView({ behavior: "smooth" });
    }

    function fnRunStepCombined(iIndex) {
        var dictWorkflow = PipeleyenApp.fdictGetWorkflow();
        var step = dictWorkflow.listSteps[iIndex];
        if (!step) return;
        var bHasOutputFiles = fbStepHasOutputFiles(step);
        var setStepsWithData =
            PipeleyenTestManager.fsetGetStepsWithData();
        if (bHasOutputFiles && setStepsWithData.has(iIndex)) {
            PipeleyenApp.fnShowConfirmModal(
                "Overwrite Output",
                "Output files already exist. Overwrite?",
                function () { fnExecuteStepCombined(iIndex); }
            );
        } else {
            fnExecuteStepCombined(iIndex);
        }
    }

    function fbStepHasOutputFiles(step) {
        var listData = step.saDataFiles || [];
        var listPlots = step.saPlotFiles || [];
        return listData.length > 0 || listPlots.length > 0;
    }

    function fnExecuteStepCombined(iIndex) {
        var dictWorkflow = PipeleyenApp.fdictGetWorkflow();
        var step = dictWorkflow.listSteps[iIndex];
        if (!step) return;
        var dictVars = PipeleyenApp.fdictBuildClientVariables();
        var listCmds = flistResolveStepCommands(step, dictVars);
        if (listCmds.length === 0) return;
        var sDir = PipeleyenApp.fsResolveTemplate(
            step.sDirectory, dictVars);
        var sUuid = _fsGenerateUuid();
        var sSentinel = "__VAIBIFY_DONE_" + sUuid + "__";
        var sFullCmd = _fsBuildInteractiveCommand(
            sDir, listCmds, sSentinel
        );
        PipeleyenTerminal.fnSendCommandInFreshTab(sFullCmd);
        _fnMonitorStepCompletion(sSentinel, iIndex);
        var elStrip = document.getElementById("terminalStrip");
        if (elStrip) elStrip.scrollIntoView({ behavior: "smooth" });
    }

    function flistResolveStepCommands(step, dictVars) {
        var listCmds = [];
        (step.saDataCommands || []).forEach(function (sCmd) {
            listCmds.push(
                PipeleyenApp.fsResolveTemplate(sCmd, dictVars));
        });
        (step.saPlotCommands || []).forEach(function (sCmd) {
            listCmds.push(
                PipeleyenApp.fsResolveTemplate(sCmd, dictVars));
        });
        return listCmds;
    }

    function _fnMonitorStepCompletion(sSentinel, iStepIndex) {
        if (_iActiveSentinelMonitor) {
            clearInterval(_iActiveSentinelMonitor);
        }
        _iActiveSentinelMonitor = setInterval(function () {
            var sText = _fsReadAllTerminalText();
            var oPattern = new RegExp(
                sSentinel.replace(/[-]/g, "\\-") + "=(\\d+)"
            );
            var oMatch = sText.match(oPattern);
            if (!oMatch) return;
            clearInterval(_iActiveSentinelMonitor);
            _iActiveSentinelMonitor = null;
            var iExitCode = parseInt(oMatch[1], 10);
            fnHandleStandaloneStepComplete(iStepIndex, iExitCode);
        }, 1000);
    }

    function fnHandleStandaloneStepComplete(iStepIndex, iExitCode) {
        var sStatus = iExitCode === 0 ? "pass" : "fail";
        PipeleyenApp.fnSetStepStatus(iStepIndex, sStatus);
        var dictWorkflow = PipeleyenApp.fdictGetWorkflow();
        var step = dictWorkflow.listSteps[iStepIndex];
        if (step) {
            step.dictRunStats = step.dictRunStats || {};
            step.dictRunStats.sLastRun =
                PipeleyenApp.fsFormatUtcTimestamp();
        }
        fnResetUserVerification(iStepIndex);
        if (iExitCode === 0) {
            PipeleyenApp.fnClearOutputModified(iStepIndex);
        }
        fnAcknowledgeStepCompletion(iStepIndex);
        PipeleyenApp.fnInvalidateStepFileCache(iStepIndex);
        PipeleyenApp.fnRenderStepList();
        var sLabel = PipeleyenApp.fsComputeStepLabel(iStepIndex);
        var sVerb = iExitCode === 0 ? "completed" : "failed";
        PipeleyenApp.fnShowToast("Step " + sLabel + " " + sVerb,
            iExitCode === 0 ? "success" : "error");
    }

    /* --- Actions --- */

    function fnRunSelected() {
        var listIndices = [];
        document.querySelectorAll(".step-checkbox:checked")
            .forEach(function (el) {
                var iIndex = parseInt(
                    el.closest(".step-item").dataset.index
                );
                listIndices.push(iIndex);
                PipeleyenApp.fnSetStepStatus(iIndex, "queued");
            });
        PipeleyenApp.fnRenderStepList();
        fnSendPipelineAction({
            sAction: "runSelected",
            listStepIndices: listIndices,
        });
    }

    function fsInteractiveWarning() {
        var dictWorkflow = PipeleyenApp.fdictGetWorkflow();
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
        var dictWorkflow = PipeleyenApp.fdictGetWorkflow();
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
            var dictRuntime = await VaibifyApi.fdictGet(
                "/api/runtime");
            return "\n\n" + (dictRuntime.sSleepWarning || "");
        } catch (e) {
            return "";
        }
    }

    function fsEstimateRunTimeSeconds() {
        var dictWorkflow = PipeleyenApp.fdictGetWorkflow();
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
        PipeleyenApp.fnShowConfirmModal(
            "Run All", sMessage, async function () {
                var dictWorkflow = PipeleyenApp.fdictGetWorkflow();
                var listEnablePromises = [];
                dictWorkflow.listSteps.forEach(
                    function (step, iIndex) {
                        if (step.bEnabled === false) {
                            listEnablePromises.push(
                                PipeleyenApp.fnToggleStepEnabled(
                                    iIndex, true)
                            );
                        }
                        PipeleyenApp.fnSetStepStatus(
                            iIndex, "queued");
                    });
                if (listEnablePromises.length > 0) {
                    await Promise.all(listEnablePromises);
                }
                PipeleyenApp.fnRenderStepList();
                fnSendPipelineAction({ sAction: "runAll" });
            });
    }

    async function fnForceRunAll() {
        var sSleepWarn = await fsGetSleepWarning();
        PipeleyenApp.fnShowConfirmModal(
            "Force Run All",
            "This will clear input hashes and re-run every " +
            "automatic step from scratch. Interactive step " +
            "outputs are preserved.\n\n" +
            "All verification states will be reset to untested.",
            function () {
                var sEstimate = fsEstimateRunTime();
                var sTimeMsg = sEstimate ?
                    "\n\n" + sEstimate : "";
                PipeleyenApp.fnShowConfirmModal(
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
        var sContainerId = PipeleyenApp.fsGetContainerId();
        PipeleyenApp.fnShowConfirmModal(
            "Stop All Tasks",
            "This will kill all running pipeline processes " +
            "in the container.\n\n" +
            "Any in-progress computations will be lost.",
            async function () {
                try {
                    var dictResult = await VaibifyApi.fdictPostRaw(
                        "/api/pipeline/" + sContainerId + "/kill"
                    );
                    if (dictResult.bSuccess) {
                        PipeleyenApp.fnClearAllStepStatuses();
                        PipeleyenApp.fnRenderStepList();
                        PipeleyenApp.fnShowToast(
                            "Killed " + dictResult.iProcessesKilled +
                            " process(es)", "success");
                    } else {
                        PipeleyenApp.fnShowToast(
                            "Kill failed", "error");
                    }
                } catch (error) {
                    PipeleyenApp.fnShowToast(
                        PipeleyenApp.fsSanitizeErrorForUser(
                            error.message), "error");
                }
            }
        );
    }

    async function _fnExecuteForceRunAll() {
        var sContainerId = PipeleyenApp.fsGetContainerId();
        PipeleyenApp.fnShowToast("Stopping running tasks...", "success");
        try {
            await VaibifyApi.fdictPostRaw(
                "/api/pipeline/" + sContainerId + "/kill"
            );
        } catch (error) { /* continue even if kill fails */ }
        PipeleyenApp.fnShowToast("Cleaning outputs...", "success");
        try {
            await VaibifyApi.fdictPostRaw(
                "/api/pipeline/" + sContainerId + "/clean"
            );
        } catch (error) {
            PipeleyenApp.fnShowToast(
                PipeleyenApp.fsSanitizeErrorForUser(error.message),
                "error");
            return;
        }
        var dictWorkflow = PipeleyenApp.fdictGetWorkflow();
        var listEnablePromises = [];
        dictWorkflow.listSteps.forEach(function (step, iIndex) {
            if (step.bEnabled === false) {
                listEnablePromises.push(
                    PipeleyenApp.fnToggleStepEnabled(iIndex, true)
                );
            }
            PipeleyenApp.fnSetStepStatus(iIndex, "queued");
        });
        if (listEnablePromises.length > 0) {
            await Promise.all(listEnablePromises);
        }
        PipeleyenApp.fnClearFileExistenceCache();
        PipeleyenApp.fnRenderStepList();
        fnSendPipelineAction({ sAction: "forceRunAll" });
    }

    function fsEstimateRunTime() {
        var dictWorkflow = PipeleyenApp.fdictGetWorkflow();
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

    /* --- Top-level --- */

    function fnVerify() {
        fnSendPipelineAction({ sAction: "verify" });
    }

    function fnRunAllTests() {
        console.log("[RUN-ALL-TESTS] sending action, wsState:",
            VaibifyWebSocket.fiGetReadyState());
        fnSendPipelineAction({ sAction: "runAllTests" });
    }

    async function fnValidateReferences() {
        var sContainerId = PipeleyenApp.fsGetContainerId();
        if (!sContainerId) return;
        try {
            var result = await VaibifyApi.fdictGet(
                "/api/steps/" + sContainerId + "/validate");
            var listWarnings = result.listWarnings;
            if (listWarnings.length === 0) {
                PipeleyenApp.fnShowToast(
                    "All cross-step references are valid",
                    "success"
                );
            } else {
                listWarnings.forEach(function (sWarning) {
                    PipeleyenApp.fnShowToast(sWarning, "error");
                });
            }
        } catch (error) {
            PipeleyenApp.fnShowToast(
                PipeleyenApp.fsSanitizeErrorForUser(error.message),
                "error");
        }
    }

    function fnDisplayLogInViewer(sLogPath) {
        PipeleyenFigureViewer.fnDisplayFileFromContainer(sLogPath);
    }

    /* --- State Management --- */

    function fnResetState() {
        iPreviousOutputCount = 0;
        dictAcknowledgedAt = {};
        if (_iActiveSentinelMonitor) {
            clearInterval(_iActiveSentinelMonitor);
            _iActiveSentinelMonitor = null;
        }
    }

    function fnCancelSentinelMonitor() {
        if (_iActiveSentinelMonitor) {
            clearInterval(_iActiveSentinelMonitor);
            _iActiveSentinelMonitor = null;
        }
    }

    function fiGetAcknowledgedAt(iStep) {
        return dictAcknowledgedAt[iStep] || 0;
    }

    return {
        fnConnectPipelineWebSocket: fnConnectPipelineWebSocket,
        fnHandlePipelineEvent: fnHandlePipelineEvent,
        fnSendPipelineAction: fnSendPipelineAction,
        fnShowInteractivePauseDialog: fnShowInteractivePauseDialog,
        fnRunInteractiveInTerminal: fnRunInteractiveInTerminal,
        fnResetUserVerification: fnResetUserVerification,
        fnAcknowledgeStepCompletion: fnAcknowledgeStepCompletion,
        fnRecoverPipelineState: fnRecoverPipelineState,
        fnHandlePipelinePollResult: fnHandlePipelinePollResult,
        fnApplyRunningState: fnApplyRunningState,
        fnApplyCompletedState: fnApplyCompletedState,
        fnInitPipelineOutput: fnInitPipelineOutput,
        fnAppendPipelineOutput: fnAppendPipelineOutput,
        fnRunSingleStep: fnRunSingleStep,
        fnRunInteractiveStep: fnRunInteractiveStep,
        fnRunInteractivePlots: fnRunInteractivePlots,
        fnRunStepCombined: fnRunStepCombined,
        fbStepHasOutputFiles: fbStepHasOutputFiles,
        fnExecuteStepCombined: fnExecuteStepCombined,
        flistResolveStepCommands: flistResolveStepCommands,
        fnHandleStandaloneStepComplete: fnHandleStandaloneStepComplete,
        fnRunSelected: fnRunSelected,
        fsInteractiveWarning: fsInteractiveWarning,
        fiCountLeadingInteractive: fiCountLeadingInteractive,
        fsGetSleepWarning: fsGetSleepWarning,
        fsEstimateRunTimeSeconds: fsEstimateRunTimeSeconds,
        fnRunAll: fnRunAll,
        fnForceRunAll: fnForceRunAll,
        fnKillPipeline: fnKillPipeline,
        fsEstimateRunTime: fsEstimateRunTime,
        fsFormatDurationLong: fsFormatDurationLong,
        fnVerify: fnVerify,
        fnRunAllTests: fnRunAllTests,
        fnValidateReferences: fnValidateReferences,
        fnDisplayLogInViewer: fnDisplayLogInViewer,
        fnResetState: fnResetState,
        fnCancelSentinelMonitor: fnCancelSentinelMonitor,
        fiGetAcknowledgedAt: fiGetAcknowledgedAt,
    };
})();
