"""The completion scripts agree with what the code around them defines.

Tab completion for ``vaibify push`` and ``vaibify pull`` broke in a
RENAME. The helper aliases were ``vc_push`` / ``vc_pull``; commit
9f0e084b renamed them to ``vaibify_push`` / ``vaibify_pull`` (with
``vaib_`` shortcuts) in ``shellSetup.py``, and the completion scripts
kept registering against the retired spellings. A ``complete -F ...
vc_push`` for a command nobody can run is not an error in any shell --
it is silently inert -- so both helpers stopped completing anything
and nothing reported it.

Two files had to agree and nothing bound them, which is the shape this
repository has been bitten by before. These tests are that binding, and
the same binding now covers the subcommand list, which each script
keeps by hand and which silently went stale (it offered a ``publish``
command that does not exist and omitted a dozen that do).

The existing coverage could not have caught it:
``testShellCompletionsShipInsideThePackage`` proves the scripts are
found by an installation, and its own docstring records that
completion "had never worked" while first-run setup recorded a marker
saying it had. Shipping a file and wiring it are different claims.

The position logic is driven in the SYSTEM bash against a stub
``vaibify``; zsh and fish are driven in ``testShellCompletionPositions``
and the insertion of a name in ``testShellCompletionInsertion``.
"""

import os
import re
import shlex
import subprocess

import pytest

from tests.shellCompletionHarness import (
    S_SYSTEM_BASH,
    fdictBuildStubWorld,
    flistReadHelperCalls,
    fsCompletionScript,
    fsRunShellProgram,
)
from vaibify.install.shellSetup import _fsCompletionPathForShell


LIST_SHELLS = ["bash", "zsh", "fish"]


def _fsReadCompletionScript(sShellName):
    """Return the shipped completion script for one shell."""
    sPath = _fsCompletionPathForShell(sShellName)
    assert sPath, f"no completion script resolves for {sShellName}"
    with open(sPath, "r", encoding="utf-8") as fileScript:
        return fileScript.read()


def _flistAliasNamesShellSetupCreates():
    """Return the helper command names first-run setup defines.

    Read from the alias block itself rather than restated here, so a
    third rename cannot leave this test asserting yesterday's names.
    """
    from vaibify.install import shellSetup
    setNames = set()
    for sShellName in ("zsh", "bash", "fish", "csh"):
        try:
            sBlock = shellSetup._fsHelperAliasBlock(sShellName)
        except AttributeError:  # pragma: no cover - name is asserted below
            pytest.fail(
                "shellSetup no longer exposes its alias block; this test "
                "must be re-pointed at whatever now defines the names"
            )
        for sMatch in re.findall(r"alias\s+([A-Za-z_][A-Za-z0-9_]*)", sBlock):
            setNames.add(sMatch)
    return sorted(setNames)


def _flistRegisteredCommandNames(sShellName):
    """Return every command name a script registers completion for."""
    sScript = _fsReadCompletionScript(sShellName)
    if sShellName == "fish":
        return re.findall(r"^complete\s+-c\s+(\S+)", sScript, re.M)
    sPattern = r"^(?:complete\b[^\n]*?-F\s+\S+|compdef\s+\S+)\s+(\S+)\s*$"
    return re.findall(sPattern, sScript, re.M)


@pytest.mark.parametrize("sShellName", LIST_SHELLS)
def testEveryTransferAliasHasACompletionRegistration(sShellName):
    """Every push/pull alias setup creates is one completion registers.

    Kills: renaming the helper aliases without re-pointing the
    completion scripts, which leaves both helpers inert and silent.
    """
    setRegistered = set(_flistRegisteredCommandNames(sShellName))
    listMissing = [
        sAlias for sAlias in _flistAliasNamesShellSetupCreates()
        if ("push" in sAlias or "pull" in sAlias)
        and sAlias not in setRegistered
    ]
    assert listMissing == [], (
        f"{sShellName} completion registers against no such command for "
        f"{listMissing}; a registration for a command that does not "
        f"exist is inert, not an error, so nothing would report it"
    )


@pytest.mark.parametrize("sShellName", LIST_SHELLS)
def testCompletionRegistersNoRetiredCommandName(sShellName):
    """A registration must not name a command setup no longer creates.

    The inverse of the test above, and the half that would have caught
    the original break: the scripts kept ``vc_push`` / ``vc_pull``
    long after those names were gone. Every registered name, not just
    the push/pull ones, must be a command that exists: the old ``vc``
    shorthand was registered for years after nothing created it.
    """
    setLive = {"vaibify", "vaib"} | set(_flistAliasNamesShellSetupCreates())
    listRegistered = _flistRegisteredCommandNames(sShellName)
    assert listRegistered, f"no registrations parsed for {sShellName}"
    listRetired = [
        sName for sName in listRegistered if sName not in setLive
    ]
    assert listRetired == [], (
        f"{sShellName} completion still registers retired command "
        f"names {listRetired}; they complete nothing and say nothing"
    )


def _fsetParseStaticSubcommands(sShellName):
    """Return the subcommand list a script keeps by hand."""
    sScript = _fsReadCompletionScript(sShellName)
    if sShellName == "fish":
        sList = re.search(
            r"^set -g __vaibify_subcommands (.+)$", sScript, re.M,
        ).group(1)
    else:
        sList = re.search(
            r'^(?:typeset -g )?_sVaibifySubcommands="([^"]+)"', sScript,
            re.M,
        ).group(1)
    return set(sList.split())


@pytest.mark.parametrize("sShellName", LIST_SHELLS)
def testTheSubcommandListIsExactlyTheVisibleCommands(sShellName):
    """Each script lists every command the CLI shows, and nothing else.

    The list is static on purpose -- asking the CLI for it would cost a
    process start per TAB -- so this test is what keeps a static copy
    honest. The hidden helper is deliberately absent: offering it would
    defeat hiding it.

    Kills: a command added or removed in ``main.py`` without the three
    scripts following.
    """
    from vaibify.cli.main import main
    setVisible = {
        sName for sName, commandEntry in main.commands.items()
        if not commandEntry.hidden
    }
    setListed = _fsetParseStaticSubcommands(sShellName)
    assert setListed == setVisible, (
        f"{sShellName}: missing {sorted(setVisible - setListed)}, "
        f"stale {sorted(setListed - setVisible)}"
    )
    assert "complete-path" not in setListed


# Builtins and syntax that do not exist in bash 3.2. Each one fails
# SILENTLY inside a completion function -- the shell reports nothing
# and the researcher simply sees no suggestions.
LIST_BASH_FOUR_ONLY = [
    ("mapfile", "mapfile"),
    ("readarray", "readarray"),
    ("declare -A", "associative arrays"),
    ("${!", "indirect/prefix expansion in this form"),
    ("${sCurrent,,}", "case-modifying expansion"),
]


def _fsBashCodeWithoutComments():
    """Return the bash script with its comment lines removed."""
    sScript = _fsReadCompletionScript("bash")
    return re.sub(r"^\s*#.*$", "", sScript, flags=re.M)


@pytest.mark.falsification
def testTheBashScriptRunsUnderTheBashMacOsShips():
    """bash 3.2 is the floor, because /bin/bash on macOS still is.

    A bash-4 builtin does not raise anything a completion function
    surfaces: the shell writes "not found" to stderr nobody reads and
    the researcher sees no suggestions. `mapfile` shipped in this
    script and container-path completion had never worked on a stock
    Mac -- caught by CI's macOS legs, never by a developer whose PATH
    bash came from a package manager.

    Kills: reintroducing mapfile/readarray or any other bash-4-only
    construct into the completion script.
    """
    sCode = _fsBashCodeWithoutComments()
    listFound = [
        sDescription for sToken, sDescription in LIST_BASH_FOUR_ONLY
        if sToken in sCode
    ]
    assert listFound == [], (
        f"the completion script uses {listFound}, absent from bash 3.2 "
        f"which macOS ships as {S_SYSTEM_BASH}; completion then offers "
        f"nothing and says nothing"
    )


def testTheShippedBashScriptParsesUnderTheSystemBash():
    """A syntax error would disable completion wholesale."""
    tResult = subprocess.run(
        [S_SYSTEM_BASH, "-n", fsCompletionScript("bash")],
        capture_output=True, text=True,
    )
    assert tResult.returncode == 0, tResult.stderr


def _flistDriveBash(sLine, tmp_path, listCandidates=(),
                    sFunction="_fnCompleteVaibify", sTail=""):
    """Return (COMPREPLY, helper calls) for one typed line in system bash.

    The line is words joined by ``|`` and the LAST word is the one under
    the cursor, as bash itself hands them over. ``sTail`` is appended to
    the program, after the completion ran.
    """
    dictWorld = fdictBuildStubWorld(str(tmp_path), listCandidates)
    sWordsLiteral = " ".join(
        shlex.quote(sWord) for sWord in sLine.split("|")
    )
    sProgram = (
        f"source {shlex.quote(fsCompletionScript('bash'))}\n"
        f"COMP_WORDS=({sWordsLiteral})\n"
        "COMP_CWORD=$(( ${#COMP_WORDS[@]} - 1 ))\n"
        "COMPREPLY=()\n"
        f"{sFunction} 2>/dev/null\n"
        "printf '%s\\n' \"${COMPREPLY[@]}\"\n"
        + sTail
    )
    sOutput = fsRunShellProgram(S_SYSTEM_BASH, ["-c"], sProgram, dictWorld)
    listReply = [sReply for sReply in sOutput.split("\n") if sReply]
    return listReply, flistReadHelperCalls(dictWorld)


@pytest.mark.falsification
def testPushCompletesContainerPathsOnlyForItsDestination(tmp_path):
    """push reads from the host and writes into the container.

    The two arguments mean opposite machines, so completing both the
    same way is wrong in one of them. The source must offer the
    researcher's own files (no completions here, which lets the
    shell's default filename completion stand) and the destination
    must offer container paths.

    Kills: completing the same side for both arguments, or dropping
    the push/pull case out of the vaibify completer so `vaibify push`
    offers nothing at all -- the state this shipped in.
    """
    listSource, listSourceCalls = _flistDriveBash(
        "vaibify|push|", tmp_path / "a", ["Step01/", "Step02/"],
    )
    listDestination, listDestinationCalls = _flistDriveBash(
        "vaibify|push|local.csv|", tmp_path / "b", ["Step01/", "Step02/"],
    )
    assert listSource == [] and listSourceCalls == [], (
        "the push SOURCE asked for container paths; it is read from the "
        f"researcher's own machine: {listSource} {listSourceCalls}"
    )
    assert listDestination == ["Step01/", "Step02/"], (
        f"the push DESTINATION offered no container paths: "
        f"{listDestination}"
    )
    assert listDestinationCalls == [
        ["complete-path", "--side", "container", "--", ""],
    ]


@pytest.mark.falsification
def testPullCompletesContainerPathsOnlyForItsSource(tmp_path):
    """pull is push's mirror, so its positions are mirrored too.

    Kills: giving pull push's position logic, which offers container
    paths for the host destination and local files for the container
    source -- backwards in both arguments.
    """
    listSource, _listSourceCalls = _flistDriveBash(
        "vaibify|pull|", tmp_path / "a", ["Step01/", "Step02/"],
    )
    listDestination, listDestinationCalls = _flistDriveBash(
        "vaibify|pull|Step01/out.csv|", tmp_path / "b",
        ["Step01/", "Step02/"],
    )
    assert listSource == ["Step01/", "Step02/"], (
        f"the pull SOURCE offered no container paths: {listSource}"
    )
    assert listDestination == [] and listDestinationCalls == [], (
        "the pull DESTINATION asked for container paths; it is written "
        f"to the researcher's own machine: {listDestination}"
    )


@pytest.mark.parametrize("sLine, bContainerExpected, sProjectExpected", [
    pytest.param("vaibify|pull|-p|fillet|", True, "fillet", id="pullDashP"),
    pytest.param(
        "vaibify|pull|--project|fillet|", True, "fillet", id="pullLong",
    ),
    pytest.param(
        "vaibify|pull|--project|=|fillet|", True, "fillet",
        id="pullLongSplitAtEquals",
    ),
    pytest.param(
        "vaibify|pull|-pfillet|", True, "fillet", id="pullGlued",
    ),
    pytest.param(
        "vaibify|pull|-p|fillet|Step01/out.csv|", False, None,
        id="pullSecondArgument",
    ),
    pytest.param(
        "vaibify|push|-p|fillet|", False, None, id="pushFirstArgument",
    ),
    pytest.param(
        "vaibify|push|-p|fillet|local.csv|", True, "fillet",
        id="pushSecondArgument",
    ),
    pytest.param(
        "vaibify|push|local.csv|-p|fillet|", True, "fillet",
        id="pushOptionAfterPath",
    ),
])
@pytest.mark.falsification
def testTheProjectIsReadFromTypedWordsAndNeverCountedAsAPath(
    tmp_path, sLine, bContainerExpected, sProjectExpected,
):
    """``-p NAME`` names a project; it is not the first path.

    The scripts used to find the container from ``./vaibify.yml``,
    ignoring ``-p`` entirely, and counted the project name as a typed
    path -- so after ``-p fillet`` the SOURCE position was taken for the
    destination. The name now goes to the helper, which resolves it the
    way push and pull do.

    Kills: counting the value of -p as a positional argument, and
    dropping the project from the helper's arguments.
    """
    _listReply, listCalls = _flistDriveBash(
        sLine, tmp_path, ["Step01/"],
    )
    if not bContainerExpected:
        assert listCalls == []
        return
    assert listCalls == [[
        "complete-path", "--side", "container",
        f"--project={sProjectExpected}", "--", "",
    ]]


def testAnOptionIsOfferedWhenADashIsTyped(tmp_path):
    """A leading dash completes options, not container paths."""
    listReply, listCalls = _flistDriveBash("vaibify|pull|-", tmp_path)
    assert listReply == ["--project", "-p", "--help", "-h"]
    assert listCalls == []


@pytest.mark.parametrize("sTyped, sExpectedPartial", [
    ("My\\ D", "My D"),
    ('"My D', "My D"),
    ("'My D", "My D"),
    ("Step01/out\\ put", "Step01/out put"),
])
def testTheHelperIsAskedAboutTheTextTheResearcherMeant(
    tmp_path, sTyped, sExpectedPartial,
):
    """Quoting is removed before the helper is asked, never evaluated."""
    _listReply, listCalls = _flistDriveBash(
        f"vaibify|pull|{sTyped}", tmp_path, ["x"],
    )
    assert listCalls == [[
        "complete-path", "--side", "container", "--", sExpectedPartial,
    ]]


def testASubcommandPrefixCompletesFromTheStaticList(tmp_path):
    """The subcommand position completes, and does not ask the helper."""
    listAll, listCalls = _flistDriveBash("vaibify|", tmp_path)
    listPrefixed, _listPrefixedCalls = _flistDriveBash(
        "vaibify|pu", tmp_path,
    )
    assert "push" in listAll and "build" in listAll
    assert listPrefixed == ["pull", "push"]
    assert listCalls == []


LIST_HOSTILE_NAMES = [
    "a b; touch PWNED",
    "$(touch PWNED)",
    "`touch PWNED`",
    "it's \"q\" & > < | ~ ! * ? # ( ) { } [ ] \\ end",
    "a\"; touch PWNED; echo \"",
    "a'; touch PWNED; echo '",
]


@pytest.mark.falsification
def testEveryOfferedNameIsOneInertWordWhenTheShellReadsIt(tmp_path):
    """A name from inside a container never becomes shell syntax.

    Names come from a place an agent controls. Each offered match is
    read back by the shell exactly as the shell reads the line the
    completion would have produced -- ``set --`` on the offered text --
    and must be ONE word equal to the name, with nothing executed.

    Kills: offering the raw name instead of the escaped one, which makes
    a file called ``$(...)`` a command the researcher runs by pressing
    TAB and Enter.
    """
    sTail = (
        'for sReply in "${COMPREPLY[@]}"; do\n'
        '    eval "set -- ${sReply}"\n'
        '    printf \'WORDS=%s FIRST=%s\\n\' "$#" "$1"\n'
        'done\n'
    )
    sWork = tmp_path / "work"
    sWork.mkdir()
    listReply, _listCalls = _flistDriveBash(
        "vaibify|pull|", tmp_path / "world", LIST_HOSTILE_NAMES,
        sTail="cd " + shlex.quote(str(sWork)) + "\n" + sTail,
    )
    listWords = [sLine for sLine in listReply if sLine.startswith("WORDS=")]
    assert listWords == [
        f"WORDS=1 FIRST={sName}" for sName in LIST_HOSTILE_NAMES
    ]
    assert not os.path.exists(sWork / "PWNED"), (
        "a completed name ran a command in the researcher's shell"
    )


def testTheCompletionIsSilentWhenVaibifyIsNotOnThePath(tmp_path):
    """No ``vaibify`` to ask is no suggestions, never an error."""
    dictWorld = fdictBuildStubWorld(str(tmp_path))
    dictWorld["PATH"] = "/usr/bin:/bin"
    sProgram = (
        f"source {shlex.quote(fsCompletionScript('bash'))}\n"
        "COMP_WORDS=(vaibify pull ''); COMP_CWORD=2; COMPREPLY=()\n"
        "_fnCompleteVaibify\n"
        "echo \"count=${#COMPREPLY[@]}\"\n"
    )
    tResult = subprocess.run(
        [S_SYSTEM_BASH, "-c", sProgram], capture_output=True, text=True,
        env=dictWorld,
    )
    assert tResult.stdout.strip() == "count=0"
    assert tResult.stderr == "", tResult.stderr
