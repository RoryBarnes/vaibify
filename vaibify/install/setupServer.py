"""FastAPI setup wizard for interactive project configuration.

Presents a web UI that walks the user through template selection,
project naming, feature toggles, and package lists.  Writes the
result to a vaibify.yml configuration file.

The wizard writes a file the next ``vaibify build`` trusts (base image,
repositories, packages), so it carries the same request guards as the
dashboard: the loopback ``Host:`` check, a per-browser credential
redeemed from a one-time launch capability, and the security headers.
It is the ONE setup wizard; an earlier second module with these guards
had no caller and was removed rather than kept beside it.

Save changes only what the page shows. Every other setting already in
``vaibify.yml`` (resource limits, ports, bind mounts, secrets, network
isolation, the dashboard port, Zenodo and LaTeX settings, a repository's
branch, the features the page has no checkbox for) is carried through
untouched; regenerating the file from the form's fields alone silently
deleted them. A file that exists but cannot be read is reported as that,
never as an empty configuration, and is overwritten only when the
researcher confirms it.
"""

import copy
import os
from pathlib import Path

import yaml
from fastapi import FastAPI, HTTPException, Request
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
from vaibify.gui import browserSession, serverMiddleware


_STATIC_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "gui", "static"
)

# The features the page has a checkbox for, and the agents whose
# auto-update choice it shows. Anything else in the file's features block
# belongs to the file, not to the form.
T_WIZARD_FEATURES = (
    "jupyter", "rLanguage", "julia", "database", "dvc", "latex",
    "claude", "codex", "antigravity", "opencode", "cline", "openhands",
    "pi", "gpu",
)
T_WIZARD_AUTO_UPDATE_FEATURES = (
    "claude", "codex", "antigravity", "opencode", "cline", "openhands",
    "pi",
)
T_WIZARD_SCALAR_KEYS = (
    "projectName", "containerUser", "pythonVersion", "baseImage",
    "workspaceRoot", "packageManager", "systemPackages", "pythonPackages",
    "neverSleep",
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
    bNeverSleep: bool = False
    bClaudeAutoUpdate: bool = True
    bCodexAutoUpdate: bool = True
    bGeminiAutoUpdate: bool = True
    bAntigravityAutoUpdate: bool = True
    bOpenCodeAutoUpdate: bool = True
    bClineAutoUpdate: bool = True
    bOpenHandsAutoUpdate: bool = True
    bPiAutoUpdate: bool = True
    bOverwriteUnreadable: bool = False


class ValidateResponse(BaseModel):
    bValid: bool
    listErrors: List[str] = []


def fappCreateSetupWizard(sOutputDirectory=".", iExpectedPort=0):
    """Build and return the setup wizard FastAPI application.

    ``iExpectedPort`` follows ``appFactory.fappCreateApplication``: a
    real bind port enables the strict loopback Host check, and 0 (the
    in-process test default) skips it.
    """
    app = FastAPI(title="Vaibify Setup Wizard")
    app.state.iExpectedPort = iExpectedPort
    app.state.dictBrowserSessions = (
        browserSession.fdictCreateBrowserSessionStore()
    )
    serverMiddleware.fnRegisterMiddleware(app)
    _fnRegisterRoutes(app, sOutputDirectory)
    _fnRegisterBootstrapRoute(app)
    _fnRegisterStaticFiles(app)
    return app


def _fnRegisterBootstrapRoute(app):
    """Register the one unauthenticated route: the capability exchange.

    The launch URL carries a one-time capability in its fragment; the
    page posts it here once for the per-browser credential every other
    ``/api`` route requires. There is no agent lane on this server, so
    unlike the dashboard's twin of this route it has no agent refusal
    to make.
    """

    @app.post("/api/bootstrap")
    async def fdictBootstrapSession(request: Request):
        try:
            dictBody = await request.json()
        except Exception:  # noqa: BLE001 -- a malformed body is just invalid
            dictBody = {}
        sSessionId, sCredential = browserSession.ftRedeemCapability(
            app.state.dictBrowserSessions,
            (dictBody or {}).get("sCapability", ""),
        )
        if not sCredential:
            raise HTTPException(
                status_code=401,
                detail="Invalid or expired bootstrap capability.",
            )
        return {"sSessionId": sSessionId, "sCredential": sCredential}


def _fnRegisterRoutes(app, sOutputDirectory):
    """Register all setup wizard API routes."""
    _fnRegisterTemplateRoutes(app)
    _fnRegisterConfigRoutes(app, sOutputDirectory)
    _fnRegisterBuildRoute(app, sOutputDirectory)


def _fnRegisterTemplateRoutes(app):
    """Register template listing and loading routes."""

    @app.get("/api/setup/templates")
    async def flistHandleGetTemplates():
        try:
            listNames = flistAvailableTemplates()
            return [
                {"sName": s, "sDescription": ""}
                for s in listNames
            ]
        except FileNotFoundError:
            return []

    @app.get("/api/setup/templates/{sTemplateName}")
    async def fdictHandleGetTemplateConfig(sTemplateName: str):
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
    async def fdictGetExistingConfig():
        sPath = str(Path(sOutputDirectory) / "vaibify.yml")
        if not Path(sPath).is_file():
            return {}
        try:
            config = fconfigLoadFromFile(sPath)
        except Exception as error:
            raise HTTPException(422, {"sMessage": (
                "vaibify.yml exists here but could not be loaded, so the "
                f"form below is NOT your configuration: {error}"
            )})
        return _fdictConfigToWizardFormat(config)

    @app.get("/api/setup/defaults")
    async def fdictGetDefaults():
        return fdictLoadDefaults()

    @app.post(
        "/api/setup/validate", response_model=ValidateResponse
    )
    async def fresponseValidateConfig(request: WizardConfigRequest):
        listErrors = _flistCollectErrors(request)
        return ValidateResponse(
            bValid=len(listErrors) == 0,
            listErrors=listErrors,
        )

    @app.post("/api/setup/save")
    async def fdictSaveConfig(request: WizardConfigRequest):
        listErrors = _flistCollectErrors(request)
        if listErrors:
            raise HTTPException(
                400, {"listErrors": listErrors}
            )
        sFilePath = str(
            Path(sOutputDirectory) / "vaibify.yml"
        )
        dictContent = _fdictBuildFileContent(request, sFilePath)
        _fnWriteYamlConfig(dictContent, sFilePath)
        return {"sFilePath": sFilePath, "bSuccess": True}


def _fnRegisterBuildRoute(app, sOutputDirectory):
    """Register the build route."""

    @app.post("/api/setup/build")
    async def fdictHandleBuildContainer(request: WizardConfigRequest):
        listErrors = _flistCollectErrors(request)
        if listErrors:
            raise HTTPException(
                400, {"listErrors": listErrors}
            )
        sFilePath = str(
            Path(sOutputDirectory) / "vaibify.yml"
        )
        dictContent = _fdictBuildFileContent(request, sFilePath)
        _fnWriteYamlConfig(dictContent, sFilePath)
        return {
            "sMessage": "Configuration saved. "
            "Run 'vaibify build' to build the container.",
            "bSuccess": True,
        }


def _fnRegisterStaticFiles(app):
    """Serve the setup wizard HTML and static assets."""

    @app.get("/")
    async def fresponseHandleServeIndex():
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
        "bClaudeAutoUpdate": config.features.bClaudeAutoUpdate,
        "bCodexAutoUpdate": config.features.bCodexAutoUpdate,
        "bGeminiAutoUpdate": config.features.bGeminiAutoUpdate,
        "bAntigravityAutoUpdate": config.features.bAntigravityAutoUpdate,
        "bOpenCodeAutoUpdate": getattr(
            config.features, "bOpenCodeAutoUpdate", True,
        ),
        "bClineAutoUpdate": getattr(
            config.features, "bClineAutoUpdate", True,
        ),
        "bOpenHandsAutoUpdate": getattr(
            config.features, "bOpenHandsAutoUpdate", True,
        ),
        "bPiAutoUpdate": getattr(config.features, "bPiAutoUpdate", True),
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
        "codex": features.bCodex,
        "gemini": features.bGemini,
        "antigravity": features.bAntigravity,
        "opencode": getattr(features, "bOpenCode", False),
        "cline": getattr(features, "bCline", False),
        "openhands": getattr(features, "bOpenHands", False),
        "pi": getattr(features, "bPi", False),
        "gpu": features.bGpu,
    }
    return [s for s, b in dictMap.items() if b]


def _fdictWizardToYaml(request, listExistingRepositories=None):
    """Convert wizard form data to vaibify.yml-compatible dict."""
    dictFeatures = _fdictFeaturesFromList(request.listFeatures)
    dictFeatures["claudeAutoUpdate"] = request.bClaudeAutoUpdate
    dictFeatures["codexAutoUpdate"] = request.bCodexAutoUpdate
    dictFeatures["geminiAutoUpdate"] = request.bGeminiAutoUpdate
    dictFeatures["antigravityAutoUpdate"] = request.bAntigravityAutoUpdate
    dictFeatures["opencodeAutoUpdate"] = request.bOpenCodeAutoUpdate
    dictFeatures["clineAutoUpdate"] = request.bClineAutoUpdate
    dictFeatures["openhandsAutoUpdate"] = request.bOpenHandsAutoUpdate
    dictFeatures["piAutoUpdate"] = request.bPiAutoUpdate
    listRepos = _flistReposFromUrls(
        request.listRepositories, listExistingRepositories,
    )
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


def _fdictBuildFileContent(request, sFilePath):
    """Return what Save writes: the form laid over what the file already holds.

    Raises
    ------
    HTTPException
        409 when the file exists but cannot be read as a mapping and the
        request has not confirmed overwriting it.
    """
    dictExisting = _fdictReadExistingFile(sFilePath)
    if dictExisting is None and not request.bOverwriteUnreadable:
        raise HTTPException(409, {
            "sMessage": (
                "vaibify.yml exists here but cannot be read, so Save "
                "would replace it with only what this form shows."
            ),
            "bNeedsOverwriteConfirmation": True,
        })
    dictWizard = _fdictWizardToYaml(
        request, (dictExisting or {}).get("repositories"),
    )
    if not dictExisting:
        return dictWizard
    return _fdictMergeWizardOverExisting(dictExisting, dictWizard)


def _fdictReadExistingFile(sFilePath):
    """Return the file's mapping, ``{}`` when absent, ``None`` when unreadable."""
    if not Path(sFilePath).is_file():
        return {}
    try:
        with open(sFilePath, "r", encoding="utf-8") as fileHandle:
            dictRaw = yaml.safe_load(fileHandle)
    except (OSError, yaml.YAMLError):
        return None
    if dictRaw is None:
        return {}
    return dictRaw if isinstance(dictRaw, dict) else None


def _fdictMergeWizardOverExisting(dictExisting, dictWizard):
    """Overlay the form's own keys on the file's content, keeping the rest."""
    dictMerged = copy.deepcopy(dictExisting)
    for sKey in T_WIZARD_SCALAR_KEYS:
        dictMerged[sKey] = dictWizard[sKey]
    dictMerged["repositories"] = dictWizard["repositories"]
    dictMerged["features"] = _fdictMergeFeatures(
        dictExisting.get("features"), dictWizard["features"],
    )
    _fnMergeOverleafProjectId(dictMerged, dictWizard)
    return dictMerged


def _fdictMergeFeatures(dictExisting, dictWizard):
    """Set the features the page shows; leave every other feature as it was."""
    dictMerged = copy.deepcopy(dictExisting) if isinstance(
        dictExisting, dict) else {}
    for sName in T_WIZARD_FEATURES:
        dictMerged[sName] = dictWizard[sName]
    for sName in T_WIZARD_AUTO_UPDATE_FEATURES:
        sKey = sName + "AutoUpdate"
        dictMerged[sKey] = dictWizard[sKey]
    return dictMerged


def _fnMergeOverleafProjectId(dictMerged, dictWizard):
    """Write the form's Overleaf id into the file's reproducibility block."""
    dictWizardOverleaf = dictWizard.get("reproducibility", {}).get(
        "overleaf", {})
    dictRepro = dictMerged.get("reproducibility")
    dictOverleaf = (
        dictRepro.get("overleaf") if isinstance(dictRepro, dict) else None)
    if not dictWizardOverleaf and not isinstance(dictOverleaf, dict):
        return
    if not isinstance(dictRepro, dict):
        dictRepro = dictMerged["reproducibility"] = {}
    if not isinstance(dictOverleaf, dict):
        dictOverleaf = dictRepro["overleaf"] = {}
    dictOverleaf["projectId"] = dictWizardOverleaf.get("projectId", "")


def _fdictFeaturesFromList(listFeatures):
    """Convert a list of feature name strings to a bool dict."""
    listAllFeatures = [
        "jupyter", "rLanguage", "julia", "database",
        "dvc", "latex", "claude", "codex", "gemini", "antigravity",
        "opencode", "cline", "openhands", "pi", "gpu",
    ]
    return {s: s in listFeatures for s in listAllFeatures}


def _flistReposFromUrls(listUrls, listExistingRepositories=None):
    """Return vaibify.yml repository entries, through the one authority.

    A URL the file already lists keeps its entry as written (branch,
    install method, destination): asking the remote again would replace
    a branch the researcher chose with the remote's default, and costs a
    network round trip per repository on every Save.
    """
    from vaibify.cli.repositoryPreflight import flistRepositoryEntriesFromUrls
    dictExistingByUrl = {
        dictEntry.get("url"): dictEntry
        for dictEntry in (listExistingRepositories or [])
        if isinstance(dictEntry, dict)
    }
    return [
        copy.deepcopy(dictExistingByUrl[sUrl]) if sUrl in dictExistingByUrl
        else flistRepositoryEntriesFromUrls([sUrl])[0]
        for sUrl in listUrls
    ]


def _flistCollectErrors(request):
    """Return a list of validation error strings.

    The container-identity fields are graded by the same function the
    build preflight uses: the request carries the same attribute names
    as the config, and a wizard that wrote a value the build then
    refuses would only move the discovery later.
    """
    from vaibify.cli.configFieldPreflight import flistDescribeInvalidFields
    listErrors = []
    if not request.sProjectName.strip():
        listErrors.append("projectName is required")
    if request.sPackageManager not in ("pip", "conda", "mamba"):
        listErrors.append(
            f"Invalid packageManager: '{request.sPackageManager}'"
        )
    listErrors.extend(flistDescribeInvalidFields(request))
    return listErrors


def _fnWriteYamlConfig(dictConfig, sFilePath):
    """Write a configuration dict to YAML."""
    pathOutput = Path(sFilePath)
    pathOutput.parent.mkdir(parents=True, exist_ok=True)
    with open(pathOutput, "w", encoding="utf-8") as fileHandle:
        yaml.safe_dump(
            dictConfig, fileHandle,
            default_flow_style=False, sort_keys=False,
        )
