"""Evaluate whether the skill descriptions trigger on the right requests.

A skill's frontmatter description is the only text the model sees when
deciding whether to invoke the skill, so its discriminative power can be
tested in isolation: present the descriptions plus a candidate user
request to a fresh model instance and ask which skill, if any, it would
invoke. This mirrors the description-optimization procedure in
Anthropic's skill-authoring guidance (trigger and non-trigger query
sets), and is deliberately an approximation — it classifies from the
descriptions alone rather than driving the full Claude Code harness.
For end-to-end behavior, use tools/evaluateSkillOutcomes.py.

Usage:
    python tools/evaluateSkillTriggers.py
    python tools/evaluateSkillTriggers.py --model haiku --timeout 120

Requires the `claude` CLI on PATH. Each prompt costs one short model
call. Exits 1 if any prompt triggers the wrong skill (or fails to
trigger the right one).
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path


__all__ = ["fnMain"]


REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_ROOT = REPO_ROOT / ".claude" / "skills"
PATH_DEFAULT_EVALS = REPO_ROOT / "tools" / "skillEvals" / "triggerPrompts.json"


def flistLoadSkillDescriptions():
    """Return (sName, sDescription) tuples parsed from each SKILL.md frontmatter."""
    listSkills = []
    for pathSkill in sorted(SKILLS_ROOT.rglob("SKILL.md")):
        dictFrontmatter = {}
        listLines = pathSkill.read_text(encoding="utf-8").splitlines()
        for sLine in listLines[1:]:
            if sLine.strip() == "---":
                break
            if ":" in sLine:
                sKey, sValue = sLine.split(":", 1)
                dictFrontmatter[sKey.strip()] = sValue.strip()
        if "name" in dictFrontmatter and "description" in dictFrontmatter:
            listSkills.append((dictFrontmatter["name"], dictFrontmatter["description"]))
    return listSkills


def fsBuildClassificationPrompt(listSkills, sRequest):
    """Return the meta-prompt asking which skill the request should trigger."""
    listLines = [
        "You are an agent deciding whether to invoke a skill. The available",
        "skills are listed below with their descriptions. Read the user",
        "request and reply with EXACTLY one line containing only the name of",
        "the single skill you would invoke, or the word none if no listed",
        "skill applies. Do not explain.",
        "",
        "Available skills:",
    ]
    for sName, sDescription in listSkills:
        listLines.append(f"- {sName}: {sDescription}")
    listLines.extend(["", f"User request: {sRequest}"])
    return "\n".join(listLines)


def fsAskClaude(sPrompt, sModel, iTimeoutSeconds):
    """Return the model's one-line answer, normalized to a bare skill name."""
    listCommand = ["claude", "-p", sPrompt]
    if sModel:
        listCommand.extend(["--model", sModel])
    result = subprocess.run(
        listCommand,
        capture_output=True,
        text=True,
        timeout=iTimeoutSeconds,
        cwd=REPO_ROOT,
    )
    if result.returncode != 0:
        raise RuntimeError(f"claude -p failed: {result.stderr.strip()[:500]}")
    listAnswerLines = [s for s in result.stdout.strip().splitlines() if s.strip()]
    sAnswer = listAnswerLines[-1] if listAnswerLines else ""
    return re.sub(r"[^a-z0-9-]", "", sAnswer.strip().lower())


def fnReportResult(sExpected, sActual, sPrompt):
    """Print one PASS/FAIL line for a classified prompt."""
    sVerdict = "PASS" if sActual == sExpected else "FAIL"
    print(f"  [{sVerdict}] expected={sExpected!r} actual={sActual!r}  {sPrompt}")


def fnMain():
    """Classify every eval prompt and exit 1 on any trigger mismatch."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--evals", type=Path, default=PATH_DEFAULT_EVALS)
    parser.add_argument("--model", default="")
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()

    listSkills = flistLoadSkillDescriptions()
    if not listSkills:
        print("No skills found under .claude/skills/ — nothing to evaluate.")
        return 1
    listPrompts = json.loads(args.evals.read_text(encoding="utf-8"))[
        "listTriggerPrompts"
    ]
    print(f"Classifying {len(listPrompts)} prompts against {len(listSkills)} skills:")
    iFailures = 0
    for dictPrompt in listPrompts:
        sExpected = dictPrompt["sExpectedSkill"]
        try:
            sActual = fsAskClaude(
                fsBuildClassificationPrompt(listSkills, dictPrompt["sPrompt"]),
                args.model,
                args.timeout,
            )
        except (RuntimeError, subprocess.TimeoutExpired, FileNotFoundError) as error:
            print(f"  [ERROR] {dictPrompt['sPrompt']}: {error}")
            iFailures += 1
            continue
        fnReportResult(sExpected, sActual, dictPrompt["sPrompt"])
        if sActual != sExpected:
            iFailures += 1
    print(f"\n{len(listPrompts) - iFailures}/{len(listPrompts)} prompts classified correctly.")
    return 1 if iFailures else 0


if __name__ == "__main__":
    sys.exit(fnMain())
