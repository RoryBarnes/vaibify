"""Tests for path traversal protection on API endpoints."""

import pytest
from fastapi import HTTPException

from vaibify.gui.pipelineServer import fnValidatePathWithinRoot


def test_fnValidatePathWithinRoot_valid_subpath():
    sResult = fnValidatePathWithinRoot(
        "/workspace/project/file.txt", "/workspace"
    )
    assert sResult == "/workspace/project/file.txt"


def test_fnValidatePathWithinRoot_exact_root():
    sResult = fnValidatePathWithinRoot("/workspace", "/workspace")
    assert sResult == "/workspace"


def test_fnValidatePathWithinRoot_traversal_dotdot():
    with pytest.raises(HTTPException) as excInfo:
        fnValidatePathWithinRoot("/workspace/../etc/passwd", "/workspace")
    assert excInfo.value.status_code == 403


def test_fnValidatePathWithinRoot_traversal_encoded():
    with pytest.raises(HTTPException) as excInfo:
        fnValidatePathWithinRoot("/workspace/../../root", "/workspace")
    assert excInfo.value.status_code == 403


def test_fnValidatePathWithinRoot_traversal_absolute():
    with pytest.raises(HTTPException) as excInfo:
        fnValidatePathWithinRoot("/etc/shadow", "/workspace")
    assert excInfo.value.status_code == 403


def test_fnValidatePathWithinRoot_nested_valid():
    sResult = fnValidatePathWithinRoot(
        "/workspace/.vaibify/logs/run_001.log",
        "/workspace/.vaibify/logs",
    )
    assert sResult == "/workspace/.vaibify/logs/run_001.log"


def test_fnValidatePathWithinRoot_logs_traversal():
    with pytest.raises(HTTPException) as excInfo:
        fnValidatePathWithinRoot(
            "/workspace/.vaibify/logs/../../secrets.env",
            "/workspace/.vaibify/logs",
        )
    assert excInfo.value.status_code == 403


def test_fnValidatePathWithinRoot_prefix_attack():
    """Ensure /workspace-evil is not treated as inside /workspace."""
    with pytest.raises(HTTPException) as excInfo:
        fnValidatePathWithinRoot("/workspace-evil/file", "/workspace")
    assert excInfo.value.status_code == 403
