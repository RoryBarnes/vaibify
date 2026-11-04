"""A manifest difference is named by what was measured, never by what was not.

Source: ``vaibify/gui/outputProvenance.py`` (``fdictExplainManifestMismatches``,
``fdictReportManifestMismatches``).

The check used to compare each recorded fact with ``!=``, so a value on
one side against nothing on the other counted as "produced under
different execution settings or hardware". A host-mode run skips the
NumPy probe and a Mac reports no processor vendor or x86-64 level, so a
reader on either got that claim, and the offer to make this computer's
results the published ones, on no evidence. The bracketed facts also
described only the first file while the sentence counted all of them.

The records here are written by hand, so each expectation follows from
what the test chose to measure, not from the code under test:

* observed: both sides measured a fact and the values differ;
* unmeasured: one side lacks it, worded with the reason its record
  gives ("not measured in host mode", "this machine does not report it");
* only a file with an observed difference counts, and the option to
  rerun here is offered only when every differing file has one.
"""

import json
import os

import pytest

from vaibify.gui import outputProvenance


S_PATH_LEVEL = "StepOne/level.json"
S_PATH_HOST = "StepTwo/host.json"
S_PATH_SAME = "StepThree/same.json"
I_TOTAL = 10
_DICT_CAPABILITIES = {"sArchitecture": "x86_64", "sVendor": "GenuineIntel",
                      "sLevel": "x86-64-v3", "iVisibleCores": 8,
                      "bReadable": True}
_DICT_PROBE = {"sProbeState": "ran", "sNumpyVersion": "1.26.4",
               "dictOpenblas": {"sCoreName": "Haswell", "iThreads": 1}}
_DICT_HOST_MODE_PROBE = {"sProbeState": "probe not run in host mode"}


def _fdictContext(dictCapabilities=None, dictProbe=None):
    return outputProvenance.fdictBuildRunContext(
        {"dictCapabilities": dict(dictCapabilities or _DICT_CAPABILITIES),
         "sProfileId": "speed-v1", "dictApplied": {"OMP_NUM_THREADS": "1"}},
        dict(_DICT_PROBE if dictProbe is None else dictProbe),
        sRecordedUtc="2026-11-03T12:00:00Z")


def _fnWriteJson(sRoot, sRelative, dictValue):
    sPath = os.path.join(sRoot, sRelative)
    os.makedirs(os.path.dirname(sPath), exist_ok=True)
    with open(sPath, "w", encoding="utf-8") as fileOut:
        json.dump(dictValue, fileOut)


def _flistRecordPairs(sRoot, dictOnDiskByPath):
    """Pin every path to the author's context; record each one's own on disk.

    Return the mismatches the manifest check would report for them.
    """
    dictPinnedContext = _fdictContext()
    dictEnvelope = {"dictRunContexts": {"pinned": dictPinnedContext},
                    "dictOutputs": {}}
    dictRecord = {"iSchemaVersion": 1, "dictRunContexts": {},
                  "dictOutputs": {}}
    listMismatches = []
    for iIndex, (sPath, dictOnDisk) in enumerate(sorted(dictOnDiskByPath.items())):
        sSha256 = f"{iIndex + 1:064x}"
        dictEnvelope["dictOutputs"][sPath] = {
            "sState": "recorded", "sSection": "data", "sRunContextId": "pinned"}
        dictRecord["dictRunContexts"][f"here{iIndex}"] = dictOnDisk
        dictRecord["dictOutputs"][sPath] = {
            "sSha256": sSha256, "sSection": "data",
            "sRunContextId": f"here{iIndex}"}
        listMismatches.append(
            {"sPath": sPath, "sExpected": "0" * 64, "sActual": sSha256})
    _fnWriteJson(sRoot, ".vaibify/environment.json",
                 {"dictOutputProvenance": dictEnvelope})
    _fnWriteJson(sRoot, outputProvenance.S_OUTPUT_PROVENANCE_REPO_PATH, dictRecord)
    return listMismatches


def _fdictDescriptionsByPath(sRoot, listMismatches):
    dictReport = outputProvenance.fdictReportManifestMismatches(
        sRoot, listMismatches, {})
    return dictReport, {dictFile["sPath"]: dictFile["sDescription"]
                        for dictFile in dictReport["listFiles"]}


@pytest.mark.falsification
def testIdenticalHardwareWithTheProbeUnmeasuredIsNotCountedAsADifference(tmp_path):
    """The same recorded hardware and settings, the probe skipped in host mode.

    Kills: counting a fact one side never measured as an observed
    difference, the ``!=`` that offered the rerun on no evidence.
    """
    sRoot = str(tmp_path)
    listMismatches = _flistRecordPairs(sRoot, {
        S_PATH_HOST: _fdictContext(dictProbe=_DICT_HOST_MODE_PROBE)})
    dictExplained = outputProvenance.fdictExplainManifestMismatches(
        sRoot, listMismatches, I_TOTAL)
    assert dictExplained["bOnlyContextDifferences"] is False
    assert dictExplained["sOption"] == ""
    assert dictExplained["listExplained"][0]["listObserved"] == []
    assert {"sFact": "NumPy version", "sThere": "1.26.4",
            "sHere": "not measured in host mode"} in (
        dictExplained["listExplained"][0]["listUnmeasured"])
    assert dictExplained["sHeadline"] == (
        "1 of 10 files differs from the manifest. Some of its hardware or "
        "settings weren't measured, so they can't be compared.")
    dictReport, dictDescriptions = _fdictDescriptionsByPath(sRoot, listMismatches)
    assert "recorded hardware or settings differ" not in dictDescriptions[S_PATH_HOST]
    assert dictReport["sCaution"] == ""
    assert dictReport["sRegenerateChoice"] == outputProvenance.S_REGENERATE_CHOICE


@pytest.mark.falsification
def testOneObservedDifferenceIsCountedAndTheOptionOffered(tmp_path):
    """Both sides measured the x86-64 level, and the values differ.

    Kills: never classifying a measured difference as observed, which
    hides the one fact a reader can inspect.
    """
    sRoot = str(tmp_path)
    listMismatches = _flistRecordPairs(sRoot, {
        S_PATH_LEVEL: _fdictContext(dict(_DICT_CAPABILITIES, sLevel="x86-64-v2"))})
    dictExplained = outputProvenance.fdictExplainManifestMismatches(
        sRoot, listMismatches, I_TOTAL)
    assert dictExplained["bOnlyContextDifferences"] is True
    assert dictExplained["sOption"] == outputProvenance.S_RERUN_HERE_OPTION
    assert dictExplained["listExplained"][0]["listObserved"] == [
        {"sFact": "x86-64 level", "sThere": "x86-64-v3", "sHere": "x86-64-v2"}]
    assert dictExplained["sHeadline"] == (
        "1 of 10 files differs from the manifest. It was made with "
        "different recorded hardware or settings.")
    dictReport, _ = _fdictDescriptionsByPath(sRoot, listMismatches)
    assert dictReport["sCaution"] == outputProvenance.S_DIFFERENCE_CAUTION
    assert dictReport["sRegenerateChoice"] == outputProvenance.S_RERUN_HERE_OPTION


@pytest.mark.falsification
def testEachFileIsDescribedByItsOwnFacts(tmp_path):
    """Three files: an observed difference, an unmeasured probe, the same context.

    Kills: describing every file by the first file's facts, which the
    old summary did while its sentence counted all of them.
    """
    sRoot = str(tmp_path)
    listMismatches = _flistRecordPairs(sRoot, {
        S_PATH_LEVEL: _fdictContext(dict(_DICT_CAPABILITIES, sLevel="x86-64-v2")),
        S_PATH_HOST: _fdictContext(dictProbe=_DICT_HOST_MODE_PROBE),
        S_PATH_SAME: _fdictContext(),
    })
    _dictReport, dictDescriptions = _fdictDescriptionsByPath(sRoot, listMismatches)
    assert dictDescriptions[S_PATH_LEVEL] == (
        "recorded hardware or settings differ: x86-64 level: pinned copy "
        "x86-64-v3, this copy x86-64-v2")
    assert "x86-64 level" not in dictDescriptions[S_PATH_HOST]
    assert dictDescriptions[S_PATH_HOST].startswith(
        "not compared, because only one computer measured them: NumPy "
        "version: pinned copy 1.26.4, this copy not measured in host mode")
    assert dictDescriptions[S_PATH_SAME] == outputProvenance.S_SAME_RECORDED_CONTEXT
    dictExplained = outputProvenance.fdictExplainManifestMismatches(
        sRoot, listMismatches, I_TOTAL)
    assert dictExplained["bOnlyContextDifferences"] is False
    assert dictExplained["sHeadline"] == (
        "3 of 10 files differ from the manifest. 1 of them was made with "
        "different recorded hardware or settings. For 1 of them, some "
        "hardware or settings weren't measured, so they can't be compared.")


def testAMachineThatReportsNoProcessorFactsSaysSo(tmp_path):
    """A Mac reports no vendor or x86-64 level; that is unmeasured, never a difference."""
    sRoot = str(tmp_path)
    listMismatches = _flistRecordPairs(sRoot, {
        S_PATH_LEVEL: _fdictContext(dict(
            _DICT_CAPABILITIES, sArchitecture="arm64", sVendor="", sLevel="",
            bReadable=False))})
    dictExplained = outputProvenance.fdictExplainManifestMismatches(
        sRoot, listMismatches, I_TOTAL)
    listUnmeasured = dictExplained["listExplained"][0]["listUnmeasured"]
    assert {"sFact": "processor vendor", "sThere": "GenuineIntel",
            "sHere": "this machine does not report it"} in listUnmeasured
    assert {"sFact": "x86-64 level", "sThere": "x86-64-v3",
            "sHere": "this machine does not report it"} in listUnmeasured
    assert dictExplained["listExplained"][0]["listObserved"] == []
    assert dictExplained["sOption"] == ""


def testNoDifferenceGivesNoHeadline(tmp_path):
    assert outputProvenance.fdictExplainManifestMismatches(
        str(tmp_path), [], I_TOTAL)["sHeadline"] == ""
