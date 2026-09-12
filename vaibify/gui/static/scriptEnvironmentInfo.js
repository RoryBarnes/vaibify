/* Vaibify — Admin > Environment Info.

   Answers "what am I actually running?" for the container this
   session is connected to: the image's identity, the archive snapshot
   its compiler toolchain came from, and the tool versions inside it.

   Every value here is a fact read off THAT image, or the word
   "unknown". Nothing falls back to the vaibify installed on this host
   — the installed Dockerfile describes the image vaibify would build
   today, which is a different question, and answering the second with
   the first would state an epoch the container never had.

   Public surface:
   - VaibifyEnvironmentInfo.fnShow()
       Fetches and opens the modal. Safe to call with no container
       connected; it says so rather than showing an empty table.
*/

var VaibifyEnvironmentInfo = (function () {
    "use strict";

    var _S_UNKNOWN_HTML =
        '<span class="env-info-unknown">unknown</span>';

    function _fsEscape(sText) {
        return VaibifyUtilities.fnEscapeHtml(sText || "");
    }

    function _fsValueHtml(sValue) {
        if (!sValue) return _S_UNKNOWN_HTML;
        return '<code>' + _fsEscape(sValue) + '</code>';
    }

    function _fsShortDigest(sDigest) {
        if (!sDigest) return "";
        var sBare = sDigest.replace(/^sha256:/, "");
        if (sBare.length <= 20) return sDigest;
        return sBare.slice(0, 20) + "…";
    }

    function _fsEpochHtml(sEpoch) {
        if (!sEpoch) {
            return _S_UNKNOWN_HTML +
                ' <span class="env-info-note">(this image was built ' +
                'before vaibify recorded the epoch)</span>';
        }
        // Rendered as a date because that is what it is; the stored
        // form is compact so it can ride in a Docker label.
        var sReadable = sEpoch.slice(0, 4) + "-" + sEpoch.slice(4, 6) +
            "-" + sEpoch.slice(6, 8);
        return '<code>' + _fsEscape(sReadable) + '</code>';
    }

    function _fsRowHtml(sLabel, sValueHtml) {
        return '<tr><th>' + _fsEscape(sLabel) + '</th>' +
            '<td>' + sValueHtml + '</td></tr>';
    }

    function _fsToolsRowsHtml(dictTools) {
        var listRows = [];
        var listKeys = Object.keys(dictTools || {}).sort();
        if (!listKeys.length) {
            return _fsRowHtml("Tools", _S_UNKNOWN_HTML +
                ' <span class="env-info-note">(the probe could not ' +
                'reach the container)</span>');
        }
        listKeys.forEach(function (sKey) {
            var value = dictTools[sKey];
            if (value && typeof value === "object") {
                value = value.sVersion || value.version || "";
            }
            listRows.push(_fsRowHtml(sKey, _fsValueHtml(value)));
        });
        return listRows.join("");
    }

    function _fsBodyHtml(dictInfo) {
        return '' +
            '<table class="env-info-table">' +
            '<tbody>' +
            '<tr class="env-info-section"><th colspan="2">' +
            'Container image</th></tr>' +
            _fsRowHtml("Digest",
                _fsValueHtml(_fsShortDigest(dictInfo.sImageDigest))) +
            _fsRowHtml("Image ID",
                _fsValueHtml(_fsShortDigest(dictInfo.sImageId))) +
            _fsRowHtml("Architecture",
                _fsValueHtml(dictInfo.sArchitecture)) +
            _fsRowHtml("Recipe fingerprint",
                _fsValueHtml(_fsShortDigest(
                    dictInfo.sRecipeFingerprint))) +
            '<tr class="env-info-section"><th colspan="2">' +
            'Compiler toolchain</th></tr>' +
            _fsRowHtml("Archive epoch",
                _fsEpochHtml(dictInfo.sToolchainEpoch)) +
            '<tr class="env-info-section"><th colspan="2">' +
            'Inside the container</th></tr>' +
            _fsToolsRowsHtml(dictInfo.dictSystemTools) +
            '<tr class="env-info-section"><th colspan="2">' +
            'This vaibify</th></tr>' +
            _fsRowHtml("Version",
                _fsValueHtml(dictInfo.sVaibifyVersion)) +
            '</tbody></table>' +
            '<p class="env-info-note">' +
            'The <strong>archive epoch</strong> is the date of the ' +
            'package archive this image\'s C compiler and libc were ' +
            'fetched from. It is pinned, so every image built from ' +
            'this recipe gets the same compiler whatever day it is ' +
            'built. It moves only when a maintainer moves it, and ' +
            'that is a deliberate change that asks you to re-run and ' +
            're-verify.' +
            '</p>' +
            '<p class="env-info-note">' +
            'The epoch covers the compiler only. Editors, LaTeX, the ' +
            'Python interpreter and pip packages track their upstreams ' +
            'and can differ between two builds of the same recipe. ' +
            'Pinning those for a result is what the project\'s ' +
            '<code>requirements.lock</code> is for.' +
            '</p>';
    }

    async function fnShow() {
        var sContainerId = VaibifyApp.fsGetContainerId();
        if (!sContainerId) {
            VaibifyModals.fnShowInfoModal(
                "Environment Info",
                '<p>No container is connected, so there is no ' +
                'environment to describe. Open a project first.</p>');
            return;
        }
        var dictInfo;
        try {
            dictInfo = await VaibifyApi.fdictGet(
                "/api/system/environment-info/" +
                encodeURIComponent(sContainerId));
        } catch (error) {
            // Never a half-filled table: a failed fetch is reported as
            // a failure, not as a container whose every field is
            // unknown, which reads as a claim about the container.
            VaibifyModals.fnShowInfoModal(
                "Environment Info",
                '<p>Could not read the environment: <code>' +
                _fsEscape(String(error && error.message || error)) +
                '</code></p>');
            return;
        }
        VaibifyModals.fnShowInfoModal(
            "Environment Info", _fsBodyHtml(dictInfo || {}));
    }

    return {
        fnShow: fnShow,
    };
})();
