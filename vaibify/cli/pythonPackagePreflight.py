"""Ask the package index whether every ``pythonPackages`` name exists.

A misspelled name under ``pythonPackages`` used to cost a full image
build to discover: apt and the pinned toolchain ran for minutes before
pip refused ``matplolib``, and the sentence the researcher then read
did not name it (a live build, 2026-09-21). The index answers the same
question in under a second per name, so it is asked before the build,
by the CLI and the GUI alike. Only NAMES are asked; a version that no
release matches is still pip's to report, and the failure catalog
names it when that happens.

Two answers are deliberately not refusals. An index vaibify cannot ask
(``pipInstallFlags`` naming another index, or pypi.org not answering)
reports NOT CHECKED and lets the build ask for itself, because a
network miss is not evidence about the name.
"""

import re
import urllib.error
import urllib.request

from .preflightResult import (
    PreflightResult, S_LEVEL_FAIL, S_LEVEL_NOT_CHECKED, S_SCOPE_PROJECT,
)


__all__ = [
    "IndexUnreachableError",
    "S_PREFLIGHT_NAME",
    "fbIndexIsOverridden",
    "fbNameExistsOnIndex",
    "flistUnknownPythonPackages",
    "fpreflightPythonPackageNames",
    "fsNormalizePackageName",
    "fsPackageNameFromRequirement",
]


S_PREFLIGHT_NAME = "python-package-names"
S_PYPI_SIMPLE_INDEX = "https://pypi.org/simple/"
F_INDEX_TIMEOUT_SECONDS = 5.0

# A requirement that is not a plain index lookup: pip options, local
# paths, URLs and VCS references. pip never asks the index for these,
# so neither does this check.
_T_UNCHECKABLE_PREFIXES = ("-", ".", "/", "~", "git+", "hg+", "svn+",
                           "http://", "https://", "file:")
# Flags under which pip reads an index other than pypi.org, in whole
# or in part; a name absent from pypi.org may exist there.
_T_INDEX_OVERRIDE_FLAGS = (
    "--index-url", "-i", "--extra-index-url", "--find-links", "-f",
    "--no-index",
)
_REGEX_REQUIREMENT_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


class IndexUnreachableError(Exception):
    """The index gave no answer about a name; nothing is known."""


def fsNormalizePackageName(sName):
    """Return the name as the simple index spells it (PEP 503)."""
    return re.sub(r"[-_.]+", "-", sName).lower()


def fsPackageNameFromRequirement(sRequirement):
    """Return the normalized name a requirement asks the index for.

    Empty when the requirement is not an index lookup at all: a pip
    option, a path, a URL, or a ``name @ url`` direct reference.
    """
    sStripped = (sRequirement or "").strip()
    if not sStripped or sStripped.startswith(_T_UNCHECKABLE_PREFIXES):
        return ""
    if " @ " in sStripped or "@" in sStripped.split("[", 1)[0].split(
        "=", 1,
    )[0]:
        return ""
    matchName = _REGEX_REQUIREMENT_NAME.match(sStripped)
    if not matchName:
        return ""
    return fsNormalizePackageName(matchName.group(1))


def fbIndexIsOverridden(sPipInstallFlags):
    """Return True when pip would read an index other than pypi.org."""
    listTokens = (sPipInstallFlags or "").split()
    return any(
        sToken == sFlag or sToken.startswith(sFlag + "=")
        for sToken in listTokens
        for sFlag in _T_INDEX_OVERRIDE_FLAGS
    )


def fbNameExistsOnIndex(
    sName, sIndexUrl=S_PYPI_SIMPLE_INDEX, fTimeout=F_INDEX_TIMEOUT_SECONDS,
):
    """Return whether the simple index serves a project page for a name.

    A 404 is the index's answer that no such project exists; any other
    failure is no answer, and is raised rather than read as absence.
    """
    requestHead = urllib.request.Request(
        f"{sIndexUrl}{sName}/", method="HEAD",
    )
    try:
        with urllib.request.urlopen(requestHead, timeout=fTimeout):
            return True
    except urllib.error.HTTPError as errorHttp:
        if errorHttp.code == 404:
            return False
        raise IndexUnreachableError(
            f"{sIndexUrl} answered HTTP {errorHttp.code} for {sName!r}",
        ) from errorHttp
    except (urllib.error.URLError, OSError) as errorNetwork:
        raise IndexUnreachableError(
            f"{sIndexUrl} could not be reached: {errorNetwork}",
        ) from errorNetwork


def flistUnknownPythonPackages(listRequirements, fbNameExists):
    """Return the requirements whose name the index does not know.

    Each returned entry is the requirement as the researcher wrote it,
    so the sentence built from it names what they must fix.
    """
    listUnknown = []
    for sRequirement in listRequirements or []:
        sName = fsPackageNameFromRequirement(sRequirement)
        if sName and not fbNameExists(sName):
            listUnknown.append(sRequirement.strip())
    return listUnknown


def fpreflightPythonPackageNames(config, fbNameExists=None):
    """Return a fail or not-checked result for the config's names, else None.

    None is the silent answer for a config whose every name the index
    knows, or that names no packages at all. The index probe is looked
    up at call time, never bound as a default, so a test that replaces
    it on the module reaches every caller and none touches pypi.org.
    """
    if fbNameExists is None:
        fbNameExists = fbNameExistsOnIndex
    listRequirements = getattr(config, "listPythonPackages", None) or []
    if not listRequirements:
        return None
    if fbIndexIsOverridden(getattr(config, "sPipInstallFlags", "")):
        return PreflightResult(
            sName=S_PREFLIGHT_NAME, sLevel=S_LEVEL_NOT_CHECKED,
            sScope=S_SCOPE_PROJECT,
            sMessage=(
                "pipInstallFlags names an index other than pypi.org, so "
                "the package names were not checked before the build."
            ),
        )
    try:
        listUnknown = flistUnknownPythonPackages(
            listRequirements, fbNameExists,
        )
    except IndexUnreachableError as errorIndex:
        return PreflightResult(
            sName=S_PREFLIGHT_NAME, sLevel=S_LEVEL_NOT_CHECKED,
            sScope=S_SCOPE_PROJECT,
            sMessage=(
                f"the package names were not checked before the build: "
                f"{errorIndex}"
            ),
        )
    if not listUnknown:
        return None
    sNames = ", ".join(f"'{sRequirement}'" for sRequirement in listUnknown)
    return PreflightResult(
        sName=S_PREFLIGHT_NAME, sLevel=S_LEVEL_FAIL, sScope=S_SCOPE_PROJECT,
        sMessage=(
            f"{sNames} under pythonPackages in vaibify.yml does not exist "
            "on the package index (pypi.org), so the build would stop at "
            "pip after the toolchain had already been installed."
        ),
        sRemediation="Fix the name in vaibify.yml, then build again.",
    )
