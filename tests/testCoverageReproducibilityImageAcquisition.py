"""The image acquisition chain's refusals, one link at a time.

Two boundaries are replaced. The Docker SDK client is a small fake
store answering ``images.pull``/``load``/``get`` and ``version`` in the
SDK's own shapes, handed to the chain as ``dockerDisposable``, so the
real ``disposableContainer`` functions run over it. The record API and
the DOI resolver are a recording transport installed as
``zenodoClient.requests``; the FILE is served by a real loopback server
(``depositFaultServer``), because the resumable download owns its own
sockets and a fake response has none.

The pinned reference and the loaded image ID are deliberately distinct
strings, so a chain that ran the wrong one could not pass.
"""

import gzip
import hashlib
import types

import pytest
import requests

from tests.depositFaultServer import FaultInjectingDepositServer
from vaibify.docker import disposableContainer
from vaibify.reproducibility import (
    downloadPartial, imageAcquisition, imageArchive, imageDeposit,
    resumableDownload,
)
from vaibify.reproducibility import zenodoClient
from vaibify.reproducibility.imageAcquisition import (
    ImageAcquisitionRefusedError,
    S_OBTAINED_LOCAL,
    S_OBTAINED_REGISTRY,
    fdictAcquirePinnedImage,
)


S_PINNED_REFERENCE = "registry.example/projectAlpha@sha256:" + "a" * 64
S_LOCAL_ONLY_ID = "sha256:" + "c" * 64
S_LOADED_ID = "sha256:" + "e" * 64
S_DOI = "10.5281/zenodo.5150"
S_TARBALL_NAME = "environment-image.tar.gz"
BA_TARBALL = gzip.compress(
    b"image stream for the acquisition fixture", mtime=0,
)


# ── a Docker SDK stand-in ────────────────────────────────────────


class _FakeImage:
    def __init__(self, sId, sArchitecture):
        self.id = sId
        self.attrs = {"Os": "linux", "Architecture": sArchitecture}


class _FakeImages:
    def __init__(self, storeImages):
        self._storeImages = storeImages

    def pull(self, sReference, platform=None, **kwargs):
        self._storeImages.listPulls.append((sReference, platform))
        raise LookupError("manifest unknown")

    def load(self, fileStream):
        self._storeImages.listLoaded.append(b"".join(fileStream))
        if self._storeImages.errorOnLoad is not None:
            raise self._storeImages.errorOnLoad
        imageLoaded = _FakeImage(S_LOADED_ID, "amd64")
        self._storeImages.dictHeld[S_LOADED_ID] = imageLoaded
        return [imageLoaded]

    def get(self, sReference):
        if sReference not in self._storeImages.dictHeld:
            raise LookupError(sReference)
        return self._storeImages.dictHeld[sReference]


class FakeImageStore:
    """The daemon, as the acquisition chain asks it questions."""

    def __init__(self, sDaemonArchitecture="amd64"):
        self.dictHeld = {}
        self.listPulls = []
        self.listLoaded = []
        self.errorOnLoad = None
        self.sDaemonArchitecture = sDaemonArchitecture
        self.images = _FakeImages(self)

    def version(self):
        return {"Arch": self.sDaemonArchitecture}


# ── a Zenodo / DOI transport stand-in ────────────────────────────


class _FakeResponse:
    def __init__(self, iStatus=200, jsonBody=None, baContent=b"",
                 dictHeaders=None, sUrl=""):
        self.status_code = iStatus
        self.headers = dict(dictHeaders or {})
        self._jsonBody = jsonBody
        self._baContent = baContent
        self.url = sUrl
        self.text = ""

    def json(self):
        return self._jsonBody

    def iter_content(self, iChunkBytes):
        for iStart in range(0, len(self._baContent), iChunkBytes):
            yield self._baContent[iStart:iStart + iChunkBytes]

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class TransportDouble:
    """Answer GETs by URL; an Exception value is raised; record URLs."""

    def __init__(self):
        self.dictAnswers = {}
        self.listUrls = []

    def fresponseGet(self, sUrl, **kwargs):
        self.listUrls.append(sUrl)
        responseAnswer = self.dictAnswers.get(sUrl)
        if isinstance(responseAnswer, Exception):
            raise responseAnswer
        if responseAnswer is None:
            return _FakeResponse(404, {"message": "not found"}, sUrl=sUrl)
        responseAnswer.url = sUrl
        return responseAnswer


@pytest.fixture
def transportDouble(monkeypatch, tmp_path):
    """Install the record transport and a loopback file server.

    The Zenodo service table is pointed at the file server, so the
    chain's allowlist admits it and the record page the DOI resolves
    to is one of its URLs.
    """
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    doubleTransport = TransportDouble()
    monkeypatch.setattr(zenodoClient, "requests", types.SimpleNamespace(
        get=doubleTransport.fresponseGet,
        RequestException=requests.RequestException,
    ))
    with FaultInjectingDepositServer(BA_TARBALL) as serverFiles:
        monkeypatch.setattr(zenodoClient, "_SERVICES", {
            "zenodo": serverFiles.sBase, "sandbox": serverFiles.sBase,
        })
        doubleTransport.serverFiles = serverFiles
        doubleTransport.sBase = serverFiles.sBase
        doubleTransport.sRecordPageUrl = serverFiles.sBase + "/records/5150"
        yield doubleTransport


@pytest.fixture
def fnShortenRetryWaits(monkeypatch):
    """Make the download's backoff waits milliseconds, not minutes."""
    monkeypatch.setattr(resumableDownload, "_F_BACKOFF_BASE_SECONDS", 0.001)
    monkeypatch.setattr(resumableDownload, "_F_BACKOFF_CAP_SECONDS", 0.002)


def fdictOpenAcquisitionFor(dictRecord):
    """Open the acquisition directory a download of ``dictRecord`` uses."""
    return downloadPartial.fdictOpenAcquisition(dictRecord, {"sKind": "test"})


def fdictRecord(**dictOverrides):
    """Return a deposit record for the fixture tarball."""
    dictRecord = {
        "sVersionDoi": S_DOI,
        "sConceptDoi": "10.5281/zenodo.5149",
        "sTarballName": S_TARBALL_NAME,
        "sTarballSha256": "sha256:" + hashlib.sha256(BA_TARBALL).hexdigest(),
        "iTarballBytes": len(BA_TARBALL),
        "sZenodoService": "zenodo",
    }
    dictRecord.update(dictOverrides)
    return dictRecord


def fdictEnvironment(sPinned=S_PINNED_REFERENCE, dictRecord=None):
    """Return an envelope pinning ``sPinned``, optionally with a deposit."""
    dictContainer = {"sImageDigest": sPinned, "sArchitecture": "amd64"}
    if dictRecord is not None:
        dictContainer[imageArchive.S_IMAGE_ARCHIVE_KEY] = dictRecord
    return {"dictContainer": dictContainer}


def fnServeViaDoiResolver(transportDouble, baServed):
    """Make the record API 404 so the DOI is followed by hand to the file."""
    transportDouble.dictAnswers["https://doi.org/" + S_DOI] = _FakeResponse(
        302, dictHeaders={"Location": transportDouble.sRecordPageUrl},
    )
    transportDouble.dictAnswers[transportDouble.sRecordPageUrl] = _FakeResponse(200)
    transportDouble.serverFiles.fnServeBytes(baServed)


# ── platform helpers ─────────────────────────────────────────────


@pytest.mark.parametrize("sPlatform, sExpected", [
    ("linux/amd64", "amd64"), (" ARM64 ", "arm64"), ("", ""), (None, ""),
])
def testArchitectureIsTheHalfAfterTheSlash(sPlatform, sExpected):
    """A bare architecture passes through normalized; a platform is split."""
    assert imageAcquisition.fsArchitectureOfPlatform(sPlatform) == sExpected


# ── the chain ────────────────────────────────────────────────────


def testWithNoClientGivenTheChainCreatesItsOwn(monkeypatch):
    """The disposable client is created only when none is handed in."""
    storeImages = FakeImageStore()
    storeImages.dictHeld[S_PINNED_REFERENCE] = _FakeImage("sha256:" + "2" * 64,
                                                          "amd64")
    monkeypatch.setattr(
        disposableContainer, "fdockerCreateDisposableClient",
        lambda: storeImages,
    )
    dictAnswer = fdictAcquirePinnedImage(fdictEnvironment(), "amd64")
    assert dictAnswer["sObtainedFrom"] == S_OBTAINED_LOCAL
    assert storeImages.listPulls == [(S_PINNED_REFERENCE, "linux/amd64")]


def testAnEnvelopeWithoutAPinIsRefusedBeforeTheDaemon():
    """No content-pinned reference means nothing is asked of any link."""
    storeImages = FakeImageStore()
    with pytest.raises(
        ImageAcquisitionRefusedError, match="pins no container",
    ):
        fdictAcquirePinnedImage(
            {"dictContainer": {}}, "amd64", dockerDisposable=storeImages,
        )
    assert storeImages.listPulls == []


def testALocalOnlyImageIdIsNeverPulled():
    """A bare image ID skips the registry; the local copy then serves."""
    storeImages = FakeImageStore()
    storeImages.dictHeld[S_LOCAL_ONLY_ID] = _FakeImage(
        S_LOCAL_ONLY_ID, "amd64",
    )
    listEvents = []
    dictAnswer = fdictAcquirePinnedImage(
        fdictEnvironment(sPinned=S_LOCAL_ONLY_ID), "linux/amd64",
        fnStatusCallback=listEvents.append, dockerDisposable=storeImages,
    )
    assert storeImages.listPulls == []
    assert dictAnswer["sObtainedFrom"] == S_OBTAINED_LOCAL
    assert dictAnswer["sImageReference"] == S_LOCAL_ONLY_ID
    assert dictAnswer["listAttempts"][0]["bSucceeded"] is False
    assert "local-only image ID" in dictAnswer["listAttempts"][0]["sDetail"]
    assert listEvents[-1]["sPhase"] == "acquired"


def testAnImageTheDaemonCannotInspectAfterwardsIsRefused(monkeypatch):
    """An obtained image with no readable platform runs nothing."""
    storeImages = FakeImageStore()
    storeImages.dictHeld[S_PINNED_REFERENCE] = _FakeImage("sha256:1", "")
    with pytest.raises(
        ImageAcquisitionRefusedError, match="could not be inspected after",
    ):
        fdictAcquirePinnedImage(
            fdictEnvironment(), "amd64", dockerDisposable=storeImages,
        )


def testADaemonThatHidesItsArchitectureIsRefused():
    """Native versus emulated cannot be known, so nothing is run."""
    storeImages = FakeImageStore(sDaemonArchitecture="")
    storeImages.dictHeld[S_PINNED_REFERENCE] = _FakeImage("sha256:1", "amd64")
    with pytest.raises(
        ImageAcquisitionRefusedError, match="did not report its architecture",
    ):
        fdictAcquirePinnedImage(
            fdictEnvironment(), "amd64", dockerDisposable=storeImages,
        )


def testAnArchiveTheDaemonCannotLoadFallsThroughAndIsNamed(transportDouble):
    """A load failure is recorded on the archive link; the chain moves on."""
    fnServeViaDoiResolver(transportDouble, BA_TARBALL)
    storeImages = FakeImageStore()
    storeImages.errorOnLoad = RuntimeError("daemon refused the tarball")
    with pytest.raises(ImageAcquisitionRefusedError) as infoError:
        fdictAcquirePinnedImage(
            fdictEnvironment(dictRecord=fdictRecord()), "amd64",
            dockerDisposable=storeImages,
        )
    sMessage = str(infoError.value)
    assert "archived deposit: failed (the daemon could not load" in sMessage
    assert "RuntimeError: daemon refused the tarball" in sMessage
    assert "copy on this daemon: failed" in sMessage
    assert storeImages.listLoaded == [
        b"image stream for the acquisition fixture",
    ]


def testAnArchiveTheDaemonLoadsIsRunByItsLoadedId(transportDouble):
    """The same chain with a loadable tarball runs the loaded ID."""
    fnServeViaDoiResolver(transportDouble, BA_TARBALL)
    storeImages = FakeImageStore()
    dictAnswer = fdictAcquirePinnedImage(
        fdictEnvironment(dictRecord=fdictRecord()), "amd64",
        dockerDisposable=storeImages,
    )
    assert dictAnswer["sImageReference"] == S_LOADED_ID
    assert dictAnswer["sPinnedImageReference"] == S_PINNED_REFERENCE
    assert dictAnswer["sObtainedFrom"] == imageAcquisition.S_OBTAINED_ARCHIVE
    assert dictAnswer["sObtainedFrom"] != S_OBTAINED_REGISTRY


@pytest.mark.falsification
def testAFailedArchiveFollowedByALocalCopyReadsAsLocalNeverArchive(
    transportDouble,
):
    """The outcome names the link that served, not the one that was tried.

    Kills: reporting the archive as the source whichever link served.
    """
    fnServeViaDoiResolver(transportDouble, BA_TARBALL)
    storeImages = FakeImageStore()
    storeImages.errorOnLoad = RuntimeError("daemon refused the tarball")
    storeImages.dictHeld[S_PINNED_REFERENCE] = _FakeImage(
        S_LOCAL_ONLY_ID, "amd64",
    )
    dictAnswer = fdictAcquirePinnedImage(
        fdictEnvironment(dictRecord=fdictRecord()), "amd64",
        dockerDisposable=storeImages,
    )
    assert dictAnswer["sObtainedFrom"] == imageAcquisition.S_OBTAINED_LOCAL
    assert dictAnswer["sObtainedFrom"] != imageAcquisition.S_OBTAINED_ARCHIVE
    assert [
        (dictAttempt["sLink"], dictAttempt["bSucceeded"])
        for dictAttempt in dictAnswer["listAttempts"]
    ] == [
        (imageAcquisition.S_LINK_REGISTRY, False),
        (imageAcquisition.S_LINK_ARCHIVE, False),
        (imageAcquisition.S_LINK_LOCAL, True),
    ]


# ── downloading the deposit ──────────────────────────────────────


def testANonZenodoDoiIsRefusedBeforeAnyFetch(transportDouble):
    """A DOI with no Zenodo record id is never followed."""
    dictRecord = fdictRecord(sVersionDoi="10.1234/journal.5150")
    with pytest.raises(ImageAcquisitionRefusedError, match="not a Zenodo DOI"):
        imageAcquisition.fsDownloadVerifiedTarball(
            dictRecord, fdictOpenAcquisitionFor(dictRecord),
            lambda dictEvent: None,
        )
    assert transportDouble.listUrls == []
    assert transportDouble.serverFiles.listFileRequests == []


def testTheDoiFollowFallbackDownloadsAndVerifiesTheTarball(transportDouble):
    """When the record API cannot answer, the DOI is followed by hand."""
    fnServeViaDoiResolver(transportDouble, BA_TARBALL)
    listEvents = []
    sPath = imageAcquisition.fsDownloadVerifiedTarball(
        fdictRecord(), fdictOpenAcquisitionFor(fdictRecord()), listEvents.append,
    )
    assert open(sPath, "rb").read() == BA_TARBALL
    assert transportDouble.listUrls[0] == (
        transportDouble.sBase + "/api/records/5150"
    )
    assert "https://doi.org/" + S_DOI in transportDouble.listUrls
    assert listEvents[-1]["sPhase"] == "downloading"
    assert listEvents[-1]["iBytes"] == len(BA_TARBALL)
    assert listEvents[-1]["iTotalBytes"] == len(BA_TARBALL)


def testTheDoiFollowFallbackStillRefusesAWrongHash(transportDouble):
    """The same path serving one different byte is emptied, not loaded.

    One automatic restart from zero is allowed (a resumed file can carry
    a torn byte); a second mismatch refuses and leaves no byte behind.
    """
    fnServeViaDoiResolver(
        transportDouble, BA_TARBALL[:-1] + bytes([BA_TARBALL[-1] ^ 0xFF]),
    )
    dictAcquisition = fdictOpenAcquisitionFor(fdictRecord())
    with pytest.raises(ImageAcquisitionRefusedError, match="did not match") as infoError:
        imageAcquisition.fsDownloadVerifiedTarball(
            fdictRecord(), dictAcquisition, lambda dictEvent: None,
        )
    assert downloadPartial.fiBytesOnDisk(dictAcquisition) == 0
    assert infoError.value.dictFailure["bPartialKept"] is False
    assert len(transportDouble.serverFiles.listFileRequests) == 2


def testAnUnresolvableDoiIsRefusedByName(transportDouble):
    """A network failure at the resolver names the DOI it could not follow."""
    transportDouble.dictAnswers["https://doi.org/" + S_DOI] = (
        requests.ConnectionError("name resolution failed")
    )
    with pytest.raises(
        ImageAcquisitionRefusedError,
        match=r"the DOI 10\.5281/zenodo\.5150 could not be resolved",
    ):
        imageAcquisition.fsDownloadVerifiedTarball(
            fdictRecord(), fdictOpenAcquisitionFor(fdictRecord()),
            lambda dictEvent: None,
        )


@pytest.mark.falsification
def testADownloadThatKeepsDroppingSpendsTheBudgetAndKeepsThePartial(
    transportDouble, fnShortenRetryWaits,
):
    """A connection that drops before the first byte, every time, stops after six tries.

    Kills: letting the downloader's refusal escape untranslated, so the
    chain never hears it as its own refusal.
    """
    fnServeViaDoiResolver(transportDouble, BA_TARBALL)
    transportDouble.serverFiles.dictBehavior["listDropAfterBytes"] = [0] * 12
    dictAcquisition = fdictOpenAcquisitionFor(fdictRecord())
    with pytest.raises(
        ImageAcquisitionRefusedError, match="stopped after 6 attempts",
    ) as infoError:
        imageAcquisition.fsDownloadVerifiedTarball(
            fdictRecord(), dictAcquisition, lambda dictEvent: None,
        )
    assert infoError.value.dictFailure["bPartialKept"] is True
    assert infoError.value.dictFailure["sFailureClass"] == (
        resumableDownload.S_FAILURE_BUDGET_EXHAUSTED
    )
    assert len(transportDouble.serverFiles.listFileRequests) == 6


def testAServerOfferingADifferentFileIsRefusedBeforeAByteIsKept(transportDouble):
    """A whole-file answer whose length is not the recorded one is not the deposit."""
    fnServeViaDoiResolver(transportDouble, BA_TARBALL * 2)
    dictAcquisition = fdictOpenAcquisitionFor(fdictRecord())
    with pytest.raises(
        ImageAcquisitionRefusedError, match="not the one this project recorded",
    ) as infoError:
        imageAcquisition.fsDownloadVerifiedTarball(
            fdictRecord(), dictAcquisition, lambda dictEvent: None,
        )
    assert downloadPartial.fiBytesOnDisk(dictAcquisition) == 0
    assert infoError.value.dictFailure["bPartialKept"] is False


def testAFileUrlCannotComeFromANonZenodoDoi():
    """The record-API lookup answers empty for a DOI with no record id."""
    assert imageAcquisition._fsFileUrlFromPublishedRecord(
        {}, "10.1234/journal.5150", S_TARBALL_NAME,
    ) == ""


# ── opening a tarball by its codec ───────────────────────────────


def testAZstdTarballWithoutACodecIsRefused(monkeypatch, tmp_path):
    """No zstd on this host is a named refusal, never a raw load."""
    pathTarball = tmp_path / "environment-image.tar.zst"
    pathTarball.write_bytes(b"zstd bytes")
    monkeypatch.setattr(imageDeposit, "_ftResolveZstdCodec", lambda: None)
    with pytest.raises(ImageAcquisitionRefusedError, match="cannot decompress zstd"):
        imageAcquisition._ffileOpenByCodec(str(pathTarball))


def testAZstdTarballIsReadThroughTheCodecsReader(monkeypatch, tmp_path):
    """The reader the deposit side chose is the one that opens it."""
    pathTarball = tmp_path / "environment-image.tar.zst"
    pathTarball.write_bytes(b"zstd bytes")
    listWrapped = []

    def ffileReader(fileRaw):
        listWrapped.append(fileRaw.name)
        return fileRaw

    monkeypatch.setattr(
        imageDeposit, "_ftResolveZstdCodec",
        lambda: (".tar.zst", None, ffileReader),
    )
    with imageAcquisition._ffileOpenByCodec(str(pathTarball)) as fileStream:
        assert fileStream.read() == b"zstd bytes"
    assert listWrapped == [str(pathTarball)]


def testAnUncompressedTarballIsOpenedAsIs(tmp_path):
    """A plain ``.tar`` is read byte for byte."""
    pathTarball = tmp_path / "environment-image.tar"
    pathTarball.write_bytes(b"plain tar bytes")
    with imageAcquisition._ffileOpenByCodec(str(pathTarball)) as fileStream:
        assert fileStream.read() == b"plain tar bytes"
