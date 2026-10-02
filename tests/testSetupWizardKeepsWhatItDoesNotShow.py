"""Save writes what the form shows and keeps everything else in vaibify.yml.

The wizard regenerated the whole file from its own fields, so a Save on an
existing project silently deleted the network isolation, the resource
limits, the dashboard port, the Zenodo and LaTeX settings, a repository's
branch and install method, and every feature the page has no checkbox
for. The load route also answered ``{}`` for a file it could not load, so
an unreadable configuration rendered as an empty one and the next Save
replaced it. These drive the real application with a real credential.
"""

import dataclasses
import os
from unittest.mock import patch

import pytest
import yaml
from fastapi.testclient import TestClient

from tests.sessionTokenTestHelper import fsBootstrapCredential
from vaibify.config import projectConfig
from vaibify.install import setupServer
from vaibify.install.setupServer import fappCreateSetupWizard

S_REPOSITORY_URL = "https://github.com/example/analysis.git"
LIST_AGENTS_WITH_A_PAGE_CHECKBOX = (
    "Claude", "Codex", "Antigravity", "OpenCode", "Cline", "OpenHands", "Pi",
)


def _fclientFor(tmp_path):
    app = fappCreateSetupWizard(sOutputDirectory=str(tmp_path))
    return TestClient(
        app, headers={"X-Session-Token": fsBootstrapCredential(app)})


def _fdictEveryKeyConfigured(tmp_path, monkeypatch):
    """A config with a non-default value in every field, as camelCase YAML."""
    pathHome = tmp_path / "home"
    (pathHome / "datasets").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(pathHome))
    return {
        "projectName": "everyKey",
        "containerUser": "analyst",
        "pythonVersion": "3.11",
        "baseImage": "ubuntu:22.04",
        "workspaceRoot": "/work",
        "packageManager": "conda",
        "repositories": [{
            "name": "analysis", "url": S_REPOSITORY_URL,
            "branch": "release-2.0", "installMethod": "pip_no_deps",
            "destination": "src/analysis",
        }],
        "systemPackages": ["gcc", "libhdf5-dev"],
        "pythonPackages": ["numpy"],
        "pipInstallFlags": "--no-cache-dir",
        "features": {
            "jupyter": True, "rLanguage": True, "julia": True,
            "database": True, "dvc": True, "nestedSampling": True,
            "latex": False, "claude": True, "claudeAutoUpdate": False,
            "codex": True, "codexAutoUpdate": False, "gemini": True,
            "geminiAutoUpdate": False, "antigravity": True,
            "antigravityAutoUpdate": False, "opencode": True,
            "opencodeAutoUpdate": False, "cline": True,
            "clineAutoUpdate": False, "openhands": True,
            "openhandsAutoUpdate": False, "pi": True,
            "piAutoUpdate": False, "gpu": True,
        },
        "binaries": [{"name": "solver", "path": "/opt/solver/bin/solver"}],
        "ports": [{"container": 8888, "host": 8899}],
        "bindMounts": [{
            "host": str(pathHome / "datasets"), "container": "/data",
            "readOnly": True,
        }],
        "secrets": [{"name": "api_token", "method": "keyring"}],
        "reproducibility": {
            "zenodoService": "production",
            "latexRoot": "paper",
            "figuresRoot": "paper/figs",
            "overleaf": {
                "projectId": "abc123", "figureDirectory": "plots",
                "pullPaths": ["main.tex"],
            },
        },
        "networkIsolation": True,
        "neverSleep": True,
        "dashboardPort": 8123,
        "cpuLimit": 3,
        "memoryLimitGigabytes": 6.5,
    }


def _fnWriteConfig(tmp_path, dictYaml):
    config = projectConfig.fconfigFromYamlDict(dictYaml)
    assert projectConfig.fbValidateConfig(
        projectConfig._fdictConfigToYamlDict(config)), "fixture is invalid"
    projectConfig.fnSaveToFile(config, str(tmp_path / "vaibify.yml"))
    return config


def _fdictSavedByThePage(dictLoaded):
    """What scriptSetupWizard.js posts after loading ``dictLoaded``."""
    dictPosted = {
        sKey: dictLoaded[sKey] for sKey in (
            "sProjectName", "sContainerUser", "sPythonVersion", "sBaseImage",
            "sWorkspaceRoot", "sPackageManager", "listRepositories",
            "listPipPackages", "listAptPackages", "sOverleafProjectId",
            "bNeverSleep")
    }
    dictPosted["listFeatures"] = [
        sFeature for sFeature in dictLoaded["listFeatures"]
        if sFeature in setupServer.T_WIZARD_FEATURES]
    for sAgent in LIST_AGENTS_WITH_A_PAGE_CHECKBOX:
        sField = f"b{sAgent}AutoUpdate"
        dictPosted[sField] = dictLoaded[sField]
    return dictPosted


# The validator refuses conda packages (nothing installs them), so no
# loadable configuration can hold a non-default value there. X11
# forwarding is refused beside network isolation, which this fixture
# turns on; its own round trip is the test below.
SET_FIELDS_NO_VALID_CONFIG_CAN_MOVE = {"listCondaPackages", "bX11Forwarding"}


def _fnAssertEveryFieldIsNonDefault(config):
    """The fixture must move EVERY field, so a new field cannot hide."""
    for configPart in (
        config, config.features, config.reproducibility,
        config.reproducibility.overleaf,
    ):
        configDefault = type(configPart)()
        for fieldDeclared in dataclasses.fields(configPart):
            sName = fieldDeclared.name
            if sName in SET_FIELDS_NO_VALID_CONFIG_CAN_MOVE:
                continue
            assert getattr(configPart, sName) != getattr(
                configDefault, sName), (
                f"{type(configPart).__name__}.{sName} still holds its "
                "default in the round-trip fixture")


@pytest.mark.falsification
def testSavingThroughTheWizardKeepsEveryKeyItDoesNotShow(
    tmp_path, monkeypatch,
):
    """Kills: regenerating vaibify.yml from the form's own fields alone."""
    dictYaml = _fdictEveryKeyConfigured(tmp_path, monkeypatch)
    configBefore = _fnWriteConfig(tmp_path, dictYaml)
    _fnAssertEveryFieldIsNonDefault(configBefore)
    clientHttp = _fclientFor(tmp_path)
    dictLoaded = clientHttp.get("/api/setup/config").json()
    responseSave = clientHttp.post(
        "/api/setup/save", json=_fdictSavedByThePage(dictLoaded))
    assert responseSave.status_code == 200, responseSave.text
    configAfter = projectConfig.fconfigLoadFromFile(
        str(tmp_path / "vaibify.yml"))
    assert dataclasses.asdict(configAfter) == dataclasses.asdict(
        configBefore)


@pytest.mark.falsification
def testSavingThroughTheWizardKeepsTheX11ForwardingOptIn(
    tmp_path, monkeypatch,
):
    """Kills: dropping x11Forwarding when the form regenerates the file."""
    dictYaml = _fdictEveryKeyConfigured(tmp_path, monkeypatch)
    dictYaml["networkIsolation"] = False
    dictYaml["x11Forwarding"] = True
    configBefore = _fnWriteConfig(tmp_path, dictYaml)
    assert configBefore.bX11Forwarding is True
    clientHttp = _fclientFor(tmp_path)
    dictLoaded = clientHttp.get("/api/setup/config").json()
    assert clientHttp.post(
        "/api/setup/save", json=_fdictSavedByThePage(dictLoaded),
    ).status_code == 200
    with open(tmp_path / "vaibify.yml", encoding="utf-8") as fileHandle:
        assert yaml.safe_load(fileHandle)["x11Forwarding"] is True
    configAfter = projectConfig.fconfigLoadFromFile(
        str(tmp_path / "vaibify.yml"))
    assert dataclasses.asdict(configAfter) == dataclasses.asdict(
        configBefore)


def testTheFormStillControlsTheFeaturesItShows(tmp_path, monkeypatch):
    dictYaml = _fdictEveryKeyConfigured(tmp_path, monkeypatch)
    _fnWriteConfig(tmp_path, dictYaml)
    clientHttp = _fclientFor(tmp_path)
    dictPosted = _fdictSavedByThePage(
        clientHttp.get("/api/setup/config").json())
    dictPosted["listFeatures"] = [
        sFeature for sFeature in dictPosted["listFeatures"]
        if sFeature != "jupyter"]
    dictPosted["bClaudeAutoUpdate"] = True
    assert clientHttp.post(
        "/api/setup/save", json=dictPosted).status_code == 200
    with open(tmp_path / "vaibify.yml", encoding="utf-8") as fileHandle:
        dictFeatures = yaml.safe_load(fileHandle)["features"]
    assert dictFeatures["jupyter"] is False
    assert dictFeatures["claudeAutoUpdate"] is True
    assert dictFeatures["gemini"] is True
    assert dictFeatures["nestedSampling"] is True


def testTheOverleafIdIsTheFormsToSetAndToClear(tmp_path, monkeypatch):
    dictYaml = _fdictEveryKeyConfigured(tmp_path, monkeypatch)
    _fnWriteConfig(tmp_path, dictYaml)
    clientHttp = _fclientFor(tmp_path)
    dictPosted = _fdictSavedByThePage(
        clientHttp.get("/api/setup/config").json())
    dictPosted["sOverleafProjectId"] = ""
    assert clientHttp.post(
        "/api/setup/save", json=dictPosted).status_code == 200
    with open(tmp_path / "vaibify.yml", encoding="utf-8") as fileHandle:
        dictOverleaf = yaml.safe_load(fileHandle)[
            "reproducibility"]["overleaf"]
    assert dictOverleaf["projectId"] == ""
    assert dictOverleaf["figureDirectory"] == "plots"
    assert dictOverleaf["pullPaths"] == ["main.tex"]


@pytest.mark.falsification
def testASavedRepositoryKeepsItsBranchWithoutAskingTheRemote(
    tmp_path, monkeypatch,
):
    """Kills: re-resolving a listed repository, replacing its chosen branch."""
    dictYaml = _fdictEveryKeyConfigured(tmp_path, monkeypatch)
    _fnWriteConfig(tmp_path, dictYaml)
    clientHttp = _fclientFor(tmp_path)
    dictPosted = _fdictSavedByThePage(
        clientHttp.get("/api/setup/config").json())

    def fsAskTheRemote(sUrl):
        raise AssertionError(f"the remote was asked about {sUrl}")

    with patch(
        "vaibify.cli.repositoryPreflight.fsDefaultBranchOfRemote",
        fsAskTheRemote,
    ):
        assert clientHttp.post(
            "/api/setup/save", json=dictPosted).status_code == 200
    with open(tmp_path / "vaibify.yml", encoding="utf-8") as fileHandle:
        dictRepository = yaml.safe_load(fileHandle)["repositories"][0]
    assert dictRepository == dictYaml["repositories"][0]


def testANewRepositoryIsResolvedAndTheOldOneKept(tmp_path, monkeypatch):
    dictYaml = _fdictEveryKeyConfigured(tmp_path, monkeypatch)
    _fnWriteConfig(tmp_path, dictYaml)
    clientHttp = _fclientFor(tmp_path)
    dictPosted = _fdictSavedByThePage(
        clientHttp.get("/api/setup/config").json())
    sNewUrl = "https://github.com/example/other.git"
    dictPosted["listRepositories"].append(sNewUrl)
    dictResolved = {"name": "other", "url": sNewUrl, "branch": "trunk",
                    "installMethod": "reference"}
    with patch(
        "vaibify.cli.repositoryPreflight.flistRepositoryEntriesFromUrls",
        return_value=[dictResolved],
    ) as mockResolve:
        assert clientHttp.post(
            "/api/setup/save", json=dictPosted).status_code == 200
    mockResolve.assert_called_once_with([sNewUrl])
    with open(tmp_path / "vaibify.yml", encoding="utf-8") as fileHandle:
        listRepositories = yaml.safe_load(fileHandle)["repositories"]
    assert listRepositories == [dictYaml["repositories"][0], dictResolved]


@pytest.mark.falsification
def testAConfigThatCannotBeLoadedIsReportedNotRenderedAsEmpty(tmp_path):
    """Kills: answering {} for a file the loader refused."""
    (tmp_path / "vaibify.yml").write_text(
        "projectName: broken\npackageManager: nonsense\n", encoding="utf-8")
    responseHttp = _fclientFor(tmp_path).get("/api/setup/config")
    assert responseHttp.status_code == 422
    sMessage = responseHttp.json()["detail"]["sMessage"]
    assert "could not be loaded" in sMessage
    assert "NOT your configuration" in sMessage


def testAFileThatDoesNotExistStillLoadsAsEmpty(tmp_path):
    responseHttp = _fclientFor(tmp_path).get("/api/setup/config")
    assert responseHttp.status_code == 200
    assert responseHttp.json() == {}


@pytest.mark.falsification
def testSavingOverUnreadableYamlNeedsTheResearchersConfirmation(tmp_path):
    """Kills: replacing a file Save could not read without being told to."""
    sUnreadable = "projectName: [unclosed\nneverSleep: true\n"
    pathConfig = tmp_path / "vaibify.yml"
    pathConfig.write_text(sUnreadable, encoding="utf-8")
    clientHttp = _fclientFor(tmp_path)
    dictPosted = {"sProjectName": "fresh", "sPackageManager": "pip"}
    responseRefused = clientHttp.post("/api/setup/save", json=dictPosted)
    assert responseRefused.status_code == 409
    assert responseRefused.json()["detail"]["bNeedsOverwriteConfirmation"]
    assert pathConfig.read_text(encoding="utf-8") == sUnreadable
    responseConfirmed = clientHttp.post(
        "/api/setup/save", json={**dictPosted, "bOverwriteUnreadable": True})
    assert responseConfirmed.status_code == 200
    assert yaml.safe_load(pathConfig.read_text(encoding="utf-8"))[
        "projectName"] == "fresh"


def testAFreshProjectSavesTheCompleteFeatureBlockAsBefore(tmp_path):
    clientHttp = _fclientFor(tmp_path)
    assert clientHttp.post("/api/setup/save", json={
        "sProjectName": "brandNew", "sPackageManager": "pip",
        "listFeatures": ["latex"],
    }).status_code == 200
    with open(tmp_path / "vaibify.yml", encoding="utf-8") as fileHandle:
        dictSaved = yaml.safe_load(fileHandle)
    assert dictSaved["features"]["latex"] is True
    assert dictSaved["features"]["gemini"] is False
    assert os.path.getsize(tmp_path / "vaibify.yml") > 0
