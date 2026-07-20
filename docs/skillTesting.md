# Skill Testing

Vaibify ships agent skills — procedural recipes under `.claude/skills/`
that teach an AI agent how to perform recurring extension tasks (adding
a route module, adding a data loader). A skill is documentation that an
agent *executes*, so it fails the way documentation fails: silently,
by drifting out of sync with the code it describes. And because a skill
is consumed by a model rather than compiled, nothing crashes when it
rots — the agent just follows stale instructions with full confidence.

There is no built-in framework for testing skills. Anthropic's
[skill-authoring guidance](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices)
recommends evaluation-driven development — establish a baseline without
the skill, write evaluations, iterate against real executions — and
distinguishes two separate questions:

1. **Triggering** — does the agent invoke the skill when it should, and
   stay quiet when it shouldn't? Only the frontmatter `description` is
   visible at decision time, so this is a property of the description.
2. **Execution** — does following the skill produce a correct result?

Vaibify implements that guidance as three layers, cheapest first. Each
layer answers a question the previous one cannot.

| Layer | Question | Where | Cost | When it runs |
|---|---|---|---|---|
| Referential integrity | Do the skill's references still exist? | `tests/testSkillIntegrity.py` | Free | Every `pytest` run and CI |
| Trigger evaluation | Does the description trigger correctly? | `tools/evaluateSkillTriggers.py` | One short model call per prompt | After editing a description |
| Outcome evaluation | Does the skill produce correct results? | `tools/evaluateSkillOutcomes.py` | Full agent run + full suite per task per arm | Before merging skill changes |

## Layer 1: referential integrity (CI)

The dominant failure mode for a recipe skill is staleness: a refactor
renames `fsReadLoaderSource` or moves a module, and the skill keeps
pointing at the old name. `tests/testSkillIntegrity.py` makes that
drift a test failure:

- **Frontmatter** — the `name` matches the skill's directory, and the
  `description` states an explicit trigger condition ("Use when …"),
  because the description is the only text the model sees when deciding
  whether to load the skill.
- **Paths** — every file path a skill references still exists. This
  delegates to `tools/checkAgentDocsPaths.py`, the same checker the
  `agent-docs-path-check` CI workflow runs on `AGENTS.md`, so path
  logic lives in exactly one place. Illustrative filenames that
  intentionally do not exist belong in that tool's
  `SET_GENERIC_FILENAME_EXAMPLES` allow-list.
- **Symbols** — every Hungarian-notation identifier named in prose
  (`fnRegisterAll`, `WORKSPACE_ROOT`) still occurs in the source tree.
  Fenced code blocks are excluded, because scaffolds legitimately use
  made-up names; placeholders like `_fLoad<FormatName>` are ignored.
- **Test names** — every test a skill tells the agent to run
  (`testAgentActionRegistered`) is still defined under `tests/`.

The extraction rules themselves have a negative control
(`testSymbolExtractorIgnoresScaffoldsAndPlaceholders`): if the
extractor ever starts flagging scaffold code, that test fails before
every skill does.

These tests are part of the normal suite — no extra invocation is
needed beyond `python -m pytest tests/ -q`.

## Layer 2: trigger evaluation

```bash
python tools/evaluateSkillTriggers.py
```

The harness reads every skill's description, then for each prompt in
`tools/skillEvals/triggerPrompts.json` asks a fresh model instance
which skill (or `none`) it would invoke, and compares against the
expected answer. The prompt set contains both should-trigger requests
and near-misses that must *not* trigger (frontend work, documentation
fixes, questions) — false triggers are as damaging as missed ones,
because a wrongly-loaded recipe steers the agent into the wrong
procedure.

This is deliberately an approximation: it classifies from the
descriptions alone rather than driving the full interactive harness.
That is the same isolation Anthropic's description-optimization
procedure uses, and it is what makes the layer cheap enough to run
after every description edit. Run it whenever a `description:` line
changes or a new skill is added; it exits 1 on any mismatch.

## Layer 3: outcome evaluation

```bash
python tools/evaluateSkillOutcomes.py                       # all tasks, both arms
python tools/evaluateSkillOutcomes.py --task iniLoader --arm with
python tools/evaluateSkillOutcomes.py --use-working-skills  # uncommitted skill edits
```

For each task in `tools/skillEvals/outcomeTasks.json`, the harness
creates a throwaway git worktree from `HEAD`, runs `claude -p` on the
task prompt inside it, and grades the result by running the task's
grade commands in the worktree — the repository's own test suite is
the oracle, which is the hard part of skill evaluation and the part
vaibify gets for free from its architectural invariants. Each task
runs in two arms:

- **with** — the skill is present. All grade commands must pass; a
  failure means the skill did not steer the agent to a correct result,
  and the harness exits 1.
- **without** — the skill's directory is deleted from the worktree
  first, establishing the baseline the authoring guidance calls for.
  A without-arm that passes anyway is not a failure, but it is
  evidence the skill may not be earning its keep.

The task set follows the falsification philosophy of the rest of the
suite (see [Testing](testing.md)): alongside straightforward tasks, it
includes adversarial ones designed to tempt the agent into a skill's
documented failure modes — for example, a loader whose format needs an
optional dependency, where the grade commands check that the import
landed *inside* the function body rather than at the top of the
embedded loader source. A skill that only passes friendly tasks has
not been tested.

Practical notes:

- Worktrees materialize `HEAD`. To evaluate uncommitted skill edits,
  pass `--use-working-skills`, which copies the working tree's
  `.claude/skills/` into the worktree after creation.
- The default `--permission-mode acceptEdits` lets the agent write
  files but not run shell commands, so all grading happens externally
  and deterministically. Pass a different mode explicitly if a task
  genuinely requires the agent to execute commands, and understand
  what that mode permits before doing so.
- `--dry-run` exercises the harness mechanics (worktree lifecycle, arm
  preparation, grading) without any model calls.
- This layer runs a full agent session plus the full test suite per
  task per arm. It is a periodic audit — run it before merging changes
  to a skill, not in CI.

## Adding a new skill

1. Write `.claude/skills/<skill-name>/SKILL.md` with frontmatter
   `name:` matching the directory and a `description:` that states
   what the skill does *and* its trigger condition ("Use when …").
2. Run `python -m pytest tests/testSkillIntegrity.py -v` — the
   integrity tests discover the new skill automatically and will fail
   on any stale reference from day one.
3. Add trigger prompts to `tools/skillEvals/triggerPrompts.json`:
   several requests that should trigger the skill, and several
   near-misses that should not. Run the layer-2 harness.
4. Add at least one outcome task to
   `tools/skillEvals/outcomeTasks.json`, including an adversarial one
   aimed at the skill's most likely failure mode, with grade commands
   that make the repository's tests the oracle. Run the layer-3
   harness before merging.

## What each layer does and does not prove

Layer 1 proves the skill's references are live, not that its procedure
is correct. Layer 2 proves the description is discriminative in
isolation, not that the full harness will load it. Layer 3 is the only
layer that observes real behavior, and even it proves correctness only
for the tasks in the eval set. A skill that passes all three layers is
consistent with the codebase and useful on the evaluated tasks —
treat any claim beyond that as asserted, not verified.

## References

- [Skill authoring best practices — evaluation and iteration](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices)
  (Anthropic): evaluation-driven development, trigger vs. execution
  testing, baseline-first workflow.
- [anthropics/skills](https://github.com/anthropics/skills): the
  `skill-creator` skill, Anthropic's own evaluation tooling — A/B
  with/without comparison, grader agents, description optimization
  over trigger/non-trigger query sets.
- [Equipping agents for the real world with Agent Skills](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills)
  (Anthropic engineering): the design rationale for skills.
