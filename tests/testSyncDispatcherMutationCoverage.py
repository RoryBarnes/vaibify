"""Mutation-coverage tests for syncDispatcher container-script literals.

Each test closes a specific coverage hole found by mutation testing:
the digest script's git-blob prefix, the template-hash regex's hex
capability, and the digest-line separator's first-space split. These
guarantees underpin reproducibility-integrity comparisons, so a silent
drift in any of them must fail the suite.
"""

import json
import subprocess

import pytest

from vaibify.gui.syncDispatcher import (
    _fsBuildTestMarkerScript,
    _S_DIGEST_SCRIPT,
    fdictComputeContainerDigests,
)
from vaibify.reproducibility.overleafMirror import fsComputeBlobSha


# ── _S_DIGEST_SCRIPT git-blob prefix ────────────────────────────────


def test_digest_script_matches_git_blob_sha(tmp_path):
    """The container digest snippet must hash with the 'blob ' prefix
    so container digests equal host/git-blob digests byte-for-byte.

    Corrupting the prefix (e.g. b'blob ' -> b'blab ') changes the
    SHA-1 and breaks the integrity comparison; this asserts the
    script's output equals fsComputeBlobSha (git hash-object)."""
    pathFile = tmp_path / "known.bytes"
    pathFile.write_bytes(b"hello vaibify blob digest\n")
    sOutput = subprocess.check_output(
        ["python3", "-c", _S_DIGEST_SCRIPT, str(pathFile)],
        text=True,
    )
    sLine = sOutput.strip()
    iSpace = sLine.find(" ")
    sSha = sLine[:iSpace]
    assert sSha == fsComputeBlobSha(str(pathFile))


# ── _fsBuildTestMarkerScript hex template-hash extraction ───────────


def test_marker_script_extracts_hex_template_hash(tmp_path):
    """The template-hash regex must accept hex digits (a-f), not just
    decimal. A real template hash contains a-f; narrowing the class to
    [0-9] truncates or drops the value, silently mismarking stale
    generated tests as current."""
    pathTests = tmp_path / "step1" / "tests"
    pathTests.mkdir(parents=True)
    (pathTests / "test_foo.py").write_text(
        "# vaibify-template-hash: a1b2c3d4e5f6\n"
        "def test_placeholder():\n"
        "    assert True\n",
    )
    sScript = _fsBuildTestMarkerScript(
        json.dumps(["step1"]), str(tmp_path), "demo",
    )
    sOutput = subprocess.check_output(
        ["python3", "-c", sScript], text=True,
    )
    dictResult = json.loads(sOutput)
    dictHashes = dictResult["testFiles"]["step1"]["dictHashes"]
    assert dictHashes.get("test_foo.py") == "a1b2c3d4e5f6"


# ── fdictComputeContainerDigests first-space split ──────────────────


def test_compute_container_digests_path_with_space():
    """A workspace path containing a space must split on the FIRST
    space (sha is the leading token, path is the remainder). Switching
    to rfind splits on the last space, corrupting both the key and the
    stored hash for any file whose path has a space."""
    from unittest.mock import MagicMock

    connectionDocker = MagicMock()
    connectionDocker.ftResultExecuteCommand.return_value = (
        0, "aaaa1111 /workspace/my fig.pdf\n",
    )
    dictResult = fdictComputeContainerDigests(
        connectionDocker, "cid", ["/workspace/my fig.pdf"],
    )
    assert dictResult == {"/workspace/my fig.pdf": "aaaa1111"}
