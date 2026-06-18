/* Vaibify — Editor-draft persistence (localStorage + backend mirror)
 *
 * When a researcher is editing a text file in the dashboard, their
 * keystrokes live only in the textarea's `.value` until they press
 * Save. Any event that destroys that DOM node — a figure pushed to
 * the same viewer, a page reload, a browser crash — also destroys
 * the unsaved content.
 *
 * This module owns the safety net:
 *
 *   • localStorage (debounced ~500 ms after the last keystroke) is
 *     fast and survives same-tab navigation, reloads, and most
 *     browser crashes.
 *   • Backend ``PUT /api/draft/...`` (debounced ~5 s) survives
 *     localStorage loss, profile resets, and machine moves.
 *
 * Saving the file through ``PUT /api/file/...`` clears both. The
 * frontend reads drafts back on edit-mode entry and offers them via
 * the recovery banner constructed in ``scriptFigureViewer.js``.
 *
 * sDraftKey is the canonical identity of a draft:
 *   ``<sContainerId>:<sWorkdir>:<sFilePath>``
 *
 * Public API:
 *   fnSaveLocalDraft(sDraftKey, dictDraft)        debounced 500 ms
 *   fnSaveRemoteDraft(sDraftKey, dictDraft, sContainerId, sFilePath,
 *                     sWorkdir)                    debounced 5000 ms
 *   fnFlushPendingSaves(sDraftKey)                immediate flush
 *   fdictGetLocalDraft(sDraftKey)                 sync read
 *   fdictGetRemoteDraft(sContainerId, sFilePath,
 *                       sWorkdir)                  async read
 *   fnDeleteDraft(sDraftKey, sContainerId,
 *                 sFilePath, sWorkdir)             both layers
 *   fsHashContent(sText)                          sha256 hex (async)
 *   fsBuildDraftKey(sContainerId, sFilePath, sWorkdir)
 */

var PipeleyenDraftManager = (function () {
    "use strict";

    var S_STORAGE_KEY = "vaibifyDrafts";
    var I_LOCAL_DEBOUNCE_MS = 500;
    var I_REMOTE_DEBOUNCE_MS = 5000;
    var I_MAX_LOCAL_BYTES = 3 * 1024 * 1024;

    var _dictLocalTimers = {};
    var _dictRemoteTimers = {};
    var _dictPendingLocalDraft = {};
    var _dictPendingRemoteArgs = {};

    function fsBuildDraftKey(sContainerId, sFilePath, sWorkdir) {
        return (sContainerId || "") + ":" +
            (sWorkdir || "") + ":" + (sFilePath || "");
    }

    function _fdictReadAllLocal() {
        try {
            var sRaw = localStorage.getItem(S_STORAGE_KEY);
            if (!sRaw) return {};
            var dictParsed = JSON.parse(sRaw);
            return typeof dictParsed === "object" && dictParsed
                ? dictParsed : {};
        } catch (error) {
            return {};
        }
    }

    function _fnWriteAllLocal(dictAll) {
        try {
            localStorage.setItem(
                S_STORAGE_KEY, JSON.stringify(dictAll),
            );
            return true;
        } catch (error) {
            return false;
        }
    }

    function _fiEstimateBytes(dictAll) {
        try {
            return JSON.stringify(dictAll).length;
        } catch (error) {
            return I_MAX_LOCAL_BYTES;
        }
    }

    function _fnEvictUntilUnderCap(dictAll) {
        var listEntries = Object.keys(dictAll).map(function (sKey) {
            return {
                sKey: sKey,
                iTimestampMs: dictAll[sKey].iTimestampMs || 0,
            };
        });
        listEntries.sort(function (a, b) {
            return a.iTimestampMs - b.iTimestampMs;
        });
        var iIndex = 0;
        while (
            iIndex < listEntries.length &&
            _fiEstimateBytes(dictAll) > I_MAX_LOCAL_BYTES
        ) {
            delete dictAll[listEntries[iIndex].sKey];
            iIndex += 1;
        }
    }

    function _fnPersistLocal(sDraftKey, dictDraft) {
        var dictAll = _fdictReadAllLocal();
        dictAll[sDraftKey] = dictDraft;
        if (_fiEstimateBytes(dictAll) > I_MAX_LOCAL_BYTES) {
            _fnEvictUntilUnderCap(dictAll);
        }
        return _fnWriteAllLocal(dictAll);
    }

    function fnSaveLocalDraft(sDraftKey, dictDraft) {
        _dictPendingLocalDraft[sDraftKey] = dictDraft;
        if (_dictLocalTimers[sDraftKey]) {
            clearTimeout(_dictLocalTimers[sDraftKey]);
        }
        _dictLocalTimers[sDraftKey] = setTimeout(function () {
            delete _dictLocalTimers[sDraftKey];
            var dictPending = _dictPendingLocalDraft[sDraftKey];
            if (!dictPending) return;
            delete _dictPendingLocalDraft[sDraftKey];
            _fnPersistLocal(sDraftKey, dictPending);
        }, I_LOCAL_DEBOUNCE_MS);
    }

    function fdictGetLocalDraft(sDraftKey) {
        var dictAll = _fdictReadAllLocal();
        return dictAll[sDraftKey] || null;
    }

    function _fnDeleteLocalDraft(sDraftKey) {
        var dictAll = _fdictReadAllLocal();
        if (!(sDraftKey in dictAll)) return;
        delete dictAll[sDraftKey];
        _fnWriteAllLocal(dictAll);
    }

    function _fsBuildBackendUrl(sContainerId, sFilePath, sWorkdir) {
        var sCleanPath = (sFilePath || "").replace(/^\/+/, "");
        var sUrl = "/api/draft/" + sContainerId + "/" + sCleanPath;
        if (sWorkdir) {
            sUrl += "?sWorkdir=" + encodeURIComponent(sWorkdir);
        }
        return sUrl;
    }

    async function _fnPersistRemote(
        sDraftKey, dictDraft, sContainerId, sFilePath, sWorkdir,
    ) {
        var sUrl = _fsBuildBackendUrl(
            sContainerId, sFilePath, sWorkdir,
        );
        try {
            await VaibifyApi.fdictPut(sUrl, {
                sContent: dictDraft.sContent,
                sBaseHash: dictDraft.sBaseHash || "",
                sWorkdir: sWorkdir || "",
            });
        } catch (error) {
            /* Remote draft is best-effort; localStorage is the
             * primary safety net so a network blip never costs the
             * researcher their unsaved work. */
        }
    }

    function fnSaveRemoteDraft(
        sDraftKey, dictDraft, sContainerId, sFilePath, sWorkdir,
    ) {
        _dictPendingRemoteArgs[sDraftKey] = {
            dictDraft: dictDraft,
            sContainerId: sContainerId,
            sFilePath: sFilePath,
            sWorkdir: sWorkdir || "",
        };
        if (_dictRemoteTimers[sDraftKey]) {
            clearTimeout(_dictRemoteTimers[sDraftKey]);
        }
        _dictRemoteTimers[sDraftKey] = setTimeout(function () {
            delete _dictRemoteTimers[sDraftKey];
            var dictArgs = _dictPendingRemoteArgs[sDraftKey];
            if (!dictArgs) return;
            delete _dictPendingRemoteArgs[sDraftKey];
            _fnPersistRemote(
                sDraftKey, dictArgs.dictDraft, dictArgs.sContainerId,
                dictArgs.sFilePath, dictArgs.sWorkdir,
            );
        }, I_REMOTE_DEBOUNCE_MS);
    }

    function fnFlushPendingSaves(sDraftKey) {
        if (_dictLocalTimers[sDraftKey]) {
            clearTimeout(_dictLocalTimers[sDraftKey]);
            delete _dictLocalTimers[sDraftKey];
            var dictLocalPending = _dictPendingLocalDraft[sDraftKey];
            delete _dictPendingLocalDraft[sDraftKey];
            if (dictLocalPending) {
                _fnPersistLocal(sDraftKey, dictLocalPending);
            }
        }
        if (_dictRemoteTimers[sDraftKey]) {
            clearTimeout(_dictRemoteTimers[sDraftKey]);
            delete _dictRemoteTimers[sDraftKey];
            var dictArgs = _dictPendingRemoteArgs[sDraftKey];
            delete _dictPendingRemoteArgs[sDraftKey];
            if (dictArgs) {
                _fnPersistRemote(
                    sDraftKey, dictArgs.dictDraft,
                    dictArgs.sContainerId,
                    dictArgs.sFilePath, dictArgs.sWorkdir,
                );
            }
        }
    }

    async function fdictGetRemoteDraft(
        sContainerId, sFilePath, sWorkdir,
    ) {
        var sUrl = _fsBuildBackendUrl(
            sContainerId, sFilePath, sWorkdir,
        );
        try {
            var dictResponse = await VaibifyApi.fdictGet(sUrl);
            if (!dictResponse || !dictResponse.bExists) return null;
            return dictResponse;
        } catch (error) {
            return null;
        }
    }

    function fnDeleteDraft(
        sDraftKey, sContainerId, sFilePath, sWorkdir,
    ) {
        fnFlushPendingSaves(sDraftKey);
        _fnDeleteLocalDraft(sDraftKey);
        var sUrl = _fsBuildBackendUrl(
            sContainerId, sFilePath, sWorkdir,
        );
        VaibifyApi.fnDelete(sUrl).catch(function () {
            /* Best-effort cleanup. A leftover backend draft is
             * harmless — the recovery banner only surfaces drafts
             * whose content differs from the on-disk file. */
        });
    }

    async function fsHashContent(sText) {
        if (typeof crypto === "undefined" ||
                !crypto.subtle ||
                typeof TextEncoder === "undefined") {
            return "";
        }
        try {
            var daBytes = new TextEncoder().encode(sText || "");
            var bufHash = await crypto.subtle.digest(
                "SHA-256", daBytes,
            );
            var listHex = [];
            var daHash = new Uint8Array(bufHash);
            for (var i = 0; i < daHash.length; i += 1) {
                var sHex = daHash[i].toString(16);
                if (sHex.length === 1) sHex = "0" + sHex;
                listHex.push(sHex);
            }
            return listHex.join("");
        } catch (error) {
            return "";
        }
    }

    return {
        fsBuildDraftKey: fsBuildDraftKey,
        fnSaveLocalDraft: fnSaveLocalDraft,
        fnSaveRemoteDraft: fnSaveRemoteDraft,
        fnFlushPendingSaves: fnFlushPendingSaves,
        fdictGetLocalDraft: fdictGetLocalDraft,
        fdictGetRemoteDraft: fdictGetRemoteDraft,
        fnDeleteDraft: fnDeleteDraft,
        fsHashContent: fsHashContent,
    };
})();
