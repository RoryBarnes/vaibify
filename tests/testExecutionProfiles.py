"""Execution profiles are applied, observed, recorded and replayed.

The rules come from the design (``design/cpuClassReproducibility.md``,
F7-F9 and I6, I10) and the build guide's attack surface item 2, not from
the code under test:

* Stage A reads the processor with every allowlisted setting unset;
  Stage B, the probe, and every step then see exactly the applied
  settings (I10);
* a processor below the profile's level drops the floor keys, keeps the
  thread pins, says why, and the author is told before the run;
* the probe reports an absent NumPy or an undetectable OpenBLAS as such
  and changes nothing;
* the shadow replays the envelope's recorded profile, never the table's
  current entry (F9);
* a recorded profile naming a setting, value or floor key outside the
  allowlist is refused by the host and by ``reproduce.sh``, naming it;
* changing the answer retires the attestation, which stays on file (F8).

The runs go through the REAL runner and lane against real directories
(``tests/assessedRerunLaneHarness.py``); only Docker is absent.
"""

import asyncio
import json
import os
import subprocess
import sys

import pytest

from tests.assessedRerunLaneHarness import (
    S_LANE_CONTAINER_ID,
    LocalRunnerContainer,
    fdictRerunThroughTheLane,
)
from tests.levelGateStubs import fdictCurrentAssessmentFields
from tests.reproduceScriptHarness import fdictRunContainerHalf
from tests.rerunAssessmentHarness import (
    fdictBuildStep,
    fdictBuildWorkflow,
    fnPinManifest,
    fnWriteText,
    fsWriteJsonCommand,
)
from vaibify.gui import determinismEnvironment, pipelineRunner
from vaibify.reproducibility import (
    executionProfiles,
    l3Attestation,
    rerunAssessment,
    rerunProcedure,
)
from vaibify.reproducibility.reproduceScriptGenerator import (
    ftRenderReproductionFiles,
)


TUPLE_KEYS = rerunProcedure.TUPLE_EXECUTION_SETTING_KEYS
S_VALUES = "values.json"
S_SEEN = "seen.json"
S_ACROSS = executionProfiles.S_PROFILE_ACROSS
_S_DUMP_THE_SETTINGS = (
    "python3 -c \"import json, os; json.dump({k: os.environ.get(k) for k "
    "in " + repr(list(TUPLE_KEYS)) + "}, open('" + S_SEEN + "', 'w'))\"")
_DICT_V2_CAPABILITIES = {
    "sArchitecture": "x86_64", "sVendor": "GenuineIntel",
    "sLevel": "x86-64-v2", "iVisibleCores": 4, "bReadable": True,
}


def _fdictSettingsStep(sRoot):
    """Return a step that writes a value and records the settings it saw."""
    fnWriteText(sRoot, f"StepOne/{S_VALUES}", json.dumps({"x": 1.0}))
    return fdictBuildStep(
        "Step one", "StepOne",
        saDataCommands=[fsWriteJsonCommand(S_VALUES, {"x": 1.0}),
                        _S_DUMP_THE_SETTINGS],
        saOutputDataFiles=[S_VALUES])


def _fsRepository(tmp_path):
    """Return a fresh repository directory."""
    sRoot = str(tmp_path / "repo")
    os.makedirs(sRoot, exist_ok=True)
    return sRoot


def _fdictPublish(sRoot, listSteps, dictRecordedProfile=None,
                  sAnswer=None):
    """Publish steps with an envelope recording a profile; return the workflow."""
    dictWorkflow = fdictBuildWorkflow(listSteps)
    dictWorkflow["sProjectRepoPath"] = sRoot
    if sAnswer:
        dictWorkflow["dictDeterminism"] = {
            executionProfiles.S_EXECUTION_PROFILE_KEY: sAnswer}
    fnWriteText(sRoot, ".vaibify/workflows/project.json",
                json.dumps(dictWorkflow))
    dictEnvelope = {"dictContainer": {"sImageDigest": "img@sha256:" + "a" * 64}}
    if dictRecordedProfile is not None:
        dictEnvelope["dictExecutionProfile"] = dictRecordedProfile
    fnWriteText(sRoot, ".vaibify/environment.json", json.dumps(dictEnvelope))
    fnPinManifest(sRoot, dictWorkflow)
    return dictWorkflow


def _fdictReadSeen(sRoot):
    """Return the allowlisted settings the step saw, keyed by name."""
    with open(os.path.join(sRoot, "StepOne", S_SEEN)) as fileIn:
        return {sKey: sValue for sKey, sValue in json.load(fileIn).items()
                if sValue is not None}


def _fdictContexts(dictOutcome):
    """Return the run context the assessment report carries."""
    return dictOutcome["dictAssessmentReport"]["dictContexts"]


def _fnPretendTheProcessorIsV2(monkeypatch):
    """Make Stage A report an x86-64-v2 processor; Stage B stays real."""
    fdictOriginal = determinismEnvironment._fdictRunProcedureStage

    def fdictStage(connectionDocker, sContainerId, sProjectRepoPath,
                   listCommand, sPrefix):
        if sPrefix == rerunProcedure.S_CAPABILITIES_RESULT_PREFIX:
            return {"dictCapabilities": dict(_DICT_V2_CAPABILITIES),
                    "listSettingsPresent": []}
        return fdictOriginal(connectionDocker, sContainerId,
                             sProjectRepoPath, listCommand, sPrefix)

    monkeypatch.setattr(
        determinismEnvironment, "_fdictRunProcedureStage", fdictStage)


def _flistRunTheAuthorsRun(sRoot, dictWorkflow):
    """Run every step through the real runner; return the status events."""
    listEvents = []

    async def fnCollect(dictEvent):
        listEvents.append(dictEvent)

    sWorkflowPath = os.path.join(sRoot, ".vaibify", "workflows",
                                 "project.json")
    asyncio.run(pipelineRunner.fiRunAllSteps(
        LocalRunnerContainer(), S_LANE_CONTAINER_ID, dictWorkflow,
        sWorkflowPath, os.path.dirname(sWorkflowPath), fnCollect))
    return listEvents


def _fdictRunTheProbe(tmp_path, dictEnvironment, sFakeNumpy=""):
    """Run the probe as a step's interpreter would; return its record."""
    dictRun = dict(os.environ)
    for sKey in TUPLE_KEYS:
        dictRun.pop(sKey, None)
    dictRun.update(dictEnvironment)
    if sFakeNumpy:
        sPackage = tmp_path / "fakePackages" / "numpy"
        sPackage.mkdir(parents=True)
        (sPackage / "__init__.py").write_text(sFakeNumpy)
        dictRun["PYTHONPATH"] = str(tmp_path / "fakePackages")
    completed = subprocess.run(
        [sys.executable, "-c", rerunAssessment.fsBuildProcedureProgram(),
         "probe", "--machine"],
        capture_output=True, text=True, env=dictRun, cwd=str(tmp_path))
    for sLine in completed.stdout.splitlines():
        if sLine.startswith(rerunProcedure.S_PROBE_RESULT_PREFIX):
            return json.loads(sLine[len(rerunProcedure.S_PROBE_RESULT_PREFIX):])
    raise AssertionError(completed.stdout + completed.stderr)


# ----------------------------------------------------------------------
# The table
# ----------------------------------------------------------------------


def testTheTableHoldsTheDesignedProfiles():
    """``speed-v1`` requests nothing; ``across-v1`` is the guide's floor."""
    assert executionProfiles.fdictProfileFromTable("speed-v1") == {
        "sProfileId": "speed-v1", "dictRequested": {}, "sRequiredLevel": "",
        "listFloorKeys": []}
    assert executionProfiles.fdictProfileFromTable(S_ACROSS) == {
        "sProfileId": S_ACROSS,
        "dictRequested": {
            "OPENBLAS_CORETYPE": "Haswell",
            "NPY_DISABLE_CPU_FEATURES": "AVX512_ICL,X86_V4",
            "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1"},
        "sRequiredLevel": "x86-64-v3",
        "listFloorKeys": ["OPENBLAS_CORETYPE", "NPY_DISABLE_CPU_FEATURES"]}
    assert executionProfiles.fsSelectedProfileId({}) == "speed-v1"


# ----------------------------------------------------------------------
# Order (I10) and the not-applied path
# ----------------------------------------------------------------------


@pytest.mark.falsification
def testEachStageSeesWhatTheOrderPromises(tmp_path, monkeypatch):
    """Stage A sees no allowlisted setting; the probe and the steps, the applied.

    The image's own environment carries two of the settings, with values
    of its own; the profile's must replace them, and the reading of the
    processor must see neither.

    Kills: Stage A run without unsetting the allowlisted settings.
    """
    monkeypatch.setenv("OPENBLAS_NUM_THREADS", "7")
    monkeypatch.setenv("OPENBLAS_CORETYPE", "Prescott")
    sRoot = _fsRepository(tmp_path)
    dictWorkflow = _fdictPublish(
        sRoot, [_fdictSettingsStep(sRoot)],
        executionProfiles.fdictProfileFromTable(S_ACROSS))
    dictOutcome = fdictRerunThroughTheLane(sRoot, dictWorkflow)
    dictSettings = _fdictContexts(dictOutcome)["dictCapabilities"]
    assert dictSettings["dictCapabilities"]["listSettingsPresent"] == []
    dictApplied = dictSettings["dictApplied"]
    assert dictApplied["OPENBLAS_NUM_THREADS"] == "1"
    assert _fdictContexts(dictOutcome)["dictProbe"]["dictSettingsSeen"] == (
        dictApplied)
    assert _fdictReadSeen(sRoot) == dictApplied


@pytest.mark.falsification
def testAProcessorBelowTheLevelGetsThePinsAndNotTheFloor(
    tmp_path, monkeypatch,
):
    """Neither the probe nor any step receives a floor key the processor lacks.

    The image sets one floor key of its own; it must not stand in for
    the floor the processor cannot honor.

    Kills: building the prefix without unsetting the dropped keys.
    """
    monkeypatch.setenv("OPENBLAS_CORETYPE", "SkylakeX")
    _fnPretendTheProcessorIsV2(monkeypatch)
    sRoot = _fsRepository(tmp_path)
    dictWorkflow = _fdictPublish(
        sRoot, [_fdictSettingsStep(sRoot)],
        executionProfiles.fdictProfileFromTable(S_ACROSS))
    dictOutcome = fdictRerunThroughTheLane(sRoot, dictWorkflow)
    dictSettings = _fdictContexts(dictOutcome)["dictCapabilities"]
    assert dictSettings["dictDecision"]["sReason"] == (
        "floor not applied: this processor lacks x86-64-v3")
    dictPins = {"OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1"}
    assert dictSettings["dictApplied"] == dictPins
    assert _fdictContexts(dictOutcome)["dictProbe"]["dictSettingsSeen"] == (
        dictPins)
    assert _fdictReadSeen(sRoot) == dictPins


@pytest.mark.falsification
def testTheDecisionKeepsThePinsWhenTheFloorIsDropped():
    """A v2 processor keeps every thread pin and drops exactly the floor.

    Kills: dropping every requested setting along with the floor.
    """
    dictSettings = executionProfiles.fdictDecideExecutionSettings(
        executionProfiles.fdictProfileFromTable(S_ACROSS),
        dict(_DICT_V2_CAPABILITIES))
    assert dictSettings["listDropped"] == [
        "NPY_DISABLE_CPU_FEATURES", "OPENBLAS_CORETYPE"]
    assert dictSettings["dictApplied"] == {
        "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1"}
    assert dictSettings["dictRequested"]["OPENBLAS_CORETYPE"] == "Haswell"


@pytest.mark.falsification
def testTheAuthorIsWarnedBeforeARunWithoutTheFloor(tmp_path, monkeypatch):
    """The floor not applied is a run-start warning, naming why.

    Kills: collecting the run-start warnings without the profile's.
    """
    _fnPretendTheProcessorIsV2(monkeypatch)
    sRoot = _fsRepository(tmp_path)
    dictWorkflow = _fdictPublish(
        sRoot, [_fdictSettingsStep(sRoot)], sAnswer=S_ACROSS)
    listEvents = _flistRunTheAuthorsRun(sRoot, dictWorkflow)
    listWarnings = [dictEvent["sMessage"] for dictEvent in listEvents
                    if dictEvent.get("sType") == "preflightWarning"]
    assert any("floor not applied: this processor lacks x86-64-v3" in s
               for s in listWarnings), listWarnings
    assert _fdictReadSeen(sRoot) == {
        "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1"}


@pytest.mark.falsification
def testAProfileThisVaibifyLacksStopsTheRunBeforeAnyStep(tmp_path):
    """An answer naming no profile here is refused, never run under another.

    Kills: running the steps when the profile cannot be looked up.
    """
    sRoot = _fsRepository(tmp_path)
    dictWorkflow = _fdictPublish(
        sRoot, [_fdictSettingsStep(sRoot)], sAnswer="across-v9")
    listEvents = _flistRunTheAuthorsRun(sRoot, dictWorkflow)
    listFailed = [dictEvent for dictEvent in listEvents
                  if dictEvent.get("sType") == "preflightFailed"]
    assert listFailed, listEvents[-3:]
    assert "across-v9" in listFailed[0]["listErrors"][0]
    assert "upgrade vaibify" in listFailed[0]["listErrors"][0]
    assert not os.path.exists(os.path.join(sRoot, "StepOne", S_SEEN))


# ----------------------------------------------------------------------
# The probe reports what it finds, and changes nothing
# ----------------------------------------------------------------------


@pytest.mark.falsification
def testAnImageWithoutNumpyIsSaid(tmp_path):
    """Kills: recording an absent NumPy as installed."""
    dictProbe = _fdictRunTheProbe(
        tmp_path, {"OPENBLAS_NUM_THREADS": "1"},
        "raise ImportError('no NumPy in this image')\n")
    assert dictProbe["sNumpyState"] == "NumPy not installed"
    assert dictProbe["dictSettingsSeen"] == {"OPENBLAS_NUM_THREADS": "1"}


@pytest.mark.falsification
def testANumpyWithoutBundledOpenblasIsSaid(tmp_path):
    """No ``numpy.libs`` OpenBLAS: "not detected", with NumPy's own BLAS name.

    Kills: assuming a bundled OpenBLAS is always there.
    """
    dictProbe = _fdictRunTheProbe(
        tmp_path, {"OMP_NUM_THREADS": "1"},
        "__version__ = '0.0'\n"
        "class __config__:\n"
        "    CONFIG = {'Build Dependencies': {'blas': {'name': 'accelerate'}}}\n")
    assert dictProbe["sNumpyState"] == "installed"
    assert dictProbe["dictOpenblas"] == {
        "sState": "OpenBLAS not detected", "sReportedBlas": "accelerate"}
    assert dictProbe["dictSettingsSeen"] == {"OMP_NUM_THREADS": "1"}


# ----------------------------------------------------------------------
# Replay and recording
# ----------------------------------------------------------------------


@pytest.mark.falsification
def testTheShadowReplaysTheRecordedProfileNotTheTable(tmp_path, monkeypatch):
    """F9: the envelope's values run, whatever the table and the answer say now.

    A test-only ``across-v2`` joins the table, ``across-v1``'s entry is
    changed under it, and the workflow now answers ``across-v2``; the
    envelope still records ``across-v1`` as it was.

    Kills: looking the recorded profile up in the current table.
    """
    dictRecorded = executionProfiles.fdictProfileFromTable(S_ACROSS)
    dictTable = {sKey: dict(dictValue) for sKey, dictValue in
                 executionProfiles.DICT_EXECUTION_PROFILES.items()}
    dictTable[S_ACROSS]["dictRequested"] = dict(
        dictTable[S_ACROSS]["dictRequested"], OPENBLAS_NUM_THREADS="2")
    dictTable["across-v2"] = {
        "dictRequested": {"OMP_NUM_THREADS": "3"}, "sRequiredLevel": "",
        "listFloorKeys": []}
    monkeypatch.setattr(executionProfiles, "DICT_EXECUTION_PROFILES", dictTable)
    sRoot = _fsRepository(tmp_path)
    dictWorkflow = _fdictPublish(
        sRoot, [_fdictSettingsStep(sRoot)], dictRecorded, sAnswer="across-v2")
    dictOutcome = fdictRerunThroughTheLane(sRoot, dictWorkflow)
    dictSeen = _fdictReadSeen(sRoot)
    assert dictSeen["OPENBLAS_NUM_THREADS"] == "1"
    assert dictSeen["OMP_NUM_THREADS"] == "1"
    assert dictOutcome["sProfileId"] == S_ACROSS
    assert dictOutcome["dictRequested"] == dictRecorded["dictRequested"]


@pytest.mark.falsification
def testTheEnvelopeRecordsTheProfileTheAnswerNames(tmp_path):
    """I6: the concrete profile is written beside the epoch.

    Kills: recording a profile other than the workflow's answer.
    """
    from unittest.mock import patch
    from vaibify.reproducibility.dataArchiver import (
        _fdictBuildEnvironmentPayload,
    )
    with patch(
        "vaibify.reproducibility.environmentSnapshot."
        "fdictCaptureContainerImageDigest", return_value={"sImageDigest": ""},
    ), patch(
        "vaibify.reproducibility.environmentSnapshot.fdictCaptureSystemTools",
        return_value={},
    ):
        dictPayload = _fdictBuildEnvironmentPayload(
            str(tmp_path), "container", None,
            {"dictDeterminism": {"sExecutionProfile": S_ACROSS}})
    assert dictPayload["dictExecutionProfile"] == (
        executionProfiles.fdictProfileFromTable(S_ACROSS))


@pytest.mark.falsification
def testChangingTheAnswerRetiresTheAttestationAndKeepsIt(tmp_path):
    """F8: a record made under ``speed-v1`` is not current once ``across-v1`` is asked.

    Kills: a currency check that ignores the record's profile.
    """
    sRoot = _fsRepository(tmp_path)
    fnWriteText(sRoot, "MANIFEST.sha256", "# vaibify manifest v1\n")
    l3Attestation.fnWriteAttestation(sRoot, l3Attestation.fdictBuildAttestation(
        "passed", l3Attestation.fsCurrentManifestDigest(sRoot),
        "img@sha256:i", 1.0, 1, 1,
        dictAssessmentFields=fdictCurrentAssessmentFields()))
    assert l3Attestation.fbL3AttestationCurrent(sRoot, {}) is True
    dictAnswered = {"dictDeterminism": {"sExecutionProfile": S_ACROSS}}
    assert l3Attestation.fbL3AttestationCurrent(sRoot, dictAnswered) is False
    dictKept = l3Attestation.fdictReadAttestation(sRoot)
    assert dictKept["sStatus"] == "passed"
    sReason = l3Attestation.fsDescribeProfileMismatch(dictKept, dictAnswered)
    assert "speed-v1" in sReason and S_ACROSS in sReason
    assert "regenerate the reproducibility envelope" in sReason


# ----------------------------------------------------------------------
# The allowlist holds: on the host
# ----------------------------------------------------------------------


def _fdictRerunWithARecordedProfile(tmp_path, dictChanges):
    """Run the lane over an envelope whose profile carries ``dictChanges``."""
    dictRecorded = executionProfiles.fdictProfileFromTable(S_ACROSS)
    for sKey, objValue in dictChanges.items():
        if sKey == "dictRequested":
            dictRecorded["dictRequested"].update(objValue)
        else:
            dictRecorded[sKey] = objValue
    sRoot = _fsRepository(tmp_path)
    dictWorkflow = _fdictPublish(
        sRoot, [_fdictSettingsStep(sRoot)], dictRecorded)
    return sRoot, fdictRerunThroughTheLane(sRoot, dictWorkflow)


def _fnAssertRefusedNaming(sRoot, dictOutcome, sNamed):
    """The rerun refused before any step, and the reason names the culprit."""
    assert dictOutcome["bRerunAttempted"] is False
    sReason = " ".join(dictOutcome["listDivergedHashes"])
    assert sNamed in sReason, sReason
    assert not os.path.exists(os.path.join(sRoot, "StepOne", S_SEEN))


@pytest.mark.falsification
def testTheHostRefusesASettingOutsideTheAllowlist(tmp_path):
    """Kills: admitting any setting a recorded profile names."""
    sRoot, dictOutcome = _fdictRerunWithARecordedProfile(
        tmp_path, {"dictRequested": {"LD_PRELOAD": "x"}})
    _fnAssertRefusedNaming(sRoot, dictOutcome, "LD_PRELOAD")


@pytest.mark.falsification
def testTheHostRefusesAValueOutsideThePattern(tmp_path):
    """Kills: checking a value's type but not its characters."""
    sRoot, dictOutcome = _fdictRerunWithARecordedProfile(
        tmp_path, {"dictRequested": {"OMP_NUM_THREADS": "$(touch x)"}})
    _fnAssertRefusedNaming(sRoot, dictOutcome, "OMP_NUM_THREADS")


@pytest.mark.falsification
def testTheHostRefusesAFloorKeyOutsideTheAllowlist(tmp_path):
    """Kills: admitting any floor key a recorded profile names."""
    sRoot, dictOutcome = _fdictRerunWithARecordedProfile(
        tmp_path, {"listFloorKeys": ["PYTHONPATH"]})
    _fnAssertRefusedNaming(sRoot, dictOutcome, "PYTHONPATH")


# ----------------------------------------------------------------------
# The allowlist holds: in reproduce.sh
# ----------------------------------------------------------------------


def _ftRunTheHostHalf(tmp_path, dictRecordedProfile):
    """Run reproduce.sh's host half with a recording docker; return the result."""
    sRoot = str(tmp_path / "project")
    os.makedirs(os.path.join(sRoot, ".vaibify"))
    dictWorkflow = fdictBuildWorkflow([fdictBuildStep(
        "Step one", "StepOne", saDataCommands=["true"])])
    for sRelative, sText in ftRenderReproductionFiles(dictWorkflow):
        fnWriteText(sRoot, sRelative, sText)
    fnWriteText(sRoot, ".vaibify/environment.json", json.dumps({
        "dictContainer": {"sImageDigest": "img@sha256:" + "a" * 64},
        "iSourceDateEpoch": 1700000000,
        "dictExecutionProfile": dictRecordedProfile}))
    sBin = tmp_path / "bin"
    sBin.mkdir()
    sRecord = tmp_path / "argv.txt"
    (sBin / "docker").write_text(
        "#!/usr/bin/env bash\n" f'printf "%s\\n" "$*" >> {sRecord}\nexit 0\n')
    (sBin / "docker").chmod(0o755)
    dictEnvironment = dict(os.environ, PATH=f"{sBin}{os.pathsep}{os.environ['PATH']}")
    completed = subprocess.run(
        ["bash", "reproduce.sh"], cwd=sRoot, env=dictEnvironment,
        capture_output=True, text=True, timeout=120)
    sArgv = sRecord.read_text() if sRecord.exists() else ""
    return completed, sArgv


def _fdictAcrossWith(dictChanges):
    """Return ``across-v1`` with some fields changed."""
    dictProfile = executionProfiles.fdictProfileFromTable(S_ACROSS)
    dictProfile.update(dictChanges)
    return dictProfile


def _fnAssertTheScriptRefused(completed, sArgv, sNamed):
    """Exit 4, the key named, and no container ever started."""
    assert completed.returncode == 4, completed.stderr
    assert sNamed in completed.stderr
    assert "Could not run: the recorded execution profile" in completed.stderr
    assert not any(sLine.startswith("run ") for sLine in sArgv.splitlines())


def testTheScriptPassesTheRecordedProfileToTheContainer(tmp_path):
    """The validated settings, level and floor keys ride as ``-e`` words."""
    completed, sArgv = _ftRunTheHostHalf(
        tmp_path, executionProfiles.fdictProfileFromTable(S_ACROSS))
    assert completed.returncode == 0, completed.stderr
    sRun = [sLine for sLine in sArgv.splitlines() if sLine.startswith("run ")][0]
    for sWord in ("-e OPENBLAS_CORETYPE=Haswell",
                  "-e NPY_DISABLE_CPU_FEATURES=AVX512_ICL,X86_V4",
                  "-e VAIBIFY_REQUIRED_LEVEL=x86-64-v3",
                  "-e VAIBIFY_FLOOR_KEYS=OPENBLAS_CORETYPE,NPY_DISABLE_CPU_FEATURES"):
        assert sWord in sRun, sRun


@pytest.mark.falsification
def testTheScriptRefusesASettingOutsideTheAllowlist(tmp_path):
    """Kills: the script passing any recorded setting through."""
    completed, sArgv = _ftRunTheHostHalf(tmp_path, _fdictAcrossWith(
        {"dictRequested": {"LD_PRELOAD": "x"}}))
    _fnAssertTheScriptRefused(completed, sArgv, "LD_PRELOAD")


@pytest.mark.falsification
def testTheScriptRefusesAValueOutsideThePattern(tmp_path):
    """Kills: the script passing a recorded value without checking it."""
    completed, sArgv = _ftRunTheHostHalf(tmp_path, _fdictAcrossWith(
        {"dictRequested": {"OMP_NUM_THREADS": "$(touch x)"}}))
    _fnAssertTheScriptRefused(completed, sArgv, "OMP_NUM_THREADS")


@pytest.mark.falsification
def testTheScriptRefusesAFloorKeyOutsideTheAllowlist(tmp_path):
    """Kills: the script passing any recorded floor key through."""
    completed, sArgv = _ftRunTheHostHalf(tmp_path, _fdictAcrossWith(
        {"listFloorKeys": ["PYTHONPATH"]}))
    _fnAssertTheScriptRefused(completed, sArgv, "PYTHONPATH")


@pytest.mark.falsification
def testTheScriptUnsetsOnlyAllowlistedKeysStageANames(tmp_path):
    """A key the capability reading names outside the allowlist stops the run.

    Kills: unsetting whatever the reading prints, which would let a
    tampered assessor unset ``PATH`` or any other variable.
    """
    sRoot = str(tmp_path / "repo")
    sBase = str(tmp_path / "run")
    os.makedirs(sBase)
    fnWriteText(sRoot, f"StepOne/{S_VALUES}", json.dumps({"x": 1.0}))
    dictWorkflow = fdictBuildWorkflow([fdictBuildStep(
        "Step one", "StepOne",
        saDataCommands=[fsWriteJsonCommand(S_VALUES, {"x": 1.0})],
        saOutputDataFiles=[S_VALUES])])
    for sRelative, sText in ftRenderReproductionFiles(dictWorkflow):
        fnWriteText(sRoot, sRelative, sText)
    fnPinManifest(sRoot, dictWorkflow)

    def fsNameAForeignKey(sBody):
        sReading = 'if sVaibifyDropped=$(env '
        assert sBody.count(sReading) == 1
        return sBody.replace(sReading, "if sVaibifyDropped=$(printf 'PATH\\n'; env ")

    dictRun = fdictRunContainerHalf(sRoot, sBase, fsEditBody=fsNameAForeignKey)
    assert dictRun["iExitCode"] == 4, dictRun["sStderr"]
    assert "the capability reading named PATH" in dictRun["sStderr"]


# ----------------------------------------------------------------------
# A dashboard test run gets the same decision as a pipeline run
# ----------------------------------------------------------------------


class _RecordingTestConnection:
    """Answers Stage A with a v3 processor and records every other command."""

    def __init__(self):
        self.listCommands = []

    def ftRunInContainerStreamed(self, sContainerId, sCommand, **dictOptions):
        from types import SimpleNamespace
        del sContainerId, dictOptions
        if " 'capabilities' " in sCommand:
            dictRecord = {"dictCapabilities": dict(
                _DICT_V2_CAPABILITIES, sLevel="x86-64-v3"),
                "listSettingsPresent": []}
            return SimpleNamespace(
                iExitCode=0, sStderr="", sStdout=(
                    rerunProcedure.S_CAPABILITIES_RESULT_PREFIX
                    + json.dumps(dictRecord) + "\n"))
        self.listCommands.append(sCommand)
        return SimpleNamespace(iExitCode=0, sStdout="ok", sStderr="")


@pytest.mark.falsification
def testADashboardTestRunGetsTheProfilesSettings():
    """"Run tests" runs under the settings a pipeline run of the step gets.

    Kills: building the test command without the Stage A decision.
    """
    from vaibify.gui.routes import testRoutes
    connection = _RecordingTestConnection()
    dictWorkflow = {"sProjectRepoPath": "/workspace/project",
                    "dictDeterminism": {"sExecutionProfile": S_ACROSS}}
    testRoutes._ftRunCategoryCommands(
        connection, "testRunContainer",
        {"sDirectory": "StepOne"}, dictWorkflow, ["pytest -q"], "integrity")
    sCommand = connection.listCommands[0]
    assert "export OPENBLAS_CORETYPE='Haswell' && " in sCommand, sCommand
    assert "export OPENBLAS_NUM_THREADS='1' && " in sCommand
    assert sCommand.index("OPENBLAS_CORETYPE") < sCommand.index("pytest -q")


@pytest.mark.falsification
def testADashboardTestRunOfAProfileNotKnownHereIsRefused():
    """Nothing runs under settings the researcher did not ask for.

    Kills: letting a profile this vaibify lacks through to the test run.
    """
    from fastapi import HTTPException
    from vaibify.gui.routes import testRoutes
    with pytest.raises(HTTPException) as errorRefused:
        testRoutes._fnRefuseAnExecutionProfileNotKnownHere(
            {"dictDeterminism": {"sExecutionProfile": "across-v9"}})
    assert errorRefused.value.status_code == 409
    assert "across-v9" in errorRefused.value.detail
    testRoutes._fnRefuseAnExecutionProfileNotKnownHere({})


def testAHostProjectReadsThisMachineAndRunsNoProbe(monkeypatch):
    """Host mode: Stage A in this process, the pins applied, no probe.

    The helper interpreter on the host is vaibify's own, so a probe
    would describe the wrong NumPy; it says so instead of running.
    """
    from vaibify.config import registryManager
    monkeypatch.setattr(registryManager, "fbIsHostProject", lambda s: True)
    dictSettings = determinismEnvironment.fdictDecideRunExecutionSettings(
        None, "hostProject", "", executionProfiles.fdictProfileFromTable(
            S_ACROSS))
    assert dictSettings["dictApplied"]["OPENBLAS_NUM_THREADS"] == "1"
    assert dictSettings["dictCapabilities"]["sArchitecture"] == os.uname().machine
    assert determinismEnvironment.fdictProbeUnderPrefix(
        None, "hostProject", "", "") == {
            "sProbeState": "probe not run in host mode"}
