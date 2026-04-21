/* Vaibify — Sync operations (Overleaf, GitHub, Zenodo) */

var VaibifySyncManager = (function () {
    "use strict";

    var _I_DIFF_DEBOUNCE_MS = 300;

    var _sPushService = "";
    var _sPushTargetDirectory = "";
    var _listPushFiles = [];
    var _dictPushStatusByPath = {};
    var _setUserTickedPaths = new Set();
    var _setUserUntickedPaths = new Set();
    var _listCaseCollisions = [];
    var _sSuggestedTargetDirectory = "";
    var _listConflicts = [];
    var _iDiffRequestToken = 0;
    var _timerDiffDebounce = null;

    var _DICT_SYNC_ERROR_MESSAGES = {
        auth: "Authentication failed. Check your credentials " +
            "in Sync > Setup.",
        rateLimit: "Rate limited. Try again in a few minutes.",
        notFound: "Resource not found. Check your project ID " +
            "or DOI.",
        network: "Network error. Check your container's " +
            "internet connection.",
    };

    async function fnOpenPushModal(sService) {
        var sContainerId = PipeleyenApp.fsGetContainerId();
        if (!sContainerId) return;
        var elToast = _fnShowOpeningToast(sService);
        try {
            var dictResult = await VaibifyApi.fdictGet(
                "/api/sync/" + sContainerId + "/check/" + sService
            );
            if (!dictResult.bConnected) {
                fnShowConnectionSetup(sService);
                return;
            }
            if (typeof VaibifyManifestCheck !== "undefined") {
                var bProceed = await VaibifyManifestCheck.fbRunBeforePush(
                    sContainerId, sService
                );
                if (!bProceed) return;
            }
            _sPushService = sService;
            await fnPopulatePushModal(sService);
        } finally {
            if (elToast && elToast.parentNode) elToast.remove();
        }
    }

    function _fnShowOpeningToast(sService) {
        var elContainer = document.getElementById("toastContainer");
        if (!elContainer) return null;
        var dictLabels = {
            overleaf: "Overleaf",
            github: "GitHub",
            zenodo: "Zenodo",
        };
        var sLabel = dictLabels[sService] || sService;
        var el = document.createElement("div");
        el.className = "toast sticky-diff-toast";
        el.innerHTML =
            '<span class="spinner"></span>' +
            '<span class="sticky-diff-toast-label">' +
            'Preparing ' + sLabel + ' push\u2026' +
            '</span>';
        elContainer.appendChild(el);
        return el;
    }

    var _I_SLOW_DIFF_TOAST_MS = 5000;

    async function fnPopulatePushModal(sService) {
        _fnResetPushState();
        var sContainerId = PipeleyenApp.fsGetContainerId();
        var listFiles = await VaibifyApi.fdictGet(
            "/api/sync/" + sContainerId + "/files" +
            "?sService=" + encodeURIComponent(sService)
        );
        _listPushFiles = listFiles || [];
        var dictNames = {
            overleaf: "Overleaf", zenodo: "Zenodo",
            github: "GitHub",
        };
        document.getElementById("modalPushTitle").textContent =
            "Push to " + dictNames[sService];
        _fnRenderOverleafTargetRow(sService, sContainerId);
        _fnRenderPushAnnotationHost(sService);
        _fnRenderPushFileList();
        _fnApplyButtonLabels();
        _fnUpdatePushButtonStates();
        if (sService === "overleaf") {
            await _fnRunInitialDiffThenShowModal(sContainerId);
            return;
        }
        document.getElementById("modalPush").style.display = "flex";
    }

    async function _fnRunInitialDiffThenShowModal(sContainerId) {
        var elToast = null;
        var iSlowTimer = setTimeout(function () {
            elToast = _fnShowStickyDiffToast();
        }, _I_SLOW_DIFF_TOAST_MS);
        try {
            await _fnPerformDiffRefresh(sContainerId);
        } finally {
            clearTimeout(iSlowTimer);
            if (elToast && elToast.parentNode) elToast.remove();
        }
        document.getElementById("modalPush").style.display = "flex";
    }

    function _fnShowStickyDiffToast() {
        var elContainer = document.getElementById("toastContainer");
        if (!elContainer) return null;
        var el = document.createElement("div");
        el.className = "toast sticky-diff-toast";
        el.innerHTML =
            '<span class="spinner"></span>' +
            '<span class="sticky-diff-toast-label">' +
            'Checking Overleaf remote\u2026' +
            '</span>';
        elContainer.appendChild(el);
        return el;
    }

    function _fnResetPushState() {
        _listPushFiles = [];
        _dictPushStatusByPath = {};
        _setUserTickedPaths.clear();
        _setUserUntickedPaths.clear();
        _listCaseCollisions = [];
        _sSuggestedTargetDirectory = "";
        _listConflicts = [];
        _iDiffRequestToken = 0;
        if (_timerDiffDebounce) {
            clearTimeout(_timerDiffDebounce);
            _timerDiffDebounce = null;
        }
    }

    function _fnApplyButtonLabels() {
        var elSelected = document.getElementById("btnPushSelected");
        var elAll = document.getElementById("btnPushAll");
        if (_sPushService === "overleaf") {
            if (elAll) elAll.style.display = "";
            if (elSelected) elSelected.textContent = "Push Selected";
        } else {
            if (elAll) elAll.style.display = "none";
            if (elSelected) elSelected.textContent = "Push Selected";
        }
    }

    function _fsCurrentOverleafTarget() {
        var dictWorkflow = PipeleyenApp.fdictGetWorkflow();
        if (!dictWorkflow) return "figures";
        return dictWorkflow.sOverleafFigureDirectory || "figures";
    }

    function _fnRenderOverleafTargetRow(sService, sContainerId) {
        var elHost = document.getElementById("modalPushTargetRow");
        if (!elHost) return;
        if (sService !== "overleaf") {
            elHost.innerHTML = "";
            elHost.style.display = "none";
            return;
        }
        elHost.style.display = "";
        _sPushTargetDirectory = _fsCurrentOverleafTarget();
        elHost.innerHTML =
            '<label class="sync-target-label" ' +
            'for="inputPushTarget">Target directory</label>' +
            '<div class="sync-target-row">' +
            '<input type="text" class="sync-target-input" ' +
            'id="inputPushTarget" ' +
            'placeholder="figures">' +
            '<button type="button" class="btn btn-small" ' +
            'id="btnPushTargetBrowse">Browse</button>' +
            '</div>' +
            '<div class="freshness-indicator" ' +
            'id="pushFreshnessIndicator"></div>';
        _fnUpdateTargetDisplay();
        _fnBindTargetInput(sContainerId);
        _fnBindTargetBrowse(sContainerId);
        _fnRefreshFreshnessIndicatorInModal(sContainerId);
    }

    function _fnRenderPushAnnotationHost(sService) {
        var elList = document.getElementById("modalPushFileList");
        if (!elList) return;
        var elExisting = document.getElementById("pushAnnotationHost");
        if (elExisting) elExisting.remove();
        if (sService !== "overleaf") return;
        var elHost = document.createElement("div");
        elHost.id = "pushAnnotationHost";
        elHost.className = "push-annotation-host";
        elList.parentNode.insertBefore(elHost, elList);
    }

    function _fnUpdateTargetDisplay() {
        var elInput = document.getElementById("inputPushTarget");
        if (elInput) {
            elInput.value = _sPushTargetDirectory || "";
        }
    }

    function _fnBindTargetInput(sContainerId) {
        var elInput = document.getElementById("inputPushTarget");
        if (!elInput) return;
        elInput.addEventListener("input", function () {
            _sPushTargetDirectory = elInput.value.trim();
            _fnScheduleDiffRefresh(sContainerId);
        });
    }

    function _fnBindTargetBrowse(sContainerId) {
        var elBtn = document.getElementById("btnPushTargetBrowse");
        if (!elBtn) return;
        elBtn.addEventListener("click", function () {
            VaibifyOverleafMirror.fnOpenTreePicker(
                sContainerId,
                _sPushTargetDirectory,
                function (sPath) {
                    _sPushTargetDirectory = sPath;
                    _fnUpdateTargetDisplay();
                    _fnScheduleDiffRefresh(sContainerId);
                }
            );
        });
    }

    function _fnRefreshFreshnessIndicatorInModal(sContainerId) {
        var elHost = document.getElementById("pushFreshnessIndicator");
        if (!elHost) return;
        (async function () {
            try {
                await VaibifyOverleafMirror.fnFetchMirrorTree(
                    sContainerId);
            } catch (error) {
                /* graceful: indicator will show "never" */
            }
            VaibifyOverleafMirror.fnRenderFreshnessIndicator(
                elHost, sContainerId);
        })();
    }

    async function _fnInitialOverleafDiff(sContainerId) {
        _fnShowAnnotationPending();
        await _fnPerformDiffRefresh(sContainerId);
    }

    function _fnScheduleDiffRefresh(sContainerId) {
        if (_sPushService !== "overleaf") return;
        _fnShowAnnotationPending();
        if (_timerDiffDebounce) clearTimeout(_timerDiffDebounce);
        _timerDiffDebounce = setTimeout(function () {
            _timerDiffDebounce = null;
            _fnPerformDiffRefresh(sContainerId);
        }, _I_DIFF_DEBOUNCE_MS);
    }

    async function _fnPerformDiffRefresh(sContainerId) {
        var iToken = ++_iDiffRequestToken;
        var listPaths = _listPushFiles.map(function (dictFile) {
            return dictFile.sPath;
        });
        if (listPaths.length === 0) {
            _fnApplyDiffResult({
                listNew: [], listOverwrite: [], listUnchanged: [],
                listConflicts: [], listCaseCollisions: [],
                sSuggestedTargetDirectory: "",
            }, iToken);
            return;
        }
        try {
            var dictResult =
                await VaibifyOverleafMirror.fdictFetchDiffFromServer(
                    sContainerId, listPaths, _sPushTargetDirectory);
            _fnApplyDiffResult(dictResult, iToken);
        } catch (error) {
            if (iToken !== _iDiffRequestToken) return;
            PipeleyenApp.fnShowToast(
                _fsDescribeDiffError(error), "error");
            _fnShowAnnotationError();
        }
    }

    function _fsDescribeDiffError(error) {
        var sMessage = error && error.message ? error.message : "";
        if (sMessage.indexOf("Overleaf project ID not set") !== -1) {
            return "Connect Overleaf first (Sync \u2192 Connect) " +
                "to set the project ID before pushing.";
        }
        return sMessage || "Unable to fetch diff from Overleaf";
    }

    function _fnApplyDiffResult(dictResult, iToken) {
        if (iToken !== _iDiffRequestToken) return;
        _dictPushStatusByPath = _fdictIndexStatuses(dictResult);
        _listConflicts = dictResult.listConflicts || [];
        _listCaseCollisions = dictResult.listCaseCollisions || [];
        _sSuggestedTargetDirectory =
            dictResult.sSuggestedTargetDirectory || "";
        _fnReconcileTickStateForStatusTransitions();
        _fnRenderPushAnnotations();
        _fnRenderPushFileList();
        _fnUpdatePushButtonStates();
    }

    function _fdictIndexStatuses(dictResult) {
        var dictMap = {};
        (dictResult.listNew || []).forEach(function (d) {
            dictMap[d.sLocalPath] = "new";
        });
        (dictResult.listOverwrite || []).forEach(function (d) {
            dictMap[d.sLocalPath] = "overwrite";
        });
        (dictResult.listUnchanged || []).forEach(function (d) {
            dictMap[d.sLocalPath] = "unchanged";
        });
        return dictMap;
    }

    function _fnReconcileTickStateForStatusTransitions() {
        _listPushFiles.forEach(function (dictFile) {
            var sStatus = _dictPushStatusByPath[dictFile.sPath];
            if (sStatus === "unchanged") {
                _setUserTickedPaths.delete(dictFile.sPath);
                _setUserUntickedPaths.delete(dictFile.sPath);
            }
        });
    }

    function _fnShowAnnotationPending() {
        var elHost = document.getElementById("pushAnnotationHost");
        if (!elHost) return;
        elHost.innerHTML =
            '<div class="push-annotation-pending">' +
            'Checking remote\u2026</div>';
    }

    function _fnShowAnnotationError() {
        var elHost = document.getElementById("pushAnnotationHost");
        if (!elHost) return;
        elHost.innerHTML =
            '<div class="push-annotation-pending">' +
            'Remote check failed.</div>';
    }

    function _fnRenderPushAnnotations() {
        var elHost = document.getElementById("pushAnnotationHost");
        if (!elHost) return;
        elHost.innerHTML =
            _fsBuildCaseCollisionBannerHtml() +
            _fsBuildConflictBannerHtml();
        _fnWireCollisionBanner();
        _fnWireConflictCheckbox();
    }

    function _fsBuildCaseCollisionBannerHtml() {
        if (!_listCaseCollisions || _listCaseCollisions.length === 0) {
            return "";
        }
        var sCount = fsFormatFileCount(_listCaseCollisions.length);
        var sButton = "";
        var sIfIgnored = "";
        if (_sSuggestedTargetDirectory) {
            var sSuggested = VaibifyUtilities.fnEscapeHtml(
                _sSuggestedTargetDirectory);
            sButton =
                '<button type="button" class="btn btn-small" ' +
                'id="btnUseCanonicalCase">Use ' +
                sSuggested + '/</button>';
            sIfIgnored =
                '<p class="case-collision-intro">' +
                'Click <strong>Use ' + sSuggested + '/</strong> to ' +
                'snap the target directory to the existing Overleaf ' +
                'path. If you ignore this, the push will use the ' +
                'case you have typed above — Overleaf will touch the ' +
                'existing entry but show it under both spellings, ' +
                'which usually looks like duplicate files.</p>';
        }
        return (
            '<div class="case-collision-banner" role="status">' +
            '<div class="case-collision-heading">' +
            'Case mismatch with Overleaf (' + sCount + ')' +
            '</div>' +
            '<p class="case-collision-intro">' +
            'Overleaf treats directory names case-insensitively. ' +
            'Pushing to a different case than the existing entry ' +
            'can create phantom duplicate files.</p>' +
            sIfIgnored +
            sButton +
            '</div>'
        );
    }

    function _fsBuildConflictBannerHtml() {
        if (!_listConflicts || _listConflicts.length === 0) return "";
        var sRows = _listConflicts.map(
            _fsBuildConflictRowHtml).join("");
        return (
            '<div class="conflict-block" role="alert">' +
            '<div class="conflict-block-heading">' +
            'Conflicts detected (' + _listConflicts.length + ')' +
            '</div>' +
            '<p class="conflict-block-intro">' +
            'These files have been modified on Overleaf since ' +
            'your last push. Pushing will overwrite those changes.' +
            '</p>' +
            sRows +
            '<label class="conflict-override-label">' +
            '<input type="checkbox" id="inputConflictOverride"> ' +
            'Overwrite conflicts anyway' +
            '</label>' +
            '</div>'
        );
    }

    function _fsBuildConflictRowHtml(dictConflict) {
        var sBaseline = (dictConflict.sBaselineDigest || "")
            .substring(0, 8) || "(none)";
        var sCurrent = (dictConflict.sCurrentDigest || "")
            .substring(0, 8) || "(missing)";
        var sLocal = dictConflict.sLocalPath || "";
        var sRemote = dictConflict.sRemotePath || "";
        return (
            '<div class="conflict-row">' +
            '<div class="conflict-row-name">' +
            VaibifyUtilities.fnEscapeHtml(sLocal) +
            ' <span class="diff-remote-path">&rarr; ' +
            VaibifyUtilities.fnEscapeHtml(sRemote) + '</span></div>' +
            '<div class="conflict-row-digests">' +
            '<span>Last pushed: <code>' +
            VaibifyUtilities.fnEscapeHtml(sBaseline) + '</code></span>' +
            '<span>Current remote: <code>' +
            VaibifyUtilities.fnEscapeHtml(sCurrent) + '</code></span>' +
            '</div></div>'
        );
    }

    function _fnWireCollisionBanner() {
        var elBtn = document.getElementById("btnUseCanonicalCase");
        if (!elBtn) return;
        elBtn.addEventListener("click", function () {
            _sPushTargetDirectory = _sSuggestedTargetDirectory;
            _fnUpdateTargetDisplay();
            var sContainerId = PipeleyenApp.fsGetContainerId();
            _fnScheduleDiffRefresh(sContainerId);
        });
    }

    function _fnWireConflictCheckbox() {
        var elCheckbox = document.getElementById(
            "inputConflictOverride");
        if (!elCheckbox) return;
        elCheckbox.addEventListener("change",
            _fnUpdatePushButtonStates);
    }

    function _fnRenderPushFileList() {
        var elList = document.getElementById("modalPushFileList");
        if (!elList) return;
        var bOverleaf = _sPushService === "overleaf";
        elList.innerHTML = _listPushFiles.map(function (dictFile) {
            return _fsBuildPushFileRowHtml(dictFile, bOverleaf);
        }).join("");
        _fnBindPushFileRowEvents();
    }

    function _fsBuildPushFileRowHtml(dictFile, bOverleaf) {
        var bSupporting = bOverleaf &&
            dictFile.sCategory === "supporting";
        if (bSupporting) {
            return _fsBuildSupportingRowHtml(dictFile);
        }
        var sStatus = _dictPushStatusByPath[dictFile.sPath] || "";
        var bUnchanged = sStatus === "unchanged";
        var bChecked = _fbRowShouldBeChecked(
            dictFile.sPath, sStatus);
        var sClass = "push-file-row" +
            (bUnchanged ? " push-file-unchanged" : "");
        var sBadge = sStatus
            ? _fsBuildStatusBadgeHtml(sStatus)
            : "";
        return (
            '<div class="' + sClass + '">' +
            '<input type="checkbox" class="push-file-checkbox" ' +
            'data-path="' +
            VaibifyUtilities.fnEscapeHtml(dictFile.sPath) + '"' +
            (bChecked ? " checked" : "") +
            (bUnchanged ? " disabled" : "") + '>' +
            '<span class="push-file-name">' +
            VaibifyUtilities.fnEscapeHtml(dictFile.sPath) +
            '</span>' + sBadge +
            '</div>'
        );
    }

    function _fsBuildSupportingRowHtml(dictFile) {
        return (
            '<div class="push-file-row push-file-supporting">' +
            '<input type="checkbox" class="push-file-checkbox" ' +
            'data-path="' +
            VaibifyUtilities.fnEscapeHtml(dictFile.sPath) +
            '" disabled>' +
            '<span class="push-file-name">' +
            VaibifyUtilities.fnEscapeHtml(dictFile.sPath) +
            ' (supporting)</span></div>'
        );
    }

    function _fsBuildStatusBadgeHtml(sStatus) {
        var dictLabels = {
            "new": "new",
            "overwrite": "overwrite",
            "unchanged": "unchanged",
        };
        var sLabel = dictLabels[sStatus] || sStatus;
        return (
            '<span class="push-file-status-badge badge-' +
            VaibifyUtilities.fnEscapeHtml(sStatus) + '">' +
            VaibifyUtilities.fnEscapeHtml(sLabel) + '</span>'
        );
    }

    function _fbRowShouldBeChecked(sPath, sStatus) {
        if (sStatus === "unchanged") return false;
        if (sStatus === "new" || sStatus === "overwrite") {
            if (_setUserUntickedPaths.has(sPath)) return false;
            return true;
        }
        if (_setUserTickedPaths.has(sPath)) return true;
        if (_setUserUntickedPaths.has(sPath)) return false;
        return true;
    }

    function _fnBindPushFileRowEvents() {
        document.querySelectorAll(".push-file-checkbox").forEach(
            function (elCheckbox) {
                elCheckbox.addEventListener(
                    "change", _fnHandleRowToggle);
            });
    }

    function _fnHandleRowToggle(event) {
        var sPath = event.currentTarget.dataset.path;
        if (!sPath) return;
        if (event.currentTarget.checked) {
            _setUserTickedPaths.add(sPath);
            _setUserUntickedPaths.delete(sPath);
        } else {
            _setUserUntickedPaths.add(sPath);
            _setUserTickedPaths.delete(sPath);
        }
        _fnUpdatePushButtonStates();
    }

    function _flistSelectedPaths() {
        var listPaths = [];
        document.querySelectorAll(
            ".push-file-checkbox:checked"
        ).forEach(function (el) {
            listPaths.push(el.dataset.path);
        });
        return listPaths;
    }

    function _flistAllPushablePaths() {
        var listPaths = [];
        _listPushFiles.forEach(function (dictFile) {
            var sStatus = _dictPushStatusByPath[dictFile.sPath];
            if (sStatus === "new" || sStatus === "overwrite") {
                listPaths.push(dictFile.sPath);
            }
        });
        return listPaths;
    }

    function _fbSubmissionIsBlockedByConflicts(listPaths) {
        if (!_listConflicts || _listConflicts.length === 0) {
            return false;
        }
        var setPaths = new Set(listPaths);
        var bAnyConflicted = _listConflicts.some(function (dict) {
            return setPaths.has(dict.sLocalPath);
        });
        if (!bAnyConflicted) return false;
        var elCheckbox = document.getElementById(
            "inputConflictOverride");
        return !(elCheckbox && elCheckbox.checked);
    }

    function _fnUpdatePushButtonStates() {
        var elSelected = document.getElementById("btnPushSelected");
        var elAll = document.getElementById("btnPushAll");
        if (_sPushService !== "overleaf") {
            if (elSelected) {
                elSelected.disabled =
                    _flistSelectedPaths().length === 0;
            }
            return;
        }
        var listSelected = _flistSelectedPaths();
        var listAll = _flistAllPushablePaths();
        if (elSelected) {
            elSelected.disabled =
                listSelected.length === 0 ||
                _fbSubmissionIsBlockedByConflicts(listSelected);
        }
        if (elAll) {
            elAll.disabled =
                listAll.length === 0 ||
                _fbSubmissionIsBlockedByConflicts(listAll);
        }
    }

    async function fnHandlePushSelected() {
        var listPaths = _flistSelectedPaths();
        await _fnBeginPush(listPaths);
    }

    async function fnHandlePushAll() {
        if (_sPushService !== "overleaf") {
            await fnHandlePushSelected();
            return;
        }
        var listPaths = _flistAllPushablePaths();
        await _fnBeginPush(listPaths);
    }

    async function _fnBeginPush(listPaths) {
        if (listPaths.length === 0) {
            PipeleyenApp.fnShowToast("No files selected", "error");
            return;
        }
        if (_sPushService === "overleaf") {
            if (!_fbValidateTargetDirectory()) return;
            _fnConfirmAndDispatchOverleafPush(listPaths);
            return;
        }
        await _fnDispatchPush(listPaths);
    }

    function _fbValidateTargetDirectory() {
        var sTarget = (_sPushTargetDirectory || "").trim();
        if (sTarget === "") return true;
        if (sTarget.charAt(0) === "/" || sTarget.charAt(0) === "\\") {
            PipeleyenApp.fnShowToast(
                "Target directory must not start with a slash.",
                "error");
            return false;
        }
        var listSegments = sTarget.split("/");
        for (var iIndex = 0; iIndex < listSegments.length; iIndex += 1) {
            if (listSegments[iIndex] === "..") {
                PipeleyenApp.fnShowToast(
                    "Target directory must not contain '..'.",
                    "error");
                return false;
            }
        }
        return true;
    }

    function _fnConfirmAndDispatchOverleafPush(listPaths) {
        var dictWorkflow = PipeleyenApp.fdictGetWorkflow() || {};
        var sProjectId = dictWorkflow.sOverleafProjectId || "";
        var sMessage = "Push " + fsFormatFileCount(listPaths.length) +
            " to Overleaf project `" + sProjectId + "`?";
        var dictDetails = {
            sDetails: listPaths.join("\n"),
        };
        PipeleyenModals.fnShowConfirmModal(
            "Confirm Overleaf push",
            sMessage,
            function () { _fnDispatchPush(listPaths); },
            dictDetails
        );
    }

    async function _fnDispatchPush(listPaths) {
        document.getElementById("modalPush").style.display = "none";
        PipeleyenApp.fnShowToast(
            "Pushing " + fsFormatFileCount(listPaths.length) +
            "...", "success");
        var sEndpoint = _fsServiceEndpoint(_sPushService);
        var sAction = _fsServiceAction(_sPushService);
        var sContainerId = PipeleyenApp.fsGetContainerId();
        var dictBody = {listFilePaths: listPaths};
        if (_sPushService === "overleaf" && _sPushTargetDirectory) {
            dictBody.sTargetDirectory = _sPushTargetDirectory;
        }
        try {
            var dictResult = await VaibifyApi.fdictPost(
                sEndpoint + sContainerId + "/" + sAction, dictBody
            );
            if (!dictResult.bSuccess) {
                fnShowSyncError(dictResult, _sPushService);
                return;
            }
            PipeleyenApp.fnShowToast("Push complete!", "success");
            PipeleyenApp.fnRenderStepList();
        } catch (error) {
            PipeleyenApp.fnShowToast(
                _fsSanitizeError(error.message), "error");
        }
    }

    function fsFormatFileCount(iCount) {
        if (iCount === 1) return "1 file";
        return iCount + " files";
    }

    function fnShowSyncError(dictResult, sService) {
        var sErrorType = dictResult.sErrorType || "unknown";
        var sMessage = _DICT_SYNC_ERROR_MESSAGES[sErrorType] ||
            dictResult.sMessage || "Unknown error";
        var sTitle = (sService || "Sync") + " failed: " +
            sErrorType;
        _fnShowErrorModal(sTitle + "\n\n" + sMessage);
    }

    function _fsServiceEndpoint(sService) {
        if (sService === "overleaf") return "/api/overleaf/";
        if (sService === "zenodo") return "/api/zenodo/";
        return "/api/github/";
    }

    function _fsServiceAction(sService) {
        if (sService === "zenodo") return "archive";
        return "push";
    }

    function fnBindPushModalEvents() {
        document.getElementById("btnPushCancel").addEventListener(
            "click", function () {
                document.getElementById("modalPush")
                    .style.display = "none";
            }
        );
        document.getElementById("btnPushSelected").addEventListener(
            "click", fnHandlePushSelected);
        var elAll = document.getElementById("btnPushAll");
        if (elAll) {
            elAll.addEventListener("click", fnHandlePushAll);
        }
        fnBindConnectionSetupEvents();
    }

    async function fnShowConnectionSetup(sService) {
        var elModal = document.getElementById("modalConnectionSetup");
        elModal.dataset.service = sService;
        var elProjectId = document.getElementById(
            "groupSetupProjectId");
        var elToken = document.getElementById("groupSetupToken");
        elProjectId.style.display = "none";
        elToken.style.display = "none";
        if (sService === "overleaf") {
            await _fnSetupOverleafFields(elProjectId, elToken, elModal);
        } else if (sService === "zenodo") {
            _fnSetupZenodoFields(elToken, elModal);
        } else {
            PipeleyenApp.fnShowToast(
                "GitHub uses gh auth. Run 'gh auth login' " +
                "on your host machine.", "error"
            );
            return;
        }
        elModal.style.display = "flex";
    }

    async function _fnSetupOverleafFields(elProjectId, elToken, elModal) {
        elProjectId.style.display = "";
        elToken.style.display = "";
        _fnApplyOverleafLabels();
        document.getElementById("modalConnectionTitle")
            .textContent = "Connect to Overleaf";
        _fnRemoveOverleafReuseRow();
        var bHasStored = await _fbHostHasOverleafCredential();
        if (bHasStored) {
            _fnShowOverleafReuseOption(elModal);
        }
    }

    function _fnApplyOverleafLabels() {
        var elLabel = document.getElementById("labelSetupToken");
        var elHelp = document.getElementById("helpSetupToken");
        elLabel.textContent = "Overleaf Git Token ";
        if (elHelp) {
            elHelp.setAttribute("title",
                "Overleaf has no direct upload API, so vaibify " +
                "pushes via its git bridge. This needs a git " +
                "authentication token (not your login password). " +
                "On overleaf.com, open Account Settings and find " +
                "the Git integration section to generate one. " +
                "Paste the token here.");
            elLabel.appendChild(elHelp);
        }
    }

    async function _fbHostHasOverleafCredential() {
        var sContainerId = PipeleyenApp.fsGetContainerId();
        if (!sContainerId) return false;
        try {
            var dictResult = await VaibifyApi.fdictGet(
                "/api/sync/" + sContainerId +
                "/has-credential/overleaf"
            );
            return !!(dictResult && dictResult.bHasCredential);
        } catch (error) {
            return false;
        }
    }

    function _fnRemoveOverleafReuseRow() {
        var elExisting = document.getElementById(
            "rowOverleafReuse");
        if (elExisting) elExisting.remove();
    }

    function _fnShowOverleafReuseOption(elModal) {
        var elToken = document.getElementById("groupSetupToken");
        elToken.style.display = "none";
        var elRow = document.createElement("div");
        elRow.id = "rowOverleafReuse";
        elRow.className = "setup-reuse-row";
        elRow.innerHTML =
            '<p>A saved Overleaf token is already on this host. ' +
            'You can reuse it or replace it.</p>' +
            '<div class="setup-reuse-buttons">' +
            '<button type="button" id="btnOverleafReuse">' +
            'Reuse saved token</button>' +
            '<button type="button" id="btnOverleafReplace">' +
            'Enter new token</button></div>';
        elToken.parentNode.insertBefore(elRow, elToken);
        document.getElementById("btnOverleafReuse")
            .addEventListener("click", _fnHandleOverleafReuse);
        document.getElementById("btnOverleafReplace")
            .addEventListener("click", function () {
                elRow.remove();
                elToken.style.display = "";
            });
    }

    async function _fnHandleOverleafReuse() {
        var elModal = document.getElementById("modalConnectionSetup");
        var sProjectId = document.getElementById(
            "inputSetupProjectId").value.trim();
        var sContainerId = PipeleyenApp.fsGetContainerId();
        var dictBody = {
            sService: "overleaf", sProjectId: sProjectId,
        };
        try {
            var dictResult = await VaibifyApi.fdictPost(
                "/api/sync/" + sContainerId + "/setup", dictBody
            );
            elModal.style.display = "none";
            if (dictResult.bConnected) {
                PipeleyenApp.fnShowToast("Connected!", "success");
                fnOpenPushModal("overleaf");
            } else {
                PipeleyenApp.fnShowToast(
                    dictResult.sMessage || "Connection failed",
                    "error"
                );
            }
        } catch (error) {
            PipeleyenApp.fnShowToast(
                _fsSanitizeError(error.message), "error");
        }
    }

    function _fnSetupZenodoFields(elToken, elModal) {
        elToken.style.display = "";
        var elLabel = document.getElementById("labelSetupToken");
        var elHelp = document.getElementById("helpSetupToken");
        elLabel.textContent = "Zenodo API Token ";
        if (elHelp) elLabel.appendChild(elHelp);
        document.getElementById("modalConnectionTitle")
            .textContent = "Connect to Zenodo";
    }

    function fnBindConnectionSetupEvents() {
        document.getElementById("btnSetupCancel").addEventListener(
            "click", function () {
                document.getElementById("modalConnectionSetup")
                    .style.display = "none";
            }
        );
        document.getElementById("btnSetupSave").addEventListener(
            "click", _fnHandleSetupSave
        );
        document.addEventListener("click", function (event) {
            var elHelp = event.target.closest(".help-icon");
            if (!elHelp) return;
            var sText = elHelp.getAttribute("title");
            if (!sText) return;
            event.preventDefault();
            event.stopPropagation();
            fnShowHelpPopup(sText);
        });
    }

    function fnShowHelpPopup(sText) {
        var elExisting = document.getElementById("popupHelp");
        if (elExisting) elExisting.remove();
        var elPopup = document.createElement("div");
        elPopup.id = "popupHelp";
        elPopup.className = "help-popup";
        elPopup.innerHTML =
            '<div class="help-popup-content">' +
            '<span class="help-popup-close">&times;</span>' +
            '<p>' + VaibifyUtilities.fnEscapeHtml(sText) + '</p></div>';
        document.body.appendChild(elPopup);
        elPopup.querySelector(".help-popup-close").addEventListener(
            "click", function () { elPopup.remove(); }
        );
    }

    async function _fnHandleSetupSave() {
        var elModal = document.getElementById("modalConnectionSetup");
        var sService = elModal.dataset.service;
        var dictBody = { sService: sService };
        var sProjectId = document.getElementById(
            "inputSetupProjectId").value.trim();
        var sToken = document.getElementById(
            "inputSetupToken").value.trim();
        if (sProjectId) dictBody.sProjectId = sProjectId;
        if (sToken) dictBody.sToken = sToken;
        var sContainerId = PipeleyenApp.fsGetContainerId();
        try {
            var dictResult = await VaibifyApi.fdictPost(
                "/api/sync/" + sContainerId + "/setup",
                dictBody
            );
            elModal.style.display = "none";
            if (dictResult.bConnected) {
                PipeleyenApp.fnShowToast("Connected!", "success");
                fnOpenPushModal(sService);
            } else {
                PipeleyenApp.fnShowToast(
                    dictResult.sMessage || "Connection failed",
                    "error"
                );
            }
        } catch (error) {
            PipeleyenApp.fnShowToast(
                _fsSanitizeError(error.message), "error");
        }
    }

    function _fsSanitizeError(sMessage) {
        return VaibifyUtilities.fsSanitizeErrorForUser(sMessage);
    }

    function _fnShowErrorModal(sMessage) {
        var elModal = document.getElementById("modalError");
        var elContent = document.getElementById("modalErrorContent");
        elContent.textContent = _fsSanitizeError(sMessage);
        elModal.style.display = "flex";
    }

    var _DICT_REMOTE_KEY_TO_SERVICE = {
        sGithub: "Github",
        sOverleaf: "Overleaf",
        sZenodo: "Zenodo",
    };

    async function fnToggleRemoteTracking(
        sRemoteKey, sResolved, sWorkdir, bShiftClick,
    ) {
        var sContainerId = PipeleyenApp.fsGetContainerId();
        if (!sContainerId) return;
        var dictTriple = VaibifyGitBadges.fdictGetBadgesForFile(
            sResolved, sWorkdir);
        var sCurrentState = dictTriple[sRemoteKey] || "none";
        var bCurrentlyTracked = sCurrentState !== "none";
        var listToFlip = [sRemoteKey];
        if (bShiftClick && !bCurrentlyTracked) {
            listToFlip = ["sGithub", "sOverleaf", "sZenodo"];
        }
        try {
            for (var i = 0; i < listToFlip.length; i++) {
                await VaibifyApi.fdictPost(
                    "/api/sync/" + sContainerId + "/track",
                    {
                        sPath: sResolved,
                        sService:
                            _DICT_REMOTE_KEY_TO_SERVICE[listToFlip[i]],
                        bTrack: !bCurrentlyTracked,
                    }
                );
            }
            await VaibifyGitBadges.fnRefresh(sContainerId);
            PipeleyenApp.fnRenderStepList();
        } catch (error) {
            PipeleyenApp.fnShowToast(
                _fsSanitizeError(error.message), "error");
        }
    }

    return {
        fnOpenPushModal: fnOpenPushModal,
        fnBindPushModalEvents: fnBindPushModalEvents,
        fnShowSyncError: fnShowSyncError,
        fnShowHelpPopup: fnShowHelpPopup,
        fnToggleRemoteTracking: fnToggleRemoteTracking,
        fsFormatFileCount: fsFormatFileCount,
    };
})();
