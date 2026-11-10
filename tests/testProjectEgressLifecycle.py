"""The hub restores a running sealed container's missing proxy, and only then.

The daemon is replaced by recorded answers for the proxy state and the
attached containers; the lifecycle lock is the real file lock, held
from the test itself where a test needs it held, because the guarantee
is that a restore cannot overlap a start, stop or delete in ANY process.
"""

import pytest

from vaibify.config import containerLock
from vaibify.docker import projectEgress
from vaibify.gui import projectEgressLifecycle

S_UUID = "e" * 32
S_CONTAINER = "sealedProject"


@pytest.fixture
def daemon(monkeypatch):
    dictState = {"sProxyState": "absent", "listRunning": [S_CONTAINER],
                 "listRestored": []}
    monkeypatch.setattr(projectEgress, "fsProxyState",
                        lambda sUuid: dictState["sProxyState"])
    monkeypatch.setattr(projectEgress, "flistContainersUsingEgress",
                        lambda sUuid: list(dictState["listRunning"]))

    def fsRestore(sUuid):
        dictState["listRestored"].append(sUuid)
        dictState["sProxyState"] = "running"
        return "172.30.0.1"

    monkeypatch.setattr(projectEgress, "fsRestoreProjectProxy", fsRestore)
    return dictState


def testAMissingProxyOfARunningContainerIsRestored(daemon):
    assert projectEgressLifecycle.fsRestoreProxyIfMissing(S_UUID) == "restored"
    assert daemon["listRestored"] == [S_UUID]


def testARunningProxyIsLeftAlone(daemon):
    daemon["sProxyState"] = "running"
    assert projectEgressLifecycle.fsRestoreProxyIfMissing(S_UUID) == "kept"
    assert daemon["listRestored"] == []


def testAStoppedContainerGetsNoProxy(daemon):
    """A stopped container's next start prepares its egress afresh."""
    daemon["listRunning"] = []
    assert projectEgressLifecycle.fsRestoreProxyIfMissing(S_UUID) == "kept"
    assert daemon["listRestored"] == []


@pytest.mark.falsification
def testARestoreDefersToAHeldLifecycleLock(daemon):
    """Kills: restoring a proxy while a start, stop or delete holds the name.

    A stop that is removing the container, or another hub's start that
    is preparing a fresh proxy, holds the lifecycle lock; a restore in
    that window would recreate a proxy for a container on its way out,
    or race the launch. Mutation: the restore's ``with
    fcontextHoldLifecycleLock(...)`` becomes ``if True:``.
    """
    with containerLock.fcontextHoldLifecycleLock(S_CONTAINER, "stopped"):
        sOutcome = projectEgressLifecycle.fsRestoreProxyIfMissing(S_UUID)
    assert sOutcome == "deferred"
    assert daemon["listRestored"] == []


def testTheStateIsReadAgainUnderTheLock(daemon, monkeypatch):
    """The container the restore was for may be gone once the lock is ours."""
    listAnswers = [[S_CONTAINER], []]
    monkeypatch.setattr(projectEgress, "flistContainersUsingEgress",
                        lambda sUuid: listAnswers.pop(0))
    assert projectEgressLifecycle.fsRestoreProxyIfMissing(S_UUID) == "kept"
    assert daemon["listRestored"] == []


def testARefusedRestoreIsReportedNotRaised(daemon, monkeypatch):
    def fsRefuse(sUuid):
        raise projectEgress.EgressSetupError("restart the container")
    monkeypatch.setattr(projectEgress, "fsRestoreProjectProxy", fsRefuse)
    assert projectEgressLifecycle.fsRestoreProxyIfMissing(S_UUID) == "restore-refused"
