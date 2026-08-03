"""Forward compatibility: a future-version workflow fails closed.

``fnApplyMigrations`` used to stamp the version DOWN to the current one at
the end, so a ``project.json`` written by a newer vaibify and opened by an
older one was silently downgraded and, on the next save, persisted with
the lower number and every field this build does not understand dropped.
These pin the fail-closed behaviour at the migrator and at the container
load path — the "unrecognized future state fails closed" rule.
"""

import copy
import json

import pytest

from vaibify.gui import workflowManager
from vaibify.gui import workflowMigrations


@pytest.mark.falsification
def test_future_schema_version_is_refused_not_downgraded():
    """A version above the current must raise, leaving the dict untouched.

    Kills: in workflowMigrations.fnApplyMigrations, neutralize the
    ``if iVersion > I_CURRENT_WORKFLOW_VERSION:`` guard, so a future
    version falls through to the final stamp and is silently downgraded.
    """
    iFuture = workflowMigrations.I_CURRENT_WORKFLOW_VERSION + 1
    dictFuture = {
        workflowMigrations.S_VERSION_KEY: iFuture,
        "sWorkflowName": "From a newer vaibify",
        "listSteps": [],
        "sFieldThisBuildDoesNotKnow": "must not be dropped",
    }
    dictBefore = copy.deepcopy(dictFuture)
    with pytest.raises(ValueError):
        workflowMigrations.fnApplyMigrations(dictFuture)
    assert dictFuture == dictBefore, (
        "a refused future workflow was mutated before the raise"
    )


def test_current_and_older_versions_still_migrate():
    """The fail-closed guard must not block ordinary forward migration."""
    dictOld = {
        workflowMigrations.S_VERSION_KEY: 0,
        "sWorkflowName": "Old",
        "listSteps": [],
    }
    iReturned = workflowMigrations.fnApplyMigrations(dictOld)
    assert iReturned == workflowMigrations.I_CURRENT_WORKFLOW_VERSION
    assert dictOld[workflowMigrations.S_VERSION_KEY] == (
        workflowMigrations.I_CURRENT_WORKFLOW_VERSION
    )


class _ConnectionServingOneWorkflow:
    """Return fixed project.json bytes for any fetch, and record the path."""

    def __init__(self, baContent):
        self.baContent = baContent
        self.sRequestedPath = None

    def fbaFetchFile(self, sContainerId, sPath):
        self.sRequestedPath = sPath
        return self.baContent


def test_container_load_refuses_a_future_workflow():
    """The container load path fails closed on a future schema.

    A downgrade via any entry point is the hazard, so the property is
    asserted at the loader, not only at the migrator. This replaced the
    same assertion against the withdrawn host-side director loader; the
    container loader is now the only one that reads a project.json.
    """
    iFuture = workflowMigrations.I_CURRENT_WORKFLOW_VERSION + 1
    baContent = json.dumps({
        workflowMigrations.S_VERSION_KEY: iFuture,
        "sWorkflowName": "Future",
        "listSteps": [],
    }).encode("utf-8")
    connectionFake = _ConnectionServingOneWorkflow(baContent)
    with pytest.raises(ValueError):
        workflowManager.fdictLoadWorkflowFromContainer(
            connectionFake, "containerId", "/workspace/repo/project.json",
        )
    assert connectionFake.sRequestedPath == (
        "/workspace/repo/project.json"
    ), "the loader refused before it read the file it was asked for"
