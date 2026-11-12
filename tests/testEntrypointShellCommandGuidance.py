"""The in-container agent guide says how to write a deletion.

Claude Code keeps its dangerous-removal checks even when started with
``--dangerously-skip-permissions``: a deletion whose target cannot be
read from the command text (a relative wildcard after a ``cd``, a
command substitution, a leading variable) stops the agent until the
researcher answers. The guide every agent reads (CLAUDE.md, symlinked
as AGENTS.md and GEMINI.md) teaches literal absolute targets instead,
and forbids hiding a deletion from the check, which would trade a
prompt for an unchecked removal.
"""

import pytest

from vaibify.resources import fpathContainerImageRoot


def _fsGeneratedGuide():
    sEntrypoint = (fpathContainerImageRoot() / "entrypoint.sh").read_text(
        encoding="utf-8")
    iStart = sEntrypoint.index("<< 'CLAUDEMD'\n")
    return sEntrypoint[iStart:sEntrypoint.index("\nCLAUDEMD\n", iStart)]


def _fsShellCommandsSection():
    sGuide = _fsGeneratedGuide()
    iStart = sGuide.index("## Shell commands\n")
    iEnd = sGuide.index("\n## ", iStart + 1)
    return sGuide[iStart:iEnd]


@pytest.mark.falsification
def testTheGuideTeachesDeletionsTheCheckerCanRead():
    """Kills: dropping the Shell commands section from the generated guide."""
    sSection = _fsShellCommandsSection()
    for sFact in (
        "--dangerously-skip-permissions", "literal absolute path",
        "relative wildcard after a `cd`", "`$(...)`",
        "starts\n  with a variable", '"${sRunDir:?}"',
        "chain with `&&`, never `;`",
    ):
        assert sFact in sSection, sFact


def testTheGuideForbidsHidingADeletionFromTheCheck():
    sSection = _fsShellCommandsSection()
    assert "safety net" in sSection
    for sEvasion in ("`find -delete`", "`shutil.rmtree`", "a script file"):
        assert sEvasion in sSection, sEvasion
