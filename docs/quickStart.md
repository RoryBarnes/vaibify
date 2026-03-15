# Quick Start

This guide walks through the minimal steps to get a VaibCask project
running on your machine. For a more detailed walkthrough of the interactive
setup process, see [Setup Wizard](setupWizard.md).

## Prerequisites

- **Docker** (or Colima on macOS)
- **Python 3.9+** with `pip`
- **Git**

## Install VaibCask

```bash
pip install vaibcask
```

Or clone and install in editable mode:

```bash
git clone https://github.com/RoryBarnes/VaibCask.git
cd VaibCask
pip install -e .
```

The installer script handles Docker and shell configuration automatically:

```bash
sh vaibcask/install/installVaibCask.sh
```

## Initialize a Project

Navigate to your project directory and run:

```bash
vaibcask init
```

This creates a `vaibcask.yml` configuration file and a `container.conf`
repository list. Choose a template when prompted:

| Template              | Description                              |
|-----------------------|------------------------------------------|
| `general`             | Empty starting point                     |
| `planetary`           | VPLanet ecosystem (10 repositories)      |
| `reproducible-paper`  | LaTeX manuscript with figures pipeline   |

Or specify a template directly:

```bash
vaibcask init --template planetary
```

## Build the Image

```bash
vaibcask build
```

On first run this installs the base image, system packages, Python
dependencies, and clones all repositories listed in `container.conf`. A
rebuild is only required when `vaibcask.yml` or `container.conf` change.

## Start and Connect

```bash
vaibcask start
```

This launches the container in the background. To open a shell inside it:

```bash
vaibcask connect
```

Or use the standalone script:

```bash
connect_vc
```

## Transfer Files

Copy files into the container:

```bash
vc_push localfile.txt .
vc_push -r results/ project/
```

Copy files out:

```bash
vc_pull output.csv .
vc_pull -r project/results/ ./backup/
```

## Stop the Container

```bash
vaibcask stop
```

The workspace volume persists between sessions. Use `vaibcask destroy`
to remove the container and optionally delete the volume.

## Check Status

```bash
vaibcask status
```

This reports whether the container is running, lists installed repositories,
and shows resource usage.
