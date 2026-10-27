"""The real Claude Code, in a real container, stays off the alternate screen.

vaibify sets ``CLAUDE_CODE_DISABLE_ALTERNATE_SCREEN=1`` on every shell
it opens (``vaibify/config/agentTerminalEnvironment.py``) so Claude Code
draws with its classic renderer, which writes into the pane's
scrollback. Whether Claude still honors that variable is upstream's
decision, and containers auto-update the agent, so this canary asks the
installed CLI itself, nightly.

It never asserts Claude's wording, only terminal behavior, and it has
three outcomes, never two:

* the CONTROL run forces the fullscreen renderer
  (``CLAUDE_CODE_NO_FLICKER=1``) and must enter the alternate screen
  (``ESC[?1049h``). That positively establishes the renderer was
  reached and fullscreen is what it would draw. If it does not, the
  run is INCONCLUSIVE -- a login prompt, an early exit, or an upstream
  change to the forcing variable -- and the test skips saying so,
  never passes;
* the SWITCHED run adds vaibify's own environment to the same request.
  Entering any alternate screen (modes 47, 1047 or 1049, alone or
  combined with others in one sequence) FAILS, whatever else the output
  shows. Otherwise it must have reached its prompt (bracketed paste,
  ``ESC[?2004h``) to pass; output that did neither is inconclusive.
  ``fsJudgeTheSwitchedRun`` makes that call, and
  ``tests/testClaudeCodeRendererVerdict.py`` pins it on every pull
  request;
* the DEFAULT run, with neither variable, is recorded and printed,
  never asserted: whether the default is fullscreen is Claude's choice.

Measured 2026-10-26 with Claude Code 2.1.311: without credentials the
CLI stops at its first-run theme picker in every configuration, so the
container seeds the CLI's own config as already onboarded, with a
dummy API key pre-approved and the directory trusted, and runs with no
network, so nothing leaves the machine and no model is called.
Measured outcomes then: default classic; ``NO_FLICKER=1`` alternate
screen plus mouse capture; ``NO_FLICKER=1`` with the switch, classic.

Each run drives vaibify's own terminal exec (``fsExecCreate`` with its
``dictEnvironment``), the call the dashboard terminal makes, in a fresh
container so no run inherits another's state.

Kill confirmed by hand on 2026-10-26, not through the registry (the
falsification lane would have to install the CLI from npm): with
vaibify's environment dropped from the switched run, the assertion
failed on ``ESC[?1049h``.

Skipped when no daemon answers, unless ``VAIBIFY_REQUIRE_DOCKER_DAEMON``
demands one. Installing the CLI from npm costs minutes, so the
``agent_cli_live`` marker keeps this out of the per-PR docker-smoke
lane; the nightly container-acceptance workflow runs it.
"""

import io
import json
import re
import select
import time

import pytest

from tests.liveContainerLabels import fdictLabels
from tests.testDockerConnectionLive import fnRequireDaemonReachable
from vaibify.config.agentTerminalEnvironment import (
    fdictBuildAgentTerminalEnvironment,
)

pytestmark = [pytest.mark.docker_live, pytest.mark.agent_cli_live]

S_FIXTURE_TAG = "vaibify-claude-renderer:live"
S_DOCKERFILE = """FROM node:22-slim
RUN npm install -g @anthropic-ai/claude-code
RUN useradd -m researcher
USER researcher
WORKDIR /home/researcher
"""
S_HOME = "/home/researcher"
S_DUMMY_API_KEY = "vaibify-canary-not-a-credential-0000000000000000"
F_CAPTURE_SECONDS = 12.0
I_ROWS = 40
I_COLUMNS = 120
# Every mode the vendored xterm switches to its alternate buffer on, not
# only the 1049 Claude uses today.
SET_ALTERNATE_SCREEN_MODES = frozenset({"47", "1047", "1049"})
S_BRACKETED_PASTE_MODE = "2004"
S_VERDICT_ENTERED_ALTERNATE_SCREEN = "entered the alternate screen"
S_VERDICT_NEVER_REACHED_PROMPT = "never reached its prompt"
S_VERDICT_STAYED_CLASSIC = "stayed in the classic renderer"
DICT_FORCE_FULLSCREEN = {"CLAUDE_CODE_NO_FLICKER": "1"}


def _fsBuildSeededConfig():
    """Return the CLI's config as an onboarded, key-approved, trusted user."""
    return json.dumps({
        "hasCompletedOnboarding": True,
        "theme": "dark",
        "customApiKeyResponses": {
            "approved": [S_DUMMY_API_KEY[-20:]], "rejected": [],
        },
        "projects": {S_HOME: {"hasTrustDialogAccepted": True}},
    })


def _fnBuildFixtureImage(clientDocker):
    import docker
    try:
        clientDocker.images.get(S_FIXTURE_TAG)
        return
    except docker.errors.ImageNotFound:
        pass
    clientDocker.images.build(
        fileobj=io.BytesIO(S_DOCKERFILE.encode()), tag=S_FIXTURE_TAG,
        rm=True, labels=fdictLabels(),
    )


@pytest.fixture(scope="module")
def tDockerClients():
    """Return (docker client, DockerConnection) with the image built."""
    fnRequireDaemonReachable()
    import docker
    from vaibify.docker.dockerConnection import (
        DockerConnection, _fnEnsureDockerHost,
    )
    _fnEnsureDockerHost()
    clientDocker = docker.from_env()
    _fnBuildFixtureImage(clientDocker)
    return clientDocker, DockerConnection()


def _fbaReadFor(socketExec, fSeconds):
    """Return everything the exec's terminal printed within fSeconds."""
    socketRaw = getattr(socketExec, "_sock", socketExec)
    baCollected = b""
    fDeadline = time.monotonic() + fSeconds
    while time.monotonic() < fDeadline:
        listReady, _, _ = select.select([socketRaw], [], [], 0.2)
        if not listReady:
            continue
        baChunk = socketRaw.recv(65536)
        if not baChunk:
            break
        baCollected += baChunk
    return baCollected


def _fbaCaptureClaude(tDockerClients, dictEnvironment):
    """Run `claude` in a fresh, network-less container; return its output."""
    clientDocker, connectionDocker = tDockerClients
    container = clientDocker.containers.run(
        S_FIXTURE_TAG, ["sleep", "300"], detach=True, network_mode="none",
        labels=fdictLabels(),
    )
    try:
        container.exec_run([
            "sh", "-c", f"printf '%s' '{_fsBuildSeededConfig()}' "
            f"> {S_HOME}/.claude.json"])
        dictExecEnvironment = {
            "TERM": "xterm-256color", "ANTHROPIC_API_KEY": S_DUMMY_API_KEY,
        }
        dictExecEnvironment.update(dictEnvironment)
        sExecId = connectionDocker.fsExecCreate(
            container.id, listCommand=["claude"],
            dictEnvironment=dictExecEnvironment)
        socketExec = connectionDocker.fsocketExecStart(sExecId)
        try:
            connectionDocker.fnExecResize(sExecId, I_ROWS, I_COLUMNS)
            return _fbaReadFor(socketExec, F_CAPTURE_SECONDS)
        finally:
            socketExec.close()
    finally:
        container.remove(force=True)


def fsetModesSet(baOutput):
    """Return every DEC private mode the output switched on.

    Parsed, never byte-matched: one sequence may set several modes
    (``ESC[?1049;1006h``), and a byte match for ``ESC[?1049h`` misses it.
    """
    setModes = set()
    for baModes in re.findall(rb"\x1b\[\?([0-9;]+)h", baOutput):
        setModes.update(baModes.decode("ascii").split(";"))
    return setModes


def fbEnteredTheAlternateScreen(baOutput):
    """Return True when the output switched to an alternate screen."""
    return bool(fsetModesSet(baOutput) & SET_ALTERNATE_SCREEN_MODES)


def fsJudgeTheSwitchedRun(baSwitched):
    """Return what the run with vaibify's environment establishes.

    The alternate screen is judged FIRST. Output that entered it is a
    failure whether or not the CLI also reached its prompt; only output
    that did NOT enter it needs the prompt as evidence that the renderer
    ran at all, and without that evidence the run is inconclusive.
    """
    if fbEnteredTheAlternateScreen(baSwitched):
        return S_VERDICT_ENTERED_ALTERNATE_SCREEN
    if S_BRACKETED_PASTE_MODE not in fsetModesSet(baSwitched):
        return S_VERDICT_NEVER_REACHED_PROMPT
    return S_VERDICT_STAYED_CLASSIC


def _fsDescribe(sLabel, baOutput):
    """Return a diagnostic line: modes set and the text, escapes removed."""
    sText = re.sub(
        rb"\x1b\[[0-9;?>]*[ -/]*[@-~]|\x1b\][^\x07]*\x07", b"", baOutput,
    ).decode("utf-8", "replace")
    return (f"{sLabel}: {len(baOutput)} bytes, modes set "
            f"{sorted(fsetModesSet(baOutput))}, text "
            f"{' '.join(sText.split())[:300]!r}")


def test_vaibifys_switch_keeps_claude_off_the_alternate_screen(
    tDockerClients,
):
    """With vaibify's environment, the forced fullscreen renderer is refused.

    Inconclusive (skipped, saying why) unless the control run proves
    fullscreen is what Claude would otherwise draw.
    """
    baControl = _fbaCaptureClaude(tDockerClients, DICT_FORCE_FULLSCREEN)
    print(_fsDescribe("control (fullscreen forced)", baControl))
    if not fbEnteredTheAlternateScreen(baControl):
        pytest.skip(
            "INCONCLUSIVE: forcing the fullscreen renderer did not enter "
            "the alternate screen, so this run cannot tell whether "
            "vaibify's switch works. Claude may have stopped at a login "
            "or onboarding screen, exited early, or changed how "
            "fullscreen is forced. " + _fsDescribe("control", baControl))
    dictSwitched = dict(DICT_FORCE_FULLSCREEN)
    dictSwitched.update(fdictBuildAgentTerminalEnvironment())
    baSwitched = _fbaCaptureClaude(tDockerClients, dictSwitched)
    print(_fsDescribe("switched (vaibify's environment)", baSwitched))
    baDefault = _fbaCaptureClaude(tDockerClients, {})
    print(_fsDescribe("default (no variables)", baDefault))
    sVerdict = fsJudgeTheSwitchedRun(baSwitched)
    assert sVerdict != S_VERDICT_ENTERED_ALTERNATE_SCREEN, (
        "Claude Code entered the alternate screen despite vaibify's "
        "CLAUDE_CODE_DISABLE_ALTERNATE_SCREEN=1: the dashboard terminal "
        "will lose scrollback and selection under it again. "
        + _fsDescribe("switched", baSwitched)
    )
    if sVerdict == S_VERDICT_NEVER_REACHED_PROMPT:
        pytest.skip(
            "INCONCLUSIVE: with vaibify's environment the CLI never "
            "reached its prompt, so the absence of the alternate screen "
            "would prove nothing. " + _fsDescribe("switched", baSwitched))
