"""Tests for the catalog-generated host CLI (vaibify do).

Covers the mechanical translation from a catalog entry to a request:
placeholder extraction, field parsing, WebSocket payload shape, and the
event stream's terminal conditions. The lane guarantees (which header is
sent, which query the release carries) live in
``testCliHubSessionMutationCoverage.py`` as kill-confirmed tests.
"""

import json

import pytest

from vaibify.cli import hubSession
from vaibify.cli.actionCommands import (
    fbLooksLikeStepLabel,
    fdictBuildWebSocketPayload,
    flistArgumentPlaceholders,
    flistPathPlaceholders,
    fnCoerceFieldValue,
    fsBuildRequestPath,
    fsMetavarForPlaceholder,
    ftSplitFieldArguments,
    do,
)


def _fdictEntry(sName, sMethod, sPath):
    return {
        "sName": sName, "sMethod": sMethod, "sPath": sPath,
        "sCategory": "files", "bAgentSafe": True,
        "sDescription": "A probe entry. With a second sentence.",
    }


# ------------------------------------------------------------------
# Route-template parsing
# ------------------------------------------------------------------


def test_placeholders_drop_the_path_converter_suffix():
    assert flistPathPlaceholders(
        "/api/file/{sContainerId}/{sFilePath:path}"
    ) == ["sContainerId", "sFilePath"]


def test_placeholders_of_a_template_without_any_is_empty():
    assert flistPathPlaceholders("/api/registry") == []


def test_argument_placeholders_exclude_the_session_supplied_container():
    dictEntry = _fdictEntry(
        "probe", "POST", "/api/steps/{sContainerId}/{iStepIndex}/rename",
    )
    assert flistArgumentPlaceholders(dictEntry) == ["iStepIndex"]


def test_websocket_actions_take_no_path_arguments():
    assert flistArgumentPlaceholders(
        _fdictEntry("run-all", "WS", "runAll")
    ) == []


def test_metavar_strips_the_hungarian_prefix_for_unmapped_names():
    assert fsMetavarForPlaceholder("sSomething") == "SOMETHING"
    assert fsMetavarForPlaceholder("iStepIndex") == "STEP"


# ------------------------------------------------------------------
# Path building — the container ID, never the container name
# ------------------------------------------------------------------


def test_request_path_fills_a_path_converter_placeholder():
    sPath = fsBuildRequestPath(
        _fdictEntry("write", "PUT", "/api/file/{sContainerId}/{sFilePath:path}"),
        {"sContainerId": "abc", "sContainerName": "n"},
        {"sFilePath": "Step/out.csv"},
    )
    assert sPath == "/api/file/abc/Step/out.csv"


def test_request_path_percent_encodes_a_value():
    sPath = fsBuildRequestPath(
        _fdictEntry("verify", "POST", "/api/sync/{sContainerId}/{sService}/verify"),
        {"sContainerId": "abc", "sContainerName": "n"},
        {"sService": "git hub"},
    )
    assert sPath == "/api/sync/abc/git%20hub/verify"


# ------------------------------------------------------------------
# Field parsing
# ------------------------------------------------------------------


@pytest.mark.parametrize("sValue,objExpected", [
    ("true", True), ("FALSE", False), ("12", 12), ("1.5", 1.5),
    ("plain", "plain"), ('["a","b"]', ["a", "b"]),
    ("[not json", "[not json"),
])
def test_field_values_coerce_by_shape(sValue, objExpected):
    assert fnCoerceFieldValue(sValue) == objExpected


def test_field_arguments_split_into_positionals_and_body():
    listPositional, dictFields = ftSplitFieldArguments(
        ("A09", "sCategory=integrity", "iLines=20"),
    )
    assert listPositional == ["A09"]
    assert dictFields == {"sCategory": "integrity", "iLines": 20}


def test_a_json_object_argument_merges_into_the_body():
    _listPositional, dictFields = ftSplitFieldArguments(
        ('{"listFilePaths": ["Step/out.csv"]}',),
    )
    assert dictFields == {"listFilePaths": ["Step/out.csv"]}


# ------------------------------------------------------------------
# WebSocket payloads
# ------------------------------------------------------------------


def test_step_labels_are_recognized_but_step_names_are_not():
    assert fbLooksLikeStepLabel("A09")
    assert fbLooksLikeStepLabel("i1")
    assert not fbLooksLikeStepLabel("A0999")
    assert not fbLooksLikeStepLabel("Analysis")


def test_run_step_payload_sends_a_label_as_a_label():
    dictPayload = fdictBuildWebSocketPayload(
        _fdictEntry("run-step", "WS", "runSelected"), ["A09"], {},
    )
    assert dictPayload == {
        "sAction": "runSelected", "listStepLabels": ["A09"],
    }


def test_run_step_payload_sends_an_integer_as_an_index():
    dictPayload = fdictBuildWebSocketPayload(
        _fdictEntry("run-step", "WS", "runSelected"), ["7"], {},
    )
    assert dictPayload == {
        "sAction": "runSelected", "listStepIndices": [7],
    }


def test_run_data_only_payload_carries_the_run_mode():
    dictPayload = fdictBuildWebSocketPayload(
        _fdictEntry("run-data-only", "WS", "runSelected"), ["A01"], {},
    )
    assert dictPayload["sRunMode"] == "dataOnly"


def test_run_from_step_payload_uses_the_start_step_fields():
    assert fdictBuildWebSocketPayload(
        _fdictEntry("run-from-step", "WS", "runFrom"), ["A03"], {},
    )["sStartStepLabel"] == "A03"
    assert fdictBuildWebSocketPayload(
        _fdictEntry("run-from-step", "WS", "runFrom"), ["2"], {},
    )["iStartStep"] == 2


def test_body_fields_reach_the_websocket_payload():
    dictPayload = fdictBuildWebSocketPayload(
        _fdictEntry("run-all", "WS", "runAll"), [],
        {"bConfirmRemoteOverwrite": True},
    )
    assert dictPayload["bConfirmRemoteOverwrite"] is True


# ------------------------------------------------------------------
# Generated command surface
# ------------------------------------------------------------------


def test_researcher_only_actions_are_generated_like_any_other():
    """bAgentSafe restrains the in-container agent, not the researcher."""
    assert "clean-outputs" in do.commands
    assert "publish-to-zenodo" in do.commands
    assert "Withheld from the in-container agent" in (
        do.commands["clean-outputs"].help
    )


def test_every_generated_command_offers_the_session_options():
    commandAction = do.commands["run-unit-tests"]
    setNames = {parameter.name for parameter in commandAction.params}
    assert {"sProjectName", "iPort", "bJson", "bDryRun"} <= setNames


# ------------------------------------------------------------------
# Event streaming
# ------------------------------------------------------------------


class _FakeConnection:
    """Yields queued frames to the stream loop, one per recv()."""

    def __init__(self, listFrames):
        self.listFrames = list(listFrames)

    def recv(self, timeout=None):
        del timeout
        return self.listFrames.pop(0)


def test_stream_skips_heartbeats_and_returns_the_run_exit_code(capsys):
    iExitCode = hubSession._fiStreamUntilTerminalEvent(
        _FakeConnection([
            json.dumps({"sType": "wsHeartbeat"}),
            json.dumps({"sType": "output", "sLine": "working"}),
            json.dumps({"sType": "completed", "iExitCode": 3}),
        ]),
        False, 5.0,
    )
    sOut = capsys.readouterr().out
    assert iExitCode == 3
    assert "wsHeartbeat" not in sOut
    assert "working" in sOut


def test_stream_treats_a_refusal_as_a_failure():
    assert hubSession._fiStreamUntilTerminalEvent(
        _FakeConnection([
            json.dumps({
                "sType": "runRefused", "sReason": "remoteDataOverwrite",
            }),
        ]),
        True, 5.0,
    ) == 1


def test_stream_ignores_a_frame_that_is_not_json():
    assert hubSession._fiStreamUntilTerminalEvent(
        _FakeConnection([
            "not json at all",
            json.dumps({"sType": "completed", "iExitCode": 0}),
        ]),
        False, 5.0,
    ) == 0
