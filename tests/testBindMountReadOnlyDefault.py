"""Bind mounts are read-only unless an entry says ``readWrite: true``.

Source: ``vaibify/config/bindMountValidator.py``,
``vaibify/docker/containerManager.py``, ``vaibify/docker/bindMountDrift.py``
and ``vaibify/cli/doctorMountChecks.py``.

A host directory a container can write is a channel out of the
container, so the default turned around (2026-10-28 ruling): every
mount is read-only, ``readWrite: true`` is the only way to a writable
one, the retired ``readOnly`` key is refused by name, the deny list
grew to every directory a same-UID agent could use to run code on the
host later, and a read-write mount may not cover the project's own
``vaibify.yml``. A container created before an edit keeps its modes, so
the dashboard and the doctor say so from the same sentences.
"""

import os

import pytest

from vaibify.cli.doctorMountChecks import flistCheckBindMountModes
from vaibify.cli.preflightResult import (
    S_LEVEL_NOT_CHECKED, S_LEVEL_OK, S_LEVEL_WARN,
)
from vaibify.config import bindMountValidator
from vaibify.config.bindMountValidator import (
    BindMountValidationError, T_BIND_MOUNT_KEYS, fbBindMountIsReadWrite,
    fnValidateBindMount,
)
from vaibify.docker.bindMountDrift import flistDescribeBindMountDrift
from vaibify.docker.containerManager import _fsBuildBindMountSpec


@pytest.fixture
def sHome(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    return str(tmp_path)


def _fdictMount(sHome, sRelative, **dictExtra):
    sHostPath = os.path.join(sHome, sRelative)
    os.makedirs(sHostPath, exist_ok=True)
    dictMount = {"host": sHostPath, "container": "/mnt/data"}
    dictMount.update(dictExtra)
    return dictMount


# ---------------------------------------------------------------------------
# The schema
# ---------------------------------------------------------------------------


def testTheSchemaIsHostContainerAndReadWrite():
    assert T_BIND_MOUNT_KEYS == ("host", "container", "readWrite")


def testOnlyAnExplicitTrueIsReadWrite():
    assert fbBindMountIsReadWrite({"readWrite": True})
    assert not fbBindMountIsReadWrite({"readWrite": False})
    assert not fbBindMountIsReadWrite({})
    assert not fbBindMountIsReadWrite({"readOnly": False})


@pytest.mark.falsification
def testAMountIsReadOnlyUnlessItSaysReadWrite():
    """The composed ``--mount`` value carries ``readonly`` by default.

    Kills: inverting the readWrite test in ``_fsBuildBindMountSpec`` so
    the default mounts writable and ``readWrite: true`` mounts
    read-only.
    """
    dictMount = {"host": "/h/data", "container": "/mnt/data"}
    assert _fsBuildBindMountSpec(dictMount).endswith(",readonly")
    dictMount["readWrite"] = True
    assert not _fsBuildBindMountSpec(dictMount).endswith(",readonly")
    dictMount["readWrite"] = False
    assert _fsBuildBindMountSpec(dictMount).endswith(",readonly")


@pytest.mark.falsification
def testTheRetiredReadOnlyKeyIsRefusedByName(sHome):
    """The old key fails loudly, so a file cannot mount read-write by accident.

    Kills: accepting ``readOnly`` again in ``_fnRejectUnknownKeys``.
    """
    with pytest.raises(BindMountValidationError, match="readWrite: true"):
        fnValidateBindMount(_fdictMount(sHome, "data", readOnly=True))
    with pytest.raises(BindMountValidationError, match="remove 'readOnly'"):
        fnValidateBindMount(_fdictMount(sHome, "data", readOnly=False))


def testAnUnknownKeyIsRefused(sHome):
    with pytest.raises(BindMountValidationError, match="unknown key"):
        fnValidateBindMount(_fdictMount(sHome, "data", mode="rw"))


def testReadWriteMustBeABoolean(sHome):
    with pytest.raises(BindMountValidationError, match="true or false"):
        fnValidateBindMount(_fdictMount(sHome, "data", readWrite="yes"))
    fnValidateBindMount(_fdictMount(sHome, "data", readWrite=True))
    fnValidateBindMount(_fdictMount(sHome, "data", readWrite=False))


# ---------------------------------------------------------------------------
# The deny list
# ---------------------------------------------------------------------------

LIST_NEW_DENIED_HOME_PATHS = [
    ".vaibify", ".vaibify/bindings", ".claude", ".codex", ".gemini",
    ".config", ".config/pip", ".gitconfig", ".netrc", ".pypirc", ".npmrc",
    ".zshrc", ".zshenv", ".zprofile", ".zlogin", ".bashrc", ".bash_profile",
    ".profile", "Library/LaunchAgents", "Library/Keychains",
]


@pytest.mark.parametrize("sRelative", LIST_NEW_DENIED_HOME_PATHS)
def testEachProtectedHomePathIsRefused(sHome, sRelative):
    sHostPath = os.path.join(sHome, sRelative)
    if "." in os.path.basename(sRelative) and not sRelative.startswith(
        (".vaibify", ".claude", ".codex", ".gemini", ".config"),
    ):
        os.makedirs(os.path.dirname(sHostPath), exist_ok=True)
        with open(sHostPath, "w", encoding="utf-8") as fileNew:
            fileNew.write("")
    else:
        os.makedirs(sHostPath, exist_ok=True)
    with pytest.raises(BindMountValidationError, match="denied location"):
        fnValidateBindMount({"host": sHostPath, "container": "/mnt"})


@pytest.mark.falsification
def testAnAgentsHomeIsDeniedInEveryDirection(sHome):
    """Kills: dropping ``.claude`` from the home-relative deny list."""
    sAgentHome = os.path.join(sHome, ".claude")
    os.makedirs(os.path.join(sAgentHome, "projects"))
    for sMount in (sAgentHome, os.path.join(sAgentHome, "projects"), sHome):
        with pytest.raises(BindMountValidationError, match="denied location"):
            fnValidateBindMount({"host": sMount, "container": "/mnt"})


def testADataDirectoryBesideThemIsStillAllowed(sHome):
    fnValidateBindMount(_fdictMount(sHome, "datasets/run1"))


# ---------------------------------------------------------------------------
# A read-write mount may not cover the project's own directory
# ---------------------------------------------------------------------------


@pytest.mark.falsification
def testAReadWriteMountOverTheProjectDirectoryIsRefused(sHome):
    """A writable mount of the project directory, or an ancestor, is refused.

    Kills: the project-directory check in ``_fnRejectReadWriteOverProject``
    admitting every path.
    """
    sProject = os.path.join(sHome, "science", "project")
    os.makedirs(sProject)
    for sMount in (sProject, os.path.join(sHome, "science")):
        with pytest.raises(BindMountValidationError, match="vaibify.yml"):
            fnValidateBindMount(
                {"host": sMount, "container": "/mnt", "readWrite": True},
                sProjectDirectory=sProject,
            )


def testAReadOnlyMountOverTheProjectDirectoryAndAWritableChildAreAllowed(sHome):
    sProject = os.path.join(sHome, "science", "project")
    os.makedirs(os.path.join(sProject, "data"))
    fnValidateBindMount(
        {"host": sProject, "container": "/mnt"}, sProjectDirectory=sProject,
    )
    fnValidateBindMount(
        {"host": os.path.join(sProject, "data"), "container": "/mnt",
         "readWrite": True},
        sProjectDirectory=sProject,
    )


@pytest.mark.falsification
def testTheLoaderNamesTheRefusedEntryInItsOwnWords(tmp_path, monkeypatch):
    """A refused entry comes back with the validator's sentence, not a bare failure.

    Kills: the loader skipping its own bind-mount pass, so the refusal
    comes back without its reason.
    """
    from vaibify.config.projectConfig import fconfigLoadFromFile
    monkeypatch.setenv("HOME", str(tmp_path))
    sData = tmp_path / "data"
    sData.mkdir()
    pathConfig = tmp_path / "vaibify.yml"
    pathConfig.write_text(
        "projectName: demo\n"
        "bindMounts:\n"
        f"  - host: {sData}\n"
        "    container: /mnt/data\n"
        "    readOnly: true\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="remove 'readOnly'"):
        fconfigLoadFromFile(str(pathConfig))
    pathProject = tmp_path / "science" / "project"
    pathProject.mkdir(parents=True)
    pathNested = pathProject / "vaibify.yml"
    pathNested.write_text(
        "projectName: demo\n"
        "bindMounts:\n"
        f"  - host: {tmp_path / 'science'}\n"
        "    container: /mnt/project\n"
        "    readWrite: true\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="vaibify.yml declares"):
        fconfigLoadFromFile(str(pathNested))


# ---------------------------------------------------------------------------
# Drift between the file and the running container
# ---------------------------------------------------------------------------

LIST_DECLARED = [
    {"host": "/h/ro", "container": "/mnt/ro"},
    {"host": "/h/rw", "container": "/mnt/rw", "readWrite": True},
]


def _fjsonInspect(bRoActual, bRwActual, bIncludeRw=True):
    listMounts = [
        {"Type": "bind", "Destination": "/mnt/ro", "RW": bRoActual},
        {"Type": "volume", "Destination": "/workspace", "RW": True},
    ]
    if bIncludeRw:
        listMounts.append(
            {"Type": "bind", "Destination": "/mnt/rw", "RW": bRwActual},
        )
    return {"Mounts": listMounts}


def testAMatchingContainerYieldsNoFinding():
    assert flistDescribeBindMountDrift(LIST_DECLARED, _fjsonInspect(False, True)) == []


def testAnUninspectableContainerClaimsNothing():
    assert flistDescribeBindMountDrift(LIST_DECLARED, {}) == []


def testEachMismatchedModeIsNamedWithBothSides():
    listLines = flistDescribeBindMountDrift(LIST_DECLARED, _fjsonInspect(True, False))
    assert len(listLines) == 2
    assert "/mnt/ro as read-only" in listLines[0]
    assert "mounts it read-write" in listLines[0]
    assert "/mnt/rw as read-write" in listLines[1]
    assert "mounts it read-only" in listLines[1]
    assert all("vaibify restart" in sLine for sLine in listLines)


def testADeclaredMountTheContainerLacksIsAFinding():
    listLines = flistDescribeBindMountDrift(
        LIST_DECLARED, _fjsonInspect(False, True, bIncludeRw=False),
    )
    assert len(listLines) == 1
    assert "created without it" in listLines[0]


def testTheDoctorRendersTheSameSentences():
    from types import SimpleNamespace
    config = SimpleNamespace(listBindMounts=LIST_DECLARED)
    listResults = flistCheckBindMountModes(config, {})
    assert [r.sLevel for r in listResults] == [S_LEVEL_NOT_CHECKED]
    listResults = flistCheckBindMountModes(config, _fjsonInspect(False, True))
    assert [r.sLevel for r in listResults] == [S_LEVEL_OK]
    listResults = flistCheckBindMountModes(config, _fjsonInspect(True, True))
    assert [r.sLevel for r in listResults] == [S_LEVEL_WARN]
    assert listResults[0].sMessage == flistDescribeBindMountDrift(
        LIST_DECLARED, _fjsonInspect(True, True),
    )[0]
    assert listResults[0].sCommand == "vaibify restart"


def testTheDenyListStillHoldsEveryEntryItHad():
    """The widening replaced three ``.vaibify`` children with the parent."""
    setDenied = set(bindMountValidator._LIST_HOME_RELATIVE_DENY_PREFIXES)
    assert {".ssh", ".aws", ".gnupg", ".docker", ".kube", ".vaibify",
            ".config"} <= setDenied
    assert not any(sEntry.startswith(".vaibify/") for sEntry in setDenied)


# ---------------------------------------------------------------------------
# The live witness: the daemon refuses a write into a default mount
# ---------------------------------------------------------------------------


@pytest.fixture
def sSharedHostPath():
    """A directory the daemon's VM can see: under the home, not the temp root.

    Colima and Docker Desktop share the home directory with the VM and
    nothing else, so a pytest temp directory is "bind source path does
    not exist" to the daemon.
    """
    import shutil
    import tempfile
    sPath = tempfile.mkdtemp(
        prefix=".vaibifyMountWitness", dir=os.path.expanduser("~"),
    )
    try:
        yield sPath
    finally:
        shutil.rmtree(sPath, ignore_errors=True)


@pytest.mark.docker_live
def testAWriteIntoADefaultMountFailsWithAReadOnlyFilesystem(sSharedHostPath):
    """The composed ``--mount`` value really is read-only inside the container."""
    import subprocess
    from tests.liveContainerLabels import flistLabelArguments
    from tests.testDockerConnectionLive import fnRequireDaemonReachable
    fnRequireDaemonReachable()
    sHostPath = os.path.join(sSharedHostPath, "data")
    os.makedirs(sHostPath)
    dictMount = {"host": sHostPath, "container": "/mnt/data"}
    processReadOnly = subprocess.run(
        ["docker", "run", "--rm", *flistLabelArguments(),
         "--mount", _fsBuildBindMountSpec(dictMount),
         "alpine:3.20", "sh", "-c", "touch /mnt/data/written"],
        capture_output=True, text=True, timeout=120,
    )
    assert processReadOnly.returncode != 0
    assert "Read-only file system" in processReadOnly.stderr
    assert not os.path.exists(os.path.join(sHostPath, "written"))
    dictMount["readWrite"] = True
    processWritable = subprocess.run(
        ["docker", "run", "--rm", *flistLabelArguments(),
         "--mount", _fsBuildBindMountSpec(dictMount),
         "alpine:3.20", "sh", "-c", "touch /mnt/data/written"],
        capture_output=True, text=True, timeout=120,
    )
    assert processWritable.returncode == 0, processWritable.stderr
    assert os.path.exists(os.path.join(sHostPath, "written"))
