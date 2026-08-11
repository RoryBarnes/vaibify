"""`python3 -c "..."` is a command, not a missing script.

Reported when a researcher tried to run a step whose command was

    python3 -c "import time; time.sleep(600)"

Pre-flight refused it with ``command not found: -c``. The script-path
extractor took the token after ``python``/``python3`` to be a script
file unconditionally, so it read the FLAG as a filename, and the
validator then looked for a file called ``-c`` and, failing that, a
program called ``-c``.

Nothing about this is host-specific. The same validator runs for a
containerized project, so any step invoking inline code has always been
un-runnable from the dashboard — it simply had not been tried, because
steps in shipped templates all invoke script files.

The empty string is a REAL answer here, meaning "this command runs no
script file", and callers must treat it as "nothing to check" rather
than falling back to a token. The pre-flight caller's fallback is
correct and is asserted below: it checks that the INTERPRETER exists,
which is the useful check for a command with no script.
"""

import pytest

from vaibify.gui.commandUtilities import (
    fsExtractScriptPath,
    ftExtractScriptPathForLanguage,
)
from vaibify.gui.pipelineValidator import _fsExtractScriptPath


@pytest.mark.falsification
def testInlineCodeAndModulesNameNoScriptFile():
    """A flag's argument is code or a module, never a path.

    Kills: taking the token after the interpreter as the script,
    which reads `-c` as a filename and refuses the step.
    """
    assert fsExtractScriptPath(
        'python3 -c "import time; time.sleep(600)"',
    ) == ""
    assert fsExtractScriptPath("python3 -m pytest") == ""
    assert fsExtractScriptPath("python3") == ""


@pytest.mark.falsification
def testAnOrdinaryScriptIsStillFound():
    """The other direction, and the one with real consequences.

    Pre-flight exists to catch a step whose script is missing or
    misspelled BEFORE a long run starts. An extractor that answered ""
    for everything would pass the test above and silently switch that
    protection off for every step in every project.

    Kills: returning "" unconditionally.
    """
    assert fsExtractScriptPath(
        "python3 makeNumbers.py --output numbers.json",
    ) == "makeNumbers.py"
    assert fsExtractScriptPath("makeNumbers.py") == "makeNumbers.py"


@pytest.mark.falsification
def testFlagsThatTakeAValueDoNotHideTheScript():
    """`-W ignore run.py` runs run.py, not a file called "ignore".

    Kills: skipping only the flag and taking its VALUE as the script,
    which refuses a step that is perfectly well formed.
    """
    assert fsExtractScriptPath("python3 -W ignore run.py") == "run.py"
    assert fsExtractScriptPath("python3 -u run.py") == "run.py"


def testTheLanguageIsStillKnownForInlineCode():
    """Inline code is Python whether or not it lives in a file.

    The dependency scanner keys on the language, so losing it would
    reclassify these commands as "unknown" and drop them from the
    scan.
    """
    assert ftExtractScriptPathForLanguage(
        'python3 -c "import time"',
    ) == ("", "python")


@pytest.mark.falsification
def testPreflightFallsBackToCheckingTheInterpreter():
    """With no script to check, check that the interpreter exists.

    This is what makes "" safe for the pre-flight caller: the command
    still gets a meaningful check, just of `python3` rather than of a
    file. Returning None instead would skip the step's validation
    entirely.

    Kills: dropping the fallback, so a command with no script file is
    not validated at all.
    """
    assert _fsExtractScriptPath(
        'python3 -c "import time; time.sleep(600)"',
    ) == "python3"
