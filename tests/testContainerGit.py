"""Tests for the container-side git driver used by gitRoutes."""

import json

import pytest

from vaibify.gui import containerGit


class _FakeDocker:
    """Records commands; returns canned (rc, stdout) responses by prefix.

    The map is keyed by a short marker string that appears in the
    command; the first matching entry wins. Unrecognized commands
    return (1, "") so an unmocked invocation fails loudly.
    """

    def __init__(self, listRules=None):
        self.listCommands = []
        self._listRules = listRules or []

    def ftResultExecuteCommand(
        self, sContainerId, sCommand, sWorkdir=None, sUser=None,
    ):
        self.listCommands.append(sCommand)
        for sMarker, iRc, sOut in self._listRules:
            if sMarker in sCommand:
                return (iRc, sOut)
        return (1, "")


# ----------------------------------------------------------------------
# fdictGitStatusInContainer
# ----------------------------------------------------------------------


def test_fdictGitStatusInContainer_detects_non_repo():
    docker = _FakeDocker([
        ("rev-parse --is-inside-work-tree", 128, ""),
    ])
    dictResult = containerGit.fdictGitStatusInContainer(docker, "cid")
    assert dictResult["bIsRepo"] is False


def test_fdictGitStatusInContainer_parses_clean_repo():
    sPorcelain = (
        "# branch.head main\n"
        "# branch.ab +0 -0\n"
    )
    docker = _FakeDocker([
        ("rev-parse --is-inside-work-tree", 0, "true\n"),
        ("status --porcelain=v2", 0, sPorcelain),
        ("rev-parse HEAD", 0, "abc1234567890\n"),
    ])
    dictResult = containerGit.fdictGitStatusInContainer(docker, "cid")
    assert dictResult["bIsRepo"] is True
    assert dictResult["sBranch"] == "main"
    assert dictResult["sHeadSha"].startswith("abc")
    assert dictResult["dictFileStates"] == {}


def test_fdictGitStatusInContainer_injects_hardening_flags():
    docker = _FakeDocker([
        ("rev-parse --is-inside-work-tree", 0, "true\n"),
        ("status --porcelain=v2", 0, "# branch.head main\n"),
        ("rev-parse HEAD", 0, "0" * 40 + "\n"),
    ])
    containerGit.fdictGitStatusInContainer(docker, "cid")
    sStatusCmd = [
        c for c in docker.listCommands if "status --porcelain=v2" in c
    ][0]
    assert "protocol.file.allow=never" in sStatusCmd
    assert "core.symlinks=false" in sStatusCmd


def test_fdictGitStatusInContainer_reports_dirty_file():
    sPorcelain = (
        "# branch.head main\n"
        "# branch.ab +0 -0\n"
        "1 .M N... 100644 100644 100644 a b step1/fig.pdf\n"
    )
    docker = _FakeDocker([
        ("rev-parse --is-inside-work-tree", 0, "true\n"),
        ("status --porcelain=v2", 0, sPorcelain),
        ("rev-parse HEAD", 0, "0" * 40 + "\n"),
    ])
    dictResult = containerGit.fdictGitStatusInContainer(docker, "cid")
    assert dictResult["dictFileStates"]["step1/fig.pdf"] == "dirty"


def test_fdictGitStatusInContainer_cds_to_workspace():
    docker = _FakeDocker([
        ("rev-parse --is-inside-work-tree", 0, "true\n"),
        ("status --porcelain=v2", 0, "# branch.head main\n"),
        ("rev-parse HEAD", 0, "0" * 40 + "\n"),
    ])
    containerGit.fdictGitStatusInContainer(docker, "cid")
    for sCmd in docker.listCommands:
        assert "cd /workspace" in sCmd


# ----------------------------------------------------------------------
# ftResultGitFetchInContainer / ftResultGitPullFastForwardInContainer
# ----------------------------------------------------------------------


def test_ftResultGitFetchInContainer_runs_fetch_with_no_tags():
    docker = _FakeDocker([
        ("fetch --no-tags origin", 0, ""),
    ])
    iExit, _ = containerGit.ftResultGitFetchInContainer(
        docker, "cid", sWorkspace="/workspace/Project",
    )
    assert iExit == 0
    sCmd = docker.listCommands[0]
    assert "cd /workspace/Project" in sCmd
    assert "fetch --no-tags origin" in sCmd
    assert "protocol.file.allow=never" in sCmd


def test_ftResultGitFetchInContainer_propagates_failure():
    docker = _FakeDocker([
        ("fetch --no-tags origin", 1, "fatal: bad remote\n"),
    ])
    iExit, sOut = containerGit.ftResultGitFetchInContainer(
        docker, "cid", sWorkspace="/workspace/Project",
    )
    assert iExit == 1
    assert "fatal" in sOut


def test_ftResultGitPullFastForwardInContainer_uses_ff_only():
    docker = _FakeDocker([
        ("pull --ff-only", 0, "Already up to date.\n"),
    ])
    iExit, _ = containerGit.ftResultGitPullFastForwardInContainer(
        docker, "cid", sWorkspace="/workspace/Project",
    )
    assert iExit == 0
    sCmd = docker.listCommands[0]
    assert "pull --ff-only" in sCmd
    assert "cd /workspace/Project" in sCmd


def test_ftResultGitPullFastForwardInContainer_passes_through_failure():
    docker = _FakeDocker([
        ("pull --ff-only", 128,
         "fatal: Not possible to fast-forward, aborting.\n"),
    ])
    iExit, sOut = containerGit.ftResultGitPullFastForwardInContainer(
        docker, "cid", sWorkspace="/workspace/Project",
    )
    assert iExit == 128
    assert "Not possible to fast-forward" in sOut


# ----------------------------------------------------------------------
# fdictComputeBlobShasInContainer
# ----------------------------------------------------------------------


def test_fdictComputeBlobShasInContainer_empty_list_short_circuits():
    docker = _FakeDocker([])
    dictResult = containerGit.fdictComputeBlobShasInContainer(
        docker, "cid", [],
    )
    assert dictResult == {}
    assert docker.listCommands == []


def test_fdictComputeBlobShasInContainer_parses_json_response():
    dictCanned = {"step1/a.pdf": "a" * 40, "step1/b.pdf": "b" * 40}
    docker = _FakeDocker([
        ("python3", 0, json.dumps(dictCanned) + "\n"),
    ])
    dictResult = containerGit.fdictComputeBlobShasInContainer(
        docker, "cid", ["step1/a.pdf", "step1/b.pdf"],
    )
    assert dictResult == dictCanned


def test_fdictComputeBlobShasInContainer_returns_empty_on_error():
    docker = _FakeDocker([
        ("python3", 1, "error"),
    ])
    dictResult = containerGit.fdictComputeBlobShasInContainer(
        docker, "cid", ["step1/a.pdf"],
    )
    assert dictResult == {}


def test_fdictComputeBlobShasInContainer_returns_empty_on_bad_json():
    docker = _FakeDocker([
        ("python3", 0, "not-json-at-all\n"),
    ])
    dictResult = containerGit.fdictComputeBlobShasInContainer(
        docker, "cid", ["step1/a.pdf"],
    )
    assert dictResult == {}


# ----------------------------------------------------------------------
# flistListContainerFiles
# ----------------------------------------------------------------------


def test_flistListContainerFiles_empty_globs_short_circuits():
    docker = _FakeDocker([])
    listResult = containerGit.flistListContainerFiles(
        docker, "cid", [],
    )
    assert listResult == []
    assert docker.listCommands == []


def test_flistListContainerFiles_returns_globbed_paths():
    docker = _FakeDocker([
        ("python3", 0, json.dumps([
            ".vaibify/workflows/main.json",
            ".vaibify/workflows/alt.json",
        ]) + "\n"),
    ])
    listResult = containerGit.flistListContainerFiles(
        docker, "cid", [".vaibify/workflows/*.json"],
    )
    assert ".vaibify/workflows/main.json" in listResult


# ----------------------------------------------------------------------
# Add / Commit
# ----------------------------------------------------------------------


def test_ftResultGitAddInContainer_hardening_flags():
    docker = _FakeDocker([("git", 0, "")])
    containerGit.ftResultGitAddInContainer(
        docker, "cid", ["a.py", "b.py"],
    )
    sCmd = docker.listCommands[0]
    assert "protocol.file.allow=never" in sCmd
    assert " add -- " in sCmd
    assert "a.py b.py" in sCmd


def test_ftResultGitAddInContainer_empty_list_is_noop():
    docker = _FakeDocker([])
    iExit, sOut = containerGit.ftResultGitAddInContainer(
        docker, "cid", [],
    )
    assert iExit == 0
    assert docker.listCommands == []


def test_ftResultGitCommitInContainer_uses_hardening_flags():
    docker = _FakeDocker([("git", 0, "")])
    containerGit.ftResultGitCommitInContainer(
        docker, "cid", "my commit message",
    )
    sCmd = docker.listCommands[0]
    assert "protocol.file.allow=never" in sCmd
    assert "commit -m" in sCmd
    # shlex.quote wraps strings with spaces in single quotes
    assert "'my commit message'" in sCmd


def test_ftResultGitAddInContainer_quotes_paths_with_spaces():
    docker = _FakeDocker([("git", 0, "")])
    containerGit.ftResultGitAddInContainer(
        docker, "cid", ["my file.py"],
    )
    sCmd = docker.listCommands[0]
    assert "'my file.py'" in sCmd


# ----------------------------------------------------------------------
# fsDetectProjectRepoInContainer
# ----------------------------------------------------------------------


def test_fsDetectProjectRepoInContainer_returns_path_on_success():
    docker = _FakeDocker([
        ("rev-parse --show-toplevel", 0, "/workspace/DemoRepo\n"),
    ])
    sResult = containerGit.fsDetectProjectRepoInContainer(
        docker, "cid",
        "/workspace/DemoRepo/.vaibify/workflows/foo.json",
    )
    assert sResult == "/workspace/DemoRepo"


def test_fsDetectProjectRepoInContainer_returns_empty_on_failure():
    docker = _FakeDocker([
        ("rev-parse --show-toplevel", 128, ""),
    ])
    sResult = containerGit.fsDetectProjectRepoInContainer(
        docker, "cid", "/workspace/workflow.json",
    )
    assert sResult == ""


def test_fsDetectProjectRepoInContainer_cds_to_workflow_dir():
    docker = _FakeDocker([
        ("rev-parse --show-toplevel", 0, "/workspace/DemoRepo\n"),
    ])
    containerGit.fsDetectProjectRepoInContainer(
        docker, "cid",
        "/workspace/DemoRepo/.vaibify/workflows/foo.json",
    )
    sCmd = docker.listCommands[0]
    assert "cd /workspace/DemoRepo/.vaibify/workflows" in sCmd


def test_fsDetectProjectRepoInContainer_empty_path_returns_empty():
    docker = _FakeDocker([])
    sResult = containerGit.fsDetectProjectRepoInContainer(
        docker, "cid", "",
    )
    assert sResult == ""
    assert docker.listCommands == []


def test_fsDetectProjectRepoInContainer_strips_trailing_newline():
    docker = _FakeDocker([
        ("rev-parse --show-toplevel", 0, "/workspace/DemoRepo\n\n"),
    ])
    sResult = containerGit.fsDetectProjectRepoInContainer(
        docker, "cid",
        "/workspace/DemoRepo/.vaibify/workflows/foo.json",
    )
    assert sResult == "/workspace/DemoRepo"
