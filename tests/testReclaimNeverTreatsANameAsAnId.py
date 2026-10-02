"""The disposable reclaim adjudicates ids, never names.

The reclaim destroys a survivor whose stamp is a container id the daemon
no longer holds. Its test for "is this stamp an id" accepted any
lowercase-hex string of twelve or more characters, so a project whose
NAME was twelve hex characters had its live disposable work called
stranded and destroyed: the daemon lists ids, never that name, so
nothing ever answered it. The daemon writes ids as 64 lowercase-hex
characters, and that is the whole test now.
"""

import pytest

from vaibify.docker import disposableContainer, disposableSpecification
from vaibify.gui import agentCouncilRunner
from tests.testDisposableSurvivorReclaim import (
    _FakeContainer,
    _fdockerBuildDaemon,
    fnRecordDestructions,  # noqa: F401 -- a fixture
)

S_HEX_PROJECT_NAME = "deadbeefcafe0123"
S_REAL_CONTAINER_ID = "0123456789abcdef" * 4


@pytest.mark.falsification
def testAHexNameIsNotAContainerId():
    """Kills: accepting any twelve-plus hex characters as an id."""
    for sStamp in (
        S_HEX_PROJECT_NAME, "0123456789ab", S_REAL_CONTAINER_ID[:63],
        S_REAL_CONTAINER_ID + "0",
    ):
        assert disposableContainer._fbStampNamesAContainerId(sStamp) is False


def testTheFullIdTheDaemonWritesIsAContainerId():
    assert disposableContainer._fbStampNamesAContainerId(
        S_REAL_CONTAINER_ID) is True


def testALiveProjectNamedInHexKeepsItsDisposableWork(fnRecordDestructions):
    """A live project's shadow is not destroyed because its name looks hex."""
    containerShadow = _FakeContainer(
        "aaaa" + "0" * 60, "liveShadow", {
            disposableSpecification.S_DISPOSABLE_LABEL: "reservation-live",
            disposableSpecification.S_DISPOSABLE_RESOURCE_LABEL:
                S_HEX_PROJECT_NAME,
        },
    )
    dictSwept = disposableContainer.fdictSweepSurvivorsOfVanishedResources(
        _fdockerBuildDaemon([containerShadow]))
    assert fnRecordDestructions == []
    assert dictSwept["listSettled"] == []


def testALiveCouncilRunnerIsNeverASurvivorOfThisSweep(fnRecordDestructions):
    """A council runner carries the council's labels, not the lane's.

    Whatever the project is called, the disposable reclaim does not
    discover it, so it cannot be destroyed by it.
    """
    containerRunner = _FakeContainer(
        "bbbb" + "0" * 60, "councilRunner", {
            agentCouncilRunner.S_COUNCIL_LABEL: "reservation-council",
            agentCouncilRunner.S_COUNCIL_RESOURCE_LABEL: S_REAL_CONTAINER_ID,
        },
    )
    dictSwept = disposableContainer.fdictSweepSurvivorsOfVanishedResources(
        _fdockerBuildDaemon([containerRunner]))
    assert fnRecordDestructions == []
    assert dictSwept["listSettled"] == []
