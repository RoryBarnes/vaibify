"""Lint a Dockerfile for AICS L3-grade reproducibility pinning.

Checks three orthogonal properties: every ``FROM`` line uses a
``@sha256:`` digest (not a floating tag); every ``apt-get install``
package is pinned to a specific version (or carries a
``# allow-unpinned`` opt-out marker on the same line); and a
``SOURCE_DATE_EPOCH`` value is set via ``ENV`` or ``ARG`` so build
artefacts are timestamp-deterministic.

Each helper returns a list of human-readable issue strings rather
than booleans so the dashboard can render an actionable per-line
gap list. The composition function ``flistLintDockerfile`` is what
``levelGates.fbVerifyDockerfilePinned`` consumes.
"""

import re
from pathlib import Path


__all__ = [
    "S_DOCKERFILE_FILENAME",
    "S_ALLOW_UNPINNED_MARKER",
    "flistLintDockerfile",
    "flistCheckBaseImageDigests",
    "flistCheckAptVersionPins",
    "flistCheckSourceDateEpoch",
    "fbDockerfilePresent",
]


S_DOCKERFILE_FILENAME = "Dockerfile"
S_ALLOW_UNPINNED_MARKER = "# allow-unpinned"

_REGEX_FROM = re.compile(r"^\s*FROM\s+(.+?)(?:\s+AS\s+\S+)?\s*$", re.IGNORECASE)
_REGEX_DIGEST = re.compile(r"@sha256:[0-9a-f]{64}\b")
_REGEX_APT_INSTALL = re.compile(
    r"apt(?:-get)?\s+install\b", re.IGNORECASE,
)
_REGEX_APT_PACKAGE_VERSIONED = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9.+\-]*=[^\s]+$",
)
_REGEX_APT_FLAG = re.compile(r"^-")
_REGEX_SDE = re.compile(
    r"^\s*(?:ENV|ARG)\s+SOURCE_DATE_EPOCH(?:\s|=)",
    re.IGNORECASE,
)


def fbDockerfilePresent(sProjectRepo):
    """Return True iff a Dockerfile exists at the project repo root."""
    return (Path(sProjectRepo) / S_DOCKERFILE_FILENAME).is_file()


def flistLintDockerfile(sProjectRepo):
    """Return a list of pin-and-determinism issues with the Dockerfile.

    Empty list means the Dockerfile satisfies all three L3 pinning
    requirements. A missing Dockerfile is reported as a single issue
    (the L3 gate treats absence as a failure, not as N/A).
    """
    pathDockerfile = Path(sProjectRepo) / S_DOCKERFILE_FILENAME
    if not pathDockerfile.is_file():
        return [f"Dockerfile not found at '{pathDockerfile}'"]
    listLines = pathDockerfile.read_text().splitlines()
    listIssues = []
    listIssues.extend(flistCheckBaseImageDigests(listLines))
    listIssues.extend(flistCheckAptVersionPins(listLines))
    listIssues.extend(flistCheckSourceDateEpoch(listLines))
    return listIssues


def flistCheckBaseImageDigests(listLines):
    """Return one issue per ``FROM`` line lacking a ``@sha256:`` digest."""
    listIssues = []
    for iIndex, sLine in enumerate(listLines, start=1):
        sStripped = _fsStripLineComment(sLine).strip()
        matchFrom = _REGEX_FROM.match(sStripped)
        if not matchFrom:
            continue
        sImage = matchFrom.group(1).strip()
        if sImage.lower() == "scratch":
            continue
        if _REGEX_DIGEST.search(sImage):
            continue
        listIssues.append(
            f"Line {iIndex}: base image '{sImage}' is not pinned by "
            "@sha256: digest"
        )
    return listIssues


def flistCheckAptVersionPins(listLines):
    """Return one issue per unpinned package in apt-get install lines.

    Treats a line continuation (``\\`` at end of line) as a single
    logical apt-install statement so multi-line installs are inspected
    as one block. A ``# allow-unpinned`` marker on the trailing
    comment of the logical line waives every package on that line.
    """
    listIssues = []
    for tLogical in _flistLogicalAptInstallLines(listLines):
        iIndex, sLogicalLine = tLogical
        if S_ALLOW_UNPINNED_MARKER in sLogicalLine:
            continue
        listIssues.extend(
            _flistFindUnpinnedAptPackages(iIndex, sLogicalLine)
        )
    return listIssues


def _flistLogicalAptInstallLines(listLines):
    """Return ``(start_line_number, joined_text)`` per apt-install block."""
    listLogical = []
    iCurrentStart = None
    listCurrentParts = []
    for iIndex, sLine in enumerate(listLines, start=1):
        if iCurrentStart is None:
            if _REGEX_APT_INSTALL.search(sLine):
                iCurrentStart = iIndex
                listCurrentParts = [sLine]
                if not sLine.rstrip().endswith("\\"):
                    listLogical.append(
                        (iCurrentStart, " ".join(listCurrentParts))
                    )
                    iCurrentStart = None
                    listCurrentParts = []
            continue
        listCurrentParts.append(sLine)
        if not sLine.rstrip().endswith("\\"):
            listLogical.append(
                (iCurrentStart, " ".join(listCurrentParts))
            )
            iCurrentStart = None
            listCurrentParts = []
    if iCurrentStart is not None:
        listLogical.append((iCurrentStart, " ".join(listCurrentParts)))
    return listLogical


def _flistFindUnpinnedAptPackages(iLine, sLogicalLine):
    """Return one issue per non-pinned package token on the logical line."""
    sPayload = _fsExtractAptPayload(sLogicalLine)
    listTokens = sPayload.split()
    listIssues = []
    for sToken in listTokens:
        if _REGEX_APT_FLAG.match(sToken):
            continue
        if _REGEX_APT_PACKAGE_VERSIONED.match(sToken):
            continue
        listIssues.append(
            f"Line {iLine}: apt package '{sToken}' is not pinned to a "
            "specific version (use 'pkg=ver' or append "
            f"'{S_ALLOW_UNPINNED_MARKER}')"
        )
    return listIssues


def _fsExtractAptPayload(sLogicalLine):
    """Return the token list after ``apt[-get] install`` minus comments."""
    sStripped = _fsStripLineComment(sLogicalLine)
    sStripped = sStripped.replace("\\", " ")
    matchInstall = _REGEX_APT_INSTALL.search(sStripped)
    if not matchInstall:
        return ""
    sAfter = sStripped[matchInstall.end():]
    return sAfter.replace("&&", " ").replace(";", " ")


def _fsStripLineComment(sLine):
    """Return sLine with the first ``#`` and trailing comment removed."""
    iHash = sLine.find("#")
    if iHash < 0:
        return sLine
    return sLine[:iHash]


def flistCheckSourceDateEpoch(listLines):
    """Return an issue list when ``SOURCE_DATE_EPOCH`` is unset.

    Either ``ENV SOURCE_DATE_EPOCH=...`` or ``ARG SOURCE_DATE_EPOCH=...``
    satisfies the requirement so projects that prefer build-arg
    parameterization (rebuild with ``--build-arg SOURCE_DATE_EPOCH=...``)
    are not penalized.
    """
    for sLine in listLines:
        if _REGEX_SDE.match(sLine):
            return []
    return [
        "SOURCE_DATE_EPOCH is not set via ENV or ARG; build "
        "timestamps will be non-deterministic"
    ]
