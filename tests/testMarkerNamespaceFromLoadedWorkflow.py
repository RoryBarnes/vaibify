"""A loaded workflow must name the marker namespace its tests write under.

Six readers computed the namespace from ``dictWorkflow["sPath"]``, a key
no loader ever set (the loader records ``_sLoadedFromPath``), and every
test fixture injected ``"sPath"`` by hand -- which is why the suite was
green while a real project got no marker namespace at all: no
``VAIBIFY_ACTIVE_WORKFLOW_SLUG`` reached pytest, a fresh clone never
bootstrapped from markers, and two projects in one repository wrote the
same marker files over each other.

Every workflow here comes from the REAL loader. None carries an
injected ``"sPath"``.
"""

import asyncio
import json
from unittest.mock import MagicMock

import pytest

from vaibify.gui import determinismEnvironment
from vaibify.gui import stateManager
from vaibify.gui import workflowManager
from vaibify.gui.routes import testRoutes

S_REPO = "/workspace/sharedRepo"
S_PATH_ALPHA = S_REPO + "/.vaibify/projects/alpha.json"
S_PATH_BETA = S_REPO + "/.vaibify/projects/beta.json"


def _fdictLoadFromContainer(sWorkflowPath):
    """Load a one-step workflow through the real loader, as the hub does."""
    mockDocker = MagicMock()
    mockDocker.fbaFetchFile.return_value = json.dumps({
        "sPlotDirectory": "Plot",
        "listSteps": [{
            "sName": "Step", "sDirectory": "stepDir",
            "saPlotCommands": ["echo"], "saPlotFiles": ["f.pdf"],
        }],
    }).encode("utf-8")
    return workflowManager.fdictLoadWorkflowFromContainer(
        mockDocker, "containerId", sWorkflowPath,
    )


def testALoadedWorkflowNamesTheFileItCameFrom():
    dictLoaded = _fdictLoadFromContainer(S_PATH_ALPHA)
    assert workflowManager.fsWorkflowLoadedFromPath(dictLoaded) == (
        S_PATH_ALPHA)
    assert workflowManager.fsWorkflowLoadedFromPath({}) == ""
    assert workflowManager.fsWorkflowLoadedFromPath(None) == ""


def testNoReaderInventsAKeyNoLoaderSets():
    dictLoaded = _fdictLoadFromContainer(S_PATH_ALPHA)
    assert "sPath" not in dictLoaded


@pytest.mark.falsification
def testTheMarkerNamespaceReachesPytestForALoadedWorkflow():
    """Kills: testRoutes._fsBuildCategoryCommand reading ``dictWorkflow.get("sPath", "")``.

    The command that runs a step's tests must export the workflow's
    slug, or the marker conftest writes outside any namespace.
    """
    dictLoaded = _fdictLoadFromContainer(S_PATH_ALPHA)
    sCommand = testRoutes._fsBuildCategoryCommand(
        dictLoaded["listSteps"][0], dictLoaded, ["pytest"],
    )
    assert "VAIBIFY_ACTIVE_WORKFLOW_SLUG='alpha'" in sCommand


def testTwoProjectsInOneRepositoryGetDifferentMarkerNamespaces():
    """The slug is the workflow file's own name, so markers cannot collide."""
    dictAlpha = _fdictLoadFromContainer(S_PATH_ALPHA)
    dictBeta = _fdictLoadFromContainer(S_PATH_BETA)
    sAlpha = testRoutes._fsBuildCategoryCommand(
        dictAlpha["listSteps"][0], dictAlpha, ["pytest"])
    sBeta = testRoutes._fsBuildCategoryCommand(
        dictBeta["listSteps"][0], dictBeta, ["pytest"])
    assert "VAIBIFY_ACTIVE_WORKFLOW_SLUG='alpha'" in sAlpha
    assert "VAIBIFY_ACTIVE_WORKFLOW_SLUG='beta'" in sBeta
    assert "beta" not in sAlpha and "alpha" not in sBeta


@pytest.mark.falsification
def testTheRunnerExportsTheNamespaceForALoadedWorkflow():
    """Kills: determinismEnvironment reading ``dictWorkflow.get("sPath", "")``."""
    dictLoaded = _fdictLoadFromContainer(S_PATH_ALPHA)
    dictLoaded["sProjectRepoPath"] = S_REPO
    dictVariables = {}
    mockDocker = MagicMock()
    mockDocker.ftResultExecuteCommand.return_value = (1, "")
    asyncio.run(determinismEnvironment._fnInjectDeterminismEnvPrefix(
        mockDocker, "containerId", dictLoaded, dictVariables,
    ))
    assert "VAIBIFY_ACTIVE_WORKFLOW_SLUG='alpha'" in dictVariables[
        determinismEnvironment.S_ENV_PREFIX_KEY
    ]


@pytest.mark.falsification
def testAFreshCloneBootstrapsFromMarkersOfItsOwnWorkflow(monkeypatch):
    """Kills: stateManager's marker bootstrap reading ``dictWorkflow.get("sPath", "")``.

    Without a slug the bootstrap returns an empty state, so a fresh
    clone never learns that its markers matched.
    """
    dictLoaded = _fdictLoadFromContainer(S_PATH_ALPHA)
    listSlugsAsked = []

    def flistRecordSlug(connectionDocker, sContainerId, sRepo, sSlug, list_):
        listSlugsAsked.append(sSlug)
        return []

    monkeypatch.setattr(stateManager, "_flistFetchMarkers", flistRecordSlug)
    stateManager.fdictBootstrapStateFromMarkers(
        MagicMock(), "containerId", dictLoaded, S_REPO,
    )
    assert listSlugsAsked == ["alpha"]


@pytest.mark.falsification
def testThePollReadsTheMarkersOfTheWorkflowItDescribes(monkeypatch):
    """Kills: pipelineRoutes._fdictLoadMarkersForPoll reading ``dictWorkflow.get("sPath", "")``."""
    from vaibify.gui.routes import pipelineRoutes
    dictLoaded = _fdictLoadFromContainer(S_PATH_BETA)
    dictLoaded["sProjectRepoPath"] = S_REPO
    listSlugsAsked = []

    def flistRecordSlug(connectionDocker, sContainerId, sRepo, sSlug, list_):
        listSlugsAsked.append(sSlug)
        return []

    monkeypatch.setattr(stateManager, "_flistFetchMarkers", flistRecordSlug)
    pipelineRoutes._fdictLoadMarkersForPoll(
        {"docker": MagicMock()}, "containerId", dictLoaded,
    )
    assert listSlugsAsked == ["beta"]


def testThePollMapsAMarkerOfALoadedWorkflowOntoItsStep():
    """The marker lane end to end: slug -> path -> file -> step index.

    Before the namespace existed this lane returned early on an empty
    slug, so the shape mismatch between the marker fetch (step dict,
    marker) and the mapper (directory, marker) was never reached.
    """
    from vaibify.gui.routes import pipelineRoutes
    dictLoaded = _fdictLoadFromContainer(S_PATH_BETA)
    dictLoaded["sProjectRepoPath"] = S_REPO
    dictMarker = {"dictOutputHashes": {"stepDir/f.pdf": "abc"}}
    mockDocker = MagicMock()

    def fbaFetchOrNone(sContainerId, sPath):
        if sPath == S_REPO + "/.vaibify/test_markers/beta/stepDir.json":
            return json.dumps(dictMarker).encode("utf-8")
        return None

    mockDocker.fdictFetchSmallFiles.side_effect = (
        lambda sContainerId, listPaths: {
            sPath: fbaFetchOrNone(sContainerId, sPath)
            for sPath in listPaths
        }
    )
    assert pipelineRoutes._fdictLoadMarkersForPoll(
        {"docker": mockDocker}, "containerId", dictLoaded,
    ) == {0: dictMarker}
