"""Coverage tests for vaibify.cli.commandStart.fnLaunchGui."""

import sys
import types

import pytest
from unittest.mock import MagicMock, patch


def _fConfigStub():
    """Build a minimal resolved project config."""
    return MagicMock(
        sProjectName="demo", sWorkspaceRoot="/workspace",
    )


def test_fnLaunchGui_calls_uvicorn_with_app(capsys):
    """fnLaunchGui creates the app and starts uvicorn on port 8050."""
    from vaibify.cli.commandStart import fnLaunchGui
    config = _fConfigStub()
    mockApp = MagicMock(name="FastAPIApp")
    mockCreate = MagicMock(return_value=mockApp)
    mockUvicorn = MagicMock()
    mockUvicorn.run = MagicMock()
    mockPipelineServer = types.ModuleType(
        "vaibify.gui.pipelineServer",
    )
    mockPipelineServer.fappCreateApplication = mockCreate
    with patch.dict(sys.modules, {
        "vaibify.gui.pipelineServer": mockPipelineServer,
        "uvicorn": mockUvicorn,
    }):
        fnLaunchGui(config)
    mockCreate.assert_called_once_with(
        sWorkspaceRoot="/workspace", iExpectedPort=8050,
    )
    mockUvicorn.run.assert_called_once()
    tArgs, dictKwargs = mockUvicorn.run.call_args
    assert tArgs[0] is mockApp
    assert dictKwargs["host"] == "127.0.0.1"
    assert dictKwargs["port"] == 8050
    sOut = capsys.readouterr().out
    assert "workflow viewer" in sOut.lower()


def test_fnLaunchGui_uvicorn_failure_propagates():
    """When uvicorn.run raises, fnLaunchGui does not swallow it."""
    from vaibify.cli.commandStart import fnLaunchGui
    config = _fConfigStub()
    mockUvicorn = MagicMock()
    mockUvicorn.run.side_effect = OSError("port in use")
    mockPipelineServer = types.ModuleType(
        "vaibify.gui.pipelineServer",
    )
    mockPipelineServer.fappCreateApplication = MagicMock(
        return_value=MagicMock(),
    )
    with patch.dict(sys.modules, {
        "vaibify.gui.pipelineServer": mockPipelineServer,
        "uvicorn": mockUvicorn,
    }):
        with pytest.raises(OSError, match="port in use"):
            fnLaunchGui(config)


def test_fnLaunchGui_uses_workspace_root_from_config():
    """The workspace root passed to the app factory comes from config."""
    from vaibify.cli.commandStart import fnLaunchGui
    config = _fConfigStub()
    config.sWorkspaceRoot = "/custom/workspace"
    mockApp = MagicMock()
    mockCreate = MagicMock(return_value=mockApp)
    mockUvicorn = MagicMock()
    mockPipelineServer = types.ModuleType(
        "vaibify.gui.pipelineServer",
    )
    mockPipelineServer.fappCreateApplication = mockCreate
    with patch.dict(sys.modules, {
        "vaibify.gui.pipelineServer": mockPipelineServer,
        "uvicorn": mockUvicorn,
    }):
        fnLaunchGui(config)
    mockCreate.assert_called_once_with(
        sWorkspaceRoot="/custom/workspace", iExpectedPort=8050,
    )
