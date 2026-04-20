/* Vaibify — per-file per-remote badge state for the Step Viewer.

   Fetches the G/O/Z badge triple for every tracked file from
   /api/git/{id}/badges and exposes:

   - VaibifyGitBadges.fnRefresh(sContainerId)
       Pulls the latest badge map and repo-level summary; returns a
       promise that resolves once the in-memory state is updated.

   - VaibifyGitBadges.fdictGetBadgesForFile(sResolvedPath, sWorkdir)
       Returns the per-file triple {sGithub, sOverleaf, sZenodo} for
       a resolved output-path. Returns null when the file hasn't been
       seen (caller renders nothing).

   - VaibifyGitBadges.fsRenderBadgeRow(dictTriple)
       Builds a fragment of three mini-badge spans for one file row.
       Pure; safe to call on every re-render.
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
        },
    };

    var _DICT_BADGE_GLYPHS = {
        synced: "\u2713",      // ✓
        drifted: "\u26A0",     // ⚠
        dirty: "\u25C6",       // ◆
        untracked: "\u002B",   // +
        ignored: "\u2013",     // –
        none: "\u2014",        // —
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
        sGithub: "G",
        sOverleaf: "O",
        sZenodo: "Z",
    };

    function _fsStripWorkspacePrefix(sResolvedPath, sWorkdir) {
        if (!sResolvedPath) return "";
        var s = sResolvedPath;
        if (s.indexOf("/workspace/") === 0) {
            return s.substring("/workspace/".length);
        }
        if (sWorkdir && s.indexOf(sWorkdir + "/") === 0) {
            return s.substring(sWorkdir.length + 1);
        }
        while (s.indexOf("/") === 0) s = s.substring(1);
        return s;
    }

    function _fdictPlaceholderTriple() {
        return {
            sGithub: "none",
            sOverleaf: "none",
            sZenodo: "none",
        };
    }

    function fdictGetBadgesForFile(sResolvedPath, sWorkdir) {
        var sKey = _fsStripWorkspacePrefix(sResolvedPath, sWorkdir);
        if (!sKey) return _fdictPlaceholderTriple();
        return _dictState.dictBadges[sKey] ||
            _fdictPlaceholderTriple();
    }

    function fsRenderBadgeRow(dictTriple) {
        var dictUse = dictTriple || _fdictPlaceholderTriple();
        var sHtml = '<span class="remote-badges">';
        ["sGithub", "sOverleaf", "sZenodo"].forEach(function (sKey) {
            var sState = dictUse[sKey] || "none";
            var sGlyph = _DICT_BADGE_GLYPHS[sState] ||
                _DICT_BADGE_GLYPHS.none;
            var sLabel = _DICT_REMOTE_LABELS[sKey];
            var sTitle = _DICT_REMOTE_LABELS[sKey] + ": " +
                (_DICT_BADGE_TITLES[sState] || sState);
            sHtml += '<span class="remote-badge badge-' + sState +
                '" title="' + sTitle + '">' +
                sLabel + sGlyph + '</span>';
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
            _dictState.dictBadges = dictResult.dictBadges || {};
            _dictState.dictRepoSummary = dictResult.dictGit ||
                _dictState.dictRepoSummary;
        }).catch(function () {
            _dictState.dictBadges = {};
        });
    }

    function fdictRepoSummary() {
        return _dictState.dictRepoSummary;
    }

    return {
        fnRefresh: fnRefresh,
        fdictGetBadgesForFile: fdictGetBadgesForFile,
        fsRenderBadgeRow: fsRenderBadgeRow,
        fdictRepoSummary: fdictRepoSummary,
    };
})();
