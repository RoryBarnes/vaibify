# QuickStart

Welcome to Vaibify. If you already have Docker/Colima installed, then it will only take five minutes to install it, open
the dashboard, and have a Docker container ready for your first
analysis.

## 1. Install

You need Python 3.9 or later and Docker (or Colima on macOS) running on
your machine. If Docker is not installed, see the [longer install
guide](install.md) for platform-specific instructions; otherwise:

```bash
pip install vaibify
vaibify
```

Run `vaibify` with no arguments in any directory to start the
**hub** — a local web server on `http://127.0.0.1:8050` — in your web browser.

![Vaibify landing page](./images/landing.png)

You should see the Vaibify logo, the tagline, and an empty
**Containers** list. No projects yet — let's create one.

## 2. Create your first container

Click the **+** icon next to *Containers*. Two choices appear:

- **Add Existing** — point at a folder that already has a `vaibify.yml`
  (someone else's project, or one of yours from another machine).
- **Create New** — start a new project from a template.

Click **Create New**. The setup wizard opens.

![Setup wizard, step 1](./images/wizard.png)

The wizard walks you through eight steps. None of them require anything
beyond clicks and short text answers; every step has a `?` button that
explains what the field controls.

| Step | What you do | Default |
|---|---|---|
| 1. Project Directory | Choose a folder on your host (e.g. `~/src/my-analysis`). Vaibify writes `vaibify.yml` here. | — |
| 2. Template | Pick **sandbox** for a clean room, **toolkit** for developing several libraries side-by-side, or **workflow** for a reproducible analysis with predefined steps. | sandbox |
| 3. Project Name | The container name. Lowercase letters, digits, and hyphens. | folder name |
| 4. Python Version | Vaibify supports 3.9 through 3.14. | 3.12 |
| 5. Repositories | Git URLs to clone into the container at startup. Skip if you have none yet. | — |
| 6. Features & Authentication | Toggle Jupyter, R, Julia, LaTeX, Claude Code, and GitHub authentication. | LaTeX on |
| 7. Packages | Extra apt or pip packages on top of the template. | — |
| 8. Summary | Review the choices and create! | — |

Click **Create** on the summary step. Vaibify builds the Docker image
in the background. First builds take five to fifteen minutes depending
on which features you enabled and your network speed; subsequent
rebuilds are much faster because Docker caches the layers.

When the build finishes, the wizard closes and the dashboard opens.

![Container dashboard](./images/dashboard.png)

You are now inside the container's dashboard. The toolbar shows the
container name and (for workflow projects) the active workflow. The
left panel shows pipeline steps. The right side has tabs for the
container's repositories, an embedded terminal, a sync panel for
GitHub/Overleaf/Zenodo, and a figure viewer.

Click in the terminal section to activate it and access a shell session inside the container.
Whatever you do here — installing a package, running a script, launching
Claude Code with `claude` — is sealed inside the container. Your home
directory, your SSH keys, and the rest of your filesystem are not
visible to anything in there.

You have your first Vaibify container.

## 3. Where to next

The dashboard is the everyday workspace; the rest of the docs go
deeper.

- **[The three templates: sandbox, toolkit, workflow](templates.md)** —
  which one to pick and how they differ. *Sandbox* is a clean room.
  *Toolkit* is for developing several peer libraries together. *Workflow*
  is for reproducible multi-step analyses where each step's output gets
  inspected and signed off.
- **[The dashboard tour](dashboard.md)** — every panel in the running
  container's UI: pipeline status dots, the repos panel, the embedded
  terminal, the figure viewer, and the verification state machine that
  records which step outputs you have looked at.
- **[Security model](security.md)** — what Vaibify protects against
  (escaped code, leaked credentials, host filesystem access) and what
  it does not. Worth reading before you let any agent write code in
  your container.
- **[Configuration reference](configuration.md)** — every field in
  `vaibify.yml`, `container.conf`, and `workflow.json`. You almost
  never need to hand-edit these; the wizard writes them for you. 
- **[Connecting external services](connecting-services.md)** — how to
  push to GitHub, sync with Overleaf, and archive a result on Zenodo
  from inside a container. Credentials are resolved from your host's
  keychain at request time and never persisted in the container.
- **[Agent action catalog](dashboard.md#agent-actions)** — the named
  operations an AI coding agent inside the container can ask the
  dashboard to perform on its behalf, and the verification each one
  triggers.
- **[Command line interface](cli.md)** - Vaibify comes with an full command line interface to access your container from a shell running on your host. Push and pull files from the container, access the container terminal from a host terminal (i.e., outside of the vaibify web application), and scripting are all available.

If something goes wrong — Docker not running, port collision, a build
that hangs — the [long-form install guide](install.md) has the
platform-specific troubleshooting.
