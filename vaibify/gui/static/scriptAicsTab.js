/* Vaibify — AICS tab: the requirements ledger.

   Three expandable sections (Level 1 / 2 / 3), each listing every
   requirement vaibify enforces at that level with its live state for
   this workflow, a plain-English description, and how to meet it —
   deep-linking to where the action lives (the Main tab's blocks, the
   Repos panel) rather than duplicating buttons. The Level 3 section
   closes with the rebuild verification button, the current
   attestation, and the reproduction history. The ``?`` legend panel
   stays a pure symbol key; this tab owns the requirement text. */

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

    /* --- Requirement catalog ---
       The single source of truth for what each AICS level demands and
       how each requirement is met with vaibify. The tab renders one
       expandable section per level from these lists; sStateKey names
       the wire flag that carries the live verdict (Level 1 rows
       resolve client-side in _fbRequirementMet). */

    var _DICT_LEVEL_SECTION_TITLES = {
        1: "Level 1 — Self-Consistent",
        2: "Level 2 — Published",
        3: "Level 3 — Reproducible",
    };

    var _DICT_REQUIREMENT_CATALOG = {
        1: [
            {sStateKey: "gitRepo",
             sLabel: "Project repository",
             sWhat: "The workflow and every file it touches live " +
                 "inside a git repository, so each change is " +
                 "tracked from the start.",
             sHow: "Vaibify detects this automatically — see the " +
                 "Repository section of the Project block.",
             sFixTabPanel: "steps",
             sFixLabel: "Open the Main tab"},
            {sStateKey: "stepsSelfConsistent",
             sLabel: "Every step self-consistent",
             sWhat: "Each step's tests pass against its current " +
                 "outputs, nothing has changed since testing, and " +
                 "you have signed off on the results.",
             sHow: "Work through the Steps block: run each step, " +
                 "generate and run its tests, and approve. The " +
                 "warning column names whatever blocks a step.",
             sFixTabPanel: "steps",
             sFixLabel: "Open the Main tab"},
        ],
        2: [
            {sStateKey: "bGithubFullySynced",
             sLabel: "GitHub mirror",
             sWhat: "Every canonical file — scripts, outputs, tests, " +
                 "the manifest — matches your public GitHub " +
                 "repository at a recently verified commit.",
             sHow: "Commit and push from the Repos panel, then " +
                 "re-verify; per-file state is under Published " +
                 "copies in the Project block.",
             sFixTabPanel: "repos",
             sFixLabel: "Open the Repos panel"},
            {sStateKey: "bZenodoFullySynced",
             sLabel: "Zenodo deposit",
             sWhat: "Published files match a Zenodo deposit with a " +
                 "DOI — the citable, permanent archive of your " +
                 "results.",
             sHow: "Publish (or re-verify) the deposit from the " +
                 "Repos panel.",
             sFixTabPanel: "repos",
             sFixLabel: "Open the Repos panel"},
            {sStateKey: "bArxivFullySynced",
             sLabel: "arXiv manuscript",
             sWhat: "When an Overleaf manuscript is bound, its " +
                 "frozen figures must match the recorded arXiv " +
                 "submission. A workflow without a manuscript " +
                 "meets this automatically.",
             sHow: "Push figures to Overleaf, then configure the " +
                 "arXiv submission under Published copies in the " +
                 "Project block.",
             sFixTabPanel: "steps",
             sFixLabel: "Open the Main tab"},
            {sStateKey: "bAiDeclarationAttested",
             sLabel: "AI Declaration attested",
             sWhat: "A committed, signed declaration of how AI was " +
                 "used to build this workflow — part of the " +
                 "published record.",
             sHow: "Add the AI Declaration step, review the " +
                 "declaration file, sign off, and commit it (the " +
                 "Attestation section of the Project block).",
             sFixTabPanel: "steps",
             sFixLabel: "Open the Main tab"},
        ],
        3: [
            {sStateKey: "bManifestComplete",
             sLabel: "Manifest complete",
             sWhat: "MANIFEST.sha256 pins the hash of every " +
                 "declared artifact, script, and test, so any " +
                 "drift is detectable.",
             sHow: "Regenerated automatically at each Level 1 pass, " +
                 "or on demand from the Artifacts section of the " +
                 "Project block.",
             sFixTabPanel: "steps",
             sFixLabel: "Open the Main tab"},
            {sStateKey: "bDependencyLockHashed",
             sLabel: "Dependency lock",
             sWhat: "requirements.lock pins every Python dependency " +
                 "by exact version with hashes, so the software " +
                 "stack is rebuildable.",
             sHow: "Regenerated with the envelope; check it from " +
                 "the Artifacts section of the Project block.",
             sFixTabPanel: "steps",
             sFixLabel: "Open the Main tab"},
            {sStateKey: "bEnvironmentDigestPinned",
             sLabel: "Environment snapshot",
             sWhat: "The exact container image digest and system " +
                 "toolchain are recorded, pinning the compute " +
                 "environment.",
             sHow: "Captured with the envelope; see the Artifacts " +
                 "section of the Project block.",
             sFixTabPanel: "steps",
             sFixLabel: "Open the Main tab"},
            {sStateKey: "bDockerfilePinned",
             sLabel: "Dockerfile pinned",
             sWhat: "The Dockerfile builds from an exact base-image " +
                 "digest so the container can be rebuilt " +
                 "bit-for-bit years later.",
             sHow: "Edit the Dockerfile to pin the base image " +
                 "(FROM <image>@sha256:…), or ask the in-container " +
                 "agent to pin it.",
             sFixTabPanel: "steps",
             sFixLabel: "Open the Main tab"},
            {sStateKey: "bReproduceScriptPinned",
             sLabel: "Reproduce script",
             sWhat: "One script at the repository root, reproduce.sh, " +
                 "reruns the whole workflow, and its hash is pinned " +
                 "in the manifest.",
             sHow: "Generate it from the Artifacts section of the " +
                 "Project block (regenerating also re-pins " +
                 "the manifest).",
             sFixTabPanel: "steps",
             sFixLabel: "Open the Main tab"},
            {sStateKey: "bDeterminismDeclared",
             sLabel: "Determinism declared",
             sWhat: "You have stated how exactly a rerun must match " +
                 "your numbers — random seeding and numeric-library " +
                 "variance rules.",
             sHow: "Declare the rules in the Determinism section of " +
                 "the Project block.",
             sFixTabPanel: "steps",
             sFixLabel: "Open the Main tab"},
            {sStateKey: "bBinariesDeclaredOrWaived",
             sLabel: "Software declared",
             sWhat: "Standalone scientific binaries are declared " +
                 "with their versions and hashes captured (or " +
                 "explicitly waived), pinning the exact code that " +
                 "produced the results.",
             sHow: "Add packages and capture version + SHA in the " +
                 "Software section of the Project block.",
             sFixTabPanel: "steps",
             sFixLabel: "Open the Main tab"},
            {sStateKey: "bL3AttestationCurrent",
             sLabel: "Rebuild attestation",
             sWhat: "A full rebuild reran the workflow and " +
                 "reproduced your outputs with matching hashes, " +
                 "recorded against the current manifest.",
             sHow: "Run the verification below once every other " +
                 "check passes — the rebuild runs in the container " +
                 "and can take hours.",
             sFixTabPanel: "",
             sFixLabel: ""},
        ],
    };

    function fnSetContainerId(sContainerId) {
        _sContainerId = sContainerId;
        _dictLastReadiness = null;
        _dictLastL3Readiness = null;
        _dictLastL3Attestation = null;
        _setExpandedLevels.clear();
        _bExpandSeeded = false;
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
        _fnSeedExpandedLevels(iLevel);
        elContent.innerHTML = _fsRenderHeaderCard(iLevel) +
            _fsRenderLevelSection(1) +
            _fsRenderLevelSection(2) +
            _fsRenderLevelSection(3);
        _fnBindGapFixLinks();
        _fnBindLevelSectionHeaders();
        _fnBindGenerateTemplateButton();
        _fnBindVerifyL3Button();
        _fnBindProgressSegments();
    }

    /* --- Level sections: requirements + live state + how-to --- */

    var _setExpandedLevels = new Set();
    var _bExpandSeeded = false;

    function _fnSeedExpandedLevels(iLevel) {
        // First paint: open the level the researcher is working
        // toward (the lowest level with an unmet requirement);
        // afterwards their manual choices win.
        if (_bExpandSeeded) return;
        _bExpandSeeded = true;
        for (var i = 1; i <= 3; i++) {
            var listRows = _DICT_REQUIREMENT_CATALOG[i];
            var bAllMet = listRows.every(function (dictReq) {
                return _fbRequirementMet(dictReq);
            });
            if (!bAllMet) {
                _setExpandedLevels.add(i);
                return;
            }
        }
        _setExpandedLevels.add(3);
    }

    function _fbRequirementMet(dictReq) {
        var sKey = dictReq.sStateKey;
        if (sKey === "gitRepo") {
            var dictWorkflow = (PipeleyenApp &&
                PipeleyenApp.fdictGetWorkflow)
                ? PipeleyenApp.fdictGetWorkflow() : null;
            return Boolean((dictWorkflow || {}).sProjectRepoPath);
        }
        if (sKey === "stepsSelfConsistent") {
            return (_dictLastReadiness.iAICSLevel || 0) >= 1;
        }
        var dictGaps = _dictLastReadiness.dictLevel2Gaps || {};
        if (sKey in dictGaps) return dictGaps[sKey] === true;
        var dictL3 = (_dictLastL3Readiness &&
            _dictLastL3Readiness.dictL3ReadinessGaps) || {};
        return dictL3[sKey] === true;
    }

    function _fsBuildLevelLight(sState, sTooltip) {
        var sInner = sState === "attained"
            ? '<img src="/static/favicon.png" ' +
                'class="level-cell-favicon" alt="met">'
            : '<span class="level-cell-circle"></span>';
        return '<span class="step-level-cell level-cell-' + sState +
            '" title="' + fnEscapeHtml(sTooltip) + '">' + sInner +
            '</span>';
    }

    function _fsRenderLevelSection(iLevelSection) {
        var listRows = _DICT_REQUIREMENT_CATALOG[iLevelSection];
        var bOpen = _setExpandedLevels.has(iLevelSection);
        var iMet = listRows.filter(_fbRequirementMet).length;
        var sState = iMet === listRows.length ? "attained"
            : (iMet === 0 ? "none" : "partial");
        var sHtml = '<div class="aics-card aics-level-section' +
            (bOpen ? '' : ' collapsed') + '" data-level="' +
            iLevelSection + '">' +
            '<div class="aics-card-header aics-level-section-header"' +
            ' data-level="' + iLevelSection + '">' +
            '<span class="aics-card-title">' +
            _DICT_LEVEL_SECTION_TITLES[iLevelSection] + '</span>' +
            '<span class="aics-card-summary">' + iMet + ' of ' +
            listRows.length + ' met</span>' +
            _fsBuildLevelLight(sState,
                _DICT_LEVEL_SECTION_TITLES[iLevelSection]) +
            '</div>';
        if (bOpen) {
            sHtml += '<div class="aics-card-body">';
            for (var i = 0; i < listRows.length; i++) {
                sHtml += _fsRenderRequirementEntry(listRows[i]);
            }
            if (iLevelSection === 3) {
                sHtml += _fsRenderLevel3Extras();
            }
            sHtml += '</div>';
        }
        return sHtml + '</div>';
    }

    function _fsRenderRequirementEntry(dictReq) {
        var bMet = _fbRequirementMet(dictReq);
        var sLight = _fsBuildLevelLight(
            bMet ? "attained" : "none",
            dictReq.sLabel + ": " + (bMet ? "met" : "not met"));
        var sLink = "";
        if (!bMet && dictReq.sFixTabPanel) {
            sLink = ' <a class="aics-gap-fix" data-target-tab="' +
                dictReq.sFixTabPanel + '" href="#">' +
                fnEscapeHtml(dictReq.sFixLabel) + '</a>';
        }
        return '<div class="aics-req-entry state-' +
            (bMet ? "satisfied" : "unsatisfied") + '">' +
            '<div class="aics-req-row">' + sLight +
            '<span class="aics-req-label">' +
            fnEscapeHtml(dictReq.sLabel) + '</span></div>' +
            '<div class="aics-req-what">' +
            fnEscapeHtml(dictReq.sWhat) + '</div>' +
            '<div class="aics-req-how">' +
            fnEscapeHtml(dictReq.sHow) + sLink + '</div></div>';
    }

    function _fsRenderLevel3Extras() {
        var dictL3 = (_dictLastL3Readiness &&
            _dictLastL3Readiness.dictL3ReadinessGaps) || {};
        return _fsRenderVerifyL3Button(dictL3) +
            _fsRenderL3AttestationCard(dictL3) +
            _fsRenderL3HistoryTable();
    }

    function _fnBindLevelSectionHeaders() {
        var elContent = _felGetTabContent();
        if (!elContent) return;
        elContent.querySelectorAll(".aics-level-section-header")
            .forEach(function (elHeader) {
                elHeader.addEventListener("click", function () {
                    var iLevel = parseInt(
                        elHeader.dataset.level, 10);
                    if (_setExpandedLevels.has(iLevel)) {
                        _setExpandedLevels.delete(iLevel);
                    } else {
                        _setExpandedLevels.add(iLevel);
                    }
                    _fnPaintFromCache();
                });
            });
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
            // The Level 1 work itself lives in the step list — take
            // the researcher to the blocked steps rather than a dead
            // in-tab scroll.
            var elStepsTab = document.querySelector(
                '.left-tab[data-panel="steps"]');
            if (elStepsTab) elStepsTab.click();
            return;
        }
        var iLevel = sLevelKey === "L3" ? 3 : 2;
        _setExpandedLevels.add(iLevel);
        _fnPaintFromCache();
        var el = _felGetTabContent();
        if (!el) return;
        var elTarget = el.querySelector(
            '.aics-level-section[data-level="' + iLevel + '"]');
        if (!elTarget) return;
        elTarget.scrollIntoView({behavior: "smooth", block: "start"});
    }

    function _fnBindGapFixLinks() {
        var elContent = _felGetTabContent();
        if (!elContent) return;
        elContent.querySelectorAll(".aics-gap-fix").forEach(
            function (elLink) {
                elLink.addEventListener("click", function (event) {
                    event.preventDefault();
                    _fnNavigateToFixSurface(elLink.dataset.targetTab);
                });
            });
    }

    function _fnNavigateToFixSurface(sTargetTab) {
        var elTab = document.querySelector(
            '.left-tab[data-panel="' + sTargetTab + '"]'
        );
        if (elTab) elTab.click();
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
