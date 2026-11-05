"""A test file the step runs is never new, and an unfinished listing is no answer.

Seen on a second computer (2026-11-04): opening a project, with nothing
run, toasted "Step A01: 3 new test file(s) discovered" for each step's
own generated tests. The comparison read the step's test commands under
keys no step carries, so every test file read as unregistered on every
poll, and the page's one guard, that its first answer is the baseline,
took an unfinished listing's empty answer as that baseline.

The step is built by the step factory, its tests are registered by the
generator's own writer, and the listing is the container's real program,
run here against a real step directory, through the real poll function.

WHAT THIS DOES NOT COVER: what the page does with the answer
(tests/browser/testAnUnfinishedListingIsNotTheBaseline.py).
"""

import asyncio
import os
import subprocess
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vaibify.gui import testGenerator
from vaibify.gui.routes.pipelineRoutes import _fdictFetchTestStatus
from vaibify.gui.workflowManager import fdictCreateStep

S_CONTAINER_ID = "cid-test-files"


class _ThisHostAsTheContainer:
    """Write files and run commands here, where the step directory is real."""

    def fnWriteFile(self, sContainerId, sPath, baContent):
        del sContainerId
        os.makedirs(os.path.dirname(sPath), exist_ok=True)
        with open(sPath, "wb") as fileHandle:
            fileHandle.write(baContent)

    def ftResultExecuteCommand(self, sContainerId, sCommand, sLane=""):
        del sContainerId, sLane
        resultProc = subprocess.run(
            ["bash", "-c", sCommand], capture_output=True, text=True,
            check=False,
        )
        return resultProc.returncode, resultProc.stdout


class _AContainerWhoseListingFails:
    def ftResultExecuteCommand(self, sContainerId, sCommand, sLane=""):
        del sContainerId, sCommand, sLane
        return 1, ""


class _AContainerWhoseListingIsCutShort:
    def ftResultExecuteCommand(self, sContainerId, sCommand, sLane=""):
        del sContainerId, sCommand, sLane
        return 0, '{"markers": {}, "testFi'


def _fdictWorkflowWithGeneratedTests(pathRepo):
    """Return a workflow whose one step registers a generated test per category."""
    dictStep = fdictCreateStep("Step", "Step")
    connectionHost = _ThisHostAsTheContainer()
    for sKey, dictCategory in dictStep["dictTests"].items():
        if not isinstance(dictCategory, dict):
            continue
        sFilePath = str(pathRepo / "Step" / "tests" / (
            "test_" + sKey[len("dict"):].lower() + "_step.py"))
        dictCategory.update(testGenerator._fdictWriteTestFile(
            connectionHost, S_CONTAINER_ID,
            "def test_placeholder():\n    pass\n", sFilePath,
        ))
    return {"sProjectRepoPath": str(pathRepo), "listSteps": [dictStep]}


def _fdictPollTestStatus(dictWorkflow, connectionDocker, pathRepo):
    dictCtx = {
        "docker": connectionDocker, "save": MagicMock(),
        "paths": {S_CONTAINER_ID: str(pathRepo / "workflow.json")},
    }
    with patch(
        "vaibify.gui.routes.pipelineRoutes._fnRefreshConftestsAndMigrateMarkers",
        new=AsyncMock(),
    ):
        return asyncio.run(_fdictFetchTestStatus(
            dictCtx, S_CONTAINER_ID, dictWorkflow,
        ))


@pytest.mark.falsification
def testATestFileTheStepRunsIsNeverNew(tmp_path):
    """The generated tests are registered; only a file no command runs is new.

    Kills: reading the test commands under keys no step carries, which
    announced every generated test file as new on every poll.
    """
    dictWorkflow = _fdictWorkflowWithGeneratedTests(tmp_path)
    (tmp_path / "Step" / "tests" / "test_extra.py").write_text("")
    dictStatus = _fdictPollTestStatus(
        dictWorkflow, _ThisHostAsTheContainer(), tmp_path)
    assert dictStatus["dictTestFileChanges"] == {
        "0": {"listNew": ["test_extra.py"], "listMissing": []},
    }


@pytest.mark.falsification
def testAnUnfinishedListingIsNoAnswer(tmp_path):
    """A listing that did not finish says nothing, rather than "no test files".

    Kills: answering an unfinished listing with an empty map, which the
    page took as its baseline, so its next answer announced every
    existing test file as new.
    """
    dictWorkflow = _fdictWorkflowWithGeneratedTests(tmp_path)
    for connectionDocker in (
            _AContainerWhoseListingFails(), _AContainerWhoseListingIsCutShort()):
        dictStatus = _fdictPollTestStatus(dictWorkflow, connectionDocker, tmp_path)
        assert dictStatus["dictTestFileChanges"] is None
