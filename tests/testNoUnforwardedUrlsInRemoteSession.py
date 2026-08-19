"""Every address the dashboard offers must be one the tunnel carries.

Through an SSH forward, exactly one port reaches the remote machine.
Anything the dashboard hands the browser on a DIFFERENT port resolves
against the laptop, where it is either nothing or -- worse -- an
unrelated local service. And the failure is misattributed: a dead tab
reads as a vaibify bug rather than as a boundary.

Same-origin URLs are safe by construction: they inherit the page's own
host and port, which IS the forward. So the property is narrow and
checkable -- any absolute address, any hardcoded port, and any
non-http scheme must either be gated on the session's lane or not
handed to the browser at all.

Jupyter is a non-issue rather than a solved one: `--jupyter` is
declared on the start command and never read again, selecting only an
image variant, so there is no published port to forward. Asserted
below so that changing it forces this file to be revisited.
"""

import pathlib
import re

import pytest

PATH_STATIC = (
    pathlib.Path(__file__).resolve().parent.parent
    / "vaibify" / "gui" / "static"
)
PATH_CLI = (
    pathlib.Path(__file__).resolve().parent.parent / "vaibify" / "cli"
)

# Handing the browser one of these means naming a host and port.
_RE_ABSOLUTE_OPEN = re.compile(
    r"""window\.open\(\s*["'](?:https?:)?//""",
)
_RE_HARDCODED_LOOPBACK = re.compile(r"""["']https?://127\.0\.0\.1:""")
_RE_CUSTOM_SCHEME_OPEN = re.compile(
    r"""window\.open\(\s*["'](?!https?:|//|/)[a-z][a-z0-9+.-]*:""",
)


def _flistStaticSources():
    return sorted(
        pathPath for pathPath in PATH_STATIC.glob("script*.js")
    )


def test_no_frontend_module_opens_a_hardcoded_loopback_address():
    """A literal 127.0.0.1 in the frontend is the laptop, always."""
    listOffenders = [
        pathSource.name
        for pathSource in _flistStaticSources()
        if _RE_HARDCODED_LOOPBACK.search(
            pathSource.read_text(encoding="utf-8"),
        )
    ]
    assert listOffenders == [], (
        "these modules name a loopback address, which through a tunnel "
        f"is the researcher's own machine: {listOffenders}"
    )


@pytest.mark.parametrize(
    "pathSource", _flistStaticSources(), ids=lambda p: p.name,
)
def test_every_browser_navigation_is_same_origin_or_lane_gated(
    pathSource,
):
    """An absolute or custom-scheme open must be gated on the lane.

    The two that existed are the reason this test does: New Window
    opened a hub on a port chosen AFTER the tunnel was built, and the
    VS Code deep link carried a container id from the remote daemon.
    Both are now hidden in a remote session.
    """
    sSource = pathSource.read_text(encoding="utf-8")
    bHasAbsolute = bool(
        _RE_ABSOLUTE_OPEN.search(sSource)
        or _RE_CUSTOM_SCHEME_OPEN.search(sSource)
    )
    if not bHasAbsolute:
        return
    assert (
        "fbIsRemoteSession" in sSource
        or "remote-session-badge" in sSource
        or "listRemoteHostile" in sSource
    ), (
        f"{pathSource.name} hands the browser an absolute address with "
        "nothing that consults the session's lane. Either derive it "
        "from window.location, or hide it in a remote session."
    )


def test_the_affordances_that_cannot_work_remotely_are_named_together():
    """One list, so a third one cannot be added and forgotten."""
    sSource = (PATH_STATIC / "scriptApplication.js").read_text(
        encoding="utf-8",
    )
    iList = sSource.index("listRemoteHostile")
    sBlock = sSource[iList:iList + 400]
    for sId in (
        "btnNewVaibifyWindow", "btnNewVaibifyWindowWorkflows", "btnVsCode",
    ):
        assert sId in sBlock, (
            f"{sId} is not in the remote-hostile list, so a remote "
            "session still offers it"
        )


def test_the_websockets_derive_their_origin_from_the_page():
    """Same-origin sockets need no forward of their own."""
    for sName in ("scriptWebSocket.js", "scriptTerminal.js"):
        sSource = (PATH_STATIC / sName).read_text(encoding="utf-8")
        assert "window.location.host" in sSource, (
            f"{sName} must build its socket URL from the page's own "
            "host, which is the forwarded port"
        )


def test_jupyter_is_still_inert_so_there_is_no_port_to_forward():
    """If this ever ships, this file needs a forward for it.

    `--jupyter` is declared and never read again: it selects an image
    variant and publishes nothing. Pinning the inertness is what makes
    the absence of a Jupyter forward honest rather than an oversight.
    """
    sStart = (PATH_CLI / "commandStart.py").read_text(encoding="utf-8")
    assert sStart.count("bJupyter") == 2, (
        "bJupyter is referenced more than twice, so it may now publish "
        "a port -- the remote lane needs an explicit forward for it, "
        "and this test needs rewriting rather than relaxing"
    )
