"""A quarantined container's tile says so, and explains why on click.

The picker must not render a quarantined container as somebody else's
locked session — the mislabel a researcher hit, where a zombie-process
quarantine read as "in use by another vaibify session". It carries a
clickable "quarantined" chip that opens a modal naming the unsettled
operations and the exact host remedy: the "why" a researcher would
otherwise have to reach an in-container agent to discover.

The registry and the detail route are answered by ``page.route`` here,
so what is driven is the real frontend render and click path over
canned server data — not the Docker adapter or the journal.
"""

import json

import pytest

pytestmark = pytest.mark.browser

S_REGISTRY_GLOB = "**/api/registry"
S_QUARANTINE_GLOB = "**/api/registry/*/quarantine"
S_NAME = "quarantined-demo"
S_NOTE = "the terminal process group could not be proven empty: 2 live member(s)"


def _fnAnswerJson(page, sGlob, dictBody):
    """Fulfil every request matching sGlob with a canned JSON body."""
    page.route(
        sGlob,
        lambda routeIntercepted: routeIntercepted.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(dictBody),
        ),
    )


def _fnServeQuarantinedContainer(page):
    """Make the picker render one quarantined container with detail."""
    _fnAnswerJson(page, S_QUARANTINE_GLOB, {
        "sName": S_NAME,
        "sJournalState": "QUARANTINED",
        "bQuarantined": True,
        "sReadState": "valid",
        "listRecords": [{
            "sOperationId": "op1",
            "sKind": "terminal",
            "sState": "NEEDS_RECONCILIATION",
            "sNote": S_NOTE,
            "sPreparedIso": "2026-08-12T15:19:22",
            "sInFlightIso": "2026-08-12T15:19:23",
            "sTarget": "deadbeefcafe",
        }],
        "sRemedy": f"vaibify reconcile {S_NAME}",
    })
    # The registry glob is registered LAST so it wins over the more
    # specific quarantine glob for the bare /api/registry path.
    _fnAnswerJson(page, S_REGISTRY_GLOB, {
        "listContainers": [{
            "sName": S_NAME,
            "sContainerId": "deadbeefcafe",
            "sStatus": "running",
            "bQuarantined": True,
            "sJournalState": "QUARANTINED",
        }],
        "listUnrecognized": [],
    })


def testTheChipReadsQuarantinedAndNotLocked(pageDashboard, serverHub):
    """The tile is an attention state, never the generic locked grey.

    Kills: rendering a quarantined container with the ``--locked`` class
    and the "in use by another session" message, which is the mislabel
    that sent a researcher to an agent to find the real cause.
    """
    _fnServeQuarantinedContainer(pageDashboard)
    pageDashboard.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    elChip = pageDashboard.wait_for_selector(
        ".containment-chip--quarantined", timeout=10000,
    )
    assert elChip.inner_text().strip().lower() == "quarantined"
    assert pageDashboard.query_selector(".container-tile--locked") is None
    assert pageDashboard.listPageErrors == []


def testClickingTheChipExplainsWhyAndNamesTheRemedy(pageDashboard, serverHub):
    """The modal carries the record's note and the host command.

    Kills: a chip that opens nothing, or a modal that states the
    quarantine without the specific reason or the way out — leaving the
    researcher exactly where the silent grey tile did.
    """
    _fnServeQuarantinedContainer(pageDashboard)
    pageDashboard.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    pageDashboard.wait_for_selector(
        ".containment-chip--quarantined", timeout=10000,
    )
    pageDashboard.click(".containment-chip--quarantined")
    elModal = pageDashboard.wait_for_selector("#modalInfo", timeout=10000)
    sText = elModal.inner_text()
    assert "could not be proven empty" in sText
    assert f"vaibify reconcile {S_NAME}" in sText
    assert pageDashboard.listPageErrors == []


def testTheRemedyCommandHasAWorkingCopyButton(pageDashboard, serverHub):
    """One click puts the exact remedy command on the clipboard.

    The command was born to be retyped from a modal into a host shell,
    which is exactly where a transcription slip costs a researcher a
    reconcile round-trip. The assertion is on the button's feedback
    state, driven in the real modal.
    """
    _fnServeQuarantinedContainer(pageDashboard)
    pageDashboard.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    pageDashboard.wait_for_selector(
        ".containment-chip--quarantined", timeout=10000,
    )
    pageDashboard.click(".containment-chip--quarantined")
    pageDashboard.wait_for_selector("#modalInfo", timeout=10000)
    elButton = pageDashboard.wait_for_selector(
        "#modalInfo .quarantine-copy-button", timeout=5000,
    )
    elButton.click()
    assert elButton.inner_text() == "Copied", (
        "the copy button gave no feedback, so the researcher cannot "
        "tell whether the command reached the clipboard"
    )
    assert pageDashboard.listPageErrors == []


S_RECONCILE_GLOB = "**/api/registry/*/reconcile"
S_STOP_GLOB = "**/api/containers/*/stop"


def _fnServeReconcilableContainer(page):
    """Serve the same quarantined container, dashboard-reconcilable."""
    _fnServeQuarantinedContainer(page)
    # Re-register the detail with the reconcilable flags; the later
    # registration wins, so the button renders.
    _fnAnswerJson(page, S_QUARANTINE_GLOB, {
        "sName": S_NAME,
        "sJournalState": "QUARANTINED",
        "bQuarantined": True,
        "sReadState": "valid",
        "listRecords": [{
            "sOperationId": "op1",
            "sKind": "terminal",
            "sState": "NEEDS_RECONCILIATION",
            "sNote": S_NOTE,
            "sPreparedIso": "2026-08-12T15:19:22",
            "sInFlightIso": "2026-08-12T15:19:23",
            "sTarget": "deadbeefcafe",
        }],
        "sRemedy": f"vaibify reconcile {S_NAME}",
        "bReconcilableHere": True,
        "bHostProject": False,
    })


def _felOpenReconcilableModal(page, serverHub):
    """Open the quarantine modal for the reconcilable container."""
    _fnServeReconcilableContainer(page)
    page.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    page.wait_for_selector(
        ".containment-chip--quarantined", timeout=10000,
    )
    page.click(".containment-chip--quarantined")
    page.wait_for_selector("#modalInfo", timeout=10000)
    return page.wait_for_selector(
        "#modalInfo .quarantine-reconcile-button", timeout=5000,
    )


def testReconcileButtonClearsTheQuarantineAndClosesTheModal(
    pageDashboard, serverHub,
):
    """Success path: the shown record ids are sent, the modal closes.

    The request body is captured off the wire so what is proven
    includes the concurrency guard — the modal reconciles the records
    it SHOWED, never whatever the journal holds by then.
    """
    listCapturedBodies = []
    pageDashboard.route(
        S_RECONCILE_GLOB,
        lambda routeIntercepted: (
            listCapturedBodies.append(
                json.loads(routeIntercepted.request.post_data)),
            routeIntercepted.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(
                    {"bReconciled": True, "listRecordNotes": []}),
            ),
        )[-1],
    )
    elButton = _felOpenReconcilableModal(pageDashboard, serverHub)
    elButton.click()
    pageDashboard.wait_for_selector(
        "#modalInfo", state="detached", timeout=5000,
    )
    assert listCapturedBodies == [
        {"listExpectedOperationIds": ["op1"]},
    ], "the modal did not send exactly the record ids it showed"
    assert pageDashboard.listPageErrors == []


def testRefusedReconcileShowsTheReasonAndOffersStopAndCertify(
    pageDashboard, serverHub,
):
    """Refusal path: reason on screen, then the kernel-proven exit.

    The stop must land BEFORE the second reconcile — stopping is what
    makes the proof conclusive — so the order of the two captured
    requests is asserted, not just their existence.
    """
    listCalls = []

    def fnAnswerReconcile(routeIntercepted):
        listCalls.append("reconcile")
        if listCalls.count("reconcile") == 1:
            routeIntercepted.fulfill(
                status=409,
                content_type="application/json",
                body=json.dumps({"detail": (
                    "operation op1: 1 process(es) outlived the terminal "
                    "exec in its recorded group"
                )}),
            )
            return
        routeIntercepted.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"bReconciled": True, "listRecordNotes": []}),
        )

    def fnAnswerStop(routeIntercepted):
        listCalls.append("stop")
        routeIntercepted.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"bSuccess": True}),
        )

    pageDashboard.route(S_RECONCILE_GLOB, fnAnswerReconcile)
    pageDashboard.route(S_STOP_GLOB, fnAnswerStop)
    elButton = _felOpenReconcilableModal(pageDashboard, serverHub)
    elButton.click()
    elStop = pageDashboard.wait_for_selector(
        "#modalInfo .quarantine-stop-certify-button", timeout=5000,
    )
    sOutcome = pageDashboard.inner_text(
        "#modalInfo .quarantine-reconcile-outcome",
    )
    assert "outlived" in sOutcome, (
        "the refusal reason never reached the screen: " + sOutcome
    )
    elStop.click()
    pageDashboard.wait_for_selector(
        "#modalInfo", state="detached", timeout=5000,
    )
    assert listCalls == ["reconcile", "stop", "reconcile"], listCalls
    assert pageDashboard.listPageErrors == []
