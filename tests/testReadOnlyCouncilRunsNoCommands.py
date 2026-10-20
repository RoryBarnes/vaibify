"""A read-only council runs nothing, in the runner as on the server.

The setting promises a deliberation "for a project that should not be
executed even in a disposable sandbox" (design/agentCouncil.md 6.3.2).
The server half always held: a read-only council reverts every
confirmed claim before the baseline executor runs
(``testReadOnlyCouncilProducesNoConfirmedClaims``). The runner half did
not: every provider ran with the same flags in both modes, and Codex's
``--sandbox read-only`` and Antigravity's ``--sandbox`` restrict writes
and the terminal, not commands, so a participant could still run the
project's scripts. Claude runs in plan mode in both modes and cannot.

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
from vaibify.gui.agentCouncilCodexProvider import (
    LIST_CODEX_READ_ONLY_COUNCIL_FLAGS,
    CodexRunnerConnection,
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
