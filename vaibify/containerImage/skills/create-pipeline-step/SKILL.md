---
name: create-pipeline-step
description: Author a new, fully wired vaibify pipeline step — scripts, declared outputs, cross-step dependency tokens, and the project JSON entry. Use whenever asked to create a new analysis, computation, or plot in a vaibify project.
---

# Creating a new pipeline step

The goal is a fully wired step with zero untracked files and a
dependency graph the backend can parse mechanically.

## Phase 0: Is there a project at all?

A step needs a project to live in. If the work is sitting in a plain
workspace directory — one you or the researcher created, with no
`project.json` — adopt the directory first:

```
vaibify-do adopt-directory-as-project '{"sDirectory": "myAnalysis",
  "sProjectName": "Name the toolbar should show"}'
```

One action does the whole thing: makes the directory a git repository
if it is not one, gives it a commit if it has none, writes the
`project.json`, and registers the repository so the dashboard offers
the project. `sDirectory` must already exist, directly under
`/workspace` — adoption never creates it, so a typo refuses instead of
producing an empty project beside the real one.

It is idempotent. A re-run reports `saStagesPerformed` and
`saStagesAlreadySatisfied` instead of refusing, and never overwrites an
existing project file, so retrying after an unclear answer is safe. A
directory that already hosts a project reports that project rather than
adding a second one.

**Never hand-write `project.json` to create a project.** The file will
be real and the repository will still be unregistered, so every
workflow action refuses with "No project is open" and the researcher
ends up with directories they cannot open. When there is no directory
to adopt, `vaibify-do create-project` asks the researcher to create the
project in their browser; it creates nothing itself. Never hand-edit an
existing `project.json` either: change it through `vaibify-do` actions
(see the running-steps skill).

Two things to tell the researcher afterwards, because you cannot do
them:

- **They open the project** from the Project field in the toolbar.
  Adoption cannot open it for them; say so explicitly rather than
  reporting the project as ready to use.
- If adoption refuses, relay its `sMessage` and `sRemedy` verbatim.
  Every refusal names the next action, and the `sRefusal` code is
  stable enough to branch on.

## Phase 1: Discover context

1. Find the project JSON:
   `find /workspace -maxdepth 4 \( -path '*/.vaibify/projects/*.json' -o -path '*/.vaibify/workflows/*.json' \)`
2. Read `listSteps`: existing steps, their outputs, available
   variables.
3. Backward dependencies: which steps' `saOutputDataFiles` produce the
   inputs this step needs?
4. Forward dependents: search all steps for `{step:<sStepId>.`
   references to the outputs you will touch. A token names the
   producer's `sStepId`, which is assigned once and never changes, so
   neither appending (`create-step`) nor inserting (`insert-step`)
   rewrites a reference; a consumer must still sit after every step it
   reads. Appending changes no existing label, while inserting
   renumbers the labels of later steps of the same type, so prefer
   appending and tell the researcher when you insert. Reordering
   (`reorder-steps`) is user-only.

## Phase 2: Name and structure

- Name the step for the scientific goal, not the method; no
  abbreviations for words under 8 characters. Names allow letters,
  digits, spaces and hyphens. The directory's basename is derived from
  the name — each word capitalized and joined, so `Posterior Samples`
  becomes `PosteriorSamples` — and the server corrects any other
  basename you send.
- Scripts: `data<Purpose>.py` (analysis) and `plot<Purpose>.py`
  (visualization). Data outputs stay in the step directory; plots go
  to `{sPlotDirectory}/`.

## Phase 3: The cross-step token contract (non-negotiable)

Every file the script reads from ANOTHER step must be a CLI argument,
referenced in the project command via a `{step:<sStepId>.<stem>}` token. A
hardcoded cross-step path (`open("../OtherStep/output.json")`) is
invisible to the dependency parser and silently breaks the L1
contract. Own-step files may be hardcoded; the boundary is the step.

- Argument names kebab-case (`--posterior-samples`); the matching token
  snake_case (`{step:posterior-samples.posterior_samples}`) — the token's stem is the
  extensionless basename of the producer's `saOutputDataFiles` entry.
- Use argparse, never raw sys.argv, so the contract is explicit.
- Outputs of different steps never collide, because the token names
  the step. When ONE step declares two outputs with the same basename,
  each registers under a qualified stem (its leading directory joined
  with an underscore: `ModelA/summary.json` is `ModelA_summary`). The
  project's `saDependencies` list is the escape hatch for edges the
  data flow does not express.

Worked example — producer declares, consumer tokenizes. Each object is
the argument of one `vaibify-do create-step '<json>'` call, producer
first:

```json
{"sName": "Posterior Samples", "sDirectory": "PosteriorSamples",
 "bNoInputData": true,
 "saDataCommands": ["python dataPosteriorSamples.py"],
 "saOutputDataFiles": ["posterior_samples.npy"]}
{"sName": "Posterior Corner", "sDirectory": "PosteriorCorner",
 "bPlotOnly": true, "bNoInputData": true,
 "saPlotCommands": ["python plotCorner.py --posterior-samples {step:posterior-samples.posterior_samples} {sPlotDirectory}/corner.{sFigureType}"],
 "saPlotFiles": ["{sPlotDirectory}/corner.{sFigureType}"]}
```

`sName` and `sDirectory` are required. You do not send an `sStepId`:
the server derives it from the name (lowercased, each run of other
characters replaced by one hyphen, so `Posterior Samples` is
`posterior-samples`, with a numeric suffix if that id is taken) and
returns it in the response's `dictStep`. Read the id there before
writing a token that names it.

## Phase 4: Scripts and the project entry

Scripts follow the repo style guide (read the repo CLAUDE.md first):
Hungarian notation, return-type function prefixes, functions ~20
lines.

Project entry rules:
- EVERY output file declared in `saOutputDataFiles` or `saPlotFiles` — no
  untracked outputs.
- Tests: once the step has run,
  `vaibify-do generate-tests-deterministic <label>` writes its
  integrity, qualitative and quantitative tests (the categories in
  `dictTests`) from what the outputs actually contain. Never fabricate
  a trivial test to fill a category.
- `bPlotOnly` defaults to `false`. Set it `true` only when the step
  has no data commands, because a plots-only step skips its data
  commands on every run. `bInteractive: true` only for human-judgment
  steps. Send `bNoInputData: true` when the step reads no raw input
  data.
- Add the entry via `vaibify-do create-step` (see
  `vaibify-do --describe create-step` for the schema) so it passes
  schema validation and atomic save.

## Phase 5: Verify

1. `vaibify-do run-step <label>` — never the scripts directly (see the
   running-steps skill). It runs the data commands, then the plots,
   then the tests. Confirm the outputs land at the declared paths.
2. Generate the tests (Phase 4), run them with
   `vaibify-do run-unit-tests <label>`, then `vaibify-do verify-only`.
3. Report the step LABEL (`A07`, not an index), directory, and output
   files to the researcher.

## Caveats

- Step labels are per-type sequential (`A09` is the 9th automated
  step, not `listSteps[9]`) — always report `sLabel` verbatim.
- Missing Python packages: `pip install` lasts for this container
  session only, and you cannot make it permanent. The one declaration
  is `pythonPackages` in the project's `vaibify.yml`, which the
  researcher edits and which takes effect when the image is rebuilt.
  `<repo>/.vaibify/requirements.txt` is a generated mirror rewritten
  on every container start, so editing it changes nothing. Name the
  package, a version lower bound and why, and ask the researcher to
  add it.

The full authoring reference (naming, worked examples, colliding
basenames, CI enforcement) is `vaibify/docs/scriptAuthoring.md`,
staged in-container at `/usr/share/vaibify/docs/scriptAuthoring.md`
(see the vaibify-doc-map skill).
