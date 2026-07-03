"""Regression tests: GitHub push and add-file cd into the project repo.

The old workspace-as-repo model stored workflow.json at
``/workspace/workflow.json`` and used its parent dir (``/workspace``)
as the git working directory. The workspace-as-git-repo migration
moved workflow.json into ``.vaibify/workflows/<name>.json`` inside
the project repo; the workflow file's parent is no longer a repo
root, so ``cd $(dirname workflow.json) && git add <relpath>`` now
fails with "no such directory" on every push. These tests pin the
routes to use ``dictWorkflow.sProjectRepoPath`` instead.
"""

import pytest
from fastapi import HTTPException
from unittest.mock import patch

from vaibify.gui.routes import syncRoutes


def test_helper_returns_project_repo_when_set():
    sResult = syncRoutes._fsRequireProjectRepoForGit(
        {"sProjectRepoPath": "/workspace/myrepo"},
    )
    assert sResult == "/workspace/myrepo"


def test_helper_raises_when_project_repo_missing():
    with pytest.raises(HTTPException) as excInfo:
        syncRoutes._fsRequireProjectRepoForGit({})
    assert excInfo.value.status_code == 409
    assert "git repository" in str(excInfo.value.detail).lower()


def test_helper_raises_on_empty_string_project_repo():
    """Empty string means workflow loaded but isn't under a git repo."""
    with pytest.raises(HTTPException) as excInfo:
        syncRoutes._fsRequireProjectRepoForGit({"sProjectRepoPath": ""})
    assert excInfo.value.status_code == 409


@pytest.fixture
def fixtureCapturedAddFileArgs():
    """Capture (sFilePath, sCommitMessage, sWorkdir) from the dispatcher."""
    return {}


def _fnPatchAddFileToGithub(fixtureCapturedAddFileArgs):
    """Patch the GitHub add-file dispatcher to capture args, not run git."""
    def _fnFakeAddFile(
        connectionDocker, sContainerId,
        sFilePath, sCommitMessage, sWorkdir,
    ):
        fixtureCapturedAddFileArgs["sFilePath"] = sFilePath
        fixtureCapturedAddFileArgs["sCommitMessage"] = sCommitMessage
        fixtureCapturedAddFileArgs["sWorkdir"] = sWorkdir
        return (0, "abc1234")
    return patch(
        "vaibify.gui.syncDispatcher.ftResultAddFileToGithub",
        side_effect=_fnFakeAddFile,
    )


def _fdictBuildContextWithRepoAt(sProjectRepoPath, sWorkflowPath):
    """Mimic the per-request dictCtx the route reads from."""
    dictWorkflow = {
        "sProjectRepoPath": sProjectRepoPath,
        "sWorkflowName": "demo",
        "listSteps": [],
        "dictRemotes": {"github": {"sRepo": "example/demo"}},
    }
    return {
        "workflows": {"cid": dictWorkflow},
        "paths": {"cid": sWorkflowPath},
        "require": lambda: None,
        "save": lambda sId, dictWf: None,
        "docker": object(),
    }


def _fnRunAddFileRoute(
    dictCtx, sFilePathInRequest, fixtureCapturedAddFileArgs,
):
    """Wire up the add-file route handler against dictCtx and invoke it."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.state.listLifespanStartup = []
    app.state.listLifespanShutdown = []
    syncRoutes.fnRegisterAll(app, dictCtx)
    with _fnPatchAddFileToGithub(fixtureCapturedAddFileArgs), patch(
        "vaibify.gui.routes.syncRoutes.fnValidatePathWithinRoot",
    ):
        return TestClient(app).post(
            "/api/github/cid/add-file",
            json={"sFilePath": sFilePathInRequest},
        )


def test_add_file_uses_project_repo_path_not_workflow_dirname(
    fixtureCapturedAddFileArgs,
):
    """sWorkdir handed to the dispatcher must be the project repo root."""
    dictCtx = _fdictBuildContextWithRepoAt(
        "/workspace/myrepo",
        "/workspace/myrepo/.vaibify/workflows/demo.json",
    )
    responseHttp = _fnRunAddFileRoute(
        dictCtx, "step01/output.dat", fixtureCapturedAddFileArgs,
    )
    assert responseHttp.status_code == 200
    assert fixtureCapturedAddFileArgs["sWorkdir"] == "/workspace/myrepo"
    assert fixtureCapturedAddFileArgs["sFilePath"] == "step01/output.dat"


def test_add_file_returns_409_when_workflow_lacks_project_repo(
    fixtureCapturedAddFileArgs,
):
    """A workflow with empty sProjectRepoPath surfaces 409, not a git error."""
    dictCtx = _fdictBuildContextWithRepoAt(
        "",
        "/workspace/.vaibify/workflows/demo.json",
    )
    responseHttp = _fnRunAddFileRoute(
        dictCtx, "any.dat", fixtureCapturedAddFileArgs,
    )
    assert responseHttp.status_code == 409
    assert "sWorkdir" not in fixtureCapturedAddFileArgs


@pytest.fixture
def fixtureCapturedPushArgs():
    return {}


def _fnPatchPushToGithub(fixtureCapturedPushArgs):
    def _fnFakePush(
        connectionDocker, sContainerId,
        listFilePaths, sCommitMessage, sWorkdir,
    ):
        fixtureCapturedPushArgs["listFilePaths"] = listFilePaths
        fixtureCapturedPushArgs["sWorkdir"] = sWorkdir
        return (0, "abc1234")
    return patch(
        "vaibify.gui.syncDispatcher.ftResultPushToGithub",
        side_effect=_fnFakePush,
    )


@pytest.fixture
def fixtureCapturedIdentityCommand():
    return {}


def _fnPatchExecuteCommand(fixtureCapturedIdentityCommand):
    """Capture the git config command issued by the identity route."""
    class _FakeDocker:
        def ftResultExecuteCommand(self, sContainerId, sCommand):
            fixtureCapturedIdentityCommand["sCommand"] = sCommand
            return (0, "")
    return _FakeDocker()


def _fnRunIdentityRoute(
    dictCtx, dictBody, fixtureCapturedIdentityCommand,
):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    dictCtx["docker"] = _fnPatchExecuteCommand(
        fixtureCapturedIdentityCommand)
    app = FastAPI()
    app.state.listLifespanStartup = []
    app.state.listLifespanShutdown = []
    syncRoutes.fnRegisterAll(app, dictCtx)
    return TestClient(app).post(
        "/api/github/cid/identity", json=dictBody,
    )


def test_identity_writes_git_config_in_project_repo(
    fixtureCapturedIdentityCommand,
):
    dictCtx = _fdictBuildContextWithRepoAt(
        "/workspace/myrepo",
        "/workspace/myrepo/.vaibify/workflows/demo.json",
    )
    responseHttp = _fnRunIdentityRoute(
        dictCtx,
        {"sName": "Rory Barnes", "sEmail": "rkb9@uw.edu"},
        fixtureCapturedIdentityCommand,
    )
    assert responseHttp.status_code == 200
    sCommand = fixtureCapturedIdentityCommand["sCommand"]
    assert "cd '/workspace/myrepo'" in sCommand
    assert "git config user.name 'Rory Barnes'" in sCommand
    assert "git config user.email 'rkb9@uw.edu'" in sCommand


def test_identity_rejects_malformed_email(
    fixtureCapturedIdentityCommand,
):
    dictCtx = _fdictBuildContextWithRepoAt(
        "/workspace/myrepo",
        "/workspace/myrepo/.vaibify/workflows/demo.json",
    )
    responseHttp = _fnRunIdentityRoute(
        dictCtx,
        {"sName": "Rory", "sEmail": "not-an-email"},
        fixtureCapturedIdentityCommand,
    )
    assert responseHttp.status_code == 400
    assert "sCommand" not in fixtureCapturedIdentityCommand


def test_identity_rejects_newline_in_name(
    fixtureCapturedIdentityCommand,
):
    dictCtx = _fdictBuildContextWithRepoAt(
        "/workspace/myrepo",
        "/workspace/myrepo/.vaibify/workflows/demo.json",
    )
    responseHttp = _fnRunIdentityRoute(
        dictCtx,
        {"sName": "Rory\nBarnes", "sEmail": "rkb9@uw.edu"},
        fixtureCapturedIdentityCommand,
    )
    assert responseHttp.status_code == 400


def test_identity_returns_409_when_no_project_repo(
    fixtureCapturedIdentityCommand,
):
    dictCtx = _fdictBuildContextWithRepoAt(
        "", "/workspace/.vaibify/workflows/demo.json",
    )
    responseHttp = _fnRunIdentityRoute(
        dictCtx,
        {"sName": "Rory", "sEmail": "rkb9@uw.edu"},
        fixtureCapturedIdentityCommand,
    )
    assert responseHttp.status_code == 409


def test_identity_shell_metacharacters_stay_inside_single_quotes(
    fixtureCapturedIdentityCommand,
):
    """Shell metacharacters in sName survive only as literal payload.

    The validator only rejects null bytes, newlines, and carriage
    returns. Every other shell metacharacter (``'``, ``"``, ``$``,
    backticks, ``;``, ``&``, ``|``) reaches ``fsShellQuote`` and must
    be wrapped in single quotes so the shell cannot interpret it.
    """
    sInjection = "'; rm -rf /; #"
    dictCtx = _fdictBuildContextWithRepoAt(
        "/workspace/myrepo",
        "/workspace/myrepo/.vaibify/workflows/demo.json",
    )
    responseHttp = _fnRunIdentityRoute(
        dictCtx,
        {"sName": sInjection, "sEmail": "ok@ok.org"},
        fixtureCapturedIdentityCommand,
    )
    assert responseHttp.status_code == 200
    sCommand = fixtureCapturedIdentityCommand["sCommand"]
    sExpectedQuoted = "'" + sInjection.replace("'", "'\\''") + "'"
    assert "git config user.name " + sExpectedQuoted in sCommand
    assert "rm -rf /" not in sCommand.replace(sExpectedQuoted, "")


def test_identity_shell_dollar_and_backtick_stay_inside_quotes(
    fixtureCapturedIdentityCommand,
):
    """``$(...)`` and backticks in sEmail must not be evaluated."""
    sName = "Name $USER `whoami` $(id)"
    dictCtx = _fdictBuildContextWithRepoAt(
        "/workspace/myrepo",
        "/workspace/myrepo/.vaibify/workflows/demo.json",
    )
    responseHttp = _fnRunIdentityRoute(
        dictCtx,
        {"sName": sName, "sEmail": "ok@ok.org"},
        fixtureCapturedIdentityCommand,
    )
    assert responseHttp.status_code == 200
    sCommand = fixtureCapturedIdentityCommand["sCommand"]
    sExpectedQuoted = "'" + sName.replace("'", "'\\''") + "'"
    assert sExpectedQuoted in sCommand


@pytest.mark.parametrize("sEmail", [
    "rkb9@uw.edu",
    "name+filter@gmail.com",
    "first.last@subdomain.example.co.uk",
    "user_name@school.ac.uk",
    "x@y.z",
])
def test_identity_accepts_realistic_emails(
    fixtureCapturedIdentityCommand, sEmail,
):
    """Validator must not reject ordinary researcher email shapes."""
    dictCtx = _fdictBuildContextWithRepoAt(
        "/workspace/myrepo",
        "/workspace/myrepo/.vaibify/workflows/demo.json",
    )
    responseHttp = _fnRunIdentityRoute(
        dictCtx,
        {"sName": "Researcher", "sEmail": sEmail},
        fixtureCapturedIdentityCommand,
    )
    assert responseHttp.status_code == 200, (
        f"Expected {sEmail} to be accepted, got "
        f"{responseHttp.status_code}: {responseHttp.text}"
    )


@pytest.mark.parametrize("sEmail", [
    "@x.y",
    "x@",
    "x@y",
    "x@.com",
    "a b@c.d",
    "x.y",
    "x@y.",
])
def test_identity_rejects_obvious_malformed_emails(
    fixtureCapturedIdentityCommand, sEmail,
):
    """Validator must catch shapes that are clearly not emails."""
    dictCtx = _fdictBuildContextWithRepoAt(
        "/workspace/myrepo",
        "/workspace/myrepo/.vaibify/workflows/demo.json",
    )
    responseHttp = _fnRunIdentityRoute(
        dictCtx,
        {"sName": "Researcher", "sEmail": sEmail},
        fixtureCapturedIdentityCommand,
    )
    assert responseHttp.status_code == 400, (
        f"Expected {sEmail!r} to be rejected, got "
        f"{responseHttp.status_code}"
    )


def test_identity_command_omits_global_flag(
    fixtureCapturedIdentityCommand,
):
    """``--global`` must never appear; outside a repo git itself errors."""
    dictCtx = _fdictBuildContextWithRepoAt(
        "/workspace/myrepo",
        "/workspace/myrepo/.vaibify/workflows/demo.json",
    )
    responseHttp = _fnRunIdentityRoute(
        dictCtx,
        {"sName": "Researcher", "sEmail": "ok@ok.org"},
        fixtureCapturedIdentityCommand,
    )
    assert responseHttp.status_code == 200
    sCommand = fixtureCapturedIdentityCommand["sCommand"]
    assert "--global" not in sCommand
    assert "--system" not in sCommand


def test_identity_surfaces_git_failure_as_502(
    fixtureCapturedIdentityCommand,
):
    """Non-zero git config exit (e.g. cwd not a repo) returns 502."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    class _FakeDockerFailing:
        def ftResultExecuteCommand(self, sContainerId, sCommand):
            fixtureCapturedIdentityCommand["sCommand"] = sCommand
            return (128, "fatal: not in a git directory")

    dictCtx = _fdictBuildContextWithRepoAt(
        "/workspace/notARepo",
        "/workspace/notARepo/.vaibify/workflows/demo.json",
    )
    dictCtx["docker"] = _FakeDockerFailing()
    app = FastAPI()
    app.state.listLifespanStartup = []
    app.state.listLifespanShutdown = []
    syncRoutes.fnRegisterAll(app, dictCtx)
    responseHttp = TestClient(app).post(
        "/api/github/cid/identity",
        json={"sName": "Researcher", "sEmail": "ok@ok.org"},
    )
    assert responseHttp.status_code == 502
    assert "not in a git directory" in responseHttp.text


def test_push_uses_project_repo_path_not_workflow_dirname(
    fixtureCapturedPushArgs,
):
    """The bulk push route shares the same cwd discipline."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    dictCtx = _fdictBuildContextWithRepoAt(
        "/workspace/myrepo",
        "/workspace/myrepo/.vaibify/workflows/demo.json",
    )
    app = FastAPI()
    app.state.listLifespanStartup = []
    app.state.listLifespanShutdown = []
    syncRoutes.fnRegisterAll(app, dictCtx)
    with _fnPatchPushToGithub(fixtureCapturedPushArgs), patch(
        "vaibify.gui.routes.syncRoutes._fnRequireNetworkAccess",
    ), patch(
        "vaibify.gui.routes.syncRoutes._fnValidateGithubPushPaths",
    ), patch(
        "vaibify.gui.routes.syncRoutes._fnAssertGithubTokenBoundToRemote",
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
    assert fixtureCapturedPushArgs["sWorkdir"] == "/workspace/myrepo"


def _fnRunPushRoute(dictCtx, sContainerId, fixtureCapturedPushArgs,
                    listVerifyCalls, iPushExit=0):
    """Drive the push route with the verify hook captured, not run."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    def _fnFakePush(
        connectionDocker, sContainerIdArg,
        listFilePaths, sCommitMessage, sWorkdir,
    ):
        fixtureCapturedPushArgs["listFilePaths"] = listFilePaths
        fixtureCapturedPushArgs["sWorkdir"] = sWorkdir
        return (iPushExit, "abc1234" if iPushExit == 0 else "boom")

    def _fnFakeVerify(dictWorkflow, sService, filesRepo):
        listVerifyCalls.append(sService)
        return {"sService": sService}

    app = FastAPI()
    app.state.listLifespanStartup = []
    app.state.listLifespanShutdown = []
    syncRoutes.fnRegisterAll(app, dictCtx)
    with patch(
        "vaibify.gui.syncDispatcher.ftResultPushToGithub",
        side_effect=_fnFakePush,
    ), patch(
        "vaibify.gui.routeContext.fdictRunRemoteVerifyBlocking",
        side_effect=_fnFakeVerify,
    ), patch(
        "vaibify.gui.routes.syncRoutes._fnRequireNetworkAccess",
    ), patch(
        "vaibify.gui.routes.syncRoutes._fnValidateGithubPushPaths",
    ), patch(
        "vaibify.gui.routes.syncRoutes._fnAssertGithubTokenBoundToRemote",
    ), patch(
        "vaibify.gui.routes.scriptRoutes._fnStoreCommitHash",
    ):
        return TestClient(app).post(
            "/api/github/" + sContainerId + "/push",
            json={
                "listFilePaths": ["step01/output.dat"],
                "sCommitMessage": "msg",
            },
        )


def test_push_success_refreshes_github_verify_cache(
    fixtureCapturedPushArgs,
):
    """FALSIFICATION TARGET: after a successful push the route must
    re-verify GitHub once, so the L2 cells clear their stale unknown
    without a manual refresh-remotes click (researcher request
    2026-07-02: 'once a user pushes, vaibify should know')."""
    dictCtx = _fdictBuildContextWithRepoAt(
        "/workspace/myrepo",
        "/workspace/myrepo/.vaibify/workflows/demo.json",
    )
    dictCtx["workflows"]["cid-verify-ok"] = dictCtx["workflows"]["cid"]
    dictCtx["paths"]["cid-verify-ok"] = dictCtx["paths"]["cid"]
    listVerifyCalls = []
    responseHttp = _fnRunPushRoute(
        dictCtx, "cid-verify-ok", fixtureCapturedPushArgs,
        listVerifyCalls,
    )
    assert responseHttp.status_code == 200
    assert listVerifyCalls == ["github"], (
        "a successful push must trigger exactly one github verify"
    )


def test_push_failure_skips_the_verify_refresh(
    fixtureCapturedPushArgs,
):
    """A failed push must not re-verify: nothing reached the remote,
    so the cached status is as fresh as it was before."""
    dictCtx = _fdictBuildContextWithRepoAt(
        "/workspace/myrepo",
        "/workspace/myrepo/.vaibify/workflows/demo.json",
    )
    dictCtx["workflows"]["cid-verify-fail"] = dictCtx["workflows"]["cid"]
    dictCtx["paths"]["cid-verify-fail"] = dictCtx["paths"]["cid"]
    listVerifyCalls = []
    responseHttp = _fnRunPushRoute(
        dictCtx, "cid-verify-fail", fixtureCapturedPushArgs,
        listVerifyCalls, iPushExit=1,
    )
    assert listVerifyCalls == [], (
        "a failed push must not touch the verify cache"
    )


def test_push_missing_manifest_warns_in_response(
    fixtureCapturedPushArgs,
):
    """FALSIFICATION TARGET (live gap 2026-07-02): the post-push
    verify died on a missing MANIFEST.sha256 with only a hub-log
    trace — the researcher saw "pushed" and an unexplained unknown
    L2. The failure must surface in the response, with the
    manifest-specific remedy and no raw exception text."""
    dictCtx = _fdictBuildContextWithRepoAt(
        "/workspace/myrepo",
        "/workspace/myrepo/.vaibify/workflows/demo.json",
    )
    dictCtx["workflows"]["cid-verify-warn"] = dictCtx["workflows"]["cid"]
    dictCtx["paths"]["cid-verify-warn"] = dictCtx["paths"]["cid"]
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    def _fnFakePush(
        connectionDocker, sContainerIdArg,
        listFilePaths, sCommitMessage, sWorkdir,
    ):
        return (0, "abc1234")

    app = FastAPI()
    app.state.listLifespanStartup = []
    app.state.listLifespanShutdown = []
    syncRoutes.fnRegisterAll(app, dictCtx)
    with patch(
        "vaibify.gui.syncDispatcher.ftResultPushToGithub",
        side_effect=_fnFakePush,
    ), patch(
        "vaibify.gui.routeContext.fdictRunRemoteVerifyBlocking",
        side_effect=FileNotFoundError(
            "manifest not found: '/workspace/myrepo/MANIFEST.sha256'"
        ),
    ), patch(
        "vaibify.gui.routes.syncRoutes._fnRequireNetworkAccess",
    ), patch(
        "vaibify.gui.routes.syncRoutes._fnValidateGithubPushPaths",
    ), patch(
        "vaibify.gui.routes.syncRoutes._fnAssertGithubTokenBoundToRemote",
    ), patch(
        "vaibify.gui.routes.scriptRoutes._fnStoreCommitHash",
    ):
        responseHttp = TestClient(app).post(
            "/api/github/cid-verify-warn/push",
            json={
                "listFilePaths": ["step01/output.dat"],
                "sCommitMessage": "msg",
            },
        )
    assert responseHttp.status_code == 200
    dictBody = responseHttp.json()
    assert dictBody["bSuccess"] is True
    sWarning = dictBody.get("sPostPushVerifyWarning", "")
    assert "MANIFEST.sha256" in sWarning
    assert "Level 1" in sWarning
    assert "/workspace/myrepo" not in sWarning, (
        "the warning must not embed raw exception text"
    )


def test_push_skips_verify_when_github_not_configured(
    fixtureCapturedPushArgs,
):
    """A workflow with no dictRemotes.github entry has nothing to
    verify against: the post-push check must be skipped silently —
    no warning, no ReverifyConfigError noise on every plain-git
    push (review finding 2026-07-02)."""
    dictCtx = _fdictBuildContextWithRepoAt(
        "/workspace/myrepo",
        "/workspace/myrepo/.vaibify/workflows/demo.json",
    )
    dictCtx["workflows"]["cid"]["dictRemotes"] = {}
    dictCtx["workflows"]["cid-no-remote"] = dictCtx["workflows"]["cid"]
    dictCtx["paths"]["cid-no-remote"] = dictCtx["paths"]["cid"]
    listVerifyCalls = []
    responseHttp = _fnRunPushRoute(
        dictCtx, "cid-no-remote", fixtureCapturedPushArgs,
        listVerifyCalls,
    )
    assert responseHttp.status_code == 200
    assert responseHttp.json()["bSuccess"] is True
    assert listVerifyCalls == [], (
        "an unconfigured service must not be verified"
    )
    assert "sPostPushVerifyWarning" not in responseHttp.json()
