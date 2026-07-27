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
