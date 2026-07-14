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

## How to write it so it can become a step

Use the project's language (Python for most vaibify projects; match
the surrounding code otherwise) and follow the step-script shape:

1. **One script, saved in the relevant step directory** — name it
   `dataFoo.py` for analysis (or `plotFoo.py` if it draws a figure),
   in camelCase, in the step's directory. If no step exists yet,
   create the directory for the analysis you are about to do.
2. **Inputs as arguments, not hardcoded paths.** Take every input via
   `argparse` (Python) or the language's equivalent. A cross-step
   input MUST be a `{step:<id>.<stem>}` token in the command, never a
   hardcoded `../OtherStep/out.json` — the token is what makes the
   dependency graph honest (see the create-pipeline-step skill).
3. **Outputs to files, not stdout.** Write results (the number, the
   fit, the samples, the summary JSON) to declared output files, so
   the value is on disk and hashable, not just printed.
4. **Seed any randomness.** An unseeded sampler is not reproducible;
   set and record the seed so the run repeats bit-for-bit.
5. **Run it through `vaibify-do`**, not by executing it directly (see
   the running-steps skill), so the dashboard reflects the run.

## Turning it into a step

Once the script exists and runs, promote it: use the
`create-pipeline-step` skill to register it as a step (its command,
declared outputs, tests, and cross-step tokens). The script you wrote
to answer the question IS the foundation of the step — that is the
point of writing it as a script in the first place.

## Honesty

Even for a quick exploratory estimate, write the script and tell the
researcher its path. If you genuinely need a scratch calculation to
decide how to proceed (not to report a result), say so explicitly and
keep it out of any reported number. Never let a throwaway value stand
in for a scientific result.
