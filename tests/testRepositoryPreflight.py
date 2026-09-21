"""A repository branch the remote lacks is refused before the build.

A wizard-written ``branch: main`` met a remote whose default is
``master``, and the container started without that repository after
the whole build (a live start, 2026-09-21). The remote is asked through
``git ls-remote`` and names its default branch, so the refusal says
what to write. These tests drive the check with faked remotes; the
live answer was confirmed by hand when the check was written.
"""

import subprocess
from types import SimpleNamespace

import pytest

from vaibify.cli.preflightResult import S_LEVEL_FAIL, S_LEVEL_NOT_CHECKED
from vaibify.cli.repositoryPreflight import (
    RemoteUnreachableError,
    S_PREFLIGHT_NAME,
    fdictProbeRepositoryBranch,
    flistMissingBranches,
    fpreflightRepositoryBranches,
    fsDefaultBranchOfRemote,
)


S_LS_REMOTE_MASTER_ONLY = (
    "ref: refs/heads/master\tHEAD\n"
    "0123456789abcdef0123456789abcdef01234567\tHEAD\n"
)
S_LS_REMOTE_MAIN = (
    "ref: refs/heads/main\tHEAD\n"
    "0123456789abcdef0123456789abcdef01234567\tHEAD\n"
    "0123456789abcdef0123456789abcdef01234567\trefs/heads/main\n"
)


def _fnProbeFake(dictRemotes):
    def fnProbe(sUrl, sBranch):
        if sUrl not in dictRemotes:
            raise RemoteUnreachableError(f"{sUrl}: could not read")
        sDefaultBranch, setBranches = dictRemotes[sUrl]
        return {
            "bBranchExists": sBranch in setBranches,
            "sDefaultBranch": sDefaultBranch,
        }
    return fnProbe


def _fconfigWith(listRepositories):
    return SimpleNamespace(listRepositories=listRepositories)


DICT_REMOTES = {
    "https://host.example/group/hextor": ("master", {"master"}),
    "https://host.example/group/fillet": ("main", {"main", "develop"}),
}


@pytest.mark.falsification
def test_a_branch_the_remote_lacks_is_refused_and_the_default_is_named():
    """Kills: the missing-branch walk returning nothing, under which
    every branch passes and the container starts without the repository."""
    preflightBranches = fpreflightRepositoryBranches(_fconfigWith([
        {"name": "hextor", "url": "https://host.example/group/hextor",
         "branch": "main"},
        {"name": "fillet", "url": "https://host.example/group/fillet",
         "branch": "main"},
    ]), _fnProbeFake(DICT_REMOTES))
    assert preflightBranches.sLevel == S_LEVEL_FAIL
    assert preflightBranches.sName == S_PREFLIGHT_NAME
    assert "'hextor' names branch 'main'" in preflightBranches.sMessage
    assert "default branch is 'master'" in preflightBranches.sMessage
    assert "fillet" not in preflightBranches.sMessage
    assert "vaibify.yml" in preflightBranches.sRemediation


def test_branches_the_remotes_have_are_silent():
    assert fpreflightRepositoryBranches(_fconfigWith([
        {"name": "fillet", "url": "https://host.example/group/fillet",
         "branch": "develop"},
    ]), _fnProbeFake(DICT_REMOTES)) is None
    assert fpreflightRepositoryBranches(_fconfigWith([]), _fnProbeFake({})) is None


@pytest.mark.falsification
def test_a_remote_that_cannot_be_asked_never_refuses():
    """A private remote or no network is not evidence about a branch;
    the entrypoint categorises those failures itself.

    Kills: reading an unreachable remote as "the branch is missing".
    """
    preflightBranches = fpreflightRepositoryBranches(_fconfigWith([
        {"name": "private", "url": "https://host.example/group/private",
         "branch": "main"},
    ]), _fnProbeFake(DICT_REMOTES))
    assert preflightBranches.sLevel == S_LEVEL_NOT_CHECKED
    assert "could not read" in preflightBranches.sMessage


def test_entries_without_a_url_or_branch_are_skipped():
    assert flistMissingBranches(
        [{"name": "x"}, {"url": "https://host.example/group/hextor"}],
        _fnProbeFake(DICT_REMOTES),
    ) == []


def test_ls_remote_output_is_read_for_the_default_and_the_branch():
    dictSeen = {}

    def fnRun(listCommand, **kwargs):
        dictSeen["listCommand"] = listCommand
        dictSeen["sPrompt"] = kwargs["env"].get("GIT_TERMINAL_PROMPT")
        sStdout = (
            S_LS_REMOTE_MASTER_ONLY if "hextor" in listCommand[3]
            else S_LS_REMOTE_MAIN
        )
        return SimpleNamespace(returncode=0, stdout=sStdout, stderr="")

    assert fdictProbeRepositoryBranch(
        "https://host.example/group/hextor", "main", fnRun,
    ) == {"bBranchExists": False, "sDefaultBranch": "master"}
    assert dictSeen["listCommand"][:3] == ["git", "ls-remote", "--symref"]
    assert dictSeen["listCommand"][-1] == "refs/heads/main"
    assert dictSeen["sPrompt"] == "0", "a private remote must fail, not prompt"
    assert fdictProbeRepositoryBranch(
        "https://host.example/group/fillet", "main", fnRun,
    ) == {"bBranchExists": True, "sDefaultBranch": "main"}


def test_a_failed_or_missing_git_is_no_answer():
    def fnFail(listCommand, **kwargs):
        return SimpleNamespace(returncode=128, stdout="", stderr="fatal: could not read")

    with pytest.raises(RemoteUnreachableError):
        fdictProbeRepositoryBranch("https://host.example/x", "main", fnFail)

    def fnMissing(listCommand, **kwargs):
        raise FileNotFoundError("git")

    with pytest.raises(RemoteUnreachableError):
        fdictProbeRepositoryBranch("https://host.example/x", "main", fnMissing)

    def fnSlow(listCommand, **kwargs):
        raise subprocess.TimeoutExpired(listCommand, 20)

    with pytest.raises(RemoteUnreachableError):
        fdictProbeRepositoryBranch("https://host.example/x", "main", fnSlow)


def test_the_default_branch_reads_empty_when_the_remote_cannot_be_asked():
    assert fsDefaultBranchOfRemote(
        "https://host.example/group/hextor", _fnProbeFake(DICT_REMOTES),
    ) == "master"
    assert fsDefaultBranchOfRemote(
        "https://host.example/group/private", _fnProbeFake(DICT_REMOTES),
    ) == ""
