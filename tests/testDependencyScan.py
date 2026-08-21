"""What a project's own scripts say they need.

The scan is a suggestion the researcher edits, so the property that
matters is not "it found everything" but "everything it says is
defensible": nothing from the standard library, nothing the project
itself provides, and nothing invented. These drive the real parser
over real files -- an import scanner tested against a mocked parser
would be testing the mock.
"""

import os

import pytest

from vaibify.gui.dependencyScan import (
    flistDetectImportedDistributions,
    flistReadTopLevelImports,
)


def _fsWriteSource(pathDirectory, sName, sSource):
    sPath = os.path.join(str(pathDirectory), sName)
    with open(sPath, "w") as fileSource:
        fileSource.write(sSource)
    return sPath


def testPlainAndFromImportsAreBothRead():
    listNames = flistReadTopLevelImports(
        "import numpy\nfrom scipy import stats\n",
    )
    assert sorted(listNames) == ["numpy", "scipy"]


def testADottedImportContributesItsTopLevelName():
    """pip is asked for ``matplotlib``, never ``matplotlib.pyplot``."""
    assert flistReadTopLevelImports(
        "import matplotlib.pyplot as plt\n",
    ) == ["matplotlib"]


def testRelativeImportsAreNotPackages():
    """``from . import x`` names a sibling file, not a distribution."""
    assert flistReadTopLevelImports(
        "from . import helper\nfrom .nested import thing\n",
    ) == []


@pytest.mark.falsification
def testTheStandardLibraryIsNeverSuggested(tmp_path):
    """Suggesting ``json`` would ask a researcher to pip-install Python.

    Vaibify supports 3.9, where ``sys.stdlib_module_names`` does not
    exist and the module reads the stdlib directory instead. That
    fallback is the path this test exercises on 3.9 and the one most
    likely to rot silently, because a wrong answer here is not an
    error -- it is a plausible-looking package name in a list the
    researcher is invited to trust.

    Kills: dropping the standard-library filter.
    """
    sPath = _fsWriteSource(
        tmp_path, "analysis.py",
        "import json\nimport os\nfrom pathlib import Path\n"
        "import numpy\n",
    )
    assert flistDetectImportedDistributions(
        [sPath], str(tmp_path),
    ) == ["numpy"]


@pytest.mark.falsification
def testTheProjectsOwnModulesAreNeverSuggested(tmp_path):
    """A sibling file is not on PyPI, and asking for it would fail.

    Kills: dropping the project's-own-modules filter, which turns
    every local helper into a package the container tries to install.
    """
    _fsWriteSource(tmp_path, "myHelper.py", "VALUE = 1\n")
    os.makedirs(os.path.join(str(tmp_path), "myPackage"))
    sPath = _fsWriteSource(
        tmp_path, "analysis.py",
        "import myHelper\nimport myPackage\nimport pandas\n",
    )
    assert flistDetectImportedDistributions(
        [sPath], str(tmp_path),
    ) == ["pandas"]


def testAnImportNameIsMappedToItsDistributionName(tmp_path):
    """``import sklearn`` installs ``scikit-learn``, not ``sklearn``."""
    sPath = _fsWriteSource(
        tmp_path, "model.py",
        "import sklearn\nimport cv2\nfrom PIL import Image\n",
    )
    assert flistDetectImportedDistributions(
        [sPath], str(tmp_path),
    ) == ["opencv-python", "pillow", "scikit-learn"]


def testAFileThatDoesNotParseIsSkippedNotFatal(tmp_path):
    """A half-written script is ordinary; it must not blank the answer."""
    sBroken = _fsWriteSource(tmp_path, "broken.py", "def f(:\n")
    sGood = _fsWriteSource(tmp_path, "good.py", "import astropy\n")
    assert flistDetectImportedDistributions(
        [sBroken, sGood], str(tmp_path),
    ) == ["astropy"]


def testTheSuggestionCarriesNoVersion(tmp_path):
    """Names only: a version constraint is the researcher's decision."""
    sPath = _fsWriteSource(tmp_path, "run.py", "import numpy\n")
    for sSuggestion in flistDetectImportedDistributions(
        [sPath], str(tmp_path),
    ):
        assert not any(
            sOperator in sSuggestion
            for sOperator in ("==", ">=", "<=", "~=", "<", ">")
        ), sSuggestion


def testDuplicateImportsAcrossFilesCollapse(tmp_path):
    sFirst = _fsWriteSource(tmp_path, "a.py", "import numpy\n")
    sSecond = _fsWriteSource(
        tmp_path, "b.py", "import numpy\nimport scipy\n",
    )
    assert flistDetectImportedDistributions(
        [sFirst, sSecond], str(tmp_path),
    ) == ["numpy", "scipy"]


S_PROJECT_NAME = "scanLaneProject"


@pytest.fixture
def tclientScan(tmp_path, monkeypatch):
    """A hub serving one registered project with real files on disk."""
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from vaibify.config import registryManager
    from vaibify.gui.registryRoutes import fnRegisterRegistryRoutes
    sRegistryDirectory = str(tmp_path / ".vaibify")
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_DIRECTORY", sRegistryDirectory,
    )
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_PATH",
        os.path.join(sRegistryDirectory, "registry.json"),
    )
    sProjectDirectory = str(tmp_path / S_PROJECT_NAME)
    os.makedirs(os.path.join(sProjectDirectory, "sub"))
    with open(
        os.path.join(sProjectDirectory, "vaibify.yml"), "w",
    ) as fileConfig:
        fileConfig.write(f"projectName: {S_PROJECT_NAME}\n")
    _fsWriteSource(sProjectDirectory, "top.py", "import numpy\n")
    _fsWriteSource(
        os.path.join(sProjectDirectory, "sub"), "deep.py",
        "import astropy\n",
    )
    # A file OUTSIDE the project, which no selection may reach.
    _fsWriteSource(tmp_path, "outside.py", "import secretpackage\n")
    registryManager.fnAddProject(sProjectDirectory, sMode="host")
    app = FastAPI()
    app.state.dictContainerOwners = {}
    app.state.iHubPort = 8050
    fnRegisterRegistryRoutes(app, {"require": lambda *a: None,
                                   "docker": None})
    return TestClient(app)


def _sScanUrl():
    return f"/api/registry/{S_PROJECT_NAME}/scan-dependencies"


def testTheRouteWalksASelectedDirectory(tclientScan):
    """Ticking a folder scans the Python files inside it."""
    response = tclientScan.post(
        _sScanUrl(), json={"saRelativePaths": ["top.py", "sub"]},
    )
    assert response.status_code == 200, response.text
    dictResult = response.json()
    assert dictResult["saDetectedPackages"] == ["astropy", "numpy"]
    assert dictResult["iScannedFileCount"] == 2


@pytest.mark.falsification
def testTheRouteCannotBeAskedToReadOutsideTheProject(tclientScan):
    """A path that escapes is skipped, never read.

    The scan REPORTS what it read, so an escape does not merely read a
    file the researcher never offered -- it echoes that file's imports
    back into the dashboard. Silent skipping is the right answer for a
    selection that cannot be scanned, but it must be a skip and not a
    read.

    Kills: dropping the containment check in
    _flistSelectPythonFilesWithin.
    """
    response = tclientScan.post(
        _sScanUrl(), json={"saRelativePaths": ["../outside.py"]},
    )
    assert response.status_code == 200, response.text
    dictResult = response.json()
    assert dictResult["saDetectedPackages"] == []
    assert dictResult["iScannedFileCount"] == 0


def testANonPythonSelectionSimplyScansNothing(tclientScan):
    """A data file in the selection is not an error; it is not Python."""
    response = tclientScan.post(
        _sScanUrl(), json={"saRelativePaths": ["vaibify.yml"]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["iScannedFileCount"] == 0
