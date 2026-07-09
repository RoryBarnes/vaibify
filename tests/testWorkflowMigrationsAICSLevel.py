"""Migration tests for the v3→v4 AICS-ladder cutover.

The v4 migrator drops two legacy keys: ``bVaibified`` (a derived
purple-theme flag that occasionally got hand-persisted) and any
pre-existing ``iAICSLevel`` (so the post-load derivation hook in
``workflowManager`` recomputes against current state.json rather
than trusting a stale persisted integer).
"""

from vaibify.gui import workflowMigrations


def test_v3_to_v4_drops_legacy_bVaibified():
    dictWorkflow = {
        "iWorkflowSchemaVersion": 3,
        "bVaibified": True,
        "listSteps": [],
    }
    workflowMigrations.fnApplyMigrations(dictWorkflow)
    assert "bVaibified" not in dictWorkflow
    assert dictWorkflow["iWorkflowSchemaVersion"] == (
        workflowMigrations.I_CURRENT_WORKFLOW_VERSION
    )


def test_v3_to_v4_drops_pre_existing_iAICSLevel():
    """Stale persisted level must not survive the migrator.

    The derivation hook in ``fdictLoadWorkflowFromContainer`` runs
    after state.json merge and is the only authority on the integer;
    a pre-existing value would mask a regression in current state.
    """
    dictWorkflow = {
        "iWorkflowSchemaVersion": 3,
        "iAICSLevel": 2,
        "listSteps": [],
    }
    workflowMigrations.fnApplyMigrations(dictWorkflow)
    assert "iAICSLevel" not in dictWorkflow


def test_v3_to_v4_is_idempotent_when_keys_absent():
    """Migrator must be a no-op when neither legacy key is present."""
    dictWorkflow = {
        "iWorkflowSchemaVersion": 3,
        "listSteps": [{"sName": "A", "sDirectory": "A"}],
    }
    workflowMigrations.fnApplyMigrations(dictWorkflow)
    assert dictWorkflow["iWorkflowSchemaVersion"] == (
        workflowMigrations.I_CURRENT_WORKFLOW_VERSION
    )
    assert "bVaibified" not in dictWorkflow
    assert "iAICSLevel" not in dictWorkflow


def test_v3_to_v4_handles_empty_workflow_defensively():
    """A minimal dict at v3 must not crash the migrator."""
    dictWorkflow = {"iWorkflowSchemaVersion": 3}
    workflowMigrations.fnApplyMigrations(dictWorkflow)
    assert dictWorkflow["iWorkflowSchemaVersion"] == (
        workflowMigrations.I_CURRENT_WORKFLOW_VERSION
    )
