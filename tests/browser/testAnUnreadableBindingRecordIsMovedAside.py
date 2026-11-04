"""The unreadable-record notice offers its own way out.

The notice used to say "Move it aside" with no button, so recovering
took a terminal on the host and a reload. Its button now asks the real
route to rename the record beside itself, and the banner then renders
the server's fresh answer in place of the unreadable row.

Driven against the real hub with a real unreadable record planted for
the seeded project, so the rename, the re-read and the render are all
real. WHAT THIS DOES NOT COVER: what the route refuses
(``tests/testBindingRecordSetAside.py``).
"""

import os

import pytest

from tests.browser.conftest import (
    S_HOST_PROJECT_READY,
    fnOpenTheSeededHostWorkflow,
)


pytestmark = pytest.mark.browser

BA_UNREADABLE = b"{ this is not a binding record"
S_INVALID_ROW = "#remoteBindingBanner [data-record-invalid]"

S_COUNT_NOTICES_THE_SERVER_GIVES = """async () => {
    const dictDescribed = await VaibifyApi.fdictGet(
        '/api/bindings/' + VaibifyApp.fsGetContainerId() +
        '?bFetchEvidence=false');
    return {
        bValid: dictDescribed.dictRecordState.bValid,
        iNotices: dictDescribed.listEntries.filter(
            (dictEntry) => dictEntry.bApplies && dictEntry.sNotice).length,
    };
}"""


@pytest.fixture
def sPlantedRecordPath():
    """Plant an unreadable record for the seeded project; remove what is left."""
    from vaibify.config import registryManager, remoteBindings
    sUuid = registryManager.fsProjectUuidFor(S_HOST_PROJECT_READY)
    sPath = remoteBindings.fsBindingPath(sUuid)
    os.makedirs(os.path.dirname(sPath), exist_ok=True)
    with open(sPath, "wb") as fileRecord:
        fileRecord.write(BA_UNREADABLE)
    yield sPath
    sDirectory = os.path.dirname(sPath)
    for sName in os.listdir(sDirectory):
        if sName.startswith(sUuid + ".json"):
            os.remove(os.path.join(sDirectory, sName))


def test_moving_an_unreadable_record_aside_replaces_its_notice(
    pageDashboard, serverHub, sPlantedRecordPath,
):
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    pageDashboard.wait_for_selector(S_INVALID_ROW + " button", timeout=15000)
    assert pageDashboard.inner_text(S_INVALID_ROW + " button") == "Move it aside"
    pageDashboard.click(S_INVALID_ROW + " button")
    pageDashboard.wait_for_function(
        f"() => !document.querySelector('{S_INVALID_ROW}')", timeout=15000)
    assert not os.path.lexists(sPlantedRecordPath)
    sDirectory, sName = os.path.split(sPlantedRecordPath)
    listSetAside = [
        sEntry for sEntry in os.listdir(sDirectory)
        if sEntry.startswith(sName + ".unreadable-")]
    assert len(listSetAside) == 1
    with open(os.path.join(sDirectory, listSetAside[0]), "rb") as fileAside:
        assert fileAside.read() == BA_UNREADABLE
    dictServer = pageDashboard.evaluate(S_COUNT_NOTICES_THE_SERVER_GIVES)
    assert dictServer["bValid"] is True
    assert pageDashboard.locator(
        "#remoteBindingBanner .remote-binding-row").count() == (
        dictServer["iNotices"])
