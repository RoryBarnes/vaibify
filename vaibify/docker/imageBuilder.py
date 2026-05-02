"""Build Docker images with deterministic overlay ordering."""

import re
import subprocess
import sys
import time
from collections import deque
from pathlib import Path

from . import fnRunDockerCommand


_I_BUILD_STDERR_TAIL_LINES = 50


def fbBuildxAvailable():
    """Return True if `docker buildx` is installed and functional."""
    try:
        resultProcess = subprocess.run(
            ["docker", "buildx", "version"],
            capture_output=True,
        )
        return resultProcess.returncode == 0
    except FileNotFoundError:
        return False


def _flistBuildPrefix():
    """Return the docker build command prefix, preferring buildx."""
    if fbBuildxAvailable():
        return ["docker", "buildx", "build"]
    return ["docker", "build"]


_LIST_OVERLAY_ORDER = [
    "gpu",
    "jupyter",
    "rlang",
    "julia",
    "database",
    "dvc",
    "claude",
]

_DICT_OVERLAY_DOCKERFILE_MAP = {
    "jupyter": "Dockerfile.jupyter",
    "rlang": "Dockerfile.rlang",
    "julia": "Dockerfile.julia",
    "database": "overlays/database.dockerfile",
    "dvc": "overlays/dvc.dockerfile",
    "gpu": "overlays/gpu.dockerfile",
    "claude": "Dockerfile.claude",
}

_GPU_BASE_IMAGE = "nvidia/cuda:12.2.0-devel-ubuntu22.04"

_DICT_FEATURE_TO_OVERLAY = {
    "bJupyter": "jupyter",
    "bRLanguage": "rlang",
    "bJulia": "julia",
    "bDatabase": "database",
    "bDvc": "dvc",
    "bGpu": "gpu",
    "bClaude": "claude",
}


def fnBuildImage(config, sDockerDir, bNoCache=False):
    """Build base image then apply overlay Dockerfiles in fixed order.

    Parameters
    ----------
    config : ProjectConfig
        Validated project configuration.
    sDockerDir : str
        Path to the directory containing Dockerfiles.
    bNoCache : bool
        If True, pass --no-cache to docker build.
    """
    sProjectName = config.sProjectName
    fnBuildBase(config, sDockerDir, bNoCache)
    listOverlays = flistDetermineOverlays(config)
    sPreviousTag = "base"
    for sOverlayName in listOverlays:
        sNewTag = sOverlayName
        fnApplyOverlay(
            sProjectName, sOverlayName, sDockerDir,
            sPreviousTag, bNoCache)
        sPreviousTag = sNewTag
    _fnTagFinalImage(sProjectName, sPreviousTag)


def flistDetermineOverlays(config):
    """Return ordered list of overlay names enabled in config.features.

    Parameters
    ----------
    config : ProjectConfig
        Validated project configuration.

    Returns
    -------
    list of str
        Overlay names in deterministic application order.
    """
    listEnabled = []
    for sFeatureField, sOverlayName in _DICT_FEATURE_TO_OVERLAY.items():
        if getattr(config.features, sFeatureField, False):
            listEnabled.append(sOverlayName)
    return _flistSortByCanonicalOrder(listEnabled)


def _flistSortByCanonicalOrder(listOverlayNames):
    """Filter and sort overlay names according to _LIST_OVERLAY_ORDER."""
    return [s for s in _LIST_OVERLAY_ORDER if s in listOverlayNames]


def fnBuildBase(config, sDockerDir, bNoCache):
    """Build the base Dockerfile with build args from config.

    Parameters
    ----------
    config : ProjectConfig
        Validated project configuration.
    sDockerDir : str
        Path to the directory containing Dockerfiles.
    bNoCache : bool
        If True, pass --no-cache to docker build.
    """
    sBaseImage = _fsResolveBaseImage(config)
    saCommand = _flistBuildBaseCommand(config, sDockerDir, sBaseImage, bNoCache)
    _fnRunDockerBuild(saCommand)


def _fsResolveBaseImage(config):
    """Return the base image, substituting NVIDIA image for GPU builds."""
    if config.features.bGpu:
        return _GPU_BASE_IMAGE
    return config.sBaseImage


def _flistBuildBaseCommand(config, sDockerDir, sBaseImage, bNoCache):
    """Assemble the docker build command list for the base image."""
    sProjectName = config.sProjectName
    sDockerfile = str(Path(sDockerDir) / "Dockerfile")
    sTag = f"{sProjectName}:base"
    saCommand = _flistBuildPrefix() + ["-f", sDockerfile, "-t", sTag]
    if bNoCache:
        saCommand.append("--no-cache")
    saCommand += _flistBuildArgPairs(config, sBaseImage)
    saCommand.append(sDockerDir)
    return saCommand


def _flistBuildArgPairs(config, sBaseImage):
    """Return list of --build-arg KEY=VALUE pairs."""
    dictArgs = {
        "BASE_IMAGE": sBaseImage,
        "PYTHON_VERSION": config.sPythonVersion,
        "CONTAINER_USER": config.sContainerUser,
        "WORKSPACE_ROOT": config.sWorkspaceRoot,
        "INSTALL_LATEX": str(config.features.bLatex).lower(),
        "INSTALL_X11": "true",
        "PACKAGE_MANAGER": config.sPackageManager,
        "VC_PROJECT_NAME": config.sProjectName,
    }
    saResult = []
    for sKey, sValue in dictArgs.items():
        saResult.extend(["--build-arg", f"{sKey}={sValue}"])
    return saResult


def fnApplyOverlay(
    sProjectName, sOverlayName, sDockerDir, sFromTag,
    bNoCache=False,
):
    """Build a single overlay Dockerfile on top of the previous tag.

    Parameters
    ----------
    sProjectName : str
        Project name used for image tagging.
    sOverlayName : str
        Name of the overlay (must be a key in _DICT_OVERLAY_DOCKERFILE_MAP).
    sDockerDir : str
        Path to the directory containing Dockerfiles.
    sFromTag : str
        Tag of the image to build on top of.
    bNoCache : bool
        If True, pass --no-cache to docker build.
    """
    sDockerfile = _fsResolveOverlayDockerfile(sOverlayName, sDockerDir)
    sNewTag = f"{sProjectName}:{sOverlayName}"
    sFromImage = f"{sProjectName}:{sFromTag}"
    saCommand = _flistOverlayCommand(
        sDockerfile, sNewTag, sFromImage, sDockerDir)
    if bNoCache:
        saCommand.append("--no-cache")
    _fnRunDockerBuild(saCommand)


def _fsResolveOverlayDockerfile(sOverlayName, sDockerDir):
    """Return full path to the overlay Dockerfile."""
    sRelativePath = _DICT_OVERLAY_DOCKERFILE_MAP.get(sOverlayName)
    if sRelativePath is None:
        raise ValueError(f"Unknown overlay name: '{sOverlayName}'")
    return str(Path(sDockerDir) / sRelativePath)


def _flistOverlayCommand(sDockerfile, sNewTag, sFromImage, sDockerDir):
    """Assemble docker build command for an overlay."""
    return _flistBuildPrefix() + [
        "-f", sDockerfile,
        "-t", sNewTag,
        "--build-arg", f"BASE_IMAGE={sFromImage}",
        sDockerDir,
    ]


def _fnTagFinalImage(sProjectName, sLastOverlayTag):
    """Tag the last built image as project:latest."""
    sSourceTag = f"{sProjectName}:{sLastOverlayTag}"
    sLatestTag = f"{sProjectName}:latest"
    saCommand = ["docker", "tag", sSourceTag, sLatestTag]
    _fnRunDockerBuild(saCommand)


def fbImageExists(sImageName):
    """Check whether a Docker image exists locally.

    Parameters
    ----------
    sImageName : str
        Full image name with tag, e.g. 'myproject:latest'.

    Returns
    -------
    bool
        True if the image exists locally.
    """
    resultProcess = subprocess.run(
        ["docker", "image", "inspect", sImageName],
        capture_output=True,
    )
    return resultProcess.returncode == 0


def _fnRunDockerBuildCapturing(saCommand):
    """Run docker build, streaming stderr to the user and capturing the tail.

    On non-zero exit, raises RuntimeError with ``sStderrTail`` set to
    the last ``_I_BUILD_STDERR_TAIL_LINES`` lines of stderr so callers
    can classify build failures.
    """
    procBuild = subprocess.Popen(
        saCommand,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    sStderrTail = _fsStreamAndCaptureStderr(procBuild)
    iReturnCode = procBuild.wait()
    if iReturnCode != 0:
        raise _ferrorBuildFailed(saCommand, iReturnCode, sStderrTail)


_RE_VIRTIOFS_LAG = re.compile(
    r"failed to compute cache key.*lstat.*no such file or directory",
    re.IGNORECASE | re.DOTALL,
)


def _fbStderrLooksLikeVirtiofsLag(sStderrTail):
    """Return True if the stderr tail matches the virtiofs sync-lag pattern."""
    return bool(_RE_VIRTIOFS_LAG.search(sStderrTail or ""))


def _fnRunDockerBuildWithVirtiofsRetry(saCommand):
    """Run docker build, retrying once on Colima virtiofs sync-lag errors."""
    try:
        _fnRunDockerBuildCapturing(saCommand)
        return
    except RuntimeError as errorBuild:
        if not _fbShouldRetryVirtiofs(errorBuild):
            raise
        _fnAnnounceVirtiofsRetry()
    time.sleep(2)
    _fnRunDockerBuildCapturing(saCommand)


def _fbShouldRetryVirtiofs(errorBuild):
    """True iff the error is a Colima virtiofs sync-lag failure."""
    from .dockerContext import fbColimaActive
    sStderrTail = getattr(errorBuild, "sStderrTail", "")
    if not _fbStderrLooksLikeVirtiofsLag(sStderrTail):
        return False
    return fbColimaActive()


def _fnAnnounceVirtiofsRetry():
    """Print the user-visible notice before retrying."""
    sys.stderr.write(
        "[vaib] Detected possible virtiofs sync lag; "
        "retrying once after 2s...\n"
    )


def _fsStreamAndCaptureStderr(procBuild):
    """Tee subprocess stderr to sys.stderr; return the captured tail."""
    dequeTail = deque(maxlen=_I_BUILD_STDERR_TAIL_LINES)
    if procBuild.stderr is None:
        return ""
    for sLine in procBuild.stderr:
        sys.stderr.write(sLine)
        dequeTail.append(sLine)
    return "".join(dequeTail)


_RE_HTTP_CREDENTIALS = re.compile(r"(https?://)[^@/\s]+@")


def fsRedactBuildOutputCredentials(sText):
    """Strip credentials embedded in HTTP(S) URLs from build output.

    Defends the captured stderr tail (and anything later derived
    from it — error logs, classification hints, GUI surfaces)
    against echoing tokens that may have been printed by a build
    step. Pattern: ``https://user:token@host`` -> ``https://REDACTED@host``.
    """
    return _RE_HTTP_CREDENTIALS.sub(r"\1REDACTED@", sText)


def _ferrorBuildFailed(saCommand, iReturnCode, sStderrTail):
    """Construct a RuntimeError carrying the captured stderr tail."""
    sCommandStr = " ".join(saCommand)
    errorBuild = RuntimeError(
        f"Docker command failed (exit {iReturnCode}): {sCommandStr}"
    )
    errorBuild.sStderrTail = fsRedactBuildOutputCredentials(sStderrTail)
    return errorBuild


_fnRunDockerBuild = _fnRunDockerBuildWithVirtiofsRetry
