# Workflow Viewer

The workflow viewer is a browser-based GUI for managing and executing
pipelines inside a Vaibify container. Launch it with:

```bash
vaibify gui
```

The viewer opens at `http://127.0.0.1:8050` and communicates with the
running container over the Docker CLI.

## Pipeline Panel

The left panel displays the steps defined in `workflow.json`. Each step
shows its name, working directory, and current status (idle, running,
passed, or failed). From this panel you can:

- **Run All** -- execute every enabled step in order.
- **Run Selected** -- execute only the checked steps.
- **Run From Step** -- execute from a chosen step to the end.
- **Enable / Disable** -- toggle individual steps without removing them.
- **Reorder** -- drag steps to change the execution order.

### Adding a Step

Click **Add Step** to open the step editor. Fill in the step name,
working directory, and commands. The editor has separate fields for data
commands (heavy computation) and plot commands (figure generation), making
it straightforward to re-run just the visualization after adjusting a
plotting script.

### Interactive Steps

Steps can be marked as **interactive** (`bInteractive: true`). When the
pipeline reaches an interactive step it pauses and waits for the user to
confirm before continuing. This is useful for manual inspection or
parameter adjustment between automated stages.

## Figure Viewer

The right panel displays figures produced by the pipeline. Figures are
loaded from the container's plot directory and refresh automatically after
each step completes. Supported formats include PDF, PNG, SVG, and JPG.

The figure viewer provides:

- **Automatic refresh** after each step completes.
- **Full-size preview** by clicking a thumbnail.
- **Side-by-side comparison** of before/after versions of a figure.

## DAG Visualization

The workflow viewer renders the pipeline as a directed acyclic graph (DAG)
showing the dependency structure between steps. The DAG updates in real time
as steps complete. A zoom toolbar allows panning and scaling.

## Integrated Terminal

Click **Terminal** to open a shell session inside the container. The
terminal runs in the browser via WebSocket and behaves like a standard
terminal emulator. Multiple sessions can run concurrently.

## Resource Monitor

The status bar at the bottom displays real-time CPU, memory, and disk
usage of the container. These metrics update continuously via WebSocket.

## Test Generation

The **Tests** panel provides AI-powered test generation for pipeline steps.
Select a step and click **Generate Tests** to produce verification scripts
based on the step's expected outputs. Generated tests can be reviewed,
edited, and added to the step's `saTestCommands` array.

## Sync Controls

The toolbar includes buttons for:

- **Push to Overleaf** -- sync figures to the configured Overleaf project.
- **Archive to Zenodo** -- upload outputs and receive a DOI.
- **Generate LaTeX** -- create `\includegraphics` commands for all figures.

These actions are also available via the CLI (`vaibify publish`).

## Hub Mode

When Vaibify is invoked with no subcommand, it starts in **hub mode** -- a
dashboard that lists all Vaibify projects on the host. Hub mode provides:

- A container registry browser for discovering published images.
- A directory browser for locating project configuration files.
- Quick-launch buttons to start, stop, or open the GUI for any project.

Hub mode binds to the same port (8050) and is intended for users who manage
multiple Vaibify environments.
