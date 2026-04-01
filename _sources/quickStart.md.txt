# Quick Start

This guide walks through the minimal steps to get a Vaibify project
running on your machine. See [Installing Vaibify](install.md) for
detailed installation instructions, or [Setup Wizard](setupWizard.md)
for the interactive configuration walkthrough.

## Initialize a Project

Navigate to your project directory and run:

```bash
vaibify init
```

This creates a `vaibify.yml` configuration file and a `container.conf`
repository list. Choose a template when prompted:

| Template     | Description                                       |
|-------------|---------------------------------------------------|
| `sandbox`   | No workflow. For exploration and interactive use.  |
| `workflow`  | Pipeline steps for reproducible data analysis.    |

Or specify a template directly:

```bash
vaibify init --template sandbox
```

See [Templates](templates.md) for details on each template and how to
create your own.

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

Or use the shell alias (configured automatically on first run):

```bash
vaibify_connect
```

## Transfer Files

Copy files into the container:

```bash
vaibify push localfile.txt /workspace/localfile.txt
```

Copy files out:

```bash
vaibify pull /workspace/output.csv ./output.csv
```

These commands work from any directory. When multiple projects are
registered, specify the target with `--project/-p`:

```bash
vaibify build -p my-project
vaibify start -p my-project
vaibify status -p my-project
vaibify connect -p my-project
vaibify push -p my-project data.csv /workspace/data.csv
vaibify stop -p my-project
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
