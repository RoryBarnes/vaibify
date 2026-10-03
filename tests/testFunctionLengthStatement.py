"""One statement about function length, everywhere it is made.

CLAUDE.md says functions are usually 20-30 lines, as a guideline: split
for reuse or a genuine conceptual boundary, never to satisfy a line
count. The guide written into every container for in-container agents
and the contributor documentation must say the same thing, or an agent
inside a container is held to a stricter rule than the repository's own.
"""

import pathlib
import re

import pytest

PATH_REPOSITORY = pathlib.Path(__file__).resolve().parent.parent
PAT_HARD_CAP_STATEMENT = re.compile(
    r"(under|fewer than|less than|within) (~)?20 lines", re.IGNORECASE)
LIST_STATEMENT_FILES = [
    "vaibify/containerImage/entrypoint.sh",
    "docs/developers.md",
]


@pytest.mark.falsification
def testTheContainerAgentGuideStatesTheGuidelineNotAHardCap():
    """Kills: restoring the stricter 'under 20 lines' rule in the guide."""
    sGuide = (PATH_REPOSITORY / "vaibify/containerImage/entrypoint.sh"
              ).read_text()
    assert "usually 20-30 lines" in sGuide
    assert "never to satisfy a line count" in sGuide


@pytest.mark.parametrize("sRelativePath", LIST_STATEMENT_FILES)
def testNoShippedDocumentStatesAHardTwentyLineCap(sRelativePath):
    sText = (PATH_REPOSITORY / sRelativePath).read_text()
    assert PAT_HARD_CAP_STATEMENT.search(sText) is None
