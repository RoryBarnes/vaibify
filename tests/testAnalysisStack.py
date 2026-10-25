"""Every image carries the Python analysis stack, under the project's bounds.

A new project names no Python packages, and its image used to install
none, so the first tests vaibify generated for it failed before they
ran: no pytest, no numpy (alpha tester, 2026-10-24). The stack is
written into the image's own requirements list, the file the Dockerfile
installs and the repository mirror copies, so these tests read that
file as the build writes it.
"""

from types import SimpleNamespace

import pytest

from vaibify.cli.commandBuild import fnWritePythonPackages
from vaibify.config.analysisStack import (
    T_ANALYSIS_STACK_PACKAGES,
    flistImagePythonPackages,
)


def _flistWrittenRequirementLines(tmp_path, listDeclared):
    config = SimpleNamespace(listPythonPackages=listDeclared)
    fnWritePythonPackages(config, str(tmp_path))
    return (tmp_path / "requirements.txt").read_text().split()


@pytest.mark.falsification
def testAProjectThatNamesNothingGetsPytestAndNumpy(tmp_path):
    """Kills: an image list that is only what the project declared."""
    listLines = _flistWrittenRequirementLines(tmp_path, [])
    assert "pytest" in listLines
    assert "numpy" in listLines
    assert listLines == list(T_ANALYSIS_STACK_PACKAGES)


def testAProjectsOwnBoundIsKeptAndNotDuplicated(tmp_path):
    listLines = _flistWrittenRequirementLines(
        tmp_path, ["numpy>=1.26", "packageAlpha"],
    )
    assert listLines[:2] == ["numpy>=1.26", "packageAlpha"]
    assert [s for s in listLines if s.startswith("numpy")] == ["numpy>=1.26"]
    assert listLines[2:] == ["scipy", "matplotlib", "pytest"]


def testANameIsMatchedTheWayPipMatchesIt():
    """Letter case and an extras suffix do not make a second package."""
    listPackages = flistImagePythonPackages(
        ["Matplotlib==3.8.0", "PyTest[testing]"],
    )
    assert listPackages == ["Matplotlib==3.8.0", "PyTest[testing]",
                            "numpy", "scipy"]


def testTheDeclarationIsNotModified():
    listDeclared = ["packageAlpha"]
    flistImagePythonPackages(listDeclared)
    assert listDeclared == ["packageAlpha"]
