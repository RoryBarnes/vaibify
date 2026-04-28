"""Workflow-level lint that flags unseeded random-number-generation configs.

When a workflow declares a ``dictRandomnessLint`` block:

    {
        "sConfigGlob": "*.in",
        "sSeedRegex": "^seed\\s+\\d+"
    }

each step's referenced configuration files matching ``sConfigGlob`` are
scanned for ``sSeedRegex``. Files lacking a match flip the step's
``dictVerification["bUnseededRandomnessWarning"]`` flag, which the UI
surfaces as a yellow badge. Both fields are optional; an absent
``dictRandomnessLint`` is a no-op.
"""

__all__ = [
    "fbStepHasUnseededRandomness",
    "fnApplyRandomnessLintToWorkflow",
    "flistConfigFilesForStep",
]

import fnmatch
import logging
import posixpath
import re
import shlex


logger = logging.getLogger("vaibify")


def _flistCommandTokensForStep(dictStep):
    """Return all tokens from setup, data, and run commands for a step."""
    listTokens = []
    for sKey in ("saSetupCommands", "saDataCommands", "saCommands"):
        for sCommand in dictStep.get(sKey, []):
            try:
                listTokens.extend(shlex.split(sCommand))
            except ValueError:
                listTokens.extend(sCommand.split())
    return listTokens


def _fbTokenLooksLikePath(sToken, sConfigGlob):
    """Return True if sToken is path-shaped and matches the glob."""
    if sToken.startswith("-"):
        return False
    sBase = posixpath.basename(sToken)
    return fnmatch.fnmatch(sBase, sConfigGlob)


def flistConfigFilesForStep(dictStep, sStepDirectory, sConfigGlob):
    """Return container-absolute paths of step commands' matching files."""
    if not sConfigGlob:
        return []
    listMatches = []
    setSeen = set()
    for sToken in _flistCommandTokensForStep(dictStep):
        if not _fbTokenLooksLikePath(sToken, sConfigGlob):
            continue
        sPath = sToken if posixpath.isabs(sToken) else posixpath.join(
            sStepDirectory, sToken,
        )
        if sPath in setSeen:
            continue
        setSeen.add(sPath)
        listMatches.append(sPath)
    return listMatches


def _fbFileContainsRegex(sContent, sSeedRegex):
    """Return True if any line in sContent matches sSeedRegex.

    A malformed sSeedRegex returns False so the unseeded-randomness
    warning fires and the user sees a yellow badge prompting them to
    fix the lint configuration. Returning True would silently mask
    both the config error and any real unseeded randomness.
    """
    if not sContent or not sSeedRegex:
        return False
    try:
        regex = re.compile(sSeedRegex, re.MULTILINE)
    except re.error as error:
        logger.warning("Invalid sSeedRegex %r: %s", sSeedRegex, error)
        return False
    return regex.search(sContent) is not None


def fbStepHasUnseededRandomness(
    dictStep, sStepDirectory, dictRandomnessLint, fnReadFile,
):
    """Return True iff any referenced config file lacks the seed pattern.

    ``fnReadFile`` is a callable ``(sPath) -> str`` so the lint stays
    testable without a Docker connection. Returns ``False`` when the
    workflow declares no lint, declares no glob, or every matching file
    contains the seed regex.
    """
    if not dictRandomnessLint:
        return False
    sConfigGlob = dictRandomnessLint.get("sConfigGlob", "")
    sSeedRegex = dictRandomnessLint.get("sSeedRegex", "")
    if not sConfigGlob or not sSeedRegex:
        return False
    listConfigFiles = flistConfigFilesForStep(
        dictStep, sStepDirectory, sConfigGlob,
    )
    for sPath in listConfigFiles:
        sContent = fnReadFile(sPath)
        if not _fbFileContainsRegex(sContent, sSeedRegex):
            return True
    return False


def fnApplyRandomnessLintToWorkflow(dictWorkflow, fnReadFile):
    """Set ``bUnseededRandomnessWarning`` on each step per the lint."""
    dictRandomnessLint = dictWorkflow.get("dictRandomnessLint")
    sRepoRoot = dictWorkflow.get("sProjectRepoPath", "")
    for dictStep in dictWorkflow.get("listSteps", []):
        sDirectory = dictStep.get("sDirectory", "")
        if sDirectory and sRepoRoot and not posixpath.isabs(sDirectory):
            sDirectory = posixpath.join(sRepoRoot, sDirectory)
        bWarn = fbStepHasUnseededRandomness(
            dictStep, sDirectory, dictRandomnessLint, fnReadFile,
        )
        dictVerification = dictStep.setdefault("dictVerification", {})
        if bWarn:
            dictVerification["bUnseededRandomnessWarning"] = True
        else:
            dictVerification.pop("bUnseededRandomnessWarning", None)
