"""The two L3 controls must say they are working, or they look broken.

Both properties here are claims about the SCREEN, so a Python test of
the poll key or the opener proves nothing about either.

**The button.** Clicking Verify spends five to ten seconds on a
readiness round trip before any modal can appear, because which modal
to open depends on the answer. For that whole window the control looked
untouched, which reads as a click that did not register — so
researchers click again (reported 2026-08-31).

**The row.** A rerun takes as long as the workflow does. Until this
existed the Rebuild-attestation row sat red and still throughout, which
is indistinguishable from a run that never started.

Neither is asserted on its class alone. A class assertion passes
against a stylesheet with no matching rule at all, which is the failure
mode that makes a "verified" feedback affordance invisible in
production — so both read a COMPUTED style back off a live element.
"""

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow


pytestmark = pytest.mark.browser


@pytest.fixture(autouse=True)
def fixtureDropClaimsBetweenTests(serverHub):
    """Give every claim back after each test.

    The hub is module-scoped and the page is not, so a test that
    claims the project and stops leaves it owned by a lease nobody
    holds, and the next test's claim is refused by a session that no
    longer exists. The symptom is not a 409 but a locked tile
    intercepting the click, which reads like a UI bug in the feature
    under test.
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


def _fnAnswerReadinessReady(pageDashboard):
    """Make the readiness pre-flight say this project is ready.

    The body is built from the real ``fdictL3ReadinessGaps`` and
    wrapped in the route's real envelope key rather than hand-written.
    A hand-written flat payload is precisely what let the pre-flight
    ship reading the flags one level too high.
    """
    import json as jsonModule
    from vaibify.reproducibility.levelGates import fdictL3ReadinessGaps
    dictGaps = {
        sKey: (True if isinstance(objValue, bool) else objValue)
        for sKey, objValue in fdictL3ReadinessGaps(
            {}, "/nonexistent-repo-for-shape",
        ).items()
    }
    pageDashboard.route(
        "**/api/workflow/**/level3/readiness",
        lambda route: route.fulfill(
            status=200, content_type="application/json",
            body=jsonModule.dumps({
                "iProofLevel": 3, "dictL3ReadinessGaps": dictGaps,
            }),
        ),
    )


# Calls the REAL opener with a real button and reads the button back
# BEFORE awaiting it. The busy hold is applied synchronously, ahead of
# the opener's first await, so this observes exactly the window a
# researcher stares at -- without racing a timer or throttling a route,
# either of which would make the test's own timing the thing under
# test.
_S_DRIVE_THE_BUTTON = """async () => {
    const elButton = document.createElement("button");
    elButton.textContent = "Verify Level 3 reproducibility";
    document.body.appendChild(elButton);
    const promiseOpener = VaibifyApp.fnConfirmLevel3Verification(
        () => {}, elButton);
    const dictDuring = {
        bDisabled: elButton.disabled,
        sText: elButton.textContent,
        sCursor: window.getComputedStyle(elButton).cursor,
        sOpacity: window.getComputedStyle(elButton).opacity,
    };
    await promiseOpener;
    const dictAfter = {
        bDisabled: elButton.disabled,
        sText: elButton.textContent,
        sCursor: window.getComputedStyle(elButton).cursor,
    };
    elButton.remove();
    return {dictDuring: dictDuring, dictAfter: dictAfter};
}"""


# Renders the Project block twice -- a rerun running, and the same
# project idle -- and reads both back off the live document.
_S_DRIVE_THE_ATTESTATION_ROW = """() => {
    const fdictRenderWith = (bRunning) => {
        const sHtml = VaibifyWorkflowRequirements.fsRenderProjectBlock({
            dictWorkflowEnvelopeDetail: {
                bRebuildAttestationCurrent: false,
                bRebuildAttestationRunning: bRunning,
            },
            setExpandedRequirementGroups: new Set(["attestation"]),
            setExpandedRequirementRows: new Set(["rebuildAttestation"]),
        });
        const elHost = document.createElement("div");
        elHost.innerHTML = sHtml;
        document.body.appendChild(elHost);
        const elRow = Array.from(
            elHost.querySelectorAll('.requirement-row')).find(
                el => (el.textContent || '').indexOf(
                    'Rebuild attestation') !== -1);
        // The strip is always three cells, L1 | L2 | L3, and this
        // row gates Level 3 -- so the FIRST cell is the n/a dash for
        // Level 1 and reading it reports "not-applicable" no matter
        // what the row says.
        const listCells = Array.from(elRow.querySelectorAll(
            '.requirement-row-header .step-level-cell'));
        const elCell = listCells[listCells.length - 1];
        const listGroupCells = Array.from(elHost.querySelectorAll(
            '.requirement-group-header .step-level-cell'));
        const elGroupCell = listGroupCells[listGroupCells.length - 1];
        const dictAnswer = {
            sAnimation: window.getComputedStyle(elCell).animationName,
            sCellClass: elCell.className,
            sCellTitle: elCell.getAttribute('title') || '',
            sGroupCellClass: elGroupCell ? elGroupCell.className : '',
            sGroupCellMarkup: elGroupCell ? elGroupCell.innerHTML : '',
            sText: elRow.textContent || '',
            bHasVerifyButton: Boolean(
                elRow.querySelector('[data-wf-action="verify-l3"]')),
        };
        elHost.remove();
        return dictAnswer;
    };
    return {
        dictRunning: fdictRenderWith(true),
        dictIdle: fdictRenderWith(false),
    };
}"""


@pytest.mark.falsification
def test_the_verify_button_says_it_is_working_then_gives_itself_back(
    pageDashboard, serverHub,
):
    """The control is visibly busy while the pre-flight runs.

    Kills: in _ffnHoldButtonBusy, invert the guard to
    "if (elButton) return function () {};" -- the hold is never
    applied, the button sits unchanged through the whole round trip,
    and the during-state assertions fail.
    """
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    _fnAnswerReadinessReady(pageDashboard)
    dictSeen = pageDashboard.evaluate(_S_DRIVE_THE_BUTTON)

    dictDuring = dictSeen["dictDuring"]
    assert dictDuring["bDisabled"] is True, (
        "the button stays clickable through the readiness round trip, "
        "so a second click starts a second pre-flight"
    )
    assert "Checking" in dictDuring["sText"], (
        "the button's label never changes, so nothing on screen says "
        f"the click was received: {dictDuring['sText']!r}"
    )
    # Read back computed, not the class: asserting .btn-busy alone
    # passes against a stylesheet carrying no rule for it.
    assert dictDuring["sCursor"] == "progress", (
        "the busy class carries no style, so the button looks "
        f"identical to an idle one: cursor is {dictDuring['sCursor']!r}"
    )
    assert float(dictDuring["sOpacity"]) < 1.0, (
        f"the busy button is not dimmed: {dictDuring['sOpacity']!r}"
    )

    dictAfter = dictSeen["dictAfter"]
    assert dictAfter["bDisabled"] is False, (
        "the hold is never released, so the researcher who cancels "
        "the modal is left with a dead button"
    )
    assert "Checking" not in dictAfter["sText"], dictAfter["sText"]


@pytest.mark.falsification
def test_the_attestation_row_pulses_while_the_rerun_runs(
    pageDashboard, serverHub,
):
    """A running rerun is visible on the row, and idle is not.

    Kills: in _flistAttestationRows, pin "bChecking: false" -- the row
    renders still while a rerun runs, which is exactly what a rerun
    that never started looks like.
    """
    fnOpenTheSeededHostWorkflow(
        pageDashboard, serverHub, bAwaitProjectBlock=True,
    )
    dictSeen = pageDashboard.evaluate(_S_DRIVE_THE_ATTESTATION_ROW)

    dictRunning = dictSeen["dictRunning"]
    assert dictRunning["sAnimation"] == "pulse", (
        "a rerun that will run for hours renders a still row, which "
        "is what a rerun that never started also looks like: "
        f"animation is {dictRunning['sAnimation']!r}"
    )
    assert "level-cell-running" in dictRunning["sCellClass"], (
        f"the running state never reaches the cell: "
        f"{dictRunning['sCellClass']!r}"
    )
    # The tooltip is built from the state's own phrase. Borrowing
    # "partial" would paint the same orange and hover "partially met"
    # over a row where nothing is met yet.
    assert "being verified now" in dictRunning["sCellTitle"], (
        f"the hover text misdescribes a running rerun: "
        f"{dictRunning['sCellTitle']!r}"
    )
    assert dictRunning["bHasVerifyButton"] is False, (
        "the row still offers Verify while a verification is running, "
        "which the backend refuses -- the researcher is invited to "
        "click something that answers 409"
    )

    # The idle half: without it every assertion above could pass on a
    # row that pulses unconditionally.
    dictIdle = dictSeen["dictIdle"]
    assert dictIdle["sAnimation"] in ("none", ""), (
        "the row pulses when nothing is running, so the pulse says "
        f"nothing at all: {dictIdle['sAnimation']!r}"
    )
    assert dictIdle["bHasVerifyButton"] is True, (
        "an idle row offers no way to start a verification"
    )

    # The GROUP banner summarizes its rows through a rule shared with
    # the Steps banner, which knows nothing about "running" -- so it
    # counted the row as nothing assessed and painted a pulsing "?"
    # over a rerun plainly under way (researcher-reported).
    assert "level-cell-unknown" not in dictRunning["sGroupCellClass"], (
        "the Attestation banner renders a pulsing question mark over a "
        "running rerun, which reads as 'nothing has been checked': "
        f"{dictRunning['sGroupCellClass']!r}"
    )
    assert "level-cell-partial" in dictRunning["sGroupCellClass"], (
        "the banner does not summarize a running rerun as progress: "
        f"{dictRunning['sGroupCellClass']!r}"
    )
    assert "level-cell-circle" in dictRunning["sGroupCellMarkup"], (
        "the banner shows a glyph rather than the orange circle the "
        f"row shows: {dictRunning['sGroupCellMarkup']!r}"
    )


# Renders the row for a rebuild that RAN and failed -- the state the
# researcher was left with no explanation for.
_S_DRIVE_A_FAILED_ATTESTATION = """() => {
    const sHtml = VaibifyWorkflowRequirements.fsRenderProjectBlock({
        dictWorkflowEnvelopeDetail: {
            bRebuildAttestationCurrent: false,
            bRebuildAttestationRunning: false,
            dictRebuildAttestation: {
                sStatus: "failed",
                sAttestedAtUtc: "2026-09-01T18:36:24Z",
                iOutputHashesMatched: 23,
                iOutputHashesTotal: 23,
                listCarriedPaths: ["AI_USAGE.md"],
                listDivergedHashes: [
                    "pipeline rerun exited non-zero",
                    "step A02 'PlotHistogram' exited 1 during the rerun",
                ],
                dictRerunFailure: {
                    sKind: "step",
                    sStepLabel: "A02",
                    sStepName: "PlotHistogram",
                    iExitCode: 1,
                    listOutputTail: [
                        "Traceback (most recent call last):",
                        "ModuleNotFoundError: No module named 'scipy'",
                    ],
                },
            },
        },
        setExpandedRequirementGroups: new Set(["attestation"]),
        setExpandedRequirementRows: new Set(["rebuildAttestation"]),
    });
    const elHost = document.createElement("div");
    elHost.innerHTML = sHtml;
    document.body.appendChild(elHost);
    const elRow = Array.from(
        elHost.querySelectorAll('.requirement-row')).find(
            el => (el.textContent || '').indexOf(
                'Rebuild attestation') !== -1);
    const elOutput = elRow.querySelector('.attestation-failure-output');
    const dictAnswer = {
        sText: elRow.textContent || '',
        bHasFailureBlock: Boolean(
            elRow.querySelector('.attestation-failure')),
        sOutputText: elOutput ? elOutput.textContent : '',
        sOutputOverflow: elOutput
            ? window.getComputedStyle(elOutput).overflowY : 'no-box',
    };
    elHost.remove();
    return dictAnswer;
}"""


def test_a_failed_rebuild_says_what_broke_and_where(
    pageDashboard, serverHub,
):
    """The only surviving evidence must actually reach the screen.

    The shadow container is destroyed as soon as the comparison is
    made, so a researcher who is not shown the failing step and its
    output has nowhere else to look: they cannot re-run the shadow and
    cannot read its logs.
    """
    fnOpenTheSeededHostWorkflow(
        pageDashboard, serverHub, bAwaitProjectBlock=True,
    )
    dictSeen = pageDashboard.evaluate(_S_DRIVE_A_FAILED_ATTESTATION)

    assert dictSeen["bHasFailureBlock"] is True, (
        "a rebuild that ran and failed renders no failure block at all"
    )
    assert "A02" in dictSeen["sText"], (
        f"the failing step is never named: {dictSeen['sText']!r}"
    )
    assert "ModuleNotFoundError" in dictSeen["sOutputText"], (
        "the failing step's output never reaches the screen, so the "
        "researcher is told a step failed and not why: "
        f"{dictSeen['sOutputText']!r}"
    )
    # "Ran and failed" must never be reported as "never run" -- that
    # told a researcher whose rerun had just failed to go and run it.
    assert "No rebuild attempted yet" not in dictSeen["sText"], (
        f"a failed rebuild is described as never attempted: "
        f"{dictSeen['sText']!r}"
    )
    assert "did NOT reproduce" in dictSeen["sText"], dictSeen["sText"]
    # The carried file is named rather than folded into the ratio, so
    # "23 of 23 matched" beside a failure cannot read as a claim about
    # every file in the manifest.
    assert "carried in unchanged" in dictSeen["sText"], dictSeen["sText"]
    # A long traceback must scroll inside its own box rather than
    # stretching the Project block.
    assert dictSeen["sOutputOverflow"] == "auto", (
        f"the output box does not scroll: {dictSeen['sOutputOverflow']!r}"
    )


# The shape the researcher actually hit: preflight refused the run, so
# no step ever ran and there is no step to name.
_S_DRIVE_A_PREFLIGHT_REFUSAL = """() => {
    const sHtml = VaibifyWorkflowRequirements.fsRenderProjectBlock({
        dictWorkflowEnvelopeDetail: {
            bRebuildAttestationCurrent: false,
            bRebuildAttestationRunning: false,
            dictRebuildAttestation: {
                sStatus: "failed",
                iOutputHashesMatched: 23,
                iOutputHashesTotal: 23,
                listCarriedPaths: ["AI_USAGE.md"],
                listDivergedHashes: [
                    "the reproduction run failed",
                    "the run was stopped before any step could start: " +
                    "Step 2 'PlotHistogram': script plot.py not found",
                ],
                dictRerunFailure: {
                    sKind: "preflight",
                    listErrors: [
                        "Step 2 'PlotHistogram': script plot.py not found",
                    ],
                    listOutputTail: [],
                },
            },
        },
        setExpandedRequirementGroups: new Set(["attestation"]),
        setExpandedRequirementRows: new Set(["rebuildAttestation"]),
    });
    const elHost = document.createElement("div");
    elHost.innerHTML = sHtml;
    document.body.appendChild(elHost);
    const elRow = Array.from(
        elHost.querySelectorAll('.requirement-row')).find(
            el => (el.textContent || '').indexOf(
                'Rebuild attestation') !== -1);
    const dictAnswer = {
        sText: elRow.textContent || '',
        bHasFailureBlock: Boolean(
            elRow.querySelector('.attestation-failure')),
    };
    elHost.remove();
    return dictAnswer;
}"""


def test_a_run_refused_before_it_started_says_so_in_plain_words(
    pageDashboard, serverHub,
):
    """No step failed, so the row must not describe a failing step.

    This is the shape a researcher met: preflight refused the run, the
    collector had no stepFail to record, and the whole of what reached
    the screen was "pipeline rerun exited non-zero" -- a POSIX
    convention naming neither the cause nor the fact that nothing had
    run at all.
    """
    fnOpenTheSeededHostWorkflow(
        pageDashboard, serverHub, bAwaitProjectBlock=True,
    )
    dictSeen = pageDashboard.evaluate(_S_DRIVE_A_PREFLIGHT_REFUSAL)

    assert dictSeen["bHasFailureBlock"] is True, (
        "a refused run renders no failure block"
    )
    assert "stopped before any step could start" in dictSeen["sText"], (
        "the row does not say the run never started, so the "
        "researcher goes looking inside a step that never ran: "
        f"{dictSeen['sText']!r}"
    )
    assert "plot.py not found" in dictSeen["sText"], (
        f"the actual validation error never reaches the screen: "
        f"{dictSeen['sText']!r}"
    )
    # The jargon this replaced. "exited non-zero" names a POSIX
    # convention and tells a researcher nothing they can act on.
    assert "non-zero" not in dictSeen["sText"], (
        f"engineer jargon reached the researcher: {dictSeen['sText']!r}"
    )
