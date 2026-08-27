"""A per-file Zenodo sync opens the archive flow, never a direct publish.

A Zenodo "sync" publishes a new deposit VERSION, and a version
REPLACES the file set: vaibify clears the inherited files and uploads
exactly the selection. The per-file badge action used to POST the
archive route with one path, so a single click published a version
containing only that file and silently shrank the public record
(live, 2026-08-27 — a 27-file deposit was superseded by a
project.json-only version). The action now diverts to the archive
flow — for a project with no Zenodo connection that is the connection
setup, for a connected one the push modal serving the full
publication union — and the direct single-file publish is
unreachable.

Kills (confirmed, not assumed): removing the ``sZenodo`` divert from
``fnSyncFileToRemote`` leaves the click a dead end (the direct-post
branch is gone too), so no archive-flow surface appears and the
wait below times out.
"""

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow


pytestmark = pytest.mark.browser


def test_a_per_file_zenodo_sync_never_publishes_directly(
    pageDashboard, serverHub,
):
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    listArchivePosts = []
    pageDashboard.route(
        "**/api/zenodo/**/archive",
        lambda route: (
            listArchivePosts.append(route.request.url),
            route.fulfill(
                status=500,
                content_type="application/json",
                body='{"bSuccess": false}',
            ),
        ),
    )
    pageDashboard.evaluate(
        "() => VaibifySyncManager.fnSyncFileToRemote("
        "'sZenodo', 'Step/out.json', '')"
    )
    # The seeded host workflow has no Zenodo connection, so the
    # archive flow surfaces as the connection setup — the point is
    # that a PROJECT-LEVEL surface opens where a single-file publish
    # used to fire.
    pageDashboard.wait_for_selector(
        "#modalConnectionSetup", state="visible", timeout=5000,
    )
    sService = pageDashboard.evaluate(
        "() => document.getElementById("
        "'modalConnectionSetup').dataset.service"
    )
    assert sService == "zenodo"
    assert listArchivePosts == []
