"""The environment every shell vaibify opens carries, from one place.

Claude Code's fullscreen renderer draws on the terminal's alternate
screen, where vaibify's xterm keeps no scrollback, and captures the
mouse, so scrolling back and selecting text stop working in the pane.
``CLAUDE_CODE_DISABLE_ALTERNATE_SCREEN=1`` selects its classic
renderer, which writes into the normal buffer the pane can scroll.

The variable rides on the container's own environment (``docker run``)
AND on every shell vaibify opens — the dashboard terminal's exec, the
``vaibify connect`` exec and the host terminal's PTY — because a
container created before this setting existed keeps the environment it
was created with. Each site renders it from this table, so no copy can
drift from another.
"""

__all__ = [
    "DICT_AGENT_TERMINAL_ENVIRONMENT",
    "fdictBuildAgentTerminalEnvironment",
    "flistBuildAgentTerminalEnvironmentArguments",
]


DICT_AGENT_TERMINAL_ENVIRONMENT = {
    "CLAUDE_CODE_DISABLE_ALTERNATE_SCREEN": "1",
}


def fdictBuildAgentTerminalEnvironment():
    """Return a fresh copy of the variables, safe for a caller to extend."""
    return dict(DICT_AGENT_TERMINAL_ENVIRONMENT)


def flistBuildAgentTerminalEnvironmentArguments():
    """Return the variables as ``docker`` ``-e KEY=VALUE`` arguments."""
    listArguments = []
    for sKey, sValue in DICT_AGENT_TERMINAL_ENVIRONMENT.items():
        listArguments.extend(["-e", f"{sKey}={sValue}"])
    return listArguments
