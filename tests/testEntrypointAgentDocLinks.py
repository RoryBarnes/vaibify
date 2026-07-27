"""Every installed agent must be able to find the project's guidance.

`.vaibify/AGENTS.md` is the one source of in-container agent guidance,
and `entrypoint.sh` makes each provider able to find it. Six of the
seven agents read a repo-root markdown file, so a flat symlink serves
them: Claude reads CLAUDE.md, Gemini reads GEMINI.md, and Codex,
OpenCode, OpenHands (v1) and Pi all read AGENTS.md.

Cline is the exception, and the reason this file exists. Its project
convention is a `.clinerules/` **directory** of markdown files, so it
cannot join the flat-name loop -- a `.clinerules` symlink would be a
file where a directory is expected. It was silently missing when four
agents were added while the symlink list stayed at three names, which
is the precise failure the guidance itself warns about: the agents
quietly start reading different instructions.

These are structural tests over the shell source, in the style of the
other entrypoint tests. They pin the coverage relationship -- every
agent the skills loop installs for must also be reachable by the doc
linker -- so adding an eighth agent cannot quietly reintroduce the
gap.
"""

import os
import re


_S_ENTRYPOINT = os.path.join(
    os.path.dirname(__file__), "..", "docker", "entrypoint.sh",
)


def _fsReadEntrypoint():
    with open(_S_ENTRYPOINT, "r", encoding="utf-8") as fileHandle:
        return fileHandle.read()


def _flistAgentsTheSkillsLoopCovers(sEntrypoint):
    """Return the agent list fnInstallAgentSkills iterates over."""
    matchLoop = re.search(
        r"for sAgent in ([a-z0-9 ]+); do", sEntrypoint,
    )
    assert matchLoop, "fnInstallAgentSkills lost its agent loop"
    return matchLoop.group(1).split()


def testFlatDocNamesCoverTheFileReadingAgents():
    """The repo-root names the six file-reading agents look for."""
    sEntrypoint = _fsReadEntrypoint()
    matchNames = re.search(
        r"for sName in ([A-Za-z0-9_. ]+); do", sEntrypoint,
    )
    assert matchNames, "the doc-link loop lost its name list"
    setNames = set(matchNames.group(1).split())
    assert {"CLAUDE.md", "AGENTS.md", "GEMINI.md"} <= setNames, (
        f"missing a provider's repo-root filename: {sorted(setNames)}"
    )


def testClineGetsARulesDirectoryNotAFlatSymlink():
    """Cline reads `.clinerules/`, so it needs a directory."""
    sEntrypoint = _fsReadEntrypoint()
    assert "fnLinkClineRules" in sEntrypoint, (
        "Cline has no doc-link path, so it starts with no project "
        "guidance at all."
    )
    matchBody = re.search(
        r"fnLinkClineRules\(\) \{(.*?)\n\}", sEntrypoint, re.S,
    )
    assert matchBody, "fnLinkClineRules lost its body"
    sBody = matchBody.group(1)
    assert "mkdir -p" in sBody and ".clinerules" in sBody, (
        "Cline's rules location must be created as a directory"
    )
    assert "../.vaibify/AGENTS.md" in sBody, (
        "the rules file must point at the canonical AGENTS.md, not "
        "hold its own copy"
    )
    assert "command -v cline" in sBody, (
        "creating a .clinerules directory in a repository that has no "
        "Cline installed leaves a stray directory to be committed by "
        "accident"
    )


def testEveryAgentWithSkillsAlsoHasADocPath():
    """No agent may be installed-for but not documented-to.

    The gap this catches is real history: four agents were added to
    the skills loop while the doc-link list stayed at three names, so
    Cline shipped with skills and no project guidance.
    """
    sEntrypoint = _fsReadEntrypoint()
    listAgents = _flistAgentsTheSkillsLoopCovers(sEntrypoint)
    # Agents served by the flat repo-root markdown names.
    setFlatNameAgents = {
        "claude", "codex", "gemini", "opencode", "openhands", "pi",
    }
    setSpecialCased = {"cline"}
    listUncovered = [
        sAgent for sAgent in listAgents
        if sAgent not in setFlatNameAgents | setSpecialCased
    ]
    assert not listUncovered, (
        "These agents get skills installed but no path to the "
        f"project's guidance: {listUncovered}. Either they read one "
        "of the repo-root names already linked (add them to "
        "setFlatNameAgents with that fact verified), or they need "
        "their own linker like fnLinkClineRules."
    )
