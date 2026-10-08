# Advanced Installation

The [QuickStart](quickStart.md) gets one project running. This page is
the reference behind it: what vaibify needs from your computer, how to
install it for research or for development, how to set up Docker, every
field of the project configuration file, and the command-line
interface.

## Prerequisites

| Requirement   | Version     | Needed for                                    |
|---------------|-------------|-----------------------------------------------|
| macOS or Linux | any current release | everything                           |
| Python        | 3.9 – 3.14  | everything                                    |
| Git           | any current release | cloning repositories, the PROOF Ladder |
| Docker Engine with Buildx, or Colima on macOS | current release | container projects only |

A **host project** runs on your own machine with no container, so
Docker is not needed to start; the [QuickStart](quickStart.md) begins
that way. Install Docker when you want the isolation that
[Level 3 of the PROOF Ladder](proofLadder.md) requires.

## Installing with pip

### For researchers

```bash
pip install vaibify
vaibify --version
vaibify doctor
```

This installs two equivalent commands, `vaibify` and the shorthand
`vaib`, together with the Docker SDK, keyring integration, and the
common data-format readers (h5py, openpyxl, Pillow, pyarrow, astropy,
scipy). `vaibify doctor` checks this machine and works before any
project exists; see [vaibify doctor](#vaibify-doctor).

Install into a Python built for your computer's processor. On an Apple
Silicon Mac, an Intel-only Python (an older Anaconda, for example) runs
through Rosetta, and everything installed into it stops working when an
operating-system upgrade removes that translation layer. The two
commands below must name the same architecture:

```bash
file "$(command -v python3)"
uname -m
```

`vaibify doctor` warns when the Python running it is being translated.

Several projects can live on one machine. Each container project gets
its own image, container, and workspace volume. `vaibify init` (or the
dashboard's container wizard) registers a project, and any command can
then target it from any directory with `--project/-p`.

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
vaibify doctor
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

`vaibify doctor` compares what a project will request against what the
Colima virtual machine has. If macOS sleeps during a long build, the
Colima virtual machine can corrupt it; prefix long commands with
`caffeinate -s`:

```bash
caffeinate -s vaibify build
```

The `neverSleep` field in `vaibify.yml` does the same for as long as a
container runs.

## Shell helpers

The first `vaibify` command after an install or upgrade configures
shell completion and three helper aliases for bash, zsh, and fish. The
setup only appends to your shell's configuration file (`~/.zshrc`;
`~/.bash_profile` on macOS or `~/.bashrc` on Linux;
`~/.config/fish/config.fish`) and never edits or removes a line.

| Alias             | Shorthand      | Runs              |
|-------------------|----------------|-------------------|
| `vaibify_connect` | `vaib_connect` | `vaibify connect` |
| `vaibify_push`    | `vaib_push`    | `vaibify push`    |
| `vaibify_pull`    | `vaib_pull`    | `vaibify pull`    |

```bash
vaibify_connect -p my-analysis
vaibify_push -p my-analysis data.csv /workspace/data.csv
vaibify_pull -p my-analysis /workspace/results.csv ./results.csv
```

On macOS the setup also links Colima's Docker socket to
`/var/run/docker.sock` when that path is free and writable without
`sudo`.

### Tab completion

Press TAB after `vaibify push` or `vaibify pull` (or their aliases) to
complete a path inside the project. `vaibify pull <TAB>` offers paths in
the container; `vaibify push data.csv <TAB>` offers container paths for
the destination. Container paths may be relative to the workspace root
(`StepName/output.csv`) or absolute. `-p NAME` is honored. A host
project has no container, so its paths complete from the project's own
directory. Nothing is offered while the container is stopped. In bash,
a path containing `=` or `:` is not completed.

### If TAB does nothing

Run `vaibify doctor`. Its `shell-completions` line reads the
configuration file of the shell named in `$SHELL` and, when that file
does not load vaibify's completion script, prints the exact line to
add. To rerun the setup, remove its marker and run any command:

```bash
rm ~/.vaibify/.setup_done
vaibify --version
```

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

`vaibify remote` drives a hub on another machine over SSH (see
[Connecting to External Resources](externalResources.md)). For it to
work, vaibify must be on the **non-interactive** PATH of the user you
connect as, and this must print a version:

```bash
ssh other-machine vaibify --version
```

A non-interactive SSH command does not read the shell files you
normally edit, so a `pip install --user`, a virtual environment, or a
conda environment activated by your profile is not found, and
`vaibify remote` reports that the remote produced no startup record.
Install somewhere already on the default PATH, link the entry point
into `/usr/local/bin`, or extend PATH before the non-interactive early
exit in that user's shell configuration. Install the same vaibify
version on both machines; a protocol mismatch is refused.

## Configuration reference

### Where vaibify keeps its files

| File | Holds |
|------|-------|
| `vaibify.yml` (project directory) | The project's container configuration, documented below |
| `.vaibify/projects/project.json` (project repository) | The workflow: steps, commands, tests. See [Environments and Projects](environmentsAndProjects.md) |
| `~/.vaibify/registry.json` | The registered projects that `--project/-p` resolves |
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
| `dashboardPort` | integer | `0` | Port for `vaibify start --gui`. `0` means unassigned: the first launch picks a free port and writes it here. Otherwise 1024–65535 |
| `cpuLimit` | integer | `0` | CPU cores for the container. `0` means all host cores minus one; a larger value is clamped to the host's core count |
| `memoryLimitGigabytes` | number | `0` | Memory cap in GB. `0` means unlimited; otherwise at least `0.25` |

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

`vaibify build` and the dashboard's Build ask the same questions before
spending an hour on a build:

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
| `VAIBIFY_ABSOLUTE_SESSION_CAP_SECONDS` | 604800 (7 days) | How long one browser tab's session lasts from when it opened, used or not. When it ends, the container and any running step keep going; `vaibify open` gives you a fresh tab. Overrides the **Session lifetime** setting |
| `VAIBIFY_SLIDING_IDLE_SECONDS` | 3600 | Ends a browser session after this long with no activity. A connected tab counts as activity |
| `VAIBIFY_RECONNECT_WINDOW_SECONDS` | 15 | How long a local session is held for a reconnecting tab |
| `VAIBIFY_REMOTE_RECONNECT_WINDOW_SECONDS` | 900 | The same, for a session opened through `vaibify remote` |
| `VAIBIFY_SUPPRESS_BROWSER` | unset | Any value has the same effect as `--no-browser` |

Each variable outranks the stored setting, which outranks the default.
The two settings apply without restarting the hub. You are warned at
three quarters, nine tenths, and nineteen twentieths of the session
lifetime, and each warning offers to renew the session.

## Command-line reference

### Global options and project targeting

| Option | Meaning |
|--------|---------|
| `--config PATH` | Use this `vaibify.yml` |
| `--port N` | Port for the hub. By default the hub reuses the port it used last (so a bookmarked tab keeps working) and moves only if another program holds it; an explicit port is used as given and fails if taken |
| `--no-browser` | Serve without opening a browser. The process still runs in the foreground; it is not a daemon |
| `--version`, `--help` | Print and exit |

Run with no command, `vaibify` starts the **hub**: the dashboard for
all registered projects. Commands that act on a project resolve it in
this order: `--project/-p NAME`, then `--config PATH`, then a
`vaibify.yml` in the current directory, then the only registered
project. With several registered projects and none of these, the
command lists them and exits.

### Commands

`vaibify <command> --help` is the authority on every option.

| Command | Purpose |
|---------|---------|
| `init` | Create a project here from a template (`--template sandbox\|toolkit\|workflow`) or, with `--name` alone, just a `vaibify.yml` (`--minimal` for the smallest one that builds). With neither, lists the templates |
| `register [DIRECTORY]` | Register an existing directory containing a `vaibify.yml`; writes no files |
| `setup` | Open the setup wizard in a browser, on port 8051 |
| `config edit \| export FILE \| import FILE` | Edit `vaibify.yml` in `$EDITOR`, write it to a file, or replace it from one |
| `doctor` | Diagnose this machine, the container, and the project; changes nothing |
| `repair dns` | Clear a container's stale name-resolution state (`--recreate`, `--yes`) |
| `build` | Build the image from `vaibify.yml` (`--no-cache`) |
| `start` | Start the container |
| `stop` | Stop the container; the workspace volume remains |
| `status` | Show container, image, and volume state; `--proof` adds the PROOF level and its blockers, `--json` emits one JSON object |
| `destroy` | Delete the workspace volume, then offer to delete the images |
| `connect` | Open a shell in the running container |
| `push SOURCE DEST` / `pull SOURCE DEST` | Copy files between the host and the workspace; a relative container path is read from `workspaceRoot` |
| `ls [PATH]` / `cat PATH` | List a container directory (`--json`) or print a file; a relative path is read from `/workspace` |
| `verify` | Run the container isolation check |
| `run` | Run every step, one step (`--step N`), or from a step onward (`--from N`); numbers are 1-based |
| `workflow` | Summarize the workflow or one step (`--step N`, `--json`) |
| `test` | Run the tests of every step or one step (`--step N`, `--json`); exits non-zero on any failure |
| `verify-step` | Record your sign-off on a step: `--step` (label such as `A09`, or 1-based number) and `--status passed\|failed\|untested` |
| `generate-standards` | Create or refresh a step's `tests/quantitative_standards.json` from its data (`--step-dir`, or `--workflow` with `--step-label`; `--rtol`, `--detect-stochastic`) |
| `reproduce` | Verify the Level 3 reproducibility envelope, or reproduce a published project |
| `gui` | Open the dashboard; with `-p NAME`, a single-project viewer on port 8050 |
| `open CONTAINER` | Move a container's live session into a fresh browser tab |
| `sessions` / `sessions stop` | List or stop live hubs and viewers |
| `do <action>` | Run any dashboard action from the terminal |
| `reconcile CONTAINER` | Prove a quarantined container's interrupted operations settled |
| `secret set-provider-key \| provider-key-status \| delete-provider-key --provider NAME` | Manage an agent provider's API key in the OS keyring; the key is read from a prompt that does not echo |
| `revoke github\|overleaf\|zenodo` | Remove a stored credential from the keyring and revoke it upstream where possible (`--keyring-slot` for one GitHub repository, `--instance` for Zenodo) |
| `remote DESTINATION` | Open a dashboard for another machine over SSH (`--port`) |
| `remote-helper` | The far end of `vaibify remote`; not run by hand |

Vaibify has no `publish` command. Zenodo and GitHub publication run
through the PROOF Level 2 rows in the dashboard and the matching
`vaibify do` actions.

### vaibify doctor

```bash
vaibify doctor [--quiet] [--build | --start | --container] [--online]
               [--json] [--explain CHECK] [-p NAME]
```

Doctor examines three scopes: **host** (the Python's architecture, the
Docker context and endpoint, the daemon and runtime, storage, memory,
CPU), **container** (network attachments, name resolution, proxy path),
and **project** (vaibify's own records of the project). With no scope
flag every scope runs; `--build` and `--start` run the host checks
relevant to that step, and `--container` runs the container and project
checks. Each check reports `ok`, `warn`, `fail`, or `not checked` (with
the reason; never counted as ok). Every `warn` and `fail` names a next
step for the runtime this machine actually uses. `--explain CHECK`
prints how one check decides.

By default doctor resolves names but opens no connections. `--online`
permits connections to the host your project already depends on (its
first repository's host or the enabled agent's provider), over the port
and proxy your project would use, without authenticating.

| Exit | Meaning |
|------|---------|
| 0 | Everything applicable was assessed and nothing failed |
| 1 | At least one check failed |
| 2 | A scope you named contains a check that could not be assessed |

### vaibify build

```bash
vaibify build [--no-cache] [-p NAME]
```

Runs the [checks before a build](#checks-before-a-build), then builds
the base image and one layer per enabled feature.

### vaibify start

```bash
vaibify start [-d] [--gui [--port N]] [--jupyter] [-p NAME]
              [--image-trust restricted|as-built|inspect] [--with-credentials]
              [COMMAND]
```

| Option | Meaning |
|--------|---------|
| `-d`, `--detach` | Start in the background and return, as a script needs. A `COMMAND` is refused with `--detach` |
| `--gui` | Also open the single-project viewer, on `--port` or the project's `dashboardPort` |
| `--jupyter` | Enable JupyterLab with port forwarding |
| `--image-trust` | How an image vaibify did not build may run. Asked interactively on a terminal; required without one |
| `--with-credentials` | Let that image's code read your stored credentials. Never implied |

Without `--detach`, `start` attaches a terminal to the container. See
[Security Model](security.md) for what each image-trust choice does.

### vaibify reproduce

```bash
vaibify reproduce [--repo PATH] [--rerun] [--workflow NAME] [--skip-tier N]
vaibify reproduce --from SOURCE [--prepare | --rerun] [--workflow NAME] [--allow-emulation]
```

The first form checks a project's Level 3 envelope in tiers: manifest
hashes, the hash-pinned dependency install (which installs into your
current Python environment), the pinned container image, and the
envelope's coherence. `--rerun` adds a fifth tier: the workflow runs in
a disposable container built from the pinned image, its outputs are
re-hashed, and the result is written to `.vaibify/l3_attestation.json`.
`--workflow` is required when the container holds more than one
workflow. Exit 0 means every selected tier passed, 1 that one failed,
2 a usage error.

With `--from`, vaibify reproduces a **published** project from a clone
URL, a `user@host:path` address, or a clean local clone. Alone it
stages and checks a snapshot of one commit; `--prepare` also obtains
the pinned image; `--rerun` reruns the snapshot and writes a report
under `~/.vaibify/reproductions/reports/`, exiting 0 only when the
verdict is *reproduced*. `--allow-emulation` accepts an image built for
another processor, and the verdict says so. See
[Reproducibility](reproducibility.md).

### vaibify gui, the hub, and ports

`vaibify` and `vaibify gui` open the hub. `vaibify gui -p NAME` serves a
single-project viewer on port 8050. Several hubs can run on one
machine; **New vaibify window** in the dashboard starts another on a
free port. Each container may be open in only one browser session at a
time: the hub marks a container held elsewhere "In use in another
browser session", and a second tab or a second `vaibify start` on it is
refused. `vaibify open CONTAINER` moves a held session into a fresh
tab.

### vaibify sessions

Hubs and viewers run in the foreground of the terminal that started
them. Closing a browser tab does not stop them. On the host only:

```bash
vaibify sessions              # pid, role, port, start time, containers held
vaibify sessions stop PID     # stop one session
vaibify sessions stop --all   # stop every session except this one
```

`stop` sends `SIGTERM`, so the server releases its session and
container locks, and it refuses any PID that is not a live vaibify
session.

### vaibify do

Every dashboard action has a `vaibify do` subcommand, generated from
the same catalog the dashboard and the in-container `vaibify-do` use,
so the three cannot drift apart.

```bash
vaibify do                          # list every action
vaibify do run-step --help          # one action's arguments
vaibify do run-step A09             # run one step
vaibify do check-l2-readiness --json
```

| Option | Meaning |
|--------|---------|
| `-p`, `--project` | Target project |
| `--port` | Port of the hub to drive, when several are running |
| `--workflow` | Container path of the `project.json` to use |
| `--json` | One JSON object per line |
| `--dry-run` | Print the call without sending it |
| `--timeout` | Seconds to wait for the hub (`0` waits indefinitely) |

Path parameters are positional; other fields are `key=value` pairs,
or one JSON object. A hub must be running (start one with `vaibify` in
another terminal), and the dashboard sees every action. The command
claims the container for one action and then releases it, so a
container open in a dashboard tab is refused rather than taken over:
close that tab, or run the action from inside the container with
`vaibify-do`. Actions reserved for the researcher, such as
`clean-outputs`, are available here and marked in `--help`.

### vaibify destroy

```{warning}
`vaibify destroy` deletes the project's workspace volume: every file in
the container that has not been committed and pushed. The volume is
Docker-managed storage, not a directory on your computer, so it is the
only copy. The confirmation prompt is the only guard.
```

After the volume, `destroy` offers to remove the project's images. It
does not remove the container itself (stop it with `vaibify stop`) or
the volume that holds the container's stored credentials, which
remains until you remove it with `docker volume rm`.
