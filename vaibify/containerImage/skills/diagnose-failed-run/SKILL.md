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
   - `sFailureCauseHost` — a summary of the host-side exception (e.g.
     an ASGI WebSocket close), with credentials redacted, the host's
     home directory written as `~`, and its length capped. This is the
     real cause; the reason is just the symptom vaibify observed.
   - `iActiveStepAtDeath` — the step that was running when the runner
     died. Start your investigation there.

2. **You want what the host recorded against this container:**
   `vaibify-do get-host-log-tail --lines 200`
   From inside the container this returns `{bSanitized: true,
   listIncidents: [...]}` and NO log lines: each incident carries only
   its time (`sIso`), level (`sLevel`) and this container's id. The raw
   `~/.vaibify/vaibify.log` spans every container and carries host
   paths, so this lane never receives its lines; when you need the
   text, ask the researcher to read that file on the host. An empty
   `listIncidents` means nothing was recorded against this container,
   NOT that nothing failed.

3. **A step failed on its own logic** (not a runner death): read the
   step's own execution log under `<repository>/.vaibify/logs/` and the
   script's traceback. A fix that changes no scientific calculation
   you may make; one that would change a calculation waits for the
   researcher's explicit direction. Either way, show the researcher the
   diff, then `vaibify-do run-step <label>`.

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
