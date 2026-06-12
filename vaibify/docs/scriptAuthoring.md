# Authoring scripts inside a vaibify workflow

This document explains the contract that vaibify expects scripts inside
a workflow to follow. The contract is small, mechanical, and exists for
exactly one reason: the workflow JSON must describe the complete
dependency graph so the dashboard can honestly track reproducibility.

## The contract

A vaibify workflow is a `workflow.json` plus a directory of scripts.
The JSON declares each step, the commands that produce its outputs, and
the files those outputs are stored at. Vaibify's dashboard parses the
JSON to:

1. Build the dependency graph between steps.
2. Decide when a step is stale because an upstream changed.
3. Decide when the workflow has reached AICS Level 1, 2, or 3.

The parser at
`vaibify/gui/workflowManager.py::fdictBuildDirectDependencies` uses one
mechanism to find dependencies: it scans command strings for
`{StepNN.varname}` tokens. Anything that token-references another step
is part of the graph. Anything else is not.

This is the contract: **every cross-step file your script reads must
arrive as a CLI argument, and the workflow JSON command must reference
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
- The workflow can therefore claim Self-Consistent (L1) status when it
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
  `saDataFiles` entry. So `flare_samples.npy` in `saDataFiles` becomes
  `{Step02.flare_samples}` in any consumer's command.
- **Use `argparse`, not raw `sys.argv` indexing.** The CLI is part of
  the workflow contract; argparse makes it explicit and self-documenting.

The director substitutes `{Step02.flare_samples}` at runtime with the
actual repo-relative path to the producer's output. Your script never
needs to know where A02 lives.

## `saDependencies` — the escape hatch

Sometimes the data flow does not naturally express the dependency. The
most common case: a plot script reads a sibling step's output but the
workflow command does not "naturally" thread that path as a CLI
argument (for instance, the script reads many sibling outputs whose
names follow a pattern).

For those cases, the workflow JSON has an explicit `saDependencies`
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
(without extension) of each entry in the producer step's `saDataFiles`.
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

## Worked example: a 2-step workflow

This is the smallest workflow that demonstrates the contract.

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

**`workflow.json`**:

```json
{
  "sName": "minimal-example",
  "listSteps": [
    {
      "iIndex": 1,
      "sName": "Sampler",
      "sDirectory": "Sampler",
      "saDataCommands": ["python dataSample.py --output samples.npy"],
      "saDataFiles": ["samples.npy"]
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
re-runs, Plot's `bUpstreamModified` flag fires correctly. The workflow
can honestly claim Self-Consistent status because the contract is
intact.

## Enforcement

The architectural invariant
`tests/testArchitecturalInvariants.py::testTemplateCommandsUseStepTokens`
scans every `vaibify/templates/*/workflow.json` at CI time and rejects
templates that ship with hardcoded cross-step paths. The invariant
applies only to vaibify-shipped templates — user-authored workflows
are out of vaibify's enforcement scope, but the same rule applies for
the dashboard to function correctly.
