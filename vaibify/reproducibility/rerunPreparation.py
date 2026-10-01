"""Prepare the shadow so a rerun can only pass on bytes it regenerated.

The shadow holds a copy of the WHOLE repository, outputs included. A
step that exits zero without writing anything -- an output cache, a
wrong command, a data command skipped by ``bPlotOnly`` -- therefore left
every pinned output exactly as the archive had it, every hash matched,
and the attestation read ``passed N of N`` for a run that computed
nothing. The comparison could not tell "regenerated identically" from
"never touched".

So before any step runs, the paths the rerun is about to regenerate are
deleted from the shadow, and a step's declared scratch directories are
cleared so a cache cannot shortcut regeneration. Afterwards a pinned
output that exists was necessarily written by the run, and one that does
not is ``missing``. Deleting is only safe if it can never reach a file
the rerun does NOT regenerate, so every declared path is classified
first:

* **produced** -- a declared output of a step the rerun executes. It is
  deleted before the run and graded after it.
* **protected** -- a declared input or script that no executed step
  produces, an output of a step the rerun carries (an interactive
  step), ``MANIFEST.sha256``, and the environment files. Never deleted.
  A step-2 input that step 1 produces is a generated intermediate, not
  protected: steps run in dependency order, so it is regenerated before
  it is read.
* **ambiguous** -- a path that is both produced and protected (a carried
  output an executed step also declares). The rerun is REFUSED by name;
  keeping the file and then counting it as regenerated would be the
  false pass this module exists to prevent.

The deletion runs as ONE program inside the shadow, because only there
can symlinks be resolved against the shadow's own filesystem. It
validates every target before it deletes anything, so a refusal leaves
the shadow untouched, and it never follows a symlink out of a directory
it clears.
"""

import json
import posixpath

from vaibify.gui.pipelineUtils import fbStepIsInteractive
from vaibify.reproducibility.manifestPaths import (
    fdictWorkflowTemplateValues,
    flistStepDeclarationRepoPaths,
    flistStepOutputRepoPaths,
)
from vaibify.reproducibility.manifestWriter import (
    flistCollectCanonicalRepoPaths,
)
from vaibify.reproducibility.publicationScope import (
    TUPLE_LEVEL3_ENVELOPE_PATHS,
)
from vaibify.reproducibility.repoFiles import fsRepoRootOf


__all__ = [
    "RerunPreparationRefusedError",
    "fdictClassifyRerunPaths",
    "fdictClearShadowBeforeRerun",
    "flistSelectStepsTheRerunExecutes",
    "fsetDeclaredOutputRepoPaths",
]


class RerunPreparationRefusedError(Exception):
    """The shadow cannot be prepared without risking a protected file.

    Derives from ``Exception``, never ``OSError``: a refusal must not be
    swallowed by an I/O handler and reported as a hiccup.
    """


_S_JOB_RELATIVE_PATH = ".vaibify/rerunPreparationJob.json"
_S_RESULT_PREFIX = "VAIBIFY_RERUN_PREPARATION "
_F_PREPARATION_TIMEOUT_SECONDS = 900.0

# Runs inside the shadow. Plain standard library, Python 3.8 or newer.
# Every target is validated before any is deleted.
_S_PREPARATION_PROGRAM = """\
import json
import os
import shutil
import sys

sJobPath = sys.argv[1]
with open(sJobPath) as fileJob:
    dictJob = json.load(fileJob)
os.remove(sJobPath)
sRoot = os.path.realpath(dictJob["sRepoRoot"])


def fsLocate(sRelative):
    sJoined = os.path.join(sRoot, sRelative)
    return os.path.join(
        os.path.realpath(os.path.dirname(sJoined)),
        os.path.basename(sJoined))


def fbIsWithin(sInner, sOuter):
    return sInner == sOuter or sInner.startswith(sOuter + os.sep)


def flistFindRefusals(sKind, sRelative, sLocated, dictProtectedNames):
    if sLocated == sRoot or not fbIsWithin(sLocated, sRoot):
        return [sKind + " " + repr(sRelative) + " is not inside the repository"]
    listRefusals = []
    for sProtected, sName in dictProtectedNames.items():
        if fbIsWithin(sProtected, sLocated):
            listRefusals.append(
                sKind + " " + repr(sRelative) + " is or contains the "
                "protected file " + repr(sName))
        elif fbIsWithin(sLocated, sProtected):
            listRefusals.append(
                sKind + " " + repr(sRelative) + " lies inside the "
                "protected path " + repr(sName))
    return listRefusals


def fbRemove(sLocated):
    if os.path.islink(sLocated) or os.path.isfile(sLocated):
        os.remove(sLocated)
    elif os.path.isdir(sLocated):
        shutil.rmtree(sLocated)
    else:
        return False
    return True


dictProtectedNames = {
    fsLocate(sPath): sPath for sPath in dictJob["listProtected"]}
listTargets = [
    ("output", sPath) for sPath in dictJob["listProduced"]
] + [
    ("scratch directory", sPath) for sPath in dictJob["listScratch"]]
listRefusals = []
for sKind, sRelative in listTargets:
    listRefusals.extend(flistFindRefusals(
        sKind, sRelative, fsLocate(sRelative), dictProtectedNames))
dictResult = {
    "listRefusals": listRefusals,
    "listDeletedOutputs": [],
    "listClearedScratch": [],
}
if not listRefusals:
    for sKind, sRelative in listTargets:
        if fbRemove(fsLocate(sRelative)):
            sKey = (
                "listDeletedOutputs" if sKind == "output"
                else "listClearedScratch")
            dictResult[sKey].append(sRelative)
print(PREFIX + json.dumps(dictResult))
"""


def flistSelectStepsTheRerunExecutes(dictWorkflow):
    """Return the steps the unattended rerun runs, in workflow order.

    Interactive steps need a researcher and a rerun has none, so their
    outputs are carried. A disabled step is refused before the rerun
    starts, so none remain here. ``reproduce.sh`` renders exactly this
    set: a stranger's script and the attestation must agree on what
    "reproduced" means.
    """
    return [
        dictStep
        for dictStep in (dictWorkflow or {}).get("listSteps", []) or []
        if isinstance(dictStep, dict)
        and not fbStepIsInteractive(dictStep)
        and dictStep.get("bRunEnabled", True)
    ]


def fsetDeclaredOutputRepoPaths(listSteps, dictTemplateValues):
    """Return every declared output path of these steps, repo-relative.

    An ai-declaration step's declaration file joins its outputs: the
    manifest pins it as a publication artefact, and a human wrote it,
    so it is given for exactly the reason its step is.
    """
    setPaths = set()
    for dictStep in listSteps:
        setPaths.update(
            flistStepOutputRepoPaths(dictStep, dictTemplateValues),
        )
        setPaths.update(flistStepDeclarationRepoPaths(dictStep))
    return {sPath for sPath in setPaths if sPath}


def fdictClassifyRerunPaths(dictWorkflow):
    """Return ``{setProduced, setProtected, setGiven, setAmbiguous}``.

    Resolved through the SAME helpers the manifest writer uses, so the
    classification and the manifest can never name different files.
    """
    dictTemplateValues = fdictWorkflowTemplateValues(dictWorkflow)
    listExecuted = flistSelectStepsTheRerunExecutes(dictWorkflow)
    setProduced = fsetDeclaredOutputRepoPaths(listExecuted, dictTemplateValues)
    setGiven = fsetDeclaredOutputRepoPaths(
        [
            dictStep for dictStep in dictWorkflow.get("listSteps", []) or []
            if isinstance(dictStep, dict) and fbStepIsInteractive(dictStep)
        ],
        dictTemplateValues,
    )
    setEnvelope = set(TUPLE_LEVEL3_ENVELOPE_PATHS)
    setDeclared = set(flistCollectCanonicalRepoPaths(dictWorkflow))
    setProtected = ((setDeclared - setProduced) | setGiven | setEnvelope)
    return {
        "setProduced": setProduced,
        "setProtected": setProtected,
        "setGiven": setGiven,
        "setAmbiguous": setProduced & (setGiven | setEnvelope),
    }


def flistResolveScratchRepoPaths(dictWorkflow, sRepoRoot):
    """Return the executed steps' scratch directories, repo-relative."""
    from vaibify.gui.workflowManager import (
        fdictBuildGlobalVariablesForRoot,
        flistResolveStepScratchDirs,
    )
    dictVariables = fdictBuildGlobalVariablesForRoot(dictWorkflow, sRepoRoot)
    listScratch = []
    for dictStep in flistSelectStepsTheRerunExecutes(dictWorkflow):
        for sAbsolute in flistResolveStepScratchDirs(dictStep, dictVariables):
            sRelative = posixpath.relpath(sAbsolute, sRepoRoot)
            if sRelative not in listScratch:
                listScratch.append(sRelative)
    return listScratch


def _fnRefuseAmbiguousOverlap(dictClassification):
    """Refuse when a path is both regenerated and not regenerable."""
    setAmbiguous = dictClassification["setAmbiguous"]
    if setAmbiguous:
        raise RerunPreparationRefusedError(
            "the rerun cannot be graded: "
            + ", ".join(sorted(setAmbiguous))
            + " is declared as an output of a step the rerun executes "
            "AND is a file the rerun carries or pins as given (an "
            "interactive step's output, or an environment file). Keeping "
            "it would count a given file as regenerated; deleting it "
            "would destroy a given file. Remove the duplicate "
            "declaration."
        )


def _fnRefuseUnsafeProducedPaths(setProduced):
    """Refuse a produced path that is absolute or climbs out of the repo."""
    listUnsafe = sorted(
        sPath for sPath in setProduced
        if posixpath.isabs(sPath)
        or posixpath.normpath(sPath).split("/")[0] == ".."
    )
    if listUnsafe:
        raise RerunPreparationRefusedError(
            "the rerun cannot be prepared: declared output(s) "
            + ", ".join(listUnsafe) + " lie outside the repository."
        )


def fdictClearShadowBeforeRerun(
    dictWorkflow, filesRepo, dictClassification,
):
    """Delete what the rerun regenerates; return what was removed.

    Raises :class:`RerunPreparationRefusedError`, with nothing deleted,
    when the classification is ambiguous, a target would reach a
    protected file, or the preparation could not run. The returned
    ``{listDeletedOutputs, listClearedScratch}`` is recorded in the
    attestation as the evidence that the outputs graded afterwards were
    absent when the run began.
    """
    _fnRefuseAmbiguousOverlap(dictClassification)
    _fnRefuseUnsafeProducedPaths(dictClassification["setProduced"])
    sRepoRoot = fsRepoRootOf(filesRepo)
    dictJob = {
        "sRepoRoot": sRepoRoot,
        "listProduced": sorted(dictClassification["setProduced"]),
        "listProtected": sorted(dictClassification["setProtected"]),
        "listScratch": flistResolveScratchRepoPaths(dictWorkflow, sRepoRoot),
    }
    dictResult = _fdictRunPreparationProgram(filesRepo, dictJob)
    if dictResult["listRefusals"]:
        raise RerunPreparationRefusedError(
            "the rerun was refused before any file was deleted: "
            + "; ".join(dictResult["listRefusals"])
        )
    return {
        "listDeletedOutputs": dictResult["listDeletedOutputs"],
        "listClearedScratch": dictResult["listClearedScratch"],
    }


def _fdictRunPreparationProgram(filesRepo, dictJob):
    """Ship the job into the shadow, run the program, parse its answer."""
    filesRepo.fnWriteTextAtomic(_S_JOB_RELATIVE_PATH, json.dumps(dictJob))
    sJobPath = posixpath.join(dictJob["sRepoRoot"], _S_JOB_RELATIVE_PATH)
    iExitCode, sOutput, sErrors = filesRepo.ftRunCommand(
        [
            "python3", "-c",
            _S_PREPARATION_PROGRAM.replace("PREFIX", repr(_S_RESULT_PREFIX)),
            sJobPath,
        ],
        _F_PREPARATION_TIMEOUT_SECONDS,
    )
    for sLine in reversed((sOutput or "").splitlines()):
        if sLine.startswith(_S_RESULT_PREFIX) and iExitCode == 0:
            return json.loads(sLine[len(_S_RESULT_PREFIX):])
    raise RerunPreparationRefusedError(
        "the shadow could not be prepared for the rerun (exit "
        f"{iExitCode}); the rerun was not attempted. "
        + ((sErrors or "") + (sOutput or "")).strip()[-400:]
    )
