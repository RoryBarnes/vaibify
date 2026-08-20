"""Live acceptance for the council snapshot against a real daemon.

Design section 15.6, first paragraph: against a real project container
whose NAME differs from its Docker id, the snapshot primitive must
produce a coherent snapshot with root validation, member validation,
exclusions and limits applied, clean up an injected mid-stream
failure, and mutate NOTHING in the container.

The fixture builds a throwaway alpine container, installs git and bash
(the exec path assumes bash, and the git identity read needs git),
creates the unprivileged ``researcher`` user the exec path defaults
to, and assembles a small generic fixture repository as that user.
Package installation needs network; when it is unavailable the run
skips with the reason stated, exactly as the daemon guard does.
"""

import secrets

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
git add -A
git commit -q -m 'initial fixture state'
printf 'uncommitted scratch\\n' > scratch.txt
"""

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
            ["/bin/sh", "-c", "apk add --no-cache git bash"],
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
        assert not any(
            sMemberName.startswith((".git", ".claude"))
            for sMemberName in dictMembers
        )
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
