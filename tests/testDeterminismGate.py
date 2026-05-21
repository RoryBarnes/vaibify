"""Tests for ``vaibify/reproducibility/determinismGate.py``.

Covers the four new anti-pattern detectors (clock-derived seeds,
explicit torch opt-outs, /dev/urandom reads, ``secrets`` module use)
plus the workflow-level BLAS / OMP declaration helper.
"""

import textwrap

import pytest

from vaibify.reproducibility.determinismGate import (
    S_ACCEPT_BLAS_WAIVER_KEY,
    S_MKL_CBWR_KEY,
    S_OMP_NUM_THREADS_KEY,
    fbWorkflowDeclaresDeterminism,
    flistAuditScriptAntiPatterns,
    flistAuditWorkflow,
)


def _fnWriteScript(tmp_path, sName, sBody):
    """Write a Python script to tmp_path and return its absolute path."""
    pathFile = tmp_path / sName
    pathFile.write_text(sBody)
    return str(pathFile)


def test_script_without_anti_patterns_is_clean(tmp_path):
    sPath = _fnWriteScript(tmp_path, "clean.py", textwrap.dedent("""
        import numpy as np

        np.random.seed(42)
        np.random.rand(10)
    """))
    assert flistAuditScriptAntiPatterns(sPath) == []


def test_clock_derived_seed_is_flagged(tmp_path):
    sPath = _fnWriteScript(tmp_path, "clockSeed.py", textwrap.dedent("""
        import numpy as np
        import time

        np.random.seed(time.time())
    """))
    listIssues = flistAuditScriptAntiPatterns(sPath)
    assert any("clock" in sIssue.lower() for sIssue in listIssues)


def test_datetime_now_seed_is_flagged(tmp_path):
    sPath = _fnWriteScript(tmp_path, "dt.py", textwrap.dedent("""
        import random
        import datetime

        random.seed(datetime.datetime.now().microsecond)
    """))
    listIssues = flistAuditScriptAntiPatterns(sPath)
    assert any("clock" in sIssue.lower() for sIssue in listIssues)


def test_os_urandom_seed_is_flagged(tmp_path):
    sPath = _fnWriteScript(tmp_path, "urandom.py", textwrap.dedent("""
        import os
        import random

        random.seed(os.urandom(4))
    """))
    listIssues = flistAuditScriptAntiPatterns(sPath)
    assert any("clock" in sIssue.lower() or "urandom" in sIssue.lower()
               for sIssue in listIssues)


def test_torch_opt_out_is_flagged(tmp_path):
    sPath = _fnWriteScript(tmp_path, "tt.py", textwrap.dedent("""
        import torch
        torch.use_deterministic_algorithms(False)
    """))
    listIssues = flistAuditScriptAntiPatterns(sPath)
    assert any("deterministic" in sIssue.lower() for sIssue in listIssues)


def test_secrets_module_use_is_flagged(tmp_path):
    sPath = _fnWriteScript(tmp_path, "secret.py", textwrap.dedent("""
        import secrets
        x = secrets.token_hex(8)
    """))
    listIssues = flistAuditScriptAntiPatterns(sPath)
    assert any("secrets" in sIssue.lower() for sIssue in listIssues)


def test_dev_urandom_read_is_flagged(tmp_path):
    sPath = _fnWriteScript(tmp_path, "read.py", textwrap.dedent("""
        with open('/dev/urandom', 'rb') as f:
            x = f.read(4)
    """))
    listIssues = flistAuditScriptAntiPatterns(sPath)
    assert any("urandom" in sIssue.lower() for sIssue in listIssues)


def test_missing_script_returns_explicit_issue(tmp_path):
    listIssues = flistAuditScriptAntiPatterns(str(tmp_path / "nope.py"))
    assert len(listIssues) == 1


def test_workflow_declares_determinism_via_omp():
    dictWorkflow = {
        "dictDeterminism": {S_OMP_NUM_THREADS_KEY: 1},
    }
    assert fbWorkflowDeclaresDeterminism(dictWorkflow)


def test_workflow_declares_determinism_via_blas_waiver():
    dictWorkflow = {
        "dictDeterminism": {S_ACCEPT_BLAS_WAIVER_KEY: True},
    }
    assert fbWorkflowDeclaresDeterminism(dictWorkflow)


def test_workflow_without_determinism_block_fails():
    assert not fbWorkflowDeclaresDeterminism({})
    assert not fbWorkflowDeclaresDeterminism(
        {"dictDeterminism": {}}
    )


def test_audit_surfaces_unseeded_step_warning():
    dictWorkflow = {
        "dictDeterminism": {S_MKL_CBWR_KEY: "COMPATIBLE"},
        "listSteps": [
            {"sName": "S", "bUnseededRandomnessWarning": True},
        ],
    }
    listIssues = flistAuditWorkflow(dictWorkflow)
    assert any("bUnseededRandomnessWarning" in sIssue
               for sIssue in listIssues)
