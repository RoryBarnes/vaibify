"""An unproven envelope row reads orange, and the two dead-end rows act.

Three defects the researcher hit in one sitting, all in the Project
block, all invisible to the Python suite because it never renders the
page.

**The mirror row painted "nobody looked" as "it differs."** The row was
``bMatched ? green : red``, while the per-file octocats beside it speak
a three-state vocabulary in which orange means "not checked" and red
means "checked, and it differs" -- the badge stylesheet says so in as
many words. So a project whose GitHub verify had simply never covered
an envelope file got a red alarm next to that same file's honest orange
badge, and the two surfaces contradicted each other about one fact. The
GATE is unchanged and still blocks on unproven; only the colour now
distinguishes "not proven" from "proven wrong", which is the
distinction the researcher acts on -- a verify versus a push.

**The Software row asked a question with no way to answer it.** The L3
gate accepts a waiver or a non-empty declaration, and an empty list is
neither, so a pure-Python project sat at "?" forever while the row's
only guidance was "Add package...". The waiver was reachable solely as
a side effect of deleting the last declared package.

**The Dockerfile row had nothing to click.** The image is built from
vaibify's own packaged Dockerfiles, which are not in the researcher's
repository, so the row was red by construction.

The seeded host project has never run a remote verify, which is
exactly the unproven state the first assertion needs; it declares no
binaries, which is the second.
"""

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow


pytestmark = pytest.mark.browser

S_LEVEL3_GROUP = "publishedEnvelope"

# Reads the LEVEL CELL state, not a row class: the cell is what the
# researcher actually looks at, and `level-cell-<state>` is the single
# owner of that markup (fsBuildLevelCell).
_S_READ_ROW_STATES = """(sGroup) => {
    const listHeaders = Array.from(document.querySelectorAll(
        '.requirement-group-header'));
    const elHeader = listHeaders.find(
        el => (el.dataset.group || '') === sGroup);
    if (!elHeader) return {bFound: false};
    const elGroup = elHeader.closest('.requirement-group');
    return {
        bFound: true,
        listRows: Array.from(
            elGroup.querySelectorAll('.requirement-row')).map(el => ({
                sTitle: (el.querySelector('.requirement-row-title')
                    || {}).textContent || '',
                listCellStates: Array.from(
                    el.querySelectorAll('.step-level-cell')).map(
                        c => Array.from(c.classList).find(
                            n => n.startsWith('level-cell-')) || ''),
            })),
    };
}"""

_S_READ_ACTIONS = """() => Array.from(
    document.querySelectorAll('[data-wf-action]')).map(
        el => el.dataset.wfAction)"""

# Every action that DISCARDS something the researcher recorded, paired
# with whether the button is painted as destructive. Asserting the
# marked ones alone would pass against a stylesheet that painted every
# button red, so a recording action is checked as a control.
_S_READ_DANGER_BY_ACTION = """() => {
    const dict = {};
    document.querySelectorAll('[data-wf-action]').forEach(el => {
        dict[el.dataset.wfAction] =
            el.classList.contains('wf-action-danger');
    });
    return dict;
}"""


def _fnExpandEverything(pageDashboard):
    """Open every group and every row so all details render.

    IDEMPOTENT, because the expansion lives in JS module state that
    outlives a workflow re-entry: a second test in the same page would
    otherwise click every already-open row CLOSED and then assert
    against an empty detail. That failure looks exactly like the
    feature being absent, which is the wrong diagnosis to hand a
    reader.
    """
    iGroups = pageDashboard.locator(".requirement-group-header").count()
    for iIndex in range(iGroups):
        elGroup = pageDashboard.locator(
            ".requirement-group-header",
        ).nth(iIndex)
        if elGroup.evaluate(
            "el => el.closest('.requirement-group')"
            ".querySelectorAll('.requirement-row').length === 0",
        ):
            elGroup.click()
    pageDashboard.wait_for_selector(
        ".requirement-row-title", timeout=10000,
    )
    iRows = pageDashboard.locator(".requirement-row-header").count()
    for iIndex in range(iRows):
        elRow = pageDashboard.locator(
            ".requirement-row-header",
        ).nth(iIndex)
        if elRow.evaluate(
            "el => !el.closest('.requirement-row')"
            ".classList.contains('expanded')",
        ):
            elRow.click()


@pytest.mark.falsification
def test_the_project_block_stops_making_unactionable_claims(
    pageDashboard, serverHub,
):
    """One open, three assertions -- the seeded project is leased.

    A second ``fnOpenTheSeededHostWorkflow`` in this file is refused
    with "In use in another browser session", which is the
    one-session-per-container rule working correctly, so the three
    defects share one page rather than one test each.

    Kills:
      - restoring ``sState: bMatched ? "green" : "red"`` paints the
        mirror cells ``level-cell-none`` -- an alarm saying the
        published envelope DIFFERS, about files no verify has looked
        at;
      - removing the ``declare-no-binaries`` button leaves the
        Software row at "?" with no reachable way to answer it;
      - removing the ``copy-image-dockerfile`` button leaves the
        Dockerfile row red with nothing to click.
    """
    fnOpenTheSeededHostWorkflow(
        pageDashboard, serverHub, bAwaitProjectBlock=True,
    )
    _fnExpandEverything(pageDashboard)

    dictSection = pageDashboard.evaluate(
        _S_READ_ROW_STATES, S_LEVEL3_GROUP,
    )
    assert dictSection["bFound"], "the Published envelope section is gone"

    listMirrorRows = [
        dictRow for dictRow in dictSection["listRows"]
        if "mirror" in dictRow["sTitle"].lower()
        or "archive" in dictRow["sTitle"].lower()
    ]
    assert listMirrorRows, (
        "no GitHub mirror / Zenodo archive rows: "
        f"{[r['sTitle'] for r in dictSection['listRows']]}"
    )
    for dictRow in listMirrorRows:
        assert "level-cell-none" not in dictRow["listCellStates"], (
            f"{dictRow['sTitle'].strip()!r} claims the published "
            "envelope DIFFERS on a project that has never run a "
            "verify; orange (partial) is the honest state and red is "
            "an alarm the researcher cannot act on correctly"
        )
        assert "level-cell-partial" in dictRow["listCellStates"], (
            f"{dictRow['sTitle'].strip()!r} should read partially "
            f"met: {dictRow['listCellStates']}"
        )

    listActions = pageDashboard.evaluate(_S_READ_ACTIONS)
    assert "declare-no-binaries" in listActions, (
        "the Software row offers no way to declare that a project has "
        f"no standalone binaries: {sorted(set(listActions))}"
    )
    assert "copy-image-dockerfile" in listActions, (
        "the Dockerfile row offers no way to obtain a Dockerfile: "
        f"{sorted(set(listActions))}"
    )
    # A destructive action must not look like the recording action
    # beside it. "Delete rules..." sits directly under "Declare
    # rules" and the two were visually identical -- misread as a
    # duplicate button by the researcher who asked for this.
    dictDanger = pageDashboard.evaluate(_S_READ_DANGER_BY_ACTION)
    for sAction in ("declare-determinism", "scan-determinism"):
        assert dictDanger.get(sAction) is False, (
            f"{sAction} records or reads and must not be painted "
            f"destructive: {sorted(dictDanger)}"
        )

    assert pageDashboard.listPageErrors == []
