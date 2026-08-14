"""Saving one workflow erases every other workflow's state in the repo.

`state.json` is repo-scoped, and a project repo may hold several
workflows — the marker subsystem namespaces its files by workflow
basename for exactly this reason (`fsWorkflowSlugFromPath`).
`dictStepState` has no such namespace. `ftSplitMergedDict` builds a
fresh document from `fdictBuildEmptyState()` and fills it from the ONE
in-memory workflow being saved, and `fnSaveWorkflowToContainer` writes
that document wholesale.

So an ordinary save of workflow A discards workflow B's verification
results and run statistics — unconditionally, with no run involved and
no directory overlap required. This is a live data-loss defect, not a
consequence of anything in the pipeline work.

Schema v3 fixes it by namespacing each project's state under its
project file's repo-relative path, and by making the save a
read-modify-write that carries every other project's section through
untouched.

**Identity is the path, not an id inside project.json.** Two files in
a directory cannot share a name, so uniqueness is free; an id is
COPIED when a researcher duplicates a project to start a variant, and
two projects claiming one identity is the clobbering this replaces.
The cost is that renaming a project file outside vaibify orphans its
state — which shows unverified and is recovered by re-verifying. That
failure is loud and conservative; the id failure is silent and asserts
something false.

**Legacy state is attributed only when sole occupancy is provable.**
A v2 document carries no owner. In a repo holding several projects it
is not merely unlabelled — it is the residue of whichever project was
saved LAST, since every earlier save destroyed the others — so
attributing it by directory match can report one project's step as
verified on a result another project produced. One project in the repo
means attribution is safe; anything else, including a discovery that
failed or was refused, quarantines: preserved in the document,
attributed to nobody, re-verified by the researcher.
"""

import json

import pytest

from vaibify.gui.stateManager import (
    S_QUARANTINE_LIST_KEY,
    fdictInstallWorkflowSection,
    fdictMigrateStateDocument,
    fnMergeStateIntoWorkflow,
    fnSaveStateToContainer,
    ftSplitMergedDict,
)


def _fdictWorkflow(sDirectory, sIntegrity):
    return {
        "listSteps": [{
            "sStepId": sDirectory.lower(),
            "sName": sDirectory,
            "sDirectory": sDirectory,
            "dictVerification": {"sIntegrity": sIntegrity},
            "dictRunStats": {"fWallClockSeconds": 12.5},
        }],
    }


def testSplittingOneWorkflowCarriesOnlyItsOwnSteps():
    """The mechanism, asserted plainly: the document is rebuilt."""
    _sJsonIgnored, dictStateA = ftSplitMergedDict(
        _fdictWorkflow("Alpha", "passed"),
    )
    _sJsonIgnored, dictStateB = ftSplitMergedDict(
        _fdictWorkflow("Beta", "passed"),
    )
    assert list(dictStateA["dictStepState"]) == ["alpha"]
    assert list(dictStateB["dictStepState"]) == ["beta"]
    assert "alpha" not in dictStateB["dictStepState"], (
        "workflow B's state document has no room for workflow A's "
        "entry, so writing it over the shared file drops A"
    )


_S_KEY_A = ".vaibify/projects/alpha.json"
_S_KEY_B = ".vaibify/projects/beta.json"


def testSavingWorkflowBPreservesWorkflowAsState():
    """A's results must survive an ordinary save of B in the same repo.

    Models the shared document directly: what B installs is what the
    next reader loads, so A's step is resolved against it.
    """
    _sJsonIgnored, dictSectionA = ftSplitMergedDict(
        _fdictWorkflow("Alpha", "passed"),
    )
    dictDocument = fdictInstallWorkflowSection({}, _S_KEY_A, dictSectionA)

    _sJsonIgnored, dictSectionB = ftSplitMergedDict(
        _fdictWorkflow("Beta", "passed"),
    )
    dictDocument = fdictInstallWorkflowSection(
        dictDocument, _S_KEY_B, dictSectionB,
    )

    dictWorkflowAReloaded = _fdictWorkflow("Alpha", "untested")
    fnMergeStateIntoWorkflow(
        dictWorkflowAReloaded, dictDocument, _S_KEY_A,
    )
    assert dictWorkflowAReloaded["listSteps"][0]["dictVerification"][
        "sIntegrity"
    ] == "passed", (
        "workflow A's verification was erased by an ordinary save of "
        "workflow B — no run involved, no directory overlap required"
    )


def testOneWorkflowNeverReadsAnothersResults():
    """Namespacing must also prevent the opposite error.

    Two projects in a repo may legitimately hold steps at the same
    directory — the marker subsystem namespaces for exactly that. B
    must not inherit A's verification just because the directories
    coincide, which a flat directory-keyed document would have done.
    """
    _sJsonIgnored, dictSectionA = ftSplitMergedDict(
        _fdictWorkflow("Shared", "passed"),
    )
    dictDocument = fdictInstallWorkflowSection({}, _S_KEY_A, dictSectionA)

    dictWorkflowB = _fdictWorkflow("Shared", "untested")
    fnMergeStateIntoWorkflow(dictWorkflowB, dictDocument, _S_KEY_B)
    assert dictWorkflowB["listSteps"][0]["dictVerification"][
        "sIntegrity"
    ] == "untested", (
        "workflow B inherited workflow A's verification for a step "
        "sharing a directory"
    )


def testLegacyStateIsAttributedWhenTheRepoHoldsOneProject():
    """A single-project repo keeps its badges through the migration."""
    _sJsonIgnored, dictLegacySection = ftSplitMergedDict(
        _fdictWorkflow("Alpha", "passed"),
    )
    dictLegacyDocument = dict(dictLegacySection)
    dictMigrated = fdictMigrateStateDocument(
        dictLegacyDocument, [_S_KEY_A],
    )
    dictWorkflowA = _fdictWorkflow("Alpha", "untested")
    fnMergeStateIntoWorkflow(dictWorkflowA, dictMigrated, _S_KEY_A)
    assert dictWorkflowA["listSteps"][0]["dictVerification"][
        "sIntegrity"
    ] == "passed"
    assert S_QUARANTINE_LIST_KEY not in dictMigrated


@pytest.mark.parametrize("listKeys", [
    [_S_KEY_A, _S_KEY_B],
    None,
])
def testAmbiguousLegacyStateIsQuarantinedNotGuessed(listKeys):
    """Multi-project and unknown both quarantine, and lose nothing.

    In a repo with several projects the surviving v2 document is the
    residue of whichever project was saved LAST — every earlier save
    destroyed the others — so attributing it by directory match can
    report one project's step as verified on a result a different
    project produced. ``None`` (discovery failed or was refused) is not
    proof of sole occupancy either, so it takes the same branch.
    """
    _sJsonIgnored, dictLegacySection = ftSplitMergedDict(
        _fdictWorkflow("Alpha", "passed"),
    )
    dictMigrated = fdictMigrateStateDocument(
        dict(dictLegacySection), listKeys,
    )
    dictWorkflowA = _fdictWorkflow("Alpha", "untested")
    fnMergeStateIntoWorkflow(dictWorkflowA, dictMigrated, _S_KEY_A)
    assert dictWorkflowA["listSteps"][0]["dictVerification"][
        "sIntegrity"
    ] == "untested", (
        "ambiguous legacy state was attributed to a project that "
        "cannot be shown to own it"
    )
    assert dictMigrated[S_QUARANTINE_LIST_KEY][0]["dictStepState"], (
        "quarantine must PRESERVE the data, not delete it — nothing "
        "here is recoverable from anywhere else"
    )


class _FakeStateDocker:
    """Model the container files fnSaveStateToContainer touches."""

    def __init__(self, dictDocumentOnDisk):
        self.baOnDisk = json.dumps(dictDocumentOnDisk).encode("utf-8")
        self.dictWrites = {}

    def fbaFetchFile(self, _sContainerId, sPath):
        if sPath.endswith(".vaibify/state.json"):
            return self.baOnDisk
        raise FileNotFoundError(sPath)

    def fnWriteFile(self, _sContainerId, sPath, baPayload):
        self.dictWrites[sPath] = baPayload

    def ftResultExecuteCommand(self, _sContainerId, _sCommand):
        return (0, "")


def testTheSAVEPreservesASiblingProjectsSection():
    """Drive the real writer, not the helper it calls.

    The helper-level tests above pass even when the save is reverted to
    a whole-document replacement, because they never go through
    ``fnSaveStateToContainer``. That is the wiring this asserts: the
    save must RE-READ the shared document and install only its own
    section, and it must re-read at save time rather than reuse
    anything loaded earlier, because a sibling project may have
    written since.
    """
    _sJsonIgnored, dictSectionB = ftSplitMergedDict(
        _fdictWorkflow("Beta", "passed"),
    )
    dictOnDisk = fdictInstallWorkflowSection({}, _S_KEY_B, dictSectionB)
    dockerFake = _FakeStateDocker(dictOnDisk)

    _sJsonIgnored, dictSectionA = ftSplitMergedDict(
        _fdictWorkflow("Alpha", "passed"),
    )
    fnSaveStateToContainer(
        dockerFake, "cid", "/workspace/repo/.vaibify/state.json",
        dictSectionA, sWorkflowKey=_S_KEY_A,
    )

    assert dockerFake.dictWrites, "the save wrote nothing at all"
    dictWritten = json.loads(
        list(dockerFake.dictWrites.values())[0].decode("utf-8"),
    )
    dictSections = dictWritten["dictWorkflowState"]
    assert _S_KEY_A in dictSections, "the saving project's own section"
    assert _S_KEY_B in dictSections, (
        "saving project A erased project B's section — the whole "
        "defect, still present at the writer even if the helper is "
        "namespaced"
    )
    assert dictSections[_S_KEY_B]["dictStepState"]["beta"][
        "dictVerification"
    ]["sIntegrity"] == "passed"


def testQuarantinedLegacyStateSurvivesAnOrdinarySave():
    """Quarantine must be DURABLE, not an in-memory decision.

    The migration transforms the loaded dict; the document on disk
    stays v2 until something rewrites it. So the sequence that matters
    is load → ordinary save → reload, not the migration in isolation:
    an earlier version of this fix quarantined in memory and then let
    the next save re-read the v2 document and delete its legacy roots,
    destroying exactly the data the ambiguous branch exists to keep.

    Asserted against the WRITER, because the writer must be safe
    without depending on a loader having run first.
    """
    dictLegacyOnDisk = {
        "iStateSchemaVersion": 2,
        "dictStepState": {
            "Alpha": {"dictVerification": {"sUser": "passed"}},
        },
        "bArchiveTrackingMigrated": True,
    }
    dockerFake = _FakeStateDocker(dictLegacyOnDisk)
    _sJsonIgnored, dictSectionB = ftSplitMergedDict(
        _fdictWorkflow("Beta", "passed"),
    )
    fnSaveStateToContainer(
        dockerFake, "cid", "/workspace/repo/.vaibify/state.json",
        dictSectionB, sWorkflowKey=_S_KEY_B,
    )
    dictWritten = json.loads(
        list(dockerFake.dictWrites.values())[0].decode("utf-8"),
    )
    assert _S_KEY_B in dictWritten["dictWorkflowState"]
    assert S_QUARANTINE_LIST_KEY in dictWritten, (
        "the legacy roots were deleted by an ordinary save; the "
        "quarantine branch preserved nothing"
    )
    assert dictWritten[S_QUARANTINE_LIST_KEY][0]["dictStepState"][
        "Alpha"
    ]["dictVerification"]["sUser"] == "passed"


def testEveryAmbiguousPayloadIsRetained():
    """BOTH rescues must survive, not whichever arrived first.

    The earlier version of this test asserted only that "Original"
    survived — and passed while the implementation silently discarded
    "Later", the second body of data it had gone to the trouble of
    modelling. Asserting one half of a two-sided guarantee is how a
    test certifies the bug it was written to catch.
    """
    dictDocument = {
        "dictWorkflowState": {},
        S_QUARANTINE_LIST_KEY: [
            {"dictStepState": {"Original": {"dictVerification": {}}}},
        ],
        "dictStepState": {"Later": {"dictVerification": {}}},
    }
    dictAfter = fdictInstallWorkflowSection(
        dictDocument, _S_KEY_A, {"dictStepState": {}},
    )
    sAll = json.dumps(dictAfter[S_QUARANTINE_LIST_KEY])
    assert "Original" in sAll, (
        "the earlier quarantine was overwritten and its data lost"
    )
    assert "Later" in sAll, (
        "the NEW legacy payload was popped from the document and then "
        "discarded because a quarantine already existed"
    )


def testWorkflowLevelLegacyStateIsRetainedWithNoSteps():
    """An empty step map must not throw away workflow-level fields.

    Keying the rescue on a non-empty ``dictStepState`` dropped
    ``iProofLevel``, ``dictWorkflowLevelHighWater`` and the tracking
    flag whenever a legacy document happened to carry no per-step
    entries — the workflow-level migration case exactly.
    """
    dictDocument = {
        "dictStepState": {},
        "iProofLevel": 3,
        "bArchiveTrackingMigrated": True,
        "dictWorkflowLevelHighWater": {"L1": "2026-01-01T00:00:00Z"},
    }
    dictAfter = fdictInstallWorkflowSection(
        dictDocument, _S_KEY_A, {"dictStepState": {}},
    )
    assert S_QUARANTINE_LIST_KEY in dictAfter, (
        "workflow-level legacy state was deleted with no rescue"
    )
    dictRecord = dictAfter[S_QUARANTINE_LIST_KEY][0]
    assert dictRecord["iProofLevel"] == 3
    assert dictRecord["bArchiveTrackingMigrated"] is True
    assert dictRecord["dictWorkflowLevelHighWater"] == {
        "L1": "2026-01-01T00:00:00Z",
    }


def testAnEmptyLegacyRootIsNotRecorded():
    """Nothing to lose means no record — quarantine must stay readable."""
    dictAfter = fdictInstallWorkflowSection(
        {"dictStepState": {}}, _S_KEY_A, {"dictStepState": {}},
    )
    assert S_QUARANTINE_LIST_KEY not in dictAfter


def testMigrationIsIdempotent():
    """Re-running the migration must not re-quarantine live state."""
    _sJsonIgnored, dictSectionA = ftSplitMergedDict(
        _fdictWorkflow("Alpha", "passed"),
    )
    dictDocument = fdictInstallWorkflowSection({}, _S_KEY_A, dictSectionA)
    dictAgain = fdictMigrateStateDocument(dictDocument, None)
    dictWorkflowA = _fdictWorkflow("Alpha", "untested")
    fnMergeStateIntoWorkflow(dictWorkflowA, dictAgain, _S_KEY_A)
    assert dictWorkflowA["listSteps"][0]["dictVerification"][
        "sIntegrity"
    ] == "passed"
