/* Vaibify — Host directory browser (extracted from scriptApplication.js) */

var PipeleyenDirectoryBrowser = (function () {
    "use strict";

    var _sBrowserCurrentPath = "";
    var _listBrowserHistory = [];
    var _iBrowserHistoryIndex = -1;
    var _bBrowserNavigating = false;
    var I_MAX_BROWSER_HISTORY = 100;
    var _bDirectoryEntryDelegationBound = false;
    var _sBrowserMode = "existing";
    var _fnOnSelectCallback = null;

    async function fnOpenDirectoryBrowser() {
        _sBrowserMode = "existing";
        _fnOnSelectCallback = null;
        _fnHideNewFolderButton();
        _fnSetSubtitleForExisting();
        document.getElementById("modalAddContainer").style.display = "flex";
        _listBrowserHistory = [];
        _iBrowserHistoryIndex = -1;
        await fnBrowseDirectory("");
    }

    async function fnOpenForCreate(fnCallback) {
        _sBrowserMode = "create";
        _fnOnSelectCallback = fnCallback;
        _fnShowNewFolderButton();
        _fnSetSubtitleForCreate();
        _fnRaiseModalZIndex();
        document.getElementById("modalAddContainer").style.display = "flex";
        _listBrowserHistory = [];
        _iBrowserHistoryIndex = -1;
        await fnBrowseDirectory("");
    }

    function _fnShowNewFolderButton() {
        var elButton = document.getElementById("btnDirectoryNewFolder");
        if (elButton) elButton.style.display = "";
    }

    function _fnHideNewFolderButton() {
        var elButton = document.getElementById("btnDirectoryNewFolder");
        if (elButton) elButton.style.display = "none";
    }

    function _fnRaiseModalZIndex() {
        var elModal = document.getElementById("modalAddContainer");
        if (elModal) elModal.style.zIndex = "1100";
    }

    function _fnSetSubtitleForExisting() {
        var elSubtitle = document.getElementById("directoryBrowserSubtitle");
        if (elSubtitle) {
            elSubtitle.innerHTML =
                "Navigate to a directory containing <code>vaibify.yml</code>.";
        }
    }

    function _fnSetSubtitleForCreate() {
        var elSubtitle = document.getElementById("directoryBrowserSubtitle");
        if (elSubtitle) {
            elSubtitle.textContent =
                "Choose a directory for your new project, " +
                "or create a new folder.";
        }
    }

    function fnHandleModalClose() {
        var elModal = document.getElementById("modalAddContainer");
        if (elModal) {
            elModal.style.display = "none";
            elModal.style.zIndex = "";
        }
        _fnHideNewFolderButton();
        _sBrowserMode = "existing";
        _fnOnSelectCallback = null;
    }

    function fnBrowserNavigateBack() {
        if (_iBrowserHistoryIndex <= 0) return;
        _bBrowserNavigating = true;
        _iBrowserHistoryIndex--;
        fnBrowseDirectory(_listBrowserHistory[_iBrowserHistoryIndex]);
    }

    function fnBrowserNavigateForward() {
        if (_iBrowserHistoryIndex >= _listBrowserHistory.length - 1) return;
        _bBrowserNavigating = true;
        _iBrowserHistoryIndex++;
        fnBrowseDirectory(_listBrowserHistory[_iBrowserHistoryIndex]);
    }

    function _fnUpdateBrowserNavButtons() {
        var elBack = document.getElementById("btnBrowserBack");
        var elForward = document.getElementById("btnBrowserForward");
        elBack.disabled = _iBrowserHistoryIndex <= 0;
        elForward.disabled =
            _iBrowserHistoryIndex >= _listBrowserHistory.length - 1;
    }

    function _fnPushBrowserHistory(sCurrentPath) {
        _listBrowserHistory = _listBrowserHistory.slice(
            0, _iBrowserHistoryIndex + 1
        );
        _listBrowserHistory.push(sCurrentPath);
        if (_listBrowserHistory.length > I_MAX_BROWSER_HISTORY) {
            _listBrowserHistory.splice(
                0, _listBrowserHistory.length - I_MAX_BROWSER_HISTORY
            );
        }
        _iBrowserHistoryIndex = _listBrowserHistory.length - 1;
    }

    function _fnApplyBrowseResult(dictResult) {
        _sBrowserCurrentPath = dictResult.sCurrentPath;
        if (!_bBrowserNavigating) {
            _fnPushBrowserHistory(dictResult.sCurrentPath);
        }
        _bBrowserNavigating = false;
        _fnUpdateBrowserNavButtons();
        fnRenderBreadcrumb(dictResult.sCurrentPath);
        fnRenderDirectoryEntries(dictResult.listEntries);
        _fnUpdateSelectButton(
            dictResult.sCurrentPath, dictResult.bHasConfig
        );
    }

    async function fnBrowseDirectory(sPath) {
        var elEntries = document.getElementById("directoryEntries");
        elEntries.innerHTML =
            '<p class="muted-text" style="text-align:center;">Loading...</p>';
        try {
            var sUrl = "/api/host-directories";
            if (sPath) sUrl += "?sPath=" + encodeURIComponent(sPath);
            var dictResult = await VaibifyApi.fdictGet(sUrl);
            _fnApplyBrowseResult(dictResult);
        } catch (error) {
            elEntries.innerHTML =
                '<p style="color:var(--color-red);">Error loading</p>';
        }
    }

    function fnRenderBreadcrumb(sPath) {
        var elBar = document.getElementById("directoryBreadcrumb");
        var listSegments = sPath.split("/").filter(function (s) {
            return s.length > 0;
        });
        elBar.innerHTML = listSegments.length === 0
            ? '<span class="breadcrumb-segment" data-path="/">/</span>'
            : _fsBuildBreadcrumbHtml(listSegments);
        _fnBindBreadcrumbDelegation(elBar);
    }

    function _fsBuildBreadcrumbHtml(listSegments) {
        var sHtml = "";
        var sBuiltPath = "";
        for (var i = 0; i < listSegments.length; i++) {
            sBuiltPath += "/" + listSegments[i];
            var sNavTarget = (i === 0) ? "/" : sBuiltPath.substring(
                0, sBuiltPath.lastIndexOf("/"));
            sHtml += _fsBuildBreadcrumbSegment(
                sNavTarget, sBuiltPath, listSegments[i]);
        }
        return sHtml;
    }

    function _fsBuildBreadcrumbSegment(sNavTarget, sBuiltPath, sLabel) {
        return '<span class="breadcrumb-sep" data-path="' +
            VaibifyUtilities.fnEscapeHtml(sNavTarget) + '">/</span>' +
            '<span class="breadcrumb-segment" data-path="' +
            VaibifyUtilities.fnEscapeHtml(sBuiltPath) + '">' +
            VaibifyUtilities.fnEscapeHtml(sLabel) + "</span>";
    }

    function _fnBindBreadcrumbDelegation(elBar) {
        if (elBar._bDelegated) return;
        elBar._bDelegated = true;
        elBar.addEventListener("click", function (event) {
            var elSegment = event.target.closest("[data-path]");
            if (elSegment) {
                fnBrowseDirectory(elSegment.dataset.path);
            }
        });
    }

    function fnRenderDirectoryEntries(listEntries) {
        var elContainer = document.getElementById("directoryEntries");
        if (listEntries.length === 0) {
            elContainer.innerHTML =
                '<p class="muted-text" style="text-align:center;">' +
                "No subdirectories</p>";
            return;
        }
        elContainer.innerHTML = listEntries.map(_fsBuildEntryHtml).join("");
        _fnBindDirectoryEntryDelegation(elContainer);
    }

    function _fsBuildEntryHtml(entry) {
        var sConfigClass = entry.bHasConfig ? " has-config" : "";
        var sIndicator = entry.bHasConfig
            ? '<img src="/static/favicon.png" class="config-indicator" alt="vaibify">'
            : "";
        return '<div class="directory-entry' + sConfigClass +
            '" data-path="' +
            VaibifyUtilities.fnEscapeHtml(entry.sPath) + '">' +
            '<span class="directory-entry-icon">&#128193;</span>' +
            '<span class="directory-entry-name">' +
            VaibifyUtilities.fnEscapeHtml(entry.sName) + "</span>" +
            sIndicator + "</div>";
    }

    function _fnBindDirectoryEntryDelegation(elContainer) {
        if (_bDirectoryEntryDelegationBound) return;
        _bDirectoryEntryDelegationBound = true;
        elContainer.addEventListener("click", function (event) {
            var elEntry = event.target.closest(".directory-entry");
            if (elEntry) {
                fnBrowseDirectory(elEntry.dataset.path);
            }
        });
    }

    function _fnUpdateSelectButton(sPath, bHasConfig) {
        var elPath = document.getElementById("directoryCurrentPath");
        var elLabel = document.getElementById("configFoundLabel");
        var elButton = document.getElementById("btnAddContainerConfirm");
        elPath.textContent = sPath;
        if (_sBrowserMode === "create") {
            _fnUpdateButtonForCreate(sPath, bHasConfig, elLabel, elButton);
        } else {
            _fnUpdateButtonForExisting(bHasConfig, elLabel, elButton);
        }
    }

    function _fnUpdateButtonForExisting(bHasConfig, elLabel, elButton) {
        elLabel.textContent = "vaibify.yml found";
        elLabel.style.display = bHasConfig ? "" : "none";
        elButton.textContent = "Select";
        elButton.disabled = !bHasConfig;
    }

    function _fnUpdateButtonForCreate(sPath, bHasConfig, elLabel, elButton) {
        elLabel.textContent = bHasConfig
            ? "Warning: vaibify.yml exists here"
            : "";
        elLabel.style.display = bHasConfig ? "" : "none";
        elButton.textContent = "Use This Directory";
        elButton.disabled = !sPath || bHasConfig;
    }

    async function fnSelectDirectory() {
        if (!_sBrowserCurrentPath) return;
        if (_sBrowserMode === "create") {
            _fnSelectForCreate();
            return;
        }
        await _fnSelectForExisting();
    }

    function _fnSelectForCreate() {
        var fnCallback = _fnOnSelectCallback;
        var sChosenPath = _sBrowserCurrentPath;
        fnHandleModalClose();
        if (fnCallback) fnCallback(sChosenPath);
    }

    async function _fnSelectForExisting() {
        try {
            await VaibifyApi.fdictPost("/api/registry", {
                sDirectory: _sBrowserCurrentPath,
            });
            PipeleyenApp.fnShowToast("Container added", "success");
            fnHandleModalClose();
        } catch (error) {
            PipeleyenApp.fnShowToast(
                VaibifyUtilities.fsSanitizeErrorForUser(error.message),
                "error");
        }
        PipeleyenContainerManager.fnLoadContainers();
    }

    function fnPromptCreateFolder() {
        PipeleyenModals.fnShowInputModal(
            "New folder name",
            "",
            function (sFolderName) {
                _fnCreateFolder(sFolderName);
            }
        );
        _fnRaiseInputModalAbovePicker();
    }

    function _fnRaiseInputModalAbovePicker() {
        var elInputModal = document.getElementById("modalInput");
        if (elInputModal) elInputModal.style.zIndex = "1200";
    }

    async function _fnCreateFolder(sFolderName) {
        var sParentPath = _sBrowserCurrentPath;
        try {
            await VaibifyApi.fdictPost(
                "/api/host-directories/create",
                {sParentPath: sParentPath,
                 sFolderName: sFolderName}
            );
            await fnBrowseDirectory(sParentPath);
            PipeleyenApp.fnShowToast(
                "Created folder: " + sFolderName, "success");
        } catch (error) {
            PipeleyenApp.fnShowToast(
                VaibifyUtilities.fsSanitizeErrorForUser(error.message),
                "error");
        }
    }

    return {
        fnOpenDirectoryBrowser: fnOpenDirectoryBrowser,
        fnOpenForCreate: fnOpenForCreate,
        fnBrowserNavigateBack: fnBrowserNavigateBack,
        fnBrowserNavigateForward: fnBrowserNavigateForward,
        fnSelectDirectory: fnSelectDirectory,
        fnPromptCreateFolder: fnPromptCreateFolder,
        fnHandleModalClose: fnHandleModalClose,
    };
})();
