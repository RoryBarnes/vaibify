"""Prove the coding agents' layers leave the published environment untouched.

The environment a researcher publishes is the agent-free image; the
coding agents are stacked above it, used while the code is written,
and never deposited. That is only honest if the agents CANNOT change
what the environment computes, and this module checks it the one way
that is deterministic: it reads the image's own layers. The same two
images always get the same verdict -- no network, no clock, no
container. The agents are not built deterministically (their
installers fetch today's release), so the check runs on each stacked
image, never once per agent.

A layer can reach a computation only by changing a file the
environment holds, or by adding one somewhere a program SEARCHES.
The five rules are those two routes, spelled out:

1. no layer overwrites or removes a file the environment holds
   (package-manager bookkeeping and logs excepted);
2. no added command shadows one the environment already has on its
   ``PATH``;
3. nothing is added where an interpreter or the loader searches: an
   existing Python package directory, the shared-library directories,
   shell and loader configuration, fonts, R and Julia libraries;
4. a shell startup file may only gain lines that prepend a directory
   to ``PATH``;
5. the image's own settings are unchanged, except ``PATH`` gaining
   directories in front.

The rules enumerate the search mechanisms vaibify knows, so a pass is
strong evidence rather than a proof of equivalence. The proof is the
Level 3 rerun, which regenerates the results in the agent-free image
itself; this check is what says, before that, exactly which file an
agent would have touched.
"""

import gzip
import hashlib
import json
import posixpath
import re
import subprocess
import tarfile

from vaibify.reproducibility.environmentSnapshot import (
    _fnEnsureDockerAvailable,
    _fsRunCheckedCommand,
)


__all__ = [
    "AgentLayerSeparationError",
    "fdictCheckAgentLayerSeparation",
    "fdictVerifyAgentsLeaveThePinAlone",
    "fsDescribeAgentsLeftOut",
    "flistFindSeparationViolations",
    "fdictReadLayerEntries",
    "fsDescribeViolations",
]


T_BOOKKEEPING_PREFIXES = (
    "/var/lib/dpkg/", "/var/lib/apt/", "/var/log/", "/var/cache/",
    "/tmp/", "/var/tmp/", "/run/",
)
T_SHELL_STARTUP_BASENAMES = (
    ".bashrc", ".profile", ".bash_profile", ".bash_login",
)
T_SHELL_STARTUP_PATHS = ("/etc/bash.bashrc", "/etc/profile")
T_SEARCHED_DIRECTORIES = (
    "/etc/profile.d", "/etc/ld.so.conf.d", "/etc/fonts",
    "/usr/share/fonts", "/usr/local/share/fonts",
    "/usr/lib/R/site-library", "/usr/local/lib/R/site-library",
    "/usr/lib/R/library",
)
T_SEARCHED_HOME_DIRECTORIES = (
    ".fonts", ".local/share/fonts", ".config/matplotlib", ".julia",
)
T_SEARCHED_FILES = ("/etc/ld.so.preload", "/etc/environment")
T_LIBRARY_DIRECTORIES = (
    "/lib", "/lib64", "/usr/lib", "/usr/lib64", "/usr/local/lib",
)
T_UNCHANGED_SETTINGS = ("Entrypoint", "Cmd", "User", "WorkingDir")
# Variables an agent-side overlay may introduce because they steer
# only that overlay's own package manager.
T_AGENT_ENVIRONMENT_VARIABLES = ("NPM_CONFIG_PREFIX",)

_REGEX_MULTIARCH_DIRECTORY = re.compile(r"^[a-z0-9_]+-linux-[a-z]+$")
_REGEX_SHARED_OBJECT = re.compile(r"\.so(\.[0-9.]+)?$")
_REGEX_PATH_PREPEND = re.compile(
    r'^\s*export\s+PATH=["\']?(?P<sDirectory>[^:"\']+):\$\{?PATH\}?["\']?\s*$'
)
_S_WHITEOUT_PREFIX = ".wh."
_S_OPAQUE_WHITEOUT = ".wh..wh..opq"
_I_READ_CHUNK_BYTES = 1 << 20


class AgentLayerSeparationError(Exception):
    """The layers could not be read, so nothing about them was established."""


# ------------------------------------------------------------------
# Reading layers
# ------------------------------------------------------------------


def fdictReadLayerEntries(fileLayer):
    """Return ``{sPath: dictEntry}`` for one uncompressed layer tar stream.

    ``dictEntry`` carries ``sKind`` (file, directory, link, whiteout,
    opaque), the mode and ownership, and -- for shell startup files
    only -- the bytes, which rule 4 compares line by line.
    """
    dictEntries = {}
    with tarfile.open(fileobj=fileLayer, mode="r|") as fileTar:
        for infoMember in fileTar:
            sPath = "/" + infoMember.name.lstrip("./").rstrip("/")
            dictEntries[sPath] = _fdictDescribeMember(
                fileTar, infoMember, sPath,
            )
    return dictEntries


def _fdictDescribeMember(fileTar, infoMember, sPath):
    """Return one layer member's entry."""
    sBasename = posixpath.basename(sPath)
    if sBasename == _S_OPAQUE_WHITEOUT:
        sKind = "opaque"
    elif sBasename.startswith(_S_WHITEOUT_PREFIX):
        sKind = "whiteout"
    elif infoMember.isdir():
        sKind = "directory"
    elif infoMember.issym() or infoMember.islnk():
        sKind = "link"
    else:
        sKind = "file"
    dictEntry = {
        "sKind": sKind, "iMode": infoMember.mode,
        "iUid": infoMember.uid, "iGid": infoMember.gid,
    }
    if sKind == "file" and _fbIsShellStartupFile(sPath):
        dictEntry["baContent"] = fileTar.extractfile(infoMember).read()
    return dictEntry


class _HashingReader:
    """Pass bytes through while hashing them: a layer's ID is that hash."""

    def __init__(self, fileSource):
        self.fileSource = fileSource
        self.hasher = hashlib.sha256()

    def read(self, iSize=-1):
        baData = self.fileSource.read(iSize)
        self.hasher.update(baData)
        return baData

    def fsFinishDigest(self):
        """Hash whatever the tar reader left unread; return the diff ID."""
        while self.read(_I_READ_CHUNK_BYTES):
            pass
        return "sha256:" + self.hasher.hexdigest()


def fdictReadSavedLayers(fileSaved, setWantedDiffIds):
    """Return ``{sDiffId: dictEntries}`` for the wanted layers of a save stream.

    ``docker save`` writes its layer blobs before the manifest that
    orders them, and the containerd store compresses them, so each
    blob is identified by the one name that cannot disagree with the
    image: the sha256 of its UNCOMPRESSED bytes, which is exactly the
    diff ID ``docker image inspect`` lists.
    """
    dictLayers = {}
    with tarfile.open(fileobj=fileSaved, mode="r|") as fileTar:
        for infoBlob in fileTar:
            if not infoBlob.isfile() or infoBlob.size == 0:
                continue
            fileBlob = fileTar.extractfile(infoBlob)
            _fnReadOneBlob(fileBlob, setWantedDiffIds, dictLayers)
    return dictLayers


def _fnReadOneBlob(fileBlob, setWantedDiffIds, dictLayers):
    """Read one blob as a layer, keeping it when it is a wanted one."""
    fileHashing = _HashingReader(_ffileDecompressed(fileBlob))
    try:
        dictEntries = fdictReadLayerEntries(fileHashing)
    except (tarfile.ReadError, EOFError, OSError):
        return
    sDiffId = fileHashing.fsFinishDigest()
    if sDiffId in setWantedDiffIds:
        dictLayers[sDiffId] = dictEntries


def _ffileDecompressed(fileBlob):
    """Return the blob's uncompressed stream, whatever the store wrote."""
    baMagic = fileBlob.peek(4)[:4]
    if baMagic[:2] == b"\x1f\x8b":
        return gzip.GzipFile(fileobj=fileBlob)
    if baMagic == b"\x28\xb5\x2f\xfd":
        return _ffileZstandardReader(fileBlob)
    return fileBlob


def _ffileZstandardReader(fileBlob):
    """Return a zstd reader, from the standard library when it has one."""
    try:
        from compression import zstd
        return zstd.ZstdFile(fileBlob)
    except ImportError:
        import zstandard
        return zstandard.ZstdDecompressor().stream_reader(fileBlob)


# ------------------------------------------------------------------
# The rules
# ------------------------------------------------------------------


def flistFindSeparationViolations(
    listBaseLayers, listAgentLayers, dictBaseConfig, dictAgentConfig,
):
    """Return every way the agent layers could reach the environment.

    Each layer is ``{sPath: dictEntry}``, bottom first; each config is
    the image's Docker ``Config``. Returns ``[{sRule, sPath, sDetail}]``,
    empty when the agents are provably separate by these rules.
    """
    dictBase = _fdictMergeLayers(listBaseLayers)
    listViolations, listPrependedDirectories = _ftCompareSettings(
        dictBaseConfig, dictAgentConfig,
    )
    dictAgentFinal = {}
    for dictLayer in listAgentLayers:
        for sPath, dictEntry in dictLayer.items():
            listViolations += _flistJudgeEntry(sPath, dictEntry, dictBase)
            dictAgentFinal[sPath] = dictEntry
    listStartup, listRcDirectories = _ftJudgeStartupFiles(
        dictBase, dictAgentFinal,
    )
    listViolations += listStartup
    listViolations += _flistFindShadowedCommands(
        dictBase, dictAgentFinal, dictBaseConfig,
        listPrependedDirectories + listRcDirectories,
    )
    return listViolations


def _fdictMergeLayers(listLayers):
    """Return the final filesystem the layers describe, bottom first."""
    dictMerged = {}
    for dictLayer in listLayers:
        for sPath, dictEntry in dictLayer.items():
            sBasename = posixpath.basename(sPath)
            sParent = posixpath.dirname(sPath)
            if dictEntry["sKind"] == "opaque":
                _fnRemoveBelow(dictMerged, sParent)
            elif dictEntry["sKind"] == "whiteout":
                sTarget = posixpath.join(
                    sParent, sBasename[len(_S_WHITEOUT_PREFIX):],
                )
                dictRemoved = dictMerged.pop(sTarget, None)
                if (dictRemoved or {}).get("sKind") == "directory":
                    _fnRemoveBelow(dictMerged, sTarget)
            else:
                dictMerged[sPath] = dictEntry
    return dictMerged


def _fnRemoveBelow(dictMerged, sDirectory):
    """Drop every path strictly below one directory."""
    sPrefix = sDirectory.rstrip("/") + "/"
    for sPath in [s for s in dictMerged if s.startswith(sPrefix)]:
        del dictMerged[sPath]


def _fdictViolation(sRule, sPath, sDetail):
    return {"sRule": sRule, "sPath": sPath, "sDetail": sDetail}


def _flistJudgeEntry(sPath, dictEntry, dictBase):
    """Rules 1 and 3 for one entry of one agent layer."""
    if _fbIsBookkeeping(sPath) or _fbIsShellStartupFile(sPath):
        return []
    sKind = dictEntry["sKind"]
    if sKind in ("whiteout", "opaque"):
        return [_fdictViolation(
            "removes", sPath, "removes a file or directory the environment holds",
        )]
    dictOld = dictBase.get(sPath)
    if dictOld is not None:
        return _flistJudgeExistingPath(sPath, dictEntry, dictOld)
    sSearched = _fsSearchedLocation(sPath, dictBase)
    if sSearched:
        return [_fdictViolation("searched", sPath, sSearched)]
    return []


def _flistJudgeExistingPath(sPath, dictEntry, dictOld):
    """Rule 1: an entry at a path the environment already holds."""
    if dictEntry["sKind"] == "directory" and dictOld["sKind"] == "directory":
        tNew = (dictEntry["iMode"], dictEntry["iUid"], dictEntry["iGid"])
        tOld = (dictOld["iMode"], dictOld["iUid"], dictOld["iGid"])
        if tNew == tOld:
            return []
        return [_fdictViolation(
            "overwrites", sPath, "changes a directory's permissions or owner",
        )]
    return [_fdictViolation(
        "overwrites", sPath, "replaces a file the environment holds",
    )]


def _fsSearchedLocation(sPath, dictBase):
    """Rule 3: say where a searching program would find this, or ``''``."""
    if sPath in T_SEARCHED_FILES:
        return "is read by the loader or every login shell"
    for sDirectory in T_SEARCHED_DIRECTORIES + _ftHomeSearchedDirectories(sPath):
        if sPath == sDirectory or sPath.startswith(sDirectory + "/"):
            return f"adds to {sDirectory}, which programs search"
    sPackages = _fsExistingPackageDirectory(sPath, dictBase)
    if sPackages:
        return f"adds to the Python package directory {sPackages}"
    sParent = posixpath.dirname(sPath)
    if _REGEX_SHARED_OBJECT.search(posixpath.basename(sPath)) and (
        _fbIsLibraryDirectory(sParent)
    ):
        return f"adds a shared library to {sParent}, which the loader searches"
    return ""


def _ftHomeSearchedDirectories(sPath):
    """Return the home-relative searched directories for this path's home."""
    sHome = _fsHomeOf(sPath)
    if not sHome:
        return ()
    return tuple(
        posixpath.join(sHome, sRelative)
        for sRelative in T_SEARCHED_HOME_DIRECTORIES
    )


def _fsHomeOf(sPath):
    """Return ``/root`` or ``/home/<user>`` when the path lies in one."""
    listParts = sPath.split("/")
    if len(listParts) > 2 and listParts[1] == "root":
        return "/root"
    if len(listParts) > 3 and listParts[1] == "home":
        return "/home/" + listParts[2]
    return ""


def _fsExistingPackageDirectory(sPath, dictBase):
    """Return the environment's own site/dist-packages holding sPath, or ''.

    A NEW package directory -- an agent's private virtual environment --
    is searched by nobody else, so only one the environment already
    had counts.
    """
    listParts = sPath.split("/")
    for iIndex, sPart in enumerate(listParts[:-1]):
        if sPart in ("site-packages", "dist-packages"):
            sDirectory = "/".join(listParts[:iIndex + 1])
            if dictBase.get(sDirectory, {}).get("sKind") == "directory":
                return sDirectory
    return ""


def _fbIsLibraryDirectory(sDirectory):
    """True for a directory the dynamic loader searches by default."""
    if sDirectory in T_LIBRARY_DIRECTORIES:
        return True
    sParent, sName = posixpath.split(sDirectory)
    return sParent in ("/lib", "/usr/lib") and bool(
        _REGEX_MULTIARCH_DIRECTORY.match(sName)
    )


def _fbIsBookkeeping(sPath):
    return any(
        sPath.startswith(sPrefix) or sPath + "/" == sPrefix
        for sPrefix in T_BOOKKEEPING_PREFIXES
    )


def _fbIsShellStartupFile(sPath):
    if sPath in T_SHELL_STARTUP_PATHS:
        return True
    return bool(_fsHomeOf(sPath)) and (
        posixpath.dirname(sPath) == _fsHomeOf(sPath)
        and posixpath.basename(sPath) in T_SHELL_STARTUP_BASENAMES
    )


def _ftJudgeStartupFiles(dictBase, dictAgentFinal):
    """Rule 4; return ``(listViolations, listPrependedDirectories)``."""
    listViolations = []
    listDirectories = []
    for sPath, dictEntry in dictAgentFinal.items():
        if not _fbIsShellStartupFile(sPath) or dictEntry["sKind"] != "file":
            continue
        baOld = dictBase.get(sPath, {}).get("baContent", b"")
        baNew = dictEntry.get("baContent", b"")
        if not baNew.startswith(baOld):
            listViolations.append(_fdictViolation(
                "startup", sPath, "rewrites a shell startup file",
            ))
            continue
        for sLine in baNew[len(baOld):].decode("utf-8", "replace").splitlines():
            sDirectory = _fsJudgeAppendedLine(sLine, _fsHomeOf(sPath))
            if sDirectory is None:
                listViolations.append(_fdictViolation(
                    "startup", sPath, f"adds the line {sLine.strip()!r}",
                ))
            elif sDirectory:
                listDirectories.append(sDirectory)
    return listViolations, listDirectories


def _fsJudgeAppendedLine(sLine, sHome):
    """Return the directory a line prepends, ``''`` if inert, None if neither."""
    sStripped = sLine.strip()
    if not sStripped or sStripped.startswith("#"):
        return ""
    matchPrepend = _REGEX_PATH_PREPEND.match(sLine)
    if matchPrepend is None:
        return None
    sDirectory = matchPrepend.group("sDirectory")
    for sHomeWord in ("$HOME", "${HOME}", "~"):
        if sDirectory.startswith(sHomeWord) and sHome:
            sDirectory = sHome + sDirectory[len(sHomeWord):]
    return sDirectory.rstrip("/")


def _ftCompareSettings(dictBaseConfig, dictAgentConfig):
    """Rule 5; return ``(listViolations, listPrependedDirectories)``."""
    listViolations = [
        _fdictViolation(
            "settings", sSetting, "changes the image's " + sSetting,
        )
        for sSetting in T_UNCHANGED_SETTINGS
        if (dictBaseConfig or {}).get(sSetting)
        != (dictAgentConfig or {}).get(sSetting)
    ]
    dictBaseEnvironment = _fdictParseEnvironment(dictBaseConfig)
    dictAgentEnvironment = _fdictParseEnvironment(dictAgentConfig)
    for sName in sorted(set(dictBaseEnvironment) | set(dictAgentEnvironment)):
        if sName == "PATH":
            continue
        if dictBaseEnvironment.get(sName) == dictAgentEnvironment.get(sName):
            continue
        if sName not in dictBaseEnvironment and (
            sName in T_AGENT_ENVIRONMENT_VARIABLES
        ):
            continue
        listViolations.append(_fdictViolation(
            "settings", sName, "changes the environment variable " + sName,
        ))
    listPrepended, bPathKept = _ftComparePath(
        dictBaseEnvironment.get("PATH", ""),
        dictAgentEnvironment.get("PATH", ""),
    )
    if not bPathKept:
        listViolations.append(_fdictViolation(
            "settings", "PATH", "reorders or drops the environment's PATH",
        ))
    return listViolations, listPrepended


def _fdictParseEnvironment(dictConfig):
    dictVariables = {}
    for sAssignment in (dictConfig or {}).get("Env") or []:
        sName, _, sValue = str(sAssignment).partition("=")
        dictVariables[sName] = sValue
    return dictVariables


def _ftComparePath(sBasePath, sAgentPath):
    """Return ``(listPrepended, bKept)``: the base PATH must survive as a suffix."""
    listBase = _flistUniqueInOrder(sBasePath.split(":"))
    listAgent = _flistUniqueInOrder(sAgentPath.split(":"))
    iExtra = len(listAgent) - len(listBase)
    if iExtra < 0 or listAgent[iExtra:] != listBase:
        return listAgent, False
    return listAgent[:iExtra], True


def _flistUniqueInOrder(listItems):
    listUnique = []
    for sItem in listItems:
        if sItem and sItem not in listUnique:
            listUnique.append(sItem)
    return listUnique


def _flistFindShadowedCommands(
    dictBase, dictAgentFinal, dictBaseConfig, listExtraDirectories,
):
    """Rule 2: an added command whose name the environment already runs."""
    listBasePath = _flistUniqueInOrder(
        _fdictParseEnvironment(dictBaseConfig).get("PATH", "").split(":"),
    )
    setBaseCommands = {
        posixpath.basename(sPath) for sPath, dictEntry in dictBase.items()
        if dictEntry["sKind"] != "directory"
        and posixpath.dirname(sPath) in listBasePath
    }
    setSearched = set(listBasePath) | {
        s.rstrip("/") for s in listExtraDirectories
    }
    return [
        _fdictViolation(
            "shadows", sPath,
            f"adds a {posixpath.basename(sPath)} command the environment "
            "already has",
        )
        for sPath, dictEntry in sorted(dictAgentFinal.items())
        if sPath not in dictBase
        and dictEntry["sKind"] in ("file", "link")
        and posixpath.dirname(sPath) in setSearched
        and posixpath.basename(sPath) in setBaseCommands
    ]


def fsDescribeViolations(listViolations, iMaximum=8):
    """Return a researcher-facing sentence naming the first few violations."""
    listLines = [
        f"{dictViolation['sPath']} ({dictViolation['sDetail']})"
        for dictViolation in listViolations[:iMaximum]
    ]
    sMore = ""
    if len(listViolations) > iMaximum:
        sMore = f"; and {len(listViolations) - iMaximum} more"
    return "; ".join(listLines) + sMore


# ------------------------------------------------------------------
# Against a daemon
# ------------------------------------------------------------------


def fdictCheckAgentLayerSeparation(sEnvironmentImageId, sAgentImageId):
    """Check the agents' image against the environment it stands on.

    Returns ``{listViolations, iAgentLayers, iAddedPaths}``. Raises
    ``AgentLayerSeparationError`` when the check could not run -- the
    agents' image is not built on the environment, or a layer could
    not be read -- because an unrun check establishes nothing and must
    never read as a pass.
    """
    _fnEnsureDockerAvailable()
    dictEnvironment = _fdictInspectImage(sEnvironmentImageId)
    dictAgents = _fdictInspectImage(sAgentImageId)
    listBaseIds = dictEnvironment["RootFS"]["Layers"]
    listAgentIds = dictAgents["RootFS"]["Layers"]
    if listAgentIds[:len(listBaseIds)] != listBaseIds:
        raise AgentLayerSeparationError(
            f"{sAgentImageId} is not built on {sEnvironmentImageId}, so "
            "its agent layers cannot be told apart from the environment"
        )
    dictLayers = _fdictSaveAndReadLayers(sAgentImageId, set(listAgentIds))
    listMissing = [s for s in listAgentIds if s not in dictLayers]
    if listMissing:
        raise AgentLayerSeparationError(
            "docker save did not yield every layer of the image "
            f"({len(listMissing)} unreadable), so nothing was checked"
        )
    listAgentLayers = [dictLayers[s] for s in listAgentIds[len(listBaseIds):]]
    return {
        "listViolations": flistFindSeparationViolations(
            [dictLayers[s] for s in listBaseIds], listAgentLayers,
            dictEnvironment.get("Config") or {},
            dictAgents.get("Config") or {},
        ),
        "iAgentLayers": len(listAgentLayers),
        "iAddedPaths": sum(len(dictLayer) for dictLayer in listAgentLayers),
    }


def _fdictInspectImage(sImageReference):
    try:
        return json.loads(_fsRunCheckedCommand(
            ["docker", "image", "inspect", sImageReference],
        ))[0]
    except (subprocess.CalledProcessError, ValueError, IndexError) as error:
        raise AgentLayerSeparationError(
            f"could not inspect {sImageReference}: {error}"
        ) from error


def _fdictSaveAndReadLayers(sImageReference, setWantedDiffIds):
    """Stream ``docker save`` once and read the wanted layers out of it."""
    processSave = subprocess.Popen(
        ["docker", "save", sImageReference],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        dictLayers = fdictReadSavedLayers(processSave.stdout, setWantedDiffIds)
        while processSave.stdout.read(_I_READ_CHUNK_BYTES):
            pass
        sStderr = (processSave.stderr.read() or b"").decode("utf-8", "replace")
    finally:
        processSave.stdout.close()
        processSave.stderr.close()
    if processSave.wait() != 0:
        raise AgentLayerSeparationError(
            f"docker save {sImageReference} failed: {sStderr.strip()}"
        )
    return dictLayers


def fdictVerifyAgentsLeaveThePinAlone(sPinnedImage, dictLiveIdentity):
    """Refuse to publish an environment the agents are part of, or can reach.

    The deposit-time gate. ``dictLiveIdentity`` is what
    ``fdictCaptureLiveImageIdentity`` answers for the researcher's
    container. Returns ``{sRelation, iAddedPaths}``: ``same`` when the
    container runs the pin itself, ``agents-above`` when it runs agents
    stacked on the pin and their layers passed every rule, and
    ``unrelated`` when it runs neither -- the envelope row reports that
    mismatch, and there is nothing here to compare. Raises
    ``AgentLayerSeparationError`` naming what to do otherwise.
    """
    listAgentsInside = _flistAgentSideOverlays(sPinnedImage)
    if listAgentsInside:
        raise AgentLayerSeparationError(
            "the image this environment pins contains coding agents ("
            + ", ".join(listAgentsInside) + "), and the environment "
            "archive publishes the agent-free environment only. Rebuild "
            "the project image with this vaibify, restart the container "
            "so the envelope pins the agent-free image, and deposit again."
        )
    dictIdentity = dictLiveIdentity or {}
    if sPinnedImage in (dictIdentity.get("sImageDigest"), dictIdentity.get("sImageId")):
        return {"sRelation": "same", "iAddedPaths": 0}
    if sPinnedImage not in (
        dictIdentity.get("sEnvironmentImageDigest") or None,
        dictIdentity.get("sEnvironmentImageId") or None,
    ):
        return {"sRelation": "unrelated", "iAddedPaths": 0}
    dictChecked = fdictCheckAgentLayerSeparation(
        dictIdentity["sEnvironmentImageId"], dictIdentity["sImageId"],
    )
    if dictChecked["listViolations"]:
        raise AgentLayerSeparationError(
            "the coding agents installed above the environment reach into "
            "it, so the agent-free image cannot stand for the one your "
            "results came from: "
            + fsDescribeViolations(dictChecked["listViolations"])
            + ". Remove the agent that installs these, rebuild, and "
            "deposit again."
        )
    return {"sRelation": "agents-above", "iAddedPaths": dictChecked["iAddedPaths"]}


def _flistAgentSideOverlays(sImageReference):
    """Return the agent-side overlays an image's own label says it holds."""
    from vaibify.docker.imageBuilder import fbOverlayBelongsToEnvironment
    from vaibify.reproducibility.environmentSnapshot import (
        _flistReadOverlaysLabel,
    )
    return [
        sOverlay for sOverlay in _flistReadOverlaysLabel(sImageReference)
        if not fbOverlayBelongsToEnvironment(sOverlay)
    ]


def fsDescribeAgentsLeftOut(dictContainer, dictAttestation):
    """Return the sentence naming the agents used beside this environment.

    Names from the envelope (the overlays the container held above the
    pinned image), versions from the AI provenance stamp the Level 3
    attestation captured; either may be absent, and the sentence says
    only what was recorded.
    """
    listAgents = list((dictContainer or {}).get("listAgentOverlays") or [])
    if not listAgents:
        return ""
    dictVersions = (
        ((dictAttestation or {}).get("dictAiProvenance") or {})
        .get("dictAgentCliVersions") or {}
    )
    listNamed = [
        f"{sAgent} ({dictVersions[sAgent]})" if dictVersions.get(sAgent)
        else sAgent
        for sAgent in listAgents
    ]
    return (
        " The coding agents the author worked with were installed above "
        "this image and are not part of it: " + ", ".join(listNamed)
        + ". Before this deposit, vaibify checked their layers and found "
        "that they only add files the environment does not search."
    )
