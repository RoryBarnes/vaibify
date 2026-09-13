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
    "S_REMEDY_SWITCH_TO_PINNED_IMAGE",
    "fbSwitchToPinnedImageIsTheRemedy",
    "fdictBuildArchiveImageSource",
    "fdictBuildArchiveImageSourceForSwitch",
    "fdictDescribeImageOriginForProject",
    "fdictDescribePinnedEnvironmentForWizard",
    "fdictOverlayRuntimeFieldsOnly",
    "fnRestoreAuthorBaseFieldsFromGit",
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
    return _fdictArchiveImageSource(
        dictProject, list(request.listFeatures), bool(request.bAllowEmulation),
    )


def _fdictArchiveImageSource(dictProject, listRequestedFeatures, bAllowEmulation):
    """Build the source record from a feature list, whoever supplied it."""
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
        sFeature for sFeature in listRequestedFeatures
        if sFeature in setAgents and sFeature not in listCandidate
    ]
    return {
        "sSource": S_ENVIRONMENT_SOURCE_ARCHIVE,
        "bAllowEmulation": bool(bAllowEmulation),
        "sPinnedImageReference": dictPinned["sPinnedImageReference"],
        "sRequiredPlatform": dictPinned["sRequiredPlatform"],
        "listAuthorOverlays": listCandidate,
        "sAuthorRecipeFingerprint": sFingerprint,
        "listAdditionalAgents": listAdditional,
        "listResolvedOverlays": None,
    }


S_REMEDY_SWITCH_TO_PINNED_IMAGE = (
    "this container runs an image built from the Dockerfile, but the "
    "envelope pins the author's image — on the Environments hub, open "
    "the tile's menu and choose 'Switch to the author's pinned image'"
)


def fdictDescribeImageOriginForProject(dictProject):
    """Say whether a project's image was BUILT and whether its clone pins one.

    Two facts read together by the registry listing (the tile offers
    the switch on them) and by the verify's refusals (the package
    mismatch and the Dockerfile-provenance mismatch both have this one
    cause on a containerized clone, and for it the remedy is the
    switch -- the rewrites they otherwise name would make the AUTHOR's
    committed files describe a build the author never made). Answered
    from the clone's envelope by the wizard's own validator, never
    from the daemon. An unregistered, host or already-obtained project
    reads as neither, so an unknown origin never names the switch.
    """
    from vaibify.config.registryManager import fbProjectImageIsObtained
    from vaibify.reproducibility.reproductionSource import (
        fdictDescribePinnedEnvironment,
    )
    dictAnswer = {"bImageWasBuilt": False, "bPinnedImageObtainable": False}
    if not dictProject or dictProject.get("sMode") != "container":
        return dictAnswer
    if fbProjectImageIsObtained(dictProject):
        return dictAnswer
    dictAnswer["bImageWasBuilt"] = True
    sDirectory = dictProject.get("sDirectory") or ""
    if not sDirectory:
        return dictAnswer
    try:
        dictAnswer["bPinnedImageObtainable"] = bool(
            fdictDescribePinnedEnvironment(sDirectory)["bObtainable"],
        )
    except OSError:
        pass
    return dictAnswer


def fbSwitchToPinnedImageIsTheRemedy(dictImageOrigin):
    """True iff a container-fact refusal should name the switch, not a rewrite."""
    return bool(
        (dictImageOrigin or {}).get("bImageWasBuilt")
        and (dictImageOrigin or {}).get("bPinnedImageObtainable")
    )


def fdictBuildArchiveImageSourceForSwitch(dictProject, bAllowEmulation):
    """Return the source record for a BUILT project switching to the pin.

    The transition has no wizard request to read the researcher's
    agents from; it reads them from the clone's working ``vaibify.yml``
    -- the agents the built image carried -- BEFORE the author's base
    fields are restored over it, then restores, then resolves the
    baseline (the committed Dockerfile's header, else the restored
    file) so the additions are exactly the agents the author's image
    lacks. The order is load-bearing: resolving the baseline from the
    working copy would read the researcher's own agents as the
    author's and stack none of them.
    """
    listRequestedAgents = _flistEnabledAgentsInConfig(dictProject["sConfigPath"])
    fnRestoreAuthorBaseFieldsFromGit(dictProject)
    return _fdictArchiveImageSource(
        dictProject, listRequestedAgents, bAllowEmulation,
    )


def _flistEnabledAgentsInConfig(sConfigPath):
    """Return the agent overlays a ``vaibify.yml`` currently enables."""
    import yaml
    from vaibify.docker.imageBuilder import T_AGENT_OVERLAY_NAMES
    try:
        with open(sConfigPath, "r", encoding="utf-8") as fileHandle:
            dictConfig = yaml.safe_load(fileHandle) or {}
    except (OSError, yaml.YAMLError):
        return []
    dictFeatures = dictConfig.get("features")
    if not isinstance(dictFeatures, dict):
        return []
    return [
        sAgent for sAgent in T_AGENT_OVERLAY_NAMES
        if dictFeatures.get(sAgent) is True
    ]


def fnRestoreAuthorBaseFieldsFromGit(dictProject):
    """Put the author's image-defining fields back into the clone's config.

    A build rewrote every image-defining field from the wizard's
    answers (the convert route's overlay is a MERGE for a built
    image). An obtained image is the author's, so the file must
    describe it again: the base keys and the whole ``features`` block
    come from the copy committed at HEAD -- the author's -- while the
    runtime keys, the agent auto-update switches and every key the
    translation never manages keep the working copy's value. It is
    ``fdictOverlayRuntimeFieldsOnly`` with the roles swapped: there the
    author's file is on disk and the runtime choices arrive in a
    request; here the runtime choices are on disk and the author's
    file is in git. Refuses by name when HEAD holds no copy, because
    without the author's file there is nothing honest to restore.
    """
    import yaml
    from vaibify.config.projectConfig import (
        fbValidateConfig,
        fconfigFromYamlDict,
        fnSaveToFile,
    )
    from vaibify.reproducibility.reproductionSource import (
        fsReadCommittedFileOrNone,
    )
    sConfigPath = dictProject["sConfigPath"]
    sDirectory = dictProject["sDirectory"]
    sRelativePath = os.path.relpath(sConfigPath, sDirectory)
    sCommitted = None
    if not sRelativePath.startswith(os.pardir):
        sCommitted = fsReadCommittedFileOrNone(sDirectory, sRelativePath)
    if sCommitted is None:
        raise HTTPException(409, detail={"sMessage": (
            "The author's vaibify.yml is not committed in this clone, so "
            "the build's rewrite of it cannot be undone automatically. "
            "Restore the file from the published repository (for "
            "example `git checkout -- vaibify.yml`), then switch again."
        )})
    try:
        dictAuthor = yaml.safe_load(sCommitted) or {}
        with open(sConfigPath, "r", encoding="utf-8") as fileHandle:
            dictExisting = yaml.safe_load(fileHandle) or {}
    except (OSError, yaml.YAMLError) as error:
        raise HTTPException(409, detail={"sMessage": (
            f"The clone's vaibify.yml could not be read: {error}"
        )})
    if not isinstance(dictAuthor, dict):
        raise HTTPException(409, detail={"sMessage": (
            "The committed vaibify.yml is not a mapping, so the author's "
            "environment cannot be read from it."
        )})
    dictMerged = _fdictAuthorBaseOntoRuntime(dictAuthor, dictExisting)
    if not fbValidateConfig(dictMerged):
        raise HTTPException(409, detail={"sMessage": (
            "The clone's configuration is invalid once the author's "
            "environment fields are restored; check the committed "
            "vaibify.yml."
        )})
    fnSaveToFile(fconfigFromYamlDict(dictMerged), sConfigPath)


def _fdictAuthorBaseOntoRuntime(dictAuthor, dictExisting):
    """Return the working config with the author's base fields restored."""
    from vaibify.gui.registryRoutes import _T_AGENT_SETTINGS
    dictMerged = dict(dictExisting)
    for sKey in T_BASE_YAML_KEYS:
        if sKey in dictAuthor:
            dictMerged[sKey] = dictAuthor[sKey]
        else:
            dictMerged.pop(sKey, None)
    dictFeatures = dict(dictAuthor.get("features") or {})
    dictExistingFeatures = dictExisting.get("features") or {}
    for sAgent, _sInstallField, _sAutoField, _sLabel in _T_AGENT_SETTINGS:
        sAutoUpdateKey = f"{sAgent}AutoUpdate"
        if sAutoUpdateKey in dictExistingFeatures:
            dictFeatures[sAutoUpdateKey] = dictExistingFeatures[sAutoUpdateKey]
    dictMerged["features"] = dictFeatures
    return dictMerged


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
