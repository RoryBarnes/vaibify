/* Vaibify — Step rendering functions */

var VaibifyStepRenderer = (function () {
    "use strict";

    var fnEscapeHtml = VaibifyUtilities.fnEscapeHtml;
    var fsBuildLevelCell = VaibifyUtilities.fsBuildLevelCell;

    var _DICT_CATEGORY_TO_REMOTE_KEYS = {
        saPlotFiles: ["sGithub", "sOverleaf", "sZenodo", "sArxiv"],
        saOutputDataFiles: ["sGithub", "sZenodo"],
        saInputDataFiles: ["sGithub", "sZenodo"],
        saStepScripts: ["sGithub", "sZenodo"],
        saTestStandards: ["sGithub", "sZenodo"],
    };

    var _DICT_STALE_ROW_LABELS = {
        "test|dataScript": "Tests older than data scripts",
        "test|dataFile": "Tests older than output data",
        "test|inputFile": "Input data modified since last run",
        "user|dataScript": "User verification older than data scripts",
        "user|dataFile": "User verification older than output data",
        "user|plotScript": "User verification older than plot scripts",
        "user|plotFile": "User verification older than plot files",
        "user|inputFile": "User verification older than input data",
    };

    /* --- Level strip (Scope F) ---
       Four right-aligned columns on every step card and on the
       workflow header row (iIndex -1): the regression-warning
       column, then L1|L2|L3. Cells carry no level text — the column
       header row labels the columns once. Cell state and tooltip
       come from the application's level-state projection
       (``fsLevelCellState`` / ``fsLevelCellTooltip``). Cell visuals
       fill in as reality does: hollow grey circle = not started
       (nothing on disk), grey filled circle = unassessed (outputs
       exist, assessment not begun), red circle = none, orange
       circle = partial, favicon = attained, question mark =
       unknown (assessed once, answer stale), muted dash = not
       applicable (no requirement at this level for this step). The
       cell markup itself comes from the shared
       ``VaibifyUtilities.fsBuildLevelCell`` builder. */

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
        // Steps block. Every level has per-step requirements (L2:
        // published copies of this step's outputs; L3: manifest,
        // script pinning, determinism, binaries), so all three are
        // headed here; the project-scope requirements that attach to
        // no step live in the Project block.
        return '<div class="level-column-header-row">' +
            '<span class="run-column-header ' +
            'level-column-header-cell" ' +
            'title="Run controls — the checkbox includes a step in ' +
            'the next run; the light beside it shows live run ' +
            'activity and failures. Hover a light for detail.">' +
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
            '<span class="step-level-cell level-column-header-cell"' +
            ' title="Level 2 Published — this step\'s outputs match ' +
            'the published copies (GitHub, Zenodo, and any bound ' +
            'manuscript figures). A dash means the step has no ' +
            'requirements at this level.">L2</span>' +
            '<span class="step-level-cell level-column-header-cell"' +
            ' title="Level 3 Reproducible — this step\'s scripts and ' +
            'outputs are pinned in the manifest, its randomness is ' +
            'declared, and any binaries it invokes are declared and ' +
            'captured. A dash means the step has no requirements at ' +
            'this level.">L3</span>' +
            '</span></div>';
    }

    var _DICT_STEP_STATUS_TITLES = {
        "": "not run in this session",
        "pass": "last run succeeded — details in the expanded "
            + "step's Last run line",
        "fail": "last run failed",
        "queued": "queued in the current run",
        "running": "running now",
        "overBudget": "running longer than its wall-clock budget — "
            + "may be hung; check the container",
        "skipped": "skipped in the last run",
    };

    function _fsBuildStepStatusCell(sRunStatus) {
        // Vocabulary: hollow grey = never run this session, filled
        // grey = queued, blinking orange = running, red = failed,
        // blinking red = over budget. A successful run renders a
        // quiet empty cell: the vaibify check is reserved for
        // attained level cells, and a success check beside an
        // unverified step read as a false Level 1 claim (2026-07-17
        // ruling). Success detail lives in the expanded step's
        // Last run line.
        var sTitle = "Run status: " +
            (_DICT_STEP_STATUS_TITLES[sRunStatus] || sRunStatus);
        var sInner = sRunStatus === "pass"
            ? ""
            : '<span class="step-status ' + sRunStatus + '"></span>';
        return '<span class="step-status-cell" title="' +
            fnEscapeHtml(sTitle) + '">' + sInner + '</span>';
    }

    function _fsBuildStepLevelStrip(dictContext, iIndex) {
        // Both scopes show the full ⚠ + L1 + L2 + L3 strip. Step
        // scope (iIndex >= 0) renders the step's own requirement
        // cells (a dash when a level has none); the workflow scope
        // (iIndex < 0, the Project banner) renders the project-wide
        // requirements that attach to no single step.
        if (!dictContext.fsLevelCellState) return "";
        var iMaxLevel = 3;
        var sHtml = '<span class="step-level-strip">' +
            _fsBuildRegressionCell(dictContext, iIndex);
        for (var iLevel = 1; iLevel <= iMaxLevel; iLevel++) {
            // Step-scope cells carry data-level so a click can open
            // the detail onto that rung's section (the banner as a
            // table of contents); the Project banner has no
            // per-step sections to open.
            sHtml += fsBuildLevelCell(
                dictContext.fsLevelCellState(iIndex, iLevel),
                dictContext.fsLevelCellTooltip(iIndex, iLevel),
                "",
                iIndex >= 0 ? ' data-level="' + iLevel + '"' : "");
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

    /* --- Hierarchical level sections (Step Viewer, 2026-07-18) ---
       The expanded detail is three expandable sections — Level 1
       (the workbench: the step's own artifacts ARE its
       self-consistency surface), Level 2 and Level 3 (requirement
       rows). Every row renders the cell's ``listRequirements``
       breakdown shipped by the levelGates projection — the SAME
       list its counts derive from, never a second computation.
       Project-scoped remedies name their Project-block section
       rather than duplicating the action here; the github/zenodo
       rows reuse the shared .wf-verify-remote handler. */

    var _DICT_STEP_LEVEL_NAMES = {
        1: "Level 1 — Self-Consistent",
        2: "Level 2 — Published",
        3: "Level 3 — Reproducible",
    };

    var _DICT_REQUIREMENT_LABELS = {
        "sUnitTest": "Unit tests pass",
        "sIntegrity": "Integrity tests pass",
        "sQualitative": "Qualitative tests pass",
        "sQuantitative": "Quantitative tests pass",
        "user-attestation": "Your sign-off recorded",
        "timing-clean": "Nothing changed since verification",
        "input-data-declared": "Input data declared",
        "github-mirror": "Outputs match the GitHub mirror",
        "zenodo-deposit": "Outputs match the Zenodo deposit",
        "figure-frozen": "Manuscript figures frozen in Overleaf",
        "ai-declaration-attested": "AI declaration signed off",
        "missing-from-manifest": "Outputs pinned in the manifest",
        "script-not-pinned": "Scripts match the manifest",
        "nondeterminism-undeclared": "Randomness seeded or declared",
        "binary-not-declared": "Invoked binaries declared",
        "binary-not-captured": "Binary versions captured",
        "binary-drifted": "Binaries match their captured hashes",
    };

    var _DICT_UNMET_REQUIREMENT_HINTS = {
        "figure-frozen": "Freeze via each plot file's Overleaf " +
            "badge, then re-verify",
        "ai-declaration-attested": "Sign off in the declaration " +
            "body above",
        "missing-from-manifest": "Refresh the manifest (Project " +
            "block → Artifacts)",
        "script-not-pinned": "Script changed since the manifest " +
            "was written — re-run or refresh the manifest " +
            "(Project block → Artifacts)",
        "nondeterminism-undeclared": "Seed the RNG or declare the " +
            "randomness (Project block → Determinism)",
        "binary-not-declared": "Declare the binary (Project block " +
            "→ Software)",
        "binary-not-captured": "Capture its version + SHA " +
            "(Project block → Software)",
        "binary-drifted": "Re-run with the current binary and " +
            "re-capture, or restore the published binary",
    };

    // Row name -> the blocker-wire criterion that carries its
    // offending-file list.
    var _DICT_REQUIREMENT_BLOCKER_CRITERIA = {
        "github-mirror": "not-in-github-mirror",
        "zenodo-deposit": "not-in-zenodo-deposit",
        "figure-frozen": "figure-not-frozen",
    };

    var _DICT_VERIFY_SERVICE_BY_REQUIREMENT = {
        "github-mirror": "github",
        "zenodo-deposit": "zenodo",
    };

    function _fdictStepLevelCell(dictContext, iIndex, iLevel) {
        var dictLevels = (dictContext.dictStepLevels || {})[
            String(iIndex)] || {};
        var dictCell = dictLevels["s" + iLevel];
        return (dictCell && typeof dictCell === "object")
            ? dictCell : null;
    }

    function _fsBuildRequirementMark(bMet) {
        if (bMet === true) {
            return VaibifyUtilities.fsBuildAttainedFavicon(
                "met", "Requirement met");
        }
        if (bMet === false) {
            return '<span class="envelope-warn" title="Requirement ' +
                'not met">&#9888;</span>';
        }
        return '<span class="envelope-light envelope-light-unknown"' +
            ' title="Not verifiable right now — the last remote ' +
            'verify is stale"></span>';
    }

    function _fsRequirementLabel(sName) {
        return _DICT_REQUIREMENT_LABELS[sName] || sName;
    }

    function _fsLevelSectionCounts(dictContext, iIndex, iLevel) {
        // Compact "6/7" summary; the header's cell carries the
        // not-applicable dash and the tooltip carries the prose, so
        // an empty string is honest for those cases.
        var sState = dictContext.fsLevelCellState(iIndex, iLevel);
        if (sState === "not-applicable") return "";
        var dictCell = _fdictStepLevelCell(dictContext, iIndex, iLevel);
        if (!dictCell) return "";
        return dictCell.iSatisfied + "/" + dictCell.iTotal;
    }

    function _fsRenderStepLevelSectionHeader(
        iIndex, iLevel, dictContext, bExpanded
    ) {
        return '<div class="step-level-section-header"' +
            ' data-step="' + iIndex + '"' +
            ' data-level="' + iLevel + '">' +
            '<span class="step-level-section-caret">' +
            (bExpanded ? "&#9662;" : "&#9656;") + '</span>' +
            fsBuildLevelCell(
                dictContext.fsLevelCellState(iIndex, iLevel),
                dictContext.fsLevelCellTooltip(iIndex, iLevel)) +
            '<span class="step-level-section-title">' +
            _DICT_STEP_LEVEL_NAMES[iLevel] + '</span>' +
            '<span class="step-level-section-counts">' +
            fnEscapeHtml(
                _fsLevelSectionCounts(dictContext, iIndex, iLevel)) +
            '</span>' +
            '<span class="step-level-info"' +
            ' data-step="' + iIndex + '"' +
            ' data-level="' + iLevel + '"' +
            ' title="Show every Level ' + iLevel +
            ' requirement for this step">&#9432;</span>' +
            '</div>';
    }

    function _flistRequirementOffenders(dictReq, iIndex, iLevel,
        dictContext) {
        var dictBlockerMap = iLevel === 2
            ? dictContext.dictBlockersByStepLevel2
            : dictContext.dictBlockersByStepLevel3;
        var dictEntry = (dictBlockerMap || {})[iIndex];
        if (!dictEntry) return [];
        var sCriterion =
            _DICT_REQUIREMENT_BLOCKER_CRITERIA[dictReq.sName]
            || dictReq.sName;
        if (dictEntry.sCriterion !== sCriterion) return [];
        return dictEntry.listOffendingFiles || [];
    }

    function _fsRequirementHintHtml(dictReq, iIndex, iLevel,
        dictContext) {
        if (dictReq.bMet === true) return "";
        var listParts = [];
        var listOffenders = _flistRequirementOffenders(
            dictReq, iIndex, iLevel, dictContext);
        if (listOffenders.length > 0) {
            listParts.push("Differs: " + listOffenders.map(
                function (sPath) {
                    return sPath.split("/").pop();
                }).join(", "));
        }
        var sHint = _DICT_UNMET_REQUIREMENT_HINTS[dictReq.sName];
        if (sHint) listParts.push(sHint);
        if (listParts.length === 0) return "";
        return '<div class="step-level-requirement-hint">' +
            fnEscapeHtml(listParts.join(" — ")) + '</div>';
    }

    function _fsRequirementActionHtml(dictReq) {
        // Only actions with an existing shared handler render here;
        // everything else stays a hint so the one real control is
        // never duplicated.
        var sService =
            _DICT_VERIFY_SERVICE_BY_REQUIREMENT[dictReq.sName];
        if (!sService || dictReq.bMet === true) return "";
        return ' <button class="btn wf-verify-remote ' +
            'step-level-verify" data-service="' + sService +
            '">Verify now</button>';
    }

    function _fsRenderLevelRequirementRows(iIndex, iLevel,
        dictContext) {
        var dictCell = _fdictStepLevelCell(dictContext, iIndex, iLevel);
        var listRequirements =
            (dictCell && dictCell.listRequirements) || [];
        if (listRequirements.length === 0) {
            return '<div class="detail-note">This step has no ' +
                'requirements at this level.</div>';
        }
        var sHtml = "";
        listRequirements.forEach(function (dictReq) {
            sHtml += '<div class="step-level-requirement-row">' +
                _fsBuildRequirementMark(dictReq.bMet) +
                '<span class="step-level-requirement-label">' +
                fnEscapeHtml(_fsRequirementLabel(dictReq.sName)) +
                '</span>' +
                _fsRequirementActionHtml(dictReq) +
                '</div>' +
                _fsRequirementHintHtml(
                    dictReq, iIndex, iLevel, dictContext);
        });
        return sHtml;
    }

    function _fsRequirementMarkMeaning(bMet) {
        if (bMet === true) return "(check = requirement met)";
        if (bMet === false) return "(⚠ = requirement not met)";
        return "(hollow circle = not verifiable right now — " +
            "the last remote verify is stale)";
    }

    function fsBuildLevelRequirementsListHtml(dictCell, iLevel) {
        // The ⓘ modal body: every requirement of one rung with its
        // live mark — built from the same wire list the cell counts
        // derive from — and a parenthetical spelling the mark out.
        var listRequirements = dictCell.listRequirements || [];
        if (listRequirements.length === 0) {
            return '<div class="detail-note">This step has no ' +
                'Level ' + iLevel + ' requirements.</div>';
        }
        return listRequirements.map(function (dictReq) {
            return '<div class="step-level-requirement-row">' +
                _fsBuildRequirementMark(dictReq.bMet) +
                '<span class="step-level-requirement-label">' +
                fnEscapeHtml(_fsRequirementLabel(dictReq.sName)) +
                '</span>' +
                '<span class="step-level-requirement-meaning">' +
                fnEscapeHtml(
                    _fsRequirementMarkMeaning(dictReq.bMet)) +
                '</span></div>';
        }).join("");
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
            'title="Include this step when running the project"' +
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

        // Hierarchical detail (2026-07-18): an optional Description
        // block, then one expandable section per ladder rung. The
        // Level 1 body is the workbench — the step's own artifacts
        // ARE its self-consistency surface. No Directory display:
        // the rename cascade pins the directory to the step name,
        // so the row's name already says it.
        sHtml += '<div class="step-detail expanded' +
            '" data-index="' + iIndex + '">';

        var sResolvedDir = dictContext.fsResolveTemplate(
            step.sDirectory || "", dictVars);
        sHtml += _fsRenderStepDescriptionBlock(
            step, iIndex, dictContext);

        for (var iLevel = 1; iLevel <= 3; iLevel++) {
            sHtml += _fsRenderStepLevelSection(
                step, iIndex, iLevel, dictVars, dictContext,
                sResolvedDir);
        }

        sHtml += "</div>";
        sHtml += "</div>";
        return sHtml;
    }

    function _fsRenderStepLevelSection(
        step, iIndex, iLevel, dictVars, dictContext, sResolvedDir
    ) {
        var bExpanded = dictContext.fbIsStepLevelExpanded
            ? dictContext.fbIsStepLevelExpanded(iIndex, iLevel)
            : iLevel === 1;
        var sHtml = '<div class="step-level-section">' +
            _fsRenderStepLevelSectionHeader(
                iIndex, iLevel, dictContext, bExpanded);
        if (!bExpanded) {
            return sHtml + '</div>';
        }
        sHtml += '<div class="step-level-section-body">';
        if (iLevel === 1) {
            sHtml += step.sStepKind === "ai-declaration"
                ? '<div class="detail-note">This step has no ' +
                    'requirements at this level.</div>'
                : _fsRenderLevelOneBody(
                    step, iIndex, dictVars, dictContext,
                    sResolvedDir);
        } else {
            if (iLevel === 2 && step.sStepKind === "ai-declaration") {
                sHtml += fsRenderAiDeclarationBody(
                    step, iIndex, dictContext);
            }
            sHtml += _fsRenderLevelRequirementRows(
                iIndex, iLevel, dictContext);
        }
        return sHtml + '</div></div>';
    }

    function _fsRenderLevelOneBody(
        step, iIndex, dictVars, dictContext, sResolvedDir
    ) {
        var bInteractive = step.bInteractive === true;
        var sHtml = "";
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

        sHtml += fsRenderInputDataSection(
            step, iIndex, dictVars, dictContext
        );

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
                    iIndex, iCmdIdx, sResolvedDir, dictContext
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
                fsRenderDataMtime(iIndex, dictContext) + '</div>';
        }

        sHtml += fsRenderSectionLabel(
            "Output Data", iIndex, "saOutputDataFiles"
        );
        if (step.saOutputDataFiles) {
            step.saOutputDataFiles.forEach(function (sFile, iFileIdx) {
                sHtml += fsRenderDetailItem(
                    sFile, dictVars, "output", "saOutputDataFiles",
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
                    iIndex, iCmdIdx, sResolvedDir, dictContext
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
        sHtml += fsRenderLastRunLine(step, iIndex, dictContext);
        return sHtml;
    }

    function _fsRenderStepDescriptionBlock(step, iIndex, dictContext) {
        // Optional researcher/agent-authored prose on what the step
        // does (sDescription). The header always renders so the
        // affordance is discoverable; the body opens seeded on
        // existing text, and clicking the text opens the inline
        // editor.
        var bExpanded = dictContext.fbIsStepDescriptionExpanded
            ? dictContext.fbIsStepDescriptionExpanded(iIndex)
            : false;
        var sHtml = '<div class="step-description-block">' +
            '<div class="step-description-header"' +
            ' data-step="' + iIndex + '"' +
            ' title="A brief note on what this step does — ' +
            'optional, written by you or an agent">' +
            '<span class="step-level-section-caret">' +
            (bExpanded ? "&#9662;" : "&#9656;") + '</span>' +
            '<span class="step-level-section-title">' +
            'Description</span></div>';
        if (!bExpanded) {
            return sHtml + '</div>';
        }
        var sText = (step.sDescription || "").trim();
        sHtml += '<div class="step-description-body"' +
            ' data-step="' + iIndex + '">' +
            '<div class="step-description-text' +
            (sText ? "" : " step-description-placeholder") + '"' +
            ' data-step="' + iIndex + '"' +
            ' title="Click to edit">' +
            (sText
                ? fnEscapeHtml(sText)
                : "Add a few sentences on what this step " +
                    "does…") +
            '</div></div>';
        return sHtml + '</div>';
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

    function _fsLastRunOutcome(step, iIndex, dictContext) {
        // This session's live status wins; otherwise the persisted
        // exit code speaks; stats recorded before outcomes were
        // kept yield no outcome claim at all.
        var sSession = dictContext.dictStepStatus[iIndex] || "";
        if (sSession === "running" || sSession === "queued"
            || sSession === "overBudget") {
            return "in progress";
        }
        if (sSession === "pass") return "succeeded";
        if (sSession === "fail") return "failed";
        var dictStats = step.dictRunStats || {};
        if (dictStats.iExitCode !== undefined) {
            return dictStats.iExitCode === 0 ? "succeeded" : "failed";
        }
        return "";
    }

    function fsRenderLastRunLine(step, iIndex, dictContext) {
        // The banner's run light went alarm-only (2026-07-17), so
        // this line is where a successful run's record lives:
        // outcome, finish stamp, and durations.
        var dictStats = step.dictRunStats || {};
        var sOutcome = _fsLastRunOutcome(step, iIndex, dictContext);
        if (!sOutcome && dictStats.fWallClock === undefined) {
            return '<div class="step-last-run">Last run: ' +
                'never run on this machine</div>';
        }
        var listParts = [];
        if (sOutcome) listParts.push(sOutcome);
        if (dictStats.sFinishedUtc) {
            listParts.push("finished " + fsFormatIsoTimestamp(
                dictStats.sFinishedUtc));
        }
        if (dictStats.fWallClock !== undefined) {
            listParts.push("wall-clock " +
                fsFormatDuration(dictStats.fWallClock));
        }
        if (dictStats.fCpuTime !== undefined) {
            listParts.push("CPU " +
                fsFormatDuration(dictStats.fCpuTime));
        }
        return '<div class="step-last-run">Last run: ' +
            fnEscapeHtml(listParts.join(" \u00b7 ")) + '</div>';
    }

    function fsRenderDataMtime(iIndex, dictContext) {
        var sMtime = (
            dictContext.dictMaxDataMtimeByStep || {}
        )[String(iIndex)];
        if (!sMtime) return "";
        return '<div class="run-stats"><span class="run-stat">' +
            'Output data last modified: ' +
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

    function fsFormatIsoTimestamp(sIso) {
        // "2026-07-17T14:02:09Z" -> "2026-07-17 14:02 UTC";
        // an unparseable stamp passes through verbatim.
        var d = new Date(sIso);
        if (isNaN(d.getTime())) return sIso;
        return fsFormatUnixTimestamp(String(
            Math.floor(d.getTime() / 1000)));
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

    /* Input Data: raw files a step consumes that no step produces.
       Entries are repo-relative, so rows resolve against the project
       repo root, never the step directory. The section renders even
       when empty so the + button and the explicit "No input data
       needed" declaration are always reachable — an undeclared step
       (no files listed, box unchecked) cannot reach AICS Level 1. */
    function fsRenderInputDataSection(
        step, iIndex, dictVars, dictContext
    ) {
        var sHtml = fsRenderSectionLabel(
            "Input Data", iIndex, "saInputDataFiles"
        );
        var listInputs = step.saInputDataFiles || [];
        listInputs.forEach(function (sFile, iFileIdx) {
            sHtml += fsRenderTrackedFileItem(
                sFile, dictVars, "saInputDataFiles", iIndex,
                iFileIdx, dictContext.sProjectRepoPath || "",
                dictContext
            );
        });
        if (listInputs.length === 0) {
            sHtml += fsRenderNoInputDataRow(step, iIndex);
        }
        return sHtml;
    }

    function fsRenderNoInputDataRow(step, iIndex) {
        var bDeclaredNone = step.bNoInputData === true;
        var sHtml = '<div class="detail-label plot-only-row">' +
            '<label class="plot-only-toggle" title="Check to declare' +
            ' explicitly that this step consumes no raw input data.' +
            ' A step reaches Level 1 only when it lists input files' +
            ' or carries this declaration.">' +
            '<input type="checkbox" class="no-input-data-checkbox"' +
            ' data-step="' + iIndex + '"' +
            (bDeclaredNone ? " checked" : "") + '>' +
            ' No input data needed</label></div>';
        if (!bDeclaredNone) {
            sHtml += '<div class="detail-note input-undeclared-note">' +
                '<span class="input-undeclared-glyph" ' +
                'title="This step cannot reach Level 1 until its ' +
                'input contract is declared">⚠</span> ' +
                'Input data undeclared &mdash; list the raw files this ' +
                'step reads, or check the box above.</div>';
        }
        return sHtml;
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
            '" data-workdir="' + fnEscapeHtml(sWorkdir || "") + '">';

        if (sType === "output" && !bInvalid) {
            sFileClass = " " + dictContext.fsInitialFileStatusClass(
                iStepIdx, sArrayKey, sRaw
            );
        }
        if (sType === "output" && !bInvalid &&
            (sArrayKey === "saOutputDataFiles" ||
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
            sArrayKey === "saOutputDataFiles") && !bInvalid) {
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

        sHtml += _fsBuildRowOverflowButton(
            iStepIdx, sArrayKey, iItemIdx, sResolved);

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
                'data-target="saOutputDataFiles">Add as data</button>' +
                '<button class="btn-discovered" ' +
                'data-target="saPlotFiles">Add as plot</button>' +
                '</div>';
        }
        if (iTotal > listDiscovered.length) {
            sHtml += '<div class="discovered-summary">' +
                'Showing ' + listDiscovered.length + ' of ' + iTotal +
                '. To see them all, raise iDiscoveryMaxDepth on this ' +
                'step or add a glob to saOutputDataFiles / saPlotFiles.' +
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
        fsRenderLastRunLine: fsRenderLastRunLine,
        fsBuildLevelRequirementsListHtml:
            fsBuildLevelRequirementsListHtml,
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
