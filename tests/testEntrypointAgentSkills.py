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


# --------- round-2 skills: load-bearing guardrails ---------


def test_all_expected_skills_are_shipped():
    """The five round-2 skills plus the original two are present."""
    listSkillNames = set(os.listdir(_S_SKILLS_DIR))
    for sExpected in (
        "session-budget", "read-arxiv", "aics-ladder",
        "create-pipeline-step", "vaibify-doc-map",
        "diagnose-failed-run", "read-manuscript", "running-steps",
        "reproducible-analysis",
    ):
        assert sExpected in listSkillNames, (
            "skill not shipped: " + sExpected
        )


def test_reproducible_analysis_forbids_throwaway_computation():
    """The load-bearing rule: numeric results come from a saved script,
    never a heredoc/python -c, and the script is structured to become a
    step (argparse inputs, file outputs)."""
    sSkill = _fsReadSkill("reproducible-analysis")
    assert "python -c" in sSkill
    assert "heredoc" in sSkill.lower()
    assert "argparse" in sSkill
    assert "create-pipeline-step" in sSkill  # promotes to a real step
    # Exploratory scripts have a git-tracked home, discoverable months
    # later by name + docstring; search before writing a duplicate.
    assert "explorations/" in sSkill
    assert "docstring" in sSkill.lower()
    assert "grep" in sSkill.lower()


def test_running_steps_prefers_dispatch_over_direct_execution():
    """The load-bearing rule: dispatch via vaibify-do so the dashboard
    shows the run; a bare shell execution is invisible as a running
    step and must be reported to the researcher."""
    sSkill = _fsReadSkill("running-steps")
    assert "run-step" in sSkill
    assert "run-selected-steps" in sSkill
    assert "invisible" in sSkill.lower()
    # Never hand-edit an existing workflow; the CAS fingerprint guards
    # concurrent edits.
    assert "never hand-edit" in sSkill.lower()
    assert "sBaseFingerprint" in sSkill or "compare-and-swap" in sSkill


def test_claude_md_carries_the_run_guardrail_and_points_to_skill():
    """The always-on CLAUDE.md keeps the short run guardrail (which the
    agent can violate without ever loading a skill) and points at the
    running-steps skill for the full protocol."""
    sEntrypoint = _fsReadDockerFile("entrypoint.sh")
    iStart = sEntrypoint.index("<< 'CLAUDEMD'\n")
    iEnd = sEntrypoint.index("\nCLAUDEMD\n", iStart)
    sBody = sEntrypoint[iStart:iEnd]
    assert "running-steps` skill" in sBody
    assert "not by executing scripts directly" in sBody
    # The reproducible-analysis guardrail is always-on (the agent can
    # reach for a heredoc without ever loading a skill), pointing at the
    # skill for the how-to.
    assert "reproducible-analysis` skill" in sBody
    assert "throwaway construction" in sBody
    assert "explorations/" in sBody


def test_aics_ladder_codifies_the_known_audit_traps():
    """The traps that produced false level reports must be stated.

    A green audit that used the wrong hash algorithm or read the
    wrong ledger is exactly the dashboard-honesty failure this skill
    exists to prevent.
    """
    sSkill = _fsReadSkill("aics-ladder")
    assert "iAICSLevel" in sSkill
    assert "blob SHA-1" in sSkill or "blob sha-1" in sSkill.lower()
    assert "state.json" in sSkill
    assert "user-only" in sSkill.lower()


def test_create_step_makes_the_token_contract_non_negotiable():
    """The cross-step {StepNN.varname} contract is the load-bearing rule."""
    sSkill = _fsReadSkill("create-pipeline-step")
    assert "{StepNN.varname}" in sSkill or "{Step02" in sSkill
    assert "argparse" in sSkill
    assert "append" in sSkill.lower()


def test_read_manuscript_forbids_unpulled_claims():
    """The skill must require pulling before claiming paper content."""
    sSkill = _fsReadSkill("read-manuscript")
    assert "pull-manuscript" in sSkill
    assert "from memory" in sSkill or "without having pulled" in sSkill
    # Honest fallback when no Overleaf binding exists.
    assert "read-arxiv" in sSkill


def test_diagnose_skill_prefers_readonly_actions_first():
    """The triage tree must name the read-only diagnostics."""
    sSkill = _fsReadSkill("diagnose-failed-run")
    assert "get-pipeline-state" in sSkill
    assert "get-host-log-tail" in sSkill
    assert "read-only" in sSkill.lower()


def test_doc_map_points_into_the_container_docs_dir():
    """The map must reference the in-container staged docs path."""
    sSkill = _fsReadSkill("vaibify-doc-map")
    assert "/usr/share/vaibify/docs" in sSkill
    assert "dashboard.md" in sSkill
    assert "reproducibility.md" in sSkill


# --------- CLAUDE.md slimming: sections became skill pointers ---------


def test_claude_md_delegates_ladder_and_step_authoring_to_skills():
    """The two heavy sections must point at skills, not re-inline them.

    The AICS-ladder walkthrough and the step-authoring protocol were
    ~325 always-on lines; they now live in on-demand skills. The
    safety-critical one-liners (authoritative iAICSLevel, user-only
    publication, the token contract) stay inline.
    """
    sEntrypoint = _fsReadDockerFile("entrypoint.sh")
    iStart = sEntrypoint.index("<< 'CLAUDEMD'\n")
    iEnd = sEntrypoint.index("\nCLAUDEMD\n", iStart)
    sBody = sEntrypoint[iStart:iEnd]
    assert "aics-ladder** skill" in sBody
    assert "create-pipeline-step** skill" in sBody
    # Safety one-liners survive inline.
    assert "iAICSLevel" in sBody
    assert "{StepNN.varname}" in sBody
    # The verbose walkthrough is gone (a body this size proves it).
    assert sBody.count("\n") < 220, (
        "CLAUDE.md body did not shrink — the heavy sections are "
        "still inlined"
    )


# --------- curated docs staged into the image ---------


def test_dockerfile_copies_staged_docs():
    """The image must COPY the curated docs to /usr/share/vaibify/docs."""
    sDockerfile = _fsReadDockerFile("Dockerfile")
    assert "COPY docs-staged /usr/share/vaibify/docs" in sDockerfile


def test_build_stages_the_curated_doc_set():
    """commandBuild must stage docs and be wired into build-context prep."""
    import os as _os
    sBuild = open(
        _os.path.join(
            _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
            "vaibify", "cli", "commandBuild.py",
        ),
        "r", encoding="utf-8",
    ).read()
    assert "def fnStageCuratedDocs" in sBuild
    assert "docs-staged" in sBuild
    # Wired into the build-context preparation, not defined-but-uncalled.
    iPrepare = sBuild.index("def fnPrepareBuildContext")
    iNext = sBuild.index("\ndef ", iPrepare + 1)
    assert "fnStageCuratedDocs" in sBuild[iPrepare:iNext]
