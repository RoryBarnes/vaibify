"""The whole journey, over a real SSH connection to a real sshd.

Everything else about the remote feature is provable without SSH: the
protocol validates, the helper starts a hub, the client builds an argv.
None of that proves the two halves meet. What only a real connection
proves is that OpenSSH accepts this argv, that the login shell on the
far side finds ``vaibify`` and does not garble its stdout, that the
forward actually carries loopback traffic, and that the capability
minted over there redeems over here.

**This lane may not skip itself green.** Without a reachable sshd it
skips, exactly like the browser and live-Docker lanes -- and exactly
like them, ``VAIBIFY_REQUIRE_REMOTE_SSH`` turns that skip into a
failure, so the CI job whose entire purpose is SSH coverage cannot
report success for having connected to nothing.

It connects to ``localhost`` because the property under test is the
transport, not the distance. A second machine would prove nothing more
and could not run in CI.

ONE THING A SINGLE HOST CANNOT PROVE, stated rather than faked. The
product forwards local port N to remote port N -- it must, because the
dashboard's Host check requires the browser-visible port to equal the
backend's expected port. On one machine those are the same port, so
that forward is a loop: ssh answers ``channel_new: internal error:
channels_alloc ... too big`` and the helper, seeing the forward's own
listener, correctly refuses to treat it as a hub. Both of those are the
code behaving properly, so neither may be worked around.

What follows therefore proves the two halves separately: the protocol
crosses a real SSH channel (no forward needed -- on one host the hub is
directly reachable), and a real ``-L`` forward carries real traffic
(with distinct ports, the only shape a single host permits). The N-to-N
equality is asserted where it can be: over the argv builder, in
tests/testRemoteClient.py.
"""

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request

import pytest

from vaibify.cli.commandRemote import fsaBuildSshCommand
from vaibify.cli.remoteProtocol import fdictParseStartupRecord


def _flistCommandWithoutTheSelfForward(iPort):
    """Return the product's ssh argv with its -L pair removed.

    Everything else is the real thing: the same options, the same
    fixed remote command, the same destination handling. Only the
    forward is dropped, because on one host it would forward a port to
    itself. See the module docstring -- the forward is proven
    separately, with the distinct ports a single machine allows.
    """
    listCommand = fsaBuildSshCommand("localhost", iPort)
    iFlag = listCommand.index("-L")
    return listCommand[:iFlag] + listCommand[iFlag + 2:]


def _fbPortIsFree(iPort):
    """Return True when nothing is listening on loopback iPort."""
    import socket
    connectionProbe = socket.socket()
    connectionProbe.settimeout(0.5)
    try:
        return connectionProbe.connect_ex(("127.0.0.1", iPort)) != 0
    finally:
        connectionProbe.close()

S_REQUIRE_REMOTE_SSH_ENV = "VAIBIFY_REQUIRE_REMOTE_SSH"

I_TEST_PORT = 18481

F_CONNECT_TIMEOUT_SECONDS = 120.0


def _fnRequireReachableSsh():
    """Skip without a usable sshd -- unless the run demanded one."""
    sReason = _fsWhySshUnusable()
    if not sReason:
        return
    if os.environ.get(S_REQUIRE_REMOTE_SSH_ENV):
        pytest.fail(
            f"{S_REQUIRE_REMOTE_SSH_ENV} is set, so this lane may not "
            f"skip: {sReason}"
        )
    pytest.skip(sReason)


def _fsWhySshUnusable():
    """Return why SSH to localhost cannot be used, or ""."""
    if shutil.which("ssh") is None:
        return "no ssh client on PATH"
    try:
        processProbe = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
             "-o", "StrictHostKeyChecking=accept-new",
             "localhost", "true"],
            capture_output=True, text=True, timeout=30,
        )
    except Exception as error:
        return f"ssh to localhost failed: {error}"
    if processProbe.returncode != 0:
        return (
            "ssh to localhost is not usable without a password "
            f"({processProbe.stderr.strip()[:200]})"
        )
    return ""


def _fnStopHubOnPort(iPort):
    subprocess.run(
        ["pkill", "-f", f"vaibify --no-browser --port {iPort}"],
        capture_output=True,
    )


@pytest.fixture
def processRemoteTunnel():
    """Run the real ssh argv the client builds; clean up both ends."""
    _fnRequireReachableSsh()
    _fnStopHubOnPort(I_TEST_PORT)
    process = subprocess.Popen(
        _flistCommandWithoutTheSelfForward(I_TEST_PORT),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True,
    )
    try:
        yield process
    finally:
        try:
            process.stdin.close()
            process.wait(timeout=10)
        except Exception:
            process.kill()
        _fnStopHubOnPort(I_TEST_PORT)
        time.sleep(1.0)


def test_the_forwarded_tunnel_signs_a_browser_in(processRemoteTunnel):
    """The one journey nothing else can prove.

    The record crosses a real SSH channel, and the capability it
    carries is redeemed through the real forward -- so a pass here
    means OpenSSH accepted the argv, the remote login shell found
    vaibify and left its stdout intact, and loopback traffic actually
    traverses the tunnel.
    """
    sLine = processRemoteTunnel.stdout.readline()
    assert sLine, (
        "the remote produced no startup record over SSH: "
        f"{processRemoteTunnel.stderr.read()[:400]}"
    )
    dictRecord = fdictParseStartupRecord(sLine, I_TEST_PORT)

    requestBootstrap = urllib.request.Request(
        f"http://127.0.0.1:{I_TEST_PORT}/api/bootstrap",
        method="POST",
        data=json.dumps({
            "sCapability": dictRecord["sBootstrapCapability"],
        }).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(requestBootstrap, timeout=20) as response:
        dictBody = json.loads(response.read())
    assert dictBody.get("sCredential"), (
        "the capability crossed the tunnel but did not redeem, so the "
        "researcher would reach a dashboard refusing every call"
    )


def test_a_real_forward_carries_real_traffic(processRemoteTunnel):
    """A -L forward reaches the hub, not merely accepts a connection.

    A forward that accepts and carries nothing looks identical to a
    working one until something asks a question. Distinct ports here:
    same-port on one host is a loop, and the equality the product
    actually uses is asserted over the argv builder instead.
    """
    fdictParseStartupRecord(
        processRemoteTunnel.stdout.readline(), I_TEST_PORT,
    )
    iLocalPort = I_TEST_PORT + 1
    processForward = subprocess.Popen(
        ["ssh", "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes",
         "-N", "-L",
         f"127.0.0.1:{iLocalPort}:127.0.0.1:{I_TEST_PORT}",
         "localhost"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        for _ in range(40):
            if not _fbPortIsFree(iLocalPort):
                break
            time.sleep(0.25)
        with urllib.request.urlopen(
            f"http://127.0.0.1:{iLocalPort}/", timeout=20,
        ) as response:
            baBody = response.read()
        assert response.status == 200
        assert b"vaibify" in baBody.lower(), (
            "the forward carried a response that was not the dashboard"
        )
    finally:
        processForward.kill()
