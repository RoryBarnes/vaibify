"""The Level 2 catalog agrees with every copy of what Level 2 requires.

Source: ``vaibify/reproducibility/proofCatalog.py``, held against
``vaibify/reproducibility/levelGates.py`` (the gaps dict, the header
tuples, the gate and the blockers), ``vaibify/gui/static/scriptProofTab.js``
(the PROOF tab's rows) and ``vaibify/gui/static/scriptApplication.js``
(the blocker glyphs).

Structural tests fail when a copy names a key, criterion or row the
catalog does not. The behavioral matrix drives real repositories into
each row's failure and asserts that the gate's verdict, the gaps keys,
the blockers, the header cell and the derived requirements all tell
the same story. Real failures come together -- a restricted archive
also withholds the comparison -- so each row states the COMBINATION it
expects, never one failing entry; entries a row does not name read met.
"""

import ast
import json
import os
import re
from datetime import datetime, timedelta, timezone

import pytest

from tests.syncStatusFixtures import (
    fdictBuildCachedVerify,
    fdictRemoteIdentityVerifiedFor,
)
from vaibify.reproducibility import levelGates, proofCatalog, proofReceipt
from vaibify.reproducibility.aiDeclarationStep import (
    S_AI_DECLARATION_STEP_KIND,
)


S_CONTAINER_ID = "catalogProject"
_PATH_REPOSITORY = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_S_PROOF_TAB = os.path.join(
    _PATH_REPOSITORY, "vaibify", "gui", "static", "scriptProofTab.js")


def _fdictEntryById(sId):
    return next(d for d in proofCatalog.T_LEVEL2_CATALOG if d["sId"] == sId)


# ── Structural agreement ─────────────────────────────────────────────


def testEveryGapsKeyIsACatalogEntryAndBack(tmp_path):
    dictGaps = levelGates.fdictLevel2Gaps({"listSteps": []}, str(tmp_path))
    setGapsKeys = set(dictGaps) - {
        "bAtLeastLevel2", "dictPublicAccessStates", "dictPublishedCopyStates"}
    setCatalogKeys = {d["sGapsKey"] for d in proofCatalog.T_LEVEL2_CATALOG
                      if d["sGapsKey"]}
    assert setGapsKeys == setCatalogKeys


def testTheRequiredWorkflowCriteriaAreTheHeaderTuples():
    """What the header counts is what the catalog requires, at workflow scope."""
    setCatalog = {
        sCriterion
        for dictEntry in proofCatalog.T_LEVEL2_CATALOG
        if dictEntry["sStatus"] != proofCatalog.S_STATUS_NOT_REQUIRED
        for sCriterion, sScope in dictEntry["tCriteria"]
        if sScope == "workflow"
    } - {"missing-ai-declaration-step"}
    setHeader = set(
        levelGates._T_WORKFLOW_LEVEL2_BASE_CRITERIA
        + levelGates._T_WORKFLOW_LEVEL2_OVERLEAF_CRITERIA
        + levelGates._T_WORKFLOW_LEVEL2_ARXIV_CRITERIA
        + (levelGates.S_BASENAME_COLLISION_CRITERION,))
    assert setCatalog == setHeader


def _flistProofTabLevelRows(iLevel):
    """Return ``[(sStateKey, bOptional)]`` for one level of the PROOF tab."""
    sSource = open(_S_PROOF_TAB, encoding="utf-8").read()
    iCatalog = sSource.index("_DICT_REQUIREMENT_CATALOG")
    iStart = sSource.index(f"\n        {iLevel}: [", iCatalog)
    iEnd = sSource.index("\n        ],", iStart)
    listRows = []
    for sRow in re.split(r"\n            \{", sSource[iStart:iEnd])[1:]:
        matchKey = re.search(r'sStateKey:\s*"([A-Za-z0-9]+)"', sRow)
        listRows.append((matchKey.group(1),
                         bool(re.search(r"bOptional:\s*true", sRow))))
    return listRows


def testTheProofTabsLevelTwoRowsAreTheCatalogsRows():
    listRows = _flistProofTabLevelRows(2)
    assert {sKey for sKey, _b in listRows} == {
        d["sProofRowKey"] for d in proofCatalog.T_LEVEL2_CATALOG
        if d["sProofRowKey"]}
    for sKey, bOptional in listRows:
        dictEntry = next(d for d in proofCatalog.T_LEVEL2_CATALOG
                         if d["sProofRowKey"] == sKey)
        assert bOptional == (
            dictEntry["sStatus"] == proofCatalog.S_STATUS_NOT_REQUIRED), sKey


def testEveryCatalogCriterionHasAGlyph():
    from tests.testStepRendererBlockerGlyphs import _fsetAllJsBlockerCriteria
    setCriteria = {sCriterion for d in proofCatalog.T_LEVEL2_CATALOG
                   for sCriterion, _sScope in d["tCriteria"]}
    assert setCriteria - _fsetAllJsBlockerCriteria() == set()


def testEveryUnknownCriterionChargesItsOwnEntry():
    for dictEntry in proofCatalog.T_LEVEL2_CATALOG:
        setCriteria = {sCriterion for sCriterion, _s in dictEntry["tCriteria"]}
        assert set(dictEntry["tUnknownCriteria"]) <= setCriteria


def testTheCatalogIsALeafModule():
    treeModule = ast.parse(open(proofCatalog.__file__).read())
    for nodeImport in ast.walk(treeModule):
        if isinstance(nodeImport, ast.ImportFrom):
            assert nodeImport.level == 0 and not (
                nodeImport.module or "").startswith("vaibify")
        if isinstance(nodeImport, ast.Import):
            assert not any(nodeAlias.name.startswith("vaibify")
                           for nodeAlias in nodeImport.names)


# ── The authority translation ───────────────────────────────────────


@pytest.mark.falsification
def testAnAuthorityAnswerOutsideTheTableRaises():
    """A verdict the catalog cannot place is an error, never a default.

    Kills: an unknown authority answer defaulted to met.
    """
    with pytest.raises(ValueError):
        proofCatalog.fsTranslateAuthorityState("probably-public")
    with pytest.raises(ValueError):
        proofCatalog.fsTranslateAuthorityState(None)


@pytest.mark.falsification
def testDiffersBecomesNotMetAndSurvivesVerbatimAsEvidence():
    """The requirement speaks requirement states; the evidence keeps the answer.

    Kills: the authority's answer passed through untranslated.
    """
    dictGaps = {
        "dictPublicAccessStates": {
            "github": {"sState": "public", "sReason": "served"},
            "zenodo": {"sState": "public", "sReason": "served"}},
        "dictPublishedCopyStates": {
            "github": {"sState": "differs", "sReason": "1 file differs"},
            "zenodo": {"sState": "matched", "sReason": "all match"}},
    }
    dictRow = proofCatalog._fdictDeriveOneRequirement(
        _fdictEntryById("github-copy-matches"), dictGaps, [],
        {"always": True})
    assert dictRow["sState"] == "not-met"
    assert dictRow["sEvidence"] == "1 file differs"


# ── The expected-state matrix ───────────────────────────────────────


def _fsIsoHoursAgo(fHoursAgo):
    return (datetime.now(timezone.utc) - timedelta(hours=fHoursAgo)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def _fdictGreenStep(sDirectory, sStepKind=None, listOutputs=()):
    dictStep = {
        "sName": sDirectory, "sDirectory": sDirectory, "bNoInputData": True,
        "saOutputDataFiles": list(listOutputs), "saPlotFiles": [],
        "dictVerification": {sKey: "passed" for sKey in (
            "sUser", "sUnitTest", "sIntegrity", "sQualitative",
            "sQuantitative")},
    }
    if sStepKind:
        dictStep["sStepKind"] = sStepKind
    return dictStep


def _fdictReadyWorkflow(sRepo):
    """A workflow that attains Level 2 once both records are green."""
    return {
        "sWorkflowName": "Catalog Project", "sProjectRepoPath": sRepo,
        "listSteps": [
            _fdictGreenStep("A"),
            _fdictGreenStep("A", sStepKind=S_AI_DECLARATION_STEP_KIND),
        ],
        "dictRemotes": {
            "github": {"sOwner": "someone", "sRepo": "something",
                       "sBranch": "main", "sCommittedSha": "abc123"},
            "zenodo": {"sRecordId": "101", "sService": "sandbox",
                       "sDoi": "10.5281/zenodo.101"},
        },
        "dictAiProvenance": {
            "listDeclaredModels": [{
                "sVendor": "ExampleVendor", "sModelId": "example-model-1",
                "sUseStartDate": "2026-01-01", "sUseEndDate": "2026-02-01"}],
            "dictPersonalLayer": {"sStatus": "none"},
        },
    }


def _fdictRecord(dictWorkflow, sService, **dictOverrides):
    dictFields = {
        "sService": sService,
        "dictRemoteIdentity": fdictRemoteIdentityVerifiedFor(
            dictWorkflow, sService),
        "sLastVerified": _fsIsoHoursAgo(1.0),
    }
    if sService == "github":
        dictFields["sCommittedShaVerified"] = "abc123"
    else:
        dictFields.update(sZenodoDoi="10.5281/zenodo.101",
                          sEndpointVerified="sandbox")
    dictFields.update(dictOverrides)
    return fdictBuildCachedVerify(**dictFields)


def _fnWriteJson(sPath, dictPayload):
    os.makedirs(os.path.dirname(sPath), exist_ok=True)
    with open(sPath, "w", encoding="utf-8") as fileJson:
        json.dump(dictPayload, fileJson)


def _fnWriteRecords(sRepo, dictWorkflow, dictGithub=None, dictZenodo=None):
    dictAll = {
        "github": _fdictRecord(dictWorkflow, "github", **(dictGithub or {})),
        "zenodo": _fdictRecord(dictWorkflow, "zenodo", **(dictZenodo or {})),
    }
    _fnWriteJson(sRepo + "/.vaibify/syncStatus.json", dictAll)


def _fnRowEverythingPasses(sRepo, dictWorkflow, monkeypatch):
    _fnWriteRecords(sRepo, dictWorkflow)


def _fnRowGithubPrivate(sRepo, dictWorkflow, monkeypatch):
    _fnWriteRecords(sRepo, dictWorkflow, dictGithub={
        "sPublicAccess": "not-public"})


def _fnRowGithubOtherRemote(sRepo, dictWorkflow, monkeypatch):
    _fnWriteRecords(sRepo, dictWorkflow, dictGithub={"dictRemoteIdentity": {
        "sOwner": "someone", "sRepo": "elsewhere", "sBranch": "main"}})


def _fnRowGithubDiverged(sRepo, dictWorkflow, monkeypatch):
    _fnWriteRecords(sRepo, dictWorkflow, dictGithub={
        "listDivergedPaths": ["step01/data.csv"]})


def _fnRowZenodoRestricted(sRepo, dictWorkflow, monkeypatch):
    _fnWriteRecords(sRepo, dictWorkflow, dictZenodo={
        "sPublicAccess": "not-public", "listComparedPaths": [],
        "bComparisonWithheld": True})


def _fnRowZenodoStale(sRepo, dictWorkflow, monkeypatch):
    _fnWriteRecords(sRepo, dictWorkflow, dictZenodo={
        "sLastVerified": _fsIsoHoursAgo(48.0)})


def _fnRowZenodoPredatesReaders(sRepo, dictWorkflow, monkeypatch):
    _fnWriteRecords(sRepo, dictWorkflow, dictZenodo={"sPublicAccess": None})


def _fnRowDeclarationUncheckable(sRepo, dictWorkflow, monkeypatch):
    from vaibify.reproducibility.declarationFreshness import (
        S_FRESHNESS_UNKNOWN,
    )
    _fnWriteRecords(sRepo, dictWorkflow)
    monkeypatch.setattr(
        levelGates, "fdictEvaluateDeclarationFreshness",
        lambda _w, _f: {"sVerdict": S_FRESHNESS_UNKNOWN, "listChanges": [],
                        "sReason": "A covered file could not be read."})


def _fnRowDeclarationUnattested(sRepo, dictWorkflow, monkeypatch):
    _fnWriteRecords(sRepo, dictWorkflow)
    dictWorkflow["listSteps"][1]["dictVerification"]["sUser"] = "untested"


def _fnRowModelsUndeclared(sRepo, dictWorkflow, monkeypatch):
    _fnWriteRecords(sRepo, dictWorkflow)
    dictWorkflow["dictAiProvenance"]["listDeclaredModels"] = []


def _fnRowImageUnanswered(sRepo, dictWorkflow, monkeypatch):
    _fnWriteRecords(sRepo, dictWorkflow)
    _fnWriteJson(sRepo + "/.vaibify/environment.json", {"dictContainer": {
        "sImageDigest": "sha256:" + "1" * 64, "sArchitecture": "amd64"}})


def _fnRowNoImagePinned(sRepo, dictWorkflow, monkeypatch):
    _fnWriteRecords(sRepo, dictWorkflow)
    _fnWriteJson(sRepo + "/.vaibify/environment.json", {"sMode": "host"})


def _fnRowOverleafStale(sRepo, dictWorkflow, monkeypatch):
    _fnWriteRecords(sRepo, dictWorkflow)
    dictWorkflow["dictRemotes"]["overleaf"] = {
        "sProjectId": "0123456789abcdef01234567",
        "sLastPushCommit": "c0ffee" + "0" * 34}


def _fnRowBasenameCollision(sRepo, dictWorkflow, monkeypatch):
    _fnWriteRecords(sRepo, dictWorkflow)
    dictWorkflow["listSteps"][0]["saOutputDataFiles"] = ["out.csv"]
    dictWorkflow["listSteps"].append(_fdictGreenStep(
        "B", listOutputs=["out.csv"]))
    for sDirectory in ("A", "B"):
        os.makedirs(os.path.join(sRepo, sDirectory), exist_ok=True)
        with open(os.path.join(sRepo, sDirectory, "out.csv"), "w") as fileOut:
            fileOut.write("x\n")


# (row id, fixture, {entry: state} beyond the defaults, gate attained,
#  header attained, criteria the blockers charge). The header counts
#  workflow-scope criteria only, so a step-scope charge leaves it
#  attained beneath a refused gate: that is its documented scope.
LIST_MATRIX = [
    ("everything-passes", _fnRowEverythingPasses, {}, True, True, set()),
    ("github-private-files-match", _fnRowGithubPrivate,
     {"github-repository-public": "not-met"}, False, False,
     {"github-repository-not-public"}),
    ("github-record-about-another-remote", _fnRowGithubOtherRemote,
     {"github-copy-matches": "unknown",
      "github-repository-public": "unknown"}, False, False,
     {"github-verify-stale"}),
    ("github-file-diverged", _fnRowGithubDiverged,
     {"github-copy-matches": "not-met"}, False, False,
     {"not-in-github-mirror"}),
    ("zenodo-record-restricted", _fnRowZenodoRestricted,
     {"zenodo-archive-public": "not-met",
      "zenodo-copy-matches": "unknown"}, False, False,
     {"zenodo-archive-not-public"}),
    ("zenodo-record-stale", _fnRowZenodoStale,
     {"zenodo-copy-matches": "unknown",
      "zenodo-archive-public": "unknown"}, False, False,
     {"zenodo-verify-stale"}),
    ("zenodo-record-predates-the-reader-question",
     _fnRowZenodoPredatesReaders,
     {"zenodo-archive-public": "unknown"}, False, False,
     {"zenodo-verify-stale"}),
    ("declaration-freshness-uncheckable", _fnRowDeclarationUncheckable,
     {"ai-declaration-attested": "unknown"}, False, True,
     {"ai-declaration-uncheckable"}),
    ("declaration-unattested", _fnRowDeclarationUnattested,
     {"ai-declaration-attested": "not-met"}, False, True,
     {"ai-declaration-unattested"}),
    ("models-undeclared", _fnRowModelsUndeclared,
     {"ai-models-declared": "not-met"}, False, False,
     {"ai-models-undeclared"}),
    ("image-pinned-question-unanswered", _fnRowImageUnanswered,
     {"image-archive-answered": "not-met"}, False, False,
     {"image-archive-unanswered"}),
    ("no-image-pinned", _fnRowNoImagePinned, {}, True, True, set()),
    ("overleaf-bound-check-stale", _fnRowOverleafStale,
     {"overleaf-figures-match": "unknown"}, False, False,
     {"overleaf-verify-stale"}),
    ("two-published-files-share-a-basename", _fnRowBasenameCollision,
     {"unique-basenames": "not-met"}, False, True,
     {"basename-collision"}),
]


def _fdictDefaultStates():
    """Return each entry's state when a row names nothing about it.

    Independent of the code under test: a required entry reads met, a
    conditional one not applicable (no row binds Overleaf, records arXiv
    or pins an image unless it says so), a not-required one not
    evaluated.
    """
    dictByStatus = {
        proofCatalog.S_STATUS_REQUIRED: "met",
        proofCatalog.S_STATUS_REQUIRED_WHEN: "not-applicable",
        proofCatalog.S_STATUS_NOT_REQUIRED: "not-evaluated",
    }
    return {d["sId"]: dictByStatus[d["sStatus"]]
            for d in proofCatalog.T_LEVEL2_CATALOG}


def fdictObserveMatrixRow(tmp_path, monkeypatch, fnRow):
    """Build one row's repository and return its observation."""
    from vaibify.config import registryManager
    monkeypatch.setattr(registryManager, "fbIsHostProject", lambda _s: True)
    sRepo = os.path.realpath(str(tmp_path)) + "/project"
    os.makedirs(sRepo + "/.vaibify/projects")
    dictWorkflow = _fdictReadyWorkflow(sRepo)
    fnRow(sRepo, dictWorkflow, monkeypatch)
    levelGates.fnClearLevelBlockerCache()
    return proofReceipt.fdictCaptureObservation(
        {"files": None, "docker": None, "paths": {}},
        S_CONTAINER_ID, dictWorkflow)


def fnAssertMatrixRow(tmp_path, monkeypatch, sRowId):
    """Drive one matrix row and assert every surface tells its one story."""
    _sId, fnRow, dictExpected, bGate, bHeader, setCharged = next(
        tRow for tRow in LIST_MATRIX if tRow[0] == sRowId)
    dictObservation = fdictObserveMatrixRow(tmp_path, monkeypatch, fnRow)
    dictGaps = dictObservation["dictGaps"]
    dictStates = _fdictDefaultStates()
    dictStates.update(dictExpected)
    listDerived = proofCatalog.flistDeriveLevel2Requirements(
        dictGaps, dictObservation["listLevel2Blockers"],
        dictObservation["dictApplicability"])
    assert {d["sId"]: d["sState"] for d in listDerived} == dictStates
    assert dictObservation["dictVerdict"]["bAtLeastLevel2"] is bGate
    assert (dictObservation["dictHeaderCell"]["sState"] == "attained") is (
        bHeader)
    assert {d["sCriterion"] for d in dictObservation[
        "listLevel2Blockers"]} == setCharged
    for dictEntry in proofCatalog.T_LEVEL2_CATALOG:
        if dictEntry["sGapsKey"] and dictEntry["sStatus"] != (
                proofCatalog.S_STATUS_NOT_REQUIRED):
            assert dictGaps[dictEntry["sGapsKey"]] is (dictStates[
                dictEntry["sId"]] in ("met", "not-applicable")), dictEntry["sId"]
    return dictObservation


@pytest.mark.falsification
def testMatrixEverythingPasses(tmp_path, monkeypatch):
    """Every required entry met, conditional ones not applicable, attained.

    Kills: a conjunct that refuses a passing project (the gate's final
    return made False).
    """
    fnAssertMatrixRow(tmp_path, monkeypatch, "everything-passes")


@pytest.mark.falsification
def testMatrixGithubPrivateFilesMatch(tmp_path, monkeypatch):
    """A private repository with matching files is refused for privacy alone.

    Kills: the GitHub reader-access conjunct removed from the gate.
    """
    fnAssertMatrixRow(tmp_path, monkeypatch, "github-private-files-match")


@pytest.mark.falsification
def testMatrixGithubRecordAboutAnotherRemote(tmp_path, monkeypatch):
    """A record about another remote answers neither GitHub question.

    Kills: the remote-identity check dropped from the evidence gap.
    """
    fnAssertMatrixRow(
        tmp_path, monkeypatch, "github-record-about-another-remote")


@pytest.mark.falsification
def testMatrixGithubFileDiverged(tmp_path, monkeypatch):
    """A proven divergence is not-met and named, never merely stale.

    Kills: the unclaimed divergence blocker dropped.
    """
    fnAssertMatrixRow(tmp_path, monkeypatch, "github-file-diverged")


@pytest.mark.falsification
def testMatrixZenodoRecordRestricted(tmp_path, monkeypatch):
    """A restricted record is not public AND withholds the comparison.

    Kills: a withheld comparison mapped to differs instead of unknown.
    """
    fnAssertMatrixRow(tmp_path, monkeypatch, "zenodo-record-restricted")


@pytest.mark.falsification
def testMatrixZenodoRecordStale(tmp_path, monkeypatch):
    """A day-old record answers neither Zenodo question.

    Kills: staleness dropped from the evidence gap.
    """
    fnAssertMatrixRow(tmp_path, monkeypatch, "zenodo-record-stale")


@pytest.mark.falsification
def testMatrixZenodoRecordPredatesTheReaderQuestion(tmp_path, monkeypatch):
    """A record that never asked about readers keeps its copy, not its access.

    Kills: absent reader evidence read as public.
    """
    fnAssertMatrixRow(
        tmp_path, monkeypatch, "zenodo-record-predates-the-reader-question")


@pytest.mark.falsification
def testMatrixDeclarationFreshnessUncheckable(tmp_path, monkeypatch):
    """A declaration vaibify could not check is unknown, not failed.

    Kills: the declaration's unknown criterion dropped from the catalog.
    """
    fnAssertMatrixRow(
        tmp_path, monkeypatch, "declaration-freshness-uncheckable")


@pytest.mark.falsification
def testMatrixDeclarationUnattested(tmp_path, monkeypatch):
    """An unsigned declaration refuses Level 2 on its step's row.

    Kills: the AI Declaration conjunct removed from the gate.
    """
    fnAssertMatrixRow(tmp_path, monkeypatch, "declaration-unattested")


@pytest.mark.falsification
def testMatrixModelsUndeclared(tmp_path, monkeypatch):
    """Undeclared models refuse Level 2 and charge the header.

    Kills: the ai-models-undeclared blocker no longer emitted.
    """
    fnAssertMatrixRow(tmp_path, monkeypatch, "models-undeclared")


@pytest.mark.falsification
def testMatrixImagePinnedQuestionUnanswered(tmp_path, monkeypatch):
    """A pinned image with no archive answer is not met.

    Kills: the environment-archive gaps key no longer read from the gate.
    """
    fnAssertMatrixRow(
        tmp_path, monkeypatch, "image-pinned-question-unanswered")


@pytest.mark.falsification
def testMatrixNoImagePinned(tmp_path, monkeypatch):
    """With no pinned image the archive question does not apply.

    Kills: the image-pinned applicability read as always.
    """
    fnAssertMatrixRow(tmp_path, monkeypatch, "no-image-pinned")


@pytest.mark.falsification
def testMatrixOverleafBoundCheckStale(tmp_path, monkeypatch):
    """A bound Overleaf project with no fresh check is unknown and refused.

    Kills: the Overleaf conjunct removed from the gate.
    """
    fnAssertMatrixRow(tmp_path, monkeypatch, "overleaf-bound-check-stale")


@pytest.mark.falsification
def testMatrixTwoPublishedFilesShareABasename(tmp_path, monkeypatch):
    """Colliding basenames refuse Level 2 on the owning steps.

    Kills: the basename-collision conjunct removed from the gate.
    """
    fnAssertMatrixRow(
        tmp_path, monkeypatch, "two-published-files-share-a-basename")


def testEveryMatrixRowHasItsOwnTest():
    setTested = {sName for sName in globals() if sName.startswith(
        "testMatrix")}
    assert len(setTested) == len(LIST_MATRIX)
