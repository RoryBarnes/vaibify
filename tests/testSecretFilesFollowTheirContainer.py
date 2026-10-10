"""A removed container's secret files go with it, on proof of removal.

Each container start writes a fresh credential file, and nothing
deleted the old one when the container went away, so plaintext token
files that no container mounted accumulated on disk. The removal paths
now read the container's mounts before
``docker rm``, confirm with the daemon that the container is gone, and
release the sources no surviving container mounts. Every proof step is
driven here through the CLI probe seam the module uses throughout.
"""

import os

from vaibify.config.ephemeralStore import fsGetEphemeralRoot
from vaibify.docker import containerManager


def _fsWriteSecret(sRoot, sName):
    sPath = os.path.join(sRoot, sName)
    with open(sPath, "w") as fileHandle:
        fileHandle.write("ghp_liveTokenShapedValue")
    return sPath


def _fnStubProbes(monkeypatch, listMountSources, dictPresence, setSurviving):
    monkeypatch.setattr(
        containerManager, "_flistMountSourcesOfContainer",
        lambda sIdentifier: list(listMountSources))
    monkeypatch.setattr(
        containerManager, "fdictProbeContainerPresence",
        lambda sName: dict(dictPresence))
    monkeypatch.setattr(
        containerManager, "_fsetMountSourcesOfSurvivingContainers",
        lambda: setSurviving)
    monkeypatch.setattr(
        containerManager.subprocess, "run",
        lambda *args, **kwargs: None)


def test_a_proven_removal_releases_the_files_no_survivor_mounts(
    monkeypatch, tmp_path,
):
    monkeypatch.setenv("HOME", str(tmp_path))
    sRoot = fsGetEphemeralRoot()
    sMine = _fsWriteSecret(sRoot, "vc_secret_gh_token_mine.tmp")
    sShared = _fsWriteSecret(sRoot, "vc_secret_gh_token_shared.tmp")
    _fnStubProbes(
        monkeypatch, [sMine, sShared, "/workspace"],
        {"bAnswered": True, "bPresent": False}, {sShared})
    containerManager.fnRemoveStopped("gone")
    assert not os.path.exists(sMine)
    assert os.path.exists(sShared)


def test_nothing_is_released_until_the_daemon_proves_the_container_gone(
    monkeypatch, tmp_path,
):
    monkeypatch.setenv("HOME", str(tmp_path))
    sRoot = fsGetEphemeralRoot()
    sMine = _fsWriteSecret(sRoot, "vc_secret_gh_token_mine.tmp")
    for dictPresence in (
        {"bAnswered": False, "bPresent": False},
        {"bAnswered": True, "bPresent": True},
    ):
        _fnStubProbes(monkeypatch, [sMine], dictPresence, set())
        containerManager.fnRemoveStopped("stillThere")
        assert os.path.exists(sMine), dictPresence


def test_nothing_is_released_when_the_survivors_cannot_be_enumerated(
    monkeypatch, tmp_path,
):
    monkeypatch.setenv("HOME", str(tmp_path))
    sRoot = fsGetEphemeralRoot()
    sMine = _fsWriteSecret(sRoot, "vc_secret_gh_token_mine.tmp")
    _fnStubProbes(
        monkeypatch, [sMine], {"bAnswered": True, "bPresent": False}, None)
    containerManager.fnRemoveStopped("gone")
    assert os.path.exists(sMine), "an unanswered enumeration releases nothing"


def test_the_force_removal_path_releases_through_the_same_proof(
    monkeypatch, tmp_path,
):
    monkeypatch.setenv("HOME", str(tmp_path))
    sRoot = fsGetEphemeralRoot()
    sMine = _fsWriteSecret(sRoot, "vc_secret_gh_token_mine.tmp")
    listCommands = []

    def ftAnswerProbe(saCommand):
        listCommands.append(list(saCommand))
        if saCommand[:2] == ["docker", "rm"]:
            return (True, "")
        return (True, "")  # ps -a -q --filter id=: nothing listed, so gone

    monkeypatch.setattr(containerManager, "_ftRunProbeCommand", ftAnswerProbe)
    monkeypatch.setattr(
        containerManager, "_flistMountSourcesOfContainer",
        lambda sIdentifier: [sMine])
    monkeypatch.setattr(
        containerManager, "_fsetMountSourcesOfSurvivingContainers",
        lambda: set())
    containerManager._fnForceRemoveContainer("0123456789ab")
    assert ["docker", "rm", "-f", "0123456789ab"] in listCommands
    assert any("id=0123456789ab" in saCommand for saCommand in listCommands)
    assert not os.path.exists(sMine)


def test_the_surviving_mount_enumerator_reads_every_container(monkeypatch):
    dictAnswers = {
        ("docker", "ps", "-a", "-q"): (True, "aaa\nbbb\n"),
        ("docker", "inspect", "-f", "{{json .Mounts}}", "aaa", "bbb"): (
            True,
            '[{"Source": "/host/one", "Destination": "/run/secrets/x"}]\n'
            '[{"Source": "/host/two"}, {"Source": ""}]\n'),
    }
    monkeypatch.setattr(
        containerManager, "_ftRunProbeCommand",
        lambda saCommand: dictAnswers[tuple(saCommand)])
    assert containerManager._fsetMountSourcesOfSurvivingContainers() == {
        "/host/one", "/host/two"}


def test_the_surviving_mount_enumerator_answers_none_when_the_daemon_does_not(
    monkeypatch,
):
    monkeypatch.setattr(
        containerManager, "_ftRunProbeCommand", lambda saCommand: (False, ""))
    assert containerManager._fsetMountSourcesOfSurvivingContainers() is None
    monkeypatch.setattr(
        containerManager, "_ftRunProbeCommand", lambda saCommand: (True, ""))
    assert containerManager._fsetMountSourcesOfSurvivingContainers() == set()


def test_the_removed_container_has_no_cleanup_helper_left_over():
    """The old ``_fnCleanupTempFiles`` had no caller; it must not return."""
    assert not hasattr(containerManager, "_fnCleanupTempFiles")
