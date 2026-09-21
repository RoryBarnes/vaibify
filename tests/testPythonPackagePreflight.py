"""A pythonPackages name the index does not know is refused before the build.

A misspelled name ("matplolib") used to cost a full image build to
discover: pip refused it minutes in, after apt and the toolchain had
run, and the sentence the researcher read did not name it (a live
build, 2026-09-21). These tests drive the check with the index
answered by a fake, so they say nothing about pypi.org itself; the
live answer was confirmed by hand when the check was written, and
``fbNameExistsOnIndex`` is the one function that talks to it.
"""

import urllib.error
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from vaibify.cli.preflightResult import S_LEVEL_FAIL, S_LEVEL_NOT_CHECKED
from vaibify.cli.pythonPackagePreflight import (
    IndexUnreachableError,
    S_PREFLIGHT_NAME,
    fbIndexIsOverridden,
    fbNameExistsOnIndex,
    flistUnknownPythonPackages,
    fpreflightPythonPackageNames,
    fsPackageNameFromRequirement,
)


SET_KNOWN_NAMES = {"numpy", "matplotlib", "scipy", "astropy", "matplot-lib"}


def _fbFakeIndex(sName):
    return sName in SET_KNOWN_NAMES


def _fconfigWith(listPackages, sFlags=""):
    return SimpleNamespace(
        listPythonPackages=listPackages, sPipInstallFlags=sFlags,
    )


@pytest.mark.falsification
def test_a_misspelled_name_is_refused_before_the_build():
    """The refusal names the requirement as written, and the file.

    Kills: the unknown-name walk returning nothing, under which the
    check passes every name and the build discovers the typo itself.
    """
    resultNames = fpreflightPythonPackageNames(
        _fconfigWith(["numpy", "matplolib", "scipy"]), _fbFakeIndex,
    )
    assert resultNames is not None
    assert resultNames.sLevel == S_LEVEL_FAIL
    assert resultNames.sName == S_PREFLIGHT_NAME
    assert "'matplolib'" in resultNames.sMessage
    assert "pythonPackages" in resultNames.sMessage
    assert "vaibify.yml" in resultNames.sRemediation


def test_a_config_whose_names_all_exist_is_silent():
    assert fpreflightPythonPackageNames(
        _fconfigWith(["numpy", "matplotlib>=3.8", "scipy"]), _fbFakeIndex,
    ) is None
    assert fpreflightPythonPackageNames(_fconfigWith([]), _fbFakeIndex) is None


@pytest.mark.parametrize("sRequirement, sExpected", [
    ("numpy", "numpy"),
    ("numpy>=1.26", "numpy"),
    ("numpy==1.26.4", "numpy"),
    ("astropy[all]", "astropy"),
    ("Matplot_Lib", "matplot-lib"),
    ("  scipy ; python_version < '3.13'", "scipy"),
])
def test_the_name_is_read_from_the_requirement_as_pip_would(
    sRequirement, sExpected,
):
    assert fsPackageNameFromRequirement(sRequirement) == sExpected


@pytest.mark.parametrize("sRequirement", [
    "-e .",
    "--no-binary :all:",
    "./vendored/package",
    "/opt/wheels/package.whl",
    "git+https://host.example/group/package.git",
    "https://host.example/package-1.0.tar.gz",
    "package @ https://host.example/package-1.0.tar.gz",
    "",
])
def test_a_requirement_pip_never_asks_the_index_for_is_not_asked(
    sRequirement,
):
    """A path, URL, option or direct reference is not an index lookup;
    reading one as a name would refuse a config pip accepts."""
    assert fsPackageNameFromRequirement(sRequirement) == ""
    assert flistUnknownPythonPackages([sRequirement], lambda sName: False) == []


@pytest.mark.falsification
def test_a_custom_index_is_not_judged_by_pypi():
    """pip reads a different index under these flags; a name absent
    from pypi.org may exist there, so the answer is not-checked.

    Kills: dropping the override guard, under which a private
    package is refused for being unknown to pypi.org.
    """
    for sFlags in (
        "--index-url https://mirror.example/simple",
        "--extra-index-url=https://mirror.example/simple",
        "-i https://mirror.example/simple",
        "--find-links /opt/wheels",
        "--no-index",
    ):
        assert fbIndexIsOverridden(sFlags), sFlags
        resultNames = fpreflightPythonPackageNames(
            _fconfigWith(["private-package"], sFlags), lambda sName: False,
        )
        assert resultNames.sLevel == S_LEVEL_NOT_CHECKED, sFlags
    assert not fbIndexIsOverridden("--prefer-binary --no-cache-dir")


@pytest.mark.falsification
def test_an_index_that_does_not_answer_never_refuses():
    """A network miss is not evidence about the name.

    Kills: reading an unreachable index as "the name does not exist",
    under which every offline researcher's build is refused.
    """
    def fnRaise(sName):
        raise IndexUnreachableError("pypi.org could not be reached")

    resultNames = fpreflightPythonPackageNames(
        _fconfigWith(["numpy"]), fnRaise,
    )
    assert resultNames.sLevel == S_LEVEL_NOT_CHECKED
    assert "pypi.org could not be reached" in resultNames.sMessage


def test_the_index_probe_reads_only_a_404_as_absence():
    """Any other failure is no answer and is raised, never read as
    absence."""
    def fnRaise404(requestHead, timeout):
        raise urllib.error.HTTPError(
            requestHead.full_url, 404, "Not Found", {}, None,
        )

    def fnRaise503(requestHead, timeout):
        raise urllib.error.HTTPError(
            requestHead.full_url, 503, "Unavailable", {}, None,
        )

    def fnRaiseNetwork(requestHead, timeout):
        raise urllib.error.URLError("name resolution failed")

    with patch("urllib.request.urlopen", fnRaise404):
        assert fbNameExistsOnIndex("matplolib") is False
    with patch("urllib.request.urlopen", fnRaise503):
        with pytest.raises(IndexUnreachableError):
            fbNameExistsOnIndex("numpy")
    with patch("urllib.request.urlopen", fnRaiseNetwork):
        with pytest.raises(IndexUnreachableError):
            fbNameExistsOnIndex("numpy")


def test_the_probe_asks_the_simple_index_by_normalized_name():
    listUrls = []

    class _ResponseOk:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fnRecord(requestHead, timeout):
        listUrls.append((requestHead.full_url, requestHead.get_method()))
        return _ResponseOk()

    with patch("urllib.request.urlopen", fnRecord):
        assert fbNameExistsOnIndex("matplot-lib") is True
    assert listUrls == [("https://pypi.org/simple/matplot-lib/", "HEAD")]
