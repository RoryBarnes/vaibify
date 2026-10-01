"""A project file the process cannot read ends in a sentence, not a traceback.

Every command below resolves the project from the ``vaibify.yml`` in the
working directory. The file exists but its permissions deny reading, so
the failure is an ``OSError`` that is not ``FileNotFoundError``.
"""

import os

import pytest
from click.testing import CliRunner

from vaibify.cli import configLoader
from vaibify.cli.main import main

pytestmark = pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root reads a file whatever its permissions",
)


@pytest.fixture
def pathUnreadableProject(tmp_path, monkeypatch):
    pathProject = tmp_path / "project"
    pathProject.mkdir()
    pathConfig = pathProject / "vaibify.yml"
    pathConfig.write_text("projectName: projectAlpha\n")
    pathConfig.chmod(0o000)
    monkeypatch.chdir(pathProject)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(configLoader, "_sConfigOverride", None)
    yield pathConfig
    pathConfig.chmod(0o644)


@pytest.mark.parametrize(
    "sCommandName", ["status", "workflow", "ls", "doctor", "stop"],
)
def testAnUnreadableProjectFileExitsNonzeroWithAMessage(
    pathUnreadableProject, sCommandName,
):
    resultInvoke = CliRunner().invoke(main, [sCommandName])
    assert isinstance(resultInvoke.exception, SystemExit), (
        f"uncaught {resultInvoke.exception!r}"
    )
    assert resultInvoke.exit_code != 0
    assert "Failed to load" in resultInvoke.output
    assert "vaibify.yml" in resultInvoke.output


def testDoctorReportsAFailedConfigurationCheck(pathUnreadableProject):
    resultInvoke = CliRunner().invoke(main, ["doctor"])
    assert resultInvoke.exit_code == 1
    assert "[fail] project-configuration" in resultInvoke.output
    assert "0 fail" not in resultInvoke.output
