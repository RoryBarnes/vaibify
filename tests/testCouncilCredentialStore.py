"""The consent/outcome store follows its transition table and its locks.

Contract A1 of the credential-consent plan: consent and test outcomes
are separate facts, a key is authorized only while its consent is
active AND its latest outcome is a pass at the CURRENT consent
generation, and both locks live on dedicated files so an ``os.replace``
of the document cannot silently stop them excluding anything.

Every row of the transition table has a test here, named after the
row. The expected answers come from the table in the plan, never from
reading the store's code back.
"""

import json
import os
import subprocess
import sys
import threading

import pytest

from vaibify.gui import (
    agentCouncilCredentialGate,
    agentCouncilCredentialStore,
)

S_IMAGE_ONE = "sha256:" + "a1" * 32
S_IMAGE_TWO = "sha256:" + "b2" * 32
S_IMAGE_THREE = "sha256:" + "c3" * 32


@pytest.fixture
def sEvidencePath(tmp_path, monkeypatch):
    """Point the gate (and so every store caller) at a per-test file."""
    pathDirectory = tmp_path / "agentCouncils"
    sPath = str(pathDirectory / "credentialEvidence.json")
    monkeypatch.setattr(
        agentCouncilCredentialGate, "fsResolveCredentialEvidencePath",
        lambda: sPath)
    return sPath


def _fbAuthorized(sProvider="claude", sImage=S_IMAGE_ONE):
    return agentCouncilCredentialGate.fdictEvaluateCredentialEnablement(
        sProvider, sImage)["bEnabled"]


def _fsState(sProvider="claude", sImage=S_IMAGE_ONE):
    return agentCouncilCredentialGate.fdictEvaluateCredentialEnablement(
        sProvider, sImage)["sState"]


def _fnRunTest(sEvidencePath, sJobId, sOutcome, sProvider="claude",
               sImage=S_IMAGE_ONE, dictDetails=None):
    agentCouncilCredentialStore.fdictBeginCredentialTest(
        sEvidencePath, sProvider, sImage, sJobId)
    return agentCouncilCredentialStore.fdictPublishCredentialTestOutcome(
        sEvidencePath, sProvider, sImage, sJobId, sOutcome, dictDetails)


def _fdictLegacyRecord(sProvider="claude", sImage=S_IMAGE_ONE):
    """One record in the real v2 shape, synthetic values throughout."""
    from vaibify.gui import agentCouncilProviderRegistry
    return {
        "sProvider": sProvider,
        "sBackend": "runner",
        "sCliVersion": "9.9.9 (synthetic CLI)",
        "sImageIdentity": sImage,
        "sCredentialSchema":
            agentCouncilProviderRegistry.fsGetProviderCredentialSchema(
                sProvider),
        "sCredentialSource": "containerPersistedLogin",
        "sHostPlatform": sys.platform,
        "sVerificationDate": "2026-01-02",
    }


def _fnWriteJson(sPath, jsonDocument):
    os.makedirs(os.path.dirname(sPath), exist_ok=True)
    with open(sPath, "w", encoding="utf-8") as fileOut:
        json.dump(jsonDocument, fileOut)


# ----- the transition table, one test per row -------------------------------


def test_row_first_consent_then_a_passing_test_authorizes(sEvidencePath):
    dictConsent = agentCouncilCredentialStore.fdictRecordConsent(
        sEvidencePath, "claude", S_IMAGE_ONE)
    assert dictConsent["iConsentGeneration"] == 1
    assert not _fbAuthorized()
    dictOutcome = _fnRunTest(sEvidencePath, "job1", "passed")
    assert dictOutcome["iConsentGeneration"] == 1
    assert dictOutcome["sVerificationMethod"] == "inAppTest"
    assert _fbAuthorized()


@pytest.mark.falsification
def test_row_a_failed_check_is_written_and_does_not_authorize(sEvidencePath):
    """Row: a failed check is recorded, names the check, and disables.

    Kills: a failed outcome counted as a pass.
    """
    agentCouncilCredentialStore.fdictRecordConsent(
        sEvidencePath, "claude", S_IMAGE_ONE)
    _fnRunTest(sEvidencePath, "job1", "failed",
               dictDetails={"sFailedCheck": "trivialTurn"})
    dictAnswer = agentCouncilCredentialGate.fdictEvaluateCredentialEnablement(
        "claude", S_IMAGE_ONE)
    assert dictAnswer["bEnabled"] is False
    assert dictAnswer["sState"] == "lastTestFailed"
    assert "trivialTurn" in dictAnswer["sReason"]


@pytest.mark.falsification
def test_row_starting_a_retest_suspends_the_older_pass(sEvidencePath):
    """Row: a re-test starting suspends the older pass (ruling 3).

    Kills: the evaluator ignoring an in-flight test, so a re-test leaves
    the older pass authorizing.
    """
    agentCouncilCredentialStore.fdictRecordConsent(
        sEvidencePath, "claude", S_IMAGE_ONE)
    _fnRunTest(sEvidencePath, "job1", "passed")
    assert _fbAuthorized()
    agentCouncilCredentialStore.fdictBeginCredentialTest(
        sEvidencePath, "claude", S_IMAGE_ONE, "job2")
    assert not _fbAuthorized()
    assert _fsState() == "testInFlight"


def test_row_an_incomplete_retest_leaves_the_provider_disabled(
        sEvidencePath):
    agentCouncilCredentialStore.fdictRecordConsent(
        sEvidencePath, "claude", S_IMAGE_ONE)
    _fnRunTest(sEvidencePath, "job1", "passed")
    _fnRunTest(sEvidencePath, "job2", "incomplete")
    dictAnswer = agentCouncilCredentialGate.fdictEvaluateCredentialEnablement(
        "claude", S_IMAGE_ONE)
    assert dictAnswer["bEnabled"] is False
    assert dictAnswer["sState"] == "lastTestIncomplete"
    assert "did not finish" in dictAnswer["sReason"]


def test_row_a_passing_retest_authorizes_again(sEvidencePath):
    agentCouncilCredentialStore.fdictRecordConsent(
        sEvidencePath, "claude", S_IMAGE_ONE)
    _fnRunTest(sEvidencePath, "job1", "failed")
    assert not _fbAuthorized()
    _fnRunTest(sEvidencePath, "job2", "passed")
    assert _fbAuthorized()


@pytest.mark.falsification
def test_row_withdraw_advances_the_generation_and_disables(sEvidencePath):
    """Row: withdrawal moves to g+1, withdrawn, and disables.

    Kills: the evaluator not distinguishing a withdrawn consent from an
    active one.
    """
    agentCouncilCredentialStore.fdictRecordConsent(
        sEvidencePath, "claude", S_IMAGE_ONE)
    _fnRunTest(sEvidencePath, "job1", "passed")
    dictWithdrawn = agentCouncilCredentialStore.fdictWithdrawConsent(
        sEvidencePath, "claude", S_IMAGE_ONE)
    assert dictWithdrawn["iConsentGeneration"] == 2
    assert dictWithdrawn["sState"] == "withdrawn"
    assert not _fbAuthorized()
    assert _fsState() == "withdrawn"


def test_row_a_test_published_after_withdrawal_is_stale_and_enables_nothing(
        sEvidencePath):
    agentCouncilCredentialStore.fdictRecordConsent(
        sEvidencePath, "claude", S_IMAGE_ONE)
    agentCouncilCredentialStore.fdictBeginCredentialTest(
        sEvidencePath, "claude", S_IMAGE_ONE, "job1")
    agentCouncilCredentialStore.fdictWithdrawConsent(
        sEvidencePath, "claude", S_IMAGE_ONE)
    dictOutcome = (
        agentCouncilCredentialStore.fdictPublishCredentialTestOutcome(
            sEvidencePath, "claude", S_IMAGE_ONE, "job1", "passed"))
    assert dictOutcome["bStale"] is True
    assert dictOutcome["iConsentGeneration"] == 1
    assert not _fbAuthorized()


@pytest.mark.falsification
def test_row_consent_again_after_withdrawal_needs_a_fresh_test(
        sEvidencePath):
    """Consent given again after a withdrawal starts a new generation.

    Kills: agentCouncilCredentialStore.fdictRecordConsent: the generation
    bump `iConsentGeneration + 1` replaced by the unchanged generation.
    """
    agentCouncilCredentialStore.fdictRecordConsent(
        sEvidencePath, "claude", S_IMAGE_ONE)
    _fnRunTest(sEvidencePath, "job1", "passed")
    agentCouncilCredentialStore.fdictWithdrawConsent(
        sEvidencePath, "claude", S_IMAGE_ONE)
    dictAgain = agentCouncilCredentialStore.fdictRecordConsent(
        sEvidencePath, "claude", S_IMAGE_ONE)
    assert dictAgain["iConsentGeneration"] == 3
    assert not _fbAuthorized()
    assert _fsState() == "staleGeneration"
    _fnRunTest(sEvidencePath, "job2", "passed")
    assert _fbAuthorized()


def test_row_a_legacy_record_never_withdrawn_is_an_implied_consent(
        sEvidencePath):
    _fnWriteJson(sEvidencePath, {"listRecords": [_fdictLegacyRecord()]})
    dictAnswer = agentCouncilCredentialGate.fdictEvaluateCredentialEnablement(
        "claude", S_IMAGE_ONE)
    assert dictAnswer["bEnabled"] is True
    assert dictAnswer["dictRecord"]["sCliVersion"] == "9.9.9 (synthetic CLI)"


@pytest.mark.falsification
def test_row_a_legacy_record_cannot_resurrect_a_withdrawn_key(sEvidencePath):
    """Row: a legacy pass cannot re-enable a withdrawn key.

    Kills: the evaluator skipping the generation comparison, so a legacy
    pass at generation 1 re-enables a key the researcher withdrew.
    """
    _fnWriteJson(sEvidencePath, {"listRecords": [_fdictLegacyRecord()]})
    agentCouncilCredentialStore.fdictWithdrawConsent(
        sEvidencePath, "claude", S_IMAGE_ONE)
    assert not _fbAuthorized()
    agentCouncilCredentialStore.fdictRecordConsent(
        sEvidencePath, "claude", S_IMAGE_ONE)
    assert not _fbAuthorized(), (
        "the legacy pass at generation 1 re-enabled a key the researcher "
        "withdrew and consented to again")
    assert _fsState() == "staleGeneration"


# ----- the remaining T-A1 properties -----------------------------------------


def test_a_failed_retest_disables_despite_an_older_pass(sEvidencePath):
    agentCouncilCredentialStore.fdictRecordConsent(
        sEvidencePath, "claude", S_IMAGE_ONE)
    _fnRunTest(sEvidencePath, "job1", "passed")
    _fnRunTest(sEvidencePath, "job2", "failed")
    assert not _fbAuthorized()


def test_a_second_begin_converts_the_orphan_marker_to_incomplete(
        sEvidencePath):
    agentCouncilCredentialStore.fdictRecordConsent(
        sEvidencePath, "claude", S_IMAGE_ONE)
    agentCouncilCredentialStore.fdictBeginCredentialTest(
        sEvidencePath, "claude", S_IMAGE_ONE, "jobOrphan")
    agentCouncilCredentialStore.fdictBeginCredentialTest(
        sEvidencePath, "claude", S_IMAGE_ONE, "jobNext")
    dictDocument = agentCouncilCredentialStore.fdictReadCredentialDocument(
        sEvidencePath)["dictDocument"]
    listOrphan = [dictOutcome for dictOutcome in dictDocument["listOutcomes"]
                  if dictOutcome["sJobId"] == "jobOrphan"]
    assert [dictOutcome["sOutcome"] for dictOutcome in listOrphan] == [
        "incomplete"]
    with pytest.raises(agentCouncilCredentialStore.CredentialStoreError):
        agentCouncilCredentialStore.fdictPublishCredentialTestOutcome(
            sEvidencePath, "claude", S_IMAGE_ONE, "jobOrphan", "passed")


def test_a_test_cannot_begin_without_active_consent(sEvidencePath):
    with pytest.raises(agentCouncilCredentialStore.CredentialStoreError):
        agentCouncilCredentialStore.fdictBeginCredentialTest(
            sEvidencePath, "claude", S_IMAGE_ONE, "job1")
    agentCouncilCredentialStore.fdictRecordConsent(
        sEvidencePath, "claude", S_IMAGE_ONE)
    agentCouncilCredentialStore.fdictWithdrawConsent(
        sEvidencePath, "claude", S_IMAGE_ONE)
    with pytest.raises(agentCouncilCredentialStore.CredentialStoreError):
        agentCouncilCredentialStore.fdictBeginCredentialTest(
            sEvidencePath, "claude", S_IMAGE_ONE, "job1")


def test_outcome_history_is_never_deleted(sEvidencePath):
    agentCouncilCredentialStore.fdictRecordConsent(
        sEvidencePath, "claude", S_IMAGE_ONE)
    for iIndex, sOutcome in enumerate(("passed", "failed", "incomplete",
                                       "passed")):
        _fnRunTest(sEvidencePath, f"job{iIndex}", sOutcome)
    agentCouncilCredentialStore.fdictWithdrawConsent(
        sEvidencePath, "claude", S_IMAGE_ONE)
    dictDocument = agentCouncilCredentialStore.fdictReadCredentialDocument(
        sEvidencePath)["dictDocument"]
    assert [dictOutcome["sOutcome"]
            for dictOutcome in dictDocument["listOutcomes"]] == [
        "passed", "failed", "incomplete", "passed"]


def test_keys_are_independent_per_provider_and_image(sEvidencePath):
    agentCouncilCredentialStore.fdictRecordConsent(
        sEvidencePath, "claude", S_IMAGE_ONE)
    _fnRunTest(sEvidencePath, "job1", "passed")
    assert _fbAuthorized("claude", S_IMAGE_ONE)
    assert not _fbAuthorized("claude", S_IMAGE_TWO)
    assert not _fbAuthorized("codex", S_IMAGE_ONE)


def test_the_document_never_holds_token_material(sEvidencePath):
    agentCouncilCredentialStore.fdictRecordConsent(
        sEvidencePath, "claude", S_IMAGE_ONE)
    _fnRunTest(sEvidencePath, "job1", "passed",
               dictDetails={"listPassedChecks": ["loginPresent"],
                            "sCliVersion": "1.0"})
    with open(sEvidencePath, encoding="utf-8") as fileEvidence:
        sText = fileEvidence.read()
    for sForbidden in ("accessToken", "refreshToken", ".credentials",
                       "sAccessToken"):
        assert sForbidden not in sText


def test_the_document_is_written_owner_only(sEvidencePath):
    agentCouncilCredentialStore.fdictRecordConsent(
        sEvidencePath, "claude", S_IMAGE_ONE)
    assert os.stat(sEvidencePath).st_mode & 0o777 == 0o600
    assert os.stat(os.path.dirname(sEvidencePath)).st_mode & 0o077 == 0


# ----- damage and legacy --------------------------------------------------


@pytest.mark.falsification
def test_a_damaged_file_is_reported_then_renamed_never_rewritten(
        sEvidencePath):
    """A read reports damage; the next write renames it aside, intact.

    Kills: a damaged document overwritten in place instead of renamed
    aside.
    """
    os.makedirs(os.path.dirname(sEvidencePath), exist_ok=True)
    with open(sEvidencePath, "w", encoding="utf-8") as fileOut:
        fileOut.write("{damaged")
    dictAnswer = agentCouncilCredentialGate.fdictEvaluateCredentialEnablement(
        "claude", S_IMAGE_ONE)
    assert dictAnswer["sState"] == "damaged"
    assert "unreadable" in dictAnswer["sReason"]
    assert "renamed" in dictAnswer["sReason"]
    with open(sEvidencePath, encoding="utf-8") as fileIn:
        assert fileIn.read() == "{damaged", "a READ rewrote a damaged file"
    agentCouncilCredentialStore.fdictRecordConsent(
        sEvidencePath, "claude", S_IMAGE_ONE)
    listDamaged = [sName for sName in os.listdir(
        os.path.dirname(sEvidencePath)) if ".damaged-" in sName]
    assert len(listDamaged) == 1
    with open(os.path.join(os.path.dirname(sEvidencePath),
                           listDamaged[0]), encoding="utf-8") as fileIn:
        assert fileIn.read() == "{damaged"
    assert json.load(open(sEvidencePath))["iSchemaVersion"] == 3


def test_a_v3_document_with_a_wrong_shape_is_damaged(sEvidencePath):
    _fnWriteJson(sEvidencePath, {"iSchemaVersion": 3, "dictConsents": []})
    assert _fsState() == "damaged"
    _fnWriteJson(sEvidencePath, {"iSchemaVersion": 99})
    assert _fsState() == "damaged"


@pytest.mark.falsification
def test_a_real_shape_v2_file_enables_exactly_what_it_did_before(
        sEvidencePath):
    """The researcher's machine must not change behaviour on upgrade.

    The fixture copies the SHAPE of a real v2 file (four records over
    three providers, two images shared by two providers, extra keys a
    later ceremony added) with synthetic values. The expectations are
    the v2 rules: each record enables its own (provider, image) and
    nothing else; the image-blind read enables a provider that has any
    valid record.

    Kills: v2 listRecords documents read as one record, which disables
    every existing manual attestation.
    """
    dictCodex = _fdictLegacyRecord("codex", S_IMAGE_THREE)
    dictCodex["listModelIds"] = ["syntheticModel"]
    dictGemini = _fdictLegacyRecord("gemini", S_IMAGE_THREE)
    dictGemini["listModelIds"] = ["syntheticModel"]
    _fnWriteJson(sEvidencePath, {"listRecords": [
        _fdictLegacyRecord("claude", S_IMAGE_ONE),
        _fdictLegacyRecord("claude", S_IMAGE_TWO),
        dictCodex, dictGemini]})
    dictExpected = {
        ("claude", S_IMAGE_ONE): True, ("claude", S_IMAGE_TWO): True,
        ("claude", S_IMAGE_THREE): False,
        ("codex", S_IMAGE_THREE): True, ("codex", S_IMAGE_ONE): False,
        ("gemini", S_IMAGE_THREE): True, ("gemini", S_IMAGE_TWO): False,
        ("claude", None): True, ("codex", None): True, ("gemini", None): True,
    }
    for (sProvider, sImage), bExpected in dictExpected.items():
        assert agentCouncilCredentialGate.fdictEvaluateCredentialEnablement(
            sProvider, sImage)["bEnabled"] is bExpected, (sProvider, sImage)
    with open(sEvidencePath, encoding="utf-8") as fileIn:
        assert "iSchemaVersion" not in fileIn.read(), (
            "reading a legacy file rewrote it")


def test_a_legacy_file_survives_a_consent_on_another_key(sEvidencePath):
    _fnWriteJson(sEvidencePath, {"listRecords": [_fdictLegacyRecord()]})
    agentCouncilCredentialStore.fdictRecordConsent(
        sEvidencePath, "claude", S_IMAGE_TWO)
    assert _fbAuthorized("claude", S_IMAGE_ONE)
    dictDocument = json.load(open(sEvidencePath))
    assert dictDocument["iSchemaVersion"] == 3
    assert dictDocument["listLegacyRecords"] == [_fdictLegacyRecord()]


@pytest.mark.falsification
def test_a_legacy_record_one_key_short_names_the_key(sEvidencePath):
    """A manual record one key short says which key.

    Kills: the legacy refusal detail dropped, so a record one key short
    reads as absent.
    """
    dictRecord = _fdictLegacyRecord()
    del dictRecord["sCliVersion"]
    _fnWriteJson(sEvidencePath, dictRecord)
    dictAnswer = agentCouncilCredentialGate.fdictEvaluateCredentialEnablement(
        "claude", S_IMAGE_ONE)
    assert dictAnswer["bEnabled"] is False
    assert "sCliVersion" in dictAnswer["sReason"]


def test_withdrawing_nothing_changes_nothing(sEvidencePath):
    _fnWriteJson(sEvidencePath, {"listRecords": [_fdictLegacyRecord()]})
    assert agentCouncilCredentialStore.fdictWithdrawConsent(
        sEvidencePath, "codex", S_IMAGE_ONE) is None
    with open(sEvidencePath, encoding="utf-8") as fileIn:
        assert "iSchemaVersion" not in fileIn.read()


# ----- concurrency ----------------------------------------------------------


@pytest.mark.falsification
def test_one_job_for_two_concurrent_requests_on_one_key(sEvidencePath):
    """The per-key test lock admits one job per key and frees on close.

    Kills: a shared rather than exclusive test lock, which admits two
    jobs for one key.
    """
    sDirectory = os.path.dirname(sEvidencePath)
    fileFirst = agentCouncilCredentialStore.ffileTryAcquireTestLock(
        sDirectory, "claude", S_IMAGE_ONE)
    assert fileFirst is not None
    try:
        assert agentCouncilCredentialStore.ffileTryAcquireTestLock(
            sDirectory, "claude", S_IMAGE_ONE) is None
        fileOther = agentCouncilCredentialStore.ffileTryAcquireTestLock(
            sDirectory, "codex", S_IMAGE_ONE)
        assert fileOther is not None
        fileOther.close()
    finally:
        fileFirst.close()
    fileAgain = agentCouncilCredentialStore.ffileTryAcquireTestLock(
        sDirectory, "claude", S_IMAGE_ONE)
    assert fileAgain is not None
    fileAgain.close()


def test_concurrent_tests_for_different_providers_both_land(sEvidencePath):
    """No lost update: every writer's outcomes survive the others'."""
    for sProvider in ("claude", "codex", "gemini"):
        agentCouncilCredentialStore.fdictRecordConsent(
            sEvidencePath, sProvider, S_IMAGE_ONE)
    iRounds = 15

    def _fnHammer(sProvider):
        for iRound in range(iRounds):
            _fnRunTest(sEvidencePath, f"{sProvider}{iRound}", "passed",
                       sProvider=sProvider)

    listThreads = [threading.Thread(target=_fnHammer, args=(sProvider,))
                   for sProvider in ("claude", "codex", "gemini")]
    for threadWorker in listThreads:
        threadWorker.start()
    for threadWorker in listThreads:
        threadWorker.join()
    dictDocument = agentCouncilCredentialStore.fdictReadCredentialDocument(
        sEvidencePath)["dictDocument"]
    assert len(dictDocument["listOutcomes"]) == 3 * iRounds
    for sProvider in ("claude", "codex", "gemini"):
        assert _fbAuthorized(sProvider, S_IMAGE_ONE)


def test_a_withdrawal_completes_while_a_test_holds_its_job_lock(
        sEvidencePath):
    sDirectory = os.path.dirname(sEvidencePath)
    agentCouncilCredentialStore.fdictRecordConsent(
        sEvidencePath, "claude", S_IMAGE_ONE)
    fileJob = agentCouncilCredentialStore.ffileTryAcquireTestLock(
        sDirectory, "claude", S_IMAGE_ONE)
    agentCouncilCredentialStore.fdictBeginCredentialTest(
        sEvidencePath, "claude", S_IMAGE_ONE, "job1")
    try:
        threadWithdraw = threading.Thread(
            target=agentCouncilCredentialStore.fdictWithdrawConsent,
            args=(sEvidencePath, "claude", S_IMAGE_ONE))
        threadWithdraw.start()
        threadWithdraw.join(timeout=10)
        assert not threadWithdraw.is_alive(), (
            "withdrawal waited on a running test")
    finally:
        fileJob.close()
    assert _fsState() == "withdrawn"


# ----- T-A1-locks -----------------------------------------------------------


def test_both_locks_live_on_dedicated_files(sEvidencePath):
    sDirectory = os.path.dirname(sEvidencePath)
    with agentCouncilCredentialStore.fcontextHoldStoreLock(sDirectory):
        assert agentCouncilCredentialStore.fbIsStoreLockHeld()
    assert not agentCouncilCredentialStore.fbIsStoreLockHeld()
    fileJob = agentCouncilCredentialStore.ffileTryAcquireTestLock(
        sDirectory, "claude", S_IMAGE_ONE)
    fileJob.close()
    setNames = set(os.listdir(sDirectory))
    assert "credentialStore.lock" in setNames
    assert agentCouncilCredentialStore.fsComposeTestLockBasename(
        "claude", S_IMAGE_ONE) in setNames
    assert not any(sName.startswith("credentialEvidence")
                   and sName.endswith(".lock") for sName in setNames)


_S_CHILD_HOLDS_STORE_LOCK = r"""
import sys, time
from vaibify.gui import agentCouncilCredentialStore
with agentCouncilCredentialStore.fcontextHoldStoreLock(sys.argv[1]):
    print("held", flush=True)
    time.sleep(float(sys.argv[2]))
"""


@pytest.mark.falsification
def test_the_store_lock_excludes_across_processes_despite_a_replace(
        sEvidencePath):
    """Lock a child, REPLACE the document, then try the lock here.

    A lock taken on the document itself would stop excluding the moment
    ``os.replace`` swapped the inode; the dedicated lock file must not.

    Kills: the store lock taken on the document os.replace swaps, which
    excludes nothing after a write.
    """
    import time
    sDirectory = os.path.dirname(sEvidencePath)
    agentCouncilCredentialStore.fdictRecordConsent(
        sEvidencePath, "claude", S_IMAGE_ONE)
    processChild = subprocess.Popen(
        [sys.executable, "-c", _S_CHILD_HOLDS_STORE_LOCK, sDirectory, "2"],
        stdout=subprocess.PIPE, text=True,
        env={**os.environ, "PYTHONPATH": os.getcwd()})
    try:
        assert processChild.stdout.readline().strip() == "held"
        with open(sEvidencePath + ".new", "w", encoding="utf-8") as fileNew:
            fileNew.write(open(sEvidencePath, encoding="utf-8").read())
        os.replace(sEvidencePath + ".new", sEvidencePath)
        fStart = time.monotonic()
        with agentCouncilCredentialStore.fcontextHoldStoreLock(sDirectory):
            fWaited = time.monotonic() - fStart
    finally:
        processChild.wait(timeout=30)
    assert fWaited > 1.0, (
        f"the store lock was granted after {fWaited:.2f}s while another "
        "process held it")


def test_a_mutation_that_touches_docker_under_the_lock_is_caught(
        sEvidencePath):
    """The fake Docker refuses under the store lock — the harness works."""

    class FakeDockerRefusingUnderLock:
        def fbaFetchCredentialFile(self, sContainerId, sPath):
            if agentCouncilCredentialStore.fbIsStoreLockHeld():
                raise AssertionError("Docker was called under the store lock")
            return b"{}"

    dockerFake = FakeDockerRefusingUnderLock()
    dockerFake.fbaFetchCredentialFile("c", "/p")
    with pytest.raises(AssertionError):
        agentCouncilCredentialStore.fdictMutateCredentialDocument(
            sEvidencePath,
            lambda dictDocument: dockerFake.fbaFetchCredentialFile("c", "/p"))


# ----- the doctor reads, never writes -----------------------------------------------


@pytest.mark.falsification
def test_the_doctor_lists_each_key_and_never_renames_a_damaged_file(
        sEvidencePath):
    """``vaibify doctor`` reports; the rename belongs to the next consent.

    Kills: the doctor's read path setting a damaged document aside,
    which would make a read-only report a writer.
    """
    from vaibify.cli import preflightChecks
    agentCouncilCredentialStore.fdictRecordConsent(
        sEvidencePath, "claude", S_IMAGE_ONE)
    _fnRunTest(sEvidencePath, "job1", "passed")
    sMessage = preflightChecks.fpreflightCouncilCredentialEvidence().sMessage
    assert "consent active; latest test passed" in sMessage
    assert S_IMAGE_ONE[:19] in sMessage
    with open(sEvidencePath, "w", encoding="utf-8") as fileOut:
        fileOut.write("{damaged")
    sMessage = preflightChecks.fpreflightCouncilCredentialEvidence().sMessage
    assert "unreadable" in sMessage
    assert open(sEvidencePath, encoding="utf-8").read() == "{damaged"
    assert not [sName for sName in os.listdir(os.path.dirname(sEvidencePath))
                if ".damaged-" in sName]
