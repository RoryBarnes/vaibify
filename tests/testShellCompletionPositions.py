"""zsh and fish ask for container paths at exactly the right positions.

The bash script's position logic is driven in ``testShellCompletionWiring``;
this is the same contract for the other two shells, each run in the REAL
shell with the ``vaibify`` command replaced by a stub that records how it
was called:

* zsh -- the completion function is called with ``words`` and ``CURRENT``
  set as the completion system sets them, and ``compadd`` / ``_files``
  are stubs that report what they were asked to offer;
* fish -- ``complete -C`` asks fish itself what it would offer for a line.

What is asserted is WHERE a container path is wanted and WHICH project
the helper is asked about. How an offered name reaches the command line
is a property of the shell, not of the script, and is tested in
``testShellCompletionInsertion`` by pressing TAB in the real thing.
"""

import re
import shlex

import pytest

from tests.shellCompletionHarness import (
    fdictBuildStubWorld,
    flistReadHelperCalls,
    fsCompletionScript,
    fsRequireShell,
    fsRunShellProgram,
)


def _flistVisibleCommandNames():
    """Return the subcommand names the CLI shows, sorted."""
    from vaibify.cli.main import main
    return sorted(
        sName for sName, commandEntry in main.commands.items()
        if not commandEntry.hidden
    )


def _flistDriveZsh(
    sLine, tmp_path, listCandidates=(), sFunction="_vaibify", sPrefix=None,
):
    """Return (events, helper calls) for one typed line in real zsh.

    An event is ``("files",)`` when the script fell back to the shell's
    own file completion, or ``("compadd", [arguments])`` for each call
    that offered matches. The LAST word of the line is the one under the
    cursor. ``PREFIX`` is what the completion system sets it to -- the
    word up to the cursor with any enclosing quote already removed --
    and defaults to the word as typed; how the real system sets it is
    exercised by ``testShellCompletionInsertion``.
    """
    sExecutable = fsRequireShell("zsh")
    dictWorld = fdictBuildStubWorld(str(tmp_path), listCandidates)
    listWords = sLine.split("|")
    sPrefixValue = listWords[-1] if sPrefix is None else sPrefix
    sProgram = (
        "compdef() { :; }\n"
        "_files() { print -r -- FILES; }\n"
        "compadd() { print -r -- CALL; local sArgument; "
        'for sArgument in "$@"; do print -r -- "ARG=$sArgument"; done; }\n'
        f"source {shlex.quote(fsCompletionScript('zsh'))}\n"
        f"words=({' '.join(shlex.quote(sWord) for sWord in listWords)})\n"
        f"CURRENT={len(listWords)}\n"
        f"PREFIX={shlex.quote(sPrefixValue)}\n"
        f"{sFunction}\n"
    )
    sOutput = fsRunShellProgram(sExecutable, ["-f", "-c"], sProgram, dictWorld)
    listEvents = []
    for sLineOut in sOutput.split("\n"):
        if sLineOut == "FILES":
            listEvents.append(("files",))
        elif sLineOut == "CALL":
            listEvents.append(("compadd", []))
        elif sLineOut.startswith("ARG="):
            listEvents[-1][1].append(sLineOut[len("ARG="):])
    return listEvents, flistReadHelperCalls(dictWorld)


def _fsFishQuote(sText):
    """Quote text as one literal fish word."""
    return "'" + sText.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _flistDriveFish(sLine, tmp_path, listCandidates=()):
    """Return (offered texts, helper calls) for a line, asking real fish."""
    sExecutable = fsRequireShell("fish")
    dictWorld = fdictBuildStubWorld(str(tmp_path), listCandidates)
    sProgram = (
        f"source {_fsFishQuote(fsCompletionScript('fish'))}; "
        f"complete -C {_fsFishQuote(sLine)}"
    )
    sOutput = fsRunShellProgram(
        sExecutable, ["--no-config", "-c"], sProgram, dictWorld,
    )
    listOffered = [
        sEntry.split("\t")[0] for sEntry in sOutput.split("\n") if sEntry
    ]
    return listOffered, flistReadHelperCalls(dictWorld)


# ---------------------------------------------------------------- zsh


def _flistZshOfferedNames(listEvents):
    """Return every name zsh was asked to offer, in order."""
    listNames = []
    for tEvent in listEvents:
        if tEvent[0] == "compadd":
            listArguments = tEvent[1]
            listNames.extend(
                listArguments[listArguments.index("--") + 1:],
            )
    return listNames


def testZshOffersTheSubcommandsTheCliShows(tmp_path):
    listEvents, listCalls = _flistDriveZsh("vaibify|", tmp_path)
    assert sorted(_flistZshOfferedNames(listEvents)) == (
        _flistVisibleCommandNames()
    )
    assert listCalls == []


def testZshPushCompletesContainerPathsOnlyForItsDestination(tmp_path):
    """push reads from this machine and writes into the container."""
    listSource, listSourceCalls = _flistDriveZsh(
        "vaibify|push|", tmp_path / "a", ["Step01/"],
    )
    listDestination, listDestinationCalls = _flistDriveZsh(
        "vaibify|push|local.csv|", tmp_path / "b", ["Step01/"],
    )
    assert listSource == [("files",)] and listSourceCalls == []
    assert _flistZshOfferedNames(listDestination) == ["Step01/"]
    assert listDestinationCalls == [
        ["complete-path", "--side", "container", "--", ""],
    ]


def testZshPullCompletesContainerPathsOnlyForItsSource(tmp_path):
    listSource, _listSourceCalls = _flistDriveZsh(
        "vaibify|pull|", tmp_path / "a", ["Step01/"],
    )
    listDestination, listDestinationCalls = _flistDriveZsh(
        "vaibify|pull|Step01/out.csv|", tmp_path / "b", ["Step01/"],
    )
    assert _flistZshOfferedNames(listSource) == ["Step01/"]
    assert listDestination == [("files",)] and listDestinationCalls == []


def testZshGivesDirectoriesNoTrailingSpaceAndFilesOne(tmp_path):
    """A directory can be descended into; a finished file takes a space."""
    listEvents, _listCalls = _flistDriveZsh(
        "vaibify|pull|", tmp_path, ["Step01/", "notes.md"],
    )
    listDirectoryCall = [
        tEvent[1] for tEvent in listEvents if "Step01/" in tEvent[1]
    ][0]
    listFileCall = [
        tEvent[1] for tEvent in listEvents if "notes.md" in tEvent[1]
    ][0]
    assert listDirectoryCall[:2] == ["-S", ""]
    assert "-S" not in listFileCall


def testZshOffersNothingWhenNoContainerPathAnswers(tmp_path):
    """No answer is no suggestions -- never the researcher's own files."""
    listEvents, listCalls = _flistDriveZsh("vaibify|pull|", tmp_path, [])
    assert listEvents == []
    assert len(listCalls) == 1


@pytest.mark.parametrize("sLine, bContainerExpected, sProjectExpected", [
    ("vaibify|pull|-p|fillet|", True, "fillet"),
    ("vaibify|pull|--project|fillet|", True, "fillet"),
    ("vaibify|pull|--project=fillet|", True, "fillet"),
    ("vaibify|pull|-pfillet|", True, "fillet"),
    ("vaibify|pull|-p|fillet|Step01/out.csv|", False, None),
    ("vaibify|push|-p|fillet|", False, None),
    ("vaibify|push|-p|fillet|local.csv|", True, "fillet"),
])
def testZshReadsTheProjectAndNeverCountsItAsAPath(
    tmp_path, sLine, bContainerExpected, sProjectExpected,
):
    _listEvents, listCalls = _flistDriveZsh(sLine, tmp_path, ["Step01/"])
    if not bContainerExpected:
        assert listCalls == []
        return
    assert listCalls == [[
        "complete-path", "--side", "container",
        f"--project={sProjectExpected}", "--", "",
    ]]


@pytest.mark.parametrize("sTyped, sPrefix, sExpectedPartial", [
    ("My\\ D", "My\\ D", "My D"),
    ('"My D', "My D", "My D"),
    ("'My D", "My D", "My D"),
])
def testZshAsksTheHelperAboutTheTextTheResearcherMeant(
    tmp_path, sTyped, sPrefix, sExpectedPartial,
):
    _listEvents, listCalls = _flistDriveZsh(
        f"vaibify|pull|{sTyped}", tmp_path, ["x"], sPrefix=sPrefix,
    )
    assert listCalls == [[
        "complete-path", "--side", "container", "--", sExpectedPartial,
    ]]


@pytest.mark.parametrize("sFunction, sLine, bContainerExpected", [
    ("_vaibify_push", "vaibify_push|", False),
    ("_vaibify_push", "vaib_push|a.csv|", True),
    ("_vaibify_pull", "vaibify_pull|", True),
    ("_vaibify_pull", "vaib_pull|Step01/out.csv|", False),
])
def testZshHelperAliasesStartCountingOneWordEarlier(
    tmp_path, sFunction, sLine, bContainerExpected,
):
    """The aliases are ``vaibify push`` / ``pull`` by another name."""
    listEvents, listCalls = _flistDriveZsh(
        sLine, tmp_path, ["Step01/"], sFunction=sFunction,
    )
    assert (len(listCalls) == 1) == bContainerExpected
    if not bContainerExpected:
        assert listEvents == [("files",)]


def testZshOffersOptionsWhenADashIsTyped(tmp_path):
    listEvents, listCalls = _flistDriveZsh("vaibify|push|-", tmp_path)
    assert _flistZshOfferedNames(listEvents) == [
        "--project", "-p", "--help", "-h",
    ]
    assert listCalls == []


# --------------------------------------------------------------- fish


def testFishOffersTheSubcommandsTheCliShows(tmp_path):
    listOffered, listCalls = _flistDriveFish("vaibify ", tmp_path)
    assert sorted(listOffered) == _flistVisibleCommandNames()
    assert listCalls == []


def testFishPushCompletesContainerPathsOnlyForItsDestination(tmp_path):
    listSource, listSourceCalls = _flistDriveFish(
        "vaibify push ", tmp_path / "a", ["Step01/"],
    )
    listDestination, listDestinationCalls = _flistDriveFish(
        "vaibify push local.csv ", tmp_path / "b", ["Step01/"],
    )
    assert "Step01/" not in listSource and listSourceCalls == []
    assert listDestination == ["Step01/"]
    assert listDestinationCalls == [
        ["complete-path", "--side", "container", "--", ""],
    ]


def testFishPullCompletesContainerPathsOnlyForItsSource(tmp_path):
    listSource, _listSourceCalls = _flistDriveFish(
        "vaibify pull ", tmp_path / "a", ["Step01/"],
    )
    listDestination, listDestinationCalls = _flistDriveFish(
        "vaibify pull Step01/out.csv ", tmp_path / "b", ["Step01/"],
    )
    assert listSource == ["Step01/"]
    assert "Step01/" not in listDestination and listDestinationCalls == []


@pytest.mark.parametrize("sLine, bContainerExpected, sProjectExpected", [
    ("vaibify pull -p fillet ", True, "fillet"),
    ("vaibify pull --project fillet ", True, "fillet"),
    ("vaibify pull --project=fillet ", True, "fillet"),
    ("vaibify pull -pfillet ", True, "fillet"),
    ("vaibify pull -p fillet Step01/out.csv ", False, None),
    ("vaibify push -p fillet ", False, None),
    ("vaibify push -p fillet local.csv ", True, "fillet"),
    ("vaibify push local.csv --project fillet ", True, "fillet"),
])
def testFishReadsTheProjectAndNeverCountsItAsAPath(
    tmp_path, sLine, bContainerExpected, sProjectExpected,
):
    _listOffered, listCalls = _flistDriveFish(sLine, tmp_path, ["Step01/"])
    if not bContainerExpected:
        assert listCalls == []
        return
    assert listCalls == [[
        "complete-path", "--side", "container",
        f"--project={sProjectExpected}", "--", "",
    ]]


@pytest.mark.parametrize("sLine, sExpectedPartial", [
    ("vaibify pull My\\ D", "My D"),
    ("vaibify pull 'My D", "My D"),
    ('vaibify pull "My D', "My D"),
])
def testFishAsksTheHelperAboutTheTextTheResearcherMeant(
    tmp_path, sLine, sExpectedPartial,
):
    _listOffered, listCalls = _flistDriveFish(sLine, tmp_path, ["x"])
    assert listCalls == [[
        "complete-path", "--side", "container", "--", sExpectedPartial,
    ]]


@pytest.mark.parametrize("sLine, bContainerExpected", [
    ("vaibify_push ", False),
    ("vaib_push a.csv ", True),
    ("vaibify_pull ", True),
    ("vaib_pull Step01/out.csv ", False),
    ("vaib pull ", True),
    ("vaib push local.csv ", True),
])
def testFishHelperAliasesAndTheShortNameFollowTheSameLogic(
    tmp_path, sLine, bContainerExpected,
):
    """Aliases are functions in fish, so each is wrapped to its command."""
    _listOffered, listCalls = _flistDriveFish(sLine, tmp_path, ["Step01/"])
    assert (len(listCalls) == 1) == bContainerExpected


def testFishOffersOptionsWhenADashIsTyped(tmp_path):
    listOffered, listCalls = _flistDriveFish("vaibify pull -", tmp_path)
    assert {"-p", "--project", "-h", "--help"} <= set(listOffered)
    assert listCalls == []


def testFishOffersNothingWhenNoContainerPathAnswers(tmp_path):
    listOffered, listCalls = _flistDriveFish("vaibify pull ", tmp_path, [])
    assert listOffered == []
    assert len(listCalls) == 1


def testFishNeverOffersTheHelperItself(tmp_path):
    """``complete-path`` is hidden; completing its prefix must not show it."""
    listOffered, _listCalls = _flistDriveFish("vaibify comp", tmp_path)
    assert listOffered == []


# ------------------------------------------------ every shell parses


@pytest.mark.parametrize("sShellName, listArguments", [
    ("zsh", ["-n"]),
    ("fish", ["--no-execute"]),
])
def testTheShippedScriptParsesInItsShell(sShellName, listArguments):
    """A syntax error would disable completion wholesale, silently."""
    import subprocess
    sExecutable = fsRequireShell(sShellName)
    tResult = subprocess.run(
        [sExecutable, *listArguments, fsCompletionScript(sShellName)],
        capture_output=True, text=True,
    )
    assert tResult.returncode == 0, tResult.stderr
    assert not re.search(r"\S", tResult.stderr), tResult.stderr
