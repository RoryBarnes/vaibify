/* Vaibify — Unified polling manager */

var VaibifyPolling = (function () {
    "use strict";

    var _iPipelinePollTimer = null;
    var _iFileChangePollTimer = null;
    var _iReposPollTimer = null;
    var _iDiscoveryPollTimer = null;
    var _iPollIntervalMs = 5000;
    var _fnOnPipelineState = null;
    var _fnOnFileStatus = null;
    var _fnOnReposStatus = null;
    var _fnOnWorkflowDiscovery = null;
    var _bPipelineInFlight = false;
    var _bFileStatusInFlight = false;
    var _bReposInFlight = false;
    var _bDiscoveryInFlight = false;
    var _iContainerHubPollTimer = null;
    var _iWorkflowHubPollTimer = null;
    var _iHubPollIntervalMs = 3000;
    var _bContainerHubInFlight = false;
    var _bWorkflowHubInFlight = false;
    var _fnOnContainerHubPoll = null;
    var _fnOnWorkflowHubPoll = null;

    /*
     * Failures are routed through VaibifyConnectionMonitor so a poll
     * that can't reach the server (e.g. port shift on restart, dropped
     * daemon, rotated session token) becomes a loud, user-visible
     * banner instead of silently freezing the dashboard. The monitor
     * may not have loaded yet (legacy embeds, test stubs); fall back
     * to console so we never lose the signal entirely.
     */

    function _fnReportPollFailure(sPoller, error) {
        if (typeof VaibifyConnectionMonitor !== "undefined" &&
            typeof VaibifyConnectionMonitor.fnReportPollFailure
                === "function") {
            VaibifyConnectionMonitor.fnReportPollFailure(sPoller, error);
            return;
        }
        console.warn(
            "[poll] " + sPoller + " failed:",
            error && error.message
        );
    }

    function _fnReportPollSuccess(sPoller) {
        if (typeof VaibifyConnectionMonitor !== "undefined" &&
            typeof VaibifyConnectionMonitor.fnReportPollSuccess
                === "function") {
            VaibifyConnectionMonitor.fnReportPollSuccess(sPoller);
        }
    }

    function fnSetPipelineStateHandler(fnHandler) {
        _fnOnPipelineState = fnHandler;
    }

    function fnSetFileStatusHandler(fnHandler) {
        _fnOnFileStatus = fnHandler;
    }

    function fnStartPipelinePolling(sContainerId) {
        fnStopPipelinePolling();
        _iLastSyncEpoch = null;
        _iPipelinePollTimer = setInterval(function () {
            _fnPollPipelineState(sContainerId);
        }, 10000);
    }

    function fnStopPipelinePolling() {
        if (_iPipelinePollTimer) {
            clearInterval(_iPipelinePollTimer);
            _iPipelinePollTimer = null;
        }
    }

    async function _fnPollPipelineState(sContainerId) {
        if (_bPipelineInFlight) return;
        _bPipelineInFlight = true;
        try {
            try {
                var dictState = await VaibifyApi.fdictGet(
                    "/api/pipeline/" + sContainerId + "/state"
                );
                _fnReportPollSuccess("pipeline-state");
                _fnMaybeRefreshBadgesOnSyncEpoch(sContainerId, dictState);
                if (_fnOnPipelineState) {
                    _fnOnPipelineState(dictState);
                }
            } catch (error) {
                _fnReportPollFailure("pipeline-state", error);
            }
        } finally {
            _bPipelineInFlight = false;
        }
    }

    var _iLastSyncEpoch = null;

    /* A sync-mutating route (push, pull, fetch, refresh-remotes)
       bumps the server-side epoch. Detecting the bump here triggers
       exactly one badge refresh — no extra polling loops and no
       remote git queries on a timer. */
    function _fnMaybeRefreshBadgesOnSyncEpoch(sContainerId, dictState) {
        var iEpoch = dictState ? dictState.iSyncEpoch : undefined;
        if (typeof iEpoch !== "number") return;
        var bChanged = _iLastSyncEpoch !== null &&
            iEpoch !== _iLastSyncEpoch;
        _iLastSyncEpoch = iEpoch;
        if (!bChanged) return;
        if (typeof VaibifyGitBadges !== "undefined" &&
            typeof VaibifyGitBadges.fnRefresh === "function") {
            VaibifyGitBadges.fnRefresh(sContainerId);
        }
    }

    function fnStartFilePolling(sContainerId) {
        fnStopFilePolling();
        _fnPollFileChanges(sContainerId);
        _iFileChangePollTimer = setInterval(function () {
            _fnPollFileChanges(sContainerId);
        }, _iPollIntervalMs);
    }

    function fnStopFilePolling() {
        if (_iFileChangePollTimer) {
            clearInterval(_iFileChangePollTimer);
            _iFileChangePollTimer = null;
        }
    }

    async function _fnPollFileChangesBody(sContainerId) {
        /* Badge refresh is owned by the sync-epoch-bump path in
           _fnMaybeRefreshBadgesOnSyncEpoch; the file-status tick used
           to also unconditionally call VaibifyGitBadges.fnRefresh,
           which doubled the per-tick container exec load for no
           observable user benefit. The badge UI now updates only
           when a sync operation actually bumps the server epoch. */
        try {
            /* The epoch tells the server which workflow revision this
               tab has applied; a stale epoch makes the server attach
               the full workflow so a dropped response or a competing
               poller can never permanently strand this tab. */
            var dictStatus = await VaibifyApi.fdictGet(
                "/api/pipeline/" + sContainerId + "/file-status" +
                "?iWorkflowEpoch=" + encodeURIComponent(
                    PipeleyenApp.fiGetWorkflowEpoch())
            );
            _fnReportPollSuccess("file-status");
            if (_fnOnFileStatus) {
                _fnOnFileStatus(dictStatus);
            }
        } catch (error) {
            _fnReportPollFailure("file-status", error);
        }
    }

    async function _fnPollFileChanges(sContainerId) {
        if (_bFileStatusInFlight) return;
        _bFileStatusInFlight = true;
        try {
            await _fnPollFileChangesBody(sContainerId);
        } finally {
            _bFileStatusInFlight = false;
        }
    }

    function fnSetReposHandler(fnHandler) {
        _fnOnReposStatus = fnHandler;
    }

    function fnStartReposPolling(sContainerId) {
        fnStopReposPolling();
        _fnPollReposStatus(sContainerId);
        _iReposPollTimer = setInterval(function () {
            _fnPollReposStatus(sContainerId);
        }, _iPollIntervalMs);
    }

    function fnStopReposPolling() {
        if (_iReposPollTimer) {
            clearInterval(_iReposPollTimer);
            _iReposPollTimer = null;
        }
    }

    async function _fnPollReposStatus(sContainerId) {
        if (_bReposInFlight) return;
        _bReposInFlight = true;
        try {
            try {
                var dictStatus = await VaibifyApi.fdictGet(
                    "/api/repos/" + sContainerId + "/status"
                );
                _fnReportPollSuccess("repos-status");
                if (_fnOnReposStatus) {
                    _fnOnReposStatus(dictStatus);
                }
            } catch (error) {
                _fnReportPollFailure("repos-status", error);
            }
        } finally {
            _bReposInFlight = false;
        }
    }

    function fnSetWorkflowDiscoveryHandler(fnHandler) {
        _fnOnWorkflowDiscovery = fnHandler;
    }

    function fnStartDiscoveryPolling(sContainerId) {
        fnStopDiscoveryPolling();
        _fnPollWorkflowDiscovery(sContainerId);
        _iDiscoveryPollTimer = setInterval(function () {
            _fnPollWorkflowDiscovery(sContainerId);
        }, _iPollIntervalMs);
    }

    function fnStopDiscoveryPolling() {
        if (_iDiscoveryPollTimer) {
            clearInterval(_iDiscoveryPollTimer);
            _iDiscoveryPollTimer = null;
        }
    }

    async function _fnPollWorkflowDiscovery(sContainerId) {
        if (_bDiscoveryInFlight) return;
        _bDiscoveryInFlight = true;
        try {
            try {
                var dictResponse = await VaibifyApi.fdictGet(
                    "/api/pipeline/" + sContainerId + "/workflow-discovery"
                );
                _fnReportPollSuccess("workflow-discovery");
                if (_fnOnWorkflowDiscovery) {
                    _fnOnWorkflowDiscovery(dictResponse);
                }
            } catch (error) {
                _fnReportPollFailure("workflow-discovery", error);
            }
        } finally {
            _bDiscoveryInFlight = false;
        }
    }

    /*
     * Container- and workflow-picker polling. These were hand-rolled
     * timers in scriptApplication.js that hit /api/registry and
     * /api/workflows directly and swallowed every failure, so a dead
     * server during the picker view kept hammering silently. Folding
     * them here routes their failures through the same connection
     * monitor as the dashboard pollers, so an unreachable server
     * surfaces a loud banner instead. The work itself stays in the
     * caller's handler; this layer owns the timer, the in-flight
     * guard, the immediate-first-poll, and the failure reporting.
     */

    function fnSetContainerHubHandler(fnHandler) {
        _fnOnContainerHubPoll = fnHandler;
    }

    function fnSetWorkflowHubHandler(fnHandler) {
        _fnOnWorkflowHubPoll = fnHandler;
    }

    function fnStartContainerHubPolling() {
        fnStopContainerHubPolling();
        _fnPollContainerHub();
        _iContainerHubPollTimer = setInterval(
            _fnPollContainerHub, _iHubPollIntervalMs);
    }

    function fnStopContainerHubPolling() {
        if (_iContainerHubPollTimer) {
            clearInterval(_iContainerHubPollTimer);
            _iContainerHubPollTimer = null;
        }
    }

    async function _fnPollContainerHub() {
        if (_bContainerHubInFlight || !_fnOnContainerHubPoll) return;
        _bContainerHubInFlight = true;
        try {
            await _fnOnContainerHubPoll();
            _fnReportPollSuccess("container-hub");
        } catch (error) {
            _fnReportPollFailure("container-hub", error);
        } finally {
            _bContainerHubInFlight = false;
        }
    }

    function fnStartWorkflowHubPolling() {
        fnStopWorkflowHubPolling();
        _fnPollWorkflowHub();
        _iWorkflowHubPollTimer = setInterval(
            _fnPollWorkflowHub, _iHubPollIntervalMs);
    }

    function fnStopWorkflowHubPolling() {
        if (_iWorkflowHubPollTimer) {
            clearInterval(_iWorkflowHubPollTimer);
            _iWorkflowHubPollTimer = null;
        }
    }

    async function _fnPollWorkflowHub() {
        if (_bWorkflowHubInFlight || !_fnOnWorkflowHubPoll) return;
        _bWorkflowHubInFlight = true;
        try {
            await _fnOnWorkflowHubPoll();
            _fnReportPollSuccess("workflow-hub");
        } catch (error) {
            _fnReportPollFailure("workflow-hub", error);
        } finally {
            _bWorkflowHubInFlight = false;
        }
    }

    function fnSetPollInterval(iSeconds) {
        _iPollIntervalMs = iSeconds * 1000;
    }

    function fiGetPollIntervalMs() {
        return _iPollIntervalMs;
    }

    return {
        fnSetPipelineStateHandler: fnSetPipelineStateHandler,
        fnSetFileStatusHandler: fnSetFileStatusHandler,
        fnStartPipelinePolling: fnStartPipelinePolling,
        fnStopPipelinePolling: fnStopPipelinePolling,
        fnStartFilePolling: fnStartFilePolling,
        fnStopFilePolling: fnStopFilePolling,
        fnSetReposHandler: fnSetReposHandler,
        fnStartReposPolling: fnStartReposPolling,
        fnStopReposPolling: fnStopReposPolling,
        fnSetWorkflowDiscoveryHandler: fnSetWorkflowDiscoveryHandler,
        fnStartDiscoveryPolling: fnStartDiscoveryPolling,
        fnStopDiscoveryPolling: fnStopDiscoveryPolling,
        fnSetContainerHubHandler: fnSetContainerHubHandler,
        fnStartContainerHubPolling: fnStartContainerHubPolling,
        fnStopContainerHubPolling: fnStopContainerHubPolling,
        fnSetWorkflowHubHandler: fnSetWorkflowHubHandler,
        fnStartWorkflowHubPolling: fnStartWorkflowHubPolling,
        fnStopWorkflowHubPolling: fnStopWorkflowHubPolling,
        fnSetPollInterval: fnSetPollInterval,
        fiGetPollIntervalMs: fiGetPollIntervalMs,
    };
})();
