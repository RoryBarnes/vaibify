# Philosophy

This document explains why Vaibify exists and what it believes about
AI-assisted scientific computing. For the implementation that flows
from these beliefs, see [architecture.md](architecture.md). For the
methodology behind the project's own agent documentation, see
[vibeCoding.md](vibeCoding.md).

## The problem

AI coding agents are remarkably capable and remarkably dangerous. An
agent running loose in a researcher's home directory can read SSH
keys, overwrite datasets built over months, or push a half-finished
branch to a shared repository. Treating a scientific workstation as
an agent's playground places years of careful work at risk for a few
hours of convenience. Vaibify's first job is to contain AI agents to
a secure environment.

## The opportunity

Once contained, you can safely allow AI coding agents to freely build
code, monitor long-running jobs, and generate figures and TeX for
manuscripts. Experiment with confidence and push the limits of the
combined abilities of your brain and AI.

Vaibify is based on the assumption that code is written by agents
now. While access to an IDE is included, vaibify is optimized for
command-line agents like Claude Code. The command line is ground
truth for the operating system. It will always be the fundamental
representation of a filesystem.

## The central assumption

Agent-written code must pass different validation tests than
human-written code, and AI-assisted science must pass higher
thresholds than human-only science. The central assumption of vaibify
is:

> *Visually verifying the outputs of AI-written software is a faster
> and more reliable path to good science than analyzing data with
> only human-written code.*

The value proposition follows: after an hour of setup, users can
obtain better results in a shorter amount of time than they could
working from a human-only codebase.

This is a falsifiable claim. If it turns out that code written only
by humans reliably outperforms code written by agents and verified
by humans, the whole architecture of vaibify is wasted effort. The
bet is that verification of outputs is the part of science that
genuinely benefits from human attention, and that delegating the
mechanical code-writing to agents frees a researcher to spend more
time on the parts that actually matter: asking the right questions,
inspecting the results, and deciding what to do next.

## How the results can be trusted

The same way they always have been: through visually inspecting
scripts, plots, and even raw data. Vaibify is more explicit about
the practice because an agent has an outsized capacity to veer off
course and change scripts or files that apply to other steps,
silently breaking downstream work.

Three mechanisms operate together:

- **Rigorous testing of produced datasets.** Every step produces
  outputs that are checked against deterministic assertions at test
  time. When a file changes, its associated tests become stale and
  re-run.
- **Dependency monitoring.** When an upstream step is modified, every
  downstream step is flagged as potentially invalidated. The
  researcher sees which steps need attention.
- **Per-step sign-off by the user.** After inspecting the outputs of
  a step, the researcher marks it verified. That sign-off is a
  first-class artifact alongside the code and the data, not a memory
  in somebody's head.

The three together close the loop: an AI writes the code, the
container constrains where it can reach, the test suite catches
regressions, dependency tracking surfaces knock-on effects, and the
researcher signs off on each step only after looking at it.

## AI-assisted development as practice

Vaibify is developed primarily through AI-assisted coding with
Claude Code, under human review and direction. This is not
incidental to the project — it is the practice the tool was built
to enable. Vaibify's own codebase is one of the first tests of the
central assumption above.

The structure of the documentation in this repository mirrors the
same principles the tool asks researchers to adopt: separate what
can be tested from what cannot, never trust what can drift, and
make the human's verification role explicit. [vibeCoding.md](vibeCoding.md)
describes this methodology in detail so that other scientific
software projects can adopt the same approach.

## The tagline is the architecture spec

The tagline *"Vibe boldly. Verify everything."* is not marketing.
It is the architectural specification for the entire project. Bold
vibing happens inside a Docker container the agent cannot escape.
Verification happens in a browser dashboard that makes the
researcher's "yes, I looked at this" a first-class artifact
alongside the code and the data. Every design choice downstream —
the containerization model, the verification state machine, the
polling cadence, the rule that the dashboard never lies — falls out
of taking both halves of the tagline seriously at the same time.

For how that specification becomes code, read
[architecture.md](architecture.md) next.
