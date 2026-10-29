"""A project copied in from the researcher's directory is never cloned over.

A converted project's folder is written by the hub's seed just after
the container starts, while the image's entrypoint clones every
repository its baked list names -- the project's own among them, into
the same folder. The seed writes ``.git`` last, so the clone raced a
half-copied folder (``clone-unknown: destination path ... already
exists``), and every later start pulled the author's branch onto the
researcher's copy. These tests drive the start's answer: the image's
own list, read out of a container that never runs, less the one entry
that lands in the copied folder, mounted read-only over the baked one.
"""

import io
import json
import os
import stat
import subprocess
import tarfile

import pytest

from tests.testContainerManager import _fConfigMinimal
from vaibify.config import containerConfig, registryManager
from vaibify.docker import containerManager


S_IMAGE_ID = "sha256:" + "ab" * 32
S_PROJECT_LINE = "aigreen|https://example.invalid/author/aigreen.git|main|reference"
S_DEPENDENCY_LINE = "solver|https://example.invalid/solver.git|v2|pip_editable"
S_IMAGE_LIST = (
    "# Vaibify Repository Configuration\n"
    "# Lines starting with # are comments.\n"
    f"{S_DEPENDENCY_LINE}\n"
    f"{S_PROJECT_LINE}\n"
)
DICT_BUILT_POSTURE = {
    "sMode": "built", "bWithCredentials": True, "sImageDigest": S_IMAGE_ID,
}


def _fbaTarOfOneFile(sText, sName="container.conf"):
    """Return the tar stream ``docker cp ... -`` produces for one file."""
    bufferArchive = io.BytesIO()
    with tarfile.open(fileobj=bufferArchive, mode="w") as fileTar:
        baContent = sText.encode("utf-8")
        infoMember = tarfile.TarInfo(sName)
        infoMember.size = len(baContent)
        fileTar.addfile(infoMember, io.BytesIO(baContent))
    return bufferArchive.getvalue()


def _fbaTarOfSymlink(sTarget):
    """Return the tar stream ``docker cp`` produces when the path is a link."""
    bufferArchive = io.BytesIO()
    with tarfile.open(fileobj=bufferArchive, mode="w") as fileTar:
        infoMember = tarfile.TarInfo("container.conf")
        infoMember.type = tarfile.SYMTYPE
        infoMember.linkname = sTarget
        fileTar.addfile(infoMember)
    return bufferArchive.getvalue()


class _DockerCommandRecorder:
    """Answer the three docker commands the read makes; record every one."""

    def __init__(self, baArchive, bCreateAnswers=True, bCopyAnswers=True,
                 sCopyRefusal=""):
        self.baArchive = baArchive
        self.bCreateAnswers = bCreateAnswers
        self.bCopyAnswers = bCopyAnswers
        self.sCopyRefusal = sCopyRefusal
        self.listCommands = []

    def __call__(self, saCommand, **kwargs):
        self.listCommands.append(list(saCommand))
        sVerb = saCommand[1]
        if sVerb == "create":
            return subprocess.CompletedProcess(
                saCommand, 0 if self.bCreateAnswers else 1,
                "readerId42\n" if self.bCreateAnswers else "", "")
        if sVerb == "cp":
            return subprocess.CompletedProcess(
                saCommand, 0 if self.bCopyAnswers else 1,
                self.baArchive if self.bCopyAnswers else b"",
                self.sCopyRefusal.encode("utf-8"))
        return subprocess.CompletedProcess(saCommand, 0, "", "")

    def flistVerbs(self):
        return [saCommand[1] for saCommand in self.listCommands]


@pytest.fixture
def fixtureSeededProject(tmp_path, monkeypatch):
    """A registered project whose folder was copied in from ``aigreen``."""
    sDirectory = str(tmp_path / "aigreen")
    os.makedirs(sDirectory)
    dictEntry = {
        "sName": "testproj", "sMode": "container", "sDirectory": sDirectory,
        registryManager.S_WORKSPACE_SEEDED_KEY: True,
    }
    monkeypatch.setattr(
        registryManager, "fdictGetProject", lambda sName: dict(dictEntry))
    return dictEntry


def _fnUseDocker(monkeypatch, recorderDocker):
    monkeypatch.setattr(containerManager.subprocess, "run", recorderDocker)
    # A freshly converted project has no credentials volume; the mount
    # asks the daemon whether one exists (2026-10-29), and that question
    # is not one of the three commands the read makes.
    monkeypatch.setattr(containerManager, "fbVolumeExists", lambda sVolumeName: False)


def _fsReadMountedList(saRunArgs):
    """Return the text of the list the run args mount, failing if none is."""
    for iIndex, sArgument in enumerate(saRunArgs):
        if sArgument == "--mount" and (
            containerConfig.S_IMAGE_REPOSITORY_LIST_PATH in saRunArgs[iIndex + 1]
        ):
            sSpec = saRunArgs[iIndex + 1]
            assert sSpec.endswith(",readonly")
            sHostPath = sSpec.split('"source=')[1].split('"')[0]
            with open(sHostPath, encoding="utf-8") as fileList:
                return fileList.read()
    raise AssertionError(f"no repository list is mounted in {saRunArgs}")


# --- the list the image is started with -----------------------------------


@pytest.mark.falsification
def test_a_seeded_project_starts_with_its_own_reference_entry_removed(
    fixtureSeededProject, monkeypatch,
):
    """Kills: a start that leaves the baked list in place for a seeded folder."""
    recorderDocker = _DockerCommandRecorder(_fbaTarOfOneFile(S_IMAGE_LIST))
    _fnUseDocker(monkeypatch, recorderDocker)
    saRunArgs = containerManager.flistBuildRunArgs(
        _fConfigMinimal(), bDetached=True, dictPosture=DICT_BUILT_POSTURE)
    sMounted = _fsReadMountedList(saRunArgs)
    assert S_PROJECT_LINE not in sMounted
    assert sMounted == S_IMAGE_LIST.replace(S_PROJECT_LINE + "\n", "")
    assert recorderDocker.flistVerbs() == ["create", "cp", "rm"]


@pytest.mark.falsification
def test_the_list_is_read_from_a_container_that_never_runs_and_is_removed(
    fixtureSeededProject, monkeypatch,
):
    """Kills: a reader container left behind, or one that is started."""
    recorderDocker = _DockerCommandRecorder(_fbaTarOfOneFile(S_IMAGE_LIST))
    _fnUseDocker(monkeypatch, recorderDocker)
    containerManager.flistBuildRunArgs(
        _fConfigMinimal(), bDetached=True, dictPosture=DICT_BUILT_POSTURE)
    saCreate, saCopy, saRemove = recorderDocker.listCommands
    assert saCreate[:2] == ["docker", "create"] and saCreate[-1] == S_IMAGE_ID
    assert f"{containerManager.S_REPOSITORY_LIST_READER_LABEL}=1" in saCreate
    assert saCopy == [
        "docker", "cp",
        "readerId42:" + containerConfig.S_IMAGE_REPOSITORY_LIST_PATH, "-",
    ]
    assert saRemove == ["docker", "rm", "-f", "readerId42"]


def test_the_reader_is_removed_even_when_the_copy_fails(
    fixtureSeededProject, monkeypatch,
):
    recorderDocker = _DockerCommandRecorder(b"", bCopyAnswers=False)
    _fnUseDocker(monkeypatch, recorderDocker)
    with pytest.raises(RuntimeError):
        containerManager.flistBuildRunArgs(
            _fConfigMinimal(), bDetached=True, dictPosture=DICT_BUILT_POSTURE)
    assert recorderDocker.flistVerbs() == ["create", "cp", "rm"]


@pytest.mark.falsification
def test_a_list_that_cannot_be_read_refuses_the_start_by_name(
    fixtureSeededProject, monkeypatch,
):
    """Kills: a failed read that starts anyway, letting the clone race the copy."""
    _fnUseDocker(monkeypatch, _DockerCommandRecorder(b"", bCreateAnswers=False))
    with pytest.raises(RuntimeError) as errorRefused:
        containerManager.flistBuildRunArgs(
            _fConfigMinimal(), bDetached=True, dictPosture=DICT_BUILT_POSTURE)
    sMessage = str(errorRefused.value)
    assert "'testproj' was not started" in sMessage
    assert "did not create the container" in sMessage
    assert "Start the project again" in sMessage


S_DAEMON_FILE_ABSENT = (
    "Error response from daemon: Could not find the file "
    "/etc/vaibify/container.conf in container readerId42\n"
)


@pytest.mark.falsification
def test_an_image_that_carries_no_list_starts_with_nothing_mounted(
    fixtureSeededProject, monkeypatch,
):
    """Kills: an image not built by vaibify refused for a list it never had."""
    recorderDocker = _DockerCommandRecorder(
        b"", bCopyAnswers=False, sCopyRefusal=S_DAEMON_FILE_ABSENT)
    _fnUseDocker(monkeypatch, recorderDocker)
    saRunArgs = containerManager.flistBuildRunArgs(
        _fConfigMinimal(), bDetached=True, dictPosture=DICT_BUILT_POSTURE)
    assert containerConfig.S_IMAGE_REPOSITORY_LIST_PATH not in " ".join(saRunArgs)
    assert recorderDocker.flistVerbs() == ["create", "cp", "rm"]


@pytest.mark.falsification
def test_a_copy_the_daemon_fails_for_another_reason_refuses_the_start(
    fixtureSeededProject, monkeypatch,
):
    """Kills: every failed copy read as "the image has no list"."""
    _fnUseDocker(monkeypatch, _DockerCommandRecorder(
        b"", bCopyAnswers=False,
        sCopyRefusal="Error response from daemon: No such container: readerId42\n"))
    with pytest.raises(RuntimeError) as errorRefused:
        containerManager.flistBuildRunArgs(
            _fConfigMinimal(), bDetached=True, dictPosture=DICT_BUILT_POSTURE)
    assert "was not started" in str(errorRefused.value)


@pytest.mark.falsification
def test_an_image_whose_list_is_a_symlink_is_refused(
    fixtureSeededProject, monkeypatch,
):
    """Kills: a link member read as the list (a host-side copy would follow it)."""
    _fnUseDocker(monkeypatch, _DockerCommandRecorder(
        _fbaTarOfSymlink("/etc/passwd")))
    with pytest.raises(RuntimeError) as errorRefused:
        containerManager.flistBuildRunArgs(
            _fConfigMinimal(), bDetached=True, dictPosture=DICT_BUILT_POSTURE)
    assert "not a single regular file" in str(errorRefused.value)


def test_a_restricted_start_reads_and_mounts_nothing(
    fixtureSeededProject, monkeypatch,
):
    recorderDocker = _DockerCommandRecorder(_fbaTarOfOneFile(S_IMAGE_LIST))
    _fnUseDocker(monkeypatch, recorderDocker)
    saRunArgs = containerManager.flistBuildRunArgs(
        _fConfigMinimal(), bDetached=True, dictPosture={
            "sMode": "restricted", "bWithCredentials": False,
            "sImageDigest": S_IMAGE_ID,
        })
    assert recorderDocker.listCommands == []
    assert containerConfig.S_IMAGE_REPOSITORY_LIST_PATH not in " ".join(saRunArgs)


@pytest.mark.falsification
def test_a_project_the_image_clones_keeps_the_baked_list(tmp_path, monkeypatch):
    """Kills: a start that drops the project's entry where nothing else provides it."""
    monkeypatch.setattr(registryManager, "fdictGetProject", lambda sName: {
        "sName": "testproj", "sMode": "container",
        "sDirectory": str(tmp_path / "aigreen"),
    })
    recorderDocker = _DockerCommandRecorder(_fbaTarOfOneFile(S_IMAGE_LIST))
    _fnUseDocker(monkeypatch, recorderDocker)
    saRunArgs = containerManager.flistBuildRunArgs(
        _fConfigMinimal(), bDetached=True, dictPosture=DICT_BUILT_POSTURE)
    assert recorderDocker.listCommands == []
    assert containerConfig.S_IMAGE_REPOSITORY_LIST_PATH not in " ".join(saRunArgs)


def test_the_written_list_is_readable_by_the_container_user():
    sPath = containerConfig.fsWriteRepositoryListForProject("testproj", S_IMAGE_LIST)
    assert stat.S_IMODE(os.stat(sPath).st_mode) == 0o644
    with open(sPath, encoding="utf-8") as fileList:
        assert fileList.read() == S_IMAGE_LIST


def test_a_project_name_that_could_leave_the_directory_is_refused():
    with pytest.raises(ValueError):
        containerConfig.fsWriteRepositoryListForProject("../escape", "")


# --- which lines are removed ------------------------------------------------


@pytest.mark.falsification
def test_only_the_reference_entry_landing_in_the_folder_is_removed():
    """Kills: a filter that drops by name alone, losing an install."""
    sInstalledHere = "aigreen|https://example.invalid/aigreen.git|main|pip_editable"
    sOther = "aigreenTools|https://example.invalid/tools.git|main|reference"
    sList = f"# comment naming aigreen\n{sInstalledHere}\n{sOther}\n"
    assert containerConfig.fsRemoveReferenceClonesOfDirectory(
        sList, "aigreen") == sList


@pytest.mark.falsification
def test_an_entry_moved_into_the_folder_is_removed():
    """Kills: a filter blind to the destination, whose move deletes the folder."""
    sMoved = "upstream|https://example.invalid/upstream.git|main|reference|aigreen"
    sList = f"{S_DEPENDENCY_LINE}\n{sMoved}\n"
    assert containerConfig.fsRemoveReferenceClonesOfDirectory(
        sList, "aigreen") == f"{S_DEPENDENCY_LINE}\n"


def test_names_are_compared_as_the_entrypoint_reads_them():
    """``read -r`` trims nothing, so a padded name clones somewhere else."""
    sPadded = "aigreen |https://example.invalid/aigreen.git|main|reference"
    assert containerConfig.fsRemoveReferenceClonesOfDirectory(
        sPadded + "\n", "aigreen") == sPadded + "\n"


def test_no_folder_name_leaves_the_list_untouched():
    assert containerConfig.fsRemoveReferenceClonesOfDirectory(
        S_IMAGE_LIST, "") == S_IMAGE_LIST


# --- the registry's record of a copied folder -------------------------------


@pytest.fixture
def fixtureIsolatedRegistry(tmp_path, monkeypatch):
    """Point the registry at ``tmp_path`` and register one host project."""
    sRegistryDirectory = str(tmp_path / "registry")
    os.makedirs(sRegistryDirectory)
    monkeypatch.setattr(registryManager, "_S_REGISTRY_DIRECTORY", sRegistryDirectory)
    monkeypatch.setattr(registryManager, "_S_REGISTRY_PATH",
                        os.path.join(sRegistryDirectory, "registry.json"))
    monkeypatch.setattr(registryManager, "_S_LOCK_PATH",
                        os.path.join(sRegistryDirectory, "registry.lock"))
    sDirectory = str(tmp_path / "aigreen")
    os.makedirs(sDirectory)
    with open(registryManager._S_REGISTRY_PATH, "w") as fileRegistry:
        json.dump({"listProjects": [{
            "sName": "aigreen", "sContainerName": "aigreen", "sMode": "host",
            "sDirectory": sDirectory,
            "sConfigPath": os.path.join(sDirectory, "vaibify.yml"),
        }]}, fileRegistry)
    return sDirectory


@pytest.mark.falsification
def test_a_conversion_that_copies_files_records_the_folder(fixtureIsolatedRegistry):
    """Kills: a conversion that forgets the folder was copied, once the copy lands."""
    registryManager.fnConvertProjectToContainer(
        "aigreen", "testproj", None, None, {"saRelativePaths": ["data"]})
    registryManager.fnSetPendingSeed("testproj", None)
    dictEntry = registryManager.fdictGetProject("testproj")
    assert registryManager.fsSeededWorkspaceDirectoryName(dictEntry) == "aigreen"


def test_a_conversion_that_copies_nothing_leaves_the_folder_to_the_image(
    fixtureIsolatedRegistry,
):
    registryManager.fnConvertProjectToContainer("aigreen", "testproj")
    dictEntry = registryManager.fdictGetProject("testproj")
    assert registryManager.fsSeededWorkspaceDirectoryName(dictEntry) == ""


def test_returning_to_the_host_forgets_the_copied_folder(fixtureIsolatedRegistry):
    registryManager.fnConvertProjectToContainer(
        "aigreen", "testproj", None, None, {"saRelativePaths": ["data"]})
    registryManager.fsReturnProjectToHost("testproj")
    dictEntry = registryManager.fdictGetProject("aigreen")
    assert registryManager.S_WORKSPACE_SEEDED_KEY not in dictEntry


def test_the_folder_name_is_the_one_the_seed_writes(tmp_path):
    """The seed route and the start ask the same function, through a symlink too."""
    os.makedirs(tmp_path / "realFolder")
    os.symlink(tmp_path / "realFolder", tmp_path / "linkFolder")
    dictEntry = {registryManager.S_WORKSPACE_SEEDED_KEY: True,
                 "sDirectory": str(tmp_path / "linkFolder") + os.sep}
    assert registryManager.fsSeededWorkspaceDirectoryName(dictEntry) == "realFolder"
