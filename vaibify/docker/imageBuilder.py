"""Build Docker images with deterministic overlay ordering."""

import subprocess
import sys
from pathlib import Path

from . import fnRunDockerCommand


def fbBuildxAvailable():
    """Return True if `docker buildx` is installed and functional."""
    resultProcess = subprocess.run(
        ["docker", "buildx", "version"],
        capture_output=True,
    )
    return resultProcess.returncode == 0


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
        fnApplyOverlay(sProjectName, sOverlayName, sDockerDir, sPreviousTag)
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


def fnApplyOverlay(sProjectName, sOverlayName, sDockerDir, sFromTag):
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
    """
    sDockerfile = _fsResolveOverlayDockerfile(sOverlayName, sDockerDir)
    sNewTag = f"{sProjectName}:{sOverlayName}"
    sFromImage = f"{sProjectName}:{sFromTag}"
    saCommand = _flistOverlayCommand(sDockerfile, sNewTag, sFromImage, sDockerDir)
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


_fnRunDockerBuild = fnRunDockerCommand
