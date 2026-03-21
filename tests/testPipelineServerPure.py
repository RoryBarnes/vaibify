"""Tests for pure functions in vaibify.gui.pipelineServer."""

from types import SimpleNamespace

from vaibify.gui.pipelineServer import (
    fbValidateWebSocketOrigin,
    _fsSanitizeServerError,
    _flistCollectOutputPaths,
    fdictFilterNonNone,
)


class MockWebSocket:
    """Mock WebSocket with configurable headers."""

    def __init__(self, dictHeaders):
        self.headers = dictHeaders


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
    sLong = "x" * 300
    sResult = _fsSanitizeServerError(sLong)
    assert len(sResult) <= 204
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
