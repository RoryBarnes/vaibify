"""Tests for the host-only control socket (design §6b/§14, 3c).

The end-to-end tests bind a REAL Unix domain socket, serve it with the
real handlers on a real event loop, and connect with the real blocking
client — so the peer-credential shim (``ftPeerUidGid``) is
exercised for real on whichever platform runs the suite: the
``LOCAL_PEERCRED`` branch on macOS, the ``SO_PEERCRED`` branch on
Linux CI. The opposite platform's *parser* is structure-tested here
against hand-packed kernel structs; its live branch is covered by the
other CI platform, not by this run.
"""

import asyncio
import json
import os
import socket
import stat
import struct
import tempfile
from types import SimpleNamespace

import pytest

from vaibify.config import containerLock, operationJournal
from vaibify.gui import containerOwnership, hostControlChannel
from vaibify.gui.hostControlChannel import (
    HostControlError,
    fdictSendHostControlRequest,
    ftPeerUidGid,
    fnRegisterHostControlChannel,
    fnUnlinkStaleControlSockets,
    fsControlSocketPathForPort,
)

S_PROJECT = "demo"
I_HUB_PORT = 8123


@pytest.fixture(autouse=True)
def fixtureIsolateJournalAndLockDirs(tmp_path, monkeypatch):
    """Redirect ~/.vaibify/journal and ~/.vaibify/locks to tmp_path."""
    monkeypatch.setattr(
        operationJournal, "_S_JOURNAL_DIRECTORY", str(tmp_path / "journal"),
    )
    monkeypatch.setattr(
        containerLock, "_S_LOCK_DIRECTORY", str(tmp_path / "locks"),
    )
    return tmp_path


@pytest.fixture
def fixtureShortControlDirectory(monkeypatch):
    """Point the control directory at a path short enough for AF_UNIX.

    ``sun_path`` is limited to ~104 bytes on macOS; pytest's tmp_path
    can exceed it, so the sockets get a short mkdtemp instead.
    """
    sDirectory = tempfile.mkdtemp(prefix="vaibifyCtl")
    if len(sDirectory) > 70:
        sDirectory = tempfile.mkdtemp(prefix="vaibifyCtl", dir="/tmp")
    monkeypatch.setattr(
        hostControlChannel, "_S_CONTROL_DIRECTORY", sDirectory,
    )
    yield sDirectory
    for sEntry in os.listdir(sDirectory):
        os.unlink(os.path.join(sDirectory, sEntry))
    os.rmdir(sDirectory)


def _fappBuildFakeHubApplication(iHubPort=I_HUB_PORT):
    """Return a SimpleNamespace app with the hub state the channel uses."""
    from vaibify.gui import browserSession
    return SimpleNamespace(state=SimpleNamespace(
        iHubPort=iHubPort,
        listLifespanStartup=[],
        listLifespanShutdown=[],
        dictContainerOwners={},
        dictMutationSupervisors={},
        dictDurableTaskRecords={},
        dictBrowserSessions=browserSession.fdictCreateBrowserSessionStore(),
    ))


def _frecordOwnerHoldingFlock():
    """Return an OwnerRecord that presents as holding the host flock."""
    return containerOwnership.OwnerRecord(
        sLeaseId="LEASE-A", fileHandleLock=object(), sContainerId="cid-1",
    )


async def _fdictDriveOneRequest(app, dictCtx, dictRequest):
    """Start the real server, send one real request, stop the server."""
    fnRegisterHostControlChannel(app, dictCtx)
    for fnStartup in app.state.listLifespanStartup:
        await fnStartup(app)
    try:
        return await asyncio.to_thread(
            fdictSendHostControlRequest, app.state.iHubPort, dictRequest,
        )
    finally:
        for fnShutdown in app.state.listLifespanShutdown:
            await fnShutdown(app)


def _fdictSendToFakeHub(app, dictRequest, dictCtx=None):
    """Synchronous wrapper around one served request/response cycle."""
    return asyncio.run(
        _fdictDriveOneRequest(app, dictCtx or {}, dictRequest),
    )


# ---------------------------------------------------------------------
# Path derivation and stale-socket discovery.
# ---------------------------------------------------------------------

def test_socket_path_requires_a_positive_integer_port():
    for valuePort in (0, -5, True, "8123", None):
        with pytest.raises(HostControlError):
            fsControlSocketPathForPort(valuePort)
    assert fsControlSocketPathForPort(8123).endswith(
        "hub-8123.controlSocket",
    )


def test_stale_sockets_are_unlinked_and_live_or_foreign_entries_kept(
    fixtureShortControlDirectory, monkeypatch,
):
    """Dead-hub sockets go; live-hub sockets and non-sockets stay."""
    import vaibify.config.sessionRegistry as sessionRegistry
    sDirectory = fixtureShortControlDirectory
    sStalePath = os.path.join(sDirectory, "hub-9001.controlSocket")
    sLivePath = os.path.join(sDirectory, "hub-9002.controlSocket")
    for sPath in (sStalePath, sLivePath):
        socketBound = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        socketBound.bind(sPath)
        socketBound.close()
    sForeignPath = os.path.join(sDirectory, "hub-9003.controlSocket")
    with open(sForeignPath, "w") as fileHandle:
        fileHandle.write("not a socket")
    monkeypatch.setattr(
        sessionRegistry, "fdictReadHubSlotByPort",
        lambda iPort: {"iPort": iPort} if iPort == 9002 else {},
    )
    fnUnlinkStaleControlSockets()
    assert not os.path.exists(sStalePath)
    assert os.path.exists(sLivePath)
    assert os.path.exists(sForeignPath), (
        "a non-socket entry must never be unlinked"
    )


def test_bind_refuses_a_non_socket_squatter_at_the_socket_path(
    fixtureShortControlDirectory,
):
    sPath = fsControlSocketPathForPort(I_HUB_PORT)
    with open(sPath, "w") as fileHandle:
        fileHandle.write("squatter")
    app = _fappBuildFakeHubApplication()
    fnRegisterHostControlChannel(app, {})
    with pytest.raises(HostControlError):
        asyncio.run(app.state.listLifespanStartup[0](app))
    assert os.path.exists(sPath)


def test_harness_hub_with_port_zero_binds_no_socket(
    fixtureShortControlDirectory,
):
    app = _fappBuildFakeHubApplication(iHubPort=0)
    fnRegisterHostControlChannel(app, {})
    asyncio.run(app.state.listLifespanStartup[0](app))
    assert getattr(app.state, "serverHostControl", None) is None
    assert os.listdir(fixtureShortControlDirectory) == []


# ---------------------------------------------------------------------
# The peer-credential shim.
# ---------------------------------------------------------------------

def test_peer_credentials_resolve_to_this_user_over_a_real_connection(
    fixtureShortControlDirectory,
):
    """The live platform branch, driven by a real connect + accept."""
    sPath = os.path.join(fixtureShortControlDirectory, "peerProbe.socket")
    socketServer = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    socketServer.bind(sPath)
    socketServer.listen(1)
    socketClient = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        socketClient.connect(sPath)
        socketAccepted, _ = socketServer.accept()
        try:
            iPeerUid, iPeerGid = ftPeerUidGid(socketAccepted)
        finally:
            socketAccepted.close()
    finally:
        socketClient.close()
        socketServer.close()
    assert iPeerUid == os.getuid()
    assert iPeerGid == os.getgid()


def test_linux_ucred_parser_reads_a_packed_struct_and_fails_closed():
    """Structure test for the SO_PEERCRED branch (live on Linux CI)."""
    byteCredentials = struct.pack("3i", 4242, 501, 20)
    assert hostControlChannel._ftParseLinuxPeerCredentials(
        byteCredentials,
    ) == (501, 20)
    with pytest.raises(HostControlError):
        hostControlChannel._ftParseLinuxPeerCredentials(b"\x01\x02")


def test_darwin_xucred_parser_reads_a_packed_struct_and_fails_closed():
    """Structure test for the LOCAL_PEERCRED branch (live on macOS)."""
    byteCredentials = bytearray(76)
    struct.pack_into("IIh", byteCredentials, 0, 0, 501, 1)
    struct.pack_into("I", byteCredentials, 12, 20)
    assert hostControlChannel._ftParseDarwinPeerCredentials(
        bytes(byteCredentials),
    ) == (501, 20)
    byteBadVersion = bytearray(byteCredentials)
    struct.pack_into("IIh", byteBadVersion, 0, 7, 501, 1)
    with pytest.raises(HostControlError):
        hostControlChannel._ftParseDarwinPeerCredentials(
            bytes(byteBadVersion),
        )
    byteNoGroups = bytearray(byteCredentials)
    struct.pack_into("IIh", byteNoGroups, 0, 0, 501, 0)
    with pytest.raises(HostControlError):
        hostControlChannel._ftParseDarwinPeerCredentials(
            bytes(byteNoGroups),
        )
    with pytest.raises(HostControlError):
        hostControlChannel._ftParseDarwinPeerCredentials(b"\x00\x01")


def test_a_reset_by_the_hub_reads_as_an_unreadable_response(
    fixtureShortControlDirectory, monkeypatch,
):
    """A refusal delivered as RST must say what a refusal delivered as EOF says.

    The hub closes on a peer it will not serve without writing a byte.
    macOS surfaces that to the client as EOF, so the reader returns b""
    and the unreadable-response branch handles it. Linux sends RST when
    a socket is closed with unread data still in its receive buffer, so
    the identical refusal arrives as ConnectionResetError instead --
    and before this guard existed it escaped as a raw traceback on
    Linux while macOS printed a clean sentence. The suite is developed
    on macOS, so CI was the only place this could show, and the lane
    that would have shown it could not check the repository out.

    Kills: in hostControlChannel.fdictSendHostControlRequest, remove
    ConnectionResetError from the except clause guarding the send and
    read, so the reset escapes instead of becoming a HostControlError.
    """
    import socket as socketModule

    def _fnResetOnSend(self, *args, **kwargs):
        raise ConnectionResetError(104, "Connection reset by peer")

    monkeypatch.setattr(socketModule.socket, "sendall", _fnResetOnSend)
    app = _fappBuildFakeHubApplication()
    with pytest.raises(HostControlError) as excInfo:
        _fdictSendToFakeHub(app, {"sOperation": "reconcile"})
    assert "unreadable" in str(excInfo.value), (
        "a reset must give the researcher the same sentence an EOF "
        f"does; got {excInfo.value!r}"
    )


def test_a_foreign_peer_is_closed_without_a_byte_of_response(
    fixtureShortControlDirectory, monkeypatch,
):
    monkeypatch.setattr(
        hostControlChannel, "_fbPeerIsThisUser", lambda writer: False,
    )
    app = _fappBuildFakeHubApplication()
    with pytest.raises(HostControlError) as excInfo:
        _fdictSendToFakeHub(app, {"sOperation": "reconcile"})
    assert "unreadable" in str(excInfo.value)


# ---------------------------------------------------------------------
# The served protocol: closed allowlist, refusals, real operations.
# ---------------------------------------------------------------------

def test_socket_file_and_directory_carry_restrictive_modes(
    fixtureShortControlDirectory,
):
    async def _fnInspectWhileServing():
        app = _fappBuildFakeHubApplication()
        fnRegisterHostControlChannel(app, {})
        await app.state.listLifespanStartup[0](app)
        try:
            iSocketMode = stat.S_IMODE(
                os.stat(fsControlSocketPathForPort(I_HUB_PORT)).st_mode,
            )
            iDirectoryMode = stat.S_IMODE(
                os.stat(fixtureShortControlDirectory).st_mode,
            )
            return iSocketMode, iDirectoryMode
        finally:
            await app.state.listLifespanShutdown[0](app)

    iSocketMode, iDirectoryMode = asyncio.run(_fnInspectWhileServing())
    assert iSocketMode == 0o600
    assert iDirectoryMode == 0o700


def test_shutdown_unlinks_the_socket(fixtureShortControlDirectory):
    app = _fappBuildFakeHubApplication()
    _fdictSendToFakeHub(app, {"sOperation": "no-such-operation"})
    assert not os.path.exists(fsControlSocketPathForPort(I_HUB_PORT))


def test_an_unknown_opcode_is_refused_naming_the_allowlist(
    fixtureShortControlDirectory,
):
    app = _fappBuildFakeHubApplication()
    dictResponse = _fdictSendToFakeHub(
        app, {"sOperation": "seize-container"},
    )
    assert dictResponse["bAccepted"] is False
    for sOperation in (
        "reconcile", "force-abandon", "break-glass", "mint-bootstrap",
    ):
        assert sOperation in dictResponse["sError"]


def test_a_non_json_request_line_is_refused(fixtureShortControlDirectory):
    async def _fdictDriveRawBytes(byteLine):
        app = _fappBuildFakeHubApplication()
        fnRegisterHostControlChannel(app, {})
        await app.state.listLifespanStartup[0](app)
        try:
            def fnSendRaw():
                socketClient = socket.socket(
                    socket.AF_UNIX, socket.SOCK_STREAM,
                )
                try:
                    socketClient.settimeout(10.0)
                    socketClient.connect(
                        fsControlSocketPathForPort(I_HUB_PORT),
                    )
                    socketClient.sendall(byteLine)
                    return socketClient.recv(65536)
                finally:
                    socketClient.close()
            return json.loads(await asyncio.to_thread(fnSendRaw))
        finally:
            await app.state.listLifespanShutdown[0](app)

    dictResponse = asyncio.run(_fdictDriveRawBytes(b"not json\n"))
    assert dictResponse["bAccepted"] is False
    assert "not a JSON object" in dictResponse["sError"]
    dictResponse = asyncio.run(_fdictDriveRawBytes(b'"a bare string"\n'))
    assert dictResponse["bAccepted"] is False


def test_reconcile_requires_the_expected_operation_ids_aba_guard(
    fixtureShortControlDirectory,
):
    app = _fappBuildFakeHubApplication()
    for valueIds in (None, [], ["ok", 7], "id-1"):
        dictRequest = {
            "sOperation": "reconcile", "sContainerName": S_PROJECT,
        }
        if valueIds is not None:
            dictRequest["listExpectedOperationIds"] = valueIds
        dictResponse = _fdictSendToFakeHub(app, dictRequest)
        assert dictResponse["bAccepted"] is False
        assert "listExpectedOperationIds" in dictResponse["sError"]


def test_reconcile_refuses_a_container_this_hub_does_not_hold(
    fixtureShortControlDirectory,
):
    app = _fappBuildFakeHubApplication()
    dictResponse = _fdictSendToFakeHub(app, {
        "sOperation": "reconcile",
        "sContainerName": S_PROJECT,
        "listExpectedOperationIds": ["op-1"],
    })
    assert dictResponse["bAccepted"] is False
    assert "does not hold" in dictResponse["sError"]


def test_reconcile_over_the_socket_proves_clears_and_unpoisons(
    fixtureShortControlDirectory,
):
    """The live-hub transaction: prove, clear poison, clear marker last."""
    import subprocess
    import sys
    processDead = subprocess.Popen(
        [sys.executable, "-c", "pass"], start_new_session=True,
    )
    processDead.wait()
    sOperationId = operationJournal.fsPrepareOperation(
        S_PROJECT, "helper", "an abandoned helper",
    )
    operationJournal.fnPromoteOperationToInFlight(
        S_PROJECT, sOperationId,
        {"iHolderPid": processDead.pid, "iHolderProcessGroup": processDead.pid},
    )
    operationJournal.fnMarkOperationNeedsReconciliation(
        S_PROJECT, sOperationId,
    )
    app = _fappBuildFakeHubApplication()
    recordOwner = _frecordOwnerHoldingFlock()
    recordOwner.poison = containerOwnership.PoisonRecord(
        sGuardedOperationId=sOperationId,
    )
    app.state.dictContainerOwners[S_PROJECT] = recordOwner
    dictResponse = _fdictSendToFakeHub(app, {
        "sOperation": "reconcile",
        "sContainerName": S_PROJECT,
        "listExpectedOperationIds": [sOperationId],
    })
    assert dictResponse["bAccepted"] is True
    assert dictResponse["bReconciled"] is True
    assert recordOwner.poison is None
    assert not os.path.exists(
        operationJournal.fsJournalPathFor(S_PROJECT),
    )


def test_reconcile_over_the_socket_refuses_stale_ids_and_keeps_the_marker(
    fixtureShortControlDirectory,
):
    sOperationId = operationJournal.fsPrepareOperation(
        S_PROJECT, "helper", "a helper",
    )
    app = _fappBuildFakeHubApplication()
    app.state.dictContainerOwners[S_PROJECT] = _frecordOwnerHoldingFlock()
    dictResponse = _fdictSendToFakeHub(app, {
        "sOperation": "reconcile",
        "sContainerName": S_PROJECT,
        "listExpectedOperationIds": ["a-successor-operation"],
    })
    assert dictResponse["bAccepted"] is False
    assert "changed since it was inspected" in dictResponse["sError"]
    assert sOperationId in (
        operationJournal.fdictReadJournalOutcome(S_PROJECT)[
            "dictOperations"
        ]
    )


def test_force_abandon_requires_a_matching_live_or_journaled_operation(
    fixtureShortControlDirectory,
):
    app = _fappBuildFakeHubApplication()
    app.state.dictContainerOwners[S_PROJECT] = _frecordOwnerHoldingFlock()
    dictResponse = _fdictSendToFakeHub(app, {
        "sOperation": "force-abandon",
        "sContainerName": S_PROJECT,
        "sExpectedOperationId": "a-stale-id",
    })
    assert dictResponse["bAccepted"] is False
    assert "stale force-abandon" in dictResponse["sError"]
    assert app.state.dictContainerOwners[S_PROJECT].poison is None


def test_force_abandon_poisons_the_record_and_mirrors_into_the_journal(
    fixtureShortControlDirectory,
):
    sOperationId = operationJournal.fsPrepareOperation(
        S_PROJECT, "exec", "a wedged command",
    )
    operationJournal.fnPromoteOperationToInFlight(
        S_PROJECT, sOperationId, {"sDockerExecId": "feedface"},
    )
    app = _fappBuildFakeHubApplication()
    recordOwner = _frecordOwnerHoldingFlock()
    app.state.dictContainerOwners[S_PROJECT] = recordOwner
    dictResponse = _fdictSendToFakeHub(app, {
        "sOperation": "force-abandon",
        "sContainerName": S_PROJECT,
        "sExpectedOperationId": sOperationId,
    })
    assert dictResponse["bAccepted"] is True
    assert dictResponse["bPoisoned"] is True
    assert dictResponse["bDurableMirrorWritten"] is True
    assert recordOwner.poison is not None
    assert recordOwner.poison.sGuardedOperationId == sOperationId
    dictRecord = operationJournal.fdictReadJournalOutcome(S_PROJECT)[
        "dictOperations"
    ][sOperationId]
    assert dictRecord["sState"] == "NEEDS_RECONCILIATION"


def test_break_glass_over_the_socket_clears_only_the_hash_matched_marker(
    fixtureShortControlDirectory, monkeypatch,
):
    listStopped = []

    def _fbRecordProvenStop(sContainerName):
        listStopped.append(sContainerName)
        return True

    monkeypatch.setattr(
        hostControlChannel, "_fbStopContainerByNameProven",
        _fbRecordProvenStop,
    )
    sJournalPath = operationJournal.fsJournalPathFor(S_PROJECT)
    os.makedirs(os.path.dirname(sJournalPath), exist_ok=True)
    with open(sJournalPath, "wb") as fileHandle:
        fileHandle.write(b"\x00malformed marker bytes")
    app = _fappBuildFakeHubApplication()
    app.state.dictContainerOwners[S_PROJECT] = _frecordOwnerHoldingFlock()
    dictRefused = _fdictSendToFakeHub(app, {
        "sOperation": "break-glass",
        "sContainerName": S_PROJECT,
        "sMarkerSha256": "f" * 64,
    })
    assert dictRefused["bAccepted"] is False
    assert os.path.exists(sJournalPath)
    dictResponse = _fdictSendToFakeHub(app, {
        "sOperation": "break-glass",
        "sContainerName": S_PROJECT,
        "sMarkerSha256": (
            operationJournal.fsComputeJournalFileSha256(S_PROJECT)
        ),
    })
    assert dictResponse["bAccepted"] is True
    assert dictResponse["bCleared"] is True
    assert listStopped == [S_PROJECT]
    assert not os.path.exists(sJournalPath)


def test_an_invalid_container_name_is_refused_on_every_operation(
    fixtureShortControlDirectory,
):
    app = _fappBuildFakeHubApplication()
    for sOperation in (
        "reconcile", "force-abandon", "break-glass", "mint-transfer",
    ):
        dictResponse = _fdictSendToFakeHub(app, {
            "sOperation": sOperation,
            "sContainerName": "../escape",
            "listExpectedOperationIds": ["op"],
            "sExpectedOperationId": "op",
            "sMarkerSha256": "f" * 64,
        })
        assert dictResponse["bAccepted"] is False
        assert "sContainerName" in dictResponse["sError"]


# ---------------------------------------------------------------------
# mint-transfer: the vaibify open handshake (design §6b, slice 5).
# ---------------------------------------------------------------------

def test_mint_transfer_without_generation_describes_and_mints_nothing(
    fixtureShortControlDirectory,
):
    app = _fappBuildFakeHubApplication()
    recordOwner = _frecordOwnerHoldingFlock()
    recordOwner.iOwnerGeneration = 7
    app.state.dictContainerOwners[S_PROJECT] = recordOwner
    dictResponse = _fdictSendToFakeHub(app, {
        "sOperation": "mint-transfer",
        "sContainerName": S_PROJECT,
    })
    assert dictResponse == {
        "bAccepted": True,
        "bMinted": False,
        "iCurrentOwnerGeneration": 7,
    }
    assert app.state.dictBrowserSessions["dictCapabilities"] == {}


def test_mint_transfer_binds_the_capability_to_the_seen_generation(
    fixtureShortControlDirectory,
):
    from vaibify.gui import browserSession
    app = _fappBuildFakeHubApplication()
    recordOwner = _frecordOwnerHoldingFlock()
    recordOwner.iOwnerGeneration = 3
    app.state.dictContainerOwners[S_PROJECT] = recordOwner
    dictResponse = _fdictSendToFakeHub(app, {
        "sOperation": "mint-transfer",
        "sContainerName": S_PROJECT,
        "iExpectedOwnerGeneration": 3,
    })
    assert dictResponse["bAccepted"] is True
    assert dictResponse["bMinted"] is True
    sCapability = dictResponse["sTransferCapability"]
    dictInspect = browserSession.fdictInspectTransferCapability(
        app.state.dictBrowserSessions, sCapability,
    )
    assert dictInspect["sState"] == "ARMED"
    assert dictInspect["sContainerName"] == S_PROJECT
    assert dictInspect["iExpectedOwnerGeneration"] == 3
    assert "sLeaseId" not in dictResponse
    assert "sCredential" not in dictResponse


@pytest.mark.falsification
def test_mint_transfer_refuses_a_generation_the_hub_no_longer_serves(
    fixtureShortControlDirectory,
):
    """A stale CLI can never mint against a successor generation.

    Case 2, mint half (design §6b): the mint round trip re-compares
    the described generation against the live record, so a CLI that
    described generation 1 cannot mint after a transfer bumped the
    owner to generation 2 — it is told to look again, and no
    capability exists that could ever displace the successor.

    Kills: dropping the generation comparison from
    ``_fdictHandleMintTransfer`` so any positive integer mints.
    """
    app = _fappBuildFakeHubApplication()
    recordOwner = _frecordOwnerHoldingFlock()
    recordOwner.iOwnerGeneration = 2
    app.state.dictContainerOwners[S_PROJECT] = recordOwner
    dictResponse = _fdictSendToFakeHub(app, {
        "sOperation": "mint-transfer",
        "sContainerName": S_PROJECT,
        "iExpectedOwnerGeneration": 1,
    })
    assert dictResponse["bAccepted"] is False
    assert "generation 2, not 1" in dictResponse["sError"]
    assert app.state.dictBrowserSessions["dictCapabilities"] == {}


def test_mint_transfer_for_an_unowned_container_says_claim_normally(
    fixtureShortControlDirectory,
):
    app = _fappBuildFakeHubApplication()
    dictResponse = _fdictSendToFakeHub(app, {
        "sOperation": "mint-transfer",
        "sContainerName": S_PROJECT,
    })
    assert dictResponse["bAccepted"] is False
    assert dictResponse["bUnowned"] is True
    assert "claim it normally" in dictResponse["sError"]


def test_mint_transfer_refuses_a_non_positive_or_boolean_generation(
    fixtureShortControlDirectory,
):
    app = _fappBuildFakeHubApplication()
    app.state.dictContainerOwners[S_PROJECT] = _frecordOwnerHoldingFlock()
    for valueGeneration in (0, -1, True, "1", 1.5):
        dictResponse = _fdictSendToFakeHub(app, {
            "sOperation": "mint-transfer",
            "sContainerName": S_PROJECT,
            "iExpectedOwnerGeneration": valueGeneration,
        })
        assert dictResponse["bAccepted"] is False, valueGeneration
        assert "positive integer" in dictResponse["sError"]


# ---------------------------------------------------------------------
# mint-bootstrap: the headless `vaibify do` credential (§6b, slice 8).
# ---------------------------------------------------------------------

def test_mint_bootstrap_mints_an_ordinary_launch_capability(
    fixtureShortControlDirectory,
):
    """The op hands back a plain bootstrap capability, nothing more.

    It must be indistinguishable from the one the hub puts in a
    browser's URL fragment: operation ``bootstrap``, no container
    name, no expected owner generation — so redeeming it can only
    create a session, never displace an owner.
    """
    from vaibify.gui import browserSession
    app = _fappBuildFakeHubApplication()
    dictResponse = _fdictSendToFakeHub(app, {
        "sOperation": "mint-bootstrap",
    })
    assert dictResponse["bAccepted"] is True
    assert dictResponse["bMinted"] is True
    sCapability = dictResponse["sBootstrapCapability"]
    recordCapability = (
        app.state.dictBrowserSessions["dictCapabilities"][sCapability]
    )
    assert recordCapability.sOperation == (
        browserSession.S_CAPABILITY_OPERATION_BOOTSTRAP
    )
    assert recordCapability.sState == "ARMED"
    assert recordCapability.sContainerName == ""
    assert recordCapability.iExpectedOwnerGeneration == 0
    assert "sCredential" not in dictResponse
    assert "sLeaseId" not in dictResponse


def test_mint_bootstrap_never_touches_an_owner_record(
    fixtureShortControlDirectory,
):
    """Minting for the CLI leaves a dashboard owner exactly as it was."""
    app = _fappBuildFakeHubApplication()
    recordOwner = _frecordOwnerHoldingFlock()
    recordOwner.iOwnerGeneration = 4
    app.state.dictContainerOwners[S_PROJECT] = recordOwner
    sLeaseBefore = recordOwner.sLeaseId
    sSessionBefore = recordOwner.sBrowserSessionId
    assert _fdictSendToFakeHub(app, {
        "sOperation": "mint-bootstrap",
        "sContainerName": S_PROJECT,
    })["bMinted"] is True
    assert recordOwner.iOwnerGeneration == 4
    assert recordOwner.sLeaseId == sLeaseBefore
    assert recordOwner.sBrowserSessionId == sSessionBefore


def test_mint_bootstrap_refuses_a_hub_with_no_session_store(
    fixtureShortControlDirectory,
):
    app = _fappBuildFakeHubApplication()
    app.state.dictBrowserSessions = None
    dictResponse = _fdictSendToFakeHub(app, {
        "sOperation": "mint-bootstrap",
    })
    assert dictResponse["bAccepted"] is False
    assert "browser-session store" in dictResponse["sError"]


# ---------------------------------------------------------------------
# The host-side client.
# ---------------------------------------------------------------------

def test_client_refuses_a_missing_or_non_socket_path(
    fixtureShortControlDirectory,
):
    with pytest.raises(HostControlError) as excInfo:
        fdictSendHostControlRequest(I_HUB_PORT, {"sOperation": "x"})
    assert "still running" in str(excInfo.value)
    sPath = fsControlSocketPathForPort(I_HUB_PORT)
    with open(sPath, "w") as fileHandle:
        fileHandle.write("not a socket")
    with pytest.raises(HostControlError):
        fdictSendHostControlRequest(I_HUB_PORT, {"sOperation": "x"})


def test_client_reports_a_stale_bound_socket_with_no_listener(
    fixtureShortControlDirectory,
):
    sPath = fsControlSocketPathForPort(I_HUB_PORT)
    socketBound = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    socketBound.bind(sPath)
    socketBound.close()
    with pytest.raises(HostControlError) as excInfo:
        fdictSendHostControlRequest(I_HUB_PORT, {"sOperation": "x"})
    assert "stale or unreachable" in str(excInfo.value)


# ---------------------------------------------------------------------
# Case 26b — the full force-abandon lifecycle over the socket.
# ---------------------------------------------------------------------

@pytest.mark.falsification
def test_force_abandon_lifecycle_poisons_refuses_and_reconciles(
    fixtureShortControlDirectory,
):
    """Case 26b: poison, retain, refuse everything, exit via reconcile.

    The full lifecycle, socket-driven end to end: a host force-abandon
    sets the ``PoisonRecord`` while the flock stays held; a claim is
    refused AS poisoned and a host transfer is refused naming the
    force-abandon (BEFORE the journal quarantine check, so the message
    points at the right recovery); the record is never reapable; and
    only ``reconcile`` — proving the recorded worker dead — clears the
    poison, after which the very same ARMED transfer capability
    commits.

    Kills: making the force-abandon handler acknowledge without
    setting the poison (``recordOwner.poison = None``): the transfer
    refusal then reads as a journal quarantine rather than the
    force-abandon, and the claim refusal loses ``bPoisoned``.
    """
    import subprocess
    import sys
    from vaibify.gui import browserSession, sessionLifecycle
    processDead = subprocess.Popen(
        [sys.executable, "-c", "pass"], start_new_session=True,
    )
    processDead.wait()
    sOperationId = operationJournal.fsPrepareOperation(
        S_PROJECT, "helper", "a wedged helper",
    )
    operationJournal.fnPromoteOperationToInFlight(
        S_PROJECT, sOperationId,
        {"iHolderPid": processDead.pid,
         "iHolderProcessGroup": processDead.pid},
    )
    app = _fappBuildFakeHubApplication()
    recordOwner = _frecordOwnerHoldingFlock()
    app.state.dictContainerOwners[S_PROJECT] = recordOwner
    dictAbandoned = _fdictSendToFakeHub(app, {
        "sOperation": "force-abandon",
        "sContainerName": S_PROJECT,
        "sExpectedOperationId": sOperationId,
    })
    assert dictAbandoned["bPoisoned"] is True
    assert recordOwner.poison is not None
    assert recordOwner.fileHandleLock is not None, (
        "poison retains the flock; it is never dropped"
    )
    iClaimCode, dictClaimBody = containerOwnership.ftClaim(
        app.state.dictContainerOwners, S_PROJECT, "a-foreign-lease", 8123,
    )
    assert iClaimCode == 409
    assert dictClaimBody.get("bPoisoned") is True
    assert containerOwnership.fbOwnerIsReapable(recordOwner, 0.0) is False
    sCapability = browserSession.fsMintTransferCapability(
        app.state.dictBrowserSessions, S_PROJECT, 1,
    )
    sOutcomeRefused, dictRefusedBody = asyncio.run(
        sessionLifecycle.ftTransferOwnership(app.state, sCapability),
    )
    assert sOutcomeRefused == sessionLifecycle.S_TRANSFER_REFUSED
    assert "force-abandoned" in dictRefusedBody["sMessage"], (
        "the refusal must name the force-abandon, not a generic "
        "journal quarantine"
    )
    dictReconciled = _fdictSendToFakeHub(app, {
        "sOperation": "reconcile",
        "sContainerName": S_PROJECT,
        "listExpectedOperationIds": [sOperationId],
    })
    assert dictReconciled["bAccepted"] is True
    assert recordOwner.poison is None
    sOutcome, dictPayload = asyncio.run(
        sessionLifecycle.ftTransferOwnership(app.state, sCapability),
    )
    assert sOutcome == sessionLifecycle.S_TRANSFER_TRANSFERRED
    assert recordOwner.iOwnerGeneration == 2
