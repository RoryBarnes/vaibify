"""A crashed job's disposable container is reclaimed without a rerun.

The disposable lane destroys its containers in a ``finally``, so the
only survivors are the ones a killed process left. Those were reclaimed
only at the START of the next job for the SAME project — which, for a
project whose researcher never runs another attestation, is never. One
shadow sat on a researcher's daemon for a fortnight that way
(2026-09-21).

The sweep that closes it acts on evidence the daemon supplies, never on
age and never on a name: a survivor stamped with a container id the
daemon no longer holds cannot belong to any live job. That is also what
makes it safe beside a peer hub, whose shadows are stamped with a
container that still exists — and the peer half is asserted as
carefully as the reclaim, because a sweep that destroyed everything
would satisfy the positive half and destroy the property.
"""

import pytest

from vaibify.docker import disposableContainer, disposableSpecification


S_LIVE_CONTAINER_ID = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6"
S_VANISHED_CONTAINER_ID = "ffffffffffffffffffffffffffffffffffffffffffffffff"


class _FakeContainer:
    """A stand-in for one container on the daemon."""

    def __init__(self, sId, sName, dictLabels=None, sStatus="running"):
        self.id = sId
        self.name = sName
        self.labels = dictLabels or {}
        self.status = sStatus


class _FakeContainerCollection:
    """The ``containers`` half of a Docker client stand-in."""

    def __init__(self, listContainers):
        self.listContainers = list(listContainers)
        self.listRemoved = []

    def list(self, all=False, filters=None):  # noqa: A002 — SDK's name
        del all
        sLabel = (filters or {}).get("label")
        if not sLabel:
            return list(self.listContainers)
        return [
            containerFound for containerFound in self.listContainers
            if sLabel in (containerFound.labels or {})
        ]


class _FakeDockerClient:
    """A Docker client stand-in that records what was removed."""

    def __init__(self, listContainers):
        self.containers = _FakeContainerCollection(listContainers)


def _fdockerBuildDaemon(listContainers):
    """Return a client stand-in and patchable destroy recorder."""
    return _FakeDockerClient(listContainers)


@pytest.fixture
def fnRecordDestructions(monkeypatch):
    """Replace the proving destroy with a recorder; return the record."""
    listDestroyed = []

    def fdictDestroy(dockerDisposable, sContainerId):
        del dockerDisposable
        listDestroyed.append(sContainerId)
        return {
            "sOutcome": disposableSpecification.S_OUTCOME_DESTROYED,
            "sReason": "",
        }

    monkeypatch.setattr(
        disposableContainer, "fdictDestroyContainerAndProveAbsence",
        fdictDestroy,
    )
    return listDestroyed


@pytest.mark.falsification
def testASweepDestroysTheStrandedSurvivorAndSparesThePeers(
    fnRecordDestructions,
):
    """The whole sweep, driven end to end against a daemon stand-in.

    Oracle as above. This drives the real
    ``fdictSweepSurvivorsOfVanishedResources`` rather than its
    predicate, so a sweep that stopped consulting the predicate — or
    stopped listing the daemon at all — is caught.

    Kills: dropping the vanished-resource filter from
    ``disposableContainer.fdictSweepSurvivorsOfVanishedResources``.
    """
    containerPeerShadow = _FakeContainer(
        "aaaa" + "0" * 60, "peerShadow", {
            disposableSpecification.S_DISPOSABLE_LABEL: "reservation-peer",
            disposableSpecification.S_DISPOSABLE_RESOURCE_LABEL:
                S_LIVE_CONTAINER_ID,
        },
    )
    containerStranded = _FakeContainer(
        "bbbb" + "0" * 60, "strandedShadow", {
            disposableSpecification.S_DISPOSABLE_LABEL: "reservation-dead",
            disposableSpecification.S_DISPOSABLE_RESOURCE_LABEL:
                S_VANISHED_CONTAINER_ID,
        },
    )
    containerProject = _FakeContainer(S_LIVE_CONTAINER_ID, "peerProject")
    dockerFake = _fdockerBuildDaemon([
        containerProject, containerPeerShadow, containerStranded,
    ])
    dictSwept = disposableContainer.fdictSweepSurvivorsOfVanishedResources(
        dockerFake)
    assert fnRecordDestructions == [containerStranded.id], (
        "only the survivor whose stamped container is gone may be "
        "destroyed; a live peer's shadow must be untouched"
    )
    assert [dictRow["sContainerName"] for dictRow in
            dictSwept["listSettled"]] == ["strandedShadow"]
    assert dictSwept["iQuarantined"] == 0


@pytest.mark.falsification
def testAJobTokenStampIsNeverAdjudicatedByTheDaemon():
    """A stamp that is not a container id is left alone, always.

    The published-reproduction lane stamps a job token
    (``reproduction-<token>``), which names nothing the daemon can be
    asked about. The oracle is the module's own rule: destroy on
    evidence or not at all. Treating an unrecognisable stamp as "gone"
    would destroy every reproduction shadow the moment any hub swept.

    Kills: widening ``_fbStampNamesAContainerId`` to accept any
    non-empty stamp.
    """
    assert disposableContainer._fbStampNamesAContainerId(
        "reproduction-6f3a1c") is False
    assert disposableContainer._fbStampNamesAContainerId("") is False
    assert disposableContainer._fbStampNamesAContainerId("abc") is False
    assert disposableContainer._fbStampNamesAContainerId(
        "ABCDEF0123456789") is False, "an id the daemon writes is lowercase"
    assert disposableContainer._fbStampNamesAContainerId(
        S_LIVE_CONTAINER_ID) is True


def testAnUnstampedSurvivorIsLeftToTheNarrowedSweep():
    """This sweep answers about stamps; unstamped is not its question."""
    containerUnstamped = _FakeContainer(
        "cccc" + "0" * 60, "unstamped", {
            disposableSpecification.S_DISPOSABLE_LABEL: "reservation-x",
        },
    )
    dockerFake = _fdockerBuildDaemon([containerUnstamped])
    assert disposableContainer.fdictSweepSurvivorsOfVanishedResources(
        dockerFake)["listSettled"] == []


def testTheHubRegistersTheReclaimLoopOnItsLifespan():
    """A reclaim nobody schedules is a reclaim that never runs."""
    from vaibify.gui import serverLifespan
    listStarts = []

    class _FakeState:
        pass

    appFake = type("AppFake", (), {})()
    appFake.state = _FakeState()
    appFake.state.listLifespanStartup = []
    appFake.state.listLifespanShutdown = []
    serverLifespan._fnRegisterDisposableReclaim(appFake, {})
    listStarts = appFake.state.listLifespanStartup
    assert listStarts, "the reclaim loop must be registered at startup"
    assert appFake.state.listLifespanShutdown, (
        "a background loop with no shutdown hook outlives its hub"
    )


def testAnUnreachableDaemonReclaimsNothingAndRaisesNothing(monkeypatch):
    """Hygiene must never take a hub down with it."""
    def fnRaise(*args, **kwargs):
        raise RuntimeError("no daemon here")

    monkeypatch.setattr(
        disposableContainer, "fdockerCreateDisposableClient", fnRaise)
    assert disposableContainer.fdictReclaimStrandedDisposables() == {
        "listSettled": [], "iQuarantined": 0,
    }
