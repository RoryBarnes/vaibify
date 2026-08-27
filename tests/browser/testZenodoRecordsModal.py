"""The Zenodo Status modal's declared-records UI, driven in a browser.

The declared-record set is what the archive criteria consult
(2026-08-26): Zenodo's own GitHub integration archives a code release
as a separate record with its own DOI, and declaring that record here
is how the Level 3 envelope-in-archive check comes to see it. This
drives the whole loop — open the modal, declare a record by DOI, see
the row render, remove it, see the empty state return — through real
HTTP against the real routes, because the modal's JavaScript is
executed by nothing else.
"""

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow


pytestmark = pytest.mark.browser


def test_declaring_and_removing_a_zenodo_record_through_the_modal(
    pageDashboard, serverHub,
):
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)

    pageDashboard.evaluate(
        "() => VaibifyZenodoDepositCard.fnOpen("
        "VaibifyApp.fsGetContainerId())"
    )
    pageDashboard.wait_for_selector(
        "#zdcRecordsSection .zdc-record-add", timeout=10000,
    )
    assert pageDashboard.locator(".zdc-records-empty").count() == 1, (
        "a project with no declared records must say so rather than "
        "render an empty list"
    )

    pageDashboard.fill("#zdcRecordAddInput", "10.5281/zenodo.424242")
    pageDashboard.click("#zdcRecordAddButton")
    pageDashboard.wait_for_selector(".zdc-record-row", timeout=10000)
    sRow = pageDashboard.locator(".zdc-record-row").inner_text()
    assert "424242" in sRow, (
        "the declared record's id (derived from the DOI) must render "
        f"in its row: {sRow!r}"
    )

    pageDashboard.click(".zdc-record-remove")
    pageDashboard.wait_for_selector(".zdc-records-empty", timeout=10000)

    assert pageDashboard.listPageErrors == []
