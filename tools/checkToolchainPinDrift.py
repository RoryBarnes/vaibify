"""Decide whether a rotated toolchain pin changed any COMPILE INPUT.

The Dockerfile pins 45 exact package versions and stops the build when
Ubuntu drops one from the archive. That refusal is correct, but it
cannot distinguish the two things it fires on:

  * a genuinely new package -- different headers, different code, a
    result that may legitimately move; and
  * a version string that rolled forward while every installed byte
    stayed the same (`linux-libc-dev` does this on Ubuntu's kernel SRU
    cadence, roughly monthly).

This tool answers that question with evidence rather than judgement. It
fetches the superseded package and the replacement, and compares their
payloads file by file.

THE VERDICT IS ASYMMETRIC, AND THAT IS THE WHOLE DESIGN. It can prove
"nothing changed"; it can never prove "this change is harmless". So
S_VERDICT_IDENTICAL is the only outcome that may pass unattended, and
every other outcome -- a real difference, a package that cannot be
fetched, a decompressor that is missing -- escalates to a maintainer.
Unknown routes to a human, never to green.

It also asks ONE mechanical question of every package alike: did any
file that is not documentation change? It holds no model of which
packages "can affect a number", because that reasoning is what failed
here before: kernel UAPI headers were called unable to reach a result,
and a plain C program including <errno.h> compiles four of them.
"""

import argparse
import hashlib
import io
import re
import subprocess
import sys
import tarfile
import urllib.error
import urllib.request
from pathlib import Path


__all__ = [
    "fnMain",
    "fdictParsePinnedVersions",
    "fsParseBaseImage",
    "fbIsDocumentationMember",
    "fdictHashPayloadMembers",
    "ftComparePackagePayloads",
    "flistDetectDrift",
    "flistDetectClosureMembershipChanges",
    "fsSupersededPoolBasename",
    "fsApplyPinBumps",
    "S_VERDICT_IDENTICAL",
    "S_VERDICT_CHANGED",
    "S_VERDICT_UNCOMPARABLE",
]


REPO_ROOT = Path(__file__).resolve().parent.parent
PATH_DOCKERFILE = REPO_ROOT / "vaibify" / "containerImage" / "Dockerfile"

S_VERDICT_IDENTICAL = "IDENTICAL"
S_VERDICT_CHANGED = "CHANGED"
S_VERDICT_UNCOMPARABLE = "UNCOMPARABLE"

S_ARCHIVE_BASE = "http://archive.ubuntu.com/ubuntu/"
S_SNAPSHOT_BASE = "https://snapshot.ubuntu.com/ubuntu/"

# How far back to look for the superseded package. Ubuntu prunes the
# live pool within weeks; snapshot.ubuntu.com keeps dated mirrors, so a
# few probes back from today find the day the old version was still
# current. A miss is UNCOMPARABLE, never a pass.
T_SNAPSHOT_PROBE_DAYS = (1, 2, 3, 5, 7, 10, 14, 21, 30, 45, 60, 90)

# Payload paths excluded from the comparison. Documentation cannot
# reach a compiled artifact, and the changelog changes on EVERY rebuild
# -- excluding nothing would make the tool answer CHANGED always and
# teach a maintainer to ignore it.
#
# Every entry ends in "/" deliberately. Written as a bare prefix,
# "usr/share/doc" also swallows "usr/share/docbook" and any future
# sibling whose name merely starts the same way, which is a silent
# widening of the exemption -- the same shape as a ".git" prefix
# exempting ".gitignore". testTheDocumentationExemptionIsNarrow pins
# this.
T_DOCUMENTATION_PREFIXES = (
    "usr/share/doc/",
    "usr/share/man/",
    "usr/share/info/",
    "usr/share/lintian/",
)

REGEX_PIN = re.compile(r"^\s+([A-Za-z0-9][A-Za-z0-9+.-]*)=(\S+?)(?: \\)?$", re.M)
REGEX_PIN_SHAPED = re.compile(r"^\s+[A-Za-z0-9][A-Za-z0-9+.-]*=\S+")
REGEX_BASE_IMAGE = re.compile(r"^ARG BASE_IMAGE=(\S+)", re.M)
REGEX_SIMULATED_INSTALL = re.compile(
    r"^Inst (\S+)(?: \[[^\]]*\])? \((\S+) ", re.M
)
REGEX_POOL_FILENAME = re.compile(r"^Filename: (\S+)", re.M)


def fsReadDockerfile():
    """Return the shipped Dockerfile's text."""
    return PATH_DOCKERFILE.read_text(encoding="utf-8")


def fsParseBaseImage(sDockerfileText):
    """Return the pinned BASE_IMAGE reference, digest included."""
    matchBase = REGEX_BASE_IMAGE.search(sDockerfileText)
    if matchBase is None:
        raise ValueError("the Dockerfile declares no ARG BASE_IMAGE")
    return matchBase.group(1)


def fsExtractToolchainBlock(sDockerfileText):
    """Return just the pinned apt block, so no other RUN is scanned."""
    sMarker = "RUN apt-get update \\\n    && if ! apt-get install"
    if sMarker not in sDockerfileText:
        raise ValueError(
            "the Dockerfile no longer contains the pinned toolchain "
            "apt block this tool compares against"
        )
    return sDockerfileText.split(sMarker, 1)[1].split("; then", 1)[0]


def fdictParsePinnedVersions(sDockerfileText):
    """Return {package: pinned version} for the toolchain block.

    Parsed from the shipped Dockerfile rather than a second list, so
    the tool can never grade a pin set the image does not use.
    """
    sBlock = fsExtractToolchainBlock(sDockerfileText)
    dictPinned = dict(REGEX_PIN.findall(sBlock))
    # A PARTIAL parse is the dangerous outcome, not an empty one. One
    # extra space on a pin line drops that package from the checked set
    # and the tool then reports "no drift" about it forever -- silently,
    # because the remaining 44 parse perfectly. Measured: a single
    # trailing space took the count from 45 to 44 with no other signal.
    # So count the pin-shaped lines independently and demand agreement.
    iPinShapedLines = len(
        [
            sLine
            for sLine in sBlock.splitlines()
            if REGEX_PIN_SHAPED.match(sLine)
        ]
    )
    if not dictPinned:
        raise ValueError("parsed zero pins; the block's shape has changed")
    if len(dictPinned) != iPinShapedLines:
        raise ValueError(
            f"parsed {len(dictPinned)} pins but the block holds "
            f"{iPinShapedLines} pin-shaped lines; the unparsed ones would "
            "be silently exempt from every comparison"
        )
    return dictPinned


def fbIsDocumentationMember(sMemberPath):
    """Return True when a payload path is documentation, not a build input."""
    sNormalised = sMemberPath.lstrip("./")
    return sNormalised.startswith(T_DOCUMENTATION_PREFIXES)


def fbaDecompressPayload(baData, sMemberName):
    """Return the decompressed bytes of a .deb's data member."""
    if sMemberName.endswith(".zst"):
        return fbaDecompressZstandard(baData)
    return baData


def fbaDecompressZstandard(baData):
    """Return zstd-decompressed bytes, or raise if no decompressor exists.

    Python gained `compression.zstd` in 3.14; older interpreters need
    the `zstandard` wheel. A missing decompressor raises rather than
    degrades, because the caller turns any raise into UNCOMPARABLE and
    escalates -- silently skipping the comparison would report a clean
    verdict for a package nobody examined.
    """
    try:
        from compression import zstd as moduleZstd

        return moduleZstd.decompress(baData)
    except ImportError:
        pass
    try:
        import zstandard
    except ImportError as errorMissing:
        raise RuntimeError(
            "no zstd decompressor available: this interpreter predates "
            "compression.zstd (3.14) and the 'zstandard' package is not "
            "installed. Install it to compare packages."
        ) from errorMissing
    return zstandard.ZstdDecompressor().stream_reader(io.BytesIO(baData)).read()


def flistReadArchiveMembers(baDeb):
    """Return [(member name, member bytes)] from a .deb's ar container."""
    if baDeb[:8] != b"!<arch>\n":
        raise ValueError("not a Debian package (missing ar signature)")
    iPosition = 8
    listMembers = []
    while iPosition + 60 <= len(baDeb):
        sName = baDeb[iPosition : iPosition + 16].decode("ascii").strip()
        sSize = baDeb[iPosition + 48 : iPosition + 58].decode("ascii").strip()
        iSize = int(sSize)
        iStart = iPosition + 60
        listMembers.append((sName, baDeb[iStart : iStart + iSize]))
        iPosition = iStart + iSize + (iSize % 2)
    return listMembers


def fsMemberIdentity(tarArchive, infoMember):
    """Return a string identifying everything about one archive member.

    Content alone is not the member. A header whose permission bits
    changed, a binary that gained setuid, a file that changed owner, or
    a symlink retargeted at a different architecture's headers are all
    real changes that a content-only hash reports as IDENTICAL --
    measured, not supposed. So the identity carries the type, the mode,
    the ownership, the link target and the PAX records (which is where
    file capabilities ride) alongside the content digest.
    """
    listFacets = [
        f"type:{infoMember.type.decode('ascii')}",
        f"mode:{infoMember.mode:o}",
        f"owner:{infoMember.uid}:{infoMember.gid}",
    ]
    if infoMember.pax_headers:
        listFacets.append(f"pax:{sorted(infoMember.pax_headers.items())}")
    if infoMember.issym() or infoMember.islnk():
        listFacets.append(f"link:{infoMember.linkname}")
    elif infoMember.isfile():
        fileHandle = tarArchive.extractfile(infoMember)
        baContent = b"" if fileHandle is None else fileHandle.read()
        listFacets.append(f"sha256:{hashlib.sha256(baContent).hexdigest()}")
    return "|".join(listFacets)


def fbaExtractArchiveMember(baDeb, sPrefix):
    """Return the decompressed bytes of the named .deb sub-archive."""
    for sName, baMember in flistReadArchiveMembers(baDeb):
        if sName.startswith(sPrefix):
            return fbaDecompressPayload(baMember, sName)
    raise ValueError(f"the package contains no {sPrefix} member")


def fbaNormaliseControlStanza(baContent):
    """Drop the two control fields that MUST differ between versions.

    `Version` and `Installed-Size` differ by definition when a package
    is superseded, so comparing them would make every rotation CHANGED
    and the tool useless. Every other field -- `Depends` above all, but
    also `Provides`, `Conflicts` and `Replaces` -- is compared, because
    a dependency change alters what gets installed alongside this
    package and is exactly the kind of drift a payload diff misses.
    """
    listKept = [
        baLine
        for baLine in baContent.split(b"\n")
        if not baLine.startswith((b"Version:", b"Installed-Size:"))
    ]
    return b"\n".join(listKept)


def fdictHashPayloadMembers(baDeb):
    """Return {namespaced path: identity} for everything that can reach a build.

    Covers BOTH sub-archives. `data.tar` is the installed files;
    `control.tar` carries the maintainer scripts and the dependency
    fields, and a changed `postinst` or `Depends` is a real change that
    comparing installed files alone cannot see.
    """
    dictIdentities = {}
    baPayload = fbaExtractArchiveMember(baDeb, "data.tar")
    with tarfile.open(fileobj=io.BytesIO(baPayload)) as tarPayload:
        for infoMember in tarPayload.getmembers():
            sPath = infoMember.name.lstrip("./")
            if not sPath or fbIsDocumentationMember(sPath):
                continue
            dictIdentities[f"data/{sPath}"] = fsMemberIdentity(
                tarPayload, infoMember
            )
    baControl = fbaExtractArchiveMember(baDeb, "control.tar")
    with tarfile.open(fileobj=io.BytesIO(baControl)) as tarControl:
        for infoMember in tarControl.getmembers():
            sPath = infoMember.name.lstrip("./")
            # md5sums restates data.tar, which is compared above, and it
            # necessarily changes whenever that does.
            if not sPath or sPath == "md5sums":
                continue
            if sPath == "control" and infoMember.isfile():
                fileHandle = tarControl.extractfile(infoMember)
                baStanza = b"" if fileHandle is None else fileHandle.read()
                dictIdentities["control/control"] = hashlib.sha256(
                    fbaNormaliseControlStanza(baStanza)
                ).hexdigest()
                continue
            dictIdentities[f"control/{sPath}"] = fsMemberIdentity(
                tarControl, infoMember
            )
    return dictIdentities


def ftComparePackagePayloads(baOld, baNew):
    """Return (verdict, sorted list of differing payload paths)."""
    dictOld = fdictHashPayloadMembers(baOld)
    dictNew = fdictHashPayloadMembers(baNew)
    # A package whose INSTALLED files read as empty has not been
    # compared, whatever the dictionaries agree about. The test is on
    # the data members specifically: control members are always
    # present, so a whole-map emptiness check would stop firing.
    for dictSide in (dictOld, dictNew):
        if not any(sPath.startswith("data/") for sPath in dictSide):
            return S_VERDICT_UNCOMPARABLE, ["payload read as empty"]
    listDiffering = sorted(
        sPath
        for sPath in set(dictOld) | set(dictNew)
        if dictOld.get(sPath) != dictNew.get(sPath)
    )
    if listDiffering:
        return S_VERDICT_CHANGED, listDiffering
    return S_VERDICT_IDENTICAL, []


def flistRunInBaseImage(sBaseImage, sScript):
    """Return the stdout lines of a shell script run in the base image."""
    listCommand = [
        "docker", "run", "--rm", "--platform", "linux/amd64",
        sBaseImage, "bash", "-c", sScript,
    ]
    processResult = subprocess.run(
        listCommand, capture_output=True, text=True, check=False
    )
    if processResult.returncode != 0:
        raise RuntimeError(
            "could not query the archive through the base image "
            f"(exit {processResult.returncode}): "
            f"{processResult.stderr.strip()[:400]}"
        )
    return processResult.stdout.splitlines()


def fdictResolveCurrentClosure(sBaseImage):
    """Return {package: version} apt would install today.

    Asks apt itself rather than reimplementing candidate selection, and
    reads the version in PARENTHESES -- the bracketed field on an
    upgrade line names the version being REPLACED.
    """
    listLines = flistRunInBaseImage(
        sBaseImage,
        "apt-get update -qq >/dev/null && "
        "apt-get install -s -y --no-install-recommends gcc g++ make",
    )
    dictResolved = dict(REGEX_SIMULATED_INSTALL.findall("\n".join(listLines)))
    if not dictResolved:
        raise RuntimeError("apt resolved no packages; the query is broken")
    return dictResolved


def fdictResolvePoolPaths(sBaseImage, listPackages, dictNewVersions):
    """Return {package: archive pool path} for the replacement packages."""
    sQueries = " ".join(
        f"{sPackage}={dictNewVersions[sPackage]}" for sPackage in listPackages
    )
    listLines = flistRunInBaseImage(
        sBaseImage,
        "apt-get update -qq >/dev/null && "
        f"for P in {sQueries}; do apt-cache show \"$P\" | grep '^Filename:'; done",
    )
    listPaths = REGEX_POOL_FILENAME.findall("\n".join(listLines))
    dictPaths = {}
    for sPath in listPaths:
        sBasename = sPath.rsplit("/", 1)[-1]
        dictPaths[sBasename.split("_", 1)[0]] = sPath
    return dictPaths


def fbaFetchUrl(sUrl):
    """Return the bytes at a URL, or raise."""
    with urllib.request.urlopen(sUrl, timeout=300) as responseHandle:
        return responseHandle.read()


def fsSupersededPoolBasename(sPoolPath, sPackage, sOldVersion):
    """Return the pool FILENAME the superseded version is stored under.

    A pool filename carries no EPOCH: gcc at version 4:13.2.0-7ubuntu1
    is stored as gcc_13.2.0-7ubuntu1_amd64.deb. Percent-encoding the
    colon instead produced a URL that 404s, so the seven epoch-bearing
    pins -- which include gcc, g++ and cpp themselves -- could only ever
    be escalated, never compared. Confirmed against the live archive.
    """
    sArchitecture = sPoolPath.rsplit("/", 1)[-1].rsplit("_", 1)[-1]
    return f"{sPackage}_{sOldVersion.split(':', 1)[-1]}_{sArchitecture}"


def fbaFetchSupersededPackage(sPoolPath, sPackage, sOldVersion):
    """Return the superseded .deb's bytes from a dated snapshot mirror.

    The pool DIRECTORY is stable across versions, so the replacement's
    path names the directory and only the basename's version differs.
    """
    import datetime

    sDirectory = sPoolPath.rsplit("/", 1)[0]
    sOldBasename = fsSupersededPoolBasename(sPoolPath, sPackage, sOldVersion)
    dateToday = datetime.datetime.now(datetime.timezone.utc)
    for iDaysBack in T_SNAPSHOT_PROBE_DAYS:
        sStamp = (
            dateToday - datetime.timedelta(days=iDaysBack)
        ).strftime("%Y%m%dT000000Z")
        sUrl = f"{S_SNAPSHOT_BASE}{sStamp}/{sDirectory}/{sOldBasename}"
        try:
            return fbaFetchUrl(sUrl)
        except (urllib.error.URLError, urllib.error.HTTPError, OSError):
            continue
    raise RuntimeError(
        f"no dated snapshot within {max(T_SNAPSHOT_PROBE_DAYS)} days still "
        f"carries {sPackage} {sOldVersion}"
    )


def flistDetectDrift(dictPinned, dictResolved):
    """Return [(package, pinned version, resolved version)] for drifted pins."""
    return [
        (sPackage, sPinned, dictResolved[sPackage])
        for sPackage, sPinned in sorted(dictPinned.items())
        if sPackage in dictResolved and dictResolved[sPackage] != sPinned
    ]


def flistDetectClosureMembershipChanges(dictPinned, dictResolved):
    """Return human-readable reasons the pin SET no longer matches the closure.

    Drift detection compares versions of packages present on both
    sides, so it is blind in both directions to the set itself
    changing. Both directions are real and neither is a version bump:

      * a package that ENTERED the closure is installed by the
        Dockerfile's apt line as an unpinned dependency, so the very
        thing the block exists to prevent happens to it silently; and
      * a package that LEFT is a pin the comparison would skip, and a
        stale pin can make apt unsatisfiable rather than merely
        redundant.

    Neither is auto-acceptable, because the fix is to regenerate the
    pin list -- a maintainer's decision, not a version substitution.
    """
    listReasons = []
    for sPackage in sorted(set(dictResolved) - set(dictPinned)):
        listReasons.append(
            f"{sPackage} entered the toolchain closure and is NOT pinned "
            f"(apt would install {dictResolved[sPackage]} unpinned)"
        )
    for sPackage in sorted(set(dictPinned) - set(dictResolved)):
        listReasons.append(
            f"{sPackage} is pinned but is no longer part of the closure"
        )
    return listReasons


def ftJudgeOnePin(sBaseImage, sPackage, sOldVersion, sNewVersion, dictPoolPaths):
    """Return (verdict, detail lines) for one drifted pin."""
    sPoolPath = dictPoolPaths.get(sPackage)
    if sPoolPath is None:
        return S_VERDICT_UNCOMPARABLE, [
            f"the archive names no pool path for {sPackage}={sNewVersion}"
        ]
    try:
        baNew = fbaFetchUrl(f"{S_ARCHIVE_BASE}{sPoolPath}")
        baOld = fbaFetchSupersededPackage(sPoolPath, sPackage, sOldVersion)
    except (RuntimeError, urllib.error.URLError, OSError) as errorFetch:
        return S_VERDICT_UNCOMPARABLE, [str(errorFetch)]
    try:
        return ftComparePackagePayloads(baOld, baNew)
    except (RuntimeError, ValueError, tarfile.TarError) as errorCompare:
        return S_VERDICT_UNCOMPARABLE, [str(errorCompare)]


def fsApplyPinBumps(sDockerfileText, listBumps):
    """Return the Dockerfile text with the named pins bumped.

    Rewrites only inside the toolchain block, and only a line that
    still carries the exact pinned version, so a package named
    elsewhere in the file cannot be rewritten by accident.
    """
    sBlock = fsExtractToolchainBlock(sDockerfileText)
    sUpdatedBlock = sBlock
    for sPackage, sOldVersion, sNewVersion in listBumps:
        sOldPin = f"{sPackage}={sOldVersion}"
        sNewPin = f"{sPackage}={sNewVersion}"
        if sUpdatedBlock.count(sOldPin) != 1:
            raise ValueError(
                f"expected exactly one {sOldPin!r} in the toolchain block; "
                f"found {sUpdatedBlock.count(sOldPin)}"
            )
        sUpdatedBlock = sUpdatedBlock.replace(sOldPin, sNewPin)
    return sDockerfileText.replace(sBlock, sUpdatedBlock, 1)


def fnReportOnePin(sPackage, sOldVersion, sNewVersion, sVerdict, listDetail):
    """Print one pin's verdict and its supporting detail."""
    print(f"  {sPackage}: {sOldVersion} -> {sNewVersion}  [{sVerdict}]")
    for sLine in listDetail[:20]:
        print(f"      {sLine}")
    if len(listDetail) > 20:
        print(f"      ... and {len(listDetail) - 20} more")


def fnMain(listArgv=None):
    """Compare drifted toolchain pins and report which are inert."""
    parserArguments = argparse.ArgumentParser(description=__doc__)
    parserArguments.add_argument(
        "--write",
        action="store_true",
        help="apply the bumps whose payloads are provably identical",
    )
    namespaceArguments = parserArguments.parse_args(listArgv)

    sDockerfileText = fsReadDockerfile()
    sBaseImage = fsParseBaseImage(sDockerfileText)
    dictPinned = fdictParsePinnedVersions(sDockerfileText)
    try:
        dictResolved = fdictResolveCurrentClosure(sBaseImage)
    except RuntimeError as errorResolve:
        print(f"Could not resolve the current closure: {errorResolve}")
        return 1

    listMembership = flistDetectClosureMembershipChanges(dictPinned, dictResolved)
    if listMembership:
        print("The pin SET no longer matches the resolved closure:")
        for sReason in listMembership:
            print(f"  {sReason}")
        print("\nRegenerate the pin list; this is not a version bump.")
        return 1

    listDrift = flistDetectDrift(dictPinned, dictResolved)
    if not listDrift:
        print(f"No pin drift: all {len(dictPinned)} pinned versions are current.")
        return 0

    print(f"Pin drift on {len(listDrift)} of {len(dictPinned)} pinned packages.")
    dictPoolPaths = fdictResolvePoolPaths(
        sBaseImage, [sPackage for sPackage, _, _ in listDrift], dictResolved
    )
    listInert, listEscalate = [], []
    for sPackage, sOldVersion, sNewVersion in listDrift:
        sVerdict, listDetail = ftJudgeOnePin(
            sBaseImage, sPackage, sOldVersion, sNewVersion, dictPoolPaths
        )
        fnReportOnePin(sPackage, sOldVersion, sNewVersion, sVerdict, listDetail)
        if sVerdict == S_VERDICT_IDENTICAL:
            listInert.append((sPackage, sOldVersion, sNewVersion))
        else:
            listEscalate.append(sPackage)

    if listEscalate:
        print(
            "\nA maintainer must decide: "
            + ", ".join(listEscalate)
            + "\nRe-run and re-verify the project's results if you accept these."
        )
        return 1

    print(f"\nAll {len(listInert)} rotations changed no compile input.")
    if namespaceArguments.write:
        PATH_DOCKERFILE.write_text(
            fsApplyPinBumps(sDockerfileText, listInert), encoding="utf-8"
        )
        print("Applied the bumps to the Dockerfile.")
    return 0


if __name__ == "__main__":
    sys.exit(fnMain())
