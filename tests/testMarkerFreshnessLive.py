"""Tests pass in a REAL container and stay passed across polls.

The reported bug was invisible to every test that rooted a project at
``tmp_path``, because the host could read such a path and so the host
lane's read succeeded. Only a real container shows what the host cannot
do for a container project: the files live in a volume this machine
cannot open, the conftest runs where they are, and the poll must judge
the digests the conftest recorded against digests taken in the same
place.

This runs the real thing end to end: pytest with the generated conftest
inside the container writes the marker; the real poll (the real typed
reads, the real snapshot program, the real verdicts and gates) runs
against the real connection, twice. The unit-test axis must read
``passed`` after both, nothing may be invalidated, and a file changed
inside the container must be noticed by the very next poll.

Skipped when no daemon answers, unless ``VAIBIFY_REQUIRE_DOCKER_DAEMON``
demands one (see ``tests/testDockerConnectionLive.py``). The container is
the one ``tests/testConfinedWriteLive.py`` creates and labels; this file
installs pytest into it, which needs network access exactly as pulling
the image did.
"""

import asyncio
import json
from unittest.mock import MagicMock

import pytest

from tests.testConfinedWriteLive import (  # noqa: F401  (fixture)
    S_PROJECT,
    S_USER,
    _fsRunAsUser,
    liveContainer,
)
from vaibify.gui import conftestManager, workflowManager
from vaibify.gui.routes import pipelineRoutes

pytestmark = pytest.mark.docker_live

S_STEP_DIRECTORY = "StepA"
S_SLUG = "demo"
S_WORKFLOW_PATH = f"{S_PROJECT}/.vaibify/projects/{S_SLUG}.json"
DICT_WORKFLOW_DOCUMENT = {
    "listSteps": [{
        "sName": "Step A", "sDirectory": S_STEP_DIRECTORY,
        "saOutputDataFiles": ["out.dat"], "saPlotFiles": [],
        "saDataCommands": [], "bNoInputData": True,
    }],
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
        f"mkdir -p {S_PROJECT}/.vaibify/projects "
        f"{S_PROJECT}/{S_STEP_DIRECTORY}/tests")
    assert iExit == 0
    sProject = f"{S_PROJECT}/{S_STEP_DIRECTORY}"
    _fnWriteInContainer(
        connection, container.id, S_WORKFLOW_PATH,
        json.dumps(DICT_WORKFLOW_DOCUMENT))
    _fnWriteInContainer(connection, container.id, f"{sProject}/out.dat", "v1")
    _fnWriteInContainer(
        connection, container.id, f"{sProject}/tests/conftest.py",
        conftestManager.fsBuildConftestSource(S_PROJECT))
    _fnWriteInContainer(
        connection, container.id, f"{sProject}/tests/test_integrity_ok.py",
        "def test_the_output_exists():\n    assert True\n")
    iExit, sPytest = _fsRunAsUser(
        container,
        f"cd {S_PROJECT} && VAIBIFY_ACTIVE_WORKFLOW_SLUG={S_SLUG} "
        f"python -m pytest {S_STEP_DIRECTORY}/tests -q -p no:cacheprovider")
    assert iExit == 0, sPytest


def _fdictPassedWorkflow(sContainerId):
    return {
        "sProjectRepoPath": S_PROJECT,
        workflowManager.S_LOADED_FROM_KEY: S_WORKFLOW_PATH,
        "listSteps": [{
            "sName": "Step A", "sLabel": "A01",
            "sDirectory": S_STEP_DIRECTORY,
            "saOutputDataFiles": ["out.dat"], "saPlotFiles": [],
            "saDataCommands": [], "bNoInputData": True,
            "dictVerification": {
                "sUnitTest": "passed", "sIntegrity": "passed",
                "sQualitative": "unnecessary",
                "sQuantitative": "unnecessary", "sUser": "passed"},
        }],
    }


def _fdictPoll(connection, sContainerId, dictWorkflow, dictCtx):
    return asyncio.run(pipelineRoutes._fdictFetchOutputStatus(
        dictCtx, sContainerId, dictWorkflow, {}))


def testPassingTestsStayPassedAcrossPollsInARealContainer(liveContainer):
    container, connection = liveContainer
    _fnBuildTheProjectAndRunItsTests(container, connection)
    dictWorkflow = _fdictPassedWorkflow(container.id)
    dictCtx = {
        "docker": connection, "save": MagicMock(), "files": object(),
        "paths": {}, "workflows": {container.id: dictWorkflow},
        "variables": MagicMock(return_value={}),
    }
    for _iPoll in range(2):
        dictAnswer = _fdictPoll(
            connection, container.id, dictWorkflow, dictCtx)
        assert dictAnswer["dictInvalidatedSteps"] == {}
        dictVerification = dictWorkflow["listSteps"][0]["dictVerification"]
        assert dictVerification["sUnitTest"] == "passed", dictVerification
    assert not [
        dictBlocker for dictBlocker in dictAnswer["listBlockers"]
        if dictBlocker["sCriterion"] == "test-freshness-unchecked"
    ], dictAnswer["listBlockers"]


def testAFileChangedInsideTheContainerIsNoticedByTheNextPoll(liveContainer):
    container, connection = liveContainer
    _fnBuildTheProjectAndRunItsTests(container, connection)
    dictWorkflow = _fdictPassedWorkflow(container.id)
    dictCtx = {
        "docker": connection, "save": MagicMock(), "files": object(),
        "paths": {}, "workflows": {container.id: dictWorkflow},
        "variables": MagicMock(return_value={}),
    }
    _fdictPoll(connection, container.id, dictWorkflow, dictCtx)
    assert dictWorkflow["listSteps"][0]["dictVerification"][
        "sUnitTest"] == "passed"
    _fnWriteInContainer(
        connection, container.id,
        f"{S_PROJECT}/{S_STEP_DIRECTORY}/out.dat", "v2")
    dictAnswer = _fdictPoll(connection, container.id, dictWorkflow, dictCtx)
    assert dictWorkflow["listSteps"][0]["dictVerification"][
        "sUnitTest"] == "untested"
    assert 0 in dictAnswer["dictInvalidatedSteps"]
