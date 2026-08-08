/* Vaibify — Pipeline execution and state recovery (extracted from scriptApplication.js) */

var VaibifyPipelineRunner = (function () {
    "use strict";

    var fbStepIsInteractive = VaibifyUtilities.fbStepIsInteractive;

    var iPreviousOutputCount = 0;
    var _sStreamingViewer = null;
    var dictAcknowledgedAt = {};
    var MAX_PIPELINE_OUTPUT_LINES = 1000;
    // Set by the remoteDataRecorded event; consumed at run end so
    // freshly pulled remote data is offered for commit immediately
    // instead of sitting silently uncommitted.
    var _bRemoteDataPulledThisRun = false;

    function _fnOfferCommitIfRemoteDataPulled() {
        if (!_bRemoteDataPulledThisRun) return;
        _bRemoteDataPulledThisRun = false;
        VaibifyManifestCheck.fbOfferCommitAfterGenerate(
            VaibifyApp.fsGetContainerId());
    }

    /* --- WebSocket --- */

    function fnConnectPipelineWebSocket() {
        var sContainerId = VaibifyApp.fsGetContainerId();
        var sSessionToken = VaibifyApp.fsGetSessionToken();
        return VaibifyWebSocket.fnConnect(
            sContainerId, sSessionToken);
    }

    function fnHandlePipelineEvent(dictEvent) {
        if (dictEvent.sType === "wsHeartbeat") {
            return;
        }
        if (dictEvent.sType === "outputBatch") {
            var listLines = dictEvent.listLines || [];
            for (var iLine = 0; iLine < listLines.length; iLine++) {
                fnAppendPipelineOutput(listLines[iLine]);
            }
        } else if (dictEvent.sType === "output") {
            fnAppendPipelineOutput(dictEvent.sLine);
        } else if (dictEvent.sType === "verifyTimeout") {
            fnAppendPipelineOutput(dictEvent.sLine);
            VaibifyApp.fnShowToast(
                "Verification timed out on a step — it's reported "
                + "unverified. See the run log.", "warning");
        } else if (dictEvent.sType === "commandFailed") {
            var sMessage =
                "FAILED: " + dictEvent.sCommand +
                "\n  Directory: " + dictEvent.sDirectory +
                "\n  Exit code: " + dictEvent.iExitCode;
            fnAppendPipelineOutput(sMessage);
            VaibifyApp.fnShowToast(
                "Command failed (exit "
                + dictEvent.iExitCode + ")", "error");
        } else if (dictEvent.sType === "preflightFailed") {
            var sErrors = dictEvent.listErrors.join("\n");
            VaibifyApp.fnShowErrorModal(
                "Pre-flight validation failed:\n\n" + sErrors
            );
        } else if (dictEvent.sType === "testResult") {
            VaibifyTestManager.fnHandleTestResult(dictEvent);
        } else if (dictEvent.sType === "stepStarted") {
            VaibifyApp.fnSetStepStatus(
                dictEvent.iStepNumber - 1, "running");
            VaibifyApp.fnRenderStepList();
        } else if (dictEvent.sType === "stepStats") {
            var iStepIdx = dictEvent.iStepNumber - 1;
            var dictWorkflow = VaibifyApp.fdictGetWorkflow();
            if (dictWorkflow && dictWorkflow.listSteps[iStepIdx]) {
                dictWorkflow.listSteps[iStepIdx].dictRunStats =
                    dictEvent.dictRunStats;
                VaibifyApp.fnRenderStepList();
            }
        } else if (dictEvent.sType === "remoteDataRecorded") {
            var iRemoteIdx = dictEvent.iStepNumber - 1;
            var dictWfRemote = VaibifyApp.fdictGetWorkflow();
            if (dictWfRemote && dictWfRemote.listSteps[iRemoteIdx]) {
                dictWfRemote.listSteps[iRemoteIdx].listRemoteData =
                    dictEvent.listRemoteData || [];
                VaibifyApp.fnRenderStepList();
            }
            // Remember that this run changed pulled data so the
            // end-of-run handler can offer to commit it — canonical
            // data must not sit silently uncommitted.
            _bRemoteDataPulledThisRun = true;
        } else if (dictEvent.sType === "stepSkipped") {
            VaibifyApp.fnSetStepStatus(
                dictEvent.iStepNumber - 1, "skipped");
            fnAppendPipelineOutput(
                "Step " + dictEvent.iStepNumber +
                ": SKIPPED (inputs unchanged)");
            VaibifyApp.fnRenderStepList();
        } else if (dictEvent.sType === "discoveredOutputs") {
            VaibifyApp.fnHandleDiscoveredOutputs(dictEvent);
        } else if (dictEvent.sType === "stepPass") {
            var iPassIdx = dictEvent.iStepNumber - 1;
            VaibifyApp.fnSetStepStatus(iPassIdx, "pass");
            VaibifyApp.fnClearOutputModified(iPassIdx);
            fnResetUserVerification(iPassIdx);
            fnAcknowledgeStepCompletion(iPassIdx);
            VaibifyApp.fnInvalidateStepFileCache(iPassIdx);
            VaibifyApp.fnRenderStepList();
        } else if (dictEvent.sType === "stepFail") {
            var iFailIdx = dictEvent.iStepNumber - 1;
            VaibifyApp.fnSetStepStatus(iFailIdx, "fail");
            fnResetUserVerification(iFailIdx);
            VaibifyApp.fnInvalidateStepFileCache(iFailIdx);
            VaibifyApp.fnRenderStepList();
        } else if (dictEvent.sType === "started") {
            VaibifyPolling.fnStopPipelinePolling();
            VaibifyPolling.fnStopFilePolling();
            fnInitPipelineOutput();
            VaibifyApp.fnShowToast(
                _fsStartedToast(dictEvent.sCommand), "success");
        } else if (dictEvent.sType === "completed") {
            VaibifyApp.fnClearRunningStatuses();
            VaibifyApp.fnStartFileChangePolling();
            VaibifyApp.fnShowToast(
                _fsCompletedToast(dictEvent.sCommand), "success");
            VaibifyApp.fnRenderStepList();
            _fnFinalizeLogDisplay(dictEvent.sLogPath);
            _fnOfferCommitIfRemoteDataPulled();
        } else if (dictEvent.sType === "failed") {
            VaibifyApp.fnClearRunningStatuses();
            VaibifyApp.fnStartFileChangePolling();
            VaibifyApp.fnShowToast(
                "Pipeline failed (exit " + dictEvent.iExitCode + ")",
                "error"
            );
            VaibifyApp.fnRenderStepList();
            _fnFinalizeLogDisplay(dictEvent.sLogPath);
            // A later step failing does not un-pull the data: the
            // successful pull still left fresh files that need review
            // and commit, so the offer fires here too.
            _fnOfferCommitIfRemoteDataPulled();
        } else if (dictEvent.sType === "runRefused") {
            VaibifyApp.fnResetQueuedSteps(
                dictEvent.listStepIndices || []);
            VaibifyApp.fnRenderStepList();
            if (dictEvent.sReason === "remoteDataOverwrite") {
                _fnHandleRemoteOverwriteRefusal(dictEvent);
                return;
            }
            VaibifyApp.fnShowToast(
                dictEvent.sMessage ||
                "A pipeline action is already running.", "error");
        } else if (dictEvent.sType === "interactivePause") {
            fnShowInteractivePauseDialog(dictEvent);
        } else if (dictEvent.sType === "interactiveTerminalStart") {
            fnRunInteractiveInTerminal(dictEvent);
        }
    }

    function fnSendPipelineAction(dictAction) {
        _fnMaybeShowRuntimeLimitNotice(dictAction);
        fnConnectPipelineWebSocket();
        VaibifyWebSocket.fnSend(dictAction);
    }

    var _SET_RUN_ACTIONS_WITH_NOTICE = {
        "runSelected": true, "runAll": true, "runFrom": true,
    };

    function _fnMaybeShowRuntimeLimitNotice(dictAction) {
        // One-time, non-blocking heads-up on the first run of a
        // project in this browser: the advisory runtime limit
        // exists, where to change it, and that no run is ever
        // stopped by it. A consent modal would be dishonest — there
        // is no consequence to consent to.
        if (!_SET_RUN_ACTIONS_WITH_NOTICE[dictAction
            && dictAction.sAction]) return;
        var dictWorkflow = VaibifyApp.fdictGetWorkflow() || {};
        var fDefault =
            dictWorkflow.fDefaultWallClockBudgetSeconds || 0;
        if (!(fDefault > 0)) return;
        var sKey = "vaibifyRuntimeNoticeShown:" +
            (dictWorkflow.sProjectRepoPath ||
                dictWorkflow.sWorkflowName || "");
        try {
            if (localStorage.getItem(sKey)) return;
            localStorage.setItem(sKey, "true");
        } catch (e) { /* localStorage may be unavailable */ }
        var sLimit = fDefault % 3600 === 0
            ? (fDefault / 3600) + " hours"
            : fDefault + " seconds";
        VaibifyApp.fnShowToast(
            "Heads-up: steps in this project are expected to " +
            "finish within " + sLimit + ". A step running longer " +
            "is flagged as possibly hung — the run is never " +
            "stopped. Change the default under Settings, or " +
            "right-click a step to set its own limit.", "warning");
    }

    function _fsStartedToast(sCommand) {
        var sCmd = sCommand || "";
        if (sCmd === "runSelected") return "Step started";
        if (sCmd === "verify") return "Verifying outputs";
        if (sCmd === "runAllTests") return "Running all tests";
        return "Pipeline started";
    }

    function _fsCompletedToast(sCommand) {
        var sCmd = sCommand || "";
        if (sCmd === "runSelected") return "Step completed";
        if (sCmd === "verify") return "Verification complete";
        if (sCmd === "runAllTests") return "All tests complete";
        return "Pipeline completed";
    }

    /* --- Interactive --- */

    function fnShowInteractivePauseDialog(dictEvent) {
        var sLabel = VaibifyApp.fsComputeStepLabel(
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
            '<h2>' + VaibifyUtilities.fnEscapeHtml(sTitle) + '</h2>' +
            '<p style="white-space:pre-wrap;margin-bottom:16px">' +
            VaibifyUtilities.fnEscapeHtml(sMessage) + '</p>' +
            '<div class="modal-actions">' +
            '<button class="btn" id="btnConfirmCancel">' +
            VaibifyUtilities.fnEscapeHtml(sCancelLabel) + '</button>' +
            '<button class="btn btn-primary" ' +
            'id="btnConfirmOk">' +
            VaibifyUtilities.fnEscapeHtml(sConfirmLabel) + '</button>' +
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
        if (!VaibifyTerminal.fbSendCommandInFreshTab(sFullCommand)) {
            _fnRefuseInteractiveWithoutTerminal();
            return;
        }
    }

    /* An interactive step is defined as one a human drives in a shell,
       so with terminals disabled there is nowhere to run it. Both
       launch paths say so; the runner-driven one additionally
       reports the step FAILED rather than leaving the runner polling for
       a sentinel no shell will ever print — a silent hang would leave
       the step showing "running" forever, which is precisely the kind of
       dashboard lie the container state must never tell. */
    var S_INTERACTIVE_NEEDS_TERMINAL =
        "Interactive steps need a terminal, and terminals are disabled. "
        + "Make the step automated, or run its commands in a shell you "
        + "open yourself with docker exec.";

    function _fnRefuseInteractiveWithoutTerminal() {
        VaibifyApp.fnShowToast(S_INTERACTIVE_NEEDS_TERMINAL, "error");
        _fnSendInteractiveComplete(1);
    }

    function _fsBuildInteractiveCommand(
        sDirectory, listCommands, sSentinel
    ) {
        var sAbsDirectory = _fsResolveStepDirectory(sDirectory);
        var sCd = sAbsDirectory
            ? "cd '" + sAbsDirectory.replace(/'/g, "'\\''") + "' && "
            : "";
        var sJoined = listCommands.join(" && ");
        return sCd + sJoined +
            "; echo " + sSentinel + "=$?";
    }

    function _fsResolveStepDirectory(sDirectory) {
        if (!sDirectory) return "";
        if (sDirectory.charAt(0) === "/") return sDirectory;
        var dictWorkflow = VaibifyApp.fdictGetWorkflow() || {};
        var sRepo = dictWorkflow.sProjectRepoPath || "";
        if (!sRepo) return sDirectory;
        return sRepo.replace(/\/+$/, "") + "/" + sDirectory;
    }

    function _fsGenerateUuid() {
        return "xxxx-xxxx".replace(/x/g, function () {
            return Math.floor(Math.random() * 16).toString(16);
        });
    }

    function _fnSendInteractiveComplete(iExitCode) {
        VaibifyWebSocket.fnSendDirect({
            sAction: "interactiveComplete",
            iExitCode: iExitCode,
        });
    }

    /* --- State --- */

    function fnResetUserVerification(iStepIndex) {
        var dictWorkflow = VaibifyApp.fdictGetWorkflow();
        var dictStep = dictWorkflow.listSteps[iStepIndex];
        if (!dictStep) return;
        var dictVerify = VaibifyApp.fdictGetVerification(dictStep);
        if (dictVerify.sUser === "untested") return;
        dictVerify.sUser = "untested";
        delete dictVerify.sLastUserUpdate;
        dictStep.dictVerification = dictVerify;
        VaibifyApp.fnSaveStepUpdate(iStepIndex, {
            dictVerification: dictStep.dictVerification,
        });
    }

    function fnAcknowledgeStepCompletion(iStepIndex) {
        var sContainerId = VaibifyApp.fsGetContainerId();
        if (!sContainerId) return;
        dictAcknowledgedAt[iStepIndex] = Date.now();
        VaibifyApi.fdictPostRaw(
            "/api/pipeline/" + sContainerId +
            "/acknowledge-step/" + iStepIndex
        ).then(function () {
            VaibifyApp.fnClearOutputModified(iStepIndex);
        }).catch(function () { /* best effort */ });
    }

    function fnClearOutputModified(iStep) {
        VaibifyApp.fnClearOutputModified(iStep);
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
                VaibifyApp.fnStartFileChangePolling();
                return;
            }
            fnApplyRunningState(dictState, true);
            VaibifyPolling.fnStartPipelinePolling(sId);
        } catch (error) {
            VaibifyApp.fnStartFileChangePolling();
        }
    }

    function fnHandlePipelinePollResult(dictState) {
        if (!dictState) return;
        if (!dictState.bRunning) {
            VaibifyPolling.fnStopPipelinePolling();
            fnApplyCompletedState(dictState);
            _fnFinalizeLogDisplay(dictState.sLogPath);
            VaibifyApp.fnShowToast(
                dictState.iExitCode === 0 ?
                    "Pipeline completed" :
                    "Pipeline failed (exit " +
                    dictState.iExitCode + ")",
                dictState.iExitCode === 0 ? "success" : "error"
            );
            VaibifyApp.fnStartFileChangePolling();
            return;
        }
        fnApplyRunningState(dictState, false);
    }

    function fnApplyRunningState(dictState, bInitial) {
        if (bInitial) {
            fnInitPipelineOutput();
            VaibifyApp.fnShowToast(
                "Reconnected to running pipeline", "success"
            );
            iPreviousOutputCount = 0;
        }
        var dictResults = dictState.dictStepResults || {};
        for (var sKey in dictResults) {
            var iStep = parseInt(sKey) - 1;
            var sStatus = dictResults[sKey].sStatus;
            if (sStatus === "passed") {
                VaibifyApp.fnSetStepStatus(iStep, "pass");
            } else if (sStatus === "failed") {
                VaibifyApp.fnSetStepStatus(iStep, "fail");
            } else if (sStatus === "skipped") {
                VaibifyApp.fnSetStepStatus(iStep, "");
            }
        }
        if (dictState.iActiveStep > 0) {
            VaibifyApp.fnSetStepStatus(
                dictState.iActiveStep - 1, "running");
        }
        var iStepCount = dictState.iStepCount || 0;
        for (var i = 0; i < iStepCount; i++) {
            var sIdx = String(i + 1);
            if (!dictResults[sIdx] &&
                i !== dictState.iActiveStep - 1) {
                if (!dictResults[sIdx]) {
                    VaibifyApp.fnSetStepStatus(i, "queued");
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
                    elLine.style.color = "var(--color-red-text)";
                } else if (sLine.startsWith("$")) {
                    elLine.style.color =
                        "var(--color-blue, #3498db)";
                }
                elOutput.appendChild(elLine);
            });
            elOutput.scrollTop = elOutput.scrollHeight;
            iPreviousOutputCount = listOutput.length;
        }
        VaibifyApp.fnRenderStepList();
    }

    function fnApplyCompletedState(dictState) {
        VaibifyApp.fnClearRunningStatuses();
        var dictResults = dictState.dictStepResults || {};
        for (var sKey in dictResults) {
            var iStep = parseInt(sKey) - 1;
            var sStatus = dictResults[sKey].sStatus;
            if (sStatus === "passed") {
                VaibifyApp.fnSetStepStatus(iStep, "pass");
            } else if (sStatus === "failed") {
                VaibifyApp.fnSetStepStatus(iStep, "fail");
            }
        }
        VaibifyApp.fnRenderStepList();
    }

    /* --- Output --- */

    function fnInitPipelineOutput() {
        if (_sStreamingViewer === null) {
            VaibifyFigureViewer.fnClaimNextViewerForReplacement(
                "pipeline output", function (sViewer) {
                    _sStreamingViewer = sViewer;
                    _fnPaintPipelineOutputViewer();
                });
            return;
        }
        _fnPaintPipelineOutputViewer();
    }

    function _fnPaintPipelineOutputViewer() {
        var elViewport = document.getElementById(
            "viewport" + _sStreamingViewer);
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
            elLine.style.color = "var(--color-red-text, #ff8589)";
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

    /* --- Remote-data overwrite confirmation --- */

    function _fnHandleRemoteOverwriteRefusal(dictEvent) {
        // The server refused because the run would re-pull remote
        // data over the canonical committed copy. The refusal echoes
        // the original request, so a confirmed retry re-dispatches
        // without any client-side caching.
        var dictRetry = Object.assign(
            {}, dictEvent.dictOriginalRequest || {},
            {
                sAction: dictEvent.sAction,
                bConfirmRemoteOverwrite: true,
            });
        VaibifyApp.fnShowConfirmModal(
            "Overwrite canonical data?",
            "Step(s) " +
            (dictEvent.listStepLabels || []).join(", ") +
            " pull remote data over the committed copy:\n" +
            (dictEvent.listRemoteOverwritePaths || []).join("\n") +
            "\n\nThe remote source may have changed since the " +
            "canonical results were generated. Overwrite and " +
            "re-pull?",
            function () { fnSendPipelineAction(dictRetry); }
        );
    }

    function fnConfirmRemoteOverwriteThen(step, fnProceed) {
        // Frontend-only gate for the interactive terminal lane: the
        // Run-in-Terminal buttons compose a shell command and never
        // reach the server dispatch choke point, so the check runs
        // here. Same rule as the server gate: a first pull (nothing
        // on disk) proceeds silently.
        var listPaths = (step.listRemoteData || [])
            .map(function (dictRemote) {
                return (dictRemote && dictRemote.sPath) || "";
            })
            .filter(Boolean);
        if (listPaths.length === 0) {
            fnProceed();
            return;
        }
        var sContainerId = VaibifyApp.fsGetContainerId();
        VaibifyApi.fdictPost(
            "/api/files/" + sContainerId + "/exist",
            {saRelativePaths: listPaths}
        ).then(function (dictResponse) {
            var dictExists = (dictResponse &&
                dictResponse.dictExists) || {};
            var listExisting = listPaths.filter(function (sPath) {
                return dictExists[sPath];
            });
            if (listExisting.length === 0) {
                fnProceed();
                return;
            }
            VaibifyApp.fnShowConfirmModal(
                "Overwrite canonical data?",
                "This step pulls remote data over the committed " +
                "copy:\n" + listExisting.join("\n") +
                "\n\nThe remote source may have changed since the " +
                "canonical results were generated. Overwrite and " +
                "re-pull?",
                fnProceed);
        }).catch(function () {
            // The existence check could not run — fail safe: ask.
            VaibifyApp.fnShowConfirmModal(
                "Overwrite canonical data?",
                "This step declares remote-pulled data and the " +
                "current files could not be checked. Overwrite if " +
                "present?",
                fnProceed);
        });
    }

    /* --- Execution --- */

    function fnRunSingleStep(iIndex) {
        var dictWorkflow = VaibifyApp.fdictGetWorkflow();
        var step = dictWorkflow.listSteps[iIndex];
        if (!step) return;
        if (fbStepIsInteractive(step)) {
            fnRunInteractiveStep(iIndex);
            return;
        }
        VaibifyApp.fnSetStepStatus(iIndex, "queued");
        VaibifyApp.fnRenderStepList();
        fnSendPipelineAction({
            sAction: "runSelected",
            listStepIndices: [iIndex],
        });
    }

    function fnRunInteractiveStep(iIndex) {
        var dictWorkflow = VaibifyApp.fdictGetWorkflow();
        var step = dictWorkflow.listSteps[iIndex];
        if (!step) return;
        if (!fbStepIsInteractive(step)) {
            _fnDispatchSingleStep(iIndex, "dataOnly");
            return;
        }
        var dictVars = VaibifyApp.fdictBuildClientVariables();
        var listCmds = (step.saDataCommands || []).map(function (c) {
            return VaibifyUtilities.fsResolveTemplate(c, dictVars);
        });
        fnConfirmRemoteOverwriteThen(step, function () {
            _fnLaunchInteractiveCommands(iIndex, step, listCmds);
        });
    }

    function fnRunInteractivePlots(iIndex) {
        var dictWorkflow = VaibifyApp.fdictGetWorkflow();
        var step = dictWorkflow.listSteps[iIndex];
        if (!step) return;
        if (!fbStepIsInteractive(step)) {
            _fnDispatchSingleStep(iIndex, "plotsOnly");
            return;
        }
        var dictVars = VaibifyApp.fdictBuildClientVariables();
        var listCmds = (step.saPlotCommands || []).map(function (c) {
            return VaibifyUtilities.fsResolveTemplate(c, dictVars);
        });
        fnConfirmRemoteOverwriteThen(step, function () {
            _fnLaunchInteractiveCommands(iIndex, step, listCmds);
        });
    }

    function _fnLaunchInteractiveCommands(iIndex, step, listCmds) {
        // Shared terminal launch for the three interactive entry
        // points (data, plots, combined) — identical before the
        // remote-overwrite gate forced the extraction (rule of three).
        if (listCmds.length === 0) return;
        var dictVars = VaibifyApp.fdictBuildClientVariables();
        var sDir = VaibifyUtilities.fsResolveTemplate(
            step.sDirectory, dictVars);
        var sUuid = _fsGenerateUuid();
        var sSentinel = "__VAIBIFY_DONE_" + sUuid + "__";
        var sFullCmd = _fsBuildInteractiveCommand(
            sDir, listCmds, sSentinel
        );
        if (!VaibifyTerminal.fbSendCommandInFreshTab(sFullCmd)) {
            VaibifyApp.fnShowToast(S_INTERACTIVE_NEEDS_TERMINAL, "error");
            return;
        }
    }

    function _fnDispatchSingleStep(iIndex, sRunMode) {
        VaibifyApp.fnSetStepStatus(iIndex, "queued");
        VaibifyApp.fnRenderStepList();
        fnSendPipelineAction({
            sAction: "runSelected",
            listStepIndices: [iIndex],
            sRunMode: sRunMode,
        });
    }

    function fnRunStepCombined(iIndex) {
        var dictWorkflow = VaibifyApp.fdictGetWorkflow();
        var step = dictWorkflow.listSteps[iIndex];
        if (!step) return;
        var bHasOutputFiles = fbStepHasOutputFiles(step);
        var setStepsWithData =
            VaibifyTestManager.fsetGetStepsWithData();
        if (bHasOutputFiles && setStepsWithData.has(iIndex)) {
            VaibifyApp.fnShowConfirmModal(
                "Overwrite Output",
                "Output files already exist. Overwrite?",
                function () { fnExecuteStepCombined(iIndex); }
            );
        } else {
            fnExecuteStepCombined(iIndex);
        }
    }

    function fbStepHasOutputFiles(step) {
        var listData = step.saOutputDataFiles || [];
        var listPlots = step.saPlotFiles || [];
        return listData.length > 0 || listPlots.length > 0;
    }

    function fnExecuteStepCombined(iIndex) {
        var dictWorkflow = VaibifyApp.fdictGetWorkflow();
        var step = dictWorkflow.listSteps[iIndex];
        if (!step) return;
        if (!fbStepIsInteractive(step)) {
            _fnDispatchSingleStep(iIndex, "full");
            return;
        }
        var dictVars = VaibifyApp.fdictBuildClientVariables();
        var listCmds = flistResolveStepCommands(step, dictVars);
        fnConfirmRemoteOverwriteThen(step, function () {
            _fnLaunchInteractiveCommands(iIndex, step, listCmds);
        });
    }

    function flistResolveStepCommands(step, dictVars) {
        var listCmds = [];
        (step.saDataCommands || []).forEach(function (sCmd) {
            listCmds.push(
                VaibifyUtilities.fsResolveTemplate(sCmd, dictVars));
        });
        (step.saPlotCommands || []).forEach(function (sCmd) {
            listCmds.push(
                VaibifyUtilities.fsResolveTemplate(sCmd, dictVars));
        });
        return listCmds;
    }

    function fnHandleStandaloneStepComplete(iStepIndex, iExitCode) {
        var sStatus = iExitCode === 0 ? "pass" : "fail";
        VaibifyApp.fnSetStepStatus(iStepIndex, sStatus);
        fnResetUserVerification(iStepIndex);
        if (iExitCode === 0) {
            VaibifyApp.fnClearOutputModified(iStepIndex);
        }
        fnAcknowledgeStepCompletion(iStepIndex);
        VaibifyApp.fnInvalidateStepFileCache(iStepIndex);
        VaibifyApp.fnRenderStepList();
        var sLabel = VaibifyApp.fsComputeStepLabel(iStepIndex);
        var sVerb = iExitCode === 0 ? "completed" : "failed";
        VaibifyApp.fnShowToast("Step " + sLabel + " " + sVerb,
            iExitCode === 0 ? "success" : "error");
        // A standalone Run-in-Terminal pull never produces the
        // remoteDataRecorded event (no server runner), so the commit
        // offer keys off the step's declaration directly.
        var dictWorkflow = VaibifyApp.fdictGetWorkflow();
        var step = dictWorkflow &&
            dictWorkflow.listSteps[iStepIndex];
        if (iExitCode === 0 && step &&
            (step.listRemoteData || []).length > 0) {
            VaibifyManifestCheck.fbOfferCommitAfterGenerate(
                VaibifyApp.fsGetContainerId());
        }
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
                VaibifyApp.fnSetStepStatus(iIndex, "queued");
            });
        VaibifyApp.fnRenderStepList();
        fnSendPipelineAction({
            sAction: "runSelected",
            listStepIndices: listIndices,
        });
    }

    function fsInteractiveWarning() {
        var dictWorkflow = VaibifyApp.fdictGetWorkflow();
        if (!dictWorkflow || !dictWorkflow.listSteps) return "";
        var iLeading = fiCountLeadingInteractive();
        if (iLeading > 0) {
            return "\n\nThe first " + iLeading +
                " step(s) are interactive. The pipeline will " +
                "pause at each one for your input.";
        }
        var bHasMiddle = dictWorkflow.listSteps.some(
            function (step) { return fbStepIsInteractive(step); }
        );
        if (bHasMiddle) {
            return "\n\nThe pipeline contains interactive steps " +
                "and will pause when it reaches them.";
        }
        return "";
    }

    function fiCountLeadingInteractive() {
        var dictWorkflow = VaibifyApp.fdictGetWorkflow();
        if (!dictWorkflow || !dictWorkflow.listSteps) return 0;
        var iCount = 0;
        for (var i = 0; i < dictWorkflow.listSteps.length; i++) {
            if (!fbStepIsInteractive(dictWorkflow.listSteps[i])) break;
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
        var dictWorkflow = VaibifyApp.fdictGetWorkflow();
        if (!dictWorkflow || !dictWorkflow.listSteps) return 0;
        var fTotal = 0;
        dictWorkflow.listSteps.forEach(function (step) {
            if (step.bRunEnabled === false) return;
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
        VaibifyApp.fnShowConfirmModal(
            "Run All", sMessage, function () {
                // Queue only the steps that will actually run. The
                // backend honors bRunEnabled and skips disabled steps,
                // so the frontend must NOT re-enable them: doing so
                // contradicts the "enabled steps" prompt, persists a
                // bRunEnabled flip to project.json, and silently clears
                // the Tier 5 reproduce refusal that names disabled steps.
                _fnQueueEnabledSteps();
                VaibifyApp.fnRenderStepList();
                fnSendPipelineAction({ sAction: "runAll" });
            });
    }

    function _fnQueueEnabledSteps() {
        var dictWorkflow = VaibifyApp.fdictGetWorkflow();
        dictWorkflow.listSteps.forEach(function (step, iIndex) {
            if (step.bRunEnabled !== false) {
                VaibifyApp.fnSetStepStatus(iIndex, "queued");
            }
        });
    }

    async function fnForceRunAll() {
        var sSleepWarn = await fsGetSleepWarning();
        VaibifyApp.fnShowConfirmModal(
            "Force Run All",
            "This will clear input hashes and re-run every " +
            "automatic step from scratch. Interactive step " +
            "outputs are preserved.\n\n" +
            "All verification states will be reset to untested.",
            function () {
                var sEstimate = fsEstimateRunTime();
                var sTimeMsg = sEstimate ?
                    "\n\n" + sEstimate : "";
                VaibifyApp.fnShowConfirmModal(
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
        var sContainerId = VaibifyApp.fsGetContainerId();
        VaibifyApp.fnShowConfirmModal(
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
                        VaibifyApp.fnClearAllStepStatuses();
                        VaibifyApp.fnRenderStepList();
                        VaibifyApp.fnShowToast(
                            "Killed " + dictResult.iProcessesKilled +
                            " process(es)", "success");
                    } else {
                        VaibifyApp.fnShowToast(
                            "Kill failed", "error");
                    }
                } catch (error) {
                    VaibifyApp.fnShowToast(
                        VaibifyUtilities.fsSanitizeErrorForUser(
                            error.message), "error");
                }
            }
        );
    }

    async function _fnExecuteForceRunAll() {
        var sContainerId = VaibifyApp.fsGetContainerId();
        VaibifyApp.fnShowToast("Stopping running tasks...", "success");
        try {
            await VaibifyApi.fdictPostRaw(
                "/api/pipeline/" + sContainerId + "/kill"
            );
        } catch (error) { /* continue even if kill fails */ }
        VaibifyApp.fnShowToast("Cleaning outputs...", "success");
        try {
            await VaibifyApi.fdictPostRaw(
                "/api/pipeline/" + sContainerId + "/clean"
            );
        } catch (error) {
            VaibifyApp.fnShowToast(
                VaibifyUtilities.fsSanitizeErrorForUser(error.message),
                "error");
            return;
        }
        // Same rule as Run All: queue only enabled steps and never
        // re-enable a disabled one. The backend skips disabled steps
        // for forceRunAll too.
        _fnQueueEnabledSteps();
        VaibifyApp.fnClearFileExistenceCache();
        VaibifyApp.fnRenderStepList();
        fnSendPipelineAction({ sAction: "forceRunAll" });
    }

    function fsEstimateRunTime() {
        var dictWorkflow = VaibifyApp.fdictGetWorkflow();
        if (!dictWorkflow || !dictWorkflow.listSteps) return "";
        var fTotalSeconds = 0;
        var iStepsWithTime = 0;
        var iEnabledSteps = 0;
        dictWorkflow.listSteps.forEach(function (step) {
            if (step.bRunEnabled === false) return;
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
            return "This project will require at least " + sTime +
                " (based on " + iStepsWithTime + " of " +
                iEnabledSteps + " steps).";
        }
        return "This project will require at least " + sTime + ".";
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

    function fiRunAllTests() {
        console.log("[RUN-ALL-TESTS] sending action, wsState:",
            VaibifyWebSocket.fiGetReadyState());
        fnSendPipelineAction({ sAction: "runAllTests" });
    }

    async function fnVerifyDependencies() {
        var sContainerId = VaibifyApp.fsGetContainerId();
        if (!sContainerId) return;
        try {
            var result = await VaibifyApi.fdictGet(
                "/api/steps/" + sContainerId + "/validate");
            var listWarnings = result.listWarnings;
            if (listWarnings.length === 0) {
                VaibifyApp.fnShowToast(
                    "All cross-step references are valid",
                    "success"
                );
            } else {
                listWarnings.forEach(function (sWarning) {
                    VaibifyApp.fnShowToast(sWarning, "error");
                });
            }
            VaibifyApp.fnStartFileChangePolling();
        } catch (error) {
            VaibifyApp.fnShowToast(
                VaibifyUtilities.fsSanitizeErrorForUser(error.message),
                "error");
        }
    }

    function fnDisplayLogInViewer(sLogPath) {
        VaibifyFigureViewer.fnDisplayFileFromContainer(sLogPath);
    }

    function _fnFinalizeLogDisplay(sLogPath) {
        if (!sLogPath) {
            _sStreamingViewer = null;
            return;
        }
        if (_sStreamingViewer !== null) {
            VaibifyFigureViewer.fnDisplayFileInViewer(
                _sStreamingViewer, sLogPath, "");
            _sStreamingViewer = null;
        } else {
            fnDisplayLogInViewer(sLogPath);
        }
    }

    /* --- State Management --- */

    function fnResetState() {
        iPreviousOutputCount = 0;
        dictAcknowledgedAt = {};
        _sStreamingViewer = null;
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
        fiRunAllTests: fiRunAllTests,
        fnVerifyDependencies: fnVerifyDependencies,
        fnDisplayLogInViewer: fnDisplayLogInViewer,
        fnResetState: fnResetState,
        fiGetAcknowledgedAt: fiGetAcknowledgedAt,
    };
})();
