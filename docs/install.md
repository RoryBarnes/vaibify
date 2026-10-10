# Advanced Installation

The [QuickStart](quickStart.md) gets one project running. This page is
the reference behind it: what vaibify needs from your computer, how to
install it for research or for development, how to set up Docker, and
every field of the project configuration file. The command line has its
own page, the [CLI Reference](cli.md).

## Prerequisites

| Requirement   | Version     | Needed for                                    |
|---------------|-------------|-----------------------------------------------|
| macOS or Linux | any current release | everything                           |
| Python        | 3.9 – 3.14, built with `dir_fd` support (see below) | everything |
| Git           | any current release | cloning repositories, the PROOF Ladder |
| Docker Engine with Buildx, or Colima on macOS | current release | container projects only |

A **host project** runs on your own machine with no container, so
Docker is not needed to start; the [QuickStart](quickStart.md) begins
that way. Install Docker when you want the isolation that
[Level 3 of the PROOF Ladder](proofLadder.md) requires.

A host project reads your files through directory-relative file
access, so it needs a Python whose `os.supports_dir_fd` is not empty:
`python -c "import os; print(bool(os.supports_dir_fd))"` should print
`True`. Some builds, such as the python.org macOS 3.9 installer, are made
without it. On one of those, a host project's file reads and downloads
stop with a message saying so; run vaibify with a conda, Homebrew or
Linux Python instead.

## Installing with pip

### For researchers

```bash
pip install vaibify
vaibify --version
```

This installs two equivalent commands, `vaibify` and the shorthand
`vaib`, together with the Docker SDK, keyring integration, and the
common data-format readers (h5py, openpyxl, Pillow, pyarrow, astropy,
scipy). Running `vaibify` opens the hub in your browser. Checking this
machine before any project exists is done from the command line; see
[CLI Reference](cli.md).

Install into a Python built for your computer's processor. On an Apple
Silicon Mac, an Intel-only Python (an older Anaconda, for example) runs
through Rosetta, and everything installed into it stops working when an
operating-system upgrade removes that translation layer. The two
commands below must name the same architecture:

```bash
file "$(command -v python3)"
uname -m
```

The command-line diagnosis in the [CLI Reference](cli.md) warns when
the Python running it is being translated.

Several projects can live on one machine. Each container project gets
its own image, container, and workspace volume. The **Create New**
wizard (the **+** on the environments page) registers a project, and
the hub then lists it whichever directory you start it from.

### For developers

```bash
git clone https://github.com/RoryBarnes/Vaibify.git
cd Vaibify
pip install -e ".[dev]"
```

The test suite lives in the repository, not in the pip package. How to
run it, including the browser tests, is in [Developers](developers.md).

### Optional extras

| Extra     | Adds                                                          |
|-----------|---------------------------------------------------------------|
| `formats` | Specialist data readers: pyvista, pysam, pyreadstat, pyreadr, safetensors, tfrecord, scapy. Several need system libraries that pip cannot provide |
| `replay`  | detect-secrets, used to redact credentials from the Prompt Record |
| `dev`     | Everything needed to run vaibify's own test suite             |
| `browser` | Playwright, for the browser test lane                         |

```bash
pip install 'vaibify[formats]'
```

The data formats vaibify's tests can read are listed in
[Testing Model](testing.md).

## Docker

Vaibify does not install a container runtime for you. Whichever
platform you use, confirm the result before building anything:

```bash
docker info          # must succeed WITHOUT sudo
docker buildx version
```

`docker info` catches most problems, and it must work as your own
user: vaibify talks to the daemon as the user who runs it, never
through `sudo`. Vaibify follows your active Docker context; a
`DOCKER_HOST` you export yourself always takes precedence.

### Docker on Linux

Install Docker Engine from Docker's own repository, following the
current instructions for your distribution
([Ubuntu](https://docs.docker.com/engine/install/ubuntu/),
[Debian](https://docs.docker.com/engine/install/debian/),
[Fedora](https://docs.docker.com/engine/install/fedora/),
[RHEL](https://docs.docker.com/engine/install/rhel/)). Distribution
packages are often too old. Install the `docker-buildx-plugin` package
with the engine: vaibify builds with BuildKit.

Then let your user reach the daemon and start it with the machine:

```bash
sudo usermod -aG docker "$USER"
newgrp docker                      # or log out and back in
sudo systemctl enable --now docker
```

Membership in the `docker` group is equivalent to root on the host,
because a container can mount the host filesystem. On a shared or
sensitive machine, prefer
[rootless mode](https://docs.docker.com/engine/security/rootless/).

On Linux a container reaches the vaibify hub through the Docker bridge
gateway (`172.17.0.1` unless the daemon is configured otherwise), not
through loopback, so the hub also listens on that address. It asks the
daemon for the address when it starts and prints what it bound. If the
daemon was not running at that moment, the hub listens on loopback
only: the dashboard works, but `vaibify-do` inside the container cannot
reach the hub. Start the daemon, then restart `vaibify`.

### Docker on macOS

[Colima](https://github.com/abiosoft/colima) is the recommended Docker
runtime on macOS. Install it with Homebrew or MacPorts, then start it
with enough CPU and memory for your work:

```bash
# Homebrew
brew install colima docker docker-buildx
# MacPorts
sudo port install colima docker docker-buildx-plugin

colima start --cpu 4 --memory 8
```

The command-line diagnosis in the [CLI Reference](cli.md) compares
what a project will request against what the Colima virtual machine
has. If macOS sleeps during a long build, the Colima virtual machine
can corrupt it; prefix long commands with `caffeinate -s`:

```bash
caffeinate -s vaibify
```

The `neverSleep` field in `vaibify.yml` does the same for as long as a
container runs.

## Shell helpers

The first `vaibify` command after an install or upgrade also sets up
shell completion and a few helper aliases. They are described in the
[CLI Reference](cli.md).

## `vaibify: command not found` after an upgrade

The `vaibify` command is a short launcher whose first line names the
Python that installed it. When that Python stops working, the launcher
fails with it, and your shell usually reports only
`command not found`. The two common causes are a Python built for a
different processor whose translation layer an operating-system upgrade
removed (on Apple Silicon the tell is `Bad CPU type in executable`),
and a Python that a package manager replaced or removed.

```bash
find "$HOME" /opt /usr/local -maxdepth 4 -name vaibify -type f 2>/dev/null
head -1 <that path>
```

Run the interpreter named on that line. If it fails, install a Python
that runs natively on this machine (see
[For researchers](#for-researchers)) and reinstall vaibify into it.

## Browser compatibility

The dashboard runs locally and opens in your default browser. Vaibify
supports current desktop releases of Firefox, Chrome, Edge, and Safari;
mobile browsers are not supported. The bundled terminal (xterm.js 5.5)
and PDF viewer (pdf.js 3.11) set the minimum versions:

| Browser       | Dashboard and terminal | PDF figures |
|---------------|------------------------|-------------|
| Firefox       | 74                     | 94          |
| Chrome / Edge | 87                     | 98          |
| Safari        | 14.1                   | 15.4        |

Below the first floor the terminal does not load; between the two, the
dashboard works but PDF figures do not render. Continuous integration
runs the browser tests in Chromium, Firefox, and WebKit.

## Installing for remote access

Remote access drives a hub on another machine over SSH (see
[Connecting to External Resources](externalResources.md)); it is
started from the command line ([CLI Reference](cli.md)). For it to
work, vaibify must be on the **non-interactive** PATH of the user you
connect as, and this must print a version:

```bash
ssh other-machine vaibify --version
```

A non-interactive SSH command does not read the shell files you
normally edit, so a `pip install --user`, a virtual environment, or a
conda environment activated by your profile is not found, and the
connection reports that the remote produced no startup record.
Install somewhere already on the default PATH, link the entry point
into `/usr/local/bin`, or extend PATH before the non-interactive early
exit in that user's shell configuration. Install the same vaibify
version on both machines; a protocol mismatch is refused.

## Uninstalling vaibify

Removing the Python package leaves your environments, their Docker
images, and vaibify's settings in place, so do these in order:

1. **Delete the environments you no longer want.** In the hub, open each
   tile's **⋮** menu and choose **Delete environment…**. This removes
   the container, its workspace volume, and its images; your project
   directory and anything already pushed or published are untouched.
   See [Environments and Projects](environmentsAndProjects.md).
2. **Revoke stored credentials** you no longer need, on each service's
   own account page; see [Connecting to External
   Resources](externalResources.md).
3. **Stop the hub.** Press Ctrl-C in the terminal where you started
   `vaibify`. Closing the browser tab does not stop it.
4. **Remove the package:** `python3 -m pip uninstall vaibify`.
5. **Remove vaibify's settings and logs:** delete `~/.vaibify`.
6. **Remove the shell helpers.** Delete each block that begins with the
   comment `# Added by Vaibify` from your shell's configuration file
   (`~/.zshrc`, `~/.bash_profile` or `~/.bashrc`, or
   `~/.config/fish/config.fish`).

Docker and Colima are not part of vaibify and stay installed.

## Configuration reference

### Where vaibify keeps its files

| File | Holds |
|------|-------|
| `vaibify.yml` (project directory) | The project's container configuration, documented below |
| `.vaibify/projects/project.json` (project repository) | The workflow: steps, commands, tests. See [Environments and Projects](environmentsAndProjects.md) |
| `~/.vaibify/registry.json` | The registered projects the hub lists |
| `~/.vaibify/preferences.json` | Host-wide dashboard settings, such as the timeouts below |
| `~/.vaibify/hub-port.json` | The port the last hub used |
| `~/.vaibify/vaibify.log` | A rotating log (10 MB per file, five backups); each line is tagged `[cid:<container-id>]`. Look here first when something misbehaves |

### vaibify.yml fields

Keys are camelCase. Every field except `projectName` has a default.

| Key | Type | Default | Meaning |
|-----|------|---------|---------|
| `projectName` | string | required | Project, container, image, and volume name. A container project's name must be lowercase letters, digits, `.`, `_`, `-` |
| `containerUser` | string | `researcher` | Unprivileged user inside the container. `root` is refused |
| `pythonVersion` | string | `3.12` | Major.minor only (`3.12`, not `3.12.1`) |
| `baseImage` | string | `ubuntu:24.04` | Left out or set to `ubuntu:24.04`, the build uses the digest-pinned image named in vaibify's Dockerfile. Another Ubuntu release is refused |
| `workspaceRoot` | string | `/workspace` | Absolute path where the workspace volume mounts |
| `packageManager` | string | `pip` | `pip`, `conda`, or `mamba` (the last two install Miniforge) |
| `pipInstallFlags` | string | `--prefer-binary` | Extra `pip install` flags for the image build; `--prefer-binary` is always kept |
| `networkIsolation` | boolean | `false` | Block all network traffic in and out of the container |
| `x11Forwarding` | boolean | `false` | Let graphical programs in the container open windows on your display. A connected client can read the screen and send input (see [Security Model](security.md)). Refused with `networkIsolation: true`. Takes effect when the container is created |
| `neverSleep` | boolean | `false` | Keep the Mac awake (`caffeinate`) while the container runs. Ignored elsewhere |
| `dashboardPort` | integer | `0` | Port for the single-project viewer started from the command line ([CLI Reference](cli.md)). `0` means unassigned: the first launch picks a free port and writes it here. Otherwise 1024–65535 |
| `cpuLimit` | integer | `0` | CPU cores for the container. `0` means all host cores minus one; a larger value is clamped to the host's core count. See [Core allocation](environmentsAndProjects.md#core-allocation) |
| `memoryLimitGigabytes` | number | `0` | Memory cap in GB (1 GB = 2^30 bytes). `0` means unlimited; otherwise at least `0.25`. The cap includes swap, and at the cap the kernel kills a process in the container. See [Core allocation](environmentsAndProjects.md#core-allocation) |

List fields:

| Key | Each entry | Meaning |
|-----|------------|---------|
| `repositories` | `name`, `url`, `branch`, `installMethod` (default `pip_editable`), optional `destination` | Repositories cloned into the workspace, in order. See [container.conf](#containerconf) for install methods |
| `systemPackages` | string | APT packages. A list you supply **replaces** the defaults (`gcc`, `make`, `git`, `curl`, `ca-certificates`, `gnupg`, `gosu`, `time`), so include the ones you still need |
| `pythonPackages` | string | pip packages |
| `condaPackages` | string | **Refused.** A non-empty list fails validation because the build has no conda install step; use `pythonPackages` |
| `binaries` | `name`, `path` | Executables already in the container; each `name` becomes an environment variable holding `path`, and its directory joins PATH |
| `ports` | `container`, optional `host` (defaults to `container`), optional `lanExpose` | Ports forwarded from the container, bound to `127.0.0.1` unless `lanExpose: true` |
| `bindMounts` | `host`, `container` | Host directories mounted into the container (rules below) |
| `secrets` | `name`, `method` (`gh_auth`, `keyring`, or `docker_secret`) | Credentials resolved on the host at start and mounted read-only at `/run/secrets/<name>`. A secret this host cannot resolve is skipped with a notice. Values never appear in `vaibify.yml` |

A `bindMounts` host path must be absolute (Docker does not expand `~`),
must exist, and must lie under your home directory or the project
repository. Vaibify refuses any mount that overlaps a credential
directory (such as `~/.ssh` or `~/.config/gh`), `/etc`, `/root`, a Docker
daemon socket, or vaibify's own control, journal, and temporary
directories under `~/.vaibify`, however the path is spelled.

### Features

Nested under `features`. All values are booleans.

| Key | Default | Installs |
|-----|---------|----------|
| `latex` | `true` | TeX Live |
| `jupyter` | `false` | JupyterLab |
| `rLanguage` | `false` | R and IRkernel |
| `julia` | `false` | Julia |
| `database` | `false` | PostgreSQL and SQLite clients, psycopg2, SQLAlchemy |
| `dvc` | `false` | DVC, for versioning data |
| `nestedSampling` | `false` | MultiNest, pymultinest, ultranest (adds a Fortran toolchain and a source build) |
| `claude`, `codex`, `gemini`, `antigravity`, `opencode`, `cline`, `openhands`, `pi` | `false` | One terminal coding agent each: Claude Code, Codex, Gemini CLI, Antigravity, OpenCode, Cline, OpenHands, Pi |
| `<agent>AutoUpdate` (for example `claudeAutoUpdate`) | `true` | Keep that agent current. Needs network access; with `networkIsolation` the update is deferred and a warning recorded |
| `gpu` | `false` | NVIDIA GPU support. **Builds with `gpu: true` are refused**: the GPU base image is Ubuntu 22.04 and vaibify's toolchain is pinned to Ubuntu 24.04 |

Every enabled agent receives the same vaibify context, skills,
persistent configuration directory, and `vaibify-do` bridge to the
dashboard.

### Reproducibility

Nested under `reproducibility`; how these are used is described in
[Reproducibility](reproducibility.md).

| Key | Default | Meaning |
|-----|---------|---------|
| `zenodoService` | `sandbox` | `sandbox` or `production` |
| `latexRoot` | `src/tex` | LaTeX source directory |
| `figuresRoot` | `src/tex/figures` | Generated figure directory |
| `overleaf.projectId` | `""` | Overleaf project identifier |
| `overleaf.figureDirectory` | `figures` | Figure directory in Overleaf |
| `overleaf.pullPaths` | `[]` | Paths to pull from Overleaf |

### Example

```yaml
projectName: my-analysis
pythonVersion: "3.12"
pythonPackages:
  - numpy
  - matplotlib
repositories:
  - name: analysis-code
    url: https://github.com/example/analysis-code.git
    branch: main
    installMethod: pip_editable
features:
  jupyter: true
  claude: true
cpuLimit: 4
```

### container.conf

The build turns `repositories` into a pipe-delimited `container.conf`
in the image's build context and bakes it into the image at
`/etc/vaibify/container.conf`. Project templates carry one too, which
the wizards read to fill in `repositories`. Each non-comment line is:

```
name|url|branch|install_method[|destination]
```

| Install method | Action |
|----------------|--------|
| `pip_editable` | `pip install -e .` (needs `setup.py` or `pyproject.toml`; without one the repository is cloned only, with a warning) |
| `pip_no_deps`  | `pip install -e . --no-deps` |
| `c_and_pip`    | `make opt`, then `pip install -e . --no-deps` |
| `scripts_only` | Add to `PYTHONPATH` and `PATH` only |
| `reference`    | Clone only; do not install |

`destination` moves the clone to that workspace-relative path. A URL
must use `https://`, `http://`, `git://`, `ssh://`, or the
`user@host:path` form.

### Checks before a build

The dashboard's **Build** asks these questions before spending an hour
on a build:

| Field | Checked against | Refused when |
|-------|-----------------|--------------|
| `containerUser`, `pythonVersion`, `workspaceRoot` | their required format | the image recipe cannot use the value |
| `baseImage`, `features.gpu` | the pinned Ubuntu 24.04 toolchain | the name says another Ubuntu release, or the GPU feature is on |
| `systemPackages` | Launchpad, for the release `baseImage` names | Ubuntu publishes no such package |
| `pythonPackages` | pypi.org's simple index | the index has no such project |
| `repositories[].branch` | `git ls-remote` on the remote | the remote has no such branch |

An index or remote that cannot be reached, a `pipInstallFlags` that
names another index, and a `baseImage` vaibify cannot identify are
reported as "not checked", and the build proceeds.

### Environment variables

These change host-wide behavior. Timeout values are seconds, or
`never` (also `off`, `none`, `disabled`); a malformed value is ignored.

| Variable | Default | Effect |
|----------|---------|--------|
| `VAIBIFY_HUB_IDLE_TIMEOUT_SECONDS` | never (browser launch); 1800 (`--no-browser`) | How long a hub with **no connected dashboard and no running pipeline** waits before shutting itself down. `0` shuts down as soon as it is idle. Overrides the **Idle shutdown** setting in the toolbar gear menu |
| `VAIBIFY_ABSOLUTE_SESSION_CAP_SECONDS` | 604800 (7 days) | How long one browser tab's session lasts from when it opened, used or not. When it ends, the container and any running step keep going; a fresh tab is opened from the command line ([CLI Reference](cli.md)). Overrides the **Session lifetime** setting |
| `VAIBIFY_SLIDING_IDLE_SECONDS` | 3600 | Ends a browser session after this long with no activity. A connected tab counts as activity |
| `VAIBIFY_RECONNECT_WINDOW_SECONDS` | 15 | How long a local session is held for a reconnecting tab |
| `VAIBIFY_REMOTE_RECONNECT_WINDOW_SECONDS` | 900 | The same, for a session on another machine opened over SSH |
| `VAIBIFY_SUPPRESS_BROWSER` | unset | Any value has the same effect as the `--no-browser` option ([CLI Reference](cli.md)) |

Each variable outranks the stored setting, which outranks the default.
The two settings apply without restarting the hub. You are warned at
three quarters, nine tenths, and nineteen twentieths of the session
lifetime, and each warning offers to renew the session.
