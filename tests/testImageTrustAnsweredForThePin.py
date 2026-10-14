"""An answer about the author's pinned image covers what vaibify stacks on it.

A reader who containerizes from the pinned image is asked, in the
wizard, how the author's code may run -- before the image is obtained,
so the answer names the PIN rather than a digest nobody has yet. The
launch guard extends it only to the IDs the host-held origin record
binds to that pin (the obtained base, and the image vaibify stacked
agents on), so a restack is not asked again while any other image still
is. The conversion route and the launch guard are driven for real; the
clone's envelope is the one boundary doubled.
"""

import os

import pytest

from tests.testCoverageRoutesBRegistry import (  # noqa: F401
    S_HOST_NAME,
    S_NEW_NAME,
    fixtureIsolateRegistryAndHome,
    fsConvertUrl,
    tupleHostClient,
)
from vaibify.config import imageOrigins, imageTrust, registryManager
from vaibify.docker import containerManager
from vaibify.reproducibility import reproductionSource

S_PIN = "sha256:" + "1" * 64
S_OTHER_PIN = "sha256:" + "2" * 64
S_BASE_ID = S_PIN
S_STACKED_ID = "sha256:" + "3" * 64
S_RESTACKED_ID = "sha256:" + "4" * 64
S_STRANGER_ID = "sha256:" + "5" * 64


def fdictOrigin(sPin=S_PIN, sRunning=S_STACKED_ID):
    return imageOrigins.fdictBuildOriginRecord(
        sPin, S_BASE_ID, sRunning, ["claude"], "archive", "zenodo",
        "10.5281/zenodo.1", "linux/amd64", "linux/amd64", False,
    )


def fdictObtainedProject(dictTrustRecord):
    return {
        "sName": S_NEW_NAME,
        "dictImageSource": {"sSource": "archive"},
        "dictImageTrust": dictTrustRecord,
    }


def fdictImage(sId):
    return {"sId": sId, "dictLabels": {}, "iSizeBytes": 1,
            "sUser": "researcher", "listEntrypoint": ["/entry"]}


def fdictPinAnswer(sChoice="as-built", sPin=S_PIN):
    return imageTrust.fdictBuildTrustRecord("", sChoice, False, sPin)


# -- the rule ----------------------------------------------------------

@pytest.mark.falsification
def testAnAnswerForThePinCoversTheImageStackedOnIt():
    """Kills: an answer naming the pin being read as answering nothing."""
    dictPosture = imageTrust.fdictResolveLaunchPosture(
        fdictObtainedProject(fdictPinAnswer()), fdictImage(S_STACKED_ID),
        S_NEW_NAME, fdictOrigin(),
    )
    assert dictPosture == {
        "sMode": "as-built", "bWithCredentials": False,
        "sImageDigest": S_STACKED_ID,
    }
    imageTrust.fdictResolveLaunchPosture(
        fdictObtainedProject(fdictPinAnswer()), fdictImage(S_BASE_ID),
        S_NEW_NAME, fdictOrigin(),
    )


def testARestackOnTheSamePinIsNotAskedAgain():
    """The agents change, the author's image does not: still answered."""
    imageTrust.fdictResolveLaunchPosture(
        fdictObtainedProject(fdictPinAnswer()), fdictImage(S_RESTACKED_ID),
        S_NEW_NAME, fdictOrigin(sRunning=S_RESTACKED_ID),
    )


@pytest.mark.falsification
def testAnAnswerForThePinDoesNotCoverAnImageTheOriginDoesNotName():
    """Kills: extending a pin's answer to any image once the pins agree.

    The tag can be moved by anything with daemon access; only the IDs
    the acquisition recorded on the host are the pin's.
    """
    with pytest.raises(imageTrust.ImageTrustRequiredError):
        imageTrust.fdictResolveLaunchPosture(
            fdictObtainedProject(fdictPinAnswer()), fdictImage(S_STRANGER_ID),
            S_NEW_NAME, fdictOrigin(),
        )


@pytest.mark.falsification
def testAnAnswerForAnotherPinCoversNothing():
    """Kills: matching an answer to the origin without comparing the pins."""
    with pytest.raises(imageTrust.ImageTrustRequiredError):
        imageTrust.fdictResolveLaunchPosture(
            fdictObtainedProject(fdictPinAnswer(sPin=S_OTHER_PIN)),
            fdictImage(S_STACKED_ID), S_NEW_NAME, fdictOrigin(),
        )


def testAnAnswerForThePinCoversNothingWithoutAnOriginRecord():
    with pytest.raises(imageTrust.ImageTrustRequiredError):
        imageTrust.fdictResolveLaunchPosture(
            fdictObtainedProject(fdictPinAnswer()), fdictImage(S_BASE_ID),
            S_NEW_NAME, None,
        )


@pytest.mark.falsification
def testAModalAnswerForAnObtainedImageAlsoNamesItsPin():
    """Kills: the modal's answer staying tied to the stacked digest alone."""
    dictPrompt = {
        "sImageDigest": S_STACKED_ID, "sPinnedImageReference": S_PIN,
        "bDigestMatchesRecordedDeposit": True,
    }
    dictRecord = imageTrust.fdictBuildTrustRecordForPrompt(
        dictPrompt, "restricted", False)
    assert dictRecord["sPinnedImageReference"] == S_PIN
    assert dictRecord["sImageDigest"] == S_STACKED_ID
    dictPrompt["bDigestMatchesRecordedDeposit"] = False
    assert imageTrust.fdictBuildTrustRecordForPrompt(
        dictPrompt, "restricted", False)["sPinnedImageReference"] == ""


@pytest.mark.falsification
def testTheLaunchGuardReadsTheHostHeldOriginRecord(
    fixtureIsolateRegistryAndHome, monkeypatch,
):
    """Kills: the real guard never handing the origin record to the rule.

    The record the acquisition wrote under ~ is what binds a pin to IDs.
    """
    sDirectory = os.path.join(fixtureIsolateRegistryAndHome, "projects", "x")
    os.makedirs(sDirectory)
    with open(os.path.join(sDirectory, "vaibify.yml"), "w") as fileConfig:
        fileConfig.write(f"projectName: {S_NEW_NAME}\n")
    registryManager.fnAddProject(sDirectory, sMode="container")
    registryManager.fnRecordImageTrust(S_NEW_NAME, fdictPinAnswer())
    imageOrigins.fnWriteOriginRecord(S_NEW_NAME, fdictOrigin())
    monkeypatch.setattr(
        containerManager, "fdictInspectImageTag",
        lambda sReference: fdictImage(S_STACKED_ID),
    )
    assert containerManager.fdictResolveLaunchPosture(
        S_NEW_NAME)["sMode"] == "as-built"


# -- the conversion ----------------------------------------------------

@pytest.fixture
def fixturePinnedClone(monkeypatch):
    """The clone's envelope pins an obtainable image."""
    monkeypatch.setattr(
        reproductionSource, "fdictDescribePinnedEnvironment",
        lambda sDirectory: {
            "bObtainable": True, "sRefusal": "",
            "sPinnedImageReference": S_PIN,
            "sRequiredPlatform": "linux/amd64",
        },
    )


def fdictConvertBody(**dictExtra):
    return dict({"sProjectName": S_NEW_NAME,
                 "sEnvironmentSource": "archive"}, **dictExtra)


@pytest.mark.falsification
def testTheWizardsAnswerIsRecordedForThePin(
    tupleHostClient, fixturePinnedClone,
):
    """The answer rides the pending record; the registry is not touched yet.

    Kills: the conversion dropping the answer the wizard sent.
    """
    from vaibify.config import pendingConversions
    clientHub, _app, _sDirectory = tupleHostClient
    responseHttp = clientHub.post(fsConvertUrl(S_HOST_NAME), json=fdictConvertBody(
        sImageTrustChoice="restricted", bImageTrustWithCredentials=True,
    ))
    assert responseHttp.status_code == 200, responseHttp.text
    assert responseHttp.json()["bConversionPending"] is True
    assert registryManager.fbIsHostProject(S_HOST_NAME)
    assert registryManager.fdictGetProject(S_NEW_NAME) is None
    dictRecord = pendingConversions.fdictReadConversionRecord(S_HOST_NAME)["dictImageTrust"]
    assert dictRecord["sPinnedImageReference"] == S_PIN
    assert dictRecord["sImageDigest"] == ""
    assert dictRecord["sChoice"] == "restricted"
    assert dictRecord["bWithCredentials"] is True


def testAConversionWithNoAnswerLeavesTheQuestionToTheStart(
    tupleHostClient, fixturePinnedClone,
):
    from vaibify.config import pendingConversions
    clientHub, _app, _sDirectory = tupleHostClient
    responseHttp = clientHub.post(
        fsConvertUrl(S_HOST_NAME), json=fdictConvertBody())
    assert responseHttp.status_code == 200, responseHttp.text
    assert pendingConversions.fdictReadConversionRecord(S_HOST_NAME)["dictImageTrust"] is None


def testInspectOnlyIsRefusedBeforeAnythingIsConverted(
    tupleHostClient, fixturePinnedClone,
):
    """A conversion exists to make a container; inspect only makes none."""
    clientHub, _app, _sDirectory = tupleHostClient
    responseHttp = clientHub.post(fsConvertUrl(S_HOST_NAME), json=fdictConvertBody(
        sImageTrustChoice="inspect",
    ))
    assert responseHttp.status_code == 400
    assert "Inspect only creates no container" in (
        responseHttp.json()["detail"]["sMessage"])
    assert registryManager.fbIsHostProject(S_HOST_NAME)
    assert registryManager.fdictGetProject(S_NEW_NAME) is None


def testABuiltConversionRecordsNoAnswer(tupleHostClient):
    """A built image never asks, so an answer sent with it is not kept."""
    clientHub, _app, _sDirectory = tupleHostClient
    responseHttp = clientHub.post(fsConvertUrl(S_HOST_NAME), json={
        "sProjectName": S_NEW_NAME, "sImageTrustChoice": "as-built",
    })
    assert responseHttp.status_code == 200, responseHttp.text
    assert "dictImageTrust" not in registryManager.fdictGetProject(S_NEW_NAME)
