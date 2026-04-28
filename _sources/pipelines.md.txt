# Pipelines

A pipeline defines a sequence of steps to execute inside the Vaibify.
Steps are self-contained units of work -- each one runs a series of
commands in order and produces output files such as figures or data products.

## Workflow File

Pipelines are defined in `workflow.json` at the project root. The file has
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
| `saDataFiles`      | string array | `[]`    | Output data files to verify    |
| `saTestCommands`   | string array | `[]`    | Pytest commands for the step   |

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
from `workflow.json`. The generated workflow builds the Docker image and
runs each step inside the container. See [Reproducibility](reproducibility.md)
for details.

## Multi-Container Workflows

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
