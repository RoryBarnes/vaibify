# Quick Start

This guide walks through the minimal steps to get a Vaibify project
running on your machine. For a more detailed walkthrough of the interactive
setup process, see [Setup Wizard](setupWizard.md).

## Prerequisites

- **Docker** (or Colima on macOS)
- **Python 3.9+** with `pip`
- **Git**

## Install Vaibify

```bash
pip install vaibify
```

Or clone and install in editable mode:

```bash
git clone https://github.com/RoryBarnes/Vaibify.git
cd Vaibify
pip install -e .
```

The installer script handles Docker and shell configuration automatically:

```bash
sh vaibify/install/installVaibify.sh
```

## Initialize a Project

Navigate to your project directory and run:

```bash
vaibify init
```

This creates a `vaibify.yml` configuration file and a `container.conf`
repository list. Choose a template when prompted:

| Template              | Description                              |
|-----------------------|------------------------------------------|
| `general`             | Empty starting point                     |
| `planetary`           | VPLanet ecosystem (10 repositories)      |
| `reproducible-paper`  | LaTeX manuscript with figures pipeline   |

Or specify a template directly:

```bash
vaibify init --template planetary
```

## Build the Image

```bash
vaibify build
```

On first run this installs the base image, system packages, Python
dependencies, and clones all repositories listed in `container.conf`. A
rebuild is only required when `vaibify.yml` or `container.conf` change.

## Start and Connect

```bash
vaibify start
```

This launches the container in the background. To open a shell inside it:

```bash
vaibify connect
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
vaibify stop
```

The workspace volume persists between sessions. Use `vaibify destroy`
to remove the container and optionally delete the volume.

## Check Status

```bash
vaibify status
```

This reports whether the container is running, lists installed repositories,
and shows resource usage.
