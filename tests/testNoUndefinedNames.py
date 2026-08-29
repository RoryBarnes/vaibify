"""A name bound in no scope must not reach a route handler.

On 2026-08-29 the council's ``/resume`` and ``/retry`` handlers both
read a free ``dictCampaign`` — a name bound only inside the START
handler, an entirely different function. Every call raised
``NameError`` and answered 500, and the researcher's Retry button had
been dead since the change landed.

A 10,367-test suite was green over it, because no test drove either
route's pre-flight. That is the shape of defect this file exists for:
not a wrong answer, but a line that cannot run at all, in a branch no
fixture reaches. A unit test per route would have caught this one
instance; a static undefined-name sweep catches the CLASS, including
every route nobody has written a test for yet.

The seed below is FROZEN and may only shrink. Every entry in it is a
name used in a docstring type annotation or an ``__all__`` string,
where the name is documentation rather than executed code. A new entry
is a bug until proven otherwise — add one only with a reason, and
never to silence a real free variable.

Line numbers are deliberately excluded from the key: they change on
every edit above them, and a seed that goes stale on unrelated commits
is one people learn to regenerate without reading.
"""

import re
import subprocess
import sys

# Fails rather than skips when absent: a lane that reports success for
# having run nothing is worse than no lane (see the `.[dev]` note in
# pyproject.toml, and the docker-info guard this repo removed).
import pyflakes  # noqa: F401 — presence is the point

_RE_LOCATION = re.compile(r":\d+:\d+:? ")

# file -> {message: count}. Frozen 2026-08-29.
DICT_SEEDED_UNDEFINED_NAMES = {
    "vaibify/cli/commandDoctor.py": {
        "undefined name 'doctor' in __all__": 1},
    "vaibify/cli/commandReproduce.py": {
        "undefined name 'reproduce' in __all__": 1},
    "vaibify/gui/commitCarrier.py": {
        "undefined name 'Callable'": 1,
        "undefined name 'MutationAdmission'": 1,
        "undefined name 'Task'": 2},
    "vaibify/gui/containerOwnership.py": {
        "undefined name 'StartReservation'": 1,
        "undefined name 'WebSocket'": 1},
    "vaibify/gui/startReservation.py": {
        "undefined name 'OwnershipIdentity'": 1,
        "undefined name 'Popen'": 1},
    "vaibify/gui/terminalContainment.py": {
        "undefined name 'DockerConnection'": 1,
        "undefined name 'TerminalSession'": 1},
}


def _fdictScanForUndefinedNames():
    """Return {file: {message: count}} for the package as it stands."""
    processScan = subprocess.run(
        [sys.executable, "-m", "pyflakes", "vaibify/"],
        capture_output=True, text=True,
    )
    dictFound = {}
    for sLine in (processScan.stdout + processScan.stderr).splitlines():
        if "undefined name" not in sLine:
            continue
        sPath, _, sRest = sLine.partition(":")
        sMessage = _RE_LOCATION.sub("", sLine[len(sPath):]).strip()
        dictFound.setdefault(sPath, {})
        dictFound[sPath][sMessage] = (
            dictFound[sPath].get(sMessage, 0) + 1)
    return dictFound


def test_no_new_undefined_name_reaches_the_package():
    """A free variable in any module fails the build.

    Kills: the ``dictCampaign`` NameError that made two council routes
    answer 500 while the whole suite stayed green.
    """
    dictFound = _fdictScanForUndefinedNames()
    listNew = []
    for sPath, dictMessages in sorted(dictFound.items()):
        dictSeeded = DICT_SEEDED_UNDEFINED_NAMES.get(sPath, {})
        for sMessage, iCount in sorted(dictMessages.items()):
            iSeeded = dictSeeded.get(sMessage, 0)
            if iCount > iSeeded:
                listNew.append(
                    f"{sPath}: {sMessage} (found {iCount}, seeded {iSeeded})")
    assert not listNew, (
        "a name bound in no scope reached the package — this is a "
        "NameError at runtime, in a branch no test may cover:\n  "
        + "\n  ".join(listNew))


def test_the_seed_may_only_shrink():
    """A fixed entry must lower the seed in the same commit.

    Otherwise the seed slowly becomes a list of things nobody has
    looked at, and its size stops meaning anything.
    """
    dictFound = _fdictScanForUndefinedNames()
    listStale = []
    for sPath, dictMessages in sorted(
            DICT_SEEDED_UNDEFINED_NAMES.items()):
        for sMessage, iSeeded in sorted(dictMessages.items()):
            iFound = dictMessages and dictFound.get(sPath, {}).get(
                sMessage, 0)
            if iFound < iSeeded:
                listStale.append(
                    f"{sPath}: {sMessage} (seeded {iSeeded}, now {iFound})")
    assert not listStale, (
        "these were fixed but the seed still budgets for them; lower it "
        "in the same commit:\n  " + "\n  ".join(listStale))
