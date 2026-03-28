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

2. **Ephemeral mounting** -- resolved secrets are written to temporary
   files with mode 600 under `/run/secrets/` inside the container. These
   files are cleaned up when the container stops.

3. **Token hygiene** -- Zenodo requests use `Authorization: Bearer` headers
   (never URL parameters). Overleaf uses Git credential helpers (never
   URL-embedded tokens).

## Security Audit

Run the built-in isolation audit to verify the container's security posture:

```bash
vaibify verify
```

The audit script (`checkIsolation.sh`) runs inside the container and checks
for:

- Docker socket accessibility.
- Privilege escalation paths (`sudo`, `setuid` binaries).
- Exposed ports beyond those declared in `vaibify.yml`.
- Secrets leaking into environment variables or process listings.
- Bind mounts that expose host directories.

The audit prints a pass/fail report. Any failure indicates a configuration
issue that should be resolved before running untrusted code.

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
