"""Real pytest sessions over a throwaway project, for the per-test marker.

Every helper here runs the generated ``conftest.py`` in a real pytest
subprocess, so what the marker contains is what a container's session
would have written. Shared by the tests of the plugin itself and by the
end-to-end tests that poll over the markers it leaves.
"""

import json
import os
import subprocess
import sys

from vaibify.gui import conftestManager
from vaibify.gui import testMarkerContract as contract

S_STEP = "StepA"
S_SLUG = "demo"
S_PASS = "def test_one():\n    assert True\n\ndef test_two():\n    assert True\n"
S_ONE_FAILS = (
    "def test_one():\n    assert True\n\n"
    "def test_two():\n    assert False\n")


def fsMarkerPath(sRepo):
    return os.path.join(
        sRepo, ".vaibify", "test_markers", S_SLUG, S_STEP + ".json")


def fnWrite(sPath, sContent):
    os.makedirs(os.path.dirname(sPath), exist_ok=True)
    with open(sPath, "w") as fileOut:
        fileOut.write(sContent)


def fsBuildProject(tmp_path, dictTestFiles):
    """Lay down a one-step project whose step has the given test files."""
    sRepo = str(tmp_path / "repo")
    fnWrite(
        os.path.join(sRepo, ".vaibify", "projects", S_SLUG + ".json"),
        json.dumps({"listSteps": [{
            "sName": "Step A", "sDirectory": S_STEP,
            "saOutputDataFiles": ["out.dat"], "saPlotFiles": []}]}))
    fnWrite(os.path.join(sRepo, S_STEP, "out.dat"), "v1")
    fnWrite(
        os.path.join(sRepo, S_STEP, "tests", "conftest.py"),
        conftestManager.fsBuildConftestSource(sRepo))
    for sFile, sContent in dictTestFiles.items():
        fnWrite(os.path.join(sRepo, S_STEP, "tests", sFile), sContent)
    return sRepo


def fnStartPytest(sRepo, listArguments, sCategory=""):
    dictEnvironment = dict(os.environ)
    dictEnvironment["VAIBIFY_ACTIVE_WORKFLOW_SLUG"] = S_SLUG
    dictEnvironment.pop("VAIBIFY_TEST_CATEGORY", None)
    if sCategory:
        dictEnvironment["VAIBIFY_TEST_CATEGORY"] = sCategory
    return subprocess.Popen(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q"]
        + listArguments,
        cwd=os.path.join(sRepo, S_STEP), env=dictEnvironment,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def fiRunPytest(sRepo, listArguments, sCategory=""):
    processRun = fnStartPytest(sRepo, listArguments, sCategory)
    sOutput = processRun.communicate()[0]
    return processRun.returncode, sOutput


def fdictReadMarker(sRepo):
    with open(fsMarkerPath(sRepo)) as fileMarker:
        return json.load(fileMarker)


def fdictStates(sRepo, dictRunVerdicts=None):
    dictMarker = fdictReadMarker(sRepo)
    sTests = os.path.join(sRepo, S_STEP, "tests")
    listFiles = sorted(
        sFile for sFile in os.listdir(sTests)
        if sFile.startswith("test_") and sFile.endswith(".py"))
    return contract.fdictCategoryStatesFromMarker(
        dictMarker, listFiles, {}, dictRunVerdicts or {}, 0)
