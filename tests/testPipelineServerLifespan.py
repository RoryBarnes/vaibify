"""Tests for pipelineServer._alifespanShared startup/shutdown safety."""

import asyncio
import logging
from types import SimpleNamespace

from vaibify.gui import pipelineServer


def _fappBuildFakeApp():
    """Return a minimal stand-in for a FastAPI app with lifespan lists."""
    appFake = SimpleNamespace()
    appFake.state = SimpleNamespace(
        listLifespanStartup=[],
        listLifespanShutdown=[],
    )
    return appFake


async def _fnDriveLifespanToCompletion(appFake):
    """Enter and exit the lifespan context, returning when shutdown finishes."""
    contextLifespan = pipelineServer._alifespanShared(appFake)
    await contextLifespan.__aenter__()
    await contextLifespan.__aexit__(None, None, None)


def test_lifespan_startup_failure_does_not_block_shutdown():
    """A startup hook that raises must not skip the shutdown loop."""
    appFake = _fappBuildFakeApp()
    listShutdownCalls = []

    async def fnFailingStartup(app):
        raise RuntimeError("simulated startup failure")

    async def fnSucceedingStartup(app):
        listShutdownCalls.append("startup-ran")

    async def fnShutdown(app):
        listShutdownCalls.append("shutdown-ran")

    appFake.state.listLifespanStartup = [
        fnFailingStartup, fnSucceedingStartup,
    ]
    appFake.state.listLifespanShutdown = [fnShutdown]

    asyncio.run(_fnDriveLifespanToCompletion(appFake))

    assert "startup-ran" in listShutdownCalls
    assert "shutdown-ran" in listShutdownCalls


def test_lifespan_shutdown_failure_does_not_block_other_shutdowns():
    """A shutdown hook that raises must not skip later shutdown hooks."""
    appFake = _fappBuildFakeApp()
    listShutdownCalls = []

    async def fnFailingShutdown(app):
        raise RuntimeError("simulated shutdown failure")

    async def fnSucceedingShutdown(app):
        listShutdownCalls.append("second-shutdown-ran")

    appFake.state.listLifespanShutdown = [
        fnFailingShutdown, fnSucceedingShutdown,
    ]

    asyncio.run(_fnDriveLifespanToCompletion(appFake))

    assert listShutdownCalls == ["second-shutdown-ran"]


def test_lifespan_logs_startup_failures(caplog):
    """A failing startup hook must emit a warning naming the exception class."""
    appFake = _fappBuildFakeApp()

    async def fnFailingStartup(app):
        raise ValueError("boom")

    appFake.state.listLifespanStartup = [fnFailingStartup]

    with caplog.at_level(logging.WARNING, logger="vaibify"):
        asyncio.run(_fnDriveLifespanToCompletion(appFake))

    listMessages = [recordLog.getMessage() for recordLog in caplog.records]
    assert any("ValueError" in sMessage for sMessage in listMessages), (
        f"expected ValueError in warning logs; got {listMessages}"
    )


def test_lifespan_logs_shutdown_failures(caplog):
    """A failing shutdown hook must emit a warning naming the exception class."""
    appFake = _fappBuildFakeApp()

    async def fnFailingShutdown(app):
        raise KeyError("missing")

    appFake.state.listLifespanShutdown = [fnFailingShutdown]

    with caplog.at_level(logging.WARNING, logger="vaibify"):
        asyncio.run(_fnDriveLifespanToCompletion(appFake))

    listMessages = [recordLog.getMessage() for recordLog in caplog.records]
    assert any("KeyError" in sMessage for sMessage in listMessages), (
        f"expected KeyError in warning logs; got {listMessages}"
    )


def test_lifespan_handles_sync_hook_failures():
    """A synchronous startup hook that raises must not block shutdown."""
    appFake = _fappBuildFakeApp()
    listCalls = []

    def fnFailingSyncStartup(app):
        raise RuntimeError("sync startup failure")

    def fnSyncShutdown(app):
        listCalls.append("sync-shutdown-ran")

    appFake.state.listLifespanStartup = [fnFailingSyncStartup]
    appFake.state.listLifespanShutdown = [fnSyncShutdown]

    asyncio.run(_fnDriveLifespanToCompletion(appFake))

    assert listCalls == ["sync-shutdown-ran"]


# ---------------------------------------------------------------
# Periodic container-cache sweep + ThreadPoolExecutor lifecycle
# ---------------------------------------------------------------


class _FakeDockerForSweep:
    """Minimal docker connection stand-in for sweep tests."""

    def __init__(self, listIds):
        self.listIds = list(listIds)
        self.iListCalls = 0
        self.listEvictedCalls = []

    def flistGetRunningContainers(self):
        self.iListCalls += 1
        return [
            {"sContainerId": sId} for sId in self.listIds
        ]

    def fnEvictAbsentContainers(self, setRunning):
        self.listEvictedCalls.append(set(setRunning))


def test_periodic_sweep_fires_repeatedly_and_evicts_caches():
    """The background loop ticks > once and removes stale entries each tick."""
    fakeDocker = _FakeDockerForSweep(["alive"])
    dictCtx = {
        "docker": fakeDocker,
        "workflows": {"alive": {}, "ghost": {}},
        "paths": {"alive": "", "ghost": ""},
        "containerUsers": {"ghost": ""},
        "pipelineTasks": {},
        "sourceCodeDeps": {"ghost": {}},
        "lastSelfWriteMtimes": {"ghost": 0},
        "lastDiscoveredWorkflows": {"ghost": {}},
        "dictPipelineStateLocks": {},
        "dictSyncEpochs": {"ghost": 1},
        "dictManifestShaCache": {"ghost": {}},
    }
    appFake = _fappBuildFakeApp()

    pipelineServer._fnRegisterPeriodicContainerSweep(
        appFake, dictCtx, fInterval=0.02,
    )

    async def fnDrive():
        contextLifespan = pipelineServer._alifespanShared(appFake)
        await contextLifespan.__aenter__()
        # Let the loop tick at least twice before shutdown.
        await asyncio.sleep(0.08)
        await contextLifespan.__aexit__(None, None, None)
    asyncio.run(fnDrive())

    assert fakeDocker.iListCalls >= 2
    assert "ghost" not in dictCtx["workflows"]
    assert "alive" in dictCtx["workflows"]


def test_periodic_sweep_cancellable_at_shutdown():
    """Shutting down the lifespan stops the sweep task without leaks."""
    fakeDocker = _FakeDockerForSweep([])
    dictCtx = {"docker": fakeDocker}
    appFake = _fappBuildFakeApp()
    pipelineServer._fnRegisterPeriodicContainerSweep(
        appFake, dictCtx, fInterval=0.01,
    )

    async def fnDrive():
        contextLifespan = pipelineServer._alifespanShared(appFake)
        await contextLifespan.__aenter__()
        await asyncio.sleep(0.02)
        await contextLifespan.__aexit__(None, None, None)
        return appFake.state.taskContainerSweep
    taskSweep = asyncio.run(fnDrive())
    assert taskSweep.done()


def test_default_executor_installed_with_io_floor_workers():
    """Lifespan startup replaces the default executor with the vaibify-io pool."""
    appFake = _fappBuildFakeApp()
    pipelineServer._fnRegisterDefaultThreadPoolExecutor(appFake)

    async def fnDrive():
        contextLifespan = pipelineServer._alifespanShared(appFake)
        await contextLifespan.__aenter__()
        executorIo = appFake.state.executorIoThreadPool
        iWorkers = executorIo._max_workers
        await contextLifespan.__aexit__(None, None, None)
        return iWorkers
    iWorkers = asyncio.run(fnDrive())
    assert iWorkers >= pipelineServer.I_VAIBIFY_IO_THREAD_POOL_FLOOR


def test_default_executor_shutdown_clears_handle():
    """Shutdown drops the recorded executor so the next startup is clean."""
    appFake = _fappBuildFakeApp()
    pipelineServer._fnRegisterDefaultThreadPoolExecutor(appFake)

    async def fnDrive():
        contextLifespan = pipelineServer._alifespanShared(appFake)
        await contextLifespan.__aenter__()
        await contextLifespan.__aexit__(None, None, None)
    asyncio.run(fnDrive())
    assert appFake.state.executorIoThreadPool is None


# ---------------------------------------------------------------
# fnPipelineMessageLoop publishes/unpublishes the interactive context
# ---------------------------------------------------------------


def test_pipeline_message_loop_unpublishes_on_exit():
    """The finally block must drop the interactive context registration."""
    import json
    from unittest.mock import AsyncMock, MagicMock, patch

    dictContexts = pipelineServer.DICT_INTERACTIVE_CONTEXTS_BY_CONTAINER
    dictContexts.pop("ctr-finally", None)
    mockWebsocket = AsyncMock()
    listMessages = [json.dumps({"sAction": "runAll"})]
    iIndex = 0

    async def fnReceiveText():
        nonlocal iIndex
        if iIndex < len(listMessages):
            sMsg = listMessages[iIndex]
            iIndex += 1
            return sMsg
        raise RuntimeError("ws-closed")

    mockWebsocket.receive_text = fnReceiveText
    mockWebsocket.send_json = AsyncMock()

    async def fnRun():
        with patch(
            "vaibify.gui.pipelineServer._fnSafeDispatch",
            new_callable=AsyncMock,
        ), patch(
            "vaibify.gui.pipelineRunner.fdictCreateInteractiveContext",
            return_value={"id": "stub"},
        ):
            try:
                await pipelineServer.fnPipelineMessageLoop(
                    mockWebsocket, MagicMock(), "ctr-finally",
                    {}, {}, "/workspace",
                )
            except RuntimeError:
                pass
    asyncio.run(fnRun())
    assert "ctr-finally" not in dictContexts


def test_unpublish_interactive_context_respects_identity():
    """A stale loop's finally must not pop a fresh loop's registration."""
    dictContexts = pipelineServer.DICT_INTERACTIVE_CONTEXTS_BY_CONTAINER
    dictOriginal = {"version": 1}
    dictReplacement = {"version": 2}
    dictContexts["ctr-id"] = dictReplacement
    try:
        pipelineServer._fnUnpublishInteractiveContext(
            "ctr-id", dictOriginal,
        )
        # The replacement is untouched because the identity check failed.
        assert dictContexts["ctr-id"] is dictReplacement
    finally:
        dictContexts.pop("ctr-id", None)
