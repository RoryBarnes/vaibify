"""Tests for vaibify.docker.x11Forwarding with platform mocks."""

import socket
from unittest.mock import MagicMock, patch

import pytest

from vaibify.docker import x11Forwarding
from vaibify.docker.x11Forwarding import (
    flistConfigureX11Args,
    fnConfigureMacX11,
    fnConfigureLinuxX11,
    fnDisableX11Auth,
    fnStartXquartz,
    fbXquartzInstalled,
    fbXquartzAcceptingNetworkConnections,
    fsResolveMacContainerDisplay,
    _fbProcessIsRunning,
    _fnGrantLocalUserXhostAccess,
)


@pytest.fixture(autouse=True)
def fnResetNoticeState():
    """Ensure each test starts with a fresh once-per-invocation notice set."""
    x11Forwarding._setNoticesShownThisInvocation.clear()
    yield
    x11Forwarding._setNoticesShownThisInvocation.clear()


@patch("vaibify.docker.x11Forwarding.platform")
@patch("vaibify.docker.x11Forwarding.fnConfigureMacX11")
def test_flistConfigureX11Args_darwin(mockMac, mockPlatform):
    mockPlatform.system.return_value = "Darwin"
    flistConfigureX11Args()
    mockMac.assert_called_once()


@patch("vaibify.docker.x11Forwarding.platform")
@patch("vaibify.docker.x11Forwarding.fnConfigureLinuxX11")
def test_flistConfigureX11Args_linux(mockLinux, mockPlatform):
    mockPlatform.system.return_value = "Linux"
    flistConfigureX11Args()
    mockLinux.assert_called_once()


@patch("vaibify.docker.x11Forwarding.platform")
def test_flistConfigureX11Args_windows(mockPlatform):
    mockPlatform.system.return_value = "Windows"
    saArgs = flistConfigureX11Args()
    assert saArgs == []


@patch("vaibify.docker.x11Forwarding.fbXquartzAcceptingNetworkConnections")
@patch("vaibify.docker.x11Forwarding.fnDisableX11Auth")
@patch("vaibify.docker.x11Forwarding.fnStartXquartz")
@patch("vaibify.docker.x11Forwarding.fbXquartzInstalled")
@patch.dict("os.environ", {"DISPLAY": ""}, clear=False)
def test_fnConfigureMacX11_sets_display_when_xquartz_present(
    mockInstalled, mockXquartz, mockAuth, mockAccept
):
    mockInstalled.return_value = True
    mockAccept.return_value = True
    saRunArgs = []
    fnConfigureMacX11(saRunArgs)
    assert "-e" in saRunArgs
    assert "DISPLAY=host.docker.internal:0" in saRunArgs
    mockXquartz.assert_called_once()
    mockAuth.assert_called_once()


@patch("vaibify.docker.x11Forwarding.fnDisableX11Auth")
@patch("vaibify.docker.x11Forwarding.fnStartXquartz")
@patch("vaibify.docker.x11Forwarding.fbXquartzInstalled")
def test_fnConfigureMacX11_short_circuits_when_xquartz_missing(
    mockInstalled, mockXquartz, mockAuth, capsys
):
    mockInstalled.return_value = False
    saRunArgs = []
    fnConfigureMacX11(saRunArgs)
    assert saRunArgs == []
    mockXquartz.assert_not_called()
    mockAuth.assert_not_called()
    captured = capsys.readouterr()
    assert "XQuartz not installed" in captured.err


@patch("vaibify.docker.x11Forwarding.fbXquartzAcceptingNetworkConnections")
@patch("vaibify.docker.x11Forwarding.fnDisableX11Auth")
@patch("vaibify.docker.x11Forwarding.fnStartXquartz")
@patch("vaibify.docker.x11Forwarding.fbXquartzInstalled")
@patch.dict("os.environ", {"DISPLAY": ""}, clear=False)
def test_fnConfigureMacX11_warns_when_network_blocked(
    mockInstalled, mockXquartz, mockAuth, mockAccept, capsys
):
    mockInstalled.return_value = True
    mockAccept.return_value = False
    saRunArgs = []
    fnConfigureMacX11(saRunArgs)
    captured = capsys.readouterr()
    assert "blocks network clients" in captured.err
    assert "DISPLAY=host.docker.internal:0" in saRunArgs


@patch("vaibify.docker.x11Forwarding.fbXquartzAcceptingNetworkConnections")
@patch("vaibify.docker.x11Forwarding.fnDisableX11Auth")
@patch("vaibify.docker.x11Forwarding.fnStartXquartz")
@patch("vaibify.docker.x11Forwarding.fbXquartzInstalled")
@patch.dict("os.environ", {"DISPLAY": ""}, clear=False)
def test_fnConfigureMacX11_silent_when_network_accepting(
    mockInstalled, mockXquartz, mockAuth, mockAccept, capsys
):
    mockInstalled.return_value = True
    mockAccept.return_value = True
    fnConfigureMacX11([])
    captured = capsys.readouterr()
    assert "blocks network clients" not in captured.err


@patch("vaibify.docker.x11Forwarding._fnGrantLocalUserXhostAccess")
@patch.dict("os.environ", {"DISPLAY": ":1"})
def test_fnConfigureLinuxX11_sets_display(mockGrant):
    saRunArgs = []
    fnConfigureLinuxX11(saRunArgs)
    assert "DISPLAY=:1" in saRunArgs
    assert "/tmp/.X11-unix:/tmp/.X11-unix:ro" in saRunArgs


@patch("vaibify.docker.x11Forwarding._fnGrantLocalUserXhostAccess")
@patch.dict("os.environ", {}, clear=True)
def test_fnConfigureLinuxX11_defaults_display_when_unset(mockGrant):
    saRunArgs = []
    fnConfigureLinuxX11(saRunArgs)
    assert "DISPLAY=:0" in saRunArgs


@patch("vaibify.docker.x11Forwarding.subprocess.run", side_effect=FileNotFoundError)
@patch.dict("os.environ", {"USER": "alice"})
def test_grantLocalUserXhostAccess_tolerates_missing_xhost(mockRun):
    _fnGrantLocalUserXhostAccess()
    mockRun.assert_called_once()


@patch("vaibify.docker.x11Forwarding.subprocess.run", side_effect=FileNotFoundError)
def test_disableX11Auth_tolerates_missing_xhost(mockRun):
    fnDisableX11Auth()
    mockRun.assert_called_once()


@patch("vaibify.docker.x11Forwarding.subprocess.run", side_effect=FileNotFoundError)
def test_processIsRunning_returns_false_when_pgrep_missing(mockRun):
    assert _fbProcessIsRunning("Xquartz") is False


@patch("vaibify.docker.x11Forwarding.subprocess.run", side_effect=FileNotFoundError)
def test_startXquartz_tolerates_missing_binaries(mockRun):
    fnStartXquartz()


@patch("vaibify.docker.x11Forwarding.os.path.exists")
def test_fbXquartzInstalled_true_when_app_path_exists(mockExists):
    mockExists.return_value = True
    assert fbXquartzInstalled() is True
    mockExists.assert_called_with(x11Forwarding.XQUARTZ_APP_PATH)


@patch("vaibify.docker.x11Forwarding.subprocess.run")
@patch("vaibify.docker.x11Forwarding.os.path.exists")
def test_fbXquartzInstalled_false_when_no_app_and_no_spotlight(
    mockExists, mockRun
):
    mockExists.return_value = False
    mockRun.return_value = MagicMock(returncode=0, stdout="")
    assert fbXquartzInstalled() is False


@patch("vaibify.docker.x11Forwarding.subprocess.run")
@patch("vaibify.docker.x11Forwarding.os.path.exists")
def test_fbXquartzInstalled_true_via_spotlight_fallback(mockExists, mockRun):
    mockExists.return_value = False
    mockRun.return_value = MagicMock(
        returncode=0, stdout="/Applications/XQuartz.app\n"
    )
    assert fbXquartzInstalled() is True


@patch("vaibify.docker.x11Forwarding.subprocess.run", side_effect=FileNotFoundError)
@patch("vaibify.docker.x11Forwarding.os.path.exists")
def test_fbXquartzInstalled_false_when_mdfind_missing(mockExists, mockRun):
    mockExists.return_value = False
    assert fbXquartzInstalled() is False


@patch("vaibify.docker.x11Forwarding.socket.create_connection")
def test_fbXquartzAcceptingNetworkConnections_true_on_connect(mockConnect):
    mockConnect.return_value.__enter__ = lambda self: self
    mockConnect.return_value.__exit__ = lambda self, *args: None
    assert fbXquartzAcceptingNetworkConnections() is True


@patch(
    "vaibify.docker.x11Forwarding.socket.create_connection",
    side_effect=ConnectionRefusedError,
)
def test_fbXquartzAcceptingNetworkConnections_false_on_refused(mockConnect):
    assert fbXquartzAcceptingNetworkConnections() is False


@patch(
    "vaibify.docker.x11Forwarding.socket.create_connection",
    side_effect=socket.timeout,
)
def test_fbXquartzAcceptingNetworkConnections_false_on_timeout(mockConnect):
    assert fbXquartzAcceptingNetworkConnections() is False


@patch.dict("os.environ", {}, clear=True)
def test_fsResolveMacContainerDisplay_defaults_when_unset():
    assert fsResolveMacContainerDisplay() == "host.docker.internal:0"


@patch.dict("os.environ", {"DISPLAY": ":0"}, clear=True)
def test_fsResolveMacContainerDisplay_bare_zero():
    assert fsResolveMacContainerDisplay() == "host.docker.internal:0"


@patch.dict("os.environ", {"DISPLAY": "localhost:0"}, clear=True)
def test_fsResolveMacContainerDisplay_localhost():
    assert fsResolveMacContainerDisplay() == "host.docker.internal:0"


@patch.dict("os.environ", {"DISPLAY": "127.0.0.1:0"}, clear=True)
def test_fsResolveMacContainerDisplay_loopback_ip():
    assert fsResolveMacContainerDisplay() == "host.docker.internal:0"


@patch.dict("os.environ", {"DISPLAY": ":1.0"}, clear=True)
def test_fsResolveMacContainerDisplay_screen_suffix():
    assert fsResolveMacContainerDisplay() == "host.docker.internal:1.0"


@patch.dict("os.environ", {"DISPLAY": "somehost:2"}, clear=True)
def test_fsResolveMacContainerDisplay_replaces_arbitrary_host():
    assert fsResolveMacContainerDisplay() == "host.docker.internal:2"


def test_xquartz_missing_notice_printed_only_once_per_invocation(capsys):
    x11Forwarding._fnPrintXquartzMissingNotice()
    x11Forwarding._fnPrintXquartzMissingNotice()
    captured = capsys.readouterr()
    assert captured.err.count("XQuartz not installed") == 1


def test_xquartz_network_blocked_notice_printed_only_once_per_invocation(
    capsys,
):
    x11Forwarding._fnPrintXquartzNetworkBlockedNotice()
    x11Forwarding._fnPrintXquartzNetworkBlockedNotice()
    captured = capsys.readouterr()
    assert captured.err.count("blocks network clients") == 1
