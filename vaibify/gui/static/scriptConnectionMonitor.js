/* Vaibify — Server-connection failure monitor
 *
 * Polling and WebSocket layers route their failures here so the user
 * sees one loud, actionable surface instead of a silently-frozen
 * dashboard. The "ground truth" invariant (see CLAUDE.md) requires
 * that any inability to reach the server is reported, not hidden.
 *
 * Two trigger paths converge:
 *   - fnReportPollFailure(sPoller, dictError) from scriptPolling.js
 *   - fnReportWsLoss(dictEvent) from scriptApplication's WS handlers
 *
 * Either a network error or an unauthorized response (token rotated
 * after a server restart) trips fnSurfaceServerUnreachable, which:
 *   1. stops every poller so we don't keep hammering a dead endpoint,
 *   2. closes the WebSocket,
 *   3. shows a persistent error toast with a "click to reload" action.
 *
 * The trigger is idempotent — once surfaced, repeated reports are
 * absorbed silently so the toast doesn't multiply across the four
 * pollers.
 */

var VaibifyConnectionMonitor = (function () {
    "use strict";

    var _bSurfaced = false;
    var _dictConsecutiveFailures = {};

    function _fbErrorIsDisconnectSignal(dictError) {
        if (!dictError) return false;
        if (dictError.sKind === "network") return true;
        if (dictError.sKind === "unauthorized") return true;
        return false;
    }

    function fnReportPollFailure(sPoller, dictError) {
        if (_bSurfaced) return;
        if (!_fbErrorIsDisconnectSignal(dictError)) {
            console.warn(
                "[poll] " + sPoller + " failed:",
                dictError && dictError.message
            );
            return;
        }
        _dictConsecutiveFailures[sPoller] =
            (_dictConsecutiveFailures[sPoller] || 0) + 1;
        fnSurfaceServerUnreachable(dictError);
    }

    function fnReportPollSuccess(sPoller) {
        _dictConsecutiveFailures[sPoller] = 0;
    }

    function fnReportWsLoss(dictEvent) {
        if (_bSurfaced) return;
        var iCode = dictEvent && dictEvent.iCode;
        if (iCode === 1000 || iCode === 1001) {
            return;
        }
        fnSurfaceServerUnreachable({
            sKind: _fsKindFromCloseCode(iCode),
            iCode: iCode,
            sMessage: "WebSocket closed (code " + (iCode || "?") + ")",
        });
    }

    function _fsKindFromCloseCode(iCode) {
        /* 4xxx closes are the server's deliberate refusals; calling
         * them "cannot reach server" misrepresents a healthy server
         * as a dead one (the ground-truth invariant). */
        if (iCode === 4401) return "unauthorized";
        if (iCode >= 4000 && iCode < 5000) return "refused";
        return "network";
    }

    function fnSurfaceServerUnreachable(dictError) {
        if (_bSurfaced) return;
        _bSurfaced = true;
        _fnStopAllPolling();
        _fnCloseWebSocket();
        _fnShowReloadToast(dictError);
    }

    function _fnStopAllPolling() {
        if (typeof VaibifyPolling === "undefined") return;
        try { VaibifyPolling.fnStopPipelinePolling(); } catch (e) {}
        try { VaibifyPolling.fnStopFilePolling(); } catch (e) {}
        try { VaibifyPolling.fnStopReposPolling(); } catch (e) {}
        try { VaibifyPolling.fnStopDiscoveryPolling(); } catch (e) {}
        try { VaibifyPolling.fnStopContainerHubPolling(); } catch (e) {}
        try { VaibifyPolling.fnStopWorkflowHubPolling(); } catch (e) {}
    }

    function _fnCloseWebSocket() {
        if (typeof VaibifyWebSocket === "undefined") return;
        try { VaibifyWebSocket.fnDisconnect(); } catch (e) {}
    }

    function _fnShowReloadToast(dictError) {
        var sMessage = _fsBuildToastMessage(dictError);
        if (typeof PipeleyenApp === "undefined" ||
            typeof PipeleyenApp.fnShowToast !== "function") {
            console.error(sMessage);
            return;
        }
        PipeleyenApp.fnShowToast(
            sMessage, "error",
            function () { window.location.reload(); }
        );
    }

    function _fsBuildToastMessage(dictError) {
        if (dictError && dictError.sKind === "unauthorized") {
            return (
                "Vaibify server has been restarted (session expired). " +
                "Click to reload the dashboard."
            );
        }
        if (dictError && dictError.sKind === "refused") {
            if (dictError.iCode === 4409) {
                return (
                    "The server refused this connection: another live " +
                    "session is already driving this container " +
                    "(code 4409). Close the other tab, then click to " +
                    "reload the dashboard."
                );
            }
            return (
                "The server refused this connection (code " +
                dictError.iCode + "): this tab no longer holds the " +
                "container's session. Click to reload and reclaim."
            );
        }
        // Deliberate refusals are named above; for a genuine outage
        // the detail (e.g. "WebSocket closed (code 1006)") is the one
        // clue that distinguishes a network drop from a server
        // restart — surface it so an incident is diagnosable without
        // the browser console.
        var sDetail = (dictError && dictError.sMessage)
            ? " [" + dictError.sMessage + "]"
            : "";
        return (
            "Cannot reach Vaibify server" + sDetail + ". The server " +
            "may have stopped or moved to a different port on " +
            "restart. Click to reload the dashboard."
        );
    }

    function fnReset() {
        _bSurfaced = false;
        _dictConsecutiveFailures = {};
    }

    function fbHasSurfaced() {
        return _bSurfaced;
    }

    return {
        fnReportPollFailure: fnReportPollFailure,
        fnReportPollSuccess: fnReportPollSuccess,
        fnReportWsLoss: fnReportWsLoss,
        fnSurfaceServerUnreachable: fnSurfaceServerUnreachable,
        fnReset: fnReset,
        fbHasSurfaced: fbHasSurfaced,
    };
})();
