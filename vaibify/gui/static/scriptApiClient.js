/* Vaibify — Centralized API client */

var VaibifyApi = (function () {
    "use strict";

    /*
     * Errors thrown from the fetch helpers carry a structured tag so
     * callers (notably the polling layer and the connection monitor)
     * can distinguish a server outage from a 401 token rotation from
     * a routine HTTP failure without string-matching error messages.
     * Shape:
     *   { sKind: "network"     | "unauthorized" | "http",
     *     iStatus: number,       // 0 for network
     *     sMessage: string }
     */

    function fnTagError(sKind, iStatus, sMessage) {
        var error = new Error(sMessage);
        error.sKind = sKind;
        error.iStatus = iStatus;
        return error;
    }

    function fdictParseJsonSafely(response) {
        return response.json().catch(function () {
            return {};
        });
    }

    function fbIsNetworkFailure(error) {
        if (error && error.sKind === "network") return true;
        return error instanceof TypeError;
    }

    async function _frResponseOrThrow(sUrl, dictOptions) {
        try {
            return await fetch(sUrl, dictOptions || {});
        } catch (error) {
            if (fbIsNetworkFailure(error)) {
                throw fnTagError(
                    "network", 0,
                    "Cannot reach Vaibify server: " +
                    (error.message || "connection refused")
                );
            }
            throw error;
        }
    }

    async function _fnThrowForStatus(response, sFallback) {
        var dictError = await fdictParseJsonSafely(response);
        var dictDetail = _fdictExtractDetail(dictError);
        var sMessage = dictDetail.sMessage
            || (sFallback + " (" + response.status + ")");
        var sKind = response.status === 401 ? "unauthorized" : "http";
        var error = fnTagError(sKind, response.status, sMessage);
        error.dictDetail = dictDetail;
        throw error;
    }

    function _fdictExtractDetail(dictError) {
        // FastAPI's `detail` is a plain string, a structured object,
        // or — for a 422 — a LIST of field validation errors.
        // Normalize all three so callers always read `sMessage` and
        // can opt-in to richer fields like `sStderrTail` without
        // breaking older string-detail routes.
        var detail = dictError && dictError.detail;
        if (detail === undefined || detail === null) return {};
        if (typeof detail === "string") return {sMessage: detail};
        if (Array.isArray(detail)) return _fdictExplainValidationErrors(detail);
        if (typeof detail === "object") return detail;
        return {sMessage: String(detail)};
    }

    function _fdictExplainValidationErrors(listErrors) {
        /* A 422 carries the field and the reason; the extractor read
           the list as a plain object, found no sMessage on it, and
           every shape rejection in the dashboard rendered as the bare
           "Request failed (422)" — a researcher who left a model
           unchosen was told a number (2026-08-28). The field PATH is
           what makes it actionable, so it is kept and only the
           framework's leading "body" segment is dropped. */
        var listSentences = listErrors.map(function (dictOne) {
            var listPath = (dictOne.loc || []).filter(function (jsonPart) {
                return jsonPart !== "body";
            });
            var sField = listPath.join(" → ");
            var sReason = dictOne.msg || "is not valid";
            return sField ? sField + ": " + sReason : sReason;
        }).filter(Boolean);
        if (!listSentences.length) return {};
        return {
            sMessage: "The server rejected this request — "
                + listSentences.join("; "),
            listValidationErrors: listErrors,
        };
    }

    /* --- Lost-claim recovery ---

       A claim that has no socket lives on a thirty-second presence
       window, and a browser throttles or freezes a page it cannot see
       for longer than that, so a researcher who returns to an open
       project finds the hub no longer holds the claim for it. The hub
       says so in a code (``claim-required``) and the cure is the claim
       the page can make itself. It is done HERE, once, because every
       request in the page can meet the refusal and four copies of the
       cure had already drifted apart.

       A request is recovered at most once, only while the view it was
       issued from is still on screen, and only for the container the
       page had selected when it was issued -- never one the selection
       has since moved to. */

    var S_REFUSAL_CLAIM_REQUIRED = "claim-required";
    var S_CLAIM_RECOVERY_RECOVERED = "recovered";
    var S_CLAIM_RECOVERY_REFUSED = "refused";
    var S_CLAIM_RECOVERY_ABANDONED = "abandoned";
    var S_CLAIM_RECOVERY_INCOMPLETE = "incomplete";
    var S_CONNECT_PREFIX = "/api/connect/";

    var _fsRecoverLostClaim = null;
    var _dictRecoveryByName = {};
    var _dictRefusedViewByName = {};
    var _dictConnectTailById = {};
    var _dictConnectCountById = {};

    function fnRegisterClaimRecovery(fsRecover) {
        _fsRecoverLostClaim = fsRecover;
    }

    function fbRefusalIsClaimRequired(error) {
        var dictDetail = (error && error.dictDetail) || {};
        return dictDetail.sRefusal === S_REFUSAL_CLAIM_REQUIRED;
    }

    function fbErrorWasHandledByRecovery(error) {
        return Boolean(error && error.bHandledByRecovery);
    }

    function _fnMarkHandledByRecovery(error) {
        error.bHandledByRecovery = true;
        return error;
    }

    function _flistPathSegments(sUrl) {
        return String(sUrl).split("?")[0].split("/").map(
            function (sSegment) {
                try {
                    return decodeURIComponent(sSegment);
                } catch (error) {
                    return sSegment;
                }
            });
    }

    function _fdictCaptureRequestContext(sUrl) {
        var dictNoTarget = {sName: "", iViewGeneration: 0};
        if (typeof VaibifyApp === "undefined" ||
                typeof VaibifyContainerManager === "undefined") {
            return dictNoTarget;
        }
        var sName = VaibifyContainerManager.fsGetSelectedContainerName();
        var sId = VaibifyContainerManager.fsGetSelectedContainerId();
        var listSegments = _flistPathSegments(sUrl);
        var bNamesSelected = [sName, sId].some(function (sIdentity) {
            return Boolean(sIdentity) &&
                listSegments.indexOf(sIdentity) !== -1;
        });
        if (!bNamesSelected) return dictNoTarget;
        return {
            sName: sName,
            iViewGeneration: VaibifyApp.fiGetViewGeneration(),
        };
    }

    function _fbViewIsUnchanged(dictIssued) {
        return VaibifyApp.fiGetViewGeneration() ===
            dictIssued.iViewGeneration && VaibifyApp.fbClaimRecoveryIsAllowed();
    }

    function _fpromiseRecoveryFor(dictIssued, bFailedRequestWasConnect) {
        /* One recovery per container name: a second claim sent before
           the first one's lease is stored would be refused as another
           session's. A recovery begun from an older view finishes
           before a newer view's begins, and one begun from THIS view
           is simply joined. */
        var sName = dictIssued.sName;
        var dictInFlight = _dictRecoveryByName[sName];
        if (dictInFlight &&
                dictInFlight.iViewGeneration === dictIssued.iViewGeneration) {
            return dictInFlight.promise;
        }
        var promisePrior = dictInFlight
            ? dictInFlight.promise.catch(function () {})
            : Promise.resolve();
        var promiseRecovery = promisePrior.then(function () {
            return _fsRecoverLostClaim(
                sName, dictIssued.iViewGeneration, bFailedRequestWasConnect);
        });
        var dictRecord = {
            promise: promiseRecovery,
            iViewGeneration: dictIssued.iViewGeneration,
        };
        _dictRecoveryByName[sName] = dictRecord;
        var fnForgetWhenSettled = function () {
            if (_dictRecoveryByName[sName] === dictRecord) {
                delete _dictRecoveryByName[sName];
            }
        };
        promiseRecovery.then(fnForgetWhenSettled, fnForgetWhenSettled);
        return promiseRecovery;
    }

    async function _fnRecoverOrThrow(errorRefused, dictIssued, sUrl) {
        /* Silently drop what the researcher has already left: no
           claim, no retry, no toast. A refusal that already earned its
           sentence is not asked for again while the same view stays. */
        var sName = dictIssued.sName;
        if (!_fbViewIsUnchanged(dictIssued) ||
                _dictRefusedViewByName[sName] === dictIssued.iViewGeneration) {
            throw _fnMarkHandledByRecovery(errorRefused);
        }
        var sOutcome = await _fpromiseRecoveryFor(
            dictIssued, sUrl.indexOf(S_CONNECT_PREFIX) === 0);
        if (sOutcome === S_CLAIM_RECOVERY_REFUSED) {
            _dictRefusedViewByName[sName] = dictIssued.iViewGeneration;
        }
        if (sOutcome !== S_CLAIM_RECOVERY_RECOVERED ||
                !_fbViewIsUnchanged(dictIssued)) {
            throw _fnMarkHandledByRecovery(errorRefused);
        }
    }

    async function _fvalueWithClaimRecovery(sUrl, fnAttempt) {
        var dictIssued = _fdictCaptureRequestContext(sUrl);
        try {
            return await fnAttempt();
        } catch (error) {
            if (!dictIssued.sName || !_fsRecoverLostClaim ||
                    !fbRefusalIsClaimRequired(error)) {
                throw error;
            }
            await _fnRecoverOrThrow(error, dictIssued, sUrl);
            return await fnAttempt();
        }
    }

    /* --- The connect queue ---

       Every POST /api/connect for one container runs one at a time, in
       the order it was asked for, because the hub caches the workflow
       the LAST connect named and that must be the researcher's latest
       choice. A slot is released the moment its response arrives, a
       refusal included, and a recovery enqueues afresh: a refused
       connect can never wait on one queued behind itself. */

    function _fpromiseRunInConnectQueue(sContainerId, fnSend) {
        var promisePrior = _dictConnectTailById[sContainerId] ||
            Promise.resolve();
        var promiseRun = promisePrior.then(fnSend);
        var fnIgnore = function () {};
        var promiseTail = promiseRun.then(fnIgnore, fnIgnore);
        _dictConnectTailById[sContainerId] = promiseTail;
        promiseTail.then(function () {
            if (_dictConnectTailById[sContainerId] === promiseTail) {
                delete _dictConnectTailById[sContainerId];
            }
        });
        return promiseRun;
    }

    function _fsConnectQueueKey(sUrl) {
        if (typeof sUrl !== "string" ||
                sUrl.indexOf(S_CONNECT_PREFIX) !== 0) {
            return "";
        }
        return sUrl.substring(S_CONNECT_PREFIX.length).split("?")[0];
    }

    function fdictPostConnectWhenReached(sContainerId, fsBuildUrl) {
        /* For a recovery's connect, which renews what a lapsed claim left
           behind and must never be the LAST word. The URL is composed
           when the request reaches the FRONT of the queue, so it names
           the workflow open then; an empty URL, or a connect the
           researcher asked for since this one was scheduled, means there
           is nothing left to renew and no request is made. */
        var iCountWhenScheduled = _dictConnectCountById[sContainerId] || 0;
        return _fpromiseRunInConnectQueue(sContainerId, function () {
            if ((_dictConnectCountById[sContainerId] || 0) !==
                    iCountWhenScheduled) {
                return Promise.resolve(null);
            }
            var sUrl = fsBuildUrl();
            if (!sUrl) return Promise.resolve(null);
            return _fdictPostRawOnce(sUrl);
        });
    }

    function fdictAdoptSourceFingerprint(dictPayload) {
        /* Any response carrying the post-save exact-source
           fingerprint updates the dashboard's acknowledged value:
           the client is by definition rendering the edit it just
           made, and without this its own step edit would make the
           next Run refuse at the dispatch freshness gate. */
        if (dictPayload &&
            typeof dictPayload.sExactSourceFingerprint === "string" &&
            dictPayload.sExactSourceFingerprint &&
            typeof VaibifyApp !== "undefined" &&
            VaibifyApp.fnAcknowledgeSourceFingerprint) {
            VaibifyApp.fnAcknowledgeSourceFingerprint(
                dictPayload.sExactSourceFingerprint);
        }
        return dictPayload;
    }

    async function _fdictGetOnce(sUrl) {
        var response = await _frResponseOrThrow(sUrl);
        if (!response.ok) {
            await _fnThrowForStatus(response, "Request failed");
        }
        return response.json().then(fdictAdoptSourceFingerprint);
    }

    async function _fdictPostOnce(sUrl, dictBody) {
        var dictOptions = {
            method: "POST",
            headers: {"Content-Type": "application/json"},
        };
        if (dictBody !== undefined) {
            dictOptions.body = JSON.stringify(dictBody);
        }
        var response = await _frResponseOrThrow(sUrl, dictOptions);
        if (!response.ok) {
            await _fnThrowForStatus(response, "Request failed");
        }
        return response.json().then(fdictAdoptSourceFingerprint);
    }

    async function _fdictPostRawOnce(sUrl) {
        var response = await _frResponseOrThrow(
            sUrl, {method: "POST"},
        );
        if (!response.ok) {
            await _fnThrowForStatus(response, "Request failed");
        }
        return response.json().then(fdictAdoptSourceFingerprint);
    }

    async function _fdictPutOnce(sUrl, dictBody) {
        var response = await _frResponseOrThrow(sUrl, {
            method: "PUT",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify(dictBody),
        });
        if (!response.ok) {
            await _fnThrowForStatus(response, "Request failed");
        }
        return response.json().then(fdictAdoptSourceFingerprint);
    }

    async function _fnDeleteOnce(sUrl) {
        var response = await _frResponseOrThrow(
            sUrl, {method: "DELETE"},
        );
        if (!response.ok) {
            await _fnThrowForStatus(response, "Delete failed");
        }
        return response.json().then(fdictAdoptSourceFingerprint);
    }

    async function _fsGetTextOnce(sUrl) {
        var response = await _frResponseOrThrow(sUrl);
        if (!response.ok) {
            await _fnThrowForStatus(response, "Request failed");
        }
        return response.text();
    }

    function fdictGet(sUrl) {
        return _fvalueWithClaimRecovery(sUrl, function () {
            return _fdictGetOnce(sUrl);
        });
    }

    function fdictPost(sUrl, dictBody) {
        return _fvalueWithClaimRecovery(sUrl, function () {
            return _fdictPostOnce(sUrl, dictBody);
        });
    }

    function fdictPostRaw(sUrl) {
        var sQueueKey = _fsConnectQueueKey(sUrl);
        if (sQueueKey) {
            _dictConnectCountById[sQueueKey] =
                (_dictConnectCountById[sQueueKey] || 0) + 1;
        }
        return _fvalueWithClaimRecovery(sUrl, function () {
            if (!sQueueKey) return _fdictPostRawOnce(sUrl);
            return _fpromiseRunInConnectQueue(sQueueKey, function () {
                return _fdictPostRawOnce(sUrl);
            });
        });
    }

    function fdictPut(sUrl, dictBody) {
        return _fvalueWithClaimRecovery(sUrl, function () {
            return _fdictPutOnce(sUrl, dictBody);
        });
    }

    function fnDelete(sUrl) {
        return _fvalueWithClaimRecovery(sUrl, function () {
            return _fnDeleteOnce(sUrl);
        });
    }

    function fsGetText(sUrl) {
        return _fvalueWithClaimRecovery(sUrl, function () {
            return _fsGetTextOnce(sUrl);
        });
    }

    async function fbHead(sUrl, dictOptions) {
        var dictFetchOptions = {method: "HEAD"};
        if (dictOptions && dictOptions.signal) {
            dictFetchOptions.signal = dictOptions.signal;
        }
        var response = await _frResponseOrThrow(
            sUrl, dictFetchOptions,
        );
        return response.ok;
    }

    /* --- Streamed upload ---

       The one place in the page that speaks XMLHttpRequest, because it
       is the only way a browser reports how much of a request BODY has
       been sent. An XHR does not pass through the fetch wrapper that
       gives every other request its credentials, so this function sets
       the session token and the lease itself, reading both when the
       request is SENT (the wrapper reads the lease inside the call for
       the same reason: a value captured earlier can be empty or
       stale). No other XHR in the page may bypass the wrapper; the
       source scan in tests/testUploadTransportContract.py holds the
       line. */

    function _fnSetUploadCredentials(xhrUpload) {
        xhrUpload.setRequestHeader(
            "X-Session-Token", VaibifyApp.fsGetSessionToken());
        var sLease = VaibifyApp.fsGetLeaseId();
        if (sLease) xhrUpload.setRequestHeader("X-Vaibify-Lease", sLease);
    }

    function _fdictParseUploadBody(xhrUpload) {
        try {
            return JSON.parse(xhrUpload.responseText);
        } catch (error) {
            return {};
        }
    }

    function _fdictDescribeUploadAnswer(xhrUpload) {
        var dictBody = _fdictParseUploadBody(xhrUpload);
        var dictDetail = _fdictExtractDetail(dictBody);
        var bOk = xhrUpload.status >= 200 && xhrUpload.status < 300;
        return {
            bOk: bOk,
            iStatus: xhrUpload.status,
            dictBody: dictBody,
            dictDetail: dictDetail,
            sMessage: bOk ? "" : (dictDetail.sMessage ||
                "The server refused the upload (" +
                xhrUpload.status + ") without saying why."),
            bAborted: false,
            bNetworkFailure: false,
        };
    }

    function _fdictDescribeUploadInterruption(bAborted) {
        return {
            bOk: false,
            iStatus: 0,
            dictBody: {},
            dictDetail: {},
            sMessage: bAborted
                ? "The upload was cancelled."
                : "Cannot reach Vaibify server: the connection was " +
                  "lost during the upload.",
            bAborted: bAborted,
            bNetworkFailure: !bAborted,
        };
    }

    function fdictStartUpload(sUrl, blobBody, fnOnProgress) {
        /* PUT one body to sUrl. Returns {promiseOutcome, fnAbort}; the
           promise always RESOLVES, to {bOk, iStatus, sMessage,
           dictDetail, dictBody, bAborted, bNetworkFailure}, so a batch
           reads every answer, the server's own sentence included, the
           same way. fnOnProgress receives the bytes sent so far. A null
           body sends none (a folder to create). */
        var xhrUpload = new XMLHttpRequest();
        var promiseOutcome = new Promise(function (fnResolve) {
            xhrUpload.open("PUT", sUrl);
            xhrUpload.setRequestHeader(
                "Content-Type", "application/octet-stream");
            _fnSetUploadCredentials(xhrUpload);
            xhrUpload.upload.onprogress = function (event) {
                if (fnOnProgress) fnOnProgress(event.loaded);
            };
            xhrUpload.onload = function () {
                fnResolve(_fdictDescribeUploadAnswer(xhrUpload));
            };
            xhrUpload.onerror = function () {
                fnResolve(_fdictDescribeUploadInterruption(false));
            };
            xhrUpload.onabort = function () {
                fnResolve(_fdictDescribeUploadInterruption(true));
            };
            xhrUpload.send(blobBody || null);
        });
        return {
            promiseOutcome: promiseOutcome,
            fnAbort: function () { xhrUpload.abort(); },
        };
    }

    /* --- Download probe ---

       An anchor download cannot show an HTTP error: the browser would
       save the error page as the file. So the page asks first. HEAD is
       the cheap question; it answers with the status, but HTTP gives a
       HEAD response no body, so the SERVER'S REASON (a link that leads
       out of the project, a path that no longer exists) cannot arrive
       with it. When HEAD says no, the same request as a GET carries the
       sentence: the route refuses before it streams a byte, and if the
       answer has changed and the GET succeeds it is abandoned at once. */

    async function _fdictExplainRefusedDownload(sUrl, iHeadStatus) {
        var controllerAbort = new AbortController();
        var response = await _frResponseOrThrow(
            sUrl, {signal: controllerAbort.signal});
        if (response.ok) {
            controllerAbort.abort();
            return {bOk: true, iStatus: response.status, sMessage: ""};
        }
        var dictDetail = _fdictExtractDetail(
            await fdictParseJsonSafely(response));
        return {
            bOk: false,
            iStatus: response.status,
            sMessage: dictDetail.sMessage || (
                "The download was refused (" + iHeadStatus + ")."),
        };
    }

    async function fdictProbeDownload(sUrl) {
        var responseHead = await _frResponseOrThrow(
            sUrl, {method: "HEAD"});
        if (responseHead.ok) {
            return {bOk: true, iStatus: responseHead.status, sMessage: ""};
        }
        return _fdictExplainRefusedDownload(sUrl, responseHead.status);
    }

    return {
        S_CLAIM_RECOVERY_RECOVERED: S_CLAIM_RECOVERY_RECOVERED,
        S_CLAIM_RECOVERY_REFUSED: S_CLAIM_RECOVERY_REFUSED,
        S_CLAIM_RECOVERY_ABANDONED: S_CLAIM_RECOVERY_ABANDONED,
        S_CLAIM_RECOVERY_INCOMPLETE: S_CLAIM_RECOVERY_INCOMPLETE,
        fnRegisterClaimRecovery: fnRegisterClaimRecovery,
        fbRefusalIsClaimRequired: fbRefusalIsClaimRequired,
        fbErrorWasHandledByRecovery: fbErrorWasHandledByRecovery,
        fdictPostConnectWhenReached: fdictPostConnectWhenReached,
        fdictGet: fdictGet,
        fdictPost: fdictPost,
        fdictPostRaw: fdictPostRaw,
        fdictPut: fdictPut,
        fnDelete: fnDelete,
        fsGetText: fsGetText,
        fbHead: fbHead,
        fdictStartUpload: fdictStartUpload,
        fdictProbeDownload: fdictProbeDownload,
    };
})();
