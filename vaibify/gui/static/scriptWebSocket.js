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
    /* The ladder is SIZED FROM the server's hold window, never
     * hardcoded beside it. A fixed [1,2,4,8,16] ladder ran 31 seconds
     * against a window that revoked the credential at about 20, so its
     * last two attempts were refused 4401 and the refusal surfaced to
     * the researcher as "the server restarted" -- which it had not.
     * The server sends fReconnectWindowSeconds at connect; a lane with
     * a longer window therefore gets a longer ladder with no change
     * here. */
    var _fReconnectWindowSeconds = 15;
    var _fReconnectElapsedSeconds = 0;
    var _bLastCloseExhaustedWindow = false;
    /* Backoff doubles from 1s but stops growing here, so a long window
     * yields many attempts rather than a few enormous gaps. */
    var _F_RECONNECT_MAX_DELAY_SECONDS = 30;
    /* Land the last attempt before the window closes, not on it. */
    var _F_RECONNECT_MARGIN_SECONDS = 2;

    function fnSetReconnectWindowSeconds(fSeconds) {
        if (typeof fSeconds === "number" && fSeconds > 0) {
            _fReconnectWindowSeconds = fSeconds;
        }
    }

    function _ffNextReconnectDelaySeconds() {
        /* Return the next backoff delay, or -1 when retrying further
         * would outlive the window the server promised to hold. */
        var fDelay = Math.min(
            Math.pow(2, _iReconnectAttempt),
            _F_RECONNECT_MAX_DELAY_SECONDS,
        );
        var fBudget =
            _fReconnectWindowSeconds - _F_RECONNECT_MARGIN_SECONDS;
        if (_fReconnectElapsedSeconds + fDelay > fBudget) {
            return -1;
        }
        return fDelay;
    }

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
        if (typeof VaibifyApp === "undefined") return "";
        return VaibifyApp.fsGetLeaseId() || "";
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
            _fReconnectElapsedSeconds = 0;
            _bLastCloseExhaustedWindow = false;
            _fnFlushPendingActions();
        };
        wsNew.onmessage = function (event) {
            /* Same fence as onclose: a superseded socket may still have
             * frames buffered, and dispatching them writes another
             * container's run output into the current view. */
            if (_wsPipeline !== wsNew) {
                return;
            }
            console.log(
                "[WS] message:", event.data.substring(0, 200));
            _fnDispatchEvent(JSON.parse(event.data));
        };
        wsNew.onclose = function (event) {
            console.log("[WS] close, code:", event.code);
            /* Only the socket still holding the slot may clear it. A
             * close fires asynchronously, so a socket torn down by
             * fnConnect lands after its replacement is already stored;
             * clearing unconditionally orphaned the live socket, left
             * fiGetReadyState() at -1, and drove a reconnect that the
             * server answered 4409 as a duplicate session. */
            if (_wsPipeline !== wsNew) {
                return;
            }
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
        var fDelaySeconds = _ffNextReconnectDelaySeconds();
        if (fDelaySeconds < 0) {
            /* The window the server promised has run out. That is a
             * different fact from "the server went away", and the
             * researcher is told which one happened. */
            _bLastCloseExhaustedWindow = true;
            _fnEmitCloseEventAndDropPending(event);
            return;
        }
        _iReconnectAttempt++;
        _fReconnectElapsedSeconds += fDelaySeconds;
        console.log(
            "[WS] scheduling reconnect attempt",
            _iReconnectAttempt, "in", fDelaySeconds, "s",
            "(", _fReconnectElapsedSeconds, "of",
            _fReconnectWindowSeconds, "s window )",
        );
        _iReconnectTimer = setTimeout(
            _fnAttemptReconnect, fDelaySeconds * 1000,
        );
    }

    function _fnEmitCloseEventAndDropPending(event) {
        var bActionsDropped = _listPendingActions.length > 0;
        _listPendingActions.length = 0;
        _fnDispatchEvent({
            sType: "_wsClose",
            iCode: event.code,
            bActionsDropped: bActionsDropped,
            bWindowExhausted: _bLastCloseExhaustedWindow,
            fWindowSeconds: _fReconnectWindowSeconds,
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
        _fReconnectElapsedSeconds = 0;
        _bLastCloseExhaustedWindow = false;
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
            /* The slot is cleared by this socket's own onclose, which
             * is what tells that handler the close belongs to the
             * live socket rather than to a superseded one. */
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
        fnSetReconnectWindowSeconds: fnSetReconnectWindowSeconds,
        fnSend: fnSend,
        fnSendDirect: fnSendDirect,
        fnDisconnect: fnDisconnect,
        fbIsOpen: fbIsOpen,
        fiGetReadyState: fiGetReadyState,
    };
})();
