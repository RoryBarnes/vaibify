#!/usr/bin/env python3
"""Re-confirm every falsification test still kills its recorded mutation.

This is the standing "negative control" for the test suite. For each entry
in ``tests.falsificationRegistry.LIST_FALSIFICATIONS`` it:

  1. requires the test to PASS on clean code (precondition),
  2. applies ``old`` -> ``new`` in the source (``old`` must occur exactly once),
  3. requires the test to then FAIL (the kill),
  4. restores the source unconditionally.

It prints KILLED / SURVIVED / ERROR per entry, lists any
``falsification``-marked test that has no registry entry, and exits
nonzero unless every entry is KILLED and every marked test is covered.

Hermetic: each file is restored in a ``finally``, and a final
``git checkout --`` on the touched source files backstops a crash. Run it
deliberately -- it mutates source, so it is NOT collected by
``pytest tests/``:

    python tools/reconfirmFalsification.py
"""

import pathlib
import subprocess
import sys


REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from tests.falsificationRegistry import LIST_FALSIFICATIONS  # noqa: E402


def _fbTestPasses(sNodeId):
    """Return True when running just this test node passes."""
    result = subprocess.run(
        [sys.executable, "-m", "pytest", sNodeId, "-q",
         "-p", "no:cacheprovider"],
        cwd=REPO, capture_output=True, text=True,
    )
    return result.returncode == 0


def _fsReconfirmOne(entry):
    """Apply one mutation, return the kill status, always restore the file."""
    pathSource = REPO / entry.source
    sOriginal = pathSource.read_text(encoding="utf-8")
    try:
        if entry.old not in sOriginal:
            return "ERROR: old-text absent"
        if sOriginal.count(entry.old) != 1:
            return "ERROR: old-text not unique"
        if not _fbTestPasses(entry.nodeid):
            return "ERROR: test does not pass on clean code"
        pathSource.write_text(
            sOriginal.replace(entry.old, entry.new, 1), encoding="utf-8",
        )
        if _fbTestPasses(entry.nodeid):
            return "SURVIVED: test did NOT catch the mutation"
        return "KILLED"
    finally:
        pathSource.write_text(sOriginal, encoding="utf-8")


def _flistMarkedTestsWithoutEntry():
    """Return falsification-marked test node ids absent from the registry."""
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-m", "falsification",
         "--collect-only", "-q", "-p", "no:cacheprovider"],
        cwd=REPO, capture_output=True, text=True,
    )
    setMarked = {
        sLine.strip() for sLine in result.stdout.splitlines()
        if "::" in sLine and not sLine.startswith(" ")
    }
    listRegistered = [entry.nodeid for entry in LIST_FALSIFICATIONS]

    def fbCovered(sMarked):
        return any(
            sMarked == sReg or sMarked.startswith(sReg + "[")
            for sReg in listRegistered
        )

    return sorted(s for s in setMarked if not fbCovered(s))


def main():
    """Re-confirm all entries; exit nonzero on any failure or coverage gap."""
    listResults = [
        (entry.nodeid, _fsReconfirmOne(entry))
        for entry in LIST_FALSIFICATIONS
    ]
    listSources = sorted({entry.source for entry in LIST_FALSIFICATIONS})
    subprocess.run(["git", "checkout", "--", *listSources], cwd=REPO)
    for sNodeId, sStatus in listResults:
        print(f"{sStatus:48}  {sNodeId}")
    listBad = [r for r in listResults if not r[1].startswith("KILLED")]
    listUncovered = _flistMarkedTestsWithoutEntry()
    print(f"\n{len(listResults) - len(listBad)}/{len(listResults)} "
          "kill-confirmed")
    if listUncovered:
        print(f"\n{len(listUncovered)} falsification-marked test(s) with "
              "no registry entry:")
        for sNodeId in listUncovered:
            print("  " + sNodeId)
    if listBad or listUncovered:
        sys.exit(1)


if __name__ == "__main__":
    main()
