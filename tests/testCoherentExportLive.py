"""The coherence check, driven against a real Docker daemon.

``tests/testCoherentExport.py`` proves what the comparison DECIDES, from
scripted observations. What it cannot see is whether the observation
program — an f-string of Python executed inside a container, running
git — actually produces those observations against a real repository on
a real daemon. That is a different question, and it is the one this
repository has been burned by before: a permissive test double and a
fail-closed production adapter can each be self-consistent while
disagreeing with one another.

So everything here is real: a real container, a real git repository
inside it, the real typed-read seam, and a real concurrent write landing
between the two observations.

The probe image is built on demand from a stock base — the check needs
git and python inside the container, which no minimal image ships — and
the test skips cleanly when it cannot be built. Nothing here is
science-specific.
"""

import io

import pytest

from tests.testDockerConnectionLive import fnRequireDaemonReachable
from vaibify.docker import coherentExport
from vaibify.docker import disposableContainer


pytestmark = pytest.mark.docker_live

S_COHERENCE_PROBE_IMAGE = "vaibify-coherence-probe:test2"
# NOT under /tmp. The disposable posture mounts a tmpfs there, and the
# daemon's archive endpoint cannot read out of a tmpfs mount -- it
# answers 404 for a directory an exec in the same container lists
# happily. The repository therefore lives on the container's own
# writable layer, which is also where a real project repo sits.
S_PROBE_REPO = "/home/researcher/probeRepo"

# Built from a stock base rather than named, so this lane depends on no
# image a particular machine happens to hold. Three packages, each for a
# stated reason: python3 and git are what the observation program itself
# needs inside the container, and BASH is what DockerConnection needs --
# every exec it dispatches is ``/bin/bash -c``, so an image without it
# fails with a non-zero exit and empty stderr, which reads like a broken
# program rather than a missing shell. The unprivileged user matches the
# uid every archive member is stamped with.
S_PROBE_DOCKERFILE = (
    "FROM alpine:3.20\n"
    "RUN apk add --no-cache bash git python3 && "
    "adduser -D -u 1000 researcher\n"
    "USER researcher\n"
)

# One shell script, run once, to make the probe repository. The point is
# to reach a committed repo holding a tracked file, an untracked file,
# an ignored file and a symlink — the four shapes the observation treats
# differently.
S_SEED_REPOSITORY = (
    "set -e; mkdir -p " + S_PROBE_REPO + "; cd " + S_PROBE_REPO + "; "
    "git init -q .; git symbolic-ref HEAD refs/heads/main; "
    "git config user.email probe@example.invalid; "
    "git config user.name Probe; "
    "printf 'alpha\\n' > tracked.txt; "
    "mkdir -p sub; printf 'beta\\n' > sub/nested.txt; "
    "printf 'derived.out\\n' > .gitignore; "
    "git add -A; git commit -qm seed; "
    "printf 'derived\\n' > derived.out; "
    "printf 'untracked\\n' > untracked.txt; "
    "ln -sf tracked.txt link.txt; "
    "echo SEEDED"
)


@pytest.fixture
def tProbeRepository():
    """Yield ``(connection, gateway, created)`` with a real git repo inside."""
    fnRequireDaemonReachable()
    from vaibify.docker.dockerConnection import DockerConnection

    dockerDisposable = (
        disposableContainer.fdockerCreateDisposableClient())
    _fnRequireProbeImage(dockerDisposable)
    dictGateway = disposableContainer.fdictCreateDisposableGateway(
        dockerDisposable, "coherenceProbeResource")
    dictCreated = disposableContainer.fdictReserveAndCreateContainer(
        dictGateway, "probe", S_COHERENCE_PROBE_IMAGE)
    try:
        dictSeeded = disposableContainer.fdictExecuteBoundedCommand(
            dictGateway, dictCreated["sHandle"],
            ["/bin/sh", "-c", S_SEED_REPOSITORY], fWallClockSeconds=120)
        assert "SEEDED" in dictSeeded["sOutput"], dictSeeded["sOutput"]
        yield (DockerConnection(), dictGateway, dictCreated)
    finally:
        if dictCreated["sHandle"] in dictGateway["dictHandlesById"]:
            disposableContainer.fdictDestroyAndSettle(
                dictGateway, dictCreated["sHandle"])


def _fnRequireProbeImage(dockerDisposable):
    """Build the probe image once, or skip when it cannot be built."""
    try:
        dockerDisposable.images.get(S_COHERENCE_PROBE_IMAGE)
        return
    except Exception:
        pass
    try:
        dockerDisposable.images.build(
            fileobj=io.BytesIO(S_PROBE_DOCKERFILE.encode()),
            tag=S_COHERENCE_PROBE_IMAGE, rm=True,
        )
    except Exception as error:
        pytest.skip(
            f"could not build {S_COHERENCE_PROBE_IMAGE} (needs a "
            f"network to fetch git and python): {error}"
        )


def _fnWriteInContainer(dictGateway, dictCreated, sRelativePath, sContent):
    """Write one file inside the probe repository, as the container user."""
    dictOutcome = disposableContainer.fdictExecuteBoundedCommand(
        dictGateway, dictCreated["sHandle"],
        ["/bin/sh", "-c",
         f"printf '{sContent}' > {S_PROBE_REPO}/{sRelativePath}"],
        fWallClockSeconds=60)
    assert dictOutcome["iExitCode"] == 0, dictOutcome["sOutput"]


def test_the_observation_sees_every_shape_git_can_enumerate(
    tProbeRepository,
):
    """Tracked, untracked, ignored and symlinked paths must all appear.

    The SET matters as much as the identities. A path the observation
    never saw is one the member check must refuse, so an observation
    that quietly omitted ignored files would turn every ignored file in
    a real repository into a refusal — and one that omitted untracked
    files would let them be swapped mid-copy unnoticed.
    """
    connectionDocker, _dictGateway, dictCreated = tProbeRepository
    dictObserved = connectionDocker.fdictFetchWorktreeIdentities(
        dictCreated["sContainerName"], S_PROBE_REPO)
    assert dictObserved["bSuccess"] is True, dictObserved["sReason"]
    dictIdentities = dictObserved["dictPathIdentities"]
    for sPath in ("tracked.txt", "sub/nested.txt", "untracked.txt",
                  ".gitignore", "derived.out"):
        assert sPath in dictIdentities, (
            f"{sPath} is absent from the observation: "
            f"{sorted(dictIdentities)}"
        )
    assert dictIdentities["link.txt"] == {
        "sType": "symlink", "sIdentity": "tracked.txt",
    }, "a symlink must record its target, never a hash read through it"
    assert dictObserved["listIgnoredPaths"] == ["derived.out"]
    assert len(dictObserved["sHeadSha"]) == 40


def test_the_container_identity_agrees_with_git_in_that_container(
    tProbeRepository,
):
    """An INDEPENDENT oracle, asked inside the container itself.

    The observation program computes blob identities with hashlib; git
    computes them its own way. Asking git in the same container, over
    the same bytes, is what makes this a check rather than the program
    agreeing with itself. If the two ever diverge — a filter, an
    encoding, a git version — the comparison would refuse every
    repository, and this test says which side moved.
    """
    connectionDocker, dictGateway, dictCreated = tProbeRepository
    dictObserved = connectionDocker.fdictFetchWorktreeIdentities(
        dictCreated["sContainerName"], S_PROBE_REPO)
    dictFromGit = disposableContainer.fdictExecuteBoundedCommand(
        dictGateway, dictCreated["sHandle"],
        ["/bin/sh", "-c",
         f"cd {S_PROBE_REPO} && git hash-object --no-filters "
         "tracked.txt sub/nested.txt untracked.txt derived.out"],
        fWallClockSeconds=60)
    listFromGit = dictFromGit["sOutput"].split()
    listFromProgram = [
        dictObserved["dictPathIdentities"][sPath]["sIdentity"]
        for sPath in ("tracked.txt", "sub/nested.txt", "untracked.txt",
                      "derived.out")
    ]
    assert listFromProgram == listFromGit, (
        "the observation program and git disagree about the same bytes"
    )


def test_a_quiet_repository_exports_and_a_written_one_is_refused(
    tProbeRepository,
):
    """The whole check, end to end, against a real concurrent write.

    Both directions in one test on purpose: the export must SUCCEED
    when nothing touches the repository, because a check that refuses
    everything would satisfy the negative half and be worthless. The
    write between the two observations is a real ``printf`` in a real
    container — the exact thing an agent or a terminal does.
    """
    connectionDocker, dictGateway, dictCreated = tProbeRepository
    sContainerName = dictCreated["sContainerName"]

    baArchive = coherentExport.fbaExportRepositoryCoherently(
        connectionDocker, sContainerName, S_PROBE_REPO, 1 << 22)
    assert baArchive, "a quiet repository must export"

    fbaFetchOriginal = connectionDocker.fbaFetchDirectoryArchive

    def fbaFetchThenWrite(sContainerId, sPath, iMaxBytes):
        """Stream the archive, then write -- the mid-copy race, made real."""
        baResult = fbaFetchOriginal(sContainerId, sPath, iMaxBytes)
        _fnWriteInContainer(
            dictGateway, dictCreated, "tracked.txt", "rewritten\\n")
        return baResult

    connectionDocker.fbaFetchDirectoryArchive = fbaFetchThenWrite
    with pytest.raises(coherentExport.ExportTornError) as errorRaised:
        coherentExport.fbaExportRepositoryCoherently(
            connectionDocker, sContainerName, S_PROBE_REPO, 1 << 22)
    assert "tracked.txt" in str(errorRaised.value)
