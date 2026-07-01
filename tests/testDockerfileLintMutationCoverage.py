"""Mutation-coverage tests for ``dockerfileLint.py``.

Each test closes a specific coverage hole surfaced by mutation
testing: the digest-length boundary, backslash line-continuation
joining of apt blocks, the ``SOURCE_DATE_EPOCH`` name boundary, and
per-line numbering. Each asserts the correct (unmutated) behavior so
it passes on clean code and fails under its mutant.
"""

import pytest

from vaibify.reproducibility.dockerfileLint import (
    flistCheckAptVersionPins,
    flistCheckBaseImageDigests,
    flistCheckSourceDateEpoch,
)

pytestmark = pytest.mark.falsification


def test_truncated_sha256_digest_is_rejected():
    """A digest must be exactly 64 hex chars; near-misses are flagged.

    Kills: _REGEX_DIGEST (line 40): weaken the digest length from {64} to
    {1,64}, accepting `@sha256:abc` as a real pin.
    """
    listShort = flistCheckBaseImageDigests(["FROM python@sha256:abc"])
    assert listShort != []
    assert any("python@sha256:abc" in sIssue for sIssue in listShort)
    listNearMiss = flistCheckBaseImageDigests(
        ["FROM python@sha256:" + "a" * 63]
    )
    assert listNearMiss != []
    listExact = flistCheckBaseImageDigests(
        ["FROM python@sha256:" + "a" * 64]
    )
    assert listExact == []


def test_continued_apt_line_packages_are_inspected():
    """Backslash continuation joins apt lines so each package is checked.

    Kills: _fbLineContinues (line 121) forced to always return False,
    disabling backslash line-continuation joining.
    """
    listIssues = flistCheckAptVersionPins(
        ["RUN apt-get install -y \\", "    bash \\", "    curl=7"]
    )
    assert any("bash" in sIssue for sIssue in listIssues)
    assert not any("curl" in sIssue for sIssue in listIssues)


def test_source_date_epoch_lookalike_is_not_accepted():
    """A prefixed lookalike must not satisfy the determinism check.

    Kills: _REGEX_SDE (line 49): remove the name-boundary anchor `(?:\\s|=)`
    after SOURCE_DATE_EPOCH so a lookalike like
    `ENV SOURCE_DATE_EPOCH_BACKUP=1` satisfies the determinism check.
    """
    listIssues = flistCheckSourceDateEpoch(
        ["ENV SOURCE_DATE_EPOCH_BACKUP=1"]
    )
    assert listIssues != []
    assert flistCheckSourceDateEpoch(["ENV SOURCE_DATE_EPOCH=1"]) == []


def test_base_image_issue_cites_one_based_line_number():
    """Cited ``Line N:`` prefix must use 1-based numbering.

    Kills: flistCheckBaseImageDigests (line 83): change enumerate start from
    1 to 0, making every cited `Line N:` off by one.
    """
    listFirst = flistCheckBaseImageDigests(["FROM python:3.11"])
    assert listFirst[0].startswith("Line 1:")
    listSecond = flistCheckBaseImageDigests(
        ["# comment", "FROM python:3.11"]
    )
    assert listSecond[0].startswith("Line 2:")
