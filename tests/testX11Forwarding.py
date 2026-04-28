"""Tests for vaibify.docker.x11Forwarding with platform mocks."""

from unittest.mock import patch

from vaibify.docker.x11Forwarding import (
    flistConfigureX11Args,
    fnConfigureMacX11,
    fnConfigureLinuxX11,
    fnDisableX11Auth,
    fnStartXquartz,
    _fbProcessIsRunning,
    _fnGrantLocalUserXhostAccess,
)


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


@patch("vaibify.docker.x11Forwarding.fnDisableX11Auth")
@patch("vaibify.docker.x11Forwarding.fnStartXquartz")
def test_fnConfigureMacX11_sets_display(mockXquartz, mockAuth):
    saRunArgs = []
    fnConfigureMacX11(saRunArgs)
    assert "-e" in saRunArgs
    assert "DISPLAY=host.docker.internal:0" in saRunArgs


@patch("vaibify.docker.x11Forwarding._fnGrantLocalUserXhostAccess")
@patch.dict("os.environ", {"DISPLAY": ":1"})
def test_fnConfigureLinuxX11_sets_display(mockGrant):
    saRunArgs = []
    fnConfigureLinuxX11(saRunArgs)
    assert "DISPLAY=:1" in saRunArgs
    assert "/tmp/.X11-unix:/tmp/.X11-unix:ro" in saRunArgs


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
