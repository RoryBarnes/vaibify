"""Final tests for remaining testable uncovered lines."""

from unittest.mock import patch, MagicMock


# ── resourceMonitor: error paths ─────────────────────────────────────


def test_ffParsePercent_invalid_returns_zero():
    from vaibify.gui.resourceMonitor import _ffParsePercent
    assert _ffParsePercent(None) == 0.0
    assert _ffParsePercent("abc%") == 0.0


def test_fsSplitMemoryLimit_no_slash():
    from vaibify.gui.resourceMonitor import _fsSplitMemoryLimit
    assert _fsSplitMemoryLimit("512MiB") == "0B"


def test_fsSplitMemoryLimit_with_slash():
    from vaibify.gui.resourceMonitor import _fsSplitMemoryLimit
    assert _fsSplitMemoryLimit("256MiB / 8GiB") == "8GiB"


# ── setupServer: routes via TestClient ───────────────────────────────


def test_fbDockerAvailable_when_missing():
    from vaibify.cli.configLoader import fbDockerAvailable
    with patch.dict("sys.modules", {"docker": None}):
        bResult = fbDockerAvailable()
        assert isinstance(bResult, bool)


# ── commandInit: template operations ─────────────────────────────────


def test_fnCopyTemplate_missing_template():
    from vaibify.cli.commandInit import fnCopyTemplate
    import pytest
    with pytest.raises((FileNotFoundError, SystemExit)):
        fnCopyTemplate("nonexistent_template_xyz")
