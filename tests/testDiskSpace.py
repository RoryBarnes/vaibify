"""Pre-flight disk-space helpers for the workflow runner.

Backs the warning banner the runner surfaces when ``/workspace`` is
near full. The probe never blocks the run on its own — vaibify cannot
estimate sweep sizes without a per-workflow
``iEstimatedOutputBytes`` declaration — but it must not silently
mask a low-disk condition either.
"""

from vaibify.gui import diskSpace


class _FakeDfDocker:
    """Docker stub whose df output is parameterized per test."""

    def __init__(self, iExitCode=0, sOutput=""):
        self.iExitCode = iExitCode
        self.sOutput = sOutput
        self.listCommands = []

    def ftResultExecuteCommand(self, sContainerId, sCommand):
        self.listCommands.append((sContainerId, sCommand))
        return (self.iExitCode, self.sOutput)


_S_DF_OUTPUT_GENEROUS = (
    "Filesystem                  1B-blocks       Used  Available "
    "Use% Mounted on\n"
    "/dev/sda1                 53687091200  10737418240  "
    "42949672960  20% /workspace\n"
)

_S_DF_OUTPUT_TIGHT = (
    "Filesystem                  1B-blocks       Used  Available "
    "Use% Mounted on\n"
    "/dev/sda1                 53687091200  53400000000   "
    "287091200  99% /workspace\n"
)


def test_fnCheckWorkspaceFreeBytes_returns_available_column():
    """The probe parses the Available column of df -B1 output."""
    connectionFake = _FakeDfDocker(0, _S_DF_OUTPUT_GENEROUS)
    iFree = diskSpace.fnCheckWorkspaceFreeBytes(connectionFake, "cid")
    assert iFree == 42949672960


def test_fnCheckWorkspaceFreeBytes_returns_negative_on_exec_failure():
    """A non-zero exit code degrades to -1 (unknown), never raises."""
    connectionFake = _FakeDfDocker(1, "")
    assert diskSpace.fnCheckWorkspaceFreeBytes(connectionFake, "cid") == -1


def test_fnCheckWorkspaceFreeBytes_returns_negative_on_garbage():
    """Malformed df output degrades to -1 instead of guessing."""
    connectionFake = _FakeDfDocker(0, "not a df listing")
    assert diskSpace.fnCheckWorkspaceFreeBytes(connectionFake, "cid") == -1


def test_fnCheckWorkspaceFreeBytes_swallows_raised_exception():
    """An unexpected docker error never escapes the probe."""

    class _RaisingDocker:
        def ftResultExecuteCommand(self, *args, **kwargs):
            raise RuntimeError("docker died")

    assert diskSpace.fnCheckWorkspaceFreeBytes(
        _RaisingDocker(), "cid",
    ) == -1


def test_fdictAssertSpaceForOutputs_returns_none_when_ample():
    """Plenty of free space yields no warning."""
    connectionFake = _FakeDfDocker(0, _S_DF_OUTPUT_GENEROUS)
    dictResult = diskSpace.fdictAssertSpaceForOutputs(
        connectionFake, "cid", iEstimatedBytes=1024 * 1024 * 100,
    )
    assert dictResult is None


def test_fdictAssertSpaceForOutputs_warns_when_below_one_gb():
    """Free space below the 1 GB floor surfaces a warning."""
    connectionFake = _FakeDfDocker(0, _S_DF_OUTPUT_TIGHT)
    dictResult = diskSpace.fdictAssertSpaceForOutputs(
        connectionFake, "cid", iEstimatedBytes=0,
    )
    assert dictResult is not None
    assert dictResult["sCode"] == "low-workspace-disk-space"
    assert dictResult["iFreeBytes"] < diskSpace.I_DEFAULT_MIN_FREE_BYTES
    assert "Workspace free space is low" in dictResult["sMessage"]


def test_fdictAssertSpaceForOutputs_warns_when_below_2x_estimated():
    """The 2x headroom rule fires even when the 1 GB floor would not."""
    sOutput = (
        "Filesystem                  1B-blocks       Used  "
        "Available Use% Mounted on\n"
        "/dev/sda1                 53687091200  10737418240  "
        "2147483648  20% /workspace\n"
    )
    connectionFake = _FakeDfDocker(0, sOutput)
    iEstimated = 5 * 1024 * 1024 * 1024
    dictResult = diskSpace.fdictAssertSpaceForOutputs(
        connectionFake, "cid", iEstimatedBytes=iEstimated,
    )
    assert dictResult is not None
    assert dictResult["iRequiredBytes"] >= iEstimated * 2


def test_fdictAssertSpaceForOutputs_silent_on_unknown_free_space():
    """An unknown probe result must not produce a spurious warning."""
    connectionFake = _FakeDfDocker(1, "")
    assert diskSpace.fdictAssertSpaceForOutputs(
        connectionFake, "cid", iEstimatedBytes=10 ** 12,
    ) is None


def test_message_formats_bytes_in_human_units():
    """The warning string shows free + required in human units."""
    sBody = diskSpace._fsFormatSpaceMessage(
        iFreeBytes=512 * 1024,
        iRequiredBytes=2 * 1024 * 1024 * 1024,
        iEstimatedBytes=1024 * 1024 * 1024,
    )
    assert "512.0 KB" in sBody
    assert "2.0 GB" in sBody


# ----------------------------------------------------------------------
# Wiring into _flistPreflightValidate — the runner's banner source.
# ----------------------------------------------------------------------


def test_preflight_validator_surfaces_low_disk_warning():
    """A low-disk probe surfaces as a soft warning, not a hard error.

    Hard ``listErrors`` would abort the run — the contract is that a
    low-disk-space warning is informative-only so a researcher can still
    start the job and react when the warning fires in the dashboard.
    """
    from vaibify.gui import pipelineRunner

    class _MixedDocker:
        def ftResultExecuteCommand(self, sContainerId, sCommand,
                                    sWorkdir=None):
            if sCommand.startswith("df "):
                return (0, _S_DF_OUTPUT_TIGHT)
            return (0, "")

        def fbaFetchFile(self, sContainerId, sPath):
            raise FileNotFoundError(sPath)

    dictWorkflow = {
        "listSteps": [{
            "sName": "S1", "sDirectory": "stepA",
            "saDataCommands": [], "saPlotCommands": [],
        }],
    }
    listWarnings = pipelineRunner._flistCollectPreflightWarnings(
        _MixedDocker(), "cid", dictWorkflow,
    )
    assert any(
        "Workspace free space is low" in sWarning
        for sWarning in listWarnings
    )


def test_preflight_validator_silent_when_disk_ample():
    """An ample-disk probe yields no preflight warnings."""
    from vaibify.gui import pipelineRunner

    class _AmpleDocker:
        def ftResultExecuteCommand(self, sContainerId, sCommand,
                                    sWorkdir=None):
            if sCommand.startswith("df "):
                return (0, _S_DF_OUTPUT_GENEROUS)
            return (0, "")

        def fbaFetchFile(self, sContainerId, sPath):
            raise FileNotFoundError(sPath)

    dictWorkflow = {
        "listSteps": [{
            "sName": "S1", "sDirectory": "stepA",
            "saDataCommands": [], "saPlotCommands": [],
        }],
    }
    listWarnings = pipelineRunner._flistCollectPreflightWarnings(
        _AmpleDocker(), "cid", dictWorkflow,
    )
    assert not any(
        "Workspace free space" in sWarning
        for sWarning in listWarnings
    )
