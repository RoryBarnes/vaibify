"""Completion registers against the alias names that actually exist.

Tab completion for ``vaibify push`` and ``vaibify pull`` broke in a
RENAME. The helper aliases were ``vc_push`` / ``vc_pull``; commit
9f0e084b renamed them to ``vaibify_push`` / ``vaibify_pull`` (with
``vaib_`` shortcuts) in ``shellSetup.py``, and the completion scripts
kept registering against the retired spellings. A ``complete -F ...
vc_push`` for a command nobody can run is not an error in any shell --
it is silently inert -- so both helpers stopped completing anything
and nothing reported it.

Two files had to agree and nothing bound them, which is the shape this
repository has been bitten by before. These tests are that binding.

The existing coverage could not have caught it:
``testShellCompletionsShipInsideThePackage`` proves the scripts are
found by an installation, and its own docstring records that
completion "had never worked" while first-run setup recorded a marker
saying it had. Shipping a file and wiring it are different claims.
"""

import re
import subprocess

import pytest

from vaibify.install.shellSetup import _fsCompletionPathForShell


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
    for sShellName in ("zsh", "bash", "csh"):
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


@pytest.mark.parametrize("sShellName", ["bash", "zsh"])
def testEveryTransferAliasHasACompletionRegistration(sShellName):
    """Every push/pull alias setup creates is one completion registers.

    Kills: renaming the helper aliases without re-pointing the
    completion scripts, which leaves both helpers inert and silent.
    """
    sScript = _fsReadCompletionScript(sShellName)
    listMissing = [
        sAlias for sAlias in _flistAliasNamesShellSetupCreates()
        if ("push" in sAlias or "pull" in sAlias)
        and not re.search(rf"(?:complete[^\n]*|compdef[^\n]*)\b{sAlias}\b",
                          sScript)
    ]
    assert listMissing == [], (
        f"{sShellName} completion registers against no such command for "
        f"{listMissing}; a registration for a command that does not "
        f"exist is inert, not an error, so nothing would report it"
    )


@pytest.mark.parametrize("sShellName", ["bash", "zsh"])
def testCompletionRegistersNoRetiredCommandName(sShellName):
    """A registration must not name a command setup no longer creates.

    The inverse of the test above, and the half that would have caught
    the original break: the scripts kept ``vc_push`` / ``vc_pull``
    long after those names were gone.

    Kills: leaving a stale registration behind on the next rename.
    """
    sScript = _fsReadCompletionScript(sShellName)
    setLive = set(_flistAliasNamesShellSetupCreates())
    listRegistered = re.findall(
        r"^(?:complete[^\n]*?|compdef[^\n]*?)\s+([A-Za-z_][A-Za-z0-9_]*)\s*$",
        sScript, re.M,
    )
    listRetired = [
        sName for sName in listRegistered
        if ("push" in sName or "pull" in sName) and sName not in setLive
    ]
    assert listRetired == [], (
        f"{sShellName} completion still registers retired command "
        f"names {listRetired}; they complete nothing and say nothing"
    )


def _fsDriveBashCompletion(sLine, sFunction="_fnCompleteVaibify"):
    """Return the completions bash offers for one typed line.

    Drives the shipped script in a real bash, with the container probe
    replaced: the point is which POSITION gets container paths, not
    whether a daemon is reachable from a test.
    """
    sPath = _fsCompletionPathForShell("bash")
    saWords = sLine.split("|")
    sWordsLiteral = " ".join(f'"{sWord}"' for sWord in saWords)
    sProgram = f"""
source {sPath}
_fnReadVcConfig() {{ VC_NAME="proj"; VC_WORKSPACE="/workspace"; }}
_fnListContainerPaths() {{ printf 'Step01/\\nStep02/\\n'; }}
COMP_WORDS=({sWordsLiteral})
COMP_CWORD=$(( ${{#COMP_WORDS[@]}} - 1 ))
COMPREPLY=()
{sFunction} 2>/dev/null
printf '%s\\n' "${{COMPREPLY[@]}}"
"""
    tResult = subprocess.run(
        ["bash", "-c", sProgram], capture_output=True, text=True,
    )
    return [sLine for sLine in tResult.stdout.split("\n") if sLine]


@pytest.mark.falsification
def testPushCompletesContainerPathsOnlyForItsDestination():
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
    listSource = _fsDriveBashCompletion("vaibify|push|")
    listDestination = _fsDriveBashCompletion("vaibify|push|local.csv|")
    assert listSource == [], (
        "the push SOURCE offered container paths; it is read from the "
        f"researcher's own machine: {listSource}"
    )
    assert listDestination == ["Step01/", "Step02/"], (
        f"the push DESTINATION offered no container paths: "
        f"{listDestination}"
    )


@pytest.mark.falsification
def testPullCompletesContainerPathsOnlyForItsSource():
    """pull is push's mirror, so its positions are mirrored too.

    Kills: giving pull push's position logic, which offers container
    paths for the host destination and local files for the container
    source -- backwards in both arguments.
    """
    listSource = _fsDriveBashCompletion("vaibify|pull|")
    listDestination = _fsDriveBashCompletion("vaibify|pull|Step01/out.csv|")
    assert listSource == ["Step01/", "Step02/"], (
        f"the pull SOURCE offered no container paths: {listSource}"
    )
    assert listDestination == [], (
        "the pull DESTINATION offered container paths; it is written "
        f"to the researcher's own machine: {listDestination}"
    )


def testTheSubcommandListStillCompletes():
    """The push/pull branch must not swallow the ordinary completion."""
    listSubcommands = _fsDriveBashCompletion("vaibify|")
    assert "push" in listSubcommands and "build" in listSubcommands, (
        f"the subcommand list regressed: {listSubcommands}"
    )
