<p align="center">
  <img width = "350" src="docs/vaibify_logo.png?raw=true"/>
</p>

<h1 align="center">Vibe Boldly. Verify Everything.</h1>

<p align="center">
  <a href="https://RoryBarnes.github.io/vaibify">
    <img src="https://img.shields.io/badge/Read-the_docs-blue.svg?style=flat">
  </a>
  <a href="https://RoryBarnes.github.io/vaibify/conduct.html">
    <img src="https://img.shields.io/badge/Code%20of-Conduct-black.svg">
  </a>
  <a href="https://github.com/RoryBarnes/vaibify/issues">
    <img src="https://img.shields.io/badge/Issues-orange.svg">
  </a>
  <a href="https://github.com/RoryBarnes/vaibify/discussions">
    <img src="https://img.shields.io/badge/Discussions-orange.svg">
  </a>
  <br>
  <img src="https://github.com/RoryBarnes/vaibify/actions/workflows/tests-linux.yml/badge.svg">
  <img src="https://img.shields.io/badge/Ubuntu%2022--24-Python%203.9--3.14-7d93c7.svg">
  <br>
  <img src="https://github.com/RoryBarnes/vaibify/actions/workflows/tests-macos.yml/badge.svg">
  <img src="https://img.shields.io/badge/macOS%2015--26-Python%203.9--3.14-7d93c7.svg">
  <br>
  <img src="https://github.com/RoryBarnes/vaibify/actions/workflows/docs.yml/badge.svg">
</p>

### Overview

`Vaibify` is a secure, containerized environment for AI-assisted data science. It decomposes projects into pipeline steps, executes them inside isolated Docker containers, verifies the outputs, and publishes the results — all with minimal IDE interaction. Vaibify lets data scientists embrace vibe coding with confidence: the AI agent runs wild inside the walls, while the host machine stays safe.

To get started, follow the [Installation Guide](https://RoryBarnes.github.io/vaibify/install.html) and then the [Quick Start](https://RoryBarnes.github.io/vaibify/quickStart.html).

### Why Vaibify?

Data scientists increasingly rely on AI coding agents to build and iterate on analysis pipelines. But running AI-generated code raises real concerns:

- **Safety** — AI agents need broad permissions to be effective, but broad permissions on your host are dangerous. Vaibify runs everything inside an isolated Docker container with no access to your host filesystem, network, or credentials beyond what you explicitly grant.
- **Reproducibility** — Vaibify tracks provenance (SHA-256 hashes of every input and output), archives results to Zenodo with a DOI, syncs figures to Overleaf, and generates GitHub Actions workflows so anyone can reproduce your pipeline.
- **Iteration** — Decompose your project into steps, run them in parallel, inspect the outputs in the workflow viewer GUI, and re-run individual steps until you're satisfied.
- **Generality** — Vaibify is not tied to any specific domain. Configure your repositories, packages, languages (Python, R, Julia), and secrets in a single YAML file. Templates get you started fast.

### Features

`Vaibify` provides a complete workflow for containerized scientific computing:

**Container Management** — Build, start, stop, and connect to Docker environments defined by a single `vaibify.yml` configuration file. Clone and install repositories, system packages, and Python/R/Julia dependencies automatically.

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

### CLI Commands

```
vaibify init [--template NAME]     Create a project from a template
vaibify setup                      Launch the setup wizard GUI
vaibify build [--no-cache]         Build the Docker image
vaibify start [--gui] [--jupyter]  Start the container
vaibify stop                       Stop the container
vaibify status                     Show environment status
vaibify connect                    Shell into the container
vaibify verify                     Run the isolation security audit
vaibify gui                        Launch the workflow viewer
vaibify push <src> <dest>          Copy files into the container
vaibify pull <src> <dest>          Copy files out of the container
vaibify config [edit|export|import]
vaibify publish [archive|workflow]
```

The shorthand `vaib` is also available.

### Resources

The [docs/](docs/) directory contains the full Sphinx documentation, also available [online](https://RoryBarnes.github.io/vaibify). The [templates/](templates/) directory contains the project templates. The [tests/](tests/) directory contains the pytest test suite.

### Code Integrity

The `Vaibify` team maintains code integrity through automatic checks at every pull request. Unit tests run across all permutations of Ubuntu 22.04/24.04, macOS 15/26, and Python 3.9 through 3.14. Tests that require a running Docker daemon are excluded from CI and run locally. Documentation is rebuilt and deployed automatically on every merge to main.

### Community

`Vaibify` is a community project. We welcome pull requests — please issue them to the `main` branch. See the [Contributor's Guide](https://RoryBarnes.github.io/vaibify/developers.html) for style conventions and testing instructions.

If you have questions or are running into issues, post to a [Discussion](https://github.com/RoryBarnes/vaibify/discussions).

If you believe you have encountered a bug, please raise an issue using the [Issues](https://github.com/RoryBarnes/vaibify/issues) tab.

### Requirements

- Python 3.9+
- Docker (or Colima on macOS)
- macOS or Linux

### License

MIT

© 2025 Rory Barnes.
