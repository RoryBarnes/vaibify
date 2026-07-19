/* Vaibify — Prompt Record modal (Replay axis "Recorded" state).

   Opened from the Project block's AI Model / Prompts row. Shows the
   live record state from GET .../prompt-record/status and offers:

     - Enable / Disable      POST .../prompt-record/configure
     - Approve first capture POST .../prompt-record/approve-first-capture
       (the human review gate: the researcher inspects the sanitized
       sample — with its visible [REDACTED: …] markers — before the
       record is treated as publishable)

   Honesty rules baked into the rendering: everything is labeled a
   *redacted transcript* (never raw tokens), redaction counts are
   shown per category, coverage intervals are listed so gaps between
   them read as unmonitored time, and a broken hash chain or tampered
   session file renders as a loud warning, never suppressed.

   Exposes:
     - VaibifyPromptRecordConfig.fnOpen()
     - VaibifyPromptRecordConfig.fnClose()
*/

var VaibifyPromptRecordConfig = (function () {
    "use strict";

    var fnEscapeHtml = VaibifyUtilities.fnEscapeHtml;

    function _felGet(sId) {
        return document.getElementById(sId);
    }

    function fnOpen() {
        _felGet("modalPromptRecord").style.display = "flex";
        _fnRefresh();
    }

    function fnClose() {
        _felGet("modalPromptRecord").style.display = "none";
    }

    async function _fnRefresh() {
        var sContainerId = PipeleyenApp.fsGetContainerId();
        if (!sContainerId) return;
        var elBody = _felGet("promptRecordBody");
        elBody.innerHTML = '<span class="placeholder">Loading…</span>';
        try {
            var dictStatus = await VaibifyApi.fdictGet(
                "/api/workflow/" + encodeURIComponent(sContainerId) +
                    "/prompt-record/status",
            );
            elBody.innerHTML = _fsRenderStatus(dictStatus);
            _fnBindBodyActions(elBody, dictStatus);
        } catch (error) {
            elBody.innerHTML = '<div class="form-error">' +
                fnEscapeHtml(_fsDetail(error)) + '</div>';
        }
    }

    function _fsRenderStatus(dictStatus) {
        var dictRecord = dictStatus.dictPromptRecord || {};
        if (dictRecord.bEnabled !== true) {
            return _fsRenderDisabledState();
        }
        return _fsRenderIntegrity(dictStatus) +
            _fsRenderCaptures(dictStatus) +
            _fsRenderCoverage(dictStatus) +
            _fsRenderReviewGate(dictStatus) +
            '<div class="modal-inline-actions">' +
            '<button type="button" class="btn" ' +
            'data-record-action="disable">Disable recording</button>' +
            '</div>';
    }

    function _fsRenderDisabledState() {
        return '<p class="muted-text">The Prompt Record is off ' +
            '(optional — never blocks a level). When enabled, the ' +
            'in-container agent’s session transcripts are ' +
            'copied into the repository as <em>redacted ' +
            'transcripts</em>: every capture is scanned and known ' +
            'secrets are replaced with visible [REDACTED: …] ' +
            'markers before anything lands. You review the first ' +
            'capture before it counts.</p>' +
            '<div class="modal-inline-actions">' +
            '<button type="button" class="btn btn-primary" ' +
            'data-record-action="enable">Enable recording</button>' +
            '</div>';
    }

    function _fsRenderIntegrity(dictStatus) {
        var listWarnings = [];
        if (dictStatus.bChainIntact !== true) {
            listWarnings.push(
                "The capture hash chain is BROKEN — a capture " +
                "record was edited or removed.");
        }
        (dictStatus.listTamperedSessions || []).forEach(
            function (sName) {
                listWarnings.push(
                    "Session file modified after capture: " + sName);
            });
        if (listWarnings.length === 0) {
            return '<p class="muted-text">Record integrity: hash ' +
                'chain intact, session files match their capture ' +
                'hashes.</p>';
        }
        return '<div class="form-error">' + listWarnings.map(
            fnEscapeHtml,
        ).join("<br>") + '</div>';
    }

    function _fsRenderCaptures(dictStatus) {
        var listCaptures = dictStatus.listCaptures || [];
        if (listCaptures.length === 0) {
            return '<p class="muted-text">No captures yet — the ' +
                'next capture pass runs within 30 seconds while a ' +
                'workflow is open.</p>';
        }
        var dictCounts = {};
        listCaptures.forEach(function (dictRecord) {
            var dictByCategory =
                dictRecord.dictRedactionsByCategory || {};
            Object.keys(dictByCategory).forEach(function (sCategory) {
                dictCounts[sCategory] = (dictCounts[sCategory] || 0) +
                    dictByCategory[sCategory];
            });
        });
        var sRedactions = Object.keys(dictCounts).sort().map(
            function (sCategory) {
                return fnEscapeHtml(
                    sCategory + ": " + dictCounts[sCategory]);
            }).join(", ") || "none";
        return '<p>' + listCaptures.length + ' capture(s) on ' +
            'record. Redactions — ' + sRedactions + '.</p>';
    }

    function _fsRenderCoverage(dictStatus) {
        var listIntervals = dictStatus.listCoverageIntervals || [];
        if (listIntervals.length === 0) return "";
        var sRows = listIntervals.map(function (dictInterval) {
            return '<li>' + fnEscapeHtml(
                dictInterval.sStartUtc + " → " +
                dictInterval.sEndUtc) + '</li>';
        }).join("");
        var sGapNote = listIntervals.length > 1
            ? '<p class="form-error">Time between intervals was NOT ' +
              'monitored — those prompts are not in the record.</p>'
            : "";
        return '<p>Recorded intervals (everything outside them is ' +
            'unmonitored):</p><ul>' + sRows + '</ul>' + sGapNote;
    }

    function _fsRenderReviewGate(dictStatus) {
        var dictRecord = dictStatus.dictPromptRecord || {};
        if (dictRecord.bFirstCaptureReviewed === true) {
            return '<p class="muted-text">First capture reviewed ' +
                'and approved.</p>';
        }
        if ((dictStatus.listCaptures || []).length === 0) {
            return '<p class="muted-text">The review gate opens ' +
                'after the first capture lands.</p>';
        }
        return '<p><strong>Review gate:</strong> inspect this ' +
            'sample of the redacted transcript (the scanner cannot ' +
            'catch prose you consider private — read before ' +
            'approving):</p>' +
            '<pre class="prompt-record-sample">' +
            fnEscapeHtml(dictStatus.sReviewSample || "") + '</pre>' +
            '<div class="modal-inline-actions">' +
            '<button type="button" class="btn btn-primary" ' +
            'data-record-action="approve">Approve first capture' +
            '</button></div>';
    }

    function _fnBindBodyActions(elBody, dictStatus) {
        elBody.querySelectorAll("[data-record-action]").forEach(
            function (elButton) {
                elButton.addEventListener("click", function () {
                    _fnRunAction(elButton.dataset.recordAction);
                });
            });
    }

    async function _fnRunAction(sAction) {
        var sContainerId = PipeleyenApp.fsGetContainerId();
        if (!sContainerId) return;
        var sBase = "/api/workflow/" +
            encodeURIComponent(sContainerId) + "/prompt-record";
        try {
            if (sAction === "enable" || sAction === "disable") {
                await VaibifyApi.fdictPost(sBase + "/configure", {
                    bEnabled: sAction === "enable",
                });
            } else if (sAction === "approve") {
                await VaibifyApi.fdictPost(
                    sBase + "/approve-first-capture", {},
                );
            }
            _fnRefresh();
        } catch (error) {
            PipeleyenApp.fnShowToast(
                "Prompt Record action failed: " + _fsDetail(error),
                "error");
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
    };
})();
