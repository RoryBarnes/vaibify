# Release Notes

Notable changes, newest first. Entries describe what a researcher
observes, not internal mechanics; the architecture document carries
the reasoning.

## Unreleased

### Reproduce a published project: stage and validate from a URL or a clone

`vaibify reproduce --from <source>` starts from what a stranger has: an
`https://` or `ssh://` clone URL, or a clean clone under your home
directory. It clones the project in full, records the exact commit,
and validates the snapshot as a complete Level 3 project -- the
project file loads, the envelope pins a content digest, every manifest
entry matches the cloned bytes, nothing declared is unpinned, and any
image deposit on record covers the pinned image and its architecture.
It then prints what a rerun would use (the pinned image, the required
platform, whether an archived copy exists) and discards the snapshot.
Nothing is pulled, installed or run; a refusal names the first rule
that failed and the file that failed it. A dirty local clone is
refused, naming the paths, because a working tree with uncommitted
changes is not a published project. Acquiring the image and re-running
the snapshot in a shadow container follow in later releases.

### A container registry is optional, not a Level 3 requirement

For part of one day, Level 3 required the container image to be
pullable from a registry such as Docker Hub or GHCR. It no longer
does. Those are commercial services with no retention policy and no
preservation commitment, so a reproducibility claim cannot rest on
them, for the same reason a GitHub repository is not a long-term
archive. The PROOF tab now shows the registry copy as an optional row
that gates nothing, and the environment archive on Zenodo is the
image's only Level 3 criterion. `reproduce.sh` still pulls from the
registry first when one holds the image, because that is the fast
path, and falls back to the archived copy when it does not.

### Your container image can be archived, so a reproduction survives the registry

`reproduce.sh` opens by pulling the container image your results were
produced in. A digest names bytes somebody else is storing for you, and
the day that registry stops serving them -- a deleted tag, a retired
service, an account that lapsed -- the reproduction dies at the first
line and the environment is gone. The compiler, the exact numeric
library, the interpreter and every installed package go with it.

Vaibify can now deposit the image itself into Zenodo, under its own
DOI, and `reproduce.sh` falls back to it when the pull fails: it
follows the DOI, checks the download against the hash your project
recorded, and loads it. A measured example: a 3.5 GB image becomes a
821 MB deposit.

You are asked once, at Level 2, in the Artifacts section of the Project
block: deposit the image, point at a deposit that already holds it, or
decline. **Answering is the requirement -- declining passes it.** The
question is asked at Level 2 rather than offered as a button because
that is the last moment the image is certainly still on your machine; a
prune, a rebuild or a new laptop can take the opportunity away even
though you can change your answer at any time. Whether an archive
actually exists is a separate Level 3 criterion which never reads your
answer, so declining is a decision and not a lock: deposit later and
Level 3 opens.

One image used for several papers is one large upload plus a small
record per paper, not one upload per paper. The row says which platform
and which DOI it covers, never a bare tick, and it distinguishes "not
deposited" from "the deposit covers a different image" from "vaibify
could not reach Zenodo" -- the last of those is never shown as a
problem with your deposit, because nothing was compared.


### A passing test suite is no longer reported as a failure

A step whose tests all passed could still be reported as failing, with
`exit 1` and no explanation, on a project running outside a container.
The tests were fine; vaibify's own bookkeeping was not.

Each step's `tests/conftest.py` writes a small marker file after every
pytest run, so the dashboard can show test status however the tests
were invoked. Older generated copies of that file had the container's
project path written into them as a fixed value, so on a project
running directly on your machine the marker write failed -- and a
failure there ended the pytest session non-zero, on top of a run that
had just printed `1 passed`. Every test tier of every step failed the
same way, which made it look like the science had broken.

Three things changed. The marker write can no longer fail a test
session at all: if the marker cannot be written, the tests keep their
own verdict and the reason is printed. The file itself now finds the
project it lives in rather than being stamped with one path, so a
single copy is correct both inside a container and on your machine.
And these files are now re-checked when you press Run, not only when
you open the project -- previously, pulling a repository after opening
it left the old copy in place with nothing said, and reopening the
project did not help. If vaibify cannot bring one up to date it now
says so before the run instead of failing quietly.

If you have an existing project, no action is needed: the files are
replaced on your next run.
### Agent Council

Two or more model participants can now deliberate about a proposed
change to your project. They read and run your code against a
disposable copy of the container, challenge one another's proposals,
ask you when a choice cannot be settled from evidence, and produce a
written deliverable. A **planning** council produces an
implementation plan; an **implementation** council, convened from a
completed planning council, produces a reviewed patch. In neither
case does a participant hold a writable path to the live project —
a patch is text you apply by hand, or not at all.

Councils are container-only: every claim a council makes about
containment rests on creating a disposable container and proving it
gone afterwards, and a host project has none to create. See
[agentCouncil.md](agentCouncil.md).

### Host mode

Vaibify can now run a project **directly on your own machine**, with
no Docker container — point it at a local directory and the same
dashboard, pipeline steps, git badges, test markers, and terminal
work against your own filesystem. Host mode is intended for
experimentation and first contact: the container remains the
recommended path for real work, because only a container can carry
the isolation that the higher reproducibility levels certify
(Level 3 and Supervised mode are deliberately unavailable to host
projects).

**A weaker — and honest — quiescence claim.** For a container,
"released" means the container was stopped: nothing survives it. A
host project has no such boundary, so vaibify's claim on release is
deliberately weaker: **"every process vaibify started has exited."**
Processes vaibify can *see* are journaled at launch and proven dead
on release; a process that detached from its recorded session (for
example with `setsid`) cannot be seen, and vaibify reports the
project's quiescence as **unproven** and routes you to
`vaibify reconcile` rather than claiming quiet it cannot prove. The
dashboard's terminal says this out loud: every host terminal session
opens with a reminder that the shell runs on your own machine and
that processes you start can keep running after the session closes.

### The terminal serves host projects

The dashboard terminal now opens a real shell on your machine for a
host project, in the project directory, with working job control.
Using it costs the same thing it costs in a container: a project in
which a terminal has run reports quiescence unproven until
reconciliation settles it.

### Doctor knows about host projects

`vaibify doctor --project <name>` on a host project checks what
actually matters there — the registered directory still exists, and
`git`/`python3` are on `PATH` — and skips the Docker battery
entirely. Every doctor report now also names **which checkout's
code** answered the command, because an editable install binds the
`vaibify` command to one working tree permanently.
