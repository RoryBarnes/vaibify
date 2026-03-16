# Setup Wizard

The setup wizard guides you through creating a `vaibify.yml`
configuration file interactively. It runs automatically when you execute
`vaibify init` without specifying a template.

## Starting the Wizard

```bash
vaibify init
```

If a `vaibify.yml` already exists, the wizard will warn you and ask
for confirmation before overwriting. Use `--force` to skip the prompt:

```bash
vaibify init --force
```

## Wizard Steps

### 1. Project Name

The project name becomes the Docker container name and the image tag. It
must be a valid Docker identifier (lowercase letters, digits, and hyphens).

```
Project name: my-analysis
```

### 2. Template Selection

Choose a starting template:

```
Available templates:
  [1] general             - Empty starting point
  [2] planetary           - VPLanet ecosystem
  [3] reproducible-paper  - LaTeX manuscript with figures
Select template [1]:
```

The template populates `container.conf` with an initial set of repositories
and creates a `workflow.json` with example pipeline steps.

### 3. Python Version

Select the Python version for the container (default: 3.12):

```
Python version [3.12]:
```

### 4. Container User

The non-root user created inside the container (default: `researcher`):

```
Container user [researcher]:
```

### 5. Feature Flags

Enable optional features by answering yes or no:

```
Enable Jupyter notebooks? [y/N]:
Enable R language support? [y/N]:
Enable Julia support? [y/N]:
Enable LaTeX? [Y/n]:
Enable Claude Code? [y/N]:
```

### 6. Additional Packages

Specify any extra system or Python packages:

```
Additional system packages (comma-separated) []:
Additional Python packages (comma-separated) []:
```

## Output Files

After completing the wizard, the following files are created in the current
directory:

| File                  | Purpose                                  |
|-----------------------|------------------------------------------|
| `vaibify.yml`   | Project configuration                    |
| `container.conf`      | Repository list                          |
| `workflow.json`         | Pipeline step definitions                |

## Editing After Setup

All generated files are plain text. Edit them directly to add repositories,
change settings, or define new pipeline steps. Run `vaibify build`
after making changes to rebuild the image.

## Importing and Exporting Configuration

Export the current configuration for sharing:

```bash
vaibify config export > my-config.yml
```

Import a configuration:

```bash
vaibify config import shared-config.yml
```
