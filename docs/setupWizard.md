# Setup Wizard

The setup wizard guides you through creating a `vaibcask.yml`
configuration file interactively. It runs automatically when you execute
`vaibcask init` without specifying a template.

## Starting the Wizard

```bash
vaibcask init
```

If a `vaibcask.yml` already exists, the wizard will warn you and ask
for confirmation before overwriting. Use `--force` to skip the prompt:

```bash
vaibcask init --force
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
and creates a `script.json` with example pipeline scenes.

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
| `vaibcask.yml`   | Project configuration                    |
| `container.conf`      | Repository list                          |
| `script.json`         | Pipeline scene definitions               |

## Editing After Setup

All generated files are plain text. Edit them directly to add repositories,
change settings, or define new pipeline scenes. Run `vaibcask build`
after making changes to rebuild the image.

## Importing and Exporting Configuration

Export the current configuration for sharing:

```bash
vaibcask config export > my-config.yml
```

Import a configuration:

```bash
vaibcask config import shared-config.yml
```
