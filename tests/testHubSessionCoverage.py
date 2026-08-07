"""Behavior tests for the researcher-lane hub client.

Exercises the request-building, response-parsing, and error paths of
``vaibify.cli.hubSession`` with the true externals mocked: ``requests``
for HTTP, ``websockets`` for the pipeline socket, and the session
registry for hub discovery. The lane-identity guarantees (which header,
which query) are kill-confirmed in
``testCliHubSessionMutationCoverage.py``; this file covers the parsing
and failure branches those tests do not touch.
"""

import json

import pytest

from unittest.mock import MagicMock, patch

from vaibify.cli import hubSession
from vaibify.cli.hubSession import HubSessionError


class _FakeResponse:
    """Minimal stand-in for a requests.Response."""

    def __init__(self, objBody, iStatusCode=200, bJsonRaises=False):
        self.status_code = iStatusCode
        self._objBody = objBody
        self.text = objBody if isinstance(objBody, str) else ""
        self._bJsonRaises = bJsonRaises

    def json(self):
        if self._bJsonRaises:
            raise ValueError("not json")
        return self._objBody


def _fdictSession():
    return {
        "sBaseUrl": "http://127.0.0.1:8137",
        "iHubPort": 8137,
        "sCredential": "browser-credential",
        "sContainerName": "someProject",
        "sContainerId": "0123456789ab",
        "sLeaseId": "lease-abc",
        "sWorkflowPath": "",
    }


# ------------------------------------------------------------------
# Hub discovery and base-URL resolution
# ------------------------------------------------------------------


def test_flist_find_live_hubs_keeps_only_port_bound_hub_role():
    listSlots = [
        {"iPort": 8050, "sRole": "hub"},
        {"iPort": 8051, "sRole": "viewer"},
        {"iPort": 0, "sRole": "hub"},
        {"sRole": "hub"},
    ]
    with patch(
        "vaibify.config.sessionRegistry.flistReadAllSlots",
        return_value=listSlots,
    ):
        listFound = hubSession.flistFindLiveHubSessions()
    assert listFound == [{"iPort": 8050, "sRole": "hub"}]


def test_resolve_base_url_honors_explicit_port():
    assert hubSession.fsResolveHubBaseUrl(8137) == "http://127.0.0.1:8137"


def test_resolve_base_url_raises_when_no_hub():
    with patch.object(
        hubSession, "flistFindLiveHubSessions", return_value=[],
    ):
        with pytest.raises(HubSessionError, match="No live vaibify hub"):
            hubSession.fsResolveHubBaseUrl()


def test_resolve_base_url_raises_when_several_hubs():
    with patch.object(
        hubSession, "flistFindLiveHubSessions",
        return_value=[{"iPort": 8050}, {"iPort": 8051}],
    ):
        with pytest.raises(HubSessionError, match="Several vaibify hubs"):
            hubSession.fsResolveHubBaseUrl()


def test_resolve_base_url_uses_the_single_live_hub():
    with patch.object(
        hubSession, "flistFindLiveHubSessions",
        return_value=[{"iPort": 8050}],
    ):
        assert hubSession.fsResolveHubBaseUrl() == "http://127.0.0.1:8050"


# ------------------------------------------------------------------
# Request building and response parsing
# ------------------------------------------------------------------


def test_parse_response_body_falls_back_to_text_when_not_json():
    fakeResponse = _FakeResponse("plain text", bJsonRaises=True)
    fakeResponse.text = "plain text"
    assert hubSession._fobjParseResponseBody(fakeResponse) == "plain text"


def test_get_fields_ride_the_query_string():
    with patch("requests.request") as mockRequest:
        mockRequest.return_value = _FakeResponse({"ok": True})
        hubSession.ftSendHttpRequest(
            "http://h", "tok", "GET", "/api/x",
            dictFields={"iLines": 5},
        )
    assert mockRequest.call_args.kwargs["params"] == {"iLines": 5}
    assert "json" not in mockRequest.call_args.kwargs


def test_post_fields_ride_the_json_body():
    with patch("requests.request") as mockRequest:
        mockRequest.return_value = _FakeResponse({"ok": True})
        hubSession.ftSendHttpRequest(
            "http://h", "tok", "POST", "/api/x",
            dictFields={"sNewName": "Foo"},
        )
    assert mockRequest.call_args.kwargs["json"] == {"sNewName": "Foo"}


def test_send_request_wraps_a_connection_error():
    import requests
    with patch("requests.request", side_effect=requests.ConnectionError("down")):
        with pytest.raises(HubSessionError, match="unreachable"):
            hubSession.ftSendHttpRequest("http://h", "tok", "GET", "/api/x")


def test_require_ok_returns_body_on_success():
    assert hubSession._fobjRequireOkResponse(
        (200, {"sToken": "abc"}), "Fetch",
    ) == {"sToken": "abc"}


def test_require_ok_raises_with_the_hubs_detail_field():
    with pytest.raises(HubSessionError, match="in use"):
        hubSession._fobjRequireOkResponse(
            (409, {"detail": "in use"}), "Claim",
        )


def test_require_ok_raises_with_raw_body_when_not_a_dict():
    with pytest.raises(HubSessionError, match="boom"):
        hubSession._fobjRequireOkResponse((500, "boom"), "Call")


# ------------------------------------------------------------------
# Session token, container id, claim, release
# ------------------------------------------------------------------


def test_redeem_host_lane_credential_exchanges_a_socket_capability():
    with patch.object(
        hubSession, "fsRequestBootstrapCapability", return_value="cap-1",
    ), patch.object(
        hubSession, "ftSendHttpRequest",
        return_value=(200, {"sCredential": "cred-1"}),
    ) as mockSend:
        assert hubSession.fsRedeemHostLaneCredential(
            8137, "http://h",
        ) == "cred-1"
    assert mockSend.call_args.args[3] == hubSession.S_BOOTSTRAP_ENDPOINT
    assert mockSend.call_args.args[4] == {"sCapability": "cap-1"}


def test_redeem_host_lane_credential_raises_when_absent():
    with patch.object(
        hubSession, "fsRequestBootstrapCapability", return_value="cap-1",
    ), patch.object(
        hubSession, "ftSendHttpRequest", return_value=(200, {}),
    ):
        with pytest.raises(HubSessionError, match="no.*credential"):
            hubSession.fsRedeemHostLaneCredential(8137, "http://h")


def test_mint_bootstrap_capability_surfaces_a_socket_refusal():
    with patch(
        "vaibify.gui.hostControlChannel.fdictSendHostControlRequest",
        return_value={"bAccepted": False, "sError": "no store"},
    ):
        with pytest.raises(HubSessionError, match="no store"):
            hubSession.fsRequestBootstrapCapability(8137)


def test_mint_bootstrap_capability_reports_an_unreachable_socket():
    from vaibify.gui.hostControlChannel import HostControlError
    with patch(
        "vaibify.gui.hostControlChannel.fdictSendHostControlRequest",
        side_effect=HostControlError("no socket for port 8137"),
    ):
        with pytest.raises(HubSessionError, match="host control socket"):
            hubSession.fsRequestBootstrapCapability(8137)


def test_resolve_container_id_returns_running_id():
    objBody = {"listContainers": [
        {"sName": "other", "sContainerId": "zzz"},
        {"sName": "mine", "sContainerId": "abc123"},
    ]}
    with patch.object(
        hubSession, "ftSendHttpRequest", return_value=(200, objBody),
    ):
        assert hubSession.fsResolveContainerId(
            "http://h", "tok", "mine",
        ) == "abc123"


def test_resolve_container_id_raises_when_not_running():
    objBody = {"listContainers": [{"sName": "mine", "sContainerId": ""}]}
    with patch.object(
        hubSession, "ftSendHttpRequest", return_value=(200, objBody),
    ):
        with pytest.raises(HubSessionError, match="is not running"):
            hubSession.fsResolveContainerId("http://h", "tok", "mine")


def test_resolve_container_id_raises_when_unknown():
    with patch.object(
        hubSession, "ftSendHttpRequest",
        return_value=(200, {"listContainers": []}),
    ):
        with pytest.raises(HubSessionError, match="does not know"):
            hubSession.fsResolveContainerId("http://h", "tok", "ghost")


def test_claim_container_returns_the_minted_lease():
    with patch.object(
        hubSession, "ftSendHttpRequest",
        return_value=(200, {"sLeaseId": "lease-9"}),
    ):
        assert hubSession.fsClaimContainer(
            "http://h", "tok", "mine",
        ) == "lease-9"


def test_claim_container_raises_when_no_lease_returned():
    with patch.object(
        hubSession, "ftSendHttpRequest", return_value=(200, {}),
    ):
        with pytest.raises(HubSessionError, match="returned no lease"):
            hubSession.fsClaimContainer("http://h", "tok", "mine")


def test_release_is_a_noop_without_a_lease():
    with patch.object(hubSession, "ftSendHttpRequest") as mockSend:
        hubSession.fnReleaseContainer(dict(_fdictSession(), sLeaseId=""))
    mockSend.assert_not_called()


def test_release_warns_when_the_send_fails(capsys):
    with patch.object(
        hubSession, "ftSendHttpRequest",
        side_effect=HubSessionError("gone"),
    ):
        hubSession.fnReleaseContainer(_fdictSession())
    assert "lease release failed" in capsys.readouterr().err


def test_release_warns_when_the_hub_still_holds_the_container(capsys):
    with patch.object(
        hubSession, "ftSendHttpRequest",
        return_value=(200, {"bReleased": False}),
    ):
        hubSession.fnReleaseContainer(_fdictSession())
    assert "still holds" in capsys.readouterr().err


# ------------------------------------------------------------------
# Workflow selection / connect / session assembly
# ------------------------------------------------------------------


def test_select_workflow_path_returns_the_explicit_value():
    assert hubSession.fsSelectWorkflowPath(
        _fdictSession(), "given/project.json",
    ) == "given/project.json"


def test_select_workflow_path_discovers_the_first_project():
    with patch.object(
        hubSession, "ftSendHttpRequest",
        return_value=(200, [{"sPath": "found/project.json"}]),
    ) as mockSend:
        assert hubSession.fsSelectWorkflowPath(
            _fdictSession(),
        ) == "found/project.json"
    assert mockSend.call_args.kwargs["sLeaseId"] == "lease-abc"


def test_select_workflow_path_raises_when_none_found():
    with patch.object(
        hubSession, "ftSendHttpRequest", return_value=(200, []),
    ):
        with pytest.raises(HubSessionError, match="No vaibify project"):
            hubSession.fsSelectWorkflowPath(_fdictSession())


def test_connect_workflow_records_the_selected_path():
    dictSession = _fdictSession()
    with patch.object(
        hubSession, "ftSendHttpRequest", return_value=(200, {}),
    ) as mockSend:
        hubSession.fnConnectWorkflow(dictSession, "p/project.json")
    assert dictSession["sWorkflowPath"] == "p/project.json"
    assert mockSend.call_args.kwargs["dictQuery"] == {
        "sWorkflowPath": "p/project.json",
    }
    assert mockSend.call_args.kwargs["sLeaseId"] == "lease-abc"


def test_inspect_hub_session_assembles_a_read_only_session():
    with patch.object(
        hubSession, "fiResolveHubPort", return_value=8137,
    ), patch.object(
        hubSession, "fsRedeemHostLaneCredential", return_value="cred",
    ), patch.object(
        hubSession, "fsResolveContainerId", return_value="cid",
    ):
        dictSession = hubSession.fdictInspectHubSession("mine", 8137)
    assert dictSession["sContainerId"] == "cid"
    assert dictSession["sLeaseId"] == ""


def test_open_researcher_session_claims_then_connects():
    with patch.object(
        hubSession, "fdictInspectHubSession", return_value=_fdictSession(),
    ), patch.object(
        hubSession, "fsClaimContainer", return_value="lease-new",
    ), patch.object(hubSession, "fnConnectWorkflow") as mockConnect:
        dictSession = hubSession.fdictOpenResearcherSession("mine")
    assert dictSession["sLeaseId"] == "lease-new"
    mockConnect.assert_called_once()


def test_open_researcher_session_releases_on_connect_failure():
    with patch.object(
        hubSession, "fdictInspectHubSession", return_value=_fdictSession(),
    ), patch.object(
        hubSession, "fsClaimContainer", return_value="lease-new",
    ), patch.object(
        hubSession, "fnConnectWorkflow",
        side_effect=HubSessionError("connect failed"),
    ), patch.object(hubSession, "fnReleaseContainer") as mockRelease:
        with pytest.raises(HubSessionError):
            hubSession.fdictOpenResearcherSession("mine")
    mockRelease.assert_called_once()


def test_resolve_step_label_returns_the_hubs_index():
    with patch.object(
        hubSession, "ftSendHttpRequest",
        return_value=(200, {"iStepIndex": 6}),
    ):
        assert hubSession.fiResolveStepLabel(_fdictSession(), "A07") == 6


# ------------------------------------------------------------------
# Payload printing and HTTP action exit codes
# ------------------------------------------------------------------


def test_print_payload_stringifies_a_scalar(capsys):
    hubSession._fnPrintPayload(42, False)
    assert capsys.readouterr().out.strip() == "42"


def test_print_payload_emits_one_json_line_when_requested(capsys):
    hubSession._fnPrintPayload({"b": 1, "a": 2}, True)
    assert capsys.readouterr().out.strip() == '{"b": 1, "a": 2}'


def test_print_payload_indents_when_not_json(capsys):
    hubSession._fnPrintPayload({"a": 1}, False)
    assert "\n" in capsys.readouterr().out


@pytest.mark.parametrize("iStatus,iExpected", [
    (200, 0), (204, 0), (400, 1), (404, 1), (500, 2), (503, 2),
])
def test_send_http_action_maps_status_to_exit_code(iStatus, iExpected, capsys):
    with patch.object(
        hubSession, "ftSendHttpRequest",
        return_value=(iStatus, {"ok": True}),
    ):
        iCode = hubSession.fiSendHttpAction(
            _fdictSession(), "POST", "/api/x", {}, False, 5.0,
        )
    assert iCode == iExpected
    assert "ok" in capsys.readouterr().out


# ------------------------------------------------------------------
# Pipeline WebSocket
# ------------------------------------------------------------------


def test_build_pipeline_socket_url_carries_credential_and_lease():
    sUrl = hubSession._fsBuildPipelineSocketUrl(_fdictSession())
    assert sUrl.startswith("ws://127.0.0.1:8137/ws/pipeline/0123456789ab")
    assert "sToken=browser-credential" in sUrl
    assert "sLeaseId=lease-abc" in sUrl


def test_print_pipeline_event_renders_a_short_line(capsys):
    hubSession._fnPrintPipelineEvent(
        {"sType": "output", "sLine": "hello", "iStep": 2}, False,
    )
    sOut = capsys.readouterr().out
    assert "[output]" in sOut and "sLine=hello" in sOut and "iStep=2" in sOut


def test_stream_pipeline_action_sends_payload_and_returns_exit_code():
    fakeConnection = MagicMock()
    fakeConnection.recv.return_value = json.dumps(
        {"sType": "completed", "iExitCode": 0}
    )
    fakeContext = MagicMock()
    fakeContext.__enter__.return_value = fakeConnection
    fakeContext.__exit__.return_value = False
    with patch("websockets.sync.client.connect", return_value=fakeContext):
        iCode = hubSession.fiStreamPipelineAction(
            _fdictSession(), {"sAction": "runAll"}, False, 5.0,
        )
    assert iCode == 0
    fakeConnection.send.assert_called_once()


def test_stream_pipeline_action_wraps_a_socket_failure():
    with patch(
        "websockets.sync.client.connect", side_effect=OSError("refused"),
    ):
        with pytest.raises(HubSessionError, match="pipeline socket failed"):
            hubSession.fiStreamPipelineAction(
                _fdictSession(), {"sAction": "runAll"}, False, 5.0,
            )


# -- CLI/hub credential contract -------------------------------------------
#
# ``testHubCredentialEndpointIsServed`` used to stand here: an
# xfail(strict) tripwire asserting that the retired
# ``/api/session-token`` oracle was still a route on the real hub app.
# It existed only to make the broken researcher lane visible in CI, and
# slice 8 discharged it — the CLI now bootstraps headlessly over the
# host control socket. Its replacement is not a route-existence check
# but the whole flow driven against real boundaries:
# ``tests/testVaibifyDoHeadless.py``.
