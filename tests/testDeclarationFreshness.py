"""The AI Declaration sign-off goes stale when the work it covers changes.

Ruling (2026-10-08): after the AI Declaration is signed off, any change
to another step's scripts, outputs or declared input data makes the
sign-off STALE and blocks Level 2 until the researcher signs off again.
Stale is sticky; evidence that cannot be read is "could not check" and
blocks Level 2 too; a sign-off from before baselines existed is
grandfathered and says so.

Every test drives real files in a real directory. The producing step's
id, name and directory are three different strings, so a lookup keyed
by the wrong one cannot pass by coincidence.
"""

import copy
import os
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from vaibify.gui.fileStatusManager import (
    fbReconcileUserVerificationByContentHash,
)
from vaibify.gui.routes import levelRoutes
from vaibify.reproducibility import levelGates
from vaibify.reproducibility.aiDeclarationStep import (
    fdictBuildAiDeclarationStep,
)
from vaibify.reproducibility.declarationFreshness import (
    S_BASELINE_KEY,
    fbLatchStaleDeclaration,
    fdictBuildDeclarationBaseline,
    fdictEvaluateDeclarationFreshness,
)

S_PRODUCER_ID = "producer-7f3"
S_PRODUCER_NAME = "Make Values"
S_PRODUCER_DIRECTORY = "ValuesStage"
S_SCRIPT = S_PRODUCER_DIRECTORY + "/makeValues.py"
S_OUTPUT = S_PRODUCER_DIRECTORY + "/values.json"
S_INPUT = "raw/measurements.csv"
S_CONTAINER_KEY = "freshness-route-key"


def _fnWrite(sRepo, sRelative, sText):
    sPath = os.path.join(sRepo, sRelative)
    os.makedirs(os.path.dirname(sPath), exist_ok=True)
    with open(sPath, "w") as fileHandle:
        fileHandle.write(sText)


def _fdictWorkflow(sRepo):
    dictProducer = {
        "sName": S_PRODUCER_NAME, "sStepId": S_PRODUCER_ID,
        "sDirectory": S_PRODUCER_DIRECTORY,
        "saDataCommands": ["python makeValues.py"],
        "saPlotCommands": [], "saPlotFiles": [],
        "saOutputDataFiles": ["values.json"],
        "saInputDataFiles": [S_INPUT],
        "dictVerification": {"sUser": "passed"},
    }
    dictDeclaration = fdictBuildAiDeclarationStep()
    dictDeclaration["sStepId"] = "declaration-91c"
    return {
        "sProjectRepoPath": sRepo, "sPlotDirectory": "Plot",
        "listSteps": [dictProducer, dictDeclaration],
    }


@pytest.fixture
def tSignedProject(tmp_path):
    """Return ``(sRepo, dictWorkflow)`` with a freshly signed declaration."""
    sRepo = str(tmp_path / "repo")
    _fnWrite(sRepo, S_SCRIPT, "print('values')\n")
    _fnWrite(sRepo, S_OUTPUT, '{"a": 1}\n')
    _fnWrite(sRepo, S_INPUT, "1,2,3\n")
    dictWorkflow = _fdictWorkflow(sRepo)
    dictVerification = dictWorkflow["listSteps"][1]["dictVerification"]
    dictVerification["sUser"] = "passed"
    dictVerification[S_BASELINE_KEY] = fdictBuildDeclarationBaseline(
        dictWorkflow, sRepo, "2026-10-08T12:00:00Z")
    return sRepo, dictWorkflow


def _fsVerdict(dictWorkflow, sRepo):
    return fdictEvaluateDeclarationFreshness(dictWorkflow, sRepo)["sVerdict"]


def test_a_fresh_sign_off_passes_level_two_s_declaration_gate(
    tSignedProject,
):
    sRepo, dictWorkflow = tSignedProject
    assert _fsVerdict(dictWorkflow, sRepo) == "fresh"
    assert levelGates.fbWorkflowAiDeclarationAttested(dictWorkflow, sRepo)


def test_a_change_in_the_same_second_as_the_sign_off_is_stale(
    tSignedProject,
):
    """Content, not clocks: an mtime comparison cannot see this one."""
    sRepo, dictWorkflow = tSignedProject
    sPath = os.path.join(sRepo, S_OUTPUT)
    fMtime = os.stat(sPath).st_mtime
    _fnWrite(sRepo, S_OUTPUT, '{"a": 2}\n')
    os.utime(sPath, (fMtime, fMtime))
    dictVerdict = fdictEvaluateDeclarationFreshness(dictWorkflow, sRepo)
    assert dictVerdict["sVerdict"] == "stale"
    assert [d["sPath"] for d in dictVerdict["listChanges"]] == [S_OUTPUT]
    assert dictVerdict["listChanges"][0]["sStepId"] == S_PRODUCER_ID
    assert not levelGates.fbWorkflowAiDeclarationAttested(
        dictWorkflow, sRepo)


@pytest.mark.falsification
def test_a_deleted_covered_file_is_stale(tSignedProject):
    """Kills: declarationFreshness dropping the bMissing branch, so a deleted
    file reads as unreadable.
    """
    sRepo, dictWorkflow = tSignedProject
    os.remove(os.path.join(sRepo, S_INPUT))
    dictVerdict = fdictEvaluateDeclarationFreshness(dictWorkflow, sRepo)
    assert dictVerdict["sVerdict"] == "stale"
    assert dictVerdict["listChanges"][0]["sChange"] == "deleted"
    assert dictVerdict["listChanges"][0]["sKind"] == "input data"


def test_a_file_added_to_a_covered_step_is_stale(tSignedProject):
    sRepo, dictWorkflow = tSignedProject
    _fnWrite(sRepo, S_PRODUCER_DIRECTORY + "/extra.json", "{}\n")
    dictWorkflow["listSteps"][0]["saOutputDataFiles"].append("extra.json")
    dictVerdict = fdictEvaluateDeclarationFreshness(dictWorkflow, sRepo)
    assert dictVerdict["sVerdict"] == "stale"
    assert dictVerdict["listChanges"][0]["sChange"] == "added"


def test_a_checkout_that_only_moves_mtimes_is_not_stale(tSignedProject):
    sRepo, dictWorkflow = tSignedProject
    fLater = time.time() + 3600
    for sRelative in (S_SCRIPT, S_OUTPUT, S_INPUT):
        os.utime(os.path.join(sRepo, sRelative), (fLater, fLater))
    assert _fsVerdict(dictWorkflow, sRepo) == "fresh"


def test_the_declaration_file_itself_is_not_a_trigger(tSignedProject):
    sRepo, dictWorkflow = tSignedProject
    _fnWrite(sRepo, "AI_USAGE.md", "# edited after signing\n")
    assert _fsVerdict(dictWorkflow, sRepo) == "fresh"


@pytest.mark.falsification
def test_a_change_then_a_revert_stays_stale_until_signed_again(
    tSignedProject,
):
    """Kills: declarationFreshness forgetting a latched stale sign-off."""
    sRepo, dictWorkflow = tSignedProject
    _fnWrite(sRepo, S_SCRIPT, "print('edited')\n")
    assert fbLatchStaleDeclaration(
        dictWorkflow, fdictEvaluateDeclarationFreshness(dictWorkflow, sRepo))
    _fnWrite(sRepo, S_SCRIPT, "print('values')\n")
    dictVerdict = fdictEvaluateDeclarationFreshness(dictWorkflow, sRepo)
    assert dictVerdict["sVerdict"] == "stale"
    assert dictVerdict["listChanges"][0]["sPath"] == S_SCRIPT
    assert not levelGates.fbWorkflowAiDeclarationAttested(
        dictWorkflow, sRepo)


@pytest.mark.falsification
def test_the_plot_reconciliation_never_restores_the_declaration(
    tSignedProject,
):
    """The content-hash pass restores ``stale`` -> ``passed`` for a step
    whose plots match their recorded hashes. Given a declaration that
    carries a plot whose hash still matches, it must still not.

    Kills: fileStatusManager dropping the declaration exclusion from the
    content-hash pass.
    """
    sRepo, dictWorkflow = tSignedProject
    _fnWrite(sRepo, "AIDeclaration/summary.png", "png bytes")
    dictDeclaration = dictWorkflow["listSteps"][1]
    dictDeclaration["saPlotFiles"] = ["summary.png"]
    dictDeclaration["dictVerification"]["sUser"] = "passed"
    fbReconcileUserVerificationByContentHash(dictWorkflow, sRepo, sRepo)
    dictDeclaration["dictVerification"]["sUser"] = "stale"
    import hashlib
    with open(os.path.join(sRepo, "AIDeclaration/summary.png"), "rb") as f:
        sHash = hashlib.sha256(f.read()).hexdigest()
    dictDeclaration["dictVerification"]["dictUserVerifiedHashes"] = {
        "AIDeclaration/summary.png": sHash}
    fbReconcileUserVerificationByContentHash(dictWorkflow, sRepo, sRepo)
    assert dictDeclaration["dictVerification"]["sUser"] == "stale"


@pytest.mark.falsification
@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root reads a mode-000 file, so it cannot be made unreadable",
)
def test_an_unreadable_covered_file_is_unknown_and_blocks_level_two(
    tSignedProject,
):
    """Kills: declarationFreshness reading an unreadable file as absent."""
    sRepo, dictWorkflow = tSignedProject
    sPath = os.path.join(sRepo, S_INPUT)
    os.chmod(sPath, 0)
    try:
        dictVerdict = fdictEvaluateDeclarationFreshness(dictWorkflow, sRepo)
        bAttested = levelGates.fbWorkflowAiDeclarationAttested(
            dictWorkflow, sRepo)
        listBlockers = levelGates._flistAiDeclarationLevel2Blockers(
            dictWorkflow, sRepo)
    finally:
        os.chmod(sPath, 0o600)
    assert dictVerdict["sVerdict"] == "unknown"
    assert S_INPUT in dictVerdict["sReason"]
    assert bAttested is False
    assert [d["sCriterion"] for d in listBlockers] == [
        "ai-declaration-uncheckable"]


def test_a_legacy_sign_off_is_grandfathered_and_says_so(tmp_path):
    sRepo = str(tmp_path / "repo")
    os.makedirs(sRepo)
    dictWorkflow = _fdictWorkflow(sRepo)
    dictWorkflow["listSteps"][1]["dictVerification"]["sUser"] = "passed"
    dictVerdict = levelGates.fdictDeclarationFreshnessForDisplay(
        dictWorkflow, sRepo)
    assert dictVerdict["sVerdict"] == "untracked"
    assert "next sign-off" in dictVerdict["sMessage"]
    assert levelGates.fbWorkflowAiDeclarationAttested(dictWorkflow, sRepo)


def test_the_stale_sentence_names_the_step_by_label(tSignedProject):
    sRepo, dictWorkflow = tSignedProject
    _fnWrite(sRepo, S_OUTPUT, '{"a": 3}\n')
    sMessage = levelGates.fdictDeclarationFreshnessForDisplay(
        dictWorkflow, sRepo)["sMessage"]
    sLabel = levelGates._fsLabelForStep(dictWorkflow, 0)
    assert sMessage.startswith(f"Step {sLabel}'s outputs changed after")
    assert "sign off again" in sMessage
    assert S_PRODUCER_ID not in sMessage


def _fclientOver(dictWorkflow):
    app = FastAPI()
    levelRoutes.fnRegisterAll(app, {
        "docker": None, "workflows": {S_CONTAINER_KEY: dictWorkflow},
        "paths": {}, "require": lambda *aArgs: None,
        "save": lambda sId, dictWf: None,
    })
    return TestClient(app)


def _fdictSurfaces(dictWorkflow, sRepo):
    """Return what each surface says about the declaration requirement."""
    levelGates.fnClearLevelBlockerCache()
    listL2 = levelGates.flistLevel2Blockers(dictWorkflow, sRepo)
    dictCells = levelGates.fdictComputeStepLevelStates(
        dictWorkflow, [], listL2, [])
    dictContext = levelGates._fdictStepProjectionContext(
        dictWorkflow, [], listL2, [])
    dictContext["dictUnknownFreshnessByStep"] = {}
    dictRequirements = levelGates._fdictStepLevelRequirementLists(
        1, dictWorkflow["listSteps"][1], dictContext)
    dictReadiness = _fclientOver(dictWorkflow).get(
        f"/api/workflow/{S_CONTAINER_KEY}/level2/readiness").json()
    return {
        "listCriteria": sorted(
            d["sCriterion"] for d in listL2 if d["iStepIndex"] == 1),
        "bRequirement": dict(dictRequirements["2"])[
            "ai-declaration-attested"],
        "sCellState": dictCells[1]["s2"]["sState"],
        "bReadiness": dictReadiness["dictLevel2Gaps"][
            "bAiDeclarationAttested"],
        "sReadinessVerdict": dictReadiness[
            "dictAiDeclarationFreshness"]["sVerdict"],
    }


def test_every_surface_agrees_on_a_fresh_sign_off(tSignedProject):
    sRepo, dictWorkflow = tSignedProject
    dictSurfaces = _fdictSurfaces(dictWorkflow, sRepo)
    assert dictSurfaces["listCriteria"] == []
    assert dictSurfaces["bRequirement"] is True
    assert dictSurfaces["bReadiness"] is True
    assert dictSurfaces["sReadinessVerdict"] == "fresh"


@pytest.mark.falsification
def test_a_change_blocks_readiness_with_no_poll_in_between(tSignedProject):
    """No latch, no cached flag: the readiness answer is computed from
    the files as they are when it is asked.

    Kills: levelGates passing the declaration on the stored sUser alone.
    """
    sRepo, dictWorkflow = tSignedProject
    _fnWrite(sRepo, S_SCRIPT, "print('changed')\n")
    assert dictWorkflow["listSteps"][1]["dictVerification"]["sUser"] == (
        "passed")
    dictSurfaces = _fdictSurfaces(dictWorkflow, sRepo)
    assert dictSurfaces["listCriteria"] == ["ai-declaration-stale"]
    assert dictSurfaces["bRequirement"] is False
    assert dictSurfaces["sCellState"] != "attained"
    assert dictSurfaces["bReadiness"] is False
    assert dictSurfaces["sReadinessVerdict"] == "stale"


def test_every_surface_agrees_once_stale_is_latched(tSignedProject):
    sRepo, dictWorkflow = tSignedProject
    _fnWrite(sRepo, S_INPUT, "4,5,6\n")
    fbLatchStaleDeclaration(
        dictWorkflow, fdictEvaluateDeclarationFreshness(dictWorkflow, sRepo))
    dictSurfaces = _fdictSurfaces(dictWorkflow, sRepo)
    assert dictSurfaces["listCriteria"] == ["ai-declaration-stale"]
    assert dictSurfaces["bRequirement"] is False
    assert dictSurfaces["bReadiness"] is False
    assert dictSurfaces["sReadinessVerdict"] == "stale"


@pytest.mark.falsification
@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root reads a mode-000 file, so it cannot be made unreadable",
)
def test_every_surface_agrees_when_it_could_not_check(tSignedProject):
    """Kills: levelGates rendering could-not-check as a failed requirement."""
    sRepo, dictWorkflow = tSignedProject
    sPath = os.path.join(sRepo, S_OUTPUT)
    os.chmod(sPath, 0)
    try:
        dictSurfaces = _fdictSurfaces(dictWorkflow, sRepo)
    finally:
        os.chmod(sPath, 0o600)
    assert dictSurfaces["listCriteria"] == ["ai-declaration-uncheckable"]
    assert dictSurfaces["bRequirement"] is None
    assert dictSurfaces["sCellState"] != "attained"
    assert dictSurfaces["bReadiness"] is False
    assert dictSurfaces["sReadinessVerdict"] == "unknown"


def test_every_surface_agrees_on_a_legacy_sign_off(tSignedProject):
    sRepo, dictWorkflow = tSignedProject
    dictWorkflow["listSteps"][1]["dictVerification"].pop(S_BASELINE_KEY)
    _fnWrite(sRepo, S_SCRIPT, "print('changed before tracking')\n")
    dictSurfaces = _fdictSurfaces(dictWorkflow, sRepo)
    assert dictSurfaces["listCriteria"] == []
    assert dictSurfaces["bRequirement"] is True
    assert dictSurfaces["bReadiness"] is True
    assert dictSurfaces["sReadinessVerdict"] == "untracked"


def test_a_baseline_cannot_be_recorded_over_an_unreadable_file(
    tSignedProject,
):
    from vaibify.reproducibility.declarationFreshness import (
        DeclarationEvidenceUnreadableError,
    )
    sRepo, dictWorkflow = tSignedProject
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("root reads a mode-000 file")
    sPath = os.path.join(sRepo, S_SCRIPT)
    os.chmod(sPath, 0)
    try:
        with pytest.raises(DeclarationEvidenceUnreadableError) as info:
            fdictBuildDeclarationBaseline(
                copy.deepcopy(dictWorkflow), sRepo, "")
    finally:
        os.chmod(sPath, 0o600)
    assert S_SCRIPT in str(info.value)


def test_the_cross_poll_blocker_cache_sees_a_changed_file(tSignedProject):
    """The L2 blocker list is cached across polls by fingerprints of the
    workflow and the published files; input data is in neither, so the
    freshness verdict itself must be part of the key."""
    sRepo, dictWorkflow = tSignedProject
    levelGates.fnClearLevelBlockerCache()
    assert not [
        d for d in levelGates.flistLevel2Blockers(dictWorkflow, sRepo)
        if d["iStepIndex"] == 1]
    _fnWrite(sRepo, S_INPUT, "7,8,9\n")
    assert [
        d["sCriterion"]
        for d in levelGates.flistLevel2Blockers(dictWorkflow, sRepo)
        if d["iStepIndex"] == 1] == ["ai-declaration-stale"]


@pytest.mark.falsification
def test_the_poll_latches_a_stale_sign_off(tSignedProject):
    """Kills: pipelineRoutes never latching a stale sign-off."""
    from vaibify.gui.routes import pipelineRoutes
    sRepo, dictWorkflow = tSignedProject
    assert pipelineRoutes._fbLatchStaleAiDeclaration(
        dictWorkflow, sRepo) is False
    _fnWrite(sRepo, S_OUTPUT, '{"a": 9}\n')
    assert pipelineRoutes._fbLatchStaleAiDeclaration(
        dictWorkflow, sRepo) is True
    dictVerification = dictWorkflow["listSteps"][1]["dictVerification"]
    assert dictVerification["sUser"] == "stale"
    assert pipelineRoutes._fbLatchStaleAiDeclaration(
        dictWorkflow, sRepo) is False


@pytest.mark.falsification
def test_the_poll_snapshot_hashes_every_covered_file(tSignedProject):
    """The poll reads freshness from its one-exec snapshot, so every
    covered path must be in the snapshot's hash batch -- an unsampled
    path reads as could-not-check.

    Kills: pipelineRoutes leaving the covered files out of the snapshot
    batch.
    """
    from vaibify.gui.routes import pipelineRoutes
    sRepo, dictWorkflow = tSignedProject
    listPaths = pipelineRoutes._flistPollHashRelPaths(
        dictWorkflow, sRepo, "")
    for sPath in (S_SCRIPT, S_OUTPUT, S_INPUT):
        assert sPath in listPaths


def _fdictSignOffUpdate(dictWorkflow, sUser, dictExtra=None):
    dictVerification = dict(
        dictWorkflow["listSteps"][1]["dictVerification"], sUser=sUser)
    dictVerification.update(dictExtra or {})
    return {"dictVerification": dictVerification}


def test_the_sign_off_records_a_baseline_and_ignores_a_client_copy(
    tmp_path,
):
    from vaibify.gui.routes.stepRoutes import _fnRecordDeclarationBaseline
    sRepo = str(tmp_path / "repo")
    _fnWrite(sRepo, S_SCRIPT, "print('values')\n")
    dictWorkflow = _fdictWorkflow(sRepo)
    dictForged = {S_BASELINE_KEY: {"dictCoveredFiles": {}},
                  "listDeclarationChanges": []}
    dictUpdates = _fdictSignOffUpdate(dictWorkflow, "passed", dictForged)
    _fnRecordDeclarationBaseline(sRepo, dictWorkflow, 1, dictUpdates)
    dictBaseline = dictUpdates["dictVerification"][S_BASELINE_KEY]
    assert set(dictBaseline["dictCoveredFiles"]) == {
        S_SCRIPT, S_OUTPUT, S_INPUT}
    assert dictBaseline["dictCoveredFiles"][S_OUTPUT]["sSha256"] == ""
    assert "listDeclarationChanges" not in dictUpdates["dictVerification"]


@pytest.mark.falsification
def test_an_unchanged_sign_off_keeps_the_stored_baseline(tSignedProject):
    """Kills: declarationFreshness taking a client-supplied baseline."""
    from vaibify.gui.routes.stepRoutes import _fnRecordDeclarationBaseline
    sRepo, dictWorkflow = tSignedProject
    dictStored = dictWorkflow["listSteps"][1]["dictVerification"][
        S_BASELINE_KEY]
    dictUpdates = _fdictSignOffUpdate(
        dictWorkflow, "passed", {S_BASELINE_KEY: {"forged": True}})
    _fnWrite(sRepo, S_OUTPUT, '{"edited": true}\n')
    _fnRecordDeclarationBaseline(sRepo, dictWorkflow, 1, dictUpdates)
    assert dictUpdates["dictVerification"][S_BASELINE_KEY] == dictStored


def test_signing_off_a_stale_declaration_records_a_new_baseline(
    tSignedProject,
):
    from vaibify.gui.routes.stepRoutes import _fnRecordDeclarationBaseline
    sRepo, dictWorkflow = tSignedProject
    _fnWrite(sRepo, S_OUTPUT, '{"a": 5}\n')
    fbLatchStaleDeclaration(
        dictWorkflow, fdictEvaluateDeclarationFreshness(dictWorkflow, sRepo))
    dictUpdates = _fdictSignOffUpdate(dictWorkflow, "passed")
    _fnRecordDeclarationBaseline(sRepo, dictWorkflow, 1, dictUpdates)
    dictWorkflow["listSteps"][1]["dictVerification"] = dictUpdates[
        "dictVerification"]
    assert _fsVerdict(dictWorkflow, sRepo) == "fresh"


@pytest.mark.falsification
@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root reads a mode-000 file, so it cannot be made unreadable",
)
def test_the_cross_poll_cache_tells_deleted_from_unreadable(
    tSignedProject,
):
    """The published-files fingerprint omits a file it cannot hash,
    whether absent or unreadable, so those two states share a cache key
    unless the freshness verdict is part of it. Deleted is stale;
    unreadable is could-not-check, and the cached answer must move.

    Kills: levelGates dropping the freshness verdict from the L2 cache key.
    """
    sRepo, dictWorkflow = tSignedProject
    sPath = os.path.join(sRepo, S_INPUT)
    levelGates.fnClearLevelBlockerCache()
    os.remove(sPath)

    def flistCriteria():
        return [
            d["sCriterion"]
            for d in levelGates.flistLevel2Blockers(dictWorkflow, sRepo)
            if d["iStepIndex"] == 1]

    assert flistCriteria() == ["ai-declaration-stale"]
    _fnWrite(sRepo, S_INPUT, "1,2,3\n")
    os.chmod(sPath, 0)
    try:
        listCriteria = flistCriteria()
    finally:
        os.chmod(sPath, 0o600)
    assert listCriteria == ["ai-declaration-uncheckable"]


@pytest.mark.falsification
def test_the_poll_snapshot_reports_a_file_it_saw_absent(tmp_path):
    """The snapshot program answers ``bMissing`` for a missing file; the
    snapshot adapter must pass it on, or every output a step has not
    produced yet reads as could-not-check on the poll while the
    readiness route, reading live, calls it absent.

    Kills: SnapshotRepoFiles.fdictHashFiles dropping bMissing.
    """
    from vaibify.reproducibility.repoFiles import SnapshotRepoFiles
    filesSnapshot = SnapshotRepoFiles(str(tmp_path), {}, {
        "seen/absent.json": {"sSha256": None, "bMissing": True},
        "seen/locked.json": {"sSha256": None},
    })
    dictEntries = filesSnapshot.fdictHashFiles(
        ["seen/absent.json", "seen/locked.json", "never/sampled.json"])
    assert dictEntries["seen/absent.json"].get("bMissing") is True
    assert "bMissing" not in dictEntries["seen/locked.json"]
    assert "bMissing" not in dictEntries["never/sampled.json"]


@pytest.mark.parametrize("sCommand,sScriptName", [
    ("bash runModel.sh --fast", "runModel.sh"),
    ("Rscript fitCurve.R", "fitCurve.R"),
    ("julia simulate.jl 10", "simulate.jl"),
    ('python "make values.py" --n 3', "make values.py"),
])
def test_a_script_in_any_language_is_covered(
    tmp_path, sCommand, sScriptName,
):
    """The sign-off covers the script a step RUNS, whatever its language
    and however its path is quoted; a Python-only reader let a change
    to every other script pass as fresh."""
    sRepo = str(tmp_path / "repo")
    sScript = S_PRODUCER_DIRECTORY + "/" + sScriptName
    _fnWrite(sRepo, sScript, "first version\n")
    dictWorkflow = _fdictWorkflow(sRepo)
    dictWorkflow["listSteps"][0]["saDataCommands"] = [sCommand]
    dictVerification = dictWorkflow["listSteps"][1]["dictVerification"]
    dictVerification["sUser"] = "passed"
    dictVerification[S_BASELINE_KEY] = fdictBuildDeclarationBaseline(
        dictWorkflow, sRepo, "")
    _fnWrite(sRepo, sScript, "second version\n")
    dictVerdict = fdictEvaluateDeclarationFreshness(dictWorkflow, sRepo)
    assert dictVerdict["sVerdict"] == "stale"
    assert [d["sPath"] for d in dictVerdict["listChanges"]] == [sScript]


@pytest.mark.falsification
def test_readiness_latches_what_it_sees_so_a_revert_stays_stale(
    tSignedProject,
):
    """Through the real route: readiness sees the change, the file is put
    back, and readiness must STILL refuse -- the observation latched.

    Kills: the readiness route answering without latching.
    """
    sRepo, dictWorkflow = tSignedProject
    listSaved = []
    app = FastAPI()
    levelRoutes.fnRegisterAll(app, {
        "docker": None, "workflows": {S_CONTAINER_KEY: dictWorkflow},
        "paths": {}, "require": lambda *aArgs: None,
        "save": lambda sId, dictWf: listSaved.append(sId),
    })
    client = TestClient(app)
    sUrl = f"/api/workflow/{S_CONTAINER_KEY}/level2/readiness"
    _fnWrite(sRepo, S_SCRIPT, "print('changed')\n")
    assert client.get(sUrl).json()["dictLevel2Gaps"][
        "bAiDeclarationAttested"] is False
    assert listSaved == [S_CONTAINER_KEY]
    _fnWrite(sRepo, S_SCRIPT, "print('values')\n")
    levelGates.fnClearLevelBlockerCache()
    dictBody = client.get(sUrl).json()
    assert dictBody["dictLevel2Gaps"]["bAiDeclarationAttested"] is False
    assert dictBody["dictAiDeclarationFreshness"]["sVerdict"] == "stale"


@pytest.mark.falsification
def test_a_changed_shell_script_makes_the_sign_off_stale(tmp_path):
    """The flags put the script beyond the first two tokens, so only the
    interpreter table can find it -- and for a shell, -e is errexit.

    Kills: recognizing only Python interpreters as running a script.
    """
    sRepo = str(tmp_path / "repo")
    sScript = S_PRODUCER_DIRECTORY + "/runModel.sh"
    _fnWrite(sRepo, sScript, "echo first\n")
    dictWorkflow = _fdictWorkflow(sRepo)
    dictWorkflow["listSteps"][0]["saDataCommands"] = [
        "bash -e -x runModel.sh"]
    dictVerification = dictWorkflow["listSteps"][1]["dictVerification"]
    dictVerification["sUser"] = "passed"
    dictVerification[S_BASELINE_KEY] = fdictBuildDeclarationBaseline(
        dictWorkflow, sRepo, "")
    _fnWrite(sRepo, sScript, "echo second\n")
    assert _fsVerdict(dictWorkflow, sRepo) == "stale"
