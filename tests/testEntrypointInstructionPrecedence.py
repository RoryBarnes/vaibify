"""The in-container agent guide yields to the researcher's instructions.

The guide every agent reads (CLAUDE.md, symlinked as AGENTS.md and
GEMINI.md) is vaibify's defaults plus the rules of the container. It
says so before anything else: the researcher's own instructions win
wherever the two conflict, except for the security boundaries and the
user-only actions, and the file is rewritten at every start, so lasting
instructions belong in the project context file. Its coding conventions
are stated as defaults the researcher may replace, never as rules.

The heredoc is quoted (``<< 'CLAUDEMD'``), so the shell expands
nothing: the text between the delimiters is exactly what the container
writes.
"""

import pytest

from vaibify.resources import fpathContainerImageRoot


def _fsGeneratedGuide():
    sEntrypoint = (fpathContainerImageRoot() / "entrypoint.sh").read_text(
        encoding="utf-8")
    iStart = sEntrypoint.index("<< 'CLAUDEMD'\n")
    return sEntrypoint[iStart:sEntrypoint.index("\nCLAUDEMD\n", iStart)]


def _fsSectionStartingWith(sHeading):
    sGuide = _fsGeneratedGuide()
    iStart = sGuide.index(sHeading + "\n")
    iEnd = sGuide.find("\n## ", iStart + 1)
    return sGuide[iStart:] if iEnd < 0 else sGuide[iStart:iEnd]


def _flistSectionHeadings():
    return [sLine for sLine in _fsGeneratedGuide().splitlines()
            if sLine.startswith("## ")]


@pytest.mark.falsification
def testTheGuideOpensBySayingTheResearchersInstructionsWin():
    """Kills: dropping the precedence section from the generated guide."""
    assert _flistSectionHeadings()[0] == "## Whose instructions win"
    sSection = _fsSectionStartingWith("## Whose instructions win")
    for sFact in (
        "take precedence over this file",
        "<repository>/.vaibify/AGENTS.md",
        "security", "user-only actions",
        "never to perform", "the hub refuses it",
        "rewritten every time the container starts",
        "put lasting instructions in the project context file",
    ):
        assert sFact in sSection, sFact


def testTheUserOnlyActionsTheSectionPointsToComeAfterIt():
    """'The user-only actions listed below' must name something below."""
    sGuide = _fsGeneratedGuide()
    iPrecedence = sGuide.index("## Whose instructions win\n")
    assert sGuide.index("is user-only", iPrecedence) > iPrecedence


@pytest.mark.falsification
def testTheConventionsAreDefaultsTheResearcherCanReplace():
    """Kills: restoring the unconditional conventions heading."""
    assert "## Conventions" not in _flistSectionHeadings()
    sSection = _fsSectionStartingWith("## Conventions (defaults)")
    assert "unless the researcher's instructions declare" in sSection
    assert "theirs win" in sSection


def testNoConventionIsPhrasedAsAnOrder():
    sSection = _fsSectionStartingWith("## Conventions (defaults)")
    assert "Follow Hungarian" not in sSection
    assert "Hungarian notation for variable names" in sSection


def testTheConventionsPointToTheGracefulExitsChecker():
    sSection = _fsSectionStartingWith("## Conventions (defaults)")
    assert "**graceful-exits** skill has a checker" in sSection
