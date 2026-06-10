"""Canonical truth-derivation helpers — pure observation-over-declaration.

Every "is this truth currently true?" question in the dashboard
resolves through a pure function in this module. The shape of each
function is:

    (declared baseline) + (current observation) + (predicate)
        -> status dict

No function in this module reads or mutates global state, performs
I/O, or imports from other ``vaibify.gui`` modules. It is a deliberate
leaf module — same pattern as ``stepPredicates.py`` and
``pipelineUtils.py`` — so anywhere in the package can call it without
introducing a cycle.

The canonical-truth pattern is what AICS Levels 2 and 3 will follow
for every new truth they monitor. Today only the Level 1 four-axis
test-state computation is implemented (``fdictComputeTestAxes``);
the reserved namespace below names the functions Levels 2 and 3 will
add without scattering new writer sites across the codebase:

- ``# fdictComputeGithubSyncStatus``  (reserved for L2)
- ``# fdictComputeZenodoSyncStatus``  (reserved for L2)
- ``# fdictComputeManifestStatus``    (reserved for L3)
- ``# fdictComputeReadinessStatus``   (reserved for L3)
- ``# fdictComputeAttestationStatus`` (reserved for L3)

Any module that today writes ``"passed"`` / ``"passed-from-marker"``
/ ``"failed"`` directly to a truth-claim axis must instead either
call into this module (truth-claim writer) or hand off the value
returned from a function here (derivation writer). State-machine
writes — ``"untested"`` / ``"unnecessary"`` — make no truth claim and
remain allowed at their original sites. The architectural invariant
``testNoDirectTruthClaimWrites`` enforces this split mechanically.
"""


__all__ = [
    "T_TEST_CATEGORY_AXIS_KEYS",
    "fdictComputeTestAxes",
    "fsAggregateUnitTestFromAxes",
    "fsResolveCategoryAxisFromCounts",
    "fsResolveUnitTestFromExitCode",
]


T_TEST_CATEGORY_AXIS_KEYS = (
    ("integrity", "sIntegrity"),
    ("qualitative", "sQualitative"),
    ("quantitative", "sQuantitative"),
)


def fdictComputeTestAxes(
    dictMarker, dictOnDiskHashes, listAvailableCategories,
):
    """Return the four-axis test verification dict from marker + observation."""
    if not dictMarker:
        return _fdictEmptyTestAxes()
    dictExpected = dictMarker.get("dictOutputHashes", {}) or {}
    sHashStatus = _fsStatusFromHashes(dictExpected, dictOnDiskHashes)
    listChanged = _flistChangedOutputs(dictExpected, dictOnDiskHashes)
    iExitStatus = dictMarker.get("iExitStatus", 0)
    dictCounts = dictMarker.get("dictCategories", {}) or {}
    dictResult = _fdictBaseAxisFields(dictMarker, listChanged)
    _fnFillAllAxes(
        dictResult, sHashStatus, iExitStatus, dictCounts,
        listAvailableCategories,
    )
    return dictResult


def _fnFillAllAxes(
    dictResult, sHashStatus, iExitStatus, dictCounts,
    listAvailableCategories,
):
    """Fill per-category axes plus the aggregate sUnitTest axis."""
    _fnFillCategoryAxes(
        dictResult, sHashStatus, iExitStatus, dictCounts,
        listAvailableCategories,
    )
    _fnFillUnitTestAxis(
        dictResult, sHashStatus, iExitStatus, dictCounts,
    )


_T_GREEN_AXIS_VALUES = ("passed", "passed-from-marker", "unnecessary")


def fsAggregateUnitTestFromAxes(listAxisValues):
    """Fold per-category axes into the aggregate ``sUnitTest`` value.

    Empty input collapses to ``"unnecessary"`` because there are no
    categories to demand a result; any ``"failed"`` short-circuits;
    all-green axes fold green, where green is any of ``"passed"``,
    ``"passed-from-marker"``, or ``"unnecessary"`` (the same set
    ``stepPredicates`` treats as green). When every demanded result
    came from a fresh run the aggregate is ``"passed"``; when any
    result was restored from a committed marker the aggregate is
    ``"passed-from-marker"`` so the badge stays honest about its
    provenance. Any non-green, non-failed axis folds to
    ``"untested"`` — "no current result for every category".
    """
    if not listAxisValues:
        return "unnecessary"
    if "failed" in listAxisValues:
        return "failed"
    if any(sState not in _T_GREEN_AXIS_VALUES for sState in listAxisValues):
        return "untested"
    if "passed-from-marker" in listAxisValues:
        return "passed-from-marker"
    if "passed" in listAxisValues:
        return "passed"
    return "unnecessary"


def fsResolveUnitTestFromExitCode(iExitCode):
    """Return ``"passed"`` for a clean exit, ``"failed"`` otherwise."""
    return "passed" if int(iExitCode or 0) == 0 else "failed"


def fsResolveCategoryAxisFromCounts(dictCounts):
    """Return the truth-claim axis value for a fresh per-category result.

    Used when a fresh test marker arrives for a single category and
    the caller already established that the marker is current (no
    hash drift, no staleness). Returns ``"failed"`` for any failure
    in the counts, ``"passed"`` when at least one test passed and
    none failed, or ``""`` when the counts hold neither — leaving
    the existing axis untouched is the caller's job.
    """
    iFailed = int(dictCounts.get("iFailed", 0) or 0)
    iPassed = int(dictCounts.get("iPassed", 0) or 0)
    if iFailed > 0:
        return "failed"
    if iPassed > 0:
        return "passed"
    return ""


def _fdictEmptyTestAxes():
    """Return the all-``untested`` axes dict for a missing marker."""
    return {
        "sUser": "",
        "sLastTestRun": "",
        "listModifiedFiles": [],
        "sUnitTest": "untested",
        "sIntegrity": "untested",
        "sQualitative": "untested",
        "sQuantitative": "untested",
    }


def _fdictBaseAxisFields(dictMarker, listChanged):
    """Seed the result dict with non-axis fields the caller relies on."""
    return {
        "sUser": "",
        "sLastTestRun": dictMarker.get("sRunAtUtc", ""),
        "listModifiedFiles": listChanged,
    }


def _fnFillCategoryAxes(
    dictResult, sHashStatus, iExitStatus, dictCategoryCounts,
    listAvailableCategories,
):
    """Compute and assign one axis per category present in marker or workflow."""
    setSeen = set()
    for sCategory, dictCounts in dictCategoryCounts.items():
        sAxisKey = _fsAxisKeyForCategory(sCategory)
        dictResult[sAxisKey] = _fsCategoryStatus(
            sHashStatus, iExitStatus, dictCounts or {},
        )
        setSeen.add(sCategory)
    for sCategory in listAvailableCategories or []:
        if sCategory in setSeen:
            continue
        sAxisKey = _fsAxisKeyForCategory(sCategory)
        dictResult.setdefault(sAxisKey, "untested")


def _fnFillUnitTestAxis(
    dictResult, sHashStatus, iExitStatus, dictCategoryCounts,
):
    """Compute the aggregate ``sUnitTest`` axis when not already supplied."""
    if "sUnitTest" in dictResult:
        return
    dictResult["sUnitTest"] = _fsCategoryStatus(
        sHashStatus, iExitStatus, dictCategoryCounts,
    )


def _fsAxisKeyForCategory(sCategory):
    """Return the camelCase axis key for a lowercase category name."""
    return "s" + sCategory[:1].upper() + sCategory[1:]


def _fsCategoryStatus(sHashStatus, iExitStatus, dictCounts):
    """Fold hash status, marker exit, and per-category counts into one axis.

    Hash mismatches (``outputs-missing`` / ``outputs-changed``) win
    because they signal that the marker no longer describes the
    on-disk state. Otherwise a non-zero ``iExitStatus`` or any
    ``iFailed`` in the category demotes the badge to ``"failed"``
    rather than letting a failed run masquerade as
    ``"passed-from-marker"``.
    """
    if sHashStatus in ("outputs-missing", "outputs-changed"):
        return sHashStatus
    if sHashStatus == "untested":
        return "untested"
    if iExitStatus != 0:
        return "failed"
    if int(dictCounts.get("iFailed", 0) or 0) > 0:
        return "failed"
    return "passed-from-marker"


def _fsStatusFromHashes(dictExpected, dictOnDisk):
    """Classify the hash comparison: passed-from-marker / changed / missing."""
    if not dictExpected:
        return "untested"
    bAnyMissing = False
    bAnyChanged = False
    for sPath, sExpectedSha in dictExpected.items():
        sActual = dictOnDisk.get(sPath, "")
        if not sActual:
            bAnyMissing = True
            continue
        if sActual != sExpectedSha:
            bAnyChanged = True
    if bAnyMissing:
        return "outputs-missing"
    if bAnyChanged:
        return "outputs-changed"
    return "passed-from-marker"


def _flistChangedOutputs(dictExpected, dictOnDisk):
    """Return repo-relative paths whose on-disk hash differs from the marker."""
    listResult = []
    for sPath, sExpectedSha in dictExpected.items():
        sActual = dictOnDisk.get(sPath, "")
        if sActual and sActual != sExpectedSha:
            listResult.append(sPath)
    return sorted(listResult)
