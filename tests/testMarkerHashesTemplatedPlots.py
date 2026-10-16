"""The marker records the digest of every declared plot, templated or not.

The marker plugin used to skip any declared output containing a ``{``,
so a plot declared as ``{sPlotDirectory}/fig.{sFigureType}`` -- the
form every generated workflow uses -- was never hashed. A plot whose
bytes then changed while its mtime did not advance (``cp -p``,
``rsync -a``, unpacking an archive) was invisible to the content check
that judges a test run, and the tests stayed passed over a plot they
had never seen.

Every test here runs the generated plugin in a real pytest session, so
the marker is the one a container's session would write. The parity
test holds the plugin's resolution of a declared path to the host's
(``fileStatusManager.fsStepFileRepoRelative``), because the poll judges
a marker's digests by the keys the host computes.
"""

import hashlib
import json
import os

import pytest

from tests import perTestMarkerHarness as harness
from vaibify.gui import hashStaleness
from vaibify.gui.fileStatusManager import fsStepFileRepoRelative

S_TEMPLATED_PLOT = "{sPlotDirectory}/fig.{sFigureType}"
S_LITERAL_PLOT = "literal.png"
S_PLOT_DIRECTORY = "Figures"


def _fdictWorkflow(sRepo, listDataFiles, listPlotFiles):
    return {
        "sProjectRepoPath": sRepo, "sPlotDirectory": S_PLOT_DIRECTORY,
        "sFigureType": "PNG",
        "listSteps": [{
            "sName": "Step A", "sDirectory": harness.S_STEP,
            "saOutputDataFiles": listDataFiles, "saPlotFiles": listPlotFiles,
        }],
    }


def _fsBuildProjectDeclaring(tmp_path, listDataFiles, listPlotFiles):
    """Lay down the harness project, then declare the given outputs."""
    sRepo = harness.fsBuildProject(
        tmp_path, {"test_integrity_a.py": harness.S_PASS})
    dictWorkflow = _fdictWorkflow(sRepo, listDataFiles, listPlotFiles)
    harness.fnWrite(
        os.path.join(sRepo, ".vaibify", "projects", harness.S_SLUG + ".json"),
        json.dumps(dictWorkflow))
    for sFile in listDataFiles + listPlotFiles:
        sRelative = fsStepFileRepoRelative(
            dictWorkflow, dictWorkflow["listSteps"][0], sFile)
        harness.fnWrite(os.path.join(sRepo, sRelative), "bytes of " + sRelative)
    return sRepo, dictWorkflow


def _fsBlobSha(sPath):
    with open(sPath, "rb") as fileIn:
        baContent = fileIn.read()
    hasher = hashlib.sha1()
    hasher.update(b"blob " + str(len(baContent)).encode() + b"\0")
    hasher.update(baContent)
    return hasher.hexdigest()


def _fdictOutputHashesOfNewestRun(sRepo):
    dictMarker = harness.fdictReadMarker(sRepo)
    dictRuns = dictMarker["dictRuns"]
    sNewest = max(dictRuns, key=lambda sKey: dictRuns[sKey]["fTimestamp"])
    return dictRuns[sNewest]["dictOutputHashes"]


def _fdictHashEntriesOnDisk(sRepo, listRelativePaths):
    """Answer for each path as the poll snapshot would: its blob digest."""
    return {
        sRelative: {"sBlobSha": _fsBlobSha(os.path.join(sRepo, sRelative))}
        for sRelative in listRelativePaths
    }


def _fnReplaceWithOlderMtime(sPath, sNewContent):
    """Change a file's bytes while moving its mtime BACK, as ``cp -p`` can."""
    fMtimeBefore = os.stat(sPath).st_mtime
    with open(sPath, "w") as fileOut:
        fileOut.write(sNewContent)
    os.utime(sPath, (fMtimeBefore - 3600, fMtimeBefore - 3600))


def test_the_plugin_resolves_declared_paths_as_the_host_does(tmp_path):
    listDataFiles = ["out.dat", "nested/deeper/table.csv", "../shared.json"]
    listPlotFiles = [
        S_TEMPLATED_PLOT, S_LITERAL_PLOT, "panels/{sFigureType}/b.{sFigureType}",
        "../sharedPlot.png",
    ]
    sRepo, dictWorkflow = _fsBuildProjectDeclaring(
        tmp_path, listDataFiles, listPlotFiles)
    iExit, sOutput = harness.fiRunPytest(sRepo, ["tests"])
    assert iExit == 0, sOutput
    dictStep = dictWorkflow["listSteps"][0]
    listHostKeys = sorted(
        fsStepFileRepoRelative(dictWorkflow, dictStep, sFile)
        for sFile in listDataFiles + listPlotFiles)
    assert "Figures/fig.png" in listHostKeys
    assert sorted(_fdictOutputHashesOfNewestRun(sRepo)) == listHostKeys


def test_an_unknown_template_token_is_left_unhashed(tmp_path):
    sRepo, _dictWorkflow = _fsBuildProjectDeclaring(
        tmp_path, ["out.dat"], [S_LITERAL_PLOT])
    sProjectPath = os.path.join(
        sRepo, ".vaibify", "projects", harness.S_SLUG + ".json")
    with open(sProjectPath) as fileIn:
        dictWorkflow = json.load(fileIn)
    dictWorkflow["listSteps"][0]["saPlotFiles"].append("{sUnknownToken}/c.png")
    harness.fnWrite(sProjectPath, json.dumps(dictWorkflow))
    iExit, sOutput = harness.fiRunPytest(sRepo, ["tests"])
    assert iExit == 0, sOutput
    assert sorted(_fdictOutputHashesOfNewestRun(sRepo)) == [
        harness.S_STEP + "/" + S_LITERAL_PLOT, harness.S_STEP + "/out.dat"]


def _fnAssertAChangedPlotLeavesTheTestsNotCurrent(tmp_path, sDeclaredPlot):
    """Run the tests, change the plot's bytes with an OLDER mtime, judge."""
    sRepo, dictWorkflow = _fsBuildProjectDeclaring(
        tmp_path, ["out.dat"], [sDeclaredPlot])
    iExit, sOutput = harness.fiRunPytest(sRepo, ["tests"])
    assert iExit == 0, sOutput
    sPlotRelative = fsStepFileRepoRelative(
        dictWorkflow, dictWorkflow["listSteps"][0], sDeclaredPlot)
    dictRecorded = _fdictOutputHashesOfNewestRun(sRepo)
    assert sPlotRelative in dictRecorded
    listJudged = sorted(dictRecorded)
    dictVerdictsBefore = hashStaleness.fdictVerdictsForMarkerRuns(
        harness.fdictReadMarker(sRepo), _fdictHashEntriesOnDisk(sRepo, listJudged))
    assert set(dictVerdictsBefore["dictRunVerdicts"].values()) == {"match"}
    assert harness.fdictStates(
        sRepo, dictVerdictsBefore["dictRunVerdicts"])["integrity"]["sState"] == "passed"
    _fnReplaceWithOlderMtime(os.path.join(sRepo, sPlotRelative), "a redrawn plot")
    dictVerdictsAfter = hashStaleness.fdictVerdictsForMarkerRuns(
        harness.fdictReadMarker(sRepo), _fdictHashEntriesOnDisk(sRepo, listJudged))
    assert dictVerdictsAfter["listDrifted"] == [sPlotRelative]
    dictIntegrity = harness.fdictStates(
        sRepo, dictVerdictsAfter["dictRunVerdicts"])["integrity"]
    assert dictIntegrity["sState"] == "untested"
    assert dictIntegrity["sNotCurrentBecause"] == "drift"


@pytest.mark.falsification
def test_a_changed_templated_plot_with_an_older_mtime_leaves_the_tests_not_current(
    tmp_path,
):
    """Kills: the plugin skipping templated plot paths again."""
    _fnAssertAChangedPlotLeavesTheTestsNotCurrent(tmp_path, S_TEMPLATED_PLOT)


def test_a_changed_literal_plot_with_an_older_mtime_leaves_the_tests_not_current(
    tmp_path,
):
    _fnAssertAChangedPlotLeavesTheTestsNotCurrent(tmp_path, S_LITERAL_PLOT)


def test_a_marker_from_before_plot_hashing_cannot_see_a_content_change(tmp_path):
    """Pins the old blind spot: a run that recorded no plot digest is
    judged by its data alone, so a plot changed with an older mtime
    leaves it passed. Only the timestamp rule can catch such a run.
    """
    sRepo, dictWorkflow = _fsBuildProjectDeclaring(
        tmp_path, ["out.dat"], [S_TEMPLATED_PLOT])
    iExit, sOutput = harness.fiRunPytest(sRepo, ["tests"])
    assert iExit == 0, sOutput
    sPlotRelative = fsStepFileRepoRelative(
        dictWorkflow, dictWorkflow["listSteps"][0], S_TEMPLATED_PLOT)
    dictMarker = harness.fdictReadMarker(sRepo)
    for dictRun in dictMarker["dictRuns"].values():
        dictRun["dictOutputHashes"].pop(sPlotRelative)
    _fnReplaceWithOlderMtime(os.path.join(sRepo, sPlotRelative), "a redrawn plot")
    dictVerdicts = hashStaleness.fdictVerdictsForMarkerRuns(
        dictMarker, _fdictHashEntriesOnDisk(sRepo, [harness.S_STEP + "/out.dat"]))
    assert set(dictVerdicts["dictRunVerdicts"].values()) == {"match"}
