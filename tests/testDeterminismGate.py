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


@pytest.mark.parametrize("waiverValue", ["false", 1, "no"])
def test_blas_waiver_requires_literal_true(waiverValue):
    """Only ``bAcceptBlasVariance is True`` honours the waiver branch.

    A truthy-but-not-True value (a hand-edited JSON string like
    'false', or 1, or 'no') must NOT falsely attest determinism.
    """
    dictWorkflow = {
        "dictDeterminism": {S_ACCEPT_BLAS_WAIVER_KEY: waiverValue},
    }
    assert fbWorkflowDeclaresDeterminism(dictWorkflow) is False


def test_mkl_cbwr_alone_declares_determinism():
    """An sMklCbwr pin alone counts as a determinism declaration."""
    dictWorkflow = {
        "dictDeterminism": {S_MKL_CBWR_KEY: "COMPATIBLE"},
    }
    assert fbWorkflowDeclaresDeterminism(dictWorkflow) is True
    listIssues = flistAuditWorkflow(dictWorkflow)
    assert not any("dictDeterminism" in sIssue for sIssue in listIssues)


def test_bare_imported_seed_with_clock_is_flagged(tmp_path):
    """``from numpy.random import seed; seed(time.time())`` is flagged.

    The bare-name seed call (no attribute prefix) must be recognised
    as a seed function so its clock-derived argument is caught.
    """
    sPath = _fnWriteScript(tmp_path, "bareSeed.py", textwrap.dedent("""
        from numpy.random import seed
        import time

        seed(time.time())
    """))
    listIssues = flistAuditScriptAntiPatterns(sPath)
    assert any("clock" in sIssue.lower() for sIssue in listIssues)


def test_bare_os_urandom_outside_seed_is_flagged(tmp_path):
    """``os.urandom(...)`` outside a seed call is flagged as urandom.

    This isolates the regex urandom detector: the call is not nested
    in a seed(...) so the AST clock path contributes nothing.
    """
    sPath = _fnWriteScript(tmp_path, "salt.py", textwrap.dedent("""
        import os
        x = os.urandom(4)
    """))
    listIssues = flistAuditScriptAntiPatterns(sPath)
    assert any("urandom" in sIssue.lower() for sIssue in listIssues)


def test_missing_determinism_block_is_an_issue():
    """A workflow with no determinism declaration surfaces an issue."""
    assert any("dictDeterminism" in sIssue
               for sIssue in flistAuditWorkflow({}))
    assert any("dictDeterminism" in sIssue
               for sIssue in flistAuditWorkflow({"dictDeterminism": {}}))


def test_from_secrets_import_is_flagged(tmp_path):
    """``from secrets import token_hex`` is flagged as an entropy source.

    The from-import form must match even when the subsequent use is a
    bare ``token_hex(...)`` call with no ``secrets.`` attribute prefix.
    """
    sPath = _fnWriteScript(tmp_path, "fromSecrets.py", textwrap.dedent("""
        from secrets import token_hex
        x = token_hex(8)
    """))
    listIssues = flistAuditScriptAntiPatterns(sPath)
    assert any("secrets" in sIssue.lower() for sIssue in listIssues)


def test_unseeded_warning_requires_literal_true():
    """Only ``bUnseededRandomnessWarning is True`` surfaces a warning.

    A truthy-but-not-True value ('false') must not produce a spurious
    warning issue, and the otherwise-clean workflow yields no issues.
    """
    dictWorkflow = {
        "dictDeterminism": {S_MKL_CBWR_KEY: "COMPATIBLE"},
        "listSteps": [
            {"sName": "S", "bUnseededRandomnessWarning": "false"},
        ],
    }
    listIssues = flistAuditWorkflow(dictWorkflow)
    assert not any("bUnseededRandomnessWarning" in sIssue
                   for sIssue in listIssues)
