"""Lint a Dockerfile for PROOF L3-grade reproducibility pinning.

Checks three orthogonal properties: every ``FROM`` line uses a
``@sha256:`` digest (not a floating tag); every ``apt-get install``
package is pinned to a specific version (or carries a
``# allow-unpinned`` opt-out marker on the same line); and a
``SOURCE_DATE_EPOCH`` value is set via ``ENV`` or ``ARG`` so build
artefacts are timestamp-deterministic.

Two forms are NOT external base images and are exempt from the digest
rule: ``scratch``, and a reference to a stage this same file declared
earlier with ``AS``. A multi-stage file's later ``FROM`` lines name
build products, which have no digest to pin and need none -- the
pinning that matters already happened at the stage they descend from.
That exemption is what makes a composed image chain (vaibify's base
plus its feature overlays, emitted as one multi-stage file) lintable
at all.

Each helper returns a list of human-readable issue strings rather
than booleans so the dashboard can render an actionable per-line
gap list. The composition function ``flistLintDockerfile`` is what
``levelGates.fbVerifyDockerfilePinned`` consumes.
"""

import os
import re

from vaibify.reproducibility.repoFiles import (
    ffilesEnsureRepoFiles,
    fsRepoRootOf,
)


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
_REGEX_FROM_STAGE_NAME = re.compile(
    r"^\s*FROM\s+.+?\s+AS\s+(\S+)\s*$", re.IGNORECASE,
)
_REGEX_DIGEST = re.compile(r"@sha256:[0-9a-f]{64}\b")
_REGEX_APT_INSTALL = re.compile(
    r"apt(?:-get)?\s+install\b", re.IGNORECASE,
)
_REGEX_APT_PACKAGE_VERSIONED = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9.+\-]*=[^\s]+$",
)
_REGEX_APT_FLAG = re.compile(r"^-")
_REGEX_ARG_DEFAULT = re.compile(
    r"^\s*ARG\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+?)\s*$",
    re.IGNORECASE,
)
_REGEX_ARG_REFERENCE = re.compile(
    r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)",
)
_REGEX_SDE = re.compile(
    r"^\s*(?:ENV|ARG)\s+SOURCE_DATE_EPOCH(?:\s|=)",
    re.IGNORECASE,
)


def fbDockerfilePresent(filesRepo):
    """Return True iff a Dockerfile exists at the project repo root."""
    return ffilesEnsureRepoFiles(filesRepo).fbIsFile(S_DOCKERFILE_FILENAME)


def flistLintDockerfile(filesRepo):
    """Return a list of pin-and-determinism issues with the Dockerfile.

    Empty list means the Dockerfile satisfies all three L3 pinning
    requirements. A missing Dockerfile is reported as a single issue
    (the L3 gate treats absence as a failure, not as N/A).
    """
    filesRepo = ffilesEnsureRepoFiles(filesRepo)
    if not filesRepo.fbIsFile(S_DOCKERFILE_FILENAME):
        sDisplayPath = os.path.join(
            fsRepoRootOf(filesRepo), S_DOCKERFILE_FILENAME,
        )
        return [f"Dockerfile not found at '{sDisplayPath}'"]
    listLines = filesRepo.fsReadText(S_DOCKERFILE_FILENAME).splitlines()
    listIssues = []
    listIssues.extend(flistCheckBaseImageDigests(listLines))
    listIssues.extend(flistCheckAptVersionPins(listLines))
    listIssues.extend(flistCheckSourceDateEpoch(listLines))
    return listIssues


def flistCheckBaseImageDigests(listLines):
    """Return one issue per ``FROM`` line lacking a ``@sha256:`` digest.

    ``FROM ${VAR}`` is resolved against the ARG defaults declared
    above it before the digest is looked for. The parameterised form
    is how a Dockerfile is written when the base is meant to be
    overridable, and vaibify's own image uses it -- with a digest in
    the default. Judging the literal ``FROM`` text called that
    unpinned, which is false: an unspecified build gets the default,
    and the default is a digest.

    An ARG with NO default stays an issue. It resolves to whatever
    the builder passed, which the file does not record, so nothing
    here can vouch for it.
    """
    dictArgDefaults = _fdictCollectArgDefaults(listLines)
    setStageNames = set()
    listIssues = []
    for iIndex, sLine in enumerate(listLines, start=1):
        sStripped = _fsStripLineComment(sLine).strip()
        matchFrom = _REGEX_FROM.match(sStripped)
        if not matchFrom:
            continue
        sImage = matchFrom.group(1).strip()
        matchStage = _REGEX_FROM_STAGE_NAME.match(sStripped)
        if sImage.lower() == "scratch" or sImage.lower() in setStageNames:
            _fnRecordStageName(matchStage, setStageNames)
            continue
        sResolved = _fsResolveArgReferences(sImage, dictArgDefaults)
        if not _REGEX_DIGEST.search(sResolved):
            listIssues.append(
                f"Line {iIndex}: base image '{sImage}' is not pinned by "
                "@sha256: digest"
            )
        _fnRecordStageName(matchStage, setStageNames)
    return listIssues


def _fnRecordStageName(matchStage, setStageNames):
    """Remember a ``FROM ... AS <name>`` stage name, lower-cased."""
    if matchStage:
        setStageNames.add(matchStage.group(1).strip().lower())


def _fdictCollectArgDefaults(listLines):
    """Return ``{sArgName: sDefault}`` for every ARG carrying a default."""
    dictDefaults = {}
    for sLine in listLines:
        matchArg = _REGEX_ARG_DEFAULT.match(
            _fsStripLineComment(sLine).strip(),
        )
        if matchArg:
            dictDefaults[matchArg.group(1)] = matchArg.group(2).strip()
    return dictDefaults


def _fsResolveArgReferences(sImage, dictArgDefaults):
    """Substitute ``${NAME}`` / ``$NAME`` with their declared defaults.

    One pass, not a fixed point: a default that is itself a reference
    is left unresolved and so reported, which is the safe direction --
    this function may only ever turn an issue into a non-issue when it
    can see a literal digest.
    """
    def _fsReplace(matchReference):
        sName = matchReference.group(1) or matchReference.group(2)
        return dictArgDefaults.get(sName, matchReference.group(0))

    return _REGEX_ARG_REFERENCE.sub(_fsReplace, sImage)


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


def _fbLineContinues(sLine):
    """Return True iff sLine ends with a backslash continuation."""
    return sLine.rstrip().endswith("\\")


def _fnFinalizeAptBlock(listLogical, iStart, listParts):
    """Append the joined apt-install block (iStart, joined text) to listLogical."""
    listLogical.append((iStart, " ".join(listParts)))


def _flistLogicalAptInstallLines(listLines):
    """Return ``(start_line_number, joined_text)`` per apt-install block."""
    listLogical = []
    iCurrentStart = None
    listCurrentParts = []
    for iIndex, sLine in enumerate(listLines, start=1):
        if iCurrentStart is None:
            if not _REGEX_APT_INSTALL.search(sLine):
                continue
            iCurrentStart = iIndex
            listCurrentParts = [sLine]
        else:
            listCurrentParts.append(sLine)
        if not _fbLineContinues(sLine):
            _fnFinalizeAptBlock(listLogical, iCurrentStart, listCurrentParts)
            iCurrentStart = None
            listCurrentParts = []
    if iCurrentStart is not None:
        _fnFinalizeAptBlock(listLogical, iCurrentStart, listCurrentParts)
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
    """Return the package tokens of the ``apt[-get] install`` statement.

    TRUNCATES at the first shell separator rather than deleting the
    separator and keeping what follows. The install statement ends
    there; everything after it is a different command, and reading it
    as a package list turns ``&& rm -rf /var/lib/apt/lists/*`` into a
    complaint that ``rm`` is unpinned.

    That bug was invisible for as long as every apt block in the image
    either ended at the package list or carried an ``allow-unpinned``
    marker, because the marker short-circuits this function entirely.
    The first block to be pinned AND followed by shell (the compiler
    toolchain, with its failure diagnostic) reported 296 issues, one
    per word of the error message.
    """
    sStripped = _fsStripLineComment(sLogicalLine)
    sStripped = sStripped.replace("\\", " ")
    matchInstall = _REGEX_APT_INSTALL.search(sStripped)
    if not matchInstall:
        return ""
    return _fsTruncateAtShellSeparator(
        sStripped[matchInstall.end():],
    )


def _fsTruncateAtShellSeparator(sAfterInstall):
    """Return sAfterInstall up to the first ``&&``, ``||``, ``|`` or ``;``."""
    iCut = len(sAfterInstall)
    for sSeparator in ("&&", "||", "|", ";"):
        iFound = sAfterInstall.find(sSeparator)
        if 0 <= iFound < iCut:
            iCut = iFound
    return sAfterInstall[:iCut]


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
