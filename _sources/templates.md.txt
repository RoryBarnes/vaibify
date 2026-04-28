# Project Templates

Vaibify ships with two project templates that provide starting
configurations for common use cases. Select a template when initializing a
new project:

```bash
vaibify init --template <name>
```

Each template creates three files in the current directory: `vaibify.yml`,
`container.conf`, and `workflow.json`.

## sandbox

An empty starting point for exploration, prototyping, and interactive use.
No pipeline steps are defined — you work directly inside the container.

**Includes:**

- Minimal `vaibify.yml` with default Python version and base image.
- Empty `container.conf` (no repositories).
- Empty `workflow.json` (no pipeline steps).

Use this template when you want a containerized environment without a
predefined workflow.

## workflow

A starting point for reproducible data analysis pipelines. Includes an
example step with data generation and plotting commands that you replace
with your own.

**Includes:**

- Minimal `vaibify.yml` with default Python version and base image.
- Empty `container.conf` (add your repositories).
- Example `workflow.json` with one step (`AnalyzeData`) that runs
  `python runAnalysis.py` and `python makePlot.py`.

Use this template when your project follows a defined sequence of analysis
steps that should be reproducible.

**Adding LaTeX compilation:** If you compile your manuscript inside the
container rather than using an external tool like Overleaf, add a step
to `workflow.json`:

```json
{
    "sName": "CompileManuscript",
    "sDirectory": "tex",
    "bRunEnabled": true,
    "bPlotOnly": false,
    "saDataCommands": [],
    "saDataFiles": [],
    "saPlotCommands": ["latexmk -pdf manuscript.tex"],
    "saPlotFiles": []
}
```

## Creating Custom Templates

Templates are stored in the `templates/` directory of the Vaibify package.
Each template is a subdirectory containing `vaibify.yml`,
`container.conf`, and `workflow.json`. To create a custom template, add a
new subdirectory with these three files and reinstall the package.
