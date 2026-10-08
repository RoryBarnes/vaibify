"""The test-marker contract: its shape, its merge, and the states it implies.

A test marker (``.vaibify/test_markers/<workflow>/<step>.json``) is the
record a pytest session leaves for the dashboard. It used to hold ONE
session's result, overwritten by the next, and the dashboard runs one
session per test category -- so the marker held only the last category
run, and the other two read untested.

A marker now records every test's own last outcome, not a session's
summary::

    {"sDirectory": "StepDir", "sLabel": "A01",
     "dictRuns": {"<sRunId>": {"fTimestamp", "sRunAtUtc", "iExitStatus",
                               "dictOutputHashes", "dictInputHashes"}},
     "dictTestFiles": {"<file>": {
         "listNodeIds": ["tests/<file>::<test>", ...] or null,
         "dictOutcomes": {"<nodeId>": {"sOutcome", "sRunId"}},
         "dictCollectionError": null or {"sRunId", "fTimestamp", "sMessage"}}},
     "dictLegacyCategories": {"<category>": {"iPassed", "iFailed", "sRunId"}},
     "dictUnattributedFailure": null or {"sRunId", "fTimestamp",
                                         "iExitStatus", "sMessage"}}

A run records exactly the tests it ran. Tests it did not run keep their
last outcome; a narrowed passing run can never hide another test's
failure, and passes from runs at different data states never combine,
because each outcome is judged against ITS OWN run's recorded hashes.

This module is a deliberate leaf (no intra-package imports), like
``truthDerivation``. Its WRITER half -- the shape, the normalizer and the
merge -- is transcribed verbatim into the container's conftest, which
cannot import from the host, so the container and the dashboard share
ONE implementation of what a marker is. The READER half (the category
states) runs only on the host. Everything the conftest receives must
therefore stay valid on every Python the container may carry, use only
the standard library, and name only what is transcribed with it.
"""

__all__ = [
    "LIST_TRANSCRIBED_WRITER_FUNCTIONS",
    "S_LEGACY_RUN_ID",
    "T_CATEGORY_FILE_PREFIXES",
    "T_NON_FAILING_OUTCOMES",
    "T_NOT_CURRENT_VERDICTS",
    "fdictCategoryStatesFromMarker",
    "fdictMergeSessionIntoMarker",
    "fdictNormalizeMarker",
    "fsCategoryOfFileName",
    "ftLatestRun",
]

# The prefix mapping the conftest has always used on node ids, applied
# to file names: the first prefix a name contains wins.
T_CATEGORY_FILE_PREFIXES = (
    ("test_integrity", "integrity"),
    ("test_qualitative", "qualitative"),
    ("test_quantitative", "quantitative"),
)

# What pytest itself treats as not failing: a non-strict xpass is a
# pass, and a skipped or xfailed test never turns the exit status red.
T_NON_FAILING_OUTCOMES = ("passed", "skipped", "xfailed", "xpassed")

# A run whose recorded data no longer describes the files (or never
# recorded any) speaks for nothing. ``drift`` is the poll's verdict;
# the other three are the fresh-clone bootstrap's finer words for the
# same fact, kept apart because its axes name which one it was.
T_NOT_CURRENT_VERDICTS = (
    "drift", "outputs-changed", "outputs-missing", "unrecorded",
)

# The run id the normalizer gives a marker written before runs existed.
S_LEGACY_RUN_ID = "legacy"

def fsCategoryOfFileName(sFileName):
    """Return the category a test file's name declares, or ``"other"``."""
    for sPrefix, sCategory in T_CATEGORY_FILE_PREFIXES:
        if sPrefix in sFileName:
            return sCategory
    return "other"


def _fdictEmptyMarker():
    """Return a marker with no runs, no files and no failures."""
    return {
        "sDirectory": "", "sLabel": "", "dictRuns": {},
        "dictTestFiles": {}, "dictLegacyCategories": {},
        "dictUnattributedFailure": None,
    }


def _fdictRunRecord(dictSource):
    """Return the run fields a marker keeps, read from any dict holding them."""
    return {
        "fTimestamp": dictSource.get("fTimestamp", 0),
        "sRunAtUtc": dictSource.get("sRunAtUtc", ""),
        "iExitStatus": dictSource.get("iExitStatus", 0),
        "dictOutputHashes": dict(dictSource.get("dictOutputHashes") or {}),
        "dictInputHashes": dict(dictSource.get("dictInputHashes") or {}),
    }


def _fdictLegacyAsNewShape(dictMarker):
    """Read a marker from before runs existed as one run plus counts.

    The run id is ``legacy``. The per-category counts survive as a
    category-level outcome with no test list: it speaks for a category
    until a new run collects that category's files, and for nothing
    finer. Published archives carry markers in this shape that cannot be
    rewritten, so it must keep reading identically.
    """
    dictLegacyCategories = {}
    for sCategory, dictCounts in (dictMarker.get("dictCategories") or {}).items():
        if isinstance(dictCounts, dict):
            dictLegacyCategories[sCategory] = {
                "iPassed": dictCounts.get("iPassed", 0),
                "iFailed": dictCounts.get("iFailed", 0),
                "sRunId": "legacy",
            }
    dictResult = _fdictEmptyMarker()
    dictResult["sDirectory"] = dictMarker.get("sDirectory", "")
    dictResult["sLabel"] = dictMarker.get("sLabel", "")
    dictResult["dictRuns"] = {"legacy": _fdictRunRecord(dictMarker)}
    dictResult["dictLegacyCategories"] = dictLegacyCategories
    return dictResult


def fdictNormalizeMarker(dictMarker):
    """Return a marker in the current shape, or None when it is not a dict.

    Idempotent: a marker already in the current shape comes back with
    only its missing sections filled in. The one function every reader
    and the writer pass a marker through, so there is one answer to
    "what does this file say".
    """
    if not isinstance(dictMarker, dict):
        return None
    if not isinstance(dictMarker.get("dictRuns"), dict):
        return _fdictLegacyAsNewShape(dictMarker)
    dictResult = _fdictEmptyMarker()
    dictResult.update(dictMarker)
    for sKey in ("dictTestFiles", "dictLegacyCategories"):
        if not isinstance(dictResult.get(sKey), dict):
            dictResult[sKey] = {}
    return dictResult


def _fnMergeFileResult(dictMarker, sRunId, sFileName, dictFileResult):
    """Fold one test file's result from this session into the marker.

    A collection error is recorded and leaves outcomes alone. A file that
    collected clears any earlier error, and records an outcome for each
    test the session ran. Only a WHOLE-FILE collection owns the test list
    (a ``file.py::test_a`` argument collects one test of many, and a
    ``-k`` run still lists every test it deselected); tests that dropped
    out of that list stop counting.
    """
    dictFile = dictMarker["dictTestFiles"].setdefault(sFileName, {
        "listNodeIds": None, "dictOutcomes": {}, "dictCollectionError": None,
    })
    if dictFileResult.get("dictCollectionError"):
        dictFile["dictCollectionError"] = dictFileResult["dictCollectionError"]
        return
    dictFile["dictCollectionError"] = None
    for sNodeId, sOutcome in dictFileResult.get("dictOutcomes", {}).items():
        dictFile["dictOutcomes"][sNodeId] = {
            "sOutcome": sOutcome, "sRunId": sRunId,
        }
    if dictFileResult.get("bWholeFile"):
        listNodeIds = list(dictFileResult.get("listNodeIds") or [])
        dictFile["listNodeIds"] = listNodeIds
        dictFile["dictOutcomes"] = {
            sNodeId: dictOutcome
            for sNodeId, dictOutcome in dictFile["dictOutcomes"].items()
            if sNodeId in listNodeIds
        }


def _fnClearCollectedLegacyCategories(dictMarker, dictFileResults):
    """Drop the legacy outcome of every category this session collected whole."""
    for sFileName, dictFileResult in dictFileResults.items():
        if dictFileResult.get("bWholeFile") and not dictFileResult.get(
            "dictCollectionError"
        ):
            dictMarker["dictLegacyCategories"].pop(
                fsCategoryOfFileName(sFileName), None)


def _fnPruneUnreferencedRuns(dictMarker):
    """Drop every run no outcome and no legacy category still points at."""
    setReferenced = {
        dictCounts.get("sRunId")
        for dictCounts in dictMarker["dictLegacyCategories"].values()
    }
    for dictFile in dictMarker["dictTestFiles"].values():
        for dictOutcome in dictFile["dictOutcomes"].values():
            setReferenced.add(dictOutcome["sRunId"])
    for sRunId in list(dictMarker["dictRuns"]):
        if sRunId not in setReferenced:
            del dictMarker["dictRuns"][sRunId]


def fdictMergeSessionIntoMarker(dictExistingMarker, dictSession):
    """Return the marker that results from adding one session to the old one.

    ``dictSession`` carries the run (``sRunId``, ``fTimestamp``,
    ``sRunAtUtc``, ``iExitStatus``, ``dictOutputHashes``,
    ``dictInputHashes``), the step's ``sDirectory`` and ``sLabel``,
    ``listPresentFiles`` (the test files now in ``tests/``) and
    ``dictFileResults`` (per file: ``bWholeFile``, ``listNodeIds``,
    ``dictOutcomes``, ``dictCollectionError``). Files no longer present
    are dropped, runs nothing refers to are pruned, and a session that
    exited 0 clears any unattributed failure while one that carries a
    failure sets it.
    """
    dictMarker = fdictNormalizeMarker(dictExistingMarker) or _fdictEmptyMarker()
    dictMarker["sDirectory"] = dictSession.get("sDirectory", "")
    dictMarker["sLabel"] = dictSession.get("sLabel", "")
    dictMarker["dictRuns"][dictSession["sRunId"]] = _fdictRunRecord(dictSession)
    dictFileResults = dictSession.get("dictFileResults", {})
    for sFileName, dictFileResult in dictFileResults.items():
        _fnMergeFileResult(
            dictMarker, dictSession["sRunId"], sFileName, dictFileResult)
    for sFileName in list(dictMarker["dictTestFiles"]):
        if sFileName not in dictSession.get("listPresentFiles", []):
            del dictMarker["dictTestFiles"][sFileName]
    _fnClearCollectedLegacyCategories(dictMarker, dictFileResults)
    if dictSession.get("dictUnattributedFailure"):
        dictMarker["dictUnattributedFailure"] = dictSession[
            "dictUnattributedFailure"]
    elif dictSession.get("iExitStatus", 0) == 0:
        dictMarker["dictUnattributedFailure"] = None
    _fnPruneUnreferencedRuns(dictMarker)
    return dictMarker


# The functions the container's conftest carries a verbatim copy of, in
# the order it needs them defined (a helper before its caller).
LIST_TRANSCRIBED_WRITER_FUNCTIONS = (
    fsCategoryOfFileName,
    _fdictEmptyMarker,
    _fdictRunRecord,
    _fdictLegacyAsNewShape,
    fdictNormalizeMarker,
    _fnMergeFileResult,
    _fnClearCollectedLegacyCategories,
    _fnPruneUnreferencedRuns,
    fdictMergeSessionIntoMarker,
)


def ftLatestRun(dictMarker):
    """Return ``(sRunId, dictRun)`` of the newest run, or ``("", {})``."""
    dictRuns = (fdictNormalizeMarker(dictMarker) or {}).get("dictRuns", {})
    if not dictRuns:
        return ("", {})
    sRunId = max(
        dictRuns, key=lambda sKey: dictRuns[sKey].get("fTimestamp", 0))
    return (sRunId, dictRuns[sRunId])


def _fbRunIsCurrent(sRunId, dictContext):
    """Return True iff nothing known says the run's results no longer apply.

    A run is not current when it carries no UTC stamp (the pre-2026-04
    format cannot be tied to any data state), when its recorded data
    DRIFTED from the files now (the poll's own hash verdict for THIS
    run), when the step's outputs are newer than the run, or when an
    unattributed failure is newer than the run. An UNKNOWN verdict does
    not make a run stale: the dashboard says so on its own row.
    """
    dictRun = dictContext["dictMarker"]["dictRuns"].get(sRunId)
    if not dictRun or not dictRun.get("sRunAtUtc"):
        return False
    if dictContext["dictRunVerdicts"].get(sRunId) in T_NOT_CURRENT_VERDICTS:
        return False
    fRunTime = dictRun.get("fTimestamp", 0)
    if dictContext["fMaxOutputMtime"] > fRunTime:
        return False
    dictFailure = dictContext["dictMarker"]["dictUnattributedFailure"]
    return not (dictFailure and dictFailure.get("fTimestamp", 0) >= fRunTime)


def _fbOutcomeIsCurrent(dictOutcome, sFileName, dictContext):
    """Return True iff the outcome's run is current and newer than its file."""
    sRunId = dictOutcome.get("sRunId", "")
    if not _fbRunIsCurrent(sRunId, dictContext):
        return False
    fRunTime = dictContext["dictMarker"]["dictRuns"][sRunId].get(
        "fTimestamp", 0)
    return dictContext["dictFileMtimes"].get(sFileName, 0) <= fRunTime


def _fdictStateOfFile(sFileName, dictContext):
    """Return one test file's ``{sState, iTotal, iCurrent, iFailed}``."""
    dictFile = dictContext["dictMarker"]["dictTestFiles"].get(sFileName)
    dictResult = {"sState": "untested", "iTotal": 0, "iCurrent": 0,
                  "iFailed": 0, "bKnown": dictFile is not None, "sReason": ""}
    if dictFile is None:
        return dictResult
    if dictFile.get("dictCollectionError"):
        dictResult["sState"] = "failed"
        return dictResult
    listNodeIds = dictFile.get("listNodeIds")
    if listNodeIds is None:
        return dictResult
    dictResult["iTotal"] = len(listNodeIds)
    _fnCountFileOutcomes(dictResult, dictFile, sFileName, dictContext)
    dictResult["sState"] = _fsFileStateFromCounts(dictResult)
    if dictResult["sState"] != "passed":
        dictResult["sReason"] = _fsNewestRunReason(dictFile, dictContext)
    return dictResult


def _fsNewestRunReason(dictFile, dictContext):
    """Return why the newest run behind a file's outcomes is not current, or ""."""
    dictRuns = dictContext["dictMarker"]["dictRuns"]
    listRunIds = [
        dictOutcome.get("sRunId", "")
        for dictOutcome in dictFile["dictOutcomes"].values()
        if dictOutcome.get("sRunId", "") in dictRuns]
    if not listRunIds:
        return ""
    sNewest = max(listRunIds, key=lambda sKey: dictRuns[sKey].get(
        "fTimestamp", 0))
    return _fsReasonRunIsNotCurrent(sNewest, dictContext)


def _fnCountFileOutcomes(dictResult, dictFile, sFileName, dictContext):
    """Count the file's listed tests that have a current outcome."""
    for sNodeId in dictFile["listNodeIds"]:
        dictOutcome = dictFile["dictOutcomes"].get(sNodeId)
        if not dictOutcome or not _fbOutcomeIsCurrent(
            dictOutcome, sFileName, dictContext
        ):
            continue
        dictResult["iCurrent"] += 1
        if dictOutcome.get("sOutcome") not in T_NON_FAILING_OUTCOMES:
            dictResult["iFailed"] += 1


def _fsFileStateFromCounts(dictResult):
    """A file fails on any current failure and passes only when all current."""
    if dictResult["iFailed"] > 0:
        return "failed"
    if dictResult["iTotal"] and dictResult["iCurrent"] == dictResult["iTotal"]:
        return "passed"
    return "untested"


def _fsReasonRunIsNotCurrent(sRunId, dictContext):
    """Return the verdict that made a run non-current, or ""."""
    sVerdict = dictContext["dictRunVerdicts"].get(sRunId, "")
    return sVerdict if sVerdict in T_NOT_CURRENT_VERDICTS else ""


def _fdictLegacyCategoryState(sCategory, dictContext):
    """Return a category's state from its pre-runs counts, or None.

    None when the counts say nothing (no test passed or failed). A
    legacy result that is no longer current is still a statement about
    the category -- it is untested NOW, and says why -- so it comes back
    as untested rather than as silence; a marker that was never run
    clean (a non-zero exit) fails the category whatever its counts say.
    """
    dictCounts = dictContext["dictMarker"]["dictLegacyCategories"].get(sCategory)
    if not dictCounts:
        return None
    iFailed = dictCounts.get("iFailed", 0)
    iPassed = dictCounts.get("iPassed", 0)
    if not iFailed and not iPassed:
        return None
    sRunId = dictCounts.get("sRunId", "")
    dictResult = {"sState": "untested", "iTotal": iPassed + iFailed,
                  "iCurrent": 0, "iFailed": 0,
                  "sReason": _fsReasonRunIsNotCurrent(sRunId, dictContext)}
    if not _fbRunIsCurrent(sRunId, dictContext):
        return dictResult
    dictRun = dictContext["dictMarker"]["dictRuns"][sRunId]
    fRunTime = dictRun.get("fTimestamp", 0)
    if any(fMtime > fRunTime for fMtime in dictContext["dictFileMtimes"].values()):
        return dictResult
    bFailed = bool(iFailed) or dictRun.get("iExitStatus", 0) != 0
    dictResult.update({
        "sState": "failed" if bFailed else "passed",
        "iCurrent": iPassed + iFailed, "iFailed": iFailed})
    return dictResult


def _fdictStateOfCategory(sCategory, listFiles, dictContext):
    """Fold a category's files into its ``{sState, iTotalTests, ...}``."""
    listStates = [_fdictStateOfFile(sFile, dictContext) for sFile in listFiles]
    bCollected = any(
        dictState["bKnown"] and dictState["iTotal"] > 0
        or dictState["sState"] == "failed" for dictState in listStates)
    dictLegacy = None if bCollected else _fdictLegacyCategoryState(
        sCategory, dictContext)
    dictResult = {
        "sState": "untested", "iTotalTests": 0, "iCurrentTests": 0,
        "iFailedTests": 0, "bHasMarkerInfo": False, "bLegacy": False,
        "sNotCurrentBecause": "",
    }
    if dictLegacy is not None:
        dictResult.update({
            "sState": dictLegacy["sState"], "iTotalTests": dictLegacy["iTotal"],
            "iCurrentTests": dictLegacy["iCurrent"],
            "iFailedTests": dictLegacy["iFailed"],
            "bHasMarkerInfo": True, "bLegacy": True,
            "sNotCurrentBecause": dictLegacy["sReason"]})
        return dictResult
    return _fdictFoldFileStates(dictResult, listStates)


_T_REASON_SEVERITY = (
    "outputs-missing", "outputs-changed", "drift", "unrecorded",
)


def _fsStrongestReason(listReasons):
    """Return the most severe non-empty reason in the list, or ""."""
    for sReason in _T_REASON_SEVERITY:
        if sReason in listReasons:
            return sReason
    return ""


def _fdictFoldFileStates(dictResult, listStates):
    """Fold per-file states into the category: failed beats passed beats untested."""
    dictResult["bHasMarkerInfo"] = any(
        dictState["bKnown"] for dictState in listStates)
    dictResult["iTotalTests"] = sum(d["iTotal"] for d in listStates)
    dictResult["iCurrentTests"] = sum(d["iCurrent"] for d in listStates)
    dictResult["iFailedTests"] = sum(d["iFailed"] for d in listStates)
    listFileStates = [dictState["sState"] for dictState in listStates]
    if "failed" in listFileStates:
        dictResult["sState"] = "failed"
    elif listFileStates and all(sState == "passed" for sState in listFileStates):
        dictResult["sState"] = "passed"
    else:
        dictResult["sNotCurrentBecause"] = _fsStrongestReason(
            [dictState["sReason"] for dictState in listStates])
    return dictResult


def fdictCategoryStatesFromMarker(
    dictMarker, listPresentFiles, dictFileMtimes, dictRunVerdicts,
    fMaxOutputMtime,
):
    """Return ``{category: {sState, iTotalTests, iCurrentTests, ...}}``.

    THE one place a marker is read into states. Computed over the test
    files PRESENT in ``tests/`` (``listPresentFiles``), grouped by
    ``fsCategoryOfFileName``:

    * ``failed`` -- any current result failed, or a file's collection
      error stands;
    * ``passed`` -- every present file has a known test list and every
      listed test has a current, non-failing result;
    * ``untested`` -- anything else.

    A result is CURRENT when its run's data still matches (the run is
    not ``drift`` in ``dictRunVerdicts``, the step's outputs are not
    newer than the run, and no unattributed failure is newer) and the
    run is not older than the test file it lives in. ``bHasMarkerInfo``
    says whether the marker speaks for the category at all; a category it
    never mentions is left to its other writers. The vocabulary stays
    ``passed`` / ``failed`` / ``untested``; counts ride beside it.
    """
    dictNormalized = fdictNormalizeMarker(dictMarker)
    if dictNormalized is None:
        return {}
    dictContext = {
        "dictMarker": dictNormalized, "dictFileMtimes": dictFileMtimes or {},
        "dictRunVerdicts": dictRunVerdicts or {},
        "fMaxOutputMtime": fMaxOutputMtime or 0,
    }
    dictResult = {}
    for _sPrefix, sCategory in T_CATEGORY_FILE_PREFIXES:
        listFiles = [
            sFile for sFile in listPresentFiles
            if fsCategoryOfFileName(sFile) == sCategory]
        dictResult[sCategory] = _fdictStateOfCategory(
            sCategory, listFiles, dictContext)
    return dictResult
