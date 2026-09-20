#!/usr/bin/env python3
"""Verify the pinned toolchain epoch, or propose the next one.

The Dockerfile pins exact package versions, one list per architecture
it builds on, AND the date of the archive snapshot they are fetched
from (``APT_SNAPSHOT_DATE``). Two axes that must agree: a date whose
archive no longer carries a pinned version is a build that cannot
succeed, and nothing else in the repository checks that they still do.

``--verify`` is the agreement check. It fetches the package indices
for the pinned date, per architecture, and confirms every pin is
present -- and that the architecture lists name one version per
package, since one Ubuntu source upload builds every architecture. It
makes no network claim beyond that and no judgement about whether the
versions are GOOD -- only that the file is internally consistent.

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
    python tools/checkToolchainEpoch.py --verify --architecture arm64
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


REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# One parser for the pin blocks, shared with the drift tool: it refuses
# a partial parse, and a second regex here once read the whole file and
# would have merged both architectures' lists into one.
from tools.checkToolchainPinDrift import (  # noqa: E402
    T_PINNED_ARCHITECTURES,
    fdictParsePinnedVersions,
)


__all__ = [
    "fsReadSnapshotDate",
    "fdictReadToolchainPins",
    "fdictReadToolchainPinsByArchitecture",
    "flistFindVersionDisagreements",
    "fdictFetchSnapshotVersions",
    "flistFindUnresolvablePins",
    "flistProposeEpochChanges",
    "fsSnapshotBaseUrl",
    "main",
]


PATH_DOCKERFILE = REPO_ROOT / "vaibify" / "containerImage" / "Dockerfile"

S_SNAPSHOT_HOST = "https://snapshot.ubuntu.com/ubuntu"
T_SNAPSHOT_SUITES = (
    "noble", "noble-updates", "noble-security", "noble-backports",
)
T_SNAPSHOT_COMPONENTS = ("main", "restricted", "universe", "multiverse")

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


def fdictReadToolchainPins(sDockerfileText, sArchitecture):
    """Return {package: version} for one architecture's pin block."""
    return fdictParsePinnedVersions(sDockerfileText, sArchitecture)


def fdictReadToolchainPinsByArchitecture(sDockerfileText, listArchitectures):
    """Return {architecture: {package: version}} for the named blocks."""
    return {
        sArchitecture: fdictReadToolchainPins(sDockerfileText, sArchitecture)
        for sArchitecture in listArchitectures
    }


def flistFindVersionDisagreements(dictPinsByArchitecture):
    """Return (package, {architecture: version}) where the lists differ.

    A package pinned by several architectures must carry one version:
    Ubuntu builds every architecture from a single source upload, so
    two versions of one package across the lists means one list was
    edited alone, and the image would compile with a different
    toolchain depending on the daemon that built it.
    """
    dictVersionsByPackage = {}
    for sArchitecture, dictPins in dictPinsByArchitecture.items():
        for sPackage, sVersion in dictPins.items():
            dictVersionsByPackage.setdefault(sPackage, {})[sArchitecture] = (
                sVersion
            )
    return sorted(
        (sPackage, dictByArchitecture)
        for sPackage, dictByArchitecture in dictVersionsByPackage.items()
        if len(set(dictByArchitecture.values())) > 1
    )


def fdictFetchSnapshotVersions(sDate, listPackageNames, sArchitecture):
    """Return {package: set(versions)} present in a date's indices.

    Only the named packages are kept: the full noble index is a
    hundred thousand stanzas and the caller wants a few dozen of them.
    """
    setWanted = set(listPackageNames)
    dictVersions = {}
    iIndicesRead = 0
    for sSuite in T_SNAPSHOT_SUITES:
        for sComponent in T_SNAPSHOT_COMPONENTS:
            sUrl = (
                f"{fsSnapshotBaseUrl(sDate)}/dists/{sSuite}/{sComponent}"
                f"/binary-{sArchitecture}/Packages.gz"
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
            f"no {sArchitecture} package index could be read for snapshot "
            f"{sDate}. Either the date has no snapshot or "
            "snapshot.ubuntu.com is unreachable -- this is NOT evidence "
            "that the pins are bad.",
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


def _fiReportDisagreements(dictPinsByArchitecture):
    """Print any package the architecture lists pin differently; 1 if so."""
    listDisagreements = flistFindVersionDisagreements(dictPinsByArchitecture)
    if not listDisagreements:
        return 0
    print(
        f"{len(listDisagreements)} package(s) are pinned at DIFFERENT "
        "versions across the architecture lists:",
    )
    for sPackage, dictByArchitecture in listDisagreements:
        print(f"  {sPackage}: {dictByArchitecture}")
    print(
        "\nOne architecture's list was edited alone. The lists carry one "
        "version per package, because one Ubuntu source upload builds "
        "every architecture.",
    )
    return 1


def _fiRunVerify(dictPinsByArchitecture, sPinnedDate):
    """Confirm every pin resolves at the pinned snapshot date."""
    if _fiReportDisagreements(dictPinsByArchitecture):
        return 1
    iStatus = 0
    for sArchitecture, dictPins in dictPinsByArchitecture.items():
        dictAvailable = fdictFetchSnapshotVersions(
            sPinnedDate, dictPins, sArchitecture,
        )
        listUnresolvable = flistFindUnresolvablePins(dictPins, dictAvailable)
        if not listUnresolvable:
            print(
                f"[{sArchitecture}] All {len(dictPins)} pins resolve at "
                f"snapshot {sPinnedDate}.",
            )
            continue
        iStatus = 1
        print(
            f"[{sArchitecture}] {len(listUnresolvable)} of {len(dictPins)} "
            f"pins do NOT resolve at snapshot {sPinnedDate}:",
        )
        for sPackage, sPinned, listSeen in listUnresolvable:
            print(f"  {sPackage}={sPinned}  (snapshot has: {listSeen})")
    if iStatus:
        print(
            "\nThe Dockerfile disagrees with its own snapshot date. "
            "Either the date or the pins were edited alone.",
        )
    return iStatus


def _fnPrintProposal(sArchitecture, listChanges, iPinCount):
    """Print one architecture's epoch-move report."""
    print(f"[{sArchitecture}] {len(listChanges)} of {iPinCount} pins move:")
    for sPackage, sPinned, listSeen in listChanges:
        sReplacement = listSeen[0] if len(listSeen) == 1 else str(listSeen)
        print(f"  {sPackage}: {sPinned} -> {sReplacement}")


def _fiRunPropose(dictPinsByArchitecture, sPinnedDate, sCandidateDate):
    """Report what moving the epoch to a later date would change."""
    bAnyChange = False
    for sArchitecture, dictPins in dictPinsByArchitecture.items():
        dictAvailable = fdictFetchSnapshotVersions(
            sCandidateDate, dictPins, sArchitecture,
        )
        listChanges = flistProposeEpochChanges(dictPins, dictAvailable)
        if listChanges:
            bAnyChange = True
            _fnPrintProposal(sArchitecture, listChanges, len(dictPins))
    if not bAnyChange:
        print(
            f"Snapshot {sCandidateDate} carries the same versions as the "
            f"pinned epoch {sPinnedDate} on every architecture. "
            "No bump needed.",
        )
        return 0
    print(
        f"\nMoving the epoch {sPinnedDate} -> {sCandidateDate} is an EPOCH "
        "BUMP. It changes the compiler and libc a researcher's binaries "
        "are built against, so results produced before it must be RE-RUN "
        "AND RE-VERIFIED. Land it as its own pull request, moving every "
        "architecture's list together.",
    )
    return 0


def _flistArchitecturesToCheck(sArchitecture):
    """Return the architectures one --architecture value names."""
    if sArchitecture == "all":
        return list(T_PINNED_ARCHITECTURES)
    return [sArchitecture]


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
    parserArguments.add_argument(
        "--architecture", dest="sArchitecture", default="all",
        choices=("all",) + T_PINNED_ARCHITECTURES,
        help="which pin list to read (default: every pinned architecture)",
    )
    namespaceArguments = parserArguments.parse_args()

    try:
        sDockerfileText = PATH_DOCKERFILE.read_text(encoding="utf-8")
        sPinnedDate = fsReadSnapshotDate(sDockerfileText)
        dictPinsByArchitecture = fdictReadToolchainPinsByArchitecture(
            sDockerfileText,
            _flistArchitecturesToCheck(namespaceArguments.sArchitecture),
        )
    except (OSError, ValueError) as errorRead:
        print(f"checkToolchainEpoch: {errorRead}", file=sys.stderr)
        return 2

    try:
        if namespaceArguments.verify:
            return _fiRunVerify(dictPinsByArchitecture, sPinnedDate)
        return _fiRunPropose(
            dictPinsByArchitecture, sPinnedDate,
            namespaceArguments.sCandidateDate or _fsDefaultProposalDate(),
        )
    except RuntimeError as errorFetch:
        print(f"checkToolchainEpoch: {errorFetch}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
