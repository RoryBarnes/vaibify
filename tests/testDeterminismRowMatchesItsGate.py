"""Determinism is three questions, and each one must be answered.

Reported 2026-08-30 in two rounds, and the second round changed the
gate rather than the copy.

Round one: the researcher pressed Declare with nothing ticked, the form
wrote ``{"bAcceptBlasVariance": false}``, the row went green over a
block that satisfied nothing, and the Level 3 verify refused them.

Round two, looking at the repaired row: it was not clear that three
separate things were being asked. They were not — the gate was an OR,
so ANY one of a BLAS waiver, a pinned thread count or an MKL mode
satisfied it. A project could attest at Level 3 having answered a third
of the question, and pinning a thread count says nothing about whether
last-digit variance is acceptable. The researcher's ruling: make them
three requirements.

So each is asked separately now, and ANSWERING is the criterion — never
a particular answer. "I do not accept last-digit differences", "the
thread count is not fixed" and "this project does not use Intel MKL"
are complete, passing answers; only silence fails. That is the same
rule the Personal AI Configuration row runs on.

The answers are recorded as their own keys rather than inferred from
the values, and that is the whole repair for round one:
``bAcceptBlasVariance: false`` is what the old form wrote when nothing
was ticked, so it cannot be told apart from never having chosen.
"""

import re

import pytest

from vaibify.reproducibility.determinismGate import (
    LIST_DETERMINISM_QUESTIONS,
    fbWorkflowDeclaresDeterminism,
    flistAuditWorkflow,
    flistUnansweredDeterminismQuestions,
)


# Every question answered, each with the answer that needs no value.
_DICT_FULLY_ANSWERED = {
    "sBlasVarianceAnswer": "rejected",
    "sOmpThreadsAnswer": "unpinned",
    "sMklModeAnswer": "not-used",
}

# The block the OLD form wrote when submitted with nothing ticked.
_DICT_LEGACY_DECLARES_NOTHING = {"bAcceptBlasVariance": False}


def _fdictWorkflow(dictDeterminism):
    """Return a workflow carrying one determinism block."""
    return {"dictDeterminism": dict(dictDeterminism)}


# -----------------------------------------------------------------------
# The gate is an AND over three answers
# -----------------------------------------------------------------------


@pytest.mark.falsification
def testEveryQuestionMustBeAnsweredNotJustOne():
    """One answer no longer carries the other two.

    This is the ruling itself: the gate was an OR, so a project that
    had answered a third of the question attested at Level 3.

    Kills: returning True from flistUnansweredDeterminismQuestions'
    caller as soon as any single question is answered, i.e. restoring
    the OR.
    """
    for dictQuestion in LIST_DETERMINISM_QUESTIONS:
        dictOnlyThisOne = {
            dictQuestion["sAnswerKey"]: dictQuestion["tAnswers"][0],
        }
        if dictQuestion["sValueKey"]:
            dictOnlyThisOne[dictQuestion["sValueKey"]] = 4
        assert fbWorkflowDeclaresDeterminism(
            _fdictWorkflow(dictOnlyThisOne),
        ) is False, (
            f"answering only {dictQuestion['sKey']} satisfied the "
            "whole determinism gate"
        )
    assert fbWorkflowDeclaresDeterminism(
        _fdictWorkflow(_DICT_FULLY_ANSWERED),
    ) is True


def testDecliningIsACompleteAnswer():
    """Answering is the criterion, never a particular answer.

    A project that accepts no variance, pins no threads and uses no
    MKL has answered every question. Requiring a WAIVER rather than an
    answer would push researchers toward the permissive option to
    clear a gate, which inverts what the gate is for.
    """
    assert flistUnansweredDeterminismQuestions(
        _fdictWorkflow(_DICT_FULLY_ANSWERED),
    ) == []
    assert flistAuditWorkflow(
        _fdictWorkflow(_DICT_FULLY_ANSWERED),
    ) == []


@pytest.mark.falsification
def testAPinnedAnswerWithoutItsValueIsHalfAnAnswer():
    """"Threads are fixed" with no count is not something a rerun can do.

    Kills: dropping the value check from _fbQuestionIsAnswered, which
    accepts "pinned" with nothing pinned and records a rule no rerun
    could follow.
    """
    dictHalf = dict(_DICT_FULLY_ANSWERED)
    dictHalf["sOmpThreadsAnswer"] = "pinned"
    assert "ompThreads" in flistUnansweredDeterminismQuestions(
        _fdictWorkflow(dictHalf),
    )
    dictHalf["dOmpNumThreads"] = 8
    assert flistUnansweredDeterminismQuestions(
        _fdictWorkflow(dictHalf),
    ) == []


def testAnUnknownAnswerIsNotAnAnswer():
    """A typo must not read as a recorded choice.

    The answer keys are strings, so a value the gate does not
    recognize would otherwise pass "is it set?" and fail every
    downstream reading of what it means.
    """
    dictTypo = dict(_DICT_FULLY_ANSWERED)
    dictTypo["sBlasVarianceAnswer"] = "acceptedd"
    assert "blasVariance" in flistUnansweredDeterminismQuestions(
        _fdictWorkflow(dictTypo),
    )


# -----------------------------------------------------------------------
# The legacy shape, which is where round one came from
# -----------------------------------------------------------------------


@pytest.mark.falsification
def testTheLegacyFalseWaiverIsNotReadAsAnAnswer():
    """`bAcceptBlasVariance: false` means "unanswered" and "declined".

    It is the byte-for-byte output of pressing Declare with nothing
    ticked, so reading it as a deliberate "no" would attest a claim
    the researcher may never have made.

    Kills: treating the legacy waiver key as the blasVariance answer,
    which silently credits every project that ever opened the old form.
    """
    assert fbWorkflowDeclaresDeterminism(
        _fdictWorkflow(_DICT_LEGACY_DECLARES_NOTHING),
    ) is False
    assert "blasVariance" in flistUnansweredDeterminismQuestions(
        _fdictWorkflow(_DICT_LEGACY_DECLARES_NOTHING),
    )


def testEveryUnansweredQuestionIsNamedSeparately():
    """One issue per open question, so a row can carry each.

    A single "determinism is not declared" line cannot tell a
    researcher which of the three is still open — which is what made
    the section unreadable.
    """
    listIssues = flistAuditWorkflow(_fdictWorkflow({}))
    assert len(listIssues) == len(LIST_DETERMINISM_QUESTIONS)
    for dictQuestion in LIST_DETERMINISM_QUESTIONS:
        assert any(
            dictQuestion["sLabel"] in sIssue for sIssue in listIssues
        ), f"no issue names {dictQuestion['sKey']}"


def testTheQuestionsAreAskedWithoutSchemaKeys():
    """The researcher-facing text names no JSON keys.

    The old copy showed `bAcceptBlasVariance` to a scientist as if it
    were a word, and never said what BLAS was.
    """
    for dictQuestion in LIST_DETERMINISM_QUESTIONS:
        sText = dictQuestion["sLabel"] + dictQuestion["sPlainQuestion"]
        for sKey in (
            "bAcceptBlasVariance", "dOmpNumThreads", "sMklCbwr",
            "dictDeterminism",
        ):
            assert sKey not in sText, (
                f"{dictQuestion['sKey']} shows {sKey} to the researcher"
            )
    # And no BARE acronym. Asserted as the property rather than as a
    # phrase: the wording was rewritten once already for being
    # unreadable, and a test pinned to a sentence gets rewritten with
    # it instead of consulted. What must survive an edit is that a
    # researcher never meets a term the text has not explained.
    dictExpansions = {
        "MKL": "Math Kernel Library",
        "BLAS": "Basic Linear Algebra",
        "OMP": "OpenMP",
    }
    for dictQuestion in LIST_DETERMINISM_QUESTIONS:
        sText = dictQuestion["sLabel"] + " " + dictQuestion["sPlainQuestion"]
        for sAcronym, sExpansion in dictExpansions.items():
            # Whole word only. "OpenBLAS" is a product name that
            # happens to contain BLAS, and demanding an expansion for
            # it would push the copy back toward the jargon this
            # rewrite removed.
            if not re.search(r"\b" + sAcronym + r"\b", sText):
                continue
            assert sExpansion in sText, (
                f"{dictQuestion['sKey']} uses {sAcronym!r} without "
                f"expanding it to {sExpansion!r}: {sText!r}"
            )


# -----------------------------------------------------------------------
# The poll ships the verdict, so the row need not re-derive it
# -----------------------------------------------------------------------


@pytest.mark.falsification
def testThePollShipsTheVerdictRatherThanTheRawBlock():
    """The row must not have to re-derive what the gate already knows.

    Two authorities on one question is how they came to disagree in
    round one.

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
    assert '"listDeterminismQuestions"' in sSource, (
        "the poll ships no per-question state, so the three rows have "
        "nothing to render"
    )


def testTheWirePayloadCarriesOneEntryPerQuestion():
    """Each row's data comes from the backend, labels included."""
    from vaibify.gui.routes.pipelineRoutes import (
        _flistEnvelopeDeterminismQuestions,
    )
    listWire = _flistEnvelopeDeterminismQuestions(
        _fdictWorkflow({"sBlasVarianceAnswer": "accepted"}),
    )
    assert len(listWire) == len(LIST_DETERMINISM_QUESTIONS)
    dictByKey = {d["sKey"]: d for d in listWire}
    assert dictByKey["blasVariance"]["bAnswered"] is True
    assert dictByKey["ompThreads"]["bAnswered"] is False
    assert dictByKey["mklMode"]["bAnswered"] is False
    assert dictByKey["blasVariance"]["sQuestion"]


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
    # Fully pinned under the old rules IS fully answered under the new
    # ones, so a project that did the work keeps its credit.
    assert fbWorkflowDeclaresDeterminism(dictWorkflow) is True


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
    # The long form states the limit; the short one must not claim
    # past it, so it hedges rather than asserting "does not use MKL".
    assert "most likely" in dictReading["sHeadline"]
    assert "DECLARES" in dictReading["sNote"]


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
