"""The shadow-container rerun lane, and what it must refuse.

Tier 5 used to re-run the workflow inside the researcher's own project
container. Two things were wrong with that and only one was about
safety: it overwrote their real outputs, and — the reason this lane
exists — it exercised whatever the project container had become rather
than the image ``environment.json`` pins, so it could pass where
``reproduce.sh`` would fail.

Every test here is written to FALSIFY one of the properties that
replaced it, not to confirm the happy path. The comparison must be
rooted on the shadow (asserted with paths made distinct, so a lane that
quietly kept the live root cannot pass); the image must come from the
pin (asserted with an image reference the live container does not
have); a missing pin must refuse before any container exists; and a
shadow the daemon cannot prove destroyed must be REPORTED, because a
silent teardown fault reads as a clean run over a container still on
the researcher's machine.
"""

import io
import posixpath
import tarfile

import pytest

from vaibify.docker import coherentExport
from vaibify.docker import disposableSpecification
from vaibify.reproducibility import shadowRerun


S_LIVE_REPO = "/workspace/liveRepo"
S_LIVE_WORKFLOW = "/workspace/liveRepo/analysis/project.json"
S_PINNED_IMAGE = "registry.example/pinned@sha256:" + "ab" * 32


class _FakeDockerContainer:
    """The minimum of docker-py's container object this lane touches."""

    def __init__(self, sIdentifier, sName):
        self.id = sIdentifier
        self.name = sName

    def start(self):
        """Accept the start the gateway performs after create."""


class _FakeDockerApi:
    """Records the daemon calls; answers absence after a removal."""

    def __init__(self, dictState):
        self._dictState = dictState

    def inspect_container(self, sContainerId):
        if sContainerId in self._dictState["setRemoved"]:
            raise _FakeNotFound(sContainerId)
        return {"Config": {"Labels": self._dictState["dictLabels"]}}

    def remove_container(self, sContainerId, force=False, v=False):
        del force, v
        if self._dictState["bRemovalFails"]:
            raise RuntimeError("daemon refused the removal")
        self._dictState["setRemoved"].add(sContainerId)

    def put_archive(self, sContainerId, sPath, baArchive):
        self._dictState["listCopies"].append(
            (sContainerId, sPath, len(baArchive)))
        return True


class _FakeNotFound(Exception):
    """Stands in for ``docker.errors.NotFound``."""


class _FakeContainerCollection:
    def __init__(self, dictState):
        self._dictState = dictState

    def create(self, sImageReference, **dictKeywords):
        self._dictState["listCreated"].append(
            (sImageReference, dictKeywords))
        self._dictState["dictLabels"] = dictKeywords["labels"]
        return _FakeDockerContainer(
            "shadowContainerId", dictKeywords["name"])


class _FakeDockerClient:
    def __init__(self, dictState):
        self.api = _FakeDockerApi(dictState)
        self.containers = _FakeContainerCollection(dictState)


class _FakeConnection:
    """A ``DockerConnection`` stand-in for the LIVE project container.

    It answers with a REAL tar archive and a git-identity observation
    derived from the same file dict, so ``coherentExport`` genuinely
    runs against it. A stand-in that returned canned bytes and a canned
    observation would agree with itself by construction and the
    coherence check would be exercised by nothing -- which is how a
    permissive double comes to hide a fail-closed adapter.

    Two hooks model the two ways an export tears, and they are
    deliberately separate because each is caught by a different half of
    the check:

    * ``fnWriteDuringCopy`` changes the repository after the first
      observation, so the two observations disagree;
    * ``dictArchiveOnlyContent`` changes only what the ARCHIVE holds,
      modelling a file rewritten and rewritten back -- the observations
      agree perfectly and only the archived bytes contradict them.
    """

    def __init__(self, dictFiles=None):
        self.listArchiveReads = []
        self.listObservedAt = []
        self.dictFiles = dict(dictFiles or {
            "alpha.txt": b"alpha\n",
            "sub/beta.txt": b"beta\n",
        })
        self.fnWriteDuringCopy = None
        self.dictArchiveOnlyContent = {}
        # The lock-satisfaction gate reads the SHADOW's files and runs
        # one pip enumeration inside it. Keyed by absolute container
        # path; empty means "no lock in the shadow", which the gate
        # deliberately skips (lock PRESENCE is the readiness gate's
        # question). sPipFreezeOutput is the enumeration's stdout.
        self.dictShadowFiles = {}
        self.sPipFreezeOutput = ""
        self.iPipExitCode = 0

    def fbContainerPathIsFile(self, sContainerId, sAbsPath):
        del sContainerId
        return sAbsPath in self.dictShadowFiles

    def fbaFetchFile(self, sContainerId, sAbsPath):
        del sContainerId
        return self.dictShadowFiles[sAbsPath]

    def ftRunInContainerStreamed(self, sContainerId, sCommand,
                                 sWorkdir=None, sUser=None):
        del sContainerId, sCommand, sWorkdir, sUser
        import types
        return types.SimpleNamespace(
            iExitCode=self.iPipExitCode,
            sStdout=self.sPipFreezeOutput, sStderr="",
        )

    def fbaFetchDirectoryArchive(self, sContainerId, sPath, iMaxBytes):
        self.listArchiveReads.append((sContainerId, sPath, iMaxBytes))
        dictContent = dict(self.dictFiles)
        dictContent.update(self.dictArchiveOnlyContent)
        if self.fnWriteDuringCopy is not None:
            self.fnWriteDuringCopy(self)
        return _fbaBuildRepositoryArchive(
            posixpath.basename(sPath.rstrip("/")), dictContent)

    def fdictFetchWorktreeIdentities(self, sContainerId, sRepoPath):
        del sContainerId, sRepoPath
        self.listObservedAt.append(dict(self.dictFiles))
        return {
            "bSuccess": True,
            "sReason": "",
            "sHeadSha": "0" * 40,
            "sPorcelainDigest": coherentExport.fsComputeGitBlobIdentity(
                repr(sorted(self.dictFiles)).encode()),
            "listIgnoredPaths": [],
            "dictPathIdentities": {
                sName: {
                    "sType": "file",
                    "sIdentity": coherentExport.fsComputeGitBlobIdentity(
                        baContent),
                }
                for sName, baContent in self.dictFiles.items()
            },
        }

    def fdictReadDaemonCapacity(self):
        return {"iMemoryBytes": 0, "iCpuCount": 0}


def _fbaBuildRepositoryArchive(sBasename, dictContent):
    """Build a tar the way ``get_archive`` names one: parent-relative."""
    bufferTar = io.BytesIO()
    with tarfile.open(fileobj=bufferTar, mode="w") as fileTar:
        for sName, baContent in sorted(dictContent.items()):
            infoMember = tarfile.TarInfo(
                name=posixpath.join(sBasename, sName))
            infoMember.size = len(baContent)
            fileTar.addfile(infoMember, io.BytesIO(baContent))
    return bufferTar.getvalue()


@pytest.fixture
def dictHarness(monkeypatch, tmp_path):
    """Wire the real gateway to a fake daemon and a fake repack.

    The repack is stubbed because it parses real tar bytes and this
    file is about the LANE, not the archive; the archive's own
    correctness is asserted against a real daemon in
    ``testDisposableContainerLive.py``. Everything else — the gateway,
    the reservation ledger, the identity-verified destroy — is the real
    code. ``HOME`` is redirected because the lane holds a real flock
    under ``~/.vaibify/locks`` for its whole lifecycle, and a test must
    never contend with (or leave files in) the researcher's own home.
    """
    import io

    monkeypatch.setenv("HOME", str(tmp_path))
    dictState = {
        "listCreated": [], "listCopies": [], "setRemoved": set(),
        "dictLabels": {}, "bRemovalFails": False,
    }
    monkeypatch.setattr(
        shadowRerun.disposableContainer, "fdockerCreateDisposableClient",
        lambda: _FakeDockerClient(dictState))
    monkeypatch.setattr(
        shadowRerun.disposableContainer, "_fmoduleGetDocker",
        lambda: type("_M", (), {
            "errors": type("_E", (), {"NotFound": _FakeNotFound})})())
    monkeypatch.setattr(
        disposableSpecification, "fbufferRepackArchiveStamped",
        lambda baArchive, sPrefix="": io.BytesIO(baArchive))
    return dictState


def _fdictWorkflow():
    return {"sProjectRepoPath": S_LIVE_REPO, "listSteps": [{"sName": "A"}]}


def _fdictEnvironment():
    return {"dictContainer": {"sImageDigest": S_PINNED_IMAGE}}


def _fnRecordingComparison(listCalls, dictOutcome=None):
    """Return a comparison stub that records how it was invoked."""
    def fdictCompare(connection, sContainerId, dictWorkflow, sWorkflowPath,
                     filesRepo, fnStatusCallback=None):
        del connection, dictWorkflow, fnStatusCallback
        listCalls.append({
            "sContainerId": sContainerId,
            "sWorkflowPath": sWorkflowPath,
            "sRepoRoot": filesRepo.sRootPath,
            "sFilesContainerId": filesRepo.sContainerId,
        })
        return dict(dictOutcome or {
            "bPassed": True, "iOutputHashesMatched": 3,
            "iOutputHashesTotal": 3, "listDivergedHashes": [],
        })
    return fdictCompare


@pytest.mark.falsification
def testTheComparisonIsRootedOnTheShadowNeverOnTheLiveRepository(
    dictHarness,
):
    """The re-hash must read the tree the rerun WROTE, not the original.

    The keys are made distinct on purpose: the live repo is
    ``/workspace/liveRepo`` and the shadow copy is ``/shadow/liveRepo``,
    so a lane that passed the live adapter through would satisfy every
    other assertion here and fail only this one. That substitution is
    exactly how a verification comes to grade a tree the rerun never
    touched and pass unconditionally.
    
    Kills: rooting the post-rerun ContainerRepoFiles on the live
    repository path instead of the shadow copy's.
    """
    listCalls = []
    connectionLive = _FakeConnection()
    dictOutcome = shadowRerun.fdictRerunInShadowContainer(
        connectionLive, "liveContainer", _fdictWorkflow(), S_LIVE_WORKFLOW,
        S_LIVE_REPO, _fdictEnvironment(),
        fdictRunAndVerify=_fnRecordingComparison(listCalls),
    )
    assert len(listCalls) == 1
    assert listCalls[0]["sRepoRoot"] == "/shadow/liveRepo"
    assert listCalls[0]["sWorkflowPath"] == (
        "/shadow/liveRepo/analysis/project.json"
    )
    assert listCalls[0]["sFilesContainerId"] != "liveContainer", (
        "the comparison adapter still points at the live container"
    )
    assert listCalls[0]["sFilesContainerId"] == listCalls[0]["sContainerId"]
    assert dictOutcome["bShadowContainerUsed"] is True
    assert dictOutcome["bPassed"] is True


def testTheShadowIsBuiltFromThePinNotFromTheLiveContainersImage(
    dictHarness,
):
    """The digest in environment.json is the only image source.

    A shadow built from the live container's image would exercise
    packages installed during a debugging session and files left by an
    interactive step — the very drift this lane exists to exclude — and
    would look identical in every other observable.
    """
    shadowRerun.fdictRerunInShadowContainer(
        _FakeConnection(), "liveContainer", _fdictWorkflow(),
        S_LIVE_WORKFLOW, S_LIVE_REPO, _fdictEnvironment(),
        fdictRunAndVerify=_fnRecordingComparison([]),
    )
    assert len(dictHarness["listCreated"]) == 1
    sImageUsed, dictKeywords = dictHarness["listCreated"][0]
    assert sImageUsed == S_PINNED_IMAGE
    assert dictKeywords["network_mode"] == "none"
    assert dictKeywords["cap_drop"] == ["ALL"]
    assert dictKeywords["user"] == "1000:1000"


@pytest.mark.falsification
def testAnUnpinnedEnvironmentRefusesBeforeAnyContainerExists(dictHarness):
    """No digest means no shadow, and no fallback to anything else.

    Falling back to the live container's image is the tempting
    behaviour and would silently reintroduce the defect this lane
    removes, so the refusal must happen before the daemon is touched at
    all.
    
    Kills: answering an unpinned environment.json with a fallback
    image reference instead of raising ShadowRerunRefusedError.
    """
    for dictPayload in (None, {}, {"dictContainer": {}},
                        {"sImageDigest": ""}):
        with pytest.raises(shadowRerun.ShadowRerunRefusedError,
                           match="pins no container image digest"):
            shadowRerun.fdictRerunInShadowContainer(
                _FakeConnection(), "liveContainer", _fdictWorkflow(),
                S_LIVE_WORKFLOW, S_LIVE_REPO, dictPayload,
                fdictRunAndVerify=_fnRecordingComparison([]),
            )
    assert dictHarness["listCreated"] == [], (
        "a refusal created a container anyway"
    )


def testAWorkflowOutsideItsRepositoryIsRefused(dictHarness):
    """A workflow the shadow copy would not contain must not be run.

    The rerun would otherwise start in a container missing the very file
    it was told to execute, and the failure would read as a broken
    workflow rather than a mis-scoped copy.
    """
    with pytest.raises(shadowRerun.ShadowRerunRefusedError,
                       match="lies outside its project repository"):
        shadowRerun.fdictRerunInShadowContainer(
            _FakeConnection(), "liveContainer", _fdictWorkflow(),
            "/workspace/otherRepo/project.json", S_LIVE_REPO,
            _fdictEnvironment(),
            fdictRunAndVerify=_fnRecordingComparison([]),
        )
    assert dictHarness["listCreated"] == []


def testAShadowThatCannotBeProvenGoneIsReportedNotAbsorbed(dictHarness):
    """An unproven teardown must reach the caller's outcome.

    Silence here would report a clean, passing reproduction over a
    container still running on the researcher's daemon — the same class
    of dishonesty as a dashboard that hides an error. The verdict itself
    is untouched: the comparison did happen, and its result is not made
    worse by a cleanup fault.
    """
    dictHarness["bRemovalFails"] = True
    dictOutcome = shadowRerun.fdictRerunInShadowContainer(
        _FakeConnection(), "liveContainer", _fdictWorkflow(),
        S_LIVE_WORKFLOW, S_LIVE_REPO, _fdictEnvironment(),
        fdictRunAndVerify=_fnRecordingComparison([]),
    )
    assert dictOutcome["sShadowTeardown"] == (
        disposableSpecification.S_OUTCOME_QUARANTINED
    )
    assert "may still be running" in dictOutcome["sShadowTeardownReason"]
    assert dictOutcome["bPassed"] is True


@pytest.mark.falsification
def testAComparisonThatRaisesStillTearsTheShadowDown(dictHarness):
    """A failed rerun must not leak the container it ran in.

    The teardown sits in ``finally`` for this case alone: without it,
    every workflow that errors mid-rerun leaves a container behind, and
    the researcher's daemon accumulates them invisibly.
    
    Kills: moving the teardown out of ``finally``, so a rerun that
    raises leaks its container onto the researcher's daemon.
    """
    def fdictExplode(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("the rerun blew up")

    with pytest.raises(RuntimeError, match="the rerun blew up"):
        shadowRerun.fdictRerunInShadowContainer(
            _FakeConnection(), "liveContainer", _fdictWorkflow(),
            S_LIVE_WORKFLOW, S_LIVE_REPO, _fdictEnvironment(),
            fdictRunAndVerify=fdictExplode,
        )
    assert dictHarness["setRemoved"] == {"shadowContainerId"}


def testTheArchiveIsReadFromTheLiveRepositoryUnderAHostBound(dictHarness):
    """The repository copy is bounded, and bounded by the HOST's memory.

    The archive is materialised in the hub's own address space, so an
    unbounded read is a swap storm on the researcher's machine rather
    than a container problem. The bound must be positive and must be
    passed — an omitted argument would be indistinguishable from a
    generous one at every other assertion.
    """
    connectionLive = _FakeConnection()
    shadowRerun.fdictRerunInShadowContainer(
        connectionLive, "liveContainer", _fdictWorkflow(), S_LIVE_WORKFLOW,
        S_LIVE_REPO, _fdictEnvironment(),
        fdictRunAndVerify=_fnRecordingComparison([]),
    )
    assert len(connectionLive.listArchiveReads) == 1
    sContainerId, sPath, iMaxBytes = connectionLive.listArchiveReads[0]
    assert (sContainerId, sPath) == ("liveContainer", S_LIVE_REPO)
    assert iMaxBytes > 0
    assert dictHarness["listCopies"][0][1] == "/"


def testTheShadowPathResolverKeepsTheWorkflowWhereTheStepsExpectIt():
    """Step directories are repo-relative, so the root must move as one.

    Resolving the workflow to the shadow root while leaving it at the
    repository top level would place every step directory correctly for
    a workflow at the root and incorrectly for one in a subdirectory —
    a bug that only appears for the second kind of project.
    """
    tPaths = shadowRerun.ftResolveShadowPaths(
        "/workspace/myRepo", "/workspace/myRepo/nested/deep/project.json",
    )
    assert tPaths == (
        "shadow", "/shadow/myRepo",
        "/shadow/myRepo/nested/deep/project.json",
    )
    for sRepoPath in ("", "relative/path", "/"):
        with pytest.raises(shadowRerun.ShadowRerunRefusedError):
            shadowRerun.ftResolveShadowPaths(sRepoPath, "x/project.json")


def testACrashedRerunsShadowIsSweptBeforeANewOneStarts(
    dictHarness, monkeypatch,
):
    """The lifecycle's ``finally`` cannot cover the hub dying mid-rerun.

    A crash there leaves a labeled container running on the
    researcher's daemon with nothing left in memory to remember it. The
    sweep is the only thing that reclaims it, and it must be narrowed
    to THIS project's stamp: a daemon-wide sweep would destroy a live
    peer hub's work, which is a mistake this repository has already made
    once.
    """
    listSwept = []
    listSurvivors = [
        {"sContainerId": "mineFromACrash", "sContainerName": "mine",
         "sReservationId": "r1", "sRole": "shadow",
         "sResourceName": "liveContainer", "sStatus": "running"},
        {"sContainerId": "aPeersLiveShadow", "sContainerName": "peer",
         "sReservationId": "r2", "sRole": "shadow",
         "sResourceName": "someOtherHubsContainer", "sStatus": "running"},
        {"sContainerId": "unattributable", "sContainerName": "orphan",
         "sReservationId": "r3", "sRole": "shadow",
         "sResourceName": "", "sStatus": "exited"},
    ]
    monkeypatch.setattr(
        shadowRerun.disposableContainer, "flistDiscoverLabeledContainers",
        lambda dockerDisposable: listSurvivors)
    monkeypatch.setattr(
        shadowRerun.disposableContainer,
        "fdictDestroyContainerAndProveAbsence",
        lambda dockerDisposable, sContainerId: (
            listSwept.append(sContainerId)
            or {"sOutcome": disposableSpecification.S_OUTCOME_DESTROYED,
                "sReason": "", "dictProbe": {}}))
    shadowRerun.fdictRerunInShadowContainer(
        _FakeConnection(), "liveContainer", _fdictWorkflow(),
        S_LIVE_WORKFLOW, S_LIVE_REPO, _fdictEnvironment(),
        sResourceName="liveContainer",
        fdictRunAndVerify=_fnRecordingComparison([]),
    )
    assert "mineFromACrash" in listSwept
    assert "unattributable" in listSwept, (
        "an unstamped survivor is exactly the leak the sweep exists for"
    )
    assert "aPeersLiveShadow" not in listSwept, (
        "the sweep destroyed a container stamped for a different "
        "project container -- on a shared daemon that is a live peer "
        "hub's work"
    )


def testASweepThatCannotReachTheDaemonDoesNotBlockTheRerun(
    dictHarness, monkeypatch,
):
    """A cleanup fault must not deny the researcher the run they asked for.

    The survivor it failed to reclaim is still visible on the next
    attempt, so nothing is lost by proceeding; refusing would trade a
    reproducible attestation for a tidy daemon.
    """
    def fnExplode(dockerDisposable):
        del dockerDisposable
        raise RuntimeError("the daemon did not answer the label query")

    monkeypatch.setattr(
        shadowRerun.disposableContainer, "flistDiscoverLabeledContainers",
        fnExplode)
    dictOutcome = shadowRerun.fdictRerunInShadowContainer(
        _FakeConnection(), "liveContainer", _fdictWorkflow(),
        S_LIVE_WORKFLOW, S_LIVE_REPO, _fdictEnvironment(),
        sResourceName="liveContainer",
        fdictRunAndVerify=_fnRecordingComparison([]),
    )
    assert dictOutcome["bPassed"] is True
    assert dictOutcome["bShadowContainerUsed"] is True


@pytest.mark.falsification
def testARepositoryThatMovesDuringTheCopyIsRefused(dictHarness):
    """A write during the copy must refuse, and name the file.

    This is the case the whole coherence check exists for. Without it
    the shadow receives a mixture of two moments, the rerun computes
    from half-stale inputs, the hashes diverge, and the researcher is
    told their workflow is non-reproducible -- a baffling failure with
    no path to the real cause. The refusal is the difference between
    that and one sentence naming the file.

    Kills: dropping the before/after observation comparison from
    coherentExport.fbaExportRepositoryCoherently.
    """
    connectionLive = _FakeConnection()

    def fnRewriteMidCopy(connection):
        connection.dictFiles["alpha.txt"] = b"rewritten mid-copy\n"

    connectionLive.fnWriteDuringCopy = fnRewriteMidCopy
    with pytest.raises(coherentExport.ExportTornError) as errorRaised:
        shadowRerun.fdictRerunInShadowContainer(
            connectionLive, "liveContainer", _fdictWorkflow(),
            S_LIVE_WORKFLOW, S_LIVE_REPO, _fdictEnvironment(),
            fdictRunAndVerify=_fnRecordingComparison([]),
        )
    assert "alpha.txt" in str(errorRaised.value)
    assert "changed while it was being copied" in str(errorRaised.value)
    assert dictHarness["listCreated"] == [], (
        "a torn export still created a shadow container"
    )


@pytest.mark.falsification
def testAFileRewrittenAndRestoredDuringTheCopyIsStillRefused(dictHarness):
    """The observations agree; only the archived BYTES disagree.

    The case that proves the two halves of the check are not redundant.
    A file changed mid-stream and changed back leaves HEAD, the
    porcelain digest and every path identity identical before and
    after -- the observation comparison sees a perfectly quiet
    repository -- while the archive holds the intermediate bytes. Only
    re-deriving each member's identity from the archived bytes
    contradicts it.

    Kills: dropping the per-member archive check from
    coherentExport._fnRefuseArchiveMismatch.
    """
    connectionLive = _FakeConnection()
    connectionLive.dictArchiveOnlyContent = {
        "sub/beta.txt": b"caught mid-write\n",
    }
    with pytest.raises(coherentExport.ExportTornError) as errorRaised:
        shadowRerun.fdictRerunInShadowContainer(
            connectionLive, "liveContainer", _fdictWorkflow(),
            S_LIVE_WORKFLOW, S_LIVE_REPO, _fdictEnvironment(),
            fdictRunAndVerify=_fnRecordingComparison([]),
        )
    assert "sub/beta.txt" in str(errorRaised.value)
    assert connectionLive.listObservedAt[0] == (
        connectionLive.listObservedAt[1]
    ), (
        "this test is only meaningful when the two observations AGREE; "
        "if they differ, the other guard is what refused and this one "
        "is untested"
    )
    assert dictHarness["listCreated"] == []


def testAnUnobservableRepositoryIsRefusedRatherThanAssumedQuiet(
    dictHarness,
):
    """"We could not look" must never be recorded as "nothing changed".

    The observation program answers ``bSuccess`` False for an
    enumeration fault rather than raising, so a caller that only read
    ``dictPathIdentities`` would receive an empty dict and conclude the
    repository was empty and therefore unchanged -- the exact shape of
    a fail-open coherence check.
    """
    connectionLive = _FakeConnection()
    connectionLive.fdictFetchWorktreeIdentities = (
        lambda sContainerId, sRepoPath: {
            "bSuccess": False, "sReason": "not a git work tree",
            "dictPathIdentities": {},
        }
    )
    with pytest.raises(coherentExport.ExportTornError,
                       match="could not be observed"):
        shadowRerun.fdictRerunInShadowContainer(
            connectionLive, "liveContainer", _fdictWorkflow(),
            S_LIVE_WORKFLOW, S_LIVE_REPO, _fdictEnvironment(),
            fdictRunAndVerify=_fnRecordingComparison([]),
        )
    assert dictHarness["listCreated"] == []


@pytest.mark.falsification
def testTheOutcomeNamesTheImageTheShadowWasBuiltFrom(dictHarness):
    """The attestation's image field must travel WITH the outcome.

    Both writer lanes used to re-read a recorded digest at write time
    — the CLI from the host ``--repo`` clone, a different file from
    the one the shadow lane pinned — so the attestation could name an
    image the rerun never executed under. The outcome carrying the pin
    is what makes both lanes name the container they actually built.

    Kills: In fdictRerunInShadowContainer, return the lifecycle's
    outcome without stamping dictOutcome["sImageDigest"] =
    sImageReference onto it.
    """
    dictOutcome = shadowRerun.fdictRerunInShadowContainer(
        _FakeConnection(), "liveContainer", _fdictWorkflow(),
        S_LIVE_WORKFLOW, S_LIVE_REPO, _fdictEnvironment(),
        fdictRunAndVerify=_fnRecordingComparison([]),
    )
    assert dictOutcome["sImageDigest"] == S_PINNED_IMAGE


def testAFloatingTagInThePinnedFieldIsRefused(dictHarness):
    """A tag in the digest field must refuse before any container exists.

    ``fsResolvePinnedImageReference`` checks presence AND shape: a
    payload whose digest field holds ``some-image:latest`` satisfies a
    non-empty check, and a shadow built from it attests an environment
    nobody pinned — a tag can be repointed without anything changing.
    """
    with pytest.raises(shadowRerun.ShadowRerunRefusedError) as infoRefusal:
        shadowRerun.fdictRerunInShadowContainer(
            _FakeConnection(), "liveContainer", _fdictWorkflow(),
            S_LIVE_WORKFLOW, S_LIVE_REPO,
            {"dictContainer": {"sImageDigest": "some-image:latest"}},
            fdictRunAndVerify=_fnRecordingComparison([]),
        )
    assert "tag" in str(infoRefusal.value)
    assert dictHarness["listCreated"] == [], (
        "a container was built from a floating tag"
    )


def testALocalImageIdIsAcceptedAsAContentPin():
    """A locally built image pins by its image ID — itself a digest."""
    sImageId = "sha256:" + "9a" * 32
    assert shadowRerun.fsResolvePinnedImageReference(
        {"sImageDigest": sImageId},
    ) == sImageId


@pytest.mark.falsification
def testASecondRerunOfTheSameProjectRefusesInsteadOfSweeping(
    dictHarness,
):
    """The crash-sweep cannot tell an orphan from a LIVE rerun's shadow.

    It destroys every survivor stamped with this project's name, so a
    second concurrent rerun of the same project would sweep the first's
    still-running container mid-comparison. The flock is the liveness
    signal the stamp lacks: held for the whole lifecycle, released by
    the OS if the holder dies. The nested attempt here runs INSIDE the
    first rerun's comparison — the moment a real collision would occur
    — and must refuse before its sweep destroys anything.

    Kills: In _fcontextHoldShadowLaneLock, replace the empty-name guard
    "if not sResourceName:" with "if True:", so the lock is never
    taken, the nested rerun proceeds to its sweep, and the live shadow
    is destroyed.
    """
    listRemovedAtRefusal = []

    def fdictCompareThenCollide(connection, sContainerId, dictWorkflow,
                                sWorkflowPath, filesRepo,
                                fnStatusCallback=None):
        del connection, dictWorkflow, sWorkflowPath, fnStatusCallback
        del filesRepo
        with pytest.raises(shadowRerun.ShadowRerunRefusedError):
            shadowRerun.fdictRerunInShadowContainer(
                _FakeConnection(), "liveContainer", _fdictWorkflow(),
                S_LIVE_WORKFLOW, S_LIVE_REPO, _fdictEnvironment(),
                sResourceName="liveContainer",
                fdictRunAndVerify=_fnRecordingComparison([]),
            )
        listRemovedAtRefusal.append(set(dictHarness["setRemoved"]))
        return {"bPassed": True, "iOutputHashesMatched": 1,
                "iOutputHashesTotal": 1, "listDivergedHashes": []}

    dictOutcome = shadowRerun.fdictRerunInShadowContainer(
        _FakeConnection(), "liveContainer", _fdictWorkflow(),
        S_LIVE_WORKFLOW, S_LIVE_REPO, _fdictEnvironment(),
        sResourceName="liveContainer",
        fdictRunAndVerify=fdictCompareThenCollide,
    )
    assert listRemovedAtRefusal == [set()], (
        "the nested rerun reached its sweep and destroyed the live "
        "rerun's shadow before refusing"
    )
    assert dictOutcome["bPassed"] is True


def testTheShadowLaneLockIsReleasedWhenTheRerunEnds(dictHarness):
    """A finished rerun must not leave its project locked forever."""
    for _ in range(2):
        dictOutcome = shadowRerun.fdictRerunInShadowContainer(
            _FakeConnection(), "liveContainer", _fdictWorkflow(),
            S_LIVE_WORKFLOW, S_LIVE_REPO, _fdictEnvironment(),
            sResourceName="liveContainer",
            fdictRunAndVerify=_fnRecordingComparison([]),
        )
        assert dictOutcome["bPassed"] is True


def _fnStockShadowLock(connectionFake, sLockText, sPipOutput):
    """Stock the fake shadow with a lock and a pip enumeration answer."""
    connectionFake.dictShadowFiles[
        "/shadow/liveRepo/requirements.lock"] = sLockText.encode()
    connectionFake.sPipFreezeOutput = sPipOutput


@pytest.mark.falsification
def testAnImageThatCannotHonourItsLockIsRefusedBeforeAnyStepRuns(
    dictHarness,
):
    """Ruling B (2026-09-01): hermetic shadow + lock-satisfaction gate.

    reproduce.sh installs the lock before running; the shadow,
    hermetic by design, does not. When the image already satisfies the
    lock the two procedures converge and the rerun proceeds; when it
    does not, a hermetic rerun would exercise an environment NEITHER
    procedure describes — the pytest saga's exact shape, which cost
    six diagnostic rounds arriving as a mid-workflow step failure.
    The refusal must come in seconds, by name, with the comparison
    never invoked and nothing attested.

    Kills: In _fdictCompareUnderTheShadowsOwnAdmission, drop the
    _fdictRefusalIfImageLacksLockedPackages call and proceed straight
    to the comparison.
    """
    listCalls = []
    connectionLive = _FakeConnection()
    _fnStockShadowLock(
        connectionLive,
        "numpy==1.26.4 \\\n    --hash=sha256:" + "aa" * 32 + "\n"
        "pytest==9.1.1 \\\n    --hash=sha256:" + "bb" * 32 + "\n",
        "numpy==1.26.4\n",
    )
    dictOutcome = shadowRerun.fdictRerunInShadowContainer(
        connectionLive, "liveContainer", _fdictWorkflow(),
        S_LIVE_WORKFLOW, S_LIVE_REPO, _fdictEnvironment(),
        fdictRunAndVerify=_fnRecordingComparison(listCalls),
    )
    assert dictOutcome["bRerunAttempted"] is False
    assert dictOutcome["bPassed"] is False
    sReasons = "\n".join(dictOutcome["listDivergedHashes"])
    assert "does not satisfy requirements.lock" in sReasons
    assert "pytest==9.1.1 (image has nothing)" in sReasons
    assert listCalls == [], (
        "the comparison ran against an image the lock disqualifies"
    )


def testAnImageThatSatisfiesItsLockProceedsToTheComparison(dictHarness):
    """Convergence: the install step would be a no-op, so run."""
    listCalls = []
    connectionLive = _FakeConnection()
    _fnStockShadowLock(
        connectionLive,
        "numpy==1.26.4 \\\n    --hash=sha256:" + "aa" * 32 + "\n",
        # pip spells the name differently; PyPA normalization must
        # reconcile them, and extra installed packages are fine.
        "NumPy==1.26.4\nsetuptools==70.0.0\n",
    )
    dictOutcome = shadowRerun.fdictRerunInShadowContainer(
        connectionLive, "liveContainer", _fdictWorkflow(),
        S_LIVE_WORKFLOW, S_LIVE_REPO, _fdictEnvironment(),
        fdictRunAndVerify=_fnRecordingComparison(listCalls),
    )
    assert len(listCalls) == 1
    assert dictOutcome["bPassed"] is True


def testAnUnenumerableImageRefusesRatherThanAssumingSatisfaction(
    dictHarness,
):
    """Fail-closed: satisfaction that cannot be checked is not
    satisfaction."""
    connectionLive = _FakeConnection()
    _fnStockShadowLock(
        connectionLive,
        "numpy==1.26.4 \\\n    --hash=sha256:" + "aa" * 32 + "\n",
        "",
    )
    connectionLive.iPipExitCode = 127
    dictOutcome = shadowRerun.fdictRerunInShadowContainer(
        connectionLive, "liveContainer", _fdictWorkflow(),
        S_LIVE_WORKFLOW, S_LIVE_REPO, _fdictEnvironment(),
        fdictRunAndVerify=_fnRecordingComparison([]),
    )
    assert dictOutcome["bRerunAttempted"] is False
    assert "could not be enumerated" in "\n".join(
        dictOutcome["listDivergedHashes"],
    )


def testALocklessShadowProceedsBecausePresenceIsAnotherGatesQuestion(
    dictHarness,
):
    """No lock, no satisfaction question; readiness owns presence."""
    listCalls = []
    dictOutcome = shadowRerun.fdictRerunInShadowContainer(
        _FakeConnection(), "liveContainer", _fdictWorkflow(),
        S_LIVE_WORKFLOW, S_LIVE_REPO, _fdictEnvironment(),
        fdictRunAndVerify=_fnRecordingComparison(listCalls),
    )
    assert len(listCalls) == 1
    assert dictOutcome["bPassed"] is True
