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
