"""Targeted tests for recently added and under-tested functionality.

Covers:
- pipelineRunner: log buffer cap, _fsExtractLogLine, ffBuildLoggingCallback
- dockerConnection: _fnEnsureDockerHost
- containerManager: detached mode excludes --rm
- pipelineServer: _fnValidateHostDestination, SessionTokenMiddleware
- registryRoutes: _fbIsVaibifyContainer, _fdictContainerToProject
- dependencyScanner: config-file scanner, edge cases
- imageBuilder: fbBuildxAvailable, _flistBuildPrefix edge cases
"""

import asyncio
import os

import pytest
from unittest.mock import MagicMock, patch


# -----------------------------------------------------------------------
# pipelineRunner: circular log buffer
# -----------------------------------------------------------------------


def test_fsExtractLogLine_output_event():
    from vaibify.gui.pipelineRunner import _fsExtractLogLine
    dictEvent = {"sType": "output", "sLine": "hello world"}
    assert _fsExtractLogLine(dictEvent) == "hello world"


def test_fsExtractLogLine_output_missing_line():
    from vaibify.gui.pipelineRunner import _fsExtractLogLine
    dictEvent = {"sType": "output"}
    assert _fsExtractLogLine(dictEvent) == ""


def test_fsExtractLogLine_command_failed():
    from vaibify.gui.pipelineRunner import _fsExtractLogLine
    dictEvent = {
        "sType": "commandFailed",
        "sCommand": "make",
        "iExitCode": 2,
    }
    sLine = _fsExtractLogLine(dictEvent)
    assert "FAILED" in sLine
    assert "make" in sLine
    assert "2" in sLine


def test_fsExtractLogLine_command_failed_missing_fields():
    from vaibify.gui.pipelineRunner import _fsExtractLogLine
    dictEvent = {"sType": "commandFailed"}
    sLine = _fsExtractLogLine(dictEvent)
    assert "FAILED" in sLine
    assert "?" in sLine


def test_fsExtractLogLine_unknown_type():
    from vaibify.gui.pipelineRunner import _fsExtractLogLine
    dictEvent = {"sType": "stepStarted", "sName": "A"}
    assert _fsExtractLogLine(dictEvent) is None


def test_fsExtractLogLine_no_type():
    from vaibify.gui.pipelineRunner import _fsExtractLogLine
    assert _fsExtractLogLine({}) is None


def test_ffBuildLoggingCallback_appends_lines():
    from vaibify.gui.pipelineRunner import ffBuildLoggingCallback
    listLogLines = []
    listCallbackCalls = []

    async def fnOriginal(dictEvent):
        listCallbackCalls.append(dictEvent)

    fnCallback = ffBuildLoggingCallback(fnOriginal, listLogLines)
    dictEvent = {"sType": "output", "sLine": "line one"}
    asyncio.run(fnCallback(dictEvent))
    assert listLogLines == ["line one"]
    assert len(listCallbackCalls) == 1


def test_ffBuildLoggingCallback_evicts_when_full():
    """Verify circular buffer evicts the oldest line at capacity."""
    from vaibify.gui.pipelineRunner import (
        ffBuildLoggingCallback, I_MAX_LOG_LINES,
    )
    listLogLines = [f"line_{i}" for i in range(I_MAX_LOG_LINES)]

    async def fnOriginal(dictEvent):
        pass

    fnCallback = ffBuildLoggingCallback(fnOriginal, listLogLines)
    dictEvent = {"sType": "output", "sLine": "overflow_line"}
    asyncio.run(fnCallback(dictEvent))
    assert len(listLogLines) == I_MAX_LOG_LINES
    assert listLogLines[0] == "line_1"
    assert listLogLines[-1] == "overflow_line"


def test_ffBuildLoggingCallback_skips_non_log_events():
    """Non-output, non-commandFailed events should not be logged."""
    from vaibify.gui.pipelineRunner import ffBuildLoggingCallback
    listLogLines = []

    async def fnOriginal(dictEvent):
        pass

    fnCallback = ffBuildLoggingCallback(fnOriginal, listLogLines)
    dictEvent = {"sType": "stepStarted", "sName": "Step A"}
    asyncio.run(fnCallback(dictEvent))
    assert listLogLines == []


def test_ffBuildLoggingCallback_multiple_evictions():
    """Evict repeatedly when buffer stays at capacity."""
    from vaibify.gui.pipelineRunner import (
        ffBuildLoggingCallback, I_MAX_LOG_LINES,
    )
    listLogLines = [f"line_{i}" for i in range(I_MAX_LOG_LINES)]

    async def fnOriginal(dictEvent):
        pass

    fnCallback = ffBuildLoggingCallback(fnOriginal, listLogLines)
    for i in range(3):
        dictEvent = {"sType": "output", "sLine": f"new_{i}"}
        asyncio.run(
            fnCallback(dictEvent))
    assert len(listLogLines) == I_MAX_LOG_LINES
    assert listLogLines[-1] == "new_2"
    assert listLogLines[-2] == "new_1"
    assert listLogLines[-3] == "new_0"
    assert listLogLines[0] == "line_3"


# -----------------------------------------------------------------------
# dockerConnection: _fnEnsureDockerHost
# -----------------------------------------------------------------------


def test_fnEnsureDockerHost_already_set(monkeypatch):
    """When DOCKER_HOST is already set, do nothing."""
    from vaibify.docker.dockerConnection import _fnEnsureDockerHost
    monkeypatch.setenv("DOCKER_HOST", "unix:///existing.sock")
    _fnEnsureDockerHost()
    assert os.environ["DOCKER_HOST"] == "unix:///existing.sock"


def test_fnEnsureDockerHost_sets_from_context(monkeypatch):
    """When DOCKER_HOST is unset, read from docker context inspect."""
    from vaibify.docker.dockerConnection import _fnEnsureDockerHost
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    mockResult = MagicMock()
    mockResult.stdout = "unix:///var/run/docker.sock\n"
    with patch("subprocess.run", return_value=mockResult):
        _fnEnsureDockerHost()
    assert os.environ["DOCKER_HOST"] == "unix:///var/run/docker.sock"


def test_fnEnsureDockerHost_empty_output(monkeypatch):
    """When docker context returns empty, DOCKER_HOST stays unset."""
    from vaibify.docker.dockerConnection import _fnEnsureDockerHost
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    mockResult = MagicMock()
    mockResult.stdout = "\n"
    with patch("subprocess.run", return_value=mockResult):
        _fnEnsureDockerHost()
    assert "DOCKER_HOST" not in os.environ


def test_fnEnsureDockerHost_docker_not_installed(monkeypatch):
    """When docker CLI is missing, gracefully do nothing."""
    from vaibify.docker.dockerConnection import _fnEnsureDockerHost
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    with patch("subprocess.run", side_effect=FileNotFoundError):
        _fnEnsureDockerHost()
    assert "DOCKER_HOST" not in os.environ


# -----------------------------------------------------------------------
# containerManager: detached mode excludes --rm
# -----------------------------------------------------------------------


@patch(
    "vaibify.docker.containerManager.flistConfigureX11Args",
    return_value=[],
)
def test_flistBuildRunArgs_detached_no_rm(mockX11):
    """Detached mode must not include --rm flag."""
    from types import SimpleNamespace
    from vaibify.docker.containerManager import flistBuildRunArgs
    features = SimpleNamespace(bGpu=False)
    config = SimpleNamespace(
        sProjectName="proj",
        sWorkspaceRoot="/workspace",
        listPorts=[],
        listBindMounts=[],
        listSecrets=[],
        features=features,
        bNetworkIsolation=False,
    )
    saArgs = flistBuildRunArgs(config, bDetached=True)
    assert "--rm" not in saArgs
    assert "-d" in saArgs
    assert "-it" not in saArgs


@patch(
    "vaibify.docker.containerManager.flistConfigureX11Args",
    return_value=[],
)
def test_flistBuildRunArgs_interactive_has_rm(mockX11):
    """Interactive mode must include --rm and -it."""
    from types import SimpleNamespace
    from vaibify.docker.containerManager import flistBuildRunArgs
    features = SimpleNamespace(bGpu=False)
    config = SimpleNamespace(
        sProjectName="proj",
        sWorkspaceRoot="/workspace",
        listPorts=[],
        listBindMounts=[],
        listSecrets=[],
        features=features,
        bNetworkIsolation=False,
    )
    saArgs = flistBuildRunArgs(config, bDetached=False)
    assert "--rm" in saArgs
    assert "-it" in saArgs
    assert "-d" not in saArgs


# -----------------------------------------------------------------------
# pipelineServer: _fnValidateHostDestination
# -----------------------------------------------------------------------


def test_fnValidateHostDestination_within_home(monkeypatch):
    """Paths under home should not raise."""
    from vaibify.gui.pipelineServer import _fnValidateHostDestination
    sHome = os.path.expanduser("~")
    sPath = os.path.join(sHome, "Documents", "output")
    _fnValidateHostDestination(sPath)


def test_fnValidateHostDestination_is_home(monkeypatch):
    """Home directory itself should not raise."""
    from vaibify.gui.pipelineServer import _fnValidateHostDestination
    sHome = os.path.expanduser("~")
    _fnValidateHostDestination(sHome)


def test_fnValidateHostDestination_outside_home():
    """Paths outside home should raise 403."""
    from fastapi import HTTPException
    from vaibify.gui.pipelineServer import _fnValidateHostDestination
    with pytest.raises(HTTPException) as excInfo:
        _fnValidateHostDestination("/etc/passwd")
    assert excInfo.value.status_code == 403


def test_fnValidateHostDestination_root_path():
    """Root path should raise 403."""
    from fastapi import HTTPException
    from vaibify.gui.pipelineServer import _fnValidateHostDestination
    with pytest.raises(HTTPException) as excInfo:
        _fnValidateHostDestination("/")
    assert excInfo.value.status_code == 403


# -----------------------------------------------------------------------
# pipelineServer: fnValidatePathWithinRoot
# -----------------------------------------------------------------------


def test_fnValidatePathWithinRoot_valid():
    from vaibify.gui.pipelineServer import fnValidatePathWithinRoot
    sResult = fnValidatePathWithinRoot(
        "/workspace/step1/data.csv", "/workspace")
    assert sResult == "/workspace/step1/data.csv"


def test_fnValidatePathWithinRoot_exact_root():
    from vaibify.gui.pipelineServer import fnValidatePathWithinRoot
    sResult = fnValidatePathWithinRoot("/workspace", "/workspace")
    assert sResult == "/workspace"


def test_fnValidatePathWithinRoot_traversal():
    from fastapi import HTTPException
    from vaibify.gui.pipelineServer import fnValidatePathWithinRoot
    with pytest.raises(HTTPException) as excInfo:
        fnValidatePathWithinRoot(
            "/workspace/../etc/passwd", "/workspace")
    assert excInfo.value.status_code == 403


def test_fnValidatePathWithinRoot_partial_prefix():
    """Paths like /workspace2 should be rejected (not a subdirectory)."""
    from fastapi import HTTPException
    from vaibify.gui.pipelineServer import fnValidatePathWithinRoot
    with pytest.raises(HTTPException) as excInfo:
        fnValidatePathWithinRoot("/workspace2/data", "/workspace")
    assert excInfo.value.status_code == 403


# -----------------------------------------------------------------------
# pipelineServer: SessionTokenMiddleware
# -----------------------------------------------------------------------


def _fAppWithMiddleware():
    """Build a minimal FastAPI app with SessionTokenMiddleware."""
    import secrets
    from fastapi import FastAPI
    from vaibify.gui.pipelineServer import (
        SessionTokenMiddleware, SecurityHeadersMiddleware,
    )
    app = FastAPI()
    sToken = "test-token-abc123"
    app.state.sSessionToken = sToken
    app.add_middleware(SessionTokenMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)

    @app.get("/api/session-token")
    async def fnGetToken():
        return {"sToken": sToken}

    @app.get("/api/data")
    async def fnGetData():
        return {"sValue": 42}

    @app.get("/not-api")
    async def fnPublic():
        return {"sPublic": True}

    return app, sToken


def test_middleware_rejects_missing_token():
    from starlette.testclient import TestClient
    app, sToken = _fAppWithMiddleware()
    client = TestClient(app)
    response = client.get("/api/data")
    assert response.status_code == 401


def test_middleware_accepts_valid_token():
    from starlette.testclient import TestClient
    app, sToken = _fAppWithMiddleware()
    client = TestClient(app)
    response = client.get(
        "/api/data",
        headers={"x-session-token": sToken},
    )
    assert response.status_code == 200
    assert response.json()["sValue"] == 42


def test_middleware_allows_session_token_endpoint():
    """The /api/session-token endpoint itself is exempted."""
    from starlette.testclient import TestClient
    app, sToken = _fAppWithMiddleware()
    client = TestClient(app)
    response = client.get("/api/session-token")
    assert response.status_code == 200


def test_middleware_allows_non_api_routes():
    from starlette.testclient import TestClient
    app, sToken = _fAppWithMiddleware()
    client = TestClient(app)
    response = client.get("/not-api")
    assert response.status_code == 200


def test_middleware_rejects_wrong_token():
    from starlette.testclient import TestClient
    app, sToken = _fAppWithMiddleware()
    client = TestClient(app)
    response = client.get(
        "/api/data",
        headers={"x-session-token": "wrong-token"},
    )
    assert response.status_code == 401


# -----------------------------------------------------------------------
# pipelineServer: _flistParseDirectoryOutput
# -----------------------------------------------------------------------


def test_flistParseDirectoryOutput_mixed():
    from vaibify.gui.pipelineServer import _flistParseDirectoryOutput
    sOutput = "d /workspace/subdir\nf /workspace/file.txt\n"
    listResult = _flistParseDirectoryOutput(sOutput)
    assert len(listResult) == 2
    assert listResult[0]["bIsDirectory"] is True
    assert listResult[0]["sName"] == "subdir"
    assert listResult[1]["bIsDirectory"] is False


def test_flistParseDirectoryOutput_empty():
    from vaibify.gui.pipelineServer import _flistParseDirectoryOutput
    assert _flistParseDirectoryOutput("") == []


def test_flistParseDirectoryOutput_short_lines_skipped():
    from vaibify.gui.pipelineServer import _flistParseDirectoryOutput
    sOutput = "x\n\nf /workspace/ok.txt\n"
    listResult = _flistParseDirectoryOutput(sOutput)
    assert len(listResult) == 1


# -----------------------------------------------------------------------
# registryRoutes: _fdictContainerToProject
# -----------------------------------------------------------------------


def test_fdictContainerToProject_structure():
    from vaibify.gui.registryRoutes import _fdictContainerToProject
    mockDocker = MagicMock()
    dictContainer = {
        "sContainerId": "abc123",
        "sShortId": "abc1",
        "sName": "my-project",
        "sImage": "my-project:latest",
    }
    dictResult = _fdictContainerToProject(mockDocker, dictContainer)
    assert dictResult["sName"] == "my-project"
    assert dictResult["sContainerId"] == "abc123"
    assert dictResult["bDiscovered"] is True
    assert dictResult["bRunning"] is True
    assert dictResult["sDirectory"] == ""


# -----------------------------------------------------------------------
# registryRoutes: _flistMergeProjectsAndContainers
# -----------------------------------------------------------------------


def test_flistMergeProjectsAndContainers_adds_new():
    from vaibify.gui.registryRoutes import (
        _flistMergeProjectsAndContainers,
    )
    listRegistered = [
        {"sContainerName": "existing", "sName": "existing"},
    ]
    listDiscovered = [
        {"sName": "new-proj", "sContainerId": "xyz789"},
    ]
    listMerged = _flistMergeProjectsAndContainers(
        listRegistered, listDiscovered)
    assert len(listMerged) == 2
    listNames = [d["sName"] for d in listMerged]
    assert "new-proj" in listNames


def test_flistMergeProjectsAndContainers_enriches_existing():
    from vaibify.gui.registryRoutes import (
        _flistMergeProjectsAndContainers,
    )
    listRegistered = [
        {"sContainerName": "proj", "sName": "proj"},
    ]
    listDiscovered = [
        {"sName": "proj", "sContainerId": "abc123"},
    ]
    listMerged = _flistMergeProjectsAndContainers(
        listRegistered, listDiscovered)
    assert len(listMerged) == 1
    assert listMerged[0]["sContainerId"] == "abc123"


# -----------------------------------------------------------------------
# dependencyScanner: _flistScanConfigFile
# -----------------------------------------------------------------------


def test_flistScanConfigFile_yaml_paths():
    from vaibify.gui.dependencyScanner import _flistScanConfigFile
    sSource = """
key: "data/input.csv"
other: "http://example.com"
nested: "results/output.dat"
"""
    listResults = _flistScanConfigFile(sSource)
    listFileNames = [r["sFileName"] for r in listResults]
    assert "data/input.csv" in listFileNames
    assert "results/output.dat" in listFileNames
    bHasUrl = any("http" in s for s in listFileNames)
    assert not bHasUrl


def test_flistScanConfigFile_json_paths():
    from vaibify.gui.dependencyScanner import _flistScanConfigFile
    sSource = '{"sInput": "analysis/data.h5", "sVersion": "1.0"}'
    listResults = _flistScanConfigFile(sSource)
    listFileNames = [r["sFileName"] for r in listResults]
    assert "analysis/data.h5" in listFileNames


def test_flistScanConfigFile_empty():
    from vaibify.gui.dependencyScanner import _flistScanConfigFile
    assert _flistScanConfigFile("") == []


# -----------------------------------------------------------------------
# dependencyScanner: fbLooksLikeDataFile edge cases
# -----------------------------------------------------------------------


def test_fbLooksLikeDataFile_empty():
    from vaibify.gui.dependencyScanner import fbLooksLikeDataFile
    assert fbLooksLikeDataFile("") is False
    assert fbLooksLikeDataFile("   ") is False
    assert fbLooksLikeDataFile(None) is False


def test_fbLooksLikeDataFile_url_rejected():
    from vaibify.gui.dependencyScanner import fbLooksLikeDataFile
    assert fbLooksLikeDataFile("https://example.com/data.csv") is False
    assert fbLooksLikeDataFile("ftp://server/file.dat") is False


def test_fbLooksLikeDataFile_directory_rejected():
    from vaibify.gui.dependencyScanner import fbLooksLikeDataFile
    assert fbLooksLikeDataFile("some/dir/") is False


def test_fbLooksLikeDataFile_too_many_spaces():
    from vaibify.gui.dependencyScanner import fbLooksLikeDataFile
    assert fbLooksLikeDataFile("this has too many spaces") is False


def test_fbLooksLikeDataFile_template_rejected():
    from vaibify.gui.dependencyScanner import fbLooksLikeDataFile
    assert fbLooksLikeDataFile("{sPlotDir}/fig.pdf") is False


def test_fbLooksLikeDataFile_path_with_slash():
    from vaibify.gui.dependencyScanner import fbLooksLikeDataFile
    assert fbLooksLikeDataFile("output/results.dat") is True


def test_fbLooksLikeDataFile_known_extension():
    from vaibify.gui.dependencyScanner import fbLooksLikeDataFile
    assert fbLooksLikeDataFile("data.csv") is True
    assert fbLooksLikeDataFile("model.hdf5") is True


# -----------------------------------------------------------------------
# dependencyScanner: _fbIsCommentLine
# -----------------------------------------------------------------------


def test_fbIsCommentLine_hash():
    from vaibify.gui.dependencyScanner import _fbIsCommentLine
    assert _fbIsCommentLine("# comment", "#") is True
    assert _fbIsCommentLine("code()", "#") is False


def test_fbIsCommentLine_slash():
    from vaibify.gui.dependencyScanner import _fbIsCommentLine
    assert _fbIsCommentLine("// comment", "//") is True


def test_fbIsCommentLine_none_prefix():
    from vaibify.gui.dependencyScanner import _fbIsCommentLine
    assert _fbIsCommentLine("anything", "") is False
    assert _fbIsCommentLine("anything", None) is False


# -----------------------------------------------------------------------
# dependencyScanner: fbLooksLikeFilePath edge cases
# -----------------------------------------------------------------------


def test_fbLooksLikeFilePath_template():
    from vaibify.gui.dependencyScanner import fbLooksLikeFilePath
    assert fbLooksLikeFilePath("{sPlotDir}/fig.pdf") is True


def test_fbLooksLikeFilePath_unknown_extension():
    """A file with unknown but short extension should match."""
    from vaibify.gui.dependencyScanner import fbLooksLikeFilePath
    assert fbLooksLikeFilePath("model.xyz") is True


def test_fbLooksLikeFilePath_no_extension_no_slash():
    from vaibify.gui.dependencyScanner import fbLooksLikeFilePath
    assert fbLooksLikeFilePath("justAWord") is False


# -----------------------------------------------------------------------
# imageBuilder: fbBuildxAvailable and _flistBuildPrefix
# -----------------------------------------------------------------------


def test_fbBuildxAvailable_success():
    from vaibify.docker.imageBuilder import fbBuildxAvailable
    mockResult = MagicMock()
    mockResult.returncode = 0
    with patch("subprocess.run", return_value=mockResult):
        assert fbBuildxAvailable() is True


def test_fbBuildxAvailable_failure():
    from vaibify.docker.imageBuilder import fbBuildxAvailable
    mockResult = MagicMock()
    mockResult.returncode = 1
    with patch("subprocess.run", return_value=mockResult):
        assert fbBuildxAvailable() is False


def test_flistBuildPrefix_with_buildx():
    from vaibify.docker.imageBuilder import _flistBuildPrefix
    with patch(
        "vaibify.docker.imageBuilder.fbBuildxAvailable",
        return_value=True,
    ):
        assert _flistBuildPrefix() == [
            "docker", "buildx", "build",
        ]


def test_flistBuildPrefix_without_buildx():
    from vaibify.docker.imageBuilder import _flistBuildPrefix
    with patch(
        "vaibify.docker.imageBuilder.fbBuildxAvailable",
        return_value=False,
    ):
        assert _flistBuildPrefix() == ["docker", "build"]


# -----------------------------------------------------------------------
# pipelineServer: fsResolveFigurePath
# -----------------------------------------------------------------------


def test_fsResolveFigurePath_absolute():
    from vaibify.gui.pipelineServer import fsResolveFigurePath
    sResult = fsResolveFigurePath(
        "/workspace/.vaibify/workflows", "/workspace/data.csv")
    assert sResult == "/workspace/data.csv"


def test_fsResolveFigurePath_relative():
    from vaibify.gui.pipelineServer import fsResolveFigurePath
    sResult = fsResolveFigurePath(
        "/workspace/.vaibify/workflows", "Plot/fig.pdf")
    assert sResult == "/workspace/.vaibify/workflows/Plot/fig.pdf"


# -----------------------------------------------------------------------
# pipelineServer: fbaFetchFigureWithFallback
# -----------------------------------------------------------------------


def test_fbaFetchFigureWithFallback_primary():
    from vaibify.gui.pipelineServer import fbaFetchFigureWithFallback
    mockDocker = MagicMock()
    mockDocker.fbaFetchFile.return_value = b"image data"
    baResult = fbaFetchFigureWithFallback(
        mockDocker, "cid", "/workspace/fig.png",
        "/workspace/.vaibify", "", "fig.png",
    )
    assert baResult == b"image data"


# -----------------------------------------------------------------------
# pipelineServer: SecurityHeadersMiddleware
# -----------------------------------------------------------------------


def test_security_headers_present():
    """All responses should have security headers."""
    from starlette.testclient import TestClient
    app, sToken = _fAppWithMiddleware()
    client = TestClient(app)
    response = client.get("/not-api")
    assert "x-content-type-options" in response.headers
    assert response.headers["x-content-type-options"] == "nosniff"


# -----------------------------------------------------------------------
# pipelineServer: fdictBuildContext
# -----------------------------------------------------------------------


def test_fdictBuildContext_with_none_docker():
    from vaibify.gui.pipelineServer import fdictBuildContext
    dictCtx = fdictBuildContext(None)
    assert dictCtx["docker"] is None
    assert callable(dictCtx["require"])


# -----------------------------------------------------------------------
# dependencyScanner: _fbLooksLikeRequiredFile
# -----------------------------------------------------------------------


def test_fbLooksLikeRequiredFile_relative():
    from vaibify.gui.dependencyScanner import _fbLooksLikeRequiredFile
    assert _fbLooksLikeRequiredFile("./helper") is True
    assert _fbLooksLikeRequiredFile("../utils") is True


def test_fbLooksLikeRequiredFile_module():
    from vaibify.gui.dependencyScanner import _fbLooksLikeRequiredFile
    assert _fbLooksLikeRequiredFile("express") is False
    assert _fbLooksLikeRequiredFile("lodash") is False


def test_fbLooksLikeRequiredFile_with_extension():
    from vaibify.gui.dependencyScanner import _fbLooksLikeRequiredFile
    assert _fbLooksLikeRequiredFile("data.json") is True
