"""Each determinism question gets its own row, marker, and answer.

Reported 2026-08-30, in two rounds. First: the researcher declared
their project had nothing non-deterministic, the row went green, and
the Level 3 verify refused — because the form wrote
``{bAcceptBlasVariance: false}`` when submitted with nothing ticked,
which the row read as "a declaration exists" and the gate read as
nothing. Then, looking at the repaired row: the section was still
unreadable, and it was not clear that three separate things were being
asked at all.

So determinism became three separately-answered questions (the 2026-08-30
ruling), each with its own row and marker. ANSWERING is the criterion,
never a particular answer — "I do not accept last-digit differences",
"the thread count is not fixed" and "this project does not use Intel
MKL" all pass. Only silence fails.

These assertions are about the SCREEN, so a Python test of the gate
proves none of them. Four properties:

* Three rows, not one, each carrying its own marker.
* An unanswered question reads RED and an answered one GREEN,
  independently — the whole point of splitting them.
* The researcher-facing text names no schema keys. The old copy put
  ``bAcceptBlasVariance`` in front of a scientist as if it were a word,
  and left BLAS undefined.
* Saving without choosing records NOTHING. The default-writing form is
  what manufactured the original bug.
"""

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow


pytestmark = pytest.mark.browser


@pytest.fixture(autouse=True)
def fixtureDropClaimsBetweenTests(serverHub):
    """Give every claim back after each test.

    The hub is module-scoped and the page is not, so a test that
    claims the project and stops leaves it owned by a lease nobody
    holds; the symptom is a locked tile intercepting the next click.
    """
    yield
    from vaibify.config.containerLock import fnReleaseContainerLock
    dictContainerOwners = serverHub.app.state.dictContainerOwners
    for _sName, recordOwner in list(dictContainerOwners.items()):
        fileHandle = getattr(recordOwner, "fileHandleLock", None)
        if fileHandle is not None:
            try:
                fnReleaseContainerLock(fileHandle)
            except OSError:
                pass
    dictContainerOwners.clear()
    serverHub.app.state.dictSessionOwner.clear()


# The payload the backend sends for a half-answered project: the BLAS
# question answered, the other two still open.
_S_DRIVE_ROWS = """(listQuestions) => {
    const sHtml = VaibifyWorkflowRequirements.fsRenderProjectBlock({
        dictWorkflowEnvelopeDetail: {
            dictDeterminism: {sBlasVarianceAnswer: 'accepted'},
            bDeterminismDeclared: false,
            listDeterminismQuestions: listQuestions,
        },
        dictRemoteChecks: {},
        setExpandedRequirementGroups: new Set(['determinism']),
        setExpandedRequirementRows: new Set([
            'determinism-blasVariance', 'determinism-ompThreads',
            'determinism-mklMode',
        ]),
        setToggledFileGroups: new Set(),
    });
    const elHost = document.createElement('div');
    elHost.innerHTML = sHtml;
    document.body.appendChild(elHost);
    const listHeaders = Array.from(elHost.querySelectorAll(
        '.requirement-group-header'));
    const elGroup = listHeaders.find(
        el => (el.dataset.group || '') === 'determinism')
            .closest('.requirement-group');
    const listRows = Array.from(
        elGroup.querySelectorAll('.requirement-row')).map((elRow) => ({
        sKey: elRow.querySelector('.requirement-row-header').dataset.req,
        sMarkup: elRow.querySelector(
            '.requirement-row-header').innerHTML,
        sText: elRow.textContent,
    }));
    const sGroupText = elGroup.textContent;
    elHost.remove();
    return {listRows: listRows, sGroupText: sGroupText};
}"""


def _flistQuestionPayload():
    """The wire shape the backend actually builds, from the backend."""
    from vaibify.gui.routes.pipelineRoutes import (
        _flistEnvelopeDeterminismQuestions,
    )
    return _flistEnvelopeDeterminismQuestions(
        {"dictDeterminism": {"sBlasVarianceAnswer": "accepted"}},
    )


@pytest.mark.falsification
def test_each_question_gets_its_own_row_and_marker(
    pageDashboard, serverHub,
):
    """Three independent sources of difference, three lights.

    One light over an OR could not say which of the three was open —
    and pinning a thread count says nothing about whether last-digit
    variance is acceptable.

    Kills: collapsing _flistDeterminismRows back to a single row, which
    puts one marker back in front of three questions.
    """
    fnOpenTheSeededHostWorkflow(
        pageDashboard, serverHub, bAwaitProjectBlock=True,
    )
    dictSeen = pageDashboard.evaluate(
        _S_DRIVE_ROWS, _flistQuestionPayload())
    listRows = dictSeen["listRows"]

    assert len(listRows) == 3, (
        f"the determinism section renders {len(listRows)} row(s), so "
        "one marker still stands for three questions"
    )
    dictByKey = {d["sKey"]: d for d in listRows}
    assert "level-cell-attained" in (
        dictByKey["determinism-blasVariance"]["sMarkup"]
    ), "the answered question is not credited"
    for sKey in ("determinism-ompThreads", "determinism-mklMode"):
        assert "level-cell-none" in dictByKey[sKey]["sMarkup"], (
            f"{sKey} is unanswered and does not read red: "
            f"{dictByKey[sKey]['sMarkup'][:300]}"
        )


@pytest.mark.falsification
def test_the_questions_are_asked_in_words_not_schema_keys(
    pageDashboard, serverHub,
):
    """A scientist is being asked about their science.

    The old copy showed `bAcceptBlasVariance` and `dOmpNumThreads` as
    if they were words, and left BLAS undefined entirely.

    Kills: dropping sPlainQuestion from the row detail, which leaves
    the researcher with a label and no question.
    """
    fnOpenTheSeededHostWorkflow(
        pageDashboard, serverHub, bAwaitProjectBlock=True,
    )
    sText = pageDashboard.evaluate(
        _S_DRIVE_ROWS, _flistQuestionPayload())["sGroupText"]

    for sKey in ("bAcceptBlasVariance", "dOmpNumThreads", "sMklCbwr"):
        assert sKey not in sText, (
            f"the researcher-facing text still shows {sKey}"
        )
    # No BARE acronym reaches the screen. Asserted as the property
    # rather than as a phrase: this copy was rewritten once for being
    # unreadable ("MKL is not defined", 2026-08-30) and a test pinned
    # to a sentence would have been rewritten with it. "OpenBLAS" is a
    # product name and is matched as a whole word so it does not
    # demand an expansion of its own.
    import re as reModule
    for sAcronym, sExpansion in (
        ("MKL", "Math Kernel Library"),
        ("BLAS", "Basic Linear Algebra"),
    ):
        if reModule.search(r"\b" + sAcronym + r"\b", sText):
            assert sExpansion in sText, (
                f"{sAcronym} reaches the screen unexpanded: "
                f"{sText[:400]}"
            )
    assert "final digits" in sText, (
        "the questions never say what the variance actually IS, which "
        f"is what a researcher needs in order to answer: {sText[:400]}"
    )

    assert pageDashboard.listPageErrors == []


def test_the_stored_entry_is_available_but_not_in_the_way(
    pageDashboard, serverHub,
):
    """Raw JSON belongs behind a disclosure, not mid-row.

    A researcher publishing a project is entitled to see the exact
    bytes their declaration became; they should not have to read past
    them to answer a question.
    """
    fnOpenTheSeededHostWorkflow(
        pageDashboard, serverHub, bAwaitProjectBlock=True,
    )
    bClosed = pageDashboard.evaluate(
        """(listQuestions) => {
            const sHtml =
                VaibifyWorkflowRequirements.fsRenderProjectBlock({
                dictWorkflowEnvelopeDetail: {
                    dictDeterminism: {sBlasVarianceAnswer: 'accepted'},
                    listDeterminismQuestions: listQuestions,
                },
                dictRemoteChecks: {},
                setExpandedRequirementGroups: new Set(['determinism']),
                setExpandedRequirementRows:
                    new Set(['determinism-blasVariance']),
                setToggledFileGroups: new Set(),
            });
            const elHost = document.createElement('div');
            elHost.innerHTML = sHtml;
            document.body.appendChild(elHost);
            const elRaw = elHost.querySelector(
                '.determinism-raw-disclosure');
            const bResult = Boolean(elRaw) && !elRaw.open;
            elHost.remove();
            return bResult;
        }""", _flistQuestionPayload())
    assert bClosed, (
        "the raw project.json entry is missing or open by default"
    )

    assert pageDashboard.listPageErrors == []
