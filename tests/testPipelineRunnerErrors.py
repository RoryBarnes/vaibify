"""Tests for error paths in vaibify.gui.pipelineRunner."""

import asyncio

import pytest

from vaibify.gui.pipelineRunner import (
    _fbShouldRunStep,
    _fnEmitBanner,
)


def test_fbShouldRunStep_enabled():
    dictStep = {"bEnabled": True}
    assert _fbShouldRunStep(dictStep, 1, 1) is True


def test_fbShouldRunStep_disabled():
    dictStep = {"bEnabled": False}
    assert _fbShouldRunStep(dictStep, 1, 1) is False


def test_fbShouldRunStep_before_start():
    dictStep = {"bEnabled": True}
    assert _fbShouldRunStep(dictStep, 1, 3) is False


def test_fbShouldRunStep_at_start():
    dictStep = {"bEnabled": True}
    assert _fbShouldRunStep(dictStep, 3, 3) is True


def test_fbShouldRunStep_interactive_eligible():
    dictStep = {"bEnabled": True, "bInteractive": True}
    assert _fbShouldRunStep(dictStep, 1, 1) is True


def test_fbShouldRunStep_default_enabled():
    dictStep = {}
    assert _fbShouldRunStep(dictStep, 2, 1) is True


def test_fnEmitBanner_emits_five_lines():
    listCaptured = []

    async def fnCapture(dictMsg):
        listCaptured.append(dictMsg)

    asyncio.get_event_loop().run_until_complete(
        _fnEmitBanner(fnCapture, 1, "Compute")
    )
    assert len(listCaptured) == 5
    listLines = [d["sLine"] for d in listCaptured]
    assert "Step 01 - Compute" in listLines


def test_fnEmitBanner_output_type():
    listCaptured = []

    async def fnCapture(dictMsg):
        listCaptured.append(dictMsg)

    asyncio.get_event_loop().run_until_complete(
        _fnEmitBanner(fnCapture, 2, "Plot")
    )
    for dictMsg in listCaptured:
        assert dictMsg["sType"] == "output"
