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

The PROOF ladder has six rungs — L1 Self-Consistent, L2 Published,
L3 Reproducible, L4 Traceable, L5 Regenerated, L6 Attested — and
[vision.md](vision.md) is the normative statement of all six. This
page is the normative statement of the ceiling vaibify implements.

**Level 3 requires a containerized project.** The level is defined by
a pinned image digest and a rerun inside that image, and a host-mode
project — one whose pipeline runs directly on the researcher's own
machine — has neither. Such a project is refused L3 with the
`host-mode` criterion and reaches L2. This is a property of what the
level claims, not a gap in host mode.

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

- `fsReadImageArchitecture(sImageReference)` — the platform the image
  was built for, recorded as `sArchitecture`. Read from the image
  rather than derived from the digest, because a **manifest list**
  digest spans several platforms and pins none of them.

This tier records what the container layer cannot pin by digest alone,
without claiming to bit-pin floating-point arithmetic across CPU
architectures.

#### The image digest names bytes somebody else is storing

A digest is a pin, not a copy. `reproduce.sh` begins with `docker
pull`, and that is the moment the pin stops being enough: when the
registry no longer serves the digest, the compiler, the exact numeric
library, the interpreter and every installed package are gone with it.

`dictContainer.dictImageArchive` records a deposit of the image itself
— a `docker save`, compressed, published to Zenodo under its own
version DOI. `reproduce.sh` falls back to it when the pull fails,
verifying the download against `sTarballSha256` before loading it. The
archive is the second link of a three-link chain — the registry pull,
then the archived copy, then a copy already present on the reproducing
host — and only the first two are open to anyone but the author, which
is why the last prints a warning naming that. A registry copy is
optional and gates no level: a container registry is a commercial
service with no preservation commitment, so vaibify integrates with it
for convenience, as it does with Overleaf and arXiv, and the archive is
the only image criterion on the ladder. The
record carries the digest and the platform it covers, so an envelope
regenerated for a different image drops it rather than claiming an
archive nobody made, and two hashes: `sTarballSha256` binds the bytes
uploaded, and `sImageStreamSha256` binds the image content
independently of which compressor produced the tarball.

Depositing is the researcher's choice, asked once at Level 2 and
answerable with `archived`, `referenced` or `declined`. Declining
satisfies Level 2 and blocks only the Level 3 criterion, which never
reads the answer — so a project that declined and later deposits
reaches Level 3 with nothing to undo.

### The Dockerfile is provenance; the digest is reproduction

PROOF Level 3 also asks for a pinned `Dockerfile` at the repository
root. A vaibify project has none by default, because the image is
built from vaibify's own packaged Dockerfiles as a *chain* — the base,
then one `docker build` per enabled feature overlay, each handed the
previous image as `BASE_IMAGE`. The **Copy image Dockerfile into repo**
action on the Dockerfile row composes that chain into a single
multi-stage file you can commit.

Read what it is for, because the distinction decides how much its
contents matter:

- **It records how the image was made.** Every stage is one step of
  the real build chain, in the order it ran.
- **It is not the reproduction recipe.** `reproduce.sh` runs
  `docker pull` against `dictContainer.sImageDigest` and never builds.
  A verifier gets byte-identical layers — the same compiler, the same
  system libraries, the same everything — without consulting the
  Dockerfile at all.

So rebuilding from that file is a *fallback*, used only if the image
itself becomes unavailable, and it does not reconstruct the original
environment. That is a property of `apt-get install` reaching a live
archive at build time, not of anything vaibify chose.

### What is pinned in the image, and what floats

The **entire toolchain closure** is version-pinned — all 45 packages
that `apt-get install --no-install-recommends gcc g++ make` resolves on
top of the pinned base image. Nothing in the compile-and-link path is
left floating:

| Group | Examples | Why it is pinned |
|---|---|---|
| Compiler | `gcc`, `gcc-13`, `cpp-13`, `g++-13`, `libgcc-13-dev`, `libstdc++-13-dev` | A binary compiled by a different compiler is a different binary, and can differ in the last significant figures. |
| Assembler / linker | `binutils`, `libbinutils`, `libctf0`, `libsframe1` | `as` and `ld` decide the emitted object and its layout. |
| C library + headers | `libc6`, `libc6-dev`, `libc-bin`, `linux-libc-dev`, `libcrypt-dev` | Headers and the libc a binary is linked against. |
| gcc's math libraries | `libisl23`, `libmpc3`, `libmpfr6` | These perform gcc's **constant folding**, so they can change emitted numeric values. |
| Runtime libraries | `libgomp1`, `libquadmath0`, `libatomic1`, sanitizers | Linked into binaries built with OpenMP, `__float128`, atomics, or `-fsanitize`. |
| Build driver | `make` | |

Two package families need both names pinned. `gcc` is a *metapackage*
at `4:13.2.0-7ubuntu1` that depends on `gcc-13 (>= ...)`, so pinning
`gcc` alone leaves the actual compiler free to float; the version
reported by `gcc --version`, and recorded in `dictSystemTools.sGcc`, is
`gcc-13`'s. The same applies to `cpp`/`cpp-13` and `g++`/`g++-13`.

**Partial pinning is worse than either extreme.** A pinned `libc6-dev`
whose `libc6` has moved on is not a looser constraint — it is an
*unsatisfiable* one, and apt fails with a dependency conflict rather
than a missing-version message. Pin the closure or pin nothing.

What is deliberately *not* pinned here: anything already present in the
base image and not upgraded by this block. Those are fixed by the base
image digest, which is the stronger guarantee — they cannot float while
the digest holds. Also unpinned are the packages that cannot reach a
numerical result (editors, viewers, `graphviz`, `poppler-utils`, LaTeX,
X11); their apt blocks carry an explicit `# allow-unpinned` marker.

### Regenerating the pin list after a base-image bump

```
docker run --rm <BASE_IMAGE> sh -c 'apt-get update -qq >/dev/null \
  && apt-get install -s -y --no-install-recommends gcc g++ make \
  | grep "^Inst "'
```

Take the version in **parentheses**, not the one in brackets. An
upgrade line reads `Inst libc6 [old] (new ...)`, so reading the
bracketed field pins the version being *replaced*. `libc6` and
`libc-bin` are upgrades from the base image and are exactly the two
this gets silently wrong.

The `-x86-64-linux-gnu` package names are safe to pin because the
pinned base digest resolves to a single-architecture `linux/amd64`
image, not a multi-arch manifest list. Repointing `BASE_IMAGE` at
another architecture requires regenerating the whole list; the build
diagnostic says so, because otherwise that failure looks identical to a
withdrawn version.

### When a pinned toolchain version disappears

Ubuntu removes superseded package versions from the archive pool
within weeks or months of a new one landing. With the full closure
pinned this happens more often than it would with a handful of pins —
that is the accepted cost of the guarantee, not a regression. When it
happens, `docker build` **stops with a non-zero exit**:

```
vaibify: the pinned compiler toolchain is no longer available.
...
This build stopped on purpose.
```

**This is the intended behaviour, not a bug to route around.** The
alternative — leaving the toolchain unpinned — is a rebuild that
quietly swaps the compiler underneath a researcher who believes they
reproduced something. A loud failure hands you the decision; a silent
substitution takes it away from you.

The diagnostic prints the three options, and prints `apt-cache policy`
for the affected packages so the currently available versions are on
screen when you choose:

1. **Reproduce the original.** Do not rebuild. Pull the published
   image by digest — `reproduce.sh` already does exactly this. The
   original toolchain is inside that image, which is why the digest,
   not the Dockerfile, is what Level 3 rests on.
2. **Accept a newer toolchain.** Update the pins in the toolchain
   block, then **re-run and re-verify**. Your outputs may legitimately
   change; the manifest hashes will say so, which is the honest signal
   that a result moved because its compiler did.
3. **Fetch the old packages.** `snapshot.ubuntu.com` serves the
   archive as it stood on a given date. Point apt at the snapshot
   covering the image's build date (`iSourceDateEpoch` in
   `environment.json` dates it) and keep the pins as they are.

Option 1 is right for verifying published work. Option 2 is right when
you are moving the project forward and are prepared to re-establish
its results. Option 3 is right when you must rebuild *and* must keep
the original toolchain — the most faithful of the three, and the most
work.

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

  A step **a human runs** — an interactive step, such as the AI
  Declaration — cannot execute unattended, and does not refuse the
  rerun. Its outputs are treated as *given*: data a person produced,
  which the steps below it consume as input. The shadow's repository
  copy carries them in unchanged, so the executable steps run against
  exactly the bytes the original run used, and those paths are dropped
  from the hash comparison and listed under `listCarriedPaths`. The
  matched/total counts therefore describe only what execution
  produced, and the carried files are named beside them — an
  attestation makes no claim about a file nobody re-computed.

  A workflow the unattended runner cannot honestly execute is still
  **refused before any step runs**: steps disabled in the dashboard,
  or a workflow with no steps at all. A disabled step leaves its
  pinned outputs untouched, so every hash would trivially match and
  the attestation would certify a rerun that ran nothing. Being
  disabled is a switch rather than a declared property of the
  workflow, which is why its outputs are not carried. A workflow whose
  *every* pinned entry is a given step's output is refused too: there
  is nothing left for a rerun to reproduce.

  Tier 5 writes an attestation whenever the comparison reached a
  verdict, pass or fail: `.vaibify/l3_attestation.json` plus a
  timestamped copy archived under `.vaibify/l3_attestations/`,
  recording the manifest digest the comparison was made against, the
  image digest, the hash-match counts, the carried paths, and every
  diverged path. A refusal reaches **no verdict** and writes nothing —
  it is reported (`rerun refused before any step executed`, then `no
  attestation written: nothing was verified`) and leaves any earlier
  attestation intact, because it established nothing about whether the
  workflow reproduces. Without `--rerun` no attestation is written.
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

## Reproducing somebody else's project: `vaibify reproduce --from`

`vaibify reproduce` grades a repository you already have checked out.
**Reproduce a published project** starts one step earlier, from the
thing a stranger has -- a clone URL, or a clone already on your
machine -- and stages an exact snapshot of it before anything is
graded:

```
$ vaibify reproduce --from https://host.example/group/project.git
Staged git-url as an exact snapshot.
  repository:      project
  commit:          3f1c...e9
  remote:          https://host.example/group/project.git
  workflow:        Demo (.vaibify/projects/project.json, 4 steps)
  manifest:        47 entries, every one matching, digest 9a2b...
A rerun would use:
  pinned image:    registry.example/project@sha256:1a2b...
  platform:        linux/amd64
  image archive:   deposited, version DOI 10.5281/zenodo.1234567
Snapshot validated as reproduction-ready (the six staging rules,
not the author's Level 3 gate) and discarded; nothing was
pulled, installed or run.
```

Alone, `--from` stages, validates, describes and discards. Two more
modes go further:

```
$ vaibify reproduce --from https://host.example/group/project.git --rerun
...
Obtaining the pinned image:
  registry pull: failed (manifest unknown)
  downloading the deposit: 0/821000000 bytes
  downloading the deposit: 821000000/821000000 bytes
  archived deposit: served
  obtained from:   archive (sha256:29e0...)
  platform:        required linux/amd64, obtained linux/amd64, daemon amd64
Re-running the snapshot in a shadow container ...
... workflow re-ran successfully in a shadow container
... hashes match 47/47 OK

Verdict: reproduced
Reproduction report: ~/.vaibify/reproductions/reports/680f090caf425eb6.json
This report is yours, not the author's attestation; nothing was written into the project.
```

- `--prepare` obtains the pinned image through the published chain
  and stops, so a long download can be done ahead of a run.
- `--rerun` obtains the image, re-runs the snapshot in a fresh shadow
  container built from it, compares the produced bytes with the
  project's manifest **inside that container**, and writes a
  reproduction report. The exit code is `0` only when the verdict is
  *reproduced*.
- `--allow-emulation` accepts a pinned build of another architecture
  than this daemon's; without it that case is refused by name.

### The image is obtained through the chain `reproduce.sh` uses

The order is a ruling and is not reordered: **registry pull, then the
archived deposit, then a copy already on this daemon.** A registry is
a convenience; the Zenodo deposit is the archive; a local copy is
survivable for the author alone, and the run says so when it takes
that path. Every link reports as it happens, and a refusal names every
link tried and why each failed. The deposit is trusted through the
envelope, never through the record page: its `sTarballSha256` is
checked before anything is handed to the daemon, a download that
differs is deleted and reported as "did not match its hash", and the
tarball is removed on every exit path. There is no Dockerfile rebuild:
the digest is the reproduction and the Dockerfile is provenance.

The Python chain and the shell chain in `reproduce.sh` are pinned to
agree link for link (`tests/testImageAcquisition.py` drives both
against the same tarball bytes), and both now request the pinned
platform: `reproduce.sh` passes `--platform linux/<arch>` to `docker
pull` and `docker run`, read from the envelope's `sArchitecture`, and
announces on stderr when an older envelope recorded none.

### The platform is three facts, not one

| Fact | Where it comes from |
|---|---|
| **required** platform | the envelope's `sArchitecture`, as `linux/<arch>` |
| **obtained** platform | what the image the chain produced reports of itself |
| **daemon** architecture | asked of the daemon itself, never of the host Python |

An obtained platform that differs from the required one **always**
refuses: the chain produced the wrong bytes. A daemon of another
architecture is *emulation*: refused unless `--allow-emulation`, and
when allowed the verdict reads "reproduced under emulation
(`linux/amd64` image on a `arm64` host)". Inspecting the obtained
image cannot reveal emulation, because an image reports its own
architecture on any host, which is why the third fact has its own
name.

### What the shadow run is, and is not

The rerun runs in the same shadow lane the dashboard's Verify uses,
seeded from the staged snapshot instead of a running container: a
container built from the obtained image with no network, no volumes,
no credentials, no ports and no GPU, whatever the snapshot's
`vaibify.yml` declares -- that file is data to the run, never an input
to its runtime specification. The comparison is rooted on the shadow's
filesystem, an interactive step's outputs are carried in and reported
beside the counts, the first failing step's output is kept, and the
shadow is destroyed with proof. When the image came from the archive,
the loaded-from-archive marker is written into the shadow before any
step runs, so the report's image re-check is *vacuous* by construction
rather than a download compared with itself.

### What a report is, and is not

A **reproduction report** is the reproducer's own artefact. It lives
under `~/.vaibify/reproductions/reports/<id>.json`, apart from the
staging directory that is deleted after every run, with its own
retention. It carries the redacted source facts, the manifest digest,
the three platform facts, where the image came from and which
reference ran, the verdict and the per-file comparison, the carried
paths beside the counts, the failure record, and the image re-check.
It is never an attestation, never written into any repository, and
never read by the Level 3 gate; vaibify offers no publishing,
depositing, pushing or attesting action on it. Its verdicts are
*reproduced*, *reproduced under emulation*, *diverged*, and *no
verdict* (the rerun never started, and the reason is named).

### What a source can be

| Source | Accepted as | Notes |
|---|---|---|
| An `https://` or `ssh://` clone URL, or `user@host:path` | `git-url` | Matched by shape, never by which forge hosts it. A URL carrying a username or password is refused: credentials in a URL end up in shell history and in reports. |
| The path of a clone under your home directory | `local-clone` | Only when `git status` reports nothing at all -- tracked, untracked *and* ignored. A dirty clone is not a published project. |

`file://`, `git://`, `ext::` and plain `http://` are refused, each
being a way to make git read or run something on this host. A Zenodo
software record as a source, and comparing a reproduction against a
published *data* deposit, are recorded design decisions not yet built.

### A reproduction is of a commit

A URL is cloned in full -- no `--depth`, because the history and the
source-date epoch matter -- and the commit it resolved to is recorded.
A local clone is materialized by **cloning the local repository**,
never by copying its working tree, and checked out at the commit you
had checked out, so the staged bytes are that commit's bytes and
nothing else. Everything downstream consumes the staged snapshot: a
branch that advances after staging, or a working tree edited since,
changes nothing about what would run.

Every `git` the stage runs carries vaibify's shared hardening flags
(`protocol.file.allow=never`, `core.symlinks=false`, no submodule
recursion), the credential-helper reset, `GIT_TERMINAL_PROMPT=0`, and
ssh in batch mode (`-o BatchMode=yes` placed FIRST in your
`GIT_SSH_COMMAND`, or in `ssh`, because OpenSSH keeps the first value
it sees for an option), so no ambient credential can answer
for a stranger's remote and neither git nor ssh can hang an unattended
run on a question -- an unknown host key or a locked key fails the
clone, and the refusal says so. One deliberate exception: the clone
of a *local* repository is itself the file transport git's hardening
refuses, so that one clone -- and only that one, only after the path
was admitted under your home -- reopens the file transport. It asks
for no submodule, so the hostile `.gitmodules` the setting defends
against is never read.

A clone is refused while it grows past a size ceiling, not after it
has filled the disk, and an abandoned staging directory is swept after
a day -- never one a live job still holds.

### Validation is strict, not advisory -- and it is not the Level 3 gate

`vaibify reproduce` warns about a manifest that omits a declared file;
`--from` refuses. It grades somebody else's project, and the six rules
it applies are the ones a rerun *depends on*: a loadable workflow, a
pinned image on a named platform, and a manifest that parses, matches
the staged bytes and covers the selected workflow's declarations. They
are deliberately **not** the author's Level 3 readiness gate. A
dependency lock, a pinned Dockerfile, determinism answers, a published
mirror, an environment archive and a current attestation are the
author's own claims; requiring them before a stranger may reproduce
the work would put the claim ahead of the check. The verdict is
therefore "reproduction-ready", never "Level 3". The rules, applied in
order, the first to fail named with the file that failed it:

1. The selected `project.json` loads through the ordinary migrations
   and validates. A file written by a newer vaibify is refused here by
   name, never as a bare traceback.
2. `.vaibify/environment.json` is present, pins a content digest (a
   tag is refused: it can be repointed without anything changing), and
   records the image's architecture. A legacy envelope with no
   architecture is refused rather than defaulted to this host's: the
   source names its environment, and there is no architecture picker.
3. `MANIFEST.sha256` parses, every line.
4. Every manifest entry matches the staged bytes.
5. No file the **selected** workflow declares is missing from the
   manifest. A repository hosting several workflows is validated for
   the one that will run.
6. If an image deposit is on record, it covers the pinned image *and*
   its architecture. No deposit on record is not a refusal -- the
   registry may still serve the image -- and is reported as such.

### What a report may carry

Only what `fdictDescribeStagedSource` returns: the source kind, the
resolved commit, the remote URL with any `user:password@` stripped,
the workflow name and its repo-relative path, and the validated facts
above. Never a path on the reproducer's machine. A reproduction report
is the reproducer's own artefact -- never an attestation, never
written into any repository, and never read by the Level 3 gate -- and
it may one day be deposited publicly, which is why the redaction is
applied when the snapshot is staged rather than when a report is
written.

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

**An archived environment is runnable, not rebuildable.** The deposit
preserves the image as it was built: binaries, not sources. It does not
let a reader reconstruct that image from first principles the way a
Guix or Nix derivation would, and it inherits `docker load`'s own
platform constraints — an arm64 deposit runs under emulation on an
amd64 host, or not at all. Preserving a runnable environment is the
claim; preserving a rebuildable one is out of scope and deliberately
so.

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
