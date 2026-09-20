"""``vaibify doctor`` warns while a translated interpreter still runs.

A Python built for a different processor than the machine's runs only
through the operating system's translation layer. When an OS upgrade
withdrew that layer, every command installed into such a Python died
with "Bad CPU type in executable", the shell reported "command not
found", and no vaibify code could run to explain it. The only moment
the diagnosis can be delivered is BEFORE the break, so the check must
fire on a working, translated interpreter.

Two lanes. The unit lane pins the verdicts with the kernel answer
stubbed. The live lane asks the kernel through a second, independent
route -- the ``sysctl`` command, which inherits this process's
translation -- and requires the check to agree, which is the only
witness a stub cannot satisfy.
"""

import subprocess
import sys
from unittest.mock import patch

import pytest

from vaibify.cli.commandDoctor import flistRunDoctorChecks
from vaibify.cli.doctorHostChecks import fpreflightInterpreterArchitecture
from vaibify.cli.preflightResult import (
    S_LEVEL_OK, S_LEVEL_WARN, S_SCOPE_HOST,
)


S_PROBE = "vaibify.cli.doctorHostChecks.fbInterpreterRunsTranslated"


def _fpreflightOnMacOs(bTranslated):
    """Run the check as if on macOS with the kernel answer fixed."""
    with patch(
        "vaibify.cli.doctorHostChecks.sys.platform", "darwin",
    ), patch(S_PROBE, return_value=bTranslated):
        return fpreflightInterpreterArchitecture()


def test_translated_interpreter_warns_and_names_the_interpreter():
    preflightResult = _fpreflightOnMacOs(True)
    assert preflightResult.sLevel == S_LEVEL_WARN
    assert preflightResult.sScope == S_SCOPE_HOST
    assert sys.executable in preflightResult.sMessage
    assert "Bad CPU type" in preflightResult.sMessage
    assert preflightResult.sRemediation
    assert preflightResult.sCommand


def test_native_interpreter_reports_ok():
    preflightResult = _fpreflightOnMacOs(False)
    assert preflightResult.sLevel == S_LEVEL_OK
    assert sys.executable in preflightResult.sMessage


def test_platforms_without_a_translation_layer_say_nothing():
    with patch(
        "vaibify.cli.doctorHostChecks.sys.platform", "linux",
    ), patch(S_PROBE) as mockProbe:
        assert fpreflightInterpreterArchitecture() is None
    mockProbe.assert_not_called()


def test_the_check_runs_ahead_of_every_scope_including_host_projects():
    """A host project skips the Docker battery, never this check."""
    with patch(
        "vaibify.cli.doctorHostChecks.sys.platform", "darwin",
    ), patch(S_PROBE, return_value=True), patch(
        "vaibify.cli.commandDoctor._fdictHostProjectOrNone",
        return_value={"sMode": "host", "sPath": "/nonexistent"},
    ), patch(
        "vaibify.cli.commandDoctor._flistHostProjectChecks",
        return_value=[],
    ):
        listResults = flistRunDoctorChecks(object(), False, False)
    listNames = [preflightResult.sName for preflightResult in listResults]
    assert listNames[:2] == ["installed-checkout", "interpreter-architecture"]


@pytest.mark.falsification
def test_the_probe_never_touches_libc_off_macos():
    """The build preflight reaches the probe on every platform.

    glibc exports no ``sysctlbyname``, so a ctypes lookup of it raises;
    on a Linux CI runner that traceback ended ``vaibify build`` before
    the preflight had reported anything (fresh-image-build, 2026-09-20).
    The doctor's own caller checked the platform first, which is how
    the probe stayed unguarded until a second caller arrived.

    Kills: dropping the platform test from the probe, so the libc
    lookup runs on Linux and raises where the fake below raises.
    """
    from vaibify.cli import doctorHostChecks

    def fnRaiseMissingSymbol(*args, **kwargs):
        raise AttributeError("libc.so.6: undefined symbol: sysctlbyname")

    with patch(
        "vaibify.cli.doctorHostChecks.sys.platform", "linux",
    ), patch.object(doctorHostChecks.ctypes, "CDLL", fnRaiseMissingSymbol):
        assert doctorHostChecks.fbInterpreterRunsTranslated() is False


@pytest.mark.skipif(
    sys.platform != "darwin", reason="only macOS exposes proc_translated",
)
def test_live_verdict_agrees_with_the_kernel_via_an_independent_route():
    """The in-process answer must match what a spawned probe inherits."""
    processResult = subprocess.run(
        ["sysctl", "-n", "sysctl.proc_translated"],
        capture_output=True, text=True, timeout=10,
    )
    bKernelSaysTranslated = processResult.stdout.strip() == "1"
    preflightResult = fpreflightInterpreterArchitecture()
    sExpectedLevel = S_LEVEL_WARN if bKernelSaysTranslated else S_LEVEL_OK
    assert preflightResult.sLevel == sExpectedLevel
