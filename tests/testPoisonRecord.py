"""Tests for the PoisonRecord axis (design §2.1, 3c).

A host-lane force-abandon poisons an owner record whose guarded worker
is wedged. While the poison stands, exclusivity is retained: claim,
release, reap, and carrier admission all refuse at their existing
choke points, and the registry listing surfaces the state truthfully.
The full lifecycle (case 26b) lands with slice 5; these are the
refusal semantics available now. The setting path — the control
socket's force-abandon handler — is driven for real in
``testHostControlChannel.py``.
"""

from vaibify.gui import commitCarrier
from vaibify.gui.containerOwnership import (
    OwnerRecord,
    PoisonRecord,
    fbOwnerIsReapable,
    fnReleaseOwnership,
    ftdictClaim,
)
from vaibify.gui.registryRoutes import _fnAnnotateOwnershipState

S_PROJECT = "demo"


def _fdictOwnersWithPoisonedRecord():
    """Return an owner registry holding one poisoned record."""
    recordOwner = OwnerRecord(
        sLeaseId="LEASE-A", fileHandleLock=object(), sContainerId="cid-1",
        sBrowserSessionId="session-a",
        poison=PoisonRecord(sGuardedOperationId="op-1"),
    )
    return {S_PROJECT: recordOwner}


def test_a_poisoned_record_refuses_every_claim_including_its_own_lease():
    dictOwners = _fdictOwnersWithPoisonedRecord()
    for sLeaseId, sSessionId in (
        ("LEASE-A", "session-a"),
        ("LEASE-B", "session-b"),
        ("", ""),
    ):
        iCode, dictPayload = ftdictClaim(
            dictOwners, S_PROJECT, sLeaseId, iPort=8000,
            sBrowserSessionId=sSessionId,
        )
        assert iCode == 409
        assert dictPayload["bPoisoned"] is True
        assert "vaibify reconcile" in dictPayload["sMessage"]
    assert dictOwners[S_PROJECT].poison is not None


def test_a_poisoned_record_refuses_release_even_to_its_owner():
    dictOwners = _fdictOwnersWithPoisonedRecord()
    bReleased = fnReleaseOwnership(
        dictOwners, S_PROJECT, "LEASE-A", sBrowserSessionId="session-a",
    )
    assert bReleased is False
    assert S_PROJECT in dictOwners, (
        "the flock-holding record must be retained"
    )


def test_a_poisoned_record_is_never_reapable():
    recordOwner = _fdictOwnersWithPoisonedRecord()[S_PROJECT]
    recordOwner.fLastSeenMonotonic = 0.0
    assert fbOwnerIsReapable(recordOwner, fGraceSeconds=0.0) is False
    recordOwner.poison = None
    assert fbOwnerIsReapable(recordOwner, fGraceSeconds=0.0) is True


def test_a_poisoned_record_refuses_carrier_admission_on_every_lane():
    dictOwners = _fdictOwnersWithPoisonedRecord()
    recordOwner = dictOwners[S_PROJECT]
    appState = type("AppState", (), {"dictContainerOwners": dictOwners})()
    dictBrowserTuple = {
        "sLane": commitCarrier.S_LANE_BROWSER,
        "iOwnerGeneration": recordOwner.iOwnerGeneration,
        "sBrowserSessionId": "session-a",
        "sLeaseId": "LEASE-A",
        "sContainerName": S_PROJECT,
    }
    assert commitCarrier.fbLaneTupleStillCurrent(
        appState, dictBrowserTuple,
    ) is False
    recordOwner.poison = None
    assert commitCarrier.fbLaneTupleStillCurrent(
        appState, dictBrowserTuple,
    ) is True


def test_registry_listing_surfaces_the_poison_truthfully():
    dictOwners = _fdictOwnersWithPoisonedRecord()
    listContainers = [{"sName": S_PROJECT}, {"sName": "healthy"}]
    dictOwners["healthy"] = OwnerRecord(
        sLeaseId="LEASE-H", fileHandleLock=object(),
    )
    _fnAnnotateOwnershipState(listContainers, dictOwners, "LEASE-X")
    assert listContainers[0]["bPoisoned"] is True
    assert listContainers[1]["bPoisoned"] is False
