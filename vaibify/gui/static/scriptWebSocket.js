/* Vaibify — WebSocket connection and event dispatch */

var VaibifyWebSocket = (function () {
    "use strict";

    var _wsPipeline = null;
    var _listPendingActions = [];
    var I_MAX_PENDING_ACTIONS = 100;
    var _dictEventHandlers = {};

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
        var sProtocol =
            window.location.protocol === "https:" ? "wss:" : "ws:";
        var sUrl = sProtocol + "//" + window.location.host +
            "/ws/pipeline/" + sContainerId +
            "?sToken=" + encodeURIComponent(sSessionToken);
        _wsPipeline = new WebSocket(sUrl);
        _wsPipeline.onopen = function () {
            console.log("[WS] open, flushing",
                _listPendingActions.length, "pending actions");
            _fnFlushPendingActions();
        };
        _wsPipeline.onmessage = function (event) {
            console.log(
                "[WS] message:", event.data.substring(0, 200));
            _fnDispatchEvent(JSON.parse(event.data));
        };
        _wsPipeline.onclose = function (event) {
            console.log("[WS] close, code:", event.code);
            _wsPipeline = null;
            var bActionsDropped = _listPendingActions.length > 0;
            _listPendingActions.length = 0;
            _fnDispatchEvent({
                sType: "_wsClose",
                iCode: event.code,
                bActionsDropped: bActionsDropped,
            });
        };
        _wsPipeline.onerror = function () {
            _wsPipeline = null;
            var bActionsDropped = _listPendingActions.length > 0;
            _listPendingActions.length = 0;
            _fnDispatchEvent({
                sType: "_wsError",
                bActionsDropped: bActionsDropped,
            });
        };
        return _wsPipeline;
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
        if (_wsPipeline) {
            _wsPipeline.close();
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
