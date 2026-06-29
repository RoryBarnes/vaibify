"""Unit tests for vaibify.gui.containerOwnership.

The owner-of-record map is the single authority that enforces one
browser session per container. These tests exercise the claim
arbitration (unowned / same-lease / foreign / reapable take-over), the
lease-verified release, the per-container live-connection count, and the
idle reaper, all against real host flocks redirected into a tmp_path.
"""

import os
import time

import pytest

from vaibify.gui import containerOwnership


@pytest.fixture
def tmp_lock_dir(tmp_path, monkeypatch):
    """Redirect ~/.vaibify/locks/ to a per-test tmp_path."""
    import vaibify.config.containerLock as containerLockModule
    monkeypatch.setattr(
        containerLockModule, "_S_LOCK_DIRECTORY", str(tmp_path),
    )
    return tmp_path


def _ftReleaseAll(dictContainerOwners):
    """Free every held flock so a test never leaks a lock file."""
    for sName in list(dictContainerOwners.keys()):
        containerOwnership._fnForceReleaseOwnership(dictContainerOwners, sName)


def test_unowned_claim_grants_a_lease(tmp_lock_dir):
    dictContainerOwners = containerOwnership.fdictCreateOwnerRegistry()
    iStatus, dictBody = containerOwnership.ftdictClaim(
        dictContainerOwners, "demo", None, 8050,
    )
    try:
        assert iStatus == 200
        assert dictBody["bClaimed"] is True
        assert dictBody["sLeaseId"]
        assert dictContainerOwners["demo"].sLeaseId == dictBody["sLeaseId"]
    finally:
        _ftReleaseAll(dictContainerOwners)


def test_same_lease_reclaim_is_idempotent(tmp_lock_dir):
    dictContainerOwners = containerOwnership.fdictCreateOwnerRegistry()
    _iStatus, dictFirst = containerOwnership.ftdictClaim(
        dictContainerOwners, "demo", None, 8050,
    )
    sLeaseId = dictFirst["sLeaseId"]
    try:
        iStatus, dictSecond = containerOwnership.ftdictClaim(
            dictContainerOwners, "demo", sLeaseId, 8050,
        )
        assert iStatus == 200
        assert dictSecond["sLeaseId"] == sLeaseId
        assert dictContainerOwners["demo"].sLeaseId == sLeaseId
    finally:
        _ftReleaseAll(dictContainerOwners)


def test_foreign_claim_returns_409_without_leaking_lease(tmp_lock_dir):
    dictContainerOwners = containerOwnership.fdictCreateOwnerRegistry()
    _iStatus, dictFirst = containerOwnership.ftdictClaim(
        dictContainerOwners, "demo", None, 8050,
    )
    sOwnerLease = dictFirst["sLeaseId"]
    try:
        iStatus, dictBody = containerOwnership.ftdictClaim(
            dictContainerOwners, "demo", "a-different-lease", 8050,
        )
        assert iStatus == 409
        assert dictBody["bClaimed"] is False
        assert "sLeaseId" not in dictBody
        assert sOwnerLease not in dictBody.values()
        assert isinstance(dictBody["sStartedIso"], str)
    finally:
        _ftReleaseAll(dictContainerOwners)


def test_no_lease_claim_on_owned_returns_409(tmp_lock_dir):
    dictContainerOwners = containerOwnership.fdictCreateOwnerRegistry()
    containerOwnership.ftdictClaim(dictContainerOwners, "demo", None, 8050)
    try:
        iStatus, dictBody = containerOwnership.ftdictClaim(
            dictContainerOwners, "demo", None, 8050,
        )
        assert iStatus == 409
        assert dictBody["bClaimed"] is False
    finally:
        _ftReleaseAll(dictContainerOwners)


def test_claim_takes_over_reapable_idle_owner(tmp_lock_dir):
    dictContainerOwners = containerOwnership.fdictCreateOwnerRegistry()
    _iStatus, dictFirst = containerOwnership.ftdictClaim(
        dictContainerOwners, "demo", None, 8050,
    )
    sOldLease = dictFirst["sLeaseId"]
    try:
        iStatus, dictBody = containerOwnership.ftdictClaim(
            dictContainerOwners, "demo", None, 8050, fGraceSeconds=0.0,
        )
        assert iStatus == 200
        assert dictBody["sLeaseId"] != sOldLease
        assert dictContainerOwners["demo"].sLeaseId == dictBody["sLeaseId"]
    finally:
        _ftReleaseAll(dictContainerOwners)


def test_claim_does_not_take_over_owner_with_live_connection(tmp_lock_dir):
    dictContainerOwners = containerOwnership.fdictCreateOwnerRegistry()
    containerOwnership.ftdictClaim(dictContainerOwners, "demo", None, 8050)
    containerOwnership.fnIncrementLiveConnection(dictContainerOwners, "demo")
    try:
        iStatus, _dictBody = containerOwnership.ftdictClaim(
            dictContainerOwners, "demo", None, 8050, fGraceSeconds=0.0,
        )
        assert iStatus == 409
    finally:
        _ftReleaseAll(dictContainerOwners)


def test_release_verifies_lease_frees_flock_and_drops_record(tmp_lock_dir):
    dictContainerOwners = containerOwnership.fdictCreateOwnerRegistry()
    _iStatus, dictFirst = containerOwnership.ftdictClaim(
        dictContainerOwners, "demo", None, 8050,
    )
    sLeaseId = dictFirst["sLeaseId"]
    bReleased = containerOwnership.fnReleaseOwnership(
        dictContainerOwners, "demo", sLeaseId,
    )
    assert bReleased is True
    assert "demo" not in dictContainerOwners
    iStatus, _dictBody = containerOwnership.ftdictClaim(
        dictContainerOwners, "demo", None, 8051,
    )
    try:
        assert iStatus == 200
    finally:
        _ftReleaseAll(dictContainerOwners)


def test_release_by_non_owner_is_rejected(tmp_lock_dir):
    dictContainerOwners = containerOwnership.fdictCreateOwnerRegistry()
    containerOwnership.ftdictClaim(dictContainerOwners, "demo", None, 8050)
    try:
        bReleased = containerOwnership.fnReleaseOwnership(
            dictContainerOwners, "demo", "not-the-owner-lease",
        )
        assert bReleased is False
        assert "demo" in dictContainerOwners
    finally:
        _ftReleaseAll(dictContainerOwners)


def test_release_unknown_container_is_rejected(tmp_lock_dir):
    dictContainerOwners = containerOwnership.fdictCreateOwnerRegistry()
    bReleased = containerOwnership.fnReleaseOwnership(
        dictContainerOwners, "absent", "any-lease",
    )
    assert bReleased is False


def test_fbSessionOwnsContainer_matches_only_the_owning_lease(tmp_lock_dir):
    dictContainerOwners = containerOwnership.fdictCreateOwnerRegistry()
    _iStatus, dictFirst = containerOwnership.ftdictClaim(
        dictContainerOwners, "demo", None, 8050,
    )
    sLeaseId = dictFirst["sLeaseId"]
    try:
        assert containerOwnership.fbSessionOwnsContainer(
            dictContainerOwners, "demo", sLeaseId,
        )
        assert not containerOwnership.fbSessionOwnsContainer(
            dictContainerOwners, "demo", "wrong",
        )
        assert not containerOwnership.fbSessionOwnsContainer(
            dictContainerOwners, "demo", None,
        )
        assert not containerOwnership.fbSessionOwnsContainer(
            dictContainerOwners, "absent", sLeaseId,
        )
    finally:
        _ftReleaseAll(dictContainerOwners)


def test_live_connection_count_increments_and_decrements(tmp_lock_dir):
    dictContainerOwners = containerOwnership.fdictCreateOwnerRegistry()
    containerOwnership.ftdictClaim(dictContainerOwners, "demo", None, 8050)
    try:
        containerOwnership.fnIncrementLiveConnection(dictContainerOwners, "demo")
        assert dictContainerOwners["demo"].iLiveConnectionCount == 1
        containerOwnership.fnDecrementLiveConnection(dictContainerOwners, "demo")
        assert dictContainerOwners["demo"].iLiveConnectionCount == 0
        containerOwnership.fnDecrementLiveConnection(dictContainerOwners, "demo")
        assert dictContainerOwners["demo"].iLiveConnectionCount == 0
    finally:
        _ftReleaseAll(dictContainerOwners)


def test_fbOwnerIsReapable_honors_live_connection_and_grace(tmp_lock_dir):
    dictContainerOwners = containerOwnership.fdictCreateOwnerRegistry()
    containerOwnership.ftdictClaim(dictContainerOwners, "demo", None, 8050)
    recordOwner = dictContainerOwners["demo"]
    try:
        assert not containerOwnership.fbOwnerIsReapable(recordOwner)
        assert containerOwnership.fbOwnerIsReapable(
            recordOwner, fGraceSeconds=0.0,
        )
        containerOwnership.fnIncrementLiveConnection(dictContainerOwners, "demo")
        assert not containerOwnership.fbOwnerIsReapable(
            recordOwner, fGraceSeconds=0.0,
        )
    finally:
        _ftReleaseAll(dictContainerOwners)


def test_flistReapIdleOwnerships_releases_only_idle_past_grace(tmp_lock_dir):
    dictContainerOwners = containerOwnership.fdictCreateOwnerRegistry()
    containerOwnership.ftdictClaim(dictContainerOwners, "idle", None, 8050)
    containerOwnership.ftdictClaim(dictContainerOwners, "busy", None, 8051)
    containerOwnership.fnIncrementLiveConnection(dictContainerOwners, "busy")
    try:
        listReaped = containerOwnership.flistReapIdleOwnerships(
            dictContainerOwners, fGraceSeconds=0.0,
        )
        assert listReaped == ["idle"]
        assert "idle" not in dictContainerOwners
        assert "busy" in dictContainerOwners
    finally:
        _ftReleaseAll(dictContainerOwners)


def test_flistReapIdleOwnerships_skips_running_pipeline(tmp_lock_dir):
    dictContainerOwners = containerOwnership.fdictCreateOwnerRegistry()
    containerOwnership.ftdictClaim(dictContainerOwners, "demo", None, 8050)
    try:
        listReaped = containerOwnership.flistReapIdleOwnerships(
            dictContainerOwners,
            fbPipelineRunning=lambda sName: True,
            fGraceSeconds=0.0,
        )
        assert listReaped == []
        assert "demo" in dictContainerOwners
    finally:
        _ftReleaseAll(dictContainerOwners)


def test_claim_mints_a_distinct_per_container_agent_token(tmp_lock_dir):
    dictContainerOwners = containerOwnership.fdictCreateOwnerRegistry()
    containerOwnership.ftdictClaim(
        dictContainerOwners, "alpha", None, 8050, sContainerId="cid-alpha",
    )
    containerOwnership.ftdictClaim(
        dictContainerOwners, "beta", None, 8050, sContainerId="cid-beta",
    )
    try:
        sAlpha = dictContainerOwners["alpha"].sAgentToken
        sBeta = dictContainerOwners["beta"].sAgentToken
        assert sAlpha and sBeta and sAlpha != sBeta
        assert dictContainerOwners["alpha"].sContainerId == "cid-alpha"
    finally:
        _ftReleaseAll(dictContainerOwners)


def test_fbAgentTokenAuthorizesContainerId_is_per_container(tmp_lock_dir):
    dictContainerOwners = containerOwnership.fdictCreateOwnerRegistry()
    containerOwnership.ftdictClaim(
        dictContainerOwners, "alpha", None, 8050, sContainerId="cid-alpha",
    )
    containerOwnership.ftdictClaim(
        dictContainerOwners, "beta", None, 8050, sContainerId="cid-beta",
    )
    try:
        sAlphaToken = dictContainerOwners["alpha"].sAgentToken
        # alpha's token authorizes alpha's container...
        assert containerOwnership.fbAgentTokenAuthorizesContainerId(
            dictContainerOwners, sAlphaToken, "cid-alpha",
        ) is True
        # ...but never beta's container, and never an empty id.
        assert containerOwnership.fbAgentTokenAuthorizesContainerId(
            dictContainerOwners, sAlphaToken, "cid-beta",
        ) is False
        assert containerOwnership.fbAgentTokenAuthorizesContainerId(
            dictContainerOwners, sAlphaToken, "",
        ) is False
        assert containerOwnership.fbAgentTokenAuthorizesContainerId(
            dictContainerOwners, "", "cid-alpha",
        ) is False
    finally:
        _ftReleaseAll(dictContainerOwners)


def test_agent_token_with_empty_container_id_fails_closed(tmp_lock_dir):
    """A valid token presented for an empty container id must not match.

    A container claimed without an sContainerId carries the dataclass
    default ''. Presenting that owner's valid agent token while naming no
    container ('' == '') would authorize a request that names no
    container, so the early ``not sPresentedToken or not sContainerId``
    guard must fail closed on the empty id alone.
    """
    dictContainerOwners = containerOwnership.fdictCreateOwnerRegistry()
    containerOwnership.ftdictClaim(dictContainerOwners, "demo", None, 8050)
    try:
        sToken = dictContainerOwners["demo"].sAgentToken
        assert dictContainerOwners["demo"].sContainerId == ""
        assert sToken
        assert containerOwnership.fbAgentTokenAuthorizesContainerId(
            dictContainerOwners, sToken, "",
        ) is False
    finally:
        _ftReleaseAll(dictContainerOwners)


def test_same_lease_reclaim_refreshes_grace_clock(tmp_lock_dir):
    """A same-lease re-claim must advance fLastSeenMonotonic to now.

    A reloaded tab re-asserts its lease over HTTP before the WebSocket
    reconnects. If the idempotent reclaim does not refresh the grace
    clock, a concurrent idle reaper can release the just-reclaimed
    session between reload and reconnect.
    """
    dictContainerOwners = containerOwnership.fdictCreateOwnerRegistry()
    _iStatus, dictFirst = containerOwnership.ftdictClaim(
        dictContainerOwners, "demo", None, 8050,
    )
    sLeaseId = dictFirst["sLeaseId"]
    try:
        recordOwner = dictContainerOwners["demo"]
        recordOwner.fLastSeenMonotonic = time.monotonic() - 1000.0
        containerOwnership.ftdictClaim(
            dictContainerOwners, "demo", sLeaseId, 8050,
        )
        fElapsedSinceReclaim = (
            time.monotonic() - dictContainerOwners["demo"].fLastSeenMonotonic
        )
        assert fElapsedSinceReclaim < 1.0
    finally:
        _ftReleaseAll(dictContainerOwners)


def test_release_stops_keep_alive(tmp_lock_dir, monkeypatch):
    """Releasing an owner must stop its container keep-alive once.

    Without the fnStopKeepAlive call every release and reap leaks the
    container's keep-alive threads/timers, keeping idle containers warm
    indefinitely and desyncing container-health from session state.
    """
    listStoppedNames = []
    monkeypatch.setattr(
        containerOwnership, "fnStopKeepAlive", listStoppedNames.append,
    )
    dictContainerOwners = containerOwnership.fdictCreateOwnerRegistry()
    _iStatus, dictFirst = containerOwnership.ftdictClaim(
        dictContainerOwners, "demo", None, 8050,
    )
    sLeaseId = dictFirst["sLeaseId"]
    try:
        bReleased = containerOwnership.fnReleaseOwnership(
            dictContainerOwners, "demo", sLeaseId,
        )
        assert bReleased is True
        assert listStoppedNames == ["demo"]
    finally:
        _ftReleaseAll(dictContainerOwners)


def test_fbOwnerIsReapable_is_true_at_exact_grace_boundary(monkeypatch):
    """At elapsed == grace the record is reapable (>= boundary)."""
    recordOwner = containerOwnership.OwnerRecord(
        sLeaseId="x", fileHandleLock=None, fLastSeenMonotonic=100.0,
    )
    monkeypatch.setattr(containerOwnership.time, "monotonic", lambda: 105.0)
    assert containerOwnership.fbOwnerIsReapable(
        recordOwner, fGraceSeconds=5.0,
    ) is True
