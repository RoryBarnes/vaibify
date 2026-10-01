#!/usr/bin/env python3
"""List the repository-local executable git surface of repositories.

Git can run programs that a repository itself configures: a filesystem
monitor, clean/smudge/process filters selected through ``.gitattributes``,
text converters, external diff and merge drivers, pagers, hooks,
credential helpers, ssh and proxy commands, signing programs, and URL
schemes such as ``ext::``. This tool reports, for each repository given,
exactly those definitions that live INSIDE the repository: its local
config (with ``include.path`` and ``includeIf`` files resolved),
``.gitattributes`` driver references, ``.git/info/attributes`` and the
executable files under ``.git/hooks``.

READ-ONLY AND NON-EXECUTING, by construction. The tool reads files and
checks permission bits; it never starts a process, never runs git, and
never opens a hook. The user's global and system git configuration is
excluded because it is the user's own trusted configuration. An
``includeIf`` file is reported as conditional but always parsed, because
whether its condition holds is something only git can decide.

The ``sFingerprint`` of a repository is a SHA-256 over its sorted surface
items, so "has this repository's executable surface changed since I last
looked" is one string comparison.

Usage::

    python tools/inventoryRepositoryGitSurface.py REPO [REPO ...]
    python tools/inventoryRepositoryGitSurface.py --discover DIR [--json]
    python tools/inventoryRepositoryGitSurface.py --discover DIR --summary

Exit status: 0 when every path was inspected, 2 when a path is not a
git repository.
"""

import argparse
import hashlib
import json
import os
import re
import sys

I_MAXIMUM_CONFIG_BYTES = 1024 * 1024
I_MAXIMUM_ATTRIBUTE_FILES = 20000
I_MAXIMUM_INCLUDE_DEPTH = 10
I_DISCOVERY_DEPTH = 2

SET_EXECUTABLE_KEY_SUFFIXES = {
    "filter": {"clean", "smudge", "process"},
    "diff": {"textconv", "command"},
    "merge": {"driver"},
    "mergetool": {"cmd"},
    "difftool": {"cmd"},
    "credential": {"helper"},
    "gpg": {"program"},
    "trailer": {"command"},
    "browser": {"cmd"},
    "remote": {"vcs", "uploadpack", "receivepack"},
}
SET_EXECUTABLE_PLAIN_KEYS = {
    "core.fsmonitor", "core.pager", "core.sshcommand", "core.gitproxy",
    "core.askpass", "core.editor", "diff.external", "gpg.program",
    "gpg.ssh.program", "gpg.x509.program", "gpg.openpgp.program",
    "sequence.editor", "web.browser",
}
SET_BOOLEAN_WORDS = {"true", "false", "yes", "no", "on", "off", "0", "1", ""}
REGEX_ATTRIBUTE_DRIVER = re.compile(r"(?:^|\s)(filter|diff|merge)=([^\s]+)")


def fsDecodeConfigValue(sRaw):
    """Return a git config value with quotes, escapes and comments resolved."""
    listCharacters = []
    bQuoted = False
    iIndex = 0
    dictEscapes = {"n": "\n", "t": "\t", "b": "\b", "\\": "\\", '"': '"'}
    while iIndex < len(sRaw):
        sCharacter = sRaw[iIndex]
        if sCharacter == "\\" and iIndex + 1 < len(sRaw):
            sNext = sRaw[iIndex + 1]
            listCharacters.append(dictEscapes.get(sNext, sNext))
            iIndex += 2
            continue
        if sCharacter == '"':
            bQuoted = not bQuoted
        elif sCharacter in "#;" and not bQuoted:
            break
        else:
            listCharacters.append(sCharacter)
        iIndex += 1
    return "".join(listCharacters).strip()


def flistJoinContinuationLines(sText):
    """Return logical config lines, joining those ending in a backslash."""
    listLogical = []
    sPending = ""
    for sLine in sText.splitlines():
        if sLine.endswith("\\") and not sLine.endswith("\\\\"):
            sPending += sLine[:-1]
            continue
        listLogical.append(sPending + sLine)
        sPending = ""
    if sPending:
        listLogical.append(sPending)
    return listLogical


def ftParseSectionHeader(sLine):
    """Return ``(section, subsection)`` from a ``[section "sub"]`` line."""
    sInner = sLine.strip()[1:].split("]", 1)[0]
    matchQuoted = re.match(r'^\s*([^\s"]+)\s+"(.*)"\s*$', sInner)
    if matchQuoted:
        return matchQuoted.group(1).lower(), matchQuoted.group(2)
    if "." in sInner:
        sSection, sSubsection = sInner.split(".", 1)
        return sSection.strip().lower(), sSubsection
    return sInner.strip().lower(), ""


def flistParseConfigText(sText):
    """Return ``(key, value)`` pairs; a key has no value when it is bare."""
    listEntries = []
    sSection, sSubsection = "", ""
    for sLine in flistJoinContinuationLines(sText):
        sStripped = sLine.strip()
        if not sStripped or sStripped[0] in "#;":
            continue
        if sStripped.startswith("["):
            sSection, sSubsection = ftParseSectionHeader(sStripped)
            sRemainder = sStripped.split("]", 1)[1].strip()
            if not sRemainder or sRemainder[0] in "#;":
                continue
            sStripped = sRemainder
        sName, sSeparator, sRawValue = sStripped.partition("=")
        sKeyName = sName.strip().lower()
        listKeyParts = [sSection, sSubsection, sKeyName]
        sFullKey = ".".join(sPart for sPart in listKeyParts if sPart)
        sValue = fsDecodeConfigValue(sRawValue) if sSeparator else None
        listEntries.append((sFullKey, sValue))
    return listEntries


def fsReadSmallTextFile(sPath):
    """Return a file's text, or empty when unreadable or oversized."""
    try:
        if os.path.getsize(sPath) > I_MAXIMUM_CONFIG_BYTES:
            return ""
        with open(sPath, "r", encoding="utf-8", errors="replace") as fileRead:
            return fileRead.read()
    except OSError:
        return ""


def fsResolveGitDirectory(sRepository):
    """Return the real git directory of a work tree, or empty when none."""
    sDotGit = os.path.join(sRepository, ".git")
    if os.path.isdir(sDotGit):
        return sDotGit
    if os.path.isfile(sDotGit):
        sPointer = fsReadSmallTextFile(sDotGit).strip()
        if sPointer.startswith("gitdir:"):
            sTarget = sPointer.split(":", 1)[1].strip()
            return os.path.normpath(os.path.join(sRepository, sTarget))
    return ""


def fsDescribeLocation(sPath, sRepository, sGitDirectory):
    """Return a path relative to the repository, never an absolute one."""
    for sRoot, sLabel in ((sGitDirectory, ".git"), (sRepository, "")):
        if sRoot and (sPath == sRoot or sPath.startswith(sRoot + os.sep)):
            sRelative = os.path.relpath(sPath, sRoot)
            return os.path.join(sLabel, sRelative) if sLabel else sRelative
    return "outside:" + os.path.basename(sPath)


def fsResolveIncludePath(sValue, sIncludingFile):
    """Return the file an ``include.path`` value names."""
    sExpanded = os.path.expanduser(sValue)
    if os.path.isabs(sExpanded):
        return sExpanded
    return os.path.normpath(
        os.path.join(os.path.dirname(sIncludingFile), sExpanded)
    )


def flistCollectConfigEntries(sConfigPath, sRepository, sGitDirectory,
                              bConditional=False, iDepth=0, setSeen=None):
    """Return ``(key, value, source, conditional)`` for a file and its includes."""
    setSeen = set() if setSeen is None else setSeen
    sReal = os.path.realpath(sConfigPath)
    if sReal in setSeen or iDepth > I_MAXIMUM_INCLUDE_DEPTH:
        return []
    setSeen.add(sReal)
    sSource = fsDescribeLocation(sConfigPath, sRepository, sGitDirectory)
    listCollected = []
    for sKey, sValue in flistParseConfigText(fsReadSmallTextFile(sConfigPath)):
        if sKey == "include.path" or (
            sKey.startswith("includeif.") and sKey.endswith(".path")
        ):
            bIncludeConditional = bConditional or sKey != "include.path"
            listCollected.extend(flistCollectConfigEntries(
                fsResolveIncludePath(sValue or "", sConfigPath), sRepository,
                sGitDirectory, bIncludeConditional, iDepth + 1, setSeen,
            ))
            continue
        listCollected.append((sKey, sValue, sSource, bConditional))
    return listCollected


def fbKeyRunsAProgram(sKey, sValue):
    """Return True when a config key names a program git may execute."""
    if sKey in SET_EXECUTABLE_PLAIN_KEYS:
        if sKey == "core.fsmonitor":
            return (sValue or "").strip().lower() not in SET_BOOLEAN_WORDS
        return bool((sValue or "").strip())
    listParts = sKey.split(".")
    if len(listParts) >= 3:
        sSection, sLeaf = listParts[0], listParts[-1]
        if sLeaf in SET_EXECUTABLE_KEY_SUFFIXES.get(sSection, set()):
            return bool((sValue or "").strip())
    if sKey.startswith("pager.") and len(listParts) == 2:
        return (sValue or "").strip().lower() not in SET_BOOLEAN_WORDS
    if sKey.startswith("credential.") and listParts[-1] == "helper":
        return bool((sValue or "").strip())
    return fbKeyRewritesToExternalTransport(sKey, sValue)


def fbKeyRewritesToExternalTransport(sKey, sValue):
    """Return True for a remote URL or rewrite that selects ``ext::``."""
    listParts = sKey.split(".")
    if listParts[0] == "remote" and listParts[-1] in ("url", "pushurl"):
        return (sValue or "").startswith("ext::")
    if listParts[0] == "url" and listParts[-1] in (
        "insteadof", "pushinsteadof",
    ):
        return ".".join(listParts[1:-1]).startswith("ext::")
    return False


def flistFindConfigSurface(listEntries):
    """Return surface items for the entries that name a program."""
    listItems = []
    for sKey, sValue, sSource, bConditional in listEntries:
        if sKey == "core.hookspath":
            listItems.append({
                "sKind": "hooksPathRedirect", "sKey": sKey,
                "sValue": sValue or "", "sSource": sSource,
                "bConditional": bConditional,
            })
        elif fbKeyRunsAProgram(sKey, sValue):
            listItems.append({
                "sKind": "config", "sKey": sKey, "sValue": sValue or "",
                "sSource": sSource, "bConditional": bConditional,
            })
    return listItems


def flistFindAttributeFiles(sRepository, sGitDirectory):
    """Return every attributes file inside the repository, bounded."""
    listFiles = []
    sInfoAttributes = os.path.join(sGitDirectory, "info", "attributes")
    if os.path.isfile(sInfoAttributes):
        listFiles.append(sInfoAttributes)
    iVisited = 0
    for sRoot, listDirectories, listNames in os.walk(sRepository):
        listDirectories[:] = [d for d in listDirectories if d != ".git"]
        iVisited += 1
        if ".gitattributes" in listNames:
            listFiles.append(os.path.join(sRoot, ".gitattributes"))
        if iVisited >= I_MAXIMUM_ATTRIBUTE_FILES:
            break
    return listFiles


def fsetDefinedDriverNames(listEntries):
    """Return ``{(kind, name)}`` for drivers the local config defines."""
    setDefined = set()
    for sKey, _sValue, _sSource, _bConditional in listEntries:
        listParts = sKey.split(".")
        if len(listParts) >= 3 and listParts[0] in ("filter", "diff", "merge"):
            setDefined.add((listParts[0], ".".join(listParts[1:-1])))
    return setDefined


def flistFindAttributeSurface(sRepository, sGitDirectory, listEntries):
    """Return the driver references in the repository's attributes files."""
    setDefined = fsetDefinedDriverNames(listEntries)
    setSeen = set()
    listItems = []
    for sAttributesFile in flistFindAttributeFiles(sRepository, sGitDirectory):
        sSource = fsDescribeLocation(sAttributesFile, sRepository,
                                     sGitDirectory)
        for sLine in fsReadSmallTextFile(sAttributesFile).splitlines():
            for sKind, sName in REGEX_ATTRIBUTE_DRIVER.findall(sLine):
                if (sSource, sKind, sName) in setSeen:
                    continue
                setSeen.add((sSource, sKind, sName))
                listItems.append({
                    "sKind": "attributeDriver", "sAttribute": sKind,
                    "sDriverName": sName, "sSource": sSource,
                    "bDefinedLocally": (sKind, sName) in setDefined,
                })
    return listItems


def flistListExecutableFiles(sDirectory):
    """Return names of executable regular files, without reading them."""
    listNames = []
    try:
        listCandidates = sorted(os.listdir(sDirectory))
    except OSError:
        return listNames
    for sName in listCandidates:
        sPath = os.path.join(sDirectory, sName)
        if (os.path.isfile(sPath) and os.access(sPath, os.X_OK)
                and not sName.endswith(".sample")):
            listNames.append(sName)
    return listNames


def flistFindHookSurface(sGitDirectory, listConfigItems, sRepository):
    """Return hooks under ``.git/hooks`` and under any redirected hooks path."""
    listItems = [
        {"sKind": "hook", "sName": sName, "sSource": ".git/hooks"}
        for sName in flistListExecutableFiles(
            os.path.join(sGitDirectory, "hooks"))
    ]
    for dictItem in listConfigItems:
        if dictItem["sKind"] != "hooksPathRedirect":
            continue
        sRedirected = os.path.expanduser(dictItem["sValue"])
        if not os.path.isabs(sRedirected):
            sRedirected = os.path.join(sRepository, sRedirected)
        for sName in flistListExecutableFiles(sRedirected):
            listItems.append({
                "sKind": "hook", "sName": sName,
                "sSource": "hooksPath:" + (
                    fsDescribeLocation(sRedirected, sRepository,
                                       sGitDirectory)),
            })
    return listItems


def fsComputeFingerprint(listItems):
    """Return a SHA-256 over the sorted surface items."""
    listCanonical = sorted(
        json.dumps(dictItem, sort_keys=True) for dictItem in listItems
    )
    return hashlib.sha256("\n".join(listCanonical).encode()).hexdigest()


def fdictInventoryRepository(sRepository):
    """Return the executable git surface of one repository, read-only."""
    sGitDirectory = fsResolveGitDirectory(sRepository)
    if not sGitDirectory:
        raise ValueError("not a git repository: " + os.path.basename(
            os.path.abspath(sRepository)))
    listEntries = []
    for sName in ("config", "config.worktree"):
        listEntries.extend(flistCollectConfigEntries(
            os.path.join(sGitDirectory, sName), sRepository, sGitDirectory,
        ))
    listItems = flistFindConfigSurface(listEntries)
    listItems += flistFindAttributeSurface(
        sRepository, sGitDirectory, listEntries)
    listItems += flistFindHookSurface(sGitDirectory, listItems, sRepository)
    return {
        "sRepository": os.path.basename(os.path.abspath(sRepository)),
        "listItems": listItems,
        "sFingerprint": fsComputeFingerprint(listItems),
    }


def flistDiscoverRepositories(sRoot):
    """Return directories under ``sRoot`` (to a fixed depth) holding a .git."""
    listFound = []
    sBase = os.path.abspath(sRoot)
    for sCurrent, listDirectories, _listNames in os.walk(sBase):
        iDepth = sCurrent[len(sBase):].count(os.sep)
        if os.path.exists(os.path.join(sCurrent, ".git")):
            listFound.append(sCurrent)
            listDirectories[:] = []
        elif iDepth >= I_DISCOVERY_DEPTH:
            listDirectories[:] = []
    return sorted(listFound)


def fdictSummarizeInventories(listInventories):
    """Return counts of repositories and surface items by mechanism."""
    dictMechanismCounts = {}
    iWithSurface = 0
    for dictInventory in listInventories:
        setMechanisms = set()
        for dictItem in dictInventory["listItems"]:
            if dictItem["sKind"] == "config":
                setMechanisms.add(dictItem["sKey"].split(".")[0]
                                  + "." + dictItem["sKey"].split(".")[-1])
            elif dictItem["sKind"] == "attributeDriver":
                sState = "defined" if dictItem["bDefinedLocally"] else "undefined"
                setMechanisms.add(
                    "attributes." + dictItem["sAttribute"] + "(" + sState + ")")
            else:
                setMechanisms.add(dictItem["sKind"])
        iWithSurface += 1 if setMechanisms else 0
        for sMechanism in setMechanisms:
            dictMechanismCounts[sMechanism] = (
                dictMechanismCounts.get(sMechanism, 0) + 1)
    return {
        "iRepositories": len(listInventories),
        "iRepositoriesWithAnySurface": iWithSurface,
        "dictRepositoriesPerMechanism": dict(sorted(dictMechanismCounts.items())),
    }


def fnPrintInventory(dictInventory):
    """Print one repository's surface as readable text."""
    print(f"{dictInventory['sRepository']}  {dictInventory['sFingerprint'][:12]}")
    if not dictInventory["listItems"]:
        print("  (no repository-local executable surface)")
    for dictItem in dictInventory["listItems"]:
        sDetail = dictItem.get("sKey") or dictItem.get("sName") or (
            dictItem["sAttribute"] + "=" + dictItem["sDriverName"])
        sValue = dictItem.get("sValue", "")
        print(f"  {dictItem['sKind']:<16} {sDetail}  {sValue}  "
              f"[{dictItem['sSource']}]")


def fparserBuildArgumentParser():
    """Return the command-line parser."""
    parserArguments = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parserArguments.add_argument("repositories", nargs="*")
    parserArguments.add_argument("--discover", metavar="DIR", action="append",
                                 default=[])
    parserArguments.add_argument("--json", action="store_true")
    parserArguments.add_argument("--summary", action="store_true")
    return parserArguments


def fiRunInventory(namespaceArguments):
    """Inventory the requested repositories; return the exit status."""
    listPaths = list(namespaceArguments.repositories)
    for sRoot in namespaceArguments.discover:
        listPaths.extend(flistDiscoverRepositories(sRoot))
    listInventories = []
    iExitStatus = 0
    for sPath in listPaths:
        try:
            listInventories.append(fdictInventoryRepository(sPath))
        except ValueError as error:
            print(f"error: {error}", file=sys.stderr)
            iExitStatus = 2
    fnEmitReport(listInventories, namespaceArguments)
    return iExitStatus


def fnEmitReport(listInventories, namespaceArguments):
    """Print the inventories in the requested format."""
    if namespaceArguments.summary:
        print(json.dumps(fdictSummarizeInventories(listInventories), indent=2))
    elif namespaceArguments.json:
        print(json.dumps(listInventories, indent=2))
    else:
        for dictInventory in listInventories:
            fnPrintInventory(dictInventory)


def main():
    """Command-line entry point."""
    namespaceArguments = fparserBuildArgumentParser().parse_args()
    if not (namespaceArguments.repositories or namespaceArguments.discover):
        print("error: give at least one repository or --discover DIR",
              file=sys.stderr)
        return 2
    return fiRunInventory(namespaceArguments)


if __name__ == "__main__":
    sys.exit(main())
