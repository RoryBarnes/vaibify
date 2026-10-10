"""Tests for the shared ephemeral-file root (audit M2)."""

import os
import stat
import time

import pytest

from vaibify.config.ephemeralStore import (
    F_SECRET_FILE_GRACE_SECONDS,
    fiReleaseSecretSources,
    fiSweepUnmountedEphemeralFiles,
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
# The credential-file sweep. Every file written here holds a live token
# or a path to one; 18 mounted-secret files from April were still
# readable in July because nothing ever retired them, and 11 more
# accumulated while a later sweep silently never ran.
# ---------------------------------------------------------------------


def _fsAgeOneFile(sRoot, sName, fAgeSeconds):
    """Create a file under sRoot and backdate it by fAgeSeconds."""
    sPath = os.path.join(sRoot, sName)
    with open(sPath, "w") as fileHandle:
        fileHandle.write("ghp_liveTokenShapedValue")
    fMtime = time.time() - fAgeSeconds
    os.utime(sPath, (fMtime, fMtime))
    return sPath


F_PAST_GRACE = F_SECRET_FILE_GRACE_SECONDS + 60


@pytest.mark.falsification
def test_sweep_removes_stale_credential_files(monkeypatch, tmp_path):
    """Files older than the grace that nothing mounts are deleted.

    Kills: ``os.unlink(sPath)`` in ``ephemeralStore._fiUnlinkQuietly``
    replaced by ``pass``, i.e. the sweep reverted to a no-op that
    reports success while the tokens stay on disk.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    sRoot = fsGetEphemeralRoot()
    sStale = _fsAgeOneFile(sRoot, "vc_secret_gh_token_old.tmp", F_PAST_GRACE)
    sFresh = _fsAgeOneFile(sRoot, "vc_secret_gh_token_new.tmp", 0)

    assert fiSweepUnmountedEphemeralFiles(set()) == 1

    assert not os.path.exists(sStale)
    assert os.path.exists(sFresh), "a file inside the grace is left alone"


def test_sweep_retires_stale_askpass_scripts(monkeypatch, tmp_path):
    """Askpass helpers point at credentials and are swept too."""
    monkeypatch.setenv("HOME", str(tmp_path))
    sRoot = fsGetEphemeralRoot()
    sStale = _fsAgeOneFile(sRoot, "vc_gh_askpass_old.py", F_PAST_GRACE)

    fiSweepUnmountedEphemeralFiles(set())

    assert not os.path.exists(sStale)


def test_sweep_honours_an_explicit_grace(monkeypatch, tmp_path):
    """A caller may retire files younger than the default grace."""
    monkeypatch.setenv("HOME", str(tmp_path))
    sRoot = fsGetEphemeralRoot()
    sPath = _fsAgeOneFile(sRoot, "vc_secret_gh_token_x.tmp", 120)

    fiSweepUnmountedEphemeralFiles(set(), fGraceSeconds=60)

    assert not os.path.exists(sPath)


def test_sweep_leaves_subdirectories_and_symlinks_alone(monkeypatch, tmp_path):
    """Only regular files are candidates; a stale directory survives."""
    monkeypatch.setenv("HOME", str(tmp_path))
    sRoot = fsGetEphemeralRoot()
    sSubdirectory = os.path.join(sRoot, "keepalive")
    os.makedirs(sSubdirectory)
    sTarget = os.path.join(tmp_path, "outside.txt")
    with open(sTarget, "w") as fileHandle:
        fileHandle.write("keep")
    sLink = os.path.join(sRoot, "vc_secret_link.tmp")
    os.symlink(sTarget, sLink)
    fMtime = time.time() - F_PAST_GRACE
    os.utime(sSubdirectory, (fMtime, fMtime))
    os.utime(sLink, (fMtime, fMtime), follow_symlinks=False)

    assert fiSweepUnmountedEphemeralFiles(set()) == 0

    assert os.path.isdir(sSubdirectory)
    assert os.path.islink(sLink) and os.path.exists(sTarget)


def test_sweep_skips_the_council_staged_copies(monkeypatch, tmp_path):
    """A council copy has its own lock-aware sweep; this one never touches it."""
    monkeypatch.setenv("HOME", str(tmp_path))
    sRoot = fsGetEphemeralRoot()
    sCopy = _fsAgeOneFile(
        sRoot, "vc_secret_claudeCouncilAccessToken_abc.tmp", F_PAST_GRACE)
    sOther = _fsAgeOneFile(sRoot, "vc_secret_gh_token_abc.tmp", F_PAST_GRACE)

    iRemoved = fiSweepUnmountedEphemeralFiles(
        set(), tExcludedPrefixes=("vc_secret_claudeCouncilAccessToken_",))

    assert iRemoved == 1
    assert os.path.exists(sCopy)
    assert not os.path.exists(sOther)


def test_sweep_raises_when_the_root_is_unreadable(monkeypatch, tmp_path):
    """An unreadable root is a failure the reaper records, not a quiet 0."""
    monkeypatch.setenv("HOME", str(tmp_path))
    fsGetEphemeralRoot()
    monkeypatch.setattr(
        "vaibify.config.ephemeralStore.os.listdir",
        lambda sPath: (_ for _ in ()).throw(PermissionError("denied")),
    )
    with pytest.raises(PermissionError):
        fiSweepUnmountedEphemeralFiles(set())


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

    Kills: the ``if sPath in setMountedSources: continue`` guard in
    ``fiSweepUnmountedEphemeralFiles`` neutralized.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    sRoot = fsGetEphemeralRoot()
    sMounted = _fsAgeOneFile(sRoot, "vc_secret_gh_token_mounted.tmp", F_PAST_GRACE)
    sOrphan = _fsAgeOneFile(sRoot, "vc_secret_gh_token_orphan.tmp", F_PAST_GRACE)

    fiSweepUnmountedEphemeralFiles({sMounted})

    assert os.path.exists(sMounted), "a mounted secret was deleted"
    assert not os.path.exists(sOrphan), "an orphan should be swept"


class _ConnectionUnreachable:
    """A DockerConnection-shaped double whose enumeration cannot answer."""

    def fsetListMountSourcesOfAllContainers(self):
        return None


class _ConnectionMounting:
    """A DockerConnection-shaped double that reports the given sources."""

    def __init__(self, setSources):
        self._setSources = set(setSources)

    def fsetListMountSourcesOfAllContainers(self):
        return set(self._setSources)


def test_mounted_host_paths_returns_none_on_enumeration_failure():
    """An unreachable daemon must be distinguishable from 'no mounts'."""
    from vaibify.gui.routes import syncRoutes
    assert syncRoutes._fsetMountedHostPaths(
        {"docker": _ConnectionUnreachable()}) is None
    assert syncRoutes._fsetMountedHostPaths({"docker": None}) is None


def test_mounted_host_paths_returns_the_sources_on_success():
    """A reachable daemon returns a set (empty or not), never None."""
    from vaibify.gui.routes import syncRoutes
    assert syncRoutes._fsetMountedHostPaths(
        {"docker": _ConnectionMounting({"/host/secret"})}
    ) == {"/host/secret"}


@pytest.mark.falsification
def test_the_sweep_enumerates_mounts_through_the_real_connection_class():
    """The sweep asks a REAL ``DockerConnection`` for the mounts.

    The previous sweep called ``.containers.list`` on the connection, an
    attribute the class has never had, inside a swallowed except; its
    unit test passed against a fake shaped like the Docker SDK client.
    This test builds the real class around a stub SDK client, so any
    attribute the class lacks raises here.

    Kills: ``_fsetMountedHostPaths`` reverted to walking
    ``connectionDocker.containers.list(all=True)`` itself.
    """
    from types import SimpleNamespace
    from vaibify.docker.dockerConnection import DockerConnection
    from vaibify.gui.routes import syncRoutes

    class _SdkContainers:
        @staticmethod
        def list(all=False):  # noqa: A002 -- the SDK's own signature
            assert all is True, "stopped containers must be enumerated"
            return [SimpleNamespace(attrs={"Mounts": [
                {"Source": "/host/mounted"}, {"Source": ""}]})]

    connectionReal = DockerConnection.__new__(DockerConnection)
    connectionReal._clientDocker = SimpleNamespace(containers=_SdkContainers())
    connectionReal._dictContainers = {}
    assert syncRoutes._fsetMountedHostPaths(
        {"docker": connectionReal}) == {"/host/mounted"}


def test_sweep_is_forbidden_when_the_daemon_is_unreachable(
    tmp_path, monkeypatch,
):
    """When mount enumeration fails, the sweep must delete nothing.

    An empty protected set protects nothing, so the sweep would delete
    every stale file -- including one a live container still mounts,
    whose loss leaves the container permanently unstartable. An
    enumeration failure must forbid the sweep entirely, not proceed with
    nothing protected.

    Kills: in syncRoutes.fdictSweepEphemeralSecrets, neutralize the
    ``if setMounted is None:`` guard, so the sweep proceeds with nothing
    protected when the daemon is unreachable.
    """
    from fastapi import FastAPI
    from vaibify.gui.routes import syncRoutes

    monkeypatch.setenv("HOME", str(tmp_path))
    sRoot = fsGetEphemeralRoot()
    sStale = _fsAgeOneFile(sRoot, "vc_secret_gh_token_stale.tmp", F_PAST_GRACE)

    app = FastAPI()
    app.state.listLifespanStartup = []
    syncRoutes._fnRegisterEphemeralSecretSweep(
        app, {"docker": _ConnectionUnreachable()},
    )
    [(_, fdictReaper)] = app.state.listRemnantReapers
    dictOutcome = fdictReaper({"docker": _ConnectionUnreachable()})

    assert dictOutcome["sOutcome"] == "forbidden", dictOutcome
    assert dictOutcome["sRemedy"]
    assert os.path.exists(sStale), (
        "the sweep deleted a credential while the daemon was unreachable"
    )


def test_the_reaper_reports_how_many_files_it_removed(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from vaibify.gui.routes import syncRoutes

    monkeypatch.setenv("HOME", str(tmp_path))
    sRoot = fsGetEphemeralRoot()
    sMounted = _fsAgeOneFile(sRoot, "vc_secret_gh_token_mounted.tmp", F_PAST_GRACE)
    _fsAgeOneFile(sRoot, "vc_secret_gh_token_orphan.tmp", F_PAST_GRACE)
    _fsAgeOneFile(sRoot, "vc_secret_codexCouncilAccessToken_x.tmp", F_PAST_GRACE)
    app = FastAPI()
    app.state.listLifespanStartup = []
    syncRoutes._fnRegisterEphemeralSecretSweep(app, {})
    [(_, fdictReaper)] = app.state.listRemnantReapers
    dictOutcome = fdictReaper({"docker": _ConnectionMounting({sMounted})})
    assert dictOutcome == {"sOutcome": "ran", "iRemoved": 1,
                           "sReason": "", "sRemedy": ""}
    assert os.path.exists(sMounted)


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
    listNames = [sName for sName, _ in app.state.listRemnantReapers]
    assert "ephemeralSecretFiles" in listNames


# ---------------------------------------------------------------------
# Release at removal: a token file lives exactly as long as some
# container mounts it.
# ---------------------------------------------------------------------


def test_release_deletes_only_unshared_secret_sources(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    sRoot = fsGetEphemeralRoot()
    sOnlyMine = _fsAgeOneFile(sRoot, "vc_secret_gh_token_a.tmp", 0)
    sShared = _fsAgeOneFile(sRoot, "vc_secret_gh_token_b.tmp", 0)
    sNotASecret = _fsAgeOneFile(sRoot, "vc_gh_askpass_c.py", 0)
    sOutsideRoot = os.path.join(tmp_path, "vc_secret_elsewhere.tmp")
    with open(sOutsideRoot, "w") as fileHandle:
        fileHandle.write("not ours to delete")

    iReleased = fiReleaseSecretSources(
        [sOnlyMine, sShared, sNotASecret, sOutsideRoot, "/workspace"],
        {sShared, "/some/volume"},
    )

    assert iReleased == 1
    assert not os.path.exists(sOnlyMine)
    assert os.path.exists(sShared), "a file another container mounts survives"
    assert os.path.exists(sNotASecret), "only secret files are released here"
    assert os.path.exists(sOutsideRoot), "nothing outside the root is touched"
