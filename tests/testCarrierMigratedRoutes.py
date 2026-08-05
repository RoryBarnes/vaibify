"""The routes phase 2 migrated, proven at the gate rather than asserted.

WHY THIS FILE EXISTS AT ALL
---------------------------

A migrated route is served on the enforced branch, which mints NO
admission, so its handler must open one through a carrier around each
logical mutation. Forgetting one raises ``MutationNotAdmittedError`` at
the primitive, and **that refusal is the migration's only proof**.

The refusal lives in :mod:`vaibify.config.mutationAdmission`, which the
REAL ``DockerConnection`` calls from ``fnWriteFileViaTar`` and
``texecRunInContainerStreamed``. Every route test in this suite drives a
permissive Docker mock that answers a write by storing bytes and never
calls that gate — so a route that lost its carrier call passes all of
them. This is not hypothetical: the first kill-confirm of this migration
deleted ``_fnCommitDraftWrite``'s carrier call outright and
``tests/testDraftRoutes.py`` reported **17 passed**. A test that cannot
fail when the guarantee is deleted is not evidence of the guarantee.

So the double here calls the SAME gates, under the SAME primitive names,
at the SAME points the real connection calls them, and records the
admission mode that was live at each one. Asserting the MODE rather than
merely "it did not raise" is what distinguishes a carried mutation from
one riding the legacy ambient mint: a route that is still awaiting
reaches the primitive under ``request``, and a migrated one must reach it
under its declared carrier's mode.

Name != id throughout, inherited from the draft harness: the owner map is
keyed by name while these routes resolve by id, and this repository has
shipped a fatal bug under a fully green suite whose fixtures collapsed
the two.
"""

import asyncio
import copy
import json
import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch

from tests.testDraftRoutes import (
    DICT_WORKFLOW,
    MockDockerDraft,
    S_CONTAINER_ID,
    S_PROJECT_REPO,
    S_WORKFLOW_PATH,
    _fnConnect,
)
from tests.sessionTokenTestHelper import fsBootstrapCredential
from vaibify.config import mutationAdmission
from vaibify.gui import (
    browserSession,
    draftManager,
    pipelineServer,
    sessionLifecycle,
)


S_PRIMITIVE_WRITE = "fnWriteFileViaTar"
S_PRIMITIVE_EXEC = "texecRunInContainerStreamed"

# The owner map, the host flock and the journal are all keyed by
# container NAME, while these routes address the container by id. The
# draft harness keeps the two deliberately distinct, so a journal
# assertion has to name this rather than reuse S_CONTAINER_ID.
S_CONTAINER_NAME = "test-container"


class DockerDoubleThatCallsTheRealGates(MockDockerDraft):
    """The draft double, plus the admission checks the real one makes.

    Only the gate calls and the recording are added; every command and
    file answer comes from the parent, so this stays one double rather
    than a second, divergent model of a container.

    ``ftResultExecuteCommand`` is where the gate is placed because the
    parent's ``texecRunInContainerStreamed`` delegates to it — gating
    both would double-count one exec, and gating neither is the hole
    this file exists to close.
    """

    def __init__(self):
        super().__init__()
        self.listAdmittedPrimitives = []

    def _fnRecordLiveAdmission(
        self, sContainerId, sPrimitiveName, sCommand="",
    ):
        """Record the admission mode live at one primitive's gate.

        The command text is recorded alongside because a route that
        declares two modes reaches the SAME primitive under both: a
        mode-(b) worker runs the operation's own commands, and the
        mode-(a) workflow save runs a ``mkdir -p`` before its write.
        Asserting "every exec ran under lock-held" would therefore be
        false for a correctly migrated route, and relaxing it to "some
        exec did" would pass for one whose worker never ran.
        """
        admission = mutationAdmission.fadmissionActiveForContainerId(
            sContainerId,
        )
        self.listAdmittedPrimitives.append({
            "sPrimitive": sPrimitiveName,
            "sMode": "" if admission is None else admission.sMode,
            "sCommand": sCommand,
        })

    def fnWriteFile(
        self, sContainerId, sPath, baContent,
        iMode=None, iUid=None, iGid=None,
    ):
        mutationAdmission.fnAssertContainerWriteAdmitted(
            sContainerId, S_PRIMITIVE_WRITE,
        )
        self._fnRecordLiveAdmission(sContainerId, S_PRIMITIVE_WRITE)
        return MockDockerDraft.fnWriteFile(
            self, sContainerId, sPath, baContent,
            iMode=iMode, iUid=iUid, iGid=iGid,
        )

    def fnWriteFileViaTar(
        self, sContainerId, sPath, baContent,
        iMode=None, iUid=None, iGid=None,
    ):
        return self.fnWriteFile(
            sContainerId, sPath, baContent,
            iMode=iMode, iUid=iUid, iGid=iGid,
        )

    def ftResultExecuteCommand(
        self, sContainerId, sCommand, sWorkdir=None,
    ):
        mutationAdmission.fnAssertContainerCommandAdmitted(
            sContainerId, S_PRIMITIVE_EXEC,
        )
        self._fnRecordLiveAdmission(
            sContainerId, S_PRIMITIVE_EXEC, sCommand,
        )
        return MockDockerDraft.ftResultExecuteCommand(
            self, sContainerId, sCommand, sWorkdir,
        )

    def fbaFetchFile(self, sContainerId, sPath, iMaxBytes=None):
        """Fetch a file the way a typed read reaches the container.

        The real adapter marks the audited-read context around its own
        exec, which is the single carve-out that keeps guarding the exec
        primitive from refusing every read implemented with one. Modelled
        here so a migrated route's reads are exercised against the same
        exemption rather than against no gate at all.
        """
        tokenRead = mutationAdmission.ftokenEnterAuditedRead()
        try:
            mutationAdmission.fnAssertContainerCommandAdmitted(
                sContainerId, S_PRIMITIVE_EXEC,
            )
        finally:
            mutationAdmission.fnExitAuditedRead(tokenRead)
        return MockDockerDraft.fbaFetchFile(
            self, sContainerId, sPath, iMaxBytes,
        )


def _tConnectGatedClient(connectionDocker):
    """Return ``(client, docker)`` connected, with the ledger cleared.

    Cleared AFTER connect on purpose: the connect handler legitimately
    mutates under the owner-establishing admission, and leaving its
    entries in the ledger would let a route that reached no primitive at
    all pass an assertion about modes.

    ``raise_server_exceptions=False`` for the same reason the clean
    route's ASGI driver passes ``raise_app_exceptions=False``: a refused
    mutation must be a 500 RESPONSE here, not an exception raised into
    the test body. With the default, a route declaring two modes cannot
    have its carriers proven separately — dropping the SAVE's carrier
    raised out of ``client.post`` before the assertion about the
    CONVERSION's admission could run, so one defect failed both tests
    and neither carrier was isolated. Verified: the same mutant now
    fails only the mode-(a) test.
    """
    with patch.object(
        pipelineServer, "_fconnectionCreateDocker",
        lambda: connectionDocker,
    ):
        app = pipelineServer.fappCreateApplication(
            sWorkspaceRoot="/workspace",
            sTerminalUserArg="testuser",
        )
    client = TestClient(
        app, headers={"X-Session-Token": fsBootstrapCredential(app)},
        raise_server_exceptions=False,
    )
    _fnConnect(client)
    connectionDocker.listAdmittedPrimitives.clear()
    return (client, connectionDocker)


@pytest.fixture
def tclientGated():
    """The gated client over the draft harness's plotless workflow."""
    return _tConnectGatedClient(DockerDoubleThatCallsTheRealGates())


def _fnAssertSelectedRanUnder(
    connectionDocker, fbSelect, sExpectedMode, sDescription,
):
    """Assert the selected gate crossings all happened under one mode.

    Narrower than :func:`_fnAssertEveryPrimitiveRanUnder` on purpose,
    and the reason is structural rather than stylistic. A route that
    declares TWO modes reaches the same primitives under BOTH: its
    mode-(b) worker runs the operation's commands, and the mode-(a)
    workflow save runs its own ``cp``/``mv`` around the write. So "every
    exec ran under lock-held" is FALSE for a correctly migrated route,
    while "some exec did" passes for one whose worker never ran.

    Selecting the crossings that belong to one carrier is also what
    makes the kill-confirms isolate: with the save's carrier removed the
    conversion still runs correctly under the drain, so only the
    mode-(a) test may fail. Nothing here asserts the response status for
    the same reason -- a refused mutation surfaces as a 500, which would
    drag either carrier's defect onto every test of the route.
    """
    listSelected = [
        dictReached
        for dictReached in connectionDocker.listAdmittedPrimitives
        if fbSelect(dictReached)
    ]
    assert listSelected, (
        f"the route made no {sDescription} at all, so this asserts "
        "nothing about how it was admitted; either the request returned "
        "before doing that container work, or a carrier earlier in the "
        "handler refused and the handler never got this far. The full "
        f"ledger is {connectionDocker.listAdmittedPrimitives}"
    )
    listWrongMode = [
        dictReached for dictReached in listSelected
        if dictReached["sMode"] != sExpectedMode
    ]
    assert listWrongMode == [], (
        f"a {sDescription} ran under an admission that is not "
        f"{sExpectedMode!r}: {listWrongMode}. Under "
        f"{mutationAdmission.S_ADMISSION_MODE_REQUEST!r} the route is "
        "still riding the legacy ambient mint; under '' it reached the "
        "primitive with no admission at all."
    )


def _fnAssertExecsNamingRanUnder(
    connectionDocker, sCommandMarker, sExpectedMode,
):
    """Assert every exec whose command names the marker ran under a mode.

    The marker must be specific to the operation under test. A loose one
    was tried in the clean-outputs test and matched the connect
    handler's ``git rev-parse ... 2>/dev/null``, which turned a timing
    assertion into noise; here it would silently fold the workflow
    save's commands into a claim about the worker's.
    """
    _fnAssertSelectedRanUnder(
        connectionDocker,
        lambda dictReached: (
            dictReached["sPrimitive"] == S_PRIMITIVE_EXEC
            and sCommandMarker in dictReached["sCommand"]
        ),
        sExpectedMode,
        f"container command naming {sCommandMarker!r}",
    )


def _fnAssertWritesRanUnder(connectionDocker, sExpectedMode):
    """Assert every container file write ran under one admission mode."""
    _fnAssertSelectedRanUnder(
        connectionDocker,
        lambda dictReached: (
            dictReached["sPrimitive"] == S_PRIMITIVE_WRITE
        ),
        sExpectedMode,
        "container file write",
    )


def _fnAssertEveryPrimitiveRanUnder(connectionDocker, sExpectedMode):
    """Assert the route reached a primitive, all under one mode."""
    listReached = connectionDocker.listAdmittedPrimitives
    assert listReached, (
        "the route reached no container primitive at all, so this "
        "asserts nothing about how it was admitted; the double records "
        "every write and exec, so an empty ledger means the request "
        "returned before doing any container work"
    )
    listWrongMode = [
        dictReached for dictReached in listReached
        if dictReached["sMode"] != sExpectedMode
    ]
    assert listWrongMode == [], (
        f"a migrated route reached a container primitive under an "
        f"admission that is not {sExpectedMode!r}: {listWrongMode}. "
        f"Under {mutationAdmission.S_ADMISSION_MODE_REQUEST!r} the route "
        "is still riding the legacy ambient mint, which means it was "
        "never removed from SET_ROUTES_AWAITING_CARRIER_MODE; under '' "
        "it reached the primitive with no admission at all."
    )


# ---------------------------------------------------------------------
# Group 1 — synchronous single-write, mode (a).
# ---------------------------------------------------------------------

@pytest.mark.falsification
def testTheDraftSaveCommitsThroughTheSynchronousCarrier(tclientGated):
    """PUT /api/draft reaches write AND exec under a mode-(a) admission.

    Both halves matter. The write is the obvious mutation; the ``mkdir
    -p`` that precedes it is an arbitrary exec, which the gate treats as
    mutating because a primitive handed command text cannot know whether
    it creates a directory or empties one. A carrier around only the
    write would leave the mkdir refused.

    Kills: replacing ``_fnCommitDraftWrite``'s
    fdictCommitSynchronousMutation call with a direct call to its effect
    closure.
    """
    client, connectionDocker = tclientGated
    response = client.put(
        f"/api/draft/{S_CONTAINER_ID}/workspace/src/carried.py",
        json={
            "sContent": "x = 1\n",
            "sBaseHash": draftManager.fsHashContent("x = 0\n"),
            "sWorkdir": "stepA",
        },
    )
    assert response.status_code == 200, response.text
    _fnAssertEveryPrimitiveRanUnder(
        connectionDocker, mutationAdmission.S_ADMISSION_MODE_SYNCHRONOUS,
    )
    setPrimitives = {
        dictReached["sPrimitive"]
        for dictReached in connectionDocker.listAdmittedPrimitives
    }
    assert setPrimitives == {S_PRIMITIVE_WRITE, S_PRIMITIVE_EXEC}, (
        "the draft save is a directory creation followed by a file "
        f"write; it reached {sorted(setPrimitives)}"
    )


@pytest.mark.falsification
def testTheDraftDeleteCommitsThroughTheSynchronousCarrier(tclientGated):
    """DELETE /api/draft runs its ``rm`` under a mode-(a) admission.

    Deliberately does NOT save a draft first. Seeding through the sibling
    PUT route made a regression in the draft SAVE's carrier fail this
    test too, so one defect killed two shapes and neither kill isolated
    a guard. ``rm -f`` on an absent draft is the same command through the
    same carrier, and the admission is what is under test.

    Kills: replacing ``_fnCommitDraftDelete``'s carrier call with a
    direct call to its effect closure.
    """
    client, connectionDocker = tclientGated
    sUrl = f"/api/draft/{S_CONTAINER_ID}/workspace/src/doomed.py"
    response = client.delete(sUrl)
    assert response.status_code == 200, response.text
    _fnAssertEveryPrimitiveRanUnder(
        connectionDocker, mutationAdmission.S_ADMISSION_MODE_SYNCHRONOUS,
    )


@pytest.mark.falsification
def testTheSettingsSaveCommitsThroughTheSynchronousCarrier(tclientGated):
    """PUT /api/settings writes project.json under a mode-(a) admission.

    The carrier call predates this migration; what the migration added
    is the declaration that takes the route OFF the ambient mint, so
    this is the assertion that would have caught declaring it without
    the carrier being real.

    Kills: replacing ``_fnCommitSettingsUpdate``'s
    fdictCommitSynchronousMutation call with a direct call to the save.

    Note what does NOT kill it, because it bounds the claim: restoring
    the route to SET_ROUTES_AWAITING_CARRIER_MODE alone changes nothing,
    since ``_fbServeOnAmbientAdmission`` gives a DECLARED route the
    enforced branch whatever the allow-list says. The allow-list grants
    the legacy mint; it does not take it back.
    """
    client, connectionDocker = tclientGated
    response = client.put(
        f"/api/settings/{S_CONTAINER_ID}",
        json={"iNumberOfCores": DICT_WORKFLOW["iNumberOfCores"] + 1},
    )
    assert response.status_code == 200, response.text
    _fnAssertEveryPrimitiveRanUnder(
        connectionDocker, mutationAdmission.S_ADMISSION_MODE_SYNCHRONOUS,
    )


@pytest.mark.falsification
def testTheFileSaveCommitsThroughTheSynchronousCarrier(tclientGated):
    """PUT /api/file writes editor content under a mode-(a) admission.

    Kills: replacing ``_fnCommitFileWrite``'s carrier call with a direct
    call to its effect closure.
    """
    client, connectionDocker = tclientGated
    response = client.put(
        f"/api/file/{S_CONTAINER_ID}/src/edited.py",
        json={"sContent": "y = 2\n", "sBaseHash": ""},
    )
    assert response.status_code == 200, response.text
    _fnAssertEveryPrimitiveRanUnder(
        connectionDocker, mutationAdmission.S_ADMISSION_MODE_SYNCHRONOUS,
    )


def testAnUnmigratedRouteStillReachesThePrimitiveOnTheAmbientMint(
    tclientGated,
):
    """The contrast that makes the four assertions above mean something.

    A route still recorded in ``SET_ROUTES_AWAITING_CARRIER_MODE``
    reaches the same primitives under the legacy ``request`` admission.
    Without this, every assertion above would pass equally well if the
    enforced branch had quietly stopped being enforced and every route
    were admitted by something — the mode is what tells the two apart,
    and this proves the two modes are actually distinguishable here.

    Uses the draft LIST route, which is read-only and awaiting: its
    reads are exempted by the audited-read carve-out rather than by the
    ambient mint, so the assertion is on the ``find`` exec that the
    listing runs as an ordinary command.
    """
    client, connectionDocker = tclientGated
    response = client.get(f"/api/drafts/{S_CONTAINER_ID}")
    assert response.status_code == 200, response.text
    listExecs = [
        dictReached
        for dictReached in connectionDocker.listAdmittedPrimitives
        if dictReached["sPrimitive"] == S_PRIMITIVE_EXEC
    ]
    assert listExecs, "the draft listing reached no exec at all"
    assert all(
        dictReached["sMode"] == mutationAdmission.S_ADMISSION_MODE_REQUEST
        for dictReached in listExecs
    ), (
        "an awaiting route no longer runs on the ambient request "
        f"admission: {listExecs}. Either it was migrated without this "
        "file being updated, or the ambient branch has stopped granting "
        "the legacy mint -- which is phase 4, not phase 2."
    )


# ---------------------------------------------------------------------
# Group 2 — lock-held, mode (b). The named live exploit.
# ---------------------------------------------------------------------

# A workflow whose steps actually declare outputs, so
# ``_flistBuildCleanCommands`` produces commands. The draft harness's
# workflow declares none, and a clean with nothing to delete never
# reaches the carrier at all -- the test would then pass having
# exercised the exploit's absence rather than its fix.
DICT_WORKFLOW_WITH_OUTPUTS = copy.deepcopy(DICT_WORKFLOW)
DICT_WORKFLOW_WITH_OUTPUTS["listSteps"][0]["saOutputDataFiles"] = [
    "results.json",
]
DICT_WORKFLOW_WITH_OUTPUTS["listSteps"][0]["saPlotFiles"] = ["figure.pdf"]
DICT_WORKFLOW_WITH_OUTPUTS["sProjectRepoPath"] = S_PROJECT_REPO

def _fbCommandIsTheOutputClean(sCommand):
    """Return True only for the clean route's own ``rm`` batch.

    Matched on the exact shape ``_flistBuildCleanCommands`` emits --
    ``rm -f <quoted path> 2>/dev/null`` -- and NOT on ``2>/dev/null``
    alone. That looser marker was tried first and matched the connect
    handler's ``git rev-parse ... 2>/dev/null``, so the double blocked
    on connect, the ledger filled before the clean ever started, and
    the test reported the delete had already finished. A marker that
    catches unrelated traffic turns a timing assertion into noise.
    """
    return sCommand.startswith("rm -f ") and "2>/dev/null" in sCommand


class DockerDoubleThatBlocksTheClean(DockerDoubleThatCallsTheRealGates):
    """The gate-faithful double, with the clean's ``rm`` held open.

    The block is a ``threading.Event`` and the worker waiting on it is
    SYNCHRONOUS, because the carrier runs workers with
    ``asyncio.to_thread``. An ``async def`` worker would be called in
    that thread, hand back a coroutine nobody awaits, and return at once
    -- the delete would never block, the drain would drop immediately,
    and a transfer attempted "mid-delete" would find an idle container
    and succeed. The test would then report a refusal it never obtained.
    """

    def __init__(self):
        super().__init__()
        self.eventCleanStarted = threading.Event()
        self.eventCleanMayFinish = threading.Event()
        self.listCleanCommandsRun = []
        self.listCleanAdmissionModes = []

    def ftResultExecuteCommand(
        self, sContainerId, sCommand, sWorkdir=None,
    ):
        if not _fbCommandIsTheOutputClean(sCommand):
            return super().ftResultExecuteCommand(
                sContainerId, sCommand, sWorkdir,
            )
        mutationAdmission.fnAssertContainerCommandAdmitted(
            sContainerId, S_PRIMITIVE_EXEC,
        )
        admission = mutationAdmission.fadmissionActiveForContainerId(
            sContainerId,
        )
        self.listCleanAdmissionModes.append(
            "" if admission is None else admission.sMode,
        )
        self.eventCleanStarted.set()
        self.eventCleanMayFinish.wait(10)
        self.listCleanCommandsRun.append(sCommand)
        return (0, "")

    def fbaFetchFile(self, sContainerId, sPath, iMaxBytes=None):
        if sPath == S_WORKFLOW_PATH:
            return json.dumps(
                DICT_WORKFLOW_WITH_OUTPUTS,
            ).encode("utf-8")
        return super().fbaFetchFile(sContainerId, sPath, iMaxBytes)


def _tBuildAsgiHubWithBlockedClean():
    """Return ``(app, connectionDocker)`` for the in-loop ASGI driver.

    Driven with httpx over ASGI rather than ``TestClient`` because the
    transfer and the in-flight request must share ONE event loop:
    ``TestClient`` runs the app in its own portal thread, and the
    container mutation lock is an ``asyncio.Lock`` bound to the loop
    that created it, so a transfer attempted from a second loop would be
    testing loop plumbing rather than the drain.
    """
    connectionDocker = DockerDoubleThatBlocksTheClean()
    with patch.object(
        pipelineServer, "_fconnectionCreateDocker",
        lambda: connectionDocker,
    ):
        app = pipelineServer.fappCreateApplication(
            sWorkspaceRoot="/workspace",
            sTerminalUserArg="testuser",
        )
    return (app, connectionDocker)


async def _tConnectOverAsgi(clientAsync):
    """Connect to the container and return its lease value."""
    response = await clientAsync.post(
        f"/api/connect/{S_CONTAINER_ID}",
        params={"sWorkflowPath": S_WORKFLOW_PATH},
    )
    assert response.status_code == 200, response.text
    return response.json().get("sLeaseId", "")


def _fsContainerNameFor(app):
    """Return the owner-map key for the connected container."""
    listNames = list(app.state.dictContainerOwners)
    assert len(listNames) == 1, (
        f"expected exactly one owned container, found {listNames}"
    )
    return listNames[0]


@pytest.mark.falsification
@pytest.mark.asyncio
async def testATransferArrivingMidCleanIsRefusedAndNamesTheClean():
    """The exploit, closed: a hand-over cannot land under a live delete.

    Before this migration ``fnCleanOutputs`` ran its ``rm`` on a bare
    ``asyncio.to_thread``. It held no mutation lock and registered no
    durable work, so a transfer arriving mid-delete saw an unlocked,
    idle-looking container, committed the hand-over, and the FORMER
    owner's delete kept running against a workspace that now belonged to
    somebody else. Nothing in the suite could observe that, because
    nothing was there to observe.

    Four assertions, in the order they matter:

    1. the transfer is REFUSED, not queued and not committed;
    2. the refusal NAMES the live operation, because an ``asyncio.Lock``
       knows only that it is held, and "busy" cannot tell a researcher
       whether to wait two seconds or give up;
    3. the worker has NOT committed at the moment of refusal -- without
       this the test would pass equally against a delete that had
       already finished, which is what asserting on a return value
       instead of on STATE buys you;
    4. the refusal is IMMEDIATE, because waiting would spend the
       capability's window on an operation of unknown length.

    Kills: replacing ``_fnDeleteOutputsUnderTheDrain``'s
    fdictRunLockHeldMutation call with the bare ``asyncio.to_thread``
    the route used before the migration.
    """
    app, connectionDocker = _tBuildAsgiHubWithBlockedClean()
    # A route error is a 500 RESPONSE here, not an exception raised
    # into this test body. What is under test is the transfer's
    # behaviour while a delete is live; the same route also commits a
    # workflow save through a second carrier, and letting that carrier's
    # refusal escape through ``await taskClean`` made a defect in it
    # fail this test as well -- a kill that lands on two shapes isolates
    # neither of them.
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=app, raise_app_exceptions=False,
        ),
        base_url="http://hub",
        headers={"X-Session-Token": fsBootstrapCredential(app)},
    ) as clientAsync:
        sLease = await _tConnectOverAsgi(clientAsync)
        clientAsync.headers["X-Vaibify-Lease"] = sLease
        sName = _fsContainerNameFor(app)

        taskClean = asyncio.ensure_future(
            clientAsync.post(f"/api/pipeline/{S_CONTAINER_ID}/clean"),
        )
        await asyncio.to_thread(
            connectionDocker.eventCleanStarted.wait, 10,
        )
        assert connectionDocker.listCleanCommandsRun == [], (
            "the delete finished before the transfer was attempted, so "
            "the container was not busy when it mattered and this test "
            "would prove nothing"
        )

        sCapability = browserSession.fsMintTransferCapability(
            app.state.dictBrowserSessions, sName,
            app.state.dictContainerOwners[sName].iOwnerGeneration,
        )
        fBefore = time.monotonic()
        sOutcome, dictPayload = await sessionLifecycle.ftTransferOwnership(
            app.state, sCapability,
        )
        fElapsed = time.monotonic() - fBefore

        assert sOutcome == sessionLifecycle.S_TRANSFER_BUSY_RETRY, (
            f"a transfer committed over a live delete: {dictPayload}"
        )
        assert "clean-outputs" in dictPayload["sMessage"], (
            "the refusal must NAME what holds the container, and that "
            "name comes from the operation the lock HOLDER registered: "
            f"{dictPayload}"
        )
        assert connectionDocker.listCleanCommandsRun == [], (
            "the delete had already committed when the transfer was "
            "refused, so the refusal proves nothing about work in flight"
        )
        assert fElapsed < 2.0, (
            f"the transfer waited {fElapsed:.1f}s on the busy container "
            "instead of refusing at once"
        )
        assert app.state.dictContainerOwners[sName].sLeaseId == sLease, (
            "the lease rotated despite the refusal, so the transfer "
            "partly committed"
        )

        # Release, then retry: the refusal must be a "not yet", not a
        # dead end, and the capability stays ARMED precisely so the
        # researcher reuses it. Asserted on the DELETE finishing rather
        # than on the route's status code, because the same route also
        # commits a workflow save through a second carrier -- keying
        # this on the response made a defect in THAT carrier fail this
        # test too, and a kill that lands on two shapes isolates
        # neither.
        connectionDocker.eventCleanMayFinish.set()
        await taskClean
        assert len(connectionDocker.listCleanCommandsRun) == 1

        sOutcomeRetry, dictRetry = await (
            sessionLifecycle.ftTransferOwnership(app.state, sCapability)
        )
        assert sOutcomeRetry == sessionLifecycle.S_TRANSFER_TRANSFERRED, (
            "the transfer stayed refused after the delete settled, so "
            f"the busy refusal was a dead end, not a 'not yet': "
            f"{dictRetry}"
        )
        assert app.state.dictContainerOwners[sName].sLeaseId != sLease, (
            "ownership moved but the lease did not rotate, so the "
            "departed session still holds a credential that works"
        )


@pytest.mark.falsification
@pytest.mark.asyncio
async def testTheCleanDeletesUnderTheDrainAndSavesSynchronously():
    """The clean's two mutations run under the two modes it declares.

    ``fnCleanOutputs`` declares BOTH ``mode-b-lock-held`` and
    ``mode-a-synchronous``, which is a real shape rather than
    indecision: the delete crosses a worker-thread ``await`` and needs
    the drain held for the worker's whole life, while the workflow save
    recording the clean is one synchronous write. Asserting a single
    mode for the whole route would have let either carrier be dropped.

    Kills: replacing ``fnCleanOutputs``'s fnCommitWorkflowSave call with
    a direct ``dictCtx["save"]``.
    """
    app, connectionDocker = _tBuildAsgiHubWithBlockedClean()
    connectionDocker.eventCleanMayFinish.set()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://hub",
        headers={"X-Session-Token": fsBootstrapCredential(app)},
    ) as clientAsync:
        clientAsync.headers["X-Vaibify-Lease"] = (
            await _tConnectOverAsgi(clientAsync)
        )
        connectionDocker.listAdmittedPrimitives.clear()
        response = await clientAsync.post(
            f"/api/pipeline/{S_CONTAINER_ID}/clean",
        )
        assert response.status_code == 200, response.text

    assert connectionDocker.listCleanAdmissionModes == [
        mutationAdmission.S_ADMISSION_MODE_LOCK_HELD,
    ], (
        "the output delete did not run under the lock-held carrier: "
        f"{connectionDocker.listCleanAdmissionModes}"
    )
    setWriteModes = {
        dictReached["sMode"]
        for dictReached in connectionDocker.listAdmittedPrimitives
        if dictReached["sPrimitive"] == S_PRIMITIVE_WRITE
    }
    assert setWriteModes == {
        mutationAdmission.S_ADMISSION_MODE_SYNCHRONOUS,
    }, (
        "the workflow save recording the clean did not commit "
        f"synchronously through the carrier: {setWriteModes}"
    )


class DockerDoubleServingAWorkflowWithPlots(
    DockerDoubleThatCallsTheRealGates,
):
    """The gate-faithful double, over a workflow that declares a plot.

    The draft harness's workflow declares no plot files, and
    ``standardize-plots`` refuses a step with none -- so against that
    workflow the route would 400 before reaching any container work and
    every assertion below would be about a refusal rather than about a
    conversion.
    """

    def fbaFetchFile(self, sContainerId, sPath, iMaxBytes=None):
        if sPath == S_WORKFLOW_PATH:
            return json.dumps(
                DICT_WORKFLOW_WITH_OUTPUTS,
            ).encode("utf-8")
        return super().fbaFetchFile(sContainerId, sPath, iMaxBytes)


@pytest.fixture
def tclientGatedWithPlots():
    """The gated client over a workflow whose first step has a plot."""
    return _tConnectGatedClient(DockerDoubleServingAWorkflowWithPlots())


# The stem the conversion writes and the verification checks --
# ``figure.pdf`` becomes ``figure_standard.png``. Specific to this
# route's own commands: the workflow save that follows names
# ``state.json``, so a claim about the worker's admission cannot
# silently absorb the save's.
S_STANDARD_PLOT_STEM = "figure_standard"


def _fresponsePostStandardizePlots(client):
    """Drive the plot-standardization route for the first step."""
    return client.post(
        f"/api/steps/{S_CONTAINER_ID}/0/standardize-plots",
        json={"sFileName": ""},
    )


@pytest.mark.falsification
def testThePlotConversionRunsUnderTheDrain(tclientGatedWithPlots):
    """POST .../standardize-plots converts under a mode-(b) admission.

    The conversion is a batch of ``convert``/``gs`` invocations that can
    run for many seconds against files the researcher is about to
    accept as the step's standards, and it used to run on a bare
    ``asyncio.to_thread`` -- holding no mutation lock, registering no
    operation, so a hand-over arriving mid-conversion saw an idle
    container, committed, and the FORMER owner's converter kept writing
    PNGs into a workspace that had changed hands.

    The ``test -f`` verification execs are asserted here too, and that
    is the point of running them inside the same worker: a report that a
    standard exists is only worth having if nothing could have replaced
    it between the write and the check.

    Kills: passing the plot conversion to
    ``commitCarrier.fdictCommitSynchronousMutation`` instead of
    ``fdictRunLockHeldMutation`` -- the exec then runs under
    ``mode-a-synchronous``, which is a real admission, so "it did not
    raise" would not catch it.
    """
    client, connectionDocker = tclientGatedWithPlots
    _fresponsePostStandardizePlots(client)
    _fnAssertExecsNamingRanUnder(
        connectionDocker, S_STANDARD_PLOT_STEM,
        mutationAdmission.S_ADMISSION_MODE_LOCK_HELD,
    )


@pytest.mark.falsification
def testThePlotStandardizationSavesSynchronously(tclientGatedWithPlots):
    """POST .../standardize-plots records the run under mode (a).

    The second of the route's two declared modes. Split from the
    conversion assertion rather than folded into it because a single
    test covering both is killed by a defect in either carrier, which
    proves neither: with the save's carrier removed the conversion still
    runs correctly under the drain, and only this test fails.

    Kills: replacing ``fnStandardizePlots``'s ``fnCommitWorkflowSave``
    call with a direct ``dictCtx["save"]``.
    """
    client, connectionDocker = tclientGatedWithPlots
    _fresponsePostStandardizePlots(client)
    _fnAssertWritesRanUnder(
        connectionDocker, mutationAdmission.S_ADMISSION_MODE_SYNCHRONOUS,
    )


@pytest.mark.falsification
@pytest.mark.parametrize("sAction", ["ignore", "untrack"])
def testTheRepoSidecarRewriteRunsUnderTheDrain(tclientGated, sAction):
    """POST /api/repos/.../{ignore,untrack} rewrites under mode (b).

    Both are a read-modify-write of the tracked-repos sidecar across two
    container round-trips. They used to run on a bare
    ``asyncio.to_thread``, so a hand-over landing between the read and
    the write let the FORMER owner's write clobber the successor's --
    and silently, because the sidecar would simply be wrong about which
    repositories the researcher tracks rather than failing.

    Parametrized rather than duplicated because the two routes share one
    helper and one carrier call: they are the same shape, and two
    hand-written copies would drift. The mutant below is in that shared
    helper, so it fails both parametrizations -- which is one defect
    landing on one shape, not on two.

    Kills: replacing ``_fnRewriteTheSidecarUnderTheDrain``'s
    fdictRunLockHeldMutation call with a bare
    ``asyncio.to_thread(fnRewriteTheSidecar, None)``.
    """
    client, connectionDocker = tclientGated
    client.post(f"/api/repos/{S_CONTAINER_ID}/somerepo/{sAction}")
    _fnAssertWritesRanUnder(
        connectionDocker, mutationAdmission.S_ADMISSION_MODE_LOCK_HELD,
    )


@pytest.mark.falsification
def testTheRepoTrackRunsUnderTheDrain(tclientGated):
    """POST /api/repos/.../track resolves and records under mode (b).

    Unlike ignore and untrack, tracking first READS the repository's
    git status to decide whether it exists, then writes the sidecar. On
    a bare ``asyncio.to_thread`` that read-then-decide-then-write chain
    spanned a hand-over exactly as the others did.

    Asserted on the EXEC and not the write, because against this double
    the repository is absent, so the route refuses with a 404 before
    writing anything. That is the more interesting half: it proves the
    DECIDING read happened inside the drain, which is what stops a
    hand-over landing between "does it exist" and "record it".

    Kills: passing ``_fobjRunRepoWorkerUnderTheDrain``'s worker to
    ``fdictCommitSynchronousMutation`` instead of
    ``fdictRunLockHeldMutation``.

    Deliberately NOT "delete the carrier call", which was tried first:
    that also fails ``testAnExpectedRefusalLeavesTheContainerUsable``,
    since without an admission the route 500s before its refusal can
    be observed. One mutant on two tests proves neither. Swapping the
    MODE leaves the refusal path intact and lands on this test alone.
    """
    client, connectionDocker = tclientGated
    client.post(f"/api/repos/{S_CONTAINER_ID}/somerepo/track")
    _fnAssertExecsNamingRanUnder(
        connectionDocker, "/workspace/somerepo",
        mutationAdmission.S_ADMISSION_MODE_LOCK_HELD,
    )


@pytest.mark.falsification
def testAnExpectedRefusalLeavesTheContainerUsable(tclientGated):
    """A 404 from inside a carrier worker must not quarantine anything.

    THE hazard of migrating a route whose refusals live below the
    container boundary. A carrier worker that RAISES is settled through
    the failure path, which marks its journal record NEEDS
    RECONCILIATION -- correct for an effect whose state nobody knows,
    and catastrophic for "no such repository": an ordinary 404 would
    take the researcher's container out of service until they ran
    ``vaibify reconcile``, for asking about a directory that is not
    there.

    So the assertion is on the JOURNAL, not on the status code. A test
    that only checked for 404 would pass just as happily against a
    handler that quarantined the container on its way to returning one,
    which is precisely the bug.

    ``init`` is the driver rather than ``track`` because its refusals
    are raised by helpers BELOW the worker's entry, which is the shape
    the capture has to survive. Against this double the one that fires
    is the 409 from ``_fbDirectoryIsGitRepo`` -- the double answers
    every ``test -d`` affirmatively, so the directory reads as an
    existing git repository. Any 4xx exercises the same path; what
    matters is that it was raised inside the carrier's worker.

    Kills: removing the ``status_code >= 500`` branch from
    ``_fdictCarryARefusalBackInsteadOfRaising`` so every HTTPException
    is re-raised inside the worker.
    """
    from vaibify.config import operationJournal

    client, connectionDocker = tclientGated
    response = client.post(
        f"/api/repos/{S_CONTAINER_ID}/init",
        json={"sDirectory": "AbsentProject", "bCreateIfMissing": False},
    )
    assert 400 <= response.status_code < 500, (
        f"expected a declined refusal, got {response.status_code}: "
        f"{response.text}"
    )

    dictResolution = operationJournal.fdictResolveContainerJournal(
        S_CONTAINER_NAME,
    )
    assert dictResolution["sResolution"] != (
        operationJournal.S_RESOLUTION_QUARANTINED
    ), (
        "an expected 404 quarantined the container: "
        f"{dictResolution}. The researcher now has to run 'vaibify "
        "reconcile' because they asked about a directory that does not "
        "exist."
    )
