# Contributor's Guide

Interested in contributing to Vaibify? This page outlines the steps to make
a meaningful contribution. Before you begin, contact
[Rory Barnes](mailto:rory@astro.washington.edu) to confirm that your
proposed changes are not duplicating work and will be of general interest.

## Style Guide

Vaibify follows the style conventions described in the project's global
development standards: camelCase with Hungarian prefixes for variables,
`f`-prefixed names for functions (with a return-type letter), files in
camelCase without Hungarian prefixes, functions under 20 lines, no
abbreviations for words shorter than 8 characters, and clear naming in
preference to inline comments. If you are developing with an AI coding
agent, read [`AGENTS.md`](https://github.com/RoryBarnes/Vaibify/blob/main/AGENTS.md) at the repo root for the rules,
traps, and discovery commands the agent should follow.

## Running Tests

Run the full test suite:

```bash
pytest tests/
```

Run only tests that require Docker:

```bash
pytest -m docker
```

Run with coverage:

```bash
pytest --cov=vaibify tests/
```

Run the architectural invariant tests directly to verify that route
registration, leaf-module discipline, and the science-agnostic source
rule are intact:

```bash
pytest tests/testArchitecturalInvariants.py -v
```

The suite has three kinds of test — unit/behavior tests, architectural
invariants, and **falsification tests** (kill-confirmed tests, proven to
fail when the guard they defend is broken) — plus a standing re-kill
harness (`python tools/reconfirmFalsification.py`) and a warn-only
cosmic-ray mutation gate that is run **on demand**, not per pull
request. See [Testing](testing.md) for what each is, why falsification
testing matters, and how to run them all.

## Portability and CI

All code must work on both macOS and Linux, and with Python versions
3.9 through 3.14. GitHub Actions runs unit tests on every pull request
across all permutations of Ubuntu 22.04/24.04, macOS 15/26, and
Python 3.9 through 3.14. Tests that require a running Docker daemon
are excluded from CI and run locally. Documentation is rebuilt and
deployed automatically on every merge to main.

A separate CI job (`agent-docs-path-check`) verifies that every path
reference in `AGENTS.md` and `SKILL.md` files resolves to an existing
file. This catches stale references after refactors rename or delete
files.

## Verifying a change reaches the screen

The Python suite does not execute the frontend at all, so a green suite
says nothing about whether the dashboard still loads. Three lanes cover
progressively more reality, and each proves something the others do not.

| Lane | What is real | When | What it proves |
|---|---|---|---|
| browser (`browser.yml`) | Chromium + uvicorn + real HTTP/WebSockets; Docker is a fail-closed fake | every PR | JS loads and evaluates; API and refusal behaviour reach the screen honestly |
| container acceptance (`containerAcceptance.yml`) | a real container, image keyed by build-input hash | nightly / manual | a real container answers the commands the browser lane's fake models |
| fresh image (`freshImageBuild.yml`) | full build from scratch | weekly / on `vaibify/containerImage/**` PRs | the image still builds; the container user is unprivileged |

Run the browser lane locally when you want the fast signal:

```bash
pip install -e '.[browser]' && python -m playwright install chromium
python -m pytest tests/browser -m browser
```

**What the browser lane does not cover.** It drives a fail-closed fake
Docker adapter, so it says nothing about container launch, file
ownership on write, the real transport, terminal content, figure
rendering, or the sync panel. Those belong to the container-acceptance
lane, which runs nightly — so drift between the fake and a real
container is caught up to a day late. The browser lane failing blocks
merge; the container-acceptance lane failing blocks the next release,
not retroactively.

### The manual check

Still the right tool when you are working on something the lane does not
assert — layout, wording, a specific interaction. About a minute, no
Docker needed:

```bash
python -m vaibify --port 8137     # scratch port, not your usual hub
```

Then at `http://127.0.0.1:8137/`:

1. **Read the console.** Zero errors is the bar. A single
   `ReferenceError` means a module failed to evaluate and every feature
   below it in load order is dead.
2. **Enumerate the globals.**
   `Object.keys(window).filter(k => /^Vaibify/.test(k)).length`. Then
   check any global your change touched resolves *as a bare identifier*,
   not via `window.`: modules declared with `const` create a global
   lexical binding, so `window.VaibifyApp` is `undefined` while
   `VaibifyApp` works. Probing the wrong one produces a false alarm.
3. **Confirm any new cross-module call resolves**, e.g.
   `typeof VaibifyApp.fsGetLeaseId` → `"function"`.
4. **Look at the page.** It should render, and any unavailable
   dependency (Docker down, no containers) must be reported honestly on
   screen rather than hidden.

Kill the scratch hub when done.

**Container-dependent paths need a container.** Anything touching the
lease, the WebSockets, or the file-status poll is not verified by the
above. Start Docker, open a project, and exercise the specific path.

**Docker-dependent tests** (`tests/testContainerBuildIntegration.py`)
are excluded from routine runs and are the only ones requiring a live
container. They are parametrized via `VAIBIFY_INTEGRATION_CONFIG` and
skip when it is unset.

## Pull Request Workflow

1. Fork the repository and create a feature branch.
2. Make your changes following the style guide.
3. Add or update tests as needed.
4. Run `pytest` locally and confirm all tests pass.
5. Open a pull request against the `main` branch with a clear description
   of the change.

## Project Layout

```
vaibify/
  cli/                Command-line interface (Click)
  completions/        Bash and zsh tab-completion scripts
  config/             Configuration dataclasses and parsers
  containerImage/     Docker build context (Dockerfiles, entrypoint,
                      overlays, in-container skills and CLI)
  docker/             Container lifecycle management
  docs/               Docs that ship into the image; the five curated
                      ones are symlinks onto the Sphinx sources
  gui/                FastAPI web application and pipeline runner
    routes/           Route modules (one per endpoint group)
    static/           JavaScript IIFE modules + CSS + HTML
    AGENTS.md         Backend subtree rules for coding agents
    static/AGENTS.md  Frontend subtree rules for coding agents
  install/            Setup wizard and shell installer
  reproducibility/    Zenodo, Overleaf, and LaTeX integration
  templates/          Project templates (sandbox, workflow, toolkit)
tests/                Pytest test suite, including
                      testArchitecturalInvariants.py
tools/                On-demand helper scripts (listModules.py,
                      checkAgentDocsPaths.py)
docs/                 Sphinx documentation (this site) including
                      architecture.md and vibeCoding.md
.claude/skills/       Conditional recipes for recurring extension tasks
AGENTS.md             Repo-wide rules, traps, and discovery commands
                      for AI coding agents (symlinked from CLAUDE.md)
```

For the full architectural narrative including module responsibilities,
dependency graph, state machine, and known technical debt, see
[architecture.md](architecture.md). For the methodology behind the
agent documentation system, see [vibeCoding.md](vibeCoding.md).

Run `python tools/listModules.py <subtree>` to print the current
module layout with `__all__` exports and docstring summaries, rather
than relying on a static module map that can drift.
