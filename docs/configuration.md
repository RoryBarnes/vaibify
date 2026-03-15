# Configuration Reference

VaibCask projects are configured through three files in the project
root directory. This page documents every field and option.

## vaibcask.yml

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

### List Fields

| YAML Key          | Element Type | Description                            |
|-------------------|-------------|----------------------------------------|
| `repositories`    | dict        | Repository definitions (see below)     |
| `systemPackages`  | string      | APT packages to install                |
| `pythonPackages`  | string      | pip packages to install                |
| `condaPackages`   | string      | conda/mamba packages to install        |
| `binaries`        | dict        | Pre-built binaries to download         |
| `ports`           | dict        | Ports to expose from the container     |
| `bindMounts`      | dict        | Host directories to mount              |
| `secrets`         | dict        | Secret references (see Security below) |

### Features Block

Nested under the `features` key:

| YAML Key     | Type    | Default | Description                     |
|--------------|---------|--------|---------------------------------|
| `jupyter`    | boolean | `false` | Install JupyterLab              |
| `rLanguage`  | boolean | `false` | Install R and IRkernel           |
| `julia`      | boolean | `false` | Install Julia                    |
| `database`   | boolean | `false` | Install PostgreSQL client        |
| `dvc`        | boolean | `false` | Install DVC for data versioning  |
| `latex`      | boolean | `true`  | Install TeX Live                 |
| `claude`     | boolean | `false` | Install Claude Code CLI          |
| `gpu`        | boolean | `false` | Enable NVIDIA GPU passthrough    |

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

## script.json

Defines the execution pipeline. See [Pipelines](pipelines.md) for full
documentation.

## Security

Secrets are never stored in configuration files. The `secrets` field in
`vaibcask.yml` lists secret *references* (names), not values. At
build time, VaibCask delegates to the host's credential manager
(e.g., `gh auth`, OS keychain) to resolve secrets. See the
[Reproducibility](reproducibility.md) page for details on how secrets
interact with published workflows.
