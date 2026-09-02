"""Live acceptance for the council snapshot against a real daemon.

Design section 15.6, first paragraph: against a real project container
whose NAME differs from its Docker id, the snapshot primitive must
produce a coherent snapshot with root validation, member validation,
exclusions and limits applied, clean up an injected mid-stream
failure, and mutate NOTHING in the container.

The R5 legs below additionally mutate the repository DURING streaming
-- by wrapping ``get_archive`` so the first yielded chunk triggers an
exec inside the container -- and assert that every tear the porcelain
digest alone cannot see (dirty-content change, change-then-revert,
rename, symlink swap) is refused with the torn property named and no
partial snapshot left behind.

The fixture builds a throwaway alpine container, installs git and bash
(the exec path assumes bash, and the git identity read needs git),
creates the unprivileged ``researcher`` user the exec path defaults
to, and assembles a small generic fixture repository as that user.
Package installation needs network; when it is unavailable the run
skips with the reason stated, exactly as the daemon guard does.
"""

import re
import secrets
import threading

import pytest

from tests.testDockerConnectionLive import fnRequireDaemonReachable


pytestmark = pytest.mark.docker_live

S_THROWAWAY_IMAGE = "alpine:3.20"
S_REPO_ROOT = "/home/researcher/sampleRepo"

S_BUILD_FIXTURE_REPO_SCRIPT = """
set -e
mkdir -p /home/researcher/sampleRepo
cd /home/researcher/sampleRepo
git init -q
git config user.email fixture@example.invalid
git config user.name Fixture
printf 'alpha payload\\n' > dataFile.txt
mkdir -p analysis
printf 'beta payload\\n' > analysis/results.txt
ln -s dataFile.txt linkToData
mkdir -p .claude
printf 'topSecretTokenValue' > .claude/credentials.json
printf 'derived/\\n*.dotenvlocal\\n' > .gitignore
git add -A
git commit -q -m 'initial fixture state'
printf 'uncommitted scratch\\n' > scratch.txt
mkdir -p derived
printf 'hours of compute went into this\\n' > derived/expensive.forward
mkdir -p .env
printf 'API_TOKEN=topSecretDotenvValue\\n' > .env/settings
"""

# The git-ignored path the fixture creates. It exists ON DISK, so the
# daemon's archive carries it, and `git ls-files` does not — the shape
# that refused every real repository until 2026-08-24. The fixture
# carried nothing like it before then: it does `git add -A` with no
# .gitignore, so every file it had was tracked and the whole class was
# structurally invisible here.
#
# Named for the case that decided the policy: a derived artifact that
# cost an hour to produce, which a council must not have to regenerate.
S_IGNORED_FIXTURE_PATH = "derived/expensive.forward"

# One digest over every file's content plus the full path listing,
# .git included: if the capture writes, touches ownership, or leaves
# anything behind, this string changes.
S_CONTENT_DIGEST_SCRIPT = (
    "cd /home/researcher/sampleRepo && "
    "find . -type f | LC_ALL=C sort | xargs -r sha256sum | sha256sum && "
    "find . | LC_ALL=C sort | sha256sum && "
    "git status --porcelain"
)


@pytest.fixture
def tLiveProjectContainer():
    """Yield (sName, sContainerId, connection) around a fixture repo."""
    fnRequireDaemonReachable()
    import docker
    from vaibify.docker.dockerConnection import DockerConnection
    clientDocker = docker.from_env()
    sName = f"vaibifyCouncilContext{secrets.token_hex(4)}"
    container = clientDocker.containers.run(
        S_THROWAWAY_IMAGE, ["sleep", "600"], name=sName, detach=True,
    )
    try:
        iExitCode, _ = container.exec_run(
            ["/bin/sh", "-c", "apk add --no-cache git bash python3"],
        )
        if iExitCode != 0:
            pytest.skip(
                "cannot install git+bash in the throwaway container "
                "(no package network?)"
            )
        iExitCode, baOutput = container.exec_run(
            ["/bin/sh", "-c", "adduser -D researcher"],
        )
        assert iExitCode == 0, baOutput
        iExitCode, baOutput = container.exec_run(
            ["/bin/bash", "-c", S_BUILD_FIXTURE_REPO_SCRIPT],
            user="researcher",
        )
        assert iExitCode == 0, (
            f"fixture repo build failed: {baOutput.decode()}"
        )
        yield (sName, container.id, DockerConnection())
    finally:
        try:
            container.remove(force=True)
        except Exception:
            pass


def _fsReadContainerContentDigest(sContainerId):
    """Return the container-side content digest string."""
    import docker
    container = docker.from_env().containers.get(sContainerId)
    iExitCode, baOutput = container.exec_run(
        ["/bin/bash", "-c", S_CONTENT_DIGEST_SCRIPT], user="researcher",
    )
    assert iExitCode == 0, baOutput
    return baOutput.decode()


def _fsReadContainerHeadSha(sContainerId):
    """Return HEAD as git inside the container reports it."""
    import docker
    container = docker.from_env().containers.get(sContainerId)
    iExitCode, baOutput = container.exec_run(
        ["/bin/bash", "-c",
         "cd /home/researcher/sampleRepo && git rev-parse HEAD"],
        user="researcher",
    )
    assert iExitCode == 0, baOutput
    return baOutput.decode().strip()


def test_live_capture_is_coherent_excluding_and_nonmutating(
    tLiveProjectContainer, tmp_path,
):
    """The section 15.6 acceptance drive, name != id asserted up front."""
    import tarfile

    from vaibify.gui.agentCouncilContext import (
        fdictCaptureProjectContextSnapshot,
    )

    sName, sContainerId, connection = tLiveProjectContainer
    assert sName != sContainerId, (
        "the fixture must exercise a container whose name differs from "
        "its id"
    )
    sDigestBefore = _fsReadContainerContentDigest(sContainerId)

    dictManifest = fdictCaptureProjectContextSnapshot(
        connection, sContainerId, S_REPO_ROOT, "live-campaign",
        sSnapshotStoreRoot=str(tmp_path),
    )

    assert dictManifest["sCommitSha"] == _fsReadContainerHeadSha(
        sContainerId,
    )
    setOmissionPaths = {
        dictRow["sPath"] for dictRow in dictManifest["listOmissions"]
    }
    assert {".git", ".claude"} <= setOmissionPaths

    pathArchive = tmp_path / "live-campaign" / "snapshot" / "snapshot.tar"
    with tarfile.open(pathArchive) as fileTar:
        dictMembers = {info.name: info for info in fileTar.getmembers()}
        assert (
            fileTar.extractfile(dictMembers["dataFile.txt"]).read()
            == b"alpha payload\n"
        )
        assert (
            fileTar.extractfile(dictMembers["scratch.txt"]).read()
            == b"uncommitted scratch\n"
        )
        assert dictMembers["linkToData"].issym()
        # COMPONENT-wise, not a bare string prefix. The exclusion
        # policy matches path components, so `.gitignore` — a tracked
        # source file that is part of the project — must ship, and a
        # `startswith(".git")` test would forbid it along with the
        # `.git` directory. Same for `.gitattributes`, `.gitmodules`.
        assert not any(
            sMemberName.split("/")[0] in (".git", ".claude")
            for sMemberName in dictMembers
        )
        assert ".gitignore" in dictMembers, (
            "a tracked .gitignore was excluded; it is source, not "
            "repository internals")
    assert b"topSecretTokenValue" not in pathArchive.read_bytes()

    assert _fsReadContainerContentDigest(sContainerId) == sDigestBefore, (
        "the capture mutated the project container"
    )


def test_live_injected_stream_failure_cleans_up_and_mutates_nothing(
    tLiveProjectContainer, tmp_path,
):
    """A torn daemon stream leaves no partial snapshot and no mark."""
    from vaibify.gui.agentCouncilContext import (
        fdictCaptureProjectContextSnapshot,
    )

    _, sContainerId, connection = tLiveProjectContainer
    sDigestBefore = _fsReadContainerContentDigest(sContainerId)
    containerReal = connection.fcontainerGetById(sContainerId)

    class _TornStreamContainerProxy:
        """Delegates everything; tears the archive stream mid-transfer."""

        def get_archive(self, sPath):
            iterReal, dictStat = containerReal.get_archive(sPath)

            def _fiterTornChunks():
                for baChunk in iterReal:
                    yield baChunk[:256]
                    raise OSError("injected stream failure")

            return (_fiterTornChunks(), dictStat)

        def __getattr__(self, sAttributeName):
            return getattr(containerReal, sAttributeName)

    connection.fcontainerGetById = (
        lambda sRequestedId: _TornStreamContainerProxy()
    )
    with pytest.raises(OSError, match="injected stream failure"):
        fdictCaptureProjectContextSnapshot(
            connection, sContainerId, S_REPO_ROOT, "torn-campaign",
            sSnapshotStoreRoot=str(tmp_path),
        )
    assert list(tmp_path.iterdir()) == [], (
        "the injected failure left a partial snapshot behind"
    )
    assert _fsReadContainerContentDigest(sContainerId) == sDigestBefore


# ---------------------------------------------------------------------
# R5: the repository is mutated DURING streaming; every tear refuses.
# ---------------------------------------------------------------------


def _fnRunInContainerAsResearcher(containerReal, sScript):
    """Run one bash script in the fixture container as the repo owner."""
    iExitCode, baOutput = containerReal.exec_run(
        ["/bin/bash", "-c", sScript], user="researcher",
    )
    assert iExitCode == 0, baOutput


class _MidStreamMutatingContainerProxy:
    """Delegates everything; runs one mutation after the FIRST chunk.

    The small chunk size keeps the daemon's tar stream multi-chunk so
    the mutation genuinely lands mid-transfer.
    """

    def __init__(self, containerReal, sMutationScript):
        self._containerReal = containerReal
        self._sMutationScript = sMutationScript

    def get_archive(self, sPath):
        iterReal, dictStat = self._containerReal.get_archive(
            sPath, chunk_size=65536,
        )
        containerReal = self._containerReal
        sMutationScript = self._sMutationScript

        def _fiterMutatingChunks():
            bMutationDone = False
            for baChunk in iterReal:
                yield baChunk
                if not bMutationDone:
                    bMutationDone = True
                    _fnRunInContainerAsResearcher(
                        containerReal, sMutationScript,
                    )

        return (_fiterMutatingChunks(), dictStat)

    def __getattr__(self, sAttributeName):
        return getattr(self._containerReal, sAttributeName)


def _fnAssertMidStreamMutationRefuses(
    tLiveProjectContainer, tmp_path, sPreparationScript, sMutationScript,
    sExpectedPattern,
):
    """Drive one capture with a mid-stream mutation; assert the refusal."""
    from vaibify.gui.agentCouncilContext import (
        SnapshotRefusedError,
        fdictCaptureProjectContextSnapshot,
    )

    sName, sContainerId, connection = tLiveProjectContainer
    assert sName != sContainerId, (
        "the fixture must exercise a container whose name differs from "
        "its id"
    )
    containerReal = connection.fcontainerGetById(sContainerId)
    if sPreparationScript:
        _fnRunInContainerAsResearcher(containerReal, sPreparationScript)
    connection.fcontainerGetById = (
        lambda sRequestedId: _MidStreamMutatingContainerProxy(
            containerReal, sMutationScript,
        )
    )
    with pytest.raises(SnapshotRefusedError) as errorInfo:
        fdictCaptureProjectContextSnapshot(
            connection, sContainerId, S_REPO_ROOT, "torn-campaign",
            sSnapshotStoreRoot=str(tmp_path),
        )
    assert re.search(sExpectedPattern, str(errorInfo.value)), (
        f"the refusal did not name the torn property: {errorInfo.value}"
    )
    assert list(tmp_path.iterdir()) == [], (
        "the refused capture left a partial snapshot behind"
    )


def test_live_midstream_content_change_of_dirty_file_refuses(
    tLiveProjectContainer, tmp_path,
):
    """R5 proof (a): an already-dirty file's CONTENT changes mid-stream.

    The porcelain state stays 'dirty' before and after, so only the
    per-path content identity can see this tear.
    """
    _fnAssertMidStreamMutationRefuses(
        tLiveProjectContainer, tmp_path,
        "cd /home/researcher/sampleRepo && "
        "printf 'dirty version one\\n' > dataFile.txt",
        "cd /home/researcher/sampleRepo && "
        "printf 'dirty version two\\n' > dataFile.txt",
        r"content identity of 'dataFile\.txt' changed",
    )


def test_live_midstream_rename_of_tracked_file_refuses(
    tLiveProjectContainer, tmp_path,
):
    """R5 proof (c): a tracked file is renamed mid-stream.

    The rename flips the porcelain map (a deletion plus an untracked
    appearance), so the state-digest half of the observation names it.
    """
    _fnAssertMidStreamMutationRefuses(
        tLiveProjectContainer, tmp_path,
        "",
        "cd /home/researcher/sampleRepo && "
        "mv dataFile.txt dataFileMoved.txt",
        r"porcelain working-tree state digest",
    )


def test_live_midstream_symlink_swap_refuses(
    tLiveProjectContainer, tmp_path,
):
    """R5 proof (d): an untracked regular file becomes a symlink.

    The porcelain state stays 'untracked' either way, so only the
    observed TYPE of the path can see this tear.
    """
    _fnAssertMidStreamMutationRefuses(
        tLiveProjectContainer, tmp_path,
        "",
        "cd /home/researcher/sampleRepo && "
        "rm scratch.txt && ln -s dataFile.txt scratch.txt",
        r"path 'scratch\.txt' changed type from file to symlink",
    )


BA_MUTATION_MARKER = b"COUNCIL_MUTATION_MARKER_BYTES_01"


class _ChangeThenRevertContainerProxy:
    """Change the dirty file to B mid-stream; revert to A only if B is
    in the streamed bytes.

    The daemon decides how much of the file it serialized before our
    first chunk arrived, so a fixed script cannot make the outcome
    deterministic. This proxy makes it deterministic by LOOKING: after
    the last chunk it searches the accumulated stream for B's marker.
    Marker present (B or torn content was captured) -> revert to A, so
    pre == post == A and the ARCHIVE-match refusal must fire. Marker
    absent (pure A was captured) -> leave B in place, so post != pre
    and the CONTENT-identity refusal must fire. Every daemon buffering
    behaviour therefore ends in a refusal.

    The mutation runs on a THREAD, and that is load-bearing, not
    tidiness: the daemon does not service an exec on a container whose
    archive stream is still draining (verified live on Docker 28.4.0 /
    colima — an inline exec after the first chunk of this 8 MiB stream
    deadlocked until the 600 s client timeout, while the small-repo
    mutation tests passed only because their whole tar fit in the
    socket buffers and the archive handler had already finished). The
    generator therefore keeps consuming chunks while the mutation exec
    waits its turn daemon-side, and joins the thread before the revert
    decision so the pre/post reads always see the mutation landed.
    """

    def __init__(self, containerReal):
        self._containerReal = containerReal

    def get_archive(self, sPath):
        iterReal, dictStat = self._containerReal.get_archive(
            sPath, chunk_size=65536,
        )
        containerReal = self._containerReal

        def _fiterAdaptiveChunks():
            listStreamedChunks = []
            threadMutate = None
            for baChunk in iterReal:
                yield baChunk
                listStreamedChunks.append(baChunk)
                if threadMutate is None:
                    threadMutate = threading.Thread(
                        target=_fnRunInContainerAsResearcher,
                        args=(containerReal,
                              "cp /home/researcher/bytesVersionB.bin "
                              "/home/researcher/sampleRepo/bigFile.bin"))
                    threadMutate.start()
            assert threadMutate is not None, "the stream yielded no chunks"
            threadMutate.join(timeout=120)
            assert not threadMutate.is_alive(), (
                "the mid-stream mutation exec never completed")
            if BA_MUTATION_MARKER in b"".join(listStreamedChunks):
                _fnRunInContainerAsResearcher(
                    containerReal,
                    "cp /home/researcher/bytesVersionA.bin "
                    "/home/researcher/sampleRepo/bigFile.bin",
                )

        return (_fiterAdaptiveChunks(), dictStat)

    def __getattr__(self, sAttributeName):
        return getattr(self._containerReal, sAttributeName)


S_BUILD_CHANGE_THEN_REVERT_FIXTURE = """
set -e
cd /home/researcher/sampleRepo
printf 'seed\\n' > bigFile.bin
git add bigFile.bin
git commit -q -m 'add the large tracked file'
head -c 8388608 /dev/zero | tr '\\0' 'a' > /home/researcher/bytesVersionA.bin
cp /home/researcher/bytesVersionA.bin /home/researcher/bytesVersionB.bin
printf 'COUNCIL_MUTATION_MARKER_BYTES_01' | dd \
of=/home/researcher/bytesVersionB.bin bs=1 seek=4194304 conv=notrunc \
2>/dev/null
cp /home/researcher/bytesVersionA.bin bigFile.bin
"""


def test_live_change_then_revert_refuses_deterministically(
    tLiveProjectContainer, tmp_path,
):
    """R5 proof (b2): change-then-revert around an 8 MiB dirty file.

    Construction used (stated per the spec): the tracked file is dirty
    at bytes-A before capture; between the first and second 64 KiB
    chunks it is changed to bytes-B (same size, one marker region
    differing); after the last chunk the proxy reverts to A ONLY when
    B's marker appears in the streamed bytes (see
    ``_ChangeThenRevertContainerProxy`` -- the adaptive revert is what
    makes SOME refusal deterministic under any daemon buffering).
    Either refusal message is accepted; both name the file and its
    content identity.
    """
    from vaibify.gui.agentCouncilContext import (
        SnapshotRefusedError,
        fdictCaptureProjectContextSnapshot,
    )

    sName, sContainerId, connection = tLiveProjectContainer
    assert sName != sContainerId
    containerReal = connection.fcontainerGetById(sContainerId)
    _fnRunInContainerAsResearcher(
        containerReal, S_BUILD_CHANGE_THEN_REVERT_FIXTURE,
    )
    connection.fcontainerGetById = (
        lambda sRequestedId: _ChangeThenRevertContainerProxy(containerReal)
    )
    with pytest.raises(SnapshotRefusedError) as errorInfo:
        fdictCaptureProjectContextSnapshot(
            connection, sContainerId, S_REPO_ROOT, "revert-campaign",
            sSnapshotStoreRoot=str(tmp_path),
        )
    assert re.search(
        r"content identity of 'bigFile\.bin'", str(errorInfo.value),
    ), (
        f"the refusal did not name the reverted file: {errorInfo.value}"
    )
    assert list(tmp_path.iterdir()) == [], (
        "the refused capture left a partial snapshot behind"
    )


@pytest.mark.docker
def testALiveGitIgnoredFileIsCarriedButAReviewedStoreIsNot(
    tLiveProjectContainer, tmp_path,
):
    """The live half of the ruling, and of the policy that outlived it.

    The unit suite drives synthetic tar streams, so it can only assert
    what a HAND-BUILT observation claims git ignores. This drives the
    real ``git ls-files`` enumerations inside a real container against
    a real ``get_archive``, which is where the two disagreed: the
    daemon serializes the filesystem and git did not enumerate the
    ignored files, so every one of them refused the whole capture until
    they became observed paths.

    Both halves matter and they pull opposite ways. The derived
    artifact MUST ship — regenerating it can cost hours, and a
    researcher expects the repository they have. The reviewed
    credential store must STILL not, because it is now the only thing
    between a project secret and a third-party provider.
    """
    from vaibify.gui import agentCouncilContext

    sName, sContainerId, connectionDocker = tLiveProjectContainer
    dictManifest = agentCouncilContext.fdictCaptureProjectContextSnapshot(
        connectionDocker, sContainerId, S_REPO_ROOT, "live-ignored",
        sSnapshotStoreRoot=str(tmp_path))

    setIncluded = {dictEntry["sPath"]
                   for dictEntry in dictManifest["listIncludedEntries"]}
    assert "dataFile.txt" in setIncluded, (
        "the tracked content is missing, so this proves nothing")
    assert S_IGNORED_FIXTURE_PATH in setIncluded, (
        "a git-ignored derived artifact was dropped; regenerating it "
        "is exactly the cost a council must not impose")
    assert dictManifest["listGitIgnoredPaths"] == [S_IGNORED_FIXTURE_PATH], (
        "the manifest does not record which included paths git leaves "
        "untracked, so a participant cannot tell derived from source")

    baSealed = (tmp_path / "live-ignored" / "snapshot"
                / "snapshot.tar").read_bytes()
    assert b"hours of compute went into this" in baSealed
    assert b"topSecretDotenvValue" not in baSealed, (
        "a dotenv shipped to third-party model providers; .gitignore "
        "no longer keeps one out, so the reviewed credential list is "
        "the whole defence")
    assert b"topSecretTokenValue" not in baSealed


@pytest.mark.docker
def testTheLivePreflightWeighsWhatTheLiveCaptureKeeps(
    tLiveProjectContainer, tmp_path,
):
    """The pre-flight and the capture must agree about the repository.

    Two independent walks — a metadata probe and a validated tar
    stream — and nothing but this test binds them. They have already
    disagreed twice: the probe counted ``.git`` (refusing a council
    over a 315 MB pack the snapshot never carries), then counted
    ignored files. Both were found by running against a real
    repository, neither by a fixture.
    """
    from vaibify.gui import agentCouncilContext

    sName, sContainerId, connectionDocker = tLiveProjectContainer
    dictFeasibility = agentCouncilContext.fdictAssessSnapshotFeasibility(
        connectionDocker, sContainerId, S_REPO_ROOT)
    dictManifest = agentCouncilContext.fdictCaptureProjectContextSnapshot(
        connectionDocker, sContainerId, S_REPO_ROOT, "live-agreement",
        sSnapshotStoreRoot=str(tmp_path))

    assert dictFeasibility["bFits"] is True
    assert dictFeasibility["iTotalBytes"] == dictManifest[
        "iTotalContentBytes"], (
        "the pre-flight and the capture disagree about how many bytes "
        f"this repository holds ({dictFeasibility['iTotalBytes']} vs "
        f"{dictManifest['iTotalContentBytes']}); one of them is "
        "weighing files the other does not")


S_BUILD_UNSNAPSHOTTABLE_REPO_SCRIPT = """
set -e
mkdir -p /home/researcher/awkwardRepo
cd /home/researcher/awkwardRepo
git init -q
git config user.email fixture@example.invalid
git config user.name Fixture
printf 'content\\n' > tracked.txt
ln -s /etc/passwd escapingLink
mkfifo namedPipe
git add -A
git commit -q -m 'awkward fixture state'
"""


@pytest.mark.docker
def testThePreflightForeseesTheStructuralRefusalsTheCaptureMakes(
    tLiveProjectContainer, tmp_path,
):
    """Predict, then confirm — the two must agree on the same repository.

    A pre-flight that only checked SIZE left every other refusal to
    arrive at convene time, which is the complaint the size check
    existed to answer, just narrower. These refusals are properties of
    the tree as it sits, so they are foreseeable.

    The test is a PAIR on one repository, and both halves are load-
    bearing. The pre-flight must NAME each problem (a warning that says
    only "cannot snapshot" sends a researcher hunting), and the capture
    must actually refuse — a pre-flight predicting a refusal that never
    comes is its own defect, and asserting only the prediction would
    pass for a detector that flags healthy repositories.
    """
    import docker

    from vaibify.gui import agentCouncilContext

    _, sContainerId, connectionDocker = tLiveProjectContainer
    container = docker.from_env().containers.get(sContainerId)
    iExitCode, baOutput = container.exec_run(
        ["/bin/bash", "-c", S_BUILD_UNSNAPSHOTTABLE_REPO_SCRIPT],
        user="researcher")
    if iExitCode != 0:
        pytest.skip(f"cannot build the awkward fixture: {baOutput.decode()}")
    sAwkwardRoot = "/home/researcher/awkwardRepo"

    dictFeasibility = agentCouncilContext.fdictAssessSnapshotFeasibility(
        connectionDocker, sContainerId, sAwkwardRoot)

    assert dictFeasibility["bFits"] is False
    assert dictFeasibility["bResolvableByExcludingFiles"] is False, (
        "a structural refusal was offered as an oversized-file "
        "exclusion; ticking a box cannot fix a symlink out of the repo")
    assert "escapingLink" in dictFeasibility["sReason"], (
        f"the escaping symlink is not named: {dictFeasibility['sReason']!r}")
    assert "namedPipe" in dictFeasibility["sReason"], (
        f"the named pipe is not named: {dictFeasibility['sReason']!r}")

    with pytest.raises(agentCouncilContext.SnapshotRefusedError):
        agentCouncilContext.fdictCaptureProjectContextSnapshot(
            connectionDocker, sContainerId, sAwkwardRoot, "awkward",
            sSnapshotStoreRoot=str(tmp_path))
