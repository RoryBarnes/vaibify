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
        bAiDeclarationStepPresent: {
            sLabel: "AI Declaration step",
            sFix: "Add an AI Declaration step in the Steps tab",
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
    }

    function _fsRenderHeaderCard(iLevel) {
        var dictHeader = _DICT_LEVEL_HEADERS[iLevel] ||
            _DICT_LEVEL_HEADERS[0];
        return '<div class="aics-header-card aics-level-' +
            iLevel + '-tint">' +
            '<div class="aics-header-title">' +
            fnEscapeHtml(dictHeader.sTitle) + '</div>' +
            '<div class="aics-header-subtitle">' +
            fnEscapeHtml(dictHeader.sSubtitle) + '</div>' +
            '</div>';
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
            "bAiDeclarationStepPresent", dictGaps);
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
        if (sGap === "bAiDeclarationStepPresent") {
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
        return iSatisfied + " / " + iTotal + " verifiers green";
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
        var sTooltip = bReady ? "Run the L3 rebuild + hash compare " +
            "(can take hours)" :
            "All six readiness verifiers must be green to attempt L3 " +
            "verification";
        var sInFlight = _bVerifyInFlight ? " in-flight" : "";
        var sLabel = _bVerifyInFlight ? "Verifying…" :
            "Verify L3 Reproducibility";
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
                '<span class="aics-card-title">L3 Attestation</span>' +
                '</div><div class="aics-card-body">' +
                'No reproduction attempt on file yet.' +
                '</div></div>';
        }
        var sStatus = dictCurrent.sStatus || "unknown";
        var sStale = _fsRenderStalenessNotice(dictCurrent);
        return '<div class="aics-card aics-attestation-card status-' +
            sStatus + '">' +
            '<div class="aics-card-header">' +
            '<span class="aics-card-title">L3 Attestation</span>' +
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

    function _fsRenderHistoryRow(dictEntry) {
        var sStatus = dictEntry.sStatus || "?";
        var sIcon = sStatus === "passed" ? "✓" : "✗";
        var sManifest = (dictEntry.sManifestDigestAtAttestation ||
            "").slice(0, 19);
        var fDuration = dictEntry.fDurationSeconds || 0;
        return '<tr class="state-' + sStatus + '">' +
            '<td><code>' + fnEscapeHtml(
                dictEntry.sAttestedAtUtc || "?") +
            '</code></td>' +
            '<td>' + sIcon + ' ' + fnEscapeHtml(sStatus) + '</td>' +
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
                "L3 verification started. Watch the pipeline " +
                "progress; results will appear in the attestation " +
                "card when the rebuild completes.", "info"
            );
        } catch (error) {
            PipeleyenApp.fnShowToast(
                "L3 verification failed to start: " + error.message,
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
