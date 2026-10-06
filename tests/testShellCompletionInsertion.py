"""A name from inside a container reaches the command line as ONE word.

Container file names are chosen by whoever can write there, and the
in-container agent can. A completion script that offered a name raw
would turn a file called ``$(...)`` into a command the researcher runs
by pressing TAB and then Enter. The scripts' only defence is to hand the
shell a name as DATA and let the shell do its own escaping -- bash does
not, so its script escapes with ``printf %q`` -- and whether that
defence holds is a property of the shell reading the line, which no stub
of the shell can show.

So these tests do what the researcher does. A real interactive shell is
started on a pseudo-terminal with the script loaded and a stub
``vaibify``; the line is typed, TAB pressed, then Enter. The stub's log
then holds the arguments the shell ACTUALLY executed the command with,
after the completion inserted the name. Reading the candidate list
instead would assert what the script thinks it said.
"""

import os

import pytest

from tests.shellCompletionHarness import (
    fdictBuildStubWorld,
    flistReadHelperCalls,
    flistTabCompleteAndRun,
    fsRequireShell,
)


LIST_SHELLS = ["bash", "zsh", "fish"]

S_HOSTILE_NAME = (
    "it's \"q\" $(touch PWNED) `touch PWNED` & ; | > < ~ ! * ? # "
    "( ) { } [ ] \\ end"
)


def _flistCompleteOneName(sShellName, tmp_path, sName, sTyped):
    """Return (calls, files left in the shell's directory) after TAB, Enter."""
    sExecutable = fsRequireShell(sShellName)
    dictWorld = fdictBuildStubWorld(str(tmp_path / "world"), [sName])
    sWorkDirectory = tmp_path / "work"
    sWorkDirectory.mkdir()
    listCalls = flistTabCompleteAndRun(
        sShellName, sExecutable, sTyped, dictWorld, str(sWorkDirectory),
    )
    return listCalls, sorted(os.listdir(sWorkDirectory)), dictWorld


def _fnAssertTheHostileNameArrivesAsOneInertWord(tmp_path, sShellName, sTyped):
    """Complete the hostile name; the shell must run the command it spells."""
    listCalls, listFiles, _dictWorld = _flistCompleteOneName(
        sShellName, tmp_path, S_HOSTILE_NAME, sTyped,
    )
    assert listCalls[-1] == ["pull", S_HOSTILE_NAME], listCalls
    assert listFiles == [], (
        f"{sShellName} ran a command out of a completed file name: "
        f"{listFiles}"
    )


# Each hostile-name test comes in two functions, and only the bash one is
# a falsification test. A falsification test must run, and fail on a
# mutant, on every lane that replays it, and the lanes that replay it do
# not all carry zsh and fish; the other shells' tests are required on the
# unit lanes instead (VAIBIFY_REQUIRE_SHELLS), which do.


@pytest.mark.falsification
def testAHostileNameIsInsertedAsOneInertWordInBash(tmp_path):
    """The executed command received the name, whole, and ran nothing.

    Kills: offering the name unescaped, which makes a file called
    ``$(...)`` a command the researcher runs by pressing TAB and Enter.
    """
    _fnAssertTheHostileNameArrivesAsOneInertWord(
        tmp_path, "bash", "vaibify pull ",
    )


@pytest.mark.parametrize("sShellName", ["zsh", "fish"])
def testAHostileNameIsInsertedAsOneInertWordInTheOtherShells(
    tmp_path, sShellName,
):
    _fnAssertTheHostileNameArrivesAsOneInertWord(
        tmp_path, sShellName, "vaibify pull ",
    )


@pytest.mark.falsification
@pytest.mark.parametrize("sOpenQuote", [
    pytest.param('"', id="doubleQuote"), pytest.param("'", id="singleQuote"),
])
def testAHostileNameStaysInertInsideAnOpenQuoteInBash(tmp_path, sOpenQuote):
    """A quote the researcher already opened is not an escape hatch.

    Typing ``vaibify pull "`` and pressing TAB completes INSIDE the
    quote, where the escaping that suits a bare word is wrong (a
    backslash before a space stays in the name) and the characters that
    close the quote are the dangerous ones. A name that contains the
    very quote it is inserted into must still be one word.

    Kills: escaping every name as a bare word regardless of the quote
    the word began with, or not escaping inside a quote at all.
    """
    _fnAssertTheHostileNameArrivesAsOneInertWord(
        tmp_path, "bash", f"vaibify pull {sOpenQuote}",
    )


@pytest.mark.parametrize("sShellName", ["zsh", "fish"])
@pytest.mark.parametrize("sOpenQuote", [
    pytest.param('"', id="doubleQuote"), pytest.param("'", id="singleQuote"),
])
def testAHostileNameStaysInertInsideAnOpenQuoteInTheOtherShells(
    tmp_path, sShellName, sOpenQuote,
):
    _fnAssertTheHostileNameArrivesAsOneInertWord(
        tmp_path, sShellName, f"vaibify pull {sOpenQuote}",
    )


@pytest.mark.parametrize("sShellName", LIST_SHELLS)
def testADirectoryNameWithASpaceCanBeDescendedInto(tmp_path, sShellName):
    """A directory keeps its slash and its space through the shell."""
    listCalls, _listFiles, _dictWorld = _flistCompleteOneName(
        sShellName, tmp_path, "Step 01/", "vaibify pull ",
    )
    assert listCalls[-1][0] == "pull"
    assert listCalls[-1][1].rstrip() == "Step 01/", listCalls


@pytest.mark.parametrize("sShellName", LIST_SHELLS)
@pytest.mark.parametrize("sTyped", [
    'vaibify pull "My D',
    "vaibify pull 'My D",
    "vaibify pull My\\ D",
])
def testAHalfTypedQuotedWordIsCompletedAndHandedOverUnquoted(
    tmp_path, sShellName, sTyped,
):
    """Quotes the researcher typed are not part of the name asked about.

    The helper must be asked about ``My D`` however the shell was told
    to spell it, and the completed name must arrive whole. A FILE is
    completed, not a directory: a directory deliberately leaves an open
    quote open so the next TAB can go deeper, and then Enter would only
    ask for the rest of the quote.
    """
    listCalls, _listFiles, dictWorld = _flistCompleteOneName(
        sShellName, tmp_path, "My Document.csv", sTyped,
    )
    assert flistReadHelperCalls(dictWorld)[0][-1] == "My D", listCalls
    assert listCalls[-1][0] == "pull"
    assert listCalls[-1][1].rstrip() == "My Document.csv", listCalls
