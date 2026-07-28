---
name: session-budget
description: Keep long-running autonomous work alive across Claude session-usage limits — measure the current 5-hour usage window, checkpoint before the limit, pause at a threshold, and resume after the window resets. Use when starting multi-hour autonomous work, when asked to monitor session usage, or when resuming after a usage limit interrupted work.
---

# Session budget: checkpoint, pause, resume

Long autonomous runs die ugly when they hit a usage limit mid-task:
uncommitted work, no report, no note for the successor. This skill
makes that failure boring instead of destructive.

## The one rule that matters most

**Checkpoint discipline is the primary defense; usage monitoring is
only the secondary one.** Some limits are invisible to any local
monitor (weekly and monthly account limits, other sessions on the
same account), so work as if the session could end at any moment:

- Commit completed units of work as you go — never let more than one
  work unit sit uncommitted.
- Maintain a running `RESUME_NOTES.md` in the working directory: what
  is done, what is in flight, the exact next command. Update it
  BEFORE starting each risky or long operation, not after.

## Measuring the current window

Claude subscription usage is windowed in 5-hour blocks that start
with the first message after a gap. The container has
`claude-monitor` installed (a Python tool that parses the local
Claude Code transcripts). Before relying on it, probe it once —
`claude-monitor --help` — and prefer its non-interactive/report
output if available; do not leave a live TUI running as a tool call.

Treat every reading as an **underestimate**, for two reasons you
should not forget:

1. It only sees THIS container's transcripts. The 5-hour window is
   account-wide; the researcher's other sessions (host-side, other
   containers) draw from the same pool and are invisible here.
2. Plan limits are not exposed by any API; the tool estimates them.

Because the reading is a lower bound, the pause threshold must be
conservative.

## Procedure for a long autonomous run

1. **At start**: create or update `RESUME_NOTES.md`; note the time
   and the current usage reading.
2. **Cadence**: check usage between work units, roughly every 5
   minutes of wall time. Above ~90% of the estimated window, check
   after every work unit. (The researcher may specify different
   thresholds in the task prompt — honor those.)
3. **At the pause threshold (default 95%)**:
   - Finish or cleanly abort the current unit; never pause mid-edit.
   - Commit everything; update `RESUME_NOTES.md` with next steps.
   - Compute seconds until the window resets (the block ends 5 hours
     after its first message; the monitor reports the block start).
     Add a 60-second margin.
   - Pause with a single blocking call: `sleep <seconds>`. A sleeping
     tool call consumes no tokens while it waits.
4. **On resume**: re-read `RESUME_NOTES.md`, confirm the usage window
   has reset, and continue from the recorded next step.
5. **If you are ever restarted without having paused** (a limit or
   crash killed the previous session): look for `RESUME_NOTES.md`
   first and continue from it.

## Honesty requirements

- Report pauses to the researcher in your output: when you paused,
  why, and when you resumed. A silent multi-hour gap reads as a hang.
- Never present the usage percentage as exact; say "at least N% by
  local measurement."
