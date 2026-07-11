/* Vaibify — Step rendering functions */

var VaibifyStepRenderer = (function () {
    "use strict";

    var fnEscapeHtml = VaibifyUtilities.fnEscapeHtml;
    var fsBuildLevelCell = VaibifyUtilities.fsBuildLevelCell;

    var _DICT_CATEGORY_TO_REMOTE_KEYS = {
        saPlotFiles: ["sGithub", "sOverleaf", "sZenodo", "sArxiv"],
        saDataFiles: ["sGithub", "sZenodo"],
        saStepScripts: ["sGithub", "sZenodo"],
        saTestStandards: ["sGithub", "sZenodo"],
    };

    var _DICT_STALE_ROW_LABELS = {
        "test|dataScript": "Tests older than data scripts",
        "test|dataFile": "Tests older than data files",
        "user|dataScript": "User verification older than data scripts",
        "user|dataFile": "User verification older than data files",
        "user|plotScript": "User verification older than plot scripts",
        "user|plotFile": "User verification older than plot files",
    };

    /* --- Level strip (Scope F) ---
       Four right-aligned columns on every step card and on the
       workflow header row (iIndex -1): the regression-warning
       column, then L1|L2|L3. Cells carry no level text — the column
       header row labels the columns once. Cell state and tooltip
       come from the application's level-state projection
       (``fsLevelCellState`` / ``fsLevelCellTooltip``). Cell visuals:
       grey filled circle = not started, red circle = none, orange
       circle = partial, favicon = attained, hollow grey circle =
       unknown, muted dash = not applicable (no requirement at this
       level for this step). The cell markup itself comes from the
       shared ``VaibifyUtilities.fsBuildLevelCell`` builder. */

    function _fsBuildRegressionCell(dictContext, iIndex) {
        var dictWarning = dictContext.fdictRegressionWarning
            ? dictContext.fdictRegressionWarning(iIndex) : null;
        if (!dictWarning) {
            return '<span class="step-regression-cell"></span>';
        }
        return '<span class="step-regression-cell ' +
            'regression-warning-' + dictWarning.sWarningSeverity +
            '" title="' +
            fnEscapeHtml(dictWarning.sWarningHint || "") +
            '">⚠</span>';
    }

    function _fsRenderLevelColumnHeaderRow() {
        // Labels the per-step status columns once at the top of the
        // Steps block. L1 is the only level that is a per-step
        // property; L2/L3 are project-wide and live in the
        // Project block, so they are not headed here.
        return '<div class="level-column-header-row">' +
            '<span class="run-column-header ' +
            'level-column-header-cell" ' +
            'title="Run controls — the checkbox includes a step in ' +
            'the next run; the light beside it shows what happened ' +
            'when the step last ran. Hover a light for detail.">' +
            'Run</span>' +
            '<span class="step-level-strip">' +
            '<span class="step-regression-cell ' +
            'level-column-header-cell" ' +
            'title="Warnings — a step that slipped back from a ' +
            'level it had reached, or whose results are out of ' +
            'date">&#9888;</span>' +
            '<span class="step-level-cell level-column-header-cell"' +
            ' title="Level 1 Self-Consistent — tests pass, files ' +
            'match, and you have signed off. A dash means the step ' +
            'has no requirements at this level.">L1</span>' +
            '</span></div>';
    }

    var _DICT_STEP_STATUS_TITLES = {
        "": "not run in this session",
        "pass": "last run succeeded",
        "fail": "last run failed",
        "queued": "queued in the current run",
        "running": "running now",
        "skipped": "skipped in the last run",
    };

    function _fsBuildStepStatusCell(sRunStatus) {
        // Vocabulary: hollow grey = never run this session, filled
        // grey = queued, blinking orange = running, red = failed,
        // the vaibify check (favicon) = last run succeeded.
        var sTitle = "Run status: " +
            (_DICT_STEP_STATUS_TITLES[sRunStatus] || sRunStatus);
        var sInner = sRunStatus === "pass"
            ? '<img src="/static/favicon.png" ' +
                'class="step-status-check" alt="last run succeeded">'
            : '<span class="step-status ' + sRunStatus + '"></span>';
        return '<span class="step-status-cell" title="' +
            fnEscapeHtml(sTitle) + '">' + sInner + '</span>';
    }

    function _fsBuildStepLevelStrip(dictContext, iIndex) {
        // Step scope (iIndex >= 0) shows only ⚠ + L1 — the levels that
        // are genuinely per-step. The workflow scope (iIndex < 0, the
        // Project banner) keeps the full ⚠ + L1 + L2 + L3
        // at-a-glance strip.
        if (!dictContext.fsLevelCellState) return "";
        var iMaxLevel = iIndex < 0 ? 3 : 1;
        var sHtml = '<span class="step-level-strip">' +
            _fsBuildRegressionCell(dictContext, iIndex);
        for (var iLevel = 1; iLevel <= iMaxLevel; iLevel++) {
            sHtml += fsBuildLevelCell(
                dictContext.fsLevelCellState(iIndex, iLevel),
                dictContext.fsLevelCellTooltip(iIndex, iLevel));
        }
        return sHtml + '</span>';
    }

    function fsBuildLevelStrip(dictContext, iIndex) {
        // Public wrapper so the Project block module can render
        // the -1 scope banner strip with the shared level-cell cells.
        return _fsBuildStepLevelStrip(dictContext, iIndex);
    }

    function fsRenderStepColumnHeader() {
        // The Run | ⚠ | L1 header row shown once atop the Steps block.
        return _fsRenderLevelColumnHeaderRow();
    }

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
        // The run light is EXECUTION-ONLY (its original meaning):
        // queued / running / how the last run ended. Verification
        // lives entirely in the ⚠ + L1/L2/L3 strip; the light and
        // the run checkbox form the execution cluster on the left
        // (intent + fact, side by side).
        var sRunStatus = dictContext.dictStepStatus[iIndex] || "";
        var bRunEnabled = step.bRunEnabled !== false;
        var bSelected = iIndex === dictContext.iSelectedStepIndex;
        var bExpanded = dictContext.setExpandedSteps.has(iIndex);

        var sStepNumber = dictContext.fsComputeStepLabel(iIndex);

        var sHtml = '<div class="step-wrapper" '
            + 'data-step-index="' + iIndex + '">' +
            '<div class="step-item' + (bSelected ? " selected" : "") +
            (bInteractive ? " interactive" : "") +
            '" data-index="' + iIndex + '" draggable="true">' +
            '<input type="checkbox" class="step-checkbox" ' +
            'title="Include this step when running the workflow"' +
            (bRunEnabled ? " checked" : "") + ">" +
            _fsBuildStepStatusCell(sRunStatus) +
            '<span class="step-number">' +
            sStepNumber + "</span>" +
            '<span class="step-name" title="' +
            fnEscapeHtml(step.sName) + '">' +
            fnEscapeHtml(step.sName) + "</span>" +
            // Every warning the step carries — staleness, blockers,
            // unseeded randomness, regressions — is consolidated
            // into the ⚠ column of the level strip, one
            // plain-English tooltip line per reason. No inline
            // glyphs render beside the step name; the per-file ✎/⚠
            // marks in the expanded detail still identify *which*
            // file went stale or missing.
            _fsBuildStepLevelStrip(dictContext, iIndex) +
            "</div>";

        if (!bExpanded) {
            return sHtml + '</div>';
        }

        sHtml += '<div class="step-detail expanded' +
            '" data-index="' + iIndex + '">';

        if (step.sStepKind === "ai-declaration") {
            sHtml += fsRenderAiDeclarationBody(
                step, iIndex, dictContext);
            sHtml += '</div></div>';
            return sHtml;
        }

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

        sHtml += fsRenderTrackedFileSection(
            "Scripts", "saStepScripts",
            step.saStepScripts || [], iIndex, dictVars,
            sResolvedDir, dictContext
        );

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

        sHtml += fsRenderTrackedFileSection(
            "Test Standards", "saTestStandards",
            step.saTestStandards || [], iIndex, dictVars,
            sResolvedDir, dictContext
        );

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
        var sStateClass = sState || "untested";
        return '<div class="sub-test-row expandable" data-step="' +
            iIndex + '" data-approver="' + sCategory + '">' +
            '<span class="verification-label">' +
            sTriangle + fnEscapeHtml(sLabel) + '</span>' +
            '<span class="verification-badge state-' +
            sStateClass + '">' +
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
        sHtml += fsRenderTestStandardsBadges(sStandardsPath);
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
        if (sCategory === "quantitative") {
            sHtml += fsRenderFalsificationBlock(iIndex, dictContext);
        }
        sHtml += '</div>';
        return sHtml;
    }

    /* --- Falsification attestation (non-gating) ---
       Renders the mutation-testing row inside the Quantitative
       Tests block. Honesty rules: "not applicable" is grey, never
       green; a recorded kill-rate states the tests' fault-detection
       sensitivity, never the result's accuracy; a digest-stale
       record renders stale, not fresh. */

    function _fsFalsificationRow(sBadgeState, sBadgeLabel) {
        return '<div class="sub-test-row">' +
            '<span class="verification-label">Falsification</span>' +
            '<span class="verification-badge state-' + sBadgeState +
            '">' + fnEscapeHtml(sBadgeLabel) + '</span></div>';
    }

    function _fsFalsificationNote(sText) {
        return '<div class="detail-note">' +
            fnEscapeHtml(sText) + '</div>';
    }

    function _fsFalsificationRunButton(iIndex, sLabel) {
        return '<div><button class="btn btn-run-falsification" ' +
            'data-step="' + iIndex + '">' + fnEscapeHtml(sLabel) +
            '</button></div>';
    }

    function fsRenderFalsificationBlock(iIndex, dictContext) {
        var dictState = dictContext.fdictGetFalsificationState ?
            dictContext.fdictGetFalsificationState(iIndex) : null;
        var sHtml = '<div class="falsification-block">';
        if (!dictState) {
            sHtml += _fsFalsificationRow("untested", "not checked");
        } else if (dictState.dictInFlight) {
            sHtml += _fsFalsificationRow(
                "stale", "running…");
            sHtml += _fsFalsificationNote(
                "Mutation testing in progress: injecting faults " +
                "and re-running the step per mutant.");
        } else if (!dictState.dictApplicability ||
                   !dictState.dictApplicability.bApplicable) {
            sHtml += _fsFalsificationRow("untested", "not applicable");
            sHtml += _fsFalsificationNote(
                (dictState.dictApplicability || {}).sReason ||
                "This step cannot be mutation-tested.");
        } else {
            sHtml += _fsRenderFalsificationVerdict(
                iIndex, dictState);
        }
        sHtml += '</div>';
        return sHtml;
    }

    function _fsRenderFalsificationVerdict(iIndex, dictState) {
        var dictRecord = dictState.dictRecord;
        if (!dictRecord) {
            return _fsFalsificationRow("untested", "not run") +
                _fsFalsificationNote(
                    "Would these tests notice if this step's code " +
                    "broke? Mutation testing answers by injecting " +
                    "deliberate faults.") +
                _fsFalsificationRunButton(iIndex, "Check test teeth");
        }
        if (dictRecord.sStatus === "error") {
            return _fsFalsificationRow("failed", "error") +
                _fsFalsificationNote(dictRecord.sReason ||
                    "The mutation run failed.") +
                _fsFalsificationRunButton(iIndex, "Retry");
        }
        if (!dictState.bRecordCurrent) {
            return _fsFalsificationRow("stale", "stale") +
                _fsFalsificationNote(
                    "The step's code or standards changed since " +
                    "this kill-rate was recorded.") +
                _fsFalsificationRunButton(iIndex, "Re-check test teeth");
        }
        return _fsRenderFalsificationKillRate(iIndex, dictRecord);
    }

    function _fsRenderFalsificationKillRate(iIndex, dictRecord) {
        var iPercent = Math.round((dictRecord.fKillRate || 0) * 100);
        var sHtml = _fsFalsificationRow(
            "passed", iPercent + "% killed");
        sHtml += _fsFalsificationNote(
            dictRecord.iMutantsKilled + " of " +
            dictRecord.iMutantsTotal + " injected faults were " +
            "detected by the quantitative tests (" +
            dictRecord.iMutantsSurvived + " survived). This " +
            "measures the tests' fault-detection sensitivity, not " +
            "the result's accuracy; surviving mutants may be " +
            "equivalent (no observable effect).");
        var listSurvivors = dictRecord.listSurvivors || [];
        for (var i = 0; i < listSurvivors.length && i < 5; i++) {
            sHtml += _fsFalsificationNote(
                "survivor: " + listSurvivors[i].sModulePath + ":" +
                listSurvivors[i].iLine + " (" +
                listSurvivors[i].sOperator + ")");
        }
        if (listSurvivors.length > 5) {
            sHtml += _fsFalsificationNote(
                "… and " + (listSurvivors.length - 5) +
                " more survivors (see the record in " +
                ".vaibify/falsification/).");
        }
        sHtml += _fsFalsificationRunButton(
            iIndex, "Re-check test teeth");
        return sHtml;
    }

    function fsRenderTestStandardsBadges(sStandardsPath) {
        if (!sStandardsPath) return "";
        var sBadgeRow = _fsBuildTrackedFileBadgeRow(
            sStandardsPath, "saTestStandards", "");
        if (!sBadgeRow) return "";
        return '<div class="sub-test-badges">' + sBadgeRow + '</div>';
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
        var sGlyph = "";
        if (dictContext.fbUpstreamStepIsL1Offending &&
            dictContext.fbUpstreamStepIsL1Offending(iIndex, iDep)) {
            var sHint = (dictContext.fsBlockerHintForStep &&
                dictContext.fsBlockerHintForStep(iIndex)) ||
                "Upstream outputs newer than this step; re-run to clear";
            sGlyph = " " + dictContext.fsBuildL1FailureGlyph(sHint);
        }
        return '<div class="dep-item">' +
            '<div class="dep-header"><span class="dep-label">' +
            sNum + ' ' + fnEscapeHtml(depStep.sName) + sGlyph +
            '</span></div>' +
            fsRenderDepAxisRow(
                "Step Status", tStates.sStepStatus, "", dictContext) +
            fsRenderDepAxisRow(
                "Timing", tStates.sTiming,
                fsFormatTimingDetail(tStates), dictContext) +
            '</div>';
    }

    function fsRenderDepAxisRow(sLabel, sState, sDetail, dictContext) {
        var sBadgeState = (sState === "unknown" || !sState)
            ? "untested" : sState;
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

    function fsRenderReadOnlySectionLabel(sLabel) {
        return '<div class="detail-label"><span>' +
            fnEscapeHtml(sLabel) + '</span></div>';
    }

    function fsRenderTrackedFileItem(
        sRaw, dictVars, sArrayKey, iStepIdx, iItemIdx,
        sWorkdir, dictContext
    ) {
        var sResolved = dictContext.fsResolveTemplate(sRaw, dictVars);
        var sHtml = '<div class="detail-item tracked-file" ' +
            'data-step="' + iStepIdx +
            '" data-array="' + sArrayKey +
            '" data-idx="' + iItemIdx +
            '" data-raw="' + fnEscapeHtml(sRaw) +
            '" data-resolved="' + fnEscapeHtml(sResolved) +
            '" data-workdir="' + fnEscapeHtml(sWorkdir || "") + '">';
        sHtml += _fsBuildTrackedFileBadgeRow(
            sResolved, sArrayKey, "");
        var sDisplayPath = dictContext.fsShortenPath(
            sResolved, sWorkdir);
        sHtml += '<div class="detail-text" title="' +
            fnEscapeHtml(sResolved) + '">' +
            fnEscapeHtml(sDisplayPath) + '</div>';
        sHtml += _fsBuildRowOverflowButton(
            iStepIdx, sArrayKey, iItemIdx, sResolved);
        sHtml += '</div>';
        return sHtml;
    }

    function _fsBuildRowOverflowButton(
        iStepIdx, sArrayKey, iItemIdx, sResolved
    ) {
        return '<button type="button" class="row-overflow-btn" ' +
            'aria-label="More actions" title="More actions" ' +
            'data-resolved="' + fnEscapeHtml(sResolved) +
            '" data-step="' + iStepIdx +
            '" data-array="' + fnEscapeHtml(sArrayKey) +
            '" data-idx="' + iItemIdx +
            '">⋯</button>';
    }

    function _fsBuildTrackedFileBadgeRow(
        sResolved, sArrayKey, sWorkdir
    ) {
        if (typeof VaibifyGitBadges === "undefined") return "";
        var dictTriple = VaibifyGitBadges.fdictGetBadgesForFile(
            sResolved, sWorkdir || ""
        );
        var aRemoteKeys = _DICT_CATEGORY_TO_REMOTE_KEYS[sArrayKey]
            || ["sGithub", "sZenodo"];
        return VaibifyGitBadges.fsRenderBadgeRow(
            dictTriple, aRemoteKeys);
    }

    function fsRenderTrackedFileSection(
        sLabel, sArrayKey, listFiles, iStepIdx, dictVars,
        sWorkdir, dictContext
    ) {
        if (!listFiles || listFiles.length === 0) return "";
        var sHtml = fsRenderReadOnlySectionLabel(sLabel);
        listFiles.forEach(function (sFile, iFileIdx) {
            sHtml += fsRenderTrackedFileItem(
                sFile, dictVars, sArrayKey, iStepIdx, iFileIdx,
                sWorkdir, dictContext
            );
        });
        return sHtml;
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
        if (sType === "output" && !bInvalid &&
            (sArrayKey === "saDataFiles" ||
                sArrayKey === "saPlotFiles") &&
            dictContext.fbFileIsL1Offending &&
            dictContext.fbFileIsL1Offending(iStepIdx, sRaw)) {
            var sFileHint = (dictContext.fsBlockerHintForFile &&
                dictContext.fsBlockerHintForFile(iStepIdx, sRaw)) ||
                "Blocking L1: re-run step to clear";
            sHtml += dictContext.fsBuildFileMarkGlyph
                ? dictContext.fsBuildFileMarkGlyph(
                    iStepIdx, sRaw, sFileHint)
                : dictContext.fsBuildL1FailureGlyph(sFileHint);
        }
        if ((sArrayKey === "saPlotFiles" ||
            sArrayKey === "saDataFiles") && !bInvalid) {
            sHtml += _fsBuildTrackedFileBadgeRow(
                sResolved, sArrayKey, sWorkdir);
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
        if (sType === "output" && !bInvalid &&
            (sArrayKey === "saPlotFiles" ||
                sArrayKey === "saDataFiles")) {
            sHtml += _fsBuildRowOverflowButton(
                iStepIdx, sArrayKey, iItemIdx, sResolved);
        }

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

    /* --- AI Declaration step kind ---
       A step with sStepKind === "ai-declaration" holds a markdown
       file path under sDeclarationFile and only an sUser attestation
       badge — no data/test/plot commands. The renderer below is the
       complete body for the expanded step detail; the standard step
       header (number, name, status dot) is unchanged. */

    function fsRenderAiDeclarationBody(step, iIndex, dictContext) {
        var sFilePath = (step.sDeclarationFile || "").trim();
        var sHtml = '<div class="ai-declaration-block" ' +
            'data-step="' + iIndex + '">';
        sHtml += '<div class="detail-label">' +
            'AI Usage Declaration</div>';
        sHtml += fsRenderAiDeclarationFileRow(sFilePath, iIndex);
        sHtml += fsRenderAiDeclarationViewer(sFilePath, iIndex);
        sHtml += fsRenderAiDeclarationAttestation(
            step, iIndex, dictContext);
        sHtml += '</div>';
        return sHtml;
    }

    function _fbDeclarationFileIsTracked(sFilePath) {
        // The GitHub badge column is plain git truth. Tracked states
        // (clean, modified, staged) offer removal; untracked, no
        // repo, or badges not yet loaded hide it — there is nothing
        // in git to remove.
        if (typeof VaibifyGitBadges === "undefined") return false;
        var dictBadges = VaibifyGitBadges.fdictGetBadgesForFile(
            sFilePath, "");
        var sState = (dictBadges && dictBadges.sGithub) || "";
        return sState === "synced" || sState === "dirty" ||
            sState === "drifted";
    }

    function _fsBuildDeclarationGitButtons(sFilePath, iIndex) {
        // Both actions coexist (researcher ruling 2026-07-02): an
        // updated declaration needs recommitting even while tracked,
        // so commit is always offered (pale blue, routine) and
        // removal appears once git tracks the file (orange, danger).
        var sHtml = ' <button class="btn btn-ai-declaration-commit" ' +
            'data-step="' + iIndex + '" ' +
            'data-file="' + fnEscapeHtml(sFilePath) + '" ' +
            'type="button" ' +
            'title="The declaration is a canonical file: it ' +
            'must be committed and pushed to count as ' +
            'published. This checks the repo and offers to ' +
            'commit just this file.">' +
            'Commit to repo&#8230;</button>';
        if (_fbDeclarationFileIsTracked(sFilePath)) {
            sHtml += ' <button class="btn btn-ai-declaration-untrack" ' +
                'data-step="' + iIndex + '" ' +
                'data-file="' + fnEscapeHtml(sFilePath) + '" ' +
                'type="button" ' +
                'title="Removes the declaration from git tracking — ' +
                'the file stays on disk, but it no longer counts ' +
                'as published.">' +
                'Remove from repo&#8230;</button>';
        }
        return sHtml;
    }

    function fsRenderAiDeclarationFileRow(sFilePath, iIndex) {
        if (sFilePath) {
            return '<div class="ai-declaration-file" ' +
                'data-step="' + iIndex + '">' +
                '<span class="ai-declaration-label">File:</span> ' +
                '<code>' + fnEscapeHtml(sFilePath) + '</code>' +
                ' <button class="btn btn-ai-declaration-choose" ' +
                'data-step="' + iIndex + '" type="button">' +
                'Choose different file</button>' +
                _fsBuildDeclarationGitButtons(sFilePath, iIndex) +
                '</div>';
        }
        return '<div class="ai-declaration-empty" ' +
            'data-step="' + iIndex + '">' +
            '<div class="ai-declaration-empty-message">' +
            'No declaration file is set for this step.</div>' +
            '<button class="btn btn-primary ' +
            'btn-ai-declaration-generate" ' +
            'data-step="' + iIndex + '" type="button">' +
            'Generate template (AI_USAGE.md)</button>' +
            ' <button class="btn btn-ai-declaration-choose" ' +
            'data-step="' + iIndex + '" type="button">' +
            'Choose existing file</button>' +
            '</div>';
    }

    function fsRenderAiDeclarationViewer(sFilePath, iIndex) {
        if (!sFilePath) return "";
        return '<div class="ai-declaration-viewer" ' +
            'data-step="' + iIndex + '" ' +
            'data-file="' + fnEscapeHtml(sFilePath) + '">' +
            '<pre class="ai-declaration-preview" ' +
            'data-file="' + fnEscapeHtml(sFilePath) + '">' +
            'Loading declaration preview…</pre>' +
            ' <button class="btn btn-ai-declaration-open" ' +
            'data-step="' + iIndex + '" ' +
            'data-file="' + fnEscapeHtml(sFilePath) + '" ' +
            'type="button">Open in viewer</button>' +
            '</div>';
    }

    var _I_DECLARATION_PREVIEW_LINES = 8;

    function fnFillAiDeclarationPreviews() {
        // Async fill of the preview shells fsRenderAiDeclarationViewer
        // rendered. Each shell is filled once per render of its card;
        // re-renders (hash change) produce a fresh shell and a fresh
        // fetch, so the preview tracks the file's real content.
        var listShells = document.querySelectorAll(
            ".ai-declaration-preview[data-file]");
        for (var i = 0; i < listShells.length; i++) {
            if (listShells[i].dataset.bFilled === "1") continue;
            listShells[i].dataset.bFilled = "1";
            _fnFetchDeclarationPreview(listShells[i]);
        }
    }

    function _fnFetchDeclarationPreview(elShell) {
        var sContainerId = PipeleyenApp.fsGetContainerId();
        if (!sContainerId) return;
        var sFilePath = elShell.dataset.file.replace(/^\/+/, "");
        var sRepoRoot = PipeleyenApp.fdictBuildClientVariables()
            .sRepoRoot || "";
        var sUrl = "/api/figure/" + sContainerId + "/" + sFilePath +
            "?sWorkdir=" + encodeURIComponent(sRepoRoot);
        VaibifyApi.fsGetText(sUrl).then(function (sText) {
            // textContent assignment never parses HTML, so the file
            // body cannot inject markup into the dashboard.
            elShell.textContent = _fsTruncateToLines(
                sText, _I_DECLARATION_PREVIEW_LINES);
        }).catch(function () {
            elShell.textContent =
                "Declaration file could not be read.";
        });
    }

    function _fsTruncateToLines(sText, iMaxLines) {
        var listLines = (sText || "").split("\n");
        if (listLines.length <= iMaxLines) return sText;
        return listLines.slice(0, iMaxLines).join("\n") + "\n…";
    }

    function fsRenderAiDeclarationAttestation(
        step, iIndex, dictContext
    ) {
        var dictVerify = dictContext.fdictGetVerification(step);
        var sUserState = dictVerify.sUser || "untested";
        var sHtml = '<div class="verification-block ' +
            'ai-declaration-attestation" data-step="' +
            iIndex + '">';
        sHtml += fsRenderVerificationRow(
            dictContext.sUserName, sUserState, "user", iIndex,
            dictContext
        );
        sHtml += '<div class="timestamp-field">' +
            fsRenderVerificationTimestamp(
                "Last updated", dictVerify.sLastUserUpdate) +
            '</div>';
        sHtml += '</div>';
        return sHtml;
    }

    return {
        fsRenderStepItem: fsRenderStepItem,
        fsBuildLevelStrip: fsBuildLevelStrip,
        fsRenderStepColumnHeader: fsRenderStepColumnHeader,
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
        fsRenderAiDeclarationBody: fsRenderAiDeclarationBody,
        fnFillAiDeclarationPreviews: fnFillAiDeclarationPreviews,
    };
})();
