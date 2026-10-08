---
name: vaibify-doc-map
description: Find and read the right section of vaibify's own documentation instead of loading a whole doc into context. Use when you need authoritative detail on how vaibify works — the dashboard, the reproducibility ladder, the project/step schema, test formats, or script authoring.
---

# vaibify documentation map

Curated docs ship in the container at `/usr/share/vaibify/docs/`.
Read the targeted section, not the whole file — most are 300-700
lines. `grep -n '^##' <file>` lists a doc's section anchors if the
map below is stale.

## Question → (doc, section)

All paths are under `/usr/share/vaibify/docs/`.

| You need to know… | Doc | Section |
|---|---|---|
| What a dashboard panel/badge/row means | environmentsAndProjects.md | `## Status lights and colors`, `### The Main tab` |
| Environments, steps, dependencies, staleness | environmentsAndProjects.md | `## Environments`, `### The parts of a step`, `### Dependencies`, `### Polling and staleness` |
| The agent-action catalog and shipped skills | forAgents.md | `## Agent actions`, `## Shipped agent skills` |
| The Agent Council | forAgents.md | `## The Agent Council` |
| What each PROOF level proves / requires | proofLadder.md | `## Ascending the ladder in vaibify` (Level 1/2/3 requirements), `## The PROOF tab` |
| The full ladder incl. L4-L6 (out of scope) | proofLadder.md | `## The levels of the PROOF Ladder`, `## Where vaibify sits` |
| AI-provenance states (declared/recorded/supervised) | proofLadder.md | `## The Replay axis` |
| How a project reaches Level 3 | reproducibility.md | `## PROOF Level 3 — Reproducible`, `## The order of the final steps` |
| The reproducibility envelope files | reproducibility.md | `## The reproducibility envelope` (Tier 1/2/3 subsections) |
| How `vaibify reproduce` verifies | reproducibility.md | `## The verification ceremony`, `## Reproducing a published project` |
| The project.json / step object schema | environmentsAndProjects.md | `## The project file`, `## The step object` |
| Project size limits, core allocation | environmentsAndProjects.md | `## Project size limits`, `## Core allocation` |
| Test kinds and how tests are generated | testing.md | `## Testing your project`, `### How tests are generated` |
| Test file formats and detection | testing.md | `### Format table`, `### How format detection works` |
| The data access-path syntax for tests | testing.md | `### Access path syntax` |
| The cross-step `{step:<id>.<stem>}` contract | scriptAuthoring.md | (whole file; the token convention + colliding basenames) |

## Caveats

- These are a curated subset. The full docs tree lives in the host
  repo (`docs/` and `vaibify/docs/`); if a topic is not staged
  in-container, say so rather than guessing — do not invent doc
  content.
- Section titles drift. If a named section is absent,
  `grep -n '^#' <file>` and pick the closest.
- For task recipes (reaching a PROOF level, authoring a step,
  diagnosing a failed run, reading a paper) prefer the dedicated
  skill over reading docs raw.
