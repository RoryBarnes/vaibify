# Working on a remote machine

Vaibify can drive a hub running on another computer — a lab
workstation, a departmental compute server, anything you can reach with
`ssh`. The dashboard runs in the browser on the machine in front of
you; everything else happens over there.

```bash
vaibify remote compute-machine
```

That opens one SSH connection, forwards a loopback port, starts or
adopts a vaibify hub beside your projects on that machine, and opens a
signed-in browser tab. The remote hub never listens on anything but its
own loopback interface, so nothing about this puts vaibify on a network.

## Three places, and why the difference matters

Once the backend is somewhere else, a file can be in three places, and
several buttons that used to be unambiguous stop being so.

| Where | What it means |
|---|---|
| **Observer machine** | The computer you are sitting at, running the browser |
| **Execution host** | The remote machine running the vaibify hub |
| **Execution environment** | The container, or the project directory in host mode |

In Docker mode the execution host and the execution environment are
different filesystems. In host mode they are the same one.

The dashboard shows a **REMOTE** badge naming the execution host
whenever you are driving another machine, because every sentence about
"this machine" needs a subject once there are two of them.

## Before it will work

**Both machines need the same version of vaibify.** The helper refuses
to adopt a hub of a different version rather than drive it with a
protocol they may not share, and it names both versions when it
refuses.

**Vaibify must be on the remote user's *non-interactive* PATH.** This
is the constraint that catches people, so it is worth being blunt: `ssh
compute-machine vaibify --version` must print a version. If it prints
`command not found`, `vaibify remote` will fail with "the remote
produced no vaibify startup record", and that is why.

A non-interactive SSH command does not read the files you normally edit
— Ubuntu's default `.bashrc` returns immediately for non-interactive
shells — so a `pip install --user`, a virtualenv, or a conda
environment activated by your shell profile is *invisible* to it.
Options, in rough order of preference:

- install vaibify somewhere already on the default PATH, or symlink its
  entry point into `/usr/local/bin`;
- put the directory on the PATH *above* the non-interactive early-exit
  in the remote user's shell configuration.

**SSH configuration belongs to SSH.** Proxy jumps, identity files,
non-standard SSH ports, and usernames go in `~/.ssh/config`, where
OpenSSH already understands them. Vaibify does not reimplement any of
that, and deliberately accepts only a plain `[user@]host`.

**There is no `--project` option**, on purpose. A project name may
contain a space, and OpenSSH hands its remote command to the far side's
login shell, so passing one would mean quoting user text into a remote
shell command. You choose the project in the dashboard once the tunnel
is up, over HTTP, where that is a solved problem.

## What happens when the connection drops

This is the part worth reading before you need it.

**The remote hub is not tied to your tunnel.** It is started detached
and keeps running, so closing your laptop does not stop a pipeline. Its
idle timeout is raised for remote sessions so it does not retire while
you are away.

**For fifteen minutes, your session is held.** The client keeps trying
to rebuild the tunnel for that long, and the hub keeps your session
valid for at least as long as the client keeps trying — the two are
derived from one number precisely so they cannot disagree.

- **Back within the window:** the dashboard reconnects and you carry on.
  Streamed output produced while you were away is not replayed, but the
  run state is reconciled by the ordinary polling.
- **Back after the window:** re-run `vaibify remote`. If exactly one
  session there lost its browser, it is handed back to you — the
  project, its lock, and anything it was running were all still yours.
  You will see "Picking up where you left off". If several sessions are
  waiting, vaibify signs you in fresh and lets you choose, rather than
  guessing which one was yours.

**A run is never interrupted by any of this.** Losing a browser ends
that browser's authority over the project; it never ends the project's
work.

## What happens to an open terminal

**A dropped connection ends your shell, and the pane comes back with a
new one.** It says so rather than pretending otherwise.

This is deliberate. Closing the socket terminates the recorded session
and proves it dead, and that proof is what lets vaibify report honestly
on whether a project is quiet. A pane that silently reattached to a
"resumed" shell would be claiming something vaibify cannot verify.

The practical consequence: **anything you need to survive a
disconnection belongs in a step, not in the terminal.** Steps are
durable and their output is recorded; a terminal is neither. The banner
at the top of every remote shell names the machine it is running on for
the same reason.

## Moving files

Three actions, named for where things actually go:

- **Download to this computer** streams the file through the browser, so
  it lands on the machine you are sitting at. This is almost always what
  you want, and it is what right-clicking a file in the Files panel does.
- **Upload from this computer** sends a local file into the execution
  environment.
- **Copy to execution-host path** copies from a container workspace to
  the remote machine's own filesystem. It is offered only when those are
  genuinely different places — in host mode they are the same
  filesystem, so it is hidden rather than performing a copy that goes
  nowhere.

## What a remote session does not change

**Security is unchanged, because nothing was relaxed to make this
work.** SSH provides the encryption and proves *you* are the remote
user. Everything after that is the same machinery a local dashboard
uses: the browser still redeems a one-time capability for a session
credential, still claims a project and holds a lease, and the hub still
enforces its loopback Host and Origin checks. Through the tunnel your
browser simply *is* a loopback client, which is why nothing needed a
remote exemption.

**Host mode is still uncontained.** Running on a dedicated remote
machine does not change what host mode claims: commands run with your
full user authority, and PROOF Level 3 and Supervised attribution
remain unavailable there. The dashboard says so in both modes.

**Two dashboard actions are hidden in a remote session** — opening a new
vaibify window, and opening the project in VS Code. Both hand the
browser an address, and through a tunnel that address resolves to the
computer you are sitting at rather than the one doing the work. They are
absent rather than broken, because a dead tab looks like a bug.

## Batch schedulers are not supported

Slurm, PBS and LSF are out of scope for this release, and putting
`sbatch` in a step's command is not a workaround: the submission
process exits successfully while the job is still queued, so vaibify
would report work finished that had not started. A remote machine is a
machine you run things on directly.

## Troubleshooting

**"the remote produced no vaibify startup record"** — usually the PATH
problem above. Check `ssh <host> vaibify --version` first. It can also
mean SSH itself could not authenticate, in which case the SSH error is
included in the message.

**"a vaibify hub is already running on port N, but it is version X"** —
the two installations differ. Upgrade one, or pass `--port` to use a
different port.

**"something is already listening on port N ... and it is not a vaibify
hub"** — something else on the remote machine holds that port. Pass
`--port` with another number.

**"port N is already in use on this machine"** — the *local* side of the
forward is taken. Omit `--port` and let vaibify choose.

**The tab says the session expired** — you were away longer than the
hold window. Re-run `vaibify remote`; if your project is still there it
is handed back.

**A terminal left the project needing reconciliation** — a shell whose
descendants could not be proven dead leaves the project reporting
quiescence as unproven. Run `vaibify reconcile` on the execution host.
Note that this needs a shell on that machine, so it is worth having a
way in that does not depend on the tunnel.
