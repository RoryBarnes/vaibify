"""A real file-status poll over a container this host cannot see.

The marker hash lane once opened a container project's files on the
HOST, where the container's volume does not exist, so every read failed
and every failure counted as drift. Every test of that lane rooted the
project at ``tmp_path``, which the host CAN read, so the suite stayed
green while the lane failed for every real container project.

This harness drives the REAL poll (``_fdictFetchOutputStatus``): the
real marker reader, the real snapshot fetch, the real verdicts, the
real invalidation and the real level gates. Only the transport is a
double, and it answers as the container would -- from a root asserted
absent on this machine. A test that wants a different answer from the
container changes what the double says, never what the poll does.
"""

import asyncio
import json
from unittest.mock import MagicMock

from tests.snapshotProgramHarness import (
    CannedSnapshotConnection,
    fdictSteadyHashEntry,
    fsContainerRootAbsentFromThisHost,
)
from vaibify.gui import workflowManager
from vaibify.gui.routes import pipelineRoutes

S_CONTAINER_ID = "cid-marker-poll"
S_WORKFLOW_SLUG = "demo"
S_WORKFLOW_RELATIVE = ".vaibify/projects/demo.json"
LIST_STEP_NAMES = ("A", "B", "C")
S_PASSED = {
    "sUnitTest": "passed", "sIntegrity": "passed",
    "sQualitative": "passed", "sQuantitative": "passed",
    "sUser": "passed",
}


def fsBaselineDigest(sStepName):
    """Return the 40-hex digest a marker records for a step's output."""
    return (sStepName.lower() * 40)[:40]


def fsOutputRelativePath(sStepName):
    return "Step" + sStepName + "/out.dat"


class MarkerPollConnection(CannedSnapshotConnection):
    """Everything the poll asks a container, answered from fixed facts."""

    def __init__(
        self, dictMarkersByRelativePath=None, dictHashEntries=None,
        sRoot="", iExitCode=0, setUnstattableRelativePaths=(),
        dictMtimeByRelativePath=None,
    ):
        super().__init__(dictHashEntries, iExitCode)
        self.sRoot = sRoot
        self.dictMarkersByRelativePath = dict(dictMarkersByRelativePath or {})
        self.setUnstattableRelativePaths = set(setUnstattableRelativePaths)
        self.dictMtimeByRelativePath = dict(dictMtimeByRelativePath or {})

    def fdictFetchSmallFiles(self, sContainerId, listPaths):
        dictFiles = {}
        for sPath in listPaths:
            sRelative = sPath[len(self.sRoot) + 1:]
            dictMarker = self.dictMarkersByRelativePath.get(sRelative)
            dictFiles[sPath] = (
                None if dictMarker is None
                else json.dumps(dictMarker).encode("utf-8"))
        return dictFiles

    def fdictStatPathMtimes(self, sContainerId, listPaths):
        dictMtimes = {}
        for sPath in listPaths:
            sRelative = sPath[len(self.sRoot) + 1:]
            if sRelative in self.setUnstattableRelativePaths:
                continue
            if sRelative.endswith("out.dat") or sRelative.endswith(
                    "demo.json"):
                dictMtimes[sPath] = self.dictMtimeByRelativePath.get(
                    sRelative, "1700000000")
        return dictMtimes

    def fsHashContainerFileSha256(self, sContainerId, sPath):
        return "0" * 64

    def fbaFetchFile(self, sContainerId, sPath, iMaxBytes=None):
        raise FileNotFoundError(sPath)

    def ftResultExecuteCommand(self, sContainerId, sCommand, sWorkdir=None):
        return (1, "")


def fdictStepFor(sStepName, listUpstreamNames=(), dictVerification=None):
    """Return one verified step; upstream names become step tokens."""
    listCommands = [
        "python run.py {step:step-%s.out}" % sUpstream.lower()
        for sUpstream in listUpstreamNames
    ]
    return {
        "sName": "Step " + sStepName, "sLabel": "A0" + str(
            LIST_STEP_NAMES.index(sStepName) + 1),
        "sStepId": "step-" + sStepName.lower(),
        "sDirectory": "Step" + sStepName, "bNoInputData": True,
        "saOutputDataFiles": ["out.dat"], "saPlotFiles": [],
        "saDataCommands": listCommands, "saTestCommands": [],
        "dictVerification": dict(dictVerification or S_PASSED),
    }


def fdictMarkerFor(sStepName):
    """Return the marker the conftest would have written for a step."""
    return {
        "sLabel": "A0" + str(LIST_STEP_NAMES.index(sStepName) + 1),
        "sDirectory": "Step" + sStepName, "iExitStatus": 0,
        "dictOutputHashes": {
            fsOutputRelativePath(sStepName): fsBaselineDigest(sStepName)},
        "dictCategories": {
            "integrity": {"iPassed": 1, "iFailed": 0},
            "qualitative": {"iPassed": 1, "iFailed": 0},
            "quantitative": {"iPassed": 1, "iFailed": 0},
        },
    }


class MarkerPollProject:
    """A three-step project: A feeds B, C stands alone."""

    def __init__(self, dictHashOverrides=None, dictMarkerOverrides=None,
                 iSnapshotExitCode=0, setUnstattableRelativePaths=(),
                 dictMtimeByRelativePath=None):
        self.sRoot = fsContainerRootAbsentFromThisHost()
        dictHashEntries = {
            fsOutputRelativePath(sName): fdictSteadyHashEntry(
                fsBaselineDigest(sName))
            for sName in LIST_STEP_NAMES
        }
        dictHashEntries.update(dictHashOverrides or {})
        dictMarkers = {
            ".vaibify/test_markers/%s/Step%s.json" % (
                S_WORKFLOW_SLUG, sName): fdictMarkerFor(sName)
            for sName in LIST_STEP_NAMES
        }
        dictMarkers.update(dictMarkerOverrides or {})
        self.connection = MarkerPollConnection(
            dictMarkers, dictHashEntries, self.sRoot, iSnapshotExitCode,
            setUnstattableRelativePaths, dictMtimeByRelativePath,
        )
        self.dictWorkflow = {
            "sProjectRepoPath": self.sRoot,
            workflowManager.S_LOADED_FROM_KEY:
                self.sRoot + "/" + S_WORKFLOW_RELATIVE,
            "listSteps": [
                fdictStepFor("A"), fdictStepFor("B", ["A"]),
                fdictStepFor("C"),
            ],
        }
        self.dictCtx = {
            "docker": self.connection, "save": MagicMock(),
            "files": object(), "paths": {}, "workflows": {
                S_CONTAINER_ID: self.dictWorkflow},
            "variables": MagicMock(return_value={}),
        }

    def fdictRunPoll(self):
        """Run one poll and return the wire answer."""
        return asyncio.run(pipelineRoutes._fdictFetchOutputStatus(
            self.dictCtx, S_CONTAINER_ID, self.dictWorkflow, {},
        ))

    def fdictVerificationOf(self, sStepName):
        iIndex = LIST_STEP_NAMES.index(sStepName)
        return self.dictWorkflow["listSteps"][iIndex]["dictVerification"]

    def flistInvalidatedStepNames(self):
        return [
            sName for sName in LIST_STEP_NAMES
            if self.fdictVerificationOf(sName)["sUnitTest"] != "passed"
        ]
