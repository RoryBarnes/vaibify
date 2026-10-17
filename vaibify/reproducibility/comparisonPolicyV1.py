"""Comparison policy ``comparison-v1``: how a Level 3 rerun is graded.

FROZEN once released. A reproduction grades under the policy recorded
with the project (line 2 of its pinned ``.vaibify/rerunAssessment.py``),
and the dashboard runs vaibify's OWN copy of that policy, never the
repository's. So a later vaibify must not change what ``comparison-v1``
means: ``tests/testRerunAssessmentStructure.py`` pins this file's SHA-256,
and after the first release that ships it a fix is a new module and a
new policy ID. Until then it may be edited, with the pinned hash updated
in the same commit.

It is concatenated verbatim after ``rerunProcedure`` into the rendered
file, so it is a LEAF on the same terms: the standard library only
(Pillow is imported inside the image check), no ``from __future__``
import, no ``if __name__`` block, no top-level name the procedure also
defines, Python 3.8 syntax. It calls nothing in the procedure, because
the procedure is current and this is frozen.

What it fixes, in the design's words (``design/cpuClassReproducibility.md``):

* the PRECEDENCE: could not run (4), then published inputs changed (3),
  then a per-output table whose first matching rule wins, then
  step-level checks, then the roll-up (failed 1, insufficient evidence
  5, comparisons passed or byte-identical 0);
* the EVIDENCE contract: JUnit reports only, one per test command, each
  expected check looked for only in its own step's commands of its own
  category, repeated results combined worst first, and ambiguity never
  resolved by guessing;
* AGREEMENT versus REQUIRED checks: only a quantitative standard, or the
  image check for a changed raster figure, can say changed bytes still
  agree. Integrity and qualitative checks must pass, and say nothing
  about agreement;
* the IMAGE check: supported encodings read from the file itself, RGBA
  per channel, and a threshold of 2 levels on the 0-255 scale;
* which extensions are raster or vector, and the testcase names of the
  generated tests.

Grading is a PURE function of the plan and the observations
(:func:`fdictGradeAssessment`), and the report carries the observations,
so the host recomputes every derived field with its own imported copy of
this module (:func:`fdictRederiveReport`) and refuses a report it cannot
re-derive. The assessor runs in the same container as the published
code; re-derivation is what keeps its verdict honest about its own
observations. It is not a defence against deliberately adversarial
published code, and claims none.
"""

import functools
import io
import json
import os
import posixpath
import warnings
import xml.etree.ElementTree as etree


__all__ = [
    "I_IMAGE_LEVEL_THRESHOLD",
    "S_COMPARISON_POLICY",
    "TUPLE_RASTER_EXTENSIONS",
    "TUPLE_VECTOR_EXTENSIONS",
    "fdictAssessRerunEvidence",
    "fdictExtractGradingObservations",
    "fdictGradeAssessment",
    "fdictMeasureImageDifference",
    "fdictRederiveReport",
    "fsClassifyOutputKind",
    "ftReadJunitTestcases",
]


S_COMPARISON_POLICY = "comparison-v1"
I_ASSESSMENT_REPORT_SCHEMA_VERSION = 1

TUPLE_RASTER_EXTENSIONS = (".png", ".jpg", ".jpeg", ".tif", ".tiff")
TUPLE_VECTOR_EXTENSIONS = (".pdf", ".eps", ".svg")
S_KIND_DATA = "data"
S_KIND_RASTER = "raster"
S_KIND_VECTOR = "vector"

I_IMAGE_LEVEL_THRESHOLD = 2
I_MAXIMUM_EVIDENCE_BYTES = 64 * 1024 * 1024
I_MAXIMUM_FIGURE_BYTES = 256 * 1024 * 1024

# Check outcomes, worst first. A later pass never erases an earlier
# failure, and ambiguity outranks a missing report.
S_CHECK_FAILED = "failed"
S_CHECK_AMBIGUOUS = "ambiguous"
S_CHECK_DID_NOT_ASSERT = "did not assert"
S_CHECK_NOT_EXECUTED = "not executed"
S_CHECK_PASSED = "passed"
TUPLE_CHECK_SEVERITY = (
    S_CHECK_FAILED, S_CHECK_AMBIGUOUS, S_CHECK_DID_NOT_ASSERT,
    S_CHECK_NOT_EXECUTED, S_CHECK_PASSED,
)

S_OUTPUT_BYTE_IDENTICAL = "byte-identical"
S_OUTPUT_COMPARISONS_PASSED = "comparisons passed"
S_OUTPUT_FAILED = "failed"
S_OUTPUT_INSUFFICIENT = "insufficient evidence"
S_OUTPUT_CARRIED = "carried"
S_OUTPUT_NOT_GRADED = "not graded"

S_VERDICT_BYTE_IDENTICAL = "byte-identical"
S_VERDICT_COMPARISONS_PASSED = "comparisons-passed"
S_VERDICT_NOT_REPRODUCED = "not-reproduced"
S_VERDICT_INSUFFICIENT_EVIDENCE = "insufficient-evidence"
S_VERDICT_INPUTS_CHANGED = "inputs-changed"
S_VERDICT_COULD_NOT_RUN = "could-not-run"
DICT_VERDICT_EXIT_CODES = {
    S_VERDICT_BYTE_IDENTICAL: 0,
    S_VERDICT_COMPARISONS_PASSED: 0,
    S_VERDICT_NOT_REPRODUCED: 1,
    S_VERDICT_INPUTS_CHANGED: 3,
    S_VERDICT_COULD_NOT_RUN: 4,
    S_VERDICT_INSUFFICIENT_EVIDENCE: 5,
}

S_JUNIT_READ = "read"
S_JUNIT_ABSENT = "absent"
S_JUNIT_UNPARSABLE = "unparsable"
S_JUNIT_NONE_EXPECTED = "none expected"
S_SECTION_COMPLETED = "completed"

TUPLE_STRUCTURED_CATEGORIES = ("integrity", "qualitative", "quantitative")
TUPLE_TEST_GROUP_ORDER = ("integrity", "qualitative", "quantitative", "legacy")
S_LEGACY_CATEGORY = "legacy"
# The generated tests' parametrized names (templateManager): a standard
# is one node, ``<function>[<id>]``, with the id taken from this key.
DICT_STANDARD_SHAPES = {
    "quantitative": ("test_quantitative_benchmark", "sName", "sDataFile"),
    "integrity": ("test_integrity_check", "sFileName", "sFileName"),
    "qualitative": ("test_qualitative_check", "sFileName", "sFileName"),
}
S_AGREEMENT_CATEGORY = "quantitative"

TUPLE_INVOCATION_OBSERVATION_KEYS = (
    "iExitStatus", "sReportState", "sReportDetail", "listTestcases",
)
TUPLE_OUTPUT_OBSERVATION_KEYS = (
    "sExpectedSha256", "sObservedSha256", "dictImageCheck",
)
I_VERDICT_NAMES_SHOWN = 12

S_VECTOR_REMEDY = (
    "vaibify cannot assess a changed vector figure; answer \"Repeatable "
    "across computers\", add a raster companion figure, or add a "
    "quantitative standard over the plotted data")


# ----------------------------------------------------------------------
# What kind of file an output is
# ----------------------------------------------------------------------


def fsClassifyOutputKind(sPath):
    """Return ``raster``, ``vector`` or ``data`` from the path's extension."""
    sExtension = posixpath.splitext(sPath or "")[1].lower()
    if sExtension in TUPLE_RASTER_EXTENSIONS:
        return S_KIND_RASTER
    if sExtension in TUPLE_VECTOR_EXTENSIONS:
        return S_KIND_VECTOR
    return S_KIND_DATA


# ----------------------------------------------------------------------
# Reading the evidence (the only part of this module that does I/O)
# ----------------------------------------------------------------------


def fbaReadContainedFileOrNone(sRoot, sRelative, iMaximumBytes):
    """Return a file's bytes when it lies within sRoot and is not too big."""
    if not sRelative or os.path.isabs(sRelative):
        return None
    sRootReal = os.path.realpath(sRoot)
    sReal = os.path.realpath(os.path.join(sRootReal, sRelative))
    if not sReal.startswith(sRootReal + os.sep):
        return None
    try:
        iDescriptor = os.open(
            sReal, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(iDescriptor, "rb") as fileIn:
            baContent = fileIn.read(iMaximumBytes + 1)
    except OSError:
        return None
    if len(baContent) > iMaximumBytes:
        return None
    return baContent


def ftReadInvocationLedger(sEvidenceDirectory, listPlannedIds):
    """Return ``(sLedgerState, {sInvocationId: iExitStatus})``.

    One line per command that ran. A planned invocation with no line
    never ran. A line naming an invocation the plan does not, a repeated
    invocation, or a status that is not a number makes the whole ledger
    malformed: an account of what ran that cannot be read is not one.
    """
    sPath = os.path.join(sEvidenceDirectory or "", "invocations.tsv")
    if not os.path.isfile(sPath):
        return ("absent", {})
    setPlanned = set(listPlannedIds)
    dictStatuses = {}
    with open(sPath, encoding="utf-8", errors="replace") as fileIn:
        listLines = fileIn.read().splitlines()
    for sLine in listLines:
        if not sLine.strip():
            continue
        listFields = sLine.split("\t")
        if (len(listFields) != 2 or listFields[0] not in setPlanned
                or listFields[0] in dictStatuses
                or not listFields[1].strip().isdigit()):
            return (f"malformed: {sLine[:200]!r}", {})
        dictStatuses[listFields[0]] = int(listFields[1].strip())
    return ("read", dictStatuses)


def fsClassifyJunitTestcase(elementCase):
    """Return passed, failed or did not assert for one ``<testcase>``."""
    setTags = {elementChild.tag for elementChild in elementCase}
    if "failure" in setTags:
        return S_CHECK_FAILED
    if "error" in setTags or "skipped" in setTags:
        return S_CHECK_DID_NOT_ASSERT
    return S_CHECK_PASSED


def ftReadJunitTestcases(sReportPath):
    """Return ``(sReportState, listTestcases, sDetail)`` for one report.

    Each testcase is ``{sClassname, sName, sResult}``. Only the name, the
    classname and the outcome are kept; a report also carries the
    container's host name, which is never copied.
    """
    if not os.path.isfile(sReportPath):
        return (S_JUNIT_ABSENT, [], "")
    try:
        if os.path.getsize(sReportPath) > I_MAXIMUM_EVIDENCE_BYTES:
            return (S_JUNIT_UNPARSABLE, [], "the report is too large")
        elementRoot = etree.parse(sReportPath).getroot()
    except (OSError, etree.ParseError) as error:
        return (S_JUNIT_UNPARSABLE, [], f"{type(error).__name__}: {error}")
    listTestcases = [
        {
            "sClassname": str(elementCase.get("classname") or ""),
            "sName": str(elementCase.get("name") or ""),
            "sResult": fsClassifyJunitTestcase(elementCase),
        }
        for elementCase in elementRoot.iter("testcase")
    ]
    return (S_JUNIT_READ, listTestcases, "")


def fdictReadStandardSet(sRepoRoot, sStepLabel, dictSet):
    """Return one standards file's entries as the generated test reads them.

    Read from the pinned file at run time, so the checks expected are the
    ones whose bytes the manifest pins.
    """
    sCategory = dictSet["sCategory"]
    _sFunction, sNameKey, sTargetKey = DICT_STANDARD_SHAPES[sCategory]
    dictRecord = {
        "sStepLabel": sStepLabel, "sCategory": sCategory,
        "sStandardsPath": dictSet["sStandardsPath"],
        "sTestPath": dictSet["sTestPath"], "sReadState": S_JUNIT_ABSENT,
        "sReadDetail": "", "listEntries": [],
    }
    baContent = fbaReadContainedFileOrNone(
        sRepoRoot, dictSet["sStandardsPath"], I_MAXIMUM_EVIDENCE_BYTES)
    if baContent is None:
        return dictRecord
    try:
        listStandards = json.loads(baContent.decode("utf-8"))["listStandards"]
        dictRecord["listEntries"] = [
            {"sCheckName": dictStandard[sNameKey],
             "sTargetFile": dictStandard[sTargetKey]}
            for dictStandard in listStandards
        ]
        if not all(isinstance(dictEntry["sCheckName"], str)
                   and isinstance(dictEntry["sTargetFile"], str)
                   for dictEntry in dictRecord["listEntries"]):
            raise TypeError("a standard's name or file is not text")
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        dictRecord.update({"sReadState": S_JUNIT_UNPARSABLE, "listEntries": [],
                           "sReadDetail": f"{type(error).__name__}: {error}"})
        return dictRecord
    dictRecord["sReadState"] = S_JUNIT_READ
    return dictRecord


# ----------------------------------------------------------------------
# The image check (design D2)
# ----------------------------------------------------------------------


def fdictEmptyImageCheck(sReason):
    """Return an image check that did not run, naming why."""
    return {
        "bRan": False, "sEncoding": "", "iLargestLevelChange": None,
        "iPixelsChanged": None, "iPixelsOverThreshold": None,
        "sReason": sReason,
    }


def ftReadTiffTagValues(fileImage, iTag, iDefault):
    """Return a TIFF tag's values as a tuple; an absent tag is its default."""
    if iTag not in fileImage.tag_v2:
        return (iDefault,)
    if isinstance(fileImage.tag_v2[iTag], (tuple, list)):
        return tuple(fileImage.tag_v2[iTag])
    return (fileImage.tag_v2[iTag],)


def ftDescribeImageEncoding(baImage, fileImage):
    """Return ``(sEncoding, sUnsupportedReason)`` read from the file itself.

    Pillow's decoded mode does not say what a file encodes: a 16-bit RGB
    PNG opens as 8-bit ``RGB``, and two palette images with the same
    indices compare as identical (design fact 23). So the bit depth comes
    from the PNG header and the TIFF tags, never from the mode.
    """
    iFrames = int(getattr(fileImage, "n_frames", 1) or 1)
    if baImage.startswith(b"\x89PNG\r\n\x1a\n"):
        if baImage[12:16] != b"IHDR" or len(baImage) < 26:
            return ("PNG", "its PNG header is malformed")
        sEncoding = f"PNG, {baImage[24]}-bit, color type {baImage[25]}"
        if baImage[24] > 8:
            return (sEncoding, f"it has {baImage[24]}-bit samples")
    elif baImage.startswith(b"\xff\xd8\xff"):
        sEncoding = f"JPEG, {fileImage.mode}"
        if fileImage.mode not in ("L", "RGB"):
            return (sEncoding, f"it is a JPEG decoding to {fileImage.mode}")
    elif baImage[:4] in (b"II*\x00", b"MM\x00*"):
        tBits = ftReadTiffTagValues(fileImage, 258, 1)
        tFormats = ftReadTiffTagValues(fileImage, 339, 1)
        sEncoding = f"TIFF, {max(tBits)}-bit, {fileImage.mode}"
        if max(tBits) > 8:
            return (sEncoding, f"it has {max(tBits)}-bit samples")
        if any(iFormat != 1 for iFormat in tFormats):
            return (sEncoding, "it has floating-point or signed samples")
        if fileImage.mode not in ("1", "L", "LA", "P", "RGB", "RGBA"):
            return (sEncoding, f"it is a TIFF decoding to {fileImage.mode}")
    else:
        return ("", "it is not a PNG, JPEG or TIFF file")
    if iFrames != 1:
        return (sEncoding, f"it has {iFrames} frames")
    return (sEncoding, "")


def flistHistogramLargestChannelChange(fileImageA, fileImageB):
    """Return the 256-bin histogram of each pixel's largest RGBA change.

    Converting to RGBA first resolves palettes and palette transparency.
    The histogram's highest occupied bin is the largest change anywhere,
    and its tail counts the pixels that moved.
    """
    from PIL import ImageChops
    tBands = ImageChops.difference(
        fileImageA.convert("RGBA"), fileImageB.convert("RGBA")).split()
    return functools.reduce(ImageChops.lighter, tBands).histogram()


def fdictCompareOpenedImages(baReference, baObserved, fileReference,
                             fileObserved):
    """Return the image check for two opened figures, or why it cannot run."""
    sEncoding, sUnsupported = ftDescribeImageEncoding(
        baReference, fileReference)
    sObservedEncoding, sObservedUnsupported = ftDescribeImageEncoding(
        baObserved, fileObserved)
    if sUnsupported or sObservedUnsupported:
        return fdictEmptyImageCheck(
            "cannot assess: the "
            + ("published" if sUnsupported else "regenerated")
            + " figure is not a supported encoding ("
            + (sUnsupported or sObservedUnsupported) + ")")
    if (sEncoding.split(",")[0] != sObservedEncoding.split(",")[0]
            or fileReference.size != fileObserved.size
            or fileReference.mode != fileObserved.mode):
        return fdictEmptyImageCheck(
            f"cannot assess: the published figure ({sEncoding}, "
            f"{fileReference.size[0]}x{fileReference.size[1]}) and the "
            f"regenerated one ({sObservedEncoding}, "
            f"{fileObserved.size[0]}x{fileObserved.size[1]}) differ in "
            "format, dimensions or decoded mode")
    listHistogram = flistHistogramLargestChannelChange(
        fileReference, fileObserved)
    return {
        "bRan": True, "sEncoding": sEncoding,
        "iLargestLevelChange": max(
            iLevel for iLevel in range(256) if listHistogram[iLevel]),
        "iPixelsChanged": sum(listHistogram[1:]),
        "iPixelsOverThreshold": sum(
            listHistogram[I_IMAGE_LEVEL_THRESHOLD + 1:]),
        "sReason": "",
    }


def fdictMeasureImageDifference(sReferencePath, sRepoRoot, sObservedPath):
    """Return D2's measurement of one changed raster figure; never raises.

    Pillow missing, a decompression bomb and any decoding error all mean
    the figure cannot be assessed, which is insufficient evidence. A
    higher-precision image is never reduced to 8 bits to make it
    comparable.
    """
    if not sReferencePath:
        return fdictEmptyImageCheck(
            "cannot assess: the published figure was not set aside before "
            "the rerun, because its bytes did not match its manifest hash")
    try:
        from PIL import Image
    except ImportError:
        return fdictEmptyImageCheck(
            "cannot assess: Pillow is not installed in the image")
    baReference = fbaReadContainedFileOrNone(
        os.path.dirname(sReferencePath), os.path.basename(sReferencePath),
        I_MAXIMUM_FIGURE_BYTES)
    baObserved = fbaReadContainedFileOrNone(
        sRepoRoot, sObservedPath, I_MAXIMUM_FIGURE_BYTES)
    if baReference is None or baObserved is None:
        return fdictEmptyImageCheck(
            "cannot assess: a figure could not be read, or is too large")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(baReference)) as fileReference, \
                    Image.open(io.BytesIO(baObserved)) as fileObserved:
                return fdictCompareOpenedImages(
                    baReference, baObserved, fileReference, fileObserved)
    except Exception as error:
        return fdictEmptyImageCheck(
            f"cannot assess: the figure could not be decoded "
            f"({type(error).__name__})")


# ----------------------------------------------------------------------
# Gathering, then grading
# ----------------------------------------------------------------------


def flistPlannedInvocations(dictPlan):
    """Return ``(sStepLabel, sSection, sInvocationId)`` in execution order."""
    listPlanned = []
    for dictStep in dictPlan["listSteps"]:
        sLabel = dictStep["sStepLabel"]
        for sId in dictStep["listDataInvocations"]:
            listPlanned.append((sLabel, "data", sId))
        for sId in dictStep["listPlotInvocations"]:
            listPlanned.append((sLabel, "plot", sId))
        for sGroup in TUPLE_TEST_GROUP_ORDER:
            for sId in dictStep["dictTestGroups"].get(sGroup, []):
                listPlanned.append((sLabel, sGroup, sId))
    return listPlanned


def fdictObserveInvocation(sSection, sInvocationId, dictStatuses, sEvidence):
    """Return one invocation's exit status and, for a test, its report."""
    iExitStatus = dictStatuses.get(sInvocationId)
    dictObserved = {
        "iExitStatus": iExitStatus, "sReportState": S_JUNIT_NONE_EXPECTED,
        "sReportDetail": "", "listTestcases": [],
    }
    if sSection in ("data", "plot"):
        return dictObserved
    if iExitStatus is None:
        dictObserved.update(
            {"sReportState": S_JUNIT_ABSENT, "sReportDetail": "never ran"})
        return dictObserved
    sState, listTestcases, sDetail = ftReadJunitTestcases(
        os.path.join(sEvidence, sInvocationId + ".xml"))
    dictObserved.update({"sReportState": sState, "sReportDetail": sDetail,
                         "listTestcases": listTestcases})
    return dictObserved


def fdictGatherPolicyObservations(dictPlan, dictObservations):
    """Read the ledger, the reports, the standards and the figures."""
    sRepoRoot = dictObservations.get("sRepoRoot") or ""
    sEvidence = dictObservations.get("sEvidenceDirectory") or ""
    listPlanned = flistPlannedInvocations(dictPlan)
    sLedgerState, dictStatuses = ftReadInvocationLedger(
        sEvidence, [tPlanned[2] for tPlanned in listPlanned])
    dictExpected = dict(dictObservations.get("dictExpectedHashes") or {})
    dictObserved = dict(dictObservations.get("dictObservedHashes") or {})
    dictReferences = dict(dictObservations.get("dictReferencePaths") or {})
    dictOutputs = {}
    for sPath in dictExpected:
        dictOutputs[sPath] = {
            "sExpectedSha256": dictExpected[sPath],
            "sObservedSha256": dictObserved.get(sPath),
            "dictImageCheck": None,
        }
    fnMeasureChangedFigures(dictPlan, dictOutputs, dictReferences, sRepoRoot)
    return {
        "sComparisonPolicySource": dictObservations.get(
            "sComparisonPolicySource", "recorded"),
        "sManifestState": dictObservations.get("sManifestState", "read"),
        "sLedgerState": sLedgerState,
        "dictIntegrity": dict(dictObservations.get("dictIntegrity") or {}),
        "dictInvocations": {
            sId: fdictObserveInvocation(sSection, sId, dictStatuses, sEvidence)
            for _sLabel, sSection, sId in listPlanned},
        "listStandardSets": [
            fdictReadStandardSet(sRepoRoot, dictStep["sStepLabel"], dictSet)
            for dictStep in dictPlan["listSteps"]
            for dictSet in dictStep["listStandardSets"]],
        "dictOutputs": dictOutputs,
        "dictContexts": dict(dictObservations.get("dictContexts") or {}),
    }


def fnMeasureChangedFigures(dictPlan, dictOutputs, dictReferences, sRepoRoot):
    """Run the image check on each raster output whose bytes changed."""
    for dictStep in dictPlan["listSteps"]:
        for dictOutput in dictStep["listOutputs"]:
            dictObservedOutput = dictOutputs.get(dictOutput["sPath"])
            if (dictOutput["sKind"] != S_KIND_RASTER
                    or dictObservedOutput is None
                    or dictObservedOutput["dictImageCheck"] is not None
                    or dictObservedOutput["sObservedSha256"] is None
                    or dictObservedOutput["sObservedSha256"]
                    == dictObservedOutput["sExpectedSha256"]):
                continue
            dictObservedOutput["dictImageCheck"] = fdictMeasureImageDifference(
                dictReferences.get(dictOutput["sPath"], ""), sRepoRoot,
                dictOutput["sPath"])


def fdictAssessRerunEvidence(dictPlan, dictObservations):
    """The policy's entry point: gather the evidence, then grade it.

    Every policy module defines this name; the procedure finds it by
    name in the rendered file.
    """
    if dictObservations.get("sManifestState", "read") != "read":
        return fdictGradeAssessment(dictPlan, {
            "sComparisonPolicySource": dictObservations.get(
                "sComparisonPolicySource", "recorded"),
            "sManifestState": dictObservations["sManifestState"],
            "sLedgerState": "absent", "dictIntegrity": {},
            "dictInvocations": {}, "listStandardSets": [], "dictOutputs": {},
            "dictContexts": {},
        })
    return fdictGradeAssessment(
        dictPlan, fdictGatherPolicyObservations(dictPlan, dictObservations))


# ----------------------------------------------------------------------
# Grading: expected checks, bound to their own step and category
# ----------------------------------------------------------------------


def fsResolveStandardTarget(sTestPath, sTargetFile):
    """Return the repo path a standard names, as the generated test opens it.

    The generated test joins the name onto its step directory, which is
    the test file's grandparent. An absolute name, or one that climbs out
    of the repository, names no planned output.
    """
    if not sTargetFile or sTargetFile.startswith("/"):
        return ""
    sStepDirectory = posixpath.dirname(posixpath.dirname(sTestPath))
    sJoined = posixpath.normpath(posixpath.join(sStepDirectory, sTargetFile))
    if sJoined == ".." or sJoined.startswith("../"):
        return ""
    return sJoined


def fbClassnameNamesTestFile(sClassname, sTestPath):
    """Return True when a JUnit classname can denote this test file.

    The classname depends on the project's pytest configuration (design
    fact 22): ``StepOne.tests.test_x`` with a root configuration file,
    ``tests.test_x`` without one. Either is the file's dotted path or a
    suffix of it that starts after a dot.
    """
    sModule = sTestPath[:-3] if sTestPath.endswith(".py") else sTestPath
    sDotted = sModule.replace("/", ".")
    return bool(sClassname) and (
        sClassname == sDotted or sDotted.endswith("." + sClassname))


def flistBuildExpectedChecks(dictStandardSet):
    """Return one expected check per standard in a readable standards file."""
    if dictStandardSet["sReadState"] != S_JUNIT_READ:
        return []
    sFunction = DICT_STANDARD_SHAPES[dictStandardSet["sCategory"]][0]
    listNames = [dictEntry["sCheckName"]
                 for dictEntry in dictStandardSet["listEntries"]]
    return [
        {
            "sStepLabel": dictStandardSet["sStepLabel"],
            "sCategory": dictStandardSet["sCategory"],
            "sTestPath": dictStandardSet["sTestPath"],
            "sTestcaseName": f"{sFunction}[{dictEntry['sCheckName']}]",
            "sTargetPath": fsResolveStandardTarget(
                dictStandardSet["sTestPath"], dictEntry["sTargetFile"]),
            "bDuplicateName": listNames.count(dictEntry["sCheckName"]) > 1,
        }
        for dictEntry in dictStandardSet["listEntries"]
    ]


def ftCombineWorstFirst(listOccurrences):
    """Return the worst ``(sOutcome, sReason)`` among the occurrences."""
    return min(
        listOccurrences,
        key=lambda tOccurrence: TUPLE_CHECK_SEVERITY.index(tOccurrence[0]))


def ftDescribeAbsentCheck(listGroupIds, dictInvocations, sCategory):
    """Return why an expected check has no occurrence at all."""
    if not listGroupIds:
        return (S_CHECK_NOT_EXECUTED,
                f"no test command runs this step's {sCategory} tests")
    listNeverRan = [sId for sId in listGroupIds
                    if dictInvocations[sId]["iExitStatus"] is None]
    if len(listNeverRan) == len(listGroupIds):
        return (S_CHECK_NOT_EXECUTED, ", ".join(listNeverRan) + " never ran")
    return (S_CHECK_NOT_EXECUTED,
            "absent from the reports of " + ", ".join(listGroupIds))


def flistMatchingTestFiles(dictTestcase, listExpectedFiles):
    """Return the step's expected test files a testcase's classname names."""
    return [sTestPath for sTestPath in listExpectedFiles
            if fbClassnameNamesTestFile(dictTestcase["sClassname"], sTestPath)]


def ftCollectOccurrences(dictStep, listExpected, dictInvocations):
    """Return ``(dictOccurrences, listUnconsumed)`` for one step.

    An expected check is looked for ONLY in the reports of this step's
    commands of its own category, so one step's report never satisfies
    another's check. A testcase that could belong to two expected files
    is an ambiguous occurrence, and it is also kept as a step-level
    check, so a failure inside it is never hidden by the ambiguity.
    """
    listExpectedFiles = sorted({dictCheck["sTestPath"]
                                for dictCheck in listExpected})
    dictOccurrences = {iIndex: [] for iIndex in range(len(listExpected))}
    listUnconsumed = []
    for sGroup in TUPLE_TEST_GROUP_ORDER:
        listInGroup = [iIndex for iIndex, dictCheck in enumerate(listExpected)
                       if dictCheck["sCategory"] == sGroup]
        for sId in dictStep["dictTestGroups"].get(sGroup, []):
            fnRecordInvocationOccurrences(
                sId, dictInvocations[sId], listInGroup, listExpected,
                listExpectedFiles, dictOccurrences, listUnconsumed)
    return dictOccurrences, listUnconsumed


def fnRecordInvocationOccurrences(sId, dictInvocation, listInGroup,
                                  listExpected, listExpectedFiles,
                                  dictOccurrences, listUnconsumed):
    """File one invocation's testcases under the checks they evidence."""
    if dictInvocation["iExitStatus"] is None:
        return
    if dictInvocation["sReportState"] != S_JUNIT_READ:
        for iIndex in listInGroup:
            dictOccurrences[iIndex].append((
                S_CHECK_DID_NOT_ASSERT,
                f"{sId} wrote no readable JUnit report (is pytest installed "
                "in the image, or does the command name its own "
                "--junitxml?)"))
        return
    for dictTestcase in dictInvocation["listTestcases"]:
        listMatched = flistMatchingTestFiles(dictTestcase, listExpectedFiles)
        bConsumed = False
        for iIndex in listInGroup:
            dictCheck = listExpected[iIndex]
            if (dictCheck["sTestcaseName"] != dictTestcase["sName"]
                    or dictCheck["sTestPath"] not in listMatched):
                continue
            bConsumed = len(listMatched) == 1
            dictOccurrences[iIndex].append(
                (dictTestcase["sResult"], f"reported by {sId}")
                if bConsumed else
                (S_CHECK_AMBIGUOUS, f"{dictTestcase['sClassname']} in {sId} "
                 "could be " + " or ".join(listMatched)))
        if not bConsumed:
            listUnconsumed.append((sId, dictTestcase))


def flistGradeExpectedChecks(dictStep, listExpected, dictInvocations,
                             dictOccurrences):
    """Return every expected check of one step with its outcome."""
    listGraded = []
    for iIndex, dictCheck in enumerate(listExpected):
        if dictCheck["bDuplicateName"]:
            sOutcome, sReason = (
                S_CHECK_AMBIGUOUS, "the standards file names this check "
                "more than once")
        elif dictOccurrences[iIndex]:
            sOutcome, sReason = ftCombineWorstFirst(dictOccurrences[iIndex])
        else:
            sOutcome, sReason = ftDescribeAbsentCheck(
                dictStep["dictTestGroups"].get(dictCheck["sCategory"], []),
                dictInvocations, dictCheck["sCategory"])
        dictGraded = {sKey: dictCheck[sKey] for sKey in (
            "sStepLabel", "sCategory", "sTestPath", "sTestcaseName",
            "sTargetPath")}
        dictGraded.update({"sOutcome": sOutcome, "sReason": sReason})
        listGraded.append(dictGraded)
    return listGraded


# ----------------------------------------------------------------------
# Grading: step-level checks
# ----------------------------------------------------------------------


def flistGradeUnconsumedTestcases(sStepLabel, listUnconsumed):
    """Return one step-level check per testcase no expected check took."""
    dictCombined = {}
    for sId, dictTestcase in listUnconsumed:
        sCheck = f"{dictTestcase['sClassname']}::{dictTestcase['sName']}"
        dictCombined.setdefault(sCheck, []).append(
            (dictTestcase["sResult"], f"reported by {sId}"))
    listChecks = []
    for sCheck in sorted(dictCombined):
        sOutcome, sReason = ftCombineWorstFirst(dictCombined[sCheck])
        listChecks.append({"sStepLabel": sStepLabel, "sCheck": sCheck,
                           "sOutcome": sOutcome, "sReason": sReason})
    return listChecks


def fdictCommandStepLevelCheck(sStepLabel, sId, dictInvocation,
                               bHasExpectedChecks):
    """Return the step-level check one test command contributes, or None."""
    iExit = dictInvocation["iExitStatus"]
    if iExit is None:
        return {"sStepLabel": sStepLabel, "sCheck": sId,
                "sOutcome": S_CHECK_NOT_EXECUTED, "sReason": "never ran"}
    if dictInvocation["sReportState"] != S_JUNIT_READ:
        if bHasExpectedChecks:
            return None
        return {"sStepLabel": sStepLabel, "sCheck": sId,
                "sOutcome": S_CHECK_PASSED if iExit == 0 else S_CHECK_FAILED,
                "sReason": f"judged by its exit status alone ({iExit})"}
    bReportedFailure = any(dictTestcase["sResult"] == S_CHECK_FAILED
                           for dictTestcase in dictInvocation["listTestcases"])
    if iExit != 0 and not bReportedFailure:
        return {"sStepLabel": sStepLabel, "sCheck": sId,
                "sOutcome": S_CHECK_DID_NOT_ASSERT,
                "sReason": f"exited {iExit} without reporting a failure "
                           "(pytest interrupted, errored, or collected "
                           "nothing)"}
    return None


def flistCommandStepLevelChecks(dictStep, listExpected, dictInvocations):
    """Return the step-level checks the step's test commands contribute."""
    setCategoriesWithChecks = {dictCheck["sCategory"]
                               for dictCheck in listExpected}
    listChecks = []
    for sGroup in TUPLE_TEST_GROUP_ORDER:
        for sId in dictStep["dictTestGroups"].get(sGroup, []):
            dictCheck = fdictCommandStepLevelCheck(
                dictStep["sStepLabel"], sId, dictInvocations[sId],
                sGroup in setCategoriesWithChecks)
            if dictCheck is not None:
                listChecks.append(dictCheck)
    return listChecks


def flistUnreadableStandardsChecks(listStandardSets):
    """Return a step-level check for every standards file that cannot be read."""
    return [
        {"sStepLabel": dictSet["sStepLabel"],
         "sCheck": f"standards file {dictSet['sStandardsPath']}",
         "sOutcome": S_CHECK_DID_NOT_ASSERT,
         "sReason": "the standards file could not be read: "
                    + dictSet["sReadDetail"]}
        for dictSet in listStandardSets
        if dictSet["sReadState"] == S_JUNIT_UNPARSABLE
    ]


# ----------------------------------------------------------------------
# Grading: sections, outputs, the roll-up
# ----------------------------------------------------------------------


def fsDescribeSectionState(listIds, dictInvocations):
    """Return ``completed``, ``failed: <ID> exited <n>`` or ``not run: ...``."""
    for sId in listIds:
        iExit = dictInvocations[sId]["iExitStatus"]
        if iExit is None:
            return f"not run: {sId} never ran"
        if iExit != 0:
            return f"failed: {sId} exited {iExit}"
    return S_SECTION_COMPLETED


def fdictDescribeStepSections(dictStep, dictInvocations):
    """Return the data and plot section states, in the runner's order."""
    sData = fsDescribeSectionState(
        dictStep["listDataInvocations"], dictInvocations)
    if sData != S_SECTION_COMPLETED:
        sPlot = f"not run: the data section did not complete ({sData})"
    else:
        sPlot = fsDescribeSectionState(
            dictStep["listPlotInvocations"], dictInvocations)
    return {"data": sData, "plot": sPlot}


def ftGradeChangedBytes(dictOutput, listAgreement):
    """Return the outcome of an output whose bytes differ (checks all passed)."""
    if dictOutput["sKind"] == S_KIND_VECTOR:
        return (S_OUTPUT_INSUFFICIENT, S_VECTOR_REMEDY)
    if dictOutput["sKind"] == S_KIND_RASTER:
        dictImage = dictOutput["dictImageCheck"] or fdictEmptyImageCheck(
            "cannot assess: the image check did not run")
        if not dictImage["bRan"]:
            return (S_OUTPUT_INSUFFICIENT, dictImage["sReason"])
        if dictImage["iLargestLevelChange"] > I_IMAGE_LEVEL_THRESHOLD:
            return (S_OUTPUT_INSUFFICIENT,
                    f"its largest pixel change is "
                    f"{dictImage['iLargestLevelChange']} levels, beyond "
                    f"vaibify's image-difference threshold (policy v1) of "
                    f"{I_IMAGE_LEVEL_THRESHOLD}")
        return (S_OUTPUT_COMPARISONS_PASSED,
                "within vaibify's image-difference threshold (policy v1)")
    if not listAgreement:
        return (S_OUTPUT_INSUFFICIENT,
                "its bytes differ and no published comparison covers it "
                "(integrity and qualitative checks never establish "
                "agreement)")
    return (S_OUTPUT_COMPARISONS_PASSED,
            f"{len(listAgreement)} published comparison(s) passed")


def ftGradeOneOutput(dictOutput, sSectionState, listAgreement, listRequired):
    """Return ``(sOutcome, sReason)``; the first matching rule wins."""
    if sSectionState != S_SECTION_COMPLETED:
        return (S_OUTPUT_FAILED, f"the {dictOutput['sSection']} section "
                f"did not complete ({sSectionState})")
    if dictOutput["sObservedSha256"] is None:
        return (S_OUTPUT_FAILED, "missing: the rerun did not write it")
    listChecks = listAgreement + listRequired
    for dictCheck in listChecks:
        if dictCheck["sOutcome"] == S_CHECK_FAILED:
            return (S_OUTPUT_FAILED,
                    f"{dictCheck['sTestcaseName']} failed")
    for dictCheck in listChecks:
        if dictCheck["sOutcome"] != S_CHECK_PASSED:
            return (S_OUTPUT_INSUFFICIENT,
                    f"{dictCheck['sTestcaseName']} {dictCheck['sOutcome']}: "
                    f"{dictCheck['sReason']}")
    if dictOutput["sObservedSha256"] == dictOutput["sExpectedSha256"]:
        return (S_OUTPUT_BYTE_IDENTICAL, "")
    return ftGradeChangedBytes(dictOutput, listAgreement)


def flistBuildOutputRecords(dictPlan, dictOutputsObserved):
    """Return one record per pinned planned output, then per carried path."""
    listRecords = []
    setSeen = set()
    setCarried = set(dictPlan.get("listCarriedPaths") or [])
    for dictStep in dictPlan["listSteps"]:
        for dictOutput in dictStep["listOutputs"]:
            sPath = dictOutput["sPath"]
            if (sPath in setSeen or sPath in setCarried
                    or sPath not in dictOutputsObserved):
                continue
            setSeen.add(sPath)
            dictRecord = {"sPath": sPath, "sRole": "output",
                          "sStepLabel": dictStep["sStepLabel"],
                          "sSection": dictOutput["sSection"],
                          "sKind": dictOutput["sKind"]}
            dictRecord.update(dictOutputsObserved[sPath])
            listRecords.append(dictRecord)
    for sPath in sorted(setCarried & set(dictOutputsObserved)):
        dictRecord = {"sPath": sPath, "sRole": "carried", "sStepLabel": "",
                      "sSection": "", "sKind": fsClassifyOutputKind(sPath)}
        dictRecord.update(dictOutputsObserved[sPath])
        listRecords.append(dictRecord)
    return listRecords


def flistGradeOutputs(listRecords, dictSections, listAllChecks, bGrade):
    """Return the per-output table, graded unless the verdict forbids it."""
    listGraded = []
    for dictRecord in listRecords:
        dictOutput = dict(dictRecord)
        listAgreement = [dictCheck for dictCheck in listAllChecks
                         if dictCheck["sTargetPath"] == dictRecord["sPath"]
                         and dictCheck["sCategory"] == S_AGREEMENT_CATEGORY]
        listRequired = [dictCheck for dictCheck in listAllChecks
                        if dictCheck["sTargetPath"] == dictRecord["sPath"]
                        and dictCheck["sCategory"] != S_AGREEMENT_CATEGORY]
        dictOutput["listAgreementChecks"] = listAgreement
        dictOutput["listRequiredChecks"] = listRequired
        if dictRecord["sRole"] == "carried":
            dictOutput.update({"sSectionState": "", "sOutcome": S_OUTPUT_CARRIED,
                               "sReason": "given by a human step, not graded"})
        else:
            sState = dictSections[dictRecord["sStepLabel"]][
                dictRecord["sSection"]]
            sOutcome, sReason = ftGradeOneOutput(
                dictRecord, sState, listAgreement, listRequired)
            if not bGrade:
                sOutcome, sReason = S_OUTPUT_NOT_GRADED, ""
            dictOutput.update({"sSectionState": sState, "sOutcome": sOutcome,
                               "sReason": sReason})
        listGraded.append(dictOutput)
    return listGraded


def ftBindAllChecks(dictPlan, dictGrading):
    """Return ``(listExpectedChecks, listStepLevelChecks)`` for every step."""
    dictInvocations = dictGrading["dictInvocations"]
    listAllExpected, listStepLevel = [], []
    for dictStep in dictPlan["listSteps"]:
        listExpected = []
        for dictSet in dictGrading["listStandardSets"]:
            if dictSet["sStepLabel"] == dictStep["sStepLabel"]:
                listExpected.extend(flistBuildExpectedChecks(dictSet))
        dictOccurrences, listUnconsumed = ftCollectOccurrences(
            dictStep, listExpected, dictInvocations)
        listAllExpected.extend(flistGradeExpectedChecks(
            dictStep, listExpected, dictInvocations, dictOccurrences))
        listStepLevel.extend(flistGradeUnconsumedTestcases(
            dictStep["sStepLabel"], listUnconsumed))
        listStepLevel.extend(flistCommandStepLevelChecks(
            dictStep, listExpected, dictInvocations))
    listStepLevel.extend(
        flistUnreadableStandardsChecks(dictGrading["listStandardSets"]))
    return listAllExpected, listStepLevel


def flistOrphanChecksAsStepLevel(listExpected, setGradedPaths):
    """Return expected checks naming no graded output, as step-level checks."""
    return [
        {"sStepLabel": dictCheck["sStepLabel"],
         "sCheck": f"{dictCheck['sTestPath']}::{dictCheck['sTestcaseName']}",
         "sOutcome": dictCheck["sOutcome"], "sReason": dictCheck["sReason"]}
        for dictCheck in listExpected
        if dictCheck["sTargetPath"] not in setGradedPaths
    ]


# ----------------------------------------------------------------------
# The verdict, its wording, and its exit code
# ----------------------------------------------------------------------


def fsListNamesForVerdict(listNames):
    """Join names for a verdict line, saying how many were left out."""
    if len(listNames) <= I_VERDICT_NAMES_SHOWN:
        return ", ".join(listNames)
    return (", ".join(listNames[:I_VERDICT_NAMES_SHOWN])
            + f", and {len(listNames) - I_VERDICT_NAMES_SHOWN} more (see "
            "the report)")


def fsFindCouldNotRunReason(dictGrading):
    """Return why the rerun could not be graded at all, or ""."""
    if dictGrading["sManifestState"] != "read":
        return ("MANIFEST.sha256 missing or unreadable, or the preparation "
                f"did not run ({dictGrading['sManifestState']})")
    if dictGrading["sLedgerState"].startswith("malformed"):
        return ("the record of which commands ran is "
                + dictGrading["sLedgerState"])
    return ""


def fdictJudgeIntegrity(dictIntegrity):
    """Return the integrity observations with their verdict derived from them.

    ``bInputsUnchanged`` is recomputed from the lists rather than taken
    from whoever observed them, so a report whose flag disagrees with
    its own lists cannot be re-derived.
    """
    dictJudged = dict(dictIntegrity)
    dictJudged["bInputsUnchanged"] = bool(dictIntegrity) and not (
        dictIntegrity.get("listChangedInputs")
        or dictIntegrity.get("listMissingInputs")
        or dictIntegrity.get("bManifestMoved"))
    return dictJudged


def fsFindUngradableReason(dictGrading, listGraded):
    """Return why an intact rerun still cannot be graded, or ""."""
    bAnyPlanned = bool(dictGrading["dictInvocations"])
    if dictGrading["sLedgerState"] == "absent" and bAnyPlanned:
        return ("there is no record of which commands ran (the "
                "invocation ledger is absent)")
    if not listGraded:
        return ("no declared output of a step the rerun executes is pinned "
                "in MANIFEST.sha256, so the rerun has nothing it could have "
                "regenerated and compared")
    return ""


def fsListChangedInputs(dictIntegrity):
    """Return the changed published inputs, named for the verdict."""
    listNames = list(dictIntegrity.get("listChangedInputs") or [])
    listNames += [f"{sPath} (missing)"
                  for sPath in dictIntegrity.get("listMissingInputs") or []]
    if dictIntegrity.get("bManifestMoved"):
        listNames.append("MANIFEST.sha256 (changed during the rerun)")
    return listNames


def ftDecideVerdict(dictGrading, dictIntegrity, listOutputs, listStepLevel):
    """Return ``(sVerdict, sVerdictRendered)`` under the precedence."""
    sCouldNotRun = fsFindCouldNotRunReason(dictGrading)
    if sCouldNotRun:
        return (S_VERDICT_COULD_NOT_RUN, f"Could not run: {sCouldNotRun}")
    if not dictIntegrity["bInputsUnchanged"]:
        return (S_VERDICT_INPUTS_CHANGED, "Published inputs changed: "
                + fsListNamesForVerdict(fsListChangedInputs(dictIntegrity)))
    listGraded = [dictOutput for dictOutput in listOutputs
                  if dictOutput["sRole"] == "output"]
    sUngradable = fsFindUngradableReason(dictGrading, listGraded)
    if sUngradable:
        return (S_VERDICT_COULD_NOT_RUN, f"Could not run: {sUngradable}")
    return ftRollUpGradedVerdict(listGraded, listStepLevel)


def ftRollUpGradedVerdict(listGraded, listStepLevel):
    """Return the overall verdict of a graded rerun, with its wording."""
    listFailed = [f"{dictOutput['sPath']} ({dictOutput['sReason']})"
                  for dictOutput in listGraded
                  if dictOutput["sOutcome"] == S_OUTPUT_FAILED]
    listFailed += [f"step {dictCheck['sStepLabel']} check {dictCheck['sCheck']}"
                   f" ({dictCheck['sReason']})" for dictCheck in listStepLevel
                   if dictCheck["sOutcome"] == S_CHECK_FAILED]
    if listFailed:
        return (S_VERDICT_NOT_REPRODUCED, "Not reproduced: "
                + fsListNamesForVerdict(listFailed) + " failed")
    listLacking = [dictOutput["sPath"] for dictOutput in listGraded
                   if dictOutput["sOutcome"] == S_OUTPUT_INSUFFICIENT]
    listLacking += [f"step {dictCheck['sStepLabel']} check {dictCheck['sCheck']}"
                    for dictCheck in listStepLevel
                    if dictCheck["sOutcome"] != S_CHECK_PASSED]
    if listLacking:
        return (S_VERDICT_INSUFFICIENT_EVIDENCE, "Not enough evidence: "
                + fsListNamesForVerdict(listLacking) + " were not assessed")
    iDiffering = sum(1 for dictOutput in listGraded
                     if dictOutput["sOutcome"] == S_OUTPUT_COMPARISONS_PASSED)
    if iDiffering:
        return (S_VERDICT_COMPARISONS_PASSED,
                "Reproduced: published comparisons passed; "
                f"{iDiffering} of {len(listGraded)} files differ in their "
                "bytes")
    return (S_VERDICT_BYTE_IDENTICAL, "Reproduced: every file byte-identical")


def fdictBuildTally(listOutputs, listStepLevel):
    """Return the count of every category, so a mixed result shows all."""
    def fiCountOutputs(sOutcome):
        return sum(1 for dictOutput in listOutputs
                   if dictOutput["sOutcome"] == sOutcome)
    return {
        "iOutputsGraded": sum(1 for dictOutput in listOutputs
                              if dictOutput["sRole"] == "output"),
        "iByteIdentical": fiCountOutputs(S_OUTPUT_BYTE_IDENTICAL),
        "iComparisonsPassed": fiCountOutputs(S_OUTPUT_COMPARISONS_PASSED),
        "iFailed": fiCountOutputs(S_OUTPUT_FAILED),
        "iInsufficientEvidence": fiCountOutputs(S_OUTPUT_INSUFFICIENT),
        "iNotGraded": fiCountOutputs(S_OUTPUT_NOT_GRADED),
        "iCarried": fiCountOutputs(S_OUTPUT_CARRIED),
        "iStepLevelPassed": sum(1 for dictCheck in listStepLevel
                                if dictCheck["sOutcome"] == S_CHECK_PASSED),
        "iStepLevelFailed": sum(1 for dictCheck in listStepLevel
                                if dictCheck["sOutcome"] == S_CHECK_FAILED),
        "iStepLevelLackingEvidence": sum(
            1 for dictCheck in listStepLevel
            if dictCheck["sOutcome"] not in (S_CHECK_PASSED, S_CHECK_FAILED)),
    }


def fsRenderTally(dictTally):
    """Return the tally line printed beside every verdict."""
    if dictTally["iNotGraded"]:
        return (f"Tally: {dictTally['iNotGraded']} regenerated outputs not "
                f"graded; {dictTally['iCarried']} carried as given.")
    return (
        f"Tally: {dictTally['iOutputsGraded']} regenerated outputs: "
        f"{dictTally['iByteIdentical']} byte-identical, "
        f"{dictTally['iComparisonsPassed']} comparisons passed, "
        f"{dictTally['iFailed']} failed, "
        f"{dictTally['iInsufficientEvidence']} not enough evidence; "
        f"{dictTally['iCarried']} carried as given; step-level checks: "
        f"{dictTally['iStepLevelPassed']} passed, "
        f"{dictTally['iStepLevelFailed']} failed, "
        f"{dictTally['iStepLevelLackingEvidence']} without evidence.")


# ----------------------------------------------------------------------
# The report, and its re-derivation on the host
# ----------------------------------------------------------------------


def flistBuildInvocationRecords(dictPlan, dictInvocations):
    """Return one record per planned invocation, in execution order."""
    listRecords = []
    for sLabel, sSection, sId in flistPlannedInvocations(dictPlan):
        dictRecord = {"sInvocationId": sId, "sStepLabel": sLabel,
                      "sSection": sSection}
        dictRecord.update(dictInvocations.get(sId) or {
            "iExitStatus": None, "sReportState": S_JUNIT_ABSENT,
            "sReportDetail": "never ran", "listTestcases": []})
        listRecords.append(dictRecord)
    return listRecords


def fdictGradeAssessment(dictPlan, dictGrading):
    """Return the whole report; a pure function of the plan and observations."""
    dictInvocations = {
        dictRecord["sInvocationId"]: dictRecord
        for dictRecord in flistBuildInvocationRecords(
            dictPlan, dictGrading["dictInvocations"])}
    listExpected, listStepLevel = ftBindAllChecks(
        dict(dictPlan), dict(dictGrading, dictInvocations=dictInvocations))
    dictSections = {
        dictStep["sStepLabel"]: fdictDescribeStepSections(
            dictStep, dictInvocations) for dictStep in dictPlan["listSteps"]}
    listRecords = flistBuildOutputRecords(dictPlan, dictGrading["dictOutputs"])
    setGradedPaths = {dictRecord["sPath"] for dictRecord in listRecords
                      if dictRecord["sRole"] == "output"}
    listStepLevel = (listStepLevel + flistOrphanChecksAsStepLevel(
        listExpected, setGradedPaths))
    dictIntegrity = fdictJudgeIntegrity(dictGrading["dictIntegrity"])
    bGrade = (not fsFindCouldNotRunReason(dictGrading)
              and dictIntegrity["bInputsUnchanged"])
    listOutputs = flistGradeOutputs(
        listRecords, dictSections, listExpected, bGrade)
    sVerdict, sVerdictRendered = ftDecideVerdict(
        dictGrading, dictIntegrity, listOutputs, listStepLevel)
    dictTally = fdictBuildTally(listOutputs, listStepLevel)
    return {
        "iSchemaVersion": I_ASSESSMENT_REPORT_SCHEMA_VERSION,
        "sComparisonPolicy": S_COMPARISON_POLICY,
        "sComparisonPolicySource": dictGrading["sComparisonPolicySource"],
        "sManifestState": dictGrading["sManifestState"],
        "sLedgerState": dictGrading["sLedgerState"],
        "dictIntegrity": dictIntegrity,
        "listInvocations": list(dictInvocations.values()),
        "listStandardSets": list(dictGrading["listStandardSets"]),
        "listOutputs": listOutputs,
        "listStepLevelChecks": listStepLevel,
        "dictContexts": dict(dictGrading["dictContexts"]),
        "dictTally": dictTally,
        "sVerdict": sVerdict,
        "sVerdictRendered": sVerdictRendered,
        "sTallyRendered": fsRenderTally(dictTally),
        "iExitCode": DICT_VERDICT_EXIT_CODES[sVerdict],
    }


def fdictExtractGradingObservations(dictReport):
    """Return the observations a report carries, in grading's shape."""
    return {
        "sComparisonPolicySource": dictReport["sComparisonPolicySource"],
        "sManifestState": dictReport["sManifestState"],
        "sLedgerState": dictReport["sLedgerState"],
        "dictIntegrity": dict(dictReport["dictIntegrity"]),
        "dictInvocations": {
            dictRecord["sInvocationId"]: {
                sKey: dictRecord[sKey]
                for sKey in TUPLE_INVOCATION_OBSERVATION_KEYS}
            for dictRecord in dictReport["listInvocations"]},
        "listStandardSets": list(dictReport["listStandardSets"]),
        "dictOutputs": {
            dictOutput["sPath"]: {
                sKey: dictOutput[sKey] for sKey in TUPLE_OUTPUT_OBSERVATION_KEYS}
            for dictOutput in dictReport["listOutputs"]},
        "dictContexts": dict(dictReport["dictContexts"]),
    }


def fdictRederiveReport(dictReport, dictPlan):
    """Return the report this policy grades from the report's own observations.

    Equal to ``dictReport`` exactly when every derived field -- each
    section state, check outcome, output outcome, the tally, the verdict,
    its wording and its exit code -- follows from what was observed.
    """
    return fdictGradeAssessment(
        dictPlan, fdictExtractGradingObservations(dictReport))
