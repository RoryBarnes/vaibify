"""Alpha-facing truthfulness: the researcher is told what happened.

Each test here guards a place where the tool used to answer a real
question with silence or with a comfortable fiction. None of them is
about a crash; all of them are about the dashboard or the CLI saying
something other than the truth, which is the failure mode this
repository treats as the serious one.
"""

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = REPO_ROOT / "vaibify" / "gui" / "static"


def test_a_refused_transfer_is_reported_with_the_servers_own_reason():
    """The 'vaibify open' landing must not swallow a refusal.

    The exchange returned "" for every non-transferred outcome, so the
    tab fell back to a stored credential -- or to none -- and the
    researcher who had just run 'vaibify open' saw a dashboard that was
    simply not attached to their container, with no reason given. Every
    refusal the server sends carries an sMessage naming its recovery
    (retry, re-mint, claim normally, reconcile); that is the message to
    show.
    """
    sSource = (STATIC_DIR / "scriptApplication.js").read_text()
    iExchange = sSource.find("_fsExchangeTransferCapability")
    iNextFunction = sSource.find("async function fnFetchSessionToken")
    sExchange = sSource[iExchange:iNextFunction]
    assert "_fnReportTransferRefusal" in sExchange, (
        "the transfer exchange still discards its refusal"
    )
    assert sExchange.count("_fnReportTransferRefusal") >= 2, (
        "both the refused-outcome path and the network-failure path "
        "must report; a silent catch is the same defect"
    )
    iReporter = sSource.find("function _fnReportTransferRefusal")
    sReporter = sSource[iReporter:iReporter + 900]
    assert ".sMessage" in sReporter, (
        "the reporter must show the SERVER's reason, not a generic "
        "line that hides which refusal happened"
    )


def test_the_pagehide_handler_sends_no_release_anywhere():
    """The handler stops polling and nothing else.

    pagehide fires on reload and navigation, not only on a real close,
    so treating it as release intent drops a running container every
    time the researcher refreshes. Several documents described a
    sendBeacon release that the frontend deliberately does not send --
    prose that would talk the next reader into building it.
    """
    sSource = (STATIC_DIR / "scriptApplication.js").read_text()
    iHandler = sSource.find('addEventListener("pagehide"')
    assert iHandler != -1, "the pagehide handler is gone entirely"
    sHandler = sSource[iHandler:iHandler + 700]
    for sCall in ("sendBeacon", "fetch(", "/api/registry", "VaibifyApi."):
        assert sCall not in sHandler, (
            f"the pagehide handler calls {sCall}; it must stop polling "
            f"and nothing else"
        )
    for pathDocument in (
        STATIC_DIR / "AGENTS.md",
        REPO_ROOT / "docs" / "dashboard.md",
        REPO_ROOT / "vaibify" / "gui" / "registryRoutes.py",
    ):
        sText = pathDocument.read_text()
        if "pagehide" not in sText:
            continue
        assert "sendBeacon" not in sText or "no release" in sText, (
            f"{pathDocument.name} still describes a pagehide release "
            f"beacon the frontend does not send"
        )


def test_no_frontend_path_force_releases_a_container():
    """Force must not be reachable from a button.

    Force overrides ONLY the agent-liveness refusal -- a live run, a
    live guarded mutation, and a poisoned record all still refuse. That
    is a safe server-side flag and an unsafe UI action: a button cannot
    show the researcher which of those it is about to override, so
    exposing one would invite them to click past a refusal they have
    not read. The flag stays reachable from the CLI, where the caller
    types it deliberately.
    """
    listOffenders = []
    for pathScript in STATIC_DIR.glob("*.js"):
        sSource = pathScript.read_text()
        for sLine in sSource.splitlines():
            if "bForce" not in sLine:
                continue
            if "release" in sLine.lower():
                listOffenders.append(f"{pathScript.name}: {sLine.strip()}")
    assert listOffenders == [], (
        f"a frontend path force-releases a container: {listOffenders}"
    )


def test_the_release_force_flag_fails_closed_on_an_unreadable_body():
    """An unparseable body is 'not forced', never 'forced'."""
    from vaibify.gui import registryRoutes

    sSource = Path(registryRoutes.__file__).read_text()
    treeAst = ast.parse(sSource)
    fnRead = next(
        node for node in ast.walk(treeAst)
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "_fbReadForceFlag"
    )
    listReturns = [
        node for node in ast.walk(fnRead) if isinstance(node, ast.Return)
    ]
    listHandlers = [
        node for node in ast.walk(fnRead) if isinstance(node, ast.ExceptHandler)
    ]
    assert listHandlers, "the force flag no longer tolerates a bad body"
    for handler in listHandlers:
        for node in ast.walk(handler):
            if isinstance(node, ast.Return):
                assert node.value is not None and (
                    getattr(node.value, "value", None) is False
                ), "an unreadable body must read as NOT forced"
    assert listReturns, "the force flag reader returns nothing"


def test_the_host_cli_conflict_guidance_never_offers_vaibify_open():
    """A held container is not recovered with 'vaibify open'.

    'vaibify open' transfers a session that already exists; it is not
    the way past a container a LIVE dashboard is holding. Naming it in
    the conflict guidance would send the researcher to a command that
    cannot help and would read, when it failed, as the tool being
    broken. The honest pointer is the in-container agent lane, which
    acts INSIDE the session that is already open.
    """
    from vaibify.cli import hubSession

    assert "vaibify-do" in hubSession.S_AGENT_LANE_POINTER
    assert "vaibify open" not in hubSession.S_AGENT_LANE_POINTER, (
        "the conflict guidance offers a command that cannot resolve it"
    )


# ---------------------------------------------------------------------
# A path argument must never become shell syntax.
# ---------------------------------------------------------------------

def test_no_cli_command_interpolates_a_path_into_a_container_command():
    """``cat`` and ``ls`` are typed reads, and stay that way.

    Both used to build ``f"cat {path}"`` / ``f"ls -1 {path}"`` and hand
    the result to ``ftResultExecuteCommand``, which runs it under
    ``/bin/bash -c``. A path is not syntax: ``vaibify cat '/tmp/a; rm
    -rf /workspace'`` executed both halves, and the far more common
    failure was that a path containing a space did not work at all.

    Raised by an external review of the mutation inventory, and true:
    the inventory listed both sites as trusted arbitrary commands, which
    is exactly the classification a reviewer would have had to correct.
    """
    from vaibify.cli import commandCat, commandLs

    for moduleCommand in (commandCat, commandLs):
        sSource = Path(moduleCommand.__file__).read_text()
        assert "ftResultExecuteCommand" not in sSource, (
            f"{moduleCommand.__name__} still runs a shell command; a "
            f"file read must go through a typed primitive"
        )
        for sInterpolated in ('f"cat {', 'f"ls -1 {', 'f"ls {'):
            assert sInterpolated not in sSource, (
                f"{moduleCommand.__name__} interpolates a path into a "
                f"container command: {sInterpolated}"
            )


def test_the_directory_adapter_cannot_supply_a_command():
    """The listing adapter names an operation; it does not build one.

    The adapter used to assemble a program and hand the text to the
    exemption, guarded by a source check that no caller value reached
    it. Two reviews defeated that check in turn, so the exemption
    stopped accepting command text: an adapter now names one of a fixed
    set of operations and supplies a path. Asserted on the call itself,
    because the guarantee is that the adapter CANNOT pass a command --
    not that this one happens not to.
    """
    import inspect

    from vaibify.docker import dockerConnection
    from vaibify.docker.dockerConnection import DockerConnection

    sSource = inspect.getsource(DockerConnection.flistDirectoryEntries)
    assert "_ftRunTypedRead(" in sSource
    assert "shlex.quote" not in sSource, (
        "the adapter is building a command again"
    )
    assert dockerConnection.S_TYPED_READ_DIRECTORY in sSource, (
        "the adapter must name a declared operation"
    )
    assert dockerConnection.S_TYPED_READ_DIRECTORY in (
        dockerConnection._DICT_TYPED_READ_PROGRAMS
    )
