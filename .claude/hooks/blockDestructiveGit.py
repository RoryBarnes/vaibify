#!/usr/bin/env python3
"""PreToolUse hook: deny destructive git operations.

Fires on Bash tool calls. Blocks force-push (except --force-with-lease)
and interactive rebase. An agent run that genuinely needs either can be
executed manually outside the agent session.
"""

import json
import re
import sys


__all__ = ["fnMain", "ftDecision"]


LIST_BLOCKED_PATTERNS = [
    (
        r"\bgit\s+push\s+(?:--force(?!-with-lease)|-f\b)",
        "Force-push can overwrite shared history. Use "
        "--force-with-lease or run the command manually.",
    ),
    (
        r"\bgit\s+rebase\s+(?:-i\b|--interactive\b)",
        "Interactive rebase requires a TTY editor and is not "
        "appropriate in an agent session. Run manually.",
    ),
]


def ftDecision(sCommand):
    """Return (bShouldBlock, sReason) for a given shell command."""
    for sPattern, sReason in LIST_BLOCKED_PATTERNS:
        if re.search(sPattern, sCommand):
            return True, sReason
    return False, ""


def fnMain():
    """Entry point invoked by Claude Code as a PreToolUse hook."""
    dictInput = json.load(sys.stdin)
    sCommand = dictInput.get("tool_input", {}).get("command", "")
    if not sCommand:
        return 0
    bShouldBlock, sReason = ftDecision(sCommand)
    if not bShouldBlock:
        return 0
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": sReason,
        }
    }))
    return 0


if __name__ == "__main__":
    sys.exit(fnMain())
