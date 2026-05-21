"""Regression tests: GitHub push refuses when token owner != remote owner.

The push endpoint must call the GitHub ``/user`` endpoint with the
resolved token and confirm the returned ``login`` matches the
remote's owner. If a mismatch is detected the push is refused with
HTTP 409 and ``ftResultPushToGithub`` is never invoked. This guards
against the confused-deputy case where a token issued for one user's
account is accidentally used to push to a fork owned by someone else.
"""

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from vaibify.gui.routes import syncRoutes
from vaibify.reproducibility import githubAuth


@pytest.fixture(autouse=True)
def fixtureClearLoginCache():
    """Each test starts with an empty /user cache."""
    githubAuth.fnClearTokenLoginCache()
    yield
    githubAuth.fnClearTokenLoginCache()


def _fdictBuildPushContext():
    """Build the per-request dictCtx the push route reads from."""
    dictWorkflow = {
        "sProjectRepoPath": "/workspace/myrepo",
        "sWorkflowName": "demo",
        "listSteps": [],
    }
    return {
        "workflows": {"cid": dictWorkflow},
        "paths": {"cid": "/workspace/myrepo/.vaibify/workflows/demo.json"},
        "require": lambda: None,
        "save": lambda sId, dictWf: None,
        "docker": object(),
    }


def _fnFakePushRecorder(dictCaptured):
    """Build a fake ftResultPushToGithub that records args."""
    def _fnFake(
        connectionDocker, sContainerId, listFilePaths,
        sCommitMessage, sWorkdir,
    ):
        dictCaptured["called"] = True
        return (0, "abc1234")
    return _fnFake


def _fnSetupPushApp(dictCtx, dictCaptured):
    """Build a FastAPI app with the push route registered."""
    app = FastAPI()
    app.state.listLifespanStartup = []
    app.state.listLifespanShutdown = []
    syncRoutes.fnRegisterAll(app, dictCtx)
    return app


def test_push_refuses_with_409_when_token_belongs_to_wrong_user():
    """Token login 'attacker' on a remote owned by 'victim' must 409."""
    dictCaptured = {}
    dictCtx = _fdictBuildPushContext()
    app = _fnSetupPushApp(dictCtx, dictCaptured)
    with patch(
        "vaibify.gui.containerGit.fsRemoteUrlInContainer",
        return_value="https://github.com/victim/myrepo.git",
    ), patch(
        "vaibify.reproducibility.githubAuth.fsResolveToken",
        return_value="ghp_fakeToken",
    ), patch(
        "vaibify.reproducibility.githubAuth._ftFetchLoginFresh",
        return_value=("attacker", ""),
    ), patch(
        "vaibify.gui.syncDispatcher.ftResultPushToGithub",
        side_effect=_fnFakePushRecorder(dictCaptured),
    ), patch(
        "vaibify.gui.routes.syncRoutes._fnRequireNetworkAccess",
    ), patch(
        "vaibify.gui.routes.syncRoutes._fnValidateGithubPushPaths",
    ):
        responseHttp = TestClient(app).post(
            "/api/github/cid/push",
            json={
                "listFilePaths": ["step01/output.dat"],
                "sCommitMessage": "msg",
            },
        )
    assert responseHttp.status_code == 409
    assert "attacker" in responseHttp.text
    assert "victim" in responseHttp.text
    assert "called" not in dictCaptured


def test_push_proceeds_when_token_owner_matches_remote_owner():
    """When the /user login matches the remote owner, push runs."""
    dictCaptured = {}
    dictCtx = _fdictBuildPushContext()
    app = _fnSetupPushApp(dictCtx, dictCaptured)
    with patch(
        "vaibify.gui.containerGit.fsRemoteUrlInContainer",
        return_value="https://github.com/victim/myrepo.git",
    ), patch(
        "vaibify.reproducibility.githubAuth.fsResolveToken",
        return_value="ghp_validToken",
    ), patch(
        "vaibify.reproducibility.githubAuth._ftFetchLoginFresh",
        return_value=("victim", ""),
    ), patch(
        "vaibify.gui.syncDispatcher.ftResultPushToGithub",
        side_effect=_fnFakePushRecorder(dictCaptured),
    ), patch(
        "vaibify.gui.routes.syncRoutes._fnRequireNetworkAccess",
    ), patch(
        "vaibify.gui.routes.syncRoutes._fnValidateGithubPushPaths",
    ), patch(
        "vaibify.gui.routes.scriptRoutes._fnStoreCommitHash",
    ):
        responseHttp = TestClient(app).post(
            "/api/github/cid/push",
            json={
                "listFilePaths": ["step01/output.dat"],
                "sCommitMessage": "msg",
            },
        )
    assert responseHttp.status_code == 200
    assert dictCaptured.get("called") is True


def test_push_refuses_when_user_endpoint_is_unreachable():
    """An empty login from /user must fail closed with 409."""
    dictCaptured = {}
    dictCtx = _fdictBuildPushContext()
    app = _fnSetupPushApp(dictCtx, dictCaptured)
    with patch(
        "vaibify.gui.containerGit.fsRemoteUrlInContainer",
        return_value="https://github.com/victim/myrepo.git",
    ), patch(
        "vaibify.reproducibility.githubAuth.fsResolveToken",
        return_value="ghp_orphanToken",
    ), patch(
        "vaibify.reproducibility.githubAuth._ftFetchLoginFresh",
        return_value=("", "GitHub /user unreachable"),
    ), patch(
        "vaibify.gui.syncDispatcher.ftResultPushToGithub",
        side_effect=_fnFakePushRecorder(dictCaptured),
    ), patch(
        "vaibify.gui.routes.syncRoutes._fnRequireNetworkAccess",
    ), patch(
        "vaibify.gui.routes.syncRoutes._fnValidateGithubPushPaths",
    ):
        responseHttp = TestClient(app).post(
            "/api/github/cid/push",
            json={
                "listFilePaths": ["step01/output.dat"],
                "sCommitMessage": "msg",
            },
        )
    assert responseHttp.status_code == 409
    assert "called" not in dictCaptured


def test_user_endpoint_response_is_cached():
    """Repeated lookups for the same token must hit /user only once."""
    iCallCount = {"count": 0}

    def _ftCount(sToken):
        iCallCount["count"] += 1
        return ("victim", "")

    with patch(
        "vaibify.reproducibility.githubAuth._ftFetchLoginFresh",
        side_effect=_ftCount,
    ):
        assert githubAuth.fsResolveTokenLoginOrEmpty(
            "ghp_token1") == "victim"
        assert githubAuth.fsResolveTokenLoginOrEmpty(
            "ghp_token1") == "victim"
        assert githubAuth.fsResolveTokenLoginOrEmpty(
            "ghp_token1") == "victim"
    assert iCallCount["count"] == 1


def test_owner_comparison_is_case_insensitive():
    """GitHub login 'Victim' must satisfy a remote owned by 'victim'."""
    with patch(
        "vaibify.reproducibility.githubAuth._ftFetchLoginFresh",
        return_value=("Victim", ""),
    ):
        githubAuth.fnAssertTokenOwnerBinding("t", "victim")


def test_assert_raises_on_empty_token():
    """An empty token must raise ValueError, not silently allow the push."""
    with pytest.raises(ValueError):
        githubAuth.fnAssertTokenOwnerBinding("", "victim")


def test_assert_raises_on_mismatch():
    """A login that doesn't match the owner raises ValueError."""
    with patch(
        "vaibify.reproducibility.githubAuth._ftFetchLoginFresh",
        return_value=("attacker", ""),
    ):
        with pytest.raises(ValueError) as excInfo:
            githubAuth.fnAssertTokenOwnerBinding("t", "victim")
    assert "attacker" in str(excInfo.value)
    assert "victim" in str(excInfo.value)


def test_parse_owner_repo_from_https_url():
    sOwner, sRepo = githubAuth.ftParseOwnerRepoFromRemoteUrl(
        "https://github.com/victim/myrepo.git",
    )
    assert (sOwner, sRepo) == ("victim", "myrepo")


def test_parse_owner_repo_from_ssh_url():
    sOwner, sRepo = githubAuth.ftParseOwnerRepoFromRemoteUrl(
        "git@github.com:victim/myrepo.git",
    )
    assert (sOwner, sRepo) == ("victim", "myrepo")


def test_parse_owner_repo_returns_empty_on_unknown_shape():
    sOwner, sRepo = githubAuth.ftParseOwnerRepoFromRemoteUrl(
        "ftp://example.org/foo/bar",
    )
    assert (sOwner, sRepo) == ("", "")
