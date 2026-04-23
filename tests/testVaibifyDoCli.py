"""Tests for docker/vaibifyDo.py, the in-container bridge CLI.

The CLI is a standalone script shipped into the container image, so
the vaibify package does not import it. We load it via
``importlib.util.spec_from_file_location`` and exercise its public
helpers directly. Each test patches the session/catalog file paths
with a tmpdir fixture so the module's module-level constants can be
overridden before invoking a helper.
"""

import importlib.util
import io
import json
import os
import socket
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import urllib.error


_S_VAIBIFY_DO_PATH = (
    Path(__file__).resolve().parent.parent / "docker" / "vaibifyDo.py"
)


@pytest.fixture
def modCli():
    """Return a fresh import of docker/vaibifyDo.py."""
    spec = importlib.util.spec_from_file_location(
        "vaibifyDoCli", _S_VAIBIFY_DO_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def dictValidEnv():
    return {
        "VAIBIFY_HOST_URL": "http://host.docker.internal:8050",
        "VAIBIFY_SESSION_TOKEN": "tok-abc",
        "VAIBIFY_CONTAINER_ID": "c-1",
    }


@pytest.fixture
def dictSampleCatalog():
    return {
        "sSchemaVersion": "1.0",
        "listActions": [
            {"sName": "run-all", "sCategory": "execution",
             "sMethod": "WS", "sPath": "runAll",
             "bAgentSafe": True,
             "sDescription": "Run every step. Details here."},
            {"sName": "run-step", "sCategory": "execution",
             "sMethod": "WS", "sPath": "runSelected",
             "bAgentSafe": True,
             "sDescription": "Run one step by name or 1-based index."},
            {"sName": "run-from-step", "sCategory": "execution",
             "sMethod": "WS", "sPath": "runFrom",
             "bAgentSafe": True,
             "sDescription": "Run from given step index to end."},
            {"sName": "run-unit-tests", "sCategory": "verification",
             "sMethod": "POST",
             "sPath": "/api/steps/{sContainerId}/{iStepIndex}/run-tests",
             "bAgentSafe": True,
             "sDescription": "Run all test categories for one step."},
            {"sName": "write-file", "sCategory": "files",
             "sMethod": "PUT",
             "sPath": "/api/file/{sContainerId}/{sFilePath:path}",
             "bAgentSafe": True,
             "sDescription": "Write text content to file."},
            {"sName": "delete-step", "sCategory": "workflow",
             "sMethod": "DELETE",
             "sPath": "/api/steps/{sContainerId}/{iStepIndex}",
             "bAgentSafe": False,
             "sDescription": "Remove a step. User-only."},
        ],
    }


def _fnWriteEnvFile(sPath, dictEnv):
    sContent = "\n".join(
        f"{sKey}={sValue}" for sKey, sValue in dictEnv.items()
    ) + "\n"
    Path(sPath).write_text(sContent, encoding="utf-8")


# -----------------------------------------------------------------------
# fdictReadCatalog
# -----------------------------------------------------------------------


def test_fdictReadCatalog_happy_path(
    modCli, tmp_path, dictSampleCatalog,
):
    sPath = tmp_path / "catalog.json"
    sPath.write_text(json.dumps(dictSampleCatalog), encoding="utf-8")
    with patch.object(modCli, "S_CATALOG_JSON_PATH", str(sPath)):
        dictResult = modCli.fdictReadCatalog()
    assert dictResult == dictSampleCatalog


def test_fdictReadCatalog_missing_exits(modCli, tmp_path):
    sPath = tmp_path / "nope.json"
    with patch.object(modCli, "S_CATALOG_JSON_PATH", str(sPath)):
        with pytest.raises(SystemExit) as excInfo:
            modCli.fdictReadCatalog()
    assert excInfo.value.code == 3


def test_fdictReadCatalog_schema_mismatch_warns(
    modCli, tmp_path, capsys,
):
    sPath = tmp_path / "cat.json"
    sPath.write_text(
        json.dumps({"sSchemaVersion": "9.9", "listActions": []}),
        encoding="utf-8",
    )
    with patch.object(modCli, "S_CATALOG_JSON_PATH", str(sPath)):
        dictResult = modCli.fdictReadCatalog()
    tCapture = capsys.readouterr()
    assert "schema" in tCapture.err.lower()
    assert dictResult["sSchemaVersion"] == "9.9"


# -----------------------------------------------------------------------
# fdictReadSession
# -----------------------------------------------------------------------


def test_fdictReadSession_happy_path(
    modCli, tmp_path, dictValidEnv,
):
    sPath = tmp_path / "session.env"
    _fnWriteEnvFile(sPath, dictValidEnv)
    with patch.object(modCli, "S_SESSION_ENV_PATH", str(sPath)):
        dictResult = modCli.fdictReadSession()
    assert dictResult == dictValidEnv


def test_fdictReadSession_missing_file_exits(modCli, tmp_path):
    with patch.object(
        modCli, "S_SESSION_ENV_PATH", str(tmp_path / "missing.env"),
    ):
        with pytest.raises(SystemExit) as excInfo:
            modCli.fdictReadSession()
    assert excInfo.value.code == 3


def test_fdictReadSession_missing_keys_exits(modCli, tmp_path):
    sPath = tmp_path / "partial.env"
    sPath.write_text("VAIBIFY_HOST_URL=http://x\n", encoding="utf-8")
    with patch.object(modCli, "S_SESSION_ENV_PATH", str(sPath)):
        with pytest.raises(SystemExit) as excInfo:
            modCli.fdictReadSession()
    assert excInfo.value.code == 3


def test_fdictReadSession_ignores_comments_and_blanks(
    modCli, tmp_path,
):
    sPath = tmp_path / "c.env"
    sPath.write_text(
        "# comment line\n"
        "\n"
        "VAIBIFY_HOST_URL=http://x\n"
        "VAIBIFY_SESSION_TOKEN=t\n"
        "VAIBIFY_CONTAINER_ID=c\n",
        encoding="utf-8",
    )
    with patch.object(modCli, "S_SESSION_ENV_PATH", str(sPath)):
        dictResult = modCli.fdictReadSession()
    assert dictResult["VAIBIFY_HOST_URL"] == "http://x"


def test_fdictReadSession_strips_quotes(modCli, tmp_path):
    sPath = tmp_path / "q.env"
    sPath.write_text(
        'VAIBIFY_HOST_URL="http://x"\n'
        "VAIBIFY_SESSION_TOKEN='tok'\n"
        "VAIBIFY_CONTAINER_ID=c\n",
        encoding="utf-8",
    )
    with patch.object(modCli, "S_SESSION_ENV_PATH", str(sPath)):
        dictResult = modCli.fdictReadSession()
    assert dictResult["VAIBIFY_HOST_URL"] == "http://x"
    assert dictResult["VAIBIFY_SESSION_TOKEN"] == "tok"


# -----------------------------------------------------------------------
# fdictFindAction
# -----------------------------------------------------------------------


def test_fdictFindAction_known(modCli, dictSampleCatalog):
    dictEntry = modCli.fdictFindAction(dictSampleCatalog, "run-all")
    assert dictEntry["sPath"] == "runAll"


def test_fdictFindAction_unknown(modCli, dictSampleCatalog):
    assert modCli.fdictFindAction(dictSampleCatalog, "nope") is None


# -----------------------------------------------------------------------
# fnPrintList / fnPrintDescribe
# -----------------------------------------------------------------------


def test_fnPrintList_emits_rows_and_legend(
    modCli, capsys, dictSampleCatalog,
):
    modCli.fnPrintList(dictSampleCatalog)
    tCapture = capsys.readouterr()
    assert "NAME" in tCapture.out
    assert "run-all" in tCapture.out
    assert "delete-step" in tCapture.out
    assert "user-only" in tCapture.out


def test_fnPrintDescribe_emits_pretty_json(
    modCli, capsys, dictSampleCatalog,
):
    modCli.fnPrintDescribe(dictSampleCatalog["listActions"][0])
    tCapture = capsys.readouterr()
    dictParsed = json.loads(tCapture.out)
    assert dictParsed["sName"] == "run-all"


# -----------------------------------------------------------------------
# _fnCoerceScalar + ftParsePositionalArgs
# -----------------------------------------------------------------------


def test_coerce_scalar_booleans(modCli):
    assert modCli._fnCoerceScalar("true") is True
    assert modCli._fnCoerceScalar("FALSE") is False


def test_coerce_scalar_int_and_float(modCli):
    assert modCli._fnCoerceScalar("7") == 7
    assert modCli._fnCoerceScalar("3.14") == 3.14


def test_coerce_scalar_json_list(modCli):
    assert modCli._fnCoerceScalar("[1,2,3]") == [1, 2, 3]


def test_coerce_scalar_json_object(modCli):
    assert modCli._fnCoerceScalar("{\"a\":1}") == {"a": 1}


def test_coerce_scalar_bad_json_falls_back_to_string(modCli):
    assert modCli._fnCoerceScalar("[nope") == "[nope"


def test_coerce_scalar_plain_string(modCli):
    assert modCli._fnCoerceScalar("hello") == "hello"


def test_parse_positional_args_splits(modCli):
    listPos, dictBody = modCli.ftParsePositionalArgs(
        ["1", "sKey=value", '{"x":1}'],
    )
    assert listPos == ["1"]
    assert dictBody == {"sKey": "value", "x": 1}


def test_parse_positional_args_inline_json_overrides(modCli):
    listPos, dictBody = modCli.ftParsePositionalArgs(
        ['{"sKey":"a"}', "sKey=b"],
    )
    # Insertion order: inline-json first then key=value override.
    assert dictBody == {"sKey": "b"}
    assert listPos == []


# -----------------------------------------------------------------------
# flistPathPlaceholders + fsFillPath
# -----------------------------------------------------------------------


def test_flistPathPlaceholders_extracts_names(modCli):
    listNames = modCli.flistPathPlaceholders(
        "/api/steps/{sContainerId}/{iStepIndex}/run-tests",
    )
    assert listNames == ["sContainerId", "iStepIndex"]


def test_flistPathPlaceholders_strips_converters(modCli):
    listNames = modCli.flistPathPlaceholders(
        "/api/file/{sContainerId}/{sFilePath:path}",
    )
    assert listNames == ["sContainerId", "sFilePath"]


def test_flistPathPlaceholders_unclosed_brace_terminates(modCli):
    assert modCli.flistPathPlaceholders("/api/{oops") == []


def test_fsFillPath_substitutes_basic(modCli):
    sPath = modCli.fsFillPath(
        "/api/steps/{sContainerId}/{iStepIndex}/run-tests",
        {"sContainerId": "c-1", "iStepIndex": 2},
    )
    assert sPath == "/api/steps/c-1/2/run-tests"


def test_fsFillPath_path_converter_preserves_slashes(modCli):
    sPath = modCli.fsFillPath(
        "/api/file/{sContainerId}/{sFilePath:path}",
        {"sContainerId": "c-1", "sFilePath": "sub/dir/file.txt"},
    )
    assert sPath == "/api/file/c-1/sub/dir/file.txt"


# -----------------------------------------------------------------------
# fdictResolveHttpTarget
# -----------------------------------------------------------------------


def test_fdictResolveHttpTarget_fills_path_and_body(
    modCli, dictSampleCatalog, dictValidEnv,
):
    dictEntry = modCli.fdictFindAction(dictSampleCatalog, "run-unit-tests")
    dictTarget = modCli.fdictResolveHttpTarget(
        dictEntry, ["5", "sFoo=bar"], dictValidEnv,
    )
    assert dictTarget["sUrl"].endswith(
        "/api/steps/c-1/5/run-tests"
    )
    assert dictTarget["dictBody"] == {"sFoo": "bar"}


def test_fdictResolveHttpTarget_insufficient_positional_exits(
    modCli, dictSampleCatalog, dictValidEnv,
):
    dictEntry = modCli.fdictFindAction(dictSampleCatalog, "run-unit-tests")
    with pytest.raises(SystemExit) as excInfo:
        modCli.fdictResolveHttpTarget(dictEntry, [], dictValidEnv)
    assert excInfo.value.code == 2


# -----------------------------------------------------------------------
# fdictResolveWsPayload: run-step and run-from-step name->index
# -----------------------------------------------------------------------


def test_resolve_ws_payload_run_step_int(modCli, dictSampleCatalog):
    dictEntry = modCli.fdictFindAction(dictSampleCatalog, "run-step")
    dictPayload = modCli.fdictResolveWsPayload(dictEntry, ["3"])
    assert dictPayload["listStepIndices"] == [3]
    assert dictPayload["sAction"] == "runSelected"


def test_resolve_ws_payload_run_step_string(modCli, dictSampleCatalog):
    dictEntry = modCli.fdictFindAction(dictSampleCatalog, "run-step")
    dictPayload = modCli.fdictResolveWsPayload(
        dictEntry, ["plot-results"])
    assert dictPayload["listStepIndices"] == ["plot-results"]


def test_resolve_ws_payload_run_from_step_coerces_int(
    modCli, dictSampleCatalog,
):
    dictEntry = modCli.fdictFindAction(dictSampleCatalog, "run-from-step")
    dictPayload = modCli.fdictResolveWsPayload(dictEntry, ["2"])
    assert dictPayload["iStartStep"] == 2


def test_resolve_ws_payload_no_positional_args(modCli, dictSampleCatalog):
    dictEntry = modCli.fdictFindAction(dictSampleCatalog, "run-all")
    dictPayload = modCli.fdictResolveWsPayload(dictEntry, [])
    assert dictPayload == {"sAction": "runAll"}


def test_resolve_ws_payload_merges_key_value(modCli, dictSampleCatalog):
    dictEntry = modCli.fdictFindAction(dictSampleCatalog, "run-all")
    dictPayload = modCli.fdictResolveWsPayload(
        dictEntry, ["bExtra=true"])
    assert dictPayload["bExtra"] is True


# -----------------------------------------------------------------------
# fnCheckAgentSafety: user-only refusal
# -----------------------------------------------------------------------


def test_fnCheckAgentSafety_user_only_refuses(
    modCli, capsys, dictSampleCatalog,
):
    dictEntry = modCli.fdictFindAction(dictSampleCatalog, "delete-step")
    with pytest.raises(SystemExit) as excInfo:
        modCli.fnCheckAgentSafety(dictEntry)
    assert excInfo.value.code == 5
    tCapture = capsys.readouterr()
    dictRefusal = json.loads(tCapture.out)
    assert dictRefusal["sRefusal"] == "user-only-action"
    assert dictRefusal["sName"] == "delete-step"
    assert "researcher" in dictRefusal["sHint"].lower()


def test_fnCheckAgentSafety_agent_safe_returns_none(
    modCli, dictSampleCatalog,
):
    dictEntry = modCli.fdictFindAction(dictSampleCatalog, "run-all")
    assert modCli.fnCheckAgentSafety(dictEntry) is None


# -----------------------------------------------------------------------
# fnDryRun
# -----------------------------------------------------------------------


def test_fnDryRun_http(
    modCli, capsys, dictSampleCatalog, dictValidEnv,
):
    dictEntry = modCli.fdictFindAction(dictSampleCatalog, "run-unit-tests")
    with pytest.raises(SystemExit) as excInfo:
        modCli.fnDryRun(dictEntry, ["3"], dictValidEnv)
    assert excInfo.value.code == 0
    dictParsed = json.loads(capsys.readouterr().out)
    assert dictParsed["sTransport"] == "HTTP"
    assert dictParsed["sMethod"] == "POST"


def test_fnDryRun_ws(
    modCli, capsys, dictSampleCatalog, dictValidEnv,
):
    dictEntry = modCli.fdictFindAction(dictSampleCatalog, "run-step")
    with pytest.raises(SystemExit) as excInfo:
        modCli.fnDryRun(dictEntry, ["2"], dictValidEnv)
    assert excInfo.value.code == 0
    dictParsed = json.loads(capsys.readouterr().out)
    assert dictParsed["sTransport"] == "WS"
    assert dictParsed["dictPayload"]["listStepIndices"] == [2]


# -----------------------------------------------------------------------
# fnSendHttp + _fnHandleHttpError + _fnPrintHttpBody
# -----------------------------------------------------------------------


class _MockResponse:
    def __init__(self, dataBody):
        self._dataBody = dataBody

    def read(self):
        return self._dataBody

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_fnSendHttp_success_prints_body(modCli, capsys):
    dictTarget = {"sUrl": "http://x/api", "dictBody": {"a": 1}}
    with patch.object(
        modCli.urllib.request, "urlopen",
        return_value=_MockResponse(b'{"ok":true}'),
    ):
        iCode = modCli.fnSendHttp(
            dictTarget, "tok", "POST", False,
        )
    assert iCode == 0
    assert "ok" in capsys.readouterr().out


def test_fnSendHttp_4xx_returns_one(modCli, capsys):
    err = urllib.error.HTTPError(
        "http://x", 404, "Not Found", {}, io.BytesIO(b'{"detail":"nope"}'),
    )
    with patch.object(
        modCli.urllib.request, "urlopen", side_effect=err,
    ):
        iCode = modCli.fnSendHttp(
            {"sUrl": "http://x", "dictBody": {}}, "tok", "GET", False,
        )
    assert iCode == 1
    assert "nope" in capsys.readouterr().out


def test_fnSendHttp_5xx_returns_two(modCli, capsys):
    err = urllib.error.HTTPError(
        "http://x", 503, "down", {}, io.BytesIO(b""),
    )
    with patch.object(
        modCli.urllib.request, "urlopen", side_effect=err,
    ):
        iCode = modCli.fnSendHttp(
            {"sUrl": "http://x", "dictBody": {}}, "tok", "POST", False,
        )
    assert iCode == 2


def test_fnSendHttp_http_error_with_broken_body_swallowed(modCli, capsys):
    """A broken ``errHttp.read()`` must not propagate; body becomes empty."""
    err = urllib.error.HTTPError(
        "http://x", 500, "down", {}, None,
    )

    def fnBrokenRead():
        raise RuntimeError("broken")

    err.read = fnBrokenRead
    with patch.object(
        modCli.urllib.request, "urlopen", side_effect=err,
    ):
        iCode = modCli.fnSendHttp(
            {"sUrl": "http://x", "dictBody": {}}, "tok", "POST", False,
        )
    assert iCode == 2


def test_fnSendHttp_401_exits_with_auth_message(modCli, capsys):
    err = urllib.error.HTTPError(
        "http://x", 401, "no", {}, io.BytesIO(b""),
    )
    with patch.object(
        modCli.urllib.request, "urlopen", side_effect=err,
    ):
        with pytest.raises(SystemExit) as excInfo:
            modCli.fnSendHttp(
                {"sUrl": "http://x", "dictBody": {}},
                "tok", "POST", False,
            )
    assert excInfo.value.code == 4
    assert "token rejected" in capsys.readouterr().err.lower()


def test_fnSendHttp_connection_timeout_exits(modCli, capsys):
    with patch.object(
        modCli.urllib.request, "urlopen",
        side_effect=socket.timeout("slow"),
    ):
        with pytest.raises(SystemExit) as excInfo:
            modCli.fnSendHttp(
                {"sUrl": "http://x", "dictBody": {}},
                "tok", "POST", False,
            )
    assert excInfo.value.code == 4
    assert "unreachable" in capsys.readouterr().err.lower()


def test_fnSendHttp_urlerror_exits(modCli, capsys):
    with patch.object(
        modCli.urllib.request, "urlopen",
        side_effect=urllib.error.URLError("boom"),
    ):
        with pytest.raises(SystemExit) as excInfo:
            modCli.fnSendHttp(
                {"sUrl": "http://x", "dictBody": {}},
                "tok", "POST", False,
            )
    assert excInfo.value.code == 4


def test_print_http_body_json_mode_compact(modCli, capsys):
    modCli._fnPrintHttpBody(b'{"b":2,"a":1}', True)
    sOut = capsys.readouterr().out.strip()
    # JSON mode uses json.dumps(objParsed) with default order.
    assert sOut in ('{"b": 2, "a": 1}', '{"a": 1, "b": 2}')


def test_print_http_body_non_json_passthrough(modCli, capsys):
    modCli._fnPrintHttpBody(b"plain text", False)
    assert capsys.readouterr().out.strip() == "plain text"


def test_print_http_body_empty_is_no_op(modCli, capsys):
    modCli._fnPrintHttpBody(b"", False)
    assert capsys.readouterr().out == ""


# -----------------------------------------------------------------------
# WebSocket path: framing helpers, handshake, stream
# -----------------------------------------------------------------------


class _MockSocket:
    """Socket double that delivers bytes from a scripted queue.

    Each element in ``listRecvChunks`` is a distinct "network
    delivery": a single ``recv`` call is served from the head chunk
    and never crosses into the next chunk. Missing length is reported
    as ``b""`` (EOF).
    """

    def __init__(self, listRecvChunks):
        self._listQueue = [bytes(c) for c in listRecvChunks]
        self.listSent = []
        self._iTimeout = None

    def sendall(self, data):
        self.listSent.append(data)

    def recv(self, iMax):
        if not self._listQueue:
            return b""
        dataHead = self._listQueue[0]
        if len(dataHead) <= iMax:
            self._listQueue.pop(0)
            return dataHead
        dataReturn = dataHead[:iMax]
        self._listQueue[0] = dataHead[iMax:]
        return dataReturn

    def settimeout(self, iTimeout):
        self._iTimeout = iTimeout

    def close(self):
        pass


def test_fnSendWsText_short_frame_format(modCli):
    sock = _MockSocket([])
    modCli.fnSendWsText(sock, "hi")
    dataFrame = sock.listSent[0]
    # First byte is 0x81 (FIN + text opcode).
    assert dataFrame[0] == 0x81
    # Payload length byte has 0x80 mask bit set + len == 2.
    assert dataFrame[1] == 0x82
    # Mask (4B) + masked payload (2B) == 6 more bytes.
    assert len(dataFrame) == 2 + 4 + 2


def test_fnSendWsText_medium_frame_uses_126(modCli):
    sock = _MockSocket([])
    modCli.fnSendWsText(sock, "x" * 200)
    dataFrame = sock.listSent[0]
    assert dataFrame[0] == 0x81
    assert dataFrame[1] == 0x80 | 126
    # 2-byte length follows.
    iLen = int.from_bytes(dataFrame[2:4], "big")
    assert iLen == 200


def test_fnSendWsText_large_frame_uses_127(modCli):
    sock = _MockSocket([])
    sPayload = "y" * 70000
    modCli.fnSendWsText(sock, sPayload)
    dataFrame = sock.listSent[0]
    assert dataFrame[1] == 0x80 | 127
    iLen = int.from_bytes(dataFrame[2:10], "big")
    assert iLen == 70000


def test_fsRecvWsFrame_reads_text(modCli):
    # Text frame, len=2, payload "hi" -> 0x81 0x02 h i
    sock = _MockSocket([bytes([0x81, 0x02]), b"hi"])
    assert modCli.fsRecvWsFrame(sock) == "hi"


def test_fsRecvWsFrame_close_opcode_returns_empty(modCli):
    sock = _MockSocket([bytes([0x88, 0x00])])
    assert modCli.fsRecvWsFrame(sock) == ""


def test_fsRecvWsFrame_ping_returns_sentinel(modCli):
    sock = _MockSocket([bytes([0x89, 0x00])])
    assert modCli.fsRecvWsFrame(sock) == "__PING__"


def test_fsRecvWsFrame_other_opcode_skip(modCli):
    sock = _MockSocket([bytes([0x82, 0x00])])
    assert modCli.fsRecvWsFrame(sock) == "__SKIP__"


def test_fsRecvWsFrame_len_126_reads_extended(modCli):
    dataPayload = b"a" * 300
    sock = _MockSocket([
        bytes([0x81, 126]),
        (300).to_bytes(2, "big"),
        dataPayload,
    ])
    assert modCli.fsRecvWsFrame(sock) == "a" * 300


def test_fsRecvWsFrame_len_127_reads_extended(modCli):
    dataPayload = b"b" * 70000
    sock = _MockSocket([
        bytes([0x81, 127]),
        (70000).to_bytes(8, "big"),
        dataPayload,
    ])
    assert modCli.fsRecvWsFrame(sock) == "b" * 70000


def test_fsRecvWsFrame_short_header_returns_empty(modCli):
    sock = _MockSocket([b""])
    assert modCli.fsRecvWsFrame(sock) == ""


def test_recv_exact_returns_empty_on_short_read(modCli):
    sock = _MockSocket([b"ab", b""])
    assert modCli._fnRecvExact(sock, 4) == b""


def test_ws_handshake_happy_path(modCli):
    sock = _MockSocket([b"HTTP/1.1 101 Switching\r\n\r\n"])
    modCli.fnWebsocketHandshake(sock, "h", 80, "/ws")
    # A handshake request was sent.
    assert b"GET /ws" in sock.listSent[0]
    assert b"Upgrade: websocket" in sock.listSent[0]


def test_ws_handshake_401_exits(modCli, capsys):
    sock = _MockSocket([b"HTTP/1.1 401 Unauthorized\r\n\r\n"])
    with pytest.raises(SystemExit) as excInfo:
        modCli.fnWebsocketHandshake(sock, "h", 80, "/ws")
    assert excInfo.value.code == 4
    assert "token rejected" in capsys.readouterr().err.lower()


def test_ws_handshake_other_reject_exits(modCli, capsys):
    sock = _MockSocket([b"HTTP/1.1 403 Forbidden\r\n\r\n"])
    with pytest.raises(SystemExit) as excInfo:
        modCli.fnWebsocketHandshake(sock, "h", 80, "/ws")
    assert excInfo.value.code == 4


def test_ws_handshake_empty_response_exits(modCli, capsys):
    sock = _MockSocket([b""])
    with pytest.raises(SystemExit) as excInfo:
        modCli.fnWebsocketHandshake(sock, "h", 80, "/ws")
    assert excInfo.value.code == 4


# -----------------------------------------------------------------------
# ftWsEndpoint + fnRunWebsocket
# -----------------------------------------------------------------------


def test_ftWsEndpoint_plain_http(modCli, dictValidEnv):
    sHost, iPort, sPath, bTls = modCli.ftWsEndpoint(dictValidEnv)
    assert sHost == "host.docker.internal"
    assert iPort == 8050
    assert sPath.startswith("/ws/pipeline/c-1")
    assert "sToken=" in sPath
    assert bTls is False


def test_ftWsEndpoint_https_defaults_443(modCli):
    dictEnv = {
        "VAIBIFY_HOST_URL": "https://example.com",
        "VAIBIFY_SESSION_TOKEN": "t",
        "VAIBIFY_CONTAINER_ID": "c",
    }
    sHost, iPort, sPath, bTls = modCli.ftWsEndpoint(dictEnv)
    assert iPort == 443
    assert bTls is True


def test_fnRunWebsocket_refuses_tls(modCli, capsys):
    dictEnv = {
        "VAIBIFY_HOST_URL": "https://example.com",
        "VAIBIFY_SESSION_TOKEN": "t",
        "VAIBIFY_CONTAINER_ID": "c",
    }
    with pytest.raises(SystemExit) as excInfo:
        modCli.fnRunWebsocket(dictEnv, {"sAction": "runAll"}, False)
    assert excInfo.value.code == 4


def test_fnRunWebsocket_connect_error_exits(modCli, dictValidEnv):
    with patch.object(
        modCli.socket, "create_connection",
        side_effect=OSError("refused"),
    ):
        with pytest.raises(SystemExit) as excInfo:
            modCli.fnRunWebsocket(
                dictValidEnv, {"sAction": "runAll"}, False,
            )
    assert excInfo.value.code == 4


def test_fnRunWebsocket_full_flow_completed(
    modCli, dictValidEnv,
):
    """End-to-end: connect, handshake, send, stream, completed."""
    sockMock = _MockSocket([
        # Handshake: 101 accepted.
        b"HTTP/1.1 101 Switching Protocols\r\n\r\n",
        # Frame 1: progress event.
        bytes([0x81, 0x1E])
        + b'{"sType":"progress","iStep":1}',
        # Frame 2: completed.
        bytes([0x81, 0x2D])
        + b'{"sType":"completed","iExitCode":0}',
    ])
    # Adjust lengths to match payload byte count precisely.
    dataProgress = b'{"sType":"progress","iStep":1}'
    dataComplete = b'{"sType":"completed","iExitCode":0}'
    sockMock = _MockSocket([
        b"HTTP/1.1 101 Switching Protocols\r\n\r\n",
        bytes([0x81, len(dataProgress)]) + dataProgress,
        bytes([0x81, len(dataComplete)]) + dataComplete,
    ])
    with patch.object(
        modCli.socket, "create_connection", return_value=sockMock,
    ):
        iCode = modCli.fnRunWebsocket(
            dictValidEnv, {"sAction": "runAll"}, False,
        )
    assert iCode == 0


def test_fnRunWebsocket_error_event_returns_one(modCli, dictValidEnv):
    dataError = b'{"sType":"error","sMessage":"boom"}'
    sock = _MockSocket([
        b"HTTP/1.1 101 Switching Protocols\r\n\r\n",
        bytes([0x81, len(dataError)]) + dataError,
    ])
    with patch.object(
        modCli.socket, "create_connection", return_value=sock,
    ):
        iCode = modCli.fnRunWebsocket(
            dictValidEnv, {"sAction": "runAll"}, False,
        )
    assert iCode == 1


def test_stream_ws_events_closed_connection_returns_one(
    modCli, capsys,
):
    sock = _MockSocket([b""])  # immediate close
    assert modCli._fnStreamWsEvents(sock, False) == 1


def test_stream_ws_events_skips_non_json(modCli):
    dataBad = b"not json"
    dataDone = b'{"sType":"completed","iExitCode":7}'
    sock = _MockSocket([
        bytes([0x81, len(dataBad)]) + dataBad,
        bytes([0x81, len(dataDone)]) + dataDone,
    ])
    assert modCli._fnStreamWsEvents(sock, False) == 7


def test_stream_ws_events_pipeline_error_returns_one(modCli):
    data = b'{"sType":"pipelineError"}'
    sock = _MockSocket([bytes([0x81, len(data)]) + data])
    assert modCli._fnStreamWsEvents(sock, False) == 1


def test_stream_ws_events_skips_ping_and_skip_frames(modCli):
    """Ping and non-text frames are skipped, then completed fires."""
    dataDone = b'{"sType":"completed","iExitCode":0}'
    sock = _MockSocket([
        bytes([0x89, 0x00]),  # ping -> __PING__
        bytes([0x82, 0x00]),  # binary -> __SKIP__
        bytes([0x81, len(dataDone)]) + dataDone,
    ])
    assert modCli._fnStreamWsEvents(sock, False) == 0


def test_print_event_text_mode(modCli, capsys):
    modCli._fnPrintEvent(
        {"sType": "progress", "iStep": 1, "sMessage": "x"}, False,
    )
    sOut = capsys.readouterr().out.strip()
    assert sOut.startswith("[progress]")
    assert "iStep=1" in sOut
    assert "sMessage=x" in sOut


def test_print_event_json_mode(modCli, capsys):
    modCli._fnPrintEvent({"sType": "x", "iStep": 2}, True)
    dictParsed = json.loads(capsys.readouterr().out)
    assert dictParsed == {"sType": "x", "iStep": 2}


# -----------------------------------------------------------------------
# main() dispatch: --list, --describe, unknown action, usage
# -----------------------------------------------------------------------


def _fnPatchCatalog(modCli, tmp_path, dictCatalog):
    """Write catalog JSON to tmp_path and return a patch context."""
    sPath = tmp_path / "cat.json"
    sPath.write_text(json.dumps(dictCatalog), encoding="utf-8")
    return patch.object(modCli, "S_CATALOG_JSON_PATH", str(sPath))


def _fnPatchSession(modCli, tmp_path, dictEnv):
    sPath = tmp_path / "s.env"
    _fnWriteEnvFile(sPath, dictEnv)
    return patch.object(modCli, "S_SESSION_ENV_PATH", str(sPath))


def test_main_list(modCli, tmp_path, dictSampleCatalog, capsys):
    argv = ["vaibify-do", "--list"]
    with _fnPatchCatalog(modCli, tmp_path, dictSampleCatalog):
        with patch.object(sys, "argv", argv):
            modCli.main()
    assert "run-all" in capsys.readouterr().out


def test_main_describe_known(
    modCli, tmp_path, dictSampleCatalog, capsys,
):
    argv = ["vaibify-do", "--describe", "run-all"]
    with _fnPatchCatalog(modCli, tmp_path, dictSampleCatalog):
        with patch.object(sys, "argv", argv):
            modCli.main()
    dictParsed = json.loads(capsys.readouterr().out)
    assert dictParsed["sName"] == "run-all"


def test_main_describe_unknown_exits(
    modCli, tmp_path, dictSampleCatalog,
):
    argv = ["vaibify-do", "--describe", "no-such"]
    with _fnPatchCatalog(modCli, tmp_path, dictSampleCatalog):
        with patch.object(sys, "argv", argv):
            with pytest.raises(SystemExit) as excInfo:
                modCli.main()
    assert excInfo.value.code == 2


def test_main_no_action_shows_usage(
    modCli, tmp_path, dictSampleCatalog, capsys,
):
    argv = ["vaibify-do"]
    with _fnPatchCatalog(modCli, tmp_path, dictSampleCatalog):
        with patch.object(sys, "argv", argv):
            with pytest.raises(SystemExit) as excInfo:
                modCli.main()
    assert excInfo.value.code == 2
    assert "usage" in capsys.readouterr().err.lower()


def test_main_unknown_action_exits(
    modCli, tmp_path, dictSampleCatalog,
):
    argv = ["vaibify-do", "ghost-action"]
    with _fnPatchCatalog(modCli, tmp_path, dictSampleCatalog):
        with patch.object(sys, "argv", argv):
            with pytest.raises(SystemExit) as excInfo:
                modCli.main()
    assert excInfo.value.code == 2


def test_main_dry_run_ws(
    modCli, tmp_path, dictSampleCatalog, dictValidEnv, capsys,
):
    argv = ["vaibify-do", "--dry-run", "run-all"]
    with _fnPatchCatalog(modCli, tmp_path, dictSampleCatalog):
        with _fnPatchSession(modCli, tmp_path, dictValidEnv):
            with patch.object(sys, "argv", argv):
                with pytest.raises(SystemExit) as excInfo:
                    modCli.main()
    assert excInfo.value.code == 0
    assert "runAll" in capsys.readouterr().out


def test_main_user_only_refused(
    modCli, tmp_path, dictSampleCatalog, dictValidEnv, capsys,
):
    argv = ["vaibify-do", "delete-step", "2"]
    with _fnPatchCatalog(modCli, tmp_path, dictSampleCatalog):
        with _fnPatchSession(modCli, tmp_path, dictValidEnv):
            with patch.object(sys, "argv", argv):
                with pytest.raises(SystemExit) as excInfo:
                    modCli.main()
    assert excInfo.value.code == 5
    dictParsed = json.loads(capsys.readouterr().out)
    assert dictParsed["sRefusal"] == "user-only-action"


def test_main_dispatch_http_invokes_urlopen(
    modCli, tmp_path, dictSampleCatalog, dictValidEnv,
):
    argv = ["vaibify-do", "run-unit-tests", "3"]
    with _fnPatchCatalog(modCli, tmp_path, dictSampleCatalog):
        with _fnPatchSession(modCli, tmp_path, dictValidEnv):
            with patch.object(
                modCli.urllib.request, "urlopen",
                return_value=_MockResponse(b'{"ok":true}'),
            ) as mockOpen:
                with patch.object(sys, "argv", argv):
                    with pytest.raises(SystemExit) as excInfo:
                        modCli.main()
    assert excInfo.value.code == 0
    mockOpen.assert_called_once()


def test_main_dispatch_ws_invokes_websocket(
    modCli, tmp_path, dictSampleCatalog, dictValidEnv,
):
    dataComplete = b'{"sType":"completed","iExitCode":0}'
    sock = _MockSocket([
        b"HTTP/1.1 101 Switching Protocols\r\n\r\n",
        bytes([0x81, len(dataComplete)]) + dataComplete,
    ])
    argv = ["vaibify-do", "run-all"]
    with _fnPatchCatalog(modCli, tmp_path, dictSampleCatalog):
        with _fnPatchSession(modCli, tmp_path, dictValidEnv):
            with patch.object(
                modCli.socket, "create_connection", return_value=sock,
            ):
                with patch.object(sys, "argv", argv):
                    with pytest.raises(SystemExit) as excInfo:
                        modCli.main()
    assert excInfo.value.code == 0


def test_runpy_entrypoint_invokes_main(tmp_path, dictSampleCatalog):
    """Running the file as __main__ hits the ``if __name__`` guard.

    The module's ``main()`` calls ``fnFail`` (sys.exit) when the
    session env is missing; we expect that exit path.
    """
    import runpy
    sys_argv_saved = list(sys.argv)
    import pathlib
    sDefault = pathlib.Path("/tmp/vaibify-action-catalog.json")
    bCreated = False
    if not sDefault.exists():
        try:
            sDefault.write_text(
                json.dumps(dictSampleCatalog), encoding="utf-8",
            )
            bCreated = True
        except OSError:
            pytest.skip("cannot write /tmp catalog")
    try:
        sys.argv = ["vaibify-do", "--list"]
        # --list returns normally (no exit), so run_path completes.
        runpy.run_path(
            str(_S_VAIBIFY_DO_PATH),
            run_name="__main__",
        )
    finally:
        sys.argv = sys_argv_saved
        if bCreated:
            sDefault.unlink()
