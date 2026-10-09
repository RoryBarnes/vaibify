"""The readiness answer says when the running limits differ from vaibify.yml.

A limit is applied by ``docker run``, so a running container keeps the
limits it was created with until it is created again, and a limit raised
by hand with ``docker update`` survives until then too. The readiness
answer compares the RUNNING limits with what the file asks for and
carries one sentence per difference on its OWN key: the start-warnings
list is headed "from the most recent container start", and this is not.

Unknown determines nothing: an unreadable file or an unreadable running
limit yields no sentence, never a claim of drift.
"""

from types import SimpleNamespace
from unittest.mock import patch

from vaibify.config import resourceLimits
from vaibify.gui.routes import systemRoutes


S_CONTAINER_ID = "d1f7c0ffee0000000000000000000000000000000000000000000000000000aa"
I_GIGABYTE = 2 ** 30


def _fdictSettledReadiness():
    return {
        "bReady": True, "sStatus": "ok", "sReason": "",
        "saWarnings": [], "iWarningCount": 0,
    }


def _fdictReadinessWith(configProject, dictRunning):
    with patch.object(
        systemRoutes, "_fdictProbeContainerReadiness",
        return_value=_fdictSettledReadiness(),
    ), patch.object(
        systemRoutes, "_flistDescribeUnresolvableSecrets", return_value=[],
    ), patch.object(
        systemRoutes, "_flistDescribeConfigurationDrift", return_value=[],
    ), patch.object(
        systemRoutes, "_ftDescribeX11Findings", return_value=([], []),
    ), patch.object(
        systemRoutes, "_fconfigForContainerOrNone",
        return_value=configProject,
    ), patch(
        "vaibify.gui.pipelineRunSlots.fdictReadRunningLimits",
        return_value=dictRunning,
    ):
        return systemRoutes._fdictReadinessWithSecretWarnings(
            None, S_CONTAINER_ID)


def _fconfig(iCpuLimit=0, fMemoryLimitGigabytes=0.0):
    return SimpleNamespace(
        iCpuLimit=iCpuLimit, fMemoryLimitGigabytes=fMemoryLimitGigabytes)


def testTheDriftRidesItsOwnKeyAndNeverTheStartWarnings():
    dictRunning = resourceLimits.fdictParseRunningLimits({
        "Memory": 6 * I_GIGABYTE, "MemorySwap": 12 * I_GIGABYTE,
        "NanoCpus": 0, "CpuQuota": 0})
    dictReadiness = _fdictReadinessWith(
        _fconfig(fMemoryLimitGigabytes=1.0), dictRunning)
    assert dictReadiness["listResourceLimitDrift"] == [
        "This container runs with a 6 GB memory limit, but vaibify.yml "
        "says 1 GB; the next Restart will apply 1 GB."]
    assert dictReadiness["saWarnings"] == []


def testAnUnreadableRunningLimitSaysNothing():
    dictReadiness = _fdictReadinessWith(
        _fconfig(fMemoryLimitGigabytes=1.0),
        resourceLimits.fdictUnknownRunningLimits())
    assert dictReadiness["listResourceLimitDrift"] == []


def testAnUnreadableFileSaysNothing():
    dictReadiness = _fdictReadinessWith(
        None, resourceLimits.fdictParseRunningLimits({"Memory": I_GIGABYTE}))
    assert dictReadiness["listResourceLimitDrift"] == []
