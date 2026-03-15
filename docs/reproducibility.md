# Reproducibility

VaibCask is built around the principle that every computational result
should be reproducible from a single command. This page describes the
tools and practices that make this possible.

## The Reproducibility Stack

A VaibCask project captures four layers of provenance:

1. **Environment** -- The Docker image pins the operating system, compilers,
   system libraries, Python version, and all package versions.
2. **Code** -- `container.conf` lists every repository with its branch or
   tag, so the exact source code is recorded.
3. **Pipeline** -- `recipe.json` defines the commands to run and their
   order, removing ambiguity about how results were produced.
4. **Configuration** -- `vaibcask.yml` records all settings, so a
   collaborator can rebuild the identical environment.

Together, these four files constitute a reproducibility manifest. Sharing
them (or the repository that contains them) is sufficient for anyone with
Docker to reproduce the results.

## Publishing a Workflow

Generate a GitHub Actions workflow that automates the entire pipeline:

```bash
vaibcask publish workflow
```

This reads `recipe.json` and `vaibcask.yml`, renders the Jinja2
template at `templates/workflow.yml.j2`, and writes the result to
`.github/workflows/vaibcask.yml`.

The generated workflow:

1. Checks out the repository.
2. Installs VaibCask.
3. Builds the Docker image.
4. Runs each pipeline step inside the container.
5. Uploads artifacts (figures, data products) to GitHub Actions.

## Archiving to Zenodo

Create a Zenodo deposit for long-term archival:

```bash
vaibcask publish archive
```

This packages the Docker image, configuration files, and pipeline outputs
into a tarball, uploads it to Zenodo (or the Zenodo sandbox, depending on
the `reproducibility.zenodoService` setting), and returns a DOI.

Authentication with Zenodo is handled through the host's credential
manager. VaibCask never stores tokens in configuration files or
environment variables.

## Version Pinning

For maximum reproducibility, pin repository branches to specific tags or
commit hashes in `container.conf`:

```
mycode|git@github.com:user/mycode.git|v1.2.3|pip_editable
```

The Docker image caches the cloned repositories, so rebuilding with
`vaibcask build` after changing a branch or tag will pull the updated
code.

## Network Isolation

Enable `networkIsolation: true` in `vaibcask.yml` to disable outbound
network access from the container. This ensures that the pipeline cannot
download external resources at runtime, guaranteeing that all dependencies
are captured in the image.

## Sharing Results

The recommended workflow for sharing reproducible results:

1. Commit `vaibcask.yml`, `container.conf`, and `recipe.json` to
   your repository.
2. Run `vaibcask publish workflow` to add CI automation.
3. Tag a release when results are final.
4. Run `vaibcask publish archive` to create a Zenodo DOI.
5. Reference the DOI in your manuscript.

A collaborator can then reproduce your results by cloning the repository
and running:

```bash
vaibcask build
vaibcask start
```
