# Installing Vaibify

Vaibify runs on macOS and Linux with Python 3.9 or later. It uses Docker
(or [Colima](https://github.com/abiosoft/colima) on macOS) to build and
manage containers.

## Prerequisites

| Requirement      | Version     | Notes                                |
|-----------------|------------|--------------------------------------|
| Python          | 3.9 -- 3.14 | Any CPython release in this range  |
| Docker          | 20.10+     | Or Colima on macOS                   |
| Docker Buildx   | 0.10+      | BuildKit-based image builder         |
| Git             | 2.0+       | For cloning repositories into images |

Python and Git you likely have. For the other two see
[Installing Docker](#installing-docker) below — and note that
neither is needed to start: a **host project** runs on your own
machine with no container at all, which is how the
[QuickStart](quickStart.md) begins.

## Users

Install the latest release from PyPI:

```bash
pip install vaibify
```

This installs the CLI, the Docker SDK, keyring integration, and the
common data format libraries. A few specialist format readers live in
an extra — see [Data Format Libraries](#data-format-libraries) below.

After installation, confirm the CLI is available:

```bash
vaibify --version
```

Multiple Vaibify projects can coexist on the same machine. Each project
gets its own container, image, and workspace volume. Use `vaibify init`
in each project directory to register it, then target any project from
anywhere with `--project/-p`.

## Developers

Clone the repository and install in editable mode:

```bash
git clone https://github.com/RoryBarnes/Vaibify.git
cd Vaibify
pip install -e ".[dev]"
```

The `[dev]` extra adds pytest-asyncio and httpx for running vaibify's
own internal test suite.

## Data Format Libraries

The common formats work out of the box: h5py, openpyxl, Pillow, pyarrow,
astropy and scipy are ordinary dependencies, so `pip install vaibify`
brings them.

The specialist readers are not. pyvista, pysam, pyreadstat, pyreadr,
safetensors, tfrecord and scapy live in the `formats` extra, because
several of them need system libraries that a plain `pip install` cannot
provide. Ask for them explicitly:

```bash
pip install 'vaibify[formats]'
```

See [Supported Data Formats](testFormats.md) for the complete list.

Verify the installation:

```bash
vaibify --version
vaibify doctor
```

`vaibify doctor` runs the environment pre-flight (Docker context,
daemon reachability, Colima health) and prints a status report; it
works before any project exists.

The `pytest` test suite is **not** shipped in the pip package — it
lives in the git repository. To run it, clone the repository and
install the development extras:

```bash
git clone https://github.com/RoryBarnes/vaibify
cd vaibify
pip install -e '.[dev]'
pytest tests/            # add -m docker for the Docker-dependent tests
```

## Shell Helpers

Shell completions and helper commands are configured automatically the
first time any `vaibify` command is run. No manual step is required.
The following aliases are added to your shell configuration:

| Alias | Shorthand | Equivalent |
|---|---|---|
| `vaibify_connect` | `vaib_connect` | `vaibify connect` |
| `vaibify_push` | `vaib_push` | `vaibify push` |
| `vaibify_pull` | `vaib_pull` | `vaibify pull` |

These commands work from any directory on the host. When multiple
projects are registered, specify the target with `--project/-p`:

```bash
vaibify_connect -p my-project
vaibify_push -p my-project data.csv /workspace/data.csv
vaibify_pull -p my-project /workspace/results.csv ./results.csv
```

When only one project is registered, the `--project` flag can be
omitted. See [CLI Reference](cli.md) for details.

To force the setup to run again, remove the marker file and invoke any
command:

```bash
rm ~/.vaibify/.setup_done
vaibify --version
```

## Installing for remote access

If you plan to drive this machine from another one with `vaibify
remote`, vaibify must be on the **non-interactive** PATH of the user
you will connect as. The test is exact:

```bash
ssh this-machine vaibify --version
```

It must print a version. A non-interactive SSH command does not read
the shell files you normally edit -- Ubuntu's default `.bashrc` returns
immediately for them -- so a `pip install --user`, a virtualenv, or a
conda environment activated by your profile will not be found, and
`vaibify remote` will report that the remote produced no startup
record.

Install somewhere already on the default PATH, symlink the entry point
into `/usr/local/bin`, or extend the PATH above the non-interactive
early-exit in that user's shell configuration. Both machines also need
the same vaibify version; a mismatch is refused rather than guessed at.

## Browser Compatibility

The Vaibify dashboard runs locally and renders in your default browser.
Vaibify targets evergreen desktop browsers; mobile browsers are out of
scope. Any reasonably current Firefox, Chrome, Edge, or Safari works.
The minimum versions below are set by the bundled terminal (xterm.js,
which uses optional chaining and `ResizeObserver`), not only by the
layout primitives — the terminal fails to load on older engines:

| Browser | Minimum version | Released |
|---|---|---|
| Firefox | 74 | March 2020 |
| Chrome / Edge | 87 | November 2020 |
| Safari | 14.1 | April 2021 |

Below the Firefox floor the bundled terminal does not load at all
(xterm.js fails to parse), so the in-container agent strip is
unavailable; other panels may also render with collapsed spacing or
misaligned modals. CI exercises the dashboard in Chromium only, so
Firefox and Safari are covered by these version floors rather than by
an automated check.

## Installing Docker

Vaibify does not install a container runtime for you, and it does not
need one until you build or run a container project: host mode works
with no Docker at all. Install it when you want the isolation that
Level 3 reproducibility is defined by.

Whichever platform you are on, confirm the result before going
further. `vaibify doctor` runs the full pre-flight -- Docker context,
daemon reachability, Colima health -- and prints a status report:

```bash
docker info          # must succeed WITHOUT sudo
docker buildx version
vaibify doctor
```

The `docker info` line is the one that catches most problems, and it
must work as your own user: vaibify talks to the daemon as the user
who runs it, never through `sudo`.

### Docker on Linux

Distribution packages are often older than the Buildx floor above, so
install Docker Engine from Docker's own repository, following the
current instructions for your distribution:

- [Ubuntu](https://docs.docker.com/engine/install/ubuntu/)
- [Debian](https://docs.docker.com/engine/install/debian/)
- [Fedora](https://docs.docker.com/engine/install/fedora/)
- [RHEL](https://docs.docker.com/engine/install/rhel/)

Those pages are linked rather than transcribed because the repository
setup changes; a copy here would go stale silently and leave you
debugging a signing key. Install the `docker-buildx-plugin` package
along with the engine -- vaibify builds with BuildKit, and an engine
without Buildx fails at the build rather than at the check.

Then grant your own user access to the daemon, which is what makes the
`docker info` check above pass without `sudo`:

```bash
sudo usermod -aG docker "$USER"
newgrp docker          # or log out and back in
```

Be aware of what that grants: membership of the `docker` group is
equivalent to root on the host, because a container can mount the host
filesystem. On a shared or sensitive machine, prefer
[rootless mode](https://docs.docker.com/engine/security/rootless/),
which vaibify works with unchanged.

Finally, make sure the daemon starts with the machine:

```bash
sudo systemctl enable --now docker
```

### Docker on macOS

On macOS, [Colima](https://github.com/abiosoft/colima) is the recommended
Docker runtime. Install with Homebrew or MacPorts:

**Homebrew:**

```bash
brew install colima docker docker-buildx
colima start --cpu 4 --memory 8
```

**MacPorts:**

```bash
sudo port install colima docker docker-buildx-plugin
colima start --cpu 4 --memory 8
```

If a Docker build takes more than a few minutes, macOS may sleep the
Colima VM and corrupt the build. Prefix any long-running command with
`caffeinate -s` to prevent this:

```bash
caffeinate -s vaibify build
```
