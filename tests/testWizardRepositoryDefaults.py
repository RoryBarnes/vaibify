"""The setup wizard writes a branch the remote has and a method that fits.

``branch: main`` written blind met a ``master`` remote, and
``installMethod: pip_editable`` written for every repository met two
with no Python project file; the container started with three
warnings after an hour of building (a live start, 2026-09-21).
"""

from unittest.mock import patch

import pytest

from vaibify.install import setupServer


@pytest.mark.falsification
def test_the_branch_is_the_remotes_default_not_main():
    """Kills: writing ``main`` for every repository again."""
    with patch(
        "vaibify.cli.repositoryPreflight.fsDefaultBranchOfRemote",
        lambda sUrl, *aArgs, **kwargs: "master" if "hextor" in sUrl else "main",
    ), patch.object(
        setupServer, "_fbGithubFileExists", lambda sRawUrl: True,
    ):
        listRepos = setupServer._flistReposFromUrls([
            "https://github.com/group/hextor",
            "https://github.com/group/fillet.git",
        ])
    assert [dictRepo["branch"] for dictRepo in listRepos] == ["master", "main"]
    assert [dictRepo["name"] for dictRepo in listRepos] == ["hextor", "fillet"]


def test_main_is_written_only_when_the_remote_cannot_be_asked():
    with patch(
        "vaibify.cli.repositoryPreflight.fsDefaultBranchOfRemote",
        lambda sUrl, *aArgs, **kwargs: "",
    ), patch.object(setupServer, "_fbGithubFileExists", lambda sRawUrl: None):
        listRepos = setupServer._flistReposFromUrls(["https://host.example/g/r"])
    assert listRepos[0]["branch"] == "main"
    assert listRepos[0]["installMethod"] == "pip_editable"


@pytest.mark.falsification
def test_a_github_repository_with_no_python_project_file_is_a_reference():
    """Kills: writing ``pip_editable`` for every repository again, under
    which a protocol or Julia repository fails pip at every start."""
    listAsked = []

    def fnExists(sRawUrl):
        listAsked.append(sRawUrl)
        return False

    with patch(
        "vaibify.cli.repositoryPreflight.fsDefaultBranchOfRemote",
        lambda sUrl, *aArgs, **kwargs: "main",
    ), patch.object(setupServer, "_fbGithubFileExists", fnExists):
        listRepos = setupServer._flistReposFromUrls([
            "https://github.com/group/protocol",
            "git@github.com:group/tool.git",
        ])
    assert [dictRepo["installMethod"] for dictRepo in listRepos] == [
        "reference", "reference",
    ]
    assert listAsked[0].startswith(
        "https://raw.githubusercontent.com/group/protocol/main/",
    )
    assert listAsked[2].startswith(
        "https://raw.githubusercontent.com/group/tool/main/",
    )


def test_an_unknown_answer_or_a_non_github_host_keeps_pip_editable():
    with patch.object(setupServer, "_fbGithubFileExists", lambda sRawUrl: None):
        assert setupServer._fsInstallMethodForRepository(
            "https://github.com/group/x", "main",
        ) == "pip_editable"
    with patch.object(setupServer, "_fbGithubFileExists", lambda sRawUrl: False):
        assert setupServer._fsInstallMethodForRepository(
            "https://gitlab.example/group/x.git", "main",
        ) == "pip_editable"
