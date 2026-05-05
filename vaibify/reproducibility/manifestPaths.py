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
    "fsExtractScriptFromCommand",
    "flistExtractStepScripts",
    "flistStepScriptRepoPaths",
    "flistStepStandardsRepoPaths",
]


S_CONTAINER_WORKSPACE_PREFIX = "/workspace/"

TUPLE_OUTPUT_KEYS = ("saOutputFiles", "saPlotFiles", "saDataFiles")
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
    """
    listTokens = sCommand.split()
    if not listTokens:
        return ""
    sFirst = listTokens[0]
    if sFirst in ("python", "python3"):
        return _fsFirstPyToken(listTokens[1:])
    if sFirst.endswith(".py"):
        return sFirst
    return ""


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
        listPaths.append(_fsResolveScriptToRepoPath(sScript, sDirectory))
    return listPaths


def _fsResolveScriptToRepoPath(sScript, sDirectory):
    """Return ``sScript`` resolved against ``sDirectory`` as a repo path."""
    if sScript.startswith("/"):
        return fsToRepoRelative(sScript)
    if sDirectory:
        sJoined = posixpath.normpath(posixpath.join(sDirectory, sScript))
        return fsToRepoRelative(sJoined)
    return fsToRepoRelative(sScript)


def flistStepStandardsRepoPaths(dictStep):
    """Return repo-relative paths of test standards for one step."""
    dictTests = dictStep.get("dictTests", {}) or {}
    listPaths = []
    for sCategory in TUPLE_TEST_CATEGORY_KEYS:
        dictCategory = dictTests.get(sCategory, {}) or {}
        sStandardsPath = dictCategory.get("sStandardsPath", "")
        if sStandardsPath:
            listPaths.append(fsToRepoRelative(sStandardsPath))
    return listPaths
