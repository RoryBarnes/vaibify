"""Tests for ``vaibify/reproducibility/dockerfileLint.py``.

The lint walks three orthogonal properties: base-image digest
pinning, apt-get version pinning, and ``SOURCE_DATE_EPOCH``
presence. Each property has both a green and a red fixture so the
sign of the assertion (issue vs no issue) is exercised both ways.
"""

import pytest

from vaibify.reproducibility.dockerfileLint import (
    S_ALLOW_UNPINNED_MARKER,
    S_DOCKERFILE_FILENAME,
    fbDockerfilePresent,
    flistCheckAptVersionPins,
    flistCheckBaseImageDigests,
    flistCheckSourceDateEpoch,
    flistLintDockerfile,
)


def _fnWriteDockerfile(tmp_path, sContents):
    """Write the supplied Dockerfile body to the temp project repo."""
    pathDockerfile = tmp_path / S_DOCKERFILE_FILENAME
    pathDockerfile.write_text(sContents)
    return pathDockerfile


def test_lint_returns_missing_issue_for_absent_dockerfile(tmp_path):
    listIssues = flistLintDockerfile(str(tmp_path))
    assert len(listIssues) == 1
    assert "not found" in listIssues[0].lower()


def test_dockerfile_presence_helper(tmp_path):
    assert not fbDockerfilePresent(str(tmp_path))
    _fnWriteDockerfile(tmp_path, "FROM scratch\n")
    assert fbDockerfilePresent(str(tmp_path))


def test_lint_passes_with_digest_pinned_image_and_sde(tmp_path):
    sBody = (
        "FROM python@sha256:" + "a" * 64 + "\n"
        "ENV SOURCE_DATE_EPOCH=1700000000\n"
        "RUN apt-get install -y bash=5.1-6ubuntu1\n"
    )
    _fnWriteDockerfile(tmp_path, sBody)
    assert flistLintDockerfile(str(tmp_path)) == []


def test_base_image_must_use_sha256_digest():
    listIssues = flistCheckBaseImageDigests(
        ["FROM python:3.11", "FROM scratch"]
    )
    assert any("python:3.11" in sIssue for sIssue in listIssues)


def test_base_image_accepts_scratch_and_digest():
    listLines = [
        "FROM python@sha256:" + "a" * 64,
        "FROM scratch AS builder",
    ]
    assert flistCheckBaseImageDigests(listLines) == []


def test_apt_install_requires_version_pin():
    listLines = ["RUN apt-get install -y bash"]
    listIssues = flistCheckAptVersionPins(listLines)
    assert any("bash" in sIssue for sIssue in listIssues)


def test_apt_allow_unpinned_marker_waives_check():
    listLines = [
        "RUN apt-get install -y bash  " + S_ALLOW_UNPINNED_MARKER,
    ]
    assert flistCheckAptVersionPins(listLines) == []


def test_apt_version_pinned_package_is_clean():
    listLines = ["RUN apt-get install -y bash=5.1-6ubuntu1 curl=7"]
    assert flistCheckAptVersionPins(listLines) == []


def test_apt_multiline_continuation_handled():
    listLines = [
        "RUN apt-get install -y \\",
        "    bash=5.1 \\",
        "    curl=7",
    ]
    assert flistCheckAptVersionPins(listLines) == []


def test_source_date_epoch_required_via_env_or_arg():
    assert flistCheckSourceDateEpoch(["FROM scratch"]) != []
    assert flistCheckSourceDateEpoch(
        ["ENV SOURCE_DATE_EPOCH=1700000000"]
    ) == []
    assert flistCheckSourceDateEpoch(
        ["ARG SOURCE_DATE_EPOCH=1700000000"]
    ) == []


def test_lint_reports_first_two_issues_when_multiple_red(tmp_path):
    sBody = (
        "FROM python:3.11\n"
        "RUN apt-get install -y bash\n"
    )
    _fnWriteDockerfile(tmp_path, sBody)
    listIssues = flistLintDockerfile(str(tmp_path))
    assert len(listIssues) >= 3
