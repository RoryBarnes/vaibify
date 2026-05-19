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
        var sMessage =
            dictError.detail ||
            (sFallback + " (" + response.status + ")");
        if (response.status === 401) {
            throw fnTagError(
                "unauthorized", response.status, sMessage
            );
        }
        throw fnTagError("http", response.status, sMessage);
    }

    async function fdictGet(sUrl) {
        var response = await _frResponseOrThrow(sUrl);
        if (!response.ok) {
            await _fnThrowForStatus(response, "Request failed");
        }
        return response.json();
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
        return response.json();
    }

    async function fdictPostRaw(sUrl) {
        var response = await _frResponseOrThrow(
            sUrl, {method: "POST"},
        );
        if (!response.ok) {
            await _fnThrowForStatus(response, "Request failed");
        }
        return response.json();
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
        return response.json();
    }

    async function fnDelete(sUrl) {
        var response = await _frResponseOrThrow(
            sUrl, {method: "DELETE"},
        );
        if (!response.ok) {
            await _fnThrowForStatus(response, "Delete failed");
        }
        return response.json();
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
