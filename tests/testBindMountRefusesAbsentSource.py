"""A missing bind-mount source must REFUSE, not manufacture a stub.

``-v`` creates a missing source as an empty directory. Measured live
on this project's own daemon (2026-09-10): the directory appeared,
owned by the invoking user, and the container started with no data
where the researcher's data was meant to be. That silent creation is
the same shape as the ``~/.vaibify/tmp`` sweep incident, where a
deleted bind-mounted credential FILE came back as a directory and left
the container unstartable -- and ``listBindMounts`` declares no
file-or-directory kind, so nothing downstream can tell a stub from
real data.

``--mount type=bind`` refuses instead, naming the path. This is a
breaking change, taken deliberately with the researcher's approval:
a project whose declared mount source is missing now fails to start.

Two lanes. The unit lane pins the composed argument, because that is
where a revert to ``-v`` would show up. The live lane proves the
daemon HONOURS it -- that the refusal happens and that no directory
appears -- which no argument assertion can establish.
"""

import os
import subprocess
import uuid

import pytest

from vaibify.docker.containerManager import _fnAddBindMounts


class _ConfigWithMount:
    """The smallest config `_fnAddBindMounts` reads."""

    def __init__(self, listBindMounts):
        self.listBindMounts = listBindMounts


@pytest.mark.falsification
def test_bind_mounts_are_composed_as_mount_not_as_v(tmp_path, monkeypatch):
    """Bind mounts are composed with --mount, never -v.

    Kills: In containerManager._fnAddBindMounts, compose the mount as
    `-v host:container` again, which the daemon answers by creating a
    missing source as an empty directory.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    sHostPath = str(tmp_path / "data")
    os.makedirs(sHostPath)
    saRunArgs = []
    _fnAddBindMounts(
        _ConfigWithMount([
            {"host": sHostPath, "container": "/mnt/data"},
        ]),
        saRunArgs,
    )
    assert saRunArgs[0] == "--mount"
    assert "-v" not in saRunArgs


def test_a_comma_in_the_path_is_carried_not_split(tmp_path, monkeypatch):
    """Docker parses the value as CSV; both paths are quoted for it."""
    monkeypatch.setenv("HOME", str(tmp_path))
    sHostPath = str(tmp_path / "a,b")
    os.makedirs(sHostPath)
    saRunArgs = []
    _fnAddBindMounts(
        _ConfigWithMount([
            {"host": sHostPath, "container": "/mnt/data"},
        ]),
        saRunArgs,
    )
    assert f'"source={sHostPath}"' in saRunArgs[1]


def test_a_double_quote_in_the_path_is_refused(tmp_path, monkeypatch):
    """It cannot be expressed in a --mount value, so it is not guessed at."""
    from vaibify.config.bindMountValidator import BindMountValidationError
    monkeypatch.setenv("HOME", str(tmp_path))
    sHostPath = str(tmp_path / 'we"ird')
    os.makedirs(sHostPath)
    with pytest.raises(BindMountValidationError) as errorRaised:
        _fnAddBindMounts(
            _ConfigWithMount([
                {"host": sHostPath, "container": "/mnt/data"},
            ]),
            [],
        )
    assert "double quote" in str(errorRaised.value)


@pytest.mark.docker_live
def test_an_absent_source_is_refused_and_no_directory_appears(tmp_path):
    """The live witness: the daemon refuses, and nothing is created."""
    from tests.testDockerConnectionLive import fnRequireDaemonReachable
    fnRequireDaemonReachable()
    sHome = os.path.expanduser("~")
    sMissing = os.path.join(sHome, "vaibifyAbsentMount" + uuid.uuid4().hex[:8])
    assert not os.path.exists(sMissing)
    processRun = subprocess.run(
        [
            "docker", "run", "--rm", "--mount",
            f'type=bind,"source={sMissing}","target=/probe"',
            "ubuntu:24.04", "true",
        ],
        capture_output=True, text=True,
    )
    try:
        assert processRun.returncode != 0, (
            "the daemon accepted a mount whose source does not exist"
        )
        assert sMissing in processRun.stderr, (
            "the refusal must name the path the researcher has to fix"
        )
        assert not os.path.exists(sMissing), (
            "a stub directory was manufactured, which is the whole "
            "defect this change exists to prevent"
        )
    finally:
        if os.path.isdir(sMissing):
            os.rmdir(sMissing)
