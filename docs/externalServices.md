# External-service integrations: Overleaf, GitHub, Zenodo

All three integrations are now landed (Overleaf first, then GitHub,
then Zenodo). This document captures the patterns that survived
three concrete implementations — the ones you can rely on when adding
a fourth service — and the traps that only become visible after you
have built more than one.

It is written to be **actionable**, not exhaustive. If a section seems
too short, that's intentional: the full source is the source of truth.

## Mental model

Every external-service integration in vaibify is four concentric layers,
ordered from the user's action inward:

1. **Frontend modal** (IIFE JS) — connect, target selection, per-file
   diff, confirm.
2. **FastAPI route** — HTTP endpoint, pydantic validation, dispatches
   to layer 3.
3. **Host-side dispatcher** (`vaibify/gui/syncDispatcher.py`) — the
   mediator that stitches together host-side operations (mirror
   refresh, digest computation, credential lookup) with container-side
   operations (running the actual push inside the container).
4. **Container CLI** — a self-contained Python script shipped into
   `/usr/share/vaibify/` that performs the network write (git push, API
   upload). Imports **only** stdlib + keyring + a handful of allowed
   adapters.

The **container/host boundary is load-bearing**: code that runs inside
the container cannot import `vaibify.*` because vaibify is not
installed in the container. Anything that needs vaibify internals
(workflow manager, route helpers) lives host-side. Respect this
boundary religiously — several rounds of debugging went into
restoring it.

## What to reuse from Overleaf

These modules and patterns are **ready to generalize** as-is:

### Token + auth plumbing
- `vaibify/reproducibility/overleafAuth.py` — `fsWriteAskpassScript`
  writes a mode-700 temp file that the git subprocess consults for
  credentials; the token never touches argv or environment. Reuse for
  GitHub directly. For Zenodo (REST API, not git) the askpass pattern
  doesn't apply, but the mode-600 temp-file discipline does.
- `vaibify/config/secretManager.py::fnStoreSecret / fsRetrieveSecret /
  fbSecretExists / fnDeleteSecret` — host OS keyring backend.
  **Change one thing**: the current Overleaf integration uses a single
  keyring slot `vaibify:overleaf_token`. This was a known smell (audit
  finding #7) and will collide across projects. For GitHub and Zenodo,
  namespace by service + project: key
  `<service>_token:<projectOrRepoId>`. Do this from the start.

### Error classification
- `syncDispatcher.fdictClassifyError` has extensible pattern lists
  (`_LIST_AUTH_PATTERNS`, `_LIST_RATE_LIMIT_PATTERNS`, etc.). Add
  service-specific patterns alongside; keep the dict shape
  `{sErrorType, sMessage}` identical across services so the frontend's
  `_DICT_SYNC_ERROR_MESSAGES` can stay DRY.
- `syncDispatcher.fsRedactStderr` (via overleafMirror) redacts
  credentials from stderr before surfacing to the UI. Always run
  service-boundary output through it or an equivalent.

### Git hardening (GitHub)
Every `git clone`, `git fetch`, and `git ls-remote` in vaibify must
carry these flags:
```
-c protocol.file.allow=never
-c protocol.allow=user
-c core.symlinks=false
-c submodule.recurse=false
```
and `--no-recurse-submodules` on clones. These defend against
malicious-repo attacks (`.gitmodules` with `file://` URLs,
cross-tree symlinks, hook execution). The canonical list lives at
`vaibify/reproducibility/gitHardening.py::LIST_GIT_HARDENING_CONFIG`
and is imported by `gui.gitStatus`, `reproducibility.overleafMirror`,
and `gui.syncDispatcher`. `reproducibility.overleafSync` keeps a
local copy because it ships into the container as a standalone
script — keep the two lists in lockstep.

### Credential helper scoping
Never mutate the container's or host's **global** git config. Always
use `-c credential.https://<host>/.helper=...` inline on the single
git command. The Overleaf implementation originally wrote a global
helper; removing it was a security fix. Don't repeat the mistake.

### Path validation (defense in depth)
Validate at three layers, every time:
1. **Route** (`syncRoutes.py`): pydantic types + explicit
   `fnValidatePathWithinRoot` against `WORKSPACE_ROOT` for every
   file path in the request. Also reject `\x00`, leading `/`, and
   `..` segments for target directories.
2. **Dispatcher** (`syncDispatcher.py`): validate the projectId /
   repoId / conceptRecId regex before it reaches any filesystem or
   shell.
3. **Container CLI** (`overleafSync.py`): validate again before the
   `pathlib.Path` join, because `Path('/tmp/clone') / '/Figures'`
   evaluates to `/Figures` (pathlib's absolute-RHS semantics — a
   silent misroute trap).

Symlink handling: on push, refuse any source where `os.path.islink`
is True. On pull, after clone, realpath-compare every file to the
repo root and refuse anything that escapes. Always pass
`follow_symlinks=False` to `shutil.copy*`.

### Host/container digest computation
When the frontend sends container-absolute paths to the server, the
server cannot compute digests host-side — those files don't exist
there. `syncDispatcher.fdictComputeContainerDigests` runs a single
`docker exec python3 -c "..."` that hashes all requested files in
one round-trip. Same shape will work for GitHub (git blob SHAs) and
for Zenodo (file content SHA256 — whatever Zenodo returns in its
file-list API; match that algorithm).

### Route layer patterns
Every state-changing endpoint is behind `dictCtx["require"]()` (CSRF
session token) AND the new `_fbRequestHasAllowedHost` middleware (DNS
rebinding defense). Keep both.

### Frontend unified push modal
The current push modal (`scriptSyncManager.js`) is service-aware:
- For Overleaf it renders a target-directory input, a diff summary
  (new / overwrite / unchanged with greyed-unchanged rows), a
  case-collision banner if applicable, a conflict banner with
  "Overwrite anyway" gating, and a "Push All" / "Push Selected" pair.
- For GitHub and Zenodo today it renders a simpler list without diff.

When you add GitHub, reuse the Overleaf flow — GitHub diff maps
cleanly. For Zenodo the diff concept is slightly different (deposits
are bundled and versioned; the unit of comparison is usually
"existing file with same name" vs "new file"). The modal can still
host the same inline-status UI.

## Gotchas likely to recur

### Case folding (Overleaf-specific, but watch for echoes)
Overleaf's underlying storage is case-insensitive. Its git bridge
surfaces both case-variants (`Figures/` and `figures/`) as separate
tree entries with the same tree SHA. This produced a spectacular
"12 unchanged" debugging session. **Detect and warn at the adapter
boundary**: `overleafMirror.flistDetectCaseCollisions` returns a list
of `{sLocalPath, sTypedRemotePath, sCanonicalRemotePath}`. The diff
endpoint returns these plus `sSuggestedTargetDirectory`, and the
frontend shows a banner with a one-click "Use canonical case" button.

**GitHub is case-sensitive** in its storage, so this specific quirk
probably won't surface. But: don't assume; Windows-hosted GitHub
repos through GitHub Desktop can introduce case weirdness, and macOS
development filesystems are case-insensitive. Add the detection
anyway; it's cheap.

**Zenodo** is REST-API-only with a flat file list per deposition, so
no directory cases at all — irrelevant for Zenodo.

### Silent no-op success (universal)
The most dangerous error class: a push that "succeeds" but changed
nothing remotely. Overleaf hit this when `git status --porcelain`
came back empty because files copied to the wrong place (pathlib
absolute-join trap) or were byte-identical to existing remote files.
**Every container CLI that mutates remote state must emit an
unambiguous status signal on stdout**: overleafSync.py emits
`PUSH_STATUS=pushed` or `PUSH_STATUS=no-changes`, parsed host-side.
Apply the same pattern to GitHub push (commit count) and Zenodo
upload (did a new version get published? was anything new added to
a draft?).

### Layer-cache masking of base-image bugs
Docker caches RUN layers aggressively. Once a base layer succeeds,
it gets reused forever until the cache is invalidated. We had an
apt/gpgv sandbox bug latent in every Ubuntu 24.04 base image we
built; it only surfaced when the user clicked Force Rebuild
(`--no-cache`) after weeks of cached-layer reuse. **Lesson**: don't
interpret "this has been working for weeks" as "the layer is
correct." When adding GitHub/Zenodo, if you change anything in the
Dockerfile (new packages, new config), test with Force Rebuild at
least once before shipping.

### Container CLI hot-patching during dev
For fast iteration: `docker cp vaibify/reproducibility/<cli>.py
<container>:/usr/share/vaibify/<cli>.py` is the answer. The
Dockerfile's `COPY` is the permanent solution. Expect this cycle
during development. Don't be fooled when your host-side tests pass
but the container still runs an older CLI — `docker exec <cid>
python3 /usr/share/vaibify/<cli>.py --help` to check.

### DNS rebinding / Host-header checks
Any localhost-bound server is vulnerable to DNS rebinding. We added
`fbIsAllowedHostHeader` middleware that rejects requests whose Host
header isn't `127.0.0.1:<port>`, `localhost:<port>`, or `[::1]:<port>`.
If you add any new endpoint, it gets this defense for free; don't
undo it.

### Stderr leaks tokens
Git sometimes echoes URLs with embedded credentials on auth failure:
`fatal: Authentication failed for 'https://git:<token>@github.com/...'`.
The existing redactor handles URL creds + "password/token/bearer/authorization"
keyword lines. GitHub's REST API may surface raw tokens in different
error shapes (JSON bodies with `"message"` fields); extend the
redactor to cover those before exposing to the UI.

### Pathlib absolute-RHS trap
`Path("/tmp/clone") / "/Figures"` is `/Figures`, not
`/tmp/clone/Figures`. Pathlib discards the left side when the right
starts with a separator. Every target-directory validator MUST
reject leading slashes before any join. This was the root cause of
Overleaf's "phantom push with no remote change" bug.

## What to modularize vs. what to write fresh

### Credential persistence: where each service's token lives

| Service | Token stored in | Set by | Persists across container rebuild? |
|---|---|---|---|
| Overleaf | host OS keychain (`fnStoreSecret("overleaf_token", ...)`) | [syncRoutes.py:649](../vaibify/gui/routes/syncRoutes.py#L649) | Yes — host-side |
| GitHub | container keyring (`fnStoreCredentialInContainer`) | [syncDispatcher.py:867](../vaibify/gui/syncDispatcher.py#L867) | Yes — via named credentials volume |
| Zenodo | container keyring (`fnStoreCredentialInContainer`) | [syncDispatcher.py:867](../vaibify/gui/syncDispatcher.py#L867) | Yes — via named credentials volume |

The GUI's Restart and Rebuild actions both do `docker rm + docker
run`, so in-container writable layers are ephemeral. Container-side
credential stores must live on a named volume or they vanish every
time the user Rebuilds. The fix is a second named volume next to the
workspace mount:
[`containerManager._fnAddCredentialsVolume`](../vaibify/docker/containerManager.py)
mounts `{projectName}-credentials` at
`/home/<user>/.local/share/python_keyring/`. The Dockerfile
pre-creates the directory with mode 700 and the container user as
owner; Docker's copy-on-mount copies that into the volume the first
time it's used, and every subsequent container recreation sees the
existing tokens.

### Already consolidated (three-service common core)

These lifted cleanly once the third service was in place:

- **`fsWriteAskpassScript`** — the on-disk temp-file machinery
  (mkstemp + chmod 700 + return path) now lives in
  [`reproducibility/askpassHelper.py::fsWriteExecutableScript`](../vaibify/reproducibility/askpassHelper.py).
  Service-specific askpass source builders stay in
  [`githubAuth.py`](../vaibify/reproducibility/githubAuth.py) and
  [`overleafAuth.py`](../vaibify/reproducibility/overleafAuth.py).
- **`LIST_GIT_HARDENING_CONFIG`** — single list of `-c` flags at
  [`reproducibility/gitHardening.py`](../vaibify/reproducibility/gitHardening.py),
  imported by `gui.gitStatus`, `reproducibility.overleafMirror`, and
  `gui.syncDispatcher`. `overleafSync.py` keeps a local copy because
  it ships into the container standalone.
- **`ZenodoClient`** — the host-side Zenodo API wrapper is also
  [shipped into the container](#ship-the-host-client-dont-reimplement)
  at `/usr/share/vaibify/zenodoClient.py`. The in-container archive
  script imports it flat and calls methods instead of
  reimplementing the HTTP surface. One bearer-token path for both
  host and container.

### Still candidates to extract (when a fourth service lands)

- `fsRedactStderr` helper (overleafMirror / overleafSync; the
  container-shipped copy is deliberately divergent).
- `fnValidateTargetDirectory` (currently in `overleafSync.py`).
- `fnValidatePullRelativePath`.
- `fdictComputeContainerDigests` (the digest-compute docker-exec helper).
- The `PUSH_STATUS=` + `HEAD_SHA=` stdout protocol.
- Host header / session token middleware (already shared).

### What we wrote fresh per service

**GitHub** — git-native like Overleaf but with a different authentication
story (`gh auth token` fallback, per-repo keyring slots). We wrote
[`githubAuth.py`](../vaibify/reproducibility/githubAuth.py),
[`gitRoutes.py`](../vaibify/gui/routes/gitRoutes.py), and push flows in
`syncDispatcher.py` fresh, stole the hardening flags and askpass
machinery, and left `overleafMirror.py` Overleaf-specific (its quirks
are in the docstring for a reason). No shared `serviceMirror.py`
emerged — the only true overlap was the hardening flags, and those
lifted cleanly without a wrapper.

**Zenodo** — not git. REST API with Bearer tokens, deposits, and
newversion semantics. First pass implemented the whole API surface
inline inside a base64-encoded container script because
`ZenodoClient` needed host-only imports. That left us with two
implementations of the same Bearer-token logic, which is the
cross-cutting concern summarized below.

### Ship the host client, don't reimplement

When a host module wraps an external REST API cleanly and a
container script needs the same API, **ship the host module into
the container** (the `overleafSync.py` flat-file pattern) instead
of reimplementing the HTTP calls inline. The cost is one line in
`docker/Dockerfile` and one entry in
[`fnCopyContainerScripts`](../vaibify/cli/commandBuild.py); the win
is a single source of truth for:

- Bearer-token auth construction
- HTTP error classification (`_fnCheckResponse` → typed exceptions)
- Response-shape edge cases (`links.latest_draft`, nested `id`
  fields, 204 handling)
- Future retry / timeout policy

Prerequisites for ship-in: the module must import **only**
container-resident packages at top level (no `vaibify.*`, no
optional deps like `tqdm` unless you lazy-import them). Use the
same `try: from vaibify.reproducibility.X import ... except
ImportError: from X import ...` fallback pattern overleafSync uses
for its siblings.

Zenodo landed this second pass in commit `d0358c3`: `ZenodoClient`
grew optional `sToken` / `sBaseUrl` kwargs, tqdm moved to lazy
imports, the archive script dropped from ~90 lines of inline HTTP
to ~55 lines of method calls.

### Do NOT modularize yet (wait for four)

These felt common across Overleaf and GitHub but may not transfer
when the fourth service lands:

- `flistDetectCaseCollisions` — Overleaf-specific case folding;
  didn't need it for GitHub.
- The specific `OverleafBehavior` fixture pattern — worth
  replicating per service, but don't force a shared API.
- Target-directory selection UI — GitHub has branches, not
  directories; Zenodo has no directory concept at all.

**Rule of thumb**: three concrete instances is the right time to
abstract. Two is premature; four starts to feel like you're fighting
divergence.

## Architectural invariants to respect

Run these after every change:
```
python -m pytest tests/testArchitecturalInvariants.py -v
```

The ones most relevant to new services:
- `testNoRawFetchInFeatureModules` — use `VaibifyApi.*` wrappers in
  the frontend.
- `testDirectorUsesOsPath` — host-side Python uses `os.path`.
- `testLeafModuleHasNoIntraPackageImports` — don't add vaibify-gui
  imports to any file that ships into the container.
- `testEveryJsFileIsRecognizedAsIIFE` — register new JS modules in
  `index.html` and follow the IIFE convention.

## Testing discipline

- **Mock `subprocess.run` at the module boundary.** Never invoke real
  git or make real network calls in unit tests.
- **Use `tmp_path` fixtures** for anything filesystem-related.
- **Behavior-adapter tests** (`testOverleafBehavior.py`): static
  fixture strings that simulate the external service's output,
  asserting the adapter interprets them correctly. These fail loudly
  when the external service changes. Create one of these per service.
- **Route tests** use FastAPI's TestClient with sessions —
  `testSyncRoutesCoverage.py` is the model.
- **Don't weaken existing tests** to make new ones pass. If a security
  fix makes an existing test's input now invalid, update the test
  narrowly to use valid input that still exercises the same behavior.

## Related files

Shared infrastructure:
- [vaibify/reproducibility/gitHardening.py](../vaibify/reproducibility/gitHardening.py) (the `-c` flags)
- [vaibify/reproducibility/askpassHelper.py](../vaibify/reproducibility/askpassHelper.py) (mode-700 temp-file writer)
- [vaibify/config/secretManager.py](../vaibify/config/secretManager.py) (host OS keyring)
- [vaibify/gui/syncDispatcher.py](../vaibify/gui/syncDispatcher.py) (host-side dispatcher)
- [vaibify/gui/routes/syncRoutes.py](../vaibify/gui/routes/syncRoutes.py)
- [vaibify/gui/pipelineServer.py](../vaibify/gui/pipelineServer.py) (pydantic models, host-header middleware)
- [vaibify/gui/workflowManager.py](../vaibify/gui/workflowManager.py) (sync-status persistence)
- [docker/Dockerfile](../docker/Dockerfile) (ships the container CLIs)
- [vaibify/cli/commandBuild.py::fnCopyContainerScripts](../vaibify/cli/commandBuild.py) (stages ship-ins)

Overleaf:
- [vaibify/reproducibility/overleafAuth.py](../vaibify/reproducibility/overleafAuth.py)
- [vaibify/reproducibility/overleafMirror.py](../vaibify/reproducibility/overleafMirror.py)
- [vaibify/reproducibility/overleafSync.py](../vaibify/reproducibility/overleafSync.py) (container CLI)
- [vaibify/reproducibility/latexConnector.py](../vaibify/reproducibility/latexConnector.py) (container CLI helper)

GitHub:
- [vaibify/reproducibility/githubAuth.py](../vaibify/reproducibility/githubAuth.py)
- [vaibify/gui/routes/gitRoutes.py](../vaibify/gui/routes/gitRoutes.py)
- [vaibify/gui/gitStatus.py](../vaibify/gui/gitStatus.py) (host-side status)
- [vaibify/gui/containerGit.py](../vaibify/gui/containerGit.py) (container-side status)

Zenodo:
- [vaibify/reproducibility/zenodoClient.py](../vaibify/reproducibility/zenodoClient.py) (host module AND container CLI — shipped in)
- [vaibify/reproducibility/dataArchiver.py](../vaibify/reproducibility/dataArchiver.py) (host-side archive orchestration)

Frontend:
- [vaibify/gui/static/scriptSyncManager.js](../vaibify/gui/static/scriptSyncManager.js)
- [vaibify/gui/static/scriptOverleafMirror.js](../vaibify/gui/static/scriptOverleafMirror.js)
- [vaibify/gui/static/scriptModals.js](../vaibify/gui/static/scriptModals.js)

Tests:
- [tests/testOverleafAuth.py](../tests/testOverleafAuth.py)
- [tests/testOverleafMirror.py](../tests/testOverleafMirror.py)
- [tests/testOverleafBehavior.py](../tests/testOverleafBehavior.py)
- [tests/testOverleafSync.py](../tests/testOverleafSync.py)
- [tests/testOverleafSyncExtended.py](../tests/testOverleafSyncExtended.py)
- [tests/testSyncDispatcherGaps.py](../tests/testSyncDispatcherGaps.py)
- [tests/testSyncRoutesCoverage.py](../tests/testSyncRoutesCoverage.py)
- [tests/testHostHeaderCheck.py](../tests/testHostHeaderCheck.py)

## Known follow-ups from the security audit

These were flagged but deferred during Overleaf's final push. They
will almost certainly bite GitHub and Zenodo too; fix them during
those integrations rather than leaving three partial implementations:

1. **Single keyring slot across projects** — namespace by service +
   project from day one.
2. **Token files leak to `/tmp` on SIGKILL** — add a startup sweep
   of `/tmp/_vc_*tok*` and `/tmp/vc_askpass_*`.
3. **`fsRedactStderr` misses bare-token lines** — when a service
   emits a raw token on a line by itself (no label keyword),
   redaction won't catch it. Consider blanket-replacing the
   just-used token string.
4. **Pydantic models without `extra="forbid"`** — hardening, not
   exploitable.

## Recommended sequence

1. Read this doc.
2. Read the Overleaf implementation top to bottom, especially
   [overleafMirror.py](../vaibify/reproducibility/overleafMirror.py)
   and [syncDispatcher.py](../vaibify/gui/syncDispatcher.py).
3. Read the two relevant plan files in `.claude/plans/` for the
   Overleaf push and mirror plans — they show the level of detail
   expected.
4. Write GitHub first (closer shape to Overleaf), landing in small
   commits.
5. Write Zenodo (different shape entirely).
6. Extract shared helpers into a `serviceAuth.py` / `serviceMirror.py`
   after both are working.
7. Run the security audit prompt (ask the user for the one we used
   on Overleaf) against the new code.

Good luck. The Overleaf round took longer than estimated because of
the seven or eight quirks documented above; budget accordingly for
the next two services.
