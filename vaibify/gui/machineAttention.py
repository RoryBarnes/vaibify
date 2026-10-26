"""What blocks Docker work on this machine, for the hub's warning sign.

The hub's warning sign is red only for something verified to be
blocked (ruled 2026-10-26). An item here rests on two pieces of
evidence and never on a prediction: a refusal vaibify recorded, and a
fresh reading of the machine showing that its cause is still there.
Today that is one case: a containerization stopped because only
docker buildx can build the stack it needs, on a machine that still
has no buildx. Installing buildx clears the item at the next reading,
and the conversion's own window still offers Try again.

Every sentence is the hub's; the page renders it verbatim.
"""

__all__ = [
    "S_SEVERITY_BLOCKED",
    "flistDescribeMachineAttention",
]

from vaibify.config import pendingConversions
from vaibify.docker.buildKitRequirement import S_FAILURE_BUILDX_MISSING


S_SEVERITY_BLOCKED = "blocked"


def _fdictDescribeBuildxRefusal(dictRecord):
    sHostName = str(dictRecord.get("sHostName") or "")
    sSentence = str((dictRecord.get("dictFailure") or {}).get("sSentence") or "")
    return {
        "sItemId": "buildx-missing:" + sHostName,
        "sSeverity": S_SEVERITY_BLOCKED,
        "sSentence": f"Containerizing '{sHostName}' is stopped. {sSentence}",
    }


def flistDescribeMachineAttention(dictFacts):
    """Return the hub's blocked items for this machine; empty when none."""
    if dictFacts.get("bBuildxWorks"):
        return []
    return [
        _fdictDescribeBuildxRefusal(dictRecord)
        for dictRecord in pendingConversions.flistReadConversionRecords()
        if (dictRecord.get("dictFailure") or {}).get("sFailureClass")
        == S_FAILURE_BUILDX_MISSING
    ]
