"""Generic three-tier container build integration tests.

This module verifies that a vaibify project configuration (a
``vaibify.yml`` paired with a ``container.conf``) produces a Docker
image whose contents match what the configuration declares. The tests
are deliberately agnostic to any particular scientific project: every
expected value is derived from the configuration itself.

Running the tests
-----------------
Set the environment variable ``VAIBIFY_INTEGRATION_CONFIG`` to the
absolute path of a ``vaibify.yml`` file. A ``container.conf`` is
expected alongside it (same parent directory)::

    export VAIBIFY_INTEGRATION_CONFIG=/path/to/project/vaibify.yml
    python -m pytest tests/testContainerBuildIntegration.py -v

To run the Docker-dependent tiers as well::

    python -m pytest tests/testContainerBuildIntegration.py -v -m docker

Skip behaviour
--------------
* ``VAIBIFY_INTEGRATION_CONFIG`` unset -> every test in this file is
  skipped.
* Path set but file missing -> every test is skipped.
* Path set and file present but malformed -> config-loading fixture
  fails the test (this is a configuration bug, not a missing
  prerequisite).
* Docker daemon unreachable -> Tier 2 and Tier 3 skip via the
  ``@pytest.mark.docker`` marker combined with a runtime skip.
* Config declares no repositories / no python packages -> the
  corresponding smoke tests skip individually, while the rest of the
  file still runs.
"""

import os
import re
import subprocess
from pathlib import Path

import pytest

from vaibify.config.projectConfig import fconfigLoadFromFile
from vaibify.config.containerConfig import (
    flistParseContainerConf,
    flistConvertFromProjectConfig,
)


__all__ = [
    "TestConfigValidation",
    "TestImageBuild",
    "TestContainerSmoke",
]


_ENV_VAR = "VAIBIFY_INTEGRATION_CONFIG"
_SKIP_MESSAGE = (
    f"{_ENV_VAR} not set; see docstring"
)
_VALID_INSTALL_METHODS = frozenset(
    {
        "c_and_pip",
        "pip_no_deps",
        "pip_editable",
        "scripts_only",
        "reference",
    }
)
_REGEX_PACKAGE_NAME = re.compile(r"^[A-Za-z0-9_.\-]+")


# ------------------------------------------------------------------
# Helpers (private, not exported)
# ------------------------------------------------------------------


def _fpathIntegrationConfig():
    """Return the configured vaibify.yml path or skip."""
    sPath = os.environ.get(_ENV_VAR, "").strip()
    if not sPath:
        pytest.skip(_SKIP_MESSAGE)
    pathConfig = Path(sPath).expanduser()
    if not pathConfig.is_file():
        pytest.skip(
            f"{_ENV_VAR} points to '{pathConfig}' which does not exist"
        )
    return pathConfig


def _fpathContainerConf(pathConfig):
    """Return the container.conf sibling of the vaibify.yml."""
    return pathConfig.parent / "container.conf"


def _fbDockerAvailable():
    """Return True if the Docker daemon responds to docker info."""
    try:
        resultProcess = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=10,
            check=False,
        )
        return resultProcess.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _fsExtractPackageName(sRequirement):
    """Return the bare package name from a pip requirement string."""
    matchResult = _REGEX_PACKAGE_NAME.match(sRequirement.strip())
    if matchResult is None:
        return ""
    return matchResult.group(0)


def _flistImportableNames(config):
    """Return candidate Python import names from listPythonPackages."""
    listNames = []
    for sRequirement in config.listPythonPackages:
        sName = _fsExtractPackageName(sRequirement)
        if sName:
            listNames.append(sName)
    return listNames


def _fsRepoDestination(dictRepo, sWorkspaceRoot):
    """Return the expected filesystem path for a cloned repo."""
    sDestination = dictRepo.get("destination", "") or dictRepo.get(
        "sDestination", ""
    )
    if sDestination:
        if sDestination.startswith("/"):
            return sDestination
        return f"{sWorkspaceRoot.rstrip('/')}/{sDestination}"
    sName = dictRepo.get("name", "") or dictRepo.get("sName", "")
    return f"{sWorkspaceRoot.rstrip('/')}/{sName}"


def _fsImageTag(config, sTag):
    """Return the fully-qualified image tag for this project."""
    return f"{config.sProjectName}:{sTag}"


def _fsContainerName(config):
    """Return the deterministic smoke-test container name."""
    sSafeProject = re.sub(
        r"[^A-Za-z0-9_.-]", "_", config.sProjectName
    )
    return f"vaibify_integration_{sSafeProject}"


def _fnRemoveImagesForProject(sProjectName):
    """Delete every image tag that belongs to the target project."""
    resultProcess = subprocess.run(
        ["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    for sTag in resultProcess.stdout.strip().splitlines():
        if sTag.startswith(f"{sProjectName}:"):
            subprocess.run(
                ["docker", "rmi", "-f", sTag],
                capture_output=True,
                check=False,
            )


def _fnRemoveContainer(sContainerName):
    """Stop and remove a container, ignoring absence."""
    subprocess.run(
        ["docker", "rm", "-f", sContainerName],
        capture_output=True,
        check=False,
    )


# ------------------------------------------------------------------
# Shared fixtures
# ------------------------------------------------------------------


@pytest.fixture(scope="module")
def configProject():
    """Load the configured ProjectConfig once per module."""
    pathConfig = _fpathIntegrationConfig()
    return fconfigLoadFromFile(str(pathConfig))


@pytest.fixture(scope="module")
def pathConfigFile():
    """Return the resolved vaibify.yml path."""
    return _fpathIntegrationConfig()


@pytest.fixture(scope="module")
def pathContainerConfFile(pathConfigFile):
    """Return the container.conf path sibling to vaibify.yml."""
    return _fpathContainerConf(pathConfigFile)


# ------------------------------------------------------------------
# Tier 1: Config validation (no Docker required)
# ------------------------------------------------------------------


class TestConfigValidation:
    """Verify the vaibify.yml and container.conf parse consistently."""

    def test_projectNameNonEmpty(self, configProject):
        assert configProject.sProjectName.strip() != ""

    def test_pythonVersionDeclared(self, configProject):
        assert configProject.sPythonVersion.strip() != ""

    def test_installMethodsValid(self, configProject):
        for dictRepo in configProject.listRepositories:
            sMethod = dictRepo.get("installMethod", "pip_editable")
            assert sMethod in _VALID_INSTALL_METHODS, (
                f"Invalid install method '{sMethod}' for repository "
                f"'{dictRepo.get('name', '?')}'"
            )

    def test_containerConfParses(
        self, configProject, pathContainerConfFile
    ):
        if not pathContainerConfFile.is_file():
            pytest.skip(
                f"container.conf not found at {pathContainerConfFile}"
            )
        listRepos = flistParseContainerConf(str(pathContainerConfFile))
        assert len(listRepos) == len(configProject.listRepositories)

    def test_containerConfNamesMatchYaml(
        self, configProject, pathContainerConfFile
    ):
        if not pathContainerConfFile.is_file():
            pytest.skip(
                f"container.conf not found at {pathContainerConfFile}"
            )
        listYamlNames = [
            dictRepo["name"]
            for dictRepo in configProject.listRepositories
        ]
        listConfNames = [
            dictRepo["sName"]
            for dictRepo in flistParseContainerConf(
                str(pathContainerConfFile)
            )
        ]
        assert listYamlNames == listConfNames

    def test_containerConfRoundtripsFromConfig(self, configProject):
        listConverted = flistConvertFromProjectConfig(configProject)
        assert len(listConverted) == len(
            configProject.listRepositories
        )
        for dictRepo in listConverted:
            assert dictRepo["sName"] != ""
            assert dictRepo["sUrl"] != ""


# ------------------------------------------------------------------
# Tier 2: Image build (requires Docker daemon)
# ------------------------------------------------------------------


@pytest.mark.docker
class TestImageBuild:
    """Build the project image and verify required tags exist."""

    @pytest.fixture(autouse=True, scope="class")
    def fnBuildOnce(self, request):
        """Build the image once for all tests in this class."""
        configProject = request.getfixturevalue("configProject")
        if not _fbDockerAvailable():
            pytest.skip("Docker daemon is not available")
        from vaibify.cli.commandBuild import fnPrepareBuildContext
        from vaibify.cli.configLoader import fsDockerDir
        from vaibify.docker.imageBuilder import fnBuildImage
        sDockerDir = fsDockerDir()
        fnPrepareBuildContext(configProject, sDockerDir)
        fnBuildImage(configProject, sDockerDir, bNoCache=False)
        yield
        _fnRemoveImagesForProject(configProject.sProjectName)

    def test_baseImageExists(self, configProject):
        from vaibify.docker.imageBuilder import fbImageExists
        assert fbImageExists(_fsImageTag(configProject, "base"))

    def test_latestTagExists(self, configProject):
        from vaibify.docker.imageBuilder import fbImageExists
        assert fbImageExists(_fsImageTag(configProject, "latest"))

    def test_pythonVersionInImageMatchesConfig(self, configProject):
        resultProcess = subprocess.run(
            [
                "docker", "run", "--rm",
                _fsImageTag(configProject, "latest"),
                "python", "--version",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
        sOutput = resultProcess.stdout + resultProcess.stderr
        assert configProject.sPythonVersion in sOutput, (
            f"Expected Python {configProject.sPythonVersion} in "
            f"image output, got: {sOutput!r}"
        )


# ------------------------------------------------------------------
# Tier 3: Container smoke tests (requires Docker daemon)
# ------------------------------------------------------------------


@pytest.mark.docker
class TestContainerSmoke:
    """Start the container and verify declared contents are present."""

    @pytest.fixture(autouse=True, scope="class")
    def fnStartOnce(self, request):
        """Ensure the image exists, then start the smoke container."""
        configProject = request.getfixturevalue("configProject")
        if not _fbDockerAvailable():
            pytest.skip("Docker daemon is not available")
        self._fnEnsureImage(configProject)
        sContainerName = _fsContainerName(configProject)
        _fnRemoveContainer(sContainerName)
        subprocess.run(
            [
                "docker", "run", "-d",
                "--name", sContainerName,
                _fsImageTag(configProject, "latest"),
                "sleep", "600",
            ],
            capture_output=True,
            check=True,
            timeout=300,
        )
        yield
        _fnRemoveContainer(sContainerName)
        _fnRemoveImagesForProject(configProject.sProjectName)

    @staticmethod
    def _fnEnsureImage(configProject):
        """Build the image if it is not already present."""
        from vaibify.cli.commandBuild import fnPrepareBuildContext
        from vaibify.cli.configLoader import fsDockerDir
        from vaibify.docker.imageBuilder import (
            fbImageExists,
            fnBuildImage,
        )
        if fbImageExists(_fsImageTag(configProject, "latest")):
            return
        sDockerDir = fsDockerDir()
        fnPrepareBuildContext(configProject, sDockerDir)
        fnBuildImage(configProject, sDockerDir, bNoCache=False)

    @staticmethod
    def _fnExecInContainer(configProject, sCommand):
        """Run a shell command inside the smoke container."""
        return subprocess.run(
            [
                "docker", "exec",
                _fsContainerName(configProject),
                "bash", "-c", sCommand,
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )

    def test_repoDirectoriesPresent(self, configProject):
        if not configProject.listRepositories:
            pytest.skip("Config declares no repositories")
        listMissing = []
        for dictRepo in configProject.listRepositories:
            sPath = _fsRepoDestination(
                dictRepo, configProject.sWorkspaceRoot
            )
            resultProcess = self._fnExecInContainer(
                configProject, f"test -d {sPath}"
            )
            if resultProcess.returncode != 0:
                listMissing.append(sPath)
        assert not listMissing, (
            f"Missing repository directories in container: {listMissing}"
        )

    def test_pythonPackagesImport(self, configProject):
        listImportable = _flistImportableNames(configProject)
        if not listImportable:
            pytest.skip("Config declares no python packages")
        sImports = "; ".join(
            f"import {sName}" for sName in listImportable
        )
        resultProcess = self._fnExecInContainer(
            configProject, f"python -c '{sImports}'"
        )
        assert resultProcess.returncode == 0, (
            f"Python import failed: {resultProcess.stderr}"
        )

    def test_pythonInterpreterRuns(self, configProject):
        resultProcess = self._fnExecInContainer(
            configProject, "python --version"
        )
        assert resultProcess.returncode == 0, (
            f"python --version failed: {resultProcess.stderr}"
        )
