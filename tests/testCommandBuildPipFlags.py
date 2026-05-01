"""Tests for --prefer-binary defaulting in pip-flags handling (F-B-11)."""

import os
import tempfile

from types import SimpleNamespace

from vaibify.cli.commandBuild import (
    _fsEnsurePreferBinary,
    fnWritePipInstallFlags,
)
from vaibify.config.projectConfig import ProjectConfig


def test_fsEnsurePreferBinary_empty_returns_flag():
    assert _fsEnsurePreferBinary("") == "--prefer-binary"


def test_fsEnsurePreferBinary_whitespace_returns_flag():
    assert _fsEnsurePreferBinary("   ") == "--prefer-binary"


def test_fsEnsurePreferBinary_none_returns_flag():
    assert _fsEnsurePreferBinary(None) == "--prefer-binary"


def test_fsEnsurePreferBinary_already_present_unchanged():
    assert _fsEnsurePreferBinary("--prefer-binary") == "--prefer-binary"


def test_fsEnsurePreferBinary_already_present_with_other_flags():
    sFlags = "--no-deps --prefer-binary --upgrade"
    assert _fsEnsurePreferBinary(sFlags) == sFlags


def test_fsEnsurePreferBinary_prepends_when_missing():
    assert (
        _fsEnsurePreferBinary("--no-deps")
        == "--prefer-binary --no-deps"
    )


def test_fsEnsurePreferBinary_preserves_user_flags_verbatim():
    sUser = "--no-deps --upgrade --extra-index-url https://example.org"
    sResult = _fsEnsurePreferBinary(sUser)
    assert sResult.endswith(sUser)
    assert sResult.startswith("--prefer-binary")


def test_fsEnsurePreferBinary_strips_leading_whitespace():
    assert (
        _fsEnsurePreferBinary("  --no-deps  ")
        == "--prefer-binary --no-deps"
    )


def test_projectConfig_default_includes_prefer_binary():
    config = ProjectConfig()
    assert "--prefer-binary" in config.sPipInstallFlags


def test_fnWritePipInstallFlags_prepends_for_user_flags():
    config = SimpleNamespace(sPipInstallFlags="--no-deps")
    with tempfile.TemporaryDirectory() as sTmpDir:
        fnWritePipInstallFlags(config, sTmpDir)
        with open(os.path.join(sTmpDir, "pip-flags.txt")) as fh:
            sContent = fh.read()
    assert sContent.strip() == "--prefer-binary --no-deps"


def test_fnWritePipInstallFlags_empty_yields_just_prefer_binary():
    config = SimpleNamespace(sPipInstallFlags="")
    with tempfile.TemporaryDirectory() as sTmpDir:
        fnWritePipInstallFlags(config, sTmpDir)
        with open(os.path.join(sTmpDir, "pip-flags.txt")) as fh:
            sContent = fh.read()
    assert sContent.strip() == "--prefer-binary"


def test_fnWritePipInstallFlags_does_not_duplicate():
    config = SimpleNamespace(
        sPipInstallFlags="--prefer-binary --no-deps"
    )
    with tempfile.TemporaryDirectory() as sTmpDir:
        fnWritePipInstallFlags(config, sTmpDir)
        with open(os.path.join(sTmpDir, "pip-flags.txt")) as fh:
            sContent = fh.read()
    assert sContent.count("--prefer-binary") == 1
