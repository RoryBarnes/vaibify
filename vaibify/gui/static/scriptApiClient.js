/* Vaibify — Centralized API client */

var VaibifyApi = (function () {
    "use strict";

    function fdictParseJsonSafely(response) {
        return response.json().catch(function () {
            return {};
        });
    }

    async function fdictGet(sUrl) {
        var response = await fetch(sUrl);
        if (!response.ok) {
            var dictError = await fdictParseJsonSafely(response);
            throw new Error(
                dictError.detail || "Request failed (" +
                response.status + ")"
            );
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
        var response = await fetch(sUrl, dictOptions);
        if (!response.ok) {
            var dictError = await fdictParseJsonSafely(response);
            throw new Error(
                dictError.detail || "Request failed (" +
                response.status + ")"
            );
        }
        return response.json();
    }

    async function fdictPostRaw(sUrl) {
        var response = await fetch(sUrl, {method: "POST"});
        if (!response.ok) {
            var dictError = await fdictParseJsonSafely(response);
            throw new Error(
                dictError.detail || "Request failed (" +
                response.status + ")"
            );
        }
        return response.json();
    }

    async function fdictPut(sUrl, dictBody) {
        var response = await fetch(sUrl, {
            method: "PUT",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify(dictBody),
        });
        if (!response.ok) {
            var dictError = await fdictParseJsonSafely(response);
            throw new Error(
                dictError.detail || "Request failed (" +
                response.status + ")"
            );
        }
        return response.json();
    }

    async function fnDelete(sUrl) {
        var response = await fetch(sUrl, {method: "DELETE"});
        if (!response.ok) {
            var dictError = await fdictParseJsonSafely(response);
            throw new Error(
                dictError.detail || "Delete failed (" +
                response.status + ")"
            );
        }
        return response.json();
    }

    async function fsGetText(sUrl) {
        var response = await fetch(sUrl);
        if (!response.ok) {
            throw new Error(
                "Request failed (" + response.status + ")"
            );
        }
        return response.text();
    }

    async function fbHead(sUrl, dictOptions) {
        var dictFetchOptions = {method: "HEAD"};
        if (dictOptions && dictOptions.signal) {
            dictFetchOptions.signal = dictOptions.signal;
        }
        var response = await fetch(sUrl, dictFetchOptions);
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
