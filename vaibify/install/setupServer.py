"""FastAPI setup wizard for interactive project configuration.

Presents a web UI that walks the user through template selection,
project naming, feature toggles, and package lists.  Writes the
result to a vaibify.yml configuration file.
"""

import os
from pathlib import Path

import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Dict, List, Optional

from vaibify.config.templateManager import (
    flistAvailableTemplates,
    fdictLoadTemplateConfig,
)
from vaibify.config.projectConfig import (
    fconfigLoadFromFile,
    fdictLoadDefaults,
)


_STATIC_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "gui", "static"
)


class WizardConfigRequest(BaseModel):
    sProjectName: str = ""
    sContainerUser: str = "researcher"
    sPythonVersion: str = "3.12"
    sBaseImage: str = "ubuntu:24.04"
    sWorkspaceRoot: str = "/workspace"
    sPackageManager: str = "pip"
    listRepositories: List[str] = []
    listFeatures: List[str] = []
    listPipPackages: List[str] = []
    listAptPackages: List[str] = []
    sOverleafProjectId: str = ""
    sZenodoDepositionId: str = ""
    bNeverSleep: bool = False


class ValidateResponse(BaseModel):
    bValid: bool
    listErrors: List[str] = []


def fappCreateSetupWizard(sOutputDirectory="."):
    """Build and return the setup wizard FastAPI application."""
    app = FastAPI(title="Vaibify Setup Wizard")
    _fnRegisterRoutes(app, sOutputDirectory)
    _fnRegisterStaticFiles(app)
    return app


def _fnRegisterRoutes(app, sOutputDirectory):
    """Register all setup wizard API routes."""
    _fnRegisterTemplateRoutes(app)
    _fnRegisterConfigRoutes(app, sOutputDirectory)
    _fnRegisterBuildRoute(app, sOutputDirectory)


def _fnRegisterTemplateRoutes(app):
    """Register template listing and loading routes."""

    @app.get("/api/setup/templates")
    async def fnGetTemplates():
        try:
            listNames = flistAvailableTemplates()
            return [
                {"sName": s, "sDescription": ""}
                for s in listNames
            ]
        except FileNotFoundError:
            return []

    @app.get("/api/setup/templates/{sTemplateName}")
    async def fnGetTemplateConfig(sTemplateName: str):
        try:
            dictTemplate = fdictLoadTemplateConfig(sTemplateName)
            return _fdictTemplateToWizardFormat(
                sTemplateName, dictTemplate
            )
        except FileNotFoundError as error:
            raise HTTPException(404, str(error))


def _fnRegisterConfigRoutes(app, sOutputDirectory):
    """Register config load, validate, and save routes."""

    @app.get("/api/setup/config")
    async def fnGetExistingConfig():
        sPath = str(Path(sOutputDirectory) / "vaibify.yml")
        if not Path(sPath).is_file():
            return {}
        try:
            config = fconfigLoadFromFile(sPath)
            return _fdictConfigToWizardFormat(config)
        except Exception:
            return {}

    @app.get("/api/setup/defaults")
    async def fnGetDefaults():
        return fdictLoadDefaults()

    @app.post(
        "/api/setup/validate", response_model=ValidateResponse
    )
    async def fnValidateConfig(request: WizardConfigRequest):
        listErrors = _flistCollectErrors(request)
        return ValidateResponse(
            bValid=len(listErrors) == 0,
            listErrors=listErrors,
        )

    @app.post("/api/setup/save")
    async def fnSaveConfig(request: WizardConfigRequest):
        listErrors = _flistCollectErrors(request)
        if listErrors:
            raise HTTPException(
                400, {"listErrors": listErrors}
            )
        dictYaml = _fdictWizardToYaml(request)
        sFilePath = str(
            Path(sOutputDirectory) / "vaibify.yml"
        )
        _fnWriteYamlConfig(dictYaml, sFilePath)
        return {"sFilePath": sFilePath, "bSuccess": True}


def _fnRegisterBuildRoute(app, sOutputDirectory):
    """Register the build route."""

    @app.post("/api/setup/build")
    async def fnBuildContainer(request: WizardConfigRequest):
        listErrors = _flistCollectErrors(request)
        if listErrors:
            raise HTTPException(
                400, {"listErrors": listErrors}
            )
        dictYaml = _fdictWizardToYaml(request)
        sFilePath = str(
            Path(sOutputDirectory) / "vaibify.yml"
        )
        _fnWriteYamlConfig(dictYaml, sFilePath)
        return {
            "sMessage": "Configuration saved. "
            "Run 'vaibify build' to build the container.",
            "bSuccess": True,
        }


def _fnRegisterStaticFiles(app):
    """Serve the setup wizard HTML and static assets."""

    @app.get("/")
    async def fnServeIndex():
        return FileResponse(
            os.path.join(_STATIC_DIR, "setupWizard.html")
        )

    if os.path.isdir(_STATIC_DIR):
        app.mount(
            "/static",
            StaticFiles(directory=_STATIC_DIR),
            name="static",
        )


def _fdictTemplateToWizardFormat(sTemplateName, dictTemplate):
    """Convert a template config dict to wizard form fields."""
    listRepoUrls = []
    for dictRepo in dictTemplate.get("listRepositories", []):
        listRepoUrls.append(dictRepo.get("sUrl", ""))
    return {
        "sProjectName": sTemplateName,
        "listRepositories": listRepoUrls,
    }


def _fdictConfigToWizardFormat(config):
    """Convert a ProjectConfig to wizard form fields."""
    listRepoUrls = [
        d.get("url", "") for d in config.listRepositories
    ]
    listFeatures = _flistEnabledFeatures(config.features)
    return {
        "sProjectName": config.sProjectName,
        "sContainerUser": config.sContainerUser,
        "sPythonVersion": config.sPythonVersion,
        "sBaseImage": config.sBaseImage,
        "sWorkspaceRoot": config.sWorkspaceRoot,
        "sPackageManager": config.sPackageManager,
        "listRepositories": listRepoUrls,
        "listFeatures": listFeatures,
        "listPipPackages": config.listPythonPackages,
        "listAptPackages": config.listSystemPackages,
        "sOverleafProjectId": (
            config.reproducibility.overleaf.sProjectId
        ),
        "bNeverSleep": config.bNeverSleep,
    }


def _flistEnabledFeatures(features):
    """Return list of feature name strings that are enabled."""
    dictMap = {
        "jupyter": features.bJupyter,
        "rLanguage": features.bRLanguage,
        "julia": features.bJulia,
        "database": features.bDatabase,
        "dvc": features.bDvc,
        "latex": features.bLatex,
        "claude": features.bClaude,
        "gpu": features.bGpu,
    }
    return [s for s, b in dictMap.items() if b]


def _fdictWizardToYaml(request):
    """Convert wizard form data to vaibify.yml-compatible dict."""
    dictFeatures = _fdictFeaturesFromList(request.listFeatures)
    listRepos = _flistReposFromUrls(request.listRepositories)
    dictYaml = {
        "projectName": request.sProjectName,
        "containerUser": request.sContainerUser,
        "pythonVersion": request.sPythonVersion,
        "baseImage": request.sBaseImage,
        "workspaceRoot": request.sWorkspaceRoot,
        "packageManager": request.sPackageManager,
        "repositories": listRepos,
        "systemPackages": request.listAptPackages,
        "pythonPackages": request.listPipPackages,
        "features": dictFeatures,
        "neverSleep": request.bNeverSleep,
    }
    if request.sOverleafProjectId:
        dictYaml["reproducibility"] = {
            "overleaf": {
                "projectId": request.sOverleafProjectId,
            }
        }
    return dictYaml


def _fdictFeaturesFromList(listFeatures):
    """Convert a list of feature name strings to a bool dict."""
    listAllFeatures = [
        "jupyter", "rLanguage", "julia", "database",
        "dvc", "latex", "claude", "gpu",
    ]
    return {s: s in listFeatures for s in listAllFeatures}


def _flistReposFromUrls(listUrls):
    """Convert a list of repo URL strings to vaibify.yml format."""
    listRepos = []
    for sUrl in listUrls:
        sName = _fsRepoNameFromUrl(sUrl)
        listRepos.append({
            "name": sName,
            "url": sUrl,
            "branch": "main",
            "installMethod": "pip_editable",
        })
    return listRepos


def _fsRepoNameFromUrl(sUrl):
    """Extract a repository name from a git URL."""
    sName = sUrl.rstrip("/").rsplit("/", 1)[-1]
    if sName.endswith(".git"):
        sName = sName[:-4]
    return sName


def _flistCollectErrors(request):
    """Return a list of validation error strings."""
    listErrors = []
    if not request.sProjectName.strip():
        listErrors.append("projectName is required")
    if request.sPackageManager not in ("pip", "conda", "mamba"):
        listErrors.append(
            f"Invalid packageManager: '{request.sPackageManager}'"
        )
    return listErrors


def _fnWriteYamlConfig(dictConfig, sFilePath):
    """Write a configuration dict to YAML."""
    pathOutput = Path(sFilePath)
    pathOutput.parent.mkdir(parents=True, exist_ok=True)
    with open(pathOutput, "w") as fileHandle:
        yaml.safe_dump(
            dictConfig, fileHandle,
            default_flow_style=False, sort_keys=False,
        )
