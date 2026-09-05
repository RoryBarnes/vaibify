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
    dictPinned = dict(REGEX_PIN.findall(fsExtractToolchainBlock(sDockerfileText)))
    if not dictPinned:
        raise ValueError("parsed zero pins; the block's shape has changed")
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


def fdictHashPayloadMembers(baDeb):
    """Return {payload path: sha256} for every non-documentation file.

    Directories and symlink targets are included by identity so a
    changed link or a new file cannot hide behind a file-content-only
    comparison.
    """
    baPayload = None
    for sName, baMember in flistReadArchiveMembers(baDeb):
        if sName.startswith("data.tar"):
            baPayload = fbaDecompressPayload(baMember, sName)
            break
    if baPayload is None:
        raise ValueError("the package contains no data.tar member")
    dictHashes = {}
    with tarfile.open(fileobj=io.BytesIO(baPayload)) as tarPayload:
        for infoMember in tarPayload.getmembers():
            sPath = infoMember.name.lstrip("./")
            if not sPath or fbIsDocumentationMember(sPath):
                continue
            if infoMember.issym() or infoMember.islnk():
                dictHashes[sPath] = f"link:{infoMember.linkname}"
                continue
            if not infoMember.isfile():
                dictHashes[sPath] = f"type:{infoMember.type.decode('ascii')}"
                continue
            fileHandle = tarPayload.extractfile(infoMember)
            baContent = b"" if fileHandle is None else fileHandle.read()
            dictHashes[sPath] = hashlib.sha256(baContent).hexdigest()
    return dictHashes


def ftComparePackagePayloads(baOld, baNew):
    """Return (verdict, sorted list of differing payload paths)."""
    dictOld = fdictHashPayloadMembers(baOld)
    dictNew = fdictHashPayloadMembers(baNew)
    if not dictOld or not dictNew:
        # A package whose payload reads as empty has not been compared,
        # whatever the dictionaries agree about.
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


def fbaFetchSupersededPackage(sPoolPath, sPackage, sOldVersion):
    """Return the superseded .deb's bytes from a dated snapshot mirror.

    The pool DIRECTORY is stable across versions, so the replacement's
    path names the directory and only the basename's version differs.
    """
    import datetime

    sDirectory, sBasename = sPoolPath.rsplit("/", 1)
    sArchitecture = sBasename.rsplit("_", 1)[-1]
    sOldBasename = f"{sPackage}_{sOldVersion.replace(':', '%3a')}_{sArchitecture}"
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
