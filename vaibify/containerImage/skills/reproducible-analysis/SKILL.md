---
name: reproducible-analysis
description: Answer a quantitative or statistical question by writing a saved, reproducible script rather than a throwaway command, structured so it can become a pipeline step. Use whenever asked to compute, estimate, fit, sample, summarize, or analyze anything numeric.
---

# Answering numeric questions reproducibly

A number is only a scientific result if someone else can regenerate
it. A value you print from a heredoc, `python -c`, a REPL, or a
one-liner vanishes the moment the shell exits — it cannot be reviewed,
re-run, versioned, or turned into a step. So the deliverable for any
quantitative or statistical question is **a saved script**, even when
the researcher only asked for the answer.

## The one rule

**Never compute a quantitative or statistical result with a throwaway
construction** — no heredocs, `python -c "..."`, `<<EOF`, inline
one-liners, or interactive sessions. Write a script, save it, run it.

## Where the script goes

- **If it extends an existing step's analysis**, put it in that step's
  directory as `dataFoo.py` (or `plotFoo.py` if it draws a figure).
- **If it is exploratory** — a question that is not yet a step, and
  may never be one — put it in `explorations/` at the project-repo
  root. This is the git-tracked home for reproducible analyses that
  are not (yet) formal steps: preserved and re-runnable, but honestly
  outside the pipeline, so their outputs are never presented as
  verified results.

Do NOT invent a fresh step directory just to hold an exploratory
script. "It belongs with a step" is not a reason to fabricate a step —
that is what `explorations/` is for.

## Before you write: check for an existing one

The point of a self-explanatory name and a purpose docstring is that a
script written today is findable months from now. So before writing a
new analysis, search first:

```
grep -rli "intrinsic scatter" explorations/   # match name OR docstring
ls explorations/
```

When the researcher asks "didn't we already make a script to
compute X?", that same search is the answer. Reuse or extend the
existing script instead of writing a near-duplicate.

## How to write it

Use the project's language (Python for most vaibify projects; match
the surrounding code otherwise):

1. **A self-explanatory, verb-first camelCase name.**
   `calculateIntrinsicScatter.py`, not `analysis1.py`. The name should
   let a researcher guess what it does; keep the file's first line a
   one-sentence docstring stating exactly what it computes, so a later
   `grep` finds it even when the name misses a keyword.
2. **Inputs as arguments, not hardcoded paths.** Take every input via
   `argparse` (Python) or the language's equivalent. A cross-step
   input MUST be a `{step:<id>.<stem>}` token in the command, never a
   hardcoded `../OtherStep/out.json` — the token is what makes the
   dependency graph honest (see the create-pipeline-step skill).
3. **Outputs to files, not stdout.** Write results (the number, the
   fit, the samples, the summary JSON) to files, so the value is on
   disk and hashable, not just printed.
4. **Seed any randomness.** An unseeded sampler is not reproducible;
   set and record the seed so the run repeats bit-for-bit.
5. **Run it through `vaibify-do`**, not by executing it directly (see
   the running-steps skill), so the dashboard reflects the run.

## Turning it into a step

When an exploration proves worth keeping, promote it: move it from
`explorations/` into a step directory, rename it to the `dataFoo.py`
step-script convention, and register it via the `create-pipeline-step`
skill (its command, declared outputs, tests, and cross-step tokens).
`explorations/` is the staging ground; steps are the formal record.
The script you wrote to answer the question IS the foundation of the
step — that is the point of writing it as a script in the first place.

## Honesty

Even for a quick exploratory estimate, write the script and tell the
researcher its path. If you genuinely need a scratch calculation to
decide how to proceed (not to report a result), say so explicitly and
keep it out of any reported number. Never let a throwaway value stand
in for a scientific result.
