# Security Model

vaibify assumes that the code running inside a container may be
adversarial. An AI agent writes and runs code there, acts on data the
researcher owns, and may hold credentials for GitHub, Zenodo and
Overleaf. The security posture is defensive by design: the container
receives only what is explicitly granted, credentials live in hardened
stores rather than in the environment, and every command an agent
sends is checked against the container it names at the moment it runs.

This page describes what each control does, what it does not do, and
how the controls are kept from silently eroding.

## The threat model

The defenses are designed to contain four things:

- **Escape to the host file system.** No wholesale host mount and no
  Docker socket. The host paths that *are* mounted are listed under
  [Container isolation](#container-isolation).
- **Exfiltration over the network.** Optional network isolation blocks
  all traffic in and out.
- **Privilege escalation inside the container.** Your code runs as an
  unprivileged user, with no `sudo` and a minimal capability set.
- **One container acting on another.** Each container's agent holds its
  own token, which authorizes that container and no other.

Credential theft is contained only **partially**: a secret you choose to
mount into a container can be read by every process in it (see
[How secrets are handled](#how-secrets-are-handled)).

vaibify does **not** defend against kernel-level container escapes. For
high-security workloads, run vaibify inside a virtual machine or use a
hardened container runtime such as gVisor.

## Container isolation

Every containerized project runs inside a Docker container started with
these restrictions:

| Control | What it means |
|---|---|
| Unprivileged user | The image creates a non-root user with UID 1000 and sets it as the image's default `USER`, so every `docker exec` lands unprivileged. |
| No `sudo` | The image installs no `sudo` binary and writes no sudoers entry. |
| Root only at startup, then `gosu` | The entrypoint starts as root to write a few system paths and fix workspace ownership, then `exec`s itself through `gosu` as the container user. Root exists at container start, but nothing you or an agent run inherits it. |
| Minimal capabilities | The container starts with `--cap-drop ALL` and adds back only the five capabilities the entrypoint needs for that ownership fix and the `gosu` drop. `no-new-privileges` is set, so no setuid binary can regain privileges after the drop. |
| No Docker socket | The daemon socket is never mounted. A bind mount that resolves to a Docker endpoint, is a socket, or contains one is refused (see below). |
| Few host mounts | Files enter and leave through vaibify's own transfer commands, not a shared host directory. Three kinds of host path can be mounted: resolved secret files (read-only, under `/run/secrets`), any `bindMounts` you declare in `vaibify.yml`, and, only for a project that opts in, the Linux X11 socket (read-only). |
| Workspace volume | A Docker named volume holds `/workspace` (or your configured `workspaceRoot`). It is not part of the host directory tree. |
| Loopback-only ports | Every forwarded container port is bound to `127.0.0.1` unless that port's entry sets `lanExpose: true`. |
| Optional network isolation | `networkIsolation: true` in `vaibify.yml` starts the container with `--network none`, blocking all traffic in and out. It cannot be combined with `x11Forwarding`. |

### Declared bind mounts

Each `bindMounts` entry is validated when the configuration loads and
again when the run arguments are built. A mount is refused if it leaves
your home directory, contains `..`, or touches `/etc`, `/root`, your
SSH, cloud, GitHub CLI, GnuPG, Docker or Kubernetes configuration, or
vaibify's own journal, control-socket or temporary-secret directories.
The Docker socket is refused by **resolution and file type**, not by
spelling: every endpoint your Docker configuration names, and any path
that is a socket or contains one, is denied (on Colima, Rancher and
rootless installs the live socket sits inside your home directory).
Mounts are passed with `--mount`, so a missing source stops the start
with a message naming the path instead of becoming an empty directory.
This validation cannot catch a daemon reached over `tcp://` (network
isolation is the control there), or a socket created inside a mounted
directory after validation.

## The hub and the browser

The vaibify hub (the backend that serves the dashboard) runs on the
host, because it orchestrates containers. It binds `127.0.0.1`. On
Linux, when it serves in-container agents, it also binds the Docker
bridge gateway address the daemon reports, because a container's
packets arrive on the bridge interface, not on loopback; on macOS the
daemon's virtual machine forwards them to loopback. A remote session
(see [Connecting to External Resources](externalResources.md)) reaches
a hub on another machine through an SSH tunnel between the two
loopback addresses.

A browser request must carry a per-browser credential obtained through
a one-time capability exchange, and its `Host` header must name a
loopback host and the hub's own port, which blocks DNS rebinding.
Responses carry a Content-Security-Policy. Any path that originates
from a request body, a `project.json` field or a configuration file is
validated against its intended root before the hub opens it. Project
files writable from inside the container (`project.json`, `state.json`,
directory names, the dependency diagram) are treated as untrusted
markup: wrongly typed fields are refused by name, the frontend escapes
through one quote-safe function, and the diagram is shown as an image.

## The per-container agent token

An in-container agent asks the hub to act through `vaibify-do`. When the
dashboard connects to a container, the hub mints a random token for
that container alone and writes it, with the container's ID, into a
file readable only by the container user. The token is distinct from
every other container's token and from any browser credential.

Every agent command names its container in the request path. At the
moment the request arrives, the hub checks that the presented token
belongs to the owner record that currently serves that container ID.
A token minted for one container never authorizes another, an empty
token or an unnamed container fails closed, and a request whose token
does not match is refused without falling through to any other check.

An authorized agent request is then filtered through the action
catalog. Every state-changing route is either marked agent-safe or
explicitly excluded, and the check runs on the server, so an action
refused by `vaibify-do` cannot be reached by calling the hub directly.
Actions that grant trust are researcher-only: clearing a quarantine,
choosing how an image may run, and writing host preferences, among
others.

## How secrets are handled

vaibify never asks you to put a credential in an environment variable,
in shell history, in Git configuration or in a committed file.

1. **Names, not values.** The `secrets` field in `vaibify.yml` lists
   secret *names* and a retrieval method. Values are resolved on demand
   through an established credential manager: `gh auth`, the operating
   system keyring, or Docker secrets.
2. **Read-only files, not variables.** A resolved secret is written to
   a host file with mode 600 inside a per-user directory with mode 700
   in vaibify's own state, then bind-mounted read-only under
   `/run/secrets` in the container. Inside the container, Git reaches
   GitHub through a credential helper that reads the mounted file at
   request time, answers only for `github.com`, and never writes the
   token to disk.
3. **These host files outlive the container on purpose.** Some Docker
   setups re-resolve bind-mount sources during later operations, and a
   missing source then breaks the container. The files are overwritten
   on the next start. At hub startup, files older than a week are
   removed unless a container still mounts them; if vaibify cannot
   enumerate the live mounts, it removes nothing.
4. **No token in a URL.** Zenodo and GitHub API requests use an
   `Authorization: Bearer` header. Git operations against Overleaf and
   GitHub use a one-shot credential helper that first resets any helper
   list, so the supplied token is the only one Git can use and it never
   appears in a URL.
5. **A secret this host cannot resolve degrades loudly.** The container
   starts without it, and vaibify names the secret, its method and the
   remedy, never its value or where it would be stored.

**Credentials inside the container are persistent and shared.** Two
stores survive container recreation. The container keyring (a
plaintext keyring backend) lives on a separate per-project credentials
volume, which survives a Rebuild; **Delete environment…** on the
environment's tile menu removes it. Each coding
agent's login and settings live on the workspace volume. All agents run
as the same container user, so file permissions isolate nothing between
them: treat "one agent was compromised" as "every configured provider's
session was exposed."

## X11 display forwarding

Graphical programs in the container (a PDF viewer, an interactive plot
window) need the host's X display. Forwarding is **off by default** and
is enabled per project with `x11Forwarding: true` in `vaibify.yml`.

It is opt-in because an X client connected to your display can read
what is drawn on the screen and send keystrokes and mouse events to
other windows. A program in the container that you do not trust,
including a compromised agent, then has a channel out of the container
that the file-system and network controls do not cover. For the same
reason vaibify refuses to start a project that sets both
`x11Forwarding` and `networkIsolation`: a display channel would defeat
the seal.

What vaibify changes on the host when forwarding is on:

- **Linux:** mounts `/tmp/.X11-unix` read-only and runs
  `xhost +SI:localuser:$USER`, which admits only your own account.
  Revoke it with `xhost -SI:localuser:$USER`.
- **macOS:** the container reaches the X server over TCP at
  `host.docker.internal`, so the server (XQuartz or the MacPorts X11
  application) must accept network clients. vaibify starts the server,
  runs `xhost +SI:localuser:$USER` and `xhost +localhost` (the local-user
  rule admits only Unix-socket clients, not a TCP connection from the
  container engine), and tells you if the server refuses network
  clients. Revoke the TCP entry with `xhost -localhost`.

The display setting is fixed when a container is created. After you
turn `x11Forwarding` on, or install or reconfigure the X server, stop
and start the container so it is created again. A command-line check
reports a running container that was created without it; see [CLI
Reference](cli.md).

## Host mode

A host project has no image, no container and no volume: its pipeline
runs directly in a directory on your machine. Every protection above
that comes from the container is therefore absent. Pipeline commands,
and any AI agent you run there, execute with your full user authority:
your files, your network, your stored credentials, and vaibify's own
state. The dashboard says so before you enter a host project.

What vaibify still does in host mode: every host subprocess is started
in its own process group and journaled, and Cancel signals only the
groups it recorded. What it cannot do: prove that a finished run left
nothing behind (a process can leave its group), reach PROOF Level 3,
provide Supervised attribution, or run the Agent Council. There is no
agent lane, because on the host the agent *is* the user, so no agent
token is minted. Use host mode on a machine dedicated to the work, or
to try vaibify before building an image; for contained, attestable work,
create a containerized project. See
[Environments and Projects](environmentsAndProjects.md).

## Images vaibify did not build

An image vaibify built (its build labels are present and the project
did not obtain it) never asks anything. An image the project obtained
(a published environment, a tarball, a registry reference), or one
whose provenance vaibify cannot establish, carries an entrypoint and a
`USER` its author chose, so a persistent container is not created from
it until you have chosen how it may run. The question is asked in the
dashboard before the start (the command line asks it too; see [CLI
Reference](cli.md)). Both read one text, in
`vaibify/config/imageTrust.py`. Nothing is preselected.

The answer is recorded in the host registry against the image's content
digest with the time it was given, never against a name or tag, so an
image that changes under the same tag asks again. The launch path
refuses an unanswered digest, so skipping the dashboard skips nothing.
The answer is set only through a route the in-container agent cannot
reach, and a badge on the project tile and in the project's settings
shows it. Changing it asks for confirmation because the container is
recreated.

| Choice | What runs |
|---|---|
| Run it restricted | The idle entrypoint and the unprivileged user described below. |
| Run it as its author built it | The image's own entrypoint, started the way vaibify starts its own images (as root, with the entrypoint capabilities, so an entrypoint that drops to its own `USER` can). |
| Inspect only | Nothing persistent. The image runs only in the disposable verification lane: restricted, no credentials, network off. |

Stored credentials are a separate choice, off by default and
unavailable under Inspect only. "Credentials" means the project's
configured secrets (mounted read-only under `/run/secrets`), the
credentials volume that holds the container keyring, and the host bridge
an in-container agent uses to call back to vaibify. Unchecked, none of
them is attached.

### Restricted mode compatibility contract

A restricted launch differs from vaibify's own launch in exactly these
ways:

| | vaibify's own image | restricted |
|---|---|---|
| Entrypoint | the image's (vaibify's root phase, then `gosu`) | `/bin/sh`, the same keep-alive the disposable lane uses |
| Command | `sleep infinity` | `sh -c "sleep 2147483647"` |
| User | `--user 0`, dropped by the entrypoint | `--user 1000:1000` from the start, never root |
| Capabilities | all dropped, five entrypoint capabilities added | all dropped, none added |
| `no-new-privileges` | set | set |
| Credentials | per the project's configuration | only if you said so |

An image runs under it only if all of these hold:

- It contains an executable `/bin/sh` and a `sleep` that user 1000 can
  run. A distroless or scratch image, or one whose utilities only root
  can run, does not stay running, and vaibify says so.
- Whatever you need inside it works as an unprivileged user with no
  entrypoint having run. Services the entrypoint would have started are
  not running; anything the entrypoint would have created, changed
  ownership of, or configured is as the image left it.
- The workspace volume is owned by user 1000. The image cannot change
  that.
- Commands vaibify runs in the container run as the image's declared
  `USER`, or as `researcher` when it declares none. That user must
  exist in the image.

When a restricted container does not start, vaibify reports the failure
and offers the other options. It never retries silently, and never
shows a container that did not start as running. Outputs from a
restricted run can differ from the author's for reasons unrelated to
the science (file ownership, `HOME`, paths), so a Level 3 comparison of
a restricted run against the author's can diverge.

## The isolation audit

The isolation audit, run from the command line (see [CLI
Reference](cli.md)), runs `checkIsolation.sh` inside the container. It
performs exactly four checks:

- **Mounts.** Anything that is not a Docker named volume, an overlay or
  tmpfs, a secret, or a known system path fails as a possible host bind
  mount.
- **Docker socket.** Fails if a socket exists at `/var/run/docker.sock`;
  no other location is searched.
- **Privileged mode.** A heuristic: fails if `/dev` holds more entries
  than an unprivileged container normally exposes.
- **Listening ports.** Information only; from inside, the script cannot
  tell whether a port is published, so it prints the `docker port`
  command that can.

Read "All checks passed" as "no host bind mount, no socket at the
standard path, not obviously privileged", not as a general statement
about the container's security. The audit does **not** check for
privilege-escalation paths (`sudo`, setuid binaries), whether listening
ports are reachable from the host, or secrets in environment variables
or process listings.

## How security is enforced in CI

A decision that is not enforced is forgotten by the next agent that
edits the code, so these controls are pinned by tests that fail the
build (see [Testing Model](testing.md)):

- **The security lane** (`security.yml`) runs the security-boundary
  suite on every pull request, on Ubuntu and macOS with the oldest and
  newest supported Python: browser and agent authorization, per-container
  agent tokens, ownership and lease arbitration, WebSocket
  authorization, GitHub authentication, and the Content-Security-Policy.
  It is its own named check so a green architectural badge can never
  hide a red security one.
- **Architectural invariants** enforce, among others, that every
  state-changing route is registered with the agent-action catalog or
  explicitly excluded.
- **Image hardening tests** assert that the Dockerfile installs no
  `sudo`, writes no sudoers entry, and pins the default user to the
  container user. The weekly fresh image build confirms on a real image
  that the default user is neither root nor UID 0.
- **Falsification tests** prove that many of these guards are actually
  defended: each was shown to fail when its guard is deliberately
  broken (bind-mount denial, credential redaction, agent-lane refusal,
  path traversal and others).
- **The container-acceptance lane** checks the unprivileged
  container-user declaration against a real container every night.
