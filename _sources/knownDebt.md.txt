# Known technical debt

These are known, deliberate, and load-bearing. **Do not "fix" any of
them without discussion** -- each one is the honest interim for a
problem whose real fix is a product decision, and several look like
oversights precisely because the honest interim resembles one.

These are known, deliberate, and load-bearing — do not "fix" them
without discussion:

- `introspectionScript.py` duplicates format-handling logic from
  `dataLoaders.py`. Container scripts cannot import from the host.
- `dictCtx` abbreviates "context" although the style guide forbids
  abbreviating words shorter than eight characters. It is the per-hub
  context dictionary every route module receives, and its spelling is
  part of a registration signature used throughout the backend
  (`fnRegisterAll(app, dictCtx)`). Ruled an accepted, documented
  exception (ruling R4, 2026-10-02): neither style scanner flags it and
  it is not to be renamed in passing. `tests/testStyleInvariants.py`
  pins this entry. `el` in the frontend is NOT such an exception; it
  waits for a JavaScript naming lane and must not be mass-renamed
  piecemeal.
- `DockerConnection.fnWriteFileViaTar` no longer builds a tarball. It
  execs a fixed program as the container user and streams the bytes on
  stdin (`vaibify/docker/confinedWrite.py`), because a `put_archive`
  extracts as root and follows symlinks the in-container agent planted.
  The name is kept because the mutation ledger, the falsification
  registry and sixty-odd call sites key on it; renaming it is a
  mechanical follow-up, not a behavior change. `fnWriteTreeViaTar` no
  longer uses `put_archive` either: it streams a tar to a fixed receiver
  program (the same module) that lands each member relative to directory
  descriptors, so a planted symlink is refused rather than followed.
  `fnWriteFileViaTar` is now a thin wrapper over
  `fnWriteFileFromStream`, the streamed funnel: the program copies
  stdin in 1 MiB chunks, fsyncs, refuses a stream whose length differs
  from the stated size, and can refuse to replace an existing file
  (atomically, by hard link, where the filesystem has one). The host
  connection has the same method. `put_archive` must never come back
  for streaming; the streaming write is this one.
- Downloads no longer ask the daemon for an archive. `get_archive` and
  `docker cp` read as root, so a component an agent swapped for a
  symlink after the path check redirected the read.
  `DockerConnection.fiterReadFileConfined` and `fiterReadDirectoryAsTar`
  run fixed programs as the container user
  (`vaibify/docker/confinedRead.py`); the host leg is
  `vaibify/host/hostConfinedRead.py`, held to the same contract by
  `tests/testConfinedReadParity.py`. A final symlink is followed only
  lexically and only inside the authorized root; a folder archive never
  follows a link and holds no member beneath one. The older
  `fiterStreamFile` (daemon archive) still serves callers that have not
  moved.
- `scriptFigureViewer.js` was not part of the 2026-01 frontend
  refactor. Kept as a single cohesive module.
- Re-export blocks exist across `pipelineRunner`, `pipelineServer`,
  `testGenerator`, and `syncDispatcher` for backward compatibility.
  Callers should migrate toward canonical imports over time; do not
  delete the re-exports until external callers are updated.
- `vaibify/reproducibility/githubWorkflow.py` is implemented, tested,
  and **unreachable** — no product code imports it. Kept rather than
  deleted because wiring it expands remote-execution surface, which is
  a product decision. `tests/testOrphanedPublishMachinery.py` fails if
  the docs re-advertise it or if it gains a caller while still marked
  unreachable.
- `condaPackages` is refused at validation rather than installed. The
  Dockerfile installs Miniforge for a non-pip package manager but has
  no `conda install` step and no build argument carries the list, so
  accepting the field produced a container without the requested
  packages and said nothing. Wiring it is the honest fix; refusing is
  the honest interim.
- `terminalContainment.py` and the `terminal` journal kind also
  reconcile records written by EARLIER hub versions — release, the
  safe reaper and shutdown all settle terminal records through
  `fdictTerminateAndProveRecord` — and
  `tests/testTerminalContainmentLive.py` keeps the container
  process-group prover as the standing demonstration that it cannot
  see a `setsid` descendant (`tests/testHostTerminal.py` carries the
  host leg's twin demonstration). This bullet used to say the
  in-memory registry was permanently empty; the terminal came back
  for containers on 2026-08-11 and for host projects on 2026-08-15,
  so live sessions register in production again.
- `commitCarrier.fdictRequestDurableTaskCancel` has no caller and
  refuses everything. Kept deliberately: Python cannot interrupt a
  worker in `asyncio.to_thread`, so there is no honest generic cancel,
  and a reader who finds no function at all re-derives that from
  scratch — or writes one. The refusal is the answer, in the place the
  question is asked.
- The poison axis is NOT subsumed by the journal quarantine. A
  quarantine record survives a crash; it does not fence a socket that
  is open right now. Poison does both — the pipeline lane is refused at
  the gate and revalidated per frame — so the two are complementary,
  not redundant.
- `bAllowEmulation` on an obtained project's registry entry is frozen
  at conversion. The wizard offers the emulation checkbox only when
  the daemon was reachable while the wizard was open and its
  architecture differed from the pin, so a clone converted while
  Docker was unreachable can never consent later: no route updates
  the flag (`fnUpdateImageSource` is called only for the overlay
  fields). The forward switch (`switch-to-pinned-image`) asks the
  question afresh, so switching to building and back is the workaround
  today; a route that updates the flag is the fix, once a researcher
  meets it.
- "Retire this container" is spelled out twice, and `vaibify destroy`
  means something narrower than the dashboard's Delete. The
  stop-or-remove branch plus the keep-alive stop lives in both
  `registryRoutes._fnExecuteStop` and
  `environmentDeletion._fbRemoveContainer`, so both reach the
  lifecycle gateway's primitives from outside it and the second copy
  raised `I_MUTATION_CAPABLE_OUTSIDE_GATEWAY_BUDGET` by two. Homing
  the operation inside `containerManager` would collapse both call
  sites and lower that ratchet by three; it was not done alongside the
  delete feature because `_fnExecuteStop` is patched and
  source-inspected by name from four test modules, so the move needs
  its own change and its own reconfirmation. Separately, the CLI's
  `vaibify destroy` removes only the workspace volume and optionally
  the image — not the container, the credentials volume, the other
  image tags, or the registry entry — so the CLI and the GUI now mean
  different things by "destroy". Widening a CLI command's blast radius
  is a product decision, so the divergence is recorded rather than
  quietly resolved.
