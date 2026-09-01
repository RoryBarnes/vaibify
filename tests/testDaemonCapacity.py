"""What a machine allows a container to carry, and the trap it avoids.

The bug this module exists to prevent is invisible on Linux, where the
Docker daemon shares the host kernel and "how much memory does this
machine have" and "how much memory can a container have" are the same
number. On macOS they are not: the daemon lives in a virtual machine
with its own, usually much smaller, allocation. A container sized from
HOST memory is over-provisioned there and the kill arrives at run time,
in the middle of a workflow, reported as an unexplained failure.

So every test here drives the two figures APART. A host reading and a
daemon reading that agree would pass whether the code read the right
one or not — which is the same reason the container-ownership bug in
this repository survived a green suite whose fixtures used name == id.
"""

import pytest

from vaibify.docker import daemonCapacity


I_HOST_MEMORY = 64 * 1024 * 1024 * 1024
I_DAEMON_MEMORY = 4 * 1024 * 1024 * 1024


class _FakeConnection:
    """A connection whose daemon reading is deliberately not the host's."""

    def __init__(self, iMemoryBytes=I_DAEMON_MEMORY, bRaises=False):
        self._iMemoryBytes = iMemoryBytes
        self._bRaises = bRaises

    def fdictReadDaemonCapacity(self):
        if self._bRaises:
            raise RuntimeError("the daemon did not answer")
        return {"iMemoryBytes": self._iMemoryBytes, "iCpuCount": 4}


@pytest.fixture
def fnPinHostMemory(monkeypatch):
    """Return a setter that pins the host reading to a chosen value."""
    def fnPin(iMemoryBytes):
        monkeypatch.setattr(
            daemonCapacity, "fiReadHostMemoryBytes",
            lambda: iMemoryBytes)
    return fnPin


def testContainerBoundsFollowTheDaemonAndNotTheHost(fnPinHostMemory):
    """A container's memory must come from the DAEMON's figure.

    Pinned 16x apart so the two cannot be confused: a container sized
    from 64 GB of host RAM against a 4 GB daemon would be killed by the
    kernel the moment a workflow used what it was promised.
    """
    fnPinHostMemory(I_HOST_MEMORY)
    dictCapacity = daemonCapacity.fdictResolveDaemonCapacity(
        _FakeConnection())
    assert dictCapacity["iDaemonMemoryBytes"] == I_DAEMON_MEMORY
    assert dictCapacity["iHostMemoryBytes"] == I_HOST_MEMORY
    assert dictCapacity["iContainerMemoryBytes"] < I_DAEMON_MEMORY, (
        "a container was offered the whole daemon; the project "
        "container and everything else the researcher runs share it"
    )
    assert dictCapacity["iContainerMemoryBytes"] < I_HOST_MEMORY // 4, (
        "the container bound tracks host RAM, which is the macOS trap "
        "this module exists to avoid"
    )


def testArchiveBoundsFollowTheHostAndNotTheDaemon(fnPinHostMemory):
    """An archive is materialised in the HUB's address space.

    The mirror-image error, and it fails the other way: sizing the
    archive read from the daemon's memory would refuse a repository the
    researcher's own machine could hold comfortably.
    """
    fnPinHostMemory(I_HOST_MEMORY)
    dictLarge = daemonCapacity.fdictResolveDaemonCapacity(
        _FakeConnection())
    fnPinHostMemory(2 * 1024 * 1024 * 1024)
    dictSmall = daemonCapacity.fdictResolveDaemonCapacity(
        _FakeConnection())
    assert dictLarge["iArchiveTotalBytes"] > dictSmall["iArchiveTotalBytes"]
    assert dictLarge["iArchiveMemberBytes"] > (
        dictSmall["iArchiveMemberBytes"]
    )


def testScalingOnlyEverRaisesNeverLowers(fnPinHostMemory):
    """A machine smaller than the floor gets the floor, not less.

    The floor is what the design was reviewed against. Shrinking below
    it would make a rerun that worked yesterday refuse today for a
    reason no message could usefully explain.
    """
    fnPinHostMemory(256 * 1024 * 1024)
    dictCapacity = daemonCapacity.fdictResolveDaemonCapacity(
        _FakeConnection(iMemoryBytes=128 * 1024 * 1024))
    assert dictCapacity["iContainerMemoryBytes"] == (
        daemonCapacity.I_FLOOR_CONTAINER_MEMORY_BYTES
    )
    assert dictCapacity["iContainerScratchBytes"] == (
        daemonCapacity.I_FLOOR_CONTAINER_SCRATCH_BYTES
    )
    assert dictCapacity["iArchiveMemberBytes"] == (
        daemonCapacity.I_FLOOR_ARCHIVE_MEMBER_BYTES
    )
    assert dictCapacity["iArchiveTotalBytes"] == (
        daemonCapacity.I_FLOOR_ARCHIVE_TOTAL_BYTES
    )


def testASilentDaemonYieldsTheFloorRatherThanAnException(fnPinHostMemory):
    """Refusing work because ``docker info`` hiccuped is the worse answer.

    Both shapes of silence are covered: no connection to ask, and a
    connection that raises. Each must leave the daemon-scaled bounds at
    their floors — the behaviour that predates any scaling — rather than
    propagating.
    """
    fnPinHostMemory(I_HOST_MEMORY)
    for connectionDocker in (None, _FakeConnection(bRaises=True)):
        dictCapacity = daemonCapacity.fdictResolveDaemonCapacity(
            connectionDocker)
        assert dictCapacity["iDaemonMemoryBytes"] == 0
        assert dictCapacity["iContainerMemoryBytes"] == (
            daemonCapacity.I_FLOOR_CONTAINER_MEMORY_BYTES
        )


def testTheMemberCeilingIsNotRaisedByAnEnormousMachine(fnPinHostMemory):
    """Past the ceiling a single file is a dataset, not a file to copy.

    A judgement rather than a measurement, so it must hold on a machine
    with memory to spare — which is exactly where an unbounded scaling
    would sail past it unnoticed.
    """
    fnPinHostMemory(1024 * 1024 * 1024 * 1024)
    dictCapacity = daemonCapacity.fdictResolveDaemonCapacity(None)
    assert dictCapacity["iArchiveMemberBytes"] == (
        daemonCapacity.I_CEILING_ARCHIVE_MEMBER_BYTES
    )


def testEveryResolvedCapacityComposesValidContainerLimits(fnPinHostMemory):
    """The scratch tmpfs must stay below the memory limit, at any scale.

    tmpfs pages are charged to the container's memory cgroup, so a
    scratch mount at the memory limit silently replaces the disk bound
    with an out-of-memory kill. The limit validator refuses that pairing
    — this asserts the capacity resolver never PRODUCES one, across the
    whole range, which the validator alone cannot tell you.
    """
    from vaibify.docker import disposableSpecification

    for iHostMemory in (0, 512 * 1024 * 1024, I_HOST_MEMORY):
        for iDaemonMemory in (0, 1024 * 1024 * 1024, I_HOST_MEMORY):
            fnPinHostMemory(iHostMemory)
            dictCapacity = daemonCapacity.fdictResolveDaemonCapacity(
                _FakeConnection(iMemoryBytes=iDaemonMemory))
            dictLimits = (
                disposableSpecification.fdictBuildDefaultLimits(
                    dictCapacity))
            assert dictLimits["iScratchBytes"] < (
                dictLimits["iMemoryBytes"]
            ), (
                f"host={iHostMemory} daemon={iDaemonMemory} produced a "
                f"scratch bound that is not below the memory bound: "
                f"{dictLimits}"
            )
            disposableSpecification.fdictComposeCreateSpecification(
                "image@sha256:" + "ab" * 32, "reservation", "shadow",
                dictLimits,
            )
