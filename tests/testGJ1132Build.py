"""Build validation tests for the GJ1132_XUV container.

Tests are in three tiers:
  1. Config validation  — no Docker required
  2. Image build        — requires Docker daemon (pytest -m docker)
  3. Container smoke    — requires Docker daemon (pytest -m docker)

All Docker tests clean up images and containers on teardown.
"""

import subprocess

import pytest

from vaibify.config.projectConfig import fconfigLoadFromFile
from vaibify.config.containerConfig import (
    flistParseContainerConf,
    flistConvertFromProjectConfig,
)

_sConfigPath = "/Users/rory/src/GJ1132/vaibify.yml"
_sContainerConfPath = "/Users/rory/src/GJ1132/container.conf"
_sProjectName = "gj1132-xuv"
_iExpectedRepoCount = 12

_listExpectedRepoNames = [
    "vplanet", "vplot", "vspace", "bigplanet",
    "multi-planet", "alabi", "vplanet_inference",
    "vconverge", "MaxLEV", "vplanet-private",
    "GJ1132", "claude",
]

_listCAndPipRepos = ["vplanet", "vplanet-private"]
_listReferenceRepos = ["GJ1132", "claude"]

_listImportablePackages = [
    "vplanet", "vplot", "vspace", "bigplanet",
    "multiplanet", "alabi", "vplanet_inference",
    "vconverge",
]


# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------

def _fbDockerAvailable():
    """Return True if the Docker daemon is reachable."""
    try:
        resultProcess = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=10,
        )
        return resultProcess.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


bDockerRunning = _fbDockerAvailable()
sSkipReason = "Docker daemon is not available"


def _fnCleanupImages():
    """Remove all GJ1132_XUV image tags."""
    resultProcess = subprocess.run(
        ["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"],
        capture_output=True,
        text=True,
    )
    for sTag in resultProcess.stdout.strip().splitlines():
        if sTag.startswith(f"{_sProjectName}:"):
            subprocess.run(
                ["docker", "rmi", "-f", sTag],
                capture_output=True,
            )


def _fnStopAndRemoveContainer(sContainerName):
    """Stop and remove a named container if it exists."""
    subprocess.run(
        ["docker", "rm", "-f", sContainerName],
        capture_output=True,
    )


# -------------------------------------------------------------------
# Tier 1: Config validation (no Docker required)
# -------------------------------------------------------------------

class TestConfigValidation:
    """Validate vaibify.yml and container.conf parse correctly."""

    def test_fbLoadConfig_succeeds(self):
        config = fconfigLoadFromFile(_sConfigPath)
        assert config.sProjectName == _sProjectName

    def test_fiRepoCount_matches(self):
        config = fconfigLoadFromFile(_sConfigPath)
        assert len(config.listRepositories) == _iExpectedRepoCount

    def test_fsProjectName_matches(self):
        config = fconfigLoadFromFile(_sConfigPath)
        assert config.sProjectName == _sProjectName

    def test_fbClaudeOverlay_enabled(self):
        config = fconfigLoadFromFile(_sConfigPath)
        assert config.features.bClaude is True

    def test_fbLatex_enabled(self):
        config = fconfigLoadFromFile(_sConfigPath)
        assert config.features.bLatex is True

    def test_flistRepoNames_complete(self):
        config = fconfigLoadFromFile(_sConfigPath)
        listNames = [
            dictRepo["name"] for dictRepo in config.listRepositories
        ]
        for sExpected in _listExpectedRepoNames:
            assert sExpected in listNames, (
                f"Missing repository: {sExpected}"
            )

    def test_fbInstallMethods_valid(self):
        listValid = {
            "c_and_pip", "pip_no_deps", "pip_editable",
            "scripts_only", "reference",
        }
        config = fconfigLoadFromFile(_sConfigPath)
        for dictRepo in config.listRepositories:
            sMethod = dictRepo["installMethod"]
            assert sMethod in listValid, (
                f"Invalid install method '{sMethod}' "
                f"for {dictRepo['name']}"
            )

    def test_fbCAndPipRepos_correct(self):
        config = fconfigLoadFromFile(_sConfigPath)
        for dictRepo in config.listRepositories:
            if dictRepo["name"] in _listCAndPipRepos:
                assert dictRepo["installMethod"] == "c_and_pip"

    def test_fbReferenceRepos_correct(self):
        config = fconfigLoadFromFile(_sConfigPath)
        for dictRepo in config.listRepositories:
            if dictRepo["name"] in _listReferenceRepos:
                assert dictRepo["installMethod"] == "reference"

    def test_flistContainerConf_parses(self):
        listRepos = flistParseContainerConf(_sContainerConfPath)
        assert len(listRepos) == _iExpectedRepoCount

    def test_fbContainerConf_names_match_yaml(self):
        config = fconfigLoadFromFile(_sConfigPath)
        listYamlNames = [
            dictRepo["name"]
            for dictRepo in config.listRepositories
        ]
        listConfRepos = flistParseContainerConf(_sContainerConfPath)
        listConfNames = [
            dictRepo["sName"] for dictRepo in listConfRepos
        ]
        assert listYamlNames == listConfNames

    def test_fbContainerConf_roundtrips_from_config(self):
        config = fconfigLoadFromFile(_sConfigPath)
        listConverted = flistConvertFromProjectConfig(config)
        assert len(listConverted) == _iExpectedRepoCount
        for dictRepo in listConverted:
            assert dictRepo["sName"] != ""
            assert dictRepo["sUrl"] != ""


# -------------------------------------------------------------------
# Tier 2: Image build (requires Docker)
# -------------------------------------------------------------------

@pytest.mark.docker
@pytest.mark.skipif(not bDockerRunning, reason=sSkipReason)
class TestImageBuild:
    """Build the GJ1132_XUV image and verify the layer chain."""

    @pytest.fixture(autouse=True, scope="class")
    def fnBuildImage(self):
        """Build the image once for all tests in this class."""
        from vaibify.docker.imageBuilder import fnBuildImage
        from vaibify.cli.configLoader import fsDockerDir

        config = fconfigLoadFromFile(_sConfigPath)
        sDockerDir = fsDockerDir()

        from vaibify.config.containerConfig import (
            fnGenerateContainerConf,
        )
        import os
        sConfPath = os.path.join(sDockerDir, "container.conf")
        fnGenerateContainerConf(config, sConfPath)

        fnBuildImage(config, sDockerDir, bNoCache=False)
        yield
        _fnCleanupImages()

    def test_fbBaseImage_exists(self):
        from vaibify.docker.imageBuilder import fbImageExists
        assert fbImageExists(f"{_sProjectName}:base")

    def test_fbClaudeOverlay_exists(self):
        from vaibify.docker.imageBuilder import fbImageExists
        assert fbImageExists(f"{_sProjectName}:claude")

    def test_fbLatestTag_exists(self):
        from vaibify.docker.imageBuilder import fbImageExists
        assert fbImageExists(f"{_sProjectName}:latest")

    def test_fsPythonVersion_correct(self):
        resultProcess = subprocess.run(
            [
                "docker", "run", "--rm",
                f"{_sProjectName}:latest",
                "python", "--version",
            ],
            capture_output=True,
            text=True,
        )
        assert resultProcess.returncode == 0
        assert "3.12" in resultProcess.stdout


# -------------------------------------------------------------------
# Tier 3: Container smoke tests (requires Docker)
# -------------------------------------------------------------------

@pytest.mark.docker
@pytest.mark.skipif(not bDockerRunning, reason=sSkipReason)
class TestContainerSmoke:
    """Start the container and verify repos clone and install."""

    _sContainerName = "vc_test_gj1132"

    @pytest.fixture(autouse=True, scope="class")
    def fnStartContainer(self):
        """Build image and start the container for smoke tests."""
        from vaibify.docker.imageBuilder import (
            fnBuildImage,
            fbImageExists,
        )
        from vaibify.cli.configLoader import fsDockerDir

        if not fbImageExists(f"{_sProjectName}:latest"):
            config = fconfigLoadFromFile(_sConfigPath)
            sDockerDir = fsDockerDir()
            from vaibify.config.containerConfig import (
                fnGenerateContainerConf,
            )
            import os
            sConfPath = os.path.join(sDockerDir, "container.conf")
            fnGenerateContainerConf(config, sConfPath)
            fnBuildImage(config, sDockerDir, bNoCache=False)

        _fnStopAndRemoveContainer(self._sContainerName)

        subprocess.run(
            [
                "docker", "run", "-d",
                "--name", self._sContainerName,
                f"{_sProjectName}:latest",
                "sleep", "600",
            ],
            capture_output=True,
            check=True,
            timeout=300,
        )
        yield
        _fnStopAndRemoveContainer(self._sContainerName)
        _fnCleanupImages()

    def _fsExec(self, sCommand):
        """Run a command inside the container and return stdout."""
        resultProcess = subprocess.run(
            [
                "docker", "exec",
                self._sContainerName,
                "bash", "-c", sCommand,
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        return resultProcess

    def test_fbVplanetBinary_runs(self):
        resultProcess = self._fsExec(
            "vplanet -h 2>&1 | head -1"
        )
        assert resultProcess.returncode == 0

    def test_fbPythonImports_succeed(self):
        sImportList = "; ".join(
            f"import {s}" for s in _listImportablePackages
        )
        resultProcess = self._fsExec(
            f"python -c '{sImportList}'"
        )
        assert resultProcess.returncode == 0, (
            f"Import failed: {resultProcess.stderr}"
        )

    def test_fbGJ1132Directory_present(self):
        resultProcess = self._fsExec(
            "test -d /workspace/GJ1132/XUV"
        )
        assert resultProcess.returncode == 0

    def test_fbClaudeDirectory_present(self):
        resultProcess = self._fsExec(
            "test -d /workspace/claude"
        )
        assert resultProcess.returncode == 0

    def test_fbIsolationCheck_passes(self):
        resultProcess = self._fsExec(
            "bash ~/checkIsolation.sh"
        )
        assert resultProcess.returncode == 0, (
            f"Isolation check failed:\n{resultProcess.stdout}"
        )
