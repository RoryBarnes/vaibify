"""The archive row's two reds mean opposite things, and differ in SHAPE.

These assertions are about the SCREEN, so a Python test of the gate
proves none of them. Four properties, and each one is a way the row
could look right while saying something false:

* DIVERGED and CLOSED are both red and must not be the same mark. One
  says "fix your deposit", the other says "nothing can be done" — and
  colour alone reaches no colour-blind reader.
* UNCHECKED is neither. Vaibify could not compare, so the row must not
  wear the colour that means the deposit disagrees.
* A deposit in flight shows its BYTES. A silent multi-minute upload is
  indistinguishable from a hang from the chair.
* An archived row names the platform and the DOI. A bare check claims
  a deposit without saying which one.
"""

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow


pytestmark = pytest.mark.browser


@pytest.fixture(autouse=True)
def fixtureDropClaimsBetweenTests(serverHub):
    """Give every claim back after each test."""
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


_S_DRIVE_ROW = """(dictArchive) => {
    const sHtml = VaibifyWorkflowRequirements.fsRenderProjectBlock({
        dictWorkflowEnvelopeDetail: {dictImageArchive: dictArchive},
        dictRemoteChecks: {},
        setExpandedRequirementGroups: new Set(['artifacts']),
        setExpandedRequirementRows: new Set(['environmentArchive']),
        setToggledFileGroups: new Set(),
    });
    const elHost = document.createElement('div');
    elHost.innerHTML = sHtml;
    document.body.appendChild(elHost);
    const elRow = Array.from(elHost.querySelectorAll(
        '.requirement-row-header')).find(
            el => (el.dataset.req || '') === 'environmentArchive');
    const dictSeen = elRow ? {
        sMarkup: elRow.innerHTML,
        sText: elRow.closest('.requirement-row').textContent,
    } : null;
    elHost.remove();
    return dictSeen;
}"""


def _fdictArchivePayload(sState, **dictExtra):
    """Return the poll payload the backend builds for one row state."""
    dictPayload = {
        "sState": sState, "dictRecord": None, "listIssues": [],
        "dictDeposit": None,
    }
    dictPayload.update(dictExtra)
    return dictPayload


@pytest.mark.falsification
def test_diverged_and_closed_are_told_apart_by_shape_not_colour(
    pageDashboard, serverHub,
):
    """Their remedies are opposite, so their marks must differ.

    DIVERGED means the deposit on record does not cover this envelope
    and the researcher can fix it. CLOSED means the image is gone and
    Level 3 is out of reach for this result. Rendering both as the
    same red circle tells a researcher to go and fix something that
    cannot be fixed — and a reader who cannot distinguish the two
    reds gets no signal at all.

    Kills: dropping the ``diverged``/``closed`` branches from
    ``fsBuildLevelCell``, which returns both to the generic circle.
    """
    fnOpenTheSeededHostWorkflow(
        pageDashboard, serverHub, bAwaitProjectBlock=True,
    )
    sDiverged = pageDashboard.evaluate(
        _S_DRIVE_ROW, _fdictArchivePayload("diverged"))["sMarkup"]
    sClosed = pageDashboard.evaluate(
        _S_DRIVE_ROW, _fdictArchivePayload("closed"))["sMarkup"]

    assert "level-cell-warning" in sDiverged, (
        "a diverged deposit does not render its own mark: " + sDiverged
    )
    assert "level-cell-closed" in sClosed, (
        "a closed archive does not render its own mark: " + sClosed
    )
    assert sDiverged != sClosed, (
        "the two reds render identically, so shape carries nothing"
    )


@pytest.mark.falsification
def test_an_unchecked_archive_never_wears_a_divergence_mark(
    pageDashboard, serverHub,
):
    """Red is a claim about the deposit; nothing was compared.

    Kills: mapping ``unknown`` onto a red state in the row's colour
    table, which reports a divergence vaibify never established.
    """
    fnOpenTheSeededHostWorkflow(
        pageDashboard, serverHub, bAwaitProjectBlock=True,
    )
    dictSeen = pageDashboard.evaluate(
        _S_DRIVE_ROW, _fdictArchivePayload("unknown"))

    assert "level-cell-warning" not in dictSeen["sMarkup"]
    assert "level-cell-closed" not in dictSeen["sMarkup"]
    assert "level-cell-none" not in dictSeen["sMarkup"], (
        "an uncheckable archive reads as unmet: " + dictSeen["sMarkup"]
    )
    assert "could not check" in dictSeen["sText"], (
        "the row does not say that nothing was compared"
    )


def test_a_running_deposit_shows_the_bytes_it_has_moved(
    pageDashboard, serverHub,
):
    """A silent upload and a hung one look the same from the chair."""
    fnOpenTheSeededHostWorkflow(
        pageDashboard, serverHub, bAwaitProjectBlock=True,
    )
    dictSeen = pageDashboard.evaluate(_S_DRIVE_ROW, _fdictArchivePayload(
        "running",
        dictDeposit={
            "sPhase": "saving", "iBytesRead": 2 * 1024 ** 3,
            "iBytesTotal": 4 * 1024 ** 3, "sReason": "",
        },
    ))

    assert "2.00 GB" in dictSeen["sText"], (
        "the row shows no progress: " + dictSeen["sText"]
    )
    assert "4.00 GB" in dictSeen["sText"]


def test_an_archived_row_names_the_platform_and_the_doi(
    pageDashboard, serverHub,
):
    """Never a bare check: a deposit nobody can identify is a claim."""
    fnOpenTheSeededHostWorkflow(
        pageDashboard, serverHub, bAwaitProjectBlock=True,
    )
    dictSeen = pageDashboard.evaluate(_S_DRIVE_ROW, _fdictArchivePayload(
        "attained",
        dictRecord={
            "sVersionDoi": "10.5281/zenodo.7000001",
            "sArchitecture": "arm64",
            "sProvenance": "original",
        },
    ))

    assert "10.5281/zenodo.7000001" in dictSeen["sText"]
    assert "arm64" in dictSeen["sText"]
    assert "level-cell-attained" in dictSeen["sMarkup"]


def test_a_failed_deposit_says_why(pageDashboard, serverHub):
    """The reason outlives the task; nothing else records it."""
    fnOpenTheSeededHostWorkflow(
        pageDashboard, serverHub, bAwaitProjectBlock=True,
    )
    dictSeen = pageDashboard.evaluate(_S_DRIVE_ROW, _fdictArchivePayload(
        "unknown",
        dictDeposit={
            "sPhase": "failed", "iBytesRead": 0, "iBytesTotal": 0,
            "sReason": "HTTP 403 from the archive",
        },
    ))

    assert "HTTP 403 from the archive" in dictSeen["sText"], (
        "a failed deposit reports no reason: " + dictSeen["sText"]
    )


@pytest.mark.falsification
def test_declining_warns_that_level_three_becomes_unreachable(
    pageDashboard, serverHub,
):
    """The decline option states its Level 3 cost, beside the option.

    Declining is a COMPLETE answer to the Level 2 question and a bar
    on Level 3, and a researcher reading only the first half retires
    an image believing the ladder is still open. The gate is not in
    doubt -- `fbImageArchiveQuestionSettled` passes on a decline and
    `image-not-archived` goes on blocking L3 -- so the only thing
    that can be wrong here is the screen, which no Python test of the
    gate can see.

    The reversibility clause is asserted too, because a warning that
    only says "you cannot reach Level 3" would read as a lock, and
    declining is deliberately NOT one: the L3 gate reads the deposit
    record and never the answer, so depositing later opens the rung
    with nothing to undo.

    Kills: deleting the `environment-archive-decline-warning` block
    from `_fsRenderArchiveForm`.
    """
    fnOpenTheSeededHostWorkflow(
        pageDashboard, serverHub, bAwaitProjectBlock=True,
    )
    dictSeen = pageDashboard.evaluate(
        _S_DRIVE_ROW, _fdictArchivePayload("none"),
    )

    sText = dictSeen["sText"]
    assert "Decline" in sText, (
        "the decline option itself is missing: " + sText
    )
    assert "Level 3" in sText, (
        "declining never names the rung it costs: " + sText
    )
    assert "deposit later" in sText, (
        "the warning reads as a lock, but declining is reversible "
        "until the image is gone: " + sText
    )


_S_READ_LEVEL_CELLS = """(dictArchive) => {
    const sHtml = VaibifyWorkflowRequirements.fsRenderProjectBlock({
        dictWorkflowEnvelopeDetail: {dictImageArchive: dictArchive},
        dictRemoteChecks: {},
        setExpandedRequirementGroups: new Set(['artifacts']),
        setExpandedRequirementRows: new Set(),
        setToggledFileGroups: new Set(),
    });
    const elHost = document.createElement('div');
    elHost.innerHTML = sHtml;
    document.body.appendChild(elHost);
    const fsReadStrip = (elHeader) => {
        if (!elHeader) return null;
        const elStrip = elHeader.querySelector('.step-level-strip');
        return Array.from(elStrip.querySelectorAll('.step-level-cell'))
            .map(el => Array.from(el.classList).find(
                s => s.indexOf('level-cell-') === 0));
    };
    const elRow = Array.from(elHost.querySelectorAll(
        '.requirement-row-header')).find(
            el => (el.dataset.req || '') === 'environmentArchive');
    const elGroup = Array.from(elHost.querySelectorAll(
        '.requirement-group-header')).find(
            el => (el.dataset.group || '') === 'artifacts');
    const dictSeen = {
        aRow: fsReadStrip(elRow),
        aGroup: fsReadStrip(elGroup),
    };
    elHost.remove();
    return dictSeen;
}"""


@pytest.mark.falsification
def test_an_unanswered_archive_is_red_at_level_two_never_a_dash(
    pageDashboard, serverHub,
):
    """The L2 cell reports the question, not "no question here".

    The row owns a question with two halves -- L2 asks whether the
    researcher ANSWERED, L3 whether an archive EXISTS -- but carried
    only `iLevel: 3`, and `_fsRenderLevelStrip` fills every unclaimed
    level with the not-applicable dash. So the row rendered "not
    applicable at Level 2" while `image-archive-unanswered` was being
    emitted as an L2 blocker behind it (researcher-reported).

    A dash and a red circle are opposite statements: one says nothing
    is being asked of you, the other says something is and you have
    not done it.

    Kills: dropping `dictStateByLevel` from
    `_flistEnvironmentArchiveRows`, which returns the L2 cell to the
    dash.
    """
    fnOpenTheSeededHostWorkflow(
        pageDashboard, serverHub, bAwaitProjectBlock=True,
    )
    dictSeen = pageDashboard.evaluate(
        _S_READ_LEVEL_CELLS,
        _fdictArchivePayload("none", bAnswered=False),
    )

    assert dictSeen["aRow"][1] == "level-cell-none", (
        "the L2 cell is not red over an unanswered question: "
        + str(dictSeen["aRow"])
    )
    assert dictSeen["aRow"][1] != "level-cell-not-applicable", (
        "the L2 cell still reads as no-question-asked"
    )


@pytest.mark.falsification
def test_declining_turns_level_two_green_while_level_three_stays_red(
    pageDashboard, serverHub,
):
    """The two halves must be able to disagree, in this direction.

    This is the case that proves the L2 cell is not derived from the
    L3 state: a decline ANSWERS the Level 2 question (so L2 is
    attained) and deposits nothing (so Level 3 is not). Any
    implementation that computed one cell from the other collapses
    exactly here -- and it is also the researcher's stated contract,
    the archive being optional at L2 and required at L3.

    Kills: deriving `dictStateByLevel[2]` from `sState` instead of
    reading the gate's `bAnswered` verdict.
    """
    fnOpenTheSeededHostWorkflow(
        pageDashboard, serverHub, bAwaitProjectBlock=True,
    )
    dictSeen = pageDashboard.evaluate(
        _S_READ_LEVEL_CELLS,
        _fdictArchivePayload("none", bAnswered=True),
    )

    assert dictSeen["aRow"][1] == "level-cell-attained", (
        "declining did not settle the Level 2 question: "
        + str(dictSeen["aRow"])
    )
    assert dictSeen["aRow"][2] == "level-cell-none", (
        "declining wrongly satisfied Level 3, which asks whether an "
        "archive EXISTS: " + str(dictSeen["aRow"])
    )


@pytest.mark.falsification
def test_the_group_header_never_outranks_the_archive_row(
    pageDashboard, serverHub,
):
    """The Artifacts L2 cell must fail on the same set as its rows.

    A row state that reaches the row and not the header above it is
    the level-cell-versus-rows bug: the header would aggregate
    Artifacts at Level 2 from zero rows, keep the dash, and sit
    directly above a red cell contradicting it.

    Kills: filtering `_fdictGroupStateByLevel` on `iLevel` alone, so
    a multi-level row is invisible at every level but its nominal
    one.
    """
    fnOpenTheSeededHostWorkflow(
        pageDashboard, serverHub, bAwaitProjectBlock=True,
    )
    dictSeen = pageDashboard.evaluate(
        _S_READ_LEVEL_CELLS,
        _fdictArchivePayload("none", bAnswered=False),
    )

    assert dictSeen["aGroup"][1] != "level-cell-not-applicable", (
        "the Artifacts header still shows n/a at Level 2 over a row "
        "that is red there: " + str(dictSeen["aGroup"])
    )
    assert dictSeen["aGroup"][1] != "level-cell-attained", (
        "the header outranks its own row: " + str(dictSeen["aGroup"])
    )


@pytest.mark.falsification
def test_a_closed_archive_keeps_its_cross_on_the_two_level_row(
    pageDashboard, serverHub,
):
    """Giving the row a second level must not cost it its L3 shape.

    `closed` and `diverged` are two reds whose remedies are opposite,
    and shape is the channel that survives colour blindness. The row
    now composes its strip from `dictStateByLevel` rather than from
    one mark, so the L3 word has to arrive intact -- a level map that
    flattened it to a generic "none" would silently undo the
    distinction the row was built to make, and the cell would still
    be red.

    Scope, stated because the obvious wider assertion is vacuous: the
    Artifacts GROUP header cannot witness this. Five sibling artifact
    rows sit at Level 3, and their states already force the aggregate
    (measured: with the archive row reduced or raw, the header reads
    `none` either way). `_fsSummarizableLevelState` is therefore
    defensive rather than load-bearing here -- it matters only for a
    multi-level row that is ALONE at its level, which no group
    currently has -- and this test does not claim to kill its
    removal.

    Kills: collapsing `dictStateByLevel[3]` to a generic state
    instead of carrying the archive row's own mark through.
    """
    fnOpenTheSeededHostWorkflow(
        pageDashboard, serverHub, bAwaitProjectBlock=True,
    )
    dictSeen = pageDashboard.evaluate(
        _S_READ_LEVEL_CELLS,
        _fdictArchivePayload("closed", bAnswered=True),
    )

    assert dictSeen["aRow"][2] == "level-cell-closed", (
        "the row lost the cross that says nothing can be done: "
        + str(dictSeen["aRow"])
    )
    assert dictSeen["aRow"][1] == "level-cell-attained", (
        "a closed archive still ANSWERED the Level 2 question: "
        + str(dictSeen["aRow"])
    )


_S_READ_FORM = """(dictArchive) => {
    const sHtml = VaibifyWorkflowRequirements.fsRenderProjectBlock({
        dictWorkflowEnvelopeDetail: {dictImageArchive: dictArchive},
        dictRemoteChecks: {},
        setExpandedRequirementGroups: new Set(['artifacts']),
        setExpandedRequirementRows: new Set(['environmentArchive']),
        setToggledFileGroups: new Set(),
    });
    const elHost = document.createElement('div');
    elHost.innerHTML = sHtml;
    document.body.appendChild(elHost);
    const dictSeen = {
        dictChecked: {},
        sDoi: (elHost.querySelector('.environment-archive-doi') || {}).value,
    };
    elHost.querySelectorAll('.environment-archive-answer').forEach(
        el => { dictSeen.dictChecked[el.value] = el.checked; });
    elHost.remove();
    return dictSeen;
}"""


@pytest.mark.falsification
def test_a_saved_answer_is_shown_back_in_the_form(
    pageDashboard, serverHub,
):
    """A write-only form reads as a save that failed.

    The radios carried no `checked` attribute at all, so a researcher
    who declined, closed the block and reopened it found an untouched
    form (researcher-reported, 2026-09-08). The natural reading is
    that the answer did not take -- and a researcher who cannot see a
    decision they recorded cannot audit it either.

    `.checked` is read off the live DOM rather than the markup,
    because that is the property a browser actually acts on.

    Kills: dropping the `fsChecked` calls from
    `_fsRenderArchiveForm`.
    """
    fnOpenTheSeededHostWorkflow(
        pageDashboard, serverHub, bAwaitProjectBlock=True,
    )
    dictSeen = pageDashboard.evaluate(
        _S_READ_FORM, _fdictArchivePayload("none", sAnswer="declined"),
    )

    assert dictSeen["dictChecked"]["declined"] is True, (
        "a recorded decline is not shown back: "
        + str(dictSeen["dictChecked"])
    )
    assert dictSeen["dictChecked"]["referenced"] is False, (
        "the wrong option is selected: " + str(dictSeen["dictChecked"])
    )


@pytest.mark.falsification
def test_an_unanswered_form_selects_nothing(pageDashboard, serverHub):
    """The other direction: silence must not pre-select an answer.

    Showing an answer back is only honest if NOT having answered
    looks different. A default selection would put a decision in the
    researcher's mouth and let one click record it.

    Kills: defaulting `sAnswer`, or marking a radio checked
    unconditionally.
    """
    fnOpenTheSeededHostWorkflow(
        pageDashboard, serverHub, bAwaitProjectBlock=True,
    )
    dictSeen = pageDashboard.evaluate(
        _S_READ_FORM, _fdictArchivePayload("none"),
    )

    assert dictSeen["dictChecked"] == {
        "referenced": False, "declined": False,
    }, "an unanswered question arrives pre-answered: " + str(
        dictSeen["dictChecked"])


@pytest.mark.falsification
def test_a_referenced_deposit_restores_its_version_doi(
    pageDashboard, serverHub,
):
    """The DOI comes back with the answer it belongs to.

    A restored radio beside an empty DOI box is the same write-only
    problem one field along: the researcher sees the choice they made
    and not the value that made it meaningful. The DOI lives in the
    deposit RECORD rather than the answer -- no answer carries a
    value, so the DOI has one authority -- and it is the VERSION DOI,
    because Zenodo's concept DOI always resolves to the newest
    version and would hand back a string that does not name the
    archived image.

    Kills: reading `sConceptDoi`, or dropping `sDoiValue` from the
    DOI input.
    """
    fnOpenTheSeededHostWorkflow(
        pageDashboard, serverHub, bAwaitProjectBlock=True,
    )
    dictSeen = pageDashboard.evaluate(
        _S_READ_FORM,
        _fdictArchivePayload(
            "none", sAnswer="referenced",
            dictRecord={
                "sVersionDoi": "10.5281/zenodo.7000001",
                "sConceptDoi": "10.5281/zenodo.7000000",
                "sArchitecture": "arm64",
            },
        ),
    )

    assert dictSeen["dictChecked"]["referenced"] is True
    assert dictSeen["sDoi"] == "10.5281/zenodo.7000001", (
        "the version DOI was not restored: " + str(dictSeen["sDoi"])
    )
    assert dictSeen["sDoi"] != "10.5281/zenodo.7000000", (
        "the concept DOI was restored, which resolves to whatever "
        "was deposited last"
    )


_S_READ_TOOLTIPS = """(dictArchive) => {
    const sHtml = VaibifyWorkflowRequirements.fsRenderProjectBlock({
        dictWorkflowEnvelopeDetail: {dictImageArchive: dictArchive},
        dictRemoteChecks: {},
        setExpandedRequirementGroups: new Set(['artifacts']),
        setExpandedRequirementRows: new Set(),
        setToggledFileGroups: new Set(),
    });
    const elHost = document.createElement('div');
    elHost.innerHTML = sHtml;
    document.body.appendChild(elHost);
    const elRow = Array.from(elHost.querySelectorAll(
        '.requirement-row-header')).find(
            el => (el.dataset.req || '') === 'environmentArchive');
    const aTitles = Array.from(
        elRow.querySelectorAll('.step-level-cell')).map(el => el.title);
    elHost.remove();
    return aTitles;
}"""


@pytest.mark.falsification
def test_the_level_three_tooltip_names_the_actual_cause(
    pageDashboard, serverHub,
):
    """"Not met" is true and useless; the hover must say WHY.

    The cell title carried only the state phrase, so a red Level 3
    read "Environment archive — Level 3: not met" and left the
    researcher to go and find the cause vaibify had already computed
    (researcher-reported, 2026-09-08). The reason comes from the
    gate's own issue list, so the tooltip and the expanded row cannot
    disagree about why the level is unmet.

    Kills: dropping `dictReasonByLevel` from the archive row, or the
    reason clause from `_fsRenderLevelStrip`.
    """
    fnOpenTheSeededHostWorkflow(
        pageDashboard, serverHub, bAwaitProjectBlock=True,
    )
    aTitles = pageDashboard.evaluate(
        _S_READ_TOOLTIPS,
        _fdictArchivePayload(
            "none", bAnswered=True, sAnswer="declined",
            listIssues=[
                "No image archive has been deposited for this envelope.",
            ],
        ),
    )

    assert "No image archive has been deposited" in aTitles[2], (
        "the Level 3 hover does not name its cause: " + aTitles[2]
    )


@pytest.mark.falsification
def test_an_unchecked_level_three_says_what_is_missing(
    pageDashboard, serverHub,
):
    """The unchecked reason is the one sentence worth reading.

    `flistDescribeImageArchiveIssues` returns [] for this state by
    design, so that an unmade comparison can never be rendered as a
    divergence -- and that discarded the only actionable sentence
    there is. A grey "?" with an empty issue list is what a
    researcher met when their envelope recorded no architecture. The
    reason travels in its own key so it can never be mistaken for a
    difference that was found.

    Kills: dropping `sUncheckedReason` from the payload, or reading
    `listIssues` for the unchecked state (which is empty, so the
    tooltip silently loses its reason).
    """
    fnOpenTheSeededHostWorkflow(
        pageDashboard, serverHub, bAwaitProjectBlock=True,
    )
    aTitles = pageDashboard.evaluate(
        _S_READ_TOOLTIPS,
        _fdictArchivePayload(
            "unknown", bAnswered=True, listIssues=[],
            sUncheckedReason=(
                "The environment snapshot records no architecture, "
                "so the deposit on record cannot be checked against "
                "it."
            ),
        ),
    )

    assert "records no architecture" in aTitles[2], (
        "an unchecked Level 3 explains nothing: " + aTitles[2]
    )


@pytest.mark.falsification
def test_a_mismatch_tooltip_names_one_cause_and_counts_the_rest(
    pageDashboard, serverHub,
):
    """The actual first difference, not a paragraph of them.

    A deposit can fail a comparison several ways at once. Joining
    them all turns a hover into a wall the researcher cannot read at
    a glance, and naming none of them is the defect this fixes. The
    expanded row lists every one.

    Kills: joining the whole issue list into the tooltip, or naming
    the count without the cause.
    """
    fnOpenTheSeededHostWorkflow(
        pageDashboard, serverHub, bAwaitProjectBlock=True,
    )
    aTitles = pageDashboard.evaluate(
        _S_READ_TOOLTIPS,
        _fdictArchivePayload(
            "diverged", bAnswered=True,
            listIssues=[
                "The deposit records no version DOI.",
                "The deposit records no tarball size.",
                "The deposit records no provenance.",
            ],
        ),
    )

    assert "The deposit records no version DOI." in aTitles[2]
    assert "and 2 more" in aTitles[2], (
        "the other differences are not accounted for: " + aTitles[2]
    )
    assert "tarball size" not in aTitles[2], (
        "the tooltip inlined the whole list: " + aTitles[2]
    )


_S_DRIVE_RERENDER = """async (bFocus) => {
    const q = (s) => document.querySelectorAll(s);
    const elBlock = document.getElementById('projectBlock');
    const elField = document.createElement('input');
    elField.type = 'radio';
    elBlock.appendChild(elField);
    if (bFocus) elField.focus();
    elField.checked = true;
    // Toggle a group through the real handler. The click is
    // DISPATCHED rather than performed, so it runs the production
    // re-render without moving focus -- a real mouse click would
    // blur the field and prove nothing.
    const elOther = Array.from(q('.requirement-group-header')).find(
        (el) => (el.dataset.group || '') === 'repository');
    const bWasOpen =
        elOther.parentElement.className.indexOf('collapsed') === -1;
    elOther.dispatchEvent(new MouseEvent('click', {bubbles: true}));
    await new Promise(
        (r) => requestAnimationFrame(() => requestAnimationFrame(r)));
    const elAfter = Array.from(q('.requirement-group-header')).find(
        (el) => (el.dataset.group || '') === 'repository');
    return {
        bRerenderLanded:
            bWasOpen !== (elAfter.parentElement.className.indexOf(
                'collapsed') === -1),
        bFieldSurvived:
            document.body.contains(elField) && elField.checked,
    };
}"""


@pytest.mark.falsification
def test_a_rerender_does_not_revert_an_answer_being_changed(
    pageDashboard, serverHub,
):
    """A researcher must be able to change their mind.

    The Project block is rewritten wholesale with innerHTML whenever
    its output differs from the last render, and the block carries
    live material -- pulsing remote badges, counts -- that moves on
    its own. Once the radios render the SAVED answer, any such render
    landing mid-edit puts the old answer back. Measured before the
    guard: a selection read `true` on the live element and `false`
    immediately after one re-render.

    The effect is total, not cosmetic. Declining is meant to be
    revocable -- the Level 3 gate reads the deposit record and never
    the answer, precisely so depositing later opens the rung -- but a
    form that snaps back to Declined before Save can be clicked makes
    it permanent in practice. Reported as "I cannot undecline, so
    there is no way to move on from Level 2" (2026-09-08).

    The unfocused leg is what stops this being vacuous: it asserts
    the re-render REALLY replaces the block and destroys the field,
    so the focused leg is measuring the guard rather than a render
    that never happened.

    Kills: removing the `fbProjectFormFieldFocused` check from
    `_fnRenderProjectBlock`.
    """
    fnOpenTheSeededHostWorkflow(
        pageDashboard, serverHub, bAwaitProjectBlock=True,
    )
    dictHeld = pageDashboard.evaluate(_S_DRIVE_RERENDER, True)
    assert dictHeld["bFieldSurvived"] is True, (
        "a re-render discarded the answer being changed, so a "
        "recorded answer cannot be revised"
    )

    pageDashboard.reload()
    fnOpenTheSeededHostWorkflow(
        pageDashboard, serverHub, bAwaitProjectBlock=True,
    )
    dictFree = pageDashboard.evaluate(_S_DRIVE_RERENDER, False)
    assert dictFree["bRerenderLanded"] is True, (
        "the trigger did not actually re-render the block, so the "
        "focused leg above proves nothing"
    )
    assert dictFree["bFieldSurvived"] is False, (
        "the block was not replaced even unfocused, so this test "
        "cannot tell the guard from a render that never ran"
    )


@pytest.mark.falsification
def test_a_deposit_in_flight_pulses(pageDashboard, serverHub):
    """A multi-minute operation must not look settled.

    A deposit is a save, a compress and an upload -- over a minute on
    a real image (researcher-reported, 2026-09-09). The orange circle
    alone is a static mark and reads as a state the project is IN;
    the pulse is what says work is under way. The
    rebuild-attestation row beside it has carried this since its
    rerun could take hours.

    Kills: dropping `bChecking` from the environment-archive row.
    """
    fnOpenTheSeededHostWorkflow(
        pageDashboard, serverHub, bAwaitProjectBlock=True,
    )
    bPulsing = pageDashboard.evaluate("""(dictArchive) => {
        const sHtml = VaibifyWorkflowRequirements.fsRenderProjectBlock({
            dictWorkflowEnvelopeDetail: {dictImageArchive: dictArchive},
            dictRemoteChecks: {},
            setExpandedRequirementGroups: new Set(['artifacts']),
            setExpandedRequirementRows: new Set(),
            setToggledFileGroups: new Set()});
        const elHost = document.createElement('div');
        elHost.innerHTML = sHtml;
        document.body.appendChild(elHost);
        const elRow = Array.from(elHost.querySelectorAll(
            '.requirement-row-header')).find(
                el => (el.dataset.req || '') === 'environmentArchive');
        const bSeen = elRow.closest('.requirement-row')
            .classList.contains('requirement-row-checking');
        elHost.remove();
        return bSeen;
    }""", _fdictArchivePayload("running", bAnswered=True))

    assert bPulsing is True, (
        "a deposit in flight renders as a settled orange mark"
    )


@pytest.mark.falsification
def test_a_record_without_an_architecture_says_so(
    pageDashboard, serverHub,
):
    """A bare "?" reads as a glitch, not as the fact it is.

    The deposit covers a build nobody recorded, so it can never be
    matched to this envelope -- a real consequence a researcher asked
    about directly after seeing "Deposited ? build under 10.5072/..."
    (2026-09-09).

    Kills: restoring `dictRecord.sArchitecture || "?"`.
    """
    fnOpenTheSeededHostWorkflow(
        pageDashboard, serverHub, bAwaitProjectBlock=True,
    )
    dictSeen = pageDashboard.evaluate(
        _S_DRIVE_ROW,
        _fdictArchivePayload(
            "unknown", bAnswered=True,
            dictRecord={"sVersionDoi": "10.5072/zenodo.599592",
                        "sArchitecture": ""},
        ),
    )

    assert "Deposited ? build" not in dictSeen["sText"], (
        "the row still prints a bare question mark: " + dictSeen["sText"]
    )
    assert "does not name" in dictSeen["sText"], (
        "the missing architecture is not explained: " + dictSeen["sText"]
    )
    assert "10.5072/zenodo.599592" in dictSeen["sText"]


@pytest.mark.falsification
def test_a_deposited_doi_gets_its_own_selectable_field(
    pageDashboard, serverHub,
):
    """The DOI a researcher must cite must be selectable.

    It was reported only inside a prose sentence while the DOI input
    below still showed its placeholder, so the screen read as though
    nothing had been recorded and the one string worth copying was
    the hardest thing on the row to select (researcher-reported,
    2026-09-09).

    Read-only and SEPARATE from the input beneath it: that input
    means "point at someone else's deposit", and this is the deposit
    this project made. Both properties are asserted, because a
    writable field here would invite editing a value whose source is
    the envelope record.

    Kills: dropping `_fsRenderDepositedDoiRow`, or rendering the DOI
    into the `environment-archive-doi` input instead.
    """
    fnOpenTheSeededHostWorkflow(
        pageDashboard, serverHub, bAwaitProjectBlock=True,
    )
    dictSeen = pageDashboard.evaluate("""(dictArchive) => {
        const sHtml = VaibifyWorkflowRequirements.fsRenderProjectBlock({
            dictWorkflowEnvelopeDetail: {dictImageArchive: dictArchive},
            dictRemoteChecks: {},
            setExpandedRequirementGroups: new Set(['artifacts']),
            setExpandedRequirementRows: new Set(['environmentArchive']),
            setToggledFileGroups: new Set()});
        const elHost = document.createElement('div');
        elHost.innerHTML = sHtml;
        document.body.appendChild(elHost);
        const elValue = elHost.querySelector(
            '.environment-archive-doi-value');
        const elInput = elHost.querySelector('.environment-archive-doi');
        const dictSeen = {
            sDeposited: elValue ? elValue.value : '',
            bReadOnly: elValue ? elValue.readOnly : null,
            sReferenceInput: elInput ? elInput.value : '',
        };
        elHost.remove();
        return dictSeen;
    }""", _fdictArchivePayload(
        "attained", bAnswered=True,
        dictRecord={"sVersionDoi": "10.5072/zenodo.599633",
                    "sArchitecture": "amd64"},
    ))

    assert dictSeen["sDeposited"] == "10.5072/zenodo.599633", (
        "the deposited DOI has no field of its own: " + str(dictSeen)
    )
    assert dictSeen["bReadOnly"] is True, (
        "the deposited DOI is editable, but its source is the "
        "envelope record"
    )
    assert dictSeen["sReferenceInput"] == "", (
        "the DOI was written into the point-at-someone-else's-deposit "
        "input: " + str(dictSeen)
    )
