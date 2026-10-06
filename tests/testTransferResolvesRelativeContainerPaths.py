"""``vaibify push`` / ``vaibify pull`` accept a path inside the project.

``fsResolveContainerPath`` existed and was called nowhere, so a relative
container path reached ``docker cp`` as typed. ``docker cp NAME:AGENTS.md``
does not read the container's working directory: it fails, and the
completion scripts were offering exactly such names (they stripped the
workspace prefix). The CLI now resolves a container-side path against the
project's own workspace root before using it, which is what makes a
completed candidate usable and what the QuickStart promises ("paths
inside the project can be relative").

Only the CONTAINER side is resolved. The host side of a transfer is the
researcher's own path, as the shell resolved it, and a host project keeps
its own copy-within-the-project path.
"""

import pytest
from click.testing import CliRunner

from vaibify.docker import fileTransfer
from vaibify.docker.fileTransfer import fsResolveContainerPath


class _ConfigStub:
    """What the CLI reads from a resolved project."""

    def __init__(self, sWorkspaceRoot):
        self.sProjectName = "transferProject"
        self.sWorkspaceRoot = sWorkspaceRoot


def _fnInstallProject(monkeypatch, sWorkspaceRoot):
    """Resolve every project to a container project with this root."""
    from vaibify.cli import main as cliMain
    monkeypatch.setattr(
        cliMain, "fconfigResolveProject",
        lambda sName: _ConfigStub(sWorkspaceRoot),
    )


def _flistRecordTransfers(monkeypatch):
    """Replace both transfer functions with recorders; return the log."""
    listCalls = []
    monkeypatch.setattr(
        fileTransfer, "fnPushToContainer",
        lambda *tArguments: listCalls.append(("push", tArguments)),
    )
    monkeypatch.setattr(
        fileTransfer, "fnPullFromContainer",
        lambda *tArguments: listCalls.append(("pull", tArguments)),
    )
    return listCalls


def _fresultRun(sVerb, listArguments):
    from vaibify.cli import main as cliMain
    fnCommand = (
        cliMain.fnPushCommand if sVerb == "push" else cliMain.fnPullCommand
    )
    return CliRunner().invoke(fnCommand, listArguments)


@pytest.mark.falsification
@pytest.mark.parametrize("sRoot, sTyped, sExpected", [
    pytest.param(
        "/workspace", "Step01/incoming.csv",
        "/workspace/Step01/incoming.csv", id="defaultRoot",
    ),
    pytest.param(
        "/srv/work", "Step01/incoming.csv", "/srv/work/Step01/incoming.csv",
        id="customRoot",
    ),
    pytest.param(
        "/srv/work", "incoming.csv", "/srv/work/incoming.csv", id="bareName",
    ),
    pytest.param(
        "/srv/work", "Step01/", "/srv/work/Step01/", id="directory",
    ),
    pytest.param("/srv/work", ".", "/srv/work", id="rootItself"),
    pytest.param(
        "/srv/work", "/elsewhere/in.csv", "/elsewhere/in.csv",
        id="absoluteUnchanged",
    ),
])
def testPushResolvesTheContainerDestinationAgainstTheProjectRoot(
    monkeypatch, sRoot, sTyped, sExpected,
):
    """A relative destination lands at ``<root>/<path>``.

    Kills: passing the destination to the transfer as typed, which makes
    ``docker`` read a bare name as something other than a workspace path.
    """
    _fnInstallProject(monkeypatch, sRoot)
    listCalls = _flistRecordTransfers(monkeypatch)
    resultRun = _fresultRun("push", ["local.csv", sTyped])
    assert resultRun.exit_code == 0, resultRun.output
    assert listCalls == [
        ("push", ("transferProject", "local.csv", sExpected)),
    ]
    assert f"Pushed local.csv -> {sTyped}" in resultRun.output


@pytest.mark.falsification
@pytest.mark.parametrize("sRoot, sTyped, sExpected", [
    pytest.param(
        "/workspace", "Step01/out.csv", "/workspace/Step01/out.csv",
        id="defaultRoot",
    ),
    pytest.param(
        "/srv/work", "Step01/out.csv", "/srv/work/Step01/out.csv",
        id="customRoot",
    ),
    pytest.param("/srv/work", "out.csv", "/srv/work/out.csv", id="bareName"),
    pytest.param(
        "/srv/work", "/elsewhere/out.csv", "/elsewhere/out.csv",
        id="absoluteUnchanged",
    ),
])
def testPullResolvesTheContainerSourceAgainstTheProjectRoot(
    monkeypatch, sRoot, sTyped, sExpected,
):
    """Kills: passing the source to the transfer as typed."""
    _fnInstallProject(monkeypatch, sRoot)
    listCalls = _flistRecordTransfers(monkeypatch)
    resultRun = _fresultRun("pull", [sTyped, "relativeHostFolder/"])
    assert resultRun.exit_code == 0, resultRun.output
    assert listCalls == [
        ("pull", ("transferProject", sExpected, "relativeHostFolder/")),
    ]


def testThePushSourceIsTheResearchersOwnPathAsTyped(monkeypatch):
    """Only the container end is resolved; the host end is never rewritten."""
    _fnInstallProject(monkeypatch, "/srv/work")
    listCalls = _flistRecordTransfers(monkeypatch)
    _fresultRun("push", ["data/local.csv", "Step01/"])
    assert listCalls[0][1][1] == "data/local.csv"


@pytest.mark.falsification
@pytest.mark.parametrize("sTyped, sExpected", [
    pytest.param("Step01/", "/workspace/Step01/", id="directory"),
    pytest.param("a/b/", "/workspace/a/b/", id="nestedDirectory"),
    pytest.param("Step01", "/workspace/Step01", id="noSlashStaysNone"),
    pytest.param("/", "/", id="root"),
])
def testATrailingSlashSurvivesResolution(sTyped, sExpected):
    """Resolving a path changes where it starts and nothing else.

    A trailing slash is how a researcher says "this is a directory", and
    ``PurePosixPath`` drops it silently. A relative destination must mean
    exactly what the same path spelled absolutely means -- including to
    any copy routine that reads the slash -- so resolution may not edit
    what was typed beyond making it absolute.

    Kills: letting ``PurePosixPath`` normalise the slash away.
    """
    assert fsResolveContainerPath(sTyped, "/workspace") == sExpected
