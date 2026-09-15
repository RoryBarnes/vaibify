"""Promoting the project deposit: a new production release, not a copy.

Only the *environment archive* copies bytes. The project deposit's
promotion publishes the CURRENT canonical publication union on
production Zenodo — the same set an ordinary archive publishes,
collected the same way — so the DOI names the tree as it is now. Saying
otherwise in the confirmation would mislead a researcher about what the
DOI they just minted actually names.

Two structural properties this module holds:

* **``project.json`` bytes do not move.** Every field a promotion
  produces is bookkeeping and lands in the uncompared sidecar. Writing
  the instance into the definition would stale the production deposit
  the moment it was minted — the treadmill ``syncBookkeeping`` exists
  to kill.
* **Additional declared records block it, ALL of them.** A declared
  record carries no instance of its own: the verify reads one instance
  off the primary and applies it to every record. A DOI-less entry
  would classify ``unknown``, pass a permanence-based check, and then
  404 against production — breaking the verify for the whole project.
"""

import json

import pytest

from vaibify.gui import workflowManager
from vaibify.gui.routes import syncRoutes
from vaibify.reproducibility import syncBookkeeping


def _fdictSandboxProject():
    return {
        "listSteps": [],
        "sZenodoService": "sandbox",
        "sZenodoDepositionId": "551",
        "sZenodoLatestDoi": "10.5072/zenodo.551",
        "sZenodoLatestUrl": "https://sandbox.zenodo.org/record/551",
        "dictRemotes": {"zenodo": {
            "sRecordId": "551",
            "sDoi": "10.5072/zenodo.551",
            "sService": "sandbox",
        }},
    }


def test_a_declared_extra_record_is_refused_even_without_a_doi():
    """The no-DOI entry is the one a permanence check would let through."""
    dictWorkflow = _fdictSandboxProject()
    dictWorkflow["dictRemotes"]["zenodo"]["listRecords"] = [
        {"sRecordId": "999"},
    ]
    with pytest.raises(Exception) as error:
        syncRoutes._fnRefuseWhileOtherRecordsAreDeclared(dictWorkflow)
    assert "999" in str(error.value.detail)
    assert "break" in str(error.value.detail)


def test_a_project_with_no_extra_records_is_not_refused():
    syncRoutes._fnRefuseWhileOtherRecordsAreDeclared(
        _fdictSandboxProject(),
    )


def test_a_production_deposit_is_refused_by_name():
    dictWorkflow = _fdictSandboxProject()
    dictWorkflow["dictRemotes"]["zenodo"]["sService"] = "zenodo"
    with pytest.raises(Exception) as error:
        syncRoutes._fnRequireSandboxProjectDeposit(dictWorkflow)
    assert "already on production" in str(error.value.detail)


def test_the_promotion_writes_no_project_definition_bytes():
    """project.json must byte-match before and after.

    Every produced field is extracted into the sidecar, so a
    definition that published to the sandbox and one that has just
    published to production serialize identically.
    """
    dictWorkflow = _fdictSandboxProject()
    sBefore, _dictState, _dictBook = (
        workflowManager._ftSplitAndSerializeWorkflow(dictWorkflow)
    )
    syncRoutes._fnRetireSupersededZenodoRecord(dictWorkflow)
    syncRoutes._fnPersistZenodoPublishRecord(
        dictWorkflow,
        {"iDepositId": 7001, "sDoi": "10.5281/zenodo.7001",
         "sConceptDoi": "10.5281/zenodo.7000",
         "sHtmlUrl": "https://zenodo.org/record/7001"},
        "zenodo",
    )
    sAfter, _dictState, dictBookkeeping = (
        workflowManager._ftSplitAndSerializeWorkflow(dictWorkflow)
    )
    assert sBefore == sAfter, (
        "the promotion moved the project definition, which the Level "
        "2 verifies compare against both remotes"
    )
    dictProduced = dictBookkeeping[
        syncBookkeeping.S_REMOTE_BOOKKEEPING_KEY]["zenodo"]
    assert dictProduced["sService"] == "zenodo"
    assert dictProduced["dictSuperseded"]["sDoi"] == "10.5072/zenodo.551"


def test_the_declared_instance_is_untouched_by_a_promotion():
    """Ruling 7: the promotion never writes a declaration for anyone."""
    dictWorkflow = _fdictSandboxProject()
    syncRoutes._fnRetireSupersededZenodoRecord(dictWorkflow)
    syncRoutes._fnPersistZenodoPublishRecord(
        dictWorkflow, {"iDepositId": 7001, "sDoi": "10.5281/zenodo.7001"},
        "zenodo",
    )
    assert dictWorkflow["sZenodoService"] == "sandbox"


def test_the_superseded_note_is_a_produced_field():
    """In project.json it would stale the deposit it describes."""
    assert "dictSuperseded" in (
        syncBookkeeping.DICT_REMOTE_PRODUCED_FIELDS["zenodo"]
    )


# ── start-new-concept: the remedy the refusal names ──


def test_start_new_concept_retires_the_identifiers_rather_than_deleting():
    """The old DOI keeps resolving; a researcher must still see it."""
    dictWorkflow = _fdictSandboxProject()
    dictWorkflow["dictRemotes"]["zenodo"]["sService"] = "zenodo"
    syncRoutes._fnRetireSupersededZenodoRecord(dictWorkflow)
    syncRoutes._fnClearPrimaryZenodoRecord(dictWorkflow)
    dictZenodo = dictWorkflow["dictRemotes"]["zenodo"]
    assert "sRecordId" not in dictZenodo
    assert "sDoi" not in dictZenodo
    assert "sService" not in dictZenodo
    assert dictZenodo["dictSuperseded"]["sRecordId"] == "551"
    assert "sZenodoDepositionId" not in dictWorkflow


def test_the_round_trip_promote_declare_refuse_start_new_concept():
    """Promote, set back to sandbox, be refused, clear, publish fresh."""
    dictWorkflow = _fdictSandboxProject()
    syncRoutes._fnRetireSupersededZenodoRecord(dictWorkflow)
    syncRoutes._fnPersistZenodoPublishRecord(
        dictWorkflow,
        {"iDepositId": 7001, "sDoi": "10.5281/zenodo.7001"}, "zenodo",
    )
    # The researcher sets the instance selector back to sandbox.
    assert dictWorkflow["sZenodoService"] == "sandbox"
    sRefusal = syncBookkeeping.fsDescribeCrossInstanceParent(
        dictWorkflow, "sandbox",
    )
    assert "new concept" in sRefusal
    syncRoutes._fnRetireSupersededZenodoRecord(dictWorkflow)
    syncRoutes._fnClearPrimaryZenodoRecord(dictWorkflow)
    assert syncBookkeeping.fsDescribeCrossInstanceParent(
        dictWorkflow, "sandbox",
    ) == ""
    assert syncBookkeeping.fiResolveZenodoParentDepositId(
        dictWorkflow,
    ) == 0, "the next publish must be a FIRST version, not a new one"
    assert dictWorkflow["dictRemotes"]["zenodo"]["dictSuperseded"][
        "sDoi"] == "10.5281/zenodo.7001"


def test_the_cleared_record_leaves_the_definition_alone():
    dictWorkflow = _fdictSandboxProject()
    sBefore, _dictState, _dictBook = (
        workflowManager._ftSplitAndSerializeWorkflow(dictWorkflow)
    )
    syncRoutes._fnRetireSupersededZenodoRecord(dictWorkflow)
    syncRoutes._fnClearPrimaryZenodoRecord(dictWorkflow)
    sAfter, _dictState, _dictBook = (
        workflowManager._ftSplitAndSerializeWorkflow(dictWorkflow)
    )
    assert json.loads(sBefore) == json.loads(sAfter)
