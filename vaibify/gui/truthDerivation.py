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

Only the Level 1 four-axis test-state computation lives here
(``fdictComputeTestAxes``). The PROOF Level 2 and 3 truths are
computed by the gate functions in ``vaibify.reproducibility.levelGates``;
the names once reserved here for them were never added.

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
    "fsResolveCategoryAxisFromState",
    "fsRunVerdictFromHashes",
    "fsResolveUnitTestFromExitCode",
]


T_TEST_CATEGORY_AXIS_KEYS = (
    ("integrity", "sIntegrity"),
    ("qualitative", "sQualitative"),
    ("quantitative", "sQuantitative"),
)


def fsRunVerdictFromHashes(dictExpected, dictOnDisk):
    """Return how one run's recorded outputs compare with the files now.

    ``match``, ``outputs-changed``, ``outputs-missing`` (missing
    outranks changed), or ``unrecorded`` when the run recorded no
    hashes and so cannot be tied to any data state. The fresh-clone
    bootstrap's words for the poll's drift verdict.
    """
    sStatus = _fsStatusFromHashes(dictExpected or {}, dictOnDisk or {})
    if sStatus == "passed-from-marker":
        return "match"
    if sStatus == "untested":
        return "unrecorded"
    return sStatus


def fdictComputeTestAxes(
    dictLatestRun, dictOnDiskHashes, listAvailableCategories,
    dictCategoryStates,
):
    """Return the four-axis test verification dict from a marker's states.

    ``dictCategoryStates`` is what ``testMarkerContract`` derived from
    the marker (this leaf cannot import it), ``dictLatestRun`` the
    marker's newest run, and ``dictOnDiskHashes`` the observation. A
    category the marker speaks for takes its state's value: a passed
    state is ``passed-from-marker`` (the result was restored, not just
    run), and a state that is untested because the run's outputs
    drifted or vanished says which.
    """
    if not dictLatestRun:
        return _fdictEmptyTestAxes()
    dictExpected = dictLatestRun.get("dictOutputHashes", {}) or {}
    listChanged = _flistChangedOutputs(dictExpected, dictOnDiskHashes)
    dictResult = _fdictBaseAxisFields(dictLatestRun, listChanged)
    _fnFillCategoryAxes(
        dictResult, dictCategoryStates or {}, listAvailableCategories,
    )
    _fnFillUnitTestAxis(dictResult, dictCategoryStates or {})
    return dictResult


def _fnFillCategoryAxes(
    dictResult, dictCategoryStates, listAvailableCategories,
):
    """Assign one axis per category the marker speaks for or the workflow has."""
    setSeen = set()
    for sCategory, dictState in dictCategoryStates.items():
        if not dictState.get("bHasMarkerInfo"):
            continue
        dictResult[_fsAxisKeyForCategory(sCategory)] = _fsAxisFromState(
            dictState)
        setSeen.add(sCategory)
    for sCategory in listAvailableCategories or []:
        if sCategory not in setSeen:
            dictResult.setdefault(
                _fsAxisKeyForCategory(sCategory), "untested")


def _fsAxisFromState(dictState):
    """Translate a category state into the verification axis vocabulary."""
    sState = dictState.get("sState")
    if sState == "passed":
        return "passed-from-marker"
    if sState == "failed":
        return "failed"
    sReason = dictState.get("sNotCurrentBecause", "")
    if sReason in ("outputs-changed", "outputs-missing"):
        return sReason
    return "untested"


def _fnFillUnitTestAxis(dictResult, dictCategoryStates):
    """Fold the aggregate ``sUnitTest`` from the axes the marker speaks for."""
    listAxes = [
        dictResult[_fsAxisKeyForCategory(sCategory)]
        for sCategory, dictState in dictCategoryStates.items()
        if dictState.get("bHasMarkerInfo")
    ]
    if not listAxes:
        dictResult["sUnitTest"] = "untested"
    elif "failed" in listAxes:
        dictResult["sUnitTest"] = "failed"
    elif listAxes and all(sAxis == listAxes[0] for sAxis in listAxes):
        dictResult["sUnitTest"] = listAxes[0]
    else:
        dictResult["sUnitTest"] = "untested"


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


def fsResolveCategoryAxisFromState(dictState):
    """Return the live axis value for one derived category state.

    The poll's vocabulary, where a result obtained in this dashboard's
    own reach reads ``passed`` (the bootstrap's ``passed-from-marker``
    says the result was restored from a committed record instead).
    ``testMarkerContract`` decides what a category's state IS, from the
    marker's per-test results; this is only the word for it.
    """
    sState = (dictState or {}).get("sState")
    if sState in ("passed", "failed"):
        return sState
    return "untested"


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


def _fsAxisKeyForCategory(sCategory):
    """Return the camelCase axis key for a lowercase category name."""
    return "s" + sCategory[:1].upper() + sCategory[1:]


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
