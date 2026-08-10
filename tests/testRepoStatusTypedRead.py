"""The Repositories poll reads; it no longer assembles a shell script.

The route this covers runs every five seconds for as long as the panel
is open, and until now each tick shelled out once per tracked
repository through a script THIS CODEBASE BUILT, interpolating each
repository's name raw into ``echo "..."`` and ``git -C
/workspace/<name>``. That one shape carried four separate defects, and
the tests below pin the fix for each rather than only the happy path:

* a repository name is user-chosen text, and it reached a shell;
* porcelain output was squeezed through ``tr`` with a pipe as the
  record separator, so a filename containing ``|`` silently corrupted
  the parse into a different answer — not an error, a WRONG one;
* ``echo -n`` is not portable, so the whole batch behaved differently
  on a BSD userland;
* being an exec, it kept the route outside the commit-guard boundary,
  because a route on a timer cannot hold the mutation drain without
  making Run Step refuse at random.

The last one is why this is not a tidy-up. Everything the route now
reaches is a declared typed read, which is what let it leave the
awaiting set and declare a mode with no carrier at all.
"""

import json

import pytest

from vaibify.docker import dockerConnection
from vaibify.gui import routeScope, trackedReposManager


S_HOSTILE_REPO_NAME = "repo; touch /tmp/pwned"


class _RecordingConnection:
    """Answers the typed read, recording exactly what it was asked."""

    def __init__(self, listStatuses=None, errorToRaise=None):
        self.listRequestedPaths = []
        self.listCommands = []
        self._listStatuses = listStatuses or []
        self._errorToRaise = errorToRaise

    def flistReadGitRepoStatuses(self, sContainerId, listRepoPaths):
        self.listRequestedPaths = list(listRepoPaths)
        if self._errorToRaise is not None:
            raise self._errorToRaise
        return self._listStatuses

    def ftResultExecuteCommand(self, sContainerId, sCommand):
        self.listCommands.append(sCommand)
        return (0, "")


def _fdictPresentRecord(sPath, sPorcelain="", sBranch="main\n"):
    """Return one raw record shaped as the read's program emits it."""
    return {
        "sPath": sPath,
        "bMissing": False,
        "sBranch": sBranch,
        "sUrl": "https://example.invalid/x.git\n",
        "sPorcelain": sPorcelain,
    }


@pytest.mark.falsification
def testTheBatchIssuesNoCommandAtAll(tmp_path):
    """A poll that shells out is a poll that cannot be carried.

    The assertion is on the ABSENCE of a command, not on the presence
    of the read: a migration that added the typed read and left the
    old script beside it would satisfy any test that only checked the
    answer.

    Kills: answering the batch by assembling and running a shell
    script again.
    """
    del tmp_path
    connection = _RecordingConnection([
        _fdictPresentRecord("/workspace/alpha"),
    ])
    trackedReposManager.flistBatchComputeRepoStatus(
        connection, "cid", ["alpha"],
    )
    assert connection.listCommands == [], (
        f"the poll ran commands: {connection.listCommands}"
    )
    assert connection.listRequestedPaths == ["/workspace/alpha"]


@pytest.mark.falsification
def testARepositoryNameNeverReachesAShell():
    """The injection surface, gone by construction rather than by escaping.

    A name carrying shell metacharacters is passed through as a plain
    path element, because the only thing that varies in a typed read is
    a Python literal inside a program this repository wrote.

    Kills: reintroducing name interpolation into command text.
    """
    connection = _RecordingConnection([
        _fdictPresentRecord("/workspace/" + S_HOSTILE_REPO_NAME),
    ])
    listStatuses = trackedReposManager.flistBatchComputeRepoStatus(
        connection, "cid", [S_HOSTILE_REPO_NAME],
    )
    assert connection.listCommands == []
    assert connection.listRequestedPaths == [
        "/workspace/" + S_HOSTILE_REPO_NAME
    ]
    assert listStatuses[0]["sName"] == S_HOSTILE_REPO_NAME


@pytest.mark.falsification
def testAFilenameContainingAPipeNoLongerCorruptsTheAnswer():
    """The defect that made a WRONG answer rather than a failed one.

    The old batch joined porcelain lines with ``|`` and split them back
    apart, so a filename containing one produced a phantom extra line.
    It still parsed; it was just untrue.

    The fixture is chosen so the corruption is OBSERVABLE, which took
    some care and is the point of the test. Porcelain reaches the
    caller only as ``bDirty``, a boolean, so an extra phantom line
    usually changes nothing — the repository was dirty either way. Here
    the single changed file is a BUILD ARTEFACT whose name contains a
    pipe: filtered out, the repository is clean; split at the pipe, the
    tail ``notes.txt`` is no longer an artefact, survives the filter,
    and the panel reports a clean repository as dirty.

    Kills: reintroducing the delimiter-joined porcelain transport.
    """
    connection = _RecordingConnection([
        _fdictPresentRecord(
            "/workspace/alpha", sPorcelain=" M build/weird|notes.txt\n",
        ),
    ])
    listStatuses = trackedReposManager.flistBatchComputeRepoStatus(
        connection, "cid", ["alpha"],
    )
    assert listStatuses[0]["sName"] == "alpha"
    assert listStatuses[0]["bDirty"] is False, (
        "a build artefact whose name contains a pipe was split into a "
        "phantom source file, so a clean repository reads as dirty"
    )


@pytest.mark.falsification
def testAnswersAreKeyedByPathNotByPosition():
    """A reordered or short answer must not realign onto another repo.

    Positional matching is the natural way to write this and is
    silently wrong: every repository would inherit its neighbour's
    branch, url and dirtiness, and the panel would look entirely
    plausible.

    Kills: zipping the raw records onto the request order.
    """
    connection = _RecordingConnection([
        _fdictPresentRecord("/workspace/beta", sPorcelain=" M x\n"),
        _fdictPresentRecord("/workspace/alpha", sBranch="trunk\n"),
    ])
    listStatuses = trackedReposManager.flistBatchComputeRepoStatus(
        connection, "cid", ["alpha", "beta"],
    )
    assert [d["sName"] for d in listStatuses] == ["alpha", "beta"]
    assert listStatuses[0]["sBranch"] == "trunk", listStatuses
    assert listStatuses[0]["bDirty"] is False, (
        "alpha inherited beta's dirtiness, so the answers realigned"
    )
    assert listStatuses[1]["bDirty"] is True, listStatuses


@pytest.mark.falsification
def testAFailedReadReportsMissingRatherThanRaising():
    """A poll answers what it can see, and a failed read saw nothing.

    Kills: letting the read's OSError escape, which turns a transient
    container hiccup into a 500 on a route the dashboard calls every
    five seconds.
    """
    connection = _RecordingConnection(
        errorToRaise=OSError("the daemon went away"),
    )
    listStatuses = trackedReposManager.flistBatchComputeRepoStatus(
        connection, "cid", ["alpha", "beta"],
    )
    assert [d["sName"] for d in listStatuses] == ["alpha", "beta"]
    assert all(d["bMissing"] for d in listStatuses), listStatuses


@pytest.mark.falsification
def testTheStatusRouteNoLongerAwaitsACarrierMode():
    """The point of the whole migration, asserted where it is recorded.

    Kills: leaving the route in the awaiting set, where it keeps the
    legacy ambient admission and the migration buys nothing.
    """
    tRoute = ("GET", "/api/repos/{sContainerId}/status")
    assert tRoute not in routeScope.SET_ROUTES_AWAITING_CARRIER_MODE
    assert tRoute in routeScope.SET_CONTAINER_READ_ROUTES, (
        "the route left the container-read allowlist, which is a "
        "frozen record of what resolves to that scope rather than a "
        "migration backlog"
    )


def testTheProgramRunsGitWithTheRepositoryMonitorDisabled():
    """`core.fsmonitor` may name a hook command that `git status` runs.

    Without the override a repository could choose what this poll
    executes, which is a repository deciding what runs on the
    researcher's machine every five seconds.
    """
    sProgram = dockerConnection.fsRenderBatchedTypedReadProgram(
        dockerConnection.S_TYPED_READ_GIT_REPO_STATUS,
        ["/workspace/alpha"],
    )
    assert "core.fsmonitor=false" in sProgram
    assert "timeout=30" in sProgram


def testTheProgramEmbedsPathsAsALiteralAndNothingElse():
    """Only a string may reach the slot, so nothing can escape it."""
    sProgram = dockerConnection.fsRenderBatchedTypedReadProgram(
        dockerConnection.S_TYPED_READ_GIT_REPO_STATUS,
        ["/workspace/" + S_HOSTILE_REPO_NAME],
    )
    assert repr(["/workspace/" + S_HOSTILE_REPO_NAME]) in sProgram
    with pytest.raises(TypeError):
        dockerConnection.fsRenderBatchedTypedReadProgram(
            dockerConnection.S_TYPED_READ_GIT_REPO_STATUS, [object()],
        )
    with pytest.raises(ValueError):
        dockerConnection.fsRenderBatchedTypedReadProgram(
            "notADeclaredOperation", ["/workspace/alpha"],
        )


def testTheProgramItselfAnswersAboutRealRepositories(tmp_path):
    """Run the program. Nothing else in this file executes it.

    A table of program TEXT is exactly the kind of thing that reads
    correctly and does not work, so the shipped source is executed here
    against a real directory: one git repository with an uncommitted
    change, and one directory that is not a repository at all.
    """
    import subprocess
    import sys

    pathRepository = tmp_path / "alpha"
    pathRepository.mkdir()
    subprocess.run(
        ["git", "init", "-q", str(pathRepository)], check=True,
    )
    (pathRepository / "data.txt").write_text("uncommitted")
    pathPlain = tmp_path / "notARepository"
    pathPlain.mkdir()

    sProgram = dockerConnection.fsRenderBatchedTypedReadProgram(
        dockerConnection.S_TYPED_READ_GIT_REPO_STATUS,
        [str(pathRepository), str(pathPlain)],
    )
    processRun = subprocess.run(
        [sys.executable, "-c", sProgram],
        capture_output=True, text=True, timeout=120,
    )
    assert processRun.returncode == 0, processRun.stderr
    listRecords = json.loads(processRun.stdout)
    assert listRecords[0]["sPath"] == str(pathRepository)
    assert listRecords[0]["bMissing"] is False
    assert "data.txt" in listRecords[0]["sPorcelain"]
    assert listRecords[1]["bMissing"] is True
