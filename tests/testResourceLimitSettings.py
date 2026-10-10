"""Saving CPU and memory limits: only real changes, each with its outcome.

Driven through the REAL hub application over HTTP with genuine browser
credentials. The dashboard sends both limit fields on every save, so the
route acts only on a field whose value differs from ``vaibify.yml``, and
answers each change with what will happen to it. Until a live path
exists, every change waits for the next start, and the response says so
field by field; ``bRestartRequired`` is set only when something does.

The in-container agent must never lift its own container's ceiling, so
the agent token is refused outright on this route and nothing is
written.
"""

import os

from vaibify.config import resourceLimits
from vaibify.config.projectConfig import fconfigLoadFromFile
from vaibify.gui import actionCatalog, containerOwnership
from tests.testContainerLifecycleGating import (  # noqa: F401
    appHub,
    fclientAuthenticated,
    fixtureIsolateHostState,
    fnRegisterProject,
)


S_PROJECT = "limits-lane-project"
S_AGENT_TOKEN = "limits-lane-agent-token"
S_CPU_LINE = "cpuLimit: 4  # four cores, chosen by hand\n"


def _fsRegisterWithLimits(client, tmp_path):
    sDirectory = fnRegisterProject(client, tmp_path, S_PROJECT)
    sConfigPath = os.path.join(sDirectory, "vaibify.yml")
    with open(sConfigPath, "a") as fileConfig:
        fileConfig.write(S_CPU_LINE)
        fileConfig.write("memoryLimitGigabytes: 1\n")
    return sConfigPath


def _fdictSave(client, dictBody):
    responseSave = client.post(
        f"/api/containers/{S_PROJECT}/settings", json=dictBody)
    assert responseSave.status_code == 200, responseSave.text
    return responseSave.json()


def testOnlyTheFieldThatChangedIsWrittenOrReported(appHub, tmp_path):
    with fclientAuthenticated(appHub) as client:
        sConfigPath = _fsRegisterWithLimits(client, tmp_path)
        dictAnswer = _fdictSave(
            client, {"iCpuLimit": 4, "fMemoryLimitGigabytes": 2.5})
    assert [d["sField"] for d in dictAnswer["listLimitOutcomes"]] == [
        resourceLimits.S_FIELD_MEMORY]
    with open(sConfigPath) as fileConfig:
        sText = fileConfig.read()
    assert S_CPU_LINE in sText, "an unchanged field was rewritten"
    assert fconfigLoadFromFile(sConfigPath).fMemoryLimitGigabytes == 2.5


def testEveryChangeSaysItWaitsForTheNextStart(appHub, tmp_path):
    with fclientAuthenticated(appHub) as client:
        _fsRegisterWithLimits(client, tmp_path)
        dictAnswer = _fdictSave(
            client, {"iCpuLimit": 2, "fMemoryLimitGigabytes": 6})
    assert dictAnswer["bRestartRequired"] is True
    dictOutcomes = {d["sField"]: d for d in dictAnswer["listLimitOutcomes"]}
    assert set(dictOutcomes) == {
        resourceLimits.S_FIELD_MEMORY, resourceLimits.S_FIELD_CPU}
    for dictOutcome in dictOutcomes.values():
        assert dictOutcome["sOutcome"] == resourceLimits.S_ACTION_NEXT_START
        assert dictOutcome["sSentence"].endswith(
            "It applies the next time the container starts.")
    assert "6 GB" in dictOutcomes[resourceLimits.S_FIELD_MEMORY]["sSentence"]


def testASaveThatChangesNoLimitAsksForNoRestart(appHub, tmp_path):
    with fclientAuthenticated(appHub) as client:
        _fsRegisterWithLimits(client, tmp_path)
        dictAnswer = _fdictSave(
            client, {"iCpuLimit": 4, "fMemoryLimitGigabytes": 1.0})
    assert dictAnswer["listLimitOutcomes"] == []
    assert dictAnswer["bRestartRequired"] is False


def testTheInContainerAgentCannotLiftItsOwnCeiling(appHub, tmp_path):
    """The agent lane is refused outright, and the file is untouched.

    The agent's token is registered on an owner record whose name is the
    project and whose Docker id is a different string, so the refusal
    cannot come from a name/id mismatch that happens to fail closed.
    """
    with fclientAuthenticated(appHub) as client:
        sConfigPath = _fsRegisterWithLimits(client, tmp_path)
    appHub.state.dictContainerOwners[S_PROJECT] = (
        containerOwnership.OwnerRecord(
            sLeaseId="researcher-lease", fileHandleLock=None,
            sAgentToken=S_AGENT_TOKEN, sContainerId="4b1d00c0ffee0000"))
    from fastapi.testclient import TestClient
    clientAgent = TestClient(appHub, headers={
        actionCatalog.S_SESSION_HEADER_NAME: S_AGENT_TOKEN,
        "Host": "host.docker.internal:8050",
    })
    responseAgent = clientAgent.post(
        f"/api/containers/{S_PROJECT}/settings",
        json={"iCpuLimit": 4, "fMemoryLimitGigabytes": 64})
    assert responseAgent.status_code in (401, 403), responseAgent.text
    assert fconfigLoadFromFile(sConfigPath).fMemoryLimitGigabytes == 1.0
