"""Tests for the shared ephemeral-file root (audit M2)."""

import os
import stat
import time

import pytest

from vaibify.config.ephemeralStore import (
    F_STALE_EPHEMERAL_AGE_SECONDS,
    fnSweepStaleEphemeralFiles,
    fsGetEphemeralRoot,
)


def test_root_lives_under_user_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    sRoot = fsGetEphemeralRoot()
    assert sRoot.startswith(str(tmp_path))
    assert sRoot.endswith(os.path.join(".vaibify", "tmp"))


def test_root_is_mode_0700(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    sRoot = fsGetEphemeralRoot()
    iMode = stat.S_IMODE(os.stat(sRoot).st_mode)
    assert iMode == 0o700


def test_root_is_idempotent(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    sFirst = fsGetEphemeralRoot()
    sSecond = fsGetEphemeralRoot()
    assert sFirst == sSecond


def test_secret_manager_temp_dir_uses_ephemeral_root(monkeypatch, tmp_path):
    """secretManager._fsGetTempDirectory routes through the shared root."""
    monkeypatch.setenv("HOME", str(tmp_path))
    from vaibify.config.secretManager import _fsGetTempDirectory
    sDir = _fsGetTempDirectory()
    assert sDir.startswith(str(tmp_path))
    assert sDir.endswith(os.path.join(".vaibify", "tmp"))


def test_askpass_helper_writes_under_ephemeral_root(monkeypatch, tmp_path):
    """askpassHelper drops scripts under ~/.vaibify/tmp on Linux too."""
    monkeypatch.setenv("HOME", str(tmp_path))
    from vaibify.reproducibility.askpassHelper import (
        fsWriteExecutableScript,
    )
    sScriptPath = fsWriteExecutableScript(
        "print('ok')\n", "vc_test_askpass_",
    )
    try:
        assert sScriptPath.startswith(str(tmp_path))
    finally:
        os.remove(sScriptPath)


def test_overleaf_write_token_file_uses_ephemeral_root(monkeypatch, tmp_path):
    """overleafSync._fsWriteTokenFile drops the token under ~/.vaibify/tmp."""
    monkeypatch.setenv("HOME", str(tmp_path))
    from vaibify.reproducibility.overleafSync import _fsWriteTokenFile
    sTokenPath = _fsWriteTokenFile("ghp_fake")
    try:
        assert sTokenPath.startswith(str(tmp_path))
    finally:
        os.remove(sTokenPath)


# ---------------------------------------------------------------------
# Stale-credential sweep. Every file written here holds a live token or
# a path to one; 18 mounted-secret files from April were still readable
# in July because nothing ever retired them.
# ---------------------------------------------------------------------


def _fsAgeOneFile(sRoot, sName, fAgeSeconds):
    """Create a file under sRoot and backdate it by fAgeSeconds."""
    sPath = os.path.join(sRoot, sName)
    with open(sPath, "w") as fileHandle:
        fileHandle.write("ghp_liveTokenShapedValue")
    fMtime = time.time() - fAgeSeconds
    os.utime(sPath, (fMtime, fMtime))
    return sPath


@pytest.mark.falsification
def test_sweep_removes_stale_credential_files(monkeypatch, tmp_path):
    """Files older than the cutoff are deleted; fresh ones survive.

    Kills: ``os.remove(os.path.join(sRoot, sName))`` in
    ``fnSweepStaleEphemeralFiles`` replaced by ``pass``, i.e. the sweep
    reverted to a no-op that reports success while the tokens stay on
    disk.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    sRoot = fsGetEphemeralRoot()
    sStale = _fsAgeOneFile(
        sRoot, "vc_secret_gh_token_old.tmp",
        F_STALE_EPHEMERAL_AGE_SECONDS + 60,
    )
    sFresh = _fsAgeOneFile(sRoot, "vc_secret_gh_token_new.tmp", 0)

    fnSweepStaleEphemeralFiles()

    assert not os.path.exists(sStale)
    assert os.path.exists(sFresh)


def test_sweep_retires_stale_askpass_scripts(monkeypatch, tmp_path):
    """Askpass helpers point at credentials and are swept too."""
    monkeypatch.setenv("HOME", str(tmp_path))
    sRoot = fsGetEphemeralRoot()
    sStale = _fsAgeOneFile(
        sRoot, "vc_gh_askpass_old.py",
        F_STALE_EPHEMERAL_AGE_SECONDS + 60,
    )

    fnSweepStaleEphemeralFiles()

    assert not os.path.exists(sStale)


def test_sweep_honours_an_explicit_age_cutoff(monkeypatch, tmp_path):
    """A caller may retire files younger than the default cutoff."""
    monkeypatch.setenv("HOME", str(tmp_path))
    sRoot = fsGetEphemeralRoot()
    sPath = _fsAgeOneFile(sRoot, "vc_secret_gh_token_x.tmp", 120)

    fnSweepStaleEphemeralFiles(fMaxAgeSeconds=60)

    assert not os.path.exists(sPath)


def test_sweep_leaves_subdirectories_alone(monkeypatch, tmp_path):
    """Only regular files are candidates; a stale directory survives."""
    monkeypatch.setenv("HOME", str(tmp_path))
    sRoot = fsGetEphemeralRoot()
    sSubdirectory = os.path.join(sRoot, "keepalive")
    os.makedirs(sSubdirectory)
    fMtime = time.time() - (F_STALE_EPHEMERAL_AGE_SECONDS + 60)
    os.utime(sSubdirectory, (fMtime, fMtime))

    fnSweepStaleEphemeralFiles()

    assert os.path.isdir(sSubdirectory)


def test_sweep_never_raises_when_the_root_is_unreadable(
    monkeypatch, tmp_path,
):
    """A sweep failure must never block a container start."""
    monkeypatch.setenv("HOME", str(tmp_path))
    fsGetEphemeralRoot()
    monkeypatch.setattr(
        "vaibify.config.ephemeralStore.os.listdir",
        lambda sPath: (_ for _ in ()).throw(PermissionError("denied")),
    )
    fnSweepStaleEphemeralFiles()


def test_hub_startup_registers_the_credential_sweep():
    """The sweep has a production driver, not just a definition."""
    from fastapi import FastAPI
    from vaibify.gui.routes import syncRoutes

    app = FastAPI()
    app.state.listLifespanStartup = []
    app.state.listLifespanShutdown = []
    syncRoutes.fnRegisterAll(
        app,
        {
            "workflows": {}, "paths": {},
            "require": lambda *aArgs: None,
            "save": lambda sId, dictWf: None,
            "docker": object(),
        },
    )
    listNames = [
        getattr(fnHook, "__name__", "")
        for fnHook in app.state.listLifespanStartup
    ]
    assert "fnSweepAtStartup" in listNames


@pytest.mark.falsification
def test_sweep_spares_a_stale_file_a_container_still_mounts(
    tmp_path, monkeypatch,
):
    """A mounted secret must survive the sweep whatever its age.

    A bind-mounted secret lives as long as the container that mounts
    it, which outlives any number of hub restarts. Deleting the source
    leaves the container permanently unstartable -- Docker fails the
    mount and creates a directory stub where the file was. Observed on
    a real machine: sweeping an April-dated token broke a container
    that had mounted it.

    Kills: in ephemeralStore.fnSweepStaleEphemeralFiles, drop the
    ``if sPath in setProtected: continue`` guard.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    sRoot = fsGetEphemeralRoot()
    os.makedirs(sRoot, exist_ok=True)
    sMounted = os.path.join(sRoot, "vc_secret_gh_token_mounted.tmp")
    sOrphan = os.path.join(sRoot, "vc_secret_gh_token_orphan.tmp")
    for sPath in (sMounted, sOrphan):
        with open(sPath, "w") as fileHandle:
            fileHandle.write("token")
        os.utime(sPath, (0, 0))

    fnSweepStaleEphemeralFiles(setProtectedPaths={sMounted})

    assert os.path.exists(sMounted), "a mounted secret was deleted"
    assert not os.path.exists(sOrphan), "an orphan should be swept"


class _FailingDocker:
    """A docker client whose container enumeration raises."""

    class containers:
        @staticmethod
        def list(all=False):  # noqa: A002 — matches docker SDK signature
            raise RuntimeError("daemon unreachable")


def test_mounted_host_paths_returns_none_on_enumeration_failure():
    """An unreachable daemon must be distinguishable from 'no mounts'.

    Returning the empty set on failure is the destructive direction: the
    caller cannot tell it apart from 'enumerated, nothing mounted' and so
    proceeds with nothing protected. None means 'reachability unknown.'
    """
    from vaibify.gui.routes import syncRoutes
    assert syncRoutes._fsetMountedHostPaths({"docker": _FailingDocker()}) is None


def test_mounted_host_paths_returns_the_sources_on_success():
    """A reachable daemon returns a set (empty or not), never None."""
    from vaibify.gui.routes import syncRoutes

    class _Container:
        attrs = {"Mounts": [{"Source": "/host/secret"}]}

    class _Docker:
        class containers:
            @staticmethod
            def list(all=False):  # noqa: A002
                return [_Container()]

    assert syncRoutes._fsetMountedHostPaths(
        {"docker": _Docker()}
    ) == {"/host/secret"}


@pytest.mark.falsification
def test_sweep_is_forbidden_when_the_daemon_is_unreachable(
    tmp_path, monkeypatch,
):
    """When mount enumeration fails, the sweep must delete nothing.

    An empty protected set protects nothing, so the sweep would delete
    every stale file -- including one a live container still mounts,
    whose loss leaves the container permanently unstartable. An
    enumeration failure must forbid the sweep entirely, not proceed with
    nothing protected.

    Kills: in syncRoutes.fnSweepAtStartup, neutralize the
    ``if setMounted is None: return`` guard, so the sweep proceeds with
    nothing protected when the daemon is unreachable.
    """
    from fastapi import FastAPI
    from vaibify.gui.routes import syncRoutes

    monkeypatch.setenv("HOME", str(tmp_path))
    sRoot = fsGetEphemeralRoot()
    os.makedirs(sRoot, exist_ok=True)
    sStale = os.path.join(sRoot, "vc_secret_gh_token_stale.tmp")
    with open(sStale, "w") as fileHandle:
        fileHandle.write("token")
    os.utime(sStale, (0, 0))  # ancient — well past the stale cutoff

    app = FastAPI()
    app.state.listLifespanStartup = []
    syncRoutes._fnRegisterEphemeralSecretSweep(
        app, {"docker": _FailingDocker()},
    )
    for fnHook in app.state.listLifespanStartup:
        fnHook(app)

    assert os.path.exists(sStale), (
        "the sweep deleted a credential while the daemon was unreachable"
    )
