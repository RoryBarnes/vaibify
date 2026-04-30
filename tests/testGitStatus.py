"""Tests for the host-side gitStatus module.

Follows the AGENTS.md testing discipline: mock subprocess.run at the
module boundary, never invoke real git. Uses tmp_path for workspace
directories so the os.path.isdir check inside ``_fbIsGitRepo`` can
short-circuit without a hidden git invocation.
"""

import subprocess
from unittest.mock import patch

import pytest

from vaibify.gui import gitStatus


def _fnFakeRun(dictResponses):
    """Return a callable that dispatches on the argv signature of each call.

    dictResponses keys are ``argv tuples`` (after 'git' + hardening
    flags are stripped) and values are (returncode, stdout, stderr).
    Any call not in the dict raises so tests don't silently pass on
    unexpected invocations.
    """
    def _fnImpl(listCmd, cwd=None, env=None, capture_output=False, text=False):
        listUser = _flistStripHardening(listCmd)
        tKey = tuple(listUser)
        if tKey not in dictResponses:
            raise AssertionError(
                f"Unexpected git invocation: {listUser}"
            )
        iRc, sOut, sErr = dictResponses[tKey]
        return subprocess.CompletedProcess(
            args=listCmd, returncode=iRc,
            stdout=sOut, stderr=sErr,
        )
    return _fnImpl


def _flistStripHardening(listCmd):
    """Strip 'git' + the hardening -c flags from a full argv."""
    listInner = list(listCmd[1:])
    listHardening = list(gitStatus.LIST_GIT_HARDENING_CONFIG)
    while listHardening and listInner[:len(listHardening)] != listHardening:
        listHardening = listHardening[2:]
    return listInner[len(listHardening):]


# ----------------------------------------------------------------------
# fdictEmptyStatus
# ----------------------------------------------------------------------


def test_fdictEmptyStatus_shape():
    dictResult = gitStatus.fdictEmptyStatus("no repo")
    assert dictResult["bIsRepo"] is False
    assert dictResult["sHeadSha"] == ""
    assert dictResult["sBranch"] == ""
    assert dictResult["iAhead"] == 0
    assert dictResult["iBehind"] == 0
    assert dictResult["dictFileStates"] == {}
    assert dictResult["sReason"] == "no repo"
    assert "sRefreshedAt" in dictResult


# ----------------------------------------------------------------------
# fsRunGit
# ----------------------------------------------------------------------


def test_fsRunGit_traps_filenotfound():
    with patch("vaibify.gui.gitStatus.subprocess.run") as mockRun:
        mockRun.side_effect = FileNotFoundError("git not installed")
        result = gitStatus.fsRunGit(["status"], sCwd="/nowhere")
    assert result.returncode == 127
    assert "git not installed" in result.stderr


def test_fsRunGit_injects_hardening_flags(tmp_path):
    with patch("vaibify.gui.gitStatus.subprocess.run") as mockRun:
        mockRun.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr="",
        )
        gitStatus.fsRunGit(["status"], sCwd=str(tmp_path))
    listCalledArgs = mockRun.call_args[0][0]
    for sFlag in gitStatus.LIST_GIT_HARDENING_CONFIG:
        assert sFlag in listCalledArgs


def test_fsRunGit_disables_terminal_prompt(tmp_path):
    with patch("vaibify.gui.gitStatus.subprocess.run") as mockRun:
        mockRun.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr="",
        )
        gitStatus.fsRunGit(["status"], sCwd=str(tmp_path))
    dictEnv = mockRun.call_args.kwargs["env"]
    assert dictEnv["GIT_TERMINAL_PROMPT"] == "0"


# ----------------------------------------------------------------------
# fdictGitStatusForWorkspace
# ----------------------------------------------------------------------


def test_fdictGitStatusForWorkspace_returns_empty_for_missing_dir():
    dictResult = gitStatus.fdictGitStatusForWorkspace("/does/not/exist")
    assert dictResult["bIsRepo"] is False
    assert "Not a git repository" in dictResult["sReason"]


def test_fdictGitStatusForWorkspace_returns_empty_for_non_repo_dir(tmp_path):
    dictResponses = {
        ("rev-parse", "--is-inside-work-tree"): (128, "", "not a repo"),
    }
    with patch(
        "vaibify.gui.gitStatus.subprocess.run",
        side_effect=_fnFakeRun(dictResponses),
    ):
        dictResult = gitStatus.fdictGitStatusForWorkspace(str(tmp_path))
    assert dictResult["bIsRepo"] is False


def test_fdictGitStatusForWorkspace_clean_repo(tmp_path):
    sPorcelain = (
        "# branch.oid abcdef0123456789abcdef0123456789abcdef01\n"
        "# branch.head main\n"
        "# branch.upstream origin/main\n"
        "# branch.ab +0 -0\n"
    )
    dictResponses = {
        ("rev-parse", "--is-inside-work-tree"): (0, "true\n", ""),
        (
            "status", "--porcelain=v2", "--branch",
            "--untracked-files=all",
        ): (0, sPorcelain, ""),
        ("rev-parse", "HEAD"): (
            0, "abcdef0123456789abcdef0123456789abcdef01\n", "",
        ),
    }
    with patch(
        "vaibify.gui.gitStatus.subprocess.run",
        side_effect=_fnFakeRun(dictResponses),
    ):
        dictResult = gitStatus.fdictGitStatusForWorkspace(str(tmp_path))
    assert dictResult["bIsRepo"] is True
    assert dictResult["sBranch"] == "main"
    assert dictResult["iAhead"] == 0
    assert dictResult["iBehind"] == 0
    assert dictResult["sHeadSha"].startswith("abcdef")
    assert dictResult["dictFileStates"] == {}


def test_fdictGitStatusForWorkspace_ahead_count_parsed(tmp_path):
    sPorcelain = (
        "# branch.head feature\n"
        "# branch.ab +3 -0\n"
    )
    dictResponses = {
        ("rev-parse", "--is-inside-work-tree"): (0, "true\n", ""),
        (
            "status", "--porcelain=v2", "--branch",
            "--untracked-files=all",
        ): (0, sPorcelain, ""),
        ("rev-parse", "HEAD"): (0, "deadbeef" * 5 + "\n", ""),
    }
    with patch(
        "vaibify.gui.gitStatus.subprocess.run",
        side_effect=_fnFakeRun(dictResponses),
    ):
        dictResult = gitStatus.fdictGitStatusForWorkspace(str(tmp_path))
    assert dictResult["iAhead"] == 3
    assert dictResult["iBehind"] == 0
    assert dictResult["sBranch"] == "feature"


def test_fdictGitStatusForWorkspace_behind_count_parsed(tmp_path):
    sPorcelain = (
        "# branch.head main\n"
        "# branch.ab +0 -5\n"
    )
    dictResponses = {
        ("rev-parse", "--is-inside-work-tree"): (0, "true\n", ""),
        (
            "status", "--porcelain=v2", "--branch",
            "--untracked-files=all",
        ): (0, sPorcelain, ""),
        ("rev-parse", "HEAD"): (0, "0" * 40 + "\n", ""),
    }
    with patch(
        "vaibify.gui.gitStatus.subprocess.run",
        side_effect=_fnFakeRun(dictResponses),
    ):
        dictResult = gitStatus.fdictGitStatusForWorkspace(str(tmp_path))
    assert dictResult["iBehind"] == 5


def test_fdictGitStatusForWorkspace_untracked_file(tmp_path):
    sPorcelain = (
        "# branch.head main\n"
        "# branch.ab +0 -0\n"
        "? Plot/new_figure.pdf\n"
    )
    dictResponses = {
        ("rev-parse", "--is-inside-work-tree"): (0, "true\n", ""),
        (
            "status", "--porcelain=v2", "--branch",
            "--untracked-files=all",
        ): (0, sPorcelain, ""),
        ("rev-parse", "HEAD"): (0, "0" * 40 + "\n", ""),
    }
    with patch(
        "vaibify.gui.gitStatus.subprocess.run",
        side_effect=_fnFakeRun(dictResponses),
    ):
        dictResult = gitStatus.fdictGitStatusForWorkspace(str(tmp_path))
    assert dictResult["dictFileStates"] == {
        "Plot/new_figure.pdf": "untracked",
    }


def test_fdictGitStatusForWorkspace_modified_file(tmp_path):
    sPorcelain = (
        "# branch.head main\n"
        "# branch.ab +0 -0\n"
        "1 .M N... 100644 100644 100644 abc def Plot/figure_1.pdf\n"
    )
    dictResponses = {
        ("rev-parse", "--is-inside-work-tree"): (0, "true\n", ""),
        (
            "status", "--porcelain=v2", "--branch",
            "--untracked-files=all",
        ): (0, sPorcelain, ""),
        ("rev-parse", "HEAD"): (0, "0" * 40 + "\n", ""),
    }
    with patch(
        "vaibify.gui.gitStatus.subprocess.run",
        side_effect=_fnFakeRun(dictResponses),
    ):
        dictResult = gitStatus.fdictGitStatusForWorkspace(str(tmp_path))
    assert dictResult["dictFileStates"]["Plot/figure_1.pdf"] == "dirty"


def test_fdictGitStatusForWorkspace_staged_file(tmp_path):
    sPorcelain = (
        "# branch.head main\n"
        "# branch.ab +0 -0\n"
        "1 M. N... 100644 100644 100644 abc def workflow.json\n"
    )
    dictResponses = {
        ("rev-parse", "--is-inside-work-tree"): (0, "true\n", ""),
        (
            "status", "--porcelain=v2", "--branch",
            "--untracked-files=all",
        ): (0, sPorcelain, ""),
        ("rev-parse", "HEAD"): (0, "0" * 40 + "\n", ""),
    }
    with patch(
        "vaibify.gui.gitStatus.subprocess.run",
        side_effect=_fnFakeRun(dictResponses),
    ):
        dictResult = gitStatus.fdictGitStatusForWorkspace(str(tmp_path))
    assert dictResult["dictFileStates"]["workflow.json"] == "uncommitted"


def test_fdictGitStatusForWorkspace_mixed_states(tmp_path):
    sPorcelain = (
        "# branch.head main\n"
        "# branch.ab +2 -0\n"
        "? Plot/new.pdf\n"
        "1 .M N... 100644 100644 100644 abc def step1/figure.pdf\n"
        "1 M. N... 100644 100644 100644 abc def step2/output.csv\n"
        "! Data/scratch.tmp\n"
    )
    dictResponses = {
        ("rev-parse", "--is-inside-work-tree"): (0, "true\n", ""),
        (
            "status", "--porcelain=v2", "--branch",
            "--untracked-files=all",
        ): (0, sPorcelain, ""),
        ("rev-parse", "HEAD"): (0, "0" * 40 + "\n", ""),
    }
    with patch(
        "vaibify.gui.gitStatus.subprocess.run",
        side_effect=_fnFakeRun(dictResponses),
    ):
        dictResult = gitStatus.fdictGitStatusForWorkspace(str(tmp_path))
    assert dictResult["dictFileStates"] == {
        "Plot/new.pdf": "untracked",
        "step1/figure.pdf": "dirty",
        "step2/output.csv": "uncommitted",
        "Data/scratch.tmp": "ignored",
    }
    assert dictResult["iAhead"] == 2


def test_fdictGitStatusForWorkspace_detached_head_has_empty_branch(tmp_path):
    sPorcelain = (
        "# branch.head (detached)\n"
    )
    dictResponses = {
        ("rev-parse", "--is-inside-work-tree"): (0, "true\n", ""),
        (
            "status", "--porcelain=v2", "--branch",
            "--untracked-files=all",
        ): (0, sPorcelain, ""),
        ("rev-parse", "HEAD"): (0, "0" * 40 + "\n", ""),
    }
    with patch(
        "vaibify.gui.gitStatus.subprocess.run",
        side_effect=_fnFakeRun(dictResponses),
    ):
        dictResult = gitStatus.fdictGitStatusForWorkspace(str(tmp_path))
    assert dictResult["sBranch"] == ""
    assert dictResult["bIsRepo"] is True


def test_fdictGitStatusForWorkspace_status_failure_surfaces_reason(tmp_path):
    dictResponses = {
        ("rev-parse", "--is-inside-work-tree"): (0, "true\n", ""),
        (
            "status", "--porcelain=v2", "--branch",
            "--untracked-files=all",
        ): (128, "", "fatal: bad object HEAD"),
    }
    with patch(
        "vaibify.gui.gitStatus.subprocess.run",
        side_effect=_fnFakeRun(dictResponses),
    ):
        dictResult = gitStatus.fdictGitStatusForWorkspace(str(tmp_path))
    assert dictResult["bIsRepo"] is False
    assert "bad object HEAD" in dictResult["sReason"]


def test_fdictGitStatusForWorkspace_renamed_entry(tmp_path):
    sPorcelain = (
        "# branch.head main\n"
        "# branch.ab +0 -0\n"
        "2 R. N... 100644 100644 100644 abc def R100 "
        "new/name.py\told/name.py\n"
    )
    dictResponses = {
        ("rev-parse", "--is-inside-work-tree"): (0, "true\n", ""),
        (
            "status", "--porcelain=v2", "--branch",
            "--untracked-files=all",
        ): (0, sPorcelain, ""),
        ("rev-parse", "HEAD"): (0, "0" * 40 + "\n", ""),
    }
    with patch(
        "vaibify.gui.gitStatus.subprocess.run",
        side_effect=_fnFakeRun(dictResponses),
    ):
        dictResult = gitStatus.fdictGitStatusForWorkspace(str(tmp_path))
    assert "new/name.py" in dictResult["dictFileStates"]


def test_fdictGitStatusForWorkspace_empty_workspace_root(tmp_path):
    dictResult = gitStatus.fdictGitStatusForWorkspace("")
    assert dictResult["bIsRepo"] is False
