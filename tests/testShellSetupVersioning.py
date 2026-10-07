"""First-run setup is versioned, and what it appends is readable by its shell.

Setup wrote ``setup complete`` into a marker and skipped itself forever
after. On the machine this was measured on, that marker predated the
packaged completion scripts, so setup had run, configured nothing, and
could never be repaired by shipping a working script: the marker told
every later run to skip it. The marker now records a VERSION, a marker
older than the current version runs setup again, and setup only ever
APPENDS to a shell configuration file.

The other half of the defect was fish, which had no script and was given
a ``[ ... ] && .`` line it cannot read. The line setup writes is now in
the syntax of the shell it is written for, and the end-to-end tests below
read the finished configuration file back in the real shell, because a
line that looks right is not a line that loads.
"""

import os

import pytest

from tests.shellCompletionHarness import (
    fsCompletionScript, fsRequireShell, fsRunShellProgram,
)
from vaibify.install import shellSetup


@pytest.fixture
def fixtureSetupHome(tmp_path, monkeypatch):
    """A scratch home, marker and login shell; setup touches nothing else."""
    sHome = str(tmp_path / "home")
    os.makedirs(sHome)
    monkeypatch.setenv("HOME", sHome)
    monkeypatch.setattr(shellSetup, "_MARKER_DIR", str(tmp_path / "marker"))
    monkeypatch.setattr(
        shellSetup, "_MARKER_PATH", str(tmp_path / "marker" / ".setup_done"),
    )
    monkeypatch.setattr(shellSetup, "fnLinkColimaSocket", lambda: None)
    return sHome


def _fnWriteMarker(sContent):
    os.makedirs(shellSetup._MARKER_DIR, exist_ok=True)
    with open(shellSetup._MARKER_PATH, "w") as fileMarker:
        fileMarker.write(sContent)


@pytest.mark.falsification
@pytest.mark.parametrize("sMarker, bExpectedComplete", [
    pytest.param(None, False, id="absent"),
    pytest.param("", False, id="empty"),
    pytest.param("setup complete\n", False, id="legacyText"),
    pytest.param("garbled\n", False, id="garbled"),
    pytest.param("setup v1\n", False, id="versionOne"),
    pytest.param(
        f"setup v{shellSetup.I_SETUP_VERSION}\n", True, id="current",
    ),
    pytest.param(
        f"setup v{shellSetup.I_SETUP_VERSION + 1}\n", True, id="newer",
    ),
])
def testAnOlderMarkerIsNotCompleteAndACurrentOneIs(
    fixtureSetupHome, sMarker, bExpectedComplete,
):
    """Only a marker of the current version or newer skips setup.

    ``setup complete`` is the marker every earlier release wrote, and it
    is version 1 by definition.

    Kills: reading the marker's mere existence as completion, which is
    how a machine whose setup configured nothing stayed that way.
    """
    if sMarker is not None:
        _fnWriteMarker(sMarker)
    assert shellSetup.fbIsSetupComplete() is bExpectedComplete


def testTheMarkerRecordsTheVersionThatWroteIt(fixtureSetupHome):
    os.makedirs(shellSetup._MARKER_DIR)
    shellSetup._fnWriteMarkerFile()
    with open(shellSetup._MARKER_PATH) as fileMarker:
        assert fileMarker.read() == f"setup v{shellSetup.I_SETUP_VERSION}\n"
    assert shellSetup.fiReadSetupVersion() == shellSetup.I_SETUP_VERSION


def testTheCliRunsSetupForAVersionOneMarkerAndNotForTheCurrentOne(
    fixtureSetupHome, monkeypatch,
):
    """The CLI's import-time hook is where the version decides anything."""
    from unittest.mock import MagicMock

    from vaibify.cli.main import _fnEnsureFirstTimeSetup
    mockSetup = MagicMock()
    monkeypatch.setattr(shellSetup, "fnRunFirstTimeSetup", mockSetup)
    _fnWriteMarker("setup complete\n")
    _fnEnsureFirstTimeSetup()
    assert mockSetup.call_count == 1
    _fnWriteMarker(f"setup v{shellSetup.I_SETUP_VERSION}\n")
    _fnEnsureFirstTimeSetup()
    assert mockSetup.call_count == 1


@pytest.mark.parametrize("sShellName", ["bash", "zsh", "fish"])
def testEveryShellHasAScriptAndTheInstallationCarriesAllThree(sShellName):
    sScript = shellSetup._fsCompletionPathForShell(sShellName)
    assert os.path.basename(sScript) == f"vaibify.{sShellName}"
    assert shellSetup.fbCompletionsArePresent() is True


@pytest.mark.falsification
def testAnInstallationMissingOneScriptIsNotComplete(tmp_path, monkeypatch):
    """A directory that exists is not evidence the scripts do.

    Kills: checking for the directory alone, so a wheel that shipped two
    of the three scripts is recorded as finished and fish's researcher is
    never told.
    """
    sDirectory = tmp_path / "completions"
    sDirectory.mkdir()
    monkeypatch.setattr(
        shellSetup, "_fsCompletionsDirectory", lambda: str(sDirectory),
    )
    for sName in ("vaibify.bash", "vaibify.zsh"):
        (sDirectory / sName).write_text("# script\n")
    assert shellSetup.fbCompletionsArePresent() is False
    (sDirectory / "vaibify.fish").write_text("# script\n")
    assert shellSetup.fbCompletionsArePresent() is True


@pytest.mark.falsification
def testTheLineForFishIsFishSyntaxAndTheOthersArePosix():
    """fish has no ``[ ... ] && .``; it has ``test`` and ``source``.

    Kills: writing the POSIX line for every shell, which fish rejects
    with a syntax error at every start-up.
    """
    sFish = shellSetup.fsBuildCompletionSourceLine("fish", "/a b/vaibify.fish")
    assert sFish == (
        'test -f "/a b/vaibify.fish"; and source "/a b/vaibify.fish"'
    )
    for sShellName in ("bash", "zsh"):
        sLine = shellSetup.fsBuildCompletionSourceLine(
            sShellName, "/a b/vaibify.x",
        )
        assert sLine == '[ -f "/a b/vaibify.x" ] && . "/a b/vaibify.x"'


def _fsReadFile(sPath):
    with open(sPath, "r", encoding="utf-8") as fileRead:
        return fileRead.read()


@pytest.mark.falsification
@pytest.mark.parametrize("sShellName", ["bash", "zsh", "fish"])
def testSetupOnlyAppendsAndNeverRemovesWhatWasThere(
    fixtureSetupHome, monkeypatch, sShellName,
):
    """The researcher's own lines, and retired aliases, are left alone.

    A version-1 machine has aliases from an earlier release in its
    configuration file. Setup adds what is missing and touches nothing
    else: the original text must still be a PREFIX of the file.

    Kills: rewriting the file instead of appending to it.
    """
    monkeypatch.setenv("SHELL", f"/usr/bin/{sShellName}")
    sRcPath = shellSetup._fsDetectShellRcFile(sShellName)
    os.makedirs(os.path.dirname(sRcPath), exist_ok=True)
    sBefore = "export KEEP=1\nalias vc_push='vaibify push'\n# mine\n"
    with open(sRcPath, "w") as fileRc:
        fileRc.write(sBefore)
    _fnWriteMarker("setup complete\n")
    shellSetup.fnRunFirstTimeSetup()
    sAfter = _fsReadFile(sRcPath)
    assert sAfter.startswith(sBefore)
    sScript = shellSetup._fsCompletionPathForShell(sShellName)
    assert shellSetup.fsBuildCompletionSourceLine(
        sShellName, sScript,
    ) in sAfter[len(sBefore):]
    assert shellSetup.fbIsSetupComplete() is True


@pytest.mark.parametrize("sShellName", ["bash", "zsh", "fish"])
def testRunningSetupTwiceAddsTheLineOnce(
    fixtureSetupHome, monkeypatch, sShellName,
):
    monkeypatch.setenv("SHELL", f"/usr/bin/{sShellName}")
    shellSetup.fnRunFirstTimeSetup()
    sRcPath = shellSetup._fsDetectShellRcFile(sShellName)
    sFirst = _fsReadFile(sRcPath)
    shellSetup.fnRunFirstTimeSetup()
    assert _fsReadFile(sRcPath) == sFirst
    sSourceLine = shellSetup.fsBuildCompletionSourceLine(
        sShellName, shellSetup._fsCompletionPathForShell(sShellName),
    )
    assert sFirst.count(sSourceLine) == 1


def testFishSetupCreatesItsConfigurationDirectory(
    fixtureSetupHome, monkeypatch,
):
    """A fresh fish has no ``config.fish`` and may have no directory."""
    monkeypatch.setenv("SHELL", "/usr/bin/fish")
    sRcPath = shellSetup._fsDetectShellRcFile("fish")
    assert not os.path.exists(os.path.dirname(sRcPath))
    shellSetup.fnRunFirstTimeSetup()
    assert "vaibify.fish" in _fsReadFile(sRcPath)


def testASetupMissingItsScriptsIsRetriedNotRecorded(
    fixtureSetupHome, monkeypatch,
):
    monkeypatch.setattr(shellSetup, "fbCompletionsArePresent", lambda: False)
    shellSetup.fnRunFirstTimeSetup()
    assert shellSetup.fbIsSetupComplete() is False


# ----------------------------------------------- read back in the shell


def _fsRunRcInShell(sShellName, sExecutable, sRcPath, sQuery, dictEnvironment):
    """Source the finished configuration file in the real shell; query it."""
    if sShellName == "fish":
        return fsRunShellProgram(
            sExecutable, ["--no-config", "-c"],
            f"source '{sRcPath}'; {sQuery}", dictEnvironment,
        )
    return fsRunShellProgram(
        sExecutable, ["--noprofile", "--norc", "-c"] if sShellName == "bash"
        else ["-f", "-c"],
        f"source '{sRcPath}'; {sQuery}", dictEnvironment,
    )


def _fnAssertTheWrittenLineLoadsTheCompletion(
    sHome, monkeypatch, sShellName, sQuery, sExpected,
):
    """Run setup, source the file it wrote in the real shell, ask the shell."""
    sExecutable = fsRequireShell(sShellName)
    monkeypatch.setenv("SHELL", f"/usr/bin/{sShellName}")
    shellSetup.fnRunFirstTimeSetup()
    sRcPath = shellSetup._fsDetectShellRcFile(sShellName)
    dictEnvironment = {
        "PATH": os.environ["PATH"], "HOME": sHome, "TERM": "xterm",
    }
    sOutput = _fsRunRcInShell(
        sShellName, sExecutable, sRcPath, sQuery, dictEnvironment,
    )
    assert sExpected in sOutput, sOutput


# Only the bash test is a falsification test: a falsification test must run
# on every lane that replays it, and not every such lane carries zsh and
# fish. The other two are required on the unit lanes instead.
@pytest.mark.falsification
def testTheLineSetupWroteLoadsTheCompletionInBash(
    fixtureSetupHome, monkeypatch,
):
    """Source the file setup wrote; ask the shell whether it is wired.

    A line that reads correctly is not a line that loads: fish was given
    one it could not parse for as long as setup supported it.

    Kills: writing a line that its shell does not load, and a script that
    registers nothing when sourced from a configuration file.
    """
    _fnAssertTheWrittenLineLoadsTheCompletion(
        fixtureSetupHome, monkeypatch, "bash",
        "complete -p vaibify vaib_push", "_fnCompleteVaibify",
    )


@pytest.mark.parametrize("sShellName, sQuery, sExpected", [
    pytest.param(
        "zsh", "print -r -- ${_comps[vaibify]}", "_vaibify", id="zsh",
    ),
    pytest.param("fish", "complete -C 'vaibify pu'", "push", id="fish"),
])
def testTheLineSetupWroteLoadsTheCompletionInTheOtherShells(
    fixtureSetupHome, monkeypatch, sShellName, sQuery, sExpected,
):
    _fnAssertTheWrittenLineLoadsTheCompletion(
        fixtureSetupHome, monkeypatch, sShellName, sQuery, sExpected,
    )


def testTheScriptLoadsWhenAnInsecureDirectoryIsOnTheFunctionPath(tmp_path):
    """An insecure completion directory is skipped, not a reason to load nothing.

    Homebrew's completion directories are often writable by a group, and
    zsh's ``compinit`` stops to ask about such a directory; with no one to
    answer it initializes nothing, so the script's ``compdef`` calls failed
    and no completion was registered. ``compinit -i`` skips the directory
    and carries on.

    Kills: calling plain ``compinit`` in the script.
    """
    sExecutable = fsRequireShell("zsh")
    sInsecure = str(tmp_path / "insecure")
    os.mkdir(sInsecure)
    os.chmod(sInsecure, 0o777)
    dictEnvironment = {
        "PATH": os.environ["PATH"], "HOME": str(tmp_path), "TERM": "xterm",
    }
    sOutput = fsRunShellProgram(
        sExecutable, ["-f", "-c"],
        f"fpath=('{sInsecure}' $fpath); "
        f"source '{fsCompletionScript('zsh')}'; "
        "print -r -- ${_comps[vaibify]}",
        dictEnvironment,
    )
    assert "_vaibify" in sOutput, sOutput


def testTheSuiteNeverRunsSetupAgainstTheResearchersOwnMarker():
    """Collection imports the CLI, and setup APPENDS to the real rc file.

    ``tests/conftest.py`` points the marker at a scratch directory before
    any test module is collected. Without it, a machine whose marker is
    older than the current version would be edited by whichever checkout
    ran the suite -- including a disposable worktree whose script path
    vanishes with it.
    """
    sRealMarker = os.path.join(
        os.path.expanduser("~"), ".vaibify", ".setup_done",
    )
    assert shellSetup._MARKER_PATH != sRealMarker
    assert shellSetup.fbIsSetupComplete() is True
