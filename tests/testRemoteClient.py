"""The laptop half: what it will and will not put on a command line.

OpenSSH hands its remote command to the far side's LOGIN SHELL. That
single fact is why this module has a test file of its own and why the
CLI has no ``--project`` option: a project name may legally contain a
space, and the argument the codebase relies on elsewhere -- "no
shell=True exists in the package" -- stops being true the moment a
feature builds a remote shell command.

So the assertions here are mostly about absence. Nothing a user typed
appears in the command string; the destination is its own argv element
and still must look like a host; and the only interpolated value in the
whole command is an integer.
"""

import pytest

from vaibify.cli import commandRemote
from vaibify.cli.commandRemote import (
    F_RECONNECT_WINDOW_SECONDS,
    RemoteClientError,
    ffNextReconnectDelaySeconds,
    fnValidateSshDestination,
    fsaBuildSshCommand,
)


@pytest.mark.parametrize("sDestination", [
    "compute-machine",
    "researcher@compute-machine",
    "host.example.edu",
    "researcher@host.sub.example.edu",
])
def test_ordinary_destinations_are_accepted(sDestination):
    fnValidateSshDestination(sDestination)


@pytest.mark.parametrize("sDestination", [
    "-oProxyCommand=curl evil.example",
    "-J jump-host",
    "host; rm -rf /",
    "host && curl evil",
    "host`whoami`",
    "host$(whoami)",
    "host | tee /tmp/x",
    "host\nsecond-line",
    "host with space",
    "'quoted-host'",
    "",
])
def test_a_destination_that_could_change_a_command_is_refused(
    sDestination,
):
    """Option-looking and shell-looking destinations both refuse.

    The leading dash matters independently of the metacharacters:
    argv position stops the shell from seeing it, and does nothing
    about ssh itself reading it as a flag.
    """
    with pytest.raises(RemoteClientError):
        fnValidateSshDestination(sDestination)


def test_the_remote_command_interpolates_only_an_integer_port():
    """The command string's only variable part is a number."""
    listCommand = fsaBuildSshCommand("compute-machine", 18050)
    sRemoteCommand = listCommand[-1]
    assert sRemoteCommand == "vaibify remote-helper --port 18050"
    assert "compute-machine" not in sRemoteCommand, (
        "the destination reached the command string, where the far "
        "side's login shell would evaluate it"
    )


def test_the_destination_is_its_own_argv_element():
    listCommand = fsaBuildSshCommand("researcher@host", 18050)
    assert "researcher@host" in listCommand
    assert listCommand.index("researcher@host") == len(listCommand) - 2


def test_a_string_port_cannot_smuggle_anything_into_the_command():
    """int() is the guard, and it is asserted rather than assumed."""
    with pytest.raises(ValueError):
        fsaBuildSshCommand("host", "18050; rm -rf /")


def test_the_forward_is_loopback_to_loopback_on_one_number():
    """Same port both ends, or the dashboard's Host check refuses.

    The production Host check requires the browser-visible port to
    equal the backend's expected port, so an N-to-M forward would
    produce a dashboard that loads and then refuses every call.
    """
    listCommand = fsaBuildSshCommand("host", 18050)
    iFlag = listCommand.index("-L")
    assert listCommand[iFlag + 1] == "127.0.0.1:18050:127.0.0.1:18050"


def test_forwarding_failure_is_fatal_rather_than_silent():
    """Without this, ssh connects and the forward quietly does not."""
    listCommand = fsaBuildSshCommand("host", 18050)
    assert "ExitOnForwardFailure=yes" in listCommand


def test_the_helper_gets_no_remote_terminal():
    """A PTY would mix the helper's protocol and prose streams."""
    assert "-T" in fsaBuildSshCommand("host", 18050)


def test_keepalives_notice_a_dead_tunnel_inside_the_window():
    """A tunnel that dies silently must be noticed, not waited on."""
    listCommand = fsaBuildSshCommand("host", 18050)
    sJoined = " ".join(listCommand)
    assert "ServerAliveInterval=15" in sJoined
    assert "ServerAliveCountMax=3" in sJoined


def test_no_project_option_exists_on_the_command():
    """The deliberate absence, pinned so it cannot be added casually.

    A project name may contain a space. Passing one through a remote
    shell command would word-split it, and guarding that with quoting
    would be defending a surface that does not need to exist: the
    project is chosen in the dashboard, over HTTP.
    """
    listOptionNames = [
        sOpt
        for parameter in commandRemote.fnRemoteCommand.params
        for sOpt in getattr(parameter, "opts", [])
    ]
    assert "--project" not in listOptionNames, (
        "a project selector on this command would have to cross the "
        "remote login shell; it belongs in the dashboard instead"
    )


def test_the_ladder_terminates_inside_the_hold_window():
    """Every scheduled attempt must land while the session is alive."""
    fElapsed = 0.0
    iAttempt = 0
    listDelays = []
    while True:
        fDelay = ffNextReconnectDelaySeconds(iAttempt, fElapsed)
        if fDelay < 0:
            break
        listDelays.append(fDelay)
        fElapsed += fDelay
        iAttempt += 1
        assert iAttempt < 10000, "the ladder did not terminate"
    assert listDelays, "the ladder scheduled no attempt at all"
    assert sum(listDelays) < F_RECONNECT_WINDOW_SECONDS, (
        f"the ladder totals {sum(listDelays)}s against a "
        f"{F_RECONNECT_WINDOW_SECONDS}s window; the tail would present "
        "a credential the hub has already revoked"
    )


def test_the_ladder_keeps_trying_for_most_of_the_window():
    """A ladder that gave up early would waste the hold it was given."""
    fElapsed = 0.0
    iAttempt = 0
    while True:
        fDelay = ffNextReconnectDelaySeconds(iAttempt, fElapsed)
        if fDelay < 0:
            break
        fElapsed += fDelay
        iAttempt += 1
    assert fElapsed > F_RECONNECT_WINDOW_SECONDS * 0.9, (
        f"the ladder stopped after {fElapsed}s of a "
        f"{F_RECONNECT_WINDOW_SECONDS}s window"
    )


def test_an_explicit_port_in_use_is_reported_not_worked_around(
    monkeypatch,
):
    """A named port is usually matching a bookmark or a firewall rule."""
    from vaibify.cli import portAllocator
    monkeypatch.setattr(portAllocator, "fbIsPortFree", lambda iPort: False)
    with pytest.raises(RemoteClientError) as excinfo:
        commandRemote.fiChooseLocalPort(18050)
    assert "already in use" in str(excinfo.value)


def test_an_out_of_range_explicit_port_is_refused():
    with pytest.raises(RemoteClientError):
        commandRemote.fiChooseLocalPort(70000)
