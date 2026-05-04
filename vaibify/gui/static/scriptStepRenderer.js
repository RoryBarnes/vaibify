/* Vaibify — Step rendering functions */

var VaibifyStepRenderer = (function () {
    "use strict";

    var fnEscapeHtml = VaibifyUtilities.fnEscapeHtml;

    var _DICT_STALE_ROW_LABELS = {
        "test|dataScript": "Tests older than data scripts",
        "test|dataFile": "Tests older than data files",
        "user|dataScript": "User verification older than data scripts",
        "user|dataFile": "User verification older than data files",
        "user|plotScript": "User verification older than plot scripts",
        "user|plotFile": "User verification older than plot files",
    };

    function _fdictGroupStaleArtifacts(listArtifacts) {
        var dictGrouped = {};
        for (var i = 0; i < listArtifacts.length; i++) {
            var dictItem = listArtifacts[i];
            var sKey = dictItem.sValidator + "|" + dictItem.sCategory;
            if (!dictGrouped[sKey]) dictGrouped[sKey] = [];
            dictGrouped[sKey].push(dictItem.sPath);
        }
        return dictGrouped;
    }

    function fsRenderStaleArtifactRows(dictContext, iIndex) {
        var listArtifacts =
            (dictContext.dictStaleArtifacts || {})[iIndex] || [];
        if (listArtifacts.length === 0) return "";
        var dictGrouped = _fdictGroupStaleArtifacts(listArtifacts);
        var sHtml = "";
        Object.keys(_DICT_STALE_ROW_LABELS).forEach(function (sKey) {
            var listPaths = dictGrouped[sKey];
            if (!listPaths || listPaths.length === 0) return;
            var listNames = listPaths.map(function (sPath) {
                return sPath.split("/").pop();
            });
            sHtml += '<div class="output-modified-warning">' +
                '\u26A0 ' + _DICT_STALE_ROW_LABELS[sKey] + ': ' +
                fnEscapeHtml(listNames.join(", ")) + '</div>';
        });
        return sHtml;
    }

    function fsRenderStepItem(step, iIndex, dictVars, dictContext) {
        var bInteractive = step.bInteractive === true;
        var sRunStatus = dictContext.dictStepStatus[iIndex] || "";
        var sStatusClass = "";
        if (sRunStatus === "running" || sRunStatus === "queued") {
            sStatusClass = sRunStatus;
        } else if (sRunStatus === "pass") {
            var sVerifyState = dictContext.fsComputeStepDotState(
                step, iIndex);
            sStatusClass = (sVerifyState === "verified")
                ? "verified" : "partial";
        } else if (sRunStatus === "fail") {
            var sVerifyOnFail = dictContext.fsComputeStepDotState(
                step, iIndex);
            sStatusClass = (sVerifyOnFail === "partial" ||
                sVerifyOnFail === "verified") ? "partial" : "fail";
        } else {
            sStatusClass = dictContext.fsComputeStepDotState(
                step, iIndex);
        }
        var bRunEnabled = step.bRunEnabled !== false;
        var bSelected = iIndex === dictContext.iSelectedStepIndex;
        var bExpanded = dictContext.setExpandedSteps.has(iIndex);

        var sVerifiedBadge = "";
        if (sStatusClass === "verified") {
            sVerifiedBadge = '<img src="/static/favicon.png" ' +
                'class="vaib-verified-badge" alt="verified">';
        }

        var sStepNumber = dictContext.fsComputeStepLabel(iIndex);

        var sHtml = '<div class="step-wrapper">' +
            '<div class="step-item' + (bSelected ? " selected" : "") +
            (bInteractive ? " interactive" : "") +
            '" data-index="' + iIndex + '" draggable="true">' +
            '<input type="checkbox" class="step-checkbox"' +
            (bRunEnabled ? " checked" : "") + ">" +
            '<span class="step-number">' +
            sStepNumber + "</span>" +
            '<span class="step-name" title="' +
            fnEscapeHtml(step.sName) + '">' +
            fnEscapeHtml(step.sName) + "</span>" +
            (dictContext.dictScriptModified[iIndex] === "modified" ?
                '<span class="script-modified-badge" ' +
                'title="Scripts modified since last run">' +
                '&#9998;</span>' : '') +
            (((step.dictVerification || {})
                .bUnseededRandomnessWarning === true) ?
                '<span class="script-unseeded-badge" ' +
                'title="Unseeded randomness detected: add a seed ' +
                'so the pilot run is reproducible.">&#9888;</span>' :
                '') +
            dictContext.fsBuildWarningBadge(step, iIndex) +
            (sStatusClass === "verified" ? "" :
                '<span class="step-status ' + sStatusClass +
                '"></span>') +
            sVerifiedBadge +
            '<span class="step-actions">' +
            '<button class="btn-icon step-edit" title="Edit">&#9998;</button>' +
            "</span></div>";

        if (!bExpanded) {
            return sHtml + '</div>';
        }

        sHtml += '<div class="step-detail expanded' +
            '" data-index="' + iIndex + '">';

        var sResolvedDir = dictContext.fsResolveTemplate(
            step.sDirectory, dictVars);
        sHtml += '<div class="detail-label">Directory</div>';
        sHtml += '<div class="detail-field" data-view="field">' +
            fnEscapeHtml(sResolvedDir) + "</div>";
        if (!bInteractive) {
            sHtml += '<div class="detail-label plot-only-row">' +
                '<label class="plot-only-toggle">' +
                '<input type="checkbox" class="plot-only-checkbox"' +
                ' data-step="' + iIndex + '"' +
                (step.bPlotOnly !== false ? " checked" : "") + '>' +
                ' Plot only (skip data analysis)</label></div>';
        }

        if (bInteractive) {
            var bHasPlots = (step.saPlotCommands || []).length > 0;
            sHtml += '<div class="interactive-run-section">' +
                '<button class="btn btn-interactive-run" ' +
                'data-index="' + iIndex + '">' +
                '&#9654; Run in Terminal</button>';
            if (bHasPlots) {
                sHtml += ' <button class="btn btn-interactive-plots" ' +
                    'data-index="' + iIndex + '">' +
                    '&#9654; Run Plots</button>';
            }
            sHtml += '<div class="detail-note">This step requires ' +
                'human judgment. It will run in the terminal ' +
                'with X11 display forwarding.</div></div>';
        }

        sHtml += fsRenderSectionLabel(
            "Data Analysis Commands", iIndex, "saDataCommands"
        );
        if (step.saDataCommands) {
            step.saDataCommands.forEach(function (sCmd, iCmdIdx) {
                sHtml += fsRenderDetailItem(
                    sCmd, dictVars, "command", "saDataCommands",
                    iIndex, iCmdIdx, undefined, dictContext
                );
            });
        }
        if ((step.saDataCommands || []).length > 0) {
            sHtml += '<button class="btn btn-run-data" ' +
                'data-step="' + iIndex +
                '">Run Data Analysis</button>';
        }

        if ((step.saDataCommands || []).length > 0) {
            sHtml += '<div class="timestamp-field">' +
                fsRenderRunStats(step) +
                fsRenderDataMtime(iIndex, dictContext) + '</div>';
        }

        sHtml += fsRenderSectionLabel(
            "Data Files", iIndex, "saDataFiles"
        );
        if (step.saDataFiles) {
            step.saDataFiles.forEach(function (sFile, iFileIdx) {
                sHtml += fsRenderDetailItem(
                    sFile, dictVars, "output", "saDataFiles",
                    iIndex, iFileIdx, sResolvedDir, dictContext
                );
            });
        }

        sHtml += fsRenderSectionLabel(
            "Plot Commands", iIndex, "saPlotCommands"
        );
        if (step.saPlotCommands) {
            step.saPlotCommands.forEach(function (sCmd, iCmdIdx) {
                sHtml += fsRenderDetailItem(
                    sCmd, dictVars, "command", "saPlotCommands",
                    iIndex, iCmdIdx, undefined, dictContext
                );
            });
        }
        if ((step.saPlotCommands || []).length > 0) {
            sHtml += '<button class="btn btn-run-plots" ' +
                'data-step="' + iIndex +
                '">Run Plots</button>';
        }

        sHtml += fsRenderSectionLabel(
            "Plot Files", iIndex, "saPlotFiles"
        );
        if (step.saPlotFiles) {
            step.saPlotFiles.forEach(function (sFile, iFileIdx) {
                sHtml += fsRenderDetailItem(
                    sFile, dictVars, "output", "saPlotFiles",
                    iIndex, iFileIdx, sResolvedDir, dictContext
                );
            });
        }

        if ((step.saPlotFiles || []).length > 0) {
            sHtml += fsRenderPlotStandardButtons(iIndex);
        }

        if ((step.saPlotFiles || []).length > 0) {
            sHtml += '<div class="timestamp-field">' +
                fsRenderPlotMtime(iIndex, dictContext) + '</div>';
        }

        sHtml += fsRenderVerificationBlock(step, iIndex, dictContext);
        sHtml += fsRenderDiscoveredOutputs(iIndex, dictContext);
        sHtml += fsRenderRunStepButton(step, iIndex);

        sHtml += "</div>";
        sHtml += "</div>";
        return sHtml;
    }

    function fsRenderRunStepButton(step, iIndex) {
        if (step.bInteractive) return "";
        var bHasDataCmds = (step.saDataCommands || []).length > 0;
        var bHasPlotCmds = (step.saPlotCommands || []).length > 0;
        if (!bHasDataCmds && !bHasPlotCmds) return "";
        return '<button class="btn btn-primary btn-run-step" ' +
            'data-step="' + iIndex +
            '">&#9654; Run Step</button>';
    }

    function fsRenderVerificationBlock(step, iIndex, dictContext) {
        var bInteractive = step.bInteractive === true;
        var bPlotOnly = (step.saDataCommands || []).length === 0;
        var dictVerify = dictContext.fdictGetVerification(step);
        var sHtml = '<div class="detail-label">Verification</div>';
        sHtml += '<div class="verification-block" data-step="' +
            iIndex + '">';
        var listModified = dictVerify.listModifiedFiles || [];
        if (listModified.length > 0) {
            var listNames = listModified.map(function (sPath) {
                return sPath.split("/").pop();
            });
            sHtml += '<div class="output-modified-warning">' +
                '\u26A0 Modified: ' +
                fnEscapeHtml(listNames.join(", ")) + '</div>';
        }
        sHtml += fsRenderStaleArtifactRows(
            dictContext, iIndex);
        if (!bInteractive && !bPlotOnly &&
            dictContext.fsEffectiveTestState(step) === "failed") {
            sHtml += '<div class="output-modified-warning">' +
                '\u26A0 Unit tests failing</div>';
        }
        if (!bInteractive && !bPlotOnly) {
            var sUnitState = dictContext.fsEffectiveTestState(step);
            sHtml += fsRenderVerificationRow(
                "Unit Tests", sUnitState, "unitTest", iIndex,
                dictContext
            );
            var sMarkerMtime = (
                dictContext.dictMarkerMtimeByStep || {}
            )[String(iIndex)];
            sHtml += '<div class="timestamp-field">' +
                fsRenderVerificationTimestamp(
                    "Last run",
                    sMarkerMtime ?
                        fsFormatUnixTimestamp(sMarkerMtime) : "") +
                '</div>';
            if (dictContext.setGeneratingInFlight.has(iIndex)) {
                sHtml += '<div class="unit-tests-expanded">' +
                    '<button class="btn-generate-test" disabled>' +
                    '<span class="spinner"></span> ' +
                    'Building Tests\u2026</button></div>';
            } else if (dictContext.setExpandedUnitTests.has(iIndex)) {
                sHtml += fsRenderUnitTestsExpanded(
                    step, iIndex, dictContext);
            }
        }
        var bHasDeps = dictContext.flistGetStepDependencies(
            iIndex).length > 0;
        if (bHasDeps) {
            var sDepsState = dictContext.fsComputeDepsState(iIndex);
            sHtml += fsRenderVerificationRow(
                "Dependencies", sDepsState, "deps", iIndex,
                dictContext
            );
            sHtml += '<div class="timestamp-field">' +
                fsRenderVerificationTimestamp(
                    "Last checked", dictVerify.sLastDepsCheck) +
                '</div>';
            if (dictContext.setExpandedDeps.has(iIndex)) {
                sHtml += fsRenderDepsExpanded(iIndex, dictContext);
            }
        }
        sHtml += fsRenderVerificationRow(
            dictContext.sUserName, dictVerify.sUser, "user", iIndex,
            dictContext
        );
        sHtml += '<div class="timestamp-field">' +
            fsRenderVerificationTimestamp(
                "Last updated", dictVerify.sLastUserUpdate) +
            '</div>';
        sHtml += '</div>';
        return sHtml;
    }

    function fsRenderUnitTestsExpanded(step, iIndex, dictContext) {
        var sHtml = '<div class="unit-tests-expanded">';
        var listCategories = [
            "qualitative", "quantitative", "integrity"];
        var bAnyTests = false;
        for (var i = 0; i < listCategories.length; i++) {
            var sCategory = listCategories[i];
            var sCatState = dictContext.fsGetCategoryState(
                step, sCategory);
            var sLabel = dictContext.fsTestCategoryLabel(sCategory);
            sHtml += fsRenderSubTestRow(
                sLabel, sCatState, sCategory, iIndex, dictContext);
            var setExp = dictContext.fsetGetExpandedCategory(
                sCategory);
            if (setExp.has(iIndex)) {
                sHtml += fsRenderSubTestExpanded(
                    step, iIndex, sCategory, dictContext);
            }
            var dictTests = dictContext.fdictGetTests(step);
            var sCatKey = "dict" +
                sCategory.charAt(0).toUpperCase() +
                sCategory.slice(1);
            if (((dictTests[sCatKey] || {}).saCommands ||
                []).length > 0) {
                bAnyTests = true;
            }
        }
        if (bAnyTests) {
            sHtml += '<button class="btn btn-run-all-tests" ' +
                'data-step="' + iIndex +
                '">Run All Tests</button>';
        }
        sHtml += fsRenderGenerateButton(step, iIndex, dictContext);
        sHtml += '</div>';
        return sHtml;
    }

    function fsRenderSubTestRow(
        sLabel, sState, sCategory, iIndex, dictContext
    ) {
        var setExp = dictContext.fsetGetExpandedCategory(sCategory);
        var bExpanded = setExp.has(iIndex);
        var sTriangle = '<span class="expand-triangle">' +
            (bExpanded ? "\u25BE" : "\u25B8") + '</span> ';
        return '<div class="sub-test-row expandable" data-step="' +
            iIndex + '" data-approver="' + sCategory + '">' +
            '<span class="verification-label">' +
            sTriangle + fnEscapeHtml(sLabel) + '</span>' +
            '<span class="verification-badge state-' +
            sState + '">' +
            dictContext.fsVerificationStateIcon(sState) + ' ' +
            dictContext.fsVerificationStateLabel(sState) +
            '</span></div>';
    }

    function fsRenderSubTestExpanded(
        step, iIndex, sCategory, dictContext
    ) {
        var dictTests = dictContext.fdictGetTests(step);
        var sCatKey = "dict" +
            sCategory.charAt(0).toUpperCase() +
            sCategory.slice(1);
        var dictCat = dictTests[sCatKey] || {};
        var sStandardsPath = dictCat.sStandardsPath || "";
        var sHtml = '<div class="sub-test-expanded sub-test-column">';
        if (sStandardsPath) {
            sHtml += '<div><span class="test-standards-link" ' +
                'data-step="' + iIndex +
                '" data-category="' + sCategory +
                '" data-path="' +
                fnEscapeHtml(sStandardsPath) +
                '">Standards</span></div>';
        }
        var sLastOutput = dictCat.sLastOutput || "";
        if (sLastOutput) {
            sHtml += '<div><span class="test-log-link" ' +
                'data-step="' + iIndex +
                '" data-category="' + sCategory +
                '" data-log="' +
                fnEscapeHtml(sCategory) +
                '">Log</span></div>';
        }
        if ((dictCat.saCommands || []).length > 0) {
            sHtml += '<div><button class="btn btn-run-category" ' +
                'data-step="' + iIndex +
                '" data-category="' + sCategory +
                '">Run</button></div>';
        }
        sHtml += fsRenderTestSourceMtimeLine(
            iIndex, sCategory, dictContext);
        sHtml += '</div>';
        return sHtml;
    }

    function fsRenderTestSourceMtimeLine(
        iIndex, sCategory, dictContext
    ) {
        var dictByStep = dictContext.dictTestCategoryMtimes || {};
        var dictCats = dictByStep[String(iIndex)] || {};
        if (!dictCats.hasOwnProperty(sCategory)) return "";
        var sFormatted = VaibifyUtilities.fsFormatEpochUtc(
            dictCats[sCategory]);
        if (!sFormatted) return "";
        return '<div class="test-source-mtime ' +
            'detail-note">Test file modified: ' +
            fnEscapeHtml(sFormatted) + '</div>';
    }

    function fsRenderDepsExpanded(iIndex, dictContext) {
        var listDeps = dictContext.flistGetStepDependencies(iIndex);
        var sHtml = '<div class="deps-expanded">';
        for (var i = 0; i < listDeps.length; i++) {
            var iDep = listDeps[i];
            if (iDep === iIndex) continue;
            var depStep = dictContext.dictWorkflow.listSteps[iDep];
            if (!depStep) continue;
            sHtml += fsRenderDepItem(iIndex, iDep, depStep, dictContext);
        }
        sHtml += '<button class="btn btn-small btn-add-deps" ' +
            'data-step="' + iIndex + '" ' +
            'style="margin-top:6px">Update Dependencies</button>';
        sHtml += ' <button class="btn btn-small btn-show-deps" ' +
            'data-step="' + iIndex + '" ' +
            'style="margin-top:6px">Show Dependencies</button>';
        sHtml += '</div>';
        return sHtml;
    }

    function fsRenderDepItem(iIndex, iDep, depStep, dictContext) {
        var tStates = dictContext.ftComputeDepAxisStates(
            iIndex, iDep);
        var sNum = dictContext.fsComputeStepLabel(iDep);
        return '<div class="dep-item">' +
            '<div class="dep-header"><span class="dep-label">' +
            sNum + ' ' + fnEscapeHtml(depStep.sName) +
            '</span></div>' +
            fsRenderDepAxisRow(
                "Step Status", tStates.sStepStatus, "", dictContext) +
            fsRenderDepAxisRow(
                "Timing", tStates.sTiming,
                fsFormatTimingDetail(tStates), dictContext) +
            '</div>';
    }

    function fsRenderDepAxisRow(sLabel, sState, sDetail, dictContext) {
        var sBadgeState = sState === "unknown" ? "untested" : sState;
        var sStateLabel = sState === "unknown" ? "—" :
            dictContext.fsVerificationStateLabel(sState);
        var sIcon = sState === "unknown" ? "" :
            dictContext.fsVerificationStateIcon(sState) + " ";
        var sHtml = '<div class="dep-axis-row">' +
            '<span class="dep-axis-label">' +
            fnEscapeHtml(sLabel) + '</span>' +
            '<span class="verification-badge state-' +
            sBadgeState + '">' + sIcon + sStateLabel +
            '</span></div>';
        if (sDetail) {
            sHtml += '<div class="dep-axis-warning">' +
                '&#9888; ' + fnEscapeHtml(sDetail) + '</div>';
        }
        return sHtml;
    }

    function fsFormatTimingDetail(tStates) {
        if (tStates.sTiming !== "failed") return "";
        if (tStates.iDepTestSrcMtime !== null
                && tStates.iDepTestSrcMtime !== undefined) {
            return "Unit tests edited " +
                fsFormatUnixTimestamp(
                    String(tStates.iDepTestSrcMtime)) +
                " after my output";
        }
        if (!tStates.iDepMtime) return "";
        return "Outputs regenerated " +
            fsFormatUnixTimestamp(String(tStates.iDepMtime)) +
            " after my output";
    }

    function fsRenderVerificationRow(
        sLabel, sState, sApprover, iIndex, dictContext
    ) {
        var sClickClass = sApprover === "user" ? " clickable" :
            " expandable";
        var sTriangle = "";
        if (sApprover === "unitTest") {
            var bExpanded = dictContext.setExpandedUnitTests.has(
                iIndex);
            sTriangle = '<span class="expand-triangle">' +
                (bExpanded ? "\u25BE" : "\u25B8") + '</span> ';
        }
        if (sApprover === "deps") {
            var bDepsExpanded = dictContext.setExpandedDeps.has(
                iIndex);
            sTriangle = '<span class="expand-triangle">' +
                (bDepsExpanded ? "\u25BE" : "\u25B8") + '</span> ';
        }
        var sStateClass = sState || "untested";
        return '<div class="verification-row' + sClickClass +
            '" data-step="' + iIndex +
            '" data-approver="' + sApprover + '">' +
            '<span class="verification-label">' +
            sTriangle + fnEscapeHtml(sLabel) + '</span>' +
            '<span class="verification-badge state-' + sStateClass + '">' +
            dictContext.fsVerificationStateIcon(sState) + ' ' +
            dictContext.fsVerificationStateLabel(sState) +
            '</span></div>';
    }

    function fsRenderGenerateButton(step, iIndex, dictContext) {
        if ((step.saDataCommands || []).length === 0) return "";
        if (dictContext.setGeneratingInFlight.has(iIndex)) {
            return '<button class="btn-generate-test" data-step="' +
                iIndex + '" id="btnGenTest' + iIndex +
                '" disabled>' +
                '<span class="spinner"></span> Building Tests' +
                '</button>';
        }
        var bDisabled = !dictContext.setStepsWithData.has(iIndex);
        var bHasExistingTests =
            (step.saTestCommands || []).length > 0;
        var sLabel;
        if (bDisabled) {
            sLabel = "No Data for Tests";
        } else if (bHasExistingTests) {
            sLabel = "Replace Tests";
        } else {
            sLabel = "Generate Tests";
        }
        return '<button class="btn-generate-test" data-step="' +
            iIndex + '"' +
            (bDisabled ? " disabled" : "") +
            ' id="btnGenTest' + iIndex + '">' +
            sLabel + '</button>';
    }

    function fsRenderTestSection(
        sLabel, listItems, iIndex, sType, dictContext
    ) {
        var sHtml = '<div class="test-section-label">' + sLabel +
            ' <button class="section-add test-add" data-step="' +
            iIndex + '" data-test-type="' + sType +
            '" title="Add">+</button></div>';
        if (!listItems || listItems.length === 0) return sHtml;
        for (var i = 0; i < listItems.length; i++) {
            var sCls = sType === "file" ?
                "test-file-item" : "test-command-item";
            sHtml += '<div class="' + sCls + '" data-step="' +
                iIndex + '" data-idx="' + i + '">' +
                '<span class="test-item-text">' +
                fnEscapeHtml(dictContext.fsResolveTemplate(
                    listItems[i],
                    dictContext.fdictBuildClientVariables())) +
                '</span>' +
                '<span class="test-item-actions">' +
                '<button class="btn-icon test-edit-cmd" ' +
                'data-step="' + iIndex + '" data-idx="' + i +
                '" title="Edit test file">&#9998;</button>' +
                '<button class="btn-icon test-delete-cmd" ' +
                'data-step="' + iIndex + '" data-idx="' + i +
                '" title="Delete test">&times;</button>' +
                '</span></div>';
        }
        return sHtml;
    }

    function fsRenderRunStats(step) {
        var dictStats = step.dictRunStats || {};
        var sWallClock = dictStats.fWallClock !== undefined ?
            fsFormatDuration(dictStats.fWallClock) : "";
        var sCpuTime = dictStats.fCpuTime !== undefined ?
            fsFormatDuration(dictStats.fCpuTime) : "";
        return '<div class="run-stats">' +
            '<span class="run-stat">Wall-clock: ' +
            (sWallClock || "\u2014") + '</span>' +
            '<span class="run-stat">CPU time: ' +
            (sCpuTime || "\u2014") + '</span></div>';
    }

    function fsRenderDataMtime(iIndex, dictContext) {
        var sMtime = (
            dictContext.dictMaxDataMtimeByStep || {}
        )[String(iIndex)];
        if (!sMtime) return "";
        return '<div class="run-stats"><span class="run-stat">' +
            'Data files last modified: ' +
            fsFormatUnixTimestamp(sMtime) +
            '</span></div>';
    }

    function fsRenderPlotMtime(iIndex, dictContext) {
        var sMtime = (
            dictContext.dictMaxPlotMtimeByStep || {}
        )[String(iIndex)];
        if (!sMtime) return "";
        return '<div class="run-stats"><span class="run-stat">' +
            'Plot files last modified: ' +
            fsFormatUnixTimestamp(sMtime) +
            '</span></div>';
    }

    function fsRenderOutputMtime(iIndex, dictContext) {
        var sOutputMtime = dictContext.dictOutputMtimes[String(iIndex)];
        if (!sOutputMtime) return "";
        return '<div class="run-stats"><span class="run-stat">' +
            'Outputs modified: ' +
            fsFormatUnixTimestamp(sOutputMtime) +
            '</span></div>';
    }

    function fsFormatDuration(fSeconds) {
        if (fSeconds < 60) return fSeconds.toFixed(1) + "s";
        var iMinutes = Math.floor(fSeconds / 60);
        var fRemainder = (fSeconds % 60).toFixed(0);
        if (iMinutes < 60) return iMinutes + "m " + fRemainder + "s";
        var iHours = Math.floor(iMinutes / 60);
        iMinutes = iMinutes % 60;
        return iHours + "h " + iMinutes + "m";
    }

    function fsFormatUnixTimestamp(sEpoch) {
        var d = new Date(parseInt(sEpoch, 10) * 1000);
        var sPad = function (i) {
            return String(i).padStart(2, "0");
        };
        return d.getUTCFullYear() + "-" +
            sPad(d.getUTCMonth() + 1) + "-" +
            sPad(d.getUTCDate()) + " " +
            sPad(d.getUTCHours()) + ":" +
            sPad(d.getUTCMinutes()) + " UTC";
    }

    function fsRenderVerificationTimestamp(sLabel, sTimestamp) {
        return '<div class="verification-timestamp">' +
            fnEscapeHtml(sLabel) + ": " +
            fnEscapeHtml(sTimestamp || "\u2014") + '</div>';
    }

    function fsRenderSectionLabel(sLabel, iStepIdx, sArrayKey) {
        return '<div class="detail-label">' +
            '<span>' + sLabel + '</span>' +
            '<button class="section-add" data-step="' + iStepIdx +
            '" data-array="' + sArrayKey +
            '" title="Add item">+</button>' +
            '</div>';
    }

    function fbIsInvalidOutputPath(sRaw, sResolved, sWorkdir) {
        if (!sResolved || sResolved.length === 0) return true;
        if (sRaw.includes("{")) return false;
        if (sResolved.startsWith("/")) return false;
        if (sWorkdir) return false;
        return true;
    }

    function fsRenderDetailItem(
        sRaw, dictVars, sType, sArrayKey, iStepIdx, iItemIdx,
        sWorkdir, dictContext
    ) {
        var sResolved = dictContext.fsResolveTemplate(sRaw, dictVars);
        if (sType === "output" && sWorkdir &&
            !sResolved.startsWith("/")) {
            sResolved = dictContext.fsJoinPath(sWorkdir, sResolved);
        }
        var sFileClass = "";
        var bInvalid = false;
        if (sType === "output") {
            if (fbIsInvalidOutputPath(sRaw, sResolved, sWorkdir)) {
                sFileClass = " file-invalid";
                bInvalid = true;
            }
        }

        var sHtml = '<div class="detail-item ' + sType +
            '" data-step="' + iStepIdx +
            '" data-array="' + sArrayKey +
            '" data-idx="' + iItemIdx +
            '" data-raw="' + fnEscapeHtml(sRaw) +
            '" data-resolved="' + fnEscapeHtml(sResolved) +
            '" data-workdir="' + fnEscapeHtml(sWorkdir || "") +
            '" draggable="true">';

        if (sType === "output" && !bInvalid) {
            sFileClass = " " + dictContext.fsInitialFileStatusClass(
                iStepIdx, sArrayKey, sRaw
            );
        }
        if ((sArrayKey === "saPlotFiles" ||
            sArrayKey === "saDataFiles") && !bInvalid) {
            if (typeof VaibifyGitBadges !== "undefined") {
                var dictTriple = VaibifyGitBadges.fdictGetBadgesForFile(
                    sResolved, sWorkdir
                );
                sHtml += VaibifyGitBadges.fsRenderBadgeRow(dictTriple);
            }
        }
        var sDisplayPath = dictContext.fsShortenPath(
            sResolved, sWorkdir);
        if (bInvalid) {
            sHtml += '<div class="detail-text file-invalid' +
                '" title="Output path is not absolute">' +
                '<em>' + fnEscapeHtml(sResolved) + '</em></div>';
        } else {
            sHtml += '<div class="detail-text' + sFileClass +
                '" title="' + fnEscapeHtml(sResolved) + '">' +
                fnEscapeHtml(sDisplayPath) + '</div>';
        }

        sHtml += '<div class="detail-actions">';
        if (sType === "output") {
            sHtml += '<button class="action-download" ' +
                'title="Download to host">' +
                '&#8615;</button>';
        }
        sHtml += '<button class="action-edit" title="Edit">&#9998;</button>' +
            '<button class="action-copy" title="Copy">&#9112;</button>' +
            '<button class="action-delete" title="Delete">&#10005;</button>' +
            '</div>';

        sHtml += '</div>';
        return sHtml;
    }

    function fsRenderPlotStandardButtons(iStepIndex) {
        return '<div class="plot-standard-button-row">' +
            '<button class="btn btn-make-standard" ' +
            'data-step="' + iStepIndex +
            '">Make Standard</button>' +
            '<button class="btn btn-compare-standard" ' +
            'data-step="' + iStepIndex +
            '">Compare to Standard</button></div>';
    }

    function fsRenderDiscoveredOutputs(iIndex, dictContext) {
        var dictDisc = dictContext.dictDiscoveredOutputs[iIndex];
        if (!dictDisc) return "";
        var listDiscovered = dictDisc.listDiscovered || [];
        if (listDiscovered.length === 0) return "";
        var iTotal = (typeof dictDisc.iTotalDiscovered === "number") ?
            dictDisc.iTotalDiscovered : listDiscovered.length;
        var sHtml = '<div class="detail-label discovered-label">' +
            'Discovered Outputs</div>';
        for (var i = 0; i < listDiscovered.length; i++) {
            var sFile = listDiscovered[i].sFilePath;
            sHtml += '<div class="discovered-item" data-step="' +
                iIndex + '" data-file="' +
                fnEscapeHtml(sFile) + '">' +
                '<span class="discovered-file">[+] ' +
                fnEscapeHtml(sFile) + '</span>' +
                '<button class="btn-discovered" ' +
                'data-target="saDataFiles">Add as data</button>' +
                '<button class="btn-discovered" ' +
                'data-target="saPlotFiles">Add as plot</button>' +
                '</div>';
        }
        if (iTotal > listDiscovered.length) {
            sHtml += '<div class="discovered-summary">' +
                'Showing ' + listDiscovered.length + ' of ' + iTotal +
                '. To see them all, raise iDiscoveryMaxDepth on this ' +
                'step or add a glob to saDataFiles / saPlotFiles.' +
                '</div>';
        }
        return sHtml;
    }

    return {
        fsRenderStepItem: fsRenderStepItem,
        fsRenderDetailItem: fsRenderDetailItem,
        fsRenderVerificationBlock: fsRenderVerificationBlock,
        fsRenderRunStepButton: fsRenderRunStepButton,
        fsRenderRunStats: fsRenderRunStats,
        fsRenderOutputMtime: fsRenderOutputMtime,
        fsRenderDataMtime: fsRenderDataMtime,
        fsRenderPlotMtime: fsRenderPlotMtime,
        fsRenderSectionLabel: fsRenderSectionLabel,
        fsRenderPlotStandardButtons: fsRenderPlotStandardButtons,
        fsRenderDiscoveredOutputs: fsRenderDiscoveredOutputs,
        fsRenderTestSection: fsRenderTestSection,
        fsRenderGenerateButton: fsRenderGenerateButton,
        fsFormatDuration: fsFormatDuration,
        fsFormatUnixTimestamp: fsFormatUnixTimestamp,
    };
})();
