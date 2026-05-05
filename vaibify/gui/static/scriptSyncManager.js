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
    var _setActiveFileSyncs = new Set();

    var _DICT_SYNC_ERROR_MESSAGES = {
        auth: "Authentication failed. Check your credentials " +
            "in Sync > Setup.",
        rateLimit: "Rate limited. Try again in a few minutes.",
        notFound: "Resource not found. Check the remote " +
            "configuration.",
        network: "Network error. Check your container's " +
            "internet connection.",
        verifyFailed: "Verification failed. The remote could not " +
            "be reached or returned an unexpected response.",
        manifestMissing: "No manifest found. Generate one before " +
            "verifying.",
        conflict: "Remote has changes you don't have locally. " +
            "Pull or fetch first, then retry.",
        noChanges: "Nothing to push — files match what is already " +
            "on the remote.",
        unknown: "Sync failed for an unrecognized reason. Check " +
            "the toast detail or container logs.",
    };

    var _DICT_SERVICE_ERROR_MESSAGES = {
        GitHub: {
            notFound: "Repository or branch not found. Check the " +
                "GitHub remote configuration in workflow.yml.",
            conflict: "Push rejected: GitHub has commits you " +
                "don't have. Run 'git pull --rebase' first.",
        },
        Zenodo: {
            notFound: "Resource not found. Check your project ID " +
                "or DOI.",
            conflict: "Deposit is already published or in an " +
                "unexpected state. Check the Zenodo deposit page.",
        },
        Overleaf: {
            notFound: "Overleaf project not found. Check the " +
                "workflow's Overleaf URL.",
        },
    };

    var _DICT_SERVICE_LABEL_BY_LOWERCASE = {
        github: "GitHub",
        zenodo: "Zenodo",
        overleaf: "Overleaf",
    };

    function _fsCanonicalServiceLabel(sService) {
        if (!sService) return "";
        var sLower = String(sService).toLowerCase();
        return _DICT_SERVICE_LABEL_BY_LOWERCASE[sLower] || sService;
    }

    function _fsLookupSyncErrorMessage(sService, sErrorType) {
        var sCanonical = _fsCanonicalServiceLabel(sService);
        var dictForService =
            _DICT_SERVICE_ERROR_MESSAGES[sCanonical] || {};
        if (dictForService[sErrorType]) {
            return dictForService[sErrorType];
        }
        return _DICT_SYNC_ERROR_MESSAGES[sErrorType] || "";
    }

    var _LIST_VERIFY_SERVICES = ["github", "overleaf", "zenodo"];
    var _DICT_VERIFY_SERVICE_LABELS = {
        github: "GitHub",
        overleaf: "Overleaf",
        zenodo: "Zenodo",
    };
    var _I_VERIFY_STALE_AGE_MS = 24 * 60 * 60 * 1000;
    var _dictVerifyStatusCache = {};
    var _setActiveVerifyServices = new Set();
    var _dictTimerByService = {};
    var _sCacheWorkflowId = "";

    async function fnOpenPushModal(sService) {
        var sContainerId = PipeleyenApp.fsGetContainerId();
        if (!sContainerId) return;
        if (await _fbContainerIsIsolated(sContainerId)) {
            _fnShowIsolationBlockedToast(sService);
            return;
        }
        var elToast = _fnShowOpeningToast(sService);
        try {
            var dictResult = await VaibifyApi.fdictGet(
                "/api/sync/" + sContainerId + "/check/" + sService
            );
            if (!dictResult.bConnected) {
                fnShowConnectionSetup(sService);
                return;
            }
            if (sService === "zenodo") {
                var bMetaOk = await _fbEnsureZenodoMetadataBeforePush(
                    sContainerId);
                if (!bMetaOk) return;
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

    async function _fbContainerIsIsolated(sContainerId) {
        try {
            var dictResult = await VaibifyApi.fdictGet(
                "/api/containers/"
                + encodeURIComponent(sContainerId)
                + "/isolation"
            );
            return dictResult && dictResult.bNetworkIsolation === true;
        } catch (error) {
            return false;
        }
    }

    function _fnShowIsolationBlockedToast(sService) {
        var dictLabels = {
            overleaf: "Overleaf", github: "GitHub", zenodo: "Zenodo",
        };
        var sLabel = dictLabels[sService] || sService;
        PipeleyenApp.fnShowToast(
            sLabel + " push disabled: container is in isolation "
            + "mode (no network). Disable in vaibify.yml: "
            + "networkIsolation: false, then rebuild.",
            "warning",
        );
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
        _fnToggleEditZenodoMetadataButton(sService);
        _fnRenderOverleafTargetRow(sService, sContainerId);
        _fnRenderPushAnnotationHost(sService);
        _fnRenderPushFileList();
        _bZenodoHasDeposit = sService === "zenodo" &&
            await _fbWorkflowHasZenodoDeposit(sContainerId);
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

    var _bZenodoHasDeposit = false;

    function _fnApplyButtonLabels() {
        var elSelected = document.getElementById("btnPushSelected");
        var elAll = document.getElementById("btnPushAll");
        if (_sPushService === "overleaf") {
            if (elAll) elAll.style.display = "";
            if (elSelected) elSelected.textContent = "Push Selected";
            return;
        }
        if (elAll) elAll.style.display = "none";
        if (elSelected) {
            elSelected.textContent = (
                _sPushService === "zenodo" && _bZenodoHasDeposit
            ) ? "Publish new version" : "Push Selected";
        }
    }

    async function _fbWorkflowHasZenodoDeposit(sContainerId) {
        try {
            var dictDeposit = await VaibifyApi.fdictGet(
                "/api/zenodo/" + encodeURIComponent(sContainerId) +
                "/deposit"
            );
            return !!(dictDeposit && dictDeposit.sDoi);
        } catch (error) {
            return false;
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
        var sServiceLabel = _fsServiceLabel(_sPushService);
        var elProgress = _fnShowPushProgressToast(
            listPaths.length, sServiceLabel,
        );
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
            _fnRemoveToast(elProgress);
            if (!dictResult.bSuccess) {
                fnShowSyncError(dictResult, _sPushService);
                return;
            }
            if (_sPushService === "zenodo" && dictResult.sDoi) {
                _fnShowZenodoSuccessToast(dictResult);
                if (typeof VaibifyZenodoDepositCard !== "undefined") {
                    VaibifyZenodoDepositCard.fnUpdateFromPushResult(
                        dictResult);
                }
            } else {
                PipeleyenApp.fnShowToast(
                    "Push complete!", "success");
            }
            PipeleyenApp.fnRenderStepList();
        } catch (error) {
            _fnRemoveToast(elProgress);
            PipeleyenApp.fnShowToast(
                _fsSanitizeError(error.message), "error");
        }
    }

    function _fsServiceLabel(sService) {
        if (sService === "overleaf") return "Overleaf";
        if (sService === "zenodo") return "Zenodo";
        return "GitHub";
    }

    function _fnShowPushProgressToast(iCount, sServiceLabel) {
        var elContainer = document.getElementById("toastContainer");
        if (!elContainer) return null;
        var elToast = document.createElement("div");
        elToast.className = "toast toast-progress";
        elToast.innerHTML =
            '<span class="toast-spinner" aria-hidden="true"></span>' +
            '<span class="toast-progress-text">Pushing ' +
            VaibifyUtilities.fnEscapeHtml(fsFormatFileCount(iCount)) +
            ' to ' + VaibifyUtilities.fnEscapeHtml(sServiceLabel) +
            '... this can take a while for large archives.</span>';
        elContainer.appendChild(elToast);
        return elToast;
    }

    function _fnRemoveToast(elToast) {
        if (elToast && elToast.parentNode) {
            elToast.parentNode.removeChild(elToast);
        }
    }

    function _fnShowZenodoSuccessToast(dictResult) {
        var elContainer = document.getElementById("toastContainer");
        if (!elContainer) return;
        var sDoi = VaibifyUtilities.fnEscapeHtml(dictResult.sDoi || "");
        var sUrl = dictResult.sHtmlUrl || "";
        var sSafeUrl = _fbSafeZenodoUrl(sUrl) ?
            VaibifyUtilities.fnEscapeHtml(sUrl) : "";
        var elToast = document.createElement("div");
        elToast.className = "toast success toast-zenodo";
        elToast.innerHTML =
            '<div class="toast-zenodo-title">Published to Zenodo</div>' +
            '<div class="toast-zenodo-doi">DOI: ' +
            '<code>' + sDoi + '</code> ' +
            '<button type="button" class="toast-zenodo-copy" ' +
            'data-doi="' + sDoi + '">Copy</button></div>' +
            (sSafeUrl
                ? '<div class="toast-zenodo-link">' +
                  '<a href="' + sSafeUrl +
                  '" target="_blank" rel="noopener">' +
                  'Open on Zenodo</a></div>'
                : '') +
            '<button class="toast-close">&times;</button>';
        elToast.querySelector(".toast-close").addEventListener(
            "click", function () { elToast.remove(); });
        var elCopy = elToast.querySelector(".toast-zenodo-copy");
        if (elCopy) {
            elCopy.addEventListener("click", function () {
                _fnCopyToClipboard(dictResult.sDoi || "");
                elCopy.textContent = "Copied";
            });
        }
        elContainer.appendChild(elToast);
    }

    function _fbSafeZenodoUrl(sUrl) {
        if (!sUrl) return false;
        return sUrl.indexOf("https://zenodo.org/") === 0 ||
            sUrl.indexOf("https://sandbox.zenodo.org/") === 0;
    }

    function _fnCopyToClipboard(sText) {
        if (!sText) return;
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(sText);
            return;
        }
        var elTmp = document.createElement("textarea");
        elTmp.value = sText;
        document.body.appendChild(elTmp);
        elTmp.select();
        try { document.execCommand("copy"); } catch (e) { /* noop */ }
        document.body.removeChild(elTmp);
    }

    function fsFormatFileCount(iCount) {
        if (iCount === 1) return "1 file";
        return iCount + " files";
    }

    function fnShowSyncError(dictResult, sService) {
        var sErrorType = dictResult.sErrorType || "unknown";
        var sMessage = _fsLookupSyncErrorMessage(
            sService, sErrorType) ||
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
        var elEditMeta = document.getElementById(
            "btnPushEditZenodoMetadata");
        if (elEditMeta) {
            elEditMeta.addEventListener("click", async function () {
                document.getElementById("modalPush")
                    .style.display = "none";
                await fnOpenZenodoMetadataModal();
            });
        }
        fnBindConnectionSetupEvents();
    }

    async function fnShowConnectionSetup(sService) {
        var elModal = document.getElementById("modalConnectionSetup");
        elModal.dataset.service = sService;
        var elProjectId = document.getElementById(
            "groupSetupProjectId");
        var elToken = document.getElementById("groupSetupToken");
        var elZenodoInstance = document.getElementById(
            "groupSetupZenodoInstance");
        elProjectId.style.display = "none";
        elToken.style.display = "none";
        if (elZenodoInstance) elZenodoInstance.style.display = "none";
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
        var elInstance = document.getElementById(
            "groupSetupZenodoInstance");
        if (elInstance) {
            elInstance.style.display = "";
            _fnBindZenodoInstanceChange();
            _fnUpdateZenodoTokenLink(_fsReadZenodoInstance());
        }
    }

    function _fsReadZenodoInstance() {
        var elChecked = document.querySelector(
            'input[name="zenodoInstance"]:checked');
        if (elChecked && elChecked.value) return elChecked.value;
        return "sandbox";
    }

    function _fnBindZenodoInstanceChange() {
        var listRadios = document.querySelectorAll(
            'input[name="zenodoInstance"]');
        for (var iRadio = 0; iRadio < listRadios.length; iRadio += 1) {
            listRadios[iRadio].onchange = function () {
                _fnUpdateZenodoTokenLink(_fsReadZenodoInstance());
            };
        }
    }

    function _fnUpdateZenodoTokenLink(sInstance) {
        var elLink = document.getElementById("linkZenodoTokens");
        if (!elLink) return;
        var sHost = (sInstance === "production")
            ? "zenodo.org" : "sandbox.zenodo.org";
        var sUrl = "https://" + sHost +
            "/account/settings/applications/";
        elLink.href = sUrl;
        elLink.textContent = sHost +
            "/account/settings/applications/";
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
        if (sService === "zenodo") {
            dictBody.sZenodoInstance = _fsReadZenodoInstance();
        }
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

    var _DICT_REMOTE_KEY_TO_LABEL = {
        sGithub: "GitHub",
        sOverleaf: "Overleaf",
        sZenodo: "Zenodo",
    };

    async function fnSyncFileToRemote(
        sRemoteKey, sResolved, sWorkdir,
    ) {
        var sContainerId = PipeleyenApp.fsGetContainerId();
        if (!sContainerId || !sResolved) return;
        if (sRemoteKey === "sOverleaf") {
            _fnShowOverleafPerFileNotice();
            return;
        }
        var sCurrentState = _fsCurrentBadgeState(
            sRemoteKey, sResolved, sWorkdir);
        if (sCurrentState === "synced") {
            _fnShowAlreadySyncedToast(sRemoteKey);
            return;
        }
        await _fnRunSyncOnce(
            sContainerId, sRemoteKey, sResolved, sCurrentState);
    }

    async function _fnRunSyncOnce(
        sContainerId, sRemoteKey, sResolved, sCurrentState,
    ) {
        var sKey = sRemoteKey + "|" + sResolved;
        if (_setActiveFileSyncs.has(sKey)) {
            PipeleyenApp.fnShowToast(
                "Sync already in progress for " +
                _DICT_REMOTE_KEY_TO_LABEL[sRemoteKey] + ".",
                "info");
            return;
        }
        _setActiveFileSyncs.add(sKey);
        try {
            await _fnEnsureTrackedThenPush(
                sContainerId, sRemoteKey, sResolved, sCurrentState);
        } finally {
            _setActiveFileSyncs.delete(sKey);
        }
    }

    function _fsCurrentBadgeState(sRemoteKey, sResolved, sWorkdir) {
        var dictTriple = VaibifyGitBadges.fdictGetBadgesForFile(
            sResolved, sWorkdir);
        return dictTriple[sRemoteKey] || "none";
    }

    function _fnShowOverleafPerFileNotice() {
        PipeleyenApp.fnShowToast(
            "Overleaf sync is workflow-level. " +
            "Use Sync › Push to Overleaf.",
            "info",
        );
    }

    function _fnShowAlreadySyncedToast(sRemoteKey) {
        PipeleyenApp.fnShowToast(
            "Already synced with " +
            _DICT_REMOTE_KEY_TO_LABEL[sRemoteKey] + ".",
            "info",
        );
    }

    async function _fnEnsureTrackedThenPush(
        sContainerId, sRemoteKey, sResolved, sCurrentState,
    ) {
        try {
            if (sCurrentState === "none") {
                await _fnPostTrack(
                    sContainerId, sResolved, sRemoteKey, true);
            }
            await _fnPushFileToRemote(
                sContainerId, sRemoteKey, sResolved);
        } catch (error) {
            PipeleyenApp.fnShowToast(
                _fsSanitizeError(error.message), "error");
        }
        await VaibifyGitBadges.fnRefresh(sContainerId);
        PipeleyenApp.fnRenderStepList();
    }

    function _fnPostTrack(
        sContainerId, sResolved, sRemoteKey, bTrack,
    ) {
        return VaibifyApi.fdictPost(
            "/api/sync/" + sContainerId + "/track",
            {
                sPath: sResolved,
                sService: _DICT_REMOTE_KEY_TO_SERVICE[sRemoteKey],
                bTrack: bTrack,
            }
        );
    }

    async function _fnPushFileToRemote(
        sContainerId, sRemoteKey, sResolved,
    ) {
        var sLabel = _DICT_REMOTE_KEY_TO_LABEL[sRemoteKey];
        PipeleyenApp.fnShowToast(
            "Syncing to " + sLabel + "…", "info");
        if (sRemoteKey === "sGithub") {
            var dictGh = await VaibifyApi.fdictPost(
                "/api/github/" + sContainerId + "/add-file",
                {sFilePath: sResolved}
            );
            _fnHandlePushResult(dictGh, "GitHub");
            return;
        }
        if (sRemoteKey === "sZenodo") {
            var dictZen = await VaibifyApi.fdictPost(
                "/api/zenodo/" + sContainerId + "/archive",
                {listFilePaths: [sResolved]}
            );
            _fnHandlePushResult(dictZen, "Zenodo");
        }
    }

    function _fnHandlePushResult(dictResult, sServiceLabel) {
        if (dictResult && dictResult.bSuccess) {
            PipeleyenApp.fnShowToast(
                "Synced to " + sServiceLabel + ".", "success");
            return;
        }
        fnShowSyncError(dictResult || {}, sServiceLabel);
    }

    function _fnToggleEditZenodoMetadataButton(sService) {
        var elButton = document.getElementById(
            "btnPushEditZenodoMetadata");
        if (!elButton) return;
        elButton.style.display = sService === "zenodo" ? "" : "none";
    }

    async function fnOpenZenodoMetadataModal() {
        var sContainerId = PipeleyenApp.fsGetContainerId();
        if (!sContainerId) return false;
        var dictMeta;
        try {
            dictMeta = await VaibifyApi.fdictGet(
                "/api/zenodo/" + sContainerId + "/metadata"
            );
        } catch (error) {
            PipeleyenApp.fnShowToast(
                "Failed to load metadata: " +
                _fsSanitizeError(error.message), "error");
            return false;
        }
        return await _fbRunZenodoMetadataModal(
            sContainerId, dictMeta || {});
    }

    function _fbRunZenodoMetadataModal(sContainerId, dictMeta) {
        return new Promise(function (fnResolve) {
            _fnPopulateZenodoMetadataForm(dictMeta);
            var elModal = document.getElementById(
                "modalZenodoMetadata");
            elModal.style.display = "flex";
            _fnBindZenodoMetadataButtons(
                sContainerId, elModal, fnResolve);
        });
    }

    function _fnPopulateZenodoMetadataForm(dictMeta) {
        document.getElementById("zmTitle").value =
            dictMeta.sTitle || "";
        document.getElementById("zmDescription").value =
            dictMeta.sDescription || "";
        document.getElementById("zmKeywords").value =
            (dictMeta.listKeywords || []).join(", ");
        document.getElementById("zmRelatedUrl").value =
            dictMeta.sRelatedGithubUrl || "";
        _fnPopulateZmLicense(
            dictMeta.sLicense || "CC-BY-4.0");
        _fnRenderZmCreators(
            dictMeta.listCreators || [],
            dictMeta.sDefaultCreatorName || "");
        var elError = document.getElementById("zmError");
        elError.style.display = "none";
        elError.textContent = "";
    }

    var _LIST_ZM_LICENSE_CHOICES = [
        "CC-BY-4.0", "CC0-1.0", "MIT", "Apache-2.0",
        "GPL-3.0-or-later", "BSD-3-Clause",
    ];

    function _fnPopulateZmLicense(sLicense) {
        var elSelect = document.getElementById("zmLicense");
        var elCustom = document.getElementById("zmLicenseCustom");
        if (_LIST_ZM_LICENSE_CHOICES.indexOf(sLicense) >= 0) {
            elSelect.value = sLicense;
            elCustom.style.display = "none";
            elCustom.value = "";
        } else {
            elSelect.value = "__custom__";
            elCustom.style.display = "";
            elCustom.value = sLicense;
        }
        elSelect.onchange = function () {
            elCustom.style.display =
                elSelect.value === "__custom__" ? "" : "none";
        };
    }

    function _fnRenderZmCreators(listCreators, sDefaultName) {
        var elList = document.getElementById("zmCreators");
        elList.innerHTML = "";
        var listToRender = listCreators && listCreators.length
            ? listCreators
            : [{sName: sDefaultName, sAffiliation: "", sOrcid: ""}];
        listToRender.forEach(function (dictCreator) {
            elList.appendChild(_fnBuildZmCreatorRow(dictCreator));
        });
    }

    function _fnBuildZmCreatorRow(dictCreator) {
        var elRow = document.createElement("div");
        elRow.className = "zm-creator-row";
        elRow.innerHTML =
            '<input type="text" class="zm-creator-name" ' +
            'placeholder="Name (required)" value="' +
            VaibifyUtilities.fnEscapeHtml(
                dictCreator.sName || "") + '">' +
            '<input type="text" class="zm-creator-affiliation" ' +
            'placeholder="Affiliation" value="' +
            VaibifyUtilities.fnEscapeHtml(
                dictCreator.sAffiliation || "") + '">' +
            '<input type="text" class="zm-creator-orcid" ' +
            'placeholder="ORCID" value="' +
            VaibifyUtilities.fnEscapeHtml(
                dictCreator.sOrcid || "") + '">' +
            '<button type="button" class="zm-creator-remove" ' +
            'title="Remove creator">&times;</button>';
        elRow.querySelector(".zm-creator-remove")
            .addEventListener("click", function () {
                var elList = document.getElementById("zmCreators");
                if (elList.children.length > 1) elRow.remove();
            });
        return elRow;
    }

    function _fnBindZenodoMetadataButtons(
        sContainerId, elModal, fnResolve,
    ) {
        document.getElementById("btnZmAddCreator").onclick =
            function () {
                document.getElementById("zmCreators").appendChild(
                    _fnBuildZmCreatorRow({}));
            };
        document.getElementById("btnZmCancel").onclick =
            function () {
                elModal.style.display = "none";
                fnResolve(false);
            };
        document.getElementById("btnZmSave").onclick =
            async function () {
                var bSaved = await _fbSaveZenodoMetadata(
                    sContainerId, elModal);
                if (bSaved) fnResolve(true);
            };
    }

    async function _fbSaveZenodoMetadata(sContainerId, elModal) {
        var dictBody = _fdictCollectZmFormValues();
        var elError = document.getElementById("zmError");
        try {
            await VaibifyApi.fdictPost(
                "/api/zenodo/" + sContainerId + "/metadata",
                dictBody);
        } catch (error) {
            elError.textContent = _fsSanitizeError(error.message);
            elError.style.display = "";
            return false;
        }
        elModal.style.display = "none";
        PipeleyenApp.fnShowToast(
            "Zenodo metadata saved.", "success");
        return true;
    }

    function _fdictCollectZmFormValues() {
        var sLicenseSelect = document.getElementById(
            "zmLicense").value;
        var sLicense = sLicenseSelect === "__custom__"
            ? document.getElementById("zmLicenseCustom").value.trim()
            : sLicenseSelect;
        var sKeywordsRaw = document.getElementById(
            "zmKeywords").value.trim();
        var listKeywords = sKeywordsRaw
            ? sKeywordsRaw.split(",")
                .map(function (s) { return s.trim(); })
                .filter(function (s) { return !!s; })
            : [];
        return {
            sTitle: document.getElementById("zmTitle").value.trim(),
            sDescription: document.getElementById(
                "zmDescription").value.trim(),
            listCreators: _flistCollectZmCreators(),
            sLicense: sLicense,
            listKeywords: listKeywords,
            sRelatedGithubUrl: document.getElementById(
                "zmRelatedUrl").value.trim(),
        };
    }

    function _flistCollectZmCreators() {
        var listRows = document.querySelectorAll(
            "#zmCreators .zm-creator-row");
        var listOut = [];
        for (var iRow = 0; iRow < listRows.length; iRow += 1) {
            var el = listRows[iRow];
            var sName = el.querySelector(
                ".zm-creator-name").value.trim();
            if (!sName) continue;
            listOut.push({
                sName: sName,
                sAffiliation: el.querySelector(
                    ".zm-creator-affiliation").value.trim(),
                sOrcid: el.querySelector(
                    ".zm-creator-orcid").value.trim(),
            });
        }
        return listOut;
    }

    async function _fbEnsureZenodoMetadataBeforePush(sContainerId) {
        try {
            var dictMeta = await VaibifyApi.fdictGet(
                "/api/zenodo/" + sContainerId + "/metadata");
        } catch (error) {
            return true;
        }
        if (_fbZenodoMetadataComplete(dictMeta)) return true;
        PipeleyenApp.fnShowToast(
            "Zenodo needs a title and at least one creator. " +
            "Fill in the metadata form first.", "warning");
        var bSaved = await _fbRunZenodoMetadataModal(
            sContainerId, dictMeta || {});
        return bSaved;
    }

    function _fbZenodoMetadataComplete(dictMeta) {
        if (!dictMeta) return false;
        if (!(dictMeta.sTitle || "").trim()) return false;
        var listCreators = dictMeta.listCreators || [];
        for (var iC = 0; iC < listCreators.length; iC += 1) {
            if ((listCreators[iC].sName || "").trim()) return true;
        }
        return false;
    }

    function _fsHumanizeAge(sIsoTimestamp) {
        if (!sIsoTimestamp) return "never verified";
        var dWhen = Date.parse(sIsoTimestamp);
        if (isNaN(dWhen)) return "never verified";
        var iMs = Date.now() - dWhen;
        if (iMs < 0) iMs = 0;
        var iSec = Math.floor(iMs / 1000);
        if (iSec < 60) return "verified just now";
        if (iSec < 3600) {
            return "verified " + Math.floor(iSec / 60) + "m ago";
        }
        if (iSec < 86400) {
            return "verified " + Math.floor(iSec / 3600) + "h ago";
        }
        return "verified " + Math.floor(iSec / 86400) + "d ago";
    }

    function _fsClassifyVerifyState(dictStatus) {
        if (!dictStatus || !dictStatus.sLastVerified) return "unknown";
        var iTotal = dictStatus.iTotalFiles || 0;
        var listDiverged = dictStatus.listDiverged || [];
        if (listDiverged.length > 0) return "diverged";
        var iMatching = dictStatus.iMatching || 0;
        if (iTotal > 0 && iMatching === iTotal) {
            return _fbVerifyIsStale(dictStatus.sLastVerified)
                ? "stale" : "synced";
        }
        return "unknown";
    }

    function _fbVerifyIsStale(sIsoTimestamp) {
        var dWhen = Date.parse(sIsoTimestamp);
        if (isNaN(dWhen)) return true;
        return (Date.now() - dWhen) > _I_VERIFY_STALE_AGE_MS;
    }

    function _fsRenderStatusPill(sState) {
        if (sState === "synced") {
            return '<span class="sync-status-pill sync-status-green" ' +
                'title="all files match">\u25CF</span>';
        }
        if (sState === "diverged") {
            return '<span class="sync-status-pill sync-status-red" ' +
                'title="files have drifted">\u25CF</span>';
        }
        return '<span class="sync-status-pill sync-status-yellow" ' +
            'title="never verified or stale">\u25D0</span>';
    }

    function _fiCoerceCount(iValue) {
        var iNumber = parseInt(iValue, 10);
        return isNaN(iNumber) || iNumber < 0 ? 0 : iNumber;
    }

    function _fsRenderSummaryText(dictStatus) {
        if (!dictStatus || !dictStatus.sLastVerified) {
            return "Never verified \u2014 click Re-verify";
        }
        var iTotal = _fiCoerceCount(dictStatus.iTotalFiles);
        var iMatching = _fiCoerceCount(dictStatus.iMatching);
        var listDiverged = Array.isArray(dictStatus.listDiverged)
            ? dictStatus.listDiverged : [];
        if (listDiverged.length === 0 && iTotal > 0 &&
            iMatching === iTotal) {
            return iTotal + "/" + iTotal + " files match SHA-256";
        }
        if (listDiverged.length > 0) {
            return iMatching + "/" + iTotal + " files match \u00b7 " +
                listDiverged.length + " drifted (" +
                _fsFormatDivergedSample(listDiverged) + ")";
        }
        return iMatching + "/" + iTotal + " files match";
    }

    function _fsFormatDivergedSample(listDiverged) {
        if (!listDiverged || listDiverged.length === 0) return "";
        var sFirst = listDiverged[0];
        if (typeof sFirst === "object" && sFirst !== null) {
            sFirst = sFirst.sPath || sFirst.sName || "";
        }
        var sEscaped = VaibifyUtilities.fnEscapeHtml(sFirst || "");
        if (listDiverged.length === 1) return sEscaped;
        return sEscaped + " +" + (listDiverged.length - 1) + " more";
    }

    function _fsRenderRemoteBusyMarkup(sService) {
        if (!_setActiveVerifyServices.has(sService)) return "";
        return '<span class="spinner sync-verify-spinner"></span>';
    }

    function _fsRenderRemoteAgeMarkup(dictStatus) {
        var sAge = _fsHumanizeAge(dictStatus && dictStatus.sLastVerified);
        return '<span class="sync-remote-age">' +
            VaibifyUtilities.fnEscapeHtml(sAge) + '</span>';
    }

    function _fsRenderRemoteReverifyButton(sService) {
        var bDisabled = _setActiveVerifyServices.has(sService);
        return '<button type="button" class="btn btn-small ' +
            'sync-reverify-btn" data-service="' + sService + '"' +
            (bDisabled ? " disabled" : "") +
            '>Re-verify</button>';
    }

    function _fsRenderRemoteRowHtml(sService, dictStatus) {
        var sState = _fsClassifyVerifyState(dictStatus);
        var sLabel = _DICT_VERIFY_SERVICE_LABELS[sService] || sService;
        return '<div class="sync-remote-row" data-service="' +
            sService + '">' +
            '<span class="sync-remote-label">' + sLabel + '</span>' +
            _fsRenderStatusPill(sState) +
            '<span class="sync-remote-summary">' +
            _fsRenderSummaryText(dictStatus) + '</span>' +
            _fsRenderRemoteAgeMarkup(dictStatus) +
            _fsRenderRemoteBusyMarkup(sService) +
            _fsRenderRemoteReverifyButton(sService) +
            '</div>';
    }

    async function _fdictFetchVerifyStatus(sContainerId, sService) {
        try {
            var dictResult = await VaibifyApi.fdictGet(
                "/api/sync/" + encodeURIComponent(sContainerId) + "/" +
                encodeURIComponent(sService) + "/status"
            );
            return dictResult || {};
        } catch (error) {
            return {};
        }
    }

    async function _fnLoadAllVerifyStatus(sContainerId) {
        var listPromises = _LIST_VERIFY_SERVICES.map(
            function (sService) {
                return _fdictFetchVerifyStatus(
                    sContainerId, sService).then(
                    function (dictStatus) {
                        _dictVerifyStatusCache[sService] =
                            dictStatus || {};
                    }
                );
            });
        await Promise.all(listPromises);
    }

    function _fnRedrawRemoteSyncPanel(sContainerId, elContainer) {
        if (!elContainer) return;
        var sHtml = '<div class="sync-remote-panel">' +
            _LIST_VERIFY_SERVICES.map(function (sService) {
                return _fsRenderRemoteRowHtml(
                    sService, _dictVerifyStatusCache[sService]);
            }).join("") + '</div>';
        elContainer.innerHTML = sHtml;
        _fnBindReverifyButtons(sContainerId, elContainer);
    }

    function _fnBindReverifyButtons(sContainerId, elContainer) {
        var listButtons = elContainer.querySelectorAll(
            ".sync-reverify-btn");
        listButtons.forEach(function (elBtn) {
            elBtn.addEventListener("click", function () {
                var sService = elBtn.dataset.service;
                if (!sService) return;
                _fnScheduleReverify(sContainerId, sService, elContainer);
            });
        });
    }

    function _fnScheduleReverify(sContainerId, sService, elContainer) {
        if (_dictTimerByService[sService]) {
            clearTimeout(_dictTimerByService[sService]);
        }
        _dictTimerByService[sService] = setTimeout(function () {
            _dictTimerByService[sService] = null;
            _fnTriggerReverify(sContainerId, sService, elContainer);
        }, _I_DIFF_DEBOUNCE_MS);
    }

    async function _fdictPostVerify(sContainerId, sService) {
        return await VaibifyApi.fdictPost(
            "/api/sync/" + encodeURIComponent(sContainerId) + "/" +
            encodeURIComponent(sService) + "/verify", {}
        );
    }

    function _fnReportVerifyError(error) {
        PipeleyenApp.fnShowToast(
            _DICT_SYNC_ERROR_MESSAGES.verifyFailed + " (" +
            _fsSanitizeError(error.message) + ")",
            "error");
    }

    async function _fnTriggerReverify(
        sContainerId, sService, elContainer,
    ) {
        if (_setActiveVerifyServices.has(sService)) return;
        _setActiveVerifyServices.add(sService);
        _fnRedrawRemoteSyncPanel(sContainerId, elContainer);
        try {
            var dictResult = await _fdictPostVerify(sContainerId, sService);
            _dictVerifyStatusCache[sService] = dictResult || {};
        } catch (error) {
            _fnReportVerifyError(error);
        } finally {
            _setActiveVerifyServices.delete(sService);
            _fnRedrawRemoteSyncPanel(sContainerId, elContainer);
        }
    }

    async function fnRenderRemoteSyncPanel(sContainerId, elContainer) {
        if (!sContainerId || !elContainer) return;
        _fnInvalidateCacheIfWorkflowChanged(sContainerId);
        if (!_fbCacheHasAnyEntry()) {
            elContainer.innerHTML =
                '<div class="sync-remote-panel-loading">' +
                'Loading sync status\u2026</div>';
        }
        await _fnLoadAllVerifyStatus(sContainerId);
        _fnRedrawRemoteSyncPanel(sContainerId, elContainer);
    }

    function _fdictTallyDriftedRemotes() {
        var dictTally = { iDrifted: 0, iDivergedRemotes: 0,
            iConfigured: 0, bAnyVerified: false };
        _LIST_VERIFY_SERVICES.forEach(function (sService) {
            var dictStatus = _dictVerifyStatusCache[sService];
            if (!dictStatus || !dictStatus.sLastVerified) return;
            dictTally.iConfigured += 1;
            dictTally.bAnyVerified = true;
            var listDiverged = dictStatus.listDiverged || [];
            if (listDiverged.length > 0) {
                dictTally.iDivergedRemotes += 1;
                dictTally.iDrifted += listDiverged.length;
            }
        });
        return dictTally;
    }

    function _fsFormatDriftedBanner(dictTally) {
        return "Remote consistency: \u26A0 " + dictTally.iDrifted +
            " " + (dictTally.iDrifted === 1 ? "file" : "files") +
            " drifted across " + dictTally.iDivergedRemotes +
            " of " + dictTally.iConfigured +
            " " + (dictTally.iConfigured === 1 ? "remote" : "remotes");
    }

    function _fsAggregateBannerText() {
        var dictTally = _fdictTallyDriftedRemotes();
        if (!dictTally.bAnyVerified) {
            return "Remote consistency: not yet verified";
        }
        if (dictTally.iDrifted === 0) {
            return "Remote consistency: \u2713 all " +
                dictTally.iConfigured +
                " configured " +
                (dictTally.iConfigured === 1 ? "remote" : "remotes") +
                " in sync";
        }
        return _fsFormatDriftedBanner(dictTally);
    }

    async function fnRenderRemoteConsistencyBanner(
        sContainerId, elContainer,
    ) {
        if (!sContainerId || !elContainer) return;
        _fnInvalidateCacheIfWorkflowChanged(sContainerId);
        if (!_fbCacheHasAnyEntry()) {
            await _fnLoadAllVerifyStatus(sContainerId);
        }
        var sText = _fsAggregateBannerText();
        elContainer.innerHTML =
            '<div class="sync-consistency-banner">' +
            VaibifyUtilities.fnEscapeHtml(sText) +
            '</div>';
    }

    function _fnInvalidateCacheIfWorkflowChanged(sContainerId) {
        if (sContainerId === _sCacheWorkflowId) return;
        _sCacheWorkflowId = sContainerId;
        fnInvalidateVerifyCache();
    }

    function fnInvalidateVerifyCache() {
        Object.keys(_dictVerifyStatusCache).forEach(function (sKey) {
            delete _dictVerifyStatusCache[sKey];
        });
        _setActiveVerifyServices.clear();
        Object.keys(_dictTimerByService).forEach(function (sKey) {
            if (_dictTimerByService[sKey]) {
                clearTimeout(_dictTimerByService[sKey]);
            }
            delete _dictTimerByService[sKey];
        });
    }

    function _fbCacheHasAnyEntry() {
        for (var iIdx = 0; iIdx < _LIST_VERIFY_SERVICES.length;
            iIdx += 1) {
            if (_dictVerifyStatusCache[_LIST_VERIFY_SERVICES[iIdx]]) {
                return true;
            }
        }
        return false;
    }

    async function fdictVerifyManifest(sContainerId) {
        if (!sContainerId) return null;
        try {
            var dictResult = await VaibifyApi.fdictPost(
                "/api/workflow/" + encodeURIComponent(sContainerId) +
                "/manifest/verify", {}
            );
            _fnShowManifestVerifyToast(dictResult);
            return dictResult;
        } catch (error) {
            PipeleyenApp.fnShowToast(
                _DICT_SYNC_ERROR_MESSAGES.verifyFailed + " (" +
                _fsSanitizeError(error.message) + ")",
                "error");
            return null;
        }
    }

    function _fnShowManifestVerifyToast(dictResult) {
        var dict = dictResult || {};
        var iTotal = dict.iTotal || 0;
        var iMatching = dict.iMatching || 0;
        var listMismatches = dict.listMismatches || [];
        var saIncomplete = dict.saIncomplete || [];
        var iIncomplete = saIncomplete.length;
        if (listMismatches.length === 0 && iIncomplete === 0) {
            PipeleyenApp.fnShowToast(
                "Manifest verified: " + iMatching + "/" + iTotal +
                " files match.", "success");
            return;
        }
        if (listMismatches.length === 0) {
            PipeleyenApp.fnShowToast(
                "Manifest verified " + iMatching + "/" + iTotal +
                ", but " + iIncomplete +
                " declared path(s) not pinned: " +
                _fsBuildMismatchSummary(saIncomplete) +
                " Re-run to refresh coverage.",
                "warning");
            return;
        }
        var sList = _fsBuildMismatchSummary(listMismatches);
        var sExtra = iIncomplete > 0
            ? " Plus " + iIncomplete + " not pinned."
            : "";
        PipeleyenApp.fnShowToast(
            "Manifest mismatch: " + iMatching + "/" + iTotal +
            " match. " + sList + sExtra, "error");
    }

    function _fsBuildMismatchSummary(listMismatches) {
        var iMaxShown = 5;
        var listSample = listMismatches.slice(0, iMaxShown).map(
            function (item) {
                if (item && typeof item === "object") {
                    return item.sPath || item.sName || "(unknown)";
                }
                return String(item);
            });
        var sJoined = listSample.join(", ");
        if (listMismatches.length > iMaxShown) {
            sJoined += " \u2026and " +
                (listMismatches.length - iMaxShown) + " more";
        }
        return sJoined;
    }

    function fnResetState() {
        _setActiveFileSyncs.clear();
        fnInvalidateVerifyCache();
    }

    return {
        fnOpenPushModal: fnOpenPushModal,
        fnOpenZenodoMetadataModal: fnOpenZenodoMetadataModal,
        fnBindPushModalEvents: fnBindPushModalEvents,
        fnShowSyncError: fnShowSyncError,
        fnShowHelpPopup: fnShowHelpPopup,
        fnSyncFileToRemote: fnSyncFileToRemote,
        fsFormatFileCount: fsFormatFileCount,
        fnRenderRemoteSyncPanel: fnRenderRemoteSyncPanel,
        fnRenderRemoteConsistencyBanner: fnRenderRemoteConsistencyBanner,
        fdictVerifyManifest: fdictVerifyManifest,
        fnInvalidateVerifyCache: fnInvalidateVerifyCache,
        fnResetState: fnResetState,
    };
})();
