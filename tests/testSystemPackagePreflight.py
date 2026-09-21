"""A systemPackages name Ubuntu does not publish is refused before the build.

Same class as a misspelled pip package, one layer down: apt would stop
the build after the base image and the pinned toolchain had been
fetched. Launchpad answers for a published binary in about a second.
These tests drive the check with a faked archive; the live answers
(``gosu`` and ``gcc`` present, a nonsense name absent) were confirmed
by hand when the check was written.
"""

import json
import urllib.error

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from vaibify.cli.preflightResult import (
    S_LEVEL_FAIL, S_LEVEL_NOT_CHECKED,
)
from vaibify.cli.systemPackagePreflight import (
    ArchiveUnreachableError,
    S_PREFLIGHT_NAME,
    fbPackageExistsInArchive,
    flistUnknownSystemPackages,
    fpreflightSystemPackageNames,
    fsSeriesForBaseImage,
)


SET_PUBLISHED = {"gcc", "make", "git", "gosu", "libopenmpi-dev"}


def _fbFakeArchive(sName, sSeries):
    assert sSeries == "noble", sSeries
    return sName in SET_PUBLISHED


def _fconfigWith(listPackages, sBaseImage="ubuntu:24.04"):
    return SimpleNamespace(
        listSystemPackages=listPackages, sBaseImage=sBaseImage,
    )


@pytest.mark.falsification
def test_a_name_ubuntu_does_not_publish_is_refused():
    """Kills: the unknown-name walk returning nothing, under which every
    apt name passes and the build discovers the typo minutes in."""
    preflightNames = fpreflightSystemPackageNames(
        _fconfigWith(["gcc", "libopnmpi-dev", "make"]), _fbFakeArchive,
    )
    assert preflightNames.sLevel == S_LEVEL_FAIL
    assert preflightNames.sName == S_PREFLIGHT_NAME
    assert "'libopnmpi-dev'" in preflightNames.sMessage
    assert "noble" in preflightNames.sMessage
    assert "vaibify.yml" in preflightNames.sRemediation


def test_names_ubuntu_publishes_are_silent():
    assert fpreflightSystemPackageNames(
        _fconfigWith(["gcc", "make", "git"]), _fbFakeArchive,
    ) is None
    assert fpreflightSystemPackageNames(_fconfigWith([]), _fbFakeArchive) is None


@pytest.mark.falsification
def test_a_base_image_outside_the_known_releases_is_not_graded():
    """The archive answer is series-specific. Grading a Debian or
    Fedora base against Ubuntu noble would refuse names that exist.

    Kills: defaulting the series to noble for any base image.
    """
    preflightNames = fpreflightSystemPackageNames(
        _fconfigWith(["gcc"], sBaseImage="debian:bookworm"),
        lambda sName, sSeries: False,
    )
    assert preflightNames.sLevel == S_LEVEL_NOT_CHECKED
    assert "baseImage" in preflightNames.sMessage


def test_the_series_is_read_from_the_base_image():
    assert fsSeriesForBaseImage("ubuntu:24.04") == "noble"
    assert fsSeriesForBaseImage("ubuntu:22.04") == "jammy"
    assert fsSeriesForBaseImage(
        "ubuntu:24.04@sha256:" + "a" * 64,
    ) == "noble"
    assert fsSeriesForBaseImage("ubuntu:18.04") == ""
    assert fsSeriesForBaseImage("python:3.12-slim") == ""
    assert fsSeriesForBaseImage("") == ""


@pytest.mark.falsification
def test_an_archive_that_does_not_answer_never_refuses():
    """Kills: reading an unreachable archive as "the name does not
    exist", under which every offline researcher's build is refused."""
    def fnRaise(sName, sSeries):
        raise ArchiveUnreachableError("Launchpad could not be asked")

    preflightNames = fpreflightSystemPackageNames(
        _fconfigWith(["gcc"]), fnRaise,
    )
    assert preflightNames.sLevel == S_LEVEL_NOT_CHECKED
    assert "Launchpad could not be asked" in preflightNames.sMessage


def test_a_version_pin_or_architecture_suffix_asks_about_the_package():
    listUnknown = flistUnknownSystemPackages(
        ["gcc=4:13.2.0-7ubuntu1", "make:amd64", "libopnmpi-dev"],
        "noble", _fbFakeArchive,
    )
    assert listUnknown == ["libopnmpi-dev"]


def test_the_probe_reads_total_size_and_raises_on_anything_else():
    class _ResponseJson:
        def __init__(self, sBody):
            self._sBody = sBody

        def read(self):
            return self._sBody.encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    listUrls = []

    def fnAnswer(sUrl, timeout=None):
        listUrls.append(sUrl)
        return _ResponseJson(json.dumps(
            {"total_size": 3 if "gosu" in sUrl else 0, "entries": []},
        ))

    with patch("urllib.request.urlopen", fnAnswer):
        assert fbPackageExistsInArchive("gosu", "noble") is True
        assert fbPackageExistsInArchive("nosuchpackagezz", "noble") is False
    assert "binary_name=gosu" in listUrls[0]
    assert "exact_match=true" in listUrls[0]
    assert "noble" in listUrls[0]

    def fnNetworkError(sUrl, timeout=None):
        raise urllib.error.URLError("name resolution failed")

    with patch("urllib.request.urlopen", fnNetworkError):
        with pytest.raises(ArchiveUnreachableError):
            fbPackageExistsInArchive("gcc", "noble")

    def fnNoTotal(sUrl, timeout=None):
        return _ResponseJson(json.dumps({"entries": []}))

    with patch("urllib.request.urlopen", fnNoTotal):
        with pytest.raises(ArchiveUnreachableError):
            fbPackageExistsInArchive("gcc", "noble")
