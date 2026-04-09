"""Tests for uncovered lines in vaibify.gui.registryRoutes."""

import os
import subprocess

import pytest
from unittest.mock import MagicMock, patch

from vaibify.gui.registryRoutes import (
    _fbContainerHasTty,
    _fbDockerContainerExists,
    _fdictRequireProject,
    _fnDockerStopCommand,
    _fnExecuteBuild,
    _fnExecuteStop,
    _fnRegisterNewProject,
    _fnRejectDuplicateProjectName,
    _fnRemoveContainer,
    _fnUpdateYamlBoolField,
    _fsDockerStartExisting,
    _fsExecuteStart,
    _fsStartOrCreate,
)

from vaibify.config import registryManager


@pytest.fixture(autouse=True)
def fixtureIsolateRegistry(tmp_path, monkeypatch):
    """Redirect registry to a temp directory for every test."""
    sRegistryDir = str(tmp_path / ".vaibify")
    sRegistryPath = os.path.join(sRegistryDir, "registry.json")
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_DIRECTORY", sRegistryDir,
    )
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_PATH", sRegistryPath,
    )


# ---------------------------------------------------------------
# _fnExecuteBuild (lines 142-150)
# ---------------------------------------------------------------

class TestExecuteBuild:
    def test_calls_build_from_config(self):
        dictProject = {"sConfigPath": "/fake/vaibify.yml"}
        mockConfig = MagicMock()
        with patch(
            "vaibify.cli.configLoader.fconfigLoadFromPath",
            return_value=mockConfig,
        ), patch(
            "vaibify.cli.configLoader.fsDockerDir",
            return_value="/docker/dir",
        ), patch(
            "vaibify.cli.commandBuild.fnBuildFromConfig",
        ) as mockBuild:
            _fnExecuteBuild(dictProject)
            mockBuild.assert_called_once_with(
                mockConfig, "/docker/dir", bNoCache=False,
            )


# ---------------------------------------------------------------
# _fsExecuteStart (lines 173-186)
# ---------------------------------------------------------------

class TestExecuteStart:
    def test_starts_container_and_returns_id(self):
        dictProject = {
            "sConfigPath": "/fake/vaibify.yml",
            "sContainerName": "my-proj",
        }
        mockConfig = MagicMock()
        mockConfig.bNeverSleep = False
        with patch(
            "vaibify.cli.configLoader.fconfigLoadFromPath",
            return_value=mockConfig,
        ), patch(
            "vaibify.cli.configLoader.fsDockerDir",
            return_value="/docker/dir",
        ), patch(
            "vaibify.gui.registryRoutes._fsStartOrCreate",
            return_value="abc123",
        ):
            sResult = _fsExecuteStart(dictProject)
            assert sResult == "abc123"

    def test_starts_keep_alive_when_never_sleep(self):
        dictProject = {
            "sConfigPath": "/fake/vaibify.yml",
            "sContainerName": "my-proj",
        }
        mockConfig = MagicMock()
        mockConfig.bNeverSleep = True
        with patch(
            "vaibify.cli.configLoader.fconfigLoadFromPath",
            return_value=mockConfig,
        ), patch(
            "vaibify.cli.configLoader.fsDockerDir",
            return_value="/docker/dir",
        ), patch(
            "vaibify.gui.registryRoutes._fsStartOrCreate",
            return_value="abc123",
        ), patch(
            "vaibify.docker.keepAliveManager.fnStartKeepAlive",
        ) as mockKeepAlive:
            _fsExecuteStart(dictProject)
            mockKeepAlive.assert_called_once_with("my-proj")


# ---------------------------------------------------------------
# _fsStartOrCreate (lines 191-203)
# ---------------------------------------------------------------

class TestStartOrCreate:
    def test_raises_if_already_running(self):
        with patch(
            "vaibify.docker.containerManager.fdictGetContainerStatus",
            return_value={"bRunning": True, "bExists": True},
        ):
            with pytest.raises(RuntimeError, match="already running"):
                _fsStartOrCreate(MagicMock(), "proj", "/docker")

    def test_starts_existing_with_tty(self):
        with patch(
            "vaibify.docker.containerManager.fdictGetContainerStatus",
            return_value={"bRunning": False, "bExists": True},
        ), patch(
            "vaibify.gui.registryRoutes._fbContainerHasTty",
            return_value=True,
        ), patch(
            "vaibify.gui.registryRoutes._fbContainerUsesSleepInfinity",
            return_value=True,
        ), patch(
            "vaibify.gui.registryRoutes._fsDockerStartExisting",
            return_value="xyz789",
        ) as mockStart:
            sResult = _fsStartOrCreate(MagicMock(), "proj", "/docker")
            assert sResult == "xyz789"
            mockStart.assert_called_once_with("proj")

    def test_removes_and_creates_without_tty(self):
        with patch(
            "vaibify.docker.containerManager.fdictGetContainerStatus",
            return_value={"bRunning": False, "bExists": True},
        ), patch(
            "vaibify.gui.registryRoutes._fbContainerHasTty",
            return_value=False,
        ), patch(
            "vaibify.gui.registryRoutes._fnRemoveContainer",
        ) as mockRemove, patch(
            "vaibify.docker.containerManager.fsStartContainerDetached",
            return_value="new123",
        ):
            sResult = _fsStartOrCreate(MagicMock(), "proj", "/docker")
            assert sResult == "new123"
            mockRemove.assert_called_once_with("proj")

    def test_creates_fresh_when_not_exists(self):
        with patch(
            "vaibify.docker.containerManager.fdictGetContainerStatus",
            return_value={"bRunning": False, "bExists": False},
        ), patch(
            "vaibify.docker.containerManager.fsStartContainerDetached",
            return_value="fresh123",
        ):
            sResult = _fsStartOrCreate(MagicMock(), "proj", "/docker")
            assert sResult == "fresh123"


# ---------------------------------------------------------------
# _fbContainerHasTty (lines 208-216)
# ---------------------------------------------------------------

class TestContainerHasTty:
    def test_returns_true_for_true_output(self):
        mockResult = MagicMock()
        mockResult.returncode = 0
        mockResult.stdout = "true\n"
        with patch("subprocess.run", return_value=mockResult):
            assert _fbContainerHasTty("proj") is True

    def test_returns_false_for_false_output(self):
        mockResult = MagicMock()
        mockResult.returncode = 0
        mockResult.stdout = "false\n"
        with patch("subprocess.run", return_value=mockResult):
            assert _fbContainerHasTty("proj") is False

    def test_returns_false_on_failure(self):
        mockResult = MagicMock()
        mockResult.returncode = 1
        mockResult.stdout = ""
        with patch("subprocess.run", return_value=mockResult):
            assert _fbContainerHasTty("proj") is False


# ---------------------------------------------------------------
# _fnRemoveContainer (lines 221-222)
# ---------------------------------------------------------------

class TestRemoveContainer:
    def test_calls_docker_rm(self):
        with patch("subprocess.run") as mockRun:
            _fnRemoveContainer("my-container")
            mockRun.assert_called_once()
            listArgs = mockRun.call_args[0][0]
            assert listArgs == ["docker", "rm", "my-container"]


# ---------------------------------------------------------------
# _fsDockerStartExisting (lines 230-240)
# ---------------------------------------------------------------

class TestDockerStartExisting:
    def test_returns_container_id(self):
        mockResult = MagicMock()
        mockResult.returncode = 0
        mockResult.stdout = "abc123\n"
        with patch("subprocess.run", return_value=mockResult):
            sResult = _fsDockerStartExisting("proj")
            assert sResult == "abc123"

    def test_raises_on_failure(self):
        mockResult = MagicMock()
        mockResult.returncode = 1
        mockResult.stderr = "Error response\n"
        with patch("subprocess.run", return_value=mockResult):
            with pytest.raises(RuntimeError, match="docker start failed"):
                _fsDockerStartExisting("proj")


# ---------------------------------------------------------------
# _fnExecuteStop (lines 261-272)
# ---------------------------------------------------------------

class TestExecuteStop:
    def test_stop_nonexistent_container(self):
        with patch(
            "vaibify.docker.containerManager.fdictGetContainerStatus",
            return_value={"bExists": False, "bRunning": False},
        ), patch(
            "vaibify.docker.keepAliveManager.fnStopKeepAlive",
        ) as mockKeepAlive:
            _fnExecuteStop("proj")
            mockKeepAlive.assert_called_once_with("proj")

    def test_stop_running_container(self):
        with patch(
            "vaibify.docker.containerManager.fdictGetContainerStatus",
            return_value={"bExists": True, "bRunning": True},
        ), patch(
            "vaibify.gui.registryRoutes._fnDockerStopCommand",
        ) as mockStop, patch(
            "vaibify.docker.containerManager.fnRemoveStopped",
        ), patch(
            "vaibify.docker.keepAliveManager.fnStopKeepAlive",
        ):
            _fnExecuteStop("proj")
            mockStop.assert_called_once_with("proj")

    def test_stop_stopped_but_existing_container(self):
        with patch(
            "vaibify.docker.containerManager.fdictGetContainerStatus",
            return_value={"bExists": True, "bRunning": False},
        ), patch(
            "vaibify.gui.registryRoutes._fnDockerStopCommand",
        ) as mockStop, patch(
            "vaibify.docker.containerManager.fnRemoveStopped",
        ) as mockRemove, patch(
            "vaibify.docker.keepAliveManager.fnStopKeepAlive",
        ):
            _fnExecuteStop("proj")
            mockStop.assert_not_called()
            mockRemove.assert_called_once()


# ---------------------------------------------------------------
# _fnDockerStopCommand (lines 277-283)
# ---------------------------------------------------------------

class TestDockerStopCommand:
    def test_success(self):
        mockResult = MagicMock()
        mockResult.returncode = 0
        with patch("subprocess.run", return_value=mockResult):
            _fnDockerStopCommand("proj")

    def test_raises_on_failure(self):
        mockResult = MagicMock()
        mockResult.returncode = 1
        mockResult.stderr = "cannot stop\n"
        with patch("subprocess.run", return_value=mockResult):
            with pytest.raises(RuntimeError, match="docker stop failed"):
                _fnDockerStopCommand("proj")


# ---------------------------------------------------------------
# Container settings (lines 294-312)
# ---------------------------------------------------------------

def _fnWriteMinimalConfig(tmp_path, sProjectName="test-project"):
    """Create a minimal vaibify.yml in a temp project directory."""
    sProjectDir = str(tmp_path / sProjectName)
    os.makedirs(sProjectDir, exist_ok=True)
    sConfigPath = os.path.join(sProjectDir, "vaibify.yml")
    with open(sConfigPath, "w") as fileHandle:
        fileHandle.write(f"projectName: {sProjectName}\n")
    return sProjectDir


@pytest.fixture
def fixtureSettingsApp():
    """Create a hub-mode app with Docker mocked out."""
    from fastapi import FastAPI
    from vaibify.gui.registryRoutes import fnRegisterRegistryRoutes
    app = FastAPI()
    dictCtx = {"require": lambda: None, "docker": None}
    fnRegisterRegistryRoutes(app, dictCtx)
    return app


@pytest.fixture
def fixtureSettingsClient(fixtureSettingsApp):
    from starlette.testclient import TestClient
    return TestClient(fixtureSettingsApp)


class TestContainerSettings:
    def test_get_settings(
        self, fixtureSettingsClient, tmp_path,
    ):
        sProjectDir = _fnWriteMinimalConfig(
            tmp_path, "settings-proj",
        )
        fixtureSettingsClient.post(
            "/api/registry",
            json={"sDirectory": sProjectDir},
        )
        mockConfig = MagicMock()
        mockConfig.bNeverSleep = True
        with patch(
            "vaibify.config.projectConfig.fconfigLoadFromFile",
            return_value=mockConfig,
        ):
            response = fixtureSettingsClient.get(
                "/api/containers/settings-proj/settings",
            )
        assert response.status_code == 200
        assert response.json()["bNeverSleep"] is True

    def test_post_settings(
        self, fixtureSettingsClient, tmp_path,
    ):
        sProjectDir = _fnWriteMinimalConfig(
            tmp_path, "settings-proj2",
        )
        fixtureSettingsClient.post(
            "/api/registry",
            json={"sDirectory": sProjectDir},
        )
        with patch(
            "vaibify.gui.registryRoutes._fnUpdateYamlBoolField",
        ) as mockUpdate:
            response = fixtureSettingsClient.post(
                "/api/containers/settings-proj2/settings",
                json={"bNeverSleep": True},
            )
        assert response.status_code == 200
        assert response.json()["bSuccess"] is True
        mockUpdate.assert_called_once()

    def test_get_settings_not_found(self, fixtureSettingsClient):
        response = fixtureSettingsClient.get(
            "/api/containers/ghost/settings",
        )
        assert response.status_code == 404

    def test_post_settings_not_found(self, fixtureSettingsClient):
        response = fixtureSettingsClient.post(
            "/api/containers/ghost/settings",
            json={"bNeverSleep": False},
        )
        assert response.status_code == 404


# ---------------------------------------------------------------
# _fnUpdateYamlBoolField (lines 317-333)
# ---------------------------------------------------------------

class TestUpdateYamlBoolField:
    def test_updates_existing_key(self, tmp_path):
        sConfigPath = str(tmp_path / "vaibify.yml")
        with open(sConfigPath, "w") as fileHandle:
            fileHandle.write(
                "projectName: test\nneverSleep: false\n",
            )
        _fnUpdateYamlBoolField(sConfigPath, "neverSleep", True)
        with open(sConfigPath, "r") as fileHandle:
            sContent = fileHandle.read()
        assert "neverSleep: true" in sContent

    def test_appends_when_missing(self, tmp_path):
        sConfigPath = str(tmp_path / "vaibify.yml")
        with open(sConfigPath, "w") as fileHandle:
            fileHandle.write("projectName: test\n")
        _fnUpdateYamlBoolField(sConfigPath, "neverSleep", True)
        with open(sConfigPath, "r") as fileHandle:
            sContent = fileHandle.read()
        assert "neverSleep: true" in sContent

    def test_appends_newline_if_missing(self, tmp_path):
        sConfigPath = str(tmp_path / "vaibify.yml")
        with open(sConfigPath, "w") as fileHandle:
            fileHandle.write("projectName: test")
        _fnUpdateYamlBoolField(sConfigPath, "neverSleep", False)
        with open(sConfigPath, "r") as fileHandle:
            sContent = fileHandle.read()
        assert "neverSleep: false" in sContent
        assert sContent.count("\n") >= 2

    def test_handles_space_colon(self, tmp_path):
        sConfigPath = str(tmp_path / "vaibify.yml")
        with open(sConfigPath, "w") as fileHandle:
            fileHandle.write("neverSleep : false\n")
        _fnUpdateYamlBoolField(sConfigPath, "neverSleep", True)
        with open(sConfigPath, "r") as fileHandle:
            sContent = fileHandle.read()
        assert "neverSleep: true" in sContent


# ---------------------------------------------------------------
# _fnRejectDuplicateProjectName (lines 589-596)
# ---------------------------------------------------------------

class TestRejectDuplicateProjectName:
    def test_rejects_registered_duplicate(self):
        from fastapi import HTTPException
        with patch(
            "vaibify.config.registryManager.flistGetAllProjects",
            return_value=[
                {"sName": "my-proj", "sDirectory": "/some/path"},
            ],
        ):
            with pytest.raises(HTTPException) as excInfo:
                _fnRejectDuplicateProjectName("my-proj")
            assert excInfo.value.status_code == 409
            assert "already registered" in excInfo.value.detail

    def test_rejects_docker_container_duplicate(self):
        from fastapi import HTTPException
        with patch(
            "vaibify.config.registryManager.flistGetAllProjects",
            return_value=[],
        ), patch(
            "vaibify.gui.registryRoutes._fbDockerContainerExists",
            return_value=True,
        ):
            with pytest.raises(HTTPException) as excInfo:
                _fnRejectDuplicateProjectName("my-proj")
            assert excInfo.value.status_code == 409
            assert "Docker container" in excInfo.value.detail

    def test_passes_when_unique(self):
        with patch(
            "vaibify.config.registryManager.flistGetAllProjects",
            return_value=[],
        ), patch(
            "vaibify.gui.registryRoutes._fbDockerContainerExists",
            return_value=False,
        ):
            _fnRejectDuplicateProjectName("new-proj")


# ---------------------------------------------------------------
# _fbDockerContainerExists (lines 613-614)
# ---------------------------------------------------------------

class TestDockerContainerExists:
    def test_returns_true_when_name_matches(self):
        mockResult = MagicMock()
        mockResult.stdout = "my-proj\n"
        with patch("subprocess.run", return_value=mockResult):
            assert _fbDockerContainerExists("my-proj") is True

    def test_returns_false_when_no_match(self):
        mockResult = MagicMock()
        mockResult.stdout = "other-proj\n"
        with patch("subprocess.run", return_value=mockResult):
            assert _fbDockerContainerExists("my-proj") is False

    def test_returns_false_on_file_not_found(self):
        with patch("subprocess.run", side_effect=FileNotFoundError):
            assert _fbDockerContainerExists("my-proj") is False

    def test_returns_false_on_timeout(self):
        with patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired("docker", 5),
        ):
            assert _fbDockerContainerExists("my-proj") is False


# ---------------------------------------------------------------
# _fnRegisterNewProject (lines 642-643)
# ---------------------------------------------------------------

class TestRegisterNewProject:
    def test_raises_409_on_value_error(self):
        from fastapi import HTTPException
        with patch(
            "vaibify.config.registryManager.fnAddProject",
            side_effect=ValueError("already registered"),
        ):
            with pytest.raises(HTTPException) as excInfo:
                _fnRegisterNewProject("/some/dir")
            assert excInfo.value.status_code == 409
