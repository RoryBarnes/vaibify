"""The determinism question renders as the gate asks it, and as it was answered.

History: on 2026-08-30 the row read green over a block that declared
nothing, and then, repaired, was unreadable. Determinism became three
separately-answered questions; on 2026-10-17 those became ONE (design
ruling 2): how much speed vaibify should trade for repeatable files. The
answer chooses an execution profile, which vaibify records.

These assertions are about the SCREEN, so a Python test of the gate
proves none of them:

* one row, red until answered, offering both answers with neither
  chosen, the recommended one marked and its cost stated;
* an answered row shows the answer in its own words AND the profile it
  recorded -- the ID a reproduction replays;
* the text is the design's wording, with no schema key in it;
* when a current attestation is on file, the form says BEFORE saving
  that a different answer retires it.
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


_S_DRIVE_ROW = """(dictArgs) => {
    const sHtml = VaibifyWorkflowRequirements.fsRenderProjectBlock({
        dictWorkflowEnvelopeDetail: {
            dictDeterminism: dictArgs.dictDeterminism,
            bDeterminismDeclared: dictArgs.bDeclared,
            bRebuildAttestationCurrent: dictArgs.bAttestationCurrent,
            listDeterminismQuestions: dictArgs.listQuestions,
        },
        dictRemoteChecks: {},
        setExpandedRequirementGroups: new Set(['determinism']),
        setExpandedRequirementRows: new Set(['determinism-repeatability']),
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
    }));
    const listRadios = Array.from(elGroup.querySelectorAll(
        '.determinism-answer')).map((el) => ({
        sValue: el.value, bChecked: el.checked}));
    const elWhy = elGroup.querySelector('.determinism-why');
    const dictSeen = {
        listRows: listRows, listRadios: listRadios,
        sGroupText: elGroup.textContent,
        bWhyClosed: Boolean(elWhy) && !elWhy.open,
        bRawClosed: (() => {
            const elRaw = elGroup.querySelector('.determinism-raw-disclosure');
            return Boolean(elRaw) && !elRaw.open;
        })(),
    };
    elHost.remove();
    return dictSeen;
}"""


def _fdictDriveTheRow(pageDashboard, dictDeterminism, bAttestationCurrent):
    """Render the row from the wire shape the backend actually builds."""
    from vaibify.gui.routes.pipelineRoutes import (
        _flistEnvelopeDeterminismQuestions,
    )
    from vaibify.reproducibility.determinismGate import (
        fbWorkflowDeclaresDeterminism,
    )
    dictWorkflow = {"dictDeterminism": dictDeterminism}
    return pageDashboard.evaluate(_S_DRIVE_ROW, {
        "dictDeterminism": dictDeterminism or None,
        "bDeclared": fbWorkflowDeclaresDeterminism(dictWorkflow),
        "bAttestationCurrent": bAttestationCurrent,
        "listQuestions": _flistEnvelopeDeterminismQuestions(dictWorkflow),
    })


def test_an_unanswered_question_is_one_red_row_offering_both_answers(
    pageDashboard, serverHub,
):
    """Neither answer is chosen for the researcher, and the cost is stated."""
    fnOpenTheSeededHostWorkflow(
        pageDashboard, serverHub, bAwaitProjectBlock=True,
    )
    dictSeen = _fdictDriveTheRow(pageDashboard, {}, False)
    assert [d["sKey"] for d in dictSeen["listRows"]] == [
        "determinism-repeatability"]
    assert "level-cell-none" in dictSeen["listRows"][0]["sMarkup"]
    assert dictSeen["listRadios"] == [
        {"sValue": "speed", "bChecked": False},
        {"sValue": "across", "bChecked": False}]
    sText = dictSeen["sGroupText"]
    assert "Recommended for most projects." in sText
    assert "several times slower" in sText
    assert dictSeen["bWhyClosed"], "the explanation is missing or open"
    assert pageDashboard.listPageErrors == []


@pytest.mark.falsification
def test_the_row_renders_the_answer_and_the_profile_it_recorded(
    pageDashboard, serverHub,
):
    """The answer in its own words, beside the ID a reproduction replays.

    Kills: describing the answer without the execution profile it
    recorded.
    """
    fnOpenTheSeededHostWorkflow(
        pageDashboard, serverHub, bAwaitProjectBlock=True,
    )
    dictSeen = _fdictDriveTheRow(pageDashboard, {
        "sRepeatabilityAnswer": "across",
        "sExecutionProfile": "across-v1"}, False)
    assert "level-cell-attained" in dictSeen["listRows"][0]["sMarkup"]
    assert ("Your answer: Repeatable across computers (execution profile "
            "across-v1)") in dictSeen["sGroupText"]
    assert {"sValue": "across", "bChecked": True} in dictSeen["listRadios"]
    assert pageDashboard.listPageErrors == []


@pytest.mark.falsification
def test_the_question_is_asked_in_words_not_schema_keys(
    pageDashboard, serverHub,
):
    """A researcher is being asked about their work, not about a schema.

    Kills: dropping the question from the row detail, which leaves the
    researcher with a label and no question.
    """
    fnOpenTheSeededHostWorkflow(
        pageDashboard, serverHub, bAwaitProjectBlock=True,
    )
    sText = _fdictDriveTheRow(pageDashboard, {}, False)["sGroupText"]
    assert ("How much speed should vaibify trade for repeatable files?"
            in sText), sText[:400]
    for sKey in ("sRepeatabilityAnswer", "sExecutionProfile",
                 "bAcceptBlasVariance", "dOmpNumThreads"):
        assert sKey not in sText, (
            f"the researcher-facing text shows {sKey}")
    assert "last digits" in sText, (
        "the text never says what can differ, which is what a "
        f"researcher needs in order to answer: {sText[:400]}")
    assert pageDashboard.listPageErrors == []


@pytest.mark.falsification
def test_a_change_that_would_retire_the_attestation_says_so_first(
    pageDashboard, serverHub,
):
    """With a current attestation on file, the form names the retirement.

    Kills: telling the researcher only after the save, from a row that
    turned orange.
    """
    fnOpenTheSeededHostWorkflow(
        pageDashboard, serverHub, bAwaitProjectBlock=True,
    )
    dictAnswered = {"sRepeatabilityAnswer": "speed",
                    "sExecutionProfile": "speed-v1"}
    sCurrent = _fdictDriveTheRow(
        pageDashboard, dictAnswered, True)["sGroupText"]
    assert "Saving a different answer retires it" in sCurrent
    sNoAttestation = _fdictDriveTheRow(
        pageDashboard, dictAnswered, False)["sGroupText"]
    assert "Saving a different answer retires it" not in sNoAttestation
    assert pageDashboard.listPageErrors == []


def test_the_stored_entry_is_available_but_not_in_the_way(
    pageDashboard, serverHub,
):
    """Raw JSON belongs behind a disclosure, not mid-row."""
    fnOpenTheSeededHostWorkflow(
        pageDashboard, serverHub, bAwaitProjectBlock=True,
    )
    dictSeen = _fdictDriveTheRow(pageDashboard, {
        "sRepeatabilityAnswer": "speed",
        "sExecutionProfile": "speed-v1"}, False)
    assert dictSeen["bRawClosed"], (
        "the raw project.json entry is missing or open by default")
    assert pageDashboard.listPageErrors == []
