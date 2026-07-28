---
name: read-manuscript
description: Read the project's OWN manuscript (the LaTeX source of the paper this project supports) before answering questions about it, checking it against results, or editing figure references. Use whenever asked to consult, summarize, check, or update the project's paper or manuscript.
---

# Reading the project's own manuscript

The manuscript is NOT in the container by default — its LaTeX lives
on Overleaf and must be pulled in. Never answer a question about the
paper's content from memory or inference; pull it and read it, or say
you cannot.

## Procedure

1. **Pull the manuscript sources**: `vaibify-do pull-manuscript`
   This mirrors the Overleaf project's `.tex`/`.bib`/`.bbl` files
   into `<project-repo>/.vaibify/manuscript/` (a git-ignored,
   read-only convenience copy) and returns `listPulledFiles`.
2. **Find the main file**:
   `grep -l '\\documentclass' <repo>/.vaibify/manuscript/*.tex`.
   Follow `\input{}` / `\include{}` to section files.
3. **Read selectively, not linearly** — abstract, then only the
   sections relevant to the question, then figure captions
   (`grep -n caption`). The token cost of reading a whole manuscript
   is real; a targeted read almost always answers the question.
4. When checking the paper against results, compare the numbers/
   figures in the tex against the project's actual outputs; report
   discrepancies rather than assuming the paper is current.

## When there is no Overleaf binding

`pull-manuscript` returns a 409 ("No Overleaf project is bound") when
the project has no manuscript configured. Then:

- Check whether the repo vendors its own tex:
  `find /workspace -name '*.tex' -not -path '*/.vaibify/*'`.
- If some other paper (a published preprint, a cited work) is meant,
  use the **read-arxiv** skill instead.
- Otherwise, tell the researcher the manuscript is Overleaf-side and
  not bound, rather than inventing its content.

## Honesty requirements

- Never state what the manuscript says without having pulled and read
  it. "I have not pulled the manuscript" is the correct answer when
  you have not.
- The pulled copy is a snapshot; if the researcher is actively
  editing on Overleaf, re-pull before relying on it.
- The manuscript copy is git-ignored on purpose — do not commit it
  or treat it as a canonical project artifact.
