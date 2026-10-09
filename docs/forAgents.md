# For Agents

AI coding agents work with vaibify in two places. Inside a vaibify
container, an agent helps a researcher build and run a project, and
vaibify gives it a harness. Outside the container, agents write vaibify
itself, and the last part of this page describes the documentation
method that keeps that work honest.

## The vaibify harness

The harness arrives in three layers. The **container system prompt**
explains vaibify, the rules of the container, the PROOF ladder, and
when to use a deterministic action instead of improvising. **Skills**
are task recipes an agent loads only when a task matches (see
[Shipped agent skills](#shipped-agent-skills)). The **Agent Council**
has its own charter for deliberations among several models (see
[Agent Council](agentCouncil.md)). All three describe how to
work inside vaibify, not how to do any particular science.

### The container system prompt

Every time a container starts, vaibify regenerates `CLAUDE.md` at the
root of `/workspace` and links `AGENTS.md` and `GEMINI.md` there to it,
so every installed agent reads the same text. Because it is rewritten
on every start, edits to it do not survive a restart. Its main rules:

- **Name steps by label.** `A09` is the ninth *automated* step and
  `I01` the first *interactive* step. Labels are per-type sequential,
  not positions in the step list, so an agent uses them verbatim.
- **Act through `vaibify-do`.** Anything the researcher could do with a
  dashboard button goes through the action catalog (see
  [Agent actions](#agent-actions)), so the dashboard stays true.
- **Compute with saved scripts.** A numeric answer is delivered as a
  saved script that could become a step, never as a heredoc, one-liner,
  or REPL session.
- **Declare cross-step files as tokens.** A file a script reads from
  another step is named in the step's command as a
  `{step:<sStepId>.<stem>}` token, so the dependency graph is complete.
- **Ask the backend for the PROOF level.** `iProofLevel` from
  `vaibify-do check-l2-readiness` is the only authoritative signal.
- **Leave publication to the researcher.** Pushing to Overleaf,
  publishing to Zenodo, and accepting figures as the standard are
  user-only.
- **Ask for dependencies.** The agent cannot change the project's
  `vaibify.yml`, which the image is built from, so it names the package
  the researcher should add and says that its own `pip install` lasts
  only for the current container session.
- **Report in the researcher's words**: step labels, badge names, and
  the dashboard's own remediation sentences, not schema field names.

The full text is on the [System Prompt](systemPrompt.md) page.

### Your own instructions

The researcher's standing instructions to the agent live in the
**project context file**, `.vaibify/AGENTS.md` in the project
repository. It is versioned with the repository and is part of the
provenance record. The dashboard can write a starter template (what the
project is, where its data comes from, its conventions, and what the
agent must never touch) or import an existing file, and an agent can
read or update it with the `read-project-context` and
`update-project-context` actions.

At each start vaibify links `CLAUDE.md`, `AGENTS.md`, and `GEMINI.md`
at the repository root to that file (and adds a `.clinerules/` entry
when Cline is installed), but only where no file exists. A real file at
one of those names belongs to the researcher and is never touched. The
container prompt describes the container; the project context file
describes the project, and it is the one file a researcher needs to add
to give the agent their own rules.

Separately, PROOF Level 2 asks whether the researcher's private,
host-side agent setup (global instructions, personal skills, memory,
hooks) governed the work. That **Personal AI Configuration** row passes
with any answer — `none`, `declared-private`, or `included` — and fails
only when unanswered. It is user-only. See
[The PROOF Ladder](proofLadder.md).

## Agent actions

An agent inside the container can ask the dashboard to perform named
operations on the researcher's behalf. These *agent actions* bridge the
agent's text-only world and the dashboard's verified state, and they
make the agent's behavior deterministic: asked to build unit tests, the
agent runs the same generator the button runs rather than inventing
tests of its own. Every action an agent takes is one the researcher
could have taken, and it triggers the same verification, so the
dashboard never drifts from what the agent actually did.

Every state-changing operation in the dashboard — running a step,
generating tests, committing, pushing to GitHub, archiving to Zenodo —
is registered in one catalog, `LIST_AGENT_ACTIONS` in
`vaibify/gui/actionCatalog.py`. Each entry carries a stable name, the
call the dashboard itself makes, a description of its arguments, and a
`bAgentSafe` flag. The agent never invents an action: it picks one from
the catalog, or it tells the researcher what it ran in the shell.

### Using `vaibify-do`

`vaibify-do` is the in-container command that reads the catalog and
sends an action to the vaibify hub on the host. The hub writes the
catalog and a per-container session token into the container when the
dashboard connects to it.

```bash
vaibify-do --list                 # every action, with its description
vaibify-do --describe run-step    # one action's arguments
vaibify-do run-step A03           # run one step
vaibify-do run-selected-steps A03 A04 A05
```

Step arguments accept labels. Other arguments go as `key=value`,
`--long-flag=value`, or a JSON object. `--dry-run` prints the call
without sending it and `--json` emits line-delimited JSON; both must
come before the action name. The dashboard updates as the action runs.

`vaibify-do` acts on the project open in the dashboard, and every
response begins with a line naming that project. An agent working in a
different project's directory is refused with `project-mismatch`, and
nothing happens; the remedy is to ask the researcher to open the right
project. If `vaibify-do` reports that the session is not initialized or
the host is unreachable, the dashboard is not connected to this
container, and the researcher reconnects by clicking the container.

### Agent-safe and user-only actions

An action with `bAgentSafe` false is **user-only**: a decision only the
researcher may make, such as stopping a running pipeline, deleting a
step, publishing to Zenodo, pushing to Overleaf, accepting figures as
the standard, or answering a consent question. `vaibify-do --list`
shows which is which. `vaibify-do` refuses a user-only action with a
JSON object carrying `sRefusal: "user-only-action"`; the agent does not
retry, and instead asks the researcher to click the matching button.

That client-side check is a courtesy, not the protection. The hub
enforces `bAgentSafe` on every HTTP request carrying an agent's session
token, so a user-only route answers 403 even when an agent calls it
directly. The check **fails closed**: a state-changing route with no
catalog entry is refused on the agent lane, so a route stays out of an
agent's reach until someone deliberately registers it as agent-safe.
Routes that change the hub's own registry or preferences are excluded
outright. The test suite fails if any state-changing route is neither
registered nor explicitly excluded (`testAgentActionRegistered`).

Some agent-safe actions still stop for the researcher. A run that would
re-pull remote data over the committed copy is refused with
`remoteDataOverwrite`; the agent relays the question and re-issues the
command with `--confirm-remote-overwrite` only after an explicit yes.

The same catalog generates the researcher's own command-line
counterpart on the host, described in the [CLI Reference](cli.md). It
authenticates as the researcher, so user-only actions are available
there: `bAgentSafe` constrains an agent in a container, not the
researcher at their own terminal.

## Shipped agent skills

At every container start, vaibify copies its skills into the skills
directory of each installed agent that supports them. The copy replaces
vaibify's own skill files each time, so put a customized recipe in a
skill with its own name.

- **create-pipeline-step** — author a fully wired step: dependencies,
  naming, the `{step:<sStepId>.<stem>}` token contract, the project
  entry, and verification.
- **running-steps** — run steps through `vaibify-do`, never by
  launching a step's script in a shell, which the dashboard cannot see.
- **reproducible-analysis** — answer a quantitative question with a
  saved script structured so it can become a step.
- **proof-ladder** — raise or audit a project's PROOF level through
  Level 3, with the known audit traps.
- **diagnose-failed-run** — triage a run that died or hung with the
  read-only `get-pipeline-state` and `get-host-log-tail` actions.
- **read-manuscript** — pull the project's Overleaf manuscript (the
  `pull-manuscript` action) into a git-ignored copy and read it, rather
  than answering from memory.
- **read-arxiv** — read a paper from its arXiv TeX source instead of
  the PDF, which costs far fewer tokens.
- **session-budget** — keep long autonomous runs alive across usage
  limits with checkpoint commits, a resume note, and a pause until the
  usage window resets (at 95% by default, adjustable in the prompt).
- **vaibify-doc-map** — point the agent at the right section of
  vaibify's documentation, staged in the container under
  `/usr/share/vaibify/docs`, instead of a whole file.

## Developing vaibify with agents

vaibify is written by AI coding agents directed by its author. Agents
are stochastic — the same prompt, run twice, can produce different
code — and the most dangerous mistakes are the plausible ones. This
part describes how vaibify documents itself for agents so that the
documentation does not drift from the code. The method applies to any
repository.

### Why agent documentation drifts

The natural response to an unreliable agent is more documentation:
where everything lives, what modules are called, how large each file
is. Then a module is split, and the documentation becomes quietly
wrong. An agent can trust the stale facts and write confidently wrong
code, or ignore them and re-derive the architecture every session. The
root cause is a document doing two jobs: stating rules that cannot be
tested, and reciting facts that should never be typed by hand.

### Deterministic and stochastic documentation

**Deterministic signals** are facts the code unambiguously is: the
modules in a package, their exported symbols, whether a test exists.
Written by hand, they become a second source of truth that will
diverge. **Stochastic signals** are rules, contracts, and hazards that
span files and cannot be read from any one of them, such as "container
paths use `posixpath`; host paths use `os.path`." Only someone who
knows the system can write them, and they matter because an agent
cannot infer them. Mixing the two buries the rules under rotting facts,
as if a simulation were asked to invent its own priors.

### The layered framework

| Layer | What it is | Trigger | Source of truth | Failure mode |
|---|---|---|---|---|
| 1 | Short prose of untestable rules (`AGENTS.md`) | Every task | Hand-written prose | Silent |
| 2 | Architectural invariants written as tests | Every commit | The assertion | Loud (CI fails) |
| 3 | Scripts that report structure from the live tree | On demand | The current source | At the point of use |
| 4 | Multi-step recipes loaded on a task match (skills) | Task match | Hand-written prose | Silent |

Layers 2 and 3 cannot drift, so they carry the deterministic content;
Layers 1 and 4 carry only what is irreducibly stochastic. Layer 1 stays
small because a wrong rule there is trusted on every task, and a skill
that fires on every task is Layer 1 in a costume.

### The scoping test

Before adding to an agent-facing document, ask in order:

1. Would a new developer, reading the code for twenty minutes, miss
   this? If not, do not write it.
2. Can it be an assertion on the code? Then write a test.
3. Can it be extracted from the code? Then write a script, and do not
   persist its output.
4. Otherwise, a single rule goes in Layer 1 and a recurring recipe in
   Layer 4.

### Traps over rules

The most valuable prose is a list of *traps*: places where the code
does the opposite of what a careful reader expects, or where two
look-alikes behave differently. A good entry names both, says which
does what, and gives the consequence. For example, a test double that
stores a written file without consulting the authorization gate the
real connection consults lets a route lose its authorization while its
tests stay green. To start an `AGENTS.md`, write the five mistakes you
would most hate to see an agent make next week.

### The feedback principle

When an agent repeats a mistake, the documentation absorbs it: first
by promoting it to a test, so CI catches any repeat; otherwise by
writing it where its reach belongs — a subsystem's skill,
[lessons](lessons.md) for how a mistake happened, and `AGENTS.md` only
for a rule that governs every edit, since its length taxes every task;
and, for a multi-step task done inconsistently, by adding a skill.

A test enforces a rule only if it can observe the rule being broken.
Break the rule on purpose and watch the test fail; if it passes, the
test's premise is wrong. Assert on the state the rule is about, not
merely that nothing raised. A guard nobody has watched fail is a guard
nobody has tested.

### Tradeoffs and limits

Rules about intent, taste, and scientific meaning resist assertion and
stay as prose. Short module docstrings drift less than long ones, so
long docstrings are reserved for behavior a reader cannot infer from
the functions. A correct, longer `AGENTS.md` beats a short one missing
load-bearing context, but every sentence is a future drift liability.
Skills are tool-specific; for portability across agent tools, rely on
`AGENTS.md`, tests, and scripts.

### A playbook for your own repository

1. Write down the traps first; they are the core of `AGENTS.md`.
2. Turn every invariant you can into a test in one invariants file.
3. Write a script that prints the module map on demand instead of a
   hand-written map.
4. Link `CLAUDE.md`, and any other filename your tools read, to
   `AGENTS.md`.
5. Add a CI check that fails when an agent document names a missing
   path.
6. Apply the feedback principle whenever you correct an agent twice.

### How this repository applies it

- **Layer 1.** The root `AGENTS.md` (`CLAUDE.md` links to it) holds the
  rules for every edit and opens with a table routing each subsystem to
  the skill that governs it. Nested `AGENTS.md` files under
  `vaibify/gui/` and `vaibify/gui/static/` hold backend and frontend
  conventions.
- **Layer 2.** `tests/testArchitecturalInvariants.py` enforces the
  structural rules (route modules do not import one another, shipped
  source names no specific science, every state-changing route is in
  the agent action catalog), and `tests/testStyleInvariants.py`
  enforces the naming contract, with existing exceptions held under
  budgets that may only fall.
- **Layer 3.** `python tools/listModules.py <subtree>` prints the
  current module map with public symbols and one-line purposes, and
  `tools/checkAgentDocsPaths.py` fails CI when an agent document names
  a missing path.
- **Layer 4.** The subsystem skills live under `.claude/skills/`.
  `tests/testSkillIntegrity.py` checks their structure, and harnesses
  in `tools/` test whether each triggers and performs as intended (see
  [Skill testing](skillTesting.md)).
- **Falsification.** Each falsification test records the mutation it
  must catch, and `tools/reconfirmFalsification.py` applies those
  mutations in a disposable worktree to confirm each test still fails.

Documentation that drifts from code is a reproducibility hazard. The
discipline that keeps an `AGENTS.md` correct is the same one that keeps
a methods section correct.

### Developer references

```{toctree}
:maxdepth: 1

developers
architecture
lessons
knownDebt
skillTesting
```
