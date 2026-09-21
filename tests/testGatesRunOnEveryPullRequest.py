"""A gate that does not run is worse than a gate that fails.

Both halves of this file come from the same afternoon.

Every gating workflow was declared ``pull_request: branches: [main]``.
A pull request based on ANY other branch -- a stacked PR, which is how
a large feature gets reviewed in pieces -- therefore triggered none of
them, and the PR page said "no checks reported". On a glance that is
almost indistinguishable from "all checks passed", and it hid a whole
feature branch, including the lane written to prove that feature, from
CI entirely.

And the browser install was the one unbounded step in front of bounded
tests. It sat for one hour forty-four minutes on a runner whose package
fetch never returned, against a lane that normally finishes in four --
reporting nothing while looking busy, and set to keep doing so until
GitHub's six-hour ceiling.

The shared shape is a check that cannot say what it did not do. This
repository already has that lesson recorded about ``docker info ||
exit 0``; these are two more instances of it.
"""

import pathlib

import pytest
import yaml

from tests.testWorkflowMergeGateSplit import T_PRE_MERGE_WORKFLOWS

PATH_WORKFLOWS = (
    pathlib.Path(__file__).resolve().parent.parent
    / ".github" / "workflows"
)

# Steps that fetch from the network before any test runs. Each one is
# an opportunity for a job to hang where nothing is watching.
T_UNBOUNDED_HAZARD_MARKERS = (
    "playwright install",
    "apt-get install",
)


def _fdictLoadWorkflow(sName):
    return yaml.safe_load(
        (PATH_WORKFLOWS / sName).read_text(encoding="utf-8"),
    )


def _fdictTriggers(dictWorkflow):
    """Return the workflow's trigger block under either YAML spelling.

    ``on`` is parsed as the boolean True by YAML 1.1, which is why this
    exists rather than a plain lookup.
    """
    return dictWorkflow.get(True) or dictWorkflow.get("on") or {}


@pytest.mark.parametrize("sName", T_PRE_MERGE_WORKFLOWS)
def test_a_gate_runs_on_a_pull_request_whatever_its_base(sName):
    """No base filter, so a stacked PR cannot run nothing at all."""
    dictTriggers = _fdictTriggers(_fdictLoadWorkflow(sName))
    assert "pull_request" in dictTriggers, (
        f"{sName} is a merge gate that no pull request triggers"
    )
    dictPullRequest = dictTriggers["pull_request"] or {}
    assert not dictPullRequest.get("branches"), (
        f"{sName} only gates pull requests into "
        f"{dictPullRequest.get('branches')}. A PR based on anything "
        "else runs it not at all and reports 'no checks', which reads "
        "like success. Remove the filter or stop calling it a gate."
    )


@pytest.mark.parametrize("sName", T_PRE_MERGE_WORKFLOWS)
def test_every_network_setup_step_in_a_gate_is_bounded(sName):
    """An unbounded setup step can hang forever in front of a test.

    The tests below it being bounded proves nothing: a job that never
    reaches them is exactly as silent as one that never started, and
    stays that way for hours.
    """
    dictWorkflow = _fdictLoadWorkflow(sName)
    listOffenders = []
    for sJobName, dictJob in (dictWorkflow.get("jobs") or {}).items():
        for dictStep in dictJob.get("steps") or []:
            sRun = dictStep.get("run") or ""
            bHazard = any(
                sMarker in sRun for sMarker in T_UNBOUNDED_HAZARD_MARKERS
            )
            if bHazard and not dictStep.get("timeout-minutes"):
                listOffenders.append(
                    f"{sJobName}: {dictStep.get('name', sRun[:40])}"
                )
    assert listOffenders == [], (
        f"{sName} has network setup steps with no timeout-minutes, so "
        f"a stalled runner reports nothing for hours: {listOffenders}"
    )


@pytest.mark.falsification
def testEveryGateSupersedesItsOwnSupersededRun():
    """A push replaces the run before it; it must not queue behind it.

    The third instance of this file's shape, and the same lesson from
    the other side: a check that cannot say what it did not do. Here
    the check says nothing because it never starts. Every push to an
    open pull request queued another full matrix, and nothing cancelled
    the run it had just made irrelevant -- so on the macOS legs, the
    scarcest runners and the widest matrix, twelve unit jobs sat at
    QUEUED for two hours behind a superseded run while the PR page
    showed them as pending (2026-09-21). Pending and never-started are
    indistinguishable on that page, which is precisely the failure this
    file exists for.

    The oracle is the two properties that make cancelling safe rather
    than merely fast, and both are asserted: the group must be keyed on
    the PULL REQUEST, so two different pull requests never cancel each
    other, and cancelling must be switched OFF for anything that is not
    a pull request, because a push to main and a manual dispatch are
    results somebody wants rather than drafts to replace.

    Kills: dropping the ``concurrency`` block from a gating workflow,
    which puts its next run back in the queue behind the last one.
    """
    for sName in sorted(T_PRE_MERGE_WORKFLOWS):
        dictWorkflow = _fdictLoadWorkflow(sName)
        dictConcurrency = dictWorkflow.get("concurrency")
        assert dictConcurrency, (
            f"{sName} has no concurrency group, so a new push queues "
            "behind the run it just superseded"
        )
        sGroup = str(dictConcurrency.get("group", ""))
        assert "pull_request.number" in sGroup, (
            f"{sName}'s concurrency group must be keyed on the pull "
            f"request, or two unrelated pull requests cancel each "
            f"other: {sGroup!r}"
        )
        sCancel = str(dictConcurrency.get("cancel-in-progress", ""))
        assert "pull_request" in sCancel, (
            f"{sName} cancels unconditionally; a push to main and a "
            f"manual dispatch are results somebody asked for: "
            f"{sCancel!r}"
        )
