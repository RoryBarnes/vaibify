# Reproducibility

PROOF Level 3, **Reproducible**, means that a third party can rerun a
project and obtain the same bytes, and that the instructions for doing
so are published beside the results. This page describes how a vaibify
project reaches and verifies Level 3: the files that record the
project, the archived environment it ran in, the declarations the
researcher makes, the order the final steps must happen in, and the
rerun that proves the claim. It also describes how to reproduce
somebody else's published project.

The ladder itself, and the requirement tables for every level, are on
[The PROOF Ladder](proofLadder.md). Pushing, archiving and remote
verification are on [Connecting to External
Resources](externalResources.md).

## PROOF Level 3 — Reproducible

Level 3 is a claim about **file-byte identity**. The SHA-256 hashes a
project publishes describe the exact bytes its run produced, and a
rerun inside the archived environment must produce the same bytes.
Every piece of the claim can be checked with standard tools
(`sha256sum`, `pip`, `docker`), so vaibify is the orchestrator of the
check, never a dependency of it.

Three properties follow:

- **Level 3 requires a containerized project.** It is defined by a
  pinned image and a rerun inside it, which a host-mode project does
  not have; such a project reaches at most Level 2.
- **Level 3 is a release-time property.** The envelope, the rebuild
  attestation and the environment image must be in permanent Zenodo
  records, and published versions are immutable, so any change to
  those files turns the Level 3 rows red until the next published
  version. "Reproducible" describes a published release, not a working
  tree.
- **The sandbox does not count.** The **Archives are permanent** row
  blocks Level 3 while the project deposit or the environment archive
  is a Zenodo sandbox record.

## What the envelope rests on

Three Level 1 rules make the envelope meaningful. Every project lives
inside a git repository (the work tree enclosing its `project.json`),
and its test markers are committed under `.vaibify/test_markers/`, so a
fresh clone carries its verification state. Every step declares the raw
data it reads (`saInputDataFiles`, or `bNoInputData`), and each run
records a SHA-256 for every declared input. And remote data is
committed, as described next. [The PROOF Ladder](proofLadder.md) has
the full Level 1 requirements.

### Remote data is committed, and its source URL is inert

Data pulled from a remote source must be committed to the repository,
because the source may change or disappear. The pulling step records
each file in `listRemoteData` with four fields: `sPath`, `sSourceUrl`,
`sDigestBecameCurrentUtc` and `sSha256`, refreshed after every
successful pull. **`sSourceUrl` is inert metadata that vaibify never
fetches.** It records where the data came from; nothing in vaibify
dereferences it.

A run that would re-pull files that already exist asks for
confirmation first (a dialog in the browser,
`--confirm-remote-overwrite` for the in-container agent after it has
asked the researcher), and fresh data is never committed
automatically.

## The reproducibility envelope

The envelope is the set of files at the root of the project repository
that let a stranger rerun the project. Each tier is a separate file
that can be verified independently.

| File | Tier | What it pins |
|---|---|---|
| `MANIFEST.sha256` | 1 | The SHA-256 of every declared file and of the other envelope files |
| `requirements.lock` | 2 | Every Python dependency, by exact version and hash |
| `.vaibify/environment.json` | 3 | The container image, its architecture, and the system toolchain |
| `Dockerfile` | provenance | How the image was built |
| `reproduce.sh` | recipe | One command that reruns the project and checks the result |

The **Artifacts** section of the Main tab's Project block has one row
per file. **Regenerate now** on the manifest, dependency lock or
environment snapshot row rewrites the whole envelope. The envelope is
also regenerated automatically when a step's verification first brings
the project to Level 1, except in a clone whose manifest was committed
by somebody else, which is never overwritten without an explicit
regeneration.

### Tier 1 — Artifacts (`MANIFEST.sha256`)

A GNU-coreutils checksum file with one `<sha256>  <path>` line per
file, paths relative to the repository root. It covers:

- every step's outputs (`saPlotFiles`, `saOutputDataFiles`) and
  declared inputs;
- the scripts named in each step's data and plot commands;
- test files and test standards (a project may opt out with
  `bArchiveTests: false`);
- the other envelope files that exist: `requirements.lock`,
  `reproduce.sh`, `.vaibify/environment.json`, `Dockerfile`, and any
  `requirements.txt`, `environment.yml` or `pyproject.toml`.

The manifest cannot pin itself, and it does not pin the rebuild
attestation, which records the manifest's digest instead. Paths
containing a newline or backslash use the GNU escape convention, so a
crafted filename cannot forge a second line. A symbolic link that
resolves inside the repository is hashed by its target's content; one
that resolves outside is never opened and is reported as a gap.

Anyone can check it without vaibify:

```bash
sha256sum -c MANIFEST.sha256
```

In the dashboard, **Check files against manifest** does the same. Two
Level 3 rows read the manifest: **Manifest complete** (every file that
should be pinned is) and **Manifest matches the files** (every pinned
hash is the file's current bytes). A manifest can list every file and
still describe none of them, so both are required.

### Tier 2 — Python dependencies (`requirements.lock`)

A hash-pinned lock file, generated with `uv pip compile
--generate-hashes` from the first declaration found, in this order:
`pyproject.toml`, `requirements.in`, `requirements.txt`,
`.vaibify/requirements.txt`. The last is the file the container
installs at startup.

The compile runs on the host but is **constrained to what the
container runs**: vaibify asks the container for its Python version,
architecture and installed packages, and the resolver holds every
package the declaration reaches to the version that actually ran. A
container that cannot be asked fails the tier by name rather than
producing an unconstrained lock. A host-mode project compiles against
the host interpreter, which is the one its steps use.

A verifier needs only stock `pip` (`pip install --require-hashes -r
requirements.lock`). **Check dependencies** confirms the file parses and every entry carries
a hash. If the container no longer satisfies the lock, the Level 3
rerun would refuse, so the dashboard says so before you start one.

### Tier 3 — Container and system layer (`.vaibify/environment.json`)

The environment snapshot records what lies beneath Python:

- the **image digest** (`dictContainer.sImageDigest`) of the agent-free
  environment the container runs on (see [the archived environment
  holds no coding agent](#the-archived-environment-holds-no-coding-agent));
- the image's **architecture** (`sArchitecture`), read from the image
  itself, because a multi-platform digest pins no single platform;
- the Python, `gcc`, C library and operating-system versions inside the
  container;
- the SHA-256 and version line of each standalone binary the project
  declares;
- the **source-date epoch** (`iSourceDateEpoch`): the timestamp every
  step runs with as `SOURCE_DATE_EPOCH`, from which matplotlib's SVG
  hash salt is also derived, so figures that embed a date or random
  identifier reproduce. Reproduction reads the recorded value rather
  than re-deriving it, because committing the manifest moves HEAD;
- the environment archive record, when one exists (see [The
  environment archive](#the-environment-archive)).

### The Dockerfile is provenance; the digest is reproduction

A vaibify image is built from vaibify's own packaged Dockerfiles as a
chain: a base image, then one build per enabled feature. **Copy image
Dockerfile into repo** on the Dockerfile row composes that chain into
one multi-stage file you can commit, and the **Dockerfile pinned** row
requires every `FROM` line to name an exact `@sha256:` digest.

The Dockerfile records *how* the image was made. It is not how a
reproduction obtains the image: `reproduce.sh` pulls or loads the
pinned digest and never builds, because rebuilding reaches a live
package archive and cannot reconstruct the original bytes.

### `reproduce.sh`

**Generate reproduce.sh** writes the one-command reproduction recipe at
the repository root and re-pins the manifest in the same action. The
**Reproduce script is current** row checks that the file on disk is
still exactly what vaibify would generate now, so an old script does
not silently lack newer machinery. On a stranger's machine it needs
Docker, `jq`, `curl` and `sha256sum` (and `zstd` for a `.zst` archive).
It reads the image digest, architecture and source-date epoch from
`.vaibify/environment.json` (announcing any that is missing rather
than guessing); obtains the image by a registry pull, then from the
archived deposit (fetched only from the Zenodo host the record names,
bounded by its recorded size, and checked against its recorded SHA-256
before `docker load`), then from a copy already on the machine (with a
warning that only the author can take that path); installs
`requirements.lock`; runs the steps inside the image at the recorded
platform and epoch; and ends with `sha256sum -c MANIFEST.sha256`.

It runs exactly the steps the Level 3 rerun executes: a step a human
performs, such as the AI Declaration, is not run, and its committed
outputs are used as given. Step text reaches the container through a
quoted heredoc, so a step command cannot inject a command onto the
reproducer's host.

## The environment archive

A digest names bytes that somebody else is storing; when a registry
stops serving the image, the compiler, libraries and packages go with
it. A newer image is not a substitute: IEEE-compliant libraries can
still differ in the last place of a transcendental function. Level 3
therefore requires the image itself in a permanent archive. A container
registry (Docker Hub, GHCR) only makes `reproduce.sh` faster; the
**Image on a registry (optional)** row reports it, but a registry
promises no preservation, so it never gates a level.

### One question, two levels

Level 2 asks whether you **answered** the question on the **Environment
archive** row in the Artifacts section: deposit the image, reference a
deposit that already holds it, or decline. Any answer satisfies Level
2. Level 3 asks whether a matching archive **exists**, and never reads
the Level 2 answer, so a project that declined and later deposits
reaches Level 3 with nothing to undo. The image is a local resource
that a prune, a rebuild or a new computer removes, so the chance to
archive it may not come back.

### Depositing

The row names every destination before anything is uploaded: a new
version of the project's earlier image record, a new record on
zenodo.org, or a new record on the sandbox (practice only). Nothing is
pre-selected; the row recommends one and says why. Vaibify then runs
`docker save`, compresses the result (zstd when available, otherwise
gzip), uploads it, and publishes it under its own version DOI, in a
record separate from the project's deposit so several papers can share
one image. Expect several gigabytes, minutes, and as much free disk.
The row shows each phase and the bytes sent, retries a dropped
connection, and lets you stop the deposit until it starts publishing.

### Referencing an existing deposit

Several papers built in one image can share one deposit. Choose
**Use a deposit that already holds this exact image** and give the
deposit's version DOI. Vaibify fetches the record and accepts it only
if its description, written by vaibify when it was deposited, names
the same image digest and architecture the envelope pins. A concept
DOI is refused, because it resolves to whatever version is newest.

### How the archive is verified

The row does not turn green on vaibify's word alone:

- **Before a deposit is published**, vaibify asks Zenodo for the
  checksum it computed of the uploaded file, and refuses to publish if
  it disagrees with what was sent.
- The record carries two hashes: one of the uploaded tarball (what a
  downloader checks) and one of the uncompressed image stream (what a
  later re-check compares, since two compressors give two tarballs for
  one image).
- The record carries the digest and architecture it covers. A
  regenerated envelope keeps the record only if both still match.
- **At attestation time**, the deposit is re-checked against the local
  image. If the image was itself loaded from the archive, the check is
  reported as vacuous rather than passed, because a download compared
  with itself always matches.

An archived environment is **runnable, not rebuildable**: it preserves
binaries, not the means to reconstruct them, and it inherits `docker
load`'s platform limits (an arm64 deposit runs under emulation on an
amd64 machine, or not at all).

### The archived environment holds no coding agent

Coding agents write code; they compute no result. So the image that
the envelope pins, the rerun uses and the archive deposits is the
**agent-free environment**, the build stage just below the first coding
agent, and a reproducer brings whichever agents they like, or none.
Each build stage is labeled with what it holds, and the snapshot pins
the agent-free stage only after confirming that its layers are a
prefix of the running image's. The agents used are recorded by name in
the envelope and by version in the AI provenance record, and the
deposit's description names them without including them.

That is honest only if the agents cannot change what the environment
computes. Before a deposit, and when a reproducer stacks their own
agents on an obtained image, vaibify reads the agent layers out of
`docker save` and refuses if they overwrite or remove an environment
file (package-manager bookkeeping and logs excepted), shadow a command
already on the `PATH`, add files where an interpreter or the loader
searches (Python packages, shared libraries, shell or loader
configuration, fonts, R or Julia libraries), or change shell startup
files or image settings beyond prepending to `PATH`. These rules cover
the search mechanisms vaibify knows, so a pass is strong evidence; the
proof is the Level 3 rerun inside the agent-free image.

## Determinism declarations

Before attesting, the researcher answers questions about the sources
of run-to-run variation. **Answering is the criterion**: a declining
answer passes, and only silence fails. The **Determinism** section of
the Project block asks three questions, each with its own marker:

| Question | Answers |
|---|---|
| **Last-digit numeric differences.** Linear-algebra libraries split sums across threads and add the pieces in whatever order they finish, so final digits can differ between runs on one machine. Do you accept those differences? | Accepted, or not accepted |
| **Thread count.** Fixing the number of threads removes one source of that reordering. Is it fixed? | Fixed (with the number), or not fixed |
| **Intel maths library (MKL).** MKL can choose different internal routines on different processors, so the same input can give different final digits on a different machine. It has a setting that prevents this. | MKL is not used, or MKL is used with that setting (with its value) |

Each question has its own **Save this answer** button; the answers are
stored in `project.json`, and **Delete all answers…** withdraws them.
A step whose scripts appear to draw random numbers without a seed
carries a warning, and the **Determinism declared** row stays red until
the generator is seeded or the declaration covers that step.

A separate row, **Software declared**, covers untracked executables:
every standalone program the steps run outside Python packages is
declared with its expected version, and **Capture version + SHA**
records its version line and hash. A project with none says so
explicitly (a waiver). Timestamps need no declaration: every step runs
with the recorded source-date epoch described under Tier 3.

## The order of the final steps

Most Level 3 requirements can be met in any order. The last few
cannot, because some actions rewrite files that others have already
recorded, and Zenodo versions cannot be corrected after publication.

1. **Make the container satisfy the dependency lock**, by regenerating
   the envelope (or by rebuilding the image to match the lock). A rerun
   in an image that does not satisfy `requirements.lock` refuses before
   it starts.
2. **Generate `reproduce.sh`.** It re-pins the manifest in the same
   action, so doing it first saves regenerating the manifest twice.
3. **Settle the manifest.** The attestation records the manifest's
   digest, so any later change to the manifest makes the attestation
   stale.
4. **Archive the environment.** Depositing writes the record into
   `environment.json` and re-pins the manifest, so an attestation made
   before it is stale at once.
5. **Verify Level 3** and commit the attestation it writes.
6. **Push the envelope to GitHub and publish a Zenodo version**
   containing the envelope and the attestation. Level 3 asks whether
   the attestation *in the archive* covers the manifest *in the
   archive*; publishing first would cost a second Zenodo version.

The Project block points at the one row to fix next when the order
matters, and shows nothing when the remaining work can be done in any
order.

## The verification ceremony

The **Rebuild attestation** row is satisfied by a rerun: vaibify copies
the project into a fresh, throwaway container built from the image the
envelope pins, reruns the whole workflow there, and compares every
output it produced with the manifest. The researcher launches it and
the attestation records the outcome.

### From the dashboard

**Verify Level 3 Reproducibility** at the foot of the PROOF tab's Level
3 section (or **Verify Level 3 reproducibility** in the Project block)
first checks readiness. If an envelope file is missing, or the
container does not satisfy the dependency lock or the declared
packages, it shows what to fix instead of starting. When ready, it
explains what will happen and asks you to confirm with **Copy and
verify**. Make sure nothing is writing in the container first (an
agent mid-task, a terminal command, a running step): the copy is taken
while the container runs, and a copy that changes while it is taken is
refused.

The rerun can take as long as the workflow itself. The PROOF tab shows
its progress only while it is running. Afterwards it shows the
**Level 3 Attestation** card (time, manifest digest, image digest,
hashes matched, duration, and a notice if the manifest has changed
since) and the **Reproduction History** of every attempt. When a rerun
fails, the first failing step's name, exit code and the end of its
output are kept, because the throwaway container no longer exists.

### What the rerun does

- **It runs in a shadow container**, never in your project container,
  so your outputs are untouched and the rerun uses the pinned image
  rather than whatever your working container has accumulated. The
  shadow carries a copy of the repository and nothing else, has no
  network, and is destroyed afterwards. It still runs on your own
  Docker daemon from an image already there, so it cannot detect an
  image a fresh machine could not obtain; that is what tiers 1 to 4 of
  `vaibify reproduce`, and `reproduce.sh` itself, are for.
- **It regenerates every output it grades.** Before any step runs, it
  deletes every output an executed step produces, so a file left over
  from the original run can never be graded as reproduced. Every step
  runs its data commands even if it is set to plot only. An expected
  output that does not appear is *missing*, never *matched*.
- **Outputs of a human step are given, not reproduced.** An interactive
  step, such as the AI Declaration, is not run; its committed outputs
  are carried into the copy unchanged, excluded from the comparison,
  and listed beside the counts. A workflow whose every pinned file is
  a given output has nothing to reproduce and is refused.
- **Disabled steps refuse the rerun.** Being interactive is a property
  of the workflow, but disabling is a switch, and attesting around a
  switched-off step would certify a rerun that skipped it.
- **The expected hashes are frozen before the run**, so a step that
  rewrites the manifest cannot bless its own changed output.

### What a verification writes

- `.vaibify/l3_attestation.json`, plus a timestamped copy under
  `.vaibify/l3_attestations/`: the manifest digest compared against,
  the image digest, the counts of regenerated outputs that matched and
  of pinned inputs left unchanged, the carried paths, and every path
  that diverged.
- `REPRODUCED.sha256` at the repository root, in the same format and
  order as `MANIFEST.sha256`, holding the hashes the rerun observed,
  with a `# MISSING` line for anything it did not produce, so the two
  files can be compared with `diff`. Earlier copies are kept under
  `.vaibify/reproducedManifests/`.

A rerun that was refused before any step ran reached no verdict and
writes nothing, leaving any earlier attestation in place. A rerun that
ran and diverged does write an attestation, recording the failure. In
a clone whose attestation was committed by somebody else, the rerun
records a reproduction under `.vaibify/reproductions/` and leaves the
author's attestation alone.

### From the command line: `vaibify reproduce`

`vaibify reproduce` walks the envelope in tiers from a checked-out
repository:

| Tier | What it does |
|---|---|
| 1 | Checks `MANIFEST.sha256` against the files on disk. |
| 2 | Installs `requirements.lock` with `pip install --require-hashes` (retrying with `uv` on a hash error, if `uv` is installed). |
| 3 | Pulls the pinned image by digest. |
| 4 | Runs the same Level 3 readiness checks as the dashboard. |
| 5 | With `--rerun`, the shadow rerun and attestation described above. |

| Option | Meaning |
|---|---|
| `--repo DIRECTORY` | The project repository (default: the current directory). |
| `--rerun` / `--no-rerun` | Also run tier 5. Off by default, because a rerun can be expensive. It needs the project's container running, since the copy is taken from it. |
| `--workflow TEXT` | The workflow to rerun, required when the container holds more than one. |
| `--skip-tier 1\|2\|3\|4` | Skip a tier; may be repeated. |

The exit code is `0` when every selected tier passes, `1` when any
tier fails, and `2` for a usage error such as a missing envelope file.
A run ends with `L3 reproduction confirmed and attested.`, `L3
reproduction ready` (without `--rerun`), or `L3 reproduction failed;
see tier output above.`

### Vaibify is not the trust anchor

If `vaibify reproduce` were ever wrong, a verifier working by hand would
catch it. Tier 1 is `sha256sum -c`, tier 2 is `pip install
--require-hashes`, tier 3 is `docker pull <image>@sha256:…`, and each
can be run by anyone who reads the envelope. That independence is what
makes vaibify auditable rather than authoritative. Vaibify never
stores tokens in environment variables or configuration files; the
credentials a project uses for publishing stay in established
credential stores (see [Connecting to External
Resources](externalResources.md)).

## Reproducing a published project

`vaibify reproduce --from` starts from what a stranger has, a clone URL
or a clean local clone, and reruns the project in a shadow container
without installing it as one of your own projects.

```bash
vaibify reproduce --from https://host.example/group/project.git --rerun
```

- Alone, `--from` stages an exact snapshot, validates it, prints what a
  rerun would use (workflow, commit, pinned image, platform, archived
  deposit), and discards it. Nothing is pulled or run.
- `--prepare` also obtains the pinned image, so a long download can be
  done ahead of time.
- `--rerun` obtains the image, reruns the snapshot in a shadow
  container, compares the outputs with the project's manifest, and
  writes a reproduction report. The exit code is `0` only when the
  verdict is *reproduced*.
- `--allow-emulation` permits running a pinned build of a different
  architecture than your Docker daemon's.
- `--workflow` selects the workflow when the repository holds several.

`--from` cannot be combined with `--repo` or `--skip-tier`.

### From the dashboard

The hub's **+** button offers **Reproduce a published project** beside
*Container* and *This machine*. Enter a source and click **Stage**; the
confirmation shows the workflow, commit, pinned image and platform, any
deposit on record, and whether your daemon's architecture matches (with
an emulation checkbox if not). Nothing is pulled or run until **Run**;
**Not now** discards the staged clone. **Hide** closes the progress
view without stopping the job, and the result lists the verdict, every
pinned file by outcome, the platform facts and a link to the report.
Staged jobs expire if never run, and jobs end with the hub that started
them. No project tile is created.

### What a source can be

| Source | Accepted as | Notes |
|---|---|---|
| An `https://` or `ssh://` clone URL, or `user@host:path` | git URL | Recognized by shape, never by which host serves it. A URL carrying a username, password or token is refused: credentials in a URL end up in shell history, reports and the container. |
| A clone under your home directory | local clone | Only when `git status` reports nothing at all, including untracked and ignored files. |

`file://`, `git://`, `ext::` and plain `http://` sources are refused.
A URL is cloned in full (history and the source-date epoch matter); a
local clone is cloned, not copied, at the commit you have checked out.
Every later stage uses that one snapshot. Git runs hardened, with no
stored credentials and no prompts, so an unknown host key or a locked
key fails the clone instead of hanging, and a clone that grows past a
size limit is stopped.

### Validation is strict, and it is not the Level 3 gate

The snapshot must pass these rules, applied in order; the first failure
is named with the file that failed it:

1. The selected `project.json` loads and validates.
2. `.vaibify/environment.json` exists, pins the image by content
   digest (not a tag), and records the image's architecture.
3. `MANIFEST.sha256` parses.
4. Every manifest entry matches the staged bytes.
5. Every file the selected workflow declares is in the manifest.
6. If an image deposit is on record, it covers the pinned image and
   architecture. No deposit is not a refusal; a registry may still
   serve the image.

The verdict is "reproduction-ready", never "Level 3". The author's
dependency lock, Dockerfile, determinism answers, published copies and
attestation are the author's claims; requiring them before a stranger
may check the work would put the claim ahead of the check.

### How the image is obtained

In the same order as `reproduce.sh`: a **registry pull**, then the
**archived deposit** (its hash checked against the envelope before
anything reaches Docker, and the tarball deleted afterwards), then a
**copy already on this daemon** (which only the author is likely to
have, and the report says so). Every attempt is reported. There is no
rebuild from the Dockerfile.

The platform is three separate facts:

| Fact | Source |
|---|---|
| Required platform | The envelope's recorded architecture |
| Obtained platform | What the image that was obtained reports |
| Daemon architecture | Asked of the Docker daemon itself |

An obtained platform that differs from the required one is always
refused. A daemon of another architecture means emulation, which is
refused unless allowed and then recorded in the verdict.

### What a report is

A reproduction report is **your** record, under
`~/.vaibify/reproductions/reports/`: the source (credentials stripped,
no local paths), commit, manifest digest, platform facts, image
origin, per-file comparison, given files, any failing step's output,
and the verdict: *reproduced*, *reproduced under emulation*,
*diverged*, or *no verdict* (with the reason). It is never an
attestation, never written into any repository, and never read by the
Level 3 gate.

## The toolchain epoch

A vaibify image pins its compiler toolchain on three axes:

- **the base image**, by digest;
- **every package in the compile-and-link path**, by version: the
  compiler, assembler and linker, C library and headers, gcc's math
  libraries (which perform constant folding and so can change emitted
  numbers), the runtime libraries, and `make`, with one list per
  supported architecture (amd64 and arm64);
- **the package archive**, by date: `APT_SNAPSHOT_DATE` in the
  Dockerfile points apt at Ubuntu's snapshot of the archive as it
  stood on that day, so the pinned versions keep resolving after
  Ubuntu drops them from its live mirrors.

The toolchain therefore changes only when a maintainer moves the date,
never when Ubuntu publishes. Packages that cannot reach a numerical
result (editors, viewers, LaTeX, graphviz) are deliberately unpinned,
though each build reads one consistent snapshot of the archive.

### Moving the epoch

Moving the date is how security and correctness fixes reach users. It
changes the compiler and C library, so it is a reviewed change, never
automatic, and results built on the old epoch must be rerun and
re-verified. `python tools/checkToolchainEpoch.py --propose` lists what
moving the date would change (every candidate version, without choosing
one), and `--verify` confirms every pin resolves at the pinned date; a
scheduled CI lane asks the same question and opens an issue. Between
epochs the container runs an older C library while holding publishing
credentials, which is why the epoch should move regularly.

### When a pinned toolchain version disappears

With the archive frozen this should not happen. If a build stops with
"the pinned compiler toolchain is no longer available", the likely
causes are: the date and the pins were edited apart (`--verify` says
so), the snapshot server is unreachable, or the Docker daemon's
architecture has no pin list (refused with its own message before apt
runs). The response is never to unpin. To verify published work you
do not need to build at all: `reproduce.sh` uses the archived image.

### When a rebuild changes the environment

When `vaibify build` produces an image different from the one the
recorded results came from, it says so, and whether vaibify's own build
recipe changed too (if not, the difference came from outside vaibify).
A banner also appears when `vaibify.yml` has changed since the running
container was built.

Published results are unaffected: they are pinned to the recorded
image. Results computed afterwards come from a different environment,
so regenerate the envelope and re-verify before mixing them with older
ones; otherwise the manifest reports the difference as a divergence.
For a chaotic system, an individual trajectory may legitimately change
after a library update; an ensemble statistic should not move by more
than its own Monte Carlo error.

## Known limitations

- **A rerun is byte-exact only within one environment.** Re-executing
  on a different CPU, linear-algebra library or toolchain can produce
  numerically near-identical but byte-different outputs. The archived
  image removes the toolchain difference; the determinism declarations
  record the rest.
- **A tampered verifier cannot be detected** by itself, which is true
  of every verification tool. The mitigation is that every tier can be
  checked with standard tools instead.
