# Installing Vaibify

Vaibify runs on macOS and Linux with Python 3.9 or later. It uses Docker
(or [Colima](https://github.com/abiosoft/colima) on macOS) to build and
manage containers.

## Prerequisites

| Requirement  | Version    | Notes                                |
|-------------|-----------|--------------------------------------|
| Python      | 3.9 -- 3.14 | Any CPython release in this range  |
| Docker      | 20.10+    | Or Colima on macOS                   |
| Git         | 2.0+      | For cloning repositories into images |

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

Shell completions and helper commands (`connect_vc`, `vc_push`,
`vc_pull`) are configured automatically the first time any `vaibify`
command is run. No manual step is required.

To force the setup to run again, remove the marker file and invoke any
command:

```bash
rm ~/.vaibify/.setup_done
vaibify --version
```

## Docker on macOS

On macOS, [Colima](https://github.com/abiosoft/colima) is the recommended
Docker runtime. Install it with Homebrew:

```bash
brew install colima docker
colima start --cpu 4 --memory 8
```

When running long builds, prevent macOS from sleeping the VM:

```bash
caffeinate -s vaibify build
```
