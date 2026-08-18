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
    listCommand = fsaBuildSshCommand("localhost", I_TEST_PORT)
    process = subprocess.Popen(
        listCommand,
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


def test_the_dashboard_answers_through_the_forward(processRemoteTunnel):
    """Loopback traffic reaches the remote hub, not just the port.

    A forward that accepts a connection and carries nothing looks
    identical to a working one until something asks a question.
    """
    fdictParseStartupRecord(
        processRemoteTunnel.stdout.readline(), I_TEST_PORT,
    )
    with urllib.request.urlopen(
        f"http://127.0.0.1:{I_TEST_PORT}/", timeout=20,
    ) as response:
        baBody = response.read()
    assert response.status == 200
    assert b"vaibify" in baBody.lower(), (
        "the forward carried a response that was not the dashboard"
    )
