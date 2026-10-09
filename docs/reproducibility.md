# Reproducibility

Most scientists consider a result reproducible if someone else can rerun
the analysis and get the same answer to within some tolerance. That is
what a project's unit tests check, and it is the bar for PROOF Level 1.
Level 3 is much stricter: anyone, anywhere, at any time, can rerun your
project and get back *exactly the same bytes* in every output file. When
that holds, there is no doubt at all about how your inputs became your
outputs.

Byte-for-byte reproduction is harder than it sounds. As the
[QuickStart](quickStart.md) showed, two computers running the same code
can disagree in the last digits of a fitted value, because the rules
that govern floating-point arithmetic allow small differences between
libraries and processors. The only reliable cure is to rerun the work
in the *exact* computing environment that produced it. This page
explains how vaibify records that environment, saves it permanently,
and proves the reproduction works.

## PROOF Level 3 — Reproducible

A project reaches Level 3 when four things exist:

1. **A fingerprint of every file.** A list of every input, script and
   output in the project, each with its SHA-256 hash, a 64-character
   summary of the file's bytes. Change one byte and the hash changes.
2. **The computing environment itself**, saved in a permanent public
   archive, so it cannot disappear.
3. **A script anyone can run** to rebuild the results from the files
   and the archived environment.
4. **Your confirmation** that you ran that reproduction and every byte
   matched.

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
| `reproduce.sh` | The script that reruns the project and checks the result |

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

### Tier 2 — the Python packages

`requirements.lock` lists each Python package at the exact version that
actually ran in your container, with a hash so a substitute package
cannot slip in. **Check dependencies** confirms that every entry
carries its hash.

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

**Generate reproduce.sh** writes the script a stranger runs. It finds
the archived image, runs every automatic step inside it, and finishes
by checking every file against the manifest. Steps that a person
performed, such as the AI Declaration, are not rerun; their committed
files are used as given. The **Reproduce script is current** row warns
you if the script is older than what vaibify would generate today.

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

## Declaring what can still vary

Even in an identical environment, a few things can make two runs
differ. Level 3 asks you to say how your project handles them. As with
the other declarations in vaibify, **answering is what counts**: an
honest "no" passes, and only an unanswered question fails. The
**Determinism** section of the Project block asks three questions, each
saved with its own **Save this answer** button:

- **Last-digit differences.** Linear-algebra libraries often split a
  sum across several threads and add the pieces in whatever order they
  finish, so the final digits can differ from run to run. Do you accept
  that?
- **Thread count.** Fixing the number of threads removes that source of
  variation. Is it fixed, and to what?
- **The Intel math library.** If your code uses it, it can choose
  different internal routines on different processors unless a setting
  prevents it. Do you use it, and with that setting?

If a step's scripts appear to draw random numbers without a seed, that
step carries a warning, and the **Determinism declared** row stays red
until you seed the generator or the declaration covers the step.

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
workflow there. Your own files are never touched. Before anything runs,
it deletes every output the steps will produce, so an old file can never
be mistaken for a reproduced one. Then it compares every new file with
the manifest and deletes the throwaway container.

The rerun takes as long as your workflow does. When it finishes, the
PROOF tab shows the **Level 3 Attestation** card, with the time, the
fingerprints it compared and how many files matched, and the
**Reproduction History** of every attempt. If a step fails, its name and
the end of its output are kept for you, since the container that ran it
is gone. The result is written to `.vaibify/l3_attestation.json`, and
the hashes the rerun observed are written to `REPRODUCED.sha256`, laid
out exactly like `MANIFEST.sha256` so the two can be compared line by
line. Commit both.

You do not have to take vaibify's word for any of this. Every check is
a standard tool that anyone can run by hand: `sha256sum` for the files,
`pip` for the packages, and `docker` for the image. Vaibify just runs
them in the right order.

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
already on your computer), reruns every step in a throwaway container,
and compares the bytes.

The result is one of *reproduced*, *reproduced under emulation*,
*diverged*, or *no verdict* (with the reason). It is saved as a report
under `~/.vaibify/reproductions/reports/`. The report is yours, not the
author's: nothing is written into their project.

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

### When a pinned toolchain version disappears

If a build stops with "the pinned compiler toolchain is no longer
available", the snapshot server may be unreachable, or vaibify's
pinned versions and their date have fallen out of step, which is for
its maintainers to fix. Never work around it by unpinning. To check published work you do not need to build anything:
`reproduce.sh` uses the archived image.

## Known limitations

- **Byte-exact means within one environment.** The same code on a
  different processor or library can give numerically near-identical
  but byte-different results. The archived image removes the library
  difference, and the determinism answers record the rest.
- **A tampered verifier cannot detect itself.** That is true of every
  verification tool, which is why each check can also be run by hand.
