"""``vaibify setup-docker`` walks the same plan as the dashboard's wizard.

The machine is fixture evidence that changes as steps run, so each test
reads like a session: what the machine had, what the command ran, what
it said. The runner is a recorder that applies the step's effect to the
facts, which is what lets "a step that changed nothing stops the
command" be tested at all.
"""

import pytest
from click.testing import CliRunner

from tests.testRuntimeSetupPlan import I_GIGABYTE, fdictMacFacts
from vaibify.cli import commandSetupDocker
from vaibify.cli.main import main


@pytest.fixture
def dictSession(monkeypatch):
    dictSession = {"dictFacts": fdictMacFacts(
        sPrivilegeChannel="passwordless-sudo"), "listRuns": [],
        "dictEffects": {}}
    monkeypatch.setattr(commandSetupDocker, "fdictReadHostFacts",
                        lambda: dict(dictSession["dictFacts"]))

    def fnRecordRun(dictPlan, sActionName, sChannel, fnEmitLine,
                    bInteractive=False):
        dictSession["listRuns"].append((sActionName, sChannel))
        if len(dictSession["listRuns"]) > 5:
            raise RuntimeError("the command kept running the same step")
        dictSession["dictFacts"].update(
            dictSession["dictEffects"].get(sActionName, {}))

    monkeypatch.setattr(commandSetupDocker, "fnRunAction", fnRecordRun)
    return dictSession


DICT_INSTALLED = {"listCommands": ["brew", "colima", "docker"],
                  "bBuildxWorks": True}
DICT_RUNNING = {"bDaemonAnswers": True, "sRuntime": "colima",
                "listColimaMachines": [{
                    "sProfile": "default", "sStatus": "Running", "iCpus": 9,
                    "iMemoryBytes": 8 * I_GIGABYTE, "sArchitecture": "aarch64"}]}


def fresultInvoke(listArguments):
    return CliRunner().invoke(main, ["setup-docker"] + listArguments)


def testAFreshMacIsInstalledThenStartedThenReady(dictSession):
    dictSession["dictEffects"] = {
        "install-with-homebrew": DICT_INSTALLED,
        "start-new-colima": DICT_RUNNING,
    }
    resultCli = fresultInvoke(["--yes"])
    assert resultCli.exit_code == 0, resultCli.output
    assert dictSession["listRuns"] == [
        ("install-with-homebrew", "passwordless-sudo"),
        ("start-new-colima", "passwordless-sudo")]
    assert resultCli.output.rstrip().endswith("Docker is ready for vaibify.")


@pytest.mark.falsification
def testAStepThatChangesNothingStopsTheCommand(dictSession):
    """Kills: running the same step again and again."""
    resultCli = fresultInvoke(["--yes"])
    assert resultCli.exit_code == 1
    assert dictSession["listRuns"] == [
        ("install-with-homebrew", "passwordless-sudo")]
    assert "ran but the machine still needs it" in resultCli.output


def testNoStartStopsBeforeStartingARuntime(dictSession):
    dictSession["dictEffects"] = {"install-with-homebrew": DICT_INSTALLED}
    resultCli = fresultInvoke(["--yes", "--no-start"])
    assert resultCli.exit_code == 0, resultCli.output
    assert [t[0] for t in dictSession["listRuns"]] == ["install-with-homebrew"]
    assert "Stopping before this step (--no-start)." in resultCli.output


def testAStepOnlyTheResearcherCanDoExitsTwoAndRunsNothing(dictSession):
    dictSession["dictFacts"] = fdictMacFacts(
        listCommands=["osascript"], sHomebrewPrefix="")
    resultCli = fresultInvoke(["--yes"])
    assert resultCli.exit_code == commandSetupDocker.I_EXIT_NEEDS_YOU
    assert dictSession["listRuns"] == []
    assert "Install a package manager" in resultCli.output


def testYesNeverRestartsAMachineForTheOptionalStep(dictSession):
    dictSession["dictFacts"] = fdictMacFacts(**dict(
        DICT_INSTALLED, bDaemonAnswers=True, sRuntime="colima",
        listColimaMachines=[{
            "sProfile": "default", "sStatus": "Running", "iCpus": 2,
            "iMemoryBytes": 2 * I_GIGABYTE, "sArchitecture": "aarch64"}]))
    resultCli = fresultInvoke(["--yes"])
    assert resultCli.exit_code == 0, resultCli.output
    assert dictSession["listRuns"] == []
    assert "Docker is ready. Optional: Give Colima more memory" in (
        resultCli.output)


def testASettingTheMachineCannotHoldIsRefusedBeforeAnythingRuns(dictSession):
    resultCli = fresultInvoke(["--yes", "--memory", "64"])
    assert resultCli.exit_code == 2
    assert "between 2 and 16" in resultCli.output
    assert dictSession["listRuns"] == []
