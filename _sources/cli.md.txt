# CLI Reference

The vaibify core is fully scriptable. Everything the dashboard does can
also be driven from the command line, so a build, a run, a test pass,
or a PROOF report can be part of a script or a scheduled job. The
other pages describe the dashboard; this page is the command-line
counterpart.

The command is `vaibify`, with the shorthand `vaib`. Every command
prints its own options, and that output is the authority:

```bash
vaibify --help             # every command
vaibify <command> --help   # one command's options
```

## Global options and project targeting

| Option | Meaning |
|--------|---------|
| `--config PATH` | Use this `vaibify.yml` (default: `./vaibify.yml`) |
| `--port N` | Port for the hub. By default the hub reuses the port it used last (so a bookmarked tab keeps working) and moves only if another program holds it; an explicit port is used as given and fails if taken |
| `--no-browser` | Serve without opening a browser. The process still runs in the foreground; it is not a daemon |
| `--version`, `--help` | Print and exit |

Run with no command, `vaibify` starts the **hub**: the dashboard for
all registered projects. Commands that act on a project resolve it in
this order: `--project/-p NAME`, then `--config PATH`, then a
`vaibify.yml` in the current directory, then the only registered
project. With several registered projects and none of these, the
command lists them and exits. A project created with `init` or
registered with `register` can be targeted with `-p` from any
directory.

## Commands

| Command | Purpose |
|---------|---------|
| `init` | Create a project in the current directory from a template, or just a `vaibify.yml` |
| `register [DIRECTORY]` | Register an existing directory containing a `vaibify.yml`; writes no files |
| `setup` | Open the setup wizard in a browser, on port 8051 |
| `config edit \| export FILE \| import FILE` | Edit `vaibify.yml` in `$EDITOR`, write it to a file, or replace it from one |
| `doctor` | Diagnose this machine, the container, and the project; changes nothing |
| `repair dns` | Clear a container's stale name-resolution state |
| `build` | Build the image from `vaibify.yml` |
| `start` | Start the container |
| `stop` | Stop the container; the workspace volume remains |
| `status` | Show container, image, and volume state, and optionally the PROOF level |
| `destroy` | Delete the workspace volume, then offer to delete the images |
| `connect` | Open a shell in the running container |
| `push SOURCE DESTINATION` / `pull SOURCE DESTINATION` | Copy files between the host and the workspace |
| `ls [PATH]` / `cat PATH` | List a container directory or print a container file |
| `verify` | Run the container isolation check |
| `run` | Run every step, one step, or from a step onward |
| `workflow` | Summarize the workflow or one step |
| `test` | Run the tests of every step or one step |
| `verify-step` | Record your sign-off on a step |
| `generate-standards` | Create or refresh a step's `tests/quantitative_standards_<step>.json` from its data |
| `reproduce` | Verify the Level 3 reproducibility envelope, or reproduce a published project |
| `gui` | Open the hub, or a single-project viewer |
| `open CONTAINER` | Move a container's live session into a fresh browser tab |
| `sessions` / `sessions stop` | List or stop live hubs and viewers |
| `do <action>` | Run any dashboard action from the host, as the researcher |
| `reconcile CONTAINER` | Prove a quarantined container's interrupted operations settled |
| `secret` | Store, check, or delete an agent provider's API key |
| `revoke github\|overleaf\|zenodo` | Remove a stored credential and revoke it upstream where possible |
| `remote DESTINATION` | Open a dashboard for another machine over SSH |
| `remote-helper` | The far end of `vaibify remote`; not run by hand |

Vaibify has no `publish` command. Zenodo and GitHub publication run
through the PROOF Level 2 rows in the dashboard and the matching
`vaibify do` actions (`push-to-github`, `publish-to-zenodo`).

## Creating and configuring projects

### vaibify init

```bash
vaibify init                              # list the installed templates
vaibify init --template NAME [--name NAME] [--force]
vaibify init --name NAME [--minimal] [--force]
```

| Option | Meaning |
|--------|---------|
| `--template NAME` | Copy a template (`sandbox`, `toolkit`, `workflow`, or a custom one; see [Environments and Projects](environmentsAndProjects.md)) into the current directory |
| `--name NAME` | Project name. Given alone, writes a `vaibify.yml` with no template |
| `--minimal` | The smallest `vaibify.yml` that still builds: no features, no packages |
| `--force` | Overwrite an existing `vaibify.yml`; without it, `init` refuses |

With a template, `init` copies the template's files into the current
directory, moves any `project.json` into `.vaibify/projects/`, writes a
`vaibify.yml` from the built-in defaults, and registers the project. It
refuses, before writing anything, when `.vaibify/projects/project.json`
already exists. Because a build clones only the repositories named in
`vaibify.yml`, `init` warns when a template's `container.conf` lists
repositories the new `vaibify.yml` does not carry. A name already
registered to a different directory is scaffolded but not registered,
and the command says so.

### vaibify register and vaibify config

`vaibify register [DIRECTORY]` adds an existing directory (default:
the current one) to the registry so `-p` finds it; the directory must
contain a `vaibify.yml`, and nothing is written there.

`vaibify config edit` opens `vaibify.yml` in `$EDITOR`;
`vaibify config export FILE [-p NAME]` writes the current configuration
to a file; `vaibify config import FILE` replaces the current
configuration with a file's contents. Every field is documented in the
[configuration reference](install.md#configuration-reference).

## Diagnosing and repairing

### vaibify doctor

```bash
vaibify doctor [--quiet] [--build | --start | --container] [--online]
               [--json] [--explain CHECK] [-p NAME]
```

Doctor examines three scopes: **host** (the Python's architecture, the
Docker context and endpoint, the daemon and runtime, storage, memory,
CPU), **container** (network attachments, name resolution, proxy path),
and **project** (vaibify's own records of the project); every scope
also reports the Agent Council's credential-test records. With no
scope flag every scope runs; `--build` and `--start` run the host checks relevant to
that step, and `--container` runs the container and project checks.
Each check reports `ok`, `warn`, `fail`, or `not checked` (with the
reason; never counted as ok). Every `warn` and `fail` names a next step
for the runtime this machine actually uses. For a running container,
doctor also reports whether it was created consistently with
`x11Forwarding`, which is fixed when a container is created (see
[Security Model](security.md)). `--quiet` hides the `ok`
lines, `--json` emits the results as JSON, and `--explain CHECK`
prints how one check decides and nothing else.

Doctor works before any project exists, so it is the first check after
an install. It warns when the Python running it is being translated
(an Intel-only Python on Apple Silicon), compares what a project will
request against what a Colima virtual machine has, and its
`shell-completions` line prints the exact line to add when TAB
completion is not loaded. In the dashboard, a failure toast's **Click
to run a diagnosis** runs the same checks without a project.

By default doctor resolves names but opens no connections. `--online`
permits connections to the host your project already depends on (its
first repository's host or the enabled agent's provider), over the port
and proxy your project would use, without authenticating.

| Exit | Meaning |
|------|---------|
| 0 | Everything applicable was assessed and nothing failed |
| 1 | At least one check failed |
| 2 | A scope you named contains a check that could not be assessed |

### vaibify repair dns

```bash
vaibify repair dns [-p NAME] [--recreate] [--yes]
```

Clears a container's stale name-resolution state by restarting it.
`--recreate` instead recreates the container from the image it is
running, which is required when DNS servers were baked into the
container from outside vaibify. `--yes` skips the confirmation prompt.

## Environment lifecycle

### vaibify build

```bash
vaibify build [--no-cache] [-p NAME]
```

Runs the [checks before a build](install.md#checks-before-a-build),
then builds the base image and one layer per enabled feature.
`--no-cache` ignores Docker's build cache. When the build produces an
image different from the one the recorded results came from, it says
so, and whether vaibify's own build recipe changed too (if not, the
difference came from outside vaibify). On macOS, a long build can be
kept from sleeping with `caffeinate -s vaibify build`.

### vaibify start

```bash
vaibify start [-d] [--gui [--port N]] [--jupyter] [-p NAME]
              [--image-trust restricted|as-built|inspect] [--with-credentials]
              [COMMAND]
```

| Option | Meaning |
|--------|---------|
| `-d`, `--detach` | Start in the background and return, as a script needs. A `COMMAND` is refused with `--detach` |
| `--gui` | Also open the single-project viewer, on `--port` or the project's `dashboardPort`. With neither, the first launch picks a free port (preferring 8050) and writes it to `dashboardPort` |
| `--jupyter` | Enable JupyterLab with port forwarding |
| `--image-trust` | How an image vaibify did not build may run. Asked interactively on a terminal; required without one |
| `--with-credentials` | Let that image's code read your stored credentials. Needs `--image-trust`; never implied |

Without `--detach`, `start` attaches a terminal to the container, or
runs `COMMAND` in it. See [Security Model](security.md) for what each
image-trust choice does.

### vaibify status

```bash
vaibify status [--proof] [--json] [-p NAME]
```

Shows the container, image, and volume state. `--proof` adds the
project's PROOF level and its blockers, the same ones the PROOF tab
shows; `--json` emits the environment and PROOF status as one JSON
object.

### vaibify stop and vaibify destroy

`vaibify stop [-p NAME]` stops the container; the workspace volume
remains.

```{warning}
`vaibify destroy` deletes the project's workspace volume: every file in
the container that has not been committed and pushed. The volume is
Docker-managed storage, not a directory on your computer, so it is the
only copy. The confirmation prompt is the only guard.
```

After the volume, `destroy` offers to remove the project's images. It
does not remove the container itself (stop it with `vaibify stop`) or
the volume that holds the container's stored credentials, which
remains until you remove it with `docker volume rm`.

### vaibify verify

`vaibify verify [-p NAME]` runs `checkIsolation.sh` inside the running
container; what it checks is in [Security Model](security.md).

## Shell helpers

The first `vaibify` command after an install or upgrade configures
shell completion and three helper aliases for bash, zsh, and fish. The
setup only appends to your shell's configuration file (`~/.zshrc`;
`~/.bash_profile` on macOS or `~/.bashrc` on Linux;
`~/.config/fish/config.fish`) and never edits or removes a line.

| Alias             | Shorthand      | Runs              |
|-------------------|----------------|-------------------|
| `vaibify_connect` | `vaib_connect` | `vaibify connect` |
| `vaibify_push`    | `vaib_push`    | `vaibify push`    |
| `vaibify_pull`    | `vaib_pull`    | `vaibify pull`    |

On macOS the setup also links Colima's Docker socket to
`/var/run/docker.sock` when that path is free and writable without
`sudo`.

### Tab completion

Press TAB after `vaibify push` or `vaibify pull` (or their aliases) to
complete a path inside the project. `vaibify pull <TAB>` offers paths in
the container; `vaibify push data.csv <TAB>` offers container paths for
the destination. Container paths may be relative to the workspace root
(`StepName/output.csv`) or absolute. `-p NAME` is honored. A host
project has no container, so its paths complete from the project's own
directory. Nothing is offered while the container is stopped. In bash,
a path containing `=` or `:` is not completed.

### If TAB does nothing

Run `vaibify doctor`. Its `shell-completions` line reads the
configuration file of the shell named in `$SHELL` and, when that file
does not load vaibify's completion script, prints the exact line to
add. To rerun the setup, remove its marker and run any command:

```bash
rm ~/.vaibify/.setup_done
vaibify --version
```

## Files and shells

| Command | Meaning |
|---------|---------|
| `vaibify connect [-p NAME]` | Open a shell inside the running container |
| `vaibify push SOURCE DESTINATION [-p NAME]` | Copy a host file into the workspace. A relative container path is read from `workspaceRoot` |
| `vaibify pull SOURCE DESTINATION [-p NAME]` | Copy a workspace file to the host. A relative container path is read from `workspaceRoot` |
| `vaibify ls [PATH] [--json] [-p NAME]` | List a container directory (default `/workspace`); a relative path is read from `/workspace` |
| `vaibify cat PATH [-p NAME]` | Print a container file; a relative path is read from `/workspace` |

The shell helpers that the first command after an install configures
(see [Shell helpers](#shell-helpers)) wrap the first three:

```bash
vaibify_connect -p my-analysis
vaibify_push -p my-analysis data.csv /workspace/data.csv
vaibify_pull -p my-analysis /workspace/results.csv ./results.csv
```

## Workflows and tests

Step numbers on these commands are 1-based. Labels such as `A09` are
per-type sequential (the ninth automated step), as in the dashboard.

| Command | Meaning |
|---------|---------|
| `vaibify run [--step N \| --from N] [-p NAME]` | Run every step, one step, or from a step onward |
| `vaibify workflow [--step N] [--json] [-p NAME]` | Summarize the workflow, or show one step in detail |
| `vaibify test [--step N] [--json] [-p NAME]` | Run the tests of every step or one step. Exits 1 on any failure, 2 when the step number is out of range |
| `vaibify verify-step --step STEP --status passed\|failed\|untested [-p NAME]` | Record your sign-off on a step. `--step` takes a label or a 1-based number |

### vaibify generate-standards

```bash
vaibify generate-standards --step-dir PATH [--rtol FLOAT] [--detect-stochastic]
vaibify generate-standards --workflow PATH --step-label LABEL [--rtol FLOAT] [--detect-stochastic]
```

Creates or refreshes a step's `tests/quantitative_standards_<step>.json` from
live data, outside the dashboard: for one step directory, or by step
label from a workflow file. `--rtol` sets the default relative
tolerance when a fresh standards file is generated, and
`--detect-stochastic` scans the step's `data*.py` scripts for unseeded
random-number generation first. The dashboard's equivalent is
**Generate**; see [Testing Model](testing.md).

## Reproducibility

### vaibify reproduce

```bash
vaibify reproduce [--repo PATH] [--rerun] [--workflow NAME] [--skip-tier N]
vaibify reproduce --from SOURCE [--prepare | --rerun] [--workflow NAME] [--allow-emulation]
```

The first form walks a project's Level 3 envelope in tiers, from a
checked-out repository (`--repo`, default: the current directory):

| Tier | What it does |
|------|--------------|
| 1 | Checks `MANIFEST.sha256` against the files on disk |
| 2 | Installs `requirements.lock` with `pip install --require-hashes` into your current Python environment, retrying with `uv` on a hash error if `uv` is installed |
| 3 | Pulls the pinned image by digest |
| 4 | Runs the same Level 3 readiness checks as the dashboard |
| 5 | Only with `--rerun`: reruns the workflow in a disposable shadow container from the pinned image, re-hashes its outputs, and writes `.vaibify/l3_attestation.json`. It needs the project's container running, since the copy is taken from it |

`--skip-tier` skips one of tiers 1–4 and may be repeated. `--workflow`
is required when the container holds more than one workflow. Exit 0
means every selected tier passed, 1 that one failed, 2 a usage error
such as a missing envelope file. A run ends with
`L3 reproduction confirmed and attested.`, `L3 reproduction ready`
(without `--rerun`), or `L3 reproduction failed; see tier output above.`
The shadow rerun uses an image already on your own Docker daemon, so
only tiers 1–4 (and `reproduce.sh`) show whether a fresh machine could
obtain the image. Each of tiers 1–3 is an ordinary command
(`sha256sum -c`, `pip install --require-hashes`,
`docker pull <image>@sha256:…`) that anyone reading the envelope can
run by hand.

With `--from`, vaibify reproduces a **published** project from an
`https://` or `ssh://` clone URL, a `user@host:path` address, or a
clean local clone under your home directory, without installing it as
one of your own projects:

| Form | What it does |
|------|--------------|
| `--from SOURCE` | Stages an exact snapshot of one commit, validates it, prints what a rerun would use (workflow, commit, pinned image, platform, archived deposit), and discards it. Nothing is pulled or run |
| `--prepare` | Also obtains the pinned image (registry, archived deposit, or local copy), so a long download can be done ahead of time |
| `--rerun` | Obtains the image, reruns the snapshot in a shadow container, compares the outputs with the project's manifest, and writes a report under `~/.vaibify/reproductions/reports/`. Exits 0 only when the verdict is *reproduced* |
| `--allow-emulation` | Accepts a pinned build for another processor; the verdict says so |
| `--workflow` | Selects the workflow when the repository holds several |

`--from` cannot be combined with `--repo` or `--skip-tier`. See
[Reproducibility](reproducibility.md).

## The hub, sessions, and ports

### vaibify gui and vaibify open

`vaibify` and `vaibify gui` open the hub. `vaibify gui -p NAME` serves
a single-project viewer on port 8050. Several hubs can run on one
machine. Each container may be open in only one browser session at a
time, so a second `vaibify start` on a container held elsewhere is
refused. `vaibify open CONTAINER` moves a held session into a fresh
browser tab; it works only from the host's own keyboard, never from a
container or a remote peer, and it is also how to get a fresh tab
after a session reaches its lifetime limit.

### vaibify sessions

Hubs and viewers run in the foreground of the terminal that started
them. Closing a browser tab does not stop them. On the host only:

```bash
vaibify sessions              # pid, role, port, start time, containers held
vaibify sessions stop PID     # stop one session
vaibify sessions stop --all   # stop every session except this one
```

`stop` sends `SIGTERM`, so the server releases its session and
container locks, and it refuses any PID that is not a live vaibify
session.

## vaibify do

Every dashboard action has a `vaibify do` subcommand, generated from
the same catalog the dashboard and the in-container `vaibify-do` use,
so the three cannot drift apart. `vaibify do` authenticates as the
researcher, so actions reserved for the researcher (user-only actions,
such as `clean-outputs`) are available here; `bAgentSafe` constrains an
agent in a container, not the researcher at their own terminal. The
agent's in-container usage is described in [For Agents](forAgents.md).

```bash
vaibify do                          # list every action
vaibify do run-step --help          # one action's arguments
vaibify do run-step A09             # run one step
vaibify do run-selected-steps A03 A04
vaibify do check-l2-readiness --json
```

| Option | Meaning |
|--------|---------|
| `-p`, `--project` | Target project |
| `--port` | Port of the hub to drive, when several are running |
| `--workflow` | Container path of the `project.json` to use |
| `--json` | One JSON object per line |
| `--dry-run` | Print the call without sending it |
| `--timeout` | Seconds to wait for the response, or for the next event of a run (`0` waits indefinitely) |

Options follow the action name. Step selectors on the run actions are
bare words (labels such as `A09`, or 0-based indices); other fields
are `key=value` pairs, or one JSON object. A hub must be running (start
one with `vaibify` in another terminal), and the dashboard shows every
action as it runs. The command claims the container for one action and
then releases it, so a container open in a dashboard tab is refused
rather than taken over: close that tab, or run the action from inside
the container with `vaibify-do`.

## Recovering a quarantined container

```bash
vaibify reconcile CONTAINER [--yes]
vaibify reconcile CONTAINER --break-glass SHA256
vaibify reconcile CONTAINER --force-abandon OPERATION
vaibify reconcile CONTAINER --terminate-recorded
vaibify reconcile CONTAINER --abandon-host-journal SHA256
```

Once a terminal has been used in an environment, vaibify reports its
quiescence as unproven on release or shutdown, and `reconcile` is how
to settle it. A plain run shows which operation was mid-write, when,
and in which container; proves the recorded writer dead and the
operation settled; and only then clears the quarantine, or refuses and
leaves the container quarantined. The dashboard's **Reconcile now**
button runs the same proof. The other options are destructive
judgments available only here:

| Option | Meaning |
|--------|---------|
| `--yes` | Skip the confirmation prompt |
| `--break-glass SHA256` | Clear a malformed marker whose raw bytes hash to this value (a plain run prints it) |
| `--force-abandon OPERATION` | Poison a wedged operation on the live hub that holds the container; changes are refused until it is reconciled |
| `--terminate-recorded` | Host projects only: signal the process groups vaibify journaled, then retry the proof. A record whose identity cannot be proven is reported, never signaled |
| `--abandon-host-journal SHA256` | Host projects only: give up on proving a malformed marker with this hash, recording the abandonment beside the journal. Proves nothing |

## Credentials

| Command | Meaning |
|---------|---------|
| `vaibify secret set-provider-key --provider NAME` | Store an agent provider's API key, read from a prompt that does not echo |
| `vaibify secret provider-key-status --provider NAME` | Report whether a provider's key is configured |
| `vaibify secret delete-provider-key --provider NAME` | Remove a provider's key |
| `vaibify revoke github [--keyring-slot SLOT]` | Remove the stored GitHub credential, or one repository's with `--keyring-slot`, and revoke it upstream where possible |
| `vaibify revoke overleaf` | The same, for Overleaf |
| `vaibify revoke zenodo [--instance sandbox\|production]` | The same, for one Zenodo instance (default `sandbox`) |

`revoke` prints whether the upstream token was revoked and whether the
local copy was cleared, and exits non-zero if the local copy was not
cleared. For GitHub it also runs `gh auth logout`. Overleaf and Zenodo
offer tools no way to revoke a token, so for them the command clears
the local copy and names the account page where you revoke the token
yourself; do that too, because clearing a local copy does not
invalidate a token that may have been copied elsewhere. `revoke` acts
on the host only: a Zenodo token entered for a container project is
held inside that container, so revoke it on Zenodo's account page,
after which the copy in the container no longer works.

## Remote machines

```bash
vaibify remote DESTINATION [--port N]
```

Opens a dashboard for another machine over SSH: it starts or adopts a
hub there, forwards one loopback port, and opens a signed-in browser
tab. `--port` chooses that port; both ends use the same number.
Pressing Ctrl-C closes the tunnel and leaves the remote hub running.
`vaibify remote-helper --port N` is the far end of that connection and
is not for interactive use.

- **Install the same vaibify version on both machines.** The helper
  refuses to drive a hub of another version and names both versions.
- **Vaibify must be on the remote user's non-interactive PATH**:
  `ssh DESTINATION vaibify --version` must print a version (see
  [Installing for remote access](install.md#installing-for-remote-access)).
- **`DESTINATION` is a plain `[user@]host`.** Proxy jumps, identity
  files, ports, and usernames belong in `~/.ssh/config`.
- **There is no project option.** You choose the project in the
  dashboard once the tunnel is up.
- **After a dropped connection**, run `vaibify remote` again. If
  exactly one session lost its browser, it is handed back to you; if
  several are waiting, you sign in fresh and choose.

| Message | Meaning |
|---------|---------|
| "the remote produced no vaibify startup record" | Usually the PATH problem above; check `ssh DESTINATION vaibify --version`. If SSH could not authenticate, its error is included |
| "...is version X and this is Y" | The two installations differ. Upgrade one, or pass `--port` |
| "...it is not a vaibify hub" | Another program holds that port on the remote machine. Pass `--port` with another number |
| "port N is already in use on this machine" | The local end of the forward is taken. Omit `--port` and let vaibify choose |
| The tab says the session expired | You were away longer than the hold window. Run `vaibify remote` again |

A terminal session on the remote machine can leave a project needing
`vaibify reconcile`, which must run on that machine, so keep a way in
that does not depend on the tunnel. See
[Connecting to External Resources](externalResources.md).
