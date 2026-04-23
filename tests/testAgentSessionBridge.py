"""Tests for vaibify.gui.agentSessionBridge.

The bridge is called on every container connect to materialize the
session env file and the action-catalog JSON. Everything is driven
through the duck-typed ``connectionDocker`` shim
(``fnWriteFile`` + ``ftResultExecuteCommand``).
"""

import json

from unittest.mock import MagicMock

from vaibify.gui import actionCatalog
from vaibify.gui import agentSessionBridge


# -----------------------------------------------------------------------
# fsBuildSessionEnvBody
# -----------------------------------------------------------------------


def test_fsBuildSessionEnvBody_has_three_var_lines_in_order():
    sBody = agentSessionBridge.fsBuildSessionEnvBody(
        "http://host.docker.internal:8050",
        "tok-abc",
        "container-42",
    )
    listLines = sBody.split("\n")
    # Three VAR=value lines and a trailing empty from final newline.
    assert listLines[0] == "VAIBIFY_HOST_URL=http://host.docker.internal:8050"
    assert listLines[1] == "VAIBIFY_SESSION_TOKEN=tok-abc"
    assert listLines[2] == "VAIBIFY_CONTAINER_ID=container-42"
    assert listLines[3] == ""


def test_fsBuildSessionEnvBody_ends_with_newline():
    sBody = agentSessionBridge.fsBuildSessionEnvBody("h", "t", "c")
    assert sBody.endswith("\n")


# -----------------------------------------------------------------------
# fsBuildHostUrl
# -----------------------------------------------------------------------


def test_fsBuildHostUrl_uses_given_port():
    sUrl = agentSessionBridge.fsBuildHostUrl(9000)
    assert sUrl == "http://host.docker.internal:9000"


def test_fsBuildHostUrl_defaults_when_zero():
    sUrl = agentSessionBridge.fsBuildHostUrl(0)
    assert sUrl == "http://host.docker.internal:8050"


def test_fsBuildHostUrl_defaults_when_none():
    sUrl = agentSessionBridge.fsBuildHostUrl(None)
    assert sUrl == "http://host.docker.internal:8050"


# -----------------------------------------------------------------------
# fnWriteSessionEnv
# -----------------------------------------------------------------------


def test_fnWriteSessionEnv_writes_body_and_chmods():
    mockDocker = MagicMock()
    mockDocker.ftResultExecuteCommand.return_value = (0, "")

    agentSessionBridge.fnWriteSessionEnv(
        mockDocker, "c-1", "tok-xyz", 8050,
    )

    # Exactly one write with the expected path + body.
    mockDocker.fnWriteFile.assert_called_once()
    tArgs = mockDocker.fnWriteFile.call_args[0]
    assert tArgs[0] == "c-1"
    assert tArgs[1] == actionCatalog.S_SESSION_ENV_PATH
    sBody = tArgs[2].decode("utf-8")
    assert "VAIBIFY_HOST_URL=http://host.docker.internal:8050" in sBody
    assert "VAIBIFY_SESSION_TOKEN=tok-xyz" in sBody
    assert "VAIBIFY_CONTAINER_ID=c-1" in sBody

    # Followed by chmod 600.
    mockDocker.ftResultExecuteCommand.assert_called_once_with(
        "c-1", f"chmod 600 {actionCatalog.S_SESSION_ENV_PATH}",
    )


def test_fnWriteSessionEnv_write_precedes_chmod():
    mockDocker = MagicMock()
    listCallOrder = []

    def fnRecordWrite(*args, **kwargs):
        listCallOrder.append("write")

    def fnRecordExec(*args, **kwargs):
        listCallOrder.append("chmod")
        return (0, "")

    mockDocker.fnWriteFile.side_effect = fnRecordWrite
    mockDocker.ftResultExecuteCommand.side_effect = fnRecordExec

    agentSessionBridge.fnWriteSessionEnv(mockDocker, "c", "t", 8050)
    assert listCallOrder == ["write", "chmod"]


# -----------------------------------------------------------------------
# fnWriteActionCatalog
# -----------------------------------------------------------------------


def test_fnWriteActionCatalog_writes_json_at_shared_path():
    mockDocker = MagicMock()
    agentSessionBridge.fnWriteActionCatalog(mockDocker, "c-1")
    mockDocker.fnWriteFile.assert_called_once()
    tArgs = mockDocker.fnWriteFile.call_args[0]
    assert tArgs[0] == "c-1"
    assert tArgs[1] == actionCatalog.S_CATALOG_JSON_PATH
    dictCatalog = json.loads(tArgs[2].decode("utf-8"))
    assert dictCatalog["sSchemaVersion"] == (
        actionCatalog.S_CATALOG_SCHEMA_VERSION
    )
    assert isinstance(dictCatalog["listActions"], list)
    assert len(dictCatalog["listActions"]) == len(
        actionCatalog.LIST_AGENT_ACTIONS
    )


# -----------------------------------------------------------------------
# fnPushAgentSessionToContainer
# -----------------------------------------------------------------------


def test_fnPushAgentSessionToContainer_writes_env_then_catalog():
    mockDocker = MagicMock()
    mockDocker.ftResultExecuteCommand.return_value = (0, "")

    agentSessionBridge.fnPushAgentSessionToContainer(
        mockDocker, "c-9", "tok-orchid", 8050,
    )

    # Two writes: session env, then catalog.
    listPaths = [
        call.args[1]
        for call in mockDocker.fnWriteFile.call_args_list
    ]
    assert listPaths == [
        actionCatalog.S_SESSION_ENV_PATH,
        actionCatalog.S_CATALOG_JSON_PATH,
    ]


def test_fnPushAgentSessionToContainer_threads_port_through():
    mockDocker = MagicMock()
    mockDocker.ftResultExecuteCommand.return_value = (0, "")

    agentSessionBridge.fnPushAgentSessionToContainer(
        mockDocker, "c", "t", 9999,
    )

    sEnvBody = mockDocker.fnWriteFile.call_args_list[0].args[2].decode(
        "utf-8")
    assert "http://host.docker.internal:9999" in sEnvBody
