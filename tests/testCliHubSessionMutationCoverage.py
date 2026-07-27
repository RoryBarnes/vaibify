"""Kill-confirmed tests for the host CLI's researcher lane.

Three guarantees that a green suite would otherwise assert nothing
about, each proven to fail when the guarantee is broken (see
``tests/falsificationRegistry.py`` for the exact mutation):

* the CLI authenticates as the RESEARCHER (shared session token in
  ``X-Session-Token``), never as the in-container agent;
* the lease release reaches the hub as a query parameter, which is the
  only place the route reads it — sending it as a body silently leaves
  the container held, and the next command is refused 409;
* generated request paths carry the container ID, while the lease is
  keyed by container NAME. Conflating the two is the exact bug
  AGENTS.md records as having shipped green.
"""

import pytest

from unittest.mock import patch

from vaibify.cli import hubSession
from vaibify.cli.actionCommands import fsBuildRequestPath


pytestmark = pytest.mark.falsification


class _FakeResponse:
    """Minimal stand-in for a requests Response."""

    def __init__(self, dictBody, iStatusCode=200):
        self.status_code = iStatusCode
        self._dictBody = dictBody
        self.text = ""

    def json(self):
        return self._dictBody


def _fdictSession():
    return {
        "sBaseUrl": "http://127.0.0.1:8137",
        "sSessionToken": "shared-token",
        "sContainerName": "someProject",
        "sContainerId": "0123456789ab",
        "sLeaseId": "lease-abc",
        "sWorkflowPath": "",
    }


def test_the_cli_authenticates_as_the_researcher_not_as_the_agent():
    """The host CLI must present the browser token, never the agent's.

    The agent lane is a per-container machine credential whose reach is
    filtered by ``bAgentSafe``; the researcher at their own terminal is
    not that principal, and borrowing its header would both mis-attribute
    the caller and lose access to researcher-only actions.

    Kills: S_BROWSER_TOKEN_HEADER = "X-Session-Token" -> the agent
    header name "X-Vaibify-Session".
    """
    with patch("requests.request") as mockRequest:
        mockRequest.return_value = _FakeResponse({"ok": True})
        hubSession.ftSendHttpRequest(
            "http://127.0.0.1:8137", "shared-token", "GET",
            "/api/registry",
        )
    dictHeaders = mockRequest.call_args.kwargs["headers"]
    assert dictHeaders == {"X-Session-Token": "shared-token"}
    assert "X-Vaibify-Session" not in dictHeaders


def test_release_sends_the_lease_where_the_route_reads_it():
    """The release lease must ride the query string, not the body.

    ``POST /api/registry/{sName}/release`` declares ``sLeaseId`` as a
    query parameter, so a body-borne lease reaches the handler as the
    empty string: the hub answers 200 with ``bReleased: false`` and goes
    on holding the container. Observed live — the next command was
    refused "In use in another browser session".

    Kills: the release call's ``dictQuery={"sLeaseId": ...}`` ->
    passing the lease as the request body instead.
    """
    with patch("requests.request") as mockRequest:
        mockRequest.return_value = _FakeResponse({"bReleased": True})
        hubSession.fnReleaseContainer(_fdictSession())
    dictKeywords = mockRequest.call_args.kwargs
    assert dictKeywords["params"] == {"sLeaseId": "lease-abc"}
    assert "json" not in dictKeywords


def test_generated_paths_carry_the_container_id_not_the_name():
    """Routes are pathed by container ID; the lease is keyed by name.

    The docker exec behind every route needs the ID, so a path built
    from the name 404s (or worse, addresses another container). This
    fixture keeps name and ID distinct precisely because a fixture where
    they agree once let this class of bug ship green.

    Kills: fsBuildRequestPath's
    ``dictValues["sContainerId"] = dictSession["sContainerId"]`` ->
    ``dictSession["sContainerName"]``.
    """
    dictEntry = {
        "sName": "probe", "sMethod": "POST",
        "sPath": "/api/steps/{sContainerId}/{iStepIndex}/run",
        "sCategory": "files", "bAgentSafe": True,
        "sDescription": "A probe entry.",
    }
    sPath = fsBuildRequestPath(
        dictEntry, _fdictSession(), {"iStepIndex": 4},
    )
    assert sPath == "/api/steps/0123456789ab/4/run"
    assert "someProject" not in sPath
