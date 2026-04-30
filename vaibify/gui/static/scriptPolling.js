/* Vaibify — Unified polling manager */

var VaibifyPolling = (function () {
    "use strict";

    var _iPipelinePollTimer = null;
    var _iFileChangePollTimer = null;
    var _iReposPollTimer = null;
    var _iPollIntervalMs = 5000;
    var _fnOnPipelineState = null;
    var _fnOnFileStatus = null;
    var _fnOnReposStatus = null;

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
            if (_fnOnPipelineState) {
                _fnOnPipelineState(dictState);
            }
        } catch (error) {
            /* poll failed, try again next interval */
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
            try { await pBadges; } catch (e) { /* badges optional */ }
            if (_fnOnFileStatus) {
                _fnOnFileStatus(dictStatus);
            }
        } catch (error) {
            /* poll failed, try again next interval */
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
            if (_fnOnReposStatus) {
                _fnOnReposStatus(dictStatus);
            }
        } catch (error) {
            /* poll failed, try again next interval */
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
        fnSetPollInterval: fnSetPollInterval,
        fiGetPollIntervalMs: fiGetPollIntervalMs,
    };
})();
