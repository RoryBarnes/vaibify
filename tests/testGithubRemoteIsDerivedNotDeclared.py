"""GitHub verification uses the repo's own remote, and says so honestly.

A researcher pushed a project to GitHub successfully, then asked
vaibify to verify the published copies and was told "Remote not
configured: configure github in vaibify.yml". Three things were wrong
at once:

1. Nothing was misconfigured. PUSH uses the project repo's git remote,
   which was present and correct. Only VERIFY demanded a second,
   hand-written declaration in ``dictRemotes.github``.
2. That declaration is written by exactly one thing -- the legacy-key
   migration from ``sGithubBaseUrl`` -- a field new projects never
   get. So every project created after that field was retired could
   push to GitHub and then be told GitHub was not configured.
3. The remedy named a file with no such setting. ``vaibify.yml`` has
   no remote section at all, so following the message could not
   possibly work, and the researcher should not be hand-editing
   configuration to clear a dashboard error regardless.

The owner and repository are derived from ``.git/config`` at each use
rather than persisted, because a git remote can be changed and a stale
stored copy would verify against the wrong repository -- reporting a
match or a divergence that means nothing.

Kills (confirmed): returning ``{}`` from ``_fdictDeriveGithubConfig``
-> the derivation test fails with the refusal raised instead.
"""

import pytest

from vaibify.reproducibility.scheduledReverify import (
    ReverifyConfigError,
    _fdictRequireServiceConfig,
    _fsReadOriginRemoteUrl,
)


S_REMOTE_URL = "https://github.com/RoryBarnes/aigreenhouse.git"

S_GIT_CONFIG = """[core]
\trepositoryformatversion = 0
\tbare = false
[user]
\tname = A Researcher
[remote "origin"]
\turl = """ + S_REMOTE_URL + """
\tfetch = +refs/heads/*:refs/remotes/origin/*
[branch "main"]
\tremote = origin
"""


class _FakeRepoFiles:
    """A files adapter holding one .git/config, and nothing else."""

    def __init__(self, sConfig=S_GIT_CONFIG, bPresent=True):
        self._sConfig = sConfig
        self._bPresent = bPresent
        self.sRootPath = "/workspace/project"

    def fbIsFile(self, sRelPath):
        return self._bPresent and sRelPath == ".git/config"

    def fsReadText(self, sRelPath):
        if not self.fbIsFile(sRelPath):
            raise OSError(sRelPath)
        return self._sConfig


def test_the_origin_url_is_read_from_git_config():
    assert _fsReadOriginRemoteUrl(_FakeRepoFiles()) == S_REMOTE_URL


def test_a_non_origin_remote_is_not_mistaken_for_origin():
    """An upstream fork's URL must not be verified against.

    Reading "the first url line" would pick up whichever remote came
    first and silently verify the project against somebody else's
    repository -- a divergence report about the wrong thing.
    """
    sConfig = (
        '[remote "upstream"]\n\turl = https://github.com/other/fork.git\n'
        '[remote "origin"]\n\turl = ' + S_REMOTE_URL + "\n"
    )
    assert _fsReadOriginRemoteUrl(
        _FakeRepoFiles(sConfig)) == S_REMOTE_URL


def test_a_repo_with_no_git_config_yields_no_url():
    assert _fsReadOriginRemoteUrl(_FakeRepoFiles(bPresent=False)) == ""


def test_github_config_is_derived_when_the_project_declares_none():
    """The bug, in the shape the researcher hit it.

    dictRemotes is absent entirely -- exactly what a project created
    after sGithubBaseUrl was retired looks like -- and verification
    must still know which repository to compare against.
    """
    dictConfig = _fdictRequireServiceConfig(
        {}, "github", _FakeRepoFiles(),
    )
    assert dictConfig["sOwner"] == "RoryBarnes"
    assert dictConfig["sRepo"] == "aigreenhouse"


def test_a_declared_owner_and_repo_win_over_the_git_remote():
    """An explicit declaration is not overwritten by the derivation."""
    dictWorkflow = {"dictRemotes": {"github": {
        "sOwner": "declaredOwner", "sRepo": "declaredRepo"}}}
    dictConfig = _fdictRequireServiceConfig(
        dictWorkflow, "github", _FakeRepoFiles(),
    )
    assert dictConfig["sOwner"] == "declaredOwner"
    assert dictConfig["sRepo"] == "declaredRepo"


def test_a_recorded_sha_survives_the_derivation():
    """Filling in owner/repo must not discard verify-recorded fields.

    ``sCommittedSha`` is stamped by a previous verification and read
    back as the identity that verification ran against; losing it
    would silently blank the L2 drift check.
    """
    dictWorkflow = {"dictRemotes": {"github": {
        "sCommittedSha": "abc123", "sBranch": "trunk"}}}
    dictConfig = _fdictRequireServiceConfig(
        dictWorkflow, "github", _FakeRepoFiles(),
    )
    assert dictConfig["sCommittedSha"] == "abc123"
    assert dictConfig["sBranch"] == "trunk"
    assert dictConfig["sOwner"] == "RoryBarnes"


def test_a_project_with_no_remote_is_refused_with_a_usable_message():
    """The refusal that legitimately remains must be actionable.

    It must not name vaibify.yml, which has no remote section, and must
    not ask the researcher to hand-edit configuration.
    """
    with pytest.raises(ReverifyConfigError) as excInfo:
        _fdictRequireServiceConfig(
            {}, "github", _FakeRepoFiles(bPresent=False),
        )
    sMessage = str(excInfo.value)
    assert "vaibify.yml" not in sMessage, (
        "the refusal still names a file with no such setting: "
        f"{sMessage}"
    )
    assert "Repos panel" in sMessage, (
        f"the refusal must name where to fix it: {sMessage}"
    )


@pytest.mark.parametrize("sService", ["zenodo", "overleaf", "arxiv"])
def test_no_service_refusal_tells_the_researcher_to_edit_yaml(sService):
    with pytest.raises(ReverifyConfigError) as excInfo:
        _fdictRequireServiceConfig({}, sService, _FakeRepoFiles())
    sMessage = str(excInfo.value)
    assert "vaibify.yml" not in sMessage, sMessage
    assert "panel" in sMessage, (
        f"the {sService} refusal names no place to fix it: {sMessage}"
    )
