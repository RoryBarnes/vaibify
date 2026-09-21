"""Ask Ubuntu's archive whether every ``systemPackages`` name exists.

The same failure as a misspelled pip package, one layer down: a name
Ubuntu does not publish stops the build at ``apt-get install`` after
the base image and the pinned toolchain have been fetched. Launchpad
answers the question for a published binary in about a second, so it
is asked before the build rather than discovered during one.

The answer is SERIES-SPECIFIC, so the series is derived from the
project's own base image and an unrecognised base image reports NOT
CHECKED rather than being graded against a series it does not run.
"""

import json
import re
import urllib.error
import urllib.parse
import urllib.request

from .preflightResult import (
    PreflightResult, S_LEVEL_FAIL, S_LEVEL_NOT_CHECKED, S_SCOPE_PROJECT,
)


__all__ = [
    "ArchiveUnreachableError",
    "S_PREFLIGHT_NAME",
    "fbPackageExistsInArchive",
    "flistUnknownSystemPackages",
    "fpreflightSystemPackageNames",
    "fsSeriesForBaseImage",
]


S_PREFLIGHT_NAME = "system-package-names"
S_LAUNCHPAD_ARCHIVE = (
    "https://api.launchpad.net/1.0/ubuntu/+archive/primary"
)
S_DISTRO_ARCH_SERIES = "https://api.launchpad.net/1.0/ubuntu/{}/amd64"
F_ARCHIVE_TIMEOUT_SECONDS = 10.0

# The Ubuntu releases vaibify's own base images name. A base image
# outside this table is not graded: its archive is a different one.
_DICT_SERIES_BY_VERSION = {
    "24.04": "noble", "22.04": "jammy", "20.04": "focal",
}
_REGEX_UBUNTU_BASE_IMAGE = re.compile(
    r"^(?:docker\.io/)?(?:library/)?ubuntu:(\d+\.\d+)(?:@sha256:[0-9a-f]+)?$",
)
# An apt name may carry an architecture suffix or a version pin; the
# archive is asked about the package, as apt would resolve it.
_REGEX_APT_PACKAGE_NAME = re.compile(r"^([a-z0-9][a-z0-9+.-]*)")


class ArchiveUnreachableError(Exception):
    """The archive gave no answer about a name; nothing is known."""


def fsSeriesForBaseImage(sBaseImage):
    """Return the Ubuntu series a base image names, or '' when unknown."""
    matchImage = _REGEX_UBUNTU_BASE_IMAGE.match((sBaseImage or "").strip())
    if matchImage is None:
        return ""
    return _DICT_SERIES_BY_VERSION.get(matchImage.group(1), "")


def _fsPackageNameFromEntry(sEntry):
    """Return the package name apt would look up, or '' for a non-name."""
    sStripped = (sEntry or "").strip().split("=", 1)[0].split(":", 1)[0]
    matchName = _REGEX_APT_PACKAGE_NAME.match(sStripped.lower())
    return matchName.group(1) if matchName else ""


def fbPackageExistsInArchive(sName, sSeries):
    """Return whether Launchpad publishes a binary of this name in a series.

    Any answer other than a well-formed listing is no answer, and is
    raised rather than read as absence.
    """
    sQuery = urllib.parse.urlencode({
        "ws.op": "getPublishedBinaries",
        "binary_name": sName,
        "exact_match": "true",
        "distro_arch_series": S_DISTRO_ARCH_SERIES.format(sSeries),
        "status": "Published",
    })
    try:
        with urllib.request.urlopen(
            f"{S_LAUNCHPAD_ARCHIVE}?{sQuery}",
            timeout=F_ARCHIVE_TIMEOUT_SECONDS,
        ) as responseArchive:
            dictAnswer = json.loads(responseArchive.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as errorArchive:
        raise ArchiveUnreachableError(
            f"Launchpad could not be asked about {sName!r}: {errorArchive}",
        ) from errorArchive
    iTotal = dictAnswer.get("total_size")
    if iTotal is None:
        raise ArchiveUnreachableError(
            f"Launchpad's answer about {sName!r} carried no total_size",
        )
    return iTotal > 0


def flistUnknownSystemPackages(listPackages, sSeries, fbPackageExists):
    """Return the entries whose package name the archive does not publish."""
    listUnknown = []
    for sEntry in listPackages or []:
        sName = _fsPackageNameFromEntry(sEntry)
        if sName and not fbPackageExists(sName, sSeries):
            listUnknown.append(sEntry.strip())
    return listUnknown


def fpreflightSystemPackageNames(config, fbPackageExists=None):
    """Return a fail or not-checked result for the config's apt names, else None.

    The probe is looked up at call time, never bound as a default, so a
    test that replaces it on the module reaches every caller and none
    touches Launchpad.
    """
    if fbPackageExists is None:
        fbPackageExists = fbPackageExistsInArchive
    listPackages = getattr(config, "listSystemPackages", None) or []
    if not listPackages:
        return None
    sSeries = fsSeriesForBaseImage(getattr(config, "sBaseImage", ""))
    if not sSeries:
        return PreflightResult(
            sName=S_PREFLIGHT_NAME, sLevel=S_LEVEL_NOT_CHECKED,
            sScope=S_SCOPE_PROJECT,
            sMessage=(
                "baseImage in vaibify.yml is not an Ubuntu release vaibify "
                "knows, so the systemPackages names were not checked "
                "against its archive."
            ),
        )
    try:
        listUnknown = flistUnknownSystemPackages(
            listPackages, sSeries, fbPackageExists,
        )
    except ArchiveUnreachableError as errorArchive:
        return PreflightResult(
            sName=S_PREFLIGHT_NAME, sLevel=S_LEVEL_NOT_CHECKED,
            sScope=S_SCOPE_PROJECT,
            sMessage=(
                "the systemPackages names were not checked before the "
                f"build: {errorArchive}"
            ),
        )
    if not listUnknown:
        return None
    sNames = ", ".join(f"'{sEntry}'" for sEntry in listUnknown)
    return PreflightResult(
        sName=S_PREFLIGHT_NAME, sLevel=S_LEVEL_FAIL, sScope=S_SCOPE_PROJECT,
        sMessage=(
            f"{sNames} under systemPackages in vaibify.yml is not a package "
            f"Ubuntu publishes for {sSeries}, so the build would stop at "
            "apt-get after the base image had been fetched."
        ),
        sRemediation="Fix the name in vaibify.yml, then build again.",
    )
