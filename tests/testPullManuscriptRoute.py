"""Tests for POST /api/overleaf/{id}/pull-manuscript.

The pull list must derive entirely from the Overleaf mirror listing
(never from request input), the target directory entirely from the
workflow's project-repo path, and the pulled copy must be self-
ignoring so it can never dirty the repo.
"""

import posixpath
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from vaibify.gui.routes.syncRoutes import _fnRegisterPullManuscript


S_CONTAINER_ID = "pull_ms_cid"
S_REPO = "/workspace/projectRepo"


def _fdictBuildWorkflow():
    return {
        "sProjectRepoPath": S_REPO,
        "sOverleafProjectId": "project1234",
        "dictRemotes": {"overleaf": {"sProjectId": "project1234"}},
        "listSteps": [],
    }


@pytest.fixture
def fixtureDocker():
    mockDocker = MagicMock()
    return mockDocker


@pytest.fixture
def fixtureClient(fixtureDocker):
    app = FastAPI()
    dictCtx = {
        "docker": fixtureDocker,
        "workflows": {S_CONTAINER_ID: _fdictBuildWorkflow()},
        "require": lambda: None,
    }
    _fnRegisterPullManuscript(app, dictCtx)
    with patch(
        "vaibify.gui.routes.syncRoutes._fnRequireNetworkAccess",
        lambda sId: None,
    ):
        yield TestClient(app), dictCtx


_LIST_MIRROR_TREE = [
    {"sPath": "main.tex", "sType": "blob", "iSize": 10, "sDigest": "a"},
    {"sPath": "sections/results.tex", "sType": "blob", "iSize": 9,
     "sDigest": "b"},
    {"sPath": "references.bib", "sType": "blob", "iSize": 8,
     "sDigest": "c"},
    {"sPath": "Figures/corner.pdf", "sType": "blob", "iSize": 7,
     "sDigest": "d"},
    {"sPath": "sections", "sType": "tree", "iSize": 0, "sDigest": "e"},
]


def test_pull_manuscript_happy_path(fixtureClient, fixtureDocker):
    """Tex/bib pull into <repo>/.vaibify/manuscript with a self-ignore."""
    clientHttp, _ = fixtureClient
    with patch(
        "vaibify.reproducibility.overleafMirror.flistListMirrorTree",
        return_value=_LIST_MIRROR_TREE,
    ), patch(
        "vaibify.gui.syncDispatcher.ftResultPullFromOverleaf",
        return_value=(0, "ok"),
    ) as mockPull:
        response = clientHttp.post(
            f"/api/overleaf/{S_CONTAINER_ID}/pull-manuscript",
        )
    assert response.status_code == 200
    dictBody = response.json()
    sExpectedTarget = posixpath.join(S_REPO, ".vaibify", "manuscript")
    assert dictBody["sManuscriptDirectory"] == sExpectedTarget
    # Figures and tree entries are excluded; only manuscript sources.
    assert dictBody["listPulledFiles"] == [
        "main.tex", "sections/results.tex", "references.bib",
    ]
    listCallArgs = mockPull.call_args[0]
    assert listCallArgs[3] == dictBody["listPulledFiles"]
    assert listCallArgs[4] == sExpectedTarget
    # The pulled copy must be self-ignoring so it cannot dirty the repo.
    tWriteArgs = fixtureDocker.fnWriteFile.call_args[0]
    assert tWriteArgs[1] == posixpath.join(
        sExpectedTarget, ".gitignore",
    )
    assert tWriteArgs[2] == b"*\n"


def test_pull_manuscript_requires_overleaf_binding(fixtureClient):
    """No bound Overleaf project maps to a 409 precondition."""
    clientHttp, dictCtx = fixtureClient
    dictCtx["workflows"][S_CONTAINER_ID]["sOverleafProjectId"] = ""
    response = clientHttp.post(
        f"/api/overleaf/{S_CONTAINER_ID}/pull-manuscript",
    )
    assert response.status_code == 409
    assert "Overleaf" in response.json()["detail"]


def test_pull_manuscript_empty_mirror_is_409_not_empty_success(
    fixtureClient,
):
    """A mirror with no manuscript sources refuses instead of
    reporting a vacuous success."""
    clientHttp, _ = fixtureClient
    with patch(
        "vaibify.reproducibility.overleafMirror.flistListMirrorTree",
        return_value=[],
    ), patch(
        "vaibify.gui.syncDispatcher.ftRefreshOverleafMirror",
        return_value=(False, "no mirror"),
    ):
        response = clientHttp.post(
            f"/api/overleaf/{S_CONTAINER_ID}/pull-manuscript",
        )
    assert response.status_code == 409
    assert "no manuscript sources" in response.json()["detail"]


def test_pull_manuscript_dispatch_failure_maps_to_502(
    fixtureClient, fixtureDocker,
):
    """A failed container pull surfaces as 502 and writes no ignore."""
    clientHttp, _ = fixtureClient
    with patch(
        "vaibify.reproducibility.overleafMirror.flistListMirrorTree",
        return_value=_LIST_MIRROR_TREE,
    ), patch(
        "vaibify.gui.syncDispatcher.ftResultPullFromOverleaf",
        return_value=(1, "fatal: could not read from remote"),
    ):
        response = clientHttp.post(
            f"/api/overleaf/{S_CONTAINER_ID}/pull-manuscript",
        )
    assert response.status_code == 502
    fixtureDocker.fnWriteFile.assert_not_called()
