# Agent Council — Codex and Gemini implementation plan

**Revision 9, 2026-09-04. This is the working document for the
implementing agent.** It is a standalone plan based on the current merged
Agent Council implementation. It does not depend on the history of the
conversation that produced it.

### Revision 9 implementation evidence

The implementation described here is now present in the working tree. Its
production-adapter smoke campaign established the following facts against the
scratch project's immutable image, not merely against fake CLIs:

- Codex CLI 0.153.0 completed a schema-valid turn as
  `gpt-5.6-luna` through the refresh-free ChatGPT credential shell and the
  provider-specific CONNECT proxy. The OpenAI strict schema requires every
  nested object to state `additionalProperties: false`; the evidence-item
  schema now does so. Optional CDN, MCP, and telemetry destinations attempted
  by the CLI remained denied and were not needed for the turn.
- Antigravity CLI 1.1.25 completed a turn as
  `gemini-3.8-flash-low` through the refresh-free access token. Its required
  runtime endpoints were measured as `antigravity-unleash.goog`,
  `www.googleapis.com`, `lh3.googleusercontent.com`, and the Cloud Code API
  hosts. Optional Playwright-download and Play Store destinations remained
  denied. Antigravity may wrap a schema result in one complete JSON fence, so
  the adapter accepts exactly that form and still rejects preamble-plus-fence
  guessing or divergent repeated objects.
- Antigravity writes RFC 3339 expiry timestamps with nanosecond fractions,
  which Python 3.9's `datetime.fromisoformat` does not accept. The credential
  parser truncates only excess fractional precision before comparing expiry.
  When the source access token is stale, `agy models` in the project container
  refreshes and persists it using the refresh token that remains there; the
  refresh token is never copied into a council runner.
- Mixed campaigns provision one credential stager and one egress namespace per
  provider. Claude retains its legacy bare campaign scope for cleanup
  compatibility; Codex and Gemini use provider-suffixed scopes.
- Quorum is computed from observed `(provider, resolved model)` identities.
  A requested alias, a missing identity, or two aliases resolving to the same
  model cannot manufacture the two-model floor.
- The 500-event live window remains bounded, while settled structured turn
  results are rendered from the durable campaign record so a completed long
  turn does not collapse to header-only content.

The direct-API backend remains out of scope. This revision records observed
implementation constraints; it does not broaden the credential or network
authority accepted by earlier revisions.

## 0. Outcome

Extend Agent Council from its current Claude-only runner backend to support
three provider CLIs:

- `claude`: Claude Code authenticated by the project's Claude subscription;
- `codex`: OpenAI Codex authenticated by the project's ChatGPT/Codex login;
- `gemini`: Gemini models reached through Google Antigravity CLI (`agy`),
  authenticated by the project's Antigravity account login.

Google ended the legacy Gemini CLI **Login with Google** service for
individual, Google AI Pro, and Google AI Ultra accounts on 2026-06-18. The
legacy binary may still present and complete its browser-code UI, but that is
not a usable consumer authentication path. Do not require or recommend a
consumer paid plan for it. Gemini CLI remains a possible enterprise-only
backend for separately licensed Gemini Code Assist Standard or Enterprise
users; that is not the portable default in this plan. Antigravity is Google's
documented replacement and must pass the same runner feasibility gates before
the `gemini` provider is implemented.

All three providers must participate in both planning and implementation
campaigns, may serve as chairbot, and must preserve the current guarantees:

- immutable sealed project context;
- server-owned charter separated from untrusted material;
- one fresh disposable runner per deliberation turn;
- bounded CPU, memory, time, stall time, and output;
- one provider credential per runner, never the shared login store;
- provider-specific network egress only;
- mechanically recorded requested and resolved model identity;
- visible failures, retries, pauses, and teardown uncertainty;
- completed participant work remains reviewable after live-event eviction or
  a hub restart;
- no false consensus and no false claim that a resource is gone.

The implementation should add provider adapters, not fork the campaign
engine. Direct OpenAI and Gemini API backends are separate execution engines,
not small fallbacks inside this work. If a CLI fails its credential,
instruction-channel, or containment gate, leave that provider visibly
unsupported and make a separate product, billing, security, and implementation
decision before planning an API backend.

## 1. Current implementation baseline

The current system already supplies the difficult provider-neutral machinery:

- `agentCouncilCampaign.py` owns campaign records, settings, lifecycle
  vocabulary, participant records, and `CouncilProviderConnection`.
- `agentCouncilCharter.py` owns the versioned charter, phase instructions,
  planning schemas, implementation-patch schemas, quoted material, repair
  instructions, and chat instructions.
- `agentCouncil.py` drives phase-synchronous deliberation, structured-result
  validation and repair, event emission, evidence processing, and checkpoints.
- `agentCouncilResolution.py` owns veto resolution, quorum, human gates,
  stopping-point descriptions, and deliberation summaries.
- `agentCouncilController.py` owns live runtimes, background tasks,
  serialization, retry/resume, snapshot restoration, and runner access.
- `agentCouncilRunner.py`, `agentCouncilDockerGateway.py`, and
  `agentCouncilEgress.py` own bounded disposable execution and proven teardown.
- `agentCouncilRegistry.py` already accounts for global concurrency and
  applies one global `iPerProviderMaxConcurrent` policy value independently to
  each provider. It does not yet hold provider-specific limit values.
- `agentCouncilChat.py` owns session-scoped chairbot conversations.
- `agentCouncilProviders.py` is the production Claude Code adapter.

The provider connection seam is sufficient for ordinary council turns:

1. `fdictPrepareImmutableContext`;
2. `fnStartTurn`;
3. `fiterStreamNormalizedEvents`;
4. `fdictCollectStructuredResult`;
5. `fsReportCompletion`.

Do not replace that seam with a new orchestration framework. The work is to
route to the right implementation and extract provider-specific responsibilities
that currently sit outside the seam.

### 1.1 Existing provider coupling to remove

The following are the primary implementation targets:

1. `agentCouncilController.fconnectionBuildParticipantConnection` always
   creates `ClaudeRunnerConnection`.
2. The runtime holds one `ftStageRunnerCredential` and one
   `dictRunnerAccess`, even though a mixed campaign needs provider-specific
   credentials and egress.
3. `routes/councilRoutes.py` validates against a literal Claude-only provider
   set and emits one Claude capability record.
4. `councilRouteGuards.py` performs Claude-specific enablement, credential
   presence, expiry, and staging.
5. `agentCouncilCredentialGate.py` accepts a provider argument but compares
   every record to the one global schema `claudeAiOauth.accessToken`.
6. `agentCouncilChat.py` directly composes Claude arguments, installs a Claude
   credential, provisions Anthropic egress, parses Claude events, and extracts
   Claude results.
7. `scriptAgentCouncil.js` renders raw provider identifiers and describes
   login, billing, expiry, and credential exposure as though every provider
   were Claude.
8. The engine labels provider events as normalized, but the production adapter
   currently exposes the provider's event dictionaries. Codex and Gemini have
   different event vocabularies, so this must become a real stable contract.

### 1.2 Existing documentation drift

`docs/agentCouncil.md` still says the first release is planning-only, says the
council never implements, and reproduces charter 1.0.0. The implementation now
supports implementation campaigns and a later charter. Correct the user
documentation before enabling another provider so tests and reviews refer to
one current product contract.

## 2. Scope and non-goals

### 2.1 In scope

- Provider registry and provider-specific capability records.
- Codex CLI runner adapter.
- Antigravity CLI runner adapter for Gemini models.
- Provider-aware credentials, expiry handling, evidence gates, and egress.
- Provider-aware chairbot chat.
- Provider-neutral display events.
- Durable participant-tab history projected from existing settled-turn
  records, without persisting raw provider streams.
- Requested-versus-resolved model correctness and final quorum enforcement.
- Provider-specific model discovery or explicitly labelled alias fallbacks.
- Provider-specific effort capabilities where the provider supports them.
- Mixed-provider planning and implementation campaigns.
- Unit, integration, Docker-live, browser, paid-account, and adversarial
  verification.

### 2.2 Explicitly out of scope for the first multi-provider release

- Running any provider in the active project container.
- Copying an entire multi-provider login store into a runner.
- Copying a refresh token merely because an official CLI supports it.
- Treating provider-native plan or sandbox modes as the containment boundary.
- Persisting provider sessions across deliberation turns.
- Persisting the live display-event stream or a new event-by-event durable
  transcript. The current bounded event ring remains ephemeral. Completed
  participant tabs instead gain a durable projection from the structured turn
  records already checkpointed; retaining more provider-stream detail would
  require a separate privacy, redaction, storage, and product decision.
- Counting auxiliary, routing, summarization, or subagent models as separate
  council participants.
- Provider-side cancellation claims that cannot be observed and proven.
- A generic provider SDK abstraction designed for hypothetical fourth
  providers.
- Any direct OpenAI or Gemini API execution backend. A failed CLI release gate
  triggers a separate design decision, not automatic expansion of this plan.

## 3. Product vocabulary

Use stable machine identifiers and separate display labels:

| Provider ID | Display label | Backend | Normal billing path |
|---|---|---|---|
| `claude` | Claude Code | runner | Project's Claude subscription |
| `codex` | OpenAI Codex — ChatGPT account | runner | Project's ChatGPT/Codex entitlement |
| `gemini` | Google Gemini — Antigravity | runner | Project's Antigravity entitlement |

Do not persist `chatgpt` as the provider identifier. ChatGPT is the account and
product surface; Codex is the coding agent and CLI actually executed. If a
future OpenAI API backend is added, represent backend and authentication
separately rather than overloading the provider name:

```text
sProvider = "codex"
sBackend = "runner" | "api"
sCredentialKind = "chatgptLogin" | "codexAccessToken" | "openaiApiKey"
```

Existing Claude campaign records remain valid without migration.

## 4. Target architecture

### 4.1 Provider modules

Keep the existing Claude implementation in `agentCouncilProviders.py` during
this work. Renaming it would create broad import and test churn without making
the provider addition safer. It would also invalidate source-coupled
falsification checks and may change generated mutation-inventory fingerprints;
those controls must be intentionally updated whenever touched, never treated
as incidental formatting churn.

Add:

- `agentCouncilProviderRegistry.py` — provider lookup and descriptor
  validation;
- `agentCouncilCodexProvider.py` — Codex CLI behavior only;
- `agentCouncilGeminiProvider.py` — Antigravity CLI behavior for Gemini models
  only.

The third production adapter satisfies the repository's rule of three, so the
registry is now justified. Do not force all behavior into a large base class.
The existing `CouncilProviderConnection` remains the common lifecycle. The
registry holds provider-owned factories and operations that must also be used
outside an ordinary turn.

### 4.2 Provider descriptor

Each registry entry must declare or construct:

- stable provider ID and display label;
- backend ID;
- connection factory;
- capability builder;
- model-discovery function;
- installed-CLI/image probe;
- credential schema identifier;
- credential-presence probe;
- login-expiry reader, if the credential expires;
- credential-stager factory;
- egress hostname set and any required DNS behavior;
- runner environment builder;
- chat runner preparation and message-execution operations;
- supported effort values per model, when known;
- instruction-channel status;
- event normalization and result/model/usage extraction;
- provider-specific failure classifiers.

Use a validated immutable mapping or small data object. Do not place secrets,
Docker objects, live connections, or user-supplied endpoints in the descriptor.

Do not assume that a nominally informational CLI command is read-only. In one
sandboxed Revision 3 tool environment, `codex --version` attempted PATH-alias
setup and `gemini --version` attempted to rewrite a project-registry file; a
separate direct host Gemini check observed no write. The behavior is therefore
environment-dependent, while the security conclusion is not. Derive normal
capabilities from an immutable image/build manifest where possible. When a
live version or readiness probe is necessary, run it in a disposable runner
with isolated writable home/config/cache directories under bounded tmpfs and
no source credential, then destroy the runner with the ordinary proof.

### 4.3 Provider lookup rules

- Routes validate provider IDs through the registry.
- The controller looks up the participant's provider for every connection.
- Unknown providers fail before campaign registration.
- A provider present in source but absent from the current project image is
  unavailable, not broken.
- A provider whose CLI exists but whose credential evidence is absent remains
  visibly disabled.
- A campaign start validates only its selected providers, but validates all of
  them before registering or spending work.
- Restore refuses an old campaign only when its recorded provider no longer
  has a reviewed adapter; it must not silently substitute another provider.

### 4.4 Runtime shape

Replace the single campaign access and stager fields with provider-indexed
records:

```text
dictRuntime["dictRunnerAccessByProvider"][sProvider]
dictRuntime["dictCredentialStagersByProvider"][sProvider]
```

Each access record contains that provider's network, proxy, allowlist, and
teardown tombstone. Provision it lazily on the first turn for that provider.
Release drains every provider record and retains the runtime while any
resource remains indeterminate.

Do not solve mixed-provider egress by giving every runner the union of all
selected providers' hostnames. That would let one compromised participant
send the snapshot to a second provider not responsible for that turn. One
provider per runner must remain true in both implementation and disclosure.

Resource names must include the campaign and provider scopes without accepting
provider-controlled text directly. Compose names from registry-owned provider
IDs that have passed the closed lookup.

### 4.5 Provider-neutral event contract

Define a bounded event vocabulary before adding Codex or Gemini:

```text
assistantMessage
toolStarted
toolFinished
status
usage
warning
error
```

Every normalized event carries only the fields required by the UI, such as
participant, event kind, safe display text, tool display name, sequence, and
bounded provider detail. Provider-native dictionaries stay inside the adapter
unless retained in an explicitly bounded diagnostic field.

Normalization does not make the live stream durable. It remains subject to the
existing bounded event ring and its explicit roll-off behavior. Structured
turn results, model identity, usage, failures, and accepted artifacts remain
durable through their existing records; raw or normalized display events do
not become durable as part of this change.

The participant tab must nevertheless remain useful after ring eviction and a
hub restart. Render a durable settled-turn history from each participant's
existing `dictTurnsByPhase` records, grouped by round and phase and showing at
least turn status, failure, repair status, requested/resolved identity, usage
when recorded, and a bounded rendering of the structured result. While a turn
is live, render normalized ring events after that durable history and
deduplicate using the server-minted turn ID plus a turn-local event ordinal.
When no display events survive, the settled-turn rendering prevents the tab
from collapsing to header text. Do not expose an implementation patch twice if
its full artifact already has a dedicated view; show a bounded summary and a
link or existing artifact affordance instead.

If complete event-by-event replay is later made a product requirement, design
it separately with explicit size, lifetime, redaction, migration, and deletion
rules.

The frontend must switch only on the normalized event kind. It must not learn
Codex `item.*`, Gemini `tool_use`, or Claude stream event shapes.

### 4.6 Failure contract

Retain core failure classes used by retry policy:

- authentication;
- rate limit;
- network unreachable;
- clean exit without result;
- non-zero exit;
- killed without exit code;
- turn wall-clock kill;
- login-expiry kill;
- stall kill;
- output-byte limit;
- OOM;
- invalid structured result after repair;
- raised transport/adapter exception.

Each adapter maps provider-native errors onto this vocabulary and may retain a
bounded provider code/message for diagnosis. Do not flatten distinct provider
conditions into prose that retry logic has to parse.

## 5. Credential and security design

### 5.1 Existing shared-store risk

The project container persists Claude, Codex, and Antigravity configuration
under one workspace and runs all agents as the same unprivileged user. A compromised
agent in the active project container can therefore read every configured
provider credential. Adding two council adapters does not create that
arrangement, but it makes the blast radius more consequential. Update the
launch disclosure and security documentation to state:

- the execution host owns the accounts being reused;
- compromise of an active project agent can expose all configured provider
  sessions;
- each council runner receives only its own provider's narrow credential;
- destroying a runner copy does not revoke the source credential;
- provider-side revocation is required after suspected compromise.

### 5.2 Evidence records

Generalize `agentCouncilCredentialGate.py` so expected values come from the
selected provider descriptor. Each evidence record remains keyed by:

- provider;
- backend;
- CLI version observed in a fresh runner;
- immutable image identity;
- credential schema;
- credential source;
- host platform;
- verification date.

Retain fail-closed behavior for missing, malformed, mismatched, or stale
records. An evidence record for one provider enables only that provider.

The CLI version is descriptive evidence and the immutable image ID is the
runtime enforcement pin. The live ceremony must execute the CLI from a fresh
runner created from that exact image, not infer its version from the active
project container.

### 5.3 Credential requirements

For every provider:

1. Read the source credential through a fixed typed credential-file primitive,
   never a caller-supplied command.
2. Extract only the fields demonstrated to authenticate.
3. Never copy the provider's whole config directory.
4. Never copy a refresh token in the runner backend.
5. Materialize the narrow document into a mode-600 ephemeral host file.
6. Delete the host file immediately after building the delivery archive.
7. Deliver it owned by the unprivileged runner user.
8. Never write refreshed state back to the project container.
9. Clamp the turn to the credential lifetime when an expiry is known.
10. Confirm a completed and failed runner turn leave the source login usable
    and byte-equivalent except for unrelated project-container activity.

"Credential" means the minimum proven authenticating document, not only a
secret token. For Claude, the existing narrow document contains the access
token plus required non-secret OAuth scope metadata. Codex and Gemini must
independently prove any required non-secret account, workspace, project, or
scope fields; none may inherit Claude's schema by analogy.

Credential redaction covers personal identifiers carried inside credentials,
not only token-shaped secrets. Decoded claims, account identifiers, workspace
identifiers, and email addresses must not enter logs, events, exceptions,
campaign records, browser payloads, or evidence records. A typed expiry reader
may extract the minimum timestamp claim and must immediately discard the rest
of the decoded payload.

If a CLI cannot work without a refresh token, its account runner backend
does not ship. Leave it unsupported until a separately approved API-key
backend or documented short-lived automation credential has its own design.

### 5.4 Provider-specific egress

Determine egress empirically with the existing CONNECT proxy and a fresh
runner. The allowlist must include only fixed registry-owned hostnames needed
for normal authenticated operation. Test and reject:

- arbitrary DNS names;
- direct IP destinations;
- IPv6 bypasses;
- alternate ports;
- redirect-based escape;
- proxy environment overrides from project files;
- access to another enabled provider's endpoints.

Never accept endpoints, base URLs, proxy URLs, headers, or model-provider
configuration from the campaign request or project snapshot.

### 5.5 Phase 0 environment prerequisites

The feasibility ceremonies require a project image that actually contains the
candidate CLIs and project-owned persisted logins. Host-installed binaries and
host login files are useful for preliminary inspection only; they do not
satisfy the project-container credential source or immutable runner-image
boundary.

Before Phase 0A or 0B:

1. Repair or explicitly work around the base-image architecture mismatch
   discovered during the Revision 5 ceremony. The default
   `ubuntu:24.04@sha256:4fbb...` value in `Dockerfile` is an OCI image index,
   not the single-architecture amd64 image claimed by its adjacent comment.
   On an arm64 Docker daemon, both that index and the floating
   `ubuntu:24.04` value emitted by `vaibify init --minimal` select arm64, while
   the pinned compiler closure requires `*-x86-64-linux-gnu` packages and the
   build fails. The repository fix must make the promised architecture
   explicit and add a preflight or falsification test that prevents the
   configured base platform and pinned toolchain architecture from silently
   diverging. Until that lands, the local feasibility ceremony may use the
   inspected immutable amd64 child digest without changing any toolchain pin.
   On the Revision 5 Apple Silicon ceremony, the x64 Claude installer then
   crashed under emulation because Bun required unavailable AVX support.
   Therefore use a Codex-and-Gemini-only scratch image for Phase 0A/0B; do not
   misread an emulated Claude failure as a provider regression. The eventual
   three-provider smoke image remains gated on a native, architecture-coherent
   build.
   The repository repair should prefer native multi-architecture support over
   forcing Apple Silicon hosts through amd64 emulation: Claude's current x64
   installer is direct evidence that an otherwise successful emulated image is
   not sufficient. Keep one authoritative default-base/platform selection and
   use an architecture-matched immutable child digest and pinned toolchain
   closure. If a supported closure does not exist, fail in preflight before
   Docker downloads large layers and report the requested platform, resolved
   platform, and supported alternatives.
2. Correct the build diagnostics exposed by this ceremony. A provider
   installer that downloaded and then exited must report its exit status and
   the resolved build/host architecture. In particular, an emulated binary
   crash must not be described only as a network-path problem. Add a
   falsification test for this distinction and apply the same diagnostic shape
   to every provider overlay even when only Claude currently reproduces the
   failure.
3. Make the disk-space warning specific and least-destructive. The current
   warning aggregates images, volumes, and build cache, then recommends
   `docker system prune -af`; during this ceremony almost all safely
   reclaimable space was build cache, while the large volume allocation was
   not reclaimable. Report Docker's categories separately and recommend
   `docker builder prune` first when build cache is reclaimable. Broader image
   or system pruning must be an explicitly warned later option, and volume
   deletion must never be a default remediation.
4. Use a user-owned nonsensitive scratch project and enable its `codex`
   feature flag. Add an architecture-matched Antigravity overlay and an
   explicit feature flag rather than treating the existing legacy `gemini`
   overlay as evidence for the Gemini provider. Enable `claude` too if the
   scratch project will host the later three-provider smoke campaign. Disable
   all enabled agent auto-updates for the ceremony so the project-container
   CLIs cannot drift away from the freshly built image.
5. Run `vaibify build` for that project, then `vaibify stop` and
   `vaibify start` so the active container uses the rebuilt image.
6. From the project terminal, verify that every enabled agent CLI resolves as
   expected. Record the immutable image ID; obtain gate-quality CLI versions
   from the image manifest or an isolated disposable probe, because even
   `--version` may attempt configuration writes.
7. Complete `codex login --device-auth` and follow its host-browser device-code
   flow. For Google, stop using the legacy `gemini` browser-code flow: it is
   retired for consumer accounts even when its UI repeats the prompt without a
   useful explanation. Start `agy` inside the project container and exercise
   its documented remote OAuth flow. If Antigravity cannot detect or support
   the container terminal, cannot persist its login without a host keyring, or
   cannot expose a safely stageable minimum credential, record that as a
   feasibility blocker. Do not silently substitute an API key or copy a host
   credential. Never paste a credential into source, logs, chat, or a shell
   command recorded by the project.
8. Confirm each CLI reports the intended account login and that its
   configuration survives a controlled container restart through the
   workspace-backed `.codex` and Antigravity configuration stores. Determine
   whether Antigravity's Linux keyring dependency needs an isolated persistent
   keyring service; do not weaken credential isolation merely to make the
   restart check pass.
9. Only then extract a minimum candidate document from the project-owned store
   into a disposable runner and begin the paid credential ceremony.

The council runner overrides the project image's normal entrypoint, so it does
not run startup auto-update or login flows. Phase 0 must execute and record the
CLI version present in the immutable image itself, not assume the active
project container's post-startup binary is equivalent.

The Revision 6 scratch image proved only that legacy Gemini CLI 0.54.4 and
Codex could be installed. Google's dated deprecation notice supersedes the
older Gemini CLI authentication documentation: a repeated consumer login
prompt is now expected failure evidence, not an invitation to purchase Google
AI Pro or Ultra. Neither a host binary nor a successfully installed retired
binary removes the rebuild and in-container Antigravity login prerequisite
above. No provider probe may be trusted with a persisted credential store
merely because its advertised purpose sounds informational.

## 6. Codex runner adapter

### 6.1 Current official interface

Use the official Codex non-interactive interface as the starting point:

- `codex exec` for headless execution;
- `--json` for JSONL events;
- `--ephemeral` to avoid persisted rollout files;
- `--output-schema` for the final council result;
- `--ignore-user-config` and `--ignore-rules` for a controlled automation
  environment;
- `--model` for the requested model;
- stdin (`-`) for the untrusted prompt and quoted material;
- `CODEX_HOME` for isolated authentication;
- `developer_instructions` for the server-owned charter candidate;
- App Server `model/list` for picker-visible models and supported effort
  values where practical.

References:

- <https://developers.openai.com/codex/noninteractive>
- <https://developers.openai.com/codex/auth>
- <https://developers.openai.com/codex/config-reference>
- <https://developers.openai.com/codex/app-server>

Do not bake a current model name into the adapter or this plan.

### 6.2 Candidate invocation

The feasibility harness should begin with the equivalent of:

```text
codex exec
  --ephemeral
  --json
  --ignore-user-config
  --ignore-rules
  --model <registry-validated model>
  --output-schema <server-owned path outside the snapshot>
  -
```

The current Codex agent environment resolves an extension-bundled Codex CLI
whose `codex exec --help` advertises `-c key=value` as a generic configuration
override. That binary is not necessarily on the researcher's ordinary login
shell PATH and is not the project image binary. Treat this only as command-
surface evidence. Pass the charter through a direct argv `-c` override for
`developer_instructions`, not through the stdin prompt. The ceremony must
still prove that the image-pinned CLI accepts this specific setting with the
isolation flags and retains the required precedence. Build argv as a list;
never invoke a shell and never interpolate researcher or peer text into an
argument.

Do not use `model_instructions_file` initially: it replaces built-in model
instructions and could remove tool/safety behavior the harness depends on.

### 6.3 Codex Phase 0 falsification

Before product wiring, prove in a disposable runner:

- stdin is the complete untrusted prompt and cannot become a flag;
- `developer_instructions` remains higher priority than hostile quoted text;
- hostile or contradictory `AGENTS.md` files do not override the charter;
- ignored user config and rules cannot re-enable MCP servers, external tools,
  alternate providers, or broader permissions;
- JSONL events are parseable incrementally and terminal failure is explicit;
- `--output-schema` accepts every planning, implementation, summary, veto,
  and repair schema used by the charter;
- multi-megabyte implementation patches survive the output path;
- the event stream identifies the primary resolved model and usage;
- auxiliary work does not masquerade as a second participant model;
- runner destruction interrupts the CLI and descendants;
- wall-clock, stall, output, and OOM outcomes map correctly;
- version/readiness probes keep every attempted state write inside isolated
  bounded tmpfs and never touch the project-owned `CODEX_HOME`;
- the minimum egress allowlist is known and cross-provider access fails.

### 6.4 Codex authentication gate

Codex supports ChatGPT login, API-key login, and enterprise access tokens.
The official headless fallback permits copying `auth.json`, but the council may
not copy refresh-capable credentials merely because the CLI supports it.
Official OpenAI documentation says ordinary cached ChatGPT sessions refresh
tokens automatically before expiry. That behavior is convenient in the active
project container and specifically forbidden in a disposable council runner;
the ceremony must prove the narrow copy cannot refresh or fall back to another
credential source.

The ceremony must determine:

1. The source path and schema under the persisted project `CODEX_HOME`.
2. Whether an access-token-only synthesized `auth.json` authenticates.
3. Whether any non-secret account/workspace metadata is also required.
4. Whether the token expiry is readable and enforceable.
5. Whether the runner attempts a refresh when the refresh field is absent.
6. Whether success, provider failure, and forced runner destruction leave the
   project login unchanged and usable.

Preliminary local evidence, recorded 2026-09-02 but not sufficient for the
gate: a read-only inspection of timestamp claims in a host-side ChatGPT login
reported a 240-hour access-token lifetime with about 49 hours remaining, a
separate refresh token, and an already-expired one-hour ID token. No token
value was printed. This makes the four-hour turn budget plausible and suggests
that the ID token may not be required, but it proves neither the minimum file
schema nor authentication inside a runner. The ceremony still uses the
project-owned login and must test a synthesized access-token-only document.

The observed source file also reportedly contains an API-key field, and the
access-token claims contain an email address. The Codex extractor must use an
exact allowlist: never copy an API key, refresh token, ID token, email claim,
or whole decoded JWT payload. Copy an account or workspace identifier only if
Phase 0 mechanically proves it is required and classify it as sensitive for
all redaction and browser-output purposes.

Release decisions:

- If access-token-only works, ship the ChatGPT subscription runner backend.
- If an official enterprise Codex access token works, support it as a distinct
  credential kind after its own evidence ceremony.
- If personal ChatGPT auth requires the refresh token, do not ship that
  subscription runner path.
- An OpenAI API key may support a separately disclosed API-billed runner or
  direct API backend under a separate approved plan; it is not represented as
  subscription reuse.

### 6.5 Codex model and effort discovery

Prefer App Server `model/list` because it returns picker-visible models and
capabilities, including effort options. Cache only for the natural capability
response lifetime; do not persist the result as a permanent model table.

If live discovery cannot run without spending paid work or widening credential
exposure, return a clearly labelled unverified alias/manual-entry capability.
The UI must distinguish discovered model IDs from suggestions.

Map `sEffortPerParticipant` only when the selected model reports support.
Unsupported effort values are refused before campaign registration. Until
that is implemented, Codex uses provider standard rather than pretending the
global setting was honored.

### 6.6 Codex adapter deliverables

- `CodexRunnerConnection` implementing the existing connection seam.
- Isolated `CODEX_HOME` and minimal credential archive.
- Server-owned output-schema file delivered outside the snapshot root.
- Codex argv composer.
- JSONL parser and normalized-event mapper.
- Final structured-result extractor.
- Requested/resolved primary model and usage extractor.
- Provider failure classifier.
- Capability and model-discovery builder.
- Credential extraction/staging functions.
- Live evidence harness and evidence-record writer input.
- Unit and Docker-live fake CLI tests.

## 7. Gemini provider through Antigravity CLI

### 7.1 Product transition and current official interface

Do not build the consumer runner around legacy `gemini`. Google states that,
since 2026-06-18, Gemini CLI no longer serves Login-with-Google requests for
individual, Google AI Pro, or Google AI Ultra accounts. Standard and Enterprise
Gemini Code Assist organization licenses remain supported, but cannot be the
default assumption for a portable Vaibify project.

Evaluate Google's replacement `agy` binary using its documented headless
interface:

- `agy -p` performs a stateless non-interactive turn;
- `--output-format stream-json` emits an `init`, bounded `step_update` events,
  and exactly one terminal `result` event;
- `--json-schema` enforces structured terminal output;
- `--model` pins a listed model, and unknown models fail instead of silently
  falling back in headless mode;
- `--sandbox` and scoped permission rules constrain tools;
- cached credentials are required before a headless run and diagnostics go to
  stderr.

References:

- <https://developers.google.com/gemini-code-assist/docs/deprecations/code-assist-individuals>
- <https://antigravity.google/docs/cli/install/>
- <https://antigravity.google/docs/cli/headless/>

The 2026-09-03 live installer exposed only `--dir` and `--help`; it rejected
the documentation's `--skip-aliases` and `--skip-path` flags. Pin the observed
installer surface in the image overlay and re-check it whenever the immutable
CLI version changes rather than treating the website's flag list as evidence.

### 7.2 Charter-channel release gate

Do not carry forward Gemini CLI's `GEMINI_SYSTEM_MD` design: it is not evidence
about Antigravity's instruction hierarchy. Phase 0 must identify the highest
priority supported instruction channel and prove that a server-owned charter
cannot be displaced by snapshot rules, migrated `GEMINI.md` content, agent
profiles, skills, MCP configuration, or ordinary prompt text. The charter and
any provider configuration must remain outside the sealed snapshot.

If the pinned Antigravity version has no maintainable server-owned instruction
channel, leave the `gemini` provider visibly unsupported. Do not weaken charter
precedence or silently switch to legacy Gemini CLI or direct API execution.

### 7.3 Antigravity Phase 0 falsification

Prove in a disposable runner:

- prompt input cannot become an argument or control message;
- the charter wins against hostile snapshot rules and migrated configuration;
- scoped permissions prevent shell, write, network, MCP, skill, and subagent
  expansion beyond the council role;
- project settings cannot select alternate endpoints or wider tools;
- no interactive trust or approval prompt can hang a headless turn;
- stream-JSON events, terminal status, stderr diagnostics, and exit codes settle
  mechanically, including the documented soft-denial behavior;
- final structured output and usage can be extracted without accepting tool
  output as assistant text;
- the primary turn model is distinguishable from agent, routing, checkpoint,
  and subagent work;
- unknown models, fallback, and model switches fail or appear visibly;
- runner destruction and every budget behave as in the Claude lane;
- startup and probes keep registries, caches, logs, and configuration inside
  isolated bounded storage;
- the minimum Google egress allowlist is known and cannot reach OpenAI or
  Anthropic endpoints.

### 7.4 Antigravity authentication gate

Antigravity normally uses a native OS keyring and has a distinct remote OAuth
flow. The container ceremony must determine:

1. Whether the Vaibify terminal can invoke the remote/manual OAuth flow rather
   than requiring a local browser or desktop keyring.
2. The exact project-owned credential and profile sources, including any Linux
   Secret Service or dbus dependency.
3. Whether a minimum credential without refresh authority authenticates a
   fresh headless runner.
4. Which non-secret account/profile fields are required.
5. Token lifetime, expiry behavior, and the safe turn-duration clamp.
6. Source-login non-interference after success, failure, expiry, and forced
   destruction.

Explicitly clear or control `GEMINI_API_KEY`, alternate endpoint settings, and
ambient Google credentials so a runner cannot fall through to an unintended
identity. Antigravity's Gemini API-key mode is technically a CLI mode, but it
has different credential exposure, billing, privacy, quota, and endpoint
semantics. It requires a separately approved product decision and must never
be selected automatically when account login fails.

Release decisions:

- If a minimum account credential works safely in a fresh runner, ship the
  Antigravity-backed `gemini` provider.
- If it requires a refresh credential or persistent shared keyring authority,
  do not ship that credential path.
- A consumer Google AI Pro or Ultra purchase does not repair legacy Gemini CLI
  login and must not appear as remediation.
- Enterprise-only legacy Gemini CLI and API-key operation remain explicit
  alternative plans, not fallbacks.

### 7.5 Gemini model identity and discovery

Use `agy models` only through an isolated, capability-scoped probe. Expose its
concrete model slugs and reasoning-effort variants, record the model field from
the initialization/result evidence, retain bounded usage, and count only the
participant's primary resolved model for quorum. Agent profiles, checkpoint
work, and subagents never increase quorum.

### 7.6 Gemini adapter deliverables

- `AntigravityRunnerConnection` implementing the existing connection seam.
- Architecture-matched immutable Antigravity image overlay and feature flag.
- Isolated Antigravity configuration/keyring boundary and minimum credential
  archive, if Phase 0 authorizes one.
- Version-bound server-owned charter mechanism.
- Antigravity argv/environment and scoped-permission composer.
- Stream-JSON parser and normalized-event mapper.
- Final structured-result and repair integration.
- Primary-model, subagent, fallback, and usage extraction.
- Exit-status and provider failure classifier.
- Capability/model-discovery builder.
- Credential extraction/staging functions.
- Live evidence harness and evidence-record writer input.
- Unit and Docker-live fake CLI tests.

## 8. Quorum and model identity correction

### 8.1 Problem

Campaign creation correctly refuses duplicate requested `(provider, model)`
pairs, but final quorum also counts requested pairs. Routed aliases can satisfy
that check while two participants actually used the same primary model.

### 8.2 Required behavior

- Keep requested-pair uniqueness as an early configuration check.
- Record model identity on every completed turn.
- For every participant that completed the substantive phase predicate, derive
  its last mechanically observed primary resolved model.
- Final readiness requires at least two distinct `(provider, resolved model)`
  pairs among qualifying participants.
- An alias is never treated as resolved identity merely because it was passed
  to the CLI.
- Missing or ambiguous primary identity produces a quorum shortfall unless the
  provider capability guarantees that the requested concrete ID is also the
  reported execution identity and the adapter records that fact mechanically.
- Auxiliary models never increase quorum.

Add the resolved identities and the reason for any shortfall to the human gate
and accepted artifact.

### 8.3 Compatibility

Do not rewrite existing accepted campaigns. Apply the new final-quorum rule to
campaigns convened after a recorded protocol/charter version boundary. Old
records remain renderable with their original requested and reported identity
evidence.

## 9. Chairbot chat refactor

The chairbot's provider is fixed by the recorded chair participant. Chat must
look up that provider and use its credential, environment, egress, argv,
parser, model extraction, and failure explanation.

Do not force the session-scoped chat lifecycle into
`CouncilProviderConnection`; its lifetime differs from a one-turn connection.
Instead, expose a narrow provider chat operation through the descriptor or a
provider-owned chat connection:

1. provision provider-specific egress;
2. reserve and create the session runner under that provider quota;
3. copy the sealed snapshot;
4. deliver the narrow provider credential;
5. run each message with the server-owned chat instruction and full quoted
   transcript;
6. normalize events and update resolved identity;
7. destroy and prove the runner absent when closed, expired, failed, or the
   project is released.

Every message remains a fresh provider invocation. No provider-owned session
is relied on, and the full bounded transcript is re-sent exactly as today.

Test every provider as chairbot, including a mixed campaign whose chairbot is
not the first provider provisioned.

## 10. Routes, capabilities, and frontend

### 10.1 Capability response

Each provider record should expose at least:

- provider ID and display label;
- backend and credential kind;
- CLI installed in the selected image;
- CLI version if safely known;
- available/disabled state and exact reason;
- credential evidence state;
- login presence and expiry when knowable;
- model-discovery source and verification flag;
- model IDs/aliases and labels;
- effort values per model;
- instruction-channel verification state;
- billing/disclosure text key.

Overall council availability is true when the selected project is eligible and
at least two distinct selectable model pairs exist across enabled providers.
Do not require all installed providers to be logged in.

The image capability must be truthful. Because overlays are optional, source
support for Codex or Gemini does not mean a particular project image contains
the CLI. Prefer a build/readiness capability manifest tied to the immutable
image. Do not run a general caller-provided probe command.

### 10.2 Start, resume, retry, and chat guards

- Start validates all selected providers before campaign registration.
- Resume and retry validate only providers required by the recorded campaign.
- Chat validates the recorded chairbot provider.
- Provider evidence, image identity, credential presence, and expiry checks
  remain ordered before paid work.
- A missing unrelated provider login does not block the action.
- Refusals name the affected provider and remediation.

### 10.3 Frontend

- Render display labels rather than raw provider IDs.
- Group models by provider and show whether each is discovered, routed, or a
  manual ID.
- Show provider-specific availability beneath each participant.
- Show effort controls only when the selected model declares them.
- Name every provider receiving project content before launch.
- Explain subscription versus API-key billing per provider/backend.
- Show login expiry per selected provider.
- Show requested and resolved models in participant consoles and artifacts.
- Show durable settled-turn outcomes in every participant tab before live
  ring events, so eviction or a hub restart cannot leave a completed agent's
  tab with header text alone.
- Show routing/fallback warnings without implying a failed council.
- Keep all unavailable and partial states truthful; do not hide a provider to
  make the chooser appear healthy.

## 11. Deferred direct API execution engine

This section records the boundary of a possible future project; it is not an
implementation fallback authorized by this plan. Consider an API backend only
after a CLI fails its release gate or the researcher explicitly requests
API-key billing, and then write and approve a separate implementation plan.

A direct backend must reproduce the useful behavior currently supplied by the
agent CLI: snapshot inspection, a typed tool loop, disposable execution,
streaming, cancellation, schemas and repair, budgets, failure classification,
credential isolation, model identity, and provider-specific billing. It cannot
be implemented as a transport substitution inside a runner adapter.

### 11.1 Common API requirements

The model has no provider CLI, filesystem mount, or credential. The host owns
the API key and mediates a closed tool loop over the sealed snapshot:

- bounded directory listing;
- bounded file read;
- bounded text search;
- disposable script/test execution seeded from the snapshot;
- fixed tool names and typed arguments;
- bounded request/tool/turn counts;
- server-enforced structured final result;
- provider-specific streaming and failure classification.

Provider content may choose arguments only inside the closed schemas. It may
not choose an endpoint, header, executable, host path, container image, or
credential.

### 11.2 OpenAI API

Use the Responses API with developer instructions, structured output, custom
typed tools, streaming, and `store: false`. Keep the API key on the host.

References:

- <https://developers.openai.com/api/reference/resources/responses/methods/create>
- <https://developers.openai.com/api/docs/guides/function-calling>
- <https://developers.openai.com/api/docs/guides/structured-outputs>

### 11.3 Gemini API

Use the current Gemini generation/interaction API with system instructions,
structured output, function calling, streaming, and `models.list`. Keep the
API key or Vertex identity on the host.

References:

- <https://ai.google.dev/api/models>
- <https://ai.google.dev/gemini-api/docs/structured-output>
- <https://ai.google.dev/gemini-api/docs/function-calling>

Do not grow `providerApiTransport.py` into a high-level council abstraction.
It should own only low-level client creation, fixed endpoints, lazy optional
dependencies, redaction, and provider request execution. Council tool loops
belong in provider adapters or a separate API-backend module after the second
real API implementation establishes their common shape.

## 12. Implementation sequence

Ship this as independently reviewable changes. Every step keeps Claude
operational and the additional providers disabled until their live gates pass.

The feasibility gates come first because they determine whether each proposed
runner backend exists at all. Use disposable scratch harnesses and a
user-owned nonsensitive project; do not first refactor the production campaign
engine around an authentication or instruction mechanism that may fail.

### Phase 0 prerequisite — build and authenticate the candidate image

- First land the base-image architecture contract fix described in Section
  5.5, or record the inspected immutable amd64 child digest used by the local
  feasibility-only workaround. A build that resolved the OCI index to arm64
  is not evidence for the x86-pinned candidate image.
- Land the installer-diagnostic and disk-remediation corrections from Section
  5.5. These do not change a provider protocol, but they are required to make
  setup failures truthful and to avoid steering users toward unnecessarily
  broad Docker cleanup.
- Enable Codex and the new Antigravity overlay in a nonsensitive scratch
  project's feature configuration, plus Claude if the same project will host
  the final mixed smoke test, and disable their auto-updates for the ceremony.
  The existing legacy Gemini overlay does not satisfy this prerequisite.
- Rebuild the image and restart the project container.
- Verify both candidate CLI binaries inside the project container. Record the
  rebuilt image ID, then obtain their gate-quality versions from the image
  manifest or an isolated disposable probe rather than touching the persisted
  credential stores with a capability probe.
- Complete Codex device-code and Antigravity account logins inside that
  container and prove the workspace-backed configuration and any isolated
  keyring state survive one controlled restart.
- Do not substitute a host extension's binary or host login file for any of
  these project-owned prerequisites.

Exit criterion for each provider: the active scratch project and a fresh
disposable runner share one recorded immutable image containing that
provider's CLI, and its project-owned persisted credential source exists. One
provider's missing login blocks only its own feasibility phase.

### Phase 0A — Codex runner feasibility gate

- Confirm the pinned CLI and candidate flags, including the generic `-c`
  override observed on the extension-bundled reference binary.
- Run the paid ChatGPT credential ceremony with the minimum synthesized
  credential document.
- Prove charter precedence, config/rules isolation, event and structured-result
  behavior, primary-model identity, token expiry, teardown, and minimum egress.
- Record every claim as observed, falsified, or unverified without storing
  credential values.

Exit criterion: a written Codex finding equivalent to
`agentCouncilPhase0Findings.md` that either authorizes the subscription runner
against an immutable image or records Codex as unsupported. Failure does not
authorize an API backend.

### Phase 0B — Antigravity-backed Gemini feasibility gate

- Record legacy Gemini CLI consumer OAuth as retired; do not spend more codes
  or purchase Google AI Pro/Ultra attempting to revive it.
- Build and pin the architecture-matched `agy` binary, then confirm its
  headless, schema, model, permission, and stream-JSON behavior.
- Exercise the remote OAuth flow inside the project container and determine
  whether its profile/keyring state can be persisted and minimally staged
  without refresh authority.
- Measure actual token lifetime against realistic and maximum turn budgets.
- Settle the highest-priority charter mechanism.
- Prove config, skills, MCP, subagent, permission, primary-model, teardown, and
  minimum-egress isolation.
- Record every claim as observed, falsified, or unverified without storing
  credential values or decoded identity payloads.

Exit criterion: a written Antigravity finding that either authorizes a
maintainable Gemini-model runner against an immutable image or records Gemini
as unsupported. Failure does not authorize legacy enterprise CLI or API-key
fallbacks.

Changes 6 and 7 are conditional on their corresponding Phase 0 authorization.
Do not create a product adapter merely to represent a backend already shown to
be unsafe or inoperable.

### Change 1 — correct resolved-model quorum

- Record a provider-neutral primary resolved identity on every completed turn.
- Implement versioned final-quorum evaluation from resolved identities.
- Add alias-collision, missing-identity, auxiliary-model, and fallback tests.
- Update accepted-plan and human-gate rendering.

Exit criterion: two requested aliases resolving to one primary model cannot
produce a ready council, while existing accepted records remain renderable.

### Change 2 — align documentation and freeze the adapter/event contract

- Update `docs/agentCouncil.md` for implementation campaigns, current charter,
  retry/resume, chat, budgets, and the Claude-only state at that point.
- Define the provider-neutral event vocabulary and explicitly preserve its
  existing bounded, ephemeral retention contract.
- Define the durable participant-history projection from existing turn
  records and the turn-ID/event-ordinal deduplication contract.
- Add conformance tests that run the existing Claude adapter through every
  planning and implementation result schema.
- Add tests for large patch output, repair, failure mapping, model identity,
  teardown, and chat.
- Record the current Claude behavior as the regression baseline.

Exit criterion: apart from the separately reviewed quorum correction, Claude
behavior is unchanged and tests and user documentation describe the same
feature.

### Change 3 — provider registry, controller, routes, and capabilities

- Add the registry and Claude descriptor.
- Route controller connection construction, provider validation, and
  capabilities through it.
- Generalize route guards to selected or recorded providers while preserving
  their pre-registration and pre-spend ordering.
- Preserve the existing one global per-provider concurrency ceiling applied
  separately to each provider; do not invent provider-specific policy values
  until a real requirement exists.
- Preserve response compatibility where possible.

Exit criterion: Claude-only campaigns remain behaviorally unchanged, and
generic controller/route/capability code no longer assumes Claude except in
intentional compatibility constants and tests.

### Change 4 — provider-scoped credentials, access, and egress

- Generalize credential evidence to provider-owned schemas and minimum
  authenticating documents, including Claude's required non-secret scopes.
- Replace the runtime's single access/stager fields with provider-indexed
  records.
- Provision and tear down provider-scoped networks, proxies, and allowlists.
- Falsify cross-provider access and retain runtimes/tombstones while any
  teardown remains indeterminate.
- Update source-coupled falsification checks and generated inventories with
  their repository tools whenever moved code changes fingerprints.

Exit criterion: the Claude path still passes unchanged, each provider can
receive only its own credential and endpoints, and no generic lane reads the
shared login store.

### Change 5 — provider-aware chat, dispatch, and event normalization

- Route chairbot preparation and message execution through the recorded
  provider.
- Move native-event parsing, result/model/usage extraction, and failure
  classification behind provider-owned operations.
- Make the frontend consume only the normalized event contract.
- Preserve the bounded event-ring roll-off UI and add the durable settled-turn
  history beneath each participant tab without introducing a durable raw or
  normalized event transcript.
- Exercise Claude campaign turns and chat as the regression provider.

Exit criterion: Claude planning, implementation, repair, retry/resume, event
display, and chairbot chat remain behaviorally unchanged with no native Claude
event assumptions in generic frontend or chat logic; after total ring eviction
and after a hub restart, every completed participant tab still shows its
settled turn outcomes.

### Change 6 — Codex product adapter

- Build a deterministic fake Codex CLI for exhaustive ordinary tests.
- Implement `CodexRunnerConnection` and its provider-owned operations from the
  accepted Phase 0 findings.
- Add capability/model discovery and provider-aware frontend disclosure.
- Enable `codex` only behind matching immutable-image evidence.
- Exercise planning, implementation, repair, retry/resume, and chat
  exhaustively with the fake and through a small real paid smoke campaign.

Exit criterion: fake-runner coverage exercises all Codex campaign paths, one
bounded real Codex campaign proves the integrated path, and all credential,
instruction, containment, and teardown checks pass.

### Change 7 — Gemini product adapter

- Build a deterministic fake Antigravity CLI for exhaustive ordinary tests.
- Implement `AntigravityRunnerConnection` and its provider-owned operations
  from the accepted Phase 0 findings.
- Add model aliases/discovery, routing disclosures, and token-expiry handling.
- Enable `gemini` only behind matching immutable-image evidence.
- Exercise planning, implementation, repair, retry/resume, and chat
  exhaustively with the fake and through a small real paid smoke campaign.

Exit criterion: fake-runner coverage exercises all Gemini campaign paths, one
bounded real Gemini campaign proves the integrated path, and all credential,
instruction, containment, expiry, and teardown checks pass.

### Change 8 — multi-provider hardening and documentation

- Exercise every pairwise and three-provider campaign combination with
  deterministic fakes for both planning and implementation.
- Treat the bounded real Codex and Antigravity adapter smokes as the paid
  provider evidence. Exercise the mixed-provider orchestration with
  deterministic fakes rather than spending a second copy of each provider's
  evidence budget on one composite campaign.
- Falsify concurrency ceilings and cross-provider egress with local
  stand-ins.
- Test one provider failing while the others finish visibly.
- Test pause, stop, restart, retry, and chat with mixed providers.
- Update user, developer, security, and revocation documentation.
- Complete browser and manual walkthroughs.

Exit criterion: no unresolved critical/high security finding, all mandatory
lanes pass, paid work remained within its approved budget, and every unverified
provider limitation is visible in product and release notes.

## 13. Test plan

### 13.1 Pure and unit tests

For each provider adapter:

- argv contains only fixed flags, server instructions, and validated model;
- user, researcher, peer, plan, patch, and transcript text appear only on
  stdin/quoted channels;
- configuration cannot supply an endpoint or extra tool;
- every native event maps to a bounded normalized event;
- malformed/truncated JSONL fails visibly;
- terminal success without a structured result is not success;
- authentication, rate limit, network, timeout, stall, output, OOM, and schema
  failures receive the correct class;
- requested and resolved identities remain distinct;
- usage is recorded only when reported;
- result repair uses a fresh runner and the same provider/model;
- credential archives contain exactly the approved fields and ownership;
- credential host files are removed across success and exceptions;
- capability and version probes cannot read or mutate the project-owned login
  store.

Registry/controller tests:

- unknown provider refused before registration;
- selected-provider gates all run before paid work;
- an unrelated unavailable provider does not block;
- per-provider access provisions once and tears down independently;
- one provider teardown uncertainty retains the runtime and durable tombstone;
- retry/resume reconstruct the same provider mapping from the record;
- the global ceiling and shared per-provider ceiling both hold across
  concurrent campaigns;
- mixed-provider synthesis fallback selects only eligible recorded
  participants.

Participant-history tests:

- settled-turn history is derived only from durable campaign turn records;
- a participant with all live events evicted still shows completed and failed
  turn outcomes;
- the same history appears after durable campaign reload with an empty ring;
- repair attempts, requested/resolved identity, usage, and failure classes are
  attributed to the correct turn;
- live events and settled history deduplicate by server-minted turn ID and
  turn-local ordinal;
- patches and rejected payloads remain bounded and use their dedicated
  artifact or diagnostic presentation rather than expanding the tab without
  limit;
- no native provider event dictionary is added to the durable checkpoint.

Quorum tests:

- distinct requested aliases, same resolved model: shortfall;
- distinct providers, same model text: distinct pairs;
- unresolved identity: shortfall;
- one participant with two auxiliary models: one quorum identity;
- model fallback is recorded and evaluated from the final primary identity;
- old campaign records remain renderable.

Chat tests:

- every provider can chair;
- correct credential and egress selected from the chair participant;
- full transcript sent as quoted material;
- provider failure does not mutate campaign decisions;
- idle/absolute expiry destroys and proves the provider runner absent;
- close during an in-flight message settles honestly.

### 13.2 Docker-live tests

Use fail-closed test-owned executables and provider stand-ins. Each fake must
list every supported command/event explicitly. Test:

- runner created from immutable image and snapshot copied 1000:1000;
- CLI executes only inside the disposable runner;
- detached, signal-resistant descendants disappear with runner destruction;
- DNS, IPv4, IPv6, redirects, and direct-IP escapes fail;
- only the selected provider stand-in is reachable;
- fake informational probes that attempt home/config/cache writes remain
  confined to bounded runner tmpfs and leave project stores byte-equivalent;
- memory, CPU, disk, wall-clock, stall, and output limits settle correctly;
- Docker daemon restart/orphan recovery retains indeterminate state until
  absence is proven.

### 13.3 Paid feasibility ceremonies and smoke budget

Real provider calls establish facts that a fake cannot: authentication with a
narrow credential, instruction precedence, provider egress, actual event/model
identity, expiry behavior, and source-login non-interference. They are not the
exhaustive behavioral test matrix.

Before each paid campaign, set and record an explicit call/token or monetary
budget. Run against a user-owned nonsensitive scratch project that never enters
source or tests. During Phase 0 for each candidate provider:

1. Record immutable image and CLI version.
2. Record the source credential schema without secret values.
3. Create the minimum credential copy and exclude refresh material.
4. Run the smallest headless and tool-using turns that prove authentication,
   charter priority, event settlement, structured output, and primary-model
   identity.
5. Exercise representative provider, network, expiry-bound, and forced-destroy
   failures; use local stand-ins for the exhaustive egress/budget matrix.
6. Confirm the project login remains usable and was not rotated by the runner.
7. Confirm staged files, runner, proxy, and network are absent.
8. Confirm logs and durable records contain no secret.
9. Write or update machine-local evidence only after every release gate passes.

For Codex, explicitly assert that the staged archive contains no API-key field,
refresh token, ID token, email address, or unapproved decoded claim. Treat the
reported 240-hour host access-token lifetime as a hypothesis until the
project-owned access-token-only runner succeeds.

After product integration, run one bounded real smoke campaign for each newly
added provider. Exercise every campaign kind, role, repair path, provider
pairing, and three-provider combination with deterministic fakes instead of
multiplying paid real campaigns or adding a separately paid mixed campaign.
Re-run a paid ceremony only when its pinned image, CLI, credential schema,
instruction mechanism, or egress evidence changes, or when the evidence record
reaches its defined freshness limit.

Ordinary CI must never spend provider credits or depend on a paid account.

### 13.4 Browser tests

Cover:

- three provider labels and availability reasons;
- discovered versus routed/unverified models;
- provider-specific effort options;
- mixed participant selection and chairbot choice;
- content-recipient, credential, billing, remote-host, and revocation
  disclosures;
- provider-specific login expiry/refusal;
- normalized participant consoles;
- completed participant history after total ring eviction and after simulated
  hub reload;
- requested/resolved/fallback model display;
- mixed-provider planning and implementation journeys;
- retry, pause, stop, resume, human gates, and chairbot chat.

After any JS change, run the required browser lane and perform the manual
screen walkthrough described in `docs/developers.md`. A green Python suite is
not frontend verification.

## 14. Required repository verification

After each implementation change, follow the repository's mandatory lanes.
At minimum:

```text
python -m pytest tests/ -q --ignore=tests/testContainerBuildIntegration.py
python -m pytest tests/testArchitecturalInvariants.py -v
pip install -e '.[browser]'
python -m playwright install chromium
python -m pytest tests/browser -m browser
```

Also run the relevant Docker-live council tests with the daemon-required
environment enabled. Do not interpret skipped live tests as success.

If Python modules, routes, mutation capabilities, or style bindings change,
regenerate only the repository-owned inventories with their documented tools;
never hand-edit a generated ledger.

## 15. Security review checklist

Before enabling each provider, falsify every item:

- [ ] CLI is present in the exact immutable runner image.
- [ ] CLI runs as the unprivileged user.
- [ ] No provider process runs in the active project container.
- [ ] Snapshot excludes agent docs, credentials, VCS internals, and oversized
      excluded files according to the current snapshot contract.
- [ ] Charter occupies the provider's verified highest-priority channel.
- [ ] Untrusted text cannot enter argv, config, endpoint, header, path, or tool
      selection.
- [ ] Runner receives only its provider's approved credential fields.
- [ ] No refresh token enters a runner.
- [ ] No source API-key field, ID token, email address, or unrelated decoded
      claim enters the runner or any observable output.
- [ ] Capability/version probes cannot read or mutate a persisted project or
      host login store.
- [ ] Provider cannot reach another provider's endpoint.
- [ ] Provider cannot bypass proxy/DNS restrictions.
- [ ] Credential, paths, headers, and raw provider diagnostics are redacted.
- [ ] Output, time, stall, memory, CPU, and disk limits are enforced.
- [ ] Runner and descendants are proven absent or visibly quarantined.
- [ ] Project login remains usable and unmodified.
- [ ] Provider identity and billing path are disclosed before launch.
- [ ] Remote execution host is disclosed.
- [ ] Revocation guidance names the provider-side action.
- [ ] Quorum uses distinct resolved primary models.

## 16. Acceptance criteria

The multi-provider work is complete only when:

1. Claude, Codex, and Gemini appear through one capability/registry path.
2. A project image lacking an overlay reports that provider unavailable.
3. Each enabled provider has current image-bound credential evidence.
4. No provider requires a refresh token in a council runner.
5. Every deliberation runner can reach only its own provider.
6. All three adapters implement the existing turn lifecycle and normalized
   event contract.
7. Each provider can complete planning and implementation roles, including
   structured repair and large patch output.
8. Each provider can serve as chairbot in bounded chat.
9. Retry, resume, pause, stop, shutdown, release, and crash recovery remain
   provider-neutral and truthful.
10. Requested aliases and primary resolved model identities are both recorded.
11. Final quorum cannot be satisfied by two aliases resolving to one model.
12. Deterministic fake campaigns pass in every pairwise combination and with
    all three providers; one bounded real adapter smoke per newly added
    provider passes before release.
13. Provider failure never disappears from the record or becomes agreement.
14. A completed participant tab remains reviewable after total event-ring
    eviction and after a hub restart, using durable settled-turn records rather
    than a persisted raw provider stream.
15. No secret or credential-derived personal identifier appears in logs,
    events, campaign records, artifacts, browser payloads, or test fixtures.
16. All required Python, architectural, Docker-live, browser, and manual lanes
    pass with no silent skips.
17. User documentation describes the actual provider availability, billing,
    credential risk, model routing, event retention, and revocation behavior.

## 17. Stop conditions

Stop implementation and return for a security/product decision if any of the
following occurs:

- Codex or Antigravity requires a refresh token for the account runner.
- A provider lacks a maintainable highest-priority charter channel.
- Required provider traffic cannot be constrained to a fixed reviewed
  hostname set.
- A project-controlled config can redirect provider traffic or enable
  unreviewed tools despite isolation flags.
- The primary resolved model cannot be distinguished from auxiliary/routed
  models, making honest quorum impossible.
- A runner or descendant can survive the existing destroy-and-prove lifecycle.
- Mixed-provider support requires widening access from one provider credential
  per runner to the shared login store.
- Any separately proposed direct API backend would require exposing its API
  key to model-controlled code rather than retaining it on the host.

Do not reinterpret a failed gate as permission to weaken containment. The
acceptable outcomes within this plan are to fix the provider-specific runner
mechanism or leave that provider visibly unsupported. A server-mediated API
backend begins only after a separate plan and product/security approval.
