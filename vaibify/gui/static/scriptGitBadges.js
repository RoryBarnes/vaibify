/* Vaibify — per-file per-remote badge state for the Step Viewer.

   Fetches the G/O/Z/A badge row for every tracked file from
   /api/git/{id}/badges and exposes:

   - VaibifyGitBadges.fnRefresh(sContainerId)
       Pulls the latest badge map and repo-level summary; returns a
       promise that resolves once the in-memory state is updated.

   - VaibifyGitBadges.fdictGetBadgesForFile(sResolvedPath, sWorkdir)
       Returns the per-file dict {sGithub, sOverleaf, sZenodo, sArxiv}
       for a resolved output-path. Returns null when the file hasn't
       been seen (caller renders nothing).

   - VaibifyGitBadges.fsRenderBadgeRow(dictBadges, aRemoteKeys)
       Builds a fragment of mini-badge spans for one file row. The
       optional aRemoteKeys array restricts which remotes render
       (default: ["sGithub", "sOverleaf", "sZenodo", "sArxiv"]).
       Callers pass a category-specific subset to hide remotes that
       don't apply (e.g., Overleaf for non-LaTeX files). Pure; safe
       to call on every re-render.
*/

var VaibifyGitBadges = (function () {
    "use strict";

    var _dictState = {
        sCurrentContainerId: "",
        dictBadges: {},
        dictRepoSummary: {
            bIsRepo: false,
            sBranch: "",
            sHeadSha: "",
            iAhead: 0,
            iBehind: 0,
            sRefreshedAt: "",
            sRemoteUrl: "",
        },
    };

    var _DICT_BADGE_TITLES = {
        synced: "in sync with remote",
        drifted: "local differs from last push",
        dirty: "uncommitted local changes",
        untracked: "not tracked by git",
        ignored: "git-ignored",
        none: "not synced to this remote",
    };

    var _DICT_REMOTE_LABELS = {
        sGithub: "GitHub",
        sOverleaf: "Overleaf",
        sZenodo: "Zenodo",
        sArxiv: "arXiv",
    };

    var _S_SVG_COMMON =
        '<svg xmlns="http://www.w3.org/2000/svg" ' +
        'viewBox="0 0 16 16" class="remote-badge-icon" ' +
        'width="14" height="14" aria-hidden="true">';

    var _DICT_REMOTE_SVG_PATHS = {
        sGithub:
            'M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07' +
            '.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49' +
            '-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01' +
            '-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66' +
            '.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95' +
            ' 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0' +
            ' .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09' +
            ' 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08' +
            ' 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65' +
            ' 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2' +
            ' 0 .21.15.46.55.38A8.013 8.013 0 0 0 16 8c0-4.42' +
            '-3.58-8-8-8z',
        sOverleaf:
            'M13.9 2.1c-5.1-.4-9.3 2.3-10.8 7-.5 1.5-.3 3 .5 3.9' +
            '.4.5 1.1.5 1.6.2 0-1.3.4-2.7 1.2-4 1.4-2.4 3.6-3.7' +
            ' 6.4-4.4-2.5 1.4-4.6 3.6-5.8 6.4l1.4.9c1.1-2.2 3-3.9' +
            ' 5.2-4.9.4-1.6.4-3.3.3-5.1z',
        sZenodo:
            'M3 2.5h10v2.5L6 13h7v2.5H3V13l7-8H3z',
        sArxiv:
            'M2 2h12v2H10l3 10h-2L8 5.5 5 14H3L6 4H2V2z',
    };

    function _fsRenderRemoteIcon(sRemoteKey) {
        var sPath = _DICT_REMOTE_SVG_PATHS[sRemoteKey];
        if (!sPath) return "";
        return _S_SVG_COMMON +
            '<path fill="currentColor" d="' + sPath + '"/>' +
            '</svg>';
    }

    function _fsStripWorkspacePrefix(sResolvedPath, sWorkdir) {
        if (!sResolvedPath) return "";
        var s = sResolvedPath;
        var sRepo = "";
        if (typeof PipeleyenApp !== "undefined" &&
            PipeleyenApp.fdictGetWorkflow) {
            sRepo = (PipeleyenApp.fdictGetWorkflow() || {})
                .sProjectRepoPath || "";
        }
        if (sRepo) {
            var sPrefix = sRepo.replace(/\/+$/, "") + "/";
            if (s.indexOf(sPrefix) === 0) {
                return s.substring(sPrefix.length);
            }
        }
        if (s.indexOf("/workspace/") === 0) {
            return s.substring("/workspace/".length);
        }
        if (sWorkdir && s.indexOf(sWorkdir + "/") === 0) {
            return s.substring(sWorkdir.length + 1);
        }
        while (s.indexOf("/") === 0) s = s.substring(1);
        return s;
    }

    function _fdictPlaceholderBadges() {
        return {
            sGithub: "none",
            sOverleaf: "none",
            sZenodo: "none",
            sArxiv: "none",
        };
    }

    function fdictGetBadgesForFile(sResolvedPath, sWorkdir) {
        var sKey = _fsStripWorkspacePrefix(sResolvedPath, sWorkdir);
        if (!sKey) return _fdictPlaceholderBadges();
        return _dictState.dictBadges[sKey] ||
            _fdictPlaceholderBadges();
    }

    var _A_DEFAULT_REMOTE_KEYS = [
        "sGithub", "sOverleaf", "sZenodo", "sArxiv",
    ];

    function fsRenderBadgeRow(dictTriple, aRemoteKeys) {
        var dictUse = dictTriple || _fdictPlaceholderBadges();
        var aKeys = aRemoteKeys || _A_DEFAULT_REMOTE_KEYS;
        var sHtml = '<span class="remote-badges" draggable="false">';
        aKeys.forEach(function (sKey) {
            var sState = dictUse[sKey] || "none";
            var sLabel = _DICT_REMOTE_LABELS[sKey];
            var sTitle = sLabel + ": " +
                (_DICT_BADGE_TITLES[sState] || sState);
            sHtml += '<span class="remote-badge badge-' + sState +
                '" title="' + sTitle + '" data-remote="' +
                sKey + '" draggable="false">' +
                _fsRenderRemoteIcon(sKey) +
                '</span>';
        });
        sHtml += '</span>';
        return sHtml;
    }

    function fnRefresh(sContainerId) {
        if (!sContainerId) {
            return Promise.resolve();
        }
        _dictState.sCurrentContainerId = sContainerId;
        return VaibifyApi.fdictGet(
            "/api/git/" + encodeURIComponent(sContainerId) + "/badges"
        ).then(function (dictResult) {
            if (!dictResult || typeof dictResult !== "object") return;
            var bChanged = _fbBadgeMapChanged(
                _dictState.dictBadges, dictResult.dictBadges || {});
            _dictState.dictBadges = dictResult.dictBadges || {};
            _dictState.dictRepoSummary = dictResult.dictGit ||
                _dictState.dictRepoSummary;
            if (bChanged) _fnRequestStepListRerender();
        }).catch(function () {
            _dictState.dictBadges = {};
        });
    }


    function _fbBadgeMapChanged(dictOld, dictNew) {
        var aOldKeys = Object.keys(dictOld || {});
        var aNewKeys = Object.keys(dictNew || {});
        if (aOldKeys.length !== aNewKeys.length) return true;
        for (var i = 0; i < aNewKeys.length; i++) {
            var sKey = aNewKeys[i];
            var dictOldEntry = dictOld[sKey];
            var dictNewEntry = dictNew[sKey];
            if (!dictOldEntry) return true;
            if (_fbBadgeEntryChanged(dictOldEntry, dictNewEntry)) {
                return true;
            }
        }
        return false;
    }


    function _fbBadgeEntryChanged(dictOld, dictNew) {
        var aKeys = ["sGithub", "sOverleaf", "sZenodo", "sArxiv"];
        for (var i = 0; i < aKeys.length; i++) {
            if (dictOld[aKeys[i]] !== dictNew[aKeys[i]]) return true;
        }
        return false;
    }


    function _fnRequestStepListRerender() {
        if (typeof PipeleyenApp === "undefined") return;
        if (typeof PipeleyenApp.fnRenderStepList !== "function") return;
        PipeleyenApp.fnRenderStepList();
    }

    function fdictRepoSummary() {
        return _dictState.dictRepoSummary;
    }

    function _fbHasBothHashFlags(dictStatus) {
        if (!dictStatus || typeof dictStatus !== "object") return false;
        return (
            Object.prototype.hasOwnProperty.call(dictStatus, "bMtimeStale") &&
            Object.prototype.hasOwnProperty.call(dictStatus, "bHashStale")
        );
    }

    function fsRenderStepStaleBadge(dictStatus) {
        if (!_fbHasBothHashFlags(dictStatus)) return "";
        var bMtimeStale = !!dictStatus.bMtimeStale;
        var bHashStale = !!dictStatus.bHashStale;
        if (bMtimeStale && !bHashStale) {
            return (
                '<span class="step-stale-badge stale-mtime-only" ' +
                'title="modified time changed but content matches ' +
                'manifest, no action needed">\u25CC</span>'
            );
        }
        return "";
    }

    function fbRenderHollowStaleIndicator(dictStatus) {
        if (!_fbHasBothHashFlags(dictStatus)) return false;
        return !!dictStatus.bMtimeStale && !dictStatus.bHashStale;
    }

    return {
        fnRefresh: fnRefresh,
        fdictGetBadgesForFile: fdictGetBadgesForFile,
        fsRenderBadgeRow: fsRenderBadgeRow,
        fdictRepoSummary: fdictRepoSummary,
        fsRenderStepStaleBadge: fsRenderStepStaleBadge,
        fbRenderHollowStaleIndicator: fbRenderHollowStaleIndicator,
    };
})();
