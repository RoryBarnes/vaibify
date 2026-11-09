"""Report which declared requirements the installed packages do not satisfy.

Runs INSIDE an image, with nothing but the standard library and
``packaging``, which the base image installs as a vaibify dependency.
Two callers ask it the same question:

- the build, after the last environment stage, in the pinned
  environment image with the container's start bypassed, exactly as the
  Level 3 shadow and ``reproduce.sh`` run it: every list the image
  installed must be satisfied by what the image holds;
- the container's start, for the declaration each project's working
  copy holds NOW, so an edit the image predates is named.

Satisfaction, not names: a tightened version, an added extra or a new
package all count. The installed set is read through
``importlib.metadata``, unfiltered, the inventory the shadow reads.

Usage: ``checkDeclaredRequirements.py [<source>=<file> ...]``. With no
arguments it checks the image's own lists. Prints one JSON object,
``{"listUnsatisfied": [...], "listUnchecked": [...]}``; each entry names
the source, the requirement as declared and, when unsatisfied, the
version installed ("" for none). Exit status 0 when every checked
requirement is satisfied, 1 when one is not, 2 when it could not run.
"""

import glob
import importlib.metadata
import json
import operator
import os
import re
import sys

try:
    from packaging.markers import UndefinedEnvironmentName
    from packaging.requirements import InvalidRequirement, Requirement
    from packaging.utils import canonicalize_name
except ImportError:
    sys.stdout.write(json.dumps({"sError": "packaging is not installed"}) + "\n")
    sys.exit(2)


S_IMAGE_REQUIREMENTS = "/etc/vaibify/requirements.txt"
S_FEATURE_REQUIREMENTS_GLOB = "/etc/vaibify/requirements.d/*.txt"
S_PROJECT_REQUIREMENTS_GLOB = "/etc/vaibify/projectRequirements/*.txt"
_REGEX_COMMENT = re.compile(r"(^|\s)#.*$")


def flistImageSources():
    """Return ``(source, file)`` for every list the image installed."""
    listSources = []
    if os.path.isfile(S_IMAGE_REQUIREMENTS):
        listSources.append(("pythonPackages in vaibify.yml", S_IMAGE_REQUIREMENTS))
    for sFile in sorted(glob.glob(S_FEATURE_REQUIREMENTS_GLOB)):
        sName = os.path.basename(sFile)[:-len(".txt")]
        listSources.append((f"the {sName} feature", sFile))
    for sFile in sorted(glob.glob(S_PROJECT_REQUIREMENTS_GLOB)):
        sName = os.path.basename(sFile)[:-len(".txt")]
        listSources.append((f"project {sName}", sFile))
    return listSources


def flistDeclaredLines(sFile):
    """Return each requirement text a file declares, in order."""
    with open(sFile, "r", encoding="utf-8") as fileHandle:
        sText = fileHandle.read()
    if sFile.endswith(".toml"):
        return _flistPyprojectDependencies(sText)
    listLines = []
    for sRawLine in sText.splitlines():
        sLine = _REGEX_COMMENT.sub("", sRawLine).strip()
        if sLine:
            listLines.append(sLine)
    return listLines


def _flistPyprojectDependencies(sText):
    try:
        import tomllib
    except ImportError:
        raise ValueError("this image's Python cannot read TOML (3.11+ can)")
    dictProject = tomllib.loads(sText).get("project")
    if not isinstance(dictProject, dict):
        return []
    return [str(sEntry) for sEntry in dictProject.get("dependencies") or []]


def fdictInstalledVersions():
    """Return ``{canonical name: version}`` for every installed distribution."""
    dictInstalled = {}
    for tMetadataAndVersion in map(
            operator.attrgetter("metadata", "version"),
            importlib.metadata.distributions()):
        sName = tMetadataAndVersion[0]["Name"]
        if sName:
            dictInstalled.setdefault(
                canonicalize_name(sName), tMetadataAndVersion[1])
    return dictInstalled


def fsUnsatisfiedReason(sLine, dictInstalled):
    """Return "" when installed packages satisfy ``sLine``, else what is installed.

    The answer for an unsatisfied requirement is the installed version
    of the package it names, or "none". An extra is satisfied when each
    requirement it adds is. Raises ``ValueError`` for a line that is not
    a requirement (an option, a path), which the caller reports as
    unchecked.
    """
    try:
        sName = Requirement(sLine).name
    except InvalidRequirement as errorRequirement:
        raise ValueError(str(errorRequirement)) from errorRequirement
    if not _fbMarkerApplies(sLine, ""):
        return ""
    sVersion = dictInstalled.get(canonicalize_name(sName))
    if sVersion is None:
        return "none"
    if not Requirement(sLine).specifier.contains(sVersion, prereleases=True):
        return sVersion
    for sExtra in sorted(Requirement(sLine).extras):
        for sAdded in _flistRequirementsOfExtra(sName, sExtra):
            if fsUnsatisfiedReason(sAdded, dictInstalled):
                return f"{sVersion}, without what [{sExtra}] adds ({sAdded})"
    return ""


def _flistRequirementsOfExtra(sName, sExtra):
    """Return the requirements an installed package adds for one extra."""
    try:
        listDeclared = importlib.metadata.requires(sName) or []
    except importlib.metadata.PackageNotFoundError:
        return []
    return [
        sRequirement.split(";", 1)[0].strip() for sRequirement in listDeclared
        if Requirement(sRequirement).marker is not None
        and _fbMarkerApplies(sRequirement, sExtra)
    ]


def _fbMarkerApplies(sLine, sExtra):
    """Return whether a requirement's environment marker holds here."""
    if Requirement(sLine).marker is None:
        return True
    try:
        return Requirement(sLine).marker.evaluate({"extra": sExtra})
    except UndefinedEnvironmentName:
        return False


def fdictCheckSources(listSources):
    """Return the unsatisfied and unchecked requirements of every source."""
    dictInstalled = fdictInstalledVersions()
    dictReport = {"listUnsatisfied": [], "listUnchecked": []}
    for sSource, sFile in listSources:
        try:
            listLines = flistDeclaredLines(sFile)
        except (OSError, ValueError, UnicodeDecodeError) as errorRead:
            dictReport["listUnchecked"].append(
                {"sSource": sSource, "sRequirement": "", "sReason": str(errorRead)})
            continue
        for sLine in listLines:
            try:
                sInstalled = fsUnsatisfiedReason(sLine, dictInstalled)
            except ValueError as errorLine:
                dictReport["listUnchecked"].append(
                    {"sSource": sSource, "sRequirement": sLine,
                     "sReason": str(errorLine)})
                continue
            if sInstalled:
                dictReport["listUnsatisfied"].append(
                    {"sSource": sSource, "sRequirement": sLine,
                     "sInstalled": sInstalled})
    return dictReport


def flistSourcesFromArguments(listArguments):
    """Return ``(source, file)`` pairs from ``<source>=<file>`` arguments."""
    listSources = []
    for sArgument in listArguments:
        sSource, sSeparator, sFile = sArgument.partition("=")
        if not sSeparator or not sSource or not sFile:
            raise ValueError(f"expected <source>=<file>, got {sArgument!r}")
        listSources.append((sSource, sFile))
    return listSources


def main():
    try:
        listSources = (flistSourcesFromArguments(sys.argv[1:])
                       if len(sys.argv) > 1 else flistImageSources())
    except ValueError as errorArguments:
        sys.stdout.write(json.dumps({"sError": str(errorArguments)}) + "\n")
        return 2
    dictReport = fdictCheckSources(listSources)
    sys.stdout.write(json.dumps(dictReport, sort_keys=True) + "\n")
    return 1 if dictReport["listUnsatisfied"] else 0


if __name__ == "__main__":
    sys.exit(main())
