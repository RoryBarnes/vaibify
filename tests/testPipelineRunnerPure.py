"""Tests for pure functions in vaibify.gui.pipelineRunner."""

from vaibify.gui.pipelineRunner import (
    fsShellQuote,
    fsGenerateLogFilename,
    _fbShouldRunStep,
)


def test_fsGenerateLogFilename_contains_timestamp():
    sFilename = fsGenerateLogFilename("Test Workflow")
    assert "Test_Workflow" in sFilename
    assert sFilename.endswith(".log")


def test_fsGenerateLogFilename_sanitizes_special_chars():
    sFilename = fsGenerateLogFilename("GJ 1132 (XUV)")
    assert "(" not in sFilename
    assert " " not in sFilename


def test_fbShouldRunStep_enabled():
    dictStep = {"bEnabled": True}
    assert _fbShouldRunStep(dictStep, 1, 1) is True


def test_fbShouldRunStep_disabled():
    dictStep = {"bEnabled": False}
    assert _fbShouldRunStep(dictStep, 1, 1) is False


def test_fbShouldRunStep_below_start_step():
    dictStep = {"bEnabled": True}
    assert _fbShouldRunStep(dictStep, 2, 3) is False


def test_fbShouldRunStep_interactive_eligible():
    dictStep = {"bEnabled": True, "bInteractive": True}
    assert _fbShouldRunStep(dictStep, 1, 1) is True


def test_fbShouldRunStep_default_enabled():
    dictStep = {}
    assert _fbShouldRunStep(dictStep, 3, 1) is True


def test_fsShellQuote_simple():
    assert fsShellQuote("hello") == "'hello'"


def test_fsShellQuote_with_single_quotes():
    sResult = fsShellQuote("it's")
    assert "it" in sResult
    assert "s" in sResult
