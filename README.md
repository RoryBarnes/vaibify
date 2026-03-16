# Vaibify

**Vibe boldly. Verify everything.**

Vaibify is a secure, containerized environment for AI-assisted data science. It decomposes your project into pipeline steps, executes them in parallel inside an isolated Docker container, verifies the outputs, and publishes the results — all with minimal IDE interaction. Vaibify lets data scientists embrace vibe coding with confidence: your AI agent runs wild inside the walls, while your host machine stays safe.

## Why Vaibify?

Data scientists increasingly rely on AI coding agents to build and iterate on analysis pipelines. But running AI-generated code raises real concerns:

- **Safety** — AI agents need broad permissions to be effective, but broad permissions on your host are dangerous. Vaibify runs everything inside an isolated Docker container with no access to your host filesystem, network, or credentials beyond what you explicitly grant.
- **Reproducibility** — Vaibify tracks provenance (SHA-256 hashes of every input and output), archives results to Zenodo with a DOI, syncs figures to Overleaf, and generates GitHub Actions workflows so anyone can reproduce your pipeline.
- **Iteration** — Decompose your project into steps, run them in parallel, inspect the outputs in the workflow viewer GUI, and re-run individual steps until you're satisfied. The AI agent and you iterate together.
- **Generality** — Vaibify is not tied to any specific domain. Configure your repositories, packages, languages (Python, R, Julia), and secrets in a single YAML file. Templates get you started fast.

## Quick Start

```bash
pip install vaibify[docker]

# Initialize a project
vaibify init --template general

# Edit the config
vaibify setup

# Build the container
vaibify build

# Start the environment
vaibify start

# Open the workflow viewer
vaibify gui
```

## Configuration

Everything is configured in `vaibify.yml`. Only `projectName` is required — everything else has sensible defaults:

```yaml
projectName: "my-research"
```

A full configuration with all options:

```yaml
projectName: "my-research"
containerUser: "researcher"
pythonVersion: "3.12"
baseImage: "ubuntu:24.04"
workspaceRoot: "/workspace"
packageManager: "pip"            # pip | conda | mamba

repositories:
  - name: "my-model"
    url: "git@github.com:user/my-model.git"
    branch: "main"
    installMethod: "pip_editable" # c_and_pip | pip_no_deps | pip_editable | scripts_only | reference

systemPackages: ["gcc", "make", "libhdf5-dev"]
pythonPackages: ["numpy>=1.24", "scipy>=1.10"]

features:
  jupyter: true
  rLanguage: false
  julia: false
  database: false
  dvc: false
  latex: true
  claude: false
  gpu: false

binaries:
  - name: "MY_BINARY"
    path: "/workspace/my-model/bin/my-model"

ports:
  - host: 8888
    container: 8888

secrets:
  - name: "github"
    method: "gh_auth"            # gh_auth | keyring | docker_secret

reproducibility:
  zenodoService: "sandbox"
  latexRoot: "src/tex"
  overleaf:
    projectId: ""
    figureDirectory: "figures"

networkIsolation: false
```

## CLI Commands

```
vaibify init [--template NAME]     Create vaibify.yml from a template
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
vaibify config export <file>       Export configuration
vaibify config import <file>       Import configuration
vaibify config edit                Open config in your editor
vaibify publish archive            Upload outputs to Zenodo
vaibify publish workflow           Generate GitHub Actions YAML
```

The `vc` command is a shorthand alias for `vaibify`.

## Templates

Vaibify ships with three project templates:

| Template | Use case |
|----------|----------|
| `general` | Blank slate for any data science project |
| `planetary` | Planetary science with VPLanet integration |
| `reproducible-paper` | LaTeX paper with automated figure generation and Zenodo archival |

```bash
vaibify init --template reproducible-paper
```

## Feature Overlays

Enable features in `vaibify.yml` and Vaibify builds them as Docker layers in a deterministic order:

| Feature | What it adds |
|---------|-------------|
| `gpu` | NVIDIA CUDA runtime + Python bindings |
| `jupyter` | JupyterLab on port 8888 |
| `rLanguage` | R from CRAN + IRkernel for Jupyter |
| `julia` | Julia + IJulia kernel |
| `database` | PostgreSQL client + SQLAlchemy |
| `dvc` | Data Version Control |
| `claude` | Claude Code CLI (Node.js + claude-code) |

## Security

Vaibify is built for the paranoid scientist:

- **No Docker socket** inside the container
- **Unprivileged user** with `gosu` privilege drop
- **Ephemeral secrets** — credentials are mounted as mode-600 temp files at `/run/secrets/`, never stored in environment variables, shell history, or git config
- **Token hygiene** — Zenodo uses `Authorization: Bearer` headers (not URL params); Overleaf uses git credential helpers (not URL-embedded tokens)
- **Network isolation** — `networkIsolation: true` starts the container with `--network none`
- **Security audit** — `vaibify verify` runs an isolation check script that audits mounts, ports, socket access, and privilege escalation paths
- **GUI binds localhost only** — `127.0.0.1`, never `0.0.0.0`

## Reproducibility

Vaibify integrates reproducibility into the pipeline workflow:

- **Provenance tracking** — SHA-256 hashes of all inputs and outputs, stored as JSON sidecars
- **Zenodo archival** — One-click upload of pipeline outputs with DOI assignment
- **Overleaf sync** — Push figures to your Overleaf project; pull manuscript updates back
- **LaTeX generation** — Auto-generate `\includegraphics` commands and margin icons linking to commits and DOIs
- **GitHub Actions** — Generate a CI workflow that rebuilds your pipeline in a fresh container

## Workflow Viewer

The built-in GUI (`vaibify gui`) provides:

- Visual pipeline editor for `workflow.json` steps
- One-click execution of individual steps or the full pipeline
- Live figure preview with automatic refresh
- Integrated terminal session
- Real-time CPU/memory/disk monitoring
- Buttons for Overleaf push, Zenodo archive, and LaTeX generation

## Requirements

- Python 3.9+
- Docker (for container features)
- macOS or Linux

## Installation

```bash
# From PyPI (when published)
pip install vaibify[all]

# From source
git clone https://github.com/RoryBarnes/Vaibify.git
cd Vaibify
pip install -e .[all]
```

## License

MIT
