# Contributor's Guide

Interested in contributing to Vaibify? This page outlines the steps to make
a meaningful contribution. Before you begin, contact
[Rory Barnes](mailto:rory@astro.washington.edu) to confirm that your
proposed changes are not duplicating work and will be of general interest.

## Style Guide

Vaibify follows the style conventions described in the project's global
development standards. The key rules are summarized here.

### Naming Conventions

- **Variables** use camelCase with Hungarian prefixes: `bEnabled`,
  `iCount`, `sName`, `fValue`, `daWeights`, `dictConfig`, `listItems`.
- **Functions** begin with `f` followed by lowercase letter(s) indicating
  the return type: `fbIsValid()`, `fsGetName()`, `fnRunStep()` (no return).
- **Files** use camelCase without Hungarian prefixes: `pipelineRunner.py`,
  `containerManager.py`.

### Functions

- Functions should be fewer than 20 lines.
- Each function should have a single, clear purpose.
- If the same lines appear in multiple places, extract them into a new
  function.

### Code Clarity

- Variable and function names should be self-explanatory.
- Use inline comments sparingly -- clear naming replaces most comments.
- Do not abbreviate any word shorter than 8 characters.
- Function names must contain an action verb (except `main`).

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

## Portability

All code must work on both macOS and Linux, and with Python versions
3.9 through 3.14. The project uses GitHub Actions to test all permutations
of OS and Python version.

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
  cli/              Command-line interface (Click)
  config/           Configuration dataclasses and parsers
  docker/           Container lifecycle management
  gui/              FastAPI web application and pipeline runner
  install/          Setup wizard and shell installer
  reproducibility/  Zenodo, Overleaf, and LaTeX integration
templates/          Project templates (general, planetary, reproducible-paper)
tests/              Pytest test suite
docs/               Sphinx documentation (this site)
```
