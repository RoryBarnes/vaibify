"""One image build reads one Ubuntu archive state.

Every amd64 build failed on 2026-09-24 with nothing in vaibify changed:
security.ubuntu.com is several servers that disagreed while a security
update propagated, each RUN step fetched its own index, and one build
installed a library at the new version and then found only the old
version of its -dev package. The Dockerfile now points Ubuntu's archive
at one snapshot.ubuntu.com timestamp before the first apt step.

These tests read the recipe as text, which is all a unit test can do
with it. What they cannot show -- that the recipe actually builds and
resolves consistently -- was checked by building it (arm64 with the
argument, amd64 without) against snapshot.ubuntu.com; the image lane
in CI is the standing check.
"""

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from vaibify.docker import imageBuilder


PATH_DOCKERFILE = (
    Path(__file__).resolve().parent.parent
    / "vaibify" / "containerImage" / "Dockerfile"
)
S_DOCKERFILE_TEXT = PATH_DOCKERFILE.read_text(encoding="utf-8")
S_SNAPSHOT_SOURCES_WRITE = "/etc/apt/sources.list.d/vaibifyBuildSnapshot.list"
T_LIVE_ARCHIVE_HOSTS = (
    "archive.ubuntu.com", "security.ubuntu.com", "ports.ubuntu.com",
)


def _flistExecutableLines():
    """Return the Dockerfile's non-comment lines."""
    return [
        sLine for sLine in S_DOCKERFILE_TEXT.splitlines()
        if not sLine.lstrip().startswith("#")
    ]


@pytest.mark.falsification
def testTheArchiveIsPinnedBeforeTheFirstAptFetch():
    """No apt step may fetch an index before the build's snapshot is in place.

    Kills: dropping the snapshot step, or moving it below any apt step,
    which lets that step read the live mirrors again.
    """
    sExecutable = "\n".join(_flistExecutableLines())
    iSnapshotWrite = sExecutable.find(S_SNAPSHOT_SOURCES_WRITE)
    listUpdates = [
        matchUpdate.start()
        for matchUpdate in re.finditer(r"apt-get\b[^\n]*\bupdate\b", sExecutable)
    ]
    assert iSnapshotWrite != -1, "the build no longer pins its archive"
    assert listUpdates, "found no apt fetch at all; the test is blind"
    assert iSnapshotWrite < min(listUpdates)


def testTheBuildNeverNamesALiveUbuntuMirror():
    """No fallback: a live mirror anywhere in the recipe reopens the defect."""
    listOffending = [
        sLine for sLine in _flistExecutableLines()
        if any(sHost in sLine for sHost in T_LIVE_ARCHIVE_HOSTS)
    ]
    assert listOffending == []


def testTheSnapshotReplacesOnlyUbuntusOwnSources():
    """A third-party repository in the base image (CUDA's) must survive."""
    listRemovals = [
        sLine for sLine in _flistExecutableLines()
        if "rm -f" in sLine and "sources.list" in sLine
        and "vaibifyLiveSources" not in sLine
    ]
    sFirstRemoval = listRemovals[0]
    assert "/etc/apt/sources.list.d/ubuntu.sources" in sFirstRemoval
    assert "sources.list.d/*" not in sFirstRemoval


def testTheSnapshotIsRecordedInsideTheImage():
    assert "/etc/vaibify/aptArchiveSnapshot" in S_DOCKERFILE_TEXT
    assert 'LABEL vaibify-apt-build-snapshot="${APT_BUILD_SNAPSHOT}"' in (
        S_DOCKERFILE_TEXT
    )


def testAPlainDockerBuildStillPicksOneSnapshot():
    """An exported copy rebuilt with no build argument computes its own."""
    assert 'ARG APT_BUILD_SNAPSHOT=""' in S_DOCKERFILE_TEXT
    assert '"${APT_BUILD_SNAPSHOT:-$(date -u +%Y%m%dT000000Z)}"' in (
        S_DOCKERFILE_TEXT
    )


def testTheBuildPassesTheMostRecentMidnightUtc():
    """A local evening is already the next day in UTC."""
    datetimeLocalEvening = datetime(
        2026, 9, 24, 23, 30, tzinfo=timezone(timedelta(hours=-7)),
    )
    assert imageBuilder.fsArchiveSnapshotForBuild(datetimeLocalEvening) == (
        "20260925T000000Z"
    )


def testEveryBuildOfOneDayPassesTheSameSnapshot():
    """Equal values keep Docker's layer cache across a day's rebuilds."""
    datetimeMorning = datetime(2026, 9, 24, 0, 5, tzinfo=timezone.utc)
    datetimeNight = datetime(2026, 9, 24, 23, 55, tzinfo=timezone.utc)
    assert imageBuilder.fsArchiveSnapshotForBuild(datetimeMorning) == (
        imageBuilder.fsArchiveSnapshotForBuild(datetimeNight)
    )


def testTheBuildCommandCarriesTheSnapshot(monkeypatch):
    monkeypatch.setattr(
        imageBuilder, "fsArchiveSnapshotForBuild",
        lambda datetimeNow=None: "20260924T000000Z",
    )
    from tests.testImageBuilderExtended import _fConfigWithFeatures
    listPairs = imageBuilder._flistBuildArgPairs(
        _fConfigWithFeatures(), "ubuntu:24.04",
    )
    assert "APT_BUILD_SNAPSHOT=20260924T000000Z" in listPairs
