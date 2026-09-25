/* Vaibify — Prompt Record viewer and first-capture review.

   Opened from the Project block's Prompt Record row ("View Record",
   "Review & Approve"). It is where the researcher actually reads what
   capture landed in their repository, before and after approving it:

     GET  .../prompt-record/sessions              the list + integrity
     GET  .../prompt-record/sessions/{name}       one session, paged
     POST .../prompt-record/approve-first-capture the review gate

   Every turn is shown as the sanitizer left it, with each
   [REDACTED: …] marker highlighted so a reviewer can judge what was
   removed, and a filter narrows the view to turns that carry one.
   Tool calls, tool results, reasoning and context messages are folded
   to a single line; a folded turn that holds a redaction says so on
   that line. A line that is not valid JSON is shown raw, never hidden.

   Exposes:
     - VaibifyPromptRecordViewer.fnOpen()
     - VaibifyPromptRecordViewer.fnClose()
     - VaibifyPromptRecordViewer.fnBindViewer()   once, at startup
*/

var VaibifyPromptRecordViewer = (function () {
    "use strict";

    var fnEscapeHtml = VaibifyUtilities.fnEscapeHtml;
    var _RE_REDACTION = /\[REDACTED: [^\]]*\]/g;
    var _I_PAGE_TURNS = 300;

    var _DICT_TURN_LABELS = {
        prompt: "You",
        reply: "Agent",
        "tool-call": "Tool call",
        "tool-result": "Tool result",
        thinking: "Agent reasoning",
        context: "Context message",
        unparsed: "Unreadable line (not valid JSON, shown as stored)",
    };
    var _SET_FOLDED_KINDS = new Set([
        "tool-call", "tool-result", "thinking", "context",
    ]);

    var _dictViewer = {
        dictSessions: null,
        sSelectedSession: "",
        listTurns: [],
        dictPage: null,
    };

    function _felGet(sId) {
        return document.getElementById(sId);
    }

    function _fsRecordBase() {
        return "/api/workflow/" +
            encodeURIComponent(VaibifyApp.fsGetContainerId()) +
            "/prompt-record";
    }

    function fnOpen() {
        _felGet("modalPromptRecordViewer").style.display = "flex";
        _fnLoadSessions();
    }

    function fnClose() {
        _felGet("modalPromptRecordViewer").style.display = "none";
    }

    async function _fnLoadSessions() {
        var elHeader = _felGet("promptRecordViewerHeader");
        elHeader.innerHTML = '<span class="placeholder">Loading…</span>';
        _felGet("promptRecordViewerTurns").innerHTML = "";
        try {
            _dictViewer.dictSessions = await VaibifyApi.fdictGet(
                _fsRecordBase() + "/sessions");
        } catch (error) {
            VaibifyDiagnosis.fnRenderFailureInline(
                elHeader, "Could not read the Prompt Record: ", error);
            return;
        }
        _fnRenderSessionsAndHeader();
        var listSessions = _dictViewer.dictSessions.listSessions || [];
        if (listSessions.length > 0) {
            _fnSelectSession(listSessions[0].sSessionFileName);
        }
    }

    function _fnRenderSessionsAndHeader() {
        var dictSessions = _dictViewer.dictSessions;
        _felGet("promptRecordViewerHeader").innerHTML =
            _fsRenderHeader(dictSessions);
        _felGet("promptRecordViewerSessions").innerHTML =
            (dictSessions.listSessions || []).map(
                _fsRenderSessionButton).join("") ||
            '<p class="muted-text">No sessions captured yet.</p>';
        _felGet("promptRecordViewerApprove").innerHTML =
            _fsRenderApproval(dictSessions);
    }

    function _fsRenderHeader(dictSessions) {
        var listSessions = dictSessions.listSessions || [];
        var iRedactions = listSessions.reduce(function (iSum, dictItem) {
            return iSum + (dictItem.iRedactionCount || 0);
        }, 0);
        var listLines = [
            listSessions.length + " session(s) · " + iRedactions +
                " redaction(s)",
        ];
        if ((dictSessions.iSessionsOutsideProject || 0) > 0) {
            listLines.push(dictSessions.iSessionsOutsideProject +
                " session(s) started outside this project’s folder " +
                "are not recorded.");
        }
        var listIntervals = dictSessions.listCoverageIntervals || [];
        if (listIntervals.length > 1) {
            listLines.push("Coverage has " + (listIntervals.length - 1) +
                " gap(s): time between recorded intervals was not " +
                "monitored.");
        }
        return '<p>' + listLines.map(fnEscapeHtml).join('<br>') + '</p>' +
            _fsRenderIntegrity(dictSessions);
    }

    function _fsRenderIntegrity(dictSessions) {
        var listProblems = [];
        if (dictSessions.bChainIntact !== true) {
            listProblems.push("The capture hash chain is BROKEN — a " +
                "capture record was edited or removed.");
        }
        (dictSessions.listTamperedSessions || []).forEach(function (s) {
            listProblems.push("Session file changed after capture: " + s);
        });
        if (listProblems.length === 0) {
            return '<p class="muted-text">Integrity: the hash chain is ' +
                'intact and every session file matches its capture.</p>';
        }
        return '<div class="form-error">' +
            listProblems.map(fnEscapeHtml).join('<br>') + '</div>';
    }

    function _fsRenderSessionButton(dictSession) {
        var bSelected =
            dictSession.sSessionFileName === _dictViewer.sSelectedSession;
        return '<button type="button" class="prompt-record-session' +
            (bSelected ? ' selected' : '') + '" data-session="' +
            fnEscapeHtml(dictSession.sSessionFileName) + '">' +
            '<span class="prompt-record-session-id">' +
            fnEscapeHtml(_fsShortSessionName(dictSession.sSessionFileName)) +
            '</span><span class="muted-text">' + fnEscapeHtml(
                "captured " + (dictSession.sLastCapturedAtUtc || "?")
                    .slice(0, 16).replace("T", " ") + " · " +
                (dictSession.iRedactionCount || 0) + " redaction(s) · " +
                ((dictSession.iBytesCaptured || 0) / 1e6).toFixed(1) +
                " MB") + '</span></button>';
    }

    function _fsShortSessionName(sFileName) {
        // "-workspace-project__<uuid>.jsonl" -> "<first 8 of uuid>"
        var sStem = String(sFileName).replace(/\.jsonl$/, "");
        var sId = sStem.split("__").pop();
        return "Session " + sId.slice(0, 8);
    }

    function _fsRenderApproval(dictSessions) {
        if (dictSessions.bFirstCaptureReviewed === true) {
            return '<p class="muted-text">First capture reviewed and ' +
                'approved.</p>';
        }
        if ((dictSessions.listSessions || []).length === 0) return "";
        return '<p class="muted-text">Approving says you have read the ' +
            'redacted sessions and are content for them to be ' +
            'published with the project. The scanner cannot catch prose ' +
            'you consider private.</p>' +
            '<button type="button" class="btn btn-primary" ' +
            'data-viewer-action="approve">Approve first capture</button>';
    }

    async function _fnSelectSession(sSessionFileName) {
        _dictViewer.sSelectedSession = sSessionFileName;
        _dictViewer.listTurns = [];
        _dictViewer.dictPage = null;
        _fnRenderSessionsAndHeader();
        _felGet("promptRecordViewerTurns").innerHTML =
            '<span class="placeholder">Loading…</span>';
        await _fnLoadNextPage();
    }

    async function _fnLoadNextPage() {
        var sSession = _dictViewer.sSelectedSession;
        var elTurns = _felGet("promptRecordViewerTurns");
        try {
            var dictPage = await VaibifyApi.fdictGet(
                _fsRecordBase() + "/sessions/" +
                encodeURIComponent(sSession) + "?iOffset=" +
                _dictViewer.listTurns.length + "&iLimit=" + _I_PAGE_TURNS);
            if (sSession !== _dictViewer.sSelectedSession) return;
            _dictViewer.dictPage = dictPage;
            _dictViewer.listTurns = _dictViewer.listTurns.concat(
                dictPage.listTurns || []);
            _fnRenderTurns();
        } catch (error) {
            VaibifyDiagnosis.fnRenderFailureInline(
                elTurns, "Could not read this session: ", error);
        }
    }

    function _fnRenderTurns() {
        var dictPage = _dictViewer.dictPage || {};
        var bOnlyRedactions = _felGet("checkPromptRecordRedactionsOnly")
            .checked;
        var listShown = _dictViewer.listTurns.filter(function (dictTurn) {
            return !bOnlyRedactions || dictTurn.bRedacted;
        });
        var bMore = _dictViewer.listTurns.length < (dictPage.iTurnCount || 0);
        _felGet("promptRecordViewerTurns").innerHTML =
            _fsRenderSessionSummary(dictPage) +
            (listShown.map(_fsRenderTurn).join("") ||
                '<p class="muted-text">No turns to show.</p>') +
            (bMore ? '<button type="button" class="btn" ' +
                'data-viewer-action="more">Load more (' +
                _dictViewer.listTurns.length + ' of ' +
                dictPage.iTurnCount + ')</button>' : '');
    }

    function _fsRenderSessionSummary(dictPage) {
        return '<p class="muted-text">' + fnEscapeHtml(
            (dictPage.iPromptCount || 0) + " prompt(s), " +
            (dictPage.iTurnCount || 0) + " turn(s), " +
            (dictPage.sFirstTimestampUtc || "?").slice(0, 16)
                .replace("T", " ") + " to " +
            (dictPage.sLastTimestampUtc || "?").slice(0, 16)
                .replace("T", " ") + " UTC. " +
            (dictPage.iRecordsWithoutConversation || 0) +
            " record(s) carry no conversation (mode changes, file " +
            "snapshots, titles) and are not shown.") + '</p>';
    }

    function _fsRenderTurn(dictTurn) {
        var sLabel = _DICT_TURN_LABELS[dictTurn.sKind] || dictTurn.sKind;
        if (dictTurn.sToolName) sLabel += " · " + dictTurn.sToolName;
        var sBody = '<div class="prompt-record-turn-text">' +
            _fsHighlightRedactions(dictTurn.sText) +
            (dictTurn.bTruncated ? '<p class="muted-text">… ' +
                dictTurn.iCharacters + ' characters; the first ' +
                dictTurn.sText.length + ' are shown.</p>' : '') + '</div>';
        var sClass = 'prompt-record-turn prompt-record-turn-' +
            fnEscapeHtml(dictTurn.sKind) +
            (dictTurn.bRedacted ? ' has-redaction' : '');
        if (!_SET_FOLDED_KINDS.has(dictTurn.sKind)) {
            return '<div class="' + sClass + '"><div class="' +
                'prompt-record-turn-label">' + fnEscapeHtml(sLabel) +
                '</div>' + sBody + '</div>';
        }
        return '<details class="' + sClass + '"><summary>' +
            fnEscapeHtml(sLabel + " · " + _fsFirstLine(dictTurn.sText)) +
            (dictTurn.bRedacted ? ' <span class="prompt-record-' +
                'redaction-badge">contains a redaction</span>' : '') +
            '</summary>' + sBody + '</details>';
    }

    function _fsFirstLine(sText) {
        var sLine = String(sText || "").split("\n")[0];
        return sLine.length > 100 ? sLine.slice(0, 100) + "…" : sLine;
    }

    function _fsHighlightRedactions(sText) {
        return fnEscapeHtml(sText || "").replace(_RE_REDACTION,
            function (sMarker) {
                return '<mark class="prompt-record-redaction">' + sMarker +
                    '</mark>';
            });
    }

    async function _fnApprove(elButton) {
        elButton.disabled = true;
        try {
            await VaibifyApi.fdictPost(
                _fsRecordBase() + "/approve-first-capture", {});
            VaibifyApp.fnShowToast("First capture approved.", "success");
            _dictViewer.dictSessions.bFirstCaptureReviewed = true;
            _fnRenderSessionsAndHeader();
            VaibifyPolling.fnStartFilePolling(VaibifyApp.fsGetContainerId());
        } catch (error) {
            elButton.disabled = false;
            VaibifyDiagnosis.fnReportFailure("Approving the first " +
                "capture failed: " + VaibifyDiagnosis.fsExplainError(error));
        }
    }

    function _fnHandleClick(event) {
        var elSession = event.target.closest("[data-session]");
        if (elSession) {
            _fnSelectSession(elSession.dataset.session);
            return;
        }
        var elAction = event.target.closest("[data-viewer-action]");
        if (!elAction) return;
        if (elAction.dataset.viewerAction === "more") _fnLoadNextPage();
        if (elAction.dataset.viewerAction === "approve") {
            _fnApprove(elAction);
        }
    }

    function fnBindViewer() {
        var elModal = _felGet("modalPromptRecordViewer");
        if (!elModal) return;
        elModal.addEventListener("click", _fnHandleClick);
        _felGet("checkPromptRecordRedactionsOnly").addEventListener(
            "change", _fnRenderTurns);
    }

    return {
        fnOpen: fnOpen,
        fnClose: fnClose,
        fnBindViewer: fnBindViewer,
    };
})();
