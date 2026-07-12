"""Tests for the entrypoint's repository ref handling.

A repo in container.conf may declare a branch, a tag, or — for the
reproducible-binary story — a raw commit hash. ``git clone --branch``
accepts the first two but never a hash, so the entrypoint routes
hash-like refs through clone-then-checkout.
"""

import os
import subprocess

_S_ENTRYPOINT = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__), "..", "docker", "entrypoint.sh",
    )
)


def _fiRunRefPredicate(sRef):
    """Source the real entrypoint and run fbRefLooksLikeCommit."""
    sScript = (
        "set +e\n"
        "WORKSPACE=/tmp\nexport WORKSPACE\n"
        "source " + _S_ENTRYPOINT + "\n"
        "fbRefLooksLikeCommit " + sRef + "\n"
        "echo exit=$?\n"
    )
    resultProc = subprocess.run(
        ["bash", "-c", sScript], capture_output=True, text=True,
    )
    for sLine in resultProc.stdout.splitlines():
        if sLine.startswith("exit="):
            return int(sLine.split("=", 1)[1])
    raise AssertionError(
        "predicate did not report: " + resultProc.stderr[-500:],
    )


def test_commit_hashes_are_recognized():
    """Full and abbreviated hex hashes route to clone-then-checkout."""
    assert _fiRunRefPredicate(
        "dd55da7e1ff063f0ea7048f91c9d2d97d6ba9a5d",
    ) == 0
    assert _fiRunRefPredicate("dd55da7") == 0


def test_branches_and_tags_are_not_hashes():
    """Names git clone --branch handles keep the historical path."""
    assert _fiRunRefPredicate("main") == 1
    assert _fiRunRefPredicate("v2.5.36") == 1
    assert _fiRunRefPredicate("v3.0") == 1
    assert _fiRunRefPredicate("feature-branch") == 1


def test_clone_routes_hash_refs_through_checkout():
    """The clone path must checkout hash refs after a plain clone."""
    with open(_S_ENTRYPOINT, "r", encoding="utf-8") as fileHandle:
        sSource = fileHandle.read()
    iCloneStart = sSource.index("fnCloneRepo()")
    sBody = sSource[iCloneStart:sSource.index("\nfnUpdateRepo")]
    assert "fbRefLooksLikeCommit" in sBody
    assert "checkout --quiet" in sBody
