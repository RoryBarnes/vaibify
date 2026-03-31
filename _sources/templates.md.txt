# Project Templates

Vaibify ships with two project templates that provide starting
configurations for common use cases. Select a template when initializing a
new project:

```bash
vaibify init --template <name>
```

Each template creates three files in the current directory: `vaibify.yml`,
`container.conf`, and `workflow.json`.

## general

A blank slate for any data science project. The configuration contains
sensible defaults with no pre-configured repositories. Use this template
when your project does not fit into one of the specialized categories.

**Includes:**

- Minimal `vaibify.yml` with default Python version and base image.
- Empty `container.conf` (no repositories).
- Empty `workflow.json` (no pipeline steps).

## reproducible-paper

Designed for writing academic papers with automated figure generation and
archival. The pipeline builds figures from data, syncs them to Overleaf,
and archives the results to Zenodo.

**Includes:**

- Reproducibility block pre-configured with Zenodo sandbox and Overleaf
  sync paths.
- LaTeX enabled by default.
- Example `workflow.json` with steps for data generation, figure production,
  and LaTeX compilation.
- GitHub Actions workflow generation ready to go.

## Creating Custom Templates

Templates are stored in the `templates/` directory of the Vaibify package.
Each template is a subdirectory containing `vaibify.yml`,
`container.conf`, and `workflow.json`. To create a custom template, add a
new subdirectory with these three files and reinstall the package.
