# Vibe Coding Scientific Software

AI coding agents write functioning code at remarkable speed, and their
training on the scientific and statistical literature lets them quickly
"understand" a research problem. They are also stochastic: there is a
non-zero probability that their code is wrong or, worse, that critical
files are erased without authorization. A researcher may trust an agent
too much and not fully validate a numerical experiment, or trust it too
little and never submit accurate and compelling research. When agents
write the code, the bottleneck shifts from *writing* code to *verifying*
it.

This page describes the properties of scientific software development
that change when agents do the writing, and how vaibify addresses each
one. Safety, verification, and reproducibility appear to be the most
critical: nobody wants a tool that could cause permanent harm, and
scientists strive to publish results their colleagues can trust and
build upon. Most of these goals already align with the scientific method
and open-science practice, and many design constraints are unchanged,
such as how quantifiable, tractable, iterable, parallelizable, and
archivable a project is. What agents change is the methodology and the
priorities. The list is assuredly partial.

## Fundamental assumptions

Vaibify's animating assumption is:

> *Visually verifying the outputs of AI-written software, partnered with
> continuous integration, is a faster path to good science than
> human-only code and analysis.*

The value proposition follows: after an hour of setup, a researcher can
generate and verify byte-reproducible results in less time than without
agents. The bet is that verifying outputs is the part of
science that most benefits from human attention, and that delegating
the mechanical code-writing frees the researcher to ask the right
questions, inspect the results, and decide what to do next. The tagline
*Vibe boldly. Verify everything.* is the specification: bold vibing
happens inside a container the agent cannot escape, and verification
happens in a dashboard that makes the researcher's "yes, I looked at
this" a first-class artifact alongside the code and the data.

**A dashboard instead of an IDE.** Researchers prompt agents to write
the code, so an integrated development environment is unnecessary. A
minimal text editor handles small tweaks, such as changing an input
parameter, but **Open in VS Code** attaches the full IDE to the
container when one is wanted. The researcher's main work is examining
plots and data files to validate the results.

**The Linux file tree is ground truth.** Vaibify treats a Linux
directory and file structure as the authoritative record, and the output
of the Linux command line as always accurate. The dashboard reports what
the files say; it never substitutes its own memory for them.

**A fully scriptable core.** Although vaibify looks like a GUI
application, its core operates independently of the GUI. The `vaibify`
command line builds and starts environments, runs and tests steps, and
reports a project's PROOF level and its blockers (see the
[CLI Reference](cli.md)), so any part of the workflow can be
automated. Inside the container, a library of scripts gives agents the
same deterministic actions the dashboard's buttons perform.

**An always-current dashboard.** The researcher must see immediately
when something has gone amiss. At regular intervals vaibify checks file
timestamps and confirms that the dependencies between steps are intact.
The polling interval defaults to 5 seconds and is adjustable in the
settings. Checks against remote services run on their own, slower
schedule: vaibify re-verifies them every few hours while the hub runs, a
GitHub or Zenodo verdict older than a day is shown as unknown rather than
passing, and the researcher can verify on demand at any time. State is
never cached beyond its natural lifetime, and no step is marked passed
optimistically.

**A defensive security posture.** The container's user is unprivileged
(uid 1000) with no `sudo`. A container can be built with full network
isolation. Credentials are held by hardened mechanisms, such as
`gh auth`, the operating system's keyring, or Docker secrets, never in
environment variables. Each container has its own agent token, so one
vaibify container cannot act on another on the same host. See
[Security Model](security.md).

**"Verified" is not "accurate."** Vaibify monitors the contents of
files. It can show that results are self-consistent and match their
published copies; interpreting them remains the researcher's job.

Vibe coding scientific software is different than human-programmed code. The following present a list of features that are different and that motivate `vaibify` design choices.

## Containable

Agents that write code and manage directories can cause serious and
permanent harm to a computing environment through a poorly constructed
prompt or reckless behavior, and even harmless agents can introduce
security vulnerabilities. Such events appear rare with modern agents,
but the failure modes are catastrophic. Agents should therefore reach
only the files a research task needs, with no access to critical files
or machines. A contained agent also sees only the data and packages the
researcher approves, so it is less likely to search for spurious
information and insert dubious code.

**In vaibify.** Agents run inside Docker containers whose processes
cannot modify files on the host, and the container doubles as a
well-defined environment for reproducible research. Researchers add
software and data at build time, reach the web from inside, and let
agents modify files freely, which is why an agent such as Claude Code
can safely run long tasks with its permission prompts disabled. Much of
vaibify itself (the "back end") runs on the host, outside the agent's
reach. A setup wizard walks through the build: language support, agent
harnesses, storage, CPU count. A package found missing later can be
added with standard tools such as `pip`, and added to the configuration
for future builds.

Exploration needs no structure. A sandbox or toolkit environment, or a
Blank Project, offers a terminal and file tree with no required
outcomes; these provide containment only and sit below Level 1 of the
[PROOF Ladder](proofLadder.md). When the work looks promising,
**+ New Project** turns it into a Project with steps. Vaibify can also
run a project directly on the host ("host mode"), which skips the image
build but gives up containment; it suits machines dedicated to running
simulations, and a host project cannot reach Level 3. See
[Environments and Projects](environmentsAndProjects.md).

## Translatable

At its core, an agent maps natural-language prompts into source code
and shell scripts. Code is more predictable than natural language, which
is why prompts translate into code so effectively, but the translation
is approximate and its accuracy partly dictates the value of the
result. Large projects challenge an agent's context window, and some
styles are more efficient than others, such as naming variables with
full, descriptive words.

In an ideal project, a design document paired with the same agent
harness always reproduces the same source code, *and vice versa*. Strict
semantic rules improve translatability and speed development.
Agent-written code should follow style standards that minimize the
"perplexity" of its token patterns, that is, how unusual a sequence of
tokens is compared to the model's training corpus. Code that ignores
style invariants is harder for a model to parse and predict, so its
translation is more prone to error.

**In vaibify.** Vaibify does not impose a naming convention on
researchers' code. Researchers declare their rules in the project
context file (`.vaibify/AGENTS.md`), the standing instructions the
in-container agent reads; vaibify links it to the names the supported
agents look for at the repository root (`AGENTS.md`, `CLAUDE.md`,
`GEMINI.md`). Vaibify's own source follows Martin (2008) and Hunt and
Thomas (2020): readability independent of comments, short orthogonal
single-purpose functions (a guideline of roughly 20 to 30 lines), little
duplication, and modest, verifiable changes. Names use camel-case
Hungarian notation, function names begin with "f" and contain a verb,
and words are spelled out, so that functions read like paragraphs and
lines read like sentences.

## Localizable

An agent's limited context window is best used when concepts and scopes
are confined to small blocks of code, so the agent can grasp a
function's goal. Agent-assisted code should avoid global variables and
dependencies spread across files; where that is unavoidable, comments
should explain the dependency and its wider scope.

**In vaibify.** Localizability is hard to guarantee for an arbitrary
problem, so vaibify addresses it through its agent harness, the
natural-language instructions that accompany the container. The harness
directs agents to avoid global variables, keep functions short, build
modular architectures, and use names that are easy to search for. Since
harnesses are fallible, a researcher should periodically ask an agent
for an architectural review. Vaibify's own repository also enforces
localizability deterministically; see [Enforceable](#enforceable).

## Contestable

Several high-quality coding agents can debate ideas and review each
other's work, contesting each other's findings. The value is mixed:
some studies suggest that a well-formed prompt to one agent is as
effective as a poorly worded prompt to several. A discussion between
agents should do at least as well as one agent, but at a higher cost,
so it is best reserved for the hardest problems.

**In vaibify.** In the Agent Council, the researcher picks several
agents from the available models, designates one as the chairbot that
drafts the final report, writes the prompt, and sets limits such as the
minimum number of rounds. Each member works in its own disposable copy
of the container, so it can run scripts and move files without touching
the real one. Members analyze the repository independently, their
anonymous reports are critiqued, the chairbot synthesizes them, and the
Council iterates until the members agree, the round limit is reached,
or the chairbot needs more information from the researcher. A planning
Council produces a roadmap; an implementation Council writes code from
a plan and reviews it. The researcher can watch, answer clarifying
questions, and question the chairbot along the way. Councils are
container-only, token-intensive, and need disk and memory for the
copies, so they are not available for every project. A Council can also
sharpen a vague prompt ("make the results statistically robust") into a
quantifiable one, though judging whether it converged on a sound
approach remains the researcher's job. See [Agent Council](agentCouncil.md).

## Decomposable

Agents have limited context, so only projects that split into small
enough pieces are viable, and the dependencies between pieces must be
identifiable so that no step relies on unverified data. Decomposing a
problem into a pipeline is a classic technique, stated here for
completeness.

**In vaibify.** Each component is a **Step** and the full set is a
**Project**; where the divisions fall is the researcher's choice. Steps
are *automated* (inputs are on disk and a script runs unattended,
labeled A01, A02, ...) or *interactive* (the researcher decides specific
quantities, labeled I01, I02, ...). Each step can carry a short
description of its goal, which helps agents write better scripts and
helps readers see how it fits the Project. Steps also support
iteration: agents revise and rerun them at the researcher's direction
until the results are accepted. Steps run one at a time, so parallelism
lives inside a step; by default a container gets every host CPU but
one.

## Verifiable

Scientists must validate agent-assisted results before publishing.
Examining plots and raw data still works and should not be replaced. But
agents can unexpectedly modify data that was already verified: a script
in Step 2 creates a file used in Step 8, an agent later edits the Step 2
script without telling the researcher, and the Step 8 result is out of
date. The timing of output files must therefore be monitored, and unit
tests are a critical requirement, so that later modifications do not
break verified functionality. Verification may be replacing code
development as the bottleneck: thousands of lines of working code take
minutes to write, but days or weeks to validate as accurate and
sustainable.

**In vaibify.** A step counts toward Level 1 only when it has declared
its input data, its integrity, qualitative, and quantitative tests pass,
nothing has changed since verification, and the researcher has signed
off. Vaibify generates tests deterministically by default, watches every
declared input, script, output, and test file on each poll, and saves
plot standards so a restructured script can be compared against the
figure it should preserve. It detects cross-step dependencies by
scanning step scripts for files that other steps produce, and lets the
researcher declare dependencies no scanner could see. When an upstream
step changes, every downstream step is flagged. The per-step sign-off
keeps a human in the loop and quantizes the accountability, which
distinguishes vaibify from tools built for fully autonomous
investigation. The requirement rows are listed on the
[PROOF Ladder](proofLadder.md) page.

## Observable

Code should produce enough output, and only enough, for the outcome and
any errors to be interpreted unambiguously. This matters more for
agent-written code, because neither the researcher nor the agent is
likely to remember where a failure could occur. Software should exit
gracefully with a message that guides humans and agents to the
offending function.

**In vaibify.** The dashboard makes dependency and output problems
visible at a glance. Every warning glyph names its reason and remedy,
file names change style when a file is missing or stale, and a failed
step's exit code is explained in plain language where it has a standard
meaning. The **?** Help panel explains the status vocabulary.

## Reproducible

All scientific work should strive for full reproducibility, and
agent-assisted work makes the goal more urgent. The crucial issue is
stochasticity: quantitative output should depend as little as possible
on a particular model's particular output. A step's unit tests, for
example, should come from deterministic processing of its results, such
as a Python script, not from a prompt; deterministic methods are always
preferable to prompting. Data, scripts, and output should be tracked and
available so that others can recover the claimed results independently
of the AI and the original authors, and the use of AI, including the
specific tasks and models, should be reported.

**In vaibify.** Vaibify integrates with GitHub, Overleaf, arXiv, and
Zenodo. The researcher chooses which files to publish, and vaibify
monitors the SHA-256 hashes of local files and their remote copies, so
anyone can confirm that the files on disk are the ones in the paper, on
GitHub, and in the archive, and an agent cannot silently change an
archived file. Every AI model used is declared with its vendor, model
identifier, and dates of use. See
[Connecting to External Resources](externalResources.md) and
[Reproducibility](reproducibility.md).

## Falsifiable

Unit tests confirm a project's behavior, but cannot show on their own
that they catch incorrect behavior. Each test is a hypothesis that it
catches a failure, and a deliberate breakage of the code is a
"falsification test" of that hypothesis. The approach is usually called
mutation testing (DeMillo et al. 1978): change the code, for example by
flipping a `<` to a `>`, and check that a test fails. Because the
changes are deliberate, "falsification test" is the more accurate name.
It matters most when a single agent writes the code, writes the tests,
and judges the suite's success.

Packages such as `mutmut` and `cosmic-ray` automate the tedium.
Falsification can be ambiguous, since broken code sometimes still
produces the expected outcome ("equivalent mutants"), but the larger the
falsifiable fraction of the code, the more likely it works as intended.
Parallel runs are hard, because mutations on the same branch interfere,
so falsification is likely the slowest part of a comprehensive suite.

**In vaibify.** The harness instructs agents to write falsifiable code
wherever possible. Each deterministic pure-Python step offers **Check
test teeth**, which mutates the step's code, runs its quantitative tests,
and records the fraction of mutants caught. The result describes the
tests' sensitivity, never the result's accuracy, and it never gates a
level, because equivalent mutants make a hard pass/fail dishonest.
Vaibify itself was developed this way, and its falsification tests
regularly reveal mistakes the unit tests miss. See
[Testing Model](testing.md).

## Enforceable

All code embodies design decisions, and well-written code enforces them
for modularity, readability, and consistency. Agents' context windows
are short compared with legacy codebases, so a fresh agent will not know
the design choices unless they are documented *and* enforced.
Agent-friendly code should specify its architecture, source style, and
network connections (for example, whether file paths are relative or
absolute), and include tests that hold the code to these invariants.
Then no agent can break an established design decision, even when its
output is correct and the unit and falsification tests pass.

**In vaibify.** Design decisions in a researcher's own code belong to
the researcher, so vaibify does not enforce them. Vaibify's repository
enforces its own through continuous integration, and those tests can
serve as a template. Three classes of invariants run on every change:
architectural (how the code treats the file tree and how the back end
connects to the front end), security (how the package handles
credentials, paths, and remote services), and style (Hungarian
notation, descriptive names, and function size). See
[Testing Model](testing.md).

## Replayable

The ideal project lets future researchers reconstruct both the code and
the results. It publishes its standing agent instructions (for example,
`AGENTS.md`) and the transcript of the conversation with the agents,
and records the model used with each prompt; open-weight models are
preferable, so that their biases can also be probed. A transcript lets
others search the original prompts for misconceptions that introduced
incorrect methods, teaches newcomers which prompt styles work, and
builds the transparency that makes agent-written code trustworthy.
Combined with the git history, a timestamped prompt history explains
the motivation behind each decision. Some decisions are made by hand,
outside the transcript, so true replayability also needs a
"supervision log" that records every file change with a timestamp,
regardless of who or what made it.

A code that adopted agents partway through should acknowledge it and
record when; agent co-authored commits and stored chat histories can
help reconstruct the date.

**In vaibify.** Full replayability belongs to Level 4 of the PROOF
Ladder, which vaibify does not implement (yet). But a transcript must exist
*before* Level 1 for a project ever to reach Level 4, so vaibify
collects the evidence from the start. The opt-in **Prompt Record**
copies the session transcripts of Claude Code running in the container
into the repository (other agents' sessions are not captured), redacts secrets at capture time, and hash-chains the
captures so that editing or removing one breaks the chain. **Supervised
mode** adds the supervision log: every repository change must be
attributable to a recorded action, and unexplained changes are flagged
permanently. Both are tracked on the Replay axis, described on the
[PROOF Ladder](proofLadder.md) page.

## Synthesizing the properties

These properties cover the scientific method from conception through
archiving to reproduction, for research developed on a local machine and
stored remotely. They deliberately do not address accuracy; their scope
is the development of software and the verification that results are
reproducible.

These properties guide a researcher in bringing agents into research, but
they are not enough alone. Without clear rules and procedures for
development and reproducibility, a skeptical reader of a manuscript
describing agent-assisted research would reasonably dimiss it if the
authors did not fully explain how the AI was used. The
[PROOF Ladder](proofLadder.md) described in the next section is a scale for assessing exactly that.

For how vaibify's own agent guide applies the Localizable and
Enforceable properties, see [For Agents](forAgents.md).
