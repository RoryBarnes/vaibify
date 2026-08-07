"""The container lease is bound to the browser session that claimed it.

Validating "some valid session" plus "some valid lease" independently
would let session B replay session A's copied lease. The owner record
carries the claiming session id, and authorization requires BOTH the
session and the lease to match — the key property behind the A1 identity
boundary (session B + lease A must be rejected).
"""

from vaibify.gui import containerOwnership


def _fdictOwners():
    return containerOwnership.fdictCreateOwnerRegistry()


def _fnRecord(dictOwners, sName, sSessionId):
    """Record an unowned claim bound to a browser session; return its lease."""
    return containerOwnership._fnRecordNewOwner(
        dictOwners, sName, fileHandleLock=None, sContainerId="cid-" + sName,
        sBrowserSessionId=sSessionId,
    )


def test_claim_records_the_browser_session_id():
    dictOwners = _fdictOwners()
    sLease = _fnRecord(dictOwners, "proj", "session-A")
    assert dictOwners["proj"].sBrowserSessionId == "session-A"
    assert dictOwners["proj"].sLeaseId == sLease


def test_owning_session_with_its_lease_is_authorized():
    dictOwners = _fdictOwners()
    sLease = _fnRecord(dictOwners, "proj", "session-A")
    assert containerOwnership.fbBrowserSessionOwnsLease(
        dictOwners, "proj", "session-A", sLease,
    ) is True


def test_session_B_cannot_replay_session_A_lease():
    """The headline property: a copied lease from another session fails."""
    dictOwners = _fdictOwners()
    sLeaseA = _fnRecord(dictOwners, "proj", "session-A")
    # Session B presents session A's (correct) lease but is not the owner.
    assert containerOwnership.fbBrowserSessionOwnsLease(
        dictOwners, "proj", "session-B", sLeaseA,
    ) is False


def test_owning_session_with_a_wrong_lease_is_rejected():
    dictOwners = _fdictOwners()
    _fnRecord(dictOwners, "proj", "session-A")
    assert containerOwnership.fbBrowserSessionOwnsLease(
        dictOwners, "proj", "session-A", "forged-lease",
    ) is False


def test_empty_session_or_lease_fails_closed():
    dictOwners = _fdictOwners()
    sLease = _fnRecord(dictOwners, "proj", "session-A")
    assert containerOwnership.fbBrowserSessionOwnsLease(
        dictOwners, "proj", "", sLease,
    ) is False
    assert containerOwnership.fbBrowserSessionOwnsLease(
        dictOwners, "proj", "session-A", "",
    ) is False


def test_unknown_container_fails_closed():
    dictOwners = _fdictOwners()
    assert containerOwnership.fbBrowserSessionOwnsLease(
        dictOwners, "no-such-project", "session-A", "any-lease",
    ) is False


def test_ftdictClaim_threads_the_session_id_onto_the_record():
    """The public claim path binds the session, not only the private mint."""
    dictOwners = _fdictOwners()
    iStatus, dictPayload = containerOwnership.ftdictClaim(
        dictOwners, "proj", sLeaseId="", iPort=0, sContainerId="cid",
        sBrowserSessionId="session-A",
    )
    assert iStatus == 200
    assert dictOwners["proj"].sBrowserSessionId == "session-A"
    assert containerOwnership.fbBrowserSessionOwnsLease(
        dictOwners, "proj", "session-A", dictPayload["sLeaseId"],
    ) is True
