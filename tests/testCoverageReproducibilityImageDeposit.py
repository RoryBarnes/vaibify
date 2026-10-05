"""The environment-archive deposit and its promotion, driven end to end.

Two external boundaries are replaced, and nothing else: the ``docker``
CLI (``subprocess.Popen`` for ``docker save``, ``subprocess.run`` for
``docker image inspect``) and Zenodo's HTTP API (the ``requests``
functions ``zenodoClient`` calls). Every other call -- the real
``ZenodoClient``, the real compression, hashing, sidecar bookkeeping,
envelope writes and the real hash comparison on a download -- runs as
production runs it, so a test asserting a hash, a DOI or a refusal is
asserting what the code computed, not what a stub was told to say.

Any command that is not ``docker`` is delegated to the real
``subprocess`` so the git a manifest-ownership check runs still works.
"""

import gzip
import hashlib
import io
import json
import logging
import os
import subprocess
import sys
import types
from collections import namedtuple

import pytest
import requests

from vaibify.config import mutationAdmission
from vaibify.reproducibility import (
    archivePromotion,
    imageAcquisition,
    imageArchive,
    imageDeposit,
    syncBookkeeping,
    zenodoClient,
)


S_IMAGE_REFERENCE = "registry.example/projectAlpha@sha256:" + "a" * 64
S_OTHER_IMAGE = "registry.example/projectAlpha@sha256:" + "b" * 64
S_ARCHITECTURE = "amd64"
S_SIDECAR_KEY = "workflowAlpha"
S_PRODUCTION_TOKEN = "tokenProductionFixture"
I_DRAFT_ID = 9001
BA_IMAGE_STREAM = b"layer-bytes-of-a-fixture-image-" * 4000

_REAL_POPEN = subprocess.Popen
_REAL_RUN = subprocess.run


# ── docker double ────────────────────────────────────────────────


class _FakeSaveProcess:
    """A ``docker save`` whose stdout is a fixed byte stream."""

    def __init__(self, baStdout, iExitCode, baStderr):
        self.stdout = io.BytesIO(baStdout)
        self.stderr = io.BytesIO(baStderr)
        self._iExitCode = iExitCode
        self.returncode = None

    def wait(self):
        self.returncode = self._iExitCode
        return self._iExitCode


class DockerDouble:
    """Answer ``docker save`` and ``docker image inspect``; record argv."""

    def __init__(self):
        self.baSaveStream = BA_IMAGE_STREAM
        self.iSaveExitCode = 0
        self.baSaveStderr = b""
        self.sImageSize = str(len(BA_IMAGE_STREAM))
        self.iInspectExitCode = 0
        self.listCommands = []

    def fnInstall(self, monkeypatch):
        """Patch the docker boundary; delegate every other command."""
        def fnPopen(saCommand, *args, **kwargs):
            if saCommand[0] != "docker":
                return _REAL_POPEN(saCommand, *args, **kwargs)
            self.listCommands.append(list(saCommand))
            return _FakeSaveProcess(
                self.baSaveStream, self.iSaveExitCode, self.baSaveStderr,
            )

        def fnRun(saCommand, *args, **kwargs):
            if saCommand[0] != "docker":
                return _REAL_RUN(saCommand, *args, **kwargs)
            self.listCommands.append(list(saCommand))
            sStdout = self.sImageSize if "{{.Size}}" in saCommand else "x"
            return subprocess.CompletedProcess(
                saCommand, self.iInspectExitCode, sStdout, "",
            )

        monkeypatch.setattr(subprocess, "Popen", fnPopen)
        monkeypatch.setattr(subprocess, "run", fnRun)
        fnRealWhich = imageDeposit.shutil.which
        monkeypatch.setattr(
            imageDeposit.shutil, "which",
            lambda sName, *args, **kwargs: (
                "/usr/bin/docker" if sName == "docker"
                else fnRealWhich(sName, *args, **kwargs)
            ),
        )


@pytest.fixture
def dockerDouble(monkeypatch, tmp_path):
    """A docker double, gzip forced, scratch under a private HOME."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(imageDeposit, "_ftResolveZstdCodec", lambda: None)
    monkeypatch.setattr(imageDeposit, "I_PROGRESS_CHUNK_BYTES", 16384)
    doubleDocker = DockerDouble()
    doubleDocker.fnInstall(monkeypatch)
    return doubleDocker


# ── Zenodo HTTP double ───────────────────────────────────────────


class _FakeResponse:
    """The slice of ``requests.Response`` the Zenodo callers read."""

    def __init__(self, iStatus, jsonBody=None, baContent=b"", sUrl=""):
        self.status_code = iStatus
        self.headers = {}
        self._jsonBody = jsonBody
        self._baContent = baContent
        self.url = sUrl
        self.text = json.dumps(jsonBody) if jsonBody is not None else ""

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


class ZenodoDouble:
    """A Zenodo that records every request and answers from its own state.

    The deposit's reported md5 and size are computed from the bytes the
    PUT actually carried, so the post-upload verification compares the
    real record against what really arrived.
    """

    def __init__(self):
        self.listRequests = []
        self.dictUploaded = {}
        self.dictPublishedRecords = {}
        self.dictServedFiles = {}
        self.fnOnUpload = None
        self.iSearchStatus = 200

    def fnInstall(self, monkeypatch):
        monkeypatch.setattr(zenodoClient, "requests", types.SimpleNamespace(
            request=self.fresponseRequest, get=self.fresponseGet,
            put=self.fresponsePut,
            RequestException=requests.RequestException,
        ))

    def fresponseRequest(self, sMethod, sUrl, **kwargs):
        self.listRequests.append((sMethod, sUrl, kwargs))
        if sMethod == "DELETE":
            return _FakeResponse(204)
        if sUrl.endswith("/actions/publish"):
            return _FakeResponse(202, {
                "doi": f"10.5281/zenodo.{I_DRAFT_ID}",
                "conceptdoi": f"10.5281/zenodo.{I_DRAFT_ID - 1}",
            })
        return _FakeResponse(201, {
            "id": I_DRAFT_ID,
            "links": {"bucket": "https://zenodo.org/api/files/bucketAlpha"},
        })

    def fresponseGet(self, sUrl, **kwargs):
        self.listRequests.append(("GET", sUrl, kwargs))
        if sUrl in self.dictServedFiles:
            return _FakeResponse(200, baContent=self.dictServedFiles[sUrl])
        if sUrl.endswith("/deposit/depositions"):
            return _FakeResponse(self.iSearchStatus, [])
        if sUrl.endswith(f"/deposit/depositions/{I_DRAFT_ID}"):
            return _FakeResponse(200, {"id": I_DRAFT_ID, "files": [
                {"filename": sName, "filesize": len(baBytes),
                 "checksum": hashlib.md5(baBytes).hexdigest()}
                for sName, baBytes in self.dictUploaded.items()
            ]})
        if sUrl in self.dictPublishedRecords:
            return _FakeResponse(200, self.dictPublishedRecords[sUrl])
        return _FakeResponse(404, {"message": "not found"})

    def fresponsePut(self, sUrl, headers=None, data=None, timeout=None):
        self.listRequests.append(("PUT", sUrl, {"headers": headers}))
        if self.fnOnUpload is not None:
            self.fnOnUpload()
        self.dictUploaded[sUrl.rsplit("/", 1)[-1]] = data.read()
        return _FakeResponse(201, {})

    def flistMethodsAndUrls(self):
        return [(sMethod, sUrl) for sMethod, sUrl, _ in self.listRequests]


@pytest.fixture
def zenodoDouble(monkeypatch):
    """Replace Zenodo's HTTP transport for the duration of one test."""
    doubleZenodo = ZenodoDouble()
    doubleZenodo.fnInstall(monkeypatch)
    return doubleZenodo


# ── shared builders ──────────────────────────────────────────────


def fnInitGitRepository(pathRepo):
    """Make ``pathRepo`` a real git repository with one empty commit."""
    pathRepo.mkdir(parents=True, exist_ok=True)
    for listArguments in (
        ["init", "-q"],
        ["-c", "user.email=fixture@example.invalid", "-c", "user.name=f",
         "-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty",
         "-m", "initial"],
    ):
        _REAL_RUN(["git", *listArguments], cwd=str(pathRepo), check=True,
                  capture_output=True)


def fnWriteEnvelope(pathRepo, dictContainer):
    """Write ``.vaibify/environment.json`` with one container block."""
    pathEnvelope = pathRepo / ".vaibify" / "environment.json"
    pathEnvelope.parent.mkdir(parents=True, exist_ok=True)
    pathEnvelope.write_text(
        json.dumps({"dictContainer": dictContainer}), encoding="utf-8",
    )


def fdictReadEnvelope(pathRepo):
    """Return the parsed envelope written under ``pathRepo``."""
    return json.loads(
        (pathRepo / ".vaibify" / "environment.json").read_text("utf-8"),
    )


def fsSha256Of(baBytes):
    """Return a ``sha256:``-prefixed digest, computed independently."""
    return "sha256:" + hashlib.sha256(baBytes).hexdigest()


def fsMakeScratch(pathParent):
    """Return a fresh scratch directory for one save under ``pathParent``."""
    pathScratch = pathParent / "scratch"
    pathScratch.mkdir(parents=True, exist_ok=True)
    return str(pathScratch)


# ── scratch directory and codec choice ───────────────────────────


def testDepositScratchIsPrivateAndPerDeposit(monkeypatch, tmp_path):
    """Each deposit gets its own directory under a 0700 ~/.vaibify root."""
    monkeypatch.setenv("HOME", str(tmp_path))
    sFirst = imageDeposit.fsResolveDepositScratchDirectory()
    sSecond = imageDeposit.fsResolveDepositScratchDirectory()
    pathRoot = tmp_path / ".vaibify" / "imageArchive"
    assert sFirst != sSecond
    assert os.path.dirname(sFirst) == str(pathRoot)
    assert os.path.dirname(sSecond) == str(pathRoot)
    assert (os.stat(pathRoot).st_mode & 0o777) == 0o700
    assert (os.stat(sFirst).st_mode & 0o777) == 0o700


def testStandardLibraryZstdIsPreferredWhenPresent(monkeypatch, tmp_path):
    """``compression.zstd`` wins, and the extension records the choice."""
    listOpened = []
    moduleZstd = types.ModuleType("compression.zstd")
    moduleZstd.ZstdFile = lambda fileRaw, sMode: listOpened.append(sMode)
    modulePackage = types.ModuleType("compression")
    modulePackage.zstd = moduleZstd
    monkeypatch.setitem(sys.modules, "compression", modulePackage)
    monkeypatch.setitem(sys.modules, "compression.zstd", moduleZstd)
    sPath, ffnWriter, ffnReader = imageDeposit._ftChooseCodec(
        str(tmp_path), "environment-image",
    )
    ffnWriter(None)
    ffnReader(None)
    assert sPath == str(tmp_path / "environment-image.tar.zst")
    assert listOpened == ["wb", "rb"]


def testZstandardIsTheSecondChoice(monkeypatch):
    """With no standard-library zstd, the ``zstandard`` package serves."""
    listBuilt = []
    moduleZstandard = types.ModuleType("zstandard")
    moduleZstandard.ZstdCompressor = lambda: types.SimpleNamespace(
        stream_writer=lambda fileRaw: listBuilt.append("writer"),
    )
    moduleZstandard.ZstdDecompressor = lambda: types.SimpleNamespace(
        stream_reader=lambda fileRaw: listBuilt.append("reader"),
    )
    monkeypatch.setitem(sys.modules, "compression", None)
    monkeypatch.setitem(sys.modules, "zstandard", moduleZstandard)
    sExtension, ffnWriter, ffnReader = imageDeposit._ftResolveZstdCodec()
    ffnWriter(None)
    ffnReader(None)
    assert sExtension == ".tar.zst"
    assert listBuilt == ["writer", "reader"]


def testNoZstdAnywhereFallsBackToGzip(monkeypatch, tmp_path):
    """Without either zstd, the tarball is gzip and says so in its name."""
    monkeypatch.setitem(sys.modules, "compression", None)
    monkeypatch.setitem(sys.modules, "zstandard", None)
    assert imageDeposit._ftResolveZstdCodec() is None
    sPath, _ffnWriter, _ffnReader = imageDeposit._ftChooseCodec(
        str(tmp_path), "environment-image",
    )
    assert sPath.endswith("environment-image.tar.gz")


# ── image size and the disk guard ────────────────────────────────


def testImageSizeIsTheDaemonsDeclaredSize(dockerDouble):
    """The declared size is read with ``docker image inspect``."""
    dockerDouble.sImageSize = "123456789\n"
    assert imageDeposit.fiReadImageSizeBytes(S_IMAGE_REFERENCE) == 123456789
    assert dockerDouble.listCommands[-1] == [
        "docker", "image", "inspect", "--format", "{{.Size}}",
        S_IMAGE_REFERENCE,
    ]


def testUnknownImageSizeIsZeroAndSkipsTheGuard(dockerDouble, monkeypatch):
    """An unreadable size answers 0, and the disk is then not consulted."""
    dockerDouble.iInspectExitCode = 1
    listConsulted = []
    monkeypatch.setattr(
        imageDeposit.shutil, "disk_usage",
        lambda sPath: listConsulted.append(sPath),
    )
    assert imageDeposit.fiReadImageSizeBytes(S_IMAGE_REFERENCE) == 0
    imageDeposit._fnRefuseWithoutRoomOnDisk("/nowhere", 0)
    assert listConsulted == []


def testASaveThatWouldFillTheDiskIsRefusedBeforeItStarts(
    dockerDouble, monkeypatch, tmp_path,
):
    """Too little free space raises before any ``docker save`` runs."""
    iGibibyte = 1024 ** 3
    dockerDouble.sImageSize = str(8 * iGibibyte)
    tUsage = namedtuple("tUsage", "total used free")
    monkeypatch.setattr(
        imageDeposit.shutil, "disk_usage",
        lambda sPath: tUsage(100 * iGibibyte, 97 * iGibibyte, 3 * iGibibyte),
    )
    with pytest.raises(OSError, match=r"needs about 10 GB and 3 GB is free"):
        imageDeposit.ftSaveAndCompressImage(
            S_IMAGE_REFERENCE, fsMakeScratch(tmp_path),
        )
    assert not any(
        listArgv[:2] == ["docker", "save"]
        for listArgv in dockerDouble.listCommands
    )


# ── save, compress, hash ─────────────────────────────────────────


def testSaveReturnsHashesOfWhatWasWrittenAndOfTheImage(
    dockerDouble, tmp_path,
):
    """Tarball sha256/md5/size describe the file; stream hash the image."""
    listProgress = []
    sPath, sSha256, iBytes, sStreamSha256, sMd5 = (
        imageDeposit.ftSaveAndCompressImage(
            S_IMAGE_REFERENCE, fsMakeScratch(tmp_path),
            lambda iRead, iTotal: listProgress.append((iRead, iTotal)),
        )
    )
    baTarball = open(sPath, "rb").read()
    assert sPath.endswith("environment-image.tar.gz")
    assert sSha256 == fsSha256Of(baTarball)
    assert sMd5 == hashlib.md5(baTarball).hexdigest()
    assert iBytes == len(baTarball)
    assert sStreamSha256 == fsSha256Of(BA_IMAGE_STREAM)
    assert gzip.decompress(baTarball) == BA_IMAGE_STREAM
    assert ["docker", "save", S_IMAGE_REFERENCE] in dockerDouble.listCommands
    assert len(listProgress) > 1
    assert listProgress[-1] == (len(BA_IMAGE_STREAM), len(BA_IMAGE_STREAM))
    assert [iRead for iRead, _ in listProgress] == sorted(
        iRead for iRead, _ in listProgress
    )


def testTwoSavesOfOneImageGiveOneTarballHash(dockerDouble, tmp_path):
    """The clock never enters the gzip bytes, so the hash is the image's."""
    tFirst = imageDeposit.ftSaveAndCompressImage(
        S_IMAGE_REFERENCE, fsMakeScratch(tmp_path / "first"),
    )
    tSecond = imageDeposit.ftSaveAndCompressImage(
        S_IMAGE_REFERENCE, fsMakeScratch(tmp_path / "second"),
    )
    assert tFirst[1] == tSecond[1]
    dockerDouble.baSaveStream = BA_IMAGE_STREAM + b"one more byte"
    tThird = imageDeposit.ftSaveAndCompressImage(
        S_IMAGE_REFERENCE, fsMakeScratch(tmp_path / "third"),
    )
    assert tThird[1] != tFirst[1]
    assert tThird[3] != tFirst[3]


def testAFailedSaveRaisesWithTheDaemonsReason(dockerDouble, tmp_path):
    """A non-zero ``docker save`` raises, carrying its stderr."""
    dockerDouble.iSaveExitCode = 1
    dockerDouble.baSaveStderr = b"Error response from daemon: No such image"
    with pytest.raises(subprocess.CalledProcessError) as infoError:
        imageDeposit.ftSaveAndCompressImage(
            S_IMAGE_REFERENCE, fsMakeScratch(tmp_path),
        )
    assert infoError.value.cmd == ["docker", "save", S_IMAGE_REFERENCE]
    assert "No such image" in infoError.value.stderr


def testRecomputedStreamHashIsTheUncompressedImage(dockerDouble):
    """The re-check hash depends on the image bytes alone."""
    assert imageDeposit.fsRecomputeImageStreamSha256(S_IMAGE_REFERENCE) == (
        fsSha256Of(BA_IMAGE_STREAM)
    )


def testRecomputedStreamHashOfAnAbsentImageIsEmpty(dockerDouble):
    """A failed save is UNAVAILABLE (''), never a differing hash."""
    dockerDouble.iSaveExitCode = 1
    assert imageDeposit.fsRecomputeImageStreamSha256(S_IMAGE_REFERENCE) == ""


# ── the attestation-time re-check ────────────────────────────────


def fdictEnvironmentWithStreamHash(sStreamSha256):
    """Return an envelope whose deposit records one image-stream hash."""
    return {"dictContainer": {
        "sImageDigest": S_IMAGE_REFERENCE,
        imageArchive.S_IMAGE_ARCHIVE_KEY: {
            "sVersionDoi": "10.5281/zenodo.1234",
            "sImageStreamSha256": sStreamSha256,
        },
    }}


def testRecheckMatchesWhenAFreshSaveHashesToTheDeposit(
    dockerDouble, tmp_path,
):
    """The local image re-saved to the deposited bytes is MATCHED."""
    dictVerdict = imageDeposit.fdictRecheckArchiveAgainstLocalImage(
        str(tmp_path),
        fdictEnvironmentWithStreamHash(fsSha256Of(BA_IMAGE_STREAM)),
    )
    assert dictVerdict["sVerdict"] == imageArchive.S_RECHECK_MATCHED
    assert ["docker", "save", S_IMAGE_REFERENCE] in dockerDouble.listCommands


def testRecheckDiffersWhenTheLocalImageChanged(dockerDouble, tmp_path):
    """One changed byte in the image stream is DIFFERS, not a pass."""
    dockerDouble.baSaveStream = BA_IMAGE_STREAM[:-1] + b"!"
    dictVerdict = imageDeposit.fdictRecheckArchiveAgainstLocalImage(
        str(tmp_path),
        fdictEnvironmentWithStreamHash(fsSha256Of(BA_IMAGE_STREAM)),
    )
    assert dictVerdict["sVerdict"] == imageArchive.S_RECHECK_DIFFERS


def testRecheckReadsTheEnvelopeFromTheRepositoryWhenNotGiven(
    dockerDouble, tmp_path,
):
    """With no envelope passed, the one on disk is what is compared."""
    fnWriteEnvelope(
        tmp_path,
        fdictEnvironmentWithStreamHash(
            fsSha256Of(BA_IMAGE_STREAM),
        )["dictContainer"],
    )
    dictVerdict = imageDeposit.fdictRecheckArchiveAgainstLocalImage(
        str(tmp_path),
    )
    assert dictVerdict["sVerdict"] == imageArchive.S_RECHECK_MATCHED


# ── deposit: discarding a failed draft ───────────────────────────


class _FailingUploadClient:
    """A client whose upload fails and whose draft cannot be discarded."""

    sService = "sandbox"

    def __init__(self):
        self.listDeleted = []

    def fdictCreateDraft(self, dictMetadata):
        return {"id": 4321, "links": {"bucket": "https://x.invalid/b"}}

    def fnUploadToBucket(self, sBucketUrl, sPath, **dictCallbacks):
        raise ConnectionError("upload interrupted")

    def fnDeleteDraft(self, iDepositId):
        self.listDeleted.append(iDepositId)
        raise RuntimeError("delete refused")


def testAnUndiscardableDraftIsLoggedAndTheOriginalFailureRaised(
    tmp_path, caplog,
):
    """The upload error is the news; the stuck draft id is logged."""
    pathTarball = tmp_path / "environment-image.tar.gz"
    pathTarball.write_bytes(b"tarball")
    clientFailing = _FailingUploadClient()
    tTarball = (str(pathTarball), fsSha256Of(b"tarball"), 7,
                fsSha256Of(b"stream"), hashlib.md5(b"tarball").hexdigest())
    with caplog.at_level(logging.WARNING, logger=imageDeposit.__name__):
        with pytest.raises(ConnectionError, match="upload interrupted"):
            imageDeposit.fdictUploadAndPublishImageArchive(
                clientFailing, S_IMAGE_REFERENCE, S_ARCHITECTURE,
                {"sTitle": "Alpha"}, tTarball,
            )
    assert clientFailing.listDeleted == [4321]
    assert "4321" in caplog.text
    assert "discard it manually" in caplog.text


# ── stamping the record and re-pinning the manifest ──────────────


def fdictMinimalRecord():
    """Return a record whose content the stamp must carry verbatim."""
    return {"sVersionDoi": "10.5281/zenodo.55", "sTarballSha256": "x"}


def testStampKeepsTheRecordWhenTheManifestRepinFails(
    monkeypatch, tmp_path,
):
    """A failed re-pin is a False flag; the record has already landed."""
    fnInitGitRepository(tmp_path)
    fnWriteEnvelope(tmp_path, {"sImageDigest": S_IMAGE_REFERENCE})
    from vaibify.reproducibility import manifestWriter

    def fnFailToWrite(filesRepo, dictWorkflow):
        raise OSError("disk full while writing the manifest")

    monkeypatch.setattr(manifestWriter, "fnWriteManifest", fnFailToWrite)
    dictResult = imageDeposit.fdictStampArchiveRecord(
        str(tmp_path), {"listSteps": []}, fdictMinimalRecord(),
        dictExtraContainerFields={"dictSupersededImageArchive": {"a": 1}},
    )
    assert dictResult["bManifestRefreshed"] is False
    dictContainer = fdictReadEnvelope(tmp_path)["dictContainer"]
    assert dictContainer[imageArchive.S_IMAGE_ARCHIVE_KEY] == (
        fdictMinimalRecord()
    )
    assert dictContainer["dictSupersededImageArchive"] == {"a": 1}
    assert dictContainer["sImageDigest"] == S_IMAGE_REFERENCE


def testStampLetsACarrierRefusalEscapeTheRepin(monkeypatch, tmp_path):
    """A control-plane refusal is not an I/O failure and is re-raised."""
    fnInitGitRepository(tmp_path)
    fnWriteEnvelope(tmp_path, {"sImageDigest": S_IMAGE_REFERENCE})
    from vaibify.reproducibility import manifestWriter

    def fnRefuse(filesRepo, dictWorkflow):
        raise mutationAdmission.MutationNotAdmittedError("no admission")

    monkeypatch.setattr(manifestWriter, "fnWriteManifest", fnRefuse)
    with pytest.raises(mutationAdmission.MutationNotAdmittedError):
        imageDeposit.fdictStampArchiveRecord(
            str(tmp_path), {"listSteps": []}, fdictMinimalRecord(),
        )


# ── promotion: token validation ──────────────────────────────────


def testPromotionRefusesWhenProductionZenodoCannotBeReached(zenodoDouble):
    """A non-auth Zenodo failure refuses before any expensive save."""
    zenodoDouble.iSearchStatus = 503
    clientProduction = zenodoClient.ZenodoClient(
        "zenodo", sToken=S_PRODUCTION_TOKEN,
    )
    with pytest.raises(
        archivePromotion.PromotionRefusedError,
        match="could not reach production Zenodo",
    ):
        archivePromotion.fnRefuseUnlessTokenValidates(clientProduction)
    assert zenodoDouble.flistMethodsAndUrls() == [
        ("GET", "https://zenodo.org/api/deposit/depositions"),
    ]


def testPromotionAcceptsAWorkingProductionToken(zenodoDouble):
    """A token that authenticates a read passes the cheap check."""
    archivePromotion.fnRefuseUnlessTokenValidates(
        zenodoClient.ZenodoClient("zenodo", sToken=S_PRODUCTION_TOKEN),
    )
    dictHeaders = zenodoDouble.listRequests[0][2]["headers"]
    assert dictHeaders["Authorization"] == "Bearer " + S_PRODUCTION_TOKEN


# ── promotion: the local-image lane ──────────────────────────────


def fdictSandboxRecord(**dictOverrides):
    """Return a sandbox deposit record covering the fixture envelope."""
    dictRecord = {
        "sVersionDoi": "10.5072/zenodo.4242",
        "sConceptDoi": "10.5072/zenodo.4241",
        "sZenodoService": "sandbox",
        "sTarballName": "environment-image.tar.gz",
        "sTarballSha256": fsSha256Of(b"sandbox tarball bytes"),
        "sTarballMd5": hashlib.md5(b"sandbox tarball bytes").hexdigest(),
        "sImageStreamSha256": fsSha256Of(b"sandbox image stream"),
        "iTarballBytes": len(b"sandbox tarball bytes"),
        "sProvenance": imageArchive.S_PROVENANCE_VERIFIED_EQUIVALENT,
        "sImageDigest": S_IMAGE_REFERENCE,
        "sArchitecture": S_ARCHITECTURE,
    }
    dictRecord.update(dictOverrides)
    return dictRecord


def fdictContainerWithRecord(dictRecord):
    """Return the envelope's container block carrying one record."""
    return {
        "sImageDigest": S_IMAGE_REFERENCE,
        "sArchitecture": S_ARCHITECTURE,
        imageArchive.S_IMAGE_ARCHIVE_KEY: dictRecord,
    }


def fdictProgressHooks(listEvents):
    """Return progress hooks that append ``(sName, value)`` events."""
    return {
        "fnReportSaveProgress": lambda iRead, iTotal: listEvents.append(
            ("save", iRead, iTotal),
        ),
        "fnReportUploadStarted": lambda iBytes: listEvents.append(
            ("upload", iBytes),
        ),
        "fnReportVerifying": lambda: listEvents.append(("verifying",)),
    }


def testPromotionFromTheLocalImageDepositsOnProduction(
    dockerDouble, zenodoDouble, tmp_path,
):
    """Production client, stamped id, durable draft id, provenance kept."""
    pathRepo = tmp_path / "repo"
    dictContainer = fdictContainerWithRecord(fdictSandboxRecord())
    fnWriteEnvelope(pathRepo, dictContainer)
    listSidecarAtUpload = []
    zenodoDouble.fnOnUpload = lambda: listSidecarAtUpload.extend(
        syncBookkeeping.flistReadPendingPromotions(
            str(pathRepo), S_SIDECAR_KEY,
        ),
    )
    listEvents = []
    dictRecord, sPromotionId = archivePromotion.ftPromoteImageArchive(
        dictContainer, S_PRODUCTION_TOKEN, str(pathRepo), S_SIDECAR_KEY,
        {"sTitle": "Alpha environment", "sDescription": "An image."},
        fdictProgressHooks(listEvents),
    )
    assert sPromotionId.startswith("promotion-")
    assert all(
        sUrl.startswith("https://zenodo.org/")
        for _, sUrl in zenodoDouble.flistMethodsAndUrls()
    )
    sMethod, sUrl, dictKwargs = zenodoDouble.listRequests[0]
    assert (sMethod, sUrl) == (
        "POST", "https://zenodo.org/api/deposit/depositions",
    )
    assert dictKwargs["headers"]["Authorization"] == (
        "Bearer " + S_PRODUCTION_TOKEN
    )
    sDescription = dictKwargs["json"]["metadata"]["description"]
    assert archivePromotion.fbDescriptionCarriesPromotionId(
        sDescription, sPromotionId,
    )
    assert [dictPending["iDepositId"] for dictPending in listSidecarAtUpload] \
        == [I_DRAFT_ID]
    assert listSidecarAtUpload[0]["sPhase"] == archivePromotion.S_PHASE_DRAFTED
    assert listSidecarAtUpload[0]["listFiles"][0]["sBasename"] == (
        "environment-image.tar.gz"
    )
    assert dictRecord["sVersionDoi"] == f"10.5281/zenodo.{I_DRAFT_ID}"
    assert dictRecord["sZenodoService"] == "zenodo"
    assert dictRecord["sProvenance"] == (
        imageArchive.S_PROVENANCE_VERIFIED_EQUIVALENT
    )
    assert dictRecord["sImageStreamSha256"] == fsSha256Of(BA_IMAGE_STREAM)
    assert ("verifying",) in listEvents
    assert any(tEvent[0] == "save" for tEvent in listEvents)
    assert os.listdir(tmp_path / "home" / ".vaibify" / "imageArchive") == []


# ── promotion: the sandbox-archive lane ──────────────────────────


S_SANDBOX_RECORD_URL = "https://sandbox.zenodo.org/api/records/4242"
S_SANDBOX_FILE_URL = (
    "https://sandbox.zenodo.org/api/records/4242/files/"
    "environment-image.tar.gz/content"
)


def fnServeSandboxDeposit(zenodoDouble, baServed):
    """Publish the sandbox record page and serve ``baServed`` as its file."""
    zenodoDouble.dictPublishedRecords[S_SANDBOX_RECORD_URL] = {"files": [{
        "key": "environment-image.tar.gz",
        "links": {"self": S_SANDBOX_FILE_URL},
    }]}
    zenodoDouble.dictServedFiles[S_SANDBOX_FILE_URL] = baServed


def testPromotionFallsBackToTheVerifiedSandboxBytes(
    dockerDouble, zenodoDouble, tmp_path,
):
    """An image gone from the daemon re-deposits the sandbox bytes."""
    dockerDouble.iInspectExitCode = 1
    fnServeSandboxDeposit(zenodoDouble, b"sandbox tarball bytes")
    pathRepo = tmp_path / "repo"
    dictContainer = fdictContainerWithRecord(fdictSandboxRecord())
    fnWriteEnvelope(pathRepo, dictContainer)
    listEvents = []
    dictRecord, _sPromotionId = archivePromotion.ftPromoteImageArchive(
        dictContainer, S_PRODUCTION_TOKEN, str(pathRepo), S_SIDECAR_KEY,
        {"sTitle": "Alpha"}, fdictProgressHooks(listEvents),
    )
    assert zenodoDouble.dictUploaded == {
        "environment-image.tar.gz": b"sandbox tarball bytes",
    }
    assert dictRecord["sTarballSha256"] == fsSha256Of(b"sandbox tarball bytes")
    assert dictRecord["sImageStreamSha256"] == (
        fsSha256Of(b"sandbox image stream")
    )
    assert dictRecord["sVersionDoi"] == f"10.5281/zenodo.{I_DRAFT_ID}"
    assert ("save", len(b"sandbox tarball bytes"),
            len(b"sandbox tarball bytes")) in listEvents
    assert not any(
        listArgv[:2] == ["docker", "save"]
        for listArgv in dockerDouble.listCommands
    )


def testATamperedSandboxDownloadIsNeverPromoted(
    dockerDouble, zenodoDouble, tmp_path,
):
    """Bytes that do not hash to the record are refused before any draft."""
    dockerDouble.iInspectExitCode = 1
    fnServeSandboxDeposit(zenodoDouble, b"sandbox tarball bytez")
    pathRepo = tmp_path / "repo"
    dictContainer = fdictContainerWithRecord(fdictSandboxRecord())
    fnWriteEnvelope(pathRepo, dictContainer)
    with pytest.raises(
        imageAcquisition.ImageAcquisitionRefusedError,
        match="did not match its hash",
    ):
        archivePromotion.ftPromoteImageArchive(
            dictContainer, S_PRODUCTION_TOKEN, str(pathRepo), S_SIDECAR_KEY,
            {"sTitle": "Alpha"}, {},
        )
    assert not any(
        sMethod in ("POST", "PUT")
        for sMethod, _ in zenodoDouble.flistMethodsAndUrls()
    )
    assert syncBookkeeping.flistReadPendingPromotions(
        str(pathRepo), S_SIDECAR_KEY,
    ) == []
