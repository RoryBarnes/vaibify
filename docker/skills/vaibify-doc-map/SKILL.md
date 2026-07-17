---
name: vaibify-doc-map
description: Find and read the right section of vaibify's own documentation instead of loading a whole doc into context. Use when you need authoritative detail on how vaibify works — the dashboard, the reproducibility ladder, the project/step schema, test formats, or script authoring.
---

# vaibify documentation map

Curated docs ship in the container at `/usr/share/vaibify/docs/`.
Read the targeted section, not the whole file — most are 140-600
lines. `grep -n '^##' <file>` lists a doc's section anchors if the
map below is stale.

## Question → (doc, section)

All paths are under `/usr/share/vaibify/docs/`.

| You need to know… | Doc | Section |
|---|---|---|
| What a dashboard panel/badge/row means | dashboard.md | `## Status lights and colours`, `## The Main tab`, `## The AICS tab` |
| The agent-action catalog and shipped skills | dashboard.md | `## Agent actions`, `### Shipped agent skills` |
| What each AICS level proves / requires | reproducibility.md | `## The Reproducibility Stack`, `## AICS Level 3 — Reproducible` |
| The reproducibility envelope files | reproducibility.md | `## The Reproducibility Envelope` (Tier 1/2/3 subsections) |
| How `vaibify reproduce` verifies | reproducibility.md | `## The verification ceremony` |
| The full ladder incl. L4/L5 (out of scope) | vision.md | (whole file is short) |
| The project.json / step object schema | pipelines.md | `## Project File`, `## Step Object` |
| Project size limits, core allocation | pipelines.md | `### Project size limits`, `## Core Allocation` |
| Test file formats and detection | testFormats.md | `## Format Table`, `## How Format Detection Works` |
| The data access-path syntax for tests | testFormats.md | `## Access Path Syntax` |
| The cross-step `{StepNN.varname}` contract | scriptAuthoring.md | (whole file; the token convention + colliding basenames) |

## Caveats

- These are a curated subset. The full docs tree lives in the host
  repo (`docs/` and `vaibify/docs/`); if a topic is not staged
  in-container, say so rather than guessing — do not invent doc
  content.
- Section titles drift. If a named section is absent,
  `grep -n '^#' <file>` and pick the closest.
- For task recipes (reaching an AICS level, authoring a step,
  diagnosing a failed run, reading a paper) prefer the dedicated
  skill over reading docs raw.
