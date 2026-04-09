<p align="center">
  <img width = "350" src="docs/vaibify_logo.png?raw=true"/>
</p>

<h1 align="center">Vibe Boldly. Verify Everything.</h1>

<p align="center">
  <a href="https://RoryBarnes.github.io/vaibify">
    <img src="https://img.shields.io/badge/Read-the_docs-blue.svg?style=flat">
  </a>
  <img src="https://github.com/RoryBarnes/vaibify/actions/workflows/docs.yml/badge.svg">
  <a href="https://RoryBarnes.github.io/vaibify/conduct.html">
    <img src="https://img.shields.io/badge/Code%20of-Conduct-black.svg">
  </a>
  <img src="https://github.com/RoryBarnes/vaibify/actions/workflows/pip-install.yml/badge.svg">
  <br>
  <img src="https://img.shields.io/badge/Unit%20Tests-1,818-darkblue.svg">
  <img src="https://github.com/RoryBarnes/vaibify/actions/workflows/tests-linux.yml/badge.svg">
  <img src="https://img.shields.io/badge/Ubuntu%2022--24-Python%203.9--3.14-7d93c7.svg">
  <br>
  <img src="https://github.com/RoryBarnes/vaibify/actions/workflows/tests-macos.yml/badge.svg">
  <img src="https://img.shields.io/badge/macOS%2015--26-Python%203.9--3.14-7d93c7.svg">
  <a href="https://codecov.io/gh/RoryBarnes/vaibify">
  <img src="https://codecov.io/gh/RoryBarnes/vaibify/branch/main/graph/badge.svg">
</a>
</p>


### Why Vaibify?

Data scientists increasingly rely on AI coding agents to build and iterate on analysis pipelines. But running AI-generated code raises real concerns:

- **Safety** — AI agents need broad permissions to be effective, but broad permissions on your host are dangerous. Vaibify runs everything inside an isolated Docker container with no access to your host filesystem, network, or credentials beyond what you explicitly grant.
- **Reproducibility** — Vaibify tracks provenance (SHA-256 hashes of every input and output), archives results to Zenodo with a DOI, syncs figures to Overleaf, and generates GitHub Actions workflows so anyone can reproduce your pipeline.
- **Iteration** — Decompose your project into steps, run them in parallel, inspect the outputs in the workflow viewer GUI, and re-run individual steps until you're satisfied.
- **Generality** — Vaibify is not tied to any specific domain. Configure your repositories, packages, languages (Python, R, Julia), and secrets in a single YAML file. Templates get you started fast.

### Features

`Vaibify` provides a complete workflow for containerized scientific computing:

**Container Management** — Build, start, stop, and connect to Docker environments defined by a single `vaibify.yml` configuration file. Multiple projects can run simultaneously, each with its own container, image, and workspace volume. Target any project from any directory with `--project/-p`. Clone and install repositories, system packages, and Python/R/Julia dependencies automatically.

**Pipeline Execution** — Define multi-step workflows in `workflow.json` with data commands, plot commands, and test commands. Run individual steps or the full pipeline with one click in the browser-based GUI.

**Workflow Viewer** — A browser-based GUI for managing pipelines, viewing figures, monitoring resources, and running terminal sessions inside the container.

**Security** — No Docker socket inside the container, unprivileged user with `gosu`, ephemeral secrets mounted as mode-600 temp files, optional network isolation, and a built-in security audit (`vaibify verify`).

**Reproducibility** — Provenance tracking, Zenodo archival with DOI assignment, Overleaf figure sync, LaTeX annotation generation, and GitHub Actions workflow generation.

**Templates** — Two project templates ship with Vaibify: `sandbox` (no workflow, for exploration) and `workflow` (pipeline steps for reproducible analysis).

### Quick Start

```bash
pip install vaibify
vaibify init --template workflow
vaibify build
vaibify start --gui
```

See the [full documentation](https://RoryBarnes.github.io/vaibify) for
CLI reference, configuration, security model, and contributor guidelines.

### License

MIT. © 2025 Rory Barnes.
