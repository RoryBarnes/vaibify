"""ETag / If-None-Match contract for ``GET /api/pipeline/{id}/file-status``.

A 100-step workflow's poll payload is 50-500 KB. Returning the full
body on every poll when nothing has changed wastes bandwidth and JSON
serialization cost. The route attaches an ``ETag`` derived from the
mtime vector + blocker counts + iSyncEpoch; a client that echoes the
prior tag in ``If-None-Match`` short-circuits to a 304 with an empty
body. Any genuine change advances the tag and forces a fresh 200.
"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from tests import testCoverageBoost as _module


@pytest.fixture
def clientEtag():
    """Provide an isolated TestClient backed by the mock docker."""
    from vaibify.gui import pipelineServer
    with patch.object(
        pipelineServer, "_fconnectionCreateDocker",
        _module._fmockCreateDockerBoost,
    ):
        app = pipelineServer.fappCreateApplication(
            sWorkspaceRoot="/workspace",
            sTerminalUserArg="testuser",
        )
    yield TestClient(
        app, headers={"X-Session-Token": app.state.sSessionToken},
    )


def _fnConnectAndPollOnce(clientEtag):
    """Connect to the mock container and return the first poll response."""
    responseConnect = clientEtag.post(
        f"/api/connect/{_module.S_CONTAINER_ID}",
        params={"sWorkflowPath": _module.S_WORKFLOW_PATH},
    )
    assert responseConnect.status_code == 200
    responseHttp = clientEtag.get(
        f"/api/pipeline/{_module.S_CONTAINER_ID}/file-status"
    )
    assert responseHttp.status_code == 200
    assert "etag" in {k.lower() for k in responseHttp.headers.keys()}
    return responseHttp


def test_file_status_returns_etag_header(clientEtag):
    """A 200 response carries a quoted SHA-256 ETag."""
    responseHttp = _fnConnectAndPollOnce(clientEtag)
    sEtag = responseHttp.headers.get("etag", "")
    assert sEtag.startswith('"') and sEtag.endswith('"')
    # SHA-256 hex digest is 64 chars; trim quotes before measuring.
    assert len(sEtag.strip('"')) == 64


def test_file_status_identical_poll_returns_304(clientEtag):
    """A repeated poll with If-None-Match matching the prior tag is 304."""
    responseFirst = _fnConnectAndPollOnce(clientEtag)
    sEtag = responseFirst.headers["etag"]
    responseSecond = clientEtag.get(
        f"/api/pipeline/{_module.S_CONTAINER_ID}/file-status",
        headers={"If-None-Match": sEtag},
    )
    assert responseSecond.status_code == 304
    # The body must be empty when 304 is returned.
    assert responseSecond.content in (b"", None)
    # The ETag header echoes back so the client can keep tracking it.
    assert responseSecond.headers.get("etag") == sEtag


def test_file_status_etag_stable_across_two_identical_polls(clientEtag):
    """Two consecutive polls with no underlying change emit the same tag."""
    responseOne = _fnConnectAndPollOnce(clientEtag)
    responseTwo = clientEtag.get(
        f"/api/pipeline/{_module.S_CONTAINER_ID}/file-status"
    )
    assert responseTwo.status_code == 200
    assert responseOne.headers["etag"] == responseTwo.headers["etag"]


def test_file_status_mtime_change_triggers_fresh_200(clientEtag):
    """A change in the underlying mtime vector advances the ETag."""
    from vaibify.gui.routes import pipelineRoutes
    responseFirst = _fnConnectAndPollOnce(clientEtag)
    sEtagFirst = responseFirst.headers["etag"]
    # Simulate a poll cycle where the mtime vector has advanced.
    dictSnapshot = responseFirst.json()
    dictMtimesNext = dict(dictSnapshot.get("dictModTimes") or {})
    if dictMtimesNext:
        sFirstKey = next(iter(dictMtimesNext))
        # Bump one mtime to model a file edit between polls.
        dictMtimesNext[sFirstKey] = str(
            int(float(dictMtimesNext[sFirstKey])) + 60,
        )
    else:
        dictMtimesNext = {"step1/data.csv": "1700000999"}
    dictSnapshotAdvanced = dict(dictSnapshot)
    dictSnapshotAdvanced["dictModTimes"] = dictMtimesNext
    sEtagAdvanced = pipelineRoutes._fsBuildFileStatusEtag(
        dictSnapshotAdvanced, iSyncEpoch=1,
    )
    assert sEtagAdvanced != sEtagFirst


def test_file_status_sync_epoch_change_changes_etag():
    """A sync-epoch bump invalidates the tag even with identical mtimes."""
    from vaibify.gui.routes import pipelineRoutes
    dictResponse = {
        "dictModTimes": {"a/b": "1"},
        "dictMaxMtimeByStep": {"0": 1},
        "iAICSLevel": 0,
        "iL1BlockerCount": 0,
        "iL2BlockerCount": 0,
        "iL3BlockerCount": 0,
    }
    sEtagOne = pipelineRoutes._fsBuildFileStatusEtag(
        dictResponse, iSyncEpoch=1,
    )
    sEtagTwo = pipelineRoutes._fsBuildFileStatusEtag(
        dictResponse, iSyncEpoch=2,
    )
    assert sEtagOne != sEtagTwo


def test_file_status_blocker_count_change_changes_etag():
    """A change in blocker counts forces a fresh payload."""
    from vaibify.gui.routes import pipelineRoutes
    dictBase = {
        "dictModTimes": {"a/b": "1"},
        "dictMaxMtimeByStep": {"0": 1},
        "iAICSLevel": 1,
        "iL1BlockerCount": 0,
        "iL2BlockerCount": 0,
        "iL3BlockerCount": 0,
    }
    sEtagBase = pipelineRoutes._fsBuildFileStatusEtag(dictBase, iSyncEpoch=1)
    dictWithBlocker = dict(dictBase, iL1BlockerCount=1)
    sEtagBlocker = pipelineRoutes._fsBuildFileStatusEtag(
        dictWithBlocker, iSyncEpoch=1,
    )
    assert sEtagBase != sEtagBlocker
