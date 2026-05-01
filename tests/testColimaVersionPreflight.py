"""Colima version pre-flight tests (F-E-05)."""

from unittest.mock import patch

from vaibify.cli.preflightChecks import fpreflightColimaVersion as fbBuild
from vaibify.cli.preflightChecks import fpreflightColimaVersion as fbStart
from vaibify.docker.dockerContext import (
    _ftParseColimaVersion,
    ftColimaVersion,
)


# -----------------------------------------------------------------------
# Parser
# -----------------------------------------------------------------------


def test_parse_plain_text_version():
    """`colima version 0.6.7` parses to (0, 6, 7)."""
    assert _ftParseColimaVersion(
        "colima version 0.6.7\nruntime: docker"
    ) == (0, 6, 7)


def test_parse_two_segment_version():
    """A two-segment version is padded with zero patch."""
    assert _ftParseColimaVersion("colima version 0.5") == (0, 5, 0)


def test_parse_unparseable_returns_empty():
    """Garbage output yields an empty tuple."""
    assert _ftParseColimaVersion("not a version") == ()


def test_parse_json_payload():
    """A JSON payload is parsed when shaped with a 'version' key."""
    sJson = '{"version": "v0.7.1"}'
    assert _ftParseColimaVersion(sJson) == (0, 7, 1)


def test_ftColimaVersion_returns_empty_when_missing():
    """ftColimaVersion handles a missing colima binary cleanly."""
    with patch(
        "vaibify.docker.dockerContext._fsRunColimaVersion", return_value="",
    ):
        assert ftColimaVersion() == ()


# -----------------------------------------------------------------------
# Pre-flight helper (commandBuild)
# -----------------------------------------------------------------------


def test_build_preflight_warns_when_below_floor():
    """A version below 0.5.0 emits a warn-level result."""
    with patch(
        "vaibify.docker.dockerContext.fbColimaActive", return_value=True,
    ), patch(
        "vaibify.docker.dockerContext.ftColimaVersion",
        return_value=(0, 4, 9),
    ):
        result = fbBuild()
    assert result is not None
    assert result.sLevel == "warn"
    assert "0.5.0" in result.sMessage
    assert "Upgrade Colima" in result.sRemediation


def test_build_preflight_silent_when_at_or_above_floor():
    """A version at or above the floor emits no result."""
    with patch(
        "vaibify.docker.dockerContext.fbColimaActive", return_value=True,
    ), patch(
        "vaibify.docker.dockerContext.ftColimaVersion",
        return_value=(0, 5, 0),
    ):
        assert fbBuild() is None


def test_build_preflight_silent_when_colima_inactive():
    """Non-Colima contexts produce no result."""
    with patch(
        "vaibify.docker.dockerContext.fbColimaActive", return_value=False,
    ):
        assert fbBuild() is None


def test_build_preflight_silent_on_parse_error():
    """An unparseable version yields no result."""
    with patch(
        "vaibify.docker.dockerContext.fbColimaActive", return_value=True,
    ), patch(
        "vaibify.docker.dockerContext.ftColimaVersion", return_value=(),
    ):
        assert fbBuild() is None


# -----------------------------------------------------------------------
# Pre-flight helper (commandStart) — same behavior surface
# -----------------------------------------------------------------------


def test_start_preflight_warns_when_below_floor():
    """The start-side helper warns identically when below the floor."""
    with patch(
        "vaibify.docker.dockerContext.fbColimaActive", return_value=True,
    ), patch(
        "vaibify.docker.dockerContext.ftColimaVersion",
        return_value=(0, 4, 0),
    ):
        result = fbStart()
    assert result is not None
    assert result.sLevel == "warn"


def test_start_preflight_silent_above_floor():
    """The start-side helper is silent above the floor."""
    with patch(
        "vaibify.docker.dockerContext.fbColimaActive", return_value=True,
    ), patch(
        "vaibify.docker.dockerContext.ftColimaVersion",
        return_value=(0, 6, 0),
    ):
        assert fbStart() is None
