# VaibCask

**Vibe boldly. Verify everything.**

VaibCask is a secure, containerized environment for AI-assisted data science. It decomposes your project into pipeline steps, executes them in parallel inside an isolated Docker container, verifies the outputs, and publishes the results — all with minimal IDE interaction. VaibCask lets data scientists embrace vibe coding with confidence: your AI agent runs wild inside the walls, while your host machine stays safe.

## Why VaibCask?

Data scientists increasingly rely on AI coding agents to build and iterate on analysis pipelines. But running AI-generated code raises real concerns:

- **Safety** — AI agents need broad permissions to be effective, but broad permissions on your host are dangerous. VaibCask runs everything inside an isolated Docker container with no access to your host filesystem, network, or credentials beyond what you explicitly grant.
- **Reproducibility** — VaibCask tracks provenance (SHA-256 hashes of every input and output), archives results to Zenodo with a DOI, syncs figures to Overleaf, and generates GitHub Actions workflows so anyone can reproduce your pipeline.
- **Iteration** — Decompose your project into steps, run them in parallel, inspect the outputs in the recipe viewer GUI, and re-run individual steps until you're satisfied. The AI agent and you iterate together.
- **Generality** — VaibCask is not tied to any specific domain. Configure your repositories, packages, languages (Python, R, Julia), and secrets in a single YAML file. Templates get you started fast.

## Quick Start

```bash
pip install vaibcask[docker]

# Initialize a project
vaibcask init --template general

# Edit the config
vaibcask setup

# Build the container
vaibcask build

# Start the environment
vaibcask start

# Open the recipe viewer
vaibcask gui
```

## Configuration

Everything is configured in `vaibcask.yml`. Only `projectName` is required — everything else has sensible defaults:

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
vaibcask init [--template NAME]     Create vaibcask.yml from a template
vaibcask setup                      Launch the setup wizard GUI
vaibcask build [--no-cache]         Build the Docker image
vaibcask start [--gui] [--jupyter]  Start the container
vaibcask stop                       Stop the container
vaibcask status                     Show environment status
vaibcask connect                    Shell into the container
vaibcask verify                     Run the isolation security audit
vaibcask gui                        Launch the recipe viewer
vaibcask push <src> <dest>          Copy files into the container
vaibcask pull <src> <dest>          Copy files out of the container
vaibcask config export <file>       Export configuration
vaibcask config import <file>       Import configuration
vaibcask config edit                Open config in your editor
vaibcask publish archive            Upload outputs to Zenodo
vaibcask publish workflow           Generate GitHub Actions YAML
```

The `vc` command is a shorthand alias for `vaibcask`.

## Templates

VaibCask ships with three project templates:

| Template | Use case |
|----------|----------|
| `general` | Blank slate for any data science project |
| `planetary` | Planetary science with VPLanet integration |
| `reproducible-paper` | LaTeX paper with automated figure generation and Zenodo archival |

```bash
vaibcask init --template reproducible-paper
```

## Feature Overlays

Enable features in `vaibcask.yml` and VaibCask builds them as Docker layers in a deterministic order:

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

VaibCask is built for the paranoid scientist:

- **No Docker socket** inside the container
- **Unprivileged user** with `gosu` privilege drop
- **Ephemeral secrets** — credentials are mounted as mode-600 temp files at `/run/secrets/`, never stored in environment variables, shell history, or git config
- **Token hygiene** — Zenodo uses `Authorization: Bearer` headers (not URL params); Overleaf uses git credential helpers (not URL-embedded tokens)
- **Network isolation** — `networkIsolation: true` starts the container with `--network none`
- **Security audit** — `vaibcask verify` runs an isolation check script that audits mounts, ports, socket access, and privilege escalation paths
- **GUI binds localhost only** — `127.0.0.1`, never `0.0.0.0`

## Reproducibility

VaibCask integrates reproducibility into the pipeline workflow:

- **Provenance tracking** — SHA-256 hashes of all inputs and outputs, stored as JSON sidecars
- **Zenodo archival** — One-click upload of pipeline outputs with DOI assignment
- **Overleaf sync** — Push figures to your Overleaf project; pull manuscript updates back
- **LaTeX generation** — Auto-generate `\includegraphics` commands and margin icons linking to commits and DOIs
- **GitHub Actions** — Generate a CI workflow that rebuilds your pipeline in a fresh container

## Recipe Viewer

The built-in GUI (`vaibcask gui`) provides:

- Visual pipeline editor for `recipe.json` steps
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
pip install vaibcask[all]

# From source
git clone https://github.com/RoryBarnes/VaibCask.git
cd VaibCask
pip install -e .[all]
```

## License

MIT
