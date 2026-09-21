"""A build is refused before it starts when the daemon's disk cannot hold it.

A build on a full daemon disk failed at the Claude overlay's ``mkdir``
after apt, the toolchain and pip had spent their minutes, and the
overlay blamed the network (a live build, 2026-09-21). The daemon does
not report free space; ``df`` inside any image it already holds does.
"""

import subprocess
from types import SimpleNamespace

import pytest

from vaibify.cli.daemonDiskPreflight import (
    I_DAEMON_FREE_DISK_FAIL_BYTES,
    I_DAEMON_FREE_DISK_WARN_BYTES,
    S_PREFLIGHT_NAME,
    fiDaemonFreeDiskBytes,
    fiParseAvailableBytesFromDf,
    fpreflightDaemonFreeDisk,
)
from vaibify.cli.preflightResult import (
    S_LEVEL_FAIL, S_LEVEL_NOT_CHECKED, S_LEVEL_WARN,
)


S_DF_FULL = (
    "Filesystem     1024-blocks     Used Available Capacity Mounted on\n"
    "overlay          102624184 98375300         0     100% /\n"
)
S_DF_ROOMY = (
    "Filesystem     1024-blocks     Used Available Capacity Mounted on\n"
    "overlay          102624184 40000000  60000000      40% /\n"
)


def test_the_available_column_is_read_in_kibibytes():
    assert fiParseAvailableBytesFromDf(S_DF_FULL) == 0
    assert fiParseAvailableBytesFromDf(S_DF_ROOMY) == 60000000 * 1024
    assert fiParseAvailableBytesFromDf("") == -1
    assert fiParseAvailableBytesFromDf("Filesystem only\n") == -1
    assert fiParseAvailableBytesFromDf("a\nb c d notanumber e\n") == -1


@pytest.mark.falsification
def test_a_full_daemon_disk_fails_the_preflight_with_the_remedy():
    """Kills: the fail bound dropping to zero, under which a disk with
    a few hundred megabytes free is called enough for a build."""
    preflightDisk = fpreflightDaemonFreeDisk(lambda: 512 * (2 ** 20))
    assert preflightDisk.sLevel == S_LEVEL_FAIL
    assert preflightDisk.sName == S_PREFLIGHT_NAME
    assert "docker system df" in preflightDisk.sRemediation
    assert "docker system prune -a" in preflightDisk.sRemediation
    assert preflightDisk.sCommand == "docker system df"


def test_the_three_other_answers():
    assert fpreflightDaemonFreeDisk(lambda: -1).sLevel == S_LEVEL_NOT_CHECKED
    assert fpreflightDaemonFreeDisk(
        lambda: I_DAEMON_FREE_DISK_FAIL_BYTES + 1,
    ).sLevel == S_LEVEL_WARN
    assert fpreflightDaemonFreeDisk(lambda: I_DAEMON_FREE_DISK_WARN_BYTES) is None


def _fnRunFake(dictAnswers):
    """A subprocess.run double answering by the docker subcommand."""
    listCalls = []

    def fnRun(listCommand, **kwargs):
        listCalls.append(listCommand)
        if listCommand[:2] == ["docker", "images"]:
            return SimpleNamespace(returncode=0, stdout=dictAnswers["images"])
        sImageId = listCommand[5]
        iCode, sStdout = dictAnswers[sImageId]
        return SimpleNamespace(returncode=iCode, stdout=sStdout)

    fnRun.listCalls = listCalls
    return fnRun


def test_the_probe_runs_df_inside_a_local_image_and_skips_one_without_df():
    fnRun = _fnRunFake({
        "images": "aaa\nbbb\nccc\n",
        "aaa": (127, ""),
        "bbb": (0, S_DF_FULL),
    })
    assert fiDaemonFreeDiskBytes(fnRun) == 0
    assert fnRun.listCalls[1][:5] == ["docker", "run", "--rm", "--entrypoint", "df"]
    assert [listCall[5] for listCall in fnRun.listCalls[1:]] == ["aaa", "bbb"]


def test_no_local_image_or_no_docker_is_no_answer():
    assert fiDaemonFreeDiskBytes(_fnRunFake({"images": ""})) == -1

    def fnMissing(listCommand, **kwargs):
        raise FileNotFoundError("docker")

    assert fiDaemonFreeDiskBytes(fnMissing) == -1

    def fnSlow(listCommand, **kwargs):
        raise subprocess.TimeoutExpired(listCommand, 20)

    assert fiDaemonFreeDiskBytes(fnSlow) == -1
