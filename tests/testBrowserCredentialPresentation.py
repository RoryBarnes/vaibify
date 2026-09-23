"""How a browser presents its credential is ONE rule, not two.

The session middleware and ``routeScope``'s container authority both
decide whether a request carries a per-browser credential. Each kept
its own copy of the rule, and they drifted: the middleware learned to
read ``?sToken=`` for a file download -- which a browser performs with
an anchor click and therefore without headers of our choosing -- and
the authority never did. A download cleared the middleware and was
then refused by the authority with "You do not hold this container's
lease", for a lease the researcher held. "Download to this computer"
could not succeed in any browser, and the only symptom reported was a
Firefox prompt asking whether to leave the page (2026-09-22).

These tests pin the rule itself, at the seam where it lives. A
credential in a URL is read by browser history, by any proxy and by
anything that logs a path, so the accommodation must stay exactly as
wide as the request that cannot do without it.
"""

import pytest

from vaibify.gui import browserSession


class _RequestDouble:
    """The three things the presentation rule reads off a request."""

    class _Url:
        def __init__(self, sPath):
            self.path = sPath

    def __init__(self, sPath, dictHeaders=None, dictQuery=None):
        self.url = _RequestDouble._Url(sPath)
        self.headers = dictHeaders or {}
        self.query_params = dictQuery or {}


def testTheHeaderIsPreferredWhereverItExists():
    """A header credential wins, so no path widens what is trusted."""
    requestDouble = _RequestDouble(
        "/api/files/abc/download/Step/out.csv",
        {"x-session-token": "fromHeader"},
        {"sToken": "fromQuery"},
    )
    assert browserSession.fsBrowserPresentedCredential(
        requestDouble) == "fromHeader"


def testADownloadMayPresentItsCredentialInTheQuery():
    """The accommodation itself: an anchor click carries no headers."""
    requestDouble = _RequestDouble(
        "/api/files/abc/download/Step/out.csv", {}, {"sToken": "fromQuery"},
    )
    assert browserSession.fsBrowserPresentedCredential(
        requestDouble) == "fromQuery"


def testAWebSocketUpgradeMayToo():
    """The pre-existing case, which must not regress."""
    requestDouble = _RequestDouble(
        "/ws/pipeline/abc", {"upgrade": "websocket"}, {"sToken": "fromQuery"},
    )
    assert browserSession.fsBrowserPresentedCredential(
        requestDouble) == "fromQuery"


@pytest.mark.falsification
def testNoOtherRouteReadsACredentialFromTheQuery():
    """The carve-out must not become a general authentication bypass.

    Kills: reading the query credential on every path -- the obvious
    simplification, and the one that turns a narrow accommodation for
    a headerless browser download into a way to authenticate any API
    request with a credential pasted into a URL.
    """
    for sPath in (
        "/api/files/abc/Step",
        "/api/pipeline/abc/run",
        "/api/registry/abc/claim",
        "/api/files/abc/upload",
        "/api/downloads-summary",
    ):
        requestDouble = _RequestDouble(sPath, {}, {"sToken": "fromQuery"})
        assert browserSession.fsBrowserPresentedCredential(
            requestDouble) == "", (
            f"{sPath} accepted a credential from the query string; only "
            f"a download and a WebSocket upgrade may"
        )


@pytest.mark.falsification
def testBothAuthoritiesAskTheSameFunction():
    """Neither caller may restate the rule instead of calling it.

    The defect was two copies disagreeing, so the binding is that
    there is one copy. A second reader of ``x-session-token`` in
    either module is that defect returning.

    Kills: re-inlining ``request.headers.get("x-session-token")`` in
    the middleware or in the container authority, which is how the
    download carve-out came to exist in one and not the other.
    """
    import inspect
    from vaibify.gui import routeScope, serverMiddleware
    for module in (routeScope, serverMiddleware):
        sSource = inspect.getsource(module)
        assert "fsBrowserPresentedCredential" in sSource, (
            f"{module.__name__} no longer asks the shared rule"
        )
        assert 'headers.get("x-session-token"' not in sSource, (
            f"{module.__name__} reads the credential header directly "
            f"again; that is the copy that drifted"
        )
