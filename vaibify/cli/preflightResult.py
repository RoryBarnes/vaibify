"""Structured pre-flight check result for vaibify CLI commands.

Every ``vaibify`` subcommand that wants to surface environment-level
diagnostics (Docker daemon reachable, host port free, image present,
context recognised) returns a list of ``PreflightResult`` records and
hands them to ``fnPrintPreflightReport``. A single shared shape keeps
human output consistent across commands and lets later rounds add
machine-readable rendering without rewriting callers.
"""

from dataclasses import dataclass


__all__ = [
    "PreflightResult", "fnPrintPreflightReport",
    "fnPrintScopedPreflightReport", "flistRenderResultsForJson",
    "S_LEVEL_OK", "S_LEVEL_WARN", "S_LEVEL_FAIL", "S_LEVEL_INFO",
    "S_LEVEL_NOT_CHECKED", "S_SCOPE_HOST", "S_SCOPE_CONTAINER",
    "S_SCOPE_PROJECT",
]


S_LEVEL_OK = "ok"
S_LEVEL_WARN = "warn"
S_LEVEL_FAIL = "fail"
S_LEVEL_INFO = "info"
# A check that could not be assessed. NEVER rendered as ``ok`` and
# never counted as one: a lane that reports success for having run
# nothing is the shape this repository has shipped twice (the
# ``docker info || exit 0`` CI guard, and the falsification legs that
# timed out for weeks).
S_LEVEL_NOT_CHECKED = "not-checked"

S_SCOPE_HOST = "host"
S_SCOPE_CONTAINER = "container"
S_SCOPE_PROJECT = "project"

_T_SCOPE_ORDER = (S_SCOPE_HOST, S_SCOPE_CONTAINER, S_SCOPE_PROJECT)

_DICT_SCOPE_HEADING = {
    S_SCOPE_HOST: "This machine",
    S_SCOPE_CONTAINER: "Inside the running container",
    S_SCOPE_PROJECT: "Project coherence",
}


@dataclass
class PreflightResult:
    """One pre-flight check outcome.

    ``sScope`` says WHERE the fact lives -- this host, inside the
    running container, or in the project's own records -- which is what
    lets a report group its findings and a researcher scope a run.
    ``sMechanism`` is the long "how this is decided" text; it is kept
    OUT of the default report and printed only by ``doctor --explain``,
    so the check that knows the mechanism is the thing that states it
    rather than a second catalogue that drifts from it.
    """

    sName: str
    sLevel: str
    sMessage: str
    sRemediation: str = ""
    sCommand: str = ""
    sScope: str = S_SCOPE_HOST
    sMechanism: str = ""


_DICT_LEVEL_PREFIX = {
    S_LEVEL_OK: "[ok]",
    S_LEVEL_WARN: "[warn]",
    S_LEVEL_FAIL: "[fail]",
    S_LEVEL_INFO: "[info]",
    S_LEVEL_NOT_CHECKED: "[not checked]",
}

_SET_LEVELS_WITH_DETAIL = {S_LEVEL_FAIL, S_LEVEL_WARN, S_LEVEL_NOT_CHECKED}


def _fsLevelPrefix(sLevel):
    """Return the bracketed prefix for sLevel (passthrough on unknown)."""
    return _DICT_LEVEL_PREFIX.get(sLevel, f"[{sLevel}]")


def _fnPrintRemediationBlock(sRemediation):
    """Print a multi-line remediation block, indented under its result."""
    for sLine in sRemediation.splitlines():
        print(f"    {sLine}")


def _fnPrintCommandLine(sCommand):
    """Print a copy-pasteable shell command indented under its result."""
    print(f"    $ {sCommand}")


def _fnPrintDetailBlock(preflightResult):
    """Print remediation text and command, when present."""
    if preflightResult.sRemediation:
        _fnPrintRemediationBlock(preflightResult.sRemediation)
    if preflightResult.sCommand:
        _fnPrintCommandLine(preflightResult.sCommand)


def _fnPrintOneResult(preflightResult):
    """Print one result line plus its detail block."""
    sPrefix = _fsLevelPrefix(preflightResult.sLevel)
    print(f"{sPrefix} {preflightResult.sName}: {preflightResult.sMessage}")
    if preflightResult.sLevel in _SET_LEVELS_WITH_DETAIL:
        _fnPrintDetailBlock(preflightResult)


def fnPrintPreflightReport(listResults):
    """Print one line per result; expand remediation under each fail/warn."""
    for preflightResult in listResults:
        _fnPrintOneResult(preflightResult)


def _flistSelectScope(listResults, sScope):
    """Return the assessed results belonging to one scope."""
    return [
        preflightResult for preflightResult in listResults
        if preflightResult.sScope == sScope
        and preflightResult.sLevel != S_LEVEL_NOT_CHECKED
    ]


def _fnPrintNotCheckedGroup(listResults):
    """Print the unassessed checks together, each with its reason.

    Their own group, deliberately. Interleaved among the passes they
    read as a quieter kind of ok, which is exactly the reading that
    makes an unassessed check dangerous.
    """
    listUnassessed = [
        preflightResult for preflightResult in listResults
        if preflightResult.sLevel == S_LEVEL_NOT_CHECKED
    ]
    if not listUnassessed:
        return
    print("\nNot checked")
    for preflightResult in listUnassessed:
        _fnPrintOneResult(preflightResult)


def fnPrintScopedPreflightReport(listResults):
    """Print the report grouped by scope, unassessed checks last."""
    for sScope in _T_SCOPE_ORDER:
        listScoped = _flistSelectScope(listResults, sScope)
        if not listScoped:
            continue
        print(f"\n{_DICT_SCOPE_HEADING[sScope]}")
        for preflightResult in listScoped:
            _fnPrintOneResult(preflightResult)
    _fnPrintNotCheckedGroup(listResults)


def flistRenderResultsForJson(listResults):
    """Return the results as a JSON-ready list of dicts."""
    return [
        {
            "sName": preflightResult.sName,
            "sLevel": preflightResult.sLevel,
            "sScope": preflightResult.sScope,
            "sMessage": preflightResult.sMessage,
            "sRemediation": preflightResult.sRemediation,
            "sCommand": preflightResult.sCommand,
        }
        for preflightResult in listResults
    ]
