"""Pure utility functions for pipeline execution (leaf module).

This module has ZERO intra-package imports. It exists to break circular
dependency cycles between pipelineRunner and its extracted submodules.
"""

import posixpath
import re
import time
import uuid
from datetime import datetime, timezone


__all__ = [
    "fdictMapOutputTokenStems",
    "fsShellQuote",
    "fsBuildUniqueTemporaryPath",
    "fbStepIsInteractive",
    "fsLabelFromStepIndex",
    "fiStepIndexFromLabel",
    "flistStepsWithLabels",
    "fdictWorkflowWithLabels",
    "fdictStepWithLabel",
    "fnAttachStepLabels",
    "fnClearOutputModifiedFlags",
    "fsSlugFromStepName",
    "fsValidateStepName",
    "fnRequireUniqueStepSlug",
    "fbStepDirectoryConforms",
    "fsDescribeStepIdConflict",
    "T_RUN_CLEARED_VERIFICATION_FLAGS",
]


# ---------------------------------------------------------------------------
# Step-name <-> directory contract (2026-07-18 ruling).
#
# A step's directory basename IS a pure function of its name:
# split the name on whitespace, uppercase each word's first letter,
# preserve the rest of the word as typed, concatenate. Hyphens pass
# through verbatim (catalogue designators and hyphenated compounds
# depend on them). "Step Name" -> "StepName"; "Spectral Line Fit" ->
# "SpectralLineFit". Parent path
# components are free; only the final component is governed.
# ---------------------------------------------------------------------------

_S_STEP_NAME_ALLOWED_PATTERN = r"^[A-Za-z0-9 \-]+$"


def fsValidateStepName(sNameRaw):
    """Return the trimmed step name or raise ValueError with the reason.

    Names become directory names under the slug contract, so the
    alphabet is letters, digits, spaces, and hyphens — nothing else.
    """
    sName = (sNameRaw or "").strip()
    if not sName:
        raise ValueError("The step name must not be empty")
    if len(sName) > 100:
        raise ValueError("Step names are limited to 100 characters")
    if not re.match(_S_STEP_NAME_ALLOWED_PATTERN, sName):
        raise ValueError(
            "Step names may contain only letters, digits, spaces, "
            "and hyphens — the name becomes the step's directory "
            "name",
        )
    if not re.search(r"[A-Za-z0-9]", sName):
        raise ValueError(
            "Step names need at least one letter or digit",
        )
    return sName


def fsSlugFromStepName(sName):
    """Return the directory basename the contract derives from a name."""
    listWords = (sName or "").split()
    return "".join(
        sWord[0].upper() + sWord[1:] for sWord in listWords if sWord
    )


def fsDescribeStepIdConflict(dictWorkflow, bRequirePresent=False):
    """Return a diagnostic when ``sStepId`` cannot serve as identity.

    ``sStepId`` is the merge authority for run-produced state: results
    are attached to steps by id, never by list position. That is only
    sound when every id present is a non-empty string and no two steps
    share one — ``fnEnsureStepIds`` preserves an existing duplicate,
    and a duplicate lets the last occurrence silently claim both
    steps' results. Callers on the load and save paths pass
    ``bRequirePresent=False`` because a legacy file legitimately omits
    ids (the loader assigns them); callers about to MERGE BY id pass
    ``True``, because an absent id there means results with no owner.

    Returns ``""`` when ids are usable as identity.
    """
    dictSeenAt = {}
    for iIndex, dictStep in enumerate(
        dictWorkflow.get("listSteps", []) or [],
    ):
        if not isinstance(dictStep, dict):
            continue
        sStepId = dictStep.get("sStepId")
        if sStepId is None or sStepId == "":
            if bRequirePresent:
                return (
                    f"Step{iIndex + 1:02d} has no sStepId; run results "
                    "cannot be attributed by identity"
                )
            continue
        if not isinstance(sStepId, str):
            return (
                f"Step{iIndex + 1:02d} has a non-string sStepId "
                f"({sStepId!r}); step ids must be strings"
            )
        if sStepId in dictSeenAt:
            return (
                f"Step{dictSeenAt[sStepId] + 1:02d} and "
                f"Step{iIndex + 1:02d} share sStepId {sStepId!r}; "
                "step ids must be unique so results and cross-step "
                "references cannot attach to the wrong step"
            )
        dictSeenAt[sStepId] = iIndex
    return ""


def fnRequireUniqueStepSlug(dictWorkflow, iStepIndex, sName):
    """Raise ValueError when another step's name maps to the same slug.

    Compared case-insensitively: macOS clones of the project repo sit
    on case-insensitive filesystems, where ``ABTest`` and ``AbTest``
    are the same directory. ``iStepIndex`` is the step being named
    (-1 for a step not yet in the list).
    """
    sSlugLower = fsSlugFromStepName(sName).lower()
    for iOther, dictOther in enumerate(
        dictWorkflow.get("listSteps") or []
    ):
        if iOther == iStepIndex or not isinstance(dictOther, dict):
            continue
        sOtherName = dictOther.get("sName") or ""
        if sOtherName and fsSlugFromStepName(
            sOtherName,
        ).lower() == sSlugLower:
            raise ValueError(
                f"'{sName}' maps to the same directory name as step "
                f"'{sOtherName}' — step directories must be unique",
            )


def fbStepDirectoryConforms(dictStep):
    """Return True iff the step honors the name->directory contract.

    Steps without a directory (ai-declaration) and templated
    directories (a ``{token}`` cannot be compared statically) are
    exempt, mirroring the boundary validation's exemptions.
    """
    if not isinstance(dictStep, dict):
        return True
    sDirectory = (dictStep.get("sDirectory") or "").strip("/")
    if not sDirectory or "{" in sDirectory:
        return True
    return posixpath.basename(sDirectory) == fsSlugFromStepName(
        dictStep.get("sName") or "",
    )


def _fsPlainTokenStem(sOutputFile):
    """Return the basename-without-extension token stem for a path."""
    sBasename = posixpath.basename(sOutputFile.replace("\\", "/"))
    return posixpath.splitext(sBasename)[0]


def _fsQualifiedTokenStem(sOutputFile, sStem):
    """Return a collision-safe token stem qualified by the path prefix.

    Scientific output filenames are public API and must not be renamed
    just to disambiguate tokens; instead the token gains the leading
    path segment: ``EngleBarnes/output/Converged_Param_Dictionary.json``
    becomes ``EngleBarnes_Converged_Param_Dictionary``.
    """
    sNormalized = sOutputFile.replace("\\", "/")
    sDirectory = posixpath.dirname(sNormalized)
    sQualifier = sDirectory.split("/")[0] if sDirectory else ""
    sCandidate = f"{sQualifier}_{sStem}" if sQualifier else sStem
    return re.sub(r"[^0-9A-Za-z_]", "_", sCandidate)


def fdictMapOutputTokenStems(listOutputFiles):
    """Map token stems to output paths, qualifying colliding basenames.

    Non-colliding entries keep the historical bare stem. When two
    declared outputs share a basename stem, EVERY colliding entry is
    registered only under its qualified stem and the ambiguous bare
    stem is dropped, so a stale reference fails loudly in reference
    validation instead of silently resolving to the last writer.
    """
    dictStemCounts = {}
    for sOutputFile in listOutputFiles:
        sStem = _fsPlainTokenStem(sOutputFile)
        dictStemCounts[sStem] = dictStemCounts.get(sStem, 0) + 1
    dictTokenStems = {}
    for sOutputFile in listOutputFiles:
        sStem = _fsPlainTokenStem(sOutputFile)
        if dictStemCounts[sStem] > 1:
            dictTokenStems[
                _fsQualifiedTokenStem(sOutputFile, sStem)] = sOutputFile
        else:
            dictTokenStems[sStem] = sOutputFile
    return dictTokenStems


def fsShellQuote(sValue):
    """Safely quote a value for use in a shell command.

    Wraps the value in single quotes and escapes any embedded single
    quotes with the standard '\\'' idiom, preventing shell injection.
    """
    return "'" + sValue.replace("'", "'\\''") + "'"


def fsBuildUniqueTemporaryPath(sTargetPath):
    """Return a sibling temp path no other writer can already hold.

    Temp-then-rename buys atomicity for ONE writer. A temp name derived
    only from the target — ``state.json`` plus ``.tmp`` — assumes there
    is only ever one, and the moment two writers overlap they share the
    name: the first to rename consumes the file the second is about to
    rename, and the second's install fails against a path that no
    longer exists. Both state files vaibify keeps are written by the
    drain-held route, the file poll and the run at once, so the overlap
    is ordinary rather than exotic.

    The target's own path stays the prefix, so the temp is a sibling on
    the same filesystem — rename atomicity requires that — and ``.tmp``
    stays last so a reader can still recognise what the file is.
    """
    return f"{sTargetPath}.{uuid.uuid4().hex}.tmp"


# ---------------------------------------------------------------------------
# Interactive-flag contract.
#
# ``bInteractive`` decides which label a step carries and therefore
# which step an agent-issued label resolves to. It arrives from JSON
# the in-container agent edits by hand and from an API field typed
# ``Optional[bool]``, so its persisted value can be a real boolean,
# ``null``, a quoted string, a number, or absent entirely. Truthiness
# and equality-against-``True`` disagree on every one of those, and a
# labeller that disagrees with the label resolver hands the researcher
# the WRONG step (shipped live: ``"bInteractive": null`` made ``A01``
# resolve to the second step). One classifier, called everywhere.
# ---------------------------------------------------------------------------

_T_INTERACTIVE_FALSE_TOKENS = (
    "", "false", "0", "no", "off", "none", "null",
)


def fbStepIsInteractive(dictStep):
    """Return True iff the step is declared interactive.

    The single classifier for ``bInteractive``. Real booleans pass
    through; a JSON string is read as the word it spells rather than
    as a non-empty object; everything else, ``null`` and absence
    included, is automated.
    """
    if not isinstance(dictStep, dict):
        return False
    valueFlag = dictStep.get("bInteractive", False)
    if isinstance(valueFlag, str):
        return valueFlag.strip().lower() \
            not in _T_INTERACTIVE_FALSE_TOKENS
    return bool(valueFlag)


def fsLabelFromStepIndex(dictWorkflow, iStepIndex):
    """Return the display label (A01, I01) for a 0-based step index.

    Labels are per-type sequential: ``A09`` means the 9th automated
    step, ``I01`` means the 1st interactive step. See ``CLAUDE.md``
    Traps for why this differs from ``listSteps[index]``. For bulk
    label computation prefer ``flistComputeAllStepLabels`` so the
    pre-scan amortizes across the whole workflow.
    """
    listSteps = dictWorkflow.get("listSteps", [])
    if iStepIndex < 0 or iStepIndex >= len(listSteps):
        return f"{iStepIndex + 1:02d}"
    listLabels = flistComputeAllStepLabels(listSteps)
    return listLabels[iStepIndex]


def flistComputeAllStepLabels(listSteps):
    """Return per-step labels in one linear pass over ``listSteps``.

    Counts automated and interactive steps inline so the whole label
    vector is produced in O(N) time instead of the historical O(N**2)
    cost incurred by per-step ``fsLabelFromStepIndex`` calls.
    """
    listLabels = []
    iAutomated = 0
    iInteractive = 0
    for dictStep in listSteps:
        if fbStepIsInteractive(dictStep):
            iInteractive += 1
            listLabels.append(f"I{iInteractive:02d}")
        else:
            iAutomated += 1
            listLabels.append(f"A{iAutomated:02d}")
    return listLabels


def fiStepIndexFromLabel(dictWorkflow, sLabel):
    """Return the 0-based index for a step label like ``A09`` or ``I01``.

    Raises ``ValueError`` with a readable message when ``sLabel`` is
    not the expected shape or does not resolve to a step in
    ``dictWorkflow``.
    """
    if not isinstance(sLabel, str):
        raise ValueError(
            f"step label must be a string, got "
            f"{type(sLabel).__name__}"
        )
    sNormalized = sLabel.strip().upper()
    if (len(sNormalized) < 2 or sNormalized[0] not in ("A", "I")
            or not sNormalized[1:].isdigit()):
        raise ValueError(
            f"invalid step label {sLabel!r} — expected "
            f"pattern like 'A09' or 'I01'"
        )
    return _fiResolveLabelWithinType(
        dictWorkflow, sLabel, sNormalized[0], int(sNormalized[1:]),
    )


def _fiResolveLabelWithinType(dictWorkflow, sLabel, sPrefix, iWanted):
    """Walk listSteps and find the iWanted-th step matching sPrefix."""
    bWantInteractive = sPrefix == "I"
    listSteps = dictWorkflow.get("listSteps", [])
    iCount = 0
    for iIndex, dictStep in enumerate(listSteps):
        if fbStepIsInteractive(dictStep) == bWantInteractive:
            iCount += 1
            if iCount == iWanted:
                return iIndex
    sKind = "interactive" if bWantInteractive else "automated"
    raise ValueError(
        f"no step {sLabel!r} — workflow has {iCount} "
        f"{sKind} step(s)"
    )


def flistStepsWithLabels(dictWorkflow):
    """Return listSteps shallow-copied with ``sLabel`` added to each.

    Does not mutate ``dictWorkflow``. The returned list contains
    fresh step dicts so the caller can hand them to a JSON
    serializer without risk of persisting ``sLabel`` into
    ``project.json``.
    """
    listSteps = dictWorkflow.get("listSteps", [])
    listLabels = flistComputeAllStepLabels(listSteps)
    listOut = []
    for iIndex, dictStep in enumerate(listSteps):
        dictCopy = dict(dictStep)
        dictCopy["sLabel"] = listLabels[iIndex]
        listOut.append(dictCopy)
    return listOut


def fdictWorkflowWithLabels(dictWorkflow):
    """Return a shallow-copied workflow whose listSteps carry sLabel."""
    dictCopy = dict(dictWorkflow)
    dictCopy["listSteps"] = flistStepsWithLabels(dictWorkflow)
    return dictCopy


def fdictStepWithLabel(dictWorkflow, iStepIndex):
    """Return a shallow copy of the step at iStepIndex with sLabel."""
    dictStep = dict(dictWorkflow["listSteps"][iStepIndex])
    dictStep["sLabel"] = fsLabelFromStepIndex(dictWorkflow, iStepIndex)
    return dictStep


def fnAttachStepLabels(dictWorkflow):
    """Mutate listSteps in place, writing a fresh sLabel on each step.

    Called from the workflow load/save paths so ``sLabel`` persists in
    ``project.json`` and in-memory state stays coherent. Recomputation
    is always fresh — insertions, deletions, or reorderings produce
    the correct per-type-sequential label on the next save.
    """
    listSteps = dictWorkflow.get("listSteps", [])
    listLabels = flistComputeAllStepLabels(listSteps)
    for iIndex, dictStep in enumerate(listSteps):
        dictStep["sLabel"] = listLabels[iIndex]


def _fnRecordRunStats(
    dictStep, fStartTime, fCpuTime=0.0, iExitCode=None,
    bDeterminismApplied=None,
):
    """Store timing, finish stamp, and outcome in the step's run stats.

    ``iExitCode`` is optional so callers without an outcome (and stats
    recorded before outcomes were kept) stay valid; the dashboard's
    Last-run line omits the outcome when it was never recorded.

    ``bDeterminismApplied`` records whether the step ran with the
    ``SOURCE_DATE_EPOCH`` / matplotlib-salt prefix. It is written only
    when the caller actually knows, because a later reproduction
    mismatch must be able to distinguish "ran deterministically",
    "ran without the guarantee", and "nobody recorded it" — an absent
    key is *unknown*, never clean.
    """
    dictRunStats = {
        "fWallClock": round(time.time() - fStartTime, 1),
        "sFinishedUtc": datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ",
        ),
    }
    # ``None`` means nobody measured it -- a host run takes no CPU
    # reading, because the measurement came from a GNU time wrapper
    # host mode does not use. The key is OMITTED rather than zeroed,
    # on the same terms as bDeterminismApplied below: an absent key is
    # unknown, and 0.0 would be a measurement.
    if fCpuTime is not None:
        dictRunStats["fCpuTime"] = round(fCpuTime, 1)
    if iExitCode is not None:
        dictRunStats["iExitCode"] = int(iExitCode)
    if bDeterminismApplied is not None:
        dictRunStats["bDeterminismEnvApplied"] = bool(bDeterminismApplied)
    dictStep["dictRunStats"] = dictRunStats


def _fdictBuildWorkflowVars(dictWorkflow):
    """Extract variable substitution dict from workflow metadata."""
    return {
        "sPlotDirectory": dictWorkflow.get("sPlotDirectory", "Plot"),
        "sFigureType": dictWorkflow.get("sFigureType", "pdf"),
        "sRepoRoot": dictWorkflow.get("sProjectRepoPath", ""),
    }


# Cleared at run start on every step, and cleared again by the
# completion merge for the steps that EXECUTED — the two sites must
# agree on what a run invalidates, which is why the tuple has one home.
T_RUN_CLEARED_VERIFICATION_FLAGS = (
    "bOutputModified", "listModifiedFiles", "bUpstreamModified",
)


def fnClearOutputModifiedFlags(dictWorkflow):
    """Clear modification flags on all steps before a pipeline run."""
    for dictStep in dictWorkflow.get("listSteps", []):
        dictVerification = dictStep.get("dictVerification", {})
        for sFlag in T_RUN_CLEARED_VERIFICATION_FLAGS:
            dictVerification.pop(sFlag, None)
        dictStep["dictVerification"] = dictVerification


async def _fnEmitCommandHeader(fnStatusCallback, sOriginal, sResolved):
    """Emit the command being run, showing resolution if different."""
    await fnStatusCallback(
        {"sType": "output", "sLine": f"$ {sOriginal}"}
    )
    if sResolved != sOriginal:
        await fnStatusCallback(
            {"sType": "output", "sLine": f"  => {sResolved}"}
        )


async def _fnEmitStepResult(fnStatusCallback, iStepNumber, iExitCode):
    """Send a stepPass or stepFail event based on exit code."""
    sType = "stepPass" if iExitCode == 0 else "stepFail"
    await fnStatusCallback({
        "sType": sType, "iStepNumber": iStepNumber,
        "iExitCode": iExitCode,
    })


async def _fnEmitCompletion(fnStatusCallback, iExitCode):
    """Send the final completed or failed event."""
    sResultType = "completed" if iExitCode == 0 else "failed"
    await fnStatusCallback(
        {"sType": sResultType, "iExitCode": iExitCode}
    )


async def _fnEmitBanner(
    fnStatusCallback, iStepNumber, sStepName, sStepLabel=None,
):
    """Emit step banner lines to the status callback."""
    if sStepLabel is None:
        sStepLabel = f"{iStepNumber:02d}"
    sBanner = f"Step {sStepLabel} - {sStepName}"
    sLine = "=" * len(sBanner)
    for sText in ["", sLine, sBanner, sLine, ""]:
        await fnStatusCallback({"sType": "output", "sLine": sText})
