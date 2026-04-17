#!/usr/bin/env python3
"""PreToolUse hook: pause for confirmation on edits to sensitive files.

Fires on Edit, Write, and NotebookEdit tool calls. Examines the target
file path and returns an "ask" decision when the path matches a known
high-blast-radius category: Docker security files, credential handling,
or agent documentation.
"""

import json
import re
import sys


__all__ = ["fnMain", "fsExtractTargetPath", "ftDecision"]


LIST_SENSITIVE_PATTERNS = [
    (
        r"/docker/",
        "Docker files govern container isolation. A weak edit "
        "(--privileged, removing --cap-drop, disabling gosu, broadening "
        "bind mounts) can enable container escape. Pausing to confirm.",
    ),
    (
        r"/vaibify/docker/containerManager\.py$",
        "containerManager.py controls the Docker security model; "
        "pausing to confirm.",
    ),
    (
        r"/vaibify/config/secretManager\.py$",
        "secretManager.py handles credentials. A wrong line can leak "
        "tokens into git history; rotation is the only remediation. "
        "Pausing to confirm.",
    ),
    (
        r"/AGENTS\.md$",
        "AGENTS.md is the overlap between human and agent. Changes to "
        "the rules should be reviewed by a human. Pausing to confirm.",
    ),
    (
        r"/\.claude/skills/[^/]+/SKILL\.md$",
        "Skill files are agent-facing recipes. Pausing to confirm.",
    ),
]


def fsExtractTargetPath(dictInput):
    """Return the file_path or notebook_path from a tool input payload."""
    dictToolInput = dictInput.get("tool_input", {})
    return dictToolInput.get("file_path") or dictToolInput.get("notebook_path", "")


def ftDecision(sFilePath):
    """Return (bShouldAsk, sReason) for a given target path."""
    for sPattern, sReason in LIST_SENSITIVE_PATTERNS:
        if re.search(sPattern, sFilePath):
            return True, sReason
    return False, ""


def fnMain():
    """Entry point invoked by Claude Code as a PreToolUse hook."""
    dictInput = json.load(sys.stdin)
    sFilePath = fsExtractTargetPath(dictInput)
    if not sFilePath:
        return 0
    bShouldAsk, sReason = ftDecision(sFilePath)
    if not bShouldAsk:
        return 0
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": sReason,
        }
    }))
    return 0


if __name__ == "__main__":
    sys.exit(fnMain())
