"""Completion is state-only: it must not write ``project.json`` at all.

The shipped defect (spec D2): ``_fnFinalizeRun`` ended every run by
``json.dumps``-ing the run's whole in-memory workflow over
``project.json``. That snapshot dates from dispatch time, so the write
destroyed any edit the reload detector had correctly accepted mid-run
— and it wrote stateful fields (``dictRunStats``,
``dictVerification``) into the declarative file while leaving
``state.json`` stale.

Completion now merges the run's per-step delta — what its own event
stream recorded — into a freshly loaded ``state.json`` document, BY
STABLE STEP ID, scoped to executed steps. ``project.json`` is never
touched. The terminal ``pipeline_state.json`` flush is acknowledged
and failable, and the terminal event reports whether the run's
results actually became durable.

Every fixture here routes writes through a filesystem-modelling fake
that implements the temp-write + ``mv`` install, so an assertion about
"what is on disk afterwards" reads the same bytes a researcher's next
load would.
"""

import asyncio
import json
import re

import pytest

from vaibify.gui import pipelineState
from vaibify.gui.pipelineLogger import (
    _fdictPersistRunResultsToState,
    _fnFinalizeRun,
)
from vaibify.gui.stateManager import (
    fdictInstallWorkflowSection,
    fdictMergeRunResultsIntoState,
)


S_CONTAINER_ID = "cid-completion-demo"
S_REPO = "/workspace/exampleRepo"
S_WORKFLOW_PATH = S_REPO + "/.vaibify/projects/demo.json"
S_STATE_PATH = S_REPO + "/.vaibify/state.json"
S_WORKFLOW_KEY = ".vaibify/projects/demo.json"
S_LOG_PATH = S_REPO + "/.vaibify/logs/demo.log"


class ConnectionModellingFiles:
    """A container whose files behave like files.

    ``fnWriteFile`` stores bytes; ``mv A B`` installs them at the
    destination; ``cp -f`` copies; the base64 log append and the
    checkpoint guard answer quietly. Reads come from the same store,
    so what a test asserts "on disk" is what the next loader would
    read — a fake that only records write calls cannot catch a write
    that lands at the wrong path, or one that never lands at all.
    """

    def __init__(self, dictFiles=None):
        self.dictFiles = dict(dictFiles or {})
        self.bFailWrites = False

    def fbaFetchFile(self, _sContainerId, sPath):
        if sPath in self.dictFiles:
            return self.dictFiles[sPath]
        raise FileNotFoundError(sPath)

    def fnWriteFile(self, _sContainerId, sPath, baPayload):
        if self.bFailWrites:
            raise OSError("simulated container write failure")
        self.dictFiles[sPath] = baPayload

    def ftResultExecuteCommand(self, _sContainerId, sCommand):
        if self.bFailWrites:
            return (1, "simulated exec failure")
        matchMove = re.match(r"mv (?:-f )?'([^']+)' '([^']+)'", sCommand)
        if matchMove:
            sSource, sDestination = matchMove.groups()
            if sSource not in self.dictFiles:
                return (1, f"mv: {sSource}: no such file")
            self.dictFiles[sDestination] = self.dictFiles.pop(sSource)
            return (0, "")
        return (0, "")

    def fdictReadJson(self, sPath):
        return json.loads(self.dictFiles[sPath].decode("utf-8"))


def _fdictRunWorkflow():
    """The run's in-memory snapshot, stats already recorded on step 1."""
    return {
        "sWorkflowName": "demo",
        "sProjectRepoPath": S_REPO,
        "listSteps": [
            {
                "sStepId": "analyze", "sName": "Analyze",
                "sDirectory": "Analyze",
                "dictRunStats": {"fWallClock": 4.2},
            },
            {
                "sStepId": "plot", "sName": "Plot",
                "sDirectory": "Plot",
            },
        ],
    }


def _fdictStateAfterStepOne():
    """The run's pipeline state after step 1 executed and recorded."""
    dictState = pipelineState.fdictBuildInitialState(
        "runSelected", S_LOG_PATH, 2, sWorkflowPath=S_WORKFLOW_PATH,
    )
    dictState["dictStepStats"] = {"1": {"fWallClock": 4.2}}
    return dictState


def _fnFinalize(connection, dictState, dictWorkflow, listEvents):
    """Drive the production completion path synchronously."""

    async def fnStatusCallback(dictEvent):
        listEvents.append(dictEvent)

    asyncio.new_event_loop().run_until_complete(
        _fnFinalizeRun(
            connection, S_CONTAINER_ID, dictState, 0,
            S_LOG_PATH, [], dictWorkflow, S_WORKFLOW_PATH,
            fnStatusCallback,
        )
    )


@pytest.mark.falsification
def testAnExternalEditToProjectJsonSurvivesCompletion():
    """Kills: restoring the end-of-run write of the run's snapshot.

    The researcher edited ``project.json`` while the run was executing
    — the exact situation the reload detector exists to accept. The
    run's snapshot predates the edit, so ANY completion-time write of
    it destroys the edit; the byte-identity assertion catches a
    rewrite even when the content happens to look plausible.
    """
    baEditedProject = json.dumps(
        {"sWorkflowName": "demo", "listSteps": [],
         "sEditedMidRun": "the researcher's new value"},
    ).encode("utf-8")
    connection = ConnectionModellingFiles({
        S_WORKFLOW_PATH: baEditedProject,
    })
    listEvents = []

    _fnFinalize(
        connection, _fdictStateAfterStepOne(), _fdictRunWorkflow(),
        listEvents,
    )

    assert connection.dictFiles[S_WORKFLOW_PATH] == baEditedProject, (
        "completion rewrote project.json; the researcher's mid-run "
        "edit was destroyed by the run's stale snapshot"
    )
    dictDocument = connection.fdictReadJson(S_STATE_PATH)
    dictSection = dictDocument["dictWorkflowState"][S_WORKFLOW_KEY]
    assert dictSection["dictStepState"]["analyze"]["dictRunStats"] == {
        "fWallClock": 4.2,
    }, "the run's statistics never reached state.json"
    assert listEvents[-1]["bRunMetadataPersisted"] is True


@pytest.mark.falsification
def testTheMergePreservesAMidRunAttestation():
    """Kills: replacing the fresh step entry instead of merging into it.

    While the run executed, the researcher attested a step by eye and
    a save landed it in ``state.json``. The completion merge may only
    replace ``dictRunStats`` and clear the run-invalidated
    modification flags; installing the run's (or an empty) entry
    wholesale erases the attestation, which is exactly what the
    whole-snapshot writer used to do one file over.
    """
    dictSection = {
        "dictStepState": {
            "analyze": {
                "dictVerification": {
                    "sUser": "verified-by-eye",
                    "bOutputModified": True,
                },
                "dictRunStats": {"fWallClock": 999.0},
            },
        },
    }
    connection = ConnectionModellingFiles({
        S_STATE_PATH: json.dumps(fdictInstallWorkflowSection(
            {}, S_WORKFLOW_KEY, dictSection,
        )).encode("utf-8"),
    })

    dictOutcome = fdictMergeRunResultsIntoState(
        connection, S_CONTAINER_ID, S_STATE_PATH, S_WORKFLOW_KEY,
        {"analyze": {"fWallClock": 4.2}}, {"analyze": "Analyze"},
    )

    assert dictOutcome["bPersisted"] is True
    dictEntry = connection.fdictReadJson(S_STATE_PATH)[
        "dictWorkflowState"
    ][S_WORKFLOW_KEY]["dictStepState"]["analyze"]
    assert dictEntry["dictVerification"]["sUser"] == "verified-by-eye", (
        "the completion merge erased an attestation recorded mid-run"
    )
    assert dictEntry["dictRunStats"] == {"fWallClock": 4.2}
    assert "bOutputModified" not in dictEntry["dictVerification"], (
        "the step just ran; its stale output-modified flag must clear"
    )


def testTheMergeTouchesOnlyExecutedSteps():
    """A step the run never executed keeps its recorded state."""
    dictSection = {
        "dictStepState": {
            "plot": {"dictRunStats": {"fWallClock": 7.7}},
        },
    }
    connection = ConnectionModellingFiles({
        S_STATE_PATH: json.dumps(fdictInstallWorkflowSection(
            {}, S_WORKFLOW_KEY, dictSection,
        )).encode("utf-8"),
    })

    fdictMergeRunResultsIntoState(
        connection, S_CONTAINER_ID, S_STATE_PATH, S_WORKFLOW_KEY,
        {"analyze": {"fWallClock": 4.2}}, {"analyze": "Analyze"},
    )

    dictStepMap = connection.fdictReadJson(S_STATE_PATH)[
        "dictWorkflowState"
    ][S_WORKFLOW_KEY]["dictStepState"]
    assert dictStepMap["plot"] == {"dictRunStats": {"fWallClock": 7.7}}
    assert dictStepMap["analyze"]["dictRunStats"] == {"fWallClock": 4.2}


def testALegacyDirectoryEntryMigratesToItsId():
    """An entry persisted under the pre-id directory key is re-keyed.

    One entry, not two: leaving the directory copy behind would fork
    the step's state, and the half that wins would depend on read
    order.
    """
    dictSection = {
        "dictStepState": {
            "Analyze": {
                "dictVerification": {"sUser": "verified-by-eye"},
            },
        },
    }
    connection = ConnectionModellingFiles({
        S_STATE_PATH: json.dumps(fdictInstallWorkflowSection(
            {}, S_WORKFLOW_KEY, dictSection,
        )).encode("utf-8"),
    })

    fdictMergeRunResultsIntoState(
        connection, S_CONTAINER_ID, S_STATE_PATH, S_WORKFLOW_KEY,
        {"analyze": {"fWallClock": 4.2}}, {"analyze": "Analyze"},
    )

    dictStepMap = connection.fdictReadJson(S_STATE_PATH)[
        "dictWorkflowState"
    ][S_WORKFLOW_KEY]["dictStepState"]
    assert "Analyze" not in dictStepMap, "the directory key survived"
    assert dictStepMap["analyze"]["dictVerification"]["sUser"] == (
        "verified-by-eye"
    )
    assert dictStepMap["analyze"]["dictRunStats"] == {"fWallClock": 4.2}


@pytest.mark.falsification
def testADuplicateStepIdRefusesTheMergeWithoutWriting():
    """Kills: merging by id without first proving ids ARE identity.

    ``fnEnsureStepIds`` preserves an existing duplicate, and
    ``fdictStepIdToIndex`` lets the last occurrence silently win — so
    with a duplicated id the delta would attach one step's results to
    another. The merge must refuse and neither file may change.
    """
    dictWorkflow = _fdictRunWorkflow()
    dictWorkflow["listSteps"][1]["sStepId"] = "analyze"
    baProjectBefore = b'{"untouched": true}'
    connection = ConnectionModellingFiles({
        S_WORKFLOW_PATH: baProjectBefore,
    })

    dictOutcome = _fdictPersistRunResultsToState(
        connection, S_CONTAINER_ID, _fdictStateAfterStepOne(),
        dictWorkflow, S_WORKFLOW_PATH,
    )

    assert dictOutcome["bPersisted"] is False
    assert "analyze" in dictOutcome["sDetail"]
    assert S_STATE_PATH not in connection.dictFiles, (
        "the merge wrote state.json despite a duplicate step id; the "
        "results may belong to either of two steps"
    )
    assert connection.dictFiles[S_WORKFLOW_PATH] == baProjectBefore


@pytest.mark.falsification
def testADuplicateStepIdRefusesTheRun():
    """Kills: dispatching a run whose step ids cannot serve as identity.

    Refused at preflight, before any step executes: the completion
    merge would have to refuse anyway, and hours of computation whose
    results cannot be attributed is strictly worse than a refusal up
    front.
    """
    from vaibify.gui.pipelineRunner import _flistPreflightValidate
    dictWorkflow = _fdictRunWorkflow()
    dictWorkflow["listSteps"][1]["sStepId"] = "analyze"

    listErrors = asyncio.new_event_loop().run_until_complete(
        _flistPreflightValidate(
            ConnectionModellingFiles(), S_CONTAINER_ID, dictWorkflow, {},
        )
    )

    assert listErrors, "a duplicate step id passed preflight"
    assert "analyze" in listErrors[0]


@pytest.mark.falsification
def testAFailedTerminalFlushIsReported():
    """Kills: presuming the terminal state write landed.

    Every write the container answers fails. The terminal event must
    say the run's results did not become durable, and the in-memory
    state must be left running/finalizing so the writer thread's
    shutdown drain cannot install a terminal snapshot that nothing
    acknowledged — the durable record then still shows a run that
    never cleanly ended, which the stale-heartbeat reconciliation
    reports as the failure it is.
    """
    connection = ConnectionModellingFiles({})
    connection.bFailWrites = True
    dictState = _fdictStateAfterStepOne()
    stateWriter = pipelineState.StateWriter(
        connection, S_CONTAINER_ID, dictState,
    )
    listEvents = []

    async def fnStatusCallback(dictEvent):
        listEvents.append(dictEvent)

    asyncio.new_event_loop().run_until_complete(
        _fnFinalizeRun(
            connection, S_CONTAINER_ID, dictState, 0,
            S_LOG_PATH, [], _fdictRunWorkflow(), S_WORKFLOW_PATH,
            fnStatusCallback, stateWriter=stateWriter,
        )
    )

    dictEvent = listEvents[-1]
    assert dictEvent["sType"] == "completed"
    assert dictEvent["bRunMetadataPersisted"] is False, (
        "every container write failed, yet the terminal event claims "
        "the run's results are durable"
    )
    assert dictState["bRunning"] is True, (
        "the terminal fields were not reverted; a later unacknowledged "
        "drain would persist a terminal state nobody proved durable"
    )


def testTheTerminalStateCarriesWorkflowIdentity():
    """The persisted terminal state names the workflow that ran.

    A container can host several workflows; a terminal (or later
    reconciled) state without identity lets workflow A's failure
    surface on workflow B's dashboard.
    """
    connection = ConnectionModellingFiles({})
    dictState = _fdictStateAfterStepOne()
    listEvents = []

    _fnFinalize(
        connection, dictState, _fdictRunWorkflow(), listEvents,
    )

    listStateWrites = [
        sPath for sPath in connection.dictFiles
        if sPath.endswith("pipeline_state.json")
    ]
    assert listStateWrites, "no terminal pipeline state was persisted"
    dictPersisted = connection.fdictReadJson(listStateWrites[0])
    assert dictPersisted["sWorkflowPath"] == S_WORKFLOW_PATH
    assert dictPersisted["bRunning"] is False
    assert dictPersisted["bRunMetadataPersisted"] is True
