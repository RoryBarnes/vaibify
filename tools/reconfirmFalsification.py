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

A mutation guarded by a test that needs a FACILITY this host does not
have cannot be evaluated here, and there is no honest way to score it:
crediting it is a false kill, and the child-side requirement -- which
exists so a skip is never misread as a survivor -- turns it into an
ERROR indistinguishable from a broken guard. Such entries are reported
by name as NOT EVALUATED, with the facility named, and left out of the
denominator.

Two facilities are recognised, and the pattern generalises to a third
by adding one row to ``T_DEFERRABLE_FACILITIES``:

* a live Docker daemon (marker ``docker_live``), and
* a browser (marker ``browser``), which the frontend guards need --
  a JavaScript mutation is only observable to a test that loads the
  page, so leaving the frontend out of this harness would mean the
  one surface this repository has already shipped un-executed is also
  the one with no negative control.

Deferral is safe only because these entries ARE evaluated on the lanes
that have the facility. Set ``VAIBIFY_REQUIRE_DOCKER_DAEMON`` and
``VAIBIFY_REQUIRE_BROWSER`` on those lanes so losing the facility turns
them red instead of silently shrinking the denominator.

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
    python tools/reconfirmFalsification.py --only tests/testHostCancel.py

``--only`` narrows the run to entries matching a node-id or source
substring, for the per-chunk re-confirmation the house rhythm asks for
after every commit. It filters AFTER the facility partition, so a
deferred entry is still reported as unevaluated rather than run
without its facility and scored as broken; and it declines to judge
registry completeness, saying so, because a subset cannot.
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

# The same pair for the browser lane.
S_REQUIRE_BROWSER_ENV = "VAIBIFY_REQUIRE_BROWSER"
S_BROWSER_MARKER = "browser"


def _fbPlaywrightInstalled():
    """Return True when the browser lane can actually run here."""
    import importlib.util
    return importlib.util.find_spec("playwright") is not None


# One row per facility a test may need and this host may lack:
# (marker, env var demanding it, availability probe, phrase for the
# NOT EVALUATED line). Adding a facility is adding a row.
#
# The phrase is a bare noun phrase with NO article, because it is read
# in two grammatical positions -- "needs a <phrase>" and "no <phrase>"
# -- and an article baked into it makes one of them wrong. It used to
# read "no a live Docker daemon".
#
# Each probe is wrapped rather than named directly so the lookup
# happens when the partition runs, not when this module is imported --
# otherwise a test could not substitute "no daemon" or "no browser"
# and the two tiers would be unassertable.
T_DEFERRABLE_FACILITIES = (
    (
        S_LIVE_DAEMON_MARKER, S_REQUIRE_DAEMON_ENV,
        lambda: _fbDaemonReachable(), "live Docker daemon",
    ),
    (
        S_BROWSER_MARKER, S_REQUIRE_BROWSER_ENV,
        lambda: _fbPlaywrightInstalled(), "browser",
    ),
)

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
    # Same argument for the browser lane: without Playwright those
    # tests skip, a skip exits 0, and this harness would score every
    # frontend mutant as having survived.
    dictEnvironment[S_REQUIRE_BROWSER_ENV] = "1"
    sPycachePrefix = tempfile.mkdtemp()
    dictEnvironment["PYTHONPYCACHEPREFIX"] = sPycachePrefix
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", *listNodeIds, "-q",
             "-p", "no:cacheprovider", "-rs"],
            cwd=PATH_TREE, capture_output=True, text=True,
            env=dictEnvironment,
        )
    finally:
        _fnDiscardPycachePrefix(sPycachePrefix, listNodeIds)
    if result.returncode == 0 and _fbOutputReportsASkip(result.stdout):
        # Belt and braces for every OTHER reason a test can skip: an
        # unevaluated mutation must never be reported as a survivor.
        return I_EXIT_SKIPPED
    return result.returncode


def _fnDiscardPycachePrefix(sPycachePrefix, listNodeIds):
    """Remove the bytecode cache, and never fail the run over it.

    ``TemporaryDirectory`` raised ``Directory not empty`` here the
    first time the browser entries were ever re-confirmed: a test's
    uvicorn hub or its Chromium outlived the pytest process, kept
    importing, and kept writing bytecode into this tree while it was
    being removed. The entry it was judging had already passed.

    Failing a whole lane because a leftover grandchild wrote one more
    ``.pyc`` into a throwaway cache is the wrong trade. Saying nothing
    would be the other wrong trade, so the leftover is named: it is a
    process that outlived its test, which is worth knowing about even
    though it is not this tool's to fix.
    """
    try:
        shutil.rmtree(sPycachePrefix)
    except OSError as error:
        print(
            f"note: left the bytecode cache {sPycachePrefix} behind "
            f"({error}). Something these tests started was still "
            f"writing into it after pytest exited: {listNodeIds}. "
            f"What it had written: {_flistNameCacheContents(sPycachePrefix)}"
        )


def _flistNameCacheContents(sPycachePrefix):
    """Return the first few paths under the cache, for diagnosis.

    The leftover tree mirrors the source path of whatever was still
    importing, so its contents name the process that outlived its
    test. Without this the note says only that SOMETHING leaked, which
    is what the first occurrence said -- and three attempts to
    reproduce it from that description found nothing.
    """
    listFound = []
    for pathEntry in pathlib.Path(sPycachePrefix).rglob("*.pyc"):
        listFound.append(str(pathEntry.relative_to(sPycachePrefix)))
        if len(listFound) >= 5:
            break
    return listFound or ["<nothing readable>"]


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
    """Return the path of a fresh detached worktree checked out at HEAD.

    The LEAF name is unique, not just the parent directory. Git names
    its bookkeeping entry after the leaf basename and disambiguates a
    collision by appending a number -- and that disambiguation is not
    atomic across processes. Four workers each asking for a worktree
    called ``tree`` raced on ``.git/worktrees/tree2/`` and one died
    with "failed to read ... commondir: Success", after its three
    siblings had each re-confirmed their slice perfectly.

    Borrowing the parent's random suffix keeps the name unique without
    inventing a second source of randomness.
    """
    sParent = tempfile.mkdtemp(prefix="vaibify-falsification-")
    sWorktree = str(
        pathlib.Path(sParent) / ("tree-" + pathlib.Path(sParent).name[-8:])
    )
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
        try:
            shutil.copy2(REPO / sRelative, pathTarget)
        except (FileNotFoundError, NotADirectoryError):
            # The listing and the copy are two moments, and a test run
            # in this checkout creates and deletes temp directories
            # between them. A file that no longer exists cannot be part
            # of the change being confirmed, so skipping it is right --
            # and dying instead made the harness unusable whenever a
            # suite happened to be running, which is the same
            # shared-working-tree hazard the module docstring already
            # warns about, arriving from the other direction.
            continue


def fnRemoveDisposableWorktree(sWorktree):
    """Remove the worktree and the temporary directory holding it."""
    subprocess.run(
        ["git", "worktree", "remove", "--force", sWorktree],
        cwd=REPO, check=False, capture_output=True,
    )
    shutil.rmtree(pathlib.Path(sWorktree).parent, ignore_errors=True)


def fsetSelectNodeIdsCarryingMarker(sMarker):
    """Return the node ids pytest reports as carrying one marker.

    Asked of pytest rather than matched against file or test names: the
    marker is what makes a test need its facility, and a hand-kept list
    goes stale the first time a marked test is added, renamed, or moved
    -- silently, and in the direction that drops entries.
    """
    dictEnvironment = dict(os.environ)
    # Same reason as the test runs below: an editable install resolves
    # ``vaibify`` to the real checkout, and this must report on the tree
    # the mutations are applied to.
    dictEnvironment["PYTHONPATH"] = str(PATH_TREE)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q",
         "-m", sMarker, "-p", "no:cacheprovider"],
        cwd=PATH_TREE, capture_output=True, text=True,
        env=dictEnvironment,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Could not ask pytest which tests carry the {sMarker!r} "
            "marker, so the registry cannot be partitioned honestly:\n"
            + (result.stdout or "") + (result.stderr or "")
        )
    return {
        sLine.strip() for sLine in result.stdout.splitlines()
        if sLine.startswith("tests/") and "::" in sLine
    }


def _fdictSelectDeferredNodeIdsByReason():
    """Return ``{node id: phrase}`` for what this host cannot evaluate.

    A mutation guarded by a test needing a facility this host lacks
    cannot be evaluated, and the harness must not pretend otherwise in
    either direction: crediting it would be a false kill, and the
    unconditional child-side requirement turns it into an ERROR that
    reads like a broken guard. Naming it as unevaluated, with the
    facility, is the only honest third answer -- and the entries are
    still evaluated on every lane that HAS the facility, which is what
    makes the deferral safe rather than a hole. Setting a requirement
    env var refuses the deferral outright, so a lane that is supposed
    to have the facility goes red when it loses it instead of quietly
    dropping to a smaller denominator.
    """
    dictReasonByNodeId = {}
    for sMarker, sEnvVar, fbAvailable, sPhrase in T_DEFERRABLE_FACILITIES:
        sOutcome = fsDaemonRequirementOutcome(
            fbAvailable(), bool(os.environ.get(sEnvVar)),
        )
        if sOutcome == S_OUTCOME_PROCEED:
            continue
        if sOutcome == S_OUTCOME_FAIL:
            print(
                f"Refusing to run: this host has no {sPhrase} but "
                f"{sEnvVar} is set, so this run was required to "
                f"evaluate the entries guarded by {sMarker!r} tests. "
                "Deferring them here would report a smaller "
                "denominator as success.",
                file=sys.stderr,
            )
            sys.exit(2)
        for sNodeId in fsetSelectNodeIdsCarryingMarker(sMarker):
            dictReasonByNodeId.setdefault(sNodeId, sPhrase)
    return dictReasonByNodeId


def _tPartitionRegistryForThisHost():
    """Split the registry into what this host can evaluate and what it cannot.

    Returns ``(evaluable, [(entry, phrase), ...])`` so the report can
    name WHICH facility each deferred entry was waiting on; "not
    evaluated" without the reason is the shape of message a reader
    stops acting on.
    """
    dictReasonByNodeId = _fdictSelectDeferredNodeIdsByReason()
    if not dictReasonByNodeId:
        return LIST_FALSIFICATIONS, []
    return (
        [e for e in LIST_FALSIFICATIONS
         if e.nodeid not in dictReasonByNodeId],
        [(e, dictReasonByNodeId[e.nodeid]) for e in LIST_FALSIFICATIONS
         if e.nodeid in dictReasonByNodeId],
    )


S_CLASS_EXCLUSIVE = "exclusive"
S_CLASS_SHAREABLE = "shareable"

# The markers that mean "this entry holds something the MACHINE owns".
# `browser` spins a real uvicorn hub and a real Chromium; `docker_live`
# drives a real container through one daemon; `exclusive` is the file
# marker for a bound port or unix socket. Two of any of them on one
# machine contend however isolated their source trees are -- which is
# the whole reason the worker lane must never be handed one.
#
# Sharding across MACHINES does not need this: every runner has its own
# daemon, its own ports and its own browser. Workers within a machine
# do, and that is the distinction the class split exists to hold.
T_EXCLUSIVE_MARKERS = ("exclusive", "browser", "docker_live")


def _fsetSelectExclusiveNodeIds():
    """Return the node ids that must not run beside another of their kind."""
    setNodeIds = set()
    for sMarker in T_EXCLUSIVE_MARKERS:
        setNodeIds |= fsetSelectNodeIdsCarryingMarker(sMarker)
    return setNodeIds


def _tSelectContentionClass(listEvaluable, listDeferred, sClass):
    """Narrow both partitions to one contention class.

    Applied AFTER the facility partition, exactly as ``--only`` and
    ``--shard`` are: a class filter reaching the raw registry would
    hand a facility-gated entry to a host that lacks the facility and
    report the skip as a broken guard.
    """
    if not sClass:
        return listEvaluable, listDeferred
    setExclusive = _fsetSelectExclusiveNodeIds()
    bWantExclusive = sClass == S_CLASS_EXCLUSIVE
    return (
        [e for e in listEvaluable
         if (e.nodeid in setExclusive) is bWantExclusive],
        [(e, sPhrase) for e, sPhrase in listDeferred
         if (e.nodeid in setExclusive) is bWantExclusive],
    )


def _fbEntryMatchesAnyNeedle(entry, listNeedles):
    """Return True when a --only needle names this entry."""
    return any(
        sNeedle in entry.nodeid or sNeedle in entry.source
        for sNeedle in listNeedles
    )


def _tSelectRequestedEntries(listEvaluable, listDeferred, listNeedles):
    """Narrow both partitions to the entries ``--only`` asked for.

    Applied AFTER the facility partition, never instead of it. A
    selector that filtered the raw registry would hand a
    ``docker_live`` entry to a daemon-less run, whose child sets
    ``VAIBIFY_REQUIRE_DOCKER_DAEMON`` and turns the skip into a
    failure — reported as "does not pass on clean code", which is a
    deferral wearing a defect's clothes. That is the exact confusion
    the NOT-EVALUATED tier exists to prevent, and it is easy to
    reintroduce by filtering one line too early.
    """
    if not listNeedles:
        return listEvaluable, listDeferred
    return (
        [e for e in listEvaluable
         if _fbEntryMatchesAnyNeedle(e, listNeedles)],
        [(e, sPhrase) for e, sPhrase in listDeferred
         if _fbEntryMatchesAnyNeedle(e, listNeedles)],
    )


def _tSelectShard(listEvaluable, listDeferred, tShard):
    """Narrow both partitions to one shard of a ``I/N`` split.

    Applied AFTER the facility partition, for the same reason
    ``--only`` is: a selector reaching the raw registry hands a
    facility-gated entry to a host that lacks the facility, and its
    skip is then reported as a broken guard.

    The split is by STRIDE, not by block. Entries sit in the registry
    grouped by the feature they defend, so consecutive ones cost
    similar amounts and a block split would hand one shard the browser
    entries and another the cheap unit ones. Striding interleaves
    them, which is the closest thing to balance available without
    timing every entry.

    Both lists are split, so the union of all N shards is exactly the
    registry — the deferred entries included, since each shard reports
    its own slice of them and the summary job adds the slices up.
    """
    if tShard is None:
        return listEvaluable, listDeferred
    iShard, iShards = tShard
    return (
        listEvaluable[iShard - 1::iShards],
        listDeferred[iShard - 1::iShards],
    )


def fnReconfirmAll(
    listOnly=(), tShard=None, sSummaryPath="", sClass="",
):
    """Re-confirm entries; exit nonzero on any failure or coverage gap.

    ``listOnly`` narrows the run to entries whose node id or source
    file contains one of the given substrings. It exists because the
    house rhythm re-confirms a chunk's own entries after every commit
    and the full registry takes far longer than a chunk does — but a
    narrowed run is NOT the standing negative control, so it declines
    to judge registry completeness and says so rather than reporting a
    clean coverage check it did not perform.

    ``tShard`` is ``(I, N)`` and splits the registry across N machines
    that each run one slice. A shard makes a WEAKER claim than a
    narrowed run makes, not a stronger one: it judges only its slice,
    and the standing negative control is the union of all N shards,
    which only the summary job can see. So a shard declines the
    completeness check too, and says which claim belongs where.
    """
    listEvaluable, listDeferred = _tSelectShard(
        *_tSelectContentionClass(
            *_tSelectRequestedEntries(
                *_tPartitionRegistryForThisHost(),
                listNeedles=list(listOnly),
            ),
            sClass=sClass,
        ),
        tShard=tShard,
    )
    if listOnly and not listEvaluable and not listDeferred:
        print(f"No registry entry matches {list(listOnly)}")
        sys.exit(2)
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
    for entry, sPhrase in listDeferred:
        print(f"{'NOT EVALUATED: needs a ' + sPhrase:48}  "
              f"{entry.nodeid}")
    listBad = [r for r in listResults if not r[1].startswith("KILLED")]
    # A narrowed run cannot speak to registry COMPLETENESS: almost every
    # marked test is outside the selection by construction, so running
    # the check would report a wall of phantom gaps. Announced rather
    # than silently skipped -- a check that can be skipped must say
    # what the skip reported.
    listUncovered = (
        [] if listOnly or tShard or sClass
        else _flistMarkedTestsWithoutEntry()
    )
    print(f"\n{len(listResults) - len(listBad)}/{len(listResults)} "
          "kill-confirmed")
    if tShard:
        print(
            f"SHARD {tShard[0]} of {tShard[1]}: this leg judged its own "
            "slice and nothing else. The standing negative control is "
            "the UNION of all shards, which only the summary job can "
            "see -- it is where registry completeness is checked and "
            "where a missing shard is caught."
        )
    if listOnly:
        print(
            f"NARROWED run (--only {' '.join(listOnly)}): this is not "
            "the standing negative control. Registry completeness was "
            "NOT checked, and every entry outside the selection was "
            "neither run nor judged."
        )
    if listDeferred:
        setPhrases = sorted({sPhrase for _, sPhrase in listDeferred})
        print(f"{len(listDeferred)} entr"
              f"{'y' if len(listDeferred) == 1 else 'ies'} NOT evaluated "
              f"on this host: no {', no '.join(setPhrases)}. They are "
              "evaluated on every lane that has the facility; set the "
              "matching requirement variable "
              f"({S_REQUIRE_DAEMON_ENV}, {S_REQUIRE_BROWSER_ENV}) to "
              "refuse the deferral instead.")
    if listUncovered:
        print(f"\n{len(listUncovered)} falsification-marked test(s) with "
              "no registry entry:")
        for sNodeId in listUncovered:
            print("  " + sNodeId)
    if sSummaryPath:
        _fnWriteShardSummary(
            sSummaryPath, tShard, listResults, listBad, listDeferred,
        )
    if listBad or listUncovered:
        sys.exit(1)


def _fnWriteShardSummary(
    sSummaryPath, tShard, listResults, listBad, listDeferred,
):
    """Write this shard's counts for the summary job to add up.

    The node ids of the FAILURES travel, so the summary can name them
    without a reader opening thirty-two job logs to find which shard
    holds the survivor. The passes travel only as a count: they are
    the bulk, and nothing downstream needs their names.
    """
    import json
    dictSummary = {
        "iShard": tShard[0] if tShard else 1,
        "iShards": tShard[1] if tShard else 1,
        "iRan": len(listResults),
        "iKilled": len(listResults) - len(listBad),
        "iDeferred": len(listDeferred),
        "listSurvivors": [
            {"sNodeId": sNodeId, "sStatus": sStatus}
            for sNodeId, sStatus in listBad
        ],
    }
    pathlib.Path(sSummaryPath).parent.mkdir(
        parents=True, exist_ok=True,
    )
    pathlib.Path(sSummaryPath).write_text(
        json.dumps(dictSummary, indent=2) + "\n", encoding="utf-8",
    )


def _fiReportRegistryCompleteness():
    """Print the coverage verdict for the whole registry; return an exit code.

    No worktree and no mutation: this reads which tests carry the
    falsification mark and which node ids the registry names, so it is
    a collection-level question that a sharded run can answer once
    rather than N times over slices that each see almost none of it.
    """
    listUncovered = _flistMarkedTestsWithoutEntry()
    if not listUncovered:
        print(
            f"registry completeness: every falsification-marked test "
            f"of {len(LIST_FALSIFICATIONS)} entries has an entry"
        )
        return 0
    print(f"{len(listUncovered)} falsification-marked test(s) with no "
          "registry entry:")
    for sNodeId in listUncovered:
        print("  " + sNodeId)
    return 1


def _tSubShardForWorker(tShard, iWorker, iWorkers):
    """Return the ``(I, N)`` a worker runs, given its parent's shard.

    A worker takes a slice of a slice, and the arithmetic has to be a
    partition or the whole scheme silently drops entries. Shard ``i``
    of ``N`` is every index congruent to ``i-1`` modulo ``N``; the
    workers split it into ``i + N*(w-1)`` of ``N*W``, whose union is
    exactly the parent's residue class again, because
    ``(i-1 + N*(w-1)) mod N == i-1`` for every ``w``.
    """
    iShard, iShards = tShard or (1, 1)
    return (iShard + iShards * (iWorker - 1), iShards * iWorkers)


def _fiRunWorkersAndSummarize(args, tShard):
    """Run W child harnesses concurrently, one worktree each; return an exit code.

    Child PROCESSES rather than threads, because the harness tracks the
    tree it is mutating in a module global and two threads would share
    it -- which is the shape of the bug this repository already has a
    Lessons entry about, one working tree and two writers.

    Each child is an ordinary invocation of this tool, so the isolation
    it gets is the isolation that has been exercised all along: its own
    disposable worktree, checked out from HEAD.
    """
    import concurrent.futures
    import tempfile
    with tempfile.TemporaryDirectory() as sSummaryDirectory:
        listCommands = [
            (iWorker, _flistBuildWorkerCommand(
                args, _tSubShardForWorker(
                    tShard, iWorker, args.iWorkers,
                ),
                os.path.join(
                    sSummaryDirectory, f"worker{iWorker}.json",
                ),
            ))
            for iWorker in range(1, args.iWorkers + 1)
        ]
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=args.iWorkers,
        ) as executor:
            listResults = list(executor.map(
                lambda t: (t[0], _tRunOneWorker(*t)), listCommands,
            ))
        return _fiReportWorkerResults(
            listResults, sSummaryDirectory, args, tShard,
        )


def _flistBuildWorkerCommand(args, tSubShard, sSummaryPath):
    """Return the argv for one worker's child harness."""
    listCommand = [
        sys.executable, os.path.abspath(__file__),
        "--shard", f"{tSubShard[0]}/{tSubShard[1]}",
        "--summary-json", sSummaryPath,
    ]
    if args.sClass:
        listCommand += ["--class", args.sClass]
    if args.include_local_diff:
        listCommand.append("--include-local-diff")
    return listCommand


def _tRunOneWorker(iWorker, listCommand):
    """Run one worker to completion; return ``(returncode, output)``."""
    del iWorker
    processWorker = subprocess.run(
        listCommand, cwd=REPO, capture_output=True, text=True,
    )
    return (processWorker.returncode, processWorker.stdout
            + processWorker.stderr)


def _fiReportWorkerResults(listResults, sSummaryDirectory, args, tShard):
    """Print every worker's output in order, then the combined verdict."""
    import json
    iRan = iKilled = iDeferred = 0
    for iWorker, (iCode, sOutput) in listResults:
        print(f"----- worker {iWorker} (exit {iCode}) -----")
        print(sOutput.rstrip())
        pathSummary = pathlib.Path(
            sSummaryDirectory, f"worker{iWorker}.json",
        )
        if not pathSummary.exists():
            continue
        dictSummary = json.loads(pathSummary.read_text(encoding="utf-8"))
        iRan += dictSummary["iRan"]
        iKilled += dictSummary["iKilled"]
        iDeferred += dictSummary["iDeferred"]
    listFailed = [i for i, (iCode, _s) in listResults if iCode != 0]
    print(f"\n{iKilled}/{iRan} kill-confirmed across "
          f"{len(listResults)} workers"
          + (f", {iDeferred} not evaluated" if iDeferred else ""))
    if listFailed:
        print(f"worker(s) {listFailed} exited nonzero; their output is "
              "above, and the entries they name are the ones to read")
    if args.sSummaryPath:
        pathlib.Path(args.sSummaryPath).parent.mkdir(
            parents=True, exist_ok=True,
        )
        pathlib.Path(args.sSummaryPath).write_text(json.dumps({
            "iShard": tShard[0] if tShard else 1,
            "iShards": tShard[1] if tShard else 1,
            "iRan": iRan, "iKilled": iKilled, "iDeferred": iDeferred,
            "listSurvivors": _flistCollectWorkerSurvivors(
                sSummaryDirectory,
            ),
        }, indent=2) + "\n", encoding="utf-8")
    return 1 if listFailed else 0


def _flistCollectWorkerSurvivors(sSummaryDirectory):
    """Return every survivor any worker reported, for the parent summary."""
    import json
    listSurvivors = []
    for pathSummary in sorted(
        pathlib.Path(sSummaryDirectory).glob("worker*.json"),
    ):
        dictSummary = json.loads(pathSummary.read_text(encoding="utf-8"))
        listSurvivors.extend(dictSummary.get("listSurvivors", []))
    return listSurvivors


def _tParseShardArgument(sShard):
    """Return ``(I, N)`` from an ``I/N`` argument, or None when absent.

    Refuses anything it cannot read rather than guessing, because
    every wrong reading here is silent: a shard index past the count
    runs NO entries and reports "0/0 kill-confirmed", which is a green
    lane that checked nothing.
    """
    if not sShard:
        return None
    try:
        iShard, iShards = (int(sPart) for sPart in sShard.split("/", 1))
    except ValueError:
        raise SystemExit(
            f"--shard wants I/N with two integers, not {sShard!r}"
        )
    if iShards < 1 or not 1 <= iShard <= iShards:
        raise SystemExit(
            f"--shard {sShard} is out of range: I must be between 1 "
            f"and N, and N at least 1"
        )
    return (iShard, iShards)


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
    parser.add_argument(
        "--only", dest="listOnly", action="append", default=[],
        metavar="SUBSTRING",
        help=(
            "Re-confirm only the entries whose pytest node id or source "
            "file contains SUBSTRING (repeatable). For checking a "
            "chunk's own entries after a commit; the result is NOT the "
            "standing negative control, and the run says so."
        ),
    )
    parser.add_argument(
        "--shard", dest="sShard", default="", metavar="I/N",
        help=(
            "Re-confirm only shard I of an N-way split, so N machines "
            "can cover the registry between them. The union of the "
            "shards is the standing negative control; a single shard "
            "is not, and says so."
        ),
    )
    parser.add_argument(
        "--class", dest="sClass", default="",
        choices=[S_CLASS_EXCLUSIVE, S_CLASS_SHAREABLE],
        help=(
            "Run only one contention class. `exclusive` entries hold "
            "something the machine owns -- a port, a socket, the "
            "Docker daemon, a browser -- so they get a lane to "
            "themselves; `shareable` entries hold nothing and may run "
            "under workers."
        ),
    )
    parser.add_argument(
        "--workers", dest="iWorkers", type=int, default=1,
        metavar="W",
        help=(
            "Run W child harnesses concurrently, each in its own "
            "disposable worktree. Only safe for the `shareable` class, "
            "and refused without it."
        ),
    )
    parser.add_argument(
        "--completeness-only", action="store_true",
        help=(
            "Run ONLY the whole-registry coverage check -- every "
            "falsification-marked test has an entry -- and skip every "
            "replay. For the summary job over a sharded run, where no "
            "single shard can make that claim."
        ),
    )
    parser.add_argument(
        "--summary-json", dest="sSummaryPath", default="",
        metavar="PATH",
        help=(
            "Write this run's counts and any survivors to PATH as "
            "JSON, for a summary job to add up across shards."
        ),
    )
    args = parser.parse_args()
    tShard = _tParseShardArgument(args.sShard)
    if args.iWorkers > 1 and args.sClass != S_CLASS_SHAREABLE:
        parser.error(
            "--workers is only safe for --class shareable. The "
            "exclusive entries hold a port, a socket, the Docker "
            "daemon or a browser, and two of those on one machine "
            "contend however isolated their source trees are."
        )
    if args.iWorkers > 1 and args.listOnly:
        parser.error(
            "--workers and --only do not compose: a narrowed run is "
            "already short, and slicing it further produces workers "
            "with nothing to do."
        )
    if tShard and args.listOnly:
        parser.error(
            "--shard and --only answer different questions: one splits "
            "the whole registry across machines, the other narrows it "
            "to a chunk you are working on. Combining them would "
            "produce a slice of a subset that nothing can reason about."
        )

    if args.completeness_only:
        sys.exit(_fiReportRegistryCompleteness())

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

    if args.iWorkers > 1:
        sys.exit(_fiRunWorkersAndSummarize(args, tShard))

    sWorktree = fsCreateDisposableWorktree()
    try:
        if args.include_local_diff:
            fnCopyLocalChangesIntoWorktree(sWorktree)
        PATH_TREE = pathlib.Path(sWorktree)
        fnReconfirmAll(
            args.listOnly, tShard, args.sSummaryPath, args.sClass,
        )
    finally:
        PATH_TREE = REPO
        fnRemoveDisposableWorktree(sWorktree)


if __name__ == "__main__":
    main()
