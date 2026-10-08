# Connecting to External Resources

A project reaches PROOF Level 2 by being published, and Level 3 also
needs its reproduction recipe and environment archived. Vaibify
connects a project to the four places where that happens: GitHub for
version control, Zenodo for permanent archives, Overleaf for the
manuscript, and arXiv for the preprint. It pushes files to the first
three, reads all four back, and compares SHA-256 hashes of every
published file with the copy on disk, so an agent (or anybody else)
cannot quietly change a file that has already left the machine without
the dashboard saying so.

The last part of this page covers driving a vaibify hub on another
computer over SSH.

## What each connection is for

| Service | What vaibify does with it | Direction | Role on the ladder |
|---|---|---|---|
| **GitHub** | The project repository's `origin` remote: commits and pushes selected files, then compares the published copies. | Push and verify | Level 2 mirror; Level 3 checks that the reproducibility envelope on GitHub matches the local one. |
| **Zenodo** | Uploads selected files to a deposit and publishes it with a DOI; separately, archives the container image. | Push and verify | Level 2 archive; Level 3 requires the envelope, the rebuild attestation and the environment image in permanent (non-sandbox) records. |
| **Overleaf** | Pushes figures into the manuscript project, and reads the manuscript sources back. | Push, pull and verify | Optional at Level 2. |
| **arXiv** | Downloads the e-print source of a posted preprint and compares its figures with yours. | Verify only | Optional at Level 2; checked only once a submission is recorded. |

Requirement tables for each level are on
[The PROOF Ladder](proofLadder.md); what the envelope contains and how
it is archived is on [Reproducibility](reproducibility.md).

## How vaibify holds your credentials

Every connection except arXiv needs a token, and the threat model
assumes an AI agent works inside the container. The rules are:

- **No token is ever placed in an environment variable, a
  configuration file, `vaibify.yml`, `project.json`, or a command
  line.** Environment variables and command lines are visible to
  process inspection and `docker inspect`; files in a repository get
  published.
- **Tokens live in established credential stores.** On the host that
  is your operating system's keyring, or your existing `gh auth`
  login for GitHub. Inside a container it is a keyring kept on a
  dedicated Docker volume, so a token survives a Rebuild without being
  baked into the image.
- **Tokens reach programs through short-lived channels.** Git is given
  a mode-700 helper script that reads the token at the moment git asks
  and is deleted afterwards; container-side programs receive a token
  on standard input. A GitHub credential the container itself needs
  is mounted read-only as a Docker secret file and handed to git by a
  credential helper only for requests to GitHub.
- **Error messages are redacted** before they reach the dashboard, so
  a server that echoes a credential back does not put it on screen.

Where each token lives depends on the service and on the kind of
project:

| Service | Container project | Host-mode project |
|---|---|---|
| GitHub | Your host's `gh auth` login, or a per-repository token in a keyring | Same |
| Zenodo | The container's keyring (one entry for the sandbox, one for production) | The host keyring |
| Overleaf | The host keyring | The host keyring |
| arXiv | No credential needed | No credential needed |

### What the in-container agent may do

The agent drives the dashboard through `vaibify-do`, and every action
is marked as agent-safe or researcher-only. Actions that make
something public under your name are researcher-only.

| Action | Agent may run it |
|---|---|
| Push selected files to GitHub | Yes |
| Push to Overleaf | No |
| Publish to Zenodo, edit Zenodo metadata, Make Permanent | No |
| Verify any remote, refresh the Overleaf mirror, read the manuscript back | Yes |
| Configure arXiv, declare an extra Zenodo record | Yes |

Run `vaibify-do --list` inside a container for the complete catalog.

## Setting up a connection

The first time you push to a service, the **Setup Connection** dialog
asks for what that service needs:

- **Overleaf**: the project ID (the string after `/project/` in the
  project's Overleaf URL) and a Git authentication token, generated in
  the Git integration section of your Overleaf account settings. Your
  Overleaf login password does not work.
- **Zenodo**: the instance (**Sandbox**, the default and the right
  choice for practice, or **Production**, which mints real DOIs) and a
  personal access token from that instance with the `deposit:write`
  and `deposit:actions` scopes. Sandbox and production are separate
  sites with separate accounts and tokens.
- **GitHub**: nothing, if `gh auth login` has been run on the host;
  otherwise a per-repository token.

**Connect** stores the token and immediately tests it against the
service. If the test fails, vaibify restores the token you had before
(or removes the new one if there was none), and the message says which
happened. A connection that cannot be made because the container runs
with network isolation is refused at once rather than after a timeout.

## GitHub

The GitHub mirror is the project repository's own `origin` remote.
There is nothing extra to create: initialize the repository, add a
GitHub remote, and push.

- **Sync → Push to GitHub** lists the project's files with their
  state. Choose files, give a commit message, and vaibify stages
  exactly those files, commits, and pushes. A retry after a failed
  push publishes what is already committed instead of stopping on
  "nothing to commit".
- The **Repos** panel's push commits changes to files git already
  tracks. Untracked files are never swept in; publish a new file
  through the file selection above.
- Before every push, vaibify asks GitHub which account the token
  belongs to and refuses unless that account name matches the owner
  named in the remote's URL.
- A container with no git author configured asks for one (**Set Git
  Identity**). The name and email are written into the project
  repository only, never into a global git configuration.

Verification compares against the repository the project last pushed
to from this hub, recorded at push time. A changed `origin` or a
`project.json` that names a different repository is refused rather
than trusted, because both can be edited from inside the container. A
project that has not pushed from this hub is asked to push once.

If commits are pushed outside the dashboard (for example by an agent
running `git push`), run the `reconcile-remote-state` action so the
badges catch up.

## Zenodo

**Sync → Archive to Zenodo** opens the push dialog with the project's
publication files: step outputs, the scripts that made them, the input
data they read, test standards, the AI declaration, `project.json`, and
the reproducibility envelope. That is the same set the Level 2 and
Level 3 Zenodo checks compare. **Edit
Zenodo metadata** sets the title, creators, license and keywords.
**Push Selected** or **Push All** uploads the files and publishes.

Four properties of Zenodo shape what vaibify does:

- **Deposits are flat.** Zenodo does not store directories, so files
  upload under their base names. A selection in which two files share a
  base name is refused before anything is uploaded, because the second
  would overwrite the first. Test files that vaibify generates carry
  a step-specific suffix so this never happens to them.
- **Published versions are immutable.** Changing a published file
  means publishing a new version of the record. Each version has its
  own DOI; vaibify always records the version DOI, never the concept
  DOI, because the concept DOI resolves to whatever is deposited next.
- **The sandbox is not an archive.** Sandbox records receive test
  DOIs, carry no preservation promise, and can be cleared at any time.
  A sandbox deposit can satisfy the Level 2 Zenodo check, but never
  Level 3. Nothing transfers between the sandbox and production, so
  **Make Permanent** on the affected row of the Project block publishes
  the current files as a new production record and keeps the sandbox
  identifiers in a superseded note.
- **Other records can hold your files.** Zenodo's own GitHub
  integration archives each GitHub release as a separate record. The
  Zenodo checks consult every record you declare, under **View →
  Zenodo Status**, and a file agrees when any declared record serves
  its exact bytes. A record that holds only a release tarball cannot be
  compared file by file.

When the recorded deposit and the instance the project is set to use
disagree (for example, a production deposit and a project switched
back to the sandbox), publishing is refused with two remedies: publish
where the record is, or start a new record on the chosen instance.

Archiving the container image to Zenodo is a separate action on the
Environment archive row, described in
[Reproducibility](reproducibility.md).

## Overleaf

**Sync → Push to Overleaf** copies figures into the bound Overleaf
project through Overleaf's Git integration. The dialog:

- asks for a **target directory**, chosen from a tree of the Overleaf
  project, and refuses absolute paths and `..`;
- shows which files are new, which would overwrite a different
  remote file, and which are unchanged;
- warns when Overleaf already holds a directory under different
  capitalization (Overleaf stores names case-insensitively but lists
  both spellings) and offers the existing spelling;
- asks you to confirm with **Overwrite anyway** before replacing a
  file that changed on Overleaf since your last push.

Only figure formats travel to a manuscript, so Overleaf rows list
figure files only. A successful push re-verifies the Overleaf row
automatically.

### Reading the manuscript back

The `pull-manuscript` action copies the Overleaf project's `.tex`,
`.bib`, `.bbl`, `.sty` and `.cls` files into the project repository's
`.vaibify/manuscript/` directory. The copy is ignored by git, so it can
never dirty the repository; it exists so the in-container agent can
read the paper and check it against the project's results rather than
answer from memory. The agent runs it with `vaibify-do pull-manuscript`.

## arXiv

arXiv offers no upload interface for tools, so vaibify only reads it.
**Sync → Configure arXiv…** records the preprint's identifier (for
example `2401.12345`). Vaibify then downloads the e-print source of
the **latest** version, extracts it safely, and compares each local
figure with the figure of the same base name. When a base name is
ambiguous, or a figure was renamed for the submission, add an explicit
mapping from local path to path inside the e-print. Removing the
identifier stops tracking. An untracked submission reads "not tracked"
and never blocks Level 2.

## How vaibify checks the published copies

A **verification** downloads each published file, computes its
SHA-256, and compares it with the SHA-256 of the same file on disk
right now. It does not compare against `MANIFEST.sha256`: the manifest
is a Level 3 artifact, and this check asks whether the files you hold
match the files you published.

| Service | How the remote copy is read |
|---|---|
| GitHub | Each file is fetched from the pushed branch over HTTPS. |
| Zenodo | Each file of every declared record is downloaded. |
| Overleaf | A local mirror of the Overleaf project is refreshed through git. |
| arXiv | The e-print source of the latest version is downloaded and unpacked. |

Results are cached in the repository's `.vaibify/syncStatus.json`, so
the dashboard can show them without contacting the services on every
refresh.

### When checks run

- **When you open a project, and when the dashboard reconnects**, it
  starts a check of every configured remote. Each badge pulses until
  its own answer arrives.
- **In the background**, a loop re-verifies every configured remote of
  every loaded project every 6 hours. Its first pass runs a minute or
  two after the hub starts, and it resumes its schedule across
  restarts.
- **A result older than 24 hours is stale.** It is shown as needing a
  check, never as passing.

A check that cannot finish (no network, a container with network
isolation, a service that is down) is reported as *uncheckable* with
the reason. It writes nothing, so the last good result stands, and it
is never shown as a divergence: red means the bytes were compared and
differ.

### Forcing a check

- **Verify now** on any row of the **Published copies** section of the
  Main tab's Project block.
- **Sync → Verify Reproducibility**, then **Re-verify** on one
  service.
- The ↻ **Refresh remote status** button above the step list re-reads
  the git remotes.
- From inside the container, `vaibify-do verify-remote github` (or
  `zenodo`, `overleaf`, `arxiv`).

### What the results look like

Each **Published copies** row groups the files it covers: **Differs
from the published copy**, **Not on the remote**, **Not checked yet**,
**Matching**, and **Not compared by vaibify**. Each file row in the
Steps and Project blocks also carries a small badge per configured
remote, tinted by that remote's state.

The **Verify Reproducibility** dialog shows one row per service:

| Field | Meaning |
|---|---|
| Status pill | Green: the last verification matched every file and is less than 24 hours old. Red: at least one file differs. Yellow: never verified, or stale. |
| Summary | `<matching>/<total> files match SHA-256`, with the first drifted files named. |
| Last verified | How long ago the last verification ran. |
| **Re-verify** | Runs a verification of that service now. |

A line at the top summarizes all services ("not yet verified", all in
sync, or how many files drifted across how many remotes), and a line at
the bottom says when the background loop last ran. **Verify Manifest**
checks the files on disk against `MANIFEST.sha256` instead.

## Automatic pushes

The **Auto Archive** checkbox in the project settings (the ⚙ button
above the step list) pushes a step's files that you have marked for
Overleaf and Zenodo automatically when that step's verification first
brings the project to Level 1. It is off by default. A failed
automatic push is logged and never blocks the step that triggered it;
the Sync menu remains the way to retry.

## Revoking credentials

`vaibify revoke` removes a stored token from the host keyring and, where
the service allows it, revokes it upstream:

```bash
vaibify revoke github --keyring-slot <slot>   # also runs gh auth logout
vaibify revoke overleaf
vaibify revoke zenodo --instance production   # default: sandbox
```

It prints whether the upstream token was revoked and whether the local
entry was cleared, and exits non-zero if the local entry was not
cleared. Overleaf and Zenodo offer no revocation interface to tools, so
the command clears the local copy and names the account page where you
revoke the token yourself; do that too, because a cleared local copy
does not invalidate a token that may have been copied elsewhere.
`vaibify revoke --help` describes the options.

`vaibify revoke` works on the host keyring only. A Zenodo token
entered for a container project lives in that container's keyring, so
revoke it on Zenodo's account page; the copy left in the container then
no longer works.

## Working on a remote machine

Vaibify can drive a hub running on another computer you reach with
`ssh`, such as a lab workstation or a departmental server. The
dashboard runs in the browser in front of you; everything else
happens over there.

```bash
vaibify remote compute-machine
```

That opens one SSH connection, forwards a loopback port, starts or
adopts a vaibify hub on the remote machine, and opens a signed-in
browser tab. The remote hub listens only on its own loopback
interface, so nothing puts vaibify on a network. `--port` chooses the
port; both ends use the same number.

### Three places

Once the hub is elsewhere, a file can be in three places:

| Where | What it means |
|---|---|
| **Observer machine** | The computer you are sitting at, running the browser. |
| **Execution host** | The remote machine running the vaibify hub. |
| **Execution environment** | The container, or the project directory in host mode. |

In container mode the execution host and environment are different
filesystems; in host mode they are the same. While you drive another
machine, the dashboard shows a **REMOTE** badge naming it.

### Prerequisites

- **The same vaibify version on both machines.** The helper refuses to
  drive a hub of another version and names both versions.
- **Vaibify on the remote user's non-interactive PATH.** `ssh
  compute-machine vaibify --version` must print a version. A
  non-interactive SSH command does not read your usual shell profile,
  so an install that is activated there (a `pip install --user`, a
  virtual environment, a conda environment) is invisible to it. Install
  vaibify somewhere already on the default PATH, link its entry point
  into `/usr/local/bin`, or extend the PATH above the non-interactive
  early exit in the remote shell configuration.
- **SSH settings belong in `~/.ssh/config`.** Proxy jumps, identity
  files, ports and usernames go there; `vaibify remote` accepts only a
  plain `[user@]host`.
- **There is no project option.** You choose the project in the
  dashboard once the tunnel is up, which keeps project names out of a
  remote shell command.

### When the connection drops

The remote hub is started detached and keeps running, so closing your
laptop does not stop a pipeline. Its idle timeout is raised for remote
sessions.

For 15 minutes, your session is held: the client keeps rebuilding the
tunnel, and the hub keeps your session valid at least that long.

- **Back within the window**, the dashboard reconnects. Output streamed
  while you were away is not replayed, but run state is reconciled by
  ordinary polling.
- **Back after the window**, run `vaibify remote` again. If exactly one
  session lost its browser, it is handed back to you ("Picking up where
  you left off"). If several are waiting, you sign in fresh and choose.

A run is never interrupted by any of this. Losing a browser ends that
browser's authority over the project, never the project's work.

### Terminals

A dropped connection ends your shell, and the pane returns with a new
one and says so. Closing the socket ends the recorded session and
proves it ended, which is what lets vaibify report honestly whether a
project is quiet. Anything that must survive a disconnection belongs in
a step, not in a terminal: steps are durable and their output is
recorded. Each remote shell's banner names the machine it runs on.

### Moving files

- **Download to this computer** streams a file through the browser to
  the observer machine. It is what right-clicking a file offers.
- **Upload from this computer** sends a local file into the execution
  environment.
- **Copy to execution-host path** copies from a container to the
  remote machine's own filesystem. It appears only when those are
  different places.

### What a remote session does not change

- **Security.** SSH encrypts the connection and proves you are the
  remote user. After that, the browser redeems a one-time capability
  for a session, claims the project, and passes the hub's loopback
  Host and Origin checks exactly as it would locally, because through
  the tunnel it *is* a loopback client.
- **Host mode is still uncontained.** Commands run with your full user
  authority on the remote machine, and PROOF Level 3 remains
  unavailable to a host-mode project.
- **Actions that hand the browser an address are hidden**: the **New
  vaibify window** buttons on the hub and project-list screens, and
  **Open in VS Code**. Through a tunnel those addresses resolve to the
  computer you are sitting at.

### Batch schedulers are not supported

Slurm, PBS and LSF are out of scope. Putting `sbatch` in a step's
command is not a workaround: the submission exits successfully while
the job is still queued, so vaibify would report unfinished work as
finished. A remote machine is one you run things on directly.

### Troubleshooting

| Message | Meaning |
|---|---|
| "the remote produced no vaibify startup record" | Usually the PATH problem above; check `ssh <host> vaibify --version`. If SSH could not authenticate, its error is included. |
| "a vaibify hub is already running on port N, but it is version X" | The two installations differ. Upgrade one, or pass `--port`. |
| "...it is not a vaibify hub" | Another program holds that port on the remote machine. Pass `--port` with another number. |
| "port N is already in use on this machine" | The local end of the forward is taken. Omit `--port` and let vaibify choose. |
| The tab says the session expired | You were away longer than the hold window. Run `vaibify remote` again. |
| A terminal left the project needing reconciliation | A shell whose descendants could not be proven ended leaves quiescence unproven. Run `vaibify reconcile` on the execution host, which needs a way in that does not depend on the tunnel. |
