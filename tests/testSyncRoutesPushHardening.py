"""GitHub push route hardening: interrupted pushes, bookkeeping, hashes.

Covers the failure modes behind the historical bare-500 push bug:

- A push exec that raises (e.g. docker ReadTimeout) while the push
  lands in the container -> probe confirms -> bSuccess True.
- A push exec that raises with an inconclusive probe -> HTTP 200 with
  sErrorType "indeterminate", never a 500.
- Bookkeeping (sync status, hash store, save) raising after a
  successful push -> bSuccess True plus sBookkeepingWarning, with the
  traceback logged to the "vaibify" logger.
- The commit hash comes from ``git rev-parse HEAD``, not from parsing
  merged stdout+stderr push output.
- The last-resort exception handler returns sanitized 500 JSON.
"""

import logging
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from vaibify.gui import containerGit
from vaibify.gui.routes import syncRoutes


S_CONTAINER_ID = "cid"
S_REPO = "/workspace/myrepo"
S_HEAD_SHA = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"


def _fdictBuildPushContext():
    """Plain dictCtx mirroring what the push route reads per request."""
    dictWorkflow = {
        "sProjectRepoPath": S_REPO,
        "sWorkflowName": "demo",
        "listSteps": [],
    }
    return {
        "workflows": {S_CONTAINER_ID: dictWorkflow},
        "paths": {S_CONTAINER_ID: S_REPO + "/.vaibify/workflows/d.json"},
        "require": lambda: None,
        "save": lambda sId, dictWf: None,
        "docker": object(),
    }


def _fclientBuildPushClient(dictCtx):
    """Register syncRoutes on a bare app and return a TestClient."""
    app = FastAPI()
    app.state.listLifespanStartup = []
    app.state.listLifespanShutdown = []
    syncRoutes.fnRegisterAll(app, dictCtx)
    return TestClient(app)


def _fdictPostPush(dictCtx, **dictPatches):
    """POST the push route with the standard guards neutralized."""
    clientHttp = _fclientBuildPushClient(dictCtx)
    with patch(
        "vaibify.gui.routes.syncRoutes._fnRequireNetworkAccess",
    ), patch(
        "vaibify.gui.routes.syncRoutes._fnAssertGithubTokenBoundToRemote",
    ):
        responseHttp = clientHttp.post(
            f"/api/github/{S_CONTAINER_ID}/push",
            json={
                "listFilePaths": ["step01/output.dat"],
                "sCommitMessage": "msg",
            },
        )
    return responseHttp


def _fdictLandedProbe():
    return {
        "bProbeConclusive": True, "bPushLanded": True,
        "sHeadSha": S_HEAD_SHA, "iAhead": 0, "iBehind": 0,
    }


def _fdictInconclusiveProbe():
    return {
        "bProbeConclusive": False, "bPushLanded": False,
        "sHeadSha": "", "iAhead": -1, "iBehind": -1,
    }


def _fdictRepoStatus():
    return {
        "bIsRepo": True, "sHeadSha": S_HEAD_SHA, "sBranch": "main",
        "iAhead": 0, "iBehind": 0, "dictFileStates": {},
        "sRefreshedAt": "2026-06-09T00:00:00Z", "sReason": "",
    }


def test_push_exec_raises_but_probe_confirms_success():
    """A ReadTimeout-style exec failure with a landed push returns success."""
    dictCtx = _fdictBuildPushContext()
    with patch(
        "vaibify.gui.syncDispatcher.ftResultPushToGithub",
        side_effect=Exception("UnixHTTPConnectionPool read timed out"),
    ), patch.object(
        containerGit, "fdictProbePushOutcome",
        return_value=_fdictLandedProbe(),
    ), patch.object(
        containerGit, "fsGitHeadShaInContainer",
        return_value=S_HEAD_SHA,
    ), patch.object(
        containerGit, "fdictGitStatusInContainer",
        return_value=_fdictRepoStatus(),
    ):
        responseHttp = _fdictPostPush(dictCtx)
    assert responseHttp.status_code == 200
    dictResult = responseHttp.json()
    assert dictResult["bSuccess"] is True
    assert dictResult["sCommitHash"] == S_HEAD_SHA
    assert dictResult["dictRemoteState"]["sHeadSha"] == S_HEAD_SHA


def test_push_exec_raises_and_probe_inconclusive_is_indeterminate():
    """An unverifiable push returns HTTP 200 indeterminate, never a 500."""
    dictCtx = _fdictBuildPushContext()
    with patch(
        "vaibify.gui.syncDispatcher.ftResultPushToGithub",
        side_effect=Exception("read timed out"),
    ), patch.object(
        containerGit, "fdictProbePushOutcome",
        return_value=_fdictInconclusiveProbe(),
    ):
        responseHttp = _fdictPostPush(dictCtx)
    assert responseHttp.status_code == 200
    dictResult = responseHttp.json()
    assert dictResult["bSuccess"] is False
    assert dictResult["sErrorType"] == "indeterminate"
    assert "Refresh" in dictResult["sMessage"]


def test_push_save_raising_yields_warning_not_500(caplog):
    """A bookkeeping failure keeps bSuccess True and logs the traceback."""
    dictCtx = _fdictBuildPushContext()

    def _fnRaiseOnSave(sId, dictWf):
        raise RuntimeError("disk full while saving workflow")

    dictCtx["save"] = _fnRaiseOnSave
    with patch(
        "vaibify.gui.syncDispatcher.ftResultPushToGithub",
        return_value=(0, "To github.com:owner/repo.git\nabc1234"),
    ), patch.object(
        containerGit, "fsGitHeadShaInContainer",
        return_value=S_HEAD_SHA,
    ), patch.object(
        containerGit, "fdictGitStatusInContainer",
        return_value=_fdictRepoStatus(),
    ), caplog.at_level(logging.ERROR, logger="vaibify"):
        responseHttp = _fdictPostPush(dictCtx)
    assert responseHttp.status_code == 200
    dictResult = responseHttp.json()
    assert dictResult["bSuccess"] is True
    assert "sBookkeepingWarning" in dictResult
    assert "RuntimeError" in caplog.text
    assert "Traceback" in caplog.text


def test_commit_hash_comes_from_rev_parse_not_output_parsing():
    """Stderr noise appended to push output must not become the hash."""
    dictCtx = _fdictBuildPushContext()
    sNoisyOutput = (
        "[main abc1234] msg\n"
        "To github.com:owner/repo.git\n"
        "   abc1234..def5678  main -> main"
    )
    with patch(
        "vaibify.gui.syncDispatcher.ftResultPushToGithub",
        return_value=(0, sNoisyOutput),
    ), patch.object(
        containerGit, "fsGitHeadShaInContainer",
        return_value=S_HEAD_SHA,
    ), patch.object(
        containerGit, "fdictGitStatusInContainer",
        return_value=_fdictRepoStatus(),
    ):
        responseHttp = _fdictPostPush(dictCtx)
    dictResult = responseHttp.json()
    assert dictResult["bSuccess"] is True
    assert dictResult["sCommitHash"] == S_HEAD_SHA
    assert "->" not in dictResult["sCommitHash"]


def test_push_stores_hash_under_repo_relative_sync_key():
    """Bookkeeping must land on the normalized dictSyncStatus key."""
    dictCtx = _fdictBuildPushContext()
    dictWorkflow = dictCtx["workflows"][S_CONTAINER_ID]
    with patch(
        "vaibify.gui.syncDispatcher.ftResultPushToGithub",
        return_value=(0, "pushed"),
    ), patch.object(
        containerGit, "fsGitHeadShaInContainer",
        return_value=S_HEAD_SHA,
    ), patch.object(
        containerGit, "fdictGitStatusInContainer",
        return_value=_fdictRepoStatus(),
    ):
        responseHttp = _fdictPostPush(dictCtx)
    assert responseHttp.json()["bSuccess"] is True
    dictEntry = dictWorkflow["dictSyncStatus"]["step01/output.dat"]
    assert dictEntry["sGithubCommit"] == S_HEAD_SHA
    assert dictEntry["bGithub"] is True


def test_add_file_exec_raises_and_probe_inconclusive_is_indeterminate():
    """The single-file route shares the indeterminate contract."""
    dictCtx = _fdictBuildPushContext()
    clientHttp = _fclientBuildPushClient(dictCtx)
    with patch(
        "vaibify.gui.syncDispatcher.ftResultAddFileToGithub",
        side_effect=Exception("read timed out"),
    ), patch.object(
        containerGit, "fdictProbePushOutcome",
        return_value=_fdictInconclusiveProbe(),
    ):
        responseHttp = clientHttp.post(
            f"/api/github/{S_CONTAINER_ID}/add-file",
            json={
                "sFilePath": "step01/output.dat",
                "sCommitMessage": "msg",
            },
        )
    assert responseHttp.status_code == 200
    dictResult = responseHttp.json()
    assert dictResult["bSuccess"] is False
    assert dictResult["sErrorType"] == "indeterminate"


def test_add_file_success_includes_rev_parse_hash():
    """The single-file route stamps the verified hash and remote state."""
    dictCtx = _fdictBuildPushContext()
    clientHttp = _fclientBuildPushClient(dictCtx)
    with patch(
        "vaibify.gui.syncDispatcher.ftResultAddFileToGithub",
        return_value=(0, "noise on stderr line"),
    ), patch.object(
        containerGit, "fsGitHeadShaInContainer",
        return_value=S_HEAD_SHA,
    ), patch.object(
        containerGit, "fdictGitStatusInContainer",
        return_value=_fdictRepoStatus(),
    ):
        responseHttp = clientHttp.post(
            f"/api/github/{S_CONTAINER_ID}/add-file",
            json={
                "sFilePath": "step01/output.dat",
                "sCommitMessage": "msg",
            },
        )
    dictResult = responseHttp.json()
    assert dictResult["bSuccess"] is True
    assert dictResult["sCommitHash"] == S_HEAD_SHA
    assert dictResult["dictRemoteState"]["sBranch"] == "main"


class _FakeProbeConnection:
    """Scripted ftResultExecuteCommand for probe unit tests."""

    def __init__(self, listResponses):
        self.listResponses = list(listResponses)
        self.iCalls = 0

    def ftResultExecuteCommand(self, sContainerId, sCommand):
        self.iCalls += 1
        objectNext = self.listResponses.pop(0)
        if isinstance(objectNext, Exception):
            raise objectNext
        return objectNext


def test_probe_push_outcome_retries_until_conclusive():
    """The probe survives an in-flight exec and confirms on a later try."""
    connection = _FakeProbeConnection([
        Exception("exec still busy"),
        (0, S_HEAD_SHA + "\n0\t0\n"),
    ])
    dictProbe = containerGit.fdictProbePushOutcome(
        connection, S_CONTAINER_ID, sWorkspace=S_REPO,
        iAttempts=3, fDelaySeconds=0.0,
    )
    assert dictProbe["bPushLanded"] is True
    assert dictProbe["sHeadSha"] == S_HEAD_SHA
    assert connection.iCalls == 2


def test_probe_push_outcome_reports_unlanded_push():
    """A conclusive probe with commits ahead never claims success."""
    connection = _FakeProbeConnection([
        (0, S_HEAD_SHA + "\n1\t0\n"),
        (0, S_HEAD_SHA + "\n1\t0\n"),
    ])
    dictProbe = containerGit.fdictProbePushOutcome(
        connection, S_CONTAINER_ID, sWorkspace=S_REPO,
        iAttempts=2, fDelaySeconds=0.0,
    )
    assert dictProbe["bProbeConclusive"] is True
    assert dictProbe["bPushLanded"] is False
    assert dictProbe["iAhead"] == 1


def test_probe_push_outcome_inconclusive_when_all_attempts_fail():
    """Exhausted retries report an inconclusive, unlanded probe."""
    connection = _FakeProbeConnection([
        (128, "fatal: not a git repository"),
        Exception("boom"),
    ])
    dictProbe = containerGit.fdictProbePushOutcome(
        connection, S_CONTAINER_ID, sWorkspace=S_REPO,
        iAttempts=2, fDelaySeconds=0.0,
    )
    assert dictProbe["bProbeConclusive"] is False
    assert dictProbe["bPushLanded"] is False


def test_last_resort_handler_returns_sanitized_500(caplog):
    """An unhandled route exception yields sanitized JSON, not a bare 500."""
    from unittest.mock import MagicMock
    from vaibify.gui import pipelineServer
    with patch.object(
        pipelineServer, "_fconnectionCreateDocker",
        return_value=MagicMock(),
    ):
        app = pipelineServer.fappCreateApplication(iExpectedPort=0)

    @app.get("/api/test-unhandled-boom")
    async def fnRaiseUnhandled():
        raise ValueError("secret host path /Users/someone/private")

    clientHttp = TestClient(
        app, raise_server_exceptions=False,
        headers={"X-Session-Token": app.state.sSessionToken},
    )
    with caplog.at_level(logging.ERROR, logger="vaibify"):
        responseHttp = clientHttp.get("/api/test-unhandled-boom")
    assert responseHttp.status_code == 500
    dictBody = responseHttp.json()
    assert "/Users/someone/private" not in dictBody["detail"]
    assert "Traceback" in caplog.text


def test_docker_client_timeout_raised():
    """The docker client must not keep the 60 s default read timeout."""
    from vaibify.docker import dockerConnection
    assert dockerConnection.I_DOCKER_CLIENT_TIMEOUT_SECONDS >= 600
