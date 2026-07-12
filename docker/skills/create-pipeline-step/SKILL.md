---
name: create-pipeline-step
description: Author a new, fully wired vaibify pipeline step — scripts, declared outputs, cross-step dependency tokens, and the workflow JSON entry. Use whenever asked to create a new analysis, computation, or plot in a vaibify workflow.
---

# Creating a new pipeline step

The goal is a fully wired step with zero untracked files and a
dependency graph the backend can parse mechanically.

## Phase 1: Discover context

1. Find the workflow JSON:
   `find /workspace -maxdepth 4 -path '*/.vaibify/workflows/*.json'`
2. Read `listSteps`: existing steps, their outputs, available
   variables.
3. Backward dependencies: which steps' `saDataFiles` produce the
   inputs this step needs?
4. Forward dependents: search all steps for `{StepNN.*}` references.
   **Strongly prefer appending** at the end — inserting renumbers
   every downstream `{StepNN.*}` token; if insertion is unavoidable,
   enumerate every reference that must change and confirm with the
   researcher first.

## Phase 2: Name and structure

- camelCase directory named for the scientific goal, not the method;
  no abbreviations for words under 8 characters.
- Scripts: `data<Purpose>.py` (analysis) and `plot<Purpose>.py`
  (visualization). Data outputs stay in the step directory; plots go
  to `{sPlotDirectory}/`.

## Phase 3: The cross-step token contract (non-negotiable)

Every file the script reads from ANOTHER step must be a CLI argument,
referenced in the workflow command via a `{StepNN.varname}` token. A
hardcoded cross-step path (`open("../OtherStep/output.json")`) is
invisible to the dependency parser and silently breaks the L1
contract. Own-step files may be hardcoded; the boundary is the step.

- Argument names kebab-case (`--flare-samples`); the matching token
  snake_case (`{Step02.flare_samples}`) — the token's varname is the
  extensionless basename of the producer's `saDataFiles` entry.
- Use argparse, never raw sys.argv, so the contract is explicit.
- When two producers declare colliding basenames, use the qualified
  token form (producer-prefixed); the workflow's `saDependencies`
  list is the escape hatch for edges the data flow does not express.

Worked example — producer declares, consumer tokenizes:

```json
{"iIndex": 2, "sName": "KeplerFfd",
 "saDataCommands": ["python dataKeplerFfd.py"],
 "saDataFiles": ["flare_samples.npy"]}
{"iIndex": 3, "sName": "FfdAgeComparison",
 "saPlotCommands": ["python plotFfd.py --flare-samples {Step02.flare_samples} {sPlotDirectory}/ffd.{sFigureType}"]}
```

## Phase 4: Scripts and the workflow entry

Scripts follow the repo style guide (read the repo CLAUDE.md first):
Hungarian notation, return-type function prefixes, functions ~20
lines, `import vplot` for any matplotlib figure.

Workflow entry rules:
- EVERY output file declared in `saDataFiles` or `saPlotFiles` — no
  untracked outputs.
- `saTestCommands` should include at least a basic sanity check.
- `bPlotOnly: true` only when the step has no data commands;
  `bInteractive: true` only for human-judgment steps.
- Add the entry via `vaibify-do create-step` (see
  `vaibify-do --describe create-step` for the schema) so it passes
  schema validation and atomic save.

## Phase 5: Verify

1. Run the data script; run the plot script; confirm outputs land at
   the declared paths.
2. `vaibify-do run-step <label>` then `vaibify-do verify-only`.
3. Report the step LABEL (`A07`, not an index), directory, and output
   files to the researcher.

## Caveats

- Step labels are per-type sequential (`A09` is the 9th automated
  step, not `listSteps[9]`) — always report `sLabel` verbatim.
- Missing Python packages: `pip install` immediately, then persist in
  `<repo>/.vaibify/requirements.txt` with a version lower bound — the
  in-container install is lost at rebuild.

The full authoring reference (naming, worked examples, colliding
basenames, CI enforcement) is `vaibify/docs/scriptAuthoring.md`,
staged in-container at `/usr/share/vaibify/docs/scriptAuthoring.md`
(see the vaibify-doc-map skill).
