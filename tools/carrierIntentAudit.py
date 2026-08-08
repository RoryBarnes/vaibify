"""Hold a route's DECLARED carrier intent against what it was observed to do.

Migration plan phase 1c, rules R1/R2/R5. A declaration authorizes
nothing; it records what a container-scoped entry point claims to do so
that CI can compare the claim against the admission a carrier actually
minted at the effect. Without this comparison a declaration is exactly
the mistake ``bAgentSafe`` was for months -- metadata that reads as a
guarantee and is checked by nothing.

WHAT IS COMPARED
----------------

Two rules, kept independent on purpose. A shape guarded twice survives
every single-mutation attempt and proves only that two guards exist, so
each rule below is reachable by a defect the other cannot mask.

``typed-read``
    An entry point declaring ``typed-read`` claims it reaches no
    mutation-capable primitive. Every observation is taken AT a
    mutation-capable primitive, so an observation carrying that
    declaration falsifies it. No judgement about the command is involved
    and none is possible: R4 settled that an undecodable command counts
    as mutating. The declaration is exclusive
    (``routeScope._fnValidateCarrierDeclarations``), so this rule can
    never be absorbed by the carrier-mode rule below.

a declared carrier mode
    An entry point declaring ``mode-a-synchronous``,
    ``mode-b-lock-held`` or ``mode-c-durable`` must be observed under a
    carrier admission whose mode is one it declared.
    ``mutationAttribution.DICT_ADMISSION_MODE_TO_CARRIER_MODE`` maps the
    ambient ``request`` mint and ``ownerEstablishing`` to NOTHING on
    purpose, so a declared route still running on the legacy ambient
    admission -- the exact state R6's allow-list retires -- is a
    violation here rather than a pass.

WHAT THIS CANNOT SEE, STATED SO NOBODY READS IT AS MORE
-------------------------------------------------------

**An observation records what its entry point DECLARED, never WHICH
entry point it was.** The artifact's fields are fixed by R5 and carry no
route identity, so this comparison can prove a declaration false but
cannot name which of two routes sharing that declaration broke it.
Phase 2 migrates one route at a time with a suite run at each step,
which is what makes that sufficient; it bounds the diagnosis, not the
detection.

**No production observation point exists.** Nothing under ``vaibify/``
records an observation. The artifact is written by whatever drives an
observing primitive, which today is the suite alone, so a route the
suite never exercises contributes nothing and its silence is not a
pass. :func:`flistSelectDeclarationsNeverObserved` is what keeps that
visible instead of letting an empty violation list read as compliance.

**The attribution blind spot does not reach this comparison.** A
primitive bound into ``asyncio.to_thread`` loses its inventory ROW
(``tools/mutationAttribution.py``) -- twenty rows permanently -- but
contextvars propagate, so the carrier's MODE survives into the worker.
This comparison keys on the declaration and the mode and never needs
the row, so those rows are compared here like any other. It is the row
that is lost, not the verdict.
"""

__all__ = [
    "S_VIOLATION_TYPED_READ_MUTATED",
    "S_VIOLATION_MODE_UNDECLARED",
    "S_UNCOMPARED_ENTRY_POINT_AWAITING",
    "S_UNCOMPARED_OUTSIDE_CARRIER_VOCABULARY",
    "DICT_DECLARATION_TO_CARRIER_MODE",
    "fdictBuildRouteDeclarationIndex",
    "fdictCompareIntentToExecution",
    "flistSelectDeclarationsNeverObserved",
]

from fastapi.routing import APIRoute

from tools import mutationAttribution
from vaibify.gui import routeScope

S_VIOLATION_TYPED_READ_MUTATED = "typedReadReachedAMutationCapablePrimitive"
S_VIOLATION_MODE_UNDECLARED = "observedAdmissionIsNotADeclaredCarrierMode"

S_UNCOMPARED_ENTRY_POINT_AWAITING = "entryPointHasNotDeclared"
S_UNCOMPARED_OUTSIDE_CARRIER_VOCABULARY = "declarationIsNotACarrierMode"

# The declarations naming a carrier mode, and the reviewer vocabulary
# each corresponds to in an observation's admission mode. Keyed off
# routeScope's constants rather than restated, so a renamed declaration
# cannot leave a stale spelling here that silently matches nothing.
# ``lifecycle-transaction`` and ``separate-authority`` are absent
# deliberately: both name an authority OUTSIDE the carrier, so an
# observation under them is uncompared rather than confirmed.
DICT_DECLARATION_TO_CARRIER_MODE = {
    routeScope.S_CARRIER_MODE_A_SYNCHRONOUS: "synchronous",
    routeScope.S_CARRIER_MODE_B_LOCK_HELD: "lock-held",
    routeScope.S_CARRIER_MODE_C_DURABLE: "durable",
}


def fdictBuildRouteDeclarationIndex(app):
    """Return ``{(sMethod, sPath): tupleDeclarations}`` for a live app.

    Only the container-scoped HTTP routes appear: they are the declaring
    population R2 defines and the ones ``ContainerAwareRoute`` serves.
    """
    dictDeclarations = {}
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        dictScope = routeScope.fdictResolveRouteScope(
            route.methods, route.path, route.endpoint,
        )
        if dictScope is None:
            continue
        if dictScope["sScope"] not in (
            routeScope._SET_AUTHORIZED_CONTAINER_SCOPES
        ):
            continue
        tupleDeclarations = routeScope.ftResolveCarrierDeclaration(
            route.endpoint,
        )
        if not tupleDeclarations:
            continue
        for sMethod in sorted(route.methods):
            dictDeclarations[(sMethod, route.path)] = tupleDeclarations
    return dictDeclarations


def fdictCompareIntentToExecution(listObservations):
    """Return declared-versus-observed: violations, confirmations, gaps.

    Every observation lands in exactly one of three lists, so none can
    be dropped through a condition nobody wrote. An UNCOMPARED
    observation is not a pass and does not read like one: it carries the
    reason it could not be compared, in the same shape as a violation.
    """
    dictOutcome = {
        "listViolations": [], "listConfirmed": [], "listUncompared": [],
    }
    for dictObservation in listObservations:
        sBucket, dictJudged = _ftJudgeOneObservation(dictObservation)
        dictOutcome[sBucket].append(dictJudged)
    return dictOutcome


def _ftJudgeOneObservation(dictObservation):
    """Return ``(sBucketName, dictJudgement)`` for one observed effect."""
    tupleDeclared = routeScope.ftParseCarrierDeclaration(
        dictObservation["sEntryPointDeclaration"],
    )
    if not tupleDeclared:
        return ("listUncompared", _fdictRecordJudgement(
            dictObservation, S_UNCOMPARED_ENTRY_POINT_AWAITING,
        ))
    if routeScope.S_CARRIER_TYPED_READ in tupleDeclared:
        return ("listViolations", _fdictRecordJudgement(
            dictObservation, S_VIOLATION_TYPED_READ_MUTATED,
        ))
    setDeclaredModes = {
        DICT_DECLARATION_TO_CARRIER_MODE[sDeclaration]
        for sDeclaration in tupleDeclared
        if sDeclaration in DICT_DECLARATION_TO_CARRIER_MODE
    }
    if not setDeclaredModes:
        return ("listUncompared", _fdictRecordJudgement(
            dictObservation, S_UNCOMPARED_OUTSIDE_CARRIER_VOCABULARY,
        ))
    sObservedCarrierMode = (
        mutationAttribution.DICT_ADMISSION_MODE_TO_CARRIER_MODE.get(
            dictObservation["sObservedAdmissionMode"], "",
        )
    )
    if sObservedCarrierMode in setDeclaredModes:
        return ("listConfirmed", _fdictRecordJudgement(dictObservation, ""))
    return ("listViolations", _fdictRecordJudgement(
        dictObservation, S_VIOLATION_MODE_UNDECLARED,
    ))


def _fdictRecordJudgement(dictObservation, sReason):
    """Return one judgement, carrying the evidence it was reached on."""
    return {
        "sReason": sReason,
        "sEntryPointDeclaration": dictObservation["sEntryPointDeclaration"],
        "sObservedAdmissionMode": dictObservation["sObservedAdmissionMode"],
        "sPrimitive": dictObservation["sPrimitive"],
        "sCarrierInvocation": dictObservation["sCarrierInvocation"],
        "sInventoryRowKey": dictObservation["sInventoryRowKey"],
    }


def flistSelectDeclarationsNeverObserved(
    dictRouteDeclarations, listObservations,
):
    """Return the declared routes no observation ever carried.

    An empty violation list means "nothing observed contradicted a
    declaration", which for a route the suite never drove is the same
    answer as for one that does not exist. This is the hand-off that
    keeps the difference visible, and it is a routing decision rather
    than a verdict -- the same standing
    ``mutationAttribution.flistSelectRowsForManualTracing`` gives a row
    nobody reached.
    """
    setObservedDeclarations = {
        dictObservation["sEntryPointDeclaration"]
        for dictObservation in listObservations
    }
    listNeverObserved = []
    for tRoute, tupleDeclarations in sorted(dictRouteDeclarations.items()):
        sDeclaration = routeScope.fsFormatCarrierDeclaration(
            tupleDeclarations,
        )
        if sDeclaration in setObservedDeclarations:
            continue
        listNeverObserved.append({
            "sRoute": f"{tRoute[0]} {tRoute[1]}",
            "sEntryPointDeclaration": sDeclaration,
            "sReason": (
                "no observation carried this declaration; the suite "
                "never drove a route declaring it, so its intent is "
                "unchecked rather than confirmed"
            ),
        })
    return listNeverObserved


def fnReportDeclarationCoverage():
    """Print which container-scoped routes declare a carrier mode.

    The counts used to live in AGENTS.md and went stale four times in a
    single migration session, because they change on every batch while
    the prose does not. A number that must be re-typed to stay true is a
    deterministic fact in a semantic document; it belongs in a command.
    """
    from vaibify.gui import appFactory, routeScope
    dictDeclared = fdictBuildRouteDeclarationIndex(
        appFactory.fappCreateHubApplication(),
    )
    setAwaiting = set(routeScope.SET_ROUTES_AWAITING_CARRIER_MODE)
    print(f"declared {len(dictDeclared)}  awaiting {len(setAwaiting)}  "
          f"total {len(dictDeclared) + len(setAwaiting)}")
    for tRoute, tModes in sorted(dictDeclared.items()):
        print(f"  {'+'.join(tModes):40}  {tRoute[0]:7} {tRoute[1]}")
    for tRoute in sorted(setAwaiting):
        print(f"  {'(awaiting)':40}  {tRoute[0]:7} {tRoute[1]}")


if __name__ == "__main__":
    fnReportDeclarationCoverage()
