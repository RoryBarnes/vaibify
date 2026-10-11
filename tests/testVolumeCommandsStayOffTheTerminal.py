"""A volume command's echo never reaches the hub's terminal.

``docker volume rm`` and ``docker volume create`` print the volume's
name on success. Run with the hub's stdout inherited, deleting an
environment printed two bare volume names into the terminal the
researcher started ``vaibify`` from (researcher-reported, 2026-10-11).
A real child process runs a stand-in ``docker`` that behaves the same
way, so the file descriptors, not a mock, decide what reaches fd 1.
"""

import os

import pytest

from vaibify.docker import volumeManager

S_VOLUME = "someProject-workspace"


@pytest.fixture()
def fixtureEchoingDocker(tmp_path, monkeypatch):
    """Put a ``docker`` on PATH that echoes its last argument like the real one."""
    pathDocker = tmp_path / "docker"
    pathDocker.write_text(
        "#!/bin/sh\n"
        'for sArgument in "$@"; do sLast="$sArgument"; done\n'
        'if [ "$sLast" = "missing-volume" ]; then\n'
        '    echo "Error: no such volume: $sLast" >&2; exit 1\n'
        "fi\n"
        'echo "$sLast"\n'
    )
    os.chmod(pathDocker, 0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")


def testDestroyingAVolumePrintsNothing(fixtureEchoingDocker, capfd):
    """Kills: inheriting stdout for the volume removal."""
    volumeManager.fnDestroyVolume(S_VOLUME)
    sOut, sError = capfd.readouterr()
    assert (sOut, sError) == ("", "")


def testCreatingAVolumePrintsNothing(fixtureEchoingDocker, capfd, monkeypatch):
    monkeypatch.setattr(volumeManager, "fbVolumeExists", lambda sName: False)
    volumeManager.fnCreateVolume(S_VOLUME)
    assert capfd.readouterr() == ("", "")


def testAFailedRemovalCarriesDockersReason(fixtureEchoingDocker):
    with pytest.raises(RuntimeError, match="no such volume: missing-volume"):
        volumeManager.fnDestroyVolume("missing-volume")
