/* Vaibify — Overleaf mirror: tree picker, diff modal, freshness display */

var VaibifyOverleafMirror = (function () {
    "use strict";

    var _dictState = {
        sCurrentContainerId: "",
        sMirrorHeadSha: "",
        sRefreshedAt: "",
        listTreeEntries: [],
        dictTreeIndex: {},
        setExpandedDirs: new Set(),
        sSelectedTargetDir: "",
    };

    /* --- Tree synthesis: blobs → directory index --- */

    function _fdictBuildCanonicalCaseMap(listBlobs) {
        /* Map lowercased directory path -> canonical (first-seen) form,
           so Overleaf's case-variant duplicates (Figures/ vs figures/)
           collapse to a single entry in the picker. */
        var dictCanonical = {};
        listBlobs.forEach(function (dictEntry) {
            if (dictEntry.sType !== "blob") return;
            var listParts = dictEntry.sPath.split("/");
            listParts.pop();
            var sAccumulated = "";
            listParts.forEach(function (sPart) {
                sAccumulated = sAccumulated
                    ? sAccumulated + "/" + sPart
                    : sPart;
                var sLower = sAccumulated.toLowerCase();
                if (!(sLower in dictCanonical)) {
                    dictCanonical[sLower] = sAccumulated;
                }
            });
        });
        return dictCanonical;
    }

    function _fnRecordDirectoryChain(
        sPath, dictIndex, setDirs, dictCanonical
    ) {
        var listParts = sPath.split("/");
        listParts.pop();
        var sAccumulated = "";
        listParts.forEach(function (sPart) {
            var sParent = sAccumulated;
            sAccumulated = sAccumulated
                ? sAccumulated + "/" + sPart
                : sPart;
            sAccumulated = dictCanonical[sAccumulated.toLowerCase()]
                || sAccumulated;
            setDirs.add(sAccumulated);
            if (!dictIndex[sParent]) dictIndex[sParent] = [];
            if (dictIndex[sParent].indexOf(sAccumulated) === -1) {
                dictIndex[sParent].push(sAccumulated);
            }
        });
    }

    function _fdictBuildTreeIndex(listBlobs) {
        var dictIndex = {};
        var setDirs = new Set();
        var dictCanonical = _fdictBuildCanonicalCaseMap(listBlobs);
        listBlobs.forEach(function (dictEntry) {
            if (dictEntry.sType !== "blob") return;
            _fnRecordDirectoryChain(
                dictEntry.sPath, dictIndex, setDirs, dictCanonical);
        });
        Object.keys(dictIndex).forEach(function (sKey) {
            dictIndex[sKey].sort();
        });
        return dictIndex;
    }

    function _flistGetChildrenOfDir(sDirPath) {
        return _dictState.dictTreeIndex[sDirPath] || [];
    }

    /* --- API wrappers --- */

    async function fnRefreshMirrorFromServer(sContainerId) {
        var sUrl = "/api/overleaf/" + encodeURIComponent(sContainerId)
            + "/mirror/refresh";
        var dictResult = await VaibifyApi.fdictPost(sUrl, {});
        if (dictResult && dictResult.bSuccess) {
            _dictState.sMirrorHeadSha = dictResult.sHeadSha || "";
            _dictState.sRefreshedAt = dictResult.sRefreshedAt || "";
        }
        return dictResult || {bSuccess: false};
    }

    async function fnFetchMirrorTree(sContainerId) {
        var sUrl = "/api/overleaf/" + encodeURIComponent(sContainerId)
            + "/mirror/tree";
        var dictResult = await VaibifyApi.fdictGet(sUrl);
        _dictState.listTreeEntries = dictResult.listEntries || [];
        _dictState.dictTreeIndex = _fdictBuildTreeIndex(
            _dictState.listTreeEntries);
        _dictState.sMirrorHeadSha = dictResult.sHeadSha || "";
        if (dictResult.sRefreshedAt) {
            _dictState.sRefreshedAt = dictResult.sRefreshedAt;
        }
        return dictResult;
    }

    async function fdictFetchDiffFromServer(
        sContainerId, listFilePaths, sTargetDir
    ) {
        var sUrl = "/api/overleaf/" + encodeURIComponent(sContainerId)
            + "/diff";
        var dictBody = {
            listFilePaths: listFilePaths,
            sTargetDirectory: sTargetDir || "",
        };
        return await VaibifyApi.fdictPost(sUrl, dictBody);
    }

    async function fnForgetContainer(sContainerId) {
        if (!sContainerId) return {bSuccess: true};
        try {
            var sUrl = "/api/overleaf/"
                + encodeURIComponent(sContainerId) + "/mirror";
            return await VaibifyApi.fnDelete(sUrl);
        } catch (error) {
            return {bSuccess: false};
        }
    }

    /* --- Freshness indicator --- */

    function _fsFormatRelativeTime(sIsoTimestamp) {
        if (!sIsoTimestamp) return "never";
        var dThen = Date.parse(sIsoTimestamp);
        if (isNaN(dThen)) return "unknown";
        var iDelta = Math.max(0, Date.now() - dThen);
        var iSeconds = Math.round(iDelta / 1000);
        if (iSeconds < 60) return "just now";
        var iMinutes = Math.round(iSeconds / 60);
        if (iMinutes < 60) return iMinutes + "m ago";
        var iHours = Math.round(iMinutes / 60);
        if (iHours < 24) return iHours + "h ago";
        var iDays = Math.round(iHours / 24);
        return iDays + "d ago";
    }

    function _fsBuildFreshnessHtml(sRefreshedAt) {
        var sRelative = _fsFormatRelativeTime(sRefreshedAt);
        return '<span class="freshness-label">'
            + 'Mirror last refreshed: '
            + '<span class="freshness-time">'
            + VaibifyUtilities.fnEscapeHtml(sRelative) + '</span>'
            + '</span>'
            + '<button type="button" class="freshness-refresh-button" '
            + 'id="btnFreshnessRefresh">Refresh</button>';
    }

    function _fsDescribeRefreshError(error) {
        var sMessage = error && error.message ? error.message : "";
        if (sMessage.indexOf("Overleaf project ID not set") !== -1) {
            return "Connect Overleaf first (Sync \u2192 Connect) " +
                "to set the project ID before refreshing.";
        }
        return sMessage || "Mirror refresh failed";
    }

    async function _fnHandleFreshnessRefreshClick(elHost, sContainerId) {
        var elButton = elHost.querySelector(".freshness-refresh-button");
        if (elButton) {
            elButton.disabled = true;
            elButton.textContent = "Refreshing...";
        }
        try {
            var dictResult = await fnRefreshMirrorFromServer(sContainerId);
            if (!dictResult.bSuccess) {
                PipeleyenApp.fnShowToast(
                    dictResult.sMessage || "Mirror refresh failed",
                    "error");
            }
        } catch (error) {
            PipeleyenApp.fnShowToast(
                _fsDescribeRefreshError(error), "error");
        }
        fnRenderFreshnessIndicator(elHost, sContainerId);
    }

    function fnRenderFreshnessIndicator(elHost, sContainerId) {
        if (!elHost) return;
        _dictState.sCurrentContainerId = sContainerId || "";
        elHost.innerHTML = _fsBuildFreshnessHtml(
            _dictState.sRefreshedAt);
        var elButton = elHost.querySelector(
            ".freshness-refresh-button");
        if (elButton) {
            elButton.addEventListener("click", function () {
                _fnHandleFreshnessRefreshClick(elHost, sContainerId);
            });
        }
    }

    /* --- Tree picker --- */

    function _fsDescribeTreeLoadError(error) {
        var sMessage = error && error.message ? error.message : "";
        if (sMessage.indexOf("Overleaf project ID not set") !== -1) {
            return "Connect Overleaf first (Sync \u2192 Connect) " +
                "to set the project ID before browsing the tree.";
        }
        if (sMessage) return sMessage;
        return "Unable to load Overleaf tree. " +
            "Click Refresh on the mirror indicator first.";
    }

    async function fnOpenTreePicker(
        sContainerId, sCurrentPath, fnOnSelect
    ) {
        _dictState.sCurrentContainerId = sContainerId;
        _dictState.setExpandedDirs.clear();
        try {
            await fnFetchMirrorTree(sContainerId);
        } catch (error) {
            PipeleyenApp.fnShowToast(
                _fsDescribeTreeLoadError(error), "error");
            return;
        }
        if (!_dictState.sMirrorHeadSha) {
            PipeleyenApp.fnShowToast(
                "Downloading Overleaf project structure\u2026",
                "info");
            try {
                await fnRefreshMirrorFromServer(sContainerId);
                await fnFetchMirrorTree(sContainerId);
            } catch (error) {
                PipeleyenApp.fnShowToast(
                    _fsDescribeRefreshError(error), "error");
                return;
            }
        }
        PipeleyenModals.fnShowTreePicker({
            sTitle: "Select Overleaf target directory",
            listDirectories: _flistGetAllDirectories(),
            dictTreeIndex: _dictState.dictTreeIndex,
            sCurrentPath: sCurrentPath || "",
            setExpandedDirs: _dictState.setExpandedDirs,
            fnOnSelect: function (sPath) {
                _dictState.sSelectedTargetDir = sPath;
                if (fnOnSelect) fnOnSelect(sPath);
            },
        });
    }

    function _flistGetAllDirectories() {
        var listDirs = [];
        Object.keys(_dictState.dictTreeIndex).forEach(function (sKey) {
            if (sKey) listDirs.push(sKey);
        });
        listDirs.sort();
        return listDirs;
    }

    function fsGetRefreshedAt() {
        return _dictState.sRefreshedAt;
    }

    function fnResetState() {
        _dictState.sCurrentContainerId = "";
        _dictState.sMirrorHeadSha = "";
        _dictState.sRefreshedAt = "";
        _dictState.listTreeEntries = [];
        _dictState.dictTreeIndex = {};
        _dictState.setExpandedDirs.clear();
        _dictState.sSelectedTargetDir = "";
    }

    return {
        fnOpenTreePicker: fnOpenTreePicker,
        fdictFetchDiffFromServer: fdictFetchDiffFromServer,
        fnRenderFreshnessIndicator: fnRenderFreshnessIndicator,
        fnForgetContainer: fnForgetContainer,
        fnRefreshMirrorFromServer: fnRefreshMirrorFromServer,
        fnFetchMirrorTree: fnFetchMirrorTree,
        fsGetRefreshedAt: fsGetRefreshedAt,
        fnResetState: fnResetState,
    };
})();
