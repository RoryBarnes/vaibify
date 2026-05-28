"""Tests for vaibify.cli.preflightResult dataclass and report printer."""

from vaibify.cli.preflightResult import (
    PreflightResult,
    fnPrintPreflightReport,
)


def test_PreflightResult_defaults_remediation_to_empty():
    resultPreflight = PreflightResult(
        sName="docker-daemon",
        sLevel="ok",
        sMessage="Docker daemon reachable",
    )
    assert resultPreflight.sRemediation == ""
    assert resultPreflight.sCommand == ""


def test_PreflightResult_accepts_full_field_set():
    resultPreflight = PreflightResult(
        sName="port-8050",
        sLevel="fail",
        sMessage="Port 8050 already in use",
        sRemediation="Stop the conflicting process\nor pick a different port",
        sCommand="lsof -iTCP:8050",
    )
    assert resultPreflight.sName == "port-8050"
    assert resultPreflight.sLevel == "fail"
    assert resultPreflight.sMessage == "Port 8050 already in use"
    assert "Stop the conflicting process" in resultPreflight.sRemediation
    assert resultPreflight.sCommand == "lsof -iTCP:8050"


def test_fnPrintPreflightReport_emits_ok_warn_fail_prefixes(capsys):
    listResults = [
        PreflightResult(sName="alpha", sLevel="ok", sMessage="alpha ok"),
        PreflightResult(sName="beta", sLevel="warn", sMessage="beta warn"),
        PreflightResult(sName="gamma", sLevel="fail", sMessage="gamma fail"),
    ]
    fnPrintPreflightReport(listResults)
    sCaptured = capsys.readouterr().out
    assert "[ok] alpha: alpha ok" in sCaptured
    assert "[warn] beta: beta warn" in sCaptured
    assert "[fail] gamma: gamma fail" in sCaptured


def test_fnPrintPreflightReport_indents_remediation_under_fail(capsys):
    listResults = [
        PreflightResult(
            sName="port-8050",
            sLevel="fail",
            sMessage="bound by another process",
            sRemediation="Run: lsof -iTCP:8050\nThen kill the holder",
        ),
    ]
    fnPrintPreflightReport(listResults)
    sCaptured = capsys.readouterr().out
    assert "[fail] port-8050: bound by another process" in sCaptured
    assert "    Run: lsof -iTCP:8050" in sCaptured
    assert "    Then kill the holder" in sCaptured


def test_fnPrintPreflightReport_renders_warn_remediation(capsys):
    """Warn-level results now print their remediation too (diagnostic probes)."""
    listResults = [
        PreflightResult(
            sName="colima-hostagent-log",
            sLevel="warn",
            sMessage="recent stale-lock error",
            sRemediation="Colima VM lock is stale.",
            sCommand="colima stop --force && colima start",
        ),
    ]
    fnPrintPreflightReport(listResults)
    sCaptured = capsys.readouterr().out
    assert "[warn] colima-hostagent-log" in sCaptured
    assert "    Colima VM lock is stale." in sCaptured
    assert "    $ colima stop --force && colima start" in sCaptured


def test_fnPrintPreflightReport_omits_detail_for_ok(capsys):
    """Ok-level results do not print remediation or command lines."""
    listResults = [
        PreflightResult(
            sName="alpha",
            sLevel="ok",
            sMessage="alpha ok",
            sRemediation="should not appear",
            sCommand="should not appear either",
        ),
    ]
    fnPrintPreflightReport(listResults)
    sCaptured = capsys.readouterr().out
    assert "should not appear" not in sCaptured


def test_fnPrintPreflightReport_omits_block_when_remediation_empty(capsys):
    listResults = [
        PreflightResult(
            sName="alpha",
            sLevel="fail",
            sMessage="fail without workaround",
        ),
    ]
    fnPrintPreflightReport(listResults)
    sCaptured = capsys.readouterr().out
    assert sCaptured.count("\n") == 1


def test_fnPrintPreflightReport_renders_command_under_fail(capsys):
    """A fail-level result with sCommand prints the `$` line."""
    listResults = [
        PreflightResult(
            sName="docker-daemon",
            sLevel="fail",
            sMessage="Docker daemon not reachable.",
            sRemediation="Colima is not running.",
            sCommand="colima start",
        ),
    ]
    fnPrintPreflightReport(listResults)
    sCaptured = capsys.readouterr().out
    assert "    Colima is not running." in sCaptured
    assert "    $ colima start" in sCaptured
