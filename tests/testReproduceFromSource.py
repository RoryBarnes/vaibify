"""``vaibify reproduce --from``: stage, describe, discard, touch nothing.

The lane never enters the tier sequence: no manifest is graded as
advisory, no ``pip install`` reaches the host interpreter, no image is
pulled. The tests drive the real command through Click against a real
git fixture, so the clone and the validation are exercised as a
researcher would exercise them.
"""

import os
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from tests.reproductionSourceFixtures import (
    S_FIXTURE_IMAGE_DIGEST,
    fnWriteText,
    fsBuildPublishedProject,
)
from vaibify.cli.commandReproduce import fnReproduceCommand
from vaibify.reproducibility import reproductionSource


@pytest.fixture
def sPublishedRepo(tmp_path, monkeypatch):
    """A complete Level 3 project under the one admitted local root."""
    sRoot = os.path.realpath(str(tmp_path))
    monkeypatch.setattr(
        reproductionSource, "flistAdmittedLocalCloneRoots", lambda: [sRoot],
    )
    sRepoPath = os.path.join(sRoot, "publishedProject")
    fsBuildPublishedProject(sRepoPath)
    return sRepoPath


def _flistStagingTokens():
    """Return the staging directories currently on disk."""
    sRoot = reproductionSource._fsStagingRoot()
    return sorted(os.listdir(sRoot)) if os.path.isdir(sRoot) else []


def _fnRefuseEveryTier():
    """Patch every tier and the pip launcher to fail loudly if reached."""
    def fnExplode(*args, **kwargs):
        raise AssertionError("--from must never enter the tier sequence")
    return patch.multiple(
        "vaibify.cli.commandReproduce",
        fbVerifyTier1=fnExplode, fbVerifyTier2=fnExplode,
        fbVerifyTier3=fnExplode, fbVerifyTier4=fnExplode,
        _ftRunPipInstall=fnExplode, fdictRerunAndVerify=fnExplode,
    )


@pytest.mark.falsification
def test_from_stages_describes_and_discards_without_a_tier(sPublishedRepo):
    """Kills: falling through into the tier loop after staging."""
    with _fnRefuseEveryTier():
        result = CliRunner().invoke(
            fnReproduceCommand, ["--from", sPublishedRepo],
        )
    assert result.exit_code == 0, result.output
    assert "Staged local-clone" in result.output
    assert S_FIXTURE_IMAGE_DIGEST in result.output
    assert "linux/amd64" in result.output
    assert "no deposit on record" in result.output
    assert "discarded" in result.output
    assert "[1/5]" not in result.output
    assert _flistStagingTokens() == []


def test_from_prints_the_refusal_and_exits_one(sPublishedRepo):
    fnWriteText(sPublishedRepo, "uncommitted.txt", "dirty\n")
    with _fnRefuseEveryTier():
        result = CliRunner().invoke(
            fnReproduceCommand, ["--from", sPublishedRepo],
        )
    assert result.exit_code == 1, result.output
    assert result.output.startswith("Refused:")
    assert "uncommitted.txt" in result.output
    assert _flistStagingTokens() == []


def test_from_refuses_the_tier_flags(sPublishedRepo):
    result = CliRunner().invoke(
        fnReproduceCommand,
        ["--from", sPublishedRepo, "--repo", sPublishedRepo],
    )
    assert result.exit_code == 2
    assert "--from" in result.output
    result = CliRunner().invoke(
        fnReproduceCommand, ["--from", sPublishedRepo, "--skip-tier", "2"],
    )
    assert result.exit_code == 2


def test_from_with_rerun_says_the_rerun_is_not_here_yet(sPublishedRepo):
    with _fnRefuseEveryTier():
        result = CliRunner().invoke(
            fnReproduceCommand, ["--from", sPublishedRepo, "--rerun"],
        )
    assert result.exit_code == 2, result.output
    assert "later release" in result.output
    assert _flistStagingTokens() == []


def test_from_selects_a_named_workflow(sPublishedRepo):
    with _fnRefuseEveryTier():
        result = CliRunner().invoke(
            fnReproduceCommand,
            ["--from", sPublishedRepo, "--workflow", "Demo"],
        )
    assert result.exit_code == 0, result.output
    assert "workflow:        Demo" in result.output
    result = CliRunner().invoke(
        fnReproduceCommand,
        ["--from", sPublishedRepo, "--workflow", "Missing"],
    )
    assert result.exit_code == 1
    assert "no workflow named 'Missing'" in result.output
