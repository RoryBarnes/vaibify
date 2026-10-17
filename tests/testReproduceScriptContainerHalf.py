"""The container half of ``reproduce.sh`` runs the rerun the runner runs.

The generated body is executed by real ``bash`` against a real repository
(``tests/reproduceScriptHarness.py``), with only Docker and the lock's
install stood in for. Each test states a behavior the design requires of
a reader's script (``design/cpuClassReproducibility.md``, D6 and I11, and
fact 24 for the runner's order), and the expected answer is that rule,
not the generator's output:

* a command that exits 0 and writes nothing does not pass on the
  published file, because the script deletes what it regenerates first;
* a failed step does not stop the run, and a failed section skips only
  what the runner skips;
* a failed check is a verdict with its tally, never a shell abort;
* anything that stops the script before grading is "Could not run";
* the ledger names exactly the commands the plan names, in its order;
* a carried output and every pinned file survive the clearing;
* each command runs under bash's default options, as the runner's
  ``bash -c`` does.
"""

import json
import os

import pytest

from tests.reproduceScriptHarness import (
    fdictRunContainerHalf,
    fnPublishForTheScript,
)
from tests.rerunAssessmentHarness import (
    fdictBuildStep,
    fdictBuildWorkflow,
    fdictQuantitativeStandard,
    fdictWriteGeneratedTests,
    fnWriteText,
    fsWriteJsonCommand,
)
from vaibify.reproducibility import rerunAssessment


S_VALUES = "values.json"


def _ftDirectories(tmp_path):
    """Return a fresh repository and a run directory beside it."""
    sRoot = str(tmp_path / "repo")
    sBase = str(tmp_path / "run")
    os.makedirs(sRoot)
    os.makedirs(sBase)
    return sRoot, sBase


def _fdictPublish(sRoot, listSteps):
    """Publish the steps with the script and assessor; return the workflow."""
    dictWorkflow = fdictBuildWorkflow(listSteps)
    fnPublishForTheScript(sRoot, dictWorkflow)
    return dictWorkflow


def _fdictQuantitativeStep(sRoot, sDirectory, dictValues, fExpected):
    """Return a published step writing ``dictValues``, checked by one standard."""
    dictTests = {"dictQuantitative": fdictWriteGeneratedTests(
        sRoot, sDirectory, "quantitative",
        [fdictQuantitativeStandard("xValue", S_VALUES, "x", fExpected)])}
    fnWriteText(sRoot, f"{sDirectory}/{S_VALUES}",
                json.dumps(dictValues, sort_keys=True))
    return fdictBuildStep(
        f"Step {sDirectory}", sDirectory,
        saDataCommands=[fsWriteJsonCommand(S_VALUES, dictValues)],
        saOutputDataFiles=[S_VALUES], dictTests=dictTests)


def _flistPlannedIds(dictPlan):
    """Return every command identity the plan lists, in execution order."""
    listIds = []
    for dictStep in dictPlan["listSteps"]:
        listIds += dictStep["listDataInvocations"]
        listIds += dictStep["listPlotInvocations"]
        for listGroup in dictStep["dictTestGroups"].values():
            listIds += listGroup
    return listIds


@pytest.mark.falsification
def testADataCommandThatWritesNothingIsNotReproduced(tmp_path):
    """``true`` exits 0 and writes nothing: the output is missing, exit 1.

    Kills: the script running its steps without first clearing what they
    regenerate, which grades the published file as the rerun's.
    """
    sRoot, sBase = _ftDirectories(tmp_path)
    fnWriteText(sRoot, f"StepOne/{S_VALUES}", "{}")
    _fdictPublish(sRoot, [fdictBuildStep(
        "Step one", "StepOne", saDataCommands=["true"],
        saOutputDataFiles=[S_VALUES])])
    dictRun = fdictRunContainerHalf(sRoot, sBase)
    assert dictRun["iExitCode"] == 1, dictRun["sStderr"]
    assert "Not reproduced: StepOne/values.json" in dictRun["sStdout"]
    assert "missing" in dictRun["sStdout"]
    assert not os.path.exists(os.path.join(sRoot, "StepOne", S_VALUES))


@pytest.mark.falsification
def testAFailedFirstStepStillRunsTheSecond(tmp_path):
    """Step 1's data fails: its plots and tests never run; step 2 still does.

    Kills: the script stopping the whole run at the first failed command.
    """
    sRoot, sBase = _ftDirectories(tmp_path)
    fnWriteText(sRoot, "StepOne/first.json", "{}")
    dictFirst = fdictBuildStep(
        "Step one", "StepOne", saDataCommands=["false"],
        saPlotCommands=["true"], saTestCommands=["true"],
        saOutputDataFiles=["first.json"])
    dictSecond = _fdictQuantitativeStep(sRoot, "StepTwo", {"x": 1.0}, 1.0)
    _fdictPublish(sRoot, [dictFirst, dictSecond])
    dictRun = fdictRunContainerHalf(sRoot, sBase)
    assert dictRun["listLedger"] == [
        ("A01.data.1", 1), ("A02.data.1", 0), ("A02.quantitative.1", 0)]
    assert dictRun["iExitCode"] == 1
    dictOutcomes = {dictOutput["sPath"]: dictOutput["sOutcome"]
                    for dictOutput in dictRun["dictReport"]["listOutputs"]}
    assert dictOutcomes[f"StepTwo/{S_VALUES}"] == "byte-identical"


@pytest.mark.falsification
def testAFailingTestIsNotReproducedWithATally(tmp_path):
    """A failed check exits 1 with the verdict and the tally printed.

    Kills: a failed command's record ending the script under ``set -e``,
    which turns a graded failure into a bare shell abort.
    """
    sRoot, sBase = _ftDirectories(tmp_path)
    _fdictPublish(sRoot, [
        _fdictQuantitativeStep(sRoot, "StepOne", {"x": 1.0}, 2.0)])
    dictRun = fdictRunContainerHalf(sRoot, sBase)
    assert dictRun["iExitCode"] == 1, dictRun["sStderr"]
    assert "Not reproduced:" in dictRun["sStdout"]
    assert "Tally:" in dictRun["sStdout"]
    assert "Could not run" not in dictRun["sStderr"]


@pytest.mark.falsification
def testAFailedInstallIsCouldNotRun(tmp_path):
    """The lock does not install: exit 4, before any step runs.

    Kills: a failed install carrying on to the steps.
    """
    sRoot, sBase = _ftDirectories(tmp_path)
    _fdictPublish(sRoot, [
        _fdictQuantitativeStep(sRoot, "StepOne", {"x": 1.0}, 1.0)])
    dictRun = fdictRunContainerHalf(sRoot, sBase, iPipExit=1)
    assert dictRun["iExitCode"] == 4
    assert "Could not run: requirements.lock" in dictRun["sStderr"]
    assert dictRun["listLedger"] is None


@pytest.mark.falsification
def testAFailureInThePreambleIsCouldNotRun(tmp_path):
    """Any preamble command that fails is "Could not run", never exit 1.

    Kills: dropping the container half's ERR trap, so a failed preamble
    command exits 1 -- which a reader takes for "not reproduced".
    """
    sRoot, sBase = _ftDirectories(tmp_path)
    _fdictPublish(sRoot, [
        _fdictQuantitativeStep(sRoot, "StepOne", {"x": 1.0}, 1.0)])

    def fsInjectFalse(sBody):
        sAnchor = 'sVaibifyAssessor='
        assert sBody.count(sAnchor) == 1
        return sBody.replace(sAnchor, "false\n" + sAnchor)

    dictRun = fdictRunContainerHalf(sRoot, sBase, fsEditBody=fsInjectFalse)
    assert dictRun["iExitCode"] == 4
    assert "Could not run: reproduce.sh failed at line" in dictRun["sStderr"]


@pytest.mark.falsification
def testTheLedgerNamesThePlansCommandsInOrder(tmp_path):
    """Every line of the ledger is the plan's identity, in the plan's order.

    An empty command is skipped by both, so it shifts neither numbering.

    Kills: the script numbering a section's commands differently from
    the plan, which binds each exit status to the wrong command.
    """
    sRoot, sBase = _ftDirectories(tmp_path)
    fnWriteText(sRoot, "StepOne/one.json", "{}")
    fnWriteText(sRoot, "StepTwo/two.json", "{}")
    dictFirst = fdictBuildStep(
        "Step one", "StepOne",
        saDataCommands=["true", "", "printf '{}' > one.json"],
        saPlotCommands=["true", "true"], saTestCommands=["true", "true"],
        saOutputDataFiles=["one.json"])
    dictSecond = fdictBuildStep(
        "Step two", "StepTwo",
        saDataCommands=["printf '{}' > two.json", "true"],
        saOutputDataFiles=["two.json"])
    dictWorkflow = _fdictPublish(sRoot, [dictFirst, dictSecond])
    dictRun = fdictRunContainerHalf(sRoot, sBase)
    listExpected = _flistPlannedIds(
        rerunAssessment.fdictBuildAssessmentPlan(dictWorkflow))
    assert [sId for sId, _iStatus in dictRun["listLedger"]] == listExpected
    assert listExpected[:3] == ["A01.data.1", "A01.data.2", "A01.plot.1"]
    assert all(iStatus == 0 for _sId, iStatus in dictRun["listLedger"])


@pytest.mark.falsification
def testACarriedOutputAndThePinnedFilesSurviveTheClearing(tmp_path):
    """The clearing deletes what the rerun regenerates and nothing else.

    A human step's output is carried as given; the script, its assessor
    and the step's own program are pinned inputs.

    Kills: a clearing job naming carried or pinned paths.
    """
    sRoot, sBase = _ftDirectories(tmp_path)
    fnWriteText(sRoot, "Given/given.json", '{"given": true}')
    fnWriteText(sRoot, "Make/make.py",
                "open('made.json', 'w').write('{}')\n")
    fnWriteText(sRoot, "Make/made.json", "{}")
    _fdictPublish(sRoot, [
        fdictBuildStep("Given", "Given", saOutputDataFiles=["given.json"],
                       bInteractive=True),
        fdictBuildStep("Make", "Make", saDataCommands=["python3 make.py"],
                       saOutputDataFiles=["made.json"]),
    ])
    dictRun = fdictRunContainerHalf(sRoot, sBase)
    assert dictRun["iExitCode"] == 0, dictRun["sStdout"] + dictRun["sStderr"]
    for sRelative in ("Given/given.json", "Make/make.py", "reproduce.sh",
                      ".vaibify/rerunAssessment.py", "MANIFEST.sha256"):
        assert os.path.isfile(os.path.join(sRoot, sRelative)), sRelative
    with open(os.path.join(sRoot, "Given", "given.json")) as fileIn:
        assert fileIn.read() == '{"given": true}'
    assert "Given/given.json" not in dictRun["sStdout"].split(
        "To restore them")[0]


@pytest.mark.falsification
def testEachCommandRunsUnderBashDefaults(tmp_path):
    """``false | true`` exits 0 and an unset variable is empty, as under the runner.

    The runner runs each command as ``bash -c``, with no ``-e``, ``-u``
    or ``pipefail``; the script's own ``set -euo pipefail`` must not
    reach a step's command.

    Kills: running the step commands under the script's strict options.
    """
    sRoot, sBase = _ftDirectories(tmp_path)
    fnWriteText(sRoot, "StepOne/out.json", "{}")
    _fdictPublish(sRoot, [fdictBuildStep(
        "Step one", "StepOne",
        saDataCommands=[
            "false | true",
            'printf "%s{}" "$VAIBIFY_NEVER_SET_ANYWHERE" > out.json'],
        saOutputDataFiles=["out.json"])])
    dictRun = fdictRunContainerHalf(sRoot, sBase)
    assert dictRun["listLedger"] == [("A01.data.1", 0), ("A01.data.2", 0)]
    assert dictRun["iExitCode"] == 0, dictRun["sStdout"] + dictRun["sStderr"]
    assert "Reproduced: every file byte-identical" in dictRun["sStdout"]
