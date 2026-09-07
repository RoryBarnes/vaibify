"""Reproduce a published project against a REAL daemon, deposit and all.

The unit lane proves the chain and the seam compose; it cannot prove
that a daemon honours a ``platform`` on create, that ``docker load``
of a saved tarball answers with an ID the shadow can be built from, or
that a real container runs the staged snapshot's step. Those are the
facts the whole feature rests on, and the environment-archive work
recorded that "docker run by ID after a fallback load" had been
reasoned about and never executed. This closes that gap.

The fixture builds a tiny image on top of a stock python base -- one
``useradd`` and a ``USER`` directive, because the shadow's execs run as
the image's declared user and its typed reads need a python -- saves it as a plain tarball, serves that
tarball from a loopback "deposit", and pins an envelope whose image
reference NO registry serves. The chain then has exactly one link that
can work, and the report must say which.

Skipped automatically when no daemon is reachable, unless
``VAIBIFY_REQUIRE_DOCKER_DAEMON`` demands one.
"""

import hashlib
import io
import json
import os
import shutil
import tarfile

import pytest
from click.testing import CliRunner

from tests.reproductionSourceFixtures import (
    fdictBuildEnvelope,
    fdictBuildWorkflow,
    fnCommitEverything,
    fnWriteJson,
    fnWriteManifest,
    fnWriteText,
    fsBuildPublishedProject,
)
from tests.testDockerConnectionLive import fnRequireDaemonReachable
from tests.testImageAcquisition import LoopbackDeposit
from vaibify.cli.commandReproduce import fnReproduceCommand
from vaibify.docker import disposableContainer
from vaibify.reproducibility import imageAcquisition
from vaibify.reproducibility import imageDeposit
from vaibify.reproducibility import reproductionReport
from vaibify.reproducibility import reproductionSource


pytestmark = pytest.mark.docker_live

# The same base the determinism live leg pulls: the shadow's typed reads
# run a python program inside the container, so the image needs one.
S_BASE_IMAGE = "python:3.12-slim"
S_PROBE_TAG = "vaibify-reproduction-probe:live"
S_UNSERVED_REFERENCE = "registry.invalid/reproduction-probe@sha256:" + "f" * 64
S_DOI = "10.5281/zenodo.7000009"
S_TARBALL_NAME = "environment-image.tar"

_S_PROBE_DOCKERFILE = (
    f"FROM {S_BASE_IMAGE}\n"
    # The shadow's execs run as the image's declared USER by name, at
    # the uid the create specification pins.
    "RUN useradd --uid 1000 --create-home researcher\n"
    "USER researcher\n"
)


def _fimageBuildProbeImage(dockerDisposable):
    """Build the probe image from a stock base; return the SDK image."""
    bufferContext = io.BytesIO()
    with tarfile.open(fileobj=bufferContext, mode="w") as fileTar:
        baDockerfile = _S_PROBE_DOCKERFILE.encode("utf-8")
        infoMember = tarfile.TarInfo("Dockerfile")
        infoMember.size = len(baDockerfile)
        fileTar.addfile(infoMember, io.BytesIO(baDockerfile))
    bufferContext.seek(0)
    imageProbe, _iterLogs = dockerDisposable.images.build(
        fileobj=bufferContext, custom_context=True, tag=S_PROBE_TAG, rm=True,
    )
    return imageProbe


def _fbaSaveImage(imageProbe):
    """Return ``docker save`` of the image as plain tar bytes."""
    return b"".join(imageProbe.save(named=False))


@pytest.fixture
def dictLiveProbe(tmp_path, monkeypatch):
    """A built image, its saved tarball on a loopback deposit, and a project.

    Every piece is real: the image is built by the daemon, the tarball
    is what the daemon saved, and the envelope pins a reference that
    resolves nowhere so the archive link is the only one that can
    serve. The tag is removed after the save so the loaded copy is
    what the shadow runs, never the build.
    """
    fnRequireDaemonReachable()
    dockerDisposable = disposableContainer.fdockerCreateDisposableClient()
    imageProbe = _fimageBuildProbeImage(dockerDisposable)
    sArchitecture = str(imageProbe.attrs.get("Architecture") or "")
    baTarball = _fbaSaveImage(imageProbe)
    sTarballSha = "sha256:" + hashlib.sha256(baTarball).hexdigest()
    pathFiles = tmp_path / "zenodo" / S_DOI / "files"
    pathFiles.mkdir(parents=True)
    (pathFiles / S_TARBALL_NAME).write_bytes(baTarball)
    dictEnvelope = fdictBuildEnvelope(dictArchiveRecord={
        "sVersionDoi": S_DOI, "sConceptDoi": "10.5281/zenodo.7000008",
        "sTarballName": S_TARBALL_NAME, "sTarballSha256": sTarballSha,
        "iTarballBytes": len(baTarball), "sProvenance": "original",
        "sImageDigest": S_UNSERVED_REFERENCE, "sArchitecture": sArchitecture,
    })
    dictEnvelope["dictContainer"]["sImageDigest"] = S_UNSERVED_REFERENCE
    dictEnvelope["dictContainer"]["sArchitecture"] = sArchitecture
    sRoot = os.path.realpath(str(tmp_path))
    monkeypatch.setattr(
        reproductionSource, "flistAdmittedLocalCloneRoots", lambda: [sRoot],
    )
    sRepoPath = os.path.join(sRoot, "liveProject")
    fsBuildPublishedProject(sRepoPath, dictEnvelope=dictEnvelope)
    dictWorkflow = fdictBuildWorkflow()
    dictWorkflow["listSteps"][0]["saDataCommands"] = ["cp numbers.txt numbers.txt.tmp && mv numbers.txt.tmp numbers.txt"]
    fnWriteJson(sRepoPath, ".vaibify/projects/project.json", dictWorkflow)
    fnWriteText(sRepoPath, "MakeNumbers/generate.py", "print(1)\n")
    fnWriteManifest(
        sRepoPath, ["MakeNumbers/generate.py", "MakeNumbers/numbers.txt"],
    )
    fnCommitEverything(sRepoPath, "pin the live envelope")
    monkeypatch.setattr(
        imageDeposit, "fsResolveDepositScratchDirectory",
        lambda: _fsFreshScratch(tmp_path),
    )
    try:
        dockerDisposable.images.remove(S_PROBE_TAG, force=True)
        yield {
            "sRepoPath": sRepoPath, "pathZenodo": tmp_path / "zenodo",
            "sArchitecture": sArchitecture, "dockerDisposable": dockerDisposable,
        }
    finally:
        for sReference in (S_PROBE_TAG, imageProbe.id):
            try:
                dockerDisposable.images.remove(sReference, force=True)
            except Exception:  # noqa: BLE001 -- cleanup only
                pass


def _fsFreshScratch(tmp_path):
    sPath = os.path.join(str(tmp_path), "scratch", os.urandom(4).hex())
    os.makedirs(sPath, exist_ok=True)
    return sPath


def test_a_deposit_loaded_image_runs_the_staged_snapshot_end_to_end(
    dictLiveProbe, monkeypatch,
):
    """Registry fails, the deposit loads, the shadow runs the loaded ID.

    The report must say the image came from the archive and that the
    re-check is vacuous, and the verdict must be reproduced: the
    step rewrites its output byte-for-byte, so the comparison inside
    the real shadow has something to grade.
    """
    with LoopbackDeposit(dictLiveProbe["pathZenodo"]) as server:
        monkeypatch.setattr(imageAcquisition, "_S_DOI_RESOLVER", server.sResolver)
        result = CliRunner().invoke(
            fnReproduceCommand,
            ["--from", dictLiveProbe["sRepoPath"], "--rerun"],
        )
    assert result.exit_code == 0, result.output
    assert "registry pull: failed" in result.output
    assert "archived deposit: served" in result.output
    assert "Verdict: reproduced" in result.output
    sReportsDirectory = reproductionReport.fsReportsDirectory()
    listReports = sorted(os.listdir(sReportsDirectory))
    assert len(listReports) == 1
    dictReport = json.load(open(os.path.join(sReportsDirectory, listReports[0])))
    assert dictReport["sObtainedFrom"] == "archive"
    assert dictReport["sImageReferenceRun"].startswith("sha256:")
    assert dictReport["dictImageRecheck"]["bVacuous"] is True
    assert dictReport["dictPlatform"]["sRequiredPlatform"] == (
        "linux/" + dictLiveProbe["sArchitecture"]
    )
    assert dictReport["dictPlatform"]["bEmulated"] is False
    assert dictReport["sShadowTeardown"] == "destroyed"
    assert dictReport["iOutputHashesTotal"] == 2
    assert not os.path.isdir(reproductionSource._fsStagingRoot()) or (
        os.listdir(reproductionSource._fsStagingRoot()) == []
    )
    assert shutil.which("docker"), "the CLI lane needs the docker CLI on PATH"


def test_the_daemon_refuses_a_create_for_the_wrong_platform(dictLiveProbe):
    """The platform REACHES the daemon: a wrong one is refused by name.

    Observing the request itself from outside is not possible, so the
    proof is the daemon's refusal of a platform the image is not built
    for. A create that ignored the platform would succeed here.
    """
    dockerDisposable = dictLiveProbe["dockerDisposable"]
    imageProbe = _fimageBuildProbeImage(dockerDisposable)
    sOther = "linux/s390x" if dictLiveProbe["sArchitecture"] != "s390x" else "linux/amd64"
    dictGateway = disposableContainer.fdictCreateDisposableGateway(
        dockerDisposable, "reproductionPlatformProbe",
    )
    with pytest.raises(Exception) as excinfo:
        disposableContainer.fdictReserveAndCreateContainer(
            dictGateway, "shadow", imageProbe.id, sPlatform=sOther,
        )
    assert "platform" in str(excinfo.value).lower()
    dictCreated = disposableContainer.fdictReserveAndCreateContainer(
        dictGateway, "shadow", imageProbe.id,
        sPlatform="linux/" + dictLiveProbe["sArchitecture"],
    )
    disposableContainer.fdictDestroyAndSettle(dictGateway, dictCreated["sHandle"])
