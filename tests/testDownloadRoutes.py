"""The download route reads through the confined readers.

Source: ``vaibify/gui/routes/downloadRoutes.py``.

Two lanes, each over a REAL connection: a host project served by the real
``HostConnection`` (real files, real links, real archive bytes) and a
container project served by the gated Docker double. The confined programs
themselves are proven against real trees and a real container in
``tests/testConfinedReadParity.py`` and
``tests/testConfinedStreamingLive.py``; what is proven here is the
route's contract: what it answers for each outcome, that a refusal is a
status and not a truncated body, and that HEAD tells the page what a GET
would do without reading the file.
"""

import hashlib
import io
import os
import tarfile

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from vaibify.config import registryManager
from vaibify.gui.routes import downloadRoutes
from vaibify.host.hostConnection import HostConnection

S_PROJECT = "downloadProject"
I_MEBIBYTE = 1 << 20


@pytest.fixture(autouse=True)
def fixtureIsolateRegistry(tmp_path, monkeypatch):
    sRegistryDirectory = str(tmp_path / ".vaibify")
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_DIRECTORY", sRegistryDirectory)
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_PATH",
        os.path.join(sRegistryDirectory, "registry.json"))
    monkeypatch.setattr(
        registryManager, "_S_LOCK_PATH",
        os.path.join(sRegistryDirectory, "registry.lock"))


@pytest.fixture
def tHostDownload(tmp_path):
    """Return ``(client, project root)`` over a registered host project."""
    sRoot = os.path.realpath(str(tmp_path / S_PROJECT))
    os.makedirs(sRoot)
    with open(os.path.join(sRoot, "vaibify.yml"), "w") as fileConfig:
        fileConfig.write(f"projectName: {S_PROJECT}\n")
    registryManager.fnAddProject(sRoot, sMode="host")
    app = FastAPI()
    dictCtx = {
        "require": lambda *aArgs: None,
        "docker": HostConnection(),
        "workflows": {}, "paths": {},
        "workflowDir": lambda sResourceId: sRoot,
    }
    downloadRoutes.fnRegisterAll(app, dictCtx, "/workspace")
    return TestClient(app, raise_server_exceptions=False), sRoot


def _fnWrite(sPath, baContent):
    os.makedirs(os.path.dirname(sPath), exist_ok=True)
    with open(sPath, "wb") as fileOut:
        fileOut.write(baContent)


def _fsUrl(sPath):
    return f"/api/files/{S_PROJECT}/download/{sPath.lstrip('/')}"


def testAFileDownloadsAsItselfWithItsExactName(tHostDownload):
    client, sRoot = tHostDownload
    baContent = os.urandom(3 * I_MEBIBYTE + 5)
    _fnWrite(sRoot + "/data/result.csv", baContent)
    response = client.get(_fsUrl(sRoot + "/data/result.csv"))
    assert response.status_code == 200, response.text
    assert response.content == baContent
    assert response.headers["content-disposition"].startswith(
        'attachment; filename="result.csv"')
    assert response.headers["content-type"] == "application/octet-stream"


def testAnEmptyFileDownloadsAsAnEmptyBody(tHostDownload):
    client, sRoot = tHostDownload
    _fnWrite(sRoot + "/empty.txt", b"")
    response = client.get(_fsUrl(sRoot + "/empty.txt"))
    assert response.status_code == 200
    assert response.content == b""


@pytest.mark.falsification
def testALinkInsideTheProjectDownloadsTheTargetsBytesNotAnEmptyFile(
    tHostDownload,
):
    """A symlinked file yields what it points at.

    The old daemon-archive route returned zero bytes for a link (the tar
    held a link member, which the payload reader skipped).

    Kills: opening the path without following an in-project final link.
    """
    client, sRoot = tHostDownload
    _fnWrite(sRoot + "/results/real.dat", b"the real bytes")
    os.symlink("results/real.dat", sRoot + "/latest.dat")
    response = client.get(_fsUrl(sRoot + "/latest.dat"))
    assert response.status_code == 200, response.text
    assert response.content == b"the real bytes"


@pytest.mark.falsification
def testALinkLeadingOutOfTheProjectIsRefusedNamingIt(tHostDownload, tmp_path):
    """Kills: following a link whose target is outside the project."""
    client, sRoot = tHostDownload
    _fnWrite(str(tmp_path / "outside" / "secret.txt"), b"secret")
    os.symlink(str(tmp_path / "outside" / "secret.txt"), sRoot + "/leak")
    response = client.get(_fsUrl(sRoot + "/leak"))
    assert response.status_code == 403, response.text
    assert b"secret" not in response.content.replace(b"secret.txt", b"")
    assert "leak" in response.text


def testAMissingFileIsA404WithAMessageNotAnEmptyDownload(tHostDownload):
    client, sRoot = tHostDownload
    response = client.get(_fsUrl(sRoot + "/nothing.csv"))
    assert response.status_code == 404
    assert "nothing.csv" in response.json()["detail"]


def testAPathOutsideTheProjectIsRefusedBeforeAnythingIsOpened(
    tHostDownload, tmp_path,
):
    """Refused by the route's lexical jail AND by the host path guard.

    Defended twice, so no single mutation here kills it; the route's own
    jail is kill-confirmed over the container double in
    ``tests/testCoverageRoutesAFileRoutes.py``.
    """
    client, sRoot = tHostDownload
    _fnWrite(str(tmp_path / "elsewhere.txt"), b"x")
    response = client.get(
        f"/api/files/{S_PROJECT}/download/{os.path.realpath(str(tmp_path))}"
        "/elsewhere.txt")
    assert response.status_code == 403


@pytest.mark.falsification
def testAFolderDownloadsAsATarNamedForIt(tHostDownload):
    """``bFolder`` selects the archive reader and the ``.tar`` name.

    Kills: ignoring ``bFolder`` (a folder answered as a file read).
    """
    client, sRoot = tHostDownload
    _fnWrite(sRoot + "/tree/a/one.txt", b"one")
    os.makedirs(sRoot + "/tree/empty")
    os.symlink("a/one.txt", sRoot + "/tree/link")
    response = client.get(
        _fsUrl(sRoot + "/tree"), params={"bFolder": "true"})
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/x-tar"
    assert response.headers["content-disposition"].startswith(
        'attachment; filename="tree.tar"')
    with tarfile.open(fileobj=io.BytesIO(response.content)) as tarIn:
        dictMembers = {infoMember.name: infoMember for infoMember in tarIn}
        assert tarIn.extractfile("tree/a/one.txt").read() == b"one"
    assert dictMembers["tree/empty"].isdir()
    assert dictMembers["tree/link"].issym()


def testAFileRequestedAsAFolderIsRefusedNotAnEmptyArchive(tHostDownload):
    client, sRoot = tHostDownload
    _fnWrite(sRoot + "/plain.txt", b"x")
    response = client.get(
        _fsUrl(sRoot + "/plain.txt"), params={"bFolder": "true"})
    assert response.status_code == 403, response.text


def testADirectoryRequestedAsAFileIsRefused(tHostDownload):
    client, sRoot = tHostDownload
    os.makedirs(sRoot + "/folder")
    response = client.get(_fsUrl(sRoot + "/folder"))
    assert response.status_code == 403, response.text
    assert "regular file" in response.text


@pytest.mark.falsification
def testHeadAnswersWhatAGetWouldWithoutSendingTheBody(tHostDownload):
    """The page probes with HEAD so it can toast a failure.

    Kills: answering HEAD with the streamed body (a probe that reads the
    whole file) or with a 200 for a path a GET would refuse.
    """
    client, sRoot = tHostDownload
    _fnWrite(sRoot + "/data.bin", os.urandom(2 * I_MEBIBYTE))
    os.symlink("/etc/hosts", sRoot + "/leak")
    responseOk = client.head(_fsUrl(sRoot + "/data.bin"))
    assert responseOk.status_code == 200, responseOk.text
    assert responseOk.content == b""
    assert responseOk.headers["content-disposition"].startswith(
        'attachment; filename="data.bin"')
    assert client.head(_fsUrl(sRoot + "/leak")).status_code == 403
    assert client.head(_fsUrl(sRoot + "/absent")).status_code == 404


@pytest.mark.falsification
def testHeadClosesTheReadItOpenedInsteadOfLeavingItRunning(
    tHostDownload, monkeypatch,
):
    """The probe starts the read and stops it; it never drains the file.

    Kills: returning from HEAD without closing the opened iterator, which
    leaves a container exec streaming a whole file to nobody.
    """
    client, sRoot = tHostDownload
    _fnWrite(sRoot + "/big.bin", os.urandom(I_MEBIBYTE))
    listClosed = []
    fnRealProbe = downloadRoutes._ftProbeFirstChunk

    class _RecordsItsClose:
        def __init__(self, iterWrapped):
            self._iterWrapped = iterWrapped

        def __iter__(self):
            return self

        def __next__(self):
            return next(self._iterWrapped)

        def close(self):
            listClosed.append(1)
            self._iterWrapped.close()

    def ftProbeAndRecordClose(*tArguments):
        baFirst, iterChunks = fnRealProbe(*tArguments)
        return baFirst, _RecordsItsClose(iterChunks)

    monkeypatch.setattr(
        downloadRoutes, "_ftProbeFirstChunk", ftProbeAndRecordClose)
    assert client.head(_fsUrl(sRoot + "/big.bin")).status_code == 200
    assert listClosed == [1]


@pytest.mark.falsification
def testAnUnreadableProjectRootNeverFallsBackToTheWorkspaceConstant(
    tHostDownload, tmp_path,
):
    """A host project's download jail is ITS directory, not ``/workspace``.

    The same guard, in the new module: a file inside the project must be
    served and the workspace constant must not be what bounds it.

    Kills: resolving the root as the workspace constant instead of the
    registered project directory.
    """
    client, sRoot = tHostDownload
    _fnWrite(sRoot + "/inside.txt", b"inside")
    assert client.get(_fsUrl(sRoot + "/inside.txt")).content == b"inside"


def testTheContentDispositionSurvivesAHostileFilename():
    sHeader = downloadRoutes.fsBuildContentDisposition('a".txt; x="y')
    assert 'filename="a\\".txt; x=\\"y"' in sHeader
