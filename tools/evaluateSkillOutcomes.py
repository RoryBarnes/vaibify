"""Evaluate skill outcomes: run agent tasks with and without a skill, grade with the suite.

For each task in tools/skillEvals/outcomeTasks.json this harness creates
a throwaway git worktree from HEAD, runs `claude -p` on the task prompt
inside it (for the "without" arm, with the skill's directory removed
first), then grades the result by running the task's grade commands in
the worktree — the repository's own pytest suite is the oracle. Passing
"with" and failing "without" is direct evidence the skill earns its keep;
failing "with" means the skill did not steer the agent to a correct
result.

This is the expensive layer of skill testing (a full agent run plus a
full test-suite run per task per arm), so it is a periodic audit before
merging skill changes, never CI. See docs/skillTesting.md.

Usage:
    python tools/evaluateSkillOutcomes.py                       # all tasks, both arms
    python tools/evaluateSkillOutcomes.py --task iniLoader --arm with
    python tools/evaluateSkillOutcomes.py --dry-run             # harness mechanics only
    python tools/evaluateSkillOutcomes.py --use-working-skills  # eval uncommitted skill edits

Notes:
- Worktrees materialize HEAD; uncommitted skill edits are invisible
  unless --use-working-skills copies the working tree's .claude/skills in.
- The default --permission-mode is acceptEdits: the agent can write files
  but cannot run shell commands, so grading happens only here, externally.
- Grade commands come from the repo-controlled JSON and run with
  shell=True in the worktree, exactly like a Makefile target.

Requires the `claude` CLI on PATH. Exits 1 if any "with"-arm grade fails.
"""

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


__all__ = ["fnMain"]


REPO_ROOT = Path(__file__).resolve().parent.parent
PATH_DEFAULT_EVALS = REPO_ROOT / "tools" / "skillEvals" / "outcomeTasks.json"


def fpathCreateWorktree(sTaskName, sArm):
    """Create a detached git worktree of HEAD and return its path."""
    pathParent = Path(tempfile.mkdtemp(prefix=f"vaibifySkillEval-{sTaskName}-{sArm}-"))
    pathWorktree = pathParent / "tree"
    subprocess.run(
        ["git", "worktree", "add", "--detach", str(pathWorktree), "HEAD"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return pathWorktree


def fnRemoveWorktree(pathWorktree):
    """Unregister and delete a worktree created by fpathCreateWorktree."""
    subprocess.run(
        ["git", "worktree", "remove", "--force", str(pathWorktree)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    shutil.rmtree(pathWorktree.parent, ignore_errors=True)


def fnPrepareSkills(pathWorktree, sSkill, sArm, bUseWorkingSkills):
    """Arrange the worktree's .claude/skills for the requested arm."""
    pathSkillsRoot = pathWorktree / ".claude" / "skills"
    if bUseWorkingSkills:
        shutil.rmtree(pathSkillsRoot, ignore_errors=True)
        shutil.copytree(REPO_ROOT / ".claude" / "skills", pathSkillsRoot)
    if sArm == "without":
        shutil.rmtree(pathSkillsRoot / sSkill, ignore_errors=True)


def fdictRunAgent(pathWorktree, sPrompt, sPermissionMode, iTimeoutSeconds):
    """Run claude -p on the task prompt inside the worktree."""
    listCommand = ["claude", "-p", sPrompt, "--permission-mode", sPermissionMode]
    try:
        result = subprocess.run(
            listCommand,
            cwd=pathWorktree,
            capture_output=True,
            text=True,
            timeout=iTimeoutSeconds,
        )
    except subprocess.TimeoutExpired:
        return {"bCompleted": False, "sOutputTail": f"timed out after {iTimeoutSeconds}s"}
    except FileNotFoundError:
        return {"bCompleted": False, "sOutputTail": "claude CLI not found on PATH"}
    sTail = (result.stdout + result.stderr)[-2000:]
    return {"bCompleted": result.returncode == 0, "sOutputTail": sTail}


def flistGradeTask(pathWorktree, listGradeCommands):
    """Run each grade command in the worktree; return (sCommand, bPassed) tuples."""
    listResults = []
    for sCommand in listGradeCommands:
        result = subprocess.run(
            sCommand, shell=True, cwd=pathWorktree, capture_output=True, text=True
        )
        listResults.append((sCommand, result.returncode == 0))
    return listResults


def fdictEvaluateArm(dictTask, sArm, args):
    """Run one (task, arm) evaluation end to end and return its result record."""
    pathWorktree = fpathCreateWorktree(dictTask["sName"], sArm)
    try:
        fnPrepareSkills(pathWorktree, dictTask["sSkill"], sArm, args.use_working_skills)
        if args.dry_run:
            dictAgent = {"bCompleted": True, "sOutputTail": "(dry run: agent skipped)"}
        else:
            dictAgent = fdictRunAgent(
                pathWorktree, dictTask["sPrompt"], args.permission_mode, args.timeout
            )
        listGrades = flistGradeTask(pathWorktree, dictTask["listGradeCommands"])
    finally:
        if args.keep_worktrees:
            print(f"  worktree kept: {pathWorktree}")
        else:
            fnRemoveWorktree(pathWorktree)
    return {"sArm": sArm, "dictAgent": dictAgent, "listGrades": listGrades}


def fnReportArm(dictResult):
    """Print the per-arm agent status and grade breakdown."""
    iPassed = sum(1 for _, bPassed in dictResult["listGrades"] if bPassed)
    iTotal = len(dictResult["listGrades"])
    sAgentStatus = "ok" if dictResult["dictAgent"]["bCompleted"] else "FAILED"
    print(f"  arm={dictResult['sArm']}: agent {sAgentStatus}, grades {iPassed}/{iTotal}")
    for sCommand, bPassed in dictResult["listGrades"]:
        print(f"    [{'PASS' if bPassed else 'FAIL'}] {sCommand}")
    if not dictResult["dictAgent"]["bCompleted"]:
        print(f"    agent output tail: {dictResult['dictAgent']['sOutputTail']}")


def fnMain():
    """Evaluate the selected tasks and arms; exit 1 if a with-arm grade fails."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--evals", type=Path, default=PATH_DEFAULT_EVALS)
    parser.add_argument("--task", default="", help="run only the named task")
    parser.add_argument("--arm", choices=["with", "without", "both"], default="both")
    parser.add_argument("--permission-mode", default="acceptEdits")
    parser.add_argument("--timeout", type=int, default=3600)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--keep-worktrees", action="store_true")
    parser.add_argument("--use-working-skills", action="store_true")
    args = parser.parse_args()

    listTasks = json.loads(args.evals.read_text(encoding="utf-8"))["listOutcomeTasks"]
    if args.task:
        listTasks = [d for d in listTasks if d["sName"] == args.task]
        if not listTasks:
            print(f"No task named {args.task!r} in {args.evals}")
            return 1
    listArms = ["with", "without"] if args.arm == "both" else [args.arm]
    bWithArmFailed = False
    for dictTask in listTasks:
        print(f"Task {dictTask['sName']} (skill: {dictTask['sSkill']}):")
        for sArm in listArms:
            dictResult = fdictEvaluateArm(dictTask, sArm, args)
            fnReportArm(dictResult)
            bArmPassed = dictResult["dictAgent"]["bCompleted"] and all(
                bPassed for _, bPassed in dictResult["listGrades"]
            )
            if sArm == "with" and not bArmPassed and not args.dry_run:
                bWithArmFailed = True
    return 1 if bWithArmFailed else 0


if __name__ == "__main__":
    sys.exit(fnMain())
