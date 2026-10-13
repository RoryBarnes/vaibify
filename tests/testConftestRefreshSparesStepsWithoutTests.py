"""The conftest refresh never creates a ``tests/`` directory.

Opening a project refreshed ``tests/conftest.py`` in every step with a
directory, creating ``tests/`` where there was none. An AI-declaration
step has no tests, so a fresh clone of a published project gained an
untracked ``AIDeclaration/tests/conftest.py`` the moment it was opened,
and a reproduction counts untracked files as a modified tree
(researcher-reported, 2026-10-11).

The probe and the write are real scripts run by a real shell against a
real directory tree, because the decision lives inside the probe
script, which no fake executes.
"""

import os
import subprocess

import pytest

from vaibify.gui import conftestManager

S_CONTAINER_ID = "host-project"


class ShellConnection:
    """Run each command through bash on this machine, as the host leg does."""

    def ftResultExecuteCommand(self, sContainerId, sCommand):
        processResult = subprocess.run(
            ["/bin/bash", "-c", sCommand], capture_output=True,
            encoding="utf-8",
        )
        return processResult.returncode, processResult.stdout

    def fnWriteFileFromStream(
        self, sContainerId, sPath, fileSource, **dictKeywords,
    ):
        """Land the marker plugin's files, making their directory."""
        os.makedirs(os.path.dirname(sPath), exist_ok=True)
        with open(sPath, "wb") as fileOut:
            fileOut.write(fileSource.read())


@pytest.fixture(autouse=True)
def fixtureClearRefreshCaches():
    conftestManager.fnClearRefreshCaches()
    yield
    conftestManager.fnClearRefreshCaches()


@pytest.fixture()
def fixtureProject(tmp_path):
    """A step with a tests/ directory and a declaration step without one."""
    os.makedirs(tmp_path / "Analysis" / "tests")
    os.makedirs(tmp_path / "Declaration")
    return str(tmp_path)


def testAStepWithoutTestsIsLeftWithoutATestsDirectory(fixtureProject):
    """Kills: treating a step with no tests/ directory as stale."""
    conftestManager.fnEnsureConftestsCurrent(
        ShellConnection(), S_CONTAINER_ID, ["Analysis", "Declaration"],
        fixtureProject,
    )
    assert os.path.isfile(
        os.path.join(fixtureProject, "Analysis", "tests", "conftest.py"))
    assert not os.path.exists(
        os.path.join(fixtureProject, "Declaration", "tests"))


def testTheRunTimeRefreshSparesItTooAndWarnsAboutNothing(fixtureProject):
    listWarnings = conftestManager.flistRefreshConftestsForRun(
        ShellConnection(), S_CONTAINER_ID, ["Analysis", "Declaration"],
        fixtureProject,
    )
    assert listWarnings == []
    assert not os.path.exists(
        os.path.join(fixtureProject, "Declaration", "tests"))
