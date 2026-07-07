/* Vaibify — WebSocket connection and event dispatch */

var VaibifyWebSocket = (function () {
    "use strict";

    var _wsPipeline = null;
    var _listPendingActions = [];
    var I_MAX_PENDING_ACTIONS = 100;
    var _dictEventHandlers = {};
    var _sActiveContainerId = null;
    var _sActiveSessionToken = null;
    var _bIntentionalDisconnect = false;
    var _iReconnectAttempt = 0;
    var _iReconnectTimer = null;
    var _laReconnectDelaysSeconds = [1, 2, 4, 8, 16];

    function fnOnEvent(sType, fnHandler) {
        if (!_dictEventHandlers[sType]) {
            _dictEventHandlers[sType] = [];
        }
        _dictEventHandlers[sType].push(fnHandler);
    }

    function _fnDispatchEvent(dictEvent) {
        var sType = dictEvent.sType;
        var listHandlers = _dictEventHandlers[sType];
        if (listHandlers) {
            for (var i = 0; i < listHandlers.length; i++) {
                listHandlers[i](dictEvent);
            }
            return;
        }
        var listWildcard = _dictEventHandlers["*"];
        if (listWildcard) {
            for (var j = 0; j < listWildcard.length; j++) {
                listWildcard[j](dictEvent);
            }
        }
    }

    function fnConnect(sContainerId, sSessionToken) {
        if (_wsPipeline && (
            _wsPipeline.readyState === WebSocket.OPEN ||
            _wsPipeline.readyState === WebSocket.CONNECTING
        )) {
            return _wsPipeline;
        }
        if (_wsPipeline) {
            try { _wsPipeline.close(); } catch (e) { /* ignore */ }
            _wsPipeline = null;
        }
        _sActiveContainerId = sContainerId;
        _sActiveSessionToken = sSessionToken;
        _bIntentionalDisconnect = false;
        _fnClearReconnectTimer();
        _wsPipeline = _fnOpenSocket(sContainerId, sSessionToken);
        return _wsPipeline;
    }

    function _fsActiveLease() {
        if (typeof PipeleyenApp === "undefined") return "";
        return PipeleyenApp.fsGetLeaseId() || "";
    }

    function _fnOpenSocket(sContainerId, sSessionToken) {
        var sProtocol =
            window.location.protocol === "https:" ? "wss:" : "ws:";
        var sUrl = sProtocol + "//" + window.location.host +
            "/ws/pipeline/" + sContainerId +
            "?sToken=" + encodeURIComponent(sSessionToken) +
            "&sLeaseId=" + encodeURIComponent(_fsActiveLease());
        var wsNew = new WebSocket(sUrl);
        wsNew.onopen = function () {
            console.log("[WS] open, flushing",
                _listPendingActions.length, "pending actions");
            _iReconnectAttempt = 0;
            _fnFlushPendingActions();
        };
        wsNew.onmessage = function (event) {
            console.log(
                "[WS] message:", event.data.substring(0, 200));
            _fnDispatchEvent(JSON.parse(event.data));
        };
        wsNew.onclose = function (event) {
            console.log("[WS] close, code:", event.code);
            _wsPipeline = null;
            _fnHandleSocketClose(event);
        };
        wsNew.onerror = function () {
            /* onclose always follows onerror; defer dispatch to it. */
        };
        return wsNew;
    }

    function _fnHandleSocketClose(event) {
        var bNormal = event.code === 1000 || event.code === 1001;
        /* 4xxx codes are the server's deliberate refusals (bad token
         * 4401, foreign lease 4403, duplicate session 4409). Retrying
         * re-asks the same question and gets the same answer; surface
         * the refusal immediately instead of after a silent ladder. */
        var bDeliberateRefusal = event.code >= 4000 && event.code < 5000;
        if (_bIntentionalDisconnect || bNormal || bDeliberateRefusal) {
            _fnEmitCloseEventAndDropPending(event);
            return;
        }
        if (_iReconnectAttempt >= _laReconnectDelaysSeconds.length) {
            _fnEmitCloseEventAndDropPending(event);
            return;
        }
        var iDelaySeconds =
            _laReconnectDelaysSeconds[_iReconnectAttempt];
        _iReconnectAttempt++;
        console.log(
            "[WS] scheduling reconnect attempt",
            _iReconnectAttempt, "in", iDelaySeconds, "s",
        );
        _iReconnectTimer = setTimeout(
            _fnAttemptReconnect, iDelaySeconds * 1000,
        );
    }

    function _fnEmitCloseEventAndDropPending(event) {
        var bActionsDropped = _listPendingActions.length > 0;
        _listPendingActions.length = 0;
        _fnDispatchEvent({
            sType: "_wsClose",
            iCode: event.code,
            bActionsDropped: bActionsDropped,
        });
    }

    function _fnAttemptReconnect() {
        _iReconnectTimer = null;
        if (_bIntentionalDisconnect) return;
        if (!_sActiveContainerId || !_sActiveSessionToken) return;
        if (_wsPipeline) return;
        console.log(
            "[WS] reconnecting attempt", _iReconnectAttempt,
        );
        _wsPipeline = _fnOpenSocket(
            _sActiveContainerId, _sActiveSessionToken,
        );
    }

    function _fnClearReconnectTimer() {
        if (_iReconnectTimer !== null) {
            clearTimeout(_iReconnectTimer);
            _iReconnectTimer = null;
        }
        _iReconnectAttempt = 0;
    }

    function fnSend(dictAction) {
        if (_wsPipeline &&
            _wsPipeline.readyState === WebSocket.OPEN) {
            _wsPipeline.send(JSON.stringify(dictAction));
        } else {
            if (_listPendingActions.length < I_MAX_PENDING_ACTIONS) {
                _listPendingActions.push(dictAction);
            }
        }
    }

    function fnSendDirect(dictMessage) {
        if (_wsPipeline) {
            _wsPipeline.send(JSON.stringify(dictMessage));
        }
    }

    function _fnFlushPendingActions() {
        while (_listPendingActions.length > 0) {
            var dictAction = _listPendingActions.shift();
            if (_wsPipeline &&
                _wsPipeline.readyState === WebSocket.OPEN) {
                _wsPipeline.send(JSON.stringify(dictAction));
            }
        }
    }

    function fnDisconnect() {
        _bIntentionalDisconnect = true;
        _fnClearReconnectTimer();
        _sActiveContainerId = null;
        _sActiveSessionToken = null;
        if (_wsPipeline) {
            /*
             * Close with code 1000 (Normal Closure) so the onclose
             * handler reports an intentional teardown. Without an
             * explicit code, browsers fire close with code 1005
             * ("No Status Received"), which the connection monitor
             * would (correctly) treat as an abnormal disconnect and
             * surface the "server unreachable" toast on every
             * user-initiated workflow switch.
             */
            try { _wsPipeline.close(1000, "client disconnect"); }
            catch (e) { /* ignore */ }
            _wsPipeline = null;
        }
        _listPendingActions.length = 0;
    }

    function fbIsOpen() {
        return _wsPipeline &&
            _wsPipeline.readyState === WebSocket.OPEN;
    }

    function fiGetReadyState() {
        return _wsPipeline ? _wsPipeline.readyState : -1;
    }

    return {
        fnOnEvent: fnOnEvent,
        fnConnect: fnConnect,
        fnSend: fnSend,
        fnSendDirect: fnSendDirect,
        fnDisconnect: fnDisconnect,
        fbIsOpen: fbIsOpen,
        fiGetReadyState: fiGetReadyState,
    };
})();
