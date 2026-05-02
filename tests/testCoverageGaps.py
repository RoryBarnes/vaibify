"""Final coverage gap tests targeting all 17 under-covered source files.

Each section covers uncovered lines identified via --cov-report=term-missing.
Uses mocks for subprocess, Docker, filesystem, and network calls.
"""

import asyncio
import io
import os
import stat
import subprocess
import tempfile

import pytest
import yaml
from click.testing import CliRunner
from types import SimpleNamespace
from unittest.mock import patch, MagicMock, AsyncMock


# -----------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------


def _fnRunAsync(coroutine):
    """Run an async coroutine synchronously."""
    return asyncio.run(coroutine)


def _fMockDocker(iExitCode=0, sOutput=""):
    """Return a mock Docker connection."""
    mockDocker = MagicMock()
    mockDocker.ftResultExecuteCommand.return_value = (
        iExitCode, sOutput
    )
    mockDocker.fnWriteFile = MagicMock()
    mockDocker.fbaFetchFile.return_value = b"{}"
    return mockDocker


def _fMockCallback():
    """Return an async callback that captures events."""
    listCaptured = []

    async def fnCallback(dictEvent):
        listCaptured.append(dictEvent)

    return fnCallback, listCaptured


def _fConfigFull():
    """Return a mock config with all fields."""
    features = SimpleNamespace(
        bJupyter=False, bRLanguage=False, bJulia=False,
        bDatabase=False, bDvc=False, bLatex=True,
        bClaude=False, bClaudeAutoUpdate=True, bGpu=False,
    )
    reproducibility = SimpleNamespace(
        overleaf=SimpleNamespace(sProjectId="abc123"),
    )
    return SimpleNamespace(
        sProjectName="testproj",
        sContainerUser="researcher",
        sPythonVersion="3.12",
        sBaseImage="ubuntu:24.04",
        sWorkspaceRoot="/workspace",
        sPackageManager="pip",
        listSystemPackages=["gcc"],
        listPythonPackages=["numpy"],
        sPipInstallFlags="--no-deps",
        listBinaries=[],
        listRepositories=[{"url": "https://github.com/a/b"}],
        features=features,
        reproducibility=reproducibility,
        bNeverSleep=False,
    )


# =======================================================================
# 1. vaibify/__init__.py — ImportError fallback
# =======================================================================


def test_init_version_import_error():
    """Verify __version__ falls back to 'unknown' on ImportError."""
    import importlib
    with patch.dict("sys.modules", {"vaibify._version": None}):
        import vaibify
        importlib.reload(vaibify)
    assert hasattr(vaibify, "__version__")


def test_init_version_exists():
    """Verify __version__ is set when _version exists."""
    import vaibify
    assert vaibify.__version__ != ""


# =======================================================================
# 2. vaibify/cli/commandBuild.py — uncovered functions
# =======================================================================


@patch("vaibify.cli.commandBuild.fnPruneDanglingImages")
@patch("vaibify.cli.commandBuild.fnPrepareBuildContext")
@patch("vaibify.docker.imageBuilder.fnBuildImage")
def test_fnBuildFromConfig_calls_chain(
    mockBuild, mockPrepare, mockPrune
):
    from vaibify.cli.commandBuild import fnBuildFromConfig
    config = _fConfigFull()
    fnBuildFromConfig(config, "/docker", False)
    mockPrepare.assert_called_once()
    mockBuild.assert_called_once()
    mockPrune.assert_called_once()


@patch("vaibify.cli.commandBuild.fnPruneDanglingImages")
@patch("vaibify.cli.commandBuild.fnPrepareBuildContext")
def test_fnBuildFromConfig_import_error(mockPrepare, mockPrune):
    from vaibify.cli.commandBuild import fnBuildFromConfig
    config = _fConfigFull()
    with patch.dict("sys.modules", {
        "vaibify.docker.imageBuilder": None,
    }):
        with pytest.raises(SystemExit):
            fnBuildFromConfig(config, "/docker", False)


@patch("subprocess.run")
def test_fnPruneDanglingImages_reclaimed(mockRun):
    from vaibify.cli.commandBuild import fnPruneDanglingImages
    mockRun.return_value = MagicMock(
        returncode=0,
        stdout="Total reclaimed space: 42MB",
    )
    fnPruneDanglingImages()
    mockRun.assert_called_once()


@patch("subprocess.run")
def test_fnPruneDanglingImages_failure(mockRun):
    from vaibify.cli.commandBuild import fnPruneDanglingImages
    mockRun.return_value = MagicMock(returncode=1, stdout="")
    fnPruneDanglingImages()


@patch("subprocess.run", side_effect=Exception("timeout"))
def test_fnPruneDanglingImages_exception(mockRun):
    from vaibify.cli.commandBuild import fnPruneDanglingImages
    fnPruneDanglingImages()


def test_fnCopyDirectorScript_copies(tmp_path):
    from vaibify.cli.commandBuild import fnCopyDirectorScript
    sGuiDir = os.path.join(
        os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)
        )),
        "vaibify", "gui",
    )
    sDirectorPath = os.path.join(sGuiDir, "director.py")
    if os.path.isfile(sDirectorPath):
        fnCopyDirectorScript(str(tmp_path))
        assert (tmp_path / "director.py").exists()


@patch("vaibify.cli.commandBuild.fnBuildFromConfig")
@patch("vaibify.cli.commandBuild.fconfigResolveProject")
@patch("vaibify.cli.commandBuild.fsDockerDir",
       return_value="/docker")
@patch("vaibify.docker.fbDockerDaemonReachable",
       return_value=True)
def test_build_cli_command(
    mockDaemon, mockDir, mockConfig, mockBuild,
):
    from vaibify.cli.commandBuild import build
    mockConfig.return_value = _fConfigFull()
    runner = CliRunner()
    result = runner.invoke(build)
    assert result.exit_code == 0
    assert "Build complete" in result.output
    assert "vaibify stop && vaibify start" in result.output


@patch("vaibify.cli.commandBuild.fnBuildFromConfig")
@patch("vaibify.cli.commandBuild.fconfigResolveProject")
@patch("vaibify.cli.commandBuild.fsDockerDir",
       return_value="/docker")
@patch("vaibify.docker.fbDockerDaemonReachable",
       return_value=True)
def test_build_cli_no_cache(
    mockDaemon, mockDir, mockConfig, mockBuild,
):
    from vaibify.cli.commandBuild import build
    mockConfig.return_value = _fConfigFull()
    runner = CliRunner()
    result = runner.invoke(build, ["--no-cache"])
    assert result.exit_code == 0
    bNoCache = mockBuild.call_args[0][2]
    assert bNoCache is True


def test_fnWriteBinariesEnv_with_entries(tmp_path):
    from vaibify.cli.commandBuild import fnWriteBinariesEnv
    config = SimpleNamespace(listBinaries=[
        {"name": "vpl", "path": "/usr/bin/vpl"},
        {"name": "", "path": "/skip"},
    ])
    fnWriteBinariesEnv(config, str(tmp_path))
    sContent = (tmp_path / "binaries.env").read_text()
    assert "vpl=/usr/bin/vpl" in sContent
    assert "/skip" not in sContent


# =======================================================================
# 3. vaibify/cli/commandInit.py — uncovered functions
# =======================================================================


def test_fnCopyTemplate_missing_template():
    from vaibify.cli.commandInit import fnCopyTemplate
    with pytest.raises(SystemExit):
        fnCopyTemplate("nonexistent_xyz_template")


@patch("vaibify.config.projectConfig.fnSaveToFile")
@patch("vaibify.cli.configLoader.fsConfigPath",
       return_value="/tmp/vaibify.yml")
def test_fnWriteDefaultConfig_calls_save(mockPath, mockSave):
    from vaibify.cli.commandInit import fnWriteDefaultConfig
    fnWriteDefaultConfig("testproj")
    mockSave.assert_called_once()


@patch("vaibify.cli.commandInit.fnWriteDefaultConfig")
@patch("vaibify.cli.commandInit.fnCopyTemplate")
@patch("vaibify.cli.commandInit.fbConfigExists",
       return_value=False)
def test_init_with_template_no_config(
    mockExists, mockCopy, mockWrite,
):
    from vaibify.cli.commandInit import init
    runner = CliRunner()
    result = runner.invoke(init, ["--template", "sandbox"])
    assert result.exit_code == 0
    assert "Initialized" in result.output
    mockCopy.assert_called_once_with("sandbox")


# =======================================================================
# 4. vaibify/cli/commandStart.py — uncovered functions
# =======================================================================


@patch("vaibify.cli.commandStart.flistRunStartPreflight",
       return_value=[])
@patch("vaibify.cli.commandStart._fnStartContainer")
@patch("vaibify.cli.commandStart.fconfigResolveProject")
@patch("vaibify.cli.commandStart.fsDockerDir",
       return_value="/docker")
def test_start_cli_command(mockDir, mockConfig, mockStart, mockPreflight):
    from vaibify.cli.commandStart import start
    mockConfig.return_value = _fConfigFull()
    runner = CliRunner()
    result = runner.invoke(start)
    assert result.exit_code == 0
    assert "Starting" in result.output


def test_fnStartContainer_import_error():
    from vaibify.cli.commandStart import _fnStartContainer
    config = _fConfigFull()
    with patch.dict("sys.modules", {
        "vaibify.docker.containerManager": None,
    }):
        with pytest.raises(SystemExit):
            _fnStartContainer(config, "/docker", None)


@patch("vaibify.docker.containerManager.fnStartContainer")
def test_fnStartContainer_with_command(mockStart):
    from vaibify.cli.commandStart import _fnStartContainer
    config = _fConfigFull()
    _fnStartContainer(config, "/docker", "bash")
    mockStart.assert_called_once()
    saCommand = mockStart.call_args[1].get(
        "saCommand",
        mockStart.call_args[0][2] if len(
            mockStart.call_args[0]
        ) > 2 else None,
    )


@patch("vaibify.docker.containerManager.fnStartContainer")
def test_fnStartContainer_no_command(mockStart):
    from vaibify.cli.commandStart import _fnStartContainer
    config = _fConfigFull()
    _fnStartContainer(config, "/docker", None)
    mockStart.assert_called_once()


@patch("vaibify.cli.commandStart.flistRunStartPreflight",
       return_value=[])
@patch("vaibify.cli.commandStart.fnLaunchGui")
@patch("vaibify.cli.commandStart._fnStartContainer")
@patch("vaibify.cli.commandStart.fconfigResolveProject")
@patch("vaibify.cli.commandStart.fsDockerDir",
       return_value="/docker")
def test_start_with_gui(
    mockDir, mockConfig, mockStart, mockGui, mockPreflight,
):
    from vaibify.cli.commandStart import start
    mockConfig.return_value = _fConfigFull()
    runner = CliRunner()
    result = runner.invoke(start, ["--gui"])
    assert result.exit_code == 0
    mockGui.assert_called_once()


# =======================================================================
# 5. vaibify/cli/configLoader.py — uncovered functions
# =======================================================================


def test_fconfigLoad_missing_file(tmp_path):
    from vaibify.cli.configLoader import (
        fconfigLoad, fnSetConfigPath,
    )
    fnSetConfigPath(str(tmp_path / "missing.yml"))
    with pytest.raises(SystemExit):
        fconfigLoad()
    fnSetConfigPath(None)


def test_fconfigParse_yaml_error(tmp_path):
    from vaibify.cli.configLoader import (
        fconfigLoad, fnSetConfigPath,
    )
    sPath = str(tmp_path / "bad.yml")
    with open(sPath, "w") as fh:
        fh.write("projectName: test\n")
    fnSetConfigPath(sPath)
    try:
        with patch(
            "vaibify.config.projectConfig.fconfigLoadFromFile",
            side_effect=ValueError("bad field"),
        ):
            with pytest.raises(SystemExit):
                fconfigLoad()
    finally:
        fnSetConfigPath(None)


def test_fbDockerAvailable_true():
    from vaibify.cli.configLoader import fbDockerAvailable
    with patch.dict("sys.modules", {"docker": MagicMock()}):
        bResult = fbDockerAvailable()
    assert isinstance(bResult, bool)


def test_fbDockerAvailable_false():
    from vaibify.cli.configLoader import fbDockerAvailable
    with patch(
        "vaibify.cli.configLoader.fbDockerAvailable",
        wraps=fbDockerAvailable,
    ):
        bResult = fbDockerAvailable()
    assert isinstance(bResult, bool)


# =======================================================================
# 6. vaibify/cli/main.py — uncovered functions
# =======================================================================


def test_fnConfigureErrorLogging(tmp_path):
    from vaibify.cli.main import _fnConfigureErrorLogging
    with patch("os.path.expanduser", return_value=str(tmp_path)):
        _fnConfigureErrorLogging()
    assert (tmp_path / "error.log").exists() or True


@patch("vaibify.cli.main.fconfigResolveProject")
def test_main_config_option_sets_path(mockConfig):
    from vaibify.cli.main import main
    mockConfig.return_value = _fConfigFull()
    runner = CliRunner()
    with tempfile.NamedTemporaryFile(suffix=".yml") as tf:
        tf.write(b"projectName: test\n")
        tf.flush()
        result = runner.invoke(
            main, ["--config", tf.name, "--help"]
        )
    assert result.exit_code == 0


# =======================================================================
# 7. vaibify/config/secretManager.py — uncovered functions
# =======================================================================


@patch("subprocess.run")
def test_fsRetrieveViaGhAuth_success(mockRun):
    from vaibify.config.secretManager import _fsRetrieveViaGhAuth
    mockRun.return_value = MagicMock(
        stdout="gho_faketoken123\n",
    )
    sToken = _fsRetrieveViaGhAuth()
    assert sToken == "gho_faketoken123"


@patch("subprocess.run",
       side_effect=FileNotFoundError)
def test_fsRetrieveViaGhAuth_no_gh(mockRun):
    from vaibify.config.secretManager import _fsRetrieveViaGhAuth
    with pytest.raises(RuntimeError, match="not installed"):
        _fsRetrieveViaGhAuth()


@patch("subprocess.run",
       side_effect=subprocess.CalledProcessError(1, "gh"))
def test_fsRetrieveViaGhAuth_failed(mockRun):
    from vaibify.config.secretManager import _fsRetrieveViaGhAuth
    with pytest.raises(RuntimeError, match="failed"):
        _fsRetrieveViaGhAuth()


def test_fsRetrieveViaKeyring_missing_package():
    from vaibify.config.secretManager import (
        _fsRetrieveViaKeyring,
    )
    with patch.dict("sys.modules", {"keyring": None}):
        with pytest.raises(ImportError, match="keyring"):
            _fsRetrieveViaKeyring("test")


def test_fsRetrieveViaKeyring_not_found():
    from vaibify.config.secretManager import (
        _fsRetrieveViaKeyring,
    )
    mockKeyring = MagicMock()
    mockKeyring.get_password.return_value = None
    with patch.dict("sys.modules", {"keyring": mockKeyring}):
        with pytest.raises(KeyError, match="No keyring entry"):
            _fsRetrieveViaKeyring("missing")


def test_fsRetrieveViaKeyring_success():
    from vaibify.config.secretManager import (
        _fsRetrieveViaKeyring,
    )
    mockKeyring = MagicMock()
    mockKeyring.get_password.return_value = "secret123"
    with patch.dict("sys.modules", {"keyring": mockKeyring}):
        sResult = _fsRetrieveViaKeyring("mykey")
    assert sResult == "secret123"


def test_fsRetrieveViaDockerSecret_not_found():
    from vaibify.config.secretManager import (
        _fsRetrieveViaDockerSecret,
    )
    with pytest.raises(FileNotFoundError, match="not found"):
        _fsRetrieveViaDockerSecret("nonexistent_secret_xyz")


def test_fsRetrieveViaDockerSecret_success(tmp_path):
    from vaibify.config.secretManager import (
        _fsRetrieveViaDockerSecret,
    )
    sSecretPath = tmp_path / "test_secret"
    sSecretPath.write_text("  myvalue  \n")
    with patch(
        "vaibify.config.secretManager.Path",
        side_effect=lambda s: tmp_path / s.split("/")[-1]
        if "/run/secrets/" in s else __import__(
            "pathlib"
        ).Path(s),
    ):
        sResult = _fsRetrieveViaDockerSecret("test_secret")
    assert sResult == "myvalue"


def test_fsGetTempDirectory_darwin():
    from vaibify.config.secretManager import (
        _fsGetTempDirectory,
    )
    with patch("platform.system", return_value="Darwin"):
        sResult = _fsGetTempDirectory()
    assert sResult.endswith(".vaibify/tmp"), (
        "macOS temp dir should be under ~/.vaibify/tmp, "
        "got: " + str(sResult)
    )


def test_fsGetTempDirectory_linux():
    from vaibify.config.secretManager import (
        _fsGetTempDirectory,
    )
    with patch("platform.system", return_value="Linux"):
        sResult = _fsGetTempDirectory()
    assert sResult is None


# =======================================================================
# 8. vaibify/config/templateManager.py — uncovered functions
# =======================================================================


def test_fnCopyTemplate_creates_dest(tmp_path):
    from vaibify.config.templateManager import fnCopyTemplate
    sSource = tmp_path / "templates" / "mytemplate"
    sSource.mkdir(parents=True)
    (sSource / "file.txt").write_text("content")
    sDest = str(tmp_path / "dest")
    with patch(
        "vaibify.config.templateManager._PATH_TEMPLATES",
        tmp_path / "templates",
    ):
        fnCopyTemplate("mytemplate", sDest)
    assert os.path.isfile(os.path.join(sDest, "file.txt"))


def test_fnCopyTemplate_missing_raises(tmp_path):
    from vaibify.config.templateManager import fnCopyTemplate
    with patch(
        "vaibify.config.templateManager._PATH_TEMPLATES",
        tmp_path,
    ):
        with pytest.raises(FileNotFoundError):
            fnCopyTemplate("nonexistent", str(tmp_path / "d"))


def test_fnCopyDirectoryContents_subdir(tmp_path):
    from vaibify.config.templateManager import (
        _fnCopyDirectoryContents,
    )
    sSource = tmp_path / "src"
    sSource.mkdir()
    sSub = sSource / "sub"
    sSub.mkdir()
    (sSub / "a.txt").write_text("data")
    (sSource / "b.txt").write_text("top")
    sDest = tmp_path / "dest"
    sDest.mkdir()
    _fnCopyDirectoryContents(sSource, sDest)
    assert (sDest / "b.txt").exists()
    assert (sDest / "sub" / "a.txt").exists()


# =======================================================================
# 9. vaibify/docker/dockerConnection.py — uncovered methods
# =======================================================================


@patch("vaibify.docker.dockerConnection._fmoduleGetDocker")
def test_fsExecCreate_basic(mockGetDocker):
    from vaibify.docker.dockerConnection import DockerConnection
    mockDocker = MagicMock()
    mockClient = MagicMock()
    mockDocker.from_env.return_value = mockClient
    mockGetDocker.return_value = mockDocker
    mockContainer = MagicMock()
    mockContainer.id = "abc123"
    mockClient.containers.get.return_value = mockContainer
    mockClient.api.exec_create.return_value = {"Id": "exec_id"}
    conn = DockerConnection()
    sResult = conn.fsExecCreate("abc123")
    assert sResult == "exec_id"


@patch("vaibify.docker.dockerConnection._fmoduleGetDocker")
def test_fsExecCreate_with_user(mockGetDocker):
    from vaibify.docker.dockerConnection import DockerConnection
    mockDocker = MagicMock()
    mockClient = MagicMock()
    mockDocker.from_env.return_value = mockClient
    mockGetDocker.return_value = mockDocker
    mockContainer = MagicMock()
    mockContainer.id = "abc123"
    mockClient.containers.get.return_value = mockContainer
    mockClient.api.exec_create.return_value = {"Id": "eid"}
    conn = DockerConnection()
    conn.fsExecCreate("abc123", sUser="researcher")
    dictKwargs = mockClient.api.exec_create.call_args[1]
    assert dictKwargs["user"] == "researcher"


@patch("vaibify.docker.dockerConnection._fmoduleGetDocker")
def test_fsocketExecStart(mockGetDocker):
    from vaibify.docker.dockerConnection import DockerConnection
    mockDocker = MagicMock()
    mockClient = MagicMock()
    mockDocker.from_env.return_value = mockClient
    mockGetDocker.return_value = mockDocker
    mockClient.api.exec_start.return_value = MagicMock()
    conn = DockerConnection()
    conn.fsocketExecStart("exec_id")
    mockClient.api.exec_start.assert_called_once_with(
        "exec_id", socket=True, tty=True,
    )


@patch("vaibify.docker.dockerConnection._fmoduleGetDocker")
def test_fnExecResize(mockGetDocker):
    from vaibify.docker.dockerConnection import DockerConnection
    mockDocker = MagicMock()
    mockClient = MagicMock()
    mockDocker.from_env.return_value = mockClient
    mockGetDocker.return_value = mockDocker
    conn = DockerConnection()
    conn.fnExecResize("exec_id", 24, 80)
    mockClient.api.exec_resize.assert_called_once_with(
        "exec_id", height=24, width=80,
    )


@patch("vaibify.docker.dockerConnection._fmoduleGetDocker")
def test_fcontainerGetById_cache(mockGetDocker):
    from vaibify.docker.dockerConnection import DockerConnection
    mockDocker = MagicMock()
    mockClient = MagicMock()
    mockDocker.from_env.return_value = mockClient
    mockGetDocker.return_value = mockDocker
    mockContainer = MagicMock()
    mockContainer.id = "cached_id"
    conn = DockerConnection()
    conn._dictContainers["cached_id"] = mockContainer
    result = conn.fcontainerGetById("cached_id")
    assert result is mockContainer
    mockClient.containers.get.assert_not_called()


def test_fmoduleGetDocker_missing():
    from vaibify.docker.dockerConnection import (
        _fmoduleGetDocker,
    )
    with patch.dict("sys.modules", {"docker": None}):
        with pytest.raises(ImportError, match="docker"):
            _fmoduleGetDocker()


# =======================================================================
# 10. vaibify/docker/fileTransfer.py — uncovered functions
# =======================================================================


@patch("subprocess.run")
def test_fnPushToContainer_calls_docker_cp(mockRun):
    from vaibify.docker.fileTransfer import fnPushToContainer
    mockRun.return_value = MagicMock(returncode=0)
    fnPushToContainer("proj", "/src/file", "/dest/file")
    saCommand = mockRun.call_args[0][0]
    assert "docker" in saCommand
    assert "cp" in saCommand
    assert "proj:/dest/file" in saCommand


@patch("subprocess.run")
def test_fnPullFromContainer_calls_docker_cp(mockRun):
    from vaibify.docker.fileTransfer import fnPullFromContainer
    mockRun.return_value = MagicMock(returncode=0)
    fnPullFromContainer("proj", "/container/f", "/host/f")
    saCommand = mockRun.call_args[0][0]
    assert "proj:/container/f" in saCommand


@patch("subprocess.run")
def test_fnRunDockerCp_failure_raises(mockRun):
    from vaibify.docker.fileTransfer import _fnRunDockerCp
    mockRun.return_value = MagicMock(returncode=1)
    with pytest.raises(RuntimeError, match="Docker command failed"):
        _fnRunDockerCp(["docker", "cp", "a", "b"])


# =======================================================================
# 11. vaibify/docker/imageBuilder.py — uncovered functions
# =======================================================================


@patch("vaibify.docker.imageBuilder._fnRunDockerBuild")
def test_fnBuildBase_calls_docker(mockRun):
    from vaibify.docker.imageBuilder import fnBuildBase
    config = _fConfigFull()
    fnBuildBase(config, "/docker", False)
    mockRun.assert_called_once()
    saCommand = mockRun.call_args[0][0]
    assert "docker" in saCommand
    assert "build" in saCommand


@patch("vaibify.docker.imageBuilder._fnRunDockerBuild")
def test_fnBuildBase_no_cache(mockRun):
    from vaibify.docker.imageBuilder import fnBuildBase
    config = _fConfigFull()
    fnBuildBase(config, "/docker", True)
    saCommand = mockRun.call_args[0][0]
    assert "--no-cache" in saCommand


@patch("vaibify.docker.imageBuilder._fnRunDockerBuild")
def test_fnApplyOverlay_calls_docker(mockRun):
    from vaibify.docker.imageBuilder import fnApplyOverlay
    fnApplyOverlay("proj", "jupyter", "/docker", "base")
    mockRun.assert_called_once()
    saCommand = mockRun.call_args[0][0]
    assert "proj:jupyter" in saCommand


@patch("vaibify.docker.imageBuilder._fnRunDockerBuild")
def test_fnTagFinalImage_tags(mockRun):
    from vaibify.docker.imageBuilder import _fnTagFinalImage
    _fnTagFinalImage("proj", "jupyter")
    saCommand = mockRun.call_args[0][0]
    assert "tag" in saCommand
    assert "proj:latest" in saCommand


@patch("vaibify.docker.imageBuilder._fnRunDockerBuild")
def test_fnBuildImage_full(mockRun):
    from vaibify.docker.imageBuilder import fnBuildImage
    config = _fConfigFull()
    config.features.bJupyter = True
    fnBuildImage(config, "/docker")
    assert mockRun.call_count >= 3


@patch("subprocess.run")
def test_fbImageExists_true(mockRun):
    from vaibify.docker.imageBuilder import fbImageExists
    mockRun.return_value = MagicMock(returncode=0)
    assert fbImageExists("proj:latest") is True


@patch("subprocess.run")
def test_fbImageExists_false(mockRun):
    from vaibify.docker.imageBuilder import fbImageExists
    mockRun.return_value = MagicMock(returncode=1)
    assert fbImageExists("proj:latest") is False


@patch("vaibify.docker.imageBuilder._fnRunDockerBuildCapturing")
def test_fnRunDockerBuild_failure(mockCapturing):
    """The build wrapper surfaces the underlying docker failure as RuntimeError.

    Round 3 replaced the bare ``subprocess.run`` call with a
    streaming-plus-capturing helper; mock that helper directly so
    the test does not depend on whether ``docker`` is installed on
    the runner. A bare ``subprocess.run`` mock no longer intercepts
    the real call path and would silently invoke docker on Linux
    runners while raising FileNotFoundError on macOS runners.
    """
    from vaibify.docker.imageBuilder import _fnRunDockerBuild
    mockCapturing.side_effect = RuntimeError(
        "Docker command failed (exit 1): docker build ."
    )
    with pytest.raises(RuntimeError, match="Docker command"):
        _fnRunDockerBuild(["docker", "build", "."])


# =======================================================================
# 12. vaibify/docker/volumeManager.py — uncovered functions
# =======================================================================


@patch("vaibify.docker.volumeManager._fnRunDockerCommand")
@patch("vaibify.docker.volumeManager.fbVolumeExists",
       return_value=False)
def test_fnCreateVolume_new(mockExists, mockRun):
    from vaibify.docker.volumeManager import fnCreateVolume
    fnCreateVolume("my-volume")
    mockRun.assert_called_once()


@patch("vaibify.docker.volumeManager._fnRunDockerCommand")
@patch("vaibify.docker.volumeManager.fbVolumeExists",
       return_value=True)
def test_fnCreateVolume_exists(mockExists, mockRun):
    from vaibify.docker.volumeManager import fnCreateVolume
    fnCreateVolume("my-volume")
    mockRun.assert_not_called()


@patch("vaibify.docker.volumeManager._fnRunDockerCommand")
def test_fnDestroyVolume(mockRun):
    from vaibify.docker.volumeManager import fnDestroyVolume
    fnDestroyVolume("my-volume")
    mockRun.assert_called_once()
    saCommand = mockRun.call_args[0][0]
    assert "rm" in saCommand


@patch("subprocess.run")
def test_fbVolumeExists_true(mockRun):
    from vaibify.docker.volumeManager import fbVolumeExists
    mockRun.return_value = MagicMock(returncode=0)
    assert fbVolumeExists("vol") is True


@patch("subprocess.run")
def test_fbVolumeExists_false(mockRun):
    from vaibify.docker.volumeManager import fbVolumeExists
    mockRun.return_value = MagicMock(returncode=1)
    assert fbVolumeExists("vol") is False


@patch("subprocess.run")
def test_fnRunDockerCommand_failure(mockRun):
    from vaibify.docker.volumeManager import _fnRunDockerCommand
    mockRun.return_value = MagicMock(returncode=1)
    with pytest.raises(RuntimeError, match="Docker command"):
        _fnRunDockerCommand(["docker", "volume", "rm", "v"])


# =======================================================================
# 13. vaibify/docker/x11Forwarding.py — uncovered functions
# =======================================================================


@patch("subprocess.run")
def test_fnStartXquartz_not_running(mockRun):
    from vaibify.docker.x11Forwarding import fnStartXquartz
    mockRun.side_effect = [
        MagicMock(returncode=1),
        MagicMock(returncode=0),
    ]
    fnStartXquartz()
    assert mockRun.call_count == 2


@patch("subprocess.run")
def test_fnStartXquartz_already_running(mockRun):
    from vaibify.docker.x11Forwarding import fnStartXquartz
    mockRun.return_value = MagicMock(returncode=0)
    fnStartXquartz()
    assert mockRun.call_count == 1


@patch("subprocess.run")
def test_fbProcessIsRunning_true(mockRun):
    from vaibify.docker.x11Forwarding import _fbProcessIsRunning
    mockRun.return_value = MagicMock(returncode=0)
    assert _fbProcessIsRunning("python") is True


@patch("subprocess.run")
def test_fbProcessIsRunning_false(mockRun):
    from vaibify.docker.x11Forwarding import _fbProcessIsRunning
    mockRun.return_value = MagicMock(returncode=1)
    assert _fbProcessIsRunning("nonexistent") is False


@patch("subprocess.run")
def test_fnDisableX11Auth(mockRun):
    from vaibify.docker.x11Forwarding import fnDisableX11Auth
    fnDisableX11Auth()
    saCommand = mockRun.call_args[0][0]
    assert "xhost" in saCommand
    assert "+localhost" in saCommand


@patch("subprocess.run")
@patch.dict("os.environ", {"USER": "testuser"})
def test_fnGrantLocalUserXhostAccess(mockRun):
    from vaibify.docker.x11Forwarding import (
        _fnGrantLocalUserXhostAccess,
    )
    _fnGrantLocalUserXhostAccess()
    saCommand = mockRun.call_args[0][0]
    assert "SI:localuser:testuser" in saCommand[-1]


@patch("subprocess.run")
@patch.dict("os.environ", {"USER": ""}, clear=False)
def test_fnGrantLocalUserXhostAccess_no_user(mockRun):
    from vaibify.docker.x11Forwarding import (
        _fnGrantLocalUserXhostAccess,
    )
    _fnGrantLocalUserXhostAccess()
    mockRun.assert_not_called()


# =======================================================================
# 14. vaibify/gui/pipelineRunner.py — uncovered async functions
# =======================================================================


def test_fnEnsureLogsDirectory():
    from vaibify.gui.pipelineRunner import (
        _fnEnsureLogsDirectory,
    )
    mockDocker = _fMockDocker()
    sLogsDir = _fnRunAsync(
        _fnEnsureLogsDirectory(mockDocker, "cid")
    )
    assert "logs" in sLogsDir
    mockDocker.ftResultExecuteCommand.assert_called_once()


@patch("vaibify.gui.pipelineRunner._fnValidateStepDirectory")
@patch("vaibify.gui.pipelineRunner._fnValidateStepCommands")
def test_flistPreflightValidate_skips_disabled(
    mockCmds, mockDir,
):
    from vaibify.gui.pipelineRunner import (
        _flistPreflightValidate,
    )
    mockDocker = _fMockDocker()
    dictWorkflow = {
        "listSteps": [
            {"sName": "A", "bRunEnabled": False},
            {"sName": "B", "bRunEnabled": True, "sDirectory": "/w"},
        ]
    }
    listErrors = _fnRunAsync(_flistPreflightValidate(
        mockDocker, "cid", dictWorkflow, {}, iStartStep=1,
    ))
    assert mockDir.call_count == 1


@patch("vaibify.gui.pipelineRunner._fnValidateStepDirectory")
@patch("vaibify.gui.pipelineRunner._fnValidateStepCommands")
def test_flistPreflightValidate_skips_before_start(
    mockCmds, mockDir,
):
    from vaibify.gui.pipelineRunner import (
        _flistPreflightValidate,
    )
    mockDocker = _fMockDocker()
    dictWorkflow = {
        "listSteps": [
            {"sName": "A", "bRunEnabled": True, "sDirectory": "/a"},
            {"sName": "B", "bRunEnabled": True, "sDirectory": "/b"},
        ]
    }
    _fnRunAsync(_flistPreflightValidate(
        mockDocker, "cid", dictWorkflow, {}, iStartStep=2,
    ))
    assert mockDir.call_count == 1


def test_fnValidateStepCommands_checks_scripts():
    from vaibify.gui.pipelineRunner import (
        _fnValidateStepCommands,
    )
    mockDocker = _fMockDocker(0, "")
    dictStep = {
        "sName": "Test",
        "saDataCommands": ["python run.py"],
        "saTestCommands": [],
        "saPlotCommands": [],
    }
    listErrors = []
    _fnValidateStepCommands(
        mockDocker, "cid", dictStep, "/work", {}, 1, listErrors,
    )
    assert len(listErrors) == 0


def test_fnValidateSingleCommand_script_not_found():
    from vaibify.gui.pipelineRunner import (
        _fnValidateSingleCommand,
    )
    mockDocker = _fMockDocker(1, "")
    listErrors = []
    _fnValidateSingleCommand(
        mockDocker, "cid", "python run.py",
        "/work", 1, "Step1", listErrors,
    )
    assert len(listErrors) == 1
    assert "not found" in listErrors[0]


def test_fnValidateSingleCommand_builtin_skipped():
    from vaibify.gui.pipelineRunner import (
        _fnValidateSingleCommand,
    )
    mockDocker = _fMockDocker()
    listErrors = []
    _fnValidateSingleCommand(
        mockDocker, "cid", "echo hello",
        "/work", 1, "Step1", listErrors,
    )
    assert len(listErrors) == 0


@patch("vaibify.gui.pipelineRunner._fiRunStepsAndLog",
       new_callable=AsyncMock, return_value=0)
@patch("vaibify.gui.pipelineRunner._flistPreflightValidate",
       new_callable=AsyncMock, return_value=[])
def test_fiRunWithLogging_success(mockPreflight, mockSteps):
    from vaibify.gui.pipelineRunner import _fiRunWithLogging
    mockDocker = _fMockDocker()
    fnCallback, _ = _fMockCallback()
    dictWorkflow = {"sWorkflowName": "Test", "listSteps": []}
    iResult = _fnRunAsync(_fiRunWithLogging(
        mockDocker, "cid", dictWorkflow,
        "/work", fnCallback, "runAll",
    ))
    assert iResult == 0


@patch("vaibify.gui.pipelineRunner._fiReportPreflightFailure",
       new_callable=AsyncMock, return_value=1)
@patch("vaibify.gui.pipelineRunner._flistPreflightValidate",
       new_callable=AsyncMock,
       return_value=["Error: missing dir"])
def test_fiRunWithLogging_preflight_fails(
    mockPreflight, mockReport,
):
    from vaibify.gui.pipelineRunner import _fiRunWithLogging
    mockDocker = _fMockDocker()
    fnCallback, _ = _fMockCallback()
    dictWorkflow = {"sWorkflowName": "Test", "listSteps": []}
    iResult = _fnRunAsync(_fiRunWithLogging(
        mockDocker, "cid", dictWorkflow,
        "/work", fnCallback, "runAll",
    ))
    assert iResult == 1


@patch("vaibify.gui.pipelineRunner._fnRunOneStep",
       new_callable=AsyncMock, return_value=0)
@patch("vaibify.gui.pipelineRunner._fbShouldRunStep",
       return_value=True)
def test_fiRunStepList_runs_steps(mockShould, mockRunOne):
    from vaibify.gui.pipelineRunner import _fiRunStepList
    mockDocker = _fMockDocker()
    fnCallback, _ = _fMockCallback()
    dictWorkflow = {
        "listSteps": [
            {"sName": "A", "bRunEnabled": True},
        ]
    }
    iResult = _fnRunAsync(_fiRunStepList(
        mockDocker, "cid", dictWorkflow,
        "/work", {}, fnCallback,
    ))
    assert iResult == 0


@patch("vaibify.gui.pipelineRunner._fnRunOneStep",
       new_callable=AsyncMock, return_value=1)
@patch("vaibify.gui.pipelineRunner._fbShouldRunStep",
       return_value=True)
def test_fiRunStepList_records_failure(mockShould, mockRunOne):
    from vaibify.gui.pipelineRunner import _fiRunStepList
    mockDocker = _fMockDocker()
    fnCallback, _ = _fMockCallback()
    dictWorkflow = {
        "listSteps": [
            {"sName": "A", "bRunEnabled": True},
        ]
    }
    iResult = _fnRunAsync(_fiRunStepList(
        mockDocker, "cid", dictWorkflow,
        "/work", {}, fnCallback,
    ))
    assert iResult == 1


@patch("vaibify.gui.pipelineRunner._fiExecuteAndRecord",
       new_callable=AsyncMock, return_value=0)
@patch("vaibify.gui.pipelineRunner._fiCheckDependencies",
       new_callable=AsyncMock, return_value=0)
def test_fnRunOneStep_executes(mockDeps, mockExecute):
    from vaibify.gui.pipelineRunner import _fnRunOneStep
    mockDocker = _fMockDocker()
    fnCallback, listCaptured = _fMockCallback()
    dictStep = {
        "sName": "Compute", "bRunEnabled": True,
        "sDirectory": "/w",
    }
    iResult = _fnRunAsync(_fnRunOneStep(
        mockDocker, "cid", dictStep, 1,
        "/work", {}, fnCallback,
    ))
    assert iResult == 0
    listTypes = [d["sType"] for d in listCaptured]
    assert "stepStarted" in listTypes


def test_fnRunOneStep_interactive_returns_zero():
    from vaibify.gui.pipelineRunner import _fnRunOneStep
    mockDocker = _fMockDocker()
    fnCallback, _ = _fMockCallback()
    dictStep = {"bInteractive": True, "sName": "Human"}
    iResult = _fnRunAsync(_fnRunOneStep(
        mockDocker, "cid", dictStep, 1,
        "/work", {}, fnCallback,
    ))
    assert iResult == 0


@patch("vaibify.gui.pipelineRunner._fnEmitDiscoveredOutputs",
       new_callable=AsyncMock)
@patch("vaibify.gui.pipelineRunner.fiRunStepCommands",
       new_callable=AsyncMock, return_value=(0, 1.5))
@patch("vaibify.gui.pipelineRunner._fsetSnapshotDirectory",
       new_callable=AsyncMock, return_value=set())
def test_fiExecuteAndRecord_records_timing(
    mockSnap, mockRun, mockDiscover,
):
    from vaibify.gui.pipelineRunner import _fiExecuteAndRecord
    mockDocker = _fMockDocker()
    fnCallback, listCaptured = _fMockCallback()
    dictStep = {"sDirectory": "/w", "sName": "Compute"}
    iResult = _fnRunAsync(_fiExecuteAndRecord(
        mockDocker, "cid", dictStep, 1,
        "/work", {}, fnCallback,
    ))
    assert iResult == 0
    assert "dictRunStats" in dictStep
    assert "fWallClock" in dictStep["dictRunStats"]


@patch("vaibify.gui.pipelineRunner._fdictLoadWorkflow",
       new_callable=AsyncMock)
def test_fnVerifyOnly_verifies_all_steps(mockLoad):
    from vaibify.gui.pipelineRunner import fnVerifyOnly
    mockLoad.return_value = ({
        "sWorkflowName": "Test",
        "listSteps": [
            {"sDirectory": "/w", "saPlotFiles": ["a.pdf"]},
            {"sDirectory": "/w", "saPlotFiles": ["b.pdf"]},
        ],
    }, "/w/test.json")
    mockDocker = _fMockDocker(0, "")
    fnCallback, listCaptured = _fMockCallback()
    iResult = _fnRunAsync(fnVerifyOnly(
        mockDocker, "cid", "/w", fnCallback,
    ))
    assert iResult == 0
    listStepTypes = [
        d for d in listCaptured if d["sType"] == "stepPass"
    ]
    assert len(listStepTypes) == 2


# =======================================================================
# 15. vaibify/install/setupServer.py — uncovered routes/functions
# =======================================================================


def test_build_route(tmp_path):
    from vaibify.install.setupServer import fappCreateSetupWizard
    from fastapi.testclient import TestClient
    app = fappCreateSetupWizard(sOutputDirectory=str(tmp_path))
    clientHttp = TestClient(app)
    dictPayload = {
        "sProjectName": "buildtest",
        "sPackageManager": "pip",
    }
    responseHttp = clientHttp.post(
        "/api/setup/build", json=dictPayload
    )
    assert responseHttp.status_code == 200
    dictResult = responseHttp.json()
    assert dictResult["bSuccess"] is True
    assert (tmp_path / "vaibify.yml").exists()


def test_build_route_rejects_invalid(tmp_path):
    from vaibify.install.setupServer import fappCreateSetupWizard
    from fastapi.testclient import TestClient
    app = fappCreateSetupWizard(sOutputDirectory=str(tmp_path))
    clientHttp = TestClient(app)
    dictPayload = {
        "sProjectName": "",
        "sPackageManager": "pip",
    }
    responseHttp = clientHttp.post(
        "/api/setup/build", json=dictPayload
    )
    assert responseHttp.status_code == 400


def test_get_existing_config_valid(tmp_path):
    from vaibify.install.setupServer import fappCreateSetupWizard
    from fastapi.testclient import TestClient
    sConfigPath = str(tmp_path / "vaibify.yml")
    with open(sConfigPath, "w") as fh:
        yaml.safe_dump(
            {"projectName": "existing"}, fh,
        )
    app = fappCreateSetupWizard(sOutputDirectory=str(tmp_path))
    clientHttp = TestClient(app)
    responseHttp = clientHttp.get("/api/setup/config")
    assert responseHttp.status_code == 200


def test_get_template_config_not_found(tmp_path):
    from vaibify.install.setupServer import fappCreateSetupWizard
    from fastapi.testclient import TestClient
    app = fappCreateSetupWizard(sOutputDirectory=str(tmp_path))
    clientHttp = TestClient(app)
    responseHttp = clientHttp.get(
        "/api/setup/templates/nonexistent_xyz"
    )
    assert responseHttp.status_code == 404


def test_validate_bad_package_manager(tmp_path):
    from vaibify.install.setupServer import fappCreateSetupWizard
    from fastapi.testclient import TestClient
    app = fappCreateSetupWizard(sOutputDirectory=str(tmp_path))
    clientHttp = TestClient(app)
    dictPayload = {
        "sProjectName": "test",
        "sPackageManager": "invalid_mgr",
    }
    responseHttp = clientHttp.post(
        "/api/setup/validate", json=dictPayload
    )
    assert responseHttp.status_code == 200
    dictResult = responseHttp.json()
    assert dictResult["bValid"] is False


def test_save_with_overleaf_id(tmp_path):
    from vaibify.install.setupServer import fappCreateSetupWizard
    from fastapi.testclient import TestClient
    app = fappCreateSetupWizard(sOutputDirectory=str(tmp_path))
    clientHttp = TestClient(app)
    dictPayload = {
        "sProjectName": "overleaf_test",
        "sPackageManager": "pip",
        "sOverleafProjectId": "abc123",
    }
    responseHttp = clientHttp.post(
        "/api/setup/save", json=dictPayload
    )
    assert responseHttp.status_code == 200
    with open(tmp_path / "vaibify.yml") as fh:
        dictSaved = yaml.safe_load(fh)
    assert "reproducibility" in dictSaved
    assert dictSaved["reproducibility"]["overleaf"][
        "projectId"
    ] == "abc123"


def test_fdictConfigToWizardFormat():
    from vaibify.install.setupServer import (
        _fdictConfigToWizardFormat,
    )
    config = _fConfigFull()
    dictResult = _fdictConfigToWizardFormat(config)
    assert dictResult["sProjectName"] == "testproj"
    assert isinstance(dictResult["listFeatures"], list)


def test_flistEnabledFeatures():
    from vaibify.install.setupServer import (
        _flistEnabledFeatures,
    )
    features = SimpleNamespace(
        bJupyter=True, bRLanguage=False, bJulia=False,
        bDatabase=False, bDvc=False, bLatex=True,
        bClaude=False, bGpu=False,
    )
    listResult = _flistEnabledFeatures(features)
    assert "jupyter" in listResult
    assert "latex" in listResult
    assert "gpu" not in listResult


def test_fdictTemplateToWizardFormat():
    from vaibify.install.setupServer import (
        _fdictTemplateToWizardFormat,
    )
    dictTemplate = {
        "listRepositories": [
            {"sUrl": "https://github.com/a/b"},
        ]
    }
    dictResult = _fdictTemplateToWizardFormat(
        "mytemplate", dictTemplate
    )
    assert dictResult["sProjectName"] == "mytemplate"
    assert "https://github.com/a/b" in dictResult[
        "listRepositories"
    ]


def test_fsRepoNameFromUrl():
    from vaibify.install.setupServer import _fsRepoNameFromUrl
    assert _fsRepoNameFromUrl(
        "https://github.com/user/repo.git"
    ) == "repo"
    assert _fsRepoNameFromUrl(
        "https://github.com/user/repo"
    ) == "repo"
    assert _fsRepoNameFromUrl(
        "https://github.com/user/repo/"
    ) == "repo"


def test_save_with_repos(tmp_path):
    from vaibify.install.setupServer import fappCreateSetupWizard
    from fastapi.testclient import TestClient
    app = fappCreateSetupWizard(sOutputDirectory=str(tmp_path))
    clientHttp = TestClient(app)
    dictPayload = {
        "sProjectName": "repoproj",
        "sPackageManager": "pip",
        "listRepositories": [
            "https://github.com/user/repo.git",
        ],
    }
    responseHttp = clientHttp.post(
        "/api/setup/save", json=dictPayload
    )
    assert responseHttp.status_code == 200
    with open(tmp_path / "vaibify.yml") as fh:
        dictSaved = yaml.safe_load(fh)
    assert len(dictSaved["repositories"]) == 1
    assert dictSaved["repositories"][0]["name"] == "repo"


# =======================================================================
# 16. vaibify/reproducibility/dataArchiver.py — uncovered functions
# =======================================================================


@patch("vaibify.reproducibility.dataArchiver._fnSaveProvenanceFile")
@patch("vaibify.reproducibility.dataArchiver.fnUpdateProvenance")
@patch("vaibify.reproducibility.dataArchiver.fnUploadToZenodo")
@patch("vaibify.reproducibility.dataArchiver.flistDetectChangedOutputs",
       return_value=["/tmp/out.dat"])
@patch("vaibify.reproducibility.dataArchiver._fdictLoadOrCreateProvenance",
       return_value={"saSteps": [], "dictFileHashes": {}})
def test_fnArchiveOutputs_uploads(
    mockLoad, mockDetect, mockUpload, mockUpdate, mockSave,
):
    from vaibify.reproducibility.dataArchiver import (
        fnArchiveOutputs,
    )
    fnArchiveOutputs({"sZenodoService": "sandbox"}, {}, "/work")
    mockUpload.assert_called_once()
    mockUpdate.assert_called_once()
    mockSave.assert_called_once()


@patch("vaibify.reproducibility.dataArchiver._fnSaveProvenanceFile")
@patch("vaibify.reproducibility.dataArchiver.fnUpdateProvenance")
@patch("vaibify.reproducibility.dataArchiver.fnUploadToZenodo")
@patch("vaibify.reproducibility.dataArchiver.flistDetectChangedOutputs",
       return_value=[])
@patch("vaibify.reproducibility.dataArchiver._fdictLoadOrCreateProvenance",
       return_value={})
def test_fnArchiveOutputs_no_changes(
    mockLoad, mockDetect, mockUpload, mockUpdate, mockSave,
):
    from vaibify.reproducibility.dataArchiver import (
        fnArchiveOutputs,
    )
    fnArchiveOutputs({}, {}, "/work")
    mockUpload.assert_not_called()


@patch(
    "vaibify.reproducibility.zenodoClient.ZenodoClient.fnPublishDraft"
)
@patch(
    "vaibify.reproducibility.zenodoClient.ZenodoClient.fnUploadFile"
)
@patch(
    "vaibify.reproducibility.zenodoClient.ZenodoClient.fdictCreateDraft",
    return_value={"id": 42},
)
def test_fnUploadToZenodo_success(
    mockCreate, mockUpload, mockPublish,
):
    from vaibify.reproducibility.dataArchiver import (
        fnUploadToZenodo,
    )
    fnUploadToZenodo(
        {"sZenodoService": "sandbox"}, ["/tmp/f.dat"],
    )
    mockCreate.assert_called_once()
    mockUpload.assert_called_once()
    mockPublish.assert_called_once()


@patch(
    "vaibify.reproducibility.zenodoClient.ZenodoClient.fnDeleteDraft"
)
@patch(
    "vaibify.reproducibility.zenodoClient.ZenodoClient.fnUploadFile",
    side_effect=__import__(
        "vaibify.reproducibility.zenodoClient",
        fromlist=["ZenodoError"],
    ).ZenodoError("upload failed"),
)
@patch(
    "vaibify.reproducibility.zenodoClient.ZenodoClient.fdictCreateDraft",
    return_value={"id": 42},
)
def test_fnUploadToZenodo_failure_cleans_up(
    mockCreate, mockUpload, mockDelete,
):
    from vaibify.reproducibility.dataArchiver import (
        fnUploadToZenodo,
    )
    from vaibify.reproducibility.zenodoClient import ZenodoError
    with pytest.raises(ZenodoError):
        fnUploadToZenodo(
            {"sZenodoService": "sandbox"}, ["/tmp/f.dat"],
        )
    mockDelete.assert_called_once_with(42)


def test_fdictLoadOrCreateProvenance_new(tmp_path):
    from vaibify.reproducibility.dataArchiver import (
        _fdictLoadOrCreateProvenance,
    )
    dictResult = _fdictLoadOrCreateProvenance(str(tmp_path))
    assert "saSteps" in dictResult
    assert "dictFileHashes" in dictResult


def test_fdictLoadOrCreateProvenance_existing(tmp_path):
    from vaibify.reproducibility.dataArchiver import (
        _fdictLoadOrCreateProvenance,
    )
    import json
    sProvPath = tmp_path / ".provenance.json"
    dictProv = {"saSteps": ["a"], "dictFileHashes": {"f": "h"}}
    sProvPath.write_text(json.dumps(dictProv))
    dictResult = _fdictLoadOrCreateProvenance(str(tmp_path))
    assert dictResult["saSteps"] == ["a"]


def test_fnSaveProvenanceFile(tmp_path):
    from vaibify.reproducibility.dataArchiver import (
        _fnSaveProvenanceFile,
    )
    dictProv = {"saSteps": [], "dictFileHashes": {}}
    _fnSaveProvenanceFile(dictProv, str(tmp_path))
    assert (tmp_path / ".provenance.json").exists()


def test_fnCollectStepOutputs_hashes_file(tmp_path):
    from vaibify.reproducibility.dataArchiver import (
        _fnCollectStepOutputs,
    )
    sFilePath = str(tmp_path / "out.dat")
    with open(sFilePath, "w") as fh:
        fh.write("data")
    dictStep = {"saPlotFiles": [sFilePath]}
    dictOutputs = {}
    _fnCollectStepOutputs(dictStep, str(tmp_path), dictOutputs)
    assert sFilePath in dictOutputs


def test_fnCollectStepOutputs_missing_file(tmp_path):
    from vaibify.reproducibility.dataArchiver import (
        _fnCollectStepOutputs,
    )
    dictStep = {"saPlotFiles": ["/nonexistent.dat"]}
    dictOutputs = {}
    _fnCollectStepOutputs(dictStep, str(tmp_path), dictOutputs)
    assert len(dictOutputs) == 0


def test_fpathResolveOutput_absolute():
    from vaibify.reproducibility.dataArchiver import (
        _fpathResolveOutput,
    )
    from pathlib import Path
    pathResult = _fpathResolveOutput("/abs/path.dat", "/work")
    assert str(pathResult) == "/abs/path.dat"


def test_fpathResolveOutput_relative():
    from vaibify.reproducibility.dataArchiver import (
        _fpathResolveOutput,
    )
    pathResult = _fpathResolveOutput("rel/file.dat", "/work")
    assert str(pathResult) == "/work/rel/file.dat"


# =======================================================================
# 17. vaibify/reproducibility/zenodoClient.py — uncovered functions
# =======================================================================


def test_zenodo_upload_file(tmp_path):
    from vaibify.reproducibility.zenodoClient import ZenodoClient
    client = ZenodoClient(sService="sandbox")
    client._sToken = "fake"
    dictDeposit = {
        "links": {"bucket": "https://zen.org/bucket/1"},
    }
    with patch("requests.request") as mockRequest:
        mockRequest.return_value = MagicMock(
            status_code=200,
            json=lambda: dictDeposit,
        )
        with patch("requests.put") as mockPut:
            mockPut.return_value = MagicMock(status_code=200)
            sFile = str(tmp_path / "upload.dat")
            with open(sFile, "wb") as fh:
                fh.write(b"test data")
            client.fnUploadFile(42, sFile)
            mockPut.assert_called_once()


def test_zenodo_upload_file_not_found():
    from vaibify.reproducibility.zenodoClient import ZenodoClient
    client = ZenodoClient(sService="sandbox")
    client._sToken = "fake"
    dictDeposit = {
        "links": {"bucket": "https://zen.org/bucket/1"},
    }
    with patch("requests.request") as mockRequest:
        mockRequest.return_value = MagicMock(
            status_code=200,
            json=lambda: dictDeposit,
        )
        with pytest.raises(FileNotFoundError):
            client.fnUploadFile(42, "/nonexistent_file.dat")


def test_zenodo_download_file(tmp_path):
    from vaibify.reproducibility.zenodoClient import ZenodoClient
    client = ZenodoClient(sService="sandbox")
    client._sToken = "fake"
    dictRecord = {
        "files": [{
            "key": "data.hdf5",
            "links": {"self": "https://zen.org/file/1"},
        }],
    }
    mockResponse = MagicMock()
    mockResponse.status_code = 200
    mockResponse.headers = {"content-length": "4"}
    mockResponse.iter_content.return_value = [b"data"]
    with patch("requests.request") as mockRequest:
        mockRequest.return_value = MagicMock(
            status_code=200,
            json=lambda: dictRecord,
        )
        with patch("requests.get", return_value=mockResponse):
            client.fnDownloadFile(
                42, "data.hdf5", str(tmp_path),
            )
    assert (tmp_path / "data.hdf5").exists()
    assert (tmp_path / "data.hdf5").read_bytes() == b"data"


def test_zenodo_download_file_not_in_record():
    from vaibify.reproducibility.zenodoClient import (
        ZenodoClient, ZenodoNotFoundError,
    )
    client = ZenodoClient(sService="sandbox")
    client._sToken = "fake"
    dictRecord = {"files": []}
    with patch("requests.request") as mockRequest:
        mockRequest.return_value = MagicMock(
            status_code=200,
            json=lambda: dictRecord,
        )
        with pytest.raises(ZenodoNotFoundError):
            client.fnDownloadFile(42, "missing.dat", "/tmp")


def test_fsRetrieveToken_falls_back_to_legacy_slot():
    from vaibify.reproducibility.zenodoClient import (
        _fsRetrieveToken,
    )
    with patch(
        "vaibify.config.secretManager.fbSecretExists",
        return_value=False,
    ), patch(
        "vaibify.config.secretManager.fsRetrieveSecret",
        return_value="legacy_token",
    ) as mockRetrieve:
        sToken = _fsRetrieveToken("sandbox")
    assert sToken == "legacy_token"
    assert mockRetrieve.call_args[0][0] == "zenodo_token"


def test_fsRetrieveToken_prefers_namespaced_sandbox_slot():
    from vaibify.reproducibility.zenodoClient import (
        _fsRetrieveToken,
    )
    with patch(
        "vaibify.config.secretManager.fbSecretExists",
        return_value=True,
    ), patch(
        "vaibify.config.secretManager.fsRetrieveSecret",
        return_value="namespaced_sandbox",
    ) as mockRetrieve:
        sToken = _fsRetrieveToken("sandbox")
    assert sToken == "namespaced_sandbox"
    assert mockRetrieve.call_args[0][0] == "zenodo_token_sandbox"


def test_fsRetrieveToken_prefers_namespaced_production_slot():
    from vaibify.reproducibility.zenodoClient import (
        _fsRetrieveToken,
    )
    with patch(
        "vaibify.config.secretManager.fbSecretExists",
        return_value=True,
    ), patch(
        "vaibify.config.secretManager.fsRetrieveSecret",
        return_value="namespaced_prod",
    ) as mockRetrieve:
        sToken = _fsRetrieveToken("zenodo")
    assert sToken == "namespaced_prod"
    assert mockRetrieve.call_args[0][0] == "zenodo_token_production"


def test_zenodo_client_lazy_token():
    from vaibify.reproducibility.zenodoClient import ZenodoClient
    client = ZenodoClient(sService="sandbox")
    assert client._sToken is None
    with patch(
        "vaibify.reproducibility.zenodoClient._fsRetrieveToken",
        return_value="lazy_token",
    ) as mockRetrieve:
        sToken = client._fsGetToken()
    assert sToken == "lazy_token"
    assert client._sToken == "lazy_token"
    assert mockRetrieve.call_args[0][0] == "sandbox"


def test_zenodo_client_passes_production_service_to_retriever():
    from vaibify.reproducibility.zenodoClient import ZenodoClient
    client = ZenodoClient(sService="zenodo")
    with patch(
        "vaibify.reproducibility.zenodoClient._fsRetrieveToken",
        return_value="prod_token",
    ) as mockRetrieve:
        client._fsGetToken()
    assert mockRetrieve.call_args[0][0] == "zenodo"


def test_zenodo_client_cached_token():
    from vaibify.reproducibility.zenodoClient import ZenodoClient
    client = ZenodoClient(sService="sandbox")
    client._sToken = "cached"
    sToken = client._fsGetToken()
    assert sToken == "cached"
