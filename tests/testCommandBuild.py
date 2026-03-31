"""Tests for vaibify.cli.commandBuild pure helpers."""

import os
import tempfile

from types import SimpleNamespace
from unittest.mock import patch

from vaibify.cli.commandBuild import (
    fnWriteSystemPackages,
    fnWritePythonPackages,
    fnWritePipInstallFlags,
    fnWriteBinariesEnv,
)


def _fConfigForBuild():
    """Return a minimal config for build context tests."""
    return SimpleNamespace(
        sProjectName="testproj",
        listSystemPackages=["gcc", "make", "git"],
        listPythonPackages=["numpy", "scipy"],
        sPipInstallFlags="--no-deps",
        listBinaries=[
            {"name": "solver", "path": "/workspace/bin/solver"},
        ],
    )


def test_fnWriteSystemPackages_content():
    config = _fConfigForBuild()
    with tempfile.TemporaryDirectory() as sTmpDir:
        fnWriteSystemPackages(config, sTmpDir)
        sPath = os.path.join(sTmpDir, "system-packages.txt")
        with open(sPath) as fh:
            sContent = fh.read()
        assert "gcc" in sContent
        assert "make" in sContent


def test_fnWritePythonPackages_content():
    config = _fConfigForBuild()
    with tempfile.TemporaryDirectory() as sTmpDir:
        fnWritePythonPackages(config, sTmpDir)
        sPath = os.path.join(sTmpDir, "requirements.txt")
        with open(sPath) as fh:
            sContent = fh.read()
        assert "numpy" in sContent
        assert "scipy" in sContent


def test_fnWritePipInstallFlags_content():
    config = _fConfigForBuild()
    with tempfile.TemporaryDirectory() as sTmpDir:
        fnWritePipInstallFlags(config, sTmpDir)
        sPath = os.path.join(sTmpDir, "pip-flags.txt")
        with open(sPath) as fh:
            sContent = fh.read()
        assert "--no-deps" in sContent


def test_fnWriteBinariesEnv_content():
    config = _fConfigForBuild()
    with tempfile.TemporaryDirectory() as sTmpDir:
        fnWriteBinariesEnv(config, sTmpDir)
        sPath = os.path.join(sTmpDir, "binaries.env")
        with open(sPath) as fh:
            sContent = fh.read()
        assert "solver=/workspace/bin/solver" in sContent


def test_fnWriteBinariesEnv_empty():
    config = SimpleNamespace(listBinaries=[])
    with tempfile.TemporaryDirectory() as sTmpDir:
        fnWriteBinariesEnv(config, sTmpDir)
        sPath = os.path.join(sTmpDir, "binaries.env")
        with open(sPath) as fh:
            sContent = fh.read()
        assert sContent.strip() == ""
