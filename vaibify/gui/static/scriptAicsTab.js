/* Vaibify — AICS tab (current level, L2 readiness card) */

var VaibifyAicsTab = (function () {
    "use strict";

    var fnEscapeHtml = VaibifyUtilities.fnEscapeHtml;

    var _sContainerId = null;
    var _dictLastReadiness = null;
    var _bReadinessRefreshInFlight = false;
    var _dictLastL3Readiness = null;
    var _dictLastL3Attestation = null;
    var _bL3ReadinessRefreshInFlight = false;
    var _bL3AttestationRefreshInFlight = false;
    var _bVerifyInFlight = false;

    var _DICT_L3_VERIFIER_LABELS = {
        bManifestComplete: "Manifest complete",
        bDependencyLockHashed: "Dependency lock hash-pinned",
        bEnvironmentDigestPinned: "Environment image digest-pinned",
        bDockerfilePinned: "Dockerfile pinned (digest + apt + " +
            "SOURCE_DATE_EPOCH)",
        bReproduceScriptPinned: "reproduce.sh present and in MANIFEST",
        bDeterminismDeclared: "Determinism declared (RNG + BLAS)",
    };

    var _DICT_LEVEL_HEADERS = {
        0: {
            sTitle: "Not yet at Level 1",
            sSubtitle: "Approve every step and resolve any " +
                "outstanding test failures.",
        },
        1: {
            sTitle: "Level 1: Self-Consistent",
            sSubtitle: "Every step is user-approved and tests pass.",
        },
        2: {
            sTitle: "Level 2: Published",
            sSubtitle: "Canonical files mirrored to GitHub and " +
                "Zenodo; AI declaration attested.",
        },
        3: {
            sTitle: "Level 3: Reproducible",
            sSubtitle: "Rebuild produces matching hashes.",
        },
    };

    var _DICT_GAP_FIX_LABELS = {
        bGithubFullySynced: {
            sLabel: "GitHub mirror",
            sFix: "Open the Repos tab to push and re-verify",
            sFixTabPanel: "repos",
        },
        bZenodoFullySynced: {
            sLabel: "Zenodo deposit + DOI",
            sFix: "Open the Repos tab to publish or re-verify",
            sFixTabPanel: "repos",
        },
        bAiDeclarationAttested: {
            sLabel: "AI Declaration step attested",
            sFix: "Add or attest the AI Declaration step in the " +
                "Steps tab",
            sFixTabPanel: "steps",
        },
    };

    function fnSetContainerId(sContainerId) {
        _sContainerId = sContainerId;
        _dictLastReadiness = null;
        _dictLastL3Readiness = null;
        _dictLastL3Attestation = null;
    }

    function _felGetTabContent() {
        return document.getElementById("aicsTabContent");
    }

    async function fnRender() {
        var elContent = _felGetTabContent();
        if (!elContent) return;
        if (!_sContainerId) {
            elContent.innerHTML = '<div class="aics-empty">' +
                'Connect to a workflow to see AICS status.</div>';
            return;
        }
        if (_dictLastReadiness === null) {
            elContent.innerHTML = '<div class="aics-empty">' +
                'Loading AICS readiness…</div>';
        }
        await Promise.all([
            _fnRefreshReadiness(),
            _fnRefreshL3Readiness(),
            _fnRefreshL3Attestation(),
        ]);
        _fnPaintFromCache();
    }

    async function _fnRefreshL3Readiness() {
        if (!_sContainerId) return;
        if (_bL3ReadinessRefreshInFlight) return;
        _bL3ReadinessRefreshInFlight = true;
        try {
            _dictLastL3Readiness = await VaibifyApi.fdictGet(
                "/api/workflow/" + _sContainerId +
                "/level3/readiness"
            );
        } catch (error) {
            _dictLastL3Readiness = {sError: error.message};
        } finally {
            _bL3ReadinessRefreshInFlight = false;
        }
    }

    async function _fnRefreshL3Attestation() {
        if (!_sContainerId) return;
        if (_bL3AttestationRefreshInFlight) return;
        _bL3AttestationRefreshInFlight = true;
        try {
            _dictLastL3Attestation = await VaibifyApi.fdictGet(
                "/api/workflow/" + _sContainerId +
                "/level3/attestation"
            );
        } catch (error) {
            _dictLastL3Attestation = {sError: error.message};
        } finally {
            _bL3AttestationRefreshInFlight = false;
        }
    }

    async function _fnRefreshReadiness() {
        if (!_sContainerId) return;
        if (_bReadinessRefreshInFlight) return;
        _bReadinessRefreshInFlight = true;
        try {
            _dictLastReadiness = await VaibifyApi.fdictGet(
                "/api/workflow/" + _sContainerId +
                "/level2/readiness"
            );
        } catch (error) {
            _dictLastReadiness = {sError: error.message};
        } finally {
            _bReadinessRefreshInFlight = false;
        }
    }

    function _fnPaintFromCache() {
        var elContent = _felGetTabContent();
        if (!elContent) return;
        if (_dictLastReadiness && _dictLastReadiness.sError) {
            elContent.innerHTML = '<div class="aics-empty">' +
                'Could not load readiness: ' +
                fnEscapeHtml(_dictLastReadiness.sError) +
                '</div>';
            return;
        }
        if (!_dictLastReadiness) return;
        var iLevel = _dictLastReadiness.iAICSLevel || 0;
        var dictGaps = _dictLastReadiness.dictLevel2Gaps || {};
        var dictL3 = (_dictLastL3Readiness &&
            _dictLastL3Readiness.dictL3ReadinessGaps) || {};
        elContent.innerHTML = _fsRenderHeaderCard(iLevel) +
            _fsRenderLevel2ReadinessCard(iLevel, dictGaps) +
            _fsRenderL3ReadinessCard(iLevel, dictL3) +
            _fsRenderL3AttestationCard(dictL3) +
            _fsRenderL3HistoryTable();
        _fnBindGapFixLinks();
        _fnBindGenerateTemplateButton();
        _fnBindVerifyL3Button();
        _fnBindProgressSegments();
    }

    function _fnNotifyCachedAicsLevel(iLevel) {
        // Mirror the AICS-tab's read of /level2/readiness into the
        // application state so per-step level dots can render before
        // the AICS tab is open.
        if (PipeleyenApp && PipeleyenApp.fnSetCachedAicsLevel) {
            PipeleyenApp.fnSetCachedAicsLevel(iLevel);
        }
    }

    function _fsRenderHeaderCard(iLevel) {
        _fnNotifyCachedAicsLevel(iLevel);
        var dictHeader = _DICT_LEVEL_HEADERS[iLevel] ||
            _DICT_LEVEL_HEADERS[0];
        return '<div class="aics-header-card aics-level-' +
            iLevel + '-tint">' +
            '<div class="aics-header-title">' +
            fnEscapeHtml(dictHeader.sTitle) + '</div>' +
            '<div class="aics-header-subtitle">' +
            fnEscapeHtml(dictHeader.sSubtitle) + '</div>' +
            '<div class="aics-header-progress-wrap">' +
            _fsFormatBlockerCountSuffix(iLevel) +
            '</div></div>';
    }

    function _fsFormatBlockerCountSuffix(iLevel) {
        // Section F: workflow-header progression. Four states (L0..L3),
        // each rendered as two clickable segments separated by ' · '.
        // Reuses the existing AICS-tab readiness cards as the click
        // target so the header is a navigation aid, not a new page.
        var dictCounts = _fdictBlockerCountsByLevel();
        var listSegments = _flistProgressSegments(iLevel, dictCounts);
        if (listSegments.length === 0) return "";
        var sHtml = '<div class="aics-progress">';
        for (var i = 0; i < listSegments.length; i++) {
            if (i > 0) {
                sHtml += '<span class="aics-progress-divider"> · </span>';
            }
            sHtml += listSegments[i];
        }
        sHtml += '</div>';
        return sHtml;
    }

    function _fdictBlockerCountsByLevel() {
        if (PipeleyenApp && PipeleyenApp.fdictBlockerCountsByLevel) {
            return PipeleyenApp.fdictBlockerCountsByLevel();
        }
        var iCount = (PipeleyenApp && PipeleyenApp.fiGetL1BlockerCount)
            ? PipeleyenApp.fiGetL1BlockerCount() : 0;
        return {iLevel1: iCount, iLevel2: 0, iLevel3: 0};
    }

    function _flistProgressSegments(iLevel, dictCounts) {
        if (iLevel >= 3) return [_fsProgressSegment("L3", "Reproducible ✓", "green")];
        if (iLevel === 2) return _flistL2DoneSegments();
        if (iLevel === 1) return _flistL1DoneSegments(dictCounts);
        return [_fsProgressSegment("L1",
            "Self-Consistent (" + dictCounts.iLevel1 +
            _fsBlockerNoun(dictCounts.iLevel1) + ")", "red")];
    }

    function _flistL1DoneSegments(dictCounts) {
        return [
            _fsProgressSegment("L1", "Self-Consistent ✓", "green"),
            _fsProgressSegment("L2",
                "Published (" + dictCounts.iLevel2 + " blocking)", "orange"),
        ];
    }

    function _flistL2DoneSegments() {
        return [
            _fsProgressSegment("L2", "Published ✓", "green"),
            _fsProgressSegment("L3", "Reproducible (env pending)", "yellow"),
        ];
    }

    function _fsProgressSegment(sLevelKey, sLabel, sColorClass) {
        var sTitle = sLevelKey === "L1"
            ? "Show the blocking steps in the step list"
            : "Jump to the Level " + sLevelKey.charAt(1) +
                " readiness card";
        return '<span class="aics-progress-segment ' +
            'aics-progress-state-' + sColorClass + '" ' +
            'data-progress-target="' + sLevelKey + '" ' +
            'title="' + sTitle + '">' +
            fnEscapeHtml(sLabel) + '</span>';
    }

    function _fsBlockerNoun(iCount) {
        var sNoun = iCount === 1 ? "step" : "steps";
        return " " + sNoun + " blocking";
    }

    function _fnBindProgressSegments() {
        var elContent = _felGetTabContent();
        if (!elContent) return;
        var listSegments = elContent.querySelectorAll(
            ".aics-progress-segment");
        Array.prototype.forEach.call(listSegments, function (elSegment) {
            elSegment.addEventListener("click", function () {
                _fnScrollToReadiness(elSegment.dataset.progressTarget);
            });
        });
    }

    function _fnScrollToReadiness(sLevelKey) {
        if (sLevelKey === "L1") {
            // The Level 1 work list is the step list itself — this
            // tab has no Level 1 card, so scrolling here would land
            // on the card the link sits in (a dead click). Take the
            // researcher to the blocked steps instead.
            var elStepsTab = document.querySelector(
                '.left-tab[data-panel="steps"]');
            if (elStepsTab) elStepsTab.click();
            return;
        }
        var sSelector = sLevelKey === "L3"
            ? ".aics-level3-card"
            : ".aics-level2-card";
        var el = _felGetTabContent();
        if (!el) return;
        var elTarget = el.querySelector(sSelector);
        if (!elTarget) return;
        elTarget.scrollIntoView({behavior: "smooth", block: "start"});
        elTarget.classList.remove("collapsed");
    }

    function _fsRenderLevel2ReadinessCard(iLevel, dictGaps) {
        var bCollapsedDefault = iLevel >= 2;
        var sCollapsedClass = bCollapsedDefault ? " collapsed" : "";
        var sHtml = '<div class="aics-card aics-level2-card' +
            sCollapsedClass + '">';
        sHtml += '<div class="aics-card-header">' +
            '<span class="aics-card-title">' +
            'Level 2 Readiness</span>' +
            '<span class="aics-card-summary">' +
            _fsBuildLevel2Summary(dictGaps) + '</span>' +
            '</div>';
        sHtml += '<div class="aics-card-body">';
        sHtml += _fsRenderGapRow(
            "bGithubFullySynced", dictGaps);
        sHtml += _fsRenderGapRow(
            "bZenodoFullySynced", dictGaps);
        sHtml += _fsRenderGapRow(
            "bAiDeclarationAttested", dictGaps);
        sHtml += '</div></div>';
        return sHtml;
    }

    function _fsBuildLevel2Summary(dictGaps) {
        var iSatisfied = 0;
        var iTotal = 0;
        Object.keys(_DICT_GAP_FIX_LABELS).forEach(function (sKey) {
            iTotal++;
            if (dictGaps[sKey] === true) iSatisfied++;
        });
        return iSatisfied + " / " + iTotal + " criteria met";
    }

    function _fsRenderGapRow(sKey, dictGaps) {
        var dictMeta = _DICT_GAP_FIX_LABELS[sKey];
        var bSatisfied = dictGaps[sKey] === true;
        var sStateClass = bSatisfied ? "satisfied" : "unsatisfied";
        var sIcon = bSatisfied ? "✓" : "⚠";
        var sFixHtml = "";
        if (!bSatisfied) {
            sFixHtml = ' <a class="aics-gap-fix" ' +
                'data-target-tab="' +
                dictMeta.sFixTabPanel + '" ' +
                'data-gap="' + sKey + '" href="#">' +
                fnEscapeHtml(dictMeta.sFix) + '</a>';
        }
        return '<div class="aics-gap-row state-' +
            sStateClass + '">' +
            '<span class="aics-gap-icon">' + sIcon + '</span>' +
            '<span class="aics-gap-label">' +
            fnEscapeHtml(dictMeta.sLabel) + '</span>' +
            sFixHtml + '</div>';
    }

    function _fnBindGapFixLinks() {
        var elContent = _felGetTabContent();
        if (!elContent) return;
        elContent.querySelectorAll(".aics-gap-fix").forEach(
            function (elLink) {
                elLink.addEventListener("click", function (event) {
                    event.preventDefault();
                    var sTarget = elLink.dataset.targetTab;
                    var sGap = elLink.dataset.gap;
                    _fnNavigateToFixSurface(sTarget, sGap);
                });
            });
    }

    function _fnNavigateToFixSurface(sTargetTab, sGap) {
        var elTab = document.querySelector(
            '.left-tab[data-panel="' + sTargetTab + '"]'
        );
        if (elTab) elTab.click();
        if (sGap === "bAiDeclarationAttested") {
            PipeleyenApp.fnShowToast(
                "Add a step with sStepKind=ai-declaration, " +
                "then point it at AI_USAGE.md.", "info"
            );
        }
    }

    function _fnBindGenerateTemplateButton() {
        var elContent = _felGetTabContent();
        if (!elContent) return;
        elContent.querySelectorAll(
            ".btn-aics-generate-template"
        ).forEach(function (elButton) {
            elButton.addEventListener(
                "click", _fnHandleGenerateTemplate
            );
        });
    }

    async function _fnHandleGenerateTemplate() {
        if (!_sContainerId) return;
        try {
            var dictResult = await VaibifyApi.fdictPost(
                "/api/workflow/" + _sContainerId +
                "/ai-declaration/generate-template",
                {}
            );
            PipeleyenApp.fnShowToast(
                "AI_USAGE.md template written to " +
                dictResult.sRelativePath, "info"
            );
        } catch (error) {
            PipeleyenApp.fnShowToast(
                "Template generation failed: " + error.message,
                "error"
            );
        }
        await fnRender();
    }

    function _fsRenderL3ReadinessCard(iLevel, dictL3) {
        if (iLevel < 2) {
            return '<div class="aics-card aics-level3-card disabled">' +
                '<div class="aics-card-header">' +
                '<span class="aics-card-title">' +
                'Level 3 Readiness</span>' +
                '<span class="aics-card-summary">' +
                'Reach Level 2 first.</span>' +
                '</div></div>';
        }
        var bCollapsedDefault = (iLevel >= 3) &&
            dictL3.bL3AttestationCurrent === true;
        var sCollapsedClass = bCollapsedDefault ? " collapsed" : "";
        var sHtml = '<div class="aics-card aics-level3-card' +
            sCollapsedClass + '">';
        sHtml += '<div class="aics-card-header">' +
            '<span class="aics-card-title">' +
            'Level 3 Readiness</span>' +
            '<span class="aics-card-summary">' +
            _fsBuildL3Summary(dictL3) + '</span>' +
            '</div>';
        sHtml += '<div class="aics-card-body">';
        Object.keys(_DICT_L3_VERIFIER_LABELS).forEach(function (sKey) {
            sHtml += _fsRenderL3VerifierRow(sKey, dictL3);
        });
        sHtml += _fsRenderVerifyL3Button(dictL3);
        sHtml += '</div></div>';
        return sHtml;
    }

    function _fsBuildL3Summary(dictL3) {
        var iSatisfied = 0;
        var iTotal = 0;
        Object.keys(_DICT_L3_VERIFIER_LABELS).forEach(function (sKey) {
            iTotal++;
            if (dictL3[sKey] === true) iSatisfied++;
        });
        return iSatisfied + " / " + iTotal + " checks passed";
    }

    function _fsRenderL3VerifierRow(sKey, dictL3) {
        var sLabel = _DICT_L3_VERIFIER_LABELS[sKey];
        var bSatisfied = dictL3[sKey] === true;
        var sStateClass = bSatisfied ? "satisfied" : "unsatisfied";
        var sIcon = bSatisfied ? "✓" : "⚠";
        return '<div class="aics-gap-row state-' +
            sStateClass + '">' +
            '<span class="aics-gap-icon">' + sIcon + '</span>' +
            '<span class="aics-gap-label">' +
            fnEscapeHtml(sLabel) + '</span>' +
            '</div>';
    }

    function _fsRenderVerifyL3Button(dictL3) {
        var bReady = dictL3.bL3ReadinessOK === true;
        var sDisabled = bReady ? "" : " disabled";
        var sTooltip = bReady ? "Run the Level 3 rebuild + hash compare " +
            "(can take hours)" :
            "All six readiness checks must pass before attempting " +
            "Level 3 verification";
        var sInFlight = _bVerifyInFlight ? " in-flight" : "";
        var sLabel = _bVerifyInFlight ? "Verifying…" :
            "Verify Level 3 Reproducibility";
        return '<div class="aics-verify-row">' +
            '<button type="button" class="btn-aics-verify-l3' +
            sInFlight + '" title="' + fnEscapeHtml(sTooltip) + '"' +
            sDisabled + '>' + fnEscapeHtml(sLabel) +
            '</button></div>';
    }

    function _fsRenderL3AttestationCard(dictL3) {
        if (!_dictLastL3Attestation) return "";
        var dictCurrent = _dictLastL3Attestation.dictCurrentAttestation;
        if (!dictCurrent) {
            return '<div class="aics-card aics-attestation-card empty">' +
                '<div class="aics-card-header">' +
                '<span class="aics-card-title">Level 3 Attestation</span>' +
                '</div><div class="aics-card-body">' +
                'No reproduction attempt on file yet.' +
                '</div></div>';
        }
        var sStatus = dictCurrent.sStatus || "unknown";
        var sStale = _fsRenderStalenessNotice(dictCurrent);
        return '<div class="aics-card aics-attestation-card status-' +
            sStatus + '">' +
            '<div class="aics-card-header">' +
            '<span class="aics-card-title">Level 3 Attestation</span>' +
            '<span class="aics-card-summary">' +
            fnEscapeHtml(sStatus) + '</span>' +
            '</div><div class="aics-card-body">' +
            _fsRenderAttestationDetails(dictCurrent) +
            sStale + '</div></div>';
    }

    function _fsRenderAttestationDetails(dictCurrent) {
        var sTimestamp = dictCurrent.sAttestedAtUtc || "?";
        var sManifest = (dictCurrent.sManifestDigestAtAttestation ||
            "").slice(0, 19);
        var sImage = (dictCurrent.sImageDigest || "").slice(0, 32);
        var iMatched = dictCurrent.iOutputHashesMatched || 0;
        var iTotal = dictCurrent.iOutputHashesTotal || 0;
        var fDuration = dictCurrent.fDurationSeconds || 0;
        return '<div class="aics-attestation-details">' +
            '<div>Timestamp: <code>' +
            fnEscapeHtml(sTimestamp) + '</code></div>' +
            '<div>Manifest digest: <code>' +
            fnEscapeHtml(sManifest) + '…</code></div>' +
            '<div>Image: <code>' +
            fnEscapeHtml(sImage) + '…</code></div>' +
            '<div>Hashes matched: ' + iMatched + ' / ' +
            iTotal + '</div>' +
            '<div>Duration: ' + fDuration.toFixed(1) +
            ' s</div></div>';
    }

    function _fsRenderStalenessNotice(dictCurrent) {
        var sLive = _dictLastL3Attestation.sLiveManifestDigest || "";
        var sRecorded = dictCurrent.sManifestDigestAtAttestation || "";
        if (!sLive || !sRecorded || sLive === sRecorded) return "";
        return '<div class="aics-attestation-stale">' +
            'Manifest digest changed since attestation; ' +
            're-run verification to refresh.</div>';
    }

    function _fsRenderL3HistoryTable() {
        var listHistory = (_dictLastL3Attestation &&
            _dictLastL3Attestation.listHistory) || [];
        if (!listHistory.length) return "";
        var sRows = listHistory.slice(0, 20).map(
            _fsRenderHistoryRow).join("");
        return '<div class="aics-card aics-history-card">' +
            '<div class="aics-card-header">' +
            '<span class="aics-card-title">' +
            'Reproduction History</span>' +
            '<span class="aics-card-summary">' +
            listHistory.length + ' attempt(s)</span>' +
            '</div><div class="aics-card-body">' +
            '<table class="aics-history-table">' +
            '<thead><tr><th>Timestamp</th><th>Status</th>' +
            '<th>Manifest</th><th>Duration</th></tr></thead>' +
            '<tbody>' + sRows + '</tbody></table>' +
            '</div></div>';
    }

    function _fsBuildHistoryStatusIconHtml(sStatus) {
        // Warning glyph, never an X: a non-passed reproduction
        // attempt is a failed verification, so the glyph reads red.
        if (sStatus === "passed") return "✓";
        return '<span class="aics-history-fail-glyph">⚠</span>';
    }

    function _fsRenderHistoryRow(dictEntry) {
        var sStatus = dictEntry.sStatus || "?";
        var sIconHtml = _fsBuildHistoryStatusIconHtml(sStatus);
        var sManifest = (dictEntry.sManifestDigestAtAttestation ||
            "").slice(0, 19);
        var fDuration = dictEntry.fDurationSeconds || 0;
        return '<tr class="state-' + sStatus + '">' +
            '<td><code>' + fnEscapeHtml(
                dictEntry.sAttestedAtUtc || "?") +
            '</code></td>' +
            '<td>' + sIconHtml + ' ' + fnEscapeHtml(sStatus) +
            '</td>' +
            '<td><code>' + fnEscapeHtml(sManifest) +
            '…</code></td>' +
            '<td>' + fDuration.toFixed(1) + ' s</td></tr>';
    }

    function _fnBindVerifyL3Button() {
        var elContent = _felGetTabContent();
        if (!elContent) return;
        elContent.querySelectorAll(".btn-aics-verify-l3")
            .forEach(function (elButton) {
                if (elButton.disabled) return;
                elButton.addEventListener(
                    "click", _fnHandleVerifyL3,
                );
            });
    }

    async function _fnHandleVerifyL3() {
        if (!_sContainerId) return;
        if (_bVerifyInFlight) return;
        _bVerifyInFlight = true;
        try {
            await VaibifyApi.fdictPost(
                "/api/workflow/" + _sContainerId +
                "/level3/verify",
                {}
            );
            PipeleyenApp.fnShowToast(
                "Level 3 verification started. Watch the pipeline " +
                "progress; results will appear in the attestation " +
                "card when the rebuild completes.", "info"
            );
        } catch (error) {
            PipeleyenApp.fnShowToast(
                "Level 3 verification failed to start: " + error.message,
                "error"
            );
        }
        _bVerifyInFlight = false;
        await fnRender();
    }

    function fdictGetL3Snapshot() {
        return {
            dictReadiness: _dictLastL3Readiness,
            dictAttestation: _dictLastL3Attestation,
        };
    }

    return {
        fnSetContainerId: fnSetContainerId,
        fnRender: fnRender,
        fnGenerateTemplate: _fnHandleGenerateTemplate,
        fdictGetL3Snapshot: fdictGetL3Snapshot,
    };
})();
