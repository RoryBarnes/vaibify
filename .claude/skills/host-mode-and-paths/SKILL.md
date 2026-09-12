---
name: host-mode-and-paths
description: Path handling across container mode and host mode: posixpath versus os.path, resolving project roots and scratch directories instead of writing /workspace or /tmp as constants, the project-repo rule, and the Linux bridge-gateway bind. Use when composing any path that a container or a host project will open.
---

# Paths, host mode, and project roots

Container paths are POSIX on every host operating system; host paths
are the host's. Host mode survives that rule by staying POSIX and
refusing Windows rather than accommodating it. Never write a root as a
constant -- ask the resolver.

The repository-wide rules in `AGENTS.md` still apply; this file is
the detail for this subsystem.

**Container paths are `posixpath`, host paths are `os.path`.**
`workflowManager.py` handles container paths, which are POSIX on every
host operating system. Any module handling host paths must use
`os.path`, whose separator is the host's. A helper shared between the
two lanes must be *pure* (e.g. `flistValidateOutputFilePaths`);
unifying the path handling itself would silently mangle one lane or
the other, and the failure would not surface until a cross-platform
user hit it.

**Host mode does not repeal that rule; it survives it by staying
POSIX.** A host project's pipeline runs on the researcher's own
machine, so `workflowManager` now composes paths that are host paths —
and it still uses `posixpath`, deliberately. Host mode is macOS and
Linux only, where `posixpath` and `os.path` are the same module, so one
implementation serves both modes exactly. The boundary is Windows: the
step commands are composed `bash -c` text and the POSIX path guards
weaken silently there, so Windows is refused rather than accommodated,
and this paragraph is the reason a reader will not find a host-path
fork of the workflow manager. One was tried (the withdrawn
`director` module) and abandoned — swap the connection object, never
fork the path handling.
Modules that are host-only (`vaibify/host/`) still use `os.path`,
because they say what they mean.

**Never write `/workspace` — or `/tmp` — as a constant.** Both name a
root, and a host project's roots are different ones. Ask
`projectRoots.fsResolveProjectRoot(sResourceId, sContainerRoot)` for
the root a project's FILES live under, and
`projectRoots.fsResolveScratchDirectory(sResourceId, sOperationName,
sContainerScratchRoot)` for the one an EPHEMERAL file may be written
to. The container answer is passed in at every call site, so that
module never becomes a second authority on what those roots are; only
a host resource overrides it, and its scratch answer is a private
0700 directory under the host-diagnostics subtree, which is the only
ephemeral root the host path guard admits. A `/tmp` literal is not a
style problem there — it is a refusal, and the whole introspection
lane answered 500 for a host project until this existed.
`tests/testHostModeProjectRoots.py` carries a symmetric falsification
pair for each resolver, and the container direction is the one with
the wider blast radius: a container handed the host answer gets a
path that does not exist inside it.

**On Linux the hub binds the Docker bridge gateway beside loopback,
and only there.** A container dials the hub as `host.docker.internal`.
On macOS the daemon's VM forwards that name to the host's loopback, so
a `127.0.0.1` bind answers; on Linux `host-gateway` IS the bridge
gateway (`172.17.0.1` by default) and a loopback-only socket refuses
the packet. Every in-container `vaibify-do` call on every Linux box
failed that way for as long as the agent bridge existed (found
2026-09-10), and nothing noticed because the dashboard talks over
loopback and an agent that cannot reach the backend improvises in the
shell. `serverLaunch.fnRunServer` binds the sockets itself and hands
them to uvicorn, with the gateway read from the DAEMON
(`bridgeGateway.fsResolveDockerBridgeGateway`) and never hard-coded.
Three things not to undo: macOS stays loopback-only (the second socket
would be exposure with no traffic behind it); an unknown gateway
degrades to loopback and is ANNOUNCED at launch, because a silent
degrade is the defect; and `0.0.0.0` is never the answer, since the
session file carries a bearer token and the gateway address is
reachable from every container on the daemon and nothing beyond it.
The Host-header check refuses a bridge-address request that carries no
per-container agent token, so the wider bind opens no route the token
does not gate. `tests/testServerLaunchContract.py` is the guard, and
its live leg runs a real uvicorn on two addresses on the Linux lane.

**Do not revert to `/workspace`-as-repo.** Every vaibify workflow
must live inside a git repository — its "project repo" —
auto-detected from the project.json's parent via
`containerGit.fsDetectProjectRepoInContainer`. `/workspace` is a
Docker-managed named volume, not a repo; it is only the discovery
root. Routes in `vaibify/gui/routes/gitRoutes.py` must thread
`dictWorkflow["sProjectRepoPath"]` into every `containerGit.*` call
(`testGitRoutesAlwaysPassProjectRepoToContainerGit` enforces this),
and no module may hardcode `/workspace/.vaibify/test_markers`
(`testNoWorkspaceRootedMarkerHardcodeInSource` enforces this — test
markers live at `<sProjectRepoPath>/.vaibify/test_markers/`). Step
`sDirectory` values and all `saOutputDataFiles` /
`saPlotFiles` paths are repo-relative; absolute or `..`-escaping
values are rejected at load time. A silent fallback to the
`/workspace` default reintroduces the all-grey-badges bug and
desynchronizes marker writes from marker reads. See
[docs/architecture.md](docs/architecture.md) — the "Workflow = git
repo" section — for the full rationale. A container may host
multiple workflows in different project-repo subdirectories; the
active workflow determines the badge scope.
