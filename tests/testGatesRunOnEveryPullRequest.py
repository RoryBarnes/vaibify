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
