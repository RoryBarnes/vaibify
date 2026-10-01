"""Branch coverage for the two halves of a remote session.

``vaibify remote`` (the laptop half) and ``vaibify remote-helper`` (the
far half) talk across an SSH process and a hub's control socket. Both
of those are external boundaries, so each test here replaces exactly
one of them -- the ssh child, the hub spawn, the control socket, or
the bootstrap mint -- and drives the real command logic around it.

The observability contract is asserted throughout: every refusal exits
nonzero with a sentence, never a traceback, and the helper never writes
anything to stdout except its single protocol record.
"""

import io
import subprocess
import sys

import click
import pytest
from click.testing import CliRunner

from vaibify.cli import commandRemote, commandRemoteHelper, hubSession
from vaibify.cli.commandRemote import (
    F_RECONNECT_MARGIN_SECONDS,
    F_RECONNECT_WINDOW_SECONDS,
    RemoteClientError,
)
from vaibify.cli.remoteProtocol import (
    S_CAPABILITY_BOOTSTRAP,
    S_CAPABILITY_TRANSFER,
    RemoteProtocolError,
    fdictParseStartupRecord,
    fsFormatStartupRecord,
)
from vaibify.gui import hostControlChannel

I_TUNNEL_PORT = 18123
S_CAPABILITY = "c" * 43
S_OTHER_CAPABILITY = "t" * 43
S_PROJECT_NAME = "projectAlpha"
I_OWNER_GENERATION = 7


def frunnerSeparatingStreams():
    """Return a CliRunner whose result keeps stderr apart from stdout."""
    try:
        return CliRunner(mix_stderr=False)
    except TypeError:
        return CliRunner()


def fnAssertCleanExit(resultInvoke, iExpectedCode):
    """Assert the exit code and that no Python traceback escaped."""
    assert resultInvoke.exit_code == iExpectedCode, resultInvoke.output
    if resultInvoke.exception is not None:
        assert isinstance(resultInvoke.exception, SystemExit), (
            f"uncaught {resultInvoke.exception!r}"
        )
    sAllText = resultInvoke.stdout + (resultInvoke.stderr or "")
    assert "Traceback" not in sAllText


class _FakeStream:
    """A stdin/stdout/stderr stand-in with scripted reads."""

    def __init__(self, sText="", bReadRaises=False):
        self.bufferText = io.StringIO(sText)
        self.bReadRaises = bReadRaises
        self.bClosed = False

    def readline(self):
        return self.bufferText.readline()

    def read(self):
        if self.bReadRaises:
            raise OSError("stream torn down")
        return self.bufferText.read()

    def close(self):
        self.bClosed = True


class _FakeTunnelProcess:
    """An ssh child whose output, exit status and teardown are scripted."""

    def __init__(
        self, sStdout="", sStderr="", iReturnCode=255,
        bWaitRaises=False, bKillRaises=False,
    ):
        self.stdin = _FakeStream()
        self.stdout = _FakeStream(sStdout)
        self.stderr = _FakeStream(sStderr)
        self.returncode = None
        self.iScriptedReturnCode = iReturnCode
        self.bWaitRaises = bWaitRaises
        self.bKillRaises = bKillRaises
        self.iWaitCalls = 0
        self.bKilled = False

    def poll(self):
        self.returncode = self.iScriptedReturnCode
        return self.returncode

    def wait(self, timeout=None):
        self.iWaitCalls += 1
        if self.bWaitRaises:
            raise subprocess.TimeoutExpired("ssh", timeout)
        return self.iScriptedReturnCode

    def kill(self):
        self.bKilled = True
        if self.bKillRaises:
            raise OSError("already gone")


def fsValidRecordLine(sKind=S_CAPABILITY_BOOTSTRAP, sProject=""):
    """Return one well-formed startup record line for I_TUNNEL_PORT."""
    return fsFormatStartupRecord(
        I_TUNNEL_PORT, S_CAPABILITY, "host", "computeNode01",
        sCapabilityKind=sKind, sReattachedContainerName=sProject,
    ) + "\n"


# ---------------------------------------------------------------------
# commandRemote: port choice
# ---------------------------------------------------------------------


def testExplicitFreePortIsUsedVerbatim(monkeypatch):
    from vaibify.cli import portAllocator
    monkeypatch.setattr(portAllocator, "fbIsPortFree", lambda iPort: True)
    assert commandRemote.fiChooseLocalPort(I_TUNNEL_PORT) == I_TUNNEL_PORT


def testOmittedPortAsksTheAllocatorWithThePreferredDefault(monkeypatch):
    from vaibify.cli import portAllocator
    listPreferred = []

    def fiRecordPick(iPreferred):
        listPreferred.append(iPreferred)
        return 18777

    monkeypatch.setattr(portAllocator, "fiPickFreePort", fiRecordPick)
    assert commandRemote.fiChooseLocalPort(None) == 18777
    assert listPreferred == [18050]


# ---------------------------------------------------------------------
# commandRemote: tunnel launch and startup record
# ---------------------------------------------------------------------


def testTunnelLaunchPipesEveryStreamAndUsesTheBuiltArgv(monkeypatch):
    dictCaptured = {}

    def fprocessRecord(listArgv, **dictKeywords):
        dictCaptured["listArgv"] = listArgv
        dictCaptured["dictKeywords"] = dictKeywords
        return "processSentinel"

    monkeypatch.setattr(commandRemote.subprocess, "Popen", fprocessRecord)
    assert commandRemote._fprocessStartTunnel(
        "researcher@compute", I_TUNNEL_PORT,
    ) == "processSentinel"
    assert dictCaptured["listArgv"] == commandRemote.fsaBuildSshCommand(
        "researcher@compute", I_TUNNEL_PORT,
    )
    for sStream in ("stdin", "stdout", "stderr"):
        assert dictCaptured["dictKeywords"][sStream] == subprocess.PIPE
    assert dictCaptured["dictKeywords"]["text"] is True


def testSilentRemoteCarriesSshStatusAndStderrIntoTheError():
    processTunnel = _FakeTunnelProcess(
        sStdout="", sStderr="Permission denied (publickey).\n",
        iReturnCode=255,
    )
    with pytest.raises(RemoteClientError) as excinfo:
        commandRemote._fsReadStartupLine(processTunnel)
    sMessage = str(excinfo.value)
    assert "no vaibify startup record" in sMessage
    assert "status 255" in sMessage
    assert "Permission denied (publickey)." in sMessage


def testUnreadableStderrStillYieldsAnExplainedError():
    processTunnel = _FakeTunnelProcess(
        sStdout="", iReturnCode=1,
    )
    processTunnel.stderr = _FakeStream(bReadRaises=True)
    with pytest.raises(RemoteClientError, match="status 1"):
        commandRemote._fsReadStartupLine(processTunnel)


def testEstablishedSessionReturnsTheValidatedRecord(monkeypatch):
    processTunnel = _FakeTunnelProcess(sStdout=fsValidRecordLine())
    monkeypatch.setattr(
        commandRemote, "_fprocessStartTunnel",
        lambda sDestination, iPort: processTunnel,
    )
    processReturned, dictRecord = commandRemote.ftEstablishSession(
        "compute", I_TUNNEL_PORT,
    )
    assert processReturned is processTunnel
    assert dictRecord["sBootstrapCapability"] == S_CAPABILITY
    assert processTunnel.stdin.bClosed is False


def testUntrustworthyRecordStopsTheTunnelBeforeRaising(monkeypatch):
    sWrongPort = fsFormatStartupRecord(
        I_TUNNEL_PORT + 1, S_CAPABILITY, "host", "computeNode01",
    ) + "\n"
    processTunnel = _FakeTunnelProcess(sStdout=sWrongPort)
    monkeypatch.setattr(
        commandRemote, "_fprocessStartTunnel",
        lambda sDestination, iPort: processTunnel,
    )
    with pytest.raises(RemoteProtocolError, match="unreachable"):
        commandRemote.ftEstablishSession("compute", I_TUNNEL_PORT)
    assert processTunnel.stdin.bClosed is True
    assert processTunnel.iWaitCalls == 1


def testStopTunnelToleratesNone():
    commandRemote._fnStopTunnel(None)


def testStopTunnelKillsAProcessThatWillNotExit():
    processTunnel = _FakeTunnelProcess(bWaitRaises=True)
    commandRemote._fnStopTunnel(processTunnel)
    assert processTunnel.stdin.bClosed is True
    assert processTunnel.bKilled is True


def testStopTunnelSwallowsAFailedKill():
    processTunnel = _FakeTunnelProcess(bWaitRaises=True, bKillRaises=True)
    commandRemote._fnStopTunnel(processTunnel)
    assert processTunnel.bKilled is True


# ---------------------------------------------------------------------
# commandRemote: the reconnection ladder
# ---------------------------------------------------------------------


def testLadderGivesUpInsideTheHoldWindowWhenTheRemoteNeverReturns(
    monkeypatch, capsys,
):
    listSlept = []
    monkeypatch.setattr(commandRemote.time, "sleep", listSlept.append)

    def fnAlwaysDown(sDestination, iPort):
        raise RemoteClientError("ssh: connect to host compute: refused")

    monkeypatch.setattr(commandRemote, "ftEstablishSession", fnAlwaysDown)
    iStatus = commandRemote._fiHoldAndReconnect(
        _FakeTunnelProcess(), "compute", I_TUNNEL_PORT,
    )
    assert iStatus == 1
    assert listSlept, "the ladder never retried at all"
    assert sum(listSlept) <= (
        F_RECONNECT_WINDOW_SECONDS - F_RECONNECT_MARGIN_SECONDS
    )
    sErr = capsys.readouterr().err
    assert "still down: ssh: connect to host compute: refused" in sErr
    assert "the remote session has expired" in sErr


def testLadderResetsItsBackoffAfterASuccessfulReconnect(
    monkeypatch, capsys,
):
    listSlept = []
    monkeypatch.setattr(commandRemote.time, "sleep", listSlept.append)
    listOutcomes = ["down", "up"]

    def ftScriptedEstablish(sDestination, iPort):
        if listOutcomes and listOutcomes.pop(0) == "up":
            return _FakeTunnelProcess(), {}
        raise RemoteProtocolError("the line was not protocol")

    monkeypatch.setattr(
        commandRemote, "ftEstablishSession", ftScriptedEstablish,
    )
    iStatus = commandRemote._fiHoldAndReconnect(
        _FakeTunnelProcess(), "compute", I_TUNNEL_PORT,
    )
    assert iStatus == 1
    assert listSlept[:3] == [1.0, 2.0, 1.0], (
        "after a reconnect the backoff must start again from one second"
    )
    assert "Reconnected." in capsys.readouterr().err


# ---------------------------------------------------------------------
# commandRemote: the command body, and the click binding around it
# ---------------------------------------------------------------------


def testRemoteCommandIsInvokableThroughClick():
    """The click layer must hand the destination to the callback.

    A shell-looking destination is refused in the body without any
    tunnel, so reaching the body at all is observable as exit 2.
    """
    resultInvoke = frunnerSeparatingStreams().invoke(
        commandRemote.fnRemoteCommand, ["host;reboot"],
    )
    fnAssertCleanExit(resultInvoke, 2)
    assert "is not a plain [user@]host" in resultInvoke.stderr


def ftInvokeRemoteBody(sDestination, iExplicitPort):
    """Run the command through click; return (exit code, stdout, stderr)."""
    listArguments = [sDestination]
    if iExplicitPort is not None:
        listArguments += ["--port", str(iExplicitPort)]
    resultInvoke = frunnerSeparatingStreams().invoke(
        commandRemote.fnRemoteCommand, listArguments,
    )
    assert isinstance(resultInvoke.exception, (SystemExit, type(None))), (
        f"uncaught {resultInvoke.exception!r}"
    )
    return resultInvoke.exit_code, resultInvoke.stdout, resultInvoke.stderr


def testShellLookingDestinationExitsTwoWithASentence(capsys):
    iCode, _, sErr = ftInvokeRemoteBody("host;reboot", None)
    assert iCode == 2
    assert "is not a plain [user@]host" in sErr


def testTakenExplicitPortExitsTwoWithASentence(monkeypatch, capsys):
    from vaibify.cli import portAllocator
    monkeypatch.setattr(portAllocator, "fbIsPortFree", lambda iPort: False)
    iCode, _, sErr = ftInvokeRemoteBody("compute", I_TUNNEL_PORT)
    assert iCode == 2
    assert f"port {I_TUNNEL_PORT} is already in use" in sErr


def testUnreachableRemoteExitsOneWithTheReason(monkeypatch, capsys):
    from vaibify.cli import portAllocator
    monkeypatch.setattr(portAllocator, "fbIsPortFree", lambda iPort: True)
    monkeypatch.setattr(
        commandRemote, "_fprocessStartTunnel",
        lambda sDestination, iPort: _FakeTunnelProcess(
            sStderr="ssh: Could not resolve hostname compute\n",
        ),
    )
    iCode, sOut, sErr = ftInvokeRemoteBody("compute", I_TUNNEL_PORT)
    assert iCode == 1
    assert "Could not resolve hostname compute" in sErr
    assert "Connecting to compute" in sOut


def fdictRunConnectedSession(monkeypatch, capsys, dictRecord):
    """Drive a connected session to Ctrl-C; return what it did."""
    from vaibify.cli import main as moduleMain
    from vaibify.cli import portAllocator
    dictObserved = {"listUrls": [], "listStopped": []}
    processTunnel = _FakeTunnelProcess()
    monkeypatch.setattr(portAllocator, "fbIsPortFree", lambda iPort: True)
    monkeypatch.setattr(
        commandRemote, "ftEstablishSession",
        lambda sDestination, iPort: (processTunnel, dictRecord),
    )
    monkeypatch.setattr(
        moduleMain, "_fnOpenBrowserUnlessSuppressed",
        dictObserved["listUrls"].append,
    )

    def fnInterrupt(processHeld, sDestination, iPort):
        raise KeyboardInterrupt

    monkeypatch.setattr(commandRemote, "_fiHoldAndReconnect", fnInterrupt)
    monkeypatch.setattr(
        commandRemote, "_fnStopTunnel", dictObserved["listStopped"].append,
    )
    iCode, sOut, sErr = ftInvokeRemoteBody(
        "researcher@compute", I_TUNNEL_PORT,
    )
    dictObserved.update(
        {"iCode": iCode, "sOut": sOut, "sErr": sErr,
         "processTunnel": processTunnel},
    )
    return dictObserved


def testConnectedBootstrapSessionOpensTheBootstrapFragment(
    monkeypatch, capsys,
):
    dictRecord = fdictParseStartupRecord(fsValidRecordLine(), I_TUNNEL_PORT)
    dictObserved = fdictRunConnectedSession(monkeypatch, capsys, dictRecord)
    assert dictObserved["iCode"] == 0
    assert dictObserved["listUrls"] == [
        f"http://127.0.0.1:{I_TUNNEL_PORT}/#bootstrap={S_CAPABILITY}",
    ]
    sOut = dictObserved["sOut"]
    assert "Connected to computeNode01 (host mode)." in sOut
    assert "Picking up where you left off" not in sOut
    assert "Closing the tunnel" in sOut
    assert dictObserved["listStopped"] == [dictObserved["processTunnel"]]
    assert S_CAPABILITY not in sOut + dictObserved["sErr"], (
        "the capability was printed to the terminal"
    )


def testConnectedTransferSessionNamesTheProjectAndUsesTransfer(
    monkeypatch, capsys,
):
    dictRecord = fdictParseStartupRecord(
        fsValidRecordLine(S_CAPABILITY_TRANSFER, S_PROJECT_NAME),
        I_TUNNEL_PORT,
    )
    dictObserved = fdictRunConnectedSession(monkeypatch, capsys, dictRecord)
    assert dictObserved["iCode"] == 0
    assert dictObserved["listUrls"] == [
        f"http://127.0.0.1:{I_TUNNEL_PORT}/#transfer={S_CAPABILITY}",
    ]
    assert f"Picking up where you left off: {S_PROJECT_NAME}" in (
        dictObserved["sOut"]
    )


def testExpiredSessionPropagatesTheLadderStatus(monkeypatch, capsys):
    from vaibify.cli import main as moduleMain
    from vaibify.cli import portAllocator
    dictRecord = fdictParseStartupRecord(fsValidRecordLine(), I_TUNNEL_PORT)
    monkeypatch.setattr(portAllocator, "fbIsPortFree", lambda iPort: True)
    monkeypatch.setattr(
        commandRemote, "ftEstablishSession",
        lambda sDestination, iPort: (_FakeTunnelProcess(), dictRecord),
    )
    monkeypatch.setattr(
        moduleMain, "_fnOpenBrowserUnlessSuppressed", lambda sUrl: None,
    )
    monkeypatch.setattr(
        commandRemote, "_fiHoldAndReconnect",
        lambda processHeld, sDestination, iPort: 1,
    )
    iCode, _, _ = ftInvokeRemoteBody("compute", I_TUNNEL_PORT)
    assert iCode == 1


# ---------------------------------------------------------------------
# commandRemoteHelper: hub discovery, spawn and readiness
# ---------------------------------------------------------------------


def testNoHubSlotOnThePortMeansNothingToReuse():
    assert commandRemoteHelper.fdictFindCompatibleHub(I_TUNNEL_PORT) == {}


def testDetachedHubOutlivesTheHelperAndSuppressesTheBrowser(monkeypatch):
    from vaibify.gui.routes.sessionRoutes import S_SUPPRESS_BROWSER_ENV
    dictCaptured = {}

    def fprocessRecord(listArgv, **dictKeywords):
        dictCaptured["listArgv"] = listArgv
        dictCaptured["dictKeywords"] = dictKeywords
        return "hubSentinel"

    monkeypatch.setattr(subprocess, "Popen", fprocessRecord)
    assert commandRemoteHelper._fprocessStartDetachedHub(
        I_TUNNEL_PORT,
    ) == "hubSentinel"
    assert dictCaptured["listArgv"] == [
        sys.executable, "-m", "vaibify", "--no-browser",
        "--port", str(I_TUNNEL_PORT),
    ]
    dictKeywords = dictCaptured["dictKeywords"]
    assert dictKeywords["start_new_session"] is True
    assert dictKeywords["stdout"] == subprocess.DEVNULL
    assert dictKeywords["env"][S_SUPPRESS_BROWSER_ENV] == "1"
    assert dictKeywords["env"]["VAIBIFY_HUB_IDLE_TIMEOUT_SECONDS"] == str(
        commandRemoteHelper.F_REMOTE_HUB_IDLE_TIMEOUT_SECONDS,
    )


class _FakeHubProcess:
    """A spawned hub whose liveness is scripted."""

    def __init__(self, iExitCode=None):
        self.returncode = iExitCode

    def poll(self):
        return self.returncode


def testHubThatDiesDuringStartupIsReportedWithItsStatus(monkeypatch):
    monkeypatch.setattr(
        commandRemoteHelper, "fbPortAcceptsConnections", lambda iPort: False,
    )
    with pytest.raises(RuntimeError, match=r"exited during startup \(status 3\)"):
        commandRemoteHelper._fnAwaitHubReadiness(
            I_TUNNEL_PORT, _FakeHubProcess(iExitCode=3),
        )


def testHubThatNeverAnswersTimesOut(monkeypatch):
    monkeypatch.setattr(
        commandRemoteHelper, "fbPortAcceptsConnections", lambda iPort: False,
    )
    monkeypatch.setattr(
        commandRemoteHelper, "F_READINESS_TIMEOUT_SECONDS", 0.05,
    )
    monkeypatch.setattr(
        commandRemoteHelper, "F_READINESS_POLL_SECONDS", 0.01,
    )
    with pytest.raises(RuntimeError, match="did not become ready"):
        commandRemoteHelper._fnAwaitHubReadiness(
            I_TUNNEL_PORT, _FakeHubProcess(),
        )


def fnCreateControlSocketPlaceholder(iPort):
    """Create the file the readiness check looks for at iPort's socket."""
    import os
    sSocketPath = hostControlChannel.fsControlSocketPathForPort(iPort)
    os.makedirs(os.path.dirname(sSocketPath), exist_ok=True)
    with open(sSocketPath, "w") as fileHandle:
        fileHandle.write("")


def testReadinessNeedsBothThePortAndTheControlSocket(monkeypatch):
    monkeypatch.setattr(
        commandRemoteHelper, "fbPortAcceptsConnections", lambda iPort: True,
    )
    monkeypatch.setattr(
        commandRemoteHelper, "F_READINESS_TIMEOUT_SECONDS", 0.05,
    )
    monkeypatch.setattr(
        commandRemoteHelper, "F_READINESS_POLL_SECONDS", 0.01,
    )
    with pytest.raises(RuntimeError, match="did not become ready"):
        commandRemoteHelper._fnAwaitHubReadiness(I_TUNNEL_PORT, None)
    fnCreateControlSocketPlaceholder(I_TUNNEL_PORT)
    commandRemoteHelper._fnAwaitHubReadiness(I_TUNNEL_PORT, None)


# ---------------------------------------------------------------------
# commandRemoteHelper: capabilities
# ---------------------------------------------------------------------


def testMintDeclaresTheRemoteSession(monkeypatch):
    listCalls = []

    def fsRecordMint(iHubPort, bRemoteSession=False):
        listCalls.append((iHubPort, bRemoteSession))
        return S_CAPABILITY

    monkeypatch.setattr(
        hubSession, "fsRequestBootstrapCapability", fsRecordMint,
    )
    assert commandRemoteHelper.fsMintOneCapability(I_TUNNEL_PORT) == (
        S_CAPABILITY
    )
    assert listCalls == [(I_TUNNEL_PORT, True)]


def testMintRefusalBecomesARuntimeErrorWithTheHubsWords(monkeypatch):
    def fsRefuse(iHubPort, bRemoteSession=False):
        raise hubSession.HubSessionError("the hub is at its sign-in cap")

    monkeypatch.setattr(hubSession, "fsRequestBootstrapCapability", fsRefuse)
    with pytest.raises(RuntimeError, match="at its sign-in cap"):
        commandRemoteHelper.fsMintOneCapability(I_TUNNEL_PORT)


def fnScriptControlSocket(monkeypatch, listResponses):
    """Answer successive control requests from listResponses; record them."""
    listRequests = []

    def fdictAnswer(iPort, dictRequest):
        listRequests.append((iPort, dictRequest))
        objResponse = listResponses.pop(0)
        if isinstance(objResponse, Exception):
            raise objResponse
        return objResponse

    monkeypatch.setattr(
        hostControlChannel, "fdictSendHostControlRequest", fdictAnswer,
    )
    return listRequests


def testUnreachableControlSocketFallsBackToAFreshSignIn(monkeypatch):
    fnScriptControlSocket(monkeypatch, [
        hostControlChannel.HostControlError("socket gone"),
    ])
    assert commandRemoteHelper.ftOfferReattachment(I_TUNNEL_PORT) == (
        S_CAPABILITY_BOOTSTRAP, "", "",
    )


def testSeveralOrphansAreNotGuessedBetween(monkeypatch, capsys):
    fnScriptControlSocket(monkeypatch, [{"listReattachable": [
        {"sContainerName": "projectAlpha", "iOwnerGeneration": 1},
        {"sContainerName": "projectBeta", "iOwnerGeneration": 2},
    ]}])
    assert commandRemoteHelper.ftOfferReattachment(I_TUNNEL_PORT) == (
        S_CAPABILITY_BOOTSTRAP, "", "",
    )
    assert "2 sessions here are waiting" in capsys.readouterr().err


def testNoOrphanIsASilentFreshSignIn(monkeypatch, capsys):
    fnScriptControlSocket(monkeypatch, [{"listReattachable": []}])
    assert commandRemoteHelper.ftOfferReattachment(I_TUNNEL_PORT) == (
        S_CAPABILITY_BOOTSTRAP, "", "",
    )
    assert capsys.readouterr().err == ""


def testMintFailureAfterListingFallsBackToAFreshSignIn(monkeypatch):
    fnScriptControlSocket(monkeypatch, [
        {"listReattachable": [{
            "sContainerName": S_PROJECT_NAME,
            "iOwnerGeneration": I_OWNER_GENERATION,
        }]},
        hostControlChannel.HostControlError("socket closed mid-request"),
    ])
    assert commandRemoteHelper.ftOfferReattachment(I_TUNNEL_PORT) == (
        S_CAPABILITY_BOOTSTRAP, "", "",
    )


def testRefusedTransferIsExplainedAndFallsBack(monkeypatch, capsys):
    fnScriptControlSocket(monkeypatch, [
        {"listReattachable": [{
            "sContainerName": S_PROJECT_NAME,
            "iOwnerGeneration": I_OWNER_GENERATION,
        }]},
        {"bMinted": False, "sError": "the owner generation moved"},
    ])
    assert commandRemoteHelper.ftOfferReattachment(I_TUNNEL_PORT) == (
        S_CAPABILITY_BOOTSTRAP, "", "",
    )
    assert "the owner generation moved" in capsys.readouterr().err


def testSingleOrphanIsMintedAtTheGenerationItWasListedAt(monkeypatch):
    listRequests = fnScriptControlSocket(monkeypatch, [
        {"listReattachable": [{
            "sContainerName": S_PROJECT_NAME,
            "iOwnerGeneration": I_OWNER_GENERATION,
        }]},
        {"bMinted": True, "sTransferCapability": S_OTHER_CAPABILITY},
    ])
    assert commandRemoteHelper.ftOfferReattachment(I_TUNNEL_PORT) == (
        S_CAPABILITY_TRANSFER, S_OTHER_CAPABILITY, S_PROJECT_NAME,
    )
    iPort, dictMintRequest = listRequests[1]
    assert iPort == I_TUNNEL_PORT
    assert dictMintRequest == {
        "sOperation": hostControlChannel.S_SOCKET_OPERATION_MINT_TRANSFER,
        "sContainerName": S_PROJECT_NAME,
        "iExpectedOwnerGeneration": I_OWNER_GENERATION,
    }


# ---------------------------------------------------------------------
# commandRemoteHelper: what the far side reports and emits
# ---------------------------------------------------------------------


@pytest.mark.parametrize("objReachable, sExpected", [
    (True, "docker"), (False, "host"),
])
def testExecutionModeFollowsDaemonReachability(
    monkeypatch, objReachable, sExpected,
):
    import vaibify.docker
    monkeypatch.setattr(
        vaibify.docker, "fbDockerDaemonReachable", lambda: objReachable,
    )
    assert commandRemoteHelper.fsDescribeExecutionMode() == sExpected


def testExecutionModeIsHostWhenTheProbeItselfFails(monkeypatch):
    import vaibify.docker

    def fbExplode():
        raise OSError("docker socket permission denied")

    monkeypatch.setattr(vaibify.docker, "fbDockerDaemonReachable", fbExplode)
    assert commandRemoteHelper.fsDescribeExecutionMode() == "host"


def testEmittedRecordParsesAndDefaultsToABootstrap(monkeypatch, capsys):
    monkeypatch.setattr(
        commandRemoteHelper, "fsDescribeExecutionMode", lambda: "docker",
    )
    monkeypatch.setattr(
        commandRemoteHelper.socket, "gethostname", lambda: "computeNode01",
    )
    commandRemoteHelper._fnEmitStartupRecord(I_TUNNEL_PORT, S_CAPABILITY)
    sOut = capsys.readouterr().out
    assert sOut.count("\n") == 1
    dictRecord = fdictParseStartupRecord(sOut, I_TUNNEL_PORT)
    assert dictRecord["sCapabilityKind"] == S_CAPABILITY_BOOTSTRAP
    assert dictRecord["sExecutionMode"] == "docker"
    assert dictRecord["sHostname"] == "computeNode01"


@pytest.mark.parametrize("objStdin", [
    io.StringIO("keepalive\nkeepalive\n"),
    _FakeStream(),
])
def testChannelHoldReturnsWhenStdinEnds(monkeypatch, objStdin):
    monkeypatch.setattr(commandRemoteHelper.sys, "stdin", objStdin)
    commandRemoteHelper._fnHoldChannelOpen()
    assert objStdin.readline() == ""


def testChannelHoldReturnsWhenStdinBreaks(monkeypatch):
    class _BrokenStdin:
        def readline(self):
            raise OSError("broken pipe")

    monkeypatch.setattr(commandRemoteHelper.sys, "stdin", _BrokenStdin())
    commandRemoteHelper._fnHoldChannelOpen()


# ---------------------------------------------------------------------
# commandRemoteHelper: the click command end to end
# ---------------------------------------------------------------------


def testOutOfRangePortExitsOneWithNothingOnStdout():
    resultInvoke = frunnerSeparatingStreams().invoke(
        commandRemoteHelper.fnRemoteHelperCommand, ["--port", "70000"],
    )
    fnAssertCleanExit(resultInvoke, 1)
    assert resultInvoke.stdout == ""
    assert "port 70000 is not a usable TCP port" in resultInvoke.stderr


def fnPatchHelperBoundaries(monkeypatch, dictSlot, listControlResponses):
    """Replace the hub slot read, spawn, port probe and control socket."""
    from vaibify.config import sessionRegistry
    listSpawned = []
    monkeypatch.setattr(
        sessionRegistry, "fdictReadHubSlotByPort", lambda iPort: dictSlot,
    )

    def fprocessSpawn(iPort):
        listSpawned.append(iPort)
        return _FakeHubProcess()

    monkeypatch.setattr(
        commandRemoteHelper, "_fprocessStartDetachedHub", fprocessSpawn,
    )
    listPortAnswers = [] if dictSlot else [False]
    monkeypatch.setattr(
        commandRemoteHelper, "fbPortAcceptsConnections",
        lambda iPort: listPortAnswers.pop(0) if listPortAnswers else True,
    )
    fnCreateControlSocketPlaceholder(I_TUNNEL_PORT)
    fnScriptControlSocket(monkeypatch, listControlResponses)
    monkeypatch.setattr(
        commandRemoteHelper, "fsDescribeExecutionMode", lambda: "host",
    )
    monkeypatch.setattr(
        commandRemoteHelper.socket, "gethostname", lambda: "computeNode01",
    )
    return listSpawned


def testFreshHelperStartsAHubAndEmitsOneBootstrapRecord(monkeypatch):
    listSpawned = fnPatchHelperBoundaries(monkeypatch, {}, [
        hostControlChannel.HostControlError("no reattachment lane"),
    ])
    monkeypatch.setattr(
        hubSession, "fsRequestBootstrapCapability",
        lambda iHubPort, bRemoteSession=False: S_CAPABILITY,
    )
    resultInvoke = frunnerSeparatingStreams().invoke(
        commandRemoteHelper.fnRemoteHelperCommand,
        ["--port", str(I_TUNNEL_PORT)], input="",
    )
    fnAssertCleanExit(resultInvoke, 0)
    assert listSpawned == [I_TUNNEL_PORT]
    dictRecord = fdictParseStartupRecord(resultInvoke.stdout, I_TUNNEL_PORT)
    assert dictRecord["sBootstrapCapability"] == S_CAPABILITY
    assert dictRecord["sCapabilityKind"] == S_CAPABILITY_BOOTSTRAP
    assert "starting a vaibify hub" in resultInvoke.stderr
    assert "client hung up; the hub keeps running" in resultInvoke.stderr


def testHelperReusesAMatchingHubAndHandsBackTheOrphan(monkeypatch):
    from vaibify.config.sessionRegistry import fsRunningVaibifyVersion
    listSpawned = fnPatchHelperBoundaries(
        monkeypatch, {"sVaibifyVersion": fsRunningVaibifyVersion()}, [
            {"listReattachable": [{
                "sContainerName": S_PROJECT_NAME,
                "iOwnerGeneration": I_OWNER_GENERATION,
            }]},
            {"bMinted": True, "sTransferCapability": S_OTHER_CAPABILITY},
        ],
    )
    resultInvoke = frunnerSeparatingStreams().invoke(
        commandRemoteHelper.fnRemoteHelperCommand,
        ["--port", str(I_TUNNEL_PORT)], input="",
    )
    fnAssertCleanExit(resultInvoke, 0)
    assert listSpawned == [], "a matching hub was not reused"
    dictRecord = fdictParseStartupRecord(resultInvoke.stdout, I_TUNNEL_PORT)
    assert dictRecord["sCapabilityKind"] == S_CAPABILITY_TRANSFER
    assert dictRecord["sBootstrapCapability"] == S_OTHER_CAPABILITY
    assert dictRecord["sReattachedContainerName"] == S_PROJECT_NAME
    assert "reusing the vaibify hub" in resultInvoke.stderr
    assert f"picking the previous session back up: {S_PROJECT_NAME}" in (
        resultInvoke.stderr
    )


def testHelperFailureWritesNothingToTheProtocolStream(monkeypatch):
    fnPatchHelperBoundaries(monkeypatch, {}, [{"listReattachable": []}])

    def fsRefuse(iHubPort, bRemoteSession=False):
        raise hubSession.HubSessionError("the hub is at its sign-in cap")

    monkeypatch.setattr(hubSession, "fsRequestBootstrapCapability", fsRefuse)
    resultInvoke = frunnerSeparatingStreams().invoke(
        commandRemoteHelper.fnRemoteHelperCommand,
        ["--port", str(I_TUNNEL_PORT)], input="",
    )
    fnAssertCleanExit(resultInvoke, 1)
    assert resultInvoke.stdout == ""
    assert "error: the hub is at its sign-in cap" in resultInvoke.stderr
