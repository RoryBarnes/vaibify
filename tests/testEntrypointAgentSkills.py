"""Tests for the vaibify-shipped agent skills and their delivery chain.

Skills live in ``docker/skills/<name>/SKILL.md``, are baked into the
image at ``/usr/share/vaibify/skills``, and are copied into the
container user's Claude Code skills directory on every container
start. These are structural tests: they pin the shipping chain and
each skill's load-bearing content so a refactor cannot silently
unship a skill or strip a guardrail the skill exists to state.
"""

import os
import re

_S_DOCKER_DIR = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__), "..", "docker",
    )
)
_S_SKILLS_DIR = os.path.join(_S_DOCKER_DIR, "skills")


def _fsReadDockerFile(sName):
    with open(
        os.path.join(_S_DOCKER_DIR, sName), "r", encoding="utf-8",
    ) as fileHandle:
        return fileHandle.read()


def _fsReadSkill(sSkillName):
    sPath = os.path.join(_S_SKILLS_DIR, sSkillName, "SKILL.md")
    with open(sPath, "r", encoding="utf-8") as fileHandle:
        return fileHandle.read()


def _fdictParseFrontmatter(sSource):
    """Return the YAML frontmatter fields of a SKILL.md."""
    matchBlock = re.match(r"^---\n(.*?)\n---\n", sSource, re.DOTALL)
    assert matchBlock, "SKILL.md must open with YAML frontmatter"
    dictFields = {}
    for sLine in matchBlock.group(1).splitlines():
        if ":" in sLine:
            sKey, sValue = sLine.split(":", 1)
            dictFields[sKey.strip()] = sValue.strip()
    return dictFields


# --------- shipping chain ---------


def test_dockerfile_bakes_skills_and_monitor():
    """The image carries the skills directory and the usage monitor."""
    sDockerfile = _fsReadDockerFile("Dockerfile")
    assert "COPY skills /usr/share/vaibify/skills" in sDockerfile
    assert "pip install --no-cache-dir claude-monitor" in sDockerfile


def test_entrypoint_installs_skills_for_the_container_user():
    """The entrypoint copies skills to ~/.claude/skills and chowns.

    Without the chown, root-owned skill files would be unreadable
    noise to the unprivileged agent — the same ownership trap as the
    host-to-container file writes.
    """
    sEntrypoint = _fsReadDockerFile("entrypoint.sh")
    assert "fnInstallAgentSkills()" in sEntrypoint
    assert "/usr/share/vaibify/skills" in sEntrypoint
    assert '/home/${CONTAINER_USER}/.claude/skills' in sEntrypoint
    iDefinition = sEntrypoint.index("fnInstallAgentSkills()")
    sBody = sEntrypoint[iDefinition:sEntrypoint.index(
        "\n}", iDefinition,
    )]
    assert "chown -R" in sBody
    # The main flow must actually call it (defined-but-never-called
    # was exactly the failure mode of the push-manifest recorder).
    sAfterDefinition = sEntrypoint[iDefinition + len(
        "fnInstallAgentSkills()",
    ):]
    assert "\n    fnInstallAgentSkills" in sAfterDefinition


def test_every_shipped_skill_has_valid_frontmatter():
    """Each skill directory carries a SKILL.md whose name matches."""
    listSkillNames = sorted(os.listdir(_S_SKILLS_DIR))
    assert len(listSkillNames) >= 2
    for sSkillName in listSkillNames:
        dictFields = _fdictParseFrontmatter(_fsReadSkill(sSkillName))
        assert dictFields.get("name") == sSkillName
        assert len(dictFields.get("description", "")) > 40, (
            sSkillName + ": the description drives skill selection "
            "and must say when to use it"
        )


# --------- session-budget guardrails ---------


def test_session_budget_puts_checkpointing_before_monitoring():
    """Checkpoint discipline must be stated as the PRIMARY defense.

    Weekly/monthly account limits and parallel sessions are invisible
    to a container-local monitor — a skill that promises monitoring
    will save the work is dishonest.
    """
    sSkill = _fsReadSkill("session-budget")
    assert "primary defense" in sSkill
    assert "RESUME_NOTES.md" in sSkill
    assert "underestimate" in sSkill or "lower bound" in sSkill
    assert "account-wide" in sSkill


def test_session_budget_pause_mechanics_are_stated():
    """The pause is a blocking sleep computed to the window reset."""
    sSkill = _fsReadSkill("session-budget")
    assert "sleep" in sSkill
    assert "95%" in sSkill  # the default pause threshold
    assert "commit" in sSkill.lower()


# --------- read-arxiv guardrails ---------


def test_read_arxiv_prefers_source_and_records_version():
    """TeX source first, version recorded, PDF only as fallback."""
    sSkill = _fsReadSkill("read-arxiv")
    assert "e-print" in sSkill
    assert "documentclass" in sSkill
    assert "version" in sSkill.lower()
    iSource = sSkill.index("e-print")
    iFallback = sSkill.lower().index("fall back to the pdf")
    assert iSource < iFallback, (
        "the PDF must be the fallback, not the default"
    )


def test_read_arxiv_respects_network_isolation():
    """The skill must not hang a network-isolated container."""
    sSkill = _fsReadSkill("read-arxiv")
    assert "network isolation" in sSkill
    assert "--max-time" in sSkill
