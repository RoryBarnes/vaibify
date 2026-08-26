"""The routes phase 2 migrated, proven at the gate rather than asserted.

WHY THIS FILE EXISTS AT ALL
---------------------------

A migrated route is served on the enforced branch, which mints NO
admission, so its handler must open one through a carrier around each
logical mutation. Forgetting one raises ``MutationNotAdmittedError`` at
the primitive, and **that refusal is the migration's only proof**.

The refusal lives in :mod:`vaibify.config.mutationAdmission`, which the
REAL ``DockerConnection`` calls from ``fnWriteFileViaTar`` and
``ftRunInContainerStreamed``. Every route test in this suite drives a
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
import contextlib
import copy
import hashlib
import json
import logging
import os
import posixpath
import shlex
import threading
import time
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from unittest.mock import patch

from vaibify.gui import commitCarrier
from vaibify.reproducibility import repoFiles
from vaibify.reproducibility.aiDeclarationStep import (
    S_AI_DECLARATION_STEP_KIND,
)
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
from vaibify.gui.routes import falsificationRoutes, reproducibilityRoutes
from vaibify.gui import (
    browserSession,
    draftManager,
    pipelineServer,
    pipelineState,
    sessionLifecycle,
)


S_PRIMITIVE_WRITE = "fnWriteFileViaTar"
S_PRIMITIVE_EXEC = "ftRunInContainerStreamed"

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
    parent's ``ftRunInContainerStreamed`` delegates to it — gating
    both would double-count one exec, and gating neither is the hole
    this file exists to close.
    """

    def __init__(self):
        super().__init__()
        self.listAdmittedPrimitives = []
        self.listTypedPathProbes = []
        # Paths the existence batch answers True for. Default absent
        # stays the harsher direction for the badge tests; push tests
        # seed the file their route selects, because the push route's
        # pre-flight (2026-08-17) refuses a selection that does not
        # exist before any git subprocess runs.
        self.setExistingPaths = set()

    def _fnRecordLiveAdmission(
        self, sContainerId, sPrimitiveName, sCommand="", sPath="",
    ):
        """Record the admission mode live at one primitive's gate.

        The command text is recorded alongside because a route that
        declares two modes reaches the SAME primitive under both: a
        mode-(b) worker runs the operation's own commands, and the
        mode-(a) workflow save runs a ``mkdir -p`` before its write.
        Asserting "every exec ran under lock-held" would therefore be
        false for a correctly migrated route, and relaxing it to "some
        exec did" would pass for one whose worker never ran.

        The write's TARGET is recorded for the same reason one level
        further in. A two-mode route whose carriers both end in a WRITE
        -- arXiv configure saves project.json under mode (a) and
        rewrites syncStatus.json under mode (b) -- cannot be separated
        by command text, because a write has none. Without the path,
        either missing carrier would fail both of that route's tests
        and neither kill would isolate a guard.
        """
        admission = mutationAdmission.fadmissionActiveForContainerId(
            sContainerId,
        )
        self.listAdmittedPrimitives.append({
            "sPrimitive": sPrimitiveName,
            "sMode": "" if admission is None else admission.sMode,
            "sCommand": sCommand,
            "sPath": sPath,
        })

    def fnWriteFile(
        self, sContainerId, sPath, baContent,
        iMode=None, iUid=None, iGid=None,
    ):
        mutationAdmission.fnAssertContainerWriteAdmitted(
            sContainerId, S_PRIMITIVE_WRITE,
        )
        self._fnRecordLiveAdmission(
            sContainerId, S_PRIMITIVE_WRITE, sPath=sPath,
        )
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

    def flistDirectoryEntries(self, sContainerId, sPath):
        """List a directory the way the real typed-read adapter does.

        Raises ``FileNotFoundError`` for anything that is not a
        directory, which is the answer the real adapter gives and the
        one ``_fnRefuseDirectorySource`` reads as "this is a file".
        Gated through the audited-read carve-out, like every other
        typed read here, so a caller that reached it OUTSIDE the
        adapter would still be refused.
        """
        tokenRead = mutationAdmission.ftokenEnterAuditedRead()
        try:
            mutationAdmission.fnAssertContainerCommandAdmitted(
                sContainerId, S_PRIMITIVE_EXEC,
            )
        finally:
            mutationAdmission.fnExitAuditedRead(tokenRead)
        self.listTypedPathProbes.append(sPath)
        raise FileNotFoundError(f"Not a directory: {sPath}")

    def fiterStreamFile(
        self, sContainerId, sPath, iChunkSizeBytes=1048576,
    ):
        """Stream a file out the way ``get_archive`` does: ungated.

        Deliberately NOT gated, because the real one is not: it reads
        through the Docker SDK's ``get_archive`` rather than through
        exec, so it never reaches the command gate. Modelling it as
        gated would invent a refusal production does not have, and
        would make the pull route look mutation-capable when the whole
        point of streaming instead of ``docker cp`` was that it cannot
        travel the other way.
        """
        yield MockDockerDraft.fbaFetchFile(self, sContainerId, sPath)

    def fbContainerPathIsFile(self, sContainerId, sPath):
        """Probe a path the way the real typed-read adapter does.

        Recorded in a SEPARATE ledger from the gated primitives, which
        is what lets a ``typed-read`` route be asserted without
        vacuity: the gated ledger must be EMPTY (it reached no
        mutation-capable primitive) while this one must be NON-empty
        (it did real container work rather than returning early).
        Folding both into one ledger would force a choice between the
        two halves.
        """
        tokenRead = mutationAdmission.ftokenEnterAuditedRead()
        try:
            mutationAdmission.fnAssertContainerCommandAdmitted(
                sContainerId, S_PRIMITIVE_EXEC,
            )
        finally:
            mutationAdmission.fnExitAuditedRead(tokenRead)
        self.listTypedPathProbes.append(sPath)
        return False

    def fdictStatPathMtimes(self, sContainerId, listPaths):
        """Stat a batch the way the real typed-read adapter does.

        Enters the audited read and asserts the command gate exactly
        as the real one does, then records into the typed-probe ledger
        rather than the admission ledger -- because a typed read is
        expected to reach the primitive with NO admission open, and
        recording it as an admitted primitive would make every
        assertion about this route's admissions answer for it.
        """
        tokenRead = mutationAdmission.ftokenEnterAuditedRead()
        try:
            mutationAdmission.fnAssertContainerCommandAdmitted(
                sContainerId, S_PRIMITIVE_EXEC,
            )
        finally:
            mutationAdmission.fnExitAuditedRead(tokenRead)
        self.listTypedPathProbes.extend(listPaths)
        return {}

    def fsHashContainerFileSha256(self, sContainerId, sPath):
        """Hash a file the way the real typed-read adapter does."""
        tokenRead = mutationAdmission.ftokenEnterAuditedRead()
        try:
            mutationAdmission.fnAssertContainerCommandAdmitted(
                sContainerId, S_PRIMITIVE_EXEC,
            )
        finally:
            mutationAdmission.fnExitAuditedRead(tokenRead)
        self.listTypedPathProbes.append(sPath)
        return ""

    def flistContainerPathsExist(self, sContainerId, listPaths):
        """Answer the existence batch the way the real adapter does.

        The badge refresh asks it so a file that is not on disk cannot
        be badged "in sync with remote". A path answers absent unless
        a fixture seeded it into ``setExistingPaths`` — absent is the
        harsher direction: a route that stopped threading the answer
        through would still be reported by its own tests, not by this
        one.
        """
        tokenRead = mutationAdmission.ftokenEnterAuditedRead()
        try:
            mutationAdmission.fnAssertContainerCommandAdmitted(
                sContainerId, S_PRIMITIVE_EXEC,
            )
        finally:
            mutationAdmission.fnExitAuditedRead(tokenRead)
        self.listTypedPathProbes.extend(listPaths)
        return [sPath in self.setExistingPaths for sPath in listPaths]

    def fbContainerPathIsDirectory(self, sContainerId, sPath):
        """Probe a directory the way the real typed-read adapter does.

        The sibling of ``fbContainerPathIsFile`` and gated the same
        way. It exists because ``repoFiles.fbIsDir`` calls it and the
        real connection answers it; a double that raised
        ``AttributeError`` here would make a route's absence check look
        like a route bug.
        """
        tokenRead = mutationAdmission.ftokenEnterAuditedRead()
        try:
            mutationAdmission.fnAssertContainerCommandAdmitted(
                sContainerId, S_PRIMITIVE_EXEC,
            )
        finally:
            mutationAdmission.fnExitAuditedRead(tokenRead)
        self.listTypedPathProbes.append(sPath)
        return False


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


@pytest.mark.falsification
def testTheFileUploadCommitsThroughTheSynchronousCarrier(tclientGated):
    """POST /api/files/upload lands its bytes under a mode-(a) admission.

    A separate shape from the editor save above even though both end in
    ``fnWriteFile``: the upload carries its own carrier call, so a
    migration that declared the route and reused nothing would reach the
    write primitive with no admission at all.

    Kills: replacing ``_fnCommitUploadedFile``'s
    fdictCommitSynchronousMutation call with a direct call to its effect
    closure.
    """
    client, connectionDocker = tclientGated
    response = client.post(
        f"/api/files/{S_CONTAINER_ID}/upload",
        json={
            "sFilename": "observations.csv",
            "sDestination": posixpath.join(S_PROJECT_REPO, "data"),
            "sContentBase64": "YSxiCjEsMgo=",
        },
    )
    assert response.status_code == 200, response.text
    _fnAssertWritesRanUnder(
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

    Kills: replacing ``_fdictDeleteOutputsUnderTheDrain``'s
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

    Kills: replacing ``fnCleanOutputs``'s fdictCommitWorkflowSave call with
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

    Kills: replacing ``fdictStandardizePlots``'s ``fdictCommitWorkflowSave``
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

    Kills: replacing ``_fdictRewriteTheSidecarUnderTheDrain``'s
    fdictRunLockHeldMutation call with a bare
    ``asyncio.to_thread(fnRewriteTheSidecar, None)``.
    """
    client, connectionDocker = tclientGated
    client.post(f"/api/repos/{S_CONTAINER_ID}/somerepo/{sAction}")
    _fnAssertWritesRanUnder(
        connectionDocker, mutationAdmission.S_ADMISSION_MODE_LOCK_HELD,
    )


@pytest.mark.falsification
def testThePlotStandardsCheckReachesNoMutatingPrimitive(
    tclientGatedWithPlots,
):
    """GET .../plot-standards is a read, and now proves it is one.

    It built ``test -f <path> && echo Y || echo N`` for every plot and
    ran the batch through the general exec primitive. That is a read by
    any reading, and the gate cannot know it: command text carries no
    such distinction, so the route was mutation-capable by
    construction and could not honestly declare ``typed-read``. It is
    now one typed read per plot.

    TWO assertions, because either alone is satisfiable by a defect.
    The gated ledger must be EMPTY -- no mutation-capable primitive was
    reached -- and the typed-probe ledger must be NON-EMPTY, or a route
    that returned early without touching the container at all would
    pass the first assertion perfectly.

    Kills: restoring the batched ``test -f … && echo Y`` command
    through ``ftResultExecuteCommand``.
    """
    client, connectionDocker = tclientGatedWithPlots
    response = client.get(
        f"/api/steps/{S_CONTAINER_ID}/0/plot-standards",
    )
    assert response.status_code == 200, response.text
    assert connectionDocker.listAdmittedPrimitives == [], (
        "a route declared typed-read reached a mutation-capable "
        "primitive: "
        f"{connectionDocker.listAdmittedPrimitives}"
    )
    assert connectionDocker.listTypedPathProbes, (
        "the route reached no typed read either, so it did no "
        "container work at all and the empty gated ledger above "
        "asserts nothing"
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

    Kills: passing ``_fgenericRunRepoWorkerUnderTheDrain``'s worker to
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


# ---------------------------------------------------------------------
# Group 2, continued — the test-execution routes, and the level probe
# hidden inside them.
# ---------------------------------------------------------------------

# The three test-execution routes probe ``fiProofLevel`` before and after
# running. On an ordinary fixture that probe clears without touching a
# general exec, so a migration that left it OUTSIDE the carrier would
# pass every test in this suite and then be REFUSED in the field — for
# exactly the researchers furthest along the reproducibility ladder.
# The chain that makes it reach one is precise, and every conjunct
# below is required:
#
#   _fbComputeLevel2 -> fbWorkflowFullySyncedWithArxiv
#     -> _fbArxivTarballMatchesPushManifest   (needs a pushed-figure list)
#     -> _fbArxivHashesCoverPushList
#     -> _fdictLiveHashesOrNone
#     -> filesRepo.fdictHashFiles             <- ONE general exec
#
# and ``fbAtLeastLevel3`` evaluates L2 first, so L3 inherits it. Stop
# short of any conjunct — no attested declaration, no declared models,
# no personal layer, no Overleaf-recorded commit, no pushed figure —
# and L2 fails EARLY, the hash is never reached, and the false green
# returns. Established by instrumenting the adapter and watching the
# call happen, not by reading the gate.

_BA_PUBLISHED_FIGURE = b"%PDF-1.4 canonical figure bytes\n"
S_PUBLISHED_FIGURE_SHA = hashlib.sha256(
    _BA_PUBLISHED_FIGURE,
).hexdigest()
S_PUBLISHED_FIGURE_RELPATH = "A/plot.pdf"
S_ARXIV_IDENTIFIER = "2401.00001"
S_OVERLEAF_PUSH_COMMIT = "commitabc"


def _fsFreshIsoTimestamp():
    """Return an ISO-8601 UTC timestamp one hour old.

    One hour rather than "now" because the sync caches are checked for
    staleness, and a timestamp in the future would be as suspicious to
    that check as one a week old.
    """
    dtThen = datetime.now(timezone.utc) - timedelta(hours=1)
    return dtThen.strftime("%Y-%m-%dT%H:%M:%SZ")


def _fdictAllGreenSyncCache():
    """Return the per-service verify cache both L2 sync gates demand.

    ``listComparedPaths`` is required since 2026-08-26: the L2 gate
    asks whether the paths LEVEL 2 owns matched, so a cache that does
    not say what it compared cannot support the claim and reads as
    unproven. A step-scoped path is used because the partition puts
    anything outside the reproducibility envelope in Level 2 — an
    envelope path here would leave the L2 scope empty and the gate
    correctly closed.
    """
    listCompared = ["step1/output.json"]
    return {
        "github": {
            "sService": "github",
            "sLastVerified": _fsFreshIsoTimestamp(),
            "iTotalFiles": 1, "iMatching": 1, "listDiverged": [],
            "listComparedPaths": list(listCompared),
            "sCommittedShaVerified": "abc123",
        },
        "zenodo": {
            "sService": "zenodo",
            "sLastVerified": _fsFreshIsoTimestamp(),
            "iTotalFiles": 1, "iMatching": 1, "listDiverged": [],
            "listComparedPaths": list(listCompared),
            "sZenodoDoi": "10.1000/example",
            "sEndpointVerified": "sandbox",
        },
    }


def _fdictGreenStepNamed(sName):
    """Return a step every L1 per-step criterion accepts."""
    return {
        "sName": sName, "sDirectory": sName,
        "bPlotOnly": False, "bRunEnabled": True, "bInteractive": False,
        "saDataCommands": [], "saPlotCommands": [],
        "dictRunStats": {},
        "saOutputDataFiles": [sName + "/data.csv"],
        "saPlotFiles": [sName + "/plot.pdf"],
        "bNoInputData": True,
        "saTestCommands": ["pytest -q"],
        "dictTests": {
            "dictIntegrity": {
                "saCommands": ["pytest -q"], "sFilePath": "",
            },
        },
        "dictVerification": {
            "sUser": "passed", "sUnitTest": "passed",
            "sIntegrity": "passed", "sQualitative": "passed",
            "sQuantitative": "passed",
        },
    }


def _fdictPublishedWorkflow():
    """Return a workflow that reaches L2, arXiv conjunct included.

    Built to the same recipe ``tests/testLevelGateL2Arxiv.py`` pins for
    the gate itself, so this fixture and the gate's own tests fail
    together if the criteria move, rather than this one quietly
    degrading into a workflow that no longer reaches the hash.
    """
    dictDeclaration = _fdictGreenStepNamed("Decl")
    dictDeclaration["sStepKind"] = S_AI_DECLARATION_STEP_KIND
    return {
        "sWorkflowName": "Published", "sPlotDirectory": "Plot",
        "sFigureType": "pdf", "iNumberOfCores": 4,
        "sProjectRepoPath": S_PROJECT_REPO,
        "sPath": S_WORKFLOW_PATH,
        "listSteps": [_fdictGreenStepNamed("A"), dictDeclaration],
        "dictRemotes": {
            "github": {
                "sOwner": "u", "sRepo": "r", "sBranch": "main",
                "sCommittedSha": "abc123",
            },
            "zenodo": {
                "sRecordId": "1", "sService": "sandbox",
                "sDoi": "10.1000/example",
            },
            "overleaf": {
                "sProjectId": "ol1234",
                "sLastPushCommit": S_OVERLEAF_PUSH_COMMIT,
            },
            "arxiv": {
                "sArxivId": S_ARXIV_IDENTIFIER, "sArxivVersion": "v1",
            },
        },
        "dictAiProvenance": {
            "listDeclaredModels": [{
                "sVendor": "ExampleVendor",
                "sModelId": "example-model-1",
                "sUseStartDate": "2026-01-01",
                "sUseEndDate": "2026-02-01",
            }],
            "dictPersonalLayer": {"sStatus": "none"},
        },
    }


class DockerDoubleServingAPublishedWorkflow(
    DockerDoubleThatCallsTheRealGates,
):
    """The gate-faithful double over a container that has REACHED L2.

    Only two container files are needed, which was measured rather than
    guessed: the level chain reads ``.vaibify/syncStatus.json`` and
    ``.vaibify/overleafPushManifest.json``, probes ``MANIFEST.sha256``
    (absent is fine), and reaches the container exactly ONCE more — the
    hash. Everything else it needs is in the workflow document.

    The embedded repo script is answered with the figure's real sha so
    the arXiv conjunct CLOSES and the workflow is genuinely L2. A double
    that returned nothing would still reach the hash and the tests below
    would still pass, but the fixture would then be quietly asserting
    against a workflow stuck below L2, and the next reader would believe
    something untrue about it.
    """

    def __init__(self):
        super().__init__()
        self._dictFiles[
            posixpath.join(S_PROJECT_REPO, ".vaibify/syncStatus.json")
        ] = json.dumps(_fdictAllGreenSyncCache()).encode("utf-8")
        self._dictFiles[
            posixpath.join(
                S_PROJECT_REPO, ".vaibify/overleafPushManifest.json",
            )
        ] = json.dumps({
            S_OVERLEAF_PUSH_COMMIT: {
                S_PUBLISHED_FIGURE_RELPATH: "figures/plot.pdf",
            },
        }).encode("utf-8")

    def fbaFetchFile(self, sContainerId, sPath, iMaxBytes=None):
        if sPath == S_WORKFLOW_PATH:
            return json.dumps(_fdictPublishedWorkflow()).encode("utf-8")
        return super().fbaFetchFile(sContainerId, sPath, iMaxBytes)

    def fbContainerPathIsFile(self, sContainerId, sPath):
        """Answer the typed probe from the in-memory tree.

        The parent answers ``False`` for everything, which is right for
        its plotless fixture and wrong here: the sync-cache probe must
        say the cache exists, or the L2 gates stop before the arXiv
        conjunct and the hash is never reached.
        """
        super().fbContainerPathIsFile(sContainerId, sPath)
        return sPath in self._dictFiles

    def fbContainerPathIsDirectory(self, sContainerId, sPath):
        """The ``test -d`` half of the typed-read pair.

        ``ContainerRepoFiles`` requires it and no draft-harness double
        defines it; a bare ``MagicMock`` here would answer truthy for
        every path, which is the polarity trap
        ``tests/dockerConnectionDoubles.py`` records.
        """
        tokenRead = mutationAdmission.ftokenEnterAuditedRead()
        try:
            mutationAdmission.fnAssertContainerCommandAdmitted(
                sContainerId, S_PRIMITIVE_EXEC,
            )
        finally:
            mutationAdmission.fnExitAuditedRead(tokenRead)
        return sPath in self._setDirs

    def ftResultExecuteCommand(
        self, sContainerId, sCommand, sWorkdir=None,
    ):
        if sCommand.startswith("python3 -c "):
            mutationAdmission.fnAssertContainerCommandAdmitted(
                sContainerId, S_PRIMITIVE_EXEC,
            )
            self._fnRecordLiveAdmission(
                sContainerId, S_PRIMITIVE_EXEC, sCommand,
            )
            return (0, json.dumps({
                S_PUBLISHED_FIGURE_RELPATH: {
                    "sSha256": S_PUBLISHED_FIGURE_SHA,
                },
            }))
        return super().ftResultExecuteCommand(
            sContainerId, sCommand, sWorkdir,
        )


@contextlib.contextmanager
def _tlistRecordEveryRepoHash(connectionDocker):
    """Record the live admission at every ``fdictHashFiles`` call.

    Instrumenting the ADAPTER rather than matching command text, because
    ``fdictHashFiles`` and ``fdictHashAbsolutePaths`` build the same
    ``python3 -c "import base64; exec(...)"`` shape and differ only
    inside a base64 payload — a text marker could not tell the arXiv
    conjunct's hash from any other embedded script, and a test that
    cannot name what it observed is not evidence that the observation
    was the one required.

    Each record also carries the length of the double's gated ledger at
    the moment of the call, which is what lets the pre-run probe and the
    post-save auto-archive be asserted SEPARATELY: they hash the same
    path under the same mode, so nothing about the call itself
    distinguishes them, but the workflow save's write lands between.
    """
    listCalls = []
    fnRealHash = repoFiles.ContainerRepoFiles.fdictHashFiles

    def fnRecordThenHash(self, listRelPaths):
        admission = mutationAdmission.fadmissionActiveForContainerId(
            self.sContainerId,
        )
        listCalls.append({
            "listRelPaths": list(listRelPaths),
            "sMode": "" if admission is None else admission.sMode,
            "iLedgerLength": len(
                connectionDocker.listAdmittedPrimitives,
            ),
        })
        return fnRealHash(self, listRelPaths)

    with patch.object(
        repoFiles.ContainerRepoFiles, "fdictHashFiles", fnRecordThenHash,
    ):
        yield listCalls


@pytest.fixture
def tclientPublished():
    """The gated client over a container whose workflow has reached L2.

    The arXiv client is stubbed for the duration, and not merely for
    speed: the gate's last two conjuncts fetch the e-print's tarball
    and resolve its latest version over the NETWORK. Unstubbed, this
    fixture made real requests to arXiv — a test whose verdict depends
    on somebody else's server, and on the developer having one.
    """
    with patch(
        "vaibify.reproducibility.arxivClient.fdictFetchRemoteHashes",
        return_value={
            S_PUBLISHED_FIGURE_RELPATH: S_PUBLISHED_FIGURE_SHA,
        },
    ), patch(
        "vaibify.reproducibility.arxivClient.fsResolveLatestVersion",
        return_value="v1",
    ):
        yield _tConnectGatedClient(
            DockerDoubleServingAPublishedWorkflow(),
        )


def _fiIndexOfTheWorkflowSave(connectionDocker):
    """Return the ledger index of the workflow save's container write."""
    for iIndex, dictReached in enumerate(
        connectionDocker.listAdmittedPrimitives,
    ):
        if dictReached["sPrimitive"] == S_PRIMITIVE_WRITE:
            return iIndex
    return len(connectionDocker.listAdmittedPrimitives)


def _fnAssertHashCallRanUnder(dictCall, sExpectedMode, sDescription):
    """Assert one recorded ``fdictHashFiles`` call's live admission."""
    assert dictCall["sMode"] == sExpectedMode, (
        f"the {sDescription} hashed the repository under "
        f"{dictCall['sMode']!r}, not {sExpectedMode!r}: {dictCall}. "
        "Under '' the probe ran OUTSIDE the carrier, which the enforced "
        "branch refuses for every workflow at Level 2 or above."
    )


def _fdictRequireHashCall(listHashCalls, iIndex, sDescription):
    """Return one recorded hash call, or fail saying what was missing.

    Selected POSITIONALLY because three separate level probes hash the
    SAME path under one request and nothing about a call distinguishes
    them: the route's pre-run probe runs first, ``fnSaveWorkflowToContainer``
    derives the level again inside the mode-(a) save, and the
    auto-archive probes a third time afterwards. Order is the only
    discriminator, so the tests below pin it.
    """
    assert listHashCalls, (
        f"the {sDescription} never reached fdictHashFiles, so this "
        "asserts nothing about how the level probe was admitted. Either "
        "the fixture stopped being a Level 2 workflow (check the arXiv "
        "conjunct: an attested declaration, declared models, a declared "
        "personal layer, and an Overleaf-recorded commit with a pushed "
        "figure are ALL required), or the probe moved. Recorded calls: "
        f"{listHashCalls}"
    )
    return listHashCalls[iIndex]


@pytest.mark.falsification
@pytest.mark.parametrize("sRoute,dictBody,sExecMarker", [
    ("run-tests", None, "pytest -q"),
    ("run-test-category", {"sCategory": "integrity"}, "pytest -q"),
    ("save-and-run-test", {
        "sFilePath": "A/tests/testOne.py",
        "sContent": "def testOne():\n    assert True\n",
    }, "A/tests/testOne.py"),
])
def testTheLevelProbeAndTheTestRunShareOneLockHeldAdmission(
    tclientPublished, sRoute, dictBody, sExecMarker,
):
    """Each test-execution route probes AND runs in one mode-(b) worker.

    Every one of them brackets its run with ``fiProofLevel`` to decide
    whether the step's transition promoted the workflow, and on a
    published workflow that probe is not a cheap dictionary read: it
    hashes every Overleaf-pushed figure through the general exec
    primitive. Left on the request coroutine it holds no admission at
    all, so the enforced branch refuses it — and no fixture below Level
    2 can see that, which is why this one goes to the trouble of being
    Level 2.

    Probe and run are asserted together because they are one guarantee
    and one worker: the probe must run under the SAME lock-held
    admission as the run it brackets, so a hand-over cannot land
    between "what level was this before" and the test that changes it.

    Parametrized over all three routes rather than written out three
    times, and that is forced rather than stylistic: they share
    ``_ftProbeLevelThenRunUnderTheDrain``, so no mutation of that helper
    can fail one and spare another. Three separate tests would have
    reported one guard as three, and every mutant would have killed all
    of them.

    ``save-and-run-test`` additionally writes the researcher's edited
    test file inside the same worker as the run — a hand-over landing
    between the write and the pytest would attribute a result to
    content the successor now owns — which is why its exec marker is
    the file path rather than the step's command.

    Kills: passing ``_ftProbeLevelThenRunUnderTheDrain``'s worker to
    ``commitCarrier.fdictCommitSynchronousMutation`` instead of
    ``fdictRunLockHeldMutation``.

    Deliberately NOT "hoist ``fiProofLevel`` out of the worker", which is
    the defect this test exists for: that mutant refuses the request
    outright, so the save and the auto-archive never happen and it lands
    on the two tests below as well. A mutant that kills three tests
    isolates none of them. The mode swap leaves the request intact and
    fails here alone — and the ``sMode`` assertion catches the hoist
    too, since a hoisted probe records ``''``.

    Nothing here asserts the response status, for the reason
    :func:`_fnAssertSelectedRanUnder` gives: a refused mutation anywhere
    in the handler surfaces as a 500, so a status assertion would drag
    every later carrier's defect onto this test. Verified — with it in
    place, both mutants below killed this test too. The happy path is
    pinned separately by
    :func:`testEveryMigratedTestRouteStillAnswersTwoHundred`.
    """
    client, connectionDocker = tclientPublished
    with _tlistRecordEveryRepoHash(connectionDocker) as listHashCalls:
        client.post(
            f"/api/steps/{S_CONTAINER_ID}/0/{sRoute}", json=dictBody,
        )
    _fnAssertHashCallRanUnder(
        _fdictRequireHashCall(
            listHashCalls, 0, f"pre-run AICS level probe on {sRoute}",
        ),
        mutationAdmission.S_ADMISSION_MODE_LOCK_HELD,
        f"pre-run AICS level probe on {sRoute}",
    )
    _fnAssertExecsNamingRanUnder(
        connectionDocker, sExecMarker,
        mutationAdmission.S_ADMISSION_MODE_LOCK_HELD,
    )


@pytest.mark.falsification
def testTheAutoArchiveProbeRunsUnderItsOwnDrain(tclientPublished):
    """POST .../run-tests carries its post-save auto-archive too.

    ``fbMaybeAutoArchive`` re-reads the AICS level — the same general
    exec — then writes the L3 envelope and pushes to Overleaf and
    Zenodo. It is reached from all three test-execution routes, so
    migrating them without carrying it would refuse every run on a
    published workflow at the archive rather than at the probe: the same
    defect, one line later.

    Asserted on the hashes AFTER the workflow save's write, which is
    what separates this from the pre-run probe: both hash the same path
    under the same mode, and the save is the only event between them.

    Kills: passing ``_fnAutoArchiveUnderTheDrain``'s worker to
    ``commitCarrier.fdictCommitSynchronousMutation`` instead of
    ``fdictRunLockHeldMutation``.

    Deliberately a mode swap rather than "delete the carrier call",
    which was tried first: an uncarried archive REFUSES on this fixture,
    and a refusal also empties the hash ledger the sibling test above
    reads, so one defect killed two tests and neither was isolated. The
    swap leaves the archive running and only its admission wrong.
    """
    client, connectionDocker = tclientPublished
    with _tlistRecordEveryRepoHash(connectionDocker) as listHashCalls:
        client.post(f"/api/steps/{S_CONTAINER_ID}/0/run-tests")
    dictLast = _fdictRequireHashCall(
        listHashCalls, -1, "post-save auto-archive level probe",
    )
    assert dictLast["iLedgerLength"] > _fiIndexOfTheWorkflowSave(
        connectionDocker,
    ), (
        "the last repository hash happened BEFORE the workflow save's "
        "write, so it was the save's own level derivation and the "
        f"auto-archive never probed at all: {listHashCalls}"
    )
    _fnAssertHashCallRanUnder(
        dictLast, mutationAdmission.S_ADMISSION_MODE_LOCK_HELD,
        "post-save auto-archive level probe",
    )


@pytest.mark.falsification
def testTheTestResultSaveCommitsSynchronously(tclientPublished):
    """POST .../run-tests records the result under a mode-(a) admission.

    The third of the route's carriers, split from the two above for the
    reason the plot route's pair is split: a single test covering all
    three is killed by a defect in any one and proves none.

    Mode (a) is not interchangeable with mode (b) here. The synchronous
    carrier is the one that writes a ``file-write`` journal record with
    the workflow's own serialization fingerprint as the expected hash,
    so a crash mid-save can be adjudicated afterwards by hashing the
    file; a lock-held worker would hold the drain and journal a
    ``helper`` record that proves nothing about the bytes.

    Kills: running ``fnRunTests``'s save through
    ``_ftProbeLevelThenRunUnderTheDrain`` instead of
    ``fdictCommitWorkflowSave``, which commits the same bytes under
    ``lockHeldAsync``.

    Deliberately not "drop the carrier entirely": an unadmitted save
    refuses, which stops the auto-archive from ever running and so kills
    the sibling test above as well.
    """
    client, connectionDocker = tclientPublished
    client.post(f"/api/steps/{S_CONTAINER_ID}/0/run-tests")
    _fnAssertWritesRanUnder(
        connectionDocker, mutationAdmission.S_ADMISSION_MODE_SYNCHRONOUS,
    )


@pytest.mark.parametrize("sRoute,dictBody", [
    ("run-tests", None),
    ("run-test-category", {"sCategory": "integrity"}),
    ("save-and-run-test", {
        "sFilePath": "A/tests/testOne.py",
        "sContent": "def testOne():\n    assert True\n",
    }),
])
def testEveryMigratedTestRouteStillAnswersTwoHundred(
    tclientPublished, sRoute, dictBody,
):
    """The happy path, pinned where it cannot distort a kill-confirm.

    The falsification tests above deliberately ignore the status code,
    because a refused mutation anywhere in a handler becomes a 500 and
    would make every carrier's defect fail every one of them. Something
    still has to notice a route that has stopped working outright, and
    this is it: not a falsification claim, just the assertion that three
    migrated routes on a published workflow still succeed.
    """
    client, _connectionDocker = tclientPublished
    response = client.post(
        f"/api/steps/{S_CONTAINER_ID}/0/{sRoute}", json=dictBody,
    )
    assert response.status_code == 200, response.text


# ---------------------------------------------------------------------
# The dispatch gate that consumes the carrier's live-work registry.
#
# These live here rather than beside the other dispatch-guard tests
# because what they exercise is the CARRIER's registry, and the only
# machinery that can put a real mode-(b) supervisor into it is the
# blocked-clean hub above. A test that faked the supervisor would be
# asserting against its own fixture.
# ---------------------------------------------------------------------

def _tDurableContextFor(app, sName, sLease, sSessionToken):
    """Return the durable context the production WebSocket path passes.

    The lane tuple is built by the REAL builder over a stand-in
    connection rather than assembled here, and that is a correction
    rather than a preference: the hand-written version omitted
    ``sContainerName``, which no assertion in this file would ever have
    checked, and the durable launch raised ``KeyError`` from inside
    ``fbLaneTupleStillCurrent``. A fixture that invents the shape of
    the thing under test can only ever drift away from it.
    """
    connectionStandIn = SimpleNamespace(
        headers={},
        query_params={"sToken": sSessionToken, "sLeaseId": sLease},
    )
    return {
        "appState": app.state,
        "sName": sName,
        "dictLaneTuple": commitCarrier.fdictBuildLaneTupleFromWebSocket(
            app.state, sName, connectionStandIn,
        ),
    }


async def _tlistDriveOneRunThroughTheMessageLoop(
    connectionDocker, dictDurableContext,
):
    """Send one ``runSelected`` frame; return ``(listSent, listStarted)``.

    Drives the REAL message loop, so the gate under test is the one
    production uses. ``fnDispatchAction`` is recorded rather than run:
    the claim is that a refused run STARTS NOTHING, and a test that
    only counted refusal events would pass against a gate that emitted
    one and dispatched anyway.
    """
    from tests.testPipelineServerTaskEviction import (
        _FakeDispatchWebSocket,
    )
    listStarted = []

    async def fnRecordDispatch(sAction, *args, **kwargs):
        listStarted.append(sAction)

    websocketFake = _FakeDispatchWebSocket([
        json.dumps({"sAction": "runSelected", "listStepIndices": [0]}),
    ])
    dictPipelineTasks = {}
    with patch.object(
        pipelineServer, "fnDispatchAction", fnRecordDispatch,
    ):
        with pytest.raises(WebSocketDisconnect):
            await pipelineServer.fnPipelineMessageLoop(
                websocketFake, connectionDocker, S_CONTAINER_ID,
                DICT_WORKFLOW_WITH_OUTPUTS,
                {S_CONTAINER_ID: S_WORKFLOW_PATH}, "/workspace",
                dictPipelineTasks=dictPipelineTasks,
                dictDurableContext=dictDurableContext,
            )
        # A dispatch that WAS admitted runs as a task the loop does not
        # await, so without draining it here "nothing started" and "it
        # started but has not been scheduled yet" are indistinguishable
        # -- and the not-refused direction would pass vacuously.
        taskStarted = dictPipelineTasks.get(S_CONTAINER_ID)
        if taskStarted is not None:
            await taskStarted
    return (websocketFake.listSent, listStarted)


@pytest.mark.falsification
@pytest.mark.asyncio
async def testARunArrivingUnderALiveCarrierWorkerIsRefusedAndNamesIt():
    """A Run Step under a live mode-(b) worker refuses, naming the worker.

    ``_fbRefuseWhilePipelineTaskLive`` consults ``dictPipelineTasks``,
    which records only pipeline actions dispatched over this WebSocket,
    so an HTTP route holding the container's mutation lock is invisible
    to it. Before this gate a Run Step arriving during a test suite, a
    plot conversion or a clean was not refused: it reached
    ``fdictLaunchDurableTask``, blocked on the lock for as long as that
    work took, and the researcher saw an unexplained wait with no way
    to tell a slow container from a wedged one.

    The refusal must NAME the holder. "Busy" cannot tell a researcher
    whether to wait two seconds or abandon the attempt, which is the
    entire reason the lock holder registers an operation kind and
    target — an ``asyncio.Lock`` knows only that it is held. So the
    assertion is on the message CONTENT, not on the event's presence: a
    gate that answered "busy" forever would satisfy the latter
    perfectly.

    The remedy is asserted too. The Kill button stops a pipeline action
    and does nothing to a carrier worker, so a refusal that offered it
    here would send the researcher to a control that cannot help.

    Kills: passing no description to ``_fdictBusyRefusalEvent`` at the
    carrier-work gate, so the refusal falls back to the generic
    pipeline-action wording.
    """
    app, connectionDocker = _tBuildAsgiHubWithBlockedClean()
    sSessionToken = fsBootstrapCredential(app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=app, raise_app_exceptions=False,
        ),
        base_url="http://hub",
        headers={"X-Session-Token": sSessionToken},
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
            "the clean finished before the run was attempted, so the "
            "container was not busy when it mattered"
        )

        listSent, listStarted = await (
            _tlistDriveOneRunThroughTheMessageLoop(
                connectionDocker,
                _tDurableContextFor(
                    app, sName, sLease, sSessionToken,
                ),
            )
        )

        connectionDocker.eventCleanMayFinish.set()
        await taskClean

    listRefusals = [
        dictEvent for dictEvent in listSent
        if dictEvent.get("sType") == "runRefused"
    ]
    assert len(listRefusals) == 1, (
        f"the run was not refused while a carrier worker held the "
        f"container's mutation lock; it would have blocked on the lock "
        f"instead. Events sent: {listSent}"
    )
    assert "clean-outputs" in listRefusals[0]["sMessage"], (
        "the refusal does not name what holds the container: "
        f"{listRefusals[0]['sMessage']!r}. A researcher told only "
        "'busy' cannot tell a two-second write from a half-hour "
        "rebuild."
    )
    assert "Kill button" not in listRefusals[0]["sMessage"], (
        "the refusal offers the Kill button, which stops a pipeline "
        "action and has no effect on a carrier worker: "
        f"{listRefusals[0]['sMessage']!r}"
    )
    assert listRefusals[0]["listStepIndices"] == [0], (
        "the refusal must carry the refused indices so the browser can "
        "reset the lights it optimistically set to queued"
    )
    assert listStarted == [], (
        f"a refused run reached the dispatcher anyway: {listStarted}"
    )


class DockerDoubleProbingTheGateMidSynchronousWrite(
    DockerDoubleThatCallsTheRealGates,
):
    """Ask the run gate what is busy DURING a mode-(a) container write.

    The false-refusal direction, and it has to be asked from inside the
    write: a synchronous commit runs to completion on the event loop, so
    by the time any later statement could look, the admission is gone
    and the question answers itself. Here the probe happens while the
    mode-(a) admission is live and the effect closure is mid-flight,
    which is the only moment the wrong answer could be given.
    """

    def __init__(self):
        super().__init__()
        self.dictDurableContext = None
        self.listGateAnswersDuringWrite = []

    def fnWriteFile(
        self, sContainerId, sPath, baContent,
        iMode=None, iUid=None, iGid=None,
    ):
        if self.dictDurableContext is not None:
            self.listGateAnswersDuringWrite.append((
                mutationAdmission.fadmissionActiveForContainerId(
                    sContainerId,
                ),
                pipelineServer._fsDescribeBlockingMutationWork(
                    self.dictDurableContext,
                ),
            ))
        return super().fnWriteFile(
            sContainerId, sPath, baContent,
            iMode=iMode, iUid=iUid, iGid=iGid,
        )


@pytest.mark.falsification
@pytest.mark.asyncio
async def testASynchronousSaveNeverMakesTheRunGateRefuse():
    """A mode-(a) save in flight must not refuse a Run Step.

    The direction that turns a safety gate into a defect. This
    repository has already shipped a Run-Step-always-refused bug once —
    the terminal socket held the only budgeted slot, every Run Step was
    closed 4409, and the researcher was told the server was
    unreachable. A gate that refused on ANY carrier activity would
    reproduce it exactly: draft saves, file saves, settings saves and
    every workflow save are mode-(a) commits, and they happen
    constantly.

    They take no lock and register no supervisor, so they must be
    invisible here. Asserted from INSIDE the write, with the mode-(a)
    admission live, because that is the only instant at which a gate
    reading "is any admission active" rather than "is the drain held"
    would answer wrongly.

    Kills: making ``_fsDescribeBlockingMutationWork`` answer from
    ``mutationAdmission.fbLaneEnforced()`` — "some admission is live"
    — before consulting the lock holder. That is the confusion the
    test exists for, and it is not hypothetical-looking: both
    questions are about carrier state, and only one of them means the
    container is actually held.

    Note what does NOT kill it, because it was tried and bounds the
    claim: degrading the gate to
    ``fbContainerHasLiveMutationWork`` + a generic "a guarded
    operation" string. That mutant is real, but it lands on the
    refusal test above (which asserts the holder is NAMED), not here —
    this test only ever sees an empty answer either way. One mutant on
    two tests would have isolated neither.
    """
    connectionDocker = DockerDoubleProbingTheGateMidSynchronousWrite()
    with patch.object(
        pipelineServer, "_fconnectionCreateDocker",
        lambda: connectionDocker,
    ):
        app = pipelineServer.fappCreateApplication(
            sWorkspaceRoot="/workspace",
            sTerminalUserArg="testuser",
        )
    sSessionToken = fsBootstrapCredential(app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=app, raise_app_exceptions=False,
        ),
        base_url="http://hub",
        headers={"X-Session-Token": sSessionToken},
    ) as clientAsync:
        sLease = await _tConnectOverAsgi(clientAsync)
        clientAsync.headers["X-Vaibify-Lease"] = sLease
        sName = _fsContainerNameFor(app)
        connectionDocker.dictDurableContext = _tDurableContextFor(
            app, sName, sLease, sSessionToken,
        )
        response = await clientAsync.put(
            f"/api/settings/{S_CONTAINER_ID}",
            json={"iNumberOfCores": DICT_WORKFLOW["iNumberOfCores"] + 1},
        )
        assert response.status_code == 200, response.text

        listStarted = (await _tlistDriveOneRunThroughTheMessageLoop(
            connectionDocker, connectionDocker.dictDurableContext,
        ))[1]

    listProbed = connectionDocker.listGateAnswersDuringWrite
    assert listProbed, (
        "the settings save reached no container write, so nothing was "
        "asked of the gate while a mode-(a) admission was live"
    )
    listLive = [
        tAnswer for tAnswer in listProbed if tAnswer[0] is not None
    ]
    assert listLive, (
        "no write happened under a live admission, so this proves "
        f"nothing about the gate during one: {listProbed}"
    )
    listRefusing = [tAnswer for tAnswer in listLive if tAnswer[1]]
    assert listRefusing == [], (
        "a synchronous commit made the run gate report the container "
        f"busy: {listRefusing}. Every draft, file and settings save "
        "would refuse the researcher's next Run Step."
    )
    assert listStarted == ["runSelected"], (
        "a run was refused with no carrier worker holding the drain at "
        f"all: {listStarted}"
    )


# ---------------------------------------------------------------------
# Group 2, continued — the routes that push to a remote.
#
# The first group whose carrier boundary meets a live credential. What
# is different about them is not the mode: it is that the operation's
# natural NAME is a remote URL, the carrier writes that name into a
# journal file on disk and into the refusal a second session is shown,
# and a token-authenticated git remote reads
# https://x-access-token:<token>@github.com/owner/repo.git. vaibify
# stores that string verbatim, because it is what
# ``git config --get remote.origin.url`` returns.
# ---------------------------------------------------------------------

S_PUSH_REPO_NAME = "ExampleRepo"

# Synthetic: a real GitHub PAT prefix in front of a fixed letter run, so
# it matches the shape the redactor must catch and matches no credential
# that exists. The suite already carries several of these
# (tests/testGithubMirror.py, tests/testCredentialRedactor.py).
S_SYNTHETIC_PUSH_TOKEN = "ghp_" + "E" * 36
S_TOKENED_PUSH_REMOTE = (
    "https://x-access-token:" + S_SYNTHETIC_PUSH_TOKEN
    + "@github.com/exampleowner/examplerepo.git"
)
# What must SURVIVE redaction. Asserting only "the token is absent"
# would pass against a target that named nothing at all, which is the
# vacuous version of the test below.
S_PUSH_REMOTE_HOST_AND_PATH = "github.com/exampleowner/examplerepo.git"

# Emitted by both push commands and by nothing else the double answers.
S_PUSH_COMMAND_MARKER = "rev-parse --short HEAD"

T_PUSH_ROUTES = (
    ("push-staged", {"sCommitMessage": "[vaibify] Update repository"}),
    ("push-files", {
        "sCommitMessage": "[vaibify] Update repository",
        "listFilePaths": ["results.json"],
    }),
)


class DockerDoubleServingATokenedTrackedRepo(
    DockerDoubleThatCallsTheRealGates,
):
    """The gate-faithful double over a tracked repo with a tokened origin.

    The draft harness answers the sidecar read with an empty string, so
    against it every push refuses "not tracked" before reaching a push
    at all — a fixture that would let the assertions below pass having
    exercised the refusal rather than the push.
    """

    def __init__(self):
        super().__init__()
        self.setExistingPaths.add(
            posixpath.join(S_PROJECT_REPO, "results.json"),
        )

    def fbaFetchFile(self, sContainerId, sPath, iMaxBytes=None):
        """Answer the sidecar read, which is a TYPED read now.

        It was a ``cat`` through the general exec primitive when this
        double was written, so it was gated and recorded like any
        exec. Reading the tracked list no longer reaches that
        primitive at all -- which is why the assertions below dropped
        their sidecar clause.
        """
        if not sPath.endswith("tracked_repos.json"):
            return super().fbaFetchFile(sContainerId, sPath, iMaxBytes)
        return json.dumps({
            "iSchemaVersion": 1,
            "listTracked": [{
                "sName": S_PUSH_REPO_NAME,
                "sUrl": S_TOKENED_PUSH_REMOTE,
            }],
            "listIgnored": [],
        }).encode("utf-8")


@pytest.fixture
def tclientTokenedRepo():
    """The gated client over a container tracking a tokened repository."""
    return _tConnectGatedClient(DockerDoubleServingATokenedTrackedRepo())


@pytest.mark.falsification
@pytest.mark.parametrize("sRoute,dictBody", T_PUSH_ROUTES)
def testTheRepositoryPushRunsUnderTheDrain(
    tclientTokenedRepo, sRoute, dictBody,
):
    """POST /api/repos/.../push-{staged,files} pushes under mode (b).

    A push commits, contacts a remote, and runs for as long as the
    network takes. It used to run on a bare ``asyncio.to_thread``,
    holding no mutation lock and registering no operation, so an
    ownership hand-over arriving mid-push saw an idle-looking container,
    committed, and the FORMER owner's git process kept pushing into a
    workspace that had changed hands.

    BOTH container round-trips are asserted, and in ONE test rather
    than two. The sidecar read that DECIDES the push is as much part of
    the guarantee as the push — outside the drain it spans a hand-over,
    so a successor's sidecar could decide the former owner's push — but
    every mutant that moves one of them out of the worker moves the
    other with it. Two tests would have reported one guard as two.

    Parametrized rather than duplicated because both routes go through
    one helper and one carrier call: they are the same shape, and the
    mutant below is in that shared helper.

    Kills: passing ``_fdictPushRepositoryUnderTheDrain``'s worker to
    ``commitCarrier.fdictCommitSynchronousMutation`` instead of
    ``fdictRunLockHeldMutation``. Deliberately a mode swap and not
    "delete the carrier call": an uncarried push is refused before the
    sidecar is even read, so the ledger is empty for a second reason
    and the assertion below can no longer distinguish "ran under the
    wrong admission" from "never ran".

    THIS MUTANT ALSO KILLS
    :func:`testALivePushNamesItsRemoteWithoutLeakingItsToken`, AND NO
    MUTANT OF THIS GUARD DOES NOT. Stated rather than papered over,
    because the usual remedy — pick a mutant that lands on one test —
    has no candidate here. That test's subject is the description a
    LIVE mode-(b) supervisor publishes while it holds the drain, so
    any defect that stops the push running under such a supervisor
    removes the thing it observes, and it then fails on its
    precondition ("a transfer committed over a live push"), never on
    its redaction claim. The reverse does NOT hold, which is what
    keeps the pair worth having: dropping the redaction leaves the
    mode untouched and kills that test alone — verified, 26 passed,
    1 failed.
    """
    client, connectionDocker = tclientTokenedRepo
    client.post(
        f"/api/repos/{S_CONTAINER_ID}/{S_PUSH_REPO_NAME}/{sRoute}",
        json=dictBody,
    )
    # The tracked-list read used to be asserted here too, because it
    # was a `cat` through the exec primitive and shared the push's
    # drain. It is a typed read now and reaches no gated primitive, so
    # there is nothing left for the admission ledger to say about it.
    # It still runs inside the same worker -- the code path did not
    # move -- but that is now a structural fact rather than an
    # observable one, and stating it that way is the honest version.
    _fnAssertExecsNamingRanUnder(
        connectionDocker, S_PUSH_COMMAND_MARKER,
        mutationAdmission.S_ADMISSION_MODE_LOCK_HELD,
    )


class DockerDoubleWhereThePushedRepoIsTheProjectRepo(
    DockerDoubleServingATokenedTrackedRepo,
):
    """The tokened-repo double, over a workflow ROOTED at that repository.

    The ordinary fixture's workflow lives at ``/workspace`` while the
    pushed repository is ``/workspace/ExampleRepo``, so
    ``_fsAfterRepoPushSuccess``'s exact-equality gate skips the
    post-push verify entirely. That is the common production shape
    inverted: a researcher pushing their own project repo takes the
    branch, and no fixture in this suite arranged for it — measured by
    putting an unconditional raise in that branch and watching all 1827
    push/sync/verify tests still pass.
    """

    def ftResultExecuteCommand(
        self, sContainerId, sCommand, sWorkdir=None,
    ):
        """Report the repository as the project repo's git top level.

        Necessary, not decorative: the workflow loader OVERWRITES
        ``sProjectRepoPath`` with what ``git rev-parse --show-toplevel``
        answers, so serving the field in the document alone left the
        workflow rooted at ``/workspace`` and the verify gate shut. The
        first version of this fixture did exactly that and reported the
        verify as never reached.
        """
        if "git rev-parse --show-toplevel" in sCommand:
            mutationAdmission.fnAssertContainerCommandAdmitted(
                sContainerId, S_PRIMITIVE_EXEC,
            )
            self._fnRecordLiveAdmission(
                sContainerId, S_PRIMITIVE_EXEC, sCommand,
            )
            return (0, "/workspace/" + S_PUSH_REPO_NAME + "\n")
        return super().ftResultExecuteCommand(
            sContainerId, sCommand, sWorkdir,
        )

    def fbaFetchFile(self, sContainerId, sPath, iMaxBytes=None):
        if sPath != S_WORKFLOW_PATH:
            return super().fbaFetchFile(sContainerId, sPath, iMaxBytes)
        dictWorkflow = copy.deepcopy(DICT_WORKFLOW)
        dictWorkflow["sProjectRepoPath"] = (
            "/workspace/" + S_PUSH_REPO_NAME
        )
        dictWorkflow["dictRemotes"] = {
            "github": {
                "sOwner": "exampleowner", "sRepo": "examplerepo",
                "sBranch": "main", "sCommittedSha": "abc123",
            },
        }
        return json.dumps(dictWorkflow).encode("utf-8")


@pytest.mark.falsification
def testThePostPushVerifyRewritesTheSyncCacheUnderItsOwnDrain():
    """The verify that follows a push carries its own mode-(b) admission.

    ``fsRefreshVerifyCacheAfterPush`` hashes the project repo and
    REWRITES ``syncStatus.json`` inside the container. It runs AFTER the
    push's carrier has settled and released the drain, so it cannot ride
    that admission and needs one of its own — and on the enforced branch
    an uncarried write is refused at the primitive, which would break
    the push for exactly the researchers who push their own project
    repo.

    Asserted by instrumenting the verify worker rather than by matching
    command text: the verify's own container work depends on the remote
    and the repository contents, so a text marker would be a claim about
    the fixture. What is under test is the admission live when the
    worker runs.

    The verify is best-effort by contract — a failure becomes a warning
    on the response and never a failed push — so this deliberately does
    not assert the verify SUCCEEDS. It asserts it was admitted.

    Kills: dropping the ``requestHttp=requestHttp`` argument from
    ``_fsAfterRepoPushSuccess``'s ``fsRefreshVerifyCacheAfterPush``
    call, which silently returns the verify to the legacy unenforced
    ``to_thread`` lane — the worker then runs under ``''``, no
    admission, and every write it makes is refused.
    """
    from vaibify.gui import routeContext

    listVerifyAdmissions = []

    def fnRecordThenVerify(dictWorkflow, sService, filesRepo):
        admission = mutationAdmission.fadmissionActiveForContainerId(
            S_CONTAINER_ID,
        )
        listVerifyAdmissions.append(
            "" if admission is None else admission.sMode,
        )
        return {"sService": sService}

    client, _connectionDocker = _tConnectGatedClient(
        DockerDoubleWhereThePushedRepoIsTheProjectRepo(),
    )
    with patch.object(
        routeContext, "fdictRunRemoteVerifyBlocking", fnRecordThenVerify,
    ):
        response = client.post(
            f"/api/repos/{S_CONTAINER_ID}/{S_PUSH_REPO_NAME}/push-staged",
            json={"sCommitMessage": "[vaibify] Update repository"},
        )
    assert response.status_code == 200, response.text
    assert listVerifyAdmissions == [
        mutationAdmission.S_ADMISSION_MODE_LOCK_HELD,
    ], (
        "the post-push verify did not run under a lock-held carrier: "
        f"{listVerifyAdmissions}. An empty list means the verify was "
        "never reached at all — check that the fixture's workflow is "
        "still rooted at the repository being pushed, which is the only "
        "reason the exact-equality gate opens; a mode of '' means it "
        "ran on the legacy to_thread lane with no admission."
    )


class DockerDoubleThatBlocksTheRepoPush(
    DockerDoubleServingATokenedTrackedRepo,
):
    """The tokened-repo double, with the ``git push`` held open.

    Synchronous ``threading.Event``s for the reason
    :class:`DockerDoubleThatBlocksTheClean` records: the carrier runs
    workers with ``asyncio.to_thread``, so an ``async def`` would hand
    back a coroutine nobody awaits and the push would never block.
    """

    def __init__(self):
        super().__init__()
        self.eventPushStarted = threading.Event()
        self.eventPushMayFinish = threading.Event()
        self.listPushCommandsRun = []

    def ftResultExecuteCommand(
        self, sContainerId, sCommand, sWorkdir=None,
    ):
        if S_PUSH_COMMAND_MARKER not in sCommand:
            return super().ftResultExecuteCommand(
                sContainerId, sCommand, sWorkdir,
            )
        mutationAdmission.fnAssertContainerCommandAdmitted(
            sContainerId, S_PRIMITIVE_EXEC,
        )
        self.eventPushStarted.set()
        self.eventPushMayFinish.wait(10)
        self.listPushCommandsRun.append(sCommand)
        return (0, "abc1234")


def _tBuildAsgiHubWithBlockedPush():
    """Return ``(app, connectionDocker)`` with the push held open.

    httpx over ASGI rather than ``TestClient`` for the reason
    :func:`_tBuildAsgiHubWithBlockedClean` gives: the transfer and the
    in-flight request must share ONE event loop, because the container
    mutation lock is an ``asyncio.Lock`` bound to the loop that made it.
    """
    connectionDocker = DockerDoubleThatBlocksTheRepoPush()
    with patch.object(
        pipelineServer, "_fconnectionCreateDocker",
        lambda: connectionDocker,
    ):
        app = pipelineServer.fappCreateApplication(
            sWorkspaceRoot="/workspace",
            sTerminalUserArg="testuser",
        )
    return (app, connectionDocker)


def _fsReadWholeJournalForTheContainer():
    """Return the container's raw write-ahead journal file as text."""
    from vaibify.config import operationJournal
    try:
        with open(
            operationJournal.fsJournalPathFor(S_CONTAINER_NAME),
            "r", encoding="utf-8",
        ) as fileJournal:
            return fileJournal.read()
    except FileNotFoundError:
        return ""


@pytest.mark.falsification
@pytest.mark.asyncio
async def testALivePushNamesItsRemoteWithoutLeakingItsToken(caplog):
    """The busy refusal names the remote; journal and logs hold no token.

    A migrated push registers what it is doing so a transfer, a Run
    Step, or a second tab can be told WHICH push holds the container —
    "a guarded operation" cannot tell a researcher whether to wait two
    seconds or abandon the attempt. The description worth having names
    the remote, and a remote is exactly where a credential hides: a
    token-authenticated clone's origin URL carries the token in its
    user-info segment, and vaibify copies that URL verbatim into the
    tracked-repos sidecar because it is what ``git config --get
    remote.origin.url`` returns.

    Three surfaces are asserted, because they fail independently: the
    refusal message a second session reads, the write-ahead journal FILE
    (on disk under ``~/.vaibify/journal``, outliving the process), and
    the hub log.

    THE JOURNAL AND THE REFUSAL CARRY DIFFERENT TEXT, AND THAT IS THE
    DESIGN. The journal record's target is fixed by
    ``fsPrepareOperation`` before the worker runs, so it can only name
    what is known WITHOUT a container round-trip — the repository. The
    remote is discovered by the sidecar read inside the worker, and
    refines the SUPERVISOR's ``sTarget``, which is mutable for exactly
    this purpose and which a busy refusal reads live. So the on-disk
    record is credential-free by construction, and the redaction guards
    the in-memory description; both are asserted, and the journal's
    assertion is the standing guard against a later change that moves
    the URL forward into the prepare-time target.

    Both directions matter. The remote's host and path must be PRESENT
    in the refusal, or a route that named nothing would pass every leak
    assertion while telling the researcher nothing; the token must be
    ABSENT everywhere.

    Kills: removing the ``fsRedactCredentials`` call from
    ``_fsDescribePushTarget``, so the sidecar's stored URL — token and
    all — becomes the lock holder's registered target.
    """
    caplog.set_level(logging.DEBUG)
    app, connectionDocker = _tBuildAsgiHubWithBlockedPush()
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

        taskPush = asyncio.ensure_future(clientAsync.post(
            f"/api/repos/{S_CONTAINER_ID}/{S_PUSH_REPO_NAME}"
            "/push-staged",
            json={"sCommitMessage": "[vaibify] Update repository"},
        ))
        await asyncio.to_thread(
            connectionDocker.eventPushStarted.wait, 10,
        )
        assert connectionDocker.listPushCommandsRun == [], (
            "the push finished before the refusal was asked for, so "
            "the container was not busy when it mattered"
        )

        sCapability = browserSession.fsMintTransferCapability(
            app.state.dictBrowserSessions, sName,
            app.state.dictContainerOwners[sName].iOwnerGeneration,
        )
        sOutcome, dictPayload = await sessionLifecycle.ftTransferOwnership(
            app.state, sCapability,
        )
        sJournalWhileLive = _fsReadWholeJournalForTheContainer()

        connectionDocker.eventPushMayFinish.set()
        await taskPush

    assert sOutcome == sessionLifecycle.S_TRANSFER_BUSY_RETRY, (
        f"a transfer committed over a live push: {dictPayload}"
    )
    sRefusal = dictPayload["sMessage"]
    assert S_PUSH_REMOTE_HOST_AND_PATH in sRefusal, (
        "the refusal does not name the remote the push is contacting, "
        "so the leak assertions below are vacuous — a target naming "
        f"nothing passes them all: {sRefusal!r}"
    )
    assert S_SYNTHETIC_PUSH_TOKEN not in sRefusal, (
        "the busy refusal shown to a second session carries the "
        f"remote's access token: {sRefusal!r}"
    )
    assert sJournalWhileLive, (
        "no write-ahead record existed while the push was in flight, "
        "so the journal assertions below assert nothing"
    )
    assert "github-push " + S_PUSH_REPO_NAME in sJournalWhileLive, (
        "the journal record does not name the push at all, so its leak "
        f"assertion is vacuous: {sJournalWhileLive!r}"
    )
    assert S_SYNTHETIC_PUSH_TOKEN not in sJournalWhileLive, (
        "the write-ahead journal FILE records the remote's access "
        "token; it sits on disk under ~/.vaibify/journal and outlives "
        f"the process: {sJournalWhileLive!r}"
    )
    assert S_SYNTHETIC_PUSH_TOKEN not in caplog.text, (
        "the remote's access token reached the hub log"
    )


# ---------------------------------------------------------------------
# Group 1 continued -- the seven declaration saves, mode (a).
#
# All seven do the same thing: edit the workflow dict, then persist
# project.json. They therefore share ONE helper,
# ``routeContext.fdictCommitWorkflowSave``, and the isolation question is
# what that sharing does to a kill-confirm. The parametrization answers
# it: the mutant for a route is that route's OWN call site reverted to
# ``dictCtx["save"](...)``, which kills exactly its own parameter case.
# A defect in the shared helper legitimately kills all seven, and
# should -- that is ONE guard being reported once per route that
# depends on it, not seven guards none of which is proven.
# ---------------------------------------------------------------------

DICT_DECLARED_MODEL = {
    "sVendor": "ExampleVendor",
    "sModelId": "example-model-1",
    "sUseStartDate": "2026-01-01",
    "sUseEndDate": "2026-02-01",
    "bOpenWeights": False,
}


def _fdictWorkflowWithDeclarationsAlreadyMade():
    """Return the draft workflow with the state two routes need present.

    ``ai-models/remove`` answers 404 with nothing declared and
    ``approve-first-capture`` answers 409 with the Prompt Record off, so
    both would return before reaching a container primitive and their
    assertions would be vacuous. Seeded in the workflow the container
    SERVES rather than by calling the sibling routes first: driving
    ``ai-models/declare`` as setup for ``ai-models/remove`` would let
    one broken carrier fail two parameter cases, which is the shape
    that proves two guards exist and neither works.
    """
    dictWorkflow = copy.deepcopy(DICT_WORKFLOW)
    dictWorkflow["dictAiProvenance"] = {
        "listDeclaredModels": [copy.deepcopy(DICT_DECLARED_MODEL)],
        "dictPromptRecord": {
            "bEnabled": True,
            "sEnabledAtUtc": "2026-01-01T00:00:00+00:00",
            "bFirstCaptureReviewed": False,
        },
    }
    return dictWorkflow


class DockerDoubleServingADeclaredWorkflow(
    DockerDoubleThatCallsTheRealGates,
):
    """The gated double over a workflow that already carries declarations.

    Only the FIRST read of ``project.json`` is redirected; once a save
    has landed, the parent's in-memory file wins, so the double keeps
    telling the truth about what was written.
    """

    def fbaFetchFile(self, sContainerId, sPath, iMaxBytes=None):
        if sPath == S_WORKFLOW_PATH and sPath not in self._dictFiles:
            self._dictFiles[sPath] = json.dumps(
                _fdictWorkflowWithDeclarationsAlreadyMade(),
            ).encode("utf-8")
        return DockerDoubleThatCallsTheRealGates.fbaFetchFile(
            self, sContainerId, sPath, iMaxBytes,
        )


@pytest.fixture
def tclientDeclared():
    """The gated client over a workflow with declarations already made."""
    return _tConnectGatedClient(DockerDoubleServingADeclaredWorkflow())


# Each body is chosen to REACH the save on a workflow in the fixture's
# state, with no prerequisite call to a sibling route. The two
# ``bEnabled: False`` bodies take the DISABLING direction on purpose:
# enabling the Prompt Record requires the detect-secrets scanner on the
# host, which would make the verdict depend on the developer's install.
T_DECLARATION_SAVE_ROUTES = [
    ("ai-models/declare", dict(DICT_DECLARED_MODEL)),
    ("ai-models/remove", {
        "sVendor": DICT_DECLARED_MODEL["sVendor"],
        "sModelId": DICT_DECLARED_MODEL["sModelId"],
    }),
    ("prompt-record/configure", {"bEnabled": False}),
    ("prompt-record/approve-first-capture", None),
    ("supervision/configure", {"bEnabled": False}),
    ("personal-layer/declare", {"sStatus": "none"}),
    ("ai-declaration/add-step", {}),
]


@pytest.mark.falsification
@pytest.mark.parametrize("sSuffix,dictBody", T_DECLARATION_SAVE_ROUTES)
def testTheDeclarationSaveCommitsThroughTheSynchronousCarrier(
    tclientDeclared, sSuffix, dictBody,
):
    """Each declaration route persists project.json under mode (a).

    Mode (a) is not interchangeable here. The synchronous carrier writes
    a ``file-write`` journal record whose expected hash IS the
    workflow's own serialization fingerprint, so a crash inside the
    commit window can be adjudicated afterwards by hashing the file on
    disk. A lock-held worker would journal a ``helper`` record, which
    proves nothing about the bytes.

    Kills: reverting this route's ``fdictCommitWorkflowSave(...)`` call to
    ``dictCtx["save"](sContainerId, dictWorkflow)``. On the enforced
    branch that save reaches the write primitive with no admission open
    at all, so the recorded mode is ``''``.

    The status code is deliberately NOT asserted, so that a lost
    carrier is reported as the admission it ran under rather than as a
    generic 500. Measured, so the claim is bounded: each of the seven
    mutants killed its own parameter case here AND its own case in the
    happy-path test below, and no sibling's. The happy-path pair is not
    a second guard -- it is the same defect surfacing as a broken
    feature, which is why it carries no falsification mark.
    """
    client, connectionDocker = tclientDeclared
    client.post(
        f"/api/workflow/{S_CONTAINER_ID}/{sSuffix}", json=dictBody,
    )
    _fnAssertWritesRanUnder(
        connectionDocker, mutationAdmission.S_ADMISSION_MODE_SYNCHRONOUS,
    )


@pytest.mark.parametrize("sSuffix,dictBody", T_DECLARATION_SAVE_ROUTES)
def testEveryMigratedDeclarationRouteStillAnswersTwoHundred(
    tclientDeclared, sSuffix, dictBody,
):
    """The happy path, kept where it cannot distort a kill-confirm.

    Not a falsification claim: just the assertion that a researcher can
    still record a declaration, which the tests above deliberately do
    not make.
    """
    client, _connectionDocker = tclientDeclared
    response = client.post(
        f"/api/workflow/{S_CONTAINER_ID}/{sSuffix}", json=dictBody,
    )
    assert response.status_code == 200, response.text


# ---------------------------------------------------------------------
# Group 1 continued -- the six step-CRUD saves, mode (a).
#
# The same shape as the declaration family above, and for the same
# reason: every one of these routes edits the workflow dict and then
# persists project.json through the shared
# ``routeContext.fdictCommitWorkflowSave``, so the isolation question is
# again what that sharing does to a kill-confirm. The parametrization
# answers it the same way -- the mutant for a route is that route's OWN
# call site reverted to ``dictCtx["save"](...)``, which kills exactly
# its own parameter case, while a defect in the shared helper
# legitimately kills all six.
#
# Each body REACHES the save against the draft harness's one-step
# workflow with no prerequisite call to a sibling route. That matters
# for two of them specifically: ``input-data`` and
# ``declare-no-input-data`` both SKIP their save when the state they
# record is already present, so arranging that state by driving a
# sibling first is what would let one broken carrier fail two
# parameter cases.
# ---------------------------------------------------------------------

def _fdictNewStepBody(sName):
    """Return a create/insert body whose slug is unique in the harness."""
    return {
        "sName": sName,
        "sDirectory": "",
        "bPlotOnly": False,
        "saPlotCommands": [],
        "saPlotFiles": [],
    }


T_STEP_SAVE_ROUTES = [
    ("POST", "create", _fdictNewStepBody("Carried Step")),
    ("POST", "insert/0", _fdictNewStepBody("Inserted Step")),
    ("DELETE", "0", None),
    ("POST", "reorder", {"iFromIndex": 0, "iToIndex": 0}),
    ("POST", "0/input-data", {"sPath": "data/observations.csv"}),
    ("POST", "declare-no-input-data", None),
]


@pytest.mark.falsification
@pytest.mark.parametrize("sMethod,sSuffix,dictBody", T_STEP_SAVE_ROUTES)
def testTheStepEditCommitsThroughTheSynchronousCarrier(
    tclientGated, sMethod, sSuffix, dictBody,
):
    """Each step-CRUD route persists project.json under mode (a).

    Mode (a) is not interchangeable here, for the reason the declaration
    family states: the synchronous carrier writes a ``file-write``
    journal record whose expected hash IS the workflow's serialization
    fingerprint, so a crash inside the commit window can be adjudicated
    afterwards by hashing the file on disk. A lock-held worker would
    journal a ``helper`` record, which proves nothing about the bytes.

    Kills: reverting this route's ``fdictCommitWorkflowSave(...)`` call to
    ``dictCtx["save"](sContainerId, dictWorkflow)``. On the enforced
    branch that save reaches the write primitive with no admission open
    at all, so the recorded mode is ``''``.

    The status code is deliberately NOT asserted, so a lost carrier is
    reported as the admission it ran under rather than as a generic 500;
    the happy path is pinned separately below.
    """
    client, connectionDocker = tclientGated
    client.request(
        sMethod, f"/api/steps/{S_CONTAINER_ID}/{sSuffix}", json=dictBody,
    )
    _fnAssertWritesRanUnder(
        connectionDocker, mutationAdmission.S_ADMISSION_MODE_SYNCHRONOUS,
    )


@pytest.mark.parametrize("sMethod,sSuffix,dictBody", T_STEP_SAVE_ROUTES)
def testEveryMigratedStepRouteStillAnswersTwoHundred(
    tclientGated, sMethod, sSuffix, dictBody,
):
    """The happy path, kept where it cannot distort a kill-confirm.

    Not a falsification claim: just the assertion that a researcher can
    still add, insert, delete, reorder and annotate steps, which the
    test above deliberately does not make.
    """
    client, _connectionDocker = tclientGated
    response = client.request(
        sMethod, f"/api/steps/{S_CONTAINER_ID}/{sSuffix}", json=dictBody,
    )
    assert response.status_code == 200, response.text


class DockerDoubleWithNothingAtTheNewProjectPath(
    DockerDoubleThatCallsTheRealGates,
):
    """The gated double, reporting the new project's path absent.

    The draft harness answers an unrecognized command ``(0, "")``, and
    the create route reads a zero exit from ``test -e`` as "a project
    already exists here" -- so without this the route would 409 before
    writing anything and the assertion below would be vacuous. The gate
    still runs through the parent; only the ANSWER is corrected, so the
    correction cannot accidentally exempt the command from the gate.
    """

    def ftResultExecuteCommand(
        self, sContainerId, sCommand, sWorkdir=None,
    ):
        tResult = DockerDoubleThatCallsTheRealGates.ftResultExecuteCommand(
            self, sContainerId, sCommand, sWorkdir,
        )
        if sCommand.startswith("test -e"):
            return (1, "")
        return tResult


@pytest.mark.falsification
def testTheProjectCreationRunsUnderTheDrain():
    """POST /api/workflows/create probes AND writes under one mode-(b) drain.

    Both halves matter, and the probes are the half a migration is
    likely to leave behind. The route's ``mkdir -p`` is an obvious
    mutation; the duplicate-name search, the repo-directory test and the
    "does this project already exist" test are container COMMANDS, which
    the gate treats as mutating because a primitive handed command text
    cannot know what the text does. A carrier around only the write
    would leave every one of those refused.

    Holding one drain across the whole sequence is also the point rather
    than a convenience: the existence probe is what licenses the write,
    so a lock dropped between them lets a second session create the same
    project in the gap.

    Kills: replacing ``_fdictCreateWorkflowUnderTheDrain``'s
    fdictRunLockHeldMutation call with a direct call to its worker.
    """
    client, connectionDocker = _tConnectGatedClient(
        DockerDoubleWithNothingAtTheNewProjectPath(),
    )
    response = client.post(
        f"/api/workflows/{S_CONTAINER_ID}/create",
        json={
            "sWorkflowName": "Carried Project",
            "sFileName": "carriedProject",
            "sRepoDirectory": "repo",
        },
    )
    assert response.status_code == 200, response.text
    _fnAssertWritesRanUnder(
        connectionDocker, mutationAdmission.S_ADMISSION_MODE_LOCK_HELD,
    )
    _fnAssertExecsNamingRanUnder(
        connectionDocker, "mkdir -p",
        mutationAdmission.S_ADMISSION_MODE_LOCK_HELD,
    )


@pytest.mark.falsification
def testARefusedProjectCreationLeavesTheContainerUsable(tclientGated):
    """A project file already there is a 409, not a quarantine.

    Kills: dropping the ``errorRefusal`` return in
    ``_fdictCreateWorkflowUnderTheDrain``'s worker so every
    ``HTTPException`` propagates out of the carrier's thread.

    The refusals the create route raises are all EXPECTED 4xx, and they
    are raised from inside the carrier's worker thread. A worker that
    lets one propagate poisons its journal record and marks the
    container as needing reconciliation, so a researcher who picked a
    filename that was already in use would be told to run ``vaibify
    reconcile``. The proof that it did not is the NEXT mutation
    succeeding against the same container.

    Uses the ORDINARY gated double, whose ``test -e`` answers zero --
    "something is already at that path" -- which is exactly the refusal
    the sibling test above had to correct away to reach the write.
    """
    client, _connectionDocker = tclientGated
    responseRefused = client.post(
        f"/api/workflows/{S_CONTAINER_ID}/create",
        json={
            "sWorkflowName": "Occupied Project",
            "sFileName": "occupiedProject",
            "sRepoDirectory": "repo",
        },
    )
    assert responseRefused.status_code == 409, responseRefused.text
    responseAfter = client.put(
        f"/api/settings/{S_CONTAINER_ID}",
        json={"iNumberOfCores": DICT_WORKFLOW["iNumberOfCores"] + 1},
    )
    assert responseAfter.status_code == 200, (
        "the refused create quarantined the container: a later mutation "
        f"answered {responseAfter.status_code} -- {responseAfter.text}"
    )


def _fdictWorkflowOneStepBelowTheWarning():
    """Return the draft workflow padded to 99 steps.

    The hundred-step warning fires on the step that CROSSES the
    threshold, so the served workflow has to sit exactly one below it.
    Padded here rather than by calling ``create`` ninety-nine times: a
    setup that drives the route under test would make its own carrier a
    prerequisite of its own assertion.
    """
    dictWorkflow = copy.deepcopy(DICT_WORKFLOW)
    dictStepTemplate = dictWorkflow["listSteps"][0]
    dictWorkflow["listSteps"] = [
        dict(
            copy.deepcopy(dictStepTemplate),
            sName=f"Filler {iStep}", sDirectory=f"Filler{iStep}",
        )
        for iStep in range(99)
    ]
    return dictWorkflow


class DockerDoubleServingNinetyNineSteps(
    DockerDoubleThatCallsTheRealGates,
):
    """The gated double over a workflow one step below the warning."""

    def fbaFetchFile(self, sContainerId, sPath, iMaxBytes=None):
        if sPath == S_WORKFLOW_PATH and sPath not in self._dictFiles:
            self._dictFiles[sPath] = json.dumps(
                _fdictWorkflowOneStepBelowTheWarning(),
            ).encode("utf-8")
        return DockerDoubleThatCallsTheRealGates.fbaFetchFile(
            self, sContainerId, sPath, iMaxBytes,
        )


@pytest.mark.falsification
def testTheHundredStepWarningSaveIsCarriedToo():
    """The create route's SECOND save is carried, not just the first.

    ``fnCreateStep`` saves twice when the workflow crosses a hundred
    steps: once for the step itself, and once more for the
    ``bWarnedHundredSteps`` flag. That flag's save is a separate call
    site, so a migration that carried only the obvious one would leave a
    write reaching the primitive unadmitted -- and it would be invisible
    to the parametrized test above, whose one-step workflow never
    crosses the threshold. This is the same class as the two-mode routes
    elsewhere in this file: one handler, two carriers, and only a test
    that drives BOTH can tell a missing one from a present one.

    Kills: reverting the ``bShouldWarn`` branch's
    ``fdictCommitWorkflowSave(...)`` to ``dictCtx["save"](...)``.
    """
    client, connectionDocker = _tConnectGatedClient(
        DockerDoubleServingNinetyNineSteps(),
    )
    response = client.post(
        f"/api/steps/{S_CONTAINER_ID}/create",
        json=_fdictNewStepBody("Hundredth Step"),
    )
    assert response.json()["bShouldWarnHundredSteps"] is True, response.text
    listWrites = [
        dictReached
        for dictReached in connectionDocker.listAdmittedPrimitives
        if dictReached["sPrimitive"] == S_PRIMITIVE_WRITE
    ]
    assert len(listWrites) >= 2, (
        "the crossing create must write project.json twice -- once for "
        f"the step and once for the warning flag; it wrote {listWrites}"
    )
    _fnAssertWritesRanUnder(
        connectionDocker, mutationAdmission.S_ADMISSION_MODE_SYNCHRONOUS,
    )


# ---------------------------------------------------------------------
# Group 5 -- five of the Sync panel's routes.
#
# The first block of syncRoutes to migrate, and it spans three shapes,
# which is why the assertions below are not one parametrization. The
# tracking toggle is an ordinary mode-(a) project.json save. The git
# identity and the single-file push are mode-(b) container EXECS. The
# remote verify is mode (b) whose effect is a container WRITE rather
# than an exec, and arXiv configure carries BOTH a mode-(a) save and a
# mode-(b) cache rewrite in one handler -- so its two carriers are
# asserted separately, or a missing one hides behind the other.
# ---------------------------------------------------------------------

def _fdictStubbedRemoteStatus(sService):
    """Return the status shape ``fnWriteSyncStatus`` persists.

    Stubbed because the real verify contacts a remote, and the claim
    under test is the ADMISSION its container write runs under, not the
    hashing. What stays real is that a write actually happens: a stub
    that returned early would leave the ledger empty and the assertion
    vacuous, which is the case ``_fnAssertSelectedRanUnder`` reports as
    a failure rather than passing.
    """
    return {
        "sService": sService,
        "bMatches": True,
        "iTotalFiles": 0,
        "iMatchedFiles": 0,
        "listDivergent": [],
        "sCheckedAt": _fsFreshIsoTimestamp(),
    }


@contextlib.contextmanager
def _fnRemoteVerifyStubbed(sService):
    """Stub the remote comparison; leave the container write real."""
    from vaibify.reproducibility import scheduledReverify
    with patch.object(
        scheduledReverify, "fdictVerifyRemoteService",
        lambda filesRepo, dictWorkflow, sRequested, sNowIso=None: (
            _fdictStubbedRemoteStatus(sService)
        ),
    ), patch(
        "vaibify.gui.routes.syncRoutes._fnRequireNetworkAccess",
        lambda sContainerId: None,
    ):
        yield


@pytest.mark.falsification
def testTheSyncTrackingToggleCommitsThroughTheSynchronousCarrier(
    tclientGated,
):
    """POST /api/sync/{id}/track persists project.json under mode (a).

    Kills: reverting ``fdictSetTracking``'s ``fdictCommitWorkflowSave(...)``
    call to ``dictCtx["save"](sContainerId, dictWorkflow)``. On the
    enforced branch that save reaches the write primitive with no
    admission open at all, so the recorded mode is ``''``.
    """
    client, connectionDocker = tclientGated
    client.post(
        f"/api/sync/{S_CONTAINER_ID}/track",
        json={
            "sPath": "stepA/output.dat",
            "sService": "Github",
            "bTrack": True,
        },
    )
    _fnAssertWritesRanUnder(
        connectionDocker, mutationAdmission.S_ADMISSION_MODE_SYNCHRONOUS,
    )


@pytest.mark.falsification
def testTheGitIdentityWriteRunsUnderTheDrain(tclientGated):
    """POST /api/github/{id}/identity runs git config under mode (b).

    The marker is ``git config user.name``, specific to this route's own
    command: a loose marker was tried elsewhere in this file, matched the
    connect handler's own git probes, and turned the assertion into
    noise.

    Kills: reverting ``fdictGithubIdentity`` to
    ``await asyncio.to_thread(_ftWriteGitIdentity, ...)``. That exec then
    reaches the primitive with no admission, recording ``''``.
    """
    client, connectionDocker = tclientGated
    client.post(
        f"/api/github/{S_CONTAINER_ID}/identity",
        json={"sName": "A Researcher", "sEmail": "someone@example.org"},
    )
    _fnAssertExecsNamingRanUnder(
        connectionDocker, "git config user.name",
        mutationAdmission.S_ADMISSION_MODE_LOCK_HELD,
    )


@pytest.mark.falsification
def testTheSingleFileGithubPushRunsUnderTheDrain(tclientGated):
    """POST /api/github/{id}/add-file commits and pushes under mode (b).

    The marker is this request's own commit MESSAGE, not ``git add``.
    The dispatcher interposes four ``-c`` hardening flags between the
    program and its subcommand, so ``git add`` matches nothing and the
    assertion would have been vacuous -- which is the failure
    ``_fnAssertSelectedRanUnder`` reports rather than passes, and is how
    this marker came to be chosen.

    Kills: reverting ``fdictGithubAddFile`` to the coroutine chain it
    replaced, whose three ``to_thread`` hops reach the exec primitive
    with no admission open.
    """
    client, connectionDocker = tclientGated
    client.post(
        f"/api/github/{S_CONTAINER_ID}/add-file",
        json={
            "sFilePath": "stepA/output.dat",
            "sCommitMessage": "carried",
        },
    )
    _fnAssertExecsNamingRanUnder(
        connectionDocker, "commit -m 'carried'",
        mutationAdmission.S_ADMISSION_MODE_LOCK_HELD,
    )


@pytest.mark.falsification
def testTheRemoteVerifyRewritesItsCacheUnderTheDrain(tclientGated):
    """POST /api/sync/{id}/{service}/verify writes under mode (b).

    The verify's mutation is not its network call but the
    ``syncStatus.json`` it rewrites inside the project repo afterwards,
    which is what the Level-2 cells read. Selecting the WRITE rather
    than every primitive is deliberate: the route also READS the
    existing cache, and those reads cross the gate through the
    audited-read carve-out rather than under any carrier.

    Kills: reverting ``fnVerifyRemote`` to
    ``await asyncio.to_thread(fdictRunRemoteVerifyBlocking, ...)``.
    """
    client, connectionDocker = tclientGated
    with _fnRemoteVerifyStubbed("github"):
        client.post(f"/api/sync/{S_CONTAINER_ID}/github/verify")
    _fnAssertWritesRanUnder(
        connectionDocker, mutationAdmission.S_ADMISSION_MODE_LOCK_HELD,
    )


@pytest.mark.falsification
def testTheArxivConfigureSaveCommitsSynchronously(tclientGated):
    """The arXiv config's project.json save runs under mode (a).

    One of this handler's TWO carriers, and asserted on the workflow
    file alone so that a defect in the sibling mode-(b) carrier cannot
    also fail this test. Confirmed: removing the cache carrier fails
    only its own test, and this one still passes.

    The isolation is ONE-DIRECTIONAL, and saying so is the honest
    reading. Removing THIS carrier fails both tests, because the save
    runs first: an unadmitted write raises at the primitive, the
    handler 500s, and the verify that would have rewritten the cache
    never runs. That is sequencing, not a weak assertion -- no test can
    separate a downstream carrier from an upstream refusal in a
    straight-line handler -- so the diagnosis to remember is that BOTH
    arXiv tests failing means the SAVE, while only the second failing
    means the cache rewrite.

    Kills: reverting ``_fnPersistArxivConfig``'s
    ``fdictCommitWorkflowSave(...)`` to ``dictCtx["save"](...)``.
    """
    client, connectionDocker = tclientGated
    with _fnRemoteVerifyStubbed("arxiv"):
        client.post(
            f"/api/sync/{S_CONTAINER_ID}/arxiv/configure",
            json={"sArxivId": "2401.12345"},
        )
    _fnAssertSelectedRanUnder(
        connectionDocker,
        lambda dictReached: (
            dictReached["sPrimitive"] == S_PRIMITIVE_WRITE
            and dictReached["sPath"] == S_WORKFLOW_PATH
        ),
        mutationAdmission.S_ADMISSION_MODE_SYNCHRONOUS,
        f"write to {S_WORKFLOW_PATH}",
    )


@pytest.mark.falsification
def testTheArxivCacheRewriteRunsUnderTheDrain(tclientGated):
    """The arXiv config's sync-cache rewrite runs under mode (b).

    The handler's OTHER carrier. Saving the configuration and verifying
    against arXiv are two mutations, not one: the save is a synchronous
    single write whose bytes the journal can adjudicate, while the
    verify contacts a remote and rewrites a different file for as long
    as the network takes. A migration that carried only the save would
    leave this write refused at the primitive.

    Selected by NAMING the cache file rather than by excluding the
    workflow path. The synchronous save is atomic, so it also writes a
    ``.tmp`` sibling -- and a "not the workflow" selector picked that up
    and reported the SAVE's mode as this carrier's, which would have
    failed the test for a correct migration.

    Kills: reverting the verify hop to
    ``await asyncio.to_thread(_fdictRunArxivVerifyAfterConfig, ...)``.
    """
    client, connectionDocker = tclientGated
    with _fnRemoteVerifyStubbed("arxiv"):
        client.post(
            f"/api/sync/{S_CONTAINER_ID}/arxiv/configure",
            json={"sArxivId": "2401.12345"},
        )
    _fnAssertSelectedRanUnder(
        connectionDocker,
        lambda dictReached: (
            dictReached["sPrimitive"] == S_PRIMITIVE_WRITE
            and "syncStatus" in dictReached["sPath"]
        ),
        mutationAdmission.S_ADMISSION_MODE_LOCK_HELD,
        "write to the sync-status cache",
    )


# ---------------------------------------------------------------------
# Group 6 -- the two routes the researcher ruled are WRITES governed
# elsewhere (2026-08-05).
#
# Both reach no mutation-capable CONTAINER primitive, so `typed-read`
# would have passed its own rule -- and would have been the wrong
# record, because a reader takes `typed-read` to mean "this only
# looks", and both of these write. One writes to the researcher's own
# machine; the other writes hub state a browser poll then acts on. The
# researcher's words: "any user would think that a read-only command
# would also not write on the host machine."
#
# So the assertion for them is not a MODE. It is that the enforced
# branch is survivable without one -- an empty gated ledger -- paired
# with evidence the route did its work, so an early return cannot pass
# the first assertion vacuously. That pairing is the same discipline
# the plot-standards typed-read test uses, for the same reason.
# ---------------------------------------------------------------------

@pytest.mark.falsification
def testTheHostFilePullReachesNoMutatingContainerPrimitive(
    tclientGated, tmp_path,
):
    """POST /api/files/{id}/pull writes the HOST, never the container.

    Two assertions, because either alone is satisfiable by a defect.
    The gated ledger must be EMPTY -- the pull streams bytes out
    through ``get_archive`` and probes the source with a typed read,
    neither of which is mutation-capable -- and the file must actually
    have LANDED on the host, or a route that refused early would pass
    the first assertion perfectly while writing nothing.

    That the bytes land is also the whole reason this route is not
    declared ``typed-read``: it is a write, just not one the container
    carrier governs.

    Kills: reverting ``_fsPullContainerFileToHost``'s ``get_archive``
    stream to a ``docker cp`` assembled and run through the general
    exec primitive, which is bidirectional and therefore a container
    write however this call site uses it.
    """
    client, connectionDocker = tclientGated
    sHostDestination = str(tmp_path / "pulled.json")
    # HOME is redirected rather than the validator stubbed, so the real
    # ``_fnValidateHostDestination`` -- one of the two authorities this
    # route's declaration NAMES -- runs for real and admits the
    # destination on its own terms.
    with patch.dict(os.environ, {"HOME": str(tmp_path)}):
        response = client.post(
            f"/api/files/{S_CONTAINER_ID}/pull",
            json={
                "sContainerPath": S_WORKFLOW_PATH,
                "sHostDestination": sHostDestination,
            },
        )
    assert response.status_code == 200, response.text
    assert connectionDocker.listAdmittedPrimitives == [], (
        "a route declared separate-authority reached a "
        "mutation-capable container primitive: "
        f"{connectionDocker.listAdmittedPrimitives}"
    )
    assert os.path.exists(response.json()["sHostPath"]), (
        "the pull landed no file on the host, so the empty gated "
        "ledger above asserts nothing -- the route returned before "
        "doing the work this test exists to characterise"
    )


@pytest.mark.falsification
def testTheProjectCreationRequestMutatesOnlyHubState(tclientGated):
    """POST .../request-creation records a request and touches no container.

    The agent cannot create a project; it can only ask, and the
    researcher confirms in the wizard. So the mutation is entirely
    inside the hub's in-process request map -- which IS a mutation, and
    is why this is ``separate-authority`` rather than the literally-true
    ``typed-read``.

    Paired assertions again: an empty gated ledger is only meaningful
    beside evidence the request was actually recorded.

    Kills: making the handler create the project.json directly (any
    call reaching ``fnWriteFile``), which is the behaviour this route
    exists to refuse and which would show up in the gated ledger.
    """
    client, connectionDocker = tclientGated
    response = client.post(
        f"/api/workflows/{S_CONTAINER_ID}/request-creation",
        json={"sWorkflowName": "Asked For", "sRepoDirectory": "asked"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["bCreated"] is False, (
        "the agent must never be told a project was created"
    )
    assert connectionDocker.listAdmittedPrimitives == [], (
        "a route declared separate-authority reached a "
        "mutation-capable container primitive: "
        f"{connectionDocker.listAdmittedPrimitives}"
    )


# ---------------------------------------------------------------------
# Group 7 -- the git panel's six mutating routes, mode (b).
#
# Every one is a SEQUENCE of git commands against a remote or an index,
# so all six are mode (b): the drain is held for the worker's whole
# life, and a hand-over or a Run Step arriving mid-fetch is refused and
# told what is running rather than landing underneath a git process that
# keeps writing. Reconcile additionally saves project.json, so it
# declares mode (a) as well and its two carriers are asserted
# separately.
#
# The panel also carries the migration's first 5xx carry-back. A failed
# ``git fetch`` answers 502, and a 5xx raised inside a carrier worker
# poisons its journal record and QUARANTINES the container -- so before
# this group, a researcher whose network blipped would have been told to
# run ``vaibify reconcile``. That is asserted on the JOURNAL, not on the
# status code, for the same reason the repo-init refusal test is.
# ---------------------------------------------------------------------

S_MARKER_GIT_FETCH = "fetch --no-tags origin"
S_MARKER_GIT_PULL = "pull --ff-only"


def _fnAssertGitExecsRanUnderTheDrain(connectionDocker, sCommandMarker):
    """Assert every exec naming the marker ran lock-held."""
    _fnAssertExecsNamingRanUnder(
        connectionDocker, sCommandMarker,
        mutationAdmission.S_ADMISSION_MODE_LOCK_HELD,
    )


@pytest.mark.falsification
def testTheProjectRepoFetchRunsUnderTheDrain(tclientGated):
    """POST /api/git/{id}/fetch-project-repo runs git fetch under mode (b).

    The marker is this route's own ``fetch --no-tags origin``, not the
    bare word ``git``: the connect handler runs ``git rev-parse
    --show-toplevel`` against the same double, and a loose marker was
    what turned an assertion elsewhere in this file into noise.

    Kills: reverting ``_fdictFetchThenReadStatus`` to the coroutine
    chain it replaced, whose ``await asyncio.to_thread(...)`` hops reach
    the exec primitive with no admission open at all.
    """
    client, connectionDocker = tclientGated
    response = client.post(
        f"/api/git/{S_CONTAINER_ID}/fetch-project-repo",
        json={"bForce": True},
    )
    assert response.status_code == 200, response.text
    _fnAssertGitExecsRanUnderTheDrain(
        connectionDocker, S_MARKER_GIT_FETCH,
    )


@pytest.mark.falsification
def testTheProjectRepoPullRunsUnderTheDrain(tclientGated):
    """POST /api/git/{id}/pull-project-repo fast-forwards under mode (b).

    The dirty check and the pull share one held drain, which is the
    point: with the lock dropped between them a write lands in the gap
    and ``git pull --ff-only`` runs against a tree the check called
    clean. The double reports no dirty files, so the pull is reached.

    Kills: reverting ``_fdictCheckCleanThenFastForward`` to the
    ``await asyncio.to_thread(...)`` chain it replaced.
    """
    client, connectionDocker = tclientGated
    response = client.post(
        f"/api/git/{S_CONTAINER_ID}/pull-project-repo",
    )
    assert response.status_code == 200, response.text
    _fnAssertGitExecsRanUnderTheDrain(
        connectionDocker, S_MARKER_GIT_PULL,
    )


@pytest.mark.falsification
def testTheRemoteRefreshRunsUnderTheDrain(tclientGated):
    """POST /api/git/{id}/refresh-remotes fetches under mode (b).

    A separate shape from fetch-project-repo even though both begin
    with the same fetch: this one goes on to read the remote-heads view
    in the SAME worker, so a migration that carried only the fetch
    would leave three further execs refused at the primitive.

    Kills: reverting ``_fdictFetchThenCollectRemotes`` to the coroutine
    chain it replaced.
    """
    client, connectionDocker = tclientGated
    response = client.post(
        f"/api/git/{S_CONTAINER_ID}/refresh-remotes",
        json={"bForce": True},
    )
    assert response.status_code == 200, response.text
    _fnAssertGitExecsRanUnderTheDrain(
        connectionDocker, S_MARKER_GIT_FETCH,
    )
    _fnAssertGitExecsRanUnderTheDrain(
        connectionDocker, "git remote get-url origin",
    )


@pytest.mark.falsification
def testTheCanonicalCommitRunsUnderTheDrain(tclientGated):
    """POST /api/git/{id}/commit-canonical commits under mode (b).

    The manifest report is stubbed to name one file that needs
    committing. Without it the double's clean repo produces an empty
    needs-commit list, the route returns before ``git add`` ever runs,
    and the assertion would be about a commit that never happened --
    the vacuity ``_fnAssertSelectedRanUnder`` reports as a failure
    rather than passing. The stub is an upstream PURE function, so
    everything this test is about still runs for real.

    Kills: reverting ``_fdictScanThenCommitCanonical`` to the coroutine
    chain it replaced, whose ``git add`` and ``git commit`` reach the
    exec primitive with no admission open.
    """
    from vaibify.gui.routes import gitRoutes

    client, connectionDocker = tclientGated
    with patch.object(
        gitRoutes.manifestCheck, "fdictBuildManifestReportFromStatus",
        lambda dictGit, listTracked: {
            "listNeedsCommit": [{"sPath": "stepA/output.dat"}],
            "sHeadSha": "0" * 40,
        },
    ):
        response = client.post(
            f"/api/git/{S_CONTAINER_ID}/commit-canonical",
            json={"sCommitMessage": "carried canonical"},
        )
    assert response.status_code == 200, response.text
    # The marker carries the request's own commit MESSAGE and the
    # pathspec. ``git add`` matches nothing, because the dispatcher
    # interposes four ``-c`` hardening flags between the program and
    # its subcommand -- the same trap the add-file test records.
    _fnAssertGitExecsRanUnderTheDrain(
        connectionDocker, "commit -m 'carried canonical' -- ",
    )


def _fdictWorkflowWithAnAiDeclaration():
    """Return the draft workflow carrying one ai-declaration step.

    ``untrack-ai-declaration`` refuses 403 for any path the workflow
    does not itself declare, so without this the route answers before
    reaching a container primitive and its assertion is vacuous.
    """
    dictWorkflow = copy.deepcopy(DICT_WORKFLOW)
    dictWorkflow["listSteps"].append({
        "sName": "Declare AI Use",
        "sDirectory": "DeclareAIUse",
        "sStepKind": S_AI_DECLARATION_STEP_KIND,
        "sDeclarationFile": "AI_DECLARATION.md",
        "bPlotOnly": False,
        "bRunEnabled": True,
        "bInteractive": False,
        "saDataCommands": [],
        "saOutputDataFiles": [],
        "saTestCommands": [],
        "saPlotCommands": [],
        "saPlotFiles": [],
        "dictRunStats": {},
        "dictVerification": {
            "sUnitTest": "untested", "sUser": "untested",
        },
    })
    return dictWorkflow


class DockerDoubleServingAnAiDeclaration(
    DockerDoubleThatCallsTheRealGates,
):
    """The gated double over a workflow declaring a declaration file."""

    def fbaFetchFile(self, sContainerId, sPath, iMaxBytes=None):
        if sPath == S_WORKFLOW_PATH and sPath not in self._dictFiles:
            self._dictFiles[sPath] = json.dumps(
                _fdictWorkflowWithAnAiDeclaration(),
            ).encode("utf-8")
        return DockerDoubleThatCallsTheRealGates.fbaFetchFile(
            self, sContainerId, sPath, iMaxBytes,
        )


@pytest.fixture
def tclientDeclaring():
    """The gated client over a workflow with an ai-declaration step."""
    return _tConnectGatedClient(DockerDoubleServingAnAiDeclaration())


@pytest.mark.falsification
def testTheDeclarationUntrackRunsUnderTheDrain(tclientDeclaring):
    """POST /api/git/{id}/untrack-ai-declaration removes under mode (b).

    Three git commands that MUST share one drain: the dirty-index
    refusal is the only thing that makes the pathspec-free commit safe,
    and with the lock dropped between them another session stages a
    change in the gap and it rides into this commit.

    Kills: reverting ``_fdictRemoveDeclarationFromTheIndex`` to the
    coroutine chain it replaced.
    """
    client, connectionDocker = tclientDeclaring
    response = client.post(
        f"/api/git/{S_CONTAINER_ID}/untrack-ai-declaration",
        json={"sPath": "AI_DECLARATION.md"},
    )
    assert response.status_code == 200, response.text
    _fnAssertGitExecsRanUnderTheDrain(connectionDocker, "rm --cached")


@pytest.mark.falsification
def testTheRemoteReconcileFetchesUnderTheDrain(tclientGated):
    """POST /api/git/{id}/reconcile-remote-state fetches under mode (b).

    The first of this handler's TWO carriers, asserted on the fetch
    alone so a defect in the sibling mode-(a) save cannot also fail it.

    Kills: reverting the reconcile fetch to the
    ``await asyncio.to_thread(containerGit.ftResultGitFetchInContainer,
    ...)`` it replaced.
    """
    client, connectionDocker = tclientGated
    response = client.post(
        f"/api/git/{S_CONTAINER_ID}/reconcile-remote-state",
    )
    assert response.status_code == 200, response.text
    _fnAssertGitExecsRanUnderTheDrain(
        connectionDocker, S_MARKER_GIT_FETCH,
    )


@contextlib.contextmanager
def _fnGithubVerifyProvesOnePath():
    """Report a GitHub verify that covered every declared canonical path.

    Reconcile only writes ``dictSyncStatus`` when the cached verify
    proves the FULL canonical set, so without this its mode-(a) save
    never runs and the declaration would be unproven. Both stubs are
    pure readers upstream of the carrier: what the test is about --
    which admission the resulting project.json write runs under -- is
    untouched.
    """
    from vaibify.reproducibility import manifestWriter, scheduledReverify
    with patch.object(
        manifestWriter, "flistCollectCanonicalRepoPaths",
        lambda dictWorkflow: ["stepA/output.dat"],
    ), patch.object(
        scheduledReverify, "fdictReadCachedSyncStatus",
        lambda filesRepo, sService: {
            "sService": sService,
            "sLastVerified": _fsFreshIsoTimestamp(),
            "iTotalFiles": 1,
            "listDiverged": [],
        },
    ):
        yield


@pytest.mark.falsification
def testTheReconcileBookkeepingSaveCommitsSynchronously(tclientGated):
    """Reconcile's project.json save runs under mode (a).

    This handler's OTHER carrier. Recording what the verify proved is a
    single atomic write whose bytes the journal can adjudicate, which is
    a different mutation from the fetch that runs for as long as the
    network takes -- so it gets mode (a) rather than sharing the fetch's
    drain, and a migration that carried only the fetch would leave this
    write refused at the primitive.

    The isolation is ONE-DIRECTIONAL, as it is for the arXiv pair above.
    Removing the FETCH's carrier fails both tests, because the fetch
    runs first: an unadmitted exec raises at the primitive, the handler
    500s, and the save never runs. So the diagnosis to remember is that
    both reconcile tests failing means the FETCH, while only this one
    failing means the bookkeeping save.

    Kills: reverting ``_fdictReconcileSyncStatusFromVerify``'s
    ``fdictCommitWorkflowSave(...)`` to ``dictCtx["save"](...)``.
    """
    client, connectionDocker = tclientGated
    with _fnGithubVerifyProvesOnePath():
        response = client.post(
            f"/api/git/{S_CONTAINER_ID}/reconcile-remote-state",
        )
    assert response.status_code == 200, response.text
    _fnAssertSelectedRanUnder(
        connectionDocker,
        lambda dictReached: (
            dictReached["sPrimitive"] == S_PRIMITIVE_WRITE
            and dictReached["sPath"] == S_WORKFLOW_PATH
        ),
        mutationAdmission.S_ADMISSION_MODE_SYNCHRONOUS,
        f"write to {S_WORKFLOW_PATH}",
    )


@pytest.mark.falsification
def testAnUnreachableRemoteLeavesTheContainerUsable(tclientGated):
    """A 502 from inside a carrier worker must not quarantine anything.

    THE hazard this panel introduced, and the first 5xx the migration
    carries back as a value. Every git route here answers 502 when
    ``git fetch`` reports a non-zero exit, which for a researcher on a
    train is simply "the network is down" -- and a 5xx raised inside a
    carrier worker is settled through the failure path, which marks the
    journal record NEEDS RECONCILIATION and QUARANTINES the container.
    Losing a container because a fetch could not reach GitHub is not a
    trade anybody would make.

    So the assertion is on the JOURNAL, not on the status code. A test
    that only checked for 502 would pass just as happily against a
    handler that quarantined the container on its way to returning one,
    which is precisely the bug.

    Kills: emptying ``_SET_GIT_REMOTE_REFUSAL_STATUSES``, so the default
    4xx/5xx split re-raises the fetch failure inside the worker.
    """
    from vaibify.config import operationJournal
    from vaibify.gui import containerGit

    client, connectionDocker = tclientGated
    with patch.object(
        containerGit, "ftResultGitFetchInContainer",
        lambda docker, sContainerId, sWorkspace=None: (
            128, "fatal: unable to access 'https://example.invalid/'",
        ),
    ):
        response = client.post(
            f"/api/git/{S_CONTAINER_ID}/fetch-project-repo",
            json={"bForce": True},
        )
    assert response.status_code == 502, response.text

    dictResolution = operationJournal.fdictResolveContainerJournal(
        S_CONTAINER_NAME,
    )
    assert dictResolution["sResolution"] != (
        operationJournal.S_RESOLUTION_QUARANTINED
    ), (
        "an unreachable remote quarantined the container: "
        f"{dictResolution}. The researcher now has to run 'vaibify "
        "reconcile' because their network dropped."
    )


# ---------------------------------------------------------------------
# Group 7b -- the badge refresh, the first AUTOMATIC read to migrate.
#
# Everything migrated before this group was something a researcher
# CLICKED. The badge refresh is issued by the dashboard itself when a
# workflow opens and whenever the sync epoch bumps, which changes what
# a correct migration looks like in one way: it must never queue. Mode
# (b) waits for the drain, and waiting spends an unpredictable amount
# of a request nobody made -- a fetch or a step run can hold it for
# minutes, and the researcher sees a panel that has stopped answering.
#
# So the route asks for the drain and takes "" for an answer: a paused
# refresh is a 200 with a typed paused payload and NO badge map. Both
# directions are proven here, because they fail identically in the
# report and oppositely in the product -- a route that never pauses
# hangs, and a route that always pauses never shows a badge again.
#
# A container is busy in three states, and each has its own test
# because each is a separate branch that can be lost on its own: a live
# SUPERVISOR (below), a drain held by a NON-carrier such as reconcile
# (``testABadgeRefreshUnderAHeldDrainIsPausedRatherThanQueued``), and a
# live DURABLE run holding no drain at all
# (``testABadgeRefreshOverALiveDurableRunIsPaused``). The last two live
# beside the in-loop ASGI harness they need.
# ---------------------------------------------------------------------

S_MARKER_BADGE_STATUS = "status --porcelain=v2 --branch"


@pytest.mark.falsification
def testTheBadgeRefreshReadsUnderOneHeldDrain(tclientGated):
    """GET /api/git/{id}/badges reads under mode (b), not the ambient mint.

    The route's probes ran concurrently through ``asyncio.gather``
    before the migration; they are serialized into one worker now, so
    the coherent refresh is ONE carrier rather than three that would
    queue behind each other on the same container's drain.

    Kills: deleting the ``fdictRunAutomaticReadUnderTheDrain`` call and
    reading the badge inputs directly, which reaches the exec primitive
    with no admission at all.
    """
    client, connectionDocker = tclientGated
    response = client.get(f"/api/git/{S_CONTAINER_ID}/badges")
    assert response.status_code == 200, response.text
    _fnAssertExecsNamingRanUnder(
        connectionDocker, S_MARKER_BADGE_STATUS,
        mutationAdmission.S_ADMISSION_MODE_LOCK_HELD,
    )
    _fnAssertExecsNamingRanUnder(
        connectionDocker, "git remote get-url origin",
        mutationAdmission.S_ADMISSION_MODE_LOCK_HELD,
    )


def testTheBadgeRefreshStillAnswersItsWholePayload(tclientGated):
    """The container-mode regression the migration owes.

    This route changed for BOTH modes: host mode is why it migrated,
    and the container leg now runs the same probes serially inside a
    carrier's worker. A regression here would be silent -- the badge
    row renders from whatever the payload holds, so a missing key
    paints "no remote state" rather than failing.
    """
    client, _ = tclientGated
    response = client.get(f"/api/git/{S_CONTAINER_ID}/badges")
    assert response.status_code == 200, response.text
    dictBody = response.json()
    assert set(dictBody) == {"dictGit", "dictBadges", "listTracked"}, (
        f"the badge payload changed shape: {sorted(dictBody)}"
    )
    assert "sRemoteUrl" in dictBody["dictGit"]
    assert dictBody.get("bRefreshPaused") is None


@pytest.mark.falsification
@pytest.mark.asyncio
async def testABadgeRefreshOverALiveDeleteIsPausedRatherThanQueued():
    """A refresh arriving while the drain is HELD answers paused at once.

    Driven against the blocked-clean hub because that is the only
    machinery here that puts a REAL mode-(b) supervisor in the registry
    and holds the drain open while a second request arrives; a test
    that registered a supervisor of its own would be asserting against
    its own fixture.

    Four assertions, each a separate guarantee:

    1. the refresh ANSWERS while the delete is still blocked -- had it
       queued, the response would not exist until the clean released;
    2. it answers IMMEDIATELY, measured, because "did not deadlock" is
       also true of a read that waited nine seconds;
    3. it names the live operation, so the paused state is actionable
       rather than a bare "busy";
    4. it reached NO container primitive, which is what makes the pause
       a pause rather than a label on a read that happened anyway.

    Kills: passing ``bPauseWhenBusy=False`` from
    ``fdictRunAutomaticReadUnderTheDrain``, which returns the route to
    queueing behind the live delete.
    """
    app, connectionDocker = _tBuildAsgiHubWithBlockedClean()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=app, raise_app_exceptions=False,
        ),
        base_url="http://hub",
        headers={"X-Session-Token": fsBootstrapCredential(app)},
    ) as clientAsync:
        sLease = await _tConnectOverAsgi(clientAsync)
        clientAsync.headers["X-Vaibify-Lease"] = sLease

        taskClean = asyncio.ensure_future(
            clientAsync.post(f"/api/pipeline/{S_CONTAINER_ID}/clean"),
        )
        await asyncio.to_thread(
            connectionDocker.eventCleanStarted.wait, 10,
        )
        connectionDocker.listAdmittedPrimitives.clear()

        fBefore = time.monotonic()
        response = await clientAsync.get(
            f"/api/git/{S_CONTAINER_ID}/badges",
        )
        fElapsed = time.monotonic() - fBefore

        assert connectionDocker.listCleanCommandsRun == [], (
            "the delete finished before the refresh was answered, so "
            "the container was not busy when it mattered and this test "
            "would prove nothing"
        )
        assert response.status_code == 200, response.text
        dictBody = response.json()
        assert dictBody.get("bRefreshPaused") is True, dictBody
        assert fElapsed < 2.0, (
            f"the refresh waited {fElapsed:.1f}s on the busy container "
            "instead of pausing at once"
        )
        assert "clean-outputs" in dictBody["sPausedBy"], (
            "the paused payload must NAME what holds the container, "
            f"from what the lock holder registered: {dictBody}"
        )
        assert "dictBadges" not in dictBody, (
            f"a paused refresh answered with a badge map: {dictBody}. "
            "An empty one renders as a claim about the repository."
        )
        assert connectionDocker.listAdmittedPrimitives == [], (
            "a paused refresh still reached a container primitive: "
            f"{connectionDocker.listAdmittedPrimitives}. Pausing is "
            "only worth anything if the read did not happen."
        )

        connectionDocker.eventCleanMayFinish.set()
        await taskClean


@pytest.mark.falsification
def testAQuietContainerIsNeverReportedAsBusy(tclientGated):
    """The other direction: an idle container must not pause the refresh.

    The pause's failure modes are symmetric and only one of them is
    loud. A read that never pauses hangs a request behind live work; a
    read that ALWAYS pauses answers instantly forever, and the badges
    simply stop changing -- a dashboard that has quietly stopped
    reporting, which is the failure this repository's rules single out
    as the worst kind.

    Kills: making the busy probe unconditional -- e.g. reporting the
    asking supervisor itself as the live work, which
    ``_fsDescribeWorkBesidesThisSupervisor`` excludes for exactly this
    reason.
    """
    client, _ = tclientGated
    response = client.get(f"/api/git/{S_CONTAINER_ID}/badges")
    assert response.status_code == 200, response.text
    dictBody = response.json()
    assert "bRefreshPaused" not in dictBody, (
        f"an idle container reported itself busy: {dictBody}. Every "
        "badge refresh from now on would answer paused and the panel "
        "would never update again."
    )
    assert "dictBadges" in dictBody


# ---------------------------------------------------------------------
# Group 7c -- the rest of the host activation surface that could move.
#
# Opening a workflow fires four automatic reads. Badges is above; these
# are the settings load and the pipeline-state recovery, and they
# migrate for opposite reasons.
#
# Settings reaches NO container primitive at all -- it answers from the
# workflow the hub already holds -- so it declares typed-read, and the
# proof is an empty ledger.
#
# Pipeline state is a typed read that occasionally WRITES: when a
# runner's heartbeat has gone stale the reader reconciles the file so
# the dashboard stops claiming a dead run is live. That write used to
# leave on the background lane, where the gate is a documented no-op.
# It is carried now, and the carrier opens ONLY on that branch -- which
# is what lets a route polled every ten seconds declare mode (b)
# without holding a drain on a timer. Both directions are asserted,
# because "the poll holds no drain" and "the reconcile is carried" are
# separately losable.
#
# The two reads that did NOT move are the repos-panel status and the
# file-status poll. Both run on a five-second timer and both reach a
# container WRITE, so a carrier on either would hold the drain on a
# timer -- and live carrier work is what the run-dispatch gate refuses
# a Run Step against. That is the Run-Step-always-refused shape this
# repository has already shipped once, so they need the read redesigned
# rather than wrapped, and they stay awaiting until that is decided.
# ---------------------------------------------------------------------

S_STATE_PATH = "/workspace/.vaibify/pipeline_state.json"


def testTheSettingsReadOpensNoContainerConnectionAtAll(tclientGated):
    """GET /api/settings/{id} is typed-read in its strongest form.

    Not "reaches only typed reads" but "reaches nothing": the handler
    answers from the in-memory workflow. The declaration would be a
    lie the moment this route grew a container call, and an empty
    ledger is the only assertion that notices.
    """
    client, connectionDocker = tclientGated
    response = client.get(f"/api/settings/{S_CONTAINER_ID}")
    assert response.status_code == 200, response.text
    assert connectionDocker.listAdmittedPrimitives == [], (
        "the settings read reached a container primitive: "
        f"{connectionDocker.listAdmittedPrimitives}. It declares "
        "typed-read, which claims it reaches no mutation-capable "
        "primitive at all."
    )


@pytest.mark.falsification
def testTheOrdinaryStatePollHoldsNoDrain(tclientGated):
    """The ten-second poll opens no carrier on its ordinary path.

    The draft double serves no state file, so the read returns "not
    running" and the reconcile branch is never taken — which is the
    ordinary case, ninety-nine polls in a hundred. If this route
    carried unconditionally it would hold the container's mutation
    drain every ten seconds, and a Run Step arriving in that window is
    refused by the dispatch gate against live carrier work.

    Kills: hoisting the carrier out of the persister and around the
    whole handler.
    """
    client, connectionDocker = tclientGated
    response = client.get(f"/api/pipeline/{S_CONTAINER_ID}/state")
    assert response.status_code == 200, response.text
    listCarried = [
        dictReached
        for dictReached in connectionDocker.listAdmittedPrimitives
        if dictReached["sMode"] == (
            mutationAdmission.S_ADMISSION_MODE_LOCK_HELD
        )
    ]
    assert listCarried == [], (
        "an ordinary state poll held the drain: "
        f"{listCarried}. Every tenth second, for as long as a workflow "
        "is open, a Run Step would race it."
    )


class DockerDoubleServingAStaleRunnerState(
    DockerDoubleThatCallsTheRealGates,
):
    """The gate-faithful double over a state file whose runner died.

    ``bRunning`` is still true and the heartbeat is an hour old, which
    is exactly what a killed runner leaves behind: nothing writes
    ``bRunning: False`` on the way out of a SIGKILL. The reader is what
    notices, and its correction is a container write.
    """

    def fbaFetchFile(self, sContainerId, sPath, iMaxBytes=None):
        if sPath == S_STATE_PATH:
            return json.dumps({
                "bRunning": True,
                "iCurrentStep": 1,
                "sLastHeartbeat": (
                    datetime.now(timezone.utc) - timedelta(hours=1)
                ).isoformat(),
            }).encode("utf-8")
        return super().fbaFetchFile(sContainerId, sPath, iMaxBytes)


@pytest.fixture
def tclientStaleRunner():
    """The gated client over a container whose runner stopped beating."""
    return _tConnectGatedClient(DockerDoubleServingAStaleRunnerState())


@pytest.mark.falsification
def testTheStaleHeartbeatReconcileWritesUnderTheDrain(tclientStaleRunner):
    """The rare branch: recording a dead runner is a carried mutation.

    The write is what makes the dashboard stop claiming a finished run
    is live, so it is not optional and it is not a read. Before this
    migration it left through ``_fnPersistReconciledOnTheBackgroundLane``,
    where the commit guard is a no-op by design — an unrecorded
    container write on the hub's most frequent poll.

    Kills: dropping ``fnPersistReconciled`` from the route's call, which
    returns the reconciling write to the background lane.
    """
    client, connectionDocker = tclientStaleRunner
    response = client.get(f"/api/pipeline/{S_CONTAINER_ID}/state")
    assert response.status_code == 200, response.text
    assert response.json()["bRunning"] is False, (
        "the reader did not reconcile the stale heartbeat, so no write "
        f"was ever due and this asserts nothing: {response.json()}"
    )
    _fnAssertSelectedRanUnder(
        connectionDocker,
        lambda dictReached: (
            dictReached["sPrimitive"] == S_PRIMITIVE_WRITE
            and dictReached["sPath"].startswith(S_STATE_PATH)
        ),
        mutationAdmission.S_ADMISSION_MODE_LOCK_HELD,
        "pipeline-state reconcile write",
    )


# ---------------------------------------------------------------------
# Group 8 -- the three step routes that are not a plain save, mode (b).
#
# Every OTHER step route persists project.json and nothing else, which
# is why Group 1 could parametrize six of them over one assertion.
# These three each reach the container for something else first: the
# rename runs a git mv cascade, the alignment runs that cascade once per
# nonconforming step, and the step update reads the AICS level before
# and after its edit so the auto-archive can tell whether the edit
# promoted the workflow. None of those is a write with a hash the
# journal can check afterwards, and every one of them can run for as
# long as a git process takes, so they are mode (b) and the drain is
# held for the WORKER's life rather than for a function call.
#
# The step update declares BOTH modes, and the two are asserted
# separately -- its save still goes through ``fdictCommitWorkflowSave``, so
# a regression in the save and a regression in the surrounding drain are
# different defects and must fail different tests.
# ---------------------------------------------------------------------

# The draft harness's step is ALREADY nonconforming: "Step A" slugs to
# "StepA" and its directory is "stepA". What it lacks is a project repo,
# without which every cascade refuses before touching the container --
# so the alignment batch would run its carrier, skip its one step, save
# nothing, and reach no primitive at all. The test would then pass
# having exercised the cascade's absence.
#
# The declared script is given as a COMMAND, not as ``saStepScripts``
# directly: that array is transient and ``fnAttachComputedTrackedPaths``
# recomputes it from the step's commands on every load, so a hard-coded
# value is overwritten before any route sees it. Setting it directly was
# tried first and the dry run reported no matching script at all.
DICT_WORKFLOW_FOR_CASCADE = copy.deepcopy(DICT_WORKFLOW)
DICT_WORKFLOW_FOR_CASCADE["sProjectRepoPath"] = S_PROJECT_REPO
DICT_WORKFLOW_FOR_CASCADE["listSteps"][0]["saDataCommands"] = [
    "python analyze.py",
]


class DockerDoubleForTheRenameCascade(DockerDoubleThatCallsTheRealGates):
    """The gate-faithful double, with a step directory that can move.

    Two departures from the parent, both required to reach the cascade
    at all:

    * the workflow it serves carries ``sProjectRepoPath`` and a declared
      script, so the cascade gets past its own precondition checks and
      the dry run has something to grep;
    * ``test -e`` answers NON-zero. The parent falls through to
      ``(0, "")`` for any command it does not recognise, which
      ``_fnMoveStepDirectory`` reads as "the destination already
      exists" -- so against the unmodified double every rename refuses
      with a 409 before it moves anything. That refusal is worth
      testing, and is (see the carried-refusal test below), but a
      success path has to be reachable too or the drain would only ever
      be asserted around a no-op.
    """

    def ftResultExecuteCommand(
        self, sContainerId, sCommand, sWorkdir=None,
    ):
        if sCommand.startswith("test -e "):
            mutationAdmission.fnAssertContainerCommandAdmitted(
                sContainerId, S_PRIMITIVE_EXEC,
            )
            self._fnRecordLiveAdmission(
                sContainerId, S_PRIMITIVE_EXEC, sCommand,
            )
            return (1, "")
        return super().ftResultExecuteCommand(
            sContainerId, sCommand, sWorkdir,
        )

    def fbaFetchFile(self, sContainerId, sPath, iMaxBytes=None):
        if sPath == S_WORKFLOW_PATH:
            return json.dumps(DICT_WORKFLOW_FOR_CASCADE).encode("utf-8")
        return super().fbaFetchFile(sContainerId, sPath, iMaxBytes)


@pytest.fixture
def tclientCascade():
    """The gated client over a workflow whose step directory can move."""
    return _tConnectGatedClient(DockerDoubleForTheRenameCascade())


@pytest.mark.falsification
def testTheStepRenameCascadeRunsUnderTheDrain(tclientCascade):
    """POST .../rename moves the directory under a mode-(b) admission.

    The marker is the move command itself rather than any exec, because
    the same request also runs the workflow save's own commands: a
    handler that reached only THOSE under the drain would pass a loose
    assertion while the git mv -- the irreversible half -- ran
    unadmitted.

    Kills: replacing ``_fdictApplyRenameUnderTheDrain``'s
    fgenericRunWorkerUnderTheDrain call with a direct call to its worker.
    """
    client, connectionDocker = tclientCascade
    response = client.post(
        f"/api/steps/{S_CONTAINER_ID}/0/rename",
        json={"sNewName": "Renamed Step", "bDryRun": False},
    )
    assert response.status_code == 200, response.text
    _fnAssertExecsNamingRanUnder(
        connectionDocker, "git mv",
        mutationAdmission.S_ADMISSION_MODE_LOCK_HELD,
    )


@pytest.mark.falsification
def testTheRenameCascadeSaveSharesTheCascadesDrain(tclientCascade):
    """The rename's project.json save runs inside the SAME admission.

    Not a duplicate of the assertion above. The cascade rewrites the
    step's directory, output paths and declared binaries in memory and
    the save is what makes that survive a reload, so the two have to
    commit together: a save left outside the worker would drop the drain
    between "the bytes moved" and "the workflow says so", and another
    session writing in that gap would produce a project.json that points
    at a directory which no longer exists.

    Kills: moving ``dictCtx["save"](sContainerId, dictWorkflow)`` out of
    ``fdictRenameThenSave`` and back into the handler, after the await.
    """
    client, connectionDocker = tclientCascade
    response = client.post(
        f"/api/steps/{S_CONTAINER_ID}/0/rename",
        json={"sNewName": "Renamed Step", "bDryRun": False},
    )
    assert response.status_code == 200, response.text
    _fnAssertWritesRanUnder(
        connectionDocker, mutationAdmission.S_ADMISSION_MODE_LOCK_HELD,
    )


@pytest.mark.falsification
def testTheRenamePreviewScanRunsUnderTheDrain(tclientCascade):
    """The dry run's script scan is carried, though it changes nothing.

    A preview that cannot run is worse than no preview: the researcher
    is asked to confirm a rename without being shown which scripts spell
    the old directory out. The scan greps one declared script per
    container round-trip, and the gate treats an exec as mutating
    because a primitive handed command text cannot know what the text
    does -- so on the enforced branch an uncarried preview is refused
    outright.

    Kills: replacing ``_flistScanScriptsUnderTheDrain``'s
    fgenericRunWorkerUnderTheDrain call with a direct call to
    ``stepRename.flistScanScriptsForOldName``.
    """
    client, connectionDocker = tclientCascade
    response = client.post(
        f"/api/steps/{S_CONTAINER_ID}/0/rename",
        json={"sNewName": "Renamed Step", "bDryRun": True},
    )
    assert response.status_code == 200, response.text
    assert response.json()["listScriptWarnings"] == ["stepA/analyze.py"], (
        "the scan reported no matching script, so the exec assertion "
        "below would be about a loop that never ran"
    )
    _fnAssertExecsNamingRanUnder(
        connectionDocker, "grep -Iq",
        mutationAdmission.S_ADMISSION_MODE_LOCK_HELD,
    )


@pytest.mark.falsification
def testTheDirectoryAlignmentBatchRunsUnderTheDrain(tclientCascade):
    """POST .../align-directories runs its whole batch under one drain.

    One admission for the batch, not one per step, is the property under
    test: every iteration rewrites the SAME workflow dict and the batch
    ends in a single save, so a drain dropped between two steps would
    let another session's save be silently overwritten by the one at the
    end.

    Kills: replacing ``_fdictAlignDirectoriesUnderTheDrain``'s
    fdictRunLockHeldMutation call with a direct call to
    ``_fdictAlignEveryNonconformingStep``.
    """
    client, connectionDocker = tclientCascade
    response = client.post(
        f"/api/steps/{S_CONTAINER_ID}/align-directories",
    )
    assert response.status_code == 200, response.text
    assert len(response.json()["listAligned"]) == 1, (
        "the batch aligned nothing, so it reached no cascade and the "
        f"admission assertion would be vacuous: {response.json()}"
    )
    _fnAssertExecsNamingRanUnder(
        connectionDocker, "git mv",
        mutationAdmission.S_ADMISSION_MODE_LOCK_HELD,
    )


@pytest.mark.falsification
def testATakenStepNameIsRefusedWithoutQuarantiningTheContainer(tclientGated):
    """A 409 from inside the rename worker must not take the container out.

    The rename's refusals live BELOW the container boundary -- the
    cascade asks the container whether the destination directory exists
    and raises ``ValueError`` when it does -- and a carrier worker that
    RAISES is settled through the failure path, which marks its journal
    record NEEDS RECONCILIATION. Choosing a name that is already taken
    would then cost the researcher their container until they ran
    ``vaibify reconcile``.

    That the 409 is safe to carry was read out of
    ``stepRename.fdictApplyStepRename``, not inferred from its shape:
    three of its four ``ValueError`` paths fire before anything moves,
    and the fourth is reached only through the cascade's own rollback,
    which puts the directory back before the error escapes. Every
    remaining failure is a 500 and still poisons.

    The default double is deliberate here: it answers ``test -e``
    affirmatively, so the destination reads as already present and the
    409 fires on the real path rather than a patched one.

    Kills: dropping ``fdictCarryARefusalBackInsteadOfRaising`` from
    ``fdictApplyTheRename``, so the 409 is re-raised inside the worker.
    """
    from vaibify.config import operationJournal

    client, _connectionDocker = tclientGated
    response = client.post(
        f"/api/steps/{S_CONTAINER_ID}/0/rename",
        json={"sNewName": "Renamed Step", "bDryRun": False},
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
        "a name collision quarantined the container: "
        f"{dictResolution}. The researcher now has to run 'vaibify "
        "reconcile' because they picked a name that was taken."
    )


@pytest.mark.falsification
def testAnUnrecoverableSplitSavesUnderTheDrainAndThenPoisons(tclientCascade):
    """The split's save LANDS, and only then does the record poison.

    The one place in this migration where a carrier must do both. The
    directory moved and could not be put back, so the workflow dict now
    records where the bytes actually are -- and that is the ONLY pointer
    the researcher has to the repair, surfaced as the nonconforming
    warning on the next load. It must be persisted. But the container is
    also genuinely split, which is exactly the unknown state the
    quarantine exists for.

    So both halves are asserted, and in order: the workflow file was
    actually WRITTEN, and the journal then resolved to QUARANTINED.
    Asserting only the quarantine would pass against a handler that
    poisoned without saving -- which is the older bug this route already
    carries a falsification for -- and asserting only the save would
    pass against one that settled the record normally and left a split
    container looking healthy.

    What is deliberately NOT asserted here is the admission MODE of that
    write. Which drain the save shares is the neighbouring test's
    guarantee, and duplicating it here would make every mutant that
    changes the cascade's mode fail this test too, so neither kill would
    isolate anything.

    Kills: reordering ``fdictRenameThenSave``'s split branch to raise
    before it saves.
    """
    from vaibify.config import operationJournal
    from vaibify.gui import stepRename

    client, connectionDocker = tclientCascade

    def fdictRaiseSplit(
        connectionDockerArg, sContainerIdArg, filesRepoArg,
        dictWorkflowArg, iStepIndexArg, dictPlanArg, sWorkflowPathArg,
    ):
        dictWorkflowArg["listSteps"][iStepIndexArg]["sDirectory"] = "Moved"
        raise stepRename.StepRenameSplitError(
            "directory moved, undo failed",
        )

    with patch.object(
        stepRename, "fdictApplyStepRename", fdictRaiseSplit,
    ):
        response = client.post(
            f"/api/steps/{S_CONTAINER_ID}/0/rename",
            json={"sNewName": "Renamed Step", "bDryRun": False},
        )
    assert response.status_code == 500, response.text

    listWorkflowWrites = [
        dictReached
        for dictReached in connectionDocker.listAdmittedPrimitives
        if dictReached["sPrimitive"] == S_PRIMITIVE_WRITE
        and dictReached["sPath"] == S_WORKFLOW_PATH
    ]
    assert listWorkflowWrites, (
        "the split raised without persisting the workflow, so the "
        "nonconforming directory it recorded lives only in memory and "
        "the next load shows a step that looks healthy. The ledger is "
        f"{connectionDocker.listAdmittedPrimitives}"
    )
    dictResolution = operationJournal.fdictResolveContainerJournal(
        S_CONTAINER_NAME,
    )
    assert dictResolution["sResolution"] == (
        operationJournal.S_RESOLUTION_QUARANTINED
    ), (
        "a container whose step directory split from its workflow "
        f"settled clean: {dictResolution}. Nothing will tell the "
        "researcher the two disagree."
    )


@pytest.mark.falsification
def testTheStepUpdateSaveCommitsThroughTheSynchronousCarrier(tclientGated):
    """PUT /api/steps/{id}/{index} writes project.json under mode (a).

    Mode (a) for the save specifically, inside the mode-(b) drain the
    rest of the route holds. The distinction is not cosmetic: the
    synchronous carrier journals a ``file-write`` record whose expected
    hash IS the workflow's serialization fingerprint, so a crash inside
    the commit window can be adjudicated afterwards by hashing the file
    on disk. Folding the save into the surrounding ``helper`` record
    would journal something that proves nothing about the bytes.

    Kills: reverting ``fnUpdateSaveAndArchive``'s
    ``fdictCommitWorkflowSave(...)`` call to
    ``dictCtx["save"](sContainerId, dictWorkflow)``.
    """
    client, connectionDocker = tclientGated
    response = client.put(
        f"/api/steps/{S_CONTAINER_ID}/0",
        json={"saDataCommands": ["python analyze.py"]},
    )
    assert response.status_code == 200, response.text
    _fnAssertWritesRanUnder(
        connectionDocker, mutationAdmission.S_ADMISSION_MODE_SYNCHRONOUS,
    )


@pytest.mark.falsification
def testTheStepUpdateHoldsTheDrainAcrossItsLevelReadings(tclientGated):
    """The edit's before/after level readings share one held drain.

    ``fbMaybeAutoArchive`` archives on a TRANSITION -- the workflow was
    below L1 and is now at or above it -- so it compares a level read
    taken before the edit with one taken after. Both readings hash the
    repo through the container, so both are guarded operations, and if
    the drain is dropped between them another session's write moves the
    level in the gap: the promotion is then detected, or missed, for a
    change the researcher never made.

    Asserted at the level read's own call site rather than through the
    save, so a regression in the save's carrier lands on the mode-(a)
    test above and this one alone reports the drain.

    THE LEVEL GATE IS STUBBED HERE, AND THAT IS THE POINT. Against this
    harness the real ``fiProofLevel`` declines at L1's first criterion --
    the one step is ``untested``/``untested``, so it is not user-approved
    -- and returns 0 without ever reaching the container. Asserting on
    the primitive ledger would then be an assertion about a read that
    never happened, which is the vacuity this file exists to avoid. The
    stub is not a stand-down: it replaces only the gate, is called by the
    real handler inside the real carrier, and reads the live admission
    contextvar at the exact statement whose admission is in question.

    Kills: replacing ``_fnUpdateThenArchiveUnderTheDrain``'s
    fgenericRunWorkerUnderTheDrain call with a direct call to its worker.
    """
    from vaibify.gui.routes import stepRoutes

    client, _connectionDocker = tclientGated
    listModesAtTheLevelRead = []

    def fiRecordTheLiveAdmission(
        dictWorkflowArg, filesRepoArg, dictScriptStatus=None,
    ):
        admission = mutationAdmission.fadmissionActiveForContainerId(
            S_CONTAINER_ID,
        )
        listModesAtTheLevelRead.append(
            "" if admission is None else admission.sMode,
        )
        return 0

    with patch.object(
        stepRoutes, "fiProofLevel", fiRecordTheLiveAdmission,
    ):
        response = client.put(
            f"/api/steps/{S_CONTAINER_ID}/0",
            json={"saDataCommands": ["python analyze.py"]},
        )
    assert response.status_code == 200, response.text
    assert listModesAtTheLevelRead == [
        mutationAdmission.S_ADMISSION_MODE_LOCK_HELD,
    ], (
        "the pre-edit level read did not run under a lock-held "
        f"admission: {listModesAtTheLevelRead}. Under "
        f"{mutationAdmission.S_ADMISSION_MODE_REQUEST!r} the route is "
        "still riding the legacy ambient mint; under '' it read the "
        "level with no admission at all, and an empty list means the "
        "handler never read it."
    )


# ---------------------------------------------------------------------
# Group 9 -- the Replay axis's five remaining routes.
#
# Four write into the container and one does not touch it at all. The
# four split three ways rather than one, and the split is the point:
#
# * the plain context UPDATE is a single write with a hash the journal
#   can adjudicate, so it is mode (a) -- but through its OWN record, not
#   through fdictCommitWorkflowSave, whose expected hash belongs to
#   project.json;
# * the TEMPLATE and the IMPORT are probe-then-write sequences whose
#   probe IS the guard ("only if absent"), so each holds one drain
#   across both halves or two sessions both read absent and one
#   silently overwrites the other;
# * the prompt-record CAPTURE is unbounded work over every transcript in
#   the container, so the drain is held for the worker's life.
#
# The fifth, the personal-layer hash, reaches the container not at all
# and is recorded separate-authority. Its assertion is the inverse of
# the others' and is written to be non-vacuous in both directions: the
# gated ledger must be EMPTY and the response must carry a real hash.
# ---------------------------------------------------------------------

S_CONTEXT_ABS_PATH = S_PROJECT_REPO + "/.vaibify/AGENTS.md"
S_ADOPTABLE_ROOT_PATH = S_PROJECT_REPO + "/AGENTS.md"

DICT_WORKFLOW_FOR_REPLAY = copy.deepcopy(DICT_WORKFLOW)
DICT_WORKFLOW_FOR_REPLAY["sProjectRepoPath"] = S_PROJECT_REPO
DICT_WORKFLOW_FOR_REPLAY["dictAiProvenance"] = {
    "dictPromptRecord": {"bEnabled": True, "bFirstCaptureReviewed": True},
}


class DockerDoubleForTheReplayAxis(DockerDoubleThatCallsTheRealGates):
    """The gate-faithful double over a workflow with a project repo.

    Every context route resolves its target through
    ``sProjectRepoPath`` and answers 400 without one, so the plain draft
    workflow would make all four of these tests assert nothing. The
    prompt record is pre-enabled for the same reason: the capture route
    refuses 409 while it is off, and driving the sibling CONFIGURE route
    to turn it on first would make a defect in that route's carrier fail
    the capture's test too.
    """

    def fbaFetchFile(self, sContainerId, sPath, iMaxBytes=None):
        if sPath == S_WORKFLOW_PATH:
            return json.dumps(DICT_WORKFLOW_FOR_REPLAY).encode("utf-8")
        return super().fbaFetchFile(sContainerId, sPath, iMaxBytes)


@pytest.fixture
def tclientReplay():
    """The gated client over a Replay-axis workflow with a repo path."""
    return _tConnectGatedClient(DockerDoubleForTheReplayAxis())


@pytest.mark.falsification
def testTheProjectContextUpdateCommitsThroughTheSynchronousCarrier(
    tclientReplay,
):
    """PUT .../project-context writes AGENTS.md under mode (a).

    Mode (a) rather than (b) because this is one write whose expected
    hash IS the sha256 of the bytes going in, so a crash inside the
    commit window resolves to "landed" or "did not" instead of to a
    quarantine.

    Kills: replacing ``_fdictCommitContextWrite``'s
    fdictCommitSynchronousMutation call with a direct call to
    ``_fnWriteContextFile``.
    """
    client, connectionDocker = tclientReplay
    response = client.put(
        f"/api/workflow/{S_CONTAINER_ID}/project-context",
        json={"sContent": "# Standing instructions\n"},
    )
    assert response.status_code == 200, response.text
    _fnAssertSelectedRanUnder(
        connectionDocker,
        lambda dictReached: (
            dictReached["sPrimitive"] == S_PRIMITIVE_WRITE
            and dictReached["sPath"] == S_CONTEXT_ABS_PATH
        ),
        mutationAdmission.S_ADMISSION_MODE_SYNCHRONOUS,
        "write of the project-context file",
    )


@pytest.mark.falsification
def testTheContextTemplateProbeAndWriteShareOneDrain(tclientReplay):
    """POST .../project-context/template holds one drain across both.

    The probe is the guard -- this route exists to create the file ONLY
    if it is absent -- so the assertion is that the probe's own exec and
    the write ran under the SAME lock-held admission. With the drain
    dropped between them two sessions both read absent, and the second
    overwrites a context the first researcher just wrote.

    Kills: replacing ``_fnWriteTheTemplateUnderTheDrain``'s
    fgenericRunWorkerUnderTheDrain call with a direct call to its worker.
    """
    client, connectionDocker = tclientReplay
    response = client.post(
        f"/api/workflow/{S_CONTAINER_ID}/project-context/template",
    )
    assert response.status_code == 200, response.text
    _fnAssertSelectedRanUnder(
        connectionDocker,
        lambda dictReached: (
            dictReached["sPrimitive"] == S_PRIMITIVE_WRITE
            and dictReached["sPath"] == S_CONTEXT_ABS_PATH
        ),
        mutationAdmission.S_ADMISSION_MODE_LOCK_HELD,
        "write of the context template",
    )


@pytest.mark.falsification
def testTheContextImportRePointsTheRootUnderTheSameDrain(tclientReplay):
    """POST .../project-context/import writes and re-links as one.

    The symlink replacement is why this matters more than the other
    context routes. It deletes the adopted root file and recreates it
    pointing at the canonical context, so between the write and the
    symlink the repository holds two real files with different contents
    -- exactly the divergence the symlink exists to prevent. Asserting
    the ``ln -s`` exec specifically, rather than any exec, is what makes
    the claim about that window rather than about the write alone.

    Kills: replacing ``_fnImportTheContextUnderTheDrain``'s
    fgenericRunWorkerUnderTheDrain call with a direct call to its worker.
    """
    client, connectionDocker = tclientReplay
    connectionDocker._dictFiles[S_ADOPTABLE_ROOT_PATH] = (
        b"# adopted from the repo root\n"
    )
    response = client.post(
        f"/api/workflow/{S_CONTAINER_ID}/project-context/import",
        json={"bAdoptRepoRoot": True, "sRootBasename": "AGENTS.md"},
    )
    assert response.status_code == 200, response.text
    _fnAssertExecsNamingRanUnder(
        connectionDocker, "ln -s",
        mutationAdmission.S_ADMISSION_MODE_LOCK_HELD,
    )


@pytest.mark.falsification
def testAnUnadoptableRootFileIsRefusedWithoutQuarantining(tclientReplay):
    """The import's 404 must not take the container out of service.

    The refusal is decided INSIDE the worker -- the route asks the
    container whether the named root file exists -- so it travels the
    carry-back path, and a raise there would settle through the failure
    path and quarantine the container for the ordinary case of naming a
    file that is not in the repository.

    Kills: dropping ``fdictCarryARefusalBackInsteadOfRaising`` from
    ``fdictRunTheImport``, so the 404 is re-raised inside the worker.
    """
    from vaibify.config import operationJournal

    client, _connectionDocker = tclientReplay
    response = client.post(
        f"/api/workflow/{S_CONTAINER_ID}/project-context/import",
        json={"bAdoptRepoRoot": True, "sRootBasename": "CLAUDE.md"},
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
        "asking to adopt a file that is not there quarantined the "
        f"container: {dictResolution}"
    )


@pytest.mark.falsification
def testThePromptRecordCaptureRunsUnderTheDrain(tclientReplay):
    """POST .../prompt-record/capture runs its pass under mode (b).

    This carrier was reached by NO test when it was written -- verified
    by planting an unconditional raise in it and watching every replay,
    project-context and transcript test stay green. A carrier nothing
    executes is indistinguishable from one that was never added.

    The capture PASS is stubbed, and the stub is not a stand-down: it
    replaces only ``fdictRunCapturePass``, is called by the real handler
    inside the real carrier, and does a real container write through the
    real gate, so the admission asserted below is the one production
    would have. It is stubbed because the genuine pass reads every agent
    transcript in the container, and this harness models no transcripts
    at all -- against it the real pass writes nothing and the assertion
    would be about work that never happened.

    Kills: replacing ``_fdictRunTheCaptureUnderTheDrain``'s
    fdictRunLockHeldMutation call with a direct call to its worker.
    """
    from vaibify.gui import promptRecordManager

    client, connectionDocker = tclientReplay
    sIndexPath = S_PROJECT_REPO + "/.vaibify/promptRecord/index.json"

    def fdictWriteAnIndexInstead(
        connectionDockerArg, sContainerIdArg, filesRepoArg, listSecrets,
    ):
        connectionDockerArg.fnWriteFile(
            sContainerIdArg, sIndexPath, b"{}",
        )
        return {"iTranscriptsCaptured": 0}

    with patch.object(
        promptRecordManager, "fdictRunCapturePass",
        fdictWriteAnIndexInstead,
    ):
        response = client.post(
            f"/api/workflow/{S_CONTAINER_ID}/prompt-record/capture",
        )
    assert response.status_code == 200, response.text
    _fnAssertSelectedRanUnder(
        connectionDocker,
        lambda dictReached: (
            dictReached["sPrimitive"] == S_PRIMITIVE_WRITE
            and dictReached["sPath"] == sIndexPath
        ),
        mutationAdmission.S_ADMISSION_MODE_LOCK_HELD,
        "write from the capture pass",
    )


def testThePersonalLayerHashReachesNoContainerPrimitive(
    tclientReplay, tmp_path, monkeypatch,
):
    """The hash route is separate-authority: it touches no container.

    Both halves are asserted, because either alone is satisfiable by a
    route that does nothing. The gated ledger must be EMPTY -- that is
    what ``separate-authority`` claims about the container -- AND the
    response must carry a real sha256, which is what proves the request
    did its work rather than returning early.

    Not marked falsification: there is no guard here to break. The
    declaration records that the commit carrier is not this route's
    authority, and the authority that IS -- the agent-lane rejection --
    already has its own coverage in tests/testAgentLaneEnforcement.py.
    """
    import hashlib

    monkeypatch.setenv("HOME", str(tmp_path))
    pathSecret = tmp_path / "privateInstructions.md"
    pathSecret.write_text("private layer\n")

    client, connectionDocker = tclientReplay
    connectionDocker.listAdmittedPrimitives.clear()
    response = client.post(
        f"/api/workflow/{S_CONTAINER_ID}/personal-layer/hash",
        json={"sLabel": "personal", "sHostPath": str(pathSecret)},
    )
    assert response.status_code == 200, response.text
    assert response.json()["dictHashCommitment"]["sSha256"] == (
        hashlib.sha256(b"private layer\n").hexdigest()
    ), response.text
    assert connectionDocker.listAdmittedPrimitives == [], (
        "a route recorded separate-authority reached a "
        "mutation-capable container primitive: "
        f"{connectionDocker.listAdmittedPrimitives}"
    )


@pytest.mark.falsification
def testTheExistenceBatchIsATypedReadAndNotAnExec(tclientGated):
    """POST /api/files/{id}/exist probes through the typed-read adapter.

    The route used to build a shell heredoc with every requested path
    interpolated raw and run it through the general exec primitive. On
    the enforced branch that is refused outright -- the gate treats an
    exec as mutating, because a primitive handed command text cannot
    know what the text does -- so the file panel's existence probe would
    simply have stopped working. Making it a declared typed read is what
    removes both problems at once.

    Both halves are asserted, because either alone is satisfiable by a
    route that does nothing. The gated ledger must be EMPTY, which is
    what ``typed-read`` claims; and the response must carry an answer
    for the requested path, which is what proves it did the work. The
    double's typed-read adapter answers False for everything, so the
    assertion is on the KEY being present, never on its value.

    Kills: reverting ``_fdictTestExistenceBatch`` to the heredoc it
    replaced, which reaches ``ftResultExecuteCommand`` and is refused
    with no admission open.
    """
    client, connectionDocker = tclientGated

    def _flistAnswerAbsent(self, sContainerIdArg, listPaths):
        tokenRead = mutationAdmission.ftokenEnterAuditedRead()
        try:
            mutationAdmission.fnAssertContainerCommandAdmitted(
                sContainerIdArg, S_PRIMITIVE_EXEC,
            )
        finally:
            mutationAdmission.fnExitAuditedRead(tokenRead)
        self.listTypedPathProbes.extend(listPaths)
        return [False for _sPath in listPaths]

    connectionDocker.listAdmittedPrimitives.clear()
    connectionDocker.listTypedPathProbes.clear()
    with patch.object(
        DockerDoubleThatCallsTheRealGates, "flistContainerPathsExist",
        _flistAnswerAbsent, create=True,
    ):
        response = client.post(
            f"/api/files/{S_CONTAINER_ID}/exist",
            json={"saRelativePaths": ["stepA/results.json"]},
        )
    assert response.status_code == 200, response.text
    assert "stepA/results.json" in response.json()["dictExists"], (
        f"the route answered nothing for the requested path: "
        f"{response.text}"
    )
    assert connectionDocker.listTypedPathProbes, (
        "the route reached no typed read at all, so the empty-ledger "
        "assertion below would pass for a request that returned early"
    )
    assert connectionDocker.listAdmittedPrimitives == [], (
        "a route recorded typed-read reached a mutation-capable "
        f"container primitive: {connectionDocker.listAdmittedPrimitives}"
    )


# ---------------------------------------------------------------------
# Group 6 — the step panel's probe-and-record routes, and the three
# POSTs that turn out to be reads.
# ---------------------------------------------------------------------

# The scratch file ``_fdictGetModTimes`` writes into the container
# before it stats. Named here so the acknowledge-step assertions can
# separate the PROBE's write from the workflow SAVE's: both go through
# the same primitive, and a write carries no command text to tell them
# apart by.
def testTheAcknowledgeStepProbeIsATypedReadAndNotAWrite(
    tclientGatedWithPlots,
):
    """Acknowledging a step stats its outputs WITHOUT writing anything.

    This test used to assert the opposite half of the same fact: the
    probe wrote its path list into ``/tmp/vaibifyPoll.list`` and ran
    ``xargs … stat`` over it, so the route had to carry both under
    mode (b) or the researcher's "I have seen this output" click would
    500. The probe is a typed read now -- the paths ride as a literal
    inside a fixed program -- so there is nothing left to admit, and
    the guarantee worth pinning is that no write happens at all.

    Both halves are asserted, because either alone is satisfiable by a
    route that does nothing: the probe must have RUN (the typed-probe
    ledger is non-empty) and no write may name the retired pathfile.

    The guarantee that the poll never writes has its own lever in
    ``tests/testFileStatusManager.py``; this asserts the property at
    the ROUTE, where a caller could reintroduce a write of its own.
    """
    client, connectionDocker = tclientGatedWithPlots
    connectionDocker.listTypedPathProbes.clear()
    client.post(
        f"/api/pipeline/{S_CONTAINER_ID}/acknowledge-step/0",
    )
    assert connectionDocker.listTypedPathProbes, (
        "the route made no typed path probe at all, so this asserts "
        "nothing: it returned before stating the step's outputs"
    )
    listPathfileWrites = [
        dictReached
        for dictReached in connectionDocker.listAdmittedPrimitives
        if dictReached["sPath"].startswith("/tmp/")
    ]
    assert listPathfileWrites == [], (
        f"the acknowledge probe wrote into /tmp: {listPathfileWrites}. "
        "The stat batch carries its paths as a literal now; a scratch "
        "file would put a container mutation back on this route."
    )


@pytest.mark.falsification
def testTheAcknowledgeStepSaveCommitsThroughTheSynchronousCarrier(
    tclientGatedWithPlots,
):
    """Acknowledging a step records the new baseline under mode (a).

    The route's OTHER carrier. The probe and the save are two
    mutations: the probe writes a scratch list and execs a stat, while
    the save rewrites ``project.json`` with bytes the journal's hash
    probe can adjudicate afterwards.

    The isolation is ONE-DIRECTIONAL and worth stating. Removing the
    PROBE's carrier fails both tests, because the probe runs first: its
    unadmitted write raises at the primitive and the save never
    happens. Removing the SAVE's carrier fails only this one. So both
    failing points at the probe; this one alone points at the save.

    Kills: reverting ``fdictAcknowledgeStep``'s ``fdictCommitWorkflowSave``
    to ``dictCtx["save"](sContainerId, dictWorkflow)``.
    """
    client, connectionDocker = tclientGatedWithPlots
    client.post(
        f"/api/pipeline/{S_CONTAINER_ID}/acknowledge-step/0",
    )
    _fnAssertSelectedRanUnder(
        connectionDocker,
        lambda dictReached: (
            dictReached["sPrimitive"] == S_PRIMITIVE_WRITE
            and dictReached["sPath"] == S_WORKFLOW_PATH
        ),
        mutationAdmission.S_ADMISSION_MODE_SYNCHRONOUS,
        f"write to {S_WORKFLOW_PATH}",
    )


@pytest.mark.falsification
def testTheGeneratedTestRemovalCommitsThroughTheSynchronousCarrier(
    tclientGated,
):
    """Deleting a step's generated tests runs its ``rm -rf`` carried.

    An ``rm -rf`` of the directory the researcher's tests live in,
    which reached the exec primitive directly. Mode (a) rather than
    (b) deliberately: it is one command and it already ran on the
    request coroutine, so a supervisor would have moved it into a
    thread — a concurrency change this migration has no reason to make.

    Selected on the ``rm -rf`` command text, so the workflow save that
    follows cannot answer for it.

    Kills: replacing ``_fdictCommitTestDirectoryRemoval``'s
    ``fdictCommitSynchronousMutation`` call with a direct call to
    ``_fnRemoveTestDirectory``.

    No status assertion, for the reason the acknowledge-step pair
    records: it would carry the SAVE's defect onto this test too.
    """
    client, connectionDocker = tclientGated
    client.delete(
        f"/api/steps/{S_CONTAINER_ID}/0/generated-test",
    )
    _fnAssertExecsNamingRanUnder(
        connectionDocker, "rm -rf",
        mutationAdmission.S_ADMISSION_MODE_SYNCHRONOUS,
    )


@pytest.mark.falsification
def testTheGeneratedTestRemovalSaveCommitsThroughItsOwnCarrier(
    tclientGated,
):
    """Deleting generated tests records the reset step under mode (a).

    The same one-directional isolation the acknowledge-step pair has,
    and in the same direction: the ``rm -rf`` runs first, so losing ITS
    carrier fails both tests, while losing the save's fails only this
    one.

    Kills: reverting ``fdictDeleteGeneratedTest``'s
    ``fdictCommitWorkflowSave`` to ``dictCtx["save"](...)``.
    """
    client, connectionDocker = tclientGated
    client.delete(
        f"/api/steps/{S_CONTAINER_ID}/0/generated-test",
    )
    _fnAssertSelectedRanUnder(
        connectionDocker,
        lambda dictReached: (
            dictReached["sPrimitive"] == S_PRIMITIVE_WRITE
            and dictReached["sPath"] == S_WORKFLOW_PATH
        ),
        mutationAdmission.S_ADMISSION_MODE_SYNCHRONOUS,
        f"write to {S_WORKFLOW_PATH}",
    )


@pytest.mark.falsification
def testTheScriptScanIsATypedReadAndNotAnExec(tclientGated):
    """POST .../scan-scripts lists the step directory as a typed read.

    It is a POST that reads. It used to send
    ``find … -printf … || ls …/*.py | xargs -n1 basename`` through the
    general exec primitive, which the gate must treat as mutating
    because command text carries no distinction between listing a
    directory and emptying one — so on the enforced branch the step
    editor's "detect scripts" button would simply have stopped working.
    The shell pipeline also lost its quoting on the ``ls`` branch, so a
    step directory containing a space listed nothing at all.

    Both halves are asserted, because either alone is satisfiable by a
    defect: the gated ledger must be EMPTY, which is what ``typed-read``
    claims, and the typed-probe ledger must be NON-EMPTY, or a route
    that returned before touching the container would pass the first.

    Kills: restoring the ``find … || ls …`` command through
    ``ftResultExecuteCommand``.
    """
    client, connectionDocker = tclientGated
    response = client.post(
        f"/api/steps/{S_CONTAINER_ID}/0/scan-scripts",
    )
    assert response.status_code == 200, response.text
    assert connectionDocker.listTypedPathProbes, (
        "the route reached no typed read at all, so the empty-ledger "
        "assertion below would pass for a request that returned early"
    )
    assert connectionDocker.listAdmittedPrimitives == [], (
        "a route declared typed-read reached a mutation-capable "
        f"container primitive: {connectionDocker.listAdmittedPrimitives}"
    )


@pytest.mark.falsification
def testTheDependencyScanReachesNoMutatingPrimitive(tclientGated):
    """POST .../scan-dependencies reads scripts and mutates nothing.

    The second POST-that-reads. It fetches each command's script
    through ``fbaFetchFile`` — already a typed read — and everything
    after that is pure: parsing the source for load calls and matching
    the filenames against upstream steps' declared outputs.

    The non-vacuity half is asserted on the RESPONSE rather than on the
    typed-probe ledger, because ``fbaFetchFile`` does not record into
    it. A detected filename can only have come from source bytes the
    route read out of the container, so its presence proves the read
    happened.

    Kills: routing ``_fsReadContainerFile`` through
    ``ftResultExecuteCommand`` with a ``cat`` — which is how a file read
    becomes an arbitrary command again.
    """
    client, connectionDocker = tclientGated
    connectionDocker._dictFiles[f"{S_PROJECT_REPO}/stepA/analyze.py"] = (
        b'import numpy as np\n'
        b'daSamples = np.loadtxt("upstream.dat")\n'
    )
    response = client.post(
        f"/api/steps/{S_CONTAINER_ID}/0/scan-dependencies",
        json={"saDataCommands": ["python analyze.py"]},
    )
    assert response.status_code == 200, response.text
    listUnmatched = response.json()["listUnmatchedFiles"]
    assert [
        dictItem for dictItem in listUnmatched
        if dictItem["sFileName"] == "upstream.dat"
    ], (
        "the route detected no load call, so it never read the script "
        f"out of the container: {response.text}"
    )
    assert connectionDocker.listAdmittedPrimitives == [], (
        "a route declared typed-read reached a mutation-capable "
        f"container primitive: {connectionDocker.listAdmittedPrimitives}"
    )


@pytest.mark.falsification
def testTheComparePlotRouteOpensNoContainerConnectionAtAll(
    tclientGatedWithPlots,
):
    """POST .../compare-plot resolves two paths and touches nothing.

    The strongest ``typed-read`` in the migration, and the one whose
    assertion needs the most care: "it reached no primitive" is ALSO
    true of a route that refused. So the response is checked first —
    both paths must come back non-empty, which is only possible if the
    handler ran to the end.

    What holds the declaration honest afterwards is the enforced branch
    itself. If this route ever grows a container touch it has no
    admission to make it under, so it will 500 rather than quietly
    mutating, which is what makes the empty-ledger assertion a standing
    guard rather than a snapshot.

    Kills: reaching ``ftResultExecuteCommand`` from the handler, e.g.
    probing the standard's existence before answering.
    """
    client, connectionDocker = tclientGatedWithPlots
    response = client.post(
        f"/api/steps/{S_CONTAINER_ID}/0/compare-plot",
        json={"sFileName": "figure.pdf"},
    )
    assert response.status_code == 200, response.text
    dictBody = response.json()
    assert dictBody["sPlotPath"] and dictBody["sStandardPath"], (
        "the route answered with an empty path, so it did not run to "
        f"the end and the empty ledger below asserts nothing: {dictBody}"
    )
    assert connectionDocker.listAdmittedPrimitives == [], (
        "a route declared typed-read reached a mutation-capable "
        f"container primitive: {connectionDocker.listAdmittedPrimitives}"
    )


# ---------------------------------------------------------------------
# Group 10 -- the pipeline Kill route's three carriers.
# ---------------------------------------------------------------------

# Kill sweeps for the workflow's OWN command patterns, so a workflow
# declaring no commands produces no grep pattern and the sweep never
# reaches the container at all. The draft harness's workflow is that
# one -- which is what lets the two state-write tests below exercise
# their carrier with the sweep switched off, and vice versa. Each test
# arranges for exactly ONE of the route's three carriers to run, which
# is what makes their kill-confirms isolate.
DICT_WORKFLOW_WITH_KILLABLE_COMMANDS = copy.deepcopy(DICT_WORKFLOW)
DICT_WORKFLOW_WITH_KILLABLE_COMMANDS["listSteps"][0]["saDataCommands"] = [
    "python analysis.py",
]
DICT_WORKFLOW_WITH_KILLABLE_COMMANDS["sProjectRepoPath"] = S_PROJECT_REPO

# The command marker for the sweep. ``ps aux`` appears in BOTH of the
# sweep's commands (the count and the kill) and in nothing the connect
# handler issues, so it selects the sweep's crossings and only those.
S_SWEEP_COMMAND_MARKER = "ps aux"


def _fdictBuildRunningPipelineState(fHeartbeatAgeSeconds):
    """Return a pipeline state claiming a runner alive N seconds ago."""
    dtBeat = datetime.now(timezone.utc) - timedelta(
        seconds=fHeartbeatAgeSeconds,
    )
    return {
        "bRunning": True,
        "sAction": "run-all",
        "sLogPath": "/tmp/run.log",
        "sStartTime": dtBeat.isoformat(),
        "sEndTime": "",
        "iExitCode": -1,
        "iActiveStep": 2,
        "iStepCount": 5,
        "dictStepResults": {},
        "listRecentOutput": [],
        "iRunnerPid": 4242,
        "sLastHeartbeat": dtBeat.isoformat(),
        "sFailureReason": "",
    }


class DockerDoubleForTheKillRoute(DockerDoubleThatCallsTheRealGates):
    """The gate-faithful double, told what the container is running.

    Two answers the parent cannot give. The parent reports ``0``
    matching processes, so the kill half of the sweep would never run;
    and it has no pipeline state, so the reconciling reader returns
    ``None`` and the stopped-state write never happens.

    ``mv`` is modelled because ``fnWriteState`` is a temp-write plus a
    rename. A double that swallowed the rename would leave the
    canonical path empty, so the assertion that the reconcile RECORDED
    the runner's real exit code would be reading a file nothing wrote.
    """

    def __init__(self, dictWorkflow):
        super().__init__()
        self._dictFiles[S_WORKFLOW_PATH] = json.dumps(
            dictWorkflow,
        ).encode("utf-8")

    def fnSeedPipelineState(self, dictPipelineState):
        """Put a pipeline state in the container, AFTER connect.

        Not in the constructor, and the difference is not cosmetic: the
        connect handler reads the pipeline state itself, and it runs on
        the owner-ESTABLISHING admission, so a stale state seeded before
        connect is reconciled by connect and Kill then finds a settled
        file with nothing left to do. Seeding afterwards models what
        actually happens — the runner dies during the session — and is
        what lets the reconcile assertions below observe anything.
        """
        self._dictFiles[pipelineState.S_STATE_PATH] = json.dumps(
            dictPipelineState,
        ).encode("utf-8")

    def ftResultExecuteCommand(
        self, sContainerId, sCommand, sWorkdir=None,
    ):
        tExec = super().ftResultExecuteCommand(
            sContainerId, sCommand, sWorkdir,
        )
        if sCommand.startswith("mv ") and (
            pipelineState.S_STATE_PATH in sCommand
        ):
            # Keyed on the DESTINATION, and the source is read out of
            # the command: the temp name carries a per-writer suffix
            # now, so no constant names it. Unquoted with shlex because
            # the writer quotes both operands — a host project's
            # directory may contain a space.
            sTempPath = shlex.split(sCommand)[1]
            self._dictFiles[pipelineState.S_STATE_PATH] = (
                self._dictFiles.pop(sTempPath, b"")
            )
            return tExec
        if S_SWEEP_COMMAND_MARKER in sCommand and "wc -l" in sCommand:
            return (0, "3\n")
        return tExec


def _tConnectGatedKillClient(dictWorkflow, dictPipelineState=None):
    """Return ``(client, docker)`` over the kill route's double."""
    tClient = _tConnectGatedClient(
        DockerDoubleForTheKillRoute(dictWorkflow),
    )
    if dictPipelineState is not None:
        tClient[1].fnSeedPipelineState(dictPipelineState)
    return tClient


@pytest.mark.falsification
def testTheKillProcessSweepRunsUnderOneHeldDrain():
    """POST .../kill counts and kills processes under mode (b).

    The count and the kill share ONE drain because the count IS the
    guard: the kill runs only when the count is non-zero, and the
    number the response reports is the number that was killed. Before
    this migration both ran on a bare ``asyncio.to_thread``, so a
    hand-over arriving between them saw an idle container and
    committed, while the former owner's ``kill -9`` went on into a
    workspace that now belonged to somebody else.

    The pipeline state is absent here, so the reconciling reader
    returns ``None`` and neither state-write carrier runs -- which is
    what keeps this test's kill-confirm from also failing theirs.

    Kills: passing ``_fiCountThenKillUnderTheDrain``'s worker to
    ``asyncio.to_thread`` instead of ``fgenericRunWorkerUnderTheDrain``.
    """
    client, connectionDocker = _tConnectGatedKillClient(
        DICT_WORKFLOW_WITH_KILLABLE_COMMANDS,
    )
    client.post(f"/api/pipeline/{S_CONTAINER_ID}/kill")
    _fnAssertExecsNamingRanUnder(
        connectionDocker, S_SWEEP_COMMAND_MARKER,
        mutationAdmission.S_ADMISSION_MODE_LOCK_HELD,
    )


@pytest.mark.falsification
def testTheKillStoppedStateWriteRunsUnderItsOwnDrain():
    """POST .../kill records "not running" under mode (b).

    The route's SECOND carrier, and it needs its own because the
    sweep's supervisor released the drain when its worker terminated —
    that release is the property mode (b) exists for. The workflow
    here declares no commands, so the sweep never reaches the
    container and this test isolates the state write.

    Selected on the WRITE primitive rather than on any command,
    because ``fnWriteState`` is a temp-file write followed by a
    rename and the write is the irreversible half.

    Kills: passing ``_fiMarkPipelineStopped``'s stopped-state worker to
    ``asyncio.to_thread`` instead of ``fgenericRunWorkerUnderTheDrain``.
    """
    client, connectionDocker = _tConnectGatedKillClient(
        DICT_WORKFLOW,
        _fdictBuildRunningPipelineState(1.0),
    )
    client.post(f"/api/pipeline/{S_CONTAINER_ID}/kill")
    _fnAssertWritesRanUnder(
        connectionDocker, mutationAdmission.S_ADMISSION_MODE_LOCK_HELD,
    )
    dictPersisted = json.loads(
        connectionDocker._dictFiles[pipelineState.S_STATE_PATH],
    )
    assert dictPersisted["bRunning"] is False, (
        "Kill did not record the pipeline as stopped, so the dashboard "
        f"would keep showing it running: {dictPersisted}"
    )
    assert dictPersisted["iExitCode"] == 130, (
        "Kill recorded an exit code that is not the interrupt code: "
        f"{dictPersisted}"
    )


@pytest.mark.falsification
def testTheKillReconcileWriteKeepsTheRunnersRealCauseOfDeath():
    """A Kill over a dead runner records what actually killed it.

    The route's THIRD carrier, and the reason the reconciling reader
    was kept rather than traded for a plain read. The runner here died
    without writing a final state, so the reader reconciles: it stamps
    the runner-disappeared sentinel and the heartbeat-stale reason, and
    persists them. A cheaper migration would have dropped the
    reconciling reader and let Kill overwrite the file with a flat
    "killed (130)", which is the dashboard stating something that did
    not happen.

    Because the reconcile returns ``bRunning: False``, the stopped-state
    write does not run, and the workflow declares no commands so the
    sweep does not either — so this isolates the reconcile carrier.

    Kills: dropping the ``fnPersistReconciled`` argument in
    ``_fiMarkPipelineStopped`` so the reconcile write falls back to the
    uncarried background lane.
    """
    client, connectionDocker = _tConnectGatedKillClient(
        DICT_WORKFLOW,
        _fdictBuildRunningPipelineState(
            pipelineState.I_HEARTBEAT_STALE_SECONDS * 3,
        ),
    )
    client.post(f"/api/pipeline/{S_CONTAINER_ID}/kill")
    _fnAssertWritesRanUnder(
        connectionDocker, mutationAdmission.S_ADMISSION_MODE_LOCK_HELD,
    )
    dictPersisted = json.loads(
        connectionDocker._dictFiles[pipelineState.S_STATE_PATH],
    )
    assert dictPersisted["iExitCode"] == (
        pipelineState.I_EXIT_CODE_RUNNER_DISAPPEARED
    ), (
        "the reconcile did not record the runner-disappeared sentinel, "
        "so Kill overwrote the real cause of death with its own: "
        f"{dictPersisted}"
    )
    assert "heartbeat_stale" in dictPersisted["sFailureReason"], (
        "the reconcile lost the failure reason a researcher reads to "
        f"find out why their run stopped: {dictPersisted}"
    )


# ---------------------------------------------------------------------
# Group 11 -- generating a step's tests: the pre-flight, the drain, the
# save.
# ---------------------------------------------------------------------

# The introspection program the deterministic generator writes into
# /tmp and runs. Its stdout has to parse as JSON or the generator
# raises, so the double answers this ONE command with an empty report
# rather than with the parent's blanket "".
S_INTROSPECT_COMMAND_MARKER = "_vaibify_introspect_"

# The directory the generator creates and writes its three test files
# into, for the step the draft harness's workflow declares.
S_GENERATED_TESTS_DIRECTORY = posixpath.join(
    S_PROJECT_REPO, "stepA", "tests",
)


class DockerDoubleForTestGeneration(DockerDoubleThatCallsTheRealGates):
    """The gate-faithful double, with the introspection program answered.

    Only the introspection run is special-cased. The parent answers
    every command with ``(0, "")``, which the generator rejects as
    unparseable and turns into a 500 -- a real outcome, and the one
    this route now QUARANTINES on, but not the one that lets the save
    downstream be reached and asserted.
    """

    def ftResultExecuteCommand(
        self, sContainerId, sCommand, sWorkdir=None,
    ):
        tExec = super().ftResultExecuteCommand(
            sContainerId, sCommand, sWorkdir,
        )
        if (
            S_INTROSPECT_COMMAND_MARKER in sCommand
            and sCommand.startswith("python3 ")
        ):
            return (0, "[]")
        return tExec


@pytest.fixture
def tclientGatedForGeneration():
    """The gated client over the test-generation double."""
    return _tConnectGatedClient(DockerDoubleForTestGeneration())


def _fresponsePostGenerateTest(client, iStepIndex=0):
    """Drive the deterministic test generator for one step."""
    return client.post(
        f"/api/steps/{S_CONTAINER_ID}/{iStepIndex}/generate-test",
        json={"bDeterministic": True, "bForceOverwrite": False},
    )


@pytest.mark.falsification
def testTheTestGenerationRunsUnderTheDrain(tclientGatedForGeneration):
    """POST .../generate-test writes its test files under mode (b).

    The generator makes a tests directory, writes an introspection
    program into /tmp and runs it, then writes a conftest marker and
    three test files. It used to do all that on a bare
    ``asyncio.to_thread``: a hand-over arriving part-way left a
    container holding half a generated test suite that belonged to
    somebody else.

    Selected on writes INTO the step's tests directory, so the workflow
    save that follows -- also a write, through the same primitive, but
    under mode (a) -- cannot answer for this carrier.

    Kills: passing ``_fdictRunTestGeneration``'s worker to
    ``asyncio.to_thread`` instead of ``fgenericRunWorkerUnderTheDrain``.
    """
    client, connectionDocker = tclientGatedForGeneration
    _fresponsePostGenerateTest(client)
    _fnAssertSelectedRanUnder(
        connectionDocker,
        lambda dictReached: (
            dictReached["sPrimitive"] == S_PRIMITIVE_WRITE
            and dictReached["sPath"].startswith(
                S_GENERATED_TESTS_DIRECTORY,
            )
        ),
        mutationAdmission.S_ADMISSION_MODE_LOCK_HELD,
        f"write under {S_GENERATED_TESTS_DIRECTORY}",
    )


@pytest.mark.falsification
def testTheGeneratedTestsAreRecordedSynchronously(
    tclientGatedForGeneration,
):
    """POST .../generate-test records the new tests under mode (a).

    The route's OTHER carrier. The isolation is one-directional and
    saying so is the honest reading: removing the GENERATION's carrier
    fails both, because it runs first and its refusal 500s the handler
    before the save is reached. Only this one failing means the save.

    Kills: reverting ``_fnApplyGeneratedTests``'s
    ``fdictCommitWorkflowSave(...)`` to ``dictCtx["save"](...)``.
    """
    client, connectionDocker = tclientGatedForGeneration
    _fresponsePostGenerateTest(client)
    _fnAssertSelectedRanUnder(
        connectionDocker,
        lambda dictReached: (
            dictReached["sPrimitive"] == S_PRIMITIVE_WRITE
            and dictReached["sPath"] == S_WORKFLOW_PATH
        ),
        mutationAdmission.S_ADMISSION_MODE_SYNCHRONOUS,
        f"write to {S_WORKFLOW_PATH}",
    )


@pytest.mark.falsification
def testAnOutOfRangeStepIsRefusedBeforeTheContainerIsTouched(
    tclientGatedForGeneration,
):
    """A bad step index answers 404 and reaches no container primitive.

    Ruling 6's pre-flight, asserted where it matters. The step index
    comes from the URL, and inside the carrier its ``IndexError``
    would settle as a FAILED worker -- marking the container as
    needing reconciliation and refusing the researcher's next
    mutation, over a typo. Both halves are asserted because either
    alone is weak: the 404 without the empty ledger would pass for a
    route that generated first and answered second, and the empty
    ledger without the 404 would pass for a route that refused for
    some other reason entirely.

    Kills: removing the ``_fnRequireStepIndexBeforeGenerating`` call
    from ``fnGenerateTest``.
    """
    client, connectionDocker = tclientGatedForGeneration
    response = _fresponsePostGenerateTest(client, iStepIndex=99)
    assert response.status_code == 404, response.text
    assert connectionDocker.listAdmittedPrimitives == [], (
        "an out-of-range step index reached a container primitive "
        "before it was refused, so the refusal happened inside a "
        "carrier and the container was marked: "
        f"{connectionDocker.listAdmittedPrimitives}"
    )


# ---------------------------------------------------------------------
# Group 12 -- the Sync panel's remaining Overleaf and Zenodo routes.
#
# Three shapes again, and the split is not cosmetic. The two mirror
# routes act on a partial clone under the researcher's OWN home
# directory and reach the container not at all, so their assertion is
# an EMPTY gated ledger paired with evidence they did their work. The
# diff, the manuscript pull and the Zenodo upload are mode-(b)
# container work. The credential setup and the Zenodo metadata save
# carry a mode-(a) project.json write, and setup carries BOTH -- so
# its two carriers are asserted separately, or a missing one hides
# behind the other.
#
# One route in this family is deliberately NOT here: POST
# /api/zenodo/{id}/download was left awaiting, because
# ``syncDispatcher.ftResultDownloadDataset`` exists nowhere in the
# repository and every production call raises. See the comment at its
# registration for why carrying a route that cannot run would make it
# worse rather than better.
# ---------------------------------------------------------------------

S_SYNC_OVERLEAF_PROJECT_ID = "ol1234"
S_SYNC_PUSHABLE_FILE = posixpath.join(S_PROJECT_REPO, "stepA/output.dat")

# Emitted by the container-side digest script the Overleaf diff runs,
# and by nothing else this double answers. A looser marker would match
# any embedded ``python3 -c`` and fold the connect handler's own probes
# into a claim about the diff's. Deliberately free of quote characters:
# the script travels through ``fsShellQuote``, which rewrites every
# ``'`` as ``'"'"'``, so a marker containing one matches nothing and
# the assertion fails on its own emptiness rather than passing.
S_SYNC_DIGEST_MARKER = "s=hashlib.sha1(b"


def _fdictSyncBoundWorkflow():
    """Return the draft workflow bound to an Overleaf project + repo.

    The draft harness's document carries neither, so every route below
    would refuse ("Overleaf project ID not set", "no repository path")
    before reaching a carrier -- a fixture under which these tests
    would pass having exercised the refusal.
    """
    dictWorkflow = copy.deepcopy(DICT_WORKFLOW)
    dictWorkflow["sProjectRepoPath"] = S_PROJECT_REPO
    dictWorkflow["sOverleafProjectId"] = S_SYNC_OVERLEAF_PROJECT_ID
    dictWorkflow["sZenodoService"] = "sandbox"
    return dictWorkflow


class DockerDoubleServingASyncBoundWorkflow(
    DockerDoubleThatCallsTheRealGates,
):
    """The gate-faithful double over a workflow bound to Overleaf."""

    def __init__(self):
        super().__init__()
        self.setExistingPaths.add(S_SYNC_PUSHABLE_FILE)

    def fbaFetchFile(self, sContainerId, sPath, iMaxBytes=None):
        if sPath == S_WORKFLOW_PATH:
            return json.dumps(_fdictSyncBoundWorkflow()).encode("utf-8")
        return super().fbaFetchFile(sContainerId, sPath, iMaxBytes)


@pytest.fixture
def tclientSyncBound():
    """The gated client over an Overleaf-bound, Zenodo-bound workflow."""
    return _tConnectGatedClient(DockerDoubleServingASyncBoundWorkflow())


@contextlib.contextmanager
def _fnOverleafHostMirrorStubbed():
    """Stub the HOST mirror and its token; leave container work real.

    Everything stubbed here runs on the researcher's own machine: the
    partial clone, the git objects read out of it, and the keyring
    lookup that would otherwise reach the real OS credential store
    during a test run. What is deliberately left REAL is every call
    that crosses into the container, because that is the whole subject
    of these tests.
    """
    from vaibify.gui import syncDispatcher
    from vaibify.reproducibility import overleafMirror
    with patch.object(
        syncDispatcher, "ftRefreshOverleafMirror",
        lambda sProjectId: (True, {
            "sHeadSha": "", "iFileCount": 0, "sRefreshedAt": "",
        }),
    ), patch.object(
        syncDispatcher, "_fsFetchOverleafToken", lambda: "",
    ), patch.object(
        overleafMirror, "fdictDiffAgainstMirror",
        lambda sProjectId, dictDigests, sTargetDirectory: {
            "listNew": [], "listOverwrite": [], "listUnchanged": [],
        },
    ), patch.object(
        overleafMirror, "flistDetectConflicts", lambda *a, **k: [],
    ), patch.object(
        overleafMirror, "flistDetectCaseCollisions", lambda *a, **k: [],
    ), patch.object(
        overleafMirror, "fsReadMirrorHeadSha", lambda sProjectId: "",
    ), patch(
        "vaibify.gui.routes.syncRoutes._fnRequireNetworkAccess",
        lambda sContainerId: None,
    ):
        yield


@pytest.mark.falsification
def testTheOverleafDiffDigestsItsFilesUnderTheDrain(tclientSyncBound):
    """POST /api/overleaf/{id}/diff runs its digest exec under mode (b).

    The push preview reads like a read and is not one at the boundary
    that decides: ``fdictDiffOverleafPush`` hashes the selection by
    running a ``python3 -c`` script through the GENERAL exec
    primitive, which the gate must treat as mutating because command
    text cannot be told apart from a delete.

    Kills: reverting ``fdictOverleafDiff`` to
    ``await asyncio.to_thread(_fdictBuildDiffResult, ...)``. That exec
    then reaches the primitive with no admission at all, so nothing
    matching the marker is recorded and the assertion reports the
    empty selection rather than passing.
    """
    client, connectionDocker = tclientSyncBound
    with _fnOverleafHostMirrorStubbed():
        client.post(
            f"/api/overleaf/{S_CONTAINER_ID}/diff",
            json={
                "listFilePaths": [S_SYNC_PUSHABLE_FILE],
                "sTargetDirectory": "figures",
            },
        )
    _fnAssertExecsNamingRanUnder(
        connectionDocker, S_SYNC_DIGEST_MARKER,
        mutationAdmission.S_ADMISSION_MODE_LOCK_HELD,
    )


@pytest.mark.falsification
def testTheManuscriptPullWritesItsIgnoreUnderTheSameDrain(
    tclientSyncBound,
):
    """POST .../pull-manuscript pulls and self-ignores under one mode (b).

    The WRITE is selected rather than every primitive, and the target
    named: the pull's own exec and the ``.gitignore`` that hides its
    output are two mutations the migration deliberately put under ONE
    carrier, because a hand-over landing between them leaves the
    successor's first ``git status`` showing the whole manuscript as
    untracked changes. Naming the path is what makes the assertion
    about THIS write rather than any write the request happened to
    make.

    Kills: reverting ``_fdictHandlePullManuscript`` to its two
    ``asyncio.to_thread`` hops plus a bare ``fnWriteFile``.
    """
    from vaibify.reproducibility import overleafMirror
    client, connectionDocker = tclientSyncBound
    with _fnOverleafHostMirrorStubbed(), patch.object(
        overleafMirror, "flistListMirrorTree",
        lambda sProjectId: [{"sPath": "main.tex", "sType": "blob"}],
    ):
        client.post(
            f"/api/overleaf/{S_CONTAINER_ID}/pull-manuscript",
        )
    _fnAssertSelectedRanUnder(
        connectionDocker,
        lambda dictReached: (
            dictReached["sPrimitive"] == S_PRIMITIVE_WRITE
            and dictReached["sPath"].endswith(
                ".vaibify/manuscript/.gitignore",
            )
        ),
        mutationAdmission.S_ADMISSION_MODE_LOCK_HELD,
        "write of the pulled manuscript's .gitignore",
    )


@pytest.mark.falsification
def testTheMirrorRefreshReachesNoContainerPrimitive(tclientSyncBound):
    """POST .../mirror/refresh touches the HOST clone and nothing else.

    Paired assertions, because either alone is satisfiable by a
    defect. The gated ledger must be EMPTY -- ``ftRefreshOverleafMirror``
    takes no docker connection at all, which is why the route is
    declared ``separate-authority`` rather than given a carrier -- and
    the route must have reached the refresh, or one that refused early
    would pass the first assertion having done nothing.

    Kills: routing the refresh through the container, e.g. running the
    fetch with ``dictCtx["docker"].ftResultExecuteCommand``.
    """
    from vaibify.gui import syncDispatcher
    client, connectionDocker = tclientSyncBound
    listRefreshed = []

    def ftRecordRefresh(sProjectId):
        listRefreshed.append(sProjectId)
        return (True, {"sHeadSha": "abc", "iFileCount": 1})

    with patch.object(
        syncDispatcher, "ftRefreshOverleafMirror", ftRecordRefresh,
    ), patch(
        "vaibify.gui.routes.syncRoutes._fnRequireNetworkAccess",
        lambda sContainerId: None,
    ):
        response = client.post(
            f"/api/overleaf/{S_CONTAINER_ID}/mirror/refresh",
        )
    assert response.status_code == 200, response.text
    assert listRefreshed == [S_SYNC_OVERLEAF_PROJECT_ID], (
        "the route did not reach the host mirror refresh, so the "
        "empty gated ledger below asserts nothing"
    )
    assert connectionDocker.listAdmittedPrimitives == [], (
        "a route declared separate-authority reached a "
        "mutation-capable container primitive: "
        f"{connectionDocker.listAdmittedPrimitives}"
    )


@pytest.mark.falsification
def testTheMirrorDeleteReachesNoContainerPrimitive(tclientSyncBound):
    """DELETE .../mirror removes a HOST directory and nothing else.

    Same paired shape as the refresh above: an empty gated ledger is
    only evidence beside proof the deletion was actually reached.

    Kills: routing the deletion through the container, e.g. an ``rm
    -rf`` assembled and run through the general exec primitive.
    """
    from vaibify.reproducibility import overleafMirror
    client, connectionDocker = tclientSyncBound
    listDeleted = []
    with patch.object(
        overleafMirror, "fnDeleteMirror", listDeleted.append,
    ):
        response = client.delete(
            f"/api/overleaf/{S_CONTAINER_ID}/mirror",
        )
    assert response.status_code == 200, response.text
    assert listDeleted == [S_SYNC_OVERLEAF_PROJECT_ID], (
        "the route did not reach the host mirror deletion, so the "
        "empty gated ledger below asserts nothing"
    )
    assert connectionDocker.listAdmittedPrimitives == [], (
        "a route declared separate-authority reached a "
        "mutation-capable container primitive: "
        f"{connectionDocker.listAdmittedPrimitives}"
    )


@pytest.mark.falsification
def testTheZenodoMetadataSaveCommitsThroughTheSynchronousCarrier(
    tclientSyncBound,
):
    """POST /api/zenodo/{id}/metadata persists project.json under mode (a).

    Kills: reverting ``fnSetZenodoMetadata``'s ``fdictCommitWorkflowSave``
    call to ``dictCtx["save"](sContainerId, dictWorkflow)``. On the
    enforced branch that save reaches the write primitive with no
    admission open at all, so the recorded mode is ``''``.
    """
    client, connectionDocker = tclientSyncBound
    response = client.post(
        f"/api/zenodo/{S_CONTAINER_ID}/metadata",
        json={
            "sTitle": "An Archive",
            "sDescription": "",
            "listCreators": [{"sName": "A Researcher"}],
            "listKeywords": [],
        },
    )
    assert response.status_code == 200, response.text
    _fnAssertWritesRanUnder(
        connectionDocker, mutationAdmission.S_ADMISSION_MODE_SYNCHRONOUS,
    )


# Emitted by the GitHub connectivity probe the credential setup runs
# inside the container, and by nothing else this double answers.
S_SYNC_GITHUB_PROBE_MARKER = "git ls-remote --exit-code origin HEAD"


@pytest.mark.falsification
def testTheCredentialSetupProbesTheServiceUnderTheDrain(tclientSyncBound):
    """POST /api/sync/{id}/setup runs its connectivity probe under mode (b).

    One of this handler's TWO carriers. The drain is the point rather
    than a formality: storing a token is stage-validate-commit over a
    SHARED slot -- snapshot the previous credential, overwrite it,
    contact the remote, restore on failure -- so a second session's
    setup interleaving between the snapshot and the restore would swap
    the two researchers' tokens and tell neither.

    The host credential lane is stubbed to False rather than left
    live: it shells out to ``gh auth token`` on the researcher's own
    machine, which would make the outcome depend on who is running the
    suite. What it decides is only ``bConnected``, and this test
    asserts the admission of the CONTAINER probe, which runs either
    way.

    Kills: reverting ``fdictSetupConnection`` to
    ``await _fdictRunSetup(...)`` with its ``asyncio.to_thread`` hops.
    That probe then reaches the exec primitive with no admission, so
    nothing matching the marker is recorded.
    """
    from vaibify.gui import syncDispatcher
    client, connectionDocker = tclientSyncBound
    with patch.object(
        syncDispatcher, "_fbHostGithubCredentialAvailable",
        lambda sRemoteUrl: False,
    ):
        client.post(
            f"/api/sync/{S_CONTAINER_ID}/setup",
            json={"sService": "github"},
        )
    _fnAssertExecsNamingRanUnder(
        connectionDocker, S_SYNC_GITHUB_PROBE_MARKER,
        mutationAdmission.S_ADMISSION_MODE_LOCK_HELD,
    )


@pytest.mark.falsification
def testTheCredentialSetupSavesItsBindingSynchronously(
    tclientSyncBound, fixtureHermeticKeyring,
):
    """A connected Overleaf setup persists project.json under mode (a).

    The handler's OTHER carrier. Binding the project id is a
    synchronous single write whose bytes the journal can adjudicate,
    where the store-and-validate before it contacts a remote for as
    long as the network takes -- two mutations, not one, and a
    migration that carried only the probe would leave this write
    refused at the primitive.

    The keyring is the suite's in-memory fake
    (``fixtureHermeticKeyring``), SEEDED here rather than patched away,
    so ``_fbServiceHasStoredCredential`` and ``_fdictCheckHostKeyring``
    both run for real against a store that is not the researcher's.

    The isolation here is BIDIRECTIONAL, unlike arXiv configure's, and
    the reason is this fixture rather than anything about the handler:
    an Overleaf setup's connectivity check reads the HOST keyring and
    its validation is stubbed, so the mode-(b) worker reaches no
    container primitive on this path and cannot be refused. Verified
    both ways -- each carrier's removal fails one test and only one.

    Kills: reverting ``_fnPersistServiceSettings``'s
    ``fdictCommitWorkflowSave(...)`` to ``dictCtx["save"](...)``.
    """
    from vaibify.gui import syncDispatcher
    client, connectionDocker = tclientSyncBound
    fixtureHermeticKeyring.dictStore[("vaibify", "overleaf_token")] = (
        "not-a-real-token"
    )
    with patch.object(
        syncDispatcher, "fbValidateOverleafCredentials",
        lambda connectionDockerCalled, sContainerId, sProjectId: (
            True, ""
        ),
    ):
        response = client.post(
            f"/api/sync/{S_CONTAINER_ID}/setup",
            json={
                "sService": "overleaf",
                "sProjectId": S_SYNC_OVERLEAF_PROJECT_ID,
            },
        )
    assert response.status_code == 200, response.text
    assert response.json()["bConnected"] is True, (
        "the setup did not report Connected, so it never reached the "
        "persist step this test asserts about: " + response.text
    )
    _fnAssertSelectedRanUnder(
        connectionDocker,
        lambda dictReached: (
            dictReached["sPrimitive"] == S_PRIMITIVE_WRITE
            and dictReached["sPath"] == S_WORKFLOW_PATH
        ),
        mutationAdmission.S_ADMISSION_MODE_SYNCHRONOUS,
        f"write to {S_WORKFLOW_PATH}",
    )


@contextlib.contextmanager
def _tlistRecordEveryZenodoUpload():
    """Record the live admission at each ``ftResultArchiveToZenodo`` call.

    Instrumenting the DISPATCHER rather than matching command text,
    because the archive script travels base64-encoded inside a
    ``python3 -c "import base64; exec(...)"`` shell -- the same shape
    ``repoFiles`` uses for every embedded script, so a text marker
    could not tell the Zenodo upload from any other one, and a test
    that cannot name what it observed is not evidence that the
    observation was the one required.

    The real dispatcher is still CALLED, so the container exec and its
    gate crossing happen exactly as in production.
    """
    from vaibify.gui import syncDispatcher
    listCalls = []
    fnReal = syncDispatcher.ftResultArchiveToZenodo

    def ftRecordThenArchive(connectionDocker, sContainerId, *aArgs):
        admission = mutationAdmission.fadmissionActiveForContainerId(
            sContainerId,
        )
        listCalls.append("" if admission is None else admission.sMode)
        return fnReal(connectionDocker, sContainerId, *aArgs)

    with patch.object(
        syncDispatcher, "ftResultArchiveToZenodo", ftRecordThenArchive,
    ):
        yield listCalls


@pytest.mark.falsification
def testTheZenodoUploadRunsUnderTheDrain(tclientSyncBound):
    """POST /api/zenodo/{id}/archive uploads under a mode-(b) admission.

    Zenodo mints a DOI at the END of the upload, so a container that
    changed hands mid-publish would leave the successor unable to say
    whether the archive exists. Holding the drain for the worker's
    life is what makes a hand-over arriving mid-upload refuse and say
    what is running.

    Kills: reverting ``_ftPerformZenodoArchive`` to
    ``await asyncio.to_thread(syncDispatcher.ftResultArchiveToZenodo,
    ...)``, which reaches the exec primitive with no admission and
    records ``''`` here.
    """
    client, _connectionDocker = tclientSyncBound
    with patch(
        "vaibify.gui.routes.syncRoutes._fnRequireNetworkAccess",
        lambda sContainerId: None,
    ), _tlistRecordEveryZenodoUpload() as listCalls:
        client.post(
            f"/api/zenodo/{S_CONTAINER_ID}/archive",
            json={"listFilePaths": [S_SYNC_PUSHABLE_FILE]},
        )
    assert listCalls, (
        "the route never reached the Zenodo upload, so this asserts "
        "nothing about the admission it runs under"
    )
    assert listCalls == [
        mutationAdmission.S_ADMISSION_MODE_LOCK_HELD
    ] * len(listCalls), (
        "the Zenodo upload ran under an admission that is not "
        f"lock-held: {listCalls}"
    )


# Emitted by ``containerGit.fdictComputeBlobShasInContainer`` and by
# nothing else the archive request runs. Quote-free for the reason
# S_SYNC_DIGEST_MARKER states, and distinct from it so the two digest
# scripts cannot be mistaken for one another.
S_ZENODO_BLOB_SHA_MARKER = "h = hashlib.sha1(); h.update(header)"


def _fresponsePostZenodoArchive(client):
    """Publish one file to Zenodo through the migrated archive route."""
    with patch(
        "vaibify.gui.routes.syncRoutes._fnRequireNetworkAccess",
        lambda sContainerId: None,
    ):
        return client.post(
            f"/api/zenodo/{S_CONTAINER_ID}/archive",
            json={"listFilePaths": [S_SYNC_PUSHABLE_FILE]},
        )


@pytest.mark.falsification
def testTheZenodoDigestPassRunsUnderItsOwnDrain(tclientSyncBound):
    """The archive's post-upload digest pass holds a mode-(b) drain.

    A SECOND mode-(b) invocation rather than an extension of the
    upload's, because the upload's supervisor released the drain when
    its worker terminated -- which is the property that makes mode (b)
    worth having. The digests are what the dashboard's Zenodo cells
    compare against, so an uncarried pass would be refused at the
    primitive and leave the archive recorded with no content
    fingerprints.

    Kills: reverting ``_fnPersistZenodoArchiveSuccess``'s digest hop to
    ``await asyncio.to_thread(_fdictComputePostArchiveZenodoDigests,
    ...)``.
    """
    client, connectionDocker = tclientSyncBound
    _fresponsePostZenodoArchive(client)
    _fnAssertExecsNamingRanUnder(
        connectionDocker, S_ZENODO_BLOB_SHA_MARKER,
        mutationAdmission.S_ADMISSION_MODE_LOCK_HELD,
    )


@pytest.mark.falsification
def testTheZenodoArchiveRecordCommitsSynchronously(tclientSyncBound):
    """The archive's project.json save runs under mode (a).

    The archive handler's THIRD carrier. The record it writes carries
    the deposit id and the DOI Zenodo just minted, so a save refused at
    the primitive would leave a published archive the workflow cannot
    name -- the researcher would see a successful publish and an empty
    deposit panel.

    Selected on the workflow path rather than on every write, so this
    test names one carrier rather than "some write happened".

    The isolation is ONE-DIRECTIONAL and the direction is worth
    knowing when diagnosing. Removing THIS carrier fails only this
    test (verified). Removing the DIGEST carrier fails both, because
    the digests run first and an unadmitted exec 500s the handler
    before the save is reached (verified). So all three archive tests
    failing means the UPLOAD, two means the DIGESTS, and one means the
    save.

    Kills: reverting ``_fnPersistZenodoArchiveSuccess``'s
    ``fdictCommitWorkflowSave(...)`` to ``dictCtx["save"](...)``.
    """
    client, connectionDocker = tclientSyncBound
    _fresponsePostZenodoArchive(client)
    _fnAssertSelectedRanUnder(
        connectionDocker,
        lambda dictReached: (
            dictReached["sPrimitive"] == S_PRIMITIVE_WRITE
            and dictReached["sPath"] == S_WORKFLOW_PATH
        ),
        mutationAdmission.S_ADMISSION_MODE_SYNCHRONOUS,
        f"write to {S_WORKFLOW_PATH}",
    )


# ---------------------------------------------------------------------
# The two repository pushes (phase 2, group 1).
#
# Both are multi-stage and both carry a credential. The GitHub push
# reads the project repo's origin URL to bind the token's owner to it,
# and a token-authenticated clone spells that URL
# ``https://x-access-token:<token>@github.com/...`` -- so the push is
# the one route family where a journal target, a refusal message or a
# log line built from container output would publish a live credential.
# ---------------------------------------------------------------------

# A realistic GitHub personal-access token shape: the ``ghp_`` prefix
# and 36 following characters the redactor's ``{20,}`` bound is written
# for. Deliberately NOT a short placeholder -- a stub like "tok" would
# pass a leak assertion for having no recognisable shape rather than
# for being redacted, which is the vacuous form of this test.
S_PLANTED_TOKEN = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
S_TOKENED_REMOTE_URL = (
    "https://x-access-token:" + S_PLANTED_TOKEN
    + "@github.com/owner/repo.git"
)

# Emitted by ``syncDispatcher.ftResultPushToGithub`` and by nothing
# else the push request runs. Quote-free for the reason
# S_SYNC_DIGEST_MARKER states.
S_GITHUB_PUSH_MARKER = "rev-parse --short HEAD"

# Emitted by the HEAD-sha read ``_fnRecordPushProvenance`` makes and by
# nothing the Overleaf push itself runs, so it names the provenance
# carrier rather than the upload's.
S_OVERLEAF_PROVENANCE_MARKER = "rev-parse HEAD"


# The push dedupe cache is a module global keyed on
# ``(container, HEAD sha, payload digest)``, and every test here drives
# the same container with the same payload against a double whose HEAD
# sha does not move -- so without a reset the second test to push would
# be answered from the first one's cache and its assertion would fail
# on its own emptiness. ``conftest``'s autouse
# ``fnClearPushDedupeCache`` already does exactly that; a second copy
# here would be the duplication that starts a divergence.


@contextlib.contextmanager
def _tlistCaptureEveryJournalPayload():
    """Capture every byte payload the write-ahead journal ever stores.

    Stronger than reading the file afterwards, and necessarily so: a
    settled operation is REMOVED from the payload, so a push that
    succeeds leaves an empty journal and an after-the-fact read would
    assert nothing. ``_fsReadWholeJournalForTheContainer`` is the right
    tool for a test that freezes a push mid-flight and reads the record
    while it is live; this one runs the push to completion, so it
    watches the writer instead of the file.
    """
    from vaibify.config import operationJournal
    listPayloads = []
    fnReal = operationJournal._fnWriteJournalBytesAtomically

    def fnCaptureThenWrite(sPath, byteContent):
        listPayloads.append(bytes(byteContent).decode("utf-8", "replace"))
        return fnReal(sPath, byteContent)

    with patch.object(
        operationJournal, "_fnWriteJournalBytesAtomically",
        fnCaptureThenWrite,
    ):
        yield listPayloads


@contextlib.contextmanager
def _tlistRecordEveryGithubPush():
    """Record the live admission at each ``ftResultPushToGithub`` call.

    Instrumenting the DISPATCHER as well as asserting on the command
    text, because the two answer different questions: the marker proves
    the exec crossed the gate under mode (b), and this proves the push
    was REACHED at all. A route whose worker refused early would leave
    the marker assertion failing on its own emptiness, which reads as
    "no push" rather than "the wrong admission".
    """
    from vaibify.gui import syncDispatcher
    listCalls = []
    fnReal = syncDispatcher.ftResultPushToGithub

    def ftRecordThenPush(connectionDocker, sContainerId, *aArgs):
        admission = mutationAdmission.fadmissionActiveForContainerId(
            sContainerId,
        )
        listCalls.append("" if admission is None else admission.sMode)
        return fnReal(connectionDocker, sContainerId, *aArgs)

    with patch.object(
        syncDispatcher, "ftResultPushToGithub", ftRecordThenPush,
    ):
        yield listCalls


@contextlib.contextmanager
def _fnGithubPushHostSidePlanted():
    """Plant a tokened remote URL and bind the token to its owner.

    Everything patched here runs on the researcher's own machine or on
    the network: the keyring lookup, GitHub's ``/user`` endpoint, and
    the isolation probe. What is left REAL is every call that crosses
    into the container -- the push exec, the commit-state reads and the
    workflow save -- because those are the subject of these tests.

    The remote URL is returned from the CONTAINER read the route makes,
    so the credential enters the handler exactly the way a real one
    does rather than being handed to it as a literal.
    """
    from vaibify.gui import containerGit
    from vaibify.reproducibility import githubAuth, githubMirror
    with patch.object(
        containerGit, "fsRemoteUrlInContainer",
        lambda *aArgs, **dictKwargs: S_TOKENED_REMOTE_URL,
    ), patch.object(
        githubMirror, "_fsResolveTokenSafely",
        lambda sOwner, sRepo: S_PLANTED_TOKEN,
    ), patch.object(
        githubAuth, "_ftFetchLoginFresh",
        lambda sToken: ("owner", ""),
    ), patch(
        "vaibify.gui.routes.syncRoutes._fnRequireNetworkAccess",
        lambda sContainerId: None,
    ):
        yield


def _fresponsePostGithubPush(client):
    """Push one file through the migrated GitHub push route."""
    return client.post(
        f"/api/github/{S_CONTAINER_ID}/push",
        json={
            "listFilePaths": [S_SYNC_PUSHABLE_FILE],
            "sCommitMessage": "carrier probe",
        },
    )


@pytest.mark.falsification
def testTheGithubPushRunsUnderTheDrain(tclientSyncBound):
    """POST /api/github/{id}/push stages and pushes under mode (b).

    The push is irreversible at the remote and the sequence around it
    is not atomic: the dedupe probe reads HEAD, the binding check reads
    the origin URL, and the push then rewrites the local ref. Holding
    the drain for the WORKER's life is what makes an ownership
    hand-over arriving mid-push refuse and say what is running, rather
    than land underneath a git process that is still writing.

    Kills: reverting ``_fdictPushToGithubUnderTheDrain`` to
    ``await asyncio.to_thread(_fdictPushToGithubBlocking, ...)``, which
    reaches the exec primitive with no admission and records ``''``.
    """
    client, connectionDocker = tclientSyncBound
    with _fnGithubPushHostSidePlanted(), (
        _tlistRecordEveryGithubPush()
    ) as listCalls:
        _fresponsePostGithubPush(client)
    assert listCalls, (
        "the route never reached the GitHub push, so this asserts "
        "nothing about the admission it runs under"
    )
    assert listCalls == [
        mutationAdmission.S_ADMISSION_MODE_LOCK_HELD
    ] * len(listCalls), (
        f"the GitHub push ran under a non-lock-held admission: "
        f"{listCalls}"
    )
    _fnAssertExecsNamingRanUnder(
        connectionDocker, S_GITHUB_PUSH_MARKER,
        mutationAdmission.S_ADMISSION_MODE_LOCK_HELD,
    )


@pytest.mark.falsification
def testTheGithubPushBookkeepingSaveCommitsSynchronously(
    tclientSyncBound,
):
    """The push's ``project.json`` save runs under mode (a).

    The push handler's SECOND carrier. The record it writes is the
    commit hash and the per-file sync status the dashboard's GitHub
    badges read, so a save refused at the primitive would leave a
    landed push the workflow cannot name -- the researcher would see a
    successful push and stale badges.

    Selected on the workflow path rather than on every write, so this
    test names one carrier rather than "some write happened".

    The isolation is ONE-DIRECTIONAL. Removing THIS carrier fails only
    this test. Removing the PUSH carrier fails both, because the push
    runs first and an unadmitted exec 500s the handler before the save
    is reached.

    Kills: reverting ``_fsApplyPushBookkeeping``'s
    ``fdictCommitWorkflowSave(...)`` to ``dictCtx["save"](...)``.
    """
    client, connectionDocker = tclientSyncBound
    with _fnGithubPushHostSidePlanted():
        _fresponsePostGithubPush(client)
    _fnAssertSelectedRanUnder(
        connectionDocker,
        lambda dictReached: (
            dictReached["sPrimitive"] == S_PRIMITIVE_WRITE
            and dictReached["sPath"] == S_WORKFLOW_PATH
        ),
        mutationAdmission.S_ADMISSION_MODE_SYNCHRONOUS,
        f"write to {S_WORKFLOW_PATH}",
    )


@pytest.mark.falsification
def testTheBookkeepingSaveRefusalIsNotAbsorbedIntoAWarning(
    tclientSyncBound,
):
    """A refused bookkeeping save must escape its broad handler.

    ``_fsApplyPushBookkeeping`` catches ``Exception`` and answers
    "badges may lag until the next refresh", which is right for a
    genuine save failure and catastrophic for a carrier refusal: a
    route whose ``fdictCommitWorkflowSave`` call was deleted would answer
    200 with a friendly toast, and the migration's only proof would
    reach the researcher as a cosmetic lag.

    Drives the refusal directly rather than by deleting the carrier,
    because the guard being asserted is the
    ``fnReRaiseControlPlaneRefusal`` call and not the carrier itself.

    Kills: deleting the ``fnReRaiseControlPlaneRefusal(error)`` line
    from ``_fsApplyPushBookkeeping``.
    """
    client, _connectionDocker = tclientSyncBound

    def fnRefuseTheSave(*aArgs, **dictKwargs):
        raise mutationAdmission.MutationNotAdmittedError(
            "no carrier admission is live",
        )

    # The benign push FIRST, in this same fixture, so the assertion
    # below cannot pass on a refusal from somewhere else. Without it a
    # 403 at the ownership gate -- a client that never held the lease --
    # would also carry no ``sBookkeepingWarning``, and the test would
    # be green while measuring the wrong refusal entirely.
    with _fnGithubPushHostSidePlanted():
        responseBenign = _fresponsePostGithubPush(client)
    assert responseBenign.status_code == 200, (
        "the benign push was itself refused, so this fixture cannot "
        f"tell a save refusal from any other: {responseBenign.text}"
    )

    from vaibify.gui.routes import syncRoutes as moduleSync
    moduleSync._DICT_RECENT_PUSH_RESULTS.clear()
    with _fnGithubPushHostSidePlanted(), patch(
        "vaibify.gui.routes.syncRoutes.fdictCommitWorkflowSave",
        fnRefuseTheSave,
    ):
        responseHttp = _fresponsePostGithubPush(client)
    assert "sBookkeepingWarning" not in responseHttp.text, (
        "a refused save was absorbed into the bookkeeping warning; the "
        "researcher would be told the badges may lag while the real "
        "answer is that a carrier call is missing"
    )
    assert responseHttp.status_code == 500, (
        "the refusal must escape the handler, not be turned into a "
        f"successful push: {responseHttp.status_code}"
    )


@pytest.mark.falsification
def testThePushedTokenReachesNoJournalRecordOrResponse(tclientSyncBound):
    """A token in the origin URL never leaves the container's boundary.

    The push route is where credential redaction actually bites: the
    binding check READS ``git remote get-url origin`` inside the
    container, and a token-authenticated clone spells that URL with the
    credential in its user-info segment. Two sinks that outlive or
    leave the request are asserted here, and they fail independently --
    the journal is written by the carrier, the response body by the
    handler.

    The journal is the one this migration ADDED, which is why the
    operation targets the push carriers pass are compile-time constants
    (``"github-push"``, the workflow path) rather than anything derived
    from the request or from container output.

    Note what this does NOT assert: that the redactor is correct.
    ``fsRedactUrlCredentials`` leaves a bare-user-info token
    (``https://<token>@host/``) completely intact -- verified -- so the
    union form ``fsRedactCredentials`` is the only safe one, and
    ``credentialRedactor``'s own tests own that distinction.

    Kills: passing the remote URL as the ``sOperationTarget`` of
    ``_fdictPushToGithubUnderTheDrain`` instead of the constant
    ``"github-push"``.
    """
    client, _connectionDocker = tclientSyncBound
    with _fnGithubPushHostSidePlanted(), (
        _tlistCaptureEveryJournalPayload()
    ) as listPayloads:
        responseHttp = _fresponsePostGithubPush(client)
    assert S_PLANTED_TOKEN not in responseHttp.text, (
        "the planted token reached the HTTP response body"
    )
    assert listPayloads, (
        "the push wrote no journal payload at all, so the leak "
        "assertion below would pass vacuously; either no carrier was "
        "reached or the journal writer moved"
    )
    for sPayload in listPayloads:
        assert S_PLANTED_TOKEN not in sPayload, (
            "the planted token reached a journal record on disk; a "
            "journal record outlives the request and is read by "
            f"'vaibify reconcile'. Payload: {sPayload}"
        )


@pytest.mark.falsification
def testThePushedTokenReachesNoLogLine(tclientSyncBound, caplog):
    """The push's log lines never carry the origin URL's credential.

    Separated from the journal/response assertion because the sinks
    fail independently and a single test would not say which one
    leaked. The hub log is the sink a researcher is most likely to
    paste into an issue.

    Kills: adding the remote URL to any ``logger.info`` in the push
    chain -- e.g. logging the bound remote alongside "GitHub push
    requested".
    """
    client, _connectionDocker = tclientSyncBound
    with caplog.at_level(logging.DEBUG, logger="vaibify"), (
        _fnGithubPushHostSidePlanted()
    ):
        _fresponsePostGithubPush(client)
    assert S_PLANTED_TOKEN not in caplog.text, (
        "the planted token reached a hub log line"
    )


@contextlib.contextmanager
def _tlistRecordEveryOverleafPush():
    """Record the live admission at each ``ftResultPushToOverleaf`` call."""
    from vaibify.gui import syncDispatcher
    listCalls = []
    fnReal = syncDispatcher.ftResultPushToOverleaf

    def ftRecordThenPush(connectionDocker, sContainerId, *aArgs, **dictKw):
        admission = mutationAdmission.fadmissionActiveForContainerId(
            sContainerId,
        )
        listCalls.append("" if admission is None else admission.sMode)
        return fnReal(connectionDocker, sContainerId, *aArgs, **dictKw)

    with patch.object(
        syncDispatcher, "ftResultPushToOverleaf", ftRecordThenPush,
    ):
        yield listCalls


def _fresponsePostOverleafPush(client):
    """Push one figure through the migrated Overleaf push route."""
    with _fnOverleafHostMirrorStubbed():
        return client.post(
            f"/api/overleaf/{S_CONTAINER_ID}/push",
            json={
                "listFilePaths": [S_SYNC_PUSHABLE_FILE],
                "sCommitMessage": "carrier probe",
            },
        )


@pytest.mark.falsification
def testTheOverleafPushRunsUnderTheDrain(tclientSyncBound):
    """POST /api/overleaf/{id}/push uploads under a mode-(b) admission.

    The push streams the researcher's selected figures into an Overleaf
    project over the network from inside the container, so it runs for
    as long as the transfer takes. This carrier predates the route's
    declaration -- ``_ftRunOverleafPushCall`` already ran under the
    drain while the route rode the ambient mint -- and the assertion
    matters MORE now, not less: with the ambient mint withdrawn, this
    carrier is the only thing admitting the exec.

    Kills: reverting ``_ftRunOverleafPushCall`` to its
    ``await asyncio.to_thread(ftPushWorker)`` branch.
    """
    client, _connectionDocker = tclientSyncBound
    with _tlistRecordEveryOverleafPush() as listCalls:
        _fresponsePostOverleafPush(client)
    assert listCalls, (
        "the route never reached the Overleaf push, so this asserts "
        "nothing about the admission it runs under"
    )
    assert listCalls == [
        mutationAdmission.S_ADMISSION_MODE_LOCK_HELD
    ] * len(listCalls), (
        f"the Overleaf push ran under a non-lock-held admission: "
        f"{listCalls}"
    )


@pytest.mark.falsification
def testTheOverleafPushProvenanceRunsUnderItsOwnDrain(tclientSyncBound):
    """The push's provenance record holds a SECOND mode-(b) drain.

    A second invocation rather than an extension of the push's, because
    the push's supervisor released the drain when its worker
    terminated -- which is the property that makes mode (b) worth
    having. The digest refresh and the provenance manifest share this
    one drain deliberately: the manifest is what the L2 figure-freeze
    blockers read and the digests are what the Overleaf cells compare
    against, so a hand-over between them would leave the successor with
    figures recorded frozen at a commit whose fingerprints were never
    written.

    Selected on the HEAD-sha read the provenance recorder makes, which
    the push itself never runs.

    The isolation is ONE-DIRECTIONAL, in the same direction as the
    Zenodo trio and for the same reason: an unadmitted exec 500s the
    handler before anything later is reached. So all three Overleaf
    push tests failing means the UPLOAD, two means the PROVENANCE, and
    one means the save. Verified.

    Kills: reverting ``_fnFinalizeOverleafPush``'s bookkeeping pair to
    the two ``await asyncio.to_thread(...)`` hops it replaced.
    """
    client, connectionDocker = tclientSyncBound
    _fresponsePostOverleafPush(client)
    _fnAssertExecsNamingRanUnder(
        connectionDocker, S_OVERLEAF_PROVENANCE_MARKER,
        mutationAdmission.S_ADMISSION_MODE_LOCK_HELD,
    )


@pytest.mark.falsification
def testTheOverleafPushBookkeepingSaveCommitsSynchronously(
    tclientSyncBound,
):
    """The Overleaf push's ``project.json`` save runs under mode (a).

    The push handler's THIRD carrier. It persists the per-file sync
    status and the ``sLastPushCommit`` stamp the figure-freeze blockers
    key on, so a refused save would leave figures reading not-frozen
    after a push that did freeze them.

    Kills: reverting ``_fnFinalizeOverleafPush``'s
    ``fdictCommitWorkflowSave(...)`` to ``dictCtx["save"](...)``.
    """
    client, connectionDocker = tclientSyncBound
    _fresponsePostOverleafPush(client)
    _fnAssertSelectedRanUnder(
        connectionDocker,
        lambda dictReached: (
            dictReached["sPrimitive"] == S_PRIMITIVE_WRITE
            and dictReached["sPath"] == S_WORKFLOW_PATH
        ),
        mutationAdmission.S_ADMISSION_MODE_SYNCHRONOUS,
        f"write to {S_WORKFLOW_PATH}",
    )


# ---------------------------------------------------------------------
# The AICS Level 3 reproducibility surface (phase 2, group 2).
#
# Eight routes, three shapes. Three are one-line declaration saves that
# needed only a mode-(a) commit. Four write files INSIDE the project
# repo through the container repo-file adapter -- which, note, spends
# THREE primitive calls per logical write (``mkdir -p`` exec, ``.tmp``
# write, ``mv -f`` exec), so "one write" is never one gate crossing
# here. And one is a POST that writes nothing at all.
# ---------------------------------------------------------------------

S_ENVIRONMENT_JSON_PATH = posixpath.join(
    S_PROJECT_REPO, ".vaibify", "environment.json",
)
S_REPRODUCE_SCRIPT_PATH = posixpath.join(S_PROJECT_REPO, "reproduce.sh")


def _fdictLevelThreeWorkflow():
    """Return the draft workflow bound to a project repo.

    The draft harness's document carries none, so every L3 route would
    refuse ("Workflow has no project repo") before reaching a carrier
    -- a fixture under which these tests would pass having exercised
    the refusal.
    """
    dictWorkflow = copy.deepcopy(DICT_WORKFLOW)
    dictWorkflow["sProjectRepoPath"] = S_PROJECT_REPO
    return dictWorkflow


class DockerDoubleServingALevelThreeWorkflow(
    DockerDoubleThatCallsTheRealGates,
):
    """The gate-faithful double over a repo-bound workflow."""

    def fbaFetchFile(self, sContainerId, sPath, iMaxBytes=None):
        if sPath == S_WORKFLOW_PATH:
            return json.dumps(_fdictLevelThreeWorkflow()).encode("utf-8")
        return super().fbaFetchFile(sContainerId, sPath, iMaxBytes)


@pytest.fixture
def tclientLevelThree():
    """The gated client over a workflow with a project repo."""
    return _tConnectGatedClient(DockerDoubleServingALevelThreeWorkflow())


def _fnAssertTheDeclarationSavedSynchronously(connectionDocker):
    """Assert the route's ``project.json`` write ran under mode (a).

    The three L3 declaration routes are one shape and one logical
    mutation apiece: validate a body in memory, edit the workflow,
    write it once. Mode (a) rather than (b) because there is no
    ``await`` between the last in-memory edit and the bytes landing, so
    there is nothing for a hand-over to slip into and no worker whose
    life a drain would need to cover.

    They are three TESTS rather than one parametrization so that each
    route's carrier call gets its own kill-confirm: a parametrized test
    takes one registry entry, which would leave two of the three calls
    provably reached but not provably load-bearing.
    """
    _fnAssertSelectedRanUnder(
        connectionDocker,
        lambda dictReached: (
            dictReached["sPrimitive"] == S_PRIMITIVE_WRITE
            and dictReached["sPath"] == S_WORKFLOW_PATH
        ),
        mutationAdmission.S_ADMISSION_MODE_SYNCHRONOUS,
        f"write to {S_WORKFLOW_PATH}",
    )


@pytest.mark.falsification
def testTheDeterminismDeclarationCommitsSynchronously(tclientLevelThree):
    """POST .../determinism/declare saves under mode (a).

    Kills: reverting ``fdictDeclareDeterminism``'s ``fdictCommitWorkflowSave``
    to ``dictCtx["save"](sContainerId, dictWorkflow)``.
    """
    client, connectionDocker = tclientLevelThree
    client.post(
        f"/api/workflow/{S_CONTAINER_ID}/determinism/declare",
        json={"dOmpNumThreads": 1},
    )
    _fnAssertTheDeclarationSavedSynchronously(connectionDocker)


@pytest.mark.falsification
def testTheBinaryDeclarationCommitsSynchronously(tclientLevelThree):
    """POST .../binaries/declare saves under mode (a).

    Kills: reverting ``fdictDeclareBinaries``'s ``fdictCommitWorkflowSave``
    to ``dictCtx["save"](sContainerId, dictWorkflow)``.
    """
    client, connectionDocker = tclientLevelThree
    client.post(
        f"/api/workflow/{S_CONTAINER_ID}/binaries/declare",
        json={"bNoStandaloneBinaries": True, "listDeclaredBinaries": []},
    )
    _fnAssertTheDeclarationSavedSynchronously(connectionDocker)


@pytest.mark.falsification
def testTheDeterminismDeletionCommitsSynchronously(tclientLevelThree):
    """DELETE .../determinism saves under mode (a).

    Kills: reverting ``fdictDeleteDeterminism``'s ``fdictCommitWorkflowSave``
    to ``dictCtx["save"](sContainerId, dictWorkflow)``.
    """
    client, connectionDocker = tclientLevelThree
    client.delete(f"/api/workflow/{S_CONTAINER_ID}/determinism")
    _fnAssertTheDeclarationSavedSynchronously(connectionDocker)


@pytest.mark.falsification
def testTheBinaryCaptureRunsUnderTheDrain(tclientLevelThree):
    """POST .../binaries/capture hashes, runs and merges under mode (b).

    Mode (b) rather than (a) for two reasons that compound. The capture
    RUNS the declared binary (``<path> --version``, bounded at five
    seconds), which does not belong on the event loop where it used to
    sit. And the environment record is read-modify-written with no lock
    of its own, so two captures arriving together could each read the
    file before either wrote and one entry would silently vanish -- the
    drain is now that lock.

    Selected on the WRITE to environment.json rather than on every
    primitive, because the adapter spends a ``mkdir -p`` exec, a
    ``.tmp`` write and an ``mv -f`` exec per logical write and the
    route's connect handler contributes execs of its own.

    Kills: reverting ``fnCaptureBinary`` to calling
    ``fdictCaptureSingleBinary`` + ``_fnAppendBinaryToEnvironmentJson``
    directly on the event loop, which reaches the write primitive with
    no admission and records ``''`` here.
    """
    client, connectionDocker = tclientLevelThree
    client.post(
        f"/api/workflow/{S_CONTAINER_ID}/binaries/capture",
        json={"sBinaryPath": "/usr/bin/env"},
    )
    _fnAssertSelectedRanUnder(
        connectionDocker,
        lambda dictReached: (
            dictReached["sPrimitive"] == S_PRIMITIVE_WRITE
            and dictReached["sPath"].startswith(S_ENVIRONMENT_JSON_PATH)
        ),
        mutationAdmission.S_ADMISSION_MODE_LOCK_HELD,
        f"write to {S_ENVIRONMENT_JSON_PATH}",
    )


@pytest.mark.falsification
def testTheReproduceScriptAndItsManifestRepinShareOneDrain(
    tclientLevelThree,
):
    """POST .../level3/reproduce-script writes and re-pins under ONE drain.

    One carrier for both halves, because the re-pin is what makes the
    script count: the Level 3 check requires the script's hash IN the
    manifest, and without the re-pin the check stayed red after every
    generation -- which read to the researcher as "the button did
    nothing". A hand-over landing between them leaves the successor
    with a script the manifest does not know about, which is exactly
    the state that bug produced.

    Asserted on BOTH writes at once, which is the point: proving they
    ran under the same MODE is not the same as proving they ran under
    one drain, but a mutant that splits them into two carriers leaves
    both under lock-held and would pass. So the shared-drain claim
    rests on the source, and what this pins is that neither half
    reaches the container uncarried.

    Kills: reverting ``_fbRepinManifestOrWarn``'s call site to
    ``await asyncio.to_thread(manifestWriter.fnWriteManifest, ...)``
    outside the worker, which reaches the write primitive with no
    admission.
    """
    client, connectionDocker = tclientLevelThree
    client.post(
        f"/api/workflow/{S_CONTAINER_ID}/level3/reproduce-script",
    )
    _fnAssertSelectedRanUnder(
        connectionDocker,
        lambda dictReached: (
            dictReached["sPrimitive"] == S_PRIMITIVE_WRITE
            and dictReached["sPath"].startswith(S_REPRODUCE_SCRIPT_PATH)
        ),
        mutationAdmission.S_ADMISSION_MODE_LOCK_HELD,
        f"write to {S_REPRODUCE_SCRIPT_PATH}",
    )
    _fnAssertSelectedRanUnder(
        connectionDocker,
        lambda dictReached: (
            dictReached["sPrimitive"] == S_PRIMITIVE_WRITE
            and "MANIFEST.sha256" in dictReached["sPath"]
        ),
        mutationAdmission.S_ADMISSION_MODE_LOCK_HELD,
        "write to MANIFEST.sha256",
    )


@pytest.mark.falsification
def testAReproduceScriptRepinRefusalIsNotAbsorbedIntoAFlag(
    tclientLevelThree,
):
    """A refused manifest re-pin must escape its broad handler.

    ``_fbRepinManifestOrWarn`` catches ``Exception`` and answers
    ``bManifestRefreshed: False``, which is right for a genuine hash
    failure and wrong for a carrier refusal: a route whose carrier call
    was deleted would answer 200 with a soft flag, and the migration's
    only proof would reach the researcher as an unchecked box.

    The benign request runs FIRST in the same fixture, so this cannot
    pass on a refusal arriving from the ownership gate instead.

    Kills: deleting the ``fnReRaiseControlPlaneRefusal(exc)`` line from
    ``_fbRepinManifestOrWarn``.
    """
    client, _connectionDocker = tclientLevelThree
    responseBenign = client.post(
        f"/api/workflow/{S_CONTAINER_ID}/level3/reproduce-script",
    )
    assert responseBenign.status_code == 200, (
        "the benign generation was itself refused, so this fixture "
        f"cannot tell a re-pin refusal from any other: "
        f"{responseBenign.text}"
    )

    def fnRefuseTheRepin(*aArgs, **dictKwargs):
        raise mutationAdmission.MutationNotAdmittedError(
            "no carrier admission is live",
        )

    with patch(
        "vaibify.reproducibility.manifestWriter.fnWriteManifest",
        fnRefuseTheRepin,
    ):
        responseHttp = client.post(
            f"/api/workflow/{S_CONTAINER_ID}/level3/reproduce-script",
        )
    assert responseHttp.status_code == 500, (
        "the refusal must escape the handler, not become "
        f"bManifestRefreshed: False -- got {responseHttp.status_code} "
        f"{responseHttp.text}"
    )


@pytest.mark.falsification
def testTheEnvelopeRegenerationRunsUnderTheDrain(tclientLevelThree):
    """POST .../level3/envelope writes its three tiers under mode (b).

    The generator writes MANIFEST.sha256, requirements.lock and
    .vaibify/environment.json across three tiers, each isolating its
    own failure on the stated principle that a partial envelope beats
    no envelope. Those handlers cannot absorb a carrier refusal --
    ``ControlPlaneRefusalError`` descends from ``Exception`` alone and
    every tier catches a narrower type (verified at the console) -- so
    a forgotten carrier still raises out of the worker rather than
    being logged as a skipped tier.

    Kills: reverting ``_fdictRegenerateEnvelopeUnderTheDrain`` to
    ``await asyncio.to_thread(dataArchiver.fnGenerate...)``.
    """
    client, connectionDocker = tclientLevelThree
    client.post(f"/api/workflow/{S_CONTAINER_ID}/level3/envelope")
    _fnAssertSelectedRanUnder(
        connectionDocker,
        lambda dictReached: (
            dictReached["sPrimitive"] == S_PRIMITIVE_WRITE
            and "MANIFEST.sha256" in dictReached["sPath"]
        ),
        mutationAdmission.S_ADMISSION_MODE_LOCK_HELD,
        "write to MANIFEST.sha256",
    )


@pytest.mark.falsification
def testTheEnvelopeReadinessReReadJoinsTheSameCarrier(tclientLevelThree):
    """The envelope's readiness re-read is carried, not left on the loop.

    The single subtlety of this route. ``fdictL3ReadinessGaps`` LOOKS
    like a read and is not one at the boundary that decides: it hashes
    the repository to compare the manifest digest, which
    ``ContainerRepoFiles.fdictHashFiles`` implements by running a
    script through the GENERAL exec primitive. It used to run on the
    event loop after the generation's thread returned; under the
    enforced branch that is refused, so it moved INSIDE the worker.

    Instrumented at ``fdictL3ReadinessGaps`` rather than by matching
    command text, for the reason ``_tlistRecordEveryZenodoUpload``
    states: the hash travels base64-encoded inside a ``python3 -c
    "import base64; exec(...)"`` shell -- the shape ``repoFiles`` uses
    for EVERY embedded script -- so a text marker could not tell this
    hash from the three tiers' own. The real function is still called,
    so its container work and gate crossings happen as in production.

    Kills: offloading the ``fdictL3ReadinessGaps`` call to a bare
    thread, which is the realistic way it leaves the carrier -- a
    fresh thread inherits no contextvars, so the admission is absent
    and the hash exec is refused. That mutant kills on the MODE, not
    merely on the re-read being gone.
    """
    client, _connectionDocker = tclientLevelThree
    listCalls = []
    fnReal = reproducibilityRoutes.fdictL3ReadinessGaps

    def fdictRecordThenRead(dictWorkflow, filesRepo):
        admission = mutationAdmission.fadmissionActiveForContainerId(
            S_CONTAINER_ID,
        )
        listCalls.append("" if admission is None else admission.sMode)
        return fnReal(dictWorkflow, filesRepo)

    with patch.object(
        reproducibilityRoutes, "fdictL3ReadinessGaps",
        fdictRecordThenRead,
    ):
        client.post(f"/api/workflow/{S_CONTAINER_ID}/level3/envelope")
    assert listCalls, (
        "the route never reached the readiness re-read, so this "
        "asserts nothing about the admission it runs under"
    )
    assert listCalls == [
        mutationAdmission.S_ADMISSION_MODE_LOCK_HELD
    ] * len(listCalls), (
        f"the readiness re-read ran outside the drain: {listCalls}"
    )


@pytest.mark.falsification
def testTheDependencyLockVerifyReachesNoMutatingPrimitive(
    tclientLevelThree,
):
    """POST .../dependencies/verify is a typed read despite the verb.

    The one route in this group that writes nothing. It is declared
    ``typed-read`` on the strength of what it CALLS -- ``fbIsFile`` and
    ``fsReadText``, both of which reach the container through the
    typed-read adapter -- not on the strength of its HTTP method, which
    is POST because the GUI models it as an action.

    Asserted in the two halves a typed-read claim needs, because either
    alone is vacuous: the gated ledger must be EMPTY (it reached no
    mutation-capable primitive) AND the typed-path ledger must be
    NON-empty (it did real container work rather than returning early
    at a 409).

    Kills: reimplementing ``flistVerifyRequirementsLock``'s existence
    check as ``filesRepo.ftRunCommand(["test", "-f", ...])``, which is
    the general exec primitive and lands in the gated ledger.
    """
    client, connectionDocker = tclientLevelThree
    connectionDocker.listTypedPathProbes.clear()
    client.post(f"/api/workflow/{S_CONTAINER_ID}/dependencies/verify")
    assert connectionDocker.listAdmittedPrimitives == [], (
        "a route declared typed-read reached a mutation-capable "
        f"primitive: {connectionDocker.listAdmittedPrimitives}"
    )
    assert connectionDocker.listTypedPathProbes, (
        "the route reached no typed read either, so the assertion "
        "above is vacuous: it probably refused before touching the "
        "container at all"
    )


# ---------------------------------------------------------------------
# The two routes left over from the group boundaries (phase 2).
#
# Neither is a save and neither belonged to a family: the AI-declaration
# template generator lives in levelRoutes and the manifest verify in
# pipelineRoutes, and each was the last awaiting route in its module.
# ---------------------------------------------------------------------

S_AI_DECLARATION_RELATIVE_PATH = ".vaibify/aiDeclaration.md"
S_AI_DECLARATION_ABS_PATH = posixpath.join(
    S_PROJECT_REPO, S_AI_DECLARATION_RELATIVE_PATH,
)


@pytest.mark.falsification
def testTheDeclarationTemplateProbeAndWriteShareOneDrain(
    tclientLevelThree,
):
    """POST .../ai-declaration/generate-template carries probe + write.

    The probe is the GUARD -- "generate only if absent" -- so it and the
    write belong to ONE carrier. Split across two, a second tab or the
    in-container agent could pass the absence check between them and the
    loser's blank template would overwrite a declaration the researcher
    had already started editing. The generator's own ``FileExistsError``
    is a second line of defence, not a substitute: it raises after the
    drain would already have been released.

    Selected on the write to the declaration path, because the adapter
    spends a ``mkdir -p`` exec and an ``mv -f`` exec around it and the
    connect handler contributes execs of its own.

    Kills: reverting ``_fdictGenerateTemplateUnderTheDrain`` to calling
    ``_fdictProbeThenWriteTemplate`` directly on the event loop, which
    reaches the write primitive with no admission and records ``''``.
    """
    client, connectionDocker = tclientLevelThree
    client.post(
        f"/api/workflow/{S_CONTAINER_ID}"
        "/ai-declaration/generate-template",
        json={"sRelativePath": S_AI_DECLARATION_RELATIVE_PATH},
    )
    _fnAssertSelectedRanUnder(
        connectionDocker,
        lambda dictReached: (
            dictReached["sPrimitive"] == S_PRIMITIVE_WRITE
            and dictReached["sPath"].startswith(
                S_AI_DECLARATION_ABS_PATH,
            )
        ),
        mutationAdmission.S_ADMISSION_MODE_LOCK_HELD,
        f"write to {S_AI_DECLARATION_ABS_PATH}",
    )


class DockerDoubleHoldingAnExistingDeclaration(
    DockerDoubleServingALevelThreeWorkflow,
):
    """The L3 double, answering that ONE declaration path exists.

    Modelled at the typed-read probe rather than by generating the file
    first, because the adapter's atomic write lands its bytes at
    ``<path>.tmp`` and installs them with an ``mv -f`` exec the
    in-memory double does not execute -- so a generated file is not
    where the next probe looks, and a two-call test would exercise the
    absent path twice while appearing to exercise the taken one.
    """

    def fbContainerPathIsFile(self, sContainerId, sPath):
        bExists = sPath == S_AI_DECLARATION_ABS_PATH
        if not bExists:
            return super().fbContainerPathIsFile(sContainerId, sPath)
        tokenRead = mutationAdmission.ftokenEnterAuditedRead()
        try:
            mutationAdmission.fnAssertContainerCommandAdmitted(
                sContainerId, S_PRIMITIVE_EXEC,
            )
        finally:
            mutationAdmission.fnExitAuditedRead(tokenRead)
        self.listTypedPathProbes.append(sPath)
        return True


@pytest.fixture
def tclientDeclarationTaken():
    """The gated client over a repo that already holds a declaration."""
    return _tConnectGatedClient(
        DockerDoubleHoldingAnExistingDeclaration(),
    )


@pytest.mark.falsification
def testAnExistingDeclarationIsRefusedWithoutQuarantining(
    tclientDeclarationTaken,
):
    """The already-exists 409 is carried back, never poisoned.

    The refusal is decided with the container UNTOUCHED -- the file is
    simply already there -- so it travels back as a value and is
    re-raised after the supervisor settled its journal record normally.
    A raise from inside the worker would settle through the failure path
    and quarantine a perfectly healthy container over "you already have
    a declaration", which is a message, not an incident.

    A generation at a DIFFERENT path runs first in the same fixture, so
    the 409 below cannot be a refusal arriving from the ownership gate
    instead -- the trap where a green test measures the wrong refusal.

    Kills: dropping the ``fdictCarryARefusalBackInsteadOfRaising``
    wrapper from ``fdictGenerateTheTemplate``, so the 409 raises out of the
    worker; the response then becomes a 500 rather than the 409 the
    dashboard tells the researcher to act on.
    """
    client, _connectionDocker = tclientDeclarationTaken
    responseFree = client.post(
        f"/api/workflow/{S_CONTAINER_ID}"
        "/ai-declaration/generate-template",
        json={"sRelativePath": ".vaibify/someOtherDeclaration.md"},
    )
    assert responseFree.status_code == 200, (
        "a generation at a free path was itself refused, so this "
        "fixture cannot tell an already-exists refusal from any "
        f"other: {responseFree.text}"
    )
    responseTaken = client.post(
        f"/api/workflow/{S_CONTAINER_ID}"
        "/ai-declaration/generate-template",
        json={"sRelativePath": S_AI_DECLARATION_RELATIVE_PATH},
    )
    assert responseTaken.status_code == 409, (
        "a taken declaration path must be refused 409, not 500: "
        f"{responseTaken.status_code} {responseTaken.text}"
    )
    # The STATUS is not the guarantee, and asserting it alone was this
    # test's first, failed kill-confirm: a 409 raised out of the worker
    # still reaches the client as a 409, because FastAPI renders an
    # HTTPException the same either way. What differs is the JOURNAL --
    # a worker that raises is settled through the failure path and
    # quarantines the container. That is the thing to assert.
    from vaibify.config import operationJournal
    dictResolution = operationJournal.fdictResolveContainerJournal(
        S_CONTAINER_NAME,
    )
    assert dictResolution["sResolution"] != (
        operationJournal.S_RESOLUTION_QUARANTINED
    ), (
        "an already-present declaration quarantined the container: "
        f"{dictResolution}. The researcher now has to run 'vaibify "
        "reconcile' because they asked twice for the same template."
    )


@pytest.mark.falsification
def testTheManifestVerifyRunsUnderTheDrain(tclientLevelThree):
    """POST .../manifest/verify re-hashes under a mode-(b) admission.

    This route reads like a read and is not one at the boundary that
    decides: ``flistVerifyManifest`` re-hashes every pinned file by
    running a script through the GENERAL exec primitive, which the gate
    must treat as mutating because command text cannot be told apart
    from a delete. ``typed-read`` would therefore be FALSE here even
    though the route writes nothing -- the same judgement the Overleaf
    diff already records.

    Instrumented at ``flistVerifyManifest`` rather than by matching
    command text, because the hash travels base64-encoded inside a
    ``python3 -c "import base64; exec(...)"`` shell, the shape
    ``repoFiles`` uses for every embedded script.

    Kills: reverting ``_fdictVerifyManifestUnderTheDrain`` to the two
    ``await asyncio.to_thread(...)`` hops it replaced.
    """
    client, _connectionDocker = tclientLevelThree
    from vaibify.reproducibility import manifestWriter
    listCalls = []
    fnReal = manifestWriter.flistVerifyManifest

    def flistRecordThenVerify(filesRepo):
        admission = mutationAdmission.fadmissionActiveForContainerId(
            S_CONTAINER_ID,
        )
        listCalls.append("" if admission is None else admission.sMode)
        return fnReal(filesRepo)

    with patch.object(
        manifestWriter, "flistVerifyManifest", flistRecordThenVerify,
    ):
        client.post(f"/api/workflow/{S_CONTAINER_ID}/manifest/verify")
    assert listCalls, (
        "the route never reached the manifest verify, so this asserts "
        "nothing about the admission it runs under"
    )
    assert listCalls == [
        mutationAdmission.S_ADMISSION_MODE_LOCK_HELD
    ] * len(listCalls), (
        f"the manifest verify ran outside the drain: {listCalls}"
    )


@pytest.mark.falsification
def testAMissingManifestIsRefusedWithoutQuarantining(tclientLevelThree):
    """The manifest verify's 409 is carried back, never poisoned.

    A missing MANIFEST.sha256 is decided with the container untouched:
    the verify read for a file and did not find it. Raising it out of
    the worker would settle through the failure path and quarantine the
    container until the researcher ran ``vaibify reconcile`` -- over a
    manifest that simply has not been generated yet, which is the
    ordinary state of a workflow before its first run.

    Kills: dropping the ``fdictCarryARefusalBackInsteadOfRaising``
    wrapper from ``fdictVerifyTheManifest``, which turns the 409 into a
    500 and poisons the journal record.
    """
    client, _connectionDocker = tclientLevelThree
    responseHttp = client.post(
        f"/api/workflow/{S_CONTAINER_ID}/manifest/verify",
    )
    assert responseHttp.status_code == 409, (
        "a workflow with no manifest must be told to generate one, not "
        f"handed a 500: {responseHttp.status_code} {responseHttp.text}"
    )
    # The STATUS is not the guarantee -- a 409 raised out of the worker
    # still renders as a 409 -- so the JOURNAL is what this asserts.
    # See the sibling declaration test for the failed kill-confirm that
    # established this.
    from vaibify.config import operationJournal
    dictResolution = operationJournal.fdictResolveContainerJournal(
        S_CONTAINER_NAME,
    )
    assert dictResolution["sResolution"] != (
        operationJournal.S_RESOLUTION_QUARANTINED
    ), (
        "a missing MANIFEST.sha256 quarantined the container: "
        f"{dictResolution}. A workflow that has not run yet has no "
        "manifest, so this would quarantine on the ordinary path."
    )


@contextlib.contextmanager
def _fnFalsificationApplicable():
    """Report the step as falsifiable, leaving the version probe real.

    The applicability classification is a judgement over the step's
    declared quantitative tests and its source tree, which the
    in-memory double cannot realistically satisfy; stubbing it is what
    lets the route reach the container call this file is about. The
    ``cosmic-ray --version`` probe is deliberately NOT stubbed -- it is
    the exec whose admission the pre-flight test asserts.
    """
    from vaibify.gui.routes import falsificationRoutes as moduleFals
    with patch.object(
        moduleFals, "fdictClassifyFalsificationApplicability",
        lambda dictStep, filesRepo: {
            "bApplicable": True, "sReason": "",
            "sClassification": "quantitative",
        },
    ):
        yield



# ---------------------------------------------------------------------
# The two durable launches (phase 2, group 3).
#
# Both return their response while the work continues, and both used to
# start it with a bare ``asyncio.create_task`` recorded in a
# module-global dict no other authority read. So an ownership
# hand-over, the shutdown drain and the idle watchdog all saw an IDLE
# container while a full workflow rerun -- or cosmic-ray rewriting the
# project's sources in place -- was writing to it.
#
# Mode (c) is what closes that. These tests therefore assert two
# different things per route: that the pre-flight probes ran under the
# drain, and that the launched work is VISIBLE to the authorities that
# ask whether the container is busy. The second is the point; a test
# that only checked the response's "bAccepted" would pass for the bare
# create_task this replaced.
# ---------------------------------------------------------------------

S_COSMIC_RAY_PROBE_MARKER = "cosmic-ray --version"


@contextlib.contextmanager
def _teventHoldTheDurableWorkerOpen(moduleRoute, sWorkerName):
    """Replace a durable worker with one that waits, and hand back the gate.

    The real workers run cosmic-ray or a whole workflow rerun; neither
    can be driven to completion here, and neither needs to be. What the
    mode-(c) claim is about is the WINDOW between launching the work and
    its finishing -- exactly the window in which a hand-over used to see
    an idle container -- so the substitute holds that window open until
    the test closes it.

    The substitute is a coroutine function, which is what the real
    workers are: passing a synchronous one would make the launch's
    ``fnStartTask`` fail rather than the assertion speak.

    The gate is a ``threading.Event``, polled, rather than an
    ``asyncio.Event``, so this helper is usable from either driver
    without binding to a loop it was not created on.
    """
    eventMayFinish = threading.Event()

    async def fnWaitThenReturn(*aArgs, **dictKwargs):
        while not eventMayFinish.is_set():
            await asyncio.sleep(0.005)

    with patch.object(moduleRoute, sWorkerName, fnWaitThenReturn):
        try:
            yield eventMayFinish
        finally:
            eventMayFinish.set()


def _tBuildAsgiHubOverALevelThreeWorkflow():
    """Return ``(app, connectionDocker)`` bound to a repo-holding workflow.

    In-loop ASGI rather than ``TestClient`` for the durable-visibility
    tests below, and NOT as a stylistic preference. ``TestClient``
    enters a fresh blocking portal PER REQUEST and tears it down when
    the response is returned, which cancels every task the request
    started -- so a durable launch is always dead by the time the test
    body looks, and "the container reads idle" is indistinguishable
    from the defect these tests exist to catch. Measured: the registry
    was empty immediately after a 200 whose task was demonstrably
    still supposed to be running.
    """
    connectionDocker = DockerDoubleServingALevelThreeWorkflow()
    with patch.object(
        pipelineServer, "_fconnectionCreateDocker",
        lambda: connectionDocker,
    ):
        app = pipelineServer.fappCreateApplication(
            sWorkspaceRoot="/workspace",
            sTerminalUserArg="testuser",
        )
    return (app, connectionDocker)


@contextlib.contextmanager
def _tclientAsgiOverALevelThreeWorkflow():
    """Yield ``(app, clientAsync)``; caller must connect and set the lease."""
    app, _connectionDocker = _tBuildAsgiHubOverALevelThreeWorkflow()
    yield app, httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=app, raise_app_exceptions=False,
        ),
        base_url="http://hub",
        headers={"X-Session-Token": fsBootstrapCredential(app)},
    )


@pytest.mark.falsification
def testTheFalsificationPreflightRunsUnderTheDrain(tclientLevelThree):
    """POST .../run-falsification probes cosmic-ray under mode (b).

    The pre-flight asks one question -- "may this run start" -- in two
    container calls: the applicability classification hashes the step's
    sources, and the version probe runs ``cosmic-ray --version``. They
    share one drain because answering half of it against a container
    that changed hands in between would let a run start against a tree
    its applicability was never judged on.

    NOTE, because it is the reason this test exists at all: before this
    migration NO test drove this route. ``testFalsificationAttestation``
    asserts only that the path is REGISTERED. So the pre-flight had
    never been executed by the suite, and a carrier added to it would
    have been reached by nothing.

    The isolation is ONE-DIRECTIONAL: this carrier runs first, so an
    unadmitted exec 500s the handler before the launch is reached and
    the visibility test fails too. The reverse does not hold. Verified.

    Kills: reverting ``_ftClassifyAndProbeCosmicRay`` to the two
    ``await asyncio.to_thread(...)`` hops it replaced, which reach the
    exec primitive with no admission and record ``''``.
    """
    client, connectionDocker = tclientLevelThree
    with _fnFalsificationApplicable(), _teventHoldTheDurableWorkerOpen(
        falsificationRoutes, "_fnRunFalsificationWorker",
    ):
        client.post(
            f"/api/steps/{S_CONTAINER_ID}/0/run-falsification",
        )
    _fnAssertExecsNamingRanUnder(
        connectionDocker, S_COSMIC_RAY_PROBE_MARKER,
        mutationAdmission.S_ADMISSION_MODE_LOCK_HELD,
    )


@pytest.mark.falsification
@pytest.mark.asyncio
async def testTheLaunchedFalsificationRunIsVisibleAsLiveWork():
    """A live falsification run makes its container read BUSY.

    This is what mode (c) buys and the whole reason the route was
    migrated. cosmic-ray rewrites the step's sources IN PLACE, so a
    hand-over, a shutdown drain or the idle watchdog acting on that
    container mid-run would act on a repository being actively
    rewritten. Registering the task under the briefly-held mutation
    lock is what lets ``fsetNamesWithLiveMutationWork`` see it.

    Asserted through the authority's own reader rather than by
    inspecting the registry, because the reader is what every caller
    uses and a test against the dict would pass for a registry nobody
    consults.

    Kills: reverting ``_fdictLaunchFalsificationDurably`` to
    ``asyncio.create_task`` + ``_fnRegisterFalsificationTask``, i.e. the
    bare launch it replaced, which leaves the container reading idle.
    """
    with _tclientAsgiOverALevelThreeWorkflow() as (app, clientAsync):
        async with clientAsync:
            sLease = await _tConnectOverAsgi(clientAsync)
            clientAsync.headers["X-Vaibify-Lease"] = sLease
            sName = _fsContainerNameFor(app)
            with _fnFalsificationApplicable(), (
                _teventHoldTheDurableWorkerOpen(
                    falsificationRoutes, "_fnRunFalsificationWorker",
                )
            ):
                response = await clientAsync.post(
                    f"/api/steps/{S_CONTAINER_ID}/0/run-falsification",
                )
                assert response.status_code == 200, (
                    "the run was refused before it could be launched, "
                    f"so this asserts nothing: {response.text}"
                )
                assert commitCarrier.fbContainerHasLiveMutationWork(
                    app.state, sName,
                ), (
                    "a live falsification run left its container "
                    "reading idle; a hand-over or the idle watchdog "
                    "would act on a repository whose sources cosmic-ray "
                    "is rewriting in place"
                )
            await asyncio.sleep(0.05)


@pytest.mark.falsification
@pytest.mark.asyncio
async def testABadgeRefreshUnderAHeldDrainIsPausedRatherThanQueued():
    """Group 7b's second busy state: the drain held by a NON-carrier.

    ``hostControlChannel``'s reconcile and break-glass, and the start
    reservation, all take the container's mutation drain without
    registering a supervisor — so the supervisor registry alone cannot
    see them, and a read that consulted only the registry would sit on
    ``acquire()`` for as long as a reconcile takes. The lock is held
    here the same way those hold it: directly, registering nothing.

    The GET is bounded by ``wait_for`` because the failure being
    excluded is a WAIT: without the lock check this request never
    returns while this block holds the lock, and an unbounded await
    would hang the suite instead of failing it.

    The reason is not asserted, deliberately. With no supervisor and no
    durable record for this container, the lock branch is the only one
    that can answer paused at all, so the flag identifies the branch by
    itself — and pinning the generic wording here would make this test
    fail for the self-exclusion mutant that
    ``testAQuietContainerIsNeverReportedAsBusy`` owns.

    Kills: deleting the ``lockMutation.locked()`` branch from
    ``_fsDescribeWorkBesidesThisSupervisor``, which returns this read
    to queueing behind a holder that registers nothing.
    """
    with _tclientAsgiOverALevelThreeWorkflow() as (app, clientAsync):
        async with clientAsync:
            sLease = await _tConnectOverAsgi(clientAsync)
            clientAsync.headers["X-Vaibify-Lease"] = sLease
            sName = _fsContainerNameFor(app)
            lockMutation = (
                sessionLifecycle.flockContainerMutationForAppState(
                    app.state, sName,
                )
            )
            async with lockMutation:
                response = await asyncio.wait_for(
                    clientAsync.get(
                        f"/api/git/{S_CONTAINER_ID}/badges",
                    ),
                    2.0,
                )
            assert response.status_code == 200, response.text
            dictBody = response.json()
            assert dictBody.get("bRefreshPaused") is True, dictBody
            assert "dictBadges" not in dictBody, dictBody


@pytest.mark.falsification
@pytest.mark.asyncio
async def testABadgeRefreshOverALiveDurableRunIsPaused():
    """Group 7b's third busy state, driven where the machinery for it is.

    A durable task holds NO drain: it takes the lock to register and
    releases it before the work runs. So the lock alone cannot answer
    whether a run is live, and an automatic read consulting only the
    lock would read a repository the run is rewriting and publish that
    torn snapshot as settled state. This is the host-mode case in
    miniature -- on the host a step's files ARE the repository's files.

    Asserted on the paused REASON, not merely on the flag: the drain
    and supervisor branches would both answer paused here too if they
    were reached, and the generic durable wording is what identifies
    which branch answered.

    Kills: deleting the durable-registry branch from
    ``_fsDescribeWorkBesidesThisSupervisor``, which leaves a live run
    invisible to the pause and lets the refresh read straight through
    it.
    """
    with _tclientAsgiOverALevelThreeWorkflow() as (app, clientAsync):
        del app
        async with clientAsync:
            sLease = await _tConnectOverAsgi(clientAsync)
            clientAsync.headers["X-Vaibify-Lease"] = sLease
            with _fnFalsificationApplicable(), (
                _teventHoldTheDurableWorkerOpen(
                    falsificationRoutes, "_fnRunFalsificationWorker",
                )
            ):
                response = await clientAsync.post(
                    f"/api/steps/{S_CONTAINER_ID}/0/run-falsification",
                )
                assert response.status_code == 200, (
                    "the run was refused before it could be launched, "
                    f"so nothing is live to pause behind: {response.text}"
                )
                responseBadges = await clientAsync.get(
                    f"/api/git/{S_CONTAINER_ID}/badges",
                )
                assert responseBadges.status_code == 200, (
                    responseBadges.text
                )
                dictBody = responseBadges.json()
                assert dictBody.get("bRefreshPaused") is True, dictBody
                assert dictBody["sPausedBy"] == (
                    commitCarrier.S_DESCRIBED_DURABLE_TASK
                ), (
                    "the pause fired, but not from the durable branch: "
                    f"{dictBody}"
                )
            await asyncio.sleep(0.05)


@pytest.mark.falsification
def testTheVerifyReadinessGateRunsUnderTheDrain(tclientLevelThree):
    """POST .../level3/verify gates readiness under mode (b).

    The readiness gate and the manifest-digest snapshot both hash the
    repository through the general exec primitive, and they share one
    drain because they must agree: the attestation is keyed to the
    digest snapshotted here, so a digest taken from a tree that changed
    after the readiness check passed would attest a state nobody
    verified.

    Instrumented at ``fbL3ReadinessOK`` rather than by matching command
    text, because the hash travels base64-encoded inside a ``python3
    -c "import base64; exec(...)"`` shell.

    The isolation is ONE-DIRECTIONAL, in the same direction and for
    the same reason as the falsification pre-flight's. Verified.

    Kills: reverting ``_fsGateReadinessAndSnapshotDigest`` to calling
    ``fbL3ReadinessOK`` and ``fsCurrentManifestDigest`` directly on the
    event loop.
    """
    client, _connectionDocker = tclientLevelThree
    listCalls = []
    fnReal = reproducibilityRoutes.fbL3ReadinessOK

    def fbRecordThenGate(dictWorkflow, filesRepo):
        admission = mutationAdmission.fadmissionActiveForContainerId(
            S_CONTAINER_ID,
        )
        listCalls.append("" if admission is None else admission.sMode)
        fnReal(dictWorkflow, filesRepo)
        return True

    with patch.object(
        reproducibilityRoutes, "fbL3ReadinessOK", fbRecordThenGate,
    ), _teventHoldTheDurableWorkerOpen(
        reproducibilityRoutes, "_fnRunVerificationWorker",
    ):
        client.post(f"/api/workflow/{S_CONTAINER_ID}/level3/verify")
    assert listCalls, (
        "the route never reached the readiness gate, so this asserts "
        "nothing about the admission it runs under"
    )
    assert listCalls == [
        mutationAdmission.S_ADMISSION_MODE_LOCK_HELD
    ] * len(listCalls), (
        f"the L3 readiness gate ran outside the drain: {listCalls}"
    )


@pytest.mark.falsification
@pytest.mark.asyncio
async def testTheLaunchedVerificationIsVisibleAsLiveWork():
    """A live L3 verification makes its container read BUSY.

    The rebuild re-executes the WHOLE workflow inside the container and
    can run for minutes. Before mode (c) the task lived only in
    ``_DICT_VERIFY_TASKS``, which no authority outside its own module
    reads, so a transfer arriving mid-rerun committed and the old
    owner's rerun kept writing.

    Kills: reverting ``_fdictLaunchVerificationDurably`` to
    ``asyncio.create_task`` + ``_fnRegisterVerifyTask``.
    """
    with _tclientAsgiOverALevelThreeWorkflow() as (app, clientAsync):
        async with clientAsync:
            sLease = await _tConnectOverAsgi(clientAsync)
            clientAsync.headers["X-Vaibify-Lease"] = sLease
            sName = _fsContainerNameFor(app)
            with patch.object(
                reproducibilityRoutes, "fbL3ReadinessOK",
                lambda dictWorkflow, filesRepo: True,
            ), _teventHoldTheDurableWorkerOpen(
                reproducibilityRoutes, "_fnRunVerificationWorker",
            ):
                response = await clientAsync.post(
                    f"/api/workflow/{S_CONTAINER_ID}/level3/verify",
                )
                assert response.status_code == 200, (
                    "the verification was refused before it could be "
                    f"launched, so this asserts nothing: {response.text}"
                )
                assert commitCarrier.fbContainerHasLiveMutationWork(
                    app.state, sName,
                ), (
                    "a live L3 verification left its container reading "
                    "idle; a transfer would commit while the old "
                    "owner's rerun kept writing to the repository"
                )
            await asyncio.sleep(0.05)
