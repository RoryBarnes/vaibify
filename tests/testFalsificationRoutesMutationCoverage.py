"""Mutation-coverage tests for ``falsificationRoutes.py`` helpers.

The run/summarize error paths build the record the researcher sees when
a falsification run cannot be graded. Each test here pins an observable
guarantee a surviving mutant broke: exact zero counts on an error
record, a real (subtraction-derived) duration, the reason string
carrying the container output tail, and the tail truncation itself.
The exec boundary is mocked — these helpers' own arithmetic and string
handling are the subject, not the container.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from vaibify.gui.routes.falsificationRoutes import (
    _fdictRunMutationSync,
    _fdictSummarizeMutationSession,
    _fsTailOfOutput,
)

pytestmark = pytest.mark.falsification


class _ConnectionStub:
    """Docker stand-in returning a canned exec result."""

    def __init__(self, iExitCode, sStdout="", sStderr=""):
        self._result = SimpleNamespace(
            iExitCode=iExitCode, sStdout=sStdout, sStderr=sStderr,
        )

    def texecRunInContainerStreamed(self, sContainerId, sCommand):
        return self._result


def _fdictSummarize(connectionStub):
    """Invoke the summarize helper with fixed identity arguments."""
    return _fdictSummarizeMutationSession(
        connectionStub, "container1", "/work/session.sqlite",
        "sha256:digest", "deterministic", "8.3.1", 0.0,
    )


def test_exec_failure_record_reports_exact_zero_counts():
    """A failed cosmic-ray exec grades nothing: all counts exactly 0.

    A nonzero count fabricated onto an error record would let the
    manuscript cite mutants that were never graded, and the duration
    must be a real elapsed float, not the residue of a rewritten
    subtraction.

    Kills: In _fdictRunMutationSync's exec-failure record, replace the
    zero mutant counts with a nonzero total.
    """
    connectionStub = _ConnectionStub(
        3, sStdout="", sStderr="toml parse error",
    )
    with patch(
        "vaibify.gui.routes.falsificationRoutes."
        "_fsPrepareMutationSession",
        return_value="/work/session.sqlite",
    ), patch(
        "vaibify.gui.routes.falsificationRoutes."
        "fsCurrentFalsificationDigest",
        return_value="sha256:digest",
    ), patch(
        "vaibify.gui.routes.falsificationRoutes."
        "flistFalsificationDigestPaths",
        return_value=[],
    ):
        dictRecord = _fdictRunMutationSync(
            {"docker": connectionStub}, "container1", {}, {},
            {"sClassification": "deterministic"}, None, "8.3.1",
        )
    assert dictRecord["sStatus"] == "error"
    assert dictRecord["iMutantsTotal"] == 0
    assert dictRecord["iMutantsKilled"] == 0
    assert dictRecord["iMutantsSurvived"] == 0
    assert isinstance(dictRecord["fDurationSeconds"], float)
    assert dictRecord["fDurationSeconds"] >= 0.0
    assert "cosmic-ray exited 3" in dictRecord["sReason"]
    assert "toml parse error" in dictRecord["sReason"]


def test_unparseable_summary_reason_carries_the_output_tail():
    """The summary-failure reason must append the real container output.

    The reason string is the only diagnostic the researcher gets; it
    must be built by concatenation (prefix plus tail), and the record's
    duration must again be a genuine float difference.

    Kills: In _fdictSummarizeMutationSession's summary-failure record,
    replace the reason-string ``+`` concatenation with ``%``.
    """
    connectionStub = _ConnectionStub(
        1, sStdout="", sStderr="sqlite locked",
    )
    dictRecord = _fdictSummarize(connectionStub)
    assert dictRecord["sStatus"] == "error"
    assert dictRecord["sReason"].startswith(
        "could not summarize the mutation session: ",
    )
    assert dictRecord["sReason"].endswith("sqlite locked")
    assert isinstance(dictRecord["fDurationSeconds"], float)
    assert dictRecord["fDurationSeconds"] >= 0.0


def test_graded_summary_builds_an_attained_record():
    """A parsed summary lands its counts on an attained record.

    Kills: In _fdictSummarizeMutationSession's attained record, swap
    the killed and survived counts.
    """
    sSummaryJson = (
        '{"iMutantsTotal": 4, "iMutantsKilled": 3,'
        ' "iMutantsSurvived": 1, "listSurvivors": ["m1"]}'
    )
    connectionStub = _ConnectionStub(0, sStdout=sSummaryJson)
    dictRecord = _fdictSummarize(connectionStub)
    assert dictRecord["sStatus"] == "attained"
    assert dictRecord["iMutantsTotal"] == 4
    assert dictRecord["iMutantsKilled"] == 3
    assert dictRecord["iMutantsSurvived"] == 1
    assert dictRecord["listSurvivors"] == ["m1"]


def test_zero_graded_mutants_is_an_error_not_an_attainment():
    """An empty grading run must never present as attained.

    Kills: In _fdictSummarizeMutationSession, weaken the zero-graded
    guard so an empty run falls through to the attained record.
    """
    sSummaryJson = (
        '{"iMutantsTotal": 0, "iMutantsKilled": 0, "iMutantsSurvived": 0}'
    )
    connectionStub = _ConnectionStub(0, sStdout=sSummaryJson)
    dictRecord = _fdictSummarize(connectionStub)
    assert dictRecord["sStatus"] == "error"
    assert "graded no mutants" in dictRecord["sReason"]


def test_tail_of_output_keeps_the_last_characters():
    """The tail is the LAST ``iMaxCharacters`` — the error is at the end.

    Negating the slice start would return the head of a long output,
    which for a long traceback discards the actual exception line.

    Kills: In _fsTailOfOutput, replace the negative slice start with
    ``not iMaxCharacters`` so the head is returned instead.
    """
    resultExec = SimpleNamespace(
        sStdout="H" * 700, sStderr="the actual error",
    )
    sTail = _fsTailOfOutput(resultExec, iMaxCharacters=600)
    assert len(sTail) == 600
    assert sTail.endswith("the actual error")
    resultShort = SimpleNamespace(sStdout="brief", sStderr="")
    assert _fsTailOfOutput(resultShort) == "brief"
