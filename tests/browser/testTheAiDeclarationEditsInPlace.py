"""An AI model declaration is edited in place, and deleted on confirmation.

Declaring a corrected model id used to add a second entry beside the
wrong one, because declarations upsert on (vendor, model id). The
editor now saves an existing card through ``ai-models/update``, which
finds the entry by its ORIGINAL vendor and model id. This drives the
whole journey through the real routes and reads the saved project file,
because the screen and the file must agree: declare, correct the model
id, confirm there is still exactly one entry, then delete it through
the confirmation dialog.
"""

import json
import os
import time

import pytest

from tests.browser.conftest import (
    S_HOST_PROJECT_READY,
    S_HOST_WORKFLOW_NAME,
    fnOpenTheSeededHostWorkflow,
)


pytestmark = pytest.mark.browser

F_SAVE_WAIT_SECONDS = 15.0
F_RESPONSE_DELAY_SECONDS = 2.0


def _flistSavedModels(serverHub):
    sWorkflowPath = os.path.join(
        serverHub.sHome, S_HOST_PROJECT_READY, ".vaibify", "projects",
        S_HOST_WORKFLOW_NAME + ".json",
    )
    with open(sWorkflowPath) as fileWorkflow:
        dictWorkflow = json.load(fileWorkflow)
    return (dictWorkflow.get("dictAiProvenance") or {}).get(
        "listDeclaredModels") or []


def _flistWaitForSavedModels(serverHub, fbDone):
    fDeadline = time.monotonic() + F_SAVE_WAIT_SECONDS
    listModels = _flistSavedModels(serverHub)
    while not fbDone(listModels) and time.monotonic() < fDeadline:
        time.sleep(0.25)
        listModels = _flistSavedModels(serverHub)
    return listModels


def _fnHoldTheResponseBack(route):
    """Let the save land, then deliver its answer two seconds late."""
    responseSaved = route.fetch()
    time.sleep(F_RESPONSE_DELAY_SECONDS)
    route.fulfill(response=responseSaved)


def _fnFillCard(elCard, dictValues):
    for sField, sValue in dictValues.items():
        elCard.locator('[data-field="' + sField + '"]').fill(sValue)


@pytest.mark.falsification
def test_a_corrected_model_id_edits_the_one_declaration(
    pageDashboard, serverHub,
):
    """Kills: saving an edit by appending instead of replacing in place.

    The edit's response is held back after the file is written, which
    is the gap a slow runner opened in CI: the project file already
    named the new model while the card still carried the old key, and a
    Delete clicked then asked to remove a model that no longer existed.
    The editor now keeps every button on a card disabled until the
    saved list re-renders it, so this test clicks Delete as soon as the
    file changes and still deletes the right declaration.
    """
    pageDashboard.route("**/ai-models/update", _fnHoldTheResponseBack)
    fnOpenTheSeededHostWorkflow(
        pageDashboard, serverHub, bAwaitProjectBlock=True,
    )
    pageDashboard.click('.requirement-group-header[data-group="ai"]')
    pageDashboard.click('.requirement-row-header[data-req="aiModels"]')
    pageDashboard.click(".wf-edit-ai-declaration")
    elCard = pageDashboard.locator("#aiModelEditorCards .ai-model-card")
    _fnFillCard(elCard.first, {
        "sVendor": "Example Vendor", "sModelId": "model-one-and-two",
        "sUseStartDate": "2026-01-01", "sUseEndDate": "2026-02-01",
    })
    elCard.first.locator('[data-card-action="save"]').click()
    listModels = _flistWaitForSavedModels(serverHub, lambda l: len(l) == 1)
    assert [d["sModelId"] for d in listModels] == ["model-one-and-two"]

    pageDashboard.wait_for_selector(
        '#aiModelEditorCards [data-card-action="delete"]', timeout=10000,
    )
    _fnFillCard(elCard.first, {"sModelId": "model-two"})
    elCard.first.locator('[data-card-action="save"]').click()
    listModels = _flistWaitForSavedModels(
        serverHub, lambda l: [d["sModelId"] for d in l] == ["model-two"],
    )
    assert [d["sModelId"] for d in listModels] == ["model-two"], (
        "correcting the model id must edit the one declaration, not "
        f"add a second: {listModels}"
    )

    elCard.first.locator('[data-card-action="delete"]').click()
    pageDashboard.wait_for_selector("#modalConfirm", timeout=10000)
    pageDashboard.click("#btnConfirmOk")
    listModels = _flistWaitForSavedModels(serverHub, lambda l: not l)
    assert listModels == []
    assert pageDashboard.listPageErrors == []
