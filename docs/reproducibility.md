# Reproducibility

Most scientists consider a result reproducible if someone else can rerun
the analysis and get the same answer to within some tolerance. That is
what a project's unit tests check, and it is the bar for PROOF Level 1.
Level 3 preserves the whole procedure: the exact computing environment,
saved permanently, and a recorded clean rerun in it whose every output
was assessed against published criteria. An output passes when its
bytes are identical to the published file, or when the author's own
published comparisons, the tests that ship with the project, pass over
it.

Why not demand identical bytes everywhere? As the
[QuickStart](quickStart.md) showed, two computers running the same code
can disagree in the last digits of a fitted value. Archiving the
environment removes the differences that come from software, but not
the ones that come from the processor: math libraries choose their
fastest code for the processor they find, and split work across however
many cores it has. So byte identity is reported whenever it holds, as
the strongest evidence there is, but it is never what Level 3 promises
on another computer. This page explains how vaibify records the
environment, saves it permanently, reruns the project in it, and judges
what the rerun produced.

## PROOF Level 3 — Reproducible

A project reaches Level 3 when four things exist:

1. **A fingerprint of every file.** A list of every input, script and
   output in the project, each with its SHA-256 hash, a 64-character
   summary of the file's bytes. Change one byte and the hash changes.
2. **The computing environment itself**, saved in a permanent public
   archive, so it cannot disappear.
3. **A script anyone can run** to rebuild the results from the files
   and the archived environment.
4. **Your confirmation** that you ran that reproduction and it passed
   its assessment: every output byte-identical, or the published
   comparisons passing over it.

Level 3 requires a containerized project, because only a container can
be saved and rerun exactly; a project in host mode reaches at most Level
2. It also builds on Level 1: the project lives in a git repository,
every step declares the data it reads, and any data pulled from a remote
source is committed, because the source may change or disappear.

### A word about Zenodo

The permanent archive vaibify uses is [Zenodo](https://zenodo.org), a
free service run by CERN. Each upload becomes a *record* with a DOI, the
same kind of permanent identifier a journal article has. A published
record can never be changed; to update it you publish a new *version*,
and every version keeps its own DOI. That permanence is the point, and it
also means mistakes are permanent, which is why the order of the final
steps matters (see below). Zenodo also runs a *sandbox* where you can
practice; sandbox records are not permanent, so they never count toward
Level 3.

Because published records are frozen, Level 3 describes a *release*,
not a work in progress. Change any of the files below after publishing
and the Level 3 rows turn red until you publish a new version.

## The reproducibility envelope

The files that let a stranger rerun your project are called the
*envelope*. They sit at the top of the project repository, and each can
be checked on its own:

| File | What it records |
|---|---|
| `MANIFEST.sha256` | The SHA-256 of every input, script, output and test file, and of the other envelope files |
| `requirements.lock` | Every Python package the project uses, with its exact version and hash |
| `.vaibify/environment.json` | The container image, the kind of processor it runs on, and the versions of Python, the compiler and the system libraries |
| `Dockerfile` | How the container image was built |
| `reproduce.sh` | The script that reruns the project and assesses the result |
| `.vaibify/rerunAssessment.py` | The assessor `reproduce.sh` runs, and the comparison policy it grades under |

The **Artifacts** section of the Project block has a row for each file.
You rarely write any of them by hand: **Regenerate now** rewrites the
whole envelope, and vaibify regenerates it automatically the first time
the project reaches Level 1.

### Tier 1 — the manifest

`MANIFEST.sha256` is the heart of the envelope. From the **Run** menu,
**Check Files Against Manifest** recomputes every hash and reports any
file that no longer matches. Two Level 3 rows read it: **Manifest
complete** (every file that should be listed is) and **Manifest matches
the files** (every listed hash still describes the file on disk). A
reader can run the same check without vaibify with `sha256sum -c
MANIFEST.sha256`.

When files differ, the notice counts them; click it for every
differing file and what is recorded about how each copy was made:
the settings and hardware recorded for the pinned copy and for this
one, or "production context unknown" when either was not recorded.
A recorded difference shows where to look; it does not prove why the
bytes changed. The check compares bytes and runs none of your
published tests, so it cannot say whether a difference is within your
published tolerances: **Verify Level 3** reruns the project and judges
that. Regenerating the envelope replaces the published hashes and
loses the comparison with them, so do it only for a change you intend
to publish.

### Tier 2 — the Python packages

`requirements.lock` lists each Python package at the exact version that
actually ran in your container, with a hash so a substitute package
cannot slip in. **Check dependencies** confirms that every entry
carries its hash. A project that uses only the Python standard library
still gets a lock: when your dependency file declares nothing, vaibify
writes a line saying so, and the check accepts it. An empty lock
without that line still fails, because it cannot tell "needs nothing"
from "never locked".

### Tier 3 — the container

`.vaibify/environment.json` records the container image by its
*digest*, an identifier computed from the image's contents, so it names
one exact image and nothing else. It also records the processor
architecture, the versions of everything beneath Python, any standalone
programs your steps run, and a fixed date that every step runs with, so
that figures which embed a date come out identical.

The `Dockerfile` records how the image was built, which is useful for
understanding it, but a reproduction never rebuilds the image.
Rebuilding would download today's packages, not the ones you used.
**Copy image Dockerfile into repo** puts it in your repository, and the
**Dockerfile pinned** row checks that it names its starting images
exactly.

### `reproduce.sh`

**Generate reproduce.sh** writes the script a stranger runs, and the
assessor beside it, `.vaibify/rerunAssessment.py`. The script finds the
archived image and, inside it:

1. checks every published input against the manifest and sets the
   published figures aside for comparison;
2. lists the declared outputs it is about to delete and how to restore
   them (`git checkout -- <paths>`, or the archived deposit), then
   deletes them, because it regenerates in the reader's own directory;
3. reads the processor and applies your recorded execution settings
   (see [The determinism question](#the-determinism-question));
4. runs every automatic step as vaibify's runner does: its data
   commands, then its plot commands, then each group of its tests, and
   the next step regardless of a failure in this one;
5. assesses the result and exits with the verdict's code (see
   [Exit codes](#exit-codes)).

It gives the same verdict, tally and exit code as **Verify Level 3
Reproducibility** on the same files. Steps that a person performed,
such as the AI Declaration, are not rerun; their committed files are
used as given. The **Reproduce script is current** row warns you if the
script or its assessor is older than what vaibify would generate today,
or if the assessor was edited.

## Archiving the environment

Why archive the whole environment instead of just listing its software?
Because a list is not enough. Images vanish from online registries, and
a newer version of the same library can differ from the old one in the
last digit of a function like `tan`, which is enough to change your
bytes. So Level 3 requires the image itself in a permanent archive.

The **Environment archive** row asks one question with three answers:
deposit the image, point to a deposit that already holds it, or decline.
Any answer satisfies Level 2. Level 3 requires that a matching archive
actually exists, so you can decline now and deposit later without
undoing anything. Don't wait too long, though: the image lives only on
your computer, and a cleanup, a rebuild or a new laptop can remove it
for good.

**Depositing** uploads the image to Zenodo as its own record, separate
from your project's record, so several papers that ran in the same
environment can share one archive. The row shows every choice of
destination before anything is uploaded, and recommends one. Expect
several gigabytes, several minutes, and the same amount of free disk.
Before publishing, vaibify asks Zenodo for the checksum of what it
received and stops if it differs from what was sent.

**Pointing to an existing deposit** (**Use a deposit that already holds
this exact image**) asks for the deposit's *version* DOI. A record's
general "concept" DOI is refused, because it always points to the newest
version, which may hold a different image. Vaibify accepts the deposit
only if it describes the same image digest and processor architecture
as your envelope.

Two things to know about the archive. First, it can be *run* but not
*rebuilt*: it preserves the finished programs, not the means to
recreate them. An image made for one kind of processor runs on another
only under emulation, which is slower. Second, it contains no coding
agent. Agents write code; they compute no results. The archive holds the
environment just beneath them, and vaibify refuses to deposit if an
agent's installation would change anything your steps use.

An online registry such as Docker Hub can also hold the image and makes
`reproduce.sh` faster, but a registry promises no preservation, so the
**Image on a registry (optional)** row never affects your level.

## The determinism question

The **Determinism** section of the Project block asks one question:
**How much speed should vaibify trade for repeatable files?** As with
the other declarations in vaibify, **answering is what counts**: either
answer passes, and only an unanswered question fails.

- **Prioritize speed.** Vaibify leaves your math libraries at full
  speed. Files made on another computer can differ in their last
  digits; Level 3 judges them by your published comparisons.
- **Repeatable across computers** (recommended for most projects).
  Vaibify runs NumPy, OpenBLAS and the C math library on one thread,
  using instructions that supported computers share, so your computers
  are more likely to produce identical files. Heavy numerical work can
  run several times slower. This reduces differences between computers;
  it cannot eliminate them.

Your answer changes how steps run, not what Level 3 requires. Changing
it later means rerunning every step once, and it retires your current
Level 3 attestation, which stays on file as history.

Behind the answer is a versioned **execution profile**, which vaibify
records and you never type: `speed-v1` requests nothing, and
`across-v1` requests `OPENBLAS_CORETYPE=Haswell`,
`NPY_DISABLE_CPU_FEATURES=AVX512_ICL,X86_V4`, `OPENBLAS_NUM_THREADS=1`
and `OMP_NUM_THREADS=1`. The envelope records the concrete profile, and
every rerun, in the dashboard or `reproduce.sh`, replays those recorded
settings rather than looking the answer up again, so a later vaibify
cannot change what your published profile asked for.

Every run follows the same order. First vaibify reads the processor, in
a process that loads no math library: its vendor, its `x86-64` level and
how many cores the run can see. A processor without `x86-64-v3` cannot
honor the instruction floor, so it runs without those two settings,
keeps the thread settings, and records "floor not applied: this
processor lacks x86-64-v3"; the dashboard warns you before such a run
starts. Then a separate probe loads NumPy under exactly the settings the
steps get, and records what OpenBLAS chose and whether NumPy applied
each feature it was asked to disable. NumPy before version 2.4 does not
know the name `X86_V4`; it ignores that request without a visible
warning, and keeps using AVX-512 instructions where the processor has
them. Vaibify says so before such a run starts, and `reproduce.sh`
prints it. The probe also multiplies two matrices with NumPy, in a
process of its own, because OpenBLAS can crash under a forced kernel on
a processor it was not tuned for: OpenBLAS 0.3.34, which NumPy 2.5.3
bundles, dies with a segmentation fault under `across-v1` on an AMD
`x86-64-v4` processor, where OpenBLAS 0.3.27 does not. When the product
crashes, vaibify names the crash and the kernel before the steps run,
in the dashboard and in `reproduce.sh`. The probe establishes what a
probe process saw; a step that changes its own settings is not
detected.

The cost was measured on one 8-core machine: about three to four times
slower for dense linear algebra, and about three times slower for
vectorized `exp` and `log`. The profiles cover NumPy, OpenBLAS and the
C math library only. Other libraries that choose code by processor,
such as Intel MKL, are not covered; the section says so when your
dependency lock names MKL.

If a step's scripts appear to draw random numbers without a seed, that
step carries a warning, and the **Determinism declared** row stays red
until you seed the generator.

The **Software declared** row covers programs your steps run that are
not Python packages, such as a compiled simulation code. Declare each
with its expected version, and **Capture version + SHA** records its
version and fingerprint. If you use none, say so.

## The order of the final steps

Most Level 3 requirements can be met in any order. The last few cannot,
because some of them rewrite files that others have already
fingerprinted, and a published Zenodo version cannot be fixed. Do them
in this order:

1. **Make the container match the package lock** by regenerating the
   envelope. A reproduction refuses to start in a container that does
   not match `requirements.lock`.
2. **Generate `reproduce.sh`.** This also updates the manifest.
3. **Settle the manifest.** The confirmation in step 5 records the
   manifest's fingerprint, so changing the manifest afterwards makes it
   stale.
4. **Archive the environment.** This writes the archive's record into
   the envelope, so it must come before the confirmation too.
5. **Verify Level 3** (next section) and commit the result.
6. **Push to GitHub and publish a Zenodo version** that includes the
   envelope and the confirmation. Publishing earlier would cost you a
   second version.

When the order matters, the Project block points to the one row to fix
next.

## The verification process

**Verify Level 3 Reproducibility**, in the **Run** menu or at the bottom
of the PROOF tab's Level 3 section, is how you confirm the reproduction
yourself. It first checks that everything is ready and tells you what
to fix if not. Then it explains what will happen and asks you to press
**Copy and verify**. Before you do, make sure nothing is changing files
in the container: no agent mid-task, no running step.

Vaibify then copies your project into a fresh, throwaway container built
from the image your envelope pins, with no network access, and reruns the entire
workflow there, tests included, under your recorded execution settings.
Your own files are never touched. Before anything runs, it checks every
published input and deletes every output the steps will produce, so an
old file can never be mistaken for a reproduced one. Then it assesses
the rerun (next section) and deletes the throwaway container.

The rerun takes as long as your workflow does. When it finishes, the
PROOF tab shows the **Level 3 Attestation** card, with the verdict and
its tally, each file's outcome and its reason, and the **Reproduction
History** of every attempt. A record made by an earlier version of the
check, which compared bytes alone, reads as a previous check: true
about what it checked, and not accepted by the current requirement. If a step fails, its name and
the end of its output are kept for you, since the container that ran it
is gone. The result is written to `.vaibify/l3_attestation.json`, and
the hashes the rerun observed are written to `REPRODUCED.sha256`, laid
out exactly like `MANIFEST.sha256` so the two can be compared line by
line. Commit both.

If someone else committed the clone's attestation, as when you check an
author's published project, the result is saved instead as a
reproduction record in `.vaibify/reproductions/` in your clone, and the
author's attestation is left alone. The hashes your rerun observed go
to a dated file in `.vaibify/reproducedManifests/`, which the record
names; the author's `REPRODUCED.sha256` is never written. Vaibify does
not stage or commit any of these files. They are yours, so commit them
only if you choose to.

You do not have to take vaibify's word for any of this. The file
checks use standard tools that anyone can run by hand: `sha256sum` for
the files, `pip` for the packages, and `docker` for the image. The
assessment itself is `.vaibify/rerunAssessment.py`, a readable Python
file pinned in your manifest, which `reproduce.sh` runs in the open.

## How a rerun is assessed

The assessment asks two questions, and keeps them apart.

**Integrity is exact.** Every published input (scripts, tests, their
standards, the envelope) must be unchanged before the rerun and after
it. Any change is "Published inputs changed", whatever else happened: a
step that loosened its own tolerance changed a published input.

**Agreement is graded.** Each regenerated output is, in this order:

1. **Failed** when its step's command failed, or it was not written, or
   a published comparison bound to it failed;
2. **Byte-identical** when its bytes equal the published file and every
   check bound to it passed;
3. **Comparisons passed** when its bytes differ but every published
   comparison bound to it passed (a figure may also pass vaibify's
   image check, below);
4. **Not enough evidence** otherwise: a check that skipped itself or
   wrote no report, or a difference with no comparison to judge it.

The overall verdict follows the same precedence: "Could not run",
"Published inputs changed", "Not reproduced", "Not enough evidence",
then "Reproduced: published comparisons passed; N of M files differ in
their bytes" or "Reproduced: every file byte-identical". The verdict
always comes with its tally.

**The evidence is the tests' own reports.** Every command the rerun runs
gets an identity (`A03.quantitative.1` is the first command of step
A03's quantitative tests), its exit status is recorded, and every test
command writes its own JUnit report under that identity. A check counts
only when the report from its own step and test file says it passed; a
command that never ran, a report that is missing, and a test that
skipped itself are all named, never read as passes. A structural check,
such as "this file exists", can fail an output but never establishes
agreement.

**The image check is narrow.** A raster figure (`.png`, `.jpg`, `.tif`)
whose bytes differ passes vaibify's own check when both files are a
supported encoding (one frame, at most 8 bits per sample) with the same
format, size and mode, and no channel of any pixel moves by more than 2
levels out of 255. The report gives the largest change and how many
pixels changed. It is labeled "within vaibify's image-difference
threshold", never as the author's comparison, and passing it is not
proof that a figure carries the same science. Anything outside it is
"not enough evidence", with the reason named; the remedy is a
quantitative standard over the data the figure shows.

**A changed vector figure cannot pass under policy v1.** A vector
figure (`.pdf`, `.eps`, `.svg`) whose bytes differ is "not enough
evidence" whatever comparisons exist: the policy does not compare
vector figures, so one reaches Level 3 only if its bytes match.
Answering "Repeatable across computers" makes matching bytes more
likely, not certain. The policy's own remedy also suggests a raster
companion or a quantitative standard over the plotted data; neither
changes the verdict on the vector file itself, and the dashboard says
so beside it. Publishing the figure as a raster puts it under the image
check. The remedy's wording cannot be corrected inside the frozen
policy; a future policy version can compare vector figures.

**The comparison policy is frozen.** These rules are the comparison
policy `comparison-v1`, recorded on the second line of
`.vaibify/rerunAssessment.py`. A reproduction grades under the policy
the project recorded, using vaibify's own copy of that policy, never
the project's file. A later fix becomes a new policy version; the Level
3 rebuild requirement accepts only an attestation made under the
current one, and regenerating `reproduce.sh` moves a project to it.

### Exit codes

`reproduce.sh`, `vaibify reproduce` and the dashboard share one table:

| Code | Verdict |
|---|---|
| 0 | Reproduced (byte-identical, or published comparisons passed) |
| 1 | Not reproduced |
| 3 | Published inputs changed |
| 4 | Could not run |
| 5 | Not enough evidence |

### Where each output came from

Each time a step's data or plot commands complete, vaibify records, in
`.vaibify/outputProvenance.json`, which outputs they wrote and the
context they ran under: the processor's vendor, `x86-64` level and
visible cores, the execution profile and the settings that applied, and
what the probe saw. Only the section that really ran records; running
the tests, a failed section and a skipped one relabel nothing. No
processor model name and no computer name is recorded. The envelope
copies each pinned output's context, and a file whose bytes no longer
match its record reads "production context unknown".

A recorded context makes the possible causes of a difference
inspectable; it does not prove why the bytes changed, and acceptance
rests on the published comparisons alone. A verification puts each
differing output's production context beside the rerun's. A step whose
outputs were made under different settings or hardware says "Produced
under different execution settings or hardware" and names what
differs, as information, not as a failure. When every manifest
mismatch was made under different recorded settings or hardware,
**Check Files Against Manifest** offers to make this computer's results
the published ones, by rerunning every step here and regenerating the
manifest. Nothing requires it, and doing it replaces the published
hashes, losing the comparison with them.

## Reproducing a published project

You can also check somebody else's Level 3 project without adding it to
your own projects. In the hub, press **+** and choose **Reproduce a
published project**. Enter the project's GitHub address (or a clean
clone on your computer) and press **Stage**. Vaibify downloads one exact
snapshot and shows you the workflow, the pinned image and processor, and
whether your computer's processor matches. If it does not, you can allow
emulation, and the result will say so. The image is not downloaded,
and nothing runs, until you press **Run**; **Not now** discards the
snapshot.

Vaibify first checks that the snapshot is ready to reproduce: its
project file loads, the image is pinned exactly, and every file matches
the manifest. This is deliberately not the author's Level 3 checklist;
whether the author finished their paperwork should not stop you from
checking the work. It then obtains the image (from a registry, then the
author's Zenodo archive, checked against its fingerprint, then any copy
already on your computer), reruns every step in a throwaway container
under the author's recorded execution settings, and assesses the result
exactly as above.

The verdict is the assessment's, followed by its tally, and says when
it ran under emulation; or it is "Could not run", with the reason. It is
saved as a report under `~/.vaibify/reproductions/reports/`. The report
is yours, not the author's: nothing is written into their project.

## Keeping the compiler fixed

The compiler and system libraries inside a vaibify container are pinned
to a dated snapshot of the Ubuntu package archive, so they change only
when vaibify's maintainers deliberately move that date, never because
Ubuntu published an update. When the date does move, results built
before the change should be rerun and re-verified.

A banner reading **This container is running an older version of your
environment** means your `vaibify.yml` has changed since the container
was built. Published results are unaffected, because they are tied to
the archived image. New results come from a different environment,
though, so regenerate the envelope and verify again before mixing them
with older ones.

A container running the author's pinned image never shows that banner.
If its settings differ from the ones the pinned image was built from,
leaving aside the coding agents vaibify added on top of it, it shows
**This environment's settings differ from the author's pinned image**
instead. The pinned image defines that environment, so do not rebuild
to apply the settings: a rebuild replaces the author's environment.

### When a pinned toolchain version disappears

If a build stops with "the pinned compiler toolchain is no longer
available", the snapshot server may be unreachable, or vaibify's
pinned versions and their date have fallen out of step, which is for
its maintainers to fix. Never work around it by unpinning. To check published work you do not need to build anything:
`reproduce.sh` uses the archived image.

## Known limitations

- **A verification establishes what happened in the tested
  environment**, not what will happen on every future computer. The
  archived image removes the software difference; the execution
  profile reduces the processor's, and cannot remove it.
- **Settings are requested and confirmed in a probe process**, not
  observed in every step's process.
- **`across-v1` cannot run on every processor.** Where OpenBLAS crashes
  under its forced kernel, every step that multiplies matrices fails,
  and the rerun grades those outputs failed. It says why, but the
  result cannot be regenerated there under that profile.
- **The image check is narrow** (above): passing it is not proof that a
  figure carries the same science.
- **`reproduce.sh` regenerates in the reader's own directory**, and
  deletes the declared outputs first. A run that fails leaves them
  absent rather than stale; `git checkout` restores them.
- **A tampered verifier cannot detect itself.** That is true of every
  verification tool, which is why each check can also be run by hand.
