/* Vaibify — Test generation, running, and state (extracted from scriptApplication.js) */

var PipeleyenTestManager = (function () {
    "use strict";

    var setExpandedUnitTests = new Set();
    var setGeneratingInFlight = new Set();
    var setGeneratedTestsPending = new Set();
    var setStepsWithData = new Set();
    var dictTestMarkerTimestamps = {};
    var _dictSeenNewTestFiles = {};
    var _bFirstTestFilePoll = true;

    /* --- Test Generation --- */

    async function fnGenerateTests(iStep) {
        var dictWorkflow = PipeleyenApp.fdictGetWorkflow();
        var step = dictWorkflow.listSteps[iStep];
        if (setGeneratingInFlight.has(iStep)) return;
        if (step && (step.saTestCommands || []).length > 0) {
            var bConfirmed = await new Promise(function (resolve) {
                PipeleyenApp.fnShowConfirmModal(
                    "Overwrite Tests",
                    "Tests already exist for this step. " +
                    "Generate new tests will overwrite them.",
                    function () { resolve(true); },
                    function () { resolve(false); }
                );
            });
            if (!bConfirmed) return;
        }
        setGeneratingInFlight.add(iStep);
        PipeleyenApp.fnRenderStepList();
        try {
            var sContainerId = PipeleyenApp.fsGetContainerId();
            var dictResult = await VaibifyApi.fdictPost(
                "/api/steps/" + sContainerId + "/" + iStep +
                "/generate-test",
                {}
            );
            setGeneratingInFlight.delete(iStep);
            if (dictResult.bNeedsFallback) {
                PipeleyenApp.fnRenderStepList();
                PipeleyenApp.fnShowConfirmModal(
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
                PipeleyenApp.fnRenderStepList();
                var listFiles = dictResult.listModifiedFiles || [];
                PipeleyenApp.fnShowConfirmModal(
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
                PipeleyenApp.fnRenderStepList();
                PipeleyenApp.fnShowToast("No tests generated", "error");
                PipeleyenApp.fnShowErrorModal(
                    "Test generation failed:\n\n" +
                    (dictResult.sMessage || "No tests generated")
                );
                return;
            }
            fnHandleGeneratedTest(iStep, dictResult);
        } catch (error) {
            setGeneratingInFlight.delete(iStep);
            PipeleyenApp.fnRenderStepList();
            PipeleyenApp.fnShowErrorModal(
                "Test generation failed:\n\n" +
                (error.message || String(error))
            );
        }
    }

    async function fnGenerateTestsForced(iStep) {
        setGeneratingInFlight.add(iStep);
        PipeleyenApp.fnRenderStepList();
        try {
            var sContainerId = PipeleyenApp.fsGetContainerId();
            var dictResult = await VaibifyApi.fdictPost(
                "/api/steps/" + sContainerId + "/" + iStep +
                "/generate-test",
                {bForceOverwrite: true}
            );
            setGeneratingInFlight.delete(iStep);
            fnHandleGeneratedTest(iStep, dictResult);
        } catch (error) {
            setGeneratingInFlight.delete(iStep);
            PipeleyenApp.fnRenderStepList();
            PipeleyenApp.fnShowErrorModal(
                "Test generation failed:\n\n" +
                (error.message || String(error))
            );
        }
    }

    function fnHandleGeneratedTest(iStep, dictResult) {
        var dictWorkflow = PipeleyenApp.fdictGetWorkflow();
        var dictStep = dictWorkflow.listSteps[iStep];
        if (!dictStep.dictTests) {
            dictStep.dictTests = PipeleyenApp.fdictGetTests(dictStep);
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
        PipeleyenApp.fnSaveStepUpdate(iStep, {
            dictTests: dictStep.dictTests,
            dictVerification: dictStep.dictVerification,
            saTestCommands: listAllCommands,
        });
        if (!setExpandedUnitTests.has(iStep)) {
            setExpandedUnitTests.add(iStep);
        }
        PipeleyenApp.fnRenderStepList();
        var iSuccessCount = listCategories.length - listErrors.length;
        var sStepLabel = PipeleyenApp.fsComputeStepLabel(iStep);
        if (listErrors.length > 0) {
            PipeleyenApp.fnShowErrorModal(
                listErrors.join(", ") +
                " failed to generate.\n" +
                "Fix manually or ask a coding agent to fix.");
        }
        PipeleyenApp.fnShowToast(
            sStepLabel + ": " + iSuccessCount +
            " of 3 test categories generated. Running\u2026",
            iSuccessCount === 3 ? "success" : "error");
        if (listAllCommands.length > 0) {
            fnRunStepTests(iStep);
        }
    }

    function fnShowApiKeyDialog(iStep) {
        var elModal = document.getElementById("modalApiConfirm");
        elModal.style.display = "flex";
        elModal.dataset.step = iStep;
    }

    function fnFinalizeGeneratedTest(iStep) {
        setGeneratedTestsPending.delete(iStep);
        var dictWorkflow = PipeleyenApp.fdictGetWorkflow();
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
        PipeleyenApp.fnSaveStepUpdate(iStep, {
            dictTests: dictStep.dictTests,
            dictVerification: dictStep.dictVerification,
        });
        PipeleyenApp.fnRenderStepList();
    }

    async function fnCancelGeneratedTest(iStep) {
        setGeneratedTestsPending.delete(iStep);
        var sContainerId = PipeleyenApp.fsGetContainerId();
        try {
            await VaibifyApi.fnDelete(
                "/api/steps/" + sContainerId + "/" + iStep +
                "/generated-test"
            );
        } catch (error) {
            PipeleyenApp.fnShowToast("Delete failed", "error");
        }
        var dictWorkflow = PipeleyenApp.fdictGetWorkflow();
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
        PipeleyenApp.fnRenderStepList();
    }

    async function fnGenerateTestsWithApi(iStep, sApiKey) {
        PipeleyenApp.fnShowToast(
            "Generating tests via API...", "success");
        var sContainerId = PipeleyenApp.fsGetContainerId();
        try {
            var dictResult = await VaibifyApi.fdictPost(
                "/api/steps/" + sContainerId + "/" + iStep +
                "/generate-test",
                {bUseApi: true, sApiKey: sApiKey}
            );
            fnHandleGeneratedTest(iStep, dictResult);
        } catch (error) {
            PipeleyenApp.fnShowToast(
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
                    PipeleyenApp.fnShowToast(
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
        var sContainerId = PipeleyenApp.fsGetContainerId();
        try {
            var dictResult = await VaibifyApi.fdictPost(
                "/api/steps/" + sContainerId + "/" + iStepIndex +
                "/run-test-category",
                {sCategory: sCategory}
            );
            fnUpdateCategoryTestState(
                iStepIndex, sCategory, dictResult);
        } catch (error) {
            PipeleyenApp.fnShowErrorModal(
                "Test run failed: " + error.message);
        }
    }

    async function fnRunStepTests(iStepIndex) {
        var sContainerId = PipeleyenApp.fsGetContainerId();
        if (!sContainerId) return;
        var dictWorkflow = PipeleyenApp.fdictGetWorkflow();
        var step = dictWorkflow.listSteps[iStepIndex];
        if (!step || !step.saTestCommands ||
            step.saTestCommands.length === 0) return;
        PipeleyenApp.fnShowToast("Running tests for Step " +
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
            PipeleyenApp.fnClearOutputModified(iStepIndex);
            var dictStepUpdate = {
                dictVerification: step.dictVerification,
            };
            if (step.dictTests) {
                dictStepUpdate.dictTests = step.dictTests;
            }
            PipeleyenApp.fnSaveStepUpdate(iStepIndex, dictStepUpdate);
            PipeleyenApp.fnRenderStepList();
            PipeleyenApp.fnUpdateHighlightState();
            var sOutput = fsCollectTestOutput(dictResult);
            PipeleyenApp.fnShowToast(
                dictResult.bPassed ?
                    "Tests passed" : "Tests FAILED",
                dictResult.bPassed ? "success" : "error"
            );
            PipeleyenFigureViewer.fnDisplayTestOutput(
                sOutput, dictResult.bPassed);
        } catch (error) {
            PipeleyenApp.fnShowToast(
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
        var dictWorkflow = PipeleyenApp.fdictGetWorkflow();
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
        PipeleyenApp.fnClearOutputModified(iStepIndex);
        var dictCatUpdate = {
            dictVerification: dictStep.dictVerification,
        };
        if (dictStep.dictTests) {
            dictCatUpdate.dictTests = dictStep.dictTests;
        }
        PipeleyenApp.fnSaveStepUpdate(iStepIndex, dictCatUpdate);
        PipeleyenApp.fnRenderStepList();
        var sLabel = VaibifyUtilities.fsTestCategoryLabel(sCategory);
        PipeleyenApp.fnShowToast(sLabel + ": " +
            (dictResult.bPassed ? "Passed" : "Failed"),
            dictResult.bPassed ? "success" : "error");
    }

    function fnComputeAggregateTestState(iStepIndex) {
        var dictWorkflow = PipeleyenApp.fdictGetWorkflow();
        var dictStep = dictWorkflow.listSteps[iStepIndex];
        var dictVerify = PipeleyenApp.fdictGetVerification(dictStep);
        var dictTests = PipeleyenApp.fdictGetTests(dictStep);
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
        var dictWorkflow = PipeleyenApp.fdictGetWorkflow();
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
        PipeleyenApp.fnClearOutputModified(iStep);
        var dictUpdate = {
            dictVerification: dictStep.dictVerification,
        };
        if (dictStep.dictTests) {
            dictUpdate.dictTests = dictStep.dictTests;
        }
        PipeleyenApp.fnSaveStepUpdate(iStep, dictUpdate);
        PipeleyenApp.fnRenderStepList();
        PipeleyenApp.fnUpdateHighlightState();
        var sLabel = dictEvent.sResult === "passed" ?
            "Tests passed" : "Tests FAILED";
        var sStepLabel = PipeleyenApp.fsComputeStepLabel(iStep);
        PipeleyenApp.fnShowToast("Step " + sStepLabel + ": " + sLabel,
            dictEvent.sResult === "passed" ? "success" : "error");
    }

    /* --- Test UI --- */

    function fnViewCategoryTestFile(iStepIndex, sCategory) {
        var dictWorkflow = PipeleyenApp.fdictGetWorkflow();
        var dictStep = dictWorkflow.listSteps[iStepIndex];
        var dictTests = PipeleyenApp.fdictGetTests(dictStep);
        var sCatKey = "dict" + sCategory.charAt(0).toUpperCase() +
            sCategory.slice(1);
        var dictCat = dictTests[sCatKey] || {};
        var sFilePath = dictCat.sFilePath || "";
        if (!sFilePath) {
            PipeleyenApp.fnShowToast(
                "No test file for this category", "error");
            return;
        }
        var sDir = dictStep.sDirectory || "";
        PipeleyenFigureViewer.fnDisplayInNextViewer(sFilePath, sDir);
    }

    function fnViewStandardsFile(iStepIndex, sCategory) {
        var dictWorkflow = PipeleyenApp.fdictGetWorkflow();
        var dictStep = dictWorkflow.listSteps[iStepIndex];
        var dictTests = PipeleyenApp.fdictGetTests(dictStep);
        var sCatKey = "dict" + sCategory.charAt(0).toUpperCase() +
            sCategory.slice(1);
        var dictCat = dictTests[sCatKey] || {};
        var sStandardsPath = dictCat.sStandardsPath || "";
        if (!sStandardsPath) {
            PipeleyenApp.fnShowToast(
                "No standards file for this category", "error");
            return;
        }
        var sDir = dictStep.sDirectory || "";
        PipeleyenFigureViewer.fnDisplayInNextViewer(
            sStandardsPath, sDir);
    }

    function fnAddTestItem(iStep, sType) {
        if (sType === "user") {
            PipeleyenApp.fnShowInputModal(
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
        PipeleyenApp.fnShowInputModal(
            sLabel, sPlaceholder, function (sValue) {
                _fnSaveTestItem(iStep, sType, sValue);
            });
    }

    async function _fnSaveTestItem(iStep, sType, sValue) {
        var dictWorkflow = PipeleyenApp.fdictGetWorkflow();
        var dictStep = dictWorkflow.listSteps[iStep];
        var sKey = sType === "file" ?
            "saTestFiles" : "saTestCommands";
        if (!dictStep[sKey]) dictStep[sKey] = [];
        dictStep[sKey].push(sValue.trim());
        var dictUpdate = {};
        dictUpdate[sKey] = dictStep[sKey];
        await PipeleyenApp.fnSaveStepUpdate(iStep, dictUpdate);
        PipeleyenApp.fnRenderStepList();
    }

    async function _fnSaveUserTest(iStep, sName) {
        var dictWorkflow = PipeleyenApp.fdictGetWorkflow();
        var dictStep = dictWorkflow.listSteps[iStep];
        if (!dictStep.dictTests) {
            dictStep.dictTests = PipeleyenApp.fdictGetTests(dictStep);
        }
        if (!dictStep.dictTests.listUserTests) {
            dictStep.dictTests.listUserTests = [];
        }
        dictStep.dictTests.listUserTests.push({
            sName: sName.trim(),
            sCommand: "",
            sFilePath: "",
        });
        await PipeleyenApp.fnSaveStepUpdate(iStep, {
            dictTests: dictStep.dictTests,
        });
        PipeleyenApp.fnRenderStepList();
    }

    function fnEditTestFile(iStepIndex, iCmdIdx) {
        var dictWorkflow = PipeleyenApp.fdictGetWorkflow();
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
        PipeleyenFigureViewer.fnDisplayInNextViewer(sFilePath, sDir);
    }

    function fnDeleteTestCommand(iStepIndex, iCmdIdx) {
        PipeleyenApp.fnShowConfirmModal(
            "Delete Test",
            "Delete this test command and its test file? " +
            "This cannot be undone.",
            async function () {
                var dictWorkflow = PipeleyenApp.fdictGetWorkflow();
                var step = dictWorkflow.listSteps[iStepIndex];
                if (!step) return;
                var listCmds = step.saTestCommands || [];
                if (iCmdIdx >= listCmds.length) return;
                listCmds.splice(iCmdIdx, 1);
                step.dictVerification = step.dictVerification || {};
                step.dictVerification.sUnitTest = "untested";
                await PipeleyenApp.fnSaveStepUpdate(iStepIndex, {
                    saTestCommands: listCmds,
                    dictVerification: step.dictVerification,
                });
                PipeleyenApp.fnRenderStepList();
            }
        );
    }

    /* --- Test Markers --- */

    function fnApplyTestMarkers(dictMarkers) {
        var bAnyChanged = false;
        for (var sIndex in dictMarkers) {
            var iStep = parseInt(sIndex, 10);
            if (!fbApplyStepMarker(iStep, dictMarkers[sIndex]))
                continue;
            bAnyChanged = true;
            var dictMarker = dictMarkers[sIndex].dictMarker || {};
            var sLabel = PipeleyenApp.fsComputeStepLabel(iStep);
            var iExitStatus = dictMarker.iExitStatus || 0;
            var sVerb = iExitStatus === 0 ? "passed" : "failed";
            var sVariant = iExitStatus === 0 ? "success" : "error";
            PipeleyenApp.fnShowToast(
                "Step " + sLabel + ": tests " + sVerb +
                " (external run detected)", sVariant
            );
        }
        if (bAnyChanged) PipeleyenApp.fnRenderStepList();
    }

    function fbApplyStepMarker(iStep, dictEntry) {
        var dictMarker = dictEntry.dictMarker || {};
        var sIndex = String(iStep);
        var fTimestamp = dictMarker.fTimestamp || 0;
        if (fTimestamp <= (dictTestMarkerTimestamps[sIndex] || 0))
            return false;
        dictTestMarkerTimestamps[sIndex] = fTimestamp;
        if (dictEntry.bStale) return false;
        var dictWorkflow = PipeleyenApp.fdictGetWorkflow();
        var dictStep = dictWorkflow.listSteps[iStep];
        if (!dictStep) return false;
        var dictVerify = dictStep.dictVerification || {};
        dictStep.dictVerification = dictVerify;
        var dictCategories = dictMarker.dictCategories || {};
        return fbApplyAllMarkerCategories(
            dictVerify, dictCategories
        );
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
                var sLabel = PipeleyenApp.fsComputeStepLabel(iStep);
                PipeleyenApp.fnShowToast(
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
        setStepsWithData.clear();
        setExpandedUnitTests.clear();
        setGeneratingInFlight.clear();
        setGeneratedTestsPending.clear();
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
