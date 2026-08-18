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
"""

import pathlib
import re
from unittest.mock import patch

import pytest

from vaibify.cli import serverLaunch


PATH_PACKAGE_ROOT = pathlib.Path(serverLaunch.__file__).resolve().parents[1]

# The launcher itself is the one place uvicorn.run may be named.
S_LAUNCHER_RELATIVE_PATH = "cli/serverLaunch.py"


def test_the_launcher_configures_both_ping_settings():
    """Both halves reach uvicorn, as floats, on every launch."""
    with patch("uvicorn.run") as mockRun:
        serverLaunch.fnRunServer(object(), 8050)
    dictKwargs = mockRun.call_args[1]
    assert dictKwargs["ws_ping_interval"] == (
        serverLaunch.F_WEBSOCKET_PING_INTERVAL_SECONDS
    )
    assert dictKwargs["ws_ping_timeout"] == (
        serverLaunch.F_WEBSOCKET_PING_TIMEOUT_SECONDS
    )
    assert dictKwargs["host"] == "127.0.0.1", (
        "a vaibify server must bind loopback only; anything else "
        "exposes the dashboard to the network it is sitting on"
    )
    assert dictKwargs["log_config"] is None, (
        "uvicorn's dictConfig closes every attached handler, which "
        "silently killed file logging in every CLI-launched hub"
    )


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
    close, reintroduced somewhere nobody would think to look.
    """
    reCall = re.compile(r"\buvicorn\s*\.\s*run\s*\(")
    listOffenders = [
        sRelative
        for sRelative, pathSource in _flistPythonSourcePaths()
        if reCall.search(pathSource.read_text(encoding="utf-8"))
    ]
    assert listOffenders == [], (
        "these modules call uvicorn.run directly instead of going "
        f"through cli/serverLaunch.fnRunServer: {listOffenders}"
    )
