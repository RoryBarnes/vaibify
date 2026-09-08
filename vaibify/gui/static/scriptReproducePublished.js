/* Vaibify — Reproduce a published project (the hub's one-shot job) */

var VaibifyReproducePublished = (function () {
    "use strict";

    /*
     * One modal, four stages, in order: the source form, the
     * confirmation card, the progress card, the result. The order is
     * the contract -- no run request leaves this module until the
     * researcher has read the confirmation and clicked Run -- and the
     * progress poll is armed only while the server reports the job
     * live, and disarmed on settle, so a finished job never pulses.
     *
     * Nothing here offers a publish, deposit, push or attest action:
     * a reproduction is the reproducer's own record, and the verdict
     * vocabulary is the report's -- reproduced, reproduced under
     * emulation, diverged, no verdict.
     */

    var fnEscapeHtml = VaibifyUtilities.fnEscapeHtml;
    var _I_POLL_MILLISECONDS = 2000;
    var _sJobId = "";
    var _iPollTimer = 0;
    var _iPollFailures = 0;
    var _I_POLL_FAILURES_TOLERATED = 3;
    var _dictStaged = null;
    var _bRunStarted = false;

    function _felById(sId) {
        return document.getElementById(sId);
    }

    function _fnShowStage(sStage) {
        ["Source", "Confirm", "Progress", "Result"].forEach(function (sName) {
            var el = _felById("reproduceStage" + sName);
            if (el) el.style.display = sName === sStage ? "" : "none";
        });
    }

    function fnOpen() {
        _sJobId = "";
        _dictStaged = null;
        _bRunStarted = false;
        _iPollFailures = 0;
        _fnDisarmPoll();
        _felById("reproduceSourceInput").value = "";
        _felById("reproduceWorkflowSelect").style.display = "none";
        _felById("reproduceWorkflowSelect").innerHTML = "";
        _felById("reproduceSourceError").textContent = "";
        _fnShowStage("Source");
        _felById("modalReproducePublished").style.display = "flex";
    }

    function fnClose() {
        /* Hiding the modal used to leave the staged clone on disk: a
           staged job holds its own lock, which is what keeps the
           staging sweep off it, so an abandoned confirmation kept a
           whole repository until the hub restarted (found by review,
           2026-09-07). Dismissing the card discards it. A RUNNING job
           is left alone -- it owns a shadow container and discards its
           own staging when it settles -- and the request is
           best-effort, because the server expires it either way. */
        _fnDisarmPoll();
        var sJobId = _sJobId;
        var bRunning = _bRunStarted;
        _sJobId = "";
        _felById("modalReproducePublished").style.display = "none";
        if (sJobId && !bRunning) {
            VaibifyApi.fdictPost(
                "/api/reproductions/" + encodeURIComponent(sJobId)
                + "/discard", {}).catch(function () {});
        }
    }

    /* ---------------- stage 1: the source ---------------- */

    async function _fnStage() {
        var sSource = (_felById("reproduceSourceInput").value || "").trim();
        var elSelect = _felById("reproduceWorkflowSelect");
        var sWorkflowName = elSelect.style.display === "none"
            ? "" : (elSelect.value || "");
        var elError = _felById("reproduceSourceError");
        elError.textContent = "";
        if (!sSource) {
            elError.textContent = "Enter a clone URL or the path of a " +
                "clean clone under your home directory.";
            return;
        }
        var elButton = _felById("btnReproduceStage");
        elButton.disabled = true;
        elButton.textContent = "Staging…";
        try {
            var dictResponse = await VaibifyApi.fdictPost(
                "/api/reproductions/stage",
                {sSource: sSource, sWorkflowName: sWorkflowName});
            if (dictResponse.bWorkflowSelectionRequired) {
                _fnOfferWorkflowChoice(dictResponse.listWorkflowNames || []);
                return;
            }
            _sJobId = dictResponse.sJobId;
            _dictStaged = dictResponse;
            _fnRenderConfirmation(dictResponse);
            _fnShowStage("Confirm");
        } catch (error) {
            elError.textContent = error.message || String(error);
        } finally {
            elButton.disabled = false;
            elButton.textContent = "Stage";
        }
    }

    function _fnOfferWorkflowChoice(listNames) {
        /* The snapshot hosts several workflows and the server refused
           to pick by sort order; the researcher picks, then stages
           again with the name. */
        var elSelect = _felById("reproduceWorkflowSelect");
        elSelect.innerHTML = listNames.map(function (sName) {
            return '<option value="' + fnEscapeHtml(sName) + '">' +
                fnEscapeHtml(sName) + '</option>';
        }).join("");
        elSelect.style.display = "";
        _felById("reproduceSourceError").textContent =
            "This repository holds " + listNames.length + " workflows. " +
            "Choose the one to reproduce, then stage again.";
    }

    /* ---------------- stage 2: the confirmation ---------------- */

    function _fsFactRow(sLabel, sValue) {
        return '<tr><th>' + fnEscapeHtml(sLabel) + '</th><td>' +
            fnEscapeHtml(sValue) + '</td></tr>';
    }

    function _fsFactRowHtml(sLabel, sHtmlValue) {
        /* The one row whose value is markup this module built itself
           (the report link). Every OTHER value goes through
           _fsFactRow, which escapes: values from a report describe a
           stranger's repository. */
        return '<tr><th>' + fnEscapeHtml(sLabel) + '</th><td>' +
            sHtmlValue + '</td></tr>';
    }

    function _fnRenderConfirmation(dictResponse) {
        var dictStaged = dictResponse.dictStaged || {};
        var dictDaemon = dictResponse.dictDaemon || {};
        var bMatches = dictDaemon.bArchitectureMatches === true;
        var sDaemon = dictDaemon.bReachable
            ? (dictDaemon.sArchitecture || "unknown")
            : "no Docker daemon reachable";
        _felById("reproduceConfirmFacts").innerHTML =
            _fsFactRow("Project", dictStaged.sRepositoryName || "") +
            _fsFactRow("Workflow", dictStaged.sWorkflowName || "") +
            _fsFactRow("Commit", dictStaged.sResolvedCommit || "") +
            _fsFactRow("Remote", dictStaged.sRemoteUrl || "(none)") +
            _fsFactRow("Pinned image", dictStaged.sPinnedImageReference || "") +
            _fsFactRow("Required platform", dictStaged.sRequiredPlatform || "") +
            _fsFactRow("This daemon", sDaemon +
                (dictDaemon.bReachable
                    ? (bMatches ? " (matches)" : " (does not match)")
                    : "")) +
            _fsFactRow("Image archive", dictStaged.bDepositOnRecord
                ? "deposited, version DOI " +
                    (dictStaged.sDepositVersionDoi || "")
                : "no deposit on record");
        _felById("reproduceChainLinks").innerHTML =
            (dictResponse.listChainLinks || []).map(function (sLink) {
                return "<li>" + fnEscapeHtml(sLink) + "</li>";
            }).join("");
        var elEmulation = _felById("reproduceEmulationRow");
        elEmulation.style.display = (dictDaemon.bReachable && !bMatches)
            ? "" : "none";
        _felById("reproduceEmulationCheckbox").checked = false;
        _felById("btnReproduceRun").disabled = !dictDaemon.bReachable;
        _felById("reproduceConfirmError").textContent = dictDaemon.bReachable
            ? "" : "No Docker daemon is reachable; the run needs one.";
    }

    async function _fnRun() {
        /* The one request that spends anything, sent only from the
           confirmation stage's own button. */
        if (!_sJobId) return;
        var elError = _felById("reproduceConfirmError");
        elError.textContent = "";
        var bAllowEmulation = _felById("reproduceEmulationCheckbox").checked;
        try {
            await VaibifyApi.fdictPost(
                "/api/reproductions/" + encodeURIComponent(_sJobId) + "/run",
                {bAllowEmulation: bAllowEmulation});
        } catch (error) {
            elError.textContent = error.message || String(error);
            return;
        }
        _bRunStarted = true;
        _fnShowStage("Progress");
        _fnRenderProgress({sPhase: "pulling", bLive: true});
        _fnArmPoll();
    }

    /* ---------------- stage 3: the progress ---------------- */

    function _fnArmPoll() {
        if (_iPollTimer) return;
        _iPollTimer = window.setInterval(_fnPollOnce, _I_POLL_MILLISECONDS);
    }

    function _fnDisarmPoll() {
        if (!_iPollTimer) return;
        window.clearInterval(_iPollTimer);
        _iPollTimer = 0;
    }

    function _fnHandlePollFailure(error) {
        /* A poll that cannot answer must eventually STOP. A 404 means
           the job is gone -- jobs live only as long as the hub, so a
           restart loses one -- and there is nothing left to wait for;
           anything else may be a moment's network trouble, so a few
           are tolerated before the card gives up. Either way the
           spinner stops and says which happened, rather than pulsing
           at a server that will never answer (found by review,
           2026-09-07). */
        var bGone = error && error.iStatus === 404;
        _iPollFailures += 1;
        if (!bGone && _iPollFailures < _I_POLL_FAILURES_TOLERATED) {
            _fnRenderProgress({sPhase: "unreachable", bLive: true,
                sFailure: error.message || String(error)});
            return;
        }
        _fnDisarmPoll();
        _fnRenderResult({sFailure: bGone
            ? "This hub is no longer holding that reproduction. Jobs "
                + "live only as long as the hub that started them; the "
                + "report, if the run reached one, is still on disk."
            : "The hub stopped answering: "
                + (error.message || String(error))});
        _fnShowStage("Result");
    }

    async function _fnPollOnce() {
        if (!_sJobId) { _fnDisarmPoll(); return; }
        var dictJob;
        try {
            dictJob = await VaibifyApi.fdictGet(
                "/api/reproductions/" + encodeURIComponent(_sJobId));
            _iPollFailures = 0;
        } catch (error) {
            _fnHandlePollFailure(error);
            return;
        }
        if (dictJob.bLive) {
            _fnRenderProgress(dictJob);
            return;
        }
        // Armed and disarmed purely from what the server reports: a
        // settled job never pulses, and a hub that lost the job does
        // not leave the card polling forever.
        _fnDisarmPoll();
        _fnRenderResult(dictJob);
        _fnShowStage("Result");
        _felById("modalReproducePublished").style.display = "flex";
    }

    function _fsFormatBytes(iBytes) {
        if (iBytes >= 1024 * 1024 * 1024) {
            return (iBytes / (1024 * 1024 * 1024)).toFixed(2) + " GB";
        }
        if (iBytes >= 1024 * 1024) {
            return (iBytes / (1024 * 1024)).toFixed(1) + " MB";
        }
        return Math.round(iBytes / 1024) + " KB";
    }

    function _fsDescribePhase(dictJob) {
        var sPhase = dictJob.sPhase || "";
        if (sPhase === "pulling") return "Pulling the pinned image from " +
            "the registry…";
        if (sPhase === "downloading") {
            var iTotal = dictJob.iTotalBytes || 0;
            return "Downloading the archived deposit" + (iTotal
                ? " (" + _fsFormatBytes(dictJob.iBytes || 0) + " of " +
                    _fsFormatBytes(iTotal) + ")" : "") + "…";
        }
        if (sPhase === "loading") return "Loading the archived image " +
            "into the daemon…";
        if (sPhase === "running") return "Re-running the workflow in a " +
            "shadow container" + (dictJob.sStepLabel
                ? " — step " + dictJob.sStepLabel +
                    (dictJob.sStepName ? " (" + dictJob.sStepName + ")" : "")
                : "") + "…";
        if (sPhase === "finishing") return "Comparing the bytes inside " +
            "the shadow and destroying it…";
        if (sPhase === "unreachable") return "Cannot reach the hub: " +
            (dictJob.sFailure || "");
        return sPhase + "…";
    }

    function _fnRenderProgress(dictJob) {
        _felById("reproduceProgressPhase").textContent =
            _fsDescribePhase(dictJob);
        var listAttempts = dictJob.listAttempts || [];
        _felById("reproduceProgressAttempts").innerHTML =
            listAttempts.map(function (dictAttempt) {
                return "<li>" + fnEscapeHtml(dictAttempt.sLink || "") + ": " +
                    (dictAttempt.bSucceeded ? "served" : "failed" +
                        (dictAttempt.sDetail
                            ? " (" + fnEscapeHtml(dictAttempt.sDetail) + ")"
                            : "")) + "</li>";
            }).join("");
    }

    /* ---------------- stage 4: the result ---------------- */

    function _fsReportLink(dictReport) {
        /* The report is a file this hub can serve, so the row is a
           link to it rather than an id and a directory to go and find
           by hand. */
        var sReportId = dictReport.sReportId || "";
        if (!sReportId) return "";
        return '<a href="/api/reproductions/reports/'
            + encodeURIComponent(sReportId) + '" target="_blank" '
            + 'rel="noopener">' + fnEscapeHtml(sReportId) + "</a>";
    }

    function _fsRenderVerdict(dictReport) {
        return dictReport.sVerdictRendered || dictReport.sVerdict || "";
    }

    function _fnRenderResult(dictJob) {
        var dictReport = dictJob.dictReport || null;
        var elVerdict = _felById("reproduceResultVerdict");
        var elBody = _felById("reproduceResultBody");
        if (!dictReport) {
            elVerdict.textContent = "no verdict";
            elVerdict.className = "reproduce-verdict reproduce-verdict-none";
            elBody.innerHTML = "<p>" + fnEscapeHtml(dictJob.sFailure ||
                "The job ended without a report.") + "</p>";
            return;
        }
        var sVerdict = _fsRenderVerdict(dictReport);
        elVerdict.textContent = sVerdict;
        elVerdict.className = "reproduce-verdict reproduce-verdict-" +
            (dictReport.sVerdict === "reproduced" ? "reproduced"
                : dictReport.sVerdict === "diverged" ? "diverged" : "none");
        var dictPlatform = dictReport.dictPlatform || {};
        var listCarried = dictReport.listCarriedPaths || [];
        elBody.innerHTML =
            '<table class="reproduce-facts">' +
            _fsFactRow("Outputs matching", dictReport.iOutputHashesMatched +
                " of " + dictReport.iOutputHashesTotal + " re-derived" +
                (listCarried.length
                    ? "; " + listCarried.length + " carried in unchanged " +
                        "(produced by a step a human runs)"
                    : "")) +
            _fsFactRow("Image obtained from", dictReport.sObtainedFrom || "") +
            _fsFactRow("Image run", dictReport.sImageReferenceRun || "") +
            _fsFactRow("Required platform", dictPlatform.sRequiredPlatform || "") +
            _fsFactRow("Obtained platform", dictPlatform.sObtainedPlatform || "") +
            _fsFactRow("Daemon architecture",
                (dictPlatform.sDaemonArchitecture || "") +
                (dictPlatform.bEmulated ? " (emulated)" : "")) +
            _fsFactRow("Deposit re-check",
                (dictReport.dictImageRecheck || {}).bVacuous
                    ? "vacuous (the image was loaded from the deposit)"
                    : ((dictReport.dictImageRecheck || {}).sVerdict ||
                        "not compared")) +
            _fsFactRowHtml("Report", _fsReportLink(dictReport)) +
            "</table>" +
            _fsRenderFileTable(dictReport, listCarried) +
            _fsRenderFailure(dictReport.dictRerunFailure || {}) +
            "<p class=\"muted-text\">This report is yours, not the " +
            "author's attestation; nothing was written into the project, " +
            "and the shadow container was " +
            fnEscapeHtml(dictReport.sShadowTeardown || "destroyed") + ".</p>";
    }

    function _fsRenderFileTable(dictReport, listCarried) {
        /* Every pinned file, not only the unhappy ones: a ratio is a
           claim about a set the reader cannot see, and "which files"
           is what somebody deciding whether to trust a result is
           asking. Diverged first, because that is what they act on. */
        var listDiverged = dictReport.listDivergedHashes || [];
        var listMatched = dictReport.listMatchedPaths || [];
        if (!listDiverged.length && !listCarried.length
                && !listMatched.length) {
            return "";
        }
        var sRows = listDiverged.map(function (sPath) {
            return "<tr><td>" + fnEscapeHtml(sPath) +
                "</td><td>diverged</td></tr>";
        }).join("") + listCarried.map(function (sPath) {
            return "<tr><td>" + fnEscapeHtml(sPath) +
                "</td><td>carried in unchanged</td></tr>";
        }).join("") + listMatched.map(function (sPath) {
            return "<tr><td>" + fnEscapeHtml(sPath) +
                "</td><td>re-derived, byte-identical</td></tr>";
        }).join("");
        return '<table class="reproduce-files"><thead><tr><th>File</th>' +
            "<th>Outcome</th></tr></thead><tbody>" + sRows +
            "</tbody></table>";
    }

    function _fsRenderFailure(dictFailure) {
        if (!dictFailure || !Object.keys(dictFailure).length) return "";
        var sHead = dictFailure.sKind === "preflight"
            ? "The run was stopped before any step started."
            : "Step " + fnEscapeHtml(dictFailure.sStepLabel || "?") +
                " stopped with error code " +
                fnEscapeHtml(String(dictFailure.iExitCode));
        var listLines = dictFailure.listOutputTail || dictFailure.listErrors
            || [];
        return "<p>" + sHead + "</p><pre class=\"reproduce-tail\">" +
            fnEscapeHtml(listLines.join("\n")) + "</pre>";
    }

    /* ---------------- wiring ---------------- */

    function fnBind() {
        _felById("btnReproduceStage").addEventListener("click", _fnStage);
        _felById("btnReproduceRun").addEventListener("click", _fnRun);
        ["btnReproduceCancelSource", "btnReproduceCancelConfirm",
         "btnReproduceCloseResult"].forEach(function (sId) {
            _felById(sId).addEventListener("click", fnClose);
        });
        _felById("btnReproduceHideProgress").addEventListener(
            "click", function () {
                /* Hiding the card does not stop the job: the poll keeps
                   running and the modal reopens on settle. */
                _felById("modalReproducePublished").style.display = "none";
            });
    }

    return {
        fnOpen: fnOpen,
        fnClose: fnClose,
        fnBind: fnBind,
    };
})();
