# Release Notes

Notable changes, newest first. Entries describe what a researcher
observes, not internal mechanics; the architecture document carries
the reasoning.

## Unreleased

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
