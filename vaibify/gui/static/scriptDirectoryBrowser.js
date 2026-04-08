/* Vaibify — Host directory browser (extracted from scriptApplication.js) */

var PipeleyenDirectoryBrowser = (function () {
    "use strict";

    var _sBrowserCurrentPath = "";
    var _listBrowserHistory = [];
    var _iBrowserHistoryIndex = -1;
    var _bBrowserNavigating = false;
    var I_MAX_BROWSER_HISTORY = 100;
    var _bDirectoryEntryDelegationBound = false;

    async function fnOpenDirectoryBrowser() {
        document.getElementById("modalAddContainer").style.display = "flex";
        _listBrowserHistory = [];
        _iBrowserHistoryIndex = -1;
        await fnBrowseDirectory("");
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
        var sHtml = "";
        var sBuiltPath = "";
        for (var i = 0; i < listSegments.length; i++) {
            sBuiltPath += "/" + listSegments[i];
            var sNavTarget = (i === 0) ? "/" : sBuiltPath.substring(
                0, sBuiltPath.lastIndexOf("/"));
            sHtml +=
                '<span class="breadcrumb-sep" data-path="' +
                VaibifyUtilities.fnEscapeHtml(sNavTarget) + '">/</span>' +
                '<span class="breadcrumb-segment" data-path="' +
                VaibifyUtilities.fnEscapeHtml(sBuiltPath) + '">' +
                VaibifyUtilities.fnEscapeHtml(listSegments[i]) + "</span>";
        }
        if (listSegments.length === 0) {
            sHtml = '<span class="breadcrumb-segment" data-path="/">/</span>';
        }
        elBar.innerHTML = sHtml;
        if (!elBar._bDelegated) {
            elBar._bDelegated = true;
            elBar.addEventListener("click", function (event) {
                var elSegment = event.target.closest(
                    "[data-path]");
                if (elSegment) {
                    fnBrowseDirectory(elSegment.dataset.path);
                }
            });
        }
    }

    function fnRenderDirectoryEntries(listEntries) {
        var elContainer = document.getElementById("directoryEntries");
        if (listEntries.length === 0) {
            elContainer.innerHTML =
                '<p class="muted-text" style="text-align:center;">' +
                "No subdirectories</p>";
            return;
        }
        elContainer.innerHTML = listEntries.map(function (entry) {
            var sConfigClass = entry.bHasConfig ? " has-config" : "";
            return (
                '<div class="directory-entry' + sConfigClass +
                '" data-path="' + VaibifyUtilities.fnEscapeHtml(entry.sPath) + '">' +
                '<span class="directory-entry-icon">&#128193;</span>' +
                '<span class="directory-entry-name">' +
                VaibifyUtilities.fnEscapeHtml(entry.sName) + "</span>" +
                (entry.bHasConfig
                    ? '<img src="/static/favicon.png" class="config-indicator" alt="vaibify">'
                    : "") +
                "</div>"
            );
        }).join("");
        _fnBindDirectoryEntryDelegation(elContainer);
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
        elLabel.style.display = bHasConfig ? "" : "none";
        elButton.disabled = !bHasConfig;
    }

    async function fnSelectDirectory() {
        if (!_sBrowserCurrentPath) return;
        try {
            await VaibifyApi.fdictPost("/api/registry", {
                sDirectory: _sBrowserCurrentPath,
            });
            PipeleyenApp.fnShowToast("Container added", "success");
            document.getElementById("modalAddContainer").style.display = "none";
        } catch (error) {
            PipeleyenApp.fnShowToast(
                VaibifyUtilities.fsSanitizeErrorForUser(error.message), "error");
        }
        PipeleyenContainerManager.fnLoadContainers();
    }

    return {
        fnOpenDirectoryBrowser: fnOpenDirectoryBrowser,
        fnBrowserNavigateBack: fnBrowserNavigateBack,
        fnBrowserNavigateForward: fnBrowserNavigateForward,
        fnSelectDirectory: fnSelectDirectory,
    };
})();
