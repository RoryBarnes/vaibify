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
        try {
            var dictState = await VaibifyApi.fdictGet(
                "/api/pipeline/" + sContainerId + "/state"
            );
            _fnReportPollSuccess("pipeline-state");
            if (_fnOnPipelineState) {
                _fnOnPipelineState(dictState);
            }
        } catch (error) {
            _fnReportPollFailure("pipeline-state", error);
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

    async function _fnPollFileChanges(sContainerId) {
        var pBadges = (typeof VaibifyGitBadges !== "undefined")
            ? VaibifyGitBadges.fnRefresh(sContainerId)
            : Promise.resolve();
        try {
            var dictStatus = await VaibifyApi.fdictGet(
                "/api/pipeline/" + sContainerId + "/file-status"
            );
            _fnReportPollSuccess("file-status");
            try { await pBadges; } catch (e) { /* badges optional */ }
            if (_fnOnFileStatus) {
                _fnOnFileStatus(dictStatus);
            }
        } catch (error) {
            _fnReportPollFailure("file-status", error);
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
        fnSetPollInterval: fnSetPollInterval,
        fiGetPollIntervalMs: fiGetPollIntervalMs,
    };
})();
