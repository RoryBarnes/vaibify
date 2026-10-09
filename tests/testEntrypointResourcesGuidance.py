"""The in-container agent guide says what a memory limit costs.

An agent that fans out subagents and background jobs is killed by the
kernel at the container's memory limit, and its scratch files in /tmp
do not survive the restart that raises it. The guide every agent reads
(CLAUDE.md, symlinked as AGENTS.md and GEMINI.md) says so in an
agent-neutral Resources section, naming where the limit is read rather
than any number, because the number is the researcher's choice.
"""

import re

import pytest

from vaibify.resources import fpathContainerImageRoot


def _fsGeneratedGuide():
    sEntrypoint = (fpathContainerImageRoot() / "entrypoint.sh").read_text(
        encoding="utf-8")
    iStart = sEntrypoint.index("<< 'CLAUDEMD'\n")
    return sEntrypoint[iStart:sEntrypoint.index("\nCLAUDEMD\n", iStart)]


def _fsResourcesSection():
    sGuide = _fsGeneratedGuide()
    iStart = sGuide.index("## Resources\n")
    iEnd = sGuide.index("\n## ", iStart + 1)
    return sGuide[iStart:iEnd]


@pytest.mark.falsification
def testTheGuideTellsTheAgentWhereItsMemoryLimitIs():
    """Kills: dropping the Resources section from the generated guide."""
    sSection = _fsResourcesSection()
    for sFact in (
        "/sys/fs/cgroup/memory.max", "memory.stat", "kills a process",
        "headroom", "Background jobs outlive", "/tmp", "/workspace",
    ):
        assert sFact in sSection, sFact


def testTheGuideNamesNoMemoryNumber():
    """The limit is the researcher's to set; the guide says where to read it."""
    assert not re.search(r"\d\s*(GB|GiB|MB|MiB)", _fsResourcesSection())


def testTheGuideIsAgentNeutral():
    sSection = _fsResourcesSection()
    for sProvider in ("Claude", "Codex", "Gemini"):
        assert sProvider not in sSection
