"""`vaibify init` must not report success when registration failed.

fnRegisterProject swallowed the duplicate-name ValueError and init
then printed "Initialized" and exited 0 regardless. Because
_fnCheckNotDuplicate matches on NAME across all directories, a second
project reusing a name was scaffolded but silently left unregistered —
and `vaibify --project <name>` then resolves to the OTHER directory
forever. init now distinguishes an idempotent re-init of the same
directory from a real cross-directory name conflict and fails loudly
on the latter.
"""

import json
import os

import pytest
from click.testing import CliRunner

from vaibify.cli.commandInit import fnInitCommand
from vaibify.config import registryManager


@pytest.fixture(autouse=True)
def fixtureIsolateRegistry(tmp_path, monkeypatch):
    sDir = str(tmp_path / "registryHome")
    os.makedirs(sDir, exist_ok=True)
    monkeypatch.setattr(registryManager, "_S_REGISTRY_DIRECTORY", sDir)
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_PATH",
        os.path.join(sDir, "registry.json"),
    )
    monkeypatch.setattr(
        registryManager, "_S_LOCK_PATH",
        os.path.join(sDir, "registry.lock"),
    )


def _fnSeedRegistry(sName, sDirectory):
    """Write a registry holding one project at a given directory."""
    registryManager.fnSaveRegistry({"listProjects": [{
        "sName": sName,
        "sDirectory": sDirectory,
        "sConfigPath": os.path.join(sDirectory, "vaibify.yml"),
        "sContainerName": sName,
    }]})


@pytest.mark.falsification
def test_name_conflict_with_a_different_dir_fails_loudly(tmp_path):
    """A name taken by another directory must not report success.

    Kills: In commandInit.fsRegisterProject, return "registered" from
    the ValueError branch instead of distinguishing same-dir from a
    cross-directory name conflict.
    """
    _fnSeedRegistry("shared", str(tmp_path / "elsewhere"))
    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(
            fnInitCommand, ["--name", "shared"], catch_exceptions=False,
        )
    assert result.exit_code == 1, result.output
    assert "already registered to a different directory" in result.output
    assert "NOT registered" in result.output


def test_fresh_name_registers_and_succeeds(tmp_path):
    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(
            fnInitCommand, ["--name", "brandnew"], catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        assert "Initialized Vaibify project 'brandnew'" in result.output
        dictEntry = registryManager.fdictGetProject("brandnew")
        assert dictEntry is not None, (
            "a successful init must actually register the project"
        )


def test_reinit_same_directory_is_idempotent_success(tmp_path):
    """Re-running init in the same directory is not a conflict."""
    runner = CliRunner()
    with runner.isolated_filesystem():
        first = runner.invoke(
            fnInitCommand, ["--name", "again"], catch_exceptions=False,
        )
        assert first.exit_code == 0, first.output
        second = runner.invoke(
            fnInitCommand, ["--name", "again", "--force"],
            catch_exceptions=False,
        )
        assert second.exit_code == 0, second.output
        assert "Initialized Vaibify project 'again'" in second.output
