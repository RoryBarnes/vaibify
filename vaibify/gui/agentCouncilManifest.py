"""Apply a participant's change manifest to a fresh evidence sandbox.

A modified-state claim says "I changed these files, then this command
gave this result" (design section 7.4). Vaibify does not take the
participant's word for the result: it seeds a fresh sandbox from the
sealed snapshot, applies the manifest there, and runs the command itself
(``agentCouncilProviders.ffnBuildBaselineEvidenceExecutor``). This
module owns the application step and the ledger's record of it.

**The manifest is data, never code.** It reaches the sandbox on the
standard input of a FIXED program (:data:`S_MANIFEST_APPLY_PROGRAM`),
run as the unprivileged council user with ``python3 -c``. No model text
is placed in an argument or a shell string; the only argument is the
server-owned working-tree root.

**Confinement is filesystem-aware, not lexical.** The lexical member
check the snapshot copy uses (``pathContainment.fsDescribeMemberEscape``)
accepts ``a -> .`` together with ``b -> a/../outside`` although ``b``
resolves outside the root on disk, so it is not used here. The program
holds a descriptor on the root and opens each directory one component
at a time with ``O_DIRECTORY | O_NOFOLLOW`` relative to the last, so no
symlink is followed on the way, including one already in the snapshot.
The final component is created, written, deleted or re-moded relative
to its parent's descriptor and is never followed either. Changes apply
in a fixed order (deletions, writes, modes, then symlinks), so a
manifest cannot create a symlink and then write through it, and a
symlink's target is stored exactly as given and never resolved.

The sandbox's system files are read-only and it has no network, so a
confinement defect could only misplace a file inside a throwaway
container. Confinement exists so the replay runs against the state the
claim describes and nothing else.
"""

__all__ = [
    "I_MANIFEST_REFUSED_EXIT_CODE",
    "S_MANIFEST_APPLY_PROGRAM",
    "fbaEncodeManifestForProgram",
    "fdictTranslateManifestForLedger",
    "flistComposeManifestApplyCommand",
]

import json

I_MANIFEST_REFUSED_EXIT_CODE = 3

S_MANIFEST_APPLY_PROGRAM = '''import errno, json, os, secrets, stat, sys
I_REFUSED = 3
I_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
def fnRefuse(sReason):
    sys.stderr.write("refused: " + sReason + "\\n")
    sys.exit(I_REFUSED)
def flistComponents(sPath):
    if not isinstance(sPath, str) or not sPath or "\\0" in sPath:
        fnRefuse("a path must be non-empty text")
    if sPath.startswith("/"):
        fnRefuse("absolute path " + repr(sPath))
    listParts = sPath.split("/")
    if any(sPart in ("", ".", "..") for sPart in listParts):
        fnRefuse("path " + repr(sPath) + " has an empty, . or .. component")
    return listParts
def fiOpenDirectory(iParent, sName):
    try:
        return os.open(sName, I_DIRECTORY_FLAGS, dir_fd=iParent)
    except OSError as error:
        if error.errno in (errno.ELOOP, errno.ENOTDIR):
            fnRefuse(repr(sName) + " is a symlink or not a directory")
        raise
def fiOpenParent(listParts, bCreate):
    iDirectory = os.dup(iRoot)
    for sName in listParts[:-1]:
        try:
            iNext = fiOpenDirectory(iDirectory, sName)
        except FileNotFoundError:
            if not bCreate:
                fnRefuse("no directory " + repr(sName))
            os.mkdir(sName, 0o755, dir_fd=iDirectory)
            iNext = fiOpenDirectory(iDirectory, sName)
        os.close(iDirectory)
        iDirectory = iNext
    return iDirectory
def fiModeWithoutFollowing(iParent, sName):
    try:
        return os.stat(sName, dir_fd=iParent, follow_symlinks=False).st_mode
    except FileNotFoundError:
        return None
def fnDelete(sPath):
    listParts = flistComponents(sPath)
    iParent = fiOpenParent(listParts, False)
    iMode = fiModeWithoutFollowing(iParent, listParts[-1])
    if iMode is None:
        fnRefuse("nothing to delete at " + repr(sPath))
    if stat.S_ISDIR(iMode):
        fnRefuse(repr(sPath) + " is a directory")
    os.unlink(listParts[-1], dir_fd=iParent)
    os.close(iParent)
def fnWrite(sPath, sContent):
    listParts = flistComponents(sPath)
    iParent = fiOpenParent(listParts, True)
    iMode = fiModeWithoutFollowing(iParent, listParts[-1])
    if iMode is not None and not stat.S_ISREG(iMode):
        fnRefuse(repr(sPath) + " is a symlink or not a regular file")
    sTemporary = ".vaibify-replay-" + secrets.token_hex(8)
    iFile = os.open(sTemporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                    | os.O_NOFOLLOW, 0o600, dir_fd=iParent)
    with os.fdopen(iFile, "wb") as fileTemporary:
        fileTemporary.write(sContent.encode("utf-8"))
        os.fchmod(fileTemporary.fileno(), stat.S_IMODE(iMode or 0o644))
    os.rename(sTemporary, listParts[-1], src_dir_fd=iParent,
              dst_dir_fd=iParent)
    os.close(iParent)
def fnChangeMode(sPath, sMode):
    listParts = flistComponents(sPath)
    if not isinstance(sMode, str) or not sMode or set(sMode) - set("01234567"):
        fnRefuse("mode " + repr(sMode) + " is not octal")
    iParent = fiOpenParent(listParts, False)
    try:
        iFile = os.open(listParts[-1], os.O_RDONLY | os.O_NOFOLLOW
                        | os.O_NONBLOCK, dir_fd=iParent)
    except OSError as error:
        fnRefuse("cannot change the mode of " + repr(sPath) + ": "
                 + os.strerror(error.errno))
    if not stat.S_ISREG(os.fstat(iFile).st_mode):
        fnRefuse(repr(sPath) + " is not a regular file")
    os.fchmod(iFile, int(sMode, 8) & 0o7777)
    os.close(iFile)
    os.close(iParent)
def fnLink(sPath, sTarget):
    listParts = flistComponents(sPath)
    if not isinstance(sTarget, str) or not sTarget or "\\0" in sTarget:
        fnRefuse("a symlink target must be non-empty text")
    iParent = fiOpenParent(listParts, True)
    iMode = fiModeWithoutFollowing(iParent, listParts[-1])
    if iMode is not None and stat.S_ISDIR(iMode):
        fnRefuse(repr(sPath) + " is a directory")
    sTemporary = ".vaibify-replay-" + secrets.token_hex(8)
    os.symlink(sTarget, sTemporary, dir_fd=iParent)
    os.rename(sTemporary, listParts[-1], src_dir_fd=iParent,
              dst_dir_fd=iParent)
    os.close(iParent)
def fnRefuseDuplicates(listPaths, sKind):
    if len(set(listPaths)) != len(listPaths):
        fnRefuse("a path appears twice in " + sKind)
try:
    dictManifest = json.loads(sys.stdin.buffer.read().decode("utf-8"))
except ValueError:
    fnRefuse("the manifest is not JSON")
iRoot = os.open(sys.argv[1], I_DIRECTORY_FLAGS)
listDeleted = dictManifest["listDeletedPaths"]
def flistPairs(sKey, sValueKey):
    return [(dictEntry["sPath"], dictEntry[sValueKey])
            for dictEntry in dictManifest[sKey]]
listWrites = flistPairs("listModifiedFiles", "sContent")
listModes = flistPairs("listChangedFileModes", "sMode")
listLinks = flistPairs("listSymlinkTargets", "sTarget")
fnRefuseDuplicates(listDeleted, "listDeletedPaths")
for sKind, listPairs in (("listModifiedFiles", listWrites),
                         ("listChangedFileModes", listModes),
                         ("listSymlinkTargets", listLinks)):
    fnRefuseDuplicates([tPair[0] for tPair in listPairs], sKind)
for sPath in listDeleted:
    fnDelete(sPath)
for sPath, sContent in listWrites:
    fnWrite(sPath, sContent)
for sPath, sMode in listModes:
    fnChangeMode(sPath, sMode)
for sPath, sTarget in listLinks:
    fnLink(sPath, sTarget)
'''


def flistComposeManifestApplyCommand(sWorkingTreeRoot):
    """Return the fixed argv that applies a manifest below the root."""
    return ["python3", "-c", S_MANIFEST_APPLY_PROGRAM, sWorkingTreeRoot]


def fbaEncodeManifestForProgram(dictManifest):
    """Encode a validated participant manifest as the program's stdin."""
    return json.dumps(dictManifest, sort_keys=True).encode("utf-8")


def fdictTranslateManifestForLedger(dictManifest):
    """Return the manifest in the ledger's form (path-keyed maps).

    The participant writes arrays because a strict schema cannot carry
    a map keyed by path; the ledger keeps the shape its
    ``LIST_CHANGE_MANIFEST_KEYS`` name. Called only after the program
    applied the manifest, which refuses a path listed twice, so no
    entry is lost in the translation.
    """
    return {
        "dictModifiedFileContents": {
            dictEntry["sPath"]: dictEntry["sContent"]
            for dictEntry in dictManifest["listModifiedFiles"]},
        "listDeletedPaths": list(dictManifest["listDeletedPaths"]),
        "dictChangedFileModes": {
            dictEntry["sPath"]: dictEntry["sMode"]
            for dictEntry in dictManifest["listChangedFileModes"]},
        "dictSymlinkTargets": {
            dictEntry["sPath"]: dictEntry["sTarget"]
            for dictEntry in dictManifest["listSymlinkTargets"]},
    }
