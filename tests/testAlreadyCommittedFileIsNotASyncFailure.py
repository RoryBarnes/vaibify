"""Pushing an already-committed file is a no-op, not a failure.

The single-file GitHub push ran ``add && commit && push``. A file that
is already committed and unchanged stages nothing, so ``git commit``
exits non-zero with "nothing added to commit", ``&&`` swallows the
push, and the researcher is told the sync "failed for an unrecognized
reason" -- about a file that was already on GitHub.

That is the ordinary state of a published file, and it is exactly the
state a researcher reaches for the push from: a badge is orange when
nothing has VERIFIED the file, which looks identical to a badge that is
orange because nothing has pushed it. So the one action available on an
orange badge failed precisely for the researcher who had already done
everything right.

This is the SAME defect ``ftResultPushToGithub`` and
``ftResultPushStagedToGithub`` were fixed for on 2026-07-02; the
single-file push was left behind, which is the class-versus-instance
trap this repository has recorded before.

The command is executed for real against a real git repository rather
than asserted as a string. A shell chain is not a string -- ``a && b ||
c && d`` parses as ``((a && b) || c) && d``, so the grouping is
load-bearing and a substring assertion would pass on a version that
runs the push after a FAILED add. The upstream is a local bare repo
reached over git's ``ext::`` transport, so nothing touches the network
and the production hardening flags stay in force.

Kills (confirmed, not assumed): reverting to the unconditional
``add && commit && push`` chain fails the no-op test; dropping the
grouping parentheses fails the failed-add test, but ONLY through its
dirty-index case -- the exit-code assertion alone passes against both
forms, which is why that test carries the extra setup.
"""

import os
import subprocess

import pytest

from vaibify.gui import syncDispatcher


class _LocalShellConnection:
    """Run the composed command in a real shell, as the container would.

    Returns the ``(iExitCode, sOutput)`` tuple the production
    connection returns; a stand-in with a richer shape would let the
    test pass against a caller that unpacks it wrongly.
    """

    def ftResultExecuteCommand(self, sContainerId, sCommand):
        processResult = subprocess.run(
            ["sh", "-c", sCommand], capture_output=True, text=True,
        )
        return (
            processResult.returncode,
            processResult.stdout + processResult.stderr,
        )


def _fnRunGit(sCwd, *aArgs):
    subprocess.run(
        ["git", *aArgs], cwd=sCwd, check=True, capture_output=True,
    )


@pytest.fixture
def tRepoWithRemote(tmp_path):
    """Return (work tree, tracked file) wired to a real upstream.

    The remote is reached through git's ``ext::`` transport, not a
    plain path: the production command carries
    ``protocol.file.allow=never``, which rightly refuses a local-path
    remote, and weakening the hardening to make a test pass would
    delete the thing being hardened. Pattern borrowed from
    ``testDeclarationPushMutationCoverage``.
    """
    sSeed = str(tmp_path / "seed")
    os.makedirs(sSeed)
    _fnRunGit(sSeed, "init", "-q")
    # Pin the branch rather than inheriting the machine's
    # init.defaultBranch, which differs between laptops and runners.
    _fnRunGit(sSeed, "symbolic-ref", "HEAD", "refs/heads/main")
    _fnRunGit(sSeed, "config", "user.email", "lane@example.invalid")
    _fnRunGit(sSeed, "config", "user.name", "Test Lane")
    _fnRunGit(sSeed, "config", "commit.gpgsign", "false")
    sTracked = "project.json"
    with open(os.path.join(sSeed, sTracked), "w") as fileHandle:
        fileHandle.write('{"sName": "example"}\n')
    _fnRunGit(sSeed, "add", "-A")
    _fnRunGit(sSeed, "commit", "-q", "-m", "seed")

    sOrigin = str(tmp_path / "origin.git")
    subprocess.run(
        ["git", "clone", "--bare", "--quiet", sSeed, sOrigin],
        check=True, capture_output=True,
    )
    sWork = str(tmp_path / "work")
    subprocess.run(
        ["git", "clone", "--quiet", sOrigin, sWork],
        check=True, capture_output=True,
    )
    _fnRunGit(sWork, "config", "user.email", "lane@example.invalid")
    _fnRunGit(sWork, "config", "user.name", "Test Lane")
    _fnRunGit(sWork, "config", "commit.gpgsign", "false")
    _fnRunGit(
        sWork, "remote", "set-url", "origin",
        "ext::git %s " + sOrigin,
    )
    return sWork, sTracked


def test_an_unchanged_committed_file_reports_success(tRepoWithRemote):
    """The reported defect, reproduced and fixed.

    Nothing to commit and nothing to push, so the honest answer is
    success — the published copy is already what the researcher has.
    """
    sWork, sTracked = tRepoWithRemote
    iExit, sOutput = syncDispatcher.ftResultAddFileToGithub(
        _LocalShellConnection(), "cid", sTracked, "sync", sWork,
    )
    dictResult = syncDispatcher.fdictSyncResult(iExit, sOutput)
    assert dictResult["bSuccess"], (
        "pushing an already-committed file reports a failure, so the "
        "one action an orange badge offers fails for the researcher "
        f"who already published: {dictResult}"
    )


def test_the_client_can_tell_a_no_op_from_real_work(tRepoWithRemote):
    """The output must let the toast avoid claiming work it did not do.

    The frontend says "Already published" rather than "Pushed" on this
    signal; without it the toast describes a push that never happened.
    """
    sWork, sTracked = tRepoWithRemote
    _iExit, sOutput = syncDispatcher.ftResultAddFileToGithub(
        _LocalShellConnection(), "cid", sTracked, "sync", sWork,
    )
    sOutput = sOutput.lower()
    assert "everything up-to-date" in sOutput, (
        f"no no-op signal for the client to read: {sOutput!r}"
    )


def test_a_real_change_still_commits_and_pushes(tRepoWithRemote):
    """The complement: the skip must not swallow actual work.

    Without this, a command that never commits anything passes the
    test above.
    """
    sWork, sTracked = tRepoWithRemote
    with open(os.path.join(sWork, sTracked), "w") as fileHandle:
        fileHandle.write('{"sName": "changed"}\n')

    iExit, sOutput = syncDispatcher.ftResultAddFileToGithub(
        _LocalShellConnection(), "cid", sTracked, "sync", sWork,
    )
    dictResult = syncDispatcher.fdictSyncResult(iExit, sOutput)
    assert dictResult["bSuccess"], dictResult

    processLog = subprocess.run(
        ["git", "log", "--oneline", "origin/main"],
        cwd=sWork, capture_output=True, text=True, check=True,
    )
    assert len(processLog.stdout.strip().splitlines()) == 2, (
        "the change was not committed and pushed to the remote: "
        f"{processLog.stdout!r}"
    )


def test_a_failed_add_never_publishes_unrelated_staged_work(
    tRepoWithRemote,
):
    """The grouping is load-bearing, and THIS is the case that shows it.

    ``a && b || c && d`` parses as ``((a && b) || c) && d``, so without
    the parentheses the skip clause becomes the alternative to the
    whole prefix. Most inputs cannot tell the two apart -- a failed add
    with a CLEAN index stops either way, which is why the obvious
    "failed add returns non-zero" assertion passes against both and
    proves nothing (checked: the ungrouped form survives it).

    The discriminating case is a failed add with a DIRTY index.
    Ungrouped, the failure falls through to the commit, which succeeds
    on the unrelated staged content and pushes it -- publishing work
    the researcher never asked to publish, under a commit message
    naming a file that was not part of it.
    """
    sWork, sTracked = tRepoWithRemote
    with open(os.path.join(sWork, sTracked), "w") as fileHandle:
        fileHandle.write('{"sName": "staged but not offered"}\n')
    _fnRunGit(sWork, "add", sTracked)

    iExit, _sOutput = syncDispatcher.ftResultAddFileToGithub(
        _LocalShellConnection(), "cid", "no/such/file.json", "sync",
        sWork,
    )
    assert iExit != 0, "a failed add reported success"

    processLog = subprocess.run(
        ["git", "log", "--oneline", "origin/main"],
        cwd=sWork, capture_output=True, text=True, check=True,
    )
    assert len(processLog.stdout.strip().splitlines()) == 1, (
        "a FAILED add published unrelated staged work to the remote: "
        f"{processLog.stdout!r}"
    )
