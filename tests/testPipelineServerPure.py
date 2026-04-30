"""Tests for pure functions in vaibify.gui.pipelineServer."""

from types import SimpleNamespace

from vaibify.gui.pipelineServer import (
    fbValidateWebSocketOrigin,
    _fsSanitizeServerError,
    _flistCollectOutputPaths,
    fdictFilterNonNone,
)


class MockWebSocket:
    """Mock WebSocket with configurable headers and query params."""

    def __init__(self, dictHeaders, dictQuery=None):
        self.headers = dictHeaders
        self.query_params = dictQuery or {}


def test_fbValidateWebSocketOrigin_localhost():
    ws = MockWebSocket({"origin": "http://localhost:8080"})
    assert fbValidateWebSocketOrigin(ws) is True


def test_fbValidateWebSocketOrigin_127():
    ws = MockWebSocket({"origin": "http://127.0.0.1:3000"})
    assert fbValidateWebSocketOrigin(ws) is True


def test_fbValidateWebSocketOrigin_https():
    ws = MockWebSocket({"origin": "https://localhost"})
    assert fbValidateWebSocketOrigin(ws) is True


def test_fbValidateWebSocketOrigin_remote_rejected():
    ws = MockWebSocket({"origin": "http://evil.com"})
    assert fbValidateWebSocketOrigin(ws) is False


def test_fbValidateWebSocketOrigin_missing():
    ws = MockWebSocket({})
    assert fbValidateWebSocketOrigin(ws) is False


def test_fbValidateWebSocketOrigin_agent_header_bypass():
    """Valid X-Vaibify-Session header bypasses loopback origin requirement."""
    ws = MockWebSocket(
        {"origin": "http://host.docker.internal:8050",
         "x-vaibify-session": "tok-123"},
    )
    assert fbValidateWebSocketOrigin(ws, "tok-123") is True


def test_fbValidateWebSocketOrigin_agent_token_query_bypass():
    """Valid sToken query param bypasses the loopback origin requirement."""
    ws = MockWebSocket(
        {"origin": "http://host.docker.internal:8050"},
        {"sToken": "tok-123"},
    )
    assert fbValidateWebSocketOrigin(ws, "tok-123") is True


def test_fbValidateWebSocketOrigin_agent_bad_token_rejected():
    """Wrong token with non-loopback origin still rejects."""
    ws = MockWebSocket(
        {"origin": "http://evil.com",
         "x-vaibify-session": "wrong-token"},
        {"sToken": "wrong-token"},
    )
    assert fbValidateWebSocketOrigin(ws, "tok-123") is False


def test_fbValidateWebSocketOrigin_empty_token_rejected():
    """Empty agent header must not match empty expected token."""
    ws = MockWebSocket(
        {"origin": "http://evil.com",
         "x-vaibify-session": ""},
    )
    assert fbValidateWebSocketOrigin(ws, "") is False


def test_fbValidateWebSocketOrigin_loopback_still_works_without_token():
    """Existing browser flow stays intact when no token arg is supplied."""
    ws = MockWebSocket({"origin": "http://localhost:8080"})
    assert fbValidateWebSocketOrigin(ws, "some-other-token") is True


def test_fsSanitizeServerError_disk_full():
    sResult = _fsSanitizeServerError("No space left on device")
    assert "prune" in sResult


def test_fsSanitizeServerError_no_container():
    sResult = _fsSanitizeServerError("No such container: abc")
    assert "stopped" in sResult.lower()


def test_fsSanitizeServerError_connection():
    sResult = _fsSanitizeServerError("Connection refused")
    assert "Docker" in sResult


def test_fsSanitizeServerError_permission():
    sResult = _fsSanitizeServerError("Permission denied")
    assert "Permission" in sResult


def test_fsSanitizeServerError_long_truncated():
    sLong = "x" * 600
    sResult = _fsSanitizeServerError(sLong)
    assert len(sResult) <= 504
    assert sResult.endswith("...")


def test_fsSanitizeServerError_passthrough():
    sResult = _fsSanitizeServerError("some error")
    assert sResult == "some error"


def test_flistCollectOutputPaths_resolves():
    dictWorkflow = {
        "sPlotDirectory": "Plot",
        "sFigureType": "pdf",
        "listSteps": [
            {
                "sDirectory": "step1",
                "saDataFiles": ["data.csv"],
                "saPlotFiles": ["{sPlotDirectory}/fig.pdf"],
            },
        ],
    }
    listPaths = _flistCollectOutputPaths(dictWorkflow)
    assert any("Plot/fig.pdf" in s for s in listPaths)
    assert any("data.csv" in s for s in listPaths)


def test_flistCollectOutputPaths_empty():
    dictWorkflow = {"listSteps": []}
    assert _flistCollectOutputPaths(dictWorkflow) == []


def test_fdictFilterNonNone_filters():
    dictSource = {"a": 1, "b": None, "c": "val", "d": None}
    dictResult = fdictFilterNonNone(dictSource)
    assert dictResult == {"a": 1, "c": "val"}


def test_fdictFilterNonNone_all_none():
    assert fdictFilterNonNone({"a": None}) == {}


def test_fdictFilterNonNone_empty():
    assert fdictFilterNonNone({}) == {}


# -----------------------------------------------------------------------
# _fnUpdateAggregateTestState
# -----------------------------------------------------------------------


def test_fnUpdateAggregateTestState_all_passed():
    from vaibify.gui.pipelineServer import _fnUpdateAggregateTestState
    dictStep = {
        "dictTests": {
            "dictIntegrity": {"saCommands": ["pytest tests/test_integrity.py"]},
            "dictQualitative": {"saCommands": ["pytest tests/test_qualitative.py"]},
            "dictQuantitative": {"saCommands": ["pytest tests/test_quantitative.py"]},
        },
        "dictVerification": {
            "sIntegrity": "passed",
            "sQualitative": "passed",
            "sQuantitative": "passed",
        },
    }
    _fnUpdateAggregateTestState(dictStep)
    assert dictStep["dictVerification"]["sUnitTest"] == "passed"


def test_fnUpdateAggregateTestState_one_failed():
    from vaibify.gui.pipelineServer import _fnUpdateAggregateTestState
    dictStep = {
        "dictTests": {
            "dictIntegrity": {"saCommands": ["pytest tests/test_integrity.py"]},
            "dictQualitative": {"saCommands": ["pytest tests/test_qualitative.py"]},
            "dictQuantitative": {"saCommands": ["pytest tests/test_quantitative.py"]},
        },
        "dictVerification": {
            "sIntegrity": "passed",
            "sQualitative": "failed",
            "sQuantitative": "passed",
        },
    }
    _fnUpdateAggregateTestState(dictStep)
    assert dictStep["dictVerification"]["sUnitTest"] == "failed"


def test_fnUpdateAggregateTestState_no_commands():
    from vaibify.gui.pipelineServer import _fnUpdateAggregateTestState
    dictStep = {
        "dictTests": {
            "dictIntegrity": {"saCommands": []},
            "dictQualitative": {"saCommands": []},
            "dictQuantitative": {"saCommands": []},
        },
        "dictVerification": {},
    }
    _fnUpdateAggregateTestState(dictStep)
    assert dictStep["dictVerification"]["sUnitTest"] == "untested"


def test_fnUpdateAggregateTestState_mixed_untested():
    from vaibify.gui.pipelineServer import _fnUpdateAggregateTestState
    dictStep = {
        "dictTests": {
            "dictIntegrity": {"saCommands": ["pytest tests/test_integrity.py"]},
            "dictQualitative": {"saCommands": ["pytest tests/test_qualitative.py"]},
            "dictQuantitative": {"saCommands": []},
        },
        "dictVerification": {
            "sIntegrity": "passed",
            "sQualitative": "untested",
        },
    }
    _fnUpdateAggregateTestState(dictStep)
    assert dictStep["dictVerification"]["sUnitTest"] == "untested"


def test_fnUpdateAggregateTestState_partial_categories():
    from vaibify.gui.pipelineServer import _fnUpdateAggregateTestState
    dictStep = {
        "dictTests": {
            "dictIntegrity": {"saCommands": ["pytest tests/test_integrity.py"]},
            "dictQualitative": {"saCommands": []},
            "dictQuantitative": {"saCommands": []},
        },
        "dictVerification": {
            "sIntegrity": "passed",
        },
    }
    _fnUpdateAggregateTestState(dictStep)
    assert dictStep["dictVerification"]["sUnitTest"] == "passed"


def test_fnUpdateAggregateTestState_empty_step():
    from vaibify.gui.pipelineServer import _fnUpdateAggregateTestState
    dictStep = {"dictVerification": {}}
    _fnUpdateAggregateTestState(dictStep)
    assert dictStep["dictVerification"]["sUnitTest"] == "untested"
