"""Behaviour tests for the agent-action CLI dispatch layer.

vaibify/cli/actionCommands.py turns the shared action catalog into host
CLI commands: it builds WebSocket payloads and HTTP request paths from
positional/field arguments, resolves step labels, and dispatches over
the researcher session — releasing the lease no matter what. These tests
assert those transformations and the dispatch/dry-run/error control flow
with the hub session mocked, not a live hub.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from vaibify.cli import actionCommands as ac


_DICT_WS_ENTRY = {"sName": "run-from-step", "sPath": "runFrom",
                  "sMethod": "WS", "bAgentSafe": True,
                  "sDescription": "run from a step"}
_DICT_HTTP_ENTRY = {
    "sName": "view-falsification-attestation",
    "sPath": "/api/workflow/{sContainerId}/falsification/{iStepIndex}",
    "sMethod": "GET", "bAgentSafe": True, "sDescription": "view",
}
_DICT_SESSION = {"sContainerId": "cid-123",
                 "sBaseUrl": "http://127.0.0.1:8050"}


# --- step selectors ---

def test_run_from_step_selector_takes_a_label():
    dictPayload = {}
    ac._fnPopulateStepSelectors(dictPayload, "run-from-step", ["A03"])
    assert dictPayload["sStartStepLabel"] == "A03"


def test_run_from_step_selector_takes_a_numeric_index():
    dictPayload = {}
    ac._fnPopulateStepSelectors(dictPayload, "run-from-step", ["4"])
    assert dictPayload["iStartStep"] == 4


def test_run_selector_splits_labels_and_indices():
    dictPayload = {}
    ac._fnPopulateStepSelectors(
        dictPayload, "run-steps", ["A01", "3", "B02"])
    assert dictPayload["listStepLabels"] == ["A01", "B02"]
    assert dictPayload["listStepIndices"] == [3]


def test_selector_noop_without_positionals():
    dictPayload = {}
    ac._fnPopulateStepSelectors(dictPayload, "run-steps", [])
    assert dictPayload == {}


# --- payloads / paths ---

def test_websocket_payload_carries_run_mode_for_plots_only():
    dictEntry = {"sName": "run-plots-only", "sPath": "runAll"}
    dictPayload = ac.fdictBuildWebSocketPayload(dictEntry, [], {})
    assert dictPayload["sAction"] == "runAll"
    assert dictPayload["sRunMode"] == "plotsOnly"


def test_request_path_substitutes_container_and_values():
    sPath = ac.fsBuildRequestPath(
        _DICT_HTTP_ENTRY, _DICT_SESSION, {"iStepIndex": 2})
    assert sPath == "/api/workflow/cid-123/falsification/2"


def test_resolve_path_values_translates_a_step_label():
    with patch("vaibify.cli.actionCommands.hubSession."
               "fiResolveStepLabel", return_value=7) as mockResolve:
        dictValues = ac.fdictResolvePathValues(
            _DICT_SESSION, ["iStepIndex"], {"istepindex": "A08"})
    assert dictValues["iStepIndex"] == 7
    mockResolve.assert_called_once()


# --- dry run ---

def test_dry_run_describes_a_ws_call(capsys):
    dictParams = {"tfields": ()}
    ac._fnPrintDryRun(_DICT_WS_ENTRY, _DICT_SESSION, dictParams)
    dictOut = json.loads(capsys.readouterr().out)
    assert dictOut["sTransport"] == "WS"
    assert dictOut["dictPayload"]["sAction"] == "runFrom"


def test_dry_run_describes_an_http_call(capsys):
    dictParams = {"tfields": (), "istepindex": 2}
    ac._fnPrintDryRun(_DICT_HTTP_ENTRY, _DICT_SESSION, dictParams)
    dictOut = json.loads(capsys.readouterr().out)
    assert dictOut["sTransport"] == "HTTP"
    assert dictOut["sUrl"].endswith("/falsification/2")


# --- dispatch ---

def test_dispatch_ws_action_streams_payload():
    dictParams = {"tfields": (), "bJson": False, "fTimeoutSeconds": 30}
    with patch("vaibify.cli.actionCommands.hubSession."
               "fiStreamPipelineAction", return_value=0) as mockStream:
        iCode = ac._fiDispatchAction(
            _DICT_WS_ENTRY, _DICT_SESSION, dictParams)
    assert iCode == 0
    mockStream.assert_called_once()


def test_dispatch_http_action_sends_request():
    dictParams = {"tfields": (), "bJson": False,
                  "fTimeoutSeconds": 30, "istepindex": "2"}
    with patch("vaibify.cli.actionCommands.hubSession."
               "fiSendHttpAction", return_value=0) as mockSend:
        iCode = ac._fiDispatchAction(
            _DICT_HTTP_ENTRY, _DICT_SESSION, dictParams)
    assert iCode == 0
    mockSend.assert_called_once()


def test_dispatch_http_rejects_stray_positionals():
    dictParams = {"tfields": ("bogus",), "bJson": False,
                  "fTimeoutSeconds": 30}
    with pytest.raises(ac.hubSession.HubSessionError):
        ac._fiDispatchAction(_DICT_HTTP_ENTRY, _DICT_SESSION, dictParams)


def test_dispatch_under_lease_always_releases():
    dictParams = {"iPort": 8050, "sWorkflowPath": None,
                  "tfields": (), "bJson": False, "fTimeoutSeconds": 30}
    with patch("vaibify.cli.actionCommands.hubSession."
               "fdictOpenResearcherSession",
               return_value=_DICT_SESSION), \
         patch("vaibify.cli.actionCommands.hubSession."
               "fnReleaseContainer") as mockRelease, \
         patch("vaibify.cli.actionCommands._fiDispatchAction",
               side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError):
            ac._fnDispatchUnderLease(_DICT_WS_ENTRY, "proj", dictParams)
    mockRelease.assert_called_once(), "the lease must be released on error"


def test_run_catalog_action_reports_session_error_and_exits_4():
    dictParams = {"sProjectName": "proj", "bDryRun": False, "iPort": 8050}
    with patch("vaibify.cli.actionCommands._fsResolveContainerName",
               return_value="proj"), \
         patch("vaibify.cli.actionCommands._fnDispatchUnderLease",
               side_effect=ac.hubSession.HubSessionError("no hub")):
        with pytest.raises(SystemExit) as excinfo:
            ac.fnRunCatalogAction(_DICT_WS_ENTRY, dictParams)
    assert excinfo.value.code == 4


def test_command_help_flags_a_researcher_only_action():
    sHelp = ac._fsBuildCommandHelp({
        "bAgentSafe": False, "sDescription": "destroy things",
    })
    assert "Withheld from the in-container agent" in sHelp


def test_command_help_plain_for_agent_safe_action():
    sHelp = ac._fsBuildCommandHelp({
        "bAgentSafe": True, "sDescription": "run all",
    })
    assert sHelp == "run all"
