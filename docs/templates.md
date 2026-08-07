# Project Templates

Vaibify ships with three project templates that provide starting
configurations for common use cases. Select a template when initializing a
new project:

```bash
vaibify init --template <name>
```

Each template contains `container.conf` and `project.json` (plus any
starter files, such as the workflow template's step directories). `vaibify
init` copies those into the current directory, moves `project.json` into
`.vaibify/projects/`, and then generates `vaibify.yml` from the built-in
defaults — the template does not supply it.

## sandbox

An empty starting point for exploration, prototyping, and interactive use.
No pipeline steps are defined — you work directly inside the container.

**Includes:**

- Empty `container.conf` (no repositories).
- Empty `project.json` (no pipeline steps).

Use this template when you want a containerized environment without a
predefined project.

## toolkit

A workspace for editing several peer code repositories side-by-side —
the right choice when you want to hack on more than one package at once
while iterating on a change that spans them.

**Includes:**

- Empty `container.conf` (add your repository URLs).
- Empty `project.json` (no pipeline steps).
- A README explaining how repository tracking and push controls work.

Toolkit containers have no workflow. Instead, the Repos panel in the
dashboard provides per-repository git status, dirty-file listings, and
push controls for every repository in `/workspace/`.

## workflow

A starting point for reproducible data analysis pipelines. Includes a
runnable two-step example — data generation feeding a plot — that you
replace with your own steps.

**Includes:**

- Empty `container.conf` (add your repositories).
- Example `project.json` with two steps: `GenerateSamples`, which runs
  `python generateSamples.py` to produce `samples.json`, and
  `PlotHistogram`, which runs `python plotHistogram.py` with the samples
  file passed via a `{step:generate-samples.samples}` token.
- The two step directories with the scripts those commands invoke.

Use this template when your project follows a defined sequence of analysis
steps that should be reproducible.

**Adding LaTeX compilation:** If you compile your manuscript inside the
container rather than using an external tool like Overleaf, add a step
to `project.json`. Note that a step's directory basename must equal the
slug derived from its name — `CompileManuscript` here — so the manuscript
sources live in a directory named after the step:

```json
{
    "sName": "CompileManuscript",
    "sStepId": "compile-manuscript",
    "sDirectory": "CompileManuscript",
    "bRunEnabled": true,
    "bPlotOnly": false,
    "saDataCommands": [],
    "saOutputDataFiles": [],
    "saPlotCommands": ["latexmk -pdf manuscript.tex"],
    "saPlotFiles": []
}
```

## Creating Custom Templates

Templates are stored in the `vaibify/templates/` directory of the
Vaibify package, so they ship inside the installed distribution.
Each template is a subdirectory containing `container.conf` and
`project.json`, plus any starter step directories. To create a custom
template, add a new subdirectory with these files and reinstall the
package. Do not place a `vaibify.yml` in a template: `vaibify init`
always generates that file itself, so one shipped in a template would
be overwritten.
