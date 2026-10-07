/* Vaibify — Uploads what was dropped onto the Files panel.

   One drop is one BATCH. Everything the drop decided is frozen into the
   batch object when it starts -- the container, the destination folder,
   the view the page was showing, the lease -- because the researcher
   keeps working while a large upload runs: opening another folder must
   not redirect the files still waiting, and opening another project
   must stop them rather than land them in a place nobody chose.

   Files go one at a time, each as a streamed PUT whose body is the file
   itself, so a file of any size crosses in bounded memory. The server
   decides what is allowed (the panel renders its verdict and never
   derives one) and every refusal arrives as a sentence the researcher
   reads in the summary: nothing is swallowed, and nothing is replaced
   unless the researcher said so for that name. Replacing is asked of
   the server (bReplaceAllowed) only for names confirmed here; for any
   other name an existing file answers 409 and is kept. The listing the
   confirmation is based on is a hint -- a file that appeared since is
   caught by that 409. */

var VaibifyFileUpload = (function () {
    "use strict";

    var _I_LISTED_REFUSALS_LIMIT = 20;
    var _I_SERVER_ERROR_STATUS = 500;
    var _dictActiveBatch = null;

    /* --- The batch --- */

    function _fdictCreateBatch(dictDrop) {
        return {
            sContainerId: dictDrop.sContainerId,
            sDestination: dictDrop.sDestination,
            iViewGeneration: VaibifyApp.fiGetViewGeneration(),
            sLeaseId: VaibifyApp.fsGetLeaseId(),
            sPhase: "preparing",
            listItems: [],
            iTotalBytes: 0,
            iBytesCompleted: 0,
            iBytesCurrent: 0,
            iItemsCompleted: 0,
            dictCurrentUpload: null,
            bCancelled: false,
            bUnexpectedFailure: false,
            sStopReason: "",
            listLanded: [],
            listRefused: [],
            listKeptByChoice: [],
            listSkippedPaths: [],
            listUnreadable: [],
        };
    }

    function _fsItemPath(dictItem) {
        return dictItem.sRelativePath
            ? dictItem.sRelativePath + "/" + dictItem.sFilename
            : dictItem.sFilename;
    }

    function _fdictBuildItem(sRelativePath, sName, file) {
        return {
            sRelativePath: sRelativePath,
            sFilename: sName,
            file: file,
            bDirectory: file === null,
            iSizeBytes: file ? file.size : 0,
            bReplaceConfirmed: false,
        };
    }

    function _fnAdoptPlan(dictBatch, dictPlan) {
        dictPlan.listEmptyDirectories.forEach(function (dictDirectory) {
            dictBatch.listItems.push(_fdictBuildItem(
                dictDirectory.sRelativePath, dictDirectory.sName, null));
        });
        dictPlan.listFileItems.forEach(function (dictFileItem) {
            dictBatch.listItems.push(_fdictBuildItem(
                dictFileItem.sRelativePath, dictFileItem.sName,
                dictFileItem.file));
        });
        dictBatch.listSkippedPaths = dictPlan.listSkippedPaths;
        dictBatch.listUnreadable = dictPlan.listUnreadable;
        _fnRecomputeTotalBytes(dictBatch);
    }

    function _fnRecomputeTotalBytes(dictBatch) {
        dictBatch.iTotalBytes = dictBatch.listItems.reduce(
            function (iSum, dictItem) { return iSum + dictItem.iSizeBytes; },
            0);
    }

    function _fsReasonBatchMustStop(dictBatch) {
        if (dictBatch.bCancelled) return "You cancelled the upload.";
        if (VaibifyApp.fsGetContainerId() !== dictBatch.sContainerId ||
            VaibifyApp.fiGetViewGeneration() !== dictBatch.iViewGeneration) {
            return "You opened another project or left this one, so " +
                "the rest of the upload was stopped.";
        }
        if (VaibifyApp.fsGetLeaseId() !== dictBatch.sLeaseId) {
            return "This project's claim changed during the upload, so " +
                "the rest of it was stopped. Drop the remaining files " +
                "again.";
        }
        return "";
    }

    /* --- Preparing: read the drop, ask the server, confirm --- */

    function _fsBuildVerdictUrl(dictBatch) {
        return "/api/upload/" + encodeURIComponent(dictBatch.sContainerId) +
            "/verdict?sDirectory=" +
            encodeURIComponent(dictBatch.sDestination) +
            "&iTotalBytes=" + dictBatch.iTotalBytes;
    }

    function _flistNamesToConfirm(dictBatch, setExistingNames) {
        return dictBatch.listItems.filter(function (dictItem) {
            return !dictItem.bDirectory && dictItem.sRelativePath === "" &&
                setExistingNames.has(dictItem.sFilename);
        }).map(function (dictItem) { return dictItem.sFilename; });
    }

    function _fsDescribeReplacement(listNames) {
        var listShown = listNames.slice(0, _I_LISTED_REFUSALS_LIMIT);
        var sMore = listNames.length > listShown.length
            ? "\n...and " + (listNames.length - listShown.length) + " more"
            : "";
        return "These already exist in this folder:\n" +
            listShown.join("\n") + sMore +
            "\n\nReplace them with the files you dropped?";
    }

    function _fpromiseAskToReplace(listNames) {
        return new Promise(function (fnResolve) {
            VaibifyApp.fnShowConfirmModal(
                "Replace existing files?", _fsDescribeReplacement(listNames),
                function () { fnResolve(true); },
                {
                    sConfirmLabel: "Replace",
                    sCancelLabel: "Keep the existing files",
                    fnOnCancel: function () { fnResolve(false); },
                });
        });
    }

    function _fnApplyReplacementAnswer(dictBatch, listNames, bReplace) {
        var setNames = new Set(listNames);
        var listAttempted = [];
        dictBatch.listItems.forEach(function (dictItem) {
            var bAsked = !dictItem.bDirectory &&
                dictItem.sRelativePath === "" &&
                setNames.has(dictItem.sFilename);
            if (bAsked && !bReplace) {
                dictBatch.listKeptByChoice.push(dictItem.sFilename);
                return;
            }
            dictItem.bReplaceConfirmed = bAsked;
            listAttempted.push(dictItem);
        });
        dictBatch.listItems = listAttempted;
        _fnRecomputeTotalBytes(dictBatch);
    }

    async function _fnConfirmReplacements(dictBatch, setExistingNames) {
        var listNames = _flistNamesToConfirm(dictBatch, setExistingNames);
        if (listNames.length === 0) return;
        var bReplace = await _fpromiseAskToReplace(listNames);
        _fnApplyReplacementAnswer(dictBatch, listNames, bReplace);
    }

    function _fnExplainAnEmptyPlan(dictBatch) {
        /* Skipped and unreadable names have their own lines in the
           summary; a drop that held none of them held nothing. */
        var bAccountedFor = dictBatch.listSkippedPaths.length > 0 ||
            dictBatch.listUnreadable.length > 0;
        if (!bAccountedFor) {
            dictBatch.sStopReason =
                "There was nothing to upload in what you dropped.";
        }
    }

    async function _fnPrepareBatch(dictBatch, dictDrop) {
        _fnShowStatus("Reading what you dropped...", 0);
        _fnAdoptPlan(dictBatch,
            await VaibifyFileDropWalker.fdictWalkEntries(dictDrop.listEntries));
        if (dictBatch.listItems.length === 0) {
            _fnExplainAnEmptyPlan(dictBatch);
            return;
        }
        var dictVerdict = await VaibifyApi.fdictGet(
            _fsBuildVerdictUrl(dictBatch));
        if (!dictVerdict.bUploadAllowed) {
            dictBatch.sStopReason = dictVerdict.sUploadRefusal;
            return;
        }
        dictBatch.sPhase = "confirming";
        await _fnConfirmReplacements(dictBatch, dictDrop.setExistingNames);
    }

    /* --- Sending --- */

    function _fsBuildUploadUrl(dictBatch, dictItem) {
        var dictParameters = {
            sDestination: dictBatch.sDestination,
            sFilename: dictItem.sFilename,
            iSizeBytes: dictItem.iSizeBytes,
            bReplaceAllowed: dictItem.bReplaceConfirmed,
            bDirectory: dictItem.bDirectory,
        };
        if (dictItem.sRelativePath) {
            dictParameters.sRelativePath = dictItem.sRelativePath;
        }
        return "/api/upload/" + encodeURIComponent(dictBatch.sContainerId) +
            "/stream?" + Object.keys(dictParameters).map(function (sKey) {
                return sKey + "=" + encodeURIComponent(dictParameters[sKey]);
            }).join("&");
    }

    function _fnRecordOutcome(dictBatch, dictItem, dictOutcome) {
        dictBatch.iBytesCompleted += dictItem.iSizeBytes;
        dictBatch.iBytesCurrent = 0;
        dictBatch.iItemsCompleted += 1;
        if (dictOutcome.bOk) {
            dictBatch.listLanded.push(dictItem);
            return;
        }
        if (dictOutcome.bAborted) {
            dictBatch.sStopReason = "You cancelled the upload.";
            return;
        }
        dictBatch.listRefused.push({
            sPath: _fsItemPath(dictItem),
            iStatus: dictOutcome.iStatus,
            sReason: dictOutcome.sMessage,
        });
        if (dictOutcome.iStatus === 0 ||
            dictOutcome.iStatus >= _I_SERVER_ERROR_STATUS) {
            dictBatch.bUnexpectedFailure = true;
        }
        if (dictOutcome.bNetworkFailure || dictOutcome.iStatus === 401) {
            dictBatch.sStopReason = dictOutcome.sMessage;
        }
    }

    async function _fnUploadOneItem(dictBatch, dictItem) {
        _fnRenderProgress(dictBatch, dictItem);
        var dictUpload = VaibifyApi.fdictStartUpload(
            _fsBuildUploadUrl(dictBatch, dictItem),
            dictItem.bDirectory ? null : dictItem.file,
            function (iBytesSent) {
                dictBatch.iBytesCurrent = iBytesSent;
                _fnRenderProgress(dictBatch, dictItem);
            });
        dictBatch.dictCurrentUpload = dictUpload;
        var dictOutcome = await dictUpload.promiseOutcome;
        dictBatch.dictCurrentUpload = null;
        _fnRecordOutcome(dictBatch, dictItem, dictOutcome);
    }

    async function _fnSendItems(dictBatch) {
        dictBatch.sPhase = "sending";
        for (var iItem = 0; iItem < dictBatch.listItems.length; iItem++) {
            dictBatch.sStopReason = dictBatch.sStopReason ||
                _fsReasonBatchMustStop(dictBatch);
            if (dictBatch.sStopReason) return;
            await _fnUploadOneItem(dictBatch, dictBatch.listItems[iItem]);
        }
    }

    async function _fnRunBatch(dictBatch, dictDrop) {
        try {
            await _fnPrepareBatch(dictBatch, dictDrop);
            if (!dictBatch.sStopReason) await _fnSendItems(dictBatch);
        } catch (error) {
            dictBatch.bUnexpectedFailure = true;
            dictBatch.sStopReason = "The upload could not go on: " +
                VaibifyDiagnosis.fsExplainError(error);
        }
    }

    function _fbActiveBatchBlocksANewDrop() {
        /* A batch waiting for the researcher's answer to a modal does
           not block: the modal covers the page, so nothing can be
           dropped under it, and a modal replaced by another dialog
           would otherwise leave every later drop refused. */
        return Boolean(_dictActiveBatch) &&
            ["preparing", "sending"].indexOf(
                _dictActiveBatch.sPhase) !== -1;
    }

    async function fpromiseUploadDropped(dictDrop) {
        /* dictDrop: sContainerId and sDestination (what the drop
           landed on), listEntries (captured synchronously from the
           DataTransfer), setExistingNames (the names the destination's
           listing showed), fnOnFinished. */
        if (_fbActiveBatchBlocksANewDrop()) {
            VaibifyApp.fnShowToast(
                "An upload is still running. Wait for it to finish, or " +
                "cancel it, before dropping more.", "warning");
            return;
        }
        var dictBatch = _fdictCreateBatch(dictDrop);
        _dictActiveBatch = dictBatch;
        _fnClearSummary();
        await _fnRunBatch(dictBatch, dictDrop);
        dictBatch.sPhase = "finished";
        _fnHideStatus();
        _fnRenderSummary(dictBatch);
        if (_dictActiveBatch === dictBatch) _dictActiveBatch = null;
        if (dictDrop.fnOnFinished) dictDrop.fnOnFinished();
    }

    function fnCancelActiveBatch() {
        if (!_dictActiveBatch) return;
        _dictActiveBatch.bCancelled = true;
        if (_dictActiveBatch.dictCurrentUpload) {
            _dictActiveBatch.dictCurrentUpload.fnAbort();
        }
    }

    function fbBatchIsRunning() {
        return Boolean(_dictActiveBatch &&
            _dictActiveBatch.sPhase === "sending");
    }

    /* --- Progress: one bar for the whole batch --- */

    function _fsFormatByteCount(iBytes) {
        var fValue = iBytes;
        var listUnits = ["B", "KB", "MB", "GB", "TB"];
        var iUnit = 0;
        while (fValue >= 1000 && iUnit < listUnits.length - 1) {
            fValue /= 1000;
            iUnit += 1;
        }
        return (iUnit === 0 ? String(fValue) : fValue.toFixed(1)) +
            " " + listUnits[iUnit];
    }

    function _ffFractionDone(dictBatch) {
        var fFraction = dictBatch.iTotalBytes > 0
            ? (dictBatch.iBytesCompleted + dictBatch.iBytesCurrent) /
                dictBatch.iTotalBytes
            : dictBatch.iItemsCompleted /
                Math.max(dictBatch.listItems.length, 1);
        return Math.min(fFraction, 1);
    }

    function _fnShowStatus(sLabel, fFraction) {
        document.getElementById("fileUploadStatus").hidden = false;
        document.getElementById("fileUploadProgress").value = fFraction;
        document.getElementById("fileUploadProgressLabel").textContent =
            sLabel;
    }

    function _fnHideStatus() {
        document.getElementById("fileUploadStatus").hidden = true;
    }

    function _fnRenderProgress(dictBatch, dictItem) {
        var fFraction = _ffFractionDone(dictBatch);
        var sBytes = dictBatch.iTotalBytes > 0
            ? " (" + _fsFormatByteCount(
                dictBatch.iBytesCompleted + dictBatch.iBytesCurrent) +
              " of " + _fsFormatByteCount(dictBatch.iTotalBytes) + ")"
            : "";
        _fnShowStatus(
            "Uploading " + (dictBatch.iItemsCompleted + 1) + " of " +
            dictBatch.listItems.length + ": " + _fsItemPath(dictItem) +
            " - " + Math.round(fFraction * 100) + "%" + sBytes,
            fFraction);
    }

    /* --- One summary at the end --- */

    function _fnClearSummary() {
        var elSummary = document.getElementById("fileUploadSummary");
        elSummary.textContent = "";
        elSummary.hidden = true;
    }

    function _fsPlural(iCount, sSingular, sPlural) {
        return iCount + " " + (iCount === 1 ? sSingular : sPlural);
    }

    function _fsDescribeLanded(dictBatch) {
        var iFolders = dictBatch.listLanded.filter(function (dictItem) {
            return dictItem.bDirectory;
        }).length;
        var iFiles = dictBatch.listLanded.length - iFolders;
        var listParts = [];
        if (iFiles) listParts.push(_fsPlural(iFiles, "file", "files"));
        if (iFolders) listParts.push(_fsPlural(iFolders, "folder", "folders"));
        return listParts.join(" and ");
    }

    function _fiBytesLanded(dictBatch) {
        return dictBatch.listLanded.reduce(function (iSum, dictItem) {
            return iSum + dictItem.iSizeBytes;
        }, 0);
    }

    function _fsBuildHeadline(dictBatch) {
        var sLanded = _fsDescribeLanded(dictBatch);
        var iNotUploaded = dictBatch.listItems.length -
            dictBatch.listLanded.length;
        var listSentences = [];
        if (sLanded) {
            listSentences.push("Uploaded " + sLanded + " (" +
                _fsFormatByteCount(_fiBytesLanded(dictBatch)) + ") to " +
                dictBatch.sDestination + ".");
        }
        if (sLanded && iNotUploaded > 0) {
            listSentences.push(_fsPlural(
                iNotUploaded, "item was", "items were") + " not uploaded.");
        }
        if (dictBatch.sStopReason) {
            listSentences.push(
                (sLanded ? "Stopped: " : "Nothing was uploaded. ") +
                dictBatch.sStopReason);
        }
        return listSentences.join(" ") ||
            "Nothing was uploaded.";
    }

    function _fnAppendList(elSummary, sHeading, listLines) {
        if (!listLines.length) return;
        var elHeading = document.createElement("div");
        elHeading.className = "file-upload-summary-heading";
        elHeading.textContent = sHeading;
        elSummary.appendChild(elHeading);
        var elList = document.createElement("ul");
        listLines.slice(0, _I_LISTED_REFUSALS_LIMIT).forEach(
            function (sLine) {
                var elLine = document.createElement("li");
                elLine.textContent = sLine;
                elList.appendChild(elLine);
            });
        if (listLines.length > _I_LISTED_REFUSALS_LIMIT) {
            var elMore = document.createElement("li");
            elMore.textContent = "...and " +
                (listLines.length - _I_LISTED_REFUSALS_LIMIT) + " more";
            elList.appendChild(elMore);
        }
        elSummary.appendChild(elList);
    }

    function _flistDescribeRefusals(dictBatch) {
        return dictBatch.listRefused.map(function (dictRefusal) {
            return dictRefusal.sPath + " - " + dictRefusal.sReason;
        });
    }

    function _flistDescribeSkipped(dictBatch) {
        return dictBatch.listSkippedPaths.map(function (sPath) {
            return sPath + " (repository internals are never uploaded)";
        });
    }

    function _flistDescribeUnreadable(dictBatch) {
        return dictBatch.listUnreadable.map(function (dictUnreadable) {
            return dictUnreadable.sPath + " - " + dictUnreadable.sReason;
        });
    }

    function _fnAppendDiagnosis(elSummary, dictBatch) {
        if (!dictBatch.bUnexpectedFailure) return;
        var elButton = document.createElement("button");
        elButton.type = "button";
        elButton.className = "diagnosis-link";
        elButton.textContent = "Run a diagnosis";
        elButton.addEventListener("click", function () {
            VaibifyDiagnosis.fnShowDoctorReport(_fsBuildHeadline(dictBatch));
        });
        elSummary.appendChild(elButton);
    }

    function _fnAppendDismiss(elSummary) {
        var elButton = document.createElement("button");
        elButton.type = "button";
        elButton.className = "btn file-upload-dismiss";
        elButton.textContent = "Dismiss";
        elButton.addEventListener("click", _fnClearSummary);
        elSummary.appendChild(elButton);
    }

    function _fnRenderSummary(dictBatch) {
        var elSummary = document.getElementById("fileUploadSummary");
        elSummary.textContent = "";
        var bProblem = Boolean(dictBatch.sStopReason ||
            dictBatch.listRefused.length || dictBatch.listUnreadable.length);
        elSummary.classList.toggle("file-upload-summary--problem", bProblem);
        var elHeadline = document.createElement("div");
        elHeadline.className = "file-upload-summary-headline";
        elHeadline.textContent = _fsBuildHeadline(dictBatch);
        elSummary.appendChild(elHeadline);
        _fnAppendList(elSummary, "Not uploaded:",
            _flistDescribeRefusals(dictBatch));
        _fnAppendList(elSummary, "Kept as they were (you chose not to " +
            "replace them):", dictBatch.listKeptByChoice);
        _fnAppendList(elSummary, "Skipped:", _flistDescribeSkipped(dictBatch));
        _fnAppendList(elSummary, "Your browser could not read:",
            _flistDescribeUnreadable(dictBatch));
        _fnAppendDiagnosis(elSummary, dictBatch);
        _fnAppendDismiss(elSummary);
        elSummary.hidden = false;
    }

    function fnBindUploadControls() {
        var elCancel = document.getElementById("btnCancelUpload");
        if (elCancel) elCancel.addEventListener("click", fnCancelActiveBatch);
    }

    document.addEventListener("DOMContentLoaded", fnBindUploadControls);

    return {
        fpromiseUploadDropped: fpromiseUploadDropped,
        fnCancelActiveBatch: fnCancelActiveBatch,
        fbBatchIsRunning: fbBatchIsRunning,
    };
})();
