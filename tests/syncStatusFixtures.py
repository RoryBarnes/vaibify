"""One builder for the cached-verify fixture the gate tests need.

Nineteen test files hand-built this dict. When the verify began
recording which SCOPE it ran under, five of them had to be edited just
to keep meaning what they already meant -- and three falsification
tests were silently DEFANGED, because a scope-less fixture makes
``_fbCachedSyncStatusFullMatch`` refuse at the scope check, before the
SHA and freshness guards those tests exist to defend. Their assertions
still passed (``False`` is what they expect), the guards became
unreachable, and only CI's mutation run could tell -- the static
registry check verifies the mutation TEXT still appears in the source,
never that a test would notice it.

So the point of this module is not tidiness. A fixture that is BUILT
gains a new required field in one place; a fixture that is TYPED gains
it in nineteen, or in five and silently not the others.

Two properties are deliberate:

**Everything is overridable and nothing is hidden.** A gate test earns
its keep by making exactly one guard decisive, so a builder that
quietly normalised the other fields would destroy the thing under
test. Callers pass what matters to them and the defaults fill a
consistent, boringly-valid cache around it.

**The counts are DERIVED, not accepted.** A real writer always sets
``iMatching = iTotalFiles - len(listDiverged)``, and a fixture that
contradicts that models a corrupt file rather than a verified one.
Tests that want the corrupt shape ask for it explicitly through
``iMatchingOverride``, which reads as the deliberate act it is.
"""

from datetime import datetime, timedelta, timezone

from vaibify.reproducibility.publicationScope import (
    I_PUBLICATION_SCOPE_VERSION,
)


__all__ = [
    "TUPLE_DEFAULT_COMPARED_PATHS",
    "fdictBuildCachedVerify",
]


# Ordinary Level 2 material: a step output, the script that made it,
# and a second step's output. Nothing here is envelope, so the Level 2
# selector keeps all three and a test that wants an envelope path in
# the compared set must say so.
TUPLE_DEFAULT_COMPARED_PATHS = (
    "step01/data.csv", "step01/run.py", "step02/out.json",
)


def fsRecentVerifyIso(fHoursAgo=1.0):
    """An ISO timestamp comfortably inside the freshness window.

    Computed at call time, because a literal date in a fixture is a
    time bomb: the 2026-08-26 literals passed all day and started
    failing at midnight, when the 24-hour freshness window slid past
    them -- six tests red with nothing wrong in the code.
    """
    return (
        datetime.now(timezone.utc) - timedelta(hours=fHoursAgo)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")


def fdictBuildCachedVerify(
    sService="github",
    listComparedPaths=None,
    listDivergedPaths=(),
    sLastVerified=None,
    bScopeCurrent=True,
    iMatchingOverride=None,
    **dictIdentityFields,
):
    """Return one service's entry as a real verify writes it.

    ``bScopeCurrent=False`` models a cache written under an EARLIER
    definition of the published set -- a different thing from a stale
    one or a diverged one, and the case the gate used to wave through.

    ``iMatchingOverride`` breaks the count relation on purpose, for the
    tests that assert a self-contradictory cache is refused. Leave it
    alone and the counts agree with ``listDivergedPaths`` the way a
    writer's always do.

    ``dictIdentityFields`` carries the per-service identity a caller
    cares about (``sCommittedShaVerified`` for GitHub, ``sZenodoDoi``
    and ``sEndpointVerified`` for Zenodo). They are passed through
    untouched: which identity a gate demands is exactly what several of
    these tests exist to pin.
    """
    listCompared = (
        list(TUPLE_DEFAULT_COMPARED_PATHS)
        if listComparedPaths is None else list(listComparedPaths)
    )
    listDiverged = [
        {"sPath": sPath, "sExpected": "aaa", "sActual": "bbb"}
        for sPath in listDivergedPaths
    ]
    dictStatus = {
        "sService": sService,
        "iTotalFiles": len(listCompared),
        "iMatching": (
            len(listCompared) - len(listDiverged)
            if iMatchingOverride is None else iMatchingOverride
        ),
        "listDiverged": listDiverged,
        "listComparedPaths": listCompared,
    }
    # Omitted entirely rather than set to None: a cache with no
    # timestamp is what a never-completed verify leaves behind, and the
    # freshness guard reads its ABSENCE.
    if sLastVerified is not None:
        dictStatus["sLastVerified"] = sLastVerified
    if bScopeCurrent:
        dictStatus["iScopeVersion"] = I_PUBLICATION_SCOPE_VERSION
    dictStatus.update(dictIdentityFields)
    return dictStatus
