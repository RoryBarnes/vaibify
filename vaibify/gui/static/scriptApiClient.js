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

    async function fdictGet(sUrl) {
        var response = await _frResponseOrThrow(sUrl);
        if (!response.ok) {
            await _fnThrowForStatus(response, "Request failed");
        }
        return response.json().then(fdictAdoptSourceFingerprint);
    }

    async function fdictPost(sUrl, dictBody) {
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

    async function fdictPostRaw(sUrl) {
        var response = await _frResponseOrThrow(
            sUrl, {method: "POST"},
        );
        if (!response.ok) {
            await _fnThrowForStatus(response, "Request failed");
        }
        return response.json().then(fdictAdoptSourceFingerprint);
    }

    async function fdictPut(sUrl, dictBody) {
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

    async function fnDelete(sUrl) {
        var response = await _frResponseOrThrow(
            sUrl, {method: "DELETE"},
        );
        if (!response.ok) {
            await _fnThrowForStatus(response, "Delete failed");
        }
        return response.json().then(fdictAdoptSourceFingerprint);
    }

    async function fsGetText(sUrl) {
        var response = await _frResponseOrThrow(sUrl);
        if (!response.ok) {
            await _fnThrowForStatus(response, "Request failed");
        }
        return response.text();
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

    return {
        fdictGet: fdictGet,
        fdictPost: fdictPost,
        fdictPostRaw: fdictPostRaw,
        fdictPut: fdictPut,
        fnDelete: fnDelete,
        fsGetText: fsGetText,
        fbHead: fbHead,
    };
})();
