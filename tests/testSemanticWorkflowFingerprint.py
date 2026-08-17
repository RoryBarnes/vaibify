"""The attestation fingerprint names the definition, not the bytes.

Spec §4.4: a stable id names *which step*, never *which definition of
it*. The semantic fingerprint is the definition's name — it must move
when any resolved-contract input moves (commands, order, directories,
declared outputs) and must NOT move when the run itself updates a
remote-data record's digest, or the provenance commit would change the
value being compared and the run would conflict with itself.
"""

import copy

import pytest

from vaibify.gui.workflowManager import (
    fsComputeSemanticWorkflowFingerprint,
    fsComputeWorkflowFingerprint,
)


def _fdictWorkflow():
    return {
        "sPlotDirectory": "plots",
        "listSteps": [{
            "sStepId": "pull-archive",
            "sName": "Pull Archive",
            "sDirectory": "PullArchive",
            "saCommands": ["python pull.py"],
            "saPlotCommands": [],
            "saPlotFiles": [],
            "saOutputDataFiles": ["data/pull.fits"],
            "listRemoteData": [{
                "sPath": "data/pull.fits",
                "sSourceUrl": "https://archive.example/query",
            }],
        }],
    }


def _fdictWithDigest(sSha256):
    dictWorkflow = _fdictWorkflow()
    dictWorkflow["listSteps"][0]["listRemoteData"][0].update({
        "sSha256": sSha256,
        "sDigestBecameCurrentUtc": "2026-08-14T00:00:00Z",
    })
    return dictWorkflow


@pytest.mark.falsification
def testTheRunsOwnDigestUpdateDoesNotMoveTheFingerprint():
    """Kills: excluding run-produced digest fields from the identity.

    If the digests were included, the provenance commit would move
    the fingerprint the completion merge is about to compare — every
    run with remote data would invalidate its own attestation.
    """
    sBare = fsComputeSemanticWorkflowFingerprint(_fdictWorkflow())
    sWithDigest = fsComputeSemanticWorkflowFingerprint(
        _fdictWithDigest("a" * 64),
    )
    sWithOtherDigest = fsComputeSemanticWorkflowFingerprint(
        _fdictWithDigest("b" * 64),
    )
    assert sBare == sWithDigest == sWithOtherDigest, (
        "the run's own digest update moved the semantic fingerprint; "
        "the run now conflicts with itself"
    )


def testTheExactFingerprintStillMovesOnADigestUpdate():
    """The two fingerprints are different names on purpose.

    The exact-source fingerprint names bytes (freshness authority);
    unifying it with the semantic one would blind the dispatch gate
    to digest edits.
    """
    assert fsComputeWorkflowFingerprint(
        _fdictWorkflow(),
    ) != fsComputeWorkflowFingerprint(_fdictWithDigest("a" * 64))


def testEveryDefinitionInputMovesTheFingerprint():
    sBaseline = fsComputeSemanticWorkflowFingerprint(_fdictWorkflow())

    dictEditedCommand = _fdictWorkflow()
    dictEditedCommand["listSteps"][0]["saCommands"] = [
        "python pull.py --deeper",
    ]
    dictEditedOutputs = _fdictWorkflow()
    dictEditedOutputs["listSteps"][0]["saOutputDataFiles"] = [
        "data/other.fits",
    ]
    dictEditedGlobal = _fdictWorkflow()
    dictEditedGlobal["sPlotDirectory"] = "figures"
    dictEditedSource = _fdictWorkflow()
    dictEditedSource["listSteps"][0]["listRemoteData"][0][
        "sSourceUrl"
    ] = "https://elsewhere.example/query"
    for dictEdited in (
        dictEditedCommand, dictEditedOutputs, dictEditedGlobal,
        dictEditedSource,
    ):
        assert fsComputeSemanticWorkflowFingerprint(
            dictEdited,
        ) != sBaseline


def testStepOrderIsPartOfTheDefinition():
    """Variables resolve through step order; swapping steps is an edit."""
    dictWorkflow = _fdictWorkflow()
    dictSecond = copy.deepcopy(dictWorkflow["listSteps"][0])
    dictSecond.update({
        "sStepId": "analyze", "sName": "Analyze",
        "sDirectory": "Analyze", "listRemoteData": [],
    })
    dictWorkflow["listSteps"].append(dictSecond)
    dictSwapped = copy.deepcopy(dictWorkflow)
    dictSwapped["listSteps"].reverse()
    assert fsComputeSemanticWorkflowFingerprint(
        dictWorkflow,
    ) != fsComputeSemanticWorkflowFingerprint(dictSwapped)


def testRunProducedStateFieldsAreNotDefinition():
    """Verification and run stats live in state.json; a merged dict
    carrying them must fingerprint identically to a bare one."""
    dictMerged = _fdictWorkflow()
    dictMerged["listSteps"][0]["dictVerification"] = {
        "sIntegrity": "passed",
    }
    dictMerged["listSteps"][0]["dictRunStats"] = {
        "fWallClockSeconds": 3.5,
    }
    assert fsComputeSemanticWorkflowFingerprint(
        dictMerged,
    ) == fsComputeSemanticWorkflowFingerprint(_fdictWorkflow())
