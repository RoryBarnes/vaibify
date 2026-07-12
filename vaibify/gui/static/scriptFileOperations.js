/* Vaibify — File existence checking, status coloring, and change detection */

var PipeleyenFileOps = (function () {
    "use strict";

    var fbIsBinaryFile = VaibifyUtilities.fbIsBinaryFile;

    /* --- Clipboard --- */

    function fnCopyToClipboard(sText) {
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(sText).then(function () {
                PipeleyenApp.fnShowToast("Copied to clipboard", "success");
            }).catch(function () {
                _fnCopyToClipboardFallback(sText);
            });
        } else {
            _fnCopyToClipboardFallback(sText);
        }
    }

    function _fnCopyToClipboardFallback(sText) {
        var elTextarea = document.createElement("textarea");
        elTextarea.value = sText;
        elTextarea.style.position = "fixed";
        elTextarea.style.opacity = "0";
        document.body.appendChild(elTextarea);
        elTextarea.select();
        try {
            document.execCommand("copy");
            PipeleyenApp.fnShowToast("Copied to clipboard", "success");
        } catch (e) {
            PipeleyenApp.fnShowToast("Copy failed", "error");
        }
        document.body.removeChild(elTextarea);
    }

    /* --- Inline Editing --- */

    function fnInlineEditItem(el, iStep, sArray, iIdx) {
        var dictWorkflow = PipeleyenApp.fdictGetWorkflow();
        var sRaw = dictWorkflow.listSteps[iStep][sArray][iIdx];
        var elText = el.querySelector(".detail-text");
        var elOverflowButton = el.querySelector(".row-overflow-btn");

        var elInput = document.createElement("input");
        elInput.type = "text";
        elInput.className = "detail-edit-input";
        elInput.value = sRaw;
        elText.style.display = "none";
        el.insertBefore(elInput, elOverflowButton);
        elInput.focus();
        elInput.select();

        var bFinished = false;
        function fnFinishEdit() {
            if (bFinished) return;
            bFinished = true;
            var sNewValue = elInput.value.trim();
            if (sNewValue && sNewValue !== sRaw) {
                dictWorkflow.listSteps[iStep][sArray][iIdx] =
                    sNewValue;
                PipeleyenApp.fnSaveStepArray(iStep, sArray, true);
            }
            elInput.removeEventListener("blur", fnFinishEdit);
            elInput.remove();
            elText.style.display = "";
            PipeleyenApp.fnRenderStepList();
        }

        elInput.addEventListener("keydown", function (event) {
            if (event.key === "Enter") fnFinishEdit();
            if (event.key === "Escape") {
                bFinished = true;
                elInput.removeEventListener("blur", fnFinishEdit);
                elInput.remove();
                elText.style.display = "";
            }
        });
        elInput.addEventListener("blur", fnFinishEdit);
    }

    /* --- File Existence Checking --- */

    var I_MAX_FILE_CACHE_ENTRIES = 500;

    function fnSetFileExistenceCache(dictCache, sKey, bValue) {
        if (Object.keys(dictCache).length >=
            I_MAX_FILE_CACHE_ENTRIES) {
            var listKeys = Object.keys(dictCache);
            for (var i = 0; i < listKeys.length; i++) {
                delete dictCache[listKeys[i]];
            }
        }
        dictCache[sKey] = bValue;
    }

    function fnScheduleFileExistenceCheck(dictState) {
        if (dictState.iFileCheckTimer) return;
        dictState.iFileCheckTimer = setTimeout(function () {
            dictState.iFileCheckTimer = null;
            dictState.bFileCheckInProgress = false;
            dictState.iInflightRequests = 0;
            _fnRunBatchedExistenceCheck(dictState);
        }, 200);
    }

    function _fnRunBatchedExistenceCheck(dictState) {
        var sContainerId = PipeleyenApp.fsGetContainerId();
        if (!sContainerId) return;
        var dictPlan = _fdictPlanExistenceRequests(dictState);
        if (dictPlan.listPaths.length === 0) {
            _fnAfterExistenceResolved(dictPlan, {}, dictState);
            return;
        }
        dictState.bFileCheckInProgress = true;
        VaibifyApi.fdictPost(
            "/api/files/" + sContainerId + "/exist",
            {saRelativePaths: dictPlan.listPaths}
        ).then(function (dictResponse) {
            var dictExists = (dictResponse &&
                dictResponse.dictExists) || {};
            _fnAfterExistenceResolved(
                dictPlan, dictExists, dictState
            );
        }).catch(function () {
            dictState.bFileCheckInProgress = false;
        });
    }

    function _fnAfterExistenceResolved(
        dictPlan, dictExists, dictState
    ) {
        _fnApplyOutputExistence(
            dictPlan.listOutputItems, dictExists, dictState
        );
        _fnApplyDataExistence(
            dictPlan.listDataItems, dictExists, dictState
        );
        dictState.bFileCheckInProgress = false;
    }

    function _fsComposeAbsoluteOrRelative(sResolved, sWorkdir) {
        if (!sResolved) return "";
        if (sResolved.charAt(0) === "/") return sResolved;
        if (!sWorkdir) return sResolved;
        return sWorkdir.replace(/\/+$/, "") + "/" + sResolved;
    }

    function _fdictPlanExistenceRequests(dictState) {
        var dictPlan = {
            listPaths: [],
            listOutputItems: [],
            listDataItems: [],
        };
        var setSeenPaths = {};
        _fnCollectOutputElementPlan(dictPlan, setSeenPaths, dictState);
        _fnCollectDataFilePlan(dictPlan, setSeenPaths, dictState);
        return dictPlan;
    }

    function _fnCollectOutputElementPlan(
        dictPlan, setSeenPaths, dictState
    ) {
        document.querySelectorAll(
            '.detail-item.output'
        ).forEach(function (el) {
            var dictItem = _fdictOutputItemForPlan(el, dictState);
            if (!dictItem) return;
            dictPlan.listOutputItems.push(dictItem);
            if (dictItem.bNeedsLookup &&
                !setSeenPaths[dictItem.sLookupPath]) {
                setSeenPaths[dictItem.sLookupPath] = true;
                dictPlan.listPaths.push(dictItem.sLookupPath);
            }
        });
    }

    function _fdictOutputItemForPlan(el, dictState) {
        var elText = el.querySelector(".detail-text");
        if (!elText || elText.classList.contains("file-invalid")) {
            return null;
        }
        var sResolved = el.dataset.resolved || "";
        var sWorkdir = el.dataset.workdir || "";
        var sCacheKey = el.dataset.step + ":" + sResolved +
            ":" + sWorkdir;
        var bCachedTrue =
            dictState.dictFileExistenceCache[sCacheKey] === true;
        var bCachedFalse =
            dictState.dictFileExistenceCache[sCacheKey] === false;
        return {
            el: el,
            sCacheKey: sCacheKey,
            // data-resolved already carries the renderer's workdir
            // join (fsRenderDetailItem prepends sWorkdir to relative
            // output paths). Composing it with the workdir AGAIN
            // built "XuvEvolution/XuvEvolution/…", the server said
            // "missing", and every existing file with a repo-relative
            // step directory rendered red (live bug, 2026-07-03).
            sLookupPath: sResolved,
            bCachedTrue: bCachedTrue,
            bCachedFalse: bCachedFalse,
            bNeedsLookup: !bCachedTrue && !bCachedFalse,
        };
    }

    function _fnCollectDataFilePlan(
        dictPlan, setSeenPaths, dictState
    ) {
        var dictWorkflow = PipeleyenApp.fdictGetWorkflow();
        if (!dictWorkflow) return;
        var setExpanded = PipeleyenApp.fsetGetExpandedSteps();
        dictWorkflow.listSteps.forEach(function (step, iStep) {
            if (!setExpanded.has(iStep)) return;
            _fnCollectStepDataPlan(
                step, iStep, dictPlan, setSeenPaths, dictState
            );
        });
    }

    function _fnCollectStepDataPlan(
        step, iStep, dictPlan, setSeenPaths, dictState
    ) {
        if (PipeleyenTestManager.fsetGetStepsWithData()
            .has(iStep)) return;
        var listNecessary = _flistNecessaryDataFiles(step, iStep);
        if (listNecessary.length === 0) return;
        var sDir = step.sDirectory || "";
        listNecessary.forEach(function (sFile) {
            var sCacheKey = iStep + ":" + sFile;
            var bCached =
                dictState.dictFileExistenceCache[sCacheKey] === true;
            var sLookup =
                _fsComposeAbsoluteOrRelative(sFile, sDir);
            dictPlan.listDataItems.push({
                iStep: iStep,
                sFile: sFile,
                sCacheKey: sCacheKey,
                sLookupPath: sLookup,
                iStepTotal: listNecessary.length,
                bCachedTrue: bCached,
            });
            if (!bCached && !setSeenPaths[sLookup]) {
                setSeenPaths[sLookup] = true;
                dictPlan.listPaths.push(sLookup);
            }
        });
    }

    function _fnApplyOutputExistence(
        listOutputItems, dictExists, dictState
    ) {
        var dictDataCounts = {};
        var dictDataPresent = {};
        listOutputItems.forEach(function (dictItem) {
            var bExists = _fbResolveItemExistence(dictItem, dictExists);
            _fnRecordItemExistenceCache(dictItem, bExists, dictState);
            fnUpdateFileStatus(dictItem.el, bExists);
            _fnTrackOutputItemForCounts(
                dictItem.el, bExists, dictDataCounts, dictDataPresent
            );
        });
    }

    function _fbResolveItemExistence(dictItem, dictExists) {
        if (dictItem.bCachedTrue) return true;
        if (dictItem.bCachedFalse) return false;
        return !!dictExists[dictItem.sLookupPath];
    }

    function _fnRecordItemExistenceCache(dictItem, bExists, dictState) {
        if (dictItem.bCachedTrue || dictItem.bCachedFalse) return;
        fnSetFileExistenceCache(
            dictState.dictFileExistenceCache,
            dictItem.sCacheKey, bExists
        );
    }

    function _fnTrackOutputItemForCounts(
        el, bExists, dictDataCounts, dictDataPresent
    ) {
        var iStep = parseInt(el.dataset.step);
        var sArray = el.dataset.array;
        var sRaw = el.dataset.raw || "";
        var bNecessaryData = sArray === "saDataFiles" &&
            PipeleyenApp.fsGetFileCategory(
                iStep, sRaw, sArray) === "archive";
        if (bNecessaryData) {
            dictDataCounts[iStep] =
                (dictDataCounts[iStep] || 0) + 1;
        }
        if (bExists) {
            _fnTrackDataPresence(
                iStep, bNecessaryData,
                dictDataCounts, dictDataPresent
            );
        }
    }

    function _fnApplyDataExistence(
        listDataItems, dictExists, dictState
    ) {
        var dictPresentCount = {};
        listDataItems.forEach(function (dictItem) {
            var bExists = dictItem.bCachedTrue ||
                !!dictExists[dictItem.sLookupPath];
            if (!bExists) return;
            fnSetFileExistenceCache(
                dictState.dictFileExistenceCache,
                dictItem.sCacheKey, true
            );
            dictPresentCount[dictItem.iStep] =
                (dictPresentCount[dictItem.iStep] || 0) + 1;
            if (dictPresentCount[dictItem.iStep] >=
                dictItem.iStepTotal) {
                PipeleyenTestManager.fsetGetStepsWithData()
                    .add(dictItem.iStep);
                _fnUpdateGenerateButton(dictItem.iStep);
            }
        });
    }

    function fnCheckDataFileExistence(dictState) {
        /* Compatibility shim: the batched scheduler now covers both
           output and data file checks in one round-trip. Callers that
           still poke this function trigger a fresh batched pass. */
        fnScheduleFileExistenceCheck(dictState);
    }

    function fnCheckStepDataFilesPublic(step, iStep, dictState) {
        /* Compatibility shim: per-step checks fold into the batched
           scheduler — issuing one batched POST instead of N HEADs. */
        fnScheduleFileExistenceCheck(dictState);
    }

    function _flistNecessaryDataFiles(step, iStep) {
        var listData = step.saDataFiles || [];
        return listData.filter(function (sFile) {
            return PipeleyenApp.fsGetFileCategory(
                iStep, sFile, "saDataFiles"
            ) === "archive";
        });
    }

    function fnCheckOutputFileExistence(dictState) {
        /* Compatibility shim: the batched scheduler now covers both
           output and data file checks in one round-trip; route
           callers through it to keep the wire footprint flat. */
        fnScheduleFileExistenceCheck(dictState);
    }

    function _fnTrackDataPresence(
        iStep, bNecessaryData, dictCounts, dictPresent
    ) {
        if (!bNecessaryData) return;
        dictPresent[iStep] = (dictPresent[iStep] || 0) + 1;
        if (dictPresent[iStep] >= (dictCounts[iStep] || 0)) {
            PipeleyenTestManager.fsetGetStepsWithData().add(iStep);
            _fnUpdateGenerateButton(iStep);
        }
    }

    function _fnUpdateGenerateButton(iStep) {
        var elBtn = document.querySelector(
            '.btn-generate-test[data-step="' + iStep + '"]'
        );
        if (elBtn) {
            elBtn.disabled = false;
        }
    }

    /* --- File Status Classes --- */

    var LIST_FILE_STATUS_CLASSES = [
        "file-necessary-red", "file-necessary-orange",
        "file-necessary-valid", "file-supplementary-valid",
        "file-supplementary-missing", "file-binary",
        "file-pending",
        "file-missing-state", "file-stale-state",
        "file-unattested-state",
    ];

    function _fnRemoveAllFileStatusClasses(elText) {
        LIST_FILE_STATUS_CLASSES.forEach(function (sCls) {
            elText.classList.remove(sCls);
        });
    }

    function fnUpdateFileStatus(el, bExists) {
        var elText = el.querySelector(".detail-text");
        if (!elText) return;
        var iStep = parseInt(el.dataset.step);
        var sArrayKey = el.dataset.array;
        var sRaw = el.dataset.raw || "";
        var sResolved = el.dataset.resolved || "";
        _fnRemoveAllFileStatusClasses(elText);
        var sClass = _fsComputeFileStatusClass(
            iStep, sArrayKey, sRaw, sResolved, bExists
        );
        elText.classList.add(sClass);
        if (sClass === "file-necessary-red") {
            elText.classList.add(_fsRedModifierClass(
                iStep, sResolved, bExists
            ));
        }
        _fnApplyBlockerTooltip(elText, iStep, sRaw);
    }

    function _fsRedModifierClass(iStep, sResolved, bExists) {
        // Section G: red-disambiguation modifier. Picks the modifier
        // for the .file-necessary-red treatment so the researcher can
        // tell missing apart from stale apart from never-attested.
        if (!bExists) return "file-missing-state";
        var dictWorkflow = PipeleyenApp.fdictGetWorkflow();
        var dictStep = (dictWorkflow.listSteps || [])[iStep] || {};
        var dictVerify = PipeleyenApp.fdictGetVerification(dictStep);
        if ((dictVerify.listModifiedFiles || []).length > 0) {
            return "file-stale-state";
        }
        return "file-unattested-state";
    }

    function _fnApplyBlockerTooltip(elText, iStep, sRaw) {
        // Section G: tooltip on file-list red glyphs sources from the
        // per-file ``dictOffendingFileHints`` entry, then the owning
        // blocker's ``sRemediationHint`` — but only when the file
        // appears in a blocker's ``listOffendingFiles``. Files that
        // are not individually offending keep the resolved-path title;
        // the step-level hint belongs to the banner glyph, not here.
        if (!PipeleyenApp.fsBlockerHintForFile) return;
        var sHint = PipeleyenApp.fsBlockerHintForFile(iStep, sRaw);
        if (sHint) {
            elText.setAttribute("title", sHint);
        }
    }

    function _fsComputeFileStatusClass(
        iStep, sArrayKey, sRaw, sResolved, bExists
    ) {
        if (fbIsBinaryFile(sRaw)) return "file-binary";
        var sCategory = PipeleyenApp.fsGetFileCategory(
            iStep, sRaw, sArrayKey
        );
        if (sCategory === "supporting") {
            return bExists ?
                "file-supplementary-valid" :
                "file-supplementary-missing";
        }
        return _fsNecessaryFileClass(iStep, sResolved, bExists);
    }

    function _fsNecessaryFileClass(iStep, sResolved, bExists) {
        if (!bExists) return "file-necessary-red";
        var dictWorkflow = PipeleyenApp.fdictGetWorkflow();
        var dictStep = dictWorkflow.listSteps[iStep];
        var dictVerify = PipeleyenApp.fdictGetVerification(dictStep);
        var listModified = dictVerify.listModifiedFiles || [];
        if (_fbFileInModifiedList(sResolved, listModified)) {
            return "file-necessary-red";
        }
        if (PipeleyenApp.fbStepIsAtLeastLevel1(
            dictStep, iStep)) {
            return "file-necessary-valid";
        }
        return "file-necessary-orange";
    }

    function _fbFileInModifiedList(sResolved, listModified) {
        if (!sResolved || listModified.length === 0) return false;
        if (listModified.indexOf(sResolved) !== -1) return true;
        for (var i = 0; i < listModified.length; i++) {
            // Legacy fallback: a workflow whose stored
            // listModifiedFiles still has absolute container paths
            // (pre-2026-04 wire format) until the loader migration
            // runs on the next connect.
            // TODO(2026-07-01): remove — at least one release after
            // the 2026-04-23 wire-format switchover, by which point
            // all workflows will have been loaded once and migrated.
            if (listModified[i].endsWith("/" + sResolved)) {
                return true;
            }
        }
        return false;
    }

    function fsInitialFileStatusClass(iStep, sArrayKey, sRaw) {
        if (fbIsBinaryFile(sRaw)) return "file-binary";
        return "file-pending";
    }

    function fbIsFileMissing(elText, dictFileExistenceCache) {
        if (elText.classList.contains(
            "file-supplementary-missing")) {
            return true;
        }
        if (!elText.classList.contains("file-necessary-red")) {
            return false;
        }
        var elItem = elText.closest(".detail-item");
        if (!elItem) return true;
        var sResolved = elItem.dataset.resolved || "";
        var sCacheKey = elItem.dataset.step + ":" +
            sResolved + ":" + (elItem.dataset.workdir || "");
        return dictFileExistenceCache[sCacheKey] === false;
    }

    /* --- File Change Detection --- */

    function fnDetectOutputFileChanges(
        dictNewMods, dictState
    ) {
        for (var sPath in dictNewMods) {
            if (dictState.dictFileModTimes[sPath] !==
                dictNewMods[sPath]) {
                dictState.dictFileModTimes = dictNewMods;
                dictState.dictFileExistenceCache = {};
                fnScheduleFileExistenceCheck(dictState);
                PipeleyenApp.fnRenderStepList();
                return;
            }
        }
    }

    function fnUpdateScriptStatus(
        dictNewScriptStatus, dictState
    ) {
        if (!dictNewScriptStatus) return;
        var dictModified = {};
        var dictStaleArtifacts = {};
        Object.keys(dictNewScriptStatus).forEach(function (sKey) {
            var dictEntry = dictNewScriptStatus[sKey];
            dictModified[sKey] = dictEntry.sStatus;
            dictStaleArtifacts[sKey] =
                dictEntry.listStaleArtifacts || [];
        });
        var sPrev = JSON.stringify(dictState.dictScriptModified);
        var sPrevStale = JSON.stringify(
            dictState.dictStaleArtifacts);
        dictState.dictScriptModified = dictModified;
        dictState.dictStaleArtifacts = dictStaleArtifacts;
        if (JSON.stringify(dictState.dictScriptModified) !== sPrev ||
                JSON.stringify(dictState.dictStaleArtifacts) !==
                sPrevStale) {
            PipeleyenApp.fnRenderStepList();
        }
    }

    return {
        fnCopyToClipboard: fnCopyToClipboard,
        fnInlineEditItem: fnInlineEditItem,
        fnScheduleFileExistenceCheck: fnScheduleFileExistenceCheck,
        fnCheckOutputFileExistence: fnCheckOutputFileExistence,
        fnCheckDataFileExistence: fnCheckDataFileExistence,
        fnCheckStepDataFiles: fnCheckStepDataFilesPublic,
        fnSetFileExistenceCache: fnSetFileExistenceCache,
        fnUpdateFileStatus: fnUpdateFileStatus,
        fsInitialFileStatusClass: fsInitialFileStatusClass,
        fbIsFileMissing: fbIsFileMissing,
        fnDetectOutputFileChanges: fnDetectOutputFileChanges,
        fnUpdateScriptStatus: fnUpdateScriptStatus,
    };
})();
