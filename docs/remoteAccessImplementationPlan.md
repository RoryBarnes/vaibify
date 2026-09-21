# Remote access implementation plan

## Status and decision

Vaibify will support viewing and controlling a remote Vaibify installation
through one local command:

```bash
vaibify remote <ssh-host>
```

The command starts or reuses a Vaibify backend on the remote machine, creates
an SSH tunnel to that backend, obtains a one-time browser bootstrap capability,
and opens the dashboard in the local browser. The remote backend remains bound
to loopback. Vaibify will not expose its HTTP or WebSocket server directly to
the LAN or internet.

The local process is a tunnel and browser launcher, not a second Vaibify
backend. The backend beside the projects is the sole control plane.

A remote session is **its own lane with its own protocol**, not a Docker or
host session viewed from further away. It carries its own connection-continuity
contract, its own terminal semantics, and its own honesty obligations about
which machine the researcher is looking at. Where the local lanes' constants
are wrong for a tunnel, the remote lane sets its own; it does not quietly widen
the local ones.

Remote access is execution-mode-neutral. It selects the Docker or host
execution target through the existing connection router without changing the
browser connection model, and it redefines neither.

Batch-scheduled supercomputers are deliberately outside the first remote
release. Their site-specific service policies, allocation lifecycles, and
scheduler semantics make them a separate product surface, and their users are
not the current target customer. The architecture must nevertheless keep SSH
transport, Docker-versus-host execution mode, and direct-versus-scheduled
placement as independent concepts so a later scheduler integration does not
require replacing the remote access design.

## Verification basis

This revision was checked against `main` at commit `f1892688`, after host mode
landed complete (PRs #50-#73). The file:line citations below were read at that
commit and will drift; treat them as pointers to the mechanism, not as facts to
be trusted after the code moves. Re-read them before implementing.

The previous revision assumed a pre-host-mode codebase. Four of its premises
were falsified by the code as it now stands, and they are why this document
changed shape rather than gaining a section:

1. **The interactive terminal is live in both modes.** Containers regained it
   on 2026-08-11, host mode in PR #67. `gui/routes/terminalRoutes.py:83-113`
   gates and then serves; the old refusal codes are reserved and never emitted.
   "Restore the disabled terminal" is no longer a non-goal because there is
   nothing left to restore.
2. **A dropped connection revokes the researcher's credential in 15 seconds**
   (`gui/sessionLifecycle.py:103`, committed at `:1086-1095`), and a returning
   browser cannot simply re-claim its own live run — the busy veto that
   protects the run also refuses the take-over
   (`gui/containerOwnership.py:875-883`). The previous plan's "the existing
   watchdogs determine when the remote hub may safely retire" was half true and
   dangerously compressed.
3. **A project name may contain a space** (`config/projectConfig.py:575`). The
   commit that admitted it reasoned, correctly at the time, that "no
   `shell=True` exists in the package." OpenSSH always runs its remote command
   through the remote login shell, so this feature is the first thing in the
   codebase to invalidate that premise.
4. **The gated create-suspended/journal/release-gate pattern does not fit a
   detached hub.** Both primitives run to completion, the journal record is
   keyed by project name, and the settle rule requires the holder dead with an
   empty process group — so a never-exiting hub would leave a permanently
   unsettled record that makes its project unclaimable and vetoes both the
   ownership reaper and idle self-exit. The previous revision adopted this
   pattern on review advice; that advice was wrong.

## User experience

### Primary workflow

Given an OpenSSH host alias named `compute-machine`:

```bash
vaibify remote compute-machine
```

Vaibify shall:

1. Select a TCP port that can be used on both the local and remote loopback
   interfaces.
2. Open one SSH connection to `compute-machine` with local port forwarding.
3. Run a fixed Vaibify remote-helper command on that connection.
4. Have the helper start or reuse a compatible loopback-only remote hub.
5. Receive a bounded, versioned startup record containing the remote port and
   a one-time bootstrap capability.
6. Wait until the forwarded local port accepts connections.
7. Open the local browser at a locally constructed URL of the form
   `http://127.0.0.1:<port>/#bootstrap=<capability>`.
8. Remain in the foreground maintaining the tunnel, reconnecting it if it
   drops, until the researcher stops it or the continuity window expires.
   `Ctrl-C` closes the tunnel but does not assert that remote work stopped.

The browser must see `127.0.0.1` with the same port the backend expects. This
preserves the current Host-header, WebSocket-Origin, content-security-policy,
and browser-session checks without weakening them.

The command should accept:

```bash
vaibify remote compute-machine
vaibify remote researcher@compute-machine
vaibify remote compute-machine --port 18050
```

SSH connection configuration belongs in `~/.ssh/config`. Proxy jumps, SSH
ports, identity files, host-key policy, and usernames remain OpenSSH concerns.
Vaibify must not implement a parallel SSH configuration format.

The Vaibify application port uses `--port`. An SSH port is not encoded as
`host:port`, because that would be ambiguous with IPv6 and with the application
port. A later `--ssh-port` option may be added only if a concrete need cannot
be expressed through OpenSSH configuration.

**There is no `--project` option.** The previous revision proposed one. A
project name may now contain a space, and OpenSSH delivers its remote command
to a login shell, so a name carried in that command is a word-splitting hazard
at best. The project is selected in the dashboard after the tunnel is up, over
HTTP, where `requests` and `encodeURIComponent` already handle the character
set correctly. This removes the interpolation surface instead of guarding it,
and it is why the remote command can stay fixed source text apart from a
validated integer port.

### Manual and diagnostic workflow

Add a supported browser-suppression flag:

```bash
vaibify --no-browser --port 18050
```

This starts the backend without trying to launch a browser. `vaibify
--no-browser` is a Click usage error today; the existing
`VAIBIFY_SUPPRESS_BROWSER` environment variable remains for internal and
backward compatibility, but user documentation should prefer the flag.

`--no-browser` must not mint and discard a browser capability. The current call
order still evaluates `_fsLaunchUrlWithCapability` as the argument to
`_fnOpenBrowserUnlessSuppressed` (`cli/main.py:218-220`), so the capability is
minted before the suppression check, and an armed credential nobody redeems
occupies one of 64 slots for its 300-second life. No test pins this in either
direction.

The two-command, manually forwarded form remains a diagnostic escape hatch,
not the primary workflow.

## Topology and file semantics

Remote operation introduces three locations:

| Location | Meaning |
|----------|---------|
| Observer machine | The laptop running the browser and `vaibify remote` |
| Execution host | The remote machine running the Vaibify backend |
| Execution environment | A Docker workspace or the host-mode project filesystem |

In Docker mode the execution host and execution environment have distinct
filesystems. In host mode they are the same filesystem.

Do not encode the assumption that an execution environment is exactly one
machine. A future scheduled environment may be an allocation spanning many
compute nodes while the backend runs on a login or service host and both see a
shared project filesystem. The initial direct implementation need not model
nodes or allocations, but topology payloads must remain structured
capabilities rather than a single `bRemote` switch.

The UI and API must therefore use topology-correct actions:

- **Download to this computer** streams a file in an HTTP response and lets the
  observer's browser save it. This is the canonical remote-to-observer path.
- **Upload from this computer** reads a browser-selected file and writes it to
  the execution environment.
- **Copy to execution-host path** copies from a Docker workspace to a path on
  the machine running the backend. It is useful only when the execution host
  and execution environment differ.

The current **Pull to host** action is worse than mislabeled. It is reachable
from two places (`static/scriptFilePull.js:22,35` and
`static/scriptSyncManager.js:1819`), neither of which consults the project
mode, and its destination guard admits anywhere under `$HOME`
(`gui/routes/fileRoutes.py:59-66`). In host mode the router sends it to the
host leg, so it is now literally a copy from a directory to `$HOME` on the same
filesystem, presented to the researcher as a transfer. It must be split into
the three actions above and hidden where source and destination filesystems are
identical. Existing browser download streaming should be reused rather than
adding a second file-transfer protocol over SSH.

The dashboard must identify the execution hostname and execution mode
prominently. Directory browsing and project registration on a remote backend
refer to remote paths and retain host mode's existing boundaries: execution is
confined to the registered project directory and its scratch subtree
(`host/hostConnection.py:154-192`), while browsing and registration reach all
of `$HOME` (`gui/registryRoutes.py:1177-1187`). Remote access must not widen
either. Note that the `$HOME`-wide pair were written on the assumption that the
browser and the filesystem belong to the same person; remote access does not
remove that assumption — SSH still proved the researcher — but it does make it
load-bearing in a way it was not before, and the security review must confirm
it rather than inherit it.

## The remote lane

### Why remote is its own lane

Every constant governing disconnection was chosen for a browser tab on the same
machine as the backend. There, a closed socket means a human closed a window,
and 15 seconds is a generous allowance for a reload. Through a tunnel, a closed
socket usually means a network changed, and the human is still sitting there.
The same evidence carries a different meaning, so the lane carries different
numbers and, in one case, a different mechanism.

The lane is identified at the hub, not inferred by the browser: the remote
helper marks the session it bootstraps as remote, and the continuity contract
below applies to sessions so marked. A local browser on the remote machine
keeps the local lane's behavior.

### Connection continuity: the 15-minute contract

**The governing invariant is that the hub's hold window must be at least as
long as the client keeps retrying.** Violating it is not a tuning error, it is
a lie: the late retries are refused, and the refusal is reported to the
researcher as a server restart. That misalignment exists today in the local
lane — a 31-second retry ladder (`static/scriptWebSocket.js:15`) against a
15-second window plus a ~5-second evaluator pass — and must be fixed there too.

The remote lane's contract:

1. **The local client retries the SSH tunnel for 15 minutes** with bounded
   backoff, preserving the same local port so existing browser URLs and Host
   checks stay valid, and showing the researcher what it is doing and how much
   of the window remains. It does not retry forever: an endless loop against a
   dead machine hides a real failure.
2. **The hub holds a remote session for 15 minutes.** The ownership record
   stays ACTIVE and the browser credential stays valid that long with no socket
   attached, rather than the local lane's 15 seconds.
3. **Reconnection inside the window is seamless.** The browser reconnects, the
   dashboard reconciles from its ordinary polled state, and nothing is lost but
   streamed output emitted during the gap — which is already discarded rather
   than buffered (`gui/pipelineServer.py:791-816`).
4. **Reconnection after the window goes through ownership transfer.** The
   client re-establishes SSH, mints a transfer capability over the hub's local
   control socket, and the dashboard is handed back its own project. A
   registered durable task is adopted and keeps running
   (`gui/sessionLifecycle.py:962-976`); the lease is rotated, not preserved.
5. **The two paths must be indistinguishable to the researcher.** Today a
   transfer capability is consumed at page load from a URL fragment, so the
   interface reloads and loses its state. The exchange itself is a plain POST
   that already records the new lease in place
   (`static/scriptApplication.js:182-207`), so an open page can redeem a
   transfer mid-life. Build that; without it the window boundary is a visible
   jolt.
6. **A run is never interrupted by any of this.** Orphaning already retains the
   flock, the keep-alive, and any live task; the busy oracle already vetoes the
   reaper. That half of the existing machinery is correct and must not be
   disturbed.

Beyond the window the session orphans exactly as a local one does, and the
existing 12-hour absolute session cap (`gui/sessionLifecycle.py:118-122`)
remains the backstop against a session abandoned for good.

Two supporting changes are required for the contract to mean anything:

**Configure WebSocket ping explicitly.** When a laptop sleeps or its network
vanishes, no reset reaches the hub — the far end is a still-open `sshd`
forwarder. Vaibify passes no `ws_ping_interval` or `ws_ping_timeout` to uvicorn
(`cli/main.py:249`), so detection rests entirely on a library default nothing
in the repository states or observes. The consequence is severe: the
live-connection count stays stuck, the hold window never starts, and the
returning browser is refused as a duplicate tab by its own ghost, with a toast
telling the researcher to close a tab that does not exist. The remote lane must
set these values explicitly and this plan must state them.

**Raise the remote hub's idle timeout.** A hub with no socket, nothing busy,
and no HTTP traffic SIGTERMs itself after 30 minutes
(`gui/serverLifespan.py:238`, predicate `:363-373`). A deliberately detached
remote hub whose researcher is asleep meets exactly that description. The
helper must start it with a longer timeout, and the value must be documented
rather than left as folklore.

### The terminal in a remote session

Every Vaibify session has terminal access, including remote ones. The terminal
is served in both modes today and is reachable through the tunnel with no
transport work: it is a plain WebSocket to the page's own origin, and a
tunneled browser presents a loopback origin the existing gate accepts.

**On a dropped connection the shell dies and the pane recovers with a fresh
one.** This preserves the current containment story exactly: the socket's
teardown still terminates the recorded session and proves it empty, and a
project whose shell cannot be proven dead still reports quiescence as unproven
and routes to `vaibify reconcile`. What changes is only that the pane stops
being permanently dead — today the terminal socket has no reconnection logic at
all, writing `[Connection closed]` and stopping
(`static/scriptTerminal.js:532-535`).

The terminal therefore needs, in the remote lane:

- a reconnection ladder on its own socket, aligned to the same 15-minute
  contract, dialing a **new** shell rather than seeking the old one;
- an honest statement in the pane that the previous shell ended and why, rather
  than a silent new prompt; and
- **a remote-specific banner.** The shipped text says "This shell runs on YOUR
  OWN machine, in the project directory"
  (`gui/routes/terminalRoutes.py:68-77`), which becomes false the moment a
  tunnel is involved. The remote banner must name the execution host. That text
  is guarded by a falsification entry, so the change is deliberate and must be
  registered, not edited around.

**Holding a shell across a drop is explicitly out of scope for the first
release.** It is not a small increment. There is no way to represent a held
shell — the record has three states and `live` means "attached." There is no
output buffer anywhere, so a held shell with nobody draining it does not lose
output but *wedges*, because the kernel buffer fills and the researcher's job
blocks on write at an arbitrary point. And a live terminal record currently
refuses ownership transfer (`gui/sessionLifecycle.py:892-906`), so holding the
shell would block the reattachment path the continuity contract depends on. It
would also require rewriting a pinned test and a falsification-registry entry
that tie teardown to socket close, which is a doctrinal change needing its own
ruling. Revisit if alpha testers report losing work this way.

## Connection architecture

### Local remote client

Add a `remote` CLI command, initially in a cohesive module such as
`vaibify/cli/commandRemote.py`. Do not split transport, parsing, and process
management into separate modules merely for line count; extract only when a
real conceptual boundary or reuse appears.

The local client owns:

- SSH destination validation;
- local port selection and bounded retry;
- construction and lifetime of the OpenSSH subprocess;
- parsing the remote startup record;
- local-forward readiness checks;
- construction of the fixed local browser URL;
- browser launch;
- the 15-minute reconnection ladder, including re-minting a transfer capability
  when the hold window has expired;
- tunnel status and user-facing diagnostics; and
- signal handling and subprocess cleanup.

The local process should remain in the foreground, like the current Vaibify
hub. It should print the remote hostname, local URL, remote execution mode, and
how to close the tunnel. Raw capabilities, browser credentials, environment
contents, and SSH command output containing secrets must not enter the Vaibify
log.

The client must not live under `vaibify/host/`:
`tests/testHostSubprocessConfinement.py` permits exactly one module in that
subtree to acquire a subprocess-launching capability.

### Remote helper

Add a narrowly scoped remote-helper entry point invoked only with a fixed
command assembled by the local client. It shall:

1. Validate the requested application port.
2. Determine whether that port belongs to a compatible live Vaibify hub, using
   Vaibify's session registry and host-control channel rather than trusting
   that a listening socket is Vaibify.
3. Reuse the compatible hub or start a detached `vaibify --no-browser` hub with
   the remote lane's idle timeout.
4. Wait for both the TCP listener and the authenticated host-control channel.
5. Ask the host-control channel to mint one bootstrap capability — or, on a
   post-window reconnection, one transfer capability.
6. Emit exactly one bounded, versioned startup record on stdout and flush it.
7. Keep the SSH channel alive for port forwarding until stdin closes or a
   termination signal arrives.

Remote helper diagnostics go to stderr. Stdout is protocol-only. A helper that
cannot prove an existing listener is a compatible Vaibify hub must refuse
instead of forwarding to it.

**The helper is mostly `vaibify open` over SSH, and should be built that way.**
`cli/commandOpen.py` with `cli/hubSession.py` already performs hub discovery,
capability minting over the Unix control socket, redemption at `/api/bootstrap`
or `/api/transfer`, and the claim — headlessly, as the researcher's own user.
Only two things in it assume locality: it calls `webbrowser.open`, and it
hard-codes `127.0.0.1:<hubport>` in the URL it prints. Both already have a
print-the-URL fallback. Reuse this rather than writing a parallel client.

Two defects on that path must be fixed as part of this work:

- **Compatibility is currently unprovable.** A session slot records only pid,
  role, port, and start time (`config/sessionRegistry.py:90-118`). The
  strongest available evidence that a listener is a compatible hub is that a
  live process holds a hub slot on that port and its control socket answers as
  our uid — which a hub of any other version satisfies equally. Either add a
  version to the slot payload or add a version operation to the control
  channel.
- **`mint-bootstrap` can answer success with an empty capability.** At the cap
  of 64 outstanding armed capabilities the mint returns `""` and the handler
  still replies `bAccepted: True, bMinted: True`
  (`gui/hostControlChannel.py:795-824`). A helper that mints per invocation and
  abandons capabilities on tunnel drops will reach that cap; the empty string
  then fails opaquely at redemption.

**Do not journal the detached hub as a gated operation.** The hub is not a
project, the journal is keyed by project name, and its settle rule requires the
holder dead with an empty process group — so a long-lived hub would leave a
permanently unsettled record that makes its project unclaimable forever and
permanently vetoes both the ownership reaper and idle self-exit. The gate's
contract is that a child does not act if its parent dies, which is the opposite
of what a deliberately-outliving hub needs. Model the launch on
`_fprocessLaunchDetachedHub` (`gui/routes/sessionRoutes.py:37-54`), the shipped
and already-classified detached launcher: `start_new_session=True`, stdio to
`DEVNULL`, readiness by TCP probe, a cap on live children, and pruning from the
watchdog. Its subprocess acquisition still requires a reviewed disposition in
the mutation inventory.

The detached hub intentionally outlives the helper and SSH tunnel. This keeps a
laptop sleep or transient network loss from killing the remote control plane
mid-pipeline.

### Startup protocol

Use a single JSON object with a small maximum byte length:

```json
{
  "iProtocolVersion": 1,
  "iPort": 18050,
  "sBootstrapCapability": "...",
  "sExecutionMode": "docker",
  "sExecutionPlacement": "direct",
  "sHostname": "compute-machine"
}
```

All fields are untrusted until validated. In particular:

- the protocol version must be an exact supported integer;
- the port must equal the requested, locally forwarded port;
- the capability must match the expected bounded base64url alphabet and size;
- execution mode must be from a closed vocabulary;
- execution placement must be the closed initial value `direct`; and
- hostname is display text only and must be escaped by the renderer.

`sExecutionMode` describes where and how commands run (`docker` or `host`); it
must never be overloaded with scheduler names. `sExecutionPlacement` describes
whether execution is direct or mediated by a durable resource allocator. A
later protocol version may add `scheduled` plus scheduler capabilities, but the
initial client must reject rather than guess how to control an unknown
placement.

The local client must never open a URL supplied by the remote. It constructs
the URL from the validated local port and capability, fixing the scheme and
host to `http://127.0.0.1`. This prevents a compromised remote response from
turning browser launch into an arbitrary local URL or command action.

### Port selection

The existing production Host check requires the browser-visible port to equal
the backend's expected port (`gui/serverMiddleware.py:44-64`). Preserve that
rule. Note that it binds HTTP only — WebSocket upgrades never reach that
middleware, and the WebSocket origin check inspects the host but not the port —
so N-to-N forwarding is correct but is not, by itself, the whole guarantee.

The local client selects a free local candidate and asks the remote helper to
bind that same number. If the remote port is unavailable, choose another local
candidate and retry a bounded number of times. An explicit `--port` does not
fall back silently; it reports the conflict.

Two facts govern the remote side. **The helper must pass an explicit `--port`**,
because an explicit port is used verbatim and fails loudly on conflict, while
an automatic port scans upward from a persisted value. And **the live port must
be read from the session registry, never from `~/.vaibify/hub-port.json`**: as
of the port-hop fix, a hub contesting an unprovable listener binds a different
port for that session and deliberately does not persist it, so the file and the
bound port can disagree in exactly the restart case that motivated the fix.

The SSH process must use `ExitOnForwardFailure=yes`. Port selection is
inherently racy, so a forwarding-bind failure is a retry for an automatic port
and an error for an explicit one.

Starting an extra hub has a side effect worth stating: `vaibify do` refuses to
act when more than one hub is live unless given `--port`. A remote helper that
starts a second hub therefore breaks portless `vaibify do` on that machine.

### SSH lifecycle

Use the system OpenSSH client. Preserve its normal host-key verification and
interactive authentication behavior. Do not set `StrictHostKeyChecking=no`,
copy private keys, or implement SSH in Python.

Recommended connection properties:

- no remote pseudo-terminal for the protocol helper;
- `ExitOnForwardFailure=yes`;
- conservative `ServerAliveInterval` and `ServerAliveCountMax`, chosen so a
  dead tunnel is detected well inside the 15-minute window; and
- an argv-based local subprocess with no local shell.

OpenSSH sends the remote command through the remote login shell. The remote
command must therefore be fixed source text except for a strictly validated
integer port. The SSH destination is a separate argv element, must not begin
with `-`, and must reject control characters. Never interpolate an arbitrary
remote name, path, project, or user field into the remote command string.

If the tunnel exits for good, report the SSH exit status and leave the browser
showing an honest disconnected state.

## Browser-session and ownership behavior

The SSH tunnel is a transport, not a new authorization principal. Existing
browser-session credentials and per-target leases remain authoritative:

1. The remote host-control channel mints a one-time capability for a process
   running as an authorized remote user. Peer-uid equality over a `0600` Unix
   socket is the whole of that authorization, and it is sufficient only because
   SSH has already proved the researcher.
2. The capability travels over encrypted SSH stdout.
3. The local browser receives it in a URL fragment.
4. The browser exchanges it for its normal per-browser credential.
5. Docker-container or host-project claim returns the normal per-target lease.

Do not add a remote bypass to Host, Origin, browser-credential, lease, agent,
or commit-carrier checks. The browser already appears as a loopback client
through the tunnel, so no bypass is necessary.

**No agent token exists for a host target.** The mint returns an empty string
by design, and the empty credential fails closed at the agent gate. Nothing in
the remote lane may assume a per-target agent credential is available to ferry.

Two shipped affordances hand the browser a URL that cannot work through a
tunnel and must be addressed:

- **New Vaibify window** spawns a second hub on a backend-chosen port and
  returns `http://127.0.0.1:<port>` (`gui/routes/sessionRoutes.py:106-127`),
  which the frontend opens blind (`static/scriptUtilities.js:318-321`). That
  port was chosen after the tunnel was built, so it is not forwarded, and on
  the observer machine that address is the observer machine. Either disable the
  affordance in a remote session with a truthful explanation, or have the local
  client add a validated matching forward on demand.
- **Open in VS Code** hands the observer a `vscode://` deep link carrying a
  container id (`static/scriptApplication.js:4834-4841`) that exists only on
  the execution host's Docker daemon. Disable it in a remote session.

Do not return an unusable URL and let the browser misreport it as a server
failure. Dynamic additional tunnels are a later slice unless required for the
initial acceptance journey.

## Host-mode boundary and sequencing

Host mode is complete and this plan consumes it as shipped. It is a
single-user, trusted-by-honesty mode: its subprocesses are gated and journaled,
its execution is confined to the registered project directory and its scratch
subtree, its warning modal is acknowledged per canonical directory, and its L3
and Supervised refusals are enforced
(`reproducibility/levelGates.py:2142-2163` and `gui/routeScope.py:764-767`).
Running on a dedicated remote machine does not upgrade any of those claims, and
the dashboard must continue to label the target as host mode and uncontained.

Creating separate control and per-project OS users could be a valuable future
product, but it requires its own ruling over provisioning, service management,
credential brokerage, and multi-project isolation. It is not a slice of remote
access and is not required by this plan.

Two host-mode limits shape the remote onboarding story and should be documented
rather than discovered:

- **Host projects can only be registered from the dashboard.** `vaibify
  register` always registers container mode, so a remote host project is
  created through the tunnel, not over SSH beforehand.
- **`build`, `start`, and `destroy` have no host guard in the CLI** and will
  attempt Docker against a host project.

Before either the client or the helper ships:

- regenerate and re-check `tests/mutationInventory.json`
  (`python tools/generateMutationInventory.py --write`, then `--check`), giving
  both subprocess acquisitions reviewed dispositions that name the supporting
  launch, validation, and readiness symbols;
- run the host-capability inventory check, whose undisposed-site budget must
  equal the actual count exactly and may only fall; and
- run the style inventory and `tests/testStyleInvariants.py`, adding no
  exception merely to admit new remote symbols.

A fixed command and a trusted SSH user do not remove the need to enumerate,
disposition, and review a process capability.

## Deferred batch-scheduler compatibility

The first remote and host-mode releases do not submit, monitor, or cancel
Slurm, PBS, LSF, or other batch jobs. In particular, putting `sbatch` in a
normal pipeline command is not scheduler integration: the submission process
can exit successfully while the job is still pending, so Vaibify would lose the
authority needed to report completion, cancellation, and residue truthfully.

Do not add a speculative scheduler base class now. Preserve these seams in the
direct implementation instead:

- SSH remains only the observer-to-control-plane transport. GUI routes call the
  selected execution connection and never issue SSH or scheduler commands
  themselves.
- Execution mode and execution placement remain orthogonal. Docker and host
  mode describe the command environment; direct and a future scheduled mode
  describe who owns its lifecycle.
- A durable mutation record identifies the authority that actually owns the
  work. Direct host execution uses its proven PID and process group, Docker
  execution its container/exec identity, and a future scheduler driver a
  cluster identity plus scheduler job and step ids. The death of a local
  submission process must never prove scheduler work ended.
- Run state may distinguish accepted, waiting, running, and terminal work
  without equating Vaibify's internal `queued` display state with a scheduler
  queue state. Unknown or unreachable authority remains visible and
  indeterminate.
- File operations address the observer, execution host, and execution
  environment through topology capabilities. They must not assume the
  environment is one node or that node-local storage is visible to the backend.
- Scheduler resource requests belong to an execution-target profile and the
  resolved run provenance, not as scheduler directives embedded throughout the
  science-agnostic workflow schema.
- The remote client must not assume institutional login nodes permit detached
  services. A future site profile may select an approved service host or a
  scheduler-owned control process without changing the browser bootstrap and
  tunnel contract.

The likely first scheduler slice would allocate resources once for a complete
pipeline, persist the returned scheduler job identity, and run the existing
sequential steps inside that allocation. Per-step batch jobs and scheduler DAG
dependencies are a later, materially larger design.

## Implementation slices

### Slice 1: headless launch contract

- Add the public `--no-browser` option.
- Preserve `VAIBIFY_SUPPRESS_BROWSER` for internal compatibility.
- Prevent suppressed launches from minting unused capabilities.
- Add unit tests for browser launch, suppression, and capability counts —
  including the currently unpinned assertion that a suppressed launch mints
  nothing.
- Document that `--no-browser` does not make the process a daemon.

Exit criterion: a hub starts on loopback without a browser side effect or an
unused capability.

### Slice 2: connection-continuity foundation

This slice precedes the tunnel because it fixes a defect that already misleads
local users, and because the remote lane is meaningless without it.

- Configure `ws_ping_interval` and `ws_ping_timeout` explicitly, so a sleeping
  laptop's socket is detected rather than left hanging.
- Align the frontend retry ladder with the backend hold window, and make the
  relationship — window at least as long as the ladder — an asserted invariant
  rather than two constants that happen to agree.
- Replace the "server has been restarted (session expired)" message on an
  orphan-revoked credential with one that says what actually happened.
- Add in-page transfer redemption, so a returning session can be handed back
  without a page reload.
- Add a reconnection ladder to the terminal socket, dialing a fresh shell and
  saying so in the pane.

Exit criterion: a local browser survives a 30-second network interruption with
no reload and no false message; a sleeping laptop's ghost socket does not
refuse its own returning browser as a duplicate tab.

### Slice 3: remote startup helper

- Add the fixed remote-helper CLI entry point and protocol schema, built on the
  existing `vaibify open` capability path.
- Reuse a compatible hub, or create a detached one modeled on the shipped
  detached-hub launcher, with the remote lane's idle timeout.
- Add a hub version to the compatibility proof and refuse an unprovable
  listener rather than forwarding to it.
- Fix the empty-capability success reply on the mint path.
- Emit bounded protocol JSON on stdout and diagnostics on stderr.
- Wait for channel closure without owning the detached hub's lifetime.

Exit criterion: a local test driver can start the helper, validate its record,
redeem the capability, and load the hub over loopback; a hub of a different
version is refused with a message naming the mismatch; killing the helper at
each launch boundary leaves no untracked hub process.

### Slice 4: local `vaibify remote` client

- Register the `remote` CLI command.
- Validate the SSH destination; carry no project name in the remote command.
- Select a matching local/remote port with bounded retries, passing an explicit
  `--port` to the remote hub and reading the live port from the session
  registry.
- Start OpenSSH with the fixed helper command and local forwarding.
- Parse and validate the startup record; construct the local URL locally.
- Wait for the local forwarded listener and launch the browser.
- Implement the 15-minute reconnection ladder, including minting a transfer
  capability once the hold window has passed, with visible status.
- Report clean, refused, and abnormal exits honestly.

Exit criterion: one command opens a local browser displaying a remote hub; no
Vaibify HTTP listener is exposed beyond either loopback interface; and a tunnel
killed and restored at 30 seconds, at 5 minutes, and at 20 minutes each
recovers by the documented path with the run intact.

### Slice 5: remote session identity and the terminal

- Mark the session the helper bootstraps as remote, at the hub.
- Apply the remote lane's hold window to sessions so marked.
- Display the execution hostname and mode in the dashboard. No hostname exists
  anywhere in the codebase today; this is net-new.
- Replace the terminal banner in a remote session with one naming the execution
  host, and register the falsification entry the change affects.
- Disable the new-window and VS Code affordances in a remote session, with
  truthful explanations.

Exit criterion: nothing in a remote dashboard claims the researcher's own
machine, and every URL the dashboard offers is either forwarded or absent.

### Slice 6: topology-correct file UX

- Introduce structured execution-topology capabilities in the connect payload.
  `sProjectMode` (`gui/pipelineServer.py:1940-1952`) is a shipped string
  consumed at several JS sites and persisted in the registry; extend alongside
  it rather than restructuring it.
- Wire **Download to this computer** to the existing streaming download route,
  which is built, hardened, and has no caller.
- Retain browser upload as **Upload from this computer**.
- Rename the Docker host export to **Copy to execution-host path** and gate it
  on the execution host and execution environment being different filesystems.
  Both entry points are currently ungated.
- Keep host-mode project selection within its existing path guards.

Exit criterion: a browser download lands on the observer machine, host mode
never presents a self-copy as a download, and every displayed path names which
machine owns it.

### Slice 7: secondary ports

- Add explicit tunnel support for any service port the dashboard advertises.
- Confirm that tunnel loss cannot terminate or optimistically complete a live
  pipeline.

Note that `--jupyter` is currently inert: it is declared in
`cli/commandStart.py` and never read again, selecting only an image variant.
There is no Jupyter port exposure to forward until that feature is wired, so
this slice covers whatever service ports actually exist when it is reached.

Exit criterion: every remote URL the dashboard offers is actually forwarded.

## Security review checklist

Before implementation and again before merge, attempt to falsify these claims:

- The remote HTTP and WebSocket listener is reachable only through remote
  loopback.
- OpenSSH host-key verification was not weakened.
- A remote name beginning with an option or containing shell metacharacters
  cannot alter either local SSH options or the remote command.
- No project name, path, or user-supplied string reaches the remote command
  string at all.
- Malformed, oversized, multiline, or version-mismatched startup output fails
  closed.
- A malicious remote response cannot make the local machine open an arbitrary
  URL or execute a command.
- Capabilities and credentials do not enter query parameters, access logs,
  Vaibify logs, exception text, session registries, or shell history.
- A non-Vaibify listener, or a Vaibify hub of an incompatible version, on the
  requested remote port is never treated as a usable hub.
- Crashing the helper before process creation, before readiness, or during
  hand-off never leaves an untracked hub.
- Forwarding failure never falls through to opening an unforwarded URL.
- Loss of the observer or tunnel never marks a pipeline complete, releases a
  busy target, or hides an indeterminate mutation.
- A returning researcher inside the window is never refused by a ghost socket,
  and past the window is never refused by the busy veto that protects their own
  run.
- A dropped tunnel never manufactures a quarantine that only a local shell can
  clear. Where a quarantine is genuine, the recovery path is reachable from the
  remote session or is documented as requiring separate access.
- Browser download writes only through the browser's normal download handling;
  copy-to-execution-host remains separately named and authorized.
- Host mode remains visibly uncontained and cannot attain the L3 or Supervised
  claims it refuses locally.
- A project published on all interfaces by `lanExpose` in its own configuration
  is refused or prominently flagged in a remote session: on a shared machine
  that setting is internet-facing exposure, not a LAN convenience.
- Direct remote mode never claims to manage a submitted batch job, and a
  scheduler allocation is never described as a container or containment
  boundary.

## Verification plan

### Unit and contract tests

Add tests for:

- `--no-browser` and environment-variable compatibility;
- no capability mint on a suppressed ordinary launch;
- the window-at-least-as-long-as-the-ladder invariant, asserted rather than
  assumed;
- remote destination validation, and the absence of any interpolated field
  beyond a validated integer port;
- SSH argv construction without a local shell;
- fixed remote-command construction;
- startup-record size, schema, alphabet, port, and version validation;
- execution-mode and direct-placement validation as independent fields;
- local URL construction that ignores remote URL-like fields;
- automatic and explicit port-conflict behavior;
- remote hub reuse, version-mismatch refusal, and non-Vaibify-listener refusal;
- the empty-capability mint reply;
- helper readiness timeout and control-channel authentication failure;
- tunnel exit and signal cleanup; and
- execution-topology projection into file actions.

### Browser lane

Extend the browser lane to load the real dashboard through a forwarded local
port and assert: bootstrap exchange succeeds; REST and WebSocket traffic remain
functional; the remote hostname and execution mode render; the terminal banner
names the execution host; a browser download reaches the browser context;
copy-to-execution-host is hidden for identical filesystems; and an unavailable
secondary port is never advertised as reachable.

The ordinary browser fake does not prove real SSH behavior. Keep its assertions
for frontend semantics, but add a distinct Linux remote lane with a local
OpenSSH server, an ephemeral key, an isolated remote home, and a real Vaibify
backend. That lane must fail when `sshd`, forwarding, the remote helper, or the
browser journey did not actually run; it may not skip itself green.

### Adversarial integration cases

Drive at least these through real process boundaries:

- local port taken after selection;
- remote port held by an unrelated server, and by an incompatible Vaibify hub;
- SSH authentication or host-key failure;
- remote Vaibify absent from the non-interactive PATH;
- local and remote protocol-version mismatch;
- truncated and malicious helper stdout;
- helper death at each boundary between child creation, readiness, and hand-off;
- SSH loss before bootstrap, after bootstrap, and during a live pipeline;
- tunnel restored at 30 seconds, at 5 minutes, and at 20 minutes;
- a suspended observer machine whose socket produces no reset, followed by a
  returning browser;
- a shell running when the tunnel drops, confirming the pane recovers and the
  project's quiescence reporting stays honest;
- remote hub restart with a stale browser credential;
- a transfer refused by each of its refusal conditions in turn; and
- distinct target name and target id throughout the real WebSocket path.

### Repository-required checks

```bash
python tools/generateMutationInventory.py --write
python tools/generateMutationInventory.py --check
python tools/generateHostCapabilityInventory.py --check
python tools/generateStyleInventory.py --check
python -m pytest tests/ -q --ignore=tests/testContainerBuildIntegration.py
python -m pytest tests/testArchitecturalInvariants.py -v
pip install -e '.[browser]' && python -m playwright install chromium
python -m pytest tests/browser -m browser
```

Also run the new real-SSH remote lane and a manual remote walkthrough. Report
Docker, host mode, browser, SSH, and secondary-port surfaces separately; a pass
in one is not evidence for another.

## Manual acceptance journey

1. Configure an OpenSSH alias for a remote Linux machine.
2. Install matching Vaibify versions locally and remotely.
3. Run `vaibify remote <alias>` with no existing remote hub.
4. Confirm one local browser opens and the remote backend remains loopback-only.
5. Register or open a remote project and run a step with streamed output.
6. Open a terminal and confirm the banner names the remote machine.
7. Download an output and confirm it lands on the observer, not merely the
   remote host.
8. Upload a local file and confirm it lands inside the selected remote
   execution environment.
9. Drop the tunnel for 30 seconds during a long run; confirm the dashboard
   reconnects without a reload and the run is untouched.
10. Drop it for 20 minutes; confirm the session is handed back on return, the
    run survived, and the terminal reports honestly that its shell ended.
11. Close the laptop lid for an hour; confirm the remote hub is still alive and
    the session recovers.
12. Repeat with an explicit port, with a port conflict, and through an SSH
    ProxyJump configuration.
13. Repeat in Docker and host modes, confirming that host mode remains visibly
    uncontained and that its L3 and Supervised refusals remain in force.

## Non-goals

The first remote release will not:

- bind Vaibify to `0.0.0.0`;
- add direct internet exposure, TLS termination, or password login to the web
  application;
- replace OpenSSH or manage private keys;
- run a local Vaibify backend that controls a remote Docker daemon;
- send arbitrary execution commands over SSH from individual GUI routes;
- claim that a dedicated bare-metal machine is equivalent to a container;
- hold a shell open across a dropped connection, or re-attach a browser to a
  shell it was previously attached to;
- provision control or per-project OS users, or introduce an isolated-host
  execution mode without a separate ruling;
- submit, monitor, recover, or cancel batch-scheduler jobs, or claim support
  for ordinary pipeline commands that merely invoke a queue submission tool; or
- make unforwarded service ports appear reachable.

## Documentation deliverables

Update the CLI, installation, dashboard, security, and architecture documents
with:

- the one-command workflow;
- required remote installation and OpenSSH configuration;
- the lifecycle of the tunnel and the detached remote backend, including its
  idle timeout;
- the 15-minute continuity contract in the researcher's terms: what a brief
  interruption costs, what a long absence costs, and what is never at risk;
- what a dropped connection does to an open shell;
- observer versus execution-host versus execution-environment terminology;
- download, upload, and host-copy semantics;
- Docker and trusted-by-honesty host-mode security claims;
- the explicit non-support of batch-scheduled supercomputers in the first
  release and the distinction between a resource allocation and containment;
- troubleshooting for port conflicts, version mismatch, PATH problems, and
  tunnel loss; and
- an explicit statement that SSH supplies transport encryption and remote-user
  authentication while Vaibify's existing browser credential and lease still
  govern dashboard sessions.
