"""The in-image checker answers satisfaction, not names.

``checkDeclaredRequirements.py`` ships into every base image and is run
by the build (in the pinned environment) and by the container's start
(on each project's working copy). It needs only the standard library
and ``packaging``, so it runs here unchanged, against this interpreter's
own installed packages, which is what these tests check it against:
``packaging`` and ``pytest`` are installed wherever this suite runs.
"""

import json
import os
import subprocess
import sys

import pytest

_S_CHECKER = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "vaibify", "containerImage",
    "checkDeclaredRequirements.py",
))


def _ftCheck(tmp_path, sText, sName="requirements.txt"):
    pathFile = tmp_path / sName
    pathFile.write_text(sText, encoding="utf-8")
    processCheck = subprocess.run(
        [sys.executable, "-I", _S_CHECKER, f"project alpha={pathFile}"],
        capture_output=True, text=True, timeout=120,
    )
    return processCheck.returncode, json.loads(processCheck.stdout)


def _fsInstalledVersion(sName):
    import importlib.metadata
    return importlib.metadata.version(sName)


def testASatisfiedDeclarationReportsNothing(tmp_path):
    iExit, dictReport = _ftCheck(
        tmp_path, "packaging>=1  # parses requirements\n\npytest\n")
    assert (iExit, dictReport) == (0, {"listUnchecked": [], "listUnsatisfied": []})


@pytest.mark.falsification
def testATightenedVersionIsUnsatisfiedThoughTheNameIsInstalled(tmp_path):
    """Kills: comparing names alone.

    The name is installed; only the specifier says the image is behind
    the declaration. Mutation: the specifier test is dropped.
    """
    iExit, dictReport = _ftCheck(tmp_path, "packaging>=9999\n")
    assert iExit == 1
    assert dictReport["listUnsatisfied"] == [{
        "sSource": "project alpha", "sRequirement": "packaging>=9999",
        "sInstalled": _fsInstalledVersion("packaging"),
    }]


def testAnAbsentPackageIsUnsatisfiedWithNoneInstalled(tmp_path):
    iExit, dictReport = _ftCheck(tmp_path, "vaibify-no-such-package-xyz\n")
    assert iExit == 1
    assert dictReport["listUnsatisfied"][0]["sInstalled"] == "none"


def testAMarkerThatExcludesThisEnvironmentIsSatisfied(tmp_path):
    iExit, dictReport = _ftCheck(
        tmp_path, "vaibify-no-such-package-xyz; python_version < '3.0'\n")
    assert (iExit, dictReport["listUnsatisfied"]) == (0, [])


def _ftCheckWithStandInDistribution(tmp_path, sText):
    """Run the checker with one stand-in distribution, ``toolbox 1.0``, installed.

    Its ``plots`` extra adds a package nothing installs, so the oracle
    does not depend on what this interpreter happens to hold.
    """
    pathSite = tmp_path / "site"
    pathInfo = pathSite / "toolbox-1.0.dist-info"
    pathInfo.mkdir(parents=True)
    (pathInfo / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: toolbox\nVersion: 1.0\n"
        "Provides-Extra: plots\n"
        'Requires-Dist: vaibify-no-such-package-xyz; extra == "plots"\n')
    pathFile = tmp_path / "requirements.txt"
    pathFile.write_text(sText, encoding="utf-8")
    processCheck = subprocess.run(
        [sys.executable, "-I", "-c",
         "import runpy, sys; sys.path.insert(0, sys.argv[1]); "
         "sys.argv = sys.argv[2:]; runpy.run_path(sys.argv[0], run_name='__main__')",
         str(pathSite), _S_CHECKER, f"project alpha={pathFile}"],
        capture_output=True, text=True, timeout=120,
    )
    return processCheck.returncode, json.loads(processCheck.stdout)


def testAnExtraIsSatisfiedOnlyWhenWhatItAddsIsInstalled(tmp_path):
    assert _ftCheckWithStandInDistribution(tmp_path, "toolbox==1.0\n")[0] == 0
    iExit, dictReport = _ftCheckWithStandInDistribution(
        tmp_path / "extra", "toolbox[plots]==1.0\n")
    assert iExit == 1
    assert dictReport["listUnsatisfied"][0]["sInstalled"] == (
        "1.0, without what [plots] adds (vaibify-no-such-package-xyz)")


@pytest.mark.parametrize("sLine", ["-e .", "-r other.txt", "./local/package"])
def testALineThatIsNotARequirementIsUncheckedNeverPassed(tmp_path, sLine):
    iExit, dictReport = _ftCheck(tmp_path, f"{sLine}\n")
    assert iExit == 0
    assert [d["sRequirement"] for d in dictReport["listUnchecked"]] == [sLine]


def testAPyprojectsDependenciesAreChecked(tmp_path):
    if sys.version_info < (3, 11):
        pytest.skip("reading TOML in the image needs Python 3.11")
    iExit, dictReport = _ftCheck(
        tmp_path, '[project]\nname = "x"\ndependencies = ["packaging>=9999"]\n',
        sName="pyproject.toml")
    assert iExit == 1
    assert dictReport["listUnsatisfied"][0]["sRequirement"] == "packaging>=9999"


def testAMalformedArgumentCannotRun(tmp_path):
    processCheck = subprocess.run(
        [sys.executable, "-I", _S_CHECKER, "no-separator"],
        capture_output=True, text=True, timeout=60)
    assert processCheck.returncode == 2
    assert "expected <source>=<file>" in json.loads(processCheck.stdout)["sError"]
