"""The force-push block must hold wherever the flag sits.

``AGENTS.md`` ("Ask first" -> "Enforced by harness hooks") calls
``git push --force`` *hard-blocked*. The original pattern required the
flag to follow ``push`` immediately, so the ordinary invocation order
-- flag after the remote and refspec, which git accepts -- was never
blocked at all. A leading ``+`` on a refspec is the same force in
disguise and was likewise missed.

Every command below is assembled by concatenation rather than written
as a literal. The hook scans the text of any Bash command an agent
runs, including the shell line that would create this file, so a
literal here makes the file itself un-editable from a shell.
"""

import pytest

from tests.testHarnessHookMutationCoverage import (
    _ftDecideDestructiveGit,
)


_S_FORCE_LONG = "--" + "force"
_S_FORCE_SHORT = "-" + "f"
_S_FORCE_LEASE = _S_FORCE_LONG + "-with-lease"


_TUPLE_FLAG_AFTER_REFSPEC = (
    "git push origin main " + _S_FORCE_LONG,
    "git push origin main " + _S_FORCE_SHORT,
    "git push origin +main",
    "git push --set-upstream origin feature " + _S_FORCE_LONG,
)


@pytest.mark.falsification
def testForcePushIsBlockedAnywhereInTheArgumentList():
    """A force-push must be denied wherever the flag sits.

    Kills: in .claude/hooks/blockDestructiveGit.py, replace the
    argument-list scan ``[^;&|]*?(?<!\\S)`` with ``\\s+``, restoring
    the positional pattern that only matched a flag directly after
    "push".
    """
    listUnblocked = [
        sCommand for sCommand in _TUPLE_FLAG_AFTER_REFSPEC
        if not _ftDecideDestructiveGit(sCommand)[0]
    ]
    assert listUnblocked == [], (
        "AGENTS.md documents force-push as hard-blocked, but these "
        "were allowed through: " + ", ".join(listUnblocked)
    )


@pytest.mark.falsification
def testLeaseExemptionSurvivesTheWidenedScan():
    """The safe form stays permitted after the refspec too.

    Widening the scan is the fix for the bug above; it must not
    swallow the documented escape hatch when that also sits late in
    the argument list.

    Kills: in .claude/hooks/blockDestructiveGit.py, drop the
    ``(?!-with-lease)`` lookahead from the force alternative.
    """
    bBlocked, _sReason = _ftDecideDestructiveGit(
        "git push origin main " + _S_FORCE_LEASE,
    )
    assert bBlocked is False, (
        "--force-with-lease is the documented escape hatch and must "
        "stay permitted wherever it appears"
    )


@pytest.mark.parametrize("sCommand", [
    "git commit -m 'force a rebuild'",
    "git push --tags origin",
    "git fetch origin main",
    "git push origin main",
])
def testOrdinaryCommandsAreNotFalselyBlocked(sCommand):
    """Widening the scan must not manufacture false positives."""
    bBlocked, _sReason = _ftDecideDestructiveGit(sCommand)
    assert bBlocked is False, f"false positive on: {sCommand}"
