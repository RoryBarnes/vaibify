"""Fixtures for the browser lane: a real server, a real Chromium.

The application under test is the real hub built by
``fappCreateHubApplication`` and served by a real uvicorn on an
ephemeral port, so the browser loads the actual IIFE modules, fetches
its own session token, and opens real WebSockets. Only the Docker
adapter is replaced, and only through a test-setup patch of
``pipelineServer._fconnectionCreateDocker`` -- never a production
parameter, environment variable, or "test mode". A runtime switch that
swapped the Docker adapter would be an attack surface, not a
convenience.

``iExpectedPort`` is passed for real, so the DNS-rebinding Host check
runs rather than being opted out of the way the in-process TestClient
harness does.
"""

import contextlib
import json
import os
import socket
import tempfile
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tests.browser.fakeDockerAdapter import (
    FailClosedDockerAdapter,
    S_CONTAINER_NAME,
)


S_REQUIRE_BROWSER_ENV = "VAIBIFY_REQUIRE_BROWSER"

pytestmark = pytest.mark.browser


def _fnRequirePlaywright():
    """Import Playwright, or skip -- unless the run demanded it.

    Same contract as the live-Docker lane: a CI job whose whole purpose
    is browser coverage must not report success because the browser was
    missing.
    """
    try:
        import playwright.sync_api  # noqa: F401
        return
    except ImportError:
        pass
    if os.environ.get(S_REQUIRE_BROWSER_ENV):
        pytest.fail(
            "Playwright is not installed, but "
            f"{S_REQUIRE_BROWSER_ENV} is set: this run was required to "
            "exercise the frontend, so skipping would report a false "
            "green. Install with: pip install -e '.[browser]' && "
            "python -m playwright install chromium"
        )
    pytest.skip(
        "playwright not installed; pip install -e '.[browser]' && "
        "python -m playwright install chromium"
    )


def _fiFreePort():
    """Return a port the OS has just confirmed is free."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _fnWaitUntilServing(iPort, fTimeoutSeconds=20.0):
    """Block until the port accepts a connection, or fail loudly."""
    fDeadline = time.monotonic() + fTimeoutSeconds
    while time.monotonic() < fDeadline:
        try:
            with socket.create_connection(
                ("127.0.0.1", iPort), timeout=0.5,
            ):
                return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError(
        f"The hub never started listening on port {iPort}."
    )


@contextlib.contextmanager
def _fnIsolateProjectRegistry():
    """Point the global registry at a throwaway directory.

    ``registryManager`` resolves ``~/.vaibify/registry.json`` at import
    time. Without this the lane reads -- and could write -- the
    researcher's real project list, so a CI-shaped test would behave
    differently on a developer machine and could damage live state.
    Seeded with the one container the fake Docker adapter reports, so
    what the browser renders is fully determined by this file.
    """
    from vaibify.config import registryManager
    with tempfile.TemporaryDirectory() as sHome:
        sRegistry = os.path.join(sHome, "registry.json")
        with open(sRegistry, "w") as fileHandle:
            json.dump({"listProjects": [{
                "sName": S_CONTAINER_NAME,
                "sContainerName": S_CONTAINER_NAME,
                "sDirectory": os.path.join(sHome, "browserLaneProject"),
                "sConfigPath": os.path.join(
                    sHome, "browserLaneProject", "vaibify.yml",
                ),
            }]}, fileHandle)
        with patch.object(
            registryManager, "_S_REGISTRY_DIRECTORY", sHome,
        ), patch.object(
            registryManager, "_S_REGISTRY_PATH", sRegistry,
        ), patch.object(
            registryManager, "_S_LOCK_PATH",
            os.path.join(sHome, "registry.lock"),
        ):
            yield sHome


# Module scope, NOT session scope. These fixtures hold `patch.object`
# on pipelineServer and registryManager open across the yield; at
# session scope those patches stay live while every other test module
# runs, which silently redirected the whole suite's Docker factory and
# project registry. Module scope tears them down as soon as the browser
# module finishes.
@pytest.fixture(scope="module")
def serverHub():
    """Serve the real hub over the fail-closed Docker adapter."""
    _fnRequirePlaywright()
    import uvicorn
    from vaibify.gui import pipelineServer
    from vaibify.gui.appFactory import fappCreateHubApplication

    adapterDocker = FailClosedDockerAdapter()
    iPort = _fiFreePort()
    with _fnIsolateProjectRegistry(), patch.object(
        pipelineServer, "_fconnectionCreateDocker",
        lambda *args, **kwargs: adapterDocker,
    ):
        app = fappCreateHubApplication(iExpectedPort=iPort)
        configServer = uvicorn.Config(
            app, host="127.0.0.1", port=iPort, log_level="warning",
        )
        server = uvicorn.Server(configServer)
        threadServer = threading.Thread(target=server.run, daemon=True)
        threadServer.start()
        try:
            _fnWaitUntilServing(iPort)
            yield SimpleNamespace(
                iPort=iPort,
                sBaseUrl=f"http://127.0.0.1:{iPort}",
                adapterDocker=adapterDocker,
                app=app,
            )
        finally:
            server.should_exit = True
            threadServer.join(timeout=10)


@pytest.fixture(scope="module")
def browserChromium():
    """A single headless Chromium for the whole lane."""
    _fnRequirePlaywright()
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwrightDriver:
        browser = playwrightDriver.chromium.launch()
        yield browser
        browser.close()


@pytest.fixture
def pageDashboard(browserChromium, serverHub):
    """A fresh page that records every console error and page error.

    The recorded lists are attached to the page object so a journey can
    assert on them. Console noise is not filtered: the bar for this
    application is zero errors, because a single ReferenceError means a
    module failed to evaluate and every module below it in load order
    is dead.
    """
    contextBrowser = browserChromium.new_context()
    page = contextBrowser.new_page()
    page.listConsoleErrors = []
    page.listPageErrors = []
    page.on("console", lambda message: (
        page.listConsoleErrors.append(message.text)
        if message.type == "error" else None
    ))
    page.on("pageerror", lambda error: page.listPageErrors.append(
        str(error)
    ))
    yield page
    contextBrowser.close()
