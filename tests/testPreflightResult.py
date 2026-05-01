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


def test_PreflightResult_accepts_full_field_set():
    resultPreflight = PreflightResult(
        sName="port-8050",
        sLevel="fail",
        sMessage="Port 8050 already in use",
        sRemediation="Stop the conflicting process\nor pick a different port",
    )
    assert resultPreflight.sName == "port-8050"
    assert resultPreflight.sLevel == "fail"
    assert resultPreflight.sMessage == "Port 8050 already in use"
    assert "Stop the conflicting process" in resultPreflight.sRemediation


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


def test_fnPrintPreflightReport_omits_remediation_for_non_fail(capsys):
    listResults = [
        PreflightResult(
            sName="alpha",
            sLevel="warn",
            sMessage="warn line",
            sRemediation="should not appear",
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
