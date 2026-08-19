"""The remote helper, driven as a real subprocess against a real hub.

This is the slice's exit criterion and it is deliberately not a unit
test. What the helper does that nothing else does is speak a checkable
sentence to a program on another machine, and every interesting way
that fails -- the record never arriving, arriving with diagnostics
mixed into it, naming a port nobody forwarded, carrying a capability
that will not redeem -- is invisible to a stub that returns the record
the test already wrote.

So: the helper is launched, its stdout is parsed by the client's own
validator, and the capability it hands back is redeemed against the
hub it actually started, over HTTP. Then the hub is stopped, because
the helper deliberately does not stop it.
"""

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest

from vaibify.cli import remoteProtocol
from vaibify.cli.remoteProtocol import (
    RemoteProtocolError,
    fdictParseStartupRecord,
)

# Well outside anything vaibify picks by default, so a developer's own
# hub is never adopted or disturbed by this test.
I_TEST_PORT = 18479

F_RECORD_TIMEOUT_SECONDS = 90.0


def _fnStopHubOnPort(iPort):
    """Stop whatever hub this test started, by port."""
    subprocess.run(
        ["pkill", "-f", f"vaibify --no-browser --port {iPort}"],
        capture_output=True,
    )


def _fbPortIsFree(iPort):
    import socket
    connectionProbe = socket.socket()
    connectionProbe.settimeout(0.5)
    try:
        return connectionProbe.connect_ex(("127.0.0.1", iPort)) != 0
    finally:
        connectionProbe.close()


@pytest.fixture
def processHelper():
    """Run the helper, yield it, and always clean up its hub."""
    if not _fbPortIsFree(I_TEST_PORT):
        pytest.skip(f"port {I_TEST_PORT} is already in use")
    process = subprocess.Popen(
        [sys.executable, "-m", "vaibify", "remote-helper",
         "--port", str(I_TEST_PORT)],
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
        # The hub is detached, so its exit is not this process's to
        # await; give the socket a moment to clear for the next test.
        time.sleep(1.0)


def test_the_helper_starts_a_hub_and_its_capability_redeems(
    processHelper,
):
    """The whole point, proven against the running thing.

    A record that parses proves the helper's half. Redeeming the
    capability proves the hub it named is real, is the one on that
    port, and will actually sign a browser in -- which is what the
    researcher is about to ask it to do.
    """
    sLine = processHelper.stdout.readline()
    dictRecord = fdictParseStartupRecord(sLine, I_TEST_PORT)
    assert dictRecord["iPort"] == I_TEST_PORT
    assert dictRecord["sExecutionMode"] in remoteProtocol.T_EXECUTION_MODES
    assert dictRecord["sExecutionPlacement"] == "direct"

    requestBootstrap = urllib.request.Request(
        f"http://127.0.0.1:{I_TEST_PORT}/api/bootstrap",
        method="POST",
        data=json.dumps({
            "sCapability": dictRecord["sBootstrapCapability"],
        }).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(requestBootstrap, timeout=15) as response:
        dictBody = json.loads(response.read())
    assert dictBody.get("sCredential"), (
        "the capability the helper handed back did not redeem; the "
        "researcher would reach a dashboard that refuses every call"
    )



def test_only_the_record_reaches_stdout(processHelper):
    """Diagnostics must not corrupt the stream the client parses.

    The helper says several things while it works. Every one of them
    belongs on stderr: a single stray line on stdout lands in front of
    the record and the client refuses a perfectly good session.
    """
    sLine = processHelper.stdout.readline()
    assert sLine.startswith(remoteProtocol.S_STARTUP_RECORD_PREFIX), (
        f"the first stdout line was not the record: {sLine[:200]!r}"
    )
    # Everything the helper reported should be on the other stream.
    processHelper.stdin.close()
    processHelper.wait(timeout=15)
    sErrors = processHelper.stderr.read()
    assert "starting a vaibify hub" in sErrors or (
        "reusing the vaibify hub" in sErrors
    ), f"the helper's diagnostics did not reach stderr: {sErrors!r}"



def test_the_hub_outlives_the_helper(processHelper):
    """A closing laptop must not stop a running pipeline.

    The detachment is the feature, not an implementation accident, so
    it is asserted: the helper exits and the hub is still answering.
    """
    fdictParseStartupRecord(
        processHelper.stdout.readline(), I_TEST_PORT,
    )
    processHelper.stdin.close()
    processHelper.wait(timeout=15)
    assert not _fbPortIsFree(I_TEST_PORT), (
        "the hub died with its helper, so a dropped tunnel would take "
        "the researcher's run with it"
    )


def test_a_foreign_listener_is_refused_rather_than_forwarded_to():
    """Something else on the port is not silently treated as a hub."""
    import socket
    from vaibify.cli import commandRemoteHelper
    socketForeign = socket.socket()
    socketForeign.bind(("127.0.0.1", 0))
    socketForeign.listen(1)
    iPort = socketForeign.getsockname()[1]
    try:
        with pytest.raises(RuntimeError) as excinfo:
            commandRemoteHelper._fnRefuseForeignListener(iPort)
        assert "not a vaibify hub" in str(excinfo.value)
    finally:
        socketForeign.close()


def test_a_version_mismatch_refuses_to_adopt_the_hub(monkeypatch):
    """A live hub slot is not proof of a compatible hub.

    Before the version was written into the slot, this question had no
    answer at all: any running hub satisfied "a live pid on the right
    port", and the helper would have driven it with a protocol it may
    not share.
    """
    from vaibify.cli import commandRemoteHelper
    from vaibify.config import sessionRegistry
    monkeypatch.setattr(
        sessionRegistry, "fdictReadHubSlotByPort",
        lambda iPort: {"iPort": iPort, "sRole": "hub",
                       "sVaibifyVersion": "0.0.1-ancient"},
    )
    monkeypatch.setattr(
        sessionRegistry, "fsRunningVaibifyVersion", lambda: "9.9.9",
    )
    with pytest.raises(RuntimeError) as excinfo:
        commandRemoteHelper.fdictFindCompatibleHub(8050)
    sMessage = str(excinfo.value)
    assert "0.0.1-ancient" in sMessage and "9.9.9" in sMessage, (
        f"the refusal must name both versions: {sMessage}"
    )


def test_a_matching_version_is_adopted(monkeypatch):
    """The symmetric half: reuse must still work.

    Refusing every hub would satisfy the mismatch test above and make
    the feature useless, so the agreeing case is pinned beside it.
    """
    from vaibify.cli import commandRemoteHelper
    from vaibify.config import sessionRegistry
    monkeypatch.setattr(
        sessionRegistry, "fdictReadHubSlotByPort",
        lambda iPort: {"iPort": iPort, "sRole": "hub",
                       "sVaibifyVersion": "1.2.3"},
    )
    monkeypatch.setattr(
        sessionRegistry, "fsRunningVaibifyVersion", lambda: "1.2.3",
    )
    assert commandRemoteHelper.fdictFindCompatibleHub(8050)
