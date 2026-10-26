"""What the hub's warning sign calls blocked, from recorded evidence.

An item needs a refusal vaibify recorded AND a fresh reading that its
cause is still there. The refusal is written here by the conversion's
own failure recorder from the real ``BuildKitRequiredError``, so the
failure class the sign reads is the one the lane writes, not a copy of
it typed into a fixture.
"""

import threading

import pytest

from vaibify.config import pendingConversions
from vaibify.docker.buildKitRequirement import BuildKitRequiredError
from vaibify.gui import conversionLifecycle, machineAttention


S_HOST_NAME = "hostProjectAlpha"


def _fnWritePendingConversion(sHostName):
    pendingConversions.fnWriteConversionRecord(
        pendingConversions.fdictBuildConversionRecord(
            sHostName, sHostName.lower() + "-container",
            {"sDirectory": "/clones/" + sHostName,
             "sConfigPath": "/clones/" + sHostName + "/vaibify.yml"},
            {}, {"sSource": "archive"}, None, None))


def _fnRecordTheLanesFailure(sHostName, error):
    """Record a failure exactly as the conversion worker records one."""
    dictControl = {"eventCancel": threading.Event(),
                   "lockLive": threading.Lock(), "dictLive": {}}
    conversionLifecycle._fnRecordFailure(
        sHostName, error, dictControl, lambda sLine: None)


def _ferrorBuildxRefusal():
    return BuildKitRequiredError(
        "/vaibify/containerImage/Dockerfile.claudeArmOnIntel",
        ["line 24 uses $BUILDPLATFORM"],
        "sudo port install docker-buildx-plugin")


@pytest.mark.falsification
def testAConversionStoppedForBuildxIsBlockedOnlyWhileBuildxIsMissing():
    """Kills: a red sign that outlives the cause the researcher fixed.

    The oracle is the ruling that red means verified blocked: once a
    fresh reading finds buildx, nothing is blocked any more.
    """
    _fnWritePendingConversion(S_HOST_NAME)
    _fnRecordTheLanesFailure(S_HOST_NAME, _ferrorBuildxRefusal())
    listItems = machineAttention.flistDescribeMachineAttention(
        {"bBuildxWorks": False})
    assert [dictItem["sSeverity"] for dictItem in listItems] == [
        machineAttention.S_SEVERITY_BLOCKED]
    assert listItems[0]["sSentence"].startswith(
        f"Containerizing '{S_HOST_NAME}' is stopped. "
        "Dockerfile.claudeArmOnIntel needs docker buildx")
    assert machineAttention.flistDescribeMachineAttention(
        {"bBuildxWorks": True}) == []


def testAConversionStoppedForAnotherReasonIsNotABuildxBlock():
    _fnWritePendingConversion(S_HOST_NAME)
    _fnRecordTheLanesFailure(S_HOST_NAME, RuntimeError("the network dropped"))
    assert machineAttention.flistDescribeMachineAttention(
        {"bBuildxWorks": False}) == []


def testAConversionStillObtainingIsNotBlocked():
    _fnWritePendingConversion(S_HOST_NAME)
    assert machineAttention.flistDescribeMachineAttention(
        {"bBuildxWorks": False}) == []


def testNoPendingConversionMeansNothingIsBlocked():
    assert machineAttention.flistDescribeMachineAttention(
        {"bBuildxWorks": False}) == []
