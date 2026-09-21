"""Every container a test lane creates must be reclaimable later.

A live lane tears its containers down in a fixture. A lane that is
KILLED — a CI cancel, a ``^C``, a harness timeout — never reaches that
teardown, so the container outlives the run. Ten of them accumulated on
one researcher's daemon (2026-09-21), with names like ``host-only``,
``acceptance`` and ``fresh-image`` that read like research environments
rather than like litter, so nobody dared delete them.

A sweep can only act on evidence, and the only evidence that
distinguishes a suite's container from a researcher's is a label the
suite wrote. This test is what keeps that evidence universal: a new
lane that creates a container without the label creates one nothing
can ever reclaim, and it fails here rather than on somebody's daemon
three weeks later.
"""

import pathlib
import re

import pytest

from tests.liveContainerLabels import S_LIVE_LANE_LABEL


S_TESTS_DIRECTORY = pathlib.Path("tests")

# A creation the label cannot ride: these construct an argv to ASSERT
# on it, or drive a stand-in, and never reach a daemon. Each entry is a
# claim that the file creates no real container; adding one is a claim
# somebody must be able to check by reading the file.
SET_FILES_THAT_CREATE_NO_CONTAINER = frozenset({
    "testContainerManagerFull.py",
    "testCoverageBroadGaps.py",
    "testDaemonDiskPreflight.py",
    "testDockerConnection.py",
    "testContainerManager.py",
    "testStartLaunchCommands.py",
    "testConvertToContainerRoute.py",
    "testDisposableContainer.py",
    "liveContainerLabels.py",
    "testLiveLanesLabelWhatTheyCreate.py",
})

_REGEX_SDK_CREATION = re.compile(r"containers\.(?:run|create)\(")
_REGEX_CLI_CREATION = re.compile(r'"docker",\s*"run"')


def _flistFindCreatingFiles():
    """Return every tests/ file that creates a container on a daemon."""
    listCreating = []
    for pathFile in sorted(S_TESTS_DIRECTORY.glob("*.py")):
        if pathFile.name in SET_FILES_THAT_CREATE_NO_CONTAINER:
            continue
        sText = pathFile.read_text(encoding="utf-8")
        if _REGEX_SDK_CREATION.search(sText) or (
            _REGEX_CLI_CREATION.search(sText)
        ):
            listCreating.append(pathFile)
    return listCreating


def testThereAreLiveLanesToGovern():
    """A guard that governs nothing has stopped being a guard."""
    assert _flistFindCreatingFiles(), (
        "no test file appears to create a container; the creation "
        "patterns this test scans for have probably changed"
    )


@pytest.mark.parametrize(
    "pathFile", _flistFindCreatingFiles(), ids=lambda p: p.name,
)
def testALaneThatCreatesAContainerLabelsIt(pathFile):
    """Each creating lane must reach the shared labelling helper.

    The sweep destroys what carries the label and nothing else — never
    a name pattern, never an age — because a researcher's container
    must be untouchable. That contract only reclaims a lane's
    containers if the lane wrote the label, so "every creating lane
    labels" is not a style rule, it is the precondition the sweep's
    safety rests on.
    """
    sText = pathFile.read_text(encoding="utf-8")
    assert "liveContainerLabels" in sText, (
        f"{pathFile.name} creates a container but never imports the "
        "shared label; a killed run would strand it with nothing able "
        "to tell it from a researcher's own container"
    )
    iCreations = len(_REGEX_SDK_CREATION.findall(sText)) + len(
        _REGEX_CLI_CREATION.findall(sText))
    iLabellings = sText.count("flistLabelArguments()") + sText.count(
        "labels=fdictLabels(")
    assert iLabellings >= iCreations, (
        f"{pathFile.name} makes {iCreations} container creation(s) but "
        f"labels {iLabellings}; every one must be reclaimable"
    )


def testTheSweepActsOnTheLabelAndNothingElse():
    """Never a name, never an age — the label is the only evidence."""
    sText = (S_TESTS_DIRECTORY / "liveContainerLabels.py").read_text(
        encoding="utf-8")
    assert 'filters={"label": S_LIVE_LANE_LABEL}' in sText, (
        "the sweep must select on the live-lane label"
    )
    assert S_LIVE_LANE_LABEL == "vaibify-live-test-lane", (
        "the label names the suite, so a researcher reading docker ps "
        "can see at a glance what wrote it"
    )
    for sForbidden in ("name=", "since=", "before=", "status="):
        assert f'filters={{"{sForbidden.rstrip("=")}"' not in sText, (
            f"selecting containers by {sForbidden} would let the sweep "
            "destroy a researcher's own container"
        )


def testTheSessionSweepRunsAtBothEndsOfALiveRun():
    """The start is the only end a killed run will reach again."""
    sConftest = (S_TESTS_DIRECTORY / "conftest.py").read_text(
        encoding="utf-8")
    iStart = sConftest.index("def fnSweepContainersLeftByAKilledLiveLane")
    sBody = sConftest[iStart:iStart + 2000]
    iYield = sBody.index("\n    yield\n    flistSweepLiveLaneContainers()")
    assert "flistSweepLiveLaneContainers()" in sBody[:iYield], (
        "sweeping only at the end reclaims nothing from the run that "
        "was killed before its end"
    )


def testAUnitRunNeverTouchesTheDaemon():
    """A selection with no live test must not reach Docker at all."""
    sConftest = (S_TESTS_DIRECTORY / "conftest.py").read_text(
        encoding="utf-8")
    assert "_fbSelectionRunsLiveDockerTests" in sConftest
    assert "docker_live" in sConftest


class _FakeContainer:
    """A stand-in for one container on the daemon."""

    def __init__(self, sName, dictLabels):
        self.name = sName
        self.labels = dictLabels
        self.bRemoved = False

    def remove(self, force=False):
        del force
        self.bRemoved = True


class _FakeContainerCollection:
    """The ``containers`` half of a Docker client stand-in."""

    def __init__(self, listContainers):
        self.listContainers = list(listContainers)

    def list(self, all=False, filters=None):  # noqa: A002 — SDK's name
        del all
        sLabel = (filters or {}).get("label")
        return [
            containerFound for containerFound in self.listContainers
            if not sLabel or sLabel in containerFound.labels
        ]


@pytest.mark.falsification
def testTheSweepTakesTheSuitesContainersAndLeavesTheResearchersAlone(
    monkeypatch,
):
    """Label-scoped, both directions, because only one direction is safe.

    The independent oracle is what the researcher actually lost: not
    disk, but the ability to tell their own containers from the
    suite's. A sweep that took a researcher's ``vaibify-vplanet``
    would be strictly worse than the leak it replaces — this
    repository has already offered one broad ``docker rm`` filter that
    matched a live research container, and it was the researcher who
    caught it.

    Kills: widening the sweep's selection beyond the label — dropping
    the ``filters`` argument, so every container on the daemon is
    listed and removed.
    """
    containerSuite = _FakeContainer(
        "suiteThrowaway", {S_LIVE_LANE_LABEL: "1"})
    containerResearcher = _FakeContainer("vaibify-vplanet", {})

    class _FakeDockerModule:
        @staticmethod
        def from_env():
            return type("ClientFake", (), {
                "containers": _FakeContainerCollection(
                    [containerSuite, containerResearcher]),
            })()

    import sys
    monkeypatch.setitem(sys.modules, "docker", _FakeDockerModule)
    from tests.liveContainerLabels import flistSweepLiveLaneContainers
    listRemoved = flistSweepLiveLaneContainers()
    assert listRemoved == ["suiteThrowaway"]
    assert containerSuite.bRemoved is True
    assert containerResearcher.bRemoved is False, (
        "a container the suite did not label is the researcher's, and "
        "the sweep must never touch it"
    )
