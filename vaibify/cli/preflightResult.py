"""Structured pre-flight check result for vaibify CLI commands.

Every ``vaibify`` subcommand that wants to surface environment-level
diagnostics (Docker daemon reachable, host port free, image present,
context recognised) returns a list of ``PreflightResult`` records and
hands them to ``fnPrintPreflightReport``. A single shared shape keeps
human output consistent across commands and lets later rounds add
machine-readable rendering without rewriting callers.
"""

from dataclasses import dataclass


__all__ = ["PreflightResult", "fnPrintPreflightReport"]


@dataclass
class PreflightResult:
    """One pre-flight check outcome."""

    sName: str
    sLevel: str
    sMessage: str
    sRemediation: str = ""


_DICT_LEVEL_PREFIX = {
    "ok": "[ok]",
    "warn": "[warn]",
    "fail": "[fail]",
    "info": "[info]",
}


def _fsLevelPrefix(sLevel):
    """Return the bracketed prefix for sLevel (passthrough on unknown)."""
    return _DICT_LEVEL_PREFIX.get(sLevel, f"[{sLevel}]")


def _fnPrintRemediationBlock(sRemediation):
    """Print a multi-line remediation block, indented under its result."""
    for sLine in sRemediation.splitlines():
        print(f"    {sLine}")


def fnPrintPreflightReport(listResults):
    """Print one line per result; expand remediation under each fail."""
    for resultPreflight in listResults:
        sPrefix = _fsLevelPrefix(resultPreflight.sLevel)
        print(f"{sPrefix} {resultPreflight.sName}: {resultPreflight.sMessage}")
        if resultPreflight.sLevel == "fail" and resultPreflight.sRemediation:
            _fnPrintRemediationBlock(resultPreflight.sRemediation)
