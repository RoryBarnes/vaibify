"""vaibify-do tells a busy host from an unreachable one, on real sockets.

A first Prompt Record pass kept the host busy for minutes; the agent's
client gave up after 60 s and reported "host unreachable", and the
agent asked the researcher to reconnect a container that was working
correctly. urllib wraps a failed CONNECT in ``URLError`` and raises a
bare timeout only after the request was sent, so these tests drive
real sockets rather than a mocked ``urlopen``: a mocked exception would
only restate the assumption under test.

Marked exclusive because the tests open real listening sockets, which
the falsification harness keeps off its parallel workers.
"""

import importlib.util
import socket
from pathlib import Path

import pytest

pytestmark = pytest.mark.exclusive

_S_VAIBIFY_DO_PATH = (
    Path(__file__).resolve().parent.parent
    / "vaibify" / "containerImage" / "vaibifyDo.py"
)


@pytest.fixture
def modCli():
    """Return a fresh import of vaibify/containerImage/vaibifyDo.py."""
    spec = importlib.util.spec_from_file_location(
        "vaibifyDoUnanswered", _S_VAIBIFY_DO_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fiFreePortNobodyListensOn():
    socketProbe = socket.socket()
    socketProbe.bind(("127.0.0.1", 0))
    iPort = socketProbe.getsockname()[1]
    socketProbe.close()
    return iPort


@pytest.mark.falsification
def test_a_host_that_accepts_but_does_not_answer_is_still_working(
    modCli, capsys, monkeypatch,
):
    """A real server that takes the request and stays silent exits 5.

    The wording matters as much as the code: an agent told "host
    unreachable" asked the researcher to reconnect a container whose
    first Prompt Record pass was simply still running.

    Kills: removing the bare-timeout clause, so an unanswered request
    falls through to "host unreachable".
    """
    socketServer = socket.socket()
    socketServer.bind(("127.0.0.1", 0))
    socketServer.listen(1)
    iPort = socketServer.getsockname()[1]
    monkeypatch.setattr(modCli, "F_READ_TIMEOUT", 0.5)
    sUrl = "http://127.0.0.1:" + str(iPort) + "/api/x"
    try:
        with pytest.raises(SystemExit) as excInfo:
            modCli.fiSendHttpRequest(
                {"sUrl": sUrl, "dictBody": {}}, "tok", "POST", False,
            )
    finally:
        socketServer.close()
    sStderr = capsys.readouterr().err
    assert excInfo.value.code == 5, sStderr
    assert "accepted the request" in sStderr
    assert "unreachable" not in sStderr.lower()
    assert sUrl in sStderr


def test_a_real_refused_connection_still_exits_unreachable(
    modCli, capsys, monkeypatch,
):
    """The new clause must not swallow a real failure to connect."""
    monkeypatch.setattr(modCli, "F_READ_TIMEOUT", 0.5)
    sUrl = "http://127.0.0.1:" + str(_fiFreePortNobodyListensOn()) + "/x"
    with pytest.raises(SystemExit) as excInfo:
        modCli.fiSendHttpRequest(
            {"sUrl": sUrl, "dictBody": {}}, "tok", "POST", False,
        )
    assert excInfo.value.code == 4
    assert "refused" in capsys.readouterr().err
