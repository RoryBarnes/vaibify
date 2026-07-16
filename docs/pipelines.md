# Pipelines

A pipeline defines a sequence of steps to execute inside the Vaibify.
Steps are self-contained units of work -- each one runs a series of
commands in order and produces output files such as figures or data products.

## Project File

Pipelines are defined in `project.json` at the repository root. The file has
four top-level fields:

| Field              | Type    | Description                              |
|--------------------|---------|------------------------------------------|
| `sPlotDirectory`   | string  | Directory where figures are collected    |
| `sFigureType`      | string  | Default figure format (`pdf`, `png`)     |
| `iNumberOfCores`   | integer | Cores to use (`-1` = all minus one)      |
| `listSteps`        | array   | Ordered list of step objects             |

## Step Object

Each step in `listSteps` has the following required fields:

| Field              | Type         | Description                         |
|--------------------|--------------|-------------------------------------|
| `sName`            | string       | Unique step identifier              |
| `sDirectory`       | string       | Working directory for the step      |
| `saPlotCommands`       | string array | Shell commands to execute in order  |
| `saPlotFiles`    | string array | Output file paths produced          |

And these optional fields:

| Field              | Type         | Default | Description                    |
|--------------------|--------------|---------|--------------------------------|
| `bRunEnabled`      | boolean      | `true`  | Whether the step is included in runs (run scope; verification iterates every step regardless) |
| `bPlotOnly`        | boolean      | `true`  | Step produces only plots       |
| `bInteractive`     | boolean      | `false` | Pause pipeline for user input  |
| `saDataCommands`   | string array | `[]`    | Commands to run before plots   |
| `saOutputDataFiles`      | string array | `[]`    | Output data files to verify    |
| `saTestCommands`   | string array | `[]`    | Pytest commands for the step   |

### Project size limits

Vaibify shows a one-shot "Project milestone" modal the first time a
project's `listSteps` reaches 100 entries. The acknowledgment is
persisted in the repository's `.vaibify/state.json` as
`bWarnedHundredSteps`, so the warning does not reappear on reload or
on subsequent additions. The threshold exists because polling cost
(file-status, repos, discovery) grows roughly linearly with the
number of tracked outputs, scripts, and markers, and beyond about
100 steps users typically notice some latency in the dashboard.

Vaibify refuses to add a 501st step to any project. The dashboard
shows a "Step limit reached" modal; the backend rejects direct API
calls with HTTP 400. The rationale is that the per-poll Docker exec
budget and the dashboard render budget both break down beyond this
scale. If a project requires more than 500 steps, split it into
sibling projects within the same repository — vaibify supports
multiple projects per container.

## Example

```json
{
    "sPlotDirectory": "Plot",
    "sFigureType": "pdf",
    "iNumberOfCores": -1,
    "listSteps": [
        {
            "sName": "RunSimulation",
            "sDirectory": "examples/EarthWater",
            "bRunEnabled": true,
            "bPlotOnly": false,
            "saDataCommands": [
                "python runAnalysis.py"
            ],
            "saPlotCommands": [
                "python makePlot.py"
            ],
            "saPlotFiles": []
        },
        {
            "sName": "PlotResults",
            "sDirectory": "examples/EarthWater",
            "bRunEnabled": true,
            "bPlotOnly": true,
            "saDataCommands": [],
            "saPlotCommands": [
                "python makePlot.py"
            ],
            "saPlotFiles": []
        }
    ]
}
```

## Core Allocation

When `iNumberOfCores` is set to `-1`, the pipeline runner detects the total
number of available cores and uses all but one. This leaves one core free
for the operating system and other processes. Specify a positive integer to
fix the core count explicitly.

## Running a Pipeline

Start the container and execute the pipeline:

```bash
vaibify start
```

The pipeline runs all steps in order. Each step runs its commands
sequentially. Steps themselves execute one at a time by default; future
versions may support parallel step execution for independent steps.

## Pipeline Output

Figures produced by step commands are copied to the `sPlotDirectory`
after each step completes. The directory is created automatically if it
does not exist.

## Integration with GitHub Actions

Use `vaibify publish workflow` to generate a GitHub Actions workflow
from `project.json`. The generated workflow builds the Docker image and
runs each step inside the container. See [Reproducibility](reproducibility.md)
for details.

## Multi-Container Projects

Each Vaibify project gets its own Docker image, container, and workspace
volume. Multiple projects can run simultaneously on the same machine
without interference. Use the `--project/-p` flag to target a specific
project from any directory:

```bash
vaibify build -p earth-water
vaibify start -p earth-water
vaibify status -p earth-water
vaibify stop -p earth-water
```

Projects are registered automatically when you run `vaibify init`. When
only one project is registered, the `--project` flag can be omitted.
When you are inside a project directory (one containing `vaibify.yml`),
the flag defaults to that project.
