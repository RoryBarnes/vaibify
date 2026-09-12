---
name: container-mutations
description: The commit-guard carrier, mutation admission, carrier mode declarations on routes, the typed-read exemption, and the generated mutation/host-capability/style ledgers. Use when adding or changing a route that touches a container, running an exec, writing a file into a container, or when a ledger drift check fails.
---

# Container mutations and the carrier

Every container mutation announces itself. This file is the contract
for how, what a route must declare, and how the generated ledgers that
record it are maintained. A ledger is regenerated, never hand-edited
and never hand-merged.

The repository-wide rules in `AGENTS.md` still apply; this file is
the detail for this subsystem.

**A batched container probe must be split to fit ONE exec argument.**
Both badge probes render their whole path list into a single argument —
one embeds it in the typed-read program, one appends it as a
here-string, and a here-string is part of the command string too.
Linux caps a single argument at 128 KB, so unbatched they stopped
working: `flistContainerPathsExist` RAISED at ~1,845 paths (inside a
carrier worker, which poisons the journal record and **quarantines the
container**), and `fdictComputeBlobShasInContainer` answered `{}`
SILENTLY at ~2,562, so every badge was computed from an empty hash map
and shown as fact. Both measured against a real daemon, 2026-08-30.
`vaibify/docker/execArgumentBudget.py` owns the split; never re-derive a budget
beside it. Two things not to "simplify": the split preserves ORDER
(the existence probe zips answers back onto paths), and a failed batch
collapses the whole blob-sha answer rather than returning the batches
that worked (a partial map reads as a claim about the files it omits).
Any NEW batched probe must go through the same splitter —
`tests/testExecArgumentBudget.py` is the kill-confirmed guard.

**Host→container file writes default to the unprivileged container
user.** Every backend write of a file inside the workspace volume
funnels through `fnWriteFile` / `fnWriteFileViaTar` in
[vaibify/docker/dockerConnection.py](vaibify/docker/dockerConnection.py),
which calls `container.put_archive(tarball)`. The tarball entry's
uid/gid IS the file's owner inside the container, and `tarfile.TarInfo`
natively defaults uid/gid to 0. If that default ever leaks through, the
file lands root-owned and the in-container agent cannot edit it (sudo
is absent by design — commit 426f6b7). The symptom is a researcher's
`git push` failing on `.git/objects/<prefix>` or the agent unable to
modify `project.json` after a backend save. The dispatcher's
`_finfoBuildTarEntry` defaults the stamps to
`_I_CONTAINER_DEFAULT_UID`/`_GID` (1000:1000, locked to the Dockerfile
by `testContainerUserUidIsOneThousand`); any new host→container write
path must preserve that default.
`tests/testArchitecturalInvariants.py::testFnWriteFileDefaultsToContainerUserOwnership`
enforces it.

**Container access has exactly one authority: the lease plus
`dictContainerOwners`.** A container is owned by a per-claim,
server-minted lease (`containerOwnership.fsMintLease`), recorded in the
in-process owner-of-record map `app.state.dictContainerOwners`, keyed by
container **name** (the host flock and the caffeinate keep-alive are both
name-keyed, so the owner map must be too; the WebSocket routes resolve
the Docker container id to a name before the lease lookup). Claim
(`registryRoutes`), the connect handler, and the pipeline WebSocket must
all authorize through the single shared guard
`webSocketAuthorization.fbAuthorizeContainerSession` /
`fiContainerSessionRejectionCode` — never an inlined container-id
membership check. Never reintroduce `setAllowedContainers` (the old
append-only, process-global allow set that leaked authorization for the
whole process lifetime) or treat the shared session token as the browser
*principal* — the shared token is only the trust/CSRF boundary, not the
thing that says *which* browser session owns a container. The
in-container agent authenticates with a **per-container** token
(`OwnerRecord.sAgentToken`, written into that container's
`/tmp/vaibify-session.env`), validated per-container by both
`webSocketAuthorization.fbCheckAgentToken` and the REST
`SessionTokenMiddleware`, so a compromised agent in one container cannot
reach another. Never collapse the agent lane back onto the hub-wide
token. The lease is the exclusivity principal; the
holder payload carries `sStartedIso` for recycle-proof staleness; exactly
one live *pipeline* WebSocket per container is enforced by the
per-container `iLivePipelineConnectionCount` (a duplicate tab that copied
the lease is closed 4409 — after `accept`, via `fnCloseWithCode`, so a
real browser sees the code instead of an unreachable-looking 1006).
Non-pipeline sockets are counted in `iLiveConnectionCount` for liveness
but never budgeted: one session legitimately holds several sockets at
once — budgeting all sockets shipped the Run-Step-always-refused bug
(the terminal, opened on workflow entry, held the only slot; every Run
Step was 4409'd and mislabeled "cannot reach server"). The terminal is
the unbudgeted lane's production caller, so that budget must never be
extended to it. Run exclusivity is additionally
enforced at dispatch for every lane, including the budget-exempt agent
lane: a run arriving while another pipeline action is live in that
container is answered with a `runRefused` event, never started
(`_fbRefuseWhilePipelineTaskLive`). The idle busy-veto reads
`dictContainerOwners.keys()` so the watchdog can never self-SIGTERM a
hub mid-run. The full normative model is the "Single browser session per
container" section of [docs/architecture.md](docs/architecture.md).
Enforced by `testClaimRejectsForeignLease`, `testReleaseRejectsNonOwner`,
`testWebSocketGatesUseSharedAuthorizationGuard`,
`testSetAllowedContainersRemoved`, and
`test_terminal_plus_pipeline_ws_coexist_in_one_session`.

## Container mutations go through the commit-guard carrier

**Arbitrary command execution is always treated as mutating**, because
the primitive cannot know whether the text it was handed reads a file
or deletes a workspace. Inside an enforced lane — an HTTP request
served by `routeScope.ContainerAwareRoute`, or a carrier-launched
durable task — an exec or a container write without a live carrier
admission raises `MutationNotAdmittedError`. Outside one (background
threads, the CLI, direct library use) the gate is a no-op: that
remainder is deliberate and named, never a silent claim of coverage.

**A typed read is exempt only inside its adapter.** Reading a file
means running a program in the container, so guarding the exec would
refuse reads too. Exactly one private method,
`DockerConnection._ftRunTypedRead`, grants the exemption. It takes
an operation NAME from a fixed table, plus a path **or a flat sequence
of paths**, and builds the command itself; it never accepts one. The
sequence form was added on 2026-08-05 for the batched file-existence
probe: the alternative was up to 1000 container round-trips on a
debounced UI path. It widens what the adapter may be *given*, never
what it may be *told to run* — `repr()` of a validated list of strings
is as inert as `repr()` of one. An adapter that forwarded a caller's string would
turn the read carve-out into a general bypass —
`tests/testMutationBoundary.py` fails the build on one that does, and
on a second grant point anywhere, pinning the name through
`S_EXEMPTION_METHOD`.

(This paragraph named `_texecRunAuditedRead` until 2026-08-04, a symbol
that exists nowhere in the repository. The enforcement was always
correct — the test reads the real name — but a security contract whose
stated grant point cannot be grepped is one an agent will conclude does
not exist, and two separate tracks reported it before it was fixed.)

**`tests/mutationInventory.json` carries three records, and only one of
them is completeness-critical.** Regenerate with `python
tools/generateMutationInventory.py --write`; drift-check with
`--check`.

- **Acquisitions** — every import or attribute-load of a member in a
  closed dangerous vocabulary: `subprocess.*` launchers, `os.system` /
  `exec*` / `spawn*` / `popen`, `asyncio.create_subprocess_*`,
  `pty.spawn`, multiprocessing and process pools, Docker client
  constructors and low-level `APIClient` methods, direct Unix-socket
  access, process signalling (`os.kill` / `os.killpg`), and reflection
  (`eval`, `exec`, `sys.modules[...]`, `importlib`, `__import__`,
  dynamic `getattr`). **This is the completeness boundary and it fails
  closed.** Importing `os` is not acquisition; `from os import system`
  is — 33 GUI modules import `os`, so a module-level reading would be
  useless.

  Signalling joined the vocabulary on 2026-08-10 and is worth a
  sentence, because it is the one member that is not command
  authority: a signal cannot make a process do anything new, only stop
  one. It went unrecorded for as long as every signal vaibify sent went
  to a process vaibify had created and was tracking. Host mode changed
  that — `hostCancellation` signals a process group named by a number
  read back out of a journal file, on the researcher's own machine.
  The scope is the namespaced `os` surface only: a bare
  `processChild.kill()` on a Popen handle is not matched, because
  `.kill()` and `.terminate()` are ordinary method names shared with
  threads and test doubles, and matching them by spelling is the defect
  this scanner exists to avoid. The launch that produced such a handle
  is already an acquisition.
- **Use sites** — decoded calls and commands. **Metadata,
  best-effort.** A launch whose argv the scan cannot read becomes a row
  with an UNKNOWN command, never a site that disappears.
- **Dispositions** — the reviewed judgement per module or named
  function: forbidden, guarded, or separately authorized.

Completeness rests on the ACQUISITION, not on decoding the command,
because decoding depends on reading an expression somebody else writes.
The withdrawn host-side director module is the demonstration: its
`subprocess.Popen(sCommand, shell=True)` was the most permissive command
authority under `vaibify/gui/` and produced **zero rows** under the old
design — one blind-spot entry, nothing more.

The drift check fails on an added, removed, duplicated, edited, or
hand-altered row, on acquisition drift, and on blind-spot drift. Three
ratchets may only fall: `I_UNCLASSIFIED_ROW_BUDGET`,
`I_UNDISPOSED_ACQUISITION_BUDGET`, and `DICT_UNRESOLVED_BUDGET`. **The
scanner never decides reachability** — a human judgement recorded
against a fingerprint is honest about being a judgement, where a
scanner's reachability verdict would pretend to be a proof. And a
fingerprint is an identity, never a warrant: for an opaque site the
expression is `subprocess.run(listCommand)` both before and after the
builder filling it is swapped from git to `docker rm`, so a manual
disposition must name the supporting symbols its review relied on.

**A generated ledger is never hand-edited, and never hand-merged.**
`tests/mutationInventory.json`, `tests/hostCapabilityInventory.json`
and `tests/styleInventory.json` are all built by a tool. Their on-disk
layout is load-bearing, not cosmetic — one record per line, no field
holding the length of a list beside it — because a ledger is
regenerated by every branch that touches the source it scans, and a
multi-line record puts git's three lines of context inside a single
record. `tools/ledgerFormat.py` is the single renderer and explains
each property; `tests/testLedgerFormatIsCanonical.py` fails when a
checked-in file is not byte-exactly what its generator writes. That
test exists because every other check parses JSON first and so cannot
see formatting at all: one ledger was rewritten by hand with
`indent=1` against a generator writing `indent=2`, every drift check
stayed green, and the next `--write` reformatted ten thousand lines.

**When a ledger conflicts on merge: resolve per record, then
regenerate.** Each side of a conflict is now a set of whole records,
one per line, so choosing between them is a reading task rather than a
JSON-repair task. Keep every record either side recorded a judgement
on, then rebuild:

```bash
$EDITOR tests/mutationInventory.json          # keep BOTH sides' records
python tools/generateMutationInventory.py --write
python tools/generateMutationInventory.py --check   # must print {}
```

**Do not resolve by taking one side wholesale.** Regeneration carries a
reviewer's judgement forward from *the file on disk* — verified by
stamping a disposition, regenerating, and finding it intact — so
`git checkout --theirs` silently discards every disposition your branch
recorded, and the drift check cannot tell you, because a row reverted
to `UNCLASSIFIED` is a legal row. The ratchets in
`tests/testMutationInventory.py` are the only thing that would notice,
and only if the count crosses a budget.

Regeneration then normalizes ordering and drops anything the scan no
longer finds, so `--check` printing `{}` means the rebuilt file agrees
with the source. Read its `removed` keys before assuming a clean
rebuild: a row your branch deleted takes its recorded review with it. A
row whose enclosing scope moved keeps its review but gets a new
`sScopeFingerprint` — that is the mechanism asking you to re-read the
judgement, not to re-stamp it.

**A route declares its carrier mode, and the declaration authorizes
NOTHING.** `routeScope.ffnDeclareCarrierMode` stamps one or more of
`typed-read`, `mode-a-synchronous`, `mode-b-lock-held`,
`mode-c-durable`, `lifecycle-transaction`, `separate-authority` onto a
handler. A declared route takes a branch with NO admission, so its
handler must open one through a carrier around each logical mutation;
forget one and the primitive raises `MutationNotAdmittedError`. **That
refusal is the proof** — a decorator that pre-admitted the handler
would delete it, which is the `bAgentSafe` mistake one level up.
`testDeclaringMintsNoAdmission` drives a declared route over real HTTP
with the owner map keyed by a name != the container id and asserts the
real gate refuses. Why each mode exists, and what the migration found,
is in [docs/architecture.md](docs/architecture.md) — "Container
mutations announce themselves".

**Two rules that are easy to undo by accident.** A refusal is not an
I/O error: `MutationNotAdmittedError` and `CommitRefusedError` derive
from `ControlPlaneRefusalError(Exception)`, never `PermissionError` —
they used to, and every `except OSError` in the package swallowed them,
which is how a refusal came to silently downgrade a reproducibility
badge. And a carrier worker must not raise an expected 4xx/502: that
poisons its journal record and quarantines the container. Carry it back
through `routeContext.fdictCarryARefusalBackInsteadOfRaising` and
re-raise outside, after the record settles. A genuinely half-finished
write still poisons, correctly.

**For the current coverage, run the command — do not trust a number
written here.**

```bash
PYTHONPATH=. python tools/carrierIntentAudit.py
```

It prints every container-scoped route with its declaration, or
`(awaiting)`. The counts used to be prose in this file and went stale
four times in one session, because they change on every batch while the
sentence does not. Two routes are `APIWebSocketRoute`s that
`app.router.route_class` never governs, so the resolved population and
the governed one differ by two; the command reports the governed one.
The migration was scoped to mutating routes, so the awaiting list
bottoms out at the read-only routes rather than empty.

**There is no production observation point**: nothing
under `vaibify/` records a carrier observation, so
`tools/carrierIntentAudit.py` compares only what the suite drove, and
an empty violation list is not compliance —
`flistSelectDeclarationsNeverObserved` keeps that visible. An
observation records what its entry point DECLARED, never *which* entry
point it was, so a violation cannot be narrowed between two routes
sharing a declaration; migrating one route at a time is what bounds the
diagnosis. And some mutation-capable rows are **structurally**
unattributable: a primitive bound into `asyncio.to_thread` loses its
row, because inside the worker the frames above it are executor
infrastructure rather than the expression the row records. Its carrier
MODE survives, so the event is still routed correctly — the row is what
is lost, and those must be traced by hand and will never be observed.
Migrating a route can *recover* one, by turning the passed callable into
a direct call the scanner can read; the current set is every
`passed-callable` row in `tests/mutationInventory.json`. The semantic
classification of the inventory is unfinished
and ratcheted: the count may only go down, and it is the input to that
migration, not a substitute for it.

**A busy container refuses a hand-over at once, and names what is busy.**
The lock HOLDER registers its operation kind and target, because an
`asyncio.Lock` knows only that it is held. A transfer never waits for a
drain — waiting spends the capability's window on an operation of
unknown length — and there is no DRAINING phase: a transfer refuses over
any terminal execution nobody has proven dead.
