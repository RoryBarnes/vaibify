"""Tests for vaibify.reproducibility.environmentSnapshot."""

import hashlib
import json
import subprocess
from unittest.mock import patch

import pytest

from vaibify.reproducibility.environmentSnapshot import (
    _fsCaptureBinaryVersion,
    _fsCaptureGccVersion,
    fdictCaptureContainerImageDigest,
    fdictCaptureHostBinaryHashes,
    fdictCaptureSystemTools,
    fnWriteEnvironmentJson,
)


def _fnMakeCompletedProcess(iReturnCode, sStdout="", sStderr=""):
    """Helper to fabricate a subprocess.CompletedProcess instance."""
    return subprocess.CompletedProcess(
        args=[], returncode=iReturnCode, stdout=sStdout, stderr=sStderr,
    )


# ------------------------------------------------------------------
# fdictCaptureContainerImageDigest
# ------------------------------------------------------------------


def test_fdictCaptureContainerImageDigest_happy_path():
    sFakeDigest = (
        "vaibify@sha256:"
        "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789"
    )
    sStdout = f"[{sFakeDigest}]\n"
    with patch(
        "vaibify.reproducibility.environmentSnapshot.shutil.which",
        return_value="/usr/local/bin/docker",
    ), patch(
        "vaibify.reproducibility.environmentSnapshot.subprocess.run",
        return_value=_fnMakeCompletedProcess(0, sStdout=sStdout),
    ):
        dictResult = fdictCaptureContainerImageDigest("vaibify-test")

    assert dictResult["sContainerName"] == "vaibify-test"
    assert dictResult["sImageDigest"] == sFakeDigest


def test_fdictCaptureContainerImageDigest_no_digest_returns_none():
    with patch(
        "vaibify.reproducibility.environmentSnapshot.shutil.which",
        return_value="/usr/local/bin/docker",
    ), patch(
        "vaibify.reproducibility.environmentSnapshot.subprocess.run",
        return_value=_fnMakeCompletedProcess(0, sStdout="[]\n"),
    ):
        dictResult = fdictCaptureContainerImageDigest("vaibify-test")

    assert dictResult["sImageDigest"] is None
    assert dictResult["sContainerName"] == "vaibify-test"


def test_fdictCaptureContainerImageDigest_docker_missing_raises():
    with patch(
        "vaibify.reproducibility.environmentSnapshot.shutil.which",
        return_value=None,
    ):
        with pytest.raises(FileNotFoundError) as excInfo:
            fdictCaptureContainerImageDigest("vaibify-test")

    assert "docker" in str(excInfo.value).lower()


def test_fdictCaptureContainerImageDigest_docker_fails_raises():
    with patch(
        "vaibify.reproducibility.environmentSnapshot.shutil.which",
        return_value="/usr/local/bin/docker",
    ), patch(
        "vaibify.reproducibility.environmentSnapshot.subprocess.run",
        return_value=_fnMakeCompletedProcess(
            1, sStderr="No such container",
        ),
    ):
        with pytest.raises(subprocess.CalledProcessError) as excInfo:
            fdictCaptureContainerImageDigest("missing-container")

    assert excInfo.value.stderr == "No such container"


def test_run_checked_command_uses_thirty_second_timeout():
    """``docker inspect`` and friends must allow 30s for cold-start runtimes.

    Colima and Docker Desktop can take 3-8s to respond on a cold VM;
    the prior 5s ceiling produced false-positive Tier-3 capture
    failures during otherwise-clean reproducibility runs.
    """
    listCallKwargs = []

    def fnCaptureKwargs(*saArgs, **dictKwargs):
        listCallKwargs.append(dictKwargs)
        return _fnMakeCompletedProcess(0, sStdout="[]\n")

    with patch(
        "vaibify.reproducibility.environmentSnapshot.shutil.which",
        return_value="/usr/local/bin/docker",
    ), patch(
        "vaibify.reproducibility.environmentSnapshot.subprocess.run",
        side_effect=fnCaptureKwargs,
    ):
        fdictCaptureContainerImageDigest("vaibify-test")

    assert listCallKwargs, "subprocess.run was not invoked"
    assert listCallKwargs[0]["timeout"] == 30.0


# ------------------------------------------------------------------
# fdictCaptureHostBinaryHashes
# ------------------------------------------------------------------


def test_fdictCaptureHostBinaryHashes_happy_path(tmp_path):
    pathBinaryOne = tmp_path / "vplanet"
    pathBinaryTwo = tmp_path / "gcc"
    pathBinaryOne.write_bytes(b"binary-one-content")
    pathBinaryTwo.write_bytes(b"binary-two-content")
    pathBinaryOne.chmod(0o755)
    pathBinaryTwo.chmod(0o755)

    def fnFakeRun(saArgs, **kwargs):
        if saArgs[0] == str(pathBinaryOne):
            return _fnMakeCompletedProcess(0, sStdout="vplanet 3.0.0\n")
        return _fnMakeCompletedProcess(
            0, sStdout="gcc (Debian 12.2.0) 12.2.0\n",
        )

    with patch(
        "vaibify.reproducibility.environmentSnapshot.subprocess.run",
        side_effect=fnFakeRun,
    ):
        dictResult = fdictCaptureHostBinaryHashes(
            [str(pathBinaryOne), str(pathBinaryTwo)],
        )

    listBinaries = dictResult["listBinaries"]
    assert len(listBinaries) == 2
    assert listBinaries[0]["sBinaryPath"] == str(pathBinaryOne)
    assert listBinaries[0]["sSha256"] == hashlib.sha256(
        b"binary-one-content"
    ).hexdigest()
    assert listBinaries[0]["sVersion"] == "vplanet 3.0.0"
    assert listBinaries[1]["sVersion"] == "gcc (Debian 12.2.0) 12.2.0"


def test_fdictCaptureHostBinaryHashes_missing_binary(tmp_path):
    sMissingPath = str(tmp_path / "does-not-exist")

    dictResult = fdictCaptureHostBinaryHashes([sMissingPath])

    listBinaries = dictResult["listBinaries"]
    assert len(listBinaries) == 1
    assert listBinaries[0]["sBinaryPath"] == sMissingPath
    assert listBinaries[0]["sSha256"] is None
    assert listBinaries[0]["sVersion"] is None


def test_fdictCaptureHostBinaryHashes_version_fails_hash_still_computed(
    tmp_path,
):
    pathBinary = tmp_path / "broken-binary"
    pathBinary.write_bytes(b"some-bytes")
    pathBinary.chmod(0o755)

    with patch(
        "vaibify.reproducibility.environmentSnapshot.subprocess.run",
        return_value=_fnMakeCompletedProcess(2, sStderr="usage error"),
    ):
        dictResult = fdictCaptureHostBinaryHashes([str(pathBinary)])

    dictBinary = dictResult["listBinaries"][0]
    assert dictBinary["sSha256"] == hashlib.sha256(b"some-bytes").hexdigest()
    assert dictBinary["sVersion"] is None


# ------------------------------------------------------------------
# fdictCaptureSystemTools
# ------------------------------------------------------------------


def test_fdictCaptureSystemTools_happy_path(tmp_path):
    pathOsRelease = tmp_path / "os-release"
    pathOsRelease.write_text('NAME="Ubuntu"\nVERSION="22.04"\n')

    def fnFakeWhich(sName):
        return f"/usr/bin/{sName}"

    with patch(
        "vaibify.reproducibility.environmentSnapshot.shutil.which",
        side_effect=fnFakeWhich,
    ), patch(
        "vaibify.reproducibility.environmentSnapshot.subprocess.run",
        return_value=_fnMakeCompletedProcess(
            0, sStdout="gcc (Ubuntu 11.4.0) 11.4.0\nMore detail\n",
        ),
    ), patch(
        "vaibify.reproducibility.environmentSnapshot._OS_RELEASE_PATH",
        str(pathOsRelease),
    ), patch(
        "vaibify.reproducibility.environmentSnapshot.platform.libc_ver",
        return_value=("glibc", "2.35"),
    ):
        dictResult = fdictCaptureSystemTools()

    assert dictResult["sGcc"] == "gcc (Ubuntu 11.4.0) 11.4.0"
    assert dictResult["sLibc"] == "glibc 2.35"
    assert "Ubuntu" in dictResult["sOsRelease"]
    assert "\n" not in dictResult["sPython"]


def test_fdictCaptureSystemTools_missing_tools(tmp_path):
    sNonexistent = str(tmp_path / "no-os-release")

    with patch(
        "vaibify.reproducibility.environmentSnapshot.shutil.which",
        return_value=None,
    ), patch(
        "vaibify.reproducibility.environmentSnapshot._OS_RELEASE_PATH",
        sNonexistent,
    ), patch(
        "vaibify.reproducibility.environmentSnapshot.platform.libc_ver",
        return_value=("", ""),
    ):
        dictResult = fdictCaptureSystemTools()

    assert dictResult["sGcc"] is None
    assert dictResult["sOsRelease"] is None
    assert dictResult["sLibc"] is None
    assert dictResult["sPython"]


# ------------------------------------------------------------------
# fnWriteEnvironmentJson
# ------------------------------------------------------------------


def test_fnWriteEnvironmentJson_writes_with_metadata(tmp_path):
    dictInput = {"sImageDigest": "image@sha256:deadbeef"}

    fnWriteEnvironmentJson(str(tmp_path), dictInput)

    pathOutput = tmp_path / ".vaibify" / "environment.json"
    assert pathOutput.is_file()
    dictLoaded = json.loads(pathOutput.read_text())
    assert dictLoaded["sImageDigest"] == "image@sha256:deadbeef"
    assert dictLoaded["sSchemaVersion"] == "1"
    assert "sTimestamp" in dictLoaded
    # Superset check
    for sKey, sValue in dictInput.items():
        assert dictLoaded[sKey] == sValue


def test_fnWriteEnvironmentJson_creates_vaibify_directory(tmp_path):
    pathVaibify = tmp_path / ".vaibify"
    assert not pathVaibify.exists()

    fnWriteEnvironmentJson(str(tmp_path), {"sKey": "value"})

    assert pathVaibify.is_dir()
    assert (pathVaibify / "environment.json").is_file()


def test_capture_binary_version_returns_none_on_timeout():
    """A hung ``--version`` invocation must surface as ``None``.

    Regression test for the Wave-1 hardening: the 5s timeout in
    ``_fsCaptureBinaryVersion`` raises ``TimeoutExpired`` which the
    function swallows so the snapshot still captures the binary's
    SHA-256 instead of hanging forever on a TTY-prompting binary.
    """
    excTimeout = subprocess.TimeoutExpired(
        cmd=["/bin/whatever", "--version"], timeout=5.0,
    )
    with patch(
        "vaibify.reproducibility.environmentSnapshot.subprocess.run",
        side_effect=excTimeout,
    ):
        sVersion = _fsCaptureBinaryVersion("/bin/whatever")
    assert sVersion is None


def test_capture_gcc_version_returns_none_on_timeout():
    """A hung ``gcc --version`` must yield ``None`` instead of hanging."""
    excTimeout = subprocess.TimeoutExpired(
        cmd=["gcc", "--version"], timeout=5.0,
    )
    with patch(
        "vaibify.reproducibility.environmentSnapshot.shutil.which",
        return_value="/usr/bin/gcc",
    ), patch(
        "vaibify.reproducibility.environmentSnapshot.subprocess.run",
        side_effect=excTimeout,
    ):
        sVersion = _fsCaptureGccVersion()
    assert sVersion is None


def test_fnWriteEnvironmentJson_is_deterministic(tmp_path):
    pathRepoOne = tmp_path / "repo-one"
    pathRepoTwo = tmp_path / "repo-two"
    pathRepoOne.mkdir()
    pathRepoTwo.mkdir()
    dictInput = {"sB": "second", "sA": "first", "sC": "third"}
    sFakeStamp = "2026-05-03T12:00:00+00:00"

    with patch(
        "vaibify.reproducibility.environmentSnapshot._fsCurrentTimestamp",
        return_value=sFakeStamp,
    ):
        fnWriteEnvironmentJson(str(pathRepoOne), dictInput)
        fnWriteEnvironmentJson(str(pathRepoTwo), dictInput)

    baFirst = (pathRepoOne / ".vaibify" / "environment.json").read_bytes()
    baSecond = (pathRepoTwo / ".vaibify" / "environment.json").read_bytes()
    assert baFirst == baSecond


def test_fnWriteEnvironmentJson_atomic_replace_preserves_old(tmp_path):
    """A failed mid-write must not leave a half-written environment.json.

    Forces ``os.replace`` to fail after the temp file is written; the
    pre-existing ``environment.json`` must remain intact (containing
    the previous payload), and the temp file must be cleaned up rather
    than left to confuse the next reader.
    """
    import os
    pathDir = tmp_path / ".vaibify"
    pathDir.mkdir()
    pathOutput = pathDir / "environment.json"
    pathOutput.write_text('{"sExisting": "preserved"}')
    sFakeStamp = "2026-05-03T12:00:00+00:00"

    def fnFailingReplace(*saArgs, **dictKwargs):
        raise OSError("disk full")

    with patch(
        "vaibify.reproducibility.environmentSnapshot._fsCurrentTimestamp",
        return_value=sFakeStamp,
    ), patch(
        "vaibify.reproducibility.repoFiles.os.replace",
        side_effect=fnFailingReplace,
    ):
        with pytest.raises(OSError):
            fnWriteEnvironmentJson(str(tmp_path), {"sNew": "payload"})

    # Old file untouched; no leftover .tmp.
    assert json.loads(pathOutput.read_text()) == {"sExisting": "preserved"}
    listLeftovers = [
        sName for sName in os.listdir(str(pathDir)) if sName.endswith(".tmp")
    ]
    assert listLeftovers == []


def test_capture_binary_version_returns_none_for_empty_path():
    """An empty ``sPath`` must short-circuit instead of invoking subprocess."""
    sVersion = _fsCaptureBinaryVersion("")
    assert sVersion is None


def test_capture_binary_version_returns_none_for_none_path():
    """A ``None`` ``sPath`` must short-circuit instead of crashing."""
    sVersion = _fsCaptureBinaryVersion(None)
    assert sVersion is None


# ------------------------------------------------------------------
# fbBinaryCaptured honesty: a null-hash record is NOT a capture
# ------------------------------------------------------------------


def _fdictEnvWithBinaryEntry(dictEntry):
    """Wrap one binary entry in the canonical environment.json layout."""
    return {"dictHostBinaries": {"listBinaries": [dictEntry]}}


def test_binary_captured_requires_a_real_hash():
    from vaibify.reproducibility.environmentSnapshot import (
        fbBinaryCaptured,
    )
    dictEnv = _fdictEnvWithBinaryEntry({
        "sBinaryPath": "/usr/local/bin/tool",
        "sSha256": "a" * 64,
        "sVersion": "tool 1.0",
    })
    assert fbBinaryCaptured(dictEnv, "/usr/local/bin/tool") is True


@pytest.mark.parametrize("badSha256", [None, ""])
def test_binary_with_null_hash_does_not_count_as_captured(badSha256):
    """A ``{sBinaryPath, sSha256: None}`` record proves nothing.

    Without this conjunct a failed capture (binary missing at capture
    time) would permanently satisfy the ``binary-not-captured``
    blocker while attesting to a binary identity nobody ever hashed.
    """
    from vaibify.reproducibility.environmentSnapshot import (
        fbBinaryCaptured,
    )
    dictEnv = _fdictEnvWithBinaryEntry({
        "sBinaryPath": "/usr/local/bin/tool",
        "sSha256": badSha256,
        "sVersion": None,
    })
    assert fbBinaryCaptured(dictEnv, "/usr/local/bin/tool") is False


def test_null_hash_entry_keeps_binary_not_captured_blocker_up():
    """The L3 blocker stays up until a real hash exists for the binary."""
    from vaibify.reproducibility.levelGates import (
        _flistDeclaredBinariesNotCaptured,
    )
    dictStep = {
        "saDataCommands": ["/usr/local/bin/tool --run input.dat"],
    }
    dictContext = {
        "listDeclaredBinaries": [{
            "sBinaryPath": "/usr/local/bin/tool",
            "sPurpose": "model integration",
            "sExpectedVersion": "1.0",
        }],
        "dictEnvironment": _fdictEnvWithBinaryEntry({
            "sBinaryPath": "/usr/local/bin/tool",
            "sSha256": None,
            "sVersion": None,
        }),
    }
    assert _flistDeclaredBinariesNotCaptured(dictStep, dictContext) == [
        "/usr/local/bin/tool",
    ]


def test_capture_single_binary_hashes_through_the_adapter():
    """Hash and version probes run where the repo adapter points.

    A container-rooted adapter must hash the *container's* binary; a
    host-side hash of a container path silently captures the wrong
    (or no) file.
    """
    from vaibify.reproducibility.environmentSnapshot import (
        fdictCaptureSingleBinary,
    )

    class _FakeAdapter:
        def __init__(self):
            self.listHashCalls = []
            self.listRunCalls = []

        def fdictHashAbsolutePaths(self, listAbsPaths):
            self.listHashCalls.append(list(listAbsPaths))
            return {sPath: "b" * 64 for sPath in listAbsPaths}

        def ftRunCommand(self, saCommand, fTimeoutSeconds):
            self.listRunCalls.append(list(saCommand))
            return (0, "tool 2.0\n", "")

    filesFake = _FakeAdapter()
    dictEntry = fdictCaptureSingleBinary(filesFake, "/opt/tool")
    assert dictEntry == {
        "sBinaryPath": "/opt/tool",
        "sSha256": "b" * 64,
        "sVersion": "tool 2.0",
    }
    assert filesFake.listHashCalls == [["/opt/tool"]]
    assert filesFake.listRunCalls == [["/opt/tool", "--version"]]
