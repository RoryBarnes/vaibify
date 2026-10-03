# Security Model

Vaibify is designed for running AI-generated and untrusted code safely.
The security model follows a principle of least privilege: the container
has access only to what is explicitly granted, and the host remains
protected even if the code inside the container is malicious.

## Container Isolation

Every Vaibify project runs inside a Docker container with the following
restrictions:

| Control                | Implementation                              |
|------------------------|---------------------------------------------|
| No Docker socket       | The Docker socket is never mounted inside the container. Code in the container cannot create, inspect, or control other containers. |
| Unprivileged user      | Your code runs as a non-root user. The entrypoint starts as root to configure system paths, then `exec`s itself through `gosu` as the unprivileged user for workspace setup and everything after — so root exists at container start, not only at image build, but nothing you run inherits it. `sudo` is absent from the image. |
| Few host mounts        | The host filesystem is not bind-mounted wholesale. Files enter and leave the container through `vaibify push` and `vaibify pull`. Three kinds of host path are mounted: resolved secret files (read-only, under `/run/secrets/`), any `listBindMounts` entries you declare (validated against an allowlist, and refused if the source is missing), and, only for a project that sets `x11Forwarding: true`, the Linux X11 socket `/tmp/.X11-unix` (read-only) after an `xhost +SI:localuser:$USER` grant to your account. See "X11 display forwarding" below. |
| Workspace volume       | A Docker volume provides persistent storage at the configured `workspaceRoot`. Volumes are isolated from the host directory tree. |
| Network isolation      | Set `networkIsolation: true` in `vaibify.yml` to start the container with `--network none`, blocking all outbound traffic. It cannot be combined with `x11Forwarding`. |
| No display by default  | A container receives no `DISPLAY`, no X11 socket, and no `xhost` grant unless the project sets `x11Forwarding: true`. |
| Localhost-only GUI     | The pipeline viewer and setup wizard bind to `127.0.0.1`, never `0.0.0.0`. |

## X11 display forwarding

Graphical programs in the container (a PDF viewer, an interactive plot
window) need the host's X display. Forwarding is **off by default** and
is enabled per project with `x11Forwarding: true` in `vaibify.yml`.

What it exposes: an X client connected to your display can read what is
drawn on the screen and send keystrokes and mouse events to other
windows. A program in the container that you do not trust, including a
compromised agent, then has a channel out of the container that the file
system and network controls do not cover. For that reason forwarding is
opt-in, and vaibify refuses to start a project that sets both
`x11Forwarding` and `networkIsolation`: a sealed container cannot reach
the X server anyway, and a display channel would defeat the seal.

What vaibify changes on the host when it is enabled:

- **Linux:** mounts `/tmp/.X11-unix` read-only and runs
  `xhost +SI:localuser:$USER`, which admits only your own account.
  Revoke it with `xhost -SI:localuser:$USER`.
- **macOS:** the container reaches the X server over TCP at
  `host.docker.internal`, which requires the server (XQuartz or the
  MacPorts X11 application) to accept network clients. Vaibify starts
  the server, runs `xhost +SI:localuser:$USER` and `xhost +localhost`
  (`SI:localuser` only admits clients on the local socket, not a TCP
  connection from the container engine), and tells you if the server
  refuses network clients. Revoke the TCP entry with `xhost -localhost`.

`DISPLAY` is fixed when a container is created. After you turn
`x11Forwarding` on, or install or reconfigure the X server, stop and
start the container so it is created again; `vaibify doctor` and the
dashboard say when a running container was created without it.

## Secrets Management

Vaibify never stores credentials in environment variables, shell history,
Git configuration, or committed files. Instead:

1. **Resolution at build time** -- the `secrets` field in `vaibify.yml`
   lists secret *names*, not values. At build or run time, Vaibify
   delegates to the host's credential manager (`gh auth`, OS keychain) to
   resolve the actual values.

2. **Ephemeral mounting** -- resolved secrets are written to host files
   with mode 600 under `~/.vaibify/tmp/` (mode 0700) and bind-mounted to
   `/run/secrets/` inside the container.

   These host files deliberately **outlive the container**. Deleting
   them at stop time breaks later operations, because the daemon
   re-resolves bind-mount sources lazily and a missing source fails the
   mount. They are overwritten on the next container start, and a stale
   file is not evidence that nothing needs it — check what the daemon
   still mounts before removing anything under `~/.vaibify`.

3. **Token hygiene** -- Zenodo requests use `Authorization: Bearer` headers
   (never URL parameters). Overleaf uses Git credential helpers (never
   URL-embedded tokens).

4. **Credentials a container agent stores are persistent, not
   ephemeral.** Logins performed by an agent inside the container (its
   provider session, and anything written to the container keyring) are
   held in a Docker named credential volume so they survive container
   recreation. The container keyring is a `PlaintextKeyring`. That
   volume is not removed by `vaibify destroy`; remove it explicitly with
   `docker volume rm` when decommissioning a project. All configured
   agents run as the same container user and share that store, so a
   compromise of one agent exposes every configured provider's session.

## Security Audit

Run the built-in isolation audit to verify the container's security posture:

```bash
vaibify verify
```

The audit script (`checkIsolation.sh`) runs inside the container and
performs exactly four checks:

- **Bind mounts** — every mount is classified, and anything that is not
  a Docker named volume, an overlay/tmpfs, or a recognized secret mount
  is reported as a possible host bind mount. This check can fail.
- **Docker socket accessibility.** This check can fail.
- **Privileged mode.** This check can fail.
- **Listening ports** — reported for information only. The script cannot
  tell from inside the container whether a listening port is published
  to the host; it prints the `docker port` command that can.

The audit prints a pass/fail report covering those checks and nothing
else. Read "All checks passed" as "no host bind mount, no Docker socket,
not privileged" — not as a general statement about the container's
security posture.

```{note}
Three things it does **not** check, despite being natural things to
expect from a security audit: privilege-escalation paths (`sudo`,
`setuid` binaries), whether listening ports are actually exposed to the
host, and secrets leaking into environment variables or process
listings. This page previously claimed all three. They are absent from
the script, so a passing audit has never been evidence about them.
```

## Images vaibify did not build

An image vaibify built (its build labels are present and the project did
not obtain it) never asks anything. An image the project obtained (a
published environment, a tarball, a registry reference), or one whose
provenance vaibify cannot establish, carries an entrypoint and a `USER`
its author chose, so a persistent container is not created from it until
the researcher has chosen how it may run. The question is asked in the
dashboard before the start, and by `vaibify start` on the command line
(`--image-trust {restricted,as-built,inspect}`, plus `--with-credentials`
when no terminal can ask). Both read one text, in
`vaibify/config/imageTrust.py`. Nothing is preselected.

The answer is recorded in the host registry against the image **digest**
with the date, never against a name or tag, so an image that changes
under the same tag asks again. The launch functions refuse an unanswered
digest, so skipping the dashboard skips nothing. The answer is set only
through a route the in-container agent cannot reach, and a badge on the
project tile and in the project's settings shows it. Changing it asks
for confirmation because the container is recreated.

| Choice | What runs |
|---|---|
| Run it restricted | The idle entrypoint and the unprivileged user described below. |
| Run it as its author built it | The image's own entrypoint, started the way vaibify starts its own images (as root, with the entrypoint capabilities, so an entrypoint that drops to its own `USER` can). |
| Inspect only | Nothing persistent. The image runs only in the disposable verification lane: restricted, no credentials, network off. |

Stored credentials are a separate choice, off by default and unavailable
under Inspect only. "Credentials" means the project's configured secrets
(mounted read-only under `/run/secrets`), the credentials volume that
holds the container keyring, and the host bridge an in-container agent
uses to call back to vaibify. Unchecked, none of them is attached.

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
| Credentials | per the project's configuration | only if the researcher said so |

An image runs under it only if all of these hold:

- It contains an executable `/bin/sh` and a `sleep` that user 1000 can
  run (the idle command is `sh -c "sleep 2147483647"`). A distroless or
  scratch image, or one whose utilities only root can run, does not stay
  running, and the dashboard says so.
- Whatever the researcher needs inside it works as an unprivileged user
  with no entrypoint having run. Services the entrypoint would have
  started are not running; anything the entrypoint would have created,
  chowned or configured is as the image left it.
- The workspace volume is owned by user 1000. The image cannot chown it.
- Commands vaibify runs in the container run as the image's declared
  `USER`, or as `researcher` when it declares none. That user must exist
  in the image.

When a restricted container does not start, the dashboard reports the
failure and offers the other options. vaibify never retries silently,
and never shows a container that did not start as running. Outputs from
a restricted run can differ from the author's for reasons unrelated to
the science (file ownership, `HOME`, paths), so a Level 3 comparison of
a restricted run against the author's can diverge.

## Threat Model

Vaibify assumes the code running inside the container may be adversarial.
The defenses are designed to contain:

- **Filesystem escape** -- no wholesale host mount and no Docker socket;
  the host paths that are mounted (secret files, declared bind mounts,
  the X11 socket of a project that opted in) are listed in the table above.
- **Network exfiltration** -- optional network isolation blocks all traffic.
- **Credential theft** -- partially. Secrets resolved from the host's
  credential manager are mode-600 files, but they outlive the container
  and an agent's own logins persist in a plaintext keyring volume (see
  Secrets Management above). Treat "an agent was compromised" as "every
  configured provider's session was exposed".
- **Privilege escalation** -- the container runs as an unprivileged user
  with no `sudo` access.
- **Markup injected through project data** -- `project.json`,
  `state.json`, directory names and the dependency diagram are all
  writable from inside the container. The loader refuses a numeric or
  state field of the wrong type by name, every frontend module escapes
  with one quote-safe function, and the diagram is shown as an image, so
  none of them can add an element or an attribute to the dashboard.

Vaibify does **not** defend against kernel-level container escapes. For
high-security workloads, run Vaibify inside a virtual machine or use a
hardened container runtime such as gVisor.
