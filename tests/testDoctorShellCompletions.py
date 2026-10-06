"""``vaibify doctor`` says whether TAB completion is wired, and how to fix it.

Setup appends the line that loads the completion script, once, silently.
When it could not -- an unwritable file, a different login shell then --
the researcher's only symptom is that TAB does nothing, and nothing says
why. Doctor reads the configuration file of the shell in ``$SHELL`` and,
when the packaged script is not sourced, prints the exact line in that
shell's syntax. It READS only: ``tests/testDoctorIsReadOnly.py`` states
why a diagnostic may never mutate what it inspects.
"""

import os

import pytest

from vaibify.cli.commandDoctor import flistRunDoctorChecks
from vaibify.cli.doctorHostChecks import fpreflightShellCompletions
from vaibify.cli.preflightResult import (
    S_LEVEL_INFO, S_LEVEL_OK, S_LEVEL_WARN,
)
from vaibify.install import shellSetup


@pytest.fixture
def fixtureLoginShell(tmp_path, monkeypatch):
    """A scratch home whose login shell a test chooses."""
    sHome = str(tmp_path / "home")
    os.makedirs(sHome)
    monkeypatch.setenv("HOME", sHome)

    def fnChoose(sShellName):
        monkeypatch.setenv("SHELL", f"/usr/bin/{sShellName}")
        return shellSetup._fsDetectShellRcFile(sShellName)

    return fnChoose


def _fnWriteRc(sRcPath, sContent):
    os.makedirs(os.path.dirname(sRcPath), exist_ok=True)
    with open(sRcPath, "w") as fileRc:
        fileRc.write(sContent)


@pytest.mark.parametrize("sShellName", ["bash", "zsh", "fish"])
def testASourcedScriptIsReportedOk(fixtureLoginShell, sShellName):
    sRcPath = fixtureLoginShell(sShellName)
    sScript = shellSetup._fsCompletionPathForShell(sShellName)
    _fnWriteRc(sRcPath, shellSetup.fsBuildCompletionSourceLine(
        sShellName, sScript,
    ) + "\n")
    preflightResult = fpreflightShellCompletions()
    assert preflightResult.sLevel == S_LEVEL_OK
    assert sRcPath in preflightResult.sMessage


@pytest.mark.falsification
@pytest.mark.parametrize("sShellName", ["bash", "zsh", "fish"])
def testAnUnsourcedScriptIsAWarningThatNamesTheExactLine(
    fixtureLoginShell, sShellName,
):
    """The remedy is the line, in the shell's own syntax, verbatim.

    Kills: reporting a shell as wired when its configuration file never
    mentions the script, which is what leaves a researcher pressing TAB
    at a prompt that does nothing.
    """
    sRcPath = fixtureLoginShell(sShellName)
    _fnWriteRc(sRcPath, "alias vc_push='vaibify push'\n")
    preflightResult = fpreflightShellCompletions()
    sScript = shellSetup._fsCompletionPathForShell(sShellName)
    assert preflightResult.sLevel == S_LEVEL_WARN
    assert preflightResult.sCommand == shellSetup.fsBuildCompletionSourceLine(
        sShellName, sScript,
    )
    assert sRcPath in preflightResult.sRemediation


def testAMissingConfigurationFileIsAnUnsourcedScript(fixtureLoginShell):
    sRcPath = fixtureLoginShell("zsh")
    assert not os.path.exists(sRcPath)
    assert fpreflightShellCompletions().sLevel == S_LEVEL_WARN


def testADifferentScriptPathIsNotTheInstalledOne(fixtureLoginShell):
    """A line left by another checkout is not this installation's script."""
    sRcPath = fixtureLoginShell("zsh")
    _fnWriteRc(
        sRcPath,
        '[ -f "/gone/vaibify.zsh" ] && . "/gone/vaibify.zsh"\n',
    )
    assert fpreflightShellCompletions().sLevel == S_LEVEL_WARN


@pytest.mark.parametrize("sShellName", ["sh", "csh", "tcsh"])
def testAShellWithoutAScriptIsInformationNotAFailure(
    fixtureLoginShell, monkeypatch, sShellName,
):
    monkeypatch.setenv("SHELL", f"/bin/{sShellName}")
    preflightResult = fpreflightShellCompletions()
    assert preflightResult.sLevel == S_LEVEL_INFO
    assert sShellName in preflightResult.sMessage


def testAnInstallationMissingTheScriptIsAWarning(
    fixtureLoginShell, monkeypatch, tmp_path,
):
    fixtureLoginShell("fish")
    monkeypatch.setattr(
        shellSetup, "_fsCompletionsDirectory",
        lambda: str(tmp_path / "noScripts"),
    )
    preflightResult = fpreflightShellCompletions()
    assert preflightResult.sLevel == S_LEVEL_WARN
    assert "fish" in preflightResult.sMessage


def testDoctorOnlyReadsTheConfigurationFile(fixtureLoginShell, tmp_path):
    """Neither a changed byte nor a created file: doctor never fixes."""
    sRcPath = fixtureLoginShell("fish")
    fpreflightShellCompletions()
    assert not os.path.exists(sRcPath)
    assert not os.path.exists(os.path.dirname(sRcPath))
    _fnWriteRc(sRcPath, "set -x KEEP 1\n")
    tBefore = (os.stat(sRcPath).st_mtime_ns, open(sRcPath).read())
    fpreflightShellCompletions()
    assert (os.stat(sRcPath).st_mtime_ns, open(sRcPath).read()) == tBefore


def testTheCheckIsInTheReportForAContainerlessInvocationToo(
    fixtureLoginShell, monkeypatch,
):
    """The report names the check whether or not a project is configured."""
    fixtureLoginShell("zsh")
    monkeypatch.setattr(
        "vaibify.cli.commandDoctor._flistSharedChecks", lambda: [],
    )
    listResults = flistRunDoctorChecks(None, False, False)
    assert "shell-completions" in [
        preflightResult.sName for preflightResult in listResults
    ]


def testTheCheckIsInTheReportForAHostProject(fixtureLoginShell, monkeypatch):
    """A host project is on this machine: its researcher types in a shell."""
    fixtureLoginShell("zsh")
    monkeypatch.setattr(
        "vaibify.cli.commandDoctor._fdictHostProjectOrNone",
        lambda config: {"sName": "hostProject", "sDirectory": "/nowhere"},
    )
    monkeypatch.setattr(
        "vaibify.cli.commandDoctor._flistHostProjectChecks",
        lambda dictProject: [],
    )
    listResults = flistRunDoctorChecks(object(), False, False)
    assert "shell-completions" in [
        preflightResult.sName for preflightResult in listResults
    ]
