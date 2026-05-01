"""Virtiofs sync-lag retry tests (F-B-15)."""

from unittest.mock import patch

import pytest

from vaibify.docker import imageBuilder
from vaibify.docker.imageBuilder import (
    _fbStderrLooksLikeVirtiofsLag,
    _fnRunDockerBuildWithVirtiofsRetry,
)


_S_VIRTIOFS_TAIL = (
    "ERROR: failed to compute cache key: failed to walk "
    "/workspace: lstat /workspace/foo: no such file or directory"
)


def test_stderr_pattern_detects_virtiofs_lag():
    """The pattern detector matches the canonical message."""
    assert _fbStderrLooksLikeVirtiofsLag(_S_VIRTIOFS_TAIL)


def test_stderr_pattern_ignores_other_errors():
    """Unrelated stderr does not trigger the retry path."""
    assert not _fbStderrLooksLikeVirtiofsLag(
        "toomanyrequests: pull rate limit"
    )


def test_retry_recovers_when_second_attempt_succeeds():
    """Build retries once and the second attempt succeeds."""
    listCalls = []

    def fakeBuild(saCommand):
        listCalls.append(tuple(saCommand))
        if len(listCalls) == 1:
            errorBuild = RuntimeError("fail")
            errorBuild.sStderrTail = _S_VIRTIOFS_TAIL
            raise errorBuild

    with patch.object(
        imageBuilder, "_fnRunDockerBuildCapturing",
        side_effect=fakeBuild,
    ), patch(
        "vaibify.docker.dockerContext.fbColimaActive", return_value=True,
    ), patch(
        "vaibify.docker.imageBuilder.time.sleep",
    ) as mockSleep:
        _fnRunDockerBuildWithVirtiofsRetry(["docker", "build", "."])
    assert len(listCalls) == 2
    mockSleep.assert_called_once_with(2)


def test_retry_raises_when_second_attempt_also_fails():
    """When both attempts fail, the second exception is raised unchanged."""

    def fakeBuild(saCommand):
        errorBuild = RuntimeError("still failing")
        errorBuild.sStderrTail = _S_VIRTIOFS_TAIL
        raise errorBuild

    with patch.object(
        imageBuilder, "_fnRunDockerBuildCapturing",
        side_effect=fakeBuild,
    ), patch(
        "vaibify.docker.dockerContext.fbColimaActive", return_value=True,
    ), patch(
        "vaibify.docker.imageBuilder.time.sleep",
    ):
        with pytest.raises(RuntimeError, match="still failing"):
            _fnRunDockerBuildWithVirtiofsRetry(["docker", "build", "."])


def test_no_retry_when_pattern_does_not_match():
    """A non-virtiofs error is raised on the first attempt without retry."""
    listCalls = []

    def fakeBuild(saCommand):
        listCalls.append(tuple(saCommand))
        errorBuild = RuntimeError("rate limited")
        errorBuild.sStderrTail = "toomanyrequests: pull rate limit"
        raise errorBuild

    with patch.object(
        imageBuilder, "_fnRunDockerBuildCapturing",
        side_effect=fakeBuild,
    ), patch(
        "vaibify.docker.dockerContext.fbColimaActive", return_value=True,
    ):
        with pytest.raises(RuntimeError, match="rate limited"):
            _fnRunDockerBuildWithVirtiofsRetry(["docker", "build", "."])
    assert len(listCalls) == 1


def test_no_retry_when_colima_inactive():
    """The pattern alone is not enough; Colima must be active."""
    listCalls = []

    def fakeBuild(saCommand):
        listCalls.append(tuple(saCommand))
        errorBuild = RuntimeError("virtiofs")
        errorBuild.sStderrTail = _S_VIRTIOFS_TAIL
        raise errorBuild

    with patch.object(
        imageBuilder, "_fnRunDockerBuildCapturing",
        side_effect=fakeBuild,
    ), patch(
        "vaibify.docker.dockerContext.fbColimaActive", return_value=False,
    ):
        with pytest.raises(RuntimeError, match="virtiofs"):
            _fnRunDockerBuildWithVirtiofsRetry(["docker", "build", "."])
    assert len(listCalls) == 1
