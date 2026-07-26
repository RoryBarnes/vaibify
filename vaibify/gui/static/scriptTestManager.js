/* Vaibify — Test generation, running, and state (extracted from scriptApplication.js) */

var VaibifyTestManager = (function () {
    "use strict";

    var setExpandedUnitTests = new Set();
    var setGeneratingInFlight = new Set();
    var setGeneratedTestsPending = new Set();
    var setStepsWithData = new Set();
    var dictTestMarkerTimestamps = {};
    var _dictSeenNewTestFiles = {};
    var _bFirstTestFilePoll = true;
    var _dictFalsificationByStep = {};
    var _setFalsificationFetchInFlight = new Set();

    /* --- Test Generation --- */

    async function fnGenerateTests(iStep) {
        var dictWorkflow = VaibifyApp.fdictGetWorkflow();
        var step = dictWorkflow.listSteps[iStep];
        if (setGeneratingInFlight.has(iStep)) return;
        if (step && (step.saTestCommands || []).length > 0) {
            var bConfirmed = await new Promise(function (resolve) {
                VaibifyApp.fnShowConfirmModal(
                    "Replace Tests",
                    "Tests already exist for this step. " +
                    "Replacing them will overwrite the " +
                    "existing test files.",
                    function () { resolve(true); },
                    function () { resolve(false); }
                );
            });
            if (!bConfirmed) return;
        }
        setGeneratingInFlight.add(iStep);
        VaibifyApp.fnRenderStepList();
        try {
            var sContainerId = VaibifyApp.fsGetContainerId();
            var dictResult = await VaibifyApi.fdictPost(
                "/api/steps/" + sContainerId + "/" + iStep +
                "/generate-test",
                {}
            );
            setGeneratingInFlight.delete(iStep);
            if (dictResult.bNeedsFallback) {
                VaibifyApp.fnRenderStepList();
                VaibifyApp.fnShowConfirmModal(
                    "Claude Code Not Found",
                    "Test generation requires Claude Code, " +
                    "which is not installed in this container. " +
                    "You can use the Anthropic API instead " +
                    "(requires an API key, may incur charges).",
                    function () { fnShowApiKeyDialog(iStep); }
                );
                return;
            }
            if (dictResult.bNeedsOverwriteConfirm) {
                VaibifyApp.fnRenderStepList();
                var listFiles = dictResult.listModifiedFiles || [];
                VaibifyApp.fnShowConfirmModal(
                    "Custom Test Files Detected",
                    "The following test files have been " +
                    "customized and will be overwritten:\n\n" +
                    listFiles.join("\n") +
                    "\n\nProceed with overwrite?",
                    function () {
                        fnGenerateTestsForced(iStep);
                    }
                );
                return;
            }
            if (!dictResult.bGenerated) {
                VaibifyApp.fnRenderStepList();
                VaibifyApp.fnShowToast("No tests generated", "error");
                VaibifyApp.fnShowErrorModal(
                    "Test generation failed:\n\n" +
                    (dictResult.sMessage || "No tests generated")
                );
                return;
            }
            fnHandleGeneratedTest(iStep, dictResult);
        } catch (error) {
            setGeneratingInFlight.delete(iStep);
            VaibifyApp.fnRenderStepList();
            VaibifyApp.fnShowErrorModal(
                "Test generation failed:\n\n" +
                (error.message || String(error))
            );
        }
    }

    async function fnGenerateTestsForced(iStep) {
        setGeneratingInFlight.add(iStep);
        VaibifyApp.fnRenderStepList();
        try {
            var sContainerId = VaibifyApp.fsGetContainerId();
            var dictResult = await VaibifyApi.fdictPost(
                "/api/steps/" + sContainerId + "/" + iStep +
                "/generate-test",
                {bForceOverwrite: true}
            );
            setGeneratingInFlight.delete(iStep);
            fnHandleGeneratedTest(iStep, dictResult);
        } catch (error) {
            setGeneratingInFlight.delete(iStep);
            VaibifyApp.fnRenderStepList();
            VaibifyApp.fnShowErrorModal(
                "Test generation failed:\n\n" +
                (error.message || String(error))
            );
        }
    }

    function fnHandleGeneratedTest(iStep, dictResult) {
        var dictWorkflow = VaibifyApp.fdictGetWorkflow();
        var dictStep = dictWorkflow.listSteps[iStep];
        if (!dictStep.dictTests) {
            dictStep.dictTests = VaibifyApp.fdictGetTests(dictStep);
        }
        if (!dictStep.dictVerification) {
            dictStep.dictVerification = {
                sUnitTest: "untested", sUser: "untested",
            };
        }
        var listCategories = [
            "qualitative", "quantitative", "integrity"];
        var listAllCommands = [];
        var listErrors = [];
        for (var i = 0; i < listCategories.length; i++) {
            var sCategory = listCategories[i];
            var sCatKey = "dict" +
                sCategory.charAt(0).toUpperCase() +
                sCategory.slice(1);
            var dictCatResult = dictResult[sCatKey];
            if (!dictCatResult) continue;
            if (dictCatResult.sError) {
                listErrors.push(
                    VaibifyUtilities.fsTestCategoryLabel(sCategory));
                var sVerifKey = "s" +
                    sCategory.charAt(0).toUpperCase() +
                    sCategory.slice(1);
                dictStep.dictVerification[sVerifKey] = "error";
                continue;
            }
            dictStep.dictTests[sCatKey] = {
                saCommands: dictCatResult.saCommands || [],
                sFilePath: dictCatResult.sFilePath || "",
            };
            if (dictCatResult.sStandardsPath) {
                dictStep.dictTests[sCatKey].sStandardsPath =
                    dictCatResult.sStandardsPath;
            }
            listAllCommands = listAllCommands.concat(
                dictCatResult.saCommands || []);
        }
        dictStep.saTestCommands = listAllCommands;
        VaibifyApp.fnSaveStepUpdate(iStep, {
            dictTests: dictStep.dictTests,
            dictVerification: dictStep.dictVerification,
            saTestCommands: listAllCommands,
        });
        if (!setExpandedUnitTests.has(iStep)) {
            setExpandedUnitTests.add(iStep);
        }
        VaibifyApp.fnRenderStepList();
        var iSuccessCount = listCategories.length - listErrors.length;
        var sStepLabel = VaibifyApp.fsComputeStepLabel(iStep);
        if (listErrors.length > 0) {
            VaibifyApp.fnShowErrorModal(
                listErrors.join(", ") +
                " failed to generate.\n" +
                "Fix manually or ask a coding agent to fix.");
        }
        VaibifyApp.fnShowToast(
            sStepLabel + ": " + iSuccessCount +
            " of 3 test categories generated. Running\u2026",
            iSuccessCount === 3 ? "success" : "error");
        fnNotifyStochasticity(sStepLabel, dictResult);
        if (listAllCommands.length > 0) {
            fnRunStepTests(iStep);
        }
    }

    function fnNotifyStochasticity(sStepLabel, dictResult) {
        var dictQ = dictResult && dictResult.dictQuantitative;
        if (!dictQ) return;
        var sClass = dictQ.sStochasticityClassification || "";
        if (sClass === "stochastic") {
            VaibifyApp.fnShowToast(
                sStepLabel + ": stochastic outputs detected. " +
                "Standards use distributional metrics " +
                "(Mean, P5, P25, P50, P75, P95, std).",
                "info");
        } else if (sClass === "stochastic_unseeded") {
            VaibifyApp.fnShowToast(
                sStepLabel + ": stochastic outputs without a fixed " +
                "seed. Tolerance is a placeholder \u2014 seed the " +
                "source of randomness, then regenerate.",
                "warning");
        } else if (sClass === "unintrospectable") {
            VaibifyApp.fnShowToast(
                sStepLabel + ": introspector could not parse this " +
                "step's outputs. Standards came from the LLM " +
                "fallback \u2014 review them carefully.",
                "warning");
        }
    }

    function fnShowApiKeyDialog(iStep) {
        var elModal = document.getElementById("modalApiConfirm");
        elModal.style.display = "flex";
        elModal.dataset.step = iStep;
    }

    function fnFinalizeGeneratedTest(iStep) {
        setGeneratedTestsPending.delete(iStep);
        var dictWorkflow = VaibifyApp.fdictGetWorkflow();
        var dictStep = dictWorkflow.listSteps[iStep];
        if (!dictStep.dictVerification) {
            dictStep.dictVerification = {
                sUnitTest: "untested", sUser: "untested",
            };
        }
        dictStep.dictVerification.sQualitative = "untested";
        dictStep.dictVerification.sQuantitative = "untested";
        dictStep.dictVerification.sIntegrity = "untested";
        dictStep.dictVerification.sUnitTest = "untested";
        VaibifyApp.fnSaveStepUpdate(iStep, {
            dictTests: dictStep.dictTests,
            dictVerification: dictStep.dictVerification,
        });
        VaibifyApp.fnRenderStepList();
    }

    async function fnCancelGeneratedTest(iStep) {
        setGeneratedTestsPending.delete(iStep);
        var sContainerId = VaibifyApp.fsGetContainerId();
        try {
            await VaibifyApi.fnDelete(
                "/api/steps/" + sContainerId + "/" + iStep +
                "/generated-test"
            );
        } catch (error) {
            VaibifyApp.fnShowToast("Delete failed", "error");
        }
        var dictWorkflow = VaibifyApp.fdictGetWorkflow();
        var dictStep = dictWorkflow.listSteps[iStep];
        dictStep.saTestCommands = [];
        dictStep.saTestFiles = [];
        dictStep.dictTests = {
            dictQualitative: {saCommands: [], sFilePath: ""},
            dictQuantitative: {
                saCommands: [], sFilePath: "", sStandardsPath: "",
            },
            dictIntegrity: {saCommands: [], sFilePath: ""},
            listUserTests: [],
        };
        VaibifyApp.fnRenderStepList();
    }

    async function fnGenerateTestsWithApi(iStep, sApiKey) {
        VaibifyApp.fnShowToast(
            "Generating tests via API...", "success");
        var sContainerId = VaibifyApp.fsGetContainerId();
        try {
            var dictResult = await VaibifyApi.fdictPost(
                "/api/steps/" + sContainerId + "/" + iStep +
                "/generate-test",
                {bUseApi: true, sApiKey: sApiKey}
            );
            fnHandleGeneratedTest(iStep, dictResult);
        } catch (error) {
            VaibifyApp.fnShowToast(
                VaibifyUtilities.fsSanitizeErrorForUser(error.message),
                "error");
        }
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
                    VaibifyApp.fnShowToast(
                        "API key is required", "error");
                    return;
                }
                elModal.style.display = "none";
                fnGenerateTestsWithApi(iStep, sApiKey);
            }
        );
    }

    /* --- Test Running --- */

    async function fnRunCategoryTests(iStepIndex, sCategory) {
        var sContainerId = VaibifyApp.fsGetContainerId();
        try {
            var dictResult = await VaibifyApi.fdictPost(
                "/api/steps/" + sContainerId + "/" + iStepIndex +
                "/run-test-category",
                {sCategory: sCategory}
            );
            fnUpdateCategoryTestState(
                iStepIndex, sCategory, dictResult);
        } catch (error) {
            VaibifyApp.fnShowErrorModal(
                "Test run failed: " + error.message);
        }
    }

    /* --- Falsification attestation (non-gating) --- */

    function fdictGetFalsificationState(iStepIndex) {
        return _dictFalsificationByStep[iStepIndex] || null;
    }

    async function fnFetchFalsificationState(iStepIndex) {
        if (_setFalsificationFetchInFlight.has(iStepIndex)) return;
        var sContainerId = VaibifyApp.fsGetContainerId();
        if (!sContainerId) return;
        _setFalsificationFetchInFlight.add(iStepIndex);
        try {
            var dictResult = await VaibifyApi.fdictGet(
                "/api/steps/" + sContainerId + "/" + iStepIndex +
                "/falsification"
            );
            _dictFalsificationByStep[iStepIndex] = dictResult;
            VaibifyApp.fnRenderStepList();
        } catch (error) {
            VaibifyApp.fnShowToast(
                "Falsification status fetch failed: " +
                error.message, "error");
        } finally {
            _setFalsificationFetchInFlight.delete(iStepIndex);
        }
    }

    async function fnRunFalsification(iStepIndex) {
        var sContainerId = VaibifyApp.fsGetContainerId();
        if (!sContainerId) return;
        try {
            await VaibifyApi.fdictPostRaw(
                "/api/steps/" + sContainerId + "/" + iStepIndex +
                "/run-falsification"
            );
        } catch (error) {
            VaibifyApp.fnShowErrorModal(
                "Falsification check refused: " + error.message);
            return;
        }
        var dictState = _dictFalsificationByStep[iStepIndex] || {};
        dictState.dictInFlight = {sPhase: "starting"};
        _dictFalsificationByStep[iStepIndex] = dictState;
        VaibifyApp.fnRenderStepList();
        _fnPollFalsificationUntilDone(iStepIndex);
    }

    function _fnPollFalsificationUntilDone(iStepIndex) {
        // Transient completion poll: alive only while a run this
        // session started is in flight, then stops. Not a standing
        // poll loop.
        setTimeout(async function () {
            await fnFetchFalsificationState(iStepIndex);
            var dictState = _dictFalsificationByStep[iStepIndex];
            if (dictState && dictState.dictInFlight) {
                _fnPollFalsificationUntilDone(iStepIndex);
            }
        }, 5000);
    }

    async function fnRunStepTests(iStepIndex) {
        var sContainerId = VaibifyApp.fsGetContainerId();
        if (!sContainerId) return;
        var dictWorkflow = VaibifyApp.fdictGetWorkflow();
        var step = dictWorkflow.listSteps[iStepIndex];
        if (!step || !step.saTestCommands ||
            step.saTestCommands.length === 0) return;
        VaibifyApp.fnShowToast("Running tests for Step " +
            (iStepIndex + 1) + "...", "success");
        try {
            var dictResult = await VaibifyApi.fdictPostRaw(
                "/api/steps/" + sContainerId + "/" +
                iStepIndex + "/run-tests"
            );
            step.dictVerification = step.dictVerification || {};
            fnApplyCategoryResults(
                step, dictResult.dictCategoryResults);
            step.dictVerification.sUnitTest =
                dictResult.bPassed ? "passed" : "failed";
            VaibifyApp.fnClearOutputModified(iStepIndex);
            var dictStepUpdate = {
                dictVerification: step.dictVerification,
            };
            if (step.dictTests) {
                dictStepUpdate.dictTests = step.dictTests;
            }
            VaibifyApp.fnSaveStepUpdate(iStepIndex, dictStepUpdate);
            VaibifyApp.fnRenderStepList();
            VaibifyApp.fnUpdateHighlightState();
            var sOutput = fsCollectTestOutput(dictResult);
            VaibifyApp.fnShowToast(
                dictResult.bPassed ?
                    "Tests passed" : "Tests FAILED",
                dictResult.bPassed ? "success" : "error"
            );
            VaibifyFigureViewer.fnDisplayTestOutput(
                sOutput, dictResult.bPassed);
        } catch (error) {
            VaibifyApp.fnShowToast(
                VaibifyUtilities.fsSanitizeErrorForUser(error.message),
                "error");
        }
    }

    function fnApplyCategoryResults(step, dictCategoryResults) {
        if (!dictCategoryResults) return;
        var dictTests = step.dictTests || {};
        var dictMap = {
            dictIntegrity: "sIntegrity",
            dictQualitative: "sQualitative",
            dictQuantitative: "sQuantitative",
        };
        for (var sKey in dictMap) {
            var dictCat = dictCategoryResults[sKey];
            if (dictCat) {
                step.dictVerification[dictMap[sKey]] =
                    dictCat.bPassed ? "passed" : "failed";
                if (dictTests[sKey]) {
                    dictTests[sKey].sLastOutput =
                        dictCat.sOutput || "";
                }
            }
        }
    }

    function fsCollectTestOutput(dictResult) {
        var listParts = [];
        var dictCats = dictResult.dictCategoryResults || {};
        for (var sKey in dictCats) {
            var sOutput = (dictCats[sKey].sOutput || "").trim();
            if (sOutput) listParts.push(sOutput);
        }
        if (listParts.length > 0) return listParts.join("\n\n");
        return dictResult.sOutput || "(no output)";
    }

    /* --- Test State --- */

    function fnUpdateCategoryTestState(
        iStepIndex, sCategory, dictResult
    ) {
        var dictWorkflow = VaibifyApp.fdictGetWorkflow();
        var dictStep = dictWorkflow.listSteps[iStepIndex];
        if (!dictStep.dictVerification) {
            dictStep.dictVerification = {
                sUnitTest: "untested", sUser: "untested",
            };
        }
        var sKey = "s" + sCategory.charAt(0).toUpperCase() +
            sCategory.slice(1);
        dictStep.dictVerification[sKey] =
            dictResult.bPassed ? "passed" : "failed";
        var sCatKey = "dict" + sCategory.charAt(0).toUpperCase() +
            sCategory.slice(1);
        var dictTests = dictStep.dictTests || {};
        if (dictTests[sCatKey] && dictResult.sOutput) {
            dictTests[sCatKey].sLastOutput = dictResult.sOutput;
        }
        fnComputeAggregateTestState(iStepIndex);
        VaibifyApp.fnClearOutputModified(iStepIndex);
        var dictCatUpdate = {
            dictVerification: dictStep.dictVerification,
        };
        if (dictStep.dictTests) {
            dictCatUpdate.dictTests = dictStep.dictTests;
        }
        VaibifyApp.fnSaveStepUpdate(iStepIndex, dictCatUpdate);
        VaibifyApp.fnRenderStepList();
        var sLabel = VaibifyUtilities.fsTestCategoryLabel(sCategory);
        VaibifyApp.fnShowToast(sLabel + ": " +
            (dictResult.bPassed ? "Passed" : "Failed"),
            dictResult.bPassed ? "success" : "error");
    }

    function fnComputeAggregateTestState(iStepIndex) {
        var dictWorkflow = VaibifyApp.fdictGetWorkflow();
        var dictStep = dictWorkflow.listSteps[iStepIndex];
        var dictVerify = VaibifyApp.fdictGetVerification(dictStep);
        var dictTests = VaibifyApp.fdictGetTests(dictStep);
        var listCategories = [
            "qualitative", "quantitative", "integrity"];
        var bAllPassed = true;
        var bAnyFailed = false;
        for (var i = 0; i < listCategories.length; i++) {
            var sCategory = listCategories[i];
            var sCatKey = "dict" +
                sCategory.charAt(0).toUpperCase() +
                sCategory.slice(1);
            var dictCat = dictTests[sCatKey] || {};
            if ((dictCat.saCommands || []).length === 0) continue;
            var sKey = "s" +
                sCategory.charAt(0).toUpperCase() +
                sCategory.slice(1);
            var sCatState = dictVerify[sKey] || "untested";
            if (sCatState !== "passed") bAllPassed = false;
            if (sCatState === "failed" || sCatState === "error") {
                bAnyFailed = true;
            }
        }
        if (bAnyFailed) {
            dictVerify.sUnitTest = "failed";
        } else if (bAllPassed) {
            dictVerify.sUnitTest = "passed";
        } else {
            dictVerify.sUnitTest = "untested";
        }
    }

    function fnApplyTestResultToCategories(
        dictStep, sResult, sOutput, dictCategoryResults
    ) {
        var dictVerify = dictStep.dictVerification;
        var dictTests = dictStep.dictTests || {};
        var listKeys = [
            ["dictIntegrity", "sIntegrity"],
            ["dictQualitative", "sQualitative"],
            ["dictQuantitative", "sQuantitative"],
        ];
        for (var i = 0; i < listKeys.length; i++) {
            var sCatKey = listKeys[i][0];
            var sVerifyKey = listKeys[i][1];
            var dictCat = dictTests[sCatKey] || {};
            if ((dictCat.saCommands || []).length === 0) continue;
            var dictCatEntry = (dictCategoryResults || {})[sCatKey];
            if (dictCatEntry && typeof dictCatEntry === "object") {
                dictVerify[sVerifyKey] = dictCatEntry.sStatus || sResult;
                if (dictCatEntry.sOutput) {
                    dictCat.sLastOutput = dictCatEntry.sOutput;
                }
            } else if (typeof dictCatEntry === "string") {
                dictVerify[sVerifyKey] = dictCatEntry;
                if (sOutput) {
                    dictCat.sLastOutput = sOutput;
                }
            } else {
                dictVerify[sVerifyKey] = sResult;
                if (sOutput) {
                    dictCat.sLastOutput = sOutput;
                }
            }
        }
    }

    function fnHandleTestResult(dictEvent) {
        var dictWorkflow = VaibifyApp.fdictGetWorkflow();
        var iStep = dictEvent.iStepNumber - 1;
        var dictStep = dictWorkflow.listSteps[iStep];
        if (!dictStep.dictVerification) {
            dictStep.dictVerification = {
                sUnitTest: "untested", sUser: "untested",
            };
        }
        dictStep.dictVerification.sUnitTest = dictEvent.sResult;
        fnApplyTestResultToCategories(
            dictStep, dictEvent.sResult, dictEvent.sOutput || "",
            dictEvent.dictCategoryResults || null);
        VaibifyApp.fnClearOutputModified(iStep);
        var dictUpdate = {
            dictVerification: dictStep.dictVerification,
        };
        if (dictStep.dictTests) {
            dictUpdate.dictTests = dictStep.dictTests;
        }
        VaibifyApp.fnSaveStepUpdate(iStep, dictUpdate);
        VaibifyApp.fnRenderStepList();
        VaibifyApp.fnUpdateHighlightState();
        var sLabel = dictEvent.sResult === "passed" ?
            "Tests passed" : "Tests FAILED";
        var sStepLabel = VaibifyApp.fsComputeStepLabel(iStep);
        VaibifyApp.fnShowToast("Step " + sStepLabel + ": " + sLabel,
            dictEvent.sResult === "passed" ? "success" : "error");
    }

    /* --- Test UI --- */

    function fnViewCategoryTestFile(iStepIndex, sCategory) {
        var dictWorkflow = VaibifyApp.fdictGetWorkflow();
        var dictStep = dictWorkflow.listSteps[iStepIndex];
        var dictTests = VaibifyApp.fdictGetTests(dictStep);
        var sCatKey = "dict" + sCategory.charAt(0).toUpperCase() +
            sCategory.slice(1);
        var dictCat = dictTests[sCatKey] || {};
        var sFilePath = dictCat.sFilePath || "";
        if (!sFilePath) {
            VaibifyApp.fnShowToast(
                "No test file for this category", "error");
            return;
        }
        var sDir = dictStep.sDirectory || "";
        VaibifyFigureViewer.fnDisplayInNextViewer(sFilePath, sDir);
    }

    function fnViewStandardsFile(iStepIndex, sCategory) {
        var dictWorkflow = VaibifyApp.fdictGetWorkflow();
        var dictStep = dictWorkflow.listSteps[iStepIndex];
        var dictTests = VaibifyApp.fdictGetTests(dictStep);
        var sCatKey = "dict" + sCategory.charAt(0).toUpperCase() +
            sCategory.slice(1);
        var dictCat = dictTests[sCatKey] || {};
        var sStandardsPath = dictCat.sStandardsPath || "";
        if (!sStandardsPath) {
            VaibifyApp.fnShowToast(
                "No standards file for this category", "error");
            return;
        }
        var sDir = dictStep.sDirectory || "";
        VaibifyFigureViewer.fnDisplayInNextViewer(
            sStandardsPath, sDir);
    }

    function fnAddTestItem(iStep, sType) {
        if (sType === "user") {
            VaibifyApp.fnShowInputModal(
                "Test name",
                "e.g. Check convergence tolerance",
                function (sValue) {
                    _fnSaveUserTest(iStep, sValue);
                }
            );
            return;
        }
        var sLabel = sType === "file" ?
            "Test file path" : "Test command";
        var sPlaceholder = sType === "file" ?
            "e.g. test_step01.py" : "e.g. pytest test_step01.py";
        VaibifyApp.fnShowInputModal(
            sLabel, sPlaceholder, function (sValue) {
                _fnSaveTestItem(iStep, sType, sValue);
            });
    }

    async function _fnSaveTestItem(iStep, sType, sValue) {
        var dictWorkflow = VaibifyApp.fdictGetWorkflow();
        var dictStep = dictWorkflow.listSteps[iStep];
        var sKey = sType === "file" ?
            "saTestFiles" : "saTestCommands";
        if (!dictStep[sKey]) dictStep[sKey] = [];
        dictStep[sKey].push(sValue.trim());
        var dictUpdate = {};
        dictUpdate[sKey] = dictStep[sKey];
        await VaibifyApp.fnSaveStepUpdate(iStep, dictUpdate);
        VaibifyApp.fnRenderStepList();
    }

    async function _fnSaveUserTest(iStep, sName) {
        var dictWorkflow = VaibifyApp.fdictGetWorkflow();
        var dictStep = dictWorkflow.listSteps[iStep];
        if (!dictStep.dictTests) {
            dictStep.dictTests = VaibifyApp.fdictGetTests(dictStep);
        }
        if (!dictStep.dictTests.listUserTests) {
            dictStep.dictTests.listUserTests = [];
        }
        dictStep.dictTests.listUserTests.push({
            sName: sName.trim(),
            sCommand: "",
            sFilePath: "",
        });
        await VaibifyApp.fnSaveStepUpdate(iStep, {
            dictTests: dictStep.dictTests,
        });
        VaibifyApp.fnRenderStepList();
    }

    function fnEditTestFile(iStepIndex, iCmdIdx) {
        var dictWorkflow = VaibifyApp.fdictGetWorkflow();
        var step = dictWorkflow.listSteps[iStepIndex];
        if (!step) return;
        var sCmd = (step.saTestCommands || [])[iCmdIdx];
        if (!sCmd) return;
        var sFilePath = sCmd
            .replace(/^python\s+-m\s+pytest\s+/, "")
            .replace(/^pytest\s+/, "")
            .replace(/\s+(-v|--verbose)(\s|$).*$/, "")
            .trim();
        var sDir = step.sDirectory || "";
        if (sFilePath.charAt(0) !== "/" && sDir) {
            sFilePath = sDir + "/" + sFilePath;
        }
        VaibifyFigureViewer.fnDisplayInNextViewer(sFilePath, sDir);
    }

    function fnDeleteTestCommand(iStepIndex, iCmdIdx) {
        VaibifyApp.fnShowConfirmModal(
            "Delete Test",
            "Delete this test command and its test file? " +
            "This cannot be undone.",
            async function () {
                var dictWorkflow = VaibifyApp.fdictGetWorkflow();
                var step = dictWorkflow.listSteps[iStepIndex];
                if (!step) return;
                var listCmds = step.saTestCommands || [];
                if (iCmdIdx >= listCmds.length) return;
                listCmds.splice(iCmdIdx, 1);
                step.dictVerification = step.dictVerification || {};
                step.dictVerification.sUnitTest = "untested";
                await VaibifyApp.fnSaveStepUpdate(iStepIndex, {
                    saTestCommands: listCmds,
                    dictVerification: step.dictVerification,
                });
                VaibifyApp.fnRenderStepList();
            }
        );
    }

    /* --- Test Markers --- */

    function fnApplyTestMarkers(dictMarkers) {
        var bAnyChanged = false;
        for (var sIndex in dictMarkers) {
            var iStep = parseInt(sIndex, 10);
            var dictEntry = dictMarkers[sIndex];
            if (!fbApplyStepMarker(iStep, dictEntry)) continue;
            bAnyChanged = true;
            // Stale resets are silent — they restore "untested" and
            // any toast about pass/fail would be misleading.
            if (dictEntry.bStale) continue;
            var dictMarker = dictEntry.dictMarker || {};
            var sLabel = VaibifyApp.fsComputeStepLabel(iStep);
            var iExitStatus = dictMarker.iExitStatus || 0;
            var sVerb = iExitStatus === 0 ? "passed" : "failed";
            var sVariant = iExitStatus === 0 ? "success" : "error";
            VaibifyApp.fnShowToast(
                "Step " + sLabel + ": tests " + sVerb +
                " (external run detected)", sVariant
            );
        }
        if (bAnyChanged) VaibifyApp.fnRenderStepList();
    }

    function fbApplyStepMarker(iStep, dictEntry) {
        var dictMarker = dictEntry.dictMarker || {};
        var sIndex = String(iStep);
        var dictWorkflow = VaibifyApp.fdictGetWorkflow();
        var dictStep = dictWorkflow && dictWorkflow.listSteps
            ? dictWorkflow.listSteps[iStep] : null;
        if (!dictStep) return false;
        var dictVerify = dictStep.dictVerification || {};
        dictStep.dictVerification = dictVerify;
        var dictCategories = dictMarker.dictCategories || {};
        if (dictEntry.bStale) {
            // Mirror backend _fnClearStaleMarkerCategories: a stale
            // marker's previously-applied "passed"/"failed" no longer
            // reflects current state, so reset to "untested" here too.
            // Without this, the in-memory dictVerification keeps the
            // last applied value forever after the backend has cleared
            // it, producing a UI/backend desync.
            return fbClearStaleMarkerCategories(
                dictVerify, dictCategories
            );
        }
        var fTimestamp = dictMarker.fTimestamp || 0;
        if (fTimestamp <= (dictTestMarkerTimestamps[sIndex] || 0))
            return false;
        dictTestMarkerTimestamps[sIndex] = fTimestamp;
        return fbApplyAllMarkerCategories(
            dictVerify, dictCategories
        );
    }

    function fbClearStaleMarkerCategories(dictVerify, dictCategories) {
        var listKeys = [
            ["integrity", "sIntegrity"],
            ["qualitative", "sQualitative"],
            ["quantitative", "sQuantitative"],
        ];
        var bChanged = false;
        for (var i = 0; i < listKeys.length; i++) {
            var sCategory = listKeys[i][0];
            var sVerifyKey = listKeys[i][1];
            if (!dictCategories[sCategory]) continue;
            if (dictVerify[sVerifyKey] === "untested") continue;
            dictVerify[sVerifyKey] = "untested";
            bChanged = true;
        }
        return bChanged;
    }

    function fbApplyAllMarkerCategories(dictVerify, dictCategories) {
        var bUpdated = false;
        bUpdated = fbApplyMarkerCategory(
            dictVerify, dictCategories, "integrity", "sIntegrity"
        ) || bUpdated;
        bUpdated = fbApplyMarkerCategory(
            dictVerify, dictCategories, "qualitative", "sQualitative"
        ) || bUpdated;
        bUpdated = fbApplyMarkerCategory(
            dictVerify, dictCategories, "quantitative", "sQuantitative"
        ) || bUpdated;
        return bUpdated;
    }

    function fbApplyMarkerCategory(
        dictVerify, dictCategories, sCategory, sVerifyKey
    ) {
        if (!dictCategories[sCategory]) return false;
        var dictCat = dictCategories[sCategory];
        var sOld = dictVerify[sVerifyKey] || "";
        if (dictCat.iFailed > 0) {
            dictVerify[sVerifyKey] = "failed";
        } else if (dictCat.iPassed > 0) {
            dictVerify[sVerifyKey] = "passed";
        }
        return dictVerify[sVerifyKey] !== sOld;
    }

    function fnNotifyTestFileChanges(dictChanges) {
        var bSeedOnly = _bFirstTestFilePoll;
        _bFirstTestFilePoll = false;
        for (var sIndex in dictChanges) {
            var iStep = parseInt(sIndex, 10);
            var dictChange = dictChanges[sIndex];
            var listNew = dictChange.listNew || [];
            var listUnseen = flistFilterUnseenTestFiles(
                iStep, listNew
            );
            if (!bSeedOnly && listUnseen.length > 0) {
                var sLabel = VaibifyApp.fsComputeStepLabel(iStep);
                VaibifyApp.fnShowToast(
                    "Step " + sLabel + ": " + listUnseen.length +
                    " new test file(s) discovered", "info"
                );
            }
            var listCustom = dictChange.listCustom || [];
            if (listCustom.length > 0) {
                fnShowCustomTestNotice(iStep, listCustom);
            }
        }
    }

    function flistFilterUnseenTestFiles(iStep, listFiles) {
        var sKey = String(iStep);
        if (!_dictSeenNewTestFiles[sKey]) {
            _dictSeenNewTestFiles[sKey] = {};
        }
        var dictSeen = _dictSeenNewTestFiles[sKey];
        var listUnseen = [];
        for (var i = 0; i < listFiles.length; i++) {
            if (!dictSeen[listFiles[i]]) {
                listUnseen.push(listFiles[i]);
            }
            dictSeen[listFiles[i]] = true;
        }
        return listUnseen;
    }

    function fnShowCustomTestNotice(iStep, listCustomFiles) {
        var elStep = document.querySelector(
            '.step-item[data-index="' + iStep + '"]'
        );
        if (!elStep) return;
        var elExisting = elStep.querySelector(".custom-test-notice");
        if (elExisting) return;
        var elNotice = document.createElement("div");
        elNotice.className = "custom-test-notice";
        elNotice.style.cssText =
            "color:var(--color-yellow,#f1c40f);" +
            "font-size:0.85em;margin-top:4px;";
        elNotice.textContent =
            "Custom test scripts detected: " +
            listCustomFiles.join(", ");
        var elTestArea = elStep.querySelector(".test-section-label");
        if (elTestArea) {
            elTestArea.parentNode.insertBefore(
                elNotice, elTestArea.nextSibling
            );
        }
    }

    /* --- State Accessors --- */

    function fnResetState() {
        dictTestMarkerTimestamps = {};
        _dictSeenNewTestFiles = {};
        _bFirstTestFilePoll = true;
        _dictFalsificationByStep = {};
        setStepsWithData.clear();
        setExpandedUnitTests.clear();
        setGeneratingInFlight.clear();
        setGeneratedTestsPending.clear();
        _setFalsificationFetchInFlight.clear();
    }

    return {
        fnGenerateTests: fnGenerateTests,
        fnGenerateTestsForced: fnGenerateTestsForced,
        fnHandleGeneratedTest: fnHandleGeneratedTest,
        fnShowApiKeyDialog: fnShowApiKeyDialog,
        fnFinalizeGeneratedTest: fnFinalizeGeneratedTest,
        fnCancelGeneratedTest: fnCancelGeneratedTest,
        fnGenerateTestsWithApi: fnGenerateTestsWithApi,
        fnBindApiConfirmModal: fnBindApiConfirmModal,
        fnRunCategoryTests: fnRunCategoryTests,
        fdictGetFalsificationState: fdictGetFalsificationState,
        fnFetchFalsificationState: fnFetchFalsificationState,
        fnRunFalsification: fnRunFalsification,
        fnRunStepTests: fnRunStepTests,
        fnApplyCategoryResults: fnApplyCategoryResults,
        fsCollectTestOutput: fsCollectTestOutput,
        fnUpdateCategoryTestState: fnUpdateCategoryTestState,
        fnComputeAggregateTestState: fnComputeAggregateTestState,
        fnApplyTestResultToCategories: fnApplyTestResultToCategories,
        fnHandleTestResult: fnHandleTestResult,
        fnViewCategoryTestFile: fnViewCategoryTestFile,
        fnViewStandardsFile: fnViewStandardsFile,
        fnAddTestItem: fnAddTestItem,
        fnEditTestFile: fnEditTestFile,
        fnDeleteTestCommand: fnDeleteTestCommand,
        fnApplyTestMarkers: fnApplyTestMarkers,
        fbApplyStepMarker: fbApplyStepMarker,
        fbApplyAllMarkerCategories: fbApplyAllMarkerCategories,
        fbApplyMarkerCategory: fbApplyMarkerCategory,
        fnNotifyTestFileChanges: fnNotifyTestFileChanges,
        flistFilterUnseenTestFiles: flistFilterUnseenTestFiles,
        fnShowCustomTestNotice: fnShowCustomTestNotice,
        fnResetState: fnResetState,
        fsetGetExpandedUnitTests: function () {
            return setExpandedUnitTests;
        },
        fsetGetGeneratingInFlight: function () {
            return setGeneratingInFlight;
        },
        fsetGetStepsWithData: function () {
            return setStepsWithData;
        },
        fbIsTestPending: function (iStep) {
            return setGeneratedTestsPending.has(iStep);
        },
    };
})();
