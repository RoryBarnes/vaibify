"""Tests for vaibify.reproducibility.environmentSnapshot."""

import hashlib
import json
import subprocess
from unittest.mock import patch

import pytest

from vaibify.reproducibility.environmentSnapshot import (
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
