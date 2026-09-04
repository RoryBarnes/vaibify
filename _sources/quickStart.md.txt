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

If you want to see it actually reproduce, install Docker and run:

```bash
vaibify reproduce --repo . --rerun
```

which rebuilds the pinned environment, re-runs the workflow inside a
disposable copy of it, and re-hashes every artefact against the
manifest — leaving your own files untouched.

## 7. Where to next

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
