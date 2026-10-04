"""A repository branch the remote lacks is refused before the build.

A wizard-written ``branch: main`` met a remote whose default is
``master``, and the container started without that repository after
the whole build (a live start, 2026-09-21). The remote is asked through
``git ls-remote`` and names its default branch, so the refusal says
what to write. These tests drive the check with faked remotes; the
live answer was confirmed by hand when the check was written.
"""

import os
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
            S_LS_REMOTE_MASTER_ONLY if "hextor" in listCommand[
                listCommand.index("--") + 1]
            else S_LS_REMOTE_MAIN
        )
        return SimpleNamespace(returncode=0, stdout=sStdout, stderr="")

    assert fdictProbeRepositoryBranch(
        "https://host.example/group/hextor", "main", fnRun,
    ) == {"bBranchExists": False, "sDefaultBranch": "master"}
    listCommand = dictSeen["listCommand"]
    assert listCommand[0] == "git"
    assert listCommand[listCommand.index("ls-remote") + 1] == "--symref"
    assert listCommand[-1] == "refs/heads/main"
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


# ----------------------------------------------------------------------
# What the probe will and will not ask, and how it asks
# ----------------------------------------------------------------------


def _fnRecordingRun(listCalls):
    def fnRun(listCommand, **kwargs):
        listCalls.append((listCommand, kwargs))
        return SimpleNamespace(returncode=0, stdout=S_LS_REMOTE_MAIN, stderr="")
    return fnRun


@pytest.mark.falsification
@pytest.mark.parametrize("sUrl", [
    "--upload-pack=touch /tmp/marker",
    "-oProxyCommand=false",
    "ext::sh -c touch% /tmp/marker",
    "fd::3",
    "foo+bar::address",
    " https://host.example/group/repo",
    "",
])
def test_an_address_that_is_an_option_or_a_command_is_never_asked(sUrl):
    """A project file's URL is data, and these are not addresses.

    A leading ``-`` is parsed as an option; ``<transport>::<address>``
    hands the address to a remote helper, and ``ext::`` runs it as a
    command.

    Kills: handing such a URL to git.
    """
    listCalls = []
    with pytest.raises(RemoteUnreachableError):
        fdictProbeRepositoryBranch(sUrl, "main", _fnRecordingRun(listCalls))
    assert listCalls == []


@pytest.mark.parametrize("sUrl", [
    "https://github.com/owner/repo",
    "git@github.com:owner/repo.git",
    "ssh://git@host.example:2222/owner/repo.git",
    "http://[::1]:8080/owner/repo.git",
])
def test_an_ordinary_address_is_asked(sUrl):
    listCalls = []
    fdictProbeRepositoryBranch(sUrl, "main", _fnRecordingRun(listCalls))
    assert len(listCalls) == 1


@pytest.mark.falsification
def test_the_probe_carries_the_hardening_list():
    """Kills: dropping the hardening list from the probe's command."""
    from vaibify.reproducibility.gitHardening import LIST_GIT_HARDENING_CONFIG
    listCalls = []
    fdictProbeRepositoryBranch(
        "https://host.example/group/repo", "main", _fnRecordingRun(listCalls))
    listCommand = listCalls[0][0]
    iStart = listCommand.index(LIST_GIT_HARDENING_CONFIG[1]) - 1
    assert listCommand[iStart:iStart + len(LIST_GIT_HARDENING_CONFIG)] == (
        LIST_GIT_HARDENING_CONFIG)
    assert listCommand.index("ls-remote") > iStart


@pytest.mark.falsification
def test_the_address_follows_the_option_terminator():
    """Kills: passing the address where git can read it as an option."""
    listCalls = []
    fdictProbeRepositoryBranch(
        "https://host.example/group/repo", "main", _fnRecordingRun(listCalls))
    listCommand = listCalls[0][0]
    iTerminator = listCommand.index("--")
    assert listCommand[iTerminator + 1] == "https://host.example/group/repo"


@pytest.mark.falsification
def test_the_probe_runs_from_an_empty_directory_that_is_not_the_inherited_one(
    monkeypatch, tmp_path,
):
    """A repository in the inherited directory must never be consulted.

    Kills: running ls-remote in the process's working directory.
    """
    monkeypatch.chdir(tmp_path)
    listCalls = []
    fdictProbeRepositoryBranch(
        "https://host.example/group/repo", "main", _fnRecordingRun(listCalls))
    sUsed = listCalls[0][1]["cwd"]
    assert os.path.realpath(sUsed) != os.path.realpath(str(tmp_path))
    assert os.path.realpath(
        listCalls[0][1]["env"]["GIT_CEILING_DIRECTORIES"],
    ) == os.path.dirname(os.path.realpath(sUsed))


@pytest.mark.falsification
def test_the_probe_does_not_inherit_a_repository_or_injected_config(
    monkeypatch,
):
    """Kills: passing the process environment through unchanged."""
    monkeypatch.setenv("GIT_DIR", "/somewhere/.git")
    monkeypatch.setenv("GIT_WORK_TREE", "/somewhere")
    monkeypatch.setenv("GIT_CONFIG_PARAMETERS", "'core.sshcommand=x'")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.sshCommand")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "x")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/the/researchers/own/config")
    listCalls = []
    fdictProbeRepositoryBranch(
        "https://host.example/group/repo", "main", _fnRecordingRun(listCalls))
    dictEnvironment = listCalls[0][1]["env"]
    for sName in ("GIT_DIR", "GIT_WORK_TREE", "GIT_CONFIG_PARAMETERS",
                  "GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_0", "GIT_CONFIG_VALUE_0"):
        assert sName not in dictEnvironment, sName
    assert dictEnvironment["GIT_CONFIG_GLOBAL"] == (
        "/the/researchers/own/config")
    assert dictEnvironment["GIT_TERMINAL_PROMPT"] == "0"
