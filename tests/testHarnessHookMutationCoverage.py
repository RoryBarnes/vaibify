"""Falsification tests for the PreToolUse harness hooks.

AGENTS.md ("Ask first" -> "Enforced by harness hooks") states two hard
rules that nothing in the suite could previously falsify:

- ``askSensitiveEdit.py`` pauses ``Edit``, ``Write`` and ``NotebookEdit``
  on ``docker/*``, ``vaibify/docker/containerManager.py``,
  ``vaibify/config/secretManager.py``, any ``AGENTS.md`` and any
  ``.claude/skills/*/SKILL.md``.
- ``blockDestructiveGit.py`` denies force-push (except
  ``--force-with-lease``) and interactive rebase.

Both hooks guard the highest-blast-radius files in the repository — the
container security model, the credential manager and the agent's own
standing instructions — yet no test referenced either script, so a
silent drift in a pattern list or in ``.claude/settings.json`` would
have removed the protection without a single red test.

The decision payloads are asserted through the hooks' own public
entry points, so a rewrite that keeps the pattern lists but stops
emitting an ``ask``/``deny`` verdict is caught too.
"""

import importlib.util
import io
import json
import os
import sys

import pytest


pytestmark = pytest.mark.falsification


_S_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_S_HOOK_DIRECTORY = os.path.join(_S_REPO_ROOT, ".claude", "hooks")


# Absolute paths, because Claude Code hands the hook an absolute
# ``file_path``. Each entry is one documented sensitive category.
_DICT_DOCUMENTED_SENSITIVE_PATHS = {
    "build context": (
        "/home/user/vaibify/vaibify/containerImage/entrypoint.sh"
    ),
    "container manager": (
        "/home/user/vaibify/vaibify/docker/containerManager.py"
    ),
    "secret manager": (
        "/home/user/vaibify/vaibify/config/secretManager.py"
    ),
    "root agent guide": "/home/user/vaibify/AGENTS.md",
    "subtree agent guide": "/home/user/vaibify/vaibify/gui/AGENTS.md",
    "skill definition": (
        "/home/user/vaibify/.claude/skills/add-route-module/SKILL.md"
    ),
}


_TUPLE_DOCUMENTED_BLOCKED_COMMANDS = (
    "git push --force",
    "git push -f origin main",
    "git rebase -i main",
    "git rebase --interactive HEAD~3",
)


def _fdictImportHookNamespace(sFileName):
    """Return the module namespace of a ``.claude/hooks`` script."""
    sModuleName = "vaibifyHook_" + sFileName.replace(".py", "")
    sPath = os.path.join(_S_HOOK_DIRECTORY, sFileName)
    specHook = importlib.util.spec_from_file_location(sModuleName, sPath)
    moduleHook = importlib.util.module_from_spec(specHook)
    specHook.loader.exec_module(moduleHook)
    return vars(moduleHook)


def _ftDecideSensitiveEdit(sFilePath):
    """Return the sensitive-edit hook's ``(bShouldAsk, sReason)`` verdict."""
    dictNamespace = _fdictImportHookNamespace("askSensitiveEdit.py")
    return dictNamespace["ftDecision"](sFilePath)


def _ftDecideDestructiveGit(sCommand):
    """Return the destructive-git hook's ``(bShouldBlock, sReason)``."""
    dictNamespace = _fdictImportHookNamespace("blockDestructiveGit.py")
    return dictNamespace["ftDecision"](sCommand)


def _fdictRunHookMain(sFileName, dictToolInput):
    """Run a hook's ``fnMain`` on a payload and return its parsed output."""
    dictNamespace = _fdictImportHookNamespace(sFileName)
    streamSavedStdin, streamSavedStdout = sys.stdin, sys.stdout
    sys.stdin = io.StringIO(json.dumps({"tool_input": dictToolInput}))
    sys.stdout = io.StringIO()
    try:
        dictNamespace["fnMain"]()
        sEmitted = sys.stdout.getvalue().strip()
    finally:
        sys.stdin, sys.stdout = streamSavedStdin, streamSavedStdout
    return json.loads(sEmitted) if sEmitted else {}


def testSensitiveEditHookAsksForEveryDocumentedPath():
    """Every documented sensitive category still pauses for confirmation.

    Kills: deleting the ``/vaibify/config/secretManager\\.py$`` entry
    from ``LIST_SENSITIVE_PATTERNS`` in
    ``.claude/hooks/askSensitiveEdit.py`` — the credential manager
    would then be editable with no human in the loop.
    """
    listUnguarded = [
        sCategory
        for sCategory, sPath in _DICT_DOCUMENTED_SENSITIVE_PATHS.items()
        if not _ftDecideSensitiveEdit(sPath)[0]
    ]
    assert listUnguarded == [], (
        "AGENTS.md promises a confirmation pause on these categories, "
        "but askSensitiveEdit.py let them through: "
        + ", ".join(listUnguarded)
    )


def testSensitiveEditHookReadsTheNotebookPathField():
    """A NotebookEdit payload is inspected, not silently ignored.

    Kills: dropping the ``notebook_path`` fallback from
    ``fsExtractTargetPath`` — ``NotebookEdit`` carries no
    ``file_path``, so every notebook edit to a sensitive file would
    bypass the pause while the hook still looks wired.
    """
    dictOutput = _fdictRunHookMain(
        "askSensitiveEdit.py",
        {
            "notebook_path": (
                "/home/user/vaibify/vaibify/containerImage/analysis.ipynb"
            ),
        },
    )
    dictSpecific = dictOutput.get("hookSpecificOutput", {})
    assert dictSpecific.get("permissionDecision") == "ask", (
        "a NotebookEdit against a sensitive path must pause; the hook "
        f"emitted {dictOutput!r}"
    )


def testSensitiveEditHookLeavesOrdinarySourceFilesAlone():
    """The pause discriminates; it is not a blanket ask.

    Kills: changing ``ftDecision``'s fallthrough to ``return True, ""``
    — every edit would prompt, training the researcher to click through
    the prompt that guards the container security model.
    """
    bShouldAsk, _sReason = _ftDecideSensitiveEdit(
        "/home/user/vaibify/vaibify/gui/workflowManager.py",
    )
    assert bShouldAsk is False, (
        "an ordinary backend module must not trigger the sensitive-edit "
        "pause; a hook that asks for everything is a hook nobody reads"
    )


def testSensitiveEditHookEmitsTheAskDecisionPayload():
    """The emitted verdict is literally ``ask``, not ``allow``.

    Kills: changing ``"permissionDecision": "ask"`` to ``"allow"`` in
    ``fnMain`` — the pattern list would still match while the harness
    waved every sensitive edit straight through.
    """
    dictOutput = _fdictRunHookMain(
        "askSensitiveEdit.py",
        {"file_path": "/home/user/vaibify/AGENTS.md"},
    )
    dictSpecific = dictOutput.get("hookSpecificOutput", {})
    assert dictSpecific.get("hookEventName") == "PreToolUse"
    assert dictSpecific.get("permissionDecision") == "ask", (
        "the sensitive-edit hook must return an 'ask' decision; got "
        f"{dictSpecific.get('permissionDecision')!r}"
    )
    assert dictSpecific.get("permissionDecisionReason"), (
        "the pause must explain itself or the researcher cannot judge it"
    )


def testDestructiveGitHookDeniesTheDocumentedCommands():
    """Force-push and interactive rebase are still hard-blocked.

    Kills: deleting the interactive-rebase entry from
    ``LIST_BLOCKED_PATTERNS`` in
    ``.claude/hooks/blockDestructiveGit.py`` — an agent session could
    then rewrite shared history from a rebase that needs a TTY it does
    not have.
    """
    listUnblocked = [
        sCommand
        for sCommand in _TUPLE_DOCUMENTED_BLOCKED_COMMANDS
        if not _ftDecideDestructiveGit(sCommand)[0]
    ]
    assert listUnblocked == [], (
        "AGENTS.md documents these as hard-blocked, but "
        "blockDestructiveGit.py allowed them: " + ", ".join(listUnblocked)
    )


def testDestructiveGitHookPermitsForceWithLease():
    """The documented ``--force-with-lease`` exemption still holds.

    Kills: removing the ``(?!-with-lease)`` lookahead from the
    force-push pattern — the safe form documented as the escape hatch
    would be denied, and the block would have no legitimate way past it.
    """
    bShouldBlock, _sReason = _ftDecideDestructiveGit(
        "git push --force-with-lease origin feature/branch",
    )
    assert bShouldBlock is False, (
        "--force-with-lease is the documented safe alternative and must "
        "not be denied"
    )


def testDestructiveGitHookEmitsTheDenyDecisionPayload():
    """A blocked command produces a ``deny`` verdict, not a bare exit.

    Kills: changing ``"permissionDecision": "deny"`` to ``"ask"`` in
    ``blockDestructiveGit.fnMain`` — AGENTS.md calls these "hard-blocked",
    and an ask-decision turns the block into a prompt an agent can talk
    its way through.
    """
    dictOutput = _fdictRunHookMain(
        "blockDestructiveGit.py", {"command": "git push --force"},
    )
    dictSpecific = dictOutput.get("hookSpecificOutput", {})
    assert dictSpecific.get("permissionDecision") == "deny", (
        "force-push must be denied outright; got "
        f"{dictSpecific.get('permissionDecision')!r}"
    )


def testHookSettingsRegisterBothPreToolUseHooks():
    """``.claude/settings.json`` still wires both hooks on their matchers.

    Kills: narrowing the sensitive-edit matcher from
    ``"Edit|Write|NotebookEdit"`` to ``"Edit"`` — ``Write`` recreates a
    file wholesale, so a narrowed matcher lets the highest-risk
    operation past a hook that still looks installed.
    """
    sPath = os.path.join(_S_REPO_ROOT, ".claude", "settings.json")
    with open(sPath, encoding="utf-8") as fileHandle:
        dictSettings = json.load(fileHandle)
    dictMatcherToCommands = {
        dictEntry.get("matcher", ""): " ".join(
            dictHook.get("command", "")
            for dictHook in dictEntry.get("hooks", [])
        )
        for dictEntry in dictSettings.get("hooks", {}).get("PreToolUse", [])
    }
    assert "askSensitiveEdit.py" in dictMatcherToCommands.get(
        "Edit|Write|NotebookEdit", "",
    ), (
        "askSensitiveEdit.py must run on Edit, Write AND NotebookEdit; "
        f"PreToolUse matchers are {sorted(dictMatcherToCommands)}"
    )
    assert "blockDestructiveGit.py" in dictMatcherToCommands.get(
        "Bash", "",
    ), "blockDestructiveGit.py must run on every Bash tool call"
