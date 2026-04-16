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
        var elActions = el.querySelector(".detail-actions");
        elActions.style.display = "none";

        var elInput = document.createElement("input");
        elInput.type = "text";
        elInput.className = "detail-edit-input";
        elInput.value = sRaw;
        elText.style.display = "none";
        el.insertBefore(elInput, elActions);
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
            elActions.style.display = "";
            PipeleyenApp.fnRenderStepList();
        }

        elInput.addEventListener("keydown", function (event) {
            if (event.key === "Enter") fnFinishEdit();
            if (event.key === "Escape") {
                bFinished = true;
                elInput.removeEventListener("blur", fnFinishEdit);
                elInput.remove();
                elText.style.display = "";
                elActions.style.display = "";
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
            fnCheckOutputFileExistence(dictState);
            fnCheckDataFileExistence(dictState);
            if (dictState.iInflightRequests === 0) {
                dictState.bFileCheckInProgress = false;
            } else {
                setTimeout(function () {
                    dictState.bFileCheckInProgress = false;
                }, 10000);
            }
        }, 200);
    }

    function _fnFileCheckComplete(dictState) {
        dictState.iInflightRequests--;
        if (dictState.iInflightRequests <= 0) {
            dictState.bFileCheckInProgress = false;
        }
    }

    function fnCheckDataFileExistence(dictState) {
        var sContainerId = PipeleyenApp.fsGetContainerId();
        var dictWorkflow = PipeleyenApp.fdictGetWorkflow();
        if (!sContainerId || !dictWorkflow) return;
        var setExpanded = PipeleyenApp.fsetGetExpandedSteps();
        dictWorkflow.listSteps.forEach(function (step, iStep) {
            if (!setExpanded.has(iStep)) return;
            _fnCheckStepDataFiles(step, iStep, dictState);
        });
    }

    function fnCheckStepDataFilesPublic(step, iStep, dictState) {
        _fnCheckStepDataFiles(step, iStep, dictState);
    }

    function _fnCheckStepDataFiles(step, iStep, dictState) {
        if (PipeleyenTestManager.fsetGetStepsWithData()
            .has(iStep)) return;
        var listNecessary = _flistNecessaryDataFiles(
            step, iStep);
        if (listNecessary.length === 0) return;
        var sContainerId = PipeleyenApp.fsGetContainerId();
        var iPresent = 0;
        var iTotal = listNecessary.length;
        listNecessary.forEach(function (sFile) {
            var sDir = step.sDirectory || "";
            var sCacheKey = iStep + ":" + sFile;
            if (dictState.dictFileExistenceCache[sCacheKey]) {
                iPresent++;
                if (iPresent >= iTotal) {
                    PipeleyenTestManager.fsetGetStepsWithData()
                        .add(iStep);
                    _fnUpdateGenerateButton(iStep);
                }
                return;
            }
            var sUrl = "/api/figure/" + sContainerId +
                "/" + sFile + "?sWorkdir=" +
                encodeURIComponent(sDir);
            dictState.iInflightRequests++;
            VaibifyApi.fbHead(sUrl).then(
                function (bExists) {
                    if (bExists) {
                        fnSetFileExistenceCache(
                            dictState.dictFileExistenceCache,
                            sCacheKey, true);
                        iPresent++;
                        if (iPresent >= iTotal) {
                            PipeleyenTestManager
                                .fsetGetStepsWithData()
                                .add(iStep);
                            _fnUpdateGenerateButton(iStep);
                        }
                    }
                    _fnFileCheckComplete(dictState);
                }
            ).catch(function () {
                _fnFileCheckComplete(dictState);
            });
        });
    }

    function _flistNecessaryDataFiles(step, iStep) {
        var listData = step.saDataFiles || [];
        return listData.filter(function (sFile) {
            return PipeleyenApp.fsGetFileCategory(
                iStep, sFile, "saDataFiles"
            ) === "archive";
        });
    }

    function fnCheckSingleOutputFile(
        el, dictDataCounts, dictDataPresent,
        signalFileCheck, dictState
    ) {
        var elText = el.querySelector(".detail-text");
        if (!elText || elText.classList.contains("file-invalid")) {
            return;
        }
        var iStep = parseInt(el.dataset.step);
        var sArray = el.dataset.array;
        var sResolved = el.dataset.resolved;
        var sWorkdir = el.dataset.workdir || "";
        var sCacheKey = iStep + ":" + sResolved + ":" + sWorkdir;
        var sRaw = el.dataset.raw || "";
        var bNecessaryData = sArray === "saDataFiles" &&
            PipeleyenApp.fsGetFileCategory(
                iStep, sRaw, sArray) === "archive";
        if (bNecessaryData) {
            dictDataCounts[iStep] =
                (dictDataCounts[iStep] || 0) + 1;
        }
        if (dictState.dictFileExistenceCache[sCacheKey] === true) {
            fnUpdateFileStatus(el, true);
            _fnTrackDataPresence(
                iStep, bNecessaryData,
                dictDataCounts, dictDataPresent
            );
            return;
        }
        if (dictState.dictFileExistenceCache[sCacheKey] === false) {
            fnUpdateFileStatus(el, false);
            return;
        }
        var sContainerId = PipeleyenApp.fsGetContainerId();
        var sUrl = "/api/figure/" + sContainerId + "/" + sResolved;
        if (sWorkdir) {
            sUrl += "?sWorkdir=" + encodeURIComponent(sWorkdir);
        }
        dictState.iInflightRequests++;
        VaibifyApi.fbHead(sUrl, {signal: signalFileCheck})
            .then(function (bExists) {
                if (bExists) {
                    fnSetFileExistenceCache(
                        dictState.dictFileExistenceCache,
                        sCacheKey, true);
                    fnUpdateFileStatus(el, true);
                    _fnTrackDataPresence(
                        iStep, bNecessaryData,
                        dictDataCounts, dictDataPresent
                    );
                } else {
                    fnSetFileExistenceCache(
                        dictState.dictFileExistenceCache,
                        sCacheKey, false);
                    fnUpdateFileStatus(el, false);
                }
                _fnFileCheckComplete(dictState);
            }).catch(function (err) {
                if (err.name === "AbortError") return;
                fnSetFileExistenceCache(
                    dictState.dictFileExistenceCache,
                    sCacheKey, false);
                fnUpdateFileStatus(el, false);
                _fnFileCheckComplete(dictState);
            });
    }

    function fnCheckOutputFileExistence(dictState) {
        var sContainerId = PipeleyenApp.fsGetContainerId();
        if (!sContainerId) return;
        if (dictState.abortControllerFileCheck) {
            dictState.abortControllerFileCheck.abort();
        }
        dictState.abortControllerFileCheck = new AbortController();
        var signalFileCheck =
            dictState.abortControllerFileCheck.signal;
        var dictDataCounts = {};
        var dictDataPresent = {};
        document.querySelectorAll(
            '.detail-item.output'
        ).forEach(function (el) {
            fnCheckSingleOutputFile(
                el, dictDataCounts, dictDataPresent,
                signalFileCheck, dictState
            );
        });
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
        if (PipeleyenApp.fbAllVerificationComplete(
            dictStep, iStep)) {
            return "file-necessary-valid";
        }
        return "file-necessary-orange";
    }

    function _fbFileInModifiedList(sResolved, listModified) {
        if (!sResolved || listModified.length === 0) return false;
        for (var i = 0; i < listModified.length; i++) {
            if (listModified[i] === sResolved) return true;
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
