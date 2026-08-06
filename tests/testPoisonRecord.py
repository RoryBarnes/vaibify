"""Tests for the PoisonRecord axis (design §2.1, 3c).

A host-lane force-abandon poisons an owner record whose guarded worker
is wedged. While the poison stands, exclusivity is retained: claim,
release, reap, and carrier admission all refuse at their existing
choke points, and the registry listing surfaces the state truthfully.
The full lifecycle (case 26b) landed with slice 5 in
``testHostControlChannel.py`` (force-abandon → refuse claim/transfer/
reap → reconcile → transfer); these are the refusal semantics. The setting path — the control
socket's force-abandon handler — is driven for real in
``testHostControlChannel.py``.
"""

from vaibify.gui import commitCarrier
from vaibify.gui.containerOwnership import (
    OwnerRecord,
    PoisonRecord,
    fbOwnerIsReapable,
    fnReleaseOwnership,
    ftClaim,
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
        iCode, dictPayload = ftClaim(
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


def _tBuildPerFrameCheck(webSocketAuthorization):
    """Build a per-frame check over a REAL redeemed browser credential.

    The credential half must genuinely pass, or a test of the container
    half would go green for the wrong reason -- the check fails closed
    on the first unmet condition, so a fabricated credential hides
    whatever comes after it.
    """
    from vaibify.gui import browserSession

    dictStore = browserSession.fdictCreateBrowserSessionStore()
    sCapability = browserSession.fsMintBootstrapCapability(dictStore)
    sSessionId, sCredential = browserSession.ftRedeemCapability(
        dictStore, sCapability,
    )

    class _FakeConnection:
        headers = {"origin": "http://localhost"}
        query_params = {"sToken": sCredential, "sLeaseId": "LEASE-A"}

    dictOwners = {
        S_PROJECT: OwnerRecord(
            sLeaseId="LEASE-A", fileHandleLock=object(),
            sContainerId="cid-1", sBrowserSessionId=sSessionId,
        ),
    }
    return (
        dictStore,
        webSocketAuthorization.ffbBuildPerFrameCredentialCheck(
            _FakeConnection(), dictStore,
            dictContainerOwners=dictOwners, sName=S_PROJECT,
            iAcceptedGeneration=1,
        ),
        dictOwners,
    )


# ---------------------------------------------------------------------
# The poison FENCE: the pipeline lane, and only the pipeline lane.
# ---------------------------------------------------------------------

def test_the_pipeline_socket_of_a_poisoned_container_is_refused():
    """Driven over a real WebSocket, with the owner's own valid lease.

    Poison denies MUTATIONS, and the pipeline socket is the mutation
    channel. The refusal has to be observable through the real route --
    the poison checks that existed before this all lived at HTTP and
    carrier choke points, so a socket admitted at the gate could still
    drive a run inside a container whose worker was unaccounted for.

    The name and the docker id are kept distinct so the id->name
    resolution is exercised rather than assumed.
    """
    import pytest
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    from vaibify.gui import webSocketAuthorization
    from tests.testContainerSessionResolution import (
        S_CONTAINER_ID, S_CREDENTIAL, S_LEASE, S_PROJECT_NAME,
        _DICT_LOOPBACK_ORIGIN, _fdictBuildContext, _fdictOwnersByName,
        _sPipelineUrl,
    )
    from vaibify.gui.routes.pipelineRoutes import _fnRegisterPipelineWs

    dictOwners = _fdictOwnersByName()
    dictCtx = _fdictBuildContext(dictOwners)
    app = FastAPI()
    _fnRegisterPipelineWs(app, dictCtx)
    client = TestClient(app)

    dictOwners[S_PROJECT_NAME].poison = PoisonRecord(
        sGuardedOperationId="op-wedged",
    )
    with client.websocket_connect(
        _sPipelineUrl(), headers=_DICT_LOOPBACK_ORIGIN,
    ) as websocketClient:
        with pytest.raises(WebSocketDisconnect) as excInfo:
            websocketClient.receive_text()
    assert excInfo.value.code == webSocketAuthorization.I_REJECT_POISONED, (
        "a poisoned container admitted its pipeline socket, or refused "
        f"it with an authorization code; got {excInfo.value.code}"
    )
    assert dictOwners[S_PROJECT_NAME].iLivePipelineConnectionCount == 0
    del S_CONTAINER_ID, S_CREDENTIAL, S_LEASE


def test_the_per_frame_backstop_refuses_once_the_poison_lands():
    """A socket admitted BEFORE the poison must stop acting after it.

    The gate covers connections that arrive after the poison; this
    covers the one that matters more -- the socket already open when a
    worker is force-abandoned. The check is re-read per frame rather
    than captured at accept, so the fact that changed is the fact
    consulted.
    """
    from vaibify.gui import webSocketAuthorization

    dictStore, fbFrameStillAuthorized, dictOwners = (
        _tBuildPerFrameCheck(webSocketAuthorization)
    )
    assert fbFrameStillAuthorized() is True

    dictOwners[S_PROJECT].poison = PoisonRecord(sGuardedOperationId="op-1")
    assert fbFrameStillAuthorized() is False, (
        "a frame arriving after the poison landed was still dispatched"
    )
    del dictStore


def test_the_per_frame_backstop_refuses_a_rotated_generation():
    """A host transfer rotates the generation; the old socket stops.

    The negative half of the same backstop: the socket is authorized
    against the generation admitted at accept, so a successor's
    generation refuses the predecessor's in-flight frame instead of
    letting two owners drive one container.
    """
    from vaibify.gui import webSocketAuthorization

    dictStore, fbFrameStillAuthorized, dictOwners = (
        _tBuildPerFrameCheck(webSocketAuthorization)
    )
    assert fbFrameStillAuthorized() is True
    dictOwners[S_PROJECT].iOwnerGeneration = 2
    assert fbFrameStillAuthorized() is False
    del dictStore


def test_poisoning_returns_only_the_pipeline_connections_to_fence():
    """Safe reads and the host lane are deliberately NOT fenced.

    Poison is cleared by ``vaibify reconcile``, which reaches a live hub
    over the host control socket. A fence that covered every lane would
    fence off the poison's own cure, so the writer returns only the
    mutation lane's connections -- asserted with both lanes present, so
    a fence that simply returned everything fails.
    """
    from vaibify.gui import containerOwnership

    recordOwner = OwnerRecord(
        sLeaseId="LEASE-A", fileHandleLock=object(), sContainerId="cid-1",
        sBrowserSessionId="session-a",
    )
    recordPipeline = containerOwnership.ConnectionRecord(
        connection=object(), sBrowserSessionId="session-a",
        iOwnerGeneration=1, sLane=containerOwnership.S_LANE_PIPELINE,
    )
    recordOther = containerOwnership.ConnectionRecord(
        connection=object(), sBrowserSessionId="session-a",
        iOwnerGeneration=1, sLane=containerOwnership.S_LANE_TERMINAL,
    )
    dictSessionSockets = {"session-a": {recordPipeline, recordOther}}

    listFenced = containerOwnership.flistPoisonAndFenceConnections(
        recordOwner, PoisonRecord(sGuardedOperationId="op-1"),
        dictSessionSockets,
    )
    assert recordOwner.poison is not None
    assert listFenced == [recordPipeline], (
        "the fence must cover the mutation lane only; fencing every "
        "lane would fence off the reconciliation that clears the poison"
    )
