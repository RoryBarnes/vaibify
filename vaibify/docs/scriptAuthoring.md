# Authoring scripts inside a vaibify project

This document explains the contract that vaibify expects scripts inside
a project to follow. The contract is small, mechanical, and exists for
exactly one reason: the project JSON must describe the complete
dependency graph so the dashboard can honestly track reproducibility.

## The contract

A vaibify project is a `project.json` plus a directory of scripts.
The JSON declares each step, the commands that produce its outputs, and
the files those outputs are stored at. Vaibify's dashboard parses the
JSON to:

1. Build the dependency graph between steps.
2. Decide when a step is stale because an upstream changed.
3. Decide when the project has reached AICS Level 1, 2, or 3.

The parser at
`vaibify/gui/workflowManager.py::fdictBuildDirectDependencies` uses one
mechanism to find dependencies: it scans command strings for
`{StepNN.varname}` tokens. Anything that token-references another step
is part of the graph. Anything else is not.

This is the contract: **every cross-step file your script reads must
arrive as a CLI argument, and the project JSON command must reference
it via a `{StepNN.varname}` token.**

## Why hardcoded cross-step paths break vaibify

Suppose step A02 produces `flare_samples.npy` and step A03 needs to
read it. The "wrong" pattern looks like this:

```python
# A03/plotFfd.py
import numpy as np
samples = np.load("../KeplerFfd/flare_samples.npy")  # hardcoded
```

vaibify's parser cannot introspect arbitrary Python source. The
`"../KeplerFfd/flare_samples.npy"` literal is invisible. So the A02 →
A03 edge does not exist in the dependency graph. The consequences:

- The dashboard's `Update Dependencies` button never finds the edge.
- When A02 is re-run, A03's `bUpstreamModified` flag does not fire —
  the dashboard thinks A03 is still consistent with A02 even though it
  isn't.
- A03 can show as Level 1 verified while its plot is based on a stale
  flare-samples file.
- The project can therefore claim Self-Consistent (L1) status when it
  is not, in fact, self-consistent.

This is not a missing feature — it is a deliberate boundary. Vaibify
cannot guarantee what arbitrary Python will read at runtime. The only
honest path is to require the user to declare it.

## The CLI argument convention

The right pattern lifts the cross-step reference into a CLI argument
and a JSON token:

```python
# A03/plotFfd.py
import argparse
import numpy as np

def fdictParseArguments():
    parser = argparse.ArgumentParser()
    parser.add_argument("--flare-samples", required=True)
    parser.add_argument("sPlotPath")
    return vars(parser.parse_args())

dictArgs = fdictParseArguments()
samples = np.load(dictArgs["flare_samples"])
```

```json
{
  "iIndex": 3,
  "sName": "FfdAgeComparison",
  "saPlotCommands": [
    "python plotFfd.py --flare-samples {Step02.flare_samples} {sPlotDirectory}/ffd.{sFigureType}"
  ]
}
```

Three conventions matter:

- **CLI arguments are kebab-case** (`--flare-samples`).
- **The token variable name is snake_case** (`{Step02.flare_samples}`)
  — it matches the basename (without extension) of the producer step's
  `saOutputDataFiles` entry. So `flare_samples.npy` in `saOutputDataFiles` becomes
  `{Step02.flare_samples}` in any consumer's command.
- **Use `argparse`, not raw `sys.argv` indexing.** The CLI is part of
  the project contract; argparse makes it explicit and self-documenting.

The director substitutes `{Step02.flare_samples}` at runtime with the
actual repo-relative path to the producer's output. Your script never
needs to know where A02 lives.

## `saDependencies` — the escape hatch

Sometimes the data flow does not naturally express the dependency. The
most common case: a plot script reads a sibling step's output but the
project command does not "naturally" thread that path as a CLI
argument (for instance, the script reads many sibling outputs whose
names follow a pattern).

For those cases, the project JSON has an explicit `saDependencies`
field. List one or more `{StepNN.*}` tokens there and the parser will
register the edge:

```json
{
  "iIndex": 4,
  "sName": "AggregatePlots",
  "saPlotCommands": ["python plotAggregate.py {sPlotDirectory}/agg.{sFigureType}"],
  "saDependencies": ["{Step02.flare_samples}", "{Step03.age_samples}"]
}
```

The `saDependencies` field is scanned for tokens just like
`saDataCommands`. Use it sparingly — when an honest CLI-argument design
is possible, that is preferred because the dependency becomes visible
to anyone reading the command.

## Handling colliding basenames

The runtime resolver keys `{StepNN.varname}` lookups by the basename
(without extension) of each entry in the producer step's `saOutputDataFiles`.
When a single step declares two outputs with the same basename — for
example, `EngleBarnes/output/Converged_Param_Dictionary.json` and
`RibasBarnes/output/Converged_Param_Dictionary.json` — each colliding
entry registers under a QUALIFIED token instead: the leading path
segment joined to the stem with an underscore. Consumers reference
`{Step10.EngleBarnes_Converged_Param_Dictionary}` and
`{Step10.RibasBarnes_Converged_Param_Dictionary}` unambiguously, and
the bare colliding stem is deliberately not registered, so a stale
bare reference fails loudly in reference validation rather than
silently resolving to the last writer.

Do NOT rename scientific output files to dodge token collisions —
output filenames are part of a scientific code's public interface, and
benchmark tests bind to them. Keep the tool's standard filenames and
let the qualified tokens disambiguate.

## Worked example: a 2-step project

This is the smallest project that demonstrates the contract.

**Step 1** — `Sampler/dataSample.py`:

```python
import argparse
import numpy as np

parser = argparse.ArgumentParser()
parser.add_argument("--output", required=True)
dictArgs = vars(parser.parse_args())

daSamples = np.random.randn(1000)
np.save(dictArgs["output"], daSamples)
```

**Step 2** — `Plot/plotHistogram.py`:

```python
import argparse
import matplotlib.pyplot as plt
import numpy as np

parser = argparse.ArgumentParser()
parser.add_argument("--samples", required=True)
parser.add_argument("sPlotPath")
dictArgs = vars(parser.parse_args())

daSamples = np.load(dictArgs["samples"])
plt.hist(daSamples)
plt.savefig(dictArgs["sPlotPath"])
```

**`project.json`**:

```json
{
  "sName": "minimal-example",
  "listSteps": [
    {
      "iIndex": 1,
      "sName": "Sampler",
      "sDirectory": "Sampler",
      "saDataCommands": ["python dataSample.py --output samples.npy"],
      "saOutputDataFiles": ["samples.npy"]
    },
    {
      "iIndex": 2,
      "sName": "Plot",
      "sDirectory": "Plot",
      "saPlotCommands": [
        "python plotHistogram.py --samples {Step01.samples} {sPlotDirectory}/hist.{sFigureType}"
      ]
    }
  ]
}
```

The token `{Step01.samples}` makes the Sampler → Plot edge explicit in
the JSON. The parser finds it. The dashboard tracks it. When Sampler
re-runs, Plot's `bUpstreamModified` flag fires correctly. The project
can honestly claim Self-Consistent status because the contract is
intact.

## Enforcement

The architectural invariant
`tests/testArchitecturalInvariants.py::testTemplateCommandsUseStepTokens`
scans every `vaibify/templates/*/project.json` at CI time and rejects
templates that ship with hardcoded cross-step paths. The invariant
applies only to vaibify-shipped templates — user-authored projects
are out of vaibify's enforcement scope, but the same rule applies for
the dashboard to function correctly.

## Input Data — raw files a step consumes

Not every file a step reads is produced by another step. Raw
observational data, instrument tables, and any other file that exists
before the pipeline runs are **input data**, declared per step in
`saInputDataFiles`:

- Entries are **repo-relative** — they resolve against the project
  repository root, never the step directory.
- Entries must NOT be step products. A `{StepNN.*}` token in
  `saInputDataFiles` is rejected at load time: cross-step files stay
  tokens in commands so the dependency parser sees the edge.
- The same file may be declared by several steps — shared inputs are
  the normal case. A modification invalidates every declaring step
  (and, through the ordinary machinery, everything downstream of
  them); no ordering edge is created between sibling consumers.

Every step must state its input contract to reach AICS Level 1:
either list the raw files it reads, or set the explicit
`bNoInputData` declaration ("this step consumes no raw data"). Both
absent means *undeclared*, which blocks Level 1 — nothing
distinguishes "verified there are no raw inputs" from "nobody
looked." An input file modified after the step's outputs were
generated means the Project is no longer self-consistent: the step
is invalidated, its tests demote, and Level 1 drops until the step
re-runs.

At every step run, the test-marker plugin records a content hash for
each declared input (`dictInputHashes`), so staleness detection
survives a fresh clone: new mtimes with identical content stay
green; changed content is flagged even when a copy preserved the
mtime.

## Remote data — pulls must become canonical

A step that downloads data from a remote source (an archive query, a
DOI resolver, a survey release) declares each pulled file in
`listRemoteData`:

```json
"listRemoteData": [
  {"sPath": "data/lightcurve.fits",
   "sSourceUrl": "https://archive.example/query?...",
   "sRetrievedUtc": "", "sSha256": ""}
]
```

`sPath` is repo-relative (the pulled file also appears in the step's
`saOutputDataFiles` — it is that step's output). `sSourceUrl` is
inert provenance metadata: vaibify never fetches it. After each
successful run, vaibify hashes the pulled files and stamps
`sRetrievedUtc`/`sSha256` when the content changed, so the Project
permanently records what was fetched, from where, and when — even if
the remote later vanishes or silently changes.

For reproducibility the pulled data must be **committed** to the
project repository (the ordinary commit-canonical flow; badges show
the drift after a pull). Because a re-run would overwrite that
canonical copy, any run covering a remote-pull step whose declared
files already exist is refused with a confirmation question — in the
browser as a modal, for the in-container agent as a `runRefused`
event it must relay to the researcher (`--confirm-remote-overwrite`
after an explicit yes). A first-ever pull never prompts.
