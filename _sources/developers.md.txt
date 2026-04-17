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
agent, read [`AGENTS.md`](../AGENTS.md) at the repo root for the rules,
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
  config/             Configuration dataclasses and parsers
  docker/             Container lifecycle management
  gui/                FastAPI web application and pipeline runner
    routes/           Route modules (one per endpoint group)
    static/           JavaScript IIFE modules + CSS + HTML
    AGENTS.md         Backend subtree rules for coding agents
    static/AGENTS.md  Frontend subtree rules for coding agents
  install/            Setup wizard and shell installer
  reproducibility/    Zenodo, Overleaf, and LaTeX integration
templates/            Project templates (sandbox, workflow, toolkit)
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
