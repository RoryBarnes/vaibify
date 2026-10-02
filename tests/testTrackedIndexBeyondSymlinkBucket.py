"""The tracked-index classifier names a path beyond a symbolic link as such.

The in-container observation reports ``beyondSymlink`` for a tracked path
whose parent directory was replaced by a link. The classifier must put
that path in its own bucket so the refusal says what happened, rather
than letting it fall through to a generic bucket or to the eligible set.
"""

import pytest

from vaibify.gui import agentCouncilSnapshotScope


def ftFindNoExcludedComponent(sPath):
    return None


@pytest.mark.falsification
def testAPathBeyondASymbolicLinkLandsInItsOwnBucket():
    """The bucket is what lets the refusal explain the link.

    Kills: the classifier no longer recognizing the beyondSymlink type.
    """
    dictRead = {"dictEntries": {"code/step.py": {
        "sMode": "100644", "listStages": [0], "sType": "beyondSymlink"}}}
    dictAnswer = agentCouncilSnapshotScope.fdictInterpretTrackedIndex(
        dictRead, ftFindNoExcludedComponent)
    assert dictAnswer["listBeyondSymlink"] == ["code/step.py"]
    assert dictAnswer["listUnrepresentable"] == []
    assert dictAnswer["dictEligible"] == {}
