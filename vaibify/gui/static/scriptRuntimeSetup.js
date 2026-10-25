/* The "Set up Docker" wizard: shows the hub's plan for this machine and
   runs its next step on the researcher's click.

   Every word and command shown comes from the hub
   (GET /api/runtime/setup); the page sends back only the NAME of an
   action the hub offered, never a command. A step only the researcher
   can do offers Close and Continue, and Continue reads the machine
   again, so the wizard carries on from whatever they installed. The
   step log is polled only while a step runs, at the build window's
   cadence. */
var VaibifyRuntimeSetup = (function () {
    "use strict";

    var I_PROGRESS_POLL_MILLISECONDS = 1500;
    var _iProgressTimer = null;
    var _dictLastStepShown = null;

    function _elOverlay() {
        return document.getElementById("modalRuntimeSetup");
    }

    function _elPart(sClass) {
        return _elOverlay().querySelector("." + sClass);
    }

    function _elMake(sTag, sClass, sText) {
        var el = document.createElement(sTag);
        if (sClass) el.className = sClass;
        if (sText !== undefined) el.textContent = sText;
        return el;
    }

    function _elButton(sLabel, sId, fnOnClick, bPrimary) {
        var el = _elMake("button", "btn" + (bPrimary ? " btn-primary" : ""),
            sLabel);
        el.type = "button";
        if (sId) el.id = sId;
        el.addEventListener("click", fnOnClick);
        return el;
    }

    function _fnSetBusy(bBusy) {
        _elPart("runtime-setup-buttons").querySelectorAll("button").forEach(
            function (el) { el.disabled = bBusy; });
    }

    function _fnShowMessage(sText, sClass) {
        var elMessage = _elPart("runtime-setup-message");
        elMessage.textContent = sText || "";
        elMessage.className = "runtime-setup-message" +
            (sClass ? " " + sClass : "");
    }

    function _elCommandBlock(sCommand) {
        var elRow = _elMake("div", "runtime-setup-command");
        elRow.appendChild(_elMake("code", "", sCommand));
        var elCopy = _elButton("Copy", "", function () {
            VaibifyFileOps.fpromiseCopyText(sCommand).then(function (bOk) {
                elCopy.textContent = bOk ? "Copied" : "Copy failed";
            });
        }, false);
        elRow.appendChild(elCopy);
        return elRow;
    }

    function _elSettingsInputs(dictSettings) {
        var elSettings = _elMake("div", "runtime-setup-settings");
        [["CPUs", "inputRuntimeSetupCpus", dictSettings.iCpus],
         ["Memory (GB)", "inputRuntimeSetupMemory",
          dictSettings.iMemoryGigabytes]].forEach(function (listField) {
            var elLabel = _elMake("label", "", listField[0] + " ");
            var elInput = _elMake("input");
            elInput.type = "number";
            elInput.min = "1";
            elInput.step = "1";
            elInput.id = listField[1];
            elInput.value = String(listField[2]);
            elLabel.appendChild(elInput);
            elSettings.appendChild(elLabel);
        });
        return elSettings;
    }

    function _elStepCard(dictStep) {
        var elCard = _elMake("div", "runtime-setup-step");
        elCard.dataset.stepName = dictStep.sStepName;
        elCard.dataset.stepKind = dictStep.sKind;
        elCard.appendChild(_elMake("h3", "", dictStep.sTitle));
        elCard.appendChild(_elMake("p", "", dictStep.sExplanation));
        if (dictStep.dictSettings) {
            elCard.appendChild(_elSettingsInputs(dictStep.dictSettings));
        }
        (dictStep.listCommandsToShow || []).forEach(function (sCommand) {
            elCard.appendChild(_elCommandBlock(sCommand));
        });
        return elCard;
    }

    function _elLaterSteps(listSteps) {
        var elLater = _elMake("p", "runtime-setup-later");
        elLater.textContent = "Then: " + listSteps.map(
            function (dictStep) { return dictStep.sTitle; }).join(", ") + ".";
        return elLater;
    }

    function _fnRenderButtons(dictPlan, dictStep) {
        var elButtons = _elPart("runtime-setup-buttons");
        elButtons.innerHTML = "";
        elButtons.appendChild(_elButton("Close", "btnRuntimeSetupClose",
            fnClose, false));
        if (!dictStep) return;
        if (dictStep.sKind === "needs-you") {
            elButtons.appendChild(_elButton("Continue",
                "btnRuntimeSetupContinue", _fnContinue, true));
            return;
        }
        dictStep.listActions.forEach(function (dictAction) {
            var elAction = _elButton(dictAction.sLabel, "", function () {
                _fnRunAction(dictAction.sActionName);
            }, true);
            elAction.dataset.actionName = dictAction.sActionName;
            elButtons.appendChild(elAction);
        });
    }

    function _fnRenderPlan(dictPlan) {
        var elBody = _elPart("runtime-setup-body");
        elBody.innerHTML = "";
        var listSteps = dictPlan.listSteps || [];
        var dictStep = listSteps[0] || null;
        if (dictPlan.bReady) {
            elBody.appendChild(_elMake("p", "runtime-setup-ready",
                "Docker is ready for vaibify." +
                (dictStep ? " One optional step remains:" : "")));
        }
        if (dictStep) elBody.appendChild(_elStepCard(dictStep));
        if (listSteps.length > 1) {
            elBody.appendChild(_elLaterSteps(listSteps.slice(1)));
        }
        _fnRenderButtons(dictPlan, dictStep);
        _dictLastStepShown = dictStep;
    }

    async function _fnReadAndRenderPlan() {
        _fnShowMessage("Reading this machine…", "");
        _fnSetBusy(true);
        try {
            var dictPlan = await VaibifyApi.fdictGet("/api/runtime/setup");
            var dictPrevious = _dictLastStepShown;
            _fnShowMessage("", "");
            _fnRenderPlan(dictPlan);
            return {dictPlan: dictPlan, dictPrevious: dictPrevious};
        } catch (error) {
            _fnShowMessage(VaibifyDiagnosis.fsExplainError(error), "error");
            _fnRenderButtons({}, null);
            return null;
        } finally {
            _fnSetBusy(false);
        }
    }

    async function _fnContinue() {
        var dictRead = await _fnReadAndRenderPlan();
        if (!dictRead || !dictRead.dictPrevious) return;
        var dictNext = (dictRead.dictPlan.listSteps || [])[0];
        if (dictNext && dictNext.sStepName === dictRead.dictPrevious.sStepName) {
            _fnShowMessage("vaibify still finds this step needed. Do it, " +
                "then press Continue again.", "warning");
        }
    }

    function _fdictRequestedSettings(sActionName) {
        var dictBody = {sActionName: sActionName};
        var elCpus = document.getElementById("inputRuntimeSetupCpus");
        var elMemory = document.getElementById("inputRuntimeSetupMemory");
        if (elCpus) dictBody.iCpus = parseInt(elCpus.value, 10);
        if (elMemory) dictBody.iMemoryGigabytes = parseInt(elMemory.value, 10);
        return dictBody;
    }

    function _fnRenderTail(dictProgress) {
        var elTail = _elPart("runtime-setup-tail");
        var listLines = dictProgress.listTail || [];
        if (!listLines.length) return;
        elTail.style.display = "block";
        elTail.textContent = listLines.join("\n");
        elTail.scrollTop = elTail.scrollHeight;
    }

    async function _fdictReadProgress() {
        return VaibifyApi.fdictGet("/api/runtime/setup/progress");
    }

    function _fnStartProgressPoll() {
        _fnStopProgressPoll();
        _iProgressTimer = setInterval(async function () {
            try {
                _fnRenderTail(await _fdictReadProgress());
            } catch (error) {
                /* A missed poll must not disturb the step; the POST
                   owns reporting how it ended. */
            }
        }, I_PROGRESS_POLL_MILLISECONDS);
    }

    function _fnStopProgressPoll() {
        if (_iProgressTimer !== null) {
            clearInterval(_iProgressTimer);
            _iProgressTimer = null;
        }
    }

    /* A dropped connection is not a failed step: the step outlives the
       request, as a build does, so wait for the log to stop and read
       the machine again. */
    async function _fnWaitForTheStepToFinish() {
        for (;;) {
            var dictProgress = await _fdictReadProgress();
            _fnRenderTail(dictProgress);
            if (!dictProgress.bLive) return dictProgress;
            await new Promise(function (fnResolve) {
                setTimeout(fnResolve, I_PROGRESS_POLL_MILLISECONDS);
            });
        }
    }

    async function _fnRunAction(sActionName) {
        var sTitle = _dictLastStepShown ? _dictLastStepShown.sTitle : "";
        _fnShowMessage("Running: " + sTitle + "…", "");
        _fnSetBusy(true);
        _fnStartProgressPoll();
        try {
            var dictPlan = await VaibifyApi.fdictPost(
                "/api/runtime/setup/run", _fdictRequestedSettings(sActionName));
            _fnRenderTail(await _fdictReadProgress());
            _fnShowMessage(sTitle + ": done.", "success");
            _fnRenderPlan(dictPlan);
        } catch (error) {
            if (error && error.sKind === "network") {
                await _fnRecoverFromDroppedRequest();
            } else {
                _fnShowMessage(VaibifyDiagnosis.fsExplainError(error), "error");
            }
        } finally {
            _fnStopProgressPoll();
            _fnSetBusy(false);
        }
    }

    async function _fnRecoverFromDroppedRequest() {
        try {
            var dictProgress = await _fnWaitForTheStepToFinish();
            await _fnReadAndRenderPlan();
            _fnShowMessage("The connection dropped while the step ran; it " +
                "ended '" + (dictProgress.sOutcome || "unknown") + "'.",
                dictProgress.sOutcome === "succeeded" ? "success" : "warning");
        } catch (errorAgain) {
            _fnShowMessage(VaibifyDiagnosis.fsExplainError(errorAgain),
                "error");
        }
    }

    /* With Docker answering, the banner is otherwise hidden; this shows
       it again when the hub's plan still has a step (buildx missing, a
       Colima machine too small to build in), so the wizard stays
       reachable. The check reads the plan without asking sudo, which
       logs a failure on every page load otherwise. A failed check
       claims nothing: Docker itself has just answered. */
    async function fnShowAdviceInBanner(elBanner) {
        var dictPlan;
        try {
            dictPlan = await VaibifyApi.fdictGet(
                "/api/runtime/setup?bAdvisory=true");
        } catch (error) {
            return;
        }
        var listSteps = dictPlan.listSteps || [];
        if (!listSteps.length || elBanner.style.display === "flex") return;
        elBanner.innerHTML = "";
        var elMessage = _elMake("div", "docker-status-banner-message");
        elMessage.appendChild(_elMake("strong", "", dictPlan.bReady
            ? "Docker works, and one setup step is still open."
            : "Docker answers, but is not ready for vaibify."));
        elMessage.appendChild(document.createTextNode(
            " " + listSteps[0].sTitle + ": " + listSteps[0].sExplanation));
        var elActions = _elMake("div", "docker-status-banner-actions");
        elActions.appendChild(_elButton("Set up Docker", "btnSetupDocker",
            fnOpen, false));
        elBanner.appendChild(elMessage);
        elBanner.appendChild(elActions);
        elBanner.classList.add("docker-status-banner-advice");
        elBanner.style.display = "flex";
    }

    async function fnOpen() {
        _dictLastStepShown = null;
        var elTail = _elPart("runtime-setup-tail");
        elTail.textContent = "";
        elTail.style.display = "none";
        _elOverlay().style.display = "flex";
        await _fnReadAndRenderPlan();
    }

    function fnClose() {
        _fnStopProgressPoll();
        _elOverlay().style.display = "none";
        if (typeof VaibifyContainerManager !== "undefined" &&
                VaibifyContainerManager.fnRetryDockerStatus) {
            VaibifyContainerManager.fnRetryDockerStatus();
        }
    }

    return {
        fnOpen: fnOpen,
        fnClose: fnClose,
        fnShowAdviceInBanner: fnShowAdviceInBanner,
    };
})();
