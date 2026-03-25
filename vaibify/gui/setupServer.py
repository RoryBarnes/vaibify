"""Standalone FastAPI app for the setup wizard (host-side, no Docker)."""

import os
import subprocess
import sys

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..config.projectConfig import fbValidateConfig


STATIC_DIRECTORY = os.path.join(os.path.dirname(__file__), "static")


class ValidateRequest(BaseModel):
    dictConfig: dict


class SaveRequest(BaseModel):
    sProjectDirectory: str
    dictConfig: dict


class BuildRequest(BaseModel):
    sProjectDirectory: str


def _fnRegisterReadRoutes(app):
    """Register template listing and validation routes."""

    @app.get("/api/setup/templates")
    async def fnListTemplates():
        return flistAvailableTemplates()

    @app.post("/api/setup/validate")
    async def fnValidate(request: ValidateRequest):
        return {"bValid": fbValidateConfig(request.dictConfig)}


def _fnRegisterWriteRoutes(app):
    """Register save and build routes."""

    @app.post("/api/setup/save")
    async def fnSave(request: SaveRequest):
        _fnValidateProjectDirectory(request.sProjectDirectory)
        fnWriteConfigToDirectory(
            request.sProjectDirectory, request.dictConfig
        )
        return {"bSuccess": True}

    @app.post("/api/setup/build")
    async def fnBuild(request: BuildRequest):
        _fnValidateProjectDirectory(request.sProjectDirectory)
        return fdictProcessBuild(request.sProjectDirectory)


def _fnRegisterIndexRoute(app):
    """Register the setup wizard index page."""

    @app.get("/")
    async def fnServeSetupIndex():
        sPath = os.path.join(STATIC_DIRECTORY, "setupWizard.html")
        if not os.path.isfile(sPath):
            raise HTTPException(404, "setupWizard.html not found")
        return FileResponse(sPath)


def _fnMountStaticFiles(app):
    """Mount the static directory if it exists."""
    if os.path.isdir(STATIC_DIRECTORY):
        app.mount(
            "/static",
            StaticFiles(directory=STATIC_DIRECTORY),
            name="static",
        )


def fappCreateSetupApplication():
    """Build and return the setup wizard FastAPI application."""
    app = FastAPI(title="Vaibify Setup Wizard")
    _fnRegisterReadRoutes(app)
    _fnRegisterWriteRoutes(app)
    _fnRegisterIndexRoute(app)
    _fnMountStaticFiles(app)
    return app


def _fnValidateProjectDirectory(sDirectory):
    """Reject directory paths that traverse outside home."""
    sNormalized = os.path.normpath(os.path.abspath(sDirectory))
    sHome = os.path.expanduser("~")
    if not sNormalized.startswith(sHome):
        raise HTTPException(
            403, "Project directory must be under home")


def flistAvailableTemplates():
    """Return template names shipped with the package."""
    from ..cli.commandInit import flistAvailableTemplates as flistGet
    return flistGet()


def fdictProcessBuild(sProjectDirectory):
    """Run build and return success dict or raise HTTPException."""
    iExitCode, sOutput = ftResultRunBuild(sProjectDirectory)
    if iExitCode != 0:
        sTruncated = sOutput[:500] if sOutput else ""
        raise HTTPException(
            500, f"Build failed (exit {iExitCode}): {sTruncated}"
        )
    return {"bSuccess": True, "sOutput": sOutput}


def fnWriteConfigToDirectory(sProjectDirectory, dictConfig):
    """Write a YAML config file into the specified project directory."""
    import yaml

    sConfigPath = os.path.join(sProjectDirectory, "vaibify.yml")
    os.makedirs(sProjectDirectory, exist_ok=True)
    with open(sConfigPath, "w") as fileHandle:
        yaml.safe_dump(
            dictConfig, fileHandle,
            default_flow_style=False, sort_keys=False,
        )


def ftResultRunBuild(sProjectDirectory):
    """Run vaibify build as a subprocess."""
    try:
        resultProcess = subprocess.run(
            [sys.executable, "-m", "vaibify", "build"],
            cwd=sProjectDirectory,
            capture_output=True, text=True, timeout=600,
        )
    except subprocess.TimeoutExpired:
        return (1, "Build timed out after 600 seconds")
    except FileNotFoundError:
        return (1, "Python interpreter not found")
    sOutput = resultProcess.stdout + resultProcess.stderr
    return (resultProcess.returncode, sOutput)
