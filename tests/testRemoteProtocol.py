"""Every field of a startup record arrives from another machine.

The client's whole exposure to the remote is this one line, so each
field gets its own refusal and each refusal gets its own test. The
governing property is the last one here: **no remote can name the URL
the local browser opens.** A schema with a URL field would turn "open
the dashboard" into "open anything", so there is no such field and the
client builds the address from what it already knows.
"""

import pytest

from vaibify.cli.remoteProtocol import (
    I_MAX_RECORD_BYTES,
    I_PROTOCOL_VERSION,
    RemoteProtocolError,
    S_STARTUP_RECORD_PREFIX,
    fdictParseStartupRecord,
    fsFormatStartupRecord,
    fsLocalDashboardUrl,
)

I_PORT = 18050
S_GOOD_CAPABILITY = "A" * 43


def _fsRecord(**dictOverrides):
    """Return a well-formed record line with fields overridden."""
    dictFields = {
        "iPort": I_PORT,
        "sBootstrapCapability": S_GOOD_CAPABILITY,
        "sExecutionMode": "docker",
        "sHostname": "compute-machine",
    }
    dictFields.update(dictOverrides)
    return fsFormatStartupRecord(**dictFields)


def test_a_well_formed_record_round_trips():
    dictRecord = fdictParseStartupRecord(_fsRecord(), I_PORT)
    assert dictRecord["iProtocolVersion"] == I_PROTOCOL_VERSION
    assert dictRecord["sBootstrapCapability"] == S_GOOD_CAPABILITY
    assert dictRecord["sExecutionPlacement"] == "direct"


def test_silence_is_named_as_a_missing_remote_vaibify():
    """The commonest real failure deserves the commonest real cause."""
    with pytest.raises(RemoteProtocolError) as excinfo:
        fdictParseStartupRecord(None, I_PORT)
    assert "PATH" in str(excinfo.value)


def test_an_oversized_record_is_refused():
    sLine = S_STARTUP_RECORD_PREFIX + ("x" * (I_MAX_RECORD_BYTES + 1))
    with pytest.raises(RemoteProtocolError) as excinfo:
        fdictParseStartupRecord(sLine, I_PORT)
    assert "oversized" in str(excinfo.value)


def test_a_non_protocol_line_is_refused():
    """A login banner or an motd is not a record."""
    with pytest.raises(RemoteProtocolError):
        fdictParseStartupRecord("Welcome to compute-machine!", I_PORT)


def test_a_multiline_payload_is_refused():
    sLine = S_STARTUP_RECORD_PREFIX + '{"a":\n"b"}'
    with pytest.raises(RemoteProtocolError):
        fdictParseStartupRecord(sLine, I_PORT)


def test_malformed_json_is_refused():
    with pytest.raises(RemoteProtocolError) as excinfo:
        fdictParseStartupRecord(S_STARTUP_RECORD_PREFIX + "{oops", I_PORT)
    assert "JSON" in str(excinfo.value)


def test_a_version_mismatch_names_both_versions():
    sLine = _fsRecord().replace(
        f'"iProtocolVersion":{I_PROTOCOL_VERSION}',
        '"iProtocolVersion":99',
    )
    with pytest.raises(RemoteProtocolError) as excinfo:
        fdictParseStartupRecord(sLine, I_PORT)
    assert "99" in str(excinfo.value)
    assert "matching versions" in str(excinfo.value)


def test_a_record_naming_another_port_is_refused():
    """The forward already exists; a different port is unreachable."""
    with pytest.raises(RemoteProtocolError) as excinfo:
        fdictParseStartupRecord(_fsRecord(), I_PORT + 1)
    assert "unreachable" in str(excinfo.value)


def test_an_empty_capability_is_named_as_the_armed_cap():
    """A hub at its limit fails opaquely at redemption otherwise."""
    with pytest.raises(RemoteProtocolError) as excinfo:
        fdictParseStartupRecord(
            _fsRecord(sBootstrapCapability=""), I_PORT,
        )
    assert "limit" in str(excinfo.value)


@pytest.mark.parametrize("sCapability", [
    "short",
    "has spaces in it aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "hasa/slash" + "a" * 40,
    "x" * 500,
])
def test_a_malformed_capability_is_refused(sCapability):
    with pytest.raises(RemoteProtocolError):
        fdictParseStartupRecord(
            _fsRecord(sBootstrapCapability=sCapability), I_PORT,
        )


@pytest.mark.parametrize("sMode", ["kubernetes", "", "DOCKER", "slurm"])
def test_an_unknown_execution_mode_is_refused(sMode):
    with pytest.raises(RemoteProtocolError) as excinfo:
        fdictParseStartupRecord(_fsRecord(sExecutionMode=sMode), I_PORT)
    assert "execution mode" in str(excinfo.value)


def test_an_unknown_placement_is_refused_rather_than_guessed():
    """A scheduled allocation is not something to improvise around.

    A submission process can exit successfully while its job is still
    pending, so a client that guessed would be claiming authority over
    work it cannot see.
    """
    sLine = _fsRecord().replace(
        '"sExecutionPlacement":"direct"',
        '"sExecutionPlacement":"scheduled"',
    )
    with pytest.raises(RemoteProtocolError) as excinfo:
        fdictParseStartupRecord(sLine, I_PORT)
    assert "will not guess" in str(excinfo.value)


@pytest.mark.parametrize("sHostname", [
    "has space", "semi;colon", "-leading-dash", "a" * 300,
    "back`tick", "$(command)", "../../etc/passwd",
])
def test_a_hostname_that_is_not_a_hostname_is_refused(sHostname):
    """It is display text, but display text still gets a shape."""
    with pytest.raises(RemoteProtocolError):
        fdictParseStartupRecord(_fsRecord(sHostname=sHostname), I_PORT)


def test_the_local_url_is_built_from_local_values_only():
    """The one property the whole schema is shaped around."""
    sUrl = fsLocalDashboardUrl(I_PORT, S_GOOD_CAPABILITY)
    assert sUrl.startswith(f"http://127.0.0.1:{I_PORT}/#bootstrap=")
    assert S_GOOD_CAPABILITY in sUrl


def test_no_remote_field_can_redirect_the_local_browser():
    """A hostile record must not move the browser off loopback.

    Not falsification-marked: the property holds because the builder
    takes no hostname at all, and a mutation that removed the loopback
    literal would be a crash rather than a behaviour change. The
    assertion is still worth keeping -- it is what fails if someone
    later "helpfully" threads the remote's hostname through for
    display.
    """
    sLine = _fsRecord(sHostname="evil-host")
    dictRecord = fdictParseStartupRecord(sLine, I_PORT)
    sUrl = fsLocalDashboardUrl(
        I_PORT, dictRecord["sBootstrapCapability"],
    )
    assert sUrl.startswith("http://127.0.0.1:"), sUrl
    assert "evil-host" not in sUrl, (
        "a value the remote chose reached the address the local "
        f"browser is told to open: {sUrl}"
    )
