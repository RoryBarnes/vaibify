"""The command line asks the same reach question as the dashboard, never silently.

Both read ``vaibify.config.reachApproval``. Without a terminal the
approval must arrive as ``--approve-reach``; with one, the prompt is
shown and the answer defaults to no.
"""

from types import SimpleNamespace

import pytest

from tests.testContainerLifecycleGating import (  # noqa: F401
    fixtureIsolateHostState,
)
from vaibify.cli import reachApprovalPrompt
from vaibify.config import reachApproval
from vaibify.docker import containerManager

S_PROJECT = "cliReachProject"


def fconfigOpen():
    return SimpleNamespace(
        sProjectName=S_PROJECT, sNetworkMode="open", bX11Forwarding=False,
        listBindMounts=[], listAllowedHosts=[])


@pytest.fixture
def listRecorded(monkeypatch):
    listRecords = []
    monkeypatch.setattr(
        reachApprovalPrompt, "fnRecordReachApproval",
        lambda sName, dictRecord: listRecords.append((sName, dictRecord)))
    return listRecords


def fnServeRegistryEntry(monkeypatch, dictEntry):
    monkeypatch.setattr(
        containerManager, "_fdictRegisteredProjectOrNone", lambda sName: dictEntry)


def fnSetTerminal(monkeypatch, bInteractive):
    monkeypatch.setattr(
        reachApprovalPrompt.sys.stdin, "isatty", lambda: bInteractive, raising=False)
    monkeypatch.setattr(
        reachApprovalPrompt.sys.stdout, "isatty", lambda: bInteractive, raising=False)


@pytest.mark.falsification
def testWithoutATerminalAnUnapprovedConfigurationStopsAndNamesTheFlag(
    monkeypatch, listRecorded, capsys,
):
    """Kills: approving by default when there is nobody to ask."""
    fnServeRegistryEntry(monkeypatch, {"sName": S_PROJECT})
    fnSetTerminal(monkeypatch, False)
    with pytest.raises(SystemExit) as errorExit:
        reachApprovalPrompt.fnConfirmReachOrExit(fconfigOpen(), False)
    assert errorExit.value.code == 2
    tCaptured = capsys.readouterr()
    assert "--approve-reach" in tCaptured.err
    assert "Network mode open" in tCaptured.out
    assert listRecorded == []


def testTheFlagRecordsTheApprovalForTheFile(monkeypatch, listRecorded):
    fnServeRegistryEntry(monkeypatch, {"sName": S_PROJECT})
    fnSetTerminal(monkeypatch, False)
    reachApprovalPrompt.fnConfirmReachOrExit(fconfigOpen(), True)
    sName, dictRecord = listRecorded[0]
    assert sName == S_PROJECT
    assert dictRecord["sReachFingerprint"] == reachApproval.fsComputeReachFingerprint(
        fconfigOpen())


def testAnInteractiveNoDoesNotStartAndRecordsNothing(monkeypatch, listRecorded):
    fnServeRegistryEntry(monkeypatch, {"sName": S_PROJECT})
    fnSetTerminal(monkeypatch, True)
    monkeypatch.setattr("click.termui.visible_prompt_func", lambda sText: "n")
    with pytest.raises(SystemExit) as errorExit:
        reachApprovalPrompt.fnConfirmReachOrExit(fconfigOpen(), False)
    assert errorExit.value.code == 2
    assert listRecorded == []
    monkeypatch.setattr("click.termui.visible_prompt_func", lambda sText: "y")
    reachApprovalPrompt.fnConfirmReachOrExit(fconfigOpen(), False)
    assert len(listRecorded) == 1


def testACoveredOrNonWideningConfigurationAsksNothing(monkeypatch, listRecorded):
    fnServeRegistryEntry(monkeypatch, {"sName": S_PROJECT})
    fnSetTerminal(monkeypatch, False)
    reachApprovalPrompt.fnConfirmReachOrExit(
        SimpleNamespace(sProjectName=S_PROJECT, sNetworkMode="web", bX11Forwarding=False,
                        listBindMounts=[], listAllowedHosts=[]), False)
    fnServeRegistryEntry(monkeypatch, {
        "sName": S_PROJECT,
        "dictReachApproval": reachApproval.fdictBuildReachApprovalRecord(fconfigOpen())})
    reachApprovalPrompt.fnConfirmReachOrExit(fconfigOpen(), False)
    assert listRecorded == []


def testAnUnregisteredProjectStopsNamingTheRemedy(monkeypatch, listRecorded, capsys):
    fnServeRegistryEntry(monkeypatch, None)
    with pytest.raises(SystemExit) as errorExit:
        reachApprovalPrompt.fnConfirmReachOrExit(fconfigOpen(), True)
    assert errorExit.value.code == 2
    assert "register it first" in capsys.readouterr().err
    assert listRecorded == []
