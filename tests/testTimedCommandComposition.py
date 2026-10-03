"""The CPU-time wrapper's composition and its marker handling, without a daemon.

Source: ``pipelineRunner._fsWrapWithTime`` and ``_fsAbsorbCpuMarker``.
``tests/testTimedCommandsLive.py`` runs the same code in a real container
with GNU time; these tests pin what can be pinned without one.
"""

import re
import shlex

import pytest

from vaibify.gui import pipelineRunner

S_MARKER = "__VAIBIFY_CPU__"


def _fsShellArgumentTimeIsGiven(sWrapped):
    """Return the one argument ``/bin/bash -c`` receives under ``time``."""
    matchShell = re.search(
        r"/usr/bin/time -f '__VAIBIFY_CPU__ %U %S' /bin/bash -c "
        r"('(?:[^']|'\\'')*')", sWrapped)
    assert matchShell, sWrapped
    listWords = shlex.split(matchShell.group(1))
    assert len(listWords) == 1
    return listWords[0]


@pytest.mark.falsification
@pytest.mark.parametrize("sCommand", [
    "cd d && python run.py",
    "export A=1 && echo $A",
    "A=1 python run.py",
    "echo it's \"quoted\" && echo $(date) `id`",
    "python - <<'EOF'\nprint('multi')\nEOF",
    "echo $'tab\\there' ; exit 3",
])
def testTheCommandReachesTheShellAsOneQuotedArgument(sCommand):
    """Whatever the researcher wrote arrives intact, as a single word.

    Kills: composing the command as time's own argument vector, which
    makes ``cd``/``export``/``A=1`` programs and times only the first
    command of a chain.
    """
    sWrapped = pipelineRunner._fsWrapWithTime(sCommand)
    assert _fsShellArgumentTimeIsGiven(sWrapped) == sCommand


def testTheFallbackBranchRunsTheCommandWithoutTime():
    sWrapped = pipelineRunner._fsWrapWithTime("cd d && ls")
    assert "else cd d && ls; fi" in sWrapped
    assert sWrapped.endswith("} 2>&1")


# ---------------------------------------------------------------------
# The marker GNU time writes after the command's last output
# ---------------------------------------------------------------------


@pytest.mark.falsification
def testAMarkerGluedToAnUnterminatedLastLineIsSplitOff():
    """GNU time writes the marker right after the last output.

    A command whose final line has no newline yields one glued line.

    Kills: recognising the marker only at the start of a line, which
    loses the step's CPU reading and prints the marker in the run log.
    """
    dictAccum = {"fCpu": 0.0}
    sForward = pipelineRunner._fsAbsorbCpuMarker(
        f"no newline at the end{S_MARKER} 1.25 0.50", dictAccum)
    assert sForward == "no newline at the end"
    assert dictAccum["fCpu"] == pytest.approx(1.75)


def testAMarkerOnItsOwnLineIsAbsorbedAndNothingIsForwarded():
    dictAccum = {"fCpu": 0.0}
    assert pipelineRunner._fsAbsorbCpuMarker(
        f"{S_MARKER} 0.10 0.05", dictAccum) is None
    assert dictAccum["fCpu"] == pytest.approx(0.15)


@pytest.mark.parametrize("sLine", ["", "plain output", "  indented", "%U %S"])
def testAnOrdinaryLineIsForwardedUnchangedAndTouchesNoReading(sLine):
    dictAccum = {"fCpu": 9.0}
    assert pipelineRunner._fsAbsorbCpuMarker(sLine, dictAccum) == sLine
    assert dictAccum["fCpu"] == 9.0
