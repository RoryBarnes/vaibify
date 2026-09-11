"""Every vaibify server binds through one launcher, with ping configured.

A WebSocket peer that stops answering does not close its socket. A
sleeping laptop, a vanished network, or an SSH tunnel whose far end is
a still-open forwarder all leave a connection reading as established,
and protocol-level ping is the only thing that notices. Leaving it to
the library default meant the hub's live-connection count could stay
stuck at one forever, so the reconnect window never started and the
returning browser was refused as a duplicate tab by its own ghost.

The per-call-site assertion is not enough on its own: a fifth server
would simply not be covered by it. The source scan below is what makes
this a closed property.

The launcher also owns WHERE a server listens. On Linux a container
reaches the host at the Docker bridge gateway, not at loopback, and a
loopback-only hub refused every in-container ``vaibify-do`` call for
as long as the agent bridge existed. The bind-address tests below pin
both halves: Linux binds the gateway beside loopback, and macOS — whose
daemon VM forwards ``host.docker.internal`` to loopback — never does.
"""

import http.client
import pathlib
import platform
import re
import socket
import subprocess
import sys
import textwrap
import time
from unittest.mock import patch

import pytest

from vaibify.cli import serverLaunch

# The bind tests hold real ports, and the live leg runs a real server:
# a machine-global resource, so the falsification harness gives this
# file a lane to itself rather than running it under workers.
pytestmark = pytest.mark.exclusive


PATH_PACKAGE_ROOT = pathlib.Path(serverLaunch.__file__).resolve().parents[1]

# The launcher itself is the one place uvicorn may be bound.
S_LAUNCHER_RELATIVE_PATH = "cli/serverLaunch.py"

S_BRIDGE_GATEWAY = "172.17.0.1"


def _fdictRunLauncherWithDoubles(listAddresses):
    """Drive fnRunServer with uvicorn and the socket binder replaced.

    Returns the uvicorn Config kwargs, the sockets handed to the
    server, and the addresses the binder was asked for.
    """
    dictSeen = {}

    def fnFakeBind(listAsked, iPort):
        dictSeen["listAddresses"] = list(listAsked)
        return ["socket:" + sAddress for sAddress in listAsked]

    with patch.object(
        serverLaunch, "flistResolveBindAddresses",
        return_value=list(listAddresses),
    ), patch.object(
        serverLaunch, "flistBindServerSockets", side_effect=fnFakeBind,
    ), patch.object(
        serverLaunch, "_fnAnnounceBridgeBinding",
    ), patch("uvicorn.Config") as mockConfig, patch(
        "uvicorn.Server",
    ) as mockServer:
        serverLaunch.fnRunServer(object(), 8050)
    dictSeen["dictConfigKwargs"] = mockConfig.call_args[1]
    dictSeen["listSockets"] = (
        mockServer.return_value.run.call_args[1]["sockets"]
    )
    return dictSeen


def test_the_launcher_configures_both_ping_settings():
    """Both halves reach uvicorn, as floats, on every launch."""
    dictKwargs = _fdictRunLauncherWithDoubles(["127.0.0.1"])[
        "dictConfigKwargs"
    ]
    assert dictKwargs["ws_ping_interval"] == (
        serverLaunch.F_WEBSOCKET_PING_INTERVAL_SECONDS
    )
    assert dictKwargs["ws_ping_timeout"] == (
        serverLaunch.F_WEBSOCKET_PING_TIMEOUT_SECONDS
    )
    assert dictKwargs["host"] == "127.0.0.1", (
        "the config's host is descriptive (uvicorn logs it, never binds "
        "it once sockets are passed) and must stay truthful"
    )
    assert dictKwargs["log_config"] is None, (
        "uvicorn's dictConfig closes every attached handler, which "
        "silently killed file logging in every CLI-launched hub"
    )


def test_the_launcher_hands_every_bound_socket_to_uvicorn():
    """The server runs on the sockets the binder produced, all of them.

    Binding here and passing the list is the whole mechanism: a launcher
    that bound the sockets and then let uvicorn bind its own single
    ``host`` would listen on loopback alone with two dead sockets held
    open beside it.
    """
    dictSeen = _fdictRunLauncherWithDoubles(["127.0.0.1", S_BRIDGE_GATEWAY])
    assert dictSeen["listAddresses"] == ["127.0.0.1", S_BRIDGE_GATEWAY]
    assert dictSeen["listSockets"] == [
        "socket:127.0.0.1", "socket:" + S_BRIDGE_GATEWAY,
    ]


def test_the_ping_settings_are_positive_and_finite():
    """A zero or None interval disables detection entirely."""
    for fValue in (
        serverLaunch.F_WEBSOCKET_PING_INTERVAL_SECONDS,
        serverLaunch.F_WEBSOCKET_PING_TIMEOUT_SECONDS,
    ):
        assert isinstance(fValue, float) and fValue > 0.0, (
            "uvicorn treats a falsy ping interval as 'never ping', "
            "which is the unstated default this module replaced"
        )


# ── Which addresses a server binds ───────────────────────────────


def _flistAddressesOn(sPlatform, sGateway):
    """Resolve the bind list with the platform and daemon answer fixed."""
    with patch.object(
        serverLaunch.platform, "system", return_value=sPlatform,
    ), patch.object(
        serverLaunch, "fsResolveDockerBridgeGateway", return_value=sGateway,
    ):
        return serverLaunch.flistResolveBindAddresses()


@pytest.mark.falsification
def test_a_linux_server_binds_the_bridge_gateway_beside_loopback():
    """Linux: loopback first, then the gateway the daemon named.

    Kills: dropping the gateway from the bind list. That is the
    loopback-only bind every Linux hub had until 2026-09-10, under
    which a container's ``host.docker.internal`` dial is refused at
    the socket and every ``vaibify-do`` call fails — while the
    dashboard, on loopback, works perfectly.
    """
    assert _flistAddressesOn("Linux", S_BRIDGE_GATEWAY) == [
        "127.0.0.1", S_BRIDGE_GATEWAY,
    ]


def test_a_linux_server_without_a_daemon_answer_binds_loopback_only():
    """An empty gateway answer degrades to loopback; nothing is guessed."""
    assert _flistAddressesOn("Linux", "") == ["127.0.0.1"]


def test_a_macos_server_never_binds_the_gateway():
    """macOS: the daemon VM forwards to loopback, so the gateway is a hole.

    The resolver answering an address is not enough to bind it; the
    platform decides, because the second socket is an exposure with no
    traffic behind it anywhere a VM sits between container and host.
    """
    assert _flistAddressesOn("Darwin", S_BRIDGE_GATEWAY) == ["127.0.0.1"]


def test_loopback_is_always_first_and_never_duplicated():
    """A daemon whose gateway IS loopback yields one socket, not a clash."""
    assert _flistAddressesOn("Linux", "127.0.0.1") == ["127.0.0.1"]


# ── Binding the sockets ──────────────────────────────────────────


def _fiFreePort():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as socketProbe:
        socketProbe.bind(("127.0.0.1", 0))
        return socketProbe.getsockname()[1]


def test_a_gateway_the_kernel_refuses_degrades_to_loopback_with_a_warning(
    capsys, caplog,
):
    """The loopback socket survives a gateway that cannot be bound.

    An address no interface carries (the daemon stopped between naming
    it and now) must not take the whole server down, and must not
    vanish silently either: the warning names the address, because a
    silent degrade is exactly the defect the second socket fixes.
    """
    iPort = _fiFreePort()
    listSockets = serverLaunch.flistBindServerSockets(
        ["127.0.0.1", "192.0.2.1"], iPort,
    )
    try:
        listBound = [
            socketBound.getsockname()[0] for socketBound in listSockets
        ]
        assert listBound == ["127.0.0.1"]
    finally:
        for socketBound in listSockets:
            socketBound.close()
    assert "192.0.2.1" in capsys.readouterr().err
    assert any(
        "192.0.2.1" in recordLog.getMessage() for recordLog in caplog.records
    )


def test_a_loopback_port_already_held_still_raises():
    """The mandatory socket failing is the port-in-use error, unmasked."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as socketHolder:
        socketHolder.bind(("127.0.0.1", 0))
        socketHolder.listen(1)
        iPort = socketHolder.getsockname()[1]
        with pytest.raises(OSError):
            serverLaunch.flistBindServerSockets(["127.0.0.1"], iPort)


S_LIVE_SERVER_SCRIPT = textwrap.dedent('''
    import sys
    from unittest.mock import patch
    from vaibify.cli import serverLaunch

    async def fnApp(scope, receive, send):
        if scope["type"] != "http":
            return
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"text/plain")]})
        await send({"type": "http.response.body", "body": b"bound"})

    with patch.object(serverLaunch.platform, "system", return_value="Linux"), \\
         patch.object(serverLaunch, "fsResolveDockerBridgeGateway",
                      return_value=sys.argv[2]):
        serverLaunch.fnRunServer(fnApp, int(sys.argv[1]))
''')


def _fsGetOverHttp(sAddress, iPort):
    connectionHttp = http.client.HTTPConnection(sAddress, iPort, timeout=2)
    try:
        connectionHttp.request("GET", "/")
        return connectionHttp.getresponse().read().decode()
    finally:
        connectionHttp.close()


@pytest.mark.skipif(
    platform.system() != "Linux",
    reason="a second loopback address (127.0.0.2) is configured on Linux "
           "only; the macOS lane cannot bind two addresses without root",
)
def test_a_live_server_answers_on_every_address_it_bound():
    """A real uvicorn, launched the product's way, answers on BOTH addresses.

    The gateway is stood in by 127.0.0.2, which Linux carries on ``lo``
    without configuration. This is the test that exercises reality: the
    double-based tests above prove the list is built and handed over,
    and only a real accept proves uvicorn served the second socket.
    """
    iPort = _fiFreePort()
    processServer = subprocess.Popen(
        [sys.executable, "-c", S_LIVE_SERVER_SCRIPT, str(iPort), "127.0.0.2"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        fDeadline = time.monotonic() + 15.0
        sAnswer = ""
        while time.monotonic() < fDeadline and sAnswer != "bound":
            try:
                sAnswer = _fsGetOverHttp("127.0.0.1", iPort)
            except OSError:
                time.sleep(0.1)
        assert sAnswer == "bound", "the loopback socket never answered"
        assert _fsGetOverHttp("127.0.0.2", iPort) == "bound", (
            "the bridge-gateway stand-in was bound but uvicorn did not "
            "serve it"
        )
    finally:
        processServer.terminate()
        sOutput = processServer.communicate(timeout=10)[0]
    assert "http://127.0.0.2:" + str(iPort) in sOutput, (
        "the launcher must announce the agent-reachable address; a "
        "researcher learns of a loopback-only hub here or not at all"
    )


# ── One launcher ─────────────────────────────────────────────────


def _flistPythonSourcePaths():
    """Return every packaged Python source file, launcher excluded."""
    listPaths = []
    for pathSource in PATH_PACKAGE_ROOT.rglob("*.py"):
        sRelative = pathSource.relative_to(PATH_PACKAGE_ROOT).as_posix()
        if sRelative == S_LAUNCHER_RELATIVE_PATH:
            continue
        listPaths.append((sRelative, pathSource))
    return listPaths


@pytest.mark.falsification
def test_only_the_launcher_runs_uvicorn():
    """No module outside the launcher may bind a server itself.

    Kills: adding a fifth ``uvicorn.run`` call site. Such a site
    inherits none of the ping settings, so its sockets go back to
    being undetectably dead — the exact defect this module exists to
    close, reintroduced somewhere nobody would think to look. The scan
    covers the ``Server``/``Config`` spelling too, since the launcher
    itself now uses it.
    """
    reCall = re.compile(r"\buvicorn\s*\.\s*(run|Server|Config)\s*\(")
    listOffenders = [
        sRelative
        for sRelative, pathSource in _flistPythonSourcePaths()
        if reCall.search(pathSource.read_text(encoding="utf-8"))
    ]
    assert listOffenders == [], (
        "these modules call uvicorn directly instead of going "
        f"through cli/serverLaunch.fnRunServer: {listOffenders}"
    )
