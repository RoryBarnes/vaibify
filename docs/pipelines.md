# Pipelines

A pipeline defines a sequence of scenes to execute inside the VaibCask.
Scenes are self-contained units of work -- each one runs a series of
commands in order and produces output files such as figures or data products.

## Script File

Pipelines are defined in `script.json` at the project root. The file has
four top-level fields:

| Field              | Type    | Description                              |
|--------------------|---------|------------------------------------------|
| `sPlotDirectory`   | string  | Directory where figures are collected    |
| `sFigureType`      | string  | Default figure format (`pdf`, `png`)     |
| `iNumberOfCores`   | integer | Cores to use (`-1` = all minus one)      |
| `listScenes`       | array   | Ordered list of scene objects            |

## Scene Object

Each scene in `listScenes` has the following required fields:

| Field              | Type         | Description                         |
|--------------------|--------------|-------------------------------------|
| `sName`            | string       | Unique scene identifier             |
| `sDirectory`       | string       | Working directory for the scene     |
| `saCommands`       | string array | Shell commands to execute in order  |
| `saOutputFiles`    | string array | Output file paths produced          |

And these optional fields:

| Field              | Type         | Default | Description                    |
|--------------------|--------------|---------|--------------------------------|
| `bEnabled`         | boolean      | `true`  | Whether the scene should run   |
| `bPlotOnly`        | boolean      | `true`  | Scene produces only plots      |
| `saSetupCommands`  | string array | `[]`    | Commands to run before saCommands |

## Example

```json
{
    "sPlotDirectory": "Plot",
    "sFigureType": "pdf",
    "iNumberOfCores": -1,
    "listScenes": [
        {
            "sName": "RunSimulation",
            "sDirectory": "examples/EarthWater",
            "bEnabled": true,
            "bPlotOnly": false,
            "saSetupCommands": [
                "vplanet vpl.in"
            ],
            "saCommands": [
                "python makePlot.py"
            ],
            "saOutputFiles": []
        },
        {
            "sName": "PlotResults",
            "sDirectory": "examples/EarthWater",
            "bEnabled": true,
            "bPlotOnly": true,
            "saSetupCommands": [],
            "saCommands": [
                "python makePlot.py"
            ],
            "saOutputFiles": []
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
vaibcask start
```

The pipeline runs all scenes in order. Each scene runs its commands
sequentially. Scenes themselves execute one at a time by default; future
versions may support parallel scene execution for independent scenes.

## Pipeline Output

Figures produced by scene commands are copied to the `sPlotDirectory`
after each scene completes. The directory is created automatically if it
does not exist.

## Integration with GitHub Actions

Use `vaibcask publish workflow` to generate a GitHub Actions workflow
from `script.json`. The generated workflow builds the Docker image and
runs each scene inside the container. See [Reproducibility](reproducibility.md)
for details.
