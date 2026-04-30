"""Tests for uncovered branches in vaibify.gui.llmInvoker."""

import sys
from unittest.mock import MagicMock, patch

import pytest

from vaibify.gui.llmInvoker import (
    _fbOutputLooksValid,
    _fnRaiseClaudeError,
    _fsInvokeLlm,
    _fsRemoveOldTestSection,
    fnEnsureClaudeMdInstructions,
    fsGenerateViaApi,
    fsReadFileFromContainer,
)


# ---------------------------------------------------------------
# fsGenerateViaApi: lines 311-324
# ---------------------------------------------------------------


def test_fsGenerateViaApi_missing_package_raises():
    """When anthropic is not installed, raise RuntimeError with hint."""
    with patch.dict(sys.modules, {"anthropic": None}):
        with pytest.raises(RuntimeError) as excInfo:
            fsGenerateViaApi("hello", "sk-key")
    assert "anthropic" in str(excInfo.value)
    assert "pip install" in str(excInfo.value)


def test_fsGenerateViaApi_calls_client_and_returns_text():
    """Verify the anthropic client is invoked with expected args."""
    mockText = MagicMock()
    mockText.text = "generated output"
    mockMessage = MagicMock()
    mockMessage.content = [mockText]
    mockClient = MagicMock()
    mockClient.messages.create.return_value = mockMessage
    mockAnthropic = MagicMock()
    mockAnthropic.Anthropic.return_value = mockClient
    with patch.dict(sys.modules, {"anthropic": mockAnthropic}):
        sResult = fsGenerateViaApi("my prompt", "sk-key")
    assert sResult == "generated output"
    mockAnthropic.Anthropic.assert_called_once_with(api_key="sk-key")
    kwargs = mockClient.messages.create.call_args[1]
    assert kwargs["messages"][0]["content"] == "my prompt"
    assert kwargs["max_tokens"] == 4096


# ---------------------------------------------------------------
# fnEnsureClaudeMdInstructions: early-return when marker present
# ---------------------------------------------------------------


def test_fnEnsureClaudeMdInstructions_skips_when_marker_present():
    """Line 238: if marker already in file, return without writing."""
    mockDocker = MagicMock()
    sExistingContent = "existing text\n<!-- vaibify-test-instructions-v11 -->\n"
    with patch(
        "vaibify.gui.llmInvoker.fsReadFileFromContainer",
        return_value=sExistingContent,
    ):
        fnEnsureClaudeMdInstructions(mockDocker, "cid")
    mockDocker.fnWriteFile.assert_not_called()


def test_fnEnsureClaudeMdInstructions_writes_when_marker_absent():
    mockDocker = MagicMock()
    with patch(
        "vaibify.gui.llmInvoker.fsReadFileFromContainer",
        return_value="old content\n",
    ):
        fnEnsureClaudeMdInstructions(mockDocker, "cid")
    mockDocker.fnWriteFile.assert_called_once()
    _tArgs, _dictKw = mockDocker.fnWriteFile.call_args
    sContent = _tArgs[2].decode("utf-8")
    assert "<!-- vaibify-test-instructions-v11 -->" in sContent


# ---------------------------------------------------------------
# _fsInvokeLlm: valid-output-on-failure branch (line 365)
# ---------------------------------------------------------------


def test_fsInvokeLlm_returns_output_on_nonzero_exit_with_valid_content():
    mockDocker = MagicMock()
    # Simulate claude CLI exiting nonzero but producing valid-looking text.
    with patch(
        "vaibify.gui.llmInvoker.ftResultGenerateViaClaude",
        return_value=(1, "def test_foo():\n    pass"),
    ):
        sResult = _fsInvokeLlm(
            mockDocker, "cid", "prompt", False, None,
        )
    assert "def test_foo" in sResult


def test_fsInvokeLlm_raises_on_nonzero_exit_with_invalid_content():
    mockDocker = MagicMock()
    with patch(
        "vaibify.gui.llmInvoker.ftResultGenerateViaClaude",
        return_value=(1, "some error output"),
    ):
        with pytest.raises(RuntimeError):
            _fsInvokeLlm(mockDocker, "cid", "prompt", False, None)


def test_fsInvokeLlm_returns_output_on_zero_exit():
    mockDocker = MagicMock()
    with patch(
        "vaibify.gui.llmInvoker.ftResultGenerateViaClaude",
        return_value=(0, "anything"),
    ):
        sResult = _fsInvokeLlm(
            mockDocker, "cid", "prompt", False, None,
        )
    assert sResult == "anything"


def test_fsInvokeLlm_dispatches_to_api_when_flag_set():
    with patch(
        "vaibify.gui.llmInvoker.fsGenerateViaApi",
        return_value="from api",
    ) as mockApi:
        sResult = _fsInvokeLlm(
            MagicMock(), "cid", "prompt", True, "sk-key",
        )
    assert sResult == "from api"
    mockApi.assert_called_once_with("prompt", "sk-key")


# ---------------------------------------------------------------
# _fbOutputLooksValid branches
# ---------------------------------------------------------------


def test_fbOutputLooksValid_detects_code_fence():
    assert _fbOutputLooksValid("some text\n```python\npass\n```\n")


def test_fbOutputLooksValid_detects_test_function():
    assert _fbOutputLooksValid("def test_something():\n    pass\n")


def test_fbOutputLooksValid_detects_standards_field():
    assert _fbOutputLooksValid('{"listStandards": []}')


def test_fbOutputLooksValid_rejects_plain_error():
    assert not _fbOutputLooksValid("error: bad happened")


def test_fbOutputLooksValid_rejects_empty():
    assert not _fbOutputLooksValid("")


# ---------------------------------------------------------------
# _fnRaiseClaudeError hint detection
# ---------------------------------------------------------------


def test_fnRaiseClaudeError_not_logged_in_includes_hint():
    with pytest.raises(RuntimeError) as excInfo:
        _fnRaiseClaudeError(1, "You are not logged in.")
    assert "not authenticated" in str(excInfo.value)


def test_fnRaiseClaudeError_login_url_includes_hint():
    with pytest.raises(RuntimeError) as excInfo:
        _fnRaiseClaudeError(1, "Please visit /login page")
    assert "not authenticated" in str(excInfo.value)


def test_fnRaiseClaudeError_generic_error_no_hint():
    with pytest.raises(RuntimeError) as excInfo:
        _fnRaiseClaudeError(2, "timed out")
    sMsg = str(excInfo.value)
    assert "exit 2" in sMsg
    assert "timed out" in sMsg
    assert "not authenticated" not in sMsg


# ---------------------------------------------------------------
# _fsRemoveOldTestSection
# ---------------------------------------------------------------


def test_fsRemoveOldTestSection_no_marker_returns_unchanged():
    sContent = "some content\nno marker here\n"
    assert _fsRemoveOldTestSection(sContent) == sContent


def test_fsRemoveOldTestSection_strips_from_marker():
    from vaibify.gui.llmInvoker import _CLAUDE_MD_MARKER
    sContent = "before\n" + _CLAUDE_MD_MARKER + "\nafter\n"
    sResult = _fsRemoveOldTestSection(sContent)
    assert "after" not in sResult
    assert sResult.rstrip() == "before"


# ---------------------------------------------------------------
# fsReadFileFromContainer exception handling
# ---------------------------------------------------------------


def test_fsReadFileFromContainer_returns_empty_on_exception():
    mockDocker = MagicMock()
    mockDocker.fbaFetchFile.side_effect = RuntimeError("not found")
    assert fsReadFileFromContainer(mockDocker, "cid", "/x") == ""


def test_fsReadFileFromContainer_decodes_utf8():
    mockDocker = MagicMock()
    mockDocker.fbaFetchFile.return_value = "hello".encode("utf-8")
    assert fsReadFileFromContainer(mockDocker, "cid", "/x") == "hello"


def test_fsReadFileFromContainer_replaces_invalid_bytes():
    mockDocker = MagicMock()
    # 0xff is not valid utf-8 start byte; errors="replace" keeps the read.
    mockDocker.fbaFetchFile.return_value = b"valid\xffbytes"
    sResult = fsReadFileFromContainer(mockDocker, "cid", "/x")
    assert "valid" in sResult
    assert "bytes" in sResult
