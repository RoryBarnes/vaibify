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


def test_a_single_pinned_value_does_not_declare_determinism():
    """One value satisfied the whole gate until the 2026-08-30 ruling.

    Both of these asserted the opposite. They are inverted rather than
    removed because "a pinned thread count is a determinism
    declaration" is the belief the ruling overturned: it says nothing
    about whether last-digit variance is acceptable, and the two are
    independent sources of run-to-run difference.
    """
    assert not fbWorkflowDeclaresDeterminism(
        {"dictDeterminism": {S_OMP_NUM_THREADS_KEY: 1}},
    )
    assert not fbWorkflowDeclaresDeterminism(
        {"dictDeterminism": {S_ACCEPT_BLAS_WAIVER_KEY: True}},
    )


def test_answering_all_three_questions_declares_determinism():
    """The passing shape, so the inversions above are not the whole story.

    Every answer here is the DECLINING one, which must pass: the gate
    asks that each question be answered, never that it be answered a
    particular way.
    """
    assert fbWorkflowDeclaresDeterminism({"dictDeterminism": {
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
            {"sName": "S", "bUnseededRandomnessWarning": True},
        ],
    }
    listIssues = flistAuditWorkflow(dictWorkflow)
    assert any("bUnseededRandomnessWarning" in sIssue
               for sIssue in listIssues)


@pytest.mark.parametrize("waiverValue", [True, "false", 1, "no"])
@pytest.mark.falsification
def test_a_legacy_waiver_value_never_attests_on_its_own(waiverValue):
    """The legacy waiver KEY satisfies nothing after the 2026-08-30 ruling.

    It used to be one arm of an OR, and this test used to pin that
    only a literal ``True`` took that arm. Determinism is now three
    separately-answered questions, so a stored VALUE — of any truthiness,
    including the literal True a real waiver wrote — is not an answer.
    Only the recorded answer keys are. A hand-edited ``"no"`` and a
    genuine tick are equally powerless here, which is stricter than the
    branch this replaces.

    The literal-True case is where the value still matters: the
    migration promotes it to an answer. That is asserted in
    ``testDeterminismRowMatchesItsGate`` where the migration lives, not
    here where the gate does.

    Kills: reading the legacy waiver key as the blasVariance answer,
    which silently credits every project that opened the old form.
    """
    from vaibify.reproducibility.determinismGate import (
        flistUnansweredDeterminismQuestions,
    )
    dictWorkflow = {
        "dictDeterminism": {S_ACCEPT_BLAS_WAIVER_KEY: waiverValue},
    }
    # Asserted PER QUESTION, not on the aggregate gate. With three
    # questions ANDed together, a mutation that wrongly answers one of
    # them leaves the other two open and the gate still refuses -- so
    # `fbWorkflowDeclaresDeterminism(...) is False` is true of the bug
    # and the fix alike, and this test survived exactly that mutation
    # before the assertion moved down here.
    assert "blasVariance" in flistUnansweredDeterminismQuestions(
        dictWorkflow,
    ), "the legacy waiver value was read as an answer"
    assert fbWorkflowDeclaresDeterminism(dictWorkflow) is False


@pytest.mark.falsification
def test_mkl_cbwr_alone_no_longer_declares_determinism():
    """One answer does not carry the other two (2026-08-30 ruling).

    This test asserted the opposite until the gate became an AND: an
    MKL pin alone satisfied the whole requirement, so a project could
    attest at Level 3 having said nothing about numeric variance or
    thread count. Inverted rather than deleted, because "MKL alone is
    enough" is precisely the belief the ruling overturned and the one
    a future reader is most likely to restore.

    Kills: restoring the OR in fbWorkflowDeclaresDeterminism.
    """
    from vaibify.reproducibility.determinismGate import (
        flistUnansweredDeterminismQuestions,
    )
    dictWorkflow = {
        "dictDeterminism": {S_MKL_CBWR_KEY: "COMPATIBLE"},
    }
    # Per question, for the reason given above: an AND over three
    # hides any mutation that flips only one of them, and this test
    # survived one before the assertion moved down here.
    assert "mklMode" in flistUnansweredDeterminismQuestions(
        dictWorkflow,
    ), "a stored MKL value was read as an answer to the MKL question"
    assert fbWorkflowDeclaresDeterminism(dictWorkflow) is False
    assert flistAuditWorkflow(dictWorkflow), (
        "a workflow answering one question of three reports no issue"
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

    One per unanswered question since 2026-08-30, and the assertion
    moved off the key name deliberately: the issue text is
    researcher-facing and must not contain ``dictDeterminism`` at all,
    so matching on it would now pin the very thing the copy fix
    removed.

    Kills: flistAuditWorkflow returning no issues for an unanswered
    workflow, which leaves the three rows red with nothing to act on.
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

    Kills: flistAuditWorkflow:214 —
    'dictStep.get("bUnseededRandomnessWarning") is True' weakened to
    truthy test
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
