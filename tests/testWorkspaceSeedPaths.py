"""The workspace seed's host-side guards.

The seed is the first path by which content on the RESEARCHER's own
machine reaches a container, so the question "which host files may this
name?" is the whole security surface. These drive the two guards
directly rather than over HTTP because both are pure functions of the
filesystem: a real directory, real symlinks, real ``..`` -- no doubles
anywhere, so a guard that only appears to hold cannot pass.

The paths are HOST paths, so every assertion here is about ``os.path``
semantics. The container-path helper that sits beside these in
``fileRoutes`` is ``posixpath`` and would answer differently on a host
whose separator is not "/" -- which is exactly why the seed does not
reuse it.
"""

import os

import pytest
from fastapi import HTTPException

from vaibify.gui.routes.fileRoutes import (
    _flistResolveSeedPaths,
    _fsRequireHostDirectoryForSeed,
)


S_PROJECT_NAME = "seedLaneProject"


@pytest.fixture
def sProjectDirectory(tmp_path):
    """A project directory holding one file, one subdirectory, one repo."""
    pathProject = tmp_path / "seedLaneDirectory"
    (pathProject / "sub").mkdir(parents=True)
    (pathProject / "analysis.py").write_text("print('hello')\n")
    (pathProject / "sub" / "data.json").write_text("{}")
    (pathProject / ".git").mkdir()
    (pathProject / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (tmp_path / "secrets.txt").write_text("not yours\n")
    return str(pathProject.resolve())


def testTheSelectedEntriesResolveToAbsoluteHostPaths(sProjectDirectory):
    """The ordinary case: names in, absolute paths out, order kept."""
    listResolved = _flistResolveSeedPaths(
        sProjectDirectory, ["analysis.py", "sub", ".git"],
    )
    assert listResolved == [
        os.path.join(sProjectDirectory, "analysis.py"),
        os.path.join(sProjectDirectory, "sub"),
        os.path.join(sProjectDirectory, ".git"),
    ]


@pytest.mark.falsification
def testARelativeEscapeIsRefused(sProjectDirectory):
    """``..`` cannot nominate a file the researcher never offered.

    The request body names paths RELATIVE to the registered directory,
    which reads as a containment guarantee -- and would be one only if
    something checked. Joining a relative path onto a root and opening
    the result is the traversal defect in its textbook form; the seed
    resolves first and proves containment second.

    Kills: dropping the containment check in _flistResolveSeedPaths,
    which lets any file the hub's user can read be copied into a
    container.
    """
    with pytest.raises(HTTPException) as excinfo:
        _flistResolveSeedPaths(
            sProjectDirectory, ["../secrets.txt"],
        )
    assert excinfo.value.status_code == 403
    assert "outside the project" in str(excinfo.value.detail)


@pytest.mark.falsification
def testASymlinkPointingOutOfTheProjectIsRefused(
    sProjectDirectory, tmp_path,
):
    """Containment is proven after resolution, not before it.

    A symlink is the case that separates a real guard from a textual
    one: "escape.txt" contains no ``..`` and is not absolute, so any
    check on the STRING admits it. Only resolving the link and asking
    where it actually lands refuses it.

    Kills: checking the relative path's text instead of the resolved
    path, and dropping the containment check outright.
    """
    os.symlink(
        str(tmp_path / "secrets.txt"),
        os.path.join(sProjectDirectory, "escape.txt"),
    )
    with pytest.raises(HTTPException) as excinfo:
        _flistResolveSeedPaths(sProjectDirectory, ["escape.txt"])
    assert excinfo.value.status_code == 403


def testAnAbsolutePathIsRefused(sProjectDirectory, tmp_path):
    """An absolute path replaces the root when joined, so it is refused."""
    with pytest.raises(HTTPException) as excinfo:
        _flistResolveSeedPaths(
            sProjectDirectory, [str(tmp_path / "secrets.txt")],
        )
    assert excinfo.value.status_code == 403


def testAVanishedEntryIsRefusedRatherThanSkipped(sProjectDirectory):
    """A file deleted between choosing and copying is an error.

    Skipping it silently would report a successful copy of a file the
    container does not have, which is the dashboard-lying-about-state
    failure this repository refuses everywhere else.
    """
    with pytest.raises(HTTPException) as excinfo:
        _flistResolveSeedPaths(sProjectDirectory, ["neverExisted.py"])
    assert excinfo.value.status_code == 404


def testAnEmptySelectionIsRefused(sProjectDirectory):
    """Copying nothing is a mistake, not a no-op worth reporting green."""
    with pytest.raises(HTTPException) as excinfo:
        _flistResolveSeedPaths(sProjectDirectory, [])
    assert excinfo.value.status_code == 400


def _fnRegisterProject(tmp_path, monkeypatch, sMode):
    """Register one project in an isolated registry; return its directory."""
    from vaibify.config import registryManager
    sRegistryDirectory = str(tmp_path / ".vaibify")
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_DIRECTORY", sRegistryDirectory,
    )
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_PATH",
        os.path.join(sRegistryDirectory, "registry.json"),
    )
    sProjectDirectory = str(tmp_path / S_PROJECT_NAME)
    os.makedirs(sProjectDirectory, exist_ok=True)
    with open(
        os.path.join(sProjectDirectory, "vaibify.yml"), "w",
    ) as fileConfig:
        fileConfig.write(f"projectName: {S_PROJECT_NAME}\n")
    registryManager.fnAddProject(sProjectDirectory, sMode=sMode)
    return sProjectDirectory


def testAContainerProjectResolvesToItsRegisteredDirectory(
    tmp_path, monkeypatch,
):
    """The destination comes from the REGISTRY, never from the request."""
    sProjectDirectory = _fnRegisterProject(
        tmp_path, monkeypatch, "container",
    )
    assert _fsRequireHostDirectoryForSeed(S_PROJECT_NAME) == (
        os.path.realpath(sProjectDirectory)
    )


@pytest.mark.falsification
def testAHostProjectIsRefusedRatherThanSeeded(tmp_path, monkeypatch):
    """A host project has nowhere to copy TO, and says so.

    Its workspace IS the researcher's directory, so every reading of
    "copy these in" either duplicates the tree onto itself or
    overwrites the originals with themselves. Refusing names that;
    proceeding would run a copy over the researcher's live files.

    Kills: dropping the host-project refusal from
    _fsRequireHostDirectoryForSeed.
    """
    _fnRegisterProject(tmp_path, monkeypatch, "host")
    with pytest.raises(HTTPException) as excinfo:
        _fsRequireHostDirectoryForSeed(S_PROJECT_NAME)
    assert excinfo.value.status_code == 409
    assert "runs on this machine" in str(excinfo.value.detail)


def testAnUnregisteredProjectIsRefused(tmp_path, monkeypatch):
    """A name no registry knows cannot name a directory to read."""
    _fnRegisterProject(tmp_path, monkeypatch, "container")
    with pytest.raises(HTTPException) as excinfo:
        _fsRequireHostDirectoryForSeed("noSuchProject")
    assert excinfo.value.status_code == 404
