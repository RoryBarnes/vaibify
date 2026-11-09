"""A package is called "no longer installed" only when nothing installed carries it.

The startup check once named pymultinest and ultranest as dropped from
a container that had both: the nestedSampling feature had installed
them, and the check compared the repository's file only with the
``pythonPackages`` list. It now compares with every list the image
records (``pythonPackages`` plus each feature's
``/etc/vaibify/requirements.d`` list) AND with the installed
distributions, read the way the shadow rerun reads them, through
``importlib.metadata``, because ``pip list`` hides an installed
``argparse``.

These source the REAL ``entrypoint.sh``. ``python3`` is replaced on
PATH by a stand-in that prints a scripted inventory, so the installed
set is the test's to choose.
"""

import os
import subprocess

import pytest

from vaibify.reproducibility import shadowRerun


_S_ENTRYPOINT = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "vaibify", "containerImage",
    "entrypoint.sh",
))


def _fsRunEntrypointFunction(tmp_path, sCall, listInstalled):
    """Source the entrypoint over a staged image and run one call."""
    pathImage = tmp_path / "image.txt"
    pathImage.write_text("numpy>=1.24\nscipy\n")
    pathFeatures = tmp_path / "requirements.d"
    pathFeatures.mkdir(exist_ok=True)
    (pathFeatures / "nestedSampling.txt").write_text("ultranest\npymultinest\n")
    pathBin = tmp_path / "bin"
    pathBin.mkdir(exist_ok=True)
    pathPython = pathBin / "python3"
    pathPython.write_text("#!/bin/bash\n" + "".join(
        f"echo '{sLine}'\n" for sLine in listInstalled))
    os.chmod(pathPython, 0o755)
    sScript = (
        "set +e\n"
        f"source {_S_ENTRYPOINT}\n"
        f"S_IMAGE_REQUIREMENTS={pathImage}\n"
        f"S_FEATURE_REQUIREMENTS_DIRECTORY={pathFeatures}\n"
        f"PATH={pathBin}:$PATH\n"
        + sCall + "\n"
        'printf "WARNING-LINE:%s\\n" "${saStartupWarnings[@]}"\n'
    )
    return subprocess.run(
        ["bash", "-c", sScript], capture_output=True, text=True, timeout=60,
    ).stdout


def _fsReportFor(tmp_path, sRepoFile, listInstalled):
    pathRepoFile = tmp_path / "repoRequirements.txt"
    pathRepoFile.write_text(sRepoFile)
    return _fsRunEntrypointFunction(
        tmp_path, f'fnReportDroppedRequirements "{pathRepoFile}" "project"',
        listInstalled)


def _fsWarningOf(sOutput):
    listWarnings = [sLine for sLine in sOutput.splitlines()
                    if sLine.startswith("WARNING-LINE:") and sLine != "WARNING-LINE:"]
    return listWarnings[0] if listWarnings else ""


def testAPackageAFeatureInstalledIsNotDropped(tmp_path):
    sOutput = _fsReportFor(tmp_path, "pymultinest\nultranest\n", [])
    assert _fsWarningOf(sOutput) == ""


@pytest.mark.falsification
def testAnInstalledPackageNoListNamesIsNotDropped(tmp_path):
    """Kills: comparing with the image's lists alone.

    ``argparse`` arrives as a dependency of something else, no list
    names it, and ``pip list`` would not show it; it is installed all
    the same. Mutation: the comparison's ``{ fnListImageRequirementNames;
    fnListInstalledDistributionNames; } | sort -u`` loses the inventory.
    """
    sOutput = _fsReportFor(
        tmp_path, "argparse\nnumpy\n", ["argparse==1.4.0", "numpy==1.26.4"])
    assert _fsWarningOf(sOutput) == ""


def testAPackageNothingInstalledIsNamedAsDropped(tmp_path):
    sOutput = _fsReportFor(
        tmp_path, "daft-pgm>=0.1.4\npymultinest\n", ["numpy==1.26.4"])
    assert _fsWarningOf(sOutput) == (
        "WARNING-LINE:project: requirements-dropped: declared only in the "
        "repo file, absent from the image, no longer installed: daft-pgm ")


def testTheMirrorCarriesEachFeaturesList(tmp_path):
    sOutput = _fsRunEntrypointFunction(tmp_path, "fnRenderRequirementsMirror", [])
    assert "numpy>=1.24\nscipy\n# installed by the nestedSampling feature\n" \
        "ultranest\npymultinest" in sOutput


def testTheInventoryIsTheShadowRerunsOwnProgram():
    """The entrypoint cannot import the host's inventory, so it carries a copy."""
    sEntrypoint = open(_S_ENTRYPOINT, encoding="utf-8").read()
    sBody = sEntrypoint.split("fnListInstalledDistributionNames() {\n", 1)[1]
    sCopied = sBody.split(" 2>/dev/null", 1)[0].strip()
    assert sCopied == shadowRerun.S_ENUMERATE_PACKAGES_COMMAND


def testTheMirrorCarriesEachProjectsDeclarationUnderItsName(tmp_path):
    pathProjects = tmp_path / "projectRequirements"
    pathProjects.mkdir()
    (pathProjects / "study.txt").write_text(
        "# study requirements.txt at " + "a" * 40 + " (branch main)\n"
        "daft-pgm>=0.1.4\n")
    sOutput = _fsRunEntrypointFunction(
        tmp_path, f"S_PROJECT_REQUIREMENTS_DIRECTORY={pathProjects}\n"
        "fnRenderRequirementsMirror", [])
    assert "# declared by project study\n# study requirements.txt at " in sOutput
    assert "daft-pgm>=0.1.4" in sOutput


@pytest.mark.falsification
def testTheVerificationComparesPythonPackagesWithTheMirrorsOwnSection(tmp_path):
    """Kills: comparing pythonPackages with the whole mirror.

    The mirror also lists each feature's and each project's packages,
    which pythonPackages never names; read whole, every one of them is
    "in the image but not declared" and the verification is refused.
    The mirror is the real entrypoint's. Mutation: the section cut in
    ``fsImageListOfMirror`` is dropped.
    """
    from vaibify.reproducibility.declaredPackages import (
        fdictComparePackageDeclarations,
    )
    pathProjects = tmp_path / "projectRequirements"
    pathProjects.mkdir()
    (pathProjects / "study.txt").write_text("# study\ndaft-pgm>=0.1.4\n")
    sMirror = _fsRunEntrypointFunction(
        tmp_path, f"S_PROJECT_REQUIREMENTS_DIRECTORY={pathProjects}\n"
        "fnRenderRequirementsMirror", [])
    sMirror = sMirror.split("WARNING-LINE:")[0]
    assert fdictComparePackageDeclarations(["numpy>=1.24", "scipy"], sMirror) == {
        "bChecked": True, "bMatches": True,
        "listMissingFromImage": [], "listExtraInImage": [],
    }
    assert fdictComparePackageDeclarations(
        ["numpy>=1.24", "scipy", "astropy"], sMirror,
    )["listMissingFromImage"] == ["astropy"]
