/* Vaibify — Plot standardization (extracted from scriptApplication.js) */

var PipeleyenPlotStandards = (function () {
    "use strict";

    async function fnLoadPlotStandardStatus(iStepIndex) {
        var sContainerId = PipeleyenApp.fsGetContainerId();
        var dictWorkflow = PipeleyenApp.fdictGetWorkflow();
        if (!sContainerId || !dictWorkflow) return;
        var step = dictWorkflow.listSteps[iStepIndex];
        if (!step || (step.saPlotFiles || []).length === 0) return;
        try {
            var dictResult = await VaibifyApi.fdictGet(
                "/api/steps/" + sContainerId + "/" +
                iStepIndex + "/plot-standards"
            );
            var dictStandards = dictResult.dictStandards || {};
            for (var sBasename in dictStandards) {
                var sKey = iStepIndex + ":" + sBasename;
                PipeleyenApp.fnSetPlotStandardExists(
                    sKey, dictStandards[sBasename]);
            }
            PipeleyenApp.fnRenderStepList();
        } catch (error) {
            /* Silently ignore - buttons remain hidden */
        }
    }

    function fnStandardizeAllPlots(iStepIndex) {
        var sContainerId = PipeleyenApp.fsGetContainerId();
        if (!sContainerId) return;
        var sMessage = _fsBuildStandardizeMessage(iStepIndex);
        PipeleyenApp.fnShowConfirmModal(
            "Make Standard",
            sMessage,
            function () {
                fnExecuteStandardizeAllPlots(iStepIndex);
            }
        );
    }

    function _fsBuildStandardizeMessage(iStepIndex) {
        var sBase = "Convert all plots in this step to standard " +
            "PNGs? This will overwrite any existing standards.";
        var sExtra = _fsBuildRemoteWarning(iStepIndex);
        return sExtra ? sBase + "\n\n" + sExtra : sBase;
    }

    function _fsBuildRemoteWarning(iStepIndex) {
        var dictWorkflow = PipeleyenApp.fdictGetWorkflow();
        if (!dictWorkflow) return "";
        var step = (dictWorkflow.listSteps || [])[iStepIndex];
        if (!step) return "";
        var iOverleaf = _fiCountTrackedPlots(
            dictWorkflow, step, "bOverleaf");
        var iZenodo = _fiCountTrackedPlots(
            dictWorkflow, step, "bZenodo");
        var listLines = [];
        if (iOverleaf > 0 && dictWorkflow.sOverleafProjectId) {
            listLines.push(
                "Overleaf project " +
                dictWorkflow.sOverleafProjectId +
                " tracks " + iOverleaf +
                " of these plots — the next sync will replace " +
                "what is currently there.");
        }
        if (iZenodo > 0 && dictWorkflow.sZenodoDepositionId) {
            listLines.push(
                "Zenodo deposit " +
                dictWorkflow.sZenodoDepositionId +
                " tracks " + iZenodo +
                " of these plots — the next archive will replace " +
                "what is currently there.");
        }
        return listLines.join("\n\n");
    }

    function _fiCountTrackedPlots(dictWorkflow, step, sFlagKey) {
        var dictSyncStatus = dictWorkflow.dictSyncStatus || {};
        var listPlots = step.saPlotFiles || [];
        var sStepDir = step.sDirectory || "";
        var iCount = 0;
        for (var i = 0; i < listPlots.length; i++) {
            var sKey = _fsRepoRelPath(sStepDir, listPlots[i]);
            var dictEntry = dictSyncStatus[sKey];
            if (dictEntry && dictEntry[sFlagKey]) iCount += 1;
        }
        return iCount;
    }

    function _fsRepoRelPath(sStepDir, sPlotFile) {
        if (!sStepDir) return sPlotFile;
        var sNormalized = sStepDir.replace(/^\/+|\/+$/g, "");
        return sNormalized + "/" + sPlotFile;
    }

    async function fnExecuteStandardizeAllPlots(iStepIndex) {
        var sContainerId = PipeleyenApp.fsGetContainerId();
        PipeleyenApp.fnShowToast(
            "Standardizing plots\u2026", "success");
        try {
            var dictResult = await VaibifyApi.fdictPost(
                "/api/steps/" + sContainerId + "/" +
                iStepIndex + "/standardize-plots",
                {}
            );
            fnApplyStandardizeResult(iStepIndex, dictResult);
        } catch (error) {
            PipeleyenApp.fnShowToast(
                VaibifyUtilities.fsSanitizeErrorForUser(error.message),
                "error");
        }
    }

    function fnApplyStandardizeResult(iStepIndex, dictResult) {
        var dictWorkflow = PipeleyenApp.fdictGetWorkflow();
        var step = dictWorkflow.listSteps[iStepIndex];
        if (!step) return;
        step.dictVerification = step.dictVerification || {};
        step.dictVerification.sLastStandardized =
            dictResult.sTimestamp ||
            VaibifyUtilities.fsFormatUtcTimestamp();
        var listBasenames =
            dictResult.listStandardizedBasenames || [];
        fnMarkStandardsExist(iStepIndex, listBasenames);
        PipeleyenApp.fnSaveStepUpdate(iStepIndex, {
            dictVerification: step.dictVerification,
        });
        PipeleyenApp.fnRenderStepList();
        PipeleyenApp.fnShowToast("Plot standards saved", "success");
    }

    function fnMarkStandardsExist(iStepIndex, listBasenames) {
        for (var i = 0; i < listBasenames.length; i++) {
            var sKey = iStepIndex + ":" + listBasenames[i];
            PipeleyenApp.fnSetPlotStandardExists(sKey, true);
        }
    }

    async function fnComparePlotToStandard(iStepIndex, sFileName) {
        var sContainerId = PipeleyenApp.fsGetContainerId();
        if (!sContainerId) return;
        try {
            var dictResult = await VaibifyApi.fdictPost(
                "/api/steps/" + sContainerId + "/" +
                iStepIndex + "/compare-plot",
                {sFileName: sFileName}
            );
            if (dictResult.sPlotPath && dictResult.sStandardPath) {
                PipeleyenFigureViewer.fnDisplayFileFromContainer(
                    dictResult.sPlotPath);
                PipeleyenFigureViewer.fnDisplayFileFromContainer(
                    dictResult.sStandardPath);
            } else if (dictResult.sStandardPath) {
                PipeleyenFigureViewer.fnDisplayFileFromContainer(
                    dictResult.sStandardPath);
            } else {
                PipeleyenApp.fnShowToast(
                    "No standard found for " + sFileName,
                    "error");
            }
        } catch (error) {
            PipeleyenApp.fnShowToast(
                VaibifyUtilities.fsSanitizeErrorForUser(error.message),
                "error");
        }
    }

    async function fnCompareStepPlots(iStepIndex) {
        if (!fbStepHasAnyStandard(iStepIndex)) {
            PipeleyenApp.fnShowToast(
                "No standards found. " +
                "Use \u2018Make Standard\u2019 first.",
                "error");
            return;
        }
        var sBasename = fsFirstPlotBasename(iStepIndex);
        if (sBasename) {
            await fnComparePlotToStandard(
                iStepIndex, sBasename);
        }
    }

    function fbStepHasAnyStandard(iStepIndex) {
        var dictWorkflow = PipeleyenApp.fdictGetWorkflow();
        var dictStep = dictWorkflow.listSteps[iStepIndex];
        var listPlots = dictStep.saPlotFiles || [];
        var dictVars = PipeleyenApp.fdictBuildClientVariables();
        for (var i = 0; i < listPlots.length; i++) {
            var sResolved = VaibifyUtilities.fsResolveTemplate(
                listPlots[i], dictVars);
            var sBasename = sResolved.split("/").pop();
            var sKey = iStepIndex + ":" + sBasename;
            if (PipeleyenApp.fbGetPlotStandardExists(sKey) === true) {
                return true;
            }
        }
        return false;
    }

    function fsFirstPlotBasename(iStepIndex) {
        var dictWorkflow = PipeleyenApp.fdictGetWorkflow();
        var dictStep = dictWorkflow.listSteps[iStepIndex];
        var listPlots = dictStep.saPlotFiles || [];
        if (listPlots.length === 0) return null;
        var dictVars = PipeleyenApp.fdictBuildClientVariables();
        var sResolved = VaibifyUtilities.fsResolveTemplate(
            listPlots[0], dictVars);
        return sResolved.split("/").pop();
    }

    function fiCountStepsWithPlots() {
        var dictWorkflow = PipeleyenApp.fdictGetWorkflow();
        var listSteps = (dictWorkflow || {}).listSteps || [];
        var iCount = 0;
        for (var i = 0; i < listSteps.length; i++) {
            if ((listSteps[i].saPlotFiles || []).length > 0) {
                iCount++;
            }
        }
        return iCount;
    }

    async function fnStandardizeAllWorkflowPlots() {
        var sContainerId = PipeleyenApp.fsGetContainerId();
        var dictWorkflow = PipeleyenApp.fdictGetWorkflow();
        if (!sContainerId || !dictWorkflow) return;
        var iCount = fiCountStepsWithPlots();
        if (iCount === 0) {
            PipeleyenApp.fnShowToast(
                "No steps have plot files", "error");
            return;
        }
        PipeleyenApp.fnShowConfirmModal(
            "Standardize All Plots",
            "Create plot standards for " + iCount +
            " step(s)? This will overwrite existing standards.",
            async function () {
                await fnStandardizeEachStep();
            }
        );
    }

    async function fnStandardizeEachStep() {
        var dictWorkflow = PipeleyenApp.fdictGetWorkflow();
        var listSteps = dictWorkflow.listSteps || [];
        for (var i = 0; i < listSteps.length; i++) {
            if ((listSteps[i].saPlotFiles || []).length > 0) {
                await fnStandardizeAllPlots(i);
            }
        }
        PipeleyenApp.fnShowToast(
            "All plot standards updated", "success");
    }

    return {
        fnLoadPlotStandardStatus: fnLoadPlotStandardStatus,
        fnStandardizeAllPlots: fnStandardizeAllPlots,
        fnCompareStepPlots: fnCompareStepPlots,
        fnStandardizeAllWorkflowPlots: fnStandardizeAllWorkflowPlots,
        fbStepHasAnyStandard: fbStepHasAnyStandard,
        fsFirstPlotBasename: fsFirstPlotBasename,
    };
})();
