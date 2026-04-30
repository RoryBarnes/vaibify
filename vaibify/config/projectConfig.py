"""YAML project configuration parser with dataclass validation."""

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

import yaml


@dataclass
class FeaturesConfig:
    bJupyter: bool = False
    bRLanguage: bool = False
    bJulia: bool = False
    bDatabase: bool = False
    bDvc: bool = False
    bLatex: bool = True
    bClaude: bool = False
    bClaudeAutoUpdate: bool = True
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
            "ca-certificates", "gnupg", "gosu", "sudo", "time",
        ]
    )
    listPythonPackages: List[str] = field(default_factory=list)
    sPipInstallFlags: str = ""
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
}

_HUNGARIAN_TO_YAML = {v: k for k, v in _YAML_TO_HUNGARIAN.items()}

_FEATURES_YAML_TO_HUNGARIAN = {
    "jupyter": "bJupyter",
    "rLanguage": "bRLanguage",
    "julia": "bJulia",
    "database": "bDatabase",
    "dvc": "bDvc",
    "latex": "bLatex",
    "claude": "bClaude",
    "claudeAutoUpdate": "bClaudeAutoUpdate",
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
        _fbValidateListFields,
        _fbValidateFeatures,
    ]
    return all(fnCheck(dictConfig) for fnCheck in listChecks)


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


def _fbValidateProjectName(dictConfig):
    """Check that projectName is a non-empty string."""
    sProjectName = dictConfig.get("projectName", "")
    if not isinstance(sProjectName, str) or not sProjectName.strip():
        return False
    return True


def _fbValidatePackageManager(dictConfig):
    """Check that packageManager is one of the allowed values."""
    sManager = dictConfig.get("packageManager", "pip")
    if sManager not in _VALID_PACKAGE_MANAGERS:
        return False
    return True


def _fbValidateListFields(dictConfig):
    """Check that list-typed fields are actually lists."""
    listKeys = [
        "repositories", "systemPackages", "pythonPackages",
        "condaPackages", "binaries", "ports",
        "bindMounts", "secrets",
    ]
    for sKey in listKeys:
        value = dictConfig.get(sKey)
        if value is not None and not isinstance(value, list):
            return False
    return True


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
    }


def _fdictFeaturesToYaml(features):
    """Convert FeaturesConfig to a camelCase dict."""
    return {
        "jupyter": features.bJupyter,
        "rLanguage": features.bRLanguage,
        "julia": features.bJulia,
        "database": features.bDatabase,
        "dvc": features.bDvc,
        "latex": features.bLatex,
        "claude": features.bClaude,
        "claudeAutoUpdate": features.bClaudeAutoUpdate,
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
