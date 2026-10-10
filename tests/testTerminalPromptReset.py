"""A container terminal's bash resets the input modes a dead program left on.

A full-screen program turns on xterm mouse reporting (DECSET 1003/1006)
and turns it off when it exits. A program killed by SIGKILL -- an AI
agent the kernel killed for lack of memory -- never turns it off, so
every mouse move after it prints garbage at the prompt. The wrapper
that starts a container terminal now hands bash an rcfile that reads
the researcher's own ``~/.bashrc`` and then appends a prompt hook,
LAST, that switches those modes off before every prompt and returns the
status it was given.

These tests pin the wrapper's shape; ``testTerminalPromptResetLive.py``
runs it in a real container under a real pty.
"""

import shlex

import pytest

from vaibify.gui import terminalContainment
from vaibify.gui.terminalContainment import (
    S_PROMPT_RESET_RCFILE,
    TerminalContainmentError,
    fsBuildGroupReportingCommand,
)


S_MARKER = "/tmp/.vaibifyTerminalGroup.0123456789abcdef"


@pytest.mark.falsification
def testABashTerminalGetsTheResetRcfileThenThePlainFallback():
    """Kills: dropping the plain-shell exec that follows a failed rcfile."""
    sCommand = fsBuildGroupReportingCommand("/bin/bash", S_MARKER)
    assert "mktemp -d" in sCommand
    assert '--rcfile "$sRcDirectory/rc"' in sCommand
    assert shlex.quote(S_PROMPT_RESET_RCFILE) in sCommand
    assert sCommand.endswith("exec /bin/bash"), (
        "a failed rcfile step must still leave the researcher a shell")
    iMarker = sCommand.index(f"mv {S_MARKER}.partial {S_MARKER}")
    assert iMarker < sCommand.index("mktemp -d"), (
        "the group is reported before anything else runs")


@pytest.mark.parametrize("sShell", ["/bin/sh", "/usr/bin/zsh", "/bin/bash -l"])
def testAnyOtherShellStartsPlain(sShell):
    sCommand = fsBuildGroupReportingCommand(sShell, S_MARKER)
    assert "--rcfile" not in sCommand
    assert "mktemp" not in sCommand
    assert sCommand.endswith(f"exec {sShell}")


def testTheShellAllowlistStillRefusesMetacharacters():
    for sShell in ("/bin/bash; rm -rf /", "/bin/bash $(id)", "bash`x`"):
        with pytest.raises(TerminalContainmentError):
            fsBuildGroupReportingCommand(sShell, S_MARKER)


def testTheRcfileDeletesItselfReadsTheUsersFileAndAppendsLast():
    listLines = S_PROMPT_RESET_RCFILE.splitlines()
    assert listLines[0].startswith('rm -f -- "${BASH_SOURCE[0]}"')
    assert "rmdir" in listLines[0]
    assert listLines[1] == "[ -f ~/.bashrc ] && . ~/.bashrc"
    assert "PROMPT_COMMAND+=(fnVaibifyResetInputModes)" in (
        S_PROMPT_RESET_RCFILE)
    for sMode in ("1000", "1002", "1003", "1005", "1006", "1015", "1004"):
        assert f"\\033[?{sMode}l" in S_PROMPT_RESET_RCFILE
    assert 'return "$iStatus"' in S_PROMPT_RESET_RCFILE


def testTheRcfileIsAConstantNotACallerValue():
    """The wrapper takes no rcfile text from its caller."""
    import inspect
    listParameters = list(inspect.signature(
        terminalContainment.fsBuildGroupReportingCommand).parameters)
    assert listParameters == ["sShellCommand", "sMarkerPath"]
