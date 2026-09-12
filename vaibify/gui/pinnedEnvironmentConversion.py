"""Converting a clone so that it runs the author's PINNED image.

The convert route rewrites a host clone's ``vaibify.yml`` from the
wizard's request. For a project that will BUILD its image that is a
merge: every image-defining field comes from the wizard. For a project
that will OBTAIN the image the clone's envelope pins, the merge is the
wrong shape -- the image already exists, and the file must go on
describing it -- so this module owns the other policy: a WHITELIST of
runtime fields, the candidate overlay baseline captured before the
rewrite, and the registry's image-source record. The routes stay in
``registryRoutes``; what changes here changes when the pinned-image
lane's rules change, not when the registry's HTTP surface does.

THE CLASSIFICATION IS TOTAL
---------------------------

Every key the wizard's translation emits is either RUNTIME (a
``docker run`` flag or an environment variable the researcher
controls) or BASE (describes the image; preserved exactly as the
author committed it). The agent INSTALL keys are neither: the
acquisition writes them after the overlays succeed, so the file
describes the image that exists. A test calls the translation on a
default request and fails when a key is emitted that no tuple names,
and a second one fails when an overlay Dockerfile is shipped without a
classification in the builder.
"""

__all__ = [
    "S_ENVIRONMENT_SOURCE_ARCHIVE",
    "S_ENVIRONMENT_SOURCE_BUILD",
    "T_BASE_FEATURE_KEYS",
    "T_BASE_YAML_KEYS",
    "T_RUNTIME_YAML_KEYS",
    "fdictBuildArchiveImageSource",
    "fdictDescribePinnedEnvironmentForWizard",
    "fdictOverlayRuntimeFieldsOnly",
    "fnRewriteConfigForObtainedImage",
]

import os

from fastapi import HTTPException


S_ENVIRONMENT_SOURCE_BUILD = "build"
S_ENVIRONMENT_SOURCE_ARCHIVE = "archive"

T_RUNTIME_YAML_KEYS = (
    "projectName", "cpuLimit", "memoryLimitGigabytes", "secrets",
    "neverSleep", "networkIsolation",
)
T_BASE_YAML_KEYS = (
    "baseImage", "pythonVersion", "containerUser", "workspaceRoot",
    "packageManager", "repositories", "systemPackages", "pythonPackages",
    "pipInstallFlags", "condaPackages",
)
# Feature keys that alter the base environment; fixed by the pinned
# image. The agent AUTO-UPDATE keys are runtime (environment variables).
T_BASE_FEATURE_KEYS = (
    "jupyter", "rLanguage", "julia", "database", "dvc", "nestedSampling",
    "latex", "gpu",
)


def fdictOverlayRuntimeFieldsOnly(sConfigPath, request):
    """Return the clone's vaibify.yml with ONLY the runtime fields overlaid.

    A whitelist, not a merge: the author's base-defining fields survive
    exactly, the researcher's runtime choices land, and the agent
    install keys are not written at all -- no overlay has been built
    yet and the file must describe the image that exists. Fields the
    translation never manages (``dashboardPort`` among them) are
    preserved by never being touched.
    """
    import yaml
    from vaibify.gui.registryRoutes import (
        _T_AGENT_SETTINGS,
        _fdictBuildYamlFromRequest,
    )
    with open(sConfigPath, "r", encoding="utf-8") as fileHandle:
        dictExisting = yaml.safe_load(fileHandle) or {}
    dictMerged = dict(dictExisting)
    dictRequested = _fdictBuildYamlFromRequest(request)
    for sKey in T_RUNTIME_YAML_KEYS:
        if sKey in dictRequested:
            dictMerged[sKey] = dictRequested[sKey]
        else:
            dictMerged.pop(sKey, None)
    dictFeatures = dict(dictExisting.get("features") or {})
    for sAgent, _sInstallField, _sAutoField, _sLabel in _T_AGENT_SETTINGS:
        sAutoUpdateKey = f"{sAgent}AutoUpdate"
        dictFeatures[sAutoUpdateKey] = dictRequested["features"][sAutoUpdateKey]
    dictMerged["features"] = dictFeatures
    return dictMerged


def fnRewriteConfigForObtainedImage(sConfigPath, request):
    """Write the runtime-only overlay, validated, for an obtained image."""
    from vaibify.config.projectConfig import (
        fbValidateConfig,
        fconfigFromYamlDict,
        fnSaveToFile,
    )
    dictMerged = fdictOverlayRuntimeFieldsOnly(sConfigPath, request)
    if not fbValidateConfig(dictMerged):
        raise HTTPException(
            400,
            "The clone's configuration is invalid after the runtime "
            "settings were applied; check the repositories and package "
            "lists it committed.",
        )
    fnSaveToFile(fconfigFromYamlDict(dictMerged), sConfigPath)


def fdictDescribePinnedEnvironmentForWizard(dictProject, connectionDocker):
    """Return what the wizard's Environment page shows for one clone."""
    from vaibify.docker import daemonDescription
    from vaibify.docker.imageBuilder import T_AGENT_OVERLAY_NAMES
    from vaibify.reproducibility.reproductionSource import (
        fdictDescribePinnedEnvironment,
    )
    dictPinned = fdictDescribePinnedEnvironment(dictProject["sDirectory"])
    return dict(
        dictPinned,
        dictDaemon=daemonDescription.fdictDescribeDaemonForPlatform(
            connectionDocker, dictPinned["sRequiredPlatform"],
        ),
        dictAuthorFeatures=_fdictReadAuthorFeatures(dictProject["sConfigPath"]),
        listAgentOverlays=list(T_AGENT_OVERLAY_NAMES),
        listBaseFeatureKeys=list(T_BASE_FEATURE_KEYS),
    )


def _fdictReadAuthorFeatures(sConfigPath):
    """Return the ``features`` block the repository's own config carries."""
    import yaml
    try:
        with open(sConfigPath, "r", encoding="utf-8") as fileHandle:
            dictConfig = yaml.safe_load(fileHandle) or {}
    except (OSError, yaml.YAMLError):
        return {}
    dictFeatures = dictConfig.get("features")
    return dict(dictFeatures) if isinstance(dictFeatures, dict) else {}


def fdictBuildArchiveImageSource(dictProject, request):
    """Return the registry's ``dictImageSource`` for an obtained image, or 409.

    The candidate baseline -- which overlays the author's image was
    built with -- comes from the committed Dockerfile's header line,
    else from the resolver over the committed ``vaibify.yml``. Both
    are CLAIMS; the acquisition binds the baseline to the image before
    trusting it. The additions are the agents the wizard asked for
    beyond that candidate.
    """
    from vaibify.docker.imageBuilder import T_AGENT_OVERLAY_NAMES
    from vaibify.reproducibility.reproductionSource import (
        fdictDescribePinnedEnvironment,
    )
    dictPinned = fdictDescribePinnedEnvironment(dictProject["sDirectory"])
    if not dictPinned["bObtainable"]:
        raise HTTPException(409, detail={"sMessage": (
            "This clone's envelope does not pin an image vaibify can "
            "obtain: " + dictPinned["sRefusal"]
        )})
    listCandidate, sFingerprint = _ftCandidateOverlayBaseline(dictProject)
    setAgents = set(T_AGENT_OVERLAY_NAMES)
    listAdditional = [
        sFeature for sFeature in request.listFeatures
        if sFeature in setAgents and sFeature not in listCandidate
    ]
    return {
        "sSource": S_ENVIRONMENT_SOURCE_ARCHIVE,
        "bAllowEmulation": bool(request.bAllowEmulation),
        "sPinnedImageReference": dictPinned["sPinnedImageReference"],
        "sRequiredPlatform": dictPinned["sRequiredPlatform"],
        "listAuthorOverlays": listCandidate,
        "sAuthorRecipeFingerprint": sFingerprint,
        "listAdditionalAgents": listAdditional,
        "listResolvedOverlays": None,
    }


def _ftCandidateOverlayBaseline(dictProject):
    """Return ``(listCandidateOverlays, sRecipeFingerprint)`` from the clone."""
    from vaibify.cli.configLoader import fconfigLoadFromPath
    from vaibify.docker.imageBuilder import flistDetermineOverlays
    from vaibify.reproducibility.dockerfileComposer import (
        flistExtractOverlayOrder,
        fsExtractRecipeFingerprint,
    )
    from vaibify.reproducibility.dockerfileLint import S_DOCKERFILE_FILENAME
    sDockerfilePath = os.path.join(
        dictProject["sDirectory"], S_DOCKERFILE_FILENAME,
    )
    try:
        with open(sDockerfilePath, "r", encoding="utf-8") as fileHandle:
            sText = fileHandle.read()
    except OSError:
        sText = ""
    listFromHeader = flistExtractOverlayOrder(sText)
    if listFromHeader is not None:
        return listFromHeader, fsExtractRecipeFingerprint(sText)
    try:
        return flistDetermineOverlays(
            fconfigLoadFromPath(dictProject["sConfigPath"]),
        ), ""
    except ValueError as error:
        raise HTTPException(409, detail={"sMessage": (
            "The clone's vaibify.yml cannot be loaded, so the author's "
            f"feature set cannot be read: {error}"
        )})
