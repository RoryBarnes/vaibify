"""Is the AI Declaration's sign-off still about the work as it stands?

A researcher signs off the AI Declaration once they have read it
against the project. Any later change to ANOTHER step's scripts,
outputs (data and plots) or declared input data means the declaration
may no longer describe the work, so the sign-off becomes STALE and
Level 2 is blocked until they sign off again. Edits to the declaration
file itself are not a trigger.

Three pieces, each with one home:

1. ``fdictBuildDeclarationBaseline`` — called ONLY by the server's
   sign-off request. It records the covered file set and each file's
   SHA-256 in the declaration step's ``dictVerification``; nothing
   else ever writes it, so a change made between signing and the first
   poll is never adopted as signed.
2. ``fdictEvaluateDeclarationFreshness`` — THE evaluator. Pure: it
   compares the baseline with the files as they are now and answers
   ``fresh``, ``stale``, ``unknown`` (the evidence could not be read)
   or ``untracked`` (a sign-off from before baselines existed, which
   stays passed and says so). The poll, the readiness payload, the
   Level 2 gate and the dashboard all read this one answer.
3. ``fbLatchStaleDeclaration`` — makes stale STICKY. The poll calls it
   with the evaluator's answer and persists the result: once a change
   has been seen, ``sUser`` reads ``stale`` and only a new sign-off
   clears it, even if the file is changed back.

The stated limitation follows from comparing snapshots: a change that
is made and completely reverted between two checks is not detected.
"""

import posixpath
import shlex

from vaibify.config.mutationAdmission import fnReRaiseControlPlaneRefusal
from vaibify.reproducibility.aiDeclarationStep import fbStepIsAiDeclaration
from vaibify.reproducibility.manifestPaths import (
    TUPLE_COMMAND_KEYS,
    fdictWorkflowTemplateValues,
    flistStepInputRepoPaths,
    flistStepOutputRepoPaths,
    fsResolveStepPathToRepoPath,
)
from vaibify.reproducibility.repoFiles import ffilesEnsureRepoFiles


__all__ = [
    "S_BASELINE_KEY",
    "S_CHANGES_KEY",
    "S_FRESHNESS_FRESH",
    "S_FRESHNESS_STALE",
    "S_FRESHNESS_UNKNOWN",
    "S_FRESHNESS_UNTRACKED",
    "S_FRESHNESS_UNSIGNED",
    "DeclarationEvidenceUnreadableError",
    "fbDeclarationFreshnessPasses",
    "fbLatchStaleDeclaration",
    "fdictBuildDeclarationBaseline",
    "fdictEvaluateDeclarationFreshness",
    "fdictCoveredFilesByPath",
    "fnApplySignOffBaseline",
    "flistPathsToHashForFreshness",
    "fnClearDeclarationBaseline",
]


S_BASELINE_KEY = "dictDeclarationBaseline"
S_CHANGES_KEY = "listDeclarationChanges"

S_FRESHNESS_FRESH = "fresh"
S_FRESHNESS_STALE = "stale"
S_FRESHNESS_UNKNOWN = "unknown"
S_FRESHNESS_UNTRACKED = "untracked"
S_FRESHNESS_UNSIGNED = "unsigned"

_SET_PASSING_VERDICTS = frozenset({S_FRESHNESS_FRESH, S_FRESHNESS_UNTRACKED})

S_KIND_SCRIPTS = "scripts"
S_KIND_OUTPUTS = "outputs"
S_KIND_INPUTS = "input data"

S_UNTRACKED_NOTE = (
    "Signed before vaibify recorded what the sign-off covered; changes "
    "are tracked from your next sign-off."
)


class DeclarationEvidenceUnreadableError(Exception):
    """A covered file could not be read, so no baseline can be recorded."""


def _fbPathIsRepoRelative(sPath):
    """Return True for a path that names a file inside the repository."""
    if not sPath or sPath.startswith("/"):
        return False
    return ".." not in posixpath.normpath(sPath).split("/")


def _fsScriptTokenOfCommand(sCommand):
    """Return the script file a command runs, in any language, or "".

    The command is split the way a shell splits it, so a quoted path
    with a space is one token. The interpreter table and the flag
    handling are the dashboard's own (``commandUtilities``), so a
    ``bash run.sh``, ``Rscript fit.R`` or ``julia sim.jl`` step is
    covered exactly as a Python one is; inline code (``-c``, ``-e``)
    and a module (``-m``) name no file.
    """
    from vaibify.gui.commandUtilities import (
        DICT_COMMAND_PREFIXES, DICT_EXTENSION_TO_LANGUAGE,
        fsExtractScriptPathFromArguments,
    )
    try:
        listTokens = shlex.split(sCommand)
    except ValueError:
        listTokens = sCommand.split()
    if not listTokens:
        return ""
    sInterpreter = posixpath.basename(listTokens[0])
    if sInterpreter in DICT_COMMAND_PREFIXES:
        listArguments = listTokens[1:]
        if DICT_COMMAND_PREFIXES[sInterpreter] == "shell":
            # For a shell, -e is errexit, not inline code as it is for
            # perl, node or ruby; only -c names no file.
            listArguments = [s for s in listArguments if s != "-e"]
        return fsExtractScriptPathFromArguments(listArguments)
    for sToken in listTokens[:2]:
        if posixpath.splitext(sToken)[1] in DICT_EXTENSION_TO_LANGUAGE:
            return sToken
    return ""


def _flistStepScriptPaths(dictStep):
    """Return repo-relative paths of every script a step's commands run."""
    sDirectory = dictStep.get("sDirectory", "") or ""
    listPaths = []
    for sKey in TUPLE_COMMAND_KEYS:
        for sCommand in dictStep.get(sKey, []) or []:
            sScript = _fsScriptTokenOfCommand(str(sCommand))
            if sScript and "{" not in sScript:
                listPaths.append(
                    fsResolveStepPathToRepoPath(sScript, sDirectory))
    return listPaths


def _flistPathsByKind(dictStep, dictTemplateValues):
    """Return ``[(sKind, sPath)]`` for one step's covered files."""
    return (
        [(S_KIND_SCRIPTS, sPath)
         for sPath in _flistStepScriptPaths(dictStep)]
        + [(S_KIND_OUTPUTS, sPath)
           for sPath in flistStepOutputRepoPaths(
               dictStep, dictTemplateValues)]
        + [(S_KIND_INPUTS, sPath)
           for sPath in flistStepInputRepoPaths(dictStep)]
    )


def fdictCoveredFilesByPath(dictWorkflow):
    """Return ``{sPath: {"sStepId", "sKind"}}`` for every covered file.

    Covered means: every step OTHER than the declaration, its scripts,
    its declared outputs (data and plots) and its declared input data.
    A path two steps share is attributed to the first that declares it.
    Paths that leave the repository are not covered; the manifest
    writer refuses them loudly elsewhere.
    """
    dictTemplateValues = fdictWorkflowTemplateValues(dictWorkflow)
    dictCovered = {}
    for dictStep in (dictWorkflow or {}).get("listSteps", []) or []:
        if not isinstance(dictStep, dict) or fbStepIsAiDeclaration(dictStep):
            continue
        sStepId = dictStep.get("sStepId") or dictStep.get("sDirectory", "")
        for sKind, sPath in _flistPathsByKind(dictStep, dictTemplateValues):
            if _fbPathIsRepoRelative(sPath) and sPath not in dictCovered:
                dictCovered[sPath] = {"sStepId": sStepId, "sKind": sKind}
    return dictCovered


def _ftHashOrUnreadable(dictEntry):
    """Return ``(sHash, bUnreadable)``; an absent file hashes to ``""``."""
    dictEntry = dictEntry or {}
    if dictEntry.get("sSha256"):
        return dictEntry["sSha256"], False
    if dictEntry.get("bMissing"):
        return "", False
    return None, True


def _ftHashCoveredFiles(filesRepo, listPaths):
    """Return ``(dictHashes, listUnreadable)`` for the paths given.

    An absent file is recorded as ``""`` — absent is an answer, and a
    file that later appears is a change. A file that exists but could
    not be read is UNREADABLE, never absent.
    """
    if not listPaths:
        return {}, []
    try:
        dictEntries = ffilesEnsureRepoFiles(filesRepo).fdictHashFiles(
            list(listPaths))
    except (OSError, KeyError, ValueError) as error:
        fnReRaiseControlPlaneRefusal(error)
        return {}, [f"{len(listPaths)} files ({error})"]
    dictHashes = {}
    listUnreadable = []
    for sPath in listPaths:
        sHash, bUnreadable = _ftHashOrUnreadable(dictEntries.get(sPath))
        if bUnreadable:
            listUnreadable.append(sPath)
        else:
            dictHashes[sPath] = sHash
    return dictHashes, listUnreadable


def fdictBuildDeclarationBaseline(dictWorkflow, filesRepo, sSignedAtUtc):
    """Return the baseline a sign-off records, or raise if it cannot.

    Raises :class:`DeclarationEvidenceUnreadableError` naming the files
    it could not read: a sign-off whose coverage cannot be recorded
    would later read as fresh against a baseline that omits them.
    """
    dictCovered = fdictCoveredFilesByPath(dictWorkflow)
    dictHashes, listUnreadable = _ftHashCoveredFiles(
        filesRepo, sorted(dictCovered))
    if listUnreadable:
        raise DeclarationEvidenceUnreadableError(
            "Could not read " + ", ".join(listUnreadable[:5])
            + (" and others" if len(listUnreadable) > 5 else ""))
    return {
        "sSignedAtUtc": sSignedAtUtc,
        "dictCoveredFiles": {
            sPath: dict(dictCovered[sPath], sSha256=dictHashes[sPath])
            for sPath in sorted(dictCovered)
        },
    }


def fnApplySignOffBaseline(
    filesRepo, dictWorkflow, iStepIndex, dictVerificationUpdate,
):
    """Apply a verification update's baseline rules, in place.

    The baseline and the latched changes are never taken from the
    update: a supplied value is discarded and the stored one kept. When
    ``sUser`` becomes ``passed`` on the declaration step, the covered
    files are hashed NOW and become the new baseline; any other change
    of ``sUser`` drops it. Raises
    :class:`DeclarationEvidenceUnreadableError` when the coverage
    cannot be read, so the caller refuses the sign-off.
    """
    listSteps = (dictWorkflow or {}).get("listSteps", []) or []
    if not isinstance(dictVerificationUpdate, dict) or not (
            0 <= iStepIndex < len(listSteps)):
        return
    dictStored = listSteps[iStepIndex].get("dictVerification") or {}
    for sKey in (S_BASELINE_KEY, S_CHANGES_KEY):
        dictVerificationUpdate.pop(sKey, None)
        if sKey in dictStored:
            dictVerificationUpdate[sKey] = dictStored[sKey]
    if not fbStepIsAiDeclaration(listSteps[iStepIndex]) or (
            dictVerificationUpdate.get("sUser") == dictStored.get("sUser")):
        return
    fnClearDeclarationBaseline(dictVerificationUpdate)
    if dictVerificationUpdate.get("sUser") == "passed":
        dictVerificationUpdate[S_BASELINE_KEY] = (
            fdictBuildDeclarationBaseline(
                dictWorkflow, filesRepo,
                dictVerificationUpdate.get("sLastUserUpdate", "")))


def _fdictFindDeclarationStep(dictWorkflow):
    for dictStep in (dictWorkflow or {}).get("listSteps", []) or []:
        if fbStepIsAiDeclaration(dictStep):
            return dictStep
    return None


def _fsChangeKindOf(dictBefore, sBeforeHash, sNowHash):
    if dictBefore is None:
        return "added"
    if sNowHash is None:
        return "no longer covered"
    if sNowHash == "":
        return "deleted"
    if sBeforeHash == "":
        return "created"
    return "changed"


def _flistCompareWithBaseline(dictBaselineFiles, dictCovered, dictNow):
    """Return one change record per covered file that differs."""
    listChanges = []
    for sPath in sorted(set(dictBaselineFiles) | set(dictCovered)):
        dictBefore = dictBaselineFiles.get(sPath)
        sBeforeHash = (dictBefore or {}).get("sSha256")
        sNowHash = dictNow.get(sPath) if sPath in dictCovered else None
        if dictBefore is not None and sNowHash == sBeforeHash:
            continue
        dictOwner = dictCovered.get(sPath) or dictBefore or {}
        listChanges.append({
            "sPath": sPath,
            "sStepId": dictOwner.get("sStepId", ""),
            "sKind": dictOwner.get("sKind", ""),
            "sChange": _fsChangeKindOf(dictBefore, sBeforeHash, sNowHash),
        })
    return listChanges


def _fdictVerdict(sVerdict, listChanges=None, sReason=""):
    return {
        "sVerdict": sVerdict,
        "listChanges": list(listChanges or []),
        "sReason": sReason,
    }


def _fdictEvaluateAgainstBaseline(dictWorkflow, dictBaseline, filesRepo):
    dictBaselineFiles = dictBaseline.get("dictCoveredFiles") or {}
    dictCovered = fdictCoveredFilesByPath(dictWorkflow)
    dictNow, listUnreadable = _ftHashCoveredFiles(
        filesRepo, sorted(dictCovered))
    if listUnreadable:
        return _fdictVerdict(
            S_FRESHNESS_UNKNOWN,
            sReason="Could not read " + ", ".join(listUnreadable[:5])
            + (" and others" if len(listUnreadable) > 5 else "")
            + ", so vaibify cannot tell whether the work changed since "
            "the AI Declaration was signed off.")
    listChanges = _flistCompareWithBaseline(
        dictBaselineFiles, dictCovered, dictNow)
    if listChanges:
        return _fdictVerdict(S_FRESHNESS_STALE, listChanges)
    return _fdictVerdict(S_FRESHNESS_FRESH)


def fdictEvaluateDeclarationFreshness(dictWorkflow, filesRepo):
    """Return ``{sVerdict, listChanges, sReason}`` for the sign-off.

    Pure: it reads the workflow and the files and writes nothing. A
    sign-off already latched stale stays stale with the changes that
    latched it, whatever the files say now.
    """
    dictStep = _fdictFindDeclarationStep(dictWorkflow)
    dictVerification = (dictStep or {}).get("dictVerification") or {}
    sUser = dictVerification.get("sUser")
    if sUser == "stale":
        return _fdictVerdict(
            S_FRESHNESS_STALE, dictVerification.get(S_CHANGES_KEY))
    if sUser != "passed":
        return _fdictVerdict(S_FRESHNESS_UNSIGNED)
    dictBaseline = dictVerification.get(S_BASELINE_KEY)
    if not isinstance(dictBaseline, dict):
        return _fdictVerdict(S_FRESHNESS_UNTRACKED, sReason=S_UNTRACKED_NOTE)
    return _fdictEvaluateAgainstBaseline(dictWorkflow, dictBaseline, filesRepo)


def flistPathsToHashForFreshness(dictWorkflow):
    """Return the covered paths a poll must hash, or [] when none is due.

    Only a sign-off with a baseline is compared against files, so a
    project that has not signed costs the poll nothing extra.
    """
    dictStep = _fdictFindDeclarationStep(dictWorkflow)
    dictVerification = (dictStep or {}).get("dictVerification") or {}
    if dictVerification.get("sUser") != "passed" or not isinstance(
            dictVerification.get(S_BASELINE_KEY), dict):
        return []
    return sorted(fdictCoveredFilesByPath(dictWorkflow))


def fbDeclarationFreshnessPasses(dictVerdict):
    """Return True iff the verdict lets a signed declaration count."""
    return (dictVerdict or {}).get("sVerdict") in _SET_PASSING_VERDICTS


def fbLatchStaleDeclaration(dictWorkflow, dictVerdict):
    """Make a newly seen stale verdict sticky; return True iff it mutated.

    Only a ``passed`` sign-off is latched, so a researcher's own
    ``failed`` or ``untested`` is never overwritten.
    """
    if (dictVerdict or {}).get("sVerdict") != S_FRESHNESS_STALE:
        return False
    dictStep = _fdictFindDeclarationStep(dictWorkflow)
    if dictStep is None:
        return False
    dictVerification = dictStep.setdefault("dictVerification", {})
    if dictVerification.get("sUser") != "passed":
        return False
    dictVerification["sUser"] = "stale"
    dictVerification[S_CHANGES_KEY] = list(dictVerdict["listChanges"])
    return True


def fnClearDeclarationBaseline(dictVerification):
    """Drop the baseline and latched changes; a new sign-off records anew."""
    dictVerification.pop(S_BASELINE_KEY, None)
    dictVerification.pop(S_CHANGES_KEY, None)
