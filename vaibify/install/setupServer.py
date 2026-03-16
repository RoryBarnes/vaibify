"""FastAPI setup wizard for interactive project configuration.

Presents a web UI that walks the user through template selection,
project naming, and feature toggles.  Writes the result to a
YAML configuration file.
"""

from pathlib import Path

import yaml
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Dict, List

from vaibify.config.templateManager import flistAvailableTemplates
from vaibify.config.projectConfig import fdictLoadDefaults


class ConfigRequest(BaseModel):
    projectName: str = ""
    containerUser: str = "researcher"
    pythonVersion: str = "3.12"
    baseImage: str = "ubuntu:24.04"
    packageManager: str = "pip"
    repositories: List[Dict[str, str]] = []
    features: Dict[str, bool] = {}


class ValidateResponse(BaseModel):
    bValid: bool
    listErrors: List[str] = []


def fappCreateSetupWizard(sOutputDirectory="."):
    """Build and return the setup wizard FastAPI application.

    Parameters
    ----------
    sOutputDirectory : str
        Directory where vaibify.yml will be written.

    Returns
    -------
    FastAPI
        Configured application instance.
    """
    app = FastAPI(title="Vaibify Setup Wizard")
    _fnRegisterReadRoutes(app)
    _fnRegisterWriteRoutes(app, sOutputDirectory)
    return app


def _fnRegisterReadRoutes(app):
    """Register template listing, defaults, and validation routes."""

    @app.get("/api/templates")
    async def fnGetTemplates():
        try:
            return flistAvailableTemplates()
        except FileNotFoundError:
            return []

    @app.get("/api/defaults")
    async def fnGetDefaults():
        return fdictLoadDefaults()

    @app.post("/api/validate", response_model=ValidateResponse)
    async def fnValidateConfig(request: ConfigRequest):
        dictConfig = request.model_dump()
        listErrors = _flistCollectValidationErrors(dictConfig)
        bValid = len(listErrors) == 0
        return ValidateResponse(bValid=bValid, listErrors=listErrors)


def _fnRegisterWriteRoutes(app, sOutputDirectory):
    """Register the save route."""

    @app.post("/api/save")
    async def fnSaveConfig(request: ConfigRequest):
        dictConfig = request.model_dump()
        listErrors = _flistCollectValidationErrors(dictConfig)
        if listErrors:
            raise HTTPException(
                status_code=400,
                detail={"listErrors": listErrors},
            )
        sFilePath = str(
            Path(sOutputDirectory) / "vaibify.yml"
        )
        _fnWriteYamlConfig(dictConfig, sFilePath)
        return {"sFilePath": sFilePath, "bSuccess": True}


def _flistCollectValidationErrors(dictConfig):
    """Return a list of human-readable validation error strings."""
    listErrors = []
    _fnValidateProjectName(dictConfig, listErrors)
    _fnValidatePackageManager(dictConfig, listErrors)
    _fnValidateFeatures(dictConfig, listErrors)
    return listErrors


def _fnValidateProjectName(dictConfig, listErrors):
    """Append an error if projectName is missing or empty."""
    sProjectName = dictConfig.get("projectName", "")
    if not isinstance(sProjectName, str) or not sProjectName.strip():
        listErrors.append("projectName is required")


def _fnValidatePackageManager(dictConfig, listErrors):
    """Append an error if packageManager is invalid."""
    sManager = dictConfig.get("packageManager", "pip")
    if sManager not in ("pip", "conda", "mamba"):
        listErrors.append(
            f"Invalid packageManager: '{sManager}'"
        )


def _fnValidateFeatures(dictConfig, listErrors):
    """Append errors for non-boolean feature values."""
    dictFeatures = dictConfig.get("features", {})
    if not isinstance(dictFeatures, dict):
        listErrors.append("features must be a mapping")
        return
    for sKey, value in dictFeatures.items():
        if not isinstance(value, bool):
            listErrors.append(
                f"Feature '{sKey}' must be a boolean"
            )


def _fnWriteYamlConfig(dictConfig, sFilePath):
    """Write a configuration dict to YAML."""
    pathOutput = Path(sFilePath)
    pathOutput.parent.mkdir(parents=True, exist_ok=True)
    with open(pathOutput, "w") as fileHandle:
        yaml.safe_dump(
            dictConfig, fileHandle,
            default_flow_style=False, sort_keys=False,
        )
