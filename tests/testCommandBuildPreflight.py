"""Tests for vaibify build pre-flight checks (F-B-09, F-B-12, F-B-13, F-E-01)."""

import json
import subprocess

from types import SimpleNamespace
from unittest.mock import patch

from click.testing import CliRunner

from vaibify.cli.commandBuild import (
    _fdiDockerDfBytes,
    _fiParseHumanSize,
    _fiSumDfSizeBytes,
    _flistArchMismatchResults,
    _fnHandleBuildError,
    _fpreflightArch,
    _fpreflightDisk,
    _fpreflightMemory,
    _fsBuildErrorHint,
    _fsNormalizeArch,
    flistRunBuildPreflight,
    fsDockerVmArch,
    fsHostArch,
)
from vaibify.cli.preflightChecks import fpreflightDaemon as _fpreflightDaemon


# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------

def _configWithGpu(bGpu):
    """Return a stub config whose features.bGpu equals bGpu."""
    return SimpleNamespace(
        sProjectName="testproj",
        features=SimpleNamespace(bGpu=bGpu),
    )


def _resultProcess(iReturnCode=0, sStdout="", sStderr=""):
    """Build a stub subprocess.run return value."""
    return SimpleNamespace(
        returncode=iReturnCode, stdout=sStdout, stderr=sStderr,
    )


# -------------------------------------------------------------------
# F-E-01 — Colima-aware daemon error message
# -------------------------------------------------------------------

@patch("vaibify.docker.fbDockerDaemonReachable", return_value=False)
@patch("vaibify.docker.dockerContext.fbColimaActive", return_value=True)
def test_fpreflightDaemon_unreachable_colima_says_colima_start(
    mockColima, mockDaemon,
):
    resultPreflight = _fpreflightDaemon()
    assert resultPreflight.sLevel == "fail"
    assert "colima start" in resultPreflight.sRemediation.lower()


@patch("vaibify.docker.fbDockerDaemonReachable", return_value=False)
@patch("vaibify.docker.dockerContext.fbColimaActive", return_value=False)
def test_fpreflightDaemon_unreachable_no_colima_says_docker_desktop(
    mockColima, mockDaemon,
):
    resultPreflight = _fpreflightDaemon()
    assert resultPreflight.sLevel == "fail"
    assert "Docker Desktop" in resultPreflight.sRemediation


@patch("vaibify.docker.fbDockerDaemonReachable", return_value=True)
def test_fpreflightDaemon_reachable_returns_ok(mockDaemon):
    resultPreflight = _fpreflightDaemon()
    assert resultPreflight.sLevel == "ok"


# -------------------------------------------------------------------
# F-E-03 — Colima socket permission denied
# -------------------------------------------------------------------

@patch("subprocess.run")
@patch("vaibify.docker.fbDockerDaemonReachable", return_value=False)
def test_fpreflightDaemon_socket_permission_denied(mockDaemon, mockRun):
    """A permission-denied stderr maps to a socket-permission message."""
    mockRun.return_value = _resultProcess(
        iReturnCode=1,
        sStderr=(
            "Got permission denied while trying to connect to the "
            "Docker daemon socket at unix:///var/run/docker.sock"
        ),
    )
    resultPreflight = _fpreflightDaemon()
    assert resultPreflight.sLevel == "fail"
    assert "permission denied" in resultPreflight.sMessage.lower()
    assert "unset DOCKER_HOST" in resultPreflight.sRemediation
    assert "usermod" in resultPreflight.sRemediation


@patch("subprocess.run")
@patch("vaibify.docker.dockerContext.fbColimaActive", return_value=True)
@patch("vaibify.docker.fbDockerDaemonReachable", return_value=False)
def test_fpreflightDaemon_unreachable_falls_through_when_no_perm(
    mockDaemon, mockColima, mockRun,
):
    """Without a permission stderr, the colima-start branch wins."""
    mockRun.return_value = _resultProcess(
        iReturnCode=1, sStderr="Cannot connect to the Docker daemon",
    )
    resultPreflight = _fpreflightDaemon()
    assert resultPreflight.sLevel == "fail"
    assert "colima start" in resultPreflight.sRemediation.lower()


# -------------------------------------------------------------------
# F-B-09 — Apple Silicon arch checks
# -------------------------------------------------------------------

def test_fsNormalizeArch_handles_known_aliases():
    assert _fsNormalizeArch("aarch64") == "arm64"
    assert _fsNormalizeArch("arm64") == "arm64"
    assert _fsNormalizeArch("x86_64") == "amd64"
    assert _fsNormalizeArch("AMD64") == "amd64"
    assert _fsNormalizeArch("") == ""
    assert _fsNormalizeArch("riscv") == ""


@patch("vaibify.cli.commandBuild.platform.machine", return_value="arm64")
def test_fsHostArch_arm64_mac(mockMachine):
    assert fsHostArch() == "arm64"


@patch("subprocess.run")
def test_fsDockerVmArch_returns_normalized(mockRun):
    mockRun.return_value = _resultProcess(
        iReturnCode=0, sStdout="x86_64\n",
    )
    assert fsDockerVmArch() == "amd64"


@patch("subprocess.run", side_effect=FileNotFoundError)
def test_fsDockerVmArch_returns_empty_when_docker_missing(mockRun):
    assert fsDockerVmArch() == ""


@patch("subprocess.run",
       side_effect=subprocess.TimeoutExpired("docker", 10))
def test_fsDockerVmArch_returns_empty_on_timeout(mockRun):
    assert fsDockerVmArch() == ""


@patch("subprocess.run")
def test_fsDockerVmArch_returns_empty_on_nonzero(mockRun):
    mockRun.return_value = _resultProcess(iReturnCode=1)
    assert fsDockerVmArch() == ""


@patch("vaibify.docker.dockerContext.fbColimaActive", return_value=True)
@patch("vaibify.cli.commandBuild.fsDockerVmArch", return_value="amd64")
@patch("vaibify.cli.commandBuild.fsHostArch", return_value="arm64")
def test_fpreflightArch_warns_on_arm_host_amd_vm(
    mockHost, mockVm, mockColima,
):
    listResults = _fpreflightArch(_configWithGpu(False))
    assert len(listResults) == 1
    assert listResults[0].sLevel == "warn"
    assert "QEMU emulation" in listResults[0].sMessage


@patch("vaibify.docker.dockerContext.fbColimaActive", return_value=True)
@patch("vaibify.cli.commandBuild.fsDockerVmArch", return_value="amd64")
@patch("vaibify.cli.commandBuild.fsHostArch", return_value="arm64")
def test_fpreflightArch_fails_when_gpu_on_arm_host(
    mockHost, mockVm, mockColima,
):
    listResults = _fpreflightArch(_configWithGpu(True))
    assert len(listResults) == 1
    assert listResults[0].sLevel == "fail"
    assert "amd64-only" in listResults[0].sMessage


@patch("vaibify.cli.commandBuild.fsDockerVmArch", return_value="arm64")
@patch("vaibify.cli.commandBuild.fsHostArch", return_value="arm64")
def test_fpreflightArch_no_result_when_arches_match(mockHost, mockVm):
    assert _fpreflightArch(_configWithGpu(False)) == []


@patch("vaibify.cli.commandBuild.fsDockerVmArch", return_value="amd64")
@patch("vaibify.cli.commandBuild.fsHostArch", return_value="amd64")
def test_fpreflightArch_no_result_intel_amd_match(mockHost, mockVm):
    assert _fpreflightArch(_configWithGpu(False)) == []


@patch("vaibify.cli.commandBuild.fsDockerVmArch", return_value="")
@patch("vaibify.cli.commandBuild.fsHostArch", return_value="arm64")
def test_fpreflightArch_skipped_when_vm_unknown(mockHost, mockVm):
    assert _fpreflightArch(_configWithGpu(False)) == []


@patch("vaibify.docker.dockerContext.fbColimaActive", return_value=False)
def test_flistArchMismatchResults_non_colima_remediation(mockColima):
    listResults = _flistArchMismatchResults(
        _configWithGpu(False), "arm64", "amd64",
    )
    assert "aarch64" in listResults[0].sRemediation


# -------------------------------------------------------------------
# F-B-12 — Disk pre-flight
# -------------------------------------------------------------------

def test_fiParseHumanSize_handles_units():
    assert _fiParseHumanSize("0B") == 0
    assert _fiParseHumanSize("100B") == 100
    assert _fiParseHumanSize("1.5GB") == int(1.5 * 1000 ** 3)
    assert _fiParseHumanSize("2GiB") == 2 * 1024 ** 3
    assert _fiParseHumanSize("1024") == 1024
    assert _fiParseHumanSize("") == 0
    assert _fiParseHumanSize("notanumber") == -1


def test_fiSumDfSizeBytes_sums_rows():
    sJson = "\n".join([
        json.dumps({"Type": "Images", "Size": "10GB"}),
        json.dumps({"Type": "Containers", "Size": "5GB"}),
    ])
    iBytes = _fiSumDfSizeBytes(sJson)
    assert iBytes == 15 * 1000 ** 3


def test_fiSumDfSizeBytes_returns_negative_on_bad_json():
    assert _fiSumDfSizeBytes("not-json\n") == -1


def test_fiSumDfSizeBytes_returns_negative_when_empty():
    assert _fiSumDfSizeBytes("") == -1


def test_fiSumDfSizeBytes_returns_negative_on_bad_size():
    sJson = json.dumps({"Type": "Images", "Size": "junk"})
    assert _fiSumDfSizeBytes(sJson) == -1


@patch("subprocess.run")
def test_fdiDockerDfBytes_happy_path(mockRun):
    sJson = "\n".join([
        json.dumps({"Type": "Images", "Size": "1GB"}),
        json.dumps({"Type": "Containers", "Size": "500MB"}),
    ])
    mockRun.return_value = _resultProcess(iReturnCode=0, sStdout=sJson)
    iBytes = _fdiDockerDfBytes()
    assert iBytes == 1 * 1000 ** 3 + 500 * 1000 ** 2


@patch("subprocess.run", side_effect=FileNotFoundError)
def test_fdiDockerDfBytes_returns_negative_when_docker_missing(mockRun):
    assert _fdiDockerDfBytes() == -1


@patch("subprocess.run",
       side_effect=subprocess.TimeoutExpired("docker", 10))
def test_fdiDockerDfBytes_returns_negative_on_timeout(mockRun):
    assert _fdiDockerDfBytes() == -1


@patch("subprocess.run")
def test_fdiDockerDfBytes_returns_negative_on_nonzero(mockRun):
    mockRun.return_value = _resultProcess(iReturnCode=2)
    assert _fdiDockerDfBytes() == -1


@patch("vaibify.cli.commandBuild._fdiDockerDfBytes", return_value=-1)
def test_fpreflightDisk_emits_info_when_unparseable(mockBytes):
    listResults = _fpreflightDisk()
    assert len(listResults) == 1
    assert listResults[0].sLevel == "info"


@patch("vaibify.cli.commandBuild._fdiDockerDfBytes",
       return_value=5 * (2 ** 30))
def test_fpreflightDisk_no_warning_when_below_threshold(mockBytes):
    assert _fpreflightDisk() == []


@patch("vaibify.docker.dockerContext.fbColimaActive", return_value=True)
@patch("vaibify.cli.commandBuild._fdiDockerDfBytes",
       return_value=80 * (2 ** 30))
def test_fpreflightDisk_warns_when_threshold_exceeded(
    mockBytes, mockColima,
):
    listResults = _fpreflightDisk()
    assert len(listResults) == 1
    assert listResults[0].sLevel == "warn"
    assert "docker system prune" in listResults[0].sRemediation
    assert "colima start --disk" in listResults[0].sRemediation


@patch("vaibify.docker.dockerContext.fbColimaActive", return_value=False)
@patch("vaibify.cli.commandBuild._fdiDockerDfBytes",
       return_value=80 * (2 ** 30))
def test_fpreflightDisk_warn_no_colima_advice_when_not_colima(
    mockBytes, mockColima,
):
    listResults = _fpreflightDisk()
    assert "colima" not in listResults[0].sRemediation


# -------------------------------------------------------------------
# F-B-13 — Memory pre-flight
# -------------------------------------------------------------------

@patch("subprocess.run")
def test_fpreflightMemory_warns_below_4gb(mockRun):
    mockRun.return_value = _resultProcess(
        iReturnCode=0, sStdout=str(2 * (2 ** 30)),
    )
    listResults = _fpreflightMemory()
    assert len(listResults) == 1
    assert listResults[0].sLevel == "warn"
    assert "OOM" in listResults[0].sMessage


@patch("subprocess.run")
def test_fpreflightMemory_silent_above_4gb(mockRun):
    mockRun.return_value = _resultProcess(
        iReturnCode=0, sStdout=str(8 * (2 ** 30)),
    )
    assert _fpreflightMemory() == []


@patch("subprocess.run", side_effect=FileNotFoundError)
def test_fpreflightMemory_silent_when_docker_missing(mockRun):
    assert _fpreflightMemory() == []


@patch("subprocess.run")
def test_fpreflightMemory_silent_on_garbage_output(mockRun):
    mockRun.return_value = _resultProcess(
        iReturnCode=0, sStdout="not-an-int",
    )
    assert _fpreflightMemory() == []


@patch("subprocess.run")
def test_fpreflightMemory_silent_on_nonzero_returncode(mockRun):
    mockRun.return_value = _resultProcess(iReturnCode=1)
    assert _fpreflightMemory() == []


# -------------------------------------------------------------------
# Build error hint (exit 137 -> OOM)
# -------------------------------------------------------------------

def test_fsBuildErrorHint_returns_oom_for_oom_classification():
    assert "OOM" in _fsBuildErrorHint("oom")


def test_fsBuildErrorHint_blank_for_unknown_classification():
    assert _fsBuildErrorHint("") == ""
    assert _fsBuildErrorHint("not-a-known-classification") == ""


def test_fnHandleBuildError_appends_oom_hint(capsys):
    error = RuntimeError(
        "Docker command failed (exit 137): docker build ..."
    )
    try:
        _fnHandleBuildError(error)
    except SystemExit:
        pass
    sCaptured = capsys.readouterr().err
    assert "OOM" in sCaptured


# -------------------------------------------------------------------
# Aggregator + CLI integration
# -------------------------------------------------------------------

@patch("vaibify.cli.commandBuild._fpreflightMemory", return_value=[])
@patch("vaibify.cli.commandBuild._fpreflightDisk", return_value=[])
@patch("vaibify.cli.commandBuild._fpreflightArch", return_value=[])
@patch("vaibify.docker.fbDockerDaemonReachable", return_value=False)
@patch("vaibify.docker.dockerContext.fbColimaActive", return_value=False)
def test_flistRunBuildPreflight_short_circuits_on_daemon_fail(
    mockColima, mockDaemon, mockArch, mockDisk, mockMem,
):
    listResults = flistRunBuildPreflight(_configWithGpu(False))
    assert len(listResults) == 1
    assert listResults[0].sLevel == "fail"
    mockArch.assert_not_called()
    mockDisk.assert_not_called()
    mockMem.assert_not_called()


@patch("vaibify.cli.commandBuild._fpreflightMemory", return_value=[])
@patch("vaibify.cli.commandBuild._fpreflightDisk", return_value=[])
@patch("vaibify.cli.commandBuild._fpreflightArch", return_value=[])
@patch("vaibify.docker.fbDockerDaemonReachable", return_value=True)
def test_flistRunBuildPreflight_runs_all_when_daemon_ok(
    mockDaemon, mockArch, mockDisk, mockMem,
):
    listResults = flistRunBuildPreflight(_configWithGpu(False))
    assert listResults[0].sLevel == "ok"
    mockArch.assert_called_once()
    mockDisk.assert_called_once()
    mockMem.assert_called_once()


@patch("vaibify.cli.commandBuild.fnBuildFromConfig")
@patch("vaibify.cli.commandBuild.fconfigResolveProject")
@patch("vaibify.cli.commandBuild.fsDockerDir", return_value="/docker")
@patch("vaibify.cli.commandBuild._fpreflightMemory", return_value=[])
@patch("vaibify.cli.commandBuild._fpreflightDisk", return_value=[])
@patch("vaibify.cli.commandBuild.fsDockerVmArch", return_value="amd64")
@patch("vaibify.cli.commandBuild.fsHostArch", return_value="arm64")
@patch("vaibify.docker.dockerContext.fbColimaActive", return_value=True)
@patch("vaibify.docker.fbDockerDaemonReachable", return_value=True)
def test_build_warns_but_proceeds_on_arch_mismatch(
    mockDaemon, mockColima, mockHost, mockVm, mockDisk,
    mockMem, mockDir, mockConfig, mockBuild,
):
    from vaibify.cli.commandBuild import build
    mockConfig.return_value = SimpleNamespace(
        sProjectName="testproj",
        features=SimpleNamespace(bGpu=False),
    )
    runner = CliRunner()
    result = runner.invoke(build)
    assert result.exit_code == 0
    assert "QEMU" in result.output
    mockBuild.assert_called_once()


@patch("vaibify.cli.commandBuild.fnBuildFromConfig")
@patch("vaibify.cli.commandBuild.fconfigResolveProject")
@patch("vaibify.cli.commandBuild.fsDockerDir", return_value="/docker")
@patch("vaibify.cli.commandBuild._fpreflightMemory", return_value=[])
@patch("vaibify.cli.commandBuild._fpreflightDisk", return_value=[])
@patch("vaibify.cli.commandBuild.fsDockerVmArch", return_value="amd64")
@patch("vaibify.cli.commandBuild.fsHostArch", return_value="arm64")
@patch("vaibify.docker.dockerContext.fbColimaActive", return_value=True)
@patch("vaibify.docker.fbDockerDaemonReachable", return_value=True)
def test_build_aborts_on_arm_host_with_gpu(
    mockDaemon, mockColima, mockHost, mockVm, mockDisk,
    mockMem, mockDir, mockConfig, mockBuild,
):
    from vaibify.cli.commandBuild import build
    mockConfig.return_value = SimpleNamespace(
        sProjectName="testproj",
        features=SimpleNamespace(bGpu=True),
    )
    runner = CliRunner()
    result = runner.invoke(build)
    assert result.exit_code != 0
    assert "amd64-only" in result.output
    mockBuild.assert_not_called()
