"""Moving a Zenodo token out of the container can neither lose it nor activate it.

The migration (``vaibify/gui/zenodoTokenMigration.py``) reads a
container slot, stages it in a PENDING host slot with a verified
read-back, deletes the container copy only once the journal records the
stage, validates on the host, and leaves the token pending until the
researcher decides. Every step re-derives its state from the slots, so a
crash anywhere is finished by the next pass. These tests drive the full
path, one row per entry of the plan's crash table, the outage, the
conflict, the failed read-back, the decline, and a hub restart in the
middle -- each against a fake container source and the suite's hermetic
host keyring, with the project identity resolved from a container id
that is NOT the project name.

The oracle for every expected value is the plan's table, not the code.
"""

import os

import pytest

from vaibify.config import zenodoMigrationJournal as journal
from vaibify.config import zenodoTokenSlots as slots
from vaibify.gui import zenodoTokenAccess, zenodoTokenMigration
from vaibify.gui.zenodoTokenMigration import (
    S_DECISION_CONFIRM,
    S_DECISION_DECLINE,
    S_DECISION_KEEP_HOST,
    S_DECISION_STAGE_PENDING,
    fdictDecideMigratedToken,
    fdictDescribeMigration,
    fdictRunMigrationPass,
)
from vaibify.reproducibility import zenodoClient

S_UUID = "c" * 32
S_CONTAINER_ID = "cid-7a2b-not-the-name"
S_SANDBOX_SLOT = "zenodo_token_sandbox"
S_PRODUCTION_SLOT = "zenodo_token_production"
S_LEGACY_SLOT = "zenodo_token"
S_TOKEN = "the-container-token"


class _FakeContainerSource:
    """A container keyring with the slots the test puts in it."""

    bRemovesSource = True

    def __init__(self, dictSlots=None):
        self.dictSlots = dict(dictSlots or {})
        self.listDeleted = []
        self.listRead = []
        self.bUnreadable = False
        self.iDeleteFailuresLeft = 0

    def fsRead(self, sSlot):
        self.listRead.append(sSlot)
        if self.bUnreadable:
            raise LookupError("Could not read the credential slot")
        return self.dictSlots.get(sSlot, "")

    def fnDelete(self, sSlot):
        if self.iDeleteFailuresLeft:
            self.iDeleteFailuresLeft -= 1
            raise RuntimeError("Keyring deletion failed: daemon unreachable")
        self.listDeleted.append(sSlot)
        self.dictSlots.pop(sSlot, None)


class _FakeProbe:
    """Answer each (service, token) probe the way the test says."""

    def __init__(self):
        self.dictOutcomes = {}
        self.listAsked = []

    def fdictProbe(self, sService, sToken):
        self.listAsked.append((sService, sToken))
        sOutcome = self.dictOutcomes.get(
            (sService, sToken), zenodoClient.S_PROBE_ACCEPTED,
        )
        return {
            "sOutcome": sOutcome,
            "sDetail": "" if sOutcome == zenodoClient.S_PROBE_ACCEPTED else "x",
            "listDepositTitles": ["A deposit"] if sOutcome == "accepted" else [],
        }


@pytest.fixture
def tMigration(tmp_path, monkeypatch, fixtureHermeticKeyring):
    """Return (source, probe, keyringFake) wired into the migration."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    os.makedirs(str(tmp_path / "home"))
    source = _FakeContainerSource()
    probe = _FakeProbe()
    monkeypatch.setattr(
        zenodoTokenAccess, "fsProjectUuidForContainer",
        lambda connectionDocker, sContainerId: (
            S_UUID if sContainerId == S_CONTAINER_ID else ""
        ),
    )
    monkeypatch.setattr(
        zenodoTokenMigration, "ftSourceFor",
        lambda connectionDocker, sContainerId: source,
    )
    monkeypatch.setattr(
        zenodoTokenMigration.zenodoClient, "fdictProbeToken", probe.fdictProbe,
    )
    return source, probe, fixtureHermeticKeyring


def _fsPending(sSlot=S_SANDBOX_SLOT):
    return slots.fsReadSlot(slots.fsPendingSlot(sSlot, S_UUID))


def _fsActive(sInstance="sandbox"):
    return slots.fsReadActiveToken(sInstance, S_UUID)


def _fsState(sSlot=S_SANDBOX_SLOT):
    return journal.fdictSlotRecord(S_UUID, sSlot).get("sState")


def _fnRun():
    return fdictRunMigrationPass(None, S_CONTAINER_ID)


# ---------------------------------------------------------------------------
# The full path
# ---------------------------------------------------------------------------


def testTheFullPathEndsPendingAndActiveOnlyAfterConfirmation(tMigration):
    source, probe, _ = tMigration
    source.dictSlots[S_SANDBOX_SLOT] = S_TOKEN
    dictDescribed = _fnRun()
    assert _fsPending() == S_TOKEN
    assert source.dictSlots == {} and source.listDeleted == [S_SANDBOX_SLOT]
    assert _fsState() == journal.S_STATE_REMOVED
    assert _fsActive() == "", "nothing becomes active without step 5"
    dictSlot = dictDescribed["listSlots"][0]
    assert dictSlot["dictValidation"]["sOutcome"] == "accepted"
    assert dictSlot["sProposedInstance"] == "sandbox"
    assert dictSlot["bAwaitingDecision"] and dictDescribed["bAwaitingDecision"]
    assert dictDescribed["bRotationAdvised"] is True
    assert dictSlot["listDepositTitles"] == ["A deposit"]

    fdictDecideMigratedToken(None, S_CONTAINER_ID, S_SANDBOX_SLOT, S_DECISION_CONFIRM)
    assert _fsActive() == S_TOKEN
    assert _fsPending() == ""
    assert _fsState() == journal.S_STATE_ACTIVATED


@pytest.mark.falsification
def testAPendingTokenIsNeverSpentByATokenBearingAction(tMigration):
    """After a hub restart mid-migration the token is pending, and pending is not active.

    A second pass in a fresh process finds the same keyring and journal:
    the token is still pending, still requires confirmation, and a
    token-bearing action reading the ACTIVE slot gets nothing.

    Kills: the active-slot reader falling back to the pending slot.
    """
    source, _, _ = tMigration
    source.dictSlots[S_SANDBOX_SLOT] = S_TOKEN
    _fnRun()
    dictAfterRestart = _fnRun()
    assert _fsPending() == S_TOKEN
    assert dictAfterRestart["bAwaitingDecision"] is True
    assert zenodoTokenAccess.fsActiveZenodoToken(None, S_CONTAINER_ID, "sandbox") == ""
    with pytest.raises(zenodoTokenAccess.ZenodoTokenUnavailable, match="awaiting"):
        zenodoTokenAccess.fsRequireActiveZenodoToken(None, S_CONTAINER_ID, "sandbox")


def testAbsentSlotsAreRecordedAbsentAndNeverReadAgain(tMigration):
    source, _, _ = tMigration
    _fnRun()
    assert all(
        _fsState(sSlot) == journal.S_STATE_ABSENT
        for sSlot in slots.LIST_CONTAINER_ZENODO_SLOTS
    )
    source.dictSlots[S_SANDBOX_SLOT] = "planted-by-the-agent-later"
    source.listRead.clear()
    _fnRun()
    assert source.listRead == [], "a finished slot is not consulted again"
    assert _fsPending() == ""


# ---------------------------------------------------------------------------
# The crash table, one row each
# ---------------------------------------------------------------------------


def testCrashAfterTheReadRedoesTheStage(tMigration):
    """Row 1: the container slot is present and nothing is staged."""
    source, _, _ = tMigration
    source.dictSlots[S_SANDBOX_SLOT] = S_TOKEN
    journal.fnRecordRotationAdvice(S_UUID)  # the journal file exists, empty of slots
    _fnRun()
    assert _fsPending() == S_TOKEN and _fsState() == journal.S_STATE_REMOVED


def testCrashAfterThePendingWriteRecordsStagedWithoutRewriting(tMigration):
    """Row 2: pending equals the container value; step 2 records staged."""
    source, _, keyringFake = tMigration
    source.dictSlots[S_SANDBOX_SLOT] = S_TOKEN
    slots.fnStoreSlotVerified(slots.fsPendingSlot(S_SANDBOX_SLOT, S_UUID), S_TOKEN)
    listWrites = []
    fnRealSet = keyringFake.set_password

    def fnRecordSet(sService, sName, sValue):
        listWrites.append(sName)
        fnRealSet(sService, sName, sValue)

    keyringFake.set_password = fnRecordSet
    _fnRun()
    assert slots.fsPendingSlot(S_SANDBOX_SLOT, S_UUID) not in listWrites
    assert _fsState() == journal.S_STATE_REMOVED
    assert source.listDeleted == [S_SANDBOX_SLOT]


def testCrashAfterTheDeleteRecordsRemoved(tMigration):
    """Row 3: the container slot is absent and staged is recorded."""
    source, probe, _ = tMigration
    slots.fnStoreSlotVerified(slots.fsPendingSlot(S_SANDBOX_SLOT, S_UUID), S_TOKEN)
    journal.fnRecordSlotState(S_UUID, S_SANDBOX_SLOT, journal.S_STATE_STAGED)
    _fnRun()
    assert _fsState() == journal.S_STATE_REMOVED
    assert probe.listAsked[0] == ("sandbox", S_TOKEN), "validation follows"
    assert _fsPending() == S_TOKEN


def testCrashAfterTheValidationValidatesAgain(tMigration):
    """Row 4: pending present, outcome missing."""
    source, probe, _ = tMigration
    slots.fnStoreSlotVerified(slots.fsPendingSlot(S_SANDBOX_SLOT, S_UUID), S_TOKEN)
    journal.fnRecordSlotState(S_UUID, S_SANDBOX_SLOT, journal.S_STATE_REMOVED)
    _fnRun()
    # The validating probe, then the describe's title fetch for the
    # researcher's eyes; the describe never records anything.
    assert probe.listAsked[0] == ("sandbox", S_TOKEN)
    assert journal.fdictSlotRecord(S_UUID, S_SANDBOX_SLOT)["dictValidation"]["sOutcome"] == (
        "accepted"
    )
    assert _fsActive() == ""


def testCrashAfterTheActiveWriteFinishesTheConfirmation(tMigration):
    """Row 5: active equals pending; finish by deleting pending."""
    source, probe, _ = tMigration
    slots.fnStoreSlotVerified(slots.fsPendingSlot(S_SANDBOX_SLOT, S_UUID), S_TOKEN)
    slots.fnStoreActiveToken("sandbox", S_UUID, S_TOKEN)
    journal.fnRecordSlotState(S_UUID, S_SANDBOX_SLOT, journal.S_STATE_REMOVED)
    _fnRun()
    assert _fsPending() == ""
    assert _fsActive() == S_TOKEN
    assert _fsState() == journal.S_STATE_ACTIVATED
    assert probe.listAsked == []


# ---------------------------------------------------------------------------
# Undetermined, conflicts, failed read-back, decline
# ---------------------------------------------------------------------------


@pytest.mark.falsification
def testAnUndeterminedValidationIsNeverRejected(tMigration):
    """An outage leaves the token pending, removed from the container, retried.

    Kills: classifying a network failure or a 5xx as a rejection.
    """
    source, probe, _ = tMigration
    source.dictSlots[S_SANDBOX_SLOT] = S_TOKEN
    probe.dictOutcomes[("sandbox", S_TOKEN)] = zenodoClient.S_PROBE_UNDETERMINED
    _fnRun()
    dictValidation = journal.fdictSlotRecord(S_UUID, S_SANDBOX_SLOT)["dictValidation"]
    assert dictValidation["sOutcome"] == zenodoClient.S_PROBE_UNDETERMINED
    assert _fsPending() == S_TOKEN, "never discarded"
    assert _fsActive() == "", "never activated"
    assert source.dictSlots == {}, "the stage was verified, so the copy left"
    assert len(probe.listAsked) == 1, "an undetermined token has no titles to show"
    probe.dictOutcomes.clear()
    _fnRun()
    assert len(probe.listAsked) >= 2, "retried at the next connect"
    assert journal.fdictSlotRecord(S_UUID, S_SANDBOX_SLOT)["dictValidation"]["sOutcome"] == (
        "accepted"
    )


@pytest.mark.falsification
def testAConflictingHostValueIsNeverOverwritten(tMigration):
    """A different active token on the host stops the slot with every copy kept.

    Kills: staging over the host's value instead of recording the conflict.
    """
    source, _, _ = tMigration
    slots.fnStoreActiveToken("sandbox", S_UUID, "the-host-token")
    source.dictSlots[S_SANDBOX_SLOT] = "the-container-token"
    dictDescribed = _fnRun()
    assert _fsState() == journal.S_STATE_CONFLICT
    assert _fsPending() == ""
    assert _fsActive() == "the-host-token"
    assert source.dictSlots == {S_SANDBOX_SLOT: "the-container-token"}
    assert dictDescribed["listSlots"][0]["bAwaitingDecision"] is True


def testKeepHostDiscardsTheContainerCopyAfterAnExplicitChoice(tMigration):
    source, _, _ = tMigration
    slots.fnStoreActiveToken("sandbox", S_UUID, "the-host-token")
    source.dictSlots[S_SANDBOX_SLOT] = "the-container-token"
    _fnRun()
    fdictDecideMigratedToken(None, S_CONTAINER_ID, S_SANDBOX_SLOT, S_DECISION_KEEP_HOST)
    assert source.dictSlots == {}
    assert _fsState() == journal.S_STATE_DECLINED
    assert _fsActive() == "the-host-token"
    assert _fsPending() == ""


def testStagePendingResolvesAConflictWithoutTouchingTheActiveSlot(tMigration):
    source, _, _ = tMigration
    slots.fnStoreActiveToken("sandbox", S_UUID, "the-host-token")
    source.dictSlots[S_SANDBOX_SLOT] = "the-container-token"
    _fnRun()
    fdictDecideMigratedToken(
        None, S_CONTAINER_ID, S_SANDBOX_SLOT, S_DECISION_STAGE_PENDING,
    )
    assert _fsPending() == "the-container-token"
    assert _fsActive() == "the-host-token"
    assert source.dictSlots == {}
    assert _fsState() == journal.S_STATE_REMOVED
    with pytest.raises(ValueError, match="bReplaceActive"):
        fdictDecideMigratedToken(None, S_CONTAINER_ID, S_SANDBOX_SLOT, S_DECISION_CONFIRM)
    fdictDecideMigratedToken(
        None, S_CONTAINER_ID, S_SANDBOX_SLOT, S_DECISION_CONFIRM, bReplaceActive=True,
    )
    assert _fsActive() == "the-container-token"


def testAValueAlreadyActiveOnTheHostIsRemovedAndRecordedActivated(tMigration):
    source, probe, _ = tMigration
    slots.fnStoreActiveToken("sandbox", S_UUID, S_TOKEN)
    source.dictSlots[S_SANDBOX_SLOT] = S_TOKEN
    _fnRun()
    assert source.dictSlots == {}
    assert _fsPending() == ""
    assert _fsState() == journal.S_STATE_ACTIVATED
    assert probe.listAsked == []


@pytest.mark.falsification
def testTheContainerCopyIsDeletedOnlyAfterTheStageIsRecorded(tMigration):
    """A host keyring that drops the write leaves the container copy in place.

    Kills: deleting the container copy without the journal recording
    ``staged`` -- the one way the move can lose a token.
    """
    source, _, keyringFake = tMigration
    source.dictSlots[S_SANDBOX_SLOT] = S_TOKEN

    def fnDropPendingWrites(sService, sName, sValue):
        if not sName.endswith(":pending"):
            keyringFake.dictStore[(sService, sName)] = sValue

    keyringFake.set_password = fnDropPendingWrites
    _fnRun()
    assert _fsState() == journal.S_STATE_STAGE_FAILED
    assert source.dictSlots == {S_SANDBOX_SLOT: S_TOKEN}
    assert source.listDeleted == []
    assert _fsPending() == ""


def testADeleteThatFailsIsRetriedWithTheStageKept(tMigration):
    source, _, _ = tMigration
    source.dictSlots[S_SANDBOX_SLOT] = S_TOKEN
    source.iDeleteFailuresLeft = 1
    _fnRun()
    assert _fsState() == journal.S_STATE_STAGED
    assert _fsPending() == S_TOKEN
    assert source.dictSlots == {S_SANDBOX_SLOT: S_TOKEN}
    _fnRun()
    assert _fsState() == journal.S_STATE_REMOVED
    assert source.dictSlots == {}


def testAnUnreadableContainerKeyringStagesNothingAndDeletesNothing(tMigration):
    source, _, _ = tMigration
    source.dictSlots[S_SANDBOX_SLOT] = S_TOKEN
    source.bUnreadable = True
    _fnRun()
    assert _fsState() == journal.S_STATE_STAGE_FAILED
    assert "could not be read" in journal.fdictSlotRecord(S_UUID, S_SANDBOX_SLOT)["sReason"]
    assert source.listDeleted == []


@pytest.mark.falsification
def testDecliningDeletesOnlyThePendingSlot(tMigration):
    """Kills: a decline that also clears the project's active slots."""
    source, _, _ = tMigration
    slots.fnStoreActiveToken("production", S_UUID, "keep-me")
    source.dictSlots[S_SANDBOX_SLOT] = S_TOKEN
    _fnRun()
    fdictDecideMigratedToken(None, S_CONTAINER_ID, S_SANDBOX_SLOT, S_DECISION_DECLINE)
    assert _fsPending() == ""
    assert _fsState() == journal.S_STATE_DECLINED
    assert _fsActive("production") == "keep-me"


def testADecisionNeedsSomethingPendingAndAKnownDecision(tMigration):
    with pytest.raises(ValueError, match="nothing is pending"):
        fdictDecideMigratedToken(None, S_CONTAINER_ID, S_SANDBOX_SLOT, S_DECISION_CONFIRM)
    with pytest.raises(ValueError, match="not a decision"):
        fdictDecideMigratedToken(None, S_CONTAINER_ID, S_SANDBOX_SLOT, "shrug")
    with pytest.raises(ValueError, match="not a container Zenodo slot"):
        fdictDecideMigratedToken(None, S_CONTAINER_ID, "overleaf_token", S_DECISION_DECLINE)
    with pytest.raises(ValueError, match="not a registered project"):
        fdictDecideMigratedToken(None, "cid-unknown", S_SANDBOX_SLOT, S_DECISION_DECLINE)


# ---------------------------------------------------------------------------
# The legacy slot, the backup slots, the host project
# ---------------------------------------------------------------------------


def testTheLegacySlotProposesTheInstanceThatAcceptedIt(tMigration):
    source, probe, _ = tMigration
    source.dictSlots[S_LEGACY_SLOT] = "old-style"
    probe.dictOutcomes[("sandbox", "old-style")] = zenodoClient.S_PROBE_REJECTED
    dictDescribed = _fnRun()
    assert _fsPending(S_LEGACY_SLOT) == "old-style"
    dictSlot = next(d for d in dictDescribed["listSlots"] if d["sSlot"] == S_LEGACY_SLOT)
    assert dictSlot["sProposedInstance"] == "production"
    assert probe.listAsked[:2] == [("sandbox", "old-style"), ("zenodo", "old-style")]
    fdictDecideMigratedToken(None, S_CONTAINER_ID, S_LEGACY_SLOT, S_DECISION_CONFIRM)
    assert _fsActive("production") == "old-style"
    assert _fsActive("sandbox") == ""


def testTheLegacySlotRejectedEverywhereStaysPendingUntilTheResearcherSays(tMigration):
    source, probe, _ = tMigration
    source.dictSlots[S_LEGACY_SLOT] = "old-style"
    probe.dictOutcomes[("sandbox", "old-style")] = zenodoClient.S_PROBE_REJECTED
    probe.dictOutcomes[("zenodo", "old-style")] = zenodoClient.S_PROBE_REJECTED
    _fnRun()
    assert _fsPending(S_LEGACY_SLOT) == "old-style"
    with pytest.raises(ValueError, match="which instance"):
        fdictDecideMigratedToken(None, S_CONTAINER_ID, S_LEGACY_SLOT, S_DECISION_CONFIRM)
    fdictDecideMigratedToken(
        None, S_CONTAINER_ID, S_LEGACY_SLOT, S_DECISION_CONFIRM, sInstance="sandbox",
    )
    assert _fsActive("sandbox") == "old-style"


def testABackupSlotEqualToTheConfirmedTokenFinishesAsActivated(tMigration):
    source, _, _ = tMigration
    source.dictSlots[S_SANDBOX_SLOT] = S_TOKEN
    source.dictSlots["zenodo_token_sandbox_backup"] = S_TOKEN
    _fnRun()
    assert _fsState("zenodo_token_sandbox_backup") == journal.S_STATE_REMOVED
    fdictDecideMigratedToken(None, S_CONTAINER_ID, S_SANDBOX_SLOT, S_DECISION_CONFIRM)
    assert _fsState("zenodo_token_sandbox_backup") == journal.S_STATE_ACTIVATED
    assert _fsPending("zenodo_token_sandbox_backup") == ""


def testAHostProjectsLegacySlotIsStagedAndKept(tmp_path, monkeypatch, fixtureHermeticKeyring):
    """The researcher's own keyring is not deleted from; the slot may serve others."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    os.makedirs(str(tmp_path / "home"))
    monkeypatch.setattr(
        zenodoTokenAccess, "fsProjectUuidForContainer",
        lambda connectionDocker, sContainerId: S_UUID,
    )
    monkeypatch.setattr(
        zenodoTokenMigration, "ftSourceFor",
        lambda connectionDocker, sContainerId: zenodoTokenMigration.ftHostTokenSource(),
    )
    monkeypatch.setattr(
        zenodoTokenMigration.zenodoClient, "fdictProbeToken", _FakeProbe().fdictProbe,
    )
    slots.fnStoreSlotVerified(S_SANDBOX_SLOT, "global-host-token")
    dictDescribed = fdictRunMigrationPass(None, "hostProject")
    assert _fsPending() == "global-host-token"
    assert slots.fsReadSlot(S_SANDBOX_SLOT) == "global-host-token"
    assert _fsState() == journal.S_STATE_SOURCE_KEPT
    assert dictDescribed["bRotationAdvised"] is False


def testAnUnregisteredContainerIsNotMigrated(tMigration):
    assert fdictRunMigrationPass(None, "cid-unknown") == {
        "bApplicable": False, "sProjectUuid": "",
    }
    assert fdictDescribeMigration(None, "cid-unknown")["bApplicable"] is False


def testTheJournalHoldsNoTokenValue(tMigration):
    source, _, _ = tMigration
    source.dictSlots[S_SANDBOX_SLOT] = S_TOKEN
    _fnRun()
    sJournalPath = os.path.join(journal.fsMigrationsDirectory(), f"zenodo-{S_UUID}.json")
    with open(sJournalPath, encoding="utf-8") as fileJournal:
        sText = fileJournal.read()
    assert S_TOKEN not in sText
    assert oct(os.stat(sJournalPath).st_mode & 0o777) == "0o600"
    assert oct(os.stat(journal.fsMigrationsDirectory()).st_mode & 0o777) == "0o700"


def testAQuietPassNeverRaises(tMigration, monkeypatch):
    monkeypatch.setattr(
        zenodoTokenMigration, "fdictRunMigrationPass",
        lambda connectionDocker, sContainerId: (_ for _ in ()).throw(
            RuntimeError("keyring exploded"),
        ),
    )
    zenodoTokenMigration.fnRunMigrationPassQuietly(None, S_CONTAINER_ID)
