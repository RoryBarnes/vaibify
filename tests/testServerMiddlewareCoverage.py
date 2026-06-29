"""Coverage tests for serverMiddleware agent-lane path/token helpers.

These close mutation-testing holes in the websocket branches of the
agent authorization lane: the ``/ws`` route prefix in
``_fsContainerIdFromPath`` and the websocket query-param token fallback
in ``_fsAgentPresentedToken``.
"""

from vaibify.gui import serverMiddleware


class FakeRequest:
    """Minimal request double exposing headers and query_params .get()."""

    def __init__(self, dictHeaders=None, dictQuery=None):
        self.headers = dict(dictHeaders or {})
        self.query_params = dict(dictQuery or {})


def testContainerIdFromPathRecognizesWebSocketPrefix():
    """A /ws/<group>/<cid>/... path yields the container-id segment."""
    sContainerId = serverMiddleware._fsContainerIdFromPath(
        "/ws/files/cid-123/connect",
    )
    assert sContainerId == "cid-123"


def testContainerIdFromPathStillRecognizesApiPrefix():
    """The companion /api prefix continues to yield the container id."""
    sContainerId = serverMiddleware._fsContainerIdFromPath(
        "/api/files/cid-456/list",
    )
    assert sContainerId == "cid-456"


def testAgentPresentedTokenFallsBackToWebSocketQueryParam():
    """With no session header, a WS upgrade reads sToken from the query."""
    request = FakeRequest(
        dictHeaders={"upgrade": "websocket"},
        dictQuery={"sToken": "agent-tok"},
    )
    assert serverMiddleware._fsAgentPresentedToken(request) == "agent-tok"


def testAgentPresentedTokenHeaderWinsOverWebSocketQuery():
    """The X-Vaibify-Session header takes precedence over the WS query."""
    request = FakeRequest(
        dictHeaders={
            "x-vaibify-session": "header-tok",
            "upgrade": "websocket",
        },
        dictQuery={"sToken": "agent-tok"},
    )
    assert serverMiddleware._fsAgentPresentedToken(request) == "header-tok"
