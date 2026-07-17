"""Falsification tests for the declaration/push surfaces (2026-07-03).

Born from a cosmic-ray sweep of the ux/dashboard-clarity branch diff
(345 mutants on changed lines: 286 killed, 59 survived at 24 lines).
Each test below kills at least one previously-surviving mutant; the
machine-applicable mutation is recorded in the ``Kills:`` docstring
line and registered in ``tests/falsificationRegistry.py`` so
``tools/reconfirmFalsification.py`` can re-confirm the kill.

Oracles are derived from the documented contracts (docstrings written
before the mutants were found, the security scope rules in AGENTS.md,
and git's own semantics), not from re-reading the implementation.
"""

import asyncio
import json
import subprocess

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock, patch

from vaibify.gui import containerGit, routeContext, syncDispatcher
from vaibify.gui.actionCatalog import LIST_AGENT_ACTIONS
from vaibify.gui.routes import gitRoutes, repoRoutes
from vaibify.reproducibility import levelGates

pytestmark = pytest.mark.falsification


# ----------------------------------------------------------------------
# routeContext: post-push verify warning construction
# ----------------------------------------------------------------------


def test_generic_verify_failure_returns_the_generic_warning():
    """A verify failure with no specific remedy must still warn, with
    the service named, no raw exception text (redaction contract),
    and the L2 consequence stated.

    Kills: routeContext.py generic-branch return -> return ""
    (and the 22 operator mutants on the same concatenation).
    """
    sWarning = routeContext._fsPostPushVerifyWarning(
        "github", RuntimeError("token=SECRET boom"),
    )
    assert "github" in sWarning
    assert "L2" in sWarning
    assert "SECRET" not in sWarning, (
        "raw exception text must never reach the warning"
    )
    assert "boom" not in sWarning


def test_manifest_warning_requires_both_filenotfound_and_manifest():
    """The manifest-specific remedy fires only for a FileNotFoundError
    whose message names the manifest; either condition alone must fall
    through to the generic warning.

    Kills: routeContext.py 'isinstance(...) and "manifest" in ...'
    -> or.
    """
    sForRuntime = routeContext._fsPostPushVerifyWarning(
        "github", RuntimeError("manifest gone"),
    )
    assert "MANIFEST.sha256" not in sForRuntime
    sForOtherFile = routeContext._fsPostPushVerifyWarning(
        "github", FileNotFoundError("no such file: data.json"),
    )
    assert "MANIFEST.sha256" not in sForOtherFile
    sForManifest = routeContext._fsPostPushVerifyWarning(
        "github", FileNotFoundError("manifest not found"),
    )
    assert "MANIFEST.sha256" in sForManifest
    assert "Level 1" in sForManifest


# ----------------------------------------------------------------------
# actionCatalog: the untrack action stays user-only
# ----------------------------------------------------------------------


def test_untrack_catalog_entry_is_user_only():
    """Withdrawing the declaration from the published record is the
    researcher's call: the catalog must mark untrack-ai-declaration
    NOT agent-safe.

    Kills: actionCatalog.py untrack entry bAgentSafe False -> True.
    """
    listMatch = [
        dictAction for dictAction in LIST_AGENT_ACTIONS
        if dictAction["sName"] == "untrack-ai-declaration"
    ]
    assert len(listMatch) == 1
    assert listMatch[0]["bAgentSafe"] is False


# ----------------------------------------------------------------------
# containerGit: rm --cached guards
# ----------------------------------------------------------------------


def test_remove_cached_with_no_paths_issues_no_git_command():
    """An empty path list must short-circuit to success without ever
    reaching docker: 'git rm --cached -- ' with no pathspec is a
    malformed command.

    Kills: containerGit.py ftResultGitRemoveCachedInContainer empty-
    list guard 'if not listFilePaths: return (0, "")' -> disabled.
    """
    mockDocker = MagicMock()
    tResult = containerGit.ftResultGitRemoveCachedInContainer(
        mockDocker, "cid", [], sWorkspace="/workspace/DemoRepo",
    )
    assert tResult == (0, "")
    mockDocker.ftResultExecuteCommand.assert_not_called()


# ----------------------------------------------------------------------
# gitRoutes: untrack failure details carry the git message
# ----------------------------------------------------------------------


def _fdictBuildDeclarationWorkflowLocal():
    return {
        "sPlotDirectory": "Plot",
        "listSteps": [{
            "sName": "AI Declaration",
            "sStepKind": "ai-declaration",
            "sDirectory": "aiDeclaration",
            "sDeclarationFile": "aiDeclaration/AI_USAGE.md",
        }],
        "sProjectRepoPath": "/workspace/DemoRepo",
        "dictSyncStatus": {},
    }


def _fnBuildGitRoutesClient(mockDocker, dictWorkflow):
    app = FastAPI()
    dictCtx = {
        "require": MagicMock(),
        "docker": mockDocker,
        "workflows": {"cid-demo": dictWorkflow},
    }
    gitRoutes.fnRegisterAll(app, dictCtx)
    return TestClient(app)


def test_untrack_rm_failure_detail_carries_git_output():
    """The 409 must relay git's own explanation — a researcher cannot
    act on 'failed' without the reason git printed.

    Kills: gitRoutes.py rm-failure detail '(sOut or "").strip()'
    -> '(sOut and "").strip()'.
    """
    def _fExec(sContainerId, sCommand, **_kw):
        if "rm --cached" in sCommand:
            return (128, "fatal: pathspec 'AI_USAGE.md' did not match")
        return (0, "")

    mockDocker = MagicMock()
    mockDocker.ftResultExecuteCommand.side_effect = _fExec
    client = _fnBuildGitRoutesClient(
        mockDocker, _fdictBuildDeclarationWorkflowLocal(),
    )
    response = client.post(
        "/api/git/cid-demo/untrack-ai-declaration",
        json={"sPath": "aiDeclaration/AI_USAGE.md"},
    )
    assert response.status_code == 409
    assert "did not match" in response.json()["detail"]


def test_untrack_commit_failure_detail_carries_git_output():
    """Same contract for the 500 branch: rm succeeded, commit failed,
    and the git message must reach the researcher.

    Kills: gitRoutes.py commit-failure detail '(sOut or "").strip()'
    -> '(sOut and "").strip()'.
    """
    def _fExec(sContainerId, sCommand, **_kw):
        if "rm --cached" in sCommand:
            return (0, "")
        if "commit -m" in sCommand:
            return (1, "gpg failed to sign the data")
        return (0, "")

    mockDocker = MagicMock()
    mockDocker.ftResultExecuteCommand.side_effect = _fExec
    client = _fnBuildGitRoutesClient(
        mockDocker, _fdictBuildDeclarationWorkflowLocal(),
    )
    response = client.post(
        "/api/git/cid-demo/untrack-ai-declaration",
        json={"sPath": "aiDeclaration/AI_USAGE.md"},
    )
    assert response.status_code == 500
    assert "gpg failed" in response.json()["detail"]


# ----------------------------------------------------------------------
# repoRoutes: project-repo gate and push-files warning
# ----------------------------------------------------------------------


def test_after_push_gate_is_exact_equality_not_ordering():
    """The post-push verify fires only when the pushed repo IS the
    active workflow's project repo — exact string identity. The
    cosmic-ray sweep proved a lexicographic-comparison mutant
    survives when the fixture names happen to sort one way, so this
    drives BOTH orderings.

    Kills: repoRoutes.py '!=' on the project-repo gate -> '>'.
    """
    async def _fnDrive(sProjectName, sPushedName):
        dictCtx = {"workflows": {
            "cid": {"sProjectRepoPath": "/workspace/" + sProjectName},
        }}
        with patch(
            "vaibify.gui.routes.repoRoutes"
            ".fsRefreshVerifyCacheAfterPush",
            new_callable=AsyncMock, return_value="",
        ) as mockVerify:
            await repoRoutes._fsAfterRepoPushSuccess(
                dictCtx, "cid", sPushedName,
            )
        return mockVerify.await_count

    assert asyncio.run(_fnDrive("aaa", "zebra")) == 0, (
        "project repo sorting BELOW the pushed repo must not verify"
    )
    assert asyncio.run(_fnDrive("zebra", "aaa")) == 0, (
        "project repo sorting ABOVE the pushed repo must not verify"
    )
    assert asyncio.run(_fnDrive("same", "same")) == 1, (
        "pushing the project repo itself must verify"
    )


def test_push_files_response_carries_verify_warning():
    """The per-file push shares the warning contract: a failed
    post-push check must reach the response, or the panel toasts a
    success while L2 silently stays unknown.

    Kills: repoRoutes.py push-files 'if sWarning:' attach -> inverted.
    """
    sWarning = "Pushed, but the github status check failed"
    app = FastAPI()
    dictCtx = {
        "require": MagicMock(),
        "docker": MagicMock(),
        "workflows": {"cid": {"sProjectRepoPath": "/workspace/alpha"}},
    }
    repoRoutes.fnRegisterAll(app, dictCtx)
    with patch(
        "vaibify.gui.routes.repoRoutes._fnRequireTracked",
    ), patch(
        "vaibify.gui.syncDispatcher.ftResultPushToGithub",
        return_value=(0, "ok"),
    ), patch(
        "vaibify.gui.routes.repoRoutes.fsRefreshVerifyCacheAfterPush",
        new_callable=AsyncMock, return_value=sWarning,
    ):
        response = TestClient(app).post(
            "/api/repos/cid/alpha/push-files",
            json={"sCommitMessage": "m", "listFilePaths": ["a.txt"]},
        )
    assert response.status_code == 200
    assert response.json()["sPostPushVerifyWarning"] == sWarning


# ----------------------------------------------------------------------
# levelGates: declaration blockers, projections, and cell arithmetic
# ----------------------------------------------------------------------


def test_unattested_blocker_requires_a_declaration_step():
    """Only unattested AI-DECLARATION steps emit the
    ai-declaration-unattested blocker; an ordinary un-approved step
    must never appear in that list (its sign-off is an L1 concern).

    Kills: levelGates.py blocker comprehension 'fbStepIsAiDeclaration
    and not fbStepUserApproved' -> or.
    """
    dictWorkflow = {"listSteps": [
        {"sName": "Ordinary", "dictVerification": {"sUser": "untested"}},
        {"sName": "Decl A", "sStepKind": "ai-declaration",
         "dictVerification": {"sUser": "passed"}},
        {"sName": "Decl B", "sStepKind": "ai-declaration",
         "dictVerification": {"sUser": "untested"}},
    ]}
    listBlockers = levelGates._flistAiDeclarationLevel2Blockers(
        dictWorkflow,
    )
    assert [d["iStepIndex"] for d in listBlockers] == [2]


def test_attested_check_fails_closed_on_non_dict_workflow():
    """A malformed workflow can never read as attested, and a workflow
    with no declaration step is not attested either (bFound gate).

    Kills: levelGates.py fbWorkflowAiDeclarationAttested non-dict
    'return False' -> 'return True'.
    """
    assert levelGates.fbWorkflowAiDeclarationAttested("junk") is False
    assert levelGates.fbWorkflowAiDeclarationAttested(None) is False
    assert levelGates.fbWorkflowAiDeclarationAttested(
        {"listSteps": [{"sName": "plain step"}]},
    ) is False


def test_l3_projection_skips_workflow_scope_entries():
    """The per-step L3 failing-criteria index covers step-scoped
    entries only: workflow-scope entries (iStepIndex -1) and
    malformed indices must be skipped WITHOUT truncating the scan.

    Kills: levelGates.py projection guard 'continue' -> 'break'
    (plus the >= 0 / isinstance mutants on the same guard).
    """
    dictByStep = levelGates._fdictLevel3FailingCriteriaByStep([
        {"iStepIndex": -1, "sCriterion": "workflow-scope-entry"},
        {"iStepIndex": "3", "sCriterion": "stringly-typed-index"},
        {"sCriterion": "entry-with-no-index-at-all"},
        {"iStepIndex": 2, "listFailingCriteria": ["a", "b"]},
        {"iStepIndex": 5, "sCriterion": "dominant-only"},
    ])
    assert dictByStep == {2: {"a", "b"}, 5: {"dominant-only"}}


def _fdictBuildLevel2Context():
    return {
        "bHasRepo": True,
        "bGithubCacheStale": False,
        "bZenodoCacheStale": False,
        "bOverleafBound": False,
    }


def test_declaration_step_l2_counts_are_exact():
    """A declaration step's L2 cell counts exactly three criteria
    (github, zenodo, sign-off): attested with clean syncs reads 3/3;
    unattested reads 2/3. The dashboard fraction is the researcher's
    ground truth, so the arithmetic is pinned to exact values.

    Kills: levelGates.py declaration branch 'iSatisfied += 1' -> += 2.
    """
    dictStep = {"sStepKind": "ai-declaration"}
    tAttested = levelGates._ftStepLevel2Counts(
        dictStep, set(), _fdictBuildLevel2Context(),
    )
    assert tAttested == (3, 3, False)
    tUnattested = levelGates._ftStepLevel2Counts(
        dictStep, {"ai-declaration-unattested"},
        _fdictBuildLevel2Context(),
    )
    assert tUnattested == (2, 3, False)


def test_step_l3_counts_zero_without_repo():
    """No project repo means no sync truth: satisfaction is zero over
    the FULL criteria tuple, never a vacuous attainment.

    Kills: levelGates.py no-repo branch
    'return (0, len(_T_STEP_LEVEL3_CRITERIA))' -> (1, ...).
    """
    tCounts = levelGates._ftStepLevel3Counts(
        {"saOutputDataFiles": ["out.dat"]}, set(), {"bHasRepo": False},
    )
    assert tCounts == (0, len(levelGates._T_STEP_LEVEL3_CRITERIA))
    assert tCounts[1] == 6


def test_step_l3_satisfied_arithmetic_is_subtraction():
    """satisfied = applicable - failing, exactly. The fixture makes
    five criteria applicable with one failing so subtraction (4) and
    the surviving right-shift mutant (5 >> 1 = 2) disagree.

    Kills: levelGates.py 'iTotal - len(setApplicable & set(setFailing))'
    -> 'iTotal >> len(...)'.
    """
    dictStep = {
        "saOutputDataFiles": ["results/output.dat"],
        "bUnseededRandomnessWarning": True,
        "saDataCommands": ["simtool run config.in"],
    }
    listDeclared = [{"sBinaryPath": "/usr/local/bin/simtool"}]
    tCounts = levelGates._ftStepLevel3Counts(
        dictStep, {"missing-from-manifest"},
        {"bHasRepo": True, "listDeclaredBinaries": listDeclared},
    )
    assert tCounts == (4, 5)


def test_randomness_criterion_requires_literal_true():
    """The nondeterminism criterion applies only when the unseeded
    warning is the literal True — False and truthy non-booleans (a
    lint writing 1) must not create a requirement.

    Kills: levelGates.py 'bUnseededRandomnessWarning") is True'
    -> '== True'.
    """
    def _fsetFor(xFlagValue):
        return levelGates._fsetStepApplicableLevel3Criteria(
            {"bUnseededRandomnessWarning": xFlagValue}, [],
        )
    assert "nondeterminism-undeclared" in _fsetFor(True)
    assert "nondeterminism-undeclared" not in _fsetFor(False)
    assert "nondeterminism-undeclared" not in _fsetFor(1)


def test_binary_reference_reads_the_declared_path():
    """A step referencing a declared binary (by path or basename) has
    the capture criterion applicable; empty command lists never match.

    Kills: levelGates.py _fbStepReferencesAnyDeclaredBinary
    'dictEntry.get("sBinaryPath") or ""' -> 'and ""'.
    """
    listDeclared = [{"sBinaryPath": "/usr/local/bin/simtool"}]
    assert levelGates._fbStepReferencesAnyDeclaredBinary(
        ["simtool run config.in"], listDeclared,
    ) is True
    assert levelGates._fbStepReferencesAnyDeclaredBinary(
        [], listDeclared,
    ) is False
    assert levelGates._fbStepInvokesAnyKnownBinary(
        [], listDeclared,
    ) is False


# ----------------------------------------------------------------------
# syncDispatcher: the staged push against a REAL git repository
# ----------------------------------------------------------------------


class _RunShellConnection:
    """Docker-connection stand-in that runs commands in a local shell.

    The dispatcher builds one POSIX shell string and hands it to the
    connection; executing that exact string against a real git
    repository is the only way to test its git semantics (the
    2026-07-02 push bug survived every command-string assertion).
    """

    def ftResultExecuteCommand(self, sContainerId, sCommand):
        procResult = subprocess.run(
            ["sh", "-c", sCommand],
            capture_output=True, text=True,
        )
        return (
            procResult.returncode,
            procResult.stdout + procResult.stderr,
        )


def _fnRunGit(sRepoPath, *saArguments):
    subprocess.run(
        ["git", "-C", sRepoPath, *saArguments],
        check=True, capture_output=True,
    )


def _fsGitOutput(sRepoPath, *saArguments):
    return subprocess.run(
        ["git", "-C", sRepoPath, *saArguments],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


def _tBuildClonePair(pathTmp):
    """Return (sClonePath, sOriginPath): a work clone with upstream."""
    sSeed = str(pathTmp / "seed")
    (pathTmp / "seed").mkdir()
    _fnRunGit(sSeed, "init", "--initial-branch=main")
    _fnRunGit(sSeed, "config", "user.email", "test@example.com")
    _fnRunGit(sSeed, "config", "user.name", "Test")
    (pathTmp / "seed" / "base.txt").write_text("base\n")
    _fnRunGit(sSeed, "add", "base.txt")
    _fnRunGit(sSeed, "commit", "-m", "base")
    sOrigin = str(pathTmp / "origin.git")
    subprocess.run(
        ["git", "clone", "--bare", "--quiet", sSeed, sOrigin],
        check=True, capture_output=True,
    )
    sClone = str(pathTmp / "work")
    subprocess.run(
        ["git", "clone", "--quiet", sOrigin, sClone],
        check=True, capture_output=True,
    )
    _fnRunGit(sClone, "config", "user.email", "test@example.com")
    _fnRunGit(sClone, "config", "user.name", "Test")
    # The dispatcher command carries the production hardening flags,
    # and protocol.file.allow=never rightly refuses a plain-path
    # remote. git's ext transport is user-allowed under the same
    # policy, so the push stays real without weakening the flags.
    # (%s tokenizes on spaces — pytest tmp paths contain none.)
    _fnRunGit(
        sClone, "remote", "set-url", "origin",
        "ext::git %s " + sOrigin,
    )
    return sClone, sOrigin


def test_push_staged_pushes_an_already_committed_repo_real_git(
    tmp_path,
):
    """THE 2026-07-02 live bug, against real git: a repo whose work is
    already committed (nothing staged, ahead of origin) must still
    push. The old unconditional 'git commit && push' died on
    'nothing to commit' and never pushed while the UI toasted
    success — and every command-string test stayed green.

    Kills: syncDispatcher.py the '(git diff --cached --quiet || ...)'
    guard -> unconditional 'git commit &&'.
    """
    sClone, sOrigin = _tBuildClonePair(tmp_path)
    (tmp_path / "work" / "declaration.md").write_text("declared\n")
    _fnRunGit(sClone, "add", "declaration.md")
    _fnRunGit(sClone, "commit", "-m", "already committed")

    iExit, sOut = syncDispatcher.ftResultPushStagedToGithub(
        _RunShellConnection(), "cid", "unused message", sClone,
    )

    assert iExit == 0, "push must succeed: " + sOut
    assert _fsGitOutput(sClone, "rev-parse", "HEAD") == (
        _fsGitOutput(sOrigin, "rev-parse", "HEAD")
    ), "origin must have received the pre-existing commit"


def test_push_staged_commits_staged_changes_then_pushes_real_git(
    tmp_path,
):
    """The staged case against real git: staged changes are committed
    with the requested message and the commit reaches origin.

    Kills: syncDispatcher.py staged-chain 'git push' -> 'git push
    --dry-run' (a commit that never reaches origin).
    """
    sClone, sOrigin = _tBuildClonePair(tmp_path)
    (tmp_path / "work" / "base.txt").write_text("updated\n")
    _fnRunGit(sClone, "add", "base.txt")

    iExit, sOut = syncDispatcher.ftResultPushStagedToGithub(
        _RunShellConnection(), "cid", "staged update", sClone,
    )

    assert iExit == 0, "push must succeed: " + sOut
    assert _fsGitOutput(
        sOrigin, "log", "-1", "--format=%s",
    ) == "staged update"


# ----------------------------------------------------------------------
# gitRoutes: untrack-ai-declaration against a REAL git repository
# ----------------------------------------------------------------------


def _tBuildDeclarationRepo(pathTmp):
    """Return (client, sRepoPath): the untrack route wired to a real
    local repo through the shell-runner connection, with the
    declaration file committed and clean."""
    sRepo = str(pathTmp / "projectRepo")
    (pathTmp / "projectRepo").mkdir()
    _fnRunGit(sRepo, "init", "--initial-branch=main")
    _fnRunGit(sRepo, "config", "user.email", "test@example.com")
    _fnRunGit(sRepo, "config", "user.name", "Test")
    (pathTmp / "projectRepo" / "declaration.md").write_text("declared\n")
    (pathTmp / "projectRepo" / "other.txt").write_text("other\n")
    _fnRunGit(sRepo, "add", "declaration.md", "other.txt")
    _fnRunGit(sRepo, "commit", "-m", "base")
    dictWorkflow = {
        "sPlotDirectory": "Plot",
        "listSteps": [{
            "sName": "AI Declaration",
            "sStepKind": "ai-declaration",
            "sDirectory": "aiDeclaration",
            "sDeclarationFile": "declaration.md",
        }],
        "sProjectRepoPath": sRepo,
        "dictSyncStatus": {},
    }
    app = FastAPI()
    dictCtx = {
        "require": MagicMock(),
        "docker": _RunShellConnection(),
        "workflows": {"cid-real": dictWorkflow},
    }
    gitRoutes.fnRegisterAll(app, dictCtx)
    return TestClient(app), sRepo


def test_untrack_clean_declaration_really_untracks_real_git(tmp_path):
    """THE 2026-07-03 high-severity finding, against real git: a
    clean, committed declaration file must actually leave git
    tracking while staying on disk. The pathspec-commit variant
    (`git commit -- <path>`) records working-tree content instead of
    the staged deletion, fails with 'nothing to commit', and strands
    a half-staged index — invisible to every stubbed test.

    Kills: gitRoutes.py untrack commit call gaining
    'listFilePaths=[request.sPath]' (the pathspec bug).
    """
    client, sRepo = _tBuildDeclarationRepo(tmp_path)
    response = client.post(
        "/api/git/cid-real/untrack-ai-declaration",
        json={"sPath": "declaration.md"},
    )
    assert response.status_code == 200, response.json()
    assert response.json()["bSuccess"] is True
    sTracked = _fsGitOutput(sRepo, "ls-files")
    assert "declaration.md" not in sTracked.split("\n")
    assert (tmp_path / "projectRepo" / "declaration.md").exists(), (
        "the file must stay on disk"
    )
    assert _fsGitOutput(sRepo, "log", "-1", "--format=%s") == (
        "[vaibify] remove AI declaration from the repo"
    )
    assert "other.txt" in _fsGitOutput(sRepo, "ls-files"), (
        "unrelated tracked files must be untouched"
    )


def test_untrack_modified_declaration_untracks_not_commits_real_git(
    tmp_path,
):
    """The false-success case: with local modifications, the pathspec
    commit SUCCEEDS by committing the file's new content — reporting
    'removed' while git still tracks it. The honest behavior is the
    same as the clean case: untracked, content preserved.

    Kills: gitRoutes.py untrack commit call gaining
    'listFilePaths=[request.sPath]' (the false-success half).
    """
    client, sRepo = _tBuildDeclarationRepo(tmp_path)
    (tmp_path / "projectRepo" / "declaration.md").write_text(
        "locally edited\n",
    )
    response = client.post(
        "/api/git/cid-real/untrack-ai-declaration",
        json={"sPath": "declaration.md"},
    )
    assert response.status_code == 200, response.json()
    assert "declaration.md" not in (
        _fsGitOutput(sRepo, "ls-files").split("\n")
    ), "the file must actually leave git tracking"
    assert (
        tmp_path / "projectRepo" / "declaration.md"
    ).read_text() == "locally edited\n", (
        "local edits must survive the removal"
    )


def test_untrack_refuses_when_other_changes_staged_real_git(tmp_path):
    """A bare commit is only safe behind the staged-index refusal: a
    pre-staged unrelated file must produce a 409 and stay staged,
    never be swept into the removal commit.

    Kills: gitRoutes.py staged-index precheck 'if iExit != 0:'
    -> 'if iExit == 0:'.
    """
    client, sRepo = _tBuildDeclarationRepo(tmp_path)
    (tmp_path / "projectRepo" / "other.txt").write_text("staged edit\n")
    _fnRunGit(sRepo, "add", "other.txt")
    response = client.post(
        "/api/git/cid-real/untrack-ai-declaration",
        json={"sPath": "declaration.md"},
    )
    assert response.status_code == 409
    assert "staged" in response.json()["detail"].lower()
    assert "declaration.md" in _fsGitOutput(sRepo, "ls-files"), (
        "a refused removal must leave the declaration tracked"
    )
    sStaged = _fsGitOutput(sRepo, "diff", "--cached", "--name-only")
    assert sStaged == "other.txt", (
        "the researcher's staged work must be untouched"
    )
