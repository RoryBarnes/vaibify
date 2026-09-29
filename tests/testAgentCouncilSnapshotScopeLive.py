"""Live acceptance for the git-tracked snapshot scope, on a real daemon.

Plan contracts B1, B3 and B4 against a real container whose name
differs from its id: the declared ``gitTrackedIdentities`` and
``gitUntrackedInventory`` programs run inside the container, and the
per-path capture streams each eligible file through the daemon's
``get_archive`` — never the repository root. The fixture repository is
the shape the scope exists for: a few tracked files beside a large
git-ignored output directory.

``test_live_per_path_capture_at_the_twenty_thousand_file_bound`` is the
benchmark the plan requires before directory batching may even be
raised: it captures a tracked set at the snapshot's file-count floor
and prints the wall clock and per-file cost.
"""

import secrets
import time

import pytest

from tests.testDockerConnectionLive import fnRequireDaemonReachable
from tests.liveContainerLabels import fdictLabels

pytestmark = pytest.mark.docker_live

S_THROWAWAY_IMAGE = "alpine:3.20"
S_REPO_ROOT = "/home/researcher/scopeRepo"
I_IGNORED_FILES = 3000

S_BUILD_SCRIPT = f"""
set -e
mkdir -p {S_REPO_ROOT} && cd {S_REPO_ROOT}
git init -q
git config user.email fixture@example.invalid
git config user.name Fixture
printf 'output/\\n' > .gitignore
mkdir -p code
printf 'print(1)\\n' > code/step.py
printf 'kept\\n' > kept.txt
printf 'sparse\\n' > sparse.txt
git add -A && git commit -q -m initial
printf 'kept, edited\\n' > kept.txt
git update-index --skip-worktree sparse.txt && rm sparse.txt
mkdir -p output
i=0; while [ $i -lt {I_IGNORED_FILES} ]; do
  printf 'result %d\\n' $i > output/r$i.bin; i=$((i+1)); done
printf 'notes\\n' > notes.txt
"""

S_BENCHMARK_SCRIPT = """
set -e
mkdir -p /home/researcher/benchRepo && cd /home/researcher/benchRepo
git init -q
git config user.email fixture@example.invalid
git config user.name Fixture
mkdir -p d
python3 -c "
for iIndex in range(19990):
    open('d/f%05d.txt' % iIndex, 'w').write('row %d\\\\n' % iIndex)
"
git add -A && git commit -q -m bench
"""


def _fcontainerStart(clientDocker, sScript, iTimeout=600):
    sName = f"vaibifyScopeLive{secrets.token_hex(4)}"
    container = clientDocker.containers.run(
        S_THROWAWAY_IMAGE, ["sleep", str(iTimeout)], name=sName,
        detach=True, labels=fdictLabels())
    iExitCode, _ = container.exec_run(
        ["/bin/sh", "-c", "apk add --no-cache git bash python3"])
    if iExitCode != 0:
        container.remove(force=True)
        pytest.skip("cannot install git, bash and python3 in the "
                    "throwaway container (no package network?)")
    assert container.exec_run(
        ["/bin/sh", "-c", "adduser -D researcher"])[0] == 0
    iExitCode, baOutput = container.exec_run(
        ["/bin/bash", "-c", sScript], user="researcher")
    assert iExitCode == 0, baOutput.decode()
    return sName, container


@pytest.fixture
def tLiveScopeContainer():
    fnRequireDaemonReachable()
    import docker
    from vaibify.docker.dockerConnection import DockerConnection
    sName, container = _fcontainerStart(docker.from_env(), S_BUILD_SCRIPT)
    try:
        yield sName, container.id, DockerConnection(), container
    finally:
        container.remove(force=True)


def _fnRecordArchivePaths(container, connection):
    """Wrap the real get_archive so the test sees every path it serves."""
    listPaths = []
    fnRealGetArchive = container.get_archive

    def _ftRecordingGetArchive(sPath, *args, **kwargs):
        listPaths.append(sPath)
        return fnRealGetArchive(sPath, *args, **kwargs)

    container.get_archive = _ftRecordingGetArchive
    connection.fcontainerGetById = lambda sContainerId: container
    return listPaths


def test_live_tracked_scope_observes_and_captures_per_path(
        tLiveScopeContainer, tmp_path, monkeypatch):
    import tarfile
    from vaibify.gui import (
        agentCouncilContext, agentCouncilSnapshotScope, containerGit)
    sName, sContainerId, connection, container = tLiveScopeContainer
    assert sName != sContainerId
    monkeypatch.setattr(
        containerGit, "fsDetectProjectRepoInContainer",
        lambda connectionDocker, sId, sPath: S_REPO_ROOT)
    listPaths = _fnRecordArchivePaths(container, connection)
    dictManifest = agentCouncilContext.fdictCaptureProjectContextSnapshot(
        connection, sContainerId, S_REPO_ROOT, "live-scope",
        sSnapshotStoreRoot=str(tmp_path),
        dictSnapshotScope=agentCouncilSnapshotScope.fdictComposeSnapshotScope(
            "gitTracked"))
    assert S_REPO_ROOT not in listPaths
    assert sorted(listPaths) == sorted(
        f"{S_REPO_ROOT}/{sPath}"
        for sPath in (".gitignore", "code/step.py", "kept.txt"))
    dictSummary = dictManifest["dictOmissionSummary"]
    assert dictSummary["dictByReason"]["ignored"]["iCount"] == I_IGNORED_FILES
    assert dictSummary["dictByReason"]["untracked"]["iCount"] == 1
    assert dictSummary["dictByReason"]["notCheckedOut"]["iCount"] == 1
    with tarfile.open(tmp_path / "live-scope" / "snapshot" /
                      "snapshot.tar") as fileTar:
        assert fileTar.extractfile("kept.txt").read() == b"kept, edited\n"
        assert not any(sMember.startswith("output")
                       for sMember in fileTar.getnames())


def test_live_per_path_capture_at_the_twenty_thousand_file_bound(tmp_path,
                                                               monkeypatch):
    """The plan's benchmark. Prints the measurement; asserts completion."""
    fnRequireDaemonReachable()
    import docker
    from vaibify.docker.dockerConnection import DockerConnection
    from vaibify.gui import (
        agentCouncilCapacity, agentCouncilContext, agentCouncilSnapshotScope,
        containerGit)
    sName, container = _fcontainerStart(docker.from_env(), S_BENCHMARK_SCRIPT,
                                        iTimeout=1800)
    try:
        sRepo = "/home/researcher/benchRepo"
        monkeypatch.setattr(
            containerGit, "fsDetectProjectRepoInContainer",
            lambda connectionDocker, sId, sPath: sRepo)
        connection = DockerConnection()
        listPaths = _fnRecordArchivePaths(container, connection)
        dictBounds = agentCouncilCapacity.fdictFloorCouncilCapacity()
        fStart = time.monotonic()
        dictManifest = (
            agentCouncilContext.fdictCaptureProjectContextSnapshot(
                connection, container.id, sRepo, "live-bench",
                sSnapshotStoreRoot=str(tmp_path), dictBounds=dictBounds,
                dictSnapshotScope=agentCouncilSnapshotScope.
                fdictComposeSnapshotScope("gitTracked")))
        fElapsed = time.monotonic() - fStart
    finally:
        container.remove(force=True)
    iFiles = len(listPaths)
    print(f"\n[tracked capture benchmark] files={iFiles} "
          f"bound={dictBounds['iMaxSnapshotFileCount']} "
          f"elapsed={fElapsed:.1f}s per-file={1000 * fElapsed / iFiles:.2f}ms "
          f"members={dictManifest['iIncludedMemberCount']}")
    assert iFiles == 19990
