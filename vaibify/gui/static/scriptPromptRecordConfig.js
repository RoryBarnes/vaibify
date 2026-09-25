/* Vaibify — Prompt Record settings (Replay axis "Recorded" state).

   Opened from the Project block's Prompt Record row ("Recording
   settings" / "Set up recording"). It holds one decision -- whether
   the in-container agent's sessions are recorded -- and says what
   recording does before it is switched on:

     - Turn on / off        POST .../prompt-record/configure

   Everything else the record offers lives where it is used: the
   sessions, their integrity and the first-capture approval in the
   record viewer (scriptPromptRecordViewer.js), and Supervised mode on
   its own row, switched through fnSetSupervision below. A settings
   dialog that also held the review and the watchdog read as the place
   to START recording even while it was already on.

   Exposes:
     - VaibifyPromptRecordConfig.fnOpen()
     - VaibifyPromptRecordConfig.fnClose()
     - VaibifyPromptRecordConfig.fnSetSupervision(bEnabled, elButton)
*/

var VaibifyPromptRecordConfig = (function () {
    "use strict";

    var fnEscapeHtml = VaibifyUtilities.fnEscapeHtml;

    function _felGet(sId) {
        return document.getElementById(sId);
    }

    function _fsRecordBase() {
        return "/api/workflow/" +
            encodeURIComponent(VaibifyApp.fsGetContainerId()) +
            "/prompt-record";
    }

    function fnOpen() {
        _felGet("modalPromptRecord").style.display = "flex";
        _fnRefresh();
    }

    function fnClose() {
        _felGet("modalPromptRecord").style.display = "none";
    }

    async function _fnRefresh() {
        if (!VaibifyApp.fsGetContainerId()) return;
        var elBody = _felGet("promptRecordBody");
        elBody.innerHTML = '<span class="placeholder">Loading…</span>';
        try {
            var dictStatus = await VaibifyApi.fdictGet(
                _fsRecordBase() + "/status");
            elBody.innerHTML = _fsRenderSettings(dictStatus);
            _fnBindSwitch(elBody);
        } catch (error) {
            elBody.innerHTML = '<div class="form-error">' +
                fnEscapeHtml(_fsDetail(error)) + '</div>';
        }
    }

    function _fsRenderSettings(dictStatus) {
        var bEnabled =
            (dictStatus.dictPromptRecord || {}).bEnabled === true;
        return '<p class="prompt-record-state"><strong>Recording is ' +
            (bEnabled ? 'on' : 'off') + '.</strong></p>' +
            _fsRenderCaptureRunning(dictStatus) +
            '<p class="muted-text">' + _S_WHAT_RECORDING_DOES + '</p>' +
            '<div class="modal-inline-actions">' +
            '<button type="button" class="btn' +
            (bEnabled ? '' : ' btn-primary') + '" ' +
            'data-record-switch="' + (bEnabled ? 'off' : 'on') + '">' +
            (bEnabled ? 'Turn off recording' : 'Turn on recording') +
            '</button></div>';
    }

    var _S_WHAT_RECORDING_DOES =
        'Recording is optional and never blocks a level. While it is ' +
        'on, the in-container agent’s session transcripts are ' +
        'copied into the repository as <em>redacted transcripts</em>: ' +
        'every capture is scanned, and known secrets are replaced with ' +
        'visible [REDACTED: …] markers before anything lands. Only ' +
        'sessions the agent started inside this project’s folder ' +
        'are captured; a session started elsewhere, such as the ' +
        'workspace root or another project, is left out. You review ' +
        'the first capture in the record viewer before it counts.';

    function _fsRenderCaptureRunning(dictStatus) {
        // A first pass over a long history takes minutes; saying
        // nothing all the while read as a stalled recorder.
        var sSince = dictStatus.sCaptureRunningSinceUtc || "";
        if (!sSince) return "";
        return '<p class="muted-text">A capture pass has been ' +
            'running since ' + fnEscapeHtml(sSince) + '. A first ' +
            'pass over a long history takes several minutes; its ' +
            'sessions appear in the record viewer when it finishes.</p>';
    }

    function _fnBindSwitch(elBody) {
        var elButton = elBody.querySelector("[data-record-switch]");
        if (!elButton) return;
        elButton.addEventListener("click", function () {
            _fnSetRecording(elButton.dataset.recordSwitch === "on");
        });
    }

    async function _fnSetRecording(bEnabled) {
        try {
            await VaibifyApi.fdictPost(_fsRecordBase() + "/configure", {
                bEnabled: bEnabled,
            });
            VaibifyApp.fnShowToast(bEnabled
                ? "Recording is on. The first capture starts within " +
                  "30 seconds; review it from the Prompt Record row."
                : "Recording is off.", "success");
            VaibifyPolling.fnStartFilePolling(
                VaibifyApp.fsGetContainerId());
            _fnRefresh();
        } catch (error) {
            VaibifyDiagnosis.fnReportFailure(
                "Changing the Prompt Record setting failed: " +
                VaibifyDiagnosis.fsExplainError(error));
        }
    }

    async function fnSetSupervision(bEnabled, elButton) {
        /* Supervised mode's own row calls this. The server refuses to
           turn it on before the record is enabled and its first capture
           reviewed, and the row says so before the button is offered;
           the refusal below is the backstop, reported in its own words. */
        var sContainerId = VaibifyApp.fsGetContainerId();
        if (!sContainerId) return;
        if (elButton) elButton.disabled = true;
        try {
            await VaibifyApi.fdictPost(
                "/api/workflow/" + encodeURIComponent(sContainerId) +
                    "/supervision/configure",
                {bEnabled: bEnabled},
            );
            VaibifyApp.fnShowToast(bEnabled
                ? "Supervised mode is on." : "Supervised mode is off.",
                "success");
            VaibifyPolling.fnStartFilePolling(sContainerId);
        } catch (error) {
            VaibifyDiagnosis.fnReportFailure(
                "Changing Supervised mode failed: " +
                VaibifyDiagnosis.fsExplainError(error));
        } finally {
            if (elButton) elButton.disabled = false;
        }
    }

    function _fsDetail(error) {
        if (!error) return "unknown error";
        if (typeof error === "string") return error;
        return error.message || "unknown error";
    }

    return {
        fnOpen: fnOpen,
        fnClose: fnClose,
        fnSetSupervision: fnSetSupervision,
    };
})();
