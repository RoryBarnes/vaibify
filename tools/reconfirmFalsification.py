#!/usr/bin/env python3
"""Re-confirm every falsification test still kills its recorded mutation.

This is the standing "negative control" for the test suite. For each entry
in ``tests.falsificationRegistry.LIST_FALSIFICATIONS`` it:

  1. requires the test to PASS on clean code (precondition),
  2. applies ``old`` -> ``new`` in the source (``old`` must occur exactly once),
  3. requires the mutated source to still COMPILE (a mutation that breaks
     syntax would make pytest exit nonzero for the wrong reason),
  4. requires the test to then FAIL with an assertion failure -- pytest
     exit code 1, NOT a collection/internal error -- which is the kill,
  5. restores the source from the in-memory original bytes.

It prints KILLED / SURVIVED / ERROR per entry, lists any
``falsification``-marked test that has no registry entry, and exits
nonzero unless every entry is KILLED and every marked test is covered.

Hermetic: every touched source file's original bytes are captured up
front and restored from that in-memory snapshot in an outer ``finally``,
so the working tree is returned to its exact starting state (including
uncommitted edits) on any exit path -- normal completion, exception, or
interrupt. ``git checkout --`` is used ONLY as a last-resort backstop for
files whose in-memory restore write itself failed; a clean run never
touches HEAD state. The guarantee covers exactly the registry ``source``
files; side effects a test writes elsewhere are not tracked (the marked
tests use ``tmp_path``). Run it deliberately -- it mutates source, so it
is NOT collected by ``pytest tests/``:

    python tools/reconfirmFalsification.py
"""

import pathlib
import subprocess
import sys


REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from tests.falsificationRegistry import LIST_FALSIFICATIONS  # noqa: E402


def _fiRunTest(sNodeId):
    """Return the pytest exit code for running just this test node.

    Exit 0 = passed; 1 = a test failed (assertion); any other nonzero is a
    collection/internal error and must NOT be credited as a kill.
    """
    result = subprocess.run(
        [sys.executable, "-m", "pytest", sNodeId, "-q",
         "-p", "no:cacheprovider"],
        cwd=REPO, capture_output=True, text=True,
    )
    return result.returncode


def _fbMutationCompiles(sMutated, pathSource):
    """Return True when the mutated source still parses as Python."""
    try:
        compile(sMutated, str(pathSource), "exec")
        return True
    except SyntaxError:
        return False


def _fsReconfirmOne(entry, sOriginal):
    """Apply one mutation, return the kill status, always restore the file."""
    pathSource = REPO / entry.source
    if entry.old not in sOriginal:
        return "ERROR: old-text absent"
    if sOriginal.count(entry.old) != 1:
        return "ERROR: old-text not unique"
    sMutated = sOriginal.replace(entry.old, entry.new, 1)
    if not _fbMutationCompiles(sMutated, pathSource):
        return "ERROR: mutation does not compile"
    if _fiRunTest(entry.nodeid) != 0:
        return "ERROR: test does not pass on clean code"
    try:
        pathSource.write_text(sMutated, encoding="utf-8")
        iCode = _fiRunTest(entry.nodeid)
    finally:
        pathSource.write_text(sOriginal, encoding="utf-8")
    if iCode == 0:
        return "SURVIVED: test did NOT catch the mutation"
    if iCode == 1:
        return "KILLED"
    return f"ERROR: pytest exit {iCode} is not an assertion failure"


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


def _fdictCaptureOriginals():
    """Snapshot every registry source file's bytes before any mutation."""
    return {
        sSource: (REPO / sSource).read_text(encoding="utf-8")
        for sSource in sorted({entry.source for entry in LIST_FALSIFICATIONS})
    }


def _fnRestoreOriginals(dictOriginal):
    """Restore each source from its snapshot; git-checkout only on write failure."""
    listFailed = []
    for sSource, sBytes in dictOriginal.items():
        try:
            (REPO / sSource).write_text(sBytes, encoding="utf-8")
        except OSError:
            listFailed.append(sSource)
    if listFailed:
        subprocess.run(["git", "checkout", "--", *listFailed], cwd=REPO)


def main():
    """Re-confirm all entries; exit nonzero on any failure or coverage gap."""
    dictOriginal = _fdictCaptureOriginals()
    try:
        listResults = [
            (entry.nodeid, _fsReconfirmOne(entry, dictOriginal[entry.source]))
            for entry in LIST_FALSIFICATIONS
        ]
    finally:
        _fnRestoreOriginals(dictOriginal)
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
