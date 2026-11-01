"""The PROOF receipt document: schema version 1, built purely, one set of bytes.

Source: ``vaibify/reproducibility/proofReceipt.py`` (the builders and
the serializer) over ``vaibify/reproducibility/proofCatalog.py``.

The schema is the part that cannot change once archived, so the first
test builds the plan's example checkpoint receipt from a known
observation and compares the document exactly: every field, every
requirement, the sort order and the bytes' shape. The expected values
are spelled from the schema, not read back from the builder; only the
fixed ``sWhy`` sentences are taken from the catalog, whose text is data.
"""

import copy
import hashlib
import json

import pytest

from vaibify.reproducibility import proofCatalog, proofReceipt


S_SHA_OUT = "3f" * 32
S_SHA_PROJECT = "9a" * 32
S_HEAD = "c0ffee" + "0" * 34
S_VAIBIFY_COMMIT = "1a2b3c4d" * 5
S_SOURCE_DIGEST = "e4" * 32

_DICT_ALL_STATE_KEYS = {
    "bAtLeastLevel1": True, "bGithubFullySynced": True,
    "bZenodoFullySynced": True, "bGithubRepositoryPublic": True,
    "bZenodoArchivePublic": True, "bArxivFullySynced": True,
    "bAiDeclarationAttested": True, "bAiModelsDeclared": True,
    "bPersonalLayerDeclared": True, "bImageArchiveAnswered": True,
    "bPromptRecordCurrent": True, "bSupervisionClean": True,
    "bProjectContextFileExists": False, "bNoBasenameCollisions": True,
    "bOverleafFullySynced": True, "bAtLeastLevel2": True,
}


def _fdictRecord(sService):
    dictRecord = {
        "sService": sService, "sLastVerified": "2026-11-03T17:40:02Z",
        "iTotalFiles": 2, "iMatching": 2, "listDiverged": [],
        "listComparedPaths": ["Step/out.csv", "MANIFEST.sha256"],
        "iScopeVersion": 7,
        "dictComparedHashes": {"Step/out.csv": S_SHA_OUT,
                               "MANIFEST.sha256": "77" * 32},
    }
    if sService == "github":
        dictRecord.update(
            sCommittedShaVerified=S_HEAD,
            dictRemoteIdentity={"sOwner": "someone", "sRepo": "something",
                                "sBranch": "main"},
            dictPublicAccess={"sState": "public", "sReason": "served"})
    else:
        dictRecord.update(
            sLastVerified="2026-11-03T17:41:15Z",
            sZenodoDoi="10.5281/zenodo.101", sEndpointVerified="zenodo",
            bComparisonWithheld=False,
            dictRemoteIdentity={"sEndpoint": "zenodo", "listRecordIds": ["101"]},
            dictPublicAccess={"sState": "public", "sReason": "served",
                              "listRecords": [{
                                  "sRecordId": "101", "sState": "public",
                                  "sReason": "Zenodo serves record 101's "
                                             "files to readers."}]})
    return dictRecord


def fdictKnownObservation():
    """An observation exactly as the capture shapes one: Level 2 met."""
    return {
        "sEvaluatedAtUtc": "2026-11-03T17:42:10Z",
        "sInputs": "one-snapshot", "sInputsReason": "", "bCoherent": True,
        "listChangedReads": [], "iPublicationScopeVersion": 7,
        "iCriteriaVersion": 1, "fMaxStaleHours": 24.0,
        "dictVerdict": {"bAtLeastLevel1": True, "bAtLeastLevel2": True},
        "dictVaibify": {
            "bModified": False, "bSourceChangedSinceStart": False,
            "sCommit": S_VAIBIFY_COMMIT, "sDescribe": "v0.3.0-0-g1a2b3c4d",
            "sMeaning": "the code this process started with",
            "sSource": "checkout", "sSourceTreeSha256": S_SOURCE_DIGEST},
        "dictWorkflowIdentity": {
            "sName": "Example Project",
            "sDefinitionPath": ".vaibify/projects/project.json",
            "sDefinitionSha256": S_SHA_PROJECT, "sRepositoryHead": S_HEAD},
        "dictGaps": dict(_DICT_ALL_STATE_KEYS, dictPublicAccessStates={
            "github": {"sState": "public", "sReason":
                       "GitHub serves someone/something to readers without "
                       "a login."},
            "zenodo": {"sState": "public", "sReason":
                       "Zenodo serves record 101's files to readers."}},
            dictPublishedCopyStates={
            "github": {"sState": "matched", "sReason":
                       "Every Level 2 file matches someone/something on main."},
            "zenodo": {"sState": "matched", "sReason":
                       "Every Level 2 file matches a declared record."}}),
        "listLevel2Blockers": [],
        "dictHeaderCell": {"sState": "attained"},
        "bMarkersHoldLevelZero": False,
        "dictRecords": {"github": _fdictRecord("github"),
                        "zenodo": _fdictRecord("zenodo"), "overleaf": None},
        "dictRecordFresh": {"github": True, "zenodo": True, "overleaf": False},
        "dictApplicability": {"always": True, "overleaf-bound": False,
                              "arxiv-recorded": False, "image-pinned": False},
        "dictManuscript": {"sLastPushCommit": "", "listPushedFigures": [],
                           "sArxivId": "", "sRecordedVersion": "",
                           "dictLocalFigureHashes": {}, "listArxivAnswers": []},
    }


def fdictKnownArchive():
    return {
        "sEndpoint": "zenodo", "sConceptDoi": "10.5281/zenodo.100",
        "sFollowsRecordId": "101",
        "listContents": [
            {"sKey": "project.json", "sRepoPath": ".vaibify/projects/project.json",
             "sSha256": S_SHA_PROJECT, "sMd5": "77" * 16, "iSizeBytes": 3511},
            {"sKey": "out.csv", "sRepoPath": "Step/out.csv",
             "sSha256": S_SHA_OUT, "sMd5": "5e" * 16, "iSizeBytes": 2048},
        ],
    }


def _fdictWhy(sId):
    return next(d["sWhy"] for d in proofCatalog.T_LEVEL2_CATALOG
                if d["sId"] == sId)


def _fdictRequirement(sId, sStatus, sApplicability, bApplies, sState,
                      sEvidence=""):
    return {"iLevel": 2, "sId": sId, "sStatus": sStatus,
            "sApplicability": sApplicability, "bApplies": bApplies,
            "sState": sState, "sEvidence": sEvidence, "sWhy": _fdictWhy(sId)}


def _flistExpectedRequirements():
    sGithubAccess = "GitHub serves someone/something to readers without a login."
    return sorted([
        _fdictRequirement("ai-declaration-attested", "required", "always",
                          True, "met"),
        _fdictRequirement("ai-models-declared", "required", "always", True,
                          "met"),
        _fdictRequirement("arxiv-matches", "required-when", "arxiv-recorded",
                          False, "not-applicable"),
        _fdictRequirement("committed-uncompared-files", "not-required",
                          "always", True, "not-evaluated"),
        _fdictRequirement("compared-not-gating-files", "not-required",
                          "always", True, "not-evaluated"),
        _fdictRequirement("github-copy-matches", "required", "always", True,
                          "met", "Every Level 2 file matches someone/something "
                          "on main."),
        _fdictRequirement("github-repository-public", "required", "always",
                          True, "met", sGithubAccess),
        _fdictRequirement("image-archive-answered", "required-when",
                          "image-pinned", False, "not-applicable"),
        _fdictRequirement("level-one", "required", "always", True, "met"),
        _fdictRequirement("overleaf-figures-match", "required-when",
                          "overleaf-bound", False, "not-applicable"),
        _fdictRequirement("personal-layer-answered", "required", "always",
                          True, "met"),
        _fdictRequirement("project-context-file", "not-required", "always",
                          True, "not-evaluated"),
        _fdictRequirement("prompt-record", "not-required", "always", True,
                          "not-evaluated"),
        _fdictRequirement("reproducibility-envelope", "not-required", "always",
                          True, "not-evaluated"),
        _fdictRequirement("supervised-mode", "not-required", "always", True,
                          "not-evaluated"),
        _fdictRequirement("unique-basenames", "required", "always", True,
                          "met"),
        _fdictRequirement("zenodo-archive-public", "required", "always", True,
                          "met", "Zenodo serves record 101's files to readers."),
        _fdictRequirement("zenodo-copy-matches", "required", "always", True,
                          "met", "Every Level 2 file matches a declared record."),
    ], key=lambda dictRow: dictRow["sId"])


def fdictExpectedCheckpointReceipt():
    """The plan's example checkpoint receipt, spelled from the schema."""
    return {
        "dictArchive": {
            "listContents": [
                {"iSizeBytes": 2048, "sKey": "out.csv", "sMd5": "5e" * 16,
                 "sRepoPath": "Step/out.csv", "sSha256": S_SHA_OUT},
                {"iSizeBytes": 3511, "sKey": "project.json", "sMd5": "77" * 16,
                 "sRepoPath": ".vaibify/projects/project.json",
                 "sSha256": S_SHA_PROJECT},
            ],
            "sConceptDoi": "10.5281/zenodo.100", "sEndpoint": "zenodo",
            "sFollowsRecordId": "101",
        },
        "dictCompletes": None,
        "dictEvidence": {
            "github": {
                "dictComparison": {"bWithheld": False, "listOutcomes": [
                    {"sExpectedSha256": S_SHA_OUT, "sObservedSha256": S_SHA_OUT,
                     "sOutcome": "matched", "sPath": "Step/out.csv"}]},
                "dictPublicAccess": {
                    "sReason": "GitHub serves someone/something to readers "
                               "without a login.", "sState": "public"},
                "dictPublishedCopy": {
                    "sReason": "Every Level 2 file matches someone/something "
                               "on main.", "sState": "matched"},
                "dictRemote": {"sBranch": "main", "sOwner": "someone",
                               "sRepo": "something"},
                "sCheckedAtUtc": "2026-11-03T17:40:02Z",
                "sCommittedShaVerified": S_HEAD,
            },
            "zenodo": {
                "dictComparison": {"bWithheld": False, "listOutcomes": [
                    {"sExpectedSha256": S_SHA_OUT, "sMatchedInRecordId": "101",
                     "sObservedSha256": S_SHA_OUT, "sOutcome": "matched",
                     "sPath": "Step/out.csv"}]},
                "dictPublicAccess": {
                    "listRecords": [{
                        "sReason": "Zenodo serves record 101's files to "
                                   "readers.",
                        "sRecordId": "101", "sState": "public"}],
                    "sReason": "Zenodo serves record 101's files to readers.",
                    "sState": "public"},
                "dictPublishedCopy": {
                    "sReason": "Every Level 2 file matches a declared record.",
                    "sState": "matched"},
                "dictRemote": {"listRecordIds": ["101"], "sEndpoint": "zenodo"},
                "sCheckedAtUtc": "2026-11-03T17:41:15Z",
                "sDoi": "10.5281/zenodo.101",
            },
        },
        "dictObservation": {
            "bCoherent": True, "fMaxStaleHours": 24.0, "iCriteriaVersion": 1,
            "iPublicationScopeVersion": 7, "listChangedReads": [],
            "sEvaluatedAtUtc": "2026-11-03T17:42:10Z",
            "sInputs": "one-snapshot", "sInputsReason": "",
        },
        "dictVaibify": {
            "bModified": False, "bSourceChangedSinceStart": False,
            "sCommit": S_VAIBIFY_COMMIT, "sDescribe": "v0.3.0-0-g1a2b3c4d",
            "sMeaning": "the code this process started with",
            "sSource": "checkout", "sSourceTreeSha256": S_SOURCE_DIGEST,
        },
        "dictVerdict": {"bAtLeastLevel1": True, "bAtLeastLevel2": True},
        "dictWorkflow": {
            "sDefinitionPath": ".vaibify/projects/project.json",
            "sDefinitionSha256": S_SHA_PROJECT, "sName": "Example Project",
            "sRepositoryHead": S_HEAD,
        },
        "iSchemaVersion": 1,
        "listRequirements": _flistExpectedRequirements(),
        "sCircumstance": (
            "Level 2 was checked against the previously declared records "
            "(see dictEvidence.zenodo). This version is what the next verify "
            "compares the files with."),
        "sKind": "checkpoint",
        "sWhatThisRecords": (
            "vaibify's Level 2 verdicts at the moment in "
            "dictObservation.sEvaluatedAtUtc. A record of what was observed, "
            "not a claim about now."),
    }


def _sSerialize(dictObservation, sKind="checkpoint", dictArchive=None,
                dictCompletes=None):
    return proofReceipt.fsSerializeProofReceipt(
        proofReceipt.fdictBuildProofReceipt(
            dictObservation, sKind,
            dictArchive if dictArchive is not None else fdictKnownArchive(),
            dictCompletes))


def testTheExampleCheckpointReceiptIsBuiltExactly():
    sText = _sSerialize(fdictKnownObservation())
    assert json.loads(sText) == fdictExpectedCheckpointReceipt()
    assert sText.endswith("}\n") and sText.startswith('{\n  "dictArchive"')
    assert sText == json.dumps(json.loads(sText), indent=2, sort_keys=True) + (
        "\n")
    listIds = [d["sId"] for d in json.loads(sText)["listRequirements"]]
    assert listIds == sorted(listIds)


@pytest.mark.falsification
def testTwoBuildsFromOneObservationAreByteIdentical():
    """One observation, one set of bytes, however often it is built.

    Kills: the builder reads the clock.
    """
    dictObservation = fdictKnownObservation()
    assert _sSerialize(dictObservation) == _sSerialize(
        copy.deepcopy(dictObservation))


def testShuffledInputsSerializeToTheSameBytes():
    dictObservation = fdictKnownObservation()
    dictArchive = fdictKnownArchive()
    dictReversed = copy.deepcopy(dictArchive)
    dictReversed["listContents"].reverse()
    assert _sSerialize(dictObservation, dictArchive=dictArchive) == (
        _sSerialize(dictObservation, dictArchive=dictReversed))


@pytest.mark.falsification
def testARequirementStateIsTheGatesNotTheRecords():
    """A gaps answer the record could not produce still decides the state.

    The record says GitHub is public; the gate (stubbed) says not. The
    receipt states what the gate decided, with the gate's reason.

    Kills: the builder recomputes access from the verify record.
    """
    dictObservation = fdictKnownObservation()
    dictObservation["dictGaps"]["dictPublicAccessStates"]["github"] = {
        "sState": "not-public", "sReason": "the gate said so"}
    dictReceipt = json.loads(_sSerialize(dictObservation))
    dictRow = next(d for d in dictReceipt["listRequirements"]
                   if d["sId"] == "github-repository-public")
    assert (dictRow["sState"], dictRow["sEvidence"]) == (
        "not-met", "the gate said so")


@pytest.mark.falsification
def testNotRequiredEntriesAppearWithTheirReasons():
    """The receipt states what Level 2 does NOT require, too.

    Kills: the builder filters the requirements to required ones.
    """
    dictReceipt = json.loads(_sSerialize(fdictKnownObservation()))
    dictNotRequired = {d["sId"]: d for d in dictReceipt["listRequirements"]
                       if d["sStatus"] == "not-required"}
    assert set(dictNotRequired) == {
        "project-context-file", "prompt-record", "supervised-mode",
        "compared-not-gating-files", "committed-uncompared-files",
        "reproducibility-envelope"}
    assert all(d["sWhy"] for d in dictNotRequired.values())


@pytest.mark.falsification
def testTheEvidenceRemotesAndTheReceivingDepositAreKeptApart():
    """The deposit receiving the receipt is not the record evidence is about.

    Kills: dictArchive filled from the verify record.
    """
    dictReceipt = json.loads(_sSerialize(fdictKnownObservation()))
    assert dictReceipt["dictArchive"]["sConceptDoi"] == "10.5281/zenodo.100"
    assert dictReceipt["dictArchive"]["sFollowsRecordId"] == "101"
    assert dictReceipt["dictEvidence"]["zenodo"]["sDoi"] == "10.5281/zenodo.101"


def testAFirstArchiveSaysSo():
    dictArchive = fdictKnownArchive()
    dictArchive["sFollowsRecordId"] = ""
    dictReceipt = json.loads(_sSerialize(
        fdictKnownObservation(), dictArchive=dictArchive))
    assert dictReceipt["sCircumstance"] == (
        proofReceipt.S_CIRCUMSTANCE_CHECKPOINT_FIRST)


def _fdictOverleafObservation():
    dictObservation = fdictKnownObservation()
    dictObservation["dictApplicability"]["overleaf-bound"] = True
    dictObservation["dictRecords"]["overleaf"] = {
        "sLastVerified": "2026-11-03T17:39:00Z", "iScopeVersion": 7,
        "iTotalFiles": 2, "iMatching": 1, "listComparedPaths": [
            "Step/a.pdf", "Step/b.pdf"],
        "listDiverged": [{"sPath": "Step/b.pdf", "sExpected": "1", "sActual": "2"}],
    }
    dictObservation["dictRecordFresh"]["overleaf"] = True
    dictObservation["dictManuscript"].update(
        sLastPushCommit=S_HEAD, listPushedFigures=["Step/a.pdf"])
    dictObservation["listLevel2Blockers"] = [{
        "iLevel": 2, "iStepIndex": 0, "sStepLabel": "A01", "sScope": "step",
        "sCriterion": "figure-not-frozen", "listOffendingFiles": ["Step/b.pdf"],
        "listOffendingUpstreamSteps": [], "sRemediationHint": "push"}]
    return dictObservation


@pytest.mark.falsification
def testAnOverleafBoundReceiptCarriesOverleafsEvidenceAndNoProjectId():
    """Overleaf's evidence has its own shape: vaibify's comparison only.

    Kills: the Overleaf entry given the authorities' shape.
    """
    dictReceipt = json.loads(_sSerialize(_fdictOverleafObservation()))
    dictOverleaf = dictReceipt["dictEvidence"]["overleaf"]
    assert set(dictOverleaf) == {
        "sCheckedAtUtc", "bFresh", "bScopeCurrent", "iMatching",
        "iTotalFiles", "listDivergedPaths", "sLastPushCommit",
        "listPushedFigures", "listNotFrozen"}
    assert dictOverleaf["listDivergedPaths"] == ["Step/b.pdf"]
    assert dictOverleaf["listNotFrozen"] == [
        {"sStepLabel": "A01", "listFigures": ["Step/b.pdf"]}]
    assert "sProjectId" not in json.dumps(dictReceipt)


def testAnArxivReceiptKeepsAFailedAskAsAnError():
    dictObservation = fdictKnownObservation()
    dictObservation["dictApplicability"]["arxiv-recorded"] = True
    dictObservation["dictManuscript"].update(
        sArxivId="2401.00001", sRecordedVersion="v1", sLastPushCommit=S_HEAD,
        listPushedFigures=["Step/a.pdf"],
        dictLocalFigureHashes={"Step/a.pdf": "aa" * 32},
        listArxivAnswers=[
            {"sQuestion": "latest-version", "genericAnswer": None,
             "sError": "arXiv unreachable"},
            {"sQuestion": "remote-hashes",
             "genericAnswer": {"Step/a.pdf": "aa" * 32}, "sError": ""}])
    dictArxiv = json.loads(_sSerialize(dictObservation))["dictEvidence"]["arxiv"]
    assert dictArxiv["dictLatestVersion"] == {
        "sAnswer": "", "sError": "arXiv unreachable"}
    assert dictArxiv["bVersionCurrent"] is False
    assert dictArxiv["dictFigureComparison"]["listOutcomes"] == [{
        "sPath": "Step/a.pdf", "sExpectedSha256": "aa" * 32,
        "sObservedSha256": "aa" * 32, "sOutcome": "matched"}]


@pytest.mark.falsification
def testACompletionNamesTheRecordSetAndWhatTheCheckpointAloneLacks():
    """Level 2 was met against the declared records; the checkpoint may lack some.

    Kills: listCheckpointLacks dropped from the completion's claim.
    """
    dictObservation = fdictKnownObservation()
    listInventory = [{"sKey": "project.json",
                      "sRepoPath": ".vaibify/projects/project.json",
                      "sSha256": S_SHA_PROJECT}]
    dictCompletes = proofReceipt.fdictBuildCompletes(
        dictObservation, "102", "10.5281/zenodo.102",
        hashlib.sha256(b"receipt").hexdigest(), listInventory)
    assert dictCompletes["listCheckpointLacks"] == ["Step/out.csv"]
    assert dictCompletes["bCheckpointAloneSuffices"] is False
    dictReceipt = json.loads(_sSerialize(
        dictObservation, "completion", dictCompletes=dictCompletes))
    assert dictReceipt["sCircumstance"] == (
        proofReceipt.S_CIRCUMSTANCE_COMPLETION_TOGETHER)
    assert dictReceipt["dictCompletes"]["sCheckpointRecordId"] == "102"


def testACheckpointHoldingEveryFileSufficesAlone():
    dictObservation = fdictKnownObservation()
    dictCompletes = proofReceipt.fdictBuildCompletes(
        dictObservation, "102", "10.5281/zenodo.102", "ab" * 32,
        fdictKnownArchive()["listContents"])
    assert dictCompletes["bCheckpointAloneSuffices"] is True
    assert json.loads(_sSerialize(
        dictObservation, "completion", dictCompletes=dictCompletes))[
        "sCircumstance"] == proofReceipt.S_CIRCUMSTANCE_COMPLETION_ALONE
