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

## Users

Install the latest release from PyPI:

```bash
pip install vaibify
```

This installs everything: the CLI, Docker SDK, keyring integration, and all
data format libraries.

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

All data format libraries (h5py, openpyxl, Pillow, pyarrow, astropy,
scipy, pyvista, pysam, pyreadstat, pyreadr, safetensors, tfrecord, scapy)
are included by default when you install Vaibify. No additional extras are
required. See [Supported Data Formats](testFormats.md) for the complete
list.

Run the tests to verify the installation:

```bash
pytest tests/
```

Tests marked with `docker` require a running Docker daemon:

```bash
pytest -m docker
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

## Browser Compatibility

The Vaibify dashboard runs locally and renders in your default browser.
Vaibify targets evergreen desktop browsers; mobile browsers are out of
scope. Any reasonably current Firefox, Chrome, Edge, or Safari works.
The minimum versions below are where layout primitives Vaibify relies
on (`gap` in flexbox, `position: sticky`, `inset`) all became stable:

| Browser | Minimum version | Released |
|---|---|---|
| Firefox | 66 | March 2019 |
| Chrome / Edge | 87 | November 2020 |
| Safari | 14.1 | April 2021 |

Below these versions some panels will render with collapsed spacing or
misaligned modals, but the underlying functionality still works. The
dashboard does not use any feature that requires the most recent
browser releases.

## Docker on macOS

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
