"""Tests for fnMigrateWorkspaceOwnership in the container entrypoint.

Source: ``vaibify/containerImage/entrypoint.sh``.

The migration is the safety net for legacy workspace volumes that carry
root-owned files (created before the two-phase entrypoint landed in
commit a2b29f2). A regression here resurfaces as "researcher cannot push
to GitHub": git's object writes hit permission-denied on a root-owned
``.git/objects/<prefix>`` directory and the agent has no sudo to fix it.

Both the detection scan and the chown are MOUNT-AWARE: a configured bind
mount can be nested beneath ``${WORKSPACE}``, and a blanket recursive
chown (or bare find) would traverse into it and rewrite ownership across
the host directory. These exercise the *when* (detection), the pruning of
nested mounts, the fail-closed behaviour when the mount table is
unreadable, and the mountinfo parsing (component boundary + octal escape
decoding). Real root ownership and real bind mounts cannot be synthesised
from an unprivileged pytest process, so ``find``/``chown`` and the
mount-listing helper are stubbed; the real-container nested-bind
behaviour is the container-acceptance lane's job.
"""

import os
import subprocess


_S_ENTRYPOINT = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__), "..", "vaibify",
        "containerImage", "entrypoint.sh",
    )
)


def _fsRunHelperScript(sWorkspace, sBody):
    """Source entrypoint.sh in a subshell and run sBody.

    The main block at the bottom of entrypoint.sh is guarded by a
    ``BASH_SOURCE == 0`` check, so sourcing leaves the helpers defined
    without executing the entrypoint itself.
    """
    sScript = (
        "set +e\n"
        "WORKSPACE=" + sWorkspace + "\n"
        "export WORKSPACE\n"
        "source " + _S_ENTRYPOINT + "\n"
        + sBody
    )
    return subprocess.run(
        ["bash", "-c", sScript],
        capture_output=True, text=True,
    )


def test_migration_noop_on_clean_workspace(tmp_path):
    """No chown fires when no entry is root-owned.

    On a clean volume ``find -uid 0 -print -quit`` returns empty; the
    helper must return without invoking chown so container boot stays
    fast.
    """
    sBody = (
        'CONTAINER_USER=test\n'
        'chown() { echo CHOWN_INVOKED >&2; return 0; }\n'
        'fnMigrateWorkspaceOwnership\n'
    )
    resultProc = _fsRunHelperScript(str(tmp_path), sBody)
    assert resultProc.returncode == 0, resultProc.stderr
    assert "CHOWN_INVOKED" not in resultProc.stderr
    assert "Migration complete" not in resultProc.stdout


def test_migration_chowns_found_paths_not_a_blanket_recursive(tmp_path):
    """A root-owned entry triggers a chown of the found paths, not -R.

    The old implementation ran ``chown -R ${WORKSPACE}``, which walks
    straight into any bind mount nested under the workspace. The new one
    chowns the paths ``find`` reports (bind subtrees pruned), so the
    argument vector must carry ``--no-dereference`` and the user but NOT
    ``-R``.
    """
    # The chown runs via ``find -print0 | xargs -0 chown``; xargs execs
    # the real chown binary (a shell-function stub would not intercept
    # it, and macOS chown rejects the GNU --no-dereference flag), so stub
    # xargs to capture the exact command it would run.
    sBody = (
        'CONTAINER_USER=test\n'
        # Readable mount table, no nested mounts (deterministic across
        # macOS, which has no /proc/self/mountinfo).
        'fnListNestedWorkspaceMounts() { return 0; }\n'
        'find() { echo /workspace/Repo/.git/objects/3f; return 0; }\n'
        'xargs() { echo "XARGS:$*" >&2; return 0; }\n'
        'fnMigrateWorkspaceOwnership\n'
    )
    resultProc = _fsRunHelperScript(str(tmp_path), sBody)
    assert resultProc.returncode == 0, resultProc.stderr
    assert "Migration complete" in resultProc.stdout
    assert "XARGS:" in resultProc.stderr, resultProc.stderr
    assert "chown --no-dereference test:test" in resultProc.stderr
    assert "-R" not in resultProc.stderr, (
        "the mount-aware migration must not run a blanket recursive chown"
    )


def test_migration_does_nothing_when_workspace_missing(tmp_path):
    """Helper returns silently when WORKSPACE does not exist."""
    sBody = (
        'CONTAINER_USER=test\n'
        'chown() { echo CHOWN_INVOKED >&2; return 0; }\n'
        'fnMigrateWorkspaceOwnership\n'
    )
    resultProc = _fsRunHelperScript(
        str(tmp_path / "does-not-exist"), sBody,
    )
    assert resultProc.returncode == 0, resultProc.stderr
    assert "CHOWN_INVOKED" not in resultProc.stderr


def test_migration_prunes_nested_mounts_from_scan_and_chown(tmp_path):
    """Nested mount points are pruned from both the scan and the chown.

    With bind mounts nested under the workspace, both ``find`` calls must
    carry a ``-path ... -prune`` expression naming each mount, so neither
    the detection scan nor the chown descends into a mounted host dir.
    """
    sFindLog = str(tmp_path / "find.log")
    sBody = (
        'CONTAINER_USER=test\n'
        'fnListNestedWorkspaceMounts() {\n'
        '  printf "%s\\n" /workspace/data /workspace/sub; return 0; }\n'
        'find() { printf "FIND:%s\\n" "$*" >> "' + sFindLog + '"; '
        'echo /workspace/hit; return 0; }\n'
        'chown() { return 0; }\n'
        'fnMigrateWorkspaceOwnership\n'
    )
    resultProc = _fsRunHelperScript(str(tmp_path), sBody)
    assert resultProc.returncode == 0, resultProc.stderr
    with open(sFindLog) as fileHandle:
        sLog = fileHandle.read()
    # Detection scan carries the prune + the -uid 0 probe.
    assert (
        "( -path /workspace/data -o -path /workspace/sub ) -prune "
        "-o -uid 0 -print -quit" in sLog
    ), sLog
    # The chown-list scan carries the same prune + -print0.
    assert (
        "( -path /workspace/data -o -path /workspace/sub ) -prune "
        "-o -print0" in sLog
    ), sLog


def test_migration_fails_closed_when_mount_table_unreadable(tmp_path):
    """An unreadable mount table skips the migration, never a blind chown.

    If we cannot enumerate the mounts we cannot tell a bind from the
    volume's own storage, so a recursive chown could rewrite ownership
    across a mounted host directory. The helper must warn and return.
    """
    sBody = (
        'CONTAINER_USER=test\n'
        'fnListNestedWorkspaceMounts() { return 1; }\n'
        'find() { echo /workspace/hit; return 0; }\n'
        'chown() { echo CHOWN_INVOKED >&2; return 0; }\n'
        'fnMigrateWorkspaceOwnership\n'
    )
    resultProc = _fsRunHelperScript(str(tmp_path), sBody)
    assert resultProc.returncode == 0, resultProc.stderr
    assert "CHOWN_INVOKED" not in resultProc.stderr
    assert "Migration complete" not in resultProc.stdout
    assert "skipping workspace-ownership migration" in resultProc.stderr


def test_list_nested_mounts_parses_boundary_and_escapes(tmp_path):
    """The mount lister keeps proper descendants and decodes octal escapes.

    Locks the awk contract: ``/workspace`` itself is excluded (equal, not
    descendant), ``/workspace-foo`` is excluded (not a component
    boundary), ``/other`` is excluded (not under the workspace), and the
    ``\\040`` space / ``\\134`` backslash escapes are decoded.
    """
    sMountInfo = tmp_path / "mountinfo"
    sMountInfo.write_text(
        "23 28 0:21 / /workspace rw - tmpfs vol rw\n"
        "24 23 0:22 / /workspace/data rw - ext4 /dev/sda1 rw\n"
        "25 23 0:23 / /workspace-foo rw - ext4 /dev/sda2 rw\n"
        "26 24 0:24 / /workspace/sub/deep rw - ext4 /dev/sda3 rw\n"
        "27 23 0:25 / /workspace/my\\040data rw - ext4 /dev/sda4 rw\n"
        "28 1 0:26 / /other rw - ext4 /dev/sda5 rw\n"
        "29 23 0:27 / /workspace/back\\134slash rw - ext4 /dev/sda6 rw\n",
        encoding="utf-8",
    )
    sBody = (
        'fnListNestedWorkspaceMounts "' + str(sMountInfo) + '"\n'
    )
    resultProc = _fsRunHelperScript("/workspace", sBody)
    assert resultProc.returncode == 0, resultProc.stderr
    listLines = resultProc.stdout.splitlines()
    assert listLines == [
        "/workspace/data",
        "/workspace/sub/deep",
        "/workspace/my data",
        "/workspace/back\\slash",
    ], resultProc.stdout


def test_list_nested_mounts_fails_closed_on_unreadable(tmp_path):
    """A missing mount table returns non-zero and emits nothing."""
    sBody = (
        'fnListNestedWorkspaceMounts "' + str(tmp_path / "nope") + '"\n'
        'echo "RC:$?"\n'
    )
    resultProc = _fsRunHelperScript("/workspace", sBody)
    assert "RC:1" in resultProc.stdout, resultProc.stdout


def test_list_nested_mounts_fails_closed_on_malformed_line(tmp_path):
    """A readable-but-malformed line returns non-zero and emits nothing.

    The regression this pins: awk silently skipped an unparseable line and
    still exited 0, so the caller read a truncated or garbled mount table
    as "no nested mounts" and would chown straight into a bind whose line
    it never saw. A short line (fewer than ten fields), a missing "-"
    separator, and a non-absolute mount point must each fail closed — and
    because results are buffered, the valid line that precedes the bad one
    must NOT leak onto stdout.
    """
    for sBadLine in (
        "23 28 0:21 / /workspace/data rw ext4 /dev/sda1 rw",   # no "-"
        "truncated line",                                        # too few
        "23 28 0:21 / relative/mount rw - ext4 /dev/sda1 rw",   # not abs
    ):
        sMountInfo = tmp_path / "mountinfo"
        sMountInfo.write_text(
            "24 23 0:22 / /workspace/good rw - ext4 /dev/sda2 rw\n"
            + sBadLine + "\n",
            encoding="utf-8",
        )
        sBody = (
            'fnListNestedWorkspaceMounts "' + str(sMountInfo) + '"\n'
            'echo "RC:$?"\n'
        )
        resultProc = _fsRunHelperScript("/workspace", sBody)
        assert "RC:1" in resultProc.stdout, (sBadLine, resultProc.stdout)
        assert "/workspace/good" not in resultProc.stdout, (
            "a buffered failure leaked the valid line before the malformed "
            f"one: {sBadLine!r} -> {resultProc.stdout!r}"
        )


def test_list_nested_mounts_fails_closed_on_empty_table(tmp_path):
    """A readable but zero-record mount table returns non-zero.

    A running process always carries at least the workspace mount, so an
    empty read is a failed table, not "no nested mounts". Reading it as the
    latter would drop the prune list and let the migration chown into a
    bind. Distinguished from the normal no-nested-mounts case, which has a
    non-empty table and simply prints nothing with a zero return.
    """
    sMountInfo = tmp_path / "mountinfo"
    sMountInfo.write_text("", encoding="utf-8")
    sBody = (
        'fnListNestedWorkspaceMounts "' + str(sMountInfo) + '"\n'
        'echo "RC:$?"\n'
    )
    resultProc = _fsRunHelperScript("/workspace", sBody)
    assert "RC:1" in resultProc.stdout, resultProc.stdout
