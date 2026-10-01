"""A credential test's error text carries no staged path and no secret.

While a login is staged for a runner, an exception raised anywhere in the
test can quote the staged file's path (``.../vc_secret_<name>_<random>.tmp``)
or the credential it holds, and ``fnRunCredentialTestJob`` used to write
``f"{type(error).__name__}: {error}"`` into the job record the panel
reads AND the durable evidence document. These tests make the test
machinery fault with exactly such text and read back every place the
outcome lands.
"""

import json
import logging
import threading

import pytest

from tests.testCoverageCouncilStateCredentialTest import (  # noqa: F401
    NoLoginDocker,
    S_CONTAINER_ID,
    fdictBeginRecordedJob,
    pathStagingRoot,
    sEvidencePath,
)
from vaibify.gui import (
    agentCouncilCredentialStore,
    agentCouncilCredentialTest,
    agentCouncilCredentialTestRecords,
)
from vaibify.gui.routes import councilCredentialRoutes

S_STAGED_PATH = "/home/someone/.vaibify/tmp/vc_secret_claude_a1b2c3.tmp"
S_SENTINEL = "SENTINEL-TOKEN-9921"
S_SHAPED_TOKEN = "sk-ant-oat01-" + "Z" * 30


def _fdictRuntime():
    return {
        "connectionDocker": NoLoginDocker(), "sContainerId": S_CONTAINER_ID,
        "eventCancel": threading.Event(),
        "listStagedPaths": [S_STAGED_PATH],
    }


def _fsEverythingTheTestPublished(sEvidencePath, dictJob):
    dictRecord = agentCouncilCredentialTestRecords.fdictReadJobRecord(
        dictJob["sJobId"])
    with open(sEvidencePath, encoding="utf-8") as fileEvidence:
        sEvidence = fileEvidence.read()
    return json.dumps(dictRecord) + sEvidence + json.dumps(
        councilCredentialRoutes._fdictDescribeJob(dictRecord))


@pytest.mark.falsification
def testAFaultThatQuotesTheStagedPathOrTheTokenIsNotPublished(
        sEvidencePath, pathStagingRoot, monkeypatch, caplog):
    """Kills: publishing the raw exception text of an unexpected fault."""
    dictJob = fdictBeginRecordedJob(sEvidencePath)

    async def fnFaultWhileStaged(dictJobArgument, dictRuntime):
        raise RuntimeError(
            f"cannot read {S_STAGED_PATH}: bad token {S_SENTINEL} "
            f"and {S_SHAPED_TOKEN}")

    monkeypatch.setattr(
        agentCouncilCredentialTest, "_fnExecuteChecks", fnFaultWhileStaged)
    with caplog.at_level(logging.WARNING, logger="vaibify"):
        agentCouncilCredentialTest.fnRunCredentialTestJob(
            dictJob, _fdictRuntime())
    sPublished = _fsEverythingTheTestPublished(sEvidencePath, dictJob)
    assert "vc_secret_" not in sPublished
    assert S_SENTINEL not in sPublished
    assert S_SHAPED_TOKEN not in sPublished
    dictRecord = agentCouncilCredentialTestRecords.fdictReadJobRecord(
        dictJob["sJobId"])
    assert dictRecord["sStatus"] == (
        agentCouncilCredentialStore.S_OUTCOME_INCOMPLETE)
    assert "RuntimeError" in dictRecord["sDetail"]
    assert "hub log" in dictRecord["sDetail"]
    sLog = caplog.text
    assert "vc_secret_" not in sLog and S_SHAPED_TOKEN not in sLog


@pytest.mark.falsification
def testADesignedCheckFailureIsScrubbedOfPathsAndSecretShapes(
        sEvidencePath, pathStagingRoot, monkeypatch):
    """Kills: publishing a check's own detail text unscrubbed."""
    dictJob = fdictBeginRecordedJob(sEvidencePath)

    async def fnFailACheck(dictJobArgument, dictRuntime):
        raise agentCouncilCredentialTest.CredentialCheckFailedError(
            "loginPresent",
            f"the runner reported {S_STAGED_PATH} and {S_SHAPED_TOKEN} "
            "while reading the login")

    monkeypatch.setattr(
        agentCouncilCredentialTest, "_fnExecuteChecks", fnFailACheck)
    agentCouncilCredentialTest.fnRunCredentialTestJob(
        dictJob, _fdictRuntime())
    sPublished = _fsEverythingTheTestPublished(sEvidencePath, dictJob)
    assert "vc_secret_" not in sPublished
    assert S_SHAPED_TOKEN not in sPublished
    dictRecord = agentCouncilCredentialTestRecords.fdictReadJobRecord(
        dictJob["sJobId"])
    assert "while reading the login" in dictRecord["sDetail"]
    assert "[staged credential file]" in dictRecord["sDetail"]


def testTheStoresRefusalTextIsScrubbedToo(sEvidencePath):
    dictJob = fdictBeginRecordedJob(sEvidencePath)
    agentCouncilCredentialTest.fnPublishJobOutcome(
        dictJob, agentCouncilCredentialStore.S_OUTCOME_PASSED, "", "")
    dictSecond = dict(dictJob)
    agentCouncilCredentialTest.fnPublishJobOutcome(
        dictSecond, agentCouncilCredentialStore.S_OUTCOME_FAILED,
        "loginPresent", f"see {S_STAGED_PATH}")
    assert "vc_secret_" not in dictSecond["sDetail"]


def testOrdinaryDetailTextPassesThroughUnchanged():
    sPlain = "the login file is present but names no refresh scope"
    assert agentCouncilCredentialTest.fsSanitizeJobDetail(sPlain) == sPlain
    assert agentCouncilCredentialTest.fsSanitizeJobDetail("") == ""
    assert agentCouncilCredentialTest.fsSanitizeJobDetail(None) == ""
