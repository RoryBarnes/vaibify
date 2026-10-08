# Project Templates

Every vaibify environment starts from a **template**: a small starter
set of files copied into the environment's directory when it is
created. You pick one on the **Template** page of the **Create New**
wizard (the **+** on the environments page), or from a terminal:

```bash
vaibify init                     # list the installed templates
vaibify init --template <name>   # scaffold the current directory
```

`vaibify init` copies the template's files into the current directory,
moves any `project.json` into `.vaibify/projects/`, and writes a
`vaibify.yml` from the built-in defaults. It refuses, before writing
anything, when `.vaibify/projects/project.json` already exists.

## The shipped templates

Three templates ship with vaibify, one for each way of working: free
exploration, co-developing software, and a reproducible Project.

| Template | Contains | Use it when |
|---|---|---|
| `sandbox` | `container.conf` only; no Project file. | You want a clean environment for free-form exploration: write code, make plots, try ideas. |
| `toolkit` | `container.conf`, a `project.json` with no steps, and a `README.md` explaining repository tracking. | You are developing several code repositories that must work together and want each one's git status and push controls side by side. |
| `workflow` | `container.conf`, a two-step example `project.json`, and the two step directories with their scripts. | You already know the analysis is a sequence of steps that must be reproducible. |

If you are unsure, start with `sandbox`. Nothing about the choice is
permanent: exploratory work can be promoted to a Project later (see
[Promoting exploratory work to a Project](#promoting-exploratory-work-to-a-project)).

### sandbox

A sandbox asks nothing of your work. There are no steps, no tests, and
no self-consistency requirement, so it sits below Level 1 of
[the PROOF Ladder](proofLadder.md): its only property is containment.
Opening a sandbox environment lands on its **Project Hub**, where
**Blank Project** opens the dashboard with a terminal and the file tree
but no pipeline. Without a Project file the left panel shows the
**Files**, **Repos**, and **Logs** tabs.

### toolkit

A toolkit is a sandbox for co-developing software. The wizard asks for
the repositories to clone and requires at least one URL. Each listed
repository is cloned into the workspace on first start and tracked
automatically, so it appears in the **Repos** panel with its branch,
uncommitted changes, and push controls. A repository you clone later
from the terminal is detected on the next poll, and the panel asks
whether to **Track** or **Ignore** it. As with a sandbox, vaibify
requires no particular outcome.

### workflow

The workflow template is a runnable two-step Project, so a new
environment produces a figure on its first **Run**:

| Step | Command | Output |
|---|---|---|
| `GenerateSamples` | `python3 generateSamples.py --count 500 --seed 12345 --output samples.json` | `samples.json` |
| `PlotHistogram` | `python3 plotHistogram.py --samples {step:generate-samples.samples} --output {sPlotDirectory}/histogram.{sFigureType}` | `{sPlotDirectory}/histogram.{sFigureType}` |

Both scripts use only the Python standard library, because a fresh
environment has no extra packages installed. The second step receives
the first step's output through the `{step:generate-samples.samples}`
token rather than a path written inside the script. That token is what
tells vaibify the second step depends on the first, so the histogram
is marked stale whenever the samples change. Replace both steps with
your own; [Environments and Projects](environmentsAndProjects.md)
describes the step format and the token rules.

## Promoting exploratory work to a Project

A **Project** is a Project file (JSON, in `.vaibify/projects/`) kept
inside a git repository: the steps vaibify tracks, tests, and grades
on the PROOF Ladder. Exploratory
work becomes a Project with a button, and which button depends on
where the work lives.

**In a container.** Open the **Files** tab and navigate into a
directory directly under the workspace root. A **Make this directory a
Project** bar appears; give the Project a name and press **Make
Project**. Vaibify makes the directory a git repository if it is not
one, adds a first commit if it has none (existing history is never
rewritten), writes a Project file named after the Project, and tracks
the repository so the dashboard lists it. Running it again reports what was already true.
An in-container agent can request the same action.

**A new Project in an existing environment.** **New Project** on the
Project Hub, or **+ New Project…** in the toolbar's project list, opens
a three-step wizard: a display name, a location (an existing git
directory, or a new directory vaibify creates and initializes), and a
confirmation. One environment can hold several Projects.

**On this computer (host mode).** Every host environment starts as a
sandbox, whichever template created it, and shows a **Convert to Project…** bar on its **Files** tab. The wizard
first asks for a destination:

- **Host Project** keeps running directly on this machine. It gains a
  name and is tracked as a Project, but no container is built.
- **Containerized Project** collects container settings, lets you
  choose which of the directory's files to copy into the container,
  and builds an image.

A host environment can also be containerized from its tile's **⋮**
menu with **Containerize Environment**. That path skips the
destination question, and it writes a starter `project.json` if the
directory has none, because a container is always a Project.

## Creating custom templates

Templates are directories inside the installed package, under
`vaibify/templates/`. Every subdirectory there is offered as a
template, under its directory name, both by `vaibify init` and on the
wizard's Template page. To add one:

1. Create `vaibify/templates/<name>/` in a vaibify source checkout.
2. Add a `container.conf`, as every shipped template does: one
   repository per line in the form `name|url|branch|install_method`,
   with `#` starting a comment. The repositories an environment
   actually clones come from its `vaibify.yml`, which the wizard fills
   from its **Repositories** page, so treat this file as a record of
   the format rather than as build input.
3. Optionally add a `project.json` at the template's top level, and the
   step directories its commands use. Scaffolding moves the file into
   `.vaibify/projects/`. Write every cross-step file reference as a
   `{step:<sStepId>.<stem>}` token.
4. Reinstall the package (`pip install .`). An editable install
   (`pip install -e .`) picks up the new directory without reinstalling.

Do not put a `vaibify.yml` in a template. `vaibify init` and the wizard
always write that file themselves, so a copy shipped in a template
would be overwritten. `__pycache__` directories are skipped when a
template is copied.
