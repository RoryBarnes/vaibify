"""Static analysis finds nothing new: pyflakes errors and pylint errors.

On 2026-08-29 the council's ``/resume`` and ``/retry`` handlers both
read a free ``dictCampaign`` — a name bound only inside the START
handler, an entirely different function. Every call raised
``NameError`` and answered 500, and the researcher's Retry button had
been dead since the change landed.

A 10,367-test suite was green over it, because no test drove either
route's pre-flight. That is the shape of defect this file exists for:
not a wrong answer, but a line that cannot run at all, in a branch no
fixture reaches. A unit test per route would have caught this one
instance; a static sweep catches the CLASS, including every route
nobody has written a test for yet.

This module began as an undefined-name sweep and now runs the whole
error-grade pass, because the same lesson recurs: a call with the wrong
number of arguments (the ``generate-tests-deterministic`` call that
shipped one argument short), a module attribute that does not exist, or
a variable read before it is assigned are all lines that cannot run.

Two tools, one rule. ``pyflakes`` reports every message except
"imported but unused": the package deliberately re-exports names
through facade modules (``# noqa: F401``, patched by tests), pyflakes
cannot tell a facade from dead code, and an unused import is not an
error. ``pylint --errors-only`` reports its error and fatal categories;
the checks it gets wrong for this codebase are disabled in
``pyproject.toml`` under ``[tool.pylint]``, each with its reason.

Each seed below is FROZEN and may only shrink. A new entry is a bug
until proven otherwise — add one only with a reason, and never to
silence a real defect. Line numbers are deliberately excluded from the
key: they change on every edit above them, and a seed that goes stale
on unrelated commits is one people learn to regenerate without reading.
"""

import functools
import os
import re
import subprocess
import sys

# Fails rather than skips when absent: a lane that reports success for
# having run nothing is worse than no lane (see the `.[dev]` note in
# pyproject.toml, and the docker-info guard this repo removed).
import pyflakes  # noqa: F401 — presence is the point
import pylint  # noqa: F401 — presence is the point

_RE_LOCATION = re.compile(r":\d+:\d+:? ")
_RE_LINE_REFERENCE = re.compile(r" from line \d+")
S_UNUSED_IMPORT_MESSAGE = "imported but unused"
I_PYLINT_FATAL_OR_USAGE_BITS = 1 | 32

# file -> {message: count}. The budget only falls. The Popen entry is a
# string annotation for a process handle: naming it needs an import of
# subprocess, which the mutation inventory counts as an undisposed
# capability acquisition, and a type-only import is not one. The
# reviewer decides between exempting type-only imports there and
# registering a typing-only prefix here. The pipelineRunner entries are
# re-export facades (``# noqa: F401``) that a function then imports
# again locally. The dataLoaders entries are the loader source that is
# embedded for the container, which repeats the host module's imports
# on purpose (see "begin loader source" in that module).
DICT_SEEDED_PYFLAKES_FINDINGS = {
    "vaibify/gui/startReservation.py": {
        "undefined name 'Popen'": 1},
    "vaibify/gui/pipelineRunner.py": {
        "redefinition of unused '_fiRunTestCommands'": 1,
        "redefinition of unused 'fnPruneOldLogs'": 1},
    "vaibify/gui/dataLoaders.py": {
        "redefinition of unused 'json'": 1,
        "redefinition of unused 'pathlib'": 1,
        "redefinition of unused 're'": 1,
        "redefinition of unused 'np'": 1},
}

# file -> {"symbol: message": count}. The budget only falls. It is empty:
# the one finding it once held, the dataset-download route calling a
# dispatcher function that exists nowhere, went away when the route was
# withdrawn.
DICT_SEEDED_PYLINT_FINDINGS = {}


def _fnRecordFinding(dictFound, sPath, sMessage):
    dictFound.setdefault(sPath, {})
    dictFound[sPath][sMessage] = dictFound[sPath].get(sMessage, 0) + 1


@functools.lru_cache(maxsize=None)
def _fdictScanWithPyflakes():
    """Return {file: {message: count}} for every non-import pyflakes finding."""
    processScan = subprocess.run(
        [sys.executable, "-m", "pyflakes", "vaibify/"],
        capture_output=True, text=True,
    )
    dictFound = {}
    for sLine in (processScan.stdout + processScan.stderr).splitlines():
        if not sLine.strip() or S_UNUSED_IMPORT_MESSAGE in sLine:
            continue
        sPath, _, _ = sLine.partition(":")
        sMessage = _RE_LOCATION.sub("", sLine[len(sPath):]).strip()
        sMessage = _RE_LINE_REFERENCE.sub("", sMessage)
        _fnRecordFinding(dictFound, sPath, sMessage)
    return dictFound


@functools.lru_cache(maxsize=None)
def _fdictScanWithPylint():
    """Return {file: {"symbol: message": count}} for pylint's error checks."""
    iJobs = max(1, (os.cpu_count() or 2) - 1)
    processScan = subprocess.run(
        [sys.executable, "-m", "pylint", "--errors-only", "--score=n",
         "--persistent=n", f"--jobs={iJobs}",
         "--msg-template={path}:{line}:{symbol}: {msg}", "vaibify/"],
        capture_output=True, text=True,
    )
    assert not processScan.returncode & I_PYLINT_FATAL_OR_USAGE_BITS, (
        f"pylint did not run to completion (exit {processScan.returncode})"
        f":\n{processScan.stdout[-1500:]}\n{processScan.stderr[-1500:]}"
    )
    dictFound = {}
    for sLine in processScan.stdout.splitlines():
        if sLine.startswith("*************") or not sLine.strip():
            continue
        sPath, _, sRest = sLine.partition(":")
        sMessage = sRest.split(":", 1)[1].strip()
        _fnRecordFinding(dictFound, sPath, sMessage)
    return dictFound


def _flistFindNewFindings(dictFound, dictSeeded):
    listNew = []
    for sPath, dictMessages in sorted(dictFound.items()):
        dictSeededHere = dictSeeded.get(sPath, {})
        for sMessage, iCount in sorted(dictMessages.items()):
            iSeeded = dictSeededHere.get(sMessage, 0)
            if iCount > iSeeded:
                listNew.append(
                    f"{sPath}: {sMessage} (found {iCount}, seeded {iSeeded})")
    return listNew


def _flistFindStaleSeeds(dictFound, dictSeeded):
    listStale = []
    for sPath, dictMessages in sorted(dictSeeded.items()):
        for sMessage, iSeeded in sorted(dictMessages.items()):
            iFound = dictFound.get(sPath, {}).get(sMessage, 0)
            if iFound < iSeeded:
                listStale.append(
                    f"{sPath}: {sMessage} (seeded {iSeeded}, now {iFound})")
    return listStale


def test_no_new_pyflakes_finding_reaches_the_package():
    """An undefined name, redefinition or similar pyflakes error fails the build.

    Kills: the ``dictCampaign`` NameError that made two council routes
    answer 500 while the whole suite stayed green.
    """
    listNew = _flistFindNewFindings(
        _fdictScanWithPyflakes(), DICT_SEEDED_PYFLAKES_FINDINGS)
    assert not listNew, (
        "pyflakes found a defect in a branch no test may cover:\n  "
        + "\n  ".join(listNew))


def test_no_new_pylint_error_reaches_the_package():
    """A pylint error-category finding fails the build.

    Kills: a call with the wrong number of arguments (E1120/E1121), a
    module attribute that does not exist (E1101), and a variable read
    before assignment (E0601) -- none of which pyflakes can see.
    """
    listNew = _flistFindNewFindings(
        _fdictScanWithPylint(), DICT_SEEDED_PYLINT_FINDINGS)
    assert not listNew, (
        "pylint found an error that fails at runtime, in a branch no "
        "test may cover:\n  " + "\n  ".join(listNew))


def test_the_seeds_may_only_shrink():
    """A fixed entry must lower its seed in the same commit.

    Otherwise a seed slowly becomes a list of things nobody has looked
    at, and its size stops meaning anything.
    """
    listStale = (
        _flistFindStaleSeeds(
            _fdictScanWithPyflakes(), DICT_SEEDED_PYFLAKES_FINDINGS)
        + _flistFindStaleSeeds(
            _fdictScanWithPylint(), DICT_SEEDED_PYLINT_FINDINGS))
    assert not listStale, (
        "these were fixed but a seed still budgets for them; lower it "
        "in the same commit:\n  " + "\n  ".join(listStale))
