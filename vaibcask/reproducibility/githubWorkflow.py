"""GitHub Actions workflow generation via Jinja2 templates.

Produces a YAML workflow file that checks out the repository, builds
the VaibCask image, runs the pipeline, and optionally uploads
artefacts.
"""

from pathlib import Path

from jinja2 import Environment, BaseLoader


# ------------------------------------------------------------------
# Embedded fallback template
# ------------------------------------------------------------------

_TEMPLATE_STRING = """\
name: VaibCask Reproducibility

on:
  push:
    branches: [{{ sBranch }}]
  pull_request:
    branches: [{{ sBranch }}]
  workflow_dispatch:

jobs:
  reproduce:
    runs-on: {{ sRunner }}
    steps:
      - name: Checkout repository
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "{{ sPythonVersion }}"

      - name: Install VaibCask
        run: pip install vaibcask

      - name: Build container image
        run: vaibcask build

      - name: Run pipeline
        run: vaibcask start --run-pipeline
{% if bUploadArtifacts %}
      - name: Upload artifacts
        uses: actions/upload-artifact@v4
        with:
          name: pipeline-outputs
          path: {{ sArtifactsPath }}
{% endif %}
"""


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------


def fsGenerateWorkflow(config):
    """Render the GitHub Actions workflow YAML from config.

    Parameters
    ----------
    config : dict
        Template variables. Expected keys: sBranch, sRunner,
        sPythonVersion, bUploadArtifacts, sArtifactsPath.

    Returns
    -------
    str
        Rendered YAML string.
    """
    dictContext = _fdictBuildContext(config)
    sTemplate = fsGetWorkflowTemplate()
    return _fsRenderTemplate(sTemplate, dictContext)


def fnWriteWorkflow(config, sOutputPath=None):
    """Render and write the workflow YAML to disk.

    Parameters
    ----------
    config : dict
        Template variables passed to fsGenerateWorkflow.
    sOutputPath : str or None
        Destination path. Defaults to
        .github/workflows/vaibcask.yml in the current directory.
    """
    if sOutputPath is None:
        sOutputPath = ".github/workflows/vaibcask.yml"
    sContent = fsGenerateWorkflow(config)
    _fnWriteFile(sOutputPath, sContent)


def fsGetWorkflowTemplate():
    """Return the Jinja2 template string.

    Attempts to load from the on-disk template file first; falls
    back to the embedded string if the file is not found.

    Returns
    -------
    str
        Jinja2 template source.
    """
    pathTemplate = _fpathLocateTemplateFile()
    if pathTemplate is not None:
        return pathTemplate.read_text()
    return _TEMPLATE_STRING


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------


def _fdictBuildContext(config):
    """Merge user config with sensible defaults."""
    return {
        "sBranch": config.get("sBranch", "main"),
        "sRunner": config.get("sRunner", "ubuntu-latest"),
        "sPythonVersion": config.get("sPythonVersion", "3.12"),
        "bUploadArtifacts": config.get("bUploadArtifacts", True),
        "sArtifactsPath": config.get("sArtifactsPath", "outputs/"),
    }


def _fsRenderTemplate(sTemplate, dictContext):
    """Render a Jinja2 template string with the given context."""
    environment = Environment(loader=BaseLoader())
    templateObject = environment.from_string(sTemplate)
    return templateObject.render(**dictContext)


def _fpathLocateTemplateFile():
    """Locate the on-disk template file, or return None."""
    pathCandidate = (
        Path(__file__).resolve().parents[2]
        / "templates"
        / "workflow.yml.j2"
    )
    if pathCandidate.is_file():
        return pathCandidate
    return None


def _fnWriteFile(sOutputPath, sContent):
    """Write string content to a file, creating parents as needed."""
    pathOutput = Path(sOutputPath)
    pathOutput.parent.mkdir(parents=True, exist_ok=True)
    with open(pathOutput, "w") as fileHandle:
        fileHandle.write(sContent)
