/* Vaibify — Unified polling manager */

var VaibifyPolling = (function () {
    "use strict";

    var _iPipelinePollTimer = null;
    var _iFileChangePollTimer = null;
    var _iPollIntervalMs = 5000;
    var _fnOnPipelineState = null;
    var _fnOnFileStatus = null;

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
        try {
            var dictStatus = await VaibifyApi.fdictGet(
                "/api/pipeline/" + sContainerId + "/file-status"
            );
            if (_fnOnFileStatus) {
                _fnOnFileStatus(dictStatus);
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
        fnSetPollInterval: fnSetPollInterval,
        fiGetPollIntervalMs: fiGetPollIntervalMs,
    };
})();
