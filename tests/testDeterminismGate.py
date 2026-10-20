"""Tests for ``vaibify/reproducibility/determinismGate.py``.

Covers the four new anti-pattern detectors (clock-derived seeds,
explicit torch opt-outs, /dev/urandom reads, ``secrets`` module use)
plus the workflow-level repeatability question, which since 2026-10-17
is the one determinism question (design ruling 2).
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


def test_a_single_pinned_value_does_not_declare_determinism():
    """A legacy value declares nothing.

    One value satisfied the whole gate until the 2026-08-30 ruling, and
    none does now: the one question asks which execution profile runs,
    and a pinned thread count or a waiver does not say.
    """
    assert not fbWorkflowDeclaresDeterminism(
        {"dictDeterminism": {S_OMP_NUM_THREADS_KEY: 1}},
    )
    assert not fbWorkflowDeclaresDeterminism(
        {"dictDeterminism": {S_ACCEPT_BLAS_WAIVER_KEY: True}},
    )


def test_either_answer_declares_determinism():
    """The passing shapes, so the inversions above are not the whole story.

    Either answer passes: the gate asks that the question be answered,
    never that it be answered a particular way. The three legacy
    answers, all given, do not answer it (design D7).
    """
    for sAnswer, sProfile in (("speed", "speed-v1"), ("across", "across-v1")):
        assert fbWorkflowDeclaresDeterminism({"dictDeterminism": {
            "sRepeatabilityAnswer": sAnswer, "sExecutionProfile": sProfile,
        }})
    assert not fbWorkflowDeclaresDeterminism({"dictDeterminism": {
        "sBlasVarianceAnswer": "rejected",
        "sOmpThreadsAnswer": "unpinned",
        "sMklModeAnswer": "not-used",
    }})


def test_workflow_without_determinism_block_fails():
    assert not fbWorkflowDeclaresDeterminism({})
    assert not fbWorkflowDeclaresDeterminism(
        {"dictDeterminism": {}}
    )


def test_audit_surfaces_unseeded_step_warning():
    dictWorkflow = {
        "dictDeterminism": {S_MKL_CBWR_KEY: "COMPATIBLE"},
        "listSteps": [
            {"sName": "S",
             "dictVerification": {"bUnseededRandomnessWarning": True}},
        ],
    }
    listIssues = flistAuditWorkflow(dictWorkflow)
    assert any("bUnseededRandomnessWarning" in sIssue
               for sIssue in listIssues)


@pytest.mark.parametrize("waiverValue", [True, "false", 1, "no"])
@pytest.mark.falsification
def test_a_legacy_waiver_value_never_attests_on_its_own(waiverValue):
    """The legacy waiver KEY answers nothing.

    It used to be one arm of an OR. A stored VALUE -- of any
    truthiness, including the literal True a real waiver wrote -- is
    not an answer to the one question, which chooses an execution
    profile a waiver never named. A hand-edited ``"no"`` and a genuine
    tick are equally powerless here.

    Kills: reading the legacy waiver as an answer to the question,
    which silently credits every project that opened an old form.
    """
    from vaibify.reproducibility.determinismGate import (
        flistUnansweredDeterminismQuestions,
    )
    dictWorkflow = {
        "dictDeterminism": {S_ACCEPT_BLAS_WAIVER_KEY: waiverValue},
    }
    assert "repeatability" in flistUnansweredDeterminismQuestions(
        dictWorkflow,
    ), "the legacy waiver value was read as an answer"
    assert fbWorkflowDeclaresDeterminism(dictWorkflow) is False


@pytest.mark.falsification
def test_mkl_cbwr_alone_no_longer_declares_determinism():
    """A stored MKL mode answers nothing.

    An MKL pin alone satisfied the whole requirement until 2026-08-30.
    It is inverted rather than deleted, because "MKL alone is enough"
    is the belief a future reader is most likely to restore -- and no
    profile covers MKL at all.

    Kills: reading a stored MKL mode as an answer to the question.
    """
    from vaibify.reproducibility.determinismGate import (
        flistUnansweredDeterminismQuestions,
    )
    dictWorkflow = {
        "dictDeterminism": {S_MKL_CBWR_KEY: "COMPATIBLE"},
    }
    assert "repeatability" in flistUnansweredDeterminismQuestions(
        dictWorkflow,
    ), "a stored MKL value was read as an answer to the question"
    assert fbWorkflowDeclaresDeterminism(dictWorkflow) is False
    assert flistAuditWorkflow(dictWorkflow), (
        "a workflow with only a legacy value reports no issue"
    )


@pytest.mark.falsification
def test_bare_imported_seed_with_clock_is_flagged(tmp_path):
    """``from numpy.random import seed; seed(time.time())`` is flagged.

    The bare-name seed call (no attribute prefix) must be recognised
    as a seed function so its clock-derived argument is caught.

    Kills: _fbCallIsSeedFunction:97-98 — the ast.Name branch removed
    """
    sPath = _fnWriteScript(tmp_path, "bareSeed.py", textwrap.dedent("""
        from numpy.random import seed
        import time

        seed(time.time())
    """))
    listIssues = flistAuditScriptAntiPatterns(sPath)
    assert any("clock" in sIssue.lower() for sIssue in listIssues)


@pytest.mark.falsification
def test_bare_os_urandom_outside_seed_is_flagged(tmp_path):
    """``os.urandom(...)`` outside a seed call is flagged as urandom.

    This isolates the regex urandom detector: the call is not nested
    in a seed(...) so the AST clock path contributes nothing.

    Kills: _flistFindUrandomReads:159 / _REGEX_OS_URANDOM:46 — the
    os.urandom( match deleted
    """
    sPath = _fnWriteScript(tmp_path, "salt.py", textwrap.dedent("""
        import os
        x = os.urandom(4)
    """))
    listIssues = flistAuditScriptAntiPatterns(sPath)
    assert any("urandom" in sIssue.lower() for sIssue in listIssues)


@pytest.mark.falsification
def test_missing_determinism_block_is_an_issue():
    """A workflow with no determinism declaration surfaces issues.

    One per unanswered question, and the assertion is off the key name
    deliberately: the issue text is researcher-facing and must not
    contain ``dictDeterminism`` at all.

    Kills: flistAuditWorkflow returning no issues for an unanswered
    workflow, which leaves the row red with nothing to act on.
    """
    from vaibify.reproducibility.determinismGate import (
        LIST_DETERMINISM_QUESTIONS,
    )
    for dictWorkflow in ({}, {"dictDeterminism": {}}):
        listIssues = flistAuditWorkflow(dictWorkflow)
        assert len(listIssues) == len(LIST_DETERMINISM_QUESTIONS)
        assert not any("dictDeterminism" in sIssue
                       for sIssue in listIssues), (
            "a researcher-facing issue names the JSON key"
        )


@pytest.mark.falsification
def test_from_secrets_import_is_flagged(tmp_path):
    """``from secrets import token_hex`` is flagged as an entropy source.

    The from-import form must match even when the subsequent use is a
    bare ``token_hex(...)`` call with no ``secrets.`` attribute prefix.

    Kills: _REGEX_SECRETS_MODULE:43-44 — the 'from\\s+secrets\\s+import\\b'
    alternative removed
    """
    sPath = _fnWriteScript(tmp_path, "fromSecrets.py", textwrap.dedent("""
        from secrets import token_hex
        x = token_hex(8)
    """))
    listIssues = flistAuditScriptAntiPatterns(sPath)
    assert any("secrets" in sIssue.lower() for sIssue in listIssues)


@pytest.mark.falsification
def test_unseeded_warning_requires_literal_true():
    """Only ``bUnseededRandomnessWarning is True`` surfaces a warning.

    A truthy-but-not-True value ('false') must not produce a spurious
    warning issue, and the otherwise-clean workflow yields no issues.

    Kills: pipelineUtils.fbStepHasUnseededRandomnessWarning --
    'bUnseededRandomnessWarning") is True' weakened to a truthy test
    """
    dictWorkflow = {
        "dictDeterminism": {S_MKL_CBWR_KEY: "COMPATIBLE"},
        "listSteps": [
            {"sName": "S",
             "dictVerification": {"bUnseededRandomnessWarning": "false"}},
        ],
    }
    listIssues = flistAuditWorkflow(dictWorkflow)
    assert not any("bUnseededRandomnessWarning" in sIssue
                   for sIssue in listIssues)
