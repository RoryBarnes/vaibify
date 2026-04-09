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


`vaibify` creates secure, containerized environments for AI-assisted data analysis that can be accessed through a GUI or the command line. While it fully embraces agentic AI code development, `vaibify` also recognizes that a human must verify all results. `vaibify` solves these challanges by configuring Docker containers that prevent AI agents from harming your sensitive data, povides a GUI that supports terminal window(s) for running agents like Claude Code, and includes "viewing windows" that allow users to inspect results (data files, figures, animations). A "Workflow" mode decomposes projects into automatic vs. interactive steps, and verifies the output via unit tests, dependency checks, and user validation. `vaibify` also seamlessly integrates with external resources like GitHub, Overleaf, and Zenodo so you can easily write articles/reports, manage your work with version control, and archive your data. `vaibify` allows you to vibe code with confidence: your host machine stays safe while the agents develop code and build your analysis pipeline — all with minimal IDE interaction — enabling you to focus on vetting the results via visual inspection, writing up a summary, and deciding your next steps.

### Features

`vaibify` provides a complete workflow for containerized scientific computing:

**Container Management** — Build, start, stop, and connect to Docker environments defined by a single `vaibify.yml` configuration file. Multiple projects can run simultaneously, each with its own container, image, and workspace volume. Target any project from any directory with `--project/-p`. Clone and install repositories, system packages, and Python/R/Julia dependencies automatically.

**Pipeline Execution** — Define multi-step workflows in `workflow.json` with data commands, plot commands, and test commands. Run individual steps or the full pipeline with one click in the browser-based GUI.

**Workflow Viewer** — A browser-based GUI for managing pipelines, viewing figures, monitoring resources, and running terminal sessions inside the container.

**Security** — No Docker socket inside the container, unprivileged user with `gosu`, ephemeral secrets mounted as mode-600 temp files, optional network isolation, and a built-in security audit (`vaibify verify`).

**Reproducibility** — Provenance tracking, Zenodo archival with DOI assignment, Overleaf figure sync, LaTeX annotation generation, and GitHub Actions workflow generation.

**Templates** — Two project templates ship with Vaibify: `sandbox` (no workflow, for exploration) and `workflow` (pipeline steps for reproducible analysis).

### Learn More

See the [full documentation](https://RoryBarnes.github.io/vaibify) for
CLI reference, configuration, security model, and contributor guidelines.

### License

MIT. © 2025 Rory Barnes.
