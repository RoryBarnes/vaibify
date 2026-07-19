"""Tests for the project-context routes and the host-import jail.

The context file (.vaibify/AGENTS.md) writes through dedicated
endpoints with a fixed server-side path, so the generic file route's
``.vaibify`` denylist stays intact — asserted here with a real
request against both routes. The host-import jail is exercised
adversarially with real filesystem fixtures: ``..`` traversal, an
absolute path outside home, and a symlink inside home pointing at a
system file must all be rejected after ``realpath`` resolution.
"""

import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from vaibify.gui.actionCatalog import SET_INTENTIONALLY_EXCLUDED_PATHS
from vaibify.gui.projectContextManager import (
    I_MAX_CONTEXT_CONTENT_BYTES,
    S_CONTEXT_TEMPLATE,
    fsValidateHostImportFile,
)
from vaibify.gui.routes.replayRoutes import fnRegisterAll


S_CONTAINER_ID = "context_cid"
S_REPO_PATH = "/workspace/exampleRepo"
S_CONTEXT_ABS_PATH = S_REPO_PATH + "/.vaibify/AGENTS.md"


class _StubDockerFiles:
    """In-memory stand-in for the container file surface."""

    def __init__(self):
        self.dictFiles = {}

    def fbaFetchFile(self, sContainerId, sFilePath):
        if sFilePath not in self.dictFiles:
            raise FileNotFoundError(sFilePath)
        return self.dictFiles[sFilePath]

    def fnWriteFile(self, sContainerId, sFilePath, baContent):
        self.dictFiles[sFilePath] = baContent


@pytest.fixture
def fixtureHarness():
    app = FastAPI()
    stubDocker = _StubDockerFiles()
    dictWorkflow = {"sProjectRepoPath": S_REPO_PATH, "listSteps": []}
    dictCtx = {
        "docker": stubDocker,
        "workflows": {S_CONTAINER_ID: dictWorkflow},
        "require": lambda: None,
        "save": lambda sId, dictWf: None,
    }
    fnRegisterAll(app, dictCtx)
    return TestClient(app), stubDocker


def _fsContextUrl(sSuffix=""):
    return (
        "/api/workflow/" + S_CONTAINER_ID + "/project-context" + sSuffix
    )


def test_read_reports_absent_file(fixtureHarness):
    clientTest, _ = fixtureHarness
    dictBody = clientTest.get(_fsContextUrl()).json()
    assert dictBody["bExists"] is False
    assert dictBody["sContent"] == ""


def test_write_then_read_round_trips(fixtureHarness):
    clientTest, stubDocker = fixtureHarness
    dictResponse = clientTest.put(
        _fsContextUrl(), json={"sContent": "# my project\n"},
    )
    assert dictResponse.status_code == 200
    assert stubDocker.dictFiles[S_CONTEXT_ABS_PATH] == b"# my project\n"
    dictBody = clientTest.get(_fsContextUrl()).json()
    assert dictBody["bExists"] is True
    assert dictBody["sContent"] == "# my project\n"


def test_write_over_cap_is_413(fixtureHarness):
    clientTest, _ = fixtureHarness
    sBig = "x" * (I_MAX_CONTEXT_CONTENT_BYTES + 1)
    dictResponse = clientTest.put(
        _fsContextUrl(), json={"sContent": sBig},
    )
    assert dictResponse.status_code == 413


def test_template_writes_then_refuses_overwrite(fixtureHarness):
    clientTest, stubDocker = fixtureHarness
    assert clientTest.post(
        _fsContextUrl("/template"),
    ).status_code == 200
    assert stubDocker.dictFiles[S_CONTEXT_ABS_PATH].decode(
        "utf-8",
    ) == S_CONTEXT_TEMPLATE
    assert clientTest.post(
        _fsContextUrl("/template"),
    ).status_code == 409


def test_import_from_host_file(fixtureHarness, tmp_path, monkeypatch):
    clientTest, stubDocker = fixtureHarness
    monkeypatch.setenv("HOME", str(tmp_path))
    pathSource = tmp_path / "myContext.md"
    pathSource.write_text("# imported context\n")
    dictResponse = clientTest.post(
        _fsContextUrl("/import"), json={"sHostPath": str(pathSource)},
    )
    assert dictResponse.status_code == 200
    assert stubDocker.dictFiles[S_CONTEXT_ABS_PATH] == (
        b"# imported context\n"
    )


def test_import_refuses_overwrite_without_flag(
    fixtureHarness, tmp_path, monkeypatch,
):
    clientTest, stubDocker = fixtureHarness
    monkeypatch.setenv("HOME", str(tmp_path))
    stubDocker.dictFiles[S_CONTEXT_ABS_PATH] = b"existing\n"
    pathSource = tmp_path / "myContext.md"
    pathSource.write_text("replacement\n")
    dictRefused = clientTest.post(
        _fsContextUrl("/import"), json={"sHostPath": str(pathSource)},
    )
    assert dictRefused.status_code == 409
    assert "bOverwrite" in dictRefused.json()["detail"]
    dictAccepted = clientTest.post(
        _fsContextUrl("/import"),
        json={"sHostPath": str(pathSource), "bOverwrite": True},
    )
    assert dictAccepted.status_code == 200
    assert stubDocker.dictFiles[S_CONTEXT_ABS_PATH] == b"replacement\n"


def test_import_jail_rejects_traversal_and_outside_paths(
    fixtureHarness, tmp_path, monkeypatch,
):
    clientTest, _ = fixtureHarness
    monkeypatch.setenv("HOME", str(tmp_path))
    listBadPaths = [
        str(tmp_path / ".." / "outsideHome.md"),
        "/etc/passwd",
        "relative/path.md",
    ]
    for sBadPath in listBadPaths:
        dictResponse = clientTest.post(
            _fsContextUrl("/import"), json={"sHostPath": sBadPath},
        )
        assert dictResponse.status_code == 400, sBadPath


def test_import_jail_rejects_symlink_escape(
    fixtureHarness, tmp_path, monkeypatch,
):
    clientTest, _ = fixtureHarness
    monkeypatch.setenv("HOME", str(tmp_path))
    pathLink = tmp_path / "sneaky.md"
    os.symlink("/etc/passwd", str(pathLink))
    dictResponse = clientTest.post(
        _fsContextUrl("/import"), json={"sHostPath": str(pathLink)},
    )
    assert dictResponse.status_code == 400


def test_validator_accepts_file_inside_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    pathGood = tmp_path / "context.md"
    pathGood.write_text("ok\n")
    assert fsValidateHostImportFile(str(pathGood)) == (
        os.path.realpath(str(pathGood))
    )


def test_import_route_is_excluded_from_agent_catalog():
    assert (
        "POST",
        "/api/workflow/{sContainerId}/project-context/import",
    ) in SET_INTENTIONALLY_EXCLUDED_PATHS


def test_generic_file_route_still_denylists_vaibify_writes(
    fixtureHarness,
):
    """The dedicated route must not have weakened the generic denylist."""
    from vaibify.gui.routes.fileRoutes import _fnRejectWriteDenylistedPath
    from fastapi import HTTPException as FastApiHttpException
    with pytest.raises(FastApiHttpException) as excInfo:
        _fnRejectWriteDenylistedPath(
            S_REPO_PATH + "/.vaibify/AGENTS.md", S_REPO_PATH,
        )
    assert excInfo.value.status_code == 403
