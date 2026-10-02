"""Tests for vaibify.docker.x11Forwarding with fake hosts (no real GUI)."""

import socket
from unittest.mock import MagicMock, patch

import pytest

from vaibify.docker import x11Forwarding
from vaibify.docker.x11Forwarding import (
    fbMacXServerAcceptingNetworkConnections,
    fdictAssessContainerX11,
    fdictAssessLinuxX11,
    fdictAssessMacX11,
    fdictFindMacXServer,
    fiResolveHostDisplayNumber,
    flistConfigureX11Args,
    fnConfigureLinuxX11,
    fnConfigureMacX11,
    fnGrantMacXhostAccess,
    fnRevokeMacXhostAccess,
    fnStartMacXServer,
    fsReadNetworkClientPreference,
    fsResolveMacContainerDisplay,
    _fbProcessIsRunning,
    _fnGrantLocalUserXhostAccess,
)

S_MACPORTS_APP = "/Applications/MacPorts/X11.app"
S_MACPORTS_DISPLAY = "/var/run/com.apple.launchd.AbCdEf/org.macports:0"
DICT_MACPORTS_SERVER = {
    "sProduct": "MacPorts X11", "sAppPath": S_MACPORTS_APP,
    "sPreferenceDomain": "org.macports.X11",
}
DICT_XQUARTZ_SERVER = {
    "sProduct": "XQuartz", "sAppPath": "/Applications/XQuartz.app",
    "sPreferenceDomain": "org.xquartz.X11",
}


@pytest.fixture(autouse=True)
def fnResetNoticeState():
    """Ensure each test starts with a fresh once-per-invocation notice set."""
    x11Forwarding._setNoticesShownThisInvocation.clear()
    yield
    x11Forwarding._setNoticesShownThisInvocation.clear()


def _fnInstallFakeMac(monkeypatch, setExistingDirectories=(),
                      setExistingFiles=(), dictMdfind=None,
                      sOnPath=None):
    """Make this process see a Mac that has exactly the given installs."""
    dictBundles = dictMdfind or {}
    monkeypatch.setattr(
        x11Forwarding.os.path, "isdir",
        lambda sPath: sPath in setExistingDirectories)
    monkeypatch.setattr(
        x11Forwarding.os.path, "isfile",
        lambda sPath: sPath in setExistingFiles)
    monkeypatch.setattr(
        x11Forwarding.shutil, "which", lambda sName: sOnPath)

    def fprocessMdfind(saArgs, **dictKeywords):
        sQuery = saArgs[1]
        for sBundleId, sPath in dictBundles.items():
            if sBundleId in sQuery:
                return MagicMock(returncode=0, stdout=sPath + "\n")
        return MagicMock(returncode=0, stdout="")

    monkeypatch.setattr(x11Forwarding.subprocess, "run", fprocessMdfind)


@pytest.mark.falsification
def test_findServer_recognizes_the_macports_application(monkeypatch):
    """Kills: declaring the MacPorts X11 application unknown to the detector."""
    _fnInstallFakeMac(monkeypatch, setExistingDirectories={S_MACPORTS_APP})
    dictServer = fdictFindMacXServer()
    assert dictServer == DICT_MACPORTS_SERVER


@pytest.mark.parametrize("sAppPath", [
    "/Applications/XQuartz.app", "/Applications/Utilities/XQuartz.app",
])
def test_findServer_recognizes_xquartz_at_both_paths(monkeypatch, sAppPath):
    _fnInstallFakeMac(monkeypatch, setExistingDirectories={sAppPath})
    dictServer = fdictFindMacXServer()
    assert dictServer["sProduct"] == "XQuartz"
    assert dictServer["sAppPath"] == sAppPath
    assert dictServer["sPreferenceDomain"] == "org.xquartz.X11"


def test_findServer_uses_the_path_spotlight_reports(monkeypatch):
    """A Homebrew cask or relocated install is launched from where it is."""
    sCaskPath = "/Users/someone/Applications/XQuartz.app"
    _fnInstallFakeMac(
        monkeypatch, dictMdfind={"org.xquartz.X11": sCaskPath})
    assert fdictFindMacXServer()["sAppPath"] == sCaskPath


def test_findServer_finds_macports_by_bundle_identifier(monkeypatch):
    sMoved = "/Users/someone/Applications/X11.app"
    _fnInstallFakeMac(
        monkeypatch, dictMdfind={"org.macports.X11": sMoved})
    dictServer = fdictFindMacXServer()
    assert dictServer["sProduct"] == "MacPorts X11"
    assert dictServer["sAppPath"] == sMoved


def test_findServer_accepts_a_bare_macports_binary(monkeypatch):
    _fnInstallFakeMac(
        monkeypatch, setExistingFiles={"/opt/local/bin/Xquartz"})
    dictServer = fdictFindMacXServer()
    assert dictServer["sAppPath"] == ""
    assert dictServer["sPreferenceDomain"] == "org.macports.X11"


def test_findServer_accepts_a_binary_on_the_path(monkeypatch):
    _fnInstallFakeMac(monkeypatch, sOnPath="/opt/X11/bin/Xquartz")
    assert fdictFindMacXServer()["sPreferenceDomain"] == "org.xquartz.X11"


def test_findServer_returns_none_when_nothing_is_installed(monkeypatch):
    _fnInstallFakeMac(monkeypatch)
    assert fdictFindMacXServer() is None


def test_findServer_survives_a_mac_without_mdfind(monkeypatch):
    _fnInstallFakeMac(monkeypatch)
    monkeypatch.setattr(
        x11Forwarding.subprocess, "run",
        MagicMock(side_effect=FileNotFoundError))
    assert fdictFindMacXServer() is None


@pytest.mark.falsification
@patch("vaibify.docker.x11Forwarding._fbWaitUntil")
@patch("vaibify.docker.x11Forwarding.subprocess.run")
@patch("vaibify.docker.x11Forwarding.fbMacXServerIsRunning")
def test_startServer_opens_the_found_application_path(
    mockRunning, mockRun, mockWait,
):
    """Kills: launching by the name XQuartz instead of the found app path."""
    """The old code ran ``open -a XQuartz``, which cannot find X11.app."""
    mockRunning.return_value = False
    fnStartMacXServer(DICT_MACPORTS_SERVER)
    assert mockRun.call_args[0][0] == ["open", "-a", S_MACPORTS_APP]


@patch("vaibify.docker.x11Forwarding.subprocess.run")
@patch("vaibify.docker.x11Forwarding.fbMacXServerIsRunning")
def test_startServer_leaves_a_running_server_alone(mockRunning, mockRun):
    mockRunning.return_value = True
    fnStartMacXServer(DICT_MACPORTS_SERVER)
    mockRun.assert_not_called()


@patch("vaibify.docker.x11Forwarding.subprocess.run")
@patch("vaibify.docker.x11Forwarding.fbMacXServerIsRunning")
def test_startServer_cannot_open_a_binary_without_an_application(
    mockRunning, mockRun,
):
    mockRunning.return_value = False
    fnStartMacXServer({
        "sProduct": "Xquartz binary", "sAppPath": "",
        "sPreferenceDomain": "org.macports.X11",
    })
    mockRun.assert_not_called()


def _fnFakeServerState(monkeypatch, bListening, sPreference, bRunning):
    monkeypatch.setattr(
        x11Forwarding, "fbMacXServerAcceptingNetworkConnections",
        lambda iPort=6000: bListening)
    monkeypatch.setattr(
        x11Forwarding, "fsReadNetworkClientPreference",
        lambda sDomain: sPreference)
    monkeypatch.setattr(
        x11Forwarding, "fbMacXServerIsRunning", lambda: bRunning)


def test_assess_ready_when_the_port_answers(monkeypatch):
    _fnFakeServerState(monkeypatch, True, "allowed", True)
    dictAssessment = fdictAssessMacX11(DICT_MACPORTS_SERVER, 6000)
    assert dictAssessment["bReady"] is True
    assert dictAssessment["sState"] == "ready"


def test_assess_names_the_install_when_no_server_exists():
    dictAssessment = fdictAssessMacX11(None, 6000)
    assert dictAssessment["sState"] == "no-server"
    assert dictAssessment["bReady"] is False
    assert "xquartz.org" in dictAssessment["sFix"]


@pytest.mark.falsification
@pytest.mark.parametrize("dictServer, sDomain", [
    (DICT_MACPORTS_SERVER, "org.macports.X11"),
    (DICT_XQUARTZ_SERVER, "org.xquartz.X11"),
])
def test_assess_blocked_names_the_servers_own_preference_domain(
    monkeypatch, dictServer, sDomain,
):
    """Kills: guiding every server to one hard-coded preference domain."""
    _fnFakeServerState(monkeypatch, False, "blocked", True)
    dictAssessment = fdictAssessMacX11(dictServer, 6000)
    assert dictAssessment["sState"] == "network-blocked"
    assert (
        f"defaults write {sDomain} nolisten_tcp -bool false"
        in dictAssessment["sFix"])


def test_assess_says_when_the_server_is_not_running(monkeypatch):
    _fnFakeServerState(monkeypatch, False, "allowed", False)
    dictAssessment = fdictAssessMacX11(DICT_MACPORTS_SERVER, 6000)
    assert dictAssessment["sState"] == "not-running"
    assert S_MACPORTS_APP in dictAssessment["sFix"]


def test_assess_says_when_a_running_server_is_not_listening(monkeypatch):
    _fnFakeServerState(monkeypatch, False, "unknown", True)
    dictAssessment = fdictAssessMacX11(DICT_MACPORTS_SERVER, 6001)
    assert dictAssessment["sState"] == "not-listening"
    assert "6001" in dictAssessment["sMessage"]


def test_assess_does_not_wait_when_the_preference_blocks_clients(
    monkeypatch,
):
    _fnFakeServerState(monkeypatch, False, "blocked", True)
    monkeypatch.setattr(
        x11Forwarding, "_fbWaitUntil",
        MagicMock(side_effect=AssertionError("waited for a blocked port")))
    fdictAssessMacX11(DICT_MACPORTS_SERVER, 6000, bWaitForPort=True)


@patch("vaibify.docker.x11Forwarding.subprocess.run")
def test_preference_reads_zero_as_allowed(mockRun):
    mockRun.return_value = MagicMock(returncode=0, stdout="0\n")
    assert fsReadNetworkClientPreference("org.macports.X11") == "allowed"
    assert mockRun.call_args[0][0] == [
        "defaults", "read", "org.macports.X11", "nolisten_tcp"]


@patch("vaibify.docker.x11Forwarding.subprocess.run")
def test_preference_reads_one_as_blocked(mockRun):
    mockRun.return_value = MagicMock(returncode=0, stdout="1\n")
    assert fsReadNetworkClientPreference("org.xquartz.X11") == "blocked"


@patch("vaibify.docker.x11Forwarding.subprocess.run")
def test_preference_is_unknown_when_unset(mockRun):
    mockRun.return_value = MagicMock(returncode=1, stdout="")
    assert fsReadNetworkClientPreference("org.xquartz.X11") == "unknown"


@patch("vaibify.docker.x11Forwarding.subprocess.run",
       side_effect=FileNotFoundError)
def test_preference_is_unknown_without_defaults(mockRun):
    assert fsReadNetworkClientPreference("org.xquartz.X11") == "unknown"


@patch.dict("os.environ", {"DISPLAY": S_MACPORTS_DISPLAY}, clear=True)
def test_display_number_comes_from_a_launchd_socket_path():
    assert fiResolveHostDisplayNumber() == 0
    assert fsResolveMacContainerDisplay() == "host.docker.internal:0"


@pytest.mark.falsification
@patch.dict(
    "os.environ",
    {"DISPLAY": "/private/tmp/com.apple.launchd.x/org.xquartz:3"},
    clear=True)
def test_display_number_three_selects_tcp_port_6003():
    """Kills: ignoring the host display number when choosing the TCP port."""
    assert fiResolveHostDisplayNumber() == 3
    assert fsResolveMacContainerDisplay() == "host.docker.internal:3"


@patch.dict("os.environ", {}, clear=True)
def test_display_number_defaults_to_zero():
    assert fiResolveHostDisplayNumber() == 0


@patch("vaibify.docker.x11Forwarding.socket.create_connection")
def test_port_probe_true_on_connect(mockConnect):
    mockConnect.return_value.__enter__ = lambda self: self
    mockConnect.return_value.__exit__ = lambda self, *args: None
    assert fbMacXServerAcceptingNetworkConnections(6003) is True
    assert mockConnect.call_args[0][0] == ("localhost", 6003)


@pytest.mark.parametrize("exceptionRaised", [
    ConnectionRefusedError, socket.timeout,
])
def test_port_probe_false_on_failure(exceptionRaised):
    with patch(
        "vaibify.docker.x11Forwarding.socket.create_connection",
        side_effect=exceptionRaised,
    ):
        assert fbMacXServerAcceptingNetworkConnections() is False


def _fnInstallConfigureFakes(monkeypatch, dictServer, bListening,
                             sPreference="allowed"):
    listRuns = []
    monkeypatch.setattr(
        x11Forwarding, "fdictFindMacXServer", lambda: dictServer)
    monkeypatch.setattr(
        x11Forwarding, "fbMacXServerIsRunning", lambda: False)
    monkeypatch.setattr(
        x11Forwarding, "_fbWaitUntil", lambda fbCondition, fSeconds: False)
    monkeypatch.setattr(
        x11Forwarding, "fbMacXServerAcceptingNetworkConnections",
        lambda iPort=6000: bListening)
    monkeypatch.setattr(
        x11Forwarding, "fsReadNetworkClientPreference",
        lambda sDomain: sPreference)
    monkeypatch.setattr(
        x11Forwarding, "_fnRunBestEffort",
        lambda saArgs: listRuns.append(saArgs))
    return listRuns


@patch.dict("os.environ", {"DISPLAY": S_MACPORTS_DISPLAY, "USER": "alice"})
def test_configureMac_on_a_macports_mac_starts_it_and_sets_display(
    monkeypatch, capsys,
):
    listRuns = _fnInstallConfigureFakes(
        monkeypatch, DICT_MACPORTS_SERVER, bListening=True)
    saRunArgs = []
    fnConfigureMacX11(saRunArgs)
    assert ["open", "-a", S_MACPORTS_APP] in listRuns
    assert saRunArgs == ["-e", "DISPLAY=host.docker.internal:0"]
    assert capsys.readouterr().err == ""


@patch.dict("os.environ", {"DISPLAY": "", "USER": "alice"})
def test_configureMac_with_no_server_sets_no_display_and_says_why(
    monkeypatch, capsys,
):
    listRuns = _fnInstallConfigureFakes(monkeypatch, None, bListening=False)
    saRunArgs = []
    fnConfigureMacX11(saRunArgs)
    assert saRunArgs == []
    assert listRuns == []
    sError = capsys.readouterr().err
    assert "not usable" in sError and "No X server" in sError


@patch.dict("os.environ", {"DISPLAY": S_MACPORTS_DISPLAY, "USER": "alice"})
def test_configureMac_when_clients_are_blocked_warns_with_the_fix(
    monkeypatch, capsys,
):
    _fnInstallConfigureFakes(
        monkeypatch, DICT_MACPORTS_SERVER, bListening=False,
        sPreference="blocked")
    saRunArgs = []
    fnConfigureMacX11(saRunArgs)
    sError = capsys.readouterr().err
    assert "defaults write org.macports.X11 nolisten_tcp" in sError
    assert "DISPLAY=host.docker.internal:0" in saRunArgs


def test_the_same_notice_is_printed_once_per_invocation(capsys):
    dictAssessment = fdictAssessMacX11(None, 6000)
    x11Forwarding._fnPrintAssessmentNotice(dictAssessment)
    x11Forwarding._fnPrintAssessmentNotice(dictAssessment)
    assert capsys.readouterr().err.count("No X server") == 1


@pytest.mark.falsification
@patch.dict("os.environ", {"USER": "alice"})
def test_macGrant_admits_the_local_user_and_the_tcp_loopback(monkeypatch):
    """Kills: dropping the TCP host entry, which locks the container out."""
    listRuns = []
    monkeypatch.setattr(
        x11Forwarding, "_fsFindXhost", lambda: "/opt/local/bin/xhost")
    monkeypatch.setattr(
        x11Forwarding, "_fnRunBestEffort",
        lambda saArgs: listRuns.append(saArgs))
    fnGrantMacXhostAccess()
    assert ["/opt/local/bin/xhost", "+SI:localuser:alice"] in listRuns
    assert ["/opt/local/bin/xhost", "+localhost"] in listRuns


def test_macRevoke_withdraws_the_tcp_loopback_entry(monkeypatch):
    listRuns = []
    monkeypatch.setattr(
        x11Forwarding, "_fsFindXhost", lambda: "xhost")
    monkeypatch.setattr(
        x11Forwarding, "_fnRunBestEffort",
        lambda saArgs: listRuns.append(saArgs))
    fnRevokeMacXhostAccess()
    assert listRuns == [["xhost", "-localhost"]]


def test_xhost_is_found_in_the_macports_directory_off_the_path(monkeypatch):
    monkeypatch.setattr(x11Forwarding.shutil, "which", lambda sName: None)
    monkeypatch.setattr(
        x11Forwarding.os.path, "isfile",
        lambda sPath: sPath == "/opt/local/bin/xhost")
    assert x11Forwarding._fsFindXhost() == "/opt/local/bin/xhost"


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
    assert flistConfigureX11Args() == []


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


@patch("vaibify.docker.x11Forwarding.subprocess.run",
       side_effect=FileNotFoundError)
@patch.dict("os.environ", {"USER": "alice"})
def test_grantLocalUserXhostAccess_tolerates_missing_xhost(mockRun):
    _fnGrantLocalUserXhostAccess()
    mockRun.assert_called_once()


@patch.dict("os.environ", {"USER": ""})
@patch("vaibify.docker.x11Forwarding.subprocess.run")
def test_grantLocalUserXhostAccess_no_op_when_user_unset(mockRun):
    _fnGrantLocalUserXhostAccess()
    mockRun.assert_not_called()


@patch("vaibify.docker.x11Forwarding.subprocess.run",
       side_effect=FileNotFoundError)
def test_processIsRunning_returns_false_when_pgrep_missing(mockRun):
    assert _fbProcessIsRunning("Xquartz") is False


@patch.dict("os.environ", {"DISPLAY": ""}, clear=True)
def test_linuxAssess_reports_a_session_without_display():
    dictAssessment = fdictAssessLinuxX11()
    assert dictAssessment["sState"] == "no-display"
    assert dictAssessment["bReady"] is False


@patch.dict("os.environ", {"DISPLAY": ":0"}, clear=True)
def test_linuxAssess_reports_a_missing_socket_directory(monkeypatch):
    monkeypatch.setattr(x11Forwarding.os.path, "isdir", lambda sPath: False)
    assert fdictAssessLinuxX11()["sState"] == "no-server"


@patch.dict("os.environ", {"DISPLAY": ":0"}, clear=True)
def test_linuxAssess_ready_with_display_and_socket(monkeypatch):
    monkeypatch.setattr(x11Forwarding.os.path, "isdir", lambda sPath: True)
    assert fdictAssessLinuxX11()["bReady"] is True


def test_containerAssess_without_display_demands_a_recreate():
    dictAssessment = fdictAssessContainerX11(False)
    assert dictAssessment["bReady"] is False
    assert "created without X11" in dictAssessment["sMessage"]
    assert "Stop and start" in dictAssessment["sFix"]


def test_containerAssess_with_display_is_ready():
    assert fdictAssessContainerX11(True)["bReady"] is True
