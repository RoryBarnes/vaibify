"""A running environment deposit can be stopped -- until it publishes.

The upload runs for minutes, and there was no way to abandon it once
started (researcher-requested, 2026-09-29). A stop is honored at the
deposit's next checkpoint and never after the publish begins, because
the publish mints a DOI that cannot be taken back. The first test runs
the route's whole synchronous deposit, with only the image save and
Zenodo replaced, so the stop crosses the real upload and discard code.
"""

import asyncio
import io

import pytest

from vaibify.gui import archiveProgress
from vaibify.gui.routes import environmentArchiveRoutes
from vaibify.reproducibility import imageDeposit, zenodoClient


def _fnPatchTheSaveAndTheAgentCheck(monkeypatch, tmp_path):
    pathTarball = tmp_path / "environment-image.tar.zst"
    pathTarball.write_bytes(b"not really an image")
    monkeypatch.setattr(
        imageDeposit, "ftSaveAndCompressImage",
        lambda sImage, sScratch, fnReport: (
            str(pathTarball), "sha256:" + "b" * 64, 19,
            "sha256:" + "c" * 64, "d" * 32,
        ),
    )
    monkeypatch.setattr(
        imageDeposit, "fsResolveDepositScratchDirectory",
        lambda: str(tmp_path / "scratch"),
    )
    monkeypatch.setattr(
        environmentArchiveRoutes, "_fnRefuseAgentsInTheEnvironment",
        lambda sContainerId, dictContainer: None,
    )


class _ZenodoThatIsStoppedMidUpload:
    """Reports two MiB of upload, with the researcher's stop between them."""

    listPublished = []
    listDeleted = []
    bUploadFinished = False
    sContainerId = ""
    sRepo = ""

    def __init__(self, sService, sToken=None):
        self.sService = sService

    def fdictCreateDraft(self, dictMetadata):
        return {"id": 7, "links": {"bucket": "https://example/b"}}

    def fnUploadToBucket(self, sUrl, sPath, fnReportProgress=None,
                         fnReportAttemptFailed=None):
        fnReportProgress(1024 * 1024, 19, 1)
        assert archiveProgress.fbRequestStop(
            _ZenodoThatIsStoppedMidUpload.sContainerId,
            _ZenodoThatIsStoppedMidUpload.sRepo,
        )
        fnReportProgress(2 * 1024 * 1024, 19, 1)
        _ZenodoThatIsStoppedMidUpload.bUploadFinished = True

    def fdictGetDeposit(self, iDepositId):
        return {"files": [{"key": "environment-image.tar.zst",
                           "checksum": "md5:" + "d" * 32, "filesize": 19}]}

    def fdictPublishDraft(self, iDepositId):
        _ZenodoThatIsStoppedMidUpload.listPublished.append(iDepositId)
        return {"doi": "10.5072/zenodo.7", "conceptdoi": ""}

    def fnDeleteDraft(self, iDepositId):
        _ZenodoThatIsStoppedMidUpload.listDeleted.append(iDepositId)


@pytest.mark.falsification
def test_a_stop_mid_upload_publishes_nothing_and_discards_the_draft(
    tmp_path, monkeypatch,
):
    """The upload stops within one MiB, not at the end.

    The publish gate would refuse a stop left pending all the way to
    the end, so "nothing was published" alone cannot tell a stop
    honored mid-upload from one honored after the whole image went up;
    the upload must not have finished.

    Kills: progress reports that no longer check for a stop.
    """
    _fnPatchTheSaveAndTheAgentCheck(monkeypatch, tmp_path)
    monkeypatch.setattr(
        zenodoClient, "ZenodoClient", _ZenodoThatIsStoppedMidUpload)
    sContainerId, sRepo = "cid-stop", str(tmp_path)
    _ZenodoThatIsStoppedMidUpload.sContainerId = sContainerId
    _ZenodoThatIsStoppedMidUpload.sRepo = sRepo
    _ZenodoThatIsStoppedMidUpload.listPublished = []
    _ZenodoThatIsStoppedMidUpload.listDeleted = []
    _ZenodoThatIsStoppedMidUpload.bUploadFinished = False
    archiveProgress.fnRegisterDeposit(
        sContainerId, None, sRepo, bStoppable=True)
    try:
        asyncio.run(environmentArchiveRoutes._fnRunDepositWorker(
            sContainerId, {"sWorkflowName": "w"},
            {"sImageDigest": "registry.example/p@sha256:" + "a" * 64,
             "sArchitecture": "arm64"},
            {"sZenodoService": "sandbox", "dictParentArchive": {}},
            "token", sRepo,
        ))
        dictSeen = archiveProgress.fdictReadDeposit(sContainerId, sRepo)
    finally:
        archiveProgress.fnForgetDeposit(sContainerId, sRepo)
    assert _ZenodoThatIsStoppedMidUpload.bUploadFinished is False, (
        "the upload ran to the end after the stop was asked for"
    )
    assert _ZenodoThatIsStoppedMidUpload.listPublished == []
    assert _ZenodoThatIsStoppedMidUpload.listDeleted == [7]
    assert dictSeen["sPhase"] == archiveProgress.S_PHASE_STOPPED


@pytest.mark.falsification
def test_a_stop_cannot_land_once_the_publish_has_begun():
    """Kills: accepting a stop during the publish, a promise nothing keeps."""
    sContainerId, sRepo = "cid-publishing", "/repo/publishing"
    archiveProgress.fnRegisterDeposit(
        sContainerId, None, sRepo, bStoppable=True)
    try:
        assert archiveProgress.fbEnterPublishingUnlessStopped(sContainerId)
        assert archiveProgress.fbRequestStop(sContainerId, sRepo) is False
    finally:
        archiveProgress.fnForgetDeposit(sContainerId, sRepo)


def test_a_stop_asked_first_keeps_the_publish_from_starting():
    sContainerId, sRepo = "cid-stop-first", "/repo/stop-first"
    archiveProgress.fnRegisterDeposit(
        sContainerId, None, sRepo, bStoppable=True)
    try:
        assert archiveProgress.fbRequestStop(sContainerId, sRepo)
        assert archiveProgress.fbEnterPublishingUnlessStopped(
            sContainerId) is False
    finally:
        archiveProgress.fnForgetDeposit(sContainerId, sRepo)


def test_a_promotion_and_another_projects_deposit_cannot_be_stopped():
    """A promotion keeps a recovery record of its draft; stopping it
    would leave that record naming a discarded draft."""
    sContainerId, sRepo = "cid-promotion", "/repo/promotion"
    archiveProgress.fnRegisterDeposit(sContainerId, None, sRepo)
    try:
        assert archiveProgress.fbRequestStop(sContainerId, sRepo) is False
        archiveProgress.fnRegisterDeposit(
            sContainerId, None, sRepo, bStoppable=True)
        assert archiveProgress.fbRequestStop(
            sContainerId, "/repo/another-project") is False
    finally:
        archiveProgress.fnForgetDeposit(sContainerId, sRepo)


class _SaveThatKeepsStreaming:
    """A docker save whose output never ends until it is killed."""

    def __init__(self, listArgs, stdout=None, stderr=None):
        self.stdout = io.BytesIO(b"x" * (64 * 1024 * 1024))
        self.stderr = io.BytesIO(b"")
        self.listCalls = []
        self.returncode = None
        _SaveThatKeepsStreaming.processLast = self

    def kill(self):
        self.listCalls.append("kill")

    def wait(self):
        self.listCalls.append("wait")
        self.returncode = -9
        return self.returncode


@pytest.mark.falsification
def test_an_abandoned_save_kills_and_reaps_docker_save(tmp_path, monkeypatch):
    """Kills: leaving docker save running when the save is abandoned."""
    monkeypatch.setattr(
        imageDeposit.subprocess, "Popen", _SaveThatKeepsStreaming)
    monkeypatch.setattr(imageDeposit, "fiReadImageSizeBytes", lambda s: 1)
    monkeypatch.setattr(
        imageDeposit, "_fnRefuseWithoutRoomOnDisk", lambda s, i: None)

    def fnStopAtFirstReport(iRead, iTotal):
        raise archiveProgress.DepositStoppedError("You stopped the deposit.")

    with pytest.raises(archiveProgress.DepositStoppedError):
        imageDeposit.ftSaveAndCompressImage(
            "image", str(tmp_path), fnStopAtFirstReport,
        )
    assert _SaveThatKeepsStreaming.processLast.listCalls == ["kill", "wait"]
