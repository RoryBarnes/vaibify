# QuickStart

In about fifteen minutes, with no Docker and nothing to build, you will
take a published scientific project, delete its results, regenerate them
on your own machine, and check the new bytes against the ones its author
recorded.

That last step is the interesting one, and not because it will succeed.

## 1. Install

You need Python 3.9 or later.

```bash
pip install vaibify
vaibify
```

Run `vaibify` with no arguments in any directory to start the **hub** —
a local web server on `http://127.0.0.1:8050` — in your browser. You
should see the Vaibify logo, the tagline, and an empty **Containers**
list.

Vaibify's full purpose is running scientific work inside containers, and
for that you will want Docker (or Colima on macOS) eventually — the
[install guide](install.md) covers it. You do not need it for this
walkthrough, and installing it is not a quick start. Everything below
runs directly on your machine in **host mode**, which trades containment
for immediacy: commands run with your user's authority on your real
files, exactly as if you had typed them in a terminal, because that is
what is happening. Vaibify says so when you enter a host project and
again at the top of every host terminal session.

## 2. Get the example project

```bash
git clone https://github.com/RoryBarnes/aigreenhouse.git
cd aigreenhouse
pip install -r .vaibify/requirements.txt
```

This is a real, finished analysis: how long until the waste heat from AI
data centres becomes large enough to matter for a planet's climate. Two
computational steps, a figure from each, three tiers of tests, and a
declaration of which AI models helped write it. It is published at PROOF
Level 3, which means its author committed a manifest of every artefact's
SHA-256 hash along with the pinned environment that produced them.

Back in the hub, click the **+** next to *Containers*, choose the option
that adds an existing directory, point it at your clone, and choose to
run it **on this machine** rather than in a container. There is no image
to build; the dashboard opens in seconds.

You are looking at somebody else's finished work — and the header says
**Level 0**, with most requirements unmet. That is correct, and it is
the first thing worth understanding about vaibify.

A PROOF level is not a badge the author publishes. It is a verdict your
copy computes, on your machine, from evidence available to *you*. Open
the **PROOF** tab and the two computational steps both say
`user-not-approved`: nobody on this machine has run them or looked at
what they produced. The author's approvals live in `.vaibify/state.json`,
which vaibify deliberately keeps out of git — "I examined this figure and
I stand behind it" is a statement by a person, and it would be worth
nothing if it copied itself to strangers. The same is true of the
records of their GitHub and Zenodo checks, and of their rebuild
attestation.

So a published project does not arrive pre-trusted. It arrives with
everything you need to decide for yourself, which is the entire point.

## 3. Check the archive before touching it

From the **Run** menu, choose **Check Files Against Manifest**.

It re-hashes every file pinned in `MANIFEST.sha256` and reports that all
of them match. This is the one claim that *does* travel, because a hash
is a property of the bytes rather than of anyone's judgement: you have
confirmed that the files in your clone are byte-for-byte the files the
author committed. Note what it is not — a statement about whether the
science is right, or whether those files can be produced again. It is
the archive's integrity, and nothing more.

That "all match" holds for a *fresh* clone. Once you have run the
pipeline yourself, five of those twenty-four files are ones your
machine produced, and the check will start reporting them as different.
That is the check working, and [section 6](#6-now-check-the-bytes) is
about why. If you are returning to a clone you have already run —
re-testing, or picking this up a second time — `git checkout .`
restores the author's bytes and the check passes again.

The same check is available from the command line, and there it will
also tell you the reproducibility envelope is coherent:

```bash
vaibify reproduce --repo . --skip-tier 2 --skip-tier 3
```

Tiers 2 and 3 are skipped because they install a pinned dependency set
and pull a container image; neither is needed to answer the question in
front of you. (If you do run tier 2 later, do it inside a virtual
environment — it installs an exact, hash-pinned dependency set into
whatever Python is active.)

## 4. Delete the results

From the **Run** menu, choose **Clean Outputs**, and confirm.

Every automatic step's data files and figures are deleted and every
verification mark resets to untested. The dashboard goes grey. The
figure viewers empty. The AI Declaration step keeps its content, because
a person wrote that and no amount of re-running would produce it again.

Nothing is lost: `git status` now lists the deleted files, and `git
checkout .` would bring them straight back. The point of stopping here
is that an empty dashboard is *evidence*. Whatever appears next was
produced by your machine, not shipped in the repository — and unlike
the level, which was never the author's to give you, the files were.

## 5. Run the pipeline

From the **Run** menu, choose **Run All Steps**.

Each step runs its data commands, then its tests, then its plots, and
turns amber as it runs and green as it finishes. So the three test
tiers run as part of the step — the integrity tests confirm the output
files have the expected structure, and the quantitative tests confirm
the numbers land inside the author's recorded tolerances. (The Run
menu's **Run All Unit Tests** re-runs only the tests, when you want
them without redoing the data and the plots.)

Click a step's figure in the viewing window above the terminal to
display it. The two plots are regenerated from scratch, in order, with
the second step consuming the first step's output through a declared
dependency rather than a hardcoded path.

Now approve each step, and the header moves from Level 0 to **Level 1**
— every step ran, every output was inspected, every test passed, and you
signed off. You did not inherit that level; you earned it, on this
machine, in about two minutes. That is as far as a host project goes:
Level 2 asks whether the outputs are published and verified against
GitHub and Zenodo, and Level 3 is *defined* by a pinned container image,
so a project running directly on your machine reports a single honest
blocker saying so rather than offering you work that cannot help.

### Doing the same thing with an agent

If you have an AI coding agent available, the terminal at the bottom of
the dashboard is a real shell in the project directory, and vaibify
exposes its own actions to an agent running there through the
`vaibify-do` command. Asking the agent to "clean the outputs and re-run
every step" performs the same operations as the menu items above, and
the dashboard follows along — the agent is not typing shell commands
behind vaibify's back, it is calling the same endpoints your clicks
call. The [agent action catalog](dashboard.md#agent-actions) lists what
it can and cannot do; destructive operations like cleaning are
researcher-only by design.

## 6. Now check the bytes

From the **Run** menu, choose **Check Files Against Manifest** again.

It will fail.

Not all of it — but the numbers you just regenerated are not identical
to the numbers the author published, and neither are the figures. This
is the most useful thing in the walkthrough, so it is worth being
precise about what went wrong, because *nothing* did:

- **The fitted values differ in their last digits.** A least-squares fit
  is a sequence of floating-point operations, and different BLAS/LAPACK
  builds order and vectorize them differently. The science is identical
  to twelve significant figures. The bytes are not.
- **The figures differ by more.** Matplotlib stamps its own version into
  every PNG it writes, so a different matplotlib version guarantees
  different bytes before you even reach font rendering, which also
  differs by platform.

Your run passed every scientific test the project defines and still did
not reproduce it byte-for-byte. Those are different claims, and vaibify
keeps them apart on purpose. "The tests pass" says the result is
consistent with what the author asserted. "The hashes match" says a
stranger re-executing this work would obtain the identical artefact —
which is what a reader must be able to check if the published numbers
are to mean anything on their own.

Getting the second claim requires pinning the environment, not just the
code: a specific image, specific library versions, a recorded thread
count. That is what PROOF Level 3 is, it is why it needs a container,
and it is why this project ships a `Dockerfile`, a `requirements.lock`
with hash pins, and an `environment.json` naming an image digest.

So the next step is to give this project the environment it is
missing, which is what the rest of this walkthrough does. If you would
rather do it from the command line, the equivalent is:

```bash
vaibify reproduce --repo . --rerun
```

which rebuilds the pinned environment, re-runs the workflow inside a
disposable copy of it, and re-hashes every artefact against the
manifest — leaving your own files untouched.

## 7. Containerize the same project

The blocker at the bottom of the PROOF tab is the honest one: Level 3
is *defined* by a pinned container image, and there isn't one. You can
lift that without starting over, and without moving a single file.

Go back to the Environments hub, open the menu on this project's tile,
and choose **Containerize…**. Confirm with **Convert and build**.

This does not create a second project. Your clone stays exactly where
it is — the same directory, the same git history, the same outputs you
just produced. What changes is how vaibify runs the steps: the project
is re-registered under a Docker-safe name, an image is built from the
`Dockerfile` and the hash-pinned `requirements.lock` this repository
ships, and from then on every command runs inside a container built
from that image rather than against whatever Python happens to be on
your PATH.

That difference is the entire point of the level you are reaching for.
Until now "it ran on my machine" has been doing real work in your
favour — your numpy, your matplotlib, your interpreter. A stranger has
none of those. Pinning the environment is what converts *your* result
into one somebody else can obtain.

A few things to know before you click:

- **The project must not be open anywhere else.** If it is open in the
  tab you are clicking from, vaibify closes it for you. A session in
  another browser or on another machine refuses the conversion instead,
  because the conversion renames the key that the project's lock,
  lease, and journal all hang from.
- **The build takes minutes, not seconds.** It installs the pinned
  dependency set. On macOS, prefix long commands with `caffeinate -s`
  (see the [install guide](install.md#docker-on-macos)) — a sleeping
  Colima VM corrupts a build in progress.
- **A failed build does not put you back where you started.** It leaves
  a registered container that has not been built yet, which is the
  normal state of any newly created container project. Fix the cause
  and build again; you have not lost the host project's work, because
  there was never a copy to lose.

When the build finishes, run the pipeline again. The steps do the same
things and produce the same figures, but now they do it in the pinned
environment — and the PROOF tab's Level 3 row stops saying the project
has no image and starts asking the questions it is really about:
whether the environment is recorded, whether the reproducibility rules
have been answered, and whether a rebuild from that image reproduces
the outputs you just made.

That last one is worth doing at least once. **Verify** on the Level 3
row creates a *shadow* container from the image digest your project
pins, copies the repository into it, runs the whole pipeline there, and
compares the results against your files — then destroys the shadow. It
does not touch your outputs. It is the difference between believing
your work reproduces and having watched it happen.

## 8. Where to next

- **[The three templates: sandbox, toolkit, workflow](templates.md)** —
  starting your own project rather than driving someone else's.
- **[The dashboard tour](dashboard.md)** — every panel, the status
  colours, and the verification state machine.
- **[The reproducibility ladder](reproducibility.md)** — what Levels 1
  through 3 each certify, and what none of them do.
- **[Security model](security.md)** — what a container protects against
  and what host mode does not. Worth reading before you let an agent
  write code anywhere.
- **[Install guide](install.md)** — Docker and Colima, plus
  platform-specific troubleshooting.
- **[Command line interface](cli.md)** — everything above, scriptable.

A note on what you will see in `git status` afterwards: opening a
project can refresh the small `conftest.py` that vaibify installs in
each step's `tests/` directory, because it records where the project
repository sits on the machine reading it. That file is vaibify's test
harness, not part of the analysis, and it is not pinned in the
manifest.
