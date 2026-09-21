"""Both wizards turn a repository URL into an entry the same way.

There were two copies. The fix for a wizard-written ``branch: main``
meeting a ``master`` remote landed in the standalone wizard, while the
hub's create wizard -- the one a researcher's project was actually
created by -- kept writing the blind default, so the same defect
shipped twice and was fixed once (2026-09-21). These tests bind the
two callers to one authority and check the shape it produces.
"""

import inspect

import pytest

from vaibify.cli import repositoryPreflight
from vaibify.gui import registryRoutes
from vaibify.install import setupServer


DICT_REMOTE_BRANCHES = {
    "https://github.com/group/hextor": "master",
    "https://github.com/group/fillet": "main",
}


def _fnPatchTheRemotes(monkeypatch, bHasProjectFile=True):
    monkeypatch.setattr(
        repositoryPreflight, "fsDefaultBranchOfRemote",
        lambda sUrl, *aArgs, **kwargs: DICT_REMOTE_BRANCHES.get(sUrl, ""),
    )
    monkeypatch.setattr(
        repositoryPreflight, "fsGithubProjectFileVerdict",
        lambda sRawUrl: (
            repositoryPreflight.S_FILE_PRESENT if bHasProjectFile
            else repositoryPreflight.S_FILE_ABSENT
        ),
    )


@pytest.mark.falsification
def test_both_wizards_write_the_branch_the_remote_actually_has(monkeypatch):
    """Kills: either wizard keeping a private copy that writes ``main``.

    The registry route's copy is the one that shipped the defect; the
    assertion is on BOTH callers, so restoring either copy fails here.
    """
    _fnPatchTheRemotes(monkeypatch)
    listUrls = [
        "https://github.com/group/hextor",
        "https://github.com/group/fillet",
    ]
    listFromHub = registryRoutes._flistRepositoriesFromUrls(listUrls)
    listFromSetup = setupServer._flistReposFromUrls(listUrls)
    assert listFromHub == listFromSetup
    assert [dictEntry["branch"] for dictEntry in listFromHub] == [
        "master", "main",
    ]


def test_neither_wizard_holds_its_own_url_to_entry_logic():
    """A private copy is the divergence itself, so the shape is pinned:
    each wizard's function is a pass-through to the one authority."""
    for fnWizard in (
        registryRoutes._flistRepositoriesFromUrls,
        setupServer._flistReposFromUrls,
    ):
        sSource = inspect.getsource(fnWizard)
        assert "flistRepositoryEntriesFromUrls" in sSource, fnWizard
        assert '"branch": "main"' not in sSource, fnWizard
        assert '"installMethod": "pip_editable"' not in sSource, fnWizard


@pytest.mark.falsification
def test_a_repository_with_no_python_project_file_is_a_reference(monkeypatch):
    """Kills: assuming ``pip_editable`` for every repository, which
    warns at every container start for one pip cannot install."""
    _fnPatchTheRemotes(monkeypatch, bHasProjectFile=False)
    listEntries = registryRoutes._flistRepositoriesFromUrls([
        "https://github.com/group/fillet",
    ])
    assert listEntries[0]["installMethod"] == "reference"


def test_a_remote_that_cannot_be_asked_falls_back_to_main(monkeypatch):
    _fnPatchTheRemotes(monkeypatch)
    listEntries = registryRoutes._flistRepositoriesFromUrls([
        "https://host.example/group/private.git",
    ])
    assert listEntries[0] == {
        "name": "private",
        "url": "https://host.example/group/private.git",
        "branch": "main",
        "installMethod": "pip_editable",
    }


@pytest.mark.parametrize("sUrl, sExpected", [
    ("https://github.com/group/fillet", "fillet"),
    ("https://github.com/group/fillet.git", "fillet"),
    ("git@github.com:group/fillet.git", "fillet"),
    ("https://host.example/group/sub/tool/", "tool"),
])
def test_the_name_is_the_directory_the_clone_lands_in(sUrl, sExpected):
    assert repositoryPreflight.fsRepositoryNameFromUrl(sUrl) == sExpected
