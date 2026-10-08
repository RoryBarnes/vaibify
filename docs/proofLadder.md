# The PROOF Ladder

The PROOF Ladder is a scale of ever-stricter standards of confidence
that AI agents have not inadvertently modified a project's code or data.
It measures how well a data-science project controlled the stochastic
behavior of language models. The name comes from the pillars a result
must rest on to be trusted by someone who was not there:
**P**rovenance, **R**eproducibility, **O**penness, **O**versight, and
**F**alsifiability.

The levels are somewhat arbitrary, but they draw heavily on fields in
which provenance and continuous confirmation of authenticity
("custody") are paramount: the Supply-chain Levels for Software
Artifacts (SLSA) framework, the Association for Computing Machinery's
artifact badging, and artwork certification. These resemble the norms,
or aspirations, of most scientific studies, so the approach is broadly
applicable.

**The ladder measures custody, not correctness.** It is *not* intended
to confirm that results accurately represent reality. It only addresses
how well the researchers monitored the role of AI in the research.

## The levels of the PROOF Ladder

SLSA, the closest model, is a community checklist for monitoring the
software behind supply chains, so that vendors can confirm their
software has not been modified by malicious actors. Its source-code
track has four levels: use of version control; a changelog that details
provenance; continuous technical control of the repository, enforcing
its rules; and independent review by multiple trusted people. The
analogy for science is clear: researchers seek to ensure that their
software and data are free of corruption introduced by AI agents.

Each level includes everything below it. Each also states what it does
*not* establish, because a level's limits matter as much as its claims.

| Level | Name | Adds | Does not establish |
|---|---|---|---|
| 1 | Self-Consistent | Version control; every step tested and approved | Anything outside the researcher's own machine |
| 2 | Published | Public release of data and a declaration of AI use | How others can exactly reproduce the results |
| 3 | Reproducible | Instructions and environment for a byte-identical rerun, attested by the authors | That inputs trace to raw data, or a public record of all AI assistance |
| 4 | Traceable | Public chain of events from raw data to publication, including prompts | That a different AI would reach the same result |
| 5 | Regenerated | The same science reproduced with a different AI | That anyone besides the researcher has done so |
| 6 | Attested | Independent, publicly verifiable attestations | That the result describes the universe correctly |

Given the pace of model and harness development, this scale may need
revision, but it gives a vocabulary for how trustworthy an
agent-assisted result is.

### Level 1: Self-Consistent

The lowest level is a project whose code and data are locally
consistent and under version control. Outcomes are validated by a clear
set of rules, such as unit tests that cover the large majority of the
code. A project at Level 1 has crossed an important threshold: the data
are analyzed, the figures are approaching camera-ready, and the
researchers believe they have controlled the agents well enough that
the results could be worth sharing. The project is, however, invisible
to the rest of the world, and so by definition it is *not* a
reproducible scientific result.

### Level 2: Published

The public release of the data, together with a clear and complete
declaration of how AI was used and controlled, marks Level 2. Maturing
projects connect to remote resources both to keep the work safe and to
prepare a publication, and those resources are natural places to
release data products and the statement of AI use. A project that aims
for Level 5 should also publish, at this stage, the tolerance it will
accept for each output quantity, so that the threshold is fixed before
any regeneration exists. Publication does *not* explain how other
researchers can exactly reproduce the results.

### Level 3: Reproducible

Level 3 adds published instructions for reproducing the results at the
bit level, and an attestation that the original authors have performed
that reproduction. A Level 3 product collects all dependencies in a
clean, well-defined environment, regenerates the results exactly as in
the original experiment, and includes hashes, such as SHA-256, that let
others verify that every byte of their recreation matches. A
reproducible result does *not* have to contain the raw observational
data behind it, nor a public record of how the data and software were
assisted by AI.

### Level 4: Traceable

Level 4 adds a complete public record of the sequence of events, from
the first commit to the curation of a long-term archive: the raw data,
the analysis scripts, and the plotting scripts behind every published
figure. Every artifact must have a public changelog, meaning both the
git history and the transcript of AI prompts, which together (almost)
prove the chain from an artifact's creation to the published
interpretation. The result traces back to all raw observational data,
including calibration and every processing step. The Level 2
publication must include the prompt transcript and a "supervision log"
of what the researcher did independently of AI.

Level 4 must be planned when the project is conceived: the transcript
has to exist before Level 1, so a project that did not record it cannot
reach Level 4 later. A traceable project lets others recreate the chain
of events, but does *not* show that different AI agents would produce
the same results.

### Level 5: Regenerated

Level 5 recreates the results with at least one different AI agent and
brings that second analysis to Level 4. Regeneration is not blindly
feeding the original prompts to another agent: the researcher is an
active participant and may have changed files by hand. Instead, the
researcher uses the Level 2 publication as an implementation plan and
collaborates with agents to build an entirely separate pipeline.

The two results are very unlikely to match at the bit level, because of
order-of-operations differences, so they must instead be judged
consistent with the original publication against the tolerances
published at Level 2. Choosing those ranges is a judgment call, but
publishing them in advance exposes it to peer review and makes Level 5
a quantitative assessment. Each artifact must independently satisfy
Level 3. "A different model" means a different model identifier, a
floor rather than a guarantee, since models from one vendor may share
training data. A regenerated result shows that one researcher working
with several agents obtains scientifically equivalent results; it does
*not* show that independent researchers have done the same.

### Level 6: Attested

The highest level adds independent, publicly verifiable attestations by
trusted parties that they regenerated the results with multiple agents
and obtained values within the tolerances of the Level 2 publication.
The attestations must be verifiable in public, for example through
tests run on a public repository or cryptographic links between
witnesses' identities (such as ORCID) and the DOIs of the archives;
Zenodo and tools such as `in-toto` support this. Because the attestors
never saw the original process, Level 6 is where independence from the
researcher arrives.

Level 6 does *not* imply that the results correctly describe the
universe. Corrupt observations, conceptual misunderstandings, and bugs
that every agent introduces identically all survive it, and scientists
can interpret the same results differently. The scientific method must
continue with independent experiments. Level 6 is a high bar that
demands real community effort, so in the near term it will likely be
reserved for high-profile claims, such as the discovery of life on
another world. Whether reaching it is more efficient than having humans
rebuild a result from scratch remains an open question.

## What the ladder is and is not

The ladder is a vocabulary for stating, and checking, the rigor with
which a computational result was produced. It is independent of
vaibify: any tool that implements container packaging, hash checks
against public authorities, reproducible builds, and attestation logs
could claim levels on it.

Two properties are deliberately kept off the ladder. **Test
meaningfulness**, whether a project's tests are strong enough to catch
subtle errors, is a property of the tests that a project should have at
every level. **Physical validation**, whether a result respects
conservation laws, symmetries, or limiting cases, is a claim about the
science, not about the bits. Results can be perfectly reproducible and
scientifically wrong.

## Where vaibify sits

Vaibify implements **Levels 1, 2, and 3** and stops there by design.
Level 4 is a phase change: the analysis must begin from raw
observational data, which most projects do not need, and Levels 4 to 6
depend on prompt publication, cross-agent regeneration, and third-party
attestation that lie outside a local-first tool. Vaibify does record
the AI-provenance evidence that Level 4 will need; see
[The Replay axis](#the-replay-axis).

**Level 3 requires a containerized project.** The level is defined by a
pinned container image and an in-container rerun. A host-mode project,
whose pipeline runs directly on the researcher's machine, reaches
Level 2 and stops; its Level 3 shows a single `host-mode` blocker.

Two design choices shape the implementation. Vaibify is **local-first**:
the project stays on the researcher's own disk and under their control,
rather than being uploaded to a hosted platform. And **agent output
receives no special privilege**: it passes the same gates as human
output, in either direction. Vaibify is not a workflow manager (it uses
a minimal JSON pipeline description), not an AI agent (it writes no code
and drafts no papers), not a hosted reproducibility platform, and not a
cryptographic attestation service.

## Ascending the ladder in vaibify

A researcher can explore in a sandbox, toolkit, or Blank Project, which
sit below Level 1, and create a Project when the work looks promising,
or start in a Project from the beginning. Project mode is for numerical
experiments expected to become part of a document: clearly defined
steps that produce data products requiring careful monitoring. As a
project matures, vaibify shows what the next level requires, and the
dashboard's theme follows the level the project has reached.

Every requirement has a **scope**. A *Project* requirement applies to
the whole experiment and lives in the Main tab's **Project** block. A
*Step* requirement must be met by each step and appears in that step's
expanded view, under its Level 1, Level 2, and Level 3 sections; the ⓘ
beside each section lists every requirement with its live mark. Level 1
is earned mostly step by step. Above Level 1 the requirements are
mostly project-wide, and the focus shifts from science to bookkeeping:
disseminating the results and making them reproducible.

The labels below are the ones the dashboard shows. Where the PROOF tab
and the Project block name the same requirement differently, both are
given.

### Level 1 requirements

| Requirement | Scope | Meaning |
|---|---|---|
| Project repository (Project block: **Git enabled**) | Project | The project and every file it touches live in a git repository. |
| Every step self-consistent | Project | Every step meets the step requirements below. |
| Input data declared | Step | The step lists its raw input files, or declares it needs none. The Project block shows the same row for all steps, with a one-click declaration for steps that have no inputs. |
| Unit tests pass; Integrity tests pass; Qualitative tests pass; Quantitative tests pass | Step | One row per test category the step has. |
| Your sign-off recorded | Step | The researcher inspected the outputs and approved the step. |
| Nothing changed since verification | Step | No script, output, or upstream step changed after the step's tests or sign-off. |
| Project context file (optional) | Project | `.vaibify/AGENTS.md` records the in-container agent's standing instructions. Never blocks a level. |

An AI declaration step has no Level 1 requirements; its sign-off
belongs to Level 2.

### Level 2 requirements

| Requirement | Scope | Meaning |
|---|---|---|
| GitHub mirror | Project | Every published file matches the GitHub repository at a recently verified commit. |
| Zenodo deposit (Project block: **Zenodo archive**) | Project | Published files match a Zenodo deposit with a DOI. |
| arXiv manuscript (Project block: **arXiv submission**) | Project | Opt-in: checked only when an arXiv submission is recorded. |
| AI model declared (Project block: **AI models**) | Project | Every model used is declared with vendor, model ID, and dates of use; open-weight models also give their weights source and revision hash. Undeclared is the only failing state. |
| Personal AI Configuration answered (Project block: **Personal AI Configuration**) | Project | See below. |
| Environment archive | Project | Asked once the environment snapshot pins a container image: deposit the image in a permanent archive, point at a deposit that holds it, or decline. Any answer meets it. |
| Supervised mode (optional) | Project | When enabled, every repository change is attributed to a recorded action. Never blocks a level. |
| Prompt Record (optional) | Project | When enabled, agent transcripts are captured and the first capture approved. Never blocks a level. |
| Published files match the GitHub mirror | Step | The step's declared files match GitHub. |
| Published files match the Zenodo deposit | Step | The step's declared files match Zenodo. |
| Manuscript figures frozen in Overleaf | Step | Shown only when an Overleaf project is bound and the step has plot files. |
| AI declaration signed off | Step | Only on the AI declaration step: the researcher has signed the declaration of AI use. If the project has no such step, an **Add AI declaration step** row appears at the foot of the step list. |

**Personal AI Configuration** asks one question: did the researcher's
own private, host-side agent setup (a global instruction file, personal
skills, memory, hooks) govern this work, and is it being disclosed?
*Answering is the criterion. Disclosure is never required.* All three
answers pass: **none** ("No personal AI configuration exists"),
**declared-private** ("Exists — content withheld"), and **included**
("Included in the project repository"). A declared-private answer with
nothing further is complete. Only an unanswered question fails. It is
not a file the researcher must produce. With a declared-private answer,
the researcher may optionally record a hash commitment to a file, which
stores only a label, the file's SHA-256, its size, and the date.

Remote checks are cached. A GitHub or Zenodo verdict older than a day
reads as unknown (**?**) rather than passing; **Verify now** refreshes
it, and vaibify also re-verifies on a schedule while the hub runs.

### Level 3 requirements

| Requirement | Scope | Meaning |
|---|---|---|
| Manifest complete | Project | `MANIFEST.sha256` pins the hash of every declared artifact, script, and test. |
| Manifest matches the files | Project | Every hash the manifest pins is the file's current bytes. |
| Dependency lock | Project | `requirements.lock` pins every Python dependency by version with hashes. |
| Environment snapshot | Project | The exact container image digest and system toolchain are recorded. |
| Dockerfile pinned | Project | The Dockerfile builds from an exact base-image digest. |
| Reproduce script | Project | `reproduce.sh` at the repository root reruns the project, and its hash is in the manifest. |
| Reproduce script is current | Project | `reproduce.sh` matches what vaibify would generate for the workflow. |
| Determinism declared | Project | The researcher has stated how exactly a rerun must match: last-digit numeric differences, thread count, and the Intel MKL setting. |
| Software declared | Project | Standalone binaries are declared with version and hash captured, or explicitly waived. |
| Envelope published | Project | The envelope files (reproduce script, manifest, lock, snapshot, Dockerfile) match the copies on GitHub. |
| Envelope archived | Project | The same envelope files are in the Zenodo archive. |
| Environment archived | Project | The container image is in a permanent archive under its own DOI, for the image and platform the envelope pins. |
| Archives are permanent | Project | Neither archive is a Zenodo sandbox deposit. |
| Image on a registry (optional) | Project | The pinned image is pullable from a registry such as Docker Hub. Never blocks a level. |
| Rebuild attestation | Project | A full rebuild reproduced the outputs with matching hashes against the current manifest. |
| Outputs pinned in the manifest | Step | The step's declared files are in the manifest. |
| Scripts match the manifest | Step | The step's scripts are unchanged since the manifest was written. |
| Randomness seeded or declared | Step | A step that uses randomness seeds it or declares it. |
| Invoked binaries declared; Binary versions captured; Binaries match their captured hashes | Step | For steps that run standalone programs. |

Step rows appear only where they apply. The Level 3 gate also requires
that the Zenodo archive carry a rebuild attestation covering its own
manifest; that requirement is reported as a Level 3 blocker rather than
as a separate PROOF-tab row.

Because Zenodo versions are immutable, any change to the envelope drops
**Envelope archived** until the next published deposit version. That is
expected: Level 3 describes a published release, not the working tree.

### Reaching Level 3

Level 3 needs the reproducibility envelope (manifest, dependency lock,
environment snapshot, pinned Dockerfile, and `reproduce.sh`), published
to GitHub and archived on Zenodo; the container image itself archived,
because a list of the environment's contents is not enough to rebuild
it byte for byte; the determinism and software declarations; and an
attestation that the researcher has rebuilt the results in a copy of
the container and confirmed that every hash matches. The order of the
final steps matters: for example, `reproduce.sh` must be generated
before the manifest records its hash. See
[Reproducibility](reproducibility.md) for the envelope, the
environment archive, the attestation, and the order in which to do
them.

## The PROOF tab

The **PROOF** tab is the requirements ledger. A header card names the
project's current level ("Not yet at Level 1", "Level 1:
Self-Consistent", "Level 2: Published", or "Level 3: Reproducible")
with a clickable progression strip. Below it are three expandable
sections, **Level 1 — Self-Consistent**, **Level 2 — Published**, and
**Level 3 — Reproducible**, each listing its project-wide requirements
with a status light, what the requirement means, how to meet it, and a
link to where the work happens (the Main tab's Project block or the
Repos panel). The tab owns the requirement text; the buttons that do the
work live in the Project block. An unmet optional row shows a neutral
dash and is not counted.

The Level 3 section ends with the **Verify Level 3 Reproducibility**
button, enabled once the readiness checks pass (the rebuild runs in the
container and can take hours), the current **Level 3 Attestation**, and
the **Reproduction History** of every attempt.

The same level and blockers are available without a browser:
`vaibify status --proof` prints them, and `vaibify status --json` emits
the environment and PROOF status as one JSON object.

## Level cells and status lights

Step rows, the Steps and Project banners, and every requirement row
carry **L1 | L2 | L3** level cells with seven states:

| Cell | Meaning |
|---|---|
| Hollow gray circle | Not started: no outputs on disk and no activity at this level. |
| Gray filled circle | Unassessed: outputs exist, but no tests, checks, or sign-off are recorded. |
| Red circle | No requirements met. |
| Orange circle | Partially met. |
| Vaibify badge | Attained: every requirement at this level is met. |
| Question mark (?) | Unknown: GitHub or Zenodo has not been checked recently. |
| Dash (—) | Not applicable: no requirements at this level for this row. |

A level cell and the rows beneath it are computed from the same list,
so they cannot disagree. A remote that has never been checked is never
shown as passing. Warning glyphs (⚠) are colored by severity, not by
level: red for something currently broken or failing, orange for
something stale or pending. Hovering a glyph gives every reason and its
remedy, and the **?** Help panel carries the full legend.

## The Replay axis

The ladder measures the state of the *artifact*. The Replay axis
measures the provenance of the *process* that produced it: which models
did the work, under what instructions, and whether the development
dialogue is preserved. Its evidence must be collected from the
beginning, because a prompt transcript cannot be reconstructed after
the fact, which is why vaibify tracks it even though Level 4 is out of
scope. Its states, each requiring the ones before it:

| State | Requires |
|---|---|
| untracked | Nothing declared. |
| declared | Every AI model declared, and the Personal AI Configuration question answered. Level 2 requires this state. |
| recorded | The Prompt Record enabled and its first capture approved by the researcher. A project at this state or above is "Replayable." |
| supervised | Supervised mode enabled on top of the Prompt Record. |

The Prompt Record copies the in-container agent's session transcripts
into `.vaibify/promptRecord/`, redacting secrets at capture and
hash-chaining the captures so that editing or removing one breaks the
chain. It requires `pip install vaibify[replay]` on the host, and the
agent can never approve its own transcript. Supervised mode flags every
repository change that cannot be attributed to a recorded action, and
those flags are permanent. The axis is honest about its limit: the
chain is *tamper-evident*, not *provably complete*. Coverage intervals
make the monitored windows explicit, and gaps are shown as gaps.
