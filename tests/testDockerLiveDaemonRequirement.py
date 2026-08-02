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
