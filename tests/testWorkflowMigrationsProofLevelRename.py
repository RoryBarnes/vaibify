"""Migration tests for the v10→v11 AICS→PROOF level-key rename.

The ladder was renamed from the AI Containment Scale to PROOF, and the
derived integer with it: ``iAICSLevel`` became ``iProofLevel``. A
modern save never writes the level into project.json — ``stateManager``
moves it to state.json — so the v11 migrator exists for the
hand-edited or forked project.json that carries the old key at a
version the v3→v4 migrator no longer runs on.

The migrator only *drops*. It must never synthesize the new key: the
level is derived from current verification state by the post-load hook
in ``workflowManager``, and a migrator that carried the old integer
across would resurrect exactly the stale value the v3→v4 migrator was
written to kill.
"""

from vaibify.gui import workflowMigrations


def test_v10_to_v11_drops_the_pre_rename_level_key():
    """A v10 document carrying ``iAICSLevel`` loses it."""
    dictWorkflow = {
        "iWorkflowSchemaVersion": 10,
        "iAICSLevel": 3,
        "listSteps": [],
    }
    workflowMigrations.fnApplyMigrations(dictWorkflow)
    assert "iAICSLevel" not in dictWorkflow
    assert dictWorkflow["iWorkflowSchemaVersion"] == (
        workflowMigrations.I_CURRENT_WORKFLOW_VERSION
    )


def test_v10_to_v11_does_not_carry_the_value_to_the_new_key():
    """The stale integer must not be renamed into ``iProofLevel``.

    Carrying it across would defeat the derivation hook, which is the
    only authority on the level, and would let a hand-edited file
    assert a level the project has not attained.
    """
    dictWorkflow = {
        "iWorkflowSchemaVersion": 10,
        "iAICSLevel": 3,
        "listSteps": [],
    }
    workflowMigrations.fnApplyMigrations(dictWorkflow)
    assert "iProofLevel" not in dictWorkflow


def test_v10_to_v11_is_a_no_op_when_the_legacy_key_is_absent():
    """The ordinary modern document passes through untouched."""
    dictWorkflow = {
        "iWorkflowSchemaVersion": 10,
        "listSteps": [{"sName": "A", "sDirectory": "A"}],
    }
    workflowMigrations.fnApplyMigrations(dictWorkflow)
    assert "iAICSLevel" not in dictWorkflow
    assert "iProofLevel" not in dictWorkflow
    assert dictWorkflow["iWorkflowSchemaVersion"] == (
        workflowMigrations.I_CURRENT_WORKFLOW_VERSION
    )


def test_legacy_document_reaches_v11_through_the_whole_chain():
    """A v0 document carrying the old key still arrives clean at v11.

    The v3→v4 migrator drops the key first, so this asserts the chain
    composes rather than that v11 did the work — a version-0 file is
    the oldest thing on disk and must survive every migrator in order.
    """
    dictWorkflow = {"iAICSLevel": 2, "listSteps": []}
    workflowMigrations.fnApplyMigrations(dictWorkflow)
    assert "iAICSLevel" not in dictWorkflow
    assert dictWorkflow["iWorkflowSchemaVersion"] == (
        workflowMigrations.I_CURRENT_WORKFLOW_VERSION
    )


def test_current_version_constant_registers_every_migrator():
    """The version must equal the last migrator's target.

    A bumped constant with no registered migrator silently skips the
    migration for every file; a registered migrator with no bump runs
    it on every load forever.
    """
    iLastTarget = workflowMigrations.T_MIGRATORS[-1][0] + 1
    assert iLastTarget == workflowMigrations.I_CURRENT_WORKFLOW_VERSION
