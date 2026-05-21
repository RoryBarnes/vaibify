"""Tests for floating base-image pin warning + digest capture.

When ``vaibify build`` is run against a config whose ``sBaseImage``
lacks an ``@sha256:`` digest:
  * A clear warning must reach stdout so the user knows L3 attestation
    will be using a captured digest rather than a pinned one.
  * The resolved digest must be captured into
    ``<projectRepo>/.vaibify/environment.json`` so attestation has
    something concrete to record even when the config is floating.
"""

import io
import json
import os
import subprocess
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

from vaibify.cli import commandBuild


def _fConfigFloating(sProjectName="floatingproj"):
    """Minimal config object with a floating-tag baseImage."""
    return SimpleNamespace(
        sProjectName=sProjectName,
        sBaseImage="ubuntu:24.04",
    )


def _fConfigPinned():
    """Minimal config object whose baseImage already has a digest."""
    return SimpleNamespace(
        sProjectName="pinnedproj",
        sBaseImage="ubuntu@sha256:" + "a" * 64,
    )


def test_fbBaseImageIsFloating_detects_missing_pin():
    assert commandBuild.fbBaseImageIsFloating(_fConfigFloating()) is True


def test_fbBaseImageIsFloating_recognizes_pin():
    assert commandBuild.fbBaseImageIsFloating(_fConfigPinned()) is False


def test_fnWarnIfBaseImageFloating_prints_warning():
    sBuffer = io.StringIO()
    with redirect_stdout(sBuffer):
        commandBuild.fnWarnIfBaseImageFloating(_fConfigFloating())
    sOutput = sBuffer.getvalue()
    assert "ubuntu:24.04" in sOutput
    assert "@sha256:" in sOutput
    assert "Warning" in sOutput


def test_fnWarnIfBaseImageFloating_silent_on_pinned():
    sBuffer = io.StringIO()
    with redirect_stdout(sBuffer):
        commandBuild.fnWarnIfBaseImageFloating(_fConfigPinned())
    assert sBuffer.getvalue() == ""


def test_fnRecordBaseImageDigestIfFloating_writes_env_json(tmp_path):
    """End-to-end: floating tag + successful inspect -> env.json updated."""
    (tmp_path / "vaibify.yml").write_text("name: test\n")
    sDigestOutput = "[ubuntu@sha256:" + ("b" * 64) + "]"
    resultMock = subprocess.CompletedProcess(
        args=[], returncode=0, stdout=sDigestOutput, stderr="",
    )
    with patch(
        "vaibify.cli.commandBuild._fsProjectDirectory",
        return_value=str(tmp_path),
    ), patch(
        "vaibify.cli.commandBuild.subprocess.run",
        return_value=resultMock,
    ):
        commandBuild.fnRecordBaseImageDigestIfFloating(_fConfigFloating())
    sPathEnv = os.path.join(str(tmp_path), ".vaibify", "environment.json")
    assert os.path.isfile(sPathEnv)
    with open(sPathEnv, "r") as fileHandle:
        dictPayload = json.load(fileHandle)
    assert dictPayload["sBaseImageDigest"].startswith("ubuntu@sha256:")
    assert dictPayload["sConfiguredBaseImage"] == "ubuntu:24.04"


def test_fnRecordBaseImageDigestIfFloating_skips_without_vaibify_yml(
    tmp_path,
):
    """No vaibify.yml in project dir → no env.json write (safety guard)."""
    sDigestOutput = "[ubuntu@sha256:" + ("c" * 64) + "]"
    resultMock = subprocess.CompletedProcess(
        args=[], returncode=0, stdout=sDigestOutput, stderr="",
    )
    with patch(
        "vaibify.cli.commandBuild._fsProjectDirectory",
        return_value=str(tmp_path),
    ), patch(
        "vaibify.cli.commandBuild.subprocess.run",
        return_value=resultMock,
    ):
        commandBuild.fnRecordBaseImageDigestIfFloating(_fConfigFloating())
    sPathEnv = os.path.join(str(tmp_path), ".vaibify", "environment.json")
    assert not os.path.exists(sPathEnv)


def test_fnRecordBaseImageDigestIfFloating_skips_when_pinned(tmp_path):
    """No env.json write when the user already pinned the digest."""
    with patch(
        "vaibify.cli.commandBuild._fsProjectDirectory",
        return_value=str(tmp_path),
    ):
        commandBuild.fnRecordBaseImageDigestIfFloating(_fConfigPinned())
    sPathEnv = os.path.join(str(tmp_path), ".vaibify", "environment.json")
    assert not os.path.exists(sPathEnv)


def test_fnRecordBaseImageDigestIfFloating_silent_on_docker_failure(
    tmp_path,
):
    """Docker inspect fails -> no env.json write, no exception raised."""
    resultMock = subprocess.CompletedProcess(
        args=[], returncode=1, stdout="", stderr="Error: no such image",
    )
    with patch(
        "vaibify.cli.commandBuild._fsProjectDirectory",
        return_value=str(tmp_path),
    ), patch(
        "vaibify.cli.commandBuild.subprocess.run",
        return_value=resultMock,
    ):
        commandBuild.fnRecordBaseImageDigestIfFloating(_fConfigFloating())
    sPathEnv = os.path.join(str(tmp_path), ".vaibify", "environment.json")
    assert not os.path.exists(sPathEnv)


def test_fsFirstRepoDigest_extracts_first_entry():
    sOutput = "[ubuntu@sha256:" + ("c" * 64) + " ubuntu:24.04]"
    assert commandBuild._fsFirstRepoDigest(
        sOutput
    ).startswith("ubuntu@sha256:")


def test_fsFirstRepoDigest_returns_empty_on_unpinned():
    sOutput = "[ubuntu:24.04]"
    assert commandBuild._fsFirstRepoDigest(sOutput) == ""


def test_fsFirstRepoDigest_empty_output_returns_empty():
    assert commandBuild._fsFirstRepoDigest("") == ""
    assert commandBuild._fsFirstRepoDigest("[]") == ""
