"""Committing to fix a dirty tree is what creates the divergence.

Reported by the researcher on 2026-09-15, working through a live
publication. They were one commit behind origin with one uncommitted
canonical file. The drift banner refused to pull -- correctly, the tree
was dirty -- and offered "Commit state & Pull". The commit succeeded,
which turned "behind 1" into "ahead 1, behind 1", and the pull then
failed with nine lines of git's own hint text about merge and rebase.

So the button could not succeed in the state it exists for: its first
step destroys the precondition for its second. And the refusal a
researcher met at the end offered no action at all.

This is also the first-run path for almost everyone. Creating a GitHub
repository with "Add a README" checked -- the default -- puts one
commit on the remote that the local repository has never had.
"""

import pytest

from vaibify.gui import containerGit
from vaibify.gui.routes import gitRoutes


class _FakeDocker:
    """Answers the git probes, and records what it was asked to run."""

    def __init__(self, dictStatus, tPreview=(0, "tree-oid"), tMerge=(0, "")):
        self.dictStatus = dictStatus
        self.tPreview = tPreview
        self.tMerge = tMerge
        self.listCommands = []

    def ftResultExecuteCommand(self, sContainerId, sCommand):
        self.listCommands.append(sCommand)
        if "merge-tree" in sCommand:
            return self.tPreview
        if " merge " in sCommand:
            return self.tMerge
        return (0, "")


def _fdictStatus(iAhead=0, iBehind=0, dictFileStates=None):
    return {
        "sBranch": "main", "iAhead": iAhead, "iBehind": iBehind,
        "dictFileStates": dictFileStates or {},
    }


def _fnPatchStatus(monkeypatch, dictStatus):
    monkeypatch.setattr(
        containerGit, "fdictGitStatusInContainer",
        lambda docker, sId, sWorkspace=None: dictStatus,
    )


def test_a_clean_preview_is_not_the_same_as_an_unaskable_one():
    """Three states, because they lead to three different decisions."""
    assert containerGit.fdictDescribeMergePreview(0, "oid")["sState"] == (
        containerGit.S_MERGE_PREVIEW_CLEAN
    )
    dictConflict = containerGit.fdictDescribeMergePreview(
        1, "oid\nsrc/a.py\nsrc/b.py",
    )
    assert dictConflict["sState"] == containerGit.S_MERGE_PREVIEW_CONFLICTS
    assert dictConflict["listConflictPaths"] == ["src/a.py", "src/b.py"]
    # An older git cannot answer at all; that is not "clean".
    assert containerGit.fdictDescribeMergePreview(
        129, "error: unknown option `write-tree'",
    )["sState"] == containerGit.S_MERGE_PREVIEW_UNKNOWN


@pytest.mark.falsification
def test_a_diverged_branch_is_named_before_git_is_asked_to_try(
    monkeypatch,
):
    """The refusal must carry an action, not git's hint text.

    Left to ``git pull --ff-only``, this comes back as a 502 carrying
    nine lines of advice about merge and rebase: correct, unreadable,
    and offering nothing to click.

    Kills: dropping the ahead/behind check, which sends the pull to
    git and turns a structured refusal into a 502.
    """
    _fnPatchStatus(monkeypatch, _fdictStatus(iAhead=1, iBehind=1))
    dockerFake = _FakeDocker(_fdictStatus(iAhead=1, iBehind=1))
    dictResult = gitRoutes._fdictCheckCleanThenFastForward(
        {"docker": dockerFake}, "container", "/workspace/p",
    )
    assert dictResult["sRefusal"] == "diverged-branches"
    assert dictResult["iAhead"] == 1 and dictResult["iBehind"] == 1
    assert dictResult["dictMergePreview"]["sState"] == "clean"
    assert not any(
        "pull --ff-only" in sCommand
        for sCommand in dockerFake.listCommands
    ), "git was asked to attempt a fast-forward it cannot do"


def test_a_merely_behind_branch_still_fast_forwards(monkeypatch):
    """The new refusal must not capture the case that always worked."""
    _fnPatchStatus(monkeypatch, _fdictStatus(iBehind=1))
    dockerFake = _FakeDocker(_fdictStatus(iBehind=1))
    monkeypatch.setattr(
        gitRoutes, "_fnRecordFetchTime", lambda sId, sRepo: None,
    )
    monkeypatch.setattr(gitRoutes, "fnBumpSyncEpoch", lambda c, s: None)
    monkeypatch.setattr(
        containerGit, "fsGitHeadShaInContainer",
        lambda docker, sId, sWorkspace=None: "abc1234",
    )
    dictResult = gitRoutes._fdictCheckCleanThenFastForward(
        {"docker": dockerFake}, "container", "/workspace/p",
    )
    assert dictResult["bSuccess"] is True
    assert any(
        "pull --ff-only" in sCommand
        for sCommand in dockerFake.listCommands
    )


@pytest.mark.falsification
def test_a_conflicting_merge_is_refused_rather_than_half_applied(
    monkeypatch,
):
    """Git would stop with conflict markers and an unfinished MERGE_HEAD.

    That is a repository state the dashboard has no vocabulary for and
    the researcher did not ask for. Refusing leaves the tree as it
    was and names the files, so resolving them by hand stays their
    choice rather than their only remaining option.

    Kills: running the merge without consulting the preview first.
    """
    _fnPatchStatus(monkeypatch, _fdictStatus(iAhead=1, iBehind=1))
    dockerFake = _FakeDocker(
        _fdictStatus(iAhead=1, iBehind=1),
        tPreview=(1, "tree-oid\nMANIFEST.sha256"),
    )
    dictResult = gitRoutes._fdictCheckCleanThenMerge(
        {"docker": dockerFake}, "container", "/workspace/p",
    )
    assert dictResult["sRefusal"] == "merge-would-conflict"
    assert dictResult["dictMergePreview"]["listConflictPaths"] == [
        "MANIFEST.sha256"
    ]
    assert not any(
        " merge " in sCommand for sCommand in dockerFake.listCommands
    ), "a conflicting merge was started anyway"


@pytest.mark.falsification
def test_an_unaskable_preview_refuses_rather_than_assuming_clean(
    monkeypatch,
):
    """Unchecked is not clean.

    A host project on a git older than 2.38 cannot answer the preview
    at all. Treating that silence as a pass would run the merge on
    exactly the machines where nothing verified it was safe.

    Kills: comparing the preview against "conflicts" instead of
    against "clean" -- unknown would then fall through to the merge.
    """
    _fnPatchStatus(monkeypatch, _fdictStatus(iAhead=1, iBehind=1))
    dockerFake = _FakeDocker(
        _fdictStatus(iAhead=1, iBehind=1),
        tPreview=(129, "error: unknown option `write-tree'"),
    )
    dictResult = gitRoutes._fdictCheckCleanThenMerge(
        {"docker": dockerFake}, "container", "/workspace/p",
    )
    assert dictResult["sRefusal"] == "merge-would-conflict"
    assert dictResult["dictMergePreview"]["sState"] == "unknown"
    assert not any(
        " merge " in sCommand for sCommand in dockerFake.listCommands
    )


def test_the_preview_changes_nothing(monkeypatch):
    """``merge-tree --write-tree`` must never touch the working tree."""
    _fnPatchStatus(monkeypatch, _fdictStatus(iAhead=1, iBehind=1))
    dockerFake = _FakeDocker(_fdictStatus(iAhead=1, iBehind=1))
    gitRoutes._fdictCheckCleanThenFastForward(
        {"docker": dockerFake}, "container", "/workspace/p",
    )
    sPreview = next(
        sCommand for sCommand in dockerFake.listCommands
        if "merge-tree" in sCommand
    )
    assert "--write-tree" in sPreview
    for sForbidden in ("checkout", "reset", "git merge ", "commit"):
        assert sForbidden not in sPreview, sForbidden


def test_no_rebase_reaches_the_container():
    """Vaibify records commit SHAs; rewriting them strands them.

    The GitHub verify cache stores the commit it compared and the
    reproduction source stages the commit it ran, so a rebase orphans
    the references that make a published claim checkable. The absence
    is asserted rather than trusted to review.
    """
    import inspect
    sMerge = inspect.getsource(
        containerGit.ftResultGitMergeUpstreamInContainer,
    )
    assert "--no-ff" in sMerge
    assert "--no-edit" in sMerge, (
        "a container exec has no editor; git would block forever"
    )
    # No git subcommand that rewrites history may be composed
    # anywhere in this module, not merely in the merge helper.
    sModule = inspect.getsource(containerGit)
    for sForbidden in (
        "git rebase", "rebase --", "filter-branch", "--amend",
    ):
        assert sForbidden not in sModule, sForbidden


def test_the_merge_is_registered_as_user_only():
    """A merge commit in a researcher's history is their call."""
    from vaibify.gui.actionCatalog import LIST_AGENT_ACTIONS
    listEntry = [
        dictEntry for dictEntry in LIST_AGENT_ACTIONS
        if dictEntry["sName"] == "merge-upstream"
    ]
    assert len(listEntry) == 1
    assert listEntry[0]["bAgentSafe"] is False
