# Release Notes

Notable changes, newest first. Entries describe what a researcher
observes, not internal mechanics; the architecture document carries
the reasoning.

## Unreleased

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
