"""Which Dockerfiles need BuildKit, and the refusal when buildx is absent.

Without docker buildx a build runs on Docker's legacy builder, which
builds every ordinary vaibify image. Measured with ``DOCKER_BUILDKIT=0``
on Docker 28.4 (2026-10-26), it meets BuildKit syntax in one of two
ways:

* it refuses: an automatic platform argument in ``FROM --platform``
  expands to nothing ("failed to parse platform : \"\" is an invalid OS
  component"), and ``RUN --mount``, ``RUN --network``, ``COPY --chmod``
  and ``COPY --parents`` are rejected;
* it builds something else, silently: an automatic platform argument
  anywhere else is empty, a heredoc's body never runs, and
  ``ADD --checksum`` is not checked at all.

Either is a reason to refuse before the build starts, in a sentence
that names the remedy, rather than after it in Docker's words or never.
``COPY --link`` changes only how layers are reused, so it is not one.
The scan reads instructions, never comments, so a Dockerfile that only
MENTIONS ``BUILDPLATFORM`` in prose needs nothing.
"""

import os
import re


__all__ = [
    "S_FAILURE_BUILDX_MISSING",
    "BuildKitRequiredError",
    "flistDescribeBuildKitSyntax",
    "flistDescribeBuildKitSyntaxInFile",
]


S_FAILURE_BUILDX_MISSING = "buildx-missing"

_RE_AUTOMATIC_PLATFORM_ARGUMENT = re.compile(
    r"\$\{?((?:BUILD|TARGET)(?:PLATFORM|OS|ARCH|VARIANT))\b")
_RE_BUILDKIT_FLAG = re.compile(
    r"^(RUN|COPY|ADD)\s+(?:--\S+\s+)*?--("
    r"mount|network|security|chmod|parents|exclude|checksum|keep-git-dir"
    r")\b", re.IGNORECASE)
# ``<<WORD`` opens a heredoc; ``<<<`` is the shell's here-string.
_RE_HEREDOC = re.compile(
    r"^(RUN|COPY|ADD)\b.*(?<!<)<<(?!<)-?\s*[\"']?[A-Za-z_]",
    re.IGNORECASE)


class BuildKitRequiredError(RuntimeError):
    """A build the legacy builder cannot do, refused before it starts.

    ``dictFailure`` is the shape the acquisition window reads, so the
    researcher sees this sentence wherever the build was asked for.
    """

    def __init__(self, sDockerfile, listSyntax, sInstall):
        sSentence = (
            f"{os.path.basename(sDockerfile)} needs docker buildx, which "
            "is not installed on this machine: Docker's legacy builder "
            f"cannot build it as written ({listSyntax[0]}). Install "
            f"buildx ({sInstall}, or run `vaibify setup-docker`), then "
            "try again."
        )
        super().__init__(sSentence)
        self.dictFailure = {
            "sFailureClass": S_FAILURE_BUILDX_MISSING,
            "sSentence": sSentence,
        }


def _ftJoinInstruction(listPending):
    """Return ``(iLineNumber, sFirstLine, sLogicalText)`` for one instruction."""
    iFirstNumber, sFirstLine = listPending[0]
    sLogical = " ".join(sLine.rstrip("\\") for _, sLine in listPending)
    return (iFirstNumber, sFirstLine, sLogical)


def _flistInstructionLines(sDockerfileText):
    """Return ``(iLineNumber, sFirstLine, sLogicalText)`` per instruction.

    A comment line is dropped wherever it falls, as Docker drops it,
    and a backslash joins an instruction's lines into its logical text.
    """
    listInstructions = []
    listPending = []
    for iNumber, sRawLine in enumerate(sDockerfileText.splitlines(), 1):
        sLine = sRawLine.strip()
        if sLine.startswith("#") or (not sLine and not listPending):
            continue
        listPending.append((iNumber, sLine))
        if not sLine.endswith("\\"):
            listInstructions.append(_ftJoinInstruction(listPending))
            listPending = []
    if listPending:
        listInstructions.append(_ftJoinInstruction(listPending))
    return listInstructions


def flistDescribeBuildKitSyntax(sDockerfileText):
    """Return one phrase per instruction that needs BuildKit; empty if none."""
    listFound = []
    for iNumber, sFirstLine, sLogical in _flistInstructionLines(
            sDockerfileText):
        matchArgument = _RE_AUTOMATIC_PLATFORM_ARGUMENT.search(sLogical)
        matchFlag = _RE_BUILDKIT_FLAG.match(sLogical)
        if matchArgument:
            listFound.append(f"line {iNumber} uses ${matchArgument.group(1)}")
        elif matchFlag:
            listFound.append(
                f"line {iNumber} uses {matchFlag.group(1).upper()} "
                f"--{matchFlag.group(2).lower()}")
        elif _RE_HEREDOC.match(sFirstLine):
            listFound.append(f"line {iNumber} uses a heredoc")
    return listFound


def flistDescribeBuildKitSyntaxInFile(sDockerfile):
    """Read a Dockerfile and describe its BuildKit syntax; empty if none.

    An unreadable file describes nothing here: the build that follows
    names the missing file in Docker's own words.
    """
    try:
        with open(sDockerfile, encoding="utf-8") as fileDockerfile:
            return flistDescribeBuildKitSyntax(fileDockerfile.read())
    except (OSError, UnicodeDecodeError):
        return []
