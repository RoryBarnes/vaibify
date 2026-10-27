"""A read-only council runs nothing, in the runner as on the server.

The setting promises a deliberation "for a project that should not be
executed even in a disposable sandbox" (design/agentCouncil.md 6.3.2).
The server half always held: a read-only council reverts every
confirmed claim before the baseline executor runs
(``testReadOnlyCouncilProducesNoConfirmedClaims``). The runner half did
not: every provider ran with the same flags in both modes, and Codex's
``--sandbox read-only`` and Antigravity's ``--sandbox`` restrict writes
and the terminal, not commands, so a participant could still run the
project's scripts. Claude's plan mode does not stop commands either: it
still runs shell commands the model judges read-only, so a read-only
Claude turn removes the Bash tool itself.

The other half is the full-sandbox council, which design section 8.6
says may "write and execute freely inside" its runner. Every provider
once ran self-restricted in that kind too, so these tests pin both
directions, and the chairbot chat, which is read-only in every council.

The engine is driven with the shared fake providers; the two real
provider connections are driven up to the bounded-turn primitive, which
records the argv it was handed and stops. No container and no model.
"""

import asyncio

import pytest

from vaibify.gui import agentCouncilDockerGateway
from vaibify.gui.agentCouncilAntigravityProvider import (
    AntigravityRunnerConnection,
)
from vaibify.gui import agentCouncilProviderRegistry
from vaibify.gui.agentCouncilCodexProvider import (
    LIST_CODEX_READ_ONLY_COUNCIL_FLAGS,
    CodexRunnerConnection,
)
from vaibify.gui.agentCouncilProviders import (
    LIST_CLAUDE_FULL_SANDBOX_FLAGS,
    LIST_CLAUDE_READ_ONLY_FLAGS,
    ClaudeRunnerConnection,
)

from tests.agentCouncilHarness import ffnDecideAllAccept, fixtureBuildCouncil


LIST_TWO_SPECS = [
    {"sHandle": "A", "sProvider": "prov-a", "sRequestedModel": "model-a"},
    {"sHandle": "B", "sProvider": "prov-b", "sRequestedModel": "model-b"},
]


class _ArgvRecorded(Exception):
    """Stops a turn at the primitive once its argv is captured."""


def _flistRequestsOfACouncil(sExecutionPermission):
    fixture = fixtureBuildCouncil(
        LIST_TWO_SPECS, ffnDecideAllAccept,
        dictSettings={"sExecutionPermission": sExecutionPermission},
        sChairbotHandle="A")
    fixture.fdictDrive()
    return fixture.recorder.listRequestLog


@pytest.mark.falsification
def testEveryTurnOfAReadOnlyCouncilSaysSo():
    """Kills: dropping the permission from the turn request."""
    listReadOnly = _flistRequestsOfACouncil("readOnly")
    listFull = _flistRequestsOfACouncil("fullSandbox")
    assert listReadOnly and listFull
    assert all(dictRequest["bReadOnlyCouncil"] is True
               for dictRequest in listReadOnly)
    assert all(dictRequest["bReadOnlyCouncil"] is False
               for dictRequest in listFull)


def _flistArgvOfOneTurn(connectionClass, bReadOnlyCouncil, monkeypatch):
    listArgv = []

    def fnRecordArgv(dictGateway, sHandle, saArgv, *args):
        listArgv.append(list(saArgv))
        raise _ArgvRecorded()

    async def fnNoCleanup():
        return None

    monkeypatch.setattr(
        agentCouncilDockerGateway, "fdictExecuteBoundedTurn", fnRecordArgv)
    connection = connectionClass({}, "campaign-1", "image", b"", "model-x")
    monkeypatch.setattr(
        connection, "_fnDestroyHandleAfterFailure", fnNoCleanup)
    dictRequest = {"sInstructionChannel": "charter",
                   "listQuotedMaterial": [],
                   "bReadOnlyCouncil": bReadOnlyCouncil}
    with pytest.raises(_ArgvRecorded):
        asyncio.run(connection.fnStartTurn(dictRequest))
    return listArgv[0]


def _fbArgvCarriesFlags(saArgv, listFlags):
    iWidth = len(listFlags)
    return any(saArgv[iStart:iStart + iWidth] == listFlags
               for iStart in range(len(saArgv) - iWidth + 1))


@pytest.mark.falsification
def testACodexParticipantInAReadOnlyCouncilHasNoCommandTool(monkeypatch):
    """Kills: composing the read-only turn with the full-sandbox argv."""
    saReadOnly = _flistArgvOfOneTurn(CodexRunnerConnection, True, monkeypatch)
    saFull = _flistArgvOfOneTurn(CodexRunnerConnection, False, monkeypatch)
    assert _fbArgvCarriesFlags(saReadOnly, LIST_CODEX_READ_ONLY_COUNCIL_FLAGS)
    assert not _fbArgvCarriesFlags(saFull, LIST_CODEX_READ_ONLY_COUNCIL_FLAGS)
    assert "--strict-config" in saReadOnly


@pytest.mark.falsification
def testAnAntigravityParticipantInAReadOnlyCouncilPlansOnly(monkeypatch):
    """Kills: leaving Antigravity's terminal available to a read-only turn."""
    saReadOnly = _flistArgvOfOneTurn(
        AntigravityRunnerConnection, True, monkeypatch)
    saFull = _flistArgvOfOneTurn(
        AntigravityRunnerConnection, False, monkeypatch)
    assert _fbArgvCarriesFlags(saReadOnly, ["--mode", "plan"])
    assert not _fbArgvCarriesFlags(saFull, ["--mode", "plan"])


@pytest.mark.falsification
def testAClaudeParticipantInAReadOnlyCouncilHasNoShell(monkeypatch):
    """Kills: a read-only Claude turn that keeps its Bash tool."""
    saReadOnly = _flistArgvOfOneTurn(
        ClaudeRunnerConnection, True, monkeypatch)
    assert _fbArgvCarriesFlags(saReadOnly, LIST_CLAUDE_READ_ONLY_FLAGS)
    sDisallowed = saReadOnly[saReadOnly.index("--disallowedTools") + 1]
    assert "Bash" in sDisallowed.split(",")
    assert saReadOnly[saReadOnly.index("--permission-mode") + 1] == "plan"


@pytest.mark.falsification
def testAClaudeParticipantInAFullSandboxMayEditAndRun(monkeypatch):
    """Kills: a full-sandbox Claude turn left in plan mode."""
    saFull = _flistArgvOfOneTurn(ClaudeRunnerConnection, False, monkeypatch)
    assert _fbArgvCarriesFlags(saFull, LIST_CLAUDE_FULL_SANDBOX_FLAGS)
    assert saFull[saFull.index("--permission-mode") + 1] == (
        "bypassPermissions")
    sDisallowed = saFull[saFull.index("--disallowedTools") + 1]
    assert "Bash" not in sDisallowed.split(",")
    assert "WebFetch" in sDisallowed.split(",")


@pytest.mark.falsification
def testTheClaudeConnectionPassesTheCouncilKindToItsArgv(monkeypatch):
    """Kills: the Claude turn composing its argv without the kind."""
    saReadOnly = _flistArgvOfOneTurn(
        ClaudeRunnerConnection, True, monkeypatch)
    saFull = _flistArgvOfOneTurn(ClaudeRunnerConnection, False, monkeypatch)
    assert saReadOnly != saFull


@pytest.mark.falsification
def testACodexParticipantInAFullSandboxWritesItsWorkspace(monkeypatch):
    """Kills: a full-sandbox Codex turn kept in the read-only sandbox."""
    saReadOnly = _flistArgvOfOneTurn(CodexRunnerConnection, True, monkeypatch)
    saFull = _flistArgvOfOneTurn(CodexRunnerConnection, False, monkeypatch)
    assert saFull[saFull.index("--sandbox") + 1] == "workspace-write"
    assert saReadOnly[saReadOnly.index("--sandbox") + 1] == "read-only"


@pytest.mark.falsification
def testEveryCodexTurnSearchesTheWebLive(monkeypatch):
    """Kills: Codex web search left disabled in either council kind."""
    for bReadOnlyCouncil in (True, False):
        saArgv = _flistArgvOfOneTurn(
            CodexRunnerConnection, bReadOnlyCouncil, monkeypatch)
        assert _fbArgvCarriesFlags(saArgv, ["-c", 'web_search="live"'])


def _flistChatArgv(sProvider):
    return agentCouncilProviderRegistry.flistComposeProviderChatArgv(
        sProvider, "model-x", "charter")


@pytest.mark.falsification
def testTheChairbotChatOverClaudeHasNoShell():
    """Kills: the Claude chat composed with the full-sandbox flags."""
    assert _fbArgvCarriesFlags(
        _flistChatArgv("claude"), LIST_CLAUDE_READ_ONLY_FLAGS)


@pytest.mark.falsification
def testTheChairbotChatOverCodexHasNoCommandTool():
    """Kills: the Codex chat keeping its shell tools."""
    saChat = _flistChatArgv("codex")
    assert _fbArgvCarriesFlags(saChat, LIST_CODEX_READ_ONLY_COUNCIL_FLAGS)
    assert saChat[saChat.index("--sandbox") + 1] == "read-only"
    assert _fbArgvCarriesFlags(saChat, ["-c", 'web_search="live"'])


@pytest.mark.falsification
def testTheChairbotChatOverAntigravityPlansOnly():
    """Kills: the Antigravity chat run without plan mode."""
    assert _fbArgvCarriesFlags(_flistChatArgv("gemini"), ["--mode", "plan"])
