"""Check Files Against Manifest says what a byte comparison can and cannot say.

The shape is the new-machine case that drove this report: a project
published on one computer and checked on another, where a JSON value
moved in its 13th significant digit, a derived binary file moved with
it, and no provenance record exists for either copy (the outputs were
made before records existed). The old toast named one file and told
everyone to "re-run the project or regenerate the envelope": the first
may reproduce the same bytes on the same computer, and the second
replaces the published hashes, destroying the comparison.

The expectations come from the plan's ruling, not from the code: every
differing file is listed with what is recorded about it ("production
context unknown" here), the check says it compares bytes and runs no
published tests, Verify Level 3 is named as the judge of tolerances,
a "speed" answer is named in the question's own words, regenerating is
a choice whose cost is stated, and no sentence claims a cause.
"""

import json
import os

import pytest

from tests.rerunAssessmentHarness import (
    fdictBuildStep,
    fdictBuildWorkflow,
    fnPinManifest,
    fnWriteBytes,
    fnWriteText,
)
from vaibify.gui import outputProvenance
from vaibify.gui.routes import pipelineRoutes
from vaibify.reproducibility.manifestWriter import flistVerifyManifest


S_JSON = "StepOne/fit.json"
S_DERIVED = "StepOne/derived.npz"


def _fdictPublishOnTheFirstComputer(sRoot):
    """Publish one step whose JSON and derived binary outputs are pinned."""
    dictWorkflow = fdictBuildWorkflow([fdictBuildStep(
        "Step one", "StepOne", saDataCommands=["true"],
        saOutputDataFiles=["fit.json", "derived.npz"])])
    dictWorkflow["sProjectRepoPath"] = sRoot
    fnWriteText(sRoot, S_JSON, json.dumps({"dSlope": 0.1234567890123}))
    fnWriteBytes(sRoot, S_DERIVED, b"PK\x03\x04" + b"\x01" * 64)
    fnPinManifest(sRoot, dictWorkflow)
    return dictWorkflow


def _fnRegenerateOnTheSecondComputer(sRoot):
    """Rewrite both outputs as a second computer would, with no records."""
    fnWriteText(sRoot, S_JSON, json.dumps({"dSlope": 0.1234567890124}))
    fnWriteBytes(sRoot, S_DERIVED, b"PK\x03\x04" + b"\x02" * 64)


def _fdictCheckFilesAgainstManifest(sRoot, dictWorkflow):
    """Run the route's own verify-and-compose path over the real tree."""
    return pipelineRoutes._fdictBuildManifestVerifyResult(
        sRoot, flistVerifyManifest(sRoot), [], dictWorkflow)


def _flistEverySentence(dictReport):
    return ([dictFile["sDescription"] for dictFile in dictReport["listFiles"]]
            + [dictReport["sScope"], dictReport["sDeterminismNote"],
               dictReport["sRegenerateChoice"]])


@pytest.fixture
def tCheckedOnASecondComputer(tmp_path):
    sRoot = str(tmp_path / "repo")
    dictWorkflow = _fdictPublishOnTheFirstComputer(sRoot)
    _fnRegenerateOnTheSecondComputer(sRoot)
    return sRoot, dictWorkflow


@pytest.mark.falsification
def testEveryDifferingFileIsListedWithWhatIsRecordedAboutIt(
        tCheckedOnASecondComputer):
    """Kills: reporting only the first differing file."""
    sRoot, dictWorkflow = tCheckedOnASecondComputer
    dictResult = _fdictCheckFilesAgainstManifest(sRoot, dictWorkflow)
    assert sorted(dictMismatch["sPath"] for dictMismatch in
                  dictResult["listMismatches"]) == [S_DERIVED, S_JSON]
    assert sorted(dictResult["dictMismatchReport"]["listFiles"],
                  key=lambda dictFile: dictFile["sPath"]) == [
        {"sPath": S_DERIVED, "sDescription": "production context unknown"},
        {"sPath": S_JSON, "sDescription": "production context unknown"}]


def testTheReportSaysTheCheckComparesBytesAndNamesVerifyLevelThree(
        tCheckedOnASecondComputer):
    dictReport = _fdictCheckFilesAgainstManifest(
        *tCheckedOnASecondComputer)["dictMismatchReport"]
    assert "runs none of the published tests" in dictReport["sScope"]
    assert "Verify Level 3" in dictReport["sScope"]


def testRegeneratingIsAChoiceWhoseCostIsStated(tCheckedOnASecondComputer):
    dictReport = _fdictCheckFilesAgainstManifest(
        *tCheckedOnASecondComputer)["dictMismatchReport"]
    sChoice = dictReport["sRegenerateChoice"]
    assert sChoice == outputProvenance.S_REGENERATE_CHOICE
    assert "only for a change you intended" in sChoice
    assert "replaces the published hashes" in sChoice
    assert "comparison with the published copies is lost" in sChoice


def testNoSentenceClaimsACauseOrAdvisesARerunAsTheFix(
        tCheckedOnASecondComputer):
    sRoot, dictWorkflow = tCheckedOnASecondComputer
    dictWorkflow["dictDeterminism"] = {"sRepeatabilityAnswer": "speed"}
    dictReport = _fdictCheckFilesAgainstManifest(
        sRoot, dictWorkflow)["dictMismatchReport"]
    for sSentence in _flistEverySentence(dictReport):
        assert "because" not in sSentence.lower(), sSentence
        assert "explain" not in sSentence.lower(), sSentence
        assert "Re-run the project" not in sSentence, sSentence


def testASpeedAnswerIsNamedInTheQuestionsOwnWords(tCheckedOnASecondComputer):
    sRoot, dictWorkflow = tCheckedOnASecondComputer
    assert _fdictCheckFilesAgainstManifest(sRoot, dictWorkflow)[
        "dictMismatchReport"]["sDeterminismNote"] == ""
    dictWorkflow["dictDeterminism"] = {"sRepeatabilityAnswer": "across"}
    assert _fdictCheckFilesAgainstManifest(sRoot, dictWorkflow)[
        "dictMismatchReport"]["sDeterminismNote"] == ""
    dictWorkflow["dictDeterminism"] = {"sRepeatabilityAnswer": "speed"}
    sNote = _fdictCheckFilesAgainstManifest(sRoot, dictWorkflow)[
        "dictMismatchReport"]["sDeterminismNote"]
    assert sNote.startswith('This project answered "Prioritize speed": ')
    assert "differ in their last digits" in sNote


def testAMissingFileIsSaidToBeMissing(tCheckedOnASecondComputer):
    sRoot, dictWorkflow = tCheckedOnASecondComputer
    os.remove(os.path.join(sRoot, S_DERIVED))
    dictFiles = {dictFile["sPath"]: dictFile["sDescription"] for dictFile in
                 _fdictCheckFilesAgainstManifest(sRoot, dictWorkflow)[
                     "dictMismatchReport"]["listFiles"]}
    assert dictFiles[S_DERIVED] == "missing on disk"


def testNothingDifferingProducesNoReport(tmp_path):
    sRoot = str(tmp_path / "repo")
    dictWorkflow = _fdictPublishOnTheFirstComputer(sRoot)
    dictResult = _fdictCheckFilesAgainstManifest(sRoot, dictWorkflow)
    assert dictResult["listMismatches"] == []
    assert dictResult["dictMismatchReport"] == {}
