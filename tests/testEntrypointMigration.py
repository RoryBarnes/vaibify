"""Tests for fnMigrateWorkspaceOwnership in docker/entrypoint.sh.

The migration is the safety net for legacy workspace volumes that
carry root-owned files (created before the two-phase entrypoint
landed in commit a2b29f2). A regression here resurfaces as
"researcher cannot push to GitHub": git's object writes hit
permission-denied on a root-owned ``.git/objects/<prefix>``
directory and the agent has no sudo to fix it.

Real root ownership cannot be synthesised inside a pytest run, so
the helper is exercised with stub ``find`` and ``chown`` shell
functions that record their invocations. The logic under test is
purely about *when* the chown fires, not about the chown itself.
"""

import os
import subprocess


_S_ENTRYPOINT = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__), "..", "docker", "entrypoint.sh",
    )
)


def _fsRunHelperScript(sWorkspace, sBody):
    """Source entrypoint.sh in a subshell and run sBody.

    The main block at the bottom of entrypoint.sh is guarded by a
    ``BASH_SOURCE == 0`` check, so sourcing leaves the helpers
    defined without executing the entrypoint itself.
    """
    sScript = (
        "set +e\n"
        "WORKSPACE=" + sWorkspace + "\n"
        "export WORKSPACE\n"
        "source " + _S_ENTRYPOINT + "\n"
        + sBody
    )
    resultProc = subprocess.run(
        ["bash", "-c", sScript],
        capture_output=True, text=True,
    )
    return resultProc


def test_migration_noop_on_clean_workspace(tmp_path):
    """No chown fires when no entry is root-owned.

    On a clean volume ``find -uid 0 -print -quit`` returns empty;
    the helper must return without invoking chown so container
    boot stays fast.
    """
    sWorkspace = str(tmp_path)
    sBody = (
        'CONTAINER_USER=test\n'
        'chown() { echo CHOWN_INVOKED >&2; return 0; }\n'
        'fnMigrateWorkspaceOwnership\n'
    )
    resultProc = _fsRunHelperScript(sWorkspace, sBody)
    assert resultProc.returncode == 0, resultProc.stderr
    assert "CHOWN_INVOKED" not in resultProc.stderr
    assert "Migration complete" not in resultProc.stdout


def test_migration_runs_recursive_chown_when_root_owned_entry_found(tmp_path):
    """A single root-owned entry triggers the full recursive chown.

    The find stub returns a fake hit (real root-owned files cannot
    be created from an unprivileged test process); the chown stub
    records the exact argument vector so the test can assert
    ``-R --no-dereference`` and the right user/path land together.
    """
    sWorkspace = str(tmp_path)
    sBody = (
        'CONTAINER_USER=test\n'
        'find() { echo /workspace/Repo/.git/objects/3f; return 0; }\n'
        'chown() { echo "CHOWN_ARGS:$*" >&2; return 0; }\n'
        'fnMigrateWorkspaceOwnership\n'
    )
    resultProc = _fsRunHelperScript(sWorkspace, sBody)
    assert resultProc.returncode == 0, resultProc.stderr
    sExpected = (
        "CHOWN_ARGS:-R --no-dereference test:test " + sWorkspace
    )
    assert sExpected in resultProc.stderr, resultProc.stderr
    assert "Migration complete" in resultProc.stdout


def test_migration_does_nothing_when_workspace_missing(tmp_path):
    """Helper returns silently when WORKSPACE does not exist.

    Guards against the helper trying to chown a path the host has
    not yet provisioned (e.g., container started before its volume
    mount finished resolving).
    """
    sWorkspace = str(tmp_path / "does-not-exist")
    sBody = (
        'CONTAINER_USER=test\n'
        'chown() { echo CHOWN_INVOKED >&2; return 0; }\n'
        'fnMigrateWorkspaceOwnership\n'
    )
    resultProc = _fsRunHelperScript(sWorkspace, sBody)
    assert resultProc.returncode == 0, resultProc.stderr
    assert "CHOWN_INVOKED" not in resultProc.stderr


def test_migration_uses_deep_scan_not_shallow_loop(tmp_path):
    """The trigger is ``find -uid 0`` over the whole tree.

    Regression guard: the previous implementation only scanned
    top-level entries of ``${WORKSPACE}/`` via a shell glob, so a
    nested root-owned path under a top-level repo dir owned by the
    container user was silently skipped. This test re-defines
    ``find`` to log its argv to a file (the helper redirects
    ``find``'s stderr to /dev/null so we cannot use stderr here)
    and asserts the helper calls it with ``-uid 0 -print -quit``
    against ``${WORKSPACE}``.
    """
    sWorkspace = str(tmp_path)
    sFindLog = str(tmp_path / "find.log")
    sBody = (
        'CONTAINER_USER=test\n'
        'find() { printf "FIND_ARGS:%s\\n" "$*" >> "' + sFindLog + '"; '
        'return 0; }\n'
        'chown() { return 0; }\n'
        'fnMigrateWorkspaceOwnership\n'
    )
    resultProc = _fsRunHelperScript(sWorkspace, sBody)
    assert resultProc.returncode == 0, resultProc.stderr
    with open(sFindLog) as fileHandle:
        sFindLogContents = fileHandle.read()
    sExpected = (
        "FIND_ARGS:" + sWorkspace + " -uid 0 -print -quit"
    )
    assert sExpected in sFindLogContents, sFindLogContents
