---
name: diagnose-failed-run
description: Triage a vaibify pipeline run that died, hung, or reported an unknown state, using the read-only diagnostic actions before asking the researcher. Use when a run reports exit-code -9999, a step is stuck, or the dashboard shows a failure you need to explain.
---

# Diagnosing a failed pipeline run

Two read-only, agent-safe actions surface what the host knows. Use
them BEFORE asking the researcher to investigate from the host — the
answer is usually already recorded.

## Decision tree

1. **A run died or a step is stuck in an unknown state** (exit-code
   `-9999` = "runner disappeared", or a step shows no terminal
   result):
   `vaibify-do get-pipeline-state`
   Returns the reconciled `pipeline_state.json`:
   - `sFailureReason` — the symptom (e.g. `heartbeat_stale`).
   - `sFailureCauseHost` — the actual host-side exception (e.g. an
     ASGI WebSocket close). This is the real cause; the reason is
     just the symptom vaibify observed.
   - `iActiveStepAtDeath` — the step that was running when the runner
     died. Start your investigation there.

2. **You need the raw host log for this container:**
   `vaibify-do get-host-log-tail --lines 200`
   Returns the last N lines of `~/.vaibify/vaibify.log` filtered to
   this container's id, plus a `listIncidents` ring of recent host
   exceptions for the same id.

3. **A step failed on its own logic** (not a runner death): read the
   step's own execution log under `/workspace/.vaibify/logs/` and the
   script's traceback; fix the script, then `vaibify-do run-step
   <label>`.

## Caveats

- Both diagnostic actions are read-only — safe to run without asking.
- Distinguish a *runner death* (infrastructure: heartbeat/WebSocket,
  read `get-pipeline-state`) from a *step failure* (the researcher's
  code raised, read the step log). Reporting one as the other sends
  the researcher down the wrong path.
- If `vaibify-do` reports the session is not initialized or the host
  is unreachable, that is a "not connected" condition — tell the
  researcher to click the container in the dashboard to reconnect;
  do not attempt workarounds.
