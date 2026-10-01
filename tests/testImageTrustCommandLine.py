"""The command line asks the same question as the dashboard, never silently.

Both read ``vaibify.config.imageTrust``. Without a terminal the answer
must arrive as flags; with one, the three options are shown with the
same text and the credentials question defaults to no.
"""

import os

import click
import pytest

from tests.testContainerLifecycleGating import (  # noqa: F401
    fixtureIsolateHostState,
)
from vaibify.cli import imageTrustPrompt
from vaibify.config import imageTrust, registryManager
from vaibify.docker import containerManager

S_PROJECT = "cliProject"
S_DIGEST = "sha256:" + "d" * 64


def fdictPromptFor(bBuilt=False, dictAnswer=None):
    dictPrompt = imageTrust.fdictBuildTrustPrompt(
        {"dictImageTrust": dictAnswer or {}},
        {"sId": S_DIGEST, "dictLabels": {}, "iSizeBytes": 10,
         "sUser": "root", "listEntrypoint": ["/start"]}, None)
    dictPrompt["bBuiltByVaibify"] = bBuilt
    return dictPrompt


@pytest.fixture
def listRecorded(monkeypatch):
    listRecords = []
    monkeypatch.setattr(
        imageTrustPrompt, "fnRecordImageTrust",
        lambda sName, dictRecord: listRecords.append((sName, dictRecord)))
    return listRecords


def fnServePrompt(monkeypatch, dictPrompt):
    monkeypatch.setattr(
        containerManager, "fdictBuildImageTrustPromptForProject",
        lambda sName: dictPrompt)


def fconfigNamed():
    return type("Config", (), {"sProjectName": S_PROJECT})()


def fnSetTerminal(monkeypatch, bInteractive):
    monkeypatch.setattr(
        imageTrustPrompt.sys.stdin, "isatty", lambda: bInteractive,
        raising=False)
    monkeypatch.setattr(
        imageTrustPrompt.sys.stdout, "isatty", lambda: bInteractive,
        raising=False)


@pytest.mark.falsification
def testWithoutATerminalAnUnansweredImageStopsAndNamesTheFlags(
    monkeypatch, listRecorded, capsys,
):
    """Kills: choosing a default when there is nobody to ask."""
    fnServePrompt(monkeypatch, fdictPromptFor())
    fnSetTerminal(monkeypatch, False)
    with pytest.raises(SystemExit) as errorExit:
        imageTrustPrompt.fnConfirmImageTrustOrExit(
            fconfigNamed(), None, False)
    assert errorExit.value.code == 2
    sErrors = capsys.readouterr().err
    assert "--image-trust" in sErrors and "--with-credentials" in sErrors
    assert listRecorded == []


def testTheFlagsRecordTheAnswerForTheDigest(monkeypatch, listRecorded):
    fnServePrompt(monkeypatch, fdictPromptFor())
    imageTrustPrompt.fnConfirmImageTrustOrExit(
        fconfigNamed(), "restricted", True)
    sName, dictRecord = listRecorded[0]
    assert sName == S_PROJECT
    assert dictRecord["sImageDigest"] == S_DIGEST
    assert dictRecord["sChoice"] == "restricted"
    assert dictRecord["bWithCredentials"] is True


def testCredentialsNeverTravelWithoutAChoice(monkeypatch, listRecorded):
    fnServePrompt(monkeypatch, fdictPromptFor())
    with pytest.raises(click.UsageError):
        imageTrustPrompt.fnConfirmImageTrustOrExit(
            fconfigNamed(), None, True)


def testAnImageVaibifyBuiltAsksNothing(monkeypatch, listRecorded):
    fnServePrompt(monkeypatch, fdictPromptFor(bBuilt=True))
    fnSetTerminal(monkeypatch, False)
    imageTrustPrompt.fnConfirmImageTrustOrExit(fconfigNamed(), None, False)
    assert listRecorded == []


def testAnAnsweredDigestAsksNothing(monkeypatch, listRecorded):
    fnServePrompt(monkeypatch, fdictPromptFor(dictAnswer={
        "sImageDigest": S_DIGEST, "sChoice": "restricted"}))
    fnSetTerminal(monkeypatch, False)
    imageTrustPrompt.fnConfirmImageTrustOrExit(fconfigNamed(), None, False)
    assert listRecorded == []


def testInteractivelyTheSameThreeOptionsAreShownAndCredentialsDefaultToNo(
    monkeypatch, listRecorded, capsys,
):
    fnServePrompt(monkeypatch, fdictPromptFor())
    fnSetTerminal(monkeypatch, True)
    listAnswers = iter(["2", ""])
    monkeypatch.setattr(
        "click.termui.visible_prompt_func", lambda sText: next(listAnswers))
    imageTrustPrompt.fnConfirmImageTrustOrExit(fconfigNamed(), None, False)
    sShown = capsys.readouterr().out
    for dictOption in imageTrust.LIST_TRUST_OPTIONS:
        assert dictOption["sLabel"] in sShown
        assert dictOption["listDetailLines"][0] in sShown
    assert listRecorded[0][1]["sChoice"] == "as-built"
    assert listRecorded[0][1]["bWithCredentials"] is False


def testInspectOnlyRecordsAndCreatesNoContainer(monkeypatch, listRecorded):
    fnServePrompt(monkeypatch, fdictPromptFor())
    with pytest.raises(SystemExit) as errorExit:
        imageTrustPrompt.fnConfirmImageTrustOrExit(
            fconfigNamed(), "inspect", True)
    assert errorExit.value.code == 0
    assert listRecorded[0][1]["sChoice"] == "inspect"
    assert listRecorded[0][1]["bWithCredentials"] is False
