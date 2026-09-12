#!/usr/bin/env python3
"""Verify the pinned toolchain epoch, or propose the next one.

The Dockerfile pins 45 exact package versions AND the date of the
archive snapshot they are fetched from (``APT_SNAPSHOT_DATE``). Two
axes that must agree: a date whose archive no longer carries a pinned
version is a build that cannot succeed, and nothing else in the
repository checks that they still do.

``--verify`` is the agreement check. It fetches the package indices
for the pinned date and confirms every pin is present. It makes no
network claim beyond that and no judgement about whether the versions
are GOOD -- only that the file is internally consistent.

``--propose`` answers the other question: what would change if the
epoch moved to a later date? That is an EPOCH BUMP -- how Ubuntu's
security and correctness fixes reach vaibify users -- and it changes
the compiler, so it is a reviewed pull request followed by a re-run
and re-verify, never an automatic commit.

**This tool deliberately holds no model of which packages "matter".**
The sibling tool checkToolchainPinDrift.py can prove two packages are
byte-identical; nothing can prove a difference is harmless. A glibc
SRU changes real bytes and no review distinguishes "this moves a
number" from "this does not", which is exactly why the decision is
about TIMING rather than about each package.

    python tools/checkToolchainEpoch.py --verify
    python tools/checkToolchainEpoch.py --propose
    python tools/checkToolchainEpoch.py --propose --date 20261001
"""

import argparse
import datetime
import gzip
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path


__all__ = [
    "fsReadSnapshotDate",
    "fdictReadToolchainPins",
    "fdictFetchSnapshotVersions",
    "flistFindUnresolvablePins",
    "flistProposeEpochChanges",
    "fsSnapshotBaseUrl",
    "main",
]


REPO_ROOT = Path(__file__).resolve().parent.parent
PATH_DOCKERFILE = REPO_ROOT / "vaibify" / "containerImage" / "Dockerfile"

S_SNAPSHOT_HOST = "https://snapshot.ubuntu.com/ubuntu"
T_SNAPSHOT_SUITES = (
    "noble", "noble-updates", "noble-security", "noble-backports",
)
T_SNAPSHOT_COMPONENTS = ("main", "restricted", "universe", "multiverse")
S_SNAPSHOT_ARCHITECTURE = "amd64"

# The pin block's shape: a continued shell line per package, and one
# final entry closing the `if !` test. Both forms are read from the
# Dockerfile rather than restated, so a pin added there cannot go
# unchecked here.
REGEX_CONTINUED_PIN = re.compile(
    r"^\s+([a-z0-9][a-z0-9+.-]*)=(\S+?) \\$", re.M,
)
REGEX_FINAL_PIN = re.compile(
    r"^\s+([a-z0-9][a-z0-9+.-]*)=(\S+); then", re.M,
)
REGEX_SNAPSHOT_DATE = re.compile(r"^ARG APT_SNAPSHOT_DATE=(\d{8})$", re.M)

REGEX_PACKAGE_STANZA_NAME = re.compile(r"^Package: (\S+)$", re.M)
REGEX_PACKAGE_STANZA_VERSION = re.compile(r"^Version: (\S+)$", re.M)


def fsSnapshotBaseUrl(sDate):
    """Return the snapshot archive root for a YYYYMMDD date."""
    return f"{S_SNAPSHOT_HOST}/{sDate}T000000Z"


def fsReadSnapshotDate(sDockerfileText):
    """Return the pinned APT_SNAPSHOT_DATE, or raise ValueError."""
    matchDate = REGEX_SNAPSHOT_DATE.search(sDockerfileText)
    if not matchDate:
        raise ValueError(
            "the Dockerfile declares no ARG APT_SNAPSHOT_DATE; the "
            "toolchain epoch is unpinned",
        )
    return matchDate.group(1)


def fdictReadToolchainPins(sDockerfileText):
    """Return {package: version} for every pin in the toolchain block."""
    dictPins = dict(REGEX_CONTINUED_PIN.findall(sDockerfileText))
    dictPins.update(REGEX_FINAL_PIN.findall(sDockerfileText))
    if not dictPins:
        raise ValueError(
            "no toolchain pins found in the Dockerfile; the pin block's "
            "shape changed and this tool would silently check nothing",
        )
    return dictPins


def fdictFetchSnapshotVersions(sDate, listPackageNames):
    """Return {package: set(versions)} present in a date's indices.

    Only the named packages are kept: the full noble index is a
    hundred thousand stanzas and the caller wants 45 of them.
    """
    setWanted = set(listPackageNames)
    dictVersions = {}
    iIndicesRead = 0
    for sSuite in T_SNAPSHOT_SUITES:
        for sComponent in T_SNAPSHOT_COMPONENTS:
            sUrl = (
                f"{fsSnapshotBaseUrl(sDate)}/dists/{sSuite}/{sComponent}"
                f"/binary-{S_SNAPSHOT_ARCHITECTURE}/Packages.gz"
            )
            try:
                baRaw = urllib.request.urlopen(sUrl, timeout=120).read()
            except (urllib.error.URLError, OSError):
                continue
            iIndicesRead += 1
            sText = gzip.decompress(baRaw).decode("utf-8", "replace")
            for sStanza in sText.split("\n\n"):
                matchName = REGEX_PACKAGE_STANZA_NAME.search(sStanza)
                if not matchName or matchName.group(1) not in setWanted:
                    continue
                matchVersion = REGEX_PACKAGE_STANZA_VERSION.search(sStanza)
                if matchVersion:
                    dictVersions.setdefault(
                        matchName.group(1), set(),
                    ).add(matchVersion.group(1))
    if not iIndicesRead:
        raise RuntimeError(
            f"no package index could be read for snapshot {sDate}. "
            "Either the date has no snapshot or snapshot.ubuntu.com is "
            "unreachable -- this is NOT evidence that the pins are bad.",
        )
    return dictVersions


def flistFindUnresolvablePins(dictPins, dictAvailable):
    """Return (package, pinned, available) for pins absent at a date."""
    return sorted(
        (sPackage, sPinned, sorted(dictAvailable.get(sPackage, ())))
        for sPackage, sPinned in dictPins.items()
        if sPinned not in dictAvailable.get(sPackage, set())
    )


def flistProposeEpochChanges(dictPins, dictAvailable):
    """Return the pins a later snapshot would move, as (name, old, new).

    A pinned version absent from the later index is one the archive has
    moved past. The replacement is whatever that index carries; when it
    carries several, all are reported rather than one being chosen,
    because picking would require dpkg version ordering and a wrong
    choice here writes a pin nobody reviewed.
    """
    return [
        (sPackage, sPinned, sorted(dictAvailable.get(sPackage, ())))
        for sPackage, sPinned in sorted(dictPins.items())
        if sPinned not in dictAvailable.get(sPackage, set())
    ]


def _fsDefaultProposalDate():
    """Yesterday, UTC. Today's snapshot may not be published yet."""
    dateYesterday = (
        datetime.datetime.now(datetime.timezone.utc).date()
        - datetime.timedelta(days=1)
    )
    return dateYesterday.strftime("%Y%m%d")


def _fiRunVerify(dictPins, sPinnedDate):
    """Confirm every pin resolves at the pinned snapshot date."""
    dictAvailable = fdictFetchSnapshotVersions(sPinnedDate, dictPins)
    listUnresolvable = flistFindUnresolvablePins(dictPins, dictAvailable)
    if listUnresolvable:
        print(
            f"{len(listUnresolvable)} of {len(dictPins)} pins do NOT "
            f"resolve at snapshot {sPinnedDate}:",
        )
        for sPackage, sPinned, listSeen in listUnresolvable:
            print(f"  {sPackage}={sPinned}  (snapshot has: {listSeen})")
        print(
            "\nThe Dockerfile disagrees with its own snapshot date. "
            "Either the date or the pins were edited alone.",
        )
        return 1
    print(f"All {len(dictPins)} pins resolve at snapshot {sPinnedDate}.")
    return 0


def _fiRunPropose(dictPins, sPinnedDate, sCandidateDate):
    """Report what moving the epoch to a later date would change."""
    dictAvailable = fdictFetchSnapshotVersions(sCandidateDate, dictPins)
    listChanges = flistProposeEpochChanges(dictPins, dictAvailable)
    if not listChanges:
        print(
            f"Snapshot {sCandidateDate} carries the same {len(dictPins)} "
            f"versions as the pinned epoch {sPinnedDate}. No bump needed.",
        )
        return 0
    print(
        f"Moving the epoch {sPinnedDate} -> {sCandidateDate} changes "
        f"{len(listChanges)} of {len(dictPins)} pinned packages:\n",
    )
    for sPackage, sPinned, listSeen in listChanges:
        sReplacement = listSeen[0] if len(listSeen) == 1 else str(listSeen)
        print(f"  {sPackage}: {sPinned} -> {sReplacement}")
    print(
        "\nThis is an EPOCH BUMP. It changes the compiler and libc a "
        "researcher's binaries are built against, so results produced "
        "before it must be RE-RUN AND RE-VERIFIED. Land it as its own "
        "pull request.",
    )
    return 0


def main():
    parserArguments = argparse.ArgumentParser(
        description="Verify or propose the pinned toolchain epoch.",
    )
    groupMode = parserArguments.add_mutually_exclusive_group(required=True)
    groupMode.add_argument(
        "--verify", action="store_true",
        help="confirm every pin resolves at the pinned snapshot date",
    )
    groupMode.add_argument(
        "--propose", action="store_true",
        help="report what moving the epoch to a later date would change",
    )
    parserArguments.add_argument(
        "--date", dest="sCandidateDate", default=None,
        help="candidate snapshot date YYYYMMDD (default: yesterday UTC)",
    )
    namespaceArguments = parserArguments.parse_args()

    try:
        sDockerfileText = PATH_DOCKERFILE.read_text(encoding="utf-8")
        sPinnedDate = fsReadSnapshotDate(sDockerfileText)
        dictPins = fdictReadToolchainPins(sDockerfileText)
    except (OSError, ValueError) as errorRead:
        print(f"checkToolchainEpoch: {errorRead}", file=sys.stderr)
        return 2

    try:
        if namespaceArguments.verify:
            return _fiRunVerify(dictPins, sPinnedDate)
        return _fiRunPropose(
            dictPins, sPinnedDate,
            namespaceArguments.sCandidateDate or _fsDefaultProposalDate(),
        )
    except RuntimeError as errorFetch:
        print(f"checkToolchainEpoch: {errorFetch}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
