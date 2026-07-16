"""Helpers for the AI Declaration step kind.

A workflow step with ``sStepKind == "ai-declaration"`` exists solely
to hold a researcher's declaration of how (and which) AI tools assisted
the work. The step has no data or test commands — its single concern
is the contents of one markdown file pointed at by ``sDeclarationFile``
and the standard ``sUser`` attestation badge.

This module owns three small responsibilities so the rest of the
codebase has one place to look:

1. ``S_DEFAULT_DECLARATION_FILENAME`` and the markdown template body
   used when generating a starter file.
2. ``fbStepIsAiDeclaration`` — the predicate used by the renderer and
   the L2 gate to recognize the kind.
3. ``fnWriteDeclarationTemplate`` — atomically writes the starter
   template to ``<projectRepo>/<sRelativePath>`` without clobbering an
   existing file.

File probes and writes go through the ``repoFiles`` adapter seam
(dual-accept: a raw path string wraps into ``HostRepoFiles``) so the
declaration file lands inside the project repo wherever it actually
lives — a host clone for the CLI, the container for GUI routes.
"""

import posixpath

from vaibify.reproducibility.repoFiles import (
    ffilesEnsureRepoFiles,
    fsRepoRootOf,
)


__all__ = [
    "S_AI_DECLARATION_STEP_KIND",
    "S_DEFAULT_DECLARATION_DIRECTORY",
    "S_DEFAULT_DECLARATION_FILENAME",
    "S_DEFAULT_DECLARATION_STEP_NAME",
    "S_DECLARATION_TEMPLATE",
    "fbStepIsAiDeclaration",
    "fbDeclarationFileExists",
    "fnWriteDeclarationTemplate",
    "fdictBuildAiDeclarationStep",
]


S_AI_DECLARATION_STEP_KIND = "ai-declaration"
S_DEFAULT_DECLARATION_FILENAME = "AI_USAGE.md"
S_DEFAULT_DECLARATION_DIRECTORY = "aiDeclaration"
S_DEFAULT_DECLARATION_STEP_NAME = "AI Declaration"


S_DECLARATION_TEMPLATE = """# AI Usage Declaration

## Models used

(e.g., Claude Opus 4.7, GPT-5)

## How AI assisted each step

(Free-form list of steps and what the AI helped with — code
generation, debugging, plot tweaks, documentation, etc.)

## Review policy

(How a human reviewed AI-generated output before it landed)

## Anything else researchers should know

"""


def fbStepIsAiDeclaration(dictStep):
    """Return True iff a step is an AI Declaration step.

    Backward-compatible default: steps with no ``sStepKind`` field are
    treated as ``"data"`` (the historical kind), so this returns False
    for legacy steps.
    """
    if not isinstance(dictStep, dict):
        return False
    return dictStep.get("sStepKind") == S_AI_DECLARATION_STEP_KIND


def fbDeclarationFileExists(filesRepo, sRelativePath):
    """Return True iff the declaration file exists in the project repo."""
    filesRepo = ffilesEnsureRepoFiles(filesRepo)
    if not filesRepo.sRootPath or not sRelativePath:
        return False
    return filesRepo.fbIsFile(sRelativePath)


def fnWriteDeclarationTemplate(filesRepo, sRelativePath):
    """Write the starter template to <projectRepo>/<sRelativePath>.

    Refuses to overwrite an existing file or directory (caller should
    check ``fbDeclarationFileExists`` first and route a re-generate
    request through an explicit overwrite path if that becomes
    needed). Creates parent directories as required. Returns the
    repo-absolute path written for the caller's response payload.
    """
    filesRepo = ffilesEnsureRepoFiles(filesRepo)
    if not filesRepo.sRootPath:
        raise ValueError("a project repo path or adapter is required")
    if not sRelativePath:
        raise ValueError("sRelativePath is required")
    if filesRepo.fbIsFile(sRelativePath) or filesRepo.fbIsDir(sRelativePath):
        raise FileExistsError(
            f"Refusing to overwrite existing file: {sRelativePath}"
        )
    filesRepo.fnWriteTextAtomic(sRelativePath, S_DECLARATION_TEMPLATE)
    return posixpath.join(fsRepoRootOf(filesRepo), sRelativePath)


def _fdictEmptyTestsBlock():
    """Return the empty dictTests skeleton shared by all step kinds."""
    return {
        "dictQualitative": {"saCommands": [], "sFilePath": ""},
        "dictQuantitative": {
            "saCommands": [], "sFilePath": "", "sStandardsPath": "",
        },
        "dictIntegrity": {"saCommands": [], "sFilePath": ""},
        "listUserTests": [],
    }


def _fdictEmptyVerificationBlock():
    """Return the default dictVerification skeleton for a new step."""
    return {
        "sUser": "untested",
        "sUnitTest": "unnecessary",
        "sIntegrity": "unnecessary",
        "sQualitative": "unnecessary",
        "sQuantitative": "unnecessary",
        "listModifiedFiles": [],
        "bUpstreamModified": False,
    }


def _fdictEmptyCommandBlock():
    """Return the empty command and file-list fields shared by step kinds."""
    return {
        "saDataCommands": [],
        "saOutputDataFiles": [],
        "saTestCommands": [],
        "saPlotCommands": [],
        "saPlotFiles": [],
        "saStepScripts": [],
    }


def _fsRequireStepDirectory(sDirectory):
    """Return the stripped step directory or raise ValueError when empty.

    ``stateManager.ftSplitMergedDict`` keys per-step state (including
    the ``sUser`` attestation) by ``sDirectory`` and silently drops
    entries with an empty key, so an ai-declaration step without a
    directory would lose its attestation across reloads.
    """
    sCleanDirectory = (sDirectory or "").strip()
    if not sCleanDirectory:
        raise ValueError(
            "ai-declaration steps require a non-empty sDirectory; "
            "per-step state is keyed by sDirectory and an empty value "
            "would drop the attestation on reload"
        )
    return sCleanDirectory


def fdictBuildAiDeclarationStep(
    sName=S_DEFAULT_DECLARATION_STEP_NAME,
    sDeclarationFile=S_DEFAULT_DECLARATION_FILENAME,
    sDirectory=S_DEFAULT_DECLARATION_DIRECTORY,
):
    """Return a fresh ai-declaration step dict with empty command lists.

    The returned shape mirrors a normal data step so the rest of the
    workflow plumbing (mtime tracking, sUser attestation, verification
    state) keeps working unchanged; the kind discriminator lives only
    in ``sStepKind`` and ``sDeclarationFile``. The step is interactive
    (``bInteractive`` True) so it earns an I-label and follows the
    sUser-only pass rule, and ``sDirectory`` must be non-empty so the
    attestation survives the workflow.json / state.json split.
    """
    dictStep = {
        "sName": (sName or "").strip() or S_DEFAULT_DECLARATION_STEP_NAME,
        "sDirectory": _fsRequireStepDirectory(sDirectory),
        "sStepKind": S_AI_DECLARATION_STEP_KIND,
        "sDeclarationFile": sDeclarationFile or "",
        "bRunEnabled": True,
        "bPlotOnly": False,
        "bInteractive": True,
        "dictTests": _fdictEmptyTestsBlock(),
        "dictVerification": _fdictEmptyVerificationBlock(),
    }
    dictStep.update(_fdictEmptyCommandBlock())
    return dictStep
