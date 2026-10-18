"""Determinism is one question, and answering it is the criterion.

History, because each step was a defect a researcher met. On 2026-08-30
the row went green over a block that declared nothing (the old form
wrote ``{"bAcceptBlasVariance": false}`` when submitted with nothing
ticked), and the gate was an OR any one of three values satisfied. The
fix made three separately-answered questions. On 2026-10-17 those became
ONE (design ruling 2), because none of the three changed what ran
(design fact 8): how much speed should vaibify trade for repeatable
files? The answer chooses an execution profile, which vaibify records
beside it.

ANSWERING is the criterion, never a particular answer: "Prioritize
speed" and "Repeatable across computers" both pass, and only silence
fails -- the rule the Personal AI Configuration row runs on. The answer
is its own key, because a value cannot say a question was considered,
and the three legacy answers do not answer the new question (D7).
"""

import pytest

from vaibify.reproducibility import executionProfiles
from vaibify.reproducibility.determinismGate import (
    LIST_DETERMINISM_QUESTIONS,
    fbWorkflowDeclaresDeterminism,
    flistAuditWorkflow,
    flistUnansweredDeterminismQuestions,
)


_DICT_ANSWERED_ACROSS = {
    "sRepeatabilityAnswer": "across", "sExecutionProfile": "across-v1",
}
_DICT_THREE_LEGACY_ANSWERS = {
    "sBlasVarianceAnswer": "rejected",
    "sOmpThreadsAnswer": "unpinned",
    "sMklModeAnswer": "not-used",
}


def _fdictWorkflow(dictDeterminism):
    """Return a workflow carrying one determinism block."""
    return {"dictDeterminism": dict(dictDeterminism)}


# -----------------------------------------------------------------------
# The gate: one question, and either answer
# -----------------------------------------------------------------------


def testSilenceFailsAndEitherAnswerPasses():
    """Answering is the criterion; neither answer is the right one."""
    assert flistUnansweredDeterminismQuestions(_fdictWorkflow({})) == [
        "repeatability"]
    for sAnswer in ("speed", "across"):
        assert fbWorkflowDeclaresDeterminism(_fdictWorkflow({
            "sRepeatabilityAnswer": sAnswer,
            "sExecutionProfile": sAnswer + "-v1"})) is True
    assert flistAuditWorkflow(_fdictWorkflow(_DICT_ANSWERED_ACROSS)) == []


@pytest.mark.falsification
def testAnAnswerWithoutItsProfileIsHalfAnAnswer():
    """The profile an answer names is what a rerun replays; without it, nothing runs.

    Kills: dropping the value check from the answered-question rule,
    which accepts an answer that names no profile.
    """
    assert "repeatability" in flistUnansweredDeterminismQuestions(
        _fdictWorkflow({"sRepeatabilityAnswer": "across"}))
    assert flistUnansweredDeterminismQuestions(
        _fdictWorkflow(_DICT_ANSWERED_ACROSS)) == []


def testAnUnknownAnswerIsNotAnAnswer():
    """A typo must not read as a recorded choice."""
    assert "repeatability" in flistUnansweredDeterminismQuestions(
        _fdictWorkflow({"sRepeatabilityAnswer": "acros",
                        "sExecutionProfile": "across-v1"}))


@pytest.mark.falsification
def testTheLegacyAnswersDoNotAnswerTheQuestion():
    """D7: three legacy answers, all given, leave the one question open.

    Kills: reading any non-empty block as an answer -- the round-one
    defect, which credits every project that ever filled in a form.
    """
    assert fbWorkflowDeclaresDeterminism(
        _fdictWorkflow(_DICT_THREE_LEGACY_ANSWERS)) is False
    assert fbWorkflowDeclaresDeterminism(
        _fdictWorkflow({"bAcceptBlasVariance": False})) is False


def testTheUnansweredQuestionIsNamedInItsOwnWords():
    """The issue says what is being asked, not that a key is missing."""
    listIssues = flistAuditWorkflow(_fdictWorkflow({}))
    assert len(listIssues) == len(LIST_DETERMINISM_QUESTIONS) == 1
    assert ("How much speed should vaibify trade for repeatable files?"
            in listIssues[0])


def testTheQuestionIsAskedWithoutSchemaKeys():
    """Every researcher-facing word comes from the design, with no key in it."""
    for dictQuestion in LIST_DETERMINISM_QUESTIONS:
        listTexts = [dictQuestion["sLabel"], dictQuestion["sPlainQuestion"],
                     dictQuestion["sWhy"], dictQuestion["sConsequence"]]
        for dictChoice in dictQuestion["listChoices"]:
            listTexts += [dictChoice["sLabel"], dictChoice["sDescription"]]
        for sText in listTexts:
            for sKey in ("sRepeatabilityAnswer", "sExecutionProfile",
                         "dictDeterminism", "speed-v1", "across-v1"):
                assert sKey not in sText, (sKey, sText)
    dictQuestion = LIST_DETERMINISM_QUESTIONS[0]
    assert [dictChoice["sAnswer"] for dictChoice in
            dictQuestion["listChoices"]] == ["speed", "across"]
    assert [dictChoice["bRecommended"] for dictChoice in
            dictQuestion["listChoices"]] == [False, True]


# -----------------------------------------------------------------------
# The poll ships the verdict, and the question in the gate's words
# -----------------------------------------------------------------------


@pytest.mark.falsification
def testThePollShipsTheVerdictRatherThanTheRawBlock():
    """The row must not have to re-derive what the gate already knows.

    Kills: dropping bDeterminismDeclared from
    _fdictBuildWorkflowEnvelopeDetail, which forces the row back onto
    the raw block.
    """
    import inspect
    from vaibify.gui.routes.pipelineRoutes import (
        _fdictBuildWorkflowEnvelopeDetail,
    )
    sSource = inspect.getsource(_fdictBuildWorkflowEnvelopeDetail)
    assert '"bDeterminismDeclared"' in sSource
    assert '"listDeterminismQuestions"' in sSource


def testTheWirePayloadCarriesTheQuestionAndTheRecordedProfile():
    """The row renders the backend's words and the profile the answer named."""
    from vaibify.gui.routes.pipelineRoutes import (
        _flistEnvelopeDeterminismQuestions,
    )
    listWire = _flistEnvelopeDeterminismQuestions(
        _fdictWorkflow(_DICT_ANSWERED_ACROSS))
    assert len(listWire) == 1
    dictEntry = listWire[0]
    assert dictEntry["bAnswered"] is True
    assert dictEntry["sAnswer"] == "across"
    assert dictEntry["sValue"] == "across-v1"
    assert dictEntry["listChoices"][1]["sLabel"] == (
        "Repeatable across computers")
    assert dictEntry["sWhy"] and dictEntry["sConsequence"]


# -----------------------------------------------------------------------
# The declare route records the answer and the newest profile for it
# -----------------------------------------------------------------------


@pytest.mark.falsification
def testTheAnswerRecordsTheNewestProfileForIt(monkeypatch):
    """vaibify writes the ID; a newer table version is what a fresh answer gets.

    Kills: recording a fixed version for the answer, which leaves a new
    profile version unreachable by answering.
    """
    from vaibify.gui.routes.reproducibilityRoutes import (
        _fdictApplyDeterminismAnswer,
    )
    dictTable = dict(executionProfiles.DICT_EXECUTION_PROFILES)
    dictTable["across-v2"] = dict(dictTable["across-v1"])
    monkeypatch.setattr(executionProfiles, "DICT_EXECUTION_PROFILES", dictTable)
    dictLegacy = dict(_DICT_THREE_LEGACY_ANSWERS)
    dictBlock = _fdictApplyDeterminismAnswer(
        dictLegacy, {"sRepeatabilityAnswer": "across"})
    assert dictBlock["sExecutionProfile"] == "across-v2"
    assert dictBlock["sRepeatabilityAnswer"] == "across"
    for sKey, sValue in _DICT_THREE_LEGACY_ANSWERS.items():
        assert dictBlock[sKey] == sValue
    assert _fdictApplyDeterminismAnswer(
        dictBlock, {"sRepeatabilityAnswer": "speed"})[
            "sExecutionProfile"] == "speed-v1"
    dictWithdrawn = _fdictApplyDeterminismAnswer(
        dictBlock, {"sRepeatabilityAnswer": None})
    assert "sRepeatabilityAnswer" not in dictWithdrawn
    assert "sExecutionProfile" not in dictWithdrawn


@pytest.mark.falsification
def testACallerCannotNameAProfileOrALegacyAnswer():
    """The route takes the answer and nothing else.

    Kills: accepting a profile ID from the caller, which lets an
    answer and the settings that run disagree.
    """
    from fastapi import HTTPException
    from vaibify.gui.routes.reproducibilityRoutes import (
        _fdictValidateDeterminismBody,
    )
    for dictBody in ({"sExecutionProfile": "across-v1"},
                     {"sBlasVarianceAnswer": "accepted"},
                     {"dOmpNumThreads": 4},
                     {"sRepeatabilityAnswer": "fastest"}):
        with pytest.raises(HTTPException) as errorRefused:
            _fdictValidateDeterminismBody(dictBody)
        assert errorRefused.value.status_code == 422, dictBody
    assert _fdictValidateDeterminismBody(
        {"sRepeatabilityAnswer": "across"}) == {
            "sRepeatabilityAnswer": "across"}


# -----------------------------------------------------------------------
# The migration, which decides what happens to projects that already
# passed under the OR
# -----------------------------------------------------------------------


@pytest.mark.falsification
def testTheMigrationPromotesOnlyUnambiguousLegacyValues():
    """A stored VALUE implies an answer only when it can mean one thing.

    `bAcceptBlasVariance: true` can only have come from a researcher
    ticking the box, so it carries over. `false` cannot: it is what
    the old form wrote when submitted with nothing ticked, so
    promoting it would attest a choice that may never have been made.

    Kills: promoting the false waiver to "rejected", which silently
    credits every project that ever opened the old form.
    """
    from vaibify.gui.workflowMigrations import fiApplyMigrations
    dictAccepted = {"dictDeterminism": {"bAcceptBlasVariance": True}}
    fiApplyMigrations(dictAccepted, "")
    assert dictAccepted["dictDeterminism"][
        "sBlasVarianceAnswer"] == "accepted"

    dictDefaulted = {"dictDeterminism": {"bAcceptBlasVariance": False}}
    fiApplyMigrations(dictDefaulted, "")
    assert "sBlasVarianceAnswer" not in dictDefaulted["dictDeterminism"]


def testTheMigrationKeepsTheValuesARerunActsOn():
    """Only the answers are new; the pinned values still mean something.

    ``vaibify reproduce`` carries the block forward verbatim, so
    dropping the legacy value keys would silently unpin a rerun.
    """
    from vaibify.gui.workflowMigrations import fiApplyMigrations
    dictWorkflow = {"dictDeterminism": {
        "bAcceptBlasVariance": True, "dOmpNumThreads": 8,
        "sMklCbwr": "COMPATIBLE",
    }}
    fiApplyMigrations(dictWorkflow, "")
    dictBlock = dictWorkflow["dictDeterminism"]
    assert dictBlock["dOmpNumThreads"] == 8
    assert dictBlock["sMklCbwr"] == "COMPATIBLE"
    assert dictBlock["sOmpThreadsAnswer"] == "pinned"
    assert dictBlock["sMklModeAnswer"] == "pinned"
    # Fully answered under the three questions, and still open under
    # the one that replaced them: none of them said which profile runs
    # (design D7). The record of what was declared stays.
    assert fbWorkflowDeclaresDeterminism(dictWorkflow) is False


def testTheMigrationNeverOverwritesAnExplicitAnswer():
    """A recorded answer outranks whatever a legacy value implies."""
    from vaibify.gui.workflowMigrations import fiApplyMigrations
    dictWorkflow = {"dictDeterminism": {
        "bAcceptBlasVariance": True,
        "sBlasVarianceAnswer": "rejected",
    }}
    fiApplyMigrations(dictWorkflow, "")
    assert dictWorkflow["dictDeterminism"][
        "sBlasVarianceAnswer"] == "rejected"


@pytest.mark.falsification
def testTheMigrationSpellsTheSameKeysTheGateReads():
    """The migrator's literals must equal the gate's constants.

    ``workflowMigrations`` may import only leaf modules or its callers
    form a cycle, so it spells the key names itself. That makes it a
    SECOND copy of the vocabulary — the exact shape that let the
    determinism row and the determinism gate disagree in the first
    place — so the relationship is pinned rather than the spelling
    trusted. A migrator writing an answer key the gate does not read
    would promote nothing while reporting success, and the researcher
    would meet an unanswered question they had answered years ago.

    Kills: changing any answer key or answer value in
    _fnMigrateV12ToV13 without changing determinismGate, which the
    migration's own behavioural tests cannot see (they assert the
    literal too).
    """
    import inspect
    from vaibify.gui import workflowMigrations
    from vaibify.reproducibility import determinismGate
    sSource = inspect.getsource(workflowMigrations._fnMigrateV12ToV13)
    for sConstant in (
        determinismGate.S_BLAS_ANSWER_KEY,
        determinismGate.S_OMP_ANSWER_KEY,
        determinismGate.S_MKL_ANSWER_KEY,
        determinismGate.S_ACCEPT_BLAS_WAIVER_KEY,
        determinismGate.S_OMP_NUM_THREADS_KEY,
        determinismGate.S_MKL_CBWR_KEY,
        determinismGate.S_BLAS_ACCEPTED,
        determinismGate.S_OMP_PINNED,
        determinismGate.S_MKL_PINNED,
    ):
        assert f'"{sConstant}"' in sSource, (
            f"the migrator does not spell {sConstant!r}, so it writes "
            "or reads a key the gate does not"
        )


@pytest.mark.falsification
def testTheV14MigrationLeavesTheQuestionOpenAndKeepsTheLegacyKeys():
    """D7: a v13 project arrives unanswered, its old answers untouched.

    Kills: a v13-to-v14 migrator that reads an answer into the legacy
    block, which attests a choice about what runs that nobody made.
    """
    from vaibify.gui.workflowMigrations import (
        I_CURRENT_WORKFLOW_VERSION, S_VERSION_KEY, fiApplyMigrations,
    )
    dictWorkflow = {S_VERSION_KEY: 13,
                    "dictDeterminism": dict(_DICT_THREE_LEGACY_ANSWERS)}
    fiApplyMigrations(dictWorkflow, "")
    assert dictWorkflow[S_VERSION_KEY] == I_CURRENT_WORKFLOW_VERSION == 14
    assert dictWorkflow["dictDeterminism"] == _DICT_THREE_LEGACY_ANSWERS
    assert flistUnansweredDeterminismQuestions(dictWorkflow) == [
        "repeatability"]
    dictCurrent = {S_VERSION_KEY: 14,
                   "dictDeterminism": dict(_DICT_ANSWERED_ACROSS)}
    fiApplyMigrations(dictCurrent, "")
    assert dictCurrent["dictDeterminism"] == _DICT_ANSWERED_ACROSS


@pytest.mark.falsification
def testTheV14MigratorSpellsNoKeyTheGateReads():
    """The sibling of the spelling test: this migrator must write NO answer.

    ``workflowMigrations`` spells keys as literals, so the way it would
    start fabricating an answer is by spelling one.

    Kills: a v13-to-v14 migrator that writes the answer or the profile.
    """
    import inspect
    from vaibify.gui import workflowMigrations
    from vaibify.reproducibility.determinismGate import (
        S_REPEATABILITY_ANSWER_KEY,
    )
    sSource = inspect.getsource(workflowMigrations._fnMigrateV13ToV14)
    for sConstant in (S_REPEATABILITY_ANSWER_KEY,
                      executionProfiles.S_EXECUTION_PROFILE_KEY):
        assert f'"{sConstant}"' not in sSource, sConstant


# -----------------------------------------------------------------------
# The maths-library reading, and the surfaces it has to reach
# -----------------------------------------------------------------------


def testTheMathsLibraryReadingCarriesBothLengths():
    """A toast and a row read the same fact at different lengths.

    Authored together so the short form cannot drift into claiming
    more than the long one does — which is the failure mode of every
    summary written somewhere other than beside its source.
    """
    import os
    import tempfile
    from vaibify.reproducibility.determinismGate import (
        fdictDetectMathsLibrary,
    )
    sRepo = tempfile.mkdtemp()
    with open(
        os.path.join(sRepo, "requirements.lock"), "w", encoding="utf-8",
    ) as fileLock:
        fileLock.write("numpy==2.4.6\nscipy==1.17.1\n")
    dictReading = fdictDetectMathsLibrary(sRepo)
    assert dictReading["bMklFound"] is False
    assert "MKL" in dictReading["sHeadline"]
    assert "MKL" in dictReading["sNote"]
    # The long form states the limit of reading a lock; the short one
    # claims only what the lock shows.
    assert "in your dependency lock" in dictReading["sHeadline"]
    assert "DECLARES" in dictReading["sNote"]
    with open(
        os.path.join(sRepo, "requirements.lock"), "w", encoding="utf-8",
    ) as fileLock:
        fileLock.write("numpy==2.4.6\nmkl==2025.0\n")
    dictFound = fdictDetectMathsLibrary(sRepo)
    assert dictFound["bMklFound"] is True
    assert "no execution profile covers" in dictFound["sHeadline"]
    assert "Neither answer covers it" in dictFound["sNote"]


@pytest.mark.falsification
def testTheScanToastReportsTheMathsLibrary():
    """The scan's TOAST is where a researcher reads its answer.

    The reading reached the response and the row but not the toast, so
    a researcher who ran the scan to settle the MKL question read
    "none of the known non-determinism patterns found" and concluded
    it had told them nothing — which about MKL it had. Reported
    2026-08-30, after the first fix shipped without this.

    Kills: dropping the maths-library suffix from
    _fdictDescribeDeterminismScan, which returns the toast to
    answering a different question than the button sits under.
    """
    import re as reModule
    sPath = "vaibify/gui/static/scriptApplication.js"
    with open(sPath, encoding="utf-8") as fileSource:
        sSource = fileSource.read()
    iStart = sSource.index("function _fdictDescribeDeterminismScan")
    sBody = sSource[iStart:sSource.index("\n    var _DICT_PROJECT_ACTIONS", iStart)]
    assert "sHeadline" in sBody, (
        "the scan toast never reads the maths-library headline"
    )
    # EVERY branch, not just the clean one: a project with findings
    # still needs the MKL answer, and that is the branch a researcher
    # with problems actually sees.
    iBranches = len(reModule.findall(r"sMessage:", sBody))
    iSuffixed = len(reModule.findall(r"sSuffix", sBody))
    assert iBranches >= 3, f"expected three toast branches, found {iBranches}"
    assert iSuffixed >= iBranches, (
        f"{iBranches} toast branches but the maths reading is appended "
        f"to only {iSuffixed - 1} of them"
    )
