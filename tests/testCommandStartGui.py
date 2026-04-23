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


def _fdictPatchSysModules(mockCreate, mockUvicorn):
    """Build the sys.modules patch dict used across tests."""
    mockPipelineServer = types.ModuleType(
        "vaibify.gui.pipelineServer",
    )
    mockPipelineServer.fappCreateApplication = mockCreate
    return {
        "vaibify.gui.pipelineServer": mockPipelineServer,
        "uvicorn": mockUvicorn,
    }


def _fnPatchLockAndPort(iResolvedPort=8050):
    """Return patch context managers for lock and port helpers."""
    mockLockHandle = MagicMock()
    patchAcquire = patch(
        "vaibify.config.containerLock.fnAcquireContainerLock",
        return_value=mockLockHandle,
    )
    patchRelease = patch(
        "vaibify.config.containerLock.fnReleaseContainerLock",
    )
    patchResolvePort = patch(
        "vaibify.cli.commandStart.fiResolvePort",
        return_value=iResolvedPort,
    )
    return patchAcquire, patchRelease, patchResolvePort


def test_fnLaunchGui_calls_uvicorn_with_app(capsys):
    """fnLaunchGui creates the app and starts uvicorn on port 8050."""
    from vaibify.cli.commandStart import fnLaunchGui
    config = _fConfigStub()
    mockApp = MagicMock(name="FastAPIApp")
    mockCreate = MagicMock(return_value=mockApp)
    mockUvicorn = MagicMock()
    mockUvicorn.run = MagicMock()
    patchAcquire, patchRelease, patchResolvePort = _fnPatchLockAndPort()
    with patch.dict(
        sys.modules, _fdictPatchSysModules(mockCreate, mockUvicorn),
    ), patchAcquire, patchRelease, patchResolvePort:
        fnLaunchGui(config, None)
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
    mockCreate = MagicMock(return_value=MagicMock())
    patchAcquire, patchRelease, patchResolvePort = _fnPatchLockAndPort()
    with patch.dict(
        sys.modules, _fdictPatchSysModules(mockCreate, mockUvicorn),
    ), patchAcquire, patchRelease, patchResolvePort:
        with pytest.raises(OSError, match="port in use"):
            fnLaunchGui(config, None)


def test_fnLaunchGui_uses_workspace_root_from_config():
    """The workspace root passed to the app factory comes from config."""
    from vaibify.cli.commandStart import fnLaunchGui
    config = _fConfigStub()
    config.sWorkspaceRoot = "/custom/workspace"
    mockApp = MagicMock()
    mockCreate = MagicMock(return_value=mockApp)
    mockUvicorn = MagicMock()
    patchAcquire, patchRelease, patchResolvePort = _fnPatchLockAndPort()
    with patch.dict(
        sys.modules, _fdictPatchSysModules(mockCreate, mockUvicorn),
    ), patchAcquire, patchRelease, patchResolvePort:
        fnLaunchGui(config, None)
    mockCreate.assert_called_once_with(
        sWorkspaceRoot="/custom/workspace", iExpectedPort=8050,
    )


def test_fnLaunchGui_passes_explicit_port_to_uvicorn():
    """Explicit --port values thread through to uvicorn.run."""
    from vaibify.cli.commandStart import fnLaunchGui
    config = _fConfigStub()
    mockCreate = MagicMock(return_value=MagicMock())
    mockUvicorn = MagicMock()
    patchAcquire, patchRelease, patchResolvePort = _fnPatchLockAndPort(
        iResolvedPort=8062,
    )
    with patch.dict(
        sys.modules, _fdictPatchSysModules(mockCreate, mockUvicorn),
    ), patchAcquire, patchRelease, patchResolvePort:
        fnLaunchGui(config, 8062)
    mockCreate.assert_called_once_with(
        sWorkspaceRoot="/workspace", iExpectedPort=8062,
    )
    _, dictKwargs = mockUvicorn.run.call_args
    assert dictKwargs["port"] == 8062


def test_fnLaunchGui_exits_when_container_locked():
    """fnLaunchGui exits nonzero when the container lock is held."""
    from vaibify.cli.commandStart import fnLaunchGui
    from vaibify.config.containerLock import ContainerLockedError
    config = _fConfigStub()
    mockCreate = MagicMock(return_value=MagicMock())
    mockUvicorn = MagicMock()
    patchAcquire = patch(
        "vaibify.config.containerLock.fnAcquireContainerLock",
        side_effect=ContainerLockedError("demo", 4242, 8050),
    )
    patchResolvePort = patch(
        "vaibify.cli.commandStart.fiResolvePort", return_value=8050,
    )
    with patch.dict(
        sys.modules, _fdictPatchSysModules(mockCreate, mockUvicorn),
    ), patchAcquire, patchResolvePort:
        with pytest.raises(SystemExit) as exitInfo:
            fnLaunchGui(config, None)
    assert exitInfo.value.code == 1
    mockUvicorn.run.assert_not_called()


def test_fnLaunchGui_releases_lock_on_uvicorn_exit():
    """The lock is released whether uvicorn returns or raises."""
    from vaibify.cli.commandStart import fnLaunchGui
    config = _fConfigStub()
    mockCreate = MagicMock(return_value=MagicMock())
    mockUvicorn = MagicMock()
    mockLockHandle = MagicMock()
    mockRelease = MagicMock()
    patchAcquire = patch(
        "vaibify.config.containerLock.fnAcquireContainerLock",
        return_value=mockLockHandle,
    )
    patchRelease = patch(
        "vaibify.config.containerLock.fnReleaseContainerLock",
        mockRelease,
    )
    patchResolvePort = patch(
        "vaibify.cli.commandStart.fiResolvePort", return_value=8050,
    )
    with patch.dict(
        sys.modules, _fdictPatchSysModules(mockCreate, mockUvicorn),
    ), patchAcquire, patchRelease, patchResolvePort:
        fnLaunchGui(config, None)
    mockRelease.assert_called_once_with(mockLockHandle)


def test_start_command_without_gui_starts_container_only():
    """`vaibify start` without --gui starts the container, skips the GUI."""
    from click.testing import CliRunner
    from vaibify.cli.commandStart import start
    mockConfig = _fConfigStub()
    mockStart = MagicMock()
    mockLaunch = MagicMock()
    with patch(
        "vaibify.cli.commandStart.fconfigResolveProject",
        return_value=mockConfig,
    ), patch(
        "vaibify.cli.commandStart.fsDockerDir", return_value="/tmp/docker",
    ), patch(
        "vaibify.cli.commandStart._fnStartContainer", mockStart,
    ), patch(
        "vaibify.cli.commandStart.fnLaunchGui", mockLaunch,
    ):
        result = CliRunner().invoke(start, [])
    assert result.exit_code == 0
    mockStart.assert_called_once()
    mockLaunch.assert_not_called()


def test_start_command_with_gui_and_port_launches_gui():
    """`vaibify start --gui --port 8062` forwards the explicit port."""
    from click.testing import CliRunner
    from vaibify.cli.commandStart import start
    mockConfig = _fConfigStub()
    mockLaunch = MagicMock()
    with patch(
        "vaibify.cli.commandStart.fconfigResolveProject",
        return_value=mockConfig,
    ), patch(
        "vaibify.cli.commandStart.fsDockerDir", return_value="/tmp/docker",
    ), patch(
        "vaibify.cli.commandStart._fnStartContainer",
    ), patch(
        "vaibify.cli.commandStart.fnLaunchGui", mockLaunch,
    ):
        result = CliRunner().invoke(start, ["--gui", "--port", "8062"])
    assert result.exit_code == 0
    mockLaunch.assert_called_once_with(mockConfig, 8062)
