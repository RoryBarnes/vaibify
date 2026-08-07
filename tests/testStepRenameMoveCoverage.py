"""Behaviour tests for the step-rename directory-move and script scan.

The rename cascade must move the step directory with git (falling back
to plain mv), refuse to clobber an existing target, treat a never-run
step as a JSON-only rename, and warn about sibling scripts that spell
the old directory name. These drive those paths with the container
exec mocked.
"""

from unittest.mock import MagicMock

import pytest

from vaibify.gui import stepRename


def _fnConnection(listResults):
    """A docker stand-in whose ftResultExecuteCommand replays results."""
    conn = MagicMock()
    conn.ftResultExecuteCommand.side_effect = list(listResults)
    return conn


# --- flistScanScriptsForOldName ---

def test_scan_returns_empty_when_directory_not_renamed():
    assert stepRename.flistScanScriptsForOldName(
        MagicMock(), "cid", {}, {"bDirectoryRenamed": False}) == []


def test_scan_returns_empty_without_a_project_repo():
    dictPlan = {"bDirectoryRenamed": True, "sOldDirectory": "Old"}
    assert stepRename.flistScanScriptsForOldName(
        MagicMock(), "cid", {"sProjectRepoPath": ""}, dictPlan) == []


def test_scan_reports_only_scripts_that_mention_the_old_name():
    dictWorkflow = {
        "sProjectRepoPath": "/workspace/repo",
        "listSteps": [
            {"saStepScripts": ["Old/mentions.py", "Old/clean.py"]},
        ],
    }
    dictPlan = {"bDirectoryRenamed": True, "sOldDirectory": "Old"}
    # grep exits 0 (found) for the first script, 1 (not found) for the
    # second; the sorted order is clean.py then mentions.py.
    conn = _fnConnection([(1, ""), (0, "")])
    listMentioning = stepRename.flistScanScriptsForOldName(
        conn, "cid", dictWorkflow, dictPlan)
    assert listMentioning == ["Old/mentions.py"]


# --- _fsBuildDirectoryMoveCommand ---

def test_move_command_prefers_git_mv_with_plain_mv_fallback():
    sCommand = stepRename._fsBuildDirectoryMoveCommand(
        "/repo", "Old", "New")
    assert "git mv" in sCommand
    assert "|| mv" in sCommand
    assert "cd " in sCommand


# --- _fnMoveStepDirectory ---

def _fdictPlan():
    return {"sOldDirectory": "Old", "sNewDirectory": "New"}


def test_move_refuses_to_clobber_an_existing_target():
    conn = _fnConnection([(0, "")])  # test -e new → exists
    with pytest.raises(ValueError):
        stepRename._fnMoveStepDirectory(conn, "cid", "/repo", _fdictPlan())


def test_move_is_json_only_when_old_directory_absent():
    # test -e new → absent (1); test -d old → absent (non-zero).
    conn = _fnConnection([(1, ""), (1, "")])
    assert stepRename._fnMoveStepDirectory(
        conn, "cid", "/repo", _fdictPlan()) is False


def test_move_succeeds_when_directory_present():
    # test -e new absent; test -d old present; git mv exit 0.
    conn = _fnConnection([(1, ""), (0, ""), (0, "")])
    assert stepRename._fnMoveStepDirectory(
        conn, "cid", "/repo", _fdictPlan()) is True


def test_move_raises_when_git_mv_fails():
    conn = _fnConnection([(1, ""), (0, ""), (1, "permission denied")])
    with pytest.raises(RuntimeError):
        stepRename._fnMoveStepDirectory(conn, "cid", "/repo", _fdictPlan())
