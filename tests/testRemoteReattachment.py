"""Coming back after the hold window expires.

A tunnel down longer than the window leaves the researcher in a
peculiar position: the project is still theirs -- the record keeps its
flock, its keep-alive and any running work -- but the credential that
proved it was theirs has been revoked. Handing back a FRESH sign-in
lands them on the picker looking at a project they cannot claim,
because the very busy-veto that protects their run also refuses the
take-over. They would watch their own six-hour job from outside it.

Transfer is the path that exists for exactly this, and it ADOPTS a
registered durable task rather than interrupting it. What was missing
was the question "which project?", which a returning client cannot
answer: it never sent one, because the project is chosen in the
dashboard after the tunnel is up. So the hub is asked.

Only ORPHANED records are offered. An ACTIVE one has a browser
attending it right now, and reattaching to that would evict a live
session rather than resume a dead one -- the difference between
recovery and theft.
"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from vaibify.cli import commandRemoteHelper
from vaibify.cli.remoteProtocol import (
    S_CAPABILITY_BOOTSTRAP,
    S_CAPABILITY_TRANSFER,
    fdictParseStartupRecord,
    fsFormatStartupRecord,
    fsLocalDashboardUrl,
)
from vaibify.config import containerLock
from vaibify.gui import containerOwnership, pipelineServer
from tests.sessionTokenTestHelper import fsBootstrapCredential
from tests.testAgentLaneEnforcement import (
    MockDockerConnection,
    S_CONTAINER_ID,
    S_CONTAINER_NAME,
)


@pytest.fixture(autouse=True)
def fixtureIsolateLockDir(tmp_path, monkeypatch):
    monkeypatch.setattr(
        containerLock, "_S_LOCK_DIRECTORY", str(tmp_path),
    )


@pytest.fixture
def appHub():
    with patch.object(
        pipelineServer, "_fconnectionCreateDocker",
        MockDockerConnection,
    ):
        return pipelineServer.fappCreateHubApplication(iExpectedPort=0)


def _fnClaimThenOrphan(appHub, sState):
    """Claim the container, then put its record into sState."""
    clientBrowser = TestClient(
        appHub, headers={"X-Session-Token": fsBootstrapCredential(appHub)},
    )
    responseClaim = clientBrowser.post(
        f"/api/registry/{S_CONTAINER_NAME}/claim",
    )
    assert responseClaim.status_code == 200, responseClaim.text
    recordOwner = appHub.state.dictContainerOwners[S_CONTAINER_NAME]
    recordOwner.sState = sState
    return recordOwner


def _flistOfferedBy(appHub):
    """Return what the control handler reports as reattachable."""
    import asyncio
    from vaibify.gui.hostControlChannel import (
        _fdictHandleListReattachable,
    )
    # asyncio.run, not get_event_loop().run_until_complete: the latter
    # raises "no current event loop" the moment any earlier test in the
    # process has closed one, so these passed alone and failed in the
    # suite -- the shape that gets written off as flakiness.
    dictAnswer = asyncio.run(
        _fdictHandleListReattachable(appHub, {}, {}),
    )
    return dictAnswer["listReattachable"]


def test_an_orphaned_session_is_offered_for_reattachment(appHub):
    """The case the whole feature exists for."""
    _fnClaimThenOrphan(
        appHub, containerOwnership.S_OWNER_STATE_ORPHANED_SESSION,
    )
    listOffered = _flistOfferedBy(appHub)
    assert len(listOffered) == 1
    assert listOffered[0]["sContainerName"] == S_CONTAINER_NAME
    assert "iOwnerGeneration" in listOffered[0], (
        "the generation is the ABA guard; without it a stale transfer "
        "could displace a successor owner"
    )


def test_an_active_session_is_never_offered(appHub):
    """The symmetric half, and it is the one with teeth.

    Offering an ACTIVE record would evict a researcher who is sitting
    there working -- recovery and theft differ only by this check.
    """
    _fnClaimThenOrphan(
        appHub, containerOwnership.S_OWNER_STATE_ACTIVE,
    )
    assert _flistOfferedBy(appHub) == [], (
        "a live session was offered for reattachment; a reconnecting "
        "tunnel would have evicted the browser attending it"
    )


def test_nothing_orphaned_means_nothing_offered(appHub):
    """A first connection has nothing to come back to."""
    assert _flistOfferedBy(appHub) == []


def test_several_candidates_decline_rather_than_guess(monkeypatch):
    """Guessing would hand back a project they did not leave."""
    monkeypatch.setattr(
        commandRemoteHelper, "_fnSay", lambda sMessage: None,
    )
    from vaibify.gui import hostControlChannel
    monkeypatch.setattr(
        hostControlChannel, "fdictSendHostControlRequest",
        lambda iPort, dictRequest, *args, **kwargs: {
            "bAccepted": True,
            "listReattachable": [
                {"sContainerName": "one", "iOwnerGeneration": 1},
                {"sContainerName": "two", "iOwnerGeneration": 1},
            ],
        },
    )
    tOffer = commandRemoteHelper.ftOfferReattachment(18050)
    assert tOffer[0] == S_CAPABILITY_BOOTSTRAP, (
        "with two candidates the helper must sign in fresh and let "
        "the researcher choose, not pick one"
    )


def test_a_refused_mint_falls_back_to_signing_in_fresh(monkeypatch):
    """A record reaped between listing and minting is ordinary.

    Not an error: a fresh sign-in still works, and failing the whole
    connection over a race would be worse than the thing it guards.
    """
    monkeypatch.setattr(
        commandRemoteHelper, "_fnSay", lambda sMessage: None,
    )
    from vaibify.gui import hostControlChannel

    def _fdictAnswer(iPort, dictRequest, *args, **kwargs):
        if dictRequest["sOperation"] == "list-reattachable":
            return {
                "bAccepted": True,
                "listReattachable": [
                    {"sContainerName": "gone", "iOwnerGeneration": 3},
                ],
            }
        return {"bAccepted": False, "bMinted": False,
                "sError": "that container is unowned"}

    monkeypatch.setattr(
        hostControlChannel, "fdictSendHostControlRequest", _fdictAnswer,
    )
    assert commandRemoteHelper.ftOfferReattachment(18050)[0] == (
        S_CAPABILITY_BOOTSTRAP
    )


def test_a_transfer_capability_rides_the_transfer_fragment():
    """The two lanes are not interchangeable.

    The bootstrap lane refuses a transfer capability outright -- by
    design, because redeeming one there would mint a bare credential
    with the ownership hand-over skipped. Putting it in the wrong
    fragment would fail as an unexplainable 401.
    """
    sUrl = fsLocalDashboardUrl(18050, "A" * 43, S_CAPABILITY_TRANSFER)
    assert "#transfer=" in sUrl
    assert "#bootstrap=" not in sUrl
    sUrlPlain = fsLocalDashboardUrl(18050, "A" * 43)
    assert "#bootstrap=" in sUrlPlain


def test_the_record_carries_the_kind_and_the_project_it_resumed():
    """The researcher is told which of the two happened."""
    sRecord = fsFormatStartupRecord(
        iPort=18050, sBootstrapCapability="A" * 43,
        sExecutionMode="host", sHostname="compute-machine",
        sCapabilityKind=S_CAPABILITY_TRANSFER,
        sReattachedContainerName="AI Greenhouse",
    )
    dictRecord = fdictParseStartupRecord(sRecord, 18050)
    assert dictRecord["sCapabilityKind"] == S_CAPABILITY_TRANSFER
    assert dictRecord["sReattachedContainerName"] == "AI Greenhouse", (
        "a project name may contain a space; it travels as JSON here "
        "and never through a shell"
    )


def test_an_unknown_capability_kind_is_refused():
    """Closed vocabulary: the kind decides which lane redeems it."""
    from vaibify.cli.remoteProtocol import RemoteProtocolError
    sRecord = fsFormatStartupRecord(
        iPort=18050, sBootstrapCapability="A" * 43,
        sExecutionMode="host", sHostname="h",
    ).replace('"sCapabilityKind":"bootstrap"', '"sCapabilityKind":"magic"')
    with pytest.raises(RemoteProtocolError) as excinfo:
        fdictParseStartupRecord(sRecord, 18050)
    assert "capability kind" in str(excinfo.value)
