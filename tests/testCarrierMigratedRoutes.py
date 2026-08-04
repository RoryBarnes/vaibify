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

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch

from tests.testDraftRoutes import (
    DICT_WORKFLOW,
    MockDockerDraft,
    S_CONTAINER_ID,
    S_WORKFLOW_PATH,
    _fnConnect,
)
from tests.sessionTokenTestHelper import fsBootstrapCredential
from vaibify.config import mutationAdmission
from vaibify.gui import draftManager, pipelineServer


S_PRIMITIVE_WRITE = "fnWriteFileViaTar"
S_PRIMITIVE_EXEC = "texecRunInContainerStreamed"


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

    def _fnRecordLiveAdmission(self, sContainerId, sPrimitiveName):
        """Record the admission mode live at one primitive's gate."""
        admission = mutationAdmission.fadmissionActiveForContainerId(
            sContainerId,
        )
        self.listAdmittedPrimitives.append({
            "sPrimitive": sPrimitiveName,
            "sMode": "" if admission is None else admission.sMode,
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
        self._fnRecordLiveAdmission(sContainerId, S_PRIMITIVE_EXEC)
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


@pytest.fixture
def tclientGated():
    """Return ``(client, docker)`` connected, with the ledger cleared.

    Cleared AFTER connect on purpose: the connect handler legitimately
    mutates under the owner-establishing admission, and leaving its
    entries in the ledger would let a route that reached no primitive at
    all pass an assertion about modes.
    """
    connectionDocker = DockerDoubleThatCallsTheRealGates()
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
    )
    _fnConnect(client)
    connectionDocker.listAdmittedPrimitives.clear()
    return (client, connectionDocker)


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
