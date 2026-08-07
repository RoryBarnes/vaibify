"""YAML project configuration parser with dataclass validation."""

import copy
import posixpath
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

import yaml

from .bindMountValidator import (
    BindMountValidationError,
    fnValidateBindMountList,
)


@dataclass
class FeaturesConfig:
    bJupyter: bool = False
    bRLanguage: bool = False
    bJulia: bool = False
    bDatabase: bool = False
    bDvc: bool = False
    bNestedSampling: bool = False
    bLatex: bool = True
    bClaude: bool = False
    bClaudeAutoUpdate: bool = True
    bCodex: bool = False
    bCodexAutoUpdate: bool = True
    bGemini: bool = False
    bGeminiAutoUpdate: bool = True
    bOpenCode: bool = False
    bOpenCodeAutoUpdate: bool = True
    bCline: bool = False
    bClineAutoUpdate: bool = True
    bOpenHands: bool = False
    bOpenHandsAutoUpdate: bool = True
    bPi: bool = False
    bPiAutoUpdate: bool = True
    bGpu: bool = False


@dataclass
class OverleafConfig:
    sProjectId: str = ""
    sFigureDirectory: str = "figures"
    listPullPaths: List[str] = field(default_factory=list)


@dataclass
class ReproducibilityConfig:
    sZenodoService: str = "sandbox"
    sLatexRoot: str = "src/tex"
    sFiguresRoot: str = "src/tex/figures"
    overleaf: OverleafConfig = field(
        default_factory=OverleafConfig
    )


@dataclass
class ProjectConfig:
    sProjectName: str = ""
    sContainerUser: str = "researcher"
    sPythonVersion: str = "3.12"
    sBaseImage: str = "ubuntu:24.04"
    sWorkspaceRoot: str = "/workspace"
    sPackageManager: str = "pip"
    listRepositories: List[Dict[str, str]] = field(
        default_factory=list
    )
    listSystemPackages: List[str] = field(
        default_factory=lambda: [
            "gcc", "make", "git", "curl",
            "ca-certificates", "gnupg", "gosu", "time",
        ]
    )
    listPythonPackages: List[str] = field(default_factory=list)
    sPipInstallFlags: str = "--prefer-binary"
    listCondaPackages: List[str] = field(default_factory=list)
    features: FeaturesConfig = field(
        default_factory=FeaturesConfig
    )
    listBinaries: List[Dict[str, str]] = field(
        default_factory=list
    )
    listPorts: List[Dict[str, Any]] = field(
        default_factory=list
    )
    listBindMounts: List[Dict[str, Any]] = field(
        default_factory=list
    )
    listSecrets: List[Dict[str, str]] = field(
        default_factory=list
    )
    reproducibility: ReproducibilityConfig = field(
        default_factory=ReproducibilityConfig
    )
    bNetworkIsolation: bool = False
    bNeverSleep: bool = False
    iDashboardPort: int = 0
    iCpuLimit: int = 0
    fMemoryLimitGigabytes: float = 0.0


# Mapping from camelCase YAML keys to Hungarian dataclass fields
_YAML_TO_HUNGARIAN = {
    "projectName": "sProjectName",
    "containerUser": "sContainerUser",
    "pythonVersion": "sPythonVersion",
    "baseImage": "sBaseImage",
    "workspaceRoot": "sWorkspaceRoot",
    "packageManager": "sPackageManager",
    "repositories": "listRepositories",
    "systemPackages": "listSystemPackages",
    "pythonPackages": "listPythonPackages",
    "pipInstallFlags": "sPipInstallFlags",
    "condaPackages": "listCondaPackages",
    "features": "features",
    "binaries": "listBinaries",
    "ports": "listPorts",
    "bindMounts": "listBindMounts",
    "secrets": "listSecrets",
    "reproducibility": "reproducibility",
    "networkIsolation": "bNetworkIsolation",
    "neverSleep": "bNeverSleep",
    "dashboardPort": "iDashboardPort",
    "cpuLimit": "iCpuLimit",
    "memoryLimitGigabytes": "fMemoryLimitGigabytes",
}

_HUNGARIAN_TO_YAML = {v: k for k, v in _YAML_TO_HUNGARIAN.items()}

_FEATURES_YAML_TO_HUNGARIAN = {
    "jupyter": "bJupyter",
    "rLanguage": "bRLanguage",
    "julia": "bJulia",
    "database": "bDatabase",
    "dvc": "bDvc",
    "nestedSampling": "bNestedSampling",
    "latex": "bLatex",
    "claude": "bClaude",
    "claudeAutoUpdate": "bClaudeAutoUpdate",
    "codex": "bCodex",
    "codexAutoUpdate": "bCodexAutoUpdate",
    "gemini": "bGemini",
    "geminiAutoUpdate": "bGeminiAutoUpdate",
    "opencode": "bOpenCode",
    "opencodeAutoUpdate": "bOpenCodeAutoUpdate",
    "cline": "bCline",
    "clineAutoUpdate": "bClineAutoUpdate",
    "openhands": "bOpenHands",
    "openhandsAutoUpdate": "bOpenHandsAutoUpdate",
    "pi": "bPi",
    "piAutoUpdate": "bPiAutoUpdate",
    "gpu": "bGpu",
}

_REPRO_YAML_TO_HUNGARIAN = {
    "zenodoService": "sZenodoService",
    "latexRoot": "sLatexRoot",
    "figuresRoot": "sFiguresRoot",
    "overleaf": "overleaf",
}

_OVERLEAF_YAML_TO_HUNGARIAN = {
    "projectId": "sProjectId",
    "figureDirectory": "sFigureDirectory",
    "pullPaths": "listPullPaths",
}

_VALID_PACKAGE_MANAGERS = {"pip", "conda", "mamba"}


def fdictLoadDefaults():
    """Return default configuration as a plain dictionary."""
    configDefault = ProjectConfig()
    return _fdictConfigToYamlDict(configDefault)


def fconfigLoadFromFile(sFilePath):
    """Read YAML config, merge with defaults, validate, return ProjectConfig.

    Parameters
    ----------
    sFilePath : str
        Path to the YAML configuration file.

    Returns
    -------
    ProjectConfig
        Validated configuration dataclass instance.
    """
    dictRaw = _fdictReadYaml(sFilePath)
    dictMerged = _fdictMergeWithDefaults(dictRaw)
    if not fbValidateConfig(dictMerged):
        raise ValueError(
            f"Configuration validation failed for '{sFilePath}'."
        )
    return _fconfigFromYamlDict(dictMerged)


def fnSaveToFile(config, sFilePath):
    """Write a ProjectConfig instance to a YAML file.

    Parameters
    ----------
    config : ProjectConfig
        Configuration to serialize.
    sFilePath : str
        Destination file path.
    """
    dictYaml = _fdictConfigToYamlDict(config)
    pathOutput = Path(sFilePath)
    pathOutput.parent.mkdir(parents=True, exist_ok=True)
    with open(pathOutput, "w") as fileHandle:
        yaml.safe_dump(
            dictYaml, fileHandle,
            default_flow_style=False, sort_keys=False,
        )


def fconfigFromYamlDict(dictRaw):
    """Merge a camelCase YAML dict with defaults and return ProjectConfig.

    Parameters
    ----------
    dictRaw : dict
        Raw camelCase configuration dictionary (may be partial).

    Returns
    -------
    ProjectConfig
        Fully-populated configuration dataclass instance.
    """
    dictMerged = _fdictMergeWithDefaults(dictRaw)
    return _fconfigFromYamlDict(dictMerged)


def fbValidateConfig(dictConfig):
    """Validate required fields and types in a camelCase config dict."""
    if not isinstance(dictConfig, dict):
        return False
    listChecks = [
        _fbValidateProjectName,
        _fbValidatePackageManager,
        _fbValidateCondaPackages,
        _fbValidateListFields,
        _fbValidateRepositories,
        _fbValidateFeatures,
        _fbValidateDashboardPort,
        _fbValidateResourceLimits,
    ]
    return all(fnCheck(dictConfig) for fnCheck in listChecks)


# A repository ``name`` is a path basename (the clone directory); a
# ``destination`` is an in-workspace relative path (where the clone is
# moved). Both are interpolated into ``container.conf`` records of the
# form ``name|url|branch|install_method[|destination]``, so ``|`` and the
# record separators must never appear in any field, and ``name`` must not
# be a traversal component or a vaibify-managed / repo-root directory.
_S_SAFE_REPO_NAME = re.compile(r"^[A-Za-z0-9._-]+$")
_S_SAFE_GIT_REF = re.compile(r"^[A-Za-z0-9._/-]+$")
_SET_RESERVED_REPO_NAMES = frozenset({".vaibify", ".git", ".", ".."})
_SET_VALID_INSTALL_METHODS = frozenset({
    "c_and_pip", "pip_no_deps", "pip_editable", "scripts_only", "reference",
})
_I_MAX_REPO_FIELD_LENGTH = 256
# container.conf field/record delimiters plus NUL, forbidden in every field.
_S_FORBIDDEN_FIELD_CHARS = "|\n\r\x00"
# Shell metacharacters and whitespace additionally forbidden in a URL. The
# scp-like git form (``git@host:path``) is preserved: ``@ : / . - _ ~`` and
# alphanumerics are allowed; command-injection characters are not.
_S_URL_FORBIDDEN_CHARS = _S_FORBIDDEN_FIELD_CHARS + " \t;&$()<>`\"'\\*?{}[]"
# The only transports git may be handed. ``ext::`` runs an arbitrary command
# and ``file://`` reads an arbitrary host path, so both — and every other
# scheme — are refused. The scp-like form (``user@host:path``) has no
# ``://`` and is matched separately.
_TUPLE_SAFE_URL_SCHEMES = ("https://", "http://", "git://", "ssh://")
_S_SCP_LIKE_URL = re.compile(r"^[A-Za-z0-9._~-]+@[A-Za-z0-9._~-]+:.+$")


def _fbHasForbiddenFieldChar(sValue):
    """True if a field contains a container.conf delimiter or NUL."""
    return any(sChar in sValue for sChar in _S_FORBIDDEN_FIELD_CHARS)


def _fbIsSafeRepositoryName(sName):
    """True when a repo name is a safe, non-reserved path basename.

    The name derives the clone SOURCE path ``${WORKSPACE}/${name}``, so an
    unvalidated name is the same collision/deletion primitive as an
    unvalidated destination, on the source side — a name of ``data`` clones
    straight onto a ``/workspace/data`` bind mount with no traversal at all,
    and a name of ``.git`` turns ``/workspace`` back into a repository root.
    """
    if not sName or len(sName) > _I_MAX_REPO_FIELD_LENGTH:
        return False
    if sName in _SET_RESERVED_REPO_NAMES:
        return False
    return bool(_S_SAFE_REPO_NAME.match(sName))


def _fbIsSafeRepositoryUrl(sUrl):
    """True when a repo URL is metacharacter-free and uses a vetted transport.

    Beyond the shell-metacharacter and length checks, the URL must not begin
    with ``-`` — git would parse a leading-dash "URL" as an OPTION
    (``--upload-pack=…``, ``-oProxyCommand=…``), a remote-code primitive — and
    its transport must be one of ``https:// http:// git:// ssh://`` or the
    scp-like ``user@host:path`` form. Permissive git schemes such as ``ext::``
    (runs a command) and ``file://`` (reads an arbitrary host path) are
    refused.
    """
    if not sUrl or len(sUrl) > _I_MAX_REPO_FIELD_LENGTH:
        return False
    if any(sChar in sUrl for sChar in _S_URL_FORBIDDEN_CHARS):
        return False
    if sUrl.startswith("-"):
        return False
    if sUrl.startswith(_TUPLE_SAFE_URL_SCHEMES):
        return True
    return bool(_S_SCP_LIKE_URL.match(sUrl))


def _fbIsSafeGitRef(sRef):
    """True when a branch/ref uses only the safe git-ref charset (or empty)."""
    if not sRef:
        return True
    if len(sRef) > _I_MAX_REPO_FIELD_LENGTH or ".." in sRef:
        return False
    return bool(_S_SAFE_GIT_REF.match(sRef))


def _fbIsSafeRepositoryDestination(sDestination):
    """True when a destination is an in-workspace relative path (or empty).

    An empty destination means "clone in place, do not relocate," so it is
    valid and contributes no destination path to the overlap checks.
    """
    if not sDestination:
        return True
    if len(sDestination) > _I_MAX_REPO_FIELD_LENGTH:
        return False
    if _fbHasForbiddenFieldChar(sDestination):
        return False
    return not (
        posixpath.isabs(sDestination) or ".." in sDestination.split("/")
    )


def _fbValidateRepositoryEntry(dictRepo):
    """Validate one repository entry's type, name, url, ref, method, dest."""
    if not isinstance(dictRepo, dict):
        return False
    if not _fbIsSafeRepositoryName(dictRepo.get("name") or ""):
        return False
    if not _fbIsSafeRepositoryUrl(dictRepo.get("url") or ""):
        return False
    if not _fbIsSafeGitRef(dictRepo.get("branch") or ""):
        return False
    sMethod = dictRepo.get("installMethod") or "pip_editable"
    if sMethod not in _SET_VALID_INSTALL_METHODS:
        return False
    return _fbIsSafeRepositoryDestination(dictRepo.get("destination") or "")


def _fbValidateRepositories(dictConfig):
    """Reject unsafe repository entries and colliding source/dest paths.

    Validates every entry's fields, then checks that no repository's
    derived SOURCE (``${WORKSPACE}/${name}``) or DESTINATION
    (``${WORKSPACE}/${destination}``) path collides with a bind mount,
    another entry, or the entry's own source — the full class of the
    name/destination path-collision hazard, at the host validation layer.
    """
    listRepos = dictConfig.get("repositories") or []
    if not isinstance(listRepos, list):
        return False
    if not all(_fbValidateRepositoryEntry(dictRepo) for dictRepo in listRepos):
        return False
    return _fbRepositoryPathsAreSafe(dictConfig)


def _ftRepositoryPaths(sWorkspace, dictRepo):
    """Return ``(sSourceAbs, sDestAbs-or-None)`` for one repo, normalized."""
    sSourceAbs = posixpath.normpath(
        posixpath.join(sWorkspace, dictRepo.get("name") or "")
    )
    sDestination = dictRepo.get("destination") or ""
    if not sDestination:
        return (sSourceAbs, None)
    return (
        sSourceAbs,
        posixpath.normpath(posixpath.join(sWorkspace, sDestination)),
    )


def _fbRepositoryPathsAreSafe(dictConfig):
    """True when every repo source/dest path clears all overlap rules."""
    sWorkspace = posixpath.normpath(
        dictConfig.get("workspaceRoot") or "/workspace"
    )
    listBindTargets = _flistAbsoluteBindTargets(dictConfig)
    listEntries = [
        _ftRepositoryPaths(sWorkspace, dictRepo)
        for dictRepo in dictConfig.get("repositories") or []
        if isinstance(dictRepo, dict) and (dictRepo.get("name") or "")
    ]
    return (
        _fbNoBindOverlap(listEntries, listBindTargets)
        and _fbNoSameEntryOverlap(listEntries)
        and _fbNoCrossEntryOverlap(listEntries)
    )


def _fbNoBindOverlap(listEntries, listBindTargets):
    """True when no repo source or destination overlaps a bind target."""
    for sSourceAbs, sDestAbs in listEntries:
        for sPath in (sSourceAbs, sDestAbs):
            if sPath is None:
                continue
            if any(
                _fbPathsOverlapCaseInsensitive(sPath, sBind)
                for sBind in listBindTargets
            ):
                return False
    return True


def _fbNoSameEntryOverlap(listEntries):
    """True when no destination sits inside or above its own source.

    An exact no-op (destination resolves to the source) is allowed; any
    other overlap would move a directory into itself or delete part of its
    own source.
    """
    for sSourceAbs, sDestAbs in listEntries:
        if sDestAbs is None or sDestAbs.lower() == sSourceAbs.lower():
            continue
        if _fbPathsOverlapCaseInsensitive(sSourceAbs, sDestAbs):
            return False
    return True


def _fbNoCrossEntryOverlap(listEntries):
    """True when no entry's source/dest overlaps another entry's source/dest.

    Catches duplicate/case-colliding names (equal sources) and one repo's
    clone target silently becoming another's relocation target.
    """
    for iFirst, tFirst in enumerate(listEntries):
        listFirst = [sPath for sPath in tFirst if sPath is not None]
        for iSecond, tSecond in enumerate(listEntries):
            if iFirst == iSecond:
                continue
            listSecond = [sPath for sPath in tSecond if sPath is not None]
            for sPathFirst in listFirst:
                if any(
                    _fbPathsOverlapCaseInsensitive(sPathFirst, sPathSecond)
                    for sPathSecond in listSecond
                ):
                    return False
    return True


def _fbPathsOverlapCaseInsensitive(sFirst, sSecond):
    """Case-insensitive equal-or-nested overlap of two absolute paths."""
    return _fbContainerPathsOverlap(sFirst.lower(), sSecond.lower())


def _fbContainerPathsOverlap(sFirst, sSecond):
    """True when two absolute paths are equal or nest in either direction.

    Deleting ``sDestAbs`` harms a bind mount whether the mount IS that
    path, sits under it (recursively deleted), or contains it (its
    contents deleted) — so overlap is checked in both directions.
    """
    if sFirst == sSecond:
        return True
    return (
        sFirst.startswith(sSecond + "/")
        or sSecond.startswith(sFirst + "/")
    )


def _flistAbsoluteBindTargets(dictConfig):
    """Return every bind mount's container-side target as an absolute path.

    Unlike the earlier workspace-relative collector, this keeps a mount
    at the workspace root (and any target that is an ancestor of a
    customized workspace), so the overlap check can catch a relocation
    that would delete into it.
    """
    listTargets = []
    for dictMount in dictConfig.get("bindMounts") or []:
        if not isinstance(dictMount, dict):
            continue
        sTarget = dictMount.get("container") or ""
        if sTarget:
            listTargets.append(posixpath.normpath(sTarget))
    return listTargets


def _fdictReadYaml(sFilePath):
    """Read a YAML file and return its contents as a dictionary."""
    pathFile = Path(sFilePath)
    if not pathFile.exists():
        raise FileNotFoundError(
            f"Configuration file not found: '{sFilePath}'"
        )
    with open(pathFile, "r") as fileHandle:
        dictContents = yaml.safe_load(fileHandle)
    if dictContents is None:
        return {}
    if not isinstance(dictContents, dict):
        raise TypeError(
            f"Expected YAML mapping in '{sFilePath}', "
            f"got {type(dictContents).__name__}."
        )
    return dictContents


def _fdictMergeWithDefaults(dictRaw):
    """Merge raw YAML dict with defaults, preserving user overrides."""
    dictDefaults = fdictLoadDefaults()
    dictMerged = copy.deepcopy(dictDefaults)
    for sKey, value in dictRaw.items():
        if sKey == "features" and isinstance(value, dict):
            dictMerged.setdefault("features", {})
            dictMerged["features"].update(value)
        elif sKey == "reproducibility" and isinstance(value, dict):
            _fnMergeReproducibility(dictMerged, value)
        else:
            dictMerged[sKey] = value
    return dictMerged


def _fnMergeReproducibility(dictMerged, dictReproUser):
    """Merge user reproducibility settings into merged config."""
    dictMerged.setdefault("reproducibility", {})
    for sKey, value in dictReproUser.items():
        if sKey == "overleaf" and isinstance(value, dict):
            dictMerged["reproducibility"].setdefault(
                "overleaf", {}
            )
            dictMerged["reproducibility"]["overleaf"].update(
                value
            )
        else:
            dictMerged["reproducibility"][sKey] = value


_RE_PROJECT_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,62}$")


def _fbValidateProjectName(dictConfig):
    """Check that projectName is a safe identifier.

    The name is interpolated into Docker container names, image
    tags, volume names, and subprocess argv on the host. Restrict
    to ``^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,62}$`` so a malicious
    vaibify.yml can't smuggle shell metacharacters or path
    components into those contexts.
    """
    sProjectName = dictConfig.get("projectName", "")
    if not isinstance(sProjectName, str) or not sProjectName.strip():
        return False
    return _RE_PROJECT_NAME.match(sProjectName) is not None


def _fbValidatePackageManager(dictConfig):
    """Check that packageManager is one of the allowed values."""
    sManager = dictConfig.get("packageManager", "pip")
    if sManager not in _VALID_PACKAGE_MANAGERS:
        return False
    return True


def _fbValidateCondaPackages(dictConfig):
    """Reject conda packages, which nothing would install.

    ``condaPackages`` is parsed, serialized, and round-tripped, and the
    image build then ignores it entirely: the Dockerfile installs
    Miniforge for a non-pip package manager but has no ``conda
    install`` step, and ``imageBuilder._flistBuildArgPairs`` passes no
    build argument carrying the list. Accepting the field silently
    produced a container without the requested packages.

    Refusing is the honest behaviour until the build wiring exists.
    Declaring packages that never arrive is the same defect class as a
    dashboard reporting a state the container is not in.
    """
    return not dictConfig.get("condaPackages")


def _fbValidateListFields(dictConfig):
    """Check that list-typed fields are actually lists.

    ``bindMounts`` gets extra inspection via
    :func:`bindMountValidator.fnValidateBindMountList`: any host
    path that escapes $HOME or hits a known sensitive prefix
    surfaces as a clear ``vaibify.yml`` validation error.
    """
    listKeys = [
        "repositories", "systemPackages", "pythonPackages",
        "condaPackages", "binaries", "ports",
        "bindMounts", "secrets",
    ]
    for sKey in listKeys:
        value = dictConfig.get(sKey)
        if value is not None and not isinstance(value, list):
            return False
    return _fbValidateBindMountSafety(dictConfig)


def _fbValidateBindMountSafety(dictConfig):
    """Apply the bind-mount allowlist check; return False on violation."""
    listMounts = dictConfig.get("bindMounts") or []
    try:
        fnValidateBindMountList(listMounts)
    except BindMountValidationError:
        return False
    return True


def _fbValidateDashboardPort(dictConfig):
    """Check that dashboardPort, if present, is a valid TCP port number.

    Zero means "not yet assigned" and is the default. A non-zero value
    must be a usable user-space port so a typo in vaibify.yml cannot
    point the dashboard at a privileged or out-of-range port.
    """
    iPort = dictConfig.get("dashboardPort", 0)
    if not isinstance(iPort, int) or isinstance(iPort, bool):
        return False
    if iPort == 0:
        return True
    return 1024 <= iPort <= 65535


def _fbValidateResourceLimits(dictConfig):
    """Check that cpuLimit and memoryLimitGigabytes, if present, are sane.

    Zero means "no explicit limit" and is the default for both. A
    non-zero cpuLimit must be a positive integer. A non-zero memory
    limit must be at least 0.25 GB, so a typo in vaibify.yml cannot
    produce a container that Docker refuses to start or that dies on
    its first Python import.
    """
    iCpuLimit = dictConfig.get("cpuLimit", 0)
    if not isinstance(iCpuLimit, int) or isinstance(iCpuLimit, bool):
        return False
    if iCpuLimit < 0:
        return False
    fMemory = dictConfig.get("memoryLimitGigabytes", 0.0)
    if isinstance(fMemory, bool) or not isinstance(
        fMemory, (int, float)
    ):
        return False
    return fMemory == 0 or fMemory >= 0.25


def _fbValidateFeatures(dictConfig):
    """Check that features values are booleans."""
    dictFeatures = dictConfig.get("features")
    if dictFeatures is None:
        return True
    if not isinstance(dictFeatures, dict):
        return False
    for sKey, value in dictFeatures.items():
        if not isinstance(value, bool):
            return False
    return True


def _fconfigFromYamlDict(dictYaml):
    """Convert a camelCase YAML dict into a ProjectConfig instance."""
    dictHungarian = _fdictConvertTopLevel(dictYaml)
    featuresConfig = _ffeaturesFromDict(
        dictYaml.get("features", {})
    )
    reproConfig = _freproFromDict(
        dictYaml.get("reproducibility", {})
    )
    dictHungarian["features"] = featuresConfig
    dictHungarian["reproducibility"] = reproConfig
    return ProjectConfig(**dictHungarian)


def _fdictConvertTopLevel(dictYaml):
    """Map top-level camelCase keys to Hungarian field names."""
    dictResult = {}
    for sYamlKey, sHungarianKey in _YAML_TO_HUNGARIAN.items():
        if sYamlKey in dictYaml:
            dictResult[sHungarianKey] = dictYaml[sYamlKey]
    return dictResult


def _ffeaturesFromDict(dictFeatures):
    """Build a FeaturesConfig from a plain dict."""
    dictMapped = {}
    for sYamlKey, sField in _FEATURES_YAML_TO_HUNGARIAN.items():
        if sYamlKey in dictFeatures:
            dictMapped[sField] = dictFeatures[sYamlKey]
    return FeaturesConfig(**dictMapped)


def _freproFromDict(dictRepro):
    """Build a ReproducibilityConfig from a plain dict."""
    dictMapped = {}
    for sYamlKey, sField in _REPRO_YAML_TO_HUNGARIAN.items():
        if sYamlKey in dictRepro and sYamlKey != "overleaf":
            dictMapped[sField] = dictRepro[sYamlKey]
    dictMapped["overleaf"] = _foverleafFromDict(
        dictRepro.get("overleaf", {})
    )
    return ReproducibilityConfig(**dictMapped)


def _foverleafFromDict(dictOverleaf):
    """Build an OverleafConfig from a plain dict."""
    dictMapped = {}
    for sYamlKey, sField in _OVERLEAF_YAML_TO_HUNGARIAN.items():
        if sYamlKey in dictOverleaf:
            dictMapped[sField] = dictOverleaf[sYamlKey]
    return OverleafConfig(**dictMapped)


def _fdictConfigToYamlDict(config):
    """Convert a ProjectConfig instance to a camelCase YAML dict."""
    dictResult = _fdictScalarFieldsToYaml(config)
    dictResult["features"] = _fdictFeaturesToYaml(config.features)
    dictResult["reproducibility"] = _fdictReproToYaml(
        config.reproducibility
    )
    return dictResult


def _fdictScalarFieldsToYaml(config):
    """Convert scalar and list fields of ProjectConfig to YAML dict."""
    return {
        "projectName": config.sProjectName,
        "containerUser": config.sContainerUser,
        "pythonVersion": config.sPythonVersion,
        "baseImage": config.sBaseImage,
        "workspaceRoot": config.sWorkspaceRoot,
        "packageManager": config.sPackageManager,
        "repositories": config.listRepositories,
        "systemPackages": config.listSystemPackages,
        "pythonPackages": config.listPythonPackages,
        "pipInstallFlags": config.sPipInstallFlags,
        "condaPackages": config.listCondaPackages,
        "binaries": config.listBinaries,
        "ports": config.listPorts,
        "bindMounts": config.listBindMounts,
        "secrets": config.listSecrets,
        "networkIsolation": config.bNetworkIsolation,
        "neverSleep": config.bNeverSleep,
        "dashboardPort": config.iDashboardPort,
        "cpuLimit": config.iCpuLimit,
        "memoryLimitGigabytes": config.fMemoryLimitGigabytes,
    }


def _fdictFeaturesToYaml(features):
    """Convert FeaturesConfig to a camelCase dict."""
    return {
        "jupyter": features.bJupyter,
        "rLanguage": features.bRLanguage,
        "julia": features.bJulia,
        "database": features.bDatabase,
        "dvc": features.bDvc,
        "nestedSampling": features.bNestedSampling,
        "latex": features.bLatex,
        "claude": features.bClaude,
        "claudeAutoUpdate": features.bClaudeAutoUpdate,
        "codex": features.bCodex,
        "codexAutoUpdate": features.bCodexAutoUpdate,
        "gemini": features.bGemini,
        "geminiAutoUpdate": features.bGeminiAutoUpdate,
        "opencode": features.bOpenCode,
        "opencodeAutoUpdate": features.bOpenCodeAutoUpdate,
        "cline": features.bCline,
        "clineAutoUpdate": features.bClineAutoUpdate,
        "openhands": features.bOpenHands,
        "openhandsAutoUpdate": features.bOpenHandsAutoUpdate,
        "pi": features.bPi,
        "piAutoUpdate": features.bPiAutoUpdate,
        "gpu": features.bGpu,
    }


def _fdictReproToYaml(configRepro):
    """Convert ReproducibilityConfig to a camelCase dict."""
    return {
        "zenodoService": configRepro.sZenodoService,
        "latexRoot": configRepro.sLatexRoot,
        "figuresRoot": configRepro.sFiguresRoot,
        "overleaf": _fdictOverleafToYaml(configRepro.overleaf),
    }


def _fdictOverleafToYaml(configOverleaf):
    """Convert OverleafConfig to a camelCase dict."""
    return {
        "projectId": configOverleaf.sProjectId,
        "figureDirectory": configOverleaf.sFigureDirectory,
        "pullPaths": configOverleaf.listPullPaths,
    }
