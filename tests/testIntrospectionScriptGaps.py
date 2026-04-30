"""Coverage tests for vaibify.gui.introspectionScript uncovered branches."""

import pytest
from unittest.mock import MagicMock

from vaibify.gui.introspectionScript import (
    _flistParseIntrospectionOutput,
    _fsRunIntrospection,
)


# ----------------------------------------------------------------------
# Line 1085: _fsRunIntrospection raises RuntimeError on non-zero exit
# ----------------------------------------------------------------------


def test_fsRunIntrospection_nonzero_exit_raises_runtime():
    """When ftResultExecuteCommand returns non-zero, raise RuntimeError."""
    mockDocker = MagicMock()
    mockDocker.fnWriteFile = MagicMock()
    mockDocker.ftResultExecuteCommand.side_effect = [
        (2, "python: syntax error somewhere"),
        (0, ""),
    ]
    with pytest.raises(RuntimeError, match="Introspection failed"):
        _fsRunIntrospection(
            mockDocker, "cid", "/ws", ["x.npy"],
        )


def test_fsRunIntrospection_error_message_includes_exit_code():
    """The raised RuntimeError includes the non-zero exit code."""
    mockDocker = MagicMock()
    mockDocker.fnWriteFile = MagicMock()
    mockDocker.ftResultExecuteCommand.side_effect = [
        (42, "traceback..."),
        (0, ""),
    ]
    with pytest.raises(RuntimeError, match="exit 42"):
        _fsRunIntrospection(
            mockDocker, "cid", "/ws", ["x.csv"],
        )


def test_fsRunIntrospection_cleans_up_script():
    """The script file is removed after execution, even on success."""
    mockDocker = MagicMock()
    mockDocker.fnWriteFile = MagicMock()
    mockDocker.ftResultExecuteCommand.side_effect = [
        (0, '[{"sFileName": "x.npy"}]'),
        (0, ""),
    ]
    listResult = _fsRunIntrospection(
        mockDocker, "cid", "/ws", ["x.npy"],
    )
    assert listResult == [{"sFileName": "x.npy"}]
    listRmCall = [
        c for c in mockDocker.ftResultExecuteCommand.call_args_list
        if c[0][1].startswith("rm -f")
    ]
    assert len(listRmCall) == 1


# ----------------------------------------------------------------------
# Lines 1103-1104: reversed-line fallback skips invalid JSON lines
# ----------------------------------------------------------------------


def test_parse_reversed_fallback_skips_invalid_bracket_line():
    """A line that starts with '[' but is invalid JSON is skipped."""
    sOutput = "nothing here\n[not valid json"
    with pytest.raises(ValueError, match="not valid JSON"):
        _flistParseIntrospectionOutput(sOutput)


def test_parse_reversed_fallback_skips_then_finds_valid():
    """Invalid '[...' line is skipped; a later valid one is returned."""
    sOutput = '[broken\n[{"sFile": "a.csv"}]\n[also broken'
    listResult = _flistParseIntrospectionOutput(sOutput)
    assert listResult == [{"sFile": "a.csv"}]


def test_parse_reversed_fallback_all_bracket_lines_invalid():
    """When every bracket line fails to parse, a ValueError is raised."""
    sOutput = "[bad one\n[bad two\n[bad three"
    with pytest.raises(ValueError, match="not valid JSON"):
        _flistParseIntrospectionOutput(sOutput)
