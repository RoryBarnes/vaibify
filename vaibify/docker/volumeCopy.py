"""Copy one named volume onto a new, empty one, and prove the copy exact.

Renaming an environment moves its two named volumes, and Docker cannot
rename a volume: the only move is a copy. A workspace volume holds
files owned by several users with their own modes, timestamps,
symlinks, hard links and extended attributes, so the copy runs in the
volume copier (``disposableSpecification``, ruling D13) -- the one
disposable that runs as root -- and is then VERIFIED: a content
manifest of every entry is computed over both volumes, inside the
copier, and the two must be identical before anything trusts the copy.

Both programs are fixed text in this module, run with fixed arguments
through the gateway's bounded exec; no caller text reaches either. Only
digests and counts leave the container, never a path or a byte of
content, because one of the two volumes holds credentials.

What the manifest records, per entry: the relative path, the file type,
the numeric owner and group, the permission bits, the size and the
modification time, the SHA-256 of a regular file's contents, a
symlink's target, a digest of its extended attributes, and which
hard-link group it belongs to (named by the group's first path, so two
trees agree only when their groups do).
"""

import json

from vaibify.docker import disposableContainer, disposableSpecification


__all__ = [
    "VolumeCopyError",
    "fdictCopyVolumeAndVerify",
    "S_MANIFEST_PROGRAM",
    "S_COPY_PROGRAM",
]


class VolumeCopyError(RuntimeError):
    """Raised when a volume copy cannot run, or does not verify."""


# Generous: a copy and two full manifests are proportional to the
# volume's size, and the bound exists to catch a hang, not to time one.
F_COPY_WALL_CLOCK_SECONDS = 12 * 3600.0
F_PROBE_WALL_CLOCK_SECONDS = 60.0
I_REPORT_BYTE_CAP = 65536

LIST_PYTHON_PROBE_COMMAND = ["python3", "-I", "-B", "-c", "import os"]

# Shared by both programs: the walk, the stat reading and the refusal of
# entry types a copy cannot recreate without a capability it lacks.
_S_WALK_SOURCE = r'''
import hashlib, json, os, stat, sys

S_CAPABILITY_ATTRIBUTE = "security.capability"


def flistWalk(sRoot):
    listPaths = ["."]
    iIndex = 0
    while iIndex < len(listPaths):
        sRelative = listPaths[iIndex]
        iIndex += 1
        sAbsolute = os.path.normpath(os.path.join(sRoot, sRelative))
        if stat.S_ISDIR(os.lstat(sAbsolute).st_mode):
            for sName in sorted(os.listdir(sAbsolute)):
                listPaths.append(os.path.normpath(
                    os.path.join(sRelative, sName)))
    return listPaths


def flistAttributes(sAbsolute):
    if not hasattr(os, "listxattr"):
        return []
    try:
        listNames = os.listxattr(sAbsolute, follow_symlinks=False)
    except OSError:
        return []
    listPairs = []
    for sName in sorted(listNames):
        baValue = os.getxattr(sAbsolute, sName, follow_symlinks=False)
        listPairs.append((sName, baValue))
    return listPairs


def fsTypeOf(iMode):
    for fbTest, sType in ((stat.S_ISDIR, "directory"),
                          (stat.S_ISREG, "file"), (stat.S_ISLNK, "symlink"),
                          (stat.S_ISFIFO, "fifo")):
        if fbTest(iMode):
            return sType
    return "unsupported"
'''

S_MANIFEST_PROGRAM = _S_WALK_SOURCE + r'''

def fsHashFile(sAbsolute):
    hashContents = hashlib.sha256()
    with open(sAbsolute, "rb") as fileHandle:
        for baChunk in iter(lambda: fileHandle.read(1048576), b""):
            hashContents.update(baChunk)
    return hashContents.hexdigest()


def fdictEntry(sRoot, sRelative, dictLinkGroups):
    sAbsolute = os.path.normpath(os.path.join(sRoot, sRelative))
    statEntry = os.lstat(sAbsolute)
    sType = fsTypeOf(statEntry.st_mode)
    hashAttributes = hashlib.sha256()
    for sName, baValue in flistAttributes(sAbsolute):
        hashAttributes.update(sName.encode() + b"\0" + baValue + b"\0")
    dictEntry = {
        "sPath": sRelative, "sType": sType, "iUid": statEntry.st_uid,
        "iGid": statEntry.st_gid,
        "iMode": stat.S_IMODE(statEntry.st_mode),
        "iSize": statEntry.st_size if sType != "directory" else 0,
        "iMtimeNs": statEntry.st_mtime_ns,
        "sAttributes": hashAttributes.hexdigest(),
    }
    if sType == "file":
        dictEntry["sSha256"] = fsHashFile(sAbsolute)
        if statEntry.st_nlink > 1:
            tInode = (statEntry.st_dev, statEntry.st_ino)
            dictEntry["sLinkGroup"] = dictLinkGroups.setdefault(
                tInode, sRelative)
    if sType == "symlink":
        dictEntry["sTarget"] = os.readlink(sAbsolute)
    return dictEntry


def main():
    sRoot = sys.argv[1]
    dictLinkGroups = {}
    hashManifest = hashlib.sha256()
    iEntries, iUnsupported, bCapability = 0, 0, False
    for sRelative in flistWalk(sRoot):
        dictEntry = fdictEntry(sRoot, sRelative, dictLinkGroups)
        iEntries += 1
        iUnsupported += dictEntry["sType"] == "unsupported"
        bCapability = bCapability or any(
            sName == S_CAPABILITY_ATTRIBUTE
            for sName, _ in flistAttributes(
                os.path.normpath(os.path.join(sRoot, sRelative))))
        hashManifest.update(
            json.dumps(dictEntry, sort_keys=True).encode() + b"\n")
    print(json.dumps({
        "sDigest": hashManifest.hexdigest(), "iEntries": iEntries,
        "iUnsupported": iUnsupported, "bCapabilityAttribute": bCapability,
    }))


main()
'''

S_COPY_PROGRAM = _S_WALK_SOURCE + r'''

def fnApplyMetadata(sSourcePath, sDestinationPath, statEntry, bSymlink):
    os.chown(sDestinationPath, statEntry.st_uid, statEntry.st_gid,
             follow_symlinks=False)
    if not bSymlink:
        os.chmod(sDestinationPath, stat.S_IMODE(statEntry.st_mode))
    for sName, baValue in flistAttributes(sSourcePath):
        os.setxattr(sDestinationPath, sName, baValue, follow_symlinks=False)
    os.utime(sDestinationPath, ns=(statEntry.st_atime_ns,
             statEntry.st_mtime_ns), follow_symlinks=False)


def fnCopyFile(sSourcePath, sDestinationPath):
    iFlags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    with open(sSourcePath, "rb") as fileSource, os.fdopen(
            os.open(sDestinationPath, iFlags, 0o600), "wb") as fileTarget:
        for baChunk in iter(lambda: fileSource.read(1048576), b""):
            fileTarget.write(baChunk)


def fnCopyEntry(sSourcePath, sDestinationPath, statEntry, dictLinked):
    sType = fsTypeOf(statEntry.st_mode)
    if sType == "directory":
        os.makedirs(sDestinationPath, exist_ok=True)
        return
    tInode = (statEntry.st_dev, statEntry.st_ino)
    if sType == "file" and statEntry.st_nlink > 1 and tInode in dictLinked:
        os.link(dictLinked[tInode], sDestinationPath)
        return
    if sType == "file":
        fnCopyFile(sSourcePath, sDestinationPath)
        dictLinked[tInode] = sDestinationPath
    elif sType == "symlink":
        os.symlink(os.readlink(sSourcePath), sDestinationPath)
    elif sType == "fifo":
        os.mkfifo(sDestinationPath)
    fnApplyMetadata(sSourcePath, sDestinationPath, statEntry,
                    sType == "symlink")


def main():
    sSource, sDestination = sys.argv[1], sys.argv[2]
    if os.listdir(sDestination):
        print(json.dumps({"sRefusal": "the destination volume is not empty"}))
        return
    listPaths = flistWalk(sSource)
    dictLinked = {}
    listDirectories = []
    for sRelative in listPaths:
        sSourcePath = os.path.normpath(os.path.join(sSource, sRelative))
        sDestinationPath = os.path.normpath(
            os.path.join(sDestination, sRelative))
        statEntry = os.lstat(sSourcePath)
        if fsTypeOf(statEntry.st_mode) == "unsupported":
            print(json.dumps({"sRefusal": "the volume holds a device or "
                              "socket, which a copy cannot recreate"}))
            return
        fnCopyEntry(sSourcePath, sDestinationPath, statEntry, dictLinked)
        if stat.S_ISDIR(statEntry.st_mode):
            listDirectories.append((sSourcePath, sDestinationPath, statEntry))
    for sSourcePath, sDestinationPath, statEntry in reversed(listDirectories):
        fnApplyMetadata(sSourcePath, sDestinationPath, statEntry, False)
    print(json.dumps({"iEntries": len(listPaths)}))


main()
'''


def fdictCopyVolumeAndVerify(
    dictGateway, sImageId, sSourceVolume, sDestinationVolume,
    sVolumeDescription,
):
    """Copy one volume onto an empty one in the copier; prove it exact.

    ``sVolumeDescription`` ("workspace", "credentials") is what a
    refusal names; nothing read from either volume ever is. Returns
    ``{"sDigest", "iEntries"}`` for the verified copy. The copier is
    destroyed with proof whatever happens.
    """
    dictCreated = disposableContainer.fdictReserveAndCreateVolumeCopier(
        dictGateway, sImageId, sSourceVolume, sDestinationVolume)
    sHandle = dictCreated["sHandle"]
    try:
        _fnRequirePython(dictGateway, sHandle)
        dictSource = _fdictReadManifest(
            dictGateway, sHandle,
            disposableSpecification.S_VOLUME_COPIER_SOURCE_ROOT,
            sVolumeDescription)
        _fnRefuseWhatCannotBeCopied(dictSource, sVolumeDescription)
        _fnRunCopy(dictGateway, sHandle, sVolumeDescription)
        dictDestination = _fdictReadManifest(
            dictGateway, sHandle,
            disposableSpecification.S_VOLUME_COPIER_DESTINATION_ROOT,
            sVolumeDescription)
    finally:
        disposableContainer.fdictDestroyAndSettle(dictGateway, sHandle)
    if dictSource["sDigest"] != dictDestination["sDigest"] or (
            dictSource["iEntries"] != dictDestination["iEntries"]):
        raise VolumeCopyError(
            f"the {sVolumeDescription} volume copy did not verify")
    return {"sDigest": dictSource["sDigest"],
            "iEntries": dictSource["iEntries"]}


def _fdictRunProgram(dictGateway, sHandle, listCommand, fWallClockSeconds):
    return disposableContainer.fdictExecuteBoundedCommand(
        dictGateway, sHandle, listCommand,
        iOutputByteCap=I_REPORT_BYTE_CAP,
        fWallClockSeconds=fWallClockSeconds,
    )


def _fnRequirePython(dictGateway, sHandle):
    dictOutcome = _fdictRunProgram(
        dictGateway, sHandle, LIST_PYTHON_PROBE_COMMAND,
        F_PROBE_WALL_CLOCK_SECONDS)
    if dictOutcome["iExitCode"] != 0:
        raise VolumeCopyError(
            "this environment's image has no python3, which renaming it "
            "needs to copy its volumes faithfully; it cannot be renamed "
            "until the image provides one."
        )


def _fdictParseReport(dictOutcome, sVolumeDescription, sStep):
    """Read a program's one-line JSON report, naming only the step."""
    if dictOutcome["iExitCode"] != 0:
        raise VolumeCopyError(
            f"the {sVolumeDescription} volume {sStep} failed "
            f"(exit {dictOutcome['iExitCode']})")
    try:
        return json.loads(dictOutcome["sOutput"].strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise VolumeCopyError(
            f"the {sVolumeDescription} volume {sStep} gave no report"
        ) from None


def _fdictReadManifest(dictGateway, sHandle, sRoot, sVolumeDescription):
    dictOutcome = _fdictRunProgram(
        dictGateway, sHandle,
        ["python3", "-I", "-B", "-c", S_MANIFEST_PROGRAM, sRoot],
        F_COPY_WALL_CLOCK_SECONDS)
    return _fdictParseReport(dictOutcome, sVolumeDescription, "manifest")


def _fnRefuseWhatCannotBeCopied(dictManifest, sVolumeDescription):
    """Refuse a capability attribute or an entry no copy can recreate.

    Setting ``security.capability`` needs SETFCAP, which the copier is
    deliberately not granted; a copy that silently dropped it would
    verify as different, so it is refused before anything is written.
    """
    if dictManifest["bCapabilityAttribute"]:
        raise VolumeCopyError(
            f"the {sVolumeDescription} volume holds a file with a "
            "security.capability attribute, which the volume copier is "
            "not permitted to set; the environment cannot be renamed."
        )
    if dictManifest["iUnsupported"]:
        raise VolumeCopyError(
            f"the {sVolumeDescription} volume holds a device or socket, "
            "which a copy cannot recreate; the environment cannot be "
            "renamed."
        )


def _fnRunCopy(dictGateway, sHandle, sVolumeDescription):
    dictOutcome = _fdictRunProgram(
        dictGateway, sHandle,
        ["python3", "-I", "-B", "-c", S_COPY_PROGRAM,
         disposableSpecification.S_VOLUME_COPIER_SOURCE_ROOT,
         disposableSpecification.S_VOLUME_COPIER_DESTINATION_ROOT],
        F_COPY_WALL_CLOCK_SECONDS)
    dictReport = _fdictParseReport(dictOutcome, sVolumeDescription, "copy")
    if dictReport.get("sRefusal"):
        raise VolumeCopyError(
            f"the {sVolumeDescription} volume could not be copied: "
            f"{dictReport['sRefusal']}")
