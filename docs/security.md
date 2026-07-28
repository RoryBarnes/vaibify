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
| Unprivileged user      | The container runs as a non-root user via `gosu`. The root user is used only during image build. |
| No host filesystem     | The host filesystem is not bind-mounted by default. Files enter and leave the container through `vaibify push` and `vaibify pull`. |
| Workspace volume       | A Docker volume provides persistent storage at the configured `workspaceRoot`. Volumes are isolated from the host directory tree. |
| Network isolation      | Set `networkIsolation: true` in `vaibify.yml` to start the container with `--network none`, blocking all outbound traffic. |
| Localhost-only GUI     | The pipeline viewer and setup wizard bind to `127.0.0.1`, never `0.0.0.0`. |

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

## Threat Model

Vaibify assumes the code running inside the container may be adversarial.
The defenses are designed to contain:

- **Filesystem escape** -- no host mounts, no Docker socket.
- **Network exfiltration** -- optional network isolation blocks all traffic.
- **Credential theft** -- secrets exist only as ephemeral files with
  restrictive permissions.
- **Privilege escalation** -- the container runs as an unprivileged user
  with no `sudo` access.

Vaibify does **not** defend against kernel-level container escapes. For
high-security workloads, run Vaibify inside a virtual machine or use a
hardened container runtime such as gVisor.
