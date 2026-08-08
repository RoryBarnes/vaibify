#!/usr/bin/env python3
"""Re-confirm every falsification test still kills its recorded mutation.

This is the standing "negative control" for the test suite. For each entry
in ``tests.falsificationRegistry.LIST_FALSIFICATIONS`` it:

  1. requires the test to PASS on clean code (precondition -- asked
     once for every node id in a single pytest run, because the answer
     is the same for all of them and a separate interpreter start per
     entry doubled the wall clock; a failing batch falls back to
     per-entry checks so the offender is still named),
  2. applies ``old`` -> ``new`` in the source (``old`` must occur exactly once),
  3. requires the mutated source to still COMPILE (a mutation that breaks
     syntax would make pytest exit nonzero for the wrong reason),
  4. requires the test to then FAIL with an assertion failure -- pytest
     exit code 1, NOT a collection/internal error -- which is the kill,
  5. restores the source from the in-memory original bytes.

It prints KILLED / SURVIVED / ERROR per entry, lists any
``falsification``-marked test that has no registry entry, and exits
nonzero unless every entry is KILLED and every marked test is covered.

A mutation guarded by a real-container (``docker_live``) test cannot be
evaluated on a host with no Docker daemon, and there is no honest way to
score it there: crediting it is a false kill, and the child-side daemon
requirement -- which exists so a skip is never misread as a survivor --
turns it into an ERROR indistinguishable from a broken guard. Such
entries are therefore reported by name as NOT EVALUATED and left out of
the denominator. That is safe only because they ARE evaluated on every
lane that has a daemon; set ``VAIBIFY_REQUIRE_DOCKER_DAEMON`` on those
lanes so losing Docker turns them red instead of silently shrinking the
denominator.

STRUCTURAL ISOLATION -- this harness never touches your files.
Everything runs inside a disposable git worktree checked out from HEAD
and removed on exit. The previous design mutated the real working tree
and restored it from an in-memory snapshot, which was correct in the
normal case but had two failure modes that both bit in practice:

* Running it beside any other pytest invocation made that other run
  read half-mutated source, producing failures in tests nobody
  touched. Diagnosing this as flakiness or test-order leakage is the
  natural mistake, and it has been made more than once. A snapshot
  cannot fix it -- only not mutating shared files can.
* A crash between write and restore left the developer's checkout
  mutated.

Because the worktree is checked out from HEAD, uncommitted work is NOT
included by default. Silently verifying code you do not have would
defeat the point, so a dirty tree is refused unless you pass
``--include-local-diff``, which copies tracked modifications *and*
untracked non-ignored files into the worktree first.

    python tools/reconfirmFalsification.py
    python tools/reconfirmFalsification.py --include-local-diff
"""

import argparse
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile


REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

# The tree the mutations are applied to. Rebound by main() to the
# disposable worktree; only the registry import below reads the real
# repository.
PATH_TREE = REPO

from tests.falsificationRegistry import LIST_FALSIFICATIONS  # noqa: E402
from tests.testDockerConnectionLive import (  # noqa: E402
    S_OUTCOME_FAIL,
    S_OUTCOME_PROCEED,
    _fbDaemonReachable,
    fsDaemonRequirementOutcome,
)

# The env var the Docker-live suites read to turn their convenience
# skip into a failure. Named once here, matching the CI workflows.
S_REQUIRE_DAEMON_ENV = "VAIBIFY_REQUIRE_DOCKER_DAEMON"

# The marker that says a test drives a real container, so no host
# without a daemon can evaluate the mutation guarding it.
S_LIVE_DAEMON_MARKER = "docker_live"

# A private exit code for "the test never ran". pytest has none: a
# skipped test exits 0, which is indistinguishable from a passing one
# and reads, in this harness, as a mutant that survived.
I_EXIT_SKIPPED = 90


def _fiRunTest(sNodeId):
    """Return the pytest exit code for running just this test node.

    Exit 0 = passed; 1 = a test failed (assertion); any other nonzero is a
    collection/internal error and must NOT be credited as a kill.

    A SKIPPED test also exits 0, which is why the runners below refuse
    to read a skip as a pass -- see :func:`_fiRunTests`.

    Each invocation gets a FRESH bytecode cache (PYTHONPYCACHEPREFIX):
    Python validates cached .pyc files by source mtime in integer
    seconds plus file size, so a mutation that preserves the file size
    and lands within the same clock second as the previous write is
    served the PREVIOUS bytecode — the test passes against code it
    never ran, and a genuine kill reports SURVIVED. Observed on the
    fastest CI runner (macOS/py3.14, 2026-07-03) for exactly the
    same-size mutations; a cold cache per run removes the timing from
    the equation.
    """
    return _fiRunTests([sNodeId])


def _fiRunTests(listNodeIds):
    """Return the pytest exit code for running these test nodes together.

    Same contract and the same cold bytecode cache as
    :func:`_fiRunTest`; the list form exists so the shared precondition
    can be answered in one interpreter start instead of one per entry.
    """
    dictEnvironment = dict(os.environ)
    # An editable install resolves ``vaibify`` to the real checkout, so
    # without this the worktree's tests would import the very sources
    # the isolation exists to leave alone.
    dictEnvironment["PYTHONPATH"] = str(PATH_TREE)
    # A Docker-live falsification test skips when no daemon answers, and
    # a skip exits 0 -- which this harness would read as "the mutant
    # survived". Requiring the daemon turns that skip into a failure, so
    # a machine with no Docker reports an ERROR it can act on instead of
    # five phantom survivors.
    dictEnvironment[S_REQUIRE_DAEMON_ENV] = "1"
    with tempfile.TemporaryDirectory() as sPycachePrefix:
        dictEnvironment["PYTHONPYCACHEPREFIX"] = sPycachePrefix
        result = subprocess.run(
            [sys.executable, "-m", "pytest", *listNodeIds, "-q",
             "-p", "no:cacheprovider", "-rs"],
            cwd=PATH_TREE, capture_output=True, text=True,
            env=dictEnvironment,
        )
    if result.returncode == 0 and _fbOutputReportsASkip(result.stdout):
        # Belt and braces for every OTHER reason a test can skip: an
        # unevaluated mutation must never be reported as a survivor.
        return I_EXIT_SKIPPED
    return result.returncode


def _fbOutputReportsASkip(sOutput):
    """Return True when pytest's summary line counts a skip."""
    return bool(re.search(r"\b\d+ skipped\b", sOutput or ""))


def _fbMutationCompiles(sMutated, pathSource):
    """Return True when the mutated source is still syntactically valid.

    The check exists to separate "the test failed because of the
    mutation" from "the test failed because the file no longer
    parses". That distinction is only checkable here for Python;
    a registry entry may legitimately target a non-Python source (the
    JavaScript slug mirror, a shell hook), and running Python's
    ``compile`` over those reports a SyntaxError for every mutation,
    turning a genuine kill into a spurious ERROR.

    Non-Python sources are therefore accepted unparsed. They are not
    unchecked: the kill still requires pytest to exit 1 on an
    assertion, and any other exit code is reported as an error.
    """
    if pathSource.suffix != ".py":
        return True
    try:
        compile(sMutated, str(pathSource), "exec")
        return True
    except SyntaxError:
        return False


def _fbAllPreconditionsPassInOneRun(listEntries):
    """Return True when every registered test passes on clean code.

    The precondition -- "this test passes before the mutation" -- is
    identical for every entry and costs a full interpreter start plus a
    vaibify import each time it is checked separately. Asking it once
    for all node ids halves the process count for the whole run, which
    is the difference between finishing inside CI's ceiling and timing
    out. When the batch passes, per-entry precondition runs are skipped;
    when it fails, the caller falls back to checking each entry alone so
    the offender is still named precisely.
    """
    listNodeIds = sorted({entry.nodeid for entry in listEntries})
    return _fiRunTests(listNodeIds) == 0


def _fsReconfirmOne(entry, sOriginal, bPreconditionKnownGood=False):
    """Apply one mutation, return the kill status, always restore the file."""
    pathSource = PATH_TREE / entry.source
    if entry.old not in sOriginal:
        return "ERROR: old-text absent"
    iFound = sOriginal.count(entry.old)
    if iFound != entry.iExpectedOccurrences:
        # Drift in EITHER direction is a real signal: a copy deleted, or
        # a fourth added that this mutation would now leave standing.
        return (f"ERROR: old-text occurs {iFound}x, entry expects "
                f"{entry.iExpectedOccurrences}x")
    sMutated = sOriginal.replace(
        entry.old, entry.new, entry.iExpectedOccurrences,
    )
    if not _fbMutationCompiles(sMutated, pathSource):
        return "ERROR: mutation does not compile"
    if not bPreconditionKnownGood and _fiRunTest(entry.nodeid) != 0:
        return "ERROR: test does not pass on clean code"
    try:
        pathSource.write_text(sMutated, encoding="utf-8")
        iCode = _fiRunTest(entry.nodeid)
    finally:
        pathSource.write_text(sOriginal, encoding="utf-8")
    if iCode == I_EXIT_SKIPPED:
        return (
            "ERROR: the test SKIPPED, so the mutation was never "
            "evaluated (a skip is not a surviving mutant)"
        )
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
        cwd=PATH_TREE, capture_output=True, text=True,
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
        sSource: (PATH_TREE / sSource).read_text(encoding="utf-8")
        for sSource in sorted({entry.source for entry in LIST_FALSIFICATIONS})
    }


def _fnRestoreOriginals(dictOriginal):
    """Restore each source from its snapshot; git-checkout only on write failure."""
    listFailed = []
    for sSource, sBytes in dictOriginal.items():
        try:
            (PATH_TREE / sSource).write_text(sBytes, encoding="utf-8")
        except OSError:
            listFailed.append(sSource)
    if listFailed:
        subprocess.run(["git", "checkout", "--", *listFailed], cwd=PATH_TREE)


def _flistUncommittedChanges():
    """Return ``git status --porcelain`` lines for the real checkout."""
    result = subprocess.run(
        ["git", "status", "--porcelain"], cwd=REPO,
        capture_output=True, text=True, check=True,
    )
    return [sLine for sLine in result.stdout.splitlines() if sLine.strip()]


def _flistUntrackedFiles():
    """Return repo-relative paths git knows about but does not track."""
    result = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"], cwd=REPO,
        capture_output=True, text=True, check=True,
    )
    return [sLine for sLine in result.stdout.splitlines() if sLine.strip()]


def fsCreateDisposableWorktree():
    """Return the path of a fresh detached worktree checked out at HEAD."""
    sParent = tempfile.mkdtemp(prefix="vaibify-falsification-")
    sWorktree = str(pathlib.Path(sParent) / "tree")
    subprocess.run(
        ["git", "worktree", "add", "--detach", "--quiet", sWorktree,
         "HEAD"],
        cwd=REPO, check=True,
    )
    return sWorktree


def fnCopyLocalChangesIntoWorktree(sWorktree):
    """Replay tracked edits and untracked files into the worktree.

    Untracked files matter as much as the diff: a newly written test
    and its registry entry are both untracked on the run that first
    confirms them, and a worktree without them reports the entry as
    uncovered rather than killed.
    """
    sDiff = subprocess.run(
        ["git", "diff", "HEAD"], cwd=REPO,
        capture_output=True, text=True, check=True,
    ).stdout
    if sDiff.strip():
        subprocess.run(
            ["git", "apply", "-"], cwd=sWorktree,
            input=sDiff, text=True, check=True,
        )
    for sRelative in _flistUntrackedFiles():
        pathTarget = pathlib.Path(sWorktree) / sRelative
        pathTarget.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / sRelative, pathTarget)


def fnRemoveDisposableWorktree(sWorktree):
    """Remove the worktree and the temporary directory holding it."""
    subprocess.run(
        ["git", "worktree", "remove", "--force", sWorktree],
        cwd=REPO, check=False, capture_output=True,
    )
    shutil.rmtree(pathlib.Path(sWorktree).parent, ignore_errors=True)


def fsetSelectNodeIdsNeedingALiveDaemon():
    """Return the node ids pytest reports as carrying the live-daemon marker.

    Asked of pytest rather than matched against file or test names: the
    marker is what makes a test need a daemon, and a hand-kept list goes
    stale the first time a marked test is added, renamed, or moved --
    silently, and in the direction that drops entries.
    """
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q",
         "-m", S_LIVE_DAEMON_MARKER, "-p", "no:cacheprovider"],
        cwd=PATH_TREE, capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "Could not ask pytest which tests need a live Docker "
            "daemon, so the registry cannot be partitioned honestly:\n"
            + (result.stdout or "") + (result.stderr or "")
        )
    return {
        sLine.strip() for sLine in result.stdout.splitlines()
        if sLine.startswith("tests/") and "::" in sLine
    }


def _tPartitionRegistryForThisHost():
    """Split the registry into what this host can evaluate and what it cannot.

    A mutation guarded by a real-container test cannot be evaluated
    without a daemon, and the harness must not pretend otherwise in
    either direction: crediting it would be a false kill, and the
    unconditional child-side daemon requirement turns it into an ERROR
    that reads like a broken guard. Naming it as unevaluated is the only
    honest third answer -- and the entries are still evaluated on every
    lane that HAS a daemon, which is what makes the deferral safe rather
    than a hole. Setting the requirement env var refuses the deferral
    outright, so a lane that is supposed to have Docker goes red when it
    loses it instead of quietly dropping to a smaller denominator.
    """
    sOutcome = fsDaemonRequirementOutcome(
        _fbDaemonReachable(), bool(os.environ.get(S_REQUIRE_DAEMON_ENV)),
    )
    if sOutcome == S_OUTCOME_PROCEED:
        return LIST_FALSIFICATIONS, []
    if sOutcome == S_OUTCOME_FAIL:
        print(
            "Refusing to run: no Docker daemon is reachable but "
            f"{S_REQUIRE_DAEMON_ENV} is set, so this run was required "
            "to evaluate the real-container entries. Deferring them "
            "here would report a smaller denominator as success.",
            file=sys.stderr,
        )
        sys.exit(2)
    setNeedsDaemon = fsetSelectNodeIdsNeedingALiveDaemon()
    return (
        [e for e in LIST_FALSIFICATIONS if e.nodeid not in setNeedsDaemon],
        [e for e in LIST_FALSIFICATIONS if e.nodeid in setNeedsDaemon],
    )


def fnReconfirmAll():
    """Re-confirm all entries; exit nonzero on any failure or coverage gap."""
    listEvaluable, listDeferred = _tPartitionRegistryForThisHost()
    dictOriginal = _fdictCaptureOriginals()
    bBatchClean = _fbAllPreconditionsPassInOneRun(listEvaluable)
    if not bBatchClean:
        print(
            "batched precondition run failed; falling back to a "
            "per-entry check so the offender is named",
        )
    try:
        listResults = [
            (entry.nodeid, _fsReconfirmOne(
                entry, dictOriginal[entry.source],
                bPreconditionKnownGood=bBatchClean,
            ))
            for entry in listEvaluable
        ]
    finally:
        _fnRestoreOriginals(dictOriginal)
    for sNodeId, sStatus in listResults:
        print(f"{sStatus:48}  {sNodeId}")
    for entry in listDeferred:
        print(f"{'NOT EVALUATED: needs a live Docker daemon':48}  "
              f"{entry.nodeid}")
    listBad = [r for r in listResults if not r[1].startswith("KILLED")]
    listUncovered = _flistMarkedTestsWithoutEntry()
    print(f"\n{len(listResults) - len(listBad)}/{len(listResults)} "
          "kill-confirmed")
    if listDeferred:
        print(f"{len(listDeferred)} entr"
              f"{'y' if len(listDeferred) == 1 else 'ies'} NOT evaluated "
              "on this host: no Docker daemon. They are evaluated on "
              f"every lane that has one; set {S_REQUIRE_DAEMON_ENV} to "
              "refuse the deferral instead.")
    if listUncovered:
        print(f"\n{len(listUncovered)} falsification-marked test(s) with "
              "no registry entry:")
        for sNodeId in listUncovered:
            print("  " + sNodeId)
    if listBad or listUncovered:
        sys.exit(1)


def main():
    """Run the re-confirmation inside a disposable worktree."""
    global PATH_TREE
    parser = argparse.ArgumentParser(
        description=(
            "Re-confirm every falsification test still kills its "
            "recorded mutation, inside a disposable git worktree."
        ),
    )
    parser.add_argument(
        "--include-local-diff", action="store_true",
        help=(
            "Replay uncommitted tracked edits and untracked files into "
            "the worktree. Without it a dirty checkout is refused, "
            "because verifying HEAD while you hold local changes "
            "reports on code you do not have."
        ),
    )
    args = parser.parse_args()

    listDirty = _flistUncommittedChanges()
    if listDirty and not args.include_local_diff:
        print(
            "Refusing to run: the working tree has uncommitted changes "
            "and this harness checks out HEAD, so it would report on "
            "code you do not have.\nRe-run with --include-local-diff to "
            "replay them into the worktree, or commit/stash first.",
            file=sys.stderr,
        )
        for sLine in listDirty[:20]:
            print("  " + sLine, file=sys.stderr)
        sys.exit(2)

    sWorktree = fsCreateDisposableWorktree()
    try:
        if args.include_local_diff:
            fnCopyLocalChangesIntoWorktree(sWorktree)
        PATH_TREE = pathlib.Path(sWorktree)
        fnReconfirmAll()
    finally:
        PATH_TREE = REPO
        fnRemoveDisposableWorktree(sWorktree)


if __name__ == "__main__":
    main()
