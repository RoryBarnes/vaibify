"""Applying a saved limit to the running container, judged by re-inspection.

The live path sends ONE ``docker update`` with only the changes that
cannot kill a process, under the per-container mutation lock, and then
re-inspects the container: what the daemon reports afterwards decides
each field's outcome, never the update's exit code. These tests drive
it with a fake daemon so that "the update said yes and the container
did not change" can be staged; ``testResourceLimitLiveApplyLive.py``
does the same against a real one.
"""

import asyncio
from types import SimpleNamespace

import pytest

from vaibify.config import resourceLimits
from vaibify.docker import containerManager
from vaibify.gui import resourceLimitApplication, sessionLifecycle


S_NAME = "live-limits-project"
S_ID = "1d5e000000000000000000000000000000000000000000000000000000000ddd"
I_GIGABYTE = 2 ** 30
I_NANO = 10 ** 9


class _FakeDaemon:
    """A container whose HostConfig the test sets before and after the update."""

    def __init__(self, dictBefore, dictAfter=None, sRefusal=""):
        self.dictBefore = dictBefore
        self.dictAfter = dictAfter if dictAfter is not None else dictBefore
        self.sRefusal = sRefusal
        self.listUpdates = []
        self.bUpdated = False

    def fjsonInspect(self, sIdentifier):
        dictHostConfig = self.dictAfter if self.bUpdated else self.dictBefore
        return {"Id": S_ID, "State": {"Running": True},
                "HostConfig": dict(dictHostConfig)}

    def fnUpdate(self, sContainerId, listChanges):
        self.listUpdates.append(
            (sContainerId, containerManager._flistResourceUpdateArgs(
                listChanges)))
        self.bUpdated = True
        if self.sRefusal:
            raise RuntimeError(self.sRefusal)


@pytest.fixture
def fnInstallDaemon(monkeypatch):
    def fnInstall(daemonFake):
        monkeypatch.setattr(
            containerManager, "fjsonInspectContainer", daemonFake.fjsonInspect)
        monkeypatch.setattr(
            containerManager, "fnApplyResourceChangesLive",
            daemonFake.fnUpdate)
        return daemonFake
    return fnInstall


def _fconfig(iCpuLimit=2, fMemoryLimitGigabytes=1.0):
    return SimpleNamespace(
        iCpuLimit=iCpuLimit, fMemoryLimitGigabytes=fMemoryLimitGigabytes)


def _flistApply(listFields, configSaved, appState=None):
    return asyncio.run(resourceLimitApplication.flistApplyChangedLimits(
        appState or SimpleNamespace(), S_NAME, listFields, configSaved))


def _fdictByField(listOutcomes):
    return {d["sField"]: d for d in listOutcomes}


def testARaiseIsSentWithItsSwapAndReportedApplied(fnInstallDaemon):
    daemonFake = fnInstallDaemon(_FakeDaemon(
        {"Memory": I_GIGABYTE, "MemorySwap": 2 * I_GIGABYTE,
         "NanoCpus": 2 * I_NANO},
        {"Memory": 4 * I_GIGABYTE, "MemorySwap": 8 * I_GIGABYTE,
         "NanoCpus": 2 * I_NANO}))
    dictOutcomes = _fdictByField(_flistApply(
        [resourceLimits.S_FIELD_MEMORY], _fconfig(fMemoryLimitGigabytes=4.0)))
    assert daemonFake.listUpdates == [(S_ID, [
        "--memory", str(4 * I_GIGABYTE),
        "--memory-swap", str(8 * I_GIGABYTE)])]
    dictMemory = dictOutcomes[resourceLimits.S_FIELD_MEMORY]
    assert dictMemory["sOutcome"] == resourceLimits.S_OUTCOME_APPLIED
    assert dictMemory["sSentence"].endswith("The running container has it now.")


@pytest.mark.falsification
def testAPartialResultIsReportedFieldByField(fnInstallDaemon):
    """Kills: trusting the update's exit code instead of re-inspecting.

    The update raises no error, yet the daemon reports only the memory
    change afterwards. Memory is applied; CPU is not, and says so.
    """
    fnInstallDaemon(_FakeDaemon(
        {"Memory": I_GIGABYTE, "MemorySwap": 2 * I_GIGABYTE,
         "NanoCpus": 2 * I_NANO},
        {"Memory": 4 * I_GIGABYTE, "MemorySwap": 8 * I_GIGABYTE,
         "NanoCpus": 2 * I_NANO}))
    dictOutcomes = _fdictByField(_flistApply(
        [resourceLimits.S_FIELD_MEMORY, resourceLimits.S_FIELD_CPU],
        _fconfig(iCpuLimit=1, fMemoryLimitGigabytes=4.0)))
    assert dictOutcomes[resourceLimits.S_FIELD_MEMORY]["sOutcome"] == (
        resourceLimits.S_OUTCOME_APPLIED)
    assert dictOutcomes[resourceLimits.S_FIELD_CPU]["sOutcome"] == (
        resourceLimits.S_OUTCOME_FAILED)


def testARefusedUpdateKeepsDockersWords(fnInstallDaemon):
    sRefusal = (
        "Error response from daemon: Memory limit should be smaller than "
        "already set memoryswap limit")
    fnInstallDaemon(_FakeDaemon(
        {"Memory": I_GIGABYTE, "MemorySwap": 2 * I_GIGABYTE,
         "NanoCpus": 2 * I_NANO}, sRefusal=sRefusal))
    dictMemory = _fdictByField(_flistApply(
        [resourceLimits.S_FIELD_MEMORY], _fconfig(fMemoryLimitGigabytes=4.0))
    )[resourceLimits.S_FIELD_MEMORY]
    assert dictMemory["sOutcome"] == resourceLimits.S_OUTCOME_FAILED
    assert "memoryswap" in dictMemory["sSentence"]
    assert "next time the container starts" in dictMemory["sSentence"]


@pytest.mark.falsification
def testAMemoryDecreaseIsNeverSentLive(fnInstallDaemon):
    """Kills: applying a memory decrease to the running container."""
    daemonFake = fnInstallDaemon(_FakeDaemon(
        {"Memory": 4 * I_GIGABYTE, "MemorySwap": 8 * I_GIGABYTE,
         "NanoCpus": 2 * I_NANO}))
    dictMemory = _fdictByField(_flistApply(
        [resourceLimits.S_FIELD_MEMORY], _fconfig(fMemoryLimitGigabytes=1.0))
    )[resourceLimits.S_FIELD_MEMORY]
    assert daemonFake.listUpdates == []
    assert dictMemory["sOutcome"] == resourceLimits.S_ACTION_NEXT_START
    assert "kill a process" in dictMemory["sSentence"]


@pytest.mark.falsification
def testEveryMemoryRaiseCarriesItsSwapLimit():
    """Kills: dropping --memory-swap from a live memory raise.

    Docker refuses a memory limit above the existing swap limit unless
    the swap limit moves in the same update (measured on Docker 28.3.3).
    """
    listArgs = containerManager._flistResourceUpdateArgs([{
        "sField": resourceLimits.S_FIELD_MEMORY,
        "sAction": resourceLimits.S_ACTION_APPLY_LIVE,
        "iDesiredBytes": 4 * I_GIGABYTE, "iSwapBytes": -1,
    }])
    assert listArgs == ["--memory", str(4 * I_GIGABYTE), "--memory-swap", "-1"]


def testAStoppedContainerIsNeverUpdated(fnInstallDaemon, monkeypatch):
    daemonFake = fnInstallDaemon(_FakeDaemon({"Memory": I_GIGABYTE}))
    monkeypatch.setattr(
        containerManager, "fjsonInspectContainer",
        lambda sName: {"Id": S_ID, "State": {"Running": False}})
    listOutcomes = _flistApply(
        [resourceLimits.S_FIELD_MEMORY], _fconfig(fMemoryLimitGigabytes=4.0))
    assert daemonFake.listUpdates == []
    assert listOutcomes[0]["sOutcome"] == resourceLimits.S_ACTION_NEXT_START


def testTheUpdateWaitsForTheContainersMutationLock(fnInstallDaemon):
    """A start settlement or guarded write holding the lock is waited out."""
    daemonFake = fnInstallDaemon(_FakeDaemon(
        {"Memory": I_GIGABYTE, "MemorySwap": 2 * I_GIGABYTE,
         "NanoCpus": 2 * I_NANO},
        {"Memory": 4 * I_GIGABYTE, "MemorySwap": 8 * I_GIGABYTE,
         "NanoCpus": 2 * I_NANO}))
    appState = SimpleNamespace()

    async def fnScenario():
        lockMutation = sessionLifecycle.flockContainerMutationForAppState(
            appState, S_NAME)
        await lockMutation.acquire()
        taskApply = asyncio.ensure_future(
            resourceLimitApplication.flistApplyChangedLimits(
                appState, S_NAME, [resourceLimits.S_FIELD_MEMORY],
                _fconfig(fMemoryLimitGigabytes=4.0)))
        await asyncio.sleep(0.3)
        iWhileHeld = len(daemonFake.listUpdates)
        lockMutation.release()
        await taskApply
        return iWhileHeld

    assert asyncio.run(fnScenario()) == 0
    assert len(daemonFake.listUpdates) == 1
