"""The toolchain epoch is pinned, and its two halves agree.

The Dockerfile pins 45 package versions AND the date of the archive
snapshot they are fetched from. Those are two halves of one statement,
edited in the same file by different hands at different times, and
nothing else in the repository notices when they drift apart. A date
whose archive no longer carries a pinned version is a build that
cannot succeed -- discovered, before this existed, an hour into the
image lane.

The network half of that check is the CI lane (`--verify`); what lives
here is everything that can be established without reaching Ubuntu,
and the failure mode those tests exist for is a PARSER that silently
matches nothing. A pin regex that stops matching turns every
downstream check into a confident statement about the empty set, which
is why fdictReadToolchainPins raises rather than returning {}.
"""

import re
from pathlib import Path

import pytest

from tools.checkToolchainEpoch import (
    fdictReadToolchainPins,
    flistFindUnresolvablePins,
    flistProposeEpochChanges,
    fsReadSnapshotDate,
    fsSnapshotBaseUrl,
)


__all__ = [
    "testTheDockerfilePinsASnapshotDate",
    "testTheDockerfilesPinsAreAllParsed",
    "testEveryParsedPinLooksLikeAPackageVersion",
    "testAMissingSnapshotDateIsRefusedNotDefaulted",
    "testAPinBlockThatMatchesNothingRaises",
    "testTheSnapshotUrlIsTheUbuntuSnapshotService",
    "testAResolvablePinSetReportsNothingUnresolvable",
    "testAnAbsentVersionIsReportedWithWhatTheArchiveHas",
    "testAProposalListsEveryCandidateRatherThanChoosing",
    "testTheToolchainBlockActuallyUsesTheSnapshotDate",
    "testTheLiveArchiveComparisonIsNotOnThePullRequestPath",
]


REPO_ROOT = Path(__file__).resolve().parent.parent
PATH_DOCKERFILE = (
    REPO_ROOT / "vaibify" / "containerImage" / "Dockerfile"
)
S_DOCKERFILE_TEXT = PATH_DOCKERFILE.read_text(encoding="utf-8")


def testTheDockerfilePinsASnapshotDate():
    sDate = fsReadSnapshotDate(S_DOCKERFILE_TEXT)
    assert re.fullmatch(r"\d{8}", sDate), sDate


def testTheDockerfilesPinsAreAllParsed():
    """The count is not asserted -- it moves with a legitimate edit --
    but an empty or tiny parse means the regex stopped matching."""
    dictPins = fdictReadToolchainPins(S_DOCKERFILE_TEXT)
    assert len(dictPins) > 40, f"parsed only {len(dictPins)} pins"
    for sExpected in ("gcc", "libc6", "make", "linux-libc-dev"):
        assert sExpected in dictPins, f"{sExpected} was not parsed"


def testEveryParsedPinLooksLikeAPackageVersion():
    """A regex that over-matches captures shell fragments as versions."""
    for sPackage, sVersion in fdictReadToolchainPins(
        S_DOCKERFILE_TEXT,
    ).items():
        assert re.fullmatch(r"[0-9][A-Za-z0-9:.~+-]*", sVersion), (
            f"{sPackage}={sVersion} is not a Debian version"
        )


def testAMissingSnapshotDateIsRefusedNotDefaulted():
    """Defaulting to 'today' would silently unfreeze the epoch."""
    with pytest.raises(ValueError, match="APT_SNAPSHOT_DATE"):
        fsReadSnapshotDate("FROM ubuntu:24.04\nRUN apt-get update\n")


def testAPinBlockThatMatchesNothingRaises():
    with pytest.raises(ValueError, match="no toolchain pins"):
        fdictReadToolchainPins("FROM ubuntu:24.04\n")


def testTheSnapshotUrlIsTheUbuntuSnapshotService():
    assert fsSnapshotBaseUrl("20260909") == (
        "https://snapshot.ubuntu.com/ubuntu/20260909T000000Z"
    )


def testAResolvablePinSetReportsNothingUnresolvable():
    assert flistFindUnresolvablePins(
        {"libc6": "2.39-0ubuntu8.8"},
        {"libc6": {"2.39-0ubuntu8.8", "2.39-0ubuntu8"}},
    ) == []


def testAnAbsentVersionIsReportedWithWhatTheArchiveHas():
    """The remedy needs the alternatives, not just the absence."""
    listFound = flistFindUnresolvablePins(
        {"libc6": "2.39-0ubuntu8.8"},
        {"libc6": {"2.39-0ubuntu8.9"}},
    )
    assert listFound == [("libc6", "2.39-0ubuntu8.8", ["2.39-0ubuntu8.9"])]


def testAProposalListsEveryCandidateRatherThanChoosing():
    """Picking between candidates needs dpkg version ordering plus a
    judgement about what results rest on. A guess here writes a pin
    nobody reviewed, so both are reported and a human chooses."""
    listChanges = flistProposeEpochChanges(
        {"libc6": "2.39-0ubuntu8.8", "make": "4.3-4.1build2"},
        {
            "libc6": {"2.39-0ubuntu8", "2.39-0ubuntu8.9"},
            "make": {"4.3-4.1build2"},
        },
    )
    assert listChanges == [
        ("libc6", "2.39-0ubuntu8.8", ["2.39-0ubuntu8", "2.39-0ubuntu8.9"]),
    ]


def testTheToolchainBlockActuallyUsesTheSnapshotDate():
    """Declaring the ARG and not reading it would leave the pins being
    fetched from the live archive while the file claims otherwise."""
    assert "${APT_SNAPSHOT_DATE}" in S_DOCKERFILE_TEXT
    assert "snapshot.ubuntu.com/ubuntu/%sT000000Z" in S_DOCKERFILE_TEXT
    iArg = S_DOCKERFILE_TEXT.index("ARG APT_SNAPSHOT_DATE=")
    iUse = S_DOCKERFILE_TEXT.index("${APT_SNAPSHOT_DATE}")
    assert iArg < iUse, "the ARG is declared after the stage that reads it"


def testTheLiveArchiveComparisonIsNotOnThePullRequestPath():
    """The epoch is a maintainer's decision on their own cadence. Asking
    it from a pull-request lane is what painted every unrelated PR red,
    and re-adding it there would restore exactly that."""
    sFreshBuild = (
        REPO_ROOT / ".github" / "workflows" / "freshImageBuild.yml"
    ).read_text(encoding="utf-8")
    assert "--verify" in sFreshBuild
    assert "--propose" not in sFreshBuild
    assert "checkToolchainPinDrift" not in sFreshBuild

    sEpochLane = (
        REPO_ROOT / ".github" / "workflows" / "toolchainEpoch.yml"
    ).read_text(encoding="utf-8")
    assert "--propose" in sEpochLane
    assert "pull_request" not in sEpochLane
