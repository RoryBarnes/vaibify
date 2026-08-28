# Reproducibility

Vaibify is built around the principle that every computational result
should be reproducible from a single command. This page describes the
tools and practices that make this possible.

## The Reproducibility Stack

A Vaibify repository captures four layers of provenance:

1. **Environment** -- The Docker image pins the operating system, compilers,
   system libraries, Python version, and all package versions.
2. **Code** -- `container.conf` lists every repository with its branch or
   tag, so the exact source code is recorded.
3. **Pipeline** -- `project.json` defines the commands to run and their
   order, removing ambiguity about how results were produced.
4. **Configuration** -- `vaibify.yml` records all settings, so a
   collaborator can rebuild the identical environment.

Together, these four files constitute a reproducibility manifest. Sharing
them (or the repository that contains them) is sufficient for anyone with
Docker to reproduce the results.

### L1 precondition: projects live inside a git repo

Vaibify enforces the lowest rung of the reproducibility ladder as a
precondition, not a best practice. Every project must live inside a
git repository — its *repository* — which vaibify auto-detects as
the git work tree enclosing the `project.json` file. A project saved
to a directory that is not a git work tree is rejected at both
creation and connect time with a clear error pointing the user to run
`git init`. The dashboard cannot display a meaningful reproducibility
level for code that cannot be committed, so asking for one would be
dishonest.

The repository path is auto-detected once per connect via
`git rev-parse --show-toplevel`, stamped on the in-memory workflow
dict, and threaded through every subsequent status, badge, and
manifest call. A single container may host multiple projects in
separate repository subdirectories (for example, a paper pipeline
and a follow-on cross-system analysis that share the same dependency
clones); the active project determines the scope of every per-file
badge.

Test markers (the JSON files that record the last pytest outcome +
output-file hashes for each step) live inside the repository under
`.vaibify/test_markers/` and are committed alongside `project.json`.
This makes a project's verification state — which tests have run,
what they produced, whether the outputs have drifted — reproducible
from a fresh clone without rerunning anything.

### L1 requires a declared input contract

Every step must state what raw data it consumes: files listed in its
`saInputDataFiles` (repo-relative, watched for modification) or the
explicit `bNoInputData` declaration. An *undeclared* step — neither
inputs listed nor the declaration — cannot reach Level 1, because
nothing distinguishes "verified there are no raw inputs" from
"nobody looked." An input file modified after outputs or plots were
generated does not satisfy Level 1 either: the results no longer
follow from the recorded inputs, so the Project is not
self-consistent until the affected steps re-run. Markers record a
per-input content hash (`dictInputHashes`) at every run, so both
verdicts survive a fresh clone.

### Canonical remote data

Data pulled from a remote source (an archive query, a survey
release) must be committed to the repository — the remote may vanish
or silently change, and a Project whose raw data cannot be
re-obtained is not reproducible. Each pulled file carries a
provenance record in the pulling step's `listRemoteData`
(`sPath`, `sSourceUrl`, `sDigestBecameCurrentUtc`, `sSha256`), refreshed
automatically after every successful pull; the URL is inert
metadata, never fetched by vaibify. Because a re-pull overwrites the
canonical copy, any run covering such a step whose files already
exist is refused pending explicit confirmation (browser modal;
`--confirm-remote-overwrite` for the agent CLI after relaying the
question to the researcher), and the fresh data is never
auto-committed — it flows through the normal review-and-commit
canonical flow.

## PROOF Level 3 — Reproducible

Vaibify targets **PROOF Level 3 ("Reproducible")** on the PROOF
ladder: third parties can confirm, at the bit level, that
the artefacts they hold are byte-for-byte identical to the artefacts
the original project produced. Level 3 is a claim about *file-byte
identity*, not numerical re-derivation. Re-running the project on a
different machine may produce slightly different bytes for the same
inputs (CPU/BLAS variance, see [Known
limitations](#known-limitations)); the hashes recorded in
`MANIFEST.sha256` describe the bytes the original run produced, and
those bytes can be redistributed and verified anywhere coreutils is
installed.

Level 3 also requires the **published-envelope pair** (2026-08-26,
superseding a same-day GitHub-only ruling): the envelope files must
match the copies on the GitHub mirror **and** be present in the
Zenodo archive. GitHub is not an archive — repositories are renamed,
made private, force-pushed, deleted — so an envelope that lives only
there gives the re-execute claim the lifetime of a mutable host. The
Zenodo check consults every **declared record**
(`dictRemotes.zenodo.listRecords` plus the primary deposit), because
Zenodo's own GitHub integration archives code releases as separate
records with their own DOIs; a file agrees with Zenodo when any
declared record serves its bytes. Records with per-file entries are
comparable; a record holding only a release tarball is not (a
documented limitation — publish the envelope through vaibify to make
it verifiable). Zenodo deposits are **flat** — the bucket API refuses
path-containing keys — so files upload under their basenames and the
verify matches a repo path to its basename only when that basename is
unique among the compared files; two paths sharing a basename are
honestly unverifiable, and the archive refuses such a selection
outright, because the second upload would silently overwrite the
first in the published record. Vaibify-generated test and standards
files carry a step-derived suffix (`test_qualitative_<step>.py`)
precisely so this never happens to generated projects. Because Zenodo deposits are immutable, restoring
agreement after an envelope change costs a new published deposit
version rather than a push: **Level 3 is a release-time property**,
red through most of a project's life and green at publication
moments. That is deliberate — "reproducible" describes a published
artifact, not a state the working tree drifts through.

## The Reproducibility Envelope

An honest L3 claim covers three tiers. Vaibify writes one file per
tier into the repository, and each tier is independently verifiable
with standard tools — vaibify is the orchestrator, not a dependency.

The envelope is regenerated automatically when the project
transitions to all-green (every step fully verified), and on demand
via the **Regenerate now** buttons in the Artifacts section of the
Main tab's Project block. This keeps the manifest in sync with the
latest verified state without requiring the user to remember to
trigger it.

### Tier 1 — Artifacts (`MANIFEST.sha256`)

A GNU-coreutils shasum-format file at the repository root listing
every declared project artefact (everything in each step's
`saPlotFiles`, `saOutputDataFiles`, and `saInputDataFiles`) by
repo-relative POSIX path with its SHA-256 hash:

```
1a2b3c...  scripts/runAnalysis.py
4d5e6f...  data/results.csv
7g8h9i...  plots/figure1.pdf
```

Paths containing newlines or backslashes are encoded with the GNU
escape convention: the line is prefixed with `\` and the path itself
has `\\` for backslash and `\n` for newline. This prevents an
attacker from forging a second manifest line by injecting a newline
into a filename.

Written by
[fnWriteManifest](../vaibify/reproducibility/manifestWriter.py) and
verified in-process by `flistVerifyManifest`. The file is also
verifiable on any system that ships `coreutils`:

```
sha256sum -c MANIFEST.sha256
```

An architectural-invariants test enforces that every path-list field
in `project.json` (`saPlotFiles`, `saOutputDataFiles`,
and any future addition) is reflected in `MANIFEST.sha256` — guarding
against silent under-tracking when the project schema is extended.

No vaibify install is required.

### Tier 2 — Python dependencies (`requirements.lock`)

A pinned, hash-augmented Python dependency lockfile at the repository
root. Generated by
[fnGenerateRequirementsLock](../vaibify/reproducibility/dependencyPinning.py)
which shells out to `uv pip compile --generate-hashes` against the
first dependency declaration it finds, in this order:

1. `pyproject.toml`
2. `requirements.in`
3. `requirements.txt`
4. `.vaibify/requirements.txt`

Each entry pins an exact version and at least one `--hash=sha256:...`
line.

The fourth candidate is the file vaibify's own container docs tell you
to maintain, and that the entrypoint installs on container startup. It
is last so a repo-root declaration — the one a Python packager reads —
always wins. Before it was probed at all, a project that followed the
documented workflow exactly could never turn the Level 3 dependency
row green, and the tier reported the miss only as a flag that stayed
false.

The compile runs on the **host**, in vaibify's own backend process,
not inside the container: for a container project the declaration is
staged out to a host temp directory, compiled there, and the resulting
lockfile written back through the container adapter. Installing a lock
generator *inside* the container therefore changes nothing.

Verifiers reproduce the environment with stock `pip`:

```
pip install --require-hashes -r requirements.lock
```

`uv` is needed only to *generate* the lockfile, never to consume it.
`flistVerifyRequirementsLock` performs a structural check (file
exists, parses, every entry carries a sha256 hash) without installing.

### Tier 3 — Container / system layer (`.vaibify/environment.json`)

A JSON document at `<projectRepo>/.vaibify/environment.json` capturing
the layers below the Python interpreter. Written by
[fnWriteEnvironmentJson](../vaibify/reproducibility/environmentSnapshot.py)
from three orthogonal capture helpers:

- `fdictCaptureContainerImageDigest(sContainerName)` — the immutable
  `<image>@sha256:...` digest of the running container image, via
  `docker inspect`.
- `fdictCaptureHostBinaryHashes(listBinaryPaths)` — for each binary
  the project declares as a host-side dependency (e.g., a compiled
  scientific executable referenced from `saHostBinaries` in
  `project.json`), the SHA-256 of the file plus the first line of
  its `--version` output.
- `fdictCaptureSystemTools()` — Python interpreter version, `gcc
  --version`, `platform.libc_ver()`, and the contents of
  `/etc/os-release` from inside the container.
- `fiCaptureSourceDateEpoch(filesRepo)` — the repo's HEAD commit
  epoch at capture time, recorded as `iSourceDateEpoch`. This is the
  value the pipeline exported as `SOURCE_DATE_EPOCH` (and as
  matplotlib's `svg.hashsalt`) when it produced the pinned artefacts.
  It is recorded rather than re-derived at reproduction time, because
  the commit that publishes the manifest moves HEAD — an epoch
  re-derived on the reproducing side would differ from the one that
  salted the pinned figures, so every timestamped artefact would
  diverge on exactly the workflows the envelope exists to certify.

This tier records what the container layer cannot pin by digest alone,
without claiming to bit-pin floating-point arithmetic across CPU
architectures.

## The verification ceremony: `vaibify reproduce`

For users who want one command instead of three,
[vaibify reproduce](../vaibify/cli/commandReproduce.py) walks five
tiers in sequence. Tiers 1–3 verify the three envelope files above;
Tier 4 verifies L3 artifact coherence (the same seven readiness
checks the dashboard's L3 gate applies: manifest completeness,
dependency lock, environment-snapshot digest form, Dockerfile
pinning, `reproduce.sh` present and in the manifest, determinism
declared, and binaries declared or waived); Tier 5 optionally
re-runs the project:

```
$ git clone <project-url> && cd <project>
$ vaibify reproduce
[1/5] Verifying file integrity (MANIFEST.sha256) ... 47/47 OK
[2/5] Reproducing Python env (requirements.lock) ... hashes verified OK
[3/5] Pulling pinned container image ... python@sha256:1a2b... OK
[4/5] Verifying L3 artifact coherence ... 7/7 OK
       - Manifest complete: OK
       - Dependency lock: OK
       - Environment snapshot digest-form: OK
       - Dockerfile pinned: OK
       - reproduce.sh present + in manifest: OK
       - Determinism declared: OK
       - Binaries declared or waived: OK
[5/5] Re-running workflow ... skipped (use --rerun)

L3 reproduction ready (no attestation on file — run --rerun to attest).
```

With `--rerun`, a fully passing run instead ends with
`L3 reproduction confirmed and attested.`; any failing tier ends with
`L3 reproduction failed; see tier output above.`

Flags:

- `--repo <path>` — path to the repository (defaults to the current
  directory).
- `--rerun` / `--no-rerun` — also run Tier 5, the full project
  re-execution. Off by default; opt-in because projects can be
  expensive and the re-run tier is best-effort (see [Known
  limitations](#known-limitations)). When enabled, vaibify dispatches
  to the same pipeline runner that `vaibify run` uses, against a
  running container resolved from the repository — and then re-hashes
  every `MANIFEST.sha256` entry **inside that container**. Note the
  asymmetry: the earlier tiers read the host repo `--repo` names,
  while the re-run tier reads the container's project repo, because
  `/workspace` is a Docker-managed named volume and the two are
  different filesystems. The expected hashes are frozen before the run
  starts, so a step that re-pins the manifest over its own changed
  output is reported as a divergence rather than blessed. The rerun
  exports the `SOURCE_DATE_EPOCH` recorded in
  `.vaibify/environment.json` (`iSourceDateEpoch`) rather than
  re-deriving it from HEAD, so timestamp-salted figures are salted
  the way the pinned artefacts were.

  A workflow the unattended runner cannot honestly execute is
  **refused before any step runs**: interactive steps, steps
  disabled in the dashboard, or a workflow with no steps at all. A
  skipped step leaves its pinned outputs untouched, so every hash
  would trivially match and the attestation would certify a rerun
  that ran nothing. The refusal is reported (`rerun refused before
  any step executed`) with one divergence line naming each
  unexecutable step, and is recorded as a *failed* attestation.

  Tier 5 always writes an attestation, pass or fail:
  `.vaibify/l3_attestation.json` plus a timestamped copy archived
  under `.vaibify/l3_attestations/`, recording the manifest digest
  the comparison was made against, the image digest, the hash-match
  counts, and every diverged path. Without `--rerun` no attestation
  is written.
- `--workflow <name>` — which workflow to re-run, when the container
  hosts more than one. Without it an ambiguous container is refused:
  attesting one workflow for a run of another produces a record that
  reads as complete and describes something that did not happen.
- `--skip-tier 1|2|3|4` — skip a tier; may be repeated. Useful when a
  verifier only wants to confirm artefact identity without installing
  Python packages. Tier 5 has no skip flag; it is opt-in via
  `--rerun`.

Exit codes:

- `0` — every selected tier passed.
- `1` — at least one tier failed; per-tier diagnostics are printed
  above the final summary.
- `2` — usage error (a required input file is missing, or a malformed
  `environment.json`).

## Trust-anchor architecture

`vaibify reproduce` is a convenience orchestrator, **not** the trust
anchor. The trust anchor for Tier 1 is `sha256sum -c MANIFEST.sha256`,
a `coreutils` binary every verifier already has. If `vaibify
reproduce` is ever wrong, a third party verifying by hand catches the
discrepancy. This is the load-bearing reason the PROOF levels are
defined independently of vaibify: it makes vaibify *auditable* rather
than authoritative. The same independence applies to Tier 2 (`pip
install --require-hashes`) and Tier 3 (`docker pull
<image>@sha256:...`); each step can be performed manually by anyone
who reads the three files.

## Remote-mirror verification

When a project is pushed to a public mirror — GitHub, Overleaf, or
Zenodo — vaibify verifies that the *remote* copy of every manifested
file still matches the SHA-256 recorded at archive time. Each remote
exposes a uniform `fdictFetchRemoteHashes(...)` API
([githubMirror.py](../vaibify/reproducibility/githubMirror.py),
[overleafMirror.py](../vaibify/reproducibility/overleafMirror.py),
[zenodoClient.py](../vaibify/reproducibility/zenodoClient.py)) that
returns one SHA-256 per declared file. Two layers run on top:

- **Cheap poll** — continuous, low-cost change detection (per-file
  blob SHA-1 or modified-time metadata). Flags "something might have
  drifted, re-verify."
- **Authoritative verify** — downloads bytes, recomputes SHA-256,
  compares against `MANIFEST.sha256`. Triggered by the per-remote
  Re-verify button in the dashboard or by the scheduled background
  loop in
  [scheduledReverify.py](../vaibify/reproducibility/scheduledReverify.py).
  The cadence is currently a single global default (6 hours) set when
  the FastAPI app is constructed and applied uniformly to every loaded
  project; per-project overrides are deferred to a future commit.

Results are cached in `<projectRepo>/.vaibify/syncStatus.json` keyed
by service so the dashboard always shows ground truth without a
network round trip on every poll. See [the dashboard
guide](dashboard.md#the-verify-reproducibility-panel) for the
resulting UI.

## Known limitations

**Symbolic links are resolved against the repo root.**
[fnWriteManifest](../vaibify/reproducibility/manifestWriter.py)
resolves a symlink anywhere on a declared path and checks the target
against the repository root. A symlink resolving *inside* the root
hashes the target's content, recorded under the declared (symlink)
path. A symlink whose target escapes the root is never opened or
hashed: that single entry is skipped as a logged per-file gap
(surfaced by the manifest-completeness check) rather than aborting
the whole manifest. Only a non-symlink declared path that escapes
the root (`..` traversal) raises `ValueError`.

**Tier 1 is bit-perfect; re-running the project is best-effort.**
`MANIFEST.sha256` records the exact bytes a particular run produced,
and `sha256sum -c` confirms those bytes were preserved. Re-executing
the project on a different CPU, BLAS implementation, or compiler
toolchain may produce numerically near-identical but
**byte-different** outputs because of floating-point order-of-operation
variance. This is a science-of-reproducibility limitation, not a
vaibify defect, and we document it rather than try to engineer around
it. Tier 5 (project re-run via `vaibify reproduce --rerun`) is
therefore advisory.

**The unfixable failure mode.** If `vaibify reproduce` itself is
replaced by a tampered binary on the verifier's machine, vaibify
cannot detect that — the same problem every verification tool has,
including a tampered `sha256sum`. The mitigation is the architectural
one above: vaibify's source is public, builds reproducibly, and any
verifier can fall back to plain coreutils.

## Publishing a Workflow

```{warning}
Not implemented — this section describes an intended feature. The
`publish` command group is not registered on the CLI, so
`vaibify publish workflow` is an unknown command, and
`vaibify/reproducibility/githubWorkflow.py` (the generator described
below) has no caller in the product. Nothing here runs today.
```

The intent is to read `project.json` and `vaibify.yml`, render the
Jinja2 template at `vaibify/templates/workflow.yml.j2`, and write the
result to `.github/workflows/vaibify.yml`.

The generated workflow would:

1. Checks out the repository.
2. Installs Vaibify.
3. Builds the Docker image.
4. Runs each pipeline step inside the container.
5. Uploads artifacts (figures, data products) to GitHub Actions.

## Archiving to Zenodo

Zenodo archiving is real and reachable — through the PROOF Level 2
workflow in the dashboard, not through the CLI.

```{warning}
`vaibify publish archive` is not implemented and not registered on the
CLI. Use the dashboard's archive action instead.
```

The intended CLI form would package the Docker image, configuration
files, and pipeline outputs into a tarball, upload it to Zenodo (or the
Zenodo sandbox, depending on the `reproducibility.zenodoService`
setting), and return a DOI.

Authentication with Zenodo is handled through the host's credential
manager. Vaibify never stores tokens in configuration files or
environment variables.

### The publish record lives in the sidecar, not in project.json

A Zenodo deposit is immutable, and `project.json` is part of what an
archive uploads — so if the archive then recorded its own success
*into* `project.json` (deposit id, DOIs, per-file digests), the local
file would necessarily diverge from the copy it had just published,
and re-archiving would mint a new deposit id that changed the file
again: a treadmill by construction. That is exactly what happened
until 2026-08-27.

The fix is structural. `project.json` holds only the definition the
researcher declares; everything a push, archive, or verify *produces*
— the per-file `dictSyncStatus`, the Zenodo publish record, and the
produced `dictRemotes` fields such as `overleaf.sLastPushCommit` and
`zenodo.sRecordId` — is split out on save into a per-workflow
`dictProjectBookkeeping` section of
`<projectRepo>/.vaibify/syncStatus.json`, which is deliberately
outside the publication comparison scope. The in-memory workflow dict
stays merged (the load path grafts the section back in), so the
dashboard and routes see one shape. The module that owns the split is
[syncBookkeeping.py](../vaibify/reproducibility/syncBookkeeping.py).

Legacy projects migrate automatically: their fielded keys are read
from `project.json` until the first save moves them into the sidecar,
after which the archived and local copies of `project.json` can
byte-match indefinitely. Sidecar values win over fielded ones on
load, so restoring an old definition from git history does not roll
back the record of what was actually published.

## Version Pinning

For maximum reproducibility, pin repository branches to specific tags or
commit hashes in `container.conf`:

```
mycode|git@github.com:user/mycode.git|v1.2.3|pip_editable
```

The Docker image caches the cloned repositories, so rebuilding with
`vaibify build` after changing a branch or tag will pull the updated
code.

## Network Isolation

Enable `networkIsolation: true` in `vaibify.yml` to disable outbound
network access from the container. This ensures that the pipeline cannot
download external resources at runtime, guaranteeing that all dependencies
are captured in the image.

## Sharing Results

The recommended workflow for sharing reproducible results:

1. Commit `vaibify.yml`, `container.conf`, and
   `.vaibify/projects/project.json` to your repository.
2. Tag a release when results are final.
3. Create a Zenodo DOI through the dashboard's archive action.
4. Reference the DOI in your manuscript.

(CI automation would be step 2 once `vaibify publish workflow` exists;
until then, add the GitHub Actions workflow by hand.)

A collaborator can then reproduce your results by cloning the repository
and running:

```bash
vaibify build
vaibify start
```
