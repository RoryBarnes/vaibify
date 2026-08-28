/* Vaibify — Project requirements block (VaibifyWorkflowRequirements)

   L1 is a per-step property; L2 ("Published") and L3 ("Reproducible")
   are project-wide gates. This module owns the "Project" block:
   an at-a-glance L1/L2/L3 banner plus two concern groups — Publication
   (GitHub / Zenodo / arXiv / AI Declaration) and Reproducibility
   (manifest, dependency lock, environment digest, Dockerfile,
   reproduce.sh, determinism, software binaries, rebuild attestation).

   Each requirement is a step-like expandable row: a status light on the
   banner and, when expanded, file rows with the relevant remote badges
   or the declared values, plus one plain-English "how to" line.

   Everything renders verbatim from the poll's
   ``dictWorkflowEnvelopeDetail`` (the four render sections plus the
   project-wide booleans bRebuildAttestationCurrent / bOverleafBound /
   bArxivConfigured / bAiModelsDeclared / bPersonalLayerDeclared /
   bProjectContextFileExists and the declared
   dictAiProvenance block for the Replay-axis AI section).
   bAiDeclarationAttested still rides the wire but no project-level
   row reads it: the AI Declaration is a step, and its state renders
   on the step's own row only (2026-08-27 ruling).
   A null remote-sync cache renders the hollow "never verified"
   light — never green (the dashboard-ground-truth honesty rule).

   This block lives in its own ``#projectBlock`` container and is
   rebuilt unconditionally on every render, so it never participates in
   the incremental step-hash memoization. If a future maintainer ever
   memoizes it, the requirement group/row expansion Sets
   (setExpandedRequirementGroups / setExpandedRequirementRows) MUST be
   folded into that signature. */

var VaibifyWorkflowRequirements = (function () {
    "use strict";

    var fnEscapeHtml = VaibifyUtilities.fnEscapeHtml;
    var fsBuildLevelCell = VaibifyUtilities.fsBuildLevelCell;
    var fsBuildAttainedFavicon =
        VaibifyUtilities.fsBuildAttainedFavicon;

    var _DICT_ENVELOPE_ARTIFACT_LABELS = {
        manifest: "Manifest (MANIFEST.sha256)",
        dependencyLock: "Dependency lock (requirements.lock)",
        environmentSnapshot: "Environment snapshot",
        dockerfile: "Dockerfile",
        reproduceScript: "Reproduce script (reproduce.sh)",
    };

    var _LIST_ENVELOPE_SYNC_SERVICES = [
        "github", "zenodo", "overleaf", "arxiv"];

    /* --- Shared status-mark vocabulary (honesty rule) --- */

    function _fsBuildEnvelopeMark(sState, sTooltip) {
        // Pass renders the theme-tinted vaibify check (its color
        // follows --highlight-color, which climbs with the PROOF
        // ladder); failures render warning glyphs; only "unknown"
        // keeps the hollow never-verified circle.
        if (sState === "green") {
            // The passing mark is the vaibify favicon — the same
            // "attained" glyph the step level cells use — not a bare
            // check character.
            return fsBuildAttainedFavicon("met", sTooltip);
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

    function _fsSummaryStateFromCounts(iSatisfied, iTotal) {
        // The section mark mirrors the level-cell vocabulary: all
        // requirements met = check, none = red, some = orange.
        if (iSatisfied >= iTotal) return "green";
        if (iSatisfied === 0) return "red";
        return "orange";
    }

    /* --- Per-requirement status derivation --- */

    function _fsArtifactStateFromDetail(dictArtifact) {
        var bPresent = dictArtifact.bPresent === true;
        var bSatisfied = dictArtifact.bSatisfied === true;
        if (bSatisfied) return "green";
        if (bPresent) return "orange";
        return "red";
    }

    function _fsDeterminismState(dictDeterminism) {
        if (!dictDeterminism ||
                Object.keys(dictDeterminism).length === 0) {
            return "red";
        }
        return "green";
    }

    function _fsSyncRowState(dictSync) {
        // A null cache means the remote was never verified; the hollow
        // grey mark is the honest rendering — never a pass.
        if (!dictSync) return "unknown";
        if ((dictSync.iTotalFiles || 0) === 0) {
            // A verify that compared zero files demonstrated nothing:
            // "0 of 0 matching" must never render as attained
            // (vacuous-attainment rule). Legacy cache entries can
            // carry this shape; the backend now refuses to write it.
            return "unknown";
        }
        if ((dictSync.iDivergedCount || 0) > 0) {
            // Some files already match the remote → partial progress
            // (orange), not "nothing published" (red). Only a total
            // miss — nothing matching the remote — is red. Mirrors the
            // level-cell none/partial/attained vocabulary and the
            // group-summary rule; the L2 gate still needs a full match
            // (levelGates._fbCachedSyncStatusFullMatch), so orange
            // honestly denies attainment without erasing the progress.
            return (dictSync.iMatching || 0) > 0 ? "orange" : "red";
        }
        if (dictSync.bStale === true) return "orange";
        // Verified completely, but against an older definition of what
        // "published" covers — so files now in scope were never looked
        // at, and their silence is not agreement. Orange, not red: the
        // researcher has published nothing wrong, they just have no
        // evidence about the newly-covered files yet.
        if (dictSync.bScopeStale === true) return "orange";
        return "green";
    }

    function _fsDescribeSyncState(dictSync) {
        if (!dictSync) {
            return "Never verified — refresh remote status from the " +
                "Repos panel";
        }
        if ((dictSync.iTotalFiles || 0) === 0) {
            return "The last verification compared no files — " +
                "verify again" +
                (dictSync.sLastVerified
                    ? " · last attempted " + dictSync.sLastVerified
                    : "");
        }
        var sText = (dictSync.iMatching || 0) + " of " +
            (dictSync.iTotalFiles || 0) + " files matching";
        if ((dictSync.iDivergedCount || 0) > 0) {
            sText += ", " + dictSync.iDivergedCount + " diverged";
        }
        if (dictSync.bStale === true) {
            sText += " · stale — re-verify";
        }
        // Says WHY the count is not enough. Without it the row reads
        // "all files matching" beside an amber mark and an orange
        // badge on a file the count never included, which is the
        // shape a researcher reasonably reads as a bug.
        if (dictSync.bScopeStale === true) {
            sText += " · more files are covered now — verify again";
        }
        if (dictSync.sLastVerified) {
            sText += " · last verified " + dictSync.sLastVerified;
        }
        return sText;
    }

    /* --- Reused detail-row renderers (envelope marks) --- */

    function _fsRenderEnvelopeMarkHeader(listColumns) {
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

    function _fsVersionMarkState(dictBinary) {
        // Hollow means "never checked". Once a capture exists (a real
        // hash was recorded) but no version could be read, the
        // version-match requirement is checked-and-not-demonstrated —
        // that is a red, not an unknown.
        if (dictBinary.bVersionMatch === true) return "green";
        if (dictBinary.bVersionMatch === false) return "red";
        if (dictBinary.sCapturedSha256) return "red";
        return "unknown";
    }

    function _fsRenderEnvelopeBinaryRow(dictBinary) {
        return '<div class="envelope-binary-row">' +
            '<span class="envelope-binary-name">' +
            fnEscapeHtml(dictBinary.sBinaryPath || "") + '</span>' +
            '<span class="envelope-row-marks">' +
            _fsWrapEnvelopeMarkSlot(_fsBuildEnvelopeMark(
                _fsVersionMarkState(dictBinary),
                _fsDescribeVersionMatch(dictBinary))) +
            _fsWrapEnvelopeMarkSlot(_fsBuildEnvelopeMark(
                dictBinary.bHashCurrent === true ? "green" : "red",
                dictBinary.bHashCurrent === true
                    ? "Hash captured in the environment snapshot"
                    : "Hash not captured — use the 'Capture " +
                      "version + SHA' button below")) +
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
        if (dictBinary.sCapturedSha256) {
            return "Capture ran but the binary reported no readable " +
                "version (" + sVersions + ") — the version match " +
                "cannot be demonstrated. The hash still pins its " +
                "exact identity.";
        }
        return "Not captured yet (" + sVersions + ") — use the " +
            "'Capture version + SHA' button below";
    }

    var _DICT_DETERMINISM_LABELS = {
        bAcceptBlasVariance: "BLAS numeric variance accepted",
        dOmpNumThreads: "OpenMP threads pinned",
        sMklCbwr: "Intel MKL reproducibility mode pinned",
    };

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

    /* --- Requirement-row detail builders --- */

    // Only figure formats travel to a manuscript: the Overleaf and
    // arXiv rows must not list .npy / .json data files.
    // Deliberately divergent from VaibifyUtilities.fbIsFigureFile
    // (browser-viewable figures, no .eps): this list mirrors the
    // backend's manuscript-figure set _FROZENSET_OVERLEAF_EXTENSIONS
    // in vaibify/gui/syncDispatcher.py, which includes .eps because
    // LaTeX accepts it. Do not "fix" one to match the other.
    var _LIST_FIGURE_EXTENSIONS = [
        ".pdf", ".png", ".jpg", ".jpeg", ".eps", ".svg"];

    function _fbIsFigureFile(sPath) {
        var sLower = String(sPath || "").toLowerCase();
        for (var i = 0; i < _LIST_FIGURE_EXTENSIONS.length; i++) {
            if (sLower.slice(-_LIST_FIGURE_EXTENSIONS[i].length) ===
                    _LIST_FIGURE_EXTENSIONS[i]) {
                return true;
            }
        }
        return false;
    }

    function _fsRenderRemoteFileRows(sBadgeKey, listExcludePaths) {
        // Files this remote knows about, each with just this remote's
        // badge. Manuscript remotes (Overleaf, arXiv) list only
        // figure files. Empty when nothing is tracked yet.
        var listFiles = VaibifyGitBadges.flistFilesForRemote(sBadgeKey);
        if (sBadgeKey === "sOverleaf" || sBadgeKey === "sArxiv") {
            listFiles = listFiles.filter(_fbIsFigureFile);
        }
        // The Published-copies rows are Level 2 rows, so the envelope
        // is not theirs to list: a researcher scanning them for why
        // their DATA is unpublished must not find reproduce.sh among
        // the answers. The excluded set comes from the backend, which
        // owns the partition; the Level 3 row lists these instead.
        if (listExcludePaths && listExcludePaths.length) {
            listFiles = listFiles.filter(function (sPath) {
                return listExcludePaths.indexOf(sPath) === -1;
            });
        }
        if (listFiles.length === 0) {
            return '<div class="envelope-empty-note">' +
                'No files tracked for this remote yet.</div>';
        }
        var sHtml = "";
        for (var i = 0; i < listFiles.length; i++) {
            sHtml += _fsRenderFileRowWithBadges(
                listFiles[i], [sBadgeKey]);
        }
        return sHtml;
    }

    function _fsRenderSyncRequirementDetail(dictSync, sBadgeKey,
                                            sHowto, sExtraHtml,
                                            listExcludePaths) {
        return '<div class="requirement-row-detail">' +
            '<div class="requirement-row-status">' +
            fnEscapeHtml(_fsDescribeSyncState(dictSync)) + '</div>' +
            _fsRenderRemoteFileRows(sBadgeKey, listExcludePaths) +
            '<div class="requirement-row-howto">' +
            fnEscapeHtml(sHowto) + ' ' +
            '<a href="#" class="envelope-open-repos">' +
            'Open the Repos panel</a></div>' +
            (sExtraHtml || '') + '</div>';
    }

    function _fsRenderArtifactDetail(sKey, dictArtifact, sHowto) {
        // Every artifact shows its repo location (with git/Zenodo
        // badges — these files are canonical), plain-English guidance,
        // and a direct action where one exists: envelope artifacts
        // regenerate on demand, reproduce.sh has its generator, the
        // manifest additionally offers a hash check, and the
        // Dockerfile can be composed from the image's own build chain.
        var sActions = '<div class="requirement-row-howto">' +
            fnEscapeHtml(sHowto) + '</div>';
        if (_SET_REGENERABLE_ARTIFACTS[sKey] === true) {
            sActions += _fsRenderActionButton(
                "regenerate-envelope", "", "Regenerate now");
        }
        if (sKey === "manifest") {
            sActions += _fsRenderActionButton(
                "verify-manifest", "",
                "Check files against manifest");
        }
        if (sKey === "dependencyLock") {
            sActions += _fsRenderActionButton(
                "verify-dependency-lock", "",
                "Check dependencies");
        }
        if (sKey === "reproduceScript") {
            sActions += _fsRenderActionButton(
                "generate-reproduce-script", "",
                "Generate reproduce.sh");
        }
        if (sKey === "dockerfile") {
            // The image is built from vaibify's own packaged
            // Dockerfiles, which are not in the researcher's repo, so
            // this row used to be red with nothing to click. The
            // action composes the real chain (base + enabled overlays,
            // in order) into one multi-stage file. It refuses to
            // overwrite a Dockerfile vaibify did not generate, so it
            // is safe to offer unconditionally.
            sActions += _fsRenderActionButton(
                "copy-image-dockerfile", "",
                "Copy image Dockerfile into repo");
        }
        return '<div class="requirement-row-detail">' +
            _fsRenderArtifactFileRow(sKey) +
            '<div class="requirement-row-howto detail-badge-scope">' +
            fnEscapeHtml(_S_BADGE_SCOPE_NOTE) + '</div>' +
            _fsRenderEnvelopeMarkHeader([
                ["F", "File — does this artifact exist in the " +
                    "project repository?"],
                ["R", "Requirement — does it satisfy its Level 3 " +
                    "check?"],
            ]) +
            '<div class="envelope-artifact-row">' +
            '<span class="envelope-artifact-name">' +
            fnEscapeHtml(_DICT_ENVELOPE_ARTIFACT_LABELS[sKey]) +
            '</span><span class="envelope-row-marks">' +
            _fsWrapEnvelopeMarkSlot(_fsBuildEnvelopeMark(
                _fsLightStateFromBoolean(
                    dictArtifact.bPresent === true),
                dictArtifact.bPresent === true
                    ? "File exists in the project repository"
                    : "File missing")) +
            _fsWrapEnvelopeMarkSlot(_fsBuildEnvelopeMark(
                _fsLightStateFromBoolean(
                    dictArtifact.bSatisfied === true),
                dictArtifact.bSatisfied === true
                    ? "Meets its Level 3 requirement"
                    : "Does not meet its Level 3 check yet — the " +
                      "guidance below says how to fix it")) +
            '</span></div>' + sActions + '</div>';
    }

    function _fsRenderDeterminismDetail(dictDeterminism) {
        // Declared: show the rules with edit + delete. Undeclared: the
        // inline declare form. The declaration is the researcher's
        // statement of how exactly a rerun must match their results;
        // it is stored in project.json (there is no separate file).
        var bDeclared = Boolean(dictDeterminism &&
            Object.keys(dictDeterminism).length > 0);
        if (bDeclared) {
            return '<div class="requirement-row-detail">' +
                '<div class="requirement-row-status">Declared: ' +
                fnEscapeHtml(
                    _fsSummarizeDeterminism(dictDeterminism)) +
                '</div>' +
                '<div class="requirement-row-howto">These rules tell ' +
                'a verifier how exactly a rerun must match your ' +
                'results. <b>bAcceptBlasVariance</b>: you accept tiny ' +
                'last-digit differences from linear-algebra thread ' +
                'ordering. <b>dOmpNumThreads</b>: the OpenMP thread ' +
                'count a rerun must use (absent = unpinned). There ' +
                'is no separate rules file — this is the exact entry ' +
                'stored in project.json, shown so you can see what ' +
                'was recorded, not so you edit it by hand:</div>' +
                '<pre class="determinism-raw">"dictDeterminism": ' +
                fnEscapeHtml(JSON.stringify(dictDeterminism, null, 2)) +
                '</pre>' +
                '<div class="requirement-row-howto">Change them by ' +
                'declaring again, or delete them to start over.' +
                '</div>' +
                _fsRenderDeterminismForm(dictDeterminism) +
                _fsRenderActionButton("delete-determinism", "",
                    "Delete rules…", true) + '</div>';
        }
        return '<div class="requirement-row-detail">' +
            '<div class="requirement-row-status">No repeatability ' +
            'rules declared yet. State how exactly a rerun must ' +
            'reproduce your numbers (stored in project.json):</div>' +
            _fsRenderDeterminismForm(null) + '</div>';
    }

    function _fsRenderDeterminismForm(dictDeterminism) {
        var dictSafe = dictDeterminism || {};
        var bBlas = dictSafe.bAcceptBlasVariance === true;
        var sThreads = (dictSafe.dOmpNumThreads !== undefined &&
            dictSafe.dOmpNumThreads !== null)
            ? String(dictSafe.dOmpNumThreads) : "";
        return '<div class="determinism-form">' +
            '<label class="determinism-form-row">' +
            '<input type="checkbox" class="determinism-accept-blas"' +
            (bBlas ? ' checked' : '') + '> ' +
            'Accept tiny run-to-run numeric differences from ' +
            'linear-algebra libraries (BLAS thread ordering)' +
            '</label>' +
            '<details class="determinism-advanced">' +
            '<summary>Advanced: thread pinning</summary>' +
            '<div class="requirement-row-howto">Multi-threaded ' +
            'linear algebra can sum in a different order on each ' +
            'run, changing the last digits. Pinning the OpenMP ' +
            'thread count makes runs comparable; most projects ' +
            'can leave this blank.</div>' +
            '<label class="determinism-form-row">' +
            'Pin OpenMP threads: ' +
            '<input type="number" min="1" ' +
            'class="determinism-omp-threads" value="' +
            fnEscapeHtml(sThreads) + '"> (leave blank for ' +
            'unpinned)</label></details>' +
            '<div class="requirement-row-howto">Not sure? Scan the ' +
            'step scripts for patterns that would make a rerun ' +
            'differ — clock-derived seeds, os.urandom, /dev/urandom, ' +
            'the secrets module. A clean scan means none of those ' +
            'were found; it is not proof of determinism, so the ' +
            'declaration below is still yours to make.</div>' +
            _fsRenderActionButton("scan-determinism", "",
                "Scan scripts for non-determinism") +
            _fsRenderActionButton("declare-determinism", "",
                "Declare rules") + '</div>';
    }

    function _fsRenderActionButton(sAction, sArg, sLabel, bDestructive) {
        // A button that runs a project action in place (the
        // functionality that used to live only on the PROOF card).
        // bDestructive paints it red — the same signal
        // .container-menu-item.danger already uses — so an action that
        // discards work does not look like the one beside it that
        // records work. Both still confirm before acting; this is the
        // signal available BEFORE the click.
        return '<div class="requirement-row-actions">' +
            '<button type="button" class="btn wf-action-btn' +
            (bDestructive ? ' wf-action-danger' : '') + '" ' +
            'data-wf-action="' + fnEscapeHtml(sAction) + '" ' +
            'data-wf-arg="' + fnEscapeHtml(sArg || "") + '">' +
            fnEscapeHtml(sLabel) + '</button></div>';
    }

    function _fsRenderPlainDetail(sStatusText, sHowto) {
        return '<div class="requirement-row-detail">' +
            '<div class="requirement-row-status">' +
            fnEscapeHtml(sStatusText) + '</div>' +
            '<div class="requirement-row-howto">' +
            fnEscapeHtml(sHowto) + '</div></div>';
    }

    /* --- Row descriptors, grouped into the four envelope categories
       (Software / Artifacts / Determinism / Published copies) plus the
       Attestation section. --- */

    function _fsBasename(sPath) {
        var listParts = String(sPath || "").split("/");
        return listParts[listParts.length - 1] || String(sPath || "");
    }

    function _fsBinaryState(dictBinary) {
        var bVersion = dictBinary.bVersionMatch === true;
        var bHash = dictBinary.bHashCurrent === true;
        if (bVersion && bHash) return "green";
        if (bVersion || bHash) return "orange";
        return "red";
    }

    function _fdictNoBinariesRow(bWaived) {
        /* An empty list is not an answer, and this row used to render
           one as "?" forever while its guidance offered only "Add
           package…". The L3 gate accepts exactly two coherent states —
           a waiver, or a non-empty declaration — so a project with no
           binaries (most pure-Python ones) had no reachable way to
           satisfy it except declaring a package and deleting it, which
           set the waiver as a side effect nobody could guess. */
        if (bWaived) {
            return {
                sKey: "software-none", iLevel: 3,
                sTitle: "No standalone binaries (declared)",
                sState: "green",
                fsDetail: function () {
                    return _fsRenderPlainDetail(
                        "You have declared that this project calls " +
                        "no standalone scientific binaries, so there " +
                        "are no external versions or hashes to pin.",
                        "Adding a package below retracts the " +
                        "declaration automatically.");
                }};
        }
        return {
            sKey: "software-none", iLevel: 3,
            sTitle: "No declared binaries",
            sState: "unknown",
            fsDetail: function () {
                return '<div class="requirement-row-detail">' +
                    '<div class="requirement-row-howto">' +
                    fnEscapeHtml(
                        "Level 3 asks one question here, and an empty " +
                        "list does not answer it: does this project " +
                        "call standalone scientific binaries? If it " +
                        "does, add each one below and capture its " +
                        "version and hash. If it does not — a " +
                        "pure-Python project, say — declare that, and " +
                        "the row is satisfied.") +
                    '</div>' +
                    _fsRenderActionButton(
                        "declare-no-binaries", "",
                        "This project uses no standalone binaries") +
                    '</div>';
            }};
    }

    function _flistSoftwareRows(dictDetail) {
        var listBinaries = dictDetail.listBinaries || [];
        if (listBinaries.length === 0) {
            return [_fdictNoBinariesRow(
                dictDetail.bNoStandaloneBinaries === true)];
        }
        return listBinaries.map(function (dictBinary) {
            return {
                sKey: "binary:" + (dictBinary.sBinaryPath || ""),
                iLevel: 3,
                sTitle: _fsBasename(dictBinary.sBinaryPath),
                sState: _fsBinaryState(dictBinary),
                fsDetail: function () {
                    return '<div class="requirement-row-detail">' +
                        _fsRenderEnvelopeMarkHeader([
                            ["V", "Version matches the declaration?"],
                            ["H", "Hash captured in the snapshot?"],
                        ]) +
                        _fsRenderEnvelopeBinaryRow(dictBinary) +
                        '<div class="requirement-row-actions">' +
                        '<button type="button" ' +
                        'class="btn wf-action-btn" ' +
                        'data-wf-action="capture-binary" ' +
                        'data-wf-arg="' + fnEscapeHtml(
                            dictBinary.sBinaryPath || "") + '">' +
                        'Capture version + SHA</button> ' +
                        '<button type="button" ' +
                        'class="btn wf-action-btn wf-action-danger" ' +
                        'data-wf-action="remove-binary" ' +
                        'data-wf-arg="' + fnEscapeHtml(
                            dictBinary.sBinaryPath || "") + '">' +
                        'Remove package…</button></div></div>';
                }};
        });
    }

    function _flistArtifactRows(dictDetail) {
        var dictArtifacts = dictDetail.dictArtifacts || {};
        return Object.keys(_DICT_ENVELOPE_ARTIFACT_LABELS).map(
            function (sKey) {
                return _fdictArtifactRow(
                    sKey, dictArtifacts[sKey] || {});
            });
    }

    function _flistEnvelopeMirrorRows(dictDetail) {
        /* The Level 3 published-copy rows, in their own section so
           they read as the parallel of the Level 2 sync rows rather
           than as sub-items of one. Zenodo's twin joined on
           2026-08-26, reversing a same-day GitHub-only ruling:
           Level 3 claims a third party can re-fetch and re-execute,
           and GitHub is not an archive — an envelope that lives only
           there gives the claim the lifetime of a mutable host. */
        var listEnvelope = dictDetail.listLevel3EnvelopePaths || [];
        return [
            _fdictEnvelopeRemoteRow(
                "envelopeMirror", "GitHub mirror", "github",
                "sGithub", listEnvelope,
                dictDetail.bEnvelopeInGithubMirror === true,
                'The published reproduce script, manifest, ' +
                'dependency lock, environment snapshot and ' +
                'Dockerfile match the copies in this repository.',
                'One of the envelope files differs from the copy on ' +
                'GitHub, or has not been compared with it. A third ' +
                'party reproducing from the published repository ' +
                'would not be running what you ran. Commit and push ' +
                'the current envelope, then verify.'),
            _fdictEnvelopeRemoteRow(
                "envelopeArchive", "Zenodo archive", "zenodo",
                "sZenodo", listEnvelope,
                dictDetail.bEnvelopeInZenodoArchive === true,
                'The envelope files are in the Zenodo archive under ' +
                'a DOI. Zenodo versions are immutable, so this row ' +
                'goes red after any envelope change and comes back ' +
                'at your next published version — Level 3 describes ' +
                'a published release, not the working tree.',
                'One of the envelope files is not in the Zenodo ' +
                'archive, differs from the archived copy, or has ' +
                'not been compared with it. The DOI a reader ' +
                'resolves in ten years must carry what they need to ' +
                're-run this project. Publish a new deposit version ' +
                'containing the envelope (or declare the Zenodo ' +
                'record that already holds it), then verify.'),
        ];
    }

    function _fsEnvelopeRemoteRowState(bMatched, sBadgeKey, listEnvelope) {
        /* Three states, because the per-file badges beside this row
           already speak a three-state vocabulary and the row must not
           contradict them: blue/synced = checked and matching, orange
           = NOBODY HAS LOOKED, red = checked and it differs.

           This row used to be `bMatched ? green : red`, so an envelope
           file that no verify had ever compared painted the row red —
           an alarm meaning "differs" over a file nothing had proven
           anything about, sitting next to that same file's honest
           orange octocat.

           The GATE is unchanged and still blocks on unproven: an
           envelope nobody compared cannot support a reproducibility
           claim. Only the colour distinguishes "not proven" from
           "proven wrong", which is the distinction the researcher
           acts on — a verify versus a push. */
        if (bMatched) return "green";
        for (var i = 0; i < listEnvelope.length; i++) {
            var dictBadges = VaibifyGitBadges.fdictGetBadgesForFile(
                listEnvelope[i], "") || {};
            if (dictBadges[sBadgeKey] === "drifted") return "red";
        }
        return "orange";
    }

    function _fdictEnvelopeRemoteRow(
        sKey, sTitle, sService, sBadgeKey, listEnvelope, bMatched,
        sMatchedNote, sDivergedNote,
    ) {
        var sState = _fsEnvelopeRemoteRowState(
            bMatched, sBadgeKey, listEnvelope);
        return {
            sKey: sKey, iLevel: 3,
            sTitle: sTitle,
            sState: sState,
            fsDetail: function () {
                // The per-file rows the Level 2 sync rows no longer
                // carry. Without them the split is invisible: the
                // envelope files vanish from one list and appear in
                // none, which reads as vaibify having stopped
                // checking them.
                var sFiles = "";
                for (var i = 0; i < listEnvelope.length; i++) {
                    sFiles += _fsRenderFileRowWithBadges(
                        listEnvelope[i], [sBadgeKey]);
                }
                if (!sFiles) {
                    sFiles = '<div class="envelope-empty-note">' +
                        'No envelope files exist yet.</div>';
                }
                // Its own Verify-now, not a pointer at the Level 2
                // row's: one verify compares both scopes in a single
                // pass, so the action that resolves this row belongs
                // on it. Sending the researcher to another section to
                // press a button is how a row becomes a dead end.
                var sVerify = '<div class="requirement-row-actions">' +
                    '<button type="button" class="btn ' +
                    'wf-verify-remote" data-service="' +
                    fnEscapeHtml(sService) + '">' +
                    'Verify now</button></div>';
                // Three notes for three states. Telling a researcher
                // a file "differs" when nothing has compared it sends
                // them to push a file that may already be identical;
                // the unchecked case names the verify instead.
                var sNote = sDivergedNote;
                if (bMatched) {
                    sNote = sMatchedNote;
                } else if (sState === "orange") {
                    sNote = 'No verify has compared every envelope ' +
                        'file against this remote yet, so vaibify ' +
                        'cannot say whether they match. Nothing here ' +
                        'is known to differ. Run Verify now.';
                }
                return sFiles + '<div class="detail-note">' + sNote +
                    '</div>' + sVerify;
            }};
    }

    function _flistDeterminismRows(dictDetail) {
        return [{
            sKey: "determinism", iLevel: 3,
            sTitle: "Reproducibility rules",
            sState: _fsDeterminismState(dictDetail.dictDeterminism),
            fsDetail: function () {
                return _fsRenderDeterminismDetail(
                    dictDetail.dictDeterminism);
            }}];
    }

    function _fdictSyncRow(sTitle, sKey, dictSync, sBadgeKey, sHowto,
                           sExtraHtml, listExcludePaths) {
        // Every sync row carries a Verify-now button: the row reports
        // the last verify result, so the action that moves it to the
        // passing state must be reachable from the row itself, not
        // hidden behind a panel the how-to text points at.
        var sVerifyButton = '<div class="requirement-row-actions">' +
            '<button type="button" class="btn wf-verify-remote" ' +
            'data-service="' + fnEscapeHtml(sKey) + '">' +
            'Verify now</button></div>';
        return {
            sKey: sKey, sTitle: sTitle, iLevel: 2,
            sState: _fsSyncRowState(dictSync),
            fsDetail: function () {
                return _fsRenderSyncRequirementDetail(
                    dictSync, sBadgeKey, sHowto,
                    sVerifyButton + (sExtraHtml || ""),
                    listExcludePaths);
            }};
    }

    function _fdictNotApplicableRow(sTitle, sKey, sExplanation) {
        return {
            sKey: sKey, sTitle: sTitle, iLevel: 2,
            sState: "not-applicable",
            fsDetail: function () {
                return _fsRenderPlainDetail(
                    "Not required for this project.", sExplanation);
            }};
    }

    function _flistPublishedCopiesRows(dictDetail) {
        var dictSyncs = dictDetail.dictRemoteSyncs || {};
        var listEnvelope = dictDetail.listLevel3EnvelopePaths || [];
        return [
            _fdictSyncRow("GitHub mirror", "github", dictSyncs.github,
                "sGithub", "Push and re-verify from the Repos panel.",
                "", listEnvelope),
            _fdictSyncRow("Zenodo deposit", "zenodo", dictSyncs.zenodo,
                "sZenodo",
                "Publish or re-verify from the Repos panel.",
                "", listEnvelope),
            _fdictOverleafRow(dictDetail, dictSyncs),
            _fdictArxivRow(dictDetail, dictSyncs),
        ];
    }

    function _fdictOverleafRow(dictDetail, dictSyncs) {
        if (dictDetail.bOverleafBound !== true) {
            // The backend exempts figure freezing from Level 2 when
            // no manuscript is bound (levelGates), so a data-only
            // workflow must not surface a fake gap here.
            return _fdictNotApplicableRow("Overleaf manuscript",
                "overleaf",
                "No Overleaf project is bound. To publish " +
                "manuscript figures, open the Repos panel and " +
                "choose Push to Overleaf — it will prompt to " +
                "connect the project.");
        }
        return _fdictSyncRow("Overleaf manuscript", "overleaf",
            dictSyncs.overleaf, "sOverleaf",
            "Only figure files (.pdf, .png, …) travel to the " +
            "manuscript. Push figures from the Repos panel — a " +
            "successful push re-verifies this row automatically.");
    }

    var _S_ARXIV_CONFIG_BUTTON =
        '<div class="requirement-row-actions">' +
        '<button type="button" class="btn wf-open-arxiv-config">' +
        'Configure arXiv…</button></div>';

    function _fdictArxivRow(dictDetail, dictSyncs) {
        // The arXiv criterion is opt-in: recording an ID claims
        // correspondence with the posted e-print, so the claim is
        // checked; without one the row is neutral ("not tracked"),
        // never red and never a green check (levelGates).
        if (dictDetail.bArxivConfigured === true) {
            return _fdictSyncRow("arXiv submission", "arxiv",
                dictSyncs.arxiv, "sArxiv",
                "The posted e-print's figures must match the " +
                "frozen Overleaf figures.", _S_ARXIV_CONFIG_BUTTON);
        }
        return _fdictArxivNotTrackedRow(
            dictDetail.bOverleafBound === true);
    }

    function _fdictArxivNotTrackedRow(bOverleafBound) {
        var sHowto = bOverleafBound
            ? "Optional: after posting the manuscript to arXiv, " +
              "record its ID here to verify the e-print's figures " +
              "match the frozen figures. Not required for Level 2."
            : "Optional, once an Overleaf manuscript is bound and " +
              "figures are pushed: record the posted e-print's ID " +
              "to verify its figures. Not required for Level 2.";
        return {
            sKey: "arxiv", sTitle: "arXiv submission", iLevel: 2,
            sState: "not-applicable",
            fsDetail: function () {
                return '<div class="requirement-row-detail">' +
                    '<div class="requirement-row-status">' +
                    'Not tracked — optional.</div>' +
                    '<div class="requirement-row-howto">' +
                    fnEscapeHtml(sHowto) + '</div>' +
                    (bOverleafBound ? _S_ARXIV_CONFIG_BUTTON : "") +
                    '</div>';
            }};
    }

    function _fsRenderDeclaredModelRow(dictModel) {
        // One declared model: vendor / id / date range, the weights
        // branch it declared, and an in-place remove button. The arg
        // carries the (vendor, model id) key as JSON for the backend.
        var sWeights = dictModel.bOpenWeights === true
            ? "open weights — " + (dictModel.sWeightsSource || "?") +
              " @ " + (dictModel.sWeightsRevisionHash || "?")
            : "closed weights";
        var sArg = JSON.stringify({
            sVendor: dictModel.sVendor || "",
            sModelId: dictModel.sModelId || "",
        });
        return '<div class="detail-item declared-model-row">' +
            '<span class="detail-text">' +
            fnEscapeHtml((dictModel.sVendor || "?") + " / " +
                (dictModel.sModelId || "?")) +
            ' <span class="declared-model-dates">(' +
            fnEscapeHtml((dictModel.sUseStartDate || "?") + " to " +
                (dictModel.sUseEndDate || "?") + ", " + sWeights) +
            ')</span></span>' +
            '<button type="button" class="btn btn-small ' +
            'wf-action-btn" data-wf-action="remove-ai-model" ' +
            'data-wf-arg="' + fnEscapeHtml(sArg) + '">Remove</button>' +
            '</div>';
    }

    function _fsRenderAiModelPromptsDetail(dictDetail) {
        // Declared models first, then the two standing prompt files:
        // the project context (user-owned, canonical at
        // .vaibify/AGENTS.md) and the generated workspace prompt
        // (machine-owned — regenerated every container start).
        var listModels = ((dictDetail.dictAiProvenance || {})
            .listDeclaredModels) || [];
        var sModels = listModels.length === 0
            ? '<div class="requirement-row-status">No AI model ' +
              'declared — the only failing state. Declare every ' +
              'model used on this project (closed-weights models ' +
              'pass by declaration).</div>'
            : listModels.map(_fsRenderDeclaredModelRow).join("");
        var sDeclareButton = '<div class="requirement-row-actions">' +
            '<button type="button" class="btn ' +
            'wf-open-ai-model-config">Declare model…</button></div>';
        var sContextRow = dictDetail.bProjectContextFileExists === true
            ? _fsRenderFileRowWithBadges(
                ".vaibify/AGENTS.md", ["sGithub", "sZenodo"])
            : VaibifyProjectContext.fsRenderMissingContextRow(
                dictDetail);
        return '<div class="requirement-row-detail">' +
            _fsRenderReplayAxisLadder(dictDetail) + sModels +
            sDeclareButton + sContextRow +
            '<div class="requirement-row-howto">The workspace ' +
            'prompt (/workspace/CLAUDE.md) is generated by vaibify ' +
            'at container start and is read-only; both prompt ' +
            'hashes are captured into the provenance stamp.' +
            '</div>' + _fsRenderPromptRecordBlock(dictDetail) +
            '</div>';
    }

    var _LIST_REPLAY_STATES = [
        ["untracked", "Untracked"],
        ["declared", "Declared"],
        ["recorded", "Recorded"],
        ["supervised", "Supervised"],
    ];

    function _fsRenderReplayAxisLadder(dictDetail) {
        // The Replay-axis state ladder; the current state is marked.
        // A project is "Replayable" at recorded or better.
        var sCurrent = dictDetail.sReplayAxisState || "untracked";
        var sCells = _LIST_REPLAY_STATES.map(function (tState) {
            var bActive = tState[0] === sCurrent;
            return '<span class="replay-axis-cell' +
                (bActive ? ' active' : '') + '">' +
                fnEscapeHtml(tState[1]) + '</span>';
        }).join('<span class="replay-axis-arrow">→</span>');
        return '<div class="replay-axis-ladder" title="The Replay ' +
            'axis: provenance of the development process. ' +
            'Replayable at Recorded or better.">Replay: ' + sCells +
            '</div>';
    }

    function _fsRenderPromptRecordBlock(dictDetail) {
        // Opt-in state honesty: off reads "Not tracked — optional";
        // on shows the true capture counts, the review gate, and a
        // loud gap warning when coverage is not continuous.
        var dictRecord = dictDetail.dictPromptRecord || {};
        var sOpenButton = '<div class="requirement-row-actions">' +
            '<button type="button" class="btn ' +
            'wf-open-prompt-record">Prompt Record…</button></div>';
        if (dictRecord.bEnabled !== true) {
            // The supervision chip renders whether or not the Prompt
            // Record is on. Returning early here meant one toggle
            // hid every permanent flag the watchdog had ever raised.
            return '<div class="requirement-row-status">Prompt ' +
                'Record: Not tracked — optional.</div>' +
                _fsRenderSupervisionChip(dictDetail) + sOpenButton;
        }
        var sState = "Prompt Record: on — " +
            (dictRecord.iSessionCount || 0) + " session(s), " +
            (dictRecord.iRedactionTotal || 0) + " redaction(s)" +
            (dictRecord.bFirstCaptureReviewed === true
                ? "." : "; first capture awaiting your review.");
        var sGap = dictRecord.bGapPresent === true
            ? '<div class="requirement-row-status">Coverage has ' +
              'gaps — time between recorded intervals was not ' +
              'monitored.</div>'
            : "";
        return '<div class="requirement-row-status">' +
            fnEscapeHtml(sState) + '</div>' + sGap +
            _fsRenderSupervisionChip(dictDetail) + sOpenButton;
    }

    function _fsRenderSupervisionChip(dictDetail) {
        // A permanent red chip: unattributed changes, a broken chain,
        // or a persisted count that disagrees with the evidence render
        // until dealt with outside the tool — never silently cleared,
        // and never conditional on any other panel's state.
        var dictSupervision = dictDetail.dictSupervision || {};
        var sChip = "";
        if (dictSupervision.bFlagChainIntact === false) {
            sChip += _fsSupervisionChipLine('Supervision flag chain ' +
                'BROKEN — a permanent flag was edited or removed.');
        }
        if (dictSupervision.bEventChainIntact === false) {
            sChip += _fsSupervisionChipLine('Recorded-event chain ' +
                'BROKEN — the attribution log was edited or ' +
                'truncated.');
        }
        if (dictSupervision.bPersistedFlagCountMatches === false) {
            sChip += _fsSupervisionChipLine('Supervision flag count ' +
                'disagrees with the recorded flags — records were ' +
                'removed.');
        }
        if (dictSupervision.bClockSkewSuspected === true) {
            sChip += _fsSupervisionChipLine('Container and host ' +
                'clocks disagree — attribution may be unreliable.');
        }
        if ((dictSupervision.iFlagCount || 0) > 0) {
            sChip += _fsSupervisionChipLine(
                dictSupervision.iFlagCount + ' permanent ' +
                'supervision flag(s) — see Prompt Record.');
        }
        return sChip;
    }

    function _fsSupervisionChipLine(sMessage) {
        return '<div class="requirement-row-status ' +
            'supervision-flag-chip">' + fnEscapeHtml(sMessage) +
            '</div>';
    }

    function _flistAiRows(dictDetail) {
        // The Replay axis: the two project-level AI-provenance
        // declarations, each an independent Level 2 check that also
        // counts in the workflow-scope header cell. The AI
        // Declaration sign-off is deliberately NOT a row here: it is
        // a step, its state lives on the step's own row, and a
        // project-level copy double-counted it (2026-08-27 ruling).
        return [
            {sKey: "aiModelPrompts", iLevel: 2,
             sTitle: "AI Model / Prompts",
             sState: _fsLightStateFromBoolean(
                 dictDetail.bAiModelsDeclared === true),
             fsDetail: function () {
                 return _fsRenderAiModelPromptsDetail(dictDetail);
             }},
            {sKey: "personalLayer", iLevel: 2,
             sTitle: "Personal AI Configuration",
             sState: _fsLightStateFromBoolean(
                 dictDetail.bPersonalLayerDeclared === true),
             fsDetail: function () {
                 return VaibifyPersonalLayer
                     .fsRenderPersonalLayerDetail(dictDetail);
             }},
        ];
    }

    function _flistAttestationRows(dictDetail, dictContext) {
        return [
            {sKey: "rebuildAttestation", iLevel: 3,
             sTitle: "Rebuild attestation",
             sState: _fsLightStateFromBoolean(
                 dictDetail.bRebuildAttestationCurrent === true),
             fsDetail: function () {
                 return '<div class="requirement-row-detail">' +
                     '<div class="requirement-row-status">' +
                     fnEscapeHtml(
                         dictDetail.bRebuildAttestationCurrent === true
                             ? "A current rebuild attestation is on file."
                             : "No current rebuild attestation. Run " +
                               "this once every other check passes; " +
                               "the rebuild runs in the container and " +
                               "the result appears here.") + '</div>' +
                     _fsRenderActionButton("verify-l3", "",
                         "Verify Level 3 reproducibility") + '</div>';
             }},
        ];
    }

    var _DICT_ARTIFACT_HOWTO = {
        manifest: "The list of every pinned file and its SHA-256 " +
            "hash. Regenerated automatically at each Level 1 pass, " +
            "or on demand with the button below.",
        dependencyLock: "Every Python dependency (when the project " +
            "uses Python) pinned by exact version and hash. " +
            "Regenerated automatically at each Level 1 pass, or on " +
            "demand with the button below.",
        environmentSnapshot: "The container image digest and system " +
            "tools, captured so the compute environment is exact. " +
            "Recaptured automatically at each Level 1 pass, or on " +
            "demand with the button below.",
        dockerfile: "Your container image is built from vaibify's " +
            "own Dockerfiles, which are not in this repository — so " +
            "this row starts red with nothing you could have done " +
            "about it. Copy composes them (the base plus each " +
            "feature overlay, in the order they were applied) into " +
            "one multi-stage Dockerfile here, and refuses to " +
            "overwrite a Dockerfile you wrote. It records HOW the " +
            "image was made; reproduction itself runs from the " +
            "pinned image digest, not from a rebuild.",
        reproduceScript: "One script, at the repository " +
            "root, that reruns the whole project. It must match " +
            "the current manifest; Generate rewrites it and makes " +
            "this row's check pass. (This is one Level 3 " +
            "requirement — the full Level 3 badge also needs the " +
            "other rows here plus a passing rebuild attestation.)",
    };

    // Repo-relative location of each artifact, shown in the detail
    // with its GitHub/Zenodo badges: these files are canonical — they
    // should be committed and archived like any other pinned file.
    var _DICT_ARTIFACT_PATHS = {
        manifest: "MANIFEST.sha256",
        dependencyLock: "requirements.lock",
        environmentSnapshot: ".vaibify/environment.json",
        dockerfile: "Dockerfile",
        reproduceScript: "reproduce.sh",
    };

    // Artifacts the on-demand envelope regeneration rewrites.
    var _SET_REGENERABLE_ARTIFACTS = {
        manifest: true, dependencyLock: true,
        environmentSnapshot: true,
    };

    // Says, once per artifact row, what those inline badges are and
    // are NOT. The row's own light answers existence + the artifact's
    // Level 3 check; whether the published copies AGREE is a separate
    // requirement, answered by the "Published envelope" section. A
    // green row beside an orange octocat is both marks telling the
    // truth about different questions, but nothing on screen said so,
    // so the row read as claiming the badges were fine.
    var _S_BADGE_SCOPE_NOTE =
        'The badges show each published copy: blue = checked and ' +
        'matching, orange = not checked yet, red = differs. This ' +
        "row's own status does not depend on them — it asks whether " +
        'the file exists and meets its Level 3 check. Whether the ' +
        'published copies agree is asked by the Published envelope ' +
        'section below, and Level 3 needs both.';

    function _fsRenderFileRowWithBadges(sPath, aBadgeKeys) {
        // A clickable file row: the path opens in the figure/file
        // viewer, the badges show the remote sync state.
        //
        // `data-resolved` is what makes the badge ACTIONABLE. The
        // global `.remote-badge` handler reads it off the enclosing
        // .detail-item and bails when it is absent, so without it
        // these badges opened no picklist at all — a researcher
        // looking at an orange octocat here had no way to push the
        // file, while the identical badge in a Step Viewer offered
        // one. The value is repo-relative with an empty workdir,
        // which is the same pair the badge lookup above uses and the
        // form `git add` wants, since the push runs `cd <repo>` first.
        var dictBadges = VaibifyGitBadges.fdictGetBadgesForFile(
            sPath, "");
        return '<div class="detail-item tracked-file" ' +
            'data-resolved="' + fnEscapeHtml(sPath) + '" ' +
            'data-workdir="">' +
            VaibifyGitBadges.fsRenderBadgeRow(dictBadges, aBadgeKeys) +
            '<span class="detail-text wf-file-link" data-path="' +
            fnEscapeHtml(sPath) + '" title="Click to view">' +
            fnEscapeHtml(sPath) + '</span></div>';
    }

    function _fsRenderArtifactFileRow(sKey) {
        var sPath = _DICT_ARTIFACT_PATHS[sKey];
        if (!sPath) return "";
        return _fsRenderFileRowWithBadges(
            sPath, ["sGithub", "sZenodo"]);
    }

    function _fdictArtifactRow(sKey, dictArtifact) {
        return {
            sKey: sKey,
            iLevel: 3,
            sTitle: _DICT_ENVELOPE_ARTIFACT_LABELS[sKey],
            sState: _fsArtifactStateFromDetail(dictArtifact),
            fsDetail: function () {
                return _fsRenderArtifactDetail(
                    sKey, dictArtifact,
                    _DICT_ARTIFACT_HOWTO[sKey] || "");
            }};
    }

    /* --- Group + row + block renderers --- */

    // Envelope-mark states map onto the step level-cell vocabulary so
    // requirement banners read exactly like step banners.
    var _DICT_MARK_TO_LEVEL_STATE = {
        green: "attained", red: "none",
        orange: "partial", unknown: "unknown",
        "not-applicable": "not-applicable",
    };

    var _DICT_LEVEL_STATE_PHRASES = {
        attained: "met", none: "not met",
        partial: "partially met", unknown: "not checked yet",
        "not-started": "nothing to check yet",
        unassessed: "present but not yet checked",
        "not-applicable": "no requirement at this level",
    };

    function _fsRenderLevelStrip(dictStateByLevel, sTitle) {
        // Three cells (L1 | L2 | L3) behind a warning-column spacer,
        // so every strip in the column shares the banner's four-slot
        // geometry: the levels this requirement gates carry its
        // state; the others show the n/a dash — a user hunting for
        // Level 2 blockers scans one column.
        var sHtml = '<span class="step-level-strip">' +
            '<span class="step-regression-cell"></span>';
        for (var iLevel = 1; iLevel <= 3; iLevel++) {
            var sLevelState = dictStateByLevel[iLevel] ||
                "not-applicable";
            sHtml += fsBuildLevelCell(
                sLevelState,
                sTitle + " — Level " + iLevel + ": " +
                _DICT_LEVEL_STATE_PHRASES[sLevelState]);
        }
        return sHtml + '</span>';
    }

    /* Every level of the Project block is click-to-expand, and none of
       them said so. A requirement whose light was red therefore read as
       "you are blocked and there is nothing to do" rather than "open me
       and I will tell you what to do" — which is exactly backwards,
       because the detail body is where the fix lives. Step rows have
       carried this triangle all along; the Project block simply never
       got it. The click handlers dispatch on the closest matching
       header, so a click that lands on the triangle still toggles. */
    function _fsExpandTriangle(bOpen) {
        return '<span class="expand-triangle">' +
            (bOpen ? "▾" : "▸") + '</span> ';
    }

    function _fsRenderRequirementRow(dictRow, setExpandedRows) {
        // Mirrors a step row's banner: triangle and title on the left,
        // the L1-L3 level strip on the right; the banner is the
        // expand control.
        var bOpen = setExpandedRows && setExpandedRows.has(dictRow.sKey);
        var dictStateByLevel = {};
        dictStateByLevel[dictRow.iLevel || 3] =
            _DICT_MARK_TO_LEVEL_STATE[dictRow.sState] || "unknown";
        var sHtml = '<div class="requirement-row' +
            (bOpen ? ' expanded' : '') + '">' +
            '<div class="requirement-row-header" data-req="' +
            fnEscapeHtml(dictRow.sKey) + '">' +
            '<span class="requirement-row-title">' +
            _fsExpandTriangle(bOpen) +
            fnEscapeHtml(dictRow.sTitle) + '</span>' +
            _fsRenderLevelStrip(dictStateByLevel, dictRow.sTitle) +
            '</div>';
        if (bOpen) {
            sHtml += dictRow.fsDetail();
        }
        return sHtml + '</div>';
    }

    var _DICT_GROUP_TITLES = {
        repository: "Repository",
        software: "Software",
        artifacts: "Artifacts",
        determinism: "Determinism",
        // No level in either title (researcher's ruling, 2026-08-26):
        // each row's level strip already shows which column lights, so
        // spelling it in the heading too is noise.
        publishedCopies: "Published copies",
        publishedEnvelope: "Published envelope",
        ai: "AI",
        attestation: "Attestation",
    };

    function _flistRepositoryRows(dictContext) {
        // The Level 1 workflow-scope requirement made visible: the
        // workflow must live inside a git repository (its project
        // repo). Nearly always a check — but a legacy workflow
        // outside any repo honestly shows red here, which is exactly
        // the situation the row exists to explain.
        var sRepoPath = dictContext.sProjectRepoPath || "";
        var bPresent = Boolean(sRepoPath);
        return [{
            sKey: "gitRepo", iLevel: 1,
            sTitle: "Git enabled",
            sState: bPresent ? "green" : "red",
            fsDetail: function () {
                var sStatus = bPresent
                    ? "The project lives inside a git repository " +
                      "(detected at " + sRepoPath + ") — the " +
                      "Level 1 project-scope requirement."
                    : "No git repository detected around this " +
                      "project. Every vaibify project must live " +
                      "inside its repository.";
                return '<div class="requirement-row-detail">' +
                    '<div class="requirement-row-status">' +
                    fnEscapeHtml(sStatus) + '</div>' +
                    '<div class="requirement-row-howto">Repository ' +
                    'status and actions live in the ' +
                    '<a href="#" class="envelope-open-repos">Repos ' +
                    'panel</a>.</div></div>';
            }}];
    }

    function _flistInputDeclarationRows(dictContext) {
        // The Level 1 input-data contract made visible: every step
        // must list its raw inputs or explicitly declare it needs
        // none. The detail names the undeclared steps and offers the
        // one-click retrofit for a Project predating the contract.
        var listSteps = ((dictContext.dictWorkflow || {})
            .listSteps) || [];
        var listUndeclared = [];
        listSteps.forEach(function (step, iStep) {
            if (step.sStepKind === "ai-declaration") return;
            var bDeclared =
                (step.saInputDataFiles || []).length > 0 ||
                step.bNoInputData === true;
            if (!bDeclared) {
                listUndeclared.push(
                    dictContext.fsComputeStepLabel(iStep));
            }
        });
        var bAllDeclared = listUndeclared.length === 0;
        return [{
            sKey: "inputDeclaration", iLevel: 1,
            sTitle: "Input data declared",
            sState: bAllDeclared ? "green" : "red",
            fsDetail: function () {
                var sStatus = bAllDeclared
                    ? "Every step lists its raw input data or " +
                      "explicitly declares it needs none."
                    : "Undeclared steps: " +
                      listUndeclared.join(", ") + ". A step " +
                      "reaches Level 1 only with an explicit " +
                      "input-data declaration — an input file " +
                      "modified after outputs were generated means " +
                      "the Project is no longer self-consistent.";
                var sAction = bAllDeclared ? "" :
                    '<button type="button" class="btn btn-small ' +
                    'wf-declare-no-input">Declare &quot;no input ' +
                    'data&quot; for all undeclared steps</button>';
                return '<div class="requirement-row-detail">' +
                    '<div class="requirement-row-status">' +
                    fnEscapeHtml(sStatus) + '</div>' + sAction +
                    '</div>';
            }}];
    }

    function _fdictGroupStateByLevel(listRows) {
        // Aggregate the group's rows per level with the shared
        // banner rule (VaibifyUtilities.fsSummarizeLevelStates):
        // all green → attained, every assessed row red → none, any
        // progress in the mix → partial, nothing assessed → unknown.
        var dictByLevel = {};
        for (var iLevel = 1; iLevel <= 3; iLevel++) {
            var listAtLevel = listRows.filter(function (dictRow) {
                return (dictRow.iLevel || 3) === iLevel;
            });
            if (listAtLevel.length === 0) continue;
            var listStates = listAtLevel.map(function (dictRow) {
                return _DICT_MARK_TO_LEVEL_STATE[dictRow.sState] ||
                    "unknown";
            });
            dictByLevel[iLevel] =
                VaibifyUtilities.fsSummarizeLevelStates(listStates);
        }
        return dictByLevel;
    }

    function _fsRenderRequirementGroup(
        sGroupKey, listRows, setExpandedGroups, setExpandedRows,
        sFooterHtml
    ) {
        var bOpen = setExpandedGroups &&
            setExpandedGroups.has(sGroupKey);
        var sHtml = '<div class="requirement-group' +
            (bOpen ? '' : ' collapsed') + '">' +
            '<div class="requirement-group-header" data-group="' +
            sGroupKey + '">' +
            '<span class="requirement-group-title">' +
            _fsExpandTriangle(bOpen) +
            _DICT_GROUP_TITLES[sGroupKey] + '</span>' +
            _fsRenderLevelStrip(
                _fdictGroupStateByLevel(listRows),
                _DICT_GROUP_TITLES[sGroupKey]) + '</div>';
        if (bOpen) {
            sHtml += '<div class="requirement-group-body">';
            for (var i = 0; i < listRows.length; i++) {
                sHtml += _fsRenderRequirementRow(
                    listRows[i], setExpandedRows);
            }
            sHtml += (sFooterHtml || '') + '</div>';
        }
        return sHtml + '</div>';
    }

    function _fsRenderBinaryAddForm(bFormOpen) {
        // Section footer: collapsed to a single "Add package…" button
        // until the researcher asks for it. The form stays open after
        // each add so several packages can be declared in a row; an
        // entry with an already-declared path replaces that entry
        // (which is also how a stale path gets fixed).
        if (!bFormOpen) {
            return '<div class="binary-add-form">' +
                '<button type="button" ' +
                'class="btn wf-toggle-binary-form">' +
                'Add package…</button></div>';
        }
        return '<div class="binary-add-form">' +
            '<div class="requirement-row-howto">Add a package that ' +
            'discovery missed, or fix a stale path (same path ' +
            'replaces the existing entry):</div>' +
            '<input type="text" class="binary-form-path" ' +
            'placeholder="/full/path/to/binary">' +
            '<input type="text" class="binary-form-purpose" ' +
            'placeholder="what it does (one line)">' +
            '<input type="text" class="binary-form-version" ' +
            'placeholder="expected version">' +
            '<div class="requirement-row-actions">' +
            '<button type="button" class="btn wf-action-btn" ' +
            'data-wf-action="declare-binary" data-wf-arg="">' +
            'Add package</button> ' +
            '<button type="button" ' +
            'class="btn wf-toggle-binary-form">Done</button>' +
            '</div></div>';
    }

    function fsRenderProjectBlock(dictContext) {
        var dictDetail = dictContext.dictWorkflowEnvelopeDetail || {};
        var bOpen = dictContext.bProjectBlockCollapsed !== true;
        var sHtml = '<div class="project-block-header">' +
            '<span class="project-block-title" ' +
            'title="Requirements that apply to the project as a ' +
            'whole rather than to any single step. Click the banner ' +
            'to collapse or expand.">' +
            _fsExpandTriangle(bOpen) + 'Project' +
            '</span>' +
            VaibifyStepRenderer.fsBuildLevelStrip(dictContext, -1) +
            '</div>';
        if (!bOpen) return sHtml;
        var listSections = [
            ["repository",
             _flistRepositoryRows(dictContext).concat(
                 _flistInputDeclarationRows(dictContext)), ""],
            ["software", _flistSoftwareRows(dictDetail),
             _fsRenderBinaryAddForm(
                 dictContext.bBinaryAddFormOpen === true)],
            ["artifacts", _flistArtifactRows(dictDetail), ""],
            ["determinism", _flistDeterminismRows(dictDetail), ""],
            // Two parallel published-copy sections, one per level.
            // "Published copies" answers Level 2 — is the generating
            // DATA published — and "Published envelope" answers Level
            // 3 — is what a third party needs in order to RE-RUN it
            // published. Same comparison, same network pass, disjoint
            // files. They are separate sections rather than one list
            // because a researcher scanning for why their data is
            // unpublished must not find a reproduce.sh problem among
            // the answers; that coupling is what the scope split
            // removed from the gates, and a merged section would put
            // it straight back on the screen.
            ["publishedCopies",
             _flistPublishedCopiesRows(dictDetail), ""],
            ["publishedEnvelope",
             _flistEnvelopeMirrorRows(dictDetail), ""],
            ["ai", _flistAiRows(dictDetail), ""],
            ["attestation",
             _flistAttestationRows(dictDetail, dictContext), ""],
        ];
        var sBody = '<div class="project-block-body">';
        for (var i = 0; i < listSections.length; i++) {
            sBody += _fsRenderRequirementGroup(
                listSections[i][0], listSections[i][1],
                dictContext.setExpandedRequirementGroups,
                dictContext.setExpandedRequirementRows,
                listSections[i][2]);
        }
        return sHtml + sBody + '</div>';
    }

    return {
        fsRenderProjectBlock: fsRenderProjectBlock,
    };
})();
