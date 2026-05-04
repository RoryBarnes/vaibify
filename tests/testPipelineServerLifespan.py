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
