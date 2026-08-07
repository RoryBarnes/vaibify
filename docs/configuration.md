# Configuration Reference

Vaibify projects are configured through three files: `vaibify.yml` and
`container.conf` in the project root directory, and `project.json` at
`.vaibify/projects/project.json`. This page documents every field and
option.

## vaibify.yml

The primary configuration file. All fields use camelCase keys in the YAML
file; the Python dataclass uses Hungarian notation internally.

### Top-Level Fields

| YAML Key          | Type    | Default           | Description                          |
|-------------------|---------|-------------------|--------------------------------------|
| `projectName`     | string  | *(required)*      | Docker container and image name      |
| `containerUser`   | string  | `researcher`      | Non-root user inside the container   |
| `pythonVersion`   | string  | `3.12`            | Python version to install            |
| `baseImage`       | string  | `ubuntu:24.04`    | Base Docker image                    |
| `workspaceRoot`   | string  | `/workspace`      | Mount point for the workspace volume |
| `packageManager`  | string  | `pip`             | Package manager: `pip`, `conda`, or `mamba` |
| `networkIsolation`| boolean | `false`           | Disable outbound network access      |
| `pipInstallFlags` | string  | `--prefer-binary` | Extra flags passed to `pip install` during the image build |
| `neverSleep`      | boolean | `false`           | Keep the host awake (`caffeinate`) while the container runs; macOS only, ignored elsewhere |
| `dashboardPort`   | integer | `0`               | The project's dashboard port. `0` means "not yet assigned": the first launch picks a free port and writes it back here so the same port is reused on every restart. A non-zero value must be 1024–65535 |
| `cpuLimit`        | integer | `0`               | Cap on container CPU cores. `0` means no explicit limit (all host cores minus one); a positive value is clamped to the host's core count |
| `memoryLimitGigabytes` | float | `0.0`         | Container memory cap in GB. `0` means unlimited; a non-zero value must be at least `0.25` |

### List Fields

| YAML Key          | Element Type | Description                            |
|-------------------|-------------|----------------------------------------|
| `repositories`    | dict        | Repository definitions (see below)     |
| `systemPackages`  | string      | APT packages to install                |
| `pythonPackages`  | string      | pip packages to install                |
| `condaPackages`   | string      | **Refused at validation** — see below  |
| `binaries`        | dict        | Pre-built binaries to download         |
| `ports`           | dict        | Ports to expose from the container     |
| `bindMounts`      | dict        | Host directories to mount              |
| `secrets`         | dict        | Secret references (see Security below) |

```{note}
**A user-supplied `systemPackages` list replaces the default set — it
does not extend it.** The defaults are `gcc`, `make`, `git`, `curl`,
`ca-certificates`, `gnupg`, `gosu`, and `time`. If you set
`systemPackages` in `vaibify.yml`, include any of those you still
need alongside your additions.
```

```{note}
**`condaPackages` is refused, not installed.** A non-empty value fails
validation. The image installs Miniforge, but there is no
`conda install` step and no build argument carries the list, so
accepting the field would produce a container without the requested
packages and say nothing. Refusing is the honest interim until the
install step is wired; install what you need with `pythonPackages`, or
add a `conda install` line to `container.conf`.
```

### Features Block

Nested under the `features` key:

| YAML Key     | Type    | Default | Description                     |
|--------------|---------|--------|---------------------------------|
| `jupyter`    | boolean | `false` | Install JupyterLab              |
| `rLanguage`  | boolean | `false` | Install R and IRkernel           |
| `julia`      | boolean | `false` | Install Julia                    |
| `database`   | boolean | `false` | Install PostgreSQL client        |
| `dvc`        | boolean | `false` | Install DVC for data versioning  |
| `nestedSampling` | boolean | `false` | Install MultiNest, pymultinest and ultranest (adds a Fortran/LAPACK toolchain and a from-source build) |
| `latex`      | boolean | `true`  | Install TeX Live                 |
| `claude`     | boolean | `false` | Install Claude Code CLI          |
| `claudeAutoUpdate` | boolean | `true` | Allow Claude Code to update itself |
| `codex`      | boolean | `false` | Install OpenAI Codex CLI         |
| `codexAutoUpdate` | boolean | `true` | Update Codex when the container starts |
| `gemini`     | boolean | `false` | Install Google Gemini CLI        |
| `geminiAutoUpdate` | boolean | `true` | Allow Gemini CLI to update itself |
| `opencode`   | boolean | `false` | Install OpenCode CLI             |
| `opencodeAutoUpdate` | boolean | `true` | Update OpenCode when the container starts |
| `cline`      | boolean | `false` | Install Cline CLI                |
| `clineAutoUpdate` | boolean | `true` | Update Cline when the container starts |
| `openhands`  | boolean | `false` | Install OpenHands CLI            |
| `openhandsAutoUpdate` | boolean | `true` | Update OpenHands when the container starts |
| `pi`         | boolean | `false` | Install Pi coding agent          |
| `piAutoUpdate` | boolean | `true` | Update Pi when the container starts |
| `gpu`        | boolean | `false` | Enable NVIDIA GPU passthrough    |

All enabled CLIs receive the same Vaibify context, skills, persistent
configuration directory, and `vaibify-do` dashboard bridge. Auto-updates
need network access; when `networkIsolation` is enabled, Vaibify records a
startup warning that the update was deferred.

### Reproducibility Block

Nested under the `reproducibility` key:

| YAML Key        | Type   | Default     | Description                    |
|-----------------|--------|------------|--------------------------------|
| `zenodoService` | string | `sandbox`   | `sandbox` or `production`      |
| `latexRoot`     | string | `src/tex`   | Path to LaTeX source files     |
| `figuresRoot`   | string | `src/tex/figures` | Path to generated figures |

#### Overleaf Sub-Block

Nested under `reproducibility.overleaf`:

| YAML Key          | Type   | Default    | Description                    |
|-------------------|--------|-----------|--------------------------------|
| `projectId`       | string | `""`       | Overleaf project identifier    |
| `figureDirectory` | string | `figures`  | Target directory in Overleaf   |
| `pullPaths`       | list   | `[]`       | Paths to sync from Overleaf    |

### Example

```yaml
projectName: earth-water-study
containerUser: researcher
pythonVersion: "3.12"
baseImage: ubuntu:24.04
workspaceRoot: /workspace
packageManager: pip
networkIsolation: false

systemPackages:
  - gcc
  - make
  - git
  - curl

pythonPackages:
  - numpy
  - matplotlib
  - h5py

features:
  jupyter: true
  latex: true

reproducibility:
  zenodoService: sandbox
  latexRoot: src/tex
  figuresRoot: src/tex/figures
```

## container.conf

A line-oriented file listing repositories to clone and install. Each
non-comment line has four pipe-separated fields:

```
name|url|branch|install_method
```

### Install Methods

| Method         | Action                                         |
|----------------|-------------------------------------------------|
| `c_and_pip`    | `make opt` then `pip install -e . --no-deps`    |
| `pip_no_deps`  | `pip install -e . --no-deps`                     |
| `pip_editable` | `pip install -e .`                                |
| `scripts_only` | Add to `PYTHONPATH` and `PATH` only              |
| `reference`    | Clone for reference, do not install              |

### Example

```
mycode|git@github.com:user/mycode.git|main|pip_editable
data-utils|git@github.com:user/data-utils.git|develop|pip_no_deps
```

## project.json

Defines the execution pipeline. It lives at
`.vaibify/projects/project.json` inside the project repository — not
at the repository root — which is where the dashboard and `vaibify
run` discover it. See [Pipelines](pipelines.md) for full
documentation.

## Environment variables

### `VAIBIFY_HUB_IDLE_TIMEOUT_SECONDS`

How long a hub or viewer server may sit idle before it self-retires.
The default is `1800` (30 minutes). Set this to a smaller value to
reap abandoned servers sooner, or a larger value to keep them alive
longer.

```bash
VAIBIFY_HUB_IDLE_TIMEOUT_SECONDS=60 vaibify
```

Self-shutdown only fires when **no browser tab is connected** and **no
pipeline is running** in any container the server holds; an open
dashboard keeps the server alive indefinitely. See the
[Session & container-lock lifecycle](architecture.md#single-browser-session-per-container)
section for the full rationale.

## Security

Secrets are never stored in configuration files. The `secrets` field in
`vaibify.yml` lists secret *references* (names), not values. At
build time, Vaibify delegates to the host's credential manager
(e.g., `gh auth`, OS keychain) to resolve secrets. See the
[Reproducibility](reproducibility.md) page for details on how secrets
interact with published projects.
