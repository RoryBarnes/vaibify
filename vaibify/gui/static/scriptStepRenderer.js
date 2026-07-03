/* Vaibify — Step rendering functions */

var VaibifyStepRenderer = (function () {
    "use strict";

    var fnEscapeHtml = VaibifyUtilities.fnEscapeHtml;

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
       level for this step). */

    function _fsBuildLevelCellInner(sState) {
        if (sState === "attained") {
            return '<img src="/static/favicon.png" ' +
                'class="level-cell-favicon" alt="attained">';
        }
        if (sState === "not-applicable") {
            return '<span class="level-cell-dash">&#8212;</span>';
        }
        return '<span class="level-cell-circle"></span>';
    }

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
        // Labels the five status columns once at the top of the
        // list; every header carries a plain-English tooltip so each
        // column is identifiable. The level cells themselves carry
        // no L1/L2/L3 text.
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
            '<span class="step-level-cell level-column-header-cell"' +
            ' title="Level 2 Published — canonical files match ' +
            'GitHub and Zenodo, and the AI declaration is ' +
            'attested.">L2</span>' +
            '<span class="step-level-cell level-column-header-cell"' +
            ' title="Level 3 Reproducible — files pinned in the ' +
            'manifest, scripts unchanged since pinning, and ' +
            'software declared and captured.">L3</span>' +
            '</span></div>';
    }

    function _fsBuildLevelCell(sState, sTooltip) {
        return '<span class="step-level-cell level-cell-' + sState +
            '" title="' + fnEscapeHtml(sTooltip) + '">' +
            _fsBuildLevelCellInner(sState) + '</span>';
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
        if (!dictContext.fsLevelCellState) return "";
        var sHtml = '<span class="step-level-strip">' +
            _fsBuildRegressionCell(dictContext, iIndex);
        for (var iLevel = 1; iLevel <= 3; iLevel++) {
            sHtml += _fsBuildLevelCell(
                dictContext.fsLevelCellState(iIndex, iLevel),
                dictContext.fsLevelCellTooltip(iIndex, iLevel));
        }
        return sHtml + '</span>';
    }

    function fsRenderWorkflowLevelHeader(dictContext) {
        // Column header row + expandable workflow row (iIndex -1);
        // expanding reveals the reproducibility-envelope detail.
        var bExpanded = dictContext.setExpandedSteps &&
            dictContext.setExpandedSteps.has(-1);
        var sHtml = _fsRenderLevelColumnHeaderRow() +
            '<div class="workflow-level-header-row' +
            (bExpanded ? ' expanded' : '') + '">' +
            '<span class="expand-triangle">' +
            (bExpanded ? "▾" : "▸") + '</span>' +
            '<span class="workflow-level-header-label" ' +
            'title="Requirements that apply to the workflow as a ' +
            'whole rather than to any single step">Workflow-wide' +
            '</span>' +
            _fsBuildStepLevelStrip(dictContext, -1) +
            '</div>';
        if (bExpanded) {
            sHtml += fsRenderWorkflowEnvelopeDetail(
                dictContext.dictWorkflowEnvelopeDetail,
                dictContext.setExpandedEnvelopeSections);
        }
        return sHtml;
    }

    function fsRenderGhostAiDeclarationRow() {
        // Rendered after the last step when no ai-declaration step
        // exists: the missing step blocks L2 for the whole workflow.
        var sTooltip = "No AI declaration step — blocks L2 for the " +
            "whole workflow";
        var sHtml = '<div class="ghost-ai-declaration-row">' +
            '<span class="ghost-ai-declaration-label">' +
            'AI Declaration (missing)</span>' +
            '<button class="btn btn-add-ai-declaration-step" ' +
            'type="button">Add AI declaration step</button>' +
            '<span class="step-level-strip">' +
            '<span class="step-regression-cell"></span>';
        for (var iLevel = 1; iLevel <= 3; iLevel++) {
            sHtml += _fsBuildLevelCell("none", sTooltip);
        }
        return sHtml + '</span></div>';
    }

    /* --- Workflow envelope detail (Scope F) ---
       The expanded body of the workflow row, rendered verbatim from
       the poll's ``dictWorkflowEnvelopeDetail``: declared software
       with version/hash status lights, envelope artifacts,
       determinism declaration, and per-service remote sync state. A
       null remote-sync cache renders the hollow grey "never
       verified" light — never green. */

    var _DICT_ENVELOPE_ARTIFACT_LABELS = {
        manifest: "Manifest (MANIFEST.sha256)",
        dependencyLock: "Dependency lock (requirements.lock)",
        environmentSnapshot: "Environment snapshot",
        dockerfile: "Dockerfile",
        reproduceScript: "Reproduce script (reproduce.sh)",
    };

    var _LIST_ENVELOPE_SYNC_SERVICES = [
        "github", "zenodo", "overleaf", "arxiv"];

    function _fsBuildEnvelopeMark(sState, sTooltip) {
        // Pass renders the theme-tinted vaibify check (its color
        // follows --highlight-color, which climbs with the AICS
        // ladder); failures render warning glyphs; only "unknown"
        // keeps the hollow never-verified circle.
        if (sState === "green") {
            return '<span class="envelope-check" title="' +
                fnEscapeHtml(sTooltip) + '">&#10003;</span>';
        }
        if (sState === "red") {
            return '<span class="envelope-warn" title="' +
                fnEscapeHtml(sTooltip) + '">&#9888;</span>';
        }
        if (sState === "orange") {
            return '<span class="envelope-warn-orange" title="' +
                fnEscapeHtml(sTooltip) + '">&#9888;</span>';
        }
        return '<span class="envelope-light envelope-light-unknown"' +
            ' title="' + fnEscapeHtml(sTooltip) + '"></span>';
    }

    function _fsLightStateFromBoolean(bValue) {
        if (bValue === true) return "green";
        if (bValue === false) return "red";
        return "unknown";
    }

    var _DICT_ENVELOPE_SECTION_TITLES = {
        software: "Software",
        artifacts: "Artifacts",
        determinism: "Determinism",
        syncs: "Published copies",
    };

    var _DICT_ENVELOPE_SUMMARY_PHRASES = {
        "green": "everything checks out",
        "red": "needs attention — expand for detail",
        "orange": "out of date — expand for detail",
        "unknown": "not fully known — expand for detail",
    };

    function fsRenderWorkflowEnvelopeDetail(dictDetail, setExpanded) {
        var dictSafe = dictDetail || {};
        var setOpen = setExpanded || new Set();
        return '<div class="workflow-level-detail">' +
            _fsRenderEnvelopeSection("software", setOpen,
                _fsSoftwareSummaryState(dictSafe.listBinaries || []),
                _fsRenderEnvelopeSoftwareBody(
                    dictSafe.listBinaries || [])) +
            _fsRenderEnvelopeSection("artifacts", setOpen,
                _fsArtifactsSummaryState(dictSafe.dictArtifacts || {}),
                _fsRenderEnvelopeArtifactBody(
                    dictSafe.dictArtifacts || {})) +
            _fsRenderEnvelopeSection("determinism", setOpen,
                _fsDeterminismSummaryState(
                    dictSafe.dictDeterminism || null),
                _fsRenderEnvelopeDeterminismBody(
                    dictSafe.dictDeterminism || null)) +
            _fsRenderEnvelopeSection("syncs", setOpen,
                _fsSyncsSummaryState(dictSafe.dictRemoteSyncs || {}),
                _fsRenderEnvelopeSyncBody(
                    dictSafe.dictRemoteSyncs || {})) +
            '</div>';
    }

    function _fsRenderEnvelopeSection(
        sKey, setOpen, sSummaryState, sBodyHtml
    ) {
        var bOpen = setOpen.has(sKey);
        var sTooltip = _DICT_ENVELOPE_SECTION_TITLES[sKey] + ": " +
            _DICT_ENVELOPE_SUMMARY_PHRASES[sSummaryState];
        var sHtml = '<div class="envelope-section">' +
            '<div class="envelope-section-header" ' +
            'data-envelope-section="' + sKey + '">' +
            '<span class="expand-triangle">' +
            (bOpen ? "&#9662;" : "&#9656;") + '</span>' +
            '<span class="envelope-section-title">' +
            _DICT_ENVELOPE_SECTION_TITLES[sKey] + '</span>' +
            _fsBuildEnvelopeMark(sSummaryState, sTooltip) +
            '</div>';
        if (bOpen) {
            sHtml += '<div class="envelope-section-body">' +
                sBodyHtml + '</div>';
        }
        return sHtml + '</div>';
    }

    function _fsSummaryStateFromCounts(iSatisfied, iTotal) {
        // The section mark mirrors the level-cell vocabulary: all
        // requirements met = check, none = red, some = orange.
        if (iSatisfied >= iTotal) return "green";
        if (iSatisfied === 0) return "red";
        return "orange";
    }

    function _fsSoftwareSummaryState(listBinaries) {
        if (listBinaries.length === 0) return "unknown";
        var iSatisfied = 0;
        for (var i = 0; i < listBinaries.length; i++) {
            if (listBinaries[i].bVersionMatch === true) iSatisfied++;
            if (listBinaries[i].bHashCurrent === true) iSatisfied++;
        }
        return _fsSummaryStateFromCounts(
            iSatisfied, listBinaries.length * 2);
    }

    function _fsArtifactsSummaryState(dictArtifacts) {
        if (Object.keys(dictArtifacts).length === 0) return "unknown";
        var listKeys = Object.keys(_DICT_ENVELOPE_ARTIFACT_LABELS);
        var iSatisfied = 0;
        for (var i = 0; i < listKeys.length; i++) {
            var dictArtifact = dictArtifacts[listKeys[i]] || {};
            if (dictArtifact.bSatisfied === true) iSatisfied++;
        }
        return _fsSummaryStateFromCounts(iSatisfied, listKeys.length);
    }

    function _fsDeterminismSummaryState(dictDeterminism) {
        if (!dictDeterminism ||
                Object.keys(dictDeterminism).length === 0) {
            return "red";
        }
        return "green";
    }

    function _fsSyncsSummaryState(dictRemoteSyncs) {
        var iGreen = 0;
        var iKnown = 0;
        var bStale = false;
        for (var i = 0; i < _LIST_ENVELOPE_SYNC_SERVICES.length; i++) {
            var dictSync =
                dictRemoteSyncs[_LIST_ENVELOPE_SYNC_SERVICES[i]] ||
                null;
            if (!dictSync) continue;
            iKnown++;
            if ((dictSync.iDivergedCount || 0) > 0) return "red";
            if (dictSync.bStale === true) {
                bStale = true;
            } else {
                iGreen++;
            }
        }
        if (iKnown === 0) return "unknown";
        if (bStale ||
                iKnown < _LIST_ENVELOPE_SYNC_SERVICES.length) {
            // Partially verified: some services fresh, others stale
            // or never checked.
            return iGreen > 0 ? "orange" : "unknown";
        }
        return "green";
    }

    function _fsRenderEnvelopeSoftwareBody(listBinaries) {
        var sHtml = "";
        if (listBinaries.length === 0) {
            sHtml += '<div class="envelope-empty-note">' +
                'No declared binaries.</div>';
        } else {
            sHtml += _fsRenderEnvelopeMarkHeader([
                ["V", "Version — does the captured version match " +
                    "the declared one? Hover each mark below for " +
                    "the values and the fix."],
                ["H", "Hash — is the executable's SHA-256 recorded " +
                    "in the environment snapshot? Hover each mark " +
                    "below for the fix."],
            ]);
        }
        for (var i = 0; i < listBinaries.length; i++) {
            sHtml += _fsRenderEnvelopeBinaryRow(listBinaries[i]);
        }
        return sHtml + '<div class="envelope-repos-link-row">' +
            'Repository status and push actions live in the ' +
            '<a href="#" class="envelope-open-repos">Repos panel' +
            '</a>.</div>';
    }

    function _fsRenderEnvelopeMarkHeader(listColumns) {
        // A mini header row over the right-aligned mark columns —
        // one letter per column, each with an instructive tooltip.
        var sMarks = "";
        for (var i = 0; i < listColumns.length; i++) {
            sMarks += '<span class="envelope-mark-slot ' +
                'envelope-mark-header" title="' +
                fnEscapeHtml(listColumns[i][1]) + '">' +
                listColumns[i][0] + '</span>';
        }
        return '<div class="envelope-row-header">' +
            '<span class="envelope-row-marks">' + sMarks +
            '</span></div>';
    }

    function _fsWrapEnvelopeMarkSlot(sMarkHtml) {
        return '<span class="envelope-mark-slot">' + sMarkHtml +
            '</span>';
    }

    function _fsRenderEnvelopeBinaryRow(dictBinary) {
        // Mirrors the step-row pattern: name on the left, status
        // marks right-aligned. Version details and remedies live in
        // the mark tooltips, not inline text.
        return '<div class="envelope-binary-row">' +
            '<span class="envelope-binary-name">' +
            fnEscapeHtml(dictBinary.sBinaryPath || "") + '</span>' +
            '<span class="envelope-row-marks">' +
            _fsWrapEnvelopeMarkSlot(_fsBuildEnvelopeMark(
                _fsLightStateFromBoolean(dictBinary.bVersionMatch),
                _fsDescribeVersionMatch(dictBinary))) +
            _fsWrapEnvelopeMarkSlot(_fsBuildEnvelopeMark(
                dictBinary.bHashCurrent === true ? "green" : "red",
                dictBinary.bHashCurrent === true
                    ? "Hash captured in the environment snapshot"
                    : "Hash not captured — open the AICS tab's " +
                      "Level 3 Readiness card and use 'Capture " +
                      "version + SHA'")) +
            '</span></div>';
    }

    function _fsDescribeVersionMatch(dictBinary) {
        var sVersions = "declared " +
            (dictBinary.sExpectedVersion || "none") + ", captured " +
            (dictBinary.sCapturedVersion || "none");
        if (dictBinary.bVersionMatch === true) {
            return "Version matches the declaration (" + sVersions +
                ")";
        }
        if (dictBinary.bVersionMatch === false) {
            return "Version differs from the declaration (" +
                sVersions + ") — fix the declaration or rebuild " +
                "the binary, then recapture";
        }
        return "No version captured yet (" + sVersions + ") — open " +
            "the AICS tab's Level 3 Readiness card and use " +
            "'Capture version + SHA' to record it";
    }

    function _fsRenderEnvelopeArtifactBody(dictArtifacts) {
        var sHtml = "";
        if (Object.keys(dictArtifacts).length === 0) {
            return '<div class="envelope-empty-note">' +
                'No project repository detected — envelope ' +
                'artifacts unavailable.</div>';
        }
        sHtml += _fsRenderEnvelopeMarkHeader([
            ["F", "File — does this artifact exist in the project " +
                "repository? Missing files can be generated from " +
                "the AICS tab's Level 3 Readiness card."],
            ["R", "Requirement — does the file satisfy its Level 3 " +
                "check (pinned digests, hashed dependency locks, " +
                "an executable reproduce script)?"],
        ]);
        var listKeys = Object.keys(_DICT_ENVELOPE_ARTIFACT_LABELS);
        for (var i = 0; i < listKeys.length; i++) {
            sHtml += _fsRenderEnvelopeArtifactRow(
                listKeys[i], dictArtifacts[listKeys[i]] || {});
        }
        return sHtml;
    }

    function _fsRenderEnvelopeArtifactRow(sKey, dictArtifact) {
        var bPresent = dictArtifact.bPresent === true;
        var bSatisfied = dictArtifact.bSatisfied === true;
        return '<div class="envelope-artifact-row">' +
            '<span class="envelope-artifact-name">' +
            fnEscapeHtml(_DICT_ENVELOPE_ARTIFACT_LABELS[sKey]) +
            '</span>' +
            '<span class="envelope-row-marks">' +
            _fsWrapEnvelopeMarkSlot(_fsBuildEnvelopeMark(
                _fsLightStateFromBoolean(bPresent),
                bPresent
                    ? "File exists in the project repository"
                    : "File missing — generate it from the AICS " +
                      "tab's Level 3 Readiness card")) +
            _fsWrapEnvelopeMarkSlot(_fsBuildEnvelopeMark(
                _fsLightStateFromBoolean(bSatisfied),
                bSatisfied
                    ? "Meets its Level 3 requirement"
                    : "Does not meet its Level 3 requirement yet — " +
                      "the Level 3 Readiness card names the " +
                      "failing check")) +
            '</span></div>';
    }

    var _DICT_DETERMINISM_LABELS = {
        bAcceptBlasVariance: "BLAS numeric variance accepted",
        dOmpNumThreads: "OpenMP threads pinned",
        sMklCbwr: "Intel MKL reproducibility mode pinned",
    };

    function _fsRenderEnvelopeDeterminismBody(dictDeterminism) {
        var bDeclared = Boolean(dictDeterminism &&
            Object.keys(dictDeterminism).length > 0);
        return _fsRenderEnvelopeMarkHeader([
            ["R", "Rules — does the workflow declare its " +
                "run-to-run repeatability rules (random seeding, " +
                "numeric-library variance)? Required for Level 3."],
        ]) +
            '<div class="envelope-determinism-row">' +
            '<span class="envelope-rule-name">Reproducibility ' +
            'rules</span>' +
            '<span class="envelope-row-marks">' +
            _fsWrapEnvelopeMarkSlot(_fsBuildEnvelopeMark(
                bDeclared ? "green" : "red",
                bDeclared
                    ? "Met — declared: " +
                        _fsSummarizeDeterminism(dictDeterminism)
                    : "Not met — declare the workflow's " +
                      "repeatability rules (random seeding, BLAS " +
                      "variance) to unlock Level 3")) +
            '</span></div>';
    }

    function _fsSummarizeDeterminism(dictDeterminism) {
        return Object.keys(dictDeterminism).map(function (sKey) {
            var sLabel = _DICT_DETERMINISM_LABELS[sKey] || sKey;
            if (dictDeterminism[sKey] !== true) {
                sLabel += " = " + _fsStringifyEnvelopeValue(
                    dictDeterminism[sKey]);
            }
            return sLabel;
        }).join("; ");
    }

    function _fsStringifyEnvelopeValue(jsonValue) {
        if (jsonValue === null || jsonValue === undefined) return "—";
        if (typeof jsonValue === "object") {
            return JSON.stringify(jsonValue);
        }
        return String(jsonValue);
    }

    var _DICT_SYNC_SERVICE_LABELS = {
        github: "GitHub",
        zenodo: "Zenodo",
        overleaf: "Overleaf",
        arxiv: "arXiv",
    };

    function _fsRenderEnvelopeSyncBody(dictRemoteSyncs) {
        var sHtml = _fsRenderEnvelopeMarkHeader([
            ["S", "Sync — do the published copies on this service " +
                "match your local files? Hover each mark for " +
                "counts and freshness; refresh remote status from " +
                "the Repos panel."],
        ]);
        var listServices = _LIST_ENVELOPE_SYNC_SERVICES;
        for (var i = 0; i < listServices.length; i++) {
            sHtml += _fsRenderEnvelopeSyncRow(
                listServices[i],
                dictRemoteSyncs[listServices[i]] || null);
        }
        return sHtml;
    }

    function _fsRenderEnvelopeSyncRow(sService, dictSync) {
        // A null cache means the remote was never verified; the
        // hollow grey mark is the honest rendering — never a pass.
        var sState = dictSync
            ? _fsSyncLightState(dictSync) : "unknown";
        var sTooltip = dictSync
            ? _fsDescribeSyncState(dictSync)
            : "Never verified — refresh remote status from the " +
              "Repos panel";
        return '<div class="envelope-sync-row">' +
            '<span class="envelope-sync-name">' +
            fnEscapeHtml(_DICT_SYNC_SERVICE_LABELS[sService] ||
                sService) + '</span>' +
            '<span class="envelope-row-marks">' +
            _fsWrapEnvelopeMarkSlot(
                _fsBuildEnvelopeMark(sState, sTooltip)) +
            '</span></div>';
    }

    function _fsSyncLightState(dictSync) {
        if ((dictSync.iDivergedCount || 0) > 0) return "red";
        if (dictSync.bStale === true) return "orange";
        return "green";
    }

    function _fsDescribeSyncState(dictSync) {
        var sText = (dictSync.iMatching || 0) + " of " +
            (dictSync.iTotalFiles || 0) + " files matching";
        if ((dictSync.iDivergedCount || 0) > 0) {
            sText += ", " + dictSync.iDivergedCount + " diverged";
        }
        if (dictSync.bStale === true) {
            sText += " · stale — re-verify";
        }
        if (dictSync.sLastVerified) {
            sText += " · last verified " + dictSync.sLastVerified;
        }
        return sText;
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
        sHtml += '</div>';
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
        fsRenderWorkflowLevelHeader: fsRenderWorkflowLevelHeader,
        fsRenderGhostAiDeclarationRow: fsRenderGhostAiDeclarationRow,
        fnFillAiDeclarationPreviews: fnFillAiDeclarationPreviews,
    };
})();
