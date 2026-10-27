"""One evidence contract, stated twice and held to agree.

A participant's evidence entry is checked in two places. Codex and
Antigravity are handed a strict JSON Schema their own CLI enforces
(``fdictComposeTurnResultJsonSchema``); Claude has no such channel, so
its entries meet only the engine's validator (``fdictValidateTurnResult``).
Before this contract was rewritten the two disagreed: the validator
accepted fields the schema forbids, and offered Claude a modified-state
claim carrying its own exit code and digest, which the ledger then
recorded as confirmed. A Claude participant could reach a "confirmed"
no other provider could.

So the table below is run through BOTH, and each row must get the same
verdict from each. The schema is judged by a real JSON Schema validator
(``jsonschema``), never by the engine's own code, and the composed
schema is itself checked against its metaschema so a malformed schema
cannot pass by rejecting everything.

Three layers are kept apart, because they are two different contracts:
what a participant may WRITE (this file's table), what the LEDGER
records (the store's tests at the bottom), and the replay that turns the
first into the second (commit-three tests).
"""

import copy
import io
import json
import re
import tarfile

import jsonschema
import pytest

from vaibify.gui import (
    agentCouncilAntigravityProvider,
    agentCouncilCampaign,
    agentCouncilCharter,
    agentCouncilCodexProvider,
    agentCouncilEvidenceContract,
)
from vaibify.gui.agentCouncilStore import CouncilEvidenceLedger

from tests.agentCouncilHarness import (
    fdictDecideCompleted,
    fdictMakeTurnResult,
    fixtureBuildCouncil,
)


def _fdictManifest(**dictOverrides):
    dictManifest = {
        "listModifiedFiles": [{"sPath": "src/model.py",
                               "sContent": "fTolerance = 1e-9\n"}],
        "listDeletedPaths": ["notes/old.txt"],
        "listChangedFileModes": [{"sPath": "run.sh", "sMode": "755"}],
        "listSymlinkTargets": [{"sPath": "latest", "sTarget": "run.sh"}],
    }
    dictManifest.update(dictOverrides)
    return dictManifest


def _fdictWeb(**dictOverrides):
    dictEntry = {
        "sStatus": "supportedBySourceInspection",
        "sStateForm": "webSource",
        "listSourceLinks": ["https://example.org/release-notes"],
        "sSearchQuery": "library release notes tolerance default",
    }
    dictEntry.update(dictOverrides)
    return dictEntry


def _fdictModified(**dictOverrides):
    dictEntry = {
        "sStatus": "confirmed", "sStateForm": "modifiedState",
        "sCommandText": "./run.sh", "dictChangeManifest": _fdictManifest(),
    }
    dictEntry.update(dictOverrides)
    return dictEntry


DICT_BASELINE = {"sStatus": "confirmed", "sStateForm": "baseline",
                 "sCommandText": "pytest -q"}

# (name, entry, valid?) — the participant INPUT contract.
LIST_CONTRACT_ROWS = [
    ("asserted", {"sStatus": "asserted"}, True),
    ("sourceInspection", {"sStatus": "supportedBySourceInspection"}, True),
    ("blocked", {"sStatus": "blockedForWantOfEvidence"}, True),
    ("baseline", dict(DICT_BASELINE), True),
    ("modifiedState", _fdictModified(), True),
    ("modifiedStateNoChangesOfSomeKinds", _fdictModified(
        dictChangeManifest=_fdictManifest(
            listDeletedPaths=[], listChangedFileModes=[],
            listSymlinkTargets=[])), True),
    ("webSource", _fdictWeb(), True),
    ("webSourceAssertedTwoLinks", _fdictWeb(
        sStatus="asserted",
        listSourceLinks=["https://a.example/x", "https://b.example/y"]),
     True),
    ("confirmedWebSource", _fdictWeb(sStatus="confirmed"), False),
    ("unknownForm", {"sStatus": "confirmed", "sStateForm": "rumour",
                     "sCommandText": "x"}, False),
    ("confirmedWithoutForm", {"sStatus": "confirmed",
                              "sCommandText": "pytest -q"}, False),
    ("baselineFormNotConfirmed", dict(DICT_BASELINE, sStatus="asserted"),
     False),
    ("baselineWithoutCommand", {"sStatus": "confirmed",
                                "sStateForm": "baseline"}, False),
    ("unconfirmedWithExtraField", {"sStatus": "asserted",
                                   "sClaimText": "it works"}, False),
    ("baselineWithModelHash", dict(DICT_BASELINE, sSnapshotHash="h"), False),
    ("modifiedStateWithSelfReportedProvenance", _fdictModified(
        sSnapshotHash="abc", sExecutionImageIdentity="sha256:x",
        iExitCode=0, sOutputDigest="d"), False),
    ("modifiedStateWithoutManifest", {
        "sStatus": "confirmed", "sStateForm": "modifiedState",
        "sCommandText": "./run.sh"}, False),
    ("manifestMissingAKind", _fdictModified(dictChangeManifest={
        sKey: jsonValue for sKey, jsonValue in _fdictManifest().items()
        if sKey != "listSymlinkTargets"}), False),
    ("manifestAsPathMap", _fdictModified(dictChangeManifest={
        "src/model.py": "fTolerance = 1e-9\n"}), False),
    ("manifestEntryWithExtraKey", _fdictModified(
        dictChangeManifest=_fdictManifest(listModifiedFiles=[{
            "sPath": "a.py", "sContent": "x", "sDigest": "d"}])), False),
    ("manifestModeNotOctal", _fdictModified(
        dictChangeManifest=_fdictManifest(listChangedFileModes=[{
            "sPath": "run.sh", "sMode": "rwxr-xr-x"}])), False),
    ("webSourceWithoutLinks", _fdictWeb(listSourceLinks=[]), False),
    ("webSourceTooManyLinks", _fdictWeb(listSourceLinks=[
        f"https://example.org/{iLink}" for iLink in range(
            agentCouncilEvidenceContract.I_MAXIMUM_SOURCE_LINKS + 1)]), False),
    ("webSourcePlainHttp", _fdictWeb(
        listSourceLinks=["http://example.org/x"]), False),
    ("webSourceOverlongLink", _fdictWeb(
        listSourceLinks=["https://example.org/" + "a" * 2100]), False),
    ("webSourceWithoutQuery", {
        sKey: jsonValue for sKey, jsonValue in _fdictWeb().items()
        if sKey != "sSearchQuery"}, False),
    ("webSourceMultilineQuery", _fdictWeb(
        sSearchQuery="first line\nsecond line"), False),
    ("emptyObject", {}, False),
    ("bareString", "I ran the tests and they passed", False),
    # Malformed values a model can still emit. Each must be a problem
    # the repair path can name, never an exception that stops the turn.
    ("formIsAnArray", {"sStatus": "confirmed", "sStateForm": [],
                       "sCommandText": "x"}, False),
    ("formIsAnObject", {"sStatus": "asserted", "sStateForm": {}}, False),
    ("modeIsANumber", _fdictModified(
        dictChangeManifest=_fdictManifest(listChangedFileModes=[{
            "sPath": "run.sh", "sMode": 755}])), False),
    # Whitespace is nothing to run and nothing to name: both refuse it.
    ("baselineBlankCommand", dict(DICT_BASELINE, sCommandText=" "), False),
    ("baselineWhitespaceCommand", dict(DICT_BASELINE, sCommandText="\t\n"),
     False),
    ("modifiedBlankPath", _fdictModified(
        dictChangeManifest=_fdictManifest(listModifiedFiles=[{
            "sPath": " ", "sContent": "x"}])), False),
    ("modifiedBlankDeletedPath", _fdictModified(
        dictChangeManifest=_fdictManifest(listDeletedPaths=["  "])), False),
    ("modifiedBlankLinkTarget", _fdictModified(
        dictChangeManifest=_fdictManifest(listSymlinkTargets=[{
            "sPath": "latest", "sTarget": " "}])), False),
    ("modifiedBlankContentIsAFile", _fdictModified(
        dictChangeManifest=_fdictManifest(listModifiedFiles=[{
            "sPath": "a.txt", "sContent": " "}])), True),
    ("commandWithNonAsciiSpaceOnly", dict(
        DICT_BASELINE, sCommandText="\u00a0"), True),
    # A schema's `$` is the end of the text; Python's also matches before
    # a final newline, so these passed the validator and failed the CLI.
    ("webSourceQueryTrailingNewline", _fdictWeb(
        sSearchQuery="release notes\n"), False),
    ("webSourceLinkTrailingNewline", _fdictWeb(
        listSourceLinks=["https://example.org/x\n"]), False),
    ("modeTrailingNewline", _fdictModified(
        dictChangeManifest=_fdictManifest(listChangedFileModes=[{
            "sPath": "run.sh", "sMode": "755\n"}])), False),
    # Python's \s and ECMA-262's differ (U+001C is whitespace to one
    # only), so the link pattern names its whitespace explicitly.
    ("webSourceLinkWithFileSeparator", _fdictWeb(
        listSourceLinks=["https://example.org/a\u001cb"]), True),
]


def _fdictCampaign():
    return agentCouncilCampaign.fdictCreateCampaign(
        "Is the solver's tolerance adequate?",
        [agentCouncilCampaign.fdictCreateParticipant("claude", "opus"),
         agentCouncilCampaign.fdictCreateParticipant("codex", "modelBeta")])


def _fdictResultWith(jsonEntry):
    dictResult = fdictMakeTurnResult(listEvidence=[jsonEntry])
    dictResult["listNotedFindings"] = []
    return dictResult


def _fiterMatchPatternAsEcma(validator, sPattern, jsonInstance, dictSchema):
    """Apply a schema pattern as ECMA-262 does, which Python's re does not.

    A JSON Schema pattern is an ECMA-262 regular expression, and the
    CLIs that enforce one follow it; ``jsonschema`` hands it to Python's
    ``re.search``, whose ``$`` also matches before a final newline. The
    stand-in must judge the way the real enforcer does.
    """
    if validator.is_type(jsonInstance, "string") and not re.search(
            sPattern.replace("$", r"\Z"), jsonInstance):
        yield jsonschema.ValidationError(
            f"{jsonInstance!r} does not match {sPattern!r}")


EcmaPatternValidator = jsonschema.validators.extend(
    jsonschema.Draft7Validator, {"pattern": _fiterMatchPatternAsEcma})


def _fbSchemaAccepts(dictSchema, dictResult):
    validator = EcmaPatternValidator(dictSchema)
    return not list(validator.iter_errors(dictResult))


def _fbValidatorAccepts(dictResult):
    return agentCouncilCharter.fdictValidateTurnResult(
        dictResult, bRequireNotes=True)["bValid"]


def _fdictProposalSchema():
    return agentCouncilCharter.fdictComposeTurnResultJsonSchema(
        _fdictCampaign(), agentCouncilCharter.S_PHASE_PROPOSAL)


def testTheComposedSchemaIsAValidSchema():
    """A malformed schema could pass the table by rejecting everything."""
    jsonschema.Draft7Validator.check_schema(_fdictProposalSchema())


@pytest.mark.falsification
def testTheSchemaAndTheValidatorGiveEveryRowTheSameVerdict():
    """Kills: the validator tolerating a field the strict schema forbids.

    One test over the whole table, so every disagreement is reported
    together rather than one row at a time.
    """
    dictSchema = _fdictProposalSchema()
    listDisagreements = []
    for sName, jsonEntry, bExpectedValid in LIST_CONTRACT_ROWS:
        dictResult = _fdictResultWith(jsonEntry)
        bSchema = _fbSchemaAccepts(dictSchema, dictResult)
        bValidator = _fbValidatorAccepts(dictResult)
        if (bSchema, bValidator) != (bExpectedValid, bExpectedValid):
            listDisagreements.append(
                f"{sName}: schema {bSchema}, validator {bValidator}, "
                f"expected {bExpectedValid}")
    assert not listDisagreements, "\n".join(listDisagreements)


def _flistCollectPatterns(jsonSchema):
    if isinstance(jsonSchema, dict):
        listPatterns = [jsonSchema["pattern"]] if "pattern" in jsonSchema else []
        for jsonValue in jsonSchema.values():
            listPatterns.extend(_flistCollectPatterns(jsonValue))
        return listPatterns
    if isinstance(jsonSchema, list):
        return [sPattern for jsonValue in jsonSchema
                for sPattern in _flistCollectPatterns(jsonValue)]
    return []


def testNoSchemaPatternUsesAClassWhoseMeaningDiffersByDialect():
    """\\s, \\d and \\w mean different sets in Python and ECMA-262.

    The validator and the CLIs would then disagree on exactly the
    characters those sets differ on, so every class is spelled out.
    """
    listPatterns = _flistCollectPatterns(_fdictProposalSchema())
    assert listPatterns
    for sPattern in listPatterns:
        assert not re.search(r"\\[sSdDwWbB]", sPattern), sPattern


LIST_PROBE_VALUES = [
    None, True, 0, 1.5, "", " ", "\t\n", "x", "x\n", [], ["x"], [" "],
    [[]], {}, {"sPath": "x"}]


def _fdictFuzzBaseResult():
    dictResult = _fdictResultWith(DICT_BASELINE)
    dictResult["listEvidence"] = [
        {"sStatus": "asserted"}, dict(DICT_BASELINE), _fdictModified(),
        _fdictWeb()]
    dictResult["listPlanItems"] = ["write the patch"]
    return dictResult


def _flistEveryValuePath(jsonValue, tPath=()):
    """Return the path to every value inside a turn result, itself included."""
    listPaths = [tPath]
    if isinstance(jsonValue, dict):
        for sKey, jsonChild in jsonValue.items():
            listPaths.extend(_flistEveryValuePath(jsonChild, tPath + (sKey,)))
    elif isinstance(jsonValue, list):
        for iIndex, jsonChild in enumerate(jsonValue):
            listPaths.extend(
                _flistEveryValuePath(jsonChild, tPath + (iIndex,)))
    return listPaths


def _fjsonReplaceAtPath(jsonRoot, tPath, jsonProbe):
    jsonCopy = copy.deepcopy(jsonRoot)
    jsonParent = jsonCopy
    for keyStep in tPath[:-1]:
        jsonParent = jsonParent[keyStep]
    jsonParent[tPath[-1]] = jsonProbe
    return jsonCopy


@pytest.mark.falsification
def testEveryMalformedValueGetsOneVerdictAndNoException():
    """Kills: a validator that raises on a value, or rules on it alone.

    Every value in a result holding all four evidence forms is replaced
    by each probe in turn. The validator must answer, not raise, because
    an exception here interrupts the campaign instead of following the
    repair path, and its answer must be the strict schema's.
    """
    dictBase = _fdictFuzzBaseResult()
    dictSchema = _fdictProposalSchema()
    assert _fbValidatorAccepts(dictBase) and _fbSchemaAccepts(
        dictSchema, dictBase)
    listFailures = []
    for tPath in _flistEveryValuePath(dictBase)[1:]:
        for jsonProbe in LIST_PROBE_VALUES:
            dictMutant = _fjsonReplaceAtPath(dictBase, tPath, jsonProbe)
            try:
                bValidator = _fbValidatorAccepts(dictMutant)
            except Exception as errorRaised:
                listFailures.append(f"{tPath} = {jsonProbe!r}: raised "
                                    f"{type(errorRaised).__name__}")
                continue
            bSchema = _fbSchemaAccepts(dictSchema, dictMutant)
            if bSchema != bValidator:
                listFailures.append(f"{tPath} = {jsonProbe!r}: schema "
                                    f"{bSchema}, validator {bValidator}")
    assert not listFailures, "\n".join(listFailures)


@pytest.mark.falsification
def testAMalformedEvidenceFormIsRepairedNotRaised():
    """Kills: an unhashable 'sStateForm' stopping the campaign.

    Driven through the engine: the first proposal carries an evidence
    form and a verdict that are arrays, the repair is valid, and the
    turn completes on its one repair attempt.
    """
    dictMalformed = fdictMakeTurnResult(listEvidence=[
        {"sStatus": "confirmed", "sStateForm": [], "sCommandText": "x"}])
    dictMalformed["sVerdict"] = []
    ffnDecide = lambda sHandle, dictRequest: (
        fdictDecideCompleted(dictMalformed)
        if (sHandle == "B" and dictRequest["sPhase"] == (
            agentCouncilCharter.S_PHASE_PROPOSAL)
            and not dictRequest["bRepairRequest"])
        else fdictDecideCompleted(fdictMakeTurnResult("accept")))
    fixture = fixtureBuildCouncil(
        [{"sHandle": "A", "sProvider": "prov-a", "sRequestedModel": "a"},
         {"sHandle": "B", "sProvider": "prov-b", "sRequestedModel": "b"}],
        ffnDecide, sChairbotHandle="A")
    dictOut = fixture.fdictDrive()
    listRecords = [
        dictRecord for dictRound in dictOut["listRounds"]
        for dictRecord in dictRound["dictTurnsByPhase"].get(
            agentCouncilCharter.S_PHASE_PROPOSAL, [])
        if fixture.fsHandleForId(dictRecord["sParticipantId"]) == "B"]
    assert listRecords[0]["bRepairAttempted"] is True
    assert listRecords[0]["sStatus"] == "completed"


def _fdictReadTarballJson(baTarball, sMemberName):
    with tarfile.open(fileobj=io.BytesIO(baTarball)) as fileTar:
        return json.loads(fileTar.extractfile(sMemberName).read())


def _fdictSchemaHandedToEachCli(tmp_path):
    """Return the schema file each strict CLI is actually given."""
    dictCampaign = _fdictCampaign()
    dictRequest = agentCouncilCharter.fdictComposeTurnRequest(
        dictCampaign, dictCampaign["listParticipants"][1],
        agentCouncilCharter.S_PHASE_PROPOSAL, 1, [])
    pathCredential = tmp_path / "credential.json"
    pathCredential.write_text("{}")
    baCodex = agentCouncilCodexProvider.fbaBuildCodexConfigTarball(
        str(pathCredential), dictRequest["dictOutputSchema"])
    baAntigravity = (
        agentCouncilAntigravityProvider.fbaBuildAntigravityConfigTarball(
            str(pathCredential), dictRequest["sInstructionChannel"],
            dictRequest["dictOutputSchema"]))
    return {
        "codex": _fdictReadTarballJson(
            baCodex, "vaibifyCouncilCodex/turn-schema.json"),
        "gemini": _fdictReadTarballJson(
            baAntigravity, "vaibifyCouncilAntigravity/turn-schema.json"),
    }


@pytest.mark.parametrize("sName,jsonEntry,bExpectedValid", [
    ("modifiedState", _fdictModified(), True),
    ("webSource", _fdictWeb(), True),
    ("modifiedStateWithSelfReportedProvenance", _fdictModified(
        iExitCode=0, sOutputDigest="d"), False),
    ("confirmedWebSource", _fdictWeb(sStatus="confirmed"), False),
])
def testEveryProviderPathReachesTheSameVerdict(
        tmp_path, sName, jsonEntry, bExpectedValid):
    """Codex and Antigravity read the schema file; Claude only validates."""
    dictResult = _fdictResultWith(jsonEntry)
    dictSchemas = _fdictSchemaHandedToEachCli(tmp_path)
    dictVerdicts = {
        sProvider: _fbSchemaAccepts(dictSchema, dictResult)
        for sProvider, dictSchema in dictSchemas.items()}
    dictVerdicts["claude"] = _fbValidatorAccepts(dictResult)
    assert dictVerdicts == {
        "codex": bExpectedValid, "gemini": bExpectedValid,
        "claude": bExpectedValid}, sName


def testTheTemplateNamesEveryFormAndEveryField():
    """The charter shows Claude the same table the validator enforces."""
    sSchema = agentCouncilCharter.fsComposeExactResultSchema()
    for sStateForm, tFields in (
            agentCouncilEvidenceContract.DICT_EVIDENCE_FIELDS_BY_FORM.items()):
        assert f"{sStateForm or 'unconfirmed'}" in sSchema
        for sField in tFields:
            assert sField in sSchema, sField
    for sKey in agentCouncilEvidenceContract.DICT_CHANGE_MANIFEST_ENTRY_FIELDS:
        assert sKey in sSchema, sKey
    assert "never confirmed" in sSchema


# ----- layer three: the LEDGER record, a different contract ------------

def _fdictServerBuiltModifiedStateEntry():
    return {
        "sClaimIdentifier": "claim-1",
        "sCommandText": "./run.sh",
        "sStateForm": "modifiedState",
        "sSnapshotHash": "snapshot-hash",
        "sExecutionImageIdentity": "sha256:image",
        "iExitCode": 0,
        "sOutputDigest": "output-digest",
        "dictChangeManifest": {
            "dictModifiedFileContents": {"src/model.py": "fTolerance = 1\n"},
            "listDeletedPaths": [], "dictChangedFileModes": {},
            "dictSymlinkTargets": {}},
    }


def testTheLedgerAcceptsAServerBuiltModifiedStateEntry():
    ledger = CouncilEvidenceLedger(65536, 262144)
    assert ledger.fdictRecordEvidence(
        _fdictServerBuiltModifiedStateEntry())["bRecorded"] is True


@pytest.mark.falsification
def testTheLedgerRefusesAnEntryWithoutObservedProvenance():
    """Kills: the ledger accepting a participant-shaped entry.

    The manifest here is already in the ledger's own form, so the only
    thing wrong is the missing provenance the sandbox would observe.
    """
    dictEntry = _fdictServerBuiltModifiedStateEntry()
    for sField in ("sSnapshotHash", "sExecutionImageIdentity",
                   "iExitCode", "sOutputDigest"):
        del dictEntry[sField]
    dictAnswer = CouncilEvidenceLedger(65536, 262144).fdictRecordEvidence(
        dictEntry)
    assert dictAnswer["bRecorded"] is False
    assert dictAnswer["sRefusalReason"] == "missingRequiredField"
