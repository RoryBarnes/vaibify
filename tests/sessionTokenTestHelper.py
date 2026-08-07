"""Bootstrap a real per-browser credential for the in-process test harness.

Sweep A retired the shared hub session token as a browser credential: the
``/api/session-token`` oracle is gone and ``SessionTokenMiddleware`` now
accepts only a ``BrowserSessionRecord`` credential minted at
``/api/bootstrap``. A TestClient must therefore authenticate exactly as a
real browser does -- redeem a launch capability for a credential -- instead
of reading ``app.state.sSessionToken`` and presenting it directly.

``fsBootstrapCredential`` performs that redemption against the app's own
browser-session store, so a test client presents a genuine credential and
the middleware's credential-only gate is exercised for real.
"""

from vaibify.gui import browserSession

__all__ = ["fsBootstrapCredential"]


def fsBootstrapCredential(app):
    """Mint and redeem a bootstrap capability on ``app``; return the credential.

    Mirrors the browser's one-time exchange at ``/api/bootstrap`` against
    ``app.state.dictBrowserSessions``, returning the per-browser credential a
    TestClient then presents in the ``X-Session-Token`` header (or the
    ``sToken`` query param for WebSocket and download routes).
    """
    dictStore = app.state.dictBrowserSessions
    sCapability = browserSession.fsMintBootstrapCapability(dictStore)
    _sSessionId, sCredential = browserSession.ftRedeemCapability(
        dictStore, sCapability,
    )
    return sCredential
