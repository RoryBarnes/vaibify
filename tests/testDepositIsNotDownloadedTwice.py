"""A deposit this daemon already loaded is not downloaded again.

A reader's retry after a later step failed (an agent overlay, a full
disk) used to fetch the whole archived image again, most of a gigabyte,
although the daemon still held the image the first download produced.
These tests run the real chain twice over the same fake daemon and the
same served deposit: the second run must serve the archive link from
the host's note without a request for the tarball, and must download
again the moment the daemon no longer holds the image.
"""

import os
import stat

import pytest

from tests.testCoverageReproducibilityImageAcquisition import (  # noqa: F401
    BA_TARBALL,
    S_LOADED_ID,
    S_LOCAL_ONLY_ID,
    FakeImageStore,
    fdictEnvironment,
    fdictRecord,
    fnServeViaDoiResolver,
    transportDouble,
)
from vaibify.reproducibility import imageAcquisition
from vaibify.reproducibility.imageAcquisition import (
    ImageAcquisitionRefusedError,
    S_OBTAINED_ARCHIVE,
    fdictAcquirePinnedImage,
)


def fdictAcquireOnce(storeImages, listEvents):
    return fdictAcquirePinnedImage(
        fdictEnvironment(sPinned=S_LOCAL_ONLY_ID, dictRecord=fdictRecord()),
        "linux/amd64", fnStatusCallback=listEvents.append,
        dockerDisposable=storeImages,
    )


def flistTarballRequests(transportDouble):
    """The requests the loopback file server saw for the tarball."""
    return transportDouble.serverFiles.listFileRequests


@pytest.mark.falsification
def testASecondAcquisitionOfAHeldDepositDownloadsNothing(transportDouble):
    """Kills: the archive link downloading a deposit the daemon still holds."""
    fnServeViaDoiResolver(transportDouble, BA_TARBALL)
    storeImages = FakeImageStore()
    fdictAcquireOnce(storeImages, [])
    assert len(flistTarballRequests(transportDouble)) == 1
    listEvents = []
    dictAnswer = fdictAcquireOnce(storeImages, listEvents)
    assert len(flistTarballRequests(transportDouble)) == 1, (
        "the deposit was downloaded again though the daemon held its image"
    )
    assert len(storeImages.listLoaded) == 1
    assert dictAnswer["sObtainedFrom"] == S_OBTAINED_ARCHIVE
    assert dictAnswer["sImageReference"] == S_LOADED_ID
    listReused = [d for d in listEvents if d["sPhase"] == "reused"]
    assert listReused and listReused[0]["sLoadedImageId"] == S_LOADED_ID


@pytest.mark.falsification
def testADaemonThatNoLongerHoldsTheImageDownloadsAgain(transportDouble):
    """Kills: trusting the host's note without asking the daemon."""
    fnServeViaDoiResolver(transportDouble, BA_TARBALL)
    storeImages = FakeImageStore()
    fdictAcquireOnce(storeImages, [])
    del storeImages.dictHeld[S_LOADED_ID]
    dictAnswer = fdictAcquireOnce(storeImages, [])
    assert len(flistTarballRequests(transportDouble)) == 2
    assert len(storeImages.listLoaded) == 2
    assert dictAnswer["sObtainedFrom"] == S_OBTAINED_ARCHIVE


def testADownloadThatFailsItsHashLeavesNoNote(transportDouble):
    """Only bytes that passed the hash check and loaded are noted."""
    fnServeViaDoiResolver(
        transportDouble, BA_TARBALL[:-1] + bytes([BA_TARBALL[-1] ^ 0xFF]),
    )
    storeImages = FakeImageStore()
    with pytest.raises(ImageAcquisitionRefusedError, match="did not match"):
        fdictAcquireOnce(storeImages, [])
    assert storeImages.listLoaded == []
    assert not os.listdir(imageAcquisition._S_DEPOSIT_LOADS_DIRECTORY)


def testTheNoteIsPrivateToTheResearcher(transportDouble):
    fnServeViaDoiResolver(transportDouble, BA_TARBALL)
    fdictAcquireOnce(FakeImageStore(), [])
    listNotes = os.listdir(imageAcquisition._S_DEPOSIT_LOADS_DIRECTORY)
    assert len(listNotes) == 1
    sPath = os.path.join(imageAcquisition._S_DEPOSIT_LOADS_DIRECTORY, listNotes[0])
    assert stat.S_IMODE(os.stat(sPath).st_mode) == 0o600
