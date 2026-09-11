"""The project-scope checks answer for as much as they honestly can.

Two failures shaped this file, and both are the same shape: a check
that silently declined to run, and a check that reported a partial
look as a clean bill.

Repository discovery keyed on the ENVIRONMENT ENVELOPE, so a perfectly
ordinary project that had never reached Level 3 was treated as having
no repository -- and the workspace-ownership check, which has nothing
to do with envelopes, was skipped with no explanation. The startup
observations were gated on the same discovery even though they read a
marker under the workspace root and never needed a repository at all.

And an ownership walk that stopped at its visit ceiling reported
``ok``: nothing wrong in the part it reached, and nothing whatever
about the rest, rendered as a pass.
"""

from unittest.mock import patch

import pytest

from vaibify.cli import doctorProjectChecks


class _ConnectionStub:
    """Answers the workspace reads discovery and ownership perform."""

    def __init__(self, listEntries=(), setExisting=(), dictOwnership=None):
        self.listEntries = list(listEntries)
        self.setExisting = set(setExisting)
        self.dictOwnership = dictOwnership or {
            "bAnswered": True, "listRootOwned": [], "listOtherOwned": [],
            "bTruncated": False, "bMountInfoReadable": True,
        }

    def flistDirectoryEntries(self, sContainerId, sPath):
        return self.listEntries

    def flistContainerPathsExist(self, sContainerId, listPaths):
        return [sPath in self.setExisting for sPath in listPaths]

    def fdictFindForeignOwnedPaths(self, *args, **kwargs):
        return self.dictOwnership


@pytest.mark.falsification
def test_a_project_without_an_envelope_still_has_its_repository_found():
    """Discovery is about a git repository, not a Level 3 artefact.

    Kills: In doctorProjectChecks.fsDiscoverProjectRepoPath, look for
    `.vaibify/environment.json` instead of `.git`, which makes every
    project below Level 3 invisible to the checks that need a
    repository.
    """
    connectionStub = _ConnectionStub(
        listEntries=["myproject"],
        setExisting={"/workspace/myproject/.git"},
    )
    assert doctorProjectChecks.fsDiscoverProjectRepoPath(
        connectionStub, "probe", "/workspace",
    ) == "/workspace/myproject"


def test_a_workspace_with_no_repository_answers_empty():
    """Discovery never falls back to the workspace volume itself."""
    connectionStub = _ConnectionStub(listEntries=["data"], setExisting=set())
    assert doctorProjectChecks.fsDiscoverProjectRepoPath(
        connectionStub, "probe", "/workspace",
    ) == ""


@pytest.mark.falsification
def test_a_truncated_ownership_walk_is_not_a_pass():
    """The files past the ceiling are where an unseen problem would sit.

    Kills: In doctorProjectChecks._fpreflightOwnershipClean, report a
    truncated walk as ok, which turns "nothing is known about most of
    this repository" into a clean bill.
    """
    connectionStub = _ConnectionStub(dictOwnership={
        "bAnswered": True, "listRootOwned": [], "listOtherOwned": [],
        "bTruncated": True, "bMountInfoReadable": True,
    })
    listResults = doctorProjectChecks.flistCheckWorkspaceOwnership(
        connectionStub, "probe", "/workspace/myproject",
    )
    assert [r.sLevel for r in listResults] == ["not-checked"]
    assert "nothing is known about" in listResults[0].sMessage


def test_a_complete_clean_walk_is_a_pass():
    """The honest pass still exists, so the check is not vacuous."""
    listResults = doctorProjectChecks.flistCheckWorkspaceOwnership(
        _ConnectionStub(), "probe", "/workspace/myproject",
    )
    assert [r.sLevel for r in listResults] == ["ok"]


@pytest.mark.falsification
def test_the_startup_observations_do_not_depend_on_a_repository():
    """They read a marker under the workspace root; a repo is irrelevant.

    Kills: In commandDoctor._flistProjectScopeChecks, move the startup
    observations below the repository-discovery early return, so a
    project with no discoverable repository silently loses them.
    """
    from vaibify.cli import commandDoctor

    class _ConfigStub:
        sProjectName = "probe"
        sWorkspaceRoot = "/workspace"

    with patch.object(
        commandDoctor, "_fconnectionOpenDockerQuietly",
        return_value=_ConnectionStub(listEntries=[], setExisting=set()),
    ), patch(
        "vaibify.docker.containerManager.fdictGetContainerStatus",
        return_value={
            "bExists": True, "bRunning": True, "sStatus": "running",
        },
    ), patch.object(
        doctorProjectChecks, "flistCheckJournalQuarantine",
        return_value=[],
    ), patch.object(
        doctorProjectChecks, "flistReportStartupObservations",
        return_value=["THE-OBSERVATIONS"],
    ):
        listResults = commandDoctor._flistProjectScopeChecks(_ConfigStub())
    assert "THE-OBSERVATIONS" in listResults


def test_a_missing_repository_names_the_checks_it_cost():
    """A check that vanishes reads as a check with nothing wrong in it."""
    from vaibify.cli import commandDoctor

    class _ConfigStub:
        sProjectName = "probe"
        sWorkspaceRoot = "/workspace"

    with patch.object(
        commandDoctor, "_fconnectionOpenDockerQuietly",
        return_value=_ConnectionStub(listEntries=[], setExisting=set()),
    ), patch(
        "vaibify.docker.containerManager.fdictGetContainerStatus",
        return_value={
            "bExists": True, "bRunning": True, "sStatus": "running",
        },
    ), patch.object(
        doctorProjectChecks, "flistCheckJournalQuarantine",
        return_value=[],
    ), patch.object(
        doctorProjectChecks, "flistReportStartupObservations",
        return_value=[],
    ):
        listResults = commandDoctor._flistProjectScopeChecks(_ConfigStub())
    setNames = {r.sName for r in listResults}
    assert setNames == {"envelope-image-currency", "workspace-ownership"}
    assert all(r.sLevel == "not-checked" for r in listResults)
    assert all("no git repository" in r.sMessage for r in listResults)
