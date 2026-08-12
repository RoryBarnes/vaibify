"""The live-daemon smoke must not be able to report a false green.

The CI job that runs ``pytest -m docker_live`` exists to exercise the
real Docker transport -- the one surface the 20-odd hand-rolled mocks
in this suite cannot speak for. It used to be a step guarded by
``docker info || exit 0``: with no daemon reachable it ran nothing and
reported SUCCESS.

Deleting that shell guard alone would not have been enough, because
the tests skip themselves when no daemon answers, which is the right
behaviour for a developer laptop and the wrong behaviour for a job
whose entire purpose is live coverage. So the contract has two halves,
and both are pinned here:

1. With no daemon and no demand, skip (developers are not blocked).
2. With no daemon and ``VAIBIFY_REQUIRE_DOCKER_DAEMON`` set, fail.

The third test forbids the shell guard from coming back.
"""

from pathlib import Path

import pytest

from tests import testDockerConnectionLive as moduleLive
from tests.falsificationRegistry import Falsification


_PATH_WORKFLOWS = (
    Path(__file__).resolve().parent.parent / ".github" / "workflows"
)


@pytest.mark.falsification
def test_demanded_but_unreachable_daemon_resolves_to_failure():
    """Kills: the branch that stops CI going green without a daemon.

    Mutation: make ``fsDaemonRequirementOutcome`` return the skip
    outcome when the daemon is demanded and unreachable.
    """
    assert moduleLive.fsDaemonRequirementOutcome(
        bReachable=False, bDemanded=True,
    ) == moduleLive.S_OUTCOME_FAIL


def test_require_env_turns_the_daemon_skip_into_a_failure(monkeypatch):
    """The decision must reach pytest as an actual failure."""
    monkeypatch.setattr(moduleLive, "_fbDaemonReachable", lambda: False)
    monkeypatch.setenv(moduleLive.S_REQUIRE_DAEMON_ENV, "1")
    with pytest.raises(pytest.fail.Exception):
        moduleLive.fnRequireDaemonReachable()


def test_absent_daemon_still_skips_when_not_demanded(monkeypatch):
    """A developer without Docker must not have a red suite."""
    monkeypatch.setattr(moduleLive, "_fbDaemonReachable", lambda: False)
    monkeypatch.delenv(moduleLive.S_REQUIRE_DAEMON_ENV, raising=False)
    with pytest.raises(pytest.skip.Exception):
        moduleLive.fnRequireDaemonReachable()


def test_reachable_daemon_neither_skips_nor_fails(monkeypatch):
    """The happy path must fall through to the real assertions."""
    monkeypatch.setattr(moduleLive, "_fbDaemonReachable", lambda: True)
    monkeypatch.setenv(moduleLive.S_REQUIRE_DAEMON_ENV, "1")
    assert moduleLive.fnRequireDaemonReachable() is None


@pytest.mark.falsification
def test_no_workflow_swallows_an_unreachable_docker_daemon():
    """Kills: reintroducing the ``docker info || exit 0`` false green.

    Mutation: put the swallowing guard back into a workflow. Any shell
    line that probes the daemon and then exits ZERO on failure makes a
    job advertised as live-Docker coverage pass for having run
    nothing.
    """
    listOffenders = []
    for pathWorkflow in sorted(_PATH_WORKFLOWS.glob("*.yml")):
        for iNumber, sLine in enumerate(
            pathWorkflow.read_text().splitlines(), start=1,
        ):
            sStripped = sLine.strip()
            if sStripped.startswith("#"):
                continue
            if "docker info" not in sStripped:
                continue
            if "exit 0" in sStripped or "|| true" in sStripped:
                listOffenders.append(
                    f"{pathWorkflow.name}:{iNumber}: {sLine.strip()}"
                )
    assert not listOffenders, (
        "A workflow probes the Docker daemon and then exits zero when "
        "it is unreachable, so the job goes green having run nothing:\n"
        + "\n".join(listOffenders)
        + "\nUse the docker-smoke job's retry-then-fail step instead."
    )


# ---------------------------------------------------------------------
# The same defect, one layer up: the falsification re-confirmation.
# ---------------------------------------------------------------------

def _fmoduleReconfirmationHarness():
    """Import tools/reconfirmFalsification.py by path."""
    import importlib.util

    pathTool = (
        Path(__file__).resolve().parent.parent
        / "tools" / "reconfirmFalsification.py"
    )
    spec = importlib.util.spec_from_file_location(
        "reconfirmFalsificationUnderTest", pathTool,
    )
    moduleTool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(moduleTool)
    return moduleTool


def test_the_reconfirmation_harness_never_reads_a_skip_as_a_survivor():
    """A skipped test exits 0; that must not mean "the mutant lived".

    pytest gives a skipped test the same exit code as a passing one, and
    this harness reads exit 0 under a mutation as SURVIVED. So on a
    machine with no Docker daemon, five Docker-live entries reported as
    surviving mutants -- an alarm about guards that were, in fact,
    hand-confirmed against a live daemon. A false alarm in a
    verification tool is not harmless: it is what teaches a reader to
    discount the tool.
    """
    moduleTool = _fmoduleReconfirmationHarness()
    assert moduleTool._fbOutputReportsASkip("1 skipped in 0.10s")
    assert moduleTool._fbOutputReportsASkip("3 passed, 1 skipped in 1.0s")
    assert not moduleTool._fbOutputReportsASkip("4 passed in 1.0s")
    assert not moduleTool._fbOutputReportsASkip(
        "4 passed in 1.0s (skipped nothing)"
    )
    assert moduleTool.I_EXIT_SKIPPED not in (0, 1), (
        "the skip code must be distinguishable from both a pass and an "
        "assertion failure, or the harness cannot tell them apart"
    )


def test_the_reconfirmation_harness_demands_a_daemon_for_its_runs():
    """It sets the same env var the CI jobs do, for the same reason.

    Detecting the skip after the fact is the backstop. Demanding the
    daemon is the primary: with it set, a Docker-live falsification test
    FAILS on a machine with no daemon, and the harness reports an error
    the operator can act on instead of a phantom survivor.
    """
    moduleTool = _fmoduleReconfirmationHarness()
    assert moduleTool.S_REQUIRE_DAEMON_ENV == (
        moduleLive.S_REQUIRE_DAEMON_ENV
    ), "the harness and the tests must name the same environment switch"
    sSource = (
        Path(__file__).resolve().parent.parent
        / "tools" / "reconfirmFalsification.py"
    ).read_text()
    assert 'dictEnvironment[S_REQUIRE_DAEMON_ENV] = "1"' in sSource, (
        "the harness must demand a live daemon for the runs it judges"
    )


# ---------------------------------------------------------------------
# Demanding the daemon has a cost: on a host that cannot have one, every
# real-container entry becomes an ERROR that reads exactly like a broken
# guard. The macOS falsification legs had timed out for weeks, so the
# first one to finish reported seven of them at once. The third answer
# -- name them as unevaluated, and refuse that deferral wherever a
# daemon is supposed to exist -- is what these pin.
# ---------------------------------------------------------------------

def testADriftedOccurrenceCountErrorsRatherThanUnderMutating():
    """A guard that exists twice must not be half-disabled and scored.

    Disabling one copy of a defence-in-depth pair changes nothing a
    caller can observe, so the entry reports SURVIVED and reads as an
    undefended guard. Three transfer entries did exactly that. Stating
    the count is what makes a copy appearing or vanishing loud.
    """
    moduleTool = _fmoduleReconfirmationHarness()
    entry = Falsification(
        nodeid="tests/testAny.py::testAny", source="anySource.py",
        old="    if bGuard:", new="    if False:",
        iExpectedOccurrences=2,
    )
    sStatus = moduleTool._fsReconfirmOne(
        entry, "    if bGuard:\n", bPreconditionKnownGood=True,
    )
    assert sStatus.startswith("ERROR"), (
        "one copy where the entry expects two must not be mutated and "
        "scored as though the guard were gone"
    )
    assert "1x" in sStatus and "2x" in sStatus, (
        "the error must name both counts, or it cannot be acted on"
    )


def testNarrowingTheRunNeverPromotesADeferredEntryIntoTheJudgedSet(
    monkeypatch,
):
    """``--only`` filters AFTER the partition, not instead of it.

    The tempting implementation narrows the raw registry and then
    partitions -- or, worse, narrows and skips the partition entirely,
    which is what a hand-rolled subset driver did. A ``docker_live``
    entry then runs on a daemon-less host, its child sets the
    requirement variable, its skip becomes a failure, and the report
    says "does not pass on clean code". That is a deferral wearing a
    defect's clothes: it names a guard the reader will go looking for
    and will not find, which is precisely the false alarm the
    NOT-EVALUATED tier was built to stop.

    So the property is compositional and worth pinning on its own: no
    selection may move an entry from the deferred list into the judged
    list.
    """
    moduleTool = _fmoduleReconfirmationHarness()
    monkeypatch.setattr(moduleTool, "_fbDaemonReachable", lambda: False)
    monkeypatch.setattr(
        moduleTool, "_fbPlaywrightInstalled", lambda: True,
    )
    monkeypatch.delenv(moduleTool.S_REQUIRE_DAEMON_ENV, raising=False)

    listEvaluable, listDeferred = moduleTool._tPartitionRegistryForThisHost()
    assert listDeferred, "no deferred entry, so this proves nothing"
    setDeferredIds = {entry.nodeid for entry, _sPhrase in listDeferred}

    # Select by the source file of a DEFERRED entry, which is the
    # realistic way to catch one: a regression sweep names the files a
    # session touched, not the tests.
    entryDeferred = listDeferred[0][0]
    listSelectedEvaluable, listSelectedDeferred = (
        moduleTool._tSelectRequestedEntries(
            listEvaluable, listDeferred, [entryDeferred.source],
        )
    )
    assert not any(
        entry.nodeid in setDeferredIds for entry in listSelectedEvaluable
    ), (
        "narrowing promoted a facility-gated entry into the judged "
        "set, where it will be run without its facility and reported "
        "as a broken guard"
    )
    assert any(
        entry.nodeid == entryDeferred.nodeid
        for entry, _sPhrase in listSelectedDeferred
    ), "the selected deferred entry vanished instead of being reported"


def testANarrowedRunDoesNotClaimToHaveCheckedRegistryCompleteness():
    """A subset cannot judge coverage, and must not imply it did.

    Almost every falsification-marked test is outside any narrow
    selection, so running the completeness check would print a wall of
    phantom gaps -- and NOT running it silently would report a clean
    bill the run never earned. The third answer is to skip it and say
    so, which is the same rule this file's daemon tier follows.
    """
    from pathlib import Path as PathAlias
    sSource = (
        PathAlias(__file__).resolve().parent.parent
        / "tools" / "reconfirmFalsification.py"
    ).read_text()
    # The condition grew a second term on 2026-08-12, when the harness
    # learned to run one SHARD of the registry. A shard is the same
    # kind of partial run for this purpose -- it sees a slice, so it
    # cannot speak for coverage -- and the summary job over the union
    # owns that check instead.
    # Pinned on the EXPRESSION rather than the whole statement: the
    # statement wraps once the condition has several terms, and a pin
    # that breaks on reflowing teaches people to edit the pin. A
    # CLASS run is partial in the same way a shard is.
    assert (
        "[] if listOnly or tShard or sClass"
    ) in sSource, (
        "a narrowed run must not run the completeness check over the "
        "whole registry"
    )
    assert "NARROWED run" in sSource, (
        "a narrowed run must announce that it is not the standing "
        "negative control"
    )


def testEntriesNeedingADaemonAreDeferredNotErroredWhenNoneExists(monkeypatch):
    """No daemon and no demand: partition, do not pretend to judge.

    The browser facility is forced AVAILABLE here so this asserts
    about the daemon tier alone; the deferral machinery is shared, and
    a test that let both tiers fire at once could not tell which one
    had put an entry in the deferred list.
    """
    moduleTool = _fmoduleReconfirmationHarness()
    monkeypatch.setattr(moduleTool, "_fbDaemonReachable", lambda: False)
    monkeypatch.setattr(
        moduleTool, "_fbPlaywrightInstalled", lambda: True,
    )
    monkeypatch.delenv(moduleTool.S_REQUIRE_DAEMON_ENV, raising=False)

    listEvaluable, listDeferred = moduleTool._tPartitionRegistryForThisHost()

    setMarked = moduleTool.fsetSelectNodeIdsCarryingMarker(
        moduleTool.S_LIVE_DAEMON_MARKER,
    )
    assert listDeferred, (
        "the registry has real-container entries, so a host with no "
        "daemon must defer some of them"
    )
    assert all(
        entry.nodeid in setMarked for entry, _sPhrase in listDeferred
    ), "an entry was deferred without carrying the live-daemon marker"
    assert not any(entry.nodeid in setMarked for entry in listEvaluable), (
        "a marked entry stayed in the evaluable set, so it will be "
        "judged by a run that cannot execute it"
    )
    assert len(listEvaluable) + len(listDeferred) == len(
        moduleTool.LIST_FALSIFICATIONS
    ), "the partition dropped or duplicated an entry"


def testFrontendEntriesAreDeferredWhenNoBrowserIsInstalled(monkeypatch):
    """A JavaScript mutant is only observable to a test that loads a page.

    Without this tier the frontend entries would be run anyway,
    Playwright would be missing, the browser lane would SKIP -- and a
    skip exits 0, which this harness reads as a surviving mutant. The
    one surface this repository has already shipped un-executed would
    then also be the one reporting phantom survivors.
    """
    moduleTool = _fmoduleReconfirmationHarness()
    monkeypatch.setattr(moduleTool, "_fbDaemonReachable", lambda: True)
    monkeypatch.setattr(
        moduleTool, "_fbPlaywrightInstalled", lambda: False,
    )
    monkeypatch.delenv(moduleTool.S_REQUIRE_BROWSER_ENV, raising=False)

    listEvaluable, listDeferred = moduleTool._tPartitionRegistryForThisHost()

    setMarked = moduleTool.fsetSelectNodeIdsCarryingMarker(
        moduleTool.S_BROWSER_MARKER,
    )
    assert listDeferred, (
        "the registry has frontend entries, so a host with no browser "
        "must defer some of them"
    )
    assert all(
        entry.nodeid in setMarked for entry, _sPhrase in listDeferred
    ), "an entry was deferred without carrying the browser marker"
    assert all(
        sPhrase == "browser" for _entry, sPhrase in listDeferred
    ), "the deferral did not name the facility it was waiting on"
    assert len(listEvaluable) + len(listDeferred) == len(
        moduleTool.LIST_FALSIFICATIONS
    ), "the partition dropped or duplicated an entry"


def testDemandingABrowserRefusesRatherThanQuietlyDeferring(monkeypatch):
    """The browser tier refuses the deferral exactly as the daemon does."""
    moduleTool = _fmoduleReconfirmationHarness()
    monkeypatch.setattr(moduleTool, "_fbDaemonReachable", lambda: True)
    monkeypatch.setattr(
        moduleTool, "_fbPlaywrightInstalled", lambda: False,
    )
    monkeypatch.setenv(moduleTool.S_REQUIRE_BROWSER_ENV, "1")

    with pytest.raises(SystemExit) as excinfo:
        moduleTool._tPartitionRegistryForThisHost()
    assert excinfo.value.code != 0


@pytest.mark.falsification
def testDemandingADaemonRefusesRatherThanQuietlyDeferring(monkeypatch):
    """Kills: deferring on a lane that is supposed to have Docker.

    Mutation: let ``_tPartitionRegistryForThisHost`` fall through to the
    partition when the daemon is demanded but unreachable. The lane
    would then report every remaining entry killed and exit zero, having
    silently stopped checking the real-container guards -- the same
    false green as ``docker info || exit 0``, one layer up.
    """
    moduleTool = _fmoduleReconfirmationHarness()
    monkeypatch.setattr(moduleTool, "_fbDaemonReachable", lambda: False)
    monkeypatch.setenv(moduleTool.S_REQUIRE_DAEMON_ENV, "1")

    with pytest.raises(SystemExit) as excinfo:
        moduleTool._tPartitionRegistryForThisHost()
    assert excinfo.value.code != 0


@pytest.mark.falsification
def testDeferredEntriesAreNamedAndLeftOutOfTheDenominator(monkeypatch, capsys):
    """Kills: dropping deferred entries from the report entirely.

    Mutation: stop printing the deferred entries. The score line then
    reads as a clean sweep of a denominator nobody was told had shrunk,
    which is precisely the reading that makes a smaller number look like
    success.
    """
    moduleTool = _fmoduleReconfirmationHarness()
    entryJudged = Falsification(
        nodeid="tests/testJudged.py::testJudged",
        source="sourceUnderTest", old="alpha", new="beta",
    )
    entryDeferred = Falsification(
        nodeid="tests/testLive.py::testNeedsARealContainer",
        source="sourceUnderTest", old="alpha", new="beta",
    )
    monkeypatch.setattr(
        moduleTool, "_tPartitionRegistryForThisHost",
        lambda: ([entryJudged], [(entryDeferred, "live Docker daemon")]),
    )
    monkeypatch.setattr(
        moduleTool, "_fdictCaptureOriginals", lambda: {"sourceUnderTest": "alpha"},
    )
    monkeypatch.setattr(moduleTool, "_fnRestoreOriginals", lambda dictAny: None)
    monkeypatch.setattr(
        moduleTool, "_fbAllPreconditionsPassInOneRun", lambda listAny: True,
    )
    monkeypatch.setattr(
        moduleTool, "_fsReconfirmOne", lambda *args, **kwargs: "KILLED",
    )
    monkeypatch.setattr(moduleTool, "_flistMarkedTestsWithoutEntry", list)

    moduleTool.fnReconfirmAll()

    sOutput = capsys.readouterr().out
    assert entryDeferred.nodeid in sOutput, (
        "a deferred entry vanished from the report, so a reader cannot "
        "tell the denominator shrank"
    )
    assert "NOT EVALUATED" in sOutput
    assert "1/1 kill-confirmed" in sOutput, (
        "an entry this host could not run must not be counted as judged"
    )
