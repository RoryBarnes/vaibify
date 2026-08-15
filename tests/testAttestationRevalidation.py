"""Attestation lives beside every definition-sensitive result (§4.4).

A stable id names *which step*, never *which definition of it*: run
statistics and verification verdicts are claims about the commands
that produced them. Each such field carries, in
``dictDefinitionProducers``, the semantic fingerprint its producer
acted under, and every state→workflow merge revalidates it — a check
made only at completion protects one write, and the next reload would
reattach old results to the new definition. The producer matrix is
R8's: the run stamps at its completion merge, the researcher's
approval stamps at the update seam, the high-water ratchet is add-only
history and never invalidated, and legacy state with no recorded
producer is unattested everywhere — never backfilled, because
attribution proves an owner, never a definition.
"""

import json

import pytest

from vaibify.gui.stateManager import (
    fdictMergeRunResultsIntoState,
    fnMergeStateIntoWorkflow,
    ftSplitMergedDict,
)
from vaibify.gui.workflowManager import (
    fnUpdateStep,
    fsComputeSemanticWorkflowFingerprint,
)

_S_CONTAINER = "cid"
_S_STATE_PATH = "/workspace/repo/.vaibify/state.json"
_S_WORKFLOW_KEY = ".vaibify/projects/study.json"
_S_STEP_ID = "analyze-sample"


class _FakeStateDocker:
    """Holds state.json bytes; applies the atomic-rename install."""

    def __init__(self):
        self.dictFiles = {}

    def fbaFetchFile(self, _sContainerId, sPath):
        if sPath in self.dictFiles:
            return self.dictFiles[sPath]
        raise FileNotFoundError(sPath)

    def fnWriteFile(self, _sContainerId, sPath, baPayload):
        self.dictFiles[sPath] = baPayload

    def ftResultExecuteCommand(self, _sContainerId, sCommand):
        if sCommand.startswith("mv -f "):
            import shlex
            listParts = shlex.split(sCommand.split("||")[0])
            self.dictFiles[listParts[3]] = self.dictFiles.pop(
                listParts[2],
            )
        return (0, "")


def _fdictWorkflow(sCommand="python analyze.py"):
    return {
        "sPlotDirectory": "plots",
        "listSteps": [{
            "sStepId": _S_STEP_ID,
            "sName": "Analyze Sample",
            "sDirectory": "AnalyzeSample",
            "saCommands": [sCommand],
            "saPlotCommands": [],
            "saPlotFiles": [],
        }],
    }


def _fdictMergedSection(dockerFake):
    return json.loads(
        dockerFake.dictFiles[_S_STATE_PATH].decode("utf-8"),
    )["dictWorkflowState"][_S_WORKFLOW_KEY]


def _fdictRunCompletionMerge(dockerFake, dictRunWorkflow):
    return fdictMergeRunResultsIntoState(
        dockerFake, _S_CONTAINER, _S_STATE_PATH, _S_WORKFLOW_KEY,
        {_S_STEP_ID: {"fWallClockSeconds": 8.0, "iExitCode": 0}},
        {_S_STEP_ID: "AnalyzeSample"},
        sRunDefinitionFingerprint=(
            fsComputeSemanticWorkflowFingerprint(dictRunWorkflow)
        ),
    )


@pytest.mark.falsification
def testARunStampsItsDispatchTimeDefinition():
    """Kills: recording WHICH definition produced the run's stats.

    Without the stamp there is no producer record, every result is
    permanently unattested, and a definition edit can never be told
    apart from the definition the run actually executed.
    """
    dockerFake = _FakeStateDocker()
    dictOutcome = _fdictRunCompletionMerge(dockerFake, _fdictWorkflow())
    assert dictOutcome["bPersisted"] is True
    dictEntry = _fdictMergedSection(dockerFake)["dictStepState"][
        _S_STEP_ID
    ]
    assert dictEntry["dictDefinitionProducers"]["dictRunStats"] == (
        fsComputeSemanticWorkflowFingerprint(_fdictWorkflow())
    )


@pytest.mark.falsification
def testAnEditedDefinitionMarksTheRunStatsSuperseded():
    """Kills: revalidation on the read merge, not only at completion.

    The run completed under definition A; the researcher then edited
    the command. Reloading must not reattach A's results to B's
    definition as if current — that silent reattachment is the §4.4
    headline failure.
    """
    dockerFake = _FakeStateDocker()
    _fdictRunCompletionMerge(dockerFake, _fdictWorkflow())

    dictEditedWorkflow = _fdictWorkflow("python analyze.py --deeper")
    fnMergeStateIntoWorkflow(
        dictEditedWorkflow,
        json.loads(dockerFake.dictFiles[_S_STATE_PATH].decode("utf-8")),
        _S_WORKFLOW_KEY,
        sCurrentSemanticFingerprint=(
            fsComputeSemanticWorkflowFingerprint(dictEditedWorkflow)
        ),
    )
    dictStep = dictEditedWorkflow["listSteps"][0]
    assert dictStep["dictRunStats"]["fWallClockSeconds"] == 8.0
    assert dictStep["dictStaleResultFields"]["dictRunStats"] == (
        "superseded"
    ), (
        "an edited definition silently reattached the old run's "
        "results as current"
    )


def testAMatchingDefinitionIsNotMarked():
    dockerFake = _FakeStateDocker()
    _fdictRunCompletionMerge(dockerFake, _fdictWorkflow())
    dictSameWorkflow = _fdictWorkflow()
    fnMergeStateIntoWorkflow(
        dictSameWorkflow,
        json.loads(dockerFake.dictFiles[_S_STATE_PATH].decode("utf-8")),
        _S_WORKFLOW_KEY,
        sCurrentSemanticFingerprint=(
            fsComputeSemanticWorkflowFingerprint(dictSameWorkflow)
        ),
    )
    assert "dictStaleResultFields" not in (
        dictSameWorkflow["listSteps"][0]
    )


def testLegacyStateIsUnattestedAndNeverBackfilled():
    """R8: no recorded producer means unattested — and a save cycle
    must not upgrade it to the current fingerprint."""
    dictWorkflow = _fdictWorkflow()
    dictLegacyState = {"dictStepState": {_S_STEP_ID: {
        "dictVerification": {"sUser": "passed"},
    }}}
    fnMergeStateIntoWorkflow(
        dictWorkflow, dictLegacyState,
        sCurrentSemanticFingerprint=(
            fsComputeSemanticWorkflowFingerprint(dictWorkflow)
        ),
    )
    dictStep = dictWorkflow["listSteps"][0]
    assert dictStep["dictStaleResultFields"]["dictVerification"] == (
        "unattested"
    )
    _dictDeclarative, dictStateSplit = ftSplitMergedDict(dictWorkflow)
    dictEntry = dictStateSplit["dictStepState"][_S_STEP_ID]
    assert "dictDefinitionProducers" not in dictEntry, (
        "the save cycle backfilled a producer the legacy state never "
        "had; attribution proves an owner, never a definition"
    )


@pytest.mark.falsification
def testTheProducerSurvivesTheSaveRoundtrip():
    """Kills: the producer record riding the merged-dict roundtrip.

    An ordinary save rebuilds the state section from the in-memory
    workflow; a producer that does not ride that roundtrip is erased
    by the first save after the run, and every result quietly
    degrades to unattested.
    """
    dictWorkflow = _fdictWorkflow()
    dictState = {"dictStepState": {_S_STEP_ID: {
        "dictRunStats": {"fWallClockSeconds": 8.0},
        "dictDefinitionProducers": {"dictRunStats": "f" * 64},
    }}}
    fnMergeStateIntoWorkflow(dictWorkflow, dictState)
    _dictDeclarative, dictStateSplit = ftSplitMergedDict(dictWorkflow)
    assert dictStateSplit["dictStepState"][_S_STEP_ID][
        "dictDefinitionProducers"
    ] == {"dictRunStats": "f" * 64}


def testTheHighWaterRatchetIsNeverMarked():
    """The add-only history records what was attained when; marking it
    stale would falsify history rather than protect it."""
    dictWorkflow = _fdictWorkflow()
    fnMergeStateIntoWorkflow(
        dictWorkflow,
        {"dictStepState": {_S_STEP_ID: {
            "dictLevelHighWater": {"1": {"sState": "attained"}},
        }}},
        sCurrentSemanticFingerprint=(
            fsComputeSemanticWorkflowFingerprint(dictWorkflow)
        ),
    )
    assert "dictStaleResultFields" not in (
        dictWorkflow["listSteps"][0]
    )


def testTheVerdictNeverPersists():
    dictWorkflow = _fdictWorkflow()
    dictWorkflow["listSteps"][0]["dictStaleResultFields"] = {
        "dictRunStats": "superseded",
    }
    dictDeclarative, dictStateSplit = ftSplitMergedDict(dictWorkflow)
    assert "dictStaleResultFields" not in dictDeclarative["listSteps"][0]
    assert not any(
        "dictStaleResultFields" in dictEntry
        for dictEntry in dictStateSplit["dictStepState"].values()
    )


@pytest.mark.falsification
def testAUserApprovalStampsTheDefinitionItSaw():
    """Kills: the researcher-approval producer stamp (R8's human act).

    An approval given while looking at one definition must not vouch
    for commands it never saw; the stamp is what lets a later edit
    mark it superseded instead.
    """
    dictWorkflow = _fdictWorkflow()
    fnUpdateStep(
        dictWorkflow, 0,
        {"dictVerification": {"sUser": "passed"}},
    )
    dictStep = dictWorkflow["listSteps"][0]
    assert dictStep["dictDefinitionProducers"]["dictVerification"] == (
        fsComputeSemanticWorkflowFingerprint(dictWorkflow)
    )


def testANonVerificationUpdateStampsNothing():
    dictWorkflow = _fdictWorkflow()
    fnUpdateStep(dictWorkflow, 0, {"sNotes": "tuning"})
    assert "dictDefinitionProducers" not in dictWorkflow["listSteps"][0]
