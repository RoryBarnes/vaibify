"""The container's marker plugin hashes a templated plot under the host's key.

The marker plugin runs inside the container and cannot import vaibify,
so it resolves ``{sPlotDirectory}`` and ``{sFigureType}`` with its own
mirror of the host resolver, against the container's repo root. A
parity test holds the two together on the host. Only a real container
shows that the plugin, running where the files are, writes the plot's
digest under the key the host poll judges it by. Then a poll that sees
the plot newer than the run with identical bytes keeps the tests
passed.

Runs the real plugin under pytest in the container, then the real poll
(both halves, ``fdictComputeFileStatus``) over the real connection,
with the variables the hub builds for the workflow. It asserts the
poll measured the plot's mtime, so the "identical bytes, newer mtime"
case cannot pass by never seeing the plot at all.

Skipped when no daemon answers, unless ``VAIBIFY_REQUIRE_DOCKER_DAEMON``
demands one (see ``tests/testDockerConnectionLive.py``).
"""

import asyncio
import json
import time
from unittest.mock import MagicMock

import pytest

from tests.testConfinedWriteLive import (  # noqa: F401  (fixture)
    S_PROJECT,
    _fsRunAsUser,
    liveContainer,
)
from vaibify.gui import conftestManager, workflowManager
from vaibify.gui.fileStatusManager import fsStepFileRepoRelative
from vaibify.gui.routes import pipelineRoutes

pytestmark = pytest.mark.docker_live

S_STEP_DIRECTORY = "PlotStep"
S_SLUG = "templated"
S_WORKFLOW_PATH = f"{S_PROJECT}/.vaibify/projects/{S_SLUG}.json"
S_PLOT_TEMPLATE = "{sPlotDirectory}/numbers.{sFigureType}"
S_PLOT_KEY = "Figures/numbers.png"
DICT_STEP = {
    "sName": "Plot Step", "sLabel": "A01", "sDirectory": S_STEP_DIRECTORY,
    "saOutputDataFiles": ["out.dat"], "saPlotFiles": [S_PLOT_TEMPLATE],
    "saDataCommands": [], "saPlotCommands": [], "bNoInputData": True,
}
DICT_WORKFLOW_DOCUMENT = {
    "sPlotDirectory": "Figures", "sFigureType": "png",
    "listSteps": [DICT_STEP],
}


def _fnWriteInContainer(connection, sContainerId, sPath, sContent):
    connection.fnWriteFile(
        sContainerId, sPath, sContent.encode("utf-8"),
        sAuthorizedRoot=S_PROJECT, tForbiddenNames=(),
    )


def _fnBuildTheProjectAndRunItsTests(container, connection):
    iExit, sOutput = container.exec_run(
        ["sh", "-c", "pip install -q pytest"], user="root")
    assert iExit == 0, "installing pytest in the container failed: " + (
        sOutput.decode("utf-8", errors="replace")[-400:])
    iExit, _ = _fsRunAsUser(
        container,
        f"mkdir -p {S_PROJECT}/.vaibify/projects {S_PROJECT}/Figures "
        f"{S_PROJECT}/{S_STEP_DIRECTORY}/tests")
    assert iExit == 0
    sStep = f"{S_PROJECT}/{S_STEP_DIRECTORY}"
    _fnWriteInContainer(
        connection, container.id, S_WORKFLOW_PATH,
        json.dumps(DICT_WORKFLOW_DOCUMENT))
    _fnWriteInContainer(connection, container.id, f"{sStep}/out.dat", "v1")
    _fnWriteInContainer(
        connection, container.id, f"{S_PROJECT}/{S_PLOT_KEY}", "the plot")
    _fnWriteInContainer(
        connection, container.id, f"{sStep}/tests/conftest.py",
        conftestManager.fsBuildConftestSource(S_PROJECT))
    _fnWriteInContainer(
        connection, container.id, f"{sStep}/tests/test_integrity_ok.py",
        "def test_the_output_exists():\n    assert True\n")
    iExit, sPytest = _fsRunAsUser(
        container,
        f"cd {S_PROJECT} && VAIBIFY_ACTIVE_WORKFLOW_SLUG={S_SLUG} "
        f"python -m pytest {S_STEP_DIRECTORY}/tests -q -p no:cacheprovider")
    assert iExit == 0, sPytest


def _fdictWorkflow():
    return dict(
        DICT_WORKFLOW_DOCUMENT, sProjectRepoPath=S_PROJECT,
        listSteps=[dict(DICT_STEP, dictVerification={
            "sUnitTest": "passed", "sIntegrity": "passed",
            "sQualitative": "unnecessary", "sQuantitative": "unnecessary",
            "sUser": "untested"})],
        **{workflowManager.S_LOADED_FROM_KEY: S_WORKFLOW_PATH})


def _fdictMarkerWrittenInTheContainer(connection, sContainerId):
    sMarker = (f"{S_PROJECT}/.vaibify/test_markers/{S_SLUG}/"
               f"{S_STEP_DIRECTORY}.json")
    return json.loads(connection.fbaFetchFile(sContainerId, sMarker))


def testTheContainersPluginHashesATemplatedPlotUnderTheHostsKey(liveContainer):
    container, connection = liveContainer
    _fnBuildTheProjectAndRunItsTests(container, connection)
    dictWorkflow = _fdictWorkflow()
    assert fsStepFileRepoRelative(
        dictWorkflow, dictWorkflow["listSteps"][0], S_PLOT_TEMPLATE,
    ) == S_PLOT_KEY
    dictMarker = _fdictMarkerWrittenInTheContainer(connection, container.id)
    listRecorded = [
        sorted(dictRun["dictOutputHashes"])
        for dictRun in dictMarker["dictRuns"].values()]
    assert listRecorded == [sorted([S_PLOT_KEY, S_STEP_DIRECTORY + "/out.dat"])]
    iNewer = int(time.time()) + 120
    iExit, sTouch = _fsRunAsUser(
        container, f"touch -d @{iNewer} {S_PROJECT}/{S_PLOT_KEY}")
    assert iExit == 0, sTouch
    dictCtx = {
        "docker": connection, "save": MagicMock(), "files": object(),
        "paths": {container.id: S_WORKFLOW_PATH},
        "workflows": {container.id: dictWorkflow},
        "variables": MagicMock(return_value={}),
    }
    dictVariables = workflowManager.fdictBuildGlobalVariables(
        dictWorkflow, S_WORKFLOW_PATH)
    for _iPoll in range(2):
        dictAnswer = asyncio.run(pipelineRoutes.fdictComputeFileStatus(
            dictCtx, container.id, dictWorkflow, dictVariables))
        assert int(float(dictAnswer["dictModTimes"][S_PLOT_KEY])) == iNewer
        dictVerification = dictWorkflow["listSteps"][0]["dictVerification"]
        assert dictVerification["sUnitTest"] == "passed", dictVerification
        assert dictAnswer["dictTestMarkers"]["0"]["bStale"] is False
