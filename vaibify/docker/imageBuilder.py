"""Build Docker images with deterministic overlay ordering."""

import re
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path

from . import fnRunDockerCommand


_I_BUILD_STDERR_TAIL_LINES = 50


def fbBuildxAvailable():
    """Return True if `docker buildx` is installed and functional."""
    try:
        processResult = subprocess.run(
            ["docker", "buildx", "version"],
            capture_output=True,
        )
        return processResult.returncode == 0
    except FileNotFoundError:
        return False


def _flistBuildPrefix():
    """Return the docker build command prefix, preferring buildx.

    When buildx is absent the fallback is the legacy builder, and
    Docker itself prints a deprecation warning about it -- with no
    indication of whose decision it was or what to do. A researcher
    reasonably reads that as vaibify shipping something deprecated
    (live report, 2026-08-21), so the fallback announces itself and
    names the fix.
    """
    if fbBuildxAvailable():
        return ["docker", "buildx", "build"]
    fnEmitBuildLine(
        "[vaib] docker buildx is not installed on this machine, so "
        "this build uses Docker's legacy builder -- that is where the "
        "DEPRECATED warning below comes from. Vaibify prefers buildx "
        "whenever it is present. To install it: brew install "
        "docker-buildx (then link it into ~/.docker/cli-plugins/), or "
        "install Docker Desktop, which bundles it.\n"
    )
    return ["docker", "build"]


_LIST_OVERLAY_ORDER = [
    "gpu",
    "jupyter",
    "rlang",
    "julia",
    "database",
    "dvc",
    "nestedSampling",
    "node",
    "uv",
    "claude",
    "codex",
    "gemini",
    "antigravity",
    "opencode",
    "cline",
    "openhands",
    "pi",
]

_DICT_OVERLAY_DOCKERFILE_MAP = {
    "jupyter": "Dockerfile.jupyter",
    "rlang": "Dockerfile.rlang",
    "julia": "Dockerfile.julia",
    "database": "overlays/database.dockerfile",
    "dvc": "overlays/dvc.dockerfile",
    "nestedSampling": "overlays/nestedSampling.dockerfile",
    "gpu": "overlays/gpu.dockerfile",
    "claude": "Dockerfile.claude",
    "codex": "Dockerfile.codex",
    "gemini": "Dockerfile.gemini",
    "antigravity": "Dockerfile.antigravity",
    "node": "Dockerfile.node",
    "uv": "Dockerfile.uv",
    "opencode": "Dockerfile.opencode",
    "cline": "Dockerfile.cline",
    "openhands": "Dockerfile.openhands",
    "pi": "Dockerfile.pi",
}

# Every overlay is one of three kinds, and a project containerized from
# the author's PINNED image treats them differently: AGENT overlays may
# be stacked onto an obtained base as additions, BASE overlays change the
# environment the author pinned and are fixed by it, and PREREQUISITE
# overlays ride in only because an agent needs them. The classification
# is total over `_LIST_OVERLAY_ORDER`, and a test fails when an overlay
# is added without being classified.
T_AGENT_OVERLAY_NAMES = (
    "claude", "codex", "gemini", "antigravity", "opencode", "cline",
    "openhands", "pi",
)
T_BASE_OVERLAY_NAMES = (
    "gpu", "jupyter", "rlang", "julia", "database", "dvc", "nestedSampling",
)
T_PREREQUISITE_OVERLAY_NAMES = ("node", "uv")

_GPU_BASE_IMAGE = "nvidia/cuda:12.2.0-devel-ubuntu22.04"

_DICT_FEATURE_TO_OVERLAY = {
    "bJupyter": "jupyter",
    "bRLanguage": "rlang",
    "bJulia": "julia",
    "bDatabase": "database",
    "bDvc": "dvc",
    "bNestedSampling": "nestedSampling",
    "bGpu": "gpu",
    "bClaude": "claude",
    "bCodex": "codex",
    "bGemini": "gemini",
    "bAntigravity": "antigravity",
    "bOpenCode": "opencode",
    "bCline": "cline",
    "bOpenHands": "openhands",
    "bPi": "pi",
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
    listOverlays = flistDetermineOverlays(config)
    sRecipeFingerprint = _fsComputeChainFingerprint(
        sDockerDir, listOverlays,
    )
    fnBuildBase(
        config, sDockerDir, bNoCache,
        sRecipeFingerprint=sRecipeFingerprint, listOverlays=listOverlays,
    )
    sPreviousTag = "base"
    for sOverlayName in listOverlays:
        sNewTag = sOverlayName
        fnApplyOverlay(
            sProjectName, sOverlayName, sDockerDir,
            sPreviousTag, bNoCache,
            sRecipeFingerprint=sRecipeFingerprint, listOverlays=listOverlays,
        )
        sPreviousTag = sNewTag
    _fnTagFinalImage(sProjectName, sPreviousTag)
    _fnPruneDanglingImages()


def fsComputeShippedRecipeFingerprint(sDockerDir, listOverlays):
    """Fingerprint THIS vaibify's shipped texts for a candidate overlay list.

    The recomputation proof for an image built before the overlays
    label existed: equal to the image's recipe label, the candidate
    list is proven (the name separators make the digest injective over
    the pair list, so a header line edited to claim a different set
    fails here even with its fingerprint intact); unequal is UNPROVEN,
    never a refusal by itself -- an author who built with a vaibify
    whose overlay texts differed is the ordinary case.
    """
    return _fsComputeChainFingerprint(sDockerDir, listOverlays)


def flistCanonicalOverlayOrder():
    """Return the canonical overlay order, for readers of the overlays label."""
    return list(_LIST_OVERLAY_ORDER)


def fsFeatureFieldForOverlay(sOverlayName):
    """Return the config feature field that enables an overlay, or ``""``."""
    for sFeatureField, sMapped in _DICT_FEATURE_TO_OVERLAY.items():
        if sMapped == sOverlayName:
            return sFeatureField
    return ""


def flistOverlaysForFeatures(dictFeatureFlags):
    """Return the overlay chain a feature dict enables, in canonical order.

    The same resolution ``flistDetermineOverlays`` performs on a
    config, over a plain ``{sFeatureField: bool}`` dict, so a merged
    feature set (an author's plus a wizard's additions) can be resolved
    in memory without composing a config file first.
    """
    class _FeaturesView:
        pass

    featuresView = _FeaturesView()
    for sField in _DICT_FEATURE_TO_OVERLAY:
        setattr(featuresView, sField, bool(dictFeatureFlags.get(sField)))

    class _ConfigView:
        features = featuresView

    return flistDetermineOverlays(_ConfigView())


def fsStackOverlaysOnObtainedBase(
    sProjectName, sBaseImageId, listOverlayChain, sDockerDir, sPlatform,
    listLabelOverlays, bNoCache=False,
):
    """Build ``listOverlayChain`` on top of an OBTAINED base image.

    The first stage's base is the obtained image ID (never a tag the
    daemon could have moved), every stage requests the required
    platform -- under emulation the installers run under qemu, slowly
    -- and every stage carries ``vaibify.pinnedBaseImageId`` plus the
    overlays label naming the proven set AND the chain, so the result
    reads as DERIVED from the base and never as the base. Returns the
    tag of the last stage, or the base ID itself when the chain is
    empty; the caller resolves and tags the final image by ID.
    """
    from vaibify.config.imageOrigins import S_PINNED_BASE_LABEL
    from vaibify.reproducibility.dockerfileComposer import (
        S_OVERLAYS_IMAGE_LABEL,
        fsRenderOverlaysLabelValue,
    )
    if not listOverlayChain:
        return sBaseImageId
    sPreviousReference = sBaseImageId
    for sOverlayName in listOverlayChain:
        sNewTag = f"{sProjectName}:{sOverlayName}"
        saCommand = _flistOverlayCommand(
            _fsResolveOverlayDockerfile(sOverlayName, sDockerDir),
            sNewTag, sPreviousReference, sDockerDir,
        )
        if bNoCache:
            saCommand.append("--no-cache")
        if sPlatform:
            saCommand[-1:-1] = ["--platform", sPlatform]
        saCommand[-1:-1] = [
            "--label", f"{S_PINNED_BASE_LABEL}={sBaseImageId}",
            "--label", (
                f"{S_OVERLAYS_IMAGE_LABEL}="
                + fsRenderOverlaysLabelValue(listLabelOverlays)
            ),
        ]
        _fnRunDockerBuild(saCommand)
        sPreviousReference = sNewTag
    return sPreviousReference


def _fsComputeChainFingerprint(sDockerDir, listOverlays):
    """Fingerprint the exact Dockerfile texts this build will use.

    Stamped onto every image in the chain as
    ``dockerfileComposer.S_RECIPE_IMAGE_LABEL`` so the exported repo
    Dockerfile — which carries the fingerprint of the texts IT was
    composed from — can later be proven to describe this image rather
    than merely resemble one. An unreadable file yields "" and no
    label: an image with no fingerprint reads as "nothing determined"
    downstream, never as a match.
    """
    from vaibify.reproducibility.dockerfileComposer import (
        fsComputeRecipeFingerprint,
    )
    try:
        sBaseText = (Path(sDockerDir) / "Dockerfile").read_text()
        listTOverlays = [
            (sOverlayName,
             Path(_fsResolveOverlayDockerfile(
                 sOverlayName, sDockerDir)).read_text())
            for sOverlayName in listOverlays
        ]
    except OSError:
        return ""
    return fsComputeRecipeFingerprint(sBaseText, listTOverlays)


def _flistRecipeLabelArguments(sRecipeFingerprint, listOverlays=None):
    """Return the ``--label`` argv pairs the build chain is stamped with.

    The recipe fingerprint rides only when one was computed; the
    overlays label rides whenever a list is given, an empty chain
    included -- an image that says "no overlays" is a different image
    from one that says nothing, and a project containerized from the
    author's pinned image relies on that difference.
    """
    from vaibify.reproducibility.dockerfileComposer import (
        S_OVERLAYS_IMAGE_LABEL,
        S_RECIPE_IMAGE_LABEL,
        fsRenderOverlaysLabelValue,
    )
    listArguments = []
    if sRecipeFingerprint:
        listArguments += [
            "--label", f"{S_RECIPE_IMAGE_LABEL}={sRecipeFingerprint}",
        ]
    if listOverlays is not None:
        listArguments += [
            "--label", (
                f"{S_OVERLAYS_IMAGE_LABEL}="
                + fsRenderOverlaysLabelValue(listOverlays)
            ),
        ]
    return listArguments


def _fnPruneDanglingImages():
    """Remove dangling image layers orphaned by the rebuild.

    Each ``docker build -t name:tag`` that replaces an existing tag
    orphans the previous image's tagless layers; they accumulate and
    eat disk in the Docker VM. ``image prune -f`` (no ``-a``) removes
    only those dangling layers — tagged images, volumes, and running
    containers are not touched. Best-effort: a failure here must not
    fail the build, since the build itself already succeeded.
    """
    try:
        subprocess.run(
            ["docker", "image", "prune", "-f"],
            capture_output=True, text=True, timeout=30,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass


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
    if any(getattr(config.features, sField, False) for sField in (
            "bGemini", "bCline", "bPi")):
        listEnabled.append("node")
    if getattr(config.features, "bOpenHands", False):
        listEnabled.append("uv")
    return _flistSortByCanonicalOrder(listEnabled)


def _flistSortByCanonicalOrder(listOverlayNames):
    """Filter and sort overlay names according to _LIST_OVERLAY_ORDER."""
    return [s for s in _LIST_OVERLAY_ORDER if s in listOverlayNames]


def fnBuildBase(
    config, sDockerDir, bNoCache, sRecipeFingerprint="", listOverlays=None,
):
    """Build the base Dockerfile with build args from config.

    Parameters
    ----------
    config : ProjectConfig
        Validated project configuration.
    sDockerDir : str
        Path to the directory containing Dockerfiles.
    bNoCache : bool
        If True, pass --no-cache to docker build.
    sRecipeFingerprint : str
        Chain fingerprint to stamp as an image label ("" stamps none).
    """
    sBaseImage = _fsResolveBaseImage(config)
    saCommand = _flistBuildBaseCommand(config, sDockerDir, sBaseImage, bNoCache)
    # Spliced BEFORE the trailing context path: docker build takes the
    # path as its positional argument and it must stay last.
    saCommand[-1:-1] = _flistRecipeLabelArguments(
        sRecipeFingerprint, listOverlays,
    )
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
    bNoCache=False, sRecipeFingerprint="", listOverlays=None,
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
    saCommand += _flistRecipeLabelArguments(sRecipeFingerprint, listOverlays)
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
    processResult = subprocess.run(
        ["docker", "image", "inspect", sImageName],
        capture_output=True,
    )
    return processResult.returncode == 0


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


_threadLocalBuildSink = threading.local()


def fnSetThreadBuildLineSink(fnLineSink):
    """Route this thread's docker build output lines to ``fnLineSink``.

    The dashboard runs each build in its own worker thread and needs
    the docker output the CLI already streams to stderr, so the
    researcher can watch an hour-long build instead of a blank
    spinner. Thread-local routing reaches the single tee point
    (:func:`_fsStreamAndCaptureStderr`) without threading a callback
    through every build-command signature; the CLI never sets a sink
    and is unchanged. Each line is credential-redacted before it
    reaches the sink. Pass ``None`` to detach — always do so in a
    ``finally``, because worker threads are pooled and reused, and a
    stale sink would route a later build's output into a finished
    build's record.
    """
    _threadLocalBuildSink.fnLineSink = fnLineSink


def _fnOfferLineToSink(fnLineSink, sLine):
    """Hand one redacted line to the sink; sink failures never fail the build."""
    try:
        fnLineSink(fsRedactBuildOutputCredentials(sLine))
    except Exception as error:
        sys.stderr.write(
            f"[vaib] build progress sink error (build continues): "
            f"{error}\n"
        )


def fnEmitBuildLine(sLine):
    """Write one vaibify-authored line to wherever build output goes.

    Docker's own output reaches the researcher through two places at
    once -- stderr for the CLI, the thread-local sink for the
    dashboard's live pane -- and a line vaibify writes ABOUT the build
    is useless in only one of them. Reuses the same sink discipline, so
    a failing sink never fails a build.
    """
    sys.stderr.write(sLine)
    fnLineSink = getattr(_threadLocalBuildSink, "fnLineSink", None)
    if fnLineSink is not None:
        _fnOfferLineToSink(fnLineSink, sLine)


def _fsStreamAndCaptureStderr(procBuild):
    """Tee subprocess stderr to sys.stderr; return the captured tail."""
    dequeTail = deque(maxlen=_I_BUILD_STDERR_TAIL_LINES)
    if procBuild.stderr is None:
        return ""
    fnLineSink = getattr(_threadLocalBuildSink, "fnLineSink", None)
    for sLine in procBuild.stderr:
        sys.stderr.write(sLine)
        dequeTail.append(sLine)
        if fnLineSink is not None:
            _fnOfferLineToSink(fnLineSink, sLine)
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
