"""Live limit changes against a real Docker daemon.

The planner's rules rest on three facts measured on Docker 28.3.3: a
memory raise applies live only when the swap limit moves with it,
``--memory 0`` and ``--cpus 0`` are silently ignored, and a CPU cap can
change either way on a running container. These tests apply real
changes to THROWAWAY containers -- uniquely named, labeled, and
force-removed in teardown -- and read the result back from the daemon,
so each rule is checked against the thing it is about.

Live-daemon convention: skips with no daemon unless
``VAIBIFY_REQUIRE_DOCKER_DAEMON`` demands one.
"""

import asyncio
import os
import secrets
from types import SimpleNamespace

import pytest

from tests.liveContainerLabels import fdictLabels
from tests.testDockerConnectionLive import fnRequireDaemonReachable
from vaibify.config import resourceLimits
from vaibify.gui import resourceLimitApplication

pytestmark = pytest.mark.docker_live

S_TEST_IMAGE = os.environ.get(
    "VAIBIFY_COUNCIL_TEST_IMAGE", "python:3.10-slim")
I_MEBIBYTE = 2 ** 20


@pytest.fixture
def fnCreateThrowaway():
    fnRequireDaemonReachable()
    import docker
    clientDocker = docker.from_env()
    listCreated = []

    def fcontainerCreate(**dictLimits):
        container = clientDocker.containers.run(
            S_TEST_IMAGE, ["sleep", "600"], detach=True,
            name="vaibify-limits-live-" + secrets.token_hex(6),
            labels=fdictLabels({"vaibify.test": "limits-live"}),
            **dictLimits)
        listCreated.append(container)
        return container

    try:
        yield fcontainerCreate
    finally:
        for container in listCreated:
            try:
                container.remove(force=True)
            except Exception:  # noqa: BLE001 -- teardown is best-effort
                pass


def _fdictApply(container, listFields, iCpuLimit, fMemoryGigabytes):
    listOutcomes = asyncio.run(resourceLimitApplication.flistApplyChangedLimits(
        SimpleNamespace(), container.name, listFields,
        SimpleNamespace(iCpuLimit=iCpuLimit,
                        fMemoryLimitGigabytes=fMemoryGigabytes)))
    container.reload()
    return {d["sField"]: d for d in listOutcomes}


def testAMemoryRaiseAppliesInPlaceAndCarriesTheSwap(fnCreateThrowaway):
    container = fnCreateThrowaway(mem_limit="256m")
    sIdBefore = container.id
    dictOutcomes = _fdictApply(
        container, [resourceLimits.S_FIELD_MEMORY], 1, 0.5)
    assert dictOutcomes["memory"]["sOutcome"] == "applied", dictOutcomes
    assert container.id == sIdBefore
    dictHostConfig = container.attrs["HostConfig"]
    assert dictHostConfig["Memory"] == 512 * I_MEBIBYTE
    assert dictHostConfig["MemorySwap"] == 1024 * I_MEBIBYTE


def testAMemoryDecreaseIsNotAppliedLive(fnCreateThrowaway):
    container = fnCreateThrowaway(mem_limit="512m")
    dictOutcomes = _fdictApply(
        container, [resourceLimits.S_FIELD_MEMORY], 1, 0.25)
    assert dictOutcomes["memory"]["sOutcome"] == "nextStart"
    assert container.attrs["HostConfig"]["Memory"] == 512 * I_MEBIBYTE


def testRemovingAMemoryLimitIsNeverReportedApplied(fnCreateThrowaway):
    container = fnCreateThrowaway(mem_limit="256m")
    dictOutcomes = _fdictApply(
        container, [resourceLimits.S_FIELD_MEMORY], 1, 0.0)
    assert dictOutcomes["memory"]["sOutcome"] == "nextStart"
    assert container.attrs["HostConfig"]["Memory"] == 256 * I_MEBIBYTE


def testACpuDecreaseAppliesLive(fnCreateThrowaway):
    container = fnCreateThrowaway(nano_cpus=2 * 10 ** 9)
    dictOutcomes = _fdictApply(
        container, [resourceLimits.S_FIELD_CPU], 1, 0.0)
    assert dictOutcomes["cpu"]["sOutcome"] == "applied", dictOutcomes
    assert container.attrs["HostConfig"]["NanoCpus"] == 10 ** 9
