"""The streamed upload route: authenticate, then spool, then carry.

Source: ``vaibify/gui/routes/uploadRoutes.py`` and
``vaibify/gui/uploadStaging.py``.

The route is driven through the REAL application (middleware, owner
records, lease, commit carrier, write-ahead journal) over a Docker double
that calls the same admission gates the real connection calls, so a route
that lost its carrier raises at the primitive instead of passing against a
permissive mock. The confined write program and the streaming semantics
are proven separately against real programs and a real container
(``tests/testConfinedStreamingWrite.py``,
``tests/testConfinedStreamingLive.py``); this file proves the ORDER of
operations and what each refusal leaves behind.

The fixture is realistic on purpose: the workflow's repository is a
SUBDIRECTORY of the workspace and the panel opens at the workspace root.
The first version of this feature was green against a fixture whose
repository WAS the workspace, which is exactly the shape that hid a 403
on every real drop.
"""

import asyncio
import errno
import hashlib
import io
import os
import posixpath

import pytest
from starlette.requests import Request

from tests.testCarrierMigratedRoutes import (
    DockerDoubleThatCallsTheRealGates,
    S_CONTAINER_ID,
    S_CONTAINER_NAME,
    _fsReadWholeJournalForTheContainer,
    _tConnectGatedClient,
)
from vaibify.config import mutationAdmission
from vaibify.docker.confinedWrite import ContainerWriteExistsError
from vaibify.gui import uploadStaging

S_WORKSPACE = "/workspace"
S_STREAM_URL = f"/api/upload/{S_CONTAINER_ID}/stream"
S_VERDICT_URL = f"/api/upload/{S_CONTAINER_ID}/verdict"
I_MEBIBYTE = 1 << 20


class DockerDoubleForUploads(DockerDoubleThatCallsTheRealGates):
    """The gated double, plus what a streamed upload asks of a connection."""

    def __init__(self):
        super().__init__()
        self.dictUsage = {"iFreeBytes": 1 << 40}
        self.listStreamedWrites = []
        self.listFolders = []
        self.errorOnWrite = None
        self.listPriorHashes = None
        self.sJournalDuringTheWrite = ""

    def fnWriteFileFromStream(
        self, sContainerId, sFilePath, fileSource,
        iExpectedBytes=None, bReplaceAllowed=True, iMode=None,
        sAuthorizedRoot=None, tForbiddenNames=(), bCreateParents=False,
    ):
        mutationAdmission.fnAssertContainerWriteAdmitted(
            sContainerId, "fnWriteFileFromStream")
        self._fnRecordLiveAdmission(
            sContainerId, "fnWriteFileFromStream", sPath=sFilePath)
        # A settled record leaves the journal, so the write-ahead entry
        # can only be seen while the write is in flight.
        self.sJournalDuringTheWrite = _fsReadWholeJournalForTheContainer()
        if self.errorOnWrite is not None:
            raise self.errorOnWrite
        if not bReplaceAllowed and sFilePath in self._dictFiles:
            raise ContainerWriteExistsError(f"{sFilePath} already exists")
        baContent = b""
        listReadSizes = []
        for baChunk in iter(lambda: fileSource.read(I_MEBIBYTE), b""):
            listReadSizes.append(len(baChunk))
            baContent += baChunk
        assert iExpectedBytes in (None, len(baContent))
        self._dictFiles[sFilePath] = baContent
        self.listStreamedWrites.append({
            "sPath": sFilePath, "iExpectedBytes": iExpectedBytes,
            "bReplaceAllowed": bReplaceAllowed,
            "bCreateParents": bCreateParents,
            "sAuthorizedRoot": sAuthorizedRoot,
            "tForbiddenNames": tForbiddenNames,
            "listReadSizes": listReadSizes,
        })

    def fnMakeDirectory(
        self, sContainerId, sDirectoryPath,
        sAuthorizedRoot=None, tForbiddenNames=(),
    ):
        mutationAdmission.fnAssertContainerWriteAdmitted(
            sContainerId, "fnMakeDirectory")
        self._fnRecordLiveAdmission(
            sContainerId, "fnMakeDirectory", sPath=sDirectoryPath)
        self.listFolders.append(sDirectoryPath)

    def fdictReadFilesystemUsage(self, sContainerId, sPath):
        return dict(self.dictUsage)

    def fsHashContainerFileSha256(self, sContainerId, sPath):
        if self.listPriorHashes:
            return self.listPriorHashes.pop(0)
        baContent = self._dictFiles.get(sPath)
        return "" if baContent is None else hashlib.sha256(
            baContent).hexdigest()


@pytest.fixture(autouse=True)
def fixtureSpoolUnderATemporaryHome(tmp_path, monkeypatch):
    sHome = str(tmp_path / "home")
    os.makedirs(sHome)
    monkeypatch.setenv("HOME", sHome)
    yield sHome


@pytest.fixture
def tclientUploads():
    return _tConnectGatedClient(DockerDoubleForUploads())


def _fresponsePut(client, dictQuery, baBody=b"", dictHeaders=None):
    dictParams = {"iSizeBytes": len(baBody), **dictQuery}
    return client.put(
        S_STREAM_URL, params=dictParams, content=baBody,
        headers=dictHeaders or {})


def _fdictDrop(sFilename="data.csv", sDestination=S_WORKSPACE, **dictMore):
    return {"sDestination": sDestination, "sFilename": sFilename, **dictMore}


def _flistSpoolFiles(sHome):
    sDirectory = os.path.join(sHome, ".vaibify", "tmp", "uploads")
    return os.listdir(sDirectory) if os.path.isdir(sDirectory) else []


# ---------------------------------------------------------------------
# The happy path, through the carrier and the journal
# ---------------------------------------------------------------------


@pytest.mark.falsification
def testADropAtTheWorkspaceRootLandsUnderALockHeldAdmission(tclientUploads):
    """The drop that used to answer 403 now lands, carried in mode (b).

    The workspace holds the workflow's repository as a subdirectory and
    the panel opens at the workspace itself; the old route confined every
    upload to that repository and refused this. Asserting the admission
    MODE separates a carried mutation from one that merely did not raise.

    Kills: committing the spool without opening the lock-held carrier.
    """
    client, connectionDocker = tclientUploads
    baBody = b"a,b\n1,2\n"
    response = _fresponsePut(client, _fdictDrop(), baBody)
    assert response.status_code == 200, response.text
    assert response.json()["sPath"] == "/workspace/data.csv"
    assert connectionDocker._dictFiles["/workspace/data.csv"] == baBody
    listWrites = [
        dictEntry for dictEntry in connectionDocker.listAdmittedPrimitives
        if dictEntry["sPrimitive"] == "fnWriteFileFromStream"]
    assert [dictEntry["sMode"] for dictEntry in listWrites] == [
        mutationAdmission.S_ADMISSION_MODE_LOCK_HELD]


@pytest.mark.falsification
def testTheJournalRecordCarriesBothHashesBeforeTheWrite(tclientUploads):
    """The write-ahead record names what the file will be and what it was.

    Kills: omitting the expected hash, which leaves a crash during the
    copy unsettleable.
    """
    client, connectionDocker = tclientUploads
    connectionDocker._dictFiles["/workspace/data.csv"] = b"old"
    baBody = b"new bytes"
    response = _fresponsePut(
        client, _fdictDrop(bReplaceAllowed="true"), baBody)
    assert response.status_code == 200, response.text
    sJournal = connectionDocker.sJournalDuringTheWrite
    assert hashlib.sha256(baBody).hexdigest() in sJournal
    assert hashlib.sha256(b"old").hexdigest() in sJournal
    assert '"file-write"' in sJournal


def testTheWriterReceivesTheConfinementTheRouteDecided(tclientUploads):
    client, connectionDocker = tclientUploads
    response = _fresponsePut(client, _fdictDrop(), b"x")
    assert response.status_code == 200, response.text
    dictWrite = connectionDocker.listStreamedWrites[0]
    assert dictWrite["sAuthorizedRoot"] == S_WORKSPACE
    assert ".git" in dictWrite["tForbiddenNames"]
    assert dictWrite["iExpectedBytes"] == 1
    assert dictWrite["bReplaceAllowed"] is False
    assert dictWrite["bCreateParents"] is False


def testANestedFileAsksForItsParentsToBeCreated(tclientUploads):
    client, connectionDocker = tclientUploads
    response = _fresponsePut(
        client, _fdictDrop(sRelativePath="dropped/sub"), b"x")
    assert response.status_code == 200, response.text
    assert connectionDocker.listStreamedWrites[0]["sPath"] == (
        "/workspace/dropped/sub/data.csv")
    assert connectionDocker.listStreamedWrites[0]["bCreateParents"] is True


@pytest.mark.falsification
def testAFolderIsCreatedUnderTheDrainAndNeedsNoBody(tclientUploads):
    """Kills: creating the folder without a carrier."""
    client, connectionDocker = tclientUploads
    response = client.put(
        S_STREAM_URL, params={
            "sDestination": S_WORKSPACE, "sFilename": "emptydir",
            "sRelativePath": "dropped", "iSizeBytes": 0,
            "bDirectory": "true"})
    assert response.status_code == 200, response.text
    assert connectionDocker.listFolders == ["/workspace/dropped/emptydir"]
    listFolderEntries = [
        dictEntry for dictEntry in connectionDocker.listAdmittedPrimitives
        if dictEntry["sPrimitive"] == "fnMakeDirectory"]
    assert [dictEntry["sMode"] for dictEntry in listFolderEntries] == [
        mutationAdmission.S_ADMISSION_MODE_LOCK_HELD]


def testALargeBodyIsSpooledAndPassedOnInBoundedReads(tclientUploads):
    client, connectionDocker = tclientUploads
    baBody = os.urandom(I_MEBIBYTE) * 3 + b"tail"
    response = _fresponsePut(client, _fdictDrop("big.bin"), baBody)
    assert response.status_code == 200, response.text
    assert connectionDocker._dictFiles["/workspace/big.bin"] == baBody
    listReadSizes = connectionDocker.listStreamedWrites[0]["listReadSizes"]
    assert max(listReadSizes) <= I_MEBIBYTE and len(listReadSizes) >= 4


def testTheSpoolIsGoneAfterASuccessfulUpload(
    tclientUploads, fixtureSpoolUnderATemporaryHome,
):
    client, _ = tclientUploads
    assert _fresponsePut(client, _fdictDrop(), b"x").status_code == 200
    assert _flistSpoolFiles(fixtureSpoolUnderATemporaryHome) == []


# ---------------------------------------------------------------------
# What is refused, and that nothing is read or written for it
# ---------------------------------------------------------------------


@pytest.fixture
def listStreamCalls(monkeypatch):
    listCalls = []
    fnRealStream = Request.stream

    def fiterRecordingStream(self):
        # The middleware wraps each request in a subclass and creates its
        # stream object up front without consuming it; only the
        # ENDPOINT's own plain Request asking for the body counts.
        if type(self) is Request:
            listCalls.append(self.url.path)
        return fnRealStream(self)

    monkeypatch.setattr(Request, "stream", fiterRecordingStream)
    return listCalls


def testARequestWithoutTheLeaseIsRefusedAndItsBodyIsNeverRead(
    tclientUploads, listStreamCalls,
):
    """Authentication and ownership come before a single body byte.

    A regression test of the end-to-end property, not kill-confirmed
    here: the refusal is made by the route class
    (``routeScope.ContainerAwareRoute``) before the handler exists, so no
    mutation of this module removes it. The route class's own guard is
    pinned where it lives (``tests/testSecurityBoundaryInvariants.py``,
    ``tests/testLifecycleRouteAuthority.py``).
    """
    client, connectionDocker = tclientUploads
    response = _fresponsePut(
        client, _fdictDrop(), b"payload",
        dictHeaders={"X-Vaibify-Lease": "not-the-lease"})
    assert response.status_code == 403, response.text
    assert listStreamCalls == []
    assert "/workspace/data.csv" not in connectionDocker._dictFiles


def testTheAgentLaneIsRefusedOnTheWiderRoute(tclientUploads, listStreamCalls):
    """The in-container agent keeps its repository-confined route.

    Refused twice, so no single mutation here kills it: the catalog
    declares the route not agent-safe (enforced by the middleware) and
    the handler rejects the agent token lane itself, as the workspace
    seed route does. The agent's token is the real per-container one, so
    this is the lane an agent would actually arrive on.
    """
    client, connectionDocker = tclientUploads
    sAgentToken = next(iter(
        client.app.state.dictContainerOwners.values())).sAgentToken
    response = _fresponsePut(
        client, _fdictDrop(), b"payload",
        dictHeaders={"X-Vaibify-Session": sAgentToken})
    assert response.status_code == 403, response.text
    assert listStreamCalls == []
    assert connectionDocker.listStreamedWrites == []


def testTheOldRouteStillConfinesAnUploadToTheRepository(tclientUploads):
    """The base64 route is unchanged; only the new one has the wider root."""
    client, connectionDocker = tclientUploads
    response = client.post(
        f"/api/files/{S_CONTAINER_ID}/upload",
        json={"sFilename": "x.csv", "sDestination": "/workspace/..",
              "sContentBase64": "eA=="})
    assert response.status_code == 403, response.text


@pytest.mark.falsification
@pytest.mark.parametrize("dictQuery, iStatus", [
    (_fdictDrop(sDestination="/workspace/repo/.git/hooks",
                sFilename="pre-commit"), 403),
    (_fdictDrop(sRelativePath=".git/hooks"), 403),
    (_fdictDrop(sRelativePath="a/.vaibify/b"), 403),
    (_fdictDrop(sFilename="project.json"), 403),
    (_fdictDrop(sDestination="/etc"), 403),
    (_fdictDrop(sDestination="/workspace/../etc"), 403),
    (_fdictDrop(sRelativePath="../escape"), 400),
    (_fdictDrop(sRelativePath="/abs"), 400),
    (_fdictDrop(sRelativePath="a//b"), 400),
    (_fdictDrop(sFilename=".."), 400),
    (_fdictDrop(sFilename="a/b"), 400),
    (_fdictDrop(sFilename="bad\nname"), 400),
])
def testHostileTargetsAreRefusedBeforeAnyByteIsRead(
    tclientUploads, listStreamCalls, dictQuery, iStatus,
):
    """Kills: dropping the lexical checks, the root check or the denylist."""
    client, connectionDocker = tclientUploads
    response = _fresponsePut(client, dictQuery, b"payload")
    assert response.status_code == iStatus, response.text
    assert listStreamCalls == []
    assert connectionDocker.listStreamedWrites == []


def testTheRefusalNamesTheWritableRootNotAPathTraversal(tclientUploads):
    client, _ = tclientUploads
    response = _fresponsePut(
        client, _fdictDrop(sDestination="/etc"), b"payload")
    assert "writable root /workspace" in response.text
    assert "traversal" not in response.text.lower()


@pytest.mark.falsification
def testAnUnconfirmedOverwriteIsRefusedAndTheOldBytesStay(
    tclientUploads, fixtureSpoolUnderATemporaryHome,
):
    """Kills: ignoring ``bReplaceAllowed`` (replacing without being told to)."""
    client, connectionDocker = tclientUploads
    connectionDocker._dictFiles["/workspace/data.csv"] = b"old"
    response = _fresponsePut(client, _fdictDrop(), b"new")
    assert response.status_code == 409, response.text
    assert "already exists" in response.text
    assert connectionDocker._dictFiles["/workspace/data.csv"] == b"old"
    assert _flistSpoolFiles(fixtureSpoolUnderATemporaryHome) == []


def testAConfirmedOverwriteReplaces(tclientUploads):
    client, connectionDocker = tclientUploads
    connectionDocker._dictFiles["/workspace/data.csv"] = b"old"
    response = _fresponsePut(
        client, _fdictDrop(bReplaceAllowed="true"), b"new")
    assert response.status_code == 200, response.text
    assert connectionDocker._dictFiles["/workspace/data.csv"] == b"new"


@pytest.mark.falsification
@pytest.mark.parametrize("iDeclared, baBody", [
    (10, b"short"),
    (3, b"much too long"),
])
def testABodyThatDisagreesWithItsDeclaredSizeWritesNothing(
    tclientUploads, fixtureSpoolUnderATemporaryHome, iDeclared, baBody,
):
    """Kills: skipping the length check between spool and commit."""
    client, connectionDocker = tclientUploads
    response = client.put(
        S_STREAM_URL, params={**_fdictDrop(), "iSizeBytes": iDeclared},
        content=baBody)
    assert response.status_code == 400, response.text
    assert connectionDocker.listStreamedWrites == []
    assert _flistSpoolFiles(fixtureSpoolUnderATemporaryHome) == []


def testANegativeDeclaredSizeIsRefused(tclientUploads):
    client, _ = tclientUploads
    response = client.put(
        S_STREAM_URL, params={**_fdictDrop(), "iSizeBytes": -1}, content=b"")
    assert response.status_code == 400, response.text


@pytest.mark.falsification
def testNoSpaceIsRefusedUpfrontNamingTheShortDisk(
    tclientUploads, listStreamCalls,
):
    """The preflight names the container's disk and reads no body.

    Kills: skipping the free-space preflight for a large file.
    """
    client, connectionDocker = tclientUploads
    connectionDocker.dictUsage = {"iFreeBytes": 1 * I_MEBIBYTE}
    response = client.put(
        S_STREAM_URL, params={
            **_fdictDrop("huge.bin"), "iSizeBytes": 50 * I_MEBIBYTE},
        content=b"x")
    assert response.status_code == 507, response.text
    assert "container's disk" in response.text
    assert "Docker" in response.text
    assert listStreamCalls == []


def testASmallFileDoesNotCostAFreeSpaceRoundTrip(tclientUploads):
    client, connectionDocker = tclientUploads
    connectionDocker.dictUsage = {"iFreeBytes": 0}
    assert _fresponsePut(client, _fdictDrop(), b"tiny").status_code == 200


@pytest.mark.falsification
def testAFailedWriteLeavesNoSpoolBehind(
    tclientUploads, fixtureSpoolUnderATemporaryHome,
):
    """Kills: deleting the spool only on success."""
    client, connectionDocker = tclientUploads
    connectionDocker.errorOnWrite = OSError("the container went away")
    response = _fresponsePut(client, _fdictDrop(), b"payload")
    assert response.status_code == 500, response.text
    assert _flistSpoolFiles(fixtureSpoolUnderATemporaryHome) == []


@pytest.mark.falsification
def testAFullContainerDiskAnswers507AndTheContainerStaysUsable(
    tclientUploads,
):
    """A full disk is a known state, not a reason to quarantine.

    Kills: letting the 507 propagate out of the carrier worker, which
    poisons the journal record and takes the container out of service.
    """
    client, connectionDocker = tclientUploads
    connectionDocker.errorOnWrite = OSError(errno.ENOSPC, "no space")
    response = _fresponsePut(client, _fdictDrop(), b"payload")
    assert response.status_code == 507, response.text
    connectionDocker.errorOnWrite = None
    response = _fresponsePut(client, _fdictDrop("second.csv"), b"ok")
    assert response.status_code == 200, response.text


def testOwnershipLostBetweenSpoolAndCommitWritesNothing(
    tclientUploads, monkeypatch,
):
    """The request is bound to its owner AGAIN at the commit point.

    The spool can take minutes. The container's claim is released after
    the last byte arrives and before the copy begins.

    A regression test, deliberately NOT kill-confirmed: the early and the
    late binding are the same call, so no one-line mutant removes only
    the late one, and the carrier's own staleness check is a second
    guard behind it. Its worth is that the refusal is observed whole.
    """
    client, connectionDocker = tclientUploads
    fnRealSpool = uploadStaging.fdictSpoolBody

    async def fdictSpoolThenLoseTheClaim(iterBody, iSizeBytes):
        dictSpool = await fnRealSpool(iterBody, iSizeBytes)
        client.app.state.dictContainerOwners.clear()
        return dictSpool

    monkeypatch.setattr(
        uploadStaging, "fdictSpoolBody", fdictSpoolThenLoseTheClaim)
    response = _fresponsePut(client, _fdictDrop(), b"payload")
    assert response.status_code in (403, 409), response.text
    assert connectionDocker.listStreamedWrites == []


@pytest.mark.falsification
def testAFileThatChangedWhileTheUploadQueuedIsNotReplaced(tclientUploads):
    """The prior hash is re-read inside the carrier; a change refuses.

    Kills: removing the re-verification, which would replace a file the
    journal record does not describe.
    """
    client, connectionDocker = tclientUploads
    connectionDocker._dictFiles["/workspace/data.csv"] = b"old"
    connectionDocker.listPriorHashes = [
        hashlib.sha256(b"old").hexdigest(),
        hashlib.sha256(b"someone else's edit").hexdigest(),
    ]
    response = _fresponsePut(
        client, _fdictDrop(bReplaceAllowed="true"), b"new")
    assert response.status_code == 409, response.text
    assert "changed while" in response.text
    assert connectionDocker._dictFiles["/workspace/data.csv"] == b"old"


# ---------------------------------------------------------------------
# The verdict the panel renders
# ---------------------------------------------------------------------


def testTheVerdictAllowsTheWorkspaceRootAndTheRepositories(tclientUploads):
    client, _ = tclientUploads
    for sDirectory in ("/workspace", "/workspace/repo/data"):
        response = client.get(S_VERDICT_URL, params={"sDirectory": sDirectory})
        assert response.status_code == 200, response.text
        assert response.json()["bUploadAllowed"] is True
        assert response.json()["sWritableRoot"] == S_WORKSPACE


@pytest.mark.falsification
@pytest.mark.parametrize("sDirectory", [
    "/workspace/repo/.git", "/workspace/repo/.git/hooks",
    "/workspace/.vaibify", "/etc", "/",
])
def testTheVerdictRefusesWhatTheUploadWouldRefuseWithItsReason(
    tclientUploads, sDirectory,
):
    """One rule, two callers: the verdict and the PUT cannot disagree.

    Kills: giving the verdict its own copy of the rule.
    """
    client, connectionDocker = tclientUploads
    response = client.get(S_VERDICT_URL, params={"sDirectory": sDirectory})
    dictVerdict = response.json()
    assert dictVerdict["bUploadAllowed"] is False
    assert dictVerdict["sUploadRefusal"]
    dictDrop = _fdictDrop(sDestination=sDirectory)
    responsePut = _fresponsePut(client, dictDrop, b"x")
    assert responsePut.status_code == 403
    assert responsePut.json()["detail"] == dictVerdict["sUploadRefusal"]


def testTheVerdictCanAskWhetherTheBatchWillFit(tclientUploads):
    client, connectionDocker = tclientUploads
    connectionDocker.dictUsage = {"iFreeBytes": 10 * I_MEBIBYTE}
    response = client.get(S_VERDICT_URL, params={
        "sDirectory": S_WORKSPACE, "iTotalBytes": 500 * I_MEBIBYTE})
    dictVerdict = response.json()
    assert dictVerdict["bUploadAllowed"] is False
    assert "container's disk" in dictVerdict["sUploadRefusal"]
    response = client.get(S_VERDICT_URL, params={
        "sDirectory": S_WORKSPACE, "iTotalBytes": 1 * I_MEBIBYTE})
    assert response.json()["bUploadAllowed"] is True


# ---------------------------------------------------------------------
# The spool sweep
# ---------------------------------------------------------------------


def testTheSweepRemovesASpoolWhoseProcessIsGoneAndKeepsALiveOne(
    fixtureSpoolUnderATemporaryHome,
):
    dictLive = uploadStaging.fdictOpenSpool()
    sDirectory = os.path.dirname(dictLive["sPath"])
    sDead = os.path.join(sDirectory, "upload-999999999-deadbeef.part")
    with open(sDead, "wb") as fileDead:
        fileDead.write(b"x")
    try:
        assert uploadStaging.fiSweepAbandonedSpools() == 1
        assert os.path.exists(dictLive["sPath"])
        assert not os.path.exists(sDead)
    finally:
        uploadStaging.fnDiscardSpool(dictLive)


@pytest.mark.falsification
def testTheHubSweepsAbandonedSpoolsBeforeItServes(
    fixtureSpoolUnderATemporaryHome,
):
    """A hub that starts finds the previous hub's crashed spool gone.

    Kills: not registering the sweep, which leaves a crashed upload's
    bytes on disk until a researcher notices the space.
    """
    from vaibify.gui import appFactory
    sDirectory = os.path.join(
        fixtureSpoolUnderATemporaryHome, ".vaibify", "tmp", "uploads")
    os.makedirs(sDirectory)
    sDead = os.path.join(sDirectory, "upload-999999999-abc123.part")
    with open(sDead, "wb") as fileDead:
        fileDead.write(b"x")
    app = appFactory.fappCreateHubApplication()
    asyncio.run(_fnRunStartupHooks(app))
    assert not os.path.exists(sDead)


async def _fnRunStartupHooks(app):
    for fnStartup in list(app.state.listLifespanStartup):
        objResult = fnStartup(app)
        if asyncio.iscoroutine(objResult):
            await objResult


@pytest.mark.falsification
@pytest.mark.parametrize("sFilename", ["a/b", "x\x00y", "tab\there", ""])
def testAFileNameThatIsNotOnePathComponentIsRefused(
    tclientUploads, sFilename,
):
    """A name is one component: no slash, no control character, not empty.

    Kills: dropping the slash test from the name check, which would let a
    "name" carry a path of its own past the lexical rules.
    """
    client, connectionDocker = tclientUploads
    response = _fresponsePut(client, _fdictDrop(sFilename=sFilename), b"x")
    assert response.status_code == 400, response.text
    assert connectionDocker.listStreamedWrites == []


@pytest.mark.falsification
def testAWriteTheContainerRefusesIsA403ThatSaysWhy(tclientUploads):
    """The writer's refusal reaches the researcher as a 403 with its reason.

    Kills: letting a refusal fall through to the generic failure, which
    would quarantine the container for a path that was merely not allowed.
    """
    from vaibify.docker.confinedWrite import ContainerWriteRefusedError
    client, connectionDocker = tclientUploads
    connectionDocker.errorOnWrite = ContainerWriteRefusedError(
        "refused: 'linked' is a symlink or not a directory")
    response = _fresponsePut(client, _fdictDrop(), b"payload")
    assert response.status_code == 403, response.text
    assert "symlink" in response.text
    connectionDocker.errorOnWrite = None
    assert _fresponsePut(
        client, _fdictDrop("again.csv"), b"ok").status_code == 200
