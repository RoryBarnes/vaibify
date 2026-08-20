# Agent Council — Phase 0 empirical findings

These are the recorded results of the Phase 0 feasibility gate (§16, §2.7).
They are implementation facts, not researcher documentation, so they live in
`design/` beside the specification rather than in the published Sphinx tree.
Each finding names how it was established so a later reader can re-run it
rather than trust it.

## Claude Code CLI as a runner-backend adapter (task 5)

Established against `claude` version 2.1.229 on 2026-08-19, by reading
`claude --help` and by running one minimal real headless call
(`claude -p "…" --model sonnet --output-format json --append-system-prompt "…"`),
which returned the single obeyed token `COUNCILOK`, exit 0.

### Headless launch — CONFIRMED

- `claude -p/--print` runs non-interactive and the workspace-trust dialog is
  skipped in non-interactive mode. This is the runner's launch mode.
- `--output-format json` returns a JSON **array** of normalized messages;
  `--output-format stream-json` streams them as they arrive (pairs with
  `--input-format stream-json` and `--include-partial-messages`). The adapter
  parses the array/stream, never a file the agent writes (§8.5).
- The final object carries `is_error`, `subtype` (`success`), `result`,
  `stop_reason`, `permission_denials`, and `usage`
  (`input_tokens`/`output_tokens`/cache fields) — so honest terminal-state and
  usage extraction (§2.5, §9.4) is mechanical.
- `--permission-mode` accepts `plan`; provider-native plan mode is available as
  a behavioural nicety but is **never** the boundary (§8.6) — the disposable
  runner is.

### Charter delivery channel, separable from project agent docs — CONFIRMED (this was the open §5.5 per-adapter question)

The CLI has a distinct, highest-priority instruction channel delivered **as a
flag**, not as a file inside the snapshot:

- `--system-prompt <text>` / `--system-prompt-file <path>` replaces the default
  system prompt; `--append-system-prompt <text>` / `--append-system-prompt-file`
  appends to it. The minimal probe proved obedience: with an appended
  instruction the model emitted exactly the requested token and nothing else.
- Because the composed charter+role+phase instruction (§5.6) arrives on the
  command line, it **does not overwrite or shadow** any `AGENTS.md`/`CLAUDE.md`
  inside the snapshot copy — the exact hazard §5.6 warns about. The runner
  adapter delivers the charter via `--append-system-prompt`/`--system-prompt`,
  never by writing an agent-doc file into the snapshot tree.
- `--bare` additionally skips `CLAUDE.md` auto-discovery, hooks, keychain reads,
  auto-memory and attribution. It is attractive for evidence-baseline purity,
  **but** in `--bare` mode Anthropic auth is strictly `ANTHROPIC_API_KEY` /
  `apiKeyHelper` — OAuth and keychain are never read. That collides with the
  §9.7 subscription-reuse credential lane. RESOLUTION for the MVP: do **not**
  use `--bare`; deliver the charter via `--append-system-prompt` and suppress
  the snapshot's own `CLAUDE.md` influence by **excluding agent-doc files from
  the snapshot at capture time** (the context primitive already excludes
  `.vaibify/` and credential stores — add the repo-root `CLAUDE.md`/`AGENTS.md`/
  `GEMINI.md` symlinks and `.vaibify/AGENTS.md` to that reviewed exclusion, so
  the runner reviews project *source*, not the researcher's agent instructions).
  This keeps the subscription credential lane usable while still preventing the
  project's agent docs from steering a council participant.

### Resolved model identity — CONFIRMED (§13.2)

Requested-vs-resolved is recorded mechanically, not by convention:

- the `system` init event reports the resolved `model` (`claude-sonnet-5` when
  `--model sonnet` was requested);
- each `assistant` message reports `message.model`;
- the final result object's `modelUsage` dict is **keyed by the exact resolved
  model ids** actually used (the probe showed `claude-sonnet-5` plus
  `claude-haiku-4-5-20251001` for an auxiliary turn).

The adapter records requested (`--model` argument) and resolved (init-event
`model`) per turn; it never launders an alias into an exact declaration.

### Model discovery for the participant picker — mechanism chosen

The CLI exposes **no** `models list` subcommand; `--model` takes an alias
(`fable`/`opus`/`sonnet`) or a full id (`claude-fable-5`). A hardcoded alias
table is exactly the staleness the spec forbids (§6.3.1, §8.2). MECHANISM: the
Claude adapter populates the picker from the Anthropic API's live
`GET /v1/models` (the same reviewed provider transport the API backend uses,
§8.2/§8.3), then passes the chosen model id to `claude --model <id>`. Live
discovery, no stale table, and it reuses `providerApiTransport.py` rather than
minting a second broker. (If the researcher has no API key configured, the
picker falls back to the small set of CLI-accepted aliases, clearly labelled as
the un-verified alias set rather than a discovered list.)

### Credential source of record and the extraction-only lane (§9.7)

- Inside a Linux runner/container there is no macOS keychain, so Claude Code's
  credential is a file: `~/.claude/.credentials.json`. In the vaibify container
  that directory is persisted onto the workspace volume as
  `${WORKSPACE}/.claude/` and symlinked back to `~/.claude`
  (`fnPersistAgentConfig`, `entrypoint.sh:654`). **That persisted file on the
  workspace volume is the §9.7 source of record** the reviewed host-side read
  primitive extracts from — a named-secret-file read, never a general container
  command.
- On the researcher's own macOS host the login lives in the login keychain
  (`security` item `Claude Code-credentials`), but the council extracts from the
  *container's* persisted config, so the keychain is not the council's concern.
- NARROWEST-CREDENTIAL and NON-INTERFERENCE questions (does a copied access
  token authenticate a headless runner without the refresh token, and does using
  it leave the project container's login valid) remain **live empirical items
  for Phase 2**, to be proven against a real runner built from a real project
  image — they cannot be settled from the host CLI alone. `claude setup-token`
  (a long-lived subscription token) is the fallback narrow credential if a raw
  copied access token proves insufficient headless.

### §2.7 residual-risk statement — drafted for the launch UI

> This council runs the provider's CLI inside a throwaway container built from
> your project's image, holding a copy of your files. To do that it reuses the
> **Claude subscription already logged in for this project**, copying the
> narrowest token that authenticates into that one container. A prompt-injected
> model could read its own copied token or push data out through the one
> network path it is allowed (its provider's API). The copy is destroyed with
> the container, but **destroying the copy does not revoke the credential** —
> revoke at the provider if a run is compromised. Under remote access the login
> being reused belongs to the account configured on **the machine the hub runs
> on** (the execution host), which may not be the machine you are sitting at.

This must be shown at launch (§6.3), name the provider receiving content, and —
per §2.7 under remote access — name **whose** account and **which machine**.
