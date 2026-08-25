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
import pathlib
import shutil
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

# The two host projects the lane's registry carries beside the one
# container. Named here so a journey can address them without
# re-deriving the seed.
S_HOST_PROJECT_READY = "hostLaneReady"
S_HOST_PROJECT_MISSING = "hostLaneMissing"

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


# The workflow the host journey opens and runs. It is a REAL git repo
# with a REAL script, because the point of the host lane is that no
# adapter stands between the dashboard and the machine: the step this
# runs is run by the actual HostConnection, through the actual gated
# and journaled launch, against the actual filesystem.
S_HOST_WORKFLOW_NAME = "hostLaneProject"
S_HOST_STEP_NAME = "MakeNumbers"
S_HOST_STEP_OUTPUT = "numbers.json"
S_HOST_DECLARATION_STEP_NAME = "AI Declaration"

_S_HOST_STEP_SCRIPT = """import argparse
import json

parserArguments = argparse.ArgumentParser()
parserArguments.add_argument("--output", required=True)
namespaceArguments = parserArguments.parse_args()
with open(namespaceArguments.output, "w") as fileOutput:
    json.dump({"listValues": [1, 2, 3]}, fileOutput)
print("wrote " + namespaceArguments.output)
"""


def fdictHostWorkflowDocument():
    """Return the project document the host journey opens."""
    return {
        "sPlotDirectory": "Plot",
        "sFigureType": "png",
        "iNumberOfCores": 1,
        "listSteps": [{
            "sName": S_HOST_STEP_NAME,
            "sStepId": "make-numbers",
            "sDirectory": S_HOST_STEP_NAME,
            "bRunEnabled": True,
            "bPlotOnly": False,
            "saDataCommands": [
                "python3 makeNumbers.py --output " + S_HOST_STEP_OUTPUT,
            ],
            "saOutputDataFiles": [S_HOST_STEP_OUTPUT],
            "saPlotCommands": [],
            "saPlotFiles": [],
        }, {
            # A second step no journey RUNS: the stop test needs a
            # step that can wear an in-flight light while the first
            # step's finished light is backed by (stubbed) server
            # state — one step cannot wear both.
            "sName": "Second Stage",
            "sStepId": "second-stage",
            "sDirectory": "SecondStage",
            "bRunEnabled": True,
            "bPlotOnly": False,
            "saDataCommands": [
                "python3 -c \"print('second stage ran')\"",
            ],
            "saOutputDataFiles": [],
            "saPlotCommands": [],
            "saPlotFiles": [],
        }, {
            # A third step no journey runs, and that CANNOT be run:
            # the declaration kind's command block is empty by
            # construction, which is what the run-light column has to
            # report honestly. Shaped exactly like the one
            # fdictBuildAiDeclarationStep emits, including the
            # slug-conforming directory, so a regression in either
            # lands here.
            "sName": S_HOST_DECLARATION_STEP_NAME,
            "sStepId": "ai-declaration",
            "sDirectory": "AIDeclaration",
            "sStepKind": "ai-declaration",
            "sDeclarationFile": "AI_USAGE.md",
            "bRunEnabled": True,
            "bPlotOnly": False,
            "bInteractive": True,
            "saDataCommands": [],
            "saOutputDataFiles": [],
            "saPlotCommands": [],
            "saPlotFiles": [],
        }],
    }


def fnSeedRunnableHostWorkflow(sProjectDirectory):
    """Make the ready host project a git repo holding a runnable step.

    Every vaibify workflow must live inside a git repository, and the
    project document lives at ``.vaibify/projects/<name>.json`` where
    discovery looks for it. Committing is deliberate: an empty repo has
    no HEAD, and the git badges the workflow view fetches on open would
    then be answering about a repository with no commits rather than
    about an ordinary one.
    """
    import subprocess
    sStepDirectory = os.path.join(sProjectDirectory, S_HOST_STEP_NAME)
    os.makedirs(sStepDirectory, exist_ok=True)
    with open(
        os.path.join(sStepDirectory, "makeNumbers.py"), "w",
    ) as fileScript:
        fileScript.write(_S_HOST_STEP_SCRIPT)
    for sDirectoryName in ("SecondStage", "AIDeclaration"):
        sStageDirectory = os.path.join(
            sProjectDirectory, sDirectoryName,
        )
        os.makedirs(sStageDirectory, exist_ok=True)
        with open(
            os.path.join(sStageDirectory, ".gitkeep"), "w",
        ) as fileKeep:
            fileKeep.write("")
    sProjectsDirectory = os.path.join(
        sProjectDirectory, ".vaibify", "projects",
    )
    os.makedirs(sProjectsDirectory, exist_ok=True)
    with open(
        os.path.join(sProjectsDirectory, S_HOST_WORKFLOW_NAME + ".json"),
        "w",
    ) as fileWorkflow:
        json.dump(fdictHostWorkflowDocument(), fileWorkflow)
    for listCommand in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "lane@example.invalid"],
        ["git", "config", "user.name", "Browser Lane"],
        ["git", "add", "-A"],
        ["git", "-c", "commit.gpgsign=false", "commit", "-q",
         "-m", "seed"],
    ):
        subprocess.run(
            listCommand, cwd=sProjectDirectory, check=True,
            capture_output=True,
        )


# A gitignored scratch root INSIDE the checkout. The project-creation
# path guard permits only directories beneath the user's home, and the
# checkout is beneath it — but a bare temp dir in the repo ROOT gets
# git-added by a later bulk commit when a killed run leaves it behind
# (that is how tmpu2uv9b_9/ shipped into history). Confining the temp
# dirs to a gitignored directory keeps that litter uncommittable while
# still exercising the real path guard.
#
# "the checkout is beneath it" is an ASSUMPTION, and the falsification
# harness breaks it: it runs every mutation inside a disposable git
# worktree under the system temp directory, which is not under $HOME on
# macOS. Any journey touching a $HOME-jailed route then fails there for
# an environment reason rather than a real one — which reports a
# falsification as unconfirmed and reads as an undefended guard
# (2026-08-21). So the root follows the checkout when the checkout is
# under $HOME, and otherwise falls back to a directory that is, keeping
# the path guard genuinely exercised in both places.
#
# The fallback sits UNDER ~/.vaibify deliberately, and moving it out
# would silently drop coverage. A host project rooted below a
# ``.vaibify`` ancestor is the shape that exposed
# fsDeriveRepoRootFromDirectory cutting the repo root at the FIRST
# ``.vaibify`` instead of the project's own: every polled path landed
# under an unrelated ancestor and the host path guard refused each one
# as an escape. Nothing but this fallback drove that path, and it found
# the bug on its first CI run.
def _fpathBrowserLaneTempRoot():
    """Return a scratch root that is under $HOME in every checkout."""
    pathCheckout = pathlib.Path(__file__).resolve().parents[2]
    pathHome = pathlib.Path.home().resolve()
    if pathCheckout == pathHome or pathHome in pathCheckout.parents:
        return pathCheckout / ".browserLaneTmp"
    return pathHome / ".vaibify" / "browserLaneTmp"


PATH_BROWSER_LANE_TMP_ROOT = _fpathBrowserLaneTempRoot()


@contextlib.contextmanager
def _fnIsolateProjectRegistry():
    """Point the global registry at a throwaway directory.

    ``registryManager`` resolves ``~/.vaibify/registry.json`` at import
    time. Without this the lane reads -- and could write -- the
    researcher's real project list, so a CI-shaped test would behave
    differently on a developer machine and could damage live state.
    Seeded with the one container the fake Docker adapter reports, so
    what the browser renders is fully determined by this file, plus
    two host projects. The host entries are here rather than in a
    fixture of their own because the picker renders one list: a host
    tile that behaved correctly in isolation but broke the container
    tile beside it would pass a separate fixture and fail a user.
    One is READY (its directory and config exist) and one is MISSING
    (nothing on disk), which are the two host states the picker has.
    """
    from vaibify.config import registryManager
    # Beneath the user's home (so the wizard exercises the real path
    # guard) but inside a gitignored subdir (so a killed run's leftover
    # can never be committed) — see PATH_BROWSER_LANE_TMP_ROOT.
    PATH_BROWSER_LANE_TMP_ROOT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        dir=str(PATH_BROWSER_LANE_TMP_ROOT),
    ) as sHome:
        sRegistry = os.path.join(sHome, "registry.json")
        sReadyDirectory = os.path.join(sHome, S_HOST_PROJECT_READY)
        os.makedirs(sReadyDirectory, exist_ok=True)
        with open(
            os.path.join(sReadyDirectory, "vaibify.yml"), "w",
        ) as fileConfig:
            fileConfig.write(f"projectName: {S_HOST_PROJECT_READY}\n")
        fnSeedRunnableHostWorkflow(sReadyDirectory)
        with open(sRegistry, "w") as fileHandle:
            json.dump({"listProjects": [{
                "sName": S_CONTAINER_NAME,
                "sContainerName": S_CONTAINER_NAME,
                "sDirectory": os.path.join(sHome, "browserLaneProject"),
                "sConfigPath": os.path.join(
                    sHome, "browserLaneProject", "vaibify.yml",
                ),
            }, {
                "sName": S_HOST_PROJECT_READY,
                "sContainerName": S_HOST_PROJECT_READY,
                "sMode": "host",
                "sDirectory": sReadyDirectory,
                "sConfigPath": os.path.join(
                    sReadyDirectory, "vaibify.yml",
                ),
            }, {
                "sName": S_HOST_PROJECT_MISSING,
                "sContainerName": S_HOST_PROJECT_MISSING,
                "sMode": "host",
                "sDirectory": os.path.join(
                    sHome, S_HOST_PROJECT_MISSING,
                ),
                "sConfigPath": os.path.join(
                    sHome, S_HOST_PROJECT_MISSING, "vaibify.yml",
                ),
            }]}, fileHandle)
        # Preferences are host-global at ~/.vaibify/preferences.json,
        # and the host-warning acknowledgement is a real write to it.
        # Without this the lane records acknowledgements against the
        # researcher's own preferences file, for temp directories that
        # stop existing the moment the run ends.
        #
        # The JOURNAL and lock directories are redirected for the same
        # reason, found the hard way (2026-08-15): the host journeys
        # write real host-exec and terminal records, and a journey
        # that dies mid-run left IN-FLIGHT records in the
        # researcher's real ~/.vaibify/journal — which then wedged
        # every later hub whose startup resolution met them.
        from vaibify.config import preferencesStore
        from vaibify.config import containerLock, operationJournal
        with patch.object(
            registryManager, "_S_REGISTRY_DIRECTORY", sHome,
        ), patch.object(
            registryManager, "_S_REGISTRY_PATH", sRegistry,
        ), patch.object(
            registryManager, "_S_LOCK_PATH",
            os.path.join(sHome, "registry.lock"),
        ), patch.object(
            preferencesStore, "_S_PREFERENCES_DIRECTORY", sHome,
        ), patch.object(
            preferencesStore, "_S_PREFERENCES_PATH",
            os.path.join(sHome, "preferences.json"),
        ), patch.object(
            preferencesStore, "_S_LOCK_PATH",
            os.path.join(sHome, "preferences.lock"),
        ), patch.object(
            operationJournal, "_S_JOURNAL_DIRECTORY",
            os.path.join(sHome, "journal"),
        ), patch.object(
            containerLock, "_S_LOCK_DIRECTORY",
            os.path.join(sHome, "locks"),
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

    from vaibify.gui import browserSession

    adapterDocker = FailClosedDockerAdapter()
    iPort = _fiFreePort()
    with _fnIsolateProjectRegistry() as sHome, patch.object(
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
        sBaseUrl = f"http://127.0.0.1:{iPort}"

        def fsBootstrapUrl():
            """Mint a fresh launch capability and return the bootstrap URL.

            Sweep A retired the shared-token oracle, so the dashboard
            authenticates only by redeeming a capability carried in the URL
            fragment. Each navigation mints its own capability, exactly as
            ``vaibify open`` launches a real browser.
            """
            sCapability = browserSession.fsMintBootstrapCapability(
                app.state.dictBrowserSessions,
            )
            return f"{sBaseUrl}/#bootstrap={sCapability}"

        try:
            _fnWaitUntilServing(iPort)
            yield SimpleNamespace(
                iPort=iPort,
                sBaseUrl=sBaseUrl,
                fsBootstrapUrl=fsBootstrapUrl,
                sHome=sHome,
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


S_ARTIFACT_DIRECTORY = "test-results"


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Stash each phase's report so fixtures can see the outcome."""
    outcome = yield
    setattr(item, f"report_{call.when}", outcome.get_result())


@pytest.fixture
def pageDashboard(browserChromium, serverHub, request):
    """A fresh page that records every console error and page error.

    The recorded lists are attached to the page object so a journey can
    assert on them. Console noise is not filtered: the bar for this
    application is zero errors, because a single ReferenceError means a
    module failed to evaluate and every module below it in load order
    is dead.

    Tracing and video are configured HERE rather than through
    pytest-playwright's ``--tracing`` / ``--video`` options. Those are
    implemented inside the plugin's own ``context`` fixture, and this
    fixture builds its context directly, so passing those flags on the
    command line produced no artifacts at all -- a CI step that
    promised a trace on failure and uploaded an empty directory.
    """
    pathArtifacts = (
        pathlib.Path(S_ARTIFACT_DIRECTORY) / request.node.name
    )
    pathArtifacts.mkdir(parents=True, exist_ok=True)
    contextBrowser = browserChromium.new_context(
        record_video_dir=str(pathArtifacts),
    )
    contextBrowser.tracing.start(screenshots=True, snapshots=True)
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

    bFailed = any(
        getattr(getattr(request.node, f"report_{sPhase}", None),
                "failed", False)
        for sPhase in ("setup", "call")
    )
    if bFailed:
        contextBrowser.tracing.stop(
            path=str(pathArtifacts / "trace.zip")
        )
        page.screenshot(path=str(pathArtifacts / "failure.png"))
        (pathArtifacts / "console.log").write_text(
            "\n".join(page.listConsoleErrors + page.listPageErrors)
        )
        contextBrowser.close()
        return
    contextBrowser.tracing.stop()
    contextBrowser.close()
    shutil.rmtree(pathArtifacts, ignore_errors=True)
