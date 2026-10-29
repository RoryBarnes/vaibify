"""Renaming a real, stopped environment on a real daemon, then starting it.

The unit tests drive the transaction against an in-memory daemon; this
drives it against Docker. A fixture environment is built, started, and
filled with files owned by root, by the container user and by a third
uid, a 0600 file the copier does not own, a symlink and a hard-linked
pair; it is stopped (the container kept), renamed, and started again
under the new name. Asserted:

- the workspace arrives intact, judged by an INDEPENDENT oracle
  (``find -printf`` and ``sha256sum`` in a plain container), never by
  the copier's own manifest;
- nothing is left under the old name: container, volumes, image tags;
- the started container answers to the new name in its hostname and
  in ``VC_PROJECT_NAME``, which is what the entrypoint's banner prints;
- what ``vaibify connect`` resolves and execs into is the renamed
  container.

What it does not show: the banner TEXT (the fixture image has no
vaibify entrypoint, only the variable the entrypoint reads), and an
interactive ``vaibify connect`` (it needs a terminal).
"""

import io
import json
import os
import secrets
import tarfile

import pytest

from tests.liveContainerLabels import fdictLabels
from tests.testDockerConnectionLive import fnRequireDaemonReachable


pytestmark = [pytest.mark.docker_live, pytest.mark.exclusive]

S_SUFFIX = secrets.token_hex(3)
S_OLD = f"vaibifyrenameold{S_SUFFIX}"
S_NEW = f"vaibifyrenamenew{S_SUFFIX}"
S_FIXTURE_DOCKERFILE = (
    "FROM python:3.12-slim\n"
    "RUN useradd --uid 1000 --create-home researcher "
    "&& mkdir -p /workspace/.vaibify/logs "
    "&& touch /workspace/.vaibify/.entrypoint_ready "
    "&& chown -R researcher:researcher /workspace\n"
    "LABEL vaibify-recipe-sha256=rename-live-fixture\n"
    "WORKDIR /workspace\n"
)
S_POPULATE_SCRIPT = r"""
set -e
cd /workspace
mkdir -p data/nested
printf 'root file\n' > data/rootOwned.txt
printf 'container user file\n' > data/userOwned.txt
printf 'third user secret\n' > data/nested/thirdUser.key
ln data/userOwned.txt data/userLinked.txt
ln -s data/rootOwned.txt pointer
chown 1000:1000 data/userOwned.txt
chown 4242:4242 data/nested/thirdUser.key data/nested
chmod 0600 data/nested/thirdUser.key
"""
S_ORACLE_SCRIPT = (
    "cd /volume && find . -printf '%P|%y|%U|%G|%m|%s|%T@|%l|%n\\n' | sort "
    "&& find . -type f -exec sha256sum {} + | sort"
)


def _fbaBuildContext():
    bufferContext = io.BytesIO()
    with tarfile.open(fileobj=bufferContext, mode="w") as fileTar:
        baDockerfile = S_FIXTURE_DOCKERFILE.encode("utf-8")
        infoMember = tarfile.TarInfo("Dockerfile")
        infoMember.size = len(baDockerfile)
        fileTar.addfile(infoMember, io.BytesIO(baDockerfile))
    bufferContext.seek(0)
    return bufferContext


def _fnRemoveEverythingNamed(dockerClient, sName):
    for sKind, sTarget in (("container", sName),
                           ("volume", sName + "-workspace"),
                           ("volume", sName + "-credentials"),
                           ("image", sName + ":latest"),
                           ("image", sName + ":base")):
        try:
            if sKind == "container":
                dockerClient.containers.get(sTarget).remove(force=True)
            elif sKind == "volume":
                dockerClient.volumes.get(sTarget).remove(force=True)
            else:
                dockerClient.images.remove(sTarget)
        except Exception:  # noqa: BLE001 -- cleanup only
            pass


@pytest.fixture
def dockerClient():
    fnRequireDaemonReachable()
    import docker
    from vaibify.docker.dockerConnection import _fnEnsureDockerHost
    _fnEnsureDockerHost()
    dockerClient = docker.from_env()
    try:
        dockerClient.images.get("python:3.12-slim")
    except Exception:
        pytest.skip("`docker pull python:3.12-slim` to run this lane")
    imageBuilt, _ = dockerClient.images.build(
        fileobj=_fbaBuildContext(), custom_context=True,
        tag=S_OLD + ":latest", rm=True, labels=fdictLabels())
    imageBuilt.tag(S_OLD, tag="base")
    try:
        yield dockerClient
    finally:
        for sName in (S_OLD, S_NEW):
            _fnRemoveEverythingNamed(dockerClient, sName)


@pytest.fixture
def sProjectDirectory(tmp_path, monkeypatch):
    """A registered project under an isolated registry."""
    from vaibify.config import registryManager
    sHome = str(tmp_path / "home")
    os.makedirs(sHome)
    for sAttribute, sValue in (
        ("_S_REGISTRY_DIRECTORY", sHome),
        ("_S_REGISTRY_PATH", os.path.join(sHome, "registry.json")),
        ("_S_LOCK_PATH", os.path.join(sHome, "registry.lock")),
    ):
        monkeypatch.setattr(registryManager, sAttribute, sValue)
    sDirectory = str(tmp_path / "project")
    os.makedirs(sDirectory)
    with open(os.path.join(sDirectory, "vaibify.yml"), "w") as fileConfig:
        fileConfig.write(f"projectName: {S_OLD}\n")
    registryManager.fnAddProject(sDirectory, sMode="container")
    return sDirectory


def _fnStart(sName):
    from vaibify.cli.configLoader import fconfigLoadFromPath, fsDockerDir
    from vaibify.config.registryManager import fdictGetProject
    from vaibify.docker import containerManager
    configProject = fconfigLoadFromPath(fdictGetProject(sName)["sConfigPath"])
    containerManager.fsStartContainerDetached(configProject, fsDockerDir())


def _fsOracle(dockerClient, sVolume):
    return dockerClient.containers.run(
        "python:3.12-slim", ["sh", "-c", S_ORACLE_SCRIPT], user="0:0",
        remove=True, network_mode="none", labels=fdictLabels(),
        volumes={sVolume: {"bind": "/volume", "mode": "ro"}},
    ).decode("utf-8")


def _fsExec(dockerClient, sName, listCommand):
    iExit, baOutput = dockerClient.containers.get(sName).exec_run(
        listCommand, user="0:0")
    assert iExit == 0, baOutput
    return baOutput.decode("utf-8").strip()


def test_a_stopped_environment_is_renamed_whole_and_starts_as_itself(
    dockerClient, sProjectDirectory,
):
    from vaibify.cli.configLoader import fconfigResolveProject
    from vaibify.config import registryManager
    from vaibify.gui import environmentRename
    _fnStart(S_OLD)
    _fsExec(dockerClient, S_OLD, ["sh", "-c", S_POPULATE_SCRIPT])
    dockerClient.containers.get(S_OLD).stop(timeout=5)
    sBefore = _fsOracle(dockerClient, S_OLD + "-workspace")
    assert "|4242|4242|600|" in sBefore
    environmentRename.fnRunRenameTransaction(S_OLD, S_NEW, 0)
    assert _fsOracle(dockerClient, S_NEW + "-workspace") == sBefore
    assert [d["sName"] for d in registryManager.flistGetAllProjects()] == [
        S_NEW]
    with open(os.path.join(sProjectDirectory, "vaibify.yml")) as fileConfig:
        assert f"projectName: {S_NEW}" in fileConfig.read()
    listLeft = [s for s in json.dumps([
        [c.name for c in dockerClient.containers.list(all=True)],
        [v.name for v in dockerClient.volumes.list()],
        [t for i in dockerClient.images.list() for t in i.tags],
    ]).split('"') if s.startswith(S_OLD)]
    assert listLeft == []
    assert sorted(dockerClient.images.get(S_NEW + ":latest").tags) == sorted(
        [S_NEW + ":latest", S_NEW + ":base"])
    _fnStart(S_NEW)
    assert _fsExec(dockerClient, S_NEW, ["hostname"]) == S_NEW
    assert _fsExec(dockerClient, S_NEW, ["printenv", "VC_PROJECT_NAME"]) == (
        S_NEW)
    assert _fsExec(dockerClient, S_NEW, [
        "stat", "-c", "%u:%g:%a", "/workspace/data/nested/thirdUser.key",
    ]) == "4242:4242:600"
    assert fconfigResolveProject(S_NEW).sProjectName == S_NEW
    assert _fsExec(dockerClient, S_NEW, ["true"]) == ""
