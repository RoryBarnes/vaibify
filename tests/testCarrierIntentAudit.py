"""Declared intent is held against observed execution, or it is metadata.

Migration plan phase 1c. ``tools/carrierIntentAudit.py`` is the check
that stops a carrier declaration becoming what ``bAgentSafe`` was for
months: a label nothing verified. Every observation compared here is
produced by driving a REAL route through the REAL
``routeScope.ContainerAwareRoute`` over real HTTP, and the admission
mode each observation records is read back out of the REAL
``vaibify.config.mutationAdmission`` contextvar rather than declared by
this file. Two of them run through the real commit carrier.

WHY ONE CASE SIMULATES A REGRESSION RATHER THAN REACHING IT
------------------------------------------------------------

``testADeclaredModeObservedOnTheAmbientAdmissionIsAViolation`` needs a
route that DECLARES ``mode-b-lock-held`` and is nonetheless served on
the ambient ``request`` admission. That state is unreachable today by
construction -- ``routeScope._fbServeOnAmbientAdmission`` gives a
declared route the enforced branch whatever else is true of it -- which
is precisely why the audit must reject it: the state is what a
regression in that one function produces, and
``testDeclaringMintsNoAdmission`` is registered against exactly that
mutant. So the branch decision is the only thing substituted here; the
route, the request, the ambient mint and the observation are all real,
and the audit is asked whether it notices the record disagreeing with
what ran.

The worker handed to the lock-held carrier is SYNCHRONOUS. The carrier
runs workers in a thread, so an ``async def`` would be called, hand back
a coroutine nobody awaits, and this file would pass having executed
nothing -- a mistake this suite has shipped once.
"""

import json
import pathlib

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from tests import attributionShapes
from tests.testCarrierModeDeclaration import (
    DICT_OWNER_HEADERS,
    SET_SEEDED_ROUTES_AWAITING_CARRIER_MODE,
    S_CONTAINER_ID,
    S_CONTAINER_NAME,
    fappBuildOwnedApplication,
)
from tools import carrierIntentAudit, mutationAttribution
from vaibify.config import operationJournal
from vaibify.gui import commitCarrier, routeScope
from vaibify.gui.appFactory import fappCreateHubApplication


PATH_THIS_MODULE = pathlib.Path(__file__).resolve()
S_THIS_MODULE_RELATIVE = "testCarrierIntentAudit.py"


@pytest.fixture(autouse=True)
def fnKeepTheOperationJournalOutOfTheHomeDirectory(monkeypatch, tmp_path):
    """The real lock-held carrier journals; none of it may touch the user."""
    monkeypatch.setattr(
        operationJournal, "_S_JOURNAL_DIRECTORY", str(tmp_path / "journal"),
    )


@pytest.fixture
def hostPrimitive():
    """A recording primitive stand-in indexed over this module's own rows."""
    return attributionShapes.RecordingPrimitiveHost(
        mutationAttribution.fdictBuildAttributionIndex(
            [(PATH_THIS_MODULE, S_THIS_MODULE_RELATIVE)],
        ),
        S_CONTAINER_ID,
    )


# ---------------------------------------------------------------------
# Real handlers, each reaching the observing primitive its own way.
# ---------------------------------------------------------------------

def _fnBuildObservingHandler(hostPrimitive, tupleDeclarations, fnRunEffect):
    """Return a FRESH stamped handler that observes at its own effect.

    Fresh per route because the declaration is stamped onto the function
    OBJECT; the declaration the observation records is derived from the
    same tuple that stamped the endpoint, so the two can never disagree
    by transcription.
    """
    sDeclaration = routeScope.fsFormatCarrierDeclaration(
        tupleDeclarations,
    ) or mutationAttribution.S_DECLARATION_ABSENT

    async def fnHandler(sContainerId: str, request: Request):
        hostPrimitive.dictInvocationContext = {
            "sOperationKind": "file-write",
            "sExecutionLane": "http",
            "sCarrierInvocation": (
                mutationAttribution.fsDescribeCarrierInvocation()
            ),
            "sEntryPointDeclaration": sDeclaration,
        }
        await fnRunEffect(request, sContainerId, hostPrimitive)
        return {"ok": True}

    if tupleDeclarations:
        routeScope.fnDeclareCarrierMode(*tupleDeclarations)(fnHandler)
    return fnHandler


async def _fnReachThePrimitiveWithNoCarrier(
    request, sContainerId, hostPrimitive,
):
    """Touch the primitive directly, the way a forgotten carrier does."""
    del request
    hostPrimitive.fnWriteFile(
        sContainerId, "/workspace/noCarrier.json", b"{}",
    )


async def _fnReachThePrimitiveThroughTheLockHeldCarrier(
    request, sContainerId, hostPrimitive,
):
    """Commit through the REAL mode-(b) carrier with a sync worker."""
    dictLaneTuple = commitCarrier.fdictBuildLaneTupleFromRequest(
        request.app.state, sContainerId, request,
    )
    assert dictLaneTuple is not None, (
        "the request could not be bound to the container's owner record, "
        "so the carrier was never exercised"
    )
    return await commitCarrier.fdictRunLockHeldMutation(
        request.app.state, S_CONTAINER_NAME, sContainerId, dictLaneTuple,
        "file-write", "/workspace/lockHeld.json",
        lambda supervisor: hostPrimitive.fnWriteFile(
            sContainerId, "/workspace/lockHeld.json", b"{}",
        ),
    )


def _fnDriveOneObservingRoute(sPath, tupleDeclarations, fnRunEffect,
                              hostPrimitive):
    """POST to one freshly built observing route; assert it answered."""
    app = fappBuildOwnedApplication([(
        sPath,
        _fnBuildObservingHandler(
            hostPrimitive, tupleDeclarations, fnRunEffect,
        ),
    )])
    response = TestClient(app).post(
        sPath.replace("{sContainerId}", S_CONTAINER_ID),
        headers=DICT_OWNER_HEADERS,
    )
    assert response.status_code == 200, response.text
    assert len(hostPrimitive.listObservations) == 1, (
        "the route answered without ever reaching the primitive, so "
        "nothing was observed and the comparison would have nothing to "
        "compare"
    )


# ---------------------------------------------------------------------
# The two rules.
# ---------------------------------------------------------------------

@pytest.mark.falsification
def testATypedReadDeclarationThatMutatesIsAViolation(hostPrimitive):
    """``typed-read`` claims no mutation-capable primitive is reached.

    An observation is taken AT such a primitive, so one carrying this
    declaration falsifies it outright. No judgement about the command is
    involved and none is available: R4 settled that an undecodable
    command counts as mutating, so "it was only a read" is not an answer
    the audit can accept from anybody.

    Kills: accepting a typed-read observation instead of recording it as
    a violation in _ftJudgeOneObservation.
    """
    _fnDriveOneObservingRoute(
        "/api/steps/{sContainerId}/audit-typed-read",
        (routeScope.S_CARRIER_TYPED_READ,),
        _fnReachThePrimitiveWithNoCarrier, hostPrimitive,
    )
    dictCompared = carrierIntentAudit.fdictCompareIntentToExecution(
        hostPrimitive.listObservations,
    )
    assert dictCompared["listConfirmed"] == []
    assert len(dictCompared["listViolations"]) == 1, dictCompared
    assert dictCompared["listViolations"][0]["sReason"] == (
        carrierIntentAudit.S_VIOLATION_TYPED_READ_MUTATED
    )
    assert dictCompared["listViolations"][0]["sPrimitive"] == "fnWriteFile"


@pytest.mark.falsification
def testADeclaredModeObservedOnTheAmbientAdmissionIsAViolation(
    hostPrimitive, monkeypatch,
):
    """A declared mode running on the legacy mint is the record lying.

    The ambient ``request`` admission maps to NO carrier mode on
    purpose, so it can never satisfy a declaration. That mapping is the
    whole difference between a migrated route and an unmigrated one, and
    an audit that let ``request`` pass for ``lock-held`` would report the
    migration complete while nothing had moved.

    The branch decision is substituted to reach a state
    ``_fbServeOnAmbientAdmission`` currently prevents (see the module
    docstring); the admission observed is a real one, minted by the real
    route class for a real authorized request.

    Kills: treating an observation whose admission maps to no declared
    carrier mode as confirmed in _ftJudgeOneObservation.
    """
    monkeypatch.setattr(
        routeScope, "_fbServeOnAmbientAdmission", lambda route: True,
    )
    _fnDriveOneObservingRoute(
        "/api/steps/{sContainerId}/audit-mode-b-on-ambient",
        (routeScope.S_CARRIER_MODE_B_LOCK_HELD,),
        _fnReachThePrimitiveWithNoCarrier, hostPrimitive,
    )
    assert hostPrimitive.listObservations[0][
        "sObservedAdmissionMode"
    ] == "request", (
        "the substituted branch did not actually produce the ambient "
        "admission, so this asserts nothing about it"
    )
    dictCompared = carrierIntentAudit.fdictCompareIntentToExecution(
        hostPrimitive.listObservations,
    )
    assert dictCompared["listConfirmed"] == []
    assert [
        dictViolation["sReason"]
        for dictViolation in dictCompared["listViolations"]
    ] == [carrierIntentAudit.S_VIOLATION_MODE_UNDECLARED]


def testAModeObservedUnderItsOwnCarrierIsConfirmed(hostPrimitive):
    """The positive case, driven through the real mode-(b) carrier.

    Without it the two rules above would be satisfied by an audit that
    called everything a violation, which detects nothing and blocks
    every migration.
    """
    _fnDriveOneObservingRoute(
        "/api/steps/{sContainerId}/audit-mode-b-carried",
        (routeScope.S_CARRIER_MODE_B_LOCK_HELD,),
        _fnReachThePrimitiveThroughTheLockHeldCarrier, hostPrimitive,
    )
    assert hostPrimitive.listObservations[0][
        "sObservedAdmissionMode"
    ] == "lockHeldAsync"
    dictCompared = carrierIntentAudit.fdictCompareIntentToExecution(
        hostPrimitive.listObservations,
    )
    assert dictCompared["listViolations"] == []
    assert len(dictCompared["listConfirmed"]) == 1
    assert dictCompared["listConfirmed"][0]["sReason"] == ""


def testAnUndeclaredEntryPointIsUncomparedAndNotConfirmed(hostPrimitive):
    """The 130 awaiting routes are not silently graded as compliant.

    Every one of them runs on the ambient admission by design, so
    comparing them would report 130 violations, and confirming them
    would report a migration that has not happened. UNCOMPARED is the
    only honest third answer, and it must be a distinct one.
    """
    _fnDriveOneObservingRoute(
        "/api/pipeline/{sContainerId}/kill", (),
        _fnReachThePrimitiveWithNoCarrier, hostPrimitive,
    )
    dictCompared = carrierIntentAudit.fdictCompareIntentToExecution(
        hostPrimitive.listObservations,
    )
    assert dictCompared["listViolations"] == []
    assert dictCompared["listConfirmed"] == []
    assert [
        dictUncompared["sReason"]
        for dictUncompared in dictCompared["listUncompared"]
    ] == [carrierIntentAudit.S_UNCOMPARED_ENTRY_POINT_AWAITING]


def testAnAuthorityOutsideTheCarrierIsUncomparedWithItsOwnReason():
    """``lifecycle-transaction`` and ``separate-authority`` are not modes.

    Both name an authority the carrier does not provide, so an
    observation under one is neither confirmed nor a violation. It is
    given its own reason rather than the awaiting one, because the two
    mean different things to whoever reads the audit: one route has not
    declared, the other has declared something this check cannot grade.
    """
    for sDeclaration in (
        routeScope.S_CARRIER_LIFECYCLE_TRANSACTION,
        routeScope.S_CARRIER_SEPARATE_AUTHORITY,
    ):
        dictCompared = carrierIntentAudit.fdictCompareIntentToExecution([{
            "sEntryPointDeclaration": sDeclaration,
            "sObservedAdmissionMode": "request",
            "sPrimitive": "fnWriteFile", "sCarrierInvocation": "",
            "sInventoryRowKey": "",
        }])
        assert dictCompared["listViolations"] == []
        assert dictCompared["listUncompared"][0]["sReason"] == (
            carrierIntentAudit.S_UNCOMPARED_OUTSIDE_CARRIER_VOCABULARY
        )


# ---------------------------------------------------------------------
# What the comparison could not see is reported, not omitted.
# ---------------------------------------------------------------------

def testADeclaredRouteTheSuiteNeverDroveIsReportedRatherThanPassed():
    """Silence is not compliance, and the audit says which silence.

    No production observation point exists yet, so the artifact holds
    only what the suite drove. An empty violation list therefore means
    "nothing observed contradicted a declaration" -- which for a route
    nobody exercised is the same answer as for a route that does not
    exist. This is the hand-off that keeps the two apart.
    """
    listNeverObserved = (
        carrierIntentAudit.flistSelectDeclarationsNeverObserved(
            {("POST", "/api/steps/{sContainerId}/hypothetical"): (
                routeScope.S_CARRIER_MODE_C_DURABLE,
            )},
            [],
        )
    )
    assert len(listNeverObserved) == 1
    assert listNeverObserved[0]["sRoute"] == (
        "POST /api/steps/{sContainerId}/hypothetical"
    )
    assert "unchecked rather than confirmed" in listNeverObserved[0]["sReason"]

    listAfterObservation = (
        carrierIntentAudit.flistSelectDeclarationsNeverObserved(
            {("POST", "/api/steps/{sContainerId}/hypothetical"): (
                routeScope.S_CARRIER_MODE_C_DURABLE,
            )},
            [{"sEntryPointDeclaration": (
                routeScope.S_CARRIER_MODE_C_DURABLE
            )}],
        )
    )
    assert listAfterObservation == []


def testTheDeclarationIndexReadsTheLiveApplication():
    """The index is resolved from the app, and pays for every migration.

    Resolved from the application rather than from a maintained list, so
    a route that declares appears here without anyone remembering to
    write it down. It was asserted EMPTY while nothing had been
    migrated; that measurement is now expressed as the conservation law
    it always stood for, which holds at every point of the migration
    instead of only at its start.

    Phase 2's per-group gate is "the allow-list shrinks by exactly as
    many routes as declared", and this is where that is mechanical: the
    declared keys and the still-awaiting keys must partition the SEEDED
    population exactly. Removing a route from the allow-list without
    declaring it fails here (the sum falls short), and so does declaring
    one without removing it (the two records overlap). The seeded set is
    the independently-edited second copy of the ratchet, so this counts
    against a record that cannot be adjusted in the same edit as the
    source.
    """
    dictIndex = carrierIntentAudit.fdictBuildRouteDeclarationIndex(
        fappCreateHubApplication(),
    )
    listOutsideTheClosedSet = [
        (tKey, tupleDeclarations)
        for tKey, tupleDeclarations in dictIndex.items()
        if not set(tupleDeclarations) <= (
            routeScope._SET_VALID_CARRIER_DECLARATIONS
        )
    ]
    assert listOutsideTheClosedSet == [], (
        "the index reported declarations outside R2's closed set: "
        f"{listOutsideTheClosedSet}. The index must report what the "
        "application's endpoints actually carry, so a value the stamp "
        "would have refused means it is no longer reading them."
    )
    setDeclared = set(dictIndex)
    setAwaiting = set(routeScope.SET_ROUTES_AWAITING_CARRIER_MODE)
    setOverlap = setDeclared & setAwaiting
    assert setOverlap == set(), (
        f"routes both declare a carrier mode and are still recorded as "
        f"awaiting one: {sorted(setOverlap)}. A migration removes the "
        "route from SET_ROUTES_AWAITING_CARRIER_MODE in the change that "
        "declares it."
    )
    setUnaccounted = SET_SEEDED_ROUTES_AWAITING_CARRIER_MODE - (
        setDeclared | setAwaiting
    )
    assert setUnaccounted == set(), (
        f"routes left the allow-list without declaring a carrier mode: "
        f"{sorted(setUnaccounted)}. Shrinking the allow-list is what "
        "moves a route onto the enforced branch, so a route dropped "
        "without a declaration is served enforced while nothing records "
        "what it was migrated TO -- and the count phase 4 reads stops "
        "meaning what it says."
    )


def testTheAuditReadsTheObservationArtifactBackOffDisk(
    hostPrimitive, tmp_path,
):
    """The audit's input is the artifact on disk, not an in-memory list."""
    _fnDriveOneObservingRoute(
        "/api/steps/{sContainerId}/audit-artifact",
        (routeScope.S_CARRIER_TYPED_READ,),
        _fnReachThePrimitiveWithNoCarrier, hostPrimitive,
    )
    pathArtifact = tmp_path / "mutationObservations.json"
    mutationAttribution.fnWriteObservationArtifact(
        hostPrimitive.listObservations, pathArtifact,
    )
    dictArtifact = json.loads(pathArtifact.read_text(encoding="utf-8"))
    dictCompared = carrierIntentAudit.fdictCompareIntentToExecution(
        dictArtifact["listObservations"],
    )
    assert [
        dictViolation["sReason"]
        for dictViolation in dictCompared["listViolations"]
    ] == [carrierIntentAudit.S_VIOLATION_TYPED_READ_MUTATED]
