"""Behaviour tests for the falsification-route helpers.

These cover the validation guards, the in-flight-task lookup, the
per-mutant test-command builder, and the session-summary parsing/record
construction — the logic behind the "run/view a mutation session"
endpoints — with the container exec mocked.
"""

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from vaibify.gui.routes import falsificationRoutes as fr
from vaibify.docker.dockerConnection import ExecResult


# --- validation guards ---

def test_require_step_returns_the_step():
    dictWorkflow = {"listSteps": [{"sName": "A"}, {"sName": "B"}]}
    assert fr._fdictRequireStep(dictWorkflow, 1)["sName"] == "B"


@pytest.mark.parametrize("iBad", [-1, 2, 99])
def test_require_step_out_of_range_is_404(iBad):
    dictWorkflow = {"listSteps": [{"sName": "A"}, {"sName": "B"}]}
    with pytest.raises(HTTPException) as excinfo:
        fr._fdictRequireStep(dictWorkflow, iBad)
    assert excinfo.value.status_code == 404


def test_require_project_repo_returns_path():
    assert fr._fsRequireProjectRepo(
        {"sProjectRepoPath": "/workspace/repo"}) == "/workspace/repo"


def test_require_project_repo_missing_is_409():
    with pytest.raises(HTTPException) as excinfo:
        fr._fsRequireProjectRepo({"sProjectRepoPath": "  "})
    assert excinfo.value.status_code == 409


# --- in-flight status lookup ---

def test_in_flight_status_none_when_no_task():
    assert fr._fdictInFlightStatus("cid", 0) is None


def test_in_flight_status_returns_live_status(monkeypatch):
    taskLive = MagicMock()
    taskLive.done.return_value = False
    monkeypatch.setitem(
        fr._DICT_FALSIFICATION_TASKS, ("cid", 3),
        {"task": taskLive, "dictStatus": {"sState": "running"}},
    )
    assert fr._fdictInFlightStatus("cid", 3) == {"sState": "running"}


def test_in_flight_status_none_when_task_done(monkeypatch):
    taskDone = MagicMock()
    taskDone.done.return_value = True
    monkeypatch.setitem(
        fr._DICT_FALSIFICATION_TASKS, ("cid", 4),
        {"task": taskDone, "dictStatus": {"sState": "running"}},
    )
    assert fr._fdictInFlightStatus("cid", 4) is None


# --- summary parsing / tail ---

def test_parse_summary_reads_the_json_line():
    result = ExecResult(
        iExitCode=0,
        sStdout='noise\n{"iMutantsTotal": 5, "iMutantsKilled": 5}\n',
        sStderr="",
    )
    dictSummary = fr._fdictParseSummaryOutput(result)
    assert dictSummary["iMutantsTotal"] == 5


def test_parse_summary_none_on_nonzero_exit():
    result = ExecResult(iExitCode=1, sStdout='{"iMutantsTotal": 1}',
                        sStderr="")
    assert fr._fdictParseSummaryOutput(result) is None


def test_parse_summary_none_without_a_matching_json_line():
    result = ExecResult(iExitCode=0, sStdout="not json\n{}\n", sStderr="")
    assert fr._fdictParseSummaryOutput(result) is None


def test_tail_of_output_returns_last_characters():
    result = ExecResult(iExitCode=1, sStdout="a" * 100,
                        sStderr="b" * 700)
    sTail = fr._fsTailOfOutput(result, iMaxCharacters=50)
    assert len(sTail) == 50
    assert set(sTail) == {"b"}


# --- per-mutant command builder ---

def test_build_mutation_test_command_reruns_data_then_pytest():
    dictCtx = {"variables": lambda sId: {"sPlotDirectory": "Plot"}}
    dictWorkflow = {"listSteps": [{"sName": "S", "sDirectory": "S"}]}
    dictStep = {
        "sName": "S", "sDirectory": "S",
        "saDataCommands": ["python generate.py"],
    }
    sCommand = fr._fsBuildMutationTestCommand(
        dictCtx, "cid", dictWorkflow, dictStep)
    assert sCommand.startswith("bash -c ")
    assert "python generate.py" in sCommand
    assert "test_quantitative.py" in sCommand


# --- session summary record construction ---

def _fnConnectionReturning(result):
    conn = MagicMock()
    conn.texecRunInContainerStreamed.return_value = result
    return conn


def test_summarize_session_error_when_summary_unparseable():
    conn = _fnConnectionReturning(
        ExecResult(iExitCode=1, sStdout="", sStderr="traceback"))
    dictRecord = fr._fdictSummarizeMutationSession(
        conn, "cid", "/s/session.sqlite", "digest", "class", "8.4.6",
        0.0,
    )
    assert dictRecord["sStatus"] == fr.S_STATUS_ERROR
    assert "could not summarize" in dictRecord["sReason"]


def test_summarize_session_error_when_zero_mutants():
    conn = _fnConnectionReturning(ExecResult(
        iExitCode=0,
        sStdout='{"iMutantsTotal": 0, "iMutantsKilled": 0, '
                '"iMutantsSurvived": 0}',
        sStderr="",
    ))
    dictRecord = fr._fdictSummarizeMutationSession(
        conn, "cid", "/s/session.sqlite", "digest", "class", "8.4.6",
        0.0,
    )
    assert dictRecord["sStatus"] == fr.S_STATUS_ERROR
    assert "no mutants" in dictRecord["sReason"]


def test_summarize_session_attained_when_mutants_graded():
    conn = _fnConnectionReturning(ExecResult(
        iExitCode=0,
        sStdout='{"iMutantsTotal": 4, "iMutantsKilled": 4, '
                '"iMutantsSurvived": 0, "listSurvivors": []}',
        sStderr="",
    ))
    dictRecord = fr._fdictSummarizeMutationSession(
        conn, "cid", "/s/session.sqlite", "digest", "class", "8.4.6",
        0.0,
    )
    assert dictRecord["sStatus"] == fr.S_STATUS_ATTAINED
