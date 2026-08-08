"""Runtime attribution is proven, or the shape is declared unprovable.

Migration plan phase 1b (rule R5). Before any route is migrated,
somebody has to establish that an observation taken where a container
mutation actually happens can be tied to ONE row of
``tests/mutationInventory.json``. If it cannot, the row goes to manual
tracing — never to inference, because a generator that guessed a row's
carrier mode from its module's other rows would launder a guess into
the record.

The five hard shapes live in ``tests/attributionShapes.py`` as REAL
source: the same scanner that builds the inventory produces their rows,
and the stacks walked here are stacks the interpreter actually had. Two
of the demonstrations run through the REAL carriers
(``vaibify/gui/commitCarrier.py``), so the admission mode an observation
records is one a carrier minted rather than one this file declared.

Every worker handed to a carrier here is SYNCHRONOUS. The carrier runs
workers in a thread; an ``async def`` would be called, hand back a
coroutine nobody awaits, and the test would pass having executed
nothing — this suite shipped exactly that once.
"""

import ast
import asyncio
import json
import pathlib
from types import SimpleNamespace

import pytest

from tests import attributionShapes
from tools import mutationAttribution
from vaibify.config import operationJournal
from vaibify.gui import commitCarrier
from vaibify.gui.containerOwnership import OwnerRecord


PATH_REPOSITORY = pathlib.Path(__file__).resolve().parent.parent
PATH_SHAPES = PATH_REPOSITORY / "tests" / "attributionShapes.py"
S_SHAPES_RELATIVE = "attributionShapes.py"

S_CONTAINER_NAME = "attributionproj"
S_CONTAINER_ID = "attributioncid987"


@pytest.fixture(scope="module", autouse=True)
def fnLeaveUsableEventLoopSlotBehind():
    """Restore a usable main-thread event-loop slot after this module."""
    yield
    asyncio.set_event_loop(asyncio.new_event_loop())


@pytest.fixture(autouse=True)
def fnKeepTheOperationJournalOutOfTheHomeDirectory(monkeypatch, tmp_path):
    """The real carriers journal; none of it may touch the researcher."""
    monkeypatch.setattr(
        operationJournal, "_S_JOURNAL_DIRECTORY",
        str(tmp_path / "journal"),
    )


@pytest.fixture(scope="module")
def dictShapesIndex():
    """The attribution index over the synthetic shapes module."""
    return mutationAttribution.fdictBuildAttributionIndex(
        [(PATH_SHAPES, S_SHAPES_RELATIVE)],
    )


@pytest.fixture
def hostPrimitive(dictShapesIndex):
    """A recording primitive stand-in bound to the shapes index."""
    return attributionShapes.RecordingPrimitiveHost(
        dictShapesIndex, S_CONTAINER_ID,
    )


def _fsReadSourceExpressionForFingerprint(sFingerprint):
    """Return the shapes-module expression a fingerprint identifies.

    The INDEPENDENT ORACLE for "which row was I given". It selects by
    reading the source and reporting the expression text, so an
    assertion can name the literal it expects (``alpha.json``) without
    ever asking the matcher which row a frame belongs to.
    """
    sModuleSource = PATH_SHAPES.read_text(encoding="utf-8")
    tupleSourceLines = mutationAttribution._moduleGenerator.ftupleSourceLines(
        sModuleSource,
    )
    treeModule = ast.parse(sModuleSource)
    for nodeReference in ast.walk(treeModule):
        if not isinstance(nodeReference, (ast.Call, ast.Attribute)):
            continue
        if mutationAttribution.fsFingerprintReferenceNode(
            nodeReference, tupleSourceLines,
        ) == sFingerprint:
            return ast.unparse(nodeReference)
    return ""


def _ftBuildOwnedAppState():
    """Return ``(appState, dictLaneTuple)`` for one owned container."""
    recordOwner = OwnerRecord(
        sLeaseId="lease-attribution", fileHandleLock=None,
        sAgentToken="agent-attribution", sContainerId=S_CONTAINER_ID,
        sBrowserSessionId="session-attribution", iOwnerGeneration=1,
    )
    appState = SimpleNamespace(
        dictContainerOwners={S_CONTAINER_NAME: recordOwner},
        dictBrowserSessions={}, dictSessionOwner={},
        dictMutationSupervisors={}, dictDurableTaskRecords={},
        bMutationAdmissionsClosed=False,
    )
    dictLaneTuple = {
        "sLane": "browser", "iOwnerGeneration": 1,
        "sBrowserSessionId": "session-attribution",
        "sLeaseId": "lease-attribution",
        "sContainerName": S_CONTAINER_NAME,
    }
    return appState, dictLaneTuple


# ---------------------------------------------------------------------
# The index is the same population as the checked-in record.
# ---------------------------------------------------------------------

@pytest.mark.falsification
def testTheIndexCoversExactlyTheCheckedInInventoryRows():
    """Attribution indexes the SAME sites the record was reviewed from.

    An index built over a different population would attribute
    observations to rows nobody is migrating, and would silently miss
    the ones somebody is. Compared against the checked-in inventory
    rather than against a fresh scan of my own, because agreeing with
    myself proves nothing.

    Kills: truncating the module list in
    fdictBuildAttributionIndexForPackage.
    """
    dictIndex = mutationAttribution.fdictBuildAttributionIndexForPackage()
    setIndexedKeys = {
        mutationAttribution.fsBuildInventoryRowKey(dictRow)
        for listRows in dictIndex["dictRowsByFrame"].values()
        for dictRow in listRows
    }
    dictInventory = json.loads(
        (PATH_REPOSITORY / "tests" / "mutationInventory.json").read_text(
            encoding="utf-8",
        ),
    )
    setRecordedKeys = {
        mutationAttribution.fsBuildInventoryRowKey(dictRow)
        for dictRow in dictInventory["listRows"]
    }
    assert setIndexedKeys == setRecordedKeys, (
        "the attribution index and the reviewed record describe "
        "different sets of call sites: "
        f"missing {sorted(setRecordedKeys - setIndexedKeys)[:5]}, "
        f"extra {sorted(setIndexedKeys - setRecordedKeys)[:5]}"
    )


# ---------------------------------------------------------------------
# Shape 0 — the baseline a direct call must satisfy.
# ---------------------------------------------------------------------

def testADirectCallIsAttributedToItsOwnRow(hostPrimitive):
    """The simplest shape resolves by fingerprint, uniquely."""
    attributionShapes.fnWriteThroughDirectCall(
        hostPrimitive, S_CONTAINER_ID,
    )
    assert len(hostPrimitive.listObservations) == 1
    dictObservation = hostPrimitive.listObservations[0]
    assert dictObservation["sAttributionEvidence"] == (
        mutationAttribution.S_EVIDENCE_FINGERPRINT_EXACT
    )
    assert "direct.json" in _fsReadSourceExpressionForFingerprint(
        dictObservation["sInventoryRowFingerprint"],
    )


# ---------------------------------------------------------------------
# Shape 2 — a primitive reached through a local alias.
# ---------------------------------------------------------------------

@pytest.mark.falsification
def testAnAliasedPrimitiveIsAttributedToItsBindingRow(hostPrimitive):
    """The row is recorded at the binding; the frame reports the call.

    ``fnWriterBound = hostPrimitive.fnWriteFile`` is a passed-callable
    row whose fingerprint covers the BINDING expression, while the
    stack frame reports the line of ``fnWriterBound(...)``. Without the
    single-hop alias resolution the two never meet and the row is
    unattributable, so this is the shape that proves the hop works.

    Kills: disabling the alias acceptance in
    _fdictMatchThroughLocalAlias.
    """
    attributionShapes.fnWriteThroughLocalAlias(
        hostPrimitive, S_CONTAINER_ID,
    )
    dictObservation = hostPrimitive.listObservations[0]
    assert dictObservation["sAttributionEvidence"] == (
        mutationAttribution.S_EVIDENCE_ALIAS_SINGLE_HOP
    ), dictObservation["sUnattributedReason"]
    assert _fsReadSourceExpressionForFingerprint(
        dictObservation["sInventoryRowFingerprint"],
    ) == "hostPrimitive.fnWriteFile"
    assert dictObservation["sInventoryRowKey"].endswith(
        "fnWriteThroughLocalAlias|fnWriteFile|passed-callable|0"
    ), dictObservation["sInventoryRowKey"]


# ---------------------------------------------------------------------
# Shape 3 — two calls to one primitive in one function.
# ---------------------------------------------------------------------

@pytest.mark.falsification
def testTwoCallsInOneFunctionAttributeToDistinctRows(hostPrimitive):
    """One frame, two rows, and the observation tells them apart.

    Attributing by ``(file, function)`` alone would hand both calls the
    same row — or refuse both — and the reviewer would classify one
    site from the other's behaviour. Asserted on the ROW IDENTITY and
    on the source each identity names, not merely on "two keys came
    back different".

    Kills: matching candidate rows by frame alone, without the
    fingerprint filter, in _fdictMatchFrameToRow.
    """
    attributionShapes.fnWriteTwiceInOneFunction(
        hostPrimitive, S_CONTAINER_ID, False,
    )
    attributionShapes.fnWriteTwiceInOneFunction(
        hostPrimitive, S_CONTAINER_ID, True,
    )
    dictFirst, dictSecond = hostPrimitive.listObservations
    assert dictFirst["sInventoryRowKey"] != dictSecond["sInventoryRowKey"]
    assert "alpha.json" in _fsReadSourceExpressionForFingerprint(
        dictFirst["sInventoryRowFingerprint"],
    )
    assert "beta.json" in _fsReadSourceExpressionForFingerprint(
        dictSecond["sInventoryRowFingerprint"],
    )
    assert dictFirst["sAttributionEvidence"] == (
        mutationAttribution.S_EVIDENCE_FINGERPRINT_EXACT
    )


@pytest.mark.falsification
def testTwoIdenticalExpressionsRefuseAttributionInsteadOfGuessing(
    hostPrimitive,
):
    """Character-identical references are two rows and one fingerprint.

    The inventory separates them only by ``iOrdinal``, which is
    assigned by sorting on the fingerprint they share, so a stack frame
    cannot recover which one ran. Refusing is the honest answer and it
    routes both rows to manual tracing; picking the first would file a
    coin toss as a classification.

    No row in the package has this shape today, which is why it is
    demonstrated synthetically rather than asserted away.

    Kills: accepting the first candidate when several share the
    observed fingerprint, in _fdictMatchFrameToRow.
    """
    attributionShapes.fnWriteIdenticalExpressionTwice(
        hostPrimitive, S_CONTAINER_ID, False,
    )
    dictObservation = hostPrimitive.listObservations[0]
    assert dictObservation["sAttributionEvidence"] == (
        mutationAttribution.S_EVIDENCE_UNATTRIBUTED
    )
    assert dictObservation["sInventoryRowKey"] == ""
    assert "share the source expression" in (
        dictObservation["sUnattributedReason"]
    )


# ---------------------------------------------------------------------
# Shape 4 — one shared helper reached under two carrier modes.
# ---------------------------------------------------------------------

@pytest.mark.falsification
def testASharedHelperUnderTwoCarrierModesKeepsBothModes(hostPrimitive):
    """One row, two modes, and the merge must keep both.

    Driven through the REAL carriers: mode (a)
    ``fdictCommitSynchronousMutation`` and mode (b)
    ``fdictRunLockHeldMutation`` with a SYNCHRONOUS worker. The modes
    recorded are the ones the carrier minted into the admission
    contextvar, read back at the effect.

    A merge that overwrote would make the record depend on which
    observation arrived last — which is how a helper reachable from two
    lanes ends up classified for one of them.

    Kills: replacing the carrier-mode union with an assignment in
    fdictMergeObservationsByRow.
    """
    appState, dictLaneTuple = _ftBuildOwnedAppState()

    hostPrimitive.dictInvocationContext = {
        "sOperationKind": "file-write", "sExecutionLane": "http",
        "sCarrierInvocation": mutationAttribution
        .fsDescribeCarrierInvocation(),
    }
    commitCarrier.fdictCommitSynchronousMutation(
        appState, S_CONTAINER_NAME, S_CONTAINER_ID, dictLaneTuple,
        "file-write", "/workspace/shared.json",
        lambda: attributionShapes.fnWriteThroughSharedHelper(
            hostPrimitive, S_CONTAINER_ID, "/workspace/shared.json",
        ),
        {"sExpectedSha256": "aa", "sPriorSha256": "bb"},
    )

    async def _fnDriveLockHeld():
        hostPrimitive.dictInvocationContext = {
            "sOperationKind": "file-write", "sExecutionLane": "websocket",
            "sCarrierInvocation": mutationAttribution
            .fsDescribeCarrierInvocation(),
        }
        return await commitCarrier.fdictRunLockHeldMutation(
            appState, S_CONTAINER_NAME, S_CONTAINER_ID, dictLaneTuple,
            "file-write", "/workspace/shared.json",
            lambda supervisor: attributionShapes.fnWriteThroughSharedHelper(
                hostPrimitive, S_CONTAINER_ID, "/workspace/shared.json",
            ),
        )

    dictOutcome = asyncio.run(_fnDriveLockHeld())
    assert dictOutcome["bCommitted"] is True

    assert len(hostPrimitive.listObservations) == 2, (
        "a carrier worker did not run its effect at all"
    )
    dictSynchronous, dictLockHeld = hostPrimitive.listObservations
    assert dictSynchronous["sObservedAdmissionMode"] == "synchronousCommit"
    assert dictLockHeld["sObservedAdmissionMode"] == "lockHeldAsync"
    assert dictSynchronous["sInventoryRowKey"] == (
        dictLockHeld["sInventoryRowKey"]
    ), "the shared helper resolved to two different rows"

    dictMerged = mutationAttribution.fdictMergeObservationsByRow(
        hostPrimitive.listObservations,
    )
    dictEntry = dictMerged[dictSynchronous["sInventoryRowKey"]]
    assert dictEntry["setCarrierModes"] == {"synchronous", "lock-held"}
    assert dictEntry["setExecutionLanes"] == {"http", "websocket"}


# ---------------------------------------------------------------------
# Shape 5 — work continuing in a context copied into a background task.
# ---------------------------------------------------------------------

def testWorkInACopiedContextIsAttributedAndKeepsItsCarrierMode(
    hostPrimitive,
):
    """A mode-(c) task's own frames are on its own stack, so it resolves.

    The durable admission is activated by the carrier immediately
    before ``create_task``, which COPIES the context, so the effect
    inside the task reads the mode the launch minted. Asserted on the
    effect having happened, not on the launch having returned.
    """
    appState, dictLaneTuple = _ftBuildOwnedAppState()

    async def _fnDrive():
        return await commitCarrier.fdictLaunchDurableTask(
            appState, S_CONTAINER_NAME, S_CONTAINER_ID, dictLaneTuple,
            lambda: asyncio.get_running_loop().create_task(
                attributionShapes.fnWriteInsideBackgroundTask(
                    hostPrimitive, S_CONTAINER_ID,
                ),
            ),
        )

    async def _fnDriveAndAwait():
        dictLaunch = await _fnDrive()
        assert dictLaunch["bLaunched"] is True
        return await dictLaunch["taskAsync"]

    assert asyncio.run(_fnDriveAndAwait()) == "backgroundWorkFinished"
    assert len(hostPrimitive.listObservations) == 1, (
        "the background task never reached its effect"
    )
    dictObservation = hostPrimitive.listObservations[0]
    assert dictObservation["sObservedAdmissionMode"] == "durableTask"
    assert dictObservation["sAttributionEvidence"] == (
        mutationAttribution.S_EVIDENCE_FINGERPRINT_EXACT
    )
    assert "background.json" in _fsReadSourceExpressionForFingerprint(
        dictObservation["sInventoryRowFingerprint"],
    )


# ---------------------------------------------------------------------
# Shape 1 — a bound primitive passed into an executor.
# ---------------------------------------------------------------------

@pytest.mark.falsification
def testAPrimitivePassedIntoAThreadKeepsItsModeButLosesItsRow(
    hostPrimitive,
):
    """The declared-unattributable shape, demonstrated both ways.

    Inside the worker thread the frame above the primitive is executor
    infrastructure; the ``asyncio.to_thread(...)`` expression that IS
    the row never appears on that stack, so no re-derivation can reach
    it. Contextvars do propagate, so the carrier's mode survives — and
    recording the mode while admitting the row is lost is the whole
    distinction between a routed observation and a guess.

    Kills: making the refusal report itself as an exact attribution in
    _fdictRefuseAttribution.
    """
    appState, dictLaneTuple = _ftBuildOwnedAppState()

    async def _fnDrive():
        dictLaunch = await commitCarrier.fdictLaunchDurableTask(
            appState, S_CONTAINER_NAME, S_CONTAINER_ID, dictLaneTuple,
            lambda: asyncio.get_running_loop().create_task(
                attributionShapes.fnWriteThroughExecutorThread(
                    hostPrimitive, S_CONTAINER_ID,
                ),
            ),
        )
        return await dictLaunch["taskAsync"]

    assert asyncio.run(_fnDrive()) == "executorWorkFinished"
    assert len(hostPrimitive.listObservations) == 1, (
        "the executor worker never reached its effect"
    )
    dictObservation = hostPrimitive.listObservations[0]
    assert dictObservation["sObservedAdmissionMode"] == "durableTask", (
        "the carrier's admission did not survive into the worker thread"
    )
    assert dictObservation["sAttributionEvidence"] == (
        mutationAttribution.S_EVIDENCE_UNATTRIBUTED
    )
    assert dictObservation["sInventoryRowKey"] == ""
    assert "executor" in dictObservation["sUnattributedReason"]


# ---------------------------------------------------------------------
# The routing an unattributed observation is entitled to, and no more.
# ---------------------------------------------------------------------

@pytest.mark.falsification
def testAnUnobservedRowIsRoutedToManualTracingBesideObservedSiblings(
    hostPrimitive, dictShapesIndex,
):
    """A row's neighbours say nothing about the row.

    The shapes module holds several rows for the SAME primitive in the
    same file. Running one of them must leave every other on the
    manual-tracing list: "the suite never reached it" is a reason to
    trace by hand, not a classification, and inferring from a sibling
    is exactly the laundering R5 forbids.

    Kills: treating a row as attributed because some other row for the
    same primitive was, in flistSelectRowsForManualTracing.
    """
    attributionShapes.fnWriteThroughDirectCall(
        hostPrimitive, S_CONTAINER_ID,
    )
    listRows = mutationAttribution.flistScanModuleForInventoryRows(
        PATH_SHAPES, S_SHAPES_RELATIVE,
    )
    listPending = mutationAttribution.flistSelectRowsForManualTracing(
        listRows, hostPrimitive.listObservations,
    )
    setPendingKeys = {
        dictPending["sInventoryRowKey"] for dictPending in listPending
    }
    assert len(listPending) == len(listRows) - 1, (
        "running one site excused more than one row from review"
    )
    assert hostPrimitive.listObservations[0][
        "sInventoryRowKey"
    ] not in setPendingKeys
    assert any(
        "fnWriteThroughSharedHelper" in sKey for sKey in setPendingKeys
    ), "an unreached sibling row was quietly excused"


# ---------------------------------------------------------------------
# The artifact.
# ---------------------------------------------------------------------

@pytest.mark.falsification
def testTheObservationArtifactCarriesEveryFactR5Names(
    hostPrimitive, tmp_path,
):
    """Every record answers all six questions R5 asks of an observation.

    The expected field list is written here, from the plan, rather than
    read from the module under test — a schema check that imports its
    own expectation passes for a module that dropped a field.

    Kills: dropping the carrier invocation identity from
    fdictRecordMutationObservation.
    """
    hostPrimitive.dictInvocationContext = {
        "sOperationKind": "file-write", "sExecutionLane": "http",
        "sCarrierInvocation": (
            mutationAttribution.fsDescribeCarrierInvocation()
        ),
        "sEntryPointDeclaration": "mode-a-synchronous",
    }
    attributionShapes.fnWriteThroughDirectCall(
        hostPrimitive, S_CONTAINER_ID,
    )
    tupleRequired = (
        "sInventoryRowKey", "sInventoryRowFingerprint",
        "sEntryPointDeclaration", "sCarrierInvocation",
        "sObservedAdmissionMode", "sOperationKind", "sTarget",
        "sExecutionLane", "sAttributionEvidence", "sUnattributedReason",
        "sPrimitive",
    )
    dictObservation = hostPrimitive.listObservations[0]
    assert set(dictObservation) == set(tupleRequired), (
        f"missing {sorted(set(tupleRequired) - set(dictObservation))}, "
        f"unexpected {sorted(set(dictObservation) - set(tupleRequired))}"
    )
    assert dictObservation["sCarrierInvocation"].startswith(
        "testMutationAttribution.py:"
    )
    assert dictObservation["sEntryPointDeclaration"] == "mode-a-synchronous"
    assert dictObservation["sTarget"] == "/workspace/direct.json"

    pathArtifact = tmp_path / "mutationObservations.json"
    mutationAttribution.fnWriteObservationArtifact(
        hostPrimitive.listObservations, pathArtifact,
    )
    dictArtifact = json.loads(pathArtifact.read_text(encoding="utf-8"))
    assert dictArtifact["iObservationCount"] == 1
    assert dictArtifact["listObservations"][0] == dictObservation
