"""Standalone FastAPI app for the setup wizard (host-side, no Docker)."""

__all__ = [
    "ValidateRequest",
    "SaveRequest",
    "BuildRequest",
    "fappCreateSetupApplication",
    "flistAvailableTemplates",
    "fdictProcessBuild",
    "fnWriteConfigToDirectory",
    "ftResultRunBuild",
]

import os
import secrets
import subprocess
import sys

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import serverMiddleware
from ..config.projectConfig import (
    fbValidateConfig,
    fconfigFromYamlDict,
    fnSaveToFile,
)


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
    async def flistListTemplates():
        return flistAvailableTemplates()

    @app.post("/api/setup/validate")
    async def fdictHandleValidate(request: ValidateRequest):
        return {"bValid": fbValidateConfig(request.dictConfig)}


def _fnRegisterWriteRoutes(app):
    """Register save and build routes."""

    @app.post("/api/setup/save")
    async def fdictHandleSave(request: SaveRequest):
        _fnValidateProjectDirectory(request.sProjectDirectory)
        fnWriteConfigToDirectory(
            request.sProjectDirectory, request.dictConfig
        )
        return {"bSuccess": True}

    @app.post("/api/setup/build")
    async def fdictHandleBuild(request: BuildRequest):
        _fnValidateProjectDirectory(request.sProjectDirectory)
        return fdictProcessBuild(request.sProjectDirectory)


def _fnRegisterSessionTokenRoute(app, sSessionToken):
    """Register the token endpoint the wizard page authenticates with.

    Same model as the main app: the token is readable only by a page
    the browser loaded from this origin, so a cross-origin page cannot
    obtain it and every other ``/api`` route stays unreachable to it.
    """

    @app.get("/api/session-token")
    async def fdictGetSessionToken():
        return {"sToken": sSessionToken}


def _fnRegisterIndexRoute(app):
    """Register the setup wizard index page."""

    @app.get("/")
    async def fresponseServeSetupIndex():
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


def fappCreateSetupApplication(iExpectedPort=0):
    """Build and return the setup wizard FastAPI application.

    The wizard writes YAML into a caller-named host directory and
    spawns a build there, so it carries the same session-token and
    ``Host:`` header guards as the viewer app. ``iExpectedPort``
    follows ``appFactory.fappCreateApplication``: a real bind port
    enables the strict Host check, and 0 (the test default) skips it.
    """
    app = FastAPI(title="Vaibify Setup Wizard")
    app.state.sSessionToken = secrets.token_urlsafe(32)
    app.state.iExpectedPort = iExpectedPort
    serverMiddleware.fnRegisterMiddleware(app)
    _fnRegisterReadRoutes(app)
    _fnRegisterWriteRoutes(app)
    _fnRegisterSessionTokenRoute(app, app.state.sSessionToken)
    _fnRegisterIndexRoute(app)
    _fnMountStaticFiles(app)
    return app


def _fnValidateProjectDirectory(sDirectory):
    """Reject directory paths that resolve outside the home tree.

    ``realpath`` plus the separator are both load-bearing: a bare
    prefix test admits ``/home/researcherBackup`` when home is
    ``/home/researcher``, and an unresolved path admits a symlink
    under home that points anywhere on the host.
    """
    if not os.path.isabs(sDirectory):
        raise HTTPException(400, "Directory must be an absolute path")
    sHome = os.path.expanduser("~")
    sResolved = os.path.realpath(sDirectory)
    if sResolved != sHome and not sResolved.startswith(sHome + os.sep):
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
    """Write a validated YAML config file into the project directory.

    ``ProjectConfig`` is a plain dataclass with no coercion, so an
    unvalidated dict persists verbatim -- ``{"cpuLimit": "8"}`` lands
    in vaibify.yml as a string and only surfaces later as a broken
    ``docker run`` argument. Validation is enforced here rather than
    in the route so no writer can bypass it.
    """
    if not fbValidateConfig(dictConfig):
        raise HTTPException(
            400, "Configuration failed validation; not written")
    sConfigPath = os.path.join(sProjectDirectory, "vaibify.yml")
    os.makedirs(sProjectDirectory, exist_ok=True)
    configProject = fconfigFromYamlDict(dictConfig)
    fnSaveToFile(configProject, sConfigPath)


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
