"""Pure helpers for extracting workflow artefact paths.

These helpers are the single source of truth for the set of repo-relative
paths that a workflow declares: outputs, the scripts that produce them,
and the test standards used to verify them. Both ``manifestWriter`` and
``vaibify.gui.stateContract`` consume this module so the manifest envelope
and the canonical-tracked-files set can never silently disagree.

The module sits in ``vaibify.reproducibility`` because that layer is below
``vaibify.gui`` in the dependency graph; ``stateContract`` is allowed to
import upward into ``reproducibility``, but ``reproducibility`` may not
import from ``gui``.

Path safety:

* All inputs are workflow-declared user-controlled strings. ``..``
  traversal is preserved in the output so the downstream
  ``_fnRejectPathEscape`` guard in ``manifestWriter`` sees and rejects
  it; the helpers do not collapse traversal to a benign-looking path.
* Container-absolute paths under ``/workspace/`` are stripped to
  repo-relative (the canonical legacy form). Other absolute paths have
  their leading ``/`` stripped (matching ``stateContract`` behaviour);
  callers that go on to hash the result rely on the ``..`` and
  symlink guards to catch any actual escape.
"""

import posixpath


__all__ = [
    "S_CONTAINER_WORKSPACE_PREFIX",
    "TUPLE_OUTPUT_KEYS",
    "TUPLE_COMMAND_KEYS",
    "TUPLE_TEST_CATEGORY_KEYS",
    "fsToRepoRelative",
    "fsResolveStepPathToRepoPath",
    "fsExtractScriptFromCommand",
    "flistExtractStepScripts",
    "flistStepDeclarationRepoPaths",
    "flistStepOutputRepoPaths",
    "flistStepScriptRepoPaths",
    "flistStepStandardsRepoPaths",
]


S_CONTAINER_WORKSPACE_PREFIX = "/workspace/"

TUPLE_OUTPUT_KEYS = ("saPlotFiles", "saOutputDataFiles")
TUPLE_COMMAND_KEYS = ("saDataCommands", "saPlotCommands")
TUPLE_TEST_CATEGORY_KEYS = (
    "dictQualitative",
    "dictQuantitative",
    "dictIntegrity",
)


def fsToRepoRelative(sPath):
    """Return a repo-root-relative posix path.

    Strips a leading ``/workspace/`` so host callers can safely join
    the result against a host workspace root. Other absolute paths
    have their leading ``/`` stripped; the manifest writer's
    ``_fnRejectPathEscape`` and ``_fnRejectSymlinkComponent`` guards
    catch any actual escape downstream.
    """
    if not sPath:
        return ""
    sNormal = posixpath.normpath(sPath)
    if sNormal.startswith(S_CONTAINER_WORKSPACE_PREFIX):
        return sNormal[len(S_CONTAINER_WORKSPACE_PREFIX):]
    if sNormal == "/workspace":
        return ""
    return sNormal.lstrip("/")


def fsExtractScriptFromCommand(sCommand):
    """Return the ``.py`` script token in a command, or empty if absent.

    Recognises the two canonical forms used by vaibify steps:

    * ``python <script.py> [args...]`` and the ``python3`` variant
    * ``<script.py> [args...]`` (a directly-executable script)

    Tokens that do not end in ``.py`` are treated as flags or
    non-script invocations (e.g. ``python -u foo.py``, ``python -m
    mymod``, ``python <<EOF``) and yield an empty string. Earlier
    behaviour returned ``listTokens[1]`` unconditionally, which made
    the manifest writer try to hash a literal file called ``-u`` when
    a step ran ``python -u foo.py`` and crashed with FileNotFoundError.

    Tokens containing ``{`` are templated references to workflow
    globals (e.g. ``python {scriptDir}/foo.py``). The reproducibility
    layer does not know how to resolve globals (that lives in
    ``vaibify.gui``) so templated tokens are skipped entirely; they
    must not enter the manifest envelope or the canonical-tracked
    set, since hashing the literal placeholder would produce a phantom
    missing-file entry.
    """
    listTokens = sCommand.split()
    if not listTokens:
        return ""
    sFirst = listTokens[0]
    if sFirst in ("python", "python3"):
        sScript = _fsFirstPyToken(listTokens[1:])
    elif sFirst.endswith(".py"):
        sScript = sFirst
    else:
        return ""
    if "{" in sScript:
        return ""
    return sScript


def _fsFirstPyToken(listTokens):
    """Return the first ``.py`` token after skipping leading ``-`` flags."""
    for sToken in listTokens:
        if sToken.endswith(".py"):
            return sToken
        if sToken.startswith("-"):
            continue
        return ""
    return ""


def flistExtractStepScripts(dictStep):
    """Return ``.py`` script tokens extracted from a step's commands."""
    listScripts = []
    for sKey in TUPLE_COMMAND_KEYS:
        for sCommand in dictStep.get(sKey, []) or []:
            sScript = fsExtractScriptFromCommand(sCommand)
            if sScript:
                listScripts.append(sScript)
    return listScripts


def flistStepScriptRepoPaths(dictStep):
    """Return repo-relative paths of scripts referenced by a step."""
    sDirectory = dictStep.get("sDirectory", "") or ""
    listPaths = []
    for sScript in flistExtractStepScripts(dictStep):
        listPaths.append(fsResolveStepPathToRepoPath(sScript, sDirectory))
    return listPaths


def fsResolveStepPathToRepoPath(sPath, sDirectory):
    """Return a step-declared path resolved to repo-relative form.

    Step-declared paths (scripts, outputs, test files) are relative to
    the step's ``sDirectory`` — the directory the step runs from and
    writes into — so non-absolute paths are joined with it before
    normalising. This helper is the single join point; every collector
    that turns step declarations into repo paths must use it, because
    a collector with its own join is how the manifest and the
    canonical-tracked-files set drift apart.
    """
    if sPath.startswith("/"):
        return fsToRepoRelative(sPath)
    if sDirectory:
        sJoined = posixpath.normpath(posixpath.join(sDirectory, sPath))
        return fsToRepoRelative(sJoined)
    return fsToRepoRelative(sPath)


def fdictWorkflowTemplateValues(dictWorkflow):
    """Return the workflow's top-level string fields for ``{token}`` paths.

    Declared file paths may reference workflow-level template tokens
    (``{sPlotDirectory}/corner.{sFigureType}``). The values those
    tokens name are the workflow's own scalar string fields, so the
    reproducibility layer can resolve file declarations without
    importing the GUI variable engine. Non-string values are excluded
    — a token that would not substitute to a path fragment stays
    unresolved and the declaration is skipped.
    """
    return {
        sKey: sValue
        for sKey, sValue in (dictWorkflow or {}).items()
        if isinstance(sKey, str) and isinstance(sValue, str)
    }


def fsResolveWorkflowTokens(sPath, dictTemplateValues):
    """Substitute ``{token}`` references from the template values.

    Unknown tokens are left in place (mirroring the GUI resolver's
    behavior) so the caller's ``"{" in`` check can skip declarations
    that did not fully resolve.
    """
    sResolved = str(sPath)
    for sKey, sValue in (dictTemplateValues or {}).items():
        sResolved = sResolved.replace("{" + sKey + "}", sValue)
    return sResolved


def flistStepOutputRepoPaths(dictStep, dictTemplateValues=None):
    """Return repo-relative declared output paths for one step.

    Covers every ``TUPLE_OUTPUT_KEYS`` entry. Non-templated entries
    resolve against the step directory (where the step writes them).
    Templated entries (``{sPlotDirectory}/foo.pdf``) resolve to
    repo-relative paths via ``dictTemplateValues`` — the workflow's
    top-level string fields — and are NOT joined with the step
    directory, matching ``stateContract._flistStepOutputRepoPaths``.
    Without ``dictTemplateValues``, or when a token stays unresolved,
    the templated entry is skipped so a literal placeholder never
    enters the manifest envelope as a phantom missing file. (Skipping
    was previously unconditional, which meant a workflow declaring
    every figure through ``{sPlotDirectory}`` never had any figure
    pinned — no figure hash existed for the Overleaf/arXiv verifies
    to compare against.) Absolute entries pass through unchanged so
    the manifest writer's absolute-path guard rejects the declaration
    loudly instead of reinterpreting it as a repo path.
    """
    sDirectory = dictStep.get("sDirectory", "") or ""
    listPaths = []
    for sKey in TUPLE_OUTPUT_KEYS:
        for sFile in dictStep.get(sKey, []) or []:
            if not sFile:
                continue
            sPath = str(sFile)
            if "{" in sPath:
                sPath = fsResolveWorkflowTokens(
                    sPath, dictTemplateValues,
                )
                if "{" in sPath:
                    continue
                listPaths.append(
                    fsToRepoRelative(posixpath.normpath(sPath))
                )
                continue
            if sPath.startswith("/"):
                listPaths.append(sPath)
                continue
            listPaths.append(
                fsResolveStepPathToRepoPath(sPath, sDirectory)
            )
    return listPaths


def flistStepStandardsRepoPaths(dictStep):
    """Return repo-relative paths of test standards for one step.

    Defensively tolerates malformed ``dictTests`` shapes — a list, a
    string, or any non-dict — by skipping the step. A workflow.json
    that fails the schema check upstream should not crash the
    canonical-tracked-files computation here.
    """
    dictTests = dictStep.get("dictTests", {})
    if not isinstance(dictTests, dict):
        return []
    listPaths = []
    for sCategory in TUPLE_TEST_CATEGORY_KEYS:
        dictCategory = dictTests.get(sCategory, {})
        if not isinstance(dictCategory, dict):
            continue
        sStandardsPath = dictCategory.get("sStandardsPath", "")
        if sStandardsPath:
            listPaths.append(fsToRepoRelative(sStandardsPath))
    return listPaths


def flistStepDeclarationRepoPaths(dictStep):
    """Return the ai-declaration step's declaration file, repo-relative.

    The declaration is a canonical publication artifact: it must be
    committed, pushed, and manifest-pinned like every other declared
    file, so it joins the declared-path set here — the single source
    of truth for both the manifest envelope and the canonical
    tracked-files set. The step-kind literal is inlined to keep this
    module a pure leaf (it matches
    ``aiDeclarationStep.S_AI_DECLARATION_STEP_KIND``).
    """
    if not isinstance(dictStep, dict):
        return []
    if dictStep.get("sStepKind") != "ai-declaration":
        return []
    sDeclarationPath = (dictStep.get("sDeclarationFile") or "").strip()
    if not sDeclarationPath:
        return []
    return [fsToRepoRelative(sDeclarationPath)]
